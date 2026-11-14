"""Mode Hanteurs, phases 3 et 4 : ORDRES aux monstres possédés et INFLUENCE sur les boss (étude
docs/etudes/mode_hanteurs.md §1 (b), §5, §6, §11 phases 3 et 4 ; la section « Vérification » finale prime).

Phase 3 (« ordres ») : le coup d'épée du Hanteur (``mCutType`` de son Link caché : coupe simple -> « attaque »,
attaque tournoyante ou coup sauté -> « lourde ») ou la touche F3 (« garde ») déclenche, chez l'autorité qui tient le
monstre en laisse (``laisse.LaisseHote``), l'attaque NATIVE du monstre possédé : l'état de son IA est forcé depuis un
état « libre », exactement comme son propre code le fait (Bokoblin : ``dr.mAction = 5 ; dr.mMode = 0`` dans
``fight_run``, d_a_bk.cpp), espèce par espèce (table ``ORDRES``).

Phase 4 (« posséder un boss » = choisir ses attaques) : chaque Link garde SON boss (A9 inchangé) ; un Hanteur choisit
dans le lanceur une attaque (Gohma : pince, coup de poing, feu, double coup ; une main de Gohdan : écraser, balayer ;
larves de Molgera : à l'attaque). Le serveur l'envoie aux Link qui combattent ce boss (``hboss``), et chaque PC de Link
la force sur SON exemplaire, si le boss est dans un état « libre » (``InfluenceLink``). Exclus, documentés :
petits Poe de Jalhalla (``action_kougeki`` non décompilé, états libres invérifiables), Ganondorf (décompilé vide ;
le script avec Zelda force la vie à 25, 50, 0 : aucun état d'attaque sûr à forcer), les autres boss (pas relus).

LE HOOK CIBLÉ (pack v19, bit 3 des capacités, ``Mailbox.ordres``) : CALL 10 « ORDRE » (arg1 = id de process de
l'acteur, cherché par le jeu ; arg2 = index dans la LISTE BLANCHE ``wwmp_ordres`` de l'asm, recopie exacte de
``ORDRES``). Sur le fil du jeu, hors événement : procName de l'acteur = celui de l'entrée, PV > 0 (si demandé), trois
gardes (u8 / s16 / s32 lu à un décalage fixe, valeur 0..31 dont le bit est levé dans un masque), puis au plus cinq
écritures (décalage, taille, valeur s8). Aucune adresse ni valeur ne vient du PC : il ne choisit qu'un index. Résultat
1 fait, 0 pas libre (rien écrit), -1 index ou espèce refusés, -3 événement, -4 acteur introuvable. Sans la capacité
(pack v17, v19 plus ancien, ``--no-pack-v19``) : aucun ordre n'est envoyé (mode dégradé = l'élan de la phase 1).

Chaque décalage, chaque valeur et chaque transition « libre -> attaque » est relevé dans le code HD (instructions du
jeu qui font la même chose, ``PREUVES``) et relu dans le ``cking.rpx`` par ``tests/test_ordres.py``.
"""
from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass, field

# --- table (recopiée dans graphicpack/WWMP_Mailbox/patch_mailbox.asm, wwmp_ordres) ---------------------------------
TAILLE_ENTREE = 0x30
ECRITURES_MAX = 5
TAILLES = (1, 2, 4)


@dataclass(frozen=True)
class Ordre:
    cle: str
    proc: int
    nom: str                                  # libellé FR (lanceur, notes)
    en: str                                   # libellé EN
    gardes: tuple                             # ((décalage, taille, valeurs permises), ...) : au plus 3
    ecritures: tuple                          # ((décalage, taille, valeur s8), ...) : au plus 5
    pv: bool = True                           # PV (+0x3A1) > 0 exigés
    genre: str = "monstre"                    # "monstre" (phase 3, monstre possédé) ou "boss" (phase 4, influence)
    geste: str | None = None                  # monstre : "attaque", "lourde" ou "garde"
    libre: float = 0.0                        # s sans laisse après l'ordre (le monstre bondit, plonge, saute)
    boss: str = ""                            # boss : nom du boss (regroupe les ordres, recharge commune)
    stages: frozenset = field(default_factory=frozenset)   # boss : stages où il se combat
    cout: int = 0                             # boss : points (× barème)
    recharge: float = 0.0                     # boss : s par Hanteur pour cet ordre
    tous: bool = False                        # boss : appliqué à tous les acteurs du proc (larves), sinon au 1er libre


# Fichiers du décompilé GameCube cités : d_a_bk.cpp (Bokoblin), d_a_mo2.cpp (Moblin), d_a_cc.cpp (Chuchu), d_a_tn.cpp
# (Darknut), d_a_ki.cpp (Keese), d_a_btd.cpp (Gohma), d_a_bst.cpp (Gohdan), d_a_bwds.cpp (larves de Molgera).
MODES_COMBAT = frozenset({0, 1, 2, 3, 4, 5, 6, 8})      # modes de fight_run (bk, mo2, tn) ; 33/34 = en plein saut
STAGES_GOHMA = frozenset({"M_DragB", "Xboss0"})
STAGES_GOHDAN = frozenset({"SirenB"})
STAGES_MOLGERA = frozenset({"kazeB", "Xboss3"})

