"""Mort commune des boss (2026-11-20, demande de l'utilisateur : « fais pareil pour les autres boss »).

Ce que les joueurs voient : le dernier coup donné chez l'un tue le boss chez TOUS, tout de suite. Plus de « coup de
grâce » à redonner chez soi (ce que faisaient les PV partagés seuls : synchro/boss_pv.py laisse le boss au plancher
de 1 PV). Gohma l'avait déjà (synchro/boss_unique.py) ; ce module le fait pour les autres boss de la table ``MORTS``.

Comment, sans rien inventer : chaque joueur garde SON boss (filtre A9 inchangé), dont la scène de mort, le cœur et le
téléporteur sont joués chez lui par le jeu lui-même. Quand le minimum des PV reçu du serveur dit que le boss est
vaincu chez un autre (``BossPvSync.target`` <= ``vaincu``), nous écrivons sur NOTRE boss exactement les valeurs que
le jeu écrit lui-même au dernier coup (relues dans le code HD, instruction par instruction : tests/test_boss_mort.py,
classe TestRpx), PV à 0 d'abord. Aucun message de plus : c'est le message « boss » des PV partagés qui porte la
nouvelle, un ancien client ou un ancien serveur n'y voit aucune différence.

Jamais pendant un événement (introduction, mort en cours), un chargement, ni sur un acteur qui n'est pas le boss
attendu (en-tête et PV max relus juste avant d'écrire), et seulement depuis un état du boss d'où le jeu sait lancer
sa fin (``etat`` de chaque recette : « vivant » = on peut écrire, « attendre » = pas maintenant, « mort » = déjà fait).

Limite connue : les écritures du jeu au dernier coup sont suivies d'appels que nous ne pouvons pas faire depuis le PC
(arrêt de la musique de combat, pause de 8 images). La scène de fin les refait presque tous elle-même ; ce qui
resterait différent est noté dans chaque recette.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Callable

from jeu import progress as prog
from synchro import boss_pv

PAS_ECRITURE = 1.5          # s entre deux essais sur le même boss (le jeu a le temps de réagir)
VIVANT, ATTENDRE, MORT = "vivant", "attendre", "mort"


def _s16(mem, addr: int) -> int | None:
    raw = mem.try_read(addr, 2)
    return None if raw is None or len(raw) != 2 else struct.unpack(">h", raw)[0]


def _u8(mem, addr: int) -> int | None:
    raw = mem.try_read(addr, 1)
    return None if raw is None or len(raw) != 1 else raw[0]


# ---------------------------------------------------------------------------
# Kalle Demos (235) [DECOMP d_a_bmd.cpp core_damage_check, HD 0x020AEFAC..0x020AEFF0]
# ---------------------------------------------------------------------------
KD_MODE = 0x422             # s16 mMode : 0 attente, 1 et 2 attaques, 3 tombé (lianes coupées), 5 il avale Link,
                            # 10 introduction, 0xB fin
KD_SOUS = 0x424             # s16 m302 : sous-état du mode
KD_COEUR = 0x428            # s16 m306 : état du cœur (100 : il meurt)
KD_TEMPO = 0x432            # s16 m310 : 20000 (plus aucun coup compté)
KD_HD = 0xC92               # s16, propre à la HD : 1 au dernier coup (relu par le dessin, 0x020AE330)
KD_CAMERA = 0xC94           # s16 mB74 : 100 = la caméra de la scène de fin
KD_BDC = 0xCFC              # f32 mBDC : 1.0
KD_BE0 = 0xD00              # u8  mBE0 : 2
KD_MODE_FIN = 0xB
KD_MODES_VIVANT = (0, 1, 2, 3)      # jamais pendant qu'il avale Link (5) ni pendant l'introduction (10)


def kalle_etat(mem, b: dict, bosses: list) -> str | None:
    mode = _s16(mem, b["base"] + KD_MODE)
    if mode is None:
        return None
    if mode == KD_MODE_FIN:
        return MORT
    return VIVANT if mode in KD_MODES_VIVANT else ATTENDRE


def kalle_tuer(mem, b: dict, bosses: list) -> int:
    """Les écritures de ``core_damage_check`` au dernier coup. Ses lianes et ses pieds (Bmdhand, Bmdfoot) suivent
    le corps tout seuls (ils lisent son ``m332``, que pose la fin). Le mode en dernier : c'est lui qui lance la fin."""
    base = b["base"]
    mem.write(base + boss_pv.OFF_HEALTH, b"\x00")
    mem.write(base + KD_HD, struct.pack(">h", 1))
    mem.write(base + KD_TEMPO, struct.pack(">h", 20000))
    mem.write(base + KD_BE0, b"\x02")
    mem.write(base + KD_BDC, struct.pack(">f", 1.0))
    mem.write(base + KD_CAMERA, struct.pack(">h", 100))
    mem.write(base + KD_COEUR, struct.pack(">h", 100))
    mem.write(base + KD_MODE, struct.pack(">hh", KD_MODE_FIN, 0))
    return 8


