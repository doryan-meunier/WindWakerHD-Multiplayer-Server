"""Le bonnet de l'icône de Link sur la carte et sa flèche (0.42.0), préparés SUR CE PC (mod natif 0.21.0 ; carte.c).

Les autres joueurs sont dessinés sur la carte du jeu avec l'icône de Link du jeu elle-même (layout ``LinkPos_00`` du
paquet 2D : flèche ``MapDirectionArrow_00^t``, visage ``MapLinkS_00^l``), et seul le bonnet prend la couleur du
joueur. Le bonnet n'est ni un volet ni une texture à part : le visage est UNE image RGBA de 64 x 64 (volet
``P_MapLinkFace_00``, matériau blanc et noir neutres). Le mod dessine donc le visage d'origine, puis par-dessus une
deuxième image de même taille : le bonnet seul, en niveaux de gris, transparent ailleurs ; le HUD du portage la
multiplie par la couleur du joueur.

Cette deuxième image est TIRÉE DU JEU : elle est calculée ici, sur le PC du joueur, depuis les fichiers du jeu que le
portage a préparés (``<données du portage>/game``) ou depuis son dump, lus seulement, et écrite dans le dossier de
données du mod (``<réglages du portage>/ModManager/Data/wwmp-natif/carte_bonnet.png``, lu par le mod avec
``WWHD_HUD_DATA``). Jamais dans le dépôt, jamais dans un paquet distribué.

Le bonnet : les pixels verts (teinte 75 à 170 degrés, saturation 0,15 ou plus, assez opaques), regroupés en taches
(voisins à 8) ; on garde les taches d'au moins ``TACHE_MIN`` pixels (le haut du bonnet et sa pointe) et on écarte les
petites (les iris, vert sombre, une trentaine de pixels chacun). Niveau de gris : la luminance du pixel rapportée à
la luminance médiane du bonnet (le vert principal donne blanc, donc exactement la couleur du joueur ; les ombres
restent plus sombres), alpha d'origine.

La flèche (mod 0.42.0, ``carte_fleche.png``, même dossier, même moment) : la texture ``MapDirectionArrow_00^t`` est un
BC5 (luminance, alpha) que le jeu peint avec le matériau ``P_Direction_00`` (noir FF0000, blanc FFFFFF : couleur =
noir + (blanc - noir) * luminance ; la luminance vaut ~0 dans la flèche, 1 sur le liseré) : rouge. Le mod donne à
chaque flèche la couleur de son joueur à la place du noir du matériau, en deux couches d'une seule image deux fois
plus large : moitié gauche la forme blanche (alpha de la flèche), moitié droite la part « noire » (blanc, alpha de la
flèche fois 1 - luminance), que le HUD teint de la couleur et pose par-dessus : blanc + (couleur - blanc) * (1 - L),
soit le mélange du matériau. Même règle que le bonnet : tirée du jeu, jamais commitée ni distribuée.
"""
from __future__ import annotations

import colorsys
import os

from jeu.fichiers import ErreurFormat, bflim, gx2, sarc, yaz0

ARCHIVE = "LinkPos_00.szs"
TEXTURE = "timg/MapLinkS_00^l.bflim"
TEXTURE_FLECHE = "timg/MapDirectionArrow_00^t.bflim"
FICHIER_FLECHE = "carte_fleche.png"
PACKS = ("permanent_2d_UsEnglish.pack", "permanent_2d_UsFrench.pack", "permanent_2d_UsSpanish.pack")
FICHIER = "carte_bonnet.png"
ID_MOD = "wwmp-natif"
TACHE_MIN = 40
ALPHA_MIN = 16
TEINTE = (75.0, 170.0)
SATURATION_MIN = 0.15


def _vert(r: int, g: int, b: int, a: int) -> bool:
    if a < ALPHA_MIN:
        return False
    h, s, _v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return s >= SATURATION_MIN and TEINTE[0] <= h * 360.0 <= TEINTE[1]


def masque(l: int, h: int, rgba: bytes) -> list[bool]:
    """Pixels du bonnet : les taches vertes d'au moins TACHE_MIN pixels (voisins à 8)."""
    verts = [_vert(*rgba[4 * i:4 * i + 4]) for i in range(l * h)]
    vu = [False] * (l * h)
    out = [False] * (l * h)
    for depart in range(l * h):
        if not verts[depart] or vu[depart]:
            continue
        tache, pile = [], [depart]
        vu[depart] = True
        while pile:
            i = pile.pop()
            tache.append(i)
            x, y = i % l, i // l
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < l and 0 <= ny < h:
                        j = ny * l + nx
                        if verts[j] and not vu[j]:
                            vu[j] = True
                            pile.append(j)
        if len(tache) >= TACHE_MIN:
            for i in tache:
                out[i] = True
    return out


