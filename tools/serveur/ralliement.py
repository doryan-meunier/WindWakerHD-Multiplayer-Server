"""Chant de ralliement (Ocarina, Majora), lots S2 et S3 de docs/etudes/autres_zelda.md §7 : le serveur.

Un joueur joue le morceau 6 de la Baguette (mesure 3/4 : haut, bas, droite ; compté par le mod natif 0.45.0, zone
WWMPTAC1, natif/mod_wwmp/ralliement.c) : son client envoie ``ralliement {stage, room, pos, rotY}``. Ici :

* règle de la PARTIE « Ralliement », ALLUMÉE par défaut (``--no-ralliement``, case du lobby, ``regles_cmd``), toujours
  COUPÉE en mode Hanteurs (ni un Hanteur ni un Link possédé ne chantent ni ne sont appelés) ;
* refusé : moins de ``DELAI`` s (5 min) depuis le dernier chant accepté de ce joueur ; appelant en salle de boss
  (synchro/boss.py BOSS_STAGES), en cinématique, occupé (menu, porté, porteur : champ « nc » du state), possédé ou
  Hanteur ; une partie de cache-cache ou une course en cours ; position trop vieille ou d'une autre salle ;
* appelés : les autres joueurs de jeu (jamais le robot ni un outil) dont le client annonce ``FEAT`` ; jamais un joueur
  en salle de boss ; dans un DONJON (serveur/medailles.py DONJONS) seulement ceux qui sont déjà dans ce donjon (on ne
  saute pas des portes que l'équipe n'a pas ouvertes) ; en mer et sur les îles : tous. Chacun reçoit
  ``ralliement_appel {id, from, nom, stage, room, pos, centre, rotY, delai}`` : ``pos`` = sa place sur un cercle de
  ``RAYON`` u (2 m) autour de l'appelant ;
* chaque appelé a ``REFUS`` s (10 s) pour refuser (``ralliement_rep {id, refus: 1}``) ; le serveur l'accepte jusqu'à
  ``REFUS + GRACE`` s et prévient l'appelant ; à l'échéance, son client le déplace (CALL 15 PLACER dans la même
  salle, ALLER sinon), sauf s'il est en menu, en message, en événement ou en changement d'étape (client/ralliement.py).
  Arrivé : ``ralliement_rep {id, arrive: 1}`` -> l'appelant est prévenu.

ESSAI « chant de ralliement » (``--essai-ralliement``, Réglages › Essais du lanceur ; à retirer après validation, tout
ce qui sert à l'essai est marqué ESSAI) : le faux coéquipier d'un client d'essai (``ID_ESSAI``, sur son seul PC) est
appelé comme un vrai (sa place renvoyée à l'appelant dans ``faux``) ; et il peut APPELER ce client
(``ralliement {faux: 1, ...}`` à sa position) : l'appel revient à ce client comme un vrai. Délai d'essai :
``ESSAI_DELAI`` s au lieu de 5 min (pour rejouer l'essai).
Aucune entrée/sortie : testé hors ligne (tests/test_ralliement.py).
"""
from __future__ import annotations

import math
import time

from serveur import medailles as med_mod
from synchro.ralliement import (DELAI, ESSAI_DELAI, FEAT, ID_ESSAI, NOM_ESSAI, RAYON, REFUS, angle_vers,  # noqa: F401
                                est_id as _id, etape_ok, place, pos3, salle_de_boss, salle_ok)

GRACE = 2.0                 # s de plus pour un refus (réseau)
ETAT_AGE = 5.0              # s : position de l'appelant plus vieille : refusé
APPELS_GARDES = 60.0        # s : un appel plus vieux est oublié
APPELES_MAX = 15


def donjon_de(stage) -> int | None:
    """Numéro du donjon de l'étape (serveur/medailles.py DONJONS), None en mer et sur les îles."""
    return med_mod.donjon_de(stage)


def zone_de(stage, room) -> str:
    return f"{stage}:{room}"


