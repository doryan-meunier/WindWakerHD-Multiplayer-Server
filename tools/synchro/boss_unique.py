"""Boss unique, première version : Gohma (docs/etudes/serveur_jeu_v2.md §11, lot J5).

Ce que les joueurs voient : un seul combat pour tous. Chacun garde SA Gohma à l'écran (le filtre A9 ne change pas : ses
scripts, son introduction, sa mort et le réceptacle tournent chez chacun, par le jeu lui-même), mais le SERVEUR tient un
seul état commun, que chaque jeu suit :

* les PV (déjà partagés : synchro/boss_pv.py) ;
* les ROCHERS tombés sur la carapace : un rocher tombé chez l'un tombe chez tous (trois brisent la carapace) ;
* la MORT : le dernier coup donné chez l'un la tue chez tous, tout de suite (plus de « coup de grâce » à donner chez
  soi). Un joueur qui arrive en cours de combat reçoit l'état commun : sa Gohma rattrape les rochers, puis meurt si
  elle est déjà vaincue.

MIROIR DES ATTAQUES (2026-11-20, seconde version) : le serveur désigne une Gohma de RÉFÉRENCE : celle du joueur qui
l'a FRAPPÉE EN DERNIER (un coup qui lui retire des PV, ou un rocher tombé sur sa carapace : c'est donc lui qu'elle
vise) ; avant le premier coup, celle du premier joueur du groupe entré dans la stage ; la référence quitte la stage :
le premier qui se signale la remplace. Pas plus d'un changement par REF_CHANGE_MIN s (deux joueurs qui frappent à tour
de rôle ne la font pas pivoter à chaque coup). Elle choisit ses attaques comme d'habitude, contre son Link ;
chaque attaque qu'elle commence est annoncée (``att``) et les autres Gohma lancent LA MÊME au même moment, chacune
vers son propre Link. Pour qu'elles n'en lancent pas d'autres de leur côté, leur repos entre deux attaques (``m02EC[0]``,
s16 +0x414 : ``wait`` n'attaque que quand il vaut 0) est tenu au-dessus de zéro tant que la référence est là.
L'attaque est posée par les écritures que fait ``wait`` lui-même (0x020F6234 et synchro/ordres.py, « Gohma 234 ») :
``mAttackType`` (+0x410), ``m02E9`` (+0x411 : 0 ou 5, jamais une autre valeur), ``m02EA`` (+0x412), puis ``m02E4 = 1``
et ``mGohmaState = 0`` (+0x40C, +0x40E, un seul bloc), seulement depuis l'attente (``m02E4 == 0``).

ORIENTATION COMMUNE (mod natif 0.63.0, zone WWMPBOS1, natif/mod_wwmp/boss.c) : chaque Gohma se tourne vers son propre
Link à chaque image (``cLib_addCalcAngleS2`` vers ``fopAcM_searchPlayerAngleY``, d_a_btd.cpp ``move``) ; recopier
l'angle d'un autre jeu depuis le PC la ferait trembler. La référence envoie donc son angle (``ang`` : current.angle.y,
+0x322), et chez les autres c'est le JEU qui tourne Gohma vers cet angle : le mod le pose comme cible de la rotation du
pas (m5E90), avec la vitesse et le lissage du jeu. Sans ce mod (plus ancien, Cemu) : chaque Gohma reste tournée vers
son Link, tout le reste marche.

Comment, sans rien inventer [DECOMP d_a_btd.cpp ``damage_check``, relu dans le code HD 0x020F4D88, tests/
test_boss_unique.py] :

* rocher : le jeu pose ``m02F6 = 1`` (u8 +0x41E) quand le bloc du plafond touche Gohma ; ``damage_check`` (chaque pas,
  quel que soit l'état) le lit, le passe à 2 et lance la séquence (``m02E4 = 2``, ``mGohmaState = 0x14``), qui compte
  le rocher (``m6190``, u8 +0x63D4 en HD), brise la carapace au troisième (``m02E0 = 1``, u8 +0x408) et remet
  ``m02F6`` à 0 quand elle ressort. Nous n'écrivons QUE ``m02F6 = 1``, et seulement quand il vaut 0, carapace entière :
  exactement ce que fait le bloc. Tout le reste est fait par le jeu.
* mort : au dernier coup le jeu écrit ``m02E4 = 0xB`` (s16 +0x40C) et ``mGohmaState = 0x32`` (s16 +0x40E), PV à 0 ou
  moins (0x020F4FC8). Nous écrivons les mêmes valeurs (les deux mots d'un seul bloc de 4 octets), PV à 0 d'abord,
  seulement carapace brisée (comme dans le jeu : le coup à l'œil ne compte que carapace brisée).

Jamais pendant un événement (introduction, mort en cours), un chargement, ni sur un acteur qui n'est pas la Gohma
attendue (PV max 10, en-tête relu juste avant d'écrire).

Messages (type « bossu ») : client -> serveur ``{group, stage, key, roc, mort}`` quand l'état local change (et en
rappel) ; serveur -> membres du groupe dans la stage ``{..., from, nom}`` avec le MAXIMUM connu (``roc`` 0 à 3,
``mort`` 0 ou 1). Un rapport en retard reçoit le maximum en réponse. Oublié quand la stage se vide. Un ancien client
ignore le message : pour lui rien ne change (PV partagés, coup de grâce).
"""
from __future__ import annotations

