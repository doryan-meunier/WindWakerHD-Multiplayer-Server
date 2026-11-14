"""Chemins de fichiers du jeu à étages, et session d'édition (arbre de cache, remballage de bas en haut).

Forme des chemins (celle de TWWHD-Randomizer, ``RandoSession`` ; SuperDude88 et contributeurs, licence MIT,
Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt) :

    content/Common/Pack/permanent_2d_UsEnglish.pack@SARC@Title_00_msbt.szs@YAZ0@SARC@Title_00.msbt@MSBT
    content/Common/Stage/sea_Room29.szs@YAZ0@SARC@Room29.bfres@BFRES@room.dzr@DZX

Après le fichier racine (relatif au dossier du dump), chaque étape dit comment lire l'étage courant : ``SARC`` et
``BFRES`` sont suivis du nom du fichier membre ; ``YAZ0`` décompresse ; ``MSBT``, ``DZX``, ``EVENTS``, ``CHARTS``, ``BRUT``
(dernière étape) disent quel objet d'édition rendre ; sans étape finale, ``Session.brut`` rend les octets.

La session ne lit le dump qu'en LECTURE (``open(…, "rb")``), garde chaque étage extrait une seule fois, et
``Session.resultats()`` remballe de bas en haut les seules branches touchées : un fichier racine n'est rendu que
s'il a changé. Monofil et déterministe.
"""
from __future__ import annotations

import os

from jeu.fichiers import ErreurFormat, bfres_inclus, charts, dzx, events, msbt, sarc, yaz0  # noqa: F401

class Brut:
    """Feuille ``BRUT`` : octets d'un fichier sans format connu ici (par exemple ``room.dzb``, collisions), édités
    sur place à taille égale (``ecrire``, qui relit d'abord les octets d'origine attendus)."""

    def __init__(self, brut: bytes):
        self.origine = bytes(brut)
        self.brut = bytearray(brut)

    def ecrire(self, pos: int, avant: bytes, apres: bytes) -> None:
        if len(avant) != len(apres) or pos < 0 or pos + len(avant) > len(self.brut):
            raise ErreurFormat(f"BRUT : écriture hors du fichier à 0x{pos:X}")
        if bytes(self.brut[pos:pos + len(avant)]) != avant:
            raise ErreurFormat(f"BRUT : octets inattendus à 0x{pos:X} ({bytes(self.brut[pos:pos + len(avant)]).hex()} "
                               f"au lieu de {avant.hex()})")
        self.brut[pos:pos + len(apres)] = apres

    def octets(self) -> bytes:
        return bytes(self.brut)


FEUILLES = {"MSBT": msbt.Msbt, "DZX": dzx.Dzx, "EVENTS": events.ListeEvenements, "BRUT": Brut,
            "CHARTS": charts.Cartes}
CONTENEURS = ("SARC", "BFRES")


def decouper(chemin: str) -> tuple[str, list[tuple[str, str | None]]]:
    """(racine, [(étape, nom de membre ou None)]). Lève ErreurFormat sur une forme inconnue."""
    parts = chemin.split("@")
    racine = parts[0].replace("\\", "/")
    if not racine or racine.startswith("/") or ".." in racine.split("/") or ":" in racine:
        raise ErreurFormat(f"chemin racine refusé : {racine!r}")
    etapes: list[tuple[str, str | None]] = []
    i = 1
    while i < len(parts):
        t = parts[i]
        if t in CONTENEURS:
            if i + 1 >= len(parts) or not parts[i + 1]:
                raise ErreurFormat(f"chemin {chemin!r} : nom de membre manquant après {t}")
            etapes.append((t, parts[i + 1]))
            i += 2
        elif t == "YAZ0":
            etapes.append((t, None))
            i += 1
        elif t in FEUILLES:
            if i != len(parts) - 1:
                raise ErreurFormat(f"chemin {chemin!r} : {t} doit être la dernière étape")
            etapes.append((t, None))
            i += 1
        else:
            raise ErreurFormat(f"chemin {chemin!r} : étape inconnue {t!r}")
    return racine, etapes


