"""Pose de Link (étape 4c) : lecture des matrices d'articulations et application au fantôme.

[LIVE] Le J3DModel du corps de Link (daPy_lk_c+0x448) a 42 articulations
(J3DModelData+0x6). Après le calcul du jeu, leurs matrices monde (3×4 float,
big-endian) sont dans un tableau pointé par J3DModel+0x40 ; la matrice de base
(position/rotation du modèle) est à J3DModel+0xC8.

Une pose est transmise en matrices *relatives* à la matrice de base :
R_i = B⁻¹ · W_i. Le receveur reconstruit W'_i = G · R_i avec la matrice de base
G du fantôme, puis le fantôme est dessiné sans recalcul (mode de dessin 2).
"""
from __future__ import annotations

import base64
import struct

import numpy as np

N_JOINTS = 42
MODEL_JOINT_MTX_PTR = 0x40
MODEL_BASE_MTX = 0xC8
PY_BODY_MODEL = 0x448


def _to44(m12: np.ndarray) -> np.ndarray:
    m = np.eye(4, dtype=np.float64)
    m[:3, :4] = m12.reshape(3, 4)
    return m


def read_world(mem, model: int, n: int = N_JOINTS) -> tuple[np.ndarray, np.ndarray]:
    """(base 4×4, matrices monde n×4×4) d'un J3DModel."""
    base = np.frombuffer(mem.read(model + MODEL_BASE_MTX, 48), dtype=">f4").astype(np.float64)
    arr = mem.u32(model + MODEL_JOINT_MTX_PTR)
    raw = np.frombuffer(mem.read(arr, n * 48), dtype=">f4").astype(np.float64).reshape(n, 3, 4)
    w = np.tile(np.eye(4), (n, 1, 1))
    w[:, :3, :] = raw
    return _to44(base), w


def relative_pose(mem, model: int) -> np.ndarray:
    """Matrices relatives à la base, n×3×4 (float32), prêtes à envoyer."""
    b, w = read_world(mem, model)
    rel = np.linalg.inv(b) @ w
    return rel[:, :3, :].astype(np.float32)


def base_matrix(pos, rot_y: int) -> np.ndarray:
    """Matrice de base d'un acteur : translation · rotation Y (comme transS + ZXYrotM avec x = z = 0)."""
    a = rot_y * 2 * np.pi / 65536
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s, pos[0]], [0, 1, 0, pos[1]], [-s, 0, c, pos[2]], [0, 0, 0, 1]], dtype=np.float64)


def apply_pose(mem, model: int, rel: np.ndarray, pos=None, rot_y: int | None = None) -> None:
    """Écrit G·R_i dans le tableau de matrices du modèle fantôme. Si pos/rot_y sont
    donnés, G est calculée ici (pas de décalage d'une frame avec le jeu) ; sinon on
    lit la matrice de base du modèle."""
    if pos is not None and rot_y is not None:
        g = base_matrix(pos, rot_y)
    else:
        g = _to44(np.frombuffer(mem.read(model + MODEL_BASE_MTX, 48), dtype=">f4").astype(np.float64))
    r = np.tile(np.eye(4), (rel.shape[0], 1, 1))
    r[:, :3, :] = rel
    w = (g @ r)[:, :3, :].astype(">f4")
    arr = mem.u32(model + MODEL_JOINT_MTX_PTR)
    mem.write(arr, w.tobytes())


# Accessoires (étape 4d) [LIVE] : champs J3DModel* de daPy_lk_c, dans l'ordre de
# wwmp_att_table du graphic pack (le fantôme les range à +0x3D0 + 4·i).
ATTACH_FIELDS = [0x44C, 0x450, 0xCFC, 0xE7C, 0xE84, 0x43B4, 0x4440, 0x4978]   # 7 = objet tenu
HELD_INDEX = 7
MBX_RES_TABLE = 0xE0      # graphic pack v5 : adresse de la table des ressources "Link"
_res_table_cache: dict[int, int] = {}


def find_res_table(mem, py: int) -> int | None:
    """[LIVE] Table des J3DModelData de l'archive "Link" (115 entrées, ordre propre au jeu,
    identique sur toutes les machines). Repérée par : corps à [0], bouclier à [12]."""
    body = mem.u32(mem.u32(py + PY_BODY_MODEL) + MODEL_DATA)
    if body in _res_table_cache:
        return _res_table_cache[body]
    shield = mem.u32(mem.u32(py + 0xE84) + MODEL_DATA)
    for h in mem.find_u32(body):
        if mem.try_read(h + 0x30, 4) and mem.u32(h + 0x30) == shield:
            _res_table_cache[body] = h
            return h
    return None


