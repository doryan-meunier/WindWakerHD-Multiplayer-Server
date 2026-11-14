"""Randomizer par fichiers générés localement, étape 2 : lot 6, les « lieux à crochet » (docs/etudes/rando_fichiers.md).

Les lieux de ce lot ne sont ni des coffres (stub N1 de WWMP_Mailbox) ni des cadeaux (stub N2) ni des octets de données
(lot 1) : leur objet est écrit dans un FICHIER du jeu (salle, liste d'événements) ou dans le CODE du jeu (immédiat d'une
instruction), par le pack WWMP_Rando GÉNÉRÉ sur chaque PC à partir de son propre dump (rando/fichiers.py). Une
famille = une option du randomizer, coupée par défaut :

========================  ======================================================================================
famille (option)          lieux, et comment
========================  ======================================================================================
tresors                   57 trésors engloutis, Big Octo, canonnières, terre noire (SCOB des salles de la mer)
  (lieux_tresors)         + drapeaux du Roc de l'Aiguille + rayon lumineux par numéro de carte + objet enterré
evenements                8 objets donnés par une liste d'événements (event_list.dat, action « 011get_item » ou
  (lieux_evenements)      « 059get_dance » pour les chants) + facteur du café (bit 0x6A02 : ne donne qu'une fois)
boss                      6 réceptacles des boss (Bitem des salles + octet du code, createItemForBoss qui suit
  (lieux_boss)            l'objet, drapeau d'objet 0x15, réceptacle qui ne pose plus le drapeau du boss)
fees                      6 Grandes Fées : objet par fée (petit code de la cave) ; bourse, sacs de bombes et
  (lieux_fees)            carquois gardent leur palier d'après l'inventaire, comme dans le jeu d'origine ; ces six
                          paliers ne vont que chez une fée et une fée ne reçoit qu'eux (échange entre les fées)
sol                       12 objets posés : pots (z_rot & 0xFF00), têtes de pierre (x_rot & 0xFF), acteurs item
  (lieux_sol)             (paramètre & 0xFF)
pnj_code                  15 personnages dont l'objet est un immédiat du code (Tingle, Mme Marie, Lenzo, Maggie,
  (lieux_pnj_code)        calmar, plate-forme, Orca, arbres flétris) + potions du Dr Bandam + Zunari
beedle                    Beedle à 20 rubis et carte à 900 rubis de l'Île du Pic (données du programme écrites par
  (lieux_beedle)          le pack, « épuisé » suivi par emplacement, bit 0x6902)
========================  ======================================================================================

Ce qui sert à TOUTES les familles : « tout objet posable au sol » (field_item_resources, item_info, ressources des
chants et des jauges, code de itemGetExecute / mode_wait / getYOffset), généré depuis le RPX du dump de ce PC.

Ce qui vient de TWWHD-Randomizer (SuperDude88 et contributeurs, licence MIT, « Copyright (c) 2024 SuperDude88 »,
texte : tools/rando/logique/LICENSE_TWWHD.txt) : chemins, décalages et adresses (rando_logique/lieux_fichiers.json),
correctifs d'assembleur (tools/rando/lot6_asm/*.asm, convertis par tools/rando/port_twwhd.py), données de
« tout objet posable au sol ». Le petit code des Grandes Fées et celui des potions sont à nous.

Règles (docs/etudes/rando_fichiers.md §3) :

* une seule voie par lieu : aucun de ces lieux n'est un coffre, un cadeau N2 ou un lieu « donnée » ;
* les objets donnés ici passent par execItemGet / createItem : jamais d'objet de donjon (la clé irait au donjon de la
  salle), jamais de palier d'arc (lot 4) ; bourse, sacs et carquois (paliers des fées) seulement chez une Grande Fée ;
* un objet hors de la liste du pack (PERMIS2) ne va que dans un lieu de ce lot (le stub N1, le stub N2 et les écritures
  de données le refuseraient) ;
* chaque édition relit la valeur d'origine attendue (nom d'acteur, nom d'action, mot du RPX) : jamais à l'aveugle.

Ce module ne touche ni au jeu ni au réseau.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct

ICI = os.path.dirname(os.path.abspath(__file__))
FICHIER_SITES = os.path.join(ICI, "logique", "lieux_fichiers.json")
DOSSIER_ASM = os.path.join(ICI, "lot6_asm")

TYPE_FICHIER = 5                       # entrée de table [5, site, 0, objet d'origine, nouvel objet]
FAMILLES = ("tresors", "evenements", "boss", "fees", "sol", "pnj_code", "beedle")
OPTION = {f: "lieux_" + f for f in FAMILLES}
OPTIONS = tuple(OPTION.values())
NOMS_FAMILLES = {
    "tresors": ("trésors engloutis", "sunken treasures"),
    "evenements": ("objets d'événements (chants compris)", "event items (songs included)"),
    "boss": ("réceptacles des boss", "boss heart containers"),
    "fees": ("Grandes Fées", "Great Fairies"),
    "sol": ("objets posés, pots et têtes de pierre", "standing items, pots and stone heads"),
    "pnj_code": ("personnages à immédiat, potions, Zunari", "hard-coded characters, potions, Zunari"),
    "beedle": ("Beedle à 20 rubis et carte à 900 rubis", "Beedle 20 rupee item and 900 rupee chart"),
}

IDS_DONJON = frozenset({0x15, 0x4E, 0x4C, 0x4D})
# identifiants sans objet dans le jeu d'origine (clés par donjon de TWWHD) : objets_vanilla.json « ids_interdits »
IDS_SANS_OBJET = frozenset({64, 65, 92, 93, 96, 115, 132, 133, 136, 137})
# paliers des Grandes Fées : objet -> genre (le palier donné suit l'inventaire, comme dans le jeu d'origine)
PALIERS_FEES = {0xAB: "bourse", 0xAC: "bourse", 0xAD: "bombes", 0xAE: "bombes", 0xAF: "carquois", 0xB0: "carquois"}
CHANTS = range(0x6D, 0x73)
# objets d'origine des deux étals de Beedle du lot (sac à appâts, carte au trésor 4) : permis à l'autre étal ("épuisé"
# suivi par emplacement, correctif de TWWHD)
OBJETS_BEEDLE = frozenset({0x2C, 0xE1})

# masques de l'objet dans les paramètres des acteurs (WriteLocations.cpp de TWWHD)
MASQUES = {"Bitem": 0x0000FF00, "Ritem": 0x000000FF, "Salvage": 0x00000FF0, "SwSlvg": 0x00000FF0,
           "Salvag2": 0x00000FF0, "SalvagN": 0x00000FF0, "SalvagE": 0x00000FF0, "SalvFM": 0x00000FF0,
           "TagKb": 0x000000FF, "item": 0x000000FF, "itemFLY": 0x000000FF}
POTS = frozenset({"kotubo", "ootubo1", "Kmtub", "Ktaru", "Ostool", "Odokuro", "Okioke", "Kmi02", "Ptubo", "KkibaB",
                  "Kmi00", "Hbox2S"})
TETES = frozenset({"Homen1", "Homen2"})
SALVAGES = frozenset({"Salvage", "SwSlvg", "Salvag2", "SalvagN", "SalvagE", "SalvFM"})

# Ankle (Île de Tingle, « toutes les statues ») : le jeu d'origine ne regarde PAS les statues possédées (leur item_func
# est vide, d_item.cpp) mais les coffres des statues OUVERTS (dComIfGs_isStageTbox(donjon, 0xF), 0x022E954C et
# suivants ; TWWHD les remplace par des bits d'événement posés par ses propres item_func, ce que nous ne portons pas).
# Avec la famille « pnj_code », la logique exige donc EN PLUS (jamais à la place) que les cinq coffres des statues
# soient atteints : un événement par coffre, défini dans la zone du coffre avec l'exigence du coffre.
ANKLE = "Tingle Island - Ankle All Statues Reward"
COFFRES_STATUES = ("Dragon Roost Cavern - Tingle Statue Chest", "Forbidden Woods - Tingle Statue Chest",
                   "Tower of the Gods - Tingle Statue Chest", "Earth Temple - Tingle Statue Chest",
                   "Wind Temple - Tingle Statue Chest")
EVTS_STATUES = tuple(f"WWMP_L6_Coffre_Statue_{i}" for i in range(1, 6))
VERROU_ANKLE = " and ".join(f"'{n}'" for n in EVTS_STATUES)

# Roc de l'Aiguille (fix_needle_rock_island_salvage_flags de TWWHD) : drapeaux 0 et 1 en double dans le jeu d'origine
CHEMIN_AIGUILLE = "content/Common/Stage/sea_Room29.szs@YAZ0@SARC@Room29.bfres@BFRES@room.dzr"

# RPX du dump USA v0 (lu seulement, pour « tout objet posable au sol » et les mots d'origine)
SHA_RPX = "c4f0ab300542e0bfc462696850534e71db2ad02288a7eb55e5a4cd4062f16153"
TAILLE_RPX = 6994496


class ErreurLot6(ValueError):
    pass


def _charger() -> tuple:
    with open(FICHIER_SITES, encoding="utf-8") as f:
        doc = json.load(f)
    sites = tuple(doc["sites"])
    for k, s in enumerate(sites):
        if s.get("famille") not in FAMILLES or not isinstance(s.get("id"), int) or not 0 < s["id"] < 0xFF:
            raise ErreurLot6(f"lieux_fichiers.json : site {k} invalide")
    noms = [s["nom"] for s in sites]
    if len(noms) != len(set(noms)):
        raise ErreurLot6("lieux_fichiers.json : lieu en double")
    return sites


SITES = _charger()
PAR_NOM = {s["nom"]: k for k, s in enumerate(SITES)}


def releve() -> str:
    """Empreinte du relevé des sites (entre dans la version des données d'une table qui en mélange un)."""
    t = json.dumps([[s["famille"], s["nom"], s["objet"], s["id"]] for s in SITES], ensure_ascii=False)
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]


