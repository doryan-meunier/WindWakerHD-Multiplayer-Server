"""Objets mobiles en direct (synchro v2, lot 3 ; docs/etudes/sync2_mobiles.md, la section
« Vérification » de l'étude prime) : logique testable sans Cemu.

Quatre synchros, chacune avec son option, toutes activées par --coop-donjon :

  Blocs poussés (--share-blocks, --no-blocks) : daObjMovebox (proc 43 : osiBLK, Hbox, miroirs Mmrr,
    MpwrB, DBLK, Hjump1…). Le bloc calcule sa position à chaque image depuis une CASE entière
    (m628/m62C) : écrire la position ne sert à rien. Chez le pousseur, la glissade (mMode 0 -> 1, ou
    m644 qui remonte, ou case qui change : glissades enchaînées séparées de ~4 images) part en
    « push » {dir, pull, at, to, y} ; la fin en « cell ». at / to / cell sont des POSITIONS
    MONDE (x, z) de la case, jamais des cases brutes : pour un bloc à chemin, home.pos dépend des
    switches lus à la création (path_init), donc du moment où chaque PC a chargé la salle. Chez les
    autres, la glissade est REJOUÉE par le jeu : m634 (dir), m644 (images), m630 (pas d'angle), puis
    mMode = 1, puis m64F = 0 en dernier (m64F est lu par mode_wait). Recalage préalable de la case si
    besoin. Refus : bloc local pas au repos, Link local en train de pousser (bit 0x800 de Link+0x3B8 :
    la fin de la glissade rejouée l'effacerait), |Δy| > 50, case d'arrivée occupée par un autre bloc
    (mode_walk ne teste aucun mur), case non alignée, géométrie at -> to fausse. Anti-écho : clé
    « rejeu en cours » jusqu'au retour au mode 0 + 0,5 s. Le serveur arbitre (verrou 1,5 s par clé,
    at = dernière case connue ; « at » et non « from », que le relais du serveur réserve à l'émetteur) et renvoie « deny » + « cell » au perdant, et un « snap » des cases
    à qui entre dans la zone (les blocs sans switch, salle 14 de la Caverne du Dragon, n'ont aucun
    autre état).
  Explosions (--share-bombs, alias --bombes-amies, --no-bombs) : une bombe « à moi » (passée par la
    main de MON Link : Link+0x65A4, jamais une bombe créée par ce client) qui explose part en
    « boom » {pos} : Bomb2 (295) mState +0x88C == 2, bombe de l'inventaire (294) mFunc +0x3B0 ==
    procExplode 0x020C74A0 [HD-ASM, écrit par procExplode_init ; à voir en jeu, test M0]. Chez les autres :
    commande 4 create(295, params 0x100 = EXPLODE_INSTANT, pos) : vraie explosion du jeu (effet,
    son, souffle, casse, blessures). Avant, le contenu des jarres relayables (A6) à moins de
    BOOM_POT_RADIUS est vidé (itemNo 0x3F) : leur objet arrive par A4 (sinon objet en double).
  Bombes-fleurs (--share-flowers, --no-flowers) : daBFlower (296). Une Bomb2 qui arrive dans la main
    de MON Link depuis une fleur (pointeur mpBombActor +0x870 de la fleur, sinon la fleur la plus
    proche à moins de 80 u) part en « pick » {key}. Chez les autres : u8 +0x860 (m58C) = 0 si la fleur
    est pleine (mBombScale.x +0x880 ≈ 1) : le jeu la vide (son de repousse) puis la fait repousser.
  Plateformes sur chemin (--share-lifts, --no-lifts) : daHmlif (proc 40, Hmlif / Hyuf1, Tour des
    Dieux). L'AUTORITÉ de la zone envoie « lift » à 1 Hz (index de point m448, sens m446, phase m488,
    position, speedF, cible m450, point précédent m45C, identité du chemin [n, x0, z0]). Chez les
    autres, l'état reçu est avancé de son âge le long du segment courant ; si l'écart dépasse
    LIFT_TOL (ou l'index diffère) et que le Link local n'est PAS dessus, l'état est réécrit (position
    en dernier). Link dessus : correction repoussée, au plus LIFT_DEFER s, puis seulement au-delà de
    LIFT_FORCE u. Jamais si l'identité du chemin diffère (setNextPath : autre chemin) ni si le point
    reçu sort du chemin local.

Non codés (voir docs/dev/SYNCHRO.md) : objets portés en marionnette (jarres 453, pierres 454 : CARRY
sans porteur [HYPO], conflits A6/A4 à régler, reporté), bombe tenue dans la main du fantôme, Klft,
Kokiie, Humi, Hcbh, statues Os, rotation des miroirs (recherche longue : repris par la synchro v3,
synchro/rares.py et docs/etudes/sync3_mecanismes_rares.md), Mswing, Kita, Blift, ponts de cordes
(irréaliste : le fantôme n'a pas de poids).

Garde commune avant toute écriture : jeu libre (ni événement, ni changement de stage), salle stable
depuis ROOM_SETTLE s, boîte aux lettres sans requête en attente, acteur relu juste avant (id de
process +0x4, proc +0x8, champs cohérents). Rien n'est sauvegardé par ces écritures, sauf path_save
d'un bloc à chemin rejoué (les mêmes switches qu'A1 relaie, écrits seulement si la case de départ
concorde en position monde).

Compatible pack v17 : écritures Python et commande 4 seulement (aucune commande v19).
"""
from __future__ import annotations

import math
import struct
import threading
import time

from synchro import obstacles
from synchro import portage

# --- procs et tailles d'instance HD [HD-ASM table des profils] -----------------------------------
PROC_HMLIF, PROC_MOVEBOX, PROC_BOMB, PROC_BOMB2, PROC_BFLOWER = 40, 43, 294, 295, 296
PROCS = (PROC_HMLIF, PROC_MOVEBOX, PROC_BFLOWER)           # relevés par le client (scanner)
SIZES = {PROC_MOVEBOX: 0x7D8, PROC_HMLIF: 0x5A4, PROC_BFLOWER: 0x890}
NAMES = {PROC_MOVEBOX: "bloc", PROC_HMLIF: "plateforme", PROC_BFLOWER: "bombe-fleur"}

# --- fopAc_ac_c (HD) ---------------------------------------------------------------------------------
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_HOME = 0x2EC            # home.pos f32[3] ; angle s16[3] à +0xC (y à 0x2FA) ; salle s8 à +0x12
OFF_POS = 0x314             # current.pos
OFF_SPEEDF = 0x370
LINK_PUSH = 0x3B8           # Link : bit 0x800 = pousse / tire (onPushPullKeep) [HD-ASM 0x023794F0]
LINK_PUSH_BIT = 0x800
LINK_GRAB_PTR = 0x65A4      # Link : objet tenu / en cours de ramassage [HD-ASM, batons_partages.md]

# --- daObjMovebox::Act_c (proc 43), champs GC + 0x118 [HD-ASM, étude §2.2] ----------------------------
MB_MODE = 0x41C             # s32 mMode : 0 attente, 1 glissade, 2 flotte
MB_BGC = 0x70C              # u32 mBgc.mStateFlags (bit 0 = au sol)
MB_TYPE = 0x710             # s32 mType
MB_PRMZ = 0x714             # u16 mPrmZ (chemin bits 0-7, switch 2 bits 8-15)
MB_CELL = 0x740             # s32[2] m628 / m62C
MB_STEP = 0x748             # f32 m630 = 0x8000 / durée
MB_DIR = 0x74C              # s32 m634 : 0 +z, 1 +x, 2 -z, 3 -x (repère du bloc) ; -1 AU REPOS (mode_wait_init,
                            # en ligne en 0x02377BF8 : li r12, -1 ; stw r12, 0x74C ; création 0x02377CBC) [HD-ASM]
MB_PPLABEL = 0x750          # u32 mPPLabel (bit 2 = traction)
MB_FRAMES = 0x75C           # s16 m644 : images restantes de la glissade
MB_SAVED = 0x767            # u8 m64F : path_save fait
MB_READ = 0x768             # une lecture : en-tête .. m64F
MB_ATTR = 0x101CB424        # table attr : + mType × 0x9C ; durées +0x04 (poussée) / +0x0A (traction)
MB_ATTR_STRIDE = 0x9C       # et taille de case +0x0C (75.0, écrite par l'initialiseur 0x0237A8A4)
MB_TYPES = 13               # types 0-12 (params bits 24-27) : l'initialiseur HD 0x0237A8A4 écrit la taille de
                            # case des 13 entrées attr (+0xC … +0x75C) [HD-ASM] ; enum Type_e [DECOMP]
