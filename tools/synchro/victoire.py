"""Mode Hanteurs : conditions de victoire (réglage « victoire » de la partie, lanceur ou --hanteurs-config).

Avant : la partie ne finissait jamais (les rôles tournaient, les points restaient une monnaie). Maintenant l'hôte
choisit une règle ; le serveur compte, annonce la fin de la manche à tous (``hfin``), puis relance une manche après
une pause. Tout est décidé par le serveur à partir de ce qu'il voit déjà (states, K.O., dégâts attribués) : rien
n'est écrit dans le jeu.

  libre   (par défaut) : pas de fin, comme avant.
  survie  : les Link gagnent s'ils tiennent ``victoire_min`` minutes ; les Hanteurs gagnent dès que les Link ont
            été mis K.O. ``victoire_ko`` fois en tout (toute mort d'un Link compte, quelle qu'en soit la cause).
  course  : les Link gagnent dès qu'ils ont traversé ``victoire_salles`` salles différentes (à eux tous, depuis
            le début de la manche) ; les Hanteurs gagnent si les ``victoire_min`` minutes sont écoulées avant.
  score   : chacun pour soi, les rôles tournent. Score = dégâts et K.O. infligés en Hanteur (SCORE_DEGAT par quart
            de cœur, SCORE_KO par K.O.) + survie en Link (1 point toutes les SCORE_SURVIE_S s en vie). Le premier
            à ``victoire_score`` gagne ; sinon le meilleur score au bout de ``victoire_min`` minutes.

Un Link possédé par la potion ou en quarantaine ne marque pas (ni salle, ni survie). Mixin de ``hanteurs.Partie``.

Fin de la PARTIE « comme dans Crawl » (``victoire_finale``, lancée par ``hanteurs.Partie.fin_ganon``) : quand le
changement des rôles est « crawl » ou « mixte », la partie finit à la mort de Ganon et gagnent ceux qui sont Link
à cet instant, quelle que soit la règle de manche ci-dessus (« libre » par défaut : pas de fin au temps).

Qui gagne (réglage ``vainqueurs``, « le but du jeu est de faire partie des Link à la fin ») :
  regle     : les gagnants de la règle de manche (ci-dessus), comme avant.
  link_fin  : quelle que soit la règle, la manche (ou la partie) finit comme d'habitude, mais GAGNENT CEUX QUI SONT
              LINK À CET INSTANT ; le score ne sert qu'à départager (tableau ``classement`` : les Link d'abord, puis
              le score de la manche en mode « score », sinon les points de Hanteur). La fin à la mort de Ganon
              (``fin_ganon``) est alors active même sans échange « comme dans Crawl ».
  auto      (par défaut) : « link_fin » quand le changement des rôles est « crawl » ou « mixte », sinon « regle ».
"""
from __future__ import annotations

import math

MODES = ("libre", "survie", "course", "score")
NOMS = {"libre": "libre", "survie": "survie", "course": "course", "score": "score"}
PAUSE = 15.0             # s entre la fin d'une manche et le début de la suivante
SCORE_DEGAT = 10         # par quart de cœur infligé (attribué à un Hanteur)
SCORE_KO = 100           # par K.O. attribué
SCORE_SURVIE_S = 3.0     # 1 point de survie toutes les 3 s en Link, en vie
SALLES_MAX = 512         # borne de l'ensemble des salles traversées
VAINQUEURS = ("auto", "regle", "link_fin")
DEFAUT = {"victoire": "libre", "victoire_min": 10, "victoire_ko": 3, "victoire_salles": 8, "victoire_score": 500,
          "vainqueurs": "auto"}
CLASSEMENT_MAX = 8       # lignes du tableau de fin (8 joueurs au plus)
BORNES = {"victoire_min": (1, 120), "victoire_ko": (1, 50), "victoire_salles": (2, 60), "victoire_score": (50, 9999)}

VAGUE_FENETRE = 30.0     # s : une mort reçue par deathlink (vague) n'est pas un K.O. de plus

