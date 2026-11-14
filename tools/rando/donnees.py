"""Randomizer complet, portage TWWHD lot 1 : lieux « donnée » (courrier, boutique de l'Île du Pic, enchères,
personnages à table de Mercantîle). L'objet de ces lieux est UN octet d'une table du programme du jeu : le client
l'écrit dans la mémoire de Cemu (rien dans le graphic pack, aucun fichier du jeu touché). Adresses et objets d'origine :
``rando.SITES_DONNEES`` (repris de TWWHD-Randomizer, Copyright (c) SuperDude88, licence MIT, voir
tools/rando/logique/LICENSE_TWWHD.txt ; relus dans le RPX par tests/test_rando_donnees.py).

Écriture GARDÉE, dans cet ordre :
  1. les témoins (``rando.TEMOINS`` : les tables du jeu autour de nos octets) doivent être exactement ceux du jeu
     d'origine, hors des octets que nous écrivons : autre version du jeu, adresse fausse -> RIEN n'est écrit ;
  2. chaque octet à nous doit valoir soit la valeur voulue, soit sa valeur d'origine, soit (objets seulement) un autre
     objet de la liste du pack, donc laissé par une AUTRE table de ces outils dans le même Cemu (l'hôte a changé de
     graine sans relancer le jeu) ; toute autre valeur -> RIEN n'est écrit, nulle part (tout ou rien) ;
  3. s'il reste quelque chose à écrire : seulement hors chargement (salle stable, pas de cinématique : ``libre``) ;
  4. chaque octet écrit est relu.
L'état voulu est défini pour TOUS les sites, tout le temps : objet de la table du serveur si elle est chargée dans le
pack, objet d'origine sinon (partie sans randomizer, --no-rando, lieu non mélangé). Relu toutes les ``periode``
secondes : Cemu relancé (octets revenus à l'origine) ou reconnexion -> réécrit tout seul.

Le point 2 est plus large que « l'origine ou la valeur voulue » : sans lui, changer de partie sans relancer Cemu
bloquerait tout. Il reste sûr parce que les témoins du point 1 entourent chaque octet et qu'un objet de la liste du
pack à ces adresses ne peut venir que de nous."""
from __future__ import annotations

from rando import noyau as rando

PERIODE = 2.0
# (étape 5, rando/lot9.py : et les objets des lieux ajoutés, hors de la liste du pack : lettres, voile rapide ;
# ils ne peuvent être là, entre les témoins, que laissés par une autre table de ces outils)
_OBJETS_LAISSES = frozenset(rando.PERMIS2 - rando.OBJETS_DE_DONJON) | rando.OBJETS_DON_SEULS


def adresses() -> dict:
    """Tous les octets que ce module peut écrire : adresse -> (site, genre) ; genre 0 : objet, 1 : accompagnement."""
    out = {}
    for k, (_g, _nom, _o, adrs, extras, _i) in enumerate(rando.SITES_DONNEES):
        for a in adrs:
            out[a] = (k, 0)
        for a, _v0, _v1 in extras:
            out[a] = (k, 1)
    return out


def voulu(r) -> dict | None:
    """Objet voulu par site ({site: objet}) d'après l'état du client ``r`` (RandoClient) ; None : ne rien toucher
    pour l'instant (pas encore de welcome, ou table du serveur pas encore chargée dans le pack)."""
    origine = {k: s[2] for k, s in enumerate(rando.SITES_DONNEES)}
    if not r.enabled:
        return origine                       # --no-rando : une session précédente a pu laisser des objets changés
    v = r.voulu
    if v is rando.RandoClient.INCONNU:
        return None
    if not isinstance(v, rando.Table2):
        return origine                       # partie sans randomizer, ou palier 1 (coffres seulement)
    e = r.etat_pack or {}
    if e.get("etat") != v.etat or e.get("emp") != v.emp_u32:
        return None                          # table pas (encore) dans le pack : coffres et données changent ensemble
    for k, (o, n) in v.entrees_don.items():
        if 0 <= k < len(rando.SITES_DONNEES) and o == origine[k]:
            origine[k] = n
    return origine


def cibles(objets: dict) -> list[tuple[int, int, int]]:
    """Écritures voulues, DANS L'ORDRE : (adresse, valeur, genre). Lieu changé : accompagnement d'abord (bit « objet
    unique » de la boutique retiré avant que l'objet change) ; lieu remis à l'origine : l'objet d'abord."""
    out = []
    for k, (_g, _nom, o, adrs, extras, _i) in enumerate(rando.SITES_DONNEES):
        n = objets.get(k, o)
        obj = [(a, n, 0) for a in adrs]
        ext = [(a, v1 if n != o else v0, 1) for a, v0, v1 in extras]
        out += (ext + obj) if n != o else (obj + ext)
    return out


