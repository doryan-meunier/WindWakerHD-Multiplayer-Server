"""event_list.dat : liste d'événements d'une stage (événements, acteurs, actions, propriétés, valeurs).

Portage partiel de TWWHD-Randomizer ``filetypes/events.cpp`` (SuperDude88 et contributeurs, licence MIT,
Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt).

Étape 1 (docs/etudes/rando_fichiers.md) : lecture et éditions À TAILLE ÉGALE seulement (nom d'action sur 0x20
octets, valeur entière d'une propriété). Rien ne bouge dans le fichier ; rien de changé = les octets d'origine.
Étape 3 : actions retirées de la liste d'un acteur et drapeaux de fin d'un événement, toujours À TAILLE ÉGALE (voir
``retirer_actions``). L'ajout d'acteurs, d'actions ou de propriétés n'a servi à aucun élément porté : non écrit.
Étape 4 (lot 8) : texte d'une propriété remplacé dans sa place (``poser_chaine``, vent de retour des boss).

En-tête (0x40) : décalage et nombre des événements (0xB0 chacun), acteurs (0x50), actions (0x50), propriétés (0x40),
flottants, entiers, puis décalage et taille de la table des chaînes.
"""
from __future__ import annotations

import struct

from jeu.fichiers import ErreurFormat

T_EVENEMENT, T_ACTEUR, T_ACTION, T_PROPRIETE = 0xB0, 0x50, 0x50, 0x40
TYPE_FLOTTANT, TYPE_VECTEUR, TYPE_ENTIER, TYPE_CHAINE = 0, 1, 3, 4


def _nom(d, o: int) -> str:
    return bytes(d[o:o + 0x20]).split(b"\0", 1)[0].decode("ascii", "replace")


class Propriete:
    def __init__(self, d, o: int):
        self.pos = o
        self.nom = _nom(d, o)
        (self.index, self.type, self.donnee, self.taille, self.suivante) = struct.unpack_from(">IIIIi", d, o + 0x20)


class Action:
    def __init__(self, d, o: int):
        self.pos = o
        self.nom = _nom(d, o)
        self.dup, self.index = struct.unpack_from(">II", d, o + 0x20)
        self.drapeau, self.premiere_prop, self.suivante = struct.unpack_from(">iii", d, o + 0x34)


class Acteur:
    def __init__(self, d, o: int):
        self.pos = o
        self.nom = _nom(d, o)
        self.staff_id, self.index, self.drapeau, self.staff_type, self.action_initiale = \
            struct.unpack_from(">IIiIi", d, o + 0x20)


class Evenement:
    def __init__(self, d, o: int):
        self.pos = o
        self.nom = _nom(d, o)
        self.index = struct.unpack_from(">I", d, o + 0x20)[0]
        self.acteurs_idx = list(struct.unpack_from(">20i", d, o + 0x2C))
        self.nb_acteurs = struct.unpack_from(">I", d, o + 0x7C)[0]


