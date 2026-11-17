"""Tenues à pouvoir (Triforce Heroes), partie A : ce que le serveur (serveur/tenues.py), le client (client/tenues.py) et
le lanceur partagent. Aucune entrée/sortie ; docs/etudes/autres_zelda.md §6.

Décisions de l'utilisateur (2026-11-17), qui REMPLACENT les bonus proposés par l'étude :

* le pouvoir est lié à la couleur de tenue (``commun/couleurs.py``, unique par joueur : le serveur l'attribue) ;
* option de partie « Tenues à pouvoir », ALLUMÉE par défaut ;
* un Hanteur n'a pas de tenue ; un Link possédé garde la sienne ; le robot n'a aucun pouvoir ;
* jamais d'objet donné, jamais la sauvegarde touchée : tout se fait en jeu, réversible (mod natif 0.46.0, tenues.c) ;
* duos orange + violet et sombre + turquoise : quand les deux membres sont EN JEU et PROCHES (même étape, même salle, à
  moins de ``DISTANCE`` (20 m, réglable), avec ``HYSTERESIS`` (2 s) avant de couper), chacun a les pouvoirs des deux.

Pouvoirs, par couleur (clés de commun/couleurs.py) :

=========  ==========================================================================================================
vert       rubis RAMASSÉS x1,5 (``rubis``) ; -25 % sur les achats en boutique (``prix``)
rouge      dégâts REÇUS / 2, au moins un quart de cœur (``solide``) ; dégâts INFLIGÉS / 2, épée et gadgets (``doux``)
bleu       vitesse x1,5 à la nage (``nage``) et en bateau (``bateau``)
violet     magie illimitée : la jauge ne baisse plus (``magie``)
jaune      vitesse au sol x1,5 (``vitesse``) ; épée x3 dans le dos d'un monstre (``dos``) ; 10 % d'esquive
           (``esquive``) ; dégâts REÇUS x2 (``fragile``)
orange     munitions illimitées : flèches et bombes ne baissent plus (``munitions``)
turquoise  dégâts x2 avec les gadgets, tout sauf l'épée (``gadgets``)
sombre     dégâts x1,5 à l'épée (``epee``)
=========  ==========================================================================================================

Les FACTEURS envoyés au mod sont en huitièmes (8 = x1, 0 = x1) ; le mod les borne lui-même.
"""
from __future__ import annotations

import math

from commun import couleurs

FEAT = "tenues"             # hello : ce PC applique les pouvoirs (mod natif 0.46.0)
DISTANCE = 2000.0           # u : 20 m entre les deux membres d'un duo
DISTANCE_MIN, DISTANCE_MAX = 300.0, 10000.0
HYSTERESIS = 2.0            # s : conditions perdues depuis ce temps avant de couper le partage
ETAT_AGE = 5.0              # s : position plus vieille : le joueur n'est plus « en jeu » pour le partage

POUVOIRS = {
    "vert": ("rubis", "prix"),
    "rouge": ("solide", "doux"),
    "bleu": ("nage", "bateau"),
    "violet": ("magie",),
    "jaune": ("vitesse", "dos", "esquive", "fragile"),
    "orange": ("munitions",),
    "turquoise": ("gadgets",),
    "sombre": ("epee",),
}
TOUS = tuple(p for c in couleurs.CODES for p in POUVOIRS[c])
DUOS = {"orange": "violet", "violet": "orange", "sombre": "turquoise", "turquoise": "sombre"}
NOMS = {   # pouvoir -> (français, anglais)
    "rubis": ("rubis ramassés ×1,5", "rupees picked up ×1.5"),
    "prix": ("boutiques -25 %", "shops -25%"),
    "solide": ("dégâts reçus ÷2", "damage taken ÷2"),
    "doux": ("dégâts infligés ÷2", "damage dealt ÷2"),
    "nage": ("nage ×1,5", "swimming ×1.5"),
    "bateau": ("bateau ×1,5", "boat ×1.5"),
    "magie": ("magie illimitée", "unlimited magic"),
    "vitesse": ("vitesse au sol ×1,5", "running ×1.5"),
    "dos": ("épée dans le dos ×3", "sword in the back ×3"),
    "esquive": ("10 % d'esquive", "10% dodge"),
    "fragile": ("dégâts reçus ×2", "damage taken ×2"),
    "munitions": ("munitions illimitées", "unlimited ammo"),
    "gadgets": ("gadgets ×2", "gadgets ×2"),
    "epee": ("épée ×1,5", "sword ×1.5"),
}
SURNOMS = {"rouge": ("tank", "tank"), "jaune": ("assassin", "assassin"), "sombre": ("noir", "dark")}

# facteurs (huitièmes) et bits de cfg du mod (jeu/mailbox.py : TEN_FACTEURS, TEN_*)
CFG_ALLUME, CFG_MAGIE, CFG_MUNITIONS = 0x1, 0x2, 0x4
ESQUIVE_PC = 10


def code_couleur(c) -> str | None:
    n = couleurs.normaliser(c)
    return couleurs.CODES[n - 1] if n else None


def pouvoirs_de(c) -> tuple[str, ...]:
    """Pouvoirs de la couleur ``c`` (1..8 ou code) ; () : aucune couleur."""
    k = code_couleur(c)
    return POUVOIRS.get(k, ()) if k else ()


