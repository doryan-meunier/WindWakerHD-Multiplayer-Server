"""A15 « rejoindre la partie » : copie de la progression de l'hôte chez le joueur 2 (J2).

Logique pure autant que possible (testable sur ``progress.FakeGuest``), voir
docs/etudes/rejoindre.md. Offsets comptés depuis gi = ``g_dComIfG_gameInfo``
(``*(u32*)0x101F84DC``). Sur HD, ``dSv_save_c`` = disposition GameCube + 0x20 [HD-ASM :
``memory_to_card`` 0x025BA9FC et ``card_to_memory`` 0x025BA7B0 copient les mêmes champs].

Principe :
  * l'hôte lit (lecture seule) son image brute gi+0x20 .. gi+0x7C0 (``lire_image``) ;
  * J2 corrige l'image comme ``putSave`` (``image_hote``), calcule le plan d'écriture
    (``plan_ecriture``) : champs COPIE pris chez l'hôte, champs GARDE (position, heure,
    options…) laissés tels quels, champs AJUSTE (vie, stage courante) combinés ;
  * l'écriture (``appliquer``) se fait à un moment sûr (``moment_sur``), dans un ordre fixe,
    stage courante en dernier, puis relecture ; en cas d'échec, l'état d'avant est réécrit.

Format fichier (``cking.sav``, secours hors ligne §9) : 3 emplacements de 0xA8C octets +
8 octets de contrôle (u32 somme, u32 -(somme + 0xA8C)), puis un bloc commun. Les champs y
sont « compactés » (sans bourrage) dans l'ordre de ``memory_to_card`` : table ``CHAMPS_FICHIER``.
"""
from __future__ import annotations

import base64
import hashlib
import struct
import time
from dataclasses import dataclass, field

from jeu import progress as prog

# ---------------------------------------------------------------------------
# Image et table des champs (§5.1)
# ---------------------------------------------------------------------------
IMG_BASE = 0x20                   # début de l'image transmise (gi+0x20 = dSv_save_c)
IMG_END = 0x7C0                   # fin (mSave, mMemory vivant, mDan.mStageNo, mDan.mSwitch[0])
IMG_LEN = IMG_END - IMG_BASE      # 0x7A0
GI_DUMP = 0x2000                  # mémoire de J2 mise de côté (gi_avant.bin)
FMT = 1
JEU = "00050000-10143500/v0"
TABLE_END = 0x7BC                 # fin de la table (gi+0x7BC = mDan : jamais touché)

GI_MAX_LIFE, GI_LIFE = 0x20, 0x22
GI_STATUS_A, STATUS_A_SIZE = 0x20, 0x18
GI_STATUS_B = 0x38
GI_DATE_IPL = 0x38                # u64 status_b.mDateIPL : identifie l'emplacement (fichier +0x018)
GI_RETURN_PLACE = 0x50
GI_ITEM, ITEM_SIZE = 0x5C, 0x15   # dSv_player_item_c : 21 emplacements (0xFF = vide)
GI_GET_ITEM = 0x71
GI_PICTURE = 0x88                 # item_record.mPictureNum
GI_ARROWS, GI_BOMBS = 0x89, 0x8A
GI_COLLECT = 0xD4                 # dSv_player_collect_c : mCollect[8], 0x8, mTact, mTriforce, mSymbol
GI_TACT, GI_TRIFORCE, GI_SYMBOL = 0xDD, 0xDE, 0xDF
GI_MAP = 0xE4                     # dSv_player_map_c : u32[4][4] (cartes), mFmapBits[0x31] (îles)
GI_FMAP, N_ISLES = 0x124, 0x31
GI_INFO = 0x168
GI_CLEAR = 0x1C0                  # info.mClearCount, mRandomSalvagePoint
GI_CONFIG, GI_PRIEST = 0x1C4, 0x1CC
GI_STATUS_C = 0x1DC
GI_MEMORY, MEM_SIZE, N_MEM = prog.GI_MEMORY, prog.MEM_SIZE, prog.N_MEM
GI_OCEAN = 0x5E0
GI_EVENT = prog.GI_EVENT
GI_RESERVE = 0x744
GI_CUR_MEMORY = prog.GI_CUR_MEMORY
GI_DAN = prog.GI_DAN
MEM_KEYNUM = 0x20                 # dSv_memBit_c.mKeyNum (compteur, pris chez l'hôte)
MEM_DUNGEON = 0x21                # bits : carte, boussole, grande clé, boss vaincu (bit 3)…

COPIE, GARDE, AJUSTE, IGNORE = "copie", "garde", "ajuste", "ignoré"

# (gi+, taille, champ, règle) : couvre gi+0x20 .. 0x7BC sans trou ni chevauchement
BLOCS: tuple[tuple[int, int, str, str], ...] = (
    (0x020, 0x002, "status_a.mMaxLife", COPIE),
    (0x022, 0x002, "status_a.mLife", AJUSTE),
    (0x024, 0x014, "status_a (rubis, X/Y/R, épée/bouclier, bourse, magie)", COPIE),
    (0x038, 0x018, "status_b (date IPL, heure, jour, vent)", GARDE),
    (0x050, 0x00C, "return_place (stage, salle, point)", GARDE),
    (0x05C, 0x02A, "item, get_item", COPIE),
    (0x086, 0x002, "item_record.mTimer", COPIE),
    (0x088, 0x001, "item_record.mPictureNum", GARDE),
    (0x089, 0x0DF, "flèches, bombes, flacons, item_max, sacs, collect, map", COPIE),
    (0x168, 0x058, "info (morts, noms GC, puzzle)", GARDE),
    (0x1C0, 0x002, "info.mClearCount, mRandomSalvagePoint", COPIE),
    (0x1C2, 0x002, "bourrage", IGNORE),
    (0x1C4, 0x008, "config", GARDE),
    (0x1CC, 0x010, "priest", GARDE),
    (0x1DC, 0x1C0, "status_c[4]", COPIE),
    (0x39C, 0x004, "bourrage", IGNORE),
    (0x3A0, 0x240, "mMemory[16]", COPIE),
    (0x5E0, 0x064, "mOcean", COPIE),
    (0x644, 0x100, "mEvent", COPIE),
    (0x744, 0x050, "mReserve", COPIE),
    (0x794, 0x004, "bourrage", IGNORE),
    (0x798, 0x024, "mMemory vivant (stage chargée)", AJUSTE),
)

