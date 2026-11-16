"""Randomizer par fichiers générés localement, étape 3 : lot 7 (docs/etudes/rando_fichiers.md §3, éléments 12 à 18).

Monde ouvert, autre départ, cinématiques raccourcies, pas de re-combats, textes du jeu en trois langues, coffres à la
place de quatre cinématiques. Tout passe par le pack WWMP_Rando GÉNÉRÉ sur chaque PC à partir de son propre dump
(rando/fichiers.py) : fichiers du jeu réécrits (salles, stages, listes d'événements, collisions, textes) et
lignes de code du pack. Une option par élément, toutes COUPÉES par défaut :

======================  ===================================================================================
option                  ce qu'elle fait
======================  ===================================================================================
monde = ouvert          « make_game_nonlinear » et « remove_cutscenes » de TWWHD (sauf ce qui est écarté plus
                        bas, avec la raison), crochet de création de partie 0x025AECE4 (bits de départ, objets
                        de départ), garde-fous écrits dans les fichiers (Makar, récifs, Forteresse, porte de
                        Jabun, île-fée, rocher de l'Île du Dragon, point d'apparition de la Tour des Dieux…).
                        Impose les quatre coffres ci-dessous.
depart = île / hasard   (exige monde = ouvert) partie neuve sur une autre île : 4 immédiats du code + bateau
                        déplacé dans le stage de la mer (en monde ouvert, le bateau est toujours amené à l'île de
                        départ, l'Île de l'Aurore comprise, comme TWWHD). « hasard » est tiré par le moteur depuis la graine ; la
                        table porte l'île choisie
coffres_cinematiques    coffres à la place de cinématiques : Reine des fées (seul possible en monde « histoire »),
                        Médolie (grappin), Jabu (perle de Nayru), Épée de légende (monde ouvert seulement)
cinematiques_courtes    Zéphos raccourci, ouverture des enchères raccourcie, sept petites caméras retirées,
                        chants rejoués sans animation, Tott danse une fois, tornade sans cinématique, menu de la
                        bouteille de Tingle sauté
pas_de_recombats        les quatre bits « souvenir du boss vaincu » posés à la création de la partie
textes                  bulles du jeu : vendeurs, enchères, Beedle, lettre de Beedle, prix du calmar géant (l'objet
                        réellement donné), numéro des cartes au trésor, capacités d'arc et de bombes, deux
                        coquilles d'origine. Toujours en anglais, français ET espagnol (même empreinte partout)
boucliers_progressifs   la fonction d'objet du bouclier du héros donne le bouclier miroir à qui a déjà un bouclier
                        (jamais de rétrogradation) ; permet de mélanger le bouclier de départ (depart_melange)
======================  ===================================================================================

Ce qui vient de TWWHD-Randomizer (SuperDude88 et contributeurs, licence MIT, « Copyright (c) 2024 SuperDude88 »,
texte : tools/rando/logique/LICENSE_TWWHD.txt) : correctifs d'assembleur (tools/rando/lot7_asm/*.asm,
convertis par tools/rando/port_twwhd.py), liste des bits de départ (init_save_with_tweaks), éditions de
fichiers (tweaks.cpp), textes (text_replacements.cpp) et noms d'objets en trois langues (item_data.yaml, recopiés
dans rando_logique/noms_objets.json). Le crochet de création (petit code à tables) et la logique du monde ouvert
(rando_logique/scenario_ouvert.json) sont à nous.

Écarts avec TWWHD, et pourquoi (le détail est dans docs/joueurs/RANDOMIZER.md) :

* les objets que le jeu d'origine donne par l'histoire et que TWWHD met dans sa réserve d'objets (épée, bouclier,
  Baguette, voile, sacs, flacon, magie, perles de Din et de Farore, hymnes de la Terre et du Vent) sont DONNÉS AU
  DÉPART : notre moteur ne fait que permuter des objets de coffres (il ne sait pas en ajouter), et TWWHD retire les
  cinématiques qui les donnaient ; le coffre de l'Épée de légende donne l'épée à pleine puissance (les prières des
  sages, retirées, ne la chargent plus). Option « depart_melange » (coupée par défaut, jamais essayée en jeu) : les
  bombes, les perles de Din et de Farore, les deux hymnes, la Baguette du Vent, le Requiem du Vent, les deux sacs et le
  flacon ne sont plus donnés au départ mais placés dans un lieu du lot 6 (le flacon : aussi dans un coffre ; la case
  de départ vidée reçoit un rubis, jamais donné). Baguette ou Requiem à trouver : contrairement à TWWHD, la logique
  compte qu'on navigue avec la seule voile (VARIANTE_VOILE, preuve dans le code du bateau). Le bouclier du héros ne se
  mélange qu'avec l'option « boucliers_progressifs ». La voile, le Talisman et les épées restent au départ (voir
  DEPART_MELANGEABLES) ;
* les quatre coffres ajoutés gardent leur objet d'origine (aucun n'est dans la liste du pack Mailbox) ;
* écartés de « make_game_nonlinear » : les six sites des serviteurs de la Tour des Dieux (ils vont avec des
  éditions de fichiers et deux acteurs neufs, SwOp et DngSw, non portés : la tour reste dans l'ordre d'origine) ;
  écartés de « remove_cutscenes » : l'ouverture rapide des coffres (code du coffre, où vit le stub N1) ;
* la porte de la Forteresse (« fix_ff_door ») écrit l'indice de propriété 0x0011 en GRAND-BOUTISTE : TWWHD l'écrit
  dans l'ordre de l'hôte, ce qui donne 0x1100 au jeu, hors des 44 propriétés du fichier ;
* non portés ici (étapes suivantes ou hors périmètre) : misc_rando_features, fix_vanilla_bugs, entrées
  (prevent_reverse_door_softlocks…), cartes du GamePad, indices.

Règles : chaque édition relit ce qu'elle remplace (nom d'acteur, octets, actions) ; options coupées = rien d'écrit,
rien de changé dans la logique ni dans le pack. Ce module ne touche ni au jeu ni au réseau.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct

ICI = os.path.dirname(os.path.abspath(__file__))
DOSSIER_ASM = os.path.join(ICI, "lot7_asm")
FICHIER_SCENARIO = os.path.join(ICI, "logique", "scenario_ouvert.json")
FICHIER_NOMS = os.path.join(ICI, "logique", "noms_objets.json")

MONDES = ("histoire", "ouvert")
# « boucliers_progressifs » (AJOUTÉE À LA FIN, coupée par défaut) : la fonction d'objet du bouclier du héros devient
# progressive (voir CAVE_BOUCLIER plus bas) ; seule voie pour mélanger le bouclier de départ (depart_melange)
OPTION_BOUCLIER = "boucliers_progressifs"
OPTIONS_BOOL = ("coffres_cinematiques", "cinematiques_courtes", "pas_de_recombats", "textes", OPTION_BOUCLIER)
OPTIONS = ("monde",) + OPTIONS_BOOL          # « depart » existait déjà (seule valeur : « origine »)
DEPART_ORIGINE, DEPART_HASARD = "origine", "hasard"

# salles des îles (islandNameToRoomNum de TWWHD, logic/Area.cpp) ; départ possible : salles 2 à 49 (pas la
# Forteresse), comme le « départ au hasard » de TWWHD
ILES = {
    "Forsaken Fortress Sector": 1, "Star Island": 2, "Northern Fairy Island": 3, "Gale Isle": 4,
    "Crescent Moon Island": 5, "Seven Star Isles": 6, "Overlook Island": 7, "Four Eye Reef": 8,
    "Mother & Child Isles": 9, "Spectacle Island": 10, "Windfall Island": 11, "Pawprint Isle": 12,
    "Dragon Roost Island": 13, "Flight Control Platform": 14, "Western Fairy Island": 15, "Rock Spire Isle": 16,
    "Tingle Island": 17, "Northern Triangle Island": 18, "Eastern Fairy Island": 19, "Fire Mountain": 20,
    "Star Belt Archipelago": 21, "Three Eye Reef": 22, "Greatfish Isle": 23, "Cyclops Reef": 24,
    "Six Eye Reef": 25, "Tower of the Gods Sector": 26, "Eastern Triangle Island": 27, "Thorned Fairy Island": 28,
    "Needle Rock Isle": 29, "Islet of Steel": 30, "Stone Watcher Island": 31, "Southern Triangle Island": 32,
    "Private Oasis": 33, "Bomb Island": 34, "Birds Peak Rock": 35, "Diamond Steppe Island": 36,
    "Five Eye Reef": 37, "Shark Island": 38, "Southern Fairy Island": 39, "Ice Ring Isle": 40,
    "Forest Haven": 41, "Cliff Plateau Isles": 42, "Horseshoe Island": 43, "Outset Island": 44,
    "Headstone Island": 45, "Two Eye Reef": 46, "Angular Isles": 47, "Boating Course": 48, "Five Star Isles": 49,
}
DEPARTS = tuple(sorted((n for n, s in ILES.items() if s >= 2), key=lambda n: ILES[n]))
SALLE_ORIGINE = 44


class ErreurLot7(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------------------------
def ouvert(options) -> bool:
    return isinstance(options, dict) and options.get("monde") == "ouvert"


def ile_depart(options) -> str | None:
    """Île de départ choisie (nom de TWWHD), ou None pour le départ d'origine."""
    if not isinstance(options, dict):
        return None
    d = options.get("depart", DEPART_ORIGINE)
    return d if d in ILES else None


def actif(options) -> bool:
    """Une option du lot 7 est-elle allumée ?"""
    return isinstance(options, dict) and (ouvert(options) or any(options.get(o) is True for o in OPTIONS_BOOL)
                                         or ile_depart(options) is not None)


def options_fichiers(options) -> dict:
    """Options du lot 7 allumées (forme courte, pour le manifeste du pack)."""
    if not actif(options):
        return {}
    out = {o: True for o in OPTIONS_BOOL if options.get(o) is True}
    if ouvert(options):
        out["monde"] = "ouvert"
    if ile_depart(options):
        out["depart"] = ile_depart(options)
    if departs_melanges(options):
        out[OPTION_DEPART] = list(departs_melanges(options))
    return out


def ile_au_hasard(graine: str) -> str:
    """Île tirée pour « depart = hasard » : condensé de la graine (pas le flux du remplissage, qui reste celui
    d'avant), donc la même île pour la même graine chez tous."""
    h = hashlib.sha256(b"WWMP-depart|" + str(graine).encode("utf-8")).digest()
    return DEPARTS[int.from_bytes(h[:8], "big") % len(DEPARTS)]


def controler_options(o: dict) -> None:
    """Contrôle des options du lot 7 DÉJÀ normalisées (types et dépendances). Lève ErreurLot7."""
    if o.get("monde") not in MONDES:
        raise ErreurLot7("option monde : « histoire » ou « ouvert »")
    for k in OPTIONS_BOOL:
        if not isinstance(o.get(k), bool):
            raise ErreurLot7(f"option {k} : booléen attendu")
    d = o.get("depart")
    if d != DEPART_ORIGINE and d != DEPART_HASARD and d not in DEPARTS:
        raise ErreurLot7("option depart : « origine », « hasard » ou le nom anglais d'une île (" +
                         ", ".join(DEPARTS[:4]) + "…)")
    if d != DEPART_ORIGINE and o.get("monde") != "ouvert":
        raise ErreurLot7("option depart : une autre île exige le monde ouvert (monde = « ouvert »)")
    if o.get("monde") == "ouvert" and o.get("coffres_cinematiques") is not True:
        raise ErreurLot7("monde ouvert : les coffres à la place des cinématiques sont exigés (les cinématiques de "
                         "Médolie, de Jabu et de l'Épée de légende sont retirées)")
    dm = o.get(OPTION_DEPART, [])
    if dm != normaliser_depart(dm):
        raise ErreurLot7("option depart_melange : liste non normalisée")
    if dm:
        if o.get("monde") != "ouvert":
            raise ErreurLot7("option depart_melange : exige le monde ouvert (monde = « ouvert ») : hors du monde "
                             "ouvert, ces objets ne sont pas donnés au départ")
        if "bouclier" in dm and o.get(OPTION_BOUCLIER) is not True:
            raise ErreurLot7("option depart_melange : " + RAISON_BOUCLIER + " ; coche les boucliers "
                             "progressifs (option boucliers_progressifs) pour le mélanger")
        if not any(o.get("lieux_" + f) is True for f in FAMILLES_DEPART):
            raise ErreurLot7("option depart_melange : ces objets ne peuvent être donnés que par un lieu du randomizer "
                             "par fichiers (le pack Mailbox ne les met pas dans un coffre) : coche au moins une de ces "
                             "familles : trésors engloutis, objets d'événements, réceptacles des boss, objets posés, "
                             "personnages à immédiat")