def duo_de(c) -> int:
    """Couleur (1..8) du partenaire de duo de ``c`` ; 0 : pas de duo."""
    k = code_couleur(c)
    d = DUOS.get(k or "")
    return couleurs.CODES.index(d) + 1 if d else 0


def effectifs(c, partage: bool) -> tuple[str, ...]:
    """Pouvoirs effectifs : ceux de la couleur, plus ceux du partenaire de duo quand le partage est actif."""
    p = list(pouvoirs_de(c))
    if partage and duo_de(c):
        p += [x for x in pouvoirs_de(duo_de(c)) if x not in p]
    return tuple(p)


def valides(pouvoirs) -> tuple[str, ...]:
    """Liste reçue (réseau) -> pouvoirs connus, sans doublon, dans l'ordre de ``TOUS``."""
    if not isinstance(pouvoirs, (list, tuple)):
        return ()
    vus = {p for p in pouvoirs if isinstance(p, str)}
    return tuple(p for p in TOUS if p in vus)


def facteurs(pouvoirs) -> dict[str, int]:
    """Facteurs du mod (huitièmes ; 8 = x1) pour ces pouvoirs ; les absents valent 8 (esquive : 0 %)."""
    p = set(valides(pouvoirs))
    recu = 8.0 * (0.5 if "solide" in p else 1.0) * (2.0 if "fragile" in p else 1.0)
    doux = 0.5 if "doux" in p else 1.0
    return {
        "recu": int(recu),
        "epee": int(8 * (1.5 if "epee" in p else 1.0) * doux),
        "gadget": int(8 * (2.0 if "gadgets" in p else 1.0) * doux),
        "dos": 24 if "dos" in p else 8,
        "sol": 12 if "vitesse" in p else 8,
        "nage": 12 if "nage" in p else 8,
        "bateau": 12 if "bateau" in p else 8,
        "rubis": 12 if "rubis" in p else 8,
        "prix": 6 if "prix" in p else 8,
        "esquive": ESQUIVE_PC if "esquive" in p else 0,
    }


def cfg(pouvoirs, on: bool = True) -> int:
    """cfg du mod : allumé (si ``on`` et au moins un pouvoir), magie, munitions."""
    p = set(valides(pouvoirs))
    if not on or not p:
        return 0
    return CFG_ALLUME | (CFG_MAGIE if "magie" in p else 0) | (CFG_MUNITIONS if "munitions" in p else 0)


def neutre(f: dict) -> bool:
    return all(f.get(k, 8) == 8 for k in ("recu", "epee", "gadget", "dos", "sol", "nage", "bateau", "rubis", "prix")) \
        and not f.get("esquive")


def texte(pouvoirs, langue: str = "fr") -> str:
    """« rubis ramassés ×1,5, boutiques -25 % » ; « aucun pouvoir »."""
    k = 1 if langue == "en" else 0
    p = valides(pouvoirs)
    if not p:
        return "no power" if k else "aucun pouvoir"
    return ", ".join(NOMS[x][k] for x in p)


def texte_couleur(c, langue: str = "fr") -> str:
    """Pouvoir d'une couleur pour le lobby : « Tank : dégâts reçus ÷2, dégâts infligés ÷2 (duo : …) »."""
    k = code_couleur(c)
    if not k:
        return ""
    en = langue == "en"
    t = texte(POUVOIRS[k], langue)
    s = SURNOMS.get(k)
    if s:
        t = f"{s[1].capitalize()}: {t}" if en else f"{s[0].capitalize()} : {t}"
    d = DUOS.get(k)
    if d:
        nom = couleurs.nom(couleurs.CODES.index(d) + 1, langue)
        t += f" (duo with {nom}: shared when close)" if en else f" (duo avec {nom} : partagés quand proches)"
    return t


def proches(a: dict | None, b: dict | None, distance: float = DISTANCE) -> bool:
    """Même étape, même salle, à moins de ``distance`` (positions finies)."""
    if not a or not b:
        return False
    za, zb = a.get("zone"), b.get("zone")
    if not isinstance(za, str) or za != zb:
        return False
    pa, pb = pos3(a.get("pos")), pos3(b.get("pos"))
    if pa is None or pb is None:
        return False
    return math.dist(pa, pb) <= distance


def pos3(v):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    return p if all(math.isfinite(x) and abs(x) < 1e6 for x in p) else None


def distance_ok(v) -> float:
    """Distance réglable (u) bornée ; défaut 20 m."""
    try:
        d = float(v)
    except (TypeError, ValueError):
        return DISTANCE
    if not math.isfinite(d):
        return DISTANCE
    return min(DISTANCE_MAX, max(DISTANCE_MIN, d))


def facteurs_hit(f: dict | None) -> list[int] | None:
    """[épée, gadgets, dos] d'un attaquant pour le coup rejoué chez l'autorité ; None : rien à changer."""
    if not isinstance(f, dict):
        return None
    v = [f.get("epee", 8), f.get("gadget", 8), f.get("dos", 8)]
    if v == [8, 8, 8]:
        return None
    return v


def valid_hit(v) -> list[int] | None:
    """Champ « ten » d'un coup reçu : trois entiers 1..255, sinon None."""
    if not isinstance(v, list) or len(v) != 3:
        return None
    if not all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= 255 for x in v):
        return None
    return list(v)
