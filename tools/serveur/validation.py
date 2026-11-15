"""Validation côté serveur (lot S2 de docs/etudes/serveur_dedie_jeu.md §7 B et §9) : journal d'anomalies.

Le serveur ne simule rien : il croit ses clients. Ce module regarde ce qui a une vérité simple et le note dans un
JOURNAL D'ANOMALIES que l'administrateur lit dans le lanceur (Héberger › « Sur mon serveur dédié » › « Administrer le
serveur ») et que le journal du serveur montre aussi (« ANOMALIE … », au plus une ligne par joueur et par genre toutes
les LOG_PAS s). Réglage ``validation`` (``--validation`` du serveur, clé de config.json, à chaud) :

* « journal » (DÉFAUT) : tout passe, les anomalies sont seulement notées ;
* « refus » (à n'allumer qu'après un essai à plusieurs PC) : un coup jugé impossible n'est pas relayé, un message au-delà
  des débits n'est pas traité ; la vitesse et les sauts restent notés seulement (refuser un ``state`` figerait le
  fantôme sans rien empêcher) ;
* « off » : rien n'est regardé.

Ce qui est regardé, avec des seuils LARGES (Internet, jeu à 30 images/s ; un seuil trop strict refuserait des coups
justes) :

1. **Vitesse des Link** (``state``) : déplacement HORIZONTAL (les chutes ne comptent pas) mesuré sur au moins
   VIT_FENETRE s, daté par l'image du jeu de l'émetteur (``fr``) ou son heure (``ts``), sinon par l'heure d'arrivée
   (avec MIN_TICK s au moins entre deux relevés : une rafale après un trou du réseau n'est pas une vitesse). Plafond
   VIT_PIED à pied, VIT_MER en bateau ou dans la stage ``sea``. Un déplacement de plus de SAUT u d'un relevé au
   suivant est un **saut** (téléportation), jugé à part.
2. **Téléportations légitimes, jamais notées** (``tests/test_validation.py`` les rejoue une à une) :
   changement de scène ou de salle (porte, chargement, Game Over, rejoindre), passage sans zone (chargement) ;
   cinématique (``busy`` : Ballade des vents, cyclone de Zéphos, coffres, portes, scènes) avant ou jusqu'à GRACE s
   après ; salle rechargée (``ground_req`` du client : chute dans le vide, Game Over) ou vie qui change (chute qui
   blesse, fée, Game Over) jusqu'à GRACE s avant ou après ; retour à un point de reprise (point d'entrée dans la zone,
   arrivée d'un saut déjà expliqué ; client ancien sans ``ground_req``) ; grappin, bateau et voile rapide (vitesse
   dans les plafonds) ; mode Hanteurs : Hanteur, Link possédé ou clone de la potion jamais mesurés, et GRACE_ROLE s
   après un changement de rôle (échange de Crawl, fin de potion). Un saut ou une vitesse attend GRACE s une
   explication avant d'être noté.
3. **Distance des coups** : ``hit`` (coup sur une marionnette) plus loin que HIT_DIST u (HIT_DIST_MER en mer) du
   monstre d'après le dernier instantané de l'autorité ; ``player_hit`` (coup reçu par un fantôme) dont le point
   d'impact est à plus de PH_DIST u (PH_DIST_MER en mer) de la dernière position de la victime. Positions de plus de
   AGE_MAX s, monstre absent de l'instantané : pas de jugement.
4. **Débits par message** : messages par seconde et par type (DEBITS, sinon DEBIT_DEFAUT), en tout (DEBIT_TOTAL) et
   octets (OCTETS_MAX), moyennés sur FENETRE_DEBIT s. Jamais refusés : JAMAIS_REFUSES (présentation, ping, lobby,
   administration, copie de sauvegarde, mises à jour).

Le journal vit en mémoire (JOURNAL_MAX entrées, même genre du même joueur regroupé pendant REGROUPE s) ; un redémarrage
le vide. Python pur, sans dépendance (serveur dédié autonome).
"""
from __future__ import annotations

import math
import time
from collections import deque

MODES = ("journal", "refus", "off")
DEFAUT = "journal"