import struct
import time

from jeu import progress as prog
from synchro import boss_pv

PROC_GOHMA = 234
MAX_PV = 10
ROCS_MAX = 3
# [HD-ASM, voir le docstring] : décalages dans l'acteur de Gohma (GC + 0x128 jusqu'à 0x2F6 ; m6190 lu dans le code)
OFF_CARAPACE = 0x408        # u8  m02E0 : 0 entière, 1 brisée, 2 seconde phase
OFF_E4 = 0x40C              # s16 m02E4 : 0xB = en train de mourir
OFF_ETAT = 0x40E            # s16 mGohmaState
OFF_ROCHER = 0x41E          # u8  m02F6 : 1 = un rocher vient de tomber (posé par le bloc), 2 = en cours
OFF_ROCS = 0x63D4           # u8  m6190 : rochers reçus (3 : la carapace se brise)
E4_MORT, ETAT_MORT = 0x0B, 0x32
OFF_ATT = 0x410             # s8 mAttackType, s8 m02E9 (+0x411), s8 m02EA (+0x412)
OFF_REPOS = 0x414           # s16 m02EC[0] : repos avant la prochaine attaque (0 : ``wait`` attaque)
OFF_ANGLE = 0x322           # s16 current.angle.y de l'acteur (ce que tourne cLib_addCalcAngleS2, 0x020F8E80)
ANGLE_PAS = 0.3             # s entre deux envois de l'angle de la référence
ANGLE_ECART = 0x100         # … et seulement s'il a tourné d'au moins cela (1,4 degré)
ANGLE_AGE = 3.0             # s : un angle reçu plus vieux n'est plus imposé (référence immobile : rappel toutes les 2 s)
E4_ATTENTE, E4_ATTAQUE = 0, 1
ATT_TYPES = (0, 1, 2, 3, 5)  # pince, coup de poing, feu, attaque vers le haut (Link pendu), double coup
REPOS_TENU, REPOS_SEUIL = 60, 30   # pas : le repos d'une Gohma qui suit est remis à 60 quand il passe sous 30
ATT_AGE = 1.0               # s : une attaque annoncée depuis plus longtemps n'est plus lancée (jamais en retard)
REF_SILENCE = 5.0           # s sans nouvelle de la référence : chaque Gohma reprend ses propres attaques
REF_CHANGE_MIN = 2.0        # s au moins entre deux changements de référence par un coup
RAPPEL = 2.0                # s : rappel de notre état (arrivée tardive)
PAS_ECRITURE = 1.5          # s entre deux écritures sur la même Gohma (le jeu a le temps de réagir)
DEBIT = 4                   # messages par seconde au plus


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def valide(msg) -> dict | None:
    """Message « bossu » validé (None : invalide, ou pas un boss de cette version)."""
    if not isinstance(msg, dict):
        return None
    stage, key, roc, mort = msg.get("stage"), msg.get("key"), msg.get("roc"), msg.get("mort")
    if not isinstance(stage, str) or not 0 < len(stage) <= 8:
        return None
    if boss_pv.key_proc(key) != PROC_GOHMA or not _int(roc) or not _int(mort):
        return None
    if not 0 <= roc <= ROCS_MAX or mort not in (0, 1):
        return None
    out = {"stage": stage, "key": key, "roc": roc, "mort": mort}
    ref = msg.get("ref")
    if _int(ref) and 0 < ref < 1 << 31:
        out["ref"] = ref
    att = attaque_valide(msg.get("att"))
    if att is not None:
        out["att"] = att
    ang = msg.get("ang")
    if _int(ang) and -0x8000 <= ang <= 0x7FFF:
        out["ang"] = ang
    return out


