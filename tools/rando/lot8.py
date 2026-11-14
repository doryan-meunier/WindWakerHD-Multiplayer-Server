"""Randomizer par fichiers générés localement, étape 4 : lot 8 (docs/etudes/rando_fichiers.md §3, éléments 19 à 23).

Sans épée, entrées mélangées (donjons ; donjons et grottes), cartes au trésor mélangées. Tout passe par le pack
WWMP_Rando GÉNÉRÉ sur chaque PC à partir de son propre dump (rando/fichiers.py) : sorties de salles réécrites
(SCLS), vent de retour des boss (WARP_WIND_AFTER), zones de mort de la Montagne de Feu et de l'Île de Glace (VolTag),
liste des cartes de la carte marine (cmapdat.bin) et trésors engloutis qui les attendent, lignes de code du pack.
Une option par élément, toutes COUPÉES par défaut :

======================  ===================================================================================
option                  ce qu'elle fait
======================  ===================================================================================
sans_epee               (exige monde = ouvert) aucune épée de la partie : ni épée du héros ni Épée de légende au
                        départ, coffre de la chambre de l'épée qui donne un rubis rouge, barrière d'Hyrule levée
                        à la création, Spectre-Ganon blessé par la Masse, épée prêtée chez Orca et contre
                        Ganondorf (swordless.asm de TWWHD)
entrees = donjons       (exige monde = ouvert) les 5 entrées de donjon mélangées entre elles ; « donjons_grottes » :
          donjons_       en plus les 22 entrées de grotte mélangées entre elles (jamais une grotte avec un donjon).
          grottes        Entrées COUPLÉES : on ressort par où l'on est entré. Le tirage (depuis la graine) est
                        écrit dans la table (« tirage_entrees ») : le générateur ne tire rien
cartes_melangees        (exige lieux_tresors) chaque carte mène à un autre trésor englouti ; le tirage est écrit
                        dans la table (« tirage_cartes »)
======================  ===================================================================================

Ce qui vient de TWWHD-Randomizer (SuperDude88 et contributeurs, licence MIT, « Copyright (c) 2024 SuperDude88 »,
texte : tools/rando/logique/LICENSE_TWWHD.txt) : table des entrées (logic/data/entrance_shuffle_table.yaml,
recopiée ici pour les donjons et les grottes), écriture des entrées (command/WriteEntrances.cpp : sorties, vent de
retour, zones de mort, cas de la grotte intérieure de l'Île de Glace), cartes (command/WriteCharts.cpp,
filetypes/charts.cpp), correctifs d'assembleur (swordless.asm, entrance_rando_features.asm, deux parties de
flexible_entrances.asm ; convertis dans tools/rando/lot8_asm/ par tools/rando/port_twwhd.py). Le tirage, la
validation et la logique (réécriture du graphe de rando_logique/monde.json) sont à nous.

Écarts avec TWWHD, et pourquoi (le détail est dans docs/joueurs/RANDOMIZER.md) :

* catégories jamais mélangées entre elles, entrées toujours couplées (``mix_pools`` et ``decouple_entrances`` non
  portés : en coop, le retour et la sauvegarde deviennent difficiles à prouver) ; portes, fontaines de fée, boss,
  mini-boss et entrées « diverses » non mélangés (voir docs/etudes/rando_fichiers.md §10) : les points d'apparition
  devant les portes de boss (« add_boss_door_return_spawns ») ne servent qu'au mélange des boss, ils ne sont donc
  pas ajoutés ; les zones de mort (VolTag) le sont, elles servent aux grottes ;
* une entrée tirée sur elle-même n'est PAS réécrite (le jeu d'origine), sauf la sortie de la grotte intérieure de
  l'Île de Glace, qui suit toujours l'entrée de l'Île de Glace (on ressort là où est le bateau) ;
* vent de retour : la sortie du vent d'origine du donjon dont la porte mène au boss (TWWHD écrit pour les Bois
  Défendus l'extérieur de l'Île de la Forêt ; nous gardons l'intérieur, comme le jeu d'origine) ;
* sans épée : la variable « swordless » de TWWHD n'existe pas ici ; ce qu'elle déclenchait (barrière levée) est posé
  par le crochet de création du lot 7 (bits 0x2C02 et 0x3B08) ; le site 0x0213AE90 (« rlwinm. », hors des
  mnémoniques acceptées) est écrit en ``.int`` ;
* crawl forcé (``custom_crawl_check``) et sortie des enchères non portés : ils ne servent qu'aux portes.

Règles : chaque édition relit ce qu'elle remplace (sortie d'origine, zone de mort, valeurs du vent, index de carte
des trésors) ; options coupées = rien d'écrit, rien de changé dans la logique ni dans le pack. Ce module ne touche ni
au jeu ni au réseau.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct

from rando import lot7 as L7  # noqa: E402

ICI = os.path.dirname(os.path.abspath(__file__))
DOSSIER_ASM = os.path.join(ICI, "lot8_asm")

OPTIONS_BOOL = ("sans_epee", "cartes_melangees")
MODES_ENTREES = ("aucune", "donjons", "donjons_grottes")
ENTREES_DEFAUT = "aucune"
OPTIONS = ("entrees",) + OPTIONS_BOOL
TIRAGES = ("tirage_entrees", "tirage_cartes")
ESSAIS_ENTREES = 2000       # tirages d'entrées essayés (un sur dix environ passe le tri du graphe, voir structure_ok)


class ErreurLot8(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------
# Table des entrées (entrance_shuffle_table.yaml de TWWHD, MIT) : donjons et grottes, dans l'ordre de TWWHD.
# (catégorie, aller, retour) ; aller / retour = (zone de départ, zone d'arrivée, stage du fichier, salle du fichier,
# index SCLS, stage visée, salle visée, point d'apparition visé)
# ---------------------------------------------------------------------------------------------------------------
ENTREES = (
    ("donjon", ("Dragon Roost Pond Past Statues", "DRC First Room", "Adanmae", 0, 2, "M_NewD2", 0, 0),
     ("DRC First Room", "Dragon Roost Pond Past Statues", "M_NewD2", 0, 0, "Adanmae", 0, 2)),
    ("donjon", ("FW Entrance Platform", "FW First Room", "sea", 41, 6, "kindan", 0, 0),
     ("FW First Room", "FW Entrance Platform", "kindan", 0, 0, "sea", 41, 6)),
    ("donjon", ("Tower of the Gods Sector", "TOTG Entrance Room", "sea", 26, 0, "Siren", 0, 0),
     ("TOTG Entrance Room", "Tower of the Gods Sector", "Siren", 0, 1, "sea", 26, 2)),
    ("donjon", ("Headstone Island Interior", "ET First Room", "Edaichi", 0, 0, "M_Dai", 0, 0),
     ("ET First Room", "Headstone Island Interior", "M_Dai", 0, 0, "Edaichi", 0, 1)),
    ("donjon", ("Gale Isle Interior", "WT First Room", "Ekaze", 0, 0, "kaze", 15, 15),
     ("WT First Room", "Gale Isle Interior", "kaze", 15, 0, "Ekaze", 0, 1)),
    ("grotte", ("Outset Near Savage Headstone", "Outset Savage Labyrinth", "sea", 44, 8, "Cave09", 0, 0),
     ("Outset Savage Labyrinth", "Outset Near Savage Headstone", "Cave09", 0, 1, "sea", 44, 10)),
    ("grotte", ("Dragon Roost Island", "Dragon Roost Island Cave", "sea", 13, 2, "TF_06", 0, 0),
     ("Dragon Roost Island Cave", "Dragon Roost Island", "TF_06", 0, 0, "sea", 13, 5)),
    ("grotte", ("Fire Mountain", "Fire Mountain Interior", "sea", 20, 0, "MiniKaz", 0, 0),
     ("Fire Mountain Interior", "Fire Mountain", "MiniKaz", 0, 0, "sea", 20, 0)),
    ("grotte", ("Ice Ring Isle", "Ice Ring Interior", "sea", 40, 0, "MiniHyo", 0, 0),
     ("Ice Ring Interior", "Ice Ring Isle", "MiniHyo", 0, 0, "sea", 40, 0)),
    ("grotte", ("Ice Ring Interior", "Ice Ring Inner Cave", "MiniHyo", 0, 1, "ITest62", 0, 0),
     ("Ice Ring Inner Cave", "Ice Ring Isle", "ITest62", 0, 0, "sea", 40, 0)),
    ("grotte", ("The Cabana", "Cabana Labyrinth", "Abesso", 0, 1, "TF_04", 0, 0),
     ("Cabana Labyrinth", "The Cabana", "TF_04", 0, 0, "Abesso", 0, 1)),
    ("grotte", ("Needle Rock Isle", "Needle Rock Cave", "sea", 29, 0, "SubD42", 0, 0),
     ("Needle Rock Cave", "Needle Rock Isle", "SubD42", 0, 0, "sea", 29, 5)),
    ("grotte", ("Angular Isles Small Isle", "Angular Isles Cave", "sea", 47, 1, "SubD43", 0, 0),
     ("Angular Isles Cave", "Angular Isles Small Isle", "SubD43", 0, 0, "sea", 47, 5)),
    ("grotte", ("Boating Course Small Isle", "Boating Course Cave", "sea", 48, 0, "SubD71", 0, 0),
     ("Boating Course Cave", "Boating Course Small Isle", "SubD71", 0, 0, "sea", 48, 5)),
    ("grotte", ("Stone Watcher Island", "Stone Watcher Cave", "sea", 31, 0, "TF_01", 0, 0),
     ("Stone Watcher Cave", "Stone Watcher Island", "TF_01", 0, 0, "sea", 31, 1)),
    ("grotte", ("Overlook Island Upper Isles", "Overlook Cave", "sea", 7, 0, "TF_02", 0, 0),
     ("Overlook Cave", "Overlook Island Upper Isles", "TF_02", 0, 0, "sea", 7, 1)),
    ("grotte", ("Birds Peak Rock Behind Bars", "Birds Peak Rock Cave", "sea", 35, 0, "TF_03", 0, 0),
     ("Birds Peak Rock Cave", "Birds Peak Rock Behind Bars", "TF_03", 0, 0, "sea", 35, 1)),
    ("grotte", ("Pawprint Isle", "Pawprint Chu Chu Cave", "sea", 12, 0, "TyuTyu", 0, 0),
     ("Pawprint Chu Chu Cave", "Pawprint Isle", "TyuTyu", 0, 0, "sea", 12, 1)),
    ("grotte", ("Pawprint Wizzrobe Cave Isle", "Pawprint Wizzrobe Cave", "sea", 12, 1, "Cave07", 0, 0),
     ("Pawprint Wizzrobe Cave", "Pawprint Wizzrobe Cave Isle", "Cave07", 0, 0, "sea", 12, 5)),
    ("grotte", ("Diamond Steppe Upper Island", "Diamond Steppe Warp Maze", "sea", 36, 0, "WarpD", 0, 0),
     ("Diamond Steppe Warp Maze", "Diamond Steppe Upper Island", "WarpD", 0, 0, "sea", 36, 1)),
    ("grotte", ("Bomb Island", "Bomb Island Cave", "sea", 34, 0, "Cave01", 0, 0),
     ("Bomb Island Cave", "Bomb Island", "Cave01", 0, 0, "sea", 34, 1)),
    ("grotte", ("Rock Spire Upper Ledges", "Rock Spire Cave", "sea", 16, 0, "Cave04", 0, 0),
     ("Rock Spire Cave", "Rock Spire Upper Ledges", "Cave04", 0, 0, "sea", 16, 1)),
    ("grotte", ("Shark Island", "Shark Island Cave", "sea", 38, 0, "ITest63", 0, 0),
     ("Shark Island Cave", "Shark Island", "ITest63", 0, 0, "sea", 38, 5)),
    ("grotte", ("Cliff Plateau Isles", "Cliff Plateau Cave", "sea", 42, 0, "Cave03", 0, 0),
     ("Cliff Plateau Cave", "Cliff Plateau Isles", "Cave03", 0, 0, "sea", 42, 2)),
    ("grotte", ("Cliff Plateau Highest Isle", "Cliff Plateau Cave Past Wooden Barrier", "sea", 42, 1, "Cave03", 0, 1),
     ("Cliff Plateau Cave Past Wooden Barrier", "Cliff Plateau Highest Isle", "Cave03", 0, 1, "sea", 42, 1)),
    ("grotte", ("Horseshoe Island Past Tentacles", "Horseshoe Cave", "sea", 43, 0, "Cave05", 0, 0),
     ("Horseshoe Cave", "Horseshoe Island Past Tentacles", "Cave05", 0, 0, "sea", 43, 5)),
    ("grotte", ("Star Island", "Star Island Cave", "sea", 2, 0, "Cave02", 0, 0),
     ("Star Island Cave", "Star Island", "Cave02", 0, 0, "sea", 2, 1)),
)
def _cible(t) -> tuple:
    """(stage, salle, point) visés par un aller ou un retour de ENTREES (valeurs de la table de TWWHD)."""
    return (t[5], t[6], t[7])


DONJONS = tuple(k for k, e in enumerate(ENTREES) if e[0] == "donjon")
GROTTES = tuple(k for k, e in enumerate(ENTREES) if e[0] == "grotte")
POOLS = {"aucune": (), "donjons": DONJONS, "donjons_grottes": DONJONS + GROTTES}
IR_INTERIEUR = 8            # « Ice Ring Isle -> Ice Ring Interior »
IR_GROTTE = 9               # « Ice Ring Interior -> Ice Ring Inner Cave » (porte À L'INTÉRIEUR d'une grotte)
# sorties lues dans le dump USA v0 qui diffèrent de la table de TWWHD (le générateur relit ces valeurs-ci avant
# d'écrire ; il écrit les valeurs de la table, comme TWWHD) : (stage, salle, index SCLS) -> (stage, point, salle)
SCLS_RELUES = {("Edaichi", 0, 0): ("M_Dai", 0, 255), ("MiniKaz", 0, 0): ("sea", 1, 20),
               ("MiniHyo", 0, 0): ("sea", 1, 40), ("ITest62", 0, 0): ("sea", 1, 40)}
# vent de retour après chaque boss de donjon (WARP_WIND_AFTER, acteur DIRECTOR, action 2 de la stage du boss) :
# donjon (index dans ENTREES) -> (stage du boss, (stage, salle, point) d'origine), valeurs relues dans le dump
VENTS = {0: ("M_DragB", ("sea", 13, 211)), 1: ("kinBOSS", ("Omori", 0, 215)), 2: ("SirenB", ("sea", 26, 1)),
         3: ("M_DaiB", ("sea", 45, 229)), 4: ("kazeB", ("sea", 4, 232))}
STAGES_MORT = ("MiniKaz", "MiniHyo")          # stages dont la zone de mort (VolTag) suit l'île d'entrée
NOMS_SAUVETAGE = frozenset({"Salvage", "SwSlvg", "Salvag2", "SalvagN", "SalvagE", "SalvFM"})
CHEMIN_CARTES = "content/Common/Misc/Misc.szs@YAZ0@SARC@Misc.bfres@BFRES@cmapdat.bin@CHARTS"
MASQUE_CARTE, DECALAGE_CARTE = 0x0FF00000, 20

# sans épée : lieux d'histoire du monde ouvert retirés, objet du coffre de la chambre de l'épée, bits posés à la
# création (BARRIER_DOWN et le second bit que la barrière pose), objets de départ retirés (épée, Excalibur)
LIEUX_EPEE = ("Start - Hero's Sword", "Start - Master Sword", "Story - Sword Chamber Full Power")
COFFRE_EPEE = "Hyrule Castle - Sword Chamber Chest"
OBJET_EPEE_SANS, NOM_OBJET_EPEE_SANS = 0x04, "Red Rupee"
BITS_SANS_EPEE = (0x2C02, 0x3B08)
OBJETS_EPEE = (0x38, 0x39)


# ---------------------------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------------------------
def mode_entrees(options) -> str:
    if not isinstance(options, dict):
        return ENTREES_DEFAUT
    m = options.get("entrees", ENTREES_DEFAUT)
    return m if m in MODES_ENTREES else ENTREES_DEFAUT


def sans_epee(options) -> bool:
    return isinstance(options, dict) and options.get("sans_epee") is True


def cartes(options) -> bool:
    return isinstance(options, dict) and options.get("cartes_melangees") is True


def actif(options) -> bool:
    """Une option du lot 8 est-elle allumée ?"""
    return sans_epee(options) or cartes(options) or mode_entrees(options) != ENTREES_DEFAUT


def options_fichiers(options) -> dict:
    """Options du lot 8 allumées (forme courte, pour le manifeste du pack)."""
    out = {}
    if sans_epee(options):
        out["sans_epee"] = True
    if mode_entrees(options) != ENTREES_DEFAUT:
        out["entrees"] = mode_entrees(options)
    if cartes(options):
        out["cartes_melangees"] = True
    return out


def a_tirer(options) -> bool:
    """Un tirage du lot 8 manque-t-il (option allumée, tirage absent) ?"""
    return (mode_entrees(options) != ENTREES_DEFAUT and options.get("tirage_entrees") is None) \
        or (cartes(options) and options.get("tirage_cartes") is None)


def _entier(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def controler_tirage_entrees(mode: str, tirage) -> None:
    """Le tirage d'entrées ``tirage`` (liste : destination derrière chaque porte du lot, dans l'ordre de
    ``POOLS[mode]``) est-il permis ? Permutation, catégorie gardée, pas la grotte intérieure de l'Île de Glace vers
    l'Île de Glace elle-même (elle ne serait plus atteignable que de l'intérieur). Lève ErreurLot8."""
    pool = POOLS[mode]
    if not isinstance(tirage, (list, tuple)) or len(tirage) != len(pool) or not all(_entier(x) for x in tirage):
        raise ErreurLot8(f"tirage des entrées : liste de {len(pool)} entiers attendue")
    if sorted(tirage) != sorted(pool):
        raise ErreurLot8("tirage des entrées : pas une permutation des entrées mélangées")
    for porte, dest in zip(pool, tirage):
        if ENTREES[porte][0] != ENTREES[dest][0]:
            raise ErreurLot8("tirage des entrées : une grotte et un donjon échangés (catégories séparées)")
        if porte == IR_GROTTE and dest == IR_INTERIEUR:
            raise ErreurLot8("tirage des entrées : la grotte intérieure de l'Île de Glace ne peut pas mener à "
                             "l'Île de Glace elle-même")


def controler_tirage_cartes(tirage) -> None:
    if not isinstance(tirage, (list, tuple)) or len(tirage) != 49 or not all(_entier(x) for x in tirage) \
            or sorted(tirage) != list(range(1, 50)):
        raise ErreurLot8("tirage des cartes : permutation des îles 1 à 49 attendue")


def controler_options(o: dict) -> None:
    """Contrôle des options du lot 8 DÉJÀ normalisées (types, dépendances, forme des tirages). Lève ErreurLot8."""
    for k in OPTIONS_BOOL:
        if not isinstance(o.get(k), bool):
            raise ErreurLot8(f"option {k} : booléen attendu")
    if o.get("entrees") not in MODES_ENTREES:
        raise ErreurLot8("option entrees : « aucune », « donjons » ou « donjons_grottes »")
    if o["sans_epee"] and o.get("monde") != "ouvert":
        raise ErreurLot8("option sans_epee : exige le monde ouvert (monde = « ouvert »)")
    if o["entrees"] != ENTREES_DEFAUT and o.get("monde") != "ouvert":
        raise ErreurLot8("option entrees : exige le monde ouvert (monde = « ouvert »)")
    if o["cartes_melangees"] and o.get("lieux_tresors") is not True:
        raise ErreurLot8("option cartes_melangees : exige les trésors engloutis mélangés (lieux_tresors)")
    te, tc = o.get("tirage_entrees"), o.get("tirage_cartes")
    if te is not None:
        if o["entrees"] == ENTREES_DEFAUT:
            raise ErreurLot8("tirage des entrées sans l'option entrees")
        controler_tirage_entrees(o["entrees"], te)
    if tc is not None:
        if not o["cartes_melangees"]:
            raise ErreurLot8("tirage des cartes sans l'option cartes_melangees")
        controler_tirage_cartes(tc)


def _flux(texte: str):
    from rando.noyau import Flux
    return Flux(hashlib.sha256(texte.encode("utf-8")).digest())


def _melanger(liste: list, flux) -> None:
    for i in range(len(liste) - 1, 0, -1):
        j = flux.sous(i + 1)
        liste[i], liste[j] = liste[j], liste[i]


def tirer_entrees(mode: str, graine: str, essai: int) -> list:
    """Un tirage d'entrées pour ``mode`` : condensé de la graine et du numéro d'essai (pas le flux du remplissage),
    chaque catégorie mélangée à part. Le moteur valide ce tirage par la logique (``rando_moteur``)."""
    f = _flux(f"WWMP-entrees|{mode}|{graine}|{essai}")
    out = []
    for cat in (DONJONS, GROTTES if mode == "donjons_grottes" else ()):
        while True:
            dest = list(cat)
            _melanger(dest, f)
            if not (IR_GROTTE in cat and dest[cat.index(IR_GROTTE)] == IR_INTERIEUR):
                break
        out += dest
    return out


def tirer_cartes(graine: str) -> list:
    """Tirage des cartes : condensé de la graine (île 1..49 -> île d'origine de la carte qui y mène)."""
    out = list(range(1, 50))
    _melanger(out, _flux(f"WWMP-cartes|{graine}"))
    return out


# ---------------------------------------------------------------------------------------------------------------
# Entrées : permutation, cibles des retours
# ---------------------------------------------------------------------------------------------------------------
def permutation(options) -> dict:
    """{porte: destination} (index dans ENTREES) des entrées mélangées par la table ({} : aucune)."""
    mode = mode_entrees(options)
    t = options.get("tirage_entrees") if isinstance(options, dict) else None
    if mode == ENTREES_DEFAUT or t is None:
        return {}
    controler_tirage_entrees(mode, t)
    return dict(zip(POOLS[mode], t))


def cibles_retours(perm: dict) -> dict:
    """{destination: porte dont le retour donne la sortie} : la sortie d'une destination ramène à l'île de la porte
    qui y mène (entrées couplées) ; la grotte intérieure de l'Île de Glace ramène à la porte qui mène à l'Île de
    Glace (le bateau y est : WriteEntrances.cpp de TWWHD)."""
    inv = {d: p for p, d in perm.items()}
    out = {}
    for d, p in inv.items():
        if p == IR_GROTTE:
            p = inv.get(IR_INTERIEUR, IR_INTERIEUR)
        out[d] = p
    return out


def changements(perm: dict) -> tuple[list, list]:
    """([(porte, destination)] des allers réécrits, [(destination, porte du retour)] des retours réécrits), triés.
    Une porte tirée sur elle-même n'est pas réécrite ; un retour qui ramène où il ramenait non plus."""
    allers = sorted((p, d) for p, d in perm.items() if p != d)
    retours = sorted((d, p) for d, p in cibles_retours(perm).items()
                     if (ENTREES[p][2][1],) + _cible(ENTREES[p][2]) != (ENTREES[d][2][1],) + _cible(ENTREES[d][2]))
    return allers, retours


# ---------------------------------------------------------------------------------------------------------------
# Logique
# ---------------------------------------------------------------------------------------------------------------
def cle_logique(options) -> tuple:
    """Ce qui, dans ces options, change la logique : () quand rien (logique d'avant le lot). Une option allumée SANS
    son tirage (avant le tirage) ne change rien : c'est le monde d'origine que le tirage réécrit."""
    if not isinstance(options, dict):
        return ()
    se = sans_epee(options)
    te = options.get("tirage_entrees") if mode_entrees(options) != ENTREES_DEFAUT else None
    tc = options.get("tirage_cartes") if cartes(options) else None
    allers, retours = changements(permutation(options)) if te is not None else ([], [])
    te_ = tuple(te) if (allers or retours) else ()
    tc_ = tuple(tc) if tc is not None and list(tc) != list(range(1, 50)) else ()
    if not (se or te_ or tc_):
        return ()
    return (se, mode_entrees(options) if te_ else "", te_, tc_)


def _joindre(sorties: dict, vers: str, ex: str) -> None:
    sorties[vers] = f"({sorties[vers]}) or ({ex})" if vers in sorties else ex


def recabler(zones: list, perm: dict) -> list:
    """Le graphe des zones (``monde.json``) avec ces entrées mélangées : la porte garde son exigence et mène à la
    destination tirée ; la sortie de la destination garde la sienne et ramène à l'île de la porte. Lève ErreurLot8 si
    une sortie de la table manque au graphe."""
    allers, retours = changements(perm)
    zs = [dict(z, sorties=dict(z["sorties"])) for z in zones]
    par = {z["nom"]: z for z in zs}
    ajouts = []
    for p, d in allers:
        a = ENTREES[p][1]
        z = par.get(a[0])
        if z is None or a[1] not in z["sorties"]:
            raise ErreurLot8(f"logique : sortie {a[0]!r} -> {a[1]!r} absente du monde")
        ajouts.append((a[0], ENTREES[d][1][1], z["sorties"][a[1]]))
    for d, p in retours:
        r = ENTREES[d][2]
        z = par.get(r[0])
        if z is None or r[1] not in z["sorties"]:
            raise ErreurLot8(f"logique : sortie {r[0]!r} -> {r[1]!r} absente du monde")
        ajouts.append((r[0], ENTREES[p][2][1], z["sorties"][r[1]]))
    for p, _d in allers:
        del par[ENTREES[p][1][0]]["sorties"][ENTREES[p][1][1]]
    for d, _p in retours:
        del par[ENTREES[d][2][0]]["sorties"][ENTREES[d][2][1]]
    for de, vers, ex in ajouts:
        if vers not in par:
            raise ErreurLot8(f"logique : zone inconnue {vers!r}")
        _joindre(par[de]["sorties"], vers, ex)
    return zs


def _atteintes(zones: list) -> set:
    """Zones atteintes depuis « Root » en passant toute sortie qui n'est pas « Impossible » (graphe seul)."""
    adj = {z["nom"]: [d for d, ex in z["sorties"].items() if ex.strip() != "Impossible"] for z in zones}
    vus, pile = {"Root"}, ["Root"]
    while pile:
        for d in adj.get(pile.pop(), ()):
            if d not in vus:
                vus.add(d)
                pile.append(d)
    return vus


_STRUCTURE = {}


def structure_ok(zones: list, perm: dict) -> bool:
    """Tri rapide d'un tirage d'entrées, avant la logique : le graphe recâblé (exigences ignorées) atteint toutes les
    zones que le graphe d'origine atteint. Écarte les boucles fermées (le plus haut îlot du Plateau, que seule sa grotte
    relie, derrière une porte qui n'est atteignable que de lui-même) sans compiler de logique."""
    cle = id(zones)
    if cle not in _STRUCTURE:
        _STRUCTURE.clear()
        _STRUCTURE[cle] = (zones, _atteintes(zones))
    return _atteintes(recabler(zones, perm)) >= _STRUCTURE[cle][1]


def docs_logique(docs: dict, cle: tuple) -> dict:
    """Données de logique de la partie : ``docs`` (non modifiés ; déjà passés par le lot 7) avec la clé ``cle`` de
    ``cle_logique``."""
    if not cle:
        return docs
    se, mode, te, tc = cle
    out = dict(docs)
    if se:
        sc = dict(docs["scenario_vanilla"])
        noms = [h["nom"] for h in sc.get("lieux_histoire", ())]
        if any(n not in noms for n in LIEUX_EPEE):
            raise ErreurLot8("logique : sans épée exige le scénario du monde ouvert (épées de départ introuvables)")
        sc["lieux_histoire"] = [h for h in sc["lieux_histoire"] if h["nom"] not in LIEUX_EPEE]
        sc["reglages"] = dict(sc["reglages"], Remove_Swords="true")
        sc["objets_corriges"] = dict(sc.get("objets_corriges", {}), **{COFFRE_EPEE: NOM_OBJET_EPEE_SANS})
        sc["objets_corriges_raisons"] = dict(sc.get("objets_corriges_raisons", {}), **{
            COFFRE_EPEE: "lot 8, sans épée : le coffre de la chambre de l'épée donne un rubis rouge"})
        out["scenario_vanilla"] = sc
    if te:
        perm = dict(zip(POOLS[mode], te))
        controler_tirage_entrees(mode, list(te))
        out["monde"] = dict(docs["monde"], zones=recabler(docs["monde"]["zones"], perm))
    if tc:
        controler_tirage_cartes(list(tc))
        m = dict(docs["macros"]["macros"])
        sc = out.get("scenario_vanilla", docs["scenario_vanilla"])
        if any(k.startswith("Chart For Island") for k in sc.get("macros", {})):
            raise ErreurLot8("logique : le scénario redéfinit une macro de carte")
        origine = {i: m[f"Chart For Island {i}"] for i in range(1, 50)}
        for i in range(1, 50):
            m[f"Chart For Island {i}"] = origine[tc[i - 1]]
        out["macros"] = dict(docs["macros"], macros=m)
    return out


def releve_logique(options) -> str:
    """Empreinte des RÈGLES du lot 8 qui changent la logique de cette partie ("" si rien) : table des entrées,
    lieux retirés sans épée (le tirage lui-même est dans les options de la table)."""
    cle = cle_logique(options)
    if not cle:
        return ""
    doc = {"sans_epee": [list(LIEUX_EPEE), COFFRE_EPEE, NOM_OBJET_EPEE_SANS] if cle[0] else None,
           "entrees": [[e[0], list(e[1][:2]), list(e[2][:2])] for e in ENTREES] if cle[2] else None,
           "ice": [IR_INTERIEUR, IR_GROTTE] if cle[2] else None,
           "cartes": "Chart For Island" if cle[3] else None}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------------------------
# Éditions de fichiers
# ---------------------------------------------------------------------------------------------------------------
def _attendu(t) -> tuple:
    """(stage, point, salle) que la sortie SCLS de l'aller ou du retour ``t`` porte dans le dump USA v0."""
    return SCLS_RELUES.get((t[2], t[3], t[4]), (t[5], t[7], t[6]))


def editions(options) -> list[tuple]:
    """Éditions de fichiers du lot 8 pour ces options : [(chemin, opération, arguments)], ordre fixe. Elles viennent
    APRÈS celles des lots 6 et 7 (le coffre de l'épée est ajouté par le lot 7)."""
    out = []
    if not actif(options):
        return out
    if sans_epee(options):
        if "epee" not in L7.coffres_actifs(options):
            raise ErreurLot8("sans épée : le coffre de la chambre de l'épée (lot 7) manque")
        out.append((L7.COFFRES["epee"][0], "epee_sans", ()))
    perm = permutation(options)
    allers, retours = changements(perm)
    for p, d in allers:
        a = ENTREES[p][1]
        out.append((L7.chemin_salle(a[2], a[3]), "sortie", (a[4], _attendu(a), _cible(ENTREES[d][1]))))
    for d, p in retours:
        r = ENTREES[d][2]
        out.append((L7.chemin_salle(r[2], r[3]), "sortie", (r[4], _attendu(r), _cible(ENTREES[p][2]))))
    # zones de mort : la destination est la Montagne de Feu ou l'Île de Glace, atteinte par une autre porte
    for p, d in allers:
        st = ENTREES[d][1][5]
        if st in STAGES_MORT:
            porte = ENTREES[p][1][5]
            genre = {("MiniKaz", "MiniHyo"): 1, ("MiniHyo", "MiniKaz"): 2}.get((porte, st))
            out.append((L7.chemin_salle(st, 0), "zone_mort", (genre,)))
    # vent de retour : le boss d'un donjon renvoie à la sortie du vent du donjon dont la porte mène à ce donjon
    inv = {d: p for p, d in perm.items()}
    for d in sorted(VENTS):
        p = inv.get(d, d)
        if p != d:
            out.append((L7.chemin_stage(VENTS[d][0], "event_list.dat@EVENTS"), "vent", (VENTS[d][1], VENTS[p][1])))
    if cartes(options) and options.get("tirage_cartes") is not None:
        tc = list(options["tirage_cartes"])
        controler_tirage_cartes(tc)
        if tc != list(range(1, 50)):
            out.append((CHEMIN_CARTES, "cartes", (tuple(tc),)))
            for ile in range(1, 50):
                if tc[ile - 1] != ile:
                    out.append((L7.chemin_salle("sea", ile), "carte_ile", (ile, tc[ile - 1])))
    return out


def appliquer(session, chemin: str, op: str, args: tuple) -> None:
    """Applique une édition de ``editions`` dans la session du générateur."""
    f = _OPS.get(op)
    if f is None:
        raise ErreurLot8(f"opération inconnue : {op}")
    f(session, chemin, *args)


def _epee_sans(s, ch):
    attendu = L7._tres(L7.TRES_EPEE, L7.COFFRES["epee"][1])
    lst = [e for e in s.ouvrir(ch).entrees("TRES") if bytes(e.data) == attendu]
    if len(lst) != 1:
        raise ErreurLot8("sans épée : coffre de la chambre de l'épée introuvable (lot 7 non appliqué ?)")
    lst[0].data[0x1C] = OBJET_EPEE_SANS


def _sortie(s, ch, index, avant, apres):
    """Sortie SCLS ``index`` : relit (stage, point, salle) ``avant`` et écrit la cible ``apres`` (stage, salle,
    point), comme WriteEntrances.cpp de TWWHD (nom sur 8 octets, point à +8, salle à +9 ; fondu gardé)."""
    dz = s.ouvrir(ch)
    scls = dz.entrees("SCLS")
    if not 0 <= index < len(scls):
        raise ErreurLot8(f"sortie {index} absente de {ch}")
    e = scls[index]
    lu = (bytes(e.data[:8]).split(b"\0", 1)[0].decode("ascii", "replace"), e.data[8], e.data[9])
    if lu != tuple(avant):
        raise ErreurLot8(f"sortie {index} de {ch} : {lu} au lieu de {tuple(avant)}")
    nom = apres[0].encode("ascii")
    if len(nom) > 7:
        raise ErreurLot8(f"nom de stage trop long : {apres[0]!r}")
    e.data[0:8] = nom + b"\0" * (8 - len(nom))
    e.data[8] = apres[2]
    e.data[9] = apres[1]


def _zone_mort(s, ch, genre):
    dz = s.ouvrir(ch)
    lst = [e for e in dz.entrees("ACTR", None) if e.nom == "VolTag"]
    if len(lst) != 1:
        raise ErreurLot8(f"zone de mort : {len(lst)} VolTag dans {ch} au lieu d'un")
    e = lst[0]
    if genre is None:
        dz.retirer(e)
    else:
        e.data[11] = (e.data[11] & 0x3F) | (genre << 6)


def _vent(s, ch, avant, apres):
    from jeu.fichiers import events
    ev = s.ouvrir(ch)
    acts = ev.actions_de(ev.acteur("WARP_WIND_AFTER", "DIRECTOR"))
    if len(acts) < 3:
        raise ErreurLot8(f"vent de retour : action 2 absente dans {ch}")
    props = {p.nom: p for p in ev.proprietes_de(acts[2])}
    if not all(n in props for n in ("StartCode", "Stage", "RoomNo")) or props["Stage"].type != events.TYPE_CHAINE:
        raise ErreurLot8(f"vent de retour : propriétés inattendues dans {ch}")
    ev.poser_entier(props["StartCode"], avant[2], apres[2])
    ev.poser_chaine(props["Stage"], avant[0], apres[0])
    ev.poser_entier(props["RoomNo"], avant[1], apres[1])


def _origine_cartes(s) -> dict:
    from jeu.fichiers import charts
    return charts.Cartes(s.brut(CHEMIN_CARTES.rsplit("@", 1)[0])).par_ile()


def _cartes(s, ch, tirage):
    c = s.ouvrir(ch)
    orig = _origine_cartes(s)
    par = c.par_ile()
    for ile in range(1, 50):
        j = tirage[ile - 1]
        if j != ile:
            # la carte de l'île j (son objet, son index possédé) montre désormais l'île ``ile``
            par[j].poser_position(orig[ile].position())


def _carte_ile(s, ch, ile, j):
    orig = _origine_cartes(s)
    attendu, nouveau = orig[ile].possedee, orig[j].possedee
    n = 0
    for e in s.ouvrir(ch).entrees("SCOB", None):
        if e.nom in NOMS_SAUVETAGE and (e.data[8] & 0xF0) >> 4 == 0:
            p = struct.unpack_from(">I", e.data, 8)[0]
            if (p & MASQUE_CARTE) >> DECALAGE_CARTE != attendu:
                raise ErreurLot8(f"carte de l'île {ile} : trésor qui attend la carte {(p & MASQUE_CARTE) >> 20} au "
                                 f"lieu de {attendu}")
            struct.pack_into(">I", e.data, 8, (p & ~MASQUE_CARTE) | (nouveau << DECALAGE_CARTE))
            n += 1
    if not n:
        raise ErreurLot8(f"carte de l'île {ile} : aucun trésor englouti à carte")


_OPS = {"epee_sans": _epee_sans, "sortie": _sortie, "zone_mort": _zone_mort, "vent": _vent, "cartes": _cartes,
        "carte_ile": _carte_ile}
OPERATIONS = frozenset(_OPS)


# ---------------------------------------------------------------------------------------------------------------
# Code du pack généré
# ---------------------------------------------------------------------------------------------------------------
GROUPES_TWWHD = {
    # swordless.asm (sans 0x0213AE90, écrit à part) : Spectre-Ganon sans Excalibur, épées prêtées
    "sans_epee": (0x0213AEA8, 0x025B2030, 0x025B26F4, 0x025B26F8),
    # entrance_rando_features.asm (portes à clé à l'envers, porte de la Cabane, sauvegarde : dernier secteur de mer
    # visité) et flexible_entrances.asm (point d'apparition du bateau sans le Lion rouge, carte du GamePad rechargée)
    "entrees": (0x021271A0, 0x02129324, 0x021A4548, 0x025220C4, 0x024136A8, 0x02657F04),
}
# swordless.asm, 0x0213AE90 : « rlwinm. r8,r10,0,15,15 » (dégât de la Masse au lieu de l'épée), hors des mnémoniques
# acceptées : écrit en .int (adresse, mot d'origine, mot de TWWHD)
SITE_SPECTRE = (0x0213AE90, 0x554807BD, 0x554803DF)
# écartés de flexible_entrances.asm : sortie des enchères (portes) et crawl forcé (paramètre posé par
# « fix_entrance_params » sur des portes et passages) : les portes ne sont pas mélangées
SITES_ECARTES = (0x0205CB8C, 0x02411DF4)


def fragments_actifs(options) -> tuple:
    out = []
    if sans_epee(options):
        out.append("sans_epee")
    if mode_entrees(options) != ENTREES_DEFAUT:
        out.append("entrees")
    return tuple(out)


def lire_fragment(nom: str) -> dict:
    from rando import lot6 as rando_lot6
    old = rando_lot6.DOSSIER_ASM
    try:
        rando_lot6.DOSSIER_ASM = DOSSIER_ASM
        return rando_lot6.lire_fragment(nom)
    finally:
        rando_lot6.DOSSIER_ASM = old


def code(options, rpx) -> dict:
    """Code du pack généré pour ces options : {symboles, cave, sites} (vides si le lot 8 n'en demande pas)."""
    sym, cave, sites = {}, [], []
    noms = fragments_actifs(options)
    if not noms:
        return {"symboles": sym, "cave": cave, "sites": sites}
    cave.append("; --- lot 8 : code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT, "
                "Copyright (c) 2024 SuperDude88 (LICENSE_TWWHD.txt) ---")
    for nom in noms:
        fr = lire_fragment(nom)
        for a, n in fr["symboles"].items():
            if sym.get(a, n) != n or (n in sym.values() and sym.get(a) != n):
                raise ErreurLot8(f"symbole {n} en double ou adresse 0x{a:08X} nommée deux fois")
            sym[a] = n
        cave.append(f"; --- lot 8, fragment {nom} (TWWHD, MIT) ---")
        cave += fr["cave"]
        sites.append(f"; --- lot 8, fragment {nom} (TWWHD, MIT) ---")
        for l in fr["sites"]:
            m = re.match(r"(0x[0-9A-Fa-f]{8}) = .*twwhd avant=([0-9A-F]{8})", l)
            if not m or rpx.mot(int(m.group(1), 16)) != int(m.group(2), 16):
                raise ErreurLot8(f"fragment {nom} : mot d'origine inattendu ({l.strip()[:40]})")
            sites.append(l)
        if nom == "sans_epee":
            adr, avant, apres = SITE_SPECTRE
            if rpx.mot(adr) != avant:
                raise ErreurLot8(f"sans épée : mot d'origine inattendu à 0x{adr:08X}")
            sites.append(f"0x{adr:08X} = .int 0x{apres:08X}   ; avant={avant:08X} rlwinm. r8,r10,0,15,15 (TWWHD)")
    adrs = [int(l.split(" ", 1)[0], 16) for l in sites if l.startswith("0x")]
    if len(adrs) != len(set(adrs)):
        raise ErreurLot8("deux lignes du lot 8 écrivent la même adresse")
    return {"symboles": sym, "cave": cave, "sites": sites}


ENTETE_FRAGMENT = (
    "; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,\n"
    "; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.\n"
    "; Converti par tools/wwmp/rando_lot8.py (--porter) avec tools/wwmp/rando_port_twwhd.py.\n"
    "; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.\n")


def porter(source: str, rpx, packs: dict) -> dict:
    """{nom: texte} des fragments du lot 8 depuis la copie de TWWHD ``source`` (outil de développement)."""
    from rando import port_twwhd as p
    src = p.Source.depuis_dossier(source)
    out = {}
    for nom, sites in GROUPES_TWWHD.items():
        prefixe = f"wwr8_{nom}_"
        controle = p.convertir(src, sites, rpx, packs, prefixe=prefixe)
        if not controle.propre:
            raise ErreurLot8(f"fragment {nom} : " + "; ".join(controle.refus + controle.collisions))
        c = p.convertir(src, sites, rpx, {}, prefixe=prefixe)
        if not c.propre or c.donnees:
            raise ErreurLot8(f"fragment {nom} : conversion différente sans les packs, ou donnée inattendue")
        t = [ENTETE_FRAGMENT, "; [symboles]"]
        t += [f"0x{a:08X} = {n}:" for a, n in sorted(c.symboles.items())]
        t += ["; [cave]"] + c.cave + ["; [sites]"]
        t += [f"0x{a:08X} = {txt:<28} ; twwhd avant={av:08X} apres={ap:08X}" for a, av, ap, txt in c.sites]
        out[nom] = ("\n".join(t) + "\n").replace(prefixe + "jeu_", "wwr_jeu_")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Spoiler
# ---------------------------------------------------------------------------------------------------------------
def spoiler(options) -> list[str]:
    """Lignes du spoiler pour le lot 8 (vide si rien)."""
    out = []
    if sans_epee(options):
        out += ["", "== Sans épée ==", "Aucune épée : barrière d'Hyrule levée, Spectre-Ganon blessé par la Masse, "
                "épée prêtée chez Orca et contre Ganondorf ; le coffre de la chambre de l'épée donne un rubis rouge."]
    perm = permutation(options) if isinstance(options, dict) else {}
    if perm:
        out += ["", f"== Entrées mélangées ({mode_entrees(options)}, couplées : on ressort par où l'on est entré) =="]
        for p in sorted(perm):
            d = perm[p]
            out.append(f"{ENTREES[p][1][0]} -> {ENTREES[d][1][1]}" + ("" if p != d else " (d'origine)"))
    if cartes(options) and options.get("tirage_cartes"):
        noms = {s: n for n, s in L7.ILES.items()}
        out += ["", "== Cartes au trésor mélangées (carte de l'île d'origine -> île où elle mène) =="]
        tc = options["tirage_cartes"]
        for ile in range(1, 50):
            if tc[ile - 1] != ile:
                out.append(f"carte de {noms.get(tc[ile - 1], tc[ile - 1])} -> {noms.get(ile, ile)}")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Outil de développement
# ---------------------------------------------------------------------------------------------------------------
def table_depuis_twwhd(chemin_yaml: str) -> tuple:
    """(outil et tests) ENTREES relue dans entrance_shuffle_table.yaml de TWWHD : donjons et grottes."""
    with open(chemin_yaml, encoding="utf-8") as f:
        t = f.read()
    out = []
    for b in re.split(r"(?m)^- Type: ", t)[1:]:
        typ = b.split()[0]
        if typ not in ("DUNGEON", "CAVE"):
            continue
        paire = []
        for k in ("Forward", "Return"):
            a = [x.strip() for x in re.search(rf"(?m)^\s*{k}:\s*(.*)$", b).group(1).split(",")]
            paire.append((a[0], a[1], a[2], int(a[3]), int(a[4]), a[5], int(a[6]), int(a[7])))
        out.append(("donjon" if typ == "DUNGEON" else "grotte", paire[0], paire[1]))
    return tuple(out)


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Lot 8 du randomizer par fichiers : outil de développement")
    ap.add_argument("--porter", metavar="TWWHD", help="reconvertit les fragments depuis une copie de TWWHD-Randomizer")
    ap.add_argument("--rpx", help="cking.rpx d'origine (lu seulement)")
    a = ap.parse_args(argv)
    if not a.porter or not a.rpx:
        ap.error("--porter et --rpx sont exigés")
    from rando import lot6 as rando_lot6
    with open(a.rpx, "rb") as f:
        rpx = rando_lot6.Rpx(f.read())
    racine = os.path.dirname(os.path.dirname(ICI))
    packs = {}
    for n, d, fn in (("mailbox", "WWMP_Mailbox", "patch_mailbox.asm"),
                     ("confort", "WWMP_Confort", "patch_confort.asm")):
        with open(os.path.join(racine, "graphicpack", d, fn), encoding="utf-8") as fh:
            packs[n] = fh.read()
    frags = porter(a.porter, rpx, packs)
    os.makedirs(DOSSIER_ASM, exist_ok=True)
    for nom, t in frags.items():
        with open(os.path.join(DOSSIER_ASM, nom + ".asm"), "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    print(f"{len(frags)} fragments écrits dans {DOSSIER_ASM}", file=sys.stderr)
    return 0


if __name__ == "__main__":             # outil de développement : cd tools && python -m rando.lot8 …
    raise SystemExit(main())
