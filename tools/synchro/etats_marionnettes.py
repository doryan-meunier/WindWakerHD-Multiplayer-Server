"""États propres à l'espèce des marionnettes : ailes de Mothula, flamme du Bubble (mod natif 0.29.0, zone WWMPETA1).

Le problème (docs/dev/SYNCHRO.md, « Mothula (204) mini-boss à fin locale, ailes non synchronisées », « Bubble (207)
flamme non synchronisée ») : chez le joueur qui n'a pas l'autorité de la zone, les monstres sont des marionnettes
(execute sauté par le mod, position et articulations écrites par le PC). Ce que l'execute fait d'autre restait figé :
les ailes coupées de Mothula restaient dessinées et ne tombaient pas, la flamme du Bubble ne s'éteignait ni ne se
rallumait et son émetteur restait là où l'IA s'était arrêtée.

Ce module (logique pure, testée sans jeu par tests/test_natif_etats.py) :

* chez l'autorité, ``releve`` lit l'état visible de chaque Mothula ou Bubble envoyé (champ « et » des ennemis, liste
  de 8 nombres, message « enemies » existant ; le serveur relaie la liste telle quelle) :
  - Mothula (corps, +0x3E9 == 0) : ailes coupées (+0x3E8 bits 0-3), celles de type 2 (relevées sur les acteurs des
    ailes eux-mêmes : paramètres (aile + 1) | 0x200, voir ``types_ailes``), animation de matériau (+0x440) et son image
    (+4 de l'animation choisie), décalage du corps (+0x4D0) ;
  - Bubble : flamme allumée (émetteur +0x81C non nul et aucun décompte d'extinction +0x40C), allure (+0x3EF) et
    l'image de l'animation de matériau qu'elle choisit ;
* chez les autres, ``valider`` et ``entree`` donnent l'entrée de l'emplacement de la marionnette dans la zone
  WWMPETA1 ; le jeu l'applique à la place de l'execute sauté, en appelant SES fonctions pour les effets
  (natif/mod_wwmp/etats.c : acteur de l'aile qui tombe et sa particule, allumage, extinction et suivi de la flamme) ;
* ``sans_ailes`` : les ailes coupées (acteurs Mothula à part, qui tombent et disparaissent) ne sont plus relayées ;
  chaque PC a les siennes, créées par son propre jeu (chez la marionnette : par le mod, à la coupe).

La fin du mini-boss (porte) passe par synchro/canons.py (switch de mort +0x3EB de Mothula, posé juste avant que le
client supprime la marionnette tuée chez l'autorité).
"""
from __future__ import annotations

import math
import struct

PROC_GM, PROC_BL = 204, 207
GENRE_MOTHULA, GENRE_BUBBLE = 1, 2
GENRES = {PROC_GM: GENRE_MOTHULA, PROC_BL: GENRE_BUBBLE}

# --- Mothula [HD-ASM, relu par tests/test_natif_etats.py TestRpx] ---------------------------------------------------
GM_BLOC, GM_TAILLE = 0x3D8, 0x104      # une lecture : animations +0x3D8..+0x3E4 jusqu'au décalage +0x4D0..+0x4DB
GM_AILES, GM_AILE, GM_ANIM, GM_DECALAGE = 0x3E8, 0x3E9, 0x440, 0x4D0
GM_ANIMS = {1: 0x3E0, 2: 0x3DC, 3: 0x3E4}   # +0x440 -> animation (sinon +0x3D8), comme le draw 0x0214C0FC
# --- Bubble ------------------------------------------------------------------------------------------------------
BL_BLOC, BL_TAILLE = 0x3D8, 0x38       # animations +0x3D8 / +0x3DC, allure +0x3EF, décompte +0x40C
BL_ALLURE, BL_DECOMPTE, BL_EMETTEUR = 0x3EF, 0x40C, 0x81C
ANIM_IMAGE = 4                         # image (f32) d'une animation de matériau (draw : lfs f1, 4(anim))

ENTREE_TAILLE = 0x14                   # sans le mot « vu » (+0x14, écrit par le jeu)
N = 8                                  # nombres du champ « et »


def est_aile(proc: int, params: int) -> bool:
    """Acteur Mothula qui est une aile coupée (create 0x02150844 : (params >> 8) & 0xFF dans +0x3E9, 0xFF ramené à 0)."""
    return proc == PROC_GM and (params >> 8) & 0xFF not in (0, 0xFF)


