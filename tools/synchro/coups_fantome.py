"""Synchro v2 : fantôme touchable (A1/A2), --share-ghost-hits (activé par --coop-donjon), --no-ghost-hits.

Étude : docs/etudes/sync2_ia_boss_cine.md §3.a « a1 » et « a2 » (la section « Vérification » finale
prime), docs/etudes/COMBAT.md §3.2 et §3.4, docs/etudes/mode_hanteurs.md (autorité par ZONE, pas par ennemi).

Ce que les joueurs voient : chez l'autorité de la zone (le PC où les monstres réfléchissent), un monstre
qui frappe le fantôme d'un autre joueur le touche vraiment. Ce joueur perd de la vie chez lui, recule, a
son temps d'invincibilité, peut parer au bouclier et être sauvé par sa fée, comme si le monstre l'avait
frappé dans son propre jeu. Pas de tir ami : l'épée de Link n'atteint pas une cible « joueur ».

Chaîne complète :

1. **A1, chez l'autorité (pack v19)** : le PC allume le TG des fantômes (``Mailbox.set_ghost_tg``)
   seulement s'il est l'autorité de SA zone, hors cinématique, avec au moins un autre joueur dans la
   zone. Le jeu inscrit alors, pour chaque fantôme, un cylindre « corps de joueur » (copie du ``mCyl`` de
   Link : TG ``Set | IsPlayer``, type 0xFF1DFEFF) ; un coup reçu va dans une file de 8 entrées.
2. Le PC lit la file (``Mailbox.read_ghost_hits``) et ne garde que les coups d'un ennemi **synchronisé**
   (clé réseau dans ``local_enemies`` : ni boss filtré par A9, ni ennemi non partagé), ou d'un projectile
   dont le parent (``parentActorID`` +0x2E8 [DÉDUIT : GC 0x1CC + 0x11C]) en est un. Tout le reste est
   jeté : bombes de Link (pas de tir ami), pièges, ennemis qui tournent aussi chez la victime (leur coup
   la touche déjà chez elle : pas de double dégât).
3. ``player_hit {to, zone, key, proc, at, dmg, vec, pos, seq}`` part au serveur, qui ne le relaie que
   si l'émetteur est l'autorité actuelle de la zone, que la victime est dans la même zone et pas en
   cinématique, au plus 4 par seconde et par paire (``wwmp_server.handle_player_hit``).
4. **A2, chez la victime** : le coup est refusé s'il a plus de ``FRESH`` s, si la zone a changé, si Link
   est en événement ou à 0 PV, s'il est à plus de ``DIST_MAX`` unités du point d'impact (roulade
   réussie), si le ``seq`` est déjà vu, ou si la marionnette locale de cet ennemi n'est pas **suspendue**
   (IA locale active : elle frappe déjà la victime elle-même, pas de double dégât). Sinon, pack v19 :
   ``Mailbox.coup_link`` (CALL 9) inscrit pendant une image un AT au centre du corps de Link, avec la
   marionnette pour propriétaire ; le jeu fait tout le reste. Sans pack v19 (ou v19 sans cette capacité) :
   rien, sauf mode dégradé ``--degats-directs`` (désactivé par défaut, jamais activé par --coop-donjon) :
   la vie de la sauvegarde courante baisse de ``dmg`` quarts, plancher 1 quart, une fois toutes les
   ``DIRECT_INVULN`` s, sans recul ni fée.

Lot L1 (docs/etudes/rollback_netcode.md §4, A2) : chez l'autorité, la file du jeu est aussi surveillée par le fil des
images (``lire_file`` : un mot de 4 octets, la séquence, à chaque image ; la file entière n'est relue que si elle a
bougé) et le coup part au serveur tout de suite, sans attendre le tour de boucle. Chez la victime, un coup reçu est
rejoué sans attendre le tour (``victim_tick`` appelé depuis la réception, mêmes gardes). Les deux chemins (fil des
images, tour de boucle) se partagent la file sous ``file_lock`` : un coup n'est jamais envoyé deux fois.

Lot L3 (docs/etudes/rollback_netcode.md §4, A5) : COMPENSATION DE LATENCE CÔTÉ VICTIME. L'autorité ajoute au
``player_hit`` ce qu'elle AFFICHAIT de la victime au moment du coup : ``fr`` (image du jeu de la victime, celle du
présent prédit ou du tampon : ``lissage.Lisseur.vue``) et ``gpos`` (position du fantôme affiché). Deux champs
optionnels : un ancien serveur les retire, un ancien client les ignore, une autorité ancienne ne les met pas (règle
d'avant : ``DIST_MAX`` depuis la position actuelle). La victime garde ~1,5 s de ses propres positions datées par son
compteur d'images (``HistoriqueSoi``) et juge le coup À CE ``fr`` (``juger_compense``) :

* **esquivé / hors de portée à cette image** : là où elle était VRAIMENT à cette image est à plus de ``TOL_XZ`` (à
  plat) ou ``TOL_Y`` (hauteur) du fantôme affiché : l'autorité frappait une prédiction fausse (roulade, demi-tour, saut
  déjà faits chez la victime, pas encore vus chez l'autorité) -> refusé ;
* sinon le coup est **juste**, même si elle a bougé de plus de ``DIST_MAX`` depuis (gros ping) : il est rejoué ;
  ``DIST_SECOURS`` reste le dernier recours. Image hors de l'historique (trop vieille, trop en avance, jeu relancé) :
  règle d'avant.
Choix : un mouvement fait APRÈS cette image (roulade lancée pendant le trajet du coup) ne l'annule pas : le coup
avait déjà porté chez l'autorité, comme en solo ; l'annuler refuserait aussi les coups justes d'un gros ping (la
victime qui court s'éloigne toujours). L'invincibilité n'est pas jugée ici : le jeu la juge lui-même à l'image du coup
rejoué (CALL 9).
"""
from __future__ import annotations

