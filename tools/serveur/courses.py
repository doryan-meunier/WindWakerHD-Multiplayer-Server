"""Courses en bateau (Skyward Sword), lot C1 de docs/etudes/autres_zelda.md §3 : le serveur tient la course.

Déroulé (une course à la fois) :

1. **Lancement** : le lanceur de l'hôte (outil lobby, ``lanceur_hote``) envoie ``course_cmd {action: "lancer",
   parcours}`` (fichier ``courses.json`` du lanceur, tracé en jeu : synchro/courses.py). Refusé en mode Hanteurs,
   pendant une partie de cache-cache ou une autre course. Tous les joueurs de jeu dont le client annonce ``FEAT``
   reçoivent ``course {ev: "annonce", parcours, ...}`` (le départ est montré comme le signal n° 3).
2. **Rassemblement** : jusqu'à ce que tous soient à moins de 40 m du départ (en mer), ou ``RASSEMBLEMENT_MAX`` s ;
   ceux qui y sont alors sont les coureurs (personne : course annulée).
3. **Décompte** : ``course {ev: "depart", dans: 3}`` ; chaque client affiche « 3… 2… 1… Partez ! ». Faux départ
   (bateau à plus de 2 m/s avant « Partez », vu par le client : ``course_faux`` ; ou bateau déplacé de plus de
   2 m/s × 3 s vu ici à « Partez ») : +3 s, une fois.
4. **Course** : ``course_passage {id, k, age}`` pour la balise ``k`` (dans l'ordre, tours compris). L'heure du
   passage est celle du SERVEUR : réception moins l'aller simple (``rtt`` des « ping ») moins ``age`` (commun/latence.py
   ``instant_emetteur``) ; jamais l'horloge du PC du coureur. Refusé : hors de l'ordre, ou vitesse impossible entre
   la balise précédente (ou le départ) et celle-ci (``VMAX``). Disqualifié : ``course_dq`` (téléportation vue par le
   client : Ballade des vents). Abandon : ``course_abandon`` (à pied, commande), hors de la mer plus de
   ``HORS_MER`` s vu ici, ou déconnexion. Requiem des vents : interdit aux coureurs (``requiem`` : le vent n'est pas
   relayé, serveur/principal.py le remet chez le coureur).
5. **Fin** : quand plus personne ne court, ``FIN_APRES`` s après la première arrivée, ou ``COURSE_MAX`` s :
   ``course {ev: "fin", classement, record}`` ; records par parcours (empreinte), gardés dans le monde persistant
   (jamais ceux de l'essai) et rendus au lanceur par le lobby (il les écrit dans son fichier).

Classement seul, sans récompense. Ralliement interdit pendant une course (serveur/ralliement.py ``course``).

ESSAI « course en bateau » (``--essai-course``, Réglages › Essais du lanceur ; à retirer après validation, tout ce qui
sert à l'essai est marqué ESSAI) : un client d'essai envoie ``course_essai {parcours}`` (boucle générée autour de SON
bateau, synchro/courses.parcours_essai) ; il court seul contre des faux concurrents (``RIVAUX``, temps simulés),
rassemblement court. Ses records sont montrés (marqués « essai ») mais jamais gardés dans le monde persistant.
Aucune entrée/sortie : testé hors ligne (tests/test_courses.py).
"""
from __future__ import annotations

import math
import time

from commun import latence as lat_mod
from synchro.courses import (DECOMPTE, DEPART_RAYON, ETAPE, FAUX_DEPART_VITESSE, FEAT, PENALITE, RIVAUX, VMAX,  # noqa: F401
                             chrono, distance, empreinte, est_id as _id, rival_temps, sequence, valide_parcours,
                             vitesse_min)