# ---------------------------------------------------------------------------
# Roi Cuirassé (238) [DECOMP d_a_bdk.cpp damage_check / end_set, fondues dans execute en HD : 0x020672A4..0x02067358]
# ---------------------------------------------------------------------------
RC_STATUT = 0x2E0           # u32 : |= 0x4000 au dernier coup
RC_POS_Y = 0x318            # f32 : hauteur du boss (>= 9810 : il meurt en l'air)
RC_TOUR = 0x3D0             # u8  m2B4 : 1 = montée de la tour (pas le vrai combat)
RC_ACTION = 0x3DA           # s16 mAction : 0xA = fin ; 0xF introduction ; 9 bris du masque ; >= 0x64 la tour
RC_SOUS = 0x3DC             # s16 mState : 0, 1 ou 2 au dernier coup (animation de départ), puis 5 à 8
RC_MASQUE = 0xA08           # u8  m8F8 : 0 à 3 coups de marteau, 4 = brisé
RC_TETE_Y = 0x1250          # f32 : hauteur de la tête (< 10000 : tête basse)
RC_DEMO = 0x269C            # s16 m25A0 : étape de cinématique (0 aucune, 100 = la fin)
RC_EN_L_AIR = 0x269E        # s8  m25A2 : 1 = mort en l'air
RC_BEC = 0x5FC4             # u8  : effet du bec, remis à 0
RC_ACTION_FIN, RC_DEMO_FIN, RC_MASQUE_BRISE = 0xA, 100, 4
RC_HAUT, RC_TETE_HAUTE = 9810.0, 10000.0
RC_ACTIONS_ATTENDRE = (0xF, 9)


def roi_etat(mem, b: dict, bosses: list) -> str | None:
    base = b["base"]
    tour, action, demo = _u8(mem, base + RC_TOUR), _s16(mem, base + RC_ACTION), _s16(mem, base + RC_DEMO)
    if None in (tour, action, demo):
        return None
    if action == RC_ACTION_FIN:
        return MORT
    if tour != 0 or action >= 0x64 or action < 0 or action in RC_ACTIONS_ATTENDRE or demo != 0:
        return ATTENDRE                 # la tour, l'introduction, le bris du masque, une cinématique en cours
    return VIVANT


def roi_tuer(mem, b: dict, bosses: list) -> int:
    """Les écritures de ``end_set``. Le sous-état dépend de NOTRE Roi Cuirassé, comme dans le jeu : au sol tête
    basse (0), au sol tête haute (1), en l'air (2). Le masque est brisé d'abord s'il ne l'était pas chez nous (la
    valeur que pose le jeu, 0x02070224 : sinon il mourrait masque encore dessiné)."""
    base = b["base"]
    y, tete, statut = mem.try_read(base + RC_POS_Y, 4), mem.try_read(base + RC_TETE_Y, 4), \
        mem.try_read(base + RC_STATUT, 4)
    if None in (y, tete, statut) or len(statut) != 4:
        return 0
    y, tete = struct.unpack(">f", y)[0], struct.unpack(">f", tete)[0]
    if not (y == y and tete == tete):
        return 0
    sous = 2 if y >= RC_HAUT else (0 if tete < RC_TETE_HAUTE else 1)
    n = 6
    mem.write(base + boss_pv.OFF_HEALTH, b"\x00")
    masque = _u8(mem, base + RC_MASQUE)
    if masque is not None and masque < RC_MASQUE_BRISE:
        mem.write(base + RC_MASQUE, bytes([RC_MASQUE_BRISE]))
        n += 1
    mem.write(base + RC_BEC, b"\x00")
    if sous == 2:
        mem.write(base + RC_EN_L_AIR, b"\x01")
        n += 1
    mem.write(base + RC_STATUT, struct.pack(">I", struct.unpack(">I", statut)[0] | 0x4000))
    mem.write(base + RC_DEMO, struct.pack(">h", RC_DEMO_FIN))
    mem.write(base + RC_ACTION, struct.pack(">hh", RC_ACTION_FIN, sous))
    return n


