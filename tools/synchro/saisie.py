"""Synchro v4 : saisie transmise, --share-saisie (activé par --coop-donjon), --no-saisie.

Étude : docs/etudes/sync3_aggro_tous.md §8 (la section « Vérification » prime).

Ce que les joueurs voient : les monstres qui SAISISSENT le joueur (Floormaster, Poe, Mothula, Boko Baba, Dexivine,
Ss ; boss Kalle Demos et Jalhalla) peuvent maintenant viser le fantôme d'un autre joueur, comme les autres
monstres. Quand un tel monstre attrape le fantôme, le joueur du PC où le monstre réfléchit (l'autorité de la
zone) ne subit RIEN : ni possession, ni déplacement, ni morsure. C'est le joueur du fantôme qui subit l'effet
chez lui : un coup (morsure, saisie, projection) ou les commandes inversées du Poe pendant quelques secondes.

Comment (pack v19, bit 9 des capacités, zone ``wwmp_saisie`` trouvée par sa magie) :

* BOUCLIER (chez l'autorité) : pendant l'execute d'un ennemi qui vise un fantôme, 7 méthodes de Link sont rendues
  sans effet et les drapeaux de Link que ces espèces écrivent directement sont remis après l'execute. Chaque
  appel bloqué est noté dans un anneau de 8 événements ; l'ennemi reste « verrouillé » (protégé) 90 images même
  si l'échange de position cesse entre-temps.
* TRANSMISSION : ce module lit l'anneau. Pour un ennemi SYNCHRONISÉ qui n'est pas une partie de boss :
  une saisie (genres 2 à 7) devient un coup ``player_hit`` ordinaire (rejoué chez la victime par CALL 9, avec
  garde, recul, invincibilité : ``synchro/coups_fantome.py``) ; la possession du Poe (bit 0x100 de Link+0x3BC)
  devient un message ``saisie`` {effet "confus"} que le serveur relaie à la victime, dont le pack pose ce même
  bit pendant ``CONFUS_FRAMES`` images (mot +0x2C de la zone, décompté par le jeu, jamais pendant un événement).
  Les boss ne transmettent rien : chacun combat son propre boss (A9), comme pour les coups.
* BATTEMENT : tant que le client tourne, il recharge un compte d'images dans le pack. Client tué ou planté : le
  compte tombe à zéro et le pack remet lui-même à 0 le cfg de la table des cibles (plus aucun échange). Le
  battement est tenu même avec --no-saisie (il ne dépend que du pack).

* VERROU ORPHELIN (relecture adverse) : le pack prolonge le verrou à chaque appel bloqué, même sans échange. Le PC
  suit la table des cibles (``suivre``) : un événement n'est transmis que si l'ennemi visait ce fantôme, et le
  verrou d'un ennemi sorti de la table est éteint dès que sa saisie est finie (``PAUSE_FRAMES`` sans appel).

Sans le bit 9 (pack plus ancien) : rien n'est écrit, les espèces qui saisissent restent derrière
--aggro-experimental, comme avant.
"""
from __future__ import annotations

import threading
import time

from synchro import cibles as _cibles

VIE = 150               # images : battement rechargé (≈ 5 s à 30 images/s)
PERIODE = 1.0           # s entre deux recharges
CONFUS_FRAMES = 90      # images de commandes inversées par message (≈ 3 s ; le Poe le repose tant qu'il possède)
CONFUS_GAP = 1.0        # s entre deux messages « confus » vers la même victime
COUP_GAP = 1.5          # s sans appel bloqué du même ennemi avant qu'une saisie compte comme NOUVELLE (un coup par saisie)
VISE_GRACE = 0.5        # s : un événement n'est transmis que si l'ennemi visait CE fantôme il y a moins que ça
VERROU_FRAMES = 90      # images posées par le pack à chaque appel bloqué (= mailbox.SAISIE_VERROU_FRAMES ; pas
                        # d'import de wwlib.mailbox ici : le serveur charge ce module sans Cemu ni ctypes)
PAUSE_FRAMES = 30       # images sans appel bloqué : la saisie du fantôme est finie, un verrou orphelin est éteint
FRESH = 1.0             # s : message reçu plus vieux (file bloquée par un chargement) -> jeté
INBOX_MAX = 8
BIT_CONFUS = 0x100      # Link+0x3BC : possession du Poe (ori 0x100 en 0x02450040)
GENRES_SAISIE = frozenset({2, 3, 4, 5, 6, 7})     # déplacement imposé, projection, morsure
GENRE_DRAPEAUX = 10
# coup forgé (aucune collision n'a eu lieu : le monstre a « appelé » Link) : attributs d'un coup d'ennemi ordinaire
AT_TYPE = 0x2           # [HYPO] type d'attaque d'un coup d'ennemi (celui des coups relevés en A1)
DMG = {119: 2, 214: 2, 204: 2, 212: 1, 252: 1, 251: 1}     # quarts de cœur par espèce
DMG_DEFAUT = 1