def attaque_valide(att) -> dict | None:
    """``att`` {n, t, a, b} : n° d'ordre, mAttackType, m02E9 (0 ou 5), m02EA (0 ou 1) ; None si invalide."""
    if not isinstance(att, dict):
        return None
    n, t, a, b = att.get("n"), att.get("t"), att.get("a"), att.get("b")
    if not all(_int(v) for v in (n, t, a, b)):
        return None
    if not 0 <= n < 1 << 31 or t not in ATT_TYPES or a not in (0, 5) or b not in (0, 1):
        return None
    return {"n": n, "t": t, "a": a, "b": b}


def lire(mem, base: int) -> dict | None:
    """État de la Gohma à ``base`` : {roc, carapace, rocher, mort} ; None si illisible ou aberrant."""
    bloc = mem.try_read(base + OFF_CARAPACE, OFF_ROCHER + 1 - OFF_CARAPACE)
    rocs = mem.try_read(base + OFF_ROCS, 1)
    if bloc is None or rocs is None or len(bloc) != OFF_ROCHER + 1 - OFF_CARAPACE:
        return None
    carapace, rocher = bloc[0], bloc[OFF_ROCHER - OFF_CARAPACE]
    e4, = struct.unpack_from(">h", bloc, OFF_E4 - OFF_CARAPACE)
    if carapace > 2 or rocher > 2 or rocs[0] > 8:
        return None
    etat, = struct.unpack_from(">h", bloc, OFF_ETAT - OFF_CARAPACE)
    ang = mem.try_read(base + OFF_ANGLE, 2)
    t, a, b = struct.unpack_from(">3b", bloc, OFF_ATT - OFF_CARAPACE)
    repos, = struct.unpack_from(">h", bloc, OFF_REPOS - OFF_CARAPACE)
    return {"roc": min(rocs[0], ROCS_MAX), "carapace": carapace, "rocher": rocher, "mort": int(e4 == E4_MORT),
            "e4": e4, "etat": etat, "att": (t, a, b), "repos": repos,
            "ang": None if ang is None or len(ang) != 2 else struct.unpack(">h", ang)[0]}


