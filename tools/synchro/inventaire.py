"""Synchro v2, module 3 : inventaire partagé, phase 1 (--share-inventory, activé par --coop-donjon,
--no-inventory).

Étude : docs/etudes/sync2_inventaire.md (la section « Vérification » finale prime sur le texte
d'avant). Ce module couvre l'ordre recommandé, étapes 2 à 6, SANS compteurs communs :

* Ce qui est partagé (union : obtenu par l'un = obtenu par tous, jamais retiré par le réseau) :
    - A. objets d'emplacement (longue-vue, voile < voile rapide, Baguette, grappin, sac à butin,
      boomerang, feuille Mojo, emplacement 7 HD, boîte à images < couleur, bottes, cape, sac à
      appâts, arc < flèches de feu et glace < flèches de lumière, bombes, sac de livraison,
      grappin-griffe, marteau), par RANG, avec le cache des boutons X/Y/R (amélioration dans le même
      emplacement) et les boutons dédiés de la Baguette et de la voile ;
    - B. nombre de flacons (max des comptes, au plus 4) ; le CONTENU reste à chacun ;
    - C. épées, boucliers, bracelets, charmes (bits ``mCollect`` + ``mSelectEquip``) ;
    - D. bits OU : chants, Triforce, perles, cartes marines, îles cartographiées, drapeaux
      « obtenu » des sacs (``protocol.INV_BIT_MASKS``) ;
    - E. plafonds (max) : bourse, carquois, sac de bombes, magie max (lue AVEC l'attente
      ``play+0x5B64``, vérificateur R23 : sinon feuille Mojo + plafond = 32 au lieu de 16) ;
    - H. sac de livraison : état répliqué (ajouts et retraits : échanges de quête).
* Ce qui ne l'est PAS (phase suivante, étude §5 étape 4 bis : il faut d'abord A4 sans recopie des
  consommables et les sources uniques partagées sans compteur, sinon revenus doublés R5/R18-R20) :
  rubis, flèches, bombes, butin du sac à butin (comptes), vie max (quarts, réceptacles), magie et vie
  actuelles, contenu des flacons, sac à appâts (phase 3), boutons X/Y/R (chacun les siens), photos,
  souvenirs des boss (``ev 0x3D/0xF0`` refusés, ``progress.SOUVENIRS_DENY``).
  Depuis : rubis, flèches et bombes sont communs par un module à part, synchro/compteurs.py (--share-rupees,
  --share-ammo, A4 « premier arrivé », coffres à compteur en bit seul) ; butin et vie max restent à chacun.

Contrat « propriétaire unique » (R19, docs/dev/SYNCHRO.md) : ce module est le SEUL à donner les objets
d'inventaire et, plus tard, les compteurs. Les coffres (synchro/coffres.py) ne posent que le BIT d'un
coffre dont l'objet est déjà arrivé ici (``InventaireSync.owned``) ; le GRANT du pack v19 ne devra
jamais toucher un compteur quand ce module est actif.

Écriture (phase 1, pack v17) : écriture directe Python, copie fidèle des ``item_func`` HD (table
``execItemGet`` 0x101E3A98, relue en désassemblage : ``onGetItem(emplacement, bit)`` sur gi+0x71,
valeur dans gi+0x5C+emplacement, ``onCollect`` sur gi+0xD4 puis ``setSelectEquip``). Chaque écriture
relit l'octet juste avant (verrou ``progress._write_lock``) et la vérifie après. Point d'accroche
pack v19 : ``InventaireSync.item_get = fn(itemNo) -> int`` (CALL ``execItemGet``, liste blanche
``ITEM_GET_OK``), branché par le client (``Mailbox.item_get``) seulement si la boîte installée est en
v19 ; None avec un pack v17 (mode dégradé : écriture directe).

Jamais : 0x08 (réceptacle : stage COURANTE), 0x15 (petite clé, R19), 0x2E (main nue : écrase les
bracelets), 0x3D, 0x4C-0x4E (objets de donjon : stage courante) ; gi+0xB6-0xBB jamais écrits.

Gardes (§3.5) : rien n'est lu ni écrit sur l'écran titre, pendant un changement de stage (puis 2 s
de stabilité), dans ``Xboss0-3`` (souvenirs) ; rien n'est écrit pendant un événement (anti-rebond
``progress.EventWatch``). Retraits faits par le jeu (épée en Forteresse 1…) : jamais envoyés ni
redonnés tant que le jeu ne redonne pas l'objet (``retires``) ; l'arc est jugé sur ses bits (la Tour
de Ganon vide l'emplacement 12 mais garde les bits) et ses améliorations sont différées dans
``GTower``. Épées et boucliers : pas pendant que Link tient l'épée (``Link+0x69B0 == 0x103``, [HYPO]
M5/M11), échéance ``EPEE_DELAI`` s puis attribution quand même (vérificateur R24). Retraits du sac
de livraison : seulement si Link n'est pas figé (menu ouvert, [HYPO]). Rechargement de partie
(gi, ``mDateIPL`` ou écran titre) : base recalée, ``full`` renvoyé, aucun retrait déduit.
"""
from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass, field

from jeu import mailbox
from jeu import progress as prog
from jeu import savecopy
from synchro import cles
from commun.protocole import INV_BIT_MASKS, INV_CAPS, INV_FLACONS_MAX, INV_ITEMS, INV_SAC, INV_SAC_MAX, valid_inv

# ---------------------------------------------------------------------------
# Adresses [HD-ASM + DECOMP + LIVE, étude §2]
# ---------------------------------------------------------------------------
GI_BASE = 0x20                    # lecture gi+0x20 .. gi+0x168 (une lecture)
GI_LEN = 0x148
GI_SELECT_ITEM = 0x29             # mSelectItem[5] : X, Y, R, [3] Baguette, [4] voile (n° d'emplacement)
GI_SELECT_EQUIP = 0x2E            # mSelectEquip[4] : épée, bouclier, bracelets
GI_WALLET, GI_MAX_MAGIC = 0x32, 0x33
GI_DATE_IPL = savecopy.GI_DATE_IPL   # u64 status_b.mDateIPL : identifie la partie
GI_ITEM = savecopy.GI_ITEM        # mItems[21]
GI_ITEM_FLAGS = savecopy.GI_GET_ITEM   # mItemFlags[21] (onGetItem)
GI_ARROWS, GI_BOMBS = savecopy.GI_ARROWS, savecopy.GI_BOMBS
GI_ARROW_MAX, GI_BOMB_MAX = 0x8F, 0x90
GI_RESERVE = 0xA6                 # mReserve[8] (sac de livraison)
GI_COLLECT = savecopy.GI_COLLECT  # mCollect[8]
PLAY = prog.SYM_PLAY              # 0x1046F0B0
PLAY_BASE = prog.SYM_PLAY_ITEMS   # play+0x5B40
PLAY_LEN = prog.PLAY_ITEMS_SIZE
P_MAX_MAGIC = 0x5B64              # s16 mItemMaxMagicCount (attente, appliquée par d_meter)
P_SELECT_ITEM = 0x5BBB            # u8[5] cache : n° d'objet par bouton (X, Y, R, Baguette, voile)
SLOTS_FLACONS = range(14, 18)
FLACON_VIDE = 0x50
FLACON_BIT = 0x04                 # onBottleItem(0x50) : mItemFlags[0] |= 0x04 [HD-ASM 0x025B5E34]
SAC_BOUTON = 0x30                 # n° d'emplacement d'un bouton pointant mReserve[k] = 0x30 + k [HD-ASM]
EQUIP_EPEE = savecopy.EQUIP_EPEE  # 0x103 [HYPO] (Link+0x69B0)
STAGES_SOUVENIRS = frozenset({"Xboss0", "Xboss1", "Xboss2", "Xboss3"})
STAGE_GTOWER = "GTower"

