"""Textures GX2 (Wii U, GPU « Latte ») en Python pur : déswizzle, décodeurs BCn / RGBA8, encodeur PNG.

Sert au trackeur (lanceur/icones_jeu.py) : les icônes et la carte de la mer sont LUES dans le dump du joueur et
converties en PNG sur son PC, jamais distribuées. Aucune bibliothèque externe.

Adresse d'un élément (pixel, ou bloc 4x4 pour BCn) dans une surface tuilée, d'après l'AddrLib publique de la Wii U
(R600 : 2 pipes, 4 banks, groupe de 256 octets), pour la tranche 0 et l'échantillon 0 (textures 2D simples) :

* mode 1 (LINEAR_ALIGNED) : ligne après ligne, ``pitch`` éléments par ligne ;
* modes 2 et 3 (1D_TILED) : micro-tuiles 8x8 rangées ligne après ligne ;
* modes 4 et suivants (2D_TILED) : micro-tuiles regroupées en macro-tuiles (32x16 pour THIN1), avec pipe et bank
  calculés depuis (x, y) puis brouillés par le « swizzle » de la surface (bit 0 : pipe, bits 1-2 : bank).

Décodeurs : BC1 (DXT1), BC3 (DXT5), BC4 (une voie), BC5 (deux voies : rendues (R, G, 0, 255)), RGBA8.

Paquet ``fichiers_jeu`` : licence et attribution dans __init__.py (portage de TWWHD-Randomizer, SuperDude88,
MIT). Ce module-ci n'en est pas porté : il est écrit d'après la description publique du format et de l'AddrLib.
"""
from __future__ import annotations

import struct
import zlib

from jeu.fichiers import ErreurFormat

PIPES, BANKS = 2, 4
_GROUPE_BITS, _PIPE_BITS, _BANK_BITS = 8, 1, 2
MODES_PRIS = (1, 2, 3, 4)          # modes de tuilage décodés (4 = 2D_TILED_THIN1, celui de presque toutes les images)


def indice_pixel(x: int, y: int, bpp: int) -> int:
    """Rang (0 à 63) de l'élément (x, y) dans sa micro-tuile 8x8, selon la taille d'élément en bits."""
    x0, x1, x2 = x & 1, (x >> 1) & 1, (x >> 2) & 1
    y0, y1, y2 = y & 1, (y >> 1) & 1, (y >> 2) & 1
    if bpp == 8:
        b = (x0, x1, x2, y1, y0, y2)
    elif bpp == 16:
        b = (x0, x1, x2, y0, y1, y2)
    elif bpp in (32, 96):
        b = (x0, x1, y0, x2, y1, y2)
    elif bpp == 64:
        b = (x0, y0, x1, x2, y1, y2)
    elif bpp == 128:
        b = (y0, x0, x1, x2, y1, y2)
    else:
        raise ErreurFormat(f"GX2 : taille d'élément non prise en charge ({bpp} bits)")
    return b[0] | b[1] << 1 | b[2] << 2 | b[3] << 3 | b[4] << 4 | b[5] << 5