ORDRES: tuple[Ordre, ...] = (
    # --- Bokoblin 189 : dr.mAction s16 +0x4A2, dr.mMode s16 +0x4A0 (dr = damagereaction en +0x49C), m0300[1] s16
    # +0x41A, m0B30 (arme : 0 / 1 / 2) s32 +0xC90. Depuis fight_run (action 4) : 5 = fight (attack_set 0, ou 2 sans
    # arme), 7 = oshi (bond + coup sauté, attack_set 1 : le jeu ne le fait qu'avec une arme), 10 = defence
    # (garde, m0300[1] = 15 images, puis attaque ou retour à fight_run).
    Ordre("bk.attaque", 189, "Bokoblin : attaque", "Bokoblin: attack",
          ((0x4A2, 2, {4}), (0x4A0, 2, MODES_COMBAT)), ((0x4A2, 2, 5), (0x4A0, 2, 0)), geste="attaque", libre=1.2),
    Ordre("bk.lourde", 189, "Bokoblin : coup sauté", "Bokoblin: jump attack",
          ((0x4A2, 2, {4}), (0x4A0, 2, MODES_COMBAT), (0xC90, 4, {1, 2})), ((0x4A2, 2, 7), (0x4A0, 2, 0)),
          geste="lourde", libre=2.0),
    Ordre("bk.garde", 189, "Bokoblin : garde", "Bokoblin: guard",
          ((0x4A2, 2, {4}), (0x4A0, 2, MODES_COMBAT)), ((0x4A2, 2, 10), (0x4A0, 2, 0), (0x41A, 2, 15)), geste="garde"),
    # --- Moblin 188 : mDamageReaction.mAction s16 +0x8C2, mMode s16 +0x8C0, mbHasInnateWeapon u8 +0x10B0 (lance).
    # 5 fight (attack_set 0), 7 oshi (coup de lance poussé), 10 defence (le mode 0 pose lui-même sa minuterie) ;
    # le jeu ne fait oshi et defence qu'avec la lance : même garde ici.
    Ordre("mo2.attaque", 188, "Moblin : attaque", "Moblin: attack",
          ((0x8C2, 2, {4}), (0x8C0, 2, MODES_COMBAT)), ((0x8C2, 2, 5), (0x8C0, 2, 0)), geste="attaque", libre=1.5),
    Ordre("mo2.lourde", 188, "Moblin : coup de lance", "Moblin: spear thrust",
          ((0x8C2, 2, {4}), (0x8C0, 2, MODES_COMBAT), (0x10B0, 1, {1})), ((0x8C2, 2, 7), (0x8C0, 2, 0)), geste="lourde", libre=1.5),
    Ordre("mo2.garde", 188, "Moblin : garde", "Moblin: guard",
          ((0x8C2, 2, {4}), (0x8C0, 2, MODES_COMBAT), (0x10B0, 1, {1})), ((0x8C2, 2, 10), (0x8C0, 2, 0)), geste="garde"),
    # --- Chuchu 206 : mCurrAction u8 +0x3FC, m2F5 u8 +0x3FD. Debout et en marche (action 0, m2F5 5) -> attaque
    # (action 2, m2F5 0x1E : bond vers le joueur, comme après le réveil 0xB).
    Ordre("cc.attaque", 206, "Chuchu : bond", "Chuchu: pounce",
          ((0x3FC, 1, {0}), (0x3FD, 1, {5})), ((0x3FC, 1, 2), (0x3FD, 1, 0x1E)), geste="attaque", libre=1.5),
    # --- Darknut 191 : mDamageReaction.mAction s16 +0x596, mMode s16 +0x594. fight_run (4) -> fight (5, mode 0).
    Ordre("tn.attaque", 191, "Darknut : attaque", "Darknut: attack",
          ((0x596, 2, {4}), (0x594, 2, MODES_COMBAT)), ((0x596, 2, 5), (0x594, 2, 0)), geste="attaque", libre=1.5),
    # --- Keese 215 : mAction u8 +0x489, mBehaviorType s8 +0x48B. Vol (1, 1) -> piqué (ACT_ATTACK_MOVE_INDEX 10, 0).
    Ordre("ki.attaque", 215, "Keese : piqué", "Keese: dive",
          ((0x489, 1, {1}), (0x48B, 1, {1})), ((0x489, 1, 10), (0x48B, 1, 0)), geste="attaque", libre=2.5),
    # --- Gohma 234 (phase 4) : m02E4 s16 +0x40C (0 attente, 1 attaque), mGohmaState s16 +0x40E (1 = attente libre),
    # mAttackType s8 +0x410, m02E9 s8 +0x411 (indice des animations : 0 droite, 5 gauche ; JAMAIS une autre valeur,
    # jab_bck[m02E9]), m02EA s8 +0x412, m02EC[0] s16 +0x414 (repos après une attaque : 0 = prête, comme wait()).
    # Les cinq champs sont écrits ensemble, comme wait().
    Ordre("btd.pince", 234, "Gohma : coup de pince", "Gohma: pincer jab",
          ((0x40C, 2, {0}), (0x40E, 2, {1}), (0x414, 2, {0})),
          ((0x40C, 2, 1), (0x40E, 2, 0), (0x410, 1, 0), (0x411, 1, 0), (0x412, 1, 1)),
          genre="boss", boss="Gohma", stages=STAGES_GOHMA, cout=30, recharge=10.0),
    Ordre("btd.poing", 234, "Gohma : coup de poing", "Gohma: punch",
          ((0x40C, 2, {0}), (0x40E, 2, {1}), (0x414, 2, {0})),
          ((0x40C, 2, 1), (0x40E, 2, 0), (0x410, 1, 1), (0x411, 1, 0), (0x412, 1, 1)),
          genre="boss", boss="Gohma", stages=STAGES_GOHMA, cout=30, recharge=10.0),
    Ordre("btd.feu", 234, "Gohma : jet de feu", "Gohma: fire breath",
          ((0x40C, 2, {0}), (0x40E, 2, {1}), (0x414, 2, {0})),
          ((0x40C, 2, 1), (0x40E, 2, 0), (0x410, 1, 2), (0x411, 1, 0), (0x412, 1, 1)),
          genre="boss", boss="Gohma", stages=STAGES_GOHMA, cout=40, recharge=14.0),
    Ordre("btd.double", 234, "Gohma : double coup", "Gohma: double punch",
          ((0x40C, 2, {0}), (0x40E, 2, {1}), (0x414, 2, {0})),
          ((0x40C, 2, 1), (0x40E, 2, 0), (0x410, 1, 5), (0x411, 1, 5), (0x412, 1, 0)),
          genre="boss", boss="Gohma", stages=STAGES_GOHMA, cout=40, recharge=14.0),
    # --- Gohdan 240, UNE main (phase 4) : mBstPartType u8 +0x3D0 (0 tête, 1 main gauche, 2 main droite),
    # mActionType s16 +0x130A (1 vol), mDamage s16 +0x130E (sous-état), mStateTimer s16 +0x133E (0 = prête).
    # Écraser = DOWN_ATTACK 10, balayer = HARAI_ATTACK 13 (main_cont fait la même chose, mDamage = 0).
    Ordre("bst.g.ecraser", 240, "Gohdan, main gauche : écraser", "Gohdan, left hand: slam",
          ((0x3D0, 1, {1}), (0x130A, 2, {1}), (0x133E, 2, {0})), ((0x130A, 2, 10), (0x130E, 2, 0)),
          genre="boss", boss="Gohdan", stages=STAGES_GOHDAN, cout=30, recharge=12.0),
    Ordre("bst.g.balayer", 240, "Gohdan, main gauche : balayer", "Gohdan, left hand: sweep",
          ((0x3D0, 1, {1}), (0x130A, 2, {1}), (0x133E, 2, {0})), ((0x130A, 2, 13), (0x130E, 2, 0)),
          genre="boss", boss="Gohdan", stages=STAGES_GOHDAN, cout=30, recharge=12.0),
    Ordre("bst.d.ecraser", 240, "Gohdan, main droite : écraser", "Gohdan, right hand: slam",
          ((0x3D0, 1, {2}), (0x130A, 2, {1}), (0x133E, 2, {0})), ((0x130A, 2, 10), (0x130E, 2, 0)),
          genre="boss", boss="Gohdan", stages=STAGES_GOHDAN, cout=30, recharge=12.0),
    Ordre("bst.d.balayer", 240, "Gohdan, main droite : balayer", "Gohdan, right hand: sweep",
          ((0x3D0, 1, {2}), (0x130A, 2, {1}), (0x133E, 2, {0})), ((0x130A, 2, 13), (0x130E, 2, 0)),
          genre="boss", boss="Gohdan", stages=STAGES_GOHDAN, cout=30, recharge=12.0),
    # --- larves de Molgera 218 (phase 4) : m02F6 s16 +0x412 (0 = sous le sable, ug_move), m02F8 s16 +0x414
    # (5 = errance) -> 0 (fin de l'errance : la larve fonce vers le joueur et bondit à 600 u). PV non exigés (larves).
    Ordre("bwds.chasse", 218, "Molgera : larves à l'attaque", "Molgera: larvae attack",
          ((0x412, 2, {0}), (0x414, 2, {5})), ((0x414, 2, 0),), pv=False,
          genre="boss", boss="Molgera", stages=STAGES_MOLGERA, cout=20, recharge=10.0, tous=True),
)
INDEX = {o.cle: i for i, o in enumerate(ORDRES)}
N_ORDRES = len(ORDRES)
GESTES = ("attaque", "lourde", "garde")
BOSS_PROCS = frozenset(o.proc for o in ORDRES if o.genre == "boss")