class _Noeud:
    """Un étage : octets d'origine (``brut``), et selon ce qu'on en a fait : conteneur ouvert (SARC/BFRES) et ses
    membres, enfant décompressé (YAZ0), ou objet d'édition (feuille)."""

    def __init__(self, brut_fn):
        self._brut_fn = brut_fn
        self._brut: bytes | None = None
        self.conteneur = None           # sarc.Sarc | bfres_inclus.Bfres
        self.genre = None               # "SARC" | "BFRES"
        self.membres: dict[str, _Noeud] = {}
        self.dec: _Noeud | None = None  # enfant YAZ0
        self.feuille = None
        self.genre_feuille = None

    def brut(self) -> bytes:
        if self._brut is None:
            self._brut = self._brut_fn()
        return self._brut

    def ouvrir(self, genre: str):
        if self.conteneur is None:
            self.conteneur = sarc.Sarc(self.brut()) if genre == "SARC" else bfres_inclus.Bfres(self.brut())
            self.genre = genre
        elif self.genre != genre:
            raise ErreurFormat(f"étage lu à la fois comme {self.genre} et {genre}")
        return self.conteneur

    def membre(self, genre: str, nom: str) -> "_Noeud":
        c = self.ouvrir(genre)
        if nom not in self.membres:
            if genre == "SARC":
                data = c._origine[nom] if nom in c._origine else c.lire(nom)
            else:
                data = c.lire(nom)
            self.membres[nom] = _Noeud(lambda d=data: d)
        return self.membres[nom]

    def decompresse(self) -> "_Noeud":
        if self.dec is None:
            self.dec = _Noeud(lambda: yaz0.decompresser(self.brut()))
        return self.dec

    def final(self, marge: float) -> bytes:
        if self.feuille is not None:
            return self.feuille.octets()
        if self.dec is not None:
            nouveau = self.dec.final(marge)
            if nouveau == self.dec.brut():
                return self.brut()
            return yaz0.recompresser(self.brut(), nouveau, self.dec.brut(), marge=marge)
        if self.conteneur is not None:
            for nom in sorted(self.membres):
                self.conteneur.remplacer(nom, self.membres[nom].final(marge))
            return self.conteneur.octets()
        return self.brut()


class Session:
    """Édition de fichiers du dump. ``jeu`` : dossier du dump (code/, content/, meta/), LU SEULEMENT."""

    def __init__(self, jeu: str, marge: float = yaz0.MARGE, verifier=None):
        """``verifier(rel, octets)`` : appelé sur chaque fichier racine dès sa lecture, AVANT toute analyse (relecture
        adverse : un fichier d'une autre version doit être refusé proprement, pas faire planter un lecteur)."""
        self.jeu = os.path.abspath(jeu)
        self.marge = marge
        self.verifier = verifier
        self.racines: dict[str, _Noeud] = {}
        self.lus: dict[str, bytes] = {}       # fichiers racine lus (octets d'origine), pour leurs empreintes

    def _racine(self, rel: str) -> _Noeud:
        if rel not in self.racines:
            chemin = os.path.join(self.jeu, *rel.split("/"))

            def lire(c=chemin, r=rel):
                try:
                    with open(c, "rb") as f:
                        d = f.read()
                except OSError as e:
                    raise ErreurFormat(f"fichier du jeu illisible : {r} ({e.strerror or e})") from None
                if self.verifier is not None:
                    self.verifier(r, d)
                self.lus[r] = d
                return d
            self.racines[rel] = _Noeud(lire)
        return self.racines[rel]

    def _noeud(self, chemin: str):
        racine, etapes = decouper(chemin)
        n = self._racine(racine)
        fin = None
        for t, nom in etapes:
            if t in CONTENEURS:
                n = n.membre(t, nom)
            elif t == "YAZ0":
                n = n.decompresse()
            else:
                fin = t
        return n, fin

    def brut(self, chemin: str) -> bytes:
        """Octets D'ORIGINE de l'étage désigné."""
        return self._noeud(chemin)[0].brut()

    def ouvrir(self, chemin: str):
        """Objet d'édition de la feuille (Msbt, Dzx, ListeEvenements), le même à chaque appel."""
        n, fin = self._noeud(chemin)
        if fin is None:
            raise ErreurFormat(f"chemin {chemin!r} : pas d'étape finale (MSBT, DZX, EVENTS)")
        if n.feuille is None:
            n.feuille = FEUILLES[fin](n.brut())
            n.genre_feuille = fin
        elif n.genre_feuille != fin:
            raise ErreurFormat(f"{chemin!r} : feuille déjà ouverte comme {n.genre_feuille}")
        return n.feuille

    def resultats(self) -> dict[str, bytes]:
        """{fichier racine: nouveaux octets} des seuls fichiers racine qui changent, triés par chemin."""
        out = {}
        for rel in sorted(self.racines):
            n = self.racines[rel]
            nouveau = n.final(self.marge)
            if nouveau != n.brut():
                out[rel] = nouveau
        return out
