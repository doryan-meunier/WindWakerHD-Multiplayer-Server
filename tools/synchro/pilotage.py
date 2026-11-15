"""Mode Hanteurs : « possession complète » (étude docs/etudes/possession_complete.md ; sa section « Vérification »
prime). Le Hanteur PILOTE le monstre qu'il possède : son stick le déplace, ses boutons le font frapper.

Options : client ``--share-pilotage`` (activé par ``--coop-donjon``), ``--no-pilotage`` pour couper ; réglages de la
partie Hanteurs ``pilotage`` (activé par défaut) et ``recharge_attaques`` (× 0,5 à × 3). Sans le pack v19 à jour chez
l'autorité de la salle (bit 16 des capacités, zone ``wwmp_pilote``), avec un pack v17, une espèce hors de la table
ci-dessous ou ``--no-pilotage`` d'un côté ou de l'autre : repli automatique sur la laisse (``synchro/laisse.py``).

Comment. Le Hanteur joue son Link caché normalement (toute manette ou tout clavier réglé dans Cemu) ; son client
ne lit que l'ÉTAT de ce Link, jamais la manette :

* 20 fois par seconde, la position, l'orientation et la vitesse horizontale mesurée du Link caché partent au serveur
  dans son ``state`` (champ ``pil``) ; le serveur les valide (vitesse plafonnée, saut de position refusé) et les
  relaie à l'autorité de la salle dans le ``hlaisse`` (``pil``, ``vit``) ;
* chez l'autorité (``laisse.LaisseHote``), le monstre est ÉPINGLÉ sur cette position (``interpoler`` : lissée,
  bornée par la vitesse de l'espèce) avant ET après son ``execute`` (pack : bit 2 de l'emplacement d'ennemi), son IA
  restant active ; quand le pilote avance, une « carotte » (``carotte``) remplace Link le temps de cet execute : un
  point loin devant, que l'IA prend pour le joueur, donc elle court dans le sens de la marche avec sa vraie
  animation ; près d'un vrai Link, la carotte disparaît et l'IA se bat d'elle-même ;
* les boutons : coup d'épée du Link caché = « attaque », attaque tournoyante ou coup sauté = « lourde », F3 =
  « garde » (ordres de ``synchro/ordres.py``, CALL 10) ; pendant qu'un coup se joue, l'épinglage est levé (le monstre
  bondit de lui-même), puis le Link caché est recollé sur le monstre ;
* une vraie recharge par geste et par espèce (``RECHARGES``), tenue par le SERVEUR, multipliée par le réglage
  « recharge des attaques », envoyée au Hanteur (``hrecharge``) et affichée dans le lanceur ;
* l'autorité répond 10 fois par seconde (``hpilote`` : accepté ou non et pourquoi, position vraie du monstre, « libre »
  pendant un coup) ; le client du Hanteur dessine sa marionnette du monstre SUR son Link caché (prédiction locale,
  aucune latence pour le pilote) et recolle le Link caché quand l'écart dépasse ``RECOLLAGE`` u.

Règle de sûreté : une espèce n'est pilotée que si la liste de ses états où l'épinglage est sans danger est PROUVÉE
bornée dans le code HD (aiguillage ``cmplwi`` + table de sauts : ``PREUVES``). Hors de cette liste (touché, projeté,
à terre, saisi, mort : états de ``c_damagereaction``), le bit 2 est retiré et le monstre joue sa réaction native.
Jamais d'écriture dans un champ d'état du monstre ici (les ordres passent par CALL 10, gardes comprises).
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from synchro import ordres as ordres_mod

VIT_MIN = 30.0             # u/s : en dessous, le pilote est « au repos » (pas de carotte de course)
AVANCE = 0.15              # s : la position imposée ne dépasse jamais la position reçue + AVANCE de vitesse
SUR_VITESSE = 1.5          # × vmax : pas maximal de la position imposée par seconde (rattrapage compris)
VIT_PLAFOND = 2.0          # × vmax : plafond de la vitesse annoncée (serveur)
SAUT_MAX = 600.0           # u : saut du Link caché entre deux états au-delà duquel la possession finit (serveur)
RECOLLAGE = 300.0          # u : écart Link caché / monstre au-delà duquel le client recolle le Link caché
RECOLLAGE_MAX = 3000.0     # u : au-delà, position de retour jugée folle : ni recollage ni prédiction (client)
RECOLLAGE_SERVEUR = 350.0  # u : un saut > SAUT_MAX est admis s'il arrive à cette distance du monstre (recollage)
RECOLLAGE_PERIODE = 0.7    # s entre deux recollages (client)
PERIODE_RETOUR = 0.1       # s entre deux ``hpilote`` (autorité -> Hanteur) ; lot L3 (A6) : 10 Hz (avant 5)
RETOUR_MAX = 1.5           # s : un ``hpilote`` plus vieux ne vaut plus (prédiction coupée)
YEUX = 110.0               # u : hauteur des « yeux » de la carotte au-dessus de son sol [HYPO : Link adulte ~110 u]
ACTION_MAX = 31            # le pack refuse la carotte hors de 0..31 (masque de 32 bits)
RAISONS = ("pack", "espece", "option", "evenement", "essai")


@dataclass(frozen=True)
class Espece:
    nom: str
    off_action: int                 # s16 : action de l'IA (aiguillage de la fonction ``*_move``)
    off_mode: int                   # s16 : mode dans l'action
    actions: frozenset              # liste blanche : marche, combat, attaque, garde (jamais une réaction aux dégâts)
    mode_min: int                   # mode <= mode_min : état spécial (Bokoblin -100 : figé par un événement)
    vmax: float                     # u/s (même valeur que laisse.POSSEDABLES)
    d_course: float                 # u : carotte de course, devant le monstre [HYPO, à régler en jeu]
    d_ordre: float                  # u : carotte au repos pendant un ordre en attente (le monstre se met en garde)
    essai: bool = False             # True : seulement avec l'option des espèces à l'essai

    @property
    def masque(self) -> int:
        m = 0
        for a in self.actions:
            m |= 1 << a
        return m


# Actions de d_a_bk.cpp / d_a_mo2.cpp (décompilé GameCube, mêmes numéros en HD : tables de sauts de ``PREUVES``) :
# 0 attente / patrouille, 4 fight_run (court vers le joueur), 5 fight (attaque), 7 oshi (coup sauté, coup de lance),
# 10 defence (garde). Tout le reste (1-3 chemins et recherche d'arme, 6 et 8-9 réactions, 11 et plus : scènes,
# torche, saut de tour, ivresse…) n'est jamais épinglé.
_MARCHE_COMBAT = frozenset({0, 4, 5, 7, 10})
ESPECES: dict[int, Espece] = {
    189: Espece("Bokoblin", 0x4A2, 0x4A0, _MARCHE_COMBAT, -100, 420.0, 600.0, 150.0),
    188: Espece("Moblin", 0x8C2, 0x8C0, _MARCHE_COMBAT, -100, 330.0, 700.0, 220.0),
}

# Espèces relayées qui ne sont PAS pilotées, et pourquoi (affiché au Hanteur, repris dans l'étude ; les espèces de
# ``AUTRES`` ont leur raison dans leur champ ``pourquoi``) :
NON_PILOTEES = {
    204: "Mothula : mini-boss à fin locale (sa mort ouvre la salle chez un seul PC), ailes non synchronisées ; son "
         "mode (+0x3F8) passe bien par une table bornée, mais aucune liste blanche de vol n'a été relue",
    207: "Bubble : sa flamme (éteinte ou non) décide de son vol et n'est pas synchronisée entre les PC ; la "
         "condition « flamme allumée » est un test de bit que la garde du pack ne sait pas lire",
    119: "Floormaster : le jeu range son mode (+0x3C8) sans le borner avant sa table (0x02141308, 0x0214135C : "
         "`stw r5, 0x3C8(r3)`), il saisit le Link de l'autorité et le renvoie à l'entrée du donjon (changement de "
         "salle non bloqué pour un monstre possédé)",
    205: "Morth : hors du groupe des ennemis (groupe 3), jamais relayé",
}
# Preuves RPX des espèces restées hors de la possession (adresse, mot attendu)
PREUVES_ECARTES = {
    119: ((0x02141308, 0x90A303C8), (0x0214135C, 0x90A303C8)),     # stw r5, 0x3C8(r3) : mode rangé sans borne
    204: ((0x0214EAC0, 0xA98E03F8), (0x0214EB04, 0x280C0008)),     # lha r12, 0x3F8 ; cmplwi r12, 8
    207: ((0x020AB1DC, 0x899F03EE), (0x020AB208, 0x280C000A)),     # lbz r12, 0x3EE ; cmplwi r12, 0x0A
}

# Preuves dans le code HD (adresse, mot attendu), en plus de ``ordres.PREUVES`` (aiguillage et cas 4, 5, 7, 10) :
# l'action est bornée (cmplwi + bgt) AVANT la table de sauts, les cas de la liste blanche mènent aux fonctions
# attendues, et le jeu lit bien l'angle (+0x322) et la vitesse (+0x370) que le pack écrit.
PREUVES = {
    189: (
        (0x020A6220, 0xA97F04A2),   # lha r11, 0x4a2(r31)      Bk_move : switch (dr.mAction)
        (0x020A6238, 0x280C001F),   # cmplwi r12, 0x1f         0..31, sinon défaut (jamais d'index hors table)
        (0x020A0C68, 0xA99F04A0),   # lha r12, 0x4a0(r31)      fight_run : dr.mMode
        (0x020A0C98, 0x387F0370),   # addi r3, r31, 0x370      fight_run : speedF
        (0x020A0CD0, 0x387F0370),   # addi r3, r31, 0x370
        (0x020A0B50, 0x387F0322),   # addi r3, r31, 0x322      fight_run : current.angle.y
        (0x020A0DCC, 0x387F0322),   # addi r3, r31, 0x322
        (0x020A0E98, 0x387F0322),   # addi r3, r31, 0x322
    ),
    188: (
        (0x021D0E68, 0xA8FF08C2),   # lha r7, 0x8c2(r31)       Mo2_move : switch (mAction)
        (0x021D0E80, 0x280B0028),   # cmplwi r11, 0x28         0..40, sinon défaut
        (0x021D2C8C, 0xA99F08C0),   # lha r12, 0x8c0(r31)      cas 5 (fight) : mMode
        (0x021D1CE4, 0x387F0322),   # addi r3, r31, 0x322      cas 4 (fight_run) : current.angle.y
        (0x021D1D5C, 0x387F0370),   # addi r3, r31, 0x370      cas 4 : speedF
    ),
}
# Tables de sauts : proc -> (adresse de la table, nombre de cas, {cas : adresse visée}) ; chaque entrée est un ``b``.
TABLES = {
    189: (0x020A6258, 32, {0: 0x020A62D8, 4: 0x020A8510, 5: 0x020A852C, 7: 0x020A8564, 10: 0x020A8548}),
    188: (0x021D0EA0, 41, {0: 0x021D0F44, 4: 0x021D1C9C, 5: 0x021D2C88, 7: 0x021D39EC, 10: 0x021D39D4}),
}
# Chaque execute d'acteur (fopAc_Execute) est suivi d'une relecture de l'acteur par le jeu, et précédé de la copie
# current -> old : écrire position et angle juste avant et juste après l'execute est le chemin de wwmp_enemies.
PREUVES_EXECUTE = (
    (0x025D4918, 0x917E0300),       # stw r11, 0x300(r30)      old.pos = current.pos
    (0x025D4930, 0x891E02DA),       # lbz r8, 0x2da(r30)       après l'execute
    (0x025D498C, 0x891E02DA),       # lbz r8, 0x2da(r30)
)
# Manette (lecture seule, étude §5.4) : CPad_GET_STICK_POS_X 0x0200796C lit le stick de la manette 0 par un
# pointeur global. Seuls les sticks sont prouvés ; aucun champ de bouton n'est localisé : garde et fin de possession
# restent au clavier (F3, F4) et au lanceur.
PREUVES_MANETTE = (
    (0x0200796C, 0x2C030000),       # cmpwi r3, 0
    (0x02007984, 0x818C5088),       # lwz r12, 0x5088(r12)     pointeur de la manette 0 (0x101F5088)
    (0x02007988, 0xC02C0130),       # lfs f1, 0x130(r12)       stick X
)

# ---------------------------------------------------------------------------------------------------------------
# Étape 2 : les autres espèces (étude docs/etudes/possession_complete.md, « Vérification de l'étape 2 »).
# Aucune écriture d'état : seulement l'épinglage du pack (bit 2) et, quand la garde du pack peut être exprimée
# (un s16 à un décalage pair, valeurs 0..31), la carotte. La liste blanche est une suite de CONDITIONS lues dans
# l'acteur à chaque image (toutes vraies, sinon rien n'est imposé : l'IA native joue sa réaction).
#
# Condition : (op, décalage, taille, valeur) ; tailles 1 (u8), 2 (s16), 4 (s32 ou f32 pour « fmax ») ;
# op : "eq", "ne", "in", "hors", "gt", "fmax" (|f32| <= valeur) ; ("ou", groupe_a, groupe_b) : l'un des deux
# groupes entier.
# Formes spéciales (pack : bit 17 des capacités, ``mailbox.CAP_FORMES``) : « cible » = carotte SEULE, le monstre
# n'est jamais replacé (il se déplace, saute, se téléporte ou tire de lui-même vers le point où se tient le
# Hanteur) ; « epingle » avec des drapeaux = épinglage de x et z seulement, gardé par l'état du monstre.
FORMES = ("carotte", "epingle", "cible")
DR_SANS_EPINGLE, DR_XZ, DR_VITESSES, DR_GARDE, DR_GARDE_U8 = 0x02, 0x04, 0x08, 0x20, 0x40   # = mailbox.PILOTE_*


@dataclass(frozen=True)
class Autre:
    nom: str
    vmax: float                     # u/s (même valeur que laisse.POSSEDABLES)
    conds: tuple                    # liste blanche : conditions toutes vraies
    off_action: int = 0             # garde de la carotte dans le pack : s16 à ce décalage (pair) ; 0 : jamais de carotte
    actions: frozenset = frozenset()   # valeurs de ce s16 où la carotte est permise (0..31)
    d_course: float = 0.0           # u : carotte de course devant le monstre ; négatif : DERRIÈRE lui (il fuit : Rat)
    d_ordre: float = 0.0            # u : carotte au repos pendant un ordre en attente
    d_attaque: float = 0.0          # u : « appât » : carotte rapprochée devant lui pendant t_appat s (attaque native)
    t_appat: float = 0.0
    forme: str = "carotte"          # "epingle" : épinglage seul (aucune garde de carotte exprimable dans le pack)
    vol: float = 0.0                # u : volant, hauteur tenue au-dessus du Link caché (0 : marcheur)
    vol_min: float = 0.0
    vol_max: float = 0.0
    essai: bool = False             # True : seulement avec le réglage « espèces à l'essai » de la partie
    drapeaux: int = 0               # forme spéciale (DR_*) : seulement avec un pack qui a le bit 17 ; sinon RIEN
                                    # n'est imposé à cette espèce (jamais la laisse classique, qui épinglerait)
    etage: float = 0.0              # u : écart de hauteur pilote / monstre toléré (0 : celui de la laisse)
    pourquoi: str = ""              # raison du classement (active par défaut / à l'essai), reprise dans l'étude

    @property
    def masque(self) -> int:
        m = 0
        for a in self.actions:
            m |= 1 << a
        return m


def _f(*v) -> frozenset:
    return frozenset(v)


AUTRES: dict[int, Autre] = {
    # Chuchu (d_a_cc) : action u8 +0x3FC bornée par ``cmplwi 8`` + table de 9 cas ; action 0 = marche, sous-état
    # u8 +0x3FD borné par ``cmplwi 0xB`` + table de 12 cas (4..11 : au sol, poursuite, bond) ; comportement +0x3F8
    # (2 = scène Mojo). Garde du pack : s16 +0x3FC = action << 8 | sous-état, donc 4..11 = action 0 seulement.
    206: Autre("Chuchu", 200.0,
               (("eq", 0x3FC, 1, 0), ("in", 0x3FD, 1, _f(4, 5, 6, 7, 8, 9, 10, 11)), ("in", 0x3F8, 1, _f(0, 1, 3))),
               off_action=0x3FC, actions=_f(4, 5, 6, 7, 8, 9, 10, 11), d_course=450.0, d_ordre=120.0,
               pourquoi="active : action et sous-état passent chacun par une table de sauts bornée, relue cas par "
                        "cas ; aucune écriture d'état"),
    # Peahat (d_a_ph) : mode u8 +0x45B borné par ``cmplwi 6`` + table de 7 cas ; mode 0 = vol ; type u8 +0x45A
    # (0 Peahat, sinon Seahat) ; sous-étape s16 +0x462 par comparaisons (1..4 ; 0 = init d'une image).
    # Garde du pack : s16 +0x45A = type << 8 | mode, donc 0 = Peahat en vol seulement.
    209: Autre("Peahat", 300.0,
               (("eq", 0x45A, 1, 0), ("eq", 0x45B, 1, 0), ("in", 0x462, 2, _f(1, 2, 3, 4))),
               off_action=0x45A, actions=_f(0), d_course=450.0, d_attaque=200.0, t_appat=1.5,
               vol=150.0, vol_min=80.0, vol_max=500.0,
               pourquoi="active : mode borné par une table de 7 cas, sous-étape par comparaisons ; le Seahat "
                        "(type != 0) est refusé"),
    # Miniblin (d_a_pt) : mode s16 +0x3EE par comparaisons (2 = au sol face au joueur) ; sous-mode s16 +0x3F0
    # (0..2 ; 3 = attaque en cours) borné par ``cmplwi 0xC`` ; +0x3D5 caché ; +0x426 invulnérable / mourant ;
    # f32 +0x438 recul. Garde du pack : s16 +0x3F0 dans {0, 1, 2}.
    247: Autre("Miniblin", 400.0,
               (("eq", 0x3D5, 1, 0), ("eq", 0x3EE, 2, 2), ("in", 0x3F0, 2, _f(0, 1, 2)), ("eq", 0x426, 2, 0),
                ("fmax", 0x438, 4, 0.01)),
               off_action=0x3F0, actions=_f(0, 1, 2), d_course=450.0, d_attaque=150.0, t_appat=1.5,
               pourquoi="active : mode distribué par comparaisons (aucune table sur le mode), seule table (13 cas) "
                        "bornée ; aucune écriture d'état"),
    # Darknut (d_a_tn) : action s16 +0x596 bornée par ``cmplwi 0x19`` + table de 26 cas (0 ronde, 4 approche,
    # 5 combat, 10 garde) ; mode s16 +0x594 (<= -100 : vol de dégâts) ; recul s16 +0xA4E ; arme tenue s32 +0xD84 ;
    # comportement u8 +0x3D8 (0 = ronde ; laisse.TN_COMPORTEMENTS refuse déjà les autres par les paramètres).
    191: Autre("Darknut", 300.0,
               (("in", 0x596, 2, _f(0, 4, 5, 10)), ("gt", 0x594, 2, -100), ("eq", 0xA4E, 2, 0), ("ne", 0xD84, 4, 0),
                ("eq", 0x3D8, 1, 0)),
               off_action=0x596, actions=_f(0, 4, 5, 10), d_course=700.0, d_ordre=450.0, essai=True,
               pourquoi="à l'essai : pas de pose d'arrêt (il court sur place contre l'épingle), le test du coup dans "
                        "le dos se calcule contre la carotte, armure et épée ne sont pas synchronisées entre les PC"),
    # Stalfos (d_a_st) : action s16 +0x3E2 par comparaisons (0 = marche et combat ; 5 tournoiement ; 0x1D et plus :
    # coupé en deux, tête, mort) ; +0x20CD compte à rebours de mort ; +0x2020 tête détachée. Le haut du corps coupé
    # est un second acteur du même proc (params & 0xF == 14 : refusé par ``laisse.eligible``).
    190: Autre("Stalfos", 300.0,
               (("eq", 0x3E2, 2, 0), ("eq", 0x20CD, 1, 0), ("eq", 0x2020, 1, 0)),
               off_action=0x3E2, actions=_f(0), d_course=500.0, d_attaque=200.0, t_appat=2.0, essai=True,
               pourquoi="à l'essai : son cycle de vie tient sur plusieurs acteurs (haut du corps, tête, masse), "
                        "non synchronisés ; l'action 0 regroupe marche et combat sans table à relire"),
    # Keese (d_a_ki) : action u8 +0x489 (décalage IMPAIR : aucune garde s16 propre, donc jamais de carotte) ;
    # 1 vol, 3 attaque permanente, 10 attaque ; +0x48C dormant, +0x48D sur un chemin, +0x494 se supprime ;
    # +0x484 type (2 = Keese de brasier), +0x486 chemin de retour (0xFF = aucun).
    215: Autre("Keese", 500.0,
               (("in", 0x489, 1, _f(1, 3, 10)), ("eq", 0x48C, 1, 0), ("eq", 0x48D, 1, 0), ("eq", 0x494, 1, 0),
                ("ne", 0x484, 1, 2), ("eq", 0x486, 1, 0xFF)),
               forme="epingle", vol=150.0, vol_min=55.0, vol_max=250.0, essai=True,
               pourquoi="à l'essai : épinglage seul (son action est un octet à un décalage impair, la garde de "
                        "carotte du pack ne peut pas la lire) ; certains exemplaires se suppriment d'eux-mêmes"),
    # Kargaroc (d_a_bb) : mode u8 +0x453 (3 = poursuite, sous-état +0x465 de 1 à 5 ; 4 et 7 exclus), mourant
    # +0xDF2, rail +0x4D1, porteur +0x455, caché +0x466, type +0x44E (1 démo, 5 et 6 porteurs), recul f32 +0x4BC.
    181: Autre("Kargaroc", 450.0,
               (("eq", 0xDF2, 1, 0), ("eq", 0x4D1, 1, 0), ("eq", 0x455, 1, 0), ("eq", 0x466, 1, 0),
                ("hors", 0x44E, 1, _f(1, 5, 6)), ("fmax", 0x4BC, 4, 0.01),
                ("ou", (("eq", 0x453, 1, 3), ("in", 0x465, 1, _f(1, 2, 3, 4, 5))),
                       (("hors", 0x453, 1, _f(3, 4, 7)), ("eq", 0x465, 1, 0)))),
               forme="epingle", vol=200.0, vol_min=80.0, vol_max=600.0, essai=True,
               pourquoi="à l'essai : épinglage seul (mode à un décalage impair) ; porteurs de Bokoblin et vols sur "
                        "rail exclus par lecture, mais non vus en jeu"),
    # Rat (d_a_nz) : mode u8 +0x3D8 par comparaisons, sous-état u8 +0x3D9 borné par ``cmplwi 7`` + table de 8 cas
    # (mode 0 : 1 guet, 3 course, 5 pause, 7 fuite) ; +0x3D1 rat à bombes ; +0x3DE gel ; +0x547 porte un objet.
    # Garde du pack : s16 +0x3D8 = mode << 8 | sous-état, donc {1, 3, 5, 7} = mode 0 seulement. Il court à
    # l'OPPOSÉ du joueur : la carotte est placée derrière lui.
    198: Autre("Rat", 600.0,
               (("eq", 0x3D8, 1, 0), ("in", 0x3D9, 1, _f(1, 3, 5, 7)), ("eq", 0x3D1, 1, 0), ("eq", 0x3DE, 1, 0),
                ("eq", 0x547, 1, 0)),
               off_action=0x3D8, actions=_f(1, 3, 5, 7), d_course=-400.0, d_attaque=300.0, t_appat=1.5, essai=True,
               pourquoi="à l'essai : il vole de vrais rubis au Link de l'autorité, se supprime seul (trou, chute "
                        "sous son origine) et rampe sur les murs (orientation de surface non vue en jeu)"),
    # ------------------------------------------------------------------------------------------------------
    # Étape 3 : formes spéciales (toutes à l'essai, toutes derrière le bit 17 du pack). AUCUNE écriture d'état.
    # Armos Knight (d_a_am, 202) : action u8 +0x3E0 (0 = vie ordinaire), mode u8 +0x3E1 borné par ``cmplwi 0xA``
    # + table de 11 cas ; 3..8 : éveillé, il saute vers le joueur. Garde : s16 +0x3E0 = action << 8 | mode.
    202: Autre("Armos Knight", 500.0,
               (("eq", 0x3E0, 1, 0), ("in", 0x3E1, 1, _f(3, 4, 5, 6, 7, 8))),
               off_action=0x3E0, actions=_f(3, 4, 5, 6, 7, 8), forme="cible", drapeaux=DR_SANS_EPINGLE, essai=True,
               pourquoi="à l'essai : carotte seule (il saute de lui-même vers le point où tu te tiens) ; ses "
                        "modes 3 à 8 sont relus comme bornés, mais leur rôle exact n'a pas été vu en jeu"),
    # Armos (d_a_am2, 203) : action u8 +0x3E8, mode u8 +0x3E9 borné par ``cmplwi 7`` + table de 8 cas.
    203: Autre("Armos", 500.0,
               (("eq", 0x3E8, 1, 0), ("in", 0x3E9, 1, _f(3, 4, 5))),
               off_action=0x3E8, actions=_f(3, 4, 5), forme="cible", drapeaux=DR_SANS_EPINGLE, essai=True,
               pourquoi="à l'essai : carotte seule (réveillé, il fonce vers le point où tu te tiens) ; rôle exact "
                        "des modes 3 à 5 non vu en jeu"),
    # Magtail (d_a_mt, 216) : action u8 +0x570 (0 marche, 1 dressé ; 2 et plus : touché, roulé en boule, mort).
    # Corps articulé : jamais d'épinglage (les segments ne suivraient pas). Garde lue en u8.
    216: Autre("Magtail", 500.0,
               (("in", 0x570, 1, _f(0, 1)),),
               off_action=0x570, actions=_f(0, 1), forme="cible", drapeaux=DR_SANS_EPINGLE | DR_GARDE_U8,
               essai=True,
               pourquoi="à l'essai : carotte seule (il rampe vers le point où tu te tiens), ordre « se dresser » "
                        "par la demande que le jeu pose lui-même ; morsure non commandée"),
    # Octorok (d_a_oq, 227) : type u8 +0x3DC (0, 1 : d'eau ou de terre ordinaires), +0x3E5 == 0, +0x3DF == 0x0A,
    # mode u8 +0x3E0 distribué par comparaisons (1..3 : sorti, il vise et tire) [HYPO : rôle exact de l'action
    # 0x0A et des modes 1 à 3 non vu en jeu]. Garde lue en u8.
    227: Autre("Octorok", 500.0,
               (("in", 0x3DC, 1, _f(0, 1)), ("eq", 0x3E5, 1, 0), ("eq", 0x3DF, 1, 0x0A),
                ("in", 0x3E0, 1, _f(1, 2, 3))),
               off_action=0x3E0, actions=_f(1, 2, 3), forme="cible", drapeaux=DR_SANS_EPINGLE | DR_GARDE_U8,
               essai=True,
               pourquoi="à l'essai : fixe, carotte seule (il vise et tire vers le point où tu te tiens) ; le tir "
                        "lui-même reste décidé par son IA"),
    # Boko Baba (214) : type u8 +0x3DC (0), action u8 +0x3E0 (0), mode u8 +0x3E1 borné par
    # ``cmplwi 7`` + table de 8 cas (0..3, 6, 7 : plante vivante, elle guette et mord).
    214: Autre("Boko Baba", 500.0,
               (("eq", 0x3DC, 1, 0), ("eq", 0x3E0, 1, 0), ("in", 0x3E1, 1, _f(0, 1, 2, 3, 6, 7))),
               off_action=0x3E0, actions=_f(0, 1, 2, 3, 6, 7), forme="cible", drapeaux=DR_SANS_EPINGLE, essai=True,
               pourquoi="à l'essai : plante fixe, carotte seule (elle se tourne et mord vers le point où tu te "
                        "tiens) ; écartée d'A3 jusqu'ici, non vue en jeu"),
    # Wizzrobe (d_a_wz, 208) : type u8 +0x464 (>= 0x0A : son projectile), action u8 +0x468 (0), mode u8 +0x469
    # borné par ``cmplwi 8`` + table de 9 cas, +0x46C != 1 (invocateur en scène). Il se téléporte de lui-même
    # autour du joueur : jamais d'épinglage. Garde : s16 +0x468 = action << 8 | mode.
    208: Autre("Wizzrobe", 500.0,
               (("in", 0x464, 1, _f(*range(10))), ("eq", 0x468, 1, 0), ("in", 0x469, 1, _f(*range(9))),
                ("ne", 0x46C, 1, 1)),
               off_action=0x468, actions=_f(*range(9)), forme="cible", drapeaux=DR_SANS_EPINGLE, essai=True,
               pourquoi="à l'essai : carotte seule (il réapparaît et tire vers le point où tu te tiens), ordre "
                        "« tir » qui vide son minuteur d'attente ; ses points d'apparition restent ceux du jeu"),
    # Poe (d_a_pw, 212) : action s16 +0x482 (0 = ronde), mode s16 +0x484 (13..16, 20 : vol libre), +0x462 == 1,
    # s32 +0x488 == -1 (aucun Link possédé). Il flotte : épinglage de x et z seulement, gardé par son mode.
    212: Autre("Poe", 250.0,
               (("eq", 0x482, 2, 0), ("in", 0x484, 2, _f(13, 14, 15, 16, 20)), ("eq", 0x462, 2, 1),
                ("eq", 0x488, 4, -1)),
               off_action=0x484, actions=_f(13, 14, 15, 16, 20), forme="epingle", drapeaux=DR_XZ | DR_GARDE,
               etage=500.0, essai=True,
               pourquoi="à l'essai : épinglage de x et z seulement (sa hauteur reste celle du jeu), ordre "
                        "« attaque » par l'écriture que le jeu fait lui-même ; il peut posséder le Link de "
                        "l'autorité (scène native), écarté d'A3 jusqu'ici"),
    # ReDead (d_a_rd, 224) : mode s32 +0x3C8 (0 attente, 4 marche ; cri, étreinte, retour, cercueil : exclus). Le
    # jeu range ce mode sans le borner : il n'est JAMAIS écrit ici, seulement lu. Garde : s16 +0x3CA (moitié
    # basse du s32). Épinglage lent de x et z, vitesses gardées (sa marche s'anime), aucune carotte.
    224: Autre("ReDead", 90.0,
               (("in", 0x3C8, 4, _f(0, 4)),),
               off_action=0x3CA, actions=_f(0, 4), forme="epingle", drapeaux=DR_XZ | DR_VITESSES | DR_GARDE,
               essai=True,
               pourquoi="à l'essai : épinglage lent de x et z, AUCUNE écriture de mode (table non bornée) ; cri et "
                        "étreinte restent natifs (le Link de l'autorité seulement), il rentre chez lui s'il sort "
                        "de sa zone"),
}

# Preuves RPX de l'étape 2 (adresse, mot attendu) : chaque décalage des conditions ci-dessus est LU ou ÉCRIT par le
# code de l'espèce, et chaque table de sauts est précédée de sa borne (cmplwi + bgt).
PREUVES_AUTRES = {
    206: (
        (0x0210DF9C, 0x899F03FC),   # lbz r12, 0x3fc(r31)      action
        (0x0210DFBC, 0x897F03F8),   # lbz r11, 0x3f8(r31)      comportement
        (0x0210E000, 0x280C0008),   # cmplwi r12, 8
        (0x0210E024, 0x41811E18),   # bgt (défaut)
        (0x0210E060, 0x88BF03FD),   # lbz r5, 0x3fd(r31)       sous-état (cas 0 de l'action)
        (0x0210E068, 0x2806000B),   # cmplwi r6, 0xb
        (0x0210E06C, 0x41810858),   # bgt (défaut)
    ),
    209: (
        (0x023CC34C, 0x899E045B),   # lbz r12, 0x45b(r30)      mode
        (0x023CC35C, 0x280C0006),   # cmplwi r12, 6
        (0x023CC364, 0x41812540),   # bgt (défaut)
        (0x023CC398, 0x891E045A),   # lbz r8, 0x45a(r30)       type (cas 0 du mode)
        (0x023CC3A4, 0x4082033C),   # bne (Seahat)
        (0x023CC3A8, 0xA81E0462),   # lha r0, 0x462(r30)       sous-étape
    ),
    247: (
        (0x024473D0, 0xA93E03EE),   # lha r9, 0x3ee(r30)       mode
        (0x024473E4, 0x28090003),   # cmplwi r9, 3
        (0x024474B8, 0x28090001),   # cmplwi r9, 1
        (0x024474F0, 0xA91E0426),   # lha r8, 0x426(r30)       invulnérable / mourant
        (0x02447740, 0x897E03D5),   # lbz r11, 0x3d5(r30)      caché
        (0x02448654, 0xA91E03F0),   # lha r8, 0x3f0(r30)       sous-mode
        (0x02448658, 0x2808000C),   # cmplwi r8, 0xc
        (0x0244865C, 0x418104D8),   # bgt (défaut)
        (0x02448A84, 0xC15E0438),   # lfs f10, 0x438(r30)      recul
    ),
    191: (
        (0x024C41D8, 0xA91E0596),   # lha r8, 0x596(r30)       action
        (0x024C41F0, 0x28060019),   # cmplwi r6, 0x19
        (0x024C3EF8, 0xA8DE0594),   # lha r6, 0x594(r30)       mode
        (0x024C403C, 0xA93E0A4E),   # lha r9, 0xa4e(r30)       recul
        (0x024BD24C, 0x80BE0D84),   # lwz r5, 0xd84(r30)       arme tenue
        (0x024C0EC0, 0x88DE03D8),   # lbz r6, 0x3d8(r30)       comportement
    ),
    190: (
        (0x02497374, 0xA97F03E2),   # lha r11, 0x3e2(r31)      St_move : action
        (0x024973A4, 0x28050010),   # cmplwi r5, 0x10
        (0x024973B0, 0x28050000),   # cmplwi r5, 0
        (0x024973B4, 0x4182006C),   # beq (cas 0)
        (0x02494010, 0x891F20CD),   # lbz r8, 0x20cd(r31)      compte à rebours de mort
        (0x0249448C, 0x88DF2020),   # lbz r6, 0x2020(r31)      tête détachée
        (0x02495CF8, 0x5549073E),   # rlwinm r9, r10, 0,28,31  create : comportement = params & 0xF
    ),
    215: (
        (0x0219AC40, 0x897F0494),   # lbz r11, 0x494(r31)      se supprime
        (0x0219ACC8, 0x88BF048C),   # lbz r5, 0x48c(r31)       dormant
        (0x0219AECC, 0x899F0489),   # lbz r12, 0x489(r31)      action
        (0x0219AEEC, 0x881F048D),   # lbz r0, 0x48d(r31)       chemin
        (0x0219D5E8, 0x997E0484),   # stb r11, 0x484(r30)      create : type
        (0x0219D5FC, 0x552A863E),   # rlwinm r10, r9, 16,24,31 create : chemin de retour = (params >> 16) & 0xFF
        (0x0219D608, 0x995E0486),   # stb r10, 0x486(r30)
    ),
    181: (
        (0x0205ECF4, 0x88DE0466),   # lbz r6, 0x466(r30)       caché
        (0x0205EDDC, 0xC15E04BC),   # lfs f10, 0x4bc(r30)      recul
        (0x0205EEBC, 0x891E0DF2),   # lbz r8, 0xdf2(r30)       mourant
        (0x0205EEEC, 0x897E04D1),   # lbz r11, 0x4d1(r30)      rail
        (0x0205EF1C, 0x88DE0465),   # lbz r6, 0x465(r30)       sous-état
        (0x0205F26C, 0x889E0455),   # lbz r4, 0x455(r30)       porteur
        (0x0205F824, 0x88FE0453),   # lbz r7, 0x453(r30)       mode
        (0x0205F828, 0x2C070003),   # cmpwi r7, 3
        (0x0205FCFC, 0x2806000B),   # cmplwi r6, 0xb           sous-état borné avant sa table
        (0x0205FE64, 0x881E044E),   # lbz r0, 0x44e(r30)       type
    ),
    198: (
        (0x0230C118, 0x88BE03D1),   # lbz r5, 0x3d1(r30)       rat à bombes
        (0x0230C148, 0x891E03D8),   # lbz r8, 0x3d8(r30)       mode
        (0x0230C684, 0x895E03D9),   # lbz r10, 0x3d9(r30)      sous-état (mode 0)
        (0x0230C68C, 0x28080007),   # cmplwi r8, 7
        (0x0230C690, 0x418108E8),   # bgt (défaut)
        (0x0230BB30, 0x897F03DE),   # lbz r11, 0x3de(r31)      gel
        (0x0230CC68, 0x9B5E0547),   # stb r26, 0x547(r30)      porte un objet
        (0x0230F394, 0x5580C63E),   # rlwinm r0, r12, 24,24,31 create : (params >> 8) & 0xFF
        (0x0230F3A0, 0x981F03D1),   # stb r0, 0x3d1(r31)
    ),
    # Mothula (refus des ailes coupées, ``cibles._gm``) : create range (params >> 8) & 0xFF dans +0x3E9
    204: (
        (0x02150844, 0x54A7C63E),   # rlwinm r7, r5, 24,24,31
        (0x02150848, 0x98FE03E9),   # stb r7, 0x3e9(r30)
        (0x0214D1A4, 0x890E03E9),   # lbz r8, 0x3e9(r14)       execute : aile coupée ?
    ),
}
# Étape 3 (formes spéciales) : mêmes règles ; pour le ReDead, le mot 0x0245AE54 est l'écriture NON bornée du mode
# par le jeu (raison de l'interdiction d'écrire), 0x0245A1B8 sa lecture.
PREUVES_AUTRES.update({
    202: ((0x020494DC, 0x895F03E0),   # lbz r10, 0x3e0(r31)      action
          (0x02049528, 0x891F03E1)),  # lbz r8, 0x3e1(r31)       mode
    203: ((0x0204CE18, 0x881F03E8),   # lbz r0, 0x3e8(r31)       action
          (0x0204D658, 0x899F03E9),   # lbz r12, 0x3e9(r31)      mode
          (0x0204D660, 0x280C0007)),  # cmplwi r12, 7
    216: ((0x021D8D78, 0x889F0570),   # lbz r4, 0x570(r31)       action
          (0x021D8EC8, 0x881F0570), (0x021D8ECC, 0x2C000002)),   # lbz r0, 0x570 ; cmpwi r0, 2
    227: ((0x023C0328, 0x897E03DC),   # lbz r11, 0x3dc(r30)      type
          (0x023C324C, 0x895F03E5), (0x023C3250, 0x2C0A0000),    # lbz r10, 0x3e5 ; cmpwi r10, 0
          (0x023C14E8, 0x881F03DF),   # lbz r0, 0x3df(r31)
          (0x023C155C, 0x881F03E0)),  # lbz r0, 0x3e0(r31)       mode (comparé à 3 puis à 1 : pas de table)
    214: ((0x020BB9F0, 0x881F03DC), (0x020BB9F4, 0x2C000000),    # lbz r0, 0x3dc ; cmpwi r0, 0
          (0x020BD048, 0x881E03E0),   # lbz r0, 0x3e0(r30)       action
          (0x020BD0A8, 0x899E03E1), (0x020BD0AC, 0x280C0007)),   # lbz r12, 0x3e1 ; cmplwi r12, 7
    208: ((0x024E222C, 0x895E0464), (0x024E2230, 0x280A000A),    # lbz r10, 0x464 ; cmplwi r10, 0x0A
          (0x024E3DA4, 0x881F0468),   # lbz r0, 0x468(r31)       action
          (0x024E3E0C, 0x891F0469), (0x024E3E10, 0x28080008), (0x024E3E14, 0x41810A30),   # mode borné : 9 cas
          (0x024E2238, 0x881E046C), (0x024E223C, 0x2C000000)),   # lbz r0, 0x46c ; cmpwi r0, 0
    212: ((0x0244B514, 0xA8DE0482),   # lha r6, 0x482(r30)       action
          (0x0244AC04, 0xA81F0484), (0x0244AC08, 0x2C000026),    # lha r0, 0x484 ; cmpwi r0, 0x26
          (0x0244C6A0, 0xA8FE0462), (0x0244C6A4, 0x2C070001),    # lha r7, 0x462 ; cmpwi r7, 1
          (0x0244A5BC, 0x807F0488), (0x0244A5C0, 0x2C03FFFF)),   # lwz r3, 0x488 ; cmpwi r3, -1
    224: ((0x0245A1B8, 0x801F03C8),   # lwz r0, 0x3c8(r31)       mode (s32)
          (0x0245AE54, 0x93FD03C8)),  # stw r31, 0x3c8(r29)      rangé SANS borne : jamais écrit par le mod
})
# Bornes des modes relues en plus : proc -> ((adresse, mot), ...) : le ``cmplwi`` et le branchement qui le suit
BORNES_FORMES = {
    202: ((0x02049534, 0x280C000A), (0x0204953C, 0x41810818)),     # cmplwi r12, 0xA ; bgt (défaut)
    203: ((0x0204D660, 0x280C0007), (0x0204D664, 0x41810748)),     # cmplwi r12, 7 ; bgt
    227: ((0x023C1564, 0x28000003), (0x023C156C, 0x40800048)),     # cmplwi r0, 3 ; bge (3 et plus : à part)
    214: ((0x020BD0AC, 0x280C0007), (0x020BD0B4, 0x4181105C)),     # cmplwi r12, 7 ; bgt
    208: ((0x024E3E10, 0x28080008), (0x024E3E14, 0x41810A30)),     # cmplwi r8, 8 ; bgt
}
# Tables de sauts des espèces actives par défaut : proc -> (adresse, nombre de cas) ; chaque entrée est un ``b``.
TABLES_AUTRES = {
    206: ((0x0210E03C, 9), (0x0210E084, 12)),
    209: ((0x023CC37C, 7),),
}
# Profils (table 0x101F3EE0 + 4 × proc) : proc -> (adresse du profil, taille de l'instance) ; tous du groupe 2
# (ennemis : octet +0x2C), seul groupe que le relais des monstres et le pack tiennent.
PROFILS_AUTRES = {
    206: (0x101B3BEC, 0xF10), 209: (0x101CE834, 0x12D8), 247: (0x101CF074, 0xEE4), 191: (0x101D2868, 0x1C5C),
    190: (0x101D0FD0, 0x25B0), 215: (0x101B82B4, 0x10BC), 181: (0x10190624, 0x13D8), 198: (0x101C77E0, 0x11CC),
    # étape 3 (formes spéciales)
    202: (0x1018FA04, 0x11BC), 203: (0x1018FBBC, 0xF60), 208: (0x101D3B6C, 0x10B0), 216: (0x101BAFD0, 0x1DD8),
    227: (0x101CE48C, 0x1194), 214: (0x10191ED0, 0x1104), 212: (0x101CF1A4, 0xF8C), 224: (0x101CF658, 0xF1C),
}
# Espèces relayées laissées de côté : leur profil relu (adresse, taille, groupe), pour la documentation et les tests
PROFILS_ECARTES = {204: (0x101B54A8, 0x1290, 2), 207: (0x1019197C, 0xE18, 2), 119: (0x101B524C, 0x101C, 2),
                   205: (0x101B8958, 0xF08, 3)}

# Qui est qui (relu dans le RPX) : le process 202 (d_a_am, 0x11BC octets) est l'ARMOS KNIGHT, le 203 (d_a_am2,
# 0xF60) l'ARMOS. Preuves : seul le code du 202 compare le nom d'un autre acteur aux bombes (294, 295 : il les
# avale), et le pilier de la Tour des Dieux (d_a_obj_hcbh) ne se dit « cassé par un Armos » (mBrokenBy = 4) que
# pour un acteur de nom 202 (c'est l'Armos Knight qui les brise en sautant).
ARMOS_CODE = {202: (0x02048B3C, 0x0204C5F8), 203: (0x0204C5F8, 0x02050684)}   # [début, fin[ du code de l'espèce
PREUVES_ARMOS = (
    (0x02048E48, 0xA95F0008), (0x02048E4C, 0x2C0A0126),      # lha r10, 8(r31) ; cmpwi r10, 294 (bombe du sac)
    (0x02048F14, 0xA81F0008), (0x02048F18, 0x2C000127),      # lha r0, 8(r31) ; cmpwi r0, 295 (Bomb2)
    (0x020490A8, 0xA8FF0008), (0x020490AC, 0x2C070126),
    (0x02049170, 0xA81F0008), (0x02049174, 0x2C000127),
    (0x0204AF2C, 0xA8A30008), (0x0204AF30, 0x2C050126),
    (0x0204AF54, 0xA9230008), (0x0204AF58, 0x2C090127),
    (0x023520F4, 0xA81F000E), (0x023520F8, 0x2C0000CA),      # pilier : lha r0, 0xE(r31) ; cmpwi r0, 202
)

HAUT_PAS = 50.0            # u : un appui sur PagePréc / PageSuiv (commande de hauteur des volants)
HAUT_MAX = 500.0           # u : borne de la commande de hauteur (le serveur et l'autorité la rebornent)
ETAGE_VOL = 2000.0         # u : un volant n'a pas d'étage ; au-delà, rien n'est imposé


def espece(proc):
    """Espèce pilotable (``Espece`` du socle ou ``Autre`` de l'étape 2), ou None."""
    return ESPECES.get(proc) or AUTRES.get(proc)


def a_l_essai() -> list[int]:
    return sorted(p for p, e in AUTRES.items() if e.essai)


def essais(v) -> list[int]:
    """Réglage « espèces à l'essai » nettoyé : True (toutes), liste de numéros de process ou de noms, texte séparé
    par des virgules ; rend la liste triée des process à l'essai retenus."""
    dispo = a_l_essai()
    if v is True:
        return dispo
    if isinstance(v, str):
        v = v.replace(";", ",").split(",")
    if not isinstance(v, (list, tuple, set, frozenset)):
        return []
    noms = {AUTRES[p].nom.lower(): p for p in dispo}
    out = set()
    for x in list(v)[:32]:
        if isinstance(x, bool):
            continue
        if isinstance(x, int) and x in dispo:
            out.add(x)
        elif isinstance(x, str):
            t = x.strip().lower()
            if t in ("tout", "toutes", "all"):
                return dispo
            if t in noms:
                out.add(noms[t])
            elif t.isdigit() and int(t) in dispo:
                out.add(int(t))
    return sorted(out)


def _nombre(v) -> bool:
    """Vrai pour un nombre fini convertible en flottant (ni booléen, ni NaN, ni infini, ni entier démesuré :
    ``math.isfinite(10**400)`` lève OverflowError)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    try:
        return math.isfinite(v)
    except OverflowError:
        return False


def hauteur(v) -> float:
    """Commande de hauteur d'un volant (u, relative à la hauteur de croisière) nettoyée et bornée."""
    if not _nombre(v):
        return 0.0
    return max(-HAUT_MAX, min(HAUT_MAX, float(v)))


def hauteur_vol(esp, haut=0.0) -> float:
    """Hauteur (u) tenue par un volant au-dessus du Link caché du pilote ; 0 pour un marcheur."""
    vol = getattr(esp, "vol", 0.0)
    if not vol:
        return 0.0
    return max(esp.vol_min, min(esp.vol_max, vol + hauteur(haut)))


def drapeaux(proc) -> int:
    """Drapeaux de forme spéciale de l'espèce (0 : pilotage ordinaire)."""
    return int(getattr(AUTRES.get(proc), "drapeaux", 0) or 0)


def forme_cible(proc) -> bool:
    """Espèce « cible » : jamais replacée, elle vise le point où se tient le Hanteur (aucun recollage du Link
    caché sur elle, aucune prédiction locale)."""
    e = AUTRES.get(proc)
    return e is not None and e.forme == "cible"


def gestes_appat(proc) -> list[str]:
    """Gestes que le pilote peut donner à une espèce sans ordre CALL 10 : « attaque » par l'appât."""
    e = AUTRES.get(proc)
    return ["attaque"] if e is not None and e.forme == "carotte" and e.d_attaque > 0 else []


def _f32(v) -> float | None:
    if v is None:
        return None
    x = struct.unpack(">f", struct.pack(">I", int(v) & 0xFFFFFFFF))[0]
    return x if math.isfinite(x) else None


def _cond(c, lire) -> bool:
    op = c[0]
    if op == "ou":
        return all(_cond(x, lire) for x in c[1]) or all(_cond(x, lire) for x in c[2])
    _op, off, taille, val = c
    v = lire(off, taille)
    if v is None:
        return False
    if op == "fmax":
        x = _f32(v)
        return x is not None and abs(x) <= val
    if op == "eq":
        return v == val
    if op == "ne":
        return v != val
    if op == "in":
        return v in val
    if op == "hors":
        return v not in val
    if op == "gt":
        return v > val
    return False                    # opération inconnue : jamais épinglé


def decalages(conds) -> list[tuple[int, int]]:
    """(décalage, taille) de chaque lecture d'une liste de conditions (tests de bornes)."""
    out = []
    for c in conds:
        if c[0] == "ou":
            out += decalages(c[1]) + decalages(c[2])
        else:
            out.append((c[1], c[2]))
    return out


# Recharges (s) par geste, tenues par le serveur ; ``None`` = l'espèce par défaut. La garde est libre.
RECHARGES_DEFAUT = {"attaque": 1.5, "lourde": 4.0, "garde": 0.0}
RECHARGES = {
    189: {"attaque": 1.5, "lourde": 4.0, "garde": 0.0},      # Bokoblin
    188: {"attaque": 2.0, "lourde": 4.5, "garde": 0.0},      # Moblin (plus lent, plus fort)
    206: {"attaque": 2.5, "lourde": 4.0, "garde": 0.0},      # Chuchu (bond)
    215: {"attaque": 3.0, "lourde": 4.0, "garde": 0.0},      # Keese (piqué)
    191: {"attaque": 3.0, "lourde": 6.0, "garde": 0.0},      # Darknut
    209: {"attaque": 4.0, "lourde": 4.0, "garde": 0.0},      # Peahat (appât : il fonce, hélice en avant)
    247: {"attaque": 2.0, "lourde": 2.0, "garde": 0.0},      # Miniblin (appât : coup de fourche)
    190: {"attaque": 3.0, "lourde": 3.0, "garde": 0.0},      # Stalfos (appât : coup de masse)
    181: {"attaque": 4.0, "lourde": 4.0, "garde": 0.0},      # Kargaroc (épinglage seul : aucun geste n'a d'effet)
    198: {"attaque": 3.0, "lourde": 3.0, "garde": 0.0},      # Rat (appât : bond, vol de rubis)
    224: {"attaque": 3.0, "lourde": 8.0, "garde": 0.0},      # ReDead (cri, étreinte natifs : aucun geste commandé)
    208: {"attaque": 4.0, "lourde": 6.0, "garde": 0.0},      # Wizzrobe (tir : boule de feu)
    212: {"attaque": 5.0, "lourde": 6.0, "garde": 0.0},      # Poe (charge à la lanterne)
    216: {"attaque": 4.0, "lourde": 6.0, "garde": 0.0},      # Magtail (se dresse)
    202: {"attaque": 4.0, "lourde": 6.0, "garde": 0.0},      # Armos Knight (aucun geste commandé : il saute seul)
    203: {"attaque": 4.0, "lourde": 6.0, "garde": 0.0},      # Armos (idem)
    227: {"attaque": 3.0, "lourde": 5.0, "garde": 0.0},      # Octorok (tir décidé par son IA)
    214: {"attaque": 3.0, "lourde": 5.0, "garde": 0.0},      # Boko Baba (morsure décidée par son IA)
}
MULT_MIN, MULT_MAX = 0.5, 3.0
MULTS = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)     # choix du lobby


