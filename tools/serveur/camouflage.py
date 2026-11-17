"""Camouflage de la tenue jaune : logique pure du serveur, testable sans jeu (tests/test_camouflage.py).

Le SERVEUR décide qui est camouflé (commun/camouflage.py) :

* un joueur (ni Hanteur, ni robot) dont la couleur de POUVOIR est jaune (celle des tenues à pouvoir, ESSAI des
  tenues compris) et qui ENTRE dans une zone (« étape:salle » différente de la précédente, première zone comprise) est
  camouflé ``duree`` (4 s, ``--camouflage-duree``) : message ``{type: "camouflage", ev: "debut", n, duree}`` à lui
  seul (son client arme le mod natif 0.49.0, zone WWMPCAM1) ;
* fin : son client la dit (``ev: "fin"``, raison « expire », « revele » : il a frappé un monstre chez lui) ; un coup
  qu'il porte à une marionnette (« hit » relayé à l'autorité) la finit ici (``coup``) et le lui dit ; autre zone : un
  nouveau camouflage ; plus jaune, Hanteur, parti, règle coupée : fini ; sans nouvelles, ``duree + MARGE`` ;
* tous les joueurs reçoivent la liste des camouflés à chaque changement (``{type: "camoufles", ids}``) : leurs
  fantômes sont transparents chez les Link, absents chez les Hanteurs (ni fantôme ni spectateur) ; chez l'autorité,
  aucun monstre ne vise leur fantôme.

ESSAI « camouflage jaune » (``--essai-camouflage``, Réglages › Essais du lanceur ; à retirer après validation, tout ce
qui sert à l'essai est marqué ESSAI) : un client d'essai est traité en jaune quelle que soit sa couleur
(``essai_jaune``) et peut demander un camouflage tout de suite (``essai_debut``).
"""
from __future__ import annotations

import time

from commun import camouflage as cm

VERSION = 1
_ABSENT = object()


