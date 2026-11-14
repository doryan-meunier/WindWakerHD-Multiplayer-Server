"""Lecture seule du ``cking.rpx`` du dump (ELF Wii U, sections compressées zlib) : octets, mots, pointeurs relogés.

Sert aux contrôles hors ligne des packs (tests : accroches à leur place, instructions d'origine) et à l'outil de
portage des correctifs de TWWHD-Randomizer (rando/port_twwhd.py). N'écrit jamais rien ; le dump n'est jamais
distribué (chaque PC lit le sien).
"""
from __future__ import annotations

import struct
import zlib


class Rpx:
    def __init__(self, chemin: str):
        with open(chemin, "rb") as f:
            d = f.read()
        shoff, = struct.unpack_from(">I", d, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from(">HHH", d, 0x2E)
        self.secs = []
        for i in range(shnum):
            nm, typ, flg, addr, off, size, link, info, align, entsz = struct.unpack_from(">10I", d, shoff + i * shentsize)
            raw = d[off:off + size] if typ != 8 else b""
            if flg & 0x08000000 and raw:
                raw = zlib.decompress(raw[4:])
            self.secs.append(dict(i=i, typ=typ, addr=addr, link=link, info=info, data=raw))

    def octets(self, a: int, n: int) -> bytes:
        for s in self.secs:
            if s["addr"] and s["data"] and s["addr"] <= a and a + n <= s["addr"] + len(s["data"]):
                return s["data"][a - s["addr"]:a - s["addr"] + n]
        raise KeyError(hex(a))

    def mot(self, a: int) -> int:
        return struct.unpack(">I", self.octets(a, 4))[0]

    def reloc(self, a: int) -> int | None:
        """Valeur relogée d'un pointeur de données (S + A d'une entrée .rela)."""
        for s in self.secs:
            if s["typ"] != 4:
                continue
            cible = self.secs[s["info"]]
            if not (cible["addr"] <= a < cible["addr"] + max(len(cible["data"]), 1)):
                continue
            sym = self.secs[s["link"]]["data"]
            d = s["data"]
            for k in range(0, len(d), 12):
                off, inf, add = struct.unpack_from(">IIi", d, k)
                if off == a:
                    val, = struct.unpack_from(">I", sym, (inf >> 8) * 16 + 4)
                    return (val + add) & 0xFFFFFFFF
        return None