# vitesse des Link
IMAGE = 1.0 / 30.0
MIN_TICK = 0.035          # s : deux relevés d'un client récent ne sont jamais plus rapprochés (commun/latence.py)
VIT_FENETRE = 1.0         # s : vitesse mesurée sur au moins…
VIT_PIED = 2500.0         # u/s à pied (course ~500 u/s ; grappin, roulades, vent et feuille compris sur 1 s)
VIT_MER = 7000.0          # u/s en bateau ou en mer (voile rapide comprise)
SAUT = 1500.0             # u : d'un relevé au suivant au-delà (et plus vite que le plafond) = saut
GRACE = 3.0               # s : autour d'une explication (cinématique, recharge, vie, zone), rien n'est noté
GRACE_ROLE = 5.0          # s : après un changement de rôle du mode Hanteurs
REPRISE = 500.0           # u : autour d'un point de reprise, un saut est un retour (chute dans le vide)
REPRISES_MAX = 8
# coups
HIT_DIST = 6000.0         # u : coup sur une marionnette (flèches, grappin, boomerang, bombes compris)
HIT_DIST_MER = 12000.0
PH_DIST = 1500.0          # u : impact d'un coup reçu par un fantôme / dernière position de la victime
PH_DIST_MER = 6000.0
AGE_MAX = 2.0             # s : positions plus vieilles : pas de jugement
# débits (messages par seconde, moyenne sur FENETRE_DEBIT s ; client normal : state 20 à 30/s, enemies et epos 10/s)
FENETRE_DEBIT = 2.0
DEBITS = {"state": 60, "enemies": 60, "epos": 60, "ping": 20, "hit": 30, "player_hit": 30, "saisie": 30,
          "pvp": 30, "lobby": 20, "trackeur": 20}
DEBIT_DEFAUT = 100
DEBIT_TOTAL = 300
OCTETS_MAX = 2 * 1024 * 1024
JAMAIS_REFUSES = frozenset({"hello", "ping", "lobby", "admin", "admin_defi", "sauvegarde_hote", "rejoindre_req",
                            "rejoindre_fin", "rejoindre_err", "maj_info", "maj_part", "maj_req", "bye"})
# journal
JOURNAL_MAX = 200
REGROUPE = 10.0           # s : même genre du même joueur : une seule entrée (compte « n »)
LOG_PAS = 10.0            # s : au plus une ligne du journal du serveur par joueur et par genre
GENRES = {"vitesse": "vitesse impossible", "saut": "téléportation inexpliquée", "coup_loin": "coup de trop loin",
          "coup_fantome_loin": "coup reçu loin du joueur", "debit": "trop de messages", "octets": "trop d'octets"}


def _f3(v):
    """Trois nombres finis (un entier démesuré, NaN ou l'infini : None)."""
    if not (isinstance(v, (list, tuple)) and len(v) == 3):
        return None
    try:
        out = tuple(float(x) for x in v if isinstance(x, (int, float)) and not isinstance(x, bool))
    except OverflowError:
        return None
    return out if len(out) == 3 and all(math.isfinite(x) and abs(x) < 1e9 for x in out) else None


def _ent(v, hi: int = 1 << 53):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < hi else None


def _xz(a, b) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


class Releve:
    __slots__ = ("t", "fr", "ts", "pos", "i")

    def __init__(self, t, fr, ts, pos, i):
        self.t, self.fr, self.ts, self.pos, self.i = t, fr, ts, pos, i


def ecart(a: Releve, b: Releve) -> float:
    """Temps écoulé CHEZ L'ÉMETTEUR entre deux relevés : image du jeu et heure du relevé (le plus grand des deux), sinon
    heure d'arrivée bornée par le nombre de relevés (MIN_TICK chacun)."""
    cand = []
    if a.fr is not None and b.fr is not None and 0 < b.fr - a.fr < 30 * 600:
        cand.append((b.fr - a.fr) * IMAGE)
    if a.ts is not None and b.ts is not None and 0 < b.ts - a.ts < 600000:
        cand.append((b.ts - a.ts) / 1000.0)
    if cand:
        return max(max(cand), IMAGE)
    return max(b.t - a.t, (b.i - a.i) * MIN_TICK, IMAGE)


