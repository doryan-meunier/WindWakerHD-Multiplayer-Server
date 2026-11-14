"""Connaissance du jeu : symboles Wind Waker HD (USA) et structures du moteur.

Chaque constante porte sa provenance :
  [RANDO]  adresse/offset tiré de TWWHD-Randomizer (SuperDude88, MIT), qui patche
           le binaire HD USA et s'en sert en production -> fiable, mais pas
           encore re-vérifié par nous en direct.
  [DECOMP] offset tiré de la décomp GameCube zeldaret/tww (vérifié dans les
           headers). Valable sur HD seulement si la structure n'a pas changé.
  [DÉDUIT] offset GameCube corrigé d'un décalage observé dans le randomizer.
  [HYPO]   pure hypothèse, à valider avec `wwhd.py calibrate`.
"""
from __future__ import annotations

import json
import math
import os
import struct
from dataclasses import dataclass, field

from jeu.cemu import MEM2_END, MEM2_START, GuestMemory

# --------------------------------------------------------------------------
# Symboles HD USA  [RANDO] asm/linker.ld de TWWHD-Randomizer
# --------------------------------------------------------------------------
SYM_GAMEINFO_PTR = 0x101F84DC     # u32 -> g_dComIfG_gameInfo (dComIfG_inf_c, alloué)
SYM_STAY_NO = 0x1047E6C8          # u8  dStage_roomControl_c::mStayNo (salle courante)
SYM_FPCLF_METHOD = 0x101F3BDC     # g_fpcLf_Method  (méthodes "leaf" -> tous les acteurs)
SYM_FOPAC_METHOD = 0x101F3088     # g_fopAc_Method  (méthodes acteur)
SYM_FOPACM_DELETE = 0x025DE1B8    # code : fopAcM_delete, sert de test de version

# [LIVE] vérifié en direct dans Cemu (USA v0, 2026-11-07). Sur HD,
# dComIfG_play_c est une globale statique (pas dans le bloc gameInfo alloué).
SYM_CUR_STAGE = 0x104741E4        # dStage_startStage_c : char[8] nom, s16 point, s8 salle, s8 layer
SYM_NEXT_STAGE = 0x104741F0       # dStage_nextStage_c (même début)
SYM_PLAYER = 0x10474BDC           # mPlayerInfo[0].mpPlayer -> daPy_lk_c (Link)
SYM_PLAYER_PTRS = 0x10474BE4      # mpPlayerPtr[3] : Link, partenaire, bateau
SYM_ACTOR_NAMES = 0x101EBFDC      # table nom de stage -> procName : {char[8], s16, s8, pad} × 809
SYM_FOPACM_CREATE = 0x025D5834    # désassemblé depuis fopAcM_createItem (proc ITEM = 0xFF)

# --------------------------------------------------------------------------
# dComIfG_inf_c sur HD
# --------------------------------------------------------------------------
# [RANDO] les commentaires du randomizer disent "mCurInfo (0x20) + mSave (0x0) + ...":
# sur HD, dSv_info_c commence à gameInfo+0x20 et garde la disposition GC.
GI_SAVE = 0x20
# [DECOMP] offsets dans dSv_info_c / dSv_save_c (confirmés par les offsets
# utilisés dans le randomizer : 0x3A0, 0x644, 0x798, 0x1178 = GC + 0x20)
SV_MAX_LIFE = 0x000               # u16, en quarts de cœur ([RANDO] lhz 0x20 = "max health")
SV_LIFE = 0x002                   # u16
SV_RUPEE = 0x004                  # u16
SV_MAX_MAGIC = 0x013              # u8 ([RANDO] gameInfo+0x33)
SV_MAGIC = 0x014                  # u8
SV_RETURN_PLACE = 0x030           # char[8] nom de stage + s8 salle + u8 point (dernier point de sauvegarde)
SV_MEMORY = 0x380                 # dSv_memory_c[16], 0x24 chacun (switches par stage)
SV_EVENT = 0x624                  # u8[0x100] flags d'événement
SV_CUR_MEMORY = 0x778             # dSv_memory_c de la stage chargée
SV_ZONE = 0x7A8                   # dSv_zone_c[32]
SV_TMP = 0x1158                   # flags temporaires
SV_SIZE = 0x12A0
# dComIfG_play_c : sur GC à gameInfo+0x12A0, avec mCurStage à +0x3E94 et
# mPlayerInfo/mpPlayerPtr à +0x48A4/+0x48AC.  [HYPO] sur HD : dispo différente,
# on retrouve ces offsets par recherche (calibrate) et on les mémorise.

