"""Randomizer par fichiers générés localement, étape 5 : restes du randomizer (docs/etudes/rando_fichiers.md §3,
éléments 24 à 26, 29, 30 ; et l'étal à 950 rubis « non rachetable »).

Quatre options, toutes COUPÉES par défaut, dans aucun préréglage, jamais essayées en jeu :

=====================  ==========================================================================================
option                 ce qu'elle fait
=====================  ==========================================================================================
lieux_kane             les quatre récompenses de Kane (« Joe », décorations de Mercantîle) mélangées : octet de la
                       table 0x101C3870 écrit par le client (écriture gardée de rando/donnees.py, comme le
                       lot 1 du portage) ; aucun fichier, aucun pack
courrier_tout          (exige lieux_courrier) les six AUTRES lettres avec objet (pub des bombes, pub de la boutique
                       de l'Île du Pic, Tingle, carte de membre argent et or de Beedle, amie de Hoskit) : octets de
                       la même table du courrier ; leurs objets (hors de la liste du pack) ne vont que dans un lieu
                       « donnée »
lieux_encheres_100     l'enchère à 100 rubis (voile rapide) : octet 0x1019018D, et le bit inutilisé 0x6B01 posé
                       dans le champ « déjà gagné » du lot (0x10190192) quand l'objet change (sinon un autre objet
                       se gagnerait sans fin) ; bit partagé entre les joueurs (jeu/progress.DEFAULT_ALLOW)
boutique_epuise        (exige lieux_boutiques ; option « fichiers » : pack WWMP_Rando GÉNÉRÉ) correctif « épuisé
                       par emplacement » de la boutique de l'Île du Pic, repris de TWWHD (12 mots à 0x02215570) :
                       l'étal à 950 rubis (et celui à 500) ne se rachète plus quel que soit son objet. Identique au
                       jeu d'origine avec les objets d'origine. Déjà inclus avec lieux_beedle (lot 6)
=====================  ==========================================================================================

Les sites (adresses, objets d'origine) sont dans wwmp/rando.SITES_DONNEES (sites 15 à 25, ajoutés après ceux du
lot 1 : une table d'avant se relit) ; ce module porte les règles de l'étape : dépendances, verrou d'histoire,
relevé pour la version des données, signaux du trackeur, code « épuisé ».

Repris de TWWHD-Randomizer (SuperDude88 et contributeurs, licence MIT, « Copyright (c) 2024 SuperDude88 », texte :
tools/rando/logique/LICENSE_TWWHD.txt) : adresses des lieux (logic/data/location_data.yaml), bit 0x6B01 de
l'enchère (asm/patches/flexible_item_locations.asm), correctif « épuisé » (même fichier, converti dans
tools/rando/lot6_asm/beedle.asm). Ce module ne touche ni au jeu ni au réseau.
"""
from __future__ import annotations

import hashlib
import json

from rando import noyau as rando

GROUPES = {"kane": "lieux_kane", "courrier2": "courrier_tout", "enchere100": "lieux_encheres_100"}
OPTIONS_DON = tuple(GROUPES.values())
OPTION_EPUISE = "boutique_epuise"
OPTIONS_BOOL = OPTIONS_DON + (OPTION_EPUISE,)
OPTIONS_FICHIERS = (OPTION_EPUISE,)            # seule option de l'étape qui exige le pack généré
DEPENDANCES = {"courrier_tout": "lieux_courrier", OPTION_EPUISE: "lieux_boutiques"}
assert set(GROUPES) == set(rando.GROUPES_LOT9)

# verrou d'histoire des lieux de l'étape (le même que le lot 1 : le moment où le jeu d'origine les ouvre n'est pas
# prouvé, la logique n'y compte qu'après le second passage à la Forteresse)
VERROU = "'Helmaroc_King_Defeated'"

# raison écrite au spoiler quand l'option du groupe est coupée
RAISONS = {
    "kane": "récompense de Kane : octet de la table du jeu 0x101C3870 ; mélangée seulement avec l'option "
            "« lieux_kane » (écriture en mémoire par le client, jamais essayée en jeu), d'origine sinon",
    "courrier2": "lettre : octet de la table du courrier du jeu ; mélangée seulement avec les options "
                 "« lieux_courrier » et « courrier_tout » (écriture en mémoire par le client, jamais essayée en jeu), "
                 "d'origine sinon",
    "enchere100": "enchère à 100 rubis (voile rapide) : octet de la table des enchères et bit « déjà gagné » 0x6B01 ; "
                  "mélangée seulement avec l'option « lieux_encheres_100 » (écriture en mémoire par le client, jamais "
                  "essayée en jeu), d'origine sinon",
}

