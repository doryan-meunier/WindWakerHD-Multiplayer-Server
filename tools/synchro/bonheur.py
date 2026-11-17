"""Fragments du Bonheur (Minish Cap), docs/etudes/autres_zelda.md §4 : ce que le serveur (serveur/bonheur.py) et le
client (client/bonheur.py) partagent : constantes, noms des moitiés, vérifications des messages. Aucune entrée/sortie.

Décisions de l'utilisateur (2026-11-17) : moitiés données directement (V1, pas de moitié au sol) ; 8 motifs × 2 côtés ;
1 chance sur 12 par monstre synchronisé vaincu ; 6 moitiés en poche au plus ; assemblage entre deux joueurs
différents, à moins de 3 m, accepté dans les 10 s ; récompense tirée au hasard : 1 % quart de cœur, 20 % fée, 79 %
pluie de rubis (plus de « flèches et bombes ») ; plafond de sécurité des quarts : 1 par donjon, 4 par partie
(réglables), au-delà le tirage du quart devient des rubis.
"""
from __future__ import annotations

import math

FEAT = "bonheur"            # hello : ce PC sait recevoir des moitiés et assembler (commande « Assembler »)
MOTIFS = ("Soleil", "Lune", "Étoile", "Vent", "Flamme", "Vague", "Feuille", "Roc")   # 8 motifs (Latin-1)
COTES = ("gauche", "droite")
CHANCE = 1.0 / 12.0         # par monstre synchronisé vaincu
POCHE_MAX = 6               # moitiés en poche au plus
DIST = 300.0                # u : 3 m entre les deux joueurs
DELAI = 10.0                # s : l'autre a ce temps pour accepter
REAPPARU = 30.0             # s : un monstre réapparu depuis moins que cela ne donne rien (pas de culture)
# récompense d'un assemblage : (nom, pour mille) ; 1 % quart de cœur, 20 % fée, 79 % pluie de rubis
RECOMPENSES = (("quart", 10), ("fee", 200), ("rubis", 790))
RUBIS = (20, 50, 100)       # montants d'une pluie de rubis (tirés au hasard)
QUARTS_DONJON = 1           # plafond de sécurité : quarts par donjon (mer et îles : un seul « donjon » à part)
QUARTS_PARTIE = 4           # plafond de sécurité : quarts par partie
NOMS_RECOMPENSES = {"quart": "quart de coeur", "fee": "fée", "rubis": "pluie de rubis"}
RECETTE_APPARITION = 6      # synchro/effets_pack.py RECETTE["apparition"] : la lumière de l'assemblage (CALL 11)
# ESSAI « Fragments du Bonheur » (--essai-bonheur, Réglages › Essais du lanceur ; à retirer après validation)
ESSAI_CHANCE = 0.5          # ESSAI : 1 chance sur 2 pour voir vite des moitiés
ESSAI_ACCEPTE = 1.5         # ESSAI : le faux coéquipier accepte après ce temps
ID_ESSAI = 94               # ESSAI : faux coéquipier (pas ceux des essais porter 98, porté 97, coups 96, ralliement 95)
NOM_ESSAI = "Essai Assorti"


def est_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def pos3(v) -> tuple[float, float, float] | None:
    """Trois nombres finis de moins de 1e6, sinon None."""
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(_num(x) for x in v):
        return None
    p = tuple(float(x) for x in v)
    return p if all(abs(x) < 1e6 for x in p) else None


def distance(a, b) -> float:
    return math.dist(a, b)


def moitie_ok(m) -> bool:
    """[motif, côté] valide."""
    return (isinstance(m, (list, tuple)) and len(m) == 2 and est_id(m[0]) and 0 <= m[0] < len(MOTIFS)
            and est_id(m[1]) and m[1] in (0, 1))


def nom_motif(i) -> str:
    return MOTIFS[i] if est_id(i) and 0 <= i < len(MOTIFS) else "?"


def nom_moitie(m) -> str:
    """« Vent (gauche) »."""
    return f"{nom_motif(m[0])} ({COTES[m[1]]})" if moitie_ok(m) else "?"


def recompense_ok(r) -> bool:
    return r in NOMS_RECOMPENSES