# --------------------------------------------------------------------------
# Acteurs
# --------------------------------------------------------------------------
# base_process_class [DECOMP] (supposé inchangé sur HD)
BP_PC_ID = 0x04                   # u32 identifiant de process
BP_PROC_NAME = 0x08               # s16 PROC_xxx
BP_PROF = 0x10                    # process_profile_definition*
BP_PC_MTD = 0xA8                  # = &g_fpcLf_Method pour tout acteur
BP_PARAMS = 0xB0                  # u32 mParameters (paramètres de spawn) [DECOMP, valeurs plausibles en LIVE]
LF_DRAW_MTD = 0xB8                # leafdraw_class::mpDrawMtd = &g_fopAc_Method
# process_profile_definition [DECOMP]
PROF_PROC_NAME = 0x08             # s16
PROF_SIZE = 0x10                  # u32 taille de l'instance

# fopAc_ac_c : offsets GC [DECOMP]. Sur HD tout ce qui suit tevStr (0x10C)
# est décalé de +0x11C [DÉDUIT] : le randomizer lit home.roomNo à 0x2FE et
# current.roomNo à 0x326 sur un acteur HD, contre 0x1E2 / 0x20A sur GC.
HD_SHIFT = 0x11C
_GC_FIELDS = {
    "set_id": 0x1BC, "group": 0x1BE, "argument": 0x1C1, "status": 0x1C4,
    "home": 0x1D0, "old": 0x1E4, "current": 0x1F8, "shape_angle": 0x20C,
    "scale": 0x214, "speed": 0x220, "speedF": 0x254, "gravity": 0x258,
    "eye": 0x260, "max_health": 0x284, "health": 0x285, "end": 0x290,
}
# actor_place [DECOMP] : cXyz pos (0x0), csXyz angle (0xC), s8 roomNo (0x12)
PLACE_POS, PLACE_ANGLE, PLACE_ROOM = 0x0, 0xC, 0x12

GROUPS = {0: "ACTOR", 1: "PLAYER", 2: "ENEMY", 3: "ENV", 4: "NPC"}  # fopAc_Group_e [DECOMP]


def default_actor_layout() -> dict[str, int]:
    return {k: v + HD_SHIFT for k, v in _GC_FIELDS.items()}


# Noms de stages connus (internes, ceux de la version GC). Non exhaustif.
KNOWN_STAGES = [
    "sea", "LinkRM", "Ojhous", "Ojhous2", "Omasao", "Onobuta", "Orichh", "A_mori",
    "Abesso", "Atorizk", "Adanmae", "Pjavdou", "MajyuE", "Majroom", "M_NewD2",
    "M_Dra09", "kindan", "Siren", "M_Dai", "kaze", "GanonK", "GTower", "Hyrule",
    "Hyroom", "Asoko", "Obombh", "Kaisen", "Ekaze", "Edaichi", "kenroom", "ADMumi",
    "Comori", "Otkura", "Pnezumi", "Kaenshi", "Pdrgsh", "Xboss0", "Xboss1",
    "Xboss2", "Xboss3", "sea_T", "Name", "ma2room", "ma3room", "majroom",
]

# --------------------------------------------------------------------------
# Config persistée (résultats de calibrate)
# --------------------------------------------------------------------------
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "wwhd_config.json")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------
def in_mem2(p: int) -> bool:
    return MEM2_START <= p < MEM2_END


CODECAVE_START, CODECAVE_END = 0x01800000, 0x02000000   # profils de nos acteurs custom


def is_profile_ptr(p: int) -> bool:
    return in_mem2(p) or CODECAVE_START <= p < CODECAVE_END


def version_check(mem: GuestMemory) -> tuple[bool, int | None]:
    """Le début de fopAcM_delete doit ressembler à un prologue PowerPC."""
    raw = mem.try_read(SYM_FOPACM_DELETE, 4)
    if raw is None:
        return False, None
    w = struct.unpack(">I", raw)[0]
    ok = (w >> 16) == 0x9421 or w == 0x7C0802A6   # stwu r1,-x(r1) / mflr r0
    return ok, w


