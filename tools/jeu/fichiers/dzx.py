"""DZX : salles et stages (``room.dzr``, ``stage.dzs``) de Wind Waker.

Portage de TWWHD-Randomizer ``filetypes/dzx.cpp`` (SuperDude88 et contributeurs, licence MIT,
Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt), réécrit en Python.

Format : u32 nombre de chunks, puis un en-tête de 12 octets par chunk (type sur 4 caractères, nombre d'entrées,
décalage absolu de la première), puis les entrées. Couches : ``ACT0``-``ACTb``, ``TRE0``-``TREb``, ``SCO0``-``SCOb``
(``ACTR``, ``TRES``, ``SCOB`` : sans couche). RTBL : table à sous-entrées (traitée à part, comme TWWHD).

Deux façons d'écrire :

* **sur place** (``octets()`` tant qu'aucune entrée n'est ajoutée) : les entrées changées sont réécrites à leur
  place dans le fichier d'origine ; rien de changé = les octets d'origine. Retirer une entrée la remplit de zéros
  (comme TWWHD) : c'est aussi sur place ;
* **reconstruction** (après ``ajouter``) : écrivain de TWWHD (en-têtes, entrées alignées sur 4 avec 0xFF, fin
  alignée sur 0x20 avec 0xFF).
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat

TAILLES = {"SCOB": 0x24, "ACTR": 0x20, "TRES": 0x20, "PLYR": 0x20, "SCLS": 0xC, "STAG": 0x14, "FILI": 0x8,
           "SHIP": 0x10, "RTBL": 0x4, "RPAT": 0xC, "RPPN": 0x10, "TGOB": 0x20, "TGSC": 0x24, "DOOR": 0x24,
           "TGDR": 0x24, "EVNT": 0x18, "2DMA": 0x38, "MULT": 0xC, "FLOR": 0x14, "LBNK": 0x1, "SOND": 0x1C,
           "RCAM": 0x14, "RARO": 0x14, "DMAP": 0x10, "EnvR": 0x8, "Colo": 0xC, "Pale": 0x6C, "Virt": 0x38,
           "LGHT": 0x1C, "LGTV": 0x1C, "MECO": 0x2, "MEMA": 0x4, "PATH": 0xC, "PPNT": 0x10, "CAMR": 0x14,
           "AROB": 0x14}
COUCHES = "0123456789ab"
SANS_COUCHE = None
_PREFIXES = {"TRE": ("TRES", "S"), "ACT": ("ACTR", "R"), "SCO": ("SCOB", "B")}


class Entree:
    __slots__ = ("data", "origine", "pos")

    def __init__(self, data: bytes, pos: int | None):
        self.data = bytearray(data)
        self.origine = bytes(data)
        self.pos = pos

    @property
    def nom(self) -> str:
        """Nom d'acteur (ACTR, TRES, SCOB, PLYR, TGOB… : 8 premiers octets)."""
        return bytes(self.data[:8]).split(b"\0", 1)[0].decode("ascii", "replace")


class Chunk:
    def __init__(self, type_: str, couche, entrees: list[Entree], brut_type: str):
        self.type = type_
        self.couche = couche
        self.entrees = entrees
        self.brut_type = brut_type


def _type_et_couche(t: str):
    p = _PREFIXES.get(t[:3])
    if p is not None and t not in ("TRES", "ACTR", "SCOB"):
        if t[3] in COUCHES:
            return p[0], COUCHES.index(t[3])
        raise ErreurFormat(f"DZX : couche inconnue {t!r}")
    return t, SANS_COUCHE