# A. emplacement -> ((itemNo, bit onGetItem, bit lu pour la détection ?), ...) du rang le plus bas au
# plus haut. Bits [HD-ASM, table des item_func] ; détection par les bits seulement là où ils ne sont
# pas ambigus (§3.2 : mItemFlags[0..2] bits >= 1 = contenus de flacon ; [1] bit 1 = voile rapide ET
# flacon d'eau, collision confirmée 0x0254F5F0 / 0x025B5E94).
SLOTS: dict[int, tuple[tuple[int, int, bool], ...]] = {
    0: ((0x20, 0, True),),
    1: ((0x78, 0, False), (0x77, 1, False)),
    2: ((0x22, 0, True),),
    3: ((0x25, 0, True),),
    4: ((0x24, 0, False),),
    5: ((0x2D, 0, False),),
    6: ((0x34, 0, False),),
    7: ((0x21, 0, False),),
    8: ((0x23, 0, True), (0x26, 1, True)),
    9: ((0x29, 0, False),),
    10: ((0x2A, 0, False),),
    11: ((0x2C, 0, False),),
    12: ((0x27, 0, True), (0x35, 1, True), (0x36, 2, True)),
    13: ((0x31, 0, True),),
    18: ((0x30, 0, False),),
    19: ((0x2F, 0, False),),
    20: ((0x33, 0, False),),
}
SLOT_OF = {no: (slot, rank) for slot, ranks in SLOTS.items() for rank, (no, _b, _d) in enumerate(ranks)}
SLOT_ARC = 12
# C. itemNo -> (index de mCollect, bit, index de mSelectEquip ou None) [HD-ASM 0x0254EE14…0x0254EFEC]
COLLECT: dict[int, tuple[int, int, int | None]] = {
    0x38: (0, 0, 0), 0x39: (0, 1, 0), 0x3A: (0, 2, 0), 0x3E: (0, 3, 0),
    0x3B: (1, 0, 1), 0x3C: (1, 1, 1),
    0x28: (2, 0, 2), 0x42: (3, 0, None), 0x43: (4, 0, None)}
FAMILLES = {0: (0x38, 0x39, 0x3A, 0x3E), 1: (0x3B, 0x3C), 2: (0x28,)}   # par index d'équipement
INTERDITS = frozenset({0x08, cles.ITEM_SMALL_KEY, 0x2E, 0x3D, 0x4C, 0x4D, 0x4E})
# pack v19 : jamais par execItemGet même quand le point d'accroche est branché (vérificateur §3.7) :
# 0x34 = +16 de magie max non idempotent (la magie max passe par E seule)
ITEM_GET_JAMAIS = frozenset({0x34})
# objets donnés par le point d'accroche v19 (liste blanche du pack, jeu/mailbox.GIVE_OK) ; les autres
# restent en écriture directe
ITEM_GET_OK = frozenset(mailbox.GIVE_OK - mailbox.NEVER_GIVE - ITEM_GET_JAMAIS)
assert not (INTERDITS & INV_ITEMS)
assert set(SLOT_OF) | set(COLLECT) == set(INV_ITEMS)

NOMS = {0x20: "Longue-vue", 0x21: "Objet de l'emplacement 7 (HD)", 0x22: "Baguette du vent",
        0x23: "Boîte à images", 0x24: "Sac à butin", 0x25: "Grappin", 0x26: "Boîte à images couleur",
        0x27: "Arc", 0x28: "Bracelets de force", 0x29: "Bottes de plomb", 0x2A: "Cape magique",
        0x2C: "Sac à appâts", 0x2D: "Boomerang", 0x2F: "Grappin-griffe", 0x30: "Sac de livraison",
        0x31: "Sac de bombes", 0x33: "Marteau", 0x34: "Feuille Mojo", 0x35: "Flèches de feu et de glace",
        0x36: "Flèches de lumière", 0x38: "Épée du héros", 0x39: "Excalibur (sans pouvoir)",
        0x3A: "Excalibur (à moitié)", 0x3E: "Excalibur", 0x3B: "Bouclier du héros", 0x3C: "Bouclier miroir",
        0x42: "Pierre des pirates", 0x43: "Pendentif du héros", 0x77: "Voile rapide", 0x78: "Voile",
        FLACON_VIDE: "Flacon vide"}
CAP_NOMS = {"w": "bourse", "am": "carquois", "bm": "sac de bombes", "mm": "magie max"}


def nom(no: int) -> str:
    if no in NOMS:
        return NOMS[no]
    if 0x8C <= no <= 0x9E:
        return f"objet d'échange 0x{no:02X}"
    return f"objet 0x{no:02X}"


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def arc_ok(no: int) -> bool:
    return SLOT_OF.get(no, (None,))[0] == SLOT_ARC


# ---------------------------------------------------------------------------
# État (fonction pure : testable sur des octets)
# ---------------------------------------------------------------------------
@dataclass
class Etat:
    acq: set = field(default_factory=set)          # jetons A et C
    bits: dict = field(default_factory=dict)       # gi_off -> octet (D, masques appliqués)
    cap: dict = field(default_factory=dict)        # E : w, am, bm, mm
    fl: int = 0                                    # B : nombre de flacons
    sac: list = field(default_factory=list)        # H : objets du sac de livraison (triés)
    mm_attente: int = 0                            # attente play+0x5B64 (lue)

    def copie(self) -> "Etat":
        return Etat(set(self.acq), dict(self.bits), dict(self.cap), self.fl,
                    None if self.sac is None else list(self.sac), self.mm_attente)


def _g(gi_bytes: bytes, off: int, n: int = 1) -> bytes:
    return gi_bytes[off - GI_BASE: off - GI_BASE + n]


def rang_emplacement(slot: int, valeur: int, drapeaux: int) -> int:
    """Rang (-1 = rien) de l'emplacement : max(rang de la valeur, rang des bits non ambigus)."""
    r = -1
    for i, (no, bit, lu) in enumerate(SLOTS[slot]):
        if valeur == no or (lu and drapeaux >> bit & 1):
            r = max(r, i)
    return r