def controler_exclus_depart(o: dict) -> None:
    """Un objet de départ mélangé ne peut pas être aussi « exclu » (sa case garderait l'objet dans la logique alors
    que le pack ne le donne plus : objet perdu). Lève ErreurLot7."""
    exclus = set(o.get("exclus") or ())
    pris = [l for n, _o, l in DEPART_MELANGEABLES if n in departs_melanges(o) and l in exclus]
    if pris:
        raise ErreurLot7(f"option depart_melange : {pris[0]!r} est aussi dans l'option exclus (décoche plutôt sa "
                         "case : un objet de départ exclu serait perdu)")


# ---------------------------------------------------------------------------------------------------------------
# Logique (seconde couche de scénario)
# ---------------------------------------------------------------------------------------------------------------
def cle_logique(options) -> tuple:
    """Ce qui, dans ces options, change la logique : () quand rien (logique d'avant le lot)."""
    if not isinstance(options, dict):
        return ()
    o, d, r = ouvert(options), ile_depart(options), options.get("pas_de_recombats") is True
    if not (o or d or r):
        return ()
    if voile_seule(options):
        # Baguette ou Requiem à trouver (option « depart_melange ») : variante « la voile suffit pour naviguer »
        # (VARIANTE_VOILE) ; quatrième élément SEULEMENT dans ce cas (clé, empreinte et version d'avant sinon)
        return ("ouvert", d or "", r, VARIANTE_VOILE[0])
    return ("ouvert" if o else "histoire", d or "", r)


def charger_scenario() -> dict:
    with open(FICHIER_SCENARIO, encoding="utf-8") as f:
        return json.load(f)


def scenario_de(sc_vanille: dict, sc_ouvert: dict) -> dict:
    """Scénario complet du monde ouvert : celui d'origine pour ce qui ne dépend pas de l'histoire (verrous prudents
    des cadeaux et des lieux « donnée », corrections de portes et d'objets, fin), le reste remplacé par
    scenario_ouvert.json."""
    sc = {k: v for k, v in sc_vanille.items() if k in sc_ouvert["garde"]}
    for k, v in sc_ouvert["remplace"].items():
        sc[k] = v
    return sc


def docs_logique(docs: dict, cle: tuple, sc_ouvert: dict | None = None) -> dict:
    """Données de logique de la partie : ``docs`` (non modifiés) avec la clé ``cle`` de ``cle_logique``."""
    if not cle:
        return docs
    monde, depart, recombats = cle[:3]
    out = dict(docs)
    sc = dict(docs["scenario_vanilla"])
    if monde == "ouvert":
        sc = scenario_de(sc, sc_ouvert or charger_scenario())
    sc["reglages"] = dict(sc["reglages"], Skip_Refights="true" if recombats else "false")
    if cle[3:]:
        if cle[3:] != (VARIANTE_VOILE[0],) or monde != "ouvert":
            raise ErreurLot7(f"logique : variante inconnue {cle[3:]!r}")
        # Baguette ou Requiem à trouver : « Can Sail Away » ne demande que la voile (preuve : VARIANTE_VOILE) ; tout
        # le reste (chants, Requiem pour un vent précis, Mélodie du Vent des énigmes) garde ses exigences de TWWHD
        macros = docs["macros"]["macros"]
        nom, avant, apres = VARIANTE_VOILE[1:]
        if macros.get(nom) != avant:
            raise ErreurLot7(f"logique : la macro {nom!r} de TWWHD a changé ({macros.get(nom)!r})")
        sc["macros"] = dict(sc.get("macros") or {}, **{nom: apres})
    out["scenario_vanilla"] = sc
    if depart:
        zones = []
        trouve = False
        for z in docs["monde"]["zones"]:
            if z["nom"] == "Link's Spawn":
                if list(z["sorties"]) != ["Outset Island"]:
                    raise ErreurLot7("logique : « Link's Spawn » ne mène plus seulement à l'Île de l'Aurore")
                z = dict(z, sorties={depart: "Nothing"})
                trouve = True
            zones.append(z)
        if not trouve or depart not in {z["nom"] for z in zones}:
            raise ErreurLot7(f"logique : zone de départ inconnue {depart!r}")
        out["monde"] = dict(docs["monde"], zones=zones)
    return out


def releve_logique(options) -> str:
    """Empreinte de ce que le lot 7 change à la logique de cette partie ("" si rien)."""
    cle = cle_logique(options)
    if not cle:
        return ""
    doc = {"cle": list(cle), "scenario": charger_scenario() if cle[0] == "ouvert" else None}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------------------------
# Chemins des fichiers (GamePath.hpp de TWWHD)
# ---------------------------------------------------------------------------------------------------------------
_PACK1 = frozenset({0, 1, 11, 13, 17, 23})
_PACK2 = frozenset({9, 39, 41, 44})


def chemin_salle(stage: str, salle: int, feuille: str = "room.dzr@DZX") -> str:
    if stage == "sea" and salle in _PACK1:
        base = f"content/Common/Pack/szs_permanent1.pack@SARC@sea_Room{salle}.szs"
    elif stage == "sea" and salle in _PACK2:
        base = f"content/Common/Pack/szs_permanent2.pack@SARC@sea_Room{salle}.szs"
    else:
        base = f"content/Common/Stage/{stage}_Room{salle}.szs"
    return f"{base}@YAZ0@SARC@Room{salle}.bfres@BFRES@{feuille}"


def chemin_stage(stage: str, feuille: str = "stage.dzs@DZX") -> str:
    if stage == "sea":
        base = "content/Common/Pack/first_szs_permanent.pack@SARC@sea_Stage.szs"
    else:
        base = f"content/Common/Stage/{stage}_Stage.szs"
    return f"{base}@YAZ0@SARC@Stage.bfres@BFRES@{feuille}"


SEA_EVENTS = chemin_stage("sea", "event_list.dat@EVENTS")

# ---------------------------------------------------------------------------------------------------------------
# Coffres à la place de cinématiques (add_chest_in_place_* de TWWHD). Octets des entrées recopiés de tweaks.cpp ;
# l'objet (octet 0x1C, octet fort de angle.z) est celui du jeu d'origine. Clés (index de sauvegarde, numéro de
# coffre) vérifiées libres sur le dump : Médolie (3, 17), Reine (0, 28), Jabu (11, 6), Épée (9, 0).
# ---------------------------------------------------------------------------------------------------------------
TRES_MEDLI = bytes.fromhex("74616b6172613300ff200880c4ca99ec4654800043838 45a0009cc160fffffff".replace(" ", ""))
TRES_REINE = bytes.fromhex("74616b6172613300ff200e00c82fcfc04434c000c8434ec000091000a5ffffff")
TRES_JABUN = bytes.fromhex("74616b6172613300ff2ff3050000000043960000c34800000000800005ffffff")
ACTR_RADEAU = b"Ikada\0\0\0" + bytes(18) + bytes.fromhex("80000000ffff")
TRES_EPEE = bytes.fromhex("74616b6172613300ff205004c2f6fd71c5494000c5f4e90a000000006affffff")
COFFRES = {
    # nom : (chemin, objet du coffre, clé (index de sauvegarde, numéro), exige le monde ouvert, lieu de la logique)
    "reine": (chemin_salle("sea", 9), 0x35, (0, 28), False, "Mother & Child Isles - Inside Mother Isle"),
    "medli": (chemin_stage("M_Dra09"), 0x25, (3, 17), True, "Dragon Roost Cavern - Mini Boss"),
    "jabun": (chemin_salle("Pjavdou", 0), 0x69, (11, 6), True, "Outset Island - Jabun's Cave Chest"),
    "epee": (chemin_salle("kenroom", 0), 0x3E, (9, 0), True, "Hyrule Castle - Sword Chamber Chest"),
}
# bit WATCHED_FIRE_AND_ICE_ARROWS_CUTSCENE : posé à la création avec le coffre de la Reine (sinon elle donnerait
# aussi ses flèches par la cinématique)
BIT_REINE = 0x1001
BITS_RECOMBATS = (0x3904, 0x3902, 0x3901, 0x3A80)


def coffres_actifs(options) -> tuple:
    if not (isinstance(options, dict) and options.get("coffres_cinematiques") is True):
        return ()
    return tuple(n for n, c in COFFRES.items() if ouvert(options) or not c[3])


def _tres(data: bytes, objet: int) -> bytes:
    d = bytearray(data)
    if len(d) != 0x20 or d[:8] != b"takara3\0":
        raise ErreurLot7("coffre : entrée TRES mal formée")
    d[0x1C] = objet
    return bytes(d)


# ---------------------------------------------------------------------------------------------------------------
# Éditions de fichiers
# ---------------------------------------------------------------------------------------------------------------
# petites caméras retirées (remove_minor_pan_cs de TWWHD) : (stage, salle ou None pour le stage, numéro d'événement)
PETITES_CAMERAS = (("M_NewD2", 2, 4), ("kindan", None, 2), ("Siren", 18, 2), ("M_Dai", 3, 7), ("sea", 41, 19),
                   ("sea", 41, 22), ("sea", 41, 23))
RECIFS = (24, 46, 22, 8, 37, 25)
WARP_GT = b"Warpmj\0\0" + bytes.fromhex("00000011c8930fd900000000c891f7fa000000000000ffff")
SPAWN_TOTG = bytes.fromhex("4c696e6b0000000032ff201a47c34f5f00000000447a0000000000000101ffff")
ROCHER_DRI = bytes.fromhex("4562726f636b0000f27f083f42932826c248000043cc75c200000000 00ffffff".replace(" ", ""))
SORTIE_FF2 = b"sea\0\0\0\0\0" + bytes([0x00, 0x01, 0x00, 0xFF])
# porte de la Forteresse : face 0x1493 de sea_Room1/room.dzb, indice de propriété 0x0020 -> 0x0011 (voir l'en-tête)
FF_FACE, FF_PROP_AVANT, FF_PROP_APRES = 0x1493, 0x0020, 0x0011


def editions(options) -> list[tuple]:
    """Éditions de fichiers du lot 7 pour ces options : [(chemin, opération, arguments)], ordre fixe."""
    out = []
    if not actif(options):
        return out
    o = ouvert(options)
    for nom in coffres_actifs(options):
        out.append((COFFRES[nom][0], "coffre_" + nom, ()))
    if o:
        out.append((chemin_salle("kaze", 3), "makar", ()))
        for r in RECIFS:
            out.append((chemin_salle("sea", r), "recifs", ()))
        out.append((chemin_salle("M2tower", 0), "ff2", ()))
        out.append((chemin_salle("sea", 1), "warp_gt", ()))
        out.append((chemin_salle("sea", 1, "room.dzb@BRUT"), "porte_ff", ()))
        out.append((chemin_salle("sea", 15), "ile_fee", ()))
        out.append((chemin_salle("Adanmae", 0), "rocher_dri", ()))
        out.append((chemin_salle("M_NewD2", 2), "murs_invisibles", ()))
        out.append((chemin_salle("sea", 26), "spawn_totg", ()))
        out.append((chemin_salle("sea", 44), "obstacles_jabun", ()))
        out.append((SEA_EVENTS, "porte_jabun", ()))
        # bateau (Lion rouge) amené au point de bateau 0 de l'île de départ, l'Île de l'Aurore comprise : comme
        # change_ship_starting_island de TWWHD, appelé pour TOUTE partie (le bateau du stage de la mer est sinon au
        # milieu de la mer, et l'île de départ sans bateau est un blocage)
        salle = ILES[ile_depart(options)] if ile_depart(options) else SALLE_ORIGINE
        out.append((chemin_salle("sea", salle), "bateau", (salle,)))
    if isinstance(options, dict) and options.get("cinematiques_courtes") is True:
        out.append((SEA_EVENTS, "zephos", ()))
        out.append((chemin_stage("Orichh", "event_list.dat@EVENTS"), "encheres", ()))
        for st, salle, ev in PETITES_CAMERAS:
            ch = chemin_salle(st, salle) if salle is not None else chemin_stage(st)
            out.append((ch, "petite_camera", (ev,)))
    return out


def _une(entrees, nom: str, quoi: str):
    lst = [e for e in entrees if e.nom == nom]
    if len(lst) != 1:
        raise ErreurLot7(f"{quoi} : {len(lst)} acteur(s) {nom!r} au lieu d'un")
    return lst[0]


def _remplacer(e, pos: int, avant: bytes, apres: bytes, quoi: str) -> None:
    if bytes(e.data[pos:pos + len(avant)]) != avant:
        raise ErreurLot7(f"{quoi} : octets inattendus ({bytes(e.data[pos:pos + len(avant)]).hex()})")
    e.data[pos:pos + len(apres)] = apres


def _ajouter_unique(dz, type_, couche, data: bytes, quoi: str):
    # même couche seulement : le téléporteur de la Tour de Ganon existe déjà, identique, dans une autre couche de la
    # Forteresse (TWWHD l'ajoute à la couche 1)
    if any(bytes(e.data) == data for e in dz.entrees(type_, couche)):
        raise ErreurLot7(f"{quoi} : entrée déjà présente dans cette couche (fichier déjà modifié ?)")
    return dz.ajouter(type_, couche, data)