class CamouflageServeur:
    """``info(pid)`` (fourni par serveur/principal.py) rend {nom, joueur, robot, hanteur, zone, couleur, leaving} ou
    None ; ``tick`` et les autres rendent les envois [(pid ou "tous", msg)]."""

    def __init__(self, on: bool = True, essai: bool = False, duree: float = cm.DUREE, clock=time.monotonic):
        self.on = bool(on)
        self.vu = False                        # changée à chaud au moins une fois (« regles » l'annonce alors)
        self.essai = bool(essai)
        self.duree = cm.duree_ok(duree)
        self.clock = clock
        self.zones: dict[int, object] = {}     # pid -> dernière zone vue
        self.actifs: dict[int, dict] = {}      # pid -> {n, debut, zone}
        self.numero = 0
        self.essai_jaune: set[int] = set()     # ESSAI : clients traités en jaune
        self.diffuse: tuple = ()               # dernière liste envoyée à tous (vide au départ : le welcome la donne)
        self.journal: list[str] = []
        self.n_debuts = self.n_coups = 0

    # -- règle ---------------------------------------------------------------------------------------------------------
    def actif(self, tenues: bool = True) -> bool:
        """La règle joue : allumée et tenues à pouvoir actives ; ESSAI : toujours."""
        return (self.on and bool(tenues)) or self.essai

    @staticmethod
    def joueur_ok(i: dict | None) -> bool:
        return bool(i and i.get("joueur") and not i.get("robot") and not i.get("hanteur") and not i.get("leaving"))

    def jaune(self, pid, i: dict) -> bool:
        return (self.essai and pid in self.essai_jaune) or cm.jaune(i.get("couleur"))

    def liste(self) -> list[int]:
        return sorted(self.actifs)

    # -- début, fin ----------------------------------------------------------------------------------------------------
    def _debut(self, pid, i: dict, now: float, raison: str = "zone") -> list:
        self.numero += 1
        self.actifs[pid] = {"n": self.numero, "debut": now, "zone": i.get("zone")}
        self.n_debuts += 1
        self.journal.append(f"camouflage : {i.get('nom', pid)} camouflé {self.duree:g} s ({raison}, n° {self.numero})")
        return [(pid, {"type": "camouflage", "ev": "debut", "n": self.numero, "duree": self.duree,
                       **({"essai": 1} if self.essai and pid in self.essai_jaune else {})})]

    def _fin(self, pid, raison: str, dire: bool = True, nom=None) -> list:
        a = self.actifs.pop(pid, None)
        if a is None:
            return []
        self.journal.append(f"camouflage : {nom or pid} n'est plus camouflé ({cm.RAISONS.get(raison, raison)})")
        return [(pid, {"type": "camouflage", "ev": "fin", "n": a["n"], "raison": raison})] if dire else []

    def _diffusion(self) -> list:
        l = tuple(self.liste())
        if l == self.diffuse:
            return []
        self.diffuse = l
        return [("tous", {"type": "camoufles", "ids": list(l)})]

    # -- tick ----------------------------------------------------------------------------------------------------------
    def tick(self, ids, info, tenues: bool = True, now: float | None = None) -> list:
        """Entrées de zone des joueurs ``ids``, fins (couleur, Hanteur, délai de secours) ; liste diffusée si elle a
        changé."""
        now = self.clock() if now is None else now
        envois: list = []
        regle = self.actif(tenues)
        presents = set()
        for pid in ids:
            i = info(pid)
            presents.add(pid)
            if not self.joueur_ok(i):
                envois += self._fin(pid, "hanteur" if i and i.get("hanteur") else "parti",
                                    dire=bool(i and i.get("hanteur")), nom=(i or {}).get("nom"))
                if i is None or i.get("leaving"):
                    self.zones.pop(pid, None)
                continue
            z = i.get("zone")
            avant = self.zones.get(pid, _ABSENT)
            self.zones[pid] = z
            if not regle:
                envois += self._fin(pid, "coupe", nom=i.get("nom"))
                continue
            entree = isinstance(z, str) and z != avant
            if pid in self.actifs and not self.jaune(pid, i):
                envois += self._fin(pid, "couleur", nom=i.get("nom"))
            elif entree and self.jaune(pid, i):
                envois += self._debut(pid, i, now)
            elif pid in self.actifs and now - self.actifs[pid]["debut"] > self.duree + cm.MARGE:
                envois += self._fin(pid, "delai", nom=i.get("nom"))
        for pid in list(self.actifs):
            if pid not in presents:
                self._fin(pid, "parti", dire=False)
        for pid in list(self.zones):
            if pid not in presents:
                self.zones.pop(pid, None)
        return envois + self._diffusion()

    def couper(self) -> list:
        """Règle coupée : tout camouflage fini, dit à chacun ; liste vide diffusée."""
        envois = []
        for pid in list(self.actifs):
            envois += self._fin(pid, "coupe")
        return envois + self._diffusion()

    # -- messages des clients, coups -----------------------------------------------------------------------------------
    def message(self, pid, msg: dict, info, tenues: bool = True, now: float | None = None) -> list:
        """``camouflage`` d'un client : ``fin`` (n, raison) ; ESSAI : ``essai_jaune``, ``essai_debut``."""
        now = self.clock() if now is None else now
        ev = msg.get("ev")
        i = info(pid)
        if ev == "fin":
            a = self.actifs.get(pid)
            if a is not None and msg.get("n") == a["n"]:
                r = msg.get("raison")
                envois = self._fin(pid, r if r in cm.RAISONS else "expire", dire=False, nom=(i or {}).get("nom"))
                return envois + self._diffusion()
            return []
        if not self.essai or msg.get("essai") not in (1, True):
            return []
        if not self.joueur_ok(i):
            return [(pid, {"type": "camouflage", "ev": "refus", "raison": "pas un Link (Hanteur, robot ou outil)"})]
        if ev == "essai_jaune":
            if pid not in self.essai_jaune:
                self.essai_jaune.add(pid)
                self.journal.append(f"camouflage : ESSAI, {i.get('nom', pid)} est traité en jaune")
            return []
        if ev == "essai_debut":
            self.essai_jaune.add(pid)
            return self._debut(pid, i, now, raison="essai") + self._diffusion()
        return []

    def coup(self, pid, nom=None) -> list:
        """Un coup porté par ``pid`` à une marionnette (« hit » relayé) : révélé."""
        if pid not in self.actifs:
            return []
        self.n_coups += 1
        return self._fin(pid, "coup", nom=nom) + self._diffusion()

    def oublier(self, pid) -> list:
        """Joueur parti : plus camouflé (rien à lui dire), liste diffusée."""
        self.actifs.pop(pid, None)
        self.zones.pop(pid, None)
        self.essai_jaune.discard(pid)
        return self._diffusion()
