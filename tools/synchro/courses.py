"""Courses en bateau (Skyward Sword), lots C1 à C4 de docs/etudes/autres_zelda.md §3 : ce que le serveur
(serveur/courses.py), le client (client/courses.py) et le lanceur (lanceur/courses.py) partagent : constantes,
parcours (validation, empreinte), géométrie des passages, chrono, parcours d'essai et faux concurrents. Aucune
entrée/sortie.

Unités du jeu : 100 u = 1 m (synchro/ralliement.py : 200 u = 2 m). Angle y : 0 = +Z, 0x4000 = +X. Le plan de la mer
est (x, z) ; la hauteur y ne compte jamais pour un passage.

Parcours (fichier ``courses.json`` du lanceur, message ``course_cmd``) : ``{nom, etape: "sea", depart: {x, y, z},
balises: [{x, y, z, r}], tours}``. Les coordonnées viennent TOUJOURS du jeu : tracé par l'hôte en naviguant (commande
« Poser une balise » : le premier point posé est le départ, les suivants les balises) ou, pour l'ESSAI seul, généré
autour de la position du bateau au moment du clic. Aucune coordonnée du jeu n'est écrite ici.
"""
from __future__ import annotations

import hashlib
import json
import math
import random

FEAT = "courses"            # hello : ce PC sait courir (passages, signal n° 3, chrono dans le bandeau)
ETAPE = "sea"               # les courses : la mer seule (les îles sont des salles de « sea », à pied = abandon)
RAYON = 1500.0              # u : rayon d'une balise (15 m)
RAYON_MIN, RAYON_MAX = 500.0, 5000.0
DEPART_RAYON = 4000.0       # u : au départ, à moins de 40 m de la ligne
DECOMPTE = 3.0              # s : « 3… 2… 1… Partez ! »
FAUX_DEPART_VITESSE = 200.0  # u/s : bateau à plus de 2 m/s avant « Partez » : faux départ
PENALITE = 3.0              # s ajoutées pour un faux départ (une fois)
# [HYPO, à mesurer pendant l'essai : le rapport donne la vitesse max vue] vitesse d'un bateau (voile rapide de la HD
# comprise) jamais dépassée : un passage plus rapide entre deux balises est refusé
VMAX = 5000.0               # u/s (50 m/s)
SAUT = 6000.0               # u : bond d'un relevé à l'autre ...
SAUT_VITESSE = 3 * VMAX     # ... à plus de cette vitesse : téléportation (Ballade des vents) -> disqualifié
PIED = 5.0                  # s à pied (ni à la barre, ni à la nage) : abandon (« quitter la mer »)
TOURS_MAX = 5
BALISES_MIN, BALISES_MAX = 2, 40
COORD_MAX = 1e6
NOM_MAX = 40
# ESSAI : parcours généré autour du bateau, faux concurrents
ESSAI_BALISES = 6
ESSAI_RAYON = 30000.0       # u : boucle d'environ 300 m de rayon
ESSAI_NOM = "Course d'essai"
RIVAUX = (("Essai Rival", 1300.0), ("Essai Rival 2", 1500.0))   # [HYPO] vitesses simulées (u/s)
RIVAL_ECART = 0.06          # ± 6 % par tronçon


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def est_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def point(d) -> dict | None:
    """{x, y, z} fini et borné (y optionnel : 0) ; accepte aussi une liste [x, y, z]."""
    if isinstance(d, (list, tuple)) and len(d) == 3:
        d = {"x": d[0], "y": d[1], "z": d[2]}
    if not isinstance(d, dict):
        return None
    x, y, z = d.get("x"), d.get("y", 0.0), d.get("z")
    if not (_num(x) and _num(y) and _num(z)) or max(abs(x), abs(y), abs(z)) >= COORD_MAX:
        return None
    return {"x": round(float(x), 1), "y": round(float(y), 1), "z": round(float(z), 1)}


def nom_ok(s, defaut: str = "Course") -> str:
    """Nom d'un parcours : texte imprimable, espaces resserrés, NOM_MAX caractères au plus."""
    t = " ".join("".join(c if c.isprintable() else " " for c in str(s if s is not None else "")).split())[:NOM_MAX]
    return t or defaut


def valide_parcours(d) -> dict | None:
    """Parcours normalisé {nom, etape, depart, balises, tours} ou None (mal formé, hors bornes)."""
    if not isinstance(d, dict) or d.get("etape", ETAPE) != ETAPE:
        return None
    dep = point(d.get("depart"))
    bl = d.get("balises")
    if dep is None or not isinstance(bl, list) or not BALISES_MIN <= len(bl) <= BALISES_MAX:
        return None
    balises = []
    for b in bl:
        p = point(b)
        if p is None:
            return None
        r = b.get("r", RAYON) if isinstance(b, dict) else RAYON
        if not _num(r) or not RAYON_MIN <= r <= RAYON_MAX:
            return None
        p["r"] = float(r)
        balises.append(p)
    tours = d.get("tours", 1)
    if not est_id(tours) or not 1 <= tours <= TOURS_MAX:
        return None
    return {"nom": nom_ok(d.get("nom")), "etape": ETAPE, "depart": dep, "balises": balises, "tours": tours}


