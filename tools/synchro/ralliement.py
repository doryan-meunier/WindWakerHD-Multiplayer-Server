"""Chant de ralliement (docs/etudes/autres_zelda.md §7) : ce que le serveur (serveur/ralliement.py) et le client
(client/ralliement.py) partagent : constantes, vérifications des messages, places d'arrivée. Aucune entrée/sortie.
"""
from __future__ import annotations

import math

from synchro import boss as boss_mod

FEAT = "ralliement"         # hello : ce PC sait être appelé (CALL 15 PLACER / ALLER)
DELAI = 300.0               # s entre deux chants acceptés d'un même joueur (5 min)
REFUS = 10.0                # s : un appelé peut refuser pendant ce temps
RAYON = 200.0               # u : cercle d'arrivée autour de l'appelant (2 m)
ESSAI_DELAI = 15.0          # ESSAI : délai entre deux chants (et deux appels du faux coéquipier)
ID_ESSAI = 95               # ESSAI : faux coéquipier (pas ceux des essais porter 98, être porté 97, coups 96)
NOM_ESSAI = "Essai Rallié"


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def est_id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def pos3(v) -> tuple[float, float, float] | None:
    """Trois nombres finis de moins de 1e6 (les bornes de CALL 15), sinon None."""
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(_num(x) for x in v):
        return None
    p = tuple(float(x) for x in v)
    return p if all(abs(x) < 1e6 for x in p) else None


def etape_ok(s) -> bool:
    """Nom d'étape que CALL 15 ALLER accepte (1 à 7 caractères ASCII visibles, ni « : » ni « / »)."""
    return isinstance(s, str) and 1 <= len(s) <= 7 and all(0x21 <= ord(c) <= 0x7E and c not in ":/" for c in s)


def salle_ok(r) -> bool:
    return est_id(r) and 0 <= r <= 63


def salle_de_boss(stage) -> bool:
    """Étape d'un combat de boss (synchro/boss.py BOSS_STAGES) : jamais de ralliement depuis ni vers elle."""
    return isinstance(stage, str) and stage in boss_mod.BOSS_STAGES


def place(centre, k: int, n: int, rot: int = 0, rayon: float = RAYON) -> list[float]:
    """Place du k-ième appelé (sur n) : cercle de ``rayon`` autour de ``centre``, en commençant DERRIÈRE l'appelant
    (angle y du jeu : 0 = +Z, 0x4000 = +X) pour ne pas arriver devant lui, dans ce qu'il regarde."""
    a = ((int(rot) & 0xFFFF) + 0x8000) * math.pi / 32768.0 + 2.0 * math.pi * k / max(1, n)
    x, y, z = centre
    return [round(x + rayon * math.sin(a), 1), round(y, 1), round(z + rayon * math.cos(a), 1)]


def angle_vers(depuis, vers) -> int:
    """Angle y (s16) pour regarder ``vers`` depuis ``depuis``."""
    a = int(round(math.atan2(vers[0] - depuis[0], vers[2] - depuis[2]) * 32768.0 / math.pi)) & 0xFFFF
    return a - 0x10000 if a >= 0x8000 else a