def appliquer(session, chemin: str, op: str, args: tuple) -> None:
    """Applique une édition de ``editions`` dans la session du générateur."""
    f = _OPS.get(op)
    if f is None:
        raise ErreurLot7(f"opération inconnue : {op}")
    f(session, chemin, *args)


def _coffre_reine(s, ch):
    _ajouter_unique(s.ouvrir(ch), "TRES", None, _tres(TRES_REINE, COFFRES["reine"][1]), "coffre de la Reine")


def _coffre_medli(s, ch):
    _ajouter_unique(s.ouvrir(ch), "TRES", None, _tres(TRES_MEDLI, COFFRES["medli"][1]), "coffre de Médolie")
    # leurre de TWWHD dans le stage de la Caverne du Dragon (mêmes octets, objet d'origine de TWWHD)
    _ajouter_unique(s.ouvrir(chemin_stage("M_NewD2")), "TRES", None, TRES_MEDLI, "leurre de Médolie")


def _coffre_jabun(s, ch):
    dz = s.ouvrir(ch)
    _ajouter_unique(dz, "ACTR", None, ACTR_RADEAU, "radeau de Jabu")
    _ajouter_unique(dz, "TRES", None, _tres(TRES_JABUN, COFFRES["jabun"][1]), "coffre de Jabu")


def _coffre_epee(s, ch):
    dz = s.ouvrir(ch)
    defaut = dz.entrees("ACTR", None)
    if len(defaut) < 7 or [e.nom for e in defaut[5:7]] != ["VmsMS", "VmsDZ"]:
        raise ErreurLot7("Épée de légende : acteurs du socle introuvables")
    dz.retirer(defaut[5])
    dz.retirer(defaut[6])
    c5 = dz.entrees("ACTR", 5)
    if len(c5) < 5 or [c5[i].nom for i in (0, 2, 3, 4)] != ["Yswdr00", "ALLdie", "Tn", "Tn"]:
        raise ErreurLot7("Épée de légende : acteurs de la couche 5 inattendus")
    copies = [bytes(c5[i].data) for i in (0, 2, 3, 4)]
    for i in (0, 2, 3, 4):
        dz.retirer(c5[i])
    for d in copies:
        dz.ajouter("ACTR", None, d)
    _ajouter_unique(dz, "TRES", None, _tres(TRES_EPEE, COFFRES["epee"][1]), "coffre de l'Épée")
    sp = [e for e in dz.entrees("PLYR") if e.data[29] == 0x0A]
    if not sp:
        raise ErreurLot7("Épée de légende : point d'apparition 0x0A introuvable")
    sp[0].data[0x14:0x18] = bytes.fromhex("C584859A")
    sp[0].data[0x10:0x14] = bytes.fromhex("C538563D")


def _makar(s, ch):
    dz = s.ouvrir(ch)
    acteurs = dz.entrees("ACTR")
    dz.retirer(_une(acteurs, "AND_SW2", "enlèvement de Makar"))
    wiz = [e for e in acteurs if e.nom == "wiz_r"]
    if not wiz:
        raise ErreurLot7("enlèvement de Makar : magicien introuvable")
    for e in wiz:
        e.data[9] = 0xFF


def _recifs(s, ch):
    n = 0
    for e in s.ouvrir(ch).entrees("ACTR"):
        if e.nom == "Ocanon" and e.data[0xA] == 0x2A:
            e.data[0xA] = 0xFF
            n += 1
        elif e.nom == "Oship" and e.data[0x19] == 0x2A:
            e.data[0x19] = 0xFF
            n += 1
    if not n:
        raise ErreurLot7(f"récifs : aucun canon ni navire lié à Spectre-Ganon dans {ch}")


def _ff2(s, ch):
    dz = s.ouvrir(ch)
    sp = [e for e in dz.entrees("PLYR") if e.data[29] == 0x10]
    if not sp:
        raise ErreurLot7("Forteresse 2 : point d'apparition 0x10 introuvable")
    sp[0].data[8] = 0xFF
    sc = [e for e in dz.entrees("SCLS") if bytes(e.data[:8]) == b"M2ganon\0"]
    if len(sc) != 1:
        raise ErreurLot7("Forteresse 2 : sortie vers M2ganon introuvable")
    sc[0].data[:] = SORTIE_FF2


def _warp_gt(s, ch):
    _ajouter_unique(s.ouvrir(ch), "ACTR", 1, WARP_GT, "téléporteur vers la Tour de Ganon")


def _porte_ff(s, ch):
    b = s.ouvrir(ch)
    faces = struct.unpack_from(">I", b.brut, 0xC)[0]
    n = struct.unpack_from(">I", b.brut, 0x8)[0]
    nprop = struct.unpack_from(">I", b.brut, 0x28)[0]
    if FF_FACE >= n or FF_PROP_APRES >= nprop:
        raise ErreurLot7("porte de la Forteresse : collisions inattendues")
    b.ecrire(faces + FF_FACE * 0xA + 6, struct.pack(">H", FF_PROP_AVANT), struct.pack(">H", FF_PROP_APRES))


def _ile_fee(s, ch):
    sp = [e for e in s.ouvrir(ch).entrees("PLYR") if e.data[29] == 0x01]
    if not sp:
        raise ErreurLot7("île-fée de l'ouest : point d'apparition 1 introuvable")
    sp[0].data[0xC:0x10] = bytes.fromhex("C89C5540")


def _rocher_dri(s, ch):
    e = [x for x in s.ouvrir(ch).entrees("ACTR") if x.nom == "Eskban"]
    if not e:
        raise ErreurLot7("rocher de l'Île du Dragon introuvable")
    e[0].data[:] = ROCHER_DRI


def _murs_invisibles(s, ch):
    n = 0
    for e in s.ouvrir(ch).entrees("SCOB"):
        if e.nom == "Akabe":
            e.data[0xB] = 0xFF
            n += 1
    if not n:
        raise ErreurLot7("murs invisibles de la Caverne du Dragon introuvables")


def _spawn_totg(s, ch):
    sp = s.ouvrir(ch).entrees("PLYR")
    if len(sp) < 10 or sp[9].nom != "Link" or sp[9].data[29] != 0x01:
        raise ErreurLot7("Tour des Dieux : point d'apparition 9 inattendu")
    sp[9].data[:] = SPAWN_TOTG


def _obstacles_jabun(s, ch):
    dz = s.ouvrir(ch)
    c5 = dz.entrees("ACTR", 5)
    if len(c5) < 2 or [c5[0].nom, c5[1].nom] != ["Ajav", "Auzu"]:
        raise ErreurLot7("Jabu : porte et tourbillon de la couche 5 introuvables")
    copies = [bytes(c5[0].data), bytes(c5[1].data)]
    dz.retirer(c5[0])
    dz.retirer(c5[1])
    for d in copies:
        dz.ajouter("ACTR", None, d)


def _porte_jabun(s, ch):
    ev = s.ouvrir(ch)
    gardes = {}
    for acteur, debut in (("DIRECTOR", 1), ("CAMERA", 2), ("Ship", 2)):
        gardes[acteur] = ev.retirer_actions("ajav_uzu", acteur, debut)
    ev.poser_drapeaux_fin("ajav_uzu", (gardes["DIRECTOR"][-1].drapeau, gardes["CAMERA"][-1].drapeau, -1))


def _bateau(s, ch, salle):
    dz = s.ouvrir(ch)
    bateaux = [e for e in dz.entrees("SHIP") if e.data[0xE] == 0]
    if not bateaux:
        raise ErreurLot7(f"départ : aucun point de bateau 0 dans la salle {salle}")
    b = bateaux[-1]              # comme TWWHD : le dernier point de bateau 0 trouvé
    stage = s.ouvrir(chemin_stage("sea"))
    ship = _une(stage.entrees("ACTR"), "Ship", "bateau du stage de la mer")
    ship.data[0xC:0x18] = b.data[0:0xC]
    ship.data[0x1A:0x1C] = b.data[0xC:0xE]
    ship.data[0x10:0x14] = bytes.fromhex("C8F42400")     # recopié de TWWHD (« prevent softlock on fire mountain »)


def _zephos(s, ch):
    ev = s.ouvrir(ch)
    g = {a: ev.retirer_actions("TACT_HT", a, n) for a, n in (("Hr", 7), ("Link", 7), ("CAMERA", 5))}
    if not any(a.nom == "059get_dance" for a in g["Link"]):
        raise ErreurLot7("Zéphos : le chant ne serait plus donné")
    ev.poser_drapeaux_fin("TACT_HT", (g["Hr"][-1].drapeau, g["Link"][-1].drapeau, g["CAMERA"][-1].drapeau))


def _encheres(s, ch):
    s.ouvrir(ch).retirer_actions("AUCTION_START", "CAMERA", 3, 5)


def _petite_camera(s, ch, ev):
    dz = s.ouvrir(ch)
    n = 0
    for e in dz.entrees("SCOB"):
        if e.nom == "TagEv" and e.data[8] == ev:
            dz.retirer(e)
            n += 1
    for e in dz.entrees("PLYR"):
        if e.data[8] == ev:
            e.data[8] = 0xFF
            n += 1
    if not n:
        raise ErreurLot7(f"petite caméra {ev} introuvable dans {ch}")


_OPS = {"coffre_reine": _coffre_reine, "coffre_medli": _coffre_medli, "coffre_jabun": _coffre_jabun,
        "coffre_epee": _coffre_epee, "makar": _makar, "recifs": _recifs, "ff2": _ff2, "warp_gt": _warp_gt,
        "porte_ff": _porte_ff, "ile_fee": _ile_fee, "rocher_dri": _rocher_dri, "murs_invisibles": _murs_invisibles,
        "spawn_totg": _spawn_totg, "obstacles_jabun": _obstacles_jabun, "porte_jabun": _porte_jabun,
        "bateau": _bateau, "zephos": _zephos, "encheres": _encheres, "petite_camera": _petite_camera}
OPERATIONS = frozenset(_OPS)


# ---------------------------------------------------------------------------------------------------------------
# Code du pack généré
# ---------------------------------------------------------------------------------------------------------------
# Fragments de TWWHD convertis (rando_lot7_asm/<nom>.asm) : sites de départ, relus dans les diffs de TWWHD
SITES_NONLINEAIRE = (
    0x02026D3C, 0x02190EF4, 0x0219264C, 0x021A4B4C, 0x021B0BC0, 0x021B51CC, 0x021CA1A8, 0x021CD230, 0x021F3F1C,
    0x021F3FB8, 0x021F4064, 0x021F40FC, 0x021F8BE0, 0x021FCDE4, 0x02208D34, 0x022099A0, 0x02209AF4, 0x02209C48,
    0x02209C70, 0x02209F88, 0x02209FCC, 0x0220B5CC, 0x0221F8D8, 0x0221FAC4, 0x0221FAE8, 0x02227DA4, 0x0222C6D8,
    0x0222C6E8, 0x0222C9BC, 0x02286314, 0x02286A98, 0x02286ABC, 0x022C7A4C, 0x022C7D4C, 0x022C7E40, 0x022D9520,
    0x022DEF78, 0x022E0A5C, 0x023149EC, 0x023164C8, 0x0231987C, 0x02339554, 0x023465DC, 0x02372F74, 0x0237305C,
    0x02374648, 0x023746CC, 0x02386BA0, 0x02387500, 0x023A4A98, 0x024731E8, 0x0247348C, 0x02474B58, 0x02474B7C,
    0x02474B9C, 0x0247A574, 0x024B22F4, 0x024B2798, 0x024D4F1C, 0x024EA7E8, 0x025221F0, 0x025224A4, 0x02522C60,
    0x025255F4, 0x02551220, 0x02551244, 0x0255B46C, 0x025B9B98, 0x025B9BB0, 0x025B9C28, 0x025B9C48, 0x025B9C5C,
    0x02677D3C, 0x02678B74, 0x026790B0)
# écartés : serviteurs de la Tour des Dieux (vont avec des éditions de fichiers et deux acteurs neufs non portés)
SITES_ECARTES_NONLINEAIRE = (0x022AC06C, 0x022AC2B8, 0x022AE9C8, 0x0235BF38, 0x0252C670, 0x0252C6A4)
SITES_CINE_OUVERT = (
    0x025AEDCC, 0x025AEDE0, 0x025AEE00,              # longue introduction sautée
    0x024D602C,                                      # cinématiques de sortie des boss
    0x02336D68,                                      # montée de la Tour des Dieux
    0x024D4EC0, 0x024D53D0,                          # téléporteur vers Hyrule : huit fragments de la Triforce
    0x023A421C, 0x025542E0, 0x02554308, 0x024D4F28,  # lettres d'Orca, d'Arielle et de Tingle
    0x0254F52C, 0x0254F540, 0x0254F554,              # perles posées dès qu'on les reçoit
    0x0207FBFC,                                      # couche de la salle de Ganon marionnette
    0x02299808, 0x022400AC, 0x02240688,              # Ivan et Mme Marie
    0x0221F530)                                      # Makar ne remercie plus (blocage rare, même d'origine)