# ---------------------------------------------------------------------------
# Molgera (217) [DECOMP d_a_bwd.cpp sita_hit, HD 0x0210008C..0x021001D8 ; la fin : ``end``, HD 0x020FEA30]
# Il ne meurt pas par ``health`` mais par le compteur de coups sur la langue (+0x1E2D, 4 = mort : 0x02100150).
# ---------------------------------------------------------------------------
MO_MUSIQUE = 0x187C         # u8  : remis à 0 au dernier coup (sinon la musique de combat peut repartir)
MO_MODE = 0x1B32            # s16 : 0 introduction, 1 2 3 sous le sable, 5 il avale, 10 11 en vol, 0xC fin, 0xD vaincu
MO_SOUS = 0x1B34            # s16 : sous-état du mode
MO_1E2A = 0x1E2A            # u8  : 0x32 au dernier coup
MO_COUPS = boss_pv.OFF_MOLGERA_COUPS    # s8 +0x1E2D : coups sur la langue (les PV partagés : 4 - coups)
MO_DEMO = 0x3E96            # s16 : cinématique en cours (0 aucune ; 100 = celle de la fin, posée par ``end``)
MO_MODE_FIN, MO_MODE_VAINCU, MO_COUPS_MORT = 0xC, 0xD, 4
MO_MODES_VIVANT = (1, 2, 3, 10, 11)     # jamais pendant l'introduction (0) ni pendant qu'il avale Link (5)


def molgera_etat(mem, b: dict, bosses: list) -> str | None:
    mode, demo = _s16(mem, b["base"] + MO_MODE), _s16(mem, b["base"] + MO_DEMO)
    if mode is None or demo is None:
        return None
    if mode in (MO_MODE_FIN, MO_MODE_VAINCU):
        return MORT
    return VIVANT if mode in MO_MODES_VIVANT and demo == 0 else ATTENDRE


def molgera_tuer(mem, b: dict, bosses: list) -> int:
    """Les écritures de ``sita_hit`` au quatrième coup. Ses larves et la cible de sa langue sont réglées par la fin
    elle-même (``end`` les marque et lance la cinématique : +0x3E96 = 100)."""
    base = b["base"]
    mem.write(base + MO_COUPS, bytes([MO_COUPS_MORT]))
    mem.write(base + MO_1E2A, b"\x32")
    mem.write(base + MO_MUSIQUE, b"\x00")
    mem.write(base + MO_MODE, struct.pack(">hh", MO_MODE_FIN, 0))
    return 4


# ---------------------------------------------------------------------------
# Gohdan (240) [DECOMP d_a_bst.cpp head_damage cas 5, fondu dans move en HD : 0x020ECDF0..0x020ECE74]
# Trois acteurs du même proc : la tête (type 0, 3 PV) et les deux mains (types 1 et 2, 4 PV). Les mains ne suivent
# pas la tête toutes seules : le jeu écrit sur chacune, nous aussi.
# ---------------------------------------------------------------------------
GD_POS_Y = 0x318            # f32 : hauteur (0 quand la tête est à terre, là où le jeu la tue)
GD_VITESSE = 0x370          # f32 speedF
GD_TYPE = 0x3D0             # u8  : 0 tête, 1 main gauche, 2 main droite
GD_ACTION = 0x130A          # s16 mActionType : 0 encastrée, 7 à terre, 20 scène du nez, 22 = fin
GD_SOUS = 0x130E            # s16 mDamage : sous-état (tête à terre : 4 et 5 = la bombe en bouche)
GD_PHASE = 0x30B0           # s8  m2E7C : 11 et 12 = le combat ; 100 = fini
GD_SCENE = 0x30CE           # s8  m2E9A : scène en cours (0 aucune ; 50 = la fin, que la caméra déroule)
GD_ACTION_FIN, GD_SCENE_FIN, GD_SOUS_MAIN, GD_PHASE_FINIE = 22, 50, 10, 100
GD_PHASES_COMBAT = (11, 12)
GD_ACTIONS_ATTENDRE = (0, 20)


