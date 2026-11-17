"""Relever un coéquipier (Four Swords Adventures, Spirit Tracks), lot R0 de docs/etudes/autres_zelda.md §5 : le serveur.

Règle de la PARTIE « Relever un coéquipier » (``--no-releve``, réglage de l'hôte, ``regles_cmd {releve}``), ALLUMÉE par
défaut ; elle ne JOUE qu'avec le deathlink, jamais avec la vie partagée, jamais en mode Hanteurs (commun/releve.effective).

Messages du client (``releve``) :

* ``{e: "terre", pos}`` : son Link est à terre (mod natif 0.48.0 : vie à 0, gelé) ; le serveur note l'heure, prévient
  les autres (``releve_info {id, nom, e: "terre", reste}``) et, au bout de ``DELAI`` s sans relève, lui envoie
  ``releve_ordre {e: "abandon"}`` (CALL 19 ABANDON : sa vraie mort, puis la vague du deathlink comme toujours) ;
* ``{e: "debout"}`` : relevé (ou vie rendue autrement) ; ``{e: "mort"}`` : vraie mort ; ``{e: "vivant"}`` : en vie après
  un Game Over (il peut de nouveau relever) ;
* ``{cmd: "relever", to}`` : la commande « Relever » d'un coéquipier à moins de ``RAYON`` u : relevé tout de suite.

Relève : un releveur (joueur de jeu, pas le robot, pas un Hanteur, pas à terre ni mort, client ``FEAT``) dans la même
salle, position récente (``ETAT_AGE``), à moins de ``RAYON`` u (1,5 m) du Link à terre pendant ``TENIR`` s (2 s) ->
``releve_ordre {e: "relever", vie, par}`` au Link à terre (CALL 19 RELEVER, 2 cœurs) et ``releve_info {e: "fait"}`` au
releveur. Pendant qu'il tient : ``releve_progres {id, nom, t, tenir}`` au releveur (bandeau « Reste près de … »).
« Relève possible » : ``releve_possible {v}`` à chaque joueur quand la réponse change : un autre Link vivant dans la
même ÉTAPE (sinon la prochaine chute est tout de suite une vraie mort).

ESSAI « relever un coéquipier » (``--essai-relever``, Réglages › Essais ; à retirer après validation, tout ce qui sert à
l'essai est marqué ESSAI) : le faux coéquipier ``ID_ESSAI`` (« Essai Secours ») d'un client d'essai compte comme un Link
vivant dans son étape ; (1) mode « secours » (défaut) : il vient relever ce client ``ESSAI_SECOURS`` s après sa chute ;
mode « personne » : il ne vient pas (les 20 s expirent : vraie mort) ; (2) ``{essai: 1, faux: "tombe", pos}`` : le
faux coéquipier est à terre à ``pos`` ; le client le relève en restant 2 s à côté (ou par la commande) ; à 20 s, il
« meurt » (rien dans le jeu). Rien de l'essai ne touche un autre joueur.
Aucune entrée/sortie : testé hors ligne (tests/test_releve.py).
"""
from __future__ import annotations

import time

from commun.releve import (DELAI, ESSAI_SECOURS, ETAT_AGE, FEAT, ID_ESSAI, NOM_ESSAI, RAYON, TENIR, VIE,  # noqa: F401
                           distance, effective, est_id, etape, pos3)

RENVOI = 1.0                # s entre deux renvois d'un ordre non suivi (abandon, relever)
SEUL = 2.0                  # s sans aucun autre Link vivant dans l'étape : sa vraie mort tout de suite
RENVOIS_MAX = 10
PROGRES_PAS = 0.2           # s entre deux « releve_progres » au même releveur