SITES = tuple(k for k, s in enumerate(rando.SITES_DONNEES) if s[0] in GROUPES)
PAR_NOM = {rando.SITES_DONNEES[k][1]: k for k in SITES}

# ---------------------------------------------------------------------------------------------------------------
# Signaux du trackeur (élément 29) : lieux « donnée » ou cadeaux dont le jeu pose un bit d'événement PARTAGÉ quand il
# donne l'objet. Le serveur connaît l'union des bits du groupe (synchro de la progression) : le trackeur coche le
# lieu tout seul, sans rien lire dans le jeu. Un bit n'est listé que s'il est prouvé dans le RPX et posé quel que
# soit l'objet placé (aucun faux « fait ») :
#   - enchères : champ +6 de chaque lot de la table 0x1019016C, lu par 0x0205A608 (isEventBit) ; l'enchère à 100
#     rubis n'en a pas d'origine (0x0000) : son bit 0x6B01 n'existe que si l'objet a changé (option ci-dessus) ;
#   - Kane : table de bits 0x101C305C lue par 0x022C6368 (isEventBit, puis onEventBit 0x022C63B4 quand l'objet est
#     donné) ;
#   - boutique de l'Île du Pic : 0x2020 (500 rubis) et 0x2010 (950 rubis), posés à l'achat [HD-ASM 0x022155F8 et
#     0x0221560C]. Sans le correctif « épuisé » le jeu ne les pose que pour le flacon et le quart de cœur, que les
#     objets interdits du lot 1 n'autorisent qu'à leur propre étal : jamais un bit pour un autre étal.
# PAS Ivan (abeilles tueuses, site N2 0), bien que 0x1340 soit posé sans condition juste après la démo de l'objet
# [HD-ASM 0x02298C14 bl createItemForPresentDemo, puis 0x02298C30-0x02298C38 `li r4, 0x1340` / onEventBit ; seule
# écriture de ce bit] : il est posé AUSSI quand Ivan donne son objet d'origine (table N2 pas encore chargée), ce qui
# cocherait à tort (relecture adverse). Ivan passe par le journal des dons, objet final vérifié.
# Lettres, Sam, Linda et Kreeb, Ivan et les autres cadeaux : signalés par les clients de jeu (rando/trackeur_client.py :
# registres du courrier, journal des dons du pack, bit 18, objet donné comparé à la table).
SIGNAUX = {
    "Windfall Island - Auction 40 Rupee": 0x0F01,
    "Windfall Island - Auction 5 Rupee": 0x1080,
    "Windfall Island - Auction 60 Rupee": 0x1040,
    "Windfall Island - Auction 80 Rupee": 0x1020,
    "Windfall Island - Auction 100 Rupee": 0x6B01,
    "Windfall Island - Kane Decorate Shop Guru": 0x2410,
    "Windfall Island - Kane Decorate Postman Statue": 0x2408,
    "Windfall Island - Kane Decorate Six Flags": 0x2404,
    "Windfall Island - Kane Decorate Six Idols": 0x2402,
    "Rock Spire Isle - Beedle 500 Rupee Item": 0x2020,
    "Rock Spire Isle - Beedle 950 Rupee Item": 0x2010,
}
BIT_ENCHERE_100 = 0x6B01


def _vrai(options, cle: str) -> bool:
    return isinstance(options, dict) and options.get(cle) is True


def groupes_actifs(options) -> frozenset:
    return frozenset(g for g, cle in GROUPES.items() if _vrai(options, cle))


def actif(options) -> bool:
    """Une option de l'étape 5 est-elle allumée ?"""
    return any(_vrai(options, k) for k in OPTIONS_BOOL)


def epuise(options) -> bool:
    return _vrai(options, OPTION_EPUISE)


def options_fichiers(options) -> dict:
    return {OPTION_EPUISE: True} if epuise(options) else {}


def controler_options(o: dict) -> None:
    """Contrôle des options de l'étape 5 DÉJÀ normalisées. Lève rando.ErreurRando."""
    for k in OPTIONS_BOOL:
        if not isinstance(o.get(k), bool):
            raise rando.ErreurRando(f"option {k} : booléen attendu")
    for k, dep in DEPENDANCES.items():
        if o[k] and o.get(dep) is not True:
            raise rando.ErreurRando(f"option {k} : exige l'option {dep}")


