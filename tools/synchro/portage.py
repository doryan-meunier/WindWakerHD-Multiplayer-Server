"""Objets portés par le fantôme (synchro v2, lot 3, suite ; docs/etudes/sync2_mobiles.md §2.3-2.5 et §3.4,
la section « Vérification » de l'étude prime ; modèle : les bâtons A3, docs/etudes/batons_partages.md).
Logique testable sans Cemu. Option --share-carry (activée par --coop-donjon), --no-carry pour couper.

Ce que voient les joueurs : quand A soulève une jarre, une cruche, un tonneau, une caisse Hbox2S
(daTsubo, proc 453), une petite pierre (daStone, proc 454) ou une bombe (bombe du sac 294, Bomb2 d'une
bombe-fleur 295), chez les autres le même objet quitte le sol (la bombe apparaît) et se tient au-dessus
de la tête du fantôme de A, qu'il suit. Quand A le lance, l'objet suit la trajectoire de A (positions
reçues), puis il est posé là où A l'a posé, ou cassé là où il a cassé chez A (vraie casse du jeu pour
une jarre), ou retiré (pierre cassée, bombe qui explose : l'explosion vient de --share-bombs).

Chez A (le porteur) : l'objet tenu est celui que pointe Link+0x65A4 (mActorKeepGrab) et qui est en mode
« porté » (453 : m678 +0x794 == 4 ; 454 : m674 +0x790 == 1 ; 295 : mState +0x88C == 1 ; 294 : bit CARRY
0x2000 de +0x2E0). Message « carry grab » (renvoyé toutes les GRAB_REFRESH s, et au changement de salle),
puis champ optionnel state.car : {k, h: 1, o: position relative au repère de Link, r: angles} tant qu'il
est tenu, {k, h: 0, w: position monde, r} pendant le vol. Fin du vol : « rest » {pos, rot} (posé, au repos
depuis REST_SCANS relevés, ou au bout de VOL_MAX s), « gone » {pos, cause} (disparu : casse, eau,
explosion).

Chez B (marionnette, comme le bâton A3) :
  - jarre / pierre de même clé (proc, paramètres sans itemNo ni bit 31, home ; identique sur tous les PC
    pour un objet de stage) posée (mode 2 / 0), pas dans les mains du Link local, contenu « relayable »
    (même règle qu'A6 : pas de clé, de quart de cœur… ; cruche seulement avec --share-jugs), spec 0x3F
    (pas de bâton caché dedans), pas sur un fond mobile : itemNo = 0x3F (le contenu de A arrive par A4,
    jamais en double), puis bit CARRY, puis mode 3 (453) / 2 (454), qui force l'exécution d'un objet
    au repos « endormi » par son test de visibilité : à l'image suivante le jeu passe en mode_carry_init
    (collisions coupées, plus ramassable) [DECOMP d_a_tsubo.cpp / d_a_stone.cpp mode_proc_call ; HD-ASM
    0x024CD3CC-0x024CD5C0, 0x0249CB88]. En mode porté, l'objet ne calcule plus sa position : on écrit
    current.pos (+0x314) et shape_angle (+0x328) à chaque relevé, dans la même passe que la pose du
    fantôme (crr_pos garde current.pos en mode porté).
  - bombe : commande 4 create(295, params 2 = Start_CARRY, pos) ; dès qu'elle est vue au relevé :
    mBombTimer (+0x934) = 0 D'ABORD (bombe inerte : chk_exp_timer et chk_exp_cc ne font rien si le
    minuteur est <= 0 [HD-ASM 0x020CAC8C, 0x020CAB6C] ; ni épée, ni explosion voisine ne la font sauter),
    puis CARRY. Pas vue dans les BOMB_SEEN s : supprimée (commande 5 par id), et supprimée aussi si elle
    apparaît plus tard.
    Une bombe inerte ne finit JAMAIS d'elle-même (minuteur 0 : chk_exp_timer ne la fait plus exploser, et son
    animation de gonflement s'arrête à la dernière image : « figée juste avant d'exploser »). Toute fin d'une
    bombe du fantôme passe donc par l'« enterrement » (_bury) : la bombe reste suivie (zombies, adresse
    connue) jusqu'à sa disparition constatée ; elle est retirée par le JEU (u8 +0x93C = 1, le drapeau que
    mode_explode / mode_sink posent : le jeu la supprime à la fin de son execute, sans boîte aux lettres),
    commande 5 en repli. Jamais pendant que le Link local la tient. Garde-fou de durée : BOMB_LIFE s après
    sa création, elle est retirée quoi qu'il arrive ; à l'arrêt du client, toutes sont retirées.
  - posée (« rest ») : position (+10 u, elle retombe), angle, speedF = 0, itemNo d'origine, CARRY effacé
    en dernier : le jeu la repose (spec_mode_put_init + mode_wait). La jarre redevient une jarre normale.
  - « gone » casse : jarre = position de la casse puis minuteur de casse m800 (+0x91C) = 1 : le jeu la
    casse (éclats, son ; rien ne tombe, itemNo 0x3F ; une cruche d'eau fait son eau) ; pierre : commande 5
    (comme A6) ; « gone » eau : posée au point d'entrée dans l'eau (le jeu la fait couler ou flotter) ;
    bombe : « explose » et --share-bombs chez nous : la copie explose ELLE-MÊME à la position reçue (minuteur
    à 1 : chk_exp_timer, le jeu la supprime après l'explosion) ; objets mobiles ne rejoue alors pas de second
    Bomb2 (claim_boom, « boom » avec l'id de la bombe) ; sinon retirée (drapeau de fin). Chez le porteur, une
    bombe disparue entre deux relevés est jugée sur sa mémoire libérée (bomb_fate), et les bombes en vol restent
    suivies malgré un relevé raté (même zone). Bug vu en jeu le 2026-11-13.
  - porteur parti dans une autre salle : objet retiré (commande 5) ; porteur muet HOLD_STALE s : posé là
    où il est (bombe : retirée).
  Sans doublon avec A6 / A4 : une marionnette n'est jamais relayée comme « cassée » par notre PotWatch
  (rappel ignore), et la casse annoncée par A6 pour sa clé est marquée traitée (rappel handled).

Garde commune avant toute transition (prise, pose, casse, création, suppression) : jeu libre (ni
événement, ni changement de stage), salle stable depuis ROOM_SETTLE s, écriture permise (boîte aux
lettres présente, sans requête en attente), acteur relu juste avant (en-tête, id de process, proc, mode,
statut), objet pas pointé par le Link local (+0x65A4 / +0x6594). Le pilotage (positions) continue pendant
un événement, jamais pendant un changement de stage.

Compatible pack v17 : écritures Python et commandes 4 / 5 seulement.
"""
from __future__ import annotations

import math
import struct
import threading
import time

from commun.protocole import zone_of

# --- procs et tailles d'instance HD [HD-ASM table des profils 0x101D2B1C, 0x101D10E4, 0x101922F4, 0x10192348] ---
PROC_TSUBO, PROC_STONE, PROC_BOMB, PROC_BOMB2 = 453, 454, 294, 295
PROCS = (PROC_TSUBO, PROC_STONE, PROC_BOMB, PROC_BOMB2)
SIZES = {PROC_TSUBO: 0x93C, PROC_STONE: 0x7AC, PROC_BOMB: 0xB50, PROC_BOMB2: 0x9A8}
KIND_POT, KIND_STONE, KIND_BOMB = "jarre", "pierre", "bombe"
KINDS = {PROC_TSUBO: KIND_POT, PROC_STONE: KIND_STONE, PROC_BOMB: KIND_BOMB, PROC_BOMB2: KIND_BOMB}

# --- fopAc_ac_c (HD) ---------------------------------------------------------------------------------------
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_STATUS = 0x2E0          # u32 ; fopAcStts_CARRY_e 0x2000, HOOK_CARRY 0x100000
ST_CARRY, ST_HOOK = 0x2000, 0x100000
OFF_OLD = 0x300             # old.pos
OFF_POS = 0x314             # current.pos
OFF_SHAPE = 0x328           # shape_angle s16[3]
OFF_SPEED = 0x33C           # speed f32[3]
OFF_SPEEDF = 0x370
LINK_EQUIP_PTR = 0x6594     # mActorKeepEquip (bâton) [HD-ASM, batons_partages.md §1.2]
LINK_GRAB_PTR = 0x65A4      # mActorKeepGrab : jarre, pierre, bombe portée [HD-ASM]

# --- daTsubo::Act_c (453) [HD-ASM, jarres_casse.md §2 ; mode_carry_init 0x024CD4xx-0x024CD5C0] ------------
TS_TYPE = 0x790             # s32 mType (2 = cruche d'eau)
TS_MODE = 0x794             # s32 m678 : 0 cachée, 1 apparition, 2 posée, 3 roule, 4 portée, 5 lancée, 6 coule, 7 flotte
TS_KILL = 0x91C             # s16 m800 : minuteur de casse (damage_kill_proc 0x024CD3CC : 1 -> damaged(1))
TS_WAIT, TS_ROLL, TS_CARRY, TS_DROP, TS_SINK, TS_FLOAT = 2, 3, 4, 5, 6, 7
TS_READ = TS_KILL + 2
# --- daStone::Act_c (454) [HD-ASM mode_carry_init : li r10,1 ; stw r10,0x790 en 0x0249CB88] --------------
SN_MODE = 0x790             # s32 m674 : 0 posée, 1 portée, 2 lancée, 3 coule
SN_WAIT, SN_CARRY, SN_DROP, SN_SINK = 0, 1, 2, 3
SN_READ = SN_MODE + 4
# --- daBomb2::Act_c (295) / daBomb_c (294) [HD-ASM, sync2_mobiles.md §2.4] -------------------------------
B2_STATE = 0x88C            # s32 mState : 0 attente, 1 portée, 2 explose, 3 coule
B2_TIMER = 0x934            # s32 mBombTimer : <= 0 = inerte (ni minuteur, ni coup) [HD-ASM 0x020CAB6C, 0x020CAC8C]
B2_KILL = 0x93C             # u8 field_0x740 : != 0 -> le jeu supprime la bombe à la fin de SON execute
                            # [HD-ASM _execute 0x020C9E78 : lbz r0,0x93C ; beq ; bl fopAcM_delete 0x025D57E0 ;
                            # écrit à 1 par mode_explode 0x020CAE54 et mode_sink 0x020CB2A4 ; 0 à la création]
