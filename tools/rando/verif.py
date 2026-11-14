"""Vérificateur INDÉPENDANT du randomizer complet (étape 1).

Ce fichier ne partage AUCUN code de logique avec `rando_moteur.py` (il n'en importe rien) : il relit lui-même
les JSON de `rando_logique/`, a son propre analyseur d'exigences (descente récursive -> arbre -> fermetures,
là où le moteur fabrique du source Python), applique lui-même le scénario d'origine, recalcule lui-même quels
coffres sont mélangés avec les options, et parcourt le monde à sa façon (recalcul complet des zones à chaque
tour). Deux écritures de la même spécification : si l'une se trompe, l'autre a peu de chances de se tromper
pareil. `rando_moteur.calculer()` refuse de rendre une table que ce vérificateur n'accepte pas.

Il répond à une seule question, pour une table donnée : est-ce une permutation permise des coffres mélangés,
et la partie reste-t-elle finissable (et, si l'option le demande, tous les lieux atteignables) ?
"""
from __future__ import annotations

import hashlib
import json
import os

DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logique")
NOMS = ("monde", "macros", "lieux", "objets", "application", "scenario_vanilla", "objets_vanilla")
CATS = {"donjons": "Dungeon", "grottes_enigme": "Puzzle Secret Cave", "grottes_combat": "Combat Secret Cave",
        "divers": "Misc", "plateformes": "Platform", "radeaux": "Raft", "sous_marins": "Submarine",
        "recifs": "Eye Reef Chests", "enigmes_iles": "Island Puzzle", "coffres_tingle": "Tingle Chest",
        "quetes_courtes": "Short Side Quest", "labyrinthe": "Savage Labyrinth",
        "quetes_longues": "Long Side Quest", "mini_jeux": "Minigame", "dons": "Free Gift",
        "achats_chers": "Expensive Purchase"}
# lieux « donnée » (portage TWWHD, lot 1) : groupe -> option qui le mélange (liste recopiée, pas importée du moteur)
GROUPES = {"courrier": "lieux_courrier", "boutiques": "lieux_boutiques", "encheres": "lieux_encheres",
           "pnj": "lieux_pnj", "kane": "lieux_kane", "courrier2": "courrier_tout", "enchere100": "lieux_encheres_100"}
# randomizer par fichiers, étape 5 : lieux « donnée » ajoutés (ils ne sont PAS dans application.json). RECOPIÉS (pas
# importés) de rando.SITES_DONNEES, sites 15 à 25 ; les tests comparent. Par lieu : nom, groupe, site, objet
# d'origine, adresses, accompagnements, objets interdits.
DONNEES9 = (
    ("Windfall Island - Kane Decorate Shop Guru", "kane", 15, 0x05, (0x101C3873,), (), ()),
    ("Windfall Island - Kane Decorate Postman Statue", "kane", 16, 0x05, (0x101C3877,), (), ()),
    ("Windfall Island - Kane Decorate Six Flags", "kane", 17, 0x04, (0x101C387B,), (), ()),
    ("Windfall Island - Kane Decorate Six Idols", "kane", 18, 0x04, (0x101C387F,), (), ()),
    ("Mailbox - Letter Advertising Bombs", "courrier2", 19, 0xC3, (0x10031524,), (), ()),
    ("Mailbox - Letter Advertising Rock Spire Shop Ship", "courrier2", 20, 0x01, (0x10031548,), (), ()),
    ("Mailbox - Letter from Tingle", "courrier2", 21, 0xCB, (0x10031554,), (), ()),
    ("Mailbox - Beedle's Silver Membership Reward", "courrier2", 22, 0x9D, (0x10031578,), (), ()),
    ("Mailbox - Letter from Hoskit's Girlfriend", "courrier2", 23, 0x3F, (0x10031584,), (), ()),
    ("Mailbox - Beedle's Gold Membership Reward", "courrier2", 24, 0x9E, (0x1003159C,), (), ()),
    ("Windfall Island - Auction 100 Rupee", "enchere100", 25, 0x77, (0x1019018D,),
     ((0x10190192, 0x00, 0x6B), (0x10190193, 0x00, 0x01)), (0x07, 0x1F, 0xE4, 0xED)),
)
GROUPES9 = frozenset({"kane", "courrier2", "enchere100"})
VERROU9 = "'Helmaroc_King_Defeated'"
# objets de ces lieux hors de PERMIS2 : ils ne vont que dans un lieu « donnée » (type 2)
SEULS9 = frozenset({0x01, 0x3F, 0x77, 0x9D, 0x9E, 0xC3, 0xCB})
EPUISE9 = (0x02215570, 0x02215574, 0x02215578, 0x0221557C, 0x02215580, 0x02215584, 0x02215588, 0x0221558C,
           0x02215590, 0x022155AC, 0x022155B8, 0x022155C0)
DEPENDANCES9 = {"courrier_tout": "lieux_courrier", "boutique_epuise": "lieux_boutiques"}
# coffres « en conflit » (portage TWWHD, lot 2, option « coffres_conflit ») : dans l'ordre des sites (clé de table
# (3, site, 0)), nom du lieu, index de sauvegarde, numéro de coffre, objet d'origine. Liste RECOPIÉE (pas importée) de
# rando.SITES_CONFLIT ; les tests comparent les deux.
CONFLIT = (
    ("Forbidden Woods - Mothula Mini Boss Chest", 4, 10, 0x2D),
    ("Wind Temple - Between Dirt Patches Chest", 7, 0, 0x06),
    ("Wind Temple - Wizzrobe Mini Boss", 7, 5, 0x2F),
    ("Wind Temple - Cyclones Room Chest", 7, 11, 0x4C),
    ("Wind Temple - Hub Room Center Chest", 7, 13, 0x4D),
    ("Islet of Steel - Interior Chest", 12, 4, 0xFE),
    ("Cliff Plateau Isles - Cave Chest", 12, 7, 0x1F),
    ("Stone Watcher Island - Cave Chest", 12, 10, 0x67),
    ("Dragon Roost Island - Cave Chest", 13, 0, 0x05),
    ("Windfall Island - Pirate Ship Chest", 13, 5, 0x31),
)
# objets que seuls ces coffres portent : jamais chez un personnage, dans une lettre ou une boutique
OBJETS_CONFLIT = frozenset({0x2D, 0x2F, 0x31, 0x67, 0xFE})
# objets progressifs (portage TWWHD, lot 4, option « progressifs ») : familles et leurs paliers, dans l'ordre. Liste
# RECOPIÉE (pas importée) de rando.FAMILLES_PROG ; les tests comparent les deux. Un palier ne va que dans un coffre
# ordinaire (type 0), et le coffre des flèches de lumière (retenu sinon par « jamais déplacé ») devient mélangeable
# quand toute sa famille l'est.
FAMILLES_PROG = (("arc", (0x27, 0x36)),)
OBJETS_PROG = frozenset({0x27, 0x36})
# objets de donjon hors de leur donjon (portage TWWHD, lot 5). Valeurs RECOPIÉES (pas importées) de rando.py ; les
# tests comparent. Une entrée [4, index, coffre, objet réel, donjon de destination] dit qu'un coffre ordinaire porte
# un objet de donjon étranger ; son entrée de coffre porte alors l'objet d'attente (rubis jaune, 3).
TYPE_HORS, OBJET_HORS = 4, 3
MODES_HORS = ("tout_donjon", "partout")
MODES_MELANGE = ("donjon",) + MODES_HORS
# lieux du randomizer par fichiers (lot 6) : type 5, clé (site, 0), site = index dans « sites » de
# rando_logique/lieux_fichiers.json (relu ICI, pas importé du moteur). Règles RECOPIÉES de rando_lot6 (les tests
# comparent) : familles et leur option, verrou d'histoire hors des donjons (sauf les boss), objets permis.
TYPE_FICHIER = 5
FAMILLES6 = {"tresors": "lieux_tresors", "evenements": "lieux_evenements", "boss": "lieux_boss",
             "fees": "lieux_fees", "sol": "lieux_sol", "pnj_code": "lieux_pnj_code", "beedle": "lieux_beedle"}
VERROU6 = "'Helmaroc_King_Defeated'"
# Ankle (famille « pnj_code ») : le jeu regarde les coffres des statues OUVERTS, pas les statues possédées ; un
# événement par coffre (zone et exigence du coffre), tous exigés en plus pour Ankle. RECOPIÉ de rando_lot6.
ANKLE6 = "Tingle Island - Ankle All Statues Reward"
COFFRES_STATUES6 = ("Dragon Roost Cavern - Tingle Statue Chest", "Forbidden Woods - Tingle Statue Chest",
                    "Tower of the Gods - Tingle Statue Chest", "Earth Temple - Tingle Statue Chest",
                    "Wind Temple - Tingle Statue Chest")
EVTS_STATUES6 = ("WWMP_L6_Coffre_Statue_1", "WWMP_L6_Coffre_Statue_2", "WWMP_L6_Coffre_Statue_3",
                 "WWMP_L6_Coffre_Statue_4", "WWMP_L6_Coffre_Statue_5")