def adresse_micro(x: int, y: int, bpp: int, pitch: int) -> int:
    """Octet de l'élément (x, y) en mode 1D_TILED (micro-tuiles 8x8)."""
    micro = bpp * 64 // 8
    return micro * (x // 8 + (y // 8) * (pitch // 8)) + ((bpp * indice_pixel(x, y, bpp)) >> 3)


def adresse_macro(x: int, y: int, bpp: int, pitch: int, swizzle: int, mode: int = 4) -> int:
    """Octet de l'élément (x, y) en mode 2D_TILED. ``swizzle`` : 0 à 7 (bit 0 pipe, bits 1-2 bank)."""
    elem = (bpp * indice_pixel(x, y, bpp)) >> 3
    pipe = ((y >> 3) ^ (x >> 3)) & 1
    bank = (((y >> 5) ^ (x >> 3)) & 1) | (((y >> 4) ^ (x >> 4)) & 1) << 1
    bp = (pipe + PIPES * bank) ^ ((swizzle & 1) + PIPES * ((swizzle >> 1) & 3))
    bp %= PIPES * BANKS
    pipe, bank = bp % PIPES, bp // PIPES
    mt_l, mt_h = 8 * BANKS, 8 * PIPES                      # macro-tuile THIN1 : 32 x 16 éléments
    if mode in (5, 9):
        mt_l, mt_h = mt_l // 2, mt_h * 2
    elif mode in (6, 10):
        mt_l, mt_h = mt_l // 4, mt_h * 4
    mt_octets = (bpp * mt_h * mt_l + 7) // 8
    mt_dec = (x // mt_l + (pitch // mt_l) * (y // mt_h)) * mt_octets
    total = elem + (mt_dec >> (_BANK_BITS + _PIPE_BITS))
    masque = (1 << _GROUPE_BITS) - 1
    return (bank << (_PIPE_BITS + _GROUPE_BITS)) | (pipe << _GROUPE_BITS) | (total & masque) \
        | ((total & ~masque) << (_BANK_BITS + _PIPE_BITS))


def adresse(x: int, y: int, bpp: int, pitch: int, mode: int, swizzle: int = 0) -> int:
    if mode in (0, 1):
        return (y * pitch + x) * (bpp // 8)
    if mode in (2, 3):
        return adresse_micro(x, y, bpp, pitch)
    if 4 <= mode <= 15:
        return adresse_macro(x, y, bpp, pitch, swizzle, mode)
    raise ErreurFormat(f"GX2 : mode de tuilage inconnu ({mode})")


def hauteur_alignee(hauteur: int, mode: int) -> int:
    """Hauteur (en éléments) de la surface rangée : multiple de la hauteur de tuile."""
    pas = 16 if mode >= 4 else 8 if mode in (2, 3) else 1
    return -(-hauteur // pas) * pas


def deswizzle(data: bytes, largeur: int, hauteur: int, bpp: int, mode: int, swizzle: int, pitch: int) -> bytes:
    """Éléments rangés ligne après ligne (``largeur`` x ``hauteur`` éléments ; pour BCn, des blocs 4x4)."""
    if mode not in MODES_PRIS:
        raise ErreurFormat(f"GX2 : mode de tuilage non pris en charge ({mode})")
    if bpp % 8 or bpp <= 0:
        raise ErreurFormat(f"GX2 : taille d'élément invalide ({bpp})")
    bpe = bpp // 8
    sortie = bytearray(largeur * hauteur * bpe)
    n = len(data)
    o = 0
    for y in range(hauteur):
        for x in range(largeur):
            a = adresse(x, y, bpp, pitch, mode, swizzle)
            if a + bpe > n:
                raise ErreurFormat("GX2 : données de texture trop courtes")
            sortie[o:o + bpe] = data[a:a + bpe]
            o += bpe
    return bytes(sortie)


# --- décodeurs (entrée : éléments en ligne, sortie : RGBA 8 bits, largeur x hauteur pixels) --------------------------
def _c565(c: int) -> tuple[int, int, int]:
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)


def bloc_bc1(b: bytes, alpha_permis: bool = True) -> list[tuple[int, int, int, int]]:
    """16 pixels RGBA d'un bloc BC1 (8 octets), ligne après ligne."""
    c0, c1, bits = struct.unpack_from("<HHI", b, 0)
    p0, p1 = _c565(c0), _c565(c1)
    if c0 > c1 or not alpha_permis:
        p2 = tuple((2 * a + c) // 3 for a, c in zip(p0, p1))
        p3 = tuple((a + 2 * c) // 3 for a, c in zip(p0, p1))
        pal = [p0 + (255,), p1 + (255,), p2 + (255,), p3 + (255,)]
    else:
        p2 = tuple((a + c) // 2 for a, c in zip(p0, p1))
        pal = [p0 + (255,), p1 + (255,), p2 + (255,), (0, 0, 0, 0)]
    return [pal[(bits >> (2 * i)) & 3] for i in range(16)]


def bloc_bc4(b: bytes) -> list[int]:
    """16 valeurs (0 à 255) d'un bloc BC4 non signé (8 octets), ligne après ligne."""
    a0, a1 = b[0], b[1]
    bits = int.from_bytes(b[2:8], "little")
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[(bits >> (3 * i)) & 7] for i in range(16)]


FORMATS_DECODES = ("RGBA8", "BC1", "BC3", "BC4", "BC5")


def decoder(fmt: str, data: bytes, largeur: int, hauteur: int) -> bytes:
    """``data`` : éléments en ligne (pixels RGBA8, ou blocs). Retour : largeur*hauteur*4 octets RGBA.

    BC4 : (v, v, v, 255). BC5 : (r, g, 0, 255) (le sens des deux voies dépend de l'usage : voir bflim)."""
    if fmt == "RGBA8":
        n = largeur * hauteur * 4
        if len(data) < n:
            raise ErreurFormat("RGBA8 : données trop courtes")
        return bytes(data[:n])
    if fmt not in FORMATS_DECODES:
        raise ErreurFormat(f"format de texture non décodé : {fmt}")
    bl, bh = (largeur + 3) // 4, (hauteur + 3) // 4
    tb = 8 if fmt in ("BC1", "BC4") else 16
    if len(data) < bl * bh * tb:
        raise ErreurFormat(f"{fmt} : données trop courtes")
    sortie = bytearray(largeur * hauteur * 4)
    for by in range(bh):
        for bx in range(bl):
            k = (by * bl + bx) * tb
            b = data[k:k + tb]
            if fmt == "BC1":
                px = bloc_bc1(b)
            elif fmt == "BC3":
                al = bloc_bc4(b[:8])
                px = [c[:3] + (a,) for c, a in zip(bloc_bc1(b[8:], False), al)]
            elif fmt == "BC4":
                px = [(v, v, v, 255) for v in bloc_bc4(b)]
            else:                                         # BC5
                px = [(r, g, 0, 255) for r, g in zip(bloc_bc4(b[:8]), bloc_bc4(b[8:]))]
            for i, p in enumerate(px):
                x, y = bx * 4 + (i & 3), by * 4 + (i >> 2)
                if x < largeur and y < hauteur:
                    o = (y * largeur + x) * 4
                    sortie[o:o + 4] = bytes(p)
    return bytes(sortie)


# --- images RGBA -----------------------------------------------------------------------------------------------------
def reduire_moitie(rgba: bytes, largeur: int, hauteur: int) -> tuple[bytes, int, int]:
    """Moyenne de boîtes 2x2 pondérée par l'alpha (pas de liseré sombre autour des formes)."""
    l2, h2 = max(1, largeur // 2), max(1, hauteur // 2)
    sortie = bytearray(l2 * h2 * 4)
    for y in range(h2):
        for x in range(l2):
            r = g = b = a = 0
            for dy in (0, 1):
                yy = min(hauteur - 1, 2 * y + dy)
                for dx in (0, 1):
                    xx = min(largeur - 1, 2 * x + dx)
                    o = (yy * largeur + xx) * 4
                    aa = rgba[o + 3]
                    r += rgba[o] * aa
                    g += rgba[o + 1] * aa
                    b += rgba[o + 2] * aa
                    a += aa
            o = (y * l2 + x) * 4
            if a:
                sortie[o:o + 4] = bytes(((r + a // 2) // a, (g + a // 2) // a, (b + a // 2) // a, (a + 2) // 4))
    return bytes(sortie), l2, h2


def recadrer_carre(rgba: bytes, largeur: int, hauteur: int) -> tuple[bytes, int]:
    """Pose l'image au centre d'un carré transparent (côté = plus grande dimension)."""
    c = max(largeur, hauteur)
    if c == largeur == hauteur:
        return bytes(rgba), c
    sortie = bytearray(c * c * 4)
    ox, oy = (c - largeur) // 2, (c - hauteur) // 2
    for y in range(hauteur):
        d = ((oy + y) * c + ox) * 4
        sortie[d:d + largeur * 4] = rgba[y * largeur * 4:(y + 1) * largeur * 4]
    return bytes(sortie), c


def png(largeur: int, hauteur: int, rgba: bytes) -> bytes:
    """PNG RGBA 8 bits, sans métadonnée ni horodatage (mêmes pixels = mêmes octets)."""
    if len(rgba) != largeur * hauteur * 4 or largeur <= 0 or hauteur <= 0:
        raise ErreurFormat("PNG : dimensions et données incohérentes")
    brut = b"".join(b"\0" + rgba[y * largeur * 4:(y + 1) * largeur * 4] for y in range(hauteur))

    def morceau(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + morceau(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 6, 0, 0, 0))
            + morceau(b"IDAT", zlib.compress(brut, 9)) + morceau(b"IEND", b""))