B2_READ = B2_TIMER + 4
B2_START_CARRY = 2          # params : prm_get_start() = 2 -> start_carry (comme la bombe-fleur, 0x02077A20)
BOMB_FUNC = 0x3B0           # u32 pointeur de fonction du ptmf mFunc (294)
BOMB_EXPLODE_FN = 0x020C74A0
BOMB_READ = BOMB_FUNC + 4

# --- paramètres d'objet [DECOMP d_a_tsubo.h / d_a_stone.h] -------------------------------------------------
PRM_ITEM = 0x3F             # itemNo (6 bits bas), 0x3F = rien
PRM_STICK = 0x80000000      # daTsubo : prm_stick (effacé par mode_carry_init : rlwinm 0,1,31 en 0x024CD5BC)
PRM_SPEC_SHIFT, PRM_SPEC_NONE = 8, 0x3F       # daTsubo : spec 1-3 = bâton caché dans la jarre, 5 = switch
PRM_MOVEBG_SHIFT = 14       # daTsubo : 2 bits, 1 = sur un fond mobile
KEY_MASK = 0x7FFFFFC0       # clé : sans itemNo (vidé par A6, les explosions, nous) ni bit 31 (effacé à la prise)

GRAB_REFRESH = 3.0          # s : le porteur renvoie « grab » tant qu'il tient l'objet (arrivants, pertes)
HOLD_STALE = 5.0            # s sans state du porteur : marionnette posée (bombe : retirée)
CAR_STALE = 2.0             # s : state du porteur sans « car » pour cette clé : l'objet ne bouge plus
ROOM_SETTLE = 2.0
FIND_WAIT = 3.0             # s : objet de la clé introuvable (ou pas posé) chez nous : abandon (note)
REST_SCANS = 3              # relevés au repos avant « rest »
REST_EPS = 1.0              # u
VOL_MAX = 8.0               # s : jarre / pierre lancée jamais posée : « rest » d'office
BOMB_VOL_MAX = 15.0         # s : bombe lancée jamais explosée : « gone » (perdue)
GONE_SCANS = 2              # relevés sans l'objet avant « gone »
BOMB_SEEN = 2.0             # s : bombe créée pas encore vue : supprimée (elle exploserait au bout de 5 s)
ZOMBIE_KEEP = 12.0          # s : une bombe abandonnée JAMAIS VUE qui apparaît quand même est supprimée
BOMB_LIFE = 12.0            # s : garde-fou, une bombe du fantôme ne vit jamais plus (une vraie bombe explose
                            # 150 images = 5 s après sa création, dans la main ou non [DECOMP d_a_bomb2 attr 0x96])
BOMB_KILL_RETRY = 2.0       # s : drapeau de fin écrit mais bombe encore là (pas exécutée : cinématique) ->
                            # drapeau réécrit, et commande 5 en repli
BOMB_ENDED_KEEP = 60.0      # s : une bombe finie chez nous n'est jamais recréée par un « grab » en retard
# --- explosion d'une bombe du fantôme (bug vu en jeu le 2026-11-13 : bombe-fleur restée là, sans exploser) ---
BOOM_WAIT = 2.0             # s : fin « explose » reçue, jeu pas libre : explosion de la copie attendue au plus
BOOM_HOLD = 6.0             # s : copie qui explose : le jeu la supprime seul au bout de ~94 images (mode_explode
                            # attend Env_c::is_end [HD-ASM 0x020CAE14-0x020CAE54, Env_c::proc 0x020C8308]) ;
                            # drapeau de fin seulement si elle est encore là après ce délai
BOOM_MATCH = 300.0          # u : « boom » sans id (ancien client) rapproché de la copie du même porteur
BOOM_KEEP = 3.0             # s : explosion faite chez nous (copie ou rejeu) retenue contre un doublon
MAX_ZOMBIES = 64
CLEAN_TTL = 30.0            # s : nettoyage des marionnettes d'une salle quittée (la salle d'arrivée n'est
                            # « stable » qu'après ROOM_SETTLE s, et la porte est un événement : 3 s ne suffisaient
                            # pas, la marionnette restait figée en l'air, CARRY posé, dans la salle voisine chargée)
MAX_MB_CALLS = 2            # commandes de boîte aux lettres par relevé
HEAD_Y = 90.0               # u : sans position relative reçue, l'objet est posé au-dessus de la tête
REST_UP = 10.0              # u : un objet posé est relâché un peu au-dessus (il retombe)
HELD_MAX = 400.0            # u : décalage relatif maximal accepté (objet tenu)
FAR_MAX = 3000.0            # u : objet local trop loin du fantôme : pas de marionnette
MAX_FLYING = 4
MAX_PUPPETS = 8
KEY_MAX = 96
EVENTS = ("grab", "rest", "gone")
CAUSES = ("casse", "eau", "explose", "perdu")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _ptr(v) -> bool:
    return _int(v) and 0x10000000 <= v < 0x50000000


POS_MAX = 1e6               # u : bornes des positions reçues, comme les relectures (read_obj). Avant : 1e5, et en
                            # mer (carrés de 100 000 u) « rest » / « gone » étaient refusés (le « grab », dont la
                            # position est facultative, et le champ car passaient) : marionnette jamais finie.


def _vec(v, lim: float = POS_MAX) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(_num(x) and abs(x) < lim for x in v):
        return None
    return [float(x) for x in v]


def _ang3(v) -> list[int] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(_int(x) and -32768 <= x <= 65535 for x in v):
        return None
    return [_s16(x) for x in v]


def _s16(a) -> int:
    a = int(a) & 0xFFFF
    return a - 0x10000 if a & 0x8000 else a


def _key(k) -> str | None:
    return k if isinstance(k, str) and 0 < len(k) <= KEY_MAX else None


def carry_key(proc: int, params: int, home) -> str:
    """Clé réseau d'une jarre ou d'une pierre de stage : comme enemy_key (A6) mais sans itemNo ni bit 31,
    que le jeu, A6, les explosions et la marionnette changent."""
    hx, hy, hz = (int(round(v)) for v in home)
    return f"c:{proc}/{params & KEY_MASK:08X}/{hx},{hy},{hz}"


def a6_keys(proc: int, params: int, home) -> list[str]:
    """Clés A6 possibles (enemy_key) de la même jarre : paramètres d'origine, bit 31 effacé, itemNo 0x3F."""
    hx, hy, hz = (int(round(v)) for v in home)
    out = []
    for p in (params, params & 0x7FFFFFFF, params | PRM_ITEM, (params & 0x7FFFFFFF) | PRM_ITEM):
        k = f"{proc}/{p & 0xFFFFFFFF:08X}/{hx},{hy},{hz}"
        if k not in out:
            out.append(k)
    return out


def pot_shape_ok(proc: int, params: int) -> bool:
    """daTsubo : pas de bâton caché ni de switch (spec 0x3F), pas sur un fond mobile. daStone : toujours."""
    if proc != PROC_TSUBO:
        return proc == PROC_STONE
    return (params >> PRM_SPEC_SHIFT) & 0x3F == PRM_SPEC_NONE and (params >> PRM_MOVEBG_SHIFT) & 3 != 1


# --- repère de Link ------------------------------------------------------------------------------------
def _cs(rot_y) -> tuple[float, float]:
    a = (int(rot_y) & 0xFFFF) * 2 * math.pi / 65536
    return math.cos(a), math.sin(a)


def to_local(p, link_pos, rot_y) -> list[float]:
    """Position monde -> repère de Link (inverse de batons.base_matrix : T(pos) · Ry(rotY))."""
    c, s = _cs(rot_y)
    dx, dy, dz = (p[i] - link_pos[i] for i in range(3))
    return [c * dx - s * dz, dy, s * dx + c * dz]


def from_matrix(g, o) -> list[float]:
    """Point du repère local `o` dans le monde, G = matrice 3×4 du fantôme (batons.ghost_matrix)."""
    return [g[0] * o[0] + g[1] * o[1] + g[2] * o[2] + g[3],
            g[4] * o[0] + g[5] * o[1] + g[6] * o[2] + g[7],
            g[8] * o[0] + g[9] * o[1] + g[10] * o[2] + g[11]]


def ghost_rot(g) -> int:
    """Angle y du fantôme lu dans G (même convention que base_matrix : g[0] = cos, g[2] = sin)."""
    return _s16(int(round(math.atan2(g[2], g[0]) * 65536 / (2 * math.pi))))


# --- validation (serveur et client) ----------------------------------------------------------------------
def valid_carry(msg) -> dict | None:
    """Message « carry » (client -> serveur et relais) : None si invalide."""
    if not isinstance(msg, dict):
        return None
    ev, key = msg.get("ev"), _key(msg.get("key"))
    if ev not in EVENTS or key is None or not (key.startswith("c:") or key.startswith("b:")):
        return None
    out: dict = {"ev": ev, "key": key}
    pos = _vec(msg.get("pos"))
    if ev == "grab":
        proc = msg.get("proc")
        if proc not in PROCS or (key.startswith("b:") != (KINDS[proc] == KIND_BOMB)):
            return None
        out["proc"] = proc
        if pos is not None:
            out["pos"] = pos
        if msg.get("cause") == "rappel":
            out["cause"] = "rappel"
        return out
    if pos is None:
        return None
    out["pos"] = pos
    if ev == "rest":
        rot = _ang3(msg.get("rot", [0, 0, 0]))
        if rot is None:
            return None
        out["rot"] = rot
    else:
        cause = msg.get("cause", "casse")
        if cause not in CAUSES:
            return None
        out["cause"] = cause
    return out