def valid_saisie(msg) -> dict | None:
    """Champs d'un message ``saisie`` (serveur et client) ; None si un champ manque ou sort des bornes."""
    if not isinstance(msg, dict):
        return None

    def _int(v, lo, hi):
        return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None
    to = _int(msg.get("to"), 1, 1 << 30)
    zone, key = msg.get("zone"), msg.get("key")
    frames = _int(msg.get("frames"), 1, 300)
    seq = _int(msg.get("seq"), 0, (1 << 31) - 1)
    proc = _int(msg.get("proc", -1), -1, 0x7FFF)
    if (to is None or frames is None or seq is None or msg.get("effet") != "confus"
            or not isinstance(zone, str) or not 0 < len(zone) <= 64
            or not isinstance(key, str) or not 0 < len(key) <= 64):
        return None
    return {"to": to, "zone": zone, "key": key, "proc": -1 if proc is None else proc, "effet": "confus",
            "frames": frames, "seq": seq}


class SaisieSync:
    """Bouclier et battement tenus dans le pack ; événements lus et transmis ; effets reçus appliqués."""

    def __init__(self, enabled: bool = False, battement: bool = False):
        self.enabled = bool(enabled)         # --share-saisie : bouclier, transmission, réception
        self.battement = bool(battement)     # A3 actif : battement tenu même sans bouclier
        self.on = False                      # bouclier armé (ce que nous avons écrit en dernier)
        self.t = None                        # dernière recharge
        self.supported = None                # None : pas encore vu de boîte
        self.total = None                    # dernier n° d'événement vu (None : première lecture)
        self.rep = 0                         # répétitions vues du dernier événement
        self.seq = 0
        self._coup_t: dict[int, float] = {}      # id de l'ennemi -> heure de son dernier appel bloqué vu
        self._confus_t: dict[int, float] = {}    # joueur -> heure du dernier « confus » envoyé
        self._vise: dict[int, tuple[int, float]] = {}   # id de l'ennemi -> (id du fantôme visé, dernière heure vue)
        self._table: frozenset = frozenset()     # ennemis de la table des cibles en vigueur
        self.eteints = self.hors_cible = 0       # verrous orphelins éteints ; événements d'un ennemi qui ne vise plus
        self.inbox: list[tuple[float, int, dict]] = []
        self._lock = threading.Lock()        # receive (boucle réseau) / victim_tick (thread de travail)
        self.notes: list[str] = []
        self.bloques = self.verrous = self.coupures = 0
        self.coups = self.confus = self.recus = self.appliques = self.ignores = 0
        self.dernier = None                  # (proc, genre) du dernier événement

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def actif(self) -> bool:
        """Bouclier armé dans le pack : les espèces qui saisissent peuvent viser un fantôme."""
        return bool(self.enabled and self.on and self.supported)

    # -- chez l'autorité : qui vise qui (table des cibles en vigueur dans le pack) ---------------
    def suivre(self, table, now: float) -> None:
        """``table`` : [(id de l'ennemi, id du fantôme)] écrite en dernier dans le pack (``AggroSync.written``),
        à passer à chaque tick AVANT ``step``. Sert à deux choses (relecture adverse) : un événement n'est
        transmis que si l'ennemi visait ce fantôme (``effets``) ; le verrou d'un ennemi sorti de la table est
        éteint dès que sa saisie est finie (``_verrous_orphelins``)."""
        vus = set()
        for x in table or ():
            try:
                enemy, ghost = int(x[0]), int(x[1])
            except (TypeError, ValueError, IndexError):
                continue
            if ghost:
                self._vise[enemy] = (ghost, now)
                vus.add(enemy)
        self._table = frozenset(vus)
        if len(self._vise) > 64:
            self._vise = {k: v for k, v in self._vise.items() if 0 <= now - v[1] < 10.0}

    def visait(self, enemy, ghost, now: float) -> bool:
        """L'ennemi vise ce fantôme (table en vigueur), ou le visait il y a moins de ``VISE_GRACE`` s."""
        v = self._vise.get(enemy)
        return bool(v and v[0] == ghost and (enemy in self._table or 0 <= now - v[1] <= VISE_GRACE))

    def _verrous_orphelins(self, mb) -> int:
        """Le pack prolonge le verrou à CHAQUE appel bloqué, même sans échange : un monstre qui, à peine sa
        saisie du fantôme finie, s'en prenait au Link local le trouvait protégé, et chaque nouvel appel
        repoussait la fin de 90 images (Link local intouchable par ce monstre tant qu'il insistait). Ici : un
        ennemi qui n'est plus dans la table et dont le verrou n'a pas été prolongé depuis ``PAUSE_FRAMES``
        images a fini sa saisie -> verrou éteint. Tant que les appels se suivent (saisie en cours), on n'y touche
        pas : le Link local ne doit jamais subir la fin d'une saisie commencée sur le fantôme."""
        lire, eteindre = getattr(mb, "saisie_verrous", None), getattr(mb, "clear_saisie_verrou", None)
        if lire is None or eteindre is None:
            return 0
        n = 0
        for v in lire():
            if v["enemy"] in self._table or v["frames"] > VERROU_FRAMES - PAUSE_FRAMES:
                continue
            if eteindre(v["i"], v["enemy"]):
                n += 1
        self.eteints += n
        return n

    # -- chez tout le monde : bouclier et battement ---------------------------------------------
    def step(self, mb, now: float, want: bool = True) -> list[dict]:
        """``mb`` : Mailbox en écriture ou None ; ``want`` : jeu jouable. Rend les événements NOUVEAUX de l'anneau
        (ou dont les répétitions ont augmenté), à passer à ``effets``."""
        if not (self.enabled or self.battement):
            return []
        ok = bool(mb is not None and getattr(mb, "saisie", False))
        if mb is not None and ok != self.supported:
            self.supported = ok
            if self.enabled:
                self.notes.append("saisie transmise : active (Floormaster, Poe, Mothula, Boko Baba, Dexivine peuvent "
                                  "viser un fantôme)" if ok else
                                  "saisie transmise : graphic pack sans cette capacité, les monstres qui saisissent "
                                  "ne visent que toi (mets le pack à jour)")
        if not ok:
            self.on = False
            return []
        shield = bool(self.enabled and want)
        if shield != self.on or self.t is None or now - self.t >= PERIODE or now < self.t:
            mb.set_saisie(shield, VIE if (self.battement or shield) else 0)
            self.on, self.t = shield, now
            c = mb.saisie_counters()
            self.bloques, self.verrous = int(c.get("bloques", 0)), int(c.get("verrous", 0))
            if int(c.get("coupures", 0)) > self.coupures and self.coupures >= 0 and self.total is not None:
                self.notes.append("monstres qui visent le fantôme : coupés par le jeu (client resté muet), repris")
            self.coupures = int(c.get("coupures", 0))
        if not self.enabled:
            return []
        total, evts = mb.read_saisie_events()     # lu AVANT d'éteindre un verrou : rien de perdu
        self._verrous_orphelins(mb)
        if self.total is None or total < self.total:      # première lecture, ou jeu relancé : rien de rejoué
            self.total = total
            self.rep = next((e["rep"] for e in evts if e["n"] == total), 0)
            return []
        out = []
        for e in evts:
            if e["n"] > self.total or (e["n"] == self.total and e["rep"] > self.rep):
                out.append(e)
        dernier = next((e for e in evts if e["n"] == total), None)
        self.total = total
        if dernier is not None:
            self.rep = dernier["rep"]
            self.dernier = (dernier["proc"], dernier["genre"])
        return out

    # -- chez l'autorité : événements -> effets à transmettre -----------------------------------
    def effets(self, events: list[dict], zone: str | None, ghost_players: dict, resolve, others: dict,
               now: float) -> tuple[list[dict], list[dict]]:
        """Événements du bouclier -> (coups forgés au format de la file A1, pour ``GhostHitSync.outgoing`` ;
        messages ``saisie``). ``ghost_players`` : id de process du fantôme -> joueur ; ``resolve(id)`` -> (clé
        réseau, proc) d'un ennemi synchronisé ou None ; ``others`` : joueur -> dernier ``state``."""
        coups, msgs = [], []
        if not self.enabled or not zone:
            return coups, msgs
        for e in events:
            player = ghost_players.get(e.get("ghost"))
            o = others.get(player) if player is not None else None
            if o is None or f"{o.get('stage')}:{o.get('room')}" != zone or o.get("busy") in (1, True):
                self.ignores += 1
                continue
            r = resolve(e.get("enemy"))
            if r is None:
                self.ignores += 1                  # ennemi local, non synchronisé : rien à transmettre
                continue
            key, proc = r
            if proc in _cibles.BOSS_PROC or e.get("proc") in _cibles.BOSS_PROC:
                self.ignores += 1                  # boss : chacun combat le sien, jamais de double effet
                continue
            genre, enemy = e.get("genre"), e.get("enemy")
            if not self.visait(enemy, e.get("ghost"), now):
                # appel bloqué sous VERROU seul : l'ennemi ne visait plus ce fantôme (il s'en prenait au Link
                # local, encore protégé) -> rien n'est arrivé au joueur du fantôme, rien à lui transmettre
                self.hors_cible += 1
                self.ignores += 1
                continue
            if genre in GENRES_SAISIE:
                # une saisie dure : l'ennemi rappelle la méthode à chaque image. Un seul coup, au début
                vu, self._coup_t[enemy] = self._coup_t.get(enemy, -1e9), now
                if 0 <= now - vu < COUP_GAP:
                    continue
                coups.append({"ghost": e.get("ghost"), "attacker": enemy, "type": AT_TYPE, "atp": 1, "spl": 0,
                              "se": 0, "mark": 0, "gat": 0, "mtrl": 0, "pos": [0.0, 0.0, 0.0],
                              "vec": [0.0, 0.0, 0.0], "dmg": DMG.get(proc, DMG_DEFAUT)})
                self.coups += 1
            elif genre == GENRE_DRAPEAUX and e.get("a1", 0) & BIT_CONFUS and not e.get("a0", 0) & BIT_CONFUS:
                if now - self._confus_t.get(player, -1e9) < CONFUS_GAP:
                    continue
                self._confus_t[player] = now
                self.seq = (self.seq + 1) & 0x7FFFFFFF
                msgs.append({"type": "saisie", "to": player, "zone": zone, "key": str(key)[:64],
                             "proc": int(proc) if isinstance(proc, int) else -1, "effet": "confus",
                             "frames": CONFUS_FRAMES, "seq": self.seq})
                self.confus += 1
        if len(self._coup_t) > 64:
            self._coup_t = {k: v for k, v in self._coup_t.items() if now - v < 10.0}
        return coups, msgs

    # -- chez la victime ----------------------------------------------------------------------
    def receive(self, msg: dict, me, now: float | None = None) -> bool:
        """Boucle réseau : message ``saisie`` relayé par le serveur, gardé pour le tick suivant."""
        if not self.enabled:
            return False
        rec = valid_saisie(msg)
        frm = msg.get("from") if isinstance(msg, dict) else None
        if (rec is None or me is None or rec["to"] != me or isinstance(frm, bool) or not isinstance(frm, int)
                or frm == me):
            return False
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self.inbox) >= INBOX_MAX:
                self.inbox.pop(0)
            self.inbox.append((now, frm, rec))
        self.recus += 1
        return True

    def pending(self) -> bool:
        return bool(self.inbox)

    def victim_tick(self, mb, now: float, zone: str | None, free: bool, holder=None) -> int:
        """Thread de travail : applique les effets reçus. ``free`` : nous jouons (ni cinématique, ni chargement,
        pas autorité de la zone) ; ``holder`` : autorité actuelle de notre zone (l'émetteur doit l'être)."""
        with self._lock:
            inbox, self.inbox = self.inbox, []
        faits = 0
        for t, frm, rec in inbox:
            if (now - t > FRESH or now < t or not free or rec["zone"] != zone
                    or (holder is not None and frm != holder)
                    or mb is None or not getattr(mb, "saisie", False)):
                self.ignores += 1
                continue
            if mb.set_confusion(rec["frames"]):
                faits += 1
                self.appliques += 1
                self.notes.append(f"{_cibles.ESPECES.get(rec['proc'], ('un monstre',))[0]} te possède : "
                                  "commandes inversées quelques secondes")
        return faits

    def stop(self, mb) -> None:
        """Arrêt du client : bouclier, battement et confusion coupés tout de suite."""
        try:
            if mb is not None and getattr(mb, "saisie", False):
                mb.set_saisie(False, 0)
                mb.set_confusion(0)
        finally:
            self.on = False

    def status(self) -> str:
        if self.supported is None:
            return "en attente du jeu"
        if not self.supported:
            return "pack sans la capacité (les monstres qui saisissent ne visent que toi)"
        if not self.enabled:
            return "coupée (battement seul)"
        s = ("bouclier armé" if self.on else "au repos") + f", {self.bloques} appel(s) bloqué(s)"
        if self.coups or self.confus:
            s += f", transmis : {self.coups} coup(s), {self.confus} possession(s)"
        if self.appliques:
            s += f", subi : {self.appliques}"
        if self.verrous:
            s += f", {self.verrous} monstre(s) verrouillé(s)"
        if self.eteints:
            s += f", {self.eteints} verrou(s) éteint(s)"
        if self.dernier:
            s += f", dernier : {_cibles.ESPECES.get(self.dernier[0], ('espèce ' + str(self.dernier[0]),))[0]}"
        return s