# Champs dans l'ordre de memory_to_card : (nom, offset dans dSv_save_c = gi - 0x20, taille).
# L'offset dans l'emplacement du fichier est la somme des tailles précédentes [recherche 2 :
# fichier vs mémoire, PC1 2026-11-08 ; reserve à fichier+0x718, fin à 0x768].
CHAMPS_FICHIER: tuple[tuple[str, int, int], ...] = (
    ("status_a", 0x000, 0x18), ("status_b", 0x018, 0x18), ("return_place", 0x030, 0xC),
    ("item", 0x03C, 0x15), ("get_item", 0x051, 0x15), ("item_record", 0x066, 0x8),
    ("item_max", 0x06E, 0x8), ("bag_item", 0x076, 0x18), ("get_bag_item", 0x090, 0xC),
    ("bag_item_record", 0x09C, 0x18), ("collect", 0x0B4, 0xD), ("map", 0x0C4, 0x84),
    ("info", 0x148, 0x5C), ("config", 0x1A4, 0x5), ("priest", 0x1AC, 0x10),
    ("status_c", 0x1BC, 0x1C0), ("memory", 0x380, 0x240), ("ocean", 0x5C0, 0x64),
    ("event", 0x624, 0x100), ("reserve", 0x724, 0x50))
SLOT_DATA = 0xA8C                 # octets d'un emplacement (somme de contrôle sur ceux-ci)
SLOT_STRIDE = 0xA94               # + u32 somme + u32 -(somme + 0xA8C)
N_SLOTS = 3
SLOT_DATE_IPL = 0x018


def disposition_fichier() -> tuple[list[tuple[str, int, int, int]], int]:
    """[(nom, gi+, fichier+, taille)], fin des champs compactés (0x768)."""
    out, p = [], 0
    for nom, m, t in CHAMPS_FICHIER:
        out.append((nom, IMG_BASE + m, p, t))
        p += t
    return out, p


def s8(b: int) -> int:
    return b - 256 if b >= 128 else b


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Image de l'hôte
# ---------------------------------------------------------------------------
def gi_depuis_image(img: bytes) -> bytes:
    """Image (gi+0x20 .. 0x7C0) -> octets indexés comme gi (en-tête à zéro)."""
    return bytes(IMG_BASE) + bytes(img)


def image_hote(img: bytes) -> bytes:
    """Corrige l'image comme ``putSave`` : mMemory[n_h] reçoit la copie vivante (gi+0x798),
    n_h = mDan.mStageNo (gi+0x7BC). En mer n_h vaut 0 : la règle s'applique quand même."""
    if len(img) != IMG_LEN:
        raise ValueError(f"image de 0x{len(img):X} octets (attendu 0x{IMG_LEN:X})")
    n_h = s8(img[GI_DAN - IMG_BASE])
    if not 0 <= n_h < N_MEM:
        raise ValueError(f"mDan.mStageNo de l'hôte hors limites ({n_h})")
    out = bytearray(img)
    dst = GI_MEMORY + n_h * MEM_SIZE - IMG_BASE
    src = GI_CUR_MEMORY - IMG_BASE
    out[dst:dst + MEM_SIZE] = img[src:src + MEM_SIZE]
    return bytes(out)


def valider_image(img: bytes) -> tuple[list[str], list[str]]:
    """(erreurs, avertissements) sur une image brute reçue (§6.2)."""
    err, warn = [], []
    if len(img) != IMG_LEN:
        return [f"taille 0x{len(img):X} au lieu de 0x{IMG_LEN:X}"], warn
    mx = struct.unpack_from(">H", img, GI_MAX_LIFE - IMG_BASE)[0]
    if not 12 <= mx <= 80:          # quarts de cœur : 13, 14, 15 sont normaux (vu le 2026-11-15)
        err.append(f"vie maximale incohérente ({mx} quarts de cœur)")
    n_h = s8(img[GI_DAN - IMG_BASE])
    if not 0 <= n_h < N_MEM:
        err.append(f"mDan.mStageNo hors limites ({n_h})")
    if img[0x5A - IMG_BASE:0x5C - IMG_BASE] != b"\x66\x67":
        # [HYPO] constantes vues en direct et dans cking.sav ; simple avertissement
        warn.append("octets gi+0x5A inattendus (pas 66 67) : format à vérifier")
    return err, warn


def partie_chargee(mem) -> str:
    """« » si une partie est chargée (gi lisible, pas l'écran titre, vie max plausible)."""
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return "gameInfo illisible"
    gi = struct.unpack(">I", raw)[0]
    if not 0x10000000 <= gi < 0x50000000:
        return "pas de sauvegarde chargée"
    st = mem.try_read(prog.SYM_CUR_STAGE, 12)
    if st is None:
        return "stage illisible"
    stage = st[:8].split(b"\0", 1)[0].decode("ascii", "replace")
    if stage in prog.TITLE_STAGES:
        return f"écran titre ({stage or 'vide'})"
    raw = mem.try_read(gi + GI_MAX_LIFE, 2)
    if raw is None:
        return "sauvegarde illisible"
    mx = struct.unpack(">H", raw)[0]
    if not 1 <= mx <= 80:           # pas « multiple de 4 » : un quart de cœur donne 13 (client jamais lancé, 2026-11-15)
        return f"vie maximale incohérente ({mx})"
    return ""


def lire_gi(mem) -> int:
    return struct.unpack(">I", mem.read(prog.SYM_GAMEINFO_PTR, 4))[0]


def lire_stage(mem) -> tuple[str, int]:
    st = mem.read(prog.SYM_CUR_STAGE, 12)
    return st[:8].split(b"\0", 1)[0].decode("ascii", "replace"), s8(st[10])


def _sans_status_b(img: bytes) -> bytes:
    a, b = GI_STATUS_B - IMG_BASE, GI_STATUS_B + 0x18 - IMG_BASE
    return img[:a] + bytes(b - a) + img[b:]