def etat_depuis(gi_bytes: bytes, play_bytes: bytes | None) -> Etat:
    """Extraction (§3.2) depuis gi+0x20..0x168 et play+0x5B40..0x5BD0 (None : attente inconnue = 0)."""
    if len(gi_bytes) < GI_LEN:
        raise ValueError("lecture de la sauvegarde trop courte")
    e = Etat()
    items = _g(gi_bytes, GI_ITEM, 21)
    flags = _g(gi_bytes, GI_ITEM_FLAGS, 21)
    for slot, ranks in SLOTS.items():
        r = rang_emplacement(slot, items[slot], flags[slot])
        e.acq |= {no for no, _b, _d in ranks[:r + 1]}
    collect = _g(gi_bytes, GI_COLLECT, 8)
    for no, (idx, bit, _eq) in COLLECT.items():
        if collect[idx] >> bit & 1:
            e.acq.add(no)
    for off, mask in INV_BIT_MASKS.items():
        v = _g(gi_bytes, off)[0] & mask
        if v:
            e.bits[off] = v
    att = 0
    if play_bytes is not None and len(play_bytes) >= PLAY_LEN:
        att = struct.unpack_from(">h", play_bytes, P_MAX_MAGIC - 0x5B40)[0]
    e.mm_attente = att
    raw = {"w": _g(gi_bytes, GI_WALLET)[0], "am": _g(gi_bytes, GI_ARROW_MAX)[0],
           "bm": _g(gi_bytes, GI_BOMB_MAX)[0], "mm": _g(gi_bytes, GI_MAX_MAGIC)[0] + att}
    e.cap = {k: v for k, v in raw.items() if v in INV_CAPS[k]}
    e.fl = sum(1 for s in SLOTS_FLACONS if items[s] != 0xFF)
    e.sac = sorted(b for b in _g(gi_bytes, GI_RESERVE, INV_SAC_MAX) if b in INV_SAC)
    return e


@dataclass
class Lecture:
    ok: bool
    raison: str = ""
    gi: int = 0
    date: bytes = b""
    stage: str = ""
    pause: str = ""                # "" = jeu lisible et stable (pas de changement de stage, pas Xboss)
    etat: Etat | None = None

    @property
    def contexte(self) -> tuple:
        return (self.gi, self.date)


def lire(mem) -> Lecture:
    r = savecopy.partie_chargee(mem)
    if r:
        return Lecture(False, r)
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    gi = struct.unpack(">I", raw)[0] if raw else 0
    st = mem.try_read(prog.SYM_CUR_STAGE, 12)
    stage = st[:8].split(b"\0", 1)[0].decode("ascii", "replace") if st else ""
    blob = mem.try_read(gi + GI_BASE, GI_LEN)
    player = mem.try_read(prog.SYM_PLAYER, 4)
    if blob is None or player is None:
        return Lecture(False, "sauvegarde illisible", gi)
    if struct.unpack(">I", player)[0] == 0:
        return Lecture(False, "Link absent (chargement)", gi)
    play = mem.try_read(PLAY_BASE, PLAY_LEN)
    lec = Lecture(True, "", gi, _g(blob, GI_DATE_IPL, 8), stage, "", etat_depuis(blob, play))
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        lec.pause = "changement de stage en cours"
    elif stage in STAGES_SOUVENIRS:
        lec.pause = f"combat de souvenir ({stage}) : inventaire du jeu remplacé, rien n'est lu ni écrit"
    return lec


def etat_local(mem) -> Etat:
    """Relecture complète de l'état (juste avant une attribution non idempotente)."""
    raw = mem.read(prog.SYM_GAMEINFO_PTR, 4)
    gi = struct.unpack(">I", raw)[0]
    return etat_depuis(mem.read(gi + GI_BASE, GI_LEN), mem.try_read(PLAY_BASE, PLAY_LEN))


# ---------------------------------------------------------------------------
# Écritures (phase 1 : copie des item_func HD ; chacune relit avant et vérifie après)
# ---------------------------------------------------------------------------
class EcritureRefusee(Exception):
    pass


def _rd(mem, va: int) -> int:
    return mem.read(va, 1)[0]


def _ecrire(mem, va: int, val: int) -> None:
    with prog._write_lock:
        mem.write(va, bytes([val & 0xFF]))
    if _rd(mem, va) != val & 0xFF:
        raise EcritureRefusee(f"relecture différente à 0x{va:08X}")


def _ou(mem, va: int, mask: int) -> None:
    prog._rmw(mem, va, mask)
    if _rd(mem, va) & mask != mask:
        raise EcritureRefusee(f"bit non posé à 0x{va:08X}")


def _boutons(mem, gi: int, slot: int, no: int) -> int:
    """Cache des boutons X/Y/R qui pointent l'emplacement ``slot`` -> ``no`` (boucle HD 0x0254E9D8 :
    3 boutons). Renvoie le nombre de boutons mis à jour."""
    n = 0
    sel = mem.read(gi + GI_SELECT_ITEM, 3)
    for b in range(3):
        if sel[b] == slot:
            _ecrire(mem, PLAY + P_SELECT_ITEM + b, no)
            n += 1
    return n


def donner_objet(mem, gi: int, no: int) -> bool:
    """Jeton A (objet d'emplacement) : valeur + bit onGetItem + effets de l'item_func HD. False si
    déjà là (rang local suffisant). EcritureRefusee si l'emplacement contient autre chose."""
    if no in INTERDITS or no not in SLOT_OF:
        raise EcritureRefusee(f"objet 0x{no:02X} hors liste")
    slot, rank = SLOT_OF[no]
    _no, bit, _lu = SLOTS[slot][rank]
    cur = _rd(mem, gi + GI_ITEM + slot)
    ranks = [x[0] for x in SLOTS[slot]]
    if cur != 0xFF and cur not in ranks:
        raise EcritureRefusee(f"emplacement {slot} : valeur inattendue 0x{cur:02X}")
    cur_rank = ranks.index(cur) if cur in ranks else -1
    if cur_rank >= rank:
        _ou(mem, gi + GI_ITEM_FLAGS + slot, 1 << bit)
        return False
    _ou(mem, gi + GI_ITEM_FLAGS + slot, 1 << bit)              # onGetItem(slot, bit)
    _ecrire(mem, gi + GI_ITEM + slot, no)
    if no == 0x27:                                             # item_func_bow 0x0254E630
        _plein(mem, gi, GI_ARROWS, GI_ARROW_MAX, 30)
    elif no == 0x31:                                           # item_func_bomb_bag 0x0254E888
        _plein(mem, gi, GI_BOMBS, GI_BOMB_MAX, 30)
    elif no == 0x22:                                           # Baguette 0x0254E118 : bouton dédié [3]
        _ecrire(mem, gi + GI_SELECT_ITEM + 3, 2)
        _ecrire(mem, PLAY + P_SELECT_ITEM + 3, no)
    elif no in (0x78, 0x77):                                   # voile 0x0254F7D0 / 0x0254F5D0 : [4]
        _ecrire(mem, gi + GI_SELECT_ITEM + 4, 1)
        _ecrire(mem, PLAY + P_SELECT_ITEM + 4, no)
    if cur_rank >= 0:
        _boutons(mem, gi, slot, no)                            # amélioration : cache des boutons
    return True