RASSEMBLEMENT_MAX = 90.0    # s : au-delà, ceux qui sont au départ courent
RASSEMBLEMENT_MIN = 2.0     # s : l'annonce se voit avant le décompte
ETAT_AGE = 3.0              # s : position plus vieille : pas comptée au départ
HORS_MER = 3.0              # s hors de la mer (vu par le serveur) : abandon
FIN_APRES = 120.0           # s après la première arrivée : fin
COURSE_MAX = 1200.0         # s (20 min)
AVANCE = 0.3                # s : un passage daté jusqu'à cela avant « Partez » est accepté (horloges, réseau)
DERNIERES_MAX = 8
RECORDS_MAX = 64
STATUTS = ("course", "arrive", "dq", "abandon", "pas_fini")
REQUIEM_ENTRE = 5.0         # s : un vent remis au plus une fois par coureur dans ce délai
ESSAI_RASSEMBLEMENT = 2.0   # ESSAI : décompte 2 s après l'annonce


def _xz(p) -> dict | None:
    if isinstance(p, (list, tuple)) and len(p) == 3 and all(isinstance(v, (int, float)) for v in p):
        return {"x": float(p[0]), "z": float(p[2])}
    return None


class CoursesServeur:
    """``joueur(pid)`` : dict {nom, joueur, zone, pos, t, feat} ou None ; les envois attendent dans ``envois``
    ([(destinataire ou None = tous les joueurs de jeu, message)]), vidés par ``prendre_envois`` ; les lignes de journal
    dans ``journal``."""

    def __init__(self, on: bool = True, essai: bool = False, clock=time.monotonic, mur=time.time):
        self.on = bool(on)
        self.essai = bool(essai) and self.on      # ESSAI : course d'essai d'un client, faux concurrents
        self.clock, self.mur = clock, mur
        self.course: dict | None = None
        self.records: dict[str, dict] = {}        # empreinte -> {parcours, temps, qui, t, essai}
        self.dernieres: list[dict] = []
        self.envois: list[tuple] = []
        self.journal: list[str] = []
        self.seq = 0
        self.rev = 0
        self.records_changes = False              # le monde persistant doit être réécrit
        self.n_courses = self.n_passages = self.n_refus = 0

    # --- outils -----------------------------------------------------------------------------------------------------
    def _log(self, s: str) -> None:
        self.journal.append("course : " + s)

    def _tous(self, m: dict) -> None:
        self.envois.append((None, m))

    def _a(self, pid: int, m: dict) -> None:
        self.envois.append((pid, m))

    def prendre_envois(self) -> list[tuple]:
        out, self.envois = self.envois, []
        return out

    def prendre_journal(self) -> list[str]:
        out, self.journal = self.journal, []
        return out

    def actif(self) -> bool:
        return self.course is not None

    def en_cours(self) -> bool:
        """Course lancée (rassemblement compris) : le chant de ralliement est interdit."""
        return self.course is not None

    def coureur(self, pid) -> dict | None:
        c = self.course
        return c["coureurs"].get(pid) if c is not None else None

    def _msg(self, ev: str, **kw) -> dict:
        c = self.course
        m = {"type": "course", "ev": ev, "id": c["id"] if c else 0}
        if c is not None and c["essai"]:
            m["essai"] = 1
        m.update(kw)
        return m

    # --- lancement ----------------------------------------------------------------------------------------------------
    def lancer(self, brut, par: str, candidats: list, now: float | None = None, hanteurs: bool = False,
               cache: bool = False, essai_de: int | None = None) -> str:
        """Lance un parcours ; ``candidats`` : les joueurs qui pourront courir (FEAT). Rend "" ou la raison du refus.
        ``essai_de`` (ESSAI) : course d'essai de ce seul joueur, avec les faux concurrents."""
        now = self.clock() if now is None else now
        essai = essai_de is not None
        if hanteurs:
            return "mode Hanteurs (pas de course)"
        if not self.on:
            return "courses coupées"
        if essai and not self.essai:
            return "serveur sans --essai-course"
        if cache:
            return "cache-cache en cours"
        if self.course is not None:
            return f"une course est déjà lancée (« {self.course['parcours']['nom']} »)"
        p = valide_parcours(brut)
        if p is None:
            return "parcours invalide"
        cand = [essai_de] if essai else [i for i in candidats if _id(i)]
        if not cand:
            return "personne ne peut courir (client sans les courses)"
        self.seq += 1
        emp = empreinte(p)
        if isinstance(brut, dict):
            self._record_fichier(emp, p["nom"], brut.get("record"), essai)
        self.course = {"id": self.seq, "parcours": p, "emp": emp, "seq": sequence(p), "phase": "rassemblement",
                       "t0": now, "t_go": None, "candidats": set(cand), "coureurs": {}, "rivaux": [],
                       "essai": essai, "par": str(par)[:24], "t_arrivee1": None}
        self.n_courses += 1
        self.rev += 1
        rec = self.records.get(self._cle(emp, essai))
        self._tous(self._msg("annonce", parcours=p, emp=emp, n=len(self.course["seq"]), par=str(par)[:24],
                             candidats=sorted(cand), rayon_depart=DEPART_RAYON,
                             attente=ESSAI_RASSEMBLEMENT if essai else RASSEMBLEMENT_MAX,
                             record=self._rec_vue(rec)))
        self._log(f"« {p['nom']} » lancée par {par}{' (ESSAI)' if essai else ''} : {len(p['balises'])} balise(s), "
                  f"{p['tours']} tour(s) ; rassemblement au départ (40 m)")
        return ""

    def annuler(self, raison: str) -> bool:
        c = self.course
        if c is None:
            return False
        self._tous(self._msg("annulee", raison=str(raison)[:80]))
        self._log(f"« {c['parcours']['nom']} » annulée ({raison})")
        self.course = None
        self.rev += 1
        return True

    # --- horloge ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _en_mer(j: dict | None) -> bool:
        z = str((j or {}).get("zone") or "")
        return z.split(":", 1)[0] == ETAPE

    def _au_depart(self, j: dict | None, now: float) -> bool:
        if not j or not j.get("joueur") or not self._en_mer(j):
            return False
        p = _xz(j.get("pos"))
        t = j.get("t")
        if p is None or not isinstance(t, (int, float)) or now - t > ETAT_AGE:
            return False
        return distance(p, self.course["parcours"]["depart"]) <= DEPART_RAYON

    def tick(self, joueur, now: float | None = None) -> None:
        """Phases (rassemblement, décompte, course, fin), faux concurrents (ESSAI), hors de la mer."""
        c = self.course
        if c is None:
            return
        now = self.clock() if now is None else now
        if c["phase"] == "rassemblement":
            presents = [i for i in sorted(c["candidats"]) if self._au_depart(joueur(i), now)]
            mini = ESSAI_RASSEMBLEMENT if c["essai"] else RASSEMBLEMENT_MIN
            tous = len(presents) == len(c["candidats"])
            if now - c["t0"] < mini or (not tous and now - c["t0"] < RASSEMBLEMENT_MAX):
                return
            if not presents:
                self.annuler("personne au départ (à moins de 40 m)")
                return
            self._decompte(presents, joueur, now)
            return
        if c["phase"] == "decompte":
            if now < c["t_go"]:
                return
            c["phase"] = "course"
            for pid, r in c["coureurs"].items():           # faux départ vu ici : bateau déplacé pendant le décompte
                j = joueur(pid)
                p = _xz((j or {}).get("pos"))
                if p is not None and r["pos0"] is not None and not r["faux"] \
                        and distance(p, r["pos0"]) > FAUX_DEPART_VITESSE * DECOMPTE:
                    self._penalite(pid, r, "déplacé pendant le décompte (vu par le serveur)")
            if c["essai"]:                                  # ESSAI : faux concurrents, temps simulés
                c["rivaux"] = [{"nom": nom, "temps": rival_temps(c["parcours"], v, c["id"] * 31 + k), "k": 0}
                               for k, (nom, v) in enumerate(RIVAUX)]
            self._log(f"« {c['parcours']['nom']} » : partez ! ({', '.join(r['nom'] for r in c['coureurs'].values())})")
            self.rev += 1
            return
        # course
        el = now - c["t_go"]
        for rv in c["rivaux"]:                              # ESSAI
            while rv["k"] < len(rv["temps"]) and rv["temps"][rv["k"]] <= el:
                rv["k"] += 1
                fini = rv["k"] == len(rv["temps"])
                self._tous(self._msg("arrivee" if fini else "passage", nom=rv["nom"], k=rv["k"] - 1,
                                     n=len(c["seq"]), temps=rv["temps"][rv["k"] - 1], rival=1,
                                     classement=self.classement_live(now)))
        for pid, r in c["coureurs"].items():
            if r["statut"] != "course":
                continue
            j = joueur(pid)
            if j is None:
                self._fin_coureur(pid, r, "abandon", "parti")
            elif not self._en_mer(j):
                if r["hors_mer"] is None:
                    r["hors_mer"] = now
                elif now - r["hors_mer"] > HORS_MER:
                    self._fin_coureur(pid, r, "abandon", "a quitté la mer")
            else:
                r["hors_mer"] = None
        actifs = [r for r in c["coureurs"].values() if r["statut"] == "course"]
        if not actifs:
            self.finir("tous arrivés ou sortis", now)
        elif c["t_arrivee1"] is not None and now - c["t_arrivee1"] > FIN_APRES:
            self.finir(f"{int(FIN_APRES)} s après la première arrivée", now)
        elif el > COURSE_MAX:
            self.finir(f"{int(COURSE_MAX // 60)} min de course", now)

    def _decompte(self, presents: list, joueur, now: float) -> None:
        c = self.course
        c["phase"] = "decompte"
        c["t_go"] = now + DECOMPTE
        for pid in presents:
            j = joueur(pid) or {}
            c["coureurs"][pid] = {"nom": str(j.get("nom") or pid)[:24], "k": 0, "t_last": None, "temps": None,
                                  "statut": "course", "penalite": 0.0, "faux": False, "refus": 0,
                                  "pos0": _xz(j.get("pos")), "hors_mer": None, "raison": ""}
        noms = [c["coureurs"][i]["nom"] for i in presents]
        self._tous(self._msg("depart", dans=DECOMPTE, coureurs=list(presents), noms=noms,
                             rivaux=[n for n, _v in RIVAUX] if c["essai"] else []))
        self._log(f"« {c['parcours']['nom']} » : décompte ({', '.join(noms)})")
        self.rev += 1

    # --- événements des coureurs ----------------------------------------------------------------------------------
    def _valide(self, pid, msg) -> tuple[dict | None, str]:
        c = self.course
        if c is None:
            return None, "pas de course"
        if not isinstance(msg, dict) or msg.get("id") != c["id"]:
            return None, "autre course"
        r = c["coureurs"].get(pid)
        if r is None:
            return None, "pas coureur"
        if r["statut"] != "course":
            return None, "déjà sorti de la course"
        return r, ""

    def passage(self, pid, msg, rtt_ms=None, now: float | None = None) -> str:
        """``course_passage {id, k, age}`` : "" si accepté, sinon la raison."""
        now = self.clock() if now is None else now
        r, raison = self._valide(pid, msg)
        if r is None:
            return raison
        c = self.course
        k = msg.get("k")
        if c["phase"] != "course" and not (c["phase"] == "decompte" and now >= c["t_go"] - AVANCE):
            return "avant le départ"
        if not _id(k) or k != r["k"]:
            if _id(k) and k < r["k"]:
                return "déjà passée"
            self.n_refus += 1
            self._a(pid, self._msg("refus", k=k if _id(k) else -1, raison="balise hors de l'ordre", attendue=r["k"]))
            return "hors de l'ordre"
        t = lat_mod.instant_emetteur(now, rtt_ms, msg.get("age"))
        t = max(t, c["t_go"])
        if k == 0:
            prec, rp, tp = c["parcours"]["depart"], DEPART_RAYON, c["t_go"]
        else:
            prec, rp, tp = c["seq"][k - 1], c["seq"][k - 1]["r"], r["t_last"]
        b = c["seq"][k]
        v = vitesse_min(prec, rp, b, b["r"], t - tp)
        if v > VMAX:
            r["refus"] += 1
            self.n_refus += 1
            self._a(pid, self._msg("refus", k=k, raison=f"vitesse impossible ({v / 100:.0f} m/s)", attendue=k))
            self._log(f"{r['nom']} : balise {k + 1} refusée (vitesse impossible, {v / 100:.0f} m/s)")
            return "vitesse impossible"
        r["k"], r["t_last"] = k + 1, t
        self.n_passages += 1
        el = round(t - c["t_go"], 2)
        if r["k"] == len(c["seq"]):
            r["temps"] = round(el + r["penalite"], 2)
            r["statut"] = "arrive"
            if c["t_arrivee1"] is None:
                c["t_arrivee1"] = now
            self._tous(self._msg("arrivee", **{"from": pid}, nom=r["nom"], k=k, n=len(c["seq"]), temps=r["temps"],
                                 penalite=r["penalite"], classement=self.classement_live(now)))
            self._log(f"{r['nom']} arrive en {chrono(r['temps'])}"
                      + (f" (dont {r['penalite']:.0f} s de pénalité)" if r["penalite"] else ""))
        else:
            self._tous(self._msg("passage", **{"from": pid}, nom=r["nom"], k=k, n=len(c["seq"]), temps=el,
                                 classement=self.classement_live(now)))
        self.rev += 1
        return ""

    def _penalite(self, pid, r: dict, pourquoi: str) -> None:
        r["faux"] = True
        r["penalite"] += PENALITE
        self._tous(self._msg("penalite", **{"from": pid}, nom=r["nom"], s=PENALITE, raison=pourquoi[:60]))
        self._log(f"{r['nom']} : faux départ, +{PENALITE:.0f} s ({pourquoi})")

    def faux_depart(self, pid, msg, now: float | None = None) -> str:
        now = self.clock() if now is None else now
        r, raison = self._valide(pid, msg)
        if r is None:
            return raison
        c = self.course
        if c["phase"] != "decompte" and not (c["phase"] == "course" and now <= c["t_go"] + 1.0):
            return "pas pendant le décompte"
        if r["faux"]:
            return "déjà pénalisé"
        self._penalite(pid, r, "bateau parti avant « Partez »")
        self.rev += 1
        return ""

    def _fin_coureur(self, pid, r: dict, statut: str, raison: str) -> None:
        r["statut"], r["raison"] = statut, str(raison)[:60]
        self._tous(self._msg("dq" if statut == "dq" else "abandon", **{"from": pid}, nom=r["nom"],
                             raison=r["raison"]))
        self._log(f"{r['nom']} : {'disqualifié' if statut == 'dq' else 'abandon'} ({r['raison']})")
        self.rev += 1

    def disqualifier(self, pid, msg, now: float | None = None) -> str:
        r, raison = self._valide(pid, msg)
        if r is None:
            return raison
        self._fin_coureur(pid, r, "dq", str(msg.get("raison") or "téléportation (Ballade des vents)"))
        return ""

    def abandon(self, pid, msg, now: float | None = None) -> str:
        r, raison = self._valide(pid, msg)
        if r is None:
            return raison
        self._fin_coureur(pid, r, "abandon", str(msg.get("raison") or "commande"))
        return ""

    def requiem(self, pid, now: float | None = None) -> tuple[bool, bool]:
        """Requiem des vents d'un coureur pendant le décompte ou la course : (interdit : le vent n'est pas relayé,
        remettre : renvoyer au coureur le vent du groupe et le prévenir, au plus une fois toutes les
        ``REQUIEM_ENTRE`` s)."""
        c = self.course
        r = self.coureur(pid)
        if c is None or r is None or r["statut"] != "course" or c["phase"] == "rassemblement":
            return False, False
        now = self.clock() if now is None else now
        if now - r.get("requiem_t", float("-inf")) < REQUIEM_ENTRE:
            return True, False
        r["requiem_t"] = now
        r["requiems"] = r.get("requiems", 0) + 1
        self._a(pid, self._msg("requiem", raison="Requiem des vents interdit pendant la course : vent remis"))
        self._log(f"{r['nom']} : Requiem des vents pendant la course, refusé (vent remis)")
        return True, True

    def oublier(self, pid) -> None:
        c = self.course
        if c is None:
            return
        c["candidats"].discard(pid)
        r = c["coureurs"].get(pid)
        if r is not None and r["statut"] == "course":
            self._fin_coureur(pid, r, "abandon", "parti")

    # --- classement ---------------------------------------------------------------------------------------------------
    def classement_live(self, now: float | None = None) -> list:
        """[[nom, balises passées, temps (s) ou None, statut], ...] dans l'ordre de la course (faux concurrents ESSAI
        compris)."""
        c = self.course
        if c is None:
            return []
        now = self.clock() if now is None else now
        lignes = []
        for r in c["coureurs"].values():
            t = r["temps"] if r["statut"] == "arrive" else (round(r["t_last"] - c["t_go"], 2) if r["t_last"] else None)
            lignes.append([r["nom"], r["k"], t, r["statut"]])
        if c["t_go"] is not None and c["phase"] == "course":
            el = now - c["t_go"]
            for rv in c["rivaux"]:
                k = sum(1 for t in rv["temps"] if t <= el)
                fini = k == len(rv["temps"])
                lignes.append([rv["nom"], k, rv["temps"][k - 1] if k else None, "arrive" if fini else "course"])

        def cle(e):
            sortie = e[3] in ("dq", "abandon")
            return (sortie, -e[1] if e[3] != "arrive" else -10 ** 6, e[2] if e[2] is not None else 1e9)
        return sorted(lignes, key=cle)

    def rang_de(self, nom: str, now: float | None = None) -> int | None:
        for i, e in enumerate(self.classement_live(now)):
            if e[0] == nom:
                return i + 1
        return None

    # --- fin et records -------------------------------------------------------------------------------------------
    @staticmethod
    def _cle(emp: str, essai: bool) -> str:
        return ("essai:" if essai else "") + emp

    @staticmethod
    def _rec_vue(rec: dict | None) -> dict | None:
        return {k: rec[k] for k in ("temps", "qui", "t")} if rec else None

    def _record_fichier(self, emp: str, nom: str, rec, essai: bool) -> None:
        """Record connu du fichier du lanceur (envoyé avec le parcours) : gardé s'il est meilleur."""
        if not isinstance(rec, dict) or essai:
            return
        t, qui = rec.get("temps"), rec.get("qui")
        if not (isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and 1.0 <= t < 1e5
                and isinstance(qui, str)):
            return
        cle = self._cle(emp, False)
        cur = self.records.get(cle)
        if cur is None or t < cur["temps"]:
            self.records[cle] = {"parcours": nom, "temps": round(float(t), 2), "qui": qui[:24],
                                 "t": rec.get("t") if _id(rec.get("t")) else 0, "essai": False}

    def finir(self, raison: str, now: float | None = None) -> dict | None:
        c = self.course
        if c is None:
            return None
        now = self.clock() if now is None else now
        lignes = []
        for r in c["coureurs"].values():
            st = r["statut"] if r["statut"] != "course" else "pas_fini"
            lignes.append({"nom": r["nom"], "temps": r["temps"], "statut": st, "penalite": r["penalite"],
                           "balises": r["k"], "raison": r["raison"]})
        for rv in c["rivaux"]:                              # ESSAI : temps simulés jusqu'au bout
            lignes.append({"nom": rv["nom"], "temps": rv["temps"][-1], "statut": "arrive", "penalite": 0.0,
                           "balises": len(rv["temps"]), "raison": "", "rival": 1})
        ordre = {"arrive": 0, "pas_fini": 1, "abandon": 2, "dq": 3}
        lignes.sort(key=lambda e: (ordre[e["statut"]], e["temps"] if e["temps"] is not None else 1e9,
                                   -e["balises"]))
        for i, e in enumerate(lignes):
            e["rang"] = i + 1 if e["statut"] == "arrive" else None
        humains = [e for e in lignes if e["statut"] == "arrive" and not e.get("rival")]
        cle = self._cle(c["emp"], c["essai"])
        avant = self.records.get(cle)
        nouveau = False
        if humains and (avant is None or humains[0]["temps"] < avant["temps"]):
            self.records[cle] = {"parcours": c["parcours"]["nom"], "temps": humains[0]["temps"],
                                 "qui": humains[0]["nom"], "t": int(self.mur()), "essai": c["essai"]}
            nouveau = True
            if not c["essai"]:
                self.records_changes = True
            while len(self.records) > RECORDS_MAX:
                self.records.pop(next(iter(self.records)))
        rec = self.records.get(cle)
        res = {"rid": f"{c['id']}-{int(self.mur())}", "id": c["id"], "emp": c["emp"], "parcours": c["parcours"]["nom"],
               "classement": lignes, "record": self._rec_vue(rec), "nouveau_record": nouveau, "raison": raison[:60],
               "t": int(self.mur()), **({"essai": 1} if c["essai"] else {})}
        self.dernieres = (self.dernieres + [res])[-DERNIERES_MAX:]
        self._tous(self._msg("fin", **{k: res[k] for k in ("rid", "emp", "parcours", "classement", "record",
                                                          "nouveau_record", "raison")}))
        podium = ", ".join(f"{e['rang']}. {e['nom']} {chrono(e['temps'])}" for e in lignes if e["rang"])
        self._log(f"« {c['parcours']['nom']} » finie ({raison}) : {podium or 'personne à l’arrivée'}"
                  + (" ; NOUVEAU RECORD" if nouveau else ""))
        self.course = None
        self.rev += 1
        return res

    # --- vue, monde persistant ----------------------------------------------------------------------------------------
    def vue(self, now: float | None = None) -> dict:
        """Section « courses » du lobby (trackeur, lanceur)."""
        c = self.course
        en = None
        if c is not None:
            now = self.clock() if now is None else now
            en = {"id": c["id"], "parcours": c["parcours"]["nom"], "emp": c["emp"], "phase": c["phase"],
                  "n": len(c["seq"]), "par": c["par"], "classement": self.classement_live(now),
                  "temps": round(now - c["t_go"], 1) if c["t_go"] is not None and now >= c["t_go"] else None,
                  **({"essai": 1} if c["essai"] else {})}
        recs = sorted(({"emp": k.split(":", 1)[-1], **v} for k, v in self.records.items()),
                      key=lambda r: -r.get("t", 0))
        return {"v": 1, "rev": self.rev, "on": self.on, "essai": 1 if self.essai else 0, "en_cours": en,
                "dernieres": list(self.dernieres), "records": recs[:RECORDS_MAX]}

    def exporter(self) -> dict:
        """Section « courses » du monde persistant : les records (jamais ceux de l'essai)."""
        return {"v": 1, "records": {k: v for k, v in self.records.items() if not v.get("essai")}}

    def restaurer(self, doc) -> int:
        """Relit la section du monde (records abîmés ignorés) ; rend le nombre d'entrées ignorées."""
        if not isinstance(doc, dict) or not isinstance(doc.get("records"), dict):
            return 0
        bad = 0
        for k, v in list(doc["records"].items())[:RECORDS_MAX]:
            ok = (isinstance(k, str) and len(k) == 12 and all(ch in "0123456789abcdef" for ch in k)
                  and isinstance(v, dict) and isinstance(v.get("temps"), (int, float))
                  and not isinstance(v.get("temps"), bool) and math.isfinite(v["temps"]) and 1.0 <= v["temps"] < 1e5
                  and isinstance(v.get("qui"), str) and isinstance(v.get("parcours"), str))
            if not ok:
                bad += 1
                continue
            self.records[k] = {"parcours": v["parcours"][:40], "temps": round(float(v["temps"]), 2),
                               "qui": v["qui"][:24], "t": v.get("t") if _id(v.get("t")) else 0, "essai": False}
        return bad

    def etat_essai(self) -> dict:
        """Rapport d'essai : compteurs."""
        return {"courses": self.n_courses, "passages": self.n_passages, "refus": self.n_refus,
                "en_cours": self.course is not None, "records": len(self.records)}