def gameinfo_ptr(mem: GuestMemory) -> int | None:
    raw = mem.try_read(SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return None
    p = struct.unpack(">I", raw)[0]
    return p if in_mem2(p) else None


@dataclass
class SaveStatus:
    max_life: int
    life: int
    rupees: int
    magic: int
    max_magic: int
    return_stage: str
    room: int


def read_status(mem: GuestMemory, gi: int) -> SaveStatus:
    s = gi + GI_SAVE
    raw = mem.read(s, 0x40)
    max_life, life, rupee = struct.unpack_from(">HHH", raw, 0)
    max_magic, magic = raw[SV_MAX_MAGIC], raw[SV_MAGIC]
    ret = raw[SV_RETURN_PLACE:SV_RETURN_PLACE + 8].split(b"\0", 1)[0].decode("ascii", "replace")
    room = mem.s8(SYM_STAY_NO)
    return SaveStatus(max_life, life, rupee, magic, max_magic, ret, room)


@dataclass
class Actor:
    base: int
    proc_id: int
    proc_name: int
    prof_size: int
    group: int
    pos: tuple[float, float, float]
    angle: tuple[int, int, int]
    shape_angle: tuple[int, int, int]
    room: int
    home_room: int
    speedF: float
    health: int
    max_health: int
    prof_ok: bool = True
    params: int = 0                                   # base_process_class::mParameters (0xB0)
    home_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def group_name(self) -> str:
        return GROUPS.get(self.group, f"?{self.group}")

    def dist(self, p) -> float:
        return math.dist(self.pos, p)


def _parse_actor(mem: GuestMemory, base: int, lay: dict, prof_ok: bool) -> Actor | None:
    raw = mem.try_read(base, lay["end"])
    if raw is None:
        return None
    if struct.unpack_from(">I", raw, 0)[0] == 0:
        return None   # [LIVE] acteur supprimé : le jeu remet mBsType à 0, la mémoire libérée garde le reste
    prof, = struct.unpack_from(">I", raw, BP_PROF)
    prof_size = 0
    if is_profile_ptr(prof):
        r = mem.try_read(prof + PROF_SIZE, 4)
        if r:
            prof_size = struct.unpack(">I", r)[0]
    return _actor_de_brut(raw, base, lay, prof_ok, prof_size)


def _actor_de_brut(raw: bytes, base: int, lay: dict, prof_ok: bool, prof_size: int) -> Actor:
    """Acteur décodé de ses octets (``lay["end"]`` octets lus à ``base``) et de la taille de son profil."""
    pc_id, = struct.unpack_from(">I", raw, BP_PC_ID)
    proc_name, = struct.unpack_from(">h", raw, BP_PROC_NAME)
    cur, home = lay["current"], lay["home"]
    pos = struct.unpack_from(">3f", raw, cur + PLACE_POS)
    angle = struct.unpack_from(">3h", raw, cur + PLACE_ANGLE)
    room = struct.unpack_from(">b", raw, cur + PLACE_ROOM)[0]
    home_room = struct.unpack_from(">b", raw, home + PLACE_ROOM)[0]
    shape = struct.unpack_from(">3h", raw, lay["shape_angle"])
    speedF, = struct.unpack_from(">f", raw, lay["speedF"])
    group = raw[lay["group"]]
    max_hp, hp = struct.unpack_from(">bb", raw, lay["max_health"])
    params, = struct.unpack_from(">I", raw, BP_PARAMS)
    home_pos = struct.unpack_from(">3f", raw, home + PLACE_POS)
    return Actor(base, pc_id, proc_name, prof_size, group, pos, angle, shape, room,
                 home_room, speedF, hp, max_hp, prof_ok, params, home_pos)


def _plausible(a: Actor) -> bool:
    return all(math.isfinite(v) and abs(v) < 1e6 for v in a.pos)


class ActorScanner:
    """Trouve les acteurs chargés par leur signature de méthodes.

    Tout acteur (fopAc_ac_c) a  base+0xA8 = &g_fpcLf_Method  et
    base+0xB8 = &g_fopAc_Method  ([DECOMP] fpcBs_Create / fpcLf_Create, symboles [RANDO]).
    On cherche ces deux mots dans MEM2 au lieu de parcourir les listes de process
    (dont les adresses HD ne sont pas encore connues). Les profils statiques
    contiennent la même paire : on les élimine en vérifiant que
    base->mpProf->mProcName == base->mProcName.
    """

    def __init__(self, mem: GuestMemory, layout: dict | None = None):
        self.mem = mem
        self.layout = layout or default_actor_layout()
        self.bases: list[int] = []
        self.unvalidated = False
        self.delta_found: int | None = None
        self._window: tuple[int, int] | None = None
        self._prof_tailles: dict[int, int] = {}     # mode léger (Linux) : taille de chaque profil (statique)

    def _leger(self) -> bool:
        """Lectures groupées (linmem en mode léger, --linux-leger) ? Jamais sous Windows."""
        return getattr(self.mem, "leger", False) is True

    def rescan(self, full: bool = False) -> list[int]:
        lo, hi = (MEM2_START, MEM2_END) if (full or self._window is None) else self._window
        lf = struct.pack(">I", SYM_FPCLF_METHOD)
        ac = struct.pack(">I", SYM_FOPAC_METHOD)
        lf_hits, ac_hits = set(), []
        for va, data in self.mem.iter_chunks(lo, hi):
            for needle, sink in ((lf, None), (ac, ac_hits)):
                i = data.find(needle)
                while i != -1:
                    if (va + i) % 4 == 0:
                        (lf_hits.add(va + i) if sink is None else sink.append(va + i))
                    i = data.find(needle, i + 1)
        # écart entre les deux pointeurs : 0x10 sur GC, mesuré ici
        hist: dict[int, int] = {}
        for h in ac_hits:
            for d in range(4, 0x40, 4):
                if h - d in lf_hits:
                    hist[d] = hist.get(d, 0) + 1
        if not hist:
            self.bases = []
            return []
        # Les profils statiques ont la paire à 0x10 ; les vrais acteurs HD à 0x14
        # (vérifié en direct : leafdraw_class HD a un mot de type en plus avant
        # mpDrawMtd). On valide chaque écart et on garde le meilleur.
        best_good, best_rest, best_delta = [], [], None
        for delta in sorted(hist, key=hist.get, reverse=True):
            cands = [h - delta - BP_PC_MTD for h in ac_hits if h - delta in lf_hits]
            good, rest = [], []
            if self._leger():
                good, rest = self._valider_lot(cands)
            for b in ([] if self._leger() else cands):
                raw = self.mem.try_read(b, 0x14)
                if raw is None:
                    continue
                name, = struct.unpack_from(">h", raw, BP_PROC_NAME)
                prof, = struct.unpack_from(">I", raw, BP_PROF)
                pn = self.mem.try_read(prof + PROF_PROC_NAME, 2) if is_profile_ptr(prof) else None
                if name != 0 and prof != b and pn is not None and struct.unpack(">h", pn)[0] == name:
                    good.append(b)
                else:
                    rest.append(b)
            if best_delta is None or len(good) > len(best_good):
                best_good, best_rest, best_delta = good, rest, delta
        good, rest = best_good, best_rest
        self.delta_found = best_delta
        self.unvalidated = not good
        self.bases = sorted(good or rest)
        if self.bases:
            margin = 32 << 20
            self._window = (max(MEM2_START, self.bases[0] - margin),
                            min(MEM2_END, self.bases[-1] + margin))
        return self.bases

    def _valider_lot(self, cands: list[int]) -> tuple[list[int], list[int]]:
        """Validation des candidats du rescan (même règle que la boucle d'avant) en deux lectures groupées."""
        good, rest = [], []
        raws = self.mem.read_lot([(b, 0x14) for b in cands])
        vus = [(b, raw) for b, raw in zip(cands, raws) if raw is not None]
        profs = [struct.unpack_from(">I", raw, BP_PROF)[0] for _b, raw in vus]
        a_lire = [p for p in profs if is_profile_ptr(p)]
        noms = dict(zip(a_lire, self.mem.read_lot([(p + PROF_PROC_NAME, 2) for p in a_lire])))
        for (b, raw), prof in zip(vus, profs):
            name, = struct.unpack_from(">h", raw, BP_PROC_NAME)
            pn = noms.get(prof) if is_profile_ptr(prof) else None
            if name != 0 and prof != b and pn is not None and struct.unpack(">h", pn)[0] == name:
                good.append(b)
            else:
                rest.append(b)
        return good, rest

    def _read_all_lot(self) -> list[Actor]:
        """read_all en mode léger : tous les acteurs en UNE lecture groupée ; tailles de profil gardées (profils
        statiques du jeu : elles ne changent pas), les nouvelles lues en une deuxième lecture groupée."""
        lay = self.layout
        prof_ok = not self.unvalidated
        raws = self.mem.read_lot([(b, lay["end"]) for b in self.bases])
        vivants = [(b, raw) for b, raw in zip(self.bases, raws)
                   if raw is not None and struct.unpack_from(">I", raw, 0)[0] != 0]
        profs = [struct.unpack_from(">I", raw, BP_PROF)[0] for _b, raw in vivants]
        neufs = sorted({p for p in profs if is_profile_ptr(p) and p not in self._prof_tailles})
        if neufs:
            for p, r in zip(neufs, self.mem.read_lot([(p + PROF_SIZE, 4) for p in neufs])):
                if r:
                    self._prof_tailles[p] = struct.unpack(">I", r)[0]
        out = []
        for (b, raw), p in zip(vivants, profs):
            a = _actor_de_brut(raw, b, lay, prof_ok, self._prof_tailles.get(p, 0) if is_profile_ptr(p) else 0)
            if _plausible(a):
                out.append(a)
        return out

    def read_all(self) -> list[Actor]:
        if self._leger():
            return self._read_all_lot()
        out = []
        for b in self.bases:
            a = _parse_actor(self.mem, b, self.layout, not self.unvalidated)
            if a is not None and _plausible(a):
                out.append(a)
        return out


def player_ptrs(mem: GuestMemory) -> tuple[int, int, int]:
    """(Link, partenaire, bateau) lus dans mpPlayerPtr ; 0 si absent."""
    raw = mem.try_read(SYM_PLAYER_PTRS, 12)
    if raw is None:
        return (0, 0, 0)
    return tuple(p if in_mem2(p) else 0 for p in struct.unpack(">3I", raw))


def read_actor(mem: GuestMemory, base: int, layout: dict | None = None) -> Actor | None:
    a = _parse_actor(mem, base, layout or default_actor_layout(), True)
    return a if a is not None and _plausible(a) else None


def pick_link(actors: list[Actor], cfg: dict, mem: GuestMemory | None = None) -> Actor | None:
    """Link = acteur pointé par mPlayerInfo[0].mpPlayer [LIVE]. Repli : acteur
    du groupe PLAYER avec la plus grosse instance."""
    if mem is not None:
        raw = mem.try_read(SYM_PLAYER, 4)
        p = struct.unpack(">I", raw)[0] if raw else 0
        for a in actors:
            if a.base == p:
                return a
    pn = cfg.get("link_proc_name")
    if pn is not None:
        for a in actors:
            if a.proc_name == pn:
                return a
    players = [a for a in actors if a.group == 1]
    pool = players or actors
    if not pool:
        return None
    best = max(pool, key=lambda a: a.prof_size)
    return best if (players or best.prof_size > 0x4000) else None


@dataclass
class StageInfo:
    name: str
    point: int
    room: int
    layer: int


def read_stage(mem: GuestMemory, addr: int = SYM_CUR_STAGE) -> StageInfo | None:
    raw = mem.try_read(addr, 12)
    if raw is None:
        return None
    name = raw[:8].split(b"\0", 1)[0].decode("ascii", "replace")
    point, room, layer = struct.unpack_from(">hbb", raw, 8)
    return StageInfo(name, point, room, layer)


CUSTOM_ACTORS = {"WwmpLink": (0x1F0, 3),    # nom: (proc, version minimale du graphic pack)
                 "WwmpModel": (0x1F1, 9)}   # acteur générique : modèles donnés par le PC (bateau)


def read_actor_names(mem: GuestMemory) -> dict[str, tuple[int, int]]:
    """Table interne des noms d'acteurs [LIVE] : {nom: (procName, argument)},
    plus nos acteurs custom (graphic pack WWMP)."""
    raw = mem.read(SYM_ACTOR_NAMES, 0xC * 900)
    out: dict[str, tuple[int, int]] = {}
    for i in range(0, len(raw), 0xC):
        nm = raw[i:i + 8].split(b"\0", 1)[0]
        if not nm or not all(32 < c < 127 for c in nm):
            break
        proc, arg = struct.unpack_from(">hb", raw, i + 8)
        out.setdefault(nm.decode("ascii"), (proc, arg))
    for nm, (proc, _v) in CUSTOM_ACTORS.items():
        out[nm] = (proc, -1)
    return out


def read_gameinfo_blob(mem: GuestMemory, gi: int, size: int = 0x40000) -> bytes:
    while size >= 0x2000:
        raw = mem.try_read(gi, size)
        if raw is not None:
            return raw
        size //= 2
    return b""


def find_stage_name_offsets(blob: bytes) -> list[tuple[int, str]]:
    hits = []
    for name in KNOWN_STAGES:
        needle = name.encode() + b"\0"
        i = blob.find(needle)
        while i != -1:
            if i % 4 == 0 and (i == 0 or blob[i - 1] == 0 or i % 8 == 0):
                hits.append((i, name))
            i = blob.find(needle, i + 1)
    hits.sort()
    return hits


def find_pointer_offsets(blob: bytes, target: int) -> list[int]:
    needle = struct.pack(">I", target)
    out, i = [], blob.find(needle)
    while i != -1:
        if i % 4 == 0:
            out.append(i)
        i = blob.find(needle, i + 1)
    return out
