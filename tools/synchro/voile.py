"""Voile, mât et gouvernail du bateau fantôme (bit 22 des capacités ; docs/etudes/BATEAU_TETE.md §6).

Constat en jeu (2026-11-13) : la voile de l'autre joueur ne se voyait pas sur son bateau fantôme, et la NÔTRE y
apparaissait quand on la hissait. Preuve dans le RPX (lecture seule, tests/test_bateau_voile.py) :

- le mât, la vergue (sail1, sail2, sail_e) et la barre (kaji, steer1) sont les articulations 5 à 10 de la coque
  (11 articulations : allroot, skelroot, main1, futa, gattai, kaji, mast, sail1, sail2, sail_e, steer1). Leur rappel
  (0x0247187C -> 0x02471298) lit l'acteur dans userArea, c'est-à-dire, pour le fantôme, une COPIE de notre bateau :
  angle de barre +0x64E, angle de voile +0x64C, échelle du mât +0x6CC, et la matrice de NOTRE coque (+0x3B4 > +0x90) ;
  l'animation du mât (BCK, McaMorf partagé) est aussi celle de notre bateau ;
- la toile est un acteur à part, Grid (proc 171), posé à chaque image par le bateau sur sail1 : position = sail1,
  angle = celui du bateau (+0x320..), taille +0x334 = |sail1·(0, 0, -365)| / 365, repli +0x2B78 =
  1 - |sail2·(265, 0, 0)| / 265 (0x02479F28..0x0247A048) ; dessinée seulement si sa taille ≥ 0,06, tournée en plus de
  l'angle de voile du bateau LOCAL (l_ship = *(0x1046438C), +0x64C ; 0x02162E68). Le bateau fantôme n'en avait aucune.

Correction :
- émetteur : ``state.ship.v = {"j": matrices relatives des articulations 5 à 10 (float16, comme ``m``), "a": angle
  de la voile}``, à chaque envoi (``m`` ne part qu'une fois par seconde) ;
- receveur, pack avec le bit 22 : les 6 matrices vont dans le WwmpModel (+0xD710, état +0xD700 = « VOIL ») ; le pack
  pose ces articulations sur la coque du fantôme (base · relative), sans plus rien lire ni écrire de notre bateau ;
- receveur : une toile (acteur Grid) par bateau fantôme, créée la première fois que la voile distante est hissée,
  posée par le PC à chaque écriture du bateau : sail1 du fantôme, taille et repli d'après les matrices reçues, angle
  = cap du fantôme + voile distante - voile LOCALE (le jeu y ajoute la nôtre au dessin).
Sans ``v`` (ancien client en face) : état 0, le pack replie le mât du fantôme (rien de notre voile) et la toile est
cachée (taille 0).
"""
from __future__ import annotations

import struct

import numpy as np

from jeu import pose

ARTS = tuple(range(5, 11))          # kaji, mast, sail1, sail2, sail_e, steer1
N_ARTS = len(ARTS)
COQUE = "0xF4>0x1C0"                # = +0x3B4 > +0x90 (McaMorf de la coque), 11 articulations [LIVE]
COQUE_ARTS = 11
SAIL1, SAIL2 = ARTS.index(7), ARTS.index(8)
BATEAU_VOILE = 0x64C                # s16 angle de la voile (daShip, GC mSailAngle 0x364 + 0x2E8)

# WwmpModel (pack, bit 22 ; taille du profil 0xD880)
ETAT = 0xD700
REL = 0xD710
MAGIE = 0x564F494C                  # « VOIL »
FAUX_MODELE = 0xD784                # copie+0x418/+0x41C : seuls +0xBC..+0xF7 sont écrits par le jeu
TAILLE_PROFIL = 0xD880

# Grid (toile du bateau) [HD-ASM]
GRILLE_PROC, GRILLE_PARAM = 171, 1
G_POS, G_ANGLE, G_ECHELLE_Y, G_REPLI = 0x314, 0x320, 0x334, 0x2B78
SEUIL = 0.06                        # daGrid_c::_draw / _execute : rien sous cette taille
HAUT_VOILE = np.array([0.0, 0.0, -365.0])
LARGE_VOILE = np.array([265.0, 0.0, 0.0])


def _s16(v: int) -> int:
    v = int(v) & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