def lire_image(mem, attente: float = 10.0, essais: int = 5, ecart: float = 0.1,
               sleep=time.sleep, clock=time.monotonic, watch: prog.EventWatch | None = None) -> tuple[bytes, dict]:
    """Côté hôte (LECTURE SEULE) : image brute gi+0x20 .. 0x7C0, lue deux fois à ``ecart`` s
    d'intervalle et identique hors status_b (heure). Attend au plus ``attente`` s une partie
    chargée, sans changement de stage ni événement. Lève RuntimeError sinon.
    Renvoie (image, infos {stage, room, stage_no})."""
    watch = watch or prog.EventWatch(clock=clock)
    t0 = clock()
    raison = "?"
    n = 0
    while True:
        raison = partie_chargee(mem)
        if not raison:
            nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
            raison = "changement de stage en cours" if nxt is None or nxt[0] else watch.poll(mem)
        if not raison:
            gi = lire_gi(mem)
            a = mem.read(gi + IMG_BASE, IMG_LEN)
            sleep(ecart)
            b = mem.read(gi + IMG_BASE, IMG_LEN)
            if _sans_status_b(a) == _sans_status_b(b) and lire_gi(mem) == gi:
                stage, room = lire_stage(mem)
                return b, {"stage": stage, "room": room, "stage_no": s8(b[GI_DAN - IMG_BASE])}
            n += 1
            raison = "la sauvegarde change pendant la lecture"
            if n >= essais:
                raise RuntimeError(f"lecture instable après {essais} essais ({raison})")
        if clock() - t0 >= attente:
            raise RuntimeError(f"partie pas prête depuis {attente:.0f} s : {raison}")
        sleep(0.1)


def decouper(img: bytes, taille: int) -> list[str]:
    """Image -> morceaux base64 d'au plus ``taille`` octets bruts."""
    return [base64.b64encode(img[i:i + taille]).decode("ascii") for i in range(0, max(1, len(img)), taille)]


# ---------------------------------------------------------------------------
# Plan d'écriture (§5.4, §6.3)
# ---------------------------------------------------------------------------
@dataclass
class Options:
    vie_pleine: bool = False
    strict: bool = False
    vie: int | None = None        # vie imposée (annulation : vie d'avant)


def stage_courante(gi_j2: bytes) -> int | None:
    n = s8(gi_j2[GI_DAN])
    return n if 0 <= n < N_MEM else None


def plan_ecriture(img_corr: bytes, gi_j2: bytes, options: Options | None = None,
                  courante: bool = True) -> list[tuple[int, bytes, str]]:
    """Blocs à écrire chez J2, dans l'ordre : [(gi+, octets, nom)].

    ``img_corr`` : image de l'hôte corrigée (``image_hote``) ; ``gi_j2`` : octets de J2 depuis
    gi (au moins 0x7C0). ``courante`` faux (mode fichier) : pas de stage chargée, mMemory
    vient entièrement de l'hôte et gi+0x798 n'est pas écrit."""
    o = options or Options()
    h = gi_depuis_image(img_corr)

    def blk(off: int, n: int, nom: str, src: bytes = h) -> tuple[int, bytes, str]:
        return off, bytes(src[off:off + n]), nom

    n_j = stage_courante(gi_j2) if courante else None
    plan = [blk(GI_EVENT, 0x100, "mEvent"), blk(GI_RESERVE, 0x50, "mReserve"), blk(GI_OCEAN, 0x64, "mOcean")]
    if n_j is None:
        plan.append(blk(GI_MEMORY, MEM_SIZE * N_MEM, "mMemory"))
    else:
        a, b = GI_MEMORY + n_j * MEM_SIZE, GI_MEMORY + (n_j + 1) * MEM_SIZE
        if a > GI_MEMORY:
            plan.append(blk(GI_MEMORY, a - GI_MEMORY, f"mMemory[0..{n_j - 1}]"))
        if b < GI_MEMORY + MEM_SIZE * N_MEM:
            plan.append(blk(b, GI_MEMORY + MEM_SIZE * N_MEM - b, f"mMemory[{n_j + 1}..15]"))
    plan += [blk(GI_STATUS_C, 0x1C0, "status_c"),
             blk(GI_ITEM, GI_PICTURE - GI_ITEM, "item, get_item, mTimer"),
             blk(GI_ARROWS, GI_INFO - GI_ARROWS, "munitions, sacs, collect, map"),
             blk(GI_CLEAR, 2, "info.mClearCount")]
    sa = bytearray(h[GI_STATUS_A:GI_STATUS_A + STATUS_A_SIZE])
    mx = struct.unpack_from(">H", sa, 0)[0]
    if o.vie is not None:
        life = o.vie
    elif o.vie_pleine:
        life = mx
    else:
        life = max(1, min(struct.unpack_from(">H", gi_j2, GI_LIFE)[0], mx))
    struct.pack_into(">H", sa, 2, life)
    plan.append((GI_STATUS_A, bytes(sa), "status_a"))
    if n_j is not None:
        hm = h[GI_MEMORY + n_j * MEM_SIZE:GI_MEMORY + (n_j + 1) * MEM_SIZE]
        if o.strict:
            st = bytes(hm)
        else:
            live = gi_j2[GI_CUR_MEMORY:GI_CUR_MEMORY + MEM_SIZE]
            st = bytearray(x | y for x, y in zip(hm, live))
            # revue A15 n° 7 : un coffre à clé ouvert par J2 seul reste ouvert (OU des bits) ;
            # garder le plus grand des deux compteurs évite de perdre cette clé pour de bon
            st[MEM_KEYNUM] = max(hm[MEM_KEYNUM], live[MEM_KEYNUM])
            st = bytes(st)
        plan.append((GI_MEMORY + n_j * MEM_SIZE, st, f"mMemory[{n_j}] (stage courante)"))
        plan.append((GI_CUR_MEMORY, st, "mMemory vivant"))
    return plan


def appliquer_plan_octets(gi: bytes, plan) -> bytes:
    """Simulation : octets de gi après le plan."""
    out = bytearray(gi)
    for off, data, _nom in plan:
        out[off:off + len(data)] = data
    return bytes(out)


def plan_dernier(plan) -> int:
    """Nombre de blocs « stage courante » en fin de plan (écrits l'un juste après l'autre)."""
    return sum(1 for off, _d, _n in plan if off == GI_CUR_MEMORY) * 2


def plan_taille(plan) -> int:
    return sum(len(d) for _o, d, _n in plan)


def verifier(gi_lu: bytes, plan) -> list[str]:
    """Blocs qui ne valent pas la valeur visée. mLife toléré (coup reçu) s'il reste <= max."""
    bad = []
    for off, data, nom in plan:
        got = gi_lu[off:off + len(data)]
        if off == GI_STATUS_A:
            mx = struct.unpack_from(">H", got, 0)[0]
            life = struct.unpack_from(">H", got, 2)[0]
            if got[:2] + got[4:] != data[:2] + data[4:] or life > mx:
                bad.append(nom)
        elif got != data:
            bad.append(nom)
    return bad


