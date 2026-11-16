"""A3 : bâtons partagés entre les PC (logique pure, testable sans Cemu).

Conception : docs/etudes/batons_partages.md. Résumé :

  * un joueur A ramasse un bâton de Bokoblin (proc 463) : son client envoie `stick grab` ;
    le serveur arbitre (premier arrivé) et relaie ; chez B, le bâton de même clé quitte le
    sol et devient une « marionnette » : vrai bâton local, mode 3 + bit CARRY (plus
    ramassable), dont Python écrit à chaque tick la matrice de base du modèle pour le mettre
    dans la main du fantôme de A (W = G · R, R = B⁻¹ · S envoyé par A dans `state.stk`) ;
  * A lance ou lâche le bâton : `state.stk` porte la matrice monde (vol), puis `stick rest`
    quand il est posé ; chez B la marionnette est relâchée à cet endroit (elle tombe de 10 u
    et redevient ramassable) ;
  * conflit (les deux ramassent en même temps) : le perdant reçoit `deny` ; son bâton est
    d'abord LÂCHÉ par écritures (mode 2 puis CARRY effacé : setActorPointer vide la main de
    Link proprement à la frame suivante), puis, une fois posé et plus pointé par Link, pris
    comme marionnette pour le gagnant (jamais de commande 5 sur un bâton dans la main de Link) ;
  * le porteur renvoie `grab` (cause « rappel ») toutes les GRAB_REFRESH s : un receveur qui
    a relâché la marionnette (porteur muet, reconnexion) la reprend ;
  * flamme : `stk.f` (tenu) ou `stick flame` (bâton libre) ; écrite si l'écart dépasse
    FLAME_TOL ou au passage 0 <-> allumé ; pour éteindre on écrit 1, jamais 0.

Identité d'un bâton (StickIds) : « arme:<clé du Bokoblin propriétaire> » (Bokoblins = acteurs
de stage, même clé partout), transmise lors d'une recréation A2 (rekey), sinon
« boko:<enemy_key(463, params, origine)> », sinon rapprochement spatial à moins de
MATCH_DIST d'un bâton libre de même type.

StickSync ne lit ni n'écrit la mémoire : observe() reçoit le relevé, on_message() les
messages, step() renvoie des travaux que le client exécute (execute_stick_job) avec
l'échantillonneur (CemuSampler / FakeSampler), result() reçoit leur issue. Les messages à
envoyer sortent par take_outgoing(), le champ `state.stk` par stk().
"""
from __future__ import annotations

import base64
import functools
import math
import struct
import threading

from synchro import armes
from commun.protocole import enemy_key

# --- états d'un bâton (par clé réseau) ----------------------------------------
LIBRE = "libre"            # bâton local au repos (ou qui tombe), ramassable
TENU_MOI = "tenu_moi"      # le Link local le tient
VOL_MOI = "vol_moi"        # lancé ou lâché par le Link local, pas encore posé
TENU_AUTRE = "tenu_autre"  # marionnette dans la main du fantôme du porteur
VOL_AUTRE = "vol_autre"    # marionnette pilotée par la matrice monde reçue
A_CREER = "a_creer"        # commande 4 à faire (ou faite, en attente de la prise)
ABSENT = "absent"          # n'existe pas chez nous
ENNEMI = "ennemi"          # tenu par un ennemi (mode 2) : pas partagé, suit son ennemi
LACHER = "lacher"          # conflit perdu : le bâton quitte la main du Link local (écritures)
PUPPET_STATES = (TENU_AUTRE, VOL_AUTRE)

# --- réglages ------------------------------------------------------------------
MATCH_DIST = 200.0         # u : rapprochement spatial d'une clé inconnue
ALIGN_DIST = 30.0          # u : au-delà, un bâton libre est remis à la position convenue
REST_TICKS = 3             # relevés immobiles (mode 0) avant d'annoncer « posé »
REST_EPS = 0.5             # u : immobile
HOLD_STALE = 5.0           # s sans `state` du porteur : la marionnette est relâchée
STK_FRESH = 1.0            # s : âge maximal du `state.stk` utilisé pour piloter
ROOM_SETTLE = 2.0          # s dans la zone avant toute transition
MAX_TRANSITIONS_PER_TICK = 2
RETRY = 2.0                # s entre deux essais d'une transition ratée
MAX_TRIES = 3
FALL_WAIT = 3.0            # s : attente qu'un bâton qui tombe se pose avant la prise
CREATE_SEEN = 6.0          # s : délai pour voir au relevé un bâton créé (rescan)
GONE_SCANS = 2             # relevés sans le bâton tenu / en vol avant `gone`
ALIGN_STABLE = 1.0         # s d'immobilité avant l'alignement par l'autorité
FLAME_TOL = 30             # écart de flamme toléré (images)
FLAME_ON = 900
CREATE_Y = 30.0            # u : un bâton créé pour un fantôme naît à ses pieds + 30
DROP_UP = 10.0             # u : relâché 10 u au-dessus de la position de repos
REST_DY = -5.0             # u : position de repos = translation de la matrice - 5 en y
DROP_MODES = ("ecrire", "recreer")
PUPPET_MODES = {"joueur": armes.MODE_PLAYER, "ennemi": armes.MODE_ENEMY}
GRAB_REFRESH = 3.0         # s : le porteur renvoie `grab` (cause rappel) tant qu'il tient le bâton
STK_LOST = 2.0             # s : porteur actif mais sans stk pour cette clé : marionnette relâchée
VOL_MAX = 8.0              # s : bâton lancé qui ne se pose jamais : « posé » d'office là où il est
FREE_SCANS = 2             # relevés où Link ne pointe plus le bâton avant prise / suppression
MAX_MB_CALLS = 2           # appels de boîte aux lettres d'A3 par tick (création, suppression)
PENDING_DIST = 60.0        # u : bâton apparu près d'une création restée en attente : adopté
PENDING_KEEP = 10.0        # s : durée de cette adoption possible

# --- messages ------------------------------------------------------------------
EVENTS_IN = ("grab", "rest", "gone", "flame", "taken")      # client -> serveur
KEY_MAX = 96
STICK_MAX = 64             # clés par zone au serveur
STICK_TYPES = (armes.TYPE_STICK, armes.TYPE_MACHETE, armes.TYPE_LIT)
CAUSE_MAX = 16


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# positions jusqu'à 1e6 u (en mer, carrés de 100 000 u : à 1e5, tout bâton en mer était refusé ; audit natif)
def _vec(v, lim: float = 1e6):
    if isinstance(v, (list, tuple)) and len(v) == 3 and all(_num(x) and abs(x) < lim for x in v):
        return [float(x) for x in v]
    return None


def valid_stick(msg: dict) -> dict | None:
    """Champs d'un message `stick` client -> serveur, nettoyés ; None si invalide.
    {ev, key, kind?, flame?, pos?, rot?, cause?} (kind = type du bâton 0/1/7) (zone vérifiée à part)."""
    if not isinstance(msg, dict):
        return None
    ev, key = msg.get("ev"), msg.get("key")
    if ev not in EVENTS_IN or not isinstance(key, str) or not 0 < len(key) <= KEY_MAX:
        return None
    out = {"ev": ev, "key": key}
    if "kind" in msg:                      # type du bâton (« type » est celui du message)
        if msg["kind"] not in STICK_TYPES or not _int(msg["kind"]):
            return None
        out["kind"] = msg["kind"]
    if "flame" in msg:
        if not _int(msg["flame"]) or not -1 <= msg["flame"] <= FLAME_ON:
            return None
        out["flame"] = msg["flame"]
    if "pos" in msg:
        p = _vec(msg["pos"])
        if p is None:
            return None
        out["pos"] = p
    if "rot" in msg:
        r = msg["rot"]
        if not (isinstance(r, (list, tuple)) and len(r) == 3 and all(_int(x) and -0x8000 <= x <= 0xFFFF for x in r)):
            return None
        out["rot"] = list(r)
    if "cause" in msg:
        if not isinstance(msg["cause"], str) or len(msg["cause"]) > CAUSE_MAX:
            return None
        out["cause"] = msg["cause"]
    if ev in ("grab", "rest") and "pos" not in out:
        return None
    return out