def familles_actives(options) -> frozenset:
    """Familles du lot 6 que ces options mélangent (option absente : coupée)."""
    if not isinstance(options, dict):
        return frozenset()
    return frozenset(f for f, o in OPTION.items() if options.get(o) is True)


def actif(options) -> bool:
    return bool(familles_actives(options))


def _permis2():
    from rando import noyau as rando
    return rando.PERMIS2


def fich_seul(objet: int) -> bool:
    """Objet qui ne peut aller QUE dans un lieu de ce lot (hors de la liste blanche des stubs du pack Mailbox)."""
    return objet not in _permis2()


def accepte(site: int, objet: int) -> bool:
    """Le lieu ``site`` peut-il donner ``objet`` ? (Son propre objet d'origine : toujours.)"""
    s = SITES[site]
    if objet == s["id"]:
        return True
    if not 0 < objet < 0xFF or objet in IDS_DONJON or objet in IDS_SANS_OBJET:
        return False
    if s["voie"] == "fee" or objet in PALIERS_FEES:
        # le palier se choisit dans le code de la fée, d'après l'inventaire : ces six objets ne vont que chez une
        # Grande Fée, et (six fées, six paliers) une fée ne reçoit qu'eux
        return s["voie"] == "fee" and objet in PALIERS_FEES
    if s["voie"] == "boutique":
        # étal : objets que le jeu met déjà dans un coffre ou chez un personnage, et ceux des deux étals de Beedle
        return objet in _permis2() or objet in OBJETS_BEEDLE
    return True


# ---------------------------------------------------------------------------------------------------------------
# Éditions des fichiers (générateur)
# ---------------------------------------------------------------------------------------------------------------
def _dzx(chemin: str) -> str:
    return chemin + "@DZX"