# ---------------------------------------------------------------------------
# Moment sûr (§6.2)
# ---------------------------------------------------------------------------
LINK_EQUIP_ITEM = 0x69B0          # u16 mEquipItem : 0x100 rien, 0x101 bâton, 0x103 épée [HD-ASM, LIVE]
EQUIP_RIEN = 0x100
EQUIP_EPEE = 0x103                # [HYPO] vu au repos sur le PC1 (épée peut-être rangée) : --ignorer-epee
ACTOR_SPEEDF = 0x254 + 0x11C      # f32 speedF [DECOMP + décalage HD, voir game.py]
ACTOR_POS = 0x1F8 + 0x11C         # cXyz current.pos
BATEAU_DIST = 250.0               # [HYPO] Link à moins de 250 u du bateau = « en bateau »
SYM_STAY_NO = 0x1047E6C8          # u8 salle courante (game.SYM_STAY_NO)
SYM_PLAYER_PTRS = 0x10474BE4      # Link, partenaire, bateau (game.SYM_PLAYER_PTRS)
SYM_DMENU_FLAG = 0x101EA069       # u8 dMenu_flag : levé par le menu de pause, les fenêtres de sauvegarde et
                                  # d'options, le Game Over, le Miiverse… [HD-ASM, docs/etudes/pause_vivante.md §2]


def _ptr(v: int) -> int:
    return v if 0x10000000 <= v < 0x50000000 else 0


LINK_ANIM = (0x300, 0xD00)        # octets de Link qui changent à chaque image, même immobile
                                  # (lecture PC1 2026-11-09 : 86 à 94 octets différents à 150 ms
                                  # d'écart, Link au repos dans M_NewD2) ; figés = menu / pause [HYPO]
FIGE_DELAI = 0.6                  # s sans aucun changement : « jeu en pause / menu ouvert »
QUESTION_DELAI = 20.0             # s bloqué sur « épée » ou « figé » avant de poser la question
RAISON_EPEE = "épée en main"
RAISON_FIGE = "jeu en pause ou menu ouvert (Link figé)"
RAISON_ANNULE = "annulé (Ctrl+C)"


def moment_sur(mem, watch: prog.EventWatch | None = None, epee_ok: bool = False) -> str:
    """« » si on peut écrire maintenant ; sinon la raison (affichée à J2).
    Pas de changement de stage, pas d'événement, Link présent, rien en main (épée rangée),
    immobile, pas en bateau. Le menu de pause est détecté à part par ``attendre_moment_sur`` : drapeau
    du menu (``menu_ouvert``, [HD-ASM]) et Link figé (autres pauses, [HYPO])."""
    r = partie_chargee(mem)
    if r:
        return r
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        return "changement de stage en cours"
    ev = (watch.poll(mem) if watch else prog.event_busy(mem))
    if ev:
        return "cinématique / dialogue / coffre en cours"
    raw = mem.try_read(prog.SYM_PLAYER, 4)
    link = _ptr(struct.unpack(">I", raw)[0]) if raw else 0
    if not link:
        return "Link absent (chargement)"
    raw = mem.try_read(link + LINK_EQUIP_ITEM, 2)
    if raw is None:
        return "Link illisible"
    eq = struct.unpack(">H", raw)[0]
    if eq == EQUIP_EPEE and not epee_ok:
        return RAISON_EPEE + " (range-la)"
    if eq not in (EQUIP_RIEN, EQUIP_EPEE):
        return f"objet en main (0x{eq:X} : lâche ce que tu tiens)"
    raw = mem.try_read(link + ACTOR_SPEEDF, 4)
    if raw is not None and abs(struct.unpack(">f", raw)[0]) >= 1.0:
        return "Link bouge (ne bouge plus)"
    ptrs = mem.try_read(SYM_PLAYER_PTRS, 12)
    ship = _ptr(struct.unpack(">3I", ptrs)[2]) if ptrs else 0
    if ship:
        a, b = mem.try_read(link + ACTOR_POS, 12), mem.try_read(ship + ACTOR_POS, 12)
        if a is not None and b is not None:
            pa, pb = struct.unpack(">3f", a), struct.unpack(">3f", b)
            if sum((x - y) ** 2 for x, y in zip(pa, pb)) < BATEAU_DIST ** 2:
                return "en bateau (descends à terre)"
    return ""


def position(mem) -> tuple:
    """(stage, salle, n_j, salle de séjour) : ce qui ne doit pas changer pendant l'écriture."""
    stage, room = lire_stage(mem)
    gi = lire_gi(mem)
    stay = mem.try_read(SYM_STAY_NO, 1)
    return stage, room, s8(mem.read(gi + GI_DAN, 1)[0]), stay[0] if stay else None


def menu_ouvert(mem) -> bool | None:
    """dMenu_flag levé (menu de pause, sauvegarde, options, Game Over…) ; None si illisible. Indispensable depuis
    le pack WWMP Pause v8 : Link y tourne (vulnérable sans commandes) sous le menu, ``link_anim`` ne voit plus le
    menu."""
    raw = mem.try_read(SYM_DMENU_FLAG, 1)
    return None if raw is None else raw[0] != 0


def link_anim(mem) -> bytes | None:
    """Octets de Link qui bougent à chaque image (``LINK_ANIM``) ; None si illisible."""
    tick = getattr(mem, "tick_anim", None)     # jeu simulé : une « image » passe
    if tick:
        tick()
    raw = mem.try_read(prog.SYM_PLAYER, 4)
    link = _ptr(struct.unpack(">I", raw)[0]) if raw else 0
    return mem.try_read(link + LINK_ANIM[0], LINK_ANIM[1]) if link else None


@dataclass
class Garde:
    """Réglages du moment sûr, modifiables pendant l'attente (réponse du joueur)."""
    epee_ok: bool = False
    fige: bool = True              # détecter « Link figé » (menu / pause)
    stop: object = None            # threading.Event : Ctrl+C
    confirmer: object = None       # confirmer(code) -> bool : « épée rangée ? », « menu fermé ? »
    demande: set = field(default_factory=set)

    def annule(self) -> bool:
        return bool(self.stop is not None and self.stop.is_set())