SITES_CINE_COURTES = (
    0x02482764, 0x0247DCAC,                          # Requiem de la Tornade sans cinématique
    0x02439DE4, 0x0243AD3C,                          # chants rejoués sans animation
    0x022EFB2C,                                      # Tott danse une fois
    0x022ECC1C)                                      # menu de la bouteille de Tingle sauté
# écartés : ouverture rapide des coffres (0x024B7408, 0x024B74D4, données 0x101D232E/3A/46) : code du coffre
SITES_ECARTES_CINE = (0x024B7408, 0x024B74D4, 0x101D232E, 0x101D233A, 0x101D2346)
GROUPES_TWWHD = {"ouvert": SITES_NONLINEAIRE, "cine_ouvert": SITES_CINE_OUVERT, "cine_courtes": SITES_CINE_COURTES}

# crochet de création de partie (le nôtre, d'après init_save_with_tweaks) : 0x025AECE4 « lis r12, 0x1020 »
SITE_CREATION, AVANT_CREATION = 0x025AECE4, 0x3D801020
EXEC_ITEM_GET, ON_EVENT_BIT, ON_DUNGEON_ITEM = 0x0254DA38, 0x025B8B68, 0x025B9098
SYMBOLES_CREATION = {EXEC_ITEM_GET: "wwr_jeu_0254DA38", ON_EVENT_BIT: "wwr_jeu_025B8B68",
                     ON_DUNGEON_ITEM: "wwr_jeu_025B9098"}
EV, EV2 = 0x644, 0x1178                     # dSv_event_c (bits d'événement) ; seconde table (grand-mère)
# bits d'événement posés à la création en monde ouvert (init_save_with_tweaks de TWWHD, dans son ordre)
BITS_OUVERT = (
    (EV, 0x3510), (EV, 0x2A80), (EV, 0x0280), (EV, 0x0520), (EV, 0x2E01), (EV, 0x0F80), (EV, 0x0908),
    (EV, 0x2A08), (EV, 0x0902), (EV, 0x1F40), (EV, 0x0380), (EV, 0x0A80), (EV, 0x0901), (EV, 0x0A20),
    (EV, 0x1801), (EV, 0x0A08), (EV, 0x0808), (EV, 0x1F02), (EV, 0x2F20), (EV, 0x3840), (EV, 0x2D04),
    (EV, 0x3802), (EV, 0x2D01), (EV, 0x2D02), (EV, 0x3201), (EV, 0x3380), (EV, 0x1001), (EV, 0x2E04),
    (EV, 0x2920), (EV, 0x1620), (EV, 0x3304), (EV, 0x2910), (EV, 0x1610), (EV, 0x3440), (EV, 0x1520),
    (EV, 0x1540), (EV, 0x3A20), (EV, 0x2D08), (EV, 0x3980), (EV, 0x3B02), (EV, 0x4002), (EV2, 0x0310))
# mots de mémoire posés à la création (stw de TWWHD : décalage dans dSv_info_c, valeur ; « Open DRC » coupé)
MOTS_OUVERT = ((0x530, 0x4018028B), (0x534, 0xC0000000), (0x3A4, 0x02000000), (0x3A8, 0x80000000),
               (0x3AC, 0x41010080), (0x410, 0x00100200), (0x414, 0x00000002), (0x418, 0x00000040),
               (0x438, 0x00400000), (0x45C, 0x00002000), (0x464, 0x00000008), (0x480, 0x00000400),
               (0x4E8, 0x00040040), (0x4C4, 0xF0042000))
# « cinématique du boss vue » (bit 5 de mDungeonItem) dans les cinq donjons : (décalage de leur dSv_memBit_c, bit)
DONJONS_OUVERT = tuple((0x3A0 + 0x24 * st, 5) for st in (3, 4, 5, 6, 7))
# octets posés : butin et appâts « déjà vus », jauge de magie (max et courante : 16, la petite jauge)
OCTETS_OUVERT = ((0xB4, 0xFF), (0xB5, 0xFF), (0x33, 0x10), (0x34, 0x10))
# objets donnés par execItemGet à la création en monde ouvert (voir l'en-tête : ce que l'histoire donnait)
# Bombes (0x31) : le coffre du bateau pirate de Mercantîle n'est pas atteignable en monde ouvert (le bateau n'y vient
# que pendant la nuit sans fin, que les bits de départ empêchent ; TWWHD l'ajoute par « add_pirate_ship_to_windfall »,
# qui exige l'acteur neuf SwOp et un événement neuf, non portés) : les bombes sont donc données au départ
OBJETS_OUVERT = (0x22, 0x6D, 0x78, 0x42, 0x38, 0x39, 0x3B, 0x24, 0x30, 0x50, 0x6A, 0x6B, 0x70, 0x71, 0x31)
NOMS_OBJETS_OUVERT = {0x22: "Baguette du Vent", 0x6D: "Requiem du Vent", 0x78: "voile", 0x42: "Talisman des pirates",
                      0x39: "Épée de légende (sans pouvoir)",
                      0x38: "Épée du héros", 0x3B: "Bouclier du héros", 0x24: "sac à butin", 0x30: "sac de facteur",
                      0x50: "flacon", 0x6A: "Perle de Din", 0x6B: "Perle de Farore",
                      0x70: "Hymne du Dieu de la Terre", 0x71: "Hymne du Dieu du Vent", 0x31: "bombes"}
# --- objets de départ mélangés (option « depart_melange », monde ouvert seulement ; jamais essayé en jeu) ---------------
# Les objets de départ que TWWHD met dans sa réserve d'objets (logic/ItemPool.cpp) et que l'on peut trouver hors de
# l'histoire sans rien casser : (nom court de l'option, objet du jeu, lieu d'histoire de scenario_ouvert.json).
# Preuves (RPX USA v0, lecture seule ; table item_func_ptr à 0x101E3A98) :
#  * perles 0x6A / 0x6B : item_func 0x0254F530 / 0x0254F544 = un seul saut vers 0x025B7CC0 (bit de la perle) ; le
#    fragment « cine_ouvert » (monde ouvert) remplace ce saut (sites 0x0254F540 / 0x0254F554) par
#    give_pearl_and_raise_totg_if_necessary de TWWHD : perle posée sur sa statue (bit 0x1480 / 0x1440) et Tour des Dieux
#    levée (0x1E40, 0x2E80) à la troisième perle, QUEL QUE SOIT celui qui donne la perle (coffre, crochet, lot 6) ;
#  * hymnes 0x70 / 0x71 : item_func 0x0254F594 / 0x0254F5A8 = un seul saut vers 0x025B7AA8 (bit du chant 3 / 4) :
#    aucun bit d'histoire, aucune condition ; les jouer exige la Baguette (logique TWWHD « Can Play EGL / WGA »,
#    gardée même quand la Baguette est à trouver) ; Médolie et Makar sont mis dans leur temple par les bits de départ
#    (BITS_OUVERT) ;
#  * bombes 0x31 : item_func 0x0254E888 pose la case (0x025B5CCC, case 13) puis met bombes ET capacité à 30
#    (0x0254E8C4 « stb r0, 0x8A(r12) », 0x0254E8CC « stb r0, 0x90(r12) ») : un sac de 60 d'une Grande Fée reçu avant
#    les bombes redescendrait à 30. Comme TWWHD (tweaks.cpp, « nop some code that sets max bombs and arrows to 30 » ;
#    custom_funcs.asm, init_save_with_tweaks : 30 bombes et capacité 30 posées à la création), le crochet de création
#    pose 30 / 30 et ces deux écritures sont effacées, SEULEMENT quand les bombes sont mélangées.
#  * Baguette 0x22 : item_func 0x0254E118 pose la case 2 (0x5E = 0x22) et son bouton dédié (mSelectItem[3], 0x2C),
#    comme quand le Lion Rouge la donne ; Requiem 0x6D : item_func 0x0254F558 = un seul saut vers 0x025B7AA8 (bit
#    du chant 0) ;
#    sac à butin 0x24 / sac de facteur 0x30 : item_func 0x0254E360 / 0x0254E840 posent la case (0x60 / 0x6E) et rien
#    d'autre ; un butin ou une lettre reçus AVANT le sac vont dans la mémoire du sac (0x96, 0xB0 : item_func 0x0254E030,
#    0x0254FDC0…) sans le regarder, et apparaissent avec lui ; flacon 0x50 : item_func 0x0254F2E8 = premier flacon
#    libre (0x025B5454). Aucun bit d'histoire. Le flacon est déjà dans la liste du pack (rando.PERMIS2) : il peut aller
#    dans un coffre ; les autres ne vont que dans un lieu du lot 6.
#  * naviguer sans Baguette ni Requiem (VARIANTE_VOILE) : la vitesse visée du bateau (0x0247E854, d_a_ship.cpp) est
#    55 x f(d) x min(2 x puissance du vent, 1), d = écart entre le cap et le vent (dKyw_get_wind_vec 0x0257DAA8) :
#    f = 1 - d/65536 vent arrière (d < 0x4000), 0,75 -> 0,30 de 90° à 135°, 0,30 -> 0 vers 140°, 0 au-delà (fsel à
#    0x0247EB70) : le bateau avance à toute allure SAUF face au vent (environ ±40°) ; on remonte le vent en tirant
#    des bords (à 135°, 30 % de la vitesse). Le vent ne change PAS tout seul : seul dKyw_tact_wind_set (0x0257E490 :
#    angles 0xA24/0xA26 et sauvegarde +0x4A/+0x4C) le tourne, appelé par le chef d'orchestre (0x022445D4, 0x02245BB8)
#    et par la voile rapide (0x0247E9FC, vent toujours arrière) ; la puissance (0xA18, niveaux 0,3/0,6/0,9) ne dépend
#    pas de la Baguette. Avec la Baguette ou le Requiem à trouver, la logique dit donc « Can Sail Away » = la voile
#    (TWWHD : Requiem + Baguette + voile, ou voile rapide) ; jouer un chant, un vent précis (Requiem : feuille Mojo
#    dehors, Canonnière, Grande roue, Tête de Pierre…), le Sanctuaire du Vent, Tott : exigences de TWWHD inchangées.
#  * bouclier du héros 0x3B (case « bouclier », AJOUTÉE À LA FIN ; exige l'option boucliers_progressifs) : item_func
#    0x0254EED4 appelle setSelectEquip(1, 0x3B) 0x02522398, qui écrit 0x3B dans le bouclier PORTÉ (+0x2F) sans
#    condition : reçu après le bouclier miroir, il le remplacerait, et le jeu n'a aucun menu pour remettre le miroir.
#    L'option boucliers_progressifs rend cette fonction progressive (CAVE_BOUCLIER, comme progressive_shield_item_func
#    de TWWHD) : il ne se mélange qu'avec elle.
# Gardés au départ, et pourquoi (le détail est dans docs/joueurs/RANDOMIZER.md) : la voile (décision : on ne navigue pas sans
# elle) ; le bouclier du héros SANS l'option boucliers_progressifs (voir plus haut) ; le Talisman
# des pirates (aucun objet de logique, TWWHD le donne toujours au départ et réutilise son icône ; cinq lecteurs du bit
# 0xD7.0 dans le code, menus et radio de Tetra, sans preuve qu'aucun ne compte) ; les épées (option sans_epee) ; la
# jauge de magie (posée par deux octets).
OPTION_DEPART = "depart_melange"
TYPE_DEPART = 6                     # entrée de table [6, case, 0, objet de départ, objet sans valeur reçu par la case]
# ordre FIXE : le numéro de case est la place dans ce tuple (entrées [6, case, …] des tables et chaînes d'avant) ; les
# objets ajoutés le sont À LA FIN
DEPART_MELANGEABLES = (
    ("bombes", 0x31, "Start - Bombs"),
    ("perle_din", 0x6A, "Start - Din's Pearl"),
    ("perle_farore", 0x6B, "Start - Farore's Pearl"),
    ("hymne_terre", 0x70, "Start - Earth God's Lyric"),
    ("hymne_vent", 0x71, "Start - Wind God's Aria"),
    ("baguette", 0x22, "Start - Wind Waker"),
    ("requiem", 0x6D, "Start - Wind's Requiem"),
    ("sac_butin", 0x24, "Start - Spoils Bag"),
    ("sac_facteur", 0x30, "Start - Delivery Bag"),
    ("flacon", 0x50, "Start - Empty Bottle"),
    ("bouclier", 0x3B, "Start - Hero's Shield"),        # exige l'option boucliers_progressifs
    # objets progressifs de TWWHD (option « progressifs_plus », rando/plus.py) : ces trois cases ne se cochent PAS dans
    # depart_melange, elles suivent leur famille (épée : Excalibur sans pouvoir du départ et le palier « pleine
    # puissance » que l'histoire donnait à la chambre de l'épée ; magie : la petite jauge, posée par deux octets)
    ("epee_legende", 0x39, "Start - Master Sword"),
    ("epee_pleine", 0x3A, "Story - Sword Chamber Full Power"),
    ("magie", 0xB1, "Start - Magic Meter"),
)
# cases qui suivent une famille de progressifs_plus (jamais dans la liste depart_melange)
CASES_PLUS = ("epee_legende", "epee_pleine", "magie")
# Baguette ou Requiem à trouver : variante de la logique (nom, macro de TWWHD, texte d'origine, texte de la variante)
VARIANTE_VOILE = ("voile_seule", "Can Sail Away", "(Can_Play_WR and Boats_Sail) or Swift_Sail",
                  "Boats_Sail or Swift_Sail")