# --- Formes spéciales (bit 17 des capacités du pack) : seconde table ``wwmp_ordres2``, en FIN de pack (la première
# ne peut plus grandir sans déplacer ce qui la suit). Index global = N_ORDRES + rang ; jamais envoyé à un pack sans
# le bit 17 (``etendus``). Chaque écriture recopie une écriture que le jeu fait lui-même sur ce champ (PREUVES2).
ORDRES2: tuple[Ordre, ...] = (
    # Wizzrobe 208 (d_a_wz) : type u8 +0x464 (>= 0x0A : projectile), action u8 +0x468, mode u8 +0x469 (table de 9
    # cas ; 3 = visible, il attend son minuteur s16 +0x4F0 que le jeu décrémente jusqu'à 0 avant de tirer).
    # L'ordre met ce minuteur à 0 (valeur qu'il atteint de lui-même) : il tire tout de suite.
    Ordre("wz.tir", 208, "tir", "shot",
          ((0x464, 1, set(range(10))), (0x468, 1, {0}), (0x469, 1, {3})), ((0x4F0, 2, 0),), geste="attaque"),
    # Poe 212 (d_a_pw) : action s16 +0x482 (0 = ronde), mode s16 +0x484, +0x462 == 1. Le jeu écrit lui-même
    # action = 1 et mode = 0x1E quand il décide d'attaquer (0x0244C6D0), et seulement depuis les modes 14, 16 et
    # 20 (cmpwi 0x0244C604, 0C, 14) : la garde est cette liste, PAS toute la liste blanche du pilotage (13, 15 :
    # pilotés mais jamais point de départ d'une attaque). Le s32 +0x488 == -1 que le jeu exige aussi (0x0244B090)
    # ne tient pas dans une garde du pack : c'est l'autorité qui le vérifie (``LaisseHote._ordre``).
    Ordre("pw.attaque", 212, "attaque", "attack",
          ((0x482, 2, {0}), (0x484, 2, {14, 16, 20}), (0x462, 2, {1})), ((0x482, 2, 1), (0x484, 2, 0x1E)),
          geste="attaque", libre=2.5),
    # Magtail 216 : action u8 +0x570 ; l'octet +0xD1D est la DEMANDE « se dresser » que le jeu pose lui-même
    # (valeur 1, 0x021D9B94) et consomme ensuite (il la remet à 0, action = 1, mode = 0 : 0x021DA4A8).
    Ordre("mt.dresser", 216, "se dresser", "rear up",
          ((0x570, 1, {0, 1}),), ((0xD1D, 1, 1),), geste="attaque", libre=2.0),
)
N_ORDRES2 = len(ORDRES2)
TOUS: tuple[Ordre, ...] = ORDRES + ORDRES2
INDEX2 = {o.cle: N_ORDRES + i for i, o in enumerate(ORDRES2)}
# Preuves RPX des ordres étendus (adresse, mot attendu) : lecture de chaque garde, écriture native recopiée.
PREUVES2 = {
    "wz.tir": ((0x024E3DD0, 0x893F0464), (0x024E3DD4, 0x2809000A),      # lbz r9, 0x464 ; cmplwi r9, 0x0A
               (0x024E3DA4, 0x881F0468),                                # lbz r0, 0x468 (action)
               (0x024E3E0C, 0x891F0469), (0x024E3E10, 0x28080008), (0x024E3E14, 0x41810A30),   # mode borné : 9 cas
               (0x024E3D60, 0x3B5F04F0),                                # addi r26, r31, 0x4F0 : les minuteurs
               (0x024E4190, 0xA8BA0000), (0x024E4194, 0x2C050000)),     # mode 3 : lha r5, 0(r26) ; cmpwi r5, 0
    "pw.attaque": ((0x0244B514, 0xA8DE0482), (0x0244B5A0, 0xA97E0484),  # lha action, lha mode
                   (0x0244C604, 0x2C0B000E), (0x0244C60C, 0x2C0B0010),  # cmpwi r11, 14 ; cmpwi r11, 16
                   (0x0244C614, 0x2C0B0014), (0x0244C618, 0x408200D4),  # cmpwi r11, 20 ; bne : pas d'attaque
                   (0x0244B090, 0x801F0488), (0x0244B094, 0x2C00FFFF),  # lwz r0, 0x488 ; cmpwi r0, -1
                   (0x0244C6A0, 0xA8FE0462), (0x0244C6A4, 0x2C070001),  # lha r7, 0x462 ; cmpwi r7, 1
                   (0x0244C6D0, 0x39400001), (0x0244C6D4, 0x3980001E),  # li r10, 1 ; li r12, 0x1E
                   (0x0244C6D8, 0xB15E0482), (0x0244C6E0, 0xB19E0484)),  # sth action ; sth mode (décision du jeu)
    "mt.dresser": ((0x021D8D78, 0x889F0570),                            # lbz r4, 0x570 (action)
                   (0x021D9B90, 0x39000001), (0x021D9B94, 0x991F0D1D),  # li r8, 1 ; stb r8, 0xD1D (le jeu demande)
                   (0x021DA484, 0x88DF0D1D), (0x021DA4A8, 0x9A9F0D1D),  # demande lue, puis remise à 0
                   (0x021DA4B4, 0x98FF0570), (0x021DA4BC, 0x9A9F0571)),  # action = 1, mode = 0
}

