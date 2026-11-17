"""Tenues à pouvoir (Triforce Heroes), partie B : les deux FUSIONS (attaques à deux) ; ce que le serveur
(serveur/fusions.py), le client (client/fusions.py) et le lanceur partagent. Aucune entrée/sortie ;
docs/etudes/autres_zelda.md §6, docs/dev/PORTAGE_NATIF.md §3.51.

Décisions de l'utilisateur (2026-11-17) :

* **Fusion tournoyante** (sombre + turquoise) : les deux chargent l'attaque tournoyante (épée maintenue) et se
  collent (``DISTANCE``, 2 m) : ils fusionnent en UN Link contrôlé à deux pendant ``DUREE`` (30 s). Le premier qui a
  chargé DÉPLACE Link (stick, roulade, saut) ; l'autre ATTAQUE (épée, gadgets) et tient la caméra. x3 sur TOUS les
  coups.
* **Méga bombe** (orange + violet) : les deux tiennent une bombe allumée ; juste avant l'explosion (bombe tenue depuis
  ``BOMBE_MURE``), les deux la lancent à ``ECART_LANCERS`` (0,5 s) au plus : même fusion (le premier qui lance
  déplace), qui commence par une grosse explosion autour d'eux (``anneau``) avec la lumière « apparition » ; x3 sur les
  objets qui consomment de la magie (flèches de feu, de glace, de lumière : ``OBJETS_MAGIE``).
* Couleurs : bonnet à la couleur de celui qui déplace, tunique à la couleur de celui qui attaque (les bottes ne sont
  pas teintables : docs/dev/PORTAGE_NATIF.md §3.51).
* Seulement entre les deux membres du duo, en jeu, même salle ; jamais pendant une cinématique, au menu, porté ou
  porteur, en course, en mode Hanteurs, ni avec le robot. Fin : 30 s, départ, changement d'étape ou de salle, mort,
  cinématique ; les deux Links se séparent à la place du porteur (jamais dans un mur).
"""
from __future__ import annotations

import math

from commun import couleurs

FEAT = "fusions"            # hello : ce PC sait fusionner (mod natif 0.47.0)
DUREE = 30.0                # s
DISTANCE = 200.0            # u (2 m) : collés
CHARGE_MIN = 1.0            # s : B tenu sans rien porter = tournoyante chargée [HYPO : la charge du jeu dure moins]
BOMBE_MURE = 2.5            # s : bombe tenue depuis au moins ce temps au lancer (« juste avant l'explosion ») [HYPO :
                            # mèche de 5 s, comme le minuteur de Bomb2, 150 images]
ECART_LANCERS = 0.5         # s : les deux lancers à moins de ce temps l'un de l'autre
REPOS = 10.0                # s : pas de nouvelle fusion pour ce duo juste après la fin
ETAT_AGE = 5.0              # s : état plus vieux : le joueur n'est plus « en jeu » pour une fusion
FACTEUR = 24                # x3 (huitièmes)
TOURNOYANTE, MEGA_BOMBE = 1, 2
GENRES = {TOURNOYANTE: "tournoyante", MEGA_BOMBE: "méga bombe"}
NOMS_GENRES_EN = {TOURNOYANTE: "spin fusion", MEGA_BOMBE: "mega bomb"}
DEPLACE, ATTAQUE = 1, 2
ROLES = {DEPLACE: "déplace", ATTAQUE: "attaque"}
DUO_GENRE = {frozenset(("sombre", "turquoise")): TOURNOYANTE, frozenset(("orange", "violet")): MEGA_BOMBE}
OBJETS_MAGIE = ("flèches de feu", "flèches de glace", "flèches de lumière")
# manette (mod : FUS_BOUTONS_PERMIS = B, X, Y, R, ZR ; FUS_STICK_*)
BOUTONS_PORTEUR = 0x4000 | 0x2000 | 0x1000 | 0x0010     # B, X, Y, R : l'épée et les trois gadgets
STICK_G, STICK_D = 0x1, 0x2
# méga bombe : explosions (Bomb2 « explose tout de suite », commande 4) au centre et en anneau
ANNEAU_N = 6
ANNEAU_RAYON = 250.0        # u : souffle de Bomb2 200 u : de 50 à 450 u autour du centre
BOUCLIER = 45               # images (1,5 s) d'invincibilité de nos Links pendant le souffle
RAISONS_FIN = {"duree": "30 s écoulées", "depart": "un joueur est parti", "zone": "étape ou salle changée",
               "mort": "un Link est mort", "cinematique": "cinématique", "l3": "L3 : le passager se détache",
               "regle": "règle coupée", "port": "le passager ne tient plus sur le porteur"}