MB_TYPE_MKIEB = 0xB         # TYPE_BLACK_BOX_WITH_MKIE (MkieBB) : pas de chemin (prmZ = switch 2) [DECOMP]
CELL_DEFAULT, FRAMES_DEFAULT = 75.0, 20
DIR_VEC = ((0, 1), (1, 0), (0, -1), (-1, 0))
CELL_MAX = 64

# --- daBomb2::Act_c (295) / daBomb_c (294) [HD-ASM, étude §2.4] ----------------------------------------
B2_STATE = 0x88C            # s32 mState : 0 attente, 1 portée, 2 explose, 3 coule
B2_EXPLODE = 2
BOMB_FUNC = 0x3B0           # u32 pointeur de fonction du ptmf mFunc (294)
BOMB_EXPLODE_FN = 0x020C74A0   # procExplode (294) : écrit par procExplode_init 0x020C61C4 [HD-ASM]
BOMB_INSTANT = 0x100        # params de Bomb2 : prm_make(EXPLODE_INSTANT, true)
BOOM_POT_RADIUS = 230.0     # u : souffle de Bomb2 200 u [DECOMP d_a_bomb2.cpp] + rayon d'une jarre

# --- daBFlower_c (296), champs GC + 0x2D4 [HD-ASM, étude §2.3] -------------------------------------------
BF_REGROW = 0x860           # u8 m58C : 1 = repousse armée ; 0 -> vidée puis repousse
BF_BOMB_PTR = 0x870         # pointeur mpBombActor
BF_SCALE = 0x880            # f32[3] mBombScale
BF_NEAR = 80.0              # u : repli sans pointeur, fleur la plus proche de la bombe

# --- daHmlif_c (40), champs GC + 0x118 [HD-ASM, étude §2.6] ---------------------------------------------
HL_STILL = 0x55D            # s8 m445 : 0xFF (-1) = immobile
HL_DIR = 0x55E              # s8 m446 : sens
HL_IDX = 0x560              # s8 m448 : index du point
HL_PATH = 0x564             # dPath* ppd
HL_TGT = 0x568              # cXyz m450 (cible)
HL_PRV = 0x574              # cXyz m45C (point précédent)
HL_PHASE = 0x5A0            # u8 m488 : 0..3
HL_READ = 0x5A4
# dPath : u16 m_num (+0), dPnt* m_points (+8) ; dPnt : 4 octets puis cXyz (+4), 0x10 octets
# [DECOMP d_path.h ; disposition HD supposée identique : vérifiée à chaque lecture]
PATH_NUM, PATH_PTS, PNT_POS, PNT_SIZE = 0x0, 0x8, 0x4, 0x10


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _ptr(v) -> bool:
    return _int(v) and 0x10000000 <= v < 0x50000000


def _pos(v, n: int = 3, lim: float = 1e5) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != n or not all(_num(x) and abs(x) < lim for x in v):
        return None
    return [float(x) for x in v]


def rkey(proc: int, room: int, params: int, home) -> str:
    """Clé réseau d'un objet de stage : proc, salle, paramètres, home x / z arrondis à 10 u (= position
    du fichier de stage pour ces objets, identique sur tous les PC)."""
    return f"{proc}:{room}:{params & 0xFFFFFFFF:08X}:{round(home[0] / 10) * 10:.0f},{round(home[2] / 10) * 10:.0f}"


def block_has_path(params: int, prmz: int, typ: int) -> bool:
    """path_init : chemin (mPrmZ & 0xFF) et switch de sauvegarde 1 (params bits 8-15) [DECOMP]."""
    return typ != MB_TYPE_MKIEB and (prmz & 0xFF) != 0xFF and (params >> 8) & 0xFF != 0xFF


def block_key(room: int, params: int, prmz: int, typ: int, home) -> str:
    """Bloc à chemin : SANS position (home.pos y est remplacé par un point du chemin qui dépend des
    switches au chargement) ; sinon avec home x / z (position du fichier, stable)."""
    base = f"43:{room}:{params & 0xFFFFFFFF:08X}:{prmz & 0xFFFF:04X}"
    if block_has_path(params, prmz, typ):
        return base
    return f"{base}:{round(home[0] / 10) * 10:.0f},{round(home[2] / 10) * 10:.0f}"


def cell_world(home, ang: int, cx: int, cz: int, size: float = CELL_DEFAULT) -> tuple[float, float]:
    """mode_wait : transS(home) · YrotM(home.angle.y) · transM(m628 × m0C, 0, m62C × m0C)."""
    a = ang * math.pi / 32768.0
    c, s = math.cos(a), math.sin(a)
    lx, lz = cx * size, cz * size
    return home[0] + c * lx + s * lz, home[2] - s * lx + c * lz


def world_cell(home, ang: int, x: float, z: float, size: float = CELL_DEFAULT) -> tuple[int, int, float]:
    """Inverse de cell_world, avec SON home : (case x, case z, écart en u à la case entière)."""
    a = ang * math.pi / 32768.0
    c, s = math.cos(a), math.sin(a)
    dx, dz = x - home[0], z - home[2]
    fx, fz = (c * dx - s * dz) / size, (s * dx + c * dz) / size
    cx, cz = round(fx), round(fz)
    return cx, cz, math.hypot(fx - cx, fz - cz) * size


# ---------------------------------------------------------------------------------------------------
# Validation des messages (serveur et client)
# ---------------------------------------------------------------------------------------------------
MOB_EVENTS = ("push", "cell", "pick")
KEY_MAX = 96
BOOM_KINDS = (PROC_BOMB, PROC_BOMB2)
LIFT_MAX = 32


def _key(k) -> str | None:
    return k if isinstance(k, str) and 0 < len(k) <= KEY_MAX else None


def valid_mob(msg) -> dict | None:
    """Événement « mob » (client -> serveur et relais) : None si invalide."""
    if not isinstance(msg, dict):
        return None
    ev, key = msg.get("ev"), _key(msg.get("key"))
    if ev not in MOB_EVENTS or key is None:
        return None
    out: dict = {"ev": ev, "key": key}
    if ev == "pick":
        return out if key.startswith(f"{PROC_BFLOWER}:") else None
    if not key.startswith(f"{PROC_MOVEBOX}:"):
        return None
    y = msg.get("y")
    if not _num(y) or abs(y) >= 1e5:
        return None
    out["y"] = float(y)
    if ev == "cell":
        cell = _pos(msg.get("cell"), 2)
        if cell is None:
            return None
        out["cell"] = cell
        return out
    d, pull = msg.get("dir"), msg.get("pull", 0)
    frm, to = _pos(msg.get("at"), 2), _pos(msg.get("to"), 2)
    if not _int(d) or not 0 <= d <= 3 or pull not in (0, 1, True, False) or frm is None or to is None:
        return None
    if not CELL_DEFAULT - 30 <= math.hypot(to[0] - frm[0], to[1] - frm[1]) <= CELL_DEFAULT * 3:
        return None                           # une case d'écart (taille 75 u ; tolérance large ici)
    out.update(dir=d, pull=int(bool(pull)), at=frm, to=to)
    return out


def valid_boom(msg) -> dict | None:
    if not isinstance(msg, dict):
        return None
    # positions jusqu'à 1e6 u comme les relectures (en mer, carrés de 100 000 u : refusées avant, à 1e5) ;
    # id : id de process de la bombe chez son porteur (facultatif ; objets portés rapproche la copie de la bombe)
    pos, kind, bid = _pos(msg.get("pos"), lim=1e6), msg.get("kind", PROC_BOMB2), msg.get("id")
    if pos is None or kind not in BOOM_KINDS:
        return None
    out = {"pos": pos, "kind": kind}
    if _int(bid) and 0 <= bid <= 0xFFFFFFFF:
        out["id"] = bid
    return out