# --- Boss aux boutons du Hanteur : dans une stage de boss, sans possession, le coup d'épée du Hanteur (ou F3)
# devient une demande ``hinfluence`` (mêmes règles serveur : coût, recharge, pause du boss). Un geste à plusieurs
# ordres (mains de Gohdan) les joue à tour de rôle.
BOUTONS_BOSS = {
    "Gohma": {"attaque": ("btd.pince", "btd.poing"), "lourde": ("btd.feu",), "garde": ("btd.double",)},
    "Gohdan": {"attaque": ("bst.g.ecraser", "bst.d.ecraser"), "lourde": ("bst.g.balayer", "bst.d.balayer")},
    "Molgera": {"attaque": ("bwds.chasse",), "lourde": ("bwds.chasse",)},
}
BOUTON_BOSS_PERIODE = 0.6    # s entre deux demandes nées d'un bouton (le serveur garde ses propres bornes)


def boss_de(stage) -> str | None:
    """Nom du boss commandable dans cette stage (None : aucun)."""
    for o in ORDRES:
        if o.genre == "boss" and stage in o.stages:
            return o.boss
    return None


def bouton_boss(stage, geste: str, rang: int = 0) -> str | None:
    """Clé de l'ordre de boss que joue ``geste`` (« attaque », « lourde », « garde ») dans ``stage`` ; ``rang`` :
    compteur de gestes (tour de rôle). None : pas de boss ici, ou geste sans ordre."""
    cles = BOUTONS_BOSS.get(boss_de(stage) or "", {}).get(geste, ())
    cles = [c for c in cles if c in INDEX and ORDRES[INDEX[c]].genre == "boss" and stage in ORDRES[INDEX[c]].stages]
    return cles[int(rang) % len(cles)] if cles else None

# profils d'acteurs (.data du RPX) : proc -> (adresse du profil, taille de l'instance) ; le test relit proc et taille
PROFILS = {189: (0x10191894, 0x19A4), 188: (0x101BABF4, 0x343C), 206: (0x101B3BEC, 0xF10), 191: (0x101D2868, 0x1C5C),
           215: (0x101B82B4, 0x10BC), 234: (0x10192EF0, 0x70D8), 240: (0x10192AD0, 0x3334), 218: (0x101B39B8, 0x19E4)}