def editions(entrees: dict, familles) -> list[tuple]:
    """Éditions de fichiers de la table : ``entrees`` {site: (origine, nouveau)} (TOUS les sites mélangés de type 5),
    ``familles`` actives. Rend [(chemin, opération, arguments)], dans l'ordre des sites (puis les correctifs)."""
    out = []
    entrees = dict(entrees)
    if "boss" in familles:
        # un boss exclu (option « exclus ») garde son réceptacle, mais son Bitem et son octet doivent quand même
        # être écrits : le code du pack lit l'objet là (0 dans le jeu d'origine)
        for k, s in enumerate(SITES):
            if s["voie"] == "boss":
                entrees.setdefault(k, (s["id"], s["id"]))
    for k in sorted(entrees):
        o, n = entrees[k]
        s = SITES[k]
        if s["famille"] not in familles:
            raise ErreurLot6(f"site {k} ({s['nom']}) d'une famille non mélangée")
        v = s["voie"]
        if v == "scob" and n != o:
            for pos, nom in zip(s["decalages"], s["acteurs"]):
                out.append((_dzx(s["chemin"]), "parametre_objet", (pos, nom, o, n)))
        elif v == "acteur" and n != o:
            for pos, nom in zip(s["decalages"], s["acteurs"]):
                out.append((_dzx(s["chemin"]), "parametre_objet", (pos, nom, o, n)))
        elif v == "evenement" and n != o:
            out.append((s["chemin"] + "@EVENTS", "objet_evenement", (s["decalage_nom"], s["decalage"], s["action"],
                                                                     o, n)))
        elif v == "boss":
            # toujours (même objet d'origine) : le code du pack lit l'objet dans le Bitem (0 dans le jeu d'origine)
            for chemin, pos in s["bitem"]:
                out.append((_dzx(chemin), "parametre_objet", (pos, "Bitem", 0, n)))
    if "tresors" in familles:
        out.append((_dzx(CHEMIN_AIGUILLE), "drapeaux_aiguille", ()))
    return out


def _entree(dz, pos: int, nom: str):
    for c in dz.chunks:
        for e in c.entrees:
            if e.pos == pos:
                if e.nom != nom:
                    raise ErreurLot6(f"acteur {e.nom!r} au lieu de {nom!r} à 0x{pos:X}")
                return e
    raise ErreurLot6(f"aucune entrée à 0x{pos:X}")


def _objet_acteur(e) -> int:
    nom = e.nom
    p = struct.unpack_from(">I", e.data, 8)[0]
    xr, _yr, zr = struct.unpack_from(">HHH", e.data, 0x18)
    if nom in POTS:
        z = (zr >> 8) & 0xFF
        return 0x1F if z == 0 and p & 0x3F == 0x1F else z          # pot d'origine : table de butin (pendentif)
    if nom in TETES:
        x = xr & 0xFF
        return 0x1F if x == 0 else x
    m = MASQUES[nom]
    return (p & m) >> ((m & -m).bit_length() - 1)


def poser_objet(dz, pos: int, nom: str, ancien: int, nouveau: int) -> None:
    """Objet d'un acteur (SCOB, pot, tête de pierre, item, Bitem), comme ModifyActor / ModifySCOB / ModifyBoss de
    TWWHD. Refus si l'acteur n'est pas celui attendu ou ne porte pas l'objet d'origine attendu."""
    e = _entree(dz, pos, nom)
    if _objet_acteur(e) != ancien and not (nom == "Bitem" and _objet_acteur(e) == 0):
        raise ErreurLot6(f"{nom} à 0x{pos:X} : objet 0x{_objet_acteur(e):02X} au lieu de 0x{ancien:02X}")
    d = e.data
    p = struct.unpack_from(">I", d, 8)[0]
    xr, yr, zr = struct.unpack_from(">HHH", d, 0x18)
    if nom in POTS:
        p = (p & ~0x3F) | 0x3F
        zr = (zr & 0x00FF) | (nouveau << 8)
    elif nom in TETES:
        p = (p & ~0x3F000) | 0x3F000
        xr = (xr & 0xFF00) | nouveau
    elif nom in MASQUES:
        m = MASQUES[nom]
        sh = (m & -m).bit_length() - 1
        p = (p & ~m) | ((nouveau << sh) & m)
        if nom in ("item", "itemFLY") and p & 0x03000000 == 0:
            p |= 0x03000000                  # comportement 3 : l'objet ne disparaît pas
    else:
        raise ErreurLot6(f"acteur {nom!r} non géré")
    struct.pack_into(">I", d, 8, p & 0xFFFFFFFF)
    struct.pack_into(">HHH", d, 0x18, xr, yr, zr)


def drapeaux_aiguille(dz) -> None:
    """fix_needle_rock_island_salvage_flags de TWWHD : les deux premiers SCOB de récupération de types 2 à 4 et de
    drapeau 0 ou 1 prennent les drapeaux 8 et 9."""
    trouves = []
    for e in dz.entrees("SCOB", None):
        d = e.data
        if e.nom in SALVAGES and (d[8] & 0xF0) >> 4 in (2, 3, 4) and ((d[8] & 0x0F) << 4 | (d[9] & 0xF0) >> 4) in (0, 1):
            trouves.append(e)
    if len(trouves) < 2:
        raise ErreurLot6("Roc de l'Aiguille : récupérations à corriger introuvables")
    trouves[0].data[9] = (0x08 << 4) | (trouves[0].data[9] & 0x0F)
    trouves[1].data[9] = (0x09 << 4) | (trouves[1].data[9] & 0x0F)