import math
import threading
import time

from synchro import cibles as _cibles

DIST_MAX = 200.0        # unités : Link de la victime à plus du point d'impact vu chez l'autorité -> esquivé
FRESH = 1.0             # s : coup reçu plus vieux (file bloquée par un chargement) -> jeté
SEND_GAP = 0.3          # s entre deux coups envoyés à la même victime (le jeu la rend invincible ensuite)
DIRECT_INVULN = 1.5     # s d'invincibilité du mode dégradé (pas de mDamageWaitTimer sans le jeu)
CFG_REFRESH = 5.0       # s : cfg de la boîte réécrit même sans changement (jeu relancé, autre boîte)
NOTE_GAP = 10.0         # s entre deux messages identiques au joueur
SEQ_MEMO = 64           # seq retenus par émetteur
INBOX_MAX = 16
ATP_MAX, SPL_MAX, DMG_MAX = 20, 15, 80
R_STALLED = -6          # code rendu par ``inject`` quand la boîte n'a pas répondu (menu pause, chargement)
# lot L3 (A5) : compensation de latence côté victime
HIST_IMAGES = 45        # images gardées dans l'historique de la victime (1,5 s)
AVANCE_FR = 6           # images : ``fr`` en avance sur le dernier relevé admise (horloges, prédiction) ; au-delà : règle d'avant
TOL_XZ = 80.0           # u (à plat) : vraie position / fantôme affiché, au-delà le coup est « hors de portée »
TOL_Y = 120.0           # u (hauteur) : idem (saut, chute)
DIST_SECOURS = 400.0    # u : dernier recours quand le coup est jugé à ``fr`` (au lieu de DIST_MAX)
OFF_PARENT = 0x2E8      # fopAc_ac_c::parentActorID (GC 0x1CC + 0x11C) [DÉDUIT]
# 181 = Kargaroc (`Bb`, d_a_bb.cpp ; docs/etudes/mode_hanteurs.md : l'étude sync2_ia_boss_cine l'appelait « Bulle »)
# A3 (boss, synchro/cibles.py) : un boss qui vise un fantôme le touche sans le blesser (chacun combat son boss, A9) ;
# filet de sécurité si une de ses parties passait un jour pour un ennemi synchronisé (stage de boss mal nommée)
BOSS_AGGRO_PROCS = frozenset(_cibles.BOSS_PROC)
PROC_NAMES = {189: "Bokoblin", 188: "Moblin", 215: "Keese", 181: "Kargaroc", 168: "Link"}