VERROU_ANKLE6 = " and ".join(f"'{n}'" for n in EVTS_STATUES6)
FEES6 = frozenset({0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB0})
SANS_OBJET6 = frozenset({64, 65, 92, 93, 96, 115, 132, 133, 136, 137})
# liste blanche des stubs N1 / N2 et des écritures de données (rando.PERMIS2), RECOPIÉE : les tests comparent
PERMIS2 = frozenset({0x03, 0x04, 0x05, 0x06, 0x07, 0x0F, 0x15, 0x4E, 0x4C, 0x4D, 0x1F, 0x45, 0x46, 0x47, 0x48, 0x23,
                     0x27, 0x28, 0x29, 0x33, 0x43, 0x50, 0x62, 0x64, 0x66, 0x68, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xC2,
                     0xC4, 0xDB, 0xFC} | set(range(0xCD, 0xD7))
                    | {0xD8, 0xD9, 0xDA, 0xDD, 0xDE, 0xDF, 0xE0, 0xE2, 0xE3, 0xE6, 0xE7, 0xE8, 0xE9, 0xEA, 0xEC}
                    | set(range(0xF2, 0xF8)) | {0xF9, 0xFB} | {0xD7, 0xE4, 0xE5, 0xEB, 0xED, 0xEF, 0xF0})


def _sites6(dossier: str | None = None) -> list:
    with open(os.path.join(dossier or DOSSIER, "lieux_fichiers.json"), encoding="utf-8") as f:
        return json.load(f)["sites"]


def _familles6(options) -> frozenset:
    return frozenset(f for f, o in FAMILLES6.items() if isinstance(options, dict) and options.get(o) is True)


def _accepte6(site: dict, objet: int, speciaux) -> bool:
    """Le lieu du lot 6 ``site`` peut-il donner ``objet`` ?"""
    if objet == site["id"]:
        return True
    if not 0 < objet < 0xFF or objet in speciaux or objet in SANS_OBJET6:
        return False
    if site["voie"] == "fee" or objet in FEES6:
        return site["voie"] == "fee" and objet in FEES6
    if site["voie"] == "boutique":
        return objet in PERMIS2 or objet in (0x2C, 0xE1)
    return True


class Refus(Exception):
    pass


# lot 7 (randomizer par fichiers, étape 3) : monde ouvert, île de départ, re-combats. Règles RECOPIÉES de rando_lot7
# (les tests comparent) ; la seconde couche de scénario est relue ICI dans rando_logique/scenario_ouvert.json.
ILES7 = ("Star Island", "Northern Fairy Island", "Gale Isle", "Crescent Moon Island", "Seven Star Isles",
         "Overlook Island", "Four Eye Reef", "Mother & Child Isles", "Spectacle Island", "Windfall Island",
         "Pawprint Isle", "Dragon Roost Island", "Flight Control Platform", "Western Fairy Island", "Rock Spire Isle",
         "Tingle Island", "Northern Triangle Island", "Eastern Fairy Island", "Fire Mountain", "Star Belt Archipelago",
         "Three Eye Reef", "Greatfish Isle", "Cyclops Reef", "Six Eye Reef", "Tower of the Gods Sector",
         "Eastern Triangle Island", "Thorned Fairy Island", "Needle Rock Isle", "Islet of Steel",
         "Stone Watcher Island", "Southern Triangle Island", "Private Oasis", "Bomb Island", "Birds Peak Rock",
         "Diamond Steppe Island", "Five Eye Reef", "Shark Island", "Southern Fairy Island", "Ice Ring Isle",
         "Forest Haven", "Cliff Plateau Isles", "Horseshoe Island", "Outset Island", "Headstone Island",
         "Two Eye Reef", "Angular Isles", "Boating Course", "Five Star Isles")


# objets de départ du monde ouvert mélangés (option « depart_melange »). RECOPIÉ (pas importé) de
# rando_lot7.DEPART_MELANGEABLES et de ses règles ; les tests comparent. Une case vidée a la clé (6, case, 0), ne reçoit
# qu'un rubis, et ces objets (hors de la liste des stubs) ne vont que dans un lieu du lot 6 (type 5).
TYPE_DEPART7 = 6
DEPART7 = (("bombes", 0x31, "Start - Bombs"), ("perle_din", 0x6A, "Start - Din's Pearl"),
           ("perle_farore", 0x6B, "Start - Farore's Pearl"), ("hymne_terre", 0x70, "Start - Earth God's Lyric"),
           ("hymne_vent", 0x71, "Start - Wind God's Aria"), ("baguette", 0x22, "Start - Wind Waker"),
           ("requiem", 0x6D, "Start - Wind's Requiem"), ("sac_butin", 0x24, "Start - Spoils Bag"),
           ("sac_facteur", 0x30, "Start - Delivery Bag"), ("flacon", 0x50, "Start - Empty Bottle"),
           ("bouclier", 0x3B, "Start - Hero's Shield"))
# boucliers progressifs (option « boucliers_progressifs ») : RECOPIÉ de rando_lot7 (OPTION_BOUCLIER, SITE_BOUCLIER…,
# TEMOINS_BOUCLIER) ; les tests comparent. La case « bouclier » n'est permise qu'avec l'option.
OPTION_BOUCLIER7 = "boucliers_progressifs"
SITE_BOUCLIER7 = (0x0254EED4, 0x7C0802A6)
SUITES_BOUCLIER7 = (0x0254EED8, 0x0254EF18)
COLLECT_BOUCLIER7 = 0xD5
TEMOINS_BOUCLIER7 = ((0x101E3A98 + 4 * 0x3B, 0x0254EED4), (0x101E3A98 + 4 * 0x3C, 0x0254EF14),
                     (0x0254EED4, 0x7C0802A6), (0x0254EED8, 0x90010004), (0x0254EEE0, 0x3D801020),
                     (0x0254EEE4, 0x818C84DC), (0x0254EEF0, 0x386C00D4), (0x0254EEF8, 0x3880003B),
                     (0x0254EEFC, 0x38600001), (0x0254EF00, 0x4BFD3499),
                     (0x0254EF14, 0x7C0802A6), (0x0254EF18, 0x90010004), (0x0254EF20, 0x3D801020),
                     (0x0254EF28, 0x818C84DC), (0x0254EF30, 0x386C00D4), (0x0254EF38, 0x3880003C),
                     (0x0254EF3C, 0x38600001), (0x0254EF40, 0x4BFD3459))
# Baguette ou Requiem à trouver : « Can Sail Away » ne demande que la voile (preuve dans rando_lot7, VARIANTE_VOILE,
# RECOPIÉE ici : les tests comparent)
VOILE7 = ("voile_seule", "Can Sail Away", "(Can_Play_WR and Boats_Sail) or Swift_Sail", "Boats_Sail or Swift_Sail")
VENT7 = ("baguette", "requiem")
SANS_VALEUR7 = frozenset({0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x0F})
FAMILLES_DEPART7 = ("tresors", "evenements", "boss", "sol", "pnj_code")
OCTETS_BOMBES7 = ((0x8A, 30), (0x90, 30))
SITES_BOMBES7 = ((0x0254E8C4, 0x980C008A), (0x0254E8CC, 0x980C0090))


def _depart7(options) -> tuple:
    """Noms des objets de départ mélangés par ces options (monde ouvert seulement), dans l'ordre de DEPART7."""
    if not isinstance(options, dict) or options.get("monde") != "ouvert":
        return ()
    v = options.get("depart_melange")
    if not isinstance(v, list):
        return ()
    return tuple(n for n, _o, _l in DEPART7 if n in v)


def _lot7(options) -> tuple:
    """(monde, île de départ ou "", re-combats sautés) quand le lot 7 change la logique, () sinon."""
    if not isinstance(options, dict):
        return ()
    ouvert = options.get("monde") == "ouvert"
    ile = options.get("depart") if options.get("depart") in ILES7 else ""
    rec = options.get("pas_de_recombats") is True
    if not (ouvert or ile or rec):
        return ()
    if any(n in VENT7 for n in _depart7(options)):
        return ("ouvert", ile, rec, VOILE7[0])      # (quatrième élément seulement dans ce cas)
    return ("ouvert" if ouvert else "histoire", ile, rec)


def _scenario7(dossier: str | None = None) -> dict:
    with open(os.path.join(dossier or DOSSIER, "scenario_ouvert.json"), encoding="utf-8") as f:
        return json.load(f)


def _docs7(docs: dict, lot7: tuple, sc7: dict) -> dict:
    """Les données de la partie avec le lot 7 : scénario du monde ouvert (clés « garde » du scénario d'origine,
    clés « remplace » de la seconde couche), réglage des re-combats, sortie du point de départ de Link."""
    monde, ile, rec = lot7[:3]
    out = dict(docs)
    sc = docs["scenario_vanilla"]
    if monde == "ouvert":
        nouveau = {}
        for k in sc7["garde"]:
            if k in sc:
                nouveau[k] = sc[k]
        nouveau.update(sc7["remplace"])
        sc = nouveau
    sc = dict(sc)
    sc["reglages"] = dict(sc["reglages"])
    sc["reglages"]["Skip_Refights"] = "true" if rec else "false"
    if lot7[3:]:
        if lot7[3:] != (VOILE7[0],) or monde != "ouvert" \
                or docs["macros"]["macros"].get(VOILE7[1]) != VOILE7[2]:
            raise Refus("lot 7 : variante de logique inconnue ou macro de TWWHD changée")
        sc["macros"] = dict(sc.get("macros") or {})
        sc["macros"][VOILE7[1]] = VOILE7[3]
    out["scenario_vanilla"] = sc
    if ile:
        zones = [dict(z) for z in docs["monde"]["zones"]]
        lien = [z for z in zones if z["nom"] == "Link's Spawn"]
        if len(lien) != 1 or set(lien[0]["sorties"]) != {"Outset Island"} or ile not in {z["nom"] for z in zones}:
            raise Refus("lot 7 : point de départ de Link inattendu")
        lien[0]["sorties"] = {ile: "Nothing"}
        out["monde"] = dict(docs["monde"])
        out["monde"]["zones"] = zones
    return out


