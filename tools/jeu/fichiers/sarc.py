"""SARC : archive de Nintendo (en-tête, table SFAT, noms SFNT, données).

Portage de TWWHD-Randomizer ``filetypes/sarc.cpp`` (SuperDude88 et contributeurs, licence MIT,
Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt), réécrit en Python.

Différence voulue avec TWWHD : on ne reconstruit pas l'archive de zéro. ``Sarc.octets()`` garde l'en-tête, la table
SFAT (ordre, hachages, attributs), la table des noms et le début des données d'origine ; seuls les décalages de
données des fichiers qui suivent un fichier qui a changé de taille bougent, chacun aligné au moins comme TWWHD le
fait (``alignement``) ET au moins comme il l'était dans l'original (plus grande puissance de 2 qui divise son
décalage, au plus 0x2000). Aucun changement : les octets d'origine, à l'identique.
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat

MAGIE = b"SARC"
CLE_HACHAGE = 0x65
ALIGNEMENTS = {"bflan": 4, "bflyt": 4, "szs": 0x2000, "sarc": 0x2000, "bfres": 0x2000, "sharcfb": 0x2000}
ALIGN_MAX = 0x2000


def hachage(nom: str, cle: int = CLE_HACHAGE) -> int:
    h = 0
    for c in nom.encode("utf-8"):
        h = (h * cle + (c if c < 0x80 else c - 0x100)) & 0xFFFFFFFF
    return h


def alignement(nom: str, data: bytes) -> int:
    """Alignement de TWWHD (``getAlignment``) : selon l'extension ; bflim : lu dans son pied « FLIM »."""
    ext = nom[nom.find(".") + 1:] if "." in nom else ""
    if ext in ALIGNEMENTS:
        return ALIGNEMENTS[ext]
    if ext == "bflim" and len(data) >= 0x28 and data[-0x28:-0x24] == b"FLIM":
        return struct.unpack(">H", data[-8:-6])[0] or 1
    return 1


def _arrondi(v: int, a: int) -> int:
    return v if a <= 1 else (v + a - 1) // a * a


def _bit_bas(v: int) -> int:
    return ALIGN_MAX if v == 0 else min(ALIGN_MAX, v & -v)