class ReleveServeur:
    """``joueur(pid)`` : dict {nom, joueur, zone, pos, t, feat, hanteur} ou None ; ``ids()`` : joueurs connectés."""

    def __init__(self, on: bool = True, essai: bool = False, clock=time.monotonic):
        self.on = bool(on)
        self.essai = bool(essai)
        self.vu = False                     # changée à chaud une fois : « regles » l'annonce (welcome sinon)
        self.clock = clock
        self.terre: dict[int, dict] = {}    # pid -> {t0, nom, pos, zone, prog: {releveur: t}, ordre, t_ordre, n}
        self.morts: set[int] = set()
        self.possibles: dict[int, bool] = {}
        self.essai_mode: dict[int, str] = {}    # ESSAI : client -> « secours » / « personne »
        self.faux: dict[int, dict] = {}     # ESSAI : client -> faux coéquipier à terre {t0, pos, prog}
        self.t_progres: dict[tuple, float] = {}
        self.journal: list[str] = []
        self.n_terre = self.n_releves = self.n_expires = self.n_commandes = 0

    # -- règle ---------------------------------------------------------------------------------------------------
    def actif(self, deathlink: bool, vie_partagee: bool, hanteurs: bool) -> bool:
        return effective(self.on, deathlink, vie_partagee, hanteurs)

    def oublier(self, pid: int) -> list[tuple[int, dict]]:
        """Départ d'un joueur : plus à terre, plus releveur ; ESSAI oublié."""
        envois = []
        if self.terre.pop(pid, None) is not None:
            envois.append(("tous", {"type": "releve_info", "id": pid, "e": "parti"}))
        self.morts.discard(pid)
        self.possibles.pop(pid, None)
        self.essai_mode.pop(pid, None)
        self.faux.pop(pid, None)
        for t in self.terre.values():
            t["prog"].pop(pid, None)
        return envois

    def couper(self) -> list[tuple[int, dict]]:
        """Règle coupée (ou deathlink coupé, vie partagée, Hanteurs) : les Link à terre meurent (le mod les tue de lui-même
        quand le client coupe cfg) ; plus rien de suivi."""
        envois = [(pid, {"type": "releve_ordre", "e": "abandon", "raison": "règle coupée"}) for pid in self.terre]
        self.terre.clear()
        self.faux.clear()
        self.possibles.clear()
        return envois

    # -- qui peut relever --------------------------------------------------------------------------------------------
    @staticmethod
    def _releveur_ok(j: dict | None) -> bool:
        return bool(j) and bool(j.get("joueur")) and not j.get("hanteur") and FEAT in (j.get("feat") or ())

    def _vivant(self, pid: int, j: dict | None) -> bool:
        return self._releveur_ok(j) and pid not in self.terre and pid not in self.morts

    def possible(self, pid: int, joueur, ids) -> bool:
        """Un autre Link vivant dans la même étape que ``pid`` (ESSAI : le faux coéquipier compte)."""
        j = joueur(pid)
        if j is None:
            return False
        if self.essai and pid in self.essai_mode:
            return True
        st = etape(j.get("zone"))
        if not st:
            return False
        for i in ids():
            if i == pid:
                continue
            o = joueur(i)
            if self._vivant(i, o) and etape(o.get("zone")) == st:
                return True
        return False

    # -- messages ----------------------------------------------------------------------------------------------------
    def message(self, pid: int, msg: dict, joueur, now: float | None = None, interne: bool = False) -> list[tuple]:
        """``releve`` d'un client. Renvoie [(destinataire, message)] ; destinataire ``"tous"`` : tous sauf ``pid``."""
        now = self.clock() if now is None else now
        if not isinstance(msg, dict):
            return []
        j = joueur(pid)
        if j is None or not j.get("joueur"):
            return []
        nom = str(j.get("nom") or pid)[:24]
        envois: list[tuple] = []
        if self.essai and msg.get("essai") in (1, True) and not interne:
            return self._essai(pid, msg, j, now)
        e = msg.get("e")
        if e == "terre":
            if pid in self.terre:
                return []
            p = pos3(msg.get("pos")) or pos3(j.get("pos"))
            self.terre[pid] = {"t0": now, "nom": nom, "pos": p, "zone": j.get("zone"), "prog": {}, "ordre": None,
                               "t_ordre": float("-inf"), "n": 0}
            self.morts.discard(pid)
            self.n_terre += 1
            self.journal.append(f"relever : {nom} est à terre ({int(DELAI)} s pour le relever)")
            envois.append(("tous", {"type": "releve_info", "id": pid, "nom": nom, "e": "terre", "reste": DELAI}))
            if self.essai and self.essai_mode.get(pid) == "secours":
                self.terre[pid]["essai_t"] = now + ESSAI_SECOURS        # ESSAI (1) : le faux coéquipier vient
                self.journal.append(f"relever (ESSAI) : « {NOM_ESSAI} » vient relever {nom} dans "
                                    f"{int(ESSAI_SECOURS)} s")
        elif e in ("debout", "mort"):
            t = self.terre.pop(pid, None)
            if e == "mort":
                self.morts.add(pid)
            if t is not None or e == "mort":
                self.journal.append(f"relever : {nom} " + ("est relevé" if e == "debout" else "est mort"))
                envois.append(("tous", {"type": "releve_info", "id": pid, "nom": nom, "e": e}))
        elif e == "vivant":
            self.morts.discard(pid)
        elif msg.get("cmd") == "relever":
            envois += self._commande(pid, j, msg.get("to"), joueur, now)
        return envois

    def _commande(self, src: int, j: dict, to, joueur, now: float) -> list[tuple]:
        """Commande « Relever » : le Link à terre le plus proche (``to`` s'il est donné) à moins de RAYON u."""
        if not self._vivant(src, j):
            return [(src, {"type": "releve_info", "e": "refus", "raison": "tu ne peux pas relever (à terre, mort, "
                                                                      "Hanteur ou client trop ancien)"})]
        cands = []
        for pid, t in self.terre.items():
            if pid == src or t.get("ordre") == "relever" or (est_id(to) and to != pid):
                continue
            o = joueur(pid)
            if o is None or o.get("zone") != j.get("zone"):
                continue
            d = distance(j.get("pos"), o.get("pos") or t["pos"])
            if d <= RAYON and now - (j.get("t") or float("-inf")) <= ETAT_AGE:
                cands.append((d, pid))
        if not cands:
            return [(src, {"type": "releve_info", "e": "refus", "raison": "personne à terre à moins de 1,5 m"})]
        self.n_commandes += 1
        return self._relever(min(cands)[1], src, j, now)

    def _relever(self, pid: int, src: int, js: dict, now: float) -> list[tuple]:
        t = self.terre[pid]
        t["ordre"], t["t_ordre"], t["n"], t["par"] = "relever", now, 1, src
        self.n_releves += 1
        par = str(js.get("nom") or src)[:24]
        self.journal.append(f"relever : {par} relève {t['nom']}")
        return [(pid, {"type": "releve_ordre", "e": "relever", "vie": VIE, "par": par}),
                (src, {"type": "releve_info", "id": pid, "nom": t["nom"], "e": "fait"})]

    # -- ESSAI ---------------------------------------------------------------------------------------------------------
    def _essai(self, pid: int, msg: dict, j: dict, now: float) -> list[tuple]:
        """ESSAI : mode du faux coéquipier, le faux coéquipier qui tombe, sa position ; puis le message normal."""
        envois: list[tuple] = []
        mode = msg.get("mode")
        if mode in ("secours", "personne"):
            self.essai_mode[pid] = mode
            self.journal.append(f"relever (ESSAI) : mode « {mode} » pour {j.get('nom')}")
        if msg.get("faux") == "tombe":
            p = pos3(msg.get("pos"))
            if p is not None and pid not in self.faux:
                self.faux[pid] = {"t0": now, "pos": p, "prog": None, "zone": j.get("zone")}
                self.journal.append(f"relever (ESSAI) : « {NOM_ESSAI} » est à terre près de {j.get('nom')}")
                envois.append((pid, {"type": "releve_info", "id": ID_ESSAI, "nom": NOM_ESSAI, "e": "terre",
                                     "reste": DELAI, "essai": 1}))
        p = pos3(msg.get("faux_pos"))
        if p is not None and pid in self.faux:
            self.faux[pid]["pos"] = p
        rest = {k: v for k, v in msg.items() if k not in ("essai", "mode", "faux", "faux_pos")}
        if rest.get("e") or rest.get("cmd"):
            if rest.get("cmd") == "relever" and pid in self.faux:
                f = self.faux[pid]
                if distance(j.get("pos"), f["pos"]) <= RAYON:
                    self.faux.pop(pid)
                    self.n_releves += 1
                    self.journal.append(f"relever (ESSAI) : {j.get('nom')} relève « {NOM_ESSAI} » (commande)")
                    return envois + [(pid, {"type": "releve_info", "id": ID_ESSAI, "nom": NOM_ESSAI, "e": "fait",
                                            "essai": 1})]
            envois += [(d, m) for d, m in self.message(pid, rest, lambda i: j if i == pid else None, now, True)
                       if d != "tous"]                  # ESSAI : rien vers les autres joueurs
        return envois

    # -- chaque tour (au plus 5 fois par seconde) ----------------------------------------------------------------------
    def tick(self, ids, joueur, now: float | None = None) -> list[tuple]:
        now = self.clock() if now is None else now
        envois: list[tuple] = []
        tous = list(ids())
        # relève possible : à chaque joueur, quand la réponse change
        for pid in tous:
            j = joueur(pid)
            if j is None or not j.get("joueur") or FEAT not in (j.get("feat") or ()):
                continue
            v = self.possible(pid, joueur, ids)
            if self.possibles.get(pid) != v:
                self.possibles[pid] = v
                envois.append((pid, {"type": "releve_possible", "v": int(v)}))
        for pid in list(self.terre):
            t = self.terre[pid]
            jt = joueur(pid)
            if jt is None:
                envois += self.oublier(pid)
                continue
            if t["ordre"] is not None:
                # ordre envoyé, pas encore suivi (le client réessaie CALL 19) : renvoyé chaque seconde, un peu
                if now - t["t_ordre"] >= RENVOI and t["n"] < RENVOIS_MAX:
                    t["t_ordre"], t["n"] = now, t["n"] + 1
                    m = {"type": "releve_ordre", "e": t["ordre"]}
                    if t["ordre"] == "relever":
                        m["vie"] = VIE
                    envois.append((pid, m))
                continue
            # ESSAI (1) : le faux coéquipier relève ce client
            if t.get("essai_t") is not None and now >= t["essai_t"]:
                t["ordre"], t["t_ordre"], t["n"] = "relever", now, 1
                self.n_releves += 1
                self.journal.append(f"relever (ESSAI) : « {NOM_ESSAI} » relève {t['nom']}")
                envois.append((pid, {"type": "releve_ordre", "e": "relever", "vie": VIE, "par": NOM_ESSAI,
                                     "essai": 1}))
                continue
            if now - t["t0"] >= DELAI:
                t["ordre"], t["t_ordre"], t["n"] = "abandon", now, 1
                self.n_expires += 1
                self.journal.append(f"relever : personne n'a relevé {t['nom']} à temps ({int(DELAI)} s) : sa vraie mort")
                envois.append((pid, {"type": "releve_ordre", "e": "abandon", "raison": "délai écoulé"}))
                continue
            # plus personne en vie dans son étape (tous partis, morts ou à terre) : sa vraie mort tout de suite
            if self.possible(pid, joueur, ids):
                t.pop("seul", None)
            elif now - t.setdefault("seul", now) >= SEUL:
                t["ordre"], t["t_ordre"], t["n"] = "abandon", now, 1
                self.n_expires += 1
                self.journal.append(f"relever : plus personne pour relever {t['nom']} dans l'étape : sa vraie mort")
                envois.append((pid, {"type": "releve_ordre", "e": "abandon", "raison": "personne dans l'étape"}))
                continue
            # releveurs près de lui
            pt = jt.get("pos") or t["pos"]
            for r in tous:
                if r == pid:
                    continue
                jr = joueur(r)
                proche = (self._vivant(r, jr) and jr.get("zone") == jt.get("zone")
                          and now - (jr.get("t") or float("-inf")) <= ETAT_AGE
                          and distance(jr.get("pos"), pt) <= RAYON)
                if not proche:
                    if t["prog"].pop(r, None) is not None:
                        envois.append((r, {"type": "releve_progres", "id": pid, "nom": t["nom"], "t": 0.0,
                                           "tenir": TENIR, "fin": 1}))
                    continue
                debut = t["prog"].setdefault(r, now)
                if now - debut >= TENIR:
                    envois += self._relever(pid, r, jr, now)
                    break
                k = (r, pid)
                if now - self.t_progres.get(k, float("-inf")) >= PROGRES_PAS:
                    self.t_progres[k] = now
                    envois.append((r, {"type": "releve_progres", "id": pid, "nom": t["nom"],
                                       "t": round(now - debut, 2), "tenir": TENIR,
                                       "reste": round(max(0.0, DELAI - (now - t["t0"])), 1)}))
        # ESSAI (2) : le faux coéquipier à terre, relevé par le client qui reste près de lui
        for pid in list(self.faux):
            f = self.faux[pid]
            j = joueur(pid)
            if j is None:
                self.faux.pop(pid)
                continue
            if now - f["t0"] >= DELAI:
                self.faux.pop(pid)
                self.n_expires += 1
                self.journal.append(f"relever (ESSAI) : « {NOM_ESSAI} » n'a pas été relevé à temps")
                envois.append((pid, {"type": "releve_info", "id": ID_ESSAI, "nom": NOM_ESSAI, "e": "mort",
                                     "essai": 1}))
                continue
            proche = (now - (j.get("t") or float("-inf")) <= ETAT_AGE and pid not in self.terre
                      and distance(j.get("pos"), f["pos"]) <= RAYON)
            if not proche:
                if f["prog"] is not None:
                    f["prog"] = None
                    envois.append((pid, {"type": "releve_progres", "id": ID_ESSAI, "nom": NOM_ESSAI, "t": 0.0,
                                         "tenir": TENIR, "fin": 1, "essai": 1}))
                continue
            if f["prog"] is None:
                f["prog"] = now
            if now - f["prog"] >= TENIR:
                self.faux.pop(pid)
                self.n_releves += 1
                self.journal.append(f"relever (ESSAI) : {j.get('nom')} relève « {NOM_ESSAI} »")
                envois.append((pid, {"type": "releve_info", "id": ID_ESSAI, "nom": NOM_ESSAI, "e": "fait", "essai": 1}))
                continue
            k = (pid, ID_ESSAI)
            if now - self.t_progres.get(k, float("-inf")) >= PROGRES_PAS:
                self.t_progres[k] = now
                envois.append((pid, {"type": "releve_progres", "id": ID_ESSAI, "nom": NOM_ESSAI,
                                     "t": round(now - f["prog"], 2), "tenir": TENIR, "essai": 1,
                                     "reste": round(max(0.0, DELAI - (now - f["t0"])), 1)}))
        return envois

    def a_terre(self) -> list[dict]:
        """Pour un nouveau venu : les Link à terre et leur temps restant."""
        now = self.clock()
        return [{"id": pid, "nom": t["nom"], "reste": round(max(0.0, DELAI - (now - t["t0"])), 1)}
                for pid, t in self.terre.items() if t["ordre"] is None]

    def vue(self) -> dict:
        return {"on": self.on, "essai": self.essai, "terre": len(self.terre), "chutes": self.n_terre,
                "releves": self.n_releves, "expires": self.n_expires, "commandes": self.n_commandes}
