"""Images BFLIM de la Wii U (``timg/*.bflim`` des archives de mise en page) : lecture et conversion en RGBA.

Structure : les pixels (tuilage GX2) commencent à l'octet 0 ; le pied occupe les 0x28 derniers octets :

* bloc ``FLIM`` (0x14 octets) : magie, BOM 0xFEFF (gros-boutiste), taille d'en-tête 0x14, version, taille du fichier ;
* bloc ``imag`` (0x14 octets) : magie, taille 0x10, largeur u16, hauteur u16, alignement u16, format u8,
  octet de tuilage u8 (bits 0-4 : mode de tuilage GX2, bits 5-7 : swizzle), taille des pixels u32.

Le pitch (largeur rangée) n'est pas écrit : il se déduit de la taille des pixels et de la hauteur alignée, et doit
être un multiple de la largeur de tuile, au moins égal à la largeur (sinon ErreurFormat : rien n'est deviné).

Formats décodés (code -> nom, bits par élément) ; les autres lèvent ErreurFormat. Dans les mises en page du jeu,
BC5 sert de luminance + alpha (voie R = luminance, voie G = alpha) : rendu en (L, L, L, A).

Paquet ``fichiers_jeu`` : licence et attribution dans __init__.py (portage de TWWHD-Randomizer, SuperDude88,
MIT). Ce module-ci n'en est pas porté : il est écrit d'après la description publique du format et de l'AddrLib.
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat
from jeu.fichiers import gx2

PIED = 0x28
FORMATS = {
    9: ("RGBA8", 32), 20: ("RGBA8", 32),          # 20 : RGBA8_SRGB
    12: ("BC1", 64), 21: ("BC1", 64),
    14: ("BC3", 128), 23: ("BC3", 128),
    15: ("BC4L", 64), 16: ("BC4A", 64),
    17: ("BC5", 128),
}


def info(b: bytes) -> dict:
    """Champs du pied. Lève ErreurFormat si ce n'est pas un BFLIM cohérent."""
    if len(b) < PIED:
        raise ErreurFormat("BFLIM : fichier trop court")
    p = len(b) - PIED
    if b[p:p + 4] != b"FLIM" or b[p + 4:p + 6] != b"\xfe\xff":
        raise ErreurFormat("BFLIM : pied FLIM absent (ou petit-boutiste, non pris en charge)")
    taille_fichier = struct.unpack_from(">I", b, p + 0x0C)[0]
    if taille_fichier != len(b):
        raise ErreurFormat("BFLIM : taille du fichier incohérente")
    im = p + 0x14
    if b[im:im + 4] != b"imag":
        raise ErreurFormat("BFLIM : bloc imag absent")
    largeur, hauteur, alignement = struct.unpack_from(">HHH", b, im + 8)
    fmt, octet_tuile = b[im + 14], b[im + 15]
    taille = struct.unpack_from(">I", b, im + 16)[0]
    if not largeur or not hauteur or taille > p:
        raise ErreurFormat("BFLIM : dimensions ou taille des pixels invalides")
    return {"largeur": largeur, "hauteur": hauteur, "alignement": alignement, "format": fmt,
            "mode": octet_tuile & 0x1F, "swizzle": octet_tuile >> 5, "taille": taille}


def geometrie(i: dict) -> tuple[str, int, int, int, int]:
    """(nom du format, bits par élément, largeur et hauteur en éléments, pitch). Pitch contrôlé."""
    if i["format"] not in FORMATS:
        raise ErreurFormat(f"BFLIM : format {i['format']} non pris en charge")
    nom, bpp = FORMATS[i["format"]]
    bc = nom.startswith("BC")
    el, eh = ((i["largeur"] + 3) // 4, (i["hauteur"] + 3) // 4) if bc else (i["largeur"], i["hauteur"])
    mode = i["mode"]
    if mode not in gx2.MODES_PRIS:
        raise ErreurFormat(f"BFLIM : mode de tuilage {mode} non pris en charge")
    ha = gx2.hauteur_alignee(eh, mode)
    ligne = ha * bpp // 8
    if i["taille"] % ligne:
        raise ErreurFormat("BFLIM : taille des pixels incompatible avec la hauteur alignée")
    pitch = i["taille"] // ligne
    pas = 32 if mode >= 4 else 8 if mode in (2, 3) else 1
    if pitch < el or pitch % pas:
        raise ErreurFormat(f"BFLIM : pitch {pitch} incohérent (largeur {el}, multiple de {pas} attendu)")
    return nom, bpp, el, eh, pitch


def decoder(b: bytes) -> tuple[int, int, bytes]:
    """(largeur, hauteur, pixels RGBA 8 bits ligne après ligne)."""
    i = info(b)
    nom, bpp, el, eh, pitch = geometrie(i)
    lin = gx2.deswizzle(b[:i["taille"]], el, eh, bpp, i["mode"], i["swizzle"], pitch)
    l, h = i["largeur"], i["hauteur"]
    if nom == "RGBA8":
        rgba = gx2.decoder("RGBA8", lin, l, h)
    elif nom in ("BC1", "BC3"):
        rgba = gx2.decoder(nom, lin, l, h)
    elif nom == "BC4L":
        rgba = gx2.decoder("BC4", lin, l, h)
    elif nom == "BC4A":
        v = gx2.decoder("BC4", lin, l, h)
        rgba = bytearray(len(v))
        rgba[0::4] = rgba[1::4] = rgba[2::4] = b"\xff" * (len(v) // 4)
        rgba[3::4] = v[0::4]
        rgba = bytes(rgba)
    else:                                                  # BC5 : luminance (R) + alpha (G)
        v = gx2.decoder("BC5", lin, l, h)
        rgba = bytearray(len(v))
        rgba[0::4] = rgba[1::4] = rgba[2::4] = v[0::4]
        rgba[3::4] = v[1::4]
        rgba = bytes(rgba)
    return l, h, rgba