def gohdan_etat(mem, b: dict, bosses: list) -> str | None:
    base = b["base"]
    typ, action, sous = _u8(mem, base + GD_TYPE), _s16(mem, base + GD_ACTION), _s16(mem, base + GD_SOUS)
    phase, scene = _u8(mem, base + GD_PHASE), _u8(mem, base + GD_SCENE)
    if None in (typ, action, sous, phase, scene) or typ != 0:
        return None
    if action == GD_ACTION_FIN or phase == GD_PHASE_FINIE or GD_SCENE_FIN <= scene <= GD_SCENE_FIN + 7:
        return MORT
    if phase not in GD_PHASES_COMBAT or scene != 0 or action in GD_ACTIONS_ATTENDRE:
        return ATTENDRE
    if action == 7 and sous in (4, 5):
        return ATTENDRE                 # la bombe en bouche : le jeu va retirer lui-même le dernier PV (il en reste 1)
    return VIVANT if len(_gohdan_mains(mem, b, bosses)) == 2 else ATTENDRE


def _gohdan_mains(mem, b: dict, bosses: list) -> list:
    return [o for o in bosses if o.get("proc") == b["proc"] and o["base"] != b["base"]
            and o.get("room") == b.get("room") and _u8(mem, o["base"] + GD_TYPE) in (1, 2)]


def gohdan_tuer(mem, b: dict, bosses: list) -> int:
    """Les mains d'abord (action 22, sous-état 10 : elles rentrent à leur place), puis la tête, la scène en dernier
    (c'est elle qui lance la fin : la caméra replace Link et la tête, pose le drapeau, sort la fleur et le cœur)."""
    mains = _gohdan_mains(mem, b, bosses)
    if len(mains) != 2:
        return 0
    for o in mains:
        h = boss_pv.read_header(mem, o["base"])
        if h is None or h[0] != o["pid"] or h[1] != o["proc"]:
            return 0
    for o in mains:
        mem.write(o["base"] + GD_SOUS, struct.pack(">h", GD_SOUS_MAIN))
        mem.write(o["base"] + GD_ACTION, struct.pack(">h", GD_ACTION_FIN))
    base = b["base"]
    mem.write(base + boss_pv.OFF_HEALTH, b"\x00")
    mem.write(base + GD_POS_Y, struct.pack(">f", 0.0))
    mem.write(base + GD_VITESSE, struct.pack(">f", 0.0))
    mem.write(base + GD_SOUS, struct.pack(">h", 0))
    mem.write(base + GD_ACTION, struct.pack(">h", GD_ACTION_FIN))
    mem.write(base + GD_SCENE, bytes([GD_SCENE_FIN]))
    return 10


# ---------------------------------------------------------------------------
# Ganon marionnette (243, 244, 245) [DECOMP d_a_bgn.cpp, d_a_bgn2.cpp, d_a_bgn3.cpp : damage_check de chacun]
# Les trois formes existent dès le début ; la forme en cours est le s8 +0x3D5 de la marionnette (0, 1, 2 ; 3 fini).
# Chaque forme a ses 3 PV (une clé par proc) : la mort de chacune est écrite quand c'est elle la forme en cours chez
# nous, hors de toute cinématique ; en retard d'une forme, on les rattrape l'une après l'autre.
# ---------------------------------------------------------------------------
PG_PROC_1, PG_PROC_2, PG_PROC_3 = 243, 244, 245
PG_FORME = 0x3D5            # s8, dans la marionnette (243) : forme en cours
PG_CAMERA = 0x151CE         # s8 mCSMode, dans la marionnette : cinématique en cours (0 aucune)
PG1_ECLAIR = 0x4BE          # s16 m0302 : 1
PG1_ETAT = 0x14EB4          # s16 mC748 : 0 à 5 le combat, 6 transformation, 7 introduction, 10 rangée
PG1_SOUS = 0x14EB6          # s16 mC74A
PG1_E4 = 0x14EE4            # deux u8 mC778, mC779 : 0
PG1_INVULN = 0x14F22        # s16 mC7B6 : 100
PG2_ETAT = 0x602            # s16 m0312 : 0 sortie, 1 2 4 5 le combat, 6 transformation, 10 rangée
PG2_SOUS = 0x604            # s16 m0314
PG2_INVULN = 0x62A          # s16 m033A : 20
PG2_FLASH = 0x3058          # s16 : 30, puis s16 +0x305A : 1
PG3_ETAT = 0x11E66          # s16 m0FD8A : 0 1 2 le combat, 3 la fin, 10 rangé
PG3_SOUS = 0x11E68          # s16 m0FD8C
PG3_9E = 0x11E7A            # s16 m0FD9E : 1
PG3_INVULN = 0x11E90        # s16 m0FDB4 : 10000
PG_RANGEE = 10