def reparer_arc(mem, gi: int) -> bool:
    """Randomizer, objets progressifs (lot 4 du portage) : la case de l'arc remise à son meilleur palier acquis.
    Les fonctions d'objet du jeu ÉCRIVENT la case sans regarder ce qu'elle contient : un deuxième coffre « arc du
    héros » (créé avant que le compte commun ne change), ou le cadeau de la Reine des fées (flèches de feu et de
    glace), font redescendre une case qui avait déjà les flèches de lumière. Le bit d'acquisition, lui, reste : si
    le rang des bits dépasse celui de la valeur, la valeur est réécrite (et le cache des boutons). Jamais si la case
    est vide ou contient autre chose (Tour de Ganon : arc retiré par le jeu). Renvoie True si la case a été écrite."""
    ranks = [x[0] for x in SLOTS[SLOT_ARC]]
    cur = _rd(mem, gi + GI_ITEM + SLOT_ARC)
    if cur not in ranks:
        return False
    r = rang_emplacement(SLOT_ARC, cur, _rd(mem, gi + GI_ITEM_FLAGS + SLOT_ARC))
    if r <= ranks.index(cur):
        return False
    _ecrire(mem, gi + GI_ITEM + SLOT_ARC, ranks[r])
    _boutons(mem, gi, SLOT_ARC, ranks[r])
    return True


def reparer_bouclier(mem, gi: int) -> bool:
    """Randomizer, boucliers progressifs (option « boucliers_progressifs ») : le bouclier PORTÉ (mSelectEquip[1])
    remis au bouclier miroir quand le bit du miroir (mCollect[1] bit 1) est acquis et que le bras porte le bouclier
    du héros. Filet : l'item_func 0x3B du pack généré ne rétrograde plus, mais setSelectEquip (0x02522398) écrit sans
    comparer, et rien ne prouve qu'aucune autre voie du jeu ne l'appelle avec 0x3B. Jamais si le bras est vide (le jeu
    a pu retirer le bouclier) ni pour un autre objet. Renvoie True si l'octet a été écrit."""
    idx, bit, eq = COLLECT[0x3C]
    if not _rd(mem, gi + GI_COLLECT + idx) >> bit & 1:
        return False
    if _rd(mem, gi + GI_SELECT_EQUIP + eq) != 0x3B:
        return False
    _ecrire(mem, gi + GI_SELECT_EQUIP + eq, 0x3C)
    return True


def _plein(mem, gi: int, off_n: int, off_max: int, val: int) -> None:
    if _rd(mem, gi + off_max) < val:
        _ecrire(mem, gi + off_max, val)
    if _rd(mem, gi + off_n) < val:
        _ecrire(mem, gi + off_n, val)


def donner_collect(mem, gi: int, no: int, retires: set) -> bool:
    """Jeton C : bit mCollect, puis mSelectEquip comme setSelectEquip HD 0x02522398 (épée / bouclier /
    bracelets : seulement si l'emplacement d'équipement est vide ou plus faible dans la même famille,
    et jamais si un objet de la famille a été retiré par le jeu)."""
    if no not in COLLECT:
        raise EcritureRefusee(f"objet 0x{no:02X} hors liste")
    idx, bit, eq = COLLECT[no]
    va = gi + GI_COLLECT + idx
    deja = bool(_rd(mem, va) >> bit & 1)
    _ou(mem, va, 1 << bit)
    if eq is not None and not (set(FAMILLES[eq]) & retires):
        fam = FAMILLES[eq]
        cur = _rd(mem, gi + GI_SELECT_EQUIP + eq)
        if cur == 0xFF or (cur in fam and fam.index(cur) < fam.index(no)):
            _ecrire(mem, gi + GI_SELECT_EQUIP + eq, no)
    return not deja


def ajouter_flacon(mem, gi: int, cible: int) -> bool:
    """Un flacon vide dans le premier emplacement 14-17 libre (setEmptyBottle 0x025B5454) + bit
    onBottleItem(0x50). Relit le nombre juste avant (R26 : jamais au-delà de la cible)."""
    items = mem.read(gi + GI_ITEM, 21)
    n = sum(1 for s in SLOTS_FLACONS if items[s] != 0xFF)
    if n >= min(cible, INV_FLACONS_MAX):
        return False
    s = next(s for s in SLOTS_FLACONS if items[s] == 0xFF)
    _ecrire(mem, gi + GI_ITEM + s, FLACON_VIDE)
    _ou(mem, gi + GI_ITEM_FLAGS + 0, FLACON_BIT)
    return True


def poser_bits(mem, gi: int, off: int, mask: int) -> bool:
    if off not in INV_BIT_MASKS or not mask or mask & ~INV_BIT_MASKS[off]:
        raise EcritureRefusee(f"bits hors liste (gi+0x{off:X}/0x{mask:02X})")
    if _rd(mem, gi + off) & mask == mask:
        return False
    _ou(mem, gi + off, mask)
    return True


def monter_plafond(mem, gi: int, k: str, val: int) -> bool:
    """E : bourse, carquois, sac de bombes (écriture directe ; carquois / sac remplis comme
    item_func 0xAD-0xB0). La magie max passe par ``monter_magie``."""
    if val not in INV_CAPS.get(k, ()):
        raise EcritureRefusee(f"plafond {k} = {val} hors bornes")
    off = {"w": GI_WALLET, "am": GI_ARROW_MAX, "bm": GI_BOMB_MAX}[k]
    cur = _rd(mem, gi + off)
    if cur >= val:
        return False
    _ecrire(mem, gi + off, val)
    if k in ("am", "bm") and cur > 0:
        _ecrire(mem, gi + (GI_ARROWS if k == "am" else GI_BOMBS), val)
    return True


def monter_magie(mem, gi: int, cible: int) -> bool:
    """E (magie max) : delta ajouté à l'attente play+0x5B64 (d_meter l'applique, comme item_func 0xB2),
    seulement si l'attente relue vaut 0 ; L = gi+0x33 + attente relu juste avant."""
    if cible not in INV_CAPS["mm"]:
        raise EcritureRefusee(f"magie max {cible} hors bornes")
    att = struct.unpack(">h", mem.read(PLAY + P_MAX_MAGIC, 2))[0]
    base = _rd(mem, gi + GI_MAX_MAGIC)
    if att != 0 or base >= cible:
        return False
    with prog._write_lock:
        mem.write(PLAY + P_MAX_MAGIC, struct.pack(">h", cible - base))
    if struct.unpack(">h", mem.read(PLAY + P_MAX_MAGIC, 2))[0] + _rd(mem, gi + GI_MAX_MAGIC) < cible:
        raise EcritureRefusee("magie max : attente non posée")
    return True