def attendre_moment_sur(mem, attente: float, tenu: float = 1.0, pas: float = 0.1, sleep=time.sleep,
                        clock=time.monotonic, afficher=None, epee_ok: bool = False,
                        garde: Garde | None = None) -> tuple[bool, str]:
    """Attend que ``moment_sur`` soit vrai ``tenu`` s de suite, même position, Link animé (pas
    de menu). (ok, dernière raison). Bloqué ``QUESTION_DELAI`` s sur « épée » ou « figé » :
    ``garde.confirmer`` pose la question une fois (oui : ce test est ignoré ensuite).
    ``garde.stop`` levé : (False, RAISON_ANNULE) aussitôt."""
    g = garde or Garde(epee_ok=epee_ok)
    watch = prog.EventWatch(clock=clock)
    t0 = clock()
    depuis, pos0, derniere = None, None, ""
    anim, anim_t = None, None
    bloque, bloque_t = "", None
    while True:
        if g.annule():
            return False, RAISON_ANNULE
        try:
            r = moment_sur(mem, watch, g.epee_ok)
            pos = position(mem) if not r else None
        except Exception as e:   # lecture impossible (chargement)
            r, pos = f"lecture impossible ({e})", None
        now = clock()
        if g.fige:
            a = link_anim(mem)
            if a is None or a != anim:
                anim, anim_t = a, now
            elif not r and now - anim_t >= FIGE_DELAI:
                r = RAISON_FIGE + " : ferme le menu"
            if not r and menu_ouvert(mem):     # pack WWMP Pause : Link tourne sous le menu
                r = RAISON_FIGE + " : ferme le menu"
        code = "epee" if r.startswith(RAISON_EPEE) else "fige" if r.startswith(RAISON_FIGE) else ""
        if code != bloque:
            bloque, bloque_t = code, now
        elif code and code not in g.demande and g.confirmer and now - bloque_t >= QUESTION_DELAI:
            g.demande.add(code)
            if g.confirmer(code):
                if code == "epee":
                    g.epee_ok = True
                else:
                    g.fige = False
                bloque = ""
                continue
            if g.annule():
                return False, RAISON_ANNULE
        if r:
            depuis = None
            if r != derniere and afficher:
                afficher(f"  [attends : {r}]")
            derniere = r
        else:
            if depuis is None or pos != pos0:
                depuis, pos0 = now, pos
            if now - depuis >= tenu:
                if afficher:
                    afficher(f"  [ok depuis {now - depuis:.1f} s]")
                return True, ""
        if now - t0 >= attente:
            return False, derniere or "pas stable assez longtemps"
        sleep(pas)


# ---------------------------------------------------------------------------
# Écriture, vérification, retour arrière (§6.3)
# ---------------------------------------------------------------------------
@dataclass
class Resultat:
    ok: bool
    statut: str                   # "ok" | "reprendre" | "echec"
    raison: str = ""
    plan: list = field(default_factory=list)
    octets: int = 0
    defait: bool = False
    ecrit: bool = False           # au moins une écriture faite dans le jeu
    gi_avant: bytes | None = None  # instantané pris juste avant la première écriture


def lire_gi_octets(mem, n: int = IMG_END) -> tuple[int, bytes]:
    gi = lire_gi(mem)
    return gi, mem.read(gi, n)


def ecrire_une_fois(mem, img_corr: bytes, options: Options, essais: int = 3) -> Resultat:
    """Une passe d'écriture. « reprendre » si la stage a changé avant le dernier bloc
    (les écritures déjà faites sont idempotentes : l'appelant attend un nouveau moment sûr)."""
    gi, avant = lire_gi_octets(mem)
    plan = plan_ecriture(img_corr, avant, options)
    k = plan_dernier(plan)
    n_j = avant[GI_DAN]
    for i in range(essais):
        for off, data, _nom in plan[:len(plan) - k]:
            mem.write(gi + off, data)
        nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
        if mem.read(gi + GI_DAN, 1)[0] != n_j or nxt is None or nxt[0] or lire_gi(mem) != gi:
            return Resultat(False, "reprendre", "la stage a changé pendant l'écriture", plan)
        for off, data, _nom in plan[len(plan) - k:]:
            mem.write(gi + off, data)
        bad = verifier(mem.read(gi, IMG_END), plan)
        nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
        if mem.read(gi + GI_DAN, 1)[0] != n_j or nxt is None or nxt[0] or lire_gi(mem) != gi:
            # revue A15 n° 9 : stage changée pendant les derniers blocs : on refait la passe
            return Resultat(False, "reprendre", "la stage a changé pendant l'écriture", plan)
        if not bad:
            return Resultat(True, "ok", "", plan, plan_taille(plan))
        raison = "relecture différente : " + ", ".join(bad)
    return Resultat(False, "echec", raison, plan)


def defaire(mem, gi_avant: bytes) -> bool:
    """Réécrit l'état d'avant (mêmes blocs, état exact, stage courante comprise)."""
    img = image_hote(gi_avant[IMG_BASE:IMG_END])
    vie = struct.unpack_from(">H", gi_avant, GI_LIFE)[0]
    gi, now = lire_gi_octets(mem)
    plan = plan_ecriture(img, now, Options(strict=True, vie=vie))
    if stage_courante(now) == stage_courante(gi_avant):
        # même stage (cas normal) : chaque bloc reprend exactement ses octets d'avant
        plan = [(o, bytes(gi_avant[o:o + len(d)]), n) for o, d, n in plan]
    for off, data, _n in plan:
        mem.write(gi + off, data)
    return not verifier(mem.read(gi, IMG_END), plan)