def objet_evenement(ev, pos_nom: int, pos_octet: int, action: str, ancien: int, nouveau: int) -> None:
    """ModifyEvent de TWWHD : nom de l'action (0x20 octets) et octet de l'objet, à taille égale."""
    a = next((x for x in ev.actions if x.pos == pos_nom), None)
    if a is None:
        raise ErreurLot6(f"event_list : aucune action à 0x{pos_nom:X}")
    if not ev.o_entiers <= pos_octet < ev.o_entiers + 4 * ev.n_entiers:
        raise ErreurLot6("event_list : octet d'objet hors de la table des entiers")
    attendu = ancien - 0x6D if action == "059get_dance" else ancien
    if ev.brut[pos_octet] != attendu:
        raise ErreurLot6(f"event_list : objet 0x{ev.brut[pos_octet]:02X} au lieu de 0x{attendu:02X}")
    if nouveau in CHANTS:
        nom, octet = "059get_dance", nouveau - 0x6D
    else:
        nom, octet = "011get_item", nouveau
    ev.renommer_action(a, action, nom)
    ev.brut[pos_octet] = octet


# ---------------------------------------------------------------------------------------------------------------
# RPX du dump (lecture seule)
# ---------------------------------------------------------------------------------------------------------------
class Rpx:
    """Sections d'un cking.rpx (ELF de Cafe OS, sections zlib), lu une fois, jamais écrit."""

    def __init__(self, data: bytes):
        import zlib
        if data[:4] != b"\x7fELF":
            raise ErreurLot6("cking.rpx illisible")
        shoff, = struct.unpack_from(">I", data, 0x20)
        shentsize, shnum = struct.unpack_from(">HH", data, 0x2E)
        self.secs = []
        for i in range(shnum):
            _nm, typ, flg, addr, off, size = struct.unpack_from(">6I", data, shoff + i * shentsize)
            raw = data[off:off + size] if typ != 8 else b""
            if flg & 0x08000000 and raw:
                raw = zlib.decompress(raw[4:])
            self.secs.append((addr, raw))

    @classmethod
    def depuis_dump(cls, jeu: str) -> "Rpx":
        p = os.path.join(jeu, "code", "cking.rpx")
        try:
            with open(p, "rb") as f:
                d = f.read()
        except OSError as e:
            raise ErreurLot6(f"code/cking.rpx illisible ({e})") from None
        if len(d) != TAILLE_RPX or hashlib.sha256(d).hexdigest() != SHA_RPX:
            raise ErreurLot6("code/cking.rpx n'est pas celui de l'USA v0 attendu")
        return cls(d)

    def octets(self, a: int, n: int) -> bytes:
        for addr, raw in self.secs:
            if addr and raw and addr <= a and a + n <= addr + len(raw):
                return raw[a - addr:a - addr + n]
        raise ErreurLot6(f"adresse 0x{a:08X} hors des sections du RPX")

    def mot(self, a: int) -> int:
        return struct.unpack(">I", self.octets(a, 4))[0]


# ---------------------------------------------------------------------------------------------------------------
# « Tout objet posable au sol » (allow_all_items_to_be_field_items de TWWHD), généré depuis le RPX
# ---------------------------------------------------------------------------------------------------------------
ITEM_RESOURCES = 0x101E4674          # 0x24 octets par objet
FIELD_ITEM_RESOURCES = 0x101E6A74    # 0x1C octets par objet
ITEM_INFO = 0x101E8674               # 4 octets par objet
SANS_MODELE = bytes.fromhex(
    "1a202122232425262728292a2b2c2d2e2f303132333536393a3c3e3f42434c4d4e505152535455565758596162636465666768696a"
    "6b6c6d6e6f70717277797a7b7c7d7e7f80989a999b9c9d9e9fa0a1a2a3a4a5a6a7aaabacadaeafb0b1b2b3b4b5b6b7b8b9babbbcbd"
    "bebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecfd0d1d2d3d4d5d6d7d8d9dadbdcdedddfe0e1e2e3e4e5e6e7e8e9eaebecedeeeff0f1f2"
    "f3f4f5f6f7f8f9fafbfcfdfe")
# objet -> pointeur du nom d'archive (.szs) dans les données du jeu (TWWHD : szs_name_pointers)
_NOMS_SZS = {
    0x1a: 0x1004e5d8, 0x20: 0x1004e414, 0x21: 0x1004ec54, 0x22: 0x1004e578, 0x23: 0x1004e4a8, 0x24: 0x1004e4d0,
    0x25: 0x1004e548, 0x26: 0x1004e658, 0x27: 0x1004e730, 0x28: 0x1004e4f0, 0x29: 0x1004e498, 0x2a: 0x1004e550,
    0x2b: 0x1004e4a0, 0x2c: 0x1004e4d8, 0x2d: 0x1004e6b0, 0x2e: 0x1004e5c0, 0x2f: 0x1004e4e8, 0x30: 0x1004e4c8,
    0x31: 0x1004e41c, 0x32: 0x1004e5c0, 0x33: 0x1004e510, 0x35: 0x1004e580, 0x36: 0x1004e590, 0x38: 0x1004e558,
    0x3c: 0x1004e560, 0x3f: 0x1004e440, 0x42: 0x1004e518, 0x43: 0x1004e520, 0x4c: 0x1004e4b8, 0x4d: 0x1004e4b0,
    0x4e: 0x1004e698, 0x50: 0x1004e430, 0x51: 0x1004e538, 0x52: 0x1004e530, 0x53: 0x1004e528, 0x54: 0x1004e5b0,
    0x55: 0x1004e5b0, 0x56: 0x1004e5b8, 0x57: 0x1004e5a0, 0x58: 0x1004e5a8, 0x59: 0x1004e598, 0x61: 0x1004e570,
    0x62: 0x1004e600, 0x63: 0x1004e608, 0x64: 0x1004e610, 0x65: 0x1004e618, 0x66: 0x1004e620, 0x67: 0x1004e628,
    0x68: 0x1004e630, 0x69: 0x1004ec24, 0x6a: 0x1004ec3c, 0x6b: 0x1004ec48, 0x6c: 0x1004e518, 0x6d: 0x1004e518,
    0x6e: 0x1004e518, 0x6f: 0x1004e518, 0x70: 0x1004e518, 0x71: 0x1004e518, 0x72: 0x1004e518, 0x77: 0x1004e434,
    0x78: 0x1004e434, 0x79: 0x1004e638, 0x7a: 0x1004e638, 0x7b: 0x1004e638, 0x7c: 0x1004e638, 0x7d: 0x1004e638,
    0x7e: 0x1004e638, 0x7f: 0x1004e638, 0x80: 0x1004e638, 0x98: 0x1004e5e0, 0x99: 0x1004e5e8, 0x9a: 0x1004e5f0,
    0x9b: 0x1004e5f8, 0x9c: 0x1004e688, 0x9d: 0x1004e500, 0x9e: 0x1004e4f8, 0x9f: 0x1004e658, 0xa0: 0x1004e518,
    0xa1: 0x1004e518, 0xa2: 0x1004e518, 0xa3: 0x1004e660, 0xa4: 0x1004e668, 0xa5: 0x1004e670, 0xa6: 0x1004e678,
    0xa7: 0x1004e680, 0xab: 0x1004e470, 0xac: 0x1004e478, 0xad: 0x1004e490, 0xae: 0x1004e4a0, 0xaf: 0x1004e480,
    0xb0: 0x1004e488, 0xc2: 0x1004e588, 0xcb: 0x1004e468, 0xdb: 0x1004e650, 0xdc: 0x1004e468, 0xed: 0x1004e648,
    0xfa: 0x1004e638, 0xfc: 0x1004e638, 0xfe: 0x1004e638}