def sans_ailes(acteurs, proc_of=lambda a: a.proc_name, params_of=lambda a: a.params) -> list:
    return [a for a in acteurs if not est_aile(proc_of(a), params_of(a))]


def types_ailes(acteurs, proc_of=lambda a: a.proc_name, params_of=lambda a: a.params,
                salle_of=lambda a: a.home_room) -> dict:
    """{salle: masque des ailes de type 2} d'après les ailes coupées vivantes (paramètres (aile + 1) | 0x200)."""
    out: dict = {}
    for a in acteurs:
        p = params_of(a)
        if not est_aile(proc_of(a), p):
            continue
        k = (p & 0xFF) - 1
        if (p >> 8) & 0xFF == 2 and 0 <= k < 4:
            s = salle_of(a)
            out[s] = out.get(s, 0) | (1 << k)
    return out


def _lire(mem, a: int, n: int) -> bytes | None:
    try:
        raw = mem.read(a, n)
    except Exception:
        return None
    return bytes(raw) if raw is not None and len(raw) == n else None


def _image(mem, anim: int) -> float:
    if not anim:
        return 0.0
    raw = _lire(mem, anim + ANIM_IMAGE, 4)
    if raw is None:
        return 0.0
    v = struct.unpack(">f", raw)[0]
    return v if math.isfinite(v) else 0.0


def releve(mem, base: int, proc: int, types2: int = 0) -> list | None:
    """Chez l'autorité : [genre, a, b, c, image, dx, dy, dz] du Mothula (corps) ou du Bubble à ``base``, ou None
    (autre espèce, aile coupée, lecture impossible). ``types2`` : masque des ailes de type 2 de sa salle."""
    if proc == PROC_GM:
        raw = _lire(mem, base + GM_BLOC, GM_TAILLE)
        if raw is None or raw[GM_AILE - GM_BLOC]:
            return None
        ailes = raw[GM_AILES - GM_BLOC] & 0xF
        choix = struct.unpack_from(">h", raw, GM_ANIM - GM_BLOC)[0]
        anim = struct.unpack_from(">I", raw, GM_ANIMS.get(choix, 0x3D8) - GM_BLOC)[0]
        dec = struct.unpack_from(">3f", raw, GM_DECALAGE - GM_BLOC)
        if not all(math.isfinite(v) for v in dec):
            return None
        return [GENRE_MOTHULA, ailes, types2 & ailes, choix & 0xFF, _image(mem, anim), *dec]
    if proc == PROC_BL:
        raw = _lire(mem, base + BL_BLOC, BL_TAILLE)
        em = _lire(mem, base + BL_EMETTEUR, 4)
        if raw is None or em is None:
            return None
        allure = raw[BL_ALLURE - BL_BLOC]
        decompte = struct.unpack_from(">h", raw, BL_DECOMPTE - BL_BLOC)[0]
        feu = 1 if struct.unpack(">I", em)[0] and decompte == 0 else 0
        anim = struct.unpack_from(">I", raw, (0x3DC if allure else 0x3D8) - BL_BLOC)[0]
        return [GENRE_BUBBLE, feu, allure, 0, _image(mem, anim), 0.0, 0.0, 0.0]
    return None


def _octet(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 0xFF


def _flottant(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and abs(x) < 1e9


def valider(et, proc=None) -> list | None:
    """Champ « et » reçu : liste normalisée, ou None (absent, mal formé, autre espèce que ``proc``)."""
    if not isinstance(et, list) or len(et) != N:
        return None
    g, a, b, c = et[:4]
    if not all(_octet(v) for v in (g, a, b, c)) or g not in (GENRE_MOTHULA, GENRE_BUBBLE):
        return None
    if proc is not None and GENRES.get(proc) != g:
        return None
    if not all(_flottant(v) for v in et[4:]):
        return None
    if g == GENRE_MOTHULA and (a > 0xF or b & ~a):
        return None
    if g == GENRE_BUBBLE and (a > 1 or c):
        return None
    return [g, a, b, c, *(float(v) for v in et[4:])]


def entree(et: list) -> bytes:
    """Entrée de la zone WWMPETA1 (0x14 octets, sans le mot « vu ») d'un état validé."""
    g, a, b, c, img, dx, dy, dz = et
    return struct.pack(">4B4f", g, a, b, c, img, dx, dy, dz)