def valid_lift_entry(e) -> dict | None:
    if not isinstance(e, dict):
        return None
    key = _key(e.get("key"))
    path, i, d, ph, sp = e.get("path"), e.get("i"), e.get("d"), e.get("ph"), e.get("sp")
    pos, tgt, prv = _pos(e.get("pos")), _pos(e.get("tgt")), _pos(e.get("prv"))
    if key is None or not key.startswith(f"{PROC_HMLIF}:") or None in (pos, tgt, prv):
        return None
    if not (isinstance(path, list) and len(path) == 3 and _int(path[0]) and 1 <= path[0] <= 255
            and all(_num(v) and abs(v) < 1e5 for v in path[1:])):
        return None
    if not (_int(i) and 0 <= i < path[0] and _int(d) and -1 <= d <= 1 and _int(ph) and 0 <= ph <= 3
            and _num(sp) and abs(sp) < 1000):
        return None
    return {"key": key, "path": [path[0], float(path[1]), float(path[2])], "i": i, "d": d, "ph": ph,
            "sp": float(sp), "pos": pos, "tgt": tgt, "prv": prv}


def valid_lift(msg) -> list[dict] | None:
    lst = msg.get("list") if isinstance(msg, dict) else None
    if not isinstance(lst, list):
        return None
    out = [e for e in (valid_lift_entry(x) for x in lst[:LIFT_MAX]) if e]
    return out or None


# ---------------------------------------------------------------------------------------------------
# Serveur : verrous et état des blocs par zone
# ---------------------------------------------------------------------------------------------------
class MobTable:
    """Partie serveur (pure) : verrou par clé de bloc, dernière case connue, débit des explosions."""

    LOCK = 1.5          # s : 20 images à 30 i/s + marge réseau
    FROM_TOL = 3.0      # u : « at » (case de départ) comparé à la dernière case connue (positions monde)
    KEYS_MAX = 64       # clés gardées au plus par zone
    BOOM_RATE = 4       # explosions relayées au plus par seconde et par joueur

    def __init__(self):
        self.cells: dict[str, dict[str, dict]] = {}       # zone -> clé -> {cell, y}
        self.locks: dict[str, dict[str, tuple]] = {}      # zone -> clé -> (joueur, heure)
        self.booms: dict[int, list[float]] = {}           # joueur -> heures

    def _lock(self, zone: str, key: str, now: float):
        lk = self.locks.get(zone, {}).get(key)
        if lk is not None and now - lk[1] >= self.LOCK:
            del self.locks[zone][key]
            return None
        return lk

    def push(self, zone: str, key: str, pid: int, frm, now: float, alive=None) -> tuple[bool, int | None, dict | None]:
        """(accepté, détenteur du verrou, dernière case connue à renvoyer au perdant)."""
        lk = self._lock(zone, key, now)
        if lk is not None and lk[0] != pid and (alive is None or alive(lk[0])):
            return False, lk[0], self.cells.get(zone, {}).get(key)
        known = self.cells.get(zone, {}).get(key)
        if known is not None and math.hypot(known["cell"][0] - frm[0], known["cell"][1] - frm[1]) > self.FROM_TOL:
            return False, None, known
        zl = self.locks.setdefault(zone, {})
        if key not in zl and len(zl) >= self.KEYS_MAX:
            return False, None, known
        zl[key] = (pid, now)
        return True, pid, None

    def cell(self, zone: str, key: str, pid: int, cell, y: float, now: float, alive=None) -> bool:
        """Case finale : acceptée du détenteur du verrou (qui est libéré) ou sans verrou actif."""
        lk = self._lock(zone, key, now)
        if lk is not None and lk[0] != pid and (alive is None or alive(lk[0])):
            return False
        zc = self.cells.setdefault(zone, {})
        if key not in zc and len(zc) >= self.KEYS_MAX:
            return False
        zc[key] = {"cell": [float(cell[0]), float(cell[1])], "y": float(y)}
        if lk is not None:
            self.locks[zone].pop(key, None)
        return True

    def snap(self, zone: str) -> list[dict]:
        return [{"key": k, "cell": v["cell"], "y": v["y"]} for k, v in self.cells.get(zone, {}).items()]

    def boom_ok(self, pid: int, now: float) -> bool:
        t = [x for x in self.booms.get(pid, []) if now - x < 1.0]
        ok = len(t) < self.BOOM_RATE
        if ok:
            t.append(now)
        self.booms[pid] = t
        return ok

    def forget(self, zone: str) -> None:
        """Zone vide : le jeu remet les blocs à leur place (ou à leur switch) au rechargement."""
        self.cells.pop(zone, None)
        self.locks.pop(zone, None)

    def left(self, pid: int) -> None:
        self.booms.pop(pid, None)
        for zl in self.locks.values():
            for k in [k for k, v in zl.items() if v[0] == pid]:
                del zl[k]


# ---------------------------------------------------------------------------------------------------
# Lectures
# ---------------------------------------------------------------------------------------------------
def _head(mem, base: int, proc: int, n: int) -> bytes | None:
    if not _ptr(base):
        return None
    raw = mem.try_read(base, n)
    if raw is None or len(raw) < n:
        return None
    bs, _pid = struct.unpack_from(">II", raw, 0)
    if bs == 0 or struct.unpack_from(">h", raw, OFF_PROC)[0] != proc:
        return None
    return raw


_ATTR_CACHE: dict = {}


def block_attr(mem, typ: int) -> tuple[int, int, float]:
    """(images de poussée, de traction, taille de case) lus dans la table attr ; défaut 20, 20, 75."""
    if typ in _ATTR_CACHE:
        return _ATTR_CACHE[typ]
    out = (FRAMES_DEFAULT, FRAMES_DEFAULT, CELL_DEFAULT)
    raw = mem.try_read(MB_ATTR + typ * MB_ATTR_STRIDE, 0x10) if 0 <= typ < MB_TYPES else None
    if raw is not None:
        push, pull = struct.unpack_from(">h", raw, 4)[0], struct.unpack_from(">h", raw, 0xA)[0]
        size = struct.unpack_from(">f", raw, 0xC)[0]
        if 1 <= push <= 120 and 1 <= pull <= 120 and math.isfinite(size) and 10.0 <= size <= 300.0:
            out = (push, pull, size)
            _ATTR_CACHE[typ] = out
    return out


def read_block(mem, base: int) -> dict | None:
    raw = _head(mem, base, PROC_MOVEBOX, MB_READ)
    if raw is None:
        return None
    o = {"base": base, "pid": struct.unpack_from(">I", raw, OFF_PC_ID)[0], "proc": PROC_MOVEBOX,
         "params": struct.unpack_from(">I", raw, OFF_PARAMS)[0],
         "home": struct.unpack_from(">3f", raw, OFF_HOME),
         "ang": struct.unpack_from(">h", raw, OFF_HOME + 0xE)[0],
         "room": struct.unpack_from(">b", raw, OFF_HOME + 0x12)[0],
         "pos": struct.unpack_from(">3f", raw, OFF_POS),
         "mode": struct.unpack_from(">i", raw, MB_MODE)[0],
         "ground": bool(struct.unpack_from(">I", raw, MB_BGC)[0] & 1),
         "type": struct.unpack_from(">i", raw, MB_TYPE)[0],
         "prmz": struct.unpack_from(">H", raw, MB_PRMZ)[0],
         "cell": struct.unpack_from(">2i", raw, MB_CELL),
         "dir": struct.unpack_from(">i", raw, MB_DIR)[0],
         "pull": bool(struct.unpack_from(">I", raw, MB_PPLABEL)[0] & 2),
         "frames": struct.unpack_from(">h", raw, MB_FRAMES)[0]}
    if not block_consistent(o):
        return None
    o["attr"] = block_attr(mem, o["type"])
    return o


def block_consistent(o: dict) -> bool:
    """m634 vaut -1 au repos (mode_wait_init) : seule une glissade (mode 1) exige une direction 0-3."""
    return (0 <= o["mode"] <= 2 and 0 <= o["type"] < MB_TYPES and -1 <= o["dir"] <= 3
            and (o["mode"] != 1 or o["dir"] >= 0)
            and all(-CELL_MAX <= c <= CELL_MAX for c in o["cell"]) and -2 <= o["frames"] <= 240
            and all(math.isfinite(v) and abs(v) < 1e6 for v in (*o["home"], *o["pos"])))