def sac_ajouter(mem, gi: int, no: int) -> bool:
    if no not in INV_SAC:
        raise EcritureRefusee(f"objet d'échange 0x{no:02X} hors liste")
    sac = mem.read(gi + GI_RESERVE, INV_SAC_MAX)
    if no in sac:
        return False
    if 0xFF not in sac:
        raise EcritureRefusee("sac de livraison plein")
    _ecrire(mem, gi + GI_RESERVE + sac.index(0xFF), no)       # setReserveItem 0x025B75AC
    return True


def sac_retirer(mem, gi: int, no: int) -> bool:
    """Retrait (échange fait chez un autre) : emplacement vidé, bouton qui le pointait vidé (comme
    setBeastItemEmpty : gi+0x29+b = 0xFF, cache 0xFF)."""
    sac = mem.read(gi + GI_RESERVE, INV_SAC_MAX)
    if no not in sac or no not in INV_SAC:
        return False
    k = sac.index(no)
    _ecrire(mem, gi + GI_RESERVE + k, 0xFF)
    sel = mem.read(gi + GI_SELECT_ITEM, 3)
    for b in range(3):
        if sel[b] == SAC_BOUTON + k:
            _ecrire(mem, gi + GI_SELECT_ITEM + b, 0xFF)
            _ecrire(mem, PLAY + P_SELECT_ITEM + b, 0xFF)
    return True


def link_equip(mem) -> int | None:
    raw = mem.try_read(prog.SYM_PLAYER, 4)
    link = struct.unpack(">I", raw)[0] if raw else 0
    if not 0x10000000 <= link < 0x50000000:
        return None
    r = mem.try_read(link + savecopy.LINK_EQUIP_ITEM, 2)
    return struct.unpack(">H", r)[0] if r else None


# ---------------------------------------------------------------------------
# Plan d'application (fonction pure)
# ---------------------------------------------------------------------------
def a_donner(local: Etat, commun: Etat, retires: set, flacons_perdus: int = 0) -> list[tuple]:
    """Actions dans l'ordre §3.5 : jetons A/C par rang, flacons, bits D, plafonds E, sac H (ajouts puis
    retraits). Le sac n'est pas comparé ici si ``commun.sac`` est None (pas encore reçu)."""
    out: list[tuple] = []
    manquants = commun.acq - local.acq - retires - INTERDITS
    for no in sorted((x for x in manquants if x in SLOT_OF), key=lambda x: SLOT_OF[x]):
        out.append(("objet", no))
    for no in sorted((x for x in manquants if x in COLLECT), key=lambda x: COLLECT[x][:2]):
        out.append(("collect", no))
    cible_fl = min(commun.fl, INV_FLACONS_MAX) - flacons_perdus
    if local.fl < cible_fl:
        out.append(("flacon", cible_fl))
    for off in sorted(commun.bits):
        m = commun.bits[off] & ~local.bits.get(off, 0) & INV_BIT_MASKS.get(off, 0)
        if m:
            out.append(("bits", off, m))
    for k in ("w", "am", "bm", "mm"):
        v = commun.cap.get(k)
        if _int(v) and v > local.cap.get(k, 0):
            out.append(("cap", k, v))
    if commun.sac is not None:
        for no in sorted(set(commun.sac) - set(local.sac)):
            out.append(("sac+", no))
        for no in sorted(set(local.sac) - set(commun.sac)):
            out.append(("sac-", no))
    return out


