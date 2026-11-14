"""cmapdat.bin : liste des cartes (cartes au trésor et cartes de la Triforce) de la carte marine.

Portage de TWWHD-Randomizer ``filetypes/charts.cpp`` (SuperDude88 et contributeurs, licence MIT,
Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt), réécrit en Python.

Format (grand-boutiste) : u32 nombre de cartes, puis 0x26 octets par carte :

    +0x00 u8  identifiant de texture          +0x01 u8  index de carte possédée + 1 (ce que lit le trésor)
    +0x02 u8  numéro affiché                   +0x03 u8  genre (0, 1, 2, 5, 6, 8 : cartes d'île ; autres : à part)
    +0x04 s8  secteur x (-3..3)                +0x05 s8  secteur y (-3..3)   -> île = (x + 3) + (y + 3) * 7 + 1
    +0x06 4 × (u16 décalage x de la texture, u16 décalage y, u16 position x du trésor, u16 position y)

Édition à taille égale seulement (aucune carte ajoutée ni retirée) : rien de changé = les octets d'origine.
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat

T_CARTE = 0x26
GENRES_ILE = (0, 1, 2, 5, 6, 8)


class Carte:
    __slots__ = ("pos", "data", "origine")

    def __init__(self, data: bytes, pos: int):
        self.pos = pos
        self.data = bytearray(data)
        self.origine = bytes(data)

    @property
    def texture(self) -> int:
        return self.data[0]

    @property
    def possedee(self) -> int:
        """Index de carte possédée + 1 (champ 0x0FF00000 des trésors engloutis qui l'attendent)."""
        return self.data[1]

    @property
    def numero(self) -> int:
        return self.data[2]

    @property
    def genre(self) -> int:
        return self.data[3]

    @property
    def secteur(self) -> tuple[int, int]:
        return struct.unpack_from(">bb", self.data, 4)

    @property
    def ile(self) -> int:
        x, y = self.secteur
        return (x + 3) + (y + 3) * 7 + 1

    def position(self) -> bytes:
        """Ce qui situe la carte sur la carte marine : texture, secteur, quatre positions (sans l'index possédé, le
        numéro ni le genre, qui suivent l'objet « carte »)."""
        return bytes(self.data[0:1]) + bytes(self.data[4:T_CARTE])

    def poser_position(self, pos: bytes) -> None:
        if len(pos) != 1 + T_CARTE - 4:
            raise ErreurFormat("cmapdat : position de carte de taille inattendue")
        self.data[0] = pos[0]
        self.data[4:T_CARTE] = pos[1:]


class Cartes:
    """Liste des cartes de ``cmapdat.bin``."""

    def __init__(self, brut: bytes):
        d = bytes(brut)
        if len(d) < 4:
            raise ErreurFormat("cmapdat : fichier trop court")
        n = struct.unpack_from(">I", d, 0)[0]
        if n == 0 or 4 + n * T_CARTE != len(d):
            raise ErreurFormat("cmapdat : nombre de cartes incohérent avec la taille")
        self.brut = d
        self.cartes = [Carte(d[4 + T_CARTE * i:4 + T_CARTE * (i + 1)], 4 + T_CARTE * i) for i in range(n)]

    def par_ile(self) -> dict[int, Carte]:
        """{île 1..49: carte d'île} (genres de GENRES_ILE). Lève ErreurFormat si une île n'en a pas exactement une."""
        out = {}
        for c in self.cartes:
            if c.genre in GENRES_ILE:
                if not 1 <= c.ile <= 49 or c.ile in out:
                    raise ErreurFormat(f"cmapdat : carte d'île inattendue (île {c.ile})")
                out[c.ile] = c
        if sorted(out) != list(range(1, 50)):
            raise ErreurFormat("cmapdat : les 49 îles n'ont pas chacune une carte")
        return out

    def octets(self) -> bytes:
        out = bytearray(self.brut)
        for c in self.cartes:
            if bytes(c.data) != c.origine:
                out[c.pos:c.pos + T_CARTE] = c.data
        return bytes(out)


def fabriquer(cartes: list[tuple]) -> bytes:
    """(tests) cmapdat synthétique : [(texture, possédée, numéro, genre, x, y, positions de 32 octets)]."""
    out = bytearray(struct.pack(">I", len(cartes)))
    for tex, pos, num, genre, x, y, p in cartes:
        if len(p) != 32:
            raise ValueError("positions : 32 octets")
        out += struct.pack(">BBBBbb", tex, pos, num, genre, x, y) + p
    return bytes(out)