class Suivi:
    """État d'un joueur (vitesse, sauts, débits)."""

    def __init__(self):
        self.zone: str | None = None
        self.dernier: Releve | None = None
        self.fenetre: deque[Releve] = deque()
        self.n = 0
        self.grace = float("-inf")          # rien n'est noté jusqu'à…
        self.explique = float("-inf")       # dernière explication (cinématique, recharge, vie, zone, rôle)
        self.reprises: list[tuple] = []
        self.attente: list[dict] = []       # sauts / vitesses qui attendent GRACE s une explication
        self.vie = None
        self.exempt = False
        self.mer = False
        self.pos = None                     # dernière position (coups)
        self.t_pos = float("-inf")
        self.msgs: dict[str, deque] = {}    # type -> heures (débits)
        self.tous: deque = deque()          # (heure, octets)
        self.octets = 0

    def coupure(self) -> None:
        self.dernier = None
        self.fenetre.clear()

    def expliquer(self, now: float, grace: float = GRACE) -> None:
        """Une explication à ``now`` : ce qui attend depuis moins de GRACE s est oublié (sauts : leur arrivée devient
        un point de reprise), rien n'est noté pendant ``grace`` s."""
        self.explique = now
        self.grace = max(self.grace, now + grace)
        garde = []
        for a in self.attente:
            if now - a["t"] <= GRACE:
                if a["genre"] == "saut":
                    self.reprise(a["arrivee"])
            else:
                garde.append(a)
        self.attente = garde
        self.coupure()

    def reprise(self, pos) -> None:
        if pos is None:
            return
        self.reprises.append(pos)
        del self.reprises[:-REPRISES_MAX]