def appliquer(mem, img_corr: bytes, options: Options, attente: float = 120.0, reprises: int = 5,
              sleep=time.sleep, clock=time.monotonic, afficher=None, epee_ok: bool = False,
              garde: Garde | None = None, attente_defaire: float = 30.0) -> Resultat:
    """Attente d'un moment sûr, écriture, vérification ; reprend si la stage change.
    En cas d'échec après une écriture, l'état d'avant (instantané pris juste avant la PREMIÈRE
    écriture, ``res.gi_avant``) est réécrit, mais seulement à un moment sûr (au plus
    ``attente_defaire`` s, même après Ctrl+C) : sinon rien n'est réécrit et ``res.defait`` est
    faux (copie PARTIELLE). ``garde.stop`` levé : plus aucune nouvelle passe."""
    g = garde or Garde(epee_ok=epee_ok)
    res = Resultat(False, "echec", "pas de moment sûr")
    gi_avant = None
    for _ in range(reprises):
        ok, raison = attendre_moment_sur(mem, attente, sleep=sleep, clock=clock, afficher=afficher, garde=g)
        if ok and g.annule():
            ok, raison = False, RAISON_ANNULE
        if not ok:
            res = Resultat(False, "echec", RAISON_ANNULE if raison == RAISON_ANNULE
                           else f"pas de moment sûr en {attente:.0f} s ({raison})")
            break
        if gi_avant is None:
            _gi, gi_avant = lire_gi_octets(mem, GI_DUMP)
        try:
            res = ecrire_une_fois(mem, img_corr, options)
        except Exception as e:      # écriture refusée par le système, jeu fermé…
            res = Resultat(False, "echec", f"écriture impossible ({e.__class__.__name__}: {e})")
        if res.statut != "reprendre":
            break
        if afficher:
            afficher("  la stage a changé pendant l'écriture : on recommence")
    if gi_avant is not None:
        res.ecrit, res.gi_avant = True, gi_avant
    if res.statut == "reprendre":
        res.statut, res.raison = "echec", f"la stage change sans cesse ({reprises} essais)"
    if not res.ok and res.ecrit:
        if afficher:
            afficher("  remise de l'état d'avant : attente d'un moment sûr ...")
        g2 = Garde(epee_ok=True, fige=g.fige)          # pas de Ctrl+C ni de question ici
        ok, raison = attendre_moment_sur(mem, attente_defaire, sleep=sleep, clock=clock, garde=g2)
        if ok:
            try:
                res.defait = defaire(mem, gi_avant)
            except Exception:
                res.defait = False
        res.raison += " ; état d'avant " + ("remis" if res.defait else "PAS remis" + (
            "" if ok else f" (pas de moment sûr en {attente_defaire:.0f} s : {raison})"))
    return res


def restaurer(mem, gi_avant: bytes, attente: float = 120.0, sleep=time.sleep, clock=time.monotonic,
              afficher=None, garde: Garde | None = None) -> Resultat:
    """Annulation en mémoire : réécrit exactement l'état ``gi_avant`` (comme ``defaire``, stage
    courante comprise : octets exacts si même stage, sinon état strict d'avant) à un moment sûr."""
    g = garde or Garde()
    ok, raison = attendre_moment_sur(mem, attente, sleep=sleep, clock=clock, afficher=afficher, garde=g)
    if not ok:
        return Resultat(False, "echec", RAISON_ANNULE if raison == RAISON_ANNULE
                        else f"pas de moment sûr en {attente:.0f} s ({raison})")
    try:
        bon = defaire(mem, gi_avant)
    except Exception as e:
        return Resultat(False, "echec", f"écriture impossible ({e.__class__.__name__}: {e})", ecrit=True)
    return Resultat(bon, "ok" if bon else "echec", "" if bon else "relecture différente", ecrit=True)


# ---------------------------------------------------------------------------
# Résumé et différences (§3.3)
# ---------------------------------------------------------------------------
# Identifiants d'objets [HYPO : table du randomizer / de la décomp, noms français approximatifs]
NOMS_OBJETS = {
    0x20: "Télescope", 0x21: "Tingle-Bouteille", 0x22: "Baguette du Vent", 0x23: "Boîte à images",
    0x24: "Sac à butin", 0x25: "Grappin", 0x26: "Boîte à images couleur", 0x27: "Arc",
    0x28: "Bracelets de force", 0x29: "Bottes de plomb", 0x2A: "Armure magique", 0x2C: "Sac à appâts",
    0x2D: "Boomerang", 0x2F: "Grappin-griffe", 0x30: "Sac de courrier", 0x31: "Bombes",
    0x33: "Marteau", 0x34: "Feuille Mojo", 0x35: "Arc (feu et glace)", 0x36: "Arc (lumière)",
    0x50: "Flacon vide",
}
CHANTS = ("Requiem du Vent", "Ballade des Vents", "Chant de commandement", "Hymne de la Terre",
          "Aria du Vent", "Chant du Passage")          # bits de mTact [HYPO : ordre]
PERLES = ("Nayru", "Din", "Farore")                     # bits de mSymbol [HYPO : ordre]
DONJONS = {2: "Forteresse Maudite", 3: "Caverne du Dragon", 4: "Bois Défendus", 5: "Tour des Dieux",
           6: "Temple de la Terre", 7: "Temple du Vent", 8: "Tour de Ganon"}   # [HYPO sauf 3]
EV_RESUME = range(0x00, 0x60)     # octets d'événement comptés (comme DEFAULT_ALLOW, hors registres)


def _bits(v: int) -> list[int]:
    return [i for i in range(8) if v >> i & 1]


def resume(img_corr: bytes) -> dict:
    """Résumé lisible d'une image corrigée (gi+0x20 .. 0x7C0)."""
    g = gi_depuis_image(img_corr)
    mx, life, rup = struct.unpack_from(">HHH", g, GI_MAX_LIFE)
    objets = sorted({b for b in g[GI_ITEM:GI_ITEM + ITEM_SIZE] if b != 0xFF})
    ev = {(o, i) for o in EV_RESUME for i in _bits(g[GI_EVENT + o])}
    cartes = sum(bin(x).count("1") for x in struct.unpack_from(">4I", g, GI_MAP))
    iles = sum(1 for b in g[GI_FMAP:GI_FMAP + N_ISLES] if b)
    donj = {}
    for n, nom in DONJONS.items():
        d = g[GI_MEMORY + n * MEM_SIZE + MEM_DUNGEON]
        if d & 0x08:
            donj[nom] = "boss vaincu"
        elif d & 0x07:
            donj[nom] = "commencé"
    return {"coeurs": mx // 4, "vie": life / 4, "rubis": rup, "objets": objets,
            "chants": _bits(g[GI_TACT]), "perles": _bits(g[GI_SYMBOL]),
            "triforce": len(_bits(g[GI_TRIFORCE])), "cartes": cartes, "iles": iles,
            "fleches": g[GI_ARROWS], "bombes": g[GI_BOMBS],
            "evenements": ev, "donjons": donj,
            "memoires": {(o, i) for o in range(MEM_SIZE * N_MEM) for i in _bits(g[GI_MEMORY + o])
                         if o % MEM_SIZE != MEM_KEYNUM}}


def nom_objet(i: int) -> str:
    return NOMS_OBJETS.get(i, f"objet 0x{i:02X}")


