"""Camouflage de la tenue jaune (tenues à pouvoir, docs/etudes/autres_zelda.md §6) : ce que le serveur
(serveur/camouflage.py), le client (client/camouflage.py) et le lanceur partagent. Aucune entrée/sortie.

Décision de l'utilisateur (2026-11-17), tenue JAUNE seulement :

1. en entrant dans une zone (étape ou salle ; arrivée dans une étape comprise), le joueur en tenue jaune est invisible
   pour les monstres pendant ``DUREE`` (4 s, réglable côté serveur : ``--camouflage-duree``) ;
2. frapper un monstre le révèle aussitôt (fin du camouflage) ;
3. les autres Links (les fantômes des coéquipiers) le voient en transparence pendant ce temps (mod natif 0.49.0 :
   dessiné une image sur deux) ;
4. les Hanteurs ne le voient pas du tout et ne peuvent pas le prendre en spectateur pendant ce temps.

Règle de la PARTIE « Camouflage », ALLUMÉE par défaut (``--no-camouflage``, ``regles_cmd`` à chaud) ; elle ne joue
qu'avec les tenues à pouvoir (une couleur de pouvoir jaune). Le SERVEUR décide qui est camouflé et le dit à tous.
"""
from __future__ import annotations

import math

from commun import couleurs

FEAT = "camouflage"         # hello : ce PC sait se camoufler (mod natif 0.49.0, zone WWMPCAM1)
COULEUR = "jaune"
DUREE = 4.0                 # s
DUREE_MIN, DUREE_MAX = 0.5, 20.0
IMAGES_PAR_S = 30
MARGE = 15.0                # s : sans nouvelles du client, le serveur finit de lui-même après duree + MARGE (le jeu
                            # attend pendant les cinématiques)
RAISONS = {"expire": "délai écoulé", "revele": "il a frappé un monstre", "coup": "il a frappé un monstre",
           "coupe": "règle coupée", "zone": "autre zone", "parti": "parti", "hanteur": "devenu Hanteur",
           "couleur": "plus en tenue jaune", "delai": "délai écoulé (serveur)", "essai": "essai"}


def duree_ok(v) -> float:
    """Durée réglable (s) bornée ; défaut 4 s."""
    try:
        d = float(v)
    except (TypeError, ValueError):
        return DUREE
    if not math.isfinite(d):
        return DUREE
    return min(DUREE_MAX, max(DUREE_MIN, d))


def images(duree) -> int:
    """Durée (s) -> images du jeu (30 par seconde), bornées comme par le mod (15..600)."""
    return max(15, min(600, int(round(duree_ok(duree) * IMAGES_PAR_S))))


def jaune(c) -> bool:
    """Couleur de POUVOIR jaune (numéro 1..8 ou code)."""
    n = couleurs.normaliser(c)
    return bool(n) and couleurs.CODES[n - 1] == COULEUR


def ids_valides(v) -> frozenset:
    """Liste reçue (réseau) -> ensemble de numéros de joueurs (entiers 0..255)."""
    if not isinstance(v, (list, tuple)):
        return frozenset()
    return frozenset(x for x in v if isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 255)