class Dzx:
    def __init__(self, brut: bytes):
        d = bytes(brut)
        if len(d) < 4:
            raise ErreurFormat("DZX : fichier vide")
        n = struct.unpack_from(">I", d, 0)[0]
        if n == 0 or 4 + 12 * n > len(d):
            raise ErreurFormat("DZX : nombre de chunks incohérent")
        self.brut = d
        self.chunks: list[Chunk] = []
        self.ajoute = False
        for c in range(n):
            o = 4 + 12 * c
            bt = d[o:o + 4].decode("ascii", "replace")
            cnt, prem = struct.unpack_from(">II", d, o + 4)
            typ, couche = _type_et_couche(bt)
            if typ not in TAILLES:
                raise ErreurFormat(f"DZX : chunk inconnu {bt!r}")
            entrees = []
            if typ == "RTBL":
                for k in range(cnt):
                    p = prem + 4 * k
                    sous = struct.unpack_from(">I", d, p)[0]
                    nb = d[sous]
                    adj = struct.unpack_from(">I", d, sous + 4)[0]
                    entrees.append(Entree(d[p:p + 4] + d[sous:sous + 8] + d[adj:adj + nb], None))
            else:
                sz = TAILLES[typ]
                if prem + sz * cnt > len(d):
                    raise ErreurFormat(f"DZX : chunk {bt} tronqué")
                for k in range(cnt):
                    p = prem + sz * k
                    entrees.append(Entree(d[p:p + sz], p))
            self.chunks.append(Chunk(typ, couche, entrees, bt))

    def entrees(self, type_: str, couche="toutes") -> list[Entree]:
        return [e for c in self.chunks if c.type == type_ and (couche == "toutes" or c.couche == couche)
                for e in c.entrees]

    def retirer(self, e: Entree) -> None:
        """Comme TWWHD : l'entrée est remplie de zéros (le jeu l'ignore), sans rien déplacer."""
        e.data[:] = b"\0" * len(e.data)

    def ajouter(self, type_: str, couche, data: bytes) -> Entree:
        if type_ not in TAILLES or type_ == "RTBL" or len(data) != TAILLES[type_]:
            raise ErreurFormat(f"DZX : entrée {type_} de {len(data)} octets refusée")
        e = Entree(data, None)
        for c in self.chunks:
            if c.type == type_ and c.couche == couche:
                c.entrees.append(e)
                break
        else:
            bt = type_ if couche is None else {"TRES": "TRE", "ACTR": "ACT", "SCOB": "SCO"}[type_] + COUCHES[couche]
            self.chunks.append(Chunk(type_, couche, [e], bt))
        self.ajoute = True
        return e

    def octets(self) -> bytes:
        if not self.ajoute:
            out = bytearray(self.brut)
            for c in self.chunks:
                for e in c.entrees:
                    if e.pos is not None and bytes(e.data) != e.origine:
                        if len(e.data) != len(e.origine):
                            raise ErreurFormat("DZX : taille d'entrée changée")
                        out[e.pos:e.pos + len(e.data)] = e.data
                    elif e.pos is None and bytes(e.data) != e.origine:
                        raise ErreurFormat("DZX : entrée RTBL modifiée : non prévu")
            return bytes(out)
        return self._reconstruire()

    def _reconstruire(self) -> bytes:
        chunks = [c for c in self.chunks if c.entrees]
        out = bytearray(struct.pack(">I", len(chunks)))
        for c in chunks:
            out += c.brut_type.encode("ascii") + struct.pack(">II", len(c.entrees), 0)
        for i, c in enumerate(chunks):
            while len(out) % 4:
                out.append(0xFF)
            prem = len(out)
            struct.pack_into(">I", out, 4 + 12 * i + 8, prem)
            if c.type == "RTBL":
                n = len(c.entrees)
                rooms = prem + n * 0xC
                tete = bytearray()
                sous = bytearray()
                salles = bytearray()
                for k, e in enumerate(c.entrees):
                    so = prem + n * 4 + 8 * k
                    tete += struct.pack(">I", so)
                    nb = e.data[4]
                    sous += bytes(e.data[4:8]) + struct.pack(">I", rooms)
                    salles += bytes(e.data[12:12 + nb])
                    rooms += nb
                out += tete + sous + salles
            else:
                for e in c.entrees:
                    out += e.data
        while len(out) % 0x20:
            out.append(0xFF)
        return bytes(out)