class Sarc:
    """Archive lue. ``noms`` : dans l'ordre de la table SFAT ; ``fichiers`` : nom -> octets (modifiables par
    ``remplacer``)."""

    def __init__(self, brut: bytes):
        d = bytes(brut)
        if len(d) < 0x20 or d[:4] != MAGIE:
            raise ErreurFormat("pas une archive SARC")
        hsz, bom, taille, debut, version, pad = struct.unpack_from(">HHIIHH", d, 4)
        if hsz != 0x14 or bom != 0xFEFF or version != 0x0100 or pad != 0:
            raise ErreurFormat("SARC : en-tête inattendu (ordre des octets ou version)")
        if taille > len(d) or debut > taille:
            raise ErreurFormat("SARC : tailles incohérentes")
        if d[0x14:0x18] != b"SFAT":
            raise ErreurFormat("SARC : table SFAT absente")
        fsz, n, cle = struct.unpack_from(">HHI", d, 0x18)
        if fsz != 0xC or cle != CLE_HACHAGE:
            raise ErreurFormat("SARC : table SFAT inattendue")
        nt = 0x20 + 0x10 * n
        if d[nt:nt + 4] != b"SFNT" or struct.unpack_from(">HH", d, nt + 4) != (8, 0):
            raise ErreurFormat("SARC : table des noms absente")
        self.brut = d
        self.debut = debut
        self.taille = taille
        self.noms: list[str] = []
        self.noeuds: list[tuple[int, int, int, int]] = []    # (hachage, attributs, début, fin)
        self.fichiers: dict[str, bytes] = {}
        for i in range(n):
            h, attr, s, e = struct.unpack_from(">IIII", d, 0x20 + 0x10 * i)
            if attr >> 24 != 1:
                raise ErreurFormat("SARC : attributs de nom inattendus (collision de hachage ?)")
            o = nt + 8 + (attr & 0xFFFFFF) * 4
            z = d.find(b"\0", o)
            if z < 0:
                raise ErreurFormat("SARC : nom tronqué")
            nom = d[o:z].decode("utf-8")
            if hachage(nom) != h:
                raise ErreurFormat(f"SARC : hachage faux pour {nom!r}")
            if not (0 <= s <= e <= taille - debut):
                raise ErreurFormat(f"SARC : données hors de l'archive pour {nom!r}")
            if nom in self.fichiers:
                raise ErreurFormat(f"SARC : nom en double {nom!r}")
            self.noms.append(nom)
            self.noeuds.append((h, attr, s, e))
            self.fichiers[nom] = d[debut + s:debut + e]
        self._origine = dict(self.fichiers)

    def lire(self, nom: str) -> bytes:
        if nom not in self.fichiers:
            raise ErreurFormat(f"SARC : fichier absent : {nom!r}")
        return self.fichiers[nom]

    def remplacer(self, nom: str, data: bytes) -> None:
        if nom not in self.fichiers:
            raise ErreurFormat(f"SARC : fichier absent : {nom!r} (aucun ajout de fichier n'est prévu)")
        self.fichiers[nom] = bytes(data)

    def modifie(self) -> bool:
        return any(self.fichiers[n] != self._origine[n] for n in self.noms)

    def octets(self) -> bytes:
        if not self.modifie():
            return self.brut
        d = self.brut
        ordre = sorted(range(len(self.noms)), key=lambda i: (self.noeuds[i][2], i))
        nouveaux: dict[int, tuple[int, int]] = {}
        decale = False
        fin_prec = 0
        for i in ordre:
            _h, _a, s, e = self.noeuds[i]
            nom = self.noms[i]
            data = self.fichiers[nom]
            if not decale:
                ns = s
                if s < fin_prec:
                    raise ErreurFormat("SARC : données qui se chevauchent")
            else:
                a = max(alignement(nom, data), _bit_bas(s), 1)
                ns = _arrondi(fin_prec, a)
            nouveaux[i] = (ns, ns + len(data))
            if len(data) != e - s:
                decale = True
            fin_prec = ns + len(data)
        orig = d[self.debut:self.taille]
        traine = d[self.taille:]                  # octets après la fin annoncée (gardés tels quels)
        if not decale:
            # mêmes tailles partout : l'archive d'origine, fichiers changés réécrits en place
            corps = bytearray(orig)
            for i, (ns, ne) in nouveaux.items():
                corps[ns:ne] = self.fichiers[self.noms[i]]
            return d[:self.debut] + bytes(corps) + traine
        fin_donnees = max((e for _s, e in nouveaux.values()), default=0)
        out = bytearray(d[:self.debut])
        struct.pack_into(">I", out, 8, self.debut + fin_donnees)
        for i, (ns, ne) in nouveaux.items():
            struct.pack_into(">II", out, 0x20 + 0x10 * i + 8, ns, ne)
        corps = bytearray(fin_donnees)
        self._recopier_bourrage(corps, orig, ordre, nouveaux)
        for i in ordre:
            ns, ne = nouveaux[i]
            corps[ns:ne] = self.fichiers[self.noms[i]]
        return bytes(out) + bytes(corps) + traine

    def _recopier_bourrage(self, corps: bytearray, orig: bytes, ordre, nouveaux) -> None:
        """Les octets ENTRE deux fichiers (bourrage) sont repris de l'original tant que les deux fichiers qui les
        bordent sont à leur place d'origine : seul ce qui suit un fichier changé de taille est à zéro."""
        prec_fin_o = prec_fin_n = 0
        for i in ordre:
            s, _e = self.noeuds[i][2], self.noeuds[i][3]
            ns, ne = nouveaux[i]
            if ns == s and prec_fin_n == prec_fin_o and s >= prec_fin_o:
                corps[prec_fin_o:s] = orig[prec_fin_o:s]
            prec_fin_o, prec_fin_n = self.noeuds[i][3], ne
