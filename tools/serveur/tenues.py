"""Tenues à pouvoir (Triforce Heroes), partie A de docs/etudes/autres_zelda.md §6 : logique pure du serveur, testable
sans jeu (tests/test_tenues.py).

Règle de la PARTIE « Tenues à pouvoir », ALLUMÉE par défaut (``--no-tenues``, case du lobby, ``regles_cmd`` à chaud).
Le SERVEUR décide la tenue EFFECTIVE de chaque joueur et l'envoie à son client (``tenue``) à chaque changement :

* sa couleur (attribuée au hello, unique : commun/couleurs.attribuer) et ses pouvoirs (commun/tenues.POUVOIRS) ;
* un Hanteur n'a pas de tenue (aucun pouvoir) ; un Link possédé garde la sienne ; le robot n'a aucun pouvoir ;
* duos orange + violet, sombre + turquoise : quand les DEUX membres sont en jeu (joueurs, ni Hanteur ni robot, état
  reçu depuis moins de 5 s, pas en changement d'étape) et PROCHES (même étape, même salle, à moins de ``distance``,
  20 m par défaut, ``--tenues-distance``), chacun a les pouvoirs des deux ; le partage est coupé seulement après
  ``hysteresis`` (2 s) sans ces conditions (un pas de trop ou un fondu ne le fait pas clignoter) ;
* message : ``{type: "tenue", on, couleur, duo, partage, pouvoirs, f (facteurs du mod), cfg}`` ; le client écrit la
  zone WWMPTEN1 (mod natif 0.46.0) ; il ne décide rien.
* coup d'un joueur relayé à l'autorité (« hit ») : ``champ_hit`` ajoute ses facteurs d'épée, de gadgets et de dos
  (``ten``), que l'autorité écrit avant le CALL 14 : un coup distant compte avec la tenue de celui qui frappe.

ESSAI « tenues à pouvoir » (``--essai-tenues``, Réglages › Essais du lanceur ; à retirer après validation, tout ce qui
sert à l'essai est marqué ESSAI) : un client d'essai peut changer SA couleur de pouvoir à chaud (``essai_couleur`` ; la
couleur de la partie n'est pas changée : seulement sa tenue d'essai), et annonce un faux coéquipier (``essai_faux`` :
position, zone) de l'autre couleur du duo, traité ici comme un vrai partenaire (même règle, même hystérésis).
Aucune entrée/sortie : testé hors ligne.
"""
from __future__ import annotations

import time

from commun import couleurs
from commun import tenues as ten

VERSION = 1
ID_ESSAI = 93                # ESSAI : faux coéquipier (pas ceux des essais porter 98, porté 97, coups 96, ralliement
                             # 95, Fragments 94)
NOM_ESSAI = "Essai Duo"
FAUX_AGE = 4.0               # s : position du faux coéquipier plus vieille : il n'est plus là


