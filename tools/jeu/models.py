"""Recherche générique des J3DModel d'un acteur (étape 5b) et pose relative à l'acteur.

Un acteur garde ses modèles soit directement (champ J3DModel*), soit via un objet
intermédiaire (ex. mDoExt_McaMorf* → J3DModel*). On cherche, en un ou deux niveaux,
les pointeurs vers un J3DModel plausible (J3DModelData à +0xAC avec un nombre
d'articulations raisonnable, tableau de matrices monde à +0x40) dont les
articulations sont près de l'acteur. L'ordre (offset de champ, puis offset
intermédiaire) est le même sur toutes les machines pour une même espèce.
"""
from __future__ import annotations

import struct

import numpy as np

from jeu import pose

MODEL_DATA, JOINTS_PTR, BASE_MTX = 0xAC, 0x40, 0xC8


def _is_heap(p: int) -> bool:
    return 0x10000000 <= p < 0x50000000


def _model_info(mem, p: int, near, radius: float):
    """(nb d'articulations) si p est un J3DModel dont les articulations sont près de `near`."""
    raw = mem.try_read(p + MODEL_DATA, 4)
    if not raw:
        return None
    data = struct.unpack(">I", raw)[0]
    if not _is_heap(data):
        return None
    nj_raw = mem.try_read(data + 6, 2)
    if not nj_raw:
        return None
    nj = struct.unpack(">H", nj_raw)[0]
    if not 1 <= nj <= 128:
        return None
    arr_raw = mem.try_read(p + JOINTS_PTR, 4)
    if not arr_raw:
        return None
    arr = struct.unpack(">I", arr_raw)[0]
    m = mem.try_read(arr, nj * 48) if _is_heap(arr) else None
    if not m:
        return None
    w = np.frombuffer(m, dtype=">f4").reshape(nj, 12)
    if not np.all(np.isfinite(w)):
        return None
    t = w[:, [3, 7, 11]].astype(np.float64)
    if np.max(np.linalg.norm(t - np.asarray(near), axis=1)) > radius:
        return None
    return nj


def find_models(mem, actor: int, pos, size: int = 0x1400, radius: float = 600.0,
                sub_size: int = 0x40) -> list[tuple[str, int, int]]:
    """[(chemin, J3DModel*, nb articulations)] ; chemin = '0x2E0' ou '0x2E0>0x4'."""
    out, seen = [], set()
    raw = mem.try_read(actor, size) or b""
    for o in range(0, len(raw), 4):
        p = struct.unpack_from(">I", raw, o)[0]
        if not _is_heap(p) or p in seen or actor <= p < actor + size:
            continue
        nj = _model_info(mem, p, pos, radius)
        if nj:
            seen.add(p)
            out.append((f"0x{o:X}", p, nj))
            continue
        sub = mem.try_read(p, sub_size)
        if not sub:
            continue
        for so in range(0, len(sub), 4):
            q = struct.unpack_from(">I", sub, so)[0]
            if not _is_heap(q) or q in seen:
                continue
            nj = _model_info(mem, q, pos, radius)
            if nj:
                seen.add(q)
                out.append((f"0x{o:X}>0x{so:X}", q, nj))
    return out


def resolve(mem, actor: int, path: str) -> int | None:
    parts = [int(x, 16) for x in path.split(">")]
    p = mem.u32(actor + parts[0])
    for so in parts[1:]:
        if not _is_heap(p):
            return None
        p = mem.u32(p + so)
    return p if _is_heap(p) else None


def relative_models(mem, actor: int, pos, rot_y: int, paths: list[tuple[str, int]]) -> dict[str, str]:
    """{chemin: pose encodée} : matrices des articulations relatives à la base de l'acteur."""
    binv = np.linalg.inv(pose.base_matrix(pos, rot_y))
    out = {}
    for path, nj in paths:
        model = resolve(mem, actor, path)
        if not model:
            continue
        raw = mem.try_read(mem.u32(model + JOINTS_PTR), nj * 48)
        if not raw:
            continue
        w = np.tile(np.eye(4), (nj, 1, 1))
        w[:, :3, :] = np.frombuffer(raw, dtype=">f4").astype(np.float64).reshape(nj, 3, 4)
        try:
            out[path] = pose.encode((binv @ w)[:, :3, :].astype(np.float32))
        except ValueError:                  # matrices périmées hors du float16 : modèle pas envoyé cette fois
            continue
    return out