for _i in range(0xb3, 0xc2):
    _NOMS_SZS[_i] = 0x1004e5d8
for _i in range(0xc3, 0xcb):
    _NOMS_SZS[_i] = 0x1004e588
for _i in list(range(0xcc, 0xdb)) + list(range(0xdd, 0xed)):
    _NOMS_SZS[_i] = 0x1004e640
for _i in (0xee, 0xef, 0xf0, 0xf1, 0xf2, 0xf3, 0xf4, 0xf5, 0xf6, 0xf7, 0xf8, 0xf9, 0xfb, 0xfd):
    _NOMS_SZS[_i] = 0x1004e648
NOPS_CHAMP = (0x0255220C, 0x02552214, 0x0255221C, 0x02552224, 0x0255222C, 0x02552234, 0x02552450)


def _copie_de(item: int) -> int:
    if item in (0x39, 0x3A, 0x3E):
        return 0x38
    if 0x6D <= item <= 0x72:
        return 0x22
    if item in (0xB1, 0xB2):
        return 0x52
    return item


def donnees_champ(rpx: Rpx) -> dict:
    """{adresse de mot: nouveau mot} des données de « tout objet posable au sol », calculés sur le RPX du dump."""
    mem = {}

    def lire(a):
        b = a & ~3
        if b not in mem:
            mem[b] = bytearray(rpx.octets(b, 4))
        return mem[b], a - b

    def ecrire(a, data: bytes):
        for i, x in enumerate(data):
            w, k = lire(a + i)
            w[k] = x
    origine = {}
    for item in SANS_MODELE:
        src = _copie_de(item)
        fix = ITEM_RESOURCES + item * 0x24 if (src != item or item == 0xAA) else 0
        ptr = _NOMS_SZS[0x38] if item == 0xAA else _NOMS_SZS[src]
        a_src = ITEM_RESOURCES + src * 0x24
        d1, d2 = rpx.octets(a_src + 8, 0xD), rpx.octets(a_src + 0x1C, 4)
        fia = FIELD_ITEM_RESOURCES + item * 0x1C
        for a, data in ((fia, struct.pack(">I", ptr)), (fia + 4, d1), (fia + 0x14, d2)):
            ecrire(a, data)
        if fix:
            for a, data in ((fix, struct.pack(">I", ptr)), (fix + 8, d1), (fix + 0x1C, d2)):
                ecrire(a, data)
    for item in range(0x100):
        a = ITEM_INFO + 4 * item
        w, _k = lire(a)
        if w[1] == 0:
            w[1] = 0x28
        if w[2] == 0:
            w[2] = 0x28
    out = {}
    for a in sorted(mem):
        w = struct.unpack(">I", bytes(mem[a]))[0]
        if w != rpx.mot(a):
            out[a] = w
    del origine
    return out


# ---------------------------------------------------------------------------------------------------------------
# Code du pack généré
# ---------------------------------------------------------------------------------------------------------------
# Fragments de TWWHD convertis (fichiers de DOSSIER_ASM), par famille
FRAGMENTS = {"*": ("champ",), "tresors": ("tres",), "evenements": ("evt",), "boss": ("boss",),
             "sol": ("sol",), "pnj_code": ("pnj",), "beedle": ("beedle",), "fees": ()}