CAMP_LINK, CAMP_HANTEUR = "link", "hanteur"


def valider(d: dict, c: dict, entier) -> None:
    """Réglages de victoire de ``d`` bornés dans ``c`` (appelé par ``hanteurs.valider_config``)."""
    if d.get("victoire") in MODES:
        c["victoire"] = d["victoire"]
    if d.get("vainqueurs") in VAINQUEURS:
        c["vainqueurs"] = d["vainqueurs"]
    for champ, (lo, hi) in BORNES.items():
        c[champ] = entier(d.get(champ), lo, hi, c[champ])


class VictoirePartie:
    """Mixin de ``hanteurs.Partie`` : manche, compteurs, fin annoncée."""

    def _victoire_init(self) -> None:
        self.v_t0: float | None = None             # début de la manche (None : pas commencée)
        self.v_pause: float | None = None          # fin de la pause entre deux manches
        self.v_manche = 0
        self.v_ko = 0                              # K.O. des Link pendant la manche
        self.v_salles: set[str] = set()            # zones traversées par les Link
        self.v_scores: dict[int, float] = {}       # id -> score (mode score)
        self.v_t_survie: float | None = None
        self.v_dernier: dict | None = None         # dernière fin annoncée (rediffusée dans l'état public)
        self.v_depart = 0                          # salles déjà occupées au début de la manche (non comptées)
        self.v_regle = "libre"                     # règle de la manche en cours (changée par l'hôte : relance)
        self.n_manches_finies = 0
        self.v_vague: dict[int, float] = {}        # Link -> échéance : sa prochaine mort vient du deathlink
        # fin de la partie « comme dans Crawl » (hanteurs.Partie.fin_ganon) : Ganon vaincu, gagnent les Link
        self.v_finie = False                       # partie finie : plus de manche, d'échange ni de rotation
        self.v_ganon: dict[int, float] = {}        # Link -> heure du premier rapport « fin de Ganondorf lancée »
        self.v_ganon_arme = True                   # désarmé à la fin ; réarmé par un Ganondorf revu debout

    def _v_mode(self) -> str:
        return self.cfg.get("victoire", "libre")

    def link_fin(self) -> bool:
        """Vrai : gagnent ceux qui sont Link à la fin (réglage ``vainqueurs``, automatique avec l'échange « comme
        dans Crawl »)."""
        v = self.cfg.get("vainqueurs", "auto")
        return v == "link_fin" or (v == "auto" and self.crawl_actif())

    def _v_classement(self, gagnants) -> list[dict]:
        """Tableau de fin : qui finit Link (gagnant avec ``link_fin``), puis le score pour départager (score de la
        manche en mode « score », sinon points de Hanteur)."""
        score = self.v_scores if self._v_mode() == "score" else self.points
        lignes = [{"nom": self.noms[p], "link": int(self.role(p) == CAMP_LINK), "score": int(score.get(p, 0)),
                   "gagne": int(p in gagnants)} for p in sorted(self.noms)]
        lignes.sort(key=lambda x: (-x["gagne"], -x["link"], -x["score"], x["nom"]))
        return lignes[:CLASSEMENT_MAX]

    def _v_marque(self, pid: int, now: float) -> bool:
        """Ce Link compte-t-il maintenant (ni possédé, ni en quarantaine) ?"""
        return (self.role(pid) == CAMP_LINK and self.possede_par(pid) is None
                and self.quarantaine.get(pid, 0.0) <= now)

    def victoire_debut(self, now: float | None = None, raison: str = "") -> None:
        """Nouvelle manche : compteurs à zéro. Appelée à la répartition, après la pause, ou par l'hôte."""
        now = self.clock() if now is None else now
        self.v_t0, self.v_pause = now, None
        self.v_ko = 0
        self.v_vague = {}
        self.v_salles = set()
        self.v_scores = {}
        self.v_t_survie = now
        self.v_depart = 0
        self.v_regle = self._v_mode()
        if self.v_regle == "libre":
            self.v_t0 = None
            return
        self.v_manche += 1
        for pid in self._liens():
            z = (self.etat.get(pid) or {}).get("zone")
            if isinstance(z, str) and self._v_marque(pid, now):
                self.v_salles.add(z)               # la salle de départ ne compte pas comme une salle gagnée…
        self.v_depart = len(self.v_salles)         # …on compte les salles au-delà de celles du départ
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : manche {self.v_manche} ({NOMS[self._v_mode()]})" + (f" : {raison}" if raison else ""))
        for pid in sorted(self.noms):
            self.sortie.append((pid, {"type": "hnote", "msg": f"manche {self.v_manche} : {self.regle()}"}))

    def regle(self) -> str:
        """La règle de la manche, en une phrase (notes aux joueurs, journal)."""
        c, m = self.cfg, self._v_mode()
        if m == "survie":
            if self.link_fin():
                return (f"survie : la manche finit après {c['victoire_min']} min ou au {c['victoire_ko']}e K.O. ; "
                        "gagnent ceux qui sont Link à la fin")
            return (f"survie : les Link gagnent s'ils tiennent {c['victoire_min']} min, les Hanteurs au "
                    f"{c['victoire_ko']}e K.O.")
        if m == "course":
            if self.link_fin():
                return (f"course : la manche finit après {c['victoire_salles']} salles ou {c['victoire_min']} min ; "
                        "gagnent ceux qui sont Link à la fin")
            return (f"course : les Link gagnent en traversant {c['victoire_salles']} salles en moins de "
                    f"{c['victoire_min']} min")
        if m == "score":
            return (f"score : le premier à {c['victoire_score']} points, ou le meilleur après {c['victoire_min']} min"
                    + (" ; gagnent ceux qui sont Link à la fin, le score départage" if self.link_fin() else ""))
        return "partie libre (pas de fin)"

    def _v_en_cours(self) -> bool:
        return self.repartie and not self.v_finie and self._v_mode() != "libre" and self.v_t0 is not None \
            and self.v_pause is None

    def _v_salles_gagnees(self) -> int:
        return max(0, len(self.v_salles) - self.v_depart)

    # --- événements (appelés par hanteurs.Partie) ------------------------------------------------------------
    def victoire_state(self, pid: int, zone, now: float) -> None:
        """State d'un joueur : salle traversée par un Link (mode course)."""
        if not self._v_en_cours() or self._v_mode() != "course" or not isinstance(zone, str):
            return
        if zone in self.v_salles or len(self.v_salles) >= SALLES_MAX or not self._v_marque(pid, now):
            return
        self.v_salles.add(zone)
        n, cible = self._v_salles_gagnees(), self.cfg["victoire_salles"]
        if n >= cible:
            self._v_fin(CAMP_LINK, f"{cible} salles traversées", now)
        else:
            self.t_diffusion = float("-inf")

    def victoire_ko(self, victime: int, hanteur: int | None, now: float) -> None:
        """Un Link est K.O. (``hanteur`` : le Hanteur crédité, ou None)."""
        if self.v_vague.pop(victime, 0.0) >= now:
            return                                 # mort par deathlink : déjà comptée (celle qui a lancé la vague)
        if not self._v_en_cours():
            return
        m = self._v_mode()
        self.v_ko += 1
        if m == "score" and hanteur is not None:
            self._v_score(hanteur, SCORE_KO, now)
        elif m == "survie" and self.v_ko >= self.cfg["victoire_ko"]:
            self._v_fin(CAMP_HANTEUR, f"{self.v_ko} K.O.", now)

    def victoire_vague(self, ids, now: float | None = None) -> None:
        """Deathlink : le serveur vient d'envoyer « mort » aux Link ``ids``. Leur prochaine mort (dans
        VAGUE_FENETRE) est la même mort que celle qui a lancé la vague : un seul K.O., pas un par Link."""
        now = self.clock() if now is None else now
        self.v_vague = {p: t for p, t in self.v_vague.items() if t >= now}
        for pid in ids:
            if self.role(pid) == CAMP_LINK:
                self.v_vague[pid] = now + VAGUE_FENETRE

    def victoire_degat(self, hanteur: int, quarts: int, now: float) -> None:
        """Dégât attribué à un Hanteur (mode score)."""
        if self._v_en_cours() and self._v_mode() == "score" and quarts > 0:
            self._v_score(hanteur, SCORE_DEGAT * quarts, now)

    def _v_score(self, pid: int, n: float, now: float) -> None:
        self.v_scores[pid] = self.v_scores.get(pid, 0.0) + n
        if self.v_scores[pid] >= self.cfg["victoire_score"]:
            self._v_fin(None, f"{self.cfg['victoire_score']} points atteints", now, gagnants=[pid])

    def _victoire_tick(self, now: float) -> None:
        if not self.repartie or self.v_finie:      # partie finie (Ganon vaincu) : plus de manche ni de minuteur
            return
        m = self._v_mode()
        if m == "libre":
            if self.v_t0 is not None or self.v_pause is not None:   # règle coupée par l'hôte en cours de manche
                self.v_t0 = self.v_pause = None
                self.v_regle = "libre"
                self.t_diffusion = float("-inf")
            return
        if m != self.v_regle:                        # règle changée par l'hôte : la manche repart de zéro
            self.victoire_debut(now, "règle changée par l'hôte")
            return
        if self.v_pause is not None:
            if now >= self.v_pause:
                self.victoire_debut(now, "nouvelle manche")
            return
        if self.v_t0 is None:
            self.victoire_debut(now)
            return
        if m == "score" and self.v_t_survie is not None:
            n = int((now - self.v_t_survie) / SCORE_SURVIE_S)
            if n > 0:
                self.v_t_survie += n * SCORE_SURVIE_S
                n = min(n, 20)                       # serveur gelé : jamais un saut de score
                for pid in self._liens():
                    v = self.vie.get(pid)
                    if self._v_marque(pid, now) and v is not None and v["v"] > 0:
                        self._v_score(pid, n, now)
                        if not self._v_en_cours():
                            return
        if now - self.v_t0 >= self.cfg["victoire_min"] * 60:
            if m == "survie":
                self._v_fin(CAMP_LINK, f"{self.cfg['victoire_min']} min tenues", now)
            elif m == "course":
                self._v_fin(CAMP_HANTEUR, "temps écoulé", now)
            else:
                best = max(self.v_scores.values(), default=0.0)
                self._v_fin(None, "temps écoulé", now,
                            gagnants=sorted(p for p, s in self.v_scores.items() if s >= best and best > 0))

    def _v_fin(self, camp, raison: str, now: float, gagnants=None) -> None:
        link_fin = self.link_fin()
        if link_fin:
            camp, gagnants = CAMP_LINK, self._liens()      # quelle que soit la règle : ceux qui sont Link à la fin
        elif camp == CAMP_LINK:
            gagnants = self._liens()
        elif camp == CAMP_HANTEUR:
            gagnants = self._hanteurs()
        gagnants = [p for p in (gagnants or []) if p in self.noms]
        noms = [self.noms[p] for p in gagnants]
        self.v_dernier = {"manche": self.v_manche, "mode": self._v_mode(), "camp": camp, "gagnants": noms,
                          "raison": raison, "scores": self._v_scores_nommes()}
        if link_fin:
            self.v_dernier.update(link_fin=1, classement=self._v_classement(gagnants))
        self.v_pause = now + PAUSE
        self.n_manches_finies += 1
        self.t_diffusion = float("-inf")
        qui = {CAMP_LINK: "les Link", CAMP_HANTEUR: "les Hanteurs"}.get(camp) or (", ".join(noms) or "personne")
        self.log(f"Hanteurs : manche {self.v_manche} ({NOMS[self._v_mode()]}) gagnée par {qui} ({raison})")
        for pid in sorted(self.noms):
            self.sortie.append((pid, dict(self.v_dernier, type="hfin", gagne=pid in gagnants, pause=PAUSE)))

    def victoire_finale(self, gagnants: list[int], raison: str, now: float) -> None:
        """Fin de la PARTIE (pas d'une manche) : Ganon est vaincu, gagnent ceux qui sont Link à cet instant. Même
        message ``hfin`` qu'une fin de manche (un ancien client l'affiche tel quel), avec ``finale`` et sans pause :
        aucune manche ne repart seule (l'hôte relance par une nouvelle répartition)."""
        noms = [self.noms[p] for p in gagnants if p in self.noms]
        self.v_finie = True
        self.v_dernier = {"manche": self.v_manche, "mode": self._v_mode(), "camp": CAMP_LINK, "gagnants": noms,
                          "raison": raison, "scores": self._v_scores_nommes(), "finale": 1, "link_fin": 1,
                          "classement": self._v_classement(gagnants)}
        self.v_t0 = self.v_pause = None
        self.n_manches_finies += 1
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : partie finie, {raison} : gagnent {', '.join(noms) or 'personne'} (Link à la fin)")
        for pid in sorted(self.noms):
            self.sortie.append((pid, dict(self.v_dernier, type="hfin", gagne=pid in gagnants, pause=0)))

    def _v_scores_nommes(self) -> dict:
        return {self.noms[p]: int(s) for p, s in sorted(self.v_scores.items()) if p in self.noms}

    def vue_victoire(self, now: float) -> dict:
        """Champs ajoutés à l'état public (lanceur, clients)."""
        m = self._v_mode()
        v = {"mode": m, "manche": self.v_manche, "dernier": self.v_dernier,
             "vainqueurs": "link_fin" if self.link_fin() else "regle"}
        if self.v_finie:
            v["finie"] = 1                         # partie finie (Ganon vaincu) : plus de manche
        if m != "libre" and not self.v_finie:
            c = self.cfg
            v.update(min=c["victoire_min"], ko_max=c["victoire_ko"], salles_max=c["victoire_salles"],
                     score_max=c["victoire_score"], regle=self.regle())
            if self._v_en_cours():
                v.update(reste=max(0, int(c["victoire_min"] * 60 - (now - self.v_t0))), ko=self.v_ko,
                         salles=self._v_salles_gagnees(), scores=self._v_scores_nommes())
            elif self.v_pause is not None:
                v["pause"] = max(0, int(self.v_pause - now))
        return {"victoire": v}