def _marionnette(mem, b: dict, bosses: list) -> tuple[int, int] | None:
    """(forme en cours, cinématique) lus dans la marionnette (243) de la même salle ; None si illisible."""
    m = b if b["proc"] == PG_PROC_1 else next(
        (o for o in bosses if o.get("proc") == PG_PROC_1 and o.get("room") == b.get("room")), None)
    if m is None:
        return None
    forme, cam = _u8(mem, m["base"] + PG_FORME), _u8(mem, m["base"] + PG_CAMERA)
    return None if forme is None or cam is None or forme > 3 else (forme, cam)


def _forme_etat(mem, b, bosses, rang: int, off_etat: int, fin: int, vivants: tuple) -> str | None:
    mc, etat = _marionnette(mem, b, bosses), _s16(mem, b["base"] + off_etat)
    if mc is None or etat is None:
        return None
    forme, cam = mc
    if forme > rang or etat == fin:
        return MORT
    return VIVANT if forme == rang and cam == 0 and etat in vivants else ATTENDRE


def marionnette_etat(mem, b, bosses):
    return _forme_etat(mem, b, bosses, 0, PG1_ETAT, 6, (0, 1, 2, 3, 4, 5))


def araignee_etat(mem, b, bosses):
    return _forme_etat(mem, b, bosses, 1, PG2_ETAT, 6, (1, 2, 4, 5))


def serpent_etat(mem, b, bosses):
    return _forme_etat(mem, b, bosses, 2, PG3_ETAT, 3, (0, 1, 2))


def marionnette_tuer(mem, b, bosses) -> int:
    base = b["base"]
    mem.write(base + boss_pv.OFF_HEALTH, b"\x00")
    mem.write(base + PG1_ECLAIR, struct.pack(">h", 1))
    mem.write(base + PG1_INVULN, struct.pack(">h", 100))
    mem.write(base + PG1_E4, b"\x00\x00")
    mem.write(base + PG1_ETAT, struct.pack(">hh", 6, 0))
    return 5


def araignee_tuer(mem, b, bosses) -> int:
    base = b["base"]
    mem.write(base + boss_pv.OFF_HEALTH, b"\x00")
    mem.write(base + PG2_FLASH, struct.pack(">hh", 30, 1))
    mem.write(base + PG2_INVULN, struct.pack(">h", 20))
    mem.write(base + PG2_ETAT, struct.pack(">hh", 6, 0))
    return 4


def serpent_tuer(mem, b, bosses) -> int:
    base = b["base"]
    mem.write(base + boss_pv.OFF_HEALTH, b"\x00")
    mem.write(base + PG3_9E, struct.pack(">h", 1))
    mem.write(base + PG3_INVULN, struct.pack(">h", 10000))
    mem.write(base + PG3_ETAT, struct.pack(">hh", 3, 0))
    return 4


# ---------------------------------------------------------------------------
# Jalhalla (211) [DECOMP d_a_bpw.cpp action_damage (séparation) et action_bunri_dousa, HD 0x020D8510 et 0x020D8970]
# Il n'a pas de dernier coup sur le corps : lancé contre les piques il se sépare en autant de petits Poe qu'il lui
# reste de PV, chaque Poe tué retire 1 PV, et c'est l'attente de la séparation (état 0x6F) qui lance la fin quand
# les PV sont à 0. Vaincu chez un autre :
# * chez nous en morceaux (action 4, état 0x6F) : PV à 0, le jeu fait le reste à l'image suivante ;
# * chez nous entier (action 0, 1 ou 2) : PV à 0 puis les deux écritures de la séparation (action 4, état 0x6E), sans
#   créer de Poe : 0x6E nettoie tout, tombe dans 0x6F, qui voit les PV à 0 et lance la fin.
# En morceaux, les petits Poe encore en vie chez nous meurent avec le corps : sur chacun (acteur 212 dont le parent
# +0x488 est ce Jalhalla), les écritures du jeu quand un Poe meurt d'un coup [HD seulement, 0x0244F218/1C] : PV 0,
# mode +0x484 = 0x36, action +0x482 = 2, et le bit 0x4000 de son statut (celui que Jalhalla donne au dernier Poe
# pour qu'il continue pendant la scène, 0x020D9504). Jamais sur un Poe qui meurt déjà, qui prend possession de Link
# ou qui le possède (actions 3 et 4 : événement et verrous tenus par le jeu) : ceux-là restent.
# ---------------------------------------------------------------------------
JA_ACTION = boss_pv.OFF_JAL_ACTION      # s16 +0x560 : 0 déplacement, 1 attaque, 2 chute, 3 touché, 4 séparation, 0x14 intro
JA_ETAT = 0x562             # s16 mActionState : 0x6E début de séparation, 0x6F attente, 0x70..0x75 il se reforme,
                            # 0x78 et plus = la fin