# --- matrices 3×4 (12 flottants, ligne par ligne, comme Mtx du jeu) --------------
def mat_mul(a, b) -> list[float]:
    """a · b, matrices affines 3×4."""
    out = []
    for i in range(3):
        r = a[4 * i:4 * i + 4]
        for j in range(4):
            v = r[0] * b[j] + r[1] * b[4 + j] + r[2] * b[8 + j]
            if j == 3:
                v += r[3]
            out.append(v)
    return out


def mat_inv(m) -> list[float]:
    """Inverse d'une matrice affine 3×4 (partie 3×3 quelconque inversible)."""
    a, b, c, tx, d, e, f, ty, g, h, i, tz = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError("matrice non inversible")
    k = 1.0 / det
    r = [(e * i - f * h) * k, (c * h - b * i) * k, (b * f - c * e) * k,
         (f * g - d * i) * k, (a * i - c * g) * k, (c * d - a * f) * k,
         (d * h - e * g) * k, (b * g - a * h) * k, (a * e - b * d) * k]
    t = [-(r[0] * tx + r[1] * ty + r[2] * tz), -(r[3] * tx + r[4] * ty + r[5] * tz),
         -(r[6] * tx + r[7] * ty + r[8] * tz)]
    return [r[0], r[1], r[2], t[0], r[3], r[4], r[5], t[1], r[6], r[7], r[8], t[2]]


def relative(b, s) -> list[float]:
    """R = B⁻¹ · S : bâton S relatif à la matrice de base B du corps de Link."""
    return mat_mul(mat_inv(b), s)


def compose(g, r) -> list[float]:
    """W = G · R : matrice monde du bâton dans la main du fantôme de base G."""
    return mat_mul(g, r)


def translation(m) -> tuple[float, float, float]:
    return (m[3], m[7], m[11])


def _ang(a) -> float:
    return (int(a) & 0xFFFF) * 2 * math.pi / 65536


def base_matrix(pos, rot_y: int) -> list[float]:
    """Comme wwlib.pose.base_matrix : T(pos) · Ry(rot_y)."""
    c, s = math.cos(_ang(rot_y)), math.sin(_ang(rot_y))
    return [c, 0.0, s, float(pos[0]), 0.0, 1.0, 0.0, float(pos[1]), -s, 0.0, c, float(pos[2])]


