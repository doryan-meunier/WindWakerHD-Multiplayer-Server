"""Progression partagée (étape 8) : lecture, différences, fusion et application des bits
de sauvegarde (flags d'événement, switches / coffres / objets par stage…).

Principe :
  * la progression est un ensemble de **bits** rangés par « clé » (une zone de la
    sauvegarde) puis par octet : ``Bits = {clé: {offset: masque}}`` ;
  * on ne partage que les bits passés de 0 à 1 ; on n'efface **jamais** un bit ;
  * la fusion est un OU logique : commutative, idempotente, insensible à l'ordre
    d'arrivée des messages et aux doublons (deux joueurs qui ouvrent le même coffre) ;
  * l'application écrit octet par octet, seulement les octets qui changent, après
    une relecture immédiate (fenêtre de course avec le jeu de quelques µs).

Clés (offsets depuis ``g_dComIfG_gameInfo``, voir docs/dev/MEMORY_MAP.md §3) :
  ``ev``             flags d'événement ``mSave.mEvent`` (gi+0x644, 0x100 octets)
  ``mem<i>``         ``mSave.mMemory[i]`` (gi+0x3A0 + i*0x24) = ``dSv_memBit_c`` de la
                     stage de sauvegarde i ; pour la stage chargée, on lit la copie
                     vivante seule et on écrit dans les deux : copie vivante ``mMemory``
                     (gi+0x798), que le jeu recopie dans ``mSave.mMemory[i]`` en
                     quittant la stage
  ``dan<i>``         switches de donjon temporaires ``mDan`` (gi+0x7BC), seulement si
                     ``mDan.mStageNo == i`` (désactivé par défaut)
  ``zone:<st>:<r>``  bits de la zone de la salle r de la stage st (``mZone[]``, gi+0x7C8),
                     seulement si on est dans cette stage (désactivé par défaut)

Ce qui n'est PAS partagé (exprès) : compteurs (clés de donjon ``mKeyNum``, rubis,
munitions), inventaire et objets obtenus (``dSv_player_*``), ``mReserve``, ``mTmp``
(flags temporaires), registres d'événement (octets ``ev`` qui ne sont pas des bits :
voir ``DEFAULT_ALLOW``), carte marine / îles (``dSv_ocean_c``).

Ce module ne dépend que de l'interface ``u8/u32/s8/read/write/try_read`` de la
mémoire : ``FakeGuest`` (plus bas) sert aux tests et au client ``--fake``.
"""
from __future__ import annotations

import re
import struct
import threading
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Adresses (voir docs/dev/MEMORY_MAP.md)
# ---------------------------------------------------------------------------
SYM_GAMEINFO_PTR = 0x101F84DC     # [RANDO + LIVE]
SYM_CUR_STAGE = 0x104741E4        # dStage_startStage_c [LIVE]
SYM_NEXT_STAGE_ENABLED = 0x104741FC   # dStage_nextStage_c::enabled (s8) [DECOMP + LIVE : 0 au repos]
SYM_PLAYER = 0x10474BDC           # mPlayerInfo[0].mpPlayer [LIVE]
SYM_KEY_PENDING = 0x10474C0C      # s16 dComIfG_play_c::mItemKeyNumCount (play+0x5B5C) [HD-ASM + LIVE]
SYM_PLAY = 0x1046F0B0             # dComIfGp (play), renvoyé par 0x025200D4 [HD-ASM + LIVE]
SYM_PLAY_ITEMS = SYM_PLAY + 0x5B40   # compteurs « en attente » et caches de boutons (sync2_inventaire §2.2)
PLAY_ITEMS_SIZE = 0x90
SYM_EVT_CTRL = 0x10474280         # dEvt_control_c (play.mEvtCtrl) [DÉDUIT + LIVE, voir MEMORY_MAP]
# Champs de dEvt_control_c [DECOMP d_event.h ; LIVE 2026-11-08 au repos dans M_NewD2 : ordres 0,
# mode 0, fin 0, mPt1/mPt2/mPtT = ids d'acteurs PÉRIMÉS du dernier coffre, mEventId -1]
EVT_ORDER_COUNT = 0xC0            # s8 mOrderCount : remis à 0 en fin de frame (instable à la lecture)
EVT_MODE = 0xC2                   # u8 mMode : != 0 pendant un événement (runCheck)
EVT_END_PROC = 0xC3               # u8 mbEndProc : != 0 pendant la fin d'un événement
EVT_PT = 0xC4                     # u32 mPt1, mPt2, mPtT, mPtI : ids de process, JAMAIS remis à -1
EVT_EVENT_ID = 0xD8               # s16 mEventId : pas remis à -1 en mode COMPULSORY (diagnostic seul)
EVT_FREE_DELAY = 0.5              # s de lectures « libre » consécutives avant de déclarer le jeu libre

GI_EVENT = 0x644                  # u8[0x100]              [RANDO + LIVE]
GI_MEMORY = 0x3A0                 # dSv_memBit_c[16]       [RANDO + LIVE]
GI_CUR_MEMORY = 0x798             # dSv_memBit_c vivant    [RANDO + LIVE]
GI_DAN = 0x7BC                    # dSv_danBit_c           [DECOMP + LIVE]
GI_ZONE = 0x7C8                   # dSv_zone_c[32]         [DECOMP + LIVE]
EVENT_SIZE = 0x100
MEM_SIZE, N_MEM = 0x24, 16
DAN_STAGE_NO, DAN_SWITCH, DAN_SWITCH_SIZE = 0x0, 0x4, 0x8
ZONE_SIZE, N_ZONES, ZONE_BITS, ZONE_BITS_SIZE = 0x4C, 32, 0x2, 0x4A

# dSv_memBit_c [DECOMP + LIVE] : champs -> plages d'octets
MEM_FIELDS = {
    "tbox": range(0x00, 0x04),        # coffres ouverts
    "switch": range(0x04, 0x14),      # switches sauvegardés (0x00..0x7F)
    "item": range(0x14, 0x18),        # objets ramassés une seule fois (quarts de cœur…)
    "visited": range(0x18, 0x20),     # salles visitées (carte du donjon)
    "keynum": range(0x20, 0x21),      # compteur de petites clés : JAMAIS en OU
    "dungeon": range(0x21, 0x22),     # carte, boussole, grande clé, boss vaincu… (bits)
}
# dSv_zoneBit_c + dSv_zoneActor_c [DECOMP d_save.h] : plages d'octets RELATIVES à ZONE_BITS
# (zone+0x2). Champs u16/u32 big-endian : le switch 0xC0 est l'octet 0x01 masque 0x01, le
# switch 0xE0 l'octet 0x05 masque 0x01 [LIVE : zone de la salle 0 de M_NewD2 = 0000 0000 0001 0000].
# clearRoomSwitch() efface seulement mSwitch[2] (0xE0-0xEF) à chaque changement de salle
# [DECOMP d_save.cpp]. Aucune de ces règles n'est dans le défaut : l'union « OU pour toujours »
# ne convient pas à ces bits temporaires (relais dédié prévu : SYNCHRO.md A7).
ZONE_FIELDS = {
    "sw": range(0x00, 0x04),          # mSwitch[0..1] : switches 0xC0-0xDF
    "room": range(0x04, 0x06),        # mSwitch[2]    : switches 0xE0-0xEF (effacés à chaque salle)
    "item": range(0x06, 0x08),        # mItem         : objets de zone ramassés
    "actor": range(0x0A, 0x4A),       # mActorFlags[16] : ennemis tués (0x08-0x09 = bourrage)
}
NEVER = {("mem", 0x20)}               # compteurs : refusés même si une règle les autorise

TITLE_STAGES = {"", "sea_T", "Name"}  # [HYPO] écran titre / choix du fichier

# Règles par défaut : ce qu'on sait être des bits de progression durables.
#   ev 0x00-0x5F : les octets non nuls observés en direct sont tous dans 0x00-0x35 et
#                  ressemblent à des bits ; 0x7E (=6) et 0xBE (=0x14) ressemblent à des
#                  registres (compteurs) : tout ce qui est au-dessus de 0x5F est exclu.
DEFAULT_ALLOW = ("ev:0x00-0x5F", "mem:tbox", "mem:switch", "mem:item", "mem:visited", "mem:dungeon",
                 # randomizer par fichiers, lot 6 (rando/lot6.py) : bits d'événement INUTILISÉS par le jeu
                 # d'origine, que le code repris de TWWHD pose pour suivre un lieu (« déjà donné / acheté ») : 0x6902
                 # étal à 20 rubis de Beedle, 0x6940 Zunari, 0x6A01 Maggie, 0x6A02 facteur du café. Déclarés un par
                 # un (jamais une plage) : partagés, le lieu ne donne qu'une fois pour le groupe. Sans effet sans le
                 # pack généré (personne ne les pose).
                 "ev:0x69/0x42", "ev:0x6A/0x03",
                 # randomizer, étape 5 (rando/lot9.py) : 0x6B01, bit INUTILISÉ par le jeu d'origine que le client
                 # écrit dans le champ « déjà gagné » de l'enchère à 100 rubis quand son objet change (option
                 # lieux_encheres_100) : partagé, l'enchère ne se gagne qu'une fois pour le groupe
                 "ev:0x6B/0x01")
# Synchro v2 (docs/etudes/sync2_inventaire.md R10) : ev 0x3D bits 0x80/0x40/0x20/0x10 = « souvenir
# du boss copié » (combats Xboss0-3). Le jeu copie l'instantané mStatusC puis pose le bit, seulement
# si le bit est FAUX [HD-ASM 0x0259188C-0x0259192C : li r4, 0x3D80 à 0x02591904]. Reçu d'un autre
# joueur, le bit empêcherait la copie chez nous : instantané vide au combat de souvenir. Refusé
# jusqu'au relais dédié de mStatusC.
SOUVENIRS_DENY = "ev:0x3D/0xF0"
DEFAULT_DENY: tuple[str, ...] = (SOUVENIRS_DENY,)