JA_SEPARATION, JA_DEBUT, JA_ATTENTE, JA_FIN = 4, 0x6E, 0x6F, 0x78
JA_ACTIONS_ENTIER = (0, 1, 2)           # jamais pendant que Link le porte (3) ni l'introduction (0x14)
POE_STATUT = 0x2E0          # u32
POE_ACTION = 0x482          # s16 : 0 1 libre, 2 touché/mourant, 3 prend possession, 4 possède Link
POE_MODE = 0x484            # s16 : 0x32..0x3B touché, 0x3C et plus : il meurt
POE_PARENT = 0x488          # u32 : id de process de son Jalhalla
POE_MODE_MORT, POE_ACTION_MORT = 0x36, 2


def jalhalla_poes(mem, b: dict, bosses: list) -> list:
    """Petits Poe de CE Jalhalla qu'on peut faire mourir maintenant."""
    out = []
    for o in bosses:
        if o.get("proc") != boss_pv.PROC_POE:
            continue
        parent = mem.try_read(o["base"] + POE_PARENT, 4)
        action, mode = _s16(mem, o["base"] + POE_ACTION), _s16(mem, o["base"] + POE_MODE)
        if parent is None or len(parent) != 4 or struct.unpack(">I", parent)[0] != b["pid"] or action is None \
                or mode is None:
            continue
        if action in (0, 1) or (action == 2 and 0x32 <= mode <= 0x3B):
            out.append(o)
    return out


def jalhalla_etat(mem, b: dict, bosses: list) -> str | None:
    base = b["base"]
    typ, action, etat = _u8(mem, base + boss_pv.OFF_JAL_TYPE), _s16(mem, base + JA_ACTION), _s16(mem, base + JA_ETAT)
    if None in (typ, action, etat) or typ != 0:
        return None
    if action == JA_SEPARATION and etat >= JA_FIN:
        return MORT
    if action in JA_ACTIONS_ENTIER or (action == JA_SEPARATION and etat == JA_ATTENTE):
        return VIVANT
    return ATTENDRE


def jalhalla_tuer(mem, b: dict, bosses: list) -> int:
    base = b["base"]
    action = _s16(mem, base + JA_ACTION)
    mem.write(base + boss_pv.OFF_HEALTH, b"\x00")
    if action == JA_SEPARATION:
        n = 1
        for o in jalhalla_poes(mem, b, bosses):
            h = boss_pv.read_header(mem, o["base"])
            statut = mem.try_read(o["base"] + POE_STATUT, 4)
            if h is None or h[0] != o["pid"] or h[1] != boss_pv.PROC_POE or statut is None or len(statut) != 4:
                continue
            mem.write(o["base"] + POE_STATUT, struct.pack(">I", struct.unpack(">I", statut)[0] | 0x4000))
            mem.write(o["base"] + boss_pv.OFF_HEALTH, b"\x00")
            mem.write(o["base"] + POE_MODE, struct.pack(">h", POE_MODE_MORT))
            mem.write(o["base"] + POE_ACTION, struct.pack(">h", POE_ACTION_MORT))
            n += 1
        return n
    mem.write(base + JA_ACTION, struct.pack(">hh", JA_SEPARATION, JA_DEBUT))
    return 2


@dataclass(frozen=True)
class Recette:
    etat: Callable          # (mem, b, bosses) -> VIVANT | ATTENDRE | MORT | None (illisible)
    tuer: Callable          # (mem, b, bosses) -> nombre d'écritures