# Grandes Fées (à nous) : à 0x02095AF8 (juste après « r3 = numéro de la fée »), l'objet vient d'une table de six
# octets ; un palier (bourse, sac de bombes, carquois) se choisit d'après l'inventaire, exactement comme le code
# d'origine (0x02095B18-0x02095B7C : octet 0x32 = bourse, 0x90 = capacité de bombes, 0x8F = capacité de flèches).
# Numéro > 5 : comme le jeu d'origine (li r30, 4).
FEE_SYMBOLES = {0x02095B80: "wwr_jeu_02095B80"}
FEE_CAVE = """\
wwr_fee:
    cmplwi r3, 5
    bgt wwr_fee_defaut
    lis r12, wwr_fees@ha
    addi r12, r12, wwr_fees@l
    lbzx r30, r12, r3
    lis r9, 0x1020
    lwz r10, -31524(r9)
    cmplwi r30, 0xAB
    beq wwr_fee_bourse
    cmplwi r30, 0xAC
    beq wwr_fee_bourse
    cmplwi r30, 0xAD
    beq wwr_fee_bombes
    cmplwi r30, 0xAE
    beq wwr_fee_bombes
    cmplwi r30, 0xAF
    beq wwr_fee_carquois
    cmplwi r30, 0xB0
    beq wwr_fee_carquois
    b wwr_jeu_02095B80
wwr_fee_bourse:
    lbz r10, 0x32(r10)
    cmpwi r10, 0
    li r30, 0xAC
    bne wwr_fee_fin
    li r30, 0xAB
    b wwr_jeu_02095B80
wwr_fee_bombes:
    lbz r10, 0x90(r10)
    cmplwi r10, 0x1E
    li r30, 0xAE
    bgt wwr_fee_fin
    li r30, 0xAD
    b wwr_jeu_02095B80
wwr_fee_carquois:
    lbz r10, 0x8F(r10)
    cmplwi r10, 0x1E
    li r30, 0xB0
    bgt wwr_fee_fin
    li r30, 0xAF
wwr_fee_fin:
    b wwr_jeu_02095B80
wwr_fee_defaut:
    li r30, 4
    b wwr_jeu_02095B80
wwr_fees:
"""
FEE_SITES = "0x02095AF8 = b wwr_fee                ; avant=28030001 (cmplwi r3, 1)"

# Potions du Dr Bandam (Custom_Symbol de TWWHD, réécrit : deux octets ALIGNÉS, l'original pointait sur un octet impair) :
# au message 7627 (« potion toute neuve »), la potion verte (0x52) ou bleue (0x53) devient l'objet de la table.
BANDAM_SYMBOLES = {0x025D7DEC: "wwr_jeu_025D7DEC"}
BANDAM_CAVE = """\
wwr_bandam:
    stwu r1, -16(r1)
    mflr r0
    stw r0, 20(r1)
    lwz r0, 2456(r30)
    cmpwi r0, 7627
    bne wwr_bandam_donne
    lis r12, wwr_potions@ha
    addi r12, r12, wwr_potions@l
    cmpwi r4, 0x52
    bne wwr_bandam_bleu
    lbz r4, 0(r12)
    b wwr_bandam_donne
wwr_bandam_bleu:
    cmpwi r4, 0x53
    bne wwr_bandam_donne
    lbz r4, 1(r12)
wwr_bandam_donne:
    bl wwr_jeu_025D7DEC
    lwz r0, 20(r1)
    mtlr r0
    addi r1, r1, 16
    blr
wwr_potions:
"""
BANDAM_SITES = ("0x022313A8 = bl wwr_bandam             ; avant=483A6A45 (bl createItemForPresentDemo)\n"
                "0x02231414 = bl wwr_bandam             ; avant=483A69D9 (bl createItemForPresentDemo)")

# Beedle (données, en plus du fragment « beedle ») : liste d'objets de rechange (Item_set_data3) et bit « objet unique »
# retiré des étals 0 (20 rubis) et 0xD (900 rubis) ; les étals 0xB et 0xC (500, 950) restent au lot 1 (client).
SHOP_ITEM_DATA = 0x101EAEA4
BEEDLE_RECHANGE = (0x101BD558, 0x00000000, 0x101EB850)


def _mot_octet(rpx: Rpx, adr: int, valeur: int) -> tuple[int, int, int]:
    """(adresse du mot, mot d'origine, mot avec l'octet ``adr`` = ``valeur``)."""
    b = adr & ~3
    w = rpx.mot(b)
    k = 3 - (adr - b)
    return b, w, (w & ~(0xFF << (8 * k))) | ((valeur & 0xFF) << (8 * k))


def lire_fragment(nom: str) -> dict:
    """Fragment converti de TWWHD : {symboles: {adresse: nom}, cave: [lignes], sites: [lignes], marques}."""
    with open(os.path.join(DOSSIER_ASM, nom + ".asm"), encoding="utf-8") as f:
        t = f.read()
    out = {"symboles": {}, "cave": [], "sites": [], "texte": t}
    partie = None
    for ligne in t.splitlines():
        s = ligne.strip()
        if s in ("; [symboles]", "; [cave]", "; [sites]"):
            partie = s[3:-1]
            continue
        if partie is None or not s or s.startswith(";"):
            continue
        if partie == "symboles":
            m = re.fullmatch(r"(0x[0-9A-Fa-f]{8}) = (\w+):", s.split(";", 1)[0].strip())
            if not m:
                raise ErreurLot6(f"fragment {nom} : symbole illisible {s!r}")
            out["symboles"][int(m.group(1), 16)] = m.group(2)
        else:
            out[partie].append(ligne.rstrip())
    return out