def read_flower(mem, base: int) -> dict | None:
    raw = _head(mem, base, PROC_BFLOWER, BF_SCALE + 12)
    if raw is None:
        return None
    sx = struct.unpack_from(">f", raw, BF_SCALE)[0]
    if not math.isfinite(sx):
        return None
    return {"base": base, "pid": struct.unpack_from(">I", raw, OFF_PC_ID)[0],
            "params": struct.unpack_from(">I", raw, OFF_PARAMS)[0], "regrow": raw[BF_REGROW],
            "bomb": struct.unpack_from(">I", raw, BF_BOMB_PTR)[0], "scale": sx,
            "pos": struct.unpack_from(">3f", raw, OFF_POS)}


def read_path(mem, ppd: int) -> tuple[int, list] | None:
    """(nombre de points, positions) du chemin, None si la disposition ne colle pas."""
    if not _ptr(ppd):
        return None
    raw = mem.try_read(ppd, 0xC)
    if raw is None:
        return None
    n, pts = struct.unpack_from(">H", raw, PATH_NUM)[0], struct.unpack_from(">I", raw, PATH_PTS)[0]
    if not 1 <= n <= 64 or not _ptr(pts):
        return None
    raw = mem.try_read(pts, n * PNT_SIZE)
    if raw is None:
        return None
    out = [struct.unpack_from(">3f", raw, k * PNT_SIZE + PNT_POS) for k in range(n)]
    if not all(math.isfinite(v) and abs(v) < 1e6 for p in out for v in p):
        return None
    return n, out


def read_lift(mem, base: int) -> dict | None:
    raw = _head(mem, base, PROC_HMLIF, HL_READ)
    if raw is None:
        return None
    o = {"base": base, "pid": struct.unpack_from(">I", raw, OFF_PC_ID)[0],
         "params": struct.unpack_from(">I", raw, OFF_PARAMS)[0],
         "pos": struct.unpack_from(">3f", raw, OFF_POS), "sp": struct.unpack_from(">f", raw, OFF_SPEEDF)[0],
         "still": struct.unpack_from(">b", raw, HL_STILL)[0], "d": struct.unpack_from(">b", raw, HL_DIR)[0],
         "i": struct.unpack_from(">b", raw, HL_IDX)[0], "ppd": struct.unpack_from(">I", raw, HL_PATH)[0],
         "tgt": struct.unpack_from(">3f", raw, HL_TGT), "prv": struct.unpack_from(">3f", raw, HL_PRV),
         "ph": raw[HL_PHASE]}
    if not all(math.isfinite(v) and abs(v) < 1e6 for v in (*o["pos"], *o["tgt"], *o["prv"], o["sp"])):
        return None
    path = read_path(mem, o["ppd"])
    if path is None:
        o["path"] = None
    else:
        n, pts = path
        o["path"] = [n, round(pts[0][0]), round(pts[0][2])]
    return o


def lift_predict(e: dict, age: float) -> list[float]:
    """État reçu avancé de `age` s le long du segment courant (pos -> cible, speedF u/image, 30 i/s),
    sans dépasser la cible."""
    pos, tgt = e["pos"], e["tgt"]
    d = math.dist(pos, tgt)
    step = min(d, max(0.0, e["sp"]) * 30.0 * max(0.0, min(age, 2.0)))
    if d < 1e-6 or step <= 0:
        return list(pos)
    return [p + (t - p) * step / d for p, t in zip(pos, tgt)]


def link_on(link_pos, plat_pos) -> bool:
    """Le Link local est-il debout sur la plateforme (géométrie, étude §3.4) ?"""
    if link_pos is None:
        return True                           # inconnu : prudence, on ne corrige pas
    dy = link_pos[1] - plat_pos[1]
    return math.hypot(link_pos[0] - plat_pos[0], link_pos[2] - plat_pos[2]) < 250.0 and -10.0 < dy < 60.0