# Preuves dans le code HD (adresse, mot attendu) : le jeu lit / écrit les mêmes champs avec les mêmes valeurs.
PREUVES = {
    189: (  # Bokoblin : Bk_move (switch sur l'action), fight_run 0x020A0A98 (r28 = 0), wait_set
        (0x020A6220, 0xA97F04A2),   # lha r11, 0x4a2(r31)      switch (dr.mAction)
        (0x020A6238, 0x280C001F),   # cmplwi r12, 0x1f
        (0x020A6268, 0x480022A8),   # b 0x20a8510              case 4 ->
        (0x020A8514, 0x4BFF8585),   # bl 0x20a0a98             fight_run
        (0x020A8530, 0x4BFF957D),   # bl 0x20a1aac             case 5 : fight
        (0x020A8568, 0x4BFFAA01),   # bl 0x20a2f68             case 7 : oshi
        (0x020A854C, 0x4BFFA8B9),   # bl 0x20a2e04             case 10 : defence
        (0x020A0B00, 0x3B800000),   # li r28, 0                (fight_run)
        (0x020A0FA0, 0x38E00005),   # li r7, 5
        (0x020A0FA4, 0xB0FF04A2),   # sth r7, 0x4a2(r31)       mAction = 5 (joueur à portée)
        (0x020A0FA8, 0xB39F04A0),   # sth r28, 0x4a0(r31)      mMode = 0
        (0x020A1778, 0x38000007),   # li r0, 7
        (0x020A177C, 0xB01F04A2),   # sth r0, 0x4a2(r31)       mAction = 7 (oshi)
        (0x020A1780, 0xB39F04A0),   # sth r28, 0x4a0(r31)
        (0x020A15C0, 0x38A0000A),   # li r5, 10
        (0x020A15C4, 0xB0BF04A2),   # sth r5, 0x4a2(r31)       mAction = 10 (defence)
        (0x020A15C8, 0xB39F04A0),   # sth r28, 0x4a0(r31)
        (0x020A15E8, 0xB0BF041A),   # sth r5, 0x41a(r31)       m0300[1] = 15 ou 30
        (0x020A2E5C, 0xA97F04A0),   # lha r11, 0x4a0(r31)      defence : switch (mMode)
        (0x0209B278, 0x801F0C90),   # lwz r0, 0xc90(r31)       wait_set : m0B30 (arme)
        (0x0209FB0C, 0x981E03F0),   # stb r0, 0x3f0(r30)       create : m02D4 (bouclier) = params & 0x20
        (0x020A1590, 0x895F03F0),   # lbz r10, 0x3f0(r31)      fight_run : bouclier ? sinon jamais de defence
    ),
    188: (  # Moblin : Mo2_move (fight_run et fight en ligne), defence, oshi
        (0x021D0E68, 0xA8FF08C2),   # lha r7, 0x8c2(r31)       switch (mAction)
        (0x021D0E80, 0x280B0028),   # cmplwi r11, 0x28         (ACTION_E3_DEMO 40)
        (0x021D1F84, 0xB09F08C2),   # sth r4, 0x8c2(r31)       mAction = 5
        (0x021D256C, 0xB01F08C2),   # sth r0, 0x8c2(r31)       mAction = 10
        (0x021D2570, 0xB0FF08C0),   # sth r7, 0x8c0(r31)       mMode = 0
        (0x021D2614, 0xB09F08C2),   # sth r4, 0x8c2(r31)       mAction = 7
        (0x021CE4B0, 0xA99F08C0),   # lha r12, 0x8c0(r31)      defence : switch (mMode)
        (0x021CE604, 0xA99F08C0),   # lha r12, 0x8c0(r31)      oshi : switch (mMode)
        (0x021D2C8C, 0xA99F08C0),   # lha r12, 0x8c0(r31)      fight : switch (mMode)
        (0x021CD20C, 0x38C00001),   # li r6, 1                 (create)
        (0x021CD214, 0x98DE10B0),   # stb r6, 0x10b0(r30)      mbHasInnateWeapon = 1
        (0x021CF5F0, 0x981E10B0),   # stb r0, 0x10b0(r30)      wepon_search : lance reprise
        (0x021D1D98, 0x88FF10B0),   # lbz r7, 0x10b0(r31)      fight_run : lance ?
    ),
    206: (  # Chuchu : daCC_Execute
        (0x0210DFE4, 0x999F03FC),   # stb r12, 0x3fc(r31)      mCurrAction
        (0x0210DFE8, 0x989F03FD),   # stb r4, 0x3fd(r31)       m2F5
        (0x0210E000, 0x280C0008),   # cmplwi r12, 8            switch (mCurrAction)
        (0x0210E060, 0x88BF03FD),   # lbz r5, 0x3fd(r31)       action_nomal_move : switch (m2F5)
        (0x0210E068, 0x2806000B),   # cmplwi r6, 0xb
        (0x0210E8B0, 0x98FF03FC),   # stb r7, 0x3fc(r31)       mCurrAction = 2
        (0x0210E8B4, 0x991F03FD),   # stb r8, 0x3fd(r31)       m2F5 = 0x1E
        (0x0210EC7C, 0x893F03FD),   # lbz r9, 0x3fd(r31)       action_attack_move : switch (m2F5)
        (0x0210EC8C, 0x2809001E),   # cmplwi r9, 0x1e
    ),
    191: (  # Darknut : Tn_move (fight_run en ligne, r27 = 0)
        (0x024C3ED4, 0x3B600000),   # li r27, 0
        (0x024C41D8, 0xA91E0596),   # lha r8, 0x596(r30)       switch (mAction)
        (0x024C41F0, 0x28060019),   # cmplwi r6, 0x19          (ACTION_S_DEMO 25)
        (0x024C558C, 0x38000005),   # li r0, 5
        (0x024C5590, 0xB01E0596),   # sth r0, 0x596(r30)       mAction = 5
        (0x024C5598, 0xB37E0594),   # sth r27, 0x594(r30)      mMode = 0
    ),
    215: (  # Keese : daKi_Execute (ki_fly_move en ligne, r30 = 0)
        (0x0219AB38, 0x3BC00000),   # li r30, 0
        (0x0219BE0C, 0x38A0000A),   # li r5, 10
        (0x0219BE10, 0x98BF0489),   # stb r5, 0x489(r31)       mAction = ACT_ATTACK_MOVE_INDEX
        (0x0219BE14, 0x9BDF048B),   # stb r30, 0x48b(r31)      mBehaviorType = 0
        (0x0219BE74, 0x2C0C000A),   # cmpwi r12, 10            -> ki_atack_move
    ),
    234: (  # Gohma : move / wait (r29 = 0)
        (0x020F60A0, 0x3BA00000),   # li r29, 0
        (0x020F6220, 0xA8DF040E),   # lha r6, 0x40e(r31)       case 0 : mGohmaState++
        (0x020F6228, 0xB0DF040E),   # sth r6, 0x40e(r31)
        (0x020F622C, 0x28181800),   # cmplwi r24, 0x1800       case 1 : joueur en face
        (0x020F6234, 0xA8FF0414),   # lha r7, 0x414(r31)       m02EC[0] == 0
        (0x020F6240, 0x39000001),   # li r8, 1
        (0x020F6248, 0xB11F040C),   # sth r8, 0x40c(r31)       m02E4 = 1
        (0x020F624C, 0xB3BF040E),   # sth r29, 0x40e(r31)      mGohmaState = 0
        (0x020F6264, 0x38000001),   # li r0, 1
        (0x020F6268, 0x9BBF0411),   # stb r29, 0x411(r31)      m02E9 = 0
        (0x020F626C, 0x981F0412),   # stb r0, 0x412(r31)       m02EA = 1
        (0x020F6280, 0x39800005),   # li r12, 5
        (0x020F6284, 0x38000000),   # li r0, 0
        (0x020F6288, 0x999F0411),   # stb r12, 0x411(r31)      m02E9 = 5
        (0x020F628C, 0x981F0412),   # stb r0, 0x412(r31)       m02EA = 0
        (0x020F62C4, 0x38A00005),   # li r5, 5
        (0x020F62CC, 0x98BF0410),   # stb r5, 0x410(r31)       mAttackType = 5
    ),
    240: (  # Gohdan : move (partie), main_cont (r26 = 0)
        (0x020E55FC, 0x3B400000),   # li r26, 0
        (0x020E5620, 0x881E03D0),   # lbz r0, 0x3d0(r30)       mBstPartType
        (0x020E5B24, 0xA8E5130A),   # lha r7, 0x130a(r5)       hand[0]->mActionType < 10
        (0x020E5B30, 0xA905133E),   # lha r8, 0x133e(r5)       hand[0]->mStateTimer == 0
        (0x020E5B3C, 0x3920000D),   # li r9, 13
        (0x020E5B40, 0xB125130A),   # sth r9, 0x130a(r5)       mActionType = HARAI_ATTACK
        (0x020E5B48, 0xB34A130E),   # sth r26, 0x130e(r10)     mDamage = 0
        (0x020E5D20, 0x3880000A),   # li r4, 10
        (0x020E5D24, 0xB085130A),   # sth r4, 0x130a(r5)       mActionType = DOWN_ATTACK
        (0x020E5D2C, 0xB345130E),   # sth r26, 0x130e(r5)      mDamage = 0
    ),
    218: (  # larves de Molgera : daBwds_Execute (move et ug_move en ligne, r16 = 0)
        (0x02106198, 0x3A000000),   # li r16, 0
        (0x02106204, 0xA8F60412),   # lha r7, 0x412(r22)       switch (m02F6)
        (0x02106298, 0xA8160414),   # lha r0, 0x414(r22)       ug_move : switch (m02F8)
        (0x02106944, 0xB2160414),   # sth r16, 0x414(r22)      case 5 : m02F8 = 0
    ),
}


# Équipement exigé, vérifié par le PC seulement (lecture) : champs qui doivent être NON NULS. Le bouclier du Bokoblin
# (m02D4, u8 +0x3F0) vaut 0 ou 0x20 (params & 0x20) : hors des valeurs 0..31 des gardes du pack. Le jeu ne passe en
# defence (10) qu'avec un bouclier (fight_run, 0x020A1590) ; sans lui, la « garde » serait une pose vide (la sphère
# de garde reste au point (0, 0, 0) : m11CC n'est calculé que pour un bouclier). Ni plantage ni blocage possibles :
# l'asm n'a pas besoin de ce contrôle, le PC n'envoie simplement pas l'ordre.
EQUIPEMENT: dict[str, tuple] = {"bk.garde": ((0x3F0, 1),)}


def equipe(o: Ordre, lire) -> bool:
    """Équipement de ``EQUIPEMENT`` présent (``lire(off, taille) -> int | None``). Illisible ou ``lire`` absent :
    vrai (le pack décide ; l'équipement ne protège que de gestes vides, jamais d'un plantage)."""
    if lire is None:
        return True
    for off, taille in EQUIPEMENT.get(o.cle, ()):
        v = lire(off, taille)
        if v is not None and v == 0:
            return False
    return True