def code(entrees: dict, familles, rpx: Rpx) -> dict:
    """Code du pack généré pour cette table : {symboles: {adresse: nom}, cave: [lignes], sites: [lignes]}.
    ``entrees`` : {site: (origine, nouveau)} des sites de type 5 ; ``familles`` : familles actives (non vide)."""
    familles = frozenset(familles)
    if not familles:
        return {"symboles": {}, "cave": [], "sites": []}
    sym, cave, sites = {}, [], []

    def ajouter_symboles(d):
        for a, n in d.items():
            if sym.get(a, n) != n or (n in sym.values() and sym.get(a) != n):
                raise ErreurLot6(f"symbole {n} en double ou adresse 0x{a:08X} nommée deux fois")
            sym[a] = n

    def site_mot(adr: int, avant: int, apres: int, note: str):
        if rpx.mot(adr) != avant:
            raise ErreurLot6(f"mot d'origine inattendu à 0x{adr:08X} ({note})")
        sites.append(f"0x{adr:08X} = .int 0x{apres:08X}   ; avant={avant:08X} {note}")

    noms = list(FRAGMENTS["*"]) + [n for f in FAMILLES if f in familles for n in FRAGMENTS[f]]
    entrees = dict(entrees)
    if "boss" in familles:                   # boss exclu : octet écrit quand même (voir ``editions``)
        for k, s in enumerate(SITES):
            if s["voie"] == "boss":
                entrees.setdefault(k, (s["id"], s["id"]))
    par_site = {}
    for k, (o, n) in entrees.items():
        par_site[k] = n
    for nom in noms:
        fr = lire_fragment(nom)
        ajouter_symboles(fr["symboles"])
        cave.append(f"; --- fragment {nom} (TWWHD, MIT) ---")
        for l in fr["cave"]:
            if "{{zunari}}" in l:
                k = PAR_NOM["Windfall Island - Zunari Exotic Flower"]
                l = l.replace("{{zunari}}", f".int 0x{par_site.get(k, SITES[k]['id']):02X}000000")
            cave.append(l)
        sites.append(f"; --- fragment {nom} (TWWHD, MIT) ---")
        for l in fr["sites"]:
            m = re.match(r"(0x[0-9A-Fa-f]{8}) = .*twwhd avant=([0-9A-F]{8})", l)
            if not m or rpx.mot(int(m.group(1), 16)) != int(m.group(2), 16):
                raise ErreurLot6(f"fragment {nom} : mot d'origine inattendu ({l.strip()[:40]})")
            sites.append(l)
    # « tout objet posable au sol » : données et 7 nops (toujours, dès qu'une famille est active)
    sites.append("; --- tout objet posable au sol : donnees (generees depuis le RPX de ce PC) ---")
    for a in NOPS_CHAMP:
        site_mot(a, rpx.mot(a), 0x60000000, "nop")
    for a, w in donnees_champ(rpx).items():
        sites.append(f"0x{a:08X} = .int 0x{w:08X}")
    if "fees" in familles:
        ajouter_symboles(FEE_SYMBOLES)
        cave.append("; --- Grandes Fees (WWMP) ---")
        cave += FEE_CAVE.splitlines()
        octets = bytearray(SITES[k]["id"] for k in range(len(SITES)) if SITES[k]["voie"] == "fee")
        for k, s in enumerate(SITES):
            if s["voie"] == "fee":
                octets[s["numero"]] = par_site.get(k, s["id"])
        cave.append(f"    .int 0x{bytes(octets[:4]).hex().upper()}")
        cave.append(f"    .int 0x{bytes(octets[4:6]).hex().upper()}0000")
        if rpx.mot(0x02095AF8) != 0x28030001:
            raise ErreurLot6("Grandes Fées : mot d'origine inattendu à 0x02095AF8")
        sites.append(FEE_SITES)
    if "pnj_code" in familles:
        ajouter_symboles(BANDAM_SYMBOLES)
        cave.append("; --- potions du Dr Bandam (WWMP, d'apres TWWHD) ---")
        cave += BANDAM_CAVE.splitlines()
        vert = par_site.get(PAR_NOM["Windfall Island - Potion Shop 15 Green Chu"], 0x52)
        bleu = par_site.get(PAR_NOM["Windfall Island - Potion Shop 15 Blue Chu"], 0x53)
        cave.append(f"    .int 0x{vert:02X}{bleu:02X}0000")
        for l, a, w in ((BANDAM_SITES.splitlines()[0], 0x022313A8, 0x483A6A45),
                        (BANDAM_SITES.splitlines()[1], 0x02231414, 0x483A69D9)):
            if rpx.mot(a) != w:
                raise ErreurLot6(f"potions : mot d'origine inattendu à 0x{a:08X}")
            sites.append(l)
    # immédiats et octets propres à la table
    sites.append("; --- objets de cette table ---")
    for k in sorted(entrees):
        o, n = entrees[k]
        s = SITES[k]
        if s["voie"] in ("immediat", "zunari") and n != o:
            for a in s["adresses"]:
                b, w0, w1 = _mot_octet(rpx, a, n)
                if (w0 >> (8 * (3 - (a - b)))) & 0xFF != o:
                    raise ErreurLot6(f"{s['nom']} : octet d'origine inattendu à 0x{a:08X}")
                site_mot(b, w0, w1, s["nom"][:40])
        elif s["voie"] == "boss":
            for a, v0 in s["octets_rpx"]:
                b, w0, w1 = _mot_octet(rpx, a, n)
                if (w0 >> (8 * (3 - (a - b)))) & 0xFF != v0:
                    raise ErreurLot6(f"{s['nom']} : octet d'origine inattendu à 0x{a:08X}")
                site_mot(b, w0, w1, s["nom"][:40])
        elif s["voie"] == "boutique":
            base = SHOP_ITEM_DATA + s["entree"] * 0x10
            w = rpx.mot(base)
            if w & 0xFF != o:
                raise ErreurLot6(f"{s['nom']} : objet d'origine inattendu")
            if n != o:
                site_mot(base, w, (w & ~0xFF) | n, s["nom"][:40])
            u = rpx.mot(base + 0xC)
            site_mot(base + 0xC, u, u & ~0x02000000, "objet unique retire")
    if "beedle" in familles:
        a, v0, v1 = BEEDLE_RECHANGE
        site_mot(a, v0, v1, "Item_set_data3 : liste de rechange (TWWHD)")
    if "sol" in familles:
        w = rpx.mot(0x101CA100)
        site_mot(0x101CA100, w, w & ~0x80, "fix_stone_head_bugs (TWWHD)")
    adrs = [int(l.split(" ", 1)[0], 16) for l in sites if l.startswith("0x")]
    if len(adrs) != len(set(adrs)):
        raise ErreurLot6("deux lignes du pack généré écrivent la même adresse")
    return {"symboles": sym, "cave": cave, "sites": sites}