DEPART_VENT = ("baguette", "requiem")
NOMS_DEPART = tuple(n for n, _o, _l in DEPART_MELANGEABLES if n not in CASES_PLUS)   # (cochables dans depart_melange)
ID_DEPART = {l: o for _n, o, l in DEPART_MELANGEABLES}          # lieu d'histoire -> objet du jeu
# ce que reçoit une case de départ vidée : un rubis tiré dans la réserve (vert, bleu, jaune, rouge, violet, orange,
# argenté). Il n'est PAS donné à la création (rien de prouvé pour un rubis donné avant le chargement d'une salle) : la
# partie a simplement un rubis de moins. Aucun objet de logique n'est un rubis.
SANS_VALEUR_DEPART = frozenset({0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x0F})
# ces objets sont hors de la liste des stubs du pack Mailbox (rando.PERMIS2) : ils ne vont que dans un lieu du lot 6
# (randomizer par fichiers), dont il faut au moins une famille qui sait les donner (pas les Grandes Fées, qui ne
# donnent que leurs paliers, ni les étals de Beedle, qui ne vendent que des objets de la liste)
FAMILLES_DEPART = ("tresors", "evenements", "boss", "sol", "pnj_code")
RAISON_BOUCLIER = ("le bouclier du héros reste au départ sans les boucliers progressifs (reçu après le bouclier "
                   "miroir, il le remplacerait : le jeu le met au bras sans condition)")
GARDES_DEPART = {"voile": "la voile reste au départ (on ne navigue pas sans elle)",
                 "talisman": "le Talisman des pirates reste au départ (aucun objet de logique ; TWWHD le donne toujours)",
                 "epee": "les épées restent au départ (pour s'en passer : option sans_epee)"}
# --- boucliers progressifs (option « boucliers_progressifs », coupée par défaut ; jamais essayée en jeu) ---------------
# Preuves (RPX USA v0, lecture seule ; table item_func_ptr à 0x101E3A98) :
#  * item_func 0x3B = 0x0254EED4 : onCollect(info+0xD4, 1, 0) (0x025B7944 : mCollect[1] |= 1, bouclier du héros) puis
#    setSelectEquip(1, 0x3B) ; item_func 0x3C = 0x0254EF14 : onCollect(info+0xD4, 1, 1) (mCollect[1] |= 2, bouclier
#    miroir) puis setSelectEquip(1, 0x3C). setSelectEquip (0x02522398) écrit l'objet dans mSelectEquip[1] (info+0x2F)
#    SANS le comparer à l'ancien : d'où la rétrogradation ;
#  * info = mot à 0x101F84DC (« lis r12, 0x1020 ; lwz r12, -31524(r12) » dans les deux fonctions) ; mCollect[1] est
#    donc l'octet info+0xD5 (wwmp/inventaire.COLLECT : 0x3B bit 0, 0x3C bit 1).
# Le crochet (pack WWMP_Rando GÉNÉRÉ, première instruction de l'item_func 0x3B, « mflr r0 » rejoué) : si mCollect[1]
# a déjà un bouclier (bit 0 ou 1), on continue dans l'item_func 0x3C (bouclier miroir) ; sinon dans celle d'origine
# (bouclier du héros). Le Ne bouclier reçu donne donc le palier N, et jamais un palier plus bas que celui qu'on a.
# Toutes les voies du jeu passent par cette fonction (execItemGet 0x0254DA38 saute par la table ; coffre, lieux du lot
# 6, crochet de création, grand-mère). L'item_func 0x3C (bouclier miroir) n'est PAS changée : le coffre du Mini-Boss
# Stalfos du Temple de la Terre (clé (6, 7) en conflit avec M_Dai, fixe) donne toujours le miroir, même s'il est
# ouvert le premier. Voie sûre, choisie exprès : (1) en coop, deux joueurs qui ouvrent les deux boucliers au même
# instant (avant le relais de l'inventaire) ne perdent jamais le miroir (le relais fait l'union des bits et monte
# l'équipement au plus haut, wwmp/inventaire.donner_collect) ; (2) le jeu donne toujours AU MOINS ce que la logique
# compte (« Mirror Shield » = deux « Progressive Shield ») ; (3) le miroir n'arrive jamais avant les portes 1 et 2 du
# Temple de la Terre (corrections « suppose_fixes » du scénario), puisque son coffre reste derrière elles et que le
# bouclier progressif ne donne le miroir qu'à qui a déjà un bouclier.
SITE_BOUCLIER, AVANT_BOUCLIER = 0x0254EED4, 0x7C0802A6          # mflr r0, rejoué par le crochet
SUITE_HEROS, SUITE_MIROIR = 0x0254EED8, 0x0254EF18             # après le « mflr r0 » des item_func 0x3B et 0x3C
SYMBOLES_BOUCLIER = {SUITE_HEROS: "wwr_jeu_0254EED8", SUITE_MIROIR: "wwr_jeu_0254EF18"}
COLLECT_BOUCLIER = 0xD5                                         # mCollect[1] dans dSv_info_c
# mots relus dans le RPX avant d'écrire quoi que ce soit (sinon refus) : table des item_func, début et cœur des deux
# fonctions
TEMOINS_BOUCLIER = ((0x101E3A98 + 4 * 0x3B, 0x0254EED4), (0x101E3A98 + 4 * 0x3C, 0x0254EF14),
                    (0x0254EED4, 0x7C0802A6), (0x0254EED8, 0x90010004), (0x0254EEE0, 0x3D801020),
                    (0x0254EEE4, 0x818C84DC), (0x0254EEF0, 0x386C00D4), (0x0254EEF8, 0x3880003B),
                    (0x0254EEFC, 0x38600001), (0x0254EF00, 0x4BFD3499),
                    (0x0254EF14, 0x7C0802A6), (0x0254EF18, 0x90010004), (0x0254EF20, 0x3D801020),
                    (0x0254EF28, 0x818C84DC), (0x0254EF30, 0x386C00D4), (0x0254EF38, 0x3880003C),
                    (0x0254EF3C, 0x38600001), (0x0254EF40, 0x4BFD3459))
CAVE_BOUCLIER = f"""\
wwr7_bouclier:
    lis r12, 0x1020
    lwz r12, -31524(r12)
    lbz r12, {COLLECT_BOUCLIER}(r12)
    andi. r12, r12, 3
    bne wwr7_bouclier_miroir
    mflr r0
    b wwr_jeu_0254EED8
wwr7_bouclier_miroir:
    mflr r0
    b wwr_jeu_0254EF18
"""


def boucliers_progressifs(options) -> bool:
    return isinstance(options, dict) and options.get(OPTION_BOUCLIER) is True