MORTS: dict[int, Recette] = {
    235: Recette(kalle_etat, kalle_tuer),
    238: Recette(roi_etat, roi_tuer),
    217: Recette(molgera_etat, molgera_tuer),
    240: Recette(gohdan_etat, gohdan_tuer),
    211: Recette(jalhalla_etat, jalhalla_tuer),
    PG_PROC_1: Recette(marionnette_etat, marionnette_tuer),
    PG_PROC_2: Recette(araignee_etat, araignee_tuer),
    PG_PROC_3: Recette(serpent_etat, serpent_tuer),
}


def _ecriture_permise(mem, b: dict) -> bool:
    """Relu juste avant d'écrire : c'est toujours ce boss (PV max attendus), hors événement, hors chargement."""
    t = boss_pv.BOSS_PV[b["proc"]]
    h = boss_pv.read_header(mem, b["base"])
    if h is None or h[0] != b["pid"] or h[1] != b["proc"]:
        return False
    mx = mem.try_read(b["base"] + boss_pv.OFF_MAX_HEALTH, 1)
    if mx is None or mx[0] != t.ident:
        return False
    ev = prog.read_event(mem)
    if ev is None or ev.busy:
        return False
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    return nxt is not None and not nxt[0]


class BossMortSync:
    """Côté client, à côté de ``BossPvSync`` (dont il lit ``target`` et ``by``). ``tick`` après celui des PV."""

    def __init__(self, pv, enabled: bool = False, clock=time.monotonic, exclus: frozenset = frozenset()):
        self.pv, self.enabled, self.clock = pv, bool(enabled), clock
        self.exclus = frozenset(exclus)
        self.essai_t: dict[tuple[str, int], float] = {}     # (clé, pid) -> dernier essai d'écriture
        self.fait: set[tuple[str, int]] = set()             # (clé, pid) déjà dits
        self.notes: list[str] = []
        self.morts_ecrites = 0
        self.attentes = 0
        self.vu: dict[str, str] = {}

    def procs(self) -> frozenset:
        """Procs dont la mort est commune ici (pour ``BossPvSync.communs`` : pas de « coup de grâce » annoncé)."""
        return frozenset(p for p in MORTS if p not in self.exclus) if self.enabled else frozenset()

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def tick(self, mem, now: float | None, stage: str | None, bosses: list | None, free: bool, can: bool) -> int:
        """Renvoie le nombre de boss dont la mort a été écrite."""
        # salles des épreuves de la Tour de Ganon (stages en « X ») : mêmes écritures ; la fin du jeu y note
        # l'épreuve réussie et renvoie dans la Tour au lieu de jouer la mort du boss : chez tous les joueurs donc
        if not self.enabled or stage is None or not bosses or stage != self.pv.stage:
            self.vu = {}
            return 0
        now = self.clock() if now is None else now
        ecrits = 0
        vu = {}
        for b in bosses:
            r = MORTS.get(b.get("proc"))
            if r is None or b["proc"] in self.exclus or b["proc"] in self.pv.exclus:
                continue
            t = boss_pv.BOSS_PV[b["proc"]]
            if b.get("max") != t.ident:
                continue                                    # autre partie du même proc (mains, clones…)
            key = boss_pv.boss_key(b["proc"], b.get("params", 0))
            e = r.etat(mem, b, bosses)
            if e is None:
                continue
            vu[key] = e
            raw = self.pv.target.get(key)
            if raw is None or raw > t.vaincu or e == MORT:
                continue
            k = (key, b["pid"])
            if e != VIVANT or not (free and can):
                self.attentes += 1
                continue
            if now - self.essai_t.get(k, -1e9) < PAS_ECRITURE:
                continue
            if not _ecriture_permise(mem, b):
                continue
            self.essai_t[k] = now
            if not r.tuer(mem, b, bosses):
                continue
            self.morts_ecrites += 1
            ecrits += 1
            if k not in self.fait:
                self.fait.add(k)
                self.notes.append(f"{t.nom} est vaincu ({self.pv.by.get(key, 'un autre joueur')} a donné le dernier "
                                  "coup) : il meurt chez toi aussi")
        self.vu = vu
        return ecrits

    def status(self) -> str:
        if not self.enabled:
            return "désactivée"
        if not self.vu:
            return f"aucun boss à mort commune ici ({self.morts_ecrites} écrite(s))"
        return ", ".join(f"{boss_pv.BOSS_PV[boss_pv.key_proc(k)].nom} : {e}" for k, e in sorted(self.vu.items())) \
            + f" ({self.morts_ecrites} écrite(s))"