class TenuesServeur:
    """``info(pid)`` (fourni par serveur/principal.py) rend {couleur, joueur, hanteur, robot, zone, pos, t, feat,
    leaving} ou None ; ``tick`` rend les messages à envoyer [(pid, msg)]."""

    def __init__(self, on: bool = True, essai: bool = False, distance: float = ten.DISTANCE,
                 hysteresis: float = ten.HYSTERESIS, clock=time.monotonic):
        self.on = bool(on)
        self.vu = False                       # changée à chaud au moins une fois (« regles » l'annonce alors)
        self.essai = bool(essai)
        self.distance = ten.distance_ok(distance)
        self.hysteresis = max(0.0, float(hysteresis))
        self.clock = clock
        self.partage: dict[int, bool] = {}    # pid -> partage actif
        self.perdu: dict[int, float] = {}     # pid -> instant où les conditions ont été perdues (partage encore actif)
        self.envoye: dict[int, dict] = {}     # pid -> dernier message envoyé
        self.essai_couleur: dict[int, int] = {}   # ESSAI : pid -> couleur de pouvoir choisie
        self.essai_faux: dict[int, dict] = {}     # ESSAI : pid -> {pos, zone, t}
        self.journal: list[str] = []
        self.n_partages = 0

    # -- règles --------------------------------------------------------------------------------------------------------
    def actif(self) -> bool:
        return self.on or self.essai

    def couleur_de(self, pid, i: dict) -> int:
        """Couleur de POUVOIR : la couleur de la partie ; ESSAI : celle choisie par le client d'essai."""
        if self.essai and pid in self.essai_couleur:
            return self.essai_couleur[pid]
        return couleurs.normaliser(i.get("couleur"))

    @staticmethod
    def a_une_tenue(i: dict | None) -> bool:
        """Joueur, ni Hanteur (même manifesté), ni robot."""
        return bool(i and i.get("joueur") and not i.get("hanteur") and not i.get("robot"))

    def en_jeu(self, i: dict | None, now: float) -> bool:
        if not self.a_une_tenue(i) or i.get("leaving"):
            return False
        t = i.get("t")
        if not isinstance(t, (int, float)) or now - t > ten.ETAT_AGE:
            return False
        return isinstance(i.get("zone"), str) and ten.pos3(i.get("pos")) is not None

    def _faux(self, pid, now: float) -> dict | None:
        """ESSAI : le faux coéquipier du client ``pid`` (position récente), ou None."""
        f = self.essai_faux.get(pid) if self.essai else None
        if not f or now - f.get("t", -1e9) > FAUX_AGE:
            return None
        return f

    # -- calcul --------------------------------------------------------------------------------------------------------
    def calculer(self, ids, info, now: float | None = None) -> dict[int, dict]:
        """Tenue effective de chaque joueur de ``ids`` : {pid: {couleur, duo, partage, pouvoirs}} (hystérésis
        appliquée, état mis à jour)."""
        now = self.clock() if now is None else now
        infos = {pid: info(pid) for pid in ids}
        par_couleur = {}
        for pid, i in infos.items():
            if self.a_une_tenue(i):
                c = self.couleur_de(pid, i)
                if c:
                    par_couleur.setdefault(c, pid)
        out = {}
        for pid, i in infos.items():
            if not self.actif() or not self.a_une_tenue(i):
                self.partage.pop(pid, None)
                self.perdu.pop(pid, None)
                out[pid] = {"couleur": 0, "duo": 0, "partage": False, "pouvoirs": ()}
                continue
            c = self.couleur_de(pid, i)
            d = ten.duo_de(c)
            cond = False
            if d and self.en_jeu(i, now):
                faux = self._faux(pid, now)
                if faux is not None:                  # ESSAI : le faux coéquipier a toujours la couleur du duo
                    cond = ten.proches({"zone": i.get("zone"), "pos": i.get("pos")}, faux, self.distance)
                else:
                    q = par_couleur.get(d)
                    if q is not None and q != pid and self.en_jeu(infos.get(q), now):
                        cond = ten.proches(i, infos[q], self.distance)
            avant = self.partage.get(pid, False)
            if cond:
                self.perdu.pop(pid, None)
                partage = True
                if not avant:
                    self.n_partages += 1
            elif avant:
                debut = self.perdu.setdefault(pid, now)
                partage = now - debut < self.hysteresis
                if not partage:
                    self.perdu.pop(pid, None)
            else:
                partage = False
            self.partage[pid] = partage
            out[pid] = {"couleur": c, "duo": d if partage else 0, "partage": partage,
                        "pouvoirs": ten.effectifs(c, partage)}
        for pid in list(self.partage):
            if pid not in infos:
                self.oublier(pid)
        return out

    def message(self, pid, e: dict) -> dict:
        f = ten.facteurs(e["pouvoirs"])
        m = {"type": "tenue", "on": int(self.actif()), "couleur": e["couleur"], "duo": e["duo"],
             "partage": int(e["partage"]), "pouvoirs": list(e["pouvoirs"]), "f": f,
             "cfg": ten.cfg(e["pouvoirs"], self.actif())}
        if self.essai:
            m["essai"] = 1                         # ESSAI : le client peut changer sa couleur et poser un faux duo
        return m

    def tick(self, ids, info, now: float | None = None) -> list[tuple[int, dict]]:
        """Messages « tenue » des joueurs dont la tenue effective a changé (et du nouveau venu)."""
        envois = []
        for pid, e in self.calculer(ids, info, now).items():
            m = self.message(pid, e)
            if self.envoye.get(pid) != m:
                avant = self.envoye.get(pid)
                self.envoye[pid] = m
                envois.append((pid, m))
                if avant is not None and avant.get("partage") != m["partage"]:
                    i = info(pid) or {}
                    self.journal.append(f"tenues : {i.get('nom', pid)} {'partage' if m['partage'] else 'ne partage plus'}"
                                        f" les pouvoirs de son duo ({ten.code_couleur(m['couleur'])})")
        return envois

    def oublier(self, pid) -> None:
        for d in (self.partage, self.perdu, self.envoye, self.essai_couleur, self.essai_faux):
            d.pop(pid, None)

    def reemettre(self) -> None:
        """Après un changement de règle : tout est renvoyé au tick suivant (règle coupée comprise : « plus rien »)."""
        self.envoye = {k: None for k in self.envoye}

    # -- coups relayés ---------------------------------------------------------------------------------------------------
    def champ_hit(self, pid) -> dict:
        """``{"ten": [épée, gadgets, dos]}`` de l'attaquant ``pid`` (dernière tenue envoyée), {} s'il n'y a rien à
        changer (règle coupée, aucun pouvoir de dégâts, ancien client)."""
        m = self.envoye.get(pid)
        if not self.actif() or not m or not m.get("cfg"):
            return {}
        v = ten.facteurs_hit(m.get("f"))
        return {"ten": v} if v else {}

    # -- ESSAI -----------------------------------------------------------------------------------------------------------
    def essai_message(self, pid, msg: dict, info, now: float | None = None) -> str:
        """ESSAI (``--essai-tenues`` seulement) : ``essai_couleur`` (couleur de pouvoir de ce client) ou ``essai_faux``
        (faux coéquipier : pos, zone ; ``loin`` : il s'est éloigné). Rend la raison d'un refus ("" : fait)."""
        if not self.essai:
            return "pas d'essai sur ce serveur"
        now = self.clock() if now is None else now
        i = info(pid)
        if not self.a_une_tenue(i):
            return "pas de tenue (Hanteur, robot ou outil)"
        ev = msg.get("ev")
        if ev == "essai_couleur":
            c = couleurs.normaliser(msg.get("couleur"))
            if not c:
                return "couleur inconnue"
            self.essai_couleur[pid] = c
            self.journal.append(f"tenues : ESSAI, {i.get('nom', pid)} essaie la tenue {ten.code_couleur(c)}")
            return ""
        if ev == "essai_faux":
            p = ten.pos3(msg.get("pos"))
            z = msg.get("zone")
            if p is None or not isinstance(z, str) or len(z) > 40:
                return "faux coéquipier mal placé"
            self.essai_faux[pid] = {"pos": list(p), "zone": z, "t": now}
            return ""
        return "action inconnue"