def diff_resume(avant: dict, apres: dict) -> dict:
    """Ce qui est gagné et perdu ; ``regression`` vrai si J2 perd quelque chose d'important."""
    d = {
        "objets_plus": [i for i in apres["objets"] if i not in avant["objets"]],
        "objets_moins": [i for i in avant["objets"] if i not in apres["objets"]],
        "chants_plus": [i for i in apres["chants"] if i not in avant["chants"]],
        "chants_moins": [i for i in avant["chants"] if i not in apres["chants"]],
        "perles_plus": [i for i in apres["perles"] if i not in avant["perles"]],
        "perles_moins": [i for i in avant["perles"] if i not in apres["perles"]],
        "ev_plus": len(apres["evenements"] - avant["evenements"]),
        "ev_moins": len(avant["evenements"] - apres["evenements"]),
        "mem_plus": len(apres.get("memoires", set()) - avant.get("memoires", set())),
        "mem_moins": len(avant.get("memoires", set()) - apres.get("memoires", set())),
        "donjons_moins": [k for k, v in avant["donjons"].items()
                          if apres["donjons"].get(k) != v and not (v == "commencé" and apres["donjons"].get(k))],
    }
    d["regression"] = (apres["coeurs"] < avant["coeurs"] or bool(d["objets_moins"]) or bool(d["chants_moins"])
                       or bool(d["perles_moins"]) or apres["triforce"] < avant["triforce"] or d["ev_moins"] > 16
                       or bool(d["donjons_moins"]) or d["mem_moins"] > 16)
    return d


def _noms(lst, table) -> str:
    return ", ".join(table[i] if isinstance(table, tuple) and i < len(table) else
                     (nom_objet(i) if table is None else f"#{i}") for i in lst)