class ListeEvenements:
    def __init__(self, brut: bytes):
        d = bytes(brut)
        if len(d) < 0x40:
            raise ErreurFormat("event_list : fichier trop court")
        v = struct.unpack_from(">14I", d, 0)
        (oe, ne, oa, na, oc, nc, op, np_, of, nf, oi, ni, os_, ts) = v
        if oe != 0x40:
            raise ErreurFormat("event_list : en-tête inattendu")
        for o, n, t in ((oe, ne, T_EVENEMENT), (oa, na, T_ACTEUR), (oc, nc, T_ACTION), (op, np_, T_PROPRIETE),
                        (of, nf, 4), (oi, ni, 4), (os_, ts, 1)):
            if o + n * t > len(d):
                raise ErreurFormat("event_list : table hors du fichier")
        self.brut = bytearray(d)
        self.origine = d
        self.evenements = [Evenement(d, oe + T_EVENEMENT * i) for i in range(ne)]
        self.acteurs = [Acteur(d, oa + T_ACTEUR * i) for i in range(na)]
        self.actions = [Action(d, oc + T_ACTION * i) for i in range(nc)]
        self.proprietes = [Propriete(d, op + T_PROPRIETE * i) for i in range(np_)]
        self.o_entiers, self.n_entiers = oi, ni
        self.o_flottants, self.n_flottants = of, nf

    # -- lecture
    def evenement(self, nom: str) -> Evenement:
        for e in self.evenements:
            if e.nom == nom:
                return e
        raise ErreurFormat(f"event_list : événement absent : {nom!r}")

    def acteur(self, evenement: str, nom: str) -> Acteur:
        e = self.evenement(evenement)
        for k in e.acteurs_idx[:e.nb_acteurs]:
            if 0 <= k < len(self.acteurs) and self.acteurs[k].nom == nom:
                return self.acteurs[k]
        raise ErreurFormat(f"event_list : acteur {nom!r} absent de {evenement!r}")

    def actions_de(self, acteur: Acteur) -> list[Action]:
        out, k, vus = [], acteur.action_initiale, set()
        while 0 <= k < len(self.actions) and k not in vus:
            vus.add(k)
            out.append(self.actions[k])
            k = self.actions[k].suivante
        return out

    def proprietes_de(self, action: Action) -> list[Propriete]:
        out, k, vus = [], action.premiere_prop, set()
        while 0 <= k < len(self.proprietes) and k not in vus:
            vus.add(k)
            out.append(self.proprietes[k])
            k = self.proprietes[k].suivante
        return out

    def entier(self, p: Propriete) -> int:
        if p.type != TYPE_ENTIER or not 0 <= p.donnee < self.n_entiers:
            raise ErreurFormat(f"event_list : propriété {p.nom!r} : pas un entier")
        return struct.unpack_from(">i", self.brut, self.o_entiers + 4 * p.donnee)[0]

    # -- éditions à taille égale
    def renommer_action(self, a: Action, ancien: str, nouveau: str) -> None:
        if a.nom != ancien:
            raise ErreurFormat(f"event_list : action {a.nom!r} au lieu de {ancien!r}")
        enc = nouveau.encode("ascii")
        if len(enc) >= 0x20:
            raise ErreurFormat("event_list : nom d'action trop long")
        self.brut[a.pos:a.pos + 0x20] = enc + b"\0" * (0x20 - len(enc))
        a.nom = nouveau

    def poser_entier(self, p: Propriete, ancien: int, nouveau: int) -> None:
        if p.taille != 1:
            raise ErreurFormat(f"event_list : propriété {p.nom!r} : plusieurs valeurs")
        if self.entier(p) != ancien:
            raise ErreurFormat(f"event_list : propriété {p.nom!r} vaut {self.entier(p)} au lieu de {ancien}")
        struct.pack_into(">i", self.brut, self.o_entiers + 4 * p.donnee, nouveau)

    def chaine(self, p: Propriete) -> str:
        """Valeur d'une propriété texte (jusqu'au premier octet nul de sa place dans la table des chaînes)."""
        o_ch, t_ch = struct.unpack_from(">II", self.brut, 0x30)
        if p.type != TYPE_CHAINE or not 0 <= p.donnee <= p.donnee + p.taille <= t_ch:
            raise ErreurFormat(f"event_list : propriété {p.nom!r} : pas une chaîne")
        brut = bytes(self.brut[o_ch + p.donnee:o_ch + p.donnee + p.taille])
        return brut.split(b"\0", 1)[0].decode("ascii", "replace")

    def poser_chaine(self, p: Propriete, ancien: str, nouveau: str) -> None:
        """Lot 8 (vent de retour d'un boss) : texte d'une propriété remplacé DANS SA PLACE (``taille`` octets, fin
        complétée de zéros) ; refus si le nouveau texte ne tient pas avec son octet nul."""
        if self.chaine(p) != ancien:
            raise ErreurFormat(f"event_list : propriété {p.nom!r} vaut {self.chaine(p)!r} au lieu de {ancien!r}")
        enc = nouveau.encode("ascii")
        if len(enc) + 1 > p.taille:
            raise ErreurFormat(f"event_list : {nouveau!r} ne tient pas dans {p.taille} octets")
        o_ch = struct.unpack_from(">I", self.brut, 0x30)[0]
        self.brut[o_ch + p.donnee:o_ch + p.donnee + p.taille] = enc + b"\0" * (p.taille - len(enc))

    # -- éditions de structure, toujours à taille égale (étape 3)
    #
    # TWWHD retire des actions de la liste d'un acteur (``actions.erase``) puis réécrit tout le fichier. Ici le
    # fichier ne bouge pas : l'action gardée qui précède les actions retirées pointe sur celle qui les suit (ou sur
    # -1 en fin de liste). Les actions retirées restent dans la table, mais plus aucune liste ne les atteint : le
    # jeu ne les exécute jamais, exactement comme si elles n'existaient plus. Un événement dont la fin attendait le
    # drapeau d'une action retirée reçoit ses nouveaux drapeaux de fin (``poser_drapeaux_fin``), comme TWWHD.
    def _poser_suivante(self, a: Action, suivante: int) -> None:
        struct.pack_into(">i", self.brut, a.pos + 0x3C, suivante)
        a.suivante = suivante

    def retirer_actions(self, evenement: str, acteur: str, debut: int, fin: int | None = None) -> list[Action]:
        """Retire les actions ``debut`` à ``fin`` (exclue ; None : jusqu'au bout) de la liste de ``acteur`` dans
        ``evenement`` (``actions.erase(begin()+debut, begin()+fin)`` de TWWHD). Rend la liste gardée."""
        ac = self.acteur(evenement, acteur)
        liste = self.actions_de(ac)
        fin = len(liste) if fin is None else fin
        if not 0 < debut < fin <= len(liste):
            raise ErreurFormat(f"event_list : {evenement}/{acteur} : actions {debut}..{fin} hors de la liste "
                               f"({len(liste)})")
        suite = self.actions.index(liste[fin]) if fin < len(liste) else -1
        self._poser_suivante(liste[debut - 1], suite)
        return liste[:debut] + liste[fin:]

    def poser_drapeaux_fin(self, evenement: str, drapeaux) -> None:
        """Les trois drapeaux de fin de l'événement (``ending_flags`` de TWWHD, -1 = aucun)."""
        e = self.evenement(evenement)
        d = list(drapeaux) + [-1] * (3 - len(drapeaux))
        if len(d) != 3:
            raise ErreurFormat("event_list : trois drapeaux de fin au plus")
        struct.pack_into(">3i", self.brut, e.pos + 0x88, *d)

    def drapeaux_fin(self, evenement: str) -> tuple:
        return struct.unpack_from(">3i", self.brut, self.evenement(evenement).pos + 0x88)

    def octets(self) -> bytes:
        return bytes(self.brut)