# lot 8 (randomizer par fichiers, étape 4) : sans épée, entrées et cartes tirées. Règles RECOPIÉES de rando_lot8 (les
# tests comparent) : pour chaque entrée mélangeable (donjons puis grottes, ordre de TWWHD), la porte (zone de départ,
# zone d'arrivée) et le retour (zone de départ, zone d'arrivée) dans le graphe de rando_logique/monde.json.
ENTREES8 = (
    ("donjon", ("Dragon Roost Pond Past Statues", "DRC First Room"), ("DRC First Room", "Dragon Roost Pond Past Statues")),
    ("donjon", ("FW Entrance Platform", "FW First Room"), ("FW First Room", "FW Entrance Platform")),
    ("donjon", ("Tower of the Gods Sector", "TOTG Entrance Room"), ("TOTG Entrance Room", "Tower of the Gods Sector")),
    ("donjon", ("Headstone Island Interior", "ET First Room"), ("ET First Room", "Headstone Island Interior")),
    ("donjon", ("Gale Isle Interior", "WT First Room"), ("WT First Room", "Gale Isle Interior")),
    ("grotte", ("Outset Near Savage Headstone", "Outset Savage Labyrinth"),
     ("Outset Savage Labyrinth", "Outset Near Savage Headstone")),
    ("grotte", ("Dragon Roost Island", "Dragon Roost Island Cave"), ("Dragon Roost Island Cave", "Dragon Roost Island")),
    ("grotte", ("Fire Mountain", "Fire Mountain Interior"), ("Fire Mountain Interior", "Fire Mountain")),
    ("grotte", ("Ice Ring Isle", "Ice Ring Interior"), ("Ice Ring Interior", "Ice Ring Isle")),
    ("grotte", ("Ice Ring Interior", "Ice Ring Inner Cave"), ("Ice Ring Inner Cave", "Ice Ring Isle")),
    ("grotte", ("The Cabana", "Cabana Labyrinth"), ("Cabana Labyrinth", "The Cabana")),
    ("grotte", ("Needle Rock Isle", "Needle Rock Cave"), ("Needle Rock Cave", "Needle Rock Isle")),
    ("grotte", ("Angular Isles Small Isle", "Angular Isles Cave"), ("Angular Isles Cave", "Angular Isles Small Isle")),
    ("grotte", ("Boating Course Small Isle", "Boating Course Cave"), ("Boating Course Cave", "Boating Course Small Isle")),
    ("grotte", ("Stone Watcher Island", "Stone Watcher Cave"), ("Stone Watcher Cave", "Stone Watcher Island")),
    ("grotte", ("Overlook Island Upper Isles", "Overlook Cave"), ("Overlook Cave", "Overlook Island Upper Isles")),
    ("grotte", ("Birds Peak Rock Behind Bars", "Birds Peak Rock Cave"), ("Birds Peak Rock Cave", "Birds Peak Rock Behind Bars")),
    ("grotte", ("Pawprint Isle", "Pawprint Chu Chu Cave"), ("Pawprint Chu Chu Cave", "Pawprint Isle")),
    ("grotte", ("Pawprint Wizzrobe Cave Isle", "Pawprint Wizzrobe Cave"),
     ("Pawprint Wizzrobe Cave", "Pawprint Wizzrobe Cave Isle")),
    ("grotte", ("Diamond Steppe Upper Island", "Diamond Steppe Warp Maze"),
     ("Diamond Steppe Warp Maze", "Diamond Steppe Upper Island")),
    ("grotte", ("Bomb Island", "Bomb Island Cave"), ("Bomb Island Cave", "Bomb Island")),
    ("grotte", ("Rock Spire Upper Ledges", "Rock Spire Cave"), ("Rock Spire Cave", "Rock Spire Upper Ledges")),
    ("grotte", ("Shark Island", "Shark Island Cave"), ("Shark Island Cave", "Shark Island")),
    ("grotte", ("Cliff Plateau Isles", "Cliff Plateau Cave"), ("Cliff Plateau Cave", "Cliff Plateau Isles")),
    ("grotte", ("Cliff Plateau Highest Isle", "Cliff Plateau Cave Past Wooden Barrier"),
     ("Cliff Plateau Cave Past Wooden Barrier", "Cliff Plateau Highest Isle")),
    ("grotte", ("Horseshoe Island Past Tentacles", "Horseshoe Cave"), ("Horseshoe Cave", "Horseshoe Island Past Tentacles")),
    ("grotte", ("Star Island", "Star Island Cave"), ("Star Island Cave", "Star Island")),
)
# porte de l'Île de Glace et porte INTÉRIEURE (grotte intérieure) : la sortie de ce qui est derrière la porte intérieure
# ramène à l'île de la porte qui mène à l'Île de Glace (le bateau y est)
GLACE8, GLACE_INT8 = 8, 9
POOLS8 = {"aucune": (), "donjons": tuple(range(5)), "donjons_grottes": tuple(range(len(ENTREES8)))}
EPEE8 = ("Start - Hero's Sword", "Start - Master Sword", "Story - Sword Chamber Full Power")
COFFRE_EPEE8, OBJET_EPEE_SANS8 = "Hyrule Castle - Sword Chamber Chest", "Red Rupee"


def _tirage8(options) -> dict:
    """{porte: destination} du tirage d'entrées de la table ({} : aucun). Lève Refus s'il est mal formé."""
    if not isinstance(options, dict):
        return {}
    mode = options.get("entrees", "aucune")
    t = options.get("tirage_entrees")
    if mode not in POOLS8:
        raise Refus("option entrees inconnue")
    if t is None:
        return {}
    pool = POOLS8[mode]
    if mode == "aucune" or not isinstance(t, list) or len(t) != len(pool) \
            or any(type(x) is not int for x in t) or sorted(t) != list(pool):
        raise Refus("tirage des entrées mal formé")
    perm = dict(zip(pool, t))
    for porte, dest in perm.items():
        if ENTREES8[porte][0] != ENTREES8[dest][0] or (porte == GLACE_INT8 and dest == GLACE8):
            raise Refus("tirage des entrées interdit (catégories mélangées, ou Île de Glace vers elle-même)")
    return perm


def _retours8(perm: dict) -> dict:
    """{destination: porte dont le retour sert} : couplé, sauf derrière la porte intérieure de l'Île de Glace."""
    porte_de = {d: p for p, d in perm.items()}
    out = {}
    for d in perm.values():
        p = porte_de[d]
        out[d] = porte_de.get(GLACE8, GLACE8) if p == GLACE_INT8 else p
    return out


def _lot8(options) -> tuple:
    """(sans épée, mode, tirage d'entrées, tirage de cartes) quand le lot 8 change la logique, () sinon. Un tirage qui
    ne change rien (chaque porte vers elle-même, chaque carte à son île) compte pour vide."""
    if not isinstance(options, dict):
        return ()
    se = options.get("sans_epee") is True
    perm = _tirage8(options)
    bouge = any(p != d for p, d in perm.items()) or any(
        ENTREES8[p][2][1] != ENTREES8[d][2][1] for d, p in _retours8(perm).items())
    te = tuple(options["tirage_entrees"]) if bouge else ()
    tc = options.get("tirage_cartes") if options.get("cartes_melangees") is True else None
    tc = tuple(tc) if isinstance(tc, list) and tc != list(range(1, 50)) else ()
    if not (se or te or tc):
        return ()
    return (se, options.get("entrees") if te else "", te, tc)