def _luminance(r: int, g: int, b: int) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def image_bonnet(l: int, h: int, rgba: bytes) -> bytes:
    """Le bonnet seul en niveaux de gris (RGBA, même taille) ; ErreurFormat si aucun bonnet n'est trouvé."""
    m = masque(l, h, rgba)
    lum = sorted(_luminance(*rgba[4 * i:4 * i + 3]) for i in range(l * h) if m[i])
    if len(lum) < TACHE_MIN:
        raise ErreurFormat("bonnet introuvable dans l'icône de Link (texture inattendue)")
    ref = lum[len(lum) // 2] or 1.0
    out = bytearray(l * h * 4)
    for i in range(l * h):
        if m[i]:
            v = min(255, int(round(255.0 * _luminance(*rgba[4 * i:4 * i + 3]) / ref)))
            out[4 * i:4 * i + 4] = bytes((v, v, v, rgba[4 * i + 3]))
    return bytes(out)


def image_fleche(l: int, h: int, rgba: bytes) -> tuple[int, bytes]:
    """La flèche (RGBA décodé : luminance en R, alpha en A) -> (largeur 2 l, RGBA) : à gauche la forme blanche (alpha
    a), à droite la part « noire » du matériau (blanc, alpha a * (255 - L) / 255). ErreurFormat si la flèche est vide."""
    if not any(rgba[4 * i + 3] for i in range(l * h)):
        raise ErreurFormat("flèche de la carte vide (texture inattendue)")
    out = bytearray(2 * l * h * 4)
    for y in range(h):
        for x in range(l):
            i = 4 * (y * l + x)
            lum, a = rgba[i], rgba[i + 3]
            g = 4 * (y * 2 * l + x)
            out[g:g + 4] = bytes((255, 255, 255, a))
            d = g + 4 * l
            out[d:d + 4] = bytes((255, 255, 255, (a * (255 - lum) + 127) // 255))
    return 2 * l, bytes(out)


def _layout(brut: bytes):
    pack = sarc.Sarc(brut)
    a = pack.lire(ARCHIVE)
    return sarc.Sarc(yaz0.decompresser(a) if yaz0.est_yaz0(a) else a)


def depuis_pack(brut: bytes) -> tuple[int, int, bytes]:
    """Le paquet 2D (SARC) -> (largeur, hauteur, RGBA du bonnet)."""
    l, h, rgba = bflim.decoder(_layout(brut).lire(TEXTURE))
    return l, h, image_bonnet(l, h, rgba)


def fleche_depuis_pack(brut: bytes) -> tuple[int, int, bytes]:
    """Le paquet 2D (SARC) -> (largeur, hauteur, RGBA des deux moitiés de la flèche)."""
    l, h, rgba = bflim.decoder(_layout(brut).lire(TEXTURE_FLECHE))
    l2, out = image_fleche(l, h, rgba)
    return l2, h, out


def png(brut_pack: bytes) -> bytes:
    l, h, rgba = depuis_pack(brut_pack)
    return gx2.png(l, h, rgba)


def png_fleche(brut_pack: bytes) -> bytes:
    l, h, rgba = fleche_depuis_pack(brut_pack)
    return gx2.png(l, h, rgba)


def chemin_pack(jeu: str) -> str:
    """Le premier paquet 2D présent dans le dossier du jeu ``jeu`` (celui qui contient ``content``), ou ""."""
    for p in PACKS:
        c = os.path.join(jeu, "content", "Common", "Pack", p)
        if os.path.isfile(c):
            return c
    return ""


def dossiers_jeu(inst: dict, autres=()) -> list[str]:
    """Où chercher les fichiers du jeu : ceux que le portage a préparés, puis les dumps connus du lanceur."""
    out = []
    if inst.get("donnees"):
        out.append(os.path.join(inst["donnees"], "game"))
    for d in autres:
        if isinstance(d, str) and d and os.path.isdir(d):
            out.append(d)
    return out


def chemin_sortie(config: str, fichier: str = FICHIER) -> str:
    return os.path.join(config, "ModManager", "Data", ID_MOD, fichier)


def _ecrire(sortie: str, data: bytes) -> bool:
    """Écrit ``data`` dans ``sortie`` seulement s'il change ; True s'il a été écrit."""
    try:
        with open(sortie, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(sortie), exist_ok=True)
    tmp = sortie + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, sortie)
    return True


def installer(config: str, jeux) -> dict:
    """Écrit ``carte_bonnet.png`` et (0.42.0) ``carte_fleche.png`` dans le dossier de données du mod (seulement s'ils
    changent). ``jeux`` : dossiers du jeu à essayer dans l'ordre. {fait: "ecrit" / "deja" / "sans_jeu", chemin,
    source, fleche (chemin de la flèche), erreur_fleche ("" ou la raison)}. La flèche ne bloque jamais le bonnet : sans
    elle, le mod garde la flèche du jeu. Lève ErreurFormat / OSError pour le bonnet."""
    sortie = chemin_sortie(config)
    sortie_fleche = chemin_sortie(config, FICHIER_FLECHE)
    for jeu in jeux:
        p = chemin_pack(jeu)
        if not p:
            continue
        with open(p, "rb") as f:                       # lecture seule
            brut = f.read()
        data = png(brut)
        erreur = ""
        try:
            fleche = png_fleche(brut)
        except (ErreurFormat, KeyError, ValueError) as e:
            fleche, erreur = None, f"{e.__class__.__name__}: {e}"
        ecrit = _ecrire(sortie, data)
        if fleche is not None:
            ecrit = _ecrire(sortie_fleche, fleche) or ecrit
        return {"fait": "ecrit" if ecrit else "deja", "chemin": sortie, "source": p, "fleche": sortie_fleche,
                "erreur_fleche": erreur}
    return {"fait": "sans_jeu", "chemin": sortie, "source": "", "fleche": sortie_fleche, "erreur_fleche": ""}
