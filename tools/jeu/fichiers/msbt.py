"""MSBT : textes LMS de Nintendo (« MsgStdBn », UTF-16 BE dans Wind Waker HD).

Portage partiel de TWWHD-Randomizer ``filetypes/msbt.cpp`` et ``filetypes/shared/lms.cpp`` (SuperDude88 et
contributeurs, licence MIT, Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt).

Étape 1 (docs/etudes/rando_fichiers.md) : lecture (étiquettes LBL1 -> index, textes TXT2) et remplacement d'un
texte À TAILLE ÉGALE : le nouveau texte (sans code de contrôle) est écrit à la place de l'ancien, suivi d'un zéro
de fin, et le reste de l'ancien emplacement est rempli de zéros. Le jeu lit jusqu'au premier zéro : aucun
décalage, aucune section ne bouge.

Étape 3 : remplacement d'un texte de N'IMPORTE QUELLE taille (``remplacer``, codes de contrôle compris) : seule la
section TXT2 est réécrite (table des décalages, puis les textes dans l'ordre des index, fin de section complétée
par 0xAB jusqu'à un multiple de 16, comme ``TXT2::write`` de TWWHD) ; les sections qui la suivent sont recopiées
telles quelles, la taille du fichier est mise à jour. Aucun message n'est ajouté ni retiré (LBL1, ATR1, TSY1
intouchées). ``Texte`` fabrique les codes de contrôle (couleur, choix, remplacement…) de ``msbtMacros.hpp``.
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat

MAGIE = b"MsgStdBn"


class Msbt:
    def __init__(self, brut: bytes):
        d = bytes(brut)
        if len(d) < 0x20 or d[:8] != MAGIE:
            raise ErreurFormat("pas un fichier MSBT")
        bom, z1, enc, ver, nsec, z2, taille = struct.unpack_from(">HHBBHHI", d, 8)
        if bom != 0xFEFF or z1 or z2 or ver != 3:
            raise ErreurFormat("MSBT : en-tête inattendu")
        if enc != 1:
            raise ErreurFormat("MSBT : seul l'UTF-16 est pris en charge")
        if taille != len(d):
            raise ErreurFormat("MSBT : taille annoncée fausse")
        self.brut = bytearray(d)
        self.sections: dict[str, tuple[int, int]] = {}    # nom -> (début des données, taille)
        o = 0x20
        for _ in range(nsec):
            if o + 16 > len(d):
                raise ErreurFormat("MSBT : section tronquée")
            nom = d[o:o + 4].decode("ascii", "replace")
            sz = struct.unpack_from(">I", d, o + 4)[0]
            if o + 16 + sz > len(d):
                raise ErreurFormat(f"MSBT : section {nom} tronquée")
            self.sections[nom] = (o + 16, sz)
            o = (o + 16 + sz + 15) // 16 * 16
        if "TXT2" not in self.sections or "LBL1" not in self.sections:
            raise ErreurFormat("MSBT : sections LBL1 ou TXT2 absentes")
        self.etiquettes = self._lire_lbl1()
        self._txt_bornes = self._lire_txt2()

    def _lire_lbl1(self) -> dict[str, int]:
        d = self.brut
        base, sz = self.sections["LBL1"]
        n = struct.unpack_from(">I", d, base)[0]
        out = {}
        for i in range(n):
            cnt, off = struct.unpack_from(">II", d, base + 4 + 8 * i)
            p = base + off
            for _ in range(cnt):
                lg = d[p]
                nom = bytes(d[p + 1:p + 1 + lg]).decode("ascii", "replace")
                idx = struct.unpack_from(">I", d, p + 1 + lg)[0]
                out[nom] = idx
                p += 1 + lg + 4
                if p > base + sz:
                    raise ErreurFormat("MSBT : étiquettes tronquées")
        return out

    def _lire_txt2(self) -> list[tuple[int, int]]:
        d = self.brut
        base, sz = self.sections["TXT2"]
        n = struct.unpack_from(">I", d, base)[0]
        offs = [struct.unpack_from(">I", d, base + 4 + 4 * i)[0] for i in range(n)]
        bornes = []
        for i, o in enumerate(offs):
            fin = offs[i + 1] if i + 1 < n else sz
            if not (4 + 4 * n <= o <= fin <= sz) or (fin - o) % 2:
                raise ErreurFormat("MSBT : décalages de textes incohérents")
            bornes.append((base + o, base + fin))
        return bornes

    def __len__(self) -> int:
        return len(self._txt_bornes)

    def index(self, etiquette: str) -> int:
        if etiquette not in self.etiquettes:
            raise ErreurFormat(f"MSBT : étiquette absente : {etiquette!r}")
        i = self.etiquettes[etiquette]
        if not 0 <= i < len(self._txt_bornes):
            raise ErreurFormat(f"MSBT : index hors des textes pour {etiquette!r}")
        return i

    def brut_texte(self, etiquette: str) -> bytes:
        a, b = self._txt_bornes[self.index(etiquette)]
        return bytes(self.brut[a:b])

    def texte(self, etiquette: str) -> str:
        """Texte lisible (codes de contrôle 0x000E… remplacés par « ¤ », jusqu'au premier zéro)."""
        r = self.brut_texte(etiquette)
        out, i = [], 0
        while i + 1 < len(r):
            c = struct.unpack_from(">H", r, i)[0]
            if c == 0:
                break
            if c == 0x0E and i + 8 <= len(r):
                lg = struct.unpack_from(">H", r, i + 6)[0]
                out.append("¤")
                i += 8 + lg
                continue
            if c == 0x0F and i + 6 <= len(r):
                out.append("¤")
                i += 6
                continue
            out.append(chr(c))
            i += 2
        return "".join(out)

    def remplacer_meme_taille(self, etiquette: str, nouveau: str) -> None:
        """Le texte de ``etiquette`` devient ``nouveau`` (sans code de contrôle), à taille égale. Lève ErreurFormat
        s'il ne tient pas (zéro de fin compris) ou contient un caractère hors du plan de base."""
        a, b = self._txt_bornes[self.index(etiquette)]
        if any(ord(c) > 0xFFFF or ord(c) in (0, 0x0E, 0x0F) for c in nouveau):
            raise ErreurFormat("MSBT : caractère non permis dans le texte de remplacement")
        enc = nouveau.encode("utf-16-be") + b"\0\0"
        if len(enc) > b - a:
            raise ErreurFormat(f"MSBT : texte trop long pour {etiquette!r} ({len(enc)} octets, place {b - a})")
        self.brut[a:b] = enc + b"\0" * (b - a - len(enc))

    def brut_complet(self, etiquette: str) -> bytes:
        """Octets UTF-16BE du message (codes de contrôle et zéro de fin compris), tels que dans TXT2."""
        return self.brut_texte(etiquette)

    def remplacer(self, etiquette: str, nouveau: bytes) -> None:
        """Le message ``etiquette`` devient ``nouveau`` (UTF-16BE, codes compris, terminé par un zéro), quelle que
        soit sa taille : TXT2 est reconstruite, les sections suivantes déplacées."""
        if len(nouveau) < 2 or len(nouveau) % 2 or nouveau[-2:] != b"\0\0":
            raise ErreurFormat(f"MSBT : texte de remplacement mal formé pour {etiquette!r}")
        i = self.index(etiquette)
        textes = [bytes(self.brut[a:b]) for a, b in self._txt_bornes]
        textes[i] = bytes(nouveau)
        base, sz = self.sections["TXT2"]
        n = len(textes)
        corps = bytearray(struct.pack(">I", n))
        o = 4 + 4 * n
        for x in textes:
            corps += struct.pack(">I", o)
            o += len(x)
        for x in textes:
            corps += x
        debut = base - 16                         # en-tête de la section TXT2
        fin_ancienne = (base + sz + 15) // 16 * 16
        tete = bytearray(self.brut[debut:base])
        struct.pack_into(">I", tete, 4, len(corps))
        bloc = tete + corps
        while len(bloc) % 16:
            bloc.append(0xAB)
        d = bytearray(self.brut[:debut]) + bloc + bytearray(self.brut[fin_ancienne:])
        struct.pack_into(">I", d, 0x12, len(d))
        self.__init__(bytes(d))

    def octets(self) -> bytes:
        return bytes(self.brut)


class Texte:
    """Fabrique d'un message (UTF-16BE) avec les codes de contrôle de TWWHD (filetypes/util/msbtMacros.hpp)."""

    ROUGE, VERT, BLEU, JAUNE, CYAN = 1, 2, 3, 4, 5
    R_ENCHERE_OBJET, R_BOMBES_MAX, R_FLECHES_MAX = 4, 22, 23

    def __init__(self):
        self.d = bytearray()

    def t(self, s: str) -> "Texte":
        if any(ord(c) > 0xFFFF or ord(c) in (0, 0x0E, 0x0F) for c in s):
            raise ErreurFormat("MSBT : caractère non permis")
        self.d += s.encode("utf-16-be")
        return self

    def code(self, groupe: int, type_: int, param: bytes = b"") -> "Texte":
        self.d += struct.pack(">HHHH", 0x0E, groupe, type_, len(param)) + param
        return self

    def couleur(self, c: int) -> "Texte":
        return self.code(0, 3, struct.pack(">H", c))

    def defaut(self) -> "Texte":
        # TEXT_COLOR_DEFAULT de TWWHD : 0xFFFF puis 0
        return self.code(0, 3, b"\xff\xff").code(0, 3, b"\0\0")

    def fin_boutique(self) -> "Texte":
        # TEXT_END de TWWHD : 0xFFFF puis zéro de fin (les messages de la boutique de l'Île du Pic l'exigent)
        self.code(0, 3, b"\xff\xff")
        self.d += b"\0\0"
        return self

    def deux_choix(self) -> "Texte":
        return self.code(1, 7)

    def majuscule(self) -> "Texte":
        return self.code(1, 0xB)

    def remplacement(self, n: int) -> "Texte":
        return self.code(2, n)

    def octets(self, fin: bool = True) -> bytes:
        return bytes(self.d) + (b"\0\0" if fin else b"")
