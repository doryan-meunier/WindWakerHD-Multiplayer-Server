"""Les PORTRAITS des monstres du menu d'invocation des Hanteurs (mod natif 0.62.0, menu.c), préparés SUR CE PC.

Le jeu n'a pas de portrait d'ennemi dans son paquet 2D (celui que le HUD du portage lit) ; il en a dans
``content/Common/Layout/Bubble_00.szs`` (images ``Enemy_NN`` de 300 x 300 : des captures du jeu, pour les bulles
Miiverse). On en tire une par monstre, recadrée sur lui et rognée en rond (bord adouci), écrite dans le dossier de
données du mod (``<réglages du portage>/ModManager/Data/wwmp-natif/monstre_<proc>.png``, lue par le mod avec
``WWHD_HUD_DATA``). Comme ``carte_bonnet.png`` (jeu/bonnet_carte.py) : tirée des fichiers du jeu du joueur, lus
seulement ; jamais dans le dépôt, jamais dans un paquet distribué. Sans elle, le menu montre le coût dans la bulle.
"""
from __future__ import annotations

import math
import os

from jeu import bonnet_carte as bc
from jeu.fichiers import ErreurFormat, bflim, gx2, sarc, yaz0

ARCHIVE = os.path.join("content", "Common", "Layout", "Bubble_00.szs")
COTE = 128                          # côté du portrait écrit
BORD = 2.0                          # largeur de l'adoucissement du bord du rond (pixels de sortie)
# proc -> (image de Bubble_00, centre x, centre y, rayon) dans l'image de 300 x 300 : le monstre bien en face
CADRES = {
    189: ("Enemy_01^o", 128, 150, 86),     # Bokoblin (vert, à terre)
    188: ("Enemy_31^o", 152, 128, 70),     # Moblin
    206: ("Enemy_04^o", 152, 136, 96),     # Chuchu (verts)
    215: ("Enemy_11^o", 114, 142, 78),     # Keese (deux, sur un pont)
    247: ("Enemy_03^o", 168, 160, 76),     # Miniblin (fourche)
    209: ("Enemy_15^o", 152, 128, 82),     # Peahat
    191: ("Enemy_34^o", 178, 138, 104),    # Darknut
}


def fichier(proc: int) -> str:
    return f"monstre_{int(proc)}.png"


def _archive(brut: bytes):
    return sarc.Sarc(yaz0.decompresser(brut) if yaz0.est_yaz0(brut) else brut)


def portrait(l: int, h: int, rgba: bytes, cx: float, cy: float, r: float, cote: int = COTE) -> bytes:
    """Le disque (cx, cy, r) de l'image RGBA (l x h), échantillonné (bilinéaire) sur ``cote`` x ``cote`` pixels,
    transparent hors du rond (bord adouci sur BORD pixels), opaque dedans. RGBA."""
    out = bytearray(cote * cote * 4)
    pas = 2.0 * r / cote
    rs = cote / 2.0

    def px(x, y):
        x = min(max(x, 0), l - 1)
        y = min(max(y, 0), h - 1)
        return 4 * (y * l + x)

    for j in range(cote):
        for i in range(cote):
            dx, dy = i + 0.5 - rs, j + 0.5 - rs
            a = min(1.0, max(0.0, (rs - math.hypot(dx, dy)) / BORD))
            if a <= 0.0:
                continue
            sx = cx - r + (i + 0.5) * pas - 0.5
            sy = cy - r + (j + 0.5) * pas - 0.5
            x0, y0 = int(math.floor(sx)), int(math.floor(sy))
            fx, fy = sx - x0, sy - y0
            p00, p10, p01, p11 = px(x0, y0), px(x0 + 1, y0), px(x0, y0 + 1), px(x0 + 1, y0 + 1)
            o = 4 * (j * cote + i)
            for c in range(3):
                v = ((rgba[p00 + c] * (1 - fx) + rgba[p10 + c] * fx) * (1 - fy)
                     + (rgba[p01 + c] * (1 - fx) + rgba[p11 + c] * fx) * fy)
                out[o + c] = min(255, int(v + 0.5))
            out[o + 3] = int(255 * a + 0.5)
    return bytes(out)


def pngs(brut_archive: bytes) -> dict[int, bytes]:
    """Bubble_00.szs (brut) -> {proc: PNG du portrait}. Un cadre dont l'image manque est sauté."""
    arc = _archive(brut_archive)
    out = {}
    for proc, (nom, cx, cy, r) in CADRES.items():
        try:
            l, h, rgba = bflim.decoder(arc.lire(f"timg/{nom}.bflim"))
        except (KeyError, ErreurFormat):
            continue
        k = l / 300.0
        out[proc] = gx2.png(COTE, COTE, portrait(l, h, rgba, cx * k, cy * k, r * k))
    if not out:
        raise ErreurFormat("Bubble_00 : aucun portrait de monstre (archive inattendue)")
    return out


def installer(config: str, jeux) -> dict:
    """Écrit ``monstre_<proc>.png`` dans le dossier de données du mod (seulement s'ils changent). ``jeux`` : dossiers
    du jeu à essayer dans l'ordre (jeu/bonnet_carte.py dossiers_jeu). {fait: "ecrit" / "deja" / "sans_jeu", source,
    procs}. Lève ErreurFormat / OSError."""
    for jeu in jeux:
        p = os.path.join(jeu, ARCHIVE)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as f:                       # lecture seule
            brut = f.read()
        ecrit = False
        images = pngs(brut)
        for proc, data in images.items():
            ecrit = bc._ecrire(bc.chemin_sortie(config, fichier(proc)), data) or ecrit
        return {"fait": "ecrit" if ecrit else "deja", "source": p, "procs": sorted(images)}
    return {"fait": "sans_jeu", "source": "", "procs": []}