def choisir(proc, geste: str, lire=None, etendus: bool = False) -> int | None:
    """Index de l'ordre à jouer pour le ``geste`` du Hanteur sur un monstre ``proc`` : « lourde » retombe sur
    « attaque » quand l'espèce n'a pas de coup lourd OU que le monstre n'a pas l'arme qu'il exige (Bokoblin ou Moblin
    désarmé : le jeu ne fait alors que l'attaque simple) ; « garde » sans bouclier : rien. ``lire`` (lecture seule de
    l'acteur, ou None) : l'ordre dont les gardes passent déjà est préféré ; sinon le premier (le pack refusera tant
    que le monstre n'est pas libre, et le PC réessaiera)."""
    cands = [pour_espece(proc, geste, etendus)]
    if geste == "lourde":
        cands.append(pour_espece(proc, "attaque", etendus))
    cands = [i for i in cands if i is not None and equipe(TOUS[i], lire)]
    if not cands:
        return None
    if lire is not None:
        for i in cands:
            if libre(TOUS[i], lire):
                return i
    return cands[0]


def masque(valeurs) -> int:
    m = 0
    for v in valeurs:
        if not 0 <= int(v) < 32:
            raise ValueError(f"valeur de garde hors 0..31 : {v}")
        m |= 1 << int(v)
    return m


def mots(o: Ordre) -> list[int]:
    """Les 12 mots (0x30 octets) de l'entrée, comme dans ``wwmp_ordres`` (asm). Disposition :
    +0x00 procName s16, drapeaux u8 (bit 0 : PV > 0), nombre d'écritures u8 ;
    +0x04, +0x0C, +0x14 : trois gardes {u16 décalage, u8 taille (0 = aucune), u8 0, u32 masque} ;
    +0x1C : cinq écritures {u16 décalage, u8 taille, s8 valeur} (les inutilisées à 0)."""
    if not 1 <= len(o.ecritures) <= ECRITURES_MAX or len(o.gardes) > 3:
        raise ValueError(o.cle)
    w = [((o.proc & 0xFFFF) << 16) | ((1 if o.pv else 0) << 8) | len(o.ecritures)]
    gardes = list(o.gardes) + [None] * (3 - len(o.gardes))
    for g in gardes:
        if g is None:
            w += [0, 0]
            continue
        off, taille, vals = g
        if taille not in TAILLES or not 0 <= off < 0x10000:
            raise ValueError(o.cle)
        w += [(off << 16) | (taille << 8), masque(vals)]
    for k in range(ECRITURES_MAX):
        if k < len(o.ecritures):
            off, taille, val = o.ecritures[k]
            if taille not in TAILLES or not 0 <= off < 0x10000 or not -128 <= val <= 127:
                raise ValueError(o.cle)
            w.append((off << 16) | (taille << 8) | (val & 0xFF))
        else:
            w.append(0)
    return w


def asm_table() -> str:
    """Texte asm de ``wwmp_ordres`` (une instruction ``.int`` par ligne), recopié dans patch_mailbox.asm."""
    lignes = []
    for i, o in enumerate(ORDRES):
        for k, m in enumerate(mots(o)):
            lignes.append(f"    .int 0x{m:08X}" + (f"     ; {i} {o.cle}" if k == 0 else ""))
    return "\n".join(lignes)


def asm_table2() -> str:
    """Texte asm de ``wwmp_ordres2`` (ordres étendus, fin du pack), recopié dans patch_mailbox.asm."""
    lignes = []
    for i, o in enumerate(ORDRES2):
        for k, m in enumerate(mots(o)):
            lignes.append(f"    .int 0x{m:08X}" + (f"     ; {N_ORDRES + i} {o.cle}" if k == 0 else ""))
    return "\n".join(lignes)


def pour_espece(proc, geste: str, etendus: bool = False) -> int | None:
    """Index de l'ordre ``geste`` de l'espèce ``proc`` (monstre possédé), None si l'espèce ne le connaît pas.
    ``etendus`` : le pack a la seconde table (bit 17) ; sinon jamais d'index >= ``N_ORDRES``."""
    for i, o in enumerate(TOUS if etendus else ORDRES):
        if o.genre == "monstre" and o.proc == proc and o.geste == geste:
            return i
    return None


def gestes_espece(proc, etendus: bool = False) -> list[str]:
    return [o.geste for o in (TOUS if etendus else ORDRES) if o.genre == "monstre" and o.proc == proc]


# mCutType (Link+0x3AC, d_a_player.h) : coupes simples et combos -> « attaque » ; tournoyante, ouragan, coups sautés
# et coups dans le dos -> « lourde ». 0 = aucun coup.
CUT_LOURDS = frozenset({0x05, 0x08, 0x09, 0x0A, 0x0C, 0x0E, 0x0F, 0x10, 0x13, 0x15, 0x17, 0x19, 0x1D})


def geste(cut_type) -> str | None:
    if not isinstance(cut_type, int) or isinstance(cut_type, bool) or cut_type <= 0 or cut_type > 0xFF:
        return None
    return "lourde" if cut_type in CUT_LOURDS else "attaque"


def ordres_boss() -> list[tuple[int, Ordre]]:
    return [(i, o) for i, o in enumerate(ORDRES) if o.genre == "boss"]


def cout_boss(o: Ordre, mult: float) -> int:
    return max(1, int(round(o.cout * mult)))


# --- phase 4, serveur (mêlé à hanteurs.Partie) ------------------------------------------------------------------
INFLUENCE_RATE = 2           # demandes acceptées au plus par seconde et par Hanteur
BOSS_PAUSE = 4.0             # s entre deux ordres au même boss (tous Hanteurs confondus)
REPONSE_MAX = 6.0            # s : au-delà, les Link qui n'ont pas répondu comptent « pas obéi »