def texte_code(c: dict) -> tuple[str, str]:
    """(lignes de symboles à mettre avant ``.origin``, lignes de cave et de sites à mettre après la zone)."""
    avant = "".join(f"0x{a:08X} = {n}:\n" for a, n in sorted(c["symboles"].items()))
    apres = "\n".join(["; --- lot 6 (randomizer par fichiers) : code repris de TWWHD-Randomizer (SuperDude88 et",
                       ";     contributeurs), licence MIT, Copyright (c) 2024 SuperDude88 (LICENSE_TWWHD.txt), et code",
                       ";     WWMP. Genere sur ce PC pour cette table : ne pas distribuer. ---",
                       "; --- lot 6 : code ajoute (cave) ---"] + c["cave"]
                      + ["; --- lot 6 : sites en place et donnees ---"] + c["sites"]) + "\n"
    return avant, apres


# ---------------------------------------------------------------------------------------------------------------
# Outil de développement : reconvertir les fragments depuis une copie de TWWHD-Randomizer
# ---------------------------------------------------------------------------------------------------------------
GROUPES_TWWHD = {
    "champ": (0x0217DE64, 0x0217DEE0, 0x0217DF2C, 0x0217DF84, 0x0217F1B4, 0x0217F1D8, 0x02182D70, 0x02182DA8,
              0x02182DB8),
    "tres": (0x024675F4, 0x02526490),
    "evt": (0x021FCCD4, 0x021FCCE0, 0x022022F0, 0x02200D54),
    "boss": (0x025D8A70, 0x0212456C, 0x020D0AE4, 0x020D0AF0, 0x02124578, 0x02124580, 0x021031F0, 0x0254DCA8,
             0x0254DCD8, 0x025D8A84),
    "sol": (0x024CC08C, 0x024CC134, 0x02358A54, 0x02358A8C),
    "pnj": (0x022D94E8, 0x022DA8A4, 0x0227D244, 0x0227C2B8, 0x0227D200, 0x02347C34, 0x02347C48, 0x02347CB4,
            0x02346F28, 0x02347ABC, 0x02347AC0, 0x02347ACC, 0x02347D18),
    "beedle": (0x02212C64, 0x0221543C, 0x02215450, 0x02215570, 0x022155AC, 0x022155B8, 0x022155C0),
}
ENTETE_FRAGMENT = (
    "; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,\n"
    "; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.\n"
    "; Converti par tools/wwmp/rando_lot6.py (--porter) avec tools/wwmp/rando_port_twwhd.py.\n"
    "; FICHIER GENERE : ne pas le modifier a la main. Utilise SEULEMENT par le pack WWMP_Rando genere.\n")


def porter(source: str, rpx, packs: dict) -> dict:
    """{nom: texte} des fragments, depuis la copie de TWWHD ``source`` (outil de développement, aucun effet en jeu).
    Lève ErreurLot6 si une conversion n'est pas propre (refus ou collision avec nos packs)."""
    from rando import port_twwhd as p
    src = p.Source.depuis_dossier(source)
    out = {}
    for nom, sites in GROUPES_TWWHD.items():
        prefixe = f"wwr_{nom}_"
        controle = p.convertir(src, sites, rpx, packs, prefixe=prefixe)
        if not controle.propre:
            raise ErreurLot6(f"fragment {nom} : " + "; ".join(controle.refus + controle.collisions))
        c = p.convertir(src, sites, rpx, {}, prefixe=prefixe)      # aucun nom repris d'un autre pack
        if not c.propre or controle.donnees:
            raise ErreurLot6(f"fragment {nom} : conversion différente sans les packs, ou donnée inattendue")
        cave = []
        suivant_zunari = False
        for l in c.cave:
            if suivant_zunari and l.strip().startswith(".int"):
                l = "    {{zunari}}"
                suivant_zunari = False
            if "zunari_magic_armor_slot_item_id" in l:
                suivant_zunari = True
            cave.append(l)
        t = [ENTETE_FRAGMENT, "; [symboles]"]
        t += [f"0x{a:08X} = {n}:" for a, n in sorted(c.symboles.items())]
        t += ["; [cave]"] + cave + ["; [sites]"]
        t += [f"0x{a:08X} = {txt:<28} ; twwhd avant={av:08X} apres={ap:08X}" for a, av, ap, txt in c.sites]
        # une fonction du jeu porte le même nom dans tous les fragments (une adresse = un nom dans le pack)
        out[nom] = ("\n".join(t) + "\n").replace(prefixe + "jeu_", "wwr_jeu_")
    return out


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="Lot 6 du randomizer par fichiers : outil de développement")
    ap.add_argument("--porter", metavar="TWWHD", help="reconvertit les fragments depuis une copie de TWWHD-Randomizer")
    ap.add_argument("--rpx", help="cking.rpx d'origine (lu seulement)")
    a = ap.parse_args(argv)
    if not a.porter or not a.rpx:
        ap.error("--porter et --rpx sont exigés")
    with open(a.rpx, "rb") as f:
        rpx = Rpx(f.read())
    racine = os.path.dirname(os.path.dirname(ICI))
    packs = {}
    for n, d, f in (("mailbox", "WWMP_Mailbox", "patch_mailbox.asm"),
                    ("confort", "WWMP_Confort", "patch_confort.asm")):
        with open(os.path.join(racine, "graphicpack", d, f), encoding="utf-8") as fh:
            packs[n] = fh.read()
    frags = porter(a.porter, rpx, packs)
    os.makedirs(DOSSIER_ASM, exist_ok=True)
    for nom, t in frags.items():
        with open(os.path.join(DOSSIER_ASM, nom + ".asm"), "w", encoding="utf-8", newline="\n") as f:
            f.write(t)
    print(f"{len(frags)} fragments écrits dans {DOSSIER_ASM}", file=sys.stderr)
    return 0


if __name__ == "__main__":             # outil de développement : cd tools && python -m rando.lot6 …
    raise SystemExit(main())