# ---------------------------------------------------------------------------
# Synchronisation côté client
# ---------------------------------------------------------------------------
class InventaireSync:
    PERIOD = 0.2           # s entre deux lectures (~5 Hz)
    PERIOD_TOL = 0.02      # s de tolérance (horloge du fil de travail)
    SETTLE = prog.COOP_DONJON_SETTLE   # s de stabilité après un changement de contexte ou de stage
    MAX_ACTIONS = 8        # écritures au plus par passe (§3.5)
    RELOAD = 4             # jetons perdus d'un coup au-delà desquels : rechargement (nouvelle base)
    EPEE_DELAI = 30.0      # s d'épée en main avant d'attribuer quand même (vérificateur R24)
    QUEUE_MAX = 256
    SEND_RATE = 12         # changements envoyés au plus par seconde (serveur : INV_RATE = 20)

    def __init__(self, enabled: bool = True, group: str = "default", clock=time.monotonic,
                 free_delay: float = prog.EVT_FREE_DELAY):
        self.enabled, self.group, self.clock = enabled, group, clock
        self.events = prog.EventWatch(free_delay, clock)
        self.commun = Etat()
        self.commun.sac = None               # pas encore reçu du serveur
        self.synced = False                  # réponse « snap » reçue : on peut écrire
        self.sent_acq: set = set()
        self.sent_bits: dict = {}
        self.sent_cap: dict = {}
        self.sent_fl = 0
        self.by: dict[int, str] = {}         # jeton -> qui l'a obtenu (messages)
        self.prev: Etat | None = None        # dernière lecture stable (base des retraits et du sac)
        self.local: Etat | None = None       # dernière lecture (coffres : ``owned``)
        self.retires: set = set()            # jetons retirés par le jeu (jamais redonnés)
        self.flacons_perdus = 0
        self.ctx = None
        self.since = 0.0
        self.need_full = True
        self.seq = 0
        self.ack = 0
        self.seq_sac = 0                     # dernier seq portant un changement du sac
        self.queue: list[dict] = []          # changements non accusés (renvoyés après un full)
        self.sent_upto = 0
        self.full_msg: dict | None = None
        self.sent_t: list[float] = []        # heures des derniers changements envoyés (débit)
        self._lock = threading.Lock()
        self.sac_tente: set = set()          # objets du sac que nous venons d'écrire (pas des échanges)
        self.epee_depuis: float | None = None
        self.anim: bytes | None = None       # octets d'animation de Link à la lecture précédente
        self.anim_bouge: float | None = None  # heure du dernier changement vu entre deux lectures
        self.last_tick = float("-inf")       # heure de la dernière passe (cadence PERIOD)
        self.mm_hist: tuple = (None, None)   # attente de magie max des deux dernières lectures
        self.item_get = None                 # pack v19 : fn(itemNo) -> int (CALL execItemGet)
        self.arc_reparer = False             # randomizer à objets progressifs : voir reparer_arc (posé par le client)
        self.bouclier_reparer = False        # randomizer, boucliers progressifs : voir reparer_bouclier (idem)
        self.notes: list[str] = []
        self.noted: set = set()
        self.n_recus = self.n_envoyes = 0
        self.status_txt = "pas encore lu"
        self.why = ""

    # --- notes ------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 512:
            self.noted.add(tag)
            self._note(s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    # --- coffres (contrat « propriétaire unique ») -------------------------------
    def owned(self, item) -> bool | None:
        """Pour synchro/coffres.py : True si l'objet d'inventaire ``item`` est déjà chez nous (le bit du
        coffre peut alors être posé seul) ; None si ce n'est pas un jeton de l'inventaire."""
        if not self.enabled or not _int(item) or item not in INV_ITEMS:
            return None
        loc = self.local
        return loc is not None and item in loc.acq

    # Compteurs dont ce module est propriétaire (contrat R19) : AUCUN. Rubis, flèches et bombes appartiennent
    # à synchro/compteurs.py ; la vie max reste à chacun (quarts : synchro/cadeaux.py et coffres)
    COMPTEURS: frozenset = frozenset()

    def owns_counter(self, name: str) -> bool:
        """Pour synchro/cadeaux.py (quarts de cœur) : vrai si l'inventaire partagé compte déjà ce
        compteur ; le cadeau pose alors le bit SEUL (sinon +2 au lieu de +1)."""
        return bool(self.enabled and name in self.COMPTEURS)

    # --- réseau -----------------------------------------------------------------
    def on_connect(self) -> None:
        """Nouvelle connexion : full au prochain tick lisible, puis renvoi des changements non accusés."""
        if not self.enabled:
            return
        with self._lock:
            self.need_full = True
            self.full_msg = None
            self.sent_upto = 0
            self.synced = False

    def on_disconnect(self) -> None:
        """Coupure : les changements continuent d'aller dans la file (renvoyés après le full de la
        reconnexion : un échange fait hors ligne n'est pas effacé par le sac du serveur)."""
        if not self.enabled:
            return
        with self._lock:
            self.synced = False

    def take_out(self, now: float | None = None) -> list[dict]:
        """Messages à envoyer maintenant : le full d'abord (toujours), puis les changements dans l'ordre,
        au plus ``SEND_RATE`` par seconde (plafond du serveur : INV_RATE = 20)."""
        if not self.enabled:
            return []
        now = self.clock() if now is None else now
        with self._lock:
            if self.need_full and self.full_msg is None:
                return []                     # full pas encore construit (jeu illisible) : on attend
            out = []
            if self.full_msg is not None:
                out.append(self.full_msg)
                self.full_msg = None
                self.need_full = False
            self.sent_t = [t for t in self.sent_t if now - t < 1.0]
            for m in self.queue:
                if m["seq"] > self.sent_upto and len(self.sent_t) < self.SEND_RATE:
                    out.append(m)
                    self.sent_t.append(now)
            if out:
                self.sent_upto = max(self.sent_upto, max(m["seq"] for m in out))
            return out

    def receive(self, msg: dict, me=None) -> int:
        """Message « inv » du serveur. Renvoie le nombre d'ajouts au commun."""
        if not self.enabled or msg.get("group") not in (None, self.group):
            return 0
        if msg.get("reset") in (1, True) and msg.get("from") is None:
            with self._lock:
                self.commun = Etat()
                self.commun.sac = None
                self.sent_acq, self.sent_bits, self.sent_cap, self.sent_fl = set(), {}, {}, 0
                self.queue.clear()
                self.need_full, self.full_msg, self.sent_upto, self.synced = True, None, 0, False
                self.prev = None
            self._note("inventaire : état commun remis à zéro par le serveur (rejoindre) : renvoi complet")
            return 0
        if me is not None and msg.get("from") == me:
            return 0
        v = valid_inv(dict(msg, seq=0) if not _int(msg.get("seq")) else msg)
        if v is None:
            return 0
        who = str(msg.get("nom") or "un autre joueur")[:24]
        n = 0
        with self._lock:
            ack = msg.get("ack")
            if _int(ack) and msg.get("from") is None:
                self.ack = max(self.ack, ack)
                self.queue = [m for m in self.queue if m["seq"] > self.ack]
            for no in v["acq"]:
                if no not in self.commun.acq:
                    self.commun.acq.add(no)
                    n += 1
                    if msg.get("from") is not None:
                        self.by[no] = who
            for off, m in v["bits"].items():
                if m & ~self.commun.bits.get(off, 0):
                    self.commun.bits[off] = self.commun.bits.get(off, 0) | m
                    n += 1
            for k, val in v["cap"].items():
                if val > self.commun.cap.get(k, 0):
                    self.commun.cap[k] = val
                    n += 1
            if v["fl"] is not None and v["fl"] > self.commun.fl:
                self.commun.fl = v["fl"]
                n += 1
            sac = msg.get("sac")
            if isinstance(sac, list):
                self.commun.sac = sorted(valid_inv({"full": 1, "sac": sac})["sac"])
                if msg.get("from") is not None:
                    for no in self.commun.sac:
                        self.by.setdefault(no, who)
            if msg.get("snap") in (1, True) and msg.get("from") is None:
                self.synced = True            # sac absent du snap : pas encore connu, rien d'imposé
        self.n_recus += n
        return n

    def _push(self, msg: dict) -> None:
        with self._lock:
            self.seq += 1
            msg["seq"] = self.seq
            if "sac" in msg:
                self.seq_sac = self.seq
            self.queue.append(msg)
            if len(self.queue) > self.QUEUE_MAX:
                self.queue = self.queue[-self.QUEUE_MAX:]

    def _full(self, e: Etat) -> None:
        with self._lock:
            self.seq += 1
            self.seq_sac = self.seq
            self.full_msg = {"type": "inv", "group": self.group, "seq": self.seq, "full": 1,
                             "acq": sorted(e.acq), "bits": [[o, m] for o, m in sorted(e.bits.items())],
                             "cap": dict(e.cap), "fl": e.fl, "sac": list(e.sac)}
            # changements en file : le full porte déjà toutes les unions ; seuls les changements du sac
            # (ajouts / retraits, que le full ne transmet pas) restent à renvoyer, RENUMÉROTÉS après le
            # full (sinon l'accusé du full les couvrirait avant qu'ils soient intégrés : le sac du serveur
            # serait imposé et notre échange retiré)
            kept = []
            for m in self.queue:
                if "sac" in m:
                    self.seq += 1
                    kept.append({"type": "inv", "group": self.group, "seq": self.seq, "sac": m["sac"]})
            self.queue = kept
            self.seq_sac = self.seq
            self.sent_upto = 0
            self.sent_acq = set(e.acq)
            self.sent_bits = dict(e.bits)
            self.sent_cap = dict(e.cap)
            self.sent_fl = e.fl

    # --- tick -------------------------------------------------------------------
    def _rebase(self, now: float, why: str) -> None:
        self.prev = None
        self.retires.clear()
        self.flacons_perdus = 0
        self.since = now
        self.sac_tente.clear()
        with self._lock:
            self.need_full = True
            self.full_msg = None
            self.sent_upto = 0
            self.queue.clear()                # changements d'une partie qui n'est plus chargée : oubliés
        if why:
            self._note(f"inventaire : {why} : état local renvoyé en entier (aucun retrait déduit)")

    def tick(self, mem, can_write: bool = True, connected: bool = True, now: float | None = None) -> int:
        """Un tick (~5 Hz) : lecture, changements -> file, application du commun. Renvoie le nombre
        d'écritures faites."""
        if not self.enabled or mem is None:
            return 0
        now = self.clock() if now is None else now
        # cadence ~5 Hz : le fil de travail du client appelle tick à --hz (20/s) ; les lectures, les
        # deux lectures de l'attente de magie, « Link figé » et MAX_ACTIONS par passe supposent PERIOD
        if now - self.last_tick < self.PERIOD - self.PERIOD_TOL:
            return 0
        self.last_tick = now
        try:
            lec = lire(mem)
        except Exception as exc:              # mémoire illisible pendant un chargement
            lec = Lecture(False, f"lecture impossible : {exc}")
        if not lec.ok:
            # chargement de stage (Link absent…) : la base est GARDÉE (un objet retiré par le jeu au
            # chargement, épée de la Forteresse 1, doit être vu comme un retrait, pas redonné)
            self.local, self.since = None, now
            self.anim = self.anim_bouge = None   # « Link figé » : échantillons repris à zéro
            if lec.raison.startswith(("écran titre", "pas de sauvegarde", "vie maximale")):
                self.ctx = None               # retour au titre : la prochaine partie est un rechargement
            self.status_txt = lec.raison
            return 0
        if lec.contexte != self.ctx:
            first = self.ctx is None and self.prev is None and not self.sent_acq
            self.ctx = lec.contexte
            self._rebase(now, "" if first else "partie (re)chargée")
        self.local = lec.etat
        if lec.pause:
            self.since = now                  # base gardée (voir plus haut), rien n'est lu ni écrit
            self.anim = self.anim_bouge = None
            self.status_txt = lec.pause
            return 0
        if now - self.since < self.SETTLE:
            self.status_txt = "attente de stabilité"
            return 0
        e = lec.etat
        self.mm_hist = (self.mm_hist[1], e.mm_attente)
        if self.prev is not None:
            perdus = self.prev.acq - e.acq
            if len(perdus) >= self.RELOAD:
                self._rebase(now, f"{len(perdus)} objets disparus d'un coup (rechargement ?)")
                self.status_txt = "rechargement : nouvelle base"
                return 0
            for no in sorted(perdus):
                if no not in self.retires:
                    self.retires.add(no)
                    self._note(f"inventaire : {nom(no)} retiré par le jeu : pas redonné tant que le jeu ne "
                               "le rend pas")
            if e.fl < self.prev.fl:
                self.flacons_perdus += self.prev.fl - e.fl
        self.retires -= e.acq
        if self.need_full and self.full_msg is None:
            self._full(e)
        # après un full aussi : un échange fait depuis la dernière lecture part en changement (le serveur
        # ne prend pas le sac d'un full, il fait foi) ; les unions sont déjà toutes dans le full
        self._deltas(e)
        self.prev = e.copie()                 # base : lecture AVANT nos écritures (sac : patché par _faire)
        self._suivre_anim(mem, now)           # « Link figé » : jugé sur des lectures consécutives
        done = 0
        self.why = ""
        if can_write and connected and self.synced and not self.need_full:
            busy = self.events.poll(mem)
            if busy:
                self.why = busy
            else:
                done = self._appliquer(mem, lec, now)
                if self.arc_reparer and lec.stage != STAGE_GTOWER and not self.events.poll(mem):
                    try:
                        if reparer_arc(mem, lec.gi):
                            done += 1
                            self._note("inventaire : case de l'arc remise à son meilleur palier acquis (objets "
                                       "progressifs)")
                    except Exception as exc:          # refus ou mémoire illisible : nouvel essai au tick suivant
                        self._once(("arc",), f"inventaire : case de l'arc pas encore réparée ({exc})")
                if self.bouclier_reparer and not self.events.poll(mem):
                    try:
                        if reparer_bouclier(mem, lec.gi):
                            done += 1
                            self._note("inventaire : bouclier miroir remis au bras (boucliers progressifs)")
                    except Exception as exc:          # refus ou mémoire illisible : nouvel essai au tick suivant
                        self._once(("bouclier",), f"inventaire : bouclier pas encore remis ({exc})")
        elif not can_write:
            self.why = "écriture impossible (lecture seule ou --progress-no-apply)"
        elif not self.synced:
            self.why = "attente de l'état commun du serveur"
        self.status_txt = "à jour" if not self.why else f"en attente : {self.why}"
        return done

    def _deltas(self, e: Etat) -> None:
        msg: dict = {"type": "inv", "group": self.group}
        with self._lock:                      # receive() (fil asyncio) modifie le commun
            commun = self.commun.copie()
        acq = sorted(e.acq - commun.acq - self.sent_acq)
        if acq:
            msg["acq"] = acq
            self.sent_acq |= set(acq)
            for no in acq:
                self._note(f"inventaire : tu as obtenu {nom(no)} : partagé")
        bits = []
        for off, v in sorted(e.bits.items()):
            m = v & ~(commun.bits.get(off, 0) | self.sent_bits.get(off, 0))
            if m:
                bits.append([off, m])
                self.sent_bits[off] = self.sent_bits.get(off, 0) | m
        if bits:
            msg["bits"] = bits
        cap = {k: v for k, v in e.cap.items() if v > max(commun.cap.get(k, 0), self.sent_cap.get(k, 0))}
        if cap:
            msg["cap"] = cap
            self.sent_cap.update(cap)
        if e.fl > max(commun.fl, self.sent_fl):
            msg["fl"] = e.fl
            self.sent_fl = e.fl
        if self.prev is not None:
            add = sorted(set(e.sac) - set(self.prev.sac) - self.sac_tente)
            dele = sorted(set(self.prev.sac) - set(e.sac) - self.sac_tente)
            if add or dele:
                msg["sac"] = {"add": add, "del": dele}
                for no in add:
                    self._note(f"inventaire : sac de livraison : {nom(no)} obtenu : partagé")
                for no in dele:
                    self._note(f"inventaire : sac de livraison : {nom(no)} donné : retiré chez les autres")
        self.sac_tente.clear()
        if len(msg) > 2:
            self.n_envoyes += 1
            self._push(msg)

    def _appliquer(self, mem, lec: Lecture, now: float) -> int:
        e, gi = lec.etat, lec.gi
        with self._lock:
            commun = self.commun.copie()
            sac_ok = self.ack >= self.seq_sac and not any("sac" in m for m in self.queue)
        if not sac_ok:
            commun.sac = None                 # nos changements du sac pas encore intégrés
        plan = a_donner(e, commun, self.retires, self.flacons_perdus)
        if not plan:
            self.epee_depuis = None
            return 0
        if lec.stage == STAGE_GTOWER and any(a[0] == "objet" and arc_ok(a[1]) for a in plan):
            plan = [a for a in plan if not (a[0] == "objet" and arc_ok(a[1]))]
            self._once(("gtower",), "inventaire : arc et flèches différés dans la Tour de Ganon (le jeu les a "
                                    "retirés) : donnés à la sortie")
        epee = [a for a in plan if a[0] == "collect" and COLLECT[a[1]][2] in (0, 1)]
        if epee:
            eq = link_equip(mem)
            if eq == EQUIP_EPEE:
                if self.epee_depuis is None:
                    self.epee_depuis = now
                if now - self.epee_depuis < self.EPEE_DELAI:
                    plan = [a for a in plan if a not in epee]
                    self.why = "épée en main (range-la pour recevoir l'épée / le bouclier)"
            else:
                self.epee_depuis = None
        else:
            self.epee_depuis = None
        if any(a[0] == "sac-" for a in plan) and self._fige(now):
            plan = [a for a in plan if a[0] != "sac-"]
            self.why = "Link figé (menu ouvert ?) : retrait du sac de livraison différé"
        done = 0
        for act in plan[:self.MAX_ACTIONS]:
            if self.events.poll(mem):         # relu entre deux écritures
                break
            try:
                if self._faire(mem, gi, act, e):
                    done += 1
            except EcritureRefusee as exc:
                self._once(("refus", act), f"inventaire : {self._desc(act)} non écrit ({exc})")
            except Exception as exc:          # mémoire illisible : nouvel essai au tick suivant
                self._once(("err", act), f"inventaire : {self._desc(act)} pas encore écrit ({exc})")
                break
        return done

    def _suivre_anim(self, mem, now: float) -> None:
        """Échantillon d'animation de Link à CHAQUE passe lisible (pas seulement quand un retrait du sac
        est prévu : sinon la première comparaison, sans lecture précédente ou avec une lecture vieille de
        plusieurs minutes, ne dit rien du menu)."""
        try:
            a = savecopy.link_anim(mem)
        except Exception:
            a = None
        try:
            self.menu = savecopy.menu_ouvert(mem)
        except Exception:
            self.menu = None
        if a is None:
            self.anim, self.anim_bouge = None, None
            return
        if self.anim is not None and a != self.anim:
            self.anim_bouge = now
        self.anim = a

    def _fige(self, now: float) -> bool:
        """Link figé (menu ouvert, pause) [HYPO, comme savecopy.attendre_moment_sur] : aucun changement
        vu entre deux lectures depuis FIGE_DELAI s, ou pas encore deux lectures (prudence). Animation
        illisible : non figé (même règle que attendre_moment_sur). Drapeau du menu levé : figé (pack WWMP
        Pause : Link tourne sous le menu, son animation ne dit plus rien)."""
        if getattr(self, "menu", None):
            return True
        if self.anim is None:
            return False
        return self.anim_bouge is None or now - self.anim_bouge >= savecopy.FIGE_DELAI

    def _desc(self, act: tuple) -> str:
        k = act[0]
        if k in ("objet", "collect"):
            return nom(act[1])
        if k == "flacon":
            return "flacon vide"
        if k == "bits":
            return f"bits gi+0x{act[1]:X}"
        if k == "cap":
            return f"{CAP_NOMS[act[1]]} {act[2]}"
        return f"sac de livraison : {nom(act[1])}"

    def _faire(self, mem, gi: int, act: tuple, e: Etat) -> bool:
        k = act[0]
        who = self.by.get(act[1], "un autre joueur") if len(act) > 1 and _int(act[1]) else "un autre joueur"
        if k == "objet":
            no = act[1]
            if self.item_get is not None and no in ITEM_GET_OK:
                # pack v19 (point d'accroche) : le jeu fait l'item_func ; relu juste avant (§2.1)
                if no in etat_local(mem).acq:
                    return False
                res = int(self.item_get(no))
                if res == mailbox.R_BUSY:
                    raise RuntimeError("événement en cours")     # transitoire : nouvel essai au tick suivant
                if res < 0:
                    raise EcritureRefusee(f"refusé par le jeu (code {res})")
                ok = True
            else:
                ok = donner_objet(mem, gi, no)
            if ok:
                e.acq.add(no)
                self._note(f"inventaire : {nom(no)} reçu (obtenu par {who})")
            return ok
        if k == "collect":
            ok = donner_collect(mem, gi, act[1], self.retires)
            e.acq.add(act[1])
            if ok:
                self._note(f"inventaire : {nom(act[1])} reçu (obtenu par {who})")
            return ok
        if k == "flacon":
            ok = ajouter_flacon(mem, gi, act[1])
            if ok:
                e.fl += 1
                self._note("inventaire : flacon vide reçu (un autre joueur en a un de plus)")
            return ok
        if k == "bits":
            ok = poser_bits(mem, gi, act[1], act[2])
            e.bits[act[1]] = e.bits.get(act[1], 0) | act[2]
            return ok
        if k == "cap":
            key, val = act[1], act[2]
            if key in ("am", "bm") and not (e.acq & ({0x27} if key == "am" else {0x31})):
                return False                  # carquois / sac sans l'arc / les bombes : attend le jeton
            if key == "mm":
                if self.mm_hist != (0, 0):
                    return False              # attente pas lue nulle deux fois (jeu en train d'appliquer)
                ok = monter_magie(mem, gi, val)
            else:
                ok = monter_plafond(mem, gi, key, val)
            if ok:
                e.cap[key] = val
                self._note(f"inventaire : {CAP_NOMS[key]} -> {val} (comme les autres joueurs)")
            return ok
        if k == "sac+":
            ok = sac_ajouter(mem, gi, act[1])
            if ok:
                self.sac_tente.add(act[1])
                self.prev.sac = sorted(set(self.prev.sac) | {act[1]})
                self._note(f"inventaire : sac de livraison : {nom(act[1])} reçu (échange de {who})")
            return ok
        if k == "sac-":
            ok = sac_retirer(mem, gi, act[1])
            if ok:
                self.sac_tente.add(act[1])
                self.prev.sac = sorted(set(self.prev.sac) - {act[1]})
                self._note(f"inventaire : sac de livraison : {nom(act[1])} retiré (donné par un autre joueur)")
            return ok
        return False

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        n = len(self.local.acq) if self.local is not None else 0
        s = f"{n} objet(s) ; {self.n_envoyes} envoi(s), {self.n_recus} reçu(s)"
        if self.retires:
            s += f", {len(self.retires)} retiré(s) par le jeu"
        if self.item_get is None:
            s += " ; écriture directe (pack v17)"
        return f"{s} ; {self.status_txt}"