class InfluencePartie:
    """Mixin de ``hanteurs.Partie`` : influence des Hanteurs sur les boss (``hinfluence`` -> ``hboss``)."""

    def _influence_init(self) -> None:
        self.infl_t: dict[int, list[float]] = {}
        self.boss_t: dict[str, float] = {}               # boss -> heure du dernier ordre
        self.infl_seq = 0
        self.infl_en_cours: dict[int, dict] = {}         # seq -> {hanteur, cle, liens, ok, rep, t, cout}
        self.n_influences = self.n_influences_ok = 0

    def combattants(self, o: Ordre) -> list[int]:
        """Link (pas possédés) dont le dernier state est dans une stage de ce boss, hors cinématique."""
        out = []
        for pid in self._liens():
            e = self.etat.get(pid) or {}
            if e.get("stage") in o.stages and not e.get("busy") and self.possede_par(pid) is None:
                out.append(pid)
        return out

    def influencer(self, pid: int, msg: dict, peut=None, now: float | None = None) -> str:
        """``hinfluence {ordre}`` d'un Hanteur. ``peut(pid) -> bool`` : le PC de ce Link sait appliquer (fonction
        ``influence`` de son hello). Renvoie "" (accepté) ou la raison du refus (``hrefus``, ``quoi: influence``)."""
        now = self.clock() if now is None else now
        raison, o, liens = self._refus_influence(pid, msg, peut, now)
        if raison:
            self.n_refus += 1
            self.sortie.append((pid, {"type": "hrefus", "quoi": "influence", "raison": raison}))
            return raison
        i = INDEX[o.cle]
        cout = cout_boss(o, self._mult_cout())
        self.points[pid] -= cout
        self.recharges[(pid, "infl:" + o.cle)] = now + o.recharge
        self.boss_t[o.boss] = now
        self.infl_seq = (self.infl_seq + 1) & 0xFFFFFF
        seq = self.infl_seq
        self.infl_en_cours[seq] = {"hanteur": pid, "cle": o.cle, "liens": set(liens), "ok": 0, "rep": set(),
                                   "t": now, "cout": cout}
        nom_h = self.noms.get(pid, "?")
        for lk in liens:
            self.sortie.append((lk, {"type": "hboss", "ordre": o.cle, "i": i, "seq": seq, "par": nom_h}))
        self.n_influences += 1
        self.sortie.append((pid, {"type": "hnote", "msg": f"{o.nom} : ordre envoyé à {len(liens)} Link (-{cout})"}))
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : {nom_h} influence {o.boss} ({o.cle}) chez {len(liens)} Link, -{cout} points")
        return ""

    def _mult_cout(self) -> float:
        from synchro.hanteurs import PRESETS
        return PRESETS[self.cfg["points"]]["cout"]

    def _refus_influence(self, pid: int, msg: dict, peut, now: float):
        if not self.repartie:
            return "la partie n'a pas commencé", None, []
        if not self.est_hanteur(pid):
            return "seuls les Hanteurs influencent les boss", None, []
        if not self.cfg.get("influence", True):
            return "influence sur les boss coupée dans cette partie", None, []
        t = [x for x in self.infl_t.get(pid, []) if now - x < 1.0]
        if len(t) >= INFLUENCE_RATE:
            self.infl_t[pid] = t
            return "trop de demandes", None, []
        t.append(now)
        self.infl_t[pid] = t
        cle = msg.get("ordre")
        if not isinstance(cle, str) or cle not in INDEX or ORDRES[INDEX[cle]].genre != "boss":
            return "ordre de boss inconnu", None, []
        o = ORDRES[INDEX[cle]]
        if pid in self.potions:
            return "tu joues un Link (potion)", None, []
        r = self.recharges.get((pid, "infl:" + cle), 0.0) - now
        if r > 0:
            return f"{o.nom} : recharge, encore {r:.0f} s", None, []
        r = self.boss_t.get(o.boss, float("-inf")) + BOSS_PAUSE - now
        if r > 0:
            return f"{o.boss} vient de recevoir un ordre (encore {r:.0f} s)", None, []
        liens = self.combattants(o)
        if peut is not None:
            liens = [lk for lk in liens if peut(lk)]
        if not liens:
            return f"aucun Link ne combat {o.boss} en ce moment (ou outils pas à jour)", None, []
        cout = cout_boss(o, self._mult_cout())
        if self.points.get(pid, 0.0) < cout:
            return f"pas assez de points ({int(self.points.get(pid, 0.0))}/{cout})", None, []
        return "", o, liens

    def influence_res(self, pid: int, msg: dict, now: float | None = None) -> None:
        """``hboss_res {seq, ok}`` d'un Link : l'ordre a été appliqué (ok = 1) ou pas (boss jamais libre, absent)."""
        now = self.clock() if now is None else now
        seq = msg.get("seq")
        en = self.infl_en_cours.get(seq) if isinstance(seq, int) and not isinstance(seq, bool) else None
        if en is None or pid not in en["liens"] or pid in en["rep"]:
            return
        en["rep"].add(pid)
        if msg.get("ok") in (1, True):
            en["ok"] += 1
        if en["rep"] >= en["liens"]:
            self._influence_fin(seq, now)

    def _influence_tick(self, now: float) -> None:
        for seq in [s for s, en in self.infl_en_cours.items() if now - en["t"] > REPONSE_MAX]:
            self._influence_fin(seq, now)

    def _influence_fin(self, seq: int, now: float) -> None:
        en = self.infl_en_cours.pop(seq, None)
        if en is None:
            return
        o = ORDRES[INDEX[en["cle"]]]
        h = en["hanteur"]
        if en["ok"]:
            self.n_influences_ok += 1
            self.sortie.append((h, {"type": "hnote", "msg": f"{o.nom} : obéi chez {en['ok']} Link sur "
                                                            f"{len(en['liens'])}"}))
        else:
            # personne n'a pu (boss jamais libre pendant le délai, mort, cinématique) : remboursé
            if h in self.points:
                self.points[h] += en["cout"]
            self.recharges.pop((h, "infl:" + en["cle"]), None)
            self.sortie.append((h, {"type": "hnote", "msg": f"{o.nom} : le boss n'était libre chez personne, "
                                                            "points rendus"}))
        self.t_diffusion = float("-inf")

    def vue_influence(self, now: float) -> dict:
        """Partie publique (diffusion ``hanteurs``) : ordres de boss, et Link en train de combattre chaque boss."""
        mult = self._mult_cout()
        combats = {}
        for _i, o in ordres_boss():
            if o.boss not in combats:
                combats[o.boss] = [str(p) for p in self.combattants(o)]
        return {"influence": int(bool(self.cfg.get("influence", True))),
                "influences": [{"i": i, "cle": o.cle, "boss": o.boss, "nom": o.nom, "en": o.en,
                                "cout": cout_boss(o, mult), "recharge": o.recharge} for i, o in ordres_boss()],
                "combats": combats}

    def moi_influence(self, pid: int, now: float) -> dict:
        return {"recharges_infl": {k[1][5:]: round(t - now, 1) for k, t in self.recharges.items()
                                   if k[0] == pid and isinstance(k[1], str) and k[1].startswith("infl:") and t > now}}


# --- phase 4, PC d'un Link -----------------------------------------------------------------------------------------
INFL_VIE = 4.0               # s : un ordre de boss jamais applicable est abandonné
INFL_PERIODE = 0.25          # s entre deux essais
INFL_MAX = 4                 # ordres en attente au plus
ACTEURS_MAX = 8              # larves essayées au plus par ordre
TIMEOUT_CALL = 0.5           # s : délai d'un CALL 10 (le fil de travail n'attend jamais plus)


