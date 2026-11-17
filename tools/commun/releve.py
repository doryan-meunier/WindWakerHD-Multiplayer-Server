"""Relever un coéquipier (Four Swords Adventures, Spirit Tracks), docs/etudes/autres_zelda.md §5 : ce que le client et
le serveur partagent (constantes, règle effective, distances). Aucune entrée/sortie.

Décisions de l'utilisateur : la règle n'existe QU'AVEC LE DEATHLINK (avec la vie partagée, tout le monde serait à terre
en même temps : coupée) ; coupée en mode Hanteurs (un Hanteur ne relève personne) ; jamais le robot. Un Link qui tombe à
0 cœur reste À TERRE ``DELAI`` s (mod natif 0.48.0, natif/mod_wwmp/releve.c) ; un coéquipier qui reste ``TENIR`` s à
moins de ``RAYON`` u (1,5 m) de lui, ou qui utilise la commande « Relever » à cette distance, le relève avec ``VIE``
quarts (2 cœurs) : pas de mort, pas de vague. Sinon sa vraie mort, puis la vague habituelle du deathlink.
"""
from __future__ import annotations

import math

FEAT = "releve"                 # le client sait être relevé (mod natif 0.48.0, CALL 19)
DELAI = 20.0                    # s à terre
PAS_PAR_S = 30
RAYON = 150.0                   # u (1,5 m) : distance du releveur
TENIR = 2.0                     # s à rester près de lui
VIE = 8                         # quarts de cœur rendus (2 cœurs)
AIR_MAX = 45                    # pas en l'air permis après la vie à 0 (recul d'un coup ; mod : air_max)
POSSIBLE_PAS = 90               # « relève possible » rechargé pour 3 s à chaque tour du client
ETAT_AGE = 3.0                  # s : position plus vieille : pas comptée pour relever
COMMANDE = "relever"            # commun/commandes.py, groupe « multi »
TOUCHE = "Ctrl+End"             # Ctrl+Fin (Fin seule : « enfermer » du mode Hanteurs)
# ESSAI « relever un coéquipier » (Réglages › Essais ; à retirer après validation)
ID_ESSAI = 91                   # pas ceux des essais porter 98, porté 97, coups 96, ralliement 95, bonheur 94,
                                # tenues 93, fusions 92
NOM_ESSAI = "Essai Secours"
ESSAI_SECOURS = 5.0             # s : le faux coéquipier vient te relever
ESSAI_PRES = 120.0              # u : place du faux coéquipier qui te relève (à côté de toi)
ESSAI_LOIN = 400.0              # u : place du faux coéquipier au départ (4 m devant toi)


def _nombre(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def est_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def pos3(p):
    """[x, y, z] de nombres finis, ou None."""
    if not isinstance(p, (list, tuple)) or len(p) < 3 or not all(_nombre(v) for v in p[:3]):
        return None
    if any(abs(v) > 1e6 for v in p[:3]):
        return None
    return [float(v) for v in p[:3]]


def distance(a, b) -> float:
    """Distance 3D (u) ; infinie si une position manque."""
    pa, pb = pos3(a), pos3(b)
    if pa is None or pb is None:
        return float("inf")
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(pa, pb)))


def effective(option: bool, deathlink: bool, vie_partagee: bool, hanteurs: bool) -> bool:
    """La règle joue-t-elle ? Option de la partie, ET deathlink, ET pas de vie partagée, ET pas le mode Hanteurs."""
    return bool(option) and bool(deathlink) and not bool(vie_partagee) and not bool(hanteurs)


def etape(zone) -> str:
    """« stage:room » -> « stage »."""
    return str(zone or "").split(":", 1)[0]