# A1 : préréglage « coop donjon » (--coop-donjon). Refuse ce qui ferait PERDRE un objet au
# second joueur tant que l'objet lui-même n'est pas donné (pack v18, SYNCHRO.md B-d) :
#   mem:tbox          un coffre ouvert chez l'un serait déjà ouvert (et vide) chez l'autre :
#                     le coffre ne lit son bit qu'à sa création [DECOMP d_a_tbox.cpp] ;
#                     Grappin, petites clés, grande clé, carte, boussole…
#   mem:dungeon/0x13  bits 0-1 carte, boussole ; bit 4 réceptacle de cœur du boss
#                     (STAGE_LIFE) [DECOMP d_save.h dSv_memBit_c] ; le bit 2 (GRANDE CLÉ, synchro v2 :
#                     jamais consommée, la porte du boss teste seulement isDungeonItem(2),
#                     docs/etudes/sync2_cles.md §2.6), le bit 3 (boss vaincu) et le bit 5
#                     (cinématique du boss vue) sont partagés
#   ev:0x3D/0xF0      souvenirs des boss (voir SOUVENIRS_DENY), aussi avec un fichier de règles
#   mem:item          objets uniques ramassés (quart de cœur placé, rubis…) : le bit seul
#                     ferait disparaître l'objet chez l'autre sans le lui donner
# Synchro v2, inventaire partagé (synchro/inventaire.py, sync2_inventaire.md §5 étapes 4 bis et 8) : mem:tbox
# et mem:item RESTENT refusés. Les conditions des études ne sont pas réunies : l'inventaire phase 1 ne
# porte aucun compteur (rubis, quarts de cœur, munitions, butin), donc un bit de coffre ou d'objet au sol
# posé par l'union OU ferait encore perdre ces objets ; et mem:tbox doit passer par le canal « coffre »
# (CHESTS_DENY ci-dessous), qui pose le bit seulement quand l'objet est arrivé (inventaire ou GRANT v19).
COOP_DONJON_DENY = ("mem:tbox", "mem:dungeon/0x13", "mem:item", SOUVENIRS_DENY)
COOP_DONJON_SETTLE = 2.0          # s de stabilité après un changement de stage / rechargement
# Synchro v2, coffres (--share-chests, synchro/coffres.py, docs/etudes/sync2_coffres.md §3.6) : GARDE-FOU.
# Le canal « coffre » pose lui-même les bits mTbox et les bits carte / boussole / réceptacle de
# mDungeonItem, AVEC l'objet (ou « bit seul » quand l'effet est reproduit autrement). Si l'union OU
# de la progression les posait aussi, elle les poserait AVANT le GRANT (pack v19), dont le test
# « déjà posé » ne donnerait alors jamais l'objet : perte d'objet. Ces refus s'ajoutent donc quelles
# que soient les règles (fichier --progress-rules, --progress-allow), comme le correctif A1.
# Le bit 2 (grande clé) reste partagé par la progression (décision du module « clés »,
# sync2_cles.md §2.6 : sans objet à donner, le bit EST l'effet) ; le canal le pose aussi, sans conflit.
CHESTS_DENY = ("mem:tbox", "mem:dungeon/0x13")


def chest_guard(deny) -> list:
    """Règles refusées + garde-fou des coffres partagés (``CHESTS_DENY``), sans doublon."""
    deny = list(deny)
    return deny + [r for r in CHESTS_DENY if r not in deny]


def tbox_byte(n: int) -> tuple[int, int]:
    """Coffre n (0..31) -> (offset dans dSv_memBit_c, masque) : mTbox u32 BE (onTbox 0x025B8C0C)."""
    if not 0 <= n < 32:
        raise ValueError(f"numéro de coffre hors bornes : {n}")
    return 3 - n // 8, 1 << (n % 8)


def dungeon_byte(b: int) -> tuple[int, int]:
    """Bit b (0..7) de mDungeonItem (+0x21) : 0 carte, 1 boussole, 2 grande clé, 4 réceptacle."""
    if not 0 <= b < 8:
        raise ValueError(f"bit de donjon hors bornes : {b}")
    return MEM_FIELDS["dungeon"].start, 1 << b


GI_OCEAN = 0x5E0                  # mSave.mOcean : u16 BE [50] (repêchages) [DECOMP + cohérence LIVE, HYPO]