def releve_bouclier(options) -> str:
    """Empreinte des règles des boucliers progressifs ("" sans l'option) : site, suites, octet lu, témoins
    (rando_verif refait le même document)."""
    if not boucliers_progressifs(options):
        return ""
    doc = {"site": [SITE_BOUCLIER, AVANT_BOUCLIER], "suites": [SUITE_HEROS, SUITE_MIROIR],
           "collect": COLLECT_BOUCLIER, "paliers": [0x3B, 0x3C], "temoins": [list(x) for x in TEMOINS_BOUCLIER]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()[:16]


OCTETS_BOMBES = ((0x8A, 30), (0x90, 30))                    # bombes et capacité (custom_funcs.asm de TWWHD)
SITES_BOMBES = ((0x0254E8C4, 0x980C008A), (0x0254E8CC, 0x980C0090))   # item_func_bomb : écritures effacées


def departs_melanges(options) -> tuple:
    """Noms courts (ordre de DEPART_MELANGEABLES) des objets de départ que ces options mélangent ; () hors du monde
    ouvert ou option absente. Tolérant (ne contrôle rien : voir controler_options)."""
    if not ouvert(options):
        return ()
    from rando import plus as rando_plus
    v = options.get(OPTION_DEPART)
    v = list(v) if isinstance(v, (list, tuple)) else []
    v += rando_plus.cases_depart(options)          # objets progressifs de TWWHD : cases qui suivent leur famille
    return tuple(n for n, _o, _l in DEPART_MELANGEABLES if n in v)


def ids_depart_melanges(options) -> frozenset:
    return frozenset(o for n, o, _l in DEPART_MELANGEABLES if n in departs_melanges(options))


def voile_seule(options) -> bool:
    """La logique de cette partie est-elle la variante « la voile suffit pour naviguer » (Baguette ou Requiem à
    trouver, monde ouvert) ?"""
    return any(n in DEPART_VENT for n in departs_melanges(options))


def normaliser_depart(v) -> list:
    """Valeur normalisée de l'option ``depart_melange`` (liste triée dans l'ordre de DEPART_MELANGEABLES). Lève
    ErreurLot7 (un objet gardé au départ est refusé avec sa raison)."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
        raise ErreurLot7("option depart_melange : liste de noms attendue (" + ", ".join(NOMS_DEPART) + ")")
    for x in v:
        if x in GARDES_DEPART:
            raise ErreurLot7("option depart_melange : " + GARDES_DEPART[x])
        if x in CASES_PLUS:
            raise ErreurLot7(f"option depart_melange : {x!r} se règle par l'option progressifs_plus (épée ou magie "
                             "progressive)")
        if x not in NOMS_DEPART:
            raise ErreurLot7(f"option depart_melange : objet inconnu {x[:30]!r} (connus : " + ", ".join(NOMS_DEPART)
                             + ")")
    return [n for n in NOMS_DEPART if n in v]


def releve_depart(options) -> str:
    """Empreinte des règles des objets de départ mélangés de cette partie ("" si aucun) : cases, objets, objets sans
    valeur reçus, familles permises, octets et sites des bombes (rando_verif refait le même document)."""
    noms = departs_melanges(options)
    if not noms:
        return ""
    doc = {"type": TYPE_DEPART, "cases": [[n, o, l] for n, o, l in DEPART_MELANGEABLES if n in noms],
           "sans_valeur": sorted(SANS_VALEUR_DEPART), "familles": list(FAMILLES_DEPART),
           "bombes": [[list(x) for x in OCTETS_BOMBES], [list(x) for x in SITES_BOMBES]] if "bombes" in noms else None}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# départ (set_new_game_starting_location de TWWHD) : « li r10, salle » et « li r0, point » (deux copies)
SITES_DEPART = ((0x025B508C, 0x3940002C, "salle"), (0x025B5090, 0x380000CE, "point"),
                (0x025B50C8, 0x3940002C, "salle"), (0x025B50CC, 0x380000CE, "point"))

CAVE_CREATION = """\
wwr7_init:
    stwu r1, -32(r1)
    mflr r0
    stw r0, 36(r1)
    stw r31, 28(r1)
    lis r31, wwr7_objets@ha
    addi r31, r31, wwr7_objets@l
wwr7_init_objet:
    lbz r3, 0(r31)
    cmplwi r3, 0xFF
    beq wwr7_init_bits
    bl wwr_jeu_0254DA38
    addi r31, r31, 1
    b wwr7_init_objet
wwr7_init_bits:
    lis r31, wwr7_bits@ha
    addi r31, r31, wwr7_bits@l
wwr7_init_bit:
    lhz r5, 0(r31)
    cmpwi r5, 0
    beq wwr7_init_mots
    lhz r4, 2(r31)
    lis r3, 0x1020
    lwz r3, -31524(r3)
    add r3, r3, r5
    bl wwr_jeu_025B8B68
    addi r31, r31, 4
    b wwr7_init_bit
wwr7_init_mots:
    lis r31, wwr7_mots@ha
    addi r31, r31, wwr7_mots@l
wwr7_init_mot:
    lwz r5, 0(r31)
    cmpwi r5, 0
    beq wwr7_init_donjons
    lwz r6, 4(r31)
    lis r3, 0x1020
    lwz r3, -31524(r3)
    add r3, r3, r5
    stw r6, 0(r3)
    addi r31, r31, 8
    b wwr7_init_mot
wwr7_init_donjons:
    lis r31, wwr7_donjons@ha
    addi r31, r31, wwr7_donjons@l
wwr7_init_donjon:
    lhz r5, 0(r31)
    cmpwi r5, 0
    beq wwr7_init_octets
    lhz r4, 2(r31)
    lis r3, 0x1020
    lwz r3, -31524(r3)
    add r3, r3, r5
    bl wwr_jeu_025B9098
    addi r31, r31, 4
    b wwr7_init_donjon
wwr7_init_octets:
    lis r31, wwr7_octets@ha
    addi r31, r31, wwr7_octets@l
wwr7_init_octet:
    lhz r5, 0(r31)
    cmpwi r5, 0
    beq wwr7_init_fin
    lhz r6, 2(r31)
    lis r3, 0x1020
    lwz r3, -31524(r3)
    add r3, r3, r5
    stb r6, 0(r3)
    addi r31, r31, 4
    b wwr7_init_octet
wwr7_init_fin:
    lwz r31, 28(r1)
    lwz r0, 36(r1)
    mtlr r0
    addi r1, r1, 32
    lis r12, 0x1020
    blr
"""


def listes_creation(options) -> dict:
    """Ce que le crochet de création pose pour ces options : {objets, bits, mots, donjons, octets} (vide : pas de
    crochet)."""
    o = ouvert(options)
    bits = list(BITS_OUVERT) if o else []
    if "reine" in coffres_actifs(options) and (EV, BIT_REINE) not in bits:
        bits.append((EV, BIT_REINE))
    if isinstance(options, dict) and options.get("pas_de_recombats") is True:
        bits += [(EV, b) for b in BITS_RECOMBATS]
    objets = list(OBJETS_OUVERT) if o else []
    if o and isinstance(options, dict) and options.get("sans_epee") is True:
        # lot 8 (rando/lot8.py), sans épée : ni épée ni Excalibur au départ ; barrière d'Hyrule levée (ce que la
        # variable « swordless » de TWWHD faisait dans init_save_with_tweaks)
        from rando import lot8 as rando_lot8
        objets = [x for x in objets if x not in rando_lot8.OBJETS_EPEE]
        bits += [(EV, b) for b in rando_lot8.BITS_SANS_EPEE]
    octets = list(OCTETS_OUVERT) if o else []
    melanges = ids_depart_melanges(options)
    if melanges:
        # objets de départ mélangés : ils se trouvent dans le monde (lieux du lot 6), plus à la création ; bombes :
        # 30 bombes et capacité 30 posées ici, comme init_save_with_tweaks de TWWHD (voir SITES_BOMBES)
        objets = [x for x in objets if x not in melanges]
        if 0x31 in melanges:
            octets += list(OCTETS_BOMBES)
        if 0xB1 in melanges:
            # magie progressive (rando/plus.py) : plus de petite jauge au départ (ni max ni magie)
            octets = [x for x in octets if x not in ((0x33, 0x10), (0x34, 0x10))]
    out = {"objets": objets, "bits": bits, "mots": list(MOTS_OUVERT) if o else [],
           "donjons": list(DONJONS_OUVERT) if o else [], "octets": octets}
    if not any(out.values()):
        return {}
    return out


def _mots_octets(octets: bytes) -> list[str]:
    d = bytes(octets) + b"\0" * (-len(octets) % 4)
    return [f"    .int 0x{struct.unpack_from('>I', d, i)[0]:08X}" for i in range(0, len(d), 4)]


def tables_creation(listes: dict) -> list[str]:
    out = ["wwr7_objets:"] + _mots_octets(bytes(listes["objets"]) + b"\xff")
    out += ["wwr7_bits:"] + _mots_octets(b"".join(struct.pack(">HH", a, b) for a, b in listes["bits"]) + b"\0" * 4)
    out += ["wwr7_mots:"] + _mots_octets(b"".join(struct.pack(">II", a, b) for a, b in listes["mots"]) + b"\0" * 8)
    out += ["wwr7_donjons:"] + _mots_octets(b"".join(struct.pack(">HH", a, b) for a, b in listes["donjons"])
                                            + b"\0" * 4)
    out += ["wwr7_octets:"] + _mots_octets(b"".join(struct.pack(">HH", a, b) for a, b in listes["octets"])
                                           + b"\0" * 4)
    return out


def fragments_actifs(options) -> tuple:
    out = []
    if ouvert(options):
        out += ["ouvert", "cine_ouvert"]
    if isinstance(options, dict) and options.get("cinematiques_courtes") is True:
        out.append("cine_courtes")
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
    """Code du pack généré pour ces options : {symboles, cave, sites} (vides si le lot 7 n'en demande pas)."""
    sym, cave, sites = {}, [], []
    if not actif(options):
        return {"symboles": sym, "cave": cave, "sites": sites}

    def ajouter_symboles(d):
        for a, n in d.items():
            if sym.get(a, n) != n or (n in sym.values() and sym.get(a) != n):
                raise ErreurLot7(f"symbole {n} en double ou adresse 0x{a:08X} nommée deux fois")
            sym[a] = n

    cave.append("; --- lot 7 : code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT, "
                "Copyright (c) 2024 SuperDude88 (LICENSE_TWWHD.txt), et code WWMP ---")
    for nom in fragments_actifs(options):
        fr = lire_fragment(nom)
        ajouter_symboles(fr["symboles"])
        cave.append(f"; --- lot 7, fragment {nom} (TWWHD, MIT) ---")
        cave += fr["cave"]
        sites.append(f"; --- lot 7, fragment {nom} (TWWHD, MIT) ---")
        for l in fr["sites"]:
            m = re.match(r"(0x[0-9A-Fa-f]{8}) = .*twwhd avant=([0-9A-F]{8})", l)
            if not m or rpx.mot(int(m.group(1), 16)) != int(m.group(2), 16):
                raise ErreurLot7(f"fragment {nom} : mot d'origine inattendu ({l.strip()[:40]})")
            sites.append(l)
    listes = listes_creation(options)
    if listes:
        ajouter_symboles(SYMBOLES_CREATION)
        cave.append("; --- lot 7 : crochet de creation de partie (WWMP, d'apres init_save_with_tweaks de TWWHD) ---")
        cave += CAVE_CREATION.splitlines() + tables_creation(listes)
        if rpx.mot(SITE_CREATION) != AVANT_CREATION:
            raise ErreurLot7("crochet de création : mot d'origine inattendu à 0x025AECE4")
        sites.append("; --- lot 7 : crochet de creation ---")
        sites.append(f"0x{SITE_CREATION:08X} = bl wwr7_init                ; avant={AVANT_CREATION:08X} "
                     "(lis r12, 0x1020, rejoue a la fin)")
    if ouvert(options):
        salle = ILES[ile_depart(options)] if ile_depart(options) else SALLE_ORIGINE
        sites.append(f"; --- lot 7 : depart (salle {salle}, point 0) ---")
        for adr, avant, quoi in SITES_DEPART:
            if rpx.mot(adr) != avant:
                raise ErreurLot7(f"départ : mot d'origine inattendu à 0x{adr:08X}")
            reg = "r10" if quoi == "salle" else "r0"
            sites.append(f"0x{adr:08X} = li {reg}, {salle if quoi == 'salle' else 0}"
                         f"{' ' * 14}; avant={avant:08X}")
    if 0x31 in ids_depart_melanges(options):
        # bombes mélangées : item_func_bomb ne remet plus bombes et capacité à 30 (tweaks.cpp de TWWHD)
        sites.append("; --- lot 7 : bombes melangees (item_func_bomb sans ses deux ecritures, comme TWWHD) ---")
        for adr, avant in SITES_BOMBES:
            if rpx.mot(adr) != avant:
                raise ErreurLot7(f"bombes : mot d'origine inattendu à 0x{adr:08X}")
            sites.append(f"0x{adr:08X} = ori r0, r0, 0                ; avant={avant:08X}")
    if boucliers_progressifs(options):
        # boucliers progressifs : l'item_func du bouclier du héros donne le miroir à qui a déjà un bouclier (WWMP)
        for adr, avant in TEMOINS_BOUCLIER:
            if rpx.mot(adr) != avant:
                raise ErreurLot7(f"boucliers progressifs : mot d'origine inattendu à 0x{adr:08X}")
        ajouter_symboles(SYMBOLES_BOUCLIER)
        cave.append("; --- lot 7 : bouclier progressif (WWMP) : un bouclier deja la -> bouclier miroir ---")
        cave += CAVE_BOUCLIER.splitlines()
        sites.append("; --- lot 7 : bouclier progressif ---")
        sites.append(f"0x{SITE_BOUCLIER:08X} = b wwr7_bouclier            ; avant={AVANT_BOUCLIER:08X} "
                     "(mflr r0, rejoue)")
    adrs =[int(l.split(" ", 1)[0], 16) for l in sites if l.startswith("0x")]
    if len(adrs) != len(set(adrs)):
        raise ErreurLot7("deux lignes du lot 7 écrivent la même adresse")
    if not adrs:                         # options du lot 7 sans code (textes seuls) : rien dans le pack
        return {"symboles": {}, "cave": [], "sites": []}
    return {"symboles": sym, "cave": cave, "sites": sites}


ENTETE_FRAGMENT = (
    "; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,\n"
    "; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.\n"
    "; Converti par tools/wwmp/rando_lot7.py (--porter) avec tools/wwmp/rando_port_twwhd.py.\n"
    "; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.\n")


def porter(source: str, rpx, packs: dict) -> dict:
    """{nom: texte} des fragments du lot 7 depuis la copie de TWWHD ``source`` (outil de développement)."""
    from rando import port_twwhd as p
    src = p.Source.depuis_dossier(source)
    out = {}
    for nom, sites in GROUPES_TWWHD.items():
        prefixe = f"wwr7_{nom}_"
        controle = p.convertir(src, sites, rpx, packs, prefixe=prefixe)
        if not controle.propre:
            raise ErreurLot7(f"fragment {nom} : " + "; ".join(controle.refus + controle.collisions))
        c = p.convertir(src, sites, rpx, {}, prefixe=prefixe)
        if not c.propre or c.donnees:
            raise ErreurLot7(f"fragment {nom} : conversion différente sans les packs, ou donnée inattendue")
        t = [ENTETE_FRAGMENT, "; [symboles]"]
        t += [f"0x{a:08X} = {n}:" for a, n in sorted(c.symboles.items())]
        t += ["; [cave]"] + c.cave + ["; [sites]"]
        t += [f"0x{a:08X} = {txt:<28} ; twwhd avant={av:08X} apres={ap:08X}" for a, av, ap, txt in c.sites]
        out[nom] = ("\n".join(t) + "\n").replace(prefixe + "jeu_", "wwr_jeu_")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Textes (text_replacements.cpp et tweaks.cpp de TWWHD), trois langues
# ---------------------------------------------------------------------------------------------------------------
LANGUES = ("English", "French", "Spanish")
# lieux du lot 6 que les textes nomment (les autres lieux nommés sont des lieux « donnée » du lot 1)
LIEUX_TEXTES_LOT6 = ("Great Sea - Beedle Shop 20 Rupee Item", "Rock Spire Isle - Beedle 900 Rupee Item",
                     "Windfall Island - Battle Squid First Prize", "Windfall Island - Battle Squid Second Prize")
_NOMS = None


def noms() -> dict:
    """{id: {langue: {nom, joli, cryptique, genre, pluriel}}} relus de rando_logique/noms_objets.json."""
    global _NOMS
    if _NOMS is None:
        with open(FICHIER_NOMS, encoding="utf-8") as f:
            doc = json.load(f)
        _NOMS = {int(k, 16): v for k, v in doc["objets"].items()}
    return _NOMS


def chemin_message(langue: str, n: str) -> str:
    return (f"content/Common/Pack/permanent_2d_Us{langue}.pack@SARC@message{n}_msbt.szs@YAZ0@SARC@"
            f"message{n}.msbt@MSBT")


FICHIERS_MESSAGES = ("", "2", "3", "4")
VOYELLES_FR = frozenset("aeiouàâæèéêëôœîïùûü")


def _couleur(T, texte: str, couleur: int):
    """apply_name_color de TWWHD : barres « | » alternées couleur / défaut ; sans barre, tout en couleur."""
    if "|" not in texte:
        return T.couleur(couleur).t(texte).defaut()
    on = False
    for i, morceau in enumerate(texte.split("|")):
        if i:
            on = not on
            if on:
                T.couleur(couleur)
            else:
                T.defaut()
        if morceau:
            T.t(morceau)
    return T


def nom_objet(objet: int, langue: str, genre: str = "nom") -> str:
    n = noms().get(objet, {}).get(langue)
    if not n or not n.get(genre if genre != "nom" else "nom"):
        raise ErreurLot7(f"textes : nom inconnu pour l'objet 0x{objet:02X} ({langue})")
    return n[genre]


def _envelopper(segments: list, largeur: int) -> list:
    """word_wrap_string de TWWHD sur une liste de segments (texte, ou code de contrôle opaque ``("code", octets)``)
    : un retour à la ligne avant le mot qui dépasse ``largeur`` caractères visibles."""
    out, mot, lg_mot, lg_ligne = [], [], 0, 0
    for seg in segments:
        if isinstance(seg, tuple):
            mot.append(seg)
            continue
        for ch in seg:
            if ch == "\n":
                out += mot + ["\n"]
                mot, lg_mot, lg_ligne = [], 0, 0
            elif ch == " ":
                out += mot + [" "]
                lg_ligne += lg_mot + 1
                mot, lg_mot = [], 0
            else:
                mot.append(ch)
                lg_mot += 1
                if lg_ligne + lg_mot > largeur:
                    out.append("\n")
                    lg_ligne = 0
                    if lg_mot > largeur:
                        out += mot + ["\n"]
                        mot = []
    out += mot
    return out


class _Seg:
    """Message en segments (texte et codes opaques), pour l'enveloppement de TWWHD."""

    def __init__(self):
        self.s = []

    def t(self, x: str):
        self.s.append(x)
        return self

    def code(self, groupe, type_, param=b""):
        self.s.append(("code", struct.pack(">HHHH", 0x0E, groupe, type_, len(param)) + param))
        return self

    def couleur(self, c):
        return self.code(0, 3, struct.pack(">H", c))

    def defaut(self):
        return self.code(0, 3, b"\xff\xff").code(0, 3, b"\0\0")

    def nom(self, objet, langue, genre="nom", couleur=1):
        n = noms()[objet][langue][genre]
        _couleur(self, n, couleur)
        return self

    def envelopper(self, largeur):
        e = _Seg()
        e.s = _envelopper(self.s, largeur)
        return e

    def ajouter(self, autre):
        self.s += autre.s
        return self

    def octets(self, fin=b"\0\0") -> bytes:
        d = bytearray()
        for x in self.s:
            d += x[1] if isinstance(x, tuple) else x.encode("utf-16-be")
        return bytes(d) + fin

    def lignes4(self):
        """pad_str_4_lines : complète jusqu'à un multiple de quatre lignes."""
        n = sum(x.count("\n") for x in self.s if isinstance(x, str)) + 1
        self.s.append("\n" * ((-n) % 4))
        return self


FIN_BOUTIQUE = struct.pack(">HHHH", 0x0E, 0, 3, 2) + b"\xff\xff" + b"\0\0"
FIN = b"\0\0"


def _defaut_seg():
    return ("code", struct.pack(">HHHH", 0x0E, 0, 3, 2) + b"\xff\xff" + struct.pack(">HHHH", 0x0E, 0, 3, 2) + b"\0\0")


def _rouge():
    return ("code", struct.pack(">HHHH", 0x0E, 0, 3, 2) + b"\0\x01")


def _choix2():
    return ("code", struct.pack(">HHHH", 0x0E, 1, 7, 0))


def _maj():
    return ("code", struct.pack(">HHHH", 0x0E, 1, 0xB, 0))


def _nomseg(objet, langue, genre="nom"):
    s = _Seg().nom(objet, langue, genre)
    return s.s


def _demonstratif_es(objet):
    n = noms()[objet]["Spanish"]
    m, p = n.get("genre") == "Male", n.get("pluriel") == "Plural"
    if n.get("genre") not in ("Male", "Female"):
        return ""
    return {(True, False): "este ", (True, True): "estos ", (False, False): "esta ", (False, True): "estas "}[(m, p)]


def _pluriel(objet, langue):
    return noms()[objet][langue].get("pluriel") == "Plural"


def _prep_fr(objet):
    j = noms()[objet]["French"]["joli"].replace("|", "")
    return "d'" if j[:1].lower() in VOYELLES_FR else "de "


def textes_lieux(objets: dict, origine: dict) -> dict:
    """{étiquette: {langue: octets}} des messages qui nomment l'objet d'un lieu CHANGÉ par la table.

    ``objets`` : {nom du lieu: objet de la table} ; ``origine`` : {nom du lieu: objet d'origine}. Un message n'est
    réécrit que si au moins un de ses lieux change (sinon : texte d'origine)."""
    def change(*lieux):
        return any(objets.get(l, origine.get(l)) != origine.get(l) for l in lieux)

    def o(l):
        return objets.get(l, origine[l])
    out = {}
    B20, B500, B950, B900 = ("Great Sea - Beedle Shop 20 Rupee Item", "Rock Spire Isle - Beedle 500 Rupee Item",
                             "Rock Spire Isle - Beedle 950 Rupee Item", "Rock Spire Isle - Beedle 900 Rupee Item")
    A = {k: f"Windfall Island - Auction {k} Rupee" for k in (5, 40, 60, 80, 100)}
    if B20 in origine and change(B20):
        b = o(B20)
        for lg, prix in (("English", "  20 Rupees"), ("French", "  20 Rubis"), ("Spanish", "  20 Rupias")):
            s = _Seg()
            s.s.append(_rouge())
            s.s += _nomseg(b, lg)
            s.t(prix)
            s.s.append(_defaut_seg())
            out.setdefault("03906", {})[lg] = s.octets()
            q = {"English": ("  20 Rupees\nWill you buy it?\n", "I'll buy it\nNo thanks"),
                 "French": ("  20 Rubis\nAcheter?\n", "Oui\nSans façon"),
                 "Spanish": ("  20 Rupias\n¿Te lo llevas?\n", "Sí\nNo")}[lg]
            s = _Seg()
            s.s += _nomseg(b, lg)
            s.t(q[0])
            s.s.append(_choix2())
            s.t(q[1])
            out.setdefault("03909", {})[lg] = s.octets()
    for lieu, prix, e1, e2, dernier in ((B500, 500, "12106", "12109", 0), (B950, 950, "12107", "12110", 1),
                                        (B900, 900, "12108", "12111", 2)):
        if lieu not in origine or not change(lieu):
            continue
        b = o(lieu)
        for lg in LANGUES:
            s = _Seg()
            s.s.append(_rouge())
            s.s += _nomseg(b, lg)
            s.t({"English": f"  {prix} Rupees", "Spanish": f"  {prix} Rupias", "French": f".  {prix} Rubis."}[lg])
            s.s.append(_defaut_seg())
            s = s.envelopper(43)
            if dernier == 0:
                s.t({"English": "\nThis is my last one.", "Spanish": "\n¡Queda solo una!",
                     "French": "\nIl ne me reste que celui-ci!"}[lg])
            elif dernier == 1:
                s.t({"English": "\nThis is my last one of these, too.",
                     "Spanish": "\nTambién es el único que me queda.",
                     "French": "\nLà encore, il ne m'en reste juste un."}[lg])
            else:
                if lg == "French":
                    s.t("\nC'est ")
                    s.s += _nomseg(b, lg)
                    s.t(", n'est-ce pas...\nDonc quel que soit son prix,\nvous parviendrez à l'amortir...")
                else:
                    s.t({"English": "\nThe price may be high, but it'll pay\noff handsomely in the end!",
                         "Spanish": "\nParece muy caro, pero ¡créeme que no te arrepentirás!"}[lg])
            out.setdefault(e1, {})[lg] = s.octets(FIN_BOUTIQUE)
            s = _Seg()
            if lg == "English":
                s.t("These " if _pluriel(b, lg) else "This ")
                s.s += _nomseg(b, lg)
                s.t((" are " if _pluriel(b, lg) else " is ") + {0: "a mere ", 1: "only ", 2: "just "}[dernier])
                s.s.append(_rouge())
                s.t(f"{prix} Rupees")
                s.s.append(_defaut_seg())
                s = s.envelopper(43)
                s.t("\nBuy it! Buy it! Buy buy buy!\n")
                s.s.append(_choix2())
                s.t("I'll buy it\nNo thanks")
            elif lg == "Spanish":
                s.s.append(_maj())
                s.t(_demonstratif_es(b))
                s.s += _nomseg(b, lg)
                s.s.append(_defaut_seg())
                if dernier == 0:
                    s.t(" - Solo ")
                else:
                    s.t(" cuestan " if _pluriel(b, lg) else " cuesta ")
                s.s.append(_rouge())
                s.t(f"{prix} Rupias")
                s.s.append(_defaut_seg())
                s = s.envelopper(43)
                s.t("\n¡Cómprala sin pensarlo dos veces!\n" if dernier == 0 else "\nCompra... ¡Compra!\n")
                s.s.append(_choix2())
                s.t("Lo quiero\nNo, gracias")
            else:
                s.s += _nomseg(b, lg)
                s.t(" seulement ")
                s.s.append(_rouge())
                s.t(f"{prix} Rubis")
                s.s.append(_defaut_seg())
                s = s.envelopper(43)
                s.t(".\nAcheter!\n")
                s.s.append(_choix2())
                s.t("Oui\nSans façon")
            out.setdefault(e2, {})[lg] = s.octets(FIN_BOUTIQUE)
    if all(l in origine for l in (B500, B950, B900)) and change(B500, B950, B900):
        b5, b9, b0 = o(B500), o(B950), o(B900)
        for lg in LANGUES:
            s = _Seg()
            if lg == "English":
                s.t("  I'm sorry to disturb you with this\n  unsolicited letter. If the following does\n  not interest "
                    "you, please throw the letter\n  away without a second thought.\n\n  BUT THIS IS YOUR BIG "
                    "CHANCE!!!\n\n\n")
                c = _Seg().t("Do you have need of ")
                c.s += _nomseg(b5, lg, "joli")
                c.t(", ")
                c.s += _nomseg(b9, lg, "joli")
                c.t(", or ")
                c.s += _nomseg(b0, lg, "joli")
                c.t("? We have them at special bargain prices.")
                s.ajouter(c.envelopper(39).lignes4())
                s.t("\n  BUT WE HAVE ONLY ONE OF EACH!\n\n\n\n  If you're interested, go to the Shop Ship\n  near ")
                s.s.append(_rouge())
                s.t("Rock Spire Island")
                s.s.append(_defaut_seg())
                s.t(". First come,\n  first served! I can't wait to serve you!\n\n  To those who took the time to "
                    "read this\n  letter...please accept my humble thanks.\n     Asst. Manager, Rock Spire Shop Ship")
            elif lg == "Spanish":
                s.t("  Le pedimos disculpas si esta\n  comunicación no es de su interés.\n  Si es así, por favor, "
                    "ignore esta carta.\n\n\n¡Gran oferta exclusiva!\n\n\n  - ")
                s.s += _nomseg(b5, lg)
                s.t("\n  - ")
                s.s += _nomseg(b9, lg)
                s.t("\n  - ")
                s.s += _nomseg(b0, lg)
                s.t("\n\n  Tres artículos de gran valor, a precios\n  de promoción.\n\n\n  * Solo disponemos de uno de "
                    "cada tipo.\n\n\n\n  Lo esperamos en las proximidades\n  de la ")
                s.s.append(_rouge())
                s.t("Isla de los Pilares")
                s.s.append(_defaut_seg())
                s.t(".\n  ¡No deje escapar esta oportunidad!\n\n  Le agradecemos que se haya tomado\n  el tiempo de "
                    "leer esta carta.\n     Jefe suplente\n     Sucursal Isla de los Pilares")
            else:
                s.t("  Cher Client,\n  Excusez-nous pour ce courrier\n  inattendu. N'hésitez pas à le\n  détruire s'il "
                    "vous importune.\n\n  Voici une chance unique, pour vous seul!\n\n\n")
                c = _Seg().t("Besoin " + _prep_fr(b5))
                c.s += _nomseg(b5, lg, "joli")
                c.t(", " + _prep_fr(b9))
                c.s += _nomseg(b9, lg, "joli")
                c.t(", " + _prep_fr(b0))
                c.s += _nomseg(b0, lg, "joli")
                c.t("? Nous vous en proposons à des prix exceptionnels!")
                s.ajouter(c.envelopper(39).lignes4())
                s.t("\n  Mais attention : nous n'avons qu'un\n  seul article de chaque sorte!\n\n  Si vous êtes "
                    "intéressé, n'hésitez pas à\n  venir acheter ces articles dans notre\n  nouvelle boutique, à l'")
                s.s.append(_rouge())
                s.t("île de la Rocaille")
                s.s.append(_defaut_seg())
                s.t(".\n\n  Nous serons très heureux de vous\n  accueillir.\n\n\n  Nous tenons à vous offrir ce petit "
                    "cadeau\n  pour vous remercier d'avoir lu ce courrier.\n  Fidèlement vôtre!\n    Boutique Terry de "
                    "l'Ile de la Rocaille")
            out.setdefault("03325", {})[lg] = s.octets(FIN_BOUTIQUE)
    # (étape 5 : l'enchère à 100 rubis, « 07444 », ne change qu'avec l'option lieux_encheres_100)
    for k, e in ((40, "07440"), (5, "07441"), (60, "07442"), (80, "07443"), (100, "07444")):
        if A[k] in origine and change(A[k]):
            for lg in LANGUES:
                s = _Seg()
                if lg != "Spanish":
                    s.s.append(_maj())
                s.s += _nomseg(o(A[k]), lg, "joli")
                out.setdefault(e, {})[lg] = s.octets()
    if all(A[k] in origine for k in (5, 40, 60, 80, 100)) and change(A[5], A[40], A[60], A[80], A[100]):
        for lg in LANGUES:
            s = _Seg()
            if lg == "English":
                s.s.append(_defaut_seg())
                s.t("Notice: Windfall Auction Tonight!")
                s.s.append(_defaut_seg())
                s.t("\nBidding starts at dusk. All comers welcome!\nParticipate for the chance to win")
                s.s.append(_defaut_seg())
                s.t("\n\n")
                fin = (",\n", ",\n", ",\n\n\n", ", and\n", "!")
            elif lg == "Spanish":
                s.s.append(_rouge())
                s.t("¡Aviso! Gran subasta de Taura")
                s.s.append(_defaut_seg())
                s.t("\nHorario: Tras la puesta del sol\n¡Esperamos su visita!\n¡Participa por una oportunidad de "
                    "ganar")
                s.s.append(_defaut_seg())
                s.t("\n")
                n100 = noms()[o(A[60])]["Spanish"]["joli"].replace("|", "")
                fin = (",\n", ",\n", ",\n\n\n", "," + ("e " if n100[:1] in "iI" else "y ") + "\n", "!")
            else:
                s.s.append(_defaut_seg())
                s.t("Avis: Enchères de Mercantîle.")
                s.s.append(_defaut_seg())
                s.t("\nLes enchères débuteront à la nuit tombée.")
                s.s.append(_defaut_seg())
                s.t("\nVous y êtes cordialement invités.\nParticipez pour une chance de gagner")
                s.s.append(_defaut_seg())
                s.t("\n")
                fin = (",\n", ",\n", ",\n\n\n", ", et\n", "!")
            for k, f in zip((5, 40, 60, 80, 100), fin):
                s.s += _nomseg(o(A[k]), lg, "joli")
                s.t(f)
            out.setdefault("00804", {})[lg] = s.octets()
    P1, P2 = "Windfall Island - Battle Squid First Prize", "Windfall Island - Battle Squid Second Prize"
    if P1 in origine and change(P1):
        p = o(P1)
        for lg in LANGUES:
            s = _Seg().code(4, 0x8E)
            if lg == "English":
                s.t("Hoorayyy! Yayyy!\nOh, thank you, Mr. Sailer!\n\n\n")
                c = _Seg().t("Please take this ")
                c.s += _nomseg(p, lg)
                c.t(" as a sign of our gratitude. You are soooooo GREAT!")
            elif lg == "Spanish":
                s.t("¡Viva, viva!\n¡Gracias, señor, gracias!\n\n\n")
                c = _Seg().t("Por favor, " + _demonstratif_es(p))
                c.s += _nomseg(p, lg)
                c.t(" como muestra de gratitud.")
            else:
                s.t("Oui, oui!\nMerci l'ami!\n\n\n")
                c = _Seg().t("Nous tenons à te faire un cadeau pour te remercier de nous avoir protégés. Voici ")
                c.s += _nomseg(p, lg, "joli")
                c.t(", prends-le!")
            s.ajouter(c.envelopper(43))
            out.setdefault("07520", {})[lg] = s.octets()
    if P2 in origine and change(P2):
        p = o(P2)
        coeur = [("code", struct.pack(">HHHH", 0x0E, 0, 3, 2) + b"\0\x01"),
                 ("code", struct.pack(">HHHH", 0x0E, 3, 21, 0)), _defaut_seg()]
        for lg in LANGUES:
            s = _Seg().code(4, 0x8E)
            if lg == "English":
                s.t("Hoorayyy! Yayyy!\nOh, thank you so much, Mr. Sailer!\n\n\n")
                c = _Seg().t("This is our thanks to you! It's been passed down on our island for many years, so don't "
                             "tell the island elder, OK? Here... ")
                c.s += coeur
                c.t("Please accept this ")
                c.s += _nomseg(p, lg)
                c.t("!")
                s.ajouter(c.envelopper(43))
            elif lg == "Spanish":
                s.t("¡Viva, viva!\n¡Gracias, señor, gracias!\n\n\n")
                c = _Seg().t("Esta es nuestra muestra de gratitud. Ha sido pasado de generación en generación, así "
                             "que no le digas a los ancianos de la isla, está bien? ¡Por favor!")
                c.s += coeur
                c.t("¡Acepta " + _demonstratif_es(p))
                c.s += _nomseg(p, lg)
                c.t("!")
                s.ajouter(c.envelopper(43))
            else:
                s.t("Oui, oui!\nMerci l'ami!\n\n\nNous tenons à te faire un cadeau pour\nte remercier de nous avoir "
                    "protégés.\n\n\n")
                c = _Seg().t("Voici ")
                c.s += _nomseg(p, lg, "joli")
                c.t(" qui se transmet depuis fort longtemps sur cette île. Prends-là et surtout ne le dis pas aux "
                    "anciens!")
                s.ajouter(c.envelopper(43))
            out.setdefault("07521", {})[lg] = s.octets()
    return out


# numéro de carte dans le message « Vous obtenez … ! » (add_chart_number_to_item_get_messages de TWWHD) : début du
# nom dans le message, en caractères UTF-16 (code d'affichage immédiat compris)
DEBUT_NOM_CARTE = {"English": 12, "Spanish": 18, "French": 17}
CARTES = tuple(i for i in range(0xCC, 0xFF) if i not in (0xDB, 0xDC))
# capacités (make_item_get_text_dynamic de TWWHD) : (étiquette, texte d'origine par langue, remplacement)
R_BOMBES, R_FLECHES = 22, 23


def _brut(s: str) -> bytes:
    return s.encode("utf-16-be")


def _code(g, t, p=b""):
    return struct.pack(">HHHH", 0x0E, g, t, len(p)) + p


ROUGE_B = _code(0, 3, b"\0\x01")
DEFAUT_B = _code(0, 3, b"\xff\xff") + _code(0, 3, b"\0\0")
CAPACITES = {
    "00140": {"English": (_brut("30 arrows"), _code(2, R_FLECHES)),
              "Spanish": (_brut("30 flechas"), _code(2, R_FLECHES)),
              "French": (_brut("30 flèches"), _code(2, R_FLECHES))},
    "00150": {"English": (ROUGE_B + _brut("30") + DEFAUT_B, ROUGE_B + _code(2, R_BOMBES) + DEFAUT_B),
              "Spanish": (ROUGE_B + _brut("30") + DEFAUT_B, ROUGE_B + _code(2, R_BOMBES) + DEFAUT_B),
              "French": (ROUGE_B + _brut("30 ") + DEFAUT_B + _brut("bombes"), ROUGE_B + _code(2, R_BOMBES) + DEFAUT_B)},
}


def _texte_couleur(objet: int, langue: str, genre: str = "joli") -> bytes:
    s = _Seg()
    s.s += _nomseg(objet, langue, genre)
    return s.octets(b"")


def editions_messages_fixes(m, langue: str, etiquette: str) -> bytes | None:
    """Nouveau contenu d'un message « fixe » (indépendant de la table) de ``m`` (Msbt), ou None."""
    b = m.brut_texte(etiquette)
    if etiquette in CAPACITES:
        avant, apres = CAPACITES[etiquette][langue]
        if b.count(avant) != 1:
            raise ErreurLot7(f"textes : {etiquette} ({langue}) ne contient pas le texte attendu")
        return b.replace(avant, apres)
    if etiquette == "00277" and langue == "Spanish":
        if b.count(_brut("bombas")) != 1:
            raise ErreurLot7("textes : 00277 (espagnol) inattendu")
        return b.replace(_brut("bombas"), _brut("flechas"))
    if etiquette == "05290" and langue == "English":
        return (_brut("The effects of the tune you conducted can\nonly be seen in places where the ") + ROUGE_B
                + _brut("sun ") + DEFAUT_B + _brut("and\n") + ROUGE_B + _brut("moon") + DEFAUT_B
                + _brut(" are visible.") + b"\0\0")
    if etiquette.isdigit() and int(etiquette) - 101 in CARTES:
        objet = int(etiquette) - 101
        debut = 2 * DEBUT_NOM_CARTE[langue]
        fin = b.find(_brut("!"), debut)
        while fin >= 0 and fin % 2:
            fin = b.find(_brut("!"), fin + 1)
        if fin < 0:
            raise ErreurLot7(f"textes : {etiquette} ({langue}) sans « ! »")
        return b[:debut] + _texte_couleur(objet, langue) + b[fin:]
    return None


ETIQUETTES_FIXES = tuple(["00140", "00150", "00277", "05290"] + [f"{101 + i:05d}" for i in CARTES])


def editions_textes(objets: dict, origine: dict) -> list[tuple]:
    """Éditions de textes : [(langue, étiquette, ("fixe", None) | ("lieu", octets))], ordre fixe."""
    out = []
    lieux = textes_lieux(objets, origine)
    for lg in LANGUES:
        for et in ETIQUETTES_FIXES:
            if et == "00277" and lg != "Spanish" or et == "05290" and lg != "English":
                continue
            out.append((lg, et, ("fixe", None)))
        for et in sorted(lieux):
            out.append((lg, et, ("lieu", lieux[et][lg])))
    return out


def appliquer_textes(session, editions: list) -> None:
    """Applique les éditions de textes : chaque étiquette est cherchée dans les quatre fichiers de messages de sa
    langue (elle doit être dans exactement un)."""
    for lg, et, (genre, octets) in editions:
        trouves = []
        for n in FICHIERS_MESSAGES:
            m = session.ouvrir(chemin_message(lg, n))
            if et in m.etiquettes:
                trouves.append(m)
        if len(trouves) > 1:
            # étape 5 : « 07444 » (enchère à 100 rubis) est aussi, VIDE, dans message4 ; on écrit celui qui a un texte
            trouves = [m for m in trouves if m.texte(et)]
        if len(trouves) != 1:
            raise ErreurLot7(f"textes : étiquette {et} ({lg}) dans {len(trouves)} fichiers au lieu d'un")
        m = trouves[0]
        nouveau = editions_messages_fixes(m, lg, et) if genre == "fixe" else octets
        if nouveau is None:
            raise ErreurLot7(f"textes : rien à écrire pour {et} ({lg})")
        m.remplacer(et, nouveau)


# ---------------------------------------------------------------------------------------------------------------
# Outil de développement
# ---------------------------------------------------------------------------------------------------------------
def noms_depuis_twwhd(chemin_yaml: str) -> dict:
    """Noms d'objets de TWWHD (logic/data/item_data.yaml) -> document de noms_objets.json."""
    with open(chemin_yaml, encoding="utf-8") as f:
        t = f.read()
    objets = {}
    for bloc in re.split(r"(?m)^- ", t)[1:]:
        sec, d = None, {}
        for l in bloc.splitlines():
            m = re.match(r"^\s*(Names|Pretty Names|Cryptic Names|Gender|Plurality):\s*$", l)
            if m:
                sec = m.group(1)
                continue
            m = re.match(r"^\s{2,}(English|Spanish|French):\s*(.*)$", l)
            if m and sec:
                v = m.group(2).strip()
                if v.startswith('"') and v.count('"') >= 2:
                    v = v[1:v.index('"', 1)]
                else:
                    v = v.split(" #", 1)[0].strip()          # commentaire YAML en fin de ligne
                d.setdefault(m.group(1), {})[sec] = v
                continue
            m = re.match(r"^\s*Game Item Id:\s*(0x[0-9A-Fa-f]+)", l)
            if m:
                d["id"] = int(m.group(1), 16)
            if re.match(r"^\s*(Small Key Dungeon|Big Key Dungeon|Map Dungeon|Compass Dungeon):", l):
                d["donjon"] = True
        if "id" not in d or d.get("donjon"):
            continue
        cle = f"0x{d['id']:02X}"
        if cle in objets or not d.get("English", {}).get("Names"):
            continue
        objets[cle] = {lg: {"nom": d[lg].get("Names", ""), "joli": d[lg].get("Pretty Names", d[lg].get("Names", "")),
                            "cryptique": d[lg].get("Cryptic Names", ""), "genre": d[lg].get("Gender", ""),
                            "pluriel": d[lg].get("Plurality", "")}
                       for lg in LANGUES if lg in d}
    return {"description": "Noms des objets en anglais, français et espagnol, recopiés de TWWHD-Randomizer "
                           "(logic/data/item_data.yaml ; SuperDude88 et contributeurs, licence MIT, Copyright (c) 2024 "
                           "SuperDude88 : LICENSE_TWWHD.txt). Sert aux textes du lot 7 (wwmp/rando_lot7.py). Produit par "
                           "« python tools/wwmp/rando_lot7.py --noms <item_data.yaml> » ; ne pas éditer.",
            "objets": dict(sorted(objets.items()))}


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Lot 7 du randomizer par fichiers : outil de développement")
    ap.add_argument("--porter", metavar="TWWHD", help="reconvertit les fragments depuis une copie de TWWHD-Randomizer")
    ap.add_argument("--rpx", help="cking.rpx d'origine (lu seulement)")
    ap.add_argument("--noms", metavar="ITEM_DATA_YAML", help="refait rando_logique/noms_objets.json")
    a = ap.parse_args(argv)
    if a.noms:
        doc = noms_depuis_twwhd(a.noms)
        with open(FICHIER_NOMS, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        print(f"{len(doc['objets'])} objets écrits dans {FICHIER_NOMS}", file=sys.stderr)
        return 0
    if not a.porter or not a.rpx:
        ap.error("--porter et --rpx (ou --noms) sont exigés")
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


if __name__ == "__main__":             # outil de développement : cd tools && python -m rando.lot7 …
    raise SystemExit(main())