def multiplicateur(v, defaut: float = 1.0) -> float:
    """Réglage « recharge des attaques » nettoyé (× 0,5 à × 3 ; valeur illisible : ``defaut``)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            v = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return defaut
    if not _nombre(v):
        return defaut
    v = float(v)
    return round(max(MULT_MIN, min(MULT_MAX, v)), 2)


def recharge(proc, geste: str, mult: float = 1.0) -> float:
    """Recharge (s) du ``geste`` pour l'espèce ``proc``, réglage de partie compris."""
    t = RECHARGES.get(proc, RECHARGES_DEFAUT).get(geste, RECHARGES_DEFAUT.get(geste, 0.0))
    return round(t * multiplicateur(mult), 2)


def pilotable(proc, essai=False) -> bool:
    """Espèce pilotée. ``essai`` : True (toutes les espèces à l'essai), ou la collection des process à l'essai
    permis par le réglage de la partie."""
    e = espece(proc)
    if e is None:
        return False
    if not e.essai:
        return True
    if isinstance(essai, (bool, int)):
        return bool(essai)
    try:
        return proc in essai
    except TypeError:
        return False


def _fini2(v) -> tuple[float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    try:
        a = (float(v[0]), float(v[1]))
    except (TypeError, ValueError, OverflowError):      # entier JSON démesuré (10**400)
        return None
    if isinstance(v[0], bool) or isinstance(v[1], bool):
        return None
    return a if all(math.isfinite(x) and abs(x) < 1e5 for x in a) else None


def vitesse(v, vmax: float) -> tuple[float, float] | None:
    """Vitesse horizontale annoncée par un Hanteur, nettoyée : None si illisible (NaN, infini, mauvais type), sinon
    plafonnée à ``VIT_PLAFOND × vmax`` (même direction)."""
    a = _fini2(v)
    if a is None:
        return None
    n = math.hypot(*a)
    lim = VIT_PLAFOND * max(0.0, float(vmax))
    if n > lim > 0:
        a = (a[0] * lim / n, a[1] * lim / n)
    elif lim <= 0:
        a = (0.0, 0.0)
    return a


def mesurer(prev, pos, dt: float) -> tuple[float, float]:
    """Vitesse horizontale (u/s) entre deux positions du Link caché ; (0, 0) si le pas de temps est inutilisable ou
    si le déplacement est un saut (téléportation, recollage)."""
    if prev is None or pos is None or not 0.005 <= dt <= 0.5:
        return (0.0, 0.0)
    vx, vz = (pos[0] - prev[0]) / dt, (pos[2] - prev[2]) / dt
    if not (math.isfinite(vx) and math.isfinite(vz)) or math.hypot(vx, vz) > 3000.0:
        return (0.0, 0.0)
    return (vx, vz)


def interpoler(prev, recu, vit, dt: float, vmax: float, etage: float = 250.0) -> tuple[float, float, float]:
    """Position imposée au monstre à ce tick : depuis ``prev`` (position imposée, ou lue, au tick précédent) vers
    ``recu`` (dernière position du Link caché) avancée d'au plus ``AVANCE`` s de ``vit``. Jamais plus de
    ``vmax × SUR_VITESSE × dt`` par tick (le monstre ne court pas plus vite que nature, rattrapage compris) ; la
    hauteur suit celle du Link caché à la même allure ; aucun déplacement si l'écart de hauteur dépasse ``etage``
    (autre étage : jamais de traversée de plancher)."""
    dt = max(0.0, min(0.2, float(dt)))
    x, y, z = prev
    if abs(recu[1] - y) > etage:
        return (x, y, z)
    v = vit if vit is not None else (0.0, 0.0)
    av = min(AVANCE, dt)
    cx, cz = recu[0] + v[0] * av, recu[2] + v[1] * av
    pas = max(0.0, float(vmax)) * SUR_VITESSE * dt
    d = math.hypot(cx - x, cz - z)
    if d > 1e-6 and pas > 0:
        k = min(1.0, pas / d)
        x, z = x + (cx - x) * k, z + (cz - z) * k
    dy = recu[1] - y
    y += max(-pas, min(pas, dy))
    return (x, y, z)


def avant(rot_y: int) -> tuple[float, float]:
    """Direction avant (x, z) d'un angle Y du jeu (s16, avant = (sin, cos))."""
    a = (int(rot_y) & 0xFFFF) * math.pi / 32768.0
    return (math.sin(a), math.cos(a))


def carotte(pos, rot_y: int, vit, esp: Espece, ordre_en_attente: bool = False, link_proche: bool = False,
            libre: bool = False):
    """Point que l'IA du monstre prendra pour le joueur pendant son execute : (x, y, z), ou None (pas de carotte :
    l'IA voit le vrai Link).

    * ``libre`` (un ordre se joue) ou ``link_proche`` (un vrai Link à moins de ``laisse.MELEE``) : None ;
    * pilote en mouvement (|vit| > ``VIT_MIN``) : à ``d_course`` devant le monstre, dans le sens de la marche ;
    * au repos avec un ordre en attente : à ``d_ordre`` devant lui selon ``rot_y`` (il se met en combat face à
      l'avant, l'ordre peut alors partir) ;
    * au repos sans ordre : None."""
    if libre or link_proche:
        return None
    v = vit if vit is not None else (0.0, 0.0)
    n = math.hypot(v[0], v[1])
    if n > VIT_MIN:
        return (pos[0] + v[0] / n * esp.d_course, pos[1], pos[2] + v[1] / n * esp.d_course)
    if ordre_en_attente:
        ax, az = avant(rot_y)
        return (pos[0] + ax * esp.d_ordre, pos[1], pos[2] + az * esp.d_ordre)
    return None


def etat_permis(esp, lire) -> bool:
    """Le monstre est dans un état de la liste blanche (``lire(décalage, taille) -> int | None``, lecture seule).
    Socle (``Espece``) : action dans la liste, mode au-dessus de ``mode_min``. Étape 2 (``Autre``) : toutes ses
    conditions vraies. Illisible : False (jamais épinglé dans le doute)."""
    try:
        if isinstance(esp, Autre):
            return bool(esp.conds) and all(_cond(c, lire) for c in esp.conds)
        a = lire(esp.off_action, 2)
        m = lire(esp.off_mode, 2)
    except Exception:
        return False
    if a is None or m is None:
        return False
    return 0 <= a <= ACTION_MAX and a in esp.actions and m > esp.mode_min


def carotte_espece(pos, rot_y: int, vit, esp, ordre_en_attente: bool = False, appat: bool = False):
    """Carotte d'une espèce de l'étape 2 (None : aucune). « epingle » : jamais. ``appat`` (le pilote a demandé une
    attaque que l'espèce n'a pas en ordre CALL 10) : point à ``d_attaque`` devant le monstre, que son IA prend
    pour le joueur à portée : elle attaque d'elle-même. Sinon, la carotte de course (``carotte`` ; ``d_course``
    négatif : derrière le monstre, pour une espèce qui fuit le joueur)."""
    if isinstance(esp, Autre):
        if esp.forme != "carotte" or not esp.off_action or not esp.masque:
            return None
        if appat and esp.d_attaque > 0:
            ax, az = avant(rot_y)
            return (pos[0] + ax * esp.d_attaque, pos[1], pos[2] + az * esp.d_attaque)
        if not esp.d_ordre:
            ordre_en_attente = False
    return carotte(pos, rot_y, vit, esp, ordre_en_attente=ordre_en_attente)


def geste_bouton(cut_type) -> str | None:
    """Bouton du Hanteur lu dans l'état de son Link caché (mCutType, Link+0x3AC) : même table que les ordres."""
    return ordres_mod.geste(cut_type)


def champ_pil(v, vmax: float | None = None):
    """Champ ``pil`` d'un ``state`` (client du Hanteur -> serveur) : ``[vx, vz]`` ou ``[vx, vz, hauteur]`` (volants) ;
    rend la vitesse nettoyée ou None (champ absent ou illisible : pas de pilotage demandé)."""
    if isinstance(v, (list, tuple)) and len(v) == 3:
        h = v[2]
        if not _nombre(h):
            return None                              # hauteur illisible : tout le champ est refusé
        v = v[:2]
    return vitesse(v, vmax if vmax is not None else 1000.0)


def champ_haut(v) -> float:
    """Commande de hauteur portée par le champ ``pil`` (troisième valeur, absente : 0)."""
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return hauteur(v[2])
    return 0.0