def valid_car(car) -> dict | None:
    """Champ state.car : {k, h: 1, o, r} (tenu) ou {k, h: 0, w, r} (en vol)."""
    if not isinstance(car, dict):
        return None
    k, h, r = _key(car.get("k")), car.get("h"), _ang3(car.get("r", [0, 0, 0]))
    if k is None or h not in (0, 1) or r is None:
        return None
    if h == 1:
        o = _vec(car.get("o"), HELD_MAX)
        return None if o is None else {"k": k, "h": 1, "o": o, "r": r}
    w = _vec(car.get("w"), 1e6)
    return None if w is None else {"k": k, "h": 0, "w": w, "r": r}


# --- serveur -----------------------------------------------------------------------------------------------
class CarryTable:
    """Partie serveur (pure) : un porteur par objet et par zone, le premier « grab » gagne."""

    LOCK_TTL = 10.0      # s sans « grab » (renvoyé toutes les GRAB_REFRESH s) : verrou libéré
    KEYS_MAX = 64

    def __init__(self):
        self.locks: dict[str, dict[str, tuple[int, float]]] = {}   # zone -> clé -> (porteur, heure)

    def _holder(self, zone: str, key: str, now: float, alive=None):
        lk = self.locks.get(zone, {}).get(key)
        if lk is None:
            return None
        if now - lk[1] >= self.LOCK_TTL or (alive is not None and not alive(lk[0])):
            del self.locks[zone][key]
            return None
        return lk[0]

    def grab(self, zone: str, key: str, pid: int, now: float, alive=None) -> tuple[bool, int | None]:
        h = self._holder(zone, key, now, alive)
        if h is not None and h != pid:
            return False, h
        zl = self.locks.setdefault(zone, {})
        if key not in zl and len(zl) >= self.KEYS_MAX:
            # verrous expirés jamais relus (bombes : une clé par bombe, fin perdue) : purgés avant de refuser ;
            # sans cela, après 64 bombes dans la salle, chaque nouvelle prise était refusée (« deny »)
            for k in [k for k, v in zl.items() if now - v[1] >= self.LOCK_TTL]:
                del zl[k]
        if key not in zl and len(zl) >= self.KEYS_MAX:
            return False, None
        zl[key] = (pid, now)
        return True, pid

    def release(self, zone: str, key: str, pid: int, now: float, alive=None) -> bool:
        """rest / gone : accepté du porteur ou sans porteur ; libère le verrou."""
        h = self._holder(zone, key, now, alive)
        if h is not None and h != pid:
            return False
        self.locks.get(zone, {}).pop(key, None)
        return True

    def left(self, pid: int) -> None:
        for zl in self.locks.values():
            for k in [k for k, v in zl.items() if v[0] == pid]:
                del zl[k]

    def moved(self, pid: int, old: str | None) -> None:
        """Le porteur a changé de zone : ses verrous de l'ancienne zone sont libérés (il renvoie « grab »
        dans la nouvelle)."""
        zl = self.locks.get(old or "", {})
        for k in [k for k, v in zl.items() if v[0] == pid]:
            del zl[k]

    def forget(self, zone: str) -> None:
        self.locks.pop(zone, None)


# --- lectures ----------------------------------------------------------------------------------------------
_READ = {PROC_TSUBO: TS_READ, PROC_STONE: SN_READ, PROC_BOMB2: B2_READ, PROC_BOMB: BOMB_READ}


def read_obj(mem, base: int, proc: int | None = None) -> dict | None:
    """Une lecture : en-tête, paramètres, statut, position, angle et champs propres au proc. None si ce
    n'est pas un acteur vivant d'un des procs suivis (ou pas celui attendu), ou si une valeur est aberrante."""
    if mem is None or not _ptr(base):
        return None
    head = mem.try_read(base, 0x10)
    if head is None or len(head) < 0x10:
        return None
    bs, pid = struct.unpack_from(">II", head, 0)
    p = struct.unpack_from(">h", head, OFF_PROC)[0]
    if bs == 0 or p not in PROCS or (proc is not None and p != proc):
        return None
    raw = mem.try_read(base, _READ[p])
    if raw is None or len(raw) < _READ[p]:
        return None
    o = {"base": base, "pid": pid, "proc": p, "kind": KINDS[p],
         "params": struct.unpack_from(">I", raw, OFF_PARAMS)[0],
         "status": struct.unpack_from(">I", raw, OFF_STATUS)[0],
         "pos": struct.unpack_from(">3f", raw, OFF_POS),
         "rot": list(struct.unpack_from(">3h", raw, OFF_SHAPE))}
    if not all(math.isfinite(v) and abs(v) < 1e6 for v in o["pos"]):
        return None
    if p == PROC_TSUBO:
        o["mode"] = struct.unpack_from(">i", raw, TS_MODE)[0]
        o["type"] = struct.unpack_from(">i", raw, TS_TYPE)[0]
        o["kill"] = struct.unpack_from(">h", raw, TS_KILL)[0]
        if not 0 <= o["mode"] <= 7:
            return None
        o["carried"] = o["mode"] == TS_CARRY
        o["rest"] = o["mode"] == TS_WAIT
        o["water"] = o["mode"] in (TS_SINK, TS_FLOAT)
    elif p == PROC_STONE:
        o["mode"] = struct.unpack_from(">i", raw, SN_MODE)[0]
        if not 0 <= o["mode"] <= 3:
            return None
        o["carried"] = o["mode"] == SN_CARRY
        o["rest"] = o["mode"] == SN_WAIT
        o["water"] = o["mode"] == SN_SINK
    elif p == PROC_BOMB2:
        o["mode"] = struct.unpack_from(">i", raw, B2_STATE)[0]
        o["timer"] = struct.unpack_from(">i", raw, B2_TIMER)[0]
        if not 0 <= o["mode"] <= 3:
            return None
        o["carried"] = o["mode"] == 1
        o["rest"] = False
        o["water"] = o["mode"] == 3
        o["boom"] = o["mode"] == 2
    else:
        o["mode"] = 0
        o["carried"] = bool(o["status"] & ST_CARRY)
        o["rest"] = False
        o["water"] = False
        o["boom"] = struct.unpack_from(">I", raw, BOMB_FUNC)[0] == BOMB_EXPLODE_FN
    return o


def bomb_fate(mem, base: int, pid: int, proc: int) -> tuple[str, list[float]] | None:
    """Fin d'une bombe disparue entre deux relevés, lue dans sa mémoire libérée : à la suppression, le jeu remet
    seulement mBsType (+0x0) à 0 et la mémoire libérée garde le reste [LIVE, jeu/game.py _parse_actor].
    Bomb2 : mState +0x88C vaut encore 2 si elle a explosé (mode_explode la garde ~94 images puis pose le drapeau
    +0x93C [HD-ASM 0x020CAE14-0x020CAE54]), 3 si elle a coulé ; bombe du sac : mFunc +0x3B0 == procExplode.
    Renvoie (cause, position) : « explose », « eau » ou « perdu » (supprimée sans exploser) ; None si la mémoire
    ne tient plus CETTE bombe (id ou proc changés : réutilisée) ou si elle vit encore."""
    if mem is None or not _ptr(base) or proc not in (PROC_BOMB, PROC_BOMB2):
        return None
    n = B2_STATE + 4 if proc == PROC_BOMB2 else BOMB_READ
    raw = mem.try_read(base, n)
    if raw is None or len(raw) < n:
        return None
    bs, cur = struct.unpack_from(">II", raw, 0)
    if bs != 0 or cur != pid or struct.unpack_from(">h", raw, OFF_PROC)[0] != proc:
        return None
    pos = list(struct.unpack_from(">3f", raw, OFF_POS))
    if not all(math.isfinite(v) and abs(v) < POS_MAX for v in pos):
        return None
    if proc == PROC_BOMB2:
        st = struct.unpack_from(">i", raw, B2_STATE)[0]
        return ("explose" if st == 2 else "eau" if st == 3 else "perdu"), pos
    return ("explose" if struct.unpack_from(">I", raw, BOMB_FUNC)[0] == BOMB_EXPLODE_FN else "perdu"), pos


def link_ptrs(mem, link_base: int) -> tuple[int, int] | None:
    """(objet tenu +0x65A4, bâton +0x6594) ; None si illisible (prudence : aucune écriture)."""
    if mem is None or not _ptr(link_base):
        return None
    a = mem.try_read(link_base + LINK_EQUIP_PTR, 4)
    b = mem.try_read(link_base + LINK_GRAB_PTR, 4)
    if a is None or b is None:
        return None
    return struct.unpack(">I", b)[0], struct.unpack(">I", a)[0]