def empreinte(p: dict) -> str:
    """Identifiant d'un parcours (records) : ses points et ses tours, pas son nom (12 caractères hexadécimaux)."""
    brut = json.dumps([p["depart"], p["balises"], p["tours"]], sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(brut.encode("ascii")).hexdigest()[:12]


def sequence(p: dict) -> list[dict]:
    """Les balises à passer dans l'ordre, tours compris."""
    return list(p["balises"]) * int(p["tours"])


def distance(a: dict, b: dict) -> float:
    return math.hypot(b["x"] - a["x"], b["z"] - a["z"])


def longueur(p: dict) -> float:
    """Longueur du parcours (u), de centre à centre."""
    pts = [p["depart"]] + sequence(p)
    return sum(distance(a, b) for a, b in zip(pts, pts[1:]))


def passage(p0, p1, centre: dict, r: float) -> float | None:
    """Fraction (0..1) du segment p0 -> p1 (positions (x, y, z), plan x/z) où le bateau ENTRE dans le disque de rayon
    ``r`` autour de ``centre`` (ou le traverse) ; 0.0 s'il y est déjà en p0 ; None s'il ne l'atteint pas."""
    cx, cz = centre["x"], centre["z"]
    ax, az = p0[0] - cx, p0[2] - cz
    dx, dz = p1[0] - p0[0], p1[2] - p0[2]
    if ax * ax + az * az <= r * r:
        return 0.0
    a = dx * dx + dz * dz
    if a <= 1e-9:
        return None
    b = 2.0 * (ax * dx + az * dz)
    c = ax * ax + az * az - r * r
    disc = b * b - 4.0 * a * c
    if disc < 0:
        return None
    t = (-b - math.sqrt(disc)) / (2.0 * a)
    return t if 0.0 <= t <= 1.0 else None


def vitesse_min(a: dict, ra: float, b: dict, rb: float, dt: float) -> float:
    """Vitesse (u/s) qu'il faut AU MOINS pour aller du bord du disque ``a`` à celui de ``b`` en ``dt`` s."""
    d = max(0.0, distance(a, b) - ra - rb)
    return d / dt if dt > 1e-6 else (float("inf") if d > 0 else 0.0)


def chrono(t) -> str:
    """Secondes -> « 1:23.4 » (« -:--.- » si inconnu)."""
    if not _num(t) or t < 0:
        return "-:--.-"
    d = int(round(float(t) * 10))
    m, d = divmod(d, 600)
    return f"{m}:{d // 10:02d}.{d % 10}"


def rang(n: int) -> str:
    return "1er" if n == 1 else f"{n}e"


# --- ESSAI : parcours généré, faux concurrents ----------------------------------------------------------------------
def parcours_essai(pos, rot: int, n: int = ESSAI_BALISES, rayon: float = ESSAI_RAYON) -> dict:
    """ESSAI : boucle de ``n`` balises de ``rayon`` u autour d'un centre placé sur le côté du bateau, départ à sa
    position ``pos`` (x, y, z), première balise dans la direction ``rot`` du bateau, la dernière SUR le départ (on finit
    là où on a commencé). Rien d'inventé : tout part de la position lue dans le jeu au moment du clic."""
    x, y, z = (float(v) for v in pos[:3])
    a = (int(rot) & 0xFFFF) * math.pi / 32768.0
    cx, cz = x + rayon * math.sin(a + math.pi / 2), z + rayon * math.cos(a + math.pi / 2)
    phi0 = a - math.pi / 2                       # angle du départ vu du centre ; φ croissant = sens du bateau
    balises = []
    for k in range(1, n + 1):
        f = phi0 + 2.0 * math.pi * k / n
        balises.append({"x": round(cx + rayon * math.sin(f), 1), "y": round(y, 1), "z": round(cz + rayon * math.cos(f), 1),
                        "r": RAYON})
    return {"nom": ESSAI_NOM, "etape": ETAPE, "depart": {"x": round(x, 1), "y": round(y, 1), "z": round(z, 1)},
            "balises": balises, "tours": 1}


def rival_temps(p: dict, vitesse: float, graine: int) -> list[float]:
    """ESSAI : heures de passage (s après « Partez ») d'un faux concurrent à chaque balise de la séquence."""
    rnd = random.Random(graine)
    pts = [p["depart"]] + sequence(p)
    t, out = 0.0, []
    for a, b in zip(pts, pts[1:]):
        v = vitesse * (1.0 + rnd.uniform(-RIVAL_ECART, RIVAL_ECART))
        t += max(0.0, distance(a, b) - b["r"]) / max(1.0, v) + 0.5
        out.append(round(t, 2))
    return out