def _f3(v) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    return out if all(math.isfinite(x) and abs(x) < 1e6 for x in out) else None


def _int(v, lo: int, hi: int) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        return None
    return v


def valid_at(at) -> dict | None:
    """Attributs de l'attaque (bornes de CALL 9)."""
    if not isinstance(at, dict):
        return None
    out = {"type": _int(at.get("type"), 1, 0xFFFFFFFF), "atp": _int(at.get("atp"), 0, ATP_MAX),
           "spl": _int(at.get("spl"), 0, SPL_MAX), "se": _int(at.get("se", 0), 0, 0xFF),
           "mark": _int(at.get("mark", 0), 0, 0xFF), "gat": _int(at.get("gat", 0), 0, 0xFFFFFFFF),
           "mtrl": _int(at.get("mtrl", 0), 0, 0xFF)}
    return None if any(v is None for v in out.values()) else out


def valid_player_hit(msg) -> dict | None:
    """Champs d'un ``player_hit`` (serveur et client) ; None si un champ manque ou sort des bornes.
    ``from`` n'est pas lu ici (posé par le serveur). Lot L3 (A5) : ``fr`` et ``gpos`` optionnels, gardés seulement
    s'ils sont valides tous les deux (sinon retirés : règle d'avant chez la victime)."""
    if not isinstance(msg, dict):
        return None
    to = _int(msg.get("to"), 1, 1 << 30)
    zone, key = msg.get("zone"), msg.get("key")
    at = valid_at(msg.get("at"))
    dmg = _int(msg.get("dmg"), 0, DMG_MAX)
    vec, pos = _f3(msg.get("vec")), _f3(msg.get("pos"))
    seq = _int(msg.get("seq"), 0, (1 << 31) - 1)
    proc = msg.get("proc", -1)
    if (to is None or not isinstance(zone, str) or not 0 < len(zone) <= 64 or not isinstance(key, str)
            or not 0 < len(key) <= 64 or at is None or dmg is None or vec is None or pos is None or seq is None):
        return None
    proc = proc if isinstance(proc, int) and not isinstance(proc, bool) and -1 <= proc < 0x8000 else -1
    out = {"to": to, "zone": zone, "key": key, "proc": proc, "at": at, "dmg": dmg, "vec": vec, "pos": pos,
           "seq": seq}
    fr, gpos = _int(msg.get("fr"), 0, (1 << 31) - 1), _f3(msg.get("gpos"))
    if fr is not None and gpos is not None:
        out["fr"], out["gpos"] = fr, gpos
    return out


def proc_label(proc) -> str:
    return PROC_NAMES.get(proc, f"monstre {proc}" if isinstance(proc, int) and proc >= 0 else "monstre")


def direct_life(life: int, dmg: int) -> int:
    """Mode dégradé : nouvelle vie (quarts), jamais au-dessus, jamais sous 1 quart (pas de game over écrit)."""
    if life <= 1 or dmg <= 0:
        return life
    return max(1, life - dmg)


class HitQueue:
    """Suit le ``seq`` de la file du jeu : rend les entrées nouvelles depuis la dernière lecture."""

    def __init__(self):
        self.last: int | None = None
        self.lost = 0

    def take(self, seq: int, entries: list[dict]) -> list[dict]:
        if self.last is None or seq < self.last:
            self.last = seq                       # première lecture, ou jeu / boîte relancés : on repart
            return []
        new = sorted((e for e in entries if self.last < e["seq"] <= seq), key=lambda e: e["seq"])
        self.lost += max(0, (seq - self.last) - len(new))
        self.last = seq
        return new