# Bateau (proc 165) [LIVE] : chemins de ses 3 modèles depuis l'acteur. La fouille
# générique les trouve, mais en parcourant 0xD198 octets ; on les fige ici.
SHIP_PATHS = ["0x5B8", "0x3B8>0x90", "0xF4>0x1C0"]
MBX_MODEL_SPEC = 0x2F0          # graphic pack v9 : 8 J3DModelData* pour WwmpModel
GHOST_MODELS, GHOST_MASK, CULL_MTX = 0x3D0, 0x3C8, 0x400


def ship_paths(mem, ship: int) -> list[tuple[str, int]]:
    """[(chemin, nb articulations)] des modèles du bateau, vérifiés."""
    out = []
    for path in SHIP_PATHS:
        model = resolve(mem, ship, path)
        if model and _is_heap(mem.u32(model + MODEL_DATA)):
            out.append((path, mem.u16(mem.u32(model + MODEL_DATA) + 6)))
    return out


def ship_model_data(mem, ship: int) -> list[int]:
    """J3DModelData* des modèles du bateau LOCAL (pour créer un bateau fantôme)."""
    return [mem.u32(resolve(mem, ship, path) + MODEL_DATA) if resolve(mem, ship, path) else 0
            for path in SHIP_PATHS]


def apply_ghost_models(mem, ghost: int, pos, rot_y: int, poses: dict[str, str], paths: list[str]) -> int:
    """Écrit les poses reçues dans les modèles d'un WwmpModel (emplacement i = paths[i])."""
    g = pose.base_matrix(pos, rot_y)
    mask, n = 0, 0
    for i, path in enumerate(paths):
        data = poses.get(path)
        model = mem.u32(ghost + GHOST_MODELS + 4 * i)
        if not data or not model:
            continue
        rel = pose.decode(data)
        if rel.shape[0] != mem.u16(mem.u32(model + MODEL_DATA) + 6):
            continue
        r = np.tile(np.eye(4), (rel.shape[0], 1, 1))
        r[:, :3, :] = rel
        mem.write(mem.u32(model + JOINTS_PTR), (g @ r)[:, :3, :].astype(">f4").tobytes())
        mask |= 1 << i
        n += 1
    mem.w_u32(ghost + GHOST_MASK, mask)
    mem.write(ghost + 0x314, struct.pack(">3f", *pos))   # position (culling)
    return n


MBX_MODEL_SRC, MBX_MODEL_SRC_SIZE, MBX_MODEL_CALC = 0x310, 0x314, 0x318   # graphic pack v10


def place_ghost_calc(mem, ghost: int, pos, rot_y: int, n_models: int) -> None:
    """Mode recalcul (v10) : on ne donne que la position/rotation ; le jeu recalcule les
    articulations (animation du bateau LOCAL) et les sommets pondérés, coque comprise."""
    mem.write(ghost + 0x314, struct.pack(">3f", *pos))
    mem.write(ghost + 0x32A, struct.pack(">h", int(rot_y)))
    mem.w_u32(ghost + GHOST_MASK, (1 << n_models) - 1)


def apply_models(mem, actor: int, pos, rot_y: int, poses: dict[str, str]) -> int:
    g = pose.base_matrix(pos, rot_y)
    n = 0
    for path, data in poses.items():
        model = resolve(mem, actor, path)
        if not model:
            continue
        rel = pose.decode(data)
        if rel.shape[0] != mem.u16(mem.u32(model + MODEL_DATA) + 6):
            continue
        r = np.tile(np.eye(4), (rel.shape[0], 1, 1))
        r[:, :3, :] = rel
        mem.write(mem.u32(model + JOINTS_PTR), (g @ r)[:, :3, :].astype(">f4").tobytes())
        n += 1
    return n