def _docs8(docs: dict, lot8: tuple) -> dict:
    """Les données de la partie avec le lot 8 (après le lot 7)."""
    se, mode, te, tc = lot8
    out = dict(docs)
    if se:
        sc = dict(docs["scenario_vanilla"])
        if not all(any(h["nom"] == n for h in sc.get("lieux_histoire", ())) for n in EPEE8):
            raise Refus("lot 8 : sans épée sans le scénario du monde ouvert")
        sc["lieux_histoire"] = [h for h in sc["lieux_histoire"] if h["nom"] not in EPEE8]
        sc["reglages"] = dict(sc["reglages"])
        sc["reglages"]["Remove_Swords"] = "true"
        sc["objets_corriges"] = dict(sc.get("objets_corriges", {}))
        sc["objets_corriges"][COFFRE_EPEE8] = OBJET_EPEE_SANS8
        out["scenario_vanilla"] = sc
    if te:
        perm = dict(zip(POOLS8[mode], te))
        zones = {z["nom"]: dict(z, sorties=dict(z["sorties"])) for z in docs["monde"]["zones"]}
        nouvelles = []
        for p, d in perm.items():
            if p != d:
                de, vers = ENTREES8[p][1]
                nouvelles.append((de, ENTREES8[d][1][1], zones[de]["sorties"][vers], (de, vers)))
        for d, p in _retours8(perm).items():
            de, vers = ENTREES8[d][2]
            if ENTREES8[p][2][1] != vers:
                nouvelles.append((de, ENTREES8[p][2][1], zones[de]["sorties"][vers], (de, vers)))
        for _de, _vers, _ex, (a, b) in nouvelles:
            zones[a]["sorties"].pop(b)
        for de, vers, ex, _ in nouvelles:
            s = zones[de]["sorties"]
            s[vers] = f"({s[vers]}) or ({ex})" if vers in s else ex
        out["monde"] = dict(docs["monde"], zones=[zones[z["nom"]] for z in docs["monde"]["zones"]])
    if tc:
        m = dict(docs["macros"]["macros"])
        avant = {i: m[f"Chart For Island {i}"] for i in range(1, 50)}
        for i in range(1, 50):
            m[f"Chart For Island {i}"] = avant[tc[i - 1]]
        out["macros"] = dict(docs["macros"], macros=m)
    return out


def _releve8(lot8: tuple) -> str:
    """Empreinte des règles du lot 8 (même document que rando_lot8.releve_logique : les tests comparent)."""
    doc = {"sans_epee": [list(EPEE8), COFFRE_EPEE8, OBJET_EPEE_SANS8] if lot8[0] else None,
           "entrees": [[e[0], list(e[1]), list(e[2])] for e in ENTREES8] if lot8[2] else None,
           "ice": [GLACE8, GLACE_INT8] if lot8[2] else None,
           "cartes": "Chart For Island" if lot8[3] else None}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _version(base: str, options) -> str:
    """Version des données qu'une table de ces options doit annoncer : celle du modèle, et avec l'option
    « coffres_conflit » le relevé de ces coffres en plus."""
    if isinstance(options, dict) and options.get("coffres_conflit") is True:
        releve = json.dumps([list(c) for c in CONFLIT], ensure_ascii=False)
        base = hashlib.sha256(f"{base}|coffres_conflit|{releve}".encode("utf-8")).hexdigest()[:16]
    if isinstance(options, dict) and options.get("progressifs") is True:
        releve = json.dumps([[n, list(p)] for n, p in FAMILLES_PROG], ensure_ascii=False)
        base = hashlib.sha256(f"{base}|progressifs|{releve}".encode("utf-8")).hexdigest()[:16]
    if isinstance(options, dict) and any(options.get(c) in MODES_HORS
                                         for c in ("cles", "grandes_cles", "cartes_donjon")):
        base = hashlib.sha256(f"{base}|hors|{TYPE_HORS}|{OBJET_HORS}".encode("utf-8")).hexdigest()[:16]
    if _familles6(options):
        releve = json.dumps([[s["famille"], s["nom"], s["objet"], s["id"]] for s in _sites6()], ensure_ascii=False)
        releve = hashlib.sha256(releve.encode("utf-8")).hexdigest()[:16]
        base = hashlib.sha256(f"{base}|lot6|{releve}|{VERROU6}|{VERROU_ANKLE6}".encode("utf-8")
                              ).hexdigest()[:16]
    l7 = _lot7(options)
    if l7:
        doc = {"cle": list(l7), "scenario": _scenario7() if l7[0] == "ouvert" else None}
        r7 = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        base = hashlib.sha256(f"{base}|lot7|{r7}".encode("utf-8")).hexdigest()[:16]
    l8 = _lot8(options)
    if l8:
        base = hashlib.sha256(f"{base}|lot8|{_releve8(l8)}".encode("utf-8")).hexdigest()[:16]
    d7 = _depart7(options)
    if d7:
        # objets de départ mélangés : même document que rando_lot7.releve_depart (les tests comparent)
        doc = {"type": TYPE_DEPART7, "cases": [[n, o, l] for n, o, l in DEPART7 if n in d7],
               "sans_valeur": sorted(SANS_VALEUR7), "familles": list(FAMILLES_DEPART7),
               "bombes": [[list(x) for x in OCTETS_BOMBES7], [list(x) for x in SITES_BOMBES7]] if "bombes" in d7
               else None}
        rd = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        base = hashlib.sha256(f"{base}|depart|{rd}".encode("utf-8")).hexdigest()[:16]
    if isinstance(options, dict) and options.get(OPTION_BOUCLIER7) is True:
        # boucliers progressifs : même document que rando_lot7.releve_bouclier (les tests comparent)
        doc = {"site": list(SITE_BOUCLIER7), "suites": list(SUITES_BOUCLIER7), "collect": COLLECT_BOUCLIER7,
               "paliers": [0x3B, 0x3C], "temoins": [list(x) for x in TEMOINS_BOUCLIER7]}
        rb = hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        base = hashlib.sha256(f"{base}|boucliers|{rb}".encode("utf-8")).hexdigest()[:16]
    g9 =_groupes(options) & GROUPES9
    ep9 = isinstance(options, dict) and options.get("boutique_epuise") is True
    if g9 or ep9:
        # étape 5 : même document que rando_lot9.releve (les tests comparent)
        doc = {"sites": [[k, g, nom, o, list(a), [list(x) for x in x4], sorted(it)]
                         for nom, g, k, o, a, x4, it in DONNEES9 if g in g9],
               "seuls": sorted(SEULS9) if g9 else [], "verrou": VERROU9 if g9 else "",
               "epuise": [f"0x{a:08X}" for a in EPUISE9] if ep9 else []}
        r9 = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        base = hashlib.sha256(f"{base}|lot9|{r9}".encode("utf-8")).hexdigest()[:16]
    return base


def _norme(nom: str) -> str:
    return "".join("_" if ch == " " else ch for ch in nom if ch != "'")


# ------------------------------------------------------------------------------------------- analyseur
def _jetons(texte: str) -> list:
    """Jetons : '(' ')' 'and' 'or' '==' '!=', ('evt', nom), ('fn', nom, argument), ('mot', nom)."""
    out, i, n = [], 0, len(texte)
    while i < n:
        ch = texte[i]
        if ch == " ":
            i += 1
        elif ch in "()":
            out.append(ch)
            i += 1
        elif ch == "'":
            j = i + 1
            while True:
                j = texte.find("'", j)
                if j < 0:
                    raise Refus(f"apostrophe non fermée : {texte}")
                k = j
                while k + 1 < n and texte[k + 1] == "'":
                    k += 1
                if k + 1 >= n or texte[k + 1] in " )":
                    break
                j += 1
            out.append(("evt", _norme(texte[i + 1:j])))
            i = k + 1
        else:
            j = i
            while j < n and texte[j] not in " ()":
                j += 1
            mot = texte[i:j]
            if j < n and texte[j] == "(" and mot in ("can_access", "count", "health"):
                prof, k = 1, j + 1
                while k < n and prof:
                    prof += (texte[k] == "(") - (texte[k] == ")")
                    k += 1
                if prof:
                    raise Refus(f"parenthèse non fermée : {texte}")
                out.append(("fn", mot, texte[j + 1:k - 1]))
                i = k
            else:
                out.append(mot if mot in ("and", "or", "==", "!=") else ("mot", mot))
                i = j
    return out


def _groupes(options) -> frozenset:
    """Groupes de lieux « donnée » mélangés par ces options (option absente : coupée)."""
    return frozenset(g for g, cle in GROUPES.items() if isinstance(options, dict) and options.get(cle) is True)