def lire(mem) -> dict | None:
    """Octets actuels de toutes nos adresses ({adresse: octet}) si TOUS les témoins sont ceux du jeu d'origine hors de
    ces adresses ; None sinon (ou lecture impossible)."""
    a_nous = adresses()
    out = {}
    for base, hexa in rando.TEMOINS:
        attendu = bytes.fromhex(hexa)
        raw = mem.try_read(base, len(attendu))
        if raw is None or len(raw) != len(attendu):
            return None
        for i, (x, y) in enumerate(zip(raw, attendu)):
            if base + i in a_nous:
                out[base + i] = x
            elif x != y:
                return None
    return out if set(out) == set(a_nous) else None


def acceptable(adresse: int, actuel: int, valeur: int) -> bool:
    """L'octet ``actuel`` peut-il être remplacé par ``valeur`` (point 2 de la garde) ?"""
    k, genre = adresses()[adresse]
    s = rando.SITES_DONNEES[k]
    if actuel == valeur:
        return True
    if genre == 0:
        return actuel == s[2] or actuel in _OBJETS_LAISSES
    return any(a == adresse and actuel in (v0, v1) for a, v0, v1 in s[4])


class Ecrivain:
    """État de l'écriture gardée côté client. ``step`` ne lève jamais pour une lecture ratée ; renvoie le nombre
    d'octets écrits à cet appel."""

    def __init__(self, periode: float = PERIODE):
        self.periode = periode
        self.t = float("-inf")
        self.ecrits = 0                      # octets écrits depuis le début (statut)
        self.etat = ""                       # dernier état, en clair (statut)
        self._dit = ""                       # dernière note donnée (une fois par raison)

    def _note(self, r, cle: str, texte: str) -> None:
        if self._dit != cle:
            self._dit = cle
            r.notes.append(texte)

    def step(self, mem, r, libre: bool, now: float) -> int:
        if now - self.t < self.periode:
            return 0
        self.t = now
        objets = voulu(r)
        if objets is None:
            self.etat = "en attente de la table"
            return 0
        origine = all(objets[k] == s[2] for k, s in enumerate(rando.SITES_DONNEES))
        actuels = lire(mem)
        if actuels is None:
            # témoins absents : autre version du jeu, jeu pas encore chargé, lecture ratée. Rien n'est écrit.
            r.don = False
            self.etat = "refusé (tables du jeu non reconnues)"
            if not origine:
                self._note(r, "temoins", "RANDOMIZER COMPLET : les tables du jeu (courrier, boutique, enchères) ne "
                           "sont pas celles de la version USA v0 attendue : RIEN n'y est écrit, ces lieux gardent "
                           "leur objet d'origine sur ce PC (l'objet prévu par la table y serait perdu pour tout le "
                           "monde). Vérifie la version du jeu, relance Cemu puis le client")
            return 0
        aecrire = []
        for a, v, genre in cibles(objets):
            if actuels[a] == v:
                continue
            if not acceptable(a, actuels[a], v):
                r.don = False
                self.etat = "refusé (octet inattendu)"
                self._note(r, "octet", f"RANDOMIZER COMPLET : octet inattendu dans une table du jeu (0x{a:08X} = "
                           f"0x{actuels[a]:02X}) : RIEN n'est écrit dans le courrier, la boutique ni les enchères "
                           "(autre outil qui modifie le jeu ?). Relance Cemu puis le client")
                return 0
            aecrire.append((a, v))
        r.don = True
        if not aecrire:
            self.etat = "d'origine" if origine else "en place"
            return 0
        if not libre:
            self.etat = "en attente (chargement ou cinématique)"
            self.t = now - self.periode + 0.5       # réessaie vite : la salle se stabilise
            return 0
        n = 0
        for a, v in aecrire:
            try:
                mem.write(a, bytes((v,)))
            except OSError as exc:
                self.etat = "écriture refusée par le système"
                self._note(r, "ecriture", f"randomizer : écriture impossible dans le jeu ({exc}) : courrier, "
                           "boutique et enchères pas (encore) changés ; nouvel essai tout seul")
                return n
            relu = mem.try_read(a, 1)
            if relu is None or relu[0] != v:
                self.etat = "écriture non relue"
                self._note(r, "relu", f"randomizer : octet écrit non relu (0x{a:08X}) : courrier, boutique et "
                           "enchères pas (encore) changés ; nouvel essai tout seul")
                return n
            n += 1
        self.ecrits += n
        self.etat = "d'origine" if origine else "en place"
        self._dit = ""
        changes = sum(1 for k, s in enumerate(rando.SITES_DONNEES) if objets[k] != s[2])
        r.notes.append(f"randomizer complet : {changes} lieu(x) du courrier, de la boutique, des enchères et des "
                       f"personnages changé(s) dans ce jeu ({n} octet(s) écrit(s))" if not origine else
                       "randomizer : courrier, boutique, enchères et personnages remis à leurs objets d'origine "
                       f"({n} octet(s))")
        return n

    def status(self) -> str:
        return f"lieux « donnée » (courrier, boutique, enchères) : {self.etat or 'pas encore essayé'}"