def code(c) -> str | None:
    n = couleurs.normaliser(c)
    return couleurs.CODES[n - 1] if n else None


def genre_de(c1, c2) -> int:
    """Fusion possible entre ces deux couleurs (1..8 ou codes) : TOURNOYANTE, MEGA_BOMBE ou 0."""
    a, b = code(c1), code(c2)
    if not a or not b or a == b:
        return 0
    return DUO_GENRE.get(frozenset((a, b)), 0)


def couleurs_du_genre(genre: int) -> tuple[int, int]:
    """Les deux couleurs (numéros) d'un genre de fusion."""
    for paire, g in DUO_GENRE.items():
        if g == genre:
            a, b = sorted(paire)
            return couleurs.CODES.index(a) + 1, couleurs.CODES.index(b) + 1
    return 0, 0


def facteurs(genre: int) -> tuple[int, int]:
    """(tous les coups, objets à magie) en huitièmes : tournoyante (24, 8), méga bombe (8, 24), sinon (8, 8)."""
    if genre == TOURNOYANTE:
        return FACTEUR, 8
    if genre == MEGA_BOMBE:
        return 8, FACTEUR
    return 8, 8


def valid_tenf(v) -> list[int] | None:
    """Champ « tenf » d'un coup reçu (facteurs de fusion de l'attaquant) : deux entiers 8..24, sinon None."""
    if not isinstance(v, list) or len(v) != 2:
        return None
    if not all(isinstance(x, int) and not isinstance(x, bool) and 8 <= x <= 24 for x in v):
        return None
    return None if v == [8, 8] else list(v)


def pos3(v):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    return p if all(math.isfinite(x) and abs(x) < 1e6 for x in p) else None


def colles(a, b, distance: float = DISTANCE) -> bool:
    """Positions finies à moins de ``distance`` (3D)."""
    pa, pb = pos3(a), pos3(b)
    return pa is not None and pb is not None and math.dist(pa, pb) <= distance


def anneau(centre, n: int = ANNEAU_N, rayon: float = ANNEAU_RAYON) -> list[list[float]]:
    """Explosions de la méga bombe : le centre, puis ``n`` points sur un cercle horizontal de ``rayon``."""
    c = pos3(centre)
    if c is None:
        return []
    out = [[round(c[0], 2), round(c[1], 2), round(c[2], 2)]]
    for k in range(max(0, int(n))):
        a = 2.0 * math.pi * k / n
        out.append([round(c[0] + rayon * math.sin(a), 2), round(c[1], 2), round(c[2] + rayon * math.cos(a), 2)])
    return out


def texte(genre: int, role: int, langue: str = "fr") -> str:
    en = langue == "en"
    g = (NOMS_GENRES_EN if en else GENRES).get(genre, "?")
    if en:
        r = {DEPLACE: "you MOVE (stick, roll, jump)", ATTAQUE: "you ATTACK (sword, items, camera)"}.get(role, "?")
        bonus = "x3 on every hit" if genre == TOURNOYANTE else "x3 with magic items (fire, ice, light arrows)"
    else:
        r = {DEPLACE: "tu DÉPLACES (stick, roulade, saut)", ATTAQUE: "tu ATTAQUES (épée, gadgets, caméra)"}.get(role, "?")
        bonus = "x3 sur tous les coups" if genre == TOURNOYANTE else "x3 avec les objets à magie (flèches de feu, de " \
                                                                     "glace, de lumière)"
    return f"{g} : {r} ; {bonus}"