def _somme(docs: dict) -> str:
    return hashlib.sha256(json.dumps({n: docs[n] for n in NOMS}, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


class _Modele:
    """Le monde tel que ce vérificateur le lit. ``groupes`` : groupes de lieux « donnée » mélangés ; les verrous
    « verrous_donnees » du scénario ne valent que pour eux (options coupées : le monde d'avant le lot 1)."""

    def __init__(self, docs: dict, groupes=frozenset(), familles6=frozenset(), sites6=(), lot7=(), sc7=None, lot8=()):
        sc, ov = docs["scenario_vanilla"], docs["objets_vanilla"]
        # version annoncée par une table : sans ce que le lot 1 a ajouté tant qu'aucun lieu « donnée » n'est mélangé
        # (la même qu'avant le lot), données entières sinon
        if groupes:
            self.version = _somme(docs)
        else:
            nu = dict(docs)
            nu["application"] = {k: v for k, v in docs["application"].items() if k != "description_donnees"}
            nu["application"]["lieux"] = [{k: v for k, v in a.items() if k != "donnees"}
                                          for a in docs["application"]["lieux"]]
            nu["scenario_vanilla"] = {k: v for k, v in sc.items() if k != "verrous_donnees"}
            self.version = _somme(nu)
        if lot7:
            # lot 7 : la version annoncée reste celle des données livrées (couche à part dans _version)
            docs = _docs7(docs, lot7, sc7)
            sc = docs["scenario_vanilla"]
        self.lot8 = tuple(lot8)
        if lot8:
            # lot 8 : idem (couche à part dans _version), après le lot 7
            docs = _docs8(docs, lot8)
            sc = docs["scenario_vanilla"]
        self.reglages = {k: str(v).lower() for k, v in sc["reglages"].items()}
        self.objets = {o["nom"]: o["id"] for o in docs["objets"]["objets"]}
        self.par_norme = {}
        for nom in self.objets:
            self.par_norme.setdefault(_norme(nom), nom)
        self.equiv = ov["equivalences"]
        self.chaines = ov["familles"]
        self.coeurs, self.recep, self.quart = ov["coeurs_depart"], ov["receptacle"], ov["quart"]
        self.macros = {_norme(k): v for k, v in docs["macros"]["macros"].items()}
        for k, v in sc.get("macros", {}).items():
            self.macros[_norme(k)] = v
        self._macro_fn, self._en_cours = {}, set()
        self._memo = {}
        zones = docs["monde"]["zones"]
        self.donjon_zone = {z["nom"]: z.get("donjon") for z in zones}
        self.depart, self.fin = sc["depart"], sc["fin"]

        # ---- arêtes, avec les verrous du scénario -----------------------------------------------------
        vg = sc["verrou_general"]

        def libre(z):
            if z in vg["zones_libres"] or self.donjon_zone[z] in vg["donjons_libres"]:
                return True
            return any(z[:len(p)] == p for p in vg["prefixes_libres"])
        corrigees = {(c["de"], c["vers"]): c["exigence"] for c in sc.get("sorties_corrigees", ())}
        self.aretes = []                                    # (zone, zone, test)
        for z in zones:
            for vers, ex in z["sorties"].items():
                tests = [self.test(corrigees.get((z["nom"], vers), ex))]
                for v in sc.get("verrous_zones", ()):
                    if v["zone"] == vers:
                        tests.append(self.test(v["exigence"]))
                if libre(z["nom"]) and not libre(vers):
                    tests.append(self.test(vg["exigence"]))
                dv = self.donjon_zone[vers]
                if dv and dv != self.donjon_zone[z["nom"]]:
                    for v in sc.get("verrous_donjons", ()):
                        if v["donjon"] == dv:
                            tests.append(self.test(v["exigence"]))
                self.aretes.append((z["nom"], vers, tests))
        self.evts = [(z["nom"], _norme(n), self.test(ex)) for z in zones for n, ex in z["evenements"].items()]
        self.evts += [(e["zone"], _norme(e["nom"]), self.test(e["exigence"])) for e in sc.get("evenements", ())]

        # ---- lieux : objet de logique et accès --------------------------------------------------------
        appli = {a["nom"]: a for a in docs["application"]["lieux"]}
        donjons = {d for d in self.donjon_zone.values() if d}
        par_id = {int(k): v for k, v in ov["par_id"].items()}
        jamais = {int(k) for k in ov["jamais_deplaces"]}
        self.speciaux = {"petite": int(ov["petite_cle"]), "grande": int(ov["grande_cle"]),
                         "carte": int(ov["carte_donjon"]), "boussole": int(ov["boussole"])}
        self.triforce = {int(x) for x in ov["triforce"]}
        self.interdits = {int(x) for x in ov["ids_interdits"]} | {0, 0xFF}
        self.par_id = par_id
        self.lieux = {}
        sites_conflit = {c[0]: (k, c[1], c[2], c[3]) for k, c in enumerate(CONFLIT)}
        for l in docs["lieux"]["lieux"]:
            a = appli[l["nom"]]
            tete = l["nom"].split(" - ")[0]
            dj = tete if tete in donjons else None
            nom_objet = sc.get("objets_corriges", {}).get(l["nom"], l["objet"])
            coffre, genre, prog = None, 0, None
            if a["applicable"] and a.get("methode", "coffre") == "coffre" and a["objet"] in OBJETS_PROG:
                # coffre d'un palier d'objet progressif : (famille, index, numéro, objet), qu'il soit ou non retenu par
                # « jamais déplacé » ; jamais hors des bornes de la table des coffres
                if type(a["idx"]) is not int or type(a["tbox"]) is not int or not 0 <= a["idx"] < 16 \
                        or not 0 <= a["tbox"] < 32 or l["type"] != "Chest":
                    raise Refus(f"coffre d'objet progressif mal décrit : {l['nom']}")
                prog = ([f for f, p in FAMILLES_PROG if a["objet"] in p][0], a["idx"], a["tbox"], a["objet"])
            if a["applicable"]:
                nom_objet = self.nom_logique(a["objet"], dj, nom_objet)
                if a.get("methode", "coffre") == "n2":
                    # cadeau d'un personnage : type 1, clé (site, 0) ; jamais dans un donjon
                    if dj is not None or type(a["site"]) is not int:
                        raise Refus(f"cadeau mal décrit : {l['nom']}")
                    genre = 1
                    if a["objet"] not in jamais:
                        coffre = (a["site"], 0, a["objet"])
                elif a.get("methode", "coffre") != "coffre":
                    raise Refus(f"méthode inconnue : {l['nom']}")
                elif a["objet"] not in jamais:
                    coffre = (a["idx"], a["tbox"], a["objet"])
            elif l["type"] in ("Chest", "Actor") and self.objets[nom_objet] in par_id:
                nom_objet = self.nom_logique(self.objets[nom_objet], dj, nom_objet)
            don = None
            dn = a.get("donnees")
            if dn is not None:
                # lieu « donnée » (courrier, boutique, enchère, personnage à table) : type 2, clé (site, 0), mélangé
                # par l'option de son groupe ; jamais dans un donjon, jamais un objet « jamais déplacé »
                if dj is not None or dn["groupe"] not in GROUPES or type(dn["site"]) is not int \
                        or type(dn["objet"]) is not int or dn["objet"] in jamais \
                        or (a["applicable"] and a["objet"] != dn["objet"]):
                    raise Refus(f"lieu « donnée » mal décrit : {l['nom']}")
                if not a["applicable"]:
                    nom_objet = self.nom_logique(dn["objet"], dj, nom_objet)
                    if self.objets[nom_objet] != dn["objet"]:
                        raise Refus(f"lieu « donnée » : objet de logique différent de la table du jeu : {l['nom']}")
                don = (dn["groupe"], dn["site"], dn["objet"], {int(x) for x in dn["interdits"]})
            conflit = None
            if l["nom"] in sites_conflit:
                # coffre « en conflit » : jamais applicable ni « donnée », clé et objet du relevé, objet de logique
                # de même identifiant, et aucun coffre ordinaire à sa clé (contrôlé après la boucle)
                site, idx, tbox, ident = sites_conflit[l["nom"]]
                if a["applicable"] or dn is not None or l["type"] != "Chest" or ident in jamais \
                        or (a.get("idx"), a.get("tbox"), a.get("objet")) != (idx, tbox, ident) \
                        or (ident not in self.speciaux.values() and self.objets[nom_objet] != ident):
                    raise Refus(f"coffre en conflit mal décrit : {l['nom']}")
                conflit = (site, idx, ident)
            self.lieux[l["nom"]] = {"objet": self.equiv.get(nom_objet, nom_objet), "coffre": coffre, "donjon": dj,
                                    "cats": set(l["categories"]), "genre": genre, "don": don, "conflit": conflit,
                                    "prog": prog, "fich": None}
            if prog is not None and (don is not None or conflit is not None
                                     or self.objets[nom_objet] != prog[3]):
                raise Refus(f"coffre d'objet progressif mal décrit : {l['nom']}")
        for _f, paliers in FAMILLES_PROG:
            for ident in paliers:
                if sum(1 for l in self.lieux.values() if l["prog"] and l["prog"][3] == ident) != 1:
                    raise Refus(f"objets progressifs : le palier {ident} n'a pas exactement un coffre")
        if sum(1 for l in self.lieux.values() if l["conflit"]) != len(CONFLIT):
            raise Refus("coffres en conflit : lieux absents des données")
        cles = {(l["coffre"][0], l["coffre"][1]) for l in self.lieux.values() if l["coffre"] and l["genre"] == 0}
        if any((c[1], c[2]) in cles for c in CONFLIT):
            raise Refus("coffres en conflit : un coffre ordinaire porte la même clé")
        for l in self.lieux.values():
            p = l["prog"]
            if p and ((l["coffre"] is None and (p[1], p[2]) in cles) or any((c[1], c[2]) == (p[1], p[2])
                                                                           for c in CONFLIT)):
                raise Refus("objets progressifs : un autre coffre porte la même clé")
        # lot 6 : lieux du randomizer par fichiers. Jamais un coffre, un cadeau, un lieu « donnée », en conflit ou
        # progressif. Famille mélangée : l'objet de logique est celui du relevé (objet réel du jeu).
        self.fich6 = {}
        for k, s in enumerate(sites6):
            l = self.lieux.get(s["nom"])
            if l is None or l["coffre"] is not None or l["don"] is not None or l["conflit"] is not None \
                    or l["prog"] is not None or l["genre"] != 0 or s["famille"] not in FAMILLES6:
                raise Refus(f"lieu du lot 6 inconnu ou déjà mélangé autrement : {s['nom']}")
            l["fich"] = (s["famille"], k, s)
            self.fich6[s["nom"]] = k
            if s["famille"] in familles6:
                if s["objet"] not in self.objets and self.equiv.get(s["objet"]) not in self.objets:
                    raise Refus(f"lieu du lot 6 : objet inconnu {s['objet']}")
                l["objet"] = self.equiv.get(s["objet"], s["objet"])
        # étape 5 : lieux « donnée » ajoutés (relevé recopié). Jamais un coffre, un cadeau, un autre lieu « donnée », en
        # conflit, progressif ou du lot 6 ; hors donjon ; objet de logique = objet du jeu à cet endroit
        brut = {l["nom"]: l["objet"] for l in docs["lieux"]["lieux"]}
        for nom, g, k, o, _a, _x, it in DONNEES9:
            l = self.lieux.get(nom)
            if l is None or l["coffre"] is not None or l["don"] is not None or l["conflit"] is not None \
                    or l["prog"] is not None or l["fich"] is not None or l["genre"] != 0 or l["donjon"] is not None \
                    or self.objets.get(brut.get(nom)) != o or o in jamais:
                raise Refus(f"lieu « donnée » de l'étape 5 mal décrit : {nom}")
            l["don"] = (g, k, o, set(it))
        cases7 = {l: (k, o) for k, (_n, o, l) in enumerate(DEPART7)}
        for h in sc.get("lieux_histoire", ()):
            self.lieux[h["nom"]] = {"objet": self.equiv.get(h["objet"], h["objet"]), "coffre": None,
                                    "donjon": None, "cats": set(), "genre": 0, "don": None, "conflit": None,
                                    "prog": None, "fich": None, "depart": cases7.get(h["nom"])}
            c = cases7.get(h["nom"])
            if c is not None and self.objets.get(self.lieux[h["nom"]]["objet"]) != c[1]:
                raise Refus(f"case de départ : objet de logique différent de l'objet donné : {h['nom']}")
        deplaces = {d["lieu"]: d for d in sc.get("lieux_deplaces", ())}
        # verrous par lieu du scénario : tests en plus de l'exigence du lieu (tous doivent passer)
        en_plus = {}
        for v in sc.get("verrous_lieux", ()):
            if v["lieu"] not in self.lieux:
                raise Refus(f"verrou sur un lieu inconnu : {v['lieu']}")
            en_plus.setdefault(v["lieu"], []).append(self.test(v["exigence"]))
        prevus = set(en_plus)
        for v in sc.get("verrous_donnees", ()):
            l = self.lieux.get(v["lieu"])
            if l is None or l["don"] is None:
                raise Refus(f"verrou « donnée » sur un lieu qui n'en est pas un : {v['lieu']}")
            prevus.add(v["lieu"])
            if l["don"][0] in groupes:                      # verrou compté seulement si le groupe est mélangé
                en_plus.setdefault(v["lieu"], []).append(self.test(v["exigence"]))
        for nom, g, _k, _o, _a, _x, _it in DONNEES9:        # étape 5 : idem
            prevus.add(nom)
            if g in groupes:
                en_plus.setdefault(nom, []).append(self.test(VERROU9))
        for nom, l in self.lieux.items():
            f = l["fich"]
            if f is not None and f[0] in familles6 and l["donjon"] is None and f[0] != "boss":
                en_plus.setdefault(nom, []).append(self.test(VERROU6))
        if "pnj_code" in familles6:                         # Ankle : coffres des statues atteints (événements)
            en_plus.setdefault(ANKLE6, []).append(self.test(VERROU_ANKLE6))
        for nom, l in self.lieux.items():
            if l["genre"] == 1 and nom not in en_plus:
                raise Refus(f"cadeau sans verrou d'histoire : {nom}")
            if l["don"] is not None and nom not in prevus:
                raise Refus(f"lieu « donnée » sans verrou d'histoire : {nom}")
        self.verrouilles = frozenset(en_plus)               # lieux qui portent un verrou compté

        def avec_verrous(nom, t):
            plus = en_plus.get(nom)
            if not plus:
                return t
            return lambda inv, ev, zo: t(inv, ev, zo) and all(u(inv, ev, zo) for u in plus)
        self.acces = []                                     # (zone, lieu, test)
        for z in zones:
            for nom, ex in z["lieux"].items():
                if nom not in deplaces:
                    self.acces.append((z["nom"], nom, avec_verrous(nom, self.test(ex))))
        for d in deplaces.values():
            self.acces.append((d["zone"], d["lieu"], avec_verrous(d["lieu"], self.test(d["exigence"]))))
        for h in sc.get("lieux_histoire", ()):
            self.acces.append((h["zone"], h["nom"], self.test(h["exigence"])))
        if "pnj_code" in familles6:
            par_lieu = {nom: (zone, t) for zone, nom, t in self.acces}
            for nom, ev in zip(COFFRES_STATUES6, EVTS_STATUES6):
                if nom not in par_lieu:
                    raise Refus(f"coffre de statue inconnu : {nom}")
                zone, t = par_lieu[nom]
                self.evts.append((zone, _norme(ev), t))
        self._origine = None

    def nom_logique(self, ident: int, donjon, defaut: str) -> str:
        """Nom de logique d'un objet du jeu posé dans un coffre du donjon `donjon`."""
        regle = self.par_id.get(ident)
        if regle is None:
            return defaut
        if regle[:2] == "@ ":
            if donjon is None:
                raise Refus("clé de donjon hors d'un donjon")
            return donjon + regle[1:]
        return regle

    # ---- exigences -> fermetures ----------------------------------------------------------------------
    def test(self, texte: str):
        f = self._memo.get(texte)
        if f is None:
            jetons = _jetons(texte)
            f, reste = self._expr(jetons, 0, texte)
            if reste != len(jetons):
                raise Refus(f"exigence mal formée : {texte}")
            self._memo[texte] = f
        return f

    def _expr(self, j, i, texte):
        termes, ops = [], set()
        f, i = self._terme(j, i, texte)
        termes.append(f)
        while i < len(j) and j[i] in ("and", "or"):
            ops.add(j[i])
            f, i = self._terme(j, i + 1, texte)
            termes.append(f)
        if len(ops) > 1:
            raise Refus(f"« and » et « or » mêlés : {texte}")
        if len(termes) == 1:
            return termes[0], i
        if ops == {"and"}:
            return (lambda inv, ev, zo: all(t(inv, ev, zo) for t in termes)), i
        return (lambda inv, ev, zo: any(t(inv, ev, zo) for t in termes)), i

    def _terme(self, j, i, texte):
        if i >= len(j):
            raise Refus(f"exigence tronquée : {texte}")
        t = j[i]
        if t == "(":
            f, i = self._expr(j, i + 1, texte)
            if i >= len(j) or j[i] != ")":
                raise Refus(f"parenthèse non fermée : {texte}")
            return f, i + 1
        if not isinstance(t, tuple):
            raise Refus(f"jeton inattendu {t!r} : {texte}")
        if t[0] == "evt":
            nom = t[1]
            return (lambda inv, ev, zo: nom in ev), i + 1
        if t[0] == "fn":
            if t[1] == "can_access":
                zone = t[2]
                if zone not in self.donjon_zone:
                    raise Refus(f"zone inconnue {zone!r}")
                return (lambda inv, ev, zo: zone in zo), i + 1
            if t[1] == "health":
                besoin = int(t[2])
                return (lambda inv, ev, zo: self.coeurs + inv.get(self.recep, 0)
                        + inv.get(self.quart, 0) // 4 >= besoin), i + 1
            n, _virgule, nom = t[2].partition(",")
            return self._possede(nom.strip(), int(n)), i + 1
        mot = t[1]
        if i + 2 < len(j) + 0 and j[i + 1] in ("==", "!="):
            droite = j[i + 2]
            if mot not in self.reglages or not isinstance(droite, tuple):
                raise Refus(f"test de réglage invalide : {texte}")
            vrai = (self.reglages[mot] == droite[1].lower()) == (j[i + 1] == "==")
            return (lambda inv, ev, zo: vrai), i + 3
        if mot == "Nothing":
            return (lambda inv, ev, zo: True), i + 1
        if mot == "Impossible":
            return (lambda inv, ev, zo: False), i + 1
        if mot in self.macros:
            return self._macro(mot), i + 1
        return self._possede(mot, 1), i + 1

    def _macro(self, mot):
        if mot not in self._macro_fn:
            if mot in self._en_cours:
                raise Refus(f"macro récursive {mot}")
            self._en_cours.add(mot)
            self._macro_fn[mot] = self.test(self.macros[mot])
            self._en_cours.discard(mot)
        return self._macro_fn[mot]

    def _possede(self, norme: str, combien: int):
        nom = self.par_norme.get(norme)
        if nom is None:
            raise Refus(f"objet inconnu {norme!r}")
        nom = self.equiv.get(nom, nom)
        chaine = self.chaines.get(nom)
        if chaine is None:
            return lambda inv, ev, zo: inv.get(nom, 0) >= combien

        def prefixe(inv, ev, zo):
            # objets distincts qui se suivent : on compte le début de chaîne possédé, dans l'ordre
            for rang, membre in enumerate(chaine):
                if inv.get(membre, 0) < 1:
                    return rang >= combien
            return len(chaine) >= combien
        return prefixe

    # ---- parcours ------------------------------------------------------------------------------------
    def atteints(self, contenu: dict, en_main: dict | None = None) -> set:
        """Lieux atteignables avec ce contenu ({lieu: objet}), en partant avec ``en_main`` ({objet: nombre}). Recalcule
        tout à chaque objet gagné."""
        inv, pris = dict(en_main or {}), set()
        while True:
            zo, ev = {self.depart}, set()
            bouge = True
            while bouge:
                bouge = False
                for de, vers, tests in self.aretes:
                    if de in zo and vers not in zo and all(t(inv, ev, zo) for t in tests):
                        zo.add(vers)
                        bouge = True
                for zone, nom, t in self.evts:
                    if zone in zo and nom not in ev and t(inv, ev, zo):
                        ev.add(nom)
                        bouge = True
            nouveaux = [lieu for zone, lieu, t in self.acces
                        if lieu not in pris and zone in zo and t(inv, ev, zo)]
            if not nouveaux:
                return pris
            for lieu in set(nouveaux):
                pris.add(lieu)
                objet = contenu.get(lieu)
                if objet:
                    inv[objet] = inv.get(objet, 0) + 1

    def ident(self, nom: str) -> int:
        """Identifiant d'origine d'un lieu mélangé (coffre, cadeau ou lieu « donnée »)."""
        l = self.lieux[nom]
        if l.get("depart") is not None:
            return l["depart"][1]
        if l["fich"] is not None:
            return l["fich"][2]["id"]
        if l["coffre"] is not None:
            return l["coffre"][2]
        if l["don"] is None and l["conflit"] is None and l["prog"] is not None:
            return l["prog"][3]
        return l["conflit"][2] if l["conflit"] is not None and l["don"] is None else l["don"][2]

    def origine(self) -> set:
        if self._origine is None:
            self._origine = self.atteints({n: l["objet"] for n, l in self.lieux.items()})
        return self._origine

    def reference8(self, places) -> set:
        """Lot 8 : lieux atteints avec en main les objets des lieux ``places`` (ceux que la table place), les autres
        gardant le leur : la référence « tous les lieux » quand le placement d'origine n'a plus de sens."""
        contenu = {n: l["objet"] for n, l in self.lieux.items() if n not in places}
        main = {}
        for n in places:
            o = self.lieux[n]["objet"]
            main[o] = main.get(o, 0) + 1
        return self.atteints(contenu, main)

    # ---- coffres mélangés selon les options --------------------------------------------------------------
    def melanges(self, options: dict) -> dict:
        """{(type, idx, tbox): nom du lieu} des lieux que ces options mélangent (type 0 : coffre ; type 1 : cadeau
        d'un personnage, clé (site, 0), seulement avec l'option « cadeaux » ; type 2 : lieu « donnée », clé
        (site, 0), dès que l'option de son groupe est allumée, quelles que soient les catégories ; type 3 : coffre
        « en conflit », clé (site, 0), seulement avec l'option « coffres_conflit » et selon les règles des coffres)."""
        actives = {CATS[c] for c in options["lieux"]}
        sp = self.speciaux
        out = {}
        # option « progressifs » : une famille est libérée seulement si TOUS ses coffres sont mélangeables (ni exclus,
        # ni hors des catégories) ; alors celui que « jamais déplacé » retenait suit les règles des coffres
        liberes = set()
        if options.get("progressifs") is True:
            for famille, paliers in FAMILLES_PROG:
                noms = [n for n, l in self.lieux.items() if l["prog"] and l["prog"][0] == famille]
                if len(noms) == len(paliers) and all(n not in options["exclus"] and self.lieux[n]["cats"] <= actives
                                                     for n in noms):
                    liberes.update(noms)
        familles6 = _familles6(options)
        d7 = _depart7(options)
        for nom, l in self.lieux.items():
            if l.get("depart") is not None:
                if DEPART7[l["depart"][0]][0] in d7:
                    if nom in options["exclus"]:
                        raise Refus(f"case de départ mélangée et exclue : {nom}")
                    out[(TYPE_DEPART7, l["depart"][0], 0)] = nom
                continue
            if l["fich"] is not None and l["fich"][0] in familles6:
                if nom not in options["exclus"] and not (l["fich"][2]["id"] in self.triforce
                                                         and options["triforce"] != "melange"):
                    out[(TYPE_FICHIER, l["fich"][1], 0)] = nom
                continue
            if l["don"] is not None and options.get(GROUPES[l["don"][0]]) is True:
                if nom not in options["exclus"]:
                    out[(2, l["don"][1], 0)] = nom
                continue
            conflit = l["conflit"] if options.get("coffres_conflit") is True else None
            coffre = l["coffre"]
            if coffre is None and conflit is None and nom in liberes:
                coffre = l["prog"][1:]
            if (coffre is None and conflit is None) or nom in options["exclus"] or not l["cats"] <= actives:
                continue
            if l["genre"] == 1 and options.get("cadeaux") is not True:
                continue
            ident = conflit[2] if conflit is not None else coffre[2]
            if ident in sp.values() and l["donjon"] is None:
                continue
            if ident == sp["petite"] and options["cles"] not in MODES_MELANGE:
                continue
            if ident == sp["grande"] and options["grandes_cles"] not in MODES_MELANGE:
                continue
            if ident in (sp["carte"], sp["boussole"]) and options["cartes_donjon"] not in MODES_MELANGE:
                continue
            if ident in self.triforce and options["triforce"] != "melange":
                continue
            if conflit is not None:
                out[(3, conflit[0], 0)] = nom
                continue
            out[(l["genre"], coffre[0], coffre[1])] = nom
        return out


_MODELES = {}


def _modele(dossier: str | None = None, options=None) -> _Modele:
    """Modèle du monde pour ces options (un par ensemble de groupes de lieux « donnée » mélangés)."""
    cle = (os.path.abspath(dossier or DOSSIER), _groupes(options), _familles6(options), _lot7(options),
           _lot8(options))
    if cle not in _MODELES:
        docs = {}
        for n in NOMS:
            with open(os.path.join(cle[0], n + ".json"), encoding="utf-8") as f:
                docs[n] = json.load(f)
        if cle[4] and (cle[4][2] or cle[4][3]):
            # lot 8 : un modèle par tirage (entrées, cartes) ; on n'en garde que les derniers
            tires = [k for k in _MODELES if k[4] and (k[4][2] or k[4][3])]
            for k in tires[:max(0, len(tires) - 31)]:
                del _MODELES[k]
        _MODELES[cle] = _Modele(docs, cle[1], cle[2], _sites6(cle[0]), cle[3],
                                _scenario7(cle[0]) if cle[3] else None, cle[4])
    return _MODELES[cle]


def verifier_table(options: dict, table, dossier: str | None = None) -> list:
    """Liste des problèmes d'une table `[[type, idx, tbox, origine, nouveau], ...]` (vide = acceptée)."""
    try:
        return _verifier(_modele(dossier, options), options, table)
    except (Refus, KeyError, TypeError, ValueError, AttributeError, IndexError) as ex:
        return [f"table ou options invérifiables : {type(ex).__name__} {ex}"]


def verifier_message(msg, dossier: str | None = None) -> list:
    """Liste des problèmes d'un message de randomizer complet (vide = accepté)."""
    try:
        m = _modele(dossier, msg.get("options"))
        if msg.get("v") != 2 or msg.get("algo") != "wwmp-rando-v2":
            return ["version ou algorithme inconnu"]
        if isinstance(msg.get("options"), dict) and msg["options"].get("depart", "origine") not in ("origine",) + ILES7:
            return ["île de départ inconnue ou non tirée"]
        o = msg.get("options")
        if isinstance(o, dict) and ((o.get("entrees", "aucune") != "aucune" and o.get("tirage_entrees") is None)
                                    or (o.get("cartes_melangees") is True and o.get("tirage_cartes") is None)):
            return ["entrées ou cartes non tirées (option sans son tirage)"]
        if isinstance(o, dict) and o.get("tirage_cartes") is not None and (
                o.get("cartes_melangees") is not True or not isinstance(o["tirage_cartes"], list)
                or sorted(o["tirage_cartes"]) != list(range(1, 50))):
            return ["tirage des cartes mal formé"]
        if isinstance(o, dict) and (o.get("sans_epee") is True or o.get("entrees", "aucune") != "aucune")                 and o.get("monde") != "ouvert":
            return ["sans épée et entrées mélangées exigent le monde ouvert"]
        if msg.get("donnees") != _version(m.version, msg.get("options")):
            return ["données de logique différentes de celles du vérificateur"]
        return _verifier(m, msg["options"], msg["t"])
    except (Refus, KeyError, TypeError, ValueError, AttributeError, IndexError) as ex:
        return [f"message invérifiable : {type(ex).__name__} {ex}"]


def _verifier(m: _Modele, options: dict, table) -> list:
    pb = []
    melanges = m.melanges(options)
    sp = set(m.speciaux.values())
    vus, avant, apres = {}, [], []
    # lot 6 : objets des lieux mélangés du randomizer par fichiers qui ne vont que dans un tel lieu (hors de PERMIS2,
    # ou paliers des Grandes Fées)
    seuls6 = {m.ident(nom) for (t6, _i, _z), nom in melanges.items() if t6 == TYPE_FICHIER
              and (m.ident(nom) not in PERMIS2 or m.ident(nom) in FEES6)}
    # étape 5 : objets des lieux « donnée » ajoutés qui ne vont que dans un lieu « donnée »
    seuls9 = {m.ident(nom) for (t9, _i, _z), nom in melanges.items() if t9 == 2 and m.ident(nom) in SEULS9}
    # objets de départ mélangés : hors de la liste des stubs, ils ne vont que dans un lieu du lot 6 (et toute pièce de
    # même numéro aussi : les bombes du bateau pirate) ; il faut une famille du lot 6 qui sache les donner
    d7 = _depart7(options)
    seuls6 |= {o for n, o, _l in DEPART7 if n in d7 and o not in PERMIS2}     # (le flacon : partout)
    if d7 and not any(options.get(FAMILLES6[f]) is True for f in FAMILLES_DEPART7):
        return ["objets de départ mélangés sans famille du lot 6 qui sache les donner"]
    if "bouclier" in d7 and options.get(OPTION_BOUCLIER7) is not True:
        return ["bouclier de départ mélangé sans les boucliers progressifs (il remplacerait le bouclier miroir)"]
    if d7 and sum(1 for k in melanges if k[0] == TYPE_DEPART7) != len(d7):
        return ["objets de départ mélangés : cases de départ absentes (scénario du monde ouvert ?)"]
    for k9, dep in DEPENDANCES9.items():
        if options.get(k9) is True and options.get(dep) is not True:
            return [f"option {k9} sans l'option {dep}"]
    hors = {}                                    # (index, coffre) -> (objet réel, donjon de destination)
    for ligne in table:
        if len(ligne) != 5 or any(type(x) is not int or not 0 <= x <= 255 for x in ligne):
            return [f"entrée mal formée : {ligne!r}"]
        typ, idx, tbox, o, n = ligne
        if typ == TYPE_HORS:
            if (idx, tbox) in hors:
                return [f"coffre étranger en double : ({idx}, {tbox})"]
            hors[(idx, tbox)] = (o, n)
            continue
        if typ not in (0, 1, 2, 3, TYPE_FICHIER, TYPE_DEPART7):
            return [f"type de lieu inconnu : {typ}"]
        nom = melanges.get((typ, idx, tbox))
        if nom is None:
            pb.append(f"lieu ({typ}, {idx}, {tbox}) : pas un lieu mélangé avec ces options")
            continue
        if typ == TYPE_FICHIER and not _accepte6(m.lieux[nom]["fich"][2], n, sp):
            pb.append(f"{nom} : objet {n} interdit à cet endroit (lieu du randomizer par fichiers)")
            continue
        if typ == TYPE_DEPART7 and n not in SANS_VALEUR7:
            pb.append(f"{nom} : une case de départ vidée ne reçoit qu'un rubis (objet {n})")
            continue
        if typ != TYPE_FICHIER and n != o and n in seuls6 and not (typ == 2 and n in seuls9):
            pb.append(f"{nom} : objet {n} d'un lieu du randomizer par fichiers hors d'un tel lieu")
            continue
        if typ != 2 and n != o and n in seuls9 and not (typ == TYPE_FICHIER and n in seuls6):
            pb.append(f"{nom} : objet {n} d'une lettre ou de l'enchère à 100 rubis hors d'un lieu « donnée »")
            continue
        if typ in (1, 2) and n in sp:
            pb.append(f"{nom} : objet de donjon {n} donné par un personnage, une lettre ou une boutique")
            continue
        if typ in (1, 2) and n in OBJETS_CONFLIT:
            pb.append(f"{nom} : objet {n} d'un coffre en conflit donné ailleurs que dans un coffre")
            continue
        if typ != 0 and n in OBJETS_PROG and options.get("progressifs") is True:
            pb.append(f"{nom} : palier d'objet progressif {n} ailleurs que dans un coffre ordinaire")
            continue
        if typ == 2 and n != o and n in m.lieux[nom]["don"][3]:
            pb.append(f"{nom} : objet {n} interdit à cet endroit")
            continue
        if nom in vus:
            pb.append(f"coffre en double : {nom}")
            continue
        if m.ident(nom) != o:
            pb.append(f"{nom} : objet d'origine faux")
        if n in m.interdits:
            pb.append(f"{nom} : identifiant interdit {n}")
        vus[nom] = n
        avant.append(o)
        apres.append(n)
    if pb:
        return pb
    if len(vus) != len(melanges):
        return [f"{len(melanges) - len(vus)} lieux mélangés absents de la table"]

    def chez(nom):
        l = m.lieux[nom]                          # index de sauvegarde : celui du coffre, en conflit ou non
        return (l["donjon"], l["coffre"][0] if l["coffre"] else l["conflit"][1] if l["conflit"]
                else l["prog"][1] if l["prog"] else -1)

    # lot 5 : coffres qui portent un objet de donjon étranger (objet d'attente dans le coffre, objet réel à part)
    mode = {m.speciaux["petite"]: options["cles"], m.speciaux["grande"]: options["grandes_cles"],
            m.speciaux["carte"]: options["cartes_donjon"], m.speciaux["boussole"]: options["cartes_donjon"]}
    # (objet de donjon, index de sauvegarde) -> donjon, d'après les lieux d'origine mélangés
    maison = {(m.ident(nom), chez(nom)[1]): chez(nom) for nom in melanges.values() if m.ident(nom) in sp}
    reel = {}                                    # lieu -> (objet réel, (donjon, index) de destination)
    for (idx, tbox), (objet, slot) in sorted(hors.items()):
        nom = melanges.get((0, idx, tbox))
        # relecture adverse (lot 5) : un lieu progressif rendu mobile (option « progressifs ») EST un coffre ordinaire
        # (sa clé est rangée dans « prog », pas dans « coffre ») : le refuser faisait échouer le tirage de la graine
        l = m.lieux[nom] if nom is not None else None
        if l is None or l["genre"] != 0 or l["conflit"] is not None or (l["coffre"] is None and l["prog"] is None):
            return [f"coffre étranger ({idx}, {tbox}) : pas un coffre ordinaire mélangé"]
        if vus[nom] != OBJET_HORS:
            return [f"{nom} : coffre étranger sans son objet d'attente"]
        if objet not in sp or mode[objet] not in MODES_HORS:
            return [f"{nom} : objet {objet} hors de son donjon sans l'option qui le permet"]
        dest = maison.get((objet, slot))
        if dest is None:
            return [f"{nom} : objet de donjon {objet} pour un donjon ({slot}) qui n'en a pas"]
        if dest == chez(nom):
            return [f"{nom} : objet de donjon noté étranger dans son propre donjon"]
        if mode[objet] == "tout_donjon" and m.lieux[nom]["donjon"] is None:
            return [f"{nom} : objet de donjon {objet} hors de tout donjon (option « tout_donjon »)"]
        reel[nom] = (objet, dest)
        apres.remove(OBJET_HORS)
        apres.append(objet)
    if sorted(avant) != sorted(apres):
        return ["objets perdus ou dupliqués : la table n'est pas une permutation"]
    # les objets de donjon ne quittent ni leur donjon ni leur index de sauvegarde (un objet étranger compte pour le
    # donjon que son entrée désigne : le serveur l'y crédite)
    for ident in sp:
        def compte(valeurs):
            c = {}
            for nom in melanges.values():
                if valeurs(nom)[0] == ident:
                    cle = valeurs(nom)[1]
                    c[cle] = c.get(cle, 0) + 1
            return c
        if compte(lambda nom: (m.ident(nom), chez(nom))) != compte(lambda nom: reel.get(nom, (vus[nom], chez(nom)))):
            pb.append(f"objet de donjon {ident} sorti de son donjon")
    if pb:
        return pb
    # nom de logique de chaque identifiant déplacé
    noms = {}
    for nom in melanges.values():
        l = m.lieux[nom]
        ident = m.ident(nom)
        if ident not in sp and noms.setdefault(ident, l["objet"]) != l["objet"]:
            return [f"identifiant {ident} : deux objets de logique différents"]
    contenu = {nom: l["objet"] for nom, l in m.lieux.items()}
    for nom, ident in vus.items():
        if nom in reel:
            objet = m.nom_logique(reel[nom][0], reel[nom][1][0], "")
            contenu[nom] = m.equiv.get(objet, objet)
        elif ident in sp:
            objet = m.nom_logique(ident, m.lieux[nom]["donjon"], "")
            contenu[nom] = m.equiv.get(objet, objet)
        else:
            contenu[nom] = noms[ident]
    pris = m.atteints(contenu)
    if m.fin not in pris:
        pb.append("partie infinissable : Ganondorf hors d'atteinte")
    if options.get("tous_lieux", True):
        ref = m.reference8(set(melanges.values())) if m.lot8 else m.origine()
        perdus = sorted(ref - pris)
        if perdus:
            pb.append(f"{len(perdus)} lieux inaccessibles (ex. {perdus[:3]})")
    return pb