def _ecriture_permise(mem, b: dict) -> bool:
    """Relu juste avant d'écrire : c'est toujours cette Gohma (PV max 10), hors événement, hors chargement."""
    h = boss_pv.read_header(mem, b["base"])
    if h is None or h[0] != b["pid"] or h[1] != PROC_GOHMA:
        return False
    mx = mem.try_read(b["base"] + boss_pv.OFF_MAX_HEALTH, 1)
    if mx is None or mx[0] != MAX_PV:
        return False
    ev = prog.read_event(mem)
    if ev is None or ev.busy:
        return False
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    return nxt is not None and not nxt[0]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class BossUniqueSync:
    """Côté client. ``tick(mem, now, stage, bosses, free, can)`` à chaque échantillon (fil de travail), avec les mêmes
    ``bosses`` que synchro/boss_pv.py ; ``receive(msg, me)`` au fil des messages ; ``take_out`` / ``take_notes``."""

    def __init__(self, enabled: bool = False, group: str = "default", clock=time.monotonic):
        self.enabled, self.group, self.clock = bool(enabled), group, clock
        self.stage: str | None = None
        self.cible: dict[str, dict] = {}       # clé -> {roc, mort} : maximum reçu du serveur
        self.envoye: dict[str, tuple] = {}     # clé -> (roc, mort) déjà envoyés
        self.envoye_t: dict[str, float] = {}
        self.pids: dict[str, int] = {}
        self.ecrit_t: dict[str, float] = {}
        self.par: dict[str, str] = {}
        self.out: list[dict] = []
        self.emis: list[float] = []
        self.notes: list[str] = []
        self.dit: set = set()
        self.rochers_ecrits = self.morts_ecrites = self.recus = 0
        self.vu: dict[str, dict] = {}
        # miroir des attaques
        self.me: int | None = None
        self.ref: dict[str, int] = {}          # clé -> joueur dont la Gohma est la référence (dit par le serveur)
        self.ref_t: dict[str, float] = {}      # dernière nouvelle du serveur pour cette clé
        self.e4_avant: dict[str, int] = {}
        self.att_n = 0                         # n° de nos annonces (référence)
        self.att_recue: dict[str, dict] = {}   # clé -> {att, t} : attaque de la référence à lancer
        self.att_vues: dict[str, int] = {}     # clé -> dernier n° reçu (jamais deux fois)
        self.att_envoyer: dict[str, dict] = {}
        self.attaques_annoncees = self.attaques_lancees = self.attaques_perdues = self.repos_tenus = 0
        # orientation commune
        self.ang_envoye: dict[str, tuple] = {}  # clé -> (angle, heure) du dernier envoi (référence)
        self.ang_recu: dict[str, tuple] = {}    # clé -> (angle, heure) reçu de la référence
        self.angles_poses = 0

    # --- réseau ----------------------------------------------------------------------------------------------------
    def take_out(self, now: float | None = None) -> list[dict]:
        if not self.out:
            return []
        now = self.clock() if now is None else now
        self.emis = [t for t in self.emis if 0 <= now - t < 1.0]
        n = max(0, DEBIT - len(self.emis))
        out, self.out = self.out[:n], self.out[n:]
        self.emis += [now] * len(out)
        return out

    def take_notes(self) -> list[str]:
        n, self.notes = self.notes, []
        return n

    def receive(self, msg: dict, me: int | None = None) -> int:
        """Maximum reçu ; renvoie 1 s'il dépasse ce qu'on connaissait."""
        if not self.enabled or msg.get("group", self.group) != self.group:
            return 0
        if me is not None and msg.get("from") == me:
            return 0
        rec = valide(msg)
        if rec is None or rec["stage"] != self.stage:
            return 0
        key = rec["key"]
        if me is not None:
            self.me = me
        if "ref" in rec:
            self.ref[key] = rec["ref"]
            self.ref_t[key] = self.clock()
        if "ang" in rec and msg.get("from") is not None and msg.get("from") == self.ref.get(key):
            self.ang_recu[key] = (rec["ang"], self.clock())
        att = rec.get("att")
        if att is not None and msg.get("from") is not None and msg.get("from") == self.ref.get(key) \
                and self.att_vues.get(key) != att["n"]:
            self.att_vues[key] = att["n"]
            self.att_recue[key] = {"att": att, "t": self.clock()}
        old = self.cible.get(key, {"roc": 0, "mort": 0})
        new = {"roc": max(old["roc"], rec["roc"]), "mort": max(old["mort"], rec["mort"])}
        if new == old:
            return 0
        self.cible[key] = new
        self.recus += 1
        who = msg.get("nom")
        if isinstance(who, str) and who:
            self.par[key] = who[:24]
        return 1

    def reset_network(self) -> None:
        self.cible.clear()
        self.envoye.clear()
        self.envoye_t.clear()
        self.out.clear()
        for d in (self.ref, self.ref_t, self.att_recue, self.att_vues, self.att_envoyer, self.ang_envoye,
                  self.ang_recu):
            d.clear()                           # sans serveur : chaque Gohma reprend ses propres attaques

    # --- jeu -------------------------------------------------------------------------------------------------------
    def _nouvelle_stage(self, stage: str | None) -> None:
        self.stage = stage
        for d in (self.cible, self.envoye, self.envoye_t, self.pids, self.ecrit_t, self.par, self.vu, self.ref,
                  self.ref_t, self.e4_avant, self.att_recue, self.att_vues, self.att_envoyer, self.ang_envoye,
                  self.ang_recu):
            d.clear()
        self.out.clear()
        self.dit.clear()

    def _envoyer(self, key: str, etat: dict, now: float) -> None:
        msg = {"type": "bossu", "group": self.group, "stage": self.stage, "key": key, "roc": etat["roc"],
               "mort": etat["mort"]}
        if key in self.ref:
            msg["ref"] = self.ref[key]          # ce que nous croyons : le serveur nous corrige si c'est faux
        att = self.att_envoyer.pop(key, None)
        if att is not None:
            msg["att"] = att
        if self.me is not None and self.ref.get(key) == self.me and etat.get("ang") is not None:
            msg["ang"] = etat["ang"]            # la référence : son orientation (commune avec le mod 0.63.0)
            self.ang_envoye[key] = (etat["ang"], now)
        self.out = [m for m in self.out if m["key"] != key or "att" in m] + [msg]
        self.envoye[key] = (etat["roc"], etat["mort"])
        self.envoye_t[key] = now

    def _dire(self, quoi, texte: str) -> None:
        if quoi not in self.dit:
            self.dit.add(quoi)
            self.notes.append(texte)

    def tick(self, mem, now: float | None, stage: str | None, bosses: list | None, free: bool, can: bool,
             mb=None) -> int:
        """Renvoie le nombre d'écritures faites. ``mb`` : la boîte du mod (orientation commune ; None : sans)."""
        if not self.enabled:
            return 0
        now = self.clock() if now is None else now
        if stage != self.stage:
            self._nouvelle_stage(stage)
        if stage is None or not bosses:
            return 0
        ecrits = 0
        for b in bosses:
            if b.get("proc") != PROC_GOHMA or b.get("max") != MAX_PV:
                continue
            key = boss_pv.boss_key(PROC_GOHMA, b.get("params", 0))
            etat = lire(mem, b["base"])
            if etat is None:
                continue
            if self.pids.get(key) != b["pid"]:
                self.pids[key] = b["pid"]             # nouvelle instance (entrée, Game Over) : on repart de son état
                self.envoye.pop(key, None)
                self.ecrit_t.pop(key, None)
            self.vu[key] = etat
            self._dire(("intro", key), "Gohma est commune à tous les joueurs : un rocher tombé chez l'un tombe chez "
                                       "tous, et le dernier coup la tue chez tout le monde")
            # 0. miroir des attaques : la référence annonce chaque attaque qu'elle commence
            e4_avant, self.e4_avant[key] = self.e4_avant.get(key), etat["e4"]
            suis_ref = self.me is not None and self.ref.get(key) == self.me
            if suis_ref and e4_avant is not None and e4_avant != E4_ATTAQUE and etat["e4"] == E4_ATTAQUE:
                t, a, bb = etat["att"]
                att = attaque_valide({"n": self.att_n + 1, "t": t, "a": 5 if a >= 5 else 0, "b": 1 if bb else 0})
                if att is not None:
                    self.att_n += 1
                    self.att_envoyer[key] = att
                    self.attaques_annoncees += 1
            # 1. notre état vers le serveur
            tourne = False
            if suis_ref and etat.get("ang") is not None:
                a0, t0 = self.ang_envoye.get(key, (None, -1e9))
                ecart = 0x10000 if a0 is None else abs(((etat["ang"] - a0 + 0x8000) & 0xFFFF) - 0x8000)
                tourne = ecart >= ANGLE_ECART and now - t0 >= ANGLE_PAS
            if self.envoye.get(key) != (etat["roc"], etat["mort"]) or now - self.envoye_t.get(key, -1e9) >= RAPPEL \
                    or key in self.att_envoyer or tourne:
                self._envoyer(key, etat, now)
            # 1 bis. miroir des attaques : une Gohma qui suit lance l'attaque de la référence, et aucune autre
            suit = (self.me is not None and key in self.ref and not suis_ref
                    and now - self.ref_t.get(key, -1e9) < REF_SILENCE)
            if suit and not etat["mort"] and free and can:
                # orientation commune : l'angle de la référence, que le JEU prend pour cible de sa rotation (mod 0.63.0)
                ar = self.ang_recu.get(key)
                if ar is not None and now - ar[1] <= ANGLE_AGE and mb is not None \
                        and callable(getattr(mb, "gohma_angle", None)):
                    try:
                        if mb.gohma_angle(ar[0]):
                            self.angles_poses += 1
                    except Exception:           # noqa: BLE001 : boîte illisible pendant un chargement
                        pass
                recue = self.att_recue.get(key)
                if recue is not None and now - recue["t"] > ATT_AGE:
                    del self.att_recue[key]                 # trop vieille : jamais lancée en retard
                    self.attaques_perdues += 1
                    recue = None
                # toute l'attente (m02E4 == 0), quel que soit son sous-état : regard autour d'elle (2), tête levée
                # (10, 11), de côté (0x14, 0x15) compris. Banc du 2026-11-20 : tenue seulement dans les états 0 et 1,
                # elle lançait sa propre attaque en sortant du « regard autour d'elle » (1 fois en 2 minutes)
                if etat["e4"] == E4_ATTENTE:
                    if recue is not None:
                        if _ecriture_permise(mem, b):
                            att = recue["att"]
                            mem.write(b["base"] + OFF_ATT, struct.pack(">3b", att["t"], att["a"], att["b"]))
                            mem.write(b["base"] + OFF_E4, struct.pack(">hh", E4_ATTAQUE, 0))
                            del self.att_recue[key]
                            self.attaques_lancees += 1
                            ecrits += 1
                    elif 0 <= etat["repos"] < REPOS_SEUIL and _ecriture_permise(mem, b):
                        mem.write(b["base"] + OFF_REPOS, struct.pack(">h", REPOS_TENU))
                        self.repos_tenus += 1
            # 2. l'état commun vers notre Gohma
            cible = self.cible.get(key)
            if cible is None or etat["mort"] or not (free and can):
                continue
            if now - self.ecrit_t.get(key, -1e9) < PAS_ECRITURE:
                continue
            qui = self.par.get(key, "un autre joueur")
            if cible["mort"] and etat["carapace"] >= 1:
                if not _ecriture_permise(mem, b):
                    continue
                mem.write(b["base"] + boss_pv.OFF_HEALTH, struct.pack(">b", 0))
                mem.write(b["base"] + OFF_E4, struct.pack(">hh", E4_MORT, ETAT_MORT))
                self.ecrit_t[key] = now
                self.morts_ecrites += 1
                ecrits += 1
                self._dire(("mort", key, b["pid"]), f"Gohma est vaincue ({qui} a donné le dernier coup) : elle meurt "
                                                    "chez toi aussi")
            elif (cible["roc"] > etat["roc"] or cible["mort"]) and etat["carapace"] == 0 and etat["rocher"] == 0:
                if not _ecriture_permise(mem, b):
                    continue
                mem.write(b["base"] + OFF_ROCHER, b"\x01")
                self.ecrit_t[key] = now
                self.rochers_ecrits += 1
                ecrits += 1
                self.notes.append(f"Gohma : un rocher est tombé sur sa carapace chez {qui} "
                                  f"({min(etat['roc'] + 1, ROCS_MAX)}/{ROCS_MAX}) : il tombe chez toi aussi")
        return ecrits

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        if not self.vu:
            return f"aucun boss commun ici ({self.recus} reçu(s))"
        parts = []
        for key, e in sorted(self.vu.items()):
            c = self.cible.get(key)
            parts.append(f"Gohma : {e['roc']}/{ROCS_MAX} rocher(s), carapace {'brisée' if e['carapace'] else 'entière'}"
                         + (", vaincue" if e["mort"] else "")
                         + (f" (commun : {c['roc']} rocher(s){', vaincue' if c['mort'] else ''})" if c else ""))
        return (", ".join(parts) + f" ; {self.rochers_ecrits} rocher(s) et {self.morts_ecrites} mort(s) appliqué(s) ; "
                f"attaques : {self.attaques_annoncees} annoncée(s), {self.attaques_lancees} suivie(s), "
                f"{self.attaques_perdues} perdue(s) ; orientation commune : {self.angles_poses} pose(s)")