class InfluenceLink:
    """Côté Link (fil de travail pour ``tick``, boucle réseau pour ``receive`` / ``take_out``) : applique à NOTRE
    boss les ordres des Hanteurs (CALL 10), seulement en jeu libre et si le boss est dans un état libre."""

    def __init__(self, enabled: bool = False, clock=time.monotonic):
        self.enabled = bool(enabled)
        self.clock = clock
        self._lock = threading.Lock()
        self.attente: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.n_recus = self.n_faits = self.n_abandons = 0
        self.vus: set[int] = set()

    def receive(self, msg: dict) -> None:
        if not self.enabled or msg.get("type") != "hboss":
            return
        cle, seq = msg.get("ordre"), msg.get("seq")
        if not isinstance(cle, str) or cle not in INDEX or ORDRES[INDEX[cle]].genre != "boss" \
                or not isinstance(seq, int) or isinstance(seq, bool) or seq in self.vus:
            return
        with self._lock:
            self.vus = (self.vus | {seq}) if len(self.vus) < 256 else {seq}
            if len(self.attente) >= INFL_MAX:
                old = self.attente.pop(0)
                self._sortir({"type": "hboss_res", "seq": old["seq"], "ok": 0})
            self.attente.append({"cle": cle, "seq": seq, "par": str(msg.get("par") or "?")[:24],
                                 "t": self.clock(), "essai": float("-inf")})
            self.n_recus += 1

    def reset(self) -> None:
        """Connexion perdue : ordres en attente oubliés, et séquences vues aussi (un serveur relancé recommence à 1 :
        ses premiers ordres seraient pris pour des doublons et jamais appliqués)."""
        with self._lock:
            self.attente.clear()
            self.vus = set()

    def take_out(self) -> list[dict]:
        with self._lock:
            out, self.out = self.out, []
        return out

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def _sortir(self, msg: dict) -> None:
        if len(self.out) < 16:
            self.out.append(msg)

    def tick(self, mb, acteurs, libre: bool, now: float | None = None) -> int:
        """``mb`` : Mailbox (capacité ``ordres``) ou None ; ``acteurs`` : [(id de process, proc)] des boss chargés ;
        ``libre`` : jeu libre (pas d'événement, pas de chargement). Renvoie le nombre d'ordres appliqués."""
        if not self.enabled:
            return 0
        now = self.clock() if now is None else now
        with self._lock:
            attente = list(self.attente)
        capable = mb is not None and bool(getattr(mb, "ordres", False))
        faits = 0
        fini: list[tuple[dict, int]] = []
        bloque = False          # la boîte n'a pas répondu (menu pause, chargement) : plus aucun appel à ce tick
        for a in attente:
            o = ORDRES[INDEX[a["cle"]]]
            if now - a["t"] > INFL_VIE or not capable:
                fini.append((a, 0))
                continue
            if bloque or not libre or now - a["essai"] < INFL_PERIODE:
                continue
            a["essai"] = now
            ok = 0
            cands = sorted({int(p) for p, proc in (acteurs or ()) if proc == o.proc
                            and isinstance(p, int) and not isinstance(p, bool)})[:ACTEURS_MAX]
            for pid in cands:
                try:
                    r = mb.ordre(pid, INDEX[a["cle"]], timeout=TIMEOUT_CALL)
                except Exception:
                    # délai dépassé (jeu en pause : le hook ne tourne plus) : chaque autre appel attendrait aussi
                    # TIMEOUT_CALL, jusqu'à INFL_MAX × ACTEURS_MAX fois, le fil de travail figé ; nouvel essai plus tard
                    bloque = True
                    break
                if r == 1:
                    ok += 1
                    if not o.tous:
                        break
            if ok:
                faits += 1
                fini.append((a, 1))
                self.notes.append(f"Hanteurs : {a['par']} commande ton boss : {o.nom}")
        with self._lock:
            for a, ok in fini:
                if a in self.attente:
                    self.attente.remove(a)
                self._sortir({"type": "hboss_res", "seq": a["seq"], "ok": ok})
                if ok:
                    self.n_faits += 1
                else:
                    self.n_abandons += 1
        return faits

    def status(self) -> str:
        if not self.enabled:
            return "désactivée"
        return f"{self.n_recus} ordre(s) de boss reçu(s), {self.n_faits} appliqué(s), {self.n_abandons} abandonné(s)"


# --- mesures en lecture seule (``wwhd.py ordres``) et sémantique des gardes (miroir de l'asm, tests) --------------
def lire_champ(mem, base: int, off: int, taille: int) -> int | None:
    """Valeur d'un champ comme l'asm la lit : u8 (taille 1), s16 (2), s32 (4) ; None si illisible."""
    try:
        r = mem.try_read(base + off, taille)
    except Exception:
        return None
    if r is None or len(r) != taille:
        return None
    return {1: lambda b: b[0], 2: lambda b: struct.unpack(">h", b)[0], 4: lambda b: struct.unpack(">i", b)[0]}[taille](r)


def libre(o: Ordre, lire) -> bool:
    """Vrai si l'asm accepterait l'ordre ``o`` (hors événement et procName) : ``lire(off, taille) -> int | None``."""
    if o.pv:
        hp = lire(0x3A1, 1)
        if hp is None or not 0 < hp < 0x80:
            return False
    for off, taille, vals in o.gardes:
        v = lire(off, taille)
        if v is None or not 0 <= v < 32 or v not in vals:
            return False
    return True


def rapport(mem, actors) -> list[str]:
    """Phase 3 et 4 (LECTURE SEULE, ``python wwhd.py ordres``) : pour chaque acteur d'une espèce de la table, les
    champs gardés et les ordres que le pack accepterait maintenant. N'écrit rien."""
    rows = []
    procs = {o.proc for o in ORDRES}
    for a in actors:
        if a.proc_name not in procs:
            continue
        lire = (lambda off, taille, b=a.base: lire_champ(mem, b, off, taille))
        champs = sorted({(off, taille) for o in ORDRES if o.proc == a.proc_name for off, taille, _v in o.gardes})
        vals = ", ".join(f"+0x{off:X}={lire(off, taille)}" for off, taille in champs)
        ok = [o.cle for o in ORDRES if o.proc == a.proc_name and libre(o, lire)]
        rows.append(f"  proc {a.proc_name:3} id {a.proc_id} PV {a.health} : {vals} -> libres : "
                    + (", ".join(ok) if ok else "aucun"))
    return rows or ["  aucun acteur de la table des ordres chargé"]
