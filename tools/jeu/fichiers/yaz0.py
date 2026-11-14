"""Yaz0 : décodeur, encodeur déterministe et recompression de la fin seulement.

Portage de TWWHD-Randomizer ``filetypes/yaz0.cpp`` (SuperDude88 et contributeurs, licence MIT,
Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt), réécrit en Python.

Format : en-tête de 16 octets (« Yaz0 », taille décompressée u32 BE, 8 octets gardés tels quels : la Wii U y met
un alignement), puis des groupes : un octet de drapeaux (bit 7 d'abord ; 1 = octet littéral, 0 = copie) suivi de
8 opérations. Copie : 2 octets ``NR RR`` (longueur N + 2, de 3 à 17 ; distance R + 1, jusqu'à 0x1000) ou, si N = 0,
3 octets ``0R RR NN`` (longueur NN + 0x12, jusqu'à 0x111).

Recompression de la FIN (``recompresser``) : les groupes d'origine sont recopiés tels quels tant qu'ils ne
décodent que des octets AVANT le premier octet changé (une copie ne lit que le passé : ils se décodent pareil) ;
la suite est recompressée par l'encodeur ci-dessous, sa fenêtre ouverte sur le préfixe. Rien de changé : les
octets d'origine, à l'identique. Garde-fou de taille : la sortie ne dépasse pas l'originale de plus de ``marge``
(2 % par défaut), sinon ErreurFormat (un pack « permanent » plus gros pourrait déborder un tas du jeu ; marge à
confirmer en jeu, docs/etudes/rando_fichiers.md §11).
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat

MAGIE = b"Yaz0"
FENETRE = 0x1000
LONGUEUR_MAX = 0x111
PROFONDEUR = 48            # candidats essayés par position (déterministe : du plus proche au plus lointain)
# relecture finale : sur certains fichiers (Misc.szs, cartes mélangées : +3 %), 48 candidats ne suffisent pas à
# rester sous la marge ; la fin est alors recompressée une seconde fois en essayant toute la fenêtre (plus lent,
# toujours déterministe). Les fichiers qui passaient au premier essai gardent les mêmes octets.
PROFONDEUR_MAX = FENETRE
MARGE = 0.02


def est_yaz0(d: bytes) -> bool:
    return len(d) >= 16 and d[:4] == MAGIE


def taille_decompressee(d: bytes) -> int:
    if not est_yaz0(d):
        raise ErreurFormat("pas un fichier Yaz0")
    return struct.unpack_from(">I", d, 4)[0]


def decompresser(d: bytes) -> bytes:
    """Décode un fichier Yaz0 entier. Lève ErreurFormat s'il est tronqué ou incohérent."""
    n = taille_decompressee(d)
    out = bytearray()
    i, fin = 16, len(d)
    try:
        while len(out) < n:
            code = d[i]
            i += 1
            for b in range(8):
                if len(out) >= n:
                    break
                if code & (0x80 >> b):
                    out.append(d[i])
                    i += 1
                    continue
                b1, b2 = d[i], d[i + 1]
                i += 2
                dist = ((b1 & 0xF) << 8 | b2) + 1
                c = b1 >> 4
                if c == 0:
                    c = d[i] + 0x12
                    i += 1
                else:
                    c += 2
                if dist > len(out):
                    raise ErreurFormat("Yaz0 : copie avant le début des données")
                deb = len(out) - dist
                if dist >= c:
                    out += out[deb:deb + c]
                else:
                    motif = bytes(out[deb:])
                    out += (motif * (c // dist + 1))[:c]
    except IndexError:
        raise ErreurFormat("Yaz0 : données tronquées") from None
    if i > fin or len(out) != n:
        raise ErreurFormat("Yaz0 : taille décompressée fausse")
    return bytes(out)


def _lcp(d: bytes, a: int, b: int, maxi: int) -> int:
    """Longueur du plus long préfixe commun de d[a:] et d[b:] (au plus ``maxi``), par galop sur des tranches."""
    n, pas = 0, 8
    while n < maxi:
        s = min(pas, maxi - n)
        if d[a + n:a + n + s] == d[b + n:b + n + s]:
            n += s
            pas *= 2
        elif s == 1:
            break
        else:
            pas = max(1, s // 2)
    return n


class _Chercheur:
    """Chaînes de hachage sur 3 octets (listes de positions dans l'ordre), fenêtre de 0x1000 octets."""

    def __init__(self, d: bytes, debut: int, profondeur: int = PROFONDEUR):
        self.d = d
        self.prof = profondeur
        self.tab: dict[bytes, list[int]] = {}
        self.ajoute = max(0, debut - FENETRE)
        self.avancer(debut)

    def avancer(self, jusqua: int) -> None:
        d, tab = self.d, self.tab
        for p in range(self.ajoute, min(jusqua, len(d) - 2)):
            k = d[p:p + 3]
            lst = tab.get(k)
            if lst is None:
                tab[k] = [p]
            else:
                lst.append(p)
                if len(lst) > 4 * self.prof:           # borne la mémoire ; garde les plus récentes
                    del lst[:len(lst) - 2 * self.prof]
        self.ajoute = max(self.ajoute, jusqua)

    def chercher(self, pos: int) -> tuple[int, int]:
        """(longueur, distance) de la meilleure copie pour ``pos`` (longueur 0 si moins de 3)."""
        d = self.d
        maxi = min(LONGUEUR_MAX, len(d) - pos)
        if maxi < 3:
            return 0, 0
        lst = self.tab.get(d[pos:pos + 3])
        if not lst:
            return 0, 0
        meilleur, dist = 0, 0
        vus = 0
        for k in range(len(lst) - 1, -1, -1):
            c = lst[k]
            if c >= pos:
                continue
            if pos - c > FENETRE:
                break
            vus += 1
            if vus > self.prof:
                break
            if meilleur and d[c + meilleur:c + meilleur + 1] != d[pos + meilleur:pos + meilleur + 1]:
                continue
            n = _lcp(d, c, pos, maxi)
            if n > meilleur:
                meilleur, dist = n, pos - c
                if n == maxi:
                    break
        return (meilleur, dist) if meilleur >= 3 else (0, 0)


def _encoder(d: bytes, debut: int, profondeur: int = PROFONDEUR) -> bytes:
    """Flux Yaz0 (groupes, sans en-tête) de d[debut:], la fenêtre ouverte sur d[:debut]. Déterministe. Recherche
    paresseuse d'un pas (comme l'encodeur de Nintendo) : une copie est différée si la position suivante en offre
    une plus longue."""
    ch = _Chercheur(d, debut, profondeur)
    out = bytearray()
    ops: list[bytes] = []
    flags = 0
    pos, n = debut, len(d)
    suivant: tuple[int, int] | None = None

    def vider():
        nonlocal flags, ops
        out.append(flags)
        for o in ops:
            out.extend(o)
        flags, ops = 0, []

    while pos < n:
        ch.avancer(pos)
        lg, di = suivant if suivant is not None else ch.chercher(pos)
        suivant = None
        if lg >= 3 and lg < LONGUEUR_MAX and pos + 1 < n:
            ch.avancer(pos + 1)
            lg2, di2 = ch.chercher(pos + 1)
            if lg2 > lg + 1:
                lg = 0                       # littéral maintenant, la copie plus longue à la position suivante
                suivant = (lg2, di2)
        bit = 0x80 >> len(ops)
        if lg >= 3:
            r = di - 1
            if lg >= 0x12:
                ops.append(bytes(((r >> 8) & 0xF, r & 0xFF, lg - 0x12)))
            else:
                ops.append(bytes((((lg - 2) << 4) | ((r >> 8) & 0xF), r & 0xFF)))
            pos += lg
        else:
            flags |= bit
            ops.append(d[pos:pos + 1])
            pos += 1
        if len(ops) == 8:
            vider()
    if ops:
        vider()
    return bytes(out)


def compresser(d: bytes, entete: bytes = b"\0" * 8) -> bytes:
    """Fichier Yaz0 complet de ``d`` (encodeur déterministe). ``entete`` : les 8 octets après la taille."""
    if len(entete) != 8:
        raise ErreurFormat("Yaz0 : 8 octets d'en-tête attendus")
    return MAGIE + struct.pack(">I", len(d)) + bytes(entete) + _encoder(bytes(d), 0)


def _groupes(comp: bytes, n: int):
    """(décalage compressé du groupe, position décompressée de son début) de chaque groupe, sans décoder."""
    i, pos = 16, 0
    while pos < n:
        yield i, pos
        code = comp[i]
        i += 1
        for b in range(8):
            if pos >= n:
                break
            if code & (0x80 >> b):
                i += 1
                pos += 1
            else:
                c = comp[i] >> 4
                if c == 0:
                    c = comp[i + 2] + 0x12
                    i += 3
                else:
                    c += 2
                    i += 2
                pos += c


def premier_ecart(a: bytes, b: bytes) -> int | None:
    """Premier indice où ``a`` et ``b`` diffèrent (None : identiques)."""
    if a == b:
        return None
    n = min(len(a), len(b))
    lo, hi = 0, n             # invariant : a[:lo] == b[:lo]
    if a[:n] == b[:n]:
        return n
    while hi - lo > 64:
        m = (lo + hi) // 2
        if a[lo:m] == b[lo:m]:
            lo = m
        else:
            hi = m
    for k in range(lo, hi):
        if a[k] != b[k]:
            return k
    return n


def recompresser(origine: bytes, nouveau: bytes, origine_dec: bytes | None = None, marge: float = MARGE) -> bytes:
    """Nouveau fichier Yaz0 pour les données décompressées ``nouveau``, à partir du fichier Yaz0 ``origine`` : son
    préfixe compressé est gardé jusqu'au groupe qui précède le premier octet changé, la fin est recompressée.
    Rien de changé : ``origine`` tel quel. Lève ErreurFormat si la sortie dépasse l'originale de plus de ``marge``."""
    if origine_dec is None:
        origine_dec = decompresser(origine)
    ecart = premier_ecart(origine_dec, nouveau)
    if ecart is None:
        return bytes(origine)
    coupe_i, coupe_pos = 16, 0
    for i, pos in _groupes(origine, len(origine_dec)):
        if pos > ecart:
            break
        coupe_i, coupe_pos = i, pos
    tete = MAGIE + struct.pack(">I", len(nouveau)) + origine[8:16] + origine[16:coupe_i]
    limite = None if marge is None else len(origine) * (1 + marge) + 64
    sortie = tete + _encoder(bytes(nouveau), coupe_pos)
    if limite is not None and len(sortie) > limite:
        sortie = tete + _encoder(bytes(nouveau), coupe_pos, PROFONDEUR_MAX)     # second essai : toute la fenêtre
    if limite is not None and len(sortie) > limite:
        raise ErreurFormat(f"Yaz0 : fichier recompressé trop gros ({len(sortie)} octets pour {len(origine)} à "
                           f"l'origine, marge {marge:.0%})")
    return sortie