def ocean_byte(grid: int, bit: int) -> tuple[int, int]:
    """Point de repêchage (case grid 0..49, bit 0..15) -> (offset depuis gi, masque). Phase 2."""
    if not (0 <= grid < 50 and 0 <= bit < 16):
        raise ValueError(f"repêchage hors bornes : {grid}, {bit}")
    return GI_OCEAN + grid * 2 + (1 - bit // 8), 1 << (bit % 8)

Bits = dict  # {clé: {offset: masque}}

KEY_RE = re.compile(r"^(ev|mem\d{1,2}|dan\d{1,2}|zone:[A-Za-z0-9_]{1,8}:\d{1,3})$")


# ---------------------------------------------------------------------------
# Opérations sur les ensembles de bits
# ---------------------------------------------------------------------------
def bits_or(a: Bits, b: Bits) -> Bits:
    """a | b (nouvel objet)."""
    out = {k: dict(v) for k, v in a.items()}
    for k, offs in b.items():
        d = out.setdefault(k, {})
        for o, m in offs.items():
            d[o] = d.get(o, 0) | m
    return out


def bits_update(a: Bits, b: Bits) -> None:
    """a |= b (sur place)."""
    for k, offs in b.items():
        d = a.setdefault(k, {})
        for o, m in offs.items():
            d[o] = d.get(o, 0) | m


def bits_and(a: Bits, b: Bits) -> Bits:
    out: Bits = {}
    for k, offs in a.items():
        ob = b.get(k)
        if not ob:
            continue
        for o, m in offs.items():
            r = m & ob.get(o, 0)
            if r:
                out.setdefault(k, {})[o] = r
    return out


def bits_sub(a: Bits, b: Bits) -> Bits:
    """a & ~b : bits de a absents de b."""
    out: Bits = {}
    for k, offs in a.items():
        ob = b.get(k, {})
        for o, m in offs.items():
            r = m & ~ob.get(o, 0) & 0xFF
            if r:
                out.setdefault(k, {})[o] = r
    return out


def bits_count(a: Bits) -> int:
    return sum(bin(m).count("1") for offs in a.values() for m in offs.values())


def bits_from_bytes(key: str, data: bytes) -> Bits:
    offs = {i: b for i, b in enumerate(data) if b}
    return {key: offs} if offs else {}


def to_wire(a: Bits) -> dict:
    """Format JSON : {clé: [[offset, masque], ...]} (les clés JSON doivent être des chaînes)."""
    return {k: sorted([o, m] for o, m in offs.items() if m) for k, offs in a.items() if any(offs.values())}


def from_wire(obj, max_keys: int = 512) -> Bits:
    """Décode et VALIDE un ensemble reçu du réseau (entrées invalides ignorées)."""
    out: Bits = {}
    if not isinstance(obj, dict):
        return out
    for k, pairs in list(obj.items())[:max_keys]:
        if not isinstance(k, str) or not KEY_RE.match(k) or not isinstance(pairs, list):
            continue
        for p in pairs[:0x200]:
            if (isinstance(p, list) and len(p) == 2 and all(isinstance(x, int) for x in p)
                    and 0 <= p[0] < 0x200 and 0 < p[1] <= 0xFF):
                d = out.setdefault(k, {})
                d[p[0]] = d.get(p[0], 0) | p[1]
    return out


def describe(a: Bits, limit: int = 6) -> str:
    """Résumé lisible, ex. « ev[0x24]=0x80, mem3[0x00]=0x01 »."""
    items = [f"{k}[0x{o:02X}]=0x{m:02X}" for k in sorted(a) for o, m in sorted(a[k].items())]
    more = f" (+{len(items) - limit})" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


# ---------------------------------------------------------------------------
# Règles (liste blanche / liste noire)
# ---------------------------------------------------------------------------
@dataclass
class Rule:
    cat: str                 # ev | mem | dan | zone
    slot: int | None         # mem3 -> 3 ; None = tous
    offsets: range | None    # None = tous
    mask: int = 0xFF

    def matches(self, key: str, off: int) -> int:
        cat, slot = split_key(key)
        if cat != self.cat or (self.slot is not None and slot != self.slot):
            return 0
        if self.offsets is not None and off not in self.offsets:
            return 0
        return self.mask


def split_key(key: str) -> tuple[str, int | None]:
    if key.startswith("zone:"):
        return "zone", None
    m = re.match(r"^([a-z]+)(\d*)$", key)
    if not m:
        return key, None
    return m.group(1), int(m.group(2)) if m.group(2) else None


def parse_rule(spec: str) -> Rule:
    """Syntaxe : ``<cat>[<slot>][:<champ>|<off>|<off>-<off>][/<masque>]``.

    Exemples : ``ev:0x00-0x5F``, ``ev:0x24/0x80``, ``mem:tbox``, ``mem3:switch``,
    ``mem:0x04-0x13``, ``dan``, ``zone``, ``zone:sw``, ``zone:room``, ``zone:item``,
    ``zone:actor`` (champs de zone : voir ``ZONE_FIELDS``).
    """
    s = spec.strip()
    mask = 0xFF
    if "/" in s:
        s, ms = s.rsplit("/", 1)
        mask = int(ms, 0) & 0xFF
    part, _, rng = s.partition(":")
    m = re.match(r"^(ev|mem|dan|zone)(\d*)$", part)
    if not m:
        raise ValueError(f"règle de progression invalide : {spec!r}")
    cat, slot = m.group(1), (int(m.group(2)) if m.group(2) else None)
    offsets: range | None = None
    if rng:
        if cat == "mem" and rng in MEM_FIELDS:
            offsets = MEM_FIELDS[rng]
        elif cat == "zone" and rng in ZONE_FIELDS:
            offsets = ZONE_FIELDS[rng]
        elif "-" in rng:
            a, b = rng.split("-", 1)
            offsets = range(int(a, 0), int(b, 0) + 1)
        else:
            offsets = range(int(rng, 0), int(rng, 0) + 1)
    return Rule(cat, slot, offsets, mask)


class Rules:
    """Un bit est partagé s'il est autorisé par une règle ``allow`` et par aucune ``deny``."""

    def __init__(self, allow=DEFAULT_ALLOW, deny=DEFAULT_DENY):
        self.allow = [parse_rule(s) for s in allow]
        self.deny = [parse_rule(s) for s in deny]
        self.allow_src, self.deny_src = list(allow), list(deny)

    @classmethod
    def from_json(cls, obj: dict) -> "Rules":
        """{"allow": [...], "deny": [...]} ; "allow" absent = règles par défaut."""
        return cls(obj.get("allow", DEFAULT_ALLOW), obj.get("deny", DEFAULT_DENY))

    def mask(self, key: str, off: int) -> int:
        cat, _ = split_key(key)
        if (cat, off) in NEVER:
            return 0
        m = 0
        for r in self.allow:
            m |= r.matches(key, off)
        for r in self.deny:
            m &= ~r.matches(key, off) & 0xFF
        return m

    def filter(self, a: Bits) -> Bits:
        out: Bits = {}
        for k, offs in a.items():
            for o, v in offs.items():
                r = v & self.mask(k, o)
                if r:
                    out.setdefault(k, {})[o] = r
        return out

    def __str__(self) -> str:
        return "autorisé " + ", ".join(self.allow_src) + (" ; refusé " + ", ".join(self.deny_src) if self.deny_src else "")


# ---------------------------------------------------------------------------
# Lecture du jeu
# ---------------------------------------------------------------------------
@dataclass
class View:
    ok: bool
    reason: str = ""
    gi: int = 0
    stage: str = ""
    room: int = -1
    cur_idx: int | None = None        # mDan.mStageNo = index de sauvegarde de la stage chargée
    zones: dict[int, int] = field(default_factory=dict)   # roomNo -> index de zone
    busy: str = ""                    # raison de ne pas appliquer ("" = libre)
    bits: Bits = field(default_factory=dict)

    @property
    def context(self) -> tuple:
        return (self.gi, self.stage, self.cur_idx)


def _u32(mem, va: int) -> int:
    return struct.unpack(">I", mem.read(va, 4))[0]


@dataclass
class EventState:
    """Champs utiles de dEvt_control_c (lecture brute, sans anti-rebond)."""
    order_count: int
    mode: int
    end_proc: int
    event_id: int

    @property
    def busy(self) -> bool:
        # Correction de la critique (SYNCHRO_critique.md §5) : seuls mMode et mbEndProc font
        # foi. mPt1..mPtI et mEventId restent posés après l'événement ; mOrderCount clignote
        # en cours de frame près d'une porte, d'un coffre ou d'un PNJ.
        return self.mode != 0 or self.end_proc != 0

    def diag(self) -> str:
        return f"mode={self.mode}, fin={self.end_proc}, id={self.event_id}, ordres={self.order_count}"


def read_event(mem) -> EventState | None:
    raw = mem.try_read(SYM_EVT_CTRL + EVT_ORDER_COUNT, EVT_EVENT_ID + 2 - EVT_ORDER_COUNT)
    if raw is None:
        return None
    return EventState(struct.unpack_from(">b", raw, 0)[0], raw[EVT_MODE - EVT_ORDER_COUNT],
                      raw[EVT_END_PROC - EVT_ORDER_COUNT],
                      struct.unpack_from(">h", raw, EVT_EVENT_ID - EVT_ORDER_COUNT)[0])


def event_busy(mem) -> str:
    """Cinématique / dialogue / coffre / porte en cours ? (lecture brute, sans anti-rebond).
    « » = libre. Occupé si mMode != 0 ou mbEndProc != 0 [DECOMP runCheck ; LIVE au repos :
    les deux à 0 alors que mPt1/mPt2 gardent les ids du dernier coffre]."""
    ev = read_event(mem)
    if ev is None:
        return "événement illisible"
    if ev.busy:
        return f"événement en cours ({ev.diag()})"
    return ""


class EventWatch:
    """Anti-rebond de ``event_busy`` : occupé dès une lecture occupée, libre seulement après
    ``free_delay`` s de lectures libres consécutives (fin d'événement, frames de transition)."""

    def __init__(self, free_delay: float = EVT_FREE_DELAY, clock=time.monotonic):
        self.free_delay, self.clock = free_delay, clock
        self.last_busy = float("-inf")
        self.reason = ""

    def feed(self, raw: str) -> str:
        """``raw`` = résultat brut d'``event_busy`` ; renvoie la raison anti-rebondie."""
        now = self.clock()
        if raw:
            self.last_busy, self.reason = now, raw
            return raw
        if now - self.last_busy < self.free_delay:
            return f"fin d'événement il y a moins de {self.free_delay:g} s"
        return ""

    def poll(self, mem) -> str:
        return self.feed(event_busy(mem))


def read_view(mem, with_dan: bool = True, with_zones: bool = True) -> View:
    """Instantané de toute la progression lisible (sans filtre de règles)."""
    raw = mem.try_read(SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return View(False, "gameInfo illisible")
    gi = struct.unpack(">I", raw)[0]
    if not 0x10000000 <= gi < 0x50000000:
        return View(False, "pas de sauvegarde chargée")
    st = mem.try_read(SYM_CUR_STAGE, 12)
    if st is None:
        return View(False, "stage illisible")
    stage = st[:8].split(b"\0", 1)[0].decode("ascii", "replace")
    room = struct.unpack_from(">b", st, 10)[0]
    if stage in TITLE_STAGES:
        return View(False, f"écran titre ({stage or 'vide'})", gi, stage)
    blob = mem.try_read(gi + GI_MEMORY, GI_ZONE + ZONE_SIZE * N_ZONES - GI_MEMORY)
    player = mem.try_read(SYM_PLAYER, 4)
    if blob is None or player is None:
        return View(False, "sauvegarde illisible", gi, stage)
    if struct.unpack(">I", player)[0] == 0:
        return View(False, "Link absent (chargement)", gi, stage)

    def at(off: int, n: int) -> bytes:
        return blob[off - GI_MEMORY: off - GI_MEMORY + n]

    v = View(True, "", gi, stage, room)
    dan_no = struct.unpack(">b", at(GI_DAN + DAN_STAGE_NO, 1))[0]
    v.cur_idx = dan_no if 0 <= dan_no < N_MEM else None
    cur = at(GI_CUR_MEMORY, MEM_SIZE)
    bits: Bits = {}
    bits.update(bits_from_bytes("ev", at(GI_EVENT, EVENT_SIZE)))
    for i in range(N_MEM):
        # stage chargée : la copie vivante seule fait foi. La copie de sauvegarde est périmée
        # jusqu'à la sortie de la stage ; l'utiliser en OU masquerait un bit que le jeu
        # efface (offSwitch agit sur la copie vivante) et on le réimposerait après coup.
        data = cur if i == v.cur_idx else at(GI_MEMORY + i * MEM_SIZE, MEM_SIZE)
        bits.update(bits_from_bytes(f"mem{i}", data))
    if with_dan and v.cur_idx is not None:
        bits.update(bits_from_bytes(f"dan{v.cur_idx}", at(GI_DAN + DAN_SWITCH, DAN_SWITCH_SIZE)))
    for z in range(N_ZONES):
        zr = struct.unpack(">b", at(GI_ZONE + z * ZONE_SIZE, 1))[0]
        if zr >= 0 and zr not in v.zones:
            v.zones[zr] = z
            if with_zones:
                bits.update(bits_from_bytes(f"zone:{stage}:{zr}",
                                            at(GI_ZONE + z * ZONE_SIZE + ZONE_BITS, ZONE_BITS_SIZE)))
    v.bits = bits
    # contrôle de cohérence : la copie vivante contient forcément les coffres et salles
    # visitées de la copie de sauvegarde (ces bits ne redescendent jamais)
    if v.cur_idx is not None:
        saved = at(GI_MEMORY + v.cur_idx * MEM_SIZE, MEM_SIZE)
        for r in (MEM_FIELDS["tbox"], MEM_FIELDS["visited"]):
            if any(saved[i] & ~cur[i] for i in r):
                v.ok, v.reason = False, "copie vivante incohérente (chargement de stage ?)"
                break
    nxt = mem.try_read(SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        v.busy = "changement de stage en cours"
    else:
        v.busy = event_busy(mem)
    return v


def addresses(view: View, key: str, off: int) -> list[int]:
    """Adresses invitées où écrire l'octet (clé, offset) ; [] si pas applicable maintenant."""
    gi = view.gi
    cat, slot = split_key(key)
    if cat == "ev" and 0 <= off < EVENT_SIZE:
        return [gi + GI_EVENT + off]
    if cat == "mem" and slot is not None and 0 <= slot < N_MEM and 0 <= off < MEM_SIZE:
        out = [gi + GI_MEMORY + slot * MEM_SIZE + off]
        if slot == view.cur_idx:
            out.append(gi + GI_CUR_MEMORY + off)
        return out
    if cat == "dan" and slot is not None and slot == view.cur_idx and 0 <= off < DAN_SWITCH_SIZE:
        return [gi + GI_DAN + DAN_SWITCH + off]
    if cat == "zone":
        _, st, room = key.split(":")
        z = view.zones.get(int(room))
        if st == view.stage and z is not None and 0 <= off < ZONE_BITS_SIZE:
            return [gi + GI_ZONE + z * ZONE_SIZE + ZONE_BITS + off]
    return []


def apply_bits(mem, view: View, bits: Bits) -> Bits:
    """Met à 1 les bits demandés (jamais de 0). Relit chaque octet juste avant de l'écrire
    et n'écrit que s'il change. Renvoie les bits effectivement posés."""
    done: Bits = {}
    for key, offs in bits.items():
        for off, m in sorted(offs.items()):
            addrs = addresses(view, key, off)
            for va in addrs:
                _rmw(mem, va, m)          # même verrou que le relais A7 (mêmes octets de zone)
            if addrs:
                done.setdefault(key, {})[off] = done.get(key, {}).get(off, 0) | m
    return done


# ---------------------------------------------------------------------------
# Synchronisation (logique pure, testable sans Cemu)
# ---------------------------------------------------------------------------
class ProgressSync:
    """État local de la progression partagée.

    À chaque ``tick`` :
      1. lit la vue ; tant que le contexte (sauvegarde, stage, index de stage) n'est pas
         stable depuis ``settle`` s, ne fait rien ;
      2. bits passés de 1 à 0 alors que tout est stable : le jeu les a effacés exprès.
         Clés ``mem``/``dan``/``zone`` : on ne les réimposera plus (``suppressed``).
         Clé ``ev`` : l'octet entier est un registre ou un flag temporaire, il n'est plus
         partagé (``volatile``). Beaucoup de bits effacés d'un coup = sauvegarde rechargée :
         on repart de zéro ;
      3. bits locaux présents dans deux lectures stables de suite, autorisés par les
         règles, jamais envoyés : à envoyer (``to_send``) ;
      4. si le jeu est libre (pas de cinématique ni de changement de stage) : pose les
         bits distants qui manquent (``remote`` - local - effacés exprès).
    """

    RELOAD_THRESHOLD = 8   # bits effacés d'un coup au-delà desquels on suppose un rechargement

    def __init__(self, rules: Rules | None = None, settle: float = 2.0, apply: bool = True,
                 clock=time.monotonic, free_delay: float = EVT_FREE_DELAY):
        self.rules = rules or Rules()
        self.settle, self.apply_enabled, self.clock = settle, apply, clock
        self.events = EventWatch(free_delay, clock)   # A0 : événement avec anti-rebond
        self.remote: Bits = {}           # union reçue du serveur
        self.sent: Bits = {}             # déjà envoyé
        self.suppressed: Bits = {}       # effacés exprès par le jeu local
        self.volatile: set[int] = set()  # octets "ev" non monotones
        self.prev: Bits | None = None
        self.ctx = None
        self.since = 0.0
        self.status = "pas encore lu"
        self.n_sent = self.n_applied = self.n_received = 0
        self.last_applied: Bits = {}
        self.reloads = 0
        self.view_ctx = None             # (gi, stage, index de sauvegarde) de la dernière lecture (A5)
        self.tot_tick: int | None = None # bits partageables confirmés à ce tick (None : pas jugé ce tick)

    # --- réseau ---------------------------------------------------------------
    def receive(self, bits: Bits) -> int:
        """Bits distants (déjà validés). Renvoie le nombre de bits nouveaux."""
        new = bits_sub(bits, self.remote)
        if new:
            self.remote = bits_or(self.remote, new)   # nouvel objet : lu par un autre thread
        n = bits_count(new)
        self.n_received += n
        return n

    def reset_network(self) -> None:
        """A15 (``progress {reset: 1}`` après un « rejoindre » réussi) : le serveur a vidé
        l'union du groupe. On oublie tout l'état réseau (``remote``, ``sent``) et l'historique
        local (``suppressed``, ``prev``) : au prochain tick stable, TOUT l'état partageable est
        renvoyé, et l'union se reforme avec ce que les parties ont vraiment. ``volatile`` est
        une propriété du jeu (octets registres) : gardé."""
        self.remote, self.sent, self.suppressed, self.last_applied = {}, {}, {}, {}
        self.prev = None
        self.since = self.clock()
        self.status = "progression remise à zéro par le serveur (rejoindre) : renvoi complet"

    def oublier_reseau(self) -> None:
        """Serveur redémarré (autre époque dans son welcome) : son union est peut-être incomplète. On oublie ce
        qu'on croit lui avoir envoyé et ce qu'on a reçu (``sent``, ``remote``) : au prochain tick stable, tout
        l'état partageable repart. L'historique du jeu local (``suppressed``, ``prev``) est gardé : un bit effacé
        exprès par le jeu n'est pas réimposé."""
        self.remote, self.sent = {}, {}

    def shareable(self, a: Bits) -> Bits:
        out = self.rules.filter(a)
        ev = out.get("ev")
        if ev and self.volatile:
            for o in list(ev):
                if o in self.volatile:
                    del ev[o]
            if not ev:
                del out["ev"]
        return out

    # --- tick -------------------------------------------------------------------
    def tick(self, mem) -> tuple[Bits, Bits]:
        """Renvoie (bits à envoyer, bits appliqués localement)."""
        now = self.clock()
        self.tot_tick = None
        try:
            view = read_view(mem, with_dan=self._wants("dan"), with_zones=self._wants("zone"))
        except Exception as e:  # mémoire illisible pendant un chargement
            view = View(False, f"lecture impossible : {e}")
        if not view.ok:
            self.prev, self.ctx = None, None
            self.status = view.reason
            return {}, {}
        if view.context != self.ctx:
            self.ctx, self.since, self.prev = view.context, now, None
        self.view_ctx = view.context
        if not view.busy.startswith("changement de stage"):
            view.busy = self.events.feed(view.busy)
        if view.busy.startswith("changement de stage"):
            # recopies mMemory <-> mSave.mMemory en cours : un 1 -> 0 vu maintenant ne veut
            # rien dire, on ne compare pas ([HYPO] : enabled reste à 1 pendant les recopies)
            self.prev, self.since = None, now
            self.status = view.busy
            return {}, {}
        if self.prev is not None:
            cleared = bits_sub(self.prev, view.bits)
            if bits_count(cleared) >= self.RELOAD_THRESHOLD:
                # sauvegarde rechargée (ou écran titre) : on oublie l'historique local
                self.reloads += 1
                self.suppressed, self.since, self.prev = {}, now, None
                self.status = "sauvegarde rechargée : attente"
                return {}, {}
            for k, offs in cleared.items():
                if k == "ev":
                    self.volatile.update(offs)
                else:
                    bits_update(self.suppressed, {k: offs})
        if now - self.since < self.settle:
            self.prev = view.bits
            self.status = "attente de stabilité"
            return {}, {}
        to_send: Bits = {}
        if self.prev is not None:
            confirmed = bits_and(view.bits, self.prev)
            partageable = self.shareable(confirmed)
            self.tot_tick = bits_count(partageable)
            to_send = bits_sub(bits_sub(partageable, self.sent), self.remote)
            if to_send:
                bits_update(self.sent, to_send)
                self.n_sent += bits_count(to_send)
        applied: Bits = {}
        if self.apply_enabled:
            need = bits_sub(bits_sub(self.shareable(self.remote), view.bits), self.suppressed)
            if need and view.busy:
                self.status = f"{bits_count(need)} bit(s) en attente : {view.busy}"
            elif need:
                applied = apply_bits(mem, view, need)
                self.n_applied += bits_count(applied)
                if applied:
                    self.last_applied = applied
                # les bits posés font partie de la vue locale à partir de maintenant
                view.bits = bits_or(view.bits, applied)
                waiting = bits_count(need) - bits_count(applied)
                self.status = "à jour" + (f", {waiting} bit(s) pour une autre stage/salle" if waiting else "")
            else:
                self.status = "à jour"
        else:
            self.status = "à jour (application désactivée)"
        self.prev = view.bits
        return to_send, applied

    def _wants(self, cat: str) -> bool:
        return any(r.cat == cat for r in self.rules.allow)

    def summary(self) -> str:
        return (f"{self.n_sent} bit(s) envoyé(s), {self.n_received} reçu(s), {self.n_applied} posé(s) ; "
                f"{self.status}" + (f" ; octets ev volatils {sorted(self.volatile)}" if self.volatile else ""))


# ---------------------------------------------------------------------------
# A7 : relais des switches temporaires (tsw) et des ennemis tués (mActorFlags)
# ---------------------------------------------------------------------------
# Numérotation des switches [DECOMP d_save.cpp dSv_info_c::onSwitch] :
#   0x80-0xBF  mDan.mSwitch[2] (u32 BE, gi+0x7BC+4) : toute la visite du donjon (même stage)
#   0xC0-0xDF  mZone[z].mSwitch[0..1] (u16 BE, zone+2) : vie de la zone de la salle
#   0xE0-0xEF  mZone[z].mSwitch[2] : « switches de salle », effacés à CHAQUE changement de salle
#              pour toutes les zones chargées (zoneCountCheck -> clearRoomSwitch)
# Ennemis tués : mZone[z].mActorFlags[16] (u32 BE, zone+0xC), bit = setID de l'acteur.
#   fopAcM_onActor(a) = dComIfGs_onActor(a->setID, a->home.roomNo) [DECOMP f_op_actor_mng.h] ;
#   dStage_actorInit ne recrée pas un acteur dont le bit est posé [DECOMP d_stage.cpp].
#   HD [ASM, copie lecture seule du code] : dSv_info_c::onActor = 0x025BA5D4, appelé 43 fois
#   avec « lhz r4, 0x2D8(acteur) » et « lbz r5, 0x2FE(acteur) » : setID = +0x2D8 (u16),
#   home.roomNo = +0x2FE. Il refuse setID -1 / 0xFFFF (acteurs sans numéro : la plupart des
#   acteurs et les ennemis apparus en cours de jeu). [LIVE] zone de la salle 0 de M_NewD2 :
#   mActorFlags[0] = 3 après deux Bokoblins tués (setID 0 et 1, valeurs des Bokoblins [HYPO] :
#   ils étaient déjà morts lors de la lecture).
TSW_DAN_FIRST, TSW_ZONE_FIRST, TSW_ROOM_FIRST, TSW_END = 0x80, 0xC0, 0xE0, 0xF0
# Numéros de sauvegarde (mDan.mStageNo) des donjons : 2 Forteresse Maudite, 3 Caverne du Dragon
# [LIVE : M_NewD2 = 3], 4 Bois Défendus, 5 Tour des Dieux, 6 Temple de la Terre, 7 Temple du
# Vent, 8 Tour de Ganon [HYPO : table des stages du jeu, seul 3 relu en direct]. Par défaut, A7
# ne relaie que dans ces stages : en mer et sur les îles, deux joueurs à des stades différents
# de l'histoire peuvent être sur des « layers » différents (mêmes setID, autres acteurs).
TSW_DUNGEON_SAVE_NOS = frozenset(range(2, 9))
ZONE_SWITCH_SIZE = 0x6            # mSwitch[3], relatif à ZONE_BITS
ZONE_ACTOR, ZONE_ACTOR_SIZE, ACTOR_MAX = 0x0C, 0x40, 512   # relatifs au début de la zone
ACTOR_SET_ID = 0x2D8              # u16 fopAc_ac_c::setID (GC 0x1BC + 0x11C) [ASM HD]
ACTOR_HOME_ROOM = 0x2FE           # s8 home.roomNo [RANDO + ASM HD]
HD_ON_ACTOR = 0x025BA5D4          # dSv_info_c::onActor(int, int) [ASM HD] (documentation seule)


def tsw_byte(sw: int) -> tuple[str, int, int]:
    """Switch 0x80-0xEF -> (« dan » | « zone », octet, masque). Octet relatif à gi+GI_DAN pour
    « dan », au début de la zone (gi+GI_ZONE+z*ZONE_SIZE) pour « zone »."""
    if TSW_DAN_FIRST <= sw < TSW_ZONE_FIRST:
        k = sw - TSW_DAN_FIRST
        return "dan", DAN_SWITCH + 4 * (k >> 5) + 3 - ((k & 31) >> 3), 1 << (k & 7)
    if TSW_ZONE_FIRST <= sw < TSW_END:
        k = sw - TSW_ZONE_FIRST
        return "zone", ZONE_BITS + 2 * (k >> 4) + 1 - ((k & 15) >> 3), 1 << (k & 7)
    raise ValueError(f"switch temporaire hors plage : 0x{sw:X}")


def actor_byte(set_id: int) -> tuple[int, int]:
    """setID 0-511 -> (octet relatif au début de la zone, masque)."""
    if not 0 <= set_id < ACTOR_MAX:
        raise ValueError(f"setID hors plage : {set_id}")
    return ZONE_ACTOR + 4 * (set_id >> 5) + 3 - ((set_id & 31) >> 3), 1 << (set_id & 7)


def _dan_switches(data: bytes) -> set[int]:
    out = set()
    for b, v in enumerate(data):
        for j in range(8):
            if v >> j & 1:
                out.add(TSW_DAN_FIRST + (b // 4) * 32 + (3 - b % 4) * 8 + j)
    return out


def _zone_switches(data: bytes) -> set[int]:
    out = set()
    for b, v in enumerate(data):
        for j in range(8):
            if v >> j & 1:
                out.add(TSW_ZONE_FIRST + (b // 2) * 16 + (1 - b % 2) * 8 + j)
    return out


def _actor_ids(data: bytes) -> set[int]:
    out = set()
    for b, v in enumerate(data):
        for j in range(8):
            if v >> j & 1:
                out.add((b // 4) * 32 + (3 - b % 4) * 8 + j)
    return out


@dataclass
class TempView:
    """Switches temporaires et ennemis tués lus dans le jeu. Salle -1 = switches de donjon."""
    ok: bool
    reason: str = ""
    gi: int = 0
    stage: str = ""
    room: int = -1
    dan_ok: bool = False                                   # mDan.mStageNo valide
    dan_no: int = -1                                       # mDan.mStageNo (numéro de sauvegarde)
    player: int = 0                                        # pointeur de Link (change au rechargement)
    zones: dict[int, int] = field(default_factory=dict)    # roomNo -> index de zone
    on: set = field(default_factory=set)                   # {(salle, switch)} à 1
    acts: set = field(default_factory=set)                 # {(salle, setID)} tués
    busy: str = ""

    def alive(self, room: int) -> bool:
        return self.dan_ok if room == -1 else room in self.zones


def read_temp(mem) -> TempView:
    """Lecture seule : mDan et les 32 zones (une seule lecture du bloc)."""
    raw = mem.try_read(SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return TempView(False, "gameInfo illisible")
    gi = struct.unpack(">I", raw)[0]
    if not 0x10000000 <= gi < 0x50000000:
        return TempView(False, "pas de sauvegarde chargée")
    st = mem.try_read(SYM_CUR_STAGE, 12)
    if st is None:
        return TempView(False, "stage illisible")
    stage = st[:8].split(b"\0", 1)[0].decode("ascii", "replace")
    room = struct.unpack_from(">b", st, 10)[0]
    if stage in TITLE_STAGES:
        return TempView(False, f"écran titre ({stage or 'vide'})", gi, stage)
    blob = mem.try_read(gi + GI_DAN, GI_ZONE + ZONE_SIZE * N_ZONES - GI_DAN)
    player = mem.try_read(SYM_PLAYER, 4)
    if blob is None or player is None:
        return TempView(False, "sauvegarde illisible", gi, stage)
    link = struct.unpack(">I", player)[0]
    if link == 0:
        return TempView(False, "Link absent (chargement)", gi, stage)
    v = TempView(True, "", gi, stage, room)
    v.player = link
    v.dan_no = dan_no = struct.unpack_from(">b", blob, DAN_STAGE_NO)[0]
    v.dan_ok = 0 <= dan_no < N_MEM
    if v.dan_ok:
        v.on.update((-1, sw) for sw in _dan_switches(blob[DAN_SWITCH:DAN_SWITCH + DAN_SWITCH_SIZE]))
    for z in range(N_ZONES):
        o = GI_ZONE - GI_DAN + z * ZONE_SIZE
        zr = struct.unpack_from(">b", blob, o)[0]
        if zr < 0 or zr in v.zones:
            continue
        v.zones[zr] = z
        v.on.update((zr, sw) for sw in _zone_switches(blob[o + ZONE_BITS:o + ZONE_BITS + ZONE_SWITCH_SIZE]))
        v.acts.update((zr, i) for i in _actor_ids(blob[o + ZONE_ACTOR:o + ZONE_ACTOR + ZONE_ACTOR_SIZE]))
    nxt = mem.try_read(SYM_NEXT_STAGE_ENABLED, 1)
    v.busy = "changement de stage en cours" if nxt is None or nxt[0] else event_busy(mem)
    return v


_write_lock = threading.Lock()   # écritures octet par octet depuis plusieurs threads du client


def _rmw(mem, va: int, set_mask: int = 0, clear_mask: int = 0) -> bool:
    """Relit l'octet juste avant d'écrire ; n'écrit que s'il change. True = écrit.

    Limite connue : le verrou ne protège que des threads du client, pas du jeu. Si le jeu pose
    un AUTRE bit du même octet entre notre lecture et notre écriture (quelques µs), notre
    écriture l'efface. Seule une commande de la boîte aux lettres exécutée par le jeu lui-même
    (OU / ET-NON atomique, ou appel de onSwitch / onActor) supprimerait cette fenêtre : à faire
    dans un futur pack (SYNCHRO.md B1)."""
    with _write_lock:
        old = mem.read(va, 1)[0]
        new = (old | set_mask) & ~clear_mask & 0xFF
        if new == old:
            return False
        mem.write(va, bytes([new]))
        return True


def _zone_base(mem, gi: int, room: int) -> int | None:
    """Adresse de la zone de la salle `room` si elle est chargée (relue à l'instant)."""
    if not 0 <= room < 64:
        return None
    raw = mem.try_read(gi + GI_ZONE, ZONE_SIZE * N_ZONES)
    if raw is None:
        return None
    for z in range(N_ZONES):
        if struct.unpack_from(">b", raw, z * ZONE_SIZE)[0] == room:
            return gi + GI_ZONE + z * ZONE_SIZE
    return None


def write_temp_switch(mem, gi: int, room: int, sw: int, val: int) -> bool | None:
    """Pose (val=1) ou efface (val=0) un switch temporaire. None si la zone (ou mDan) est
    absente ; sinon True si l'octet a changé."""
    kind, off, mask = tsw_byte(sw)
    if kind == "dan":
        raw = mem.try_read(gi + GI_DAN + DAN_STAGE_NO, 1)
        if raw is None or not 0 <= struct.unpack(">b", raw)[0] < N_MEM:
            return None
        va = gi + GI_DAN + off
    else:
        base = _zone_base(mem, gi, room)
        if base is None:
            return None
        va = base + off
    return _rmw(mem, va, mask if val else 0, 0 if val else mask)


def mark_actor_dead(mem, set_id: int, room: int, gi: int | None = None) -> bool | None:
    """Équivalent Python de fopAcM_onActor : pose le bit `set_id` de la zone de `room`.
    None si rien à faire (setID absent, zone non chargée) ; True si l'octet a changé."""
    if set_id in (-1, 0xFFFF) or not 0 <= set_id < ACTOR_MAX or room is None or room < 0:
        return None
    if gi is None:
        raw = mem.try_read(SYM_GAMEINFO_PTR, 4)
        if raw is None:
            return None
        gi = struct.unpack(">I", raw)[0]
        if not 0x10000000 <= gi < 0x50000000:
            return None
    base = _zone_base(mem, gi, room)
    if base is None:
        return None
    off, mask = actor_byte(set_id)
    return _rmw(mem, base + off, mask)


def mark_actor_dead_from(mem, actor_base: int) -> tuple[int, int, bool | None]:
    """Lit setID (+0x2D8) et home.roomNo (+0x2FE) de l'acteur ENCORE VIVANT en mémoire, puis
    pose son bit « tué ». Renvoie (setID, salle, résultat de mark_actor_dead)."""
    set_id = struct.unpack(">H", mem.read(actor_base + ACTOR_SET_ID, 2))[0]
    room = struct.unpack(">b", mem.read(actor_base + ACTOR_HOME_ROOM, 1))[0]
    return set_id, room, mark_actor_dead(mem, set_id, room)


def sw_name(room: int, sw: int) -> str:
    return f"0x{sw:02X}" + ("" if room == -1 else f"@{room}")


class TempSwitchRelay:
    """A7 (--share-temp) : relais en direct des switches temporaires (donjon 0x80-0xBF, zone
    0xC0-0xEF), dans les deux sens (0->1 et 1->0), et des ennemis tués (mActorFlags, 0->1 seul).

    Par défaut, seulement dans les donjons (``TSW_DUNGEON_SAVE_NOS``, option ``dungeons_only``).

    À chaque ``tick`` (thread de travail du client) :
      1. lit mDan et les zones ; un changement de sauvegarde ou de stage, de numéro de donjon
         (mDan.mStageNo) ou de Link (salle rechargée, chute) repart de zéro : rien à relayer ;
      2. compare à la lecture précédente, seulement pour les zones présentes dans les deux
         lectures (une zone qui apparaît ou disparaît n'est pas un changement) :
           - switch passé à 0 ou à 1 -> à envoyer, et compté « sans écho » TOUT DE SUITE (une
             valeur distante plus ancienne arrivée avant l'envoi ne l'écrase pas) ;
           - switch de salle (0xE0-0xEF) passé à 0 : mis en attente ROOM_GRACE s et oublié si
             la salle locale (ou l'ensemble des zones) change dans la fenêtre [-1 s, +1 s]
             (clearRoomSwitch du jeu, à NE PAS relayer) ;
           - ennemi tué (bit 0->1) -> à envoyer (jamais de 1->0), sauf les bits posés par nos
             soins sans preuve venue du jeu (``quiet_act`` : ennemi orphelin, marionnette) ;
      3. pose les valeurs reçues si : même stage, zone de la salle chargée, pas d'événement en
         cours (A0), pas de changement de salle depuis ROOM_GRACE s, pas de changement local
         de ce switch encore sans écho. Nos propres écritures sont reportées dans la lecture de
         référence : jamais d'écho. Un ennemi tué reçu dont l'acteur vit peut-être encore chez
         nous (salle chargée avant l'arrivée du bit) devient une « cible » : le client le supprime.

    Anti-battement (``flap_guard``) : un switch que le jeu local défait juste après notre
    écriture (plaque de poids, torche minutée, switch recalculé par un acteur), ou qui change
    plus de FLAP_MAX fois en FLAP_WINDOW s, devient « local » pendant MUTE_TIME s : ni envoyé
    ni posé. Sans cela, les deux jeux se le renverraient sans fin.

    Anti-cycle : un switch qu'un acteur bascule de lui-même à intervalles fixes (jet de lave
    Yfire00 du Pic du Dragon : 0xE0@2 à 1 pendant ~7,4 s puis à 0 pendant ~8,5 s, réécrit à
    CHAQUE image par daObjFlame::set_switch) est « local » tant qu'il continue : CYCLE_N
    changements alternés dont les durées « à 1 » et « à 0 » se répètent à CYCLE_TOL s près.
    Le relayer ne sert à rien (l'acteur de l'autre jeu le réécrit à l'image suivante). Une
    torche minutée rallumée par un joueur a une durée « à 1 » fixe mais une durée « à 0 » qui
    dépend du joueur : pas reconnue comme cyclique (sauf rallumages réglés à la demi-seconde).

    Switches d'acteur (``set_owned``) : le client signale les switches qu'un acteur chargé
    réécrit lui-même à chaque image (jet de lave Yfire00 : swSave, voir synchro/obstacles.py) ;
    ils ne sont ni envoyés ni posés tant que l'acteur est chargé, dès le premier changement
    (l'anti-cycle ci-dessus reste le filet pour les acteurs non répertoriés).

    Ordre (« le dernier écrivain gagne ») : le serveur numérote chaque changement (seq) et le
    renvoie à TOUS les joueurs du stage, émetteur compris. Tant qu'un de nos changements n'est
    pas revenu du serveur, les valeurs des autres pour ce switch sont plus anciennes : ignorées.

    Attente : une valeur reçue reste en attente tant qu'on est dans le même stage (cinématique,
    zone pas encore chargée…), sauf un switch de salle (0xE0-0xEF) dont la zone est absente,
    oublié après PENDING_TTL s (le jeu les efface de toute façon). Le serveur garde l'état du
    stage (switches de donjon et de zone, ennemis tués) tant qu'un joueur du groupe y est, et
    l'envoie en entier (« snap ») à chaque changement de salle."""

    ROOM_GRACE = 1.0      # s autour d'un changement de salle local (clearRoomSwitch)
    PENDING_TTL = 20.0    # s : switch de salle reçu dont la zone n'est pas chargée -> oublié
    REVERT_WINDOW = 1.5   # s : le jeu défait notre écriture dans ce délai -> switch « local »
    FLAP_WINDOW, FLAP_MAX = 5.0, 4   # changements d'un même switch au plus dans la fenêtre
    MUTE_TIME = 30.0      # s pendant lesquels un switch qui bat n'est plus relayé
    REVERT_MUTE = 300.0   # s : switch réécrit par un acteur local (défait aussitôt) : switch de salle
    #                       (oublié quand sa zone part), ou switch de donjon défait une 2e fois ;
    #                       1re fois pour un switch de donjon (jamais oublié par la zone) : MUTE_TIME
    CYCLE_N = 7           # changements locaux alternés pour reconnaître un cycle (3 durées « à 1 », 3 « à 0 »)
    CYCLE_TOL = 0.45      # s : écart permis entre deux durées de même sens (tick ~5/s : ±0,4 s)
    CYCLE_MIN, CYCLE_MAX = 3.0, 60.0   # s : durée d'un état dans un cycle reconnu
    MAX_TARGETS = 1024

    def __init__(self, apply: bool = True, clock=time.monotonic, free_delay: float = EVT_FREE_DELAY,
                 dungeons_only: bool = True, flap_guard: bool = True):
        self.apply_enabled, self.clock = apply, clock
        self.dungeons_only, self.flap_guard = dungeons_only, flap_guard
        self.events = EventWatch(free_delay, clock)
        self.lock = threading.Lock()
        self.prev: TempView | None = None
        self.ctx = None
        self.stage = ""
        self.room_t = float("-inf")       # dernier changement de salle / de zones local
        self.held: dict[tuple, float] = {}        # (salle, sw) -> heure du 1->0 (switch de salle)
        self.pending: dict[tuple, tuple] = {}     # (stage, salle, sw) -> (val, heure de réception, seq)
        self.pending_act: dict[tuple, float] = {}  # (stage, salle, setID) -> heure
        self.seq_seen: dict[tuple, int] = {}      # (stage, salle, sw) -> dernier seq connu
        self.unacked: dict[tuple, int] = {}       # (stage, salle, sw) -> nos changements sans écho
        self.wrote: dict[tuple, tuple] = {}       # (salle, sw) -> (valeur posée par nous, heure)
        self.flaps: dict[tuple, list] = {}        # (salle, sw) -> heures des derniers changements
        self.muted: dict[tuple, float] = {}       # (salle, sw) -> fin du silence (anti-battement)
        self.cycles: dict[tuple, list] = {}       # (salle, sw) -> [(heure, val)] derniers changements locaux
        self.cyclic: dict[tuple, float] = {}      # (salle, sw) -> période mesurée (anti-cycle)
        self.owned: dict[tuple, str] = {}         # (salle, sw) -> acteur local qui le réécrit (set_owned)
        self.owned_noted: set = set()             # switches d'acteur déjà signalés au journal
        self.owned_drop_noted: set = set()        # valeurs reçues jetées (switch d'acteur) déjà signalées
        self.reverted: set = set()                # (-1, sw) de donjon déjà « défaits par ton jeu » (ce stage)
        self.n_owned = 0                          # changements locaux non relayés (switch d'acteur)
        self.quiet: set = set()                   # {(salle, setID)} posés par nous : pas relayés
        self.targets: dict[tuple, float] = {}     # {(salle, setID): heure} tués ailleurs, acteur peut-être vivant
        self.notes: list[str] = []                # messages pour le journal du client
        self.status = "pas encore lu"
        self.n_sent = self.n_received = self.n_applied = self.n_acts_sent = self.n_acts_applied = 0
        self.n_room_clears = 0                    # effacements de salle non relayés
        self.n_muted = 0

    # --- réseau (thread de la boucle asyncio) -------------------------------
    def reset_network(self) -> None:
        """Connexion perdue : les numéros du serveur repartent peut-être de zéro. Les changements
        encore en file chez le client sont recomptés par lui (mark_sent) après cet appel."""
        with self.lock:
            self.pending.clear()
            self.pending_act.clear()
            self.seq_seen.clear()
            self.unacked.clear()

    def mark_sent(self, stage: str, ch) -> None:
        """Compte des changements locaux comme « sans écho » (tick le fait déjà pour les siens ;
        sert après une reconnexion pour les messages restés en file)."""
        with self.lock:
            for room, sw, _val in ch:
                k = (stage, room, sw)
                self.unacked[k] = self.unacked.get(k, 0) + 1

    def receive(self, stage: str, ch, acts, from_me: bool, snap: bool = False) -> int:
        """ch = [[salle, sw, val, seq]], acts = [[salle, setID]] (déjà validés).
        Renvoie le nombre de valeurs retenues pour application."""
        now, n = self.clock(), 0
        with self.lock:
            for room, sw, val, seq in ch:
                k = (stage, room, sw)
                if from_me:
                    left = self.unacked.get(k, 0) - 1
                    if left > 0:
                        self.unacked[k] = left
                    else:
                        self.unacked.pop(k, None)
                    self.seq_seen[k] = max(seq, self.seq_seen.get(k, -1))
                    p = self.pending.get(k)
                    if p is not None and p[2] < seq:
                        del self.pending[k]   # valeur distante plus ancienne que la nôtre
                    continue
                if self.unacked.get(k):
                    continue                  # notre changement, plus récent, est en route
                last = self.seq_seen.get(k, -1)
                if seq < last or (seq == last and not snap):
                    continue
                self.seq_seen[k] = seq
                self.pending[k] = (val, now, seq)
                n += 1
            if not from_me:
                for room, i in acts:
                    k = (stage, room, i)
                    if k not in self.pending_act:
                        self.pending_act[k] = now
                        n += 1
            self.n_received += n
        return n

    # --- jeu (thread de travail) ----------------------------------------------
    def quiet_act(self, room: int, set_id: int) -> None:
        """Bit « tué » posé par le client sans que le jeu l'ait décidé (orphelin supprimé,
        marionnette morte chez l'autorité) : à ne pas relayer (l'autorité a son propre bit)."""
        if len(self.quiet) < 4096:
            self.quiet.add((room, set_id))

    def target_done(self, room: int, set_id: int) -> None:
        self.targets.pop((room, set_id), None)

    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-20:]

    def set_owned(self, owned: dict) -> None:
        """Switches réécrits à chaque image par un acteur chargé chez nous : {(salle, sw): nom}.
        Remplace la liste précédente (relevé des acteurs du client). Ni envoyés ni posés."""
        self.owned = {k: str(v) for k, v in (owned or {}).items()
                      if isinstance(k, tuple) and len(k) == 2 and TSW_DAN_FIRST <= k[1] < TSW_END}

    def _owned(self, room: int, sw: int) -> bool:
        k = (room, sw)
        if k not in self.owned:
            return False
        if k not in self.owned_noted and len(self.owned_noted) < 256:
            self.owned_noted.add(k)
            self._note(f"switch {sw_name(room, sw)} : réécrit sans cesse par un acteur de ton jeu "
                       f"({self.owned[k]}) ; jamais relayé")
        return True

    def _cycle(self, k: tuple, val: int, t: float) -> float | None:
        """Historique des changements locaux (gardé pendant le silence). Renvoie la période si
        les CYCLE_N derniers changements alternent avec des durées régulières, sinon None."""
        h = [x for x in self.cycles.get(k, []) if t - x[0] < self.CYCLE_N * self.CYCLE_MAX]
        h = (h + [(t, val)])[-self.CYCLE_N:]
        self.cycles[k] = h
        if len(h) < self.CYCLE_N or any(a[1] == b[1] for a, b in zip(h, h[1:])):
            return None
        d = [b[0] - a[0] for a, b in zip(h, h[1:])]
        if max(d) > self.CYCLE_MAX or min(d) < self.CYCLE_MIN or any(abs(d[i] - d[i - 2]) > self.CYCLE_TOL for i in range(2, len(d))):
            return None
        return d[-1] + d[-2]

    def _local_change(self, room: int, sw: int, val: int, now: float, t: float | None = None) -> bool:
        """Anti-battement : True si ce changement local peut partir. `t` : heure réelle du
        changement (un 1->0 de salle est relevé ROOM_GRACE s plus tard)."""
        k = (room, sw)
        if self._owned(room, sw):
            self.n_owned += 1
            return False
        if not self.flap_guard:
            return True
        period = self._cycle(k, val, now if t is None else t)
        if period is not None:
            if k not in self.cyclic:
                self.n_muted += 1
                self._note(f"switch {sw_name(room, sw)} : bascule tout seul toutes les {period:.1f} s "
                           f"(acteur du jeu) ; plus relayé tant qu'il continue")
            self.cyclic[k] = period
            self.muted[k] = max(self.muted.get(k, now), now + max(self.MUTE_TIME, 2 * period))
            self.flaps.pop(k, None)
            return False
        if k in self.cyclic and not self._muted(room, sw, now):
            del self.cyclic[k]                  # arrêté depuis 2 périodes : de nouveau relayé
        until = self.muted.get(k)
        if until is not None:
            if now < until:
                return False
            del self.muted[k]
        w = self.wrote.pop(k, None)
        times = [t for t in self.flaps.get(k, []) if now - t < self.FLAP_WINDOW] + [now]
        self.flaps[k] = times
        reverted = w is not None and w[0] != val and now - w[1] < self.REVERT_WINDOW
        if reverted or len(times) > self.FLAP_MAX:
            # switch de donjon (salle -1, jamais oublié par la règle de zone) défait une 1re fois :
            # peut-être deux joueurs à moins de REVERT_WINDOW s d'écart -> MUTE_TIME seulement
            long = reverted and (room >= 0 or k in self.reverted)
            if reverted and room < 0:
                self.reverted.add(k)
            dur = self.REVERT_MUTE if long else self.MUTE_TIME
            self.muted[k] = now + dur
            self.flaps.pop(k, None)
            self.n_muted += 1
            why = "défait par ton jeu juste après l'avoir reçu (réécrit par un acteur)" if reverted else \
                f"plus de {self.FLAP_MAX} changements en {self.FLAP_WINDOW:g} s"
            self._note(f"switch {sw_name(room, sw)} : {why} ; plus relayé pendant {dur:g} s")
            return False
        return True

    def _muted(self, room: int, sw: int, now: float) -> bool:
        until = self.muted.get((room, sw))
        return until is not None and now < until

    def _reset_local(self, now: float) -> None:
        self.prev = None
        self.held.clear()
        self.room_t = now

    def tick(self, mem) -> tuple[list, list, list]:
        """Renvoie (changements [[salle, sw, val]], tués [[salle, setID]], posés [(salle, sw|("tué", id), val)])."""
        now = self.clock()
        try:
            view = read_temp(mem)
        except Exception as e:   # mémoire illisible pendant un chargement
            view = TempView(False, f"lecture impossible : {e}")
        if not view.ok or view.busy.startswith("changement de stage"):
            self._reset_local(now)
            self.status = view.reason or view.busy
            return [], [], []
        if self.dungeons_only and not (view.dan_ok and view.dan_no in TSW_DUNGEON_SAVE_NOS):
            self._reset_local(now)
            self.ctx = None
            self.status = f"en pause hors donjon ({view.stage})"
            return [], [], []
        ctx = (view.gi, view.stage)
        if ctx != self.ctx:
            self.ctx, self.prev = ctx, None
            self.held.clear()
            self.room_t = now
            self.wrote.clear()
            self.flaps.clear()
            self.muted.clear()
            self.cycles.clear()
            self.cyclic.clear()
            self.owned_noted.clear()
            self.owned_drop_noted.clear()
            self.reverted.clear()
            self.quiet.clear()
            self.targets.clear()
            with self.lock:   # valeurs reçues pour un autre stage : sans objet
                self.pending = {k: v for k, v in self.pending.items() if k[0] == view.stage}
                self.pending_act = {k: v for k, v in self.pending_act.items() if k[0] == view.stage}
        self.stage = view.stage
        prev = self.prev
        if prev is not None and (prev.player != view.player or prev.dan_no != view.dan_no):
            prev = None                                   # salle rechargée (chute, Game Over) ou autre donjon
            self.held.clear()
            self.room_t = now
        if prev is not None and (view.room != prev.room or view.zones != prev.zones):
            self.room_t = now
        for k in [k for k in self.muted if k[0] != -1 and k[0] not in view.zones]:
            del self.muted[k]                             # zone déchargée : verdict « local » oublié
            self.cyclic.pop(k, None)
            self.cycles.pop(k, None)
        busy = self.events.feed(view.busy)
        changes: list = []
        acts: list = []
        if prev is not None:
            def comparable(room):
                if room == -1:
                    return view.dan_ok and prev.dan_ok and view.dan_no == prev.dan_no
                return room in view.zones and prev.zones.get(room) == view.zones[room]
            for room, sw in sorted(view.on ^ prev.on):
                if not comparable(room):
                    continue
                val = 1 if (room, sw) in view.on else 0
                if val == 0 and sw >= TSW_ROOM_FIRST:
                    self.held[(room, sw)] = now          # peut-être clearRoomSwitch : on attend
                    continue
                self.held.pop((room, sw), None)
                if self._local_change(room, sw, val, now):
                    changes.append([room, sw, val])
            acts = [[room, i] for room, i in sorted(view.acts - prev.acts)
                    if comparable(room) and (room, i) not in self.quiet]
        if self.quiet:
            self.quiet -= view.acts                      # vus posés : consommés
        for (room, sw), t in list(self.held.items()):
            if self.room_t >= t - self.ROOM_GRACE:
                del self.held[(room, sw)]                # changement de salle autour : effacement du jeu
                self.n_room_clears += 1
            elif now - t >= self.ROOM_GRACE:
                del self.held[(room, sw)]
                if view.alive(room) and (room, sw) not in view.on and self._local_change(room, sw, 0, now, t):
                    changes.append([room, sw, 0])
        if changes:
            with self.lock:                               # compté dès maintenant (pas à l'envoi)
                for room, sw, _v in changes:
                    k = (view.stage, room, sw)
                    self.unacked[k] = self.unacked.get(k, 0) + 1
        self.n_sent += len(changes)
        self.n_acts_sent += len(acts)
        applied = self._apply(mem, view, now, busy) if self.apply_enabled else []
        self.prev = view
        if not self.apply_enabled:
            self.status = "à jour (application désactivée)"
        return changes, acts, applied

    def _apply(self, mem, view: TempView, now: float, busy: str) -> list:
        applied: list = []
        with self.lock:
            items = [(k, v) for k, v in self.pending.items() if k[0] == view.stage]
            acts = [(k, t) for k, t in self.pending_act.items() if k[0] == view.stage]
            unacked = {k for k, n in self.unacked.items() if n > 0}
        done, done_act = [], []
        for k, t in acts:                 # tués : seulement 0->1, sans attendre (lus au chargement)
            _st, room, i = k
            if not view.alive(room):
                continue                  # zone pas (encore) chargée : gardé tant qu'on reste dans le stage
            done_act.append(k)
            if (room, i) not in view.acts and mark_actor_dead(mem, i, room, view.gi):
                view.acts.add((room, i))
                applied.append((room, ("tué", i), 1))
                self.n_acts_applied += 1
                if len(self.targets) < self.MAX_TARGETS:
                    self.targets[(room, i)] = now   # l'acteur est peut-être déjà né : à supprimer
        waiting = 0
        settled = now - self.room_t >= self.ROOM_GRACE
        for k, (val, t, _seq) in items:
            _st, room, sw = k
            if k in unacked or self._muted(room, sw, now) or (room, sw) in self.owned:
                done.append(k)            # notre valeur, plus récente, part ; ou switch « local »
                if (room, sw) in self.owned and k not in unacked and (room, sw) not in self.owned_drop_noted \
                        and len(self.owned_drop_noted) < 256:
                    self.owned_drop_noted.add((room, sw))   # l'autre PC le relaie encore (pas à jour)
                    self._note(f"switch {sw_name(room, sw)} : valeur reçue ignorée (switch d'acteur, "
                               f"{self.owned[(room, sw)]}) ; l'autre PC le relaie encore : mets-le à jour")
                continue
            if not view.alive(room):
                if sw >= TSW_ROOM_FIRST and now - t > self.PENDING_TTL:
                    done.append(k)        # switch de salle d'une zone absente : le jeu l'efface
                else:
                    waiting += 1
                continue
            if busy or not settled or (room, sw) in self.held:
                waiting += 1
                continue
            done.append(k)
            if (((room, sw) in view.on) != bool(val)) and write_temp_switch(mem, view.gi, room, sw, val):
                (view.on.add if val else view.on.discard)((room, sw))   # pas d'écho au tick suivant
                self.wrote[(room, sw)] = (val, now)
                if self.flap_guard:
                    times = [x for x in self.flaps.get((room, sw), []) if now - x < self.FLAP_WINDOW]
                    self.flaps[(room, sw)] = times + [now]
                applied.append((room, sw, val))
                self.n_applied += 1
        seen = dict(items)
        with self.lock:
            for k in done:
                if self.pending.get(k) == seen[k]:     # pas remplacée entre-temps par une valeur plus récente
                    del self.pending[k]
            for k in done_act:
                self.pending_act.pop(k, None)
        if waiting:
            why = busy or ("changement de salle" if not settled else "zone non chargée")
            self.status = f"{waiting} switch(es) en attente : {why}"
        else:
            self.status = "à jour"
        return applied

    def summary(self) -> str:
        return (f"{self.n_sent} switch(es) envoyé(s), {self.n_received} reçu(s), {self.n_applied} posé(s) ; "
                f"ennemis tués : {self.n_acts_sent} envoyé(s), {self.n_acts_applied} posé(s) ; {self.status}"
                + (f" ; {self.n_room_clears} effacement(s) de salle non relayé(s)" if self.n_room_clears else "")
                + (f" ; {self.n_muted} switch(es) qui battaient rendus locaux" if self.n_muted else "")
                + (f" ; {self.n_owned} changement(s) de switch d'acteur non relayé(s)" if self.n_owned else ""))


# ---------------------------------------------------------------------------
# Mémoire simulée (tests, client --fake)
# ---------------------------------------------------------------------------
class FakeGuest:
    """Mémoire invitée minimale : quelques segments big-endian (bytearray).
    Lire hors segment lève ``KeyError`` ; ``try_read`` renvoie alors None."""

    def __init__(self):
        self.segs: dict[int, bytearray] = {}
        self.writes: list[tuple[int, bytes]] = []

    def add(self, base: int, size: int) -> None:
        self.segs[base] = bytearray(size)

    def _seg(self, va: int, n: int) -> tuple[bytearray, int]:
        for b, s in self.segs.items():
            if b <= va and va + n <= b + len(s):
                return s, va - b
        raise KeyError(f"adresse simulée hors segment : 0x{va:X}")

    def read(self, va: int, n: int) -> bytes:
        s, o = self._seg(va, n)
        return bytes(s[o:o + n])

    def try_read(self, va: int, n: int) -> bytes | None:
        try:
            return self.read(va, n)
        except KeyError:
            return None

    def write(self, va: int, data: bytes) -> None:
        s, o = self._seg(va, len(data))
        s[o:o + len(data)] = data
        self.writes.append((va, bytes(data)))

    def u8(self, va): return self.read(va, 1)[0]
    def s8(self, va): return struct.unpack(">b", self.read(va, 1))[0]
    def u32(self, va): return struct.unpack(">I", self.read(va, 4))[0]
    def w_u32(self, va, v): self.write(va, struct.pack(">I", v & 0xFFFFFFFF))


FAKE_GI = 0x145B7B60


def make_fake_game(stage: str = "sea", room: int = 13, stage_idx: int = 0) -> FakeGuest:
    """Jeu simulé : sauvegarde vide, Link présent, aucun événement, stage stable."""
    m = FakeGuest()
    m.add(SYM_GAMEINFO_PTR, 4)
    m.add(SYM_CUR_STAGE, 0x20)          # mCurStage + mNextStage (enabled à +0x18)
    m.add(SYM_EVT_CTRL, 0x100)
    m.add(SYM_PLAYER, 4)
    m.add(FAKE_GI, 0x1400)
    # synchro v2 : compteurs « en attente » de dComIfGp (play+0x5B40..0x5BD0), dont les petites clés
    # (SYM_KEY_PENDING, synchro/cles.py) et ceux de l'inventaire (synchro/inventaire.py)
    m.add(SYM_PLAY_ITEMS, PLAY_ITEMS_SIZE)
    m.w_u32(SYM_GAMEINFO_PTR, FAKE_GI)
    m.w_u32(SYM_PLAYER, 0x47C48E14)
    for i in range(4):
        m.w_u32(SYM_EVT_CTRL + EVT_PT + 4 * i, 0xFFFFFFFF)
    m.write(SYM_EVT_CTRL + EVT_EVENT_ID, struct.pack(">h", -1))   # mEventId = -1
    set_fake_stage(m, stage, room, stage_idx)
    for z in range(N_ZONES):
        m.write(FAKE_GI + GI_ZONE + z * ZONE_SIZE, b"\xff")
    m.write(FAKE_GI + GI_ZONE, struct.pack(">b", room))
    m.writes.clear()
    return m


def set_fake_stage(m: FakeGuest, stage: str, room: int, stage_idx: int) -> None:
    """Change de stage comme le jeu : recopie mMemory -> mSave.mMemory[ancien], puis l'inverse."""
    gi = FAKE_GI
    old = struct.unpack(">b", m.read(gi + GI_DAN, 1))[0]
    if m.read(SYM_CUR_STAGE, 1) != b"\0" and 0 <= old < N_MEM:
        m.write(gi + GI_MEMORY + old * MEM_SIZE, m.read(gi + GI_CUR_MEMORY, MEM_SIZE))
    m.write(gi + GI_CUR_MEMORY, m.read(gi + GI_MEMORY + stage_idx * MEM_SIZE, MEM_SIZE))
    m.write(gi + GI_DAN, struct.pack(">b", stage_idx) + bytes(DAN_SWITCH + DAN_SWITCH_SIZE - 1))
    m.write(SYM_CUR_STAGE, stage.encode()[:8].ljust(8, b"\0") + struct.pack(">hbb", 0, room, -1))