class Validation:
    def __init__(self, mode: str = DEFAUT, log=print, horloge=None, mur=None):
        self.mode = mode if mode in MODES else DEFAUT
        self.log = log
        self.horloge = horloge or (lambda: time.monotonic())     # lu à chaque appel (horloge simulée des tests)
        self.mur = mur or (lambda: time.time())
        self.suivis: dict[int, Suivi] = {}
        self.journal: deque[dict] = deque(maxlen=JOURNAL_MAX)
        self.total = 0
        self.par_genre: dict[str, int] = {}
        self.refuses = 0
        self.log_t: dict[tuple, float] = {}
        self.derniers: dict[tuple, dict] = {}   # (joueur, genre) -> dernière entrée (regroupement sans parcours)
        self.seq = 0

    # --- réglage ---------------------------------------------------------------------------------------------
    @property
    def actif(self) -> bool:
        return self.mode != "off"

    @property
    def refus(self) -> bool:
        return self.mode == "refus"

    def regler(self, mode) -> bool:
        if mode not in MODES or mode == self.mode:
            return False
        self.mode = mode
        if mode == "off":
            self.suivis.clear()
        return True

    def suivi(self, pid: int) -> Suivi:
        s = self.suivis.get(pid)
        if s is None:
            s = self.suivis[pid] = Suivi()
        return s

    def depart(self, pid: int) -> None:
        self.suivis.pop(pid, None)
        for k in [k for k in self.log_t if k[0] == pid]:
            del self.log_t[k]

    # --- journal ---------------------------------------------------------------------------------------------
    def noter(self, pid: int, nom: str, genre: str, detail: str, refuse: bool = False, now: float | None = None) -> None:
        now = self.horloge() if now is None else now
        self.total += 1
        self.par_genre[genre] = self.par_genre.get(genre, 0) + 1
        if refuse:
            self.refuses += 1
        dernier = self.derniers.get((pid, genre))
        if dernier is not None and (not self.journal or dernier["_seq"] < self.journal[0]["_seq"]):
            dernier = None                      # sortie du journal (plus de JOURNAL_MAX entrées depuis)
        if dernier is not None and now - dernier["_t"] < REGROUPE:
            dernier["n"] += 1
            dernier["detail"] = detail
            dernier["refuse"] = dernier["refuse"] or refuse
            dernier["_t"] = now
            dernier["h"] = time.strftime("%H:%M:%S", time.localtime(self.mur()))
        else:
            self.seq += 1
            e = {"h": time.strftime("%H:%M:%S", time.localtime(self.mur())), "id": pid, "joueur": str(nom)[:24],
                 "genre": genre, "detail": detail[:200], "n": 1, "refuse": bool(refuse), "_t": now, "_seq": self.seq}
            self.journal.append(e)
            self.derniers[(pid, genre)] = e
            if len(self.derniers) > 4 * JOURNAL_MAX:
                debut = self.journal[0]["_seq"]
                self.derniers = {k: v for k, v in self.derniers.items() if v["_seq"] >= debut}
        cle = (pid, genre)
        if now - self.log_t.get(cle, float("-inf")) >= LOG_PAS:
            self.log_t[cle] = now
            quoi = "REFUSÉ" if refuse else "journal seulement" if self.mode == "journal" else "noté"
            self.log(f"ANOMALIE ({quoi}) {nom} : {GENRES.get(genre, genre)} : {detail}")

    def vue(self, n: int = 50) -> dict:
        """Pour l'administrateur (« lire ») : réglage, compteurs, dernières entrées (la plus récente d'abord)."""
        lst = [{k: v for k, v in e.items() if not k.startswith("_")} for e in list(self.journal)[-n:]][::-1]
        return {"mode": self.mode, "total": self.total, "refuses": self.refuses, "par_genre": dict(self.par_genre),
                "liste": lst}

    def effacer(self) -> None:
        self.journal.clear()
        self.derniers.clear()
        self.total = self.refuses = 0
        self.par_genre.clear()

    # --- vitesse et sauts des Link --------------------------------------------------------------------------
    def etat(self, p, msg: dict, zone: str | None, busy: bool, exempt: bool = False, now: float | None = None) -> None:
        """``state`` reçu de ``p`` (zone déjà calculée par le serveur). Ne refuse jamais rien."""
        if not self.actif:
            return
        now = self.horloge() if now is None else now
        s = self.suivi(p.id)
        pos = _f3(msg.get("pos"))
        vie = msg.get("life") if isinstance(msg.get("life"), int) and not isinstance(msg.get("life"), bool) else None
        s.mer = isinstance(msg.get("ship"), dict) or (isinstance(zone, str) and zone.startswith("sea:"))
        if pos is not None:
            s.pos, s.t_pos = pos, now
        if exempt != s.exempt:
            s.exempt = exempt
            s.expliquer(now, GRACE_ROLE)            # changement de rôle (Hanteurs) : téléportation possible
        if zone != s.zone:
            s.zone = zone
            s.reprises = []
            s.expliquer(now)                         # changement de scène, de salle, chargement
            if zone is not None and pos is not None:
                s.reprise(pos)                       # point d'entrée : là où une chute ramène
        if vie is not None and s.vie is not None and vie != s.vie:
            s.expliquer(now)                         # vie qui change : chute qui blesse, fée, Game Over
        s.vie = vie if vie is not None else s.vie
        if exempt or busy:
            s.expliquer(now)                         # Hanteur, possédé, clone ; cinématique
            self.juger(p, s, now)
            return
        if zone is None or pos is None:
            self.juger(p, s, now)
            return
        s.n += 1
        r = Releve(now, _ent(msg.get("fr"), 1 << 32), _ent(msg.get("ts")), pos, s.n)
        prec = s.dernier
        s.dernier = r
        plafond = VIT_MER if s.mer else VIT_PIED
        if prec is None or now < s.grace:
            s.fenetre.clear()
            s.fenetre.append(r)
            self.juger(p, s, now)
            return
        d = _xz(prec.pos, pos)
        if d > SAUT and d > plafond * ecart(prec, r):
            # saut : un retour à un point de reprise est une chute dans le vide (client sans ground_req)
            if not any(_xz(pos, q) <= REPRISE for q in s.reprises):
                s.attente.append({"genre": "saut", "t": now, "arrivee": pos,
                                  "detail": f"{d:.0f} u d'un coup dans {zone} (vers {pos[0]:.0f}, {pos[2]:.0f})"})
            s.fenetre.clear()
            s.fenetre.append(r)
            self.juger(p, s, now)
            return
        s.fenetre.append(r)
        while len(s.fenetre) > 2 and now - s.fenetre[1].t >= VIT_FENETRE:
            s.fenetre.popleft()
        a = s.fenetre[0]
        if now - a.t >= VIT_FENETRE:
            dt = ecart(a, r)
            v = _xz(a.pos, pos) / dt
            if v > plafond:
                s.attente.append({"genre": "vitesse", "t": now, "arrivee": None,
                                  "detail": f"{v:.0f} u/s sur {dt:.1f} s dans {zone} (plafond {plafond:.0f} u/s"
                                            f"{' en mer' if s.mer else ' à pied'})"})
                s.fenetre.clear()
                s.fenetre.append(r)
        self.juger(p, s, now)

    def juger(self, p, s: Suivi, now: float) -> None:
        """Ce qui attend depuis plus de GRACE s sans explication est noté."""
        if not s.attente:
            return
        garde = []
        for a in s.attente:
            if now - a["t"] > GRACE:
                self.noter(p.id, p.name, a["genre"], a["detail"], now=now)
            else:
                garde.append(a)
        s.attente = garde

    def recharge(self, p, now: float | None = None) -> None:
        """``ground_req`` : salle rechargée sans changement de zone (chute dans le vide, Game Over)."""
        if self.actif:
            self.suivi(p.id).expliquer(self.horloge() if now is None else now)

    # --- coups ---------------------------------------------------------------------------------------------
    def position(self, pid: int, now: float):
        s = self.suivis.get(pid)
        if s is None or s.pos is None or now - s.t_pos > AGE_MAX:
            return None, False
        return s.pos, s.mer

    def coup(self, p, zone: str, key, snapshot, exempt: bool = False, now: float | None = None) -> bool:
        """``hit`` de ``p`` sur la marionnette ``key`` : False si refusé (mode « refus », coup de trop loin)."""
        if not self.actif or exempt or not isinstance(key, str):
            return True
        now = self.horloge() if now is None else now
        pos, mer = self.position(p.id, now)
        if pos is None or snapshot is None:
            return True
        t, snap = snapshot
        if now - t > AGE_MAX or not isinstance(snap, dict):
            return True
        e = next((x for x in snap.get("list") or [] if isinstance(x, dict) and x.get("key") == key), None)
        cible = _f3(e.get("pos")) if e is not None else None
        if cible is None:
            return True
        d = math.dist(pos, cible)
        plafond = HIT_DIST_MER if mer or zone.startswith("sea:") else HIT_DIST
        if d <= plafond:
            return True
        self.noter(p.id, p.name, "coup_loin", f"coup sur {key[:40]} à {d:.0f} u (plafond {plafond:.0f} u) dans {zone}",
                   refuse=self.refus, now=now)
        return not self.refus

    def coup_fantome(self, p, victime, rec: dict, now: float | None = None) -> bool:
        """``player_hit`` de l'autorité ``p`` sur le fantôme de ``victime`` : False si refusé (impact trop loin)."""
        if not self.actif:
            return True
        now = self.horloge() if now is None else now
        pos, mer = self.position(victime.id, now)
        impact = _f3(rec.get("pos"))
        if pos is None or impact is None:
            return True
        d = math.dist(pos, impact)
        plafond = PH_DIST_MER if mer else PH_DIST
        if d <= plafond:
            return True
        self.noter(p.id, p.name, "coup_fantome_loin",
                   f"{rec.get('key', '?')!s:.40} touche {victime.name} à {d:.0f} u de sa position (plafond {plafond:.0f} u)",
                   refuse=self.refus, now=now)
        return not self.refus

    # --- débits ---------------------------------------------------------------------------------------------
    def debit(self, p, typ, octets: int = 0, now: float | None = None) -> bool:
        """Un message reçu de ``p`` : False s'il ne doit pas être traité (mode « refus », au-delà des débits)."""
        if not self.actif:
            return True
        now = self.horloge() if now is None else now
        s = self.suivi(p.id)
        typ = typ if isinstance(typ, str) else "?"
        typ = typ[:24]
        lim = now - FENETRE_DEBIT
        tous = s.tous
        while tous and tous[0][0] < lim:
            s.octets -= tous.popleft()[1]
        q = s.msgs.get(typ)
        if q is None:
            if len(s.msgs) >= 64:
                s.msgs = {k: v for k, v in s.msgs.items() if v and v[-1] >= lim}
            q = s.msgs[typ] = deque()
        while q and q[0] < lim:
            q.popleft()
        plafond = DEBITS.get(typ, DEBIT_DEFAUT) * FENETRE_DEBIT
        jamais = typ in JAMAIS_REFUSES
        genre = detail = None
        if len(q) >= plafond:
            genre, detail = "debit", f"plus de {plafond:.0f} messages « {typ} » en {FENETRE_DEBIT:.0f} s"
        elif len(tous) >= DEBIT_TOTAL * FENETRE_DEBIT:
            genre, detail = "debit", f"plus de {DEBIT_TOTAL * FENETRE_DEBIT:.0f} messages en {FENETRE_DEBIT:.0f} s"
        elif s.octets + octets > OCTETS_MAX * FENETRE_DEBIT:
            genre, detail = "octets", f"plus de {OCTETS_MAX * FENETRE_DEBIT / 1048576:.0f} Mo en {FENETRE_DEBIT:.0f} s"
        refuse = genre is not None and self.refus and not jamais
        if genre is not None:
            self.noter(p.id, p.name, genre, detail, refuse=refuse, now=now)
        if refuse:
            return False
        q.append(now)
        tous.append((now, octets))
        s.octets += octets
        return True