# -- émetteur ----------------------------------------------------------------------------------------------------
def emettre(mem, bateau: int, pos, rot_y: int) -> dict | None:
    """``state.ship.v`` : matrices relatives (base = pos, rot_y, comme ``models.relative_models``) des articulations
    5 à 10 de notre coque, et angle de notre voile. None si la coque est illisible ou douteuse."""
    from jeu import models
    modele = models.resolve(mem, bateau, COQUE)
    if not modele:
        return None
    data = mem.u32(modele + models.MODEL_DATA)
    if not models._is_heap(data) or mem.u16(data + 6) != COQUE_ARTS:
        return None
    arr = mem.u32(modele + models.JOINTS_PTR)
    raw = mem.try_read(arr, COQUE_ARTS * 48) if models._is_heap(arr) else None
    if not raw:
        return None
    w = np.tile(np.eye(4), (N_ARTS, 1, 1))
    w[:, :3, :] = np.frombuffer(raw, dtype=">f4").astype(np.float64).reshape(COQUE_ARTS, 3, 4)[ARTS[0]:ARTS[-1] + 1]
    rel = np.linalg.inv(pose.base_matrix(pos, rot_y)) @ w
    try:
        j = pose.encode(rel[:, :3, :].astype(np.float32))
    except ValueError:                  # matrices périmées hors du float16 : rien cette fois
        return None
    return {"j": j, "a": _s16(mem.u16(bateau + BATEAU_VOILE))}


# -- receveur ----------------------------------------------------------------------------------------------------
def lire(v) -> dict | None:
    """``state.ship.v`` reçu -> {"rel": 6×3×4 float32, "a": angle, "taille": échelle y de la toile, "repli": …},
    ou None (absent, illisible, valeurs non finies : on n'écrit alors rien de lui)."""
    if not isinstance(v, dict) or not isinstance(v.get("j"), str):
        return None
    try:
        rel = pose.decode(v["j"])
    except Exception:
        return None
    if rel.shape != (N_ARTS, 3, 4) or not np.all(np.isfinite(rel)):
        return None
    a = v.get("a", 0)
    if isinstance(a, bool) or not isinstance(a, int):
        return None
    r = rel.astype(np.float64)
    taille = float(np.linalg.norm(r[SAIL1, :, :3] @ HAUT_VOILE) / 365.0)
    repli = float(1.0 - np.linalg.norm(r[SAIL2, :, :3] @ LARGE_VOILE) / 265.0)
    if not (np.isfinite(taille) and np.isfinite(repli)):
        return None
    return {"rel": rel.astype(np.float32), "a": _s16(a), "taille": taille, "repli": repli}


def ecrire_fantome(mem, fantome: int, etat: dict | None) -> None:
    """Matrices relatives dans le WwmpModel (pack, bit 22), état écrit APRÈS ; sans état : 0 (mât replié)."""
    if etat is None:
        mem.w_u32(fantome + ETAT, 0)
        return
    mem.write(fantome + REL, etat["rel"].astype(">f4").tobytes())
    mem.w_u32(fantome + ETAT, MAGIE)


def pose_grille(pos, rot_y: int, etat: dict | None, voile_locale: int) -> tuple:
    """(position, angle y, taille, repli) de la toile du fantôme. Taille 0 sans état (toile cachée)."""
    if etat is None:
        return tuple(float(x) for x in pos), _s16(rot_y), 0.0, 1.0
    r = np.eye(4)
    r[:3, :] = etat["rel"][SAIL1]
    p = (pose.base_matrix(pos, rot_y) @ r)[:3, 3]
    return (tuple(float(x) for x in p), _s16(int(rot_y) + etat["a"] - int(voile_locale)), etat["taille"],
            etat["repli"])


def ecrire_grille(mem, grille: int, pos, rot_y: int, etat: dict | None, voile_locale: int) -> None:
    p, ay, taille, repli = pose_grille(pos, rot_y, etat, voile_locale)
    mem.write(grille + G_POS, struct.pack(">3f", *p))
    mem.write(grille + G_ANGLE, struct.pack(">3h", 0, ay, 0))
    mem.write(grille + G_ECHELLE_Y, struct.pack(">f", taille))
    mem.write(grille + G_REPLI, struct.pack(">f", repli))


def voile_locale(mem, bateau: int | None) -> int:
    """Angle de la voile de NOTRE bateau (le dessin de la toile l'ajoute ; 0 si pas de bateau)."""
    if not bateau:
        return 0
    try:
        return _s16(mem.u16(bateau + BATEAU_VOILE))
    except Exception:
        return 0