class HistoriqueSoi:
    """Lot L3 (A5) : positions de NOTRE Link datées par le compteur d'images de la boîte (``fr`` du ``state``), sur
    ``HIST_IMAGES`` images. Écrit par le fil de travail, lu par la victime (tour ou réception) : sous verrou."""

    def __init__(self, n_images: int = HIST_IMAGES):
        self.n_images = int(n_images)
        self.ech: list[tuple[int, tuple]] = []
        self._lock = threading.Lock()

    def noter(self, fr, pos) -> bool:
        if isinstance(fr, bool) or not isinstance(fr, int) or fr < 0:
            return False
        p = _f3(pos)
        if p is None:
            return False
        with self._lock:
            if self.ech and fr <= self.ech[-1][0]:
                if fr == self.ech[-1][0]:
                    return False                  # même image (pause, relevé répété)
                self.ech.clear()                  # compteur reparti en arrière : jeu ou boîte relancés
            self.ech.append((fr, tuple(p)))
            while self.ech and self.ech[0][0] < fr - self.n_images:
                self.ech.pop(0)
        return True

    def position(self, fr) -> tuple | None:
        """Position à l'image ``fr`` (interpolée entre deux relevés) ; None hors de l'historique (plus vieille que le
        premier relevé, ou plus de ``AVANCE_FR`` images après le dernier)."""
        if isinstance(fr, bool) or not isinstance(fr, int):
            return None
        with self._lock:
            ech = list(self.ech)
        if not ech or fr < ech[0][0] or fr > ech[-1][0] + AVANCE_FR:
            return None
        if fr >= ech[-1][0]:
            return ech[-1][1]
        for (fa, pa), (fb, pb) in zip(ech, ech[1:]):
            if fa <= fr <= fb:
                u = (fr - fa) / (fb - fa)
                return tuple(a + (b - a) * u for a, b in zip(pa, pb))
        return None


def juger_compense(hist: HistoriqueSoi, rec: dict, link_pos) -> str | None:
    """Lot L3 (A5) : verdict d'un coup qui porte ``fr`` et ``gpos`` : None (juste : à rejouer), « portee » (esquivé :
    hors de portée à cette image), « secours » (bien trop loin maintenant), ou « inconnu » (image hors de
    l'historique, champs absents : la règle d'avant s'applique)."""
    fr, gpos = rec.get("fr"), rec.get("gpos")
    lp = _f3(link_pos)
    if fr is None or gpos is None or lp is None:
        return "inconnu"
    p = hist.position(fr)
    if p is None:
        return "inconnu"
    if math.hypot(p[0] - gpos[0], p[2] - gpos[2]) > TOL_XZ or abs(p[1] - gpos[1]) > TOL_Y:
        return "portee"
    if math.dist(lp, rec["pos"]) > DIST_SECOURS:
        return "secours"
    return None