def rest_matrix(pos, rot=(0, 0, 0)) -> list[float]:
    """Matrice de repos T(pos) · ZXYrot(rot) = T · Ry · Rx · Rz (mDoMtx_stack_c::ZXYrotM)."""
    rx, ry, rz = (_ang(v) for v in (list(rot) + [0, 0, 0])[:3])
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    ry_m = [cy, 0.0, sy, 0.0, 0.0, 1.0, 0.0, 0.0, -sy, 0.0, cy, 0.0]
    rx_m = [1.0, 0.0, 0.0, 0.0, 0.0, cx, -sx, 0.0, 0.0, sx, cx, 0.0]
    rz_m = [cz, -sz, 0.0, 0.0, sz, cz, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    m = mat_mul(mat_mul(ry_m, rx_m), rz_m)
    m[3], m[7], m[11] = float(pos[0]), float(pos[1]), float(pos[2])
    return m


def encode_rel(r) -> str | list:
    """Matrice relative pour le réseau : 12 float16 big-endian en base64 (même format que
    wwlib.pose.encode pour une matrice) ; liste de flottants si une valeur sort du float16."""
    if all(abs(v) < 60000 for v in r):
        return base64.b64encode(struct.pack(">12e", *r)).decode("ascii")
    return [round(float(v), 3) for v in r]


def decode_rel(m) -> list[float] | None:
    try:
        if isinstance(m, str):
            raw = base64.b64decode(m, validate=True)
            if len(raw) != 24:
                return None
            v = list(struct.unpack(">12e", raw))
        elif isinstance(m, (list, tuple)) and len(m) == 12:
            v = [float(x) for x in m]
        else:
            return None
    except (ValueError, TypeError, struct.error):
        return None
    return v if all(math.isfinite(x) for x in v) else None


def world_list(m) -> list[float]:
    """Matrice monde pour le réseau (vol) : float32 arrondis à 0,001 (un float16 a un pas de
    4 u vers 5 000 : inutilisable pour une position absolue)."""
    return [round(float(v), 3) for v in m]


def ghost_matrix(o: dict | None, y_offset: float = 0.0) -> list[float] | None:
    """G du fantôme d'un joueur distant, comme GhostManager.update (pos + y_offset, rotY)."""
    if not o:
        return None
    p = o.get("pos")
    if not (isinstance(p, (list, tuple)) and len(p) == 3 and all(_num(v) for v in p)) or not _num(o.get("rotY", 0)):
        return None
    return base_matrix((p[0], p[1] + y_offset, p[2]), int(o.get("rotY", 0)))


def same_type(a: int, b: int) -> bool:
    """Bâton éteint (0) et allumé (7) sont le même objet ; la machette (1) est à part."""
    def norm(t):
        return armes.TYPE_STICK if (t & 0xFF) == armes.TYPE_LIT else t & 0xFF
    return norm(a) == norm(b)


def _dist(a, b) -> float:
    return math.dist(a[:3], b[:3])


# ---------------------------------------------------------------------------
class StickIds:
    """pid local -> clé réseau. Remise à zéro au changement de zone (sauf ce qu'on garde)."""

    def __init__(self):
        self.keys: dict[int, str] = {}

    def reset(self, keep=()) -> None:
        keep = set(keep)
        self.keys = {p: k for p, k in self.keys.items() if p in keep}

    def learn(self, bokos) -> None:
        """Bokoblins vivants : le bâton qu'ils détiennent (bk+0x1360) prend la clé « arme: ».
        Une clé de repli « boko: » est remplacée ; une clé « arme: » déjà connue est gardée."""
        for b in bokos or ():
            w, k = b.get("weapon"), b.get("key")
            if w in armes.NO_ID or not k:
                continue
            cur = self.keys.get(w)
            if cur is None or cur.startswith("boko:"):
                self.keys[w] = "arme:" + k

    def key_of(self, s: dict) -> str:
        pid = s.get("pid")
        k = self.keys.get(pid)
        if k is None:
            home = s.get("home") or s.get("pos") or (0.0, 0.0, 0.0)
            k = self.keys[pid] = "boko:" + enemy_key(armes.PROC_BOKO, s.get("params", 0) & 0xFFFFFFFF, home)
        return k

    def set(self, pid: int, key: str) -> None:
        self.keys[pid] = key

    def drop(self, pid) -> None:
        self.keys.pop(pid, None)

    def rekey(self, old: int, new: int | None) -> bool:
        """A2 a recréé l'orphelin `old` sous l'id `new` : la clé suit le bâton."""
        if new is None or old not in self.keys:
            return False
        self.keys[new] = self.keys.pop(old)
        return True

    def rename(self, old_key: str, new_key: str) -> None:
        for p, k in list(self.keys.items()):
            if k == old_key:
                self.keys[p] = new_key


class StickEntry:
    """Un bâton partagé, par clé."""

    def __init__(self, key: str, state: str = ABSENT):
        self.key = key
        self.state = state
        self.pid: int | None = None
        self.base: int | None = None
        self.owner: int | None = None      # porteur distant (marionnette) ou gagnant attendu
        self.type = armes.TYPE_STICK
        self.flame = 0
        self.mode: int | None = None
        self.status = 0
        self.mtx: list[float] | None = None
        self.angle = (0, 0, 0)
        self.pos: tuple | None = None      # position de repos (translation - 5 en y)
        self.rest_pos: tuple | None = None  # dernière position de repos vue (avant la prise)
        self.stable = 0
        self.still_since: float | None = None
        self.thrown = False
        self.missing = 0
        self.grabbed = False               # marionnette : prise faite (mode 3 + CARRY)
        self.want: dict | None = None      # transition à faire
        self.tries = 0
        self.next_try = 0.0
        self.wait_since: float | None = None
        self.created_t: float | None = None
        self.last_w: list[float] | None = None
        self.flame_target: int | None = None   # bâton libre : flamme reçue (message flame)
        self.creates = 0                   # créations pour cette clé (limite MAX_TRIES)
        self.aligns = 0                    # déplacements d'alignement (limite MAX_TRIES)
        # revue A3
        self.free_scans = 0                # relevés consécutifs où Link (+0x6594, +0x65A4) ne le pointe pas
        self.after: dict | None = None     # LACHER : transition qui suit le lâcher (prise / deplace)
        self.remote_rest: tuple | None = None   # TENU_MOI : « rest » du gagnant reçu avant le deny
        self.doomed = False                # clé « #e » : doublon local, supprimé dès qu'il est libre
        self.stk_seen = 0.0                # marionnette : dernier stk frais de sa clé
        self.refresh_t = 0.0               # TENU_MOI : dernier `grab` envoyé (rappel)
        self.vol_t = 0.0                   # VOL_MOI : début du vol
        self.pending: tuple | None = None  # (pos, t) : création restée en attente (boîte aux lettres)
        self.gave_up = False               # abandon : les `grab` de rappel ne relancent pas la prise


def _locked(fn):
    """StickSync est utilisé par la boucle asyncio (messages, départs, déconnexion) et par le
    thread de travail (relevé, transitions) : un verrou par instance."""
    @functools.wraps(fn)
    def wrap(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrap


class StickSync:
    """Machine à états des bâtons partagés (voir le docstring du module)."""

    ROOM_SETTLE = ROOM_SETTLE

    def __init__(self, enabled: bool = True, drop_mode: str = "ecrire", puppet_mode: str = "joueur"):
        if drop_mode not in DROP_MODES:
            raise ValueError(f"mode de relâchement inconnu : {drop_mode} (attendu : {', '.join(DROP_MODES)})")
        if puppet_mode not in PUPPET_MODES:
            raise ValueError(f"mode de marionnette inconnu : {puppet_mode} (attendu : {', '.join(PUPPET_MODES)})")
        self._lock = threading.RLock()
        self.enabled = enabled
        self.drop_mode = drop_mode
        self.puppet_mode = PUPPET_MODES[puppet_mode]
        self.ids = StickIds()
        self.entries: dict[str, StickEntry] = {}
        self.zone: str | None = None
        self.room_t = None
        self.zone_t = 0.0
        self.inbox: list[tuple[float, dict]] = []
        self.out: list[dict] = []
        self.ref: dict[str, tuple] = {}         # clé -> position de repos convenue (reçue ou annoncée)
        self.ref_remote: set[str] = set()       # clés dont `ref` vient d'un autre joueur (rest reçu)
        self.announced: dict[str, tuple] = {}   # autorité : position annoncée par « rest aligne »
        self.remote_keys: set[str] = set()      # clés citées par les autres
        self.to_delete: list[int] = []          # marionnettes de l'ancienne salle à supprimer
        self.auth = False
        self.resend_hold = False
        self.cur_stk: dict | None = None
        self.me: int | None = None
        self.dead: dict[int, float] = {}        # pids supprimés par nous : ignorés au relevé quelques s
        self.hold_ptrs: set[int] = set()        # Link+0x6594 / +0x65A4 au dernier relevé
        # compteurs (affichage)
        self.grabs = self.rests = self.puppets_made = self.conflicts = self.failures = 0
        self.last = ""

    # --- utilitaires ----------------------------------------------------------
    def _entry(self, key: str) -> StickEntry:
        e = self.entries.get(key)
        if e is None:
            e = self.entries[key] = StickEntry(key)
        return e

    @_locked
    def puppet_pids(self) -> set[int]:
        return {e.pid for e in self.entries.values() if e.pid is not None and e.state in PUPPET_STATES}

    @_locked
    def busy_pids(self) -> set[int]:
        """Ids à cacher à A2 (find_orphans skip) : marionnettes, bâtons en transition, doublons
        « #e » (supprimés par A3, pas recréés), marionnettes de l'ancienne salle."""
        out = self.puppet_pids()
        out |= {e.pid for e in self.entries.values()
                if e.pid is not None and (e.want or e.doomed or e.state == LACHER)}
        return out | set(self.to_delete) | set(self.dead)

    def _send(self, ev: str, key: str, **kw) -> None:
        # zone de l'envoi : un « rest » émis au changement de salle porte l'ANCIENNE zone
        self.out.append(dict(ev=ev, key=key, zone=self.zone, **kw))

    @_locked
    def take_outgoing(self) -> list[dict]:
        out, self.out = self.out, []
        return out

    @_locked
    def stk(self) -> dict | None:
        return self.cur_stk

    def reset(self) -> None:
        """Nouvelle zone (ou salle rechargée) : on oublie tout sauf le bâton tenu par le Link local.
        Un bâton lancé pas encore posé (VOL_MOI) est annoncé posé là où il est, dans l'ancienne
        zone (sinon le serveur le croirait encore tenu et l'emmènerait de zone en zone)."""
        for e in self.entries.values():
            if e.state == VOL_MOI:
                p = e.pos or e.rest_pos or (0.0, 0.0, 0.0)
                self._send("rest", e.key, kind=e.type, flame=e.flame, pos=[round(v, 2) for v in p],
                           rot=[int(a) for a in e.angle], cause="pose")
        keep = {k: e for k, e in self.entries.items() if e.state == TENU_MOI}
        self.ids.reset({e.pid for e in keep.values()})
        self.dead.clear()
        self.entries = keep
        self.ref.clear()
        self.ref_remote.clear()
        self.announced.clear()
        self.remote_keys.clear()

    @_locked
    def reset_network(self) -> None:
        """Connexion perdue : les marionnettes sont relâchées (le serveur fera de même), le
        bâton tenu sera réannoncé à la reconnexion."""
        self.inbox.clear()
        self.out.clear()
        for e in self.entries.values():
            if e.state in PUPPET_STATES and e.grabbed:
                e.want = {"kind": "relache", "pos": self._puppet_drop_pos(e), "rot": list(e.angle)}
            elif e.state == A_CREER:
                e.state, e.want, e.owner = (LIBRE, None, None) if e.pid is not None else (ABSENT, None, None)
            elif e.state == LACHER:
                e.after = None                  # lâché quand même ; plus de marionnette à faire
        self.resend_hold = True

    # --- relevé local ----------------------------------------------------------
    @staticmethod
    def _held_by_me(s: dict, hold: dict) -> bool:
        if s.get("mode") != armes.MODE_PLAYER or not s.get("base"):
            return False
        b = s["base"]
        return (hold.get("equip") == b and hold.get("item") == armes.ITEM_BOKO) or hold.get("grab") == b

    @staticmethod
    def _rest_of(s: dict) -> tuple:
        m = s.get("mtx")
        if m:
            t = translation(m)
            return (t[0], t[1] + REST_DY, t[2])
        p = s.get("pos") or (0.0, 0.0, 0.0)
        return (float(p[0]), float(p[1]), float(p[2]))

    def _adopt_pending(self, s: dict, now: float) -> StickEntry | None:
        """Bâton inconnu apparu là où une création a échoué par délai (requête restée en attente
        dans la boîte aux lettres, exécutée plus tard) : c'est le nôtre, on ne recrée pas."""
        p = s.get("home") or s.get("pos")
        if not p:
            return None
        for e in self.entries.values():
            if e.pending is None or e.pid is not None:
                continue
            ppos, t = e.pending
            if now - t > PENDING_KEEP:
                e.pending = None
                continue
            if _dist(p, ppos) < PENDING_DIST:
                e.pending = None
                pid = s.get("pid")
                e.pid, e.base, e.mode, e.grabbed, e.free_scans = pid, None, None, False, FREE_SCANS
                self.ids.set(pid, e.key)
                e.created_t, e.tries, e.next_try = now, 0, 0.0
                e.creates += 1
                w = e.want or {}
                if w.get("kind") == "cree" and w.get("owner") is not None:
                    e.state, e.owner, e.want = A_CREER, w["owner"], {"kind": "prise", "owner": w["owner"]}
                elif w.get("kind") == "cree":
                    e.state, e.owner, e.want = LIBRE, None, None
                return e
        return None

    @_locked
    def observe(self, now: float, zone: str | None, room_t, sticks, bokos, hold: dict | None,
                auth: bool = False, me: int | None = None) -> None:
        """Relevé du thread de travail. sticks : [{pid, base, params, status, mode, flame, model,
        pos, home, room, mtx, angle}] ; bokos : [{pid, weapon, key, …}] ; hold : {equip, grab,
        item, body} (Link+0x6594, +0x65A4, +0x69B0, matrice de base du corps). None = chargement."""
        if not self.enabled:
            return
        self.auth, self.me = auth, me
        if zone is None or sticks is None:
            self.cur_stk = None
            return
        if (zone, room_t) != (self.zone, self.room_t):
            old = self.zone
            if old is not None and old.rpartition(":")[0] == zone.rpartition(":")[0]:
                # changement de salle (ou salle rechargée) dans le même stage : les marionnettes
                # peuvent rester chargées (salle voisine, faux rechargement) ; on les supprime
                # (commande 5 par id : sans effet si le jeu les a déjà détruites)
                gone = self.puppet_pids()
                self.to_delete += list(gone)
            else:
                gone = set()
            if old is not None:
                self.reset()
                for p in gone:
                    self.dead[p] = now         # ignorées au relevé tant qu'elles existent encore
            self.zone, self.room_t, self.zone_t = zone, room_t, now
            self.inbox = [(t, m) for t, m in self.inbox if m.get("zone") == zone]
        hold = hold or {}
        self.hold_ptrs = {v for v in (hold.get("equip"), hold.get("grab")) if v}
        self.ids.learn(bokos)
        owned = {b.get("weapon") for b in bokos or () if b.get("weapon") not in armes.NO_ID}
        for p in [p for p, t in self.dead.items() if now - t > CREATE_SEEN]:
            del self.dead[p]
        # seuls les bâtons partagés (0 bâton, 1 machette, 7 allumé) : pas les lances de Moblin…
        sticks = [s for s in sticks if s.get("pid") not in self.dead
                  and (s.get("params", 0) & 0xFF) in STICK_TYPES]
        present = {s.get("pid") for s in sticks}
        by_pid = {e.pid: e for e in self.entries.values() if e.pid is not None}
        seen: set[str] = set()
        for s in sticks:
            pid = s.get("pid")
            e = by_pid.get(pid)
            if e is None and pid not in self.ids.keys:
                e = self._adopt_pending(s, now)
            if e is None:
                key = self.ids.key_of(s)
                e = self.entries.get(key)
                if e is not None and e.pid is not None and e.pid != pid and e.pid in present:
                    # deux bâtons locaux sous la même clé (marionnette + bâton libéré par A2…)
                    n = 2
                    while f"{key}#{n}" in self.entries:
                        n += 1
                    key = f"{key}#{n}"
                    self.ids.set(pid, key)
                    e = None
                if e is None:
                    e = self.entries[key] = StickEntry(key, LIBRE)
                    e.doomed = key.endswith("#e")
                if e.pid != pid:
                    e.pid, e.base, e.grabbed, e.free_scans = pid, None, False, 0
                    if e.state == ABSENT:
                        e.state = LIBRE
            seen.add(e.key)
            self._observe_one(e, s, hold, now, pid in owned)
        for e in list(self.entries.values()):
            if e.key in seen or e.pid is None:
                continue
            if e.state == A_CREER and e.created_t is not None and now - e.created_t < CREATE_SEEN:
                continue                       # créé, pas encore vu (rescan)
            e.missing += 1
            if e.missing < GONE_SCANS:
                continue
            if e.state in (TENU_MOI, VOL_MOI):
                self._send("gone", e.key)
                self.last = f"bâton {e.key} disparu (magma, abîme…)"
                del self.entries[e.key]
                continue
            if e.doomed:
                self.ids.drop(e.pid)
                del self.entries[e.key]
                continue
            self.ids.drop(e.pid)
            e.pid, e.base, e.grabbed, e.missing = None, None, False, 0
            if e.state in PUPPET_STATES:
                # marionnette détruite par le jeu : on la recrée pour le même porteur
                e.state, e.want = A_CREER, {"kind": "cree", "owner": e.owner, "pos": e.pos}
            elif e.state in (LIBRE, ENNEMI, LACHER):
                e.state, e.want = ABSENT, None
        for e in self.entries.values():
            if e.state == TENU_MOI and now - e.refresh_t >= GRAB_REFRESH:
                # rappel : un receveur qui a relâché la marionnette (porteur muet) la reprend
                e.refresh_t = now
                self._send("grab", e.key, kind=e.type, flame=e.flame,
                           pos=[round(v, 2) for v in (e.rest_pos or e.pos or (0, 0, 0))], cause="rappel")
        self._update_stk(hold)
        if self.resend_hold:
            self.resend_hold = False
            for e in self.entries.values():
                if e.state in (TENU_MOI, VOL_MOI):
                    e.refresh_t = now
                    self._send("grab", e.key, kind=e.type, flame=e.flame, pos=list(e.rest_pos or e.pos or (0, 0, 0)))

    def _check_puppet(self, e: StickEntry, mode, status, mine: bool, owned: bool) -> bool:
        """Marionnette prise : toujours la nôtre ? Renvoie True si elle le reste. Sinon l'entrée
        quitte l'état marionnette (plus aucune écriture dessus) et le relevé continue."""
        if mine:
            # le Link local la tient (course au ramassage, CARRY perdu puis repris…) : conflit,
            # on arrête de la piloter ; la suite envoie `grab`, le serveur tranchera (deny -> LACHER)
            self.conflicts += 1
            self.last = f"marionnette {e.key} ramassée chez toi : conflit"
            e.state, e.grabbed, e.want, e.owner = LIBRE, False, None, None
            return False
        if owned:
            e.state, e.grabbed, e.want, e.owner = ENNEMI, False, None, None   # un Bokoblin l'a prise
            return False
        if not status & armes.ST_CARRY or mode not in (armes.MODE_PLAYER, armes.MODE_ENEMY):
            e.state, e.grabbed, e.want, e.owner = LIBRE, False, None, None    # CARRY perdu : libre
            e.last_w = None
            return False
        return True

    def _observe_one(self, e: StickEntry, s: dict, hold: dict, now: float, owned: bool) -> None:
        e.missing = 0
        e.base = s.get("base") or e.base
        e.free_scans = 0 if (e.base and e.base in self.hold_ptrs) else e.free_scans + 1
        mode, status = s.get("mode"), s.get("status", 0)
        prev_flame = e.flame
        e.mode, e.status, e.flame = mode, status, s.get("flame", 0)
        e.type = s.get("params", e.type) & 0xFF
        mtx = s.get("mtx")
        moved = e.mtx is None or mtx is None or max(abs(a - b) for a, b in zip(e.mtx, mtx)) > REST_EPS
        e.mtx = list(mtx) if mtx else None
        e.angle = tuple(s.get("angle") or (0, 0, 0))
        pos = self._rest_of(s)
        mine = self._held_by_me(s, hold)
        if e.state in PUPPET_STATES:
            if not e.grabbed or self._check_puppet(e, mode, status, mine, owned):
                return                         # nos marionnettes : rien à publier
        if e.state == LACHER:
            e.pos = pos
            return                             # conflit perdu : le lâcher est en cours
        if e.doomed and not mine:
            # doublon « #e » (le bâton est tenu par un autre joueur) : supprimé dès qu'aucun
            # Bokoblin vivant ne le tient plus (A2 ne le recrée pas : busy_pids)
            enemy = mode == armes.MODE_ENEMY and status & armes.ST_CARRY and owned
            if not enemy and not e.want:
                e.want = {"kind": "supprime", "then": None}
            e.state = ENNEMI if enemy else LIBRE
            e.pos = pos
            return
        if e.state == A_CREER and not mine:
            e.pos = pos
            return                             # créé pour un fantôme, en attente de la prise
        if mine:
            e.doomed = False
            if e.state == VOL_MOI:
                e.state = TENU_MOI             # rattrapé en vol : la clé est encore à nous
            elif e.state != TENU_MOI:
                e.state, e.want, e.owner, e.remote_rest = TENU_MOI, None, None, None
                rp = e.rest_pos or pos
                self._send("grab", e.key, kind=e.type, flame=e.flame, pos=[round(v, 2) for v in rp])
                e.refresh_t = now
                self.ref.pop(e.key, None)
                self.ref_remote.discard(e.key)
                self.grabs += 1
            e.pos = pos
            return
        if mode == armes.MODE_ENEMY and status & armes.ST_CARRY:
            if e.state in (TENU_MOI, VOL_MOI):
                # lancé puis attrapé par un ennemi : la clé n'est plus à nous (sinon VOL_MOI à vie)
                self._send("rest", e.key, kind=e.type, flame=e.flame, pos=[round(v, 2) for v in pos],
                           rot=[int(a) for a in e.angle], cause="ennemi")
                if self.auth and owned:
                    self._send("taken", e.key)
                e.state = ENNEMI
            elif e.state == LIBRE and self.auth and owned and (e.key in self.remote_keys or e.key in self.announced):
                self._send("taken", e.key)     # un Bokoblin a ramassé un bâton libre partagé
                self.announced.pop(e.key, None)
            if e.state in (LIBRE, ABSENT, ENNEMI):
                e.state = ENNEMI
            e.want = None                      # plus de déplacement en attente : il est à l'ennemi
            self.ref_remote.discard(e.key)
            e.pos = pos
            return
        if e.state == TENU_MOI:
            if mode in (armes.MODE_THROW, armes.MODE_MOVE, armes.MODE_WAIT):
                e.state, e.stable, e.thrown, e.vol_t = VOL_MOI, 0, mode == armes.MODE_THROW, now
            e.pos = pos
            return
        if e.state == VOL_MOI:
            e.thrown = e.thrown or mode == armes.MODE_THROW
            e.stable = e.stable + 1 if mode == armes.MODE_WAIT and not moved else 0
            e.pos = pos
            if e.stable >= REST_TICKS or now - e.vol_t > VOL_MAX:
                e.state, e.rest_pos, e.still_since = LIBRE, pos, now
                cause = "lance" if e.thrown else "pose"
                self._send("rest", e.key, kind=e.type, flame=e.flame, pos=[round(v, 2) for v in pos],
                           rot=[int(a) for a in e.angle], cause=cause)
                self.ref[e.key] = pos
                self.ref_remote.discard(e.key)
                self.announced[e.key] = pos
                self.rests += 1
            return
        if mode in (armes.MODE_PLAYER, armes.MODE_THROW):
            e.pos = pos                        # tenu / lancé sans être à nous : pas un bâton libre
            e.still_since = None
            return
        if e.state == ENNEMI and mode in (armes.MODE_WAIT, armes.MODE_MOVE):
            e.state = LIBRE                    # lâché par son Bokoblin, ou libéré par A2
        if e.state in (ABSENT,):
            e.state = LIBRE
        # LIBRE : immobilité (alignement), position de repos, fronts de flamme
        if mode == armes.MODE_WAIT and not moved:
            if e.still_since is None:
                e.still_since = now
            e.rest_pos = pos
        else:
            e.still_since = None
        e.pos = pos
        if e.key in self.remote_keys or e.key in self.announced:
            lit, was = e.flame > 0, prev_flame > 0
            if lit and not was:
                self._send("flame", e.key, flame=e.flame)
            elif was and not lit and prev_flame > 60:
                self._send("flame", e.key, flame=0)   # éteint dans l'eau (pas la fin naturelle)

    def _update_stk(self, hold: dict) -> None:
        """state.stk : le bâton TENU passe avant un bâton en vol (un seul champ)."""
        self.cur_stk = None
        body = hold.get("body")
        held = [e for e in self.entries.values() if e.state == TENU_MOI and e.mtx]
        if held:
            e = held[0]
            if body:
                try:
                    self.cur_stk = {"k": e.key, "h": 1, "m": encode_rel(relative(body, e.mtx)), "f": e.flame}
                except ValueError:
                    self.cur_stk = None
            return
        for e in self.entries.values():
            if e.state == VOL_MOI and e.mtx:
                self.cur_stk = {"k": e.key, "h": 0, "w": world_list(e.mtx), "f": e.flame}
                return

    # --- messages reçus -------------------------------------------------------
    @_locked
    def on_message(self, msg: dict, now: float, me: int | None = None) -> None:
        """Message `stick` du serveur (relais, deny, snap…) : mis en file, traité par step()."""
        if not self.enabled or not isinstance(msg, dict) or not isinstance(msg.get("zone"), str):
            return
        if me is not None and msg.get("from") == me:
            return
        self.inbox.append((now, msg))
        if len(self.inbox) > 256:
            del self.inbox[:-256]

    @_locked
    def player_left(self, pid: int, now: float) -> None:
        for e in list(self.entries.values()):
            if e.owner == pid and e.state in PUPPET_STATES and e.grabbed:
                e.want = {"kind": "relache", "pos": self._puppet_drop_pos(e), "rot": list(e.angle)}
            elif e.owner == pid and e.state == A_CREER:
                e.owner = None
                if e.pid is None:
                    e.state, e.want = ABSENT, None
                elif e.grabbed:
                    e.want = {"kind": "relache", "pos": self._puppet_drop_pos(e), "rot": list(e.angle)}
                else:
                    e.state, e.want = LIBRE, None
            elif e.owner == pid and e.state == LACHER:
                e.after, e.owner = None, None

    def _puppet_drop_pos(self, e: StickEntry) -> tuple:
        if e.last_w:
            t = translation(e.last_w)
            return (t[0], t[1] + REST_DY, t[2])
        return e.pos or (0.0, 0.0, 0.0)

    def _match(self, key: str, pos, typ) -> StickEntry | None:
        """Rapprochement spatial d'une clé inconnue : bâton libre, au repos, de même type, sans
        clé connue des autres, le plus proche à moins de MATCH_DIST. La clé locale est renommée."""
        if pos is None:
            return None
        best, bd = None, MATCH_DIST
        for e in self.entries.values():
            if e.state != LIBRE or e.pid is None or e.key in self.remote_keys or e.want or e.doomed:
                continue
            if typ is not None and not same_type(e.type, typ):
                continue
            p = e.rest_pos or e.pos
            if p is None:
                continue
            d = _dist(p, pos)
            if d < bd:
                best, bd = e, d
        if best is None:
            return None
        old = best.key
        del self.entries[old]
        best.key = key
        self.entries[key] = best
        self.ids.rename(old, key)
        self.ref.pop(old, None)
        self.ref_remote.discard(old)
        self.announced.pop(old, None)
        return best

    def _lookup(self, key: str, pos=None, typ=None) -> StickEntry:
        e = self.entries.get(key)
        if e is None:
            e = self._match(key, pos, typ) or self._entry(key)
        self.remote_keys.add(key)
        return e

    def _ghost_create_pos(self, owner, others, fallback):
        o = (others or {}).get(owner)
        p = o.get("pos") if o else None
        if isinstance(p, (list, tuple)) and len(p) == 3 and all(_num(v) for v in p):
            return (p[0], p[1] + CREATE_Y, p[2])
        if fallback is not None:
            return (fallback[0], fallback[1] + DROP_UP, fallback[2])
        return None

    def _detach(self, e: StickEntry, suffix: str) -> None:
        """Le bâton local de cette clé n'est plus pour le partage (tenu par notre ennemi « #e »,
        ou en mode 3 / 6 sans être reconnu comme le nôtre « #l ») : il garde une clé à part."""
        if e.pid is not None:
            self.ids.set(e.pid, e.key + suffix)
        e.pid, e.base, e.grabbed, e.mode, e.free_scans = None, None, False, None, 0

    def _remote_grab(self, owner, key, pos, typ, flame, others, cause=None) -> None:
        e = self._lookup(key, pos, typ)
        self.ref.pop(key, None)
        self.ref_remote.discard(key)
        if typ is not None and e.pid is None:
            e.type = typ
        if e.state in (TENU_MOI, VOL_MOI):
            e.owner, e.remote_rest = owner, None   # conflit : le serveur tranchera (deny)
            return
        if e.state == LACHER:
            e.owner, e.after = owner, {"kind": "prise", "owner": owner}
            return
        if cause == "rappel":
            if e.state in PUPPET_STATES and e.owner == owner:
                if e.want and e.want.get("kind") == "relache":
                    e.want = None              # le porteur le tient toujours : on garde la marionnette
                return
            if e.state == A_CREER and e.owner == owner and e.want:
                return                         # prise ou création déjà en cours
            if e.gave_up:
                return                         # abandonné : pas de relance tous les 3 s
        e.gave_up = False
        e.owner = owner
        e.tries, e.next_try, e.wait_since, e.creates = 0, 0.0, None, 0
        if e.state in PUPPET_STATES:
            e.state, e.want = TENU_AUTRE, None
            return
        if e.pid is not None and e.state in (LIBRE, A_CREER) and not e.doomed:
            e.state, e.want = A_CREER, {"kind": "prise", "owner": owner}
            return
        if e.pid is not None:
            # tenu par un de nos ennemis : il reste à lui sous la clé « #e » (doublon, supprimé
            # dès qu'il est libre), on crée une marionnette pour le porteur
            self._detach(e, "#e")
        e.state = A_CREER
        e.want = {"kind": "cree", "owner": owner, "pos": self._ghost_create_pos(owner, others, pos),
                  "flame": flame}

    def _remote_rest(self, key, pos, rot, typ, flame, cause) -> None:
        e = self._lookup(key, pos, typ)
        self.ref[key] = tuple(pos)
        self.ref_remote.add(key)
        self.announced[key] = tuple(pos)       # position convenue : l'autorité ne la réannonce pas
        e.tries, e.next_try, e.wait_since, e.creates, e.aligns = 0, 0.0, None, 0, 0
        e.gave_up = False
        if typ is not None and e.pid is None:
            e.type = typ
        drop = (pos[0], pos[1] + DROP_UP, pos[2])
        rot = list(rot or e.angle)
        if e.state in (TENU_MOI, VOL_MOI):
            e.remote_rest = (drop, rot)        # utilisé si un deny suit (conflit perdu)
            return
        if e.state == LACHER:
            e.owner, e.after = None, {"kind": "deplace", "pos": drop, "rot": rot}
            return
        if e.state in PUPPET_STATES:
            e.owner = None
            e.want = {"kind": "relache", "pos": drop, "rot": rot}
            return
        if e.state == A_CREER:
            e.owner = None
            if e.pid is None:
                e.want = {"kind": "cree", "owner": None, "pos": drop, "flame": flame}
            else:
                # créé ou attendu pour une prise, pas encore pris : déplacé là où l'autre l'a posé
                e.state, e.want = LIBRE, {"kind": "deplace", "pos": drop, "rot": rot}
            return
        if e.state == ENNEMI:
            return                             # tenu par notre ennemi : A2 le libérera, puis alignement
        if e.state == LIBRE and e.pid is not None:
            p = e.rest_pos or e.pos
            if p is None or _dist(p, pos) > ALIGN_DIST or e.mode != armes.MODE_WAIT:
                e.want = {"kind": "deplace", "pos": drop, "rot": rot}   # attend qu'il soit posé
            if flame is not None:
                e.flame_target = flame
            return
        e.state = ABSENT
        e.want = {"kind": "cree", "owner": None, "pos": drop, "flame": flame}

    def _remote_gone(self, key) -> None:
        self.ref.pop(key, None)
        self.ref_remote.discard(key)
        self.remote_keys.add(key)
        e = self.entries.get(key)
        if e is None or e.state in (TENU_MOI, VOL_MOI):
            return
        e.owner = None
        if e.state == LACHER:
            e.after = None
            return
        if e.pid is not None and e.state != ENNEMI:
            e.want = {"kind": "supprime", "then": None}
        else:
            e.state, e.want = ABSENT, None

    def _deny(self, msg: dict) -> None:
        key = msg.get("key")
        e = self.entries.get(key) if isinstance(key, str) else None
        if e is None:
            return
        self.conflicts += 1
        q = msg.get("holder")
        self.last = f"conflit sur {key} : gardé par un autre joueur"
        if e.pid is None or e.state not in (TENU_MOI, VOL_MOI):
            return
        if e.remote_rest is not None:
            # le gagnant l'a déjà reposé : notre bâton ira là où il l'a posé
            drop, rot = e.remote_rest
            after = {"kind": "deplace", "pos": drop, "rot": rot}
            e.owner = None
        else:
            after = {"kind": "prise", "owner": q} if q is not None else None
            e.owner = q
        e.remote_rest = None
        e.tries, e.next_try, e.wait_since = 0, 0.0, None
        if e.state == TENU_MOI:
            # jamais de commande 5 sur un bâton dans la main de Link (daPy_lk_c::execute utilise
            # le pointeur de la frame précédente) : on le LÂCHE par écritures d'abord
            e.state, e.want, e.after = LACHER, {"kind": "lacher"}, after
        else:
            # lancé (pas dans la main) : la prise attend qu'il se pose
            e.state, e.after = (A_CREER, None) if after and after["kind"] == "prise" else (LIBRE, None)
            e.want = after

    def _process(self, msg: dict, others: dict) -> None:
        ev = msg.get("ev")
        src = msg.get("from")
        if ev == "snap":
            for it in msg.get("list") or []:
                if not isinstance(it, dict):
                    continue
                v = valid_stick(dict(it, ev="rest"))
                if v is None:
                    continue
                h = it.get("holder")
                if h is not None and h != self.me:
                    self._remote_grab(h, v["key"], v["pos"], v.get("kind"), v.get("flame", 0), others)
                elif h is None:
                    self._remote_rest(v["key"], v["pos"], v.get("rot"), v.get("kind"), v.get("flame"), "snap")
            return
        if ev == "deny":
            self._deny(msg)
            return
        v = valid_stick(msg)
        if v is None:
            return
        key = v["key"]
        if ev == "grab":
            self._remote_grab(src, key, v["pos"], v.get("kind"), v.get("flame", 0), others, v.get("cause"))
        elif ev == "rest":
            self._remote_rest(key, v["pos"], v.get("rot"), v.get("kind"), v.get("flame"), v.get("cause"))
        elif ev in ("gone", "taken"):
            self._remote_gone(key)
        elif ev == "flame":
            e = self.entries.get(key)
            if e is not None and e.state == LIBRE:
                e.flame_target = v.get("flame", 0)

    # --- décisions -----------------------------------------------------------
    def _mb_cost(self, job: dict) -> int:
        """Appels de boîte aux lettres d'un travail (création, suppression ; pire cas)."""
        k = job["kind"]
        if k in ("cree", "supprime", "supprime_ancienne"):
            return 1
        if k == "deplace":
            return 2
        if k == "relache":
            return 2                           # mode ecrire : 0, mais repli par recréation possible
        return 0

    @_locked
    def step(self, now: float, ok: bool, can_write: bool, drive_ok: bool, room: int,
             others: dict | None = None, g_of=None, mb_ok: bool = True) -> list[dict]:
        """Travaux du tick. ok : pas d'événement, pas de changement de stage (les transitions
        attendent en plus ROOM_SETTLE s dans la zone) ; can_write : boîte aux lettres présente ;
        drive_ok : pilotage permis (jeu lisible, pas de changement de stage) ; others : états des
        joueurs distants (stk, pos, rotY, _t) ; g_of(pid) -> matrice G du fantôme ou None ;
        mb_ok : boîte aux lettres libre (pas de requête restée en attente)."""
        if not self.enabled or self.zone is None:
            return []
        others = others or {}
        jobs: list[dict] = []
        if not can_write:
            self.inbox.clear()                  # lecture seule : on publie, on n'écrit rien
            return []
        calls = 0
        if self.to_delete and drive_ok and mb_ok:
            for pid in self.to_delete[:MAX_MB_CALLS]:
                jobs.append({"kind": "supprime_ancienne", "pid": pid, "key": None})
                calls += 1
            del self.to_delete[:MAX_MB_CALLS]
        eligible = ok and now - self.zone_t >= self.ROOM_SETTLE
        if eligible:
            inbox, self.inbox = self.inbox, []
            for _t, msg in inbox:
                if msg.get("zone") == self.zone:
                    self._process(msg, others)
            self._timers(now, others)
            n = 0
            for e in sorted(self.entries.values(), key=lambda x: x.key):
                if n >= MAX_TRANSITIONS_PER_TICK:
                    break
                job = self._transition(e, now, others, room)
                if job is None:
                    continue
                cost = self._mb_cost(job)
                if cost and (not mb_ok or calls + cost > MAX_MB_CALLS):
                    continue                    # au tick suivant (la transition reste voulue)
                calls += cost
                jobs.append(job)
                n += 1
        if drive_ok:
            jobs += self._drive(now, others, g_of)
        return jobs

    def _timers(self, now: float, others: dict) -> None:
        for e in self.entries.values():
            if e.state in PUPPET_STATES and e.grabbed and not e.want:
                o = others.get(e.owner)
                if o is None or now - o.get("_t", 0) > HOLD_STALE:
                    e.want = {"kind": "relache", "pos": self._puppet_drop_pos(e), "rot": list(e.angle)}
                    e.owner = None
                elif now - e.stk_seen > STK_LOST:
                    # le porteur joue mais ne publie plus ce bâton : il ne le tient plus (rest perdu,
                    # ordre des messages) ; relâché là où il est convenu, sinon là où il est
                    r = self.ref.get(e.key)
                    pos = (r[0], r[1] + DROP_UP, r[2]) if r is not None else self._puppet_drop_pos(e)
                    e.want = {"kind": "relache", "pos": pos, "rot": list(e.angle)}
                    e.owner = None
                    self.last = f"bâton {e.key} : plus tenu par son porteur, relâché"
            if e.state == LIBRE and e.pid is not None and not e.want and e.mode == armes.MODE_WAIT \
                    and e.still_since is not None and now - e.still_since >= ALIGN_STABLE and not e.doomed:
                p = e.rest_pos or e.pos
                if p is None:
                    continue
                if self.auth and e.key not in self.ref_remote:
                    a = self.announced.get(e.key)
                    if a is None or _dist(a, p) > ALIGN_DIST:
                        self._send("rest", e.key, kind=e.type, flame=e.flame, pos=[round(v, 2) for v in p],
                                   rot=[int(x) for x in e.angle], cause="aligne")
                        self.announced[e.key] = p
                        self.ref[e.key] = p
                else:
                    # position reçue d'un autre joueur (même pour l'autorité) : on s'y aligne
                    r = self.ref.get(e.key)
                    if r is not None and _dist(r, p) > ALIGN_DIST and e.aligns < MAX_TRIES:
                        e.aligns += 1
                        e.want = {"kind": "deplace", "pos": (r[0], r[1] + DROP_UP, r[2]), "rot": list(e.angle)}

    def _wait(self, e: StickEntry, now: float, limit: float) -> bool:
        """Attente bornée : True quand `limit` s sont écoulées."""
        if e.wait_since is None:
            e.wait_since = now
        if now - e.wait_since > limit:
            e.wait_since = None
            return True
        return False

    def _transition(self, e: StickEntry, now: float, others: dict, room: int) -> dict | None:
        w = e.want
        if w is None or now < e.next_try:
            return None
        kind = w["kind"]
        base = {"key": e.key, "kind": kind}
        if kind == "lacher":
            if e.pid is None or not e.base:
                e.state, e.want = ABSENT, None
                return None
            return dict(base, pid=e.pid, b=e.base)
        if kind == "prise":
            if e.pid is None:
                e.want = {"kind": "cree", "owner": w.get("owner"), "pos": self._ghost_create_pos(w.get("owner"), others, e.pos)}
                return None
            if not e.base or e.mode is None:
                if self._wait(e, now, CREATE_SEEN):
                    self._give_up(e, "bâton créé introuvable")
                return None
            if e.mode != armes.MODE_WAIT or e.status & armes.ST_CARRY or e.free_scans < FREE_SCANS:
                if self._wait(e, now, FALL_WAIT):
                    # il ne se pose pas (ou un ennemi l'a pris) : on en crée un autre pour le fantôme
                    if e.mode == armes.MODE_ENEMY and e.status & armes.ST_CARRY:
                        self._detach(e, "#e")              # à notre ennemi : doublon supprimé plus tard
                    elif e.mode in (armes.MODE_PLAYER, armes.MODE_THROW) or e.free_scans < FREE_SCANS:
                        self._detach(e, "#l")              # peut-être dans la main de Link : jamais supprimé
                    else:
                        e.want = {"kind": "supprime", "then": w.get("owner")}
                        return None
                    e.want = {"kind": "cree", "owner": w.get("owner"),
                              "pos": self._ghost_create_pos(w.get("owner"), others, e.pos)}
                return None
            return dict(base, pid=e.pid, b=e.base, owner=w.get("owner"), mode=self.puppet_mode)
        if kind == "cree":
            pos = w.get("pos") or self._ghost_create_pos(w.get("owner"), others, e.pos)
            if pos is None:
                self._give_up(e, "position inconnue")
                return None
            if e.creates >= MAX_TRIES:
                self._give_up(e, f"{MAX_TRIES} créations sans résultat")
                return None
            flame = w.get("flame")
            flame = e.flame if flame is None else flame
            return dict(base, pos=tuple(pos), params=armes.stick_params(e.type, flame or 0), room=room,
                        owner=w.get("owner"))
        if kind in ("relache", "deplace"):
            if e.pid is None:
                e.want = {"kind": "cree", "owner": None, "pos": w["pos"]}
                return None
            if kind == "deplace" and e.state in PUPPET_STATES:
                kind = "relache"
            if kind == "relache" and (not e.grabbed or not e.base):
                kind = "deplace"                # pas (encore) de marionnette prise : on recrée
            if kind == "deplace":
                if e.state not in (LIBRE, A_CREER, ABSENT) or e.doomed:
                    e.want = None               # pris entre-temps (ennemi, Link) : plus rien à déplacer
                    return None
                if e.mode != armes.MODE_WAIT or e.status & armes.ST_CARRY or e.free_scans < FREE_SCANS:
                    return None                 # attend qu'il soit posé (et plus pointé par Link)
                p = e.rest_pos or e.pos
                tgt = w["pos"]
                if p is not None and _dist(p, (tgt[0], tgt[1] - DROP_UP, tgt[2])) <= ALIGN_DIST:
                    e.want = None               # déjà au bon endroit
                    if e.state == A_CREER:
                        e.state = LIBRE
                    return None
            elif e.free_scans < FREE_SCANS:
                return None
            return dict(base, kind=kind, pid=e.pid, b=e.base, pos=tuple(w["pos"]), rot=list(w.get("rot") or (0, 0, 0)),
                        params=armes.stick_params(e.type, e.flame), room=room, mode=self.drop_mode)
        if kind == "supprime":
            if e.pid is None:
                self._after_delete(e, w.get("then"), now, others)
                return None
            if e.mode == armes.MODE_PLAYER and e.state not in PUPPET_STATES:
                # mode 3 sans être notre marionnette : peut-être dans la main de Link, jamais supprimé
                self._detach(e, "#l")
                self._after_delete(e, w.get("then"), now, others)
                return None
            if e.free_scans < FREE_SCANS:
                return None                     # Link le pointe encore : on attend
            return dict(base, pid=e.pid, b=e.base, then=w.get("then"))
        return None

    def _give_up(self, e: StickEntry, why: str) -> None:
        self.failures += 1
        self.last = f"bâton {e.key} : {why}"
        e.want, e.after, e.gave_up = None, None, True
        if e.pid is None:
            e.state = ABSENT
        elif (e.state == A_CREER and not e.grabbed) or e.state == LACHER:
            e.state = LIBRE

    def _after_delete(self, e: StickEntry, then, now, others) -> None:
        if e.pid is not None:
            self.ids.drop(e.pid)
            self.dead[e.pid] = now
        e.pid, e.base, e.grabbed, e.mode = None, None, False, None
        if then is not None:
            e.state, e.owner = A_CREER, then
            e.want = {"kind": "cree", "owner": then, "pos": self._ghost_create_pos(then, others, e.pos)}
        else:
            e.state, e.want, e.owner = ABSENT, None, None

    def _drive(self, now: float, others: dict, g_of) -> list[dict]:
        """Pilotage des marionnettes prises : matrice (main du fantôme ou vol) et flamme."""
        jobs = []
        for e in self.entries.values():
            if e.pid is None or not e.base:
                continue
            if e.state == LIBRE and e.flame_target is not None and not e.want:
                v = self._flame_write(e.flame, e.flame_target)
                e.flame_target = None
                if v is not None:
                    jobs.append({"kind": "flamme", "key": e.key, "pid": e.pid, "b": e.base, "value": v})
                continue
            if not (e.state in PUPPET_STATES and e.grabbed) or e.want:
                continue
            o = others.get(e.owner) or {}
            stk = o.get("stk")
            if not isinstance(stk, dict) or stk.get("k") != e.key or now - o.get("_t", 0) > STK_FRESH:
                continue                       # pas de stk frais : la marionnette reste où elle est
            e.stk_seen = now
            w = None
            if stk.get("h") == 1:
                r = decode_rel(stk.get("m"))
                g = g_of(e.owner) if g_of else None
                if r is not None and g is not None:
                    w = compose(g, r)
                    e.state = TENU_AUTRE
            elif stk.get("h") == 0:
                ww = stk.get("w")
                if isinstance(ww, (list, tuple)) and len(ww) == 12 and all(_num(v) and abs(v) < 1e6 for v in ww):
                    w = [float(v) for v in ww]
                    e.state = VOL_AUTRE
            if w is not None:
                e.last_w = w
                jobs.append({"kind": "pilote", "key": e.key, "pid": e.pid, "b": e.base, "w": w})
            f = stk.get("f")
            if _int(f):
                v = self._flame_write(e.flame, f)
                if v is not None:
                    jobs.append({"kind": "flamme", "key": e.key, "pid": e.pid, "b": e.base, "value": v})
                    e.flame = v                # pas de seconde écriture avant le prochain relevé
        return jobs

    @staticmethod
    def _flame_write(local: int, target: int) -> int | None:
        """Valeur à écrire pour suivre la flamme reçue, ou None. Éteindre = 1 (jamais 0)."""
        if target <= 0:
            return 1 if local > 1 else None
        if local <= 0 or abs(local - target) > FLAME_TOL:
            return int(min(FLAME_ON, target))
        return None

    # --- issue des travaux -----------------------------------------------------
    @_locked
    def result(self, job: dict, ok: bool, now: float, new_pid: int | None = None, others: dict | None = None) -> None:
        kind = job["kind"]
        if kind in ("pilote", "flamme", "supprime_ancienne"):
            return
        e = self.entries.get(job.get("key"))
        if e is None:
            return
        if not ok:
            if kind == "cree" and job.get("pending"):
                # délai dépassé (menu, chargement) : la requête reste en attente et peut s'exécuter
                # plus tard ; le bâton qui apparaîtra là sera adopté au lieu d'être recréé
                e.pending = (tuple(job["pos"]), now)
            e.tries += 1
            e.next_try = now + RETRY
            if e.tries >= MAX_TRIES:
                self._give_up(e, f"{kind} refusé {MAX_TRIES} fois")
            return
        e.tries, e.next_try, e.wait_since = 0, 0.0, None
        if kind == "lacher":
            after, e.after = e.after, None
            e.grabbed, e.free_scans = False, 0
            if after and after.get("kind") == "prise":
                e.state, e.want = A_CREER, after
            else:
                e.state, e.want, e.owner = LIBRE, after, None
            e.still_since = None
        elif kind == "prise":
            e.state, e.grabbed, e.want = TENU_AUTRE, True, None
            e.owner = job.get("owner", e.owner)
            self.puppets_made += 1
            e.last_w = None
            e.stk_seen = now
        elif kind == "cree":
            e.pending = None
            if new_pid is not None:
                # créé par nous : jamais dans la main de Link
                e.pid, e.base, e.mode, e.grabbed, e.free_scans = new_pid, None, None, False, FREE_SCANS
                self.ids.set(new_pid, e.key)
            e.created_t = now
            e.creates += 1
            owner = job.get("owner")
            if owner is not None:
                e.state, e.owner, e.want = A_CREER, owner, {"kind": "prise", "owner": owner}
            else:
                e.state, e.owner, e.want = LIBRE, None, None
                e.still_since = None
        elif kind in ("relache", "deplace"):
            if new_pid is not None:            # recréé (deplace, ou relâchement de repli)
                self.ids.drop(e.pid)
                if e.pid is not None:
                    self.dead[e.pid] = now
                e.pid, e.base, e.mode, e.free_scans = new_pid, None, None, FREE_SCANS
                self.ids.set(new_pid, e.key)
                e.created_t = now
            e.state, e.grabbed, e.want, e.owner = LIBRE, False, None, None
            e.still_since, e.last_w = None, None
            p = job["pos"]
            e.rest_pos = (p[0], p[1] - DROP_UP, p[2])
        elif kind == "supprime":
            self._after_delete(e, job.get("then"), now, others or {})

    @_locked
    def status(self, names: dict | None = None) -> str:
        if not self.enabled:
            return "désactivé"
        names = names or {}
        held = [e for e in self.entries.values() if e.state in PUPPET_STATES]
        free = sum(1 for e in self.entries.values() if e.state == LIBRE)
        mine = any(e.state in (TENU_MOI, VOL_MOI) for e in self.entries.values())
        parts = []
        if mine:
            parts.append("1 tenu par toi")
        for e in held:
            parts.append(f"1 tenu par {names.get(e.owner, '?')}")
        parts.append(f"{free} au repos")
        s = ", ".join(parts) + f", {self.conflicts} conflit(s)"
        if self.failures:
            s += f", {self.failures} échec(s)"
        return s + (f" ; dernier : {self.last}" if self.last else "")


# ---------------------------------------------------------------------------
def execute_stick_job(job: dict, ex) -> tuple[bool, int | None, str]:
    """Exécute un travail de StickSync.step avec `ex` (grab_puppet, set_stick_matrix,
    drop_puppet, set_flame, release_held, create_actor, delete_actor ; facultatifs :
    link_holds(base), stick_state(pid, base), delete_stick(pid, base)). Renvoie (ok, id créé,
    message). Les gardes facultatives relisent le jeu juste avant d'écrire."""
    kind = job["kind"]
    if kind == "pilote":
        return bool(ex.set_stick_matrix(job["pid"], job["b"], job["w"])), None, ""
    if kind == "flamme":
        return bool(ex.set_flame(job["pid"], job["b"], job["value"])), None, ""
    if kind == "supprime_ancienne":
        return bool(ex.delete_actor(job["pid"])), None, ""
    if kind == "lacher":
        ok = bool(ex.release_held(job["pid"], job["b"]))
        return ok, None, (f"conflit : bâton {job['key']} lâché (gardé par l'autre joueur)" if ok
                          else f"bâton {job['key']} : lâcher refusé")
    if kind == "prise":
        mode = job.get("mode", armes.MODE_PLAYER)
        ok = bool(ex.grab_puppet(job["pid"], job["b"]) if mode == armes.MODE_PLAYER
                  else ex.grab_puppet(job["pid"], job["b"], mode=mode))
        return ok, None, (f"bâton {job['key']} dans la main du fantôme" if ok
                          else f"bâton {job['key']} : prise refusée")
    holds = getattr(ex, "link_holds", None)
    if kind == "supprime":
        if holds is not None and holds(job.get("b")):
            return False, None, f"bâton {job['key']} : dans la main de Link, pas supprimé"
        dele = getattr(ex, "delete_stick", None)
        ok = bool(dele(job["pid"], job.get("b")) if dele else ex.delete_actor(job["pid"]))
        return ok, None, "" if ok else f"bâton {job['key']} : suppression refusée"
    pos = job["pos"]
    where = f"({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f})"
    if kind == "cree":
        new = ex.create_actor(armes.PROC_BOKO, job["params"], pos, job["room"])
        return new is not None, new, (f"bâton {job['key']} créé en {where}" if new is not None
                                      else f"bâton {job['key']} : création refusée")
    if kind == "relache" and job.get("mode", "ecrire") == "ecrire":
        if ex.drop_puppet(job["pid"], job["b"], pos, job.get("rot") or (0, 0, 0)):
            return True, None, f"bâton {job['key']} lâché en {where}"
        # garde refusée (acteur changé, mode inattendu) : repli, on recrée
    # deplace, ou relâchement par recréation : créer d'abord (l'archive Boko reste chargée), puis
    # supprimer ; on revérifie avant que l'ancien n'est ni dans la main de Link ni à un ennemi
    if holds is not None and holds(job.get("b")):
        return False, None, f"bâton {job['key']} : dans la main de Link, pas déplacé"
    state = getattr(ex, "stick_state", None)
    if state is not None and kind == "deplace":
        s = state(job["pid"], job.get("b"))
        if s is None or s["status"] & armes.ST_CARRY or s["mode"] not in (armes.MODE_WAIT, armes.MODE_MOVE):
            return False, None, f"bâton {job['key']} : plus libre, pas déplacé"
    new = ex.create_actor(armes.PROC_BOKO, job["params"], pos, job["room"])
    if new is None:
        return False, None, f"bâton {job['key']} : création refusée"
    ex.delete_actor(job["pid"])
    return True, new, f"bâton {job['key']} recréé en {where}"