# ---------------------------------------------------------------------------
# Serveur
# ---------------------------------------------------------------------------
class BossUniqueTable:
    """Maximum par (groupe, stage, clé). ``rapport`` renvoie (genre, enregistrement) : « haut » (l'état commun
    avance : relais aux autres), « retard » (l'auteur est en retard : l'état commun lui est renvoyé), « egal »."""

    def __init__(self, clock=time.monotonic):
        self.etat: dict[tuple[str, str, str], dict] = {}
        self.clock = clock
        self.ref_t: dict[tuple[str, str, str], float] = {}     # heure du dernier changement de référence par un coup

    def frappe(self, group: str, stage: str, key: str, pid: int, now: float | None = None) -> dict | None:
        """``pid`` vient de frapper le boss (PV retirés, rocher) : il devient la référence (c'est lui qu'elle vise).
        Rend l'état si la référence a changé (à diffuser), None sinon (déjà lui, boss inconnu, changement trop
        récent)."""
        k = (group, stage, key)
        old = self.etat.get(k)
        if old is None or old.get("ref") == pid:
            return None
        now = self.clock() if now is None else now
        if now - self.ref_t.get(k, float("-inf")) < REF_CHANGE_MIN:
            return None
        self.ref_t[k] = now
        new = dict(old, ref=pid)
        self.etat[k] = new
        return new

    def rapport(self, group: str, rec: dict, nom: str, pid: int | None = None,
                presents=None) -> tuple[str, dict | None]:
        """``pid`` : l'auteur ; ``presents`` : joueurs du groupe dans la stage (None : inconnu). La RÉFÉRENCE (miroir
        des attaques) est le premier auteur ; elle n'est plus dans la stage : l'auteur la remplace. Un changement de
        référence est un état qui avance (« haut » : tous l'apprennent)."""
        k = (group, rec["stage"], rec["key"])
        old = self.etat.get(k)
        if old is None:
            new = {"stage": rec["stage"], "key": rec["key"], "roc": rec["roc"], "mort": rec["mort"], "nom": nom}
            if pid is not None:
                new["ref"] = pid
            self.etat[k] = new
            return ("haut" if (rec["roc"] or rec["mort"] or pid is not None) else "egal"), new
        change = False
        if pid is not None and presents is not None and old.get("ref") not in presents:
            old = dict(old, ref=pid)
            self.etat[k] = old
            change = True
        if rec["roc"] > old["roc"] or rec["mort"] > old["mort"]:
            new = dict(old, roc=max(old["roc"], rec["roc"]), mort=max(old["mort"], rec["mort"]), nom=nom)
            self.etat[k] = new
            if pid is not None and rec["roc"] > old["roc"]:
                new = self.frappe(group, rec["stage"], rec["key"], pid) or new    # le rocher est un coup
            return "haut", new
        if change:
            return "haut", old
        if rec["roc"] < old["roc"] or rec["mort"] < old["mort"] or (pid is not None and rec.get("ref") != old.get("ref")):
            return "retard", old
        return "egal", None

    def reference(self, group: str, stage: str, key: str) -> int | None:
        return (self.etat.get((group, stage, key)) or {}).get("ref")

    def gc(self, present: set[tuple[str, str]]) -> list[tuple[str, str, str]]:
        """Oublie les boss des stages où plus aucun membre du groupe n'est présent."""
        partis = [k for k in self.etat if (k[0], k[1]) not in present]
        for k in partis:
            del self.etat[k]
            self.ref_t.pop(k, None)
        return partis