def tableau(avant: dict, apres: dict, hote: str) -> str:
    """Tableau « ta partie / après la copie » affiché avant la question."""
    d = diff_resume(avant, apres)
    L = [f"{'':24s}{'TA PARTIE':19s}APRÈS LA COPIE ({hote})"]

    def ligne(nom, a, b, extra=""):
        L.append(f"{nom:24s}{str(a):19s}{str(b):5s}{('  ' + extra) if extra else ''}")

    ligne("Cœurs", f"{avant['coeurs']} (vie {avant['vie']:g})", f"{apres['coeurs']} (vie {apres['vie']:g})")
    ligne("Rubis", avant["rubis"], apres["rubis"])
    ligne("Flèches / bombes", f"{avant['fleches']} / {avant['bombes']}", f"{apres['fleches']} / {apres['bombes']}")
    ex = ("+ " + _noms(d["objets_plus"], None)) if d["objets_plus"] else ""
    if d["objets_moins"]:
        ex += ("  " if ex else "") + "- " + _noms(d["objets_moins"], None)
    ligne("Objets", len(avant["objets"]), len(apres["objets"]), ex)
    ex = ("+ " + _noms(d["chants_plus"], CHANTS)) if d["chants_plus"] else ""
    if d["chants_moins"]:
        ex += ("  " if ex else "") + "- " + _noms(d["chants_moins"], CHANTS)
    ligne("Chants", len(avant["chants"]), len(apres["chants"]), ex)
    ex = ("+ " + _noms(d["perles_plus"], PERLES)) if d["perles_plus"] else ""
    if d["perles_moins"]:
        ex += ("  " if ex else "") + "- " + _noms(d["perles_moins"], PERLES)
    ligne("Perles", len(avant["perles"]), len(apres["perles"]), ex)
    ligne("Triforce", avant["triforce"], apres["triforce"])
    ligne("Cartes", avant["cartes"], apres["cartes"])
    ligne("Îles cartographiées", avant["iles"], apres["iles"])
    ligne("Flags d'événement", len(avant["evenements"]), len(apres["evenements"]),
          f"(+{d['ev_plus']}, -{d['ev_moins']})")
    if "memoires" in avant:
        ligne("Mémoires de stage", len(avant["memoires"]), len(apres["memoires"]),
              f"(+{d['mem_plus']}, -{d['mem_moins']}) coffres, portes, switches")
    dj = "; ".join(f"{k} : {v}" for k, v in apres["donjons"].items()) or "—"
    ligne("Donjons", "; ".join(f"{k} : {v}" for k, v in avant["donjons"].items()) or "—", "", dj)
    if d["donjons_moins"]:
        L.append(f"{'':24s}PERDU : {', '.join(d['donjons_moins'])}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Format fichier : emplacement compacté de cking.sav (§9)
# ---------------------------------------------------------------------------
def somme_controle(data: bytes) -> tuple[int, int]:
    s = sum(data) & 0xFFFFFFFF
    return s, (-(s + len(data))) & 0xFFFFFFFF


def lire_emplacement(fichier: bytes, k: int) -> bytes:
    if not 0 <= k < N_SLOTS:
        raise ValueError(f"emplacement {k} hors limites (0 à {N_SLOTS - 1})")
    a = k * SLOT_STRIDE
    if len(fichier) < a + SLOT_STRIDE:
        raise ValueError("cking.sav trop court")
    return fichier[a:a + SLOT_DATA]


def controle_ok(fichier: bytes, k: int) -> bool:
    a = k * SLOT_STRIDE
    data = fichier[a:a + SLOT_DATA]
    return struct.unpack_from(">II", fichier, a + SLOT_DATA) == somme_controle(data)


def emplacement_vers_image(slot: bytes) -> bytes:
    """Emplacement compacté -> image gi+0x20 .. 0x7C0 (bourrage à zéro ; pas de stage chargée :
    gi+0x798 = 0 et mDan.mStageNo = -1)."""
    lay, _end = disposition_fichier()
    g = bytearray(IMG_END)
    for _nom, gi_off, f_off, t in lay:
        g[gi_off:gi_off + t] = slot[f_off:f_off + t]
    g[GI_DAN] = 0xFF
    return bytes(g[IMG_BASE:])


def image_vers_emplacement(img: bytes, slot_ref: bytes) -> bytes:
    """Image -> emplacement compacté ; les octets propres à HD (0x768 .. 0xA8C) de ``slot_ref``
    sont gardés."""
    lay, end = disposition_fichier()
    g = gi_depuis_image(img)
    out = bytearray(slot_ref[:SLOT_DATA])
    for _nom, gi_off, f_off, t in lay:
        out[f_off:f_off + t] = g[gi_off:gi_off + t]
    return bytes(out)


def emplacements_par_date(fichier: bytes, date_ipl: bytes) -> list[int]:
    """Emplacements dont status_b.mDateIPL (fichier +0x018) vaut ``date_ipl`` (gi+0x38)."""
    out = []
    for k in range(N_SLOTS):
        try:
            s = lire_emplacement(fichier, k)
        except ValueError:
            break
        if s[SLOT_DATE_IPL:SLOT_DATE_IPL + 8] == date_ipl and any(date_ipl):
            out.append(k)
    return out


def emplacements_non_vides(fichier: bytes) -> list[int]:
    out = []
    for k in range(N_SLOTS):
        try:
            s = lire_emplacement(fichier, k)
        except ValueError:
            break
        if any(s[SLOT_DATE_IPL:SLOT_DATE_IPL + 8]):
            out.append(k)
    return out


def fusion_fichier(fichier: bytes, k: int, img_corr: bytes, options: Options | None = None) -> bytes:
    """Nouveau contenu de cking.sav : emplacement ``k`` remplacé selon les règles §5.1 (sans
    stage courante), somme recalculée ; les autres emplacements et le bloc commun inchangés."""
    slot = lire_emplacement(fichier, k)
    gi_j2 = gi_depuis_image(emplacement_vers_image(slot))
    plan = plan_ecriture(img_corr, gi_j2, options, courante=False)
    new_gi = appliquer_plan_octets(gi_j2, plan)
    new_slot = image_vers_emplacement(new_gi[IMG_BASE:IMG_END], slot)
    a = k * SLOT_STRIDE
    out = bytearray(fichier)
    out[a:a + SLOT_DATA] = new_slot
    struct.pack_into(">II", out, a + SLOT_DATA, *somme_controle(new_slot))
    return bytes(out)


OSTIME_HZ = 62_156_250                # horloge OSTime de la Wii U (bus 248,625 MHz / 4)
OSTIME_EPOQUE = 946684800             # 2000-01-01 00:00:00 (UTC), en secondes Unix


def date_ipl_maintenant(t: float | None = None) -> bytes:
    """mDateIPL (u64 big-endian, OSTime) pour l'instant ``t`` (secondes Unix ; maintenant par défaut)."""
    t = time.time() if t is None else t
    return struct.pack(">Q", max(1, int((t - OSTIME_EPOQUE) * OSTIME_HZ)))


def copie_integrale(fichier: bytes, k: int, img_corr: bytes) -> bytes:
    """A16 (menu Coop, menu_coop.md §8.5) : emplacement ``k`` = COPIE INTÉGRALE de l'image de
    l'hôte (tous les champs compactés, point de reprise et nom compris : le Journal démarre là où
    l'hôte a sauvegardé). Les octets propres à HD (0x768..0xA8C) de l'emplacement ``k`` sont
    gardés (vides pour un Journal vide [HYPO M1-F : reconnu tel quel par les Journaux]). Somme
    recalculée ; autres emplacements et bloc commun inchangés."""
    slot = lire_emplacement(fichier, k)
    new_slot = bytearray(image_vers_emplacement(img_corr, slot))
    if not any(new_slot[SLOT_DATE_IPL:SLOT_DATE_IPL + 8]):
        # vu le 2026-11-13 : partie neuve de l'hôte jamais sauvegardée -> mDateIPL nul (le jeu ne le pose qu'à la
        # première sauvegarde). Un Journal à date nulle serait vu comme vide : on y met la date de maintenant, au
        # format OSTime de la Wii U (ticks à 62 156 250 Hz depuis le 1er janvier 2000).
        new_slot[SLOT_DATE_IPL:SLOT_DATE_IPL + 8] = date_ipl_maintenant()
    new_slot = bytes(new_slot)
    a = k * SLOT_STRIDE
    out = bytearray(fichier)
    out[a:a + SLOT_DATA] = new_slot
    struct.pack_into(">II", out, a + SLOT_DATA, *somme_controle(new_slot))
    return bytes(out)


def fichier_vide(n_commun: int = 0x34A) -> bytes:
    """Fichier de 3 Journaux vides (sommes justes) + bloc commun à zéro : tests et jeu simulé
    seulement (le vrai est construit par le jeu, 0x027226B4 / 0x0272276C)."""
    out = bytearray(N_SLOTS * SLOT_STRIDE + n_commun)
    for k in range(N_SLOTS):
        struct.pack_into(">II", out, k * SLOT_STRIDE + SLOT_DATA, *somme_controle(bytes(SLOT_DATA)))
    return bytes(out)


# ---------------------------------------------------------------------------
# Jeu simulé (tests, --fake)
# ---------------------------------------------------------------------------
FAKE_LINK = 0x47C48E14
FAKE_SHIP = 0x47D00000


def make_fake_save_game(stage: str = "sea", room: int = 13, stage_idx: int = 0, max_life: int = 12,
                        life: int | None = None, rupees: int = 0, date_ipl: bytes = b"\x00\xbb\x24\x1f\xd5\x95\xf7\xde"):
    """``progress.make_fake_game`` + sauvegarde plausible (vie, constantes 66 67), segment gi
    de 0x2000 octets, Link (mEquipItem = rien, immobile), pointeurs joueur et salle courante."""
    m = prog.make_fake_game(stage, room, stage_idx)
    old = m.segs[prog.FAKE_GI]
    m.segs[prog.FAKE_GI] = bytearray(old) + bytearray(GI_DUMP - len(old))
    m.add(FAKE_LINK, 0x7000)
    m.add(FAKE_SHIP, 0x400)
    m.add(SYM_PLAYER_PTRS, 12)
    m.add(SYM_STAY_NO, 1)
    m.add(SYM_DMENU_FLAG, 1)
    gi = prog.FAKE_GI
    m.write(gi + GI_MAX_LIFE, struct.pack(">HHH", max_life, max_life if life is None else life, rupees))
    m.write(gi + GI_DATE_IPL, date_ipl)
    m.write(gi + GI_RETURN_PLACE, stage.encode()[:8].ljust(8, b"\0") + bytes([room & 0xFF, 0]) + b"\x66\x67")
    m.write(gi + GI_ITEM, b"\xff" * ITEM_SIZE)
    m.write(FAKE_LINK + LINK_EQUIP_ITEM, struct.pack(">H", EQUIP_RIEN))
    m.write(SYM_PLAYER_PTRS, struct.pack(">3I", FAKE_LINK, 0, 0))
    m.write(SYM_STAY_NO, bytes([room & 0xFF]))
    m.writes.clear()

    def tick() -> None:            # Link « animé » : un octet change à chaque lecture (voir link_anim)
        s, o = m._seg(FAKE_LINK + LINK_ANIM[0], 1)
        s[o] = (s[o] + 1) & 0xFF
    m.tick_anim = tick
    return m