class RalliementServeur:
    """``joueur(pid)`` : dict {nom, joueur, zone, busy, nc, pos, t, feat, role, possede} ou None ; ``ids()`` : les
    joueurs connectés."""

    def __init__(self, on: bool = True, essai: bool = False, clock=time.monotonic):
        self.on = bool(on)
        self.essai = bool(essai)                 # ESSAI : faux coéquipier appelé / appelant, délai court
        self.vu = False                          # changée à chaud une fois : « regles » l'annonce (welcome sinon)
        self.clock = clock
        self.dernier: dict[int, float] = {}      # joueur -> heure du dernier chant accepté
        self.appels: dict[int, dict] = {}        # id -> {src, t, cibles: set, refus: set, arrives: set}
        self.seq = 0
        self.n_chants = self.n_refus = self.n_refuses = self.n_arrives = 0

    def oublier(self, pid: int) -> None:
        self.dernier.pop(pid, None)
        for a in self.appels.values():
            a["cibles"].discard(pid)

    def _purger(self, now: float) -> None:
        for k in [k for k, a in self.appels.items() if now - a["t"] > APPELS_GARDES]:
            del self.appels[k]

    def delai(self) -> float:
        return ESSAI_DELAI if self.essai else DELAI

    @staticmethod
    def _occupe(j: dict, chant: bool = False) -> str:
        """"" si ce joueur peut chanter / être appelé (vu du serveur), sinon la raison. ``chant`` : pour l'APPELANT,
        la cinématique ne compte pas : jouer de la Baguette est lui-même un événement du jeu (``busy`` vrai jusqu'à
        la fin du morceau ; rapport du 2026-11-17 : « appelant en cinématique » à chaque chant)."""
        if j.get("role") == "hanteur":
            return "Hanteur"
        if j.get("possede"):
            return "Link possédé"
        if j.get("busy") and not chant:
            return "en cinématique"
        if j.get("nc"):
            return "occupé (menu, porté ou porteur)"
        return ""

    def chant(self, src: int, msg: dict, joueur, ids, now: float, hanteurs: bool = False, cache: bool = False,
              course: bool = False) -> tuple[str, dict | None, list[tuple[int, dict]]]:
        """``ralliement`` de ``src``. Renvoie (raison du refus ou "", réponse pour ``src``, [(destinataire, appel)])."""
        self._purger(now)
        if hanteurs:
            return "mode Hanteurs (ralliement coupé)", None, []
        essai = self.essai and msg.get("essai") in (1, True)
        if not self.on and not essai:
            return "règle de la partie coupée", None, []
        stage, room, p = msg.get("stage"), msg.get("room"), pos3(msg.get("pos"))
        rot = msg.get("rotY") if _id(msg.get("rotY")) else 0
        if not etape_ok(stage) or not salle_ok(room) or p is None:
            return "message invalide", None, []
        es = joueur(src)
        if es is None or not es.get("joueur"):
            return "pas un joueur de jeu (robot, outil)", None, []
        faux = essai and msg.get("faux") in (1, True)        # ESSAI (2) : le faux coéquipier appelle src
        appelant = ID_ESSAI if faux else src
        if not faux:
            r = self._occupe(es, chant=True)
            if r:
                return "appelant " + r, None, []
            if es.get("zone") != zone_de(stage, room):
                return "appelant pas dans cette salle", None, []
            if pos3(es.get("pos")) is None or es.get("t") is None or now - es["t"] > ETAT_AGE:
                return "position de l'appelant trop vieille", None, []
        if salle_de_boss(stage):
            return "salle de boss", None, []
        if cache:
            return "cache-cache en cours", None, []
        if course:
            return "course en cours", None, []
        reste = self.delai() - (now - self.dernier.get(appelant, float("-inf")))
        if reste > 0:
            return f"encore {int(math.ceil(reste))} s avant un nouveau chant", None, []
        # appelés
        d = donjon_de(stage)
        candidats = [src] if faux else [i for i in sorted(ids()) if i != src]
        cibles, exclus = [], []
        for i in candidats:
            j = joueur(i)
            if j is None or not j.get("joueur"):
                continue
            nom = str(j.get("nom") or i)[:24]
            if FEAT not in (j.get("feat") or ()):
                exclus.append([nom, "client sans le ralliement"])
                continue
            r = self._occupe(j) if j.get("role") == "hanteur" or j.get("possede") else ""
            zj = str(j.get("zone") or "")
            sj = zj.split(":", 1)[0] if zj else None
            if r:
                exclus.append([nom, r])
            elif salle_de_boss(sj):
                exclus.append([nom, "en salle de boss"])
            elif d is not None and donjon_de(sj) != d:
                exclus.append([nom, "pas dans ce donjon"])
            else:
                cibles.append(i)
        faux_cible = essai and not faux                     # ESSAI (1) : le faux coéquipier est appelé aussi
        n = len(cibles[:APPELES_MAX]) + (1 if faux_cible else 0)
        if n == 0:
            return "personne à appeler" + (" (" + ", ".join(f"{a} : {b}" for a, b in exclus[:4]) + ")"
                                           if exclus else ""), None, []
        self.dernier[appelant] = now
        self.seq += 1
        self.n_chants += 1
        aid = self.seq
        nom_appelant = NOM_ESSAI if faux else str(es.get("nom") or src)[:24]
        envois = []
        for k, i in enumerate(cibles[:APPELES_MAX]):
            arr = place(p, k, n, rot)
            envois.append((i, {"type": "ralliement_appel", "id": aid, "from": appelant, "nom": nom_appelant,
                               "stage": stage, "room": room, "pos": arr, "centre": [round(v, 1) for v in p],
                               "rotY": angle_vers(arr, p), "delai": REFUS, **({"essai": 1} if faux else {})}))
        self.appels[aid] = {"src": appelant, "t": now, "cibles": set(cibles[:APPELES_MAX]), "refus": set(),
                            "arrives": set()}
        rep = {"type": "ralliement_rep", "ok": 1, "id": aid, "n": n, "exclus": exclus[:8], "delai": REFUS}
        if faux:
            rep.update(essai=1, faux=1)
        if faux_cible:
            arr = place(p, n - 1, n, rot)
            rep.update(essai=1, faux_pos=arr, faux_rotY=angle_vers(arr, p))
        return "", rep, envois

    def reponse(self, src: int, msg: dict, nom: str, now: float) -> tuple[str, tuple[int, dict] | None]:
        """``ralliement_rep`` d'un appelé : refus (dans les REFUS + GRACE s) ou arrivée. Renvoie (raison du refus ou
        "", (appelant, message pour lui) ou None)."""
        self._purger(now)
        aid = msg.get("id")
        a = self.appels.get(aid) if _id(aid) else None
        if a is None:
            return "appel inconnu ou trop vieux", None
        if src not in a["cibles"]:
            return "pas appelé", None
        if msg.get("refus") in (1, True):
            if now - a["t"] > REFUS + GRACE:
                self.n_refuses += 1
                return "refus trop tard", None
            if src in a["refus"] or src in a["arrives"]:
                return "déjà répondu", None
            a["refus"].add(src)
            self.n_refus += 1
            out = {"type": "ralliement_info", "id": aid, "from": src, "nom": str(nom)[:24], "refus": 1}
        elif msg.get("arrive") in (1, True):
            if src in a["refus"] or src in a["arrives"]:
                return "déjà répondu", None
            a["arrives"].add(src)
            self.n_arrives += 1
            out = {"type": "ralliement_info", "id": aid, "from": src, "nom": str(nom)[:24], "arrive": 1}
        else:
            return "réponse invalide", None
        if a["src"] == ID_ESSAI:
            return "", None                      # ESSAI : le faux coéquipier n'a pas de client à prévenir
        return "", (a["src"], out)

    def vue(self) -> dict:
        return {"on": self.on, "essai": self.essai, "chants": self.n_chants, "refus": self.n_refus,
                "arrives": self.n_arrives, "appels": len(self.appels)}