class GhostHitSync:
    def __init__(self, enabled: bool = False, direct: bool = False, clock=time.monotonic,
                 dist_max: float = DIST_MAX):
        self.enabled = bool(enabled)
        self.direct = bool(direct) and self.enabled
        self.clock = clock
        self.dist_max = float(dist_max)
        self.queue = HitQueue()
        self.cfg: bool | None = None          # dernier cfg écrit dans la boîte (None = jamais)
        self.cfg_t = -1e9
        self.last_sent: dict[int, float] = {}   # victime -> heure du dernier envoi
        self.seq = 0
        self.inbox: list[tuple[float, int, dict]] = []   # (heure, émetteur, coup validé)
        self._lock = threading.Lock()         # receive (boucle réseau) / victim_tick (thread de travail)
        # coups à envoyer : remplis par le thread de travail, vidés par la boucle réseau (relecture adverse :
        # sans verrou, un extend sur l'ancienne liste juste après l'échange perdait le coup)
        self.out_lock = threading.Lock()
        self.seen: dict[int, list[int]] = {}
        # lot L1 (A2) : la file du jeu est lue par le tour de boucle ET par le fil des images (lire_file)
        self.file_lock = threading.Lock()
        self.contexte: dict | None = None     # posé à chaque tour par le client : ce que le fil des images doit savoir
        self.delais: list[float] = []          # lot L1 (A1) : réception -> coup rejoué (s), mesure
        self._victime_lock = threading.Lock()  # un seul victim_tick à la fois (tour de boucle, réception)
        self.last_direct = -1e9
        self.hist = HistoriqueSoi()           # lot L3 (A5) : nos positions datées par image (victime)
        self.compense = True                  # lot L3 (A5) : coups jugés au ``fr`` vu par l'autorité (prudent : non)
        self.n_juges = self.n_esquives = self.n_gardes = 0   # jugés à ``fr`` ; refusés ; gardés malgré DIST_MAX
        self.degats_mult = 1          # difficulté : dégâts reçus ×N (écriture directe : le pack ne la voit pas)
        self._notes: list[str] = []
        self._said: dict[str, float] = {}
        # compteurs (affichage)
        self.sent = self.dropped = self.received = self.applied = self.refused = 0

    # -- messages au joueur -------------------------------------------------
    def _note(self, kind: str, text: str, now: float) -> None:
        if now - self._said.get(kind, -1e9) >= NOTE_GAP:
            self._said[kind] = now
            self._notes.append(text)

    def take_notes(self) -> list[str]:
        n, self._notes = self._notes, []
        return n

    # -- A1 : chez l'autorité -------------------------------------------------
    @staticmethod
    def want_tg(supported: bool, authority: bool, busy: bool, others_here: bool) -> bool:
        """TG des fantômes : seulement chez l'autorité de la zone, hors cinématique, avec un autre joueur."""
        return bool(supported and authority and not busy and others_here)

    def sync_cfg(self, want: bool, write, now: float) -> bool:
        """Écrit le cfg de la boîte au changement (et toutes les CFG_REFRESH s). Renvoie True si écrit."""
        if self.cfg == want and now - self.cfg_t < CFG_REFRESH:
            return False
        write(want)
        self.cfg, self.cfg_t = want, now
        return True

    def lire_file(self, mb, zone: str | None, ghost_players: dict, resolve, others: dict, now: float,
                  want: bool, vue=None) -> list[dict]:
        """Lot L1 (A2) : file du jeu lue (deux lectures concordantes) puis coups nouveaux -> ``player_hit``. Partagée
        entre le tour de boucle et le fil des images : sous ``file_lock``, une entrée n'est prise qu'une fois.
        ``vue`` : voir ``outgoing``."""
        with self.file_lock:
            seq, entries = mb.read_ghost_hits()
            new = self.queue.take(seq, entries)
            if not new or not want:
                return []
            return self.outgoing(new, zone, ghost_players, resolve, others, now, vue=vue)

    def file_a_bouge(self, seq) -> bool:
        """Lot L1 (A2) : la séquence lue (un mot) diffère-t-elle de la dernière prise ? (première lecture : oui)"""
        return isinstance(seq, int) and seq != self.queue.last

    def prendre_delais(self) -> list[float]:
        """Lot L1 (A1, mesure) : délais « coup reçu -> rejoué » depuis le dernier appel."""
        out, self.delais = self.delais, []
        return out

    def outgoing(self, entries: list[dict], zone: str | None, ghost_players: dict, resolve, others: dict,
                 now: float, vue=None) -> list[dict]:
        """Coups lus dans la file -> messages ``player_hit``. ``ghost_players`` : id de process du fantôme ->
        id du joueur ; ``resolve(id attaquant)`` -> (clé réseau, proc) d'un ennemi synchronisé, ou None ;
        ``others`` : id du joueur -> dernier ``state`` reçu. Lot L3 (A5) : ``vue(joueur)`` -> (image de la victime,
        position) du fantôme affiché, ou None ; ajoutés au message (``fr``, ``gpos``) pour la compensation."""
        out = []
        if not self.enabled or not zone:
            return out
        for e in entries:
            player = ghost_players.get(e.get("ghost"))
            o = others.get(player) if player is not None else None
            if o is None or f"{o.get('stage')}:{o.get('room')}" != zone or o.get("busy") in (1, True):
                self.dropped += 1
                continue
            r = resolve(e.get("attacker"))
            if r is None:
                self.dropped += 1              # pas un ennemi synchronisé : bombe, piège, boss, ennemi local
                continue
            key, proc = r
            if proc in BOSS_AGGRO_PROCS:
                self.dropped += 1              # partie de boss (A3, cibles.BOSS_GROUPES) : jamais de double coup
                continue
            at = valid_at({"type": e.get("type"), "atp": min(int(e.get("atp", 0)), ATP_MAX),
                           "spl": min(int(e.get("spl", 0)), SPL_MAX), "se": e.get("se", 0),
                           "mark": e.get("mark", 0), "gat": e.get("gat", 0), "mtrl": e.get("mtrl", 0)})
            pos = _f3(e.get("pos"))
            if pos is None or pos == [0.0, 0.0, 0.0]:
                pos = _f3(o.get("pos"))
            vec = _f3(e.get("vec")) or [0.0, 0.0, 0.0]
            if at is None or pos is None:
                self.dropped += 1
                continue
            if now - self.last_sent.get(player, -1e9) < SEND_GAP:
                self.dropped += 1              # même coup vu sur plusieurs images, ou victime déjà invincible
                continue
            self.last_sent[player] = now
            self.seq = (self.seq + 1) & 0x7FFFFFFF
            out.append({"type": "player_hit", "to": player, "zone": zone, "key": str(key)[:64],
                        "proc": int(proc) if isinstance(proc, int) else -1, "at": at,
                        "dmg": max(0, min(DMG_MAX, int(e.get("dmg", 0)))), "vec": [round(v, 3) for v in vec],
                        "pos": [round(v, 1) for v in pos], "seq": self.seq})
            v = None
            if vue is not None:
                try:
                    v = vue(player)
                except Exception:
                    v = None
            gp = _f3(v[1]) if isinstance(v, tuple) and len(v) == 2 else None
            if gp is not None and isinstance(v[0], int) and not isinstance(v[0], bool) and 0 <= v[0] < (1 << 31):
                out[-1]["fr"] = v[0]
                out[-1]["gpos"] = [round(x, 1) for x in gp]
            self.sent += 1
        return out

    # -- A2 : chez la victime -------------------------------------------------
    def receive(self, msg: dict, me, now: float | None = None) -> bool:
        if not self.enabled:
            return False
        now = self.clock() if now is None else now
        rec = valid_player_hit(msg)
        src = msg.get("from") if isinstance(msg, dict) else None
        if rec is None or me is None or rec["to"] != me or not isinstance(src, int) or isinstance(src, bool):
            return False
        with self._lock:
            seen = self.seen.setdefault(src, [])
            if rec["seq"] in seen:
                return False
            seen.append(rec["seq"])
            del seen[:-SEQ_MEMO]
            self.received += 1
            self.inbox = (self.inbox + [(now, src, rec)])[-INBOX_MAX:]
        return True

    def pending(self) -> bool:
        return bool(self.inbox)

    def noter_soi(self, fr, pos) -> bool:
        """Lot L3 (A5) : relevé de notre Link (``fr`` et ``pos`` de notre ``state``), pour juger les coups reçus."""
        return self.hist.noter(fr, pos)

    def victim_tick(self, now: float, zone: str | None, free: bool, life: int | None, link_pos, owner_of,
                    inject, direct, names: dict | None = None) -> int:
        """Applique les coups reçus. ``owner_of(clé)`` -> id de process de la marionnette SUSPENDUE de cet
        ennemi, ou None ; ``inject(owner, at, vec)`` -> code de CALL 9, ou None si le pack ne sait pas faire ;
        ``direct(nouvelle vie)`` -> True si écrit (mode dégradé). Renvoie le nombre de coups appliqués. Appelé par le
        tour de boucle ou, dès la réception (lot L1, A2), par le client : un seul à la fois, l'autre ne fait rien."""
        if not self._victime_lock.acquire(blocking=False):
            return 0                            # déjà en cours sur l'autre chemin : il prendra ce coup
        try:
            return self._victim_tick(now, zone, free, life, link_pos, owner_of, inject, direct, names)
        finally:
            self._victime_lock.release()

    def _victim_tick(self, now: float, zone: str | None, free: bool, life: int | None, link_pos, owner_of,
                     inject, direct, names: dict | None = None) -> int:
        with self._lock:
            box, self.inbox = self.inbox, []
        n = 0
        stalled = False                         # boîte sans réponse : le reste est jeté (pas 2 s par coup)
        names = names or {}
        for t, src, rec in box:
            who = f"{proc_label(rec['proc'])} (chez {names.get(src, '?')})"
            if stalled or now - t > FRESH or rec["zone"] != zone or not free:
                self.refused += 1               # vieux, autre zone, événement ou chargement : jeté sans bruit
                continue
            if life is None or life <= 0:
                self.refused += 1
                continue
            lp = _f3(link_pos)
            verdict = juger_compense(self.hist, rec, lp) if self.compense else "inconnu"
            if verdict in ("portee", "secours"):
                self.refused += 1
                self.n_juges += 1
                self.n_esquives += 1
                self._note("esquive", f"coup de {who} esquivé (tu n'étais plus là quand il a frappé)"
                           if verdict == "portee" else f"coup de {who} esquivé (tu étais déjà loin)", now)
                continue
            if verdict is None:
                self.n_juges += 1
                if lp is not None and math.dist(lp, rec["pos"]) > self.dist_max:
                    self.n_gardes += 1          # juste à l'image du coup : gardé (avant : refusé par DIST_MAX)
            elif lp is None or math.dist(lp, rec["pos"]) > self.dist_max:
                self.refused += 1
                self._note("esquive", f"coup de {who} esquivé (tu étais déjà loin)", now)
                continue
            owner = owner_of(rec["key"])
            if owner is None:
                self.refused += 1
                self._note("actif", f"coup de {who} ignoré : ce monstre est actif chez toi (il te frappe lui-même)",
                           now)
                continue
            code = inject(owner, rec["at"], rec["vec"])
            if code is not None:
                if code == 1:
                    n += 1
                    self.applied += 1
                    self._note("coup", f"touché par {who}", now)
                    if 0.0 <= now - t < 5.0 and len(self.delais) < 64:
                        self.delais.append(now - t)     # lot L1 (A1) : mesure
                else:
                    self.refused += 1           # -3 événement, -4 marionnette partie, -2 Medli/Makar…
                    stalled = code == R_STALLED
                continue
            if self.direct and direct is not None and rec["dmg"] > 0:
                if now - self.last_direct < DIRECT_INVULN:
                    self.refused += 1
                    continue
                new = direct_life(int(life), rec["dmg"] * (self.degats_mult if self.degats_mult in (2, 3, 4) else 1))
                if new < life and direct(new):
                    self.last_direct = now
                    life = new
                    n += 1
                    self.applied += 1
                    self._note("direct", f"touché par {who} : -{rec['dmg']} quart(s) (mode dégradé, sans recul)",
                               now)
                else:
                    self.refused += 1
                continue
            self.refused += 1
            self._note("pack", f"coup de {who} perdu : il faut le graphic pack v19 du dépôt (fantôme touchable), "
                       "ou --degats-directs", now)
        return n

    def status(self) -> str:
        return (f"fantôme touchable : {self.sent} coup(s) envoyé(s), {self.received} reçu(s), {self.applied} "
                f"appliqué(s), {self.refused} refusé(s)" + (", dégâts directs" if self.direct else "")
                + (f", compensés : {self.n_juges} jugé(s) à l'image du coup, {self.n_esquives} esquivé(s), "
                   f"{self.n_gardes} gardé(s) malgré la distance" if self.n_juges else ""))
