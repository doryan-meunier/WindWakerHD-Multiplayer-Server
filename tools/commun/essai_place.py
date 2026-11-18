"""Faux coéquipiers des essais solo : leur place est reprise quand elle est périmée.

Banc d'essai du 2026-11-18 : en arrivant dans une étape, le « state » de Link garde encore quelques images la
position d'avant le chargement ; le faux coéquipier posé à ce moment restait à 2 km de Link (invisible, impossible
d'approcher) tant que la salle ne changeait pas. Une place à plus de ``LOIN`` u de Link est donc reprise.
"""
from __future__ import annotations

import math

LOIN = 3000.0          # u (30 m) : au-delà, la place du faux coéquipier est reprise devant Link


def perimee(place: dict | None, st: dict | None, loin: float = LOIN) -> bool:
    """Vrai si ``place`` (dict avec « pos ») est à plus de ``loin`` u (plan horizontal) de la position de Link."""
    try:
        px, _py, pz = (float(v) for v in place["pos"][:3])
        lx, _ly, lz = (float(v) for v in st["pos"][:3])
    except (TypeError, KeyError, ValueError, IndexError):
        return False
    return math.hypot(px - lx, pz - lz) > loin