def report(mem, objs, link_base: int, link_pos=None, rot_y: int = 0) -> list[str]:
    """`wwhd.py portes` (LECTURE SEULE, test P0 du plan) : objet tenu par Link et objets portables de la
    salle (mode, statut, minuteurs), position relative à Link de l'objet tenu."""
    rows = []
    ptrs = link_ptrs(mem, link_base)
    held = ptrs[0] if ptrs else 0
    for a in objs or []:
        pid, base, proc = a[0], a[1], a[2]
        o = read_obj(mem, base, proc)
        if o is None:
            rows.append(f"  {KINDS.get(proc, proc)} {pid} : illisible")
            continue
        extra = ""
        if proc == PROC_TSUBO:
            extra = f" type {o['type']} m800 {o['kill']}"
        elif proc == PROC_BOMB2:
            extra = f" minuteur {o['timer']}"
        elif proc == PROC_BOMB:
            extra = f" explose {'oui' if o['boom'] else 'non'}"
        tag = " <- TENU PAR LINK" if base == held else ""
        rel = ""
        if base == held and link_pos is not None:
            r = to_local(o["pos"], link_pos, rot_y)
            rel = f" relatif ({r[0]:.0f}, {r[1]:.0f}, {r[2]:.0f})"
        rows.append(f"  {o['kind']} {pid} {o['params']:08X} mode {o['mode']} statut {o['status']:08X}"
                    f" (CARRY {'oui' if o['status'] & ST_CARRY else 'non'}){extra}"
                    f" pos ({o['pos'][0]:.0f}, {o['pos'][1]:.0f}, {o['pos'][2]:.0f}){rel}{tag}")
    rows.append(f"Link : objet tenu (+0x65A4) {held:08X}" if ptrs else "Link : illisible")
    return rows


# --- client ------------------------------------------------------------------------------------------------
class _Mine:
    """Objet que le Link local tient ou vient de lancer."""

    def __init__(self, o: dict, key: str, now: float):
        self.pid, self.base, self.proc, self.kind = o["pid"], o["base"], o["proc"], o["kind"]
        self.key = key
        self.t = now                 # début du port (ou du vol)
        self.grab_t = float("-inf")  # dernier « grab » envoyé
        self.pos = list(o["pos"])
        self.rot = list(o["rot"])
        self.water = False
        self.still = 0
        self.missing = 0
        self.zone: str | None = None


class _Puppet:
    """Marionnette chez nous d'un objet tenu ou lancé par un autre joueur."""

    def __init__(self, key: str, holder: int, proc: int, now: float, pos=None):
        self.key, self.holder, self.kind = key, holder, KINDS[proc]
        # proc de L'ACTEUR LOCAL : la bombe du fantôme est toujours une Bomb2 (295), même quand le porteur
        # tient une bombe du sac (294). Bug vu en jeu (2026-11-12) : avec proc = 294 reçu, toutes les relectures
        # read_obj(…, 294) de la Bomb2 échouaient : jamais pilotée (restée là où elle était tenue), et à la fin
        # prise pour « déjà disparue » : oubliée sans être retirée, inerte (minuteur 0) pour toujours.
        self.proc = PROC_BOMB2 if self.kind == KIND_BOMB else proc
        self.t = now                 # dernier message du porteur
        self.t0 = now
        self.pid: int | None = None
        self.base = 0
        self.orig: int | None = None   # paramètres d'origine (jarre, pierre)
        self.a6: list[str] = []
        self.phase = "attente"       # attente -> cree (bombe) -> tenu ; fin : release
        self.created_t = 0.0
        self.pos = list(pos) if pos else None
        self.release: tuple | None = None   # ("rest", pos, rot) | ("gone", pos, cause) | ("perdu",) | ("parti",)
        self.missing = 0
        self.car_t = now