# --- côté client (wwmp/hanteurs.HanteurSync) ------------------------------------------------------------------
def _n(v, lo: int, hi: int):
    return max(lo, min(hi, int(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def lire(v) -> dict:
    """Champ ``victoire`` de l'état public reçu du serveur, nettoyé (types et bornes ; inconnu : {})."""
    if not isinstance(v, dict) or v.get("mode") not in MODES:
        return {}
    out = {"mode": v["mode"], "manche": _n(v.get("manche"), 0, 10 ** 6) or 0,
           "finie": int(v.get("finie") in (1, True)),
           "vainqueurs": "link_fin" if v.get("vainqueurs") == "link_fin" else "regle"}
    for champ, hi in (("min", 120), ("ko_max", 50), ("salles_max", 60), ("score_max", 9999), ("reste", 7200),
                      ("ko", 10 ** 4), ("salles", SALLES_MAX), ("pause", 3600)):
        n = _n(v.get(champ), 0, hi)
        if n is not None:
            out[champ] = n
    if isinstance(v.get("regle"), str):
        out["regle"] = v["regle"][:160]
    out["scores"] = _scores(v.get("scores"))
    d = v.get("dernier")
    if isinstance(d, dict) and d.get("mode") in MODES:
        out["dernier"] = {"manche": _n(d.get("manche"), 0, 10 ** 6) or 0, "mode": d["mode"],
                          "camp": d.get("camp") if d.get("camp") in (CAMP_LINK, CAMP_HANTEUR) else None,
                          "gagnants": [str(g)[:24] for g in (d.get("gagnants") or [])[:8]]
                          if isinstance(d.get("gagnants"), list) else [],
                          "raison": str(d.get("raison") or "")[:80], "scores": _scores(d.get("scores")),
                          "finale": int(d.get("finale") in (1, True)),
                          "link_fin": int(d.get("link_fin") in (1, True)),
                          "classement": _classement(d.get("classement"))}
    else:
        out["dernier"] = None
    return out


def _scores(s) -> dict:
    if not isinstance(s, dict):
        return {}
    out = {}
    for k, n in list(s.items())[:8]:
        n = _n(n, 0, 10 ** 7)
        if n is not None:
            out[str(k)[:24]] = n
    return out


def _classement(c) -> list[dict]:
    """Tableau de fin reçu du serveur, nettoyé (au plus CLASSEMENT_MAX lignes ; inconnu : [])."""
    if not isinstance(c, list):
        return []
    out = []
    for x in c[:CLASSEMENT_MAX]:
        if not isinstance(x, dict):
            continue
        out.append({"nom": str(x.get("nom") or "?")[:24], "link": int(x.get("link") in (1, True)),
                    "score": _n(x.get("score"), 0, 10 ** 7) or 0, "gagne": int(x.get("gagne") in (1, True))})
    return out


def tableau(classement) -> str:
    """Tableau de fin en une ligne : « Mathis (Link, 120) · Doryan (Hanteur, 300) »."""
    return " · ".join(f"{x['nom']} ({'Link' if x['link'] else 'Hanteur'}, {x['score']})"
                      for x in _classement(classement))


def vieillir(v: dict, dt: float) -> dict:
    """``v`` (de ``lire``) vu ``dt`` s plus tard : les comptes à rebours continuent entre deux diffusions."""
    if not v:
        return {}
    out = dict(v)
    dt = max(0, int(dt)) if isinstance(dt, (int, float)) and math.isfinite(dt) else 0
    for champ in ("reste", "pause"):
        if champ in out:
            out[champ] = max(0, out[champ] - dt)
    return out


def annonce(msg: dict) -> str:
    """Message ``hfin`` -> la phrase affichée au joueur."""
    camp, g = msg.get("camp"), msg.get("gagnants")
    noms = ", ".join(str(n)[:24] for n in g[:8]) if isinstance(g, list) else ""
    qui = {CAMP_LINK: "les Link gagnent", CAMP_HANTEUR: "les Hanteurs gagnent"}.get(camp) \
        or (f"{noms} gagne" if noms else "personne ne gagne")
    toi = " : TU AS GAGNÉ" if msg.get("gagne") in (1, True) else ""
    if msg.get("finale") in (1, True):             # fin de la partie « comme dans Crawl » (Ganon vaincu)
        return (f"FIN DE LA PARTIE : {str(msg.get('raison') or '?')[:80]} ; gagnent ceux qui sont Link : "
                f"{noms or 'personne'}{toi}")[:200]
    pause = _n(msg.get("pause"), 0, 3600)
    if msg.get("link_fin") in (1, True):           # réglage « vainqueurs : ceux qui sont Link à la fin »
        return (f"fin de la manche {_n(msg.get('manche'), 0, 10 ** 6) or 0} ({str(msg.get('raison') or '?')[:60]}) : "
                f"gagnent ceux qui sont Link : {noms or 'personne'}{toi}"
                + (f" ; nouvelle manche dans {pause} s" if pause else ""))[:200]
    return (f"fin de la manche {_n(msg.get('manche'), 0, 10 ** 6) or 0} : {qui} ({str(msg.get('raison') or '?')[:80]})"
            f"{toi}" + (f" ; nouvelle manche dans {pause} s" if pause else ""))[:200]
