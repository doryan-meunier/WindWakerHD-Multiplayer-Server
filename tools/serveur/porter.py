"""Porter un coéquipier, lot P3 « réseau » : le serveur (docs/etudes/porter_coequipier.md §5, commun/porter.py).

Le serveur arbitre et relaie, rien d'autre : premier ``prise`` reçu gagne ; il prévient le porté (``porte``), relaie o,
et défait le port des deux côtés dès qu'un bout le lâche (pose, lancer, descente, départ, salle, règle coupée).

Refusé (``refus`` au porteur, dont le client fait lâcher le fantôme) : règle coupée, mode Hanteurs, robot, l'un sans la
fonction ``porter``, pas la même salle, trop loin, cible en cinématique ou pas « portable », cible déjà portée ou
porteuse, porteur déjà porté ou porteur d'un autre. Deux joueurs qui se soulèvent en même temps : le premier ``prise``
gagne, l'autre est refusé (son jeu lâche).
"""
from __future__ import annotations

import time

from commun import porter as pc


class PorterServeur:
    def __init__(self, on: bool = True, clock=time.monotonic):
        self.on = bool(on)
        self.vu = False                      # règle changée au moins une fois (envoyée dans « regles »)
        self.hanteurs = False
        self.clock = clock
        self.ports: dict[int, dict] = {}     # porteur -> {"porte", "o", "zone", "t"}
        self.journal: list[str] = []
        self.n_ports = 0

    def actif(self) -> bool:
        return self.on and not self.hanteurs

    def porteur_de(self, pid) -> int | None:
        for a, p in self.ports.items():
            if p["porte"] == pid:
                return a
        return None

    def vue(self) -> dict:
        return {a: dict(p) for a, p in self.ports.items()}

    # -- messages des clients ----------------------------------------------------------------------------------------
    def message(self, pid: int, msg: dict, joueur, now: float | None = None) -> list:
        """``porter`` d'un client ; ``joueur(pid)`` -> dict (nom, joueur, robot, hanteur, zone, busy, prtbl, pos, t,
        feat) ou None. Rend les envois [(pid, message)]."""
        now = self.clock() if now is None else now
        ev = msg.get("ev")
        if ev == "prise":
            return self._prise(pid, msg.get("cible"), msg.get("o"), joueur, now)
        if ev == "o":
            p = self.ports.get(pid)
            o = pc.o_valide(msg.get("o"))
            if p is None or o is None:
                return []
            p["o"] = o
            return [(p["porte"], {"type": "porter", "ev": "o", "par": pid, "o": o})]
        if ev == "fin":                      # le porteur ne tient plus le fantôme
            p = self.ports.pop(pid, None)
            if p is None:
                return []
            r = pc.raison_txt(msg.get("raison")) or "posé"
            self._noter(joueur, pid, p["porte"], f"fin ({r})")
            return [(p["porte"], {"type": "porter", "ev": "fin", "par": pid, "raison": r})]
        if ev == "descend":                  # le porté a été libéré par son mod (L3, dégâts, grappin…)
            a = self.porteur_de(pid)
            if a is None:
                return []
            self.ports.pop(a, None)
            r = pc.raison_txt(msg.get("raison")) or "descendu"
            self._noter(joueur, a, pid, f"descendu ({r})")
            return [(a, {"type": "porter", "ev": "lacher", "cible": pid, "raison": r})]
        return []

    def _refus(self, pid, cible, joueur, now) -> str:
        if not self.on:
            return "le port de coéquipier est coupé dans cette partie"
        if self.hanteurs:
            return "pas de port de coéquipier en mode Hanteurs"
        if not pc.pid_ok(cible) or cible == pid:
            return "cible inconnue"
        a, c = joueur(pid), joueur(cible)
        if a is None or c is None:
            return "joueur inconnu"
        if not a.get("joueur") or not c.get("joueur") or a.get("robot") or c.get("robot"):
            return "seulement entre deux joueurs"
        if a.get("hanteur") or c.get("hanteur"):
            return "pas avec un Hanteur"
        if pc.FEAT not in (a.get("feat") or ()) or pc.FEAT not in (c.get("feat") or ()):
            return f"{c.get('nom', '?')} ne peut pas être porté (client ou mod trop ancien)"
        if a.get("zone") is None or a.get("zone") != c.get("zone"):
            return f"{c.get('nom', '?')} n'est pas dans ta salle"
        if c.get("busy"):
            return f"{c.get('nom', '?')} est en cinématique"
        if not c.get("prtbl"):
            return f"{c.get('nom', '?')} ne peut pas être porté maintenant (bateau, nage, échelle, menu…)"
        d = None
        if now - (a.get("t") or float("-inf")) <= pc.ETAT_AGE and now - (c.get("t") or float("-inf")) <= pc.ETAT_AGE:
            d = pc.dist_xz(a.get("pos"), c.get("pos"))
        if d is not None and d > pc.PORTEE:
            return f"{c.get('nom', '?')} est trop loin"
        if self.porteur_de(cible) is not None:
            return f"{c.get('nom', '?')} est déjà porté"
        if cible in self.ports:
            return f"{c.get('nom', '?')} porte déjà quelqu'un"
        if self.porteur_de(pid) is not None:
            return "tu es toi-même porté"
        if pid in self.ports:
            return "tu portes déjà quelqu'un"
        return ""

    def _prise(self, pid, cible, o, joueur, now) -> list:
        raison = self._refus(pid, cible, joueur, now)
        if raison:
            self.journal.append(f"porter : prise de {self._nom(joueur, pid)} refusée ({raison})")
            return [(pid, {"type": "porter", "ev": "refus", "cible": cible if pc.pid_ok(cible) else None,
                           "raison": raison})]
        o = pc.o_valide(o) or list(pc.O_DEFAUT)
        self.ports[pid] = {"porte": cible, "o": o, "zone": joueur(pid).get("zone"), "t": now}
        self.n_ports += 1
        self._noter(joueur, pid, cible, "soulève")
        return [(cible, {"type": "porter", "ev": "porte", "par": pid, "o": o}),
                (pid, {"type": "porter", "ev": "ok", "cible": cible})]

    # -- surveillance ------------------------------------------------------------------------------------------------
    def tick(self, joueur, now: float | None = None) -> list:
        """Défait les ports dont un bout est parti, a changé de salle, est devenu Hanteur ou robot, ou si la règle est
        coupée."""
        out = []
        for a, p in list(self.ports.items()):
            ia, ic = joueur(a), joueur(p["porte"])
            r = ""
            if not self.actif():
                r = "règle coupée"
            elif ia is None:
                r = "porteur parti"
            elif ic is None:
                r = "porté parti"
            elif ia.get("hanteur") or ic.get("hanteur"):
                r = "Hanteur"
            elif ia.get("zone") != p["zone"] or ic.get("zone") != p["zone"]:
                r = "salle changée"
            if r:
                out += self._defaire(a, r, joueur)
        return out

    def oublier(self, pid, joueur) -> list:
        """Joueur parti : ses ports (porteur ou porté) sont défaits."""
        out = []
        if pid in self.ports:
            out += self._defaire(pid, "porteur parti", joueur)
        a = self.porteur_de(pid)
        if a is not None:
            out += self._defaire(a, "porté parti", joueur)
        return out

    def couper(self, joueur) -> list:
        out = []
        for a in list(self.ports):
            out += self._defaire(a, "règle coupée", joueur)
        return out

    def _defaire(self, a, raison, joueur) -> list:
        p = self.ports.pop(a, None)
        if p is None:
            return []
        self._noter(joueur, a, p["porte"], f"fin ({raison})")
        return [(p["porte"], {"type": "porter", "ev": "fin", "par": a, "raison": raison}),
                (a, {"type": "porter", "ev": "lacher", "cible": p["porte"], "raison": raison})]

    # -- journal -----------------------------------------------------------------------------------------------------
    @staticmethod
    def _nom(joueur, pid) -> str:
        i = joueur(pid) if callable(joueur) else None
        return (i or {}).get("nom") or str(pid)

    def _noter(self, joueur, a, c, quoi) -> None:
        self.journal.append(f"porter : {self._nom(joueur, a)} {quoi} {self._nom(joueur, c)}"
                            if quoi == "soulève" else
                            f"porter : {self._nom(joueur, a)} / {self._nom(joueur, c)} : {quoi}")