MBX_ATT_SPEC = 0xE4       # graphic pack v6 : 8 octets, position dans la table + 1 (0 = rien)


def attachment_spec(mem, py: int) -> list[int]:
    """Pour chaque accessoire, position de son modèle actuel dans la table "Link" + 1.
    [LIVE] le joueur remplace certains modèles selon l'équipement (ex. +0x4440 : créé avec
    l'index 0x1B, la feuille, puis remplacé par l'épée = position 13)."""
    table = find_res_table(mem, py)
    if not table:
        return [0] * len(ATTACH_FIELDS)
    raw = np.frombuffer(mem.read(table, 115 * 4), dtype=">u4")
    spec = []
    for i, field in enumerate(ATTACH_FIELDS):
        model = mem.u32(py + field)
        hit = []
        if i not in ATTACH_SKIP and 0x10000000 <= model < 0x50000000:
            hit = np.nonzero(raw == mem.u32(model + MODEL_DATA))[0]
        spec.append(int(hit[0]) + 1 if len(hit) else 0)
    return spec


def held_slot(mem, py: int) -> int | None:
    """Position, dans la table "Link", du modèle de l'objet tenu (daPy+0x4978)."""
    table = find_res_table(mem, py)
    model = mem.u32(py + ATTACH_FIELDS[HELD_INDEX])
    if not table or not (0x10000000 <= model < 0x50000000):
        return None
    data = mem.u32(model + MODEL_DATA)
    raw = np.frombuffer(mem.read(table, 115 * 4), dtype=">u4")
    hit = np.nonzero(raw == data)[0]
    return int(hit[0]) if len(hit) else None
# [LIVE] exclus après essais visuels :
#  1 (index 0x29, daPy+0x450) : masque du Charme du héros (lunettes et barbe orange), que le
#    joueur ne dessine que lorsqu'il le porte (0x023D8EB0) ; ce ne sont PAS les pupilles.
#  0 (index 0x20, daPy+0x44C) : pièce de tête, exclue par prudence (pas d'apport visible).
# 6 (daPy+0x4440) est l'ÉPÉE : créée avec l'index 0x1B (feuille Mojo), puis remplacée par
# le jeu ; depuis la v6 le fantôme crée le même modèle que l'émetteur (spec de positions).
ATTACH_SKIP = {0, 1}
MBX_EYE_MODE = 0x31C   # yeux du fantôme : 0 aucun, 1 ZOn (v13), 2 passe du joueur (v15), 3 idem sans la liste 5D5C
GHOST_ATTACH = 0x3D0
GHOST_ATTACH_MASK = 0x3C8
MODELDATA_JOINT_NUM = 0x6
MODEL_DATA = 0xAC
VISIBLE_RADIUS = 250.0   # un accessoire non dessiné garde une matrice de base périmée, loin de Link


def joint_count(mem, model: int) -> int:
    return mem.u16(mem.u32(model + MODEL_DATA) + MODELDATA_JOINT_NUM)


def relative_attachments(mem, py: int) -> dict[str, str]:
    """{indice: pose encodée} des accessoires que le jeu dessine en ce moment."""
    body = mem.u32(py + PY_BODY_MODEL)
    b, _ = read_world(mem, body, 1)
    binv = np.linalg.inv(b)
    out = {}
    for i, field in enumerate(ATTACH_FIELDS):
        if i in ATTACH_SKIP:
            continue
        model = mem.u32(py + field)
        if not (0x10000000 <= model < 0x50000000):
            continue
        mb, w = read_world(mem, model, joint_count(mem, model))
        if np.linalg.norm(mb[:3, 3] - b[:3, 3]) > VISIBLE_RADIUS:
            continue
        rel = binv @ w
        # [LIVE] certains accessoires non dessinés ont une base à jour mais des articulations
        # périmées très loin : on exige que toutes les articulations soient près de Link.
        if not np.all(np.isfinite(rel)) or np.abs(rel[:, :3, 3]).max() > VISIBLE_RADIUS:
            continue
        out[str(i)] = encode(rel[:, :3, :].astype(np.float32))
    gaze = eye_gaze(mem, py)
    if gaze:
        out["eye"] = gaze      # 4g : regard (pas des matrices)
    return out