def releve(options) -> str:
    """Empreinte des règles de l'étape 5 qui jouent dans cette partie ("" si aucune option) : sites (adresses,
    objets d'origine, objets interdits, accompagnements), objets réservés aux lieux « donnée », verrou, sites du
    correctif « épuisé »."""
    g = groupes_actifs(options)
    if not g and not epuise(options):
        return ""
    doc = {"sites": [[k, s[0], s[1], s[2], list(s[3]), [list(x) for x in s[4]], sorted(s[5])]
                     for k, s in enumerate(rando.SITES_DONNEES) if s[0] in g],
           "seuls": sorted(rando.OBJETS_DON_SEULS) if g else [], "verrou": VERROU if g else "",
           "epuise": [f"0x{a:08X}" for a in EPUISE_SITES] if epuise(options) else []}
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------------------------
# Trackeur : lieux faits d'après les bits d'événement partagés
# ---------------------------------------------------------------------------------------------------------------
def bit_pose(bits, bit: int) -> bool:
    """``bits`` : progression au format de wwlib.progress ({"ev": {octet: masque}, ...}, union d'un groupe)."""
    ev = bits.get("ev") if isinstance(bits, dict) else None
    if not isinstance(ev, dict):
        return False
    m = ev.get(bit >> 8, 0)
    return isinstance(m, int) and bool(m & (bit & 0xFF))


def lieux_signales(lg, unions) -> set:
    """Index des lieux de la logique ``lg`` faits d'après les bits d'événement ``unions`` (liste d'unions de groupes,
    ou une seule). Lecture seule ; ne dépend ni de la table ni des objets placés (aucun spoiler)."""
    if isinstance(unions, dict):
        unions = [unions]
    out = set()
    for nom, bit in SIGNAUX.items():
        i = lg.il.get(nom) if hasattr(lg, "il") else None
        if i is not None and any(bit_pose(u, bit) for u in unions or ()):
            out.add(i)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Correctif « épuisé » de la boutique de l'Île du Pic (option boutique_epuise ; pack généré)
# ---------------------------------------------------------------------------------------------------------------
# Les seuls sites du fragment « beedle » du lot 6 qui font l'« épuisé par emplacement » (TWWHD,
# flexible_item_locations.asm) : à l'achat, le bit 0x2000 + (0x20 >> emplacement) au lieu du test du numéro d'objet
# (0x50, 0x07, 0xE1). Le reste du fragment (Beedle à 20 rubis, bit 0x6902) appartient à lieux_beedle.
EPUISE_SITES = (0x02215570, 0x02215574, 0x02215578, 0x0221557C, 0x02215580, 0x02215584, 0x02215588, 0x0221558C,
                0x02215590, 0x022155AC, 0x022155B8, 0x022155C0)


def code(options, rpx, familles6=frozenset()) -> dict:
    """Code du pack généré pour l'option boutique_epuise : {symboles, cave, sites}. Vide si l'option est coupée, ou
    si la famille « beedle » du lot 6 est mélangée (son fragment contient déjà ces sites). Chaque mot d'origine est
    relu dans le RPX ``rpx`` (rando_lot6.Rpx). Lève rando_lot6.ErreurLot6."""
    from rando import lot6 as L6
    vide = {"symboles": {}, "cave": [], "sites": []}
    if not epuise(options) or "beedle" in frozenset(familles6):
        return vide
    fr = L6.lire_fragment("beedle")
    sites = ["; --- etape 5 : boutique de l'Ile du Pic, « epuise » par emplacement (TWWHD, MIT) ---"]
    vus, noms = set(), set()
    for ligne in fr["sites"]:
        s = ligne.strip()
        if not s.startswith("0x"):
            continue
        a = int(s.split(" ", 1)[0], 16)
        if a not in EPUISE_SITES:
            continue
        avant = int(s.split("avant=", 1)[1][:8], 16)
        if rpx.mot(a) != avant:
            raise L6.ErreurLot6(f"boutique « épuisé » : mot d'origine inattendu à 0x{a:08X}")
        sites.append(ligne)
        vus.add(a)
        for mot in s.split(";", 1)[0].split():
            if mot.startswith("wwr_jeu_"):
                noms.add(mot)
    if vus != set(EPUISE_SITES):
        raise L6.ErreurLot6("boutique « épuisé » : sites absents du fragment beedle")
    sym = {a: n for a, n in fr["symboles"].items() if n in noms}
    if set(sym.values()) != noms:
        raise L6.ErreurLot6("boutique « épuisé » : symbole absent du fragment beedle")
    return {"symboles": sym, "cave": [], "sites": sites}