# ---------------------------------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------------------------------
class MobileSync:
    ROOM_SETTLE = 2.0       # s dans la salle avant toute écriture
    PEND_TTL = 3.0          # s : une glissade reçue non rejouée est abandonnée (avec une note)
    CELL_TTL = 30.0         # s : une case reçue (état final ; la dernière remplace les précédentes) attend plus
    ECHO_TAIL = 0.5         # s après le retour au mode 0 d'un bloc rejoué : rien n'est renvoyé
    QUIET = 1.0             # s après un recalage de case : le changement n'est pas renvoyé
    REPLAY_CHECK = 0.35     # s : glissade rejouée pas commencée -> nouvel essai
    REPLAY_TRIES = 2
    GUARD_MAX = 5.0         # s : garde anti-écho au plus
    Y_PUSH = 50.0           # u : |Δy| maximal pour rejouer une glissade
    Y_FALL = 30.0           # u : bloc retombé au repos -> nouvelle case annoncée
    ALIGN = 10.0            # u : écart maximal à une case entière
    TO_TOL = 3.0            # u : géométrie from -> to
    OCCUPIED = 37.5         # u : demi-case : case d'arrivée occupée par un autre bloc
    BOOM_TTL = 2.0          # s : explosion reçue non rejouée -> abandonnée
    BOOM_MAX_RATE = 4       # explosions créées au plus par seconde
    BOMB_TTL = 30.0         # s : bombe « à moi » suivie au plus
    LIFT_PERIOD = 1.0       # s entre deux envois de l'autorité
    LIFT_TOL = 60.0         # u : écart toléré avant correction
    LIFT_FORCE = 300.0      # u : correction forcée sous les pieds de Link après LIFT_DEFER s
    LIFT_DEFER = 10.0
    LIFT_TTL = 2.5          # s : état reçu trop vieux -> ignoré

    def __init__(self, blocks: bool = False, bombs: bool = False, flowers: bool = False, lifts: bool = False,
                 clock=time.time):
        self.blocks_on, self.bombs_on, self.flowers_on, self.lifts_on = blocks, bombs, flowers, lifts
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.noted: set = set()
        self.ctx = None
        self.zone: str | None = None
        self.prep_pots = None                # rappel du client : vide les jarres relayables (pos) -> n
        # rappel d'objets portés : claim(src, pos, id, now) -> vrai si la copie de cette bombe explose elle-même
        # (pas de second Bomb2 : une seule explosion)
        self.claim = None
        # blocs
        self.prev: dict[str, dict] = {}
        self.guard: dict[str, dict] = {}      # clé -> {"t": début, "zero": heure du mode 0 vu, "kind"}
        self.quiet: dict[str, float] = {}
        self.denied: dict[str, float] = {}
        self.sent: dict[str, tuple] = {}      # clé -> (case monde, y) annoncée
        self.pending: list[dict] = []         # événements reçus (push, cell, snap) à appliquer
        self.replays: dict[str, dict] = {}    # clé -> {t, e, tries} (vérification)
        # bombes
        self.mine: dict[int, dict] = {}       # id -> {base, proc, t, pos}
        self.created: set[int] = set()        # bombes créées par nous : jamais relayées
        self.done_bombs: set[int] = set()
        self.booms: list[dict] = []
        self.boom_times: list[float] = []
        self.sent_booms: list[float] = []
        self.last_grab = 0
        # bombes-fleurs
        self.picks: list[dict] = []
        # plateformes
        self.lift_t = float("-inf")
        self.lift_in: dict | None = None      # dernier message de l'autorité {t, list, zone}
        self.lift_defer: dict[str, float] = {}
        self.lifts_off = ""                   # raison : correction désactivée pour la session
        self.n = {"push_out": 0, "push_in": 0, "cell_in": 0, "boom_out": 0, "boom_in": 0, "boom_copie": 0,
                  "pick_out": 0, "pick_in": 0, "lift_fix": 0, "refus": 0}

    @property
    def enabled(self) -> bool:
        return self.blocks_on or self.bombs_on or self.flowers_on or self.lifts_on

    # --- notes et files --------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 256:
            self.noted.add(tag)
            self._note(s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def _send(self, msg: dict) -> None:
        msg.setdefault("zone", self.zone)
        with self.lock:
            self.out = (self.out + [msg])[-128:]

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        """Boucle réseau : rangé dans la boîte de réception, appliqué au prochain step (thread de travail)."""
        if not isinstance(msg, dict) or (me is not None and msg.get("from") == me and msg.get("from") is not None):
            return
        m = dict(msg, _t=self.clock() if now is None else now)
        with self.lock:
            self.inbox = (self.inbox + [m])[-256:]

    def reset_network(self) -> None:
        with self.lock:
            self.inbox.clear()
            self.out.clear()
        self.pending.clear()
        self.booms.clear()
        self.picks.clear()
        self.lift_in = None

    def _reset_visit(self) -> None:
        self.prev.clear()
        self.guard.clear()
        self.quiet.clear()
        self.denied.clear()
        self.sent.clear()
        self.pending.clear()
        self.replays.clear()
        self.mine.clear()
        self.booms.clear()
        self.picks.clear()
        self.lift_in = None
        self.lift_defer.clear()
        self.last_grab = 0

    # --- passe principale ------------------------------------------------------------------------
    def step(self, mem, now: float | None = None, zone: str | None = None, ctx=None, actors=None,
             link_base: int = 0, link_pos=None, room: int = -1, auth: bool = False, free: bool = True,
             settled: bool = True, can: bool = True, create=None) -> None:
        """actors : [(id, adresse, proc, params, salle d'origine, taille, home)] (procs 43, 40, 296) ;
        ctx : (zone, heure de salle) — un changement oublie la visite ; free : jeu libre ; settled :
        salle stable ; can : écriture permise (boîte présente, sans requête en attente) ; create :
        create(proc, params, pos, room) -> id (commande 4) ; auth : autorité de la zone."""
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        if ctx != self.ctx:
            # même zone (un relevé raté remet seulement l'heure de la salle) : nos bombes en vol restent suivies,
            # sinon leur explosion n'était jamais envoyée (bug vu en jeu le 2026-11-13)
            mine = dict(self.mine) if zone is not None and zone == self.zone else {}
            self._reset_visit()
            self.mine.update(mine)
            self.ctx = ctx
        with self.lock:
            inbox, self.inbox = self.inbox, []
        self.zone = zone
        if zone is None or mem is None or actors is None:
            return
        for m in inbox:
            self._receive(m, zone, now)
        ok_write = free and settled and can and not obstacles.game_free(mem)
        objs = self._index(mem, actors)
        link_push = self._link_pushing(mem, link_base)
        if self.blocks_on:
            self._blocks_observe(now, objs["blocks"])
            self._blocks_apply(mem, now, objs["blocks"], link_push, ok_write)
        grab = self._grab(mem, link_base)
        if self.flowers_on:
            self._flowers_observe(mem, grab, objs["flowers"], now)
            self._flowers_apply(mem, now, objs["flowers"], ok_write)
        if self.bombs_on:
            self._bombs_observe(mem, grab, now)
            self._bombs_apply(now, room, ok_write, create)
        self.last_grab = grab
        if self.lifts_on:
            if auth:
                self._lifts_send(objs["lifts"], now)
            else:
                self._lifts_apply(mem, now, objs["lifts"], link_pos, ok_write)

    def _index(self, mem, actors) -> dict:
        """Lecture des objets de la salle, rangés par clé (clés en double : ignorées, note unique)."""
        out = {"blocks": {}, "flowers": {}, "lifts": {}}
        dup: dict[str, int] = {}
        for a in actors:
            if len(a) < 7:
                continue
            pid, base, proc, params, room, size, home = a[:7]
            if proc not in SIZES:
                continue
            if size != SIZES[proc]:
                self._once(("taille", proc, size), f"objets mobiles : {NAMES[proc]} (proc {proc}) : taille "
                                                   f"d'instance 0x{size:X} inattendue : ignoré(e)")
                continue
            if proc == PROC_MOVEBOX and self.blocks_on:
                o = read_block(mem, base)
                if o is None or o["pid"] != pid:
                    continue
                k, tab = block_key(o["room"], o["params"], o["prmz"], o["type"], o["home"]), out["blocks"]
            elif proc == PROC_BFLOWER and self.flowers_on:
                o = read_flower(mem, base)
                if o is None or o["pid"] != pid:
                    continue
                k, tab = rkey(proc, room, params, home), out["flowers"]
            elif proc == PROC_HMLIF and self.lifts_on:
                o = read_lift(mem, base)
                if o is None or o["pid"] != pid:
                    continue
                k, tab = rkey(proc, room, params, home), out["lifts"]
            else:
                continue
            o["key"] = k
            dup[k] = dup.get(k, 0) + 1
            tab[k] = o
        for k, n in dup.items():
            if n > 1:
                for tab in out.values():
                    tab.pop(k, None)
                self._once(("double", k), f"objets mobiles : {n} objets avec la même clé {k} : ignorés")
        return out

    @staticmethod
    def _link_pushing(mem, link_base: int) -> bool:
        if not _ptr(link_base):
            return True                       # Link inconnu : prudence
        raw = mem.try_read(link_base + LINK_PUSH, 4)
        return raw is None or bool(struct.unpack(">I", raw)[0] & LINK_PUSH_BIT)

    @staticmethod
    def _grab(mem, link_base: int) -> int:
        if not _ptr(link_base):
            return 0
        raw = mem.try_read(link_base + LINK_GRAB_PTR, 4)
        v = struct.unpack(">I", raw)[0] if raw else 0
        return v if _ptr(v) else 0

    def _receive(self, m: dict, zone: str, now: float) -> None:
        t = m.get("type")
        if m.get("zone") != zone:
            return                            # autre salle (message en retard) : ignoré
        if t == "mob":
            ev = m.get("ev")
            if ev == "deny":
                k = _key(m.get("key"))
                if k:
                    self.denied[k] = now
                    self._note("objets mobiles : bloc poussé en même temps qu'un autre joueur : il a gagné, "
                               "ton bloc sera recalé")
                return
            if ev == "snap":
                for e in (m.get("list") or [])[:MobTable.KEYS_MAX]:
                    v = valid_mob(dict(e, ev="cell") if isinstance(e, dict) else None)
                    if v is not None and self.blocks_on:
                        self._queue(dict(v, t=m["_t"], snap=True))
                return
            v = valid_mob(m)
            if v is None:
                return
            v["t"] = m["_t"]
            v["src"] = m.get("from")
            if v["ev"] == "pick":
                if self.flowers_on:
                    self.picks.append(v)
            elif self.blocks_on:
                self._queue(v)
        elif t == "boom" and self.bombs_on:
            v = valid_boom(m)
            if v is not None:
                if self.claim is not None:
                    try:
                        mine = bool(self.claim(m.get("from"), v["pos"], v.get("id"), now))
                    except Exception:
                        mine = False
                    if mine:
                        self.n["boom_copie"] += 1     # la copie de cette bombe explose elle-même (objets portés)
                        return
                v.update(t=m["_t"], src=m.get("from"))
                self.booms.append(v)
                self.booms = self.booms[-16:]
        elif t == "lift" and self.lifts_on:
            lst = valid_lift(m)
            if lst:
                self.lift_in = {"t": m["_t"], "list": lst}

    def _queue(self, v: dict) -> None:
        """Une case reçue est l'état final du bloc : elle remplace les événements encore en attente pour
        ce bloc (sinon une glissade ou une case périmée appliquée après elle le déplacerait à tort)."""
        if v["ev"] == "cell":
            self.pending = [e for e in self.pending if e["key"] != v["key"]]
        self.pending = (self.pending + [v])[-256:]

    # --- blocs : relevé (envoi) ------------------------------------------------------------------
    def _world(self, o: dict, cell) -> list[float]:
        _push, _pull, size = block_attr_cached(o)
        x, z = cell_world(o["home"], o["ang"], cell[0], cell[1], size)
        return [round(x, 1), round(z, 1)]

    def _blocks_observe(self, now: float, blocks: dict) -> None:
        for k in [k for k in self.prev if k not in blocks]:
            del self.prev[k]
        for k, o in blocks.items():
            p = self.prev.get(k)
            self.prev[k] = o
            if p is None or p["pid"] != o["pid"]:
                continue                      # première vue : état de référence
            new_slide = o["mode"] == 1 and p["mode"] != 1
            g = self.guard.get(k)
            if g is not None:
                # la glissade rejouée n'est « vue » qu'en mode 1 (ou case changée) : un relevé fait avant
                # que le jeu ait lu nos écritures (encore mode 0) ne lance pas la fin de la garde
                if not g.get("seen") and (o["mode"] == 1 or tuple(o["cell"]) != tuple(g.get("cell0", o["cell"]))):
                    g["seen"] = True
                if g.get("seen") and o["mode"] == 0 and g.get("zero") is None:
                    g["zero"] = now
                if g.get("zero") is not None and o["mode"] == 1:
                    # nouvelle glissade APRÈS le retour au repos du rejeu : c'est MON Link qui pousse (un
                    # rejeu enchaîné aurait renouvelé la garde) ; elle part (sinon perdue pour les autres)
                    del self.guard[k]
                elif (g.get("zero") is not None and now - g["zero"] >= self.ECHO_TAIL) or now - g["t"] > self.GUARD_MAX:
                    del self.guard[k]
                    self.sent[k] = (tuple(self._world(o, o["cell"])), o["pos"][1])
                    continue
                else:
                    continue                  # rejeu en cours : rien n'est renvoyé (anti-écho)
            if now < self.quiet.get(k, 0.0) and not new_slide:
                self.sent[k] = (tuple(self._world(o, o["cell"])), o["pos"][1])
                continue                      # recalage reçu : pas renvoyé (une vraie poussée, si)
            dn = self.denied.get(k)
            if dn is not None:
                if o["mode"] == 0 and now - dn >= self.ECHO_TAIL:
                    del self.denied[k]
                continue                      # poussée refusée : on attend le recalage du serveur
            cell_changed = tuple(o["cell"]) != tuple(p["cell"])
            ended = p["mode"] == 1 and o["mode"] == 0
            started = o["mode"] == 1 and (p["mode"] != 1 or cell_changed or o["frames"] > p["frames"])
            if cell_changed or ended:
                self._send_cell(k, o)
            elif o["mode"] == 0 and o["ground"] and k in self.sent \
                    and abs(self.sent[k][1] - o["pos"][1]) > self.Y_FALL:
                self._send_cell(k, o)        # bloc tombé d'un rebord : nouvelle hauteur
            if started:
                # ma poussée passe avant ce qui attend encore pour ce bloc (le serveur arbitre : refus
                # + case si ma case de départ est périmée, sinon la case du gagnant suivra)
                self.pending = [e for e in self.pending if e["key"] != k]
                self.replays.pop(k, None)
                frm = self._world(o, o["cell"])
                dx, dz = DIR_VEC[o["dir"]]
                to = self._world(o, (o["cell"][0] + dx, o["cell"][1] + dz))
                self._send({"type": "mob", "ev": "push", "key": k, "dir": o["dir"], "pull": int(o["pull"]),
                            "at": frm, "to": to, "y": round(o["pos"][1], 1)})
                self.n["push_out"] += 1

    def _send_cell(self, k: str, o: dict) -> None:
        w = self._world(o, o["cell"])
        self.sent[k] = (tuple(w), o["pos"][1])
        self._send({"type": "mob", "ev": "cell", "key": k, "cell": w, "y": round(o["pos"][1], 1)})

    # --- blocs : application (réception) ---------------------------------------------------------
    def _occupied(self, blocks: dict, key: str, x: float, z: float, y: float) -> bool:
        for k, o in blocks.items():
            if k != key and math.hypot(o["pos"][0] - x, o["pos"][2] - z) < self.OCCUPIED \
                    and abs(o["pos"][1] - y) < self.Y_PUSH:
                return True
        return False

    def _drop(self, e: dict, why: str) -> None:
        self.n["refus"] += 1
        what = "glissade" if e["ev"] == "push" else "case"
        self._note(f"objets mobiles : {what} d'un bloc reçue non rejouée ({why})")

    def _blocks_apply(self, mem, now: float, blocks: dict, link_push: bool, ok_write: bool) -> None:
        # vérification des rejeux : glissade pas commencée -> nouvel essai
        for k, r in list(self.replays.items()):
            o = blocks.get(k)
            if o is None:
                del self.replays[k]
                continue
            if o["mode"] == 1 or tuple(o["cell"]) != tuple(r["cell0"]):
                del self.replays[k]
                continue
            if now - r["t"] >= self.REPLAY_CHECK:
                del self.replays[k]
                self.guard.pop(k, None)
                if r["tries"] < self.REPLAY_TRIES:
                    self.pending.insert(0, dict(r["e"], tries=r["tries"]))
                else:
                    self._drop(r["e"], "le jeu n'a pas lancé la glissade")
        keep = []
        for e in self.pending:
            k = e["key"]
            ttl = self.CELL_TTL if e["ev"] == "cell" else self.PEND_TTL
            if now - e["t"] > ttl:
                self._drop(e, "salle pas prête, bloc introuvable ou occupé pendant "
                              f"{ttl:g} s" + ("" if k in blocks else " (bloc absent chez toi)"))
                continue
            o = blocks.get(k)
            # rejeu précédent encore en glissade : attendre ; revenu au repos (fin de garde) : la glissade
            # suivante d'une poussée continue peut partir tout de suite (la garde est renouvelée)
            sliding = k in self.guard and self.guard[k].get("zero") is None
            if o is None or not ok_write or sliding or k in self.replays or o["mode"] != 0:
                keep.append(e)
                continue
            r = self._apply_one(mem, now, e, o, blocks, link_push)
            if r == "attendre":
                keep.append(e)
            elif r:
                self._drop(e, r)
        self.pending = keep

    def _apply_one(self, mem, now: float, e: dict, o: dict, blocks: dict, link_push: bool) -> str:
        """'' = fait ; 'attendre' = plus tard ; sinon raison du refus."""
        push, pull, size = block_attr_cached(o)
        if e["ev"] == "cell":
            cx, cz, err = world_cell(o["home"], o["ang"], e["cell"][0], e["cell"][1], size)
            if err > self.ALIGN or not (-CELL_MAX <= cx <= CELL_MAX and -CELL_MAX <= cz <= CELL_MAX):
                return f"case non alignée chez toi ({err:.0f} u)"
            if (cx, cz) == tuple(o["cell"]):
                return ""
            if e["y"] > o["pos"][1] + self.Y_PUSH:
                self._once(("diverge", e["key"]), "objets mobiles : bloc divergent (tombé chez toi, pas chez "
                                                  "l'autre) : ressors et rentre dans la salle")
                return ""
            x, z = cell_world(o["home"], o["ang"], cx, cz, size)
            if self._occupied(blocks, e["key"], x, z, o["pos"][1]):
                return "case occupée par un autre bloc"
            if link_push:
                return "attendre"
            if not self._write_cell(mem, o, (cx, cz)):
                return "acteur changé avant l'écriture"
            self.quiet[e["key"]] = now + self.QUIET
            self.n["cell_in"] += 1
            if not e.get("snap"):
                self._note(f"objets mobiles : bloc recalé ({e['key']})")
            return ""
        # push
        if link_push:
            return "attendre"                 # la fin du rejeu effacerait le bit « pousse » de ton Link
        if abs(e["y"] - o["pos"][1]) > self.Y_PUSH:
            return f"bloc à une autre hauteur chez toi (Δy {e['y'] - o['pos'][1]:.0f} u)"
        fx, fz, err = world_cell(o["home"], o["ang"], e["at"][0], e["at"][1], size)
        if err > self.ALIGN or not (-CELL_MAX < fx < CELL_MAX and -CELL_MAX < fz < CELL_MAX):
            return f"case de départ non alignée chez toi ({err:.0f} u)"
        dx, dz = DIR_VEC[e["dir"]]
        tx, tz = cell_world(o["home"], o["ang"], fx + dx, fz + dz, size)
        if math.hypot(tx - e["to"][0], tz - e["to"][1]) > self.TO_TOL:
            return "direction incohérente (repère du bloc différent)"
        if tuple(o["cell"]) == (fx + dx, fz + dz):
            # déjà sur la case d'arrivée (poussé ici aussi en même temps, ou nouvel essai alors que le jeu
            # avait fini la glissade) : ni recalage en arrière ni deuxième glissade
            return ""
        if self._occupied(blocks, e["key"], tx, tz, o["pos"][1]):
            return "case d'arrivée occupée chez toi"
        frames = pull if e.get("pull") else push
        if not self._write_push(mem, o, (fx, fz), e["dir"], frames):
            return "acteur changé avant l'écriture"
        self.guard[e["key"]] = {"t": now, "zero": None, "seen": False, "cell0": (fx, fz)}
        self.replays[e["key"]] = {"t": now, "e": e, "tries": e.get("tries", 0) + 1, "cell0": (fx, fz)}
        self.n["push_in"] += 1
        return ""

    @staticmethod
    def _same_block(mem, o: dict) -> dict | None:
        cur = read_block(mem, o["base"])
        if cur is None or cur["pid"] != o["pid"] or cur["params"] != o["params"] or cur["mode"] != 0:
            return None
        return cur

    def _write_cell(self, mem, o: dict, cell) -> bool:
        if self._same_block(mem, o) is None:
            return False
        try:
            mem.write(o["base"] + MB_CELL, struct.pack(">2i", int(cell[0]), int(cell[1])))
        except Exception:
            return False
        return True

    def _write_push(self, mem, o: dict, frm, d: int, frames: int) -> bool:
        """Ordre (étude §3.4 corrigé) : recalage éventuel, m634, m644, m630, mMode = 1, m64F = 0."""
        cur = self._same_block(mem, o)
        if cur is None:
            return False
        try:
            b = o["base"]
            if tuple(cur["cell"]) != tuple(frm):
                mem.write(b + MB_CELL, struct.pack(">2i", int(frm[0]), int(frm[1])))
            mem.write(b + MB_DIR, struct.pack(">i", d))
            mem.write(b + MB_FRAMES, struct.pack(">h", frames))
            mem.write(b + MB_STEP, struct.pack(">f", 32768.0 / frames))
            mem.write(b + MB_MODE, struct.pack(">i", 1))
            mem.write(b + MB_SAVED, b"\x00")
        except Exception:
            return False
        return True

    # --- bombes-fleurs ---------------------------------------------------------------------------
    def _flowers_observe(self, mem, grab: int, flowers: dict, now: float) -> None:
        if not grab or grab == self.last_grab:
            return
        raw = _head(mem, grab, PROC_BOMB2, OFF_POS + 12)
        if raw is None:
            return
        bpos = struct.unpack_from(">3f", raw, OFF_POS)
        best, dist = None, BF_NEAR
        for k, f in flowers.items():
            if f["bomb"] == grab:
                best = k
                break
            d = math.hypot(f["pos"][0] - bpos[0], f["pos"][2] - bpos[2])
            if d < dist:
                best, dist = k, d
        if best is not None:
            self._send({"type": "mob", "ev": "pick", "key": best})
            self.n["pick_out"] += 1

    def _flowers_apply(self, mem, now: float, flowers: dict, ok_write: bool) -> None:
        keep = []
        for e in self.picks:
            f = flowers.get(e["key"])
            if now - e["t"] > self.PEND_TTL:
                self._note("objets mobiles : bombe-fleur vidée ailleurs, pas chez toi (salle pas prête ou "
                           "fleur introuvable)")
                continue
            if f is None or not ok_write:
                keep.append(e)
                continue
            cur = read_flower(mem, f["base"])
            if cur is None or cur["pid"] != f["pid"]:
                continue
            if cur["regrow"] != 1 or cur["scale"] < 0.95:
                continue                      # déjà vide ou en train de repousser : rien à faire
            try:
                mem.write(f["base"] + BF_REGROW, b"\x00")
                self.n["pick_in"] += 1
            except Exception:
                keep.append(e)
        self.picks = keep

    # --- explosions ------------------------------------------------------------------------------
    def _bombs_observe(self, mem, grab: int, now: float) -> None:
        if grab and grab != self.last_grab:
            raw = mem.try_read(grab, 0x10)
            if raw is not None and len(raw) >= 0x10:
                pid, proc = struct.unpack_from(">I", raw, OFF_PC_ID)[0], struct.unpack_from(">h", raw, OFF_PROC)[0]
                if proc in BOOM_KINDS and pid not in self.created and pid not in self.done_bombs \
                        and pid not in self.mine and len(self.mine) < 16:
                    self.mine[pid] = {"base": grab, "proc": proc, "t": now, "pos": None}
        for pid, b in list(self.mine.items()):
            n = (B2_STATE + 4) if b["proc"] == PROC_BOMB2 else (BOMB_FUNC + 4)
            raw = _head(mem, b["base"], b["proc"], max(n, OFF_POS + 12))
            if raw is None or struct.unpack_from(">I", raw, OFF_PC_ID)[0] != pid:
                del self.mine[pid]
                # disparue entre deux relevés : sa mémoire libérée dit si elle a explosé (l'état « explose » ne
                # dure que ~3 s : un relevé lent ou raté le manquait, et l'explosion n'était jamais envoyée)
                fate = portage.bomb_fate(mem, b["base"], pid, b["proc"])
                if fate is not None and fate[0] == "explose":
                    self._boom_out(pid, b["proc"], fate[1], now)
                elif b["proc"] == PROC_BOMB and fate is None:
                    self._once("bombe294", "objets mobiles : une bombe de ton sac a disparu sans explosion vue "
                                           "(normal si elle est tombée à l'eau ; sinon la détection 294 est à "
                                           "vérifier, test M0 du plan)")
                continue
            pos = struct.unpack_from(">3f", raw, OFF_POS)
            if b["proc"] == PROC_BOMB2:
                boom = struct.unpack_from(">i", raw, B2_STATE)[0] == B2_EXPLODE
            else:
                boom = struct.unpack_from(">I", raw, BOMB_FUNC)[0] == BOMB_EXPLODE_FN
            if boom and all(math.isfinite(v) for v in pos):
                del self.mine[pid]
                self._boom_out(pid, b["proc"], pos, now)
            elif now - b["t"] > self.BOMB_TTL:
                del self.mine[pid]

    def _boom_out(self, pid: int, proc: int, pos, now: float) -> None:
        self.done_bombs.add(pid)
        if len(self.done_bombs) > 4096:
            self.done_bombs.clear()
        self.sent_booms = [t for t in self.sent_booms if now - t < 1.0]
        if len(self.sent_booms) < self.BOOM_MAX_RATE:
            self.sent_booms.append(now)
            # id : la copie de cette bombe chez les autres (objets portés, clé b:{moi}:{id}) explose elle-même
            self._send({"type": "boom", "pos": [round(v, 1) for v in pos], "kind": proc, "id": pid})
            self.n["boom_out"] += 1

    def _bombs_apply(self, now: float, room: int, ok_write: bool, create) -> None:
        keep = []
        for e in self.booms:
            if now - e["t"] > self.BOOM_TTL:
                self._note("objets mobiles : explosion d'un autre joueur non rejouée (cinématique, salle pas "
                           "prête ou boîte aux lettres occupée)")
                continue
            if not ok_write or create is None or not 0 <= room < 64:
                keep.append(e)
                continue
            self.boom_times = [t for t in self.boom_times if now - t < 1.0]
            if len(self.boom_times) >= self.BOOM_MAX_RATE:
                keep.append(e)
                continue
            if self.prep_pots is not None:
                try:
                    self.prep_pots(e["pos"])
                except Exception:
                    pass
            try:
                pid = create(PROC_BOMB2, BOMB_INSTANT, e["pos"], room)
            except Exception as exc:
                self._note(f"objets mobiles : explosion non rejouée ({exc})")
                continue
            self.boom_times.append(now)
            if pid is not None:
                self.created.add(pid)
                if len(self.created) > 4096:
                    self.created.clear()
                self.n["boom_in"] += 1
        self.booms = keep

    # --- plateformes -----------------------------------------------------------------------------
    def _lifts_send(self, lifts: dict, now: float) -> None:
        self.lift_in = None
        if now - self.lift_t < self.LIFT_PERIOD:
            return
        self.lift_t = now
        lst = []
        for k, o in sorted(lifts.items()):
            if o["still"] == -1 or o["path"] is None or not 0 <= o["i"] < o["path"][0] or not 0 <= o["ph"] <= 3:
                continue
            lst.append({"key": k, "path": o["path"], "i": o["i"], "d": max(-1, min(1, o["d"])), "ph": o["ph"],
                        "sp": round(o["sp"], 3), "pos": [round(v, 1) for v in o["pos"]],
                        "tgt": [round(v, 1) for v in o["tgt"]], "prv": [round(v, 1) for v in o["prv"]]})
        if lst:
            self._send({"type": "lift", "list": lst[:LIFT_MAX]})

    def _lifts_apply(self, mem, now: float, lifts: dict, link_pos, ok_write: bool) -> None:
        msg = self.lift_in
        if msg is None or self.lifts_off or not ok_write:
            return
        age = now - msg["t"]
        if age > self.LIFT_TTL:
            self.lift_in = None
            return
        self.lift_in = None
        for e in msg["list"]:
            o = lifts.get(e["key"])
            if o is None or o["still"] == -1:
                continue
            if o["path"] is None:
                self._once(("chemin", e["key"]), "objets mobiles : chemin de plateforme illisible chez toi : "
                                                 "plateforme non corrigée")
                continue
            if o["path"] != e["path"]:
                self._once(("autre", e["key"]), "objets mobiles : plateforme sur un autre chemin chez toi "
                                                "(switch pas encore reçu ?) : pas de correction")
                continue
            pred = lift_predict(e, age)
            dev = math.dist(pred, o["pos"])
            if e["i"] == o["i"] and e["d"] == o["d"] and dev <= self.LIFT_TOL:
                self.lift_defer.pop(e["key"], None)
                continue
            if link_on(link_pos, o["pos"]):
                t0 = self.lift_defer.setdefault(e["key"], now)
                if now - t0 < self.LIFT_DEFER or dev <= self.LIFT_FORCE:
                    continue
            self.lift_defer.pop(e["key"], None)
            if self._write_lift(mem, o, e, pred):
                self.n["lift_fix"] += 1
                self._once(("lift", e["key"]), f"objets mobiles : plateforme recalée sur l'autorité ({dev:.0f} u)")

    def _write_lift(self, mem, o: dict, e: dict, pred) -> bool:
        cur = read_lift(mem, o["base"])
        if cur is None or cur["pid"] != o["pid"] or cur["path"] != e["path"]:
            return False
        try:
            b = o["base"]
            mem.write(b + HL_TGT, struct.pack(">3f", *e["tgt"]))
            mem.write(b + HL_PRV, struct.pack(">3f", *e["prv"]))
            mem.write(b + HL_IDX, struct.pack(">b", e["i"]))
            mem.write(b + HL_DIR, struct.pack(">b", e["d"]))
            mem.write(b + HL_PHASE, bytes([e["ph"]]))
            mem.write(b + OFF_SPEEDF, struct.pack(">f", e["sp"]))
            mem.write(b + OFF_POS, struct.pack(">3f", *pred))
        except Exception:
            return False
        return True

    # --- affichage -------------------------------------------------------------------------------
    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        parts = []
        n = self.n
        if self.blocks_on:
            parts.append(f"blocs {n['push_out']} poussé(s) / {n['push_in']} rejoué(s), {n['cell_in']} recalé(s)")
        if self.bombs_on:
            parts.append(f"explosions {n['boom_out']} envoyée(s) / {n['boom_in']} rejouée(s)"
                         + (f" (+ {n['boom_copie']} par la bombe du fantôme)" if n["boom_copie"] else ""))
        if self.flowers_on:
            parts.append(f"bombes-fleurs {n['pick_out']} / {n['pick_in']}")
        if self.lifts_on:
            parts.append(f"plateformes {n['lift_fix']} correction(s)")
        if n["refus"]:
            parts.append(f"{n['refus']} refus")
        return ", ".join(parts)


def empty_pots(mem, pots, pos, relayable, held: int = 0, radius: float = BOOM_POT_RADIUS) -> int:
    """Avant une explosion rejouée : les jarres daTsubo (453) relayables par A6 (relayable(proc, params))
    à moins de `radius` de `pos` sont vidées (itemNo 0x3F) : si l'explosion les casse, leur objet arrive
    déjà de l'autre joueur par A4 (sinon objet en double). Jamais la jarre tenue par Link (`held`).
    Revalidation (id, proc, paramètres) juste avant chaque écriture. Renvoie le nombre de jarres vidées."""
    from synchro import drops
    n = 0
    for p in pots or []:
        base, pid, params = p.get("base"), p.get("pid"), p.get("params")
        if p.get("proc") != drops.TSUBO_PROC or not _ptr(base) or base == held or not _int(params):
            continue
        pp = p.get("pos")
        if not _pos(pp, lim=1e6) or math.dist(pp, pos) > radius:
            continue
        if params & drops.POT_ITEM_MASK == drops.POT_ITEM_MASK or not relayable(p["proc"], params):
            continue
        raw = _head(mem, base, drops.TSUBO_PROC, OFF_PARAMS + 4)
        if raw is None or struct.unpack_from(">I", raw, OFF_PC_ID)[0] != pid \
                or struct.unpack_from(">I", raw, OFF_PARAMS)[0] != params:
            continue
        try:
            mem.write(base + OFF_PARAMS, struct.pack(">I", params | drops.POT_ITEM_MASK))
            n += 1
        except Exception:
            pass
    return n


def report(mem, actors, link_base: int) -> list[str]:
    """`wwhd.py mobiles` (LECTURE SEULE, test M0 du plan) : une ligne par objet mobile relu et l'état de
    Link (bit « pousse », objet tenu, bombe tenue). actors : [(id, adresse, proc, params, salle, taille,
    home)]."""
    rows = []
    for a in actors:
        pid, base, proc, params, room, size, home = a[:7]
        if proc == PROC_MOVEBOX:
            o = read_block(mem, base)
            if o is None:
                rows.append(f"  bloc {pid} {params:08X} : illisible ou incohérent (taille 0x{size:X})")
                continue
            push, pull, cell = o["attr"]
            rows.append(f"  bloc {pid} type {o['type']} {block_key(o['room'], o['params'], o['prmz'], o['type'], o['home'])}"
                        f" mode {o['mode']} case {tuple(o['cell'])} dir {o['dir']} m644 {o['frames']}"
                        f" {'traction' if o['pull'] else 'poussée'} sol {int(o['ground'])} y {o['pos'][1]:.1f}"
                        f" (attr {push}/{pull} images, case {cell:g} u)")
        elif proc == PROC_BFLOWER:
            f = read_flower(mem, base)
            rows.append(f"  bombe-fleur {pid} : illisible" if f is None else
                        f"  bombe-fleur {pid} {rkey(proc, room, params, home)} m58C {f['regrow']} échelle "
                        f"{f['scale']:.2f} bombe {f['bomb']:08X}")
        elif proc == PROC_HMLIF:
            o = read_lift(mem, base)
            rows.append(f"  plateforme {pid} : illisible" if o is None else
                        f"  plateforme {pid} {rkey(proc, room, params, home)} m445 {o['still']} m448 {o['i']} "
                        f"m446 {o['d']} m488 {o['ph']} speedF {o['sp']:.2f} chemin {o['path']} "
                        f"pos ({o['pos'][0]:.0f}, {o['pos'][1]:.0f}, {o['pos'][2]:.0f})")
    raw = mem.try_read(link_base + LINK_PUSH, 4) if _ptr(link_base) else None
    push = "?" if raw is None else ("oui" if struct.unpack(">I", raw)[0] & LINK_PUSH_BIT else "non")
    grab = MobileSync._grab(mem, link_base)
    tail = f"Link : pousse {push}, objet tenu (+0x65A4) {grab:08X}"
    if grab:
        hd = mem.try_read(grab, BOMB_FUNC + 4)
        if hd is not None and len(hd) >= BOMB_FUNC + 4:
            proc = struct.unpack_from(">h", hd, OFF_PROC)[0]
            if proc == PROC_BOMB:
                tail += f" = bombe du sac (294), mFunc +0x3B0 {struct.unpack_from('>I', hd, BOMB_FUNC)[0]:08X}" \
                        f" (explosion attendue : {BOMB_EXPLODE_FN:08X})"
            elif proc == PROC_BOMB2:
                st = mem.try_read(grab + B2_STATE, 4)
                tail += f" = Bomb2 (295), mState {struct.unpack('>i', st)[0] if st else '?'}"
    return rows + [tail]


def block_attr_cached(o: dict) -> tuple[int, int, float]:
    """Durées et taille de case d'un bloc relu (rangées dans le relevé par le client)."""
    a = o.get("attr")
    return a if a else (FRAMES_DEFAULT, FRAMES_DEFAULT, CELL_DEFAULT)
