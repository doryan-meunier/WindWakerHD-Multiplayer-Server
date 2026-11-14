"""BFRES (Wii U) : fichiers INCLUS seulement (groupe d'index n° 11 : room.dzr, stage.dzs, event_list.dat…).

Portage partiel de TWWHD-Randomizer ``filetypes/bfres.cpp`` (``readEmbedded``, ``replaceEmbeddedFile`` ;
SuperDude88 et contributeurs, licence MIT, Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt).

Pas de reconstruction du BFRES : un inclus remplacé est réécrit à sa place ; s'il change de taille, les données qui le
suivent sont décalées et les décalages relatifs des inclus suivants corrigés, comme TWWHD. C'est juste SEULEMENT si
les inclus sont les dernières données du BFRES (aucune autre donnée, ni la table des chaînes, après le premier
inclus déplacé) : invariant contrôlé à chaque remplacement (``inclus_en_dernier``) et sur le dump par les tests.
Rien de changé : les octets d'origine.
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat

MAGIE = b"FRES"
GROUPE = 11


def _arrondi(v: int, a: int) -> int:
    return v if a <= 1 else (v + a - 1) // a * a


class Inclus:
    __slots__ = ("nom", "desc", "debut", "taille")

    def __init__(self, nom: str, desc: int, debut: int, taille: int):
        self.nom, self.desc, self.debut, self.taille = nom, desc, debut, taille


class Bfres:
    def __init__(self, brut: bytes):
        d = bytes(brut)
        if len(d) < 0x6C or d[:4] != MAGIE:
            raise ErreurFormat("pas un BFRES")
        bom = struct.unpack_from(">H", d, 8)[0]
        if bom != 0xFEFF:
            raise ErreurFormat("BFRES : ordre des octets inattendu")
        self.taille_annoncee, self.align = struct.unpack_from(">II", d, 0x0C)
        self.chaines_taille, chaines_rel = struct.unpack_from(">Ii", d, 0x18)
        self.chaines = 0x1C + chaines_rel if chaines_rel else 0
        self.brut = bytearray(d)
        self.inclus: list[Inclus] = []
        pos = 0x20 + GROUPE * 4
        rel = struct.unpack_from(">i", d, pos)[0]
        if rel:
            g = pos + rel
            _lg, n = struct.unpack_from(">Ii", d, g)
            if not 0 <= n <= 4096:
                raise ErreurFormat("BFRES : groupe des inclus incohérent")
            for k in range(1, n + 1):
                e = g + 8 + 16 * k
                _sv, _l, _r, nom_rel, data_rel = struct.unpack_from(">IHHii", d, e)
                nom = ""
                if nom_rel:
                    o = e + 8 + nom_rel
                    z = d.find(b"\0", o)
                    if z < 0:
                        raise ErreurFormat("BFRES : nom d'inclus tronqué")
                    nom = d[o:z].decode("ascii", "replace")
                dp = e + 12 + data_rel
                off, sz = struct.unpack_from(">iI", d, dp)
                deb = dp + off
                if not (0 <= deb <= deb + sz <= len(d)):
                    raise ErreurFormat(f"BFRES : inclus {nom!r} hors du fichier")
                self.inclus.append(Inclus(nom, dp, deb, sz))
        self._origine = bytes(d)

    def noms(self) -> list[str]:
        return [i.nom for i in self.inclus]

    def _trouver(self, nom: str) -> Inclus:
        for i in self.inclus:
            if i.nom == nom:
                return i
        raise ErreurFormat(f"BFRES : inclus absent : {nom!r}")

    def lire(self, nom: str) -> bytes:
        i = self._trouver(nom)
        return bytes(self.brut[i.debut:i.debut + i.taille])

    def inclus_en_dernier(self, depuis: int | None = None) -> bool:
        """Toutes les données à partir de ``depuis`` (défaut : le premier inclus) ne sont que des inclus (et du
        bourrage plus petit que l'alignement du fichier), et la table des chaînes est avant."""
        if not self.inclus:
            return True
        tri = sorted(self.inclus, key=lambda i: i.debut)
        depuis = tri[0].debut if depuis is None else depuis
        if self.chaines and self.chaines + self.chaines_taille > depuis:
            return False
        if any(i.desc >= depuis for i in self.inclus):
            return False
        pos = depuis
        for i in tri:
            if i.debut < depuis:
                continue
            if i.debut < pos or i.debut - pos >= max(self.align, 0x20) + 0x2000:
                return False
            pos = i.debut + i.taille
        return len(self.brut) - pos < max(self.align, 0x20) + 0x2000

    def remplacer(self, nom: str, data: bytes) -> None:
        cible = self._trouver(nom)
        data = bytes(data)
        if len(data) == cible.taille:
            self.brut[cible.debut:cible.debut + cible.taille] = data
            return
        if not self.inclus_en_dernier(cible.debut):
            raise ErreurFormat(f"BFRES : d'autres données suivent l'inclus {nom!r} : remplacement de taille "
                               "différente refusé")
        apres = sorted((i for i in self.inclus if i.debut > cible.debut), key=lambda i: i.debut)
        delta = len(data) - cible.taille
        if apres:
            a = min(0x2000, apres[0].debut & -apres[0].debut) or 0x2000
            delta = _arrondi(delta, a) if delta > 0 else -_arrondi(-delta, a)
            fin = cible.debut + cible.taille
            if fin + delta < cible.debut + len(data):
                delta += a
        ancien_fin = cible.debut + cible.taille
        reste = bytes(self.brut[ancien_fin:])
        if apres:
            bourrage = delta - (len(data) - cible.taille)
            nouveau = data + b"\0" * max(0, bourrage)
            reste_garde = reste if bourrage >= 0 else reste[-bourrage:]
        else:
            nouveau, reste_garde = data, b""
            # dernier inclus : on garde le bourrage d'alignement de fin d'origine
            fin_alignee = _arrondi(cible.debut + len(data), max(self.align, 1))
            reste_garde = b"\0" * (fin_alignee - (cible.debut + len(data)))
        self.brut[cible.debut:] = nouveau + reste_garde
        struct.pack_into(">I", self.brut, cible.desc + 4, len(data))
        for i in apres:
            off = struct.unpack_from(">i", self.brut, i.desc)[0]
            struct.pack_into(">i", self.brut, i.desc, off + delta)
            i.debut += delta
        cible.taille = len(data)
        struct.pack_into(">I", self.brut, 0x0C, len(self.brut))

    def octets(self) -> bytes:
        return bytes(self.brut)