class CarrySync:
    ROOM_SETTLE = ROOM_SETTLE

    def __init__(self, enabled: bool = False, clock=time.time):
        self.enabled = enabled
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.noted: set = set()
        self.ctx = None
        self.zone: str | None = None
        # rappels du client (A6, cruches) : ignore(pid), restore(pid), handled(zone, clés), jug(at, now)
        self.hooks: dict = {}
        self.relayable = None        # relayable(proc, params) -> bool (règle A6), None : tout
        self.held: _Mine | None = None
        self.flying: list[_Mine] = []
        self.denied: dict[str, float] = {}
        self.refused: set[int] = set()        # pids tenus pas partagés (contenu précieux…) : une note
        self.puppets: dict[str, _Puppet] = {}
        self.cleanup: list[tuple[float, _Puppet]] = []
        # bombes du fantôme finies (ou abandonnées) : suivies jusqu'à leur disparition constatée, retirées
        # par le jeu (u8 +0x93C) ; id -> {t, base (0 : jamais vue), kill_t (dernier drapeau), tries}
        self.zombies: dict[int, dict] = {}
        self.ended: dict[str, float] = {}     # clés de bombes finies chez nous : un « grab » en retard est ignoré
        self.created: set[int] = set()
        self.car_field: dict | None = None
        self.reset_req = False
        # explosions rejouées (--share-bombs chez NOUS) : la copie d'une bombe qui a explosé chez son porteur
        # explose elle-même (une seule explosion, à sa place, tout de suite) ; sinon elle disparaît seulement
        self.bombs = False
        self.booms_done: list[dict] = []      # {t, src, pos, key, copie} : explosions faites ici (copie / rejeu)
        self.n = {"grab_out": 0, "rest_out": 0, "gone_out": 0, "pris": 0, "poses": 0, "casses": 0,
                  "retires": 0, "bombes": 0, "explosions": 0, "refus": 0}

    # --- notes et files ----------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 512:
            self.noted.add(tag)
            self._note(s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def _send(self, ev: str, key: str, **kw) -> None:
        msg = dict(type="carry", ev=ev, key=key, zone=self.zone, **kw)
        with self.lock:
            self.out = (self.out + [msg])[-64:]
        self.n[f"{ev}_out"] = self.n.get(f"{ev}_out", 0) + 1

    def car(self) -> dict | None:
        """Champ state.car du dernier relevé (None : rien de tenu ni en vol)."""
        return self.car_field

    def puppet_pids(self) -> set[int]:
        return {p.pid for p in self.puppets.values() if p.pid is not None}

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        """Boucle réseau : rangé, appliqué au prochain relevé (thread de travail)."""
        if not self.enabled or not isinstance(msg, dict):
            return
        if me is not None and msg.get("from") == me:
            return
        m = dict(msg, _t=self.clock() if now is None else now)
        with self.lock:
            self.inbox = (self.inbox + [m])[-256:]

    def reset_network(self) -> None:
        """Connexion perdue : messages oubliés ; l'objet tenu sera réannoncé ; les marionnettes sont
        posées (bombes retirées) au prochain relevé."""
        with self.lock:
            self.inbox.clear()
            self.out.clear()
            self.reset_req = True              # appliqué par le thread de travail (step)

    def _apply_reset(self) -> None:
        with self.lock:
            if not self.reset_req:
                return
            self.reset_req = False
        if self.held is not None:
            self.held.grab_t = float("-inf")
        self.flying.clear()
        self.denied.clear()
        for p in self.puppets.values():
            if p.release is None:
                p.release = ("perdu",)

    # --- passe principale ----------------------------------------------------------------------------
    def step(self, mem, now: float | None = None, zone: str | None = None, ctx=None, objs=None,
             link_base: int = 0, me: dict | None = None, me_id: int | None = None, others: dict | None = None,
             g_of=None, room: int = -1, free: bool = True, settled: bool = True, drive: bool = True,
             can: bool = True, create=None, delete=None) -> None:
        """objs : [(id, adresse, proc, params, salle d'origine, taille, home)] (procs 453, 454, 294, 295) ;
        me : state local {pos, rotY} ; others : états des autres joueurs (id -> state) ; g_of(id) -> G du
        fantôme (batons.ghost_matrix) ; free : jeu libre ; settled : salle stable ; drive : pilotage permis
        (pas de changement de stage) ; can : écriture permise ; create(proc, params, pos, room) -> id
        (commande 4) ; delete(id) -> bool (commande 5)."""
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        others = others or {}
        self._apply_reset()
        with self.lock:
            inbox, self.inbox = self.inbox, []
        if ctx != self.ctx:
            self._new_visit(now, zone)
            self.ctx = ctx
        self.zone = zone
        self.car_field = None
        if zone is None or mem is None or objs is None:
            return
        idx = self._index(objs)
        for m in inbox:
            self._receive(m, zone, now)
        ok_write = free and settled and can
        if ok_write:
            ok_write = not _game_busy(mem)
        ptrs = link_ptrs(mem, link_base)
        self._observe_mine(mem, now, idx, ptrs, me, me_id)
        calls = [MAX_MB_CALLS]
        self._clean(mem, now, ptrs, ok_write, delete, calls)
        self._puppets(mem, now, zone, idx, ptrs, others, g_of, room, ok_write, drive and can, create, delete, calls)
        # bombes finies : retirées par le jeu dès que possible (écriture seule, pas de boîte aux lettres :
        # permise pendant une cinématique, jamais pendant un changement de stage)
        self._zombies(mem, idx, now, ptrs, drive and can, ok_write, delete, calls)

    def _index(self, objs) -> dict:
        by_pid, by_key = {}, {}
        dup = set()
        for a in objs:
            if len(a) < 7:
                continue
            pid, base, proc, params, hroom, size, home = a[:7]
            if proc not in SIZES:
                continue
            if size != SIZES[proc]:
                self._once(("taille", proc, size), f"objets portés : proc {proc} de taille 0x{size:X} inattendue : "
                                                   "ignoré")
                continue
            e = {"pid": pid, "base": base, "proc": proc, "params": params, "room": hroom, "home": home}
            by_pid[pid] = e
            if proc in (PROC_TSUBO, PROC_STONE):
                k = carry_key(proc, params, home)
                if k in by_key:
                    dup.add(k)
                by_key[k] = e
        for k in dup:
            del by_key[k]                      # deux objets de même clé : on ne choisit pas
        return {"pid": by_pid, "key": by_key}

    def _new_visit(self, now: float, zone: str | None) -> None:
        """Changement de salle (ou rechargement) : les marionnettes de l'ancienne visite sont nettoyées
        (posées, bombes retirées) si elles existent encore ; les objets en vol oubliés. L'objet tenu suit
        Link (le jeu le passe sur la couche du stage) : réannoncé dans la nouvelle zone."""
        for p in self.puppets.values():
            if p.kind == KIND_BOMB:
                self._bury(p, now)             # bombe : suivie jusqu'à sa disparition, retirée par le jeu
            elif p.pid is not None:
                self.cleanup.append((now, p))
        self.cleanup = self.cleanup[-16:]
        self.puppets.clear()
        # Bug vu en jeu (2026-11-13) : un seul relevé raté (sample() -> None : salle « peut-être rechargée »,
        # room_t remis à l'heure) changeait ctx et vidait `flying` : une bombe lancée ou posée n'avait plus ni
        # « gone » ni champ car ; chez l'autre sa copie restait (garde-fou 12 s), et objets mobiles perdait la
        # même bombe au même moment (aucun « boom »). Même zone : les bombes en vol restent suivies (id relu à
        # leur adresse ; si la salle a vraiment été rechargée, bomb_fate dit « perdu »).
        self.flying = [f for f in self.flying if f.kind == KIND_BOMB and zone is not None and f.zone == zone]
        if self.held is not None:
            self.held.grab_t = float("-inf")

    # --- réception -----------------------------------------------------------------------------------
    def _receive(self, m: dict, zone: str, now: float) -> None:
        if m.get("type") != "carry" or m.get("zone") != zone:
            return
        if m.get("ev") == "deny":
            k = _key(m.get("key"))
            if k:
                self.denied[k] = now
                self.n["refus"] += 1
                self._note("objets portés : objet soulevé en même temps qu'un autre joueur : il a gagné, tu "
                           "gardes ta copie (elle n'est pas montrée aux autres)")
            return
        v = valid_carry(m)
        src = m.get("from")
        if v is None or not _int(src):
            return
        k = v["key"]
        p = self.puppets.get(k)
        if v["ev"] == "grab":
            if self.held is not None and self.held.key == k:
                return                         # nous le tenons : conflit, le serveur a tranché
            if p is None and k in self.ended and now - self.ended[k] < BOMB_ENDED_KEEP:
                return                         # bombe déjà finie chez nous (rappel croisé) : jamais recréée
            if p is None:
                if len(self.puppets) >= MAX_PUPPETS:
                    self._once("max", "objets portés : trop d'objets portés à la fois : certains pas montrés")
                    return
                self.puppets[k] = _Puppet(k, src, v["proc"], now, v.get("pos"))
            elif p.release is None or p.holder == src:
                p.holder, p.t = src, now
                if p.release is not None and p.release[0] == "rest":
                    p.release = None           # reposé puis repris aussitôt (message croisé)
            return
        if p is None or p.holder != src:
            return
        p.t = now
        if v["ev"] == "rest":
            p.release = ("rest", v["pos"], v["rot"])
        else:
            p.release = ("gone", v["pos"], v["cause"])

    # --- chez le porteur ------------------------------------------------------------------------------
    def _observe_mine(self, mem, now: float, idx: dict, ptrs, me: dict | None, me_id) -> None:
        held_base = ptrs[0] if ptrs else 0
        mine_pids = self.puppet_pids() | self.created
        cand = None
        if _ptr(held_base):
            e = next((x for x in idx["pid"].values() if x["base"] == held_base), None)
            if e is not None and e["pid"] not in mine_pids:
                o = read_obj(mem, held_base, e["proc"])
                if o is not None and o["pid"] == e["pid"] and o["carried"]:
                    cand = (o, e)
        h = self.held
        if cand is not None:
            o, e = cand
            if h is None or h.pid != o["pid"]:
                if h is not None:
                    self._to_flying(h, now)
                self.flying = [f for f in self.flying if f.pid != o["pid"]]   # relancé puis repris
                key = self._my_key(o, e, me_id)
                if key:
                    self.denied.pop(key, None)  # nouvelle prise : un refus ancien (conflit passé) ne compte plus
                h = self.held = _Mine(o, key, now) if key else None
            if h is not None:
                h.pos, h.rot = list(o["pos"]), list(o["rot"])
                if h.zone != self.zone:
                    h.zone, h.grab_t = self.zone, float("-inf")   # nouvelle salle : réannoncé tout de suite
                if h.key not in self.denied and now - h.grab_t >= GRAB_REFRESH:
                    cause = {} if h.grab_t == float("-inf") else {"cause": "rappel"}
                    h.grab_t = now
                    self._send("grab", h.key, proc=h.proc, pos=[round(v, 1) for v in h.pos], **cause)
                if h.key not in self.denied and me and _vec(me.get("pos"), 1e6) and _num(me.get("rotY", 0)):
                    rel = to_local(h.pos, me["pos"], me.get("rotY", 0))
                    if all(abs(v) < HELD_MAX for v in rel):
                        self.car_field = {"k": h.key, "h": 1, "o": [round(v, 1) for v in rel],
                                          "r": [_s16(h.rot[0]), _s16(h.rot[1] - int(me.get("rotY", 0))),
                                                _s16(h.rot[2])]}
        elif h is not None:
            self._to_flying(h, now)
            self.held = None
        self._observe_flying(mem, now, idx)

    def _my_key(self, o: dict, e: dict, me_id) -> str | None:
        if o["kind"] == KIND_BOMB:
            return f"b:{me_id}:{o['pid']}" if _int(me_id) else None
        params = e["params"]
        if not pot_shape_ok(o["proc"], params) or (self.relayable is not None
                                                     and not self.relayable(o["proc"], params)):
            if o["pid"] not in self.refused:
                self.refused.add(o["pid"])
                self._note(f"objets portés : {o['kind']} au contenu précieux, à bâton caché ou sur un fond mobile "
                           f"(paramètres {params:08X}) : pas montrée aux autres (chacun garde la sienne)")
            return None
        return carry_key(o["proc"], params, e["home"])

    def _to_flying(self, h: _Mine, now: float) -> None:
        if h.key in self.denied:
            return
        h.t, h.still, h.missing = now, 0, 0
        self.flying = ([f for f in self.flying if f.pid != h.pid] + [h])[-MAX_FLYING:]

    def _observe_flying(self, mem, now: float, idx: dict) -> None:
        keep = []
        for f in self.flying:
            if f.kind == KIND_BOMB:
                o = read_obj(mem, f.base, f.proc)      # id et proc relus à son adresse : sans dépendre du relevé
            else:
                o = read_obj(mem, f.base, f.proc) if f.pid in idx["pid"] else None
            if o is None or o["pid"] != f.pid:
                fate = bomb_fate(mem, f.base, f.pid, f.proc) if f.kind == KIND_BOMB else None
                if fate is not None:
                    # disparue entre deux relevés : sa mémoire dit si elle a explosé (l'état « explose » dure
                    # ~3 s, un relevé lent ou raté le manquait : la copie disparaissait sans exploser)
                    cause, pos = fate
                    if cause == "perdu" and f.water:
                        cause = "eau"
                    self._send("gone", f.key, pos=[round(v, 1) for v in pos], cause=cause)
                    continue
                f.missing += 1
                if f.missing >= GONE_SCANS:
                    cause = "eau" if f.water else ("explose" if f.kind == KIND_BOMB else "casse")
                    self._send("gone", f.key, pos=[round(v, 1) for v in f.pos], cause=cause)
                    continue
                keep.append(f)
                continue
            f.missing = 0
            moved = math.dist(o["pos"], f.pos)
            f.pos, f.rot = list(o["pos"]), list(o["rot"])
            f.water = f.water or o["water"]
            if f.kind == KIND_BOMB:
                if o.get("boom") or o["water"]:
                    self._send("gone", f.key, pos=[round(v, 1) for v in f.pos],
                               cause="eau" if o["water"] and not o.get("boom") else "explose")
                    continue
                if now - f.t > BOMB_VOL_MAX:
                    self._send("gone", f.key, pos=[round(v, 1) for v in f.pos], cause="perdu")
                    continue
            else:
                if o["carried"]:
                    keep.append(f)             # repris par un ennemi, un grappin… : on attend
                    continue
                f.still = f.still + 1 if (o["rest"] and moved < REST_EPS) else 0
                if f.still >= REST_SCANS or now - f.t > VOL_MAX:
                    self._send("rest", f.key, pos=[round(v, 1) for v in f.pos], rot=[_s16(v) for v in f.rot])
                    continue
            keep.append(f)
        self.flying = keep
        if self.car_field is None and self.flying:
            f = self.flying[-1]
            if f.missing == 0:
                self.car_field = {"k": f.key, "h": 0, "w": [round(v, 1) for v in f.pos],
                                  "r": [_s16(v) for v in f.rot]}

    # --- chez les autres : marionnettes ----------------------------------------------------------------
    def _target(self, p: _Puppet, others: dict, g_of, now: float) -> tuple[list, list] | None:
        st = others.get(p.holder)
        car = valid_car(st.get("car")) if isinstance(st, dict) else None
        if car is not None and car["k"] == p.key:
            p.car_t = now
            if car["h"] == 0:
                return car["w"], car["r"]
            g = g_of(p.holder) if g_of is not None else None
            if g is None:
                return None
            rot = ghost_rot(g)
            return from_matrix(g, car["o"]), [car["r"][0], _s16(car["r"][1] + rot), car["r"][2]]
        if p.phase == "attente" and g_of is not None:
            g = g_of(p.holder)
            if g is not None:
                return from_matrix(g, [0.0, HEAD_Y, 0.0]), [0, ghost_rot(g), 0]
        return None

    def _holder_state(self, p: _Puppet, others: dict, zone: str, now: float) -> str:
        """'' si le porteur est là ; sinon « perdu » (muet) ou « parti » (autre salle). Un objet en vol
        n'a plus de « grab » (rappel) : c'est son champ state.car qui le garde en vie."""
        st = others.get(p.holder)
        if isinstance(st, dict):
            car = st.get("car")
            if isinstance(car, dict) and car.get("k") == p.key:
                p.car_t = max(p.car_t, st.get("_t", now))
        last = max(p.t, p.car_t)
        if not isinstance(st, dict) or now - st.get("_t", now) > HOLD_STALE or now - last > HOLD_STALE + GRAB_REFRESH:
            return "perdu"
        if isinstance(st.get("stage"), str) and _int(st.get("room")) and zone_of(st["stage"], st["room"]) != zone:
            return "parti"
        return ""

    def _puppets(self, mem, now, zone, idx, ptrs, others, g_of, room, ok_write, drive_ok, create, delete,
                 calls) -> None:
        for k in list(self.puppets):
            p = self.puppets[k]
            if p.release is None:
                why = self._holder_state(p, others, zone, now)
                if why:
                    p.release = (why,)
            if p.release is None and p.kind == KIND_BOMB and p.pid is not None and now - p.created_t > BOMB_LIFE:
                p.release = ("perdu",)         # garde-fou : une vraie bombe a explosé depuis longtemps
                self._note("objets portés : bombe du fantôme retirée (garde-fou de durée : fin jamais reçue)")
            if p.release is not None:
                if p.pid is None:
                    del self.puppets[k]        # jamais montrée : rien à défaire
                    continue
                if p.kind == KIND_BOMB:
                    if self._bomb_end(mem, p, now, ptrs, ok_write, drive_ok, delete, calls):
                        del self.puppets[k]
                    continue
                if not ok_write:
                    # on attend que le jeu soit libre (jamais abandonnée avec CARRY : elle resterait figée en
                    # l'air) ; seulement oubliée si elle a disparu entre-temps (lecture seule)
                    if p.phase != "cree" and p.base:
                        o = read_obj(mem, p.base, p.proc)
                        if o is None or o["pid"] != p.pid:
                            if p.kind != KIND_BOMB:
                                self._hook("handled", self.zone, p.a6)
                            del self.puppets[k]
                    continue
                if self._finish(mem, p, ptrs, delete, calls, now):
                    del self.puppets[k]
                continue
            if p.phase == "attente":
                if ok_write:
                    if p.kind == KIND_BOMB:
                        self._create_bomb(p, others, g_of, room, create, calls, now)
                    else:
                        self._take(mem, p, idx, ptrs, others, g_of, now)
                if p.phase == "attente" and now - p.t0 > FIND_WAIT:
                    del self.puppets[k]        # le prochain « grab » (rappel) relance l'essai
                continue
            if p.phase == "cree":
                self._adopt_bomb(mem, p, idx, now, ok_write, delete, calls)
                if p.phase == "cree" and now - p.created_t > BOMB_SEEN:
                    # jeu pas libre : pas de commande 5 ; retenue (supprimée dès qu'elle est vue, jeu libre)
                    self._give_up_bomb(p, delete if ok_write else None, calls, now)
                    del self.puppets[k]
                continue
            # tenu / en vol : pilotage
            if p.pid not in idx["pid"]:
                p.missing += 1
                if p.missing >= GONE_SCANS:
                    self._lost_locally(p, now)
                    del self.puppets[k]
                continue
            p.missing = 0
            if drive_ok:
                self._drive(mem, p, ptrs, others, g_of, now)

    def _take(self, mem, p: _Puppet, idx, ptrs, others, g_of, now) -> bool:
        """Prise d'une jarre / pierre locale de même clé. Vrai si faite (ou abandonnée avec une note)."""
        e = idx["key"].get(p.key)
        if e is None or e["proc"] != p.proc:
            if now - p.t0 > FIND_WAIT:
                self._once(("introuvable", p.key), f"objets portés : {p.kind} soulevée par un autre joueur "
                                                   "introuvable chez toi (déjà cassée ou salle pas chargée)")
            return False
        if ptrs is None or e["base"] in ptrs:
            return False                       # dans les mains du Link local (ou Link illisible)
        if not pot_shape_ok(e["proc"], e["params"]) or (self.relayable is not None
                                                          and not self.relayable(e["proc"], e["params"])):
            self._once(("precieux", p.key), f"objets portés : {p.kind} au contenu précieux : chacun garde la "
                                            "sienne (pas de marionnette)")
            p.t0 = float("-inf")
            return False
        tgt = self._target(p, others, g_of, now)
        if tgt is None:
            return False
        o = read_obj(mem, e["base"], e["proc"])
        if o is None or o["pid"] != e["pid"]:
            return False
        if math.dist(o["pos"], tgt[0]) > FAR_MAX:
            self._once(("loin", p.key), "objets portés : ta copie de l'objet est trop loin du fantôme : pas de "
                                        "marionnette")
            p.t0 = float("-inf")
            return False
        if not o["rest"] or o["status"] & (ST_CARRY | ST_HOOK) or o.get("kill", 0) != 0:
            return False                       # pas posée (roule, en vol, cachée, en train de casser) : plus tard
        b = e["base"]
        try:
            if o["params"] & PRM_ITEM != PRM_ITEM:
                mem.write(b + OFF_PARAMS, struct.pack(">I", o["params"] | PRM_ITEM))
            cur = read_obj(mem, b, e["proc"])
        except Exception:
            return False
        if cur is None or cur["pid"] != o["pid"] or not cur["rest"] or cur["status"] & (ST_CARRY | ST_HOOK):
            self._restore_item(mem, b, e["proc"], o["pid"], o["params"])
            return False
        # suivie AVANT le bit CARRY : une écriture ratée ensuite la fait reposer (jamais d'objet figé en l'air)
        p.pid, p.base, p.orig = o["pid"], b, o["params"]
        p.a6 = a6_keys(e["proc"], o["params"], e["home"])
        p.phase = "tenu"
        p.pos = list(tgt[0])
        self._hook("ignore", p.pid)
        try:
            mem.write(b + OFF_STATUS, struct.pack(">I", (cur["status"] | ST_CARRY) & 0xFFFFFFFF))
            # mode « en mouvement » : l'objet au repos est exécuté même hors de la vue, et passe en
            # mode_carry_init à l'image suivante (relu : si le jeu l'a déjà fait, on n'y touche pas)
            m = read_obj(mem, b, e["proc"])
            if m is not None and m["pid"] == o["pid"] and not m["carried"]:
                if e["proc"] == PROC_TSUBO:
                    mem.write(b + TS_MODE, struct.pack(">i", TS_ROLL))
                else:
                    mem.write(b + SN_MODE, struct.pack(">i", SN_DROP))
            mem.write(b + OFF_POS, struct.pack(">3f", *tgt[0]))
        except Exception:
            p.release = ("perdu",)
            return True
        self.n["pris"] += 1
        self._note(f"objets portés : {p.kind} soulevée par un autre joueur : au-dessus de la tête de son fantôme")
        return True

    @staticmethod
    def _restore_item(mem, base: int, proc: int, pid: int, params: int) -> None:
        cur = read_obj(mem, base, proc)
        if cur is not None and cur["pid"] == pid and cur["params"] & PRM_ITEM != params & PRM_ITEM:
            try:
                mem.write(base + OFF_PARAMS, struct.pack(">I", (cur["params"] & ~PRM_ITEM) | (params & PRM_ITEM)))
            except Exception:
                pass

    def _create_bomb(self, p: _Puppet, others, g_of, room, create, calls, now) -> None:
        if create is None or calls[0] <= 0 or not 0 <= room < 64:
            return
        tgt = self._target(p, others, g_of, now)
        if tgt is None:
            return
        calls[0] -= 1
        try:
            pid = create(PROC_BOMB2, B2_START_CARRY, tgt[0], room)
        except Exception as exc:
            self._note(f"objets portés : bombe du fantôme non créée ({exc})")
            pid = None
        if not _int(pid):
            p.t0 = min(p.t0, now - FIND_WAIT + 1.0)    # nouvel essai au plus tard dans 1 s, puis abandon
            return
        p.pid, p.phase, p.created_t, p.pos = pid, "cree", now, list(tgt[0])
        self.created.add(pid)
        if len(self.created) > 4096:
            self.created = {pid}

    def _adopt_bomb(self, mem, p: _Puppet, idx, now, ok_write, delete, calls) -> None:
        e = idx["pid"].get(p.pid)
        if e is None or e["proc"] != PROC_BOMB2 or not ok_write:
            return
        o = read_obj(mem, e["base"], PROC_BOMB2)
        if o is None or o["pid"] != p.pid:
            return
        try:
            # minuteur à 0 D'ABORD : la bombe ne peut plus exploser (ni minuteur, ni coup, ni souffle)
            mem.write(e["base"] + B2_TIMER, struct.pack(">i", 0))
            cur = read_obj(mem, e["base"], PROC_BOMB2)
            if cur is None or cur["pid"] != p.pid or cur["timer"] > 0:
                return
            mem.write(e["base"] + OFF_STATUS, struct.pack(">I", (cur["status"] | ST_CARRY) & 0xFFFFFFFF))
        except Exception:
            return
        p.base, p.phase = e["base"], "tenu"
        self.n["bombes"] += 1

    # --- explosion de la copie (bug vu en jeu le 2026-11-13) --------------------------------------------------
    def _bomb_end(self, mem, p: _Puppet, now, ptrs, ok_write, drive_ok, delete, calls) -> bool:
        """Fin d'une copie de bombe. Fin « explose » et --share-bombs chez nous : la copie (inerte, minuteur 0)
        explose ELLE-MÊME, à la position reçue, tout de suite : minuteur à 1, chk_exp_timer la fait exploser à
        l'image suivante (eff_explode : effet, son ; mode_explode_init : souffle, casse, blessures ; elle n'est
        plus dessinée), et le jeu la supprime seul à la fin de l'explosion [DECOMP d_a_bomb2.cpp chk_exp_timer,
        mode_explode ; HD-ASM 0x020CAE14]. Une seule explosion : pas si l'explosion de cette bombe a déjà été
        rejouée par objets mobiles (« boom » arrivé avant). Sinon (eau, perdue, jeu pas libre plus de
        BOOM_WAIT s, Link local qui la tient…) : retirée sans explosion, suivie jusqu'à sa disparition.
        Vrai : la marionnette est finie (à oublier) ; faux : on attend le jeu libre."""
        rel = p.release
        if (self.bombs and rel[0] == "gone" and rel[2] == "explose" and p.phase == "tenu" and p.base
                and not self._boom_done(p.holder, rel[1], p.key, now)):
            if not (ok_write and drive_ok):
                if now - p.t < BOOM_WAIT:
                    return False               # cinématique, boîte occupée : l'explosion attend un peu
            elif self._explode_copy(mem, p, rel[1], ptrs, now):
                return True
        # inerte, elle ne finirait jamais seule : suivie jusqu'à sa disparition (pas de jeu libre requis pour
        # l'écriture du drapeau de fin ; Link local qui la tient : attendu)
        self._bury(p, now, delete if ok_write else None, calls)
        return True

    def _explode_copy(self, mem, p: _Puppet, pos, ptrs, now) -> bool:
        if ptrs is None or p.base in ptrs:
            return False                       # dans les mains du Link local (ou Link illisible) : jamais
        o = read_obj(mem, p.base, PROC_BOMB2)
        if o is None or o["pid"] != p.pid or o["mode"] == 2:
            return False
        self._hook("boom", [float(v) for v in pos])   # jarres relayables vidées avant (leur objet arrive par A4)
        try:
            xyz = struct.pack(">3f", *(float(v) for v in pos))
            mem.write(p.base + OFF_POS, xyz)
            mem.write(p.base + OFF_OLD, xyz)
            mem.write(p.base + OFF_SPEED, struct.pack(">3f", 0.0, 0.0, 0.0))
            mem.write(p.base + OFF_SPEEDF, struct.pack(">f", 0.0))
            cur = read_obj(mem, p.base, PROC_BOMB2)
            if cur is None or cur["pid"] != p.pid:
                return False
            mem.write(p.base + B2_TIMER, struct.pack(">i", 1))    # en dernier : explose à l'image suivante
        except Exception:
            return False
        self.booms_done = (self.booms_done + [{"t": now, "src": p.holder, "pos": [float(v) for v in pos],
                                               "key": p.key, "copie": True}])[-32:]
        self._bury(p, now)
        z = self.zombies.get(p.pid)
        if z is not None:
            z["hold"] = now + BOOM_HOLD        # le jeu la supprime seul après l'explosion ; drapeau seulement après
        self.n["explosions"] += 1
        self._once("explose", "objets portés : la bombe du fantôme explose chez toi aussi (une seule explosion, "
                              "à sa place)")
        return True

    def _boom_done(self, src, pos, key, now) -> bool:
        """Explosion de cette bombe déjà faite ici (copie qui a explosé, ou « boom » rejoué par objets mobiles) ?
        Même clé ; sans clé (« boom » d'un ancien client), même porteur, à moins de BOOM_MATCH u, depuis 1 s."""
        self.booms_done = [b for b in self.booms_done if now - b["t"] < BOOM_KEEP]
        for b in self.booms_done:
            if key is not None and b["key"] == key:
                return True
            if (key is None or b["key"] is None) and b["src"] == src and now - b["t"] < 1.0 and pos is not None \
                    and math.dist(b["pos"], pos) <= BOOM_MATCH:
                return True
        return False

    def claim_boom(self, src, pos, bid=None, now: float | None = None) -> bool:
        """Rappel d'objets mobiles (thread de travail), pour un « boom » reçu : vrai si l'explosion est faite
        par la copie de cette bombe (ou l'a déjà été) ; objets mobiles ne la rejoue pas alors (une seule
        explosion, jamais de double dégât). Faux : objets mobiles la rejoue, et la copie (si elle existe) ne
        fera que disparaître. bid : id de process de la bombe chez son porteur (clé b:{src}:{bid})."""
        now = self.clock() if now is None else now
        pos = _vec(pos)
        if not self.enabled or not self.bombs or not _int(src) or pos is None:
            return False
        key = f"b:{src}:{bid}" if _int(bid) else None
        if self._boom_done(src, pos, key, now):
            return True
        p = self.puppets.get(key) if key else None
        if p is None and key is None:
            near = [q for q in self.puppets.values() if q.kind == KIND_BOMB and q.holder == src and q.pos
                    and math.dist(q.pos, pos) <= BOOM_MATCH]
            p = min(near, key=lambda q: math.dist(q.pos, pos)) if near else None
        if p is not None and p.kind == KIND_BOMB and p.holder == src and p.phase == "tenu" and p.base \
                and (p.release is None or p.release[0] == "gone"):
            p.release, p.t = ("gone", pos, "explose"), now
            return True
        self.booms_done = (self.booms_done + [{"t": now, "src": src, "pos": pos, "key": key, "copie": False}])[-32:]
        return False

    def _give_up_bomb(self, p: _Puppet, delete, calls, now) -> None:
        """Bombe créée jamais vue : supprimée par id (sans effet si elle n'existe pas encore), et retenue :
        si elle apparaît plus tard, elle est supprimée aussi (elle exploserait 5 s après sa création)."""
        if p.pid is None:
            return
        self._bury(p, now, delete, calls)
        self._note("objets portés : bombe du fantôme pas vue à temps : retirée")

    def _bury(self, p: _Puppet, now: float, delete=None, calls=None) -> None:
        """Fin d'une bombe du fantôme, quelle qu'en soit la raison : elle est inerte (minuteur 0), elle ne
        finirait jamais seule. Suivie dans `zombies` jusqu'à sa disparition constatée (adresse connue : sans
        dépendre du relevé des acteurs), retirée par le jeu (_zombies). Jamais vue (création en cours) :
        commande 5 par id tout de suite si permise, puis drapeau de fin dès qu'elle apparaît."""
        if p.kind != KIND_BOMB or p.pid is None:
            return
        self.ended[p.key] = now
        if len(self.ended) > 256:
            self.ended = {k: t for k, t in self.ended.items() if now - t < BOMB_ENDED_KEEP}
        z = self.zombies.get(p.pid)
        if z is None:
            if len(self.zombies) >= MAX_ZOMBIES:
                old = min(self.zombies, key=lambda i: self.zombies[i]["t"])
                del self.zombies[old]
                self._once("zombies", "objets portés : trop de bombes du fantôme à retirer à la fois : la plus "
                                      "ancienne n'est plus suivie")
            z = self.zombies[p.pid] = {"t": now, "base": p.base or 0, "kill_t": float("-inf"), "tries": 0}
        elif p.base and not z["base"]:
            z["base"] = p.base
        if not z["base"] and delete is not None and calls is not None and calls[0] > 0:
            calls[0] -= 1
            try:
                delete(p.pid)                  # sans effet si elle n'existe pas encore
            except Exception:
                pass

    @staticmethod
    def _bomb_alive(mem, base: int, pid: int) -> bool | None:
        """Bomb2 d'id `pid` à `base` encore vivante ? None : lecture impossible (on ne conclut rien)."""
        head = mem.try_read(base, 0x10) if (mem is not None and _ptr(base)) else None
        if head is None or len(head) < 0x10:
            return None
        bs, cur = struct.unpack_from(">II", head, 0)
        return bs != 0 and cur == pid and struct.unpack_from(">h", head, OFF_PROC)[0] == PROC_BOMB2

    def _kill_bomb(self, mem, base: int, pid: int) -> bool:
        """Drapeau de fin u8 +0x93C = 1 (relu juste avant : même id, proc 295) : le jeu supprime la bombe à la
        fin de son prochain execute, comme après son explosion. Écriture seule, sans boîte aux lettres."""
        if not self._bomb_alive(mem, base, pid):
            return False
        try:
            mem.write(base + B2_TIMER, struct.pack(">i", 0))   # toujours inerte jusqu'au bout
            mem.write(base + B2_KILL, b"\x01")
        except Exception:
            return False
        return True

    def _zombies(self, mem, idx, now, ptrs, kill_ok, ok_write, delete, calls) -> None:
        for pid, z in list(self.zombies.items()):
            base = z["base"]
            if not base:
                e = idx["pid"].get(pid)
                if e is not None and e["proc"] == PROC_BOMB2:
                    base = z["base"] = e["base"]
                elif now - z["t"] > ZOMBIE_KEEP:
                    del self.zombies[pid]      # jamais apparue : jamais rendue inerte, elle finirait seule
                    continue
                else:
                    continue
            alive = self._bomb_alive(mem, base, pid)
            if alive is False:
                del self.zombies[pid]          # disparue : c'est fini
                continue
            if alive is None or not kill_ok or now < z.get("hold", 0.0):
                continue                       # (hold : copie en train d'exploser, le jeu la supprime seul)
            if ptrs is None or base in ptrs:
                # dans les mains du Link local (ou Link illisible) : jamais supprimée sous ses doigts
                self._once(("tenue", pid), "objets portés : tu tiens la bombe du fantôme : elle disparaîtra "
                                           "quand tu la lâcheras")
                continue
            if now - z["kill_t"] < BOMB_KILL_RETRY:
                continue
            if z["tries"] > 0 and ok_write and delete is not None and calls[0] > 0:
                # drapeau déjà écrit mais toujours là (pas exécutée : cinématique ?) : commande 5 en repli
                calls[0] -= 1
                try:
                    delete(pid)
                except Exception:
                    pass
            if self._kill_bomb(mem, base, pid):
                if z["tries"] == 0:
                    self.n["retires"] += 1
                z["tries"] += 1
                z["kill_t"] = now

    def shutdown(self, mem, link_base: int = 0) -> int:
        """Arrêt du client : toutes les bombes du fantôme encore là sont retirées par le jeu (drapeau de fin,
        sans boîte aux lettres), sinon elles resteraient inertes jusqu'au déchargement de la salle (sauf celle
        que le Link local tient). Renvoie le nombre de drapeaux écrits. Jamais d'exception."""
        n = 0
        try:
            ptrs = link_ptrs(mem, link_base) or ()
            todo = [(p.pid, p.base) for p in self.puppets.values() if p.kind == KIND_BOMB and p.pid and p.base]
            todo += [(pid, z["base"]) for pid, z in self.zombies.items() if z["base"]]
            for pid, base in todo:
                if base in ptrs:
                    continue
                if self._kill_bomb(mem, base, pid):
                    n += 1
        except Exception:
            pass
        return n

    def _drive(self, mem, p: _Puppet, ptrs, others, g_of, now) -> None:
        tgt = self._target(p, others, g_of, now)
        if tgt is None:
            if now - p.car_t > CAR_STALE and p.kind != KIND_BOMB:
                p.release = ("perdu",)         # le porteur ne la montre plus (posée sans message ?)
            return
        if ptrs is None:
            return                             # Link illisible : prudence, rien d'écrit
        if p.base in ptrs:
            self._local_wins(p, now)
            return
        o = read_obj(mem, p.base, p.proc)
        if o is None or o["pid"] != p.pid:
            return
        if not o["status"] & ST_CARRY:
            # CARRY effacé par le jeu (damaged -> cancelCarryNow : elle casse) : plus rien à écrire ; une bombe
            # (inerte, posée au sol) est retirée : sinon elle resterait là, figée, pour toujours
            self.puppets.pop(p.key, None)
            self._lost_locally(p, now)
            return
        try:
            if p.kind == KIND_BOMB and o.get("timer", 0) > 0:
                mem.write(p.base + B2_TIMER, struct.pack(">i", 0))
            mem.write(p.base + OFF_POS, struct.pack(">3f", *tgt[0]))
            mem.write(p.base + OFF_SHAPE, struct.pack(">3h", *(_s16(v) for v in tgt[1])))
        except Exception:
            return
        p.pos = list(tgt[0])
        p.phase = "tenu"

    def _local_wins(self, p: _Puppet, now: float | None = None) -> None:
        """Le Link local tient la marionnette (course à la prise) : on la lui laisse, sans plus l'écrire.
        Bombe (inerte, elle ne lui servirait à rien et resterait éternelle) : retirée dès qu'il la lâche."""
        self.puppets.pop(p.key, None)
        if p.kind == KIND_BOMB:
            self._bury(p, self.clock() if now is None else now)
            return
        self._hook("restore", p.pid)
        self._note(f"objets portés : tu tiens toi-même la {p.kind} que l'autre joueur portait : elle reste à toi")

    def _lost_locally(self, p: _Puppet, now: float | None = None) -> None:
        """Marionnette disparue chez nous (cassée par ton épée, une explosion…) : pas relayée (ignore),
        la casse annoncée ensuite par l'autre pour cette clé est marquée traitée. Bombe : plus vue dans le
        relevé, ou CARRY perdu : enterrée (retirée si elle existe encore, adresse relue)."""
        if p.kind == KIND_BOMB:
            self._bury(p, self.clock() if now is None else now)
        else:
            self._hook("handled", self.zone, p.a6)
            self._note(f"objets portés : la {p.kind} du fantôme a cassé chez toi seulement")

    def _hook(self, name: str, *a) -> None:
        f = self.hooks.get(name)
        if f is None:
            return
        try:
            f(*a)
        except Exception:
            pass

    def _finish(self, mem, p: _Puppet, ptrs, delete, calls, now) -> bool:
        """Fin d'une marionnette (posée, cassée, retirée). Vrai si c'est fait (ou impossible à faire)."""
        rel = p.release
        what = rel[0]
        if p.phase == "cree":
            self._give_up_bomb(p, delete, calls, now)
            return True
        if p.kind == KIND_BOMB:
            self._bury(p, now, delete, calls)  # jamais de suppression abandonnée : suivie jusqu'au bout
            return True
        if ptrs is None:
            return False
        if p.base in ptrs:
            self._local_wins(p, now)
            return True
        o = read_obj(mem, p.base, p.proc) if p.base else None
        if o is None or o["pid"] != p.pid:
            self._hook("handled", self.zone, p.a6)
            return True                        # déjà disparue
        if what == "parti" or (what == "gone" and rel[2] == "casse" and p.proc == PROC_STONE):
            if delete is None or calls[0] <= 0:
                return False
            calls[0] -= 1
            try:
                ok = bool(delete(p.pid))
            except Exception:
                ok = False
            if not ok:
                return now - p.t > 10.0
            self._hook("handled", self.zone, p.a6)
            self.n["retires"] += 1
            return True
        if what == "gone" and rel[2] in ("casse", "explose", "perdu") and p.proc == PROC_TSUBO:
            if not self._break_at(mem, p, o, rel[1]):
                return now - p.t > 10.0
            self._hook("handled", self.zone, p.a6)
            if o["type"] == 2:
                self._hook("jug", list(rel[1]), now)
            self.n["casses"] += 1
            return True
        # posée : rest, eau (le jeu la fait couler / flotter), perdu (porteur muet), à la position voulue
        if what == "rest":
            pos, rot = rel[1], rel[2]
        elif what == "gone":
            pos, rot = rel[1], o["rot"]
        else:
            pos, rot = list(o["pos"]), o["rot"]
        if not self._put(mem, p, o, pos, rot, restore=what != "gone"):
            return now - p.t > 10.0
        if what == "gone":
            self._hook("handled", self.zone, p.a6)     # elle va couler : sa disparition n'est pas relayée
        else:
            self._hook("restore", p.pid)               # redevient une jarre normale chez nous
        self.n["poses"] += 1
        return True

    def _put(self, mem, p: _Puppet, o: dict, pos, rot, restore: bool) -> bool:
        b = p.base
        up = [float(pos[0]), float(pos[1]) + REST_UP, float(pos[2])]
        try:
            xyz = struct.pack(">3f", *up)
            mem.write(b + OFF_POS, xyz)
            mem.write(b + OFF_OLD, xyz)
            mem.write(b + OFF_SHAPE, struct.pack(">3h", *(_s16(v) for v in rot)))
            mem.write(b + OFF_SPEED, struct.pack(">3f", 0.0, 0.0, 0.0))
            mem.write(b + OFF_SPEEDF, struct.pack(">f", 0.0))
            if restore and p.orig is not None:
                self._restore_item(mem, b, p.proc, p.pid, p.orig)
            cur = read_obj(mem, b, p.proc)
            if cur is None or cur["pid"] != p.pid:
                return True                    # disparue entre-temps : plus rien à faire
            # CARRY effacé en dernier : le jeu la repose (speedF = 0 : pas de lancer)
            mem.write(b + OFF_STATUS, struct.pack(">I", cur["status"] & ~ST_CARRY & 0xFFFFFFFF))
        except Exception:
            return False
        return True

    @staticmethod
    def _break_at(mem, p: _Puppet, o: dict, at) -> bool:
        """Jarre marionnette cassée PAR LE JEU au point `at` : position, itemNo 0x3F (déjà), m800 = 1 ;
        damage_kill_proc tourne dans tous les modes [HD-ASM 0x024CD3CC] : damaged(1) puis suppression."""
        b = p.base
        try:
            xyz = struct.pack(">3f", *(float(v) for v in at))
            mem.write(b + OFF_POS, xyz)
            mem.write(b + OFF_OLD, xyz)
            if o["params"] & PRM_ITEM != PRM_ITEM:
                mem.write(b + OFF_PARAMS, struct.pack(">I", o["params"] | PRM_ITEM))
            cur = read_obj(mem, b, PROC_TSUBO)
            if cur is None or cur["pid"] != p.pid:
                return True
            mem.write(b + TS_KILL, struct.pack(">h", 1))
        except Exception:
            return False
        return True

    def _clean(self, mem, now, ptrs, ok_write, delete, calls) -> None:
        """Marionnettes d'une visite précédente (salle quittée, rechargée) : posées si elles existent encore,
        au plus CLEAN_TTL s (les bombes n'y passent pas : enterrées tout de suite, _new_visit)."""
        keep = []
        for t, p in self.cleanup:
            if p.kind == KIND_BOMB:
                self._bury(p, now)             # (ancien chemin) suivie jusqu'à sa disparition
                continue
            if now - t > CLEAN_TTL:
                continue
            if not ok_write:
                keep.append((t, p))
                continue
            p.release = ("perdu",)
            if not self._finish(mem, p, ptrs, delete, calls, now):
                keep.append((t, p))
        self.cleanup = keep

    # --- affichage ---------------------------------------------------------------------------------------
    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        n = self.n
        held = f"tenu : {self.held.kind}" if self.held is not None else "rien de tenu"
        return (f"{held} ; envoyés {n['grab_out']} prise(s), {n['rest_out']} pose(s), {n['gone_out']} fin(s) ; "
                f"chez toi {len(self.puppets)} marionnette(s), {n['pris']} prise(s), {n['bombes']} bombe(s), "
                f"{n['poses']} posée(s), {n['casses']} cassée(s), {n['retires']} retirée(s)"
                + (f", {n['explosions']} explosion(s) de bombe du fantôme" if n["explosions"] else "")
                + (f", {n['refus']} conflit(s)" if n["refus"] else ""))


def _game_busy(mem) -> bool:
    from synchro import obstacles
    try:
        return bool(obstacles.game_free(mem))
    except Exception:
        return True