# Étape 4g (pack v16) : regard. [LIVE] La position des pupilles est la translation du
# texmtx1 des matériaux eyeL (8) et eyeR (11), dans les instances de matériaux PROPRES à
# chaque modèle (J3DModel+0x34, 0x3C octets, tampon de paramètres +0x28, translation +0x58).
EYE_MATERIALS = (8, 11)
MODEL_MAT_INST = 0x34
MAT_INST_SIZE = 0x3C
MAT_INST_PARAMS = 0x28
TEXMTX1_TRANS = 0x58
GHOST_GAZE = 0x86C0          # acteur fantôme : drapeau, puis 4 flottants (gauche x, y, droit x, y)
GAZE_MIN_VERSION = 16


def eye_gaze(mem, py: int) -> list[float] | None:
    """[gx, gy, dx, dy] : translations des pupilles du vrai Link."""
    body = mem.u32(py + PY_BODY_MODEL)
    inst = mem.u32(body + MODEL_MAT_INST)
    if not (0x10000000 <= inst < 0x50000000):
        return None
    out = []
    for i in EYE_MATERIALS:
        pb = mem.u32(inst + i * MAT_INST_SIZE + MAT_INST_PARAMS)
        if not (0x10000000 <= pb < 0x50000000):
            return None
        x, y = struct.unpack(">2f", mem.read(pb + TEXMTX1_TRANS, 8))
        if not (np.isfinite(x) and np.isfinite(y)) or abs(x) > 2 or abs(y) > 2:
            return None
        out += [round(float(x), 4), round(float(y), 4)]
    return out


def apply_gaze(mem, ghost: int, gaze) -> None:
    """Écrit le regard reçu dans l'acteur fantôme (lu par le crochet du pack v16)."""
    if not gaze or len(gaze) != 4:
        mem.w_u32(ghost + GHOST_GAZE, 0)
        return
    mem.write(ghost + GHOST_GAZE + 4, struct.pack(">4f", *map(float, gaze)))
    mem.w_u32(ghost + GHOST_GAZE, 1)


def apply_attachments(mem, ghost: int, atts: dict, pos, rot_y: int) -> None:
    g = base_matrix(pos, rot_y)
    mask = 0
    for k, data in (atts or {}).items():
        if not str(k).isdigit():
            continue          # ex. "held" : position de l'objet tenu, pas des matrices
        i = int(k)
        model = mem.u32(ghost + GHOST_ATTACH + 4 * i)
        if not model:
            continue
        rel = decode(data)
        if rel.shape[0] != joint_count(mem, model):
            continue
        r = np.tile(np.eye(4), (rel.shape[0], 1, 1))
        r[:, :3, :] = rel
        mem.write(mem.u32(model + MODEL_JOINT_MTX_PTR), (g @ r)[:, :3, :].astype(">f4").tobytes())
        mask |= 1 << i
    mem.w_u32(ghost + GHOST_ATTACH_MASK, mask)


F16_MAX = 60000.0         # float16 : 65504 au plus ; marge comme wwmp/batons.encode_rel


def encode(rel: np.ndarray) -> str:
    """Pour le réseau : float16 big-endian en base64 (42×12×2 = 1008 octets → 1344 caractères,
    contre ~4 Ko en JSON de flottants). Précision ~1e-3 sur les rotations, ~0,06 unité sur
    les translations (qui restent sous ~150 unités par rapport à la base)."""
    # [LIVE 2026-11-12] « RuntimeWarning: overflow encountered in cast » : une valeur hors du float16 (pose
    # périmée pendant un chargement) serait partie en inf et aurait cassé le fantôme chez les autres : on refuse
    # (les appelants n'envoient alors pas cette pose, comme pour toute autre erreur de lecture)
    if not np.all(np.isfinite(rel)) or np.abs(rel).max(initial=0.0) > F16_MAX:
        raise ValueError("pose hors de la plage du float16")
    return base64.b64encode(rel.astype(">f2").tobytes()).decode("ascii")


def decode(data) -> np.ndarray:
    if isinstance(data, str):
        return np.frombuffer(base64.b64decode(data), dtype=">f2").astype(np.float32).reshape(-1, 3, 4)
    return np.asarray(data, dtype=np.float32).reshape(-1, 3, 4)   # ancien format (liste)
