"""Monde vivant, suite (synchro v4 ; étude docs/etudes/sync3_monde_vivant.md §4 et §5, la section
« Vérification » de l'étude prime) : logique testable sans Cemu.

Trois choses, toutes en écritures Python (aucune commande de la boîte aux lettres : pack v17 suffisant) :

  Position des PNJ qui se déplacent (--share-pnj, activé par --coop-donjon ; --no-pnj) :
    l'AUTORITÉ de la zone envoie deux fois par seconde « pnj » {zone, list: [{k, p, a, v, ...}]} pour
    les PNJ suivis qui bougent (PNJ_MAX au plus, les plus proches d'elle d'abord). Chez les autres, le
    PNJ de même clé (proc, salle, paramètres, position du fichier de stage) est RAMENÉ vers la position
    reçue par petits pas (PULL_MAX unités par passe à 20 Hz : il glisse, il ne saute pas), x et z
    seulement (la gravité et les murs du jeu continuent de s'appliquer : old.pos -> current.pos).
    Seule exception : le recalage d'un coup, hors de vue (voir FAR / LOIN), pose aussi la hauteur.
    Jamais d'écriture : pendant un événement chez nous (dialogue, cinématique, coffre), sur un PNJ qui
    a une commande d'événement, qui est en démo, porté, tenu par notre Link, ou dont notre Link est
    tout près (il va lui parler / le ramasser), ni salle pas stabilisée, ni écriture interdite.
    Classes suivies :
      373 daNpcPeople_c (habitants et marins de Mercantîle : Uo, Ub, Uw, Um, Sa, Ug) : en plus de la
        position, l'index et le sens du chemin (dNpc_PathRun_c, +0x875 / +0x876, bornés par le nombre
        de points du chemin LOCAL qui doit être le même que chez l'autorité) sont recopiés : le PNJ
        marche vers le même point (sans cela il repartirait vers le sien). Marche / arrêt : le
        minuteur natif m76E (+0x8E2) est mis à 1 -> le jeu lui-même passe de la marche à l'attente
        (executeWalk : m76E-- == 0 -> executeSetMode(0)) ou de l'attente à la marche (executeWait :
        m76E-- == 0 -> executeSetMode(3)) avec ses animations ; jamais l'octet de mode lui-même.
        Un minuteur à 0 (PNJ sans minuteur) n'est jamais touché.
      220 cochon (kb_class), 200 crabe (kn) : pas de chemin (errance) -> position seulement.
    Non suivis, dit clairement (étude §4) : mouettes (vol 3D piloté image par image), Piafs, Korogus,
    enfants, autres PNJ à chemin (l'offset de leur dNpc_PathRun_c n'a pas été vérifié classe par
    classe : sans l'index du chemin ils marcheraient à reculons).
  « Je le porte » / « je lui parle » (mêmes messages, drapeaux c / t, envoyés par N'IMPORTE QUEL
    joueur de la zone, autorité ou non ; le serveur ne relaie d'un non-autorité que ces entrées) :
      c : un cochon que je porte (statut CARRY) suit mon fantôme chez les autres (au sol : y n'est
          pas écrit) ; encore CLAIM_KEEP secondes après le lâcher (il retombe), puis l'autorité reprend.
      t : le PNJ auquel je parle (événement en mode dialogue, mPt1 / mPt2) s'arrête chez les autres à
          l'endroit où il est chez moi et y reste tant que dure le dialogue : on VOIT que ce PNJ est
          pris, sans que rien ne soit bloqué (chacun peut toujours lui parler chez soi).
    Tant qu'une telle entrée est fraîche (CLAIM_TTL), l'autorité n'envoie plus sa propre position
    pour ce PNJ.
  Compteur des mini-jeux (--share-minigames, déjà activé par --coop-donjon) : le compteur affiché du
    mini-jeu (play+0x5CEC, s16 mMiniGameRupee, lecture seule) part dans state.mgs pendant le mini-jeu ;
    à la fin, les autres lisent « X : mini-jeu terminé, compteur N » et le meilleur de la partie par
    mini-jeu est gardé (« record de la partie »). Rien n'est écrit.

Offsets HD [HD-ASM, relus dans cking.rpx par tests/test_pnj.py] :
  executeWalk 0x022C46D0 : addi r3, r31, 0x870 (mPathRun) ; lbz 0x875 (mIdx), lbz/stb 0x876 (mbDir),
    lbz 0x8FD / 0x8FE (joueur tout près), lbz 0x903 (mode : 0 attente, 1 dialogue, 2 marche,
    3 demi-tour…), lha/sth 0x8E2 (m76E) ; executeWalkInit 0x022C461C : sth 0x8E2, li r3, 2.
  mMiniGameRupee : lha/sth 0x5CEC(play) (0x0215F428, 0x02059610).
"""
from __future__ import annotations

import math
import struct
import threading
import time

from jeu import progress as prog
from synchro.mobiles import _int, _key, _num, _pos, _ptr, read_path, rkey

PROC_GENS, PROC_COCHON, PROC_CRABE = 373, 220, 200
# proc -> taille minimale du profil (HD) : rien n'est lu ni écrit au-delà
TAILLES = {PROC_GENS: 0x920, PROC_COCHON: 0xE2C, PROC_CRABE: 0x658}
PROCS = frozenset(TAILLES)
NOMS = {PROC_GENS: "habitant", PROC_COCHON: "cochon", PROC_CRABE: "crabe"}

OFF_PC_ID, OFF_PROC = 0x04, 0x08
OFF_EVT_CMD = 0xF8          # u16 eventInfo.mCommand (0 au repos)
OFF_STATUS = 0x2E0          # u32 ; 0x1000 démo, 0x2000 CARRY
STATUS_DEMO, STATUS_CARRY = 0x1000, 0x2000
OFF_POS = 0x314             # f32[3] current.pos
OFF_ANGLE_Y = 0x322         # s16 current.angle.y
OFF_SPEEDF = 0x370          # f32
HEAD = 0x374                # une lecture : en-tête .. speedF
LINK_GRAB_PTR = 0x65A4      # Link : acteur tenu / en cours de ramassage

# daNpcPeople_c (373) [HD-ASM executeWalk 0x022C46D0]
G_PATH = 0x870              # dPath* mPathRun.mPath
G_IDX = 0x875               # u8 mPathRun.mIdx
G_DIR = 0x876               # u8 mPathRun.mbDir (0 / 1)
G_TIMER = 0x8E2             # s16 m76E : images avant marche <-> attente (0 = pas de minuteur)
G_NEAR = 0x8FD              # u8 m789, u8 m78A : joueur tout près (le PNJ s'arrête de lui-même)
G_MODE = 0x903              # u8 m78F
G_LO, G_HI = G_PATH, G_MODE + 1
MODE_ATTENTE, MODE_PARLE, MODE_MARCHE, MODE_TOURNE = 0, 1, 2, 3

MINI_COMPTEUR = prog.SYM_PLAY + 0x5CEC      # s16 mMiniGameRupee
EVT_TALK = 1                                 # dEvt_control_c mMode : dialogue

FPS = 30
PNJ_MAX = 24                # entrées par message
CLAIM_MAX = 2               # entrées c / t acceptées d'un joueur qui n'est pas l'autorité
KEY_LEN = 64


def valid_entry(e) -> dict | None:
    """Une entrée « pnj » reçue du réseau : tout est borné, None si invalide."""
    if not isinstance(e, dict):
        return None
    k, p, a, v = _key(e.get("k")), _pos(e.get("p")), e.get("a"), e.get("v", 0.0)
    if k is None or len(k) > KEY_LEN or p is None or not (_int(a) and -32768 <= a <= 32767):
        return None
    if not (_num(v) and abs(v) < 100.0):
        return None
    try:
        proc = int(k.split(":", 1)[0])
    except ValueError:
        return None
    if proc not in PROCS:
        return None
    out = {"k": k, "p": p, "a": a, "v": float(v)}
    if e.get("c"):
        if proc != PROC_COCHON:
            return None
        out["c"] = 1
    if e.get("t"):
        out["t"] = 1
    if proc == PROC_GENS and "m" in e:
        m, i, d, n = e.get("m"), e.get("i"), e.get("d"), e.get("n")
        if not (_int(m) and 0 <= m <= 255):
            return None
        out["m"] = m
        if _int(n) and 1 <= n <= 64 and _int(i) and 0 <= i < n and _int(d) and 0 <= d <= 1:
            out.update(i=i, d=d, n=n)
    return out


def valid_pnj(msg) -> list[dict] | None:
    """Message « pnj » (client -> serveur et relais) : liste des entrées valides, None si aucune."""
    lst = msg.get("list") if isinstance(msg, dict) else None
    if not isinstance(lst, list):
        return None
    out, vus = [], set()
    for x in lst[:PNJ_MAX]:
        e = valid_entry(x)
        if e and e["k"] not in vus:
            vus.add(e["k"])
            out.append(e)
    return out or None


def filtre_serveur(lst: list[dict], autorite: bool) -> list[dict]:
    """Ce que le serveur relaie : tout de l'autorité ; d'un autre joueur, ses seules entrées c / t."""
    if autorite:
        return lst
    return [e for e in lst if e.get("c") or e.get("t")][:CLAIM_MAX]


def read_pnj(mem, base: int, proc: int, size: int) -> dict | None:
    """État d'un PNJ suivi (lecture seule) ; None si l'acteur n'est plus celui-là ou illisible."""
    if not _ptr(base) or proc not in PROCS or size < TAILLES[proc]:
        return None
    raw = mem.try_read(base, HEAD)
    if raw is None or len(raw) < HEAD:
        return None
    bs, pid = struct.unpack_from(">II", raw, 0)
    if bs == 0 or struct.unpack_from(">h", raw, OFF_PROC)[0] != proc:
        return None
    pos = struct.unpack_from(">3f", raw, OFF_POS)
    sp = struct.unpack_from(">f", raw, OFF_SPEEDF)[0]
    if not all(math.isfinite(v) and abs(v) < 1e5 for v in (*pos, sp)):
        return None
    o = {"base": base, "pid": pid, "proc": proc, "pos": pos, "sp": sp,
         "a": struct.unpack_from(">h", raw, OFF_ANGLE_Y)[0],
         "evt": struct.unpack_from(">H", raw, OFF_EVT_CMD)[0],
         "status": struct.unpack_from(">I", raw, OFF_STATUS)[0]}
    if proc == PROC_GENS:
        g = mem.try_read(base + G_LO, G_HI - G_LO)
        if g is None or len(g) < G_HI - G_LO:
            return None
        o.update(ppd=struct.unpack_from(">I", g, 0)[0], i=g[G_IDX - G_LO], d=g[G_DIR - G_LO],
                 timer=struct.unpack_from(">h", g, G_TIMER - G_LO)[0],
                 near=bool(g[G_NEAR - G_LO] or g[G_NEAR + 1 - G_LO]), m=g[G_MODE - G_LO])
    return o


def lire_compteur(mem) -> int | None:
    """Compteur affiché du mini-jeu en cours (lecture seule), borné 0..9999 ; None si illisible."""
    raw = mem.try_read(MINI_COMPTEUR, 2) if mem is not None else None
    if raw is None or len(raw) < 2:
        return None
    return max(0, min(9999, struct.unpack(">h", raw)[0]))


class PnjSync:
    """Partie client (thread de travail : step ; thread réseau : on_message)."""

    ROOM_SETTLE = 2.0       # s dans la salle avant toute écriture et tout envoi
    PERIOD = 0.5            # s entre deux envois de l'autorité
    CLAIM_PERIOD = 0.25     # s entre deux envois « je le porte / je lui parle »
    CLAIM_KEEP = 1.5        # s : encore « porté » après le lâcher (le cochon retombe)
    CLAIM_TTL = 1.5         # s : entrée c / t reçue encore valable
    TTL = 2.5               # s : position reçue de l'autorité encore valable
    ACTIVE = 3.0            # s : un PNJ arrêté reste envoyé (pour que l'arrêt arrive)
    MOVE_EPS = 1.0          # u : déplacement entre deux envois qui compte comme « bouge »
    DEAD = 30.0             # u : écart toléré (rien n'est écrit en dessous)
    DEAD_CARRY = 15.0
    PULL_MAX = 14.0         # u par passe (20 Hz -> 280 u/s)
    PULL_CARRY = 30.0       # u par passe pour un cochon porté (Link court à ~510 u/s)
    PULL_GAIN = 0.2         # part de l'écart rattrapée par passe
    FAR = 1200.0            # u : au-delà, recalage d'un coup seulement si notre Link est loin (LOIN)
    LOIN = 2500.0
    NEAR = {PROC_GENS: 220.0, PROC_COCHON: 130.0, PROC_CRABE: 0.0}   # u : notre Link tout près -> on n'y touche pas
    EXTRA_MAX = 60.0        # u : avance extrapolée au plus (âge du message × vitesse)
    HOLD_MIN, HOLD = 30, 60  # images : attente locale prolongée tant que l'autorité attend
    IDX_DELAY = 1.0         # s : écart de point du chemin toléré avant de le recopier (voir _rythme)
    LIM = 1e5

    def __init__(self, enabled: bool = False, clock=time.time):
        self.enabled = enabled
        self.clock = clock
        self.lock = threading.Lock()
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.once: set = set()
        self.zone = None
        self.inbox: dict[str, tuple] = {}       # clé -> (entrée, heure) : autorité
        self.claims: dict[str, tuple] = {}      # clé -> (entrée, heure, joueur) : c / t des autres
        self.last: dict[str, tuple] = {}        # clé -> (pos, heure du dernier mouvement)
        self.mine: dict[str, float] = {}        # clé -> heure jusqu'à laquelle j'envoie encore « c »
        self.paths: dict[int, int | None] = {}  # dPath* -> nombre de points (None : illisible)
        self.ecart: dict[str, float] = {}       # clé -> heure depuis laquelle le point du chemin diffère
        self.t_send = self.t_claim = 0.0
        self.n = {"out": 0, "pull": 0, "snap": 0, "idx": 0, "stop": 0, "go": 0, "claim": 0}
        self.suivis = 0

    # --- réseau ----------------------------------------------------------------------------------
    def on_message(self, msg: dict, me: int | None = None) -> None:
        if not self.enabled or not isinstance(msg, dict) or msg.get("type") != "pnj":
            return
        lst, frm, zone = valid_pnj(msg), msg.get("from"), msg.get("zone")
        if lst is None or not _int(frm) or frm == me or not isinstance(zone, str):
            return
        now = self.clock()
        with self.lock:
            if zone != self.zone:
                return
            for e in lst:
                if e.get("c") or e.get("t"):
                    # deux joueurs sur le même PNJ (chacun porte SON cochon) : le premier garde la main
                    # tant que son signalement est frais, sinon le PNJ ferait la navette entre les deux
                    old = self.claims.get(e["k"])
                    if old is not None and old[2] != frm and now - old[1] <= self.CLAIM_TTL:
                        continue
                    self.claims[e["k"]] = (e, now, frm)
                else:
                    self.inbox[e["k"]] = (e, now)
            for d in (self.inbox, self.claims):
                if len(d) > 4 * PNJ_MAX:        # jamais sans borne
                    for k in sorted(d, key=lambda k: d[k][1])[:len(d) - 4 * PNJ_MAX]:
                        del d[k]

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def take_notes(self) -> list[str]:
        with self.lock:
            out, self.notes = self.notes, []
        return out

    def reset_network(self) -> None:
        with self.lock:
            self.out.clear()
            self.inbox.clear()
            self.claims.clear()
            self.mine.clear()

    def _note(self, s: str) -> None:
        with self.lock:
            if len(self.notes) < 20:
                self.notes.append(s)

    def _once(self, tag, s: str) -> None:
        if tag not in self.once:
            if len(self.once) > 200:
                self.once.clear()
            self.once.add(tag)
            self._note(s)

    # --- une passe -------------------------------------------------------------------------------
    def step(self, mem, now: float, zone, actors, link_base: int = 0, link_pos=None, *, auth: bool = False,
             free: bool = True, settled: bool = True, can: bool = True, leaving: bool = False) -> None:
        """actors : [(pid, base, proc, params, salle, taille du profil, home)] de l'échantillonneur.
        free : jeu libre chez nous (pas d'événement) ; leaving : changement de salle en cours."""
        if not self.enabled:
            return
        if zone != self.zone:
            with self.lock:
                self.zone = zone
                self.inbox.clear()
                self.claims.clear()
                self.out.clear()
            self.last.clear()
            self.mine.clear()
            self.paths.clear()
            self.ecart.clear()
        if mem is None or zone is None or not actors or leaving or not settled:
            self.suivis = 0
            return
        loc = self._collect(mem, actors)
        self.suivis = len(loc)
        if not loc:
            return
        grab = self._grab(mem, link_base)
        talk = self._talk(mem) if not free else ()
        with self.lock:
            for d, ttl in ((self.inbox, self.TTL), (self.claims, self.CLAIM_TTL)):
                for k in [k for k, v in d.items() if now - v[1] > ttl]:
                    del d[k]
            inbox, claims = dict(self.inbox), dict(self.claims)
        self._send(mem, now, zone, loc, link_pos, grab, talk, claims, auth)
        if free and can:
            for k, o in loc.items():
                src = claims.get(k) or (None if auth else inbox.get(k))
                if src is not None and k not in self.mine:
                    self._apply(mem, now, o, src[0], now - src[1], link_pos, grab)

    def _collect(self, mem, actors) -> dict[str, dict]:
        loc, dup = {}, set()
        for a in actors:
            try:
                pid, base, proc, params, room, size, home = a[:7]
            except (TypeError, ValueError):
                continue
            if proc not in PROCS or not all(_num(v) for v in home):
                continue
            o = read_pnj(mem, base, proc, size)
            if o is None or o["pid"] != pid:
                continue
            k = rkey(proc, room, params, home)
            if len(k) > KEY_LEN:
                continue
            if k in loc:
                dup.add(k)              # deux acteurs de même clé : on ne sait pas lequel -> aucun
            loc[k] = o
        for k in dup:
            del loc[k]
        return loc

    @staticmethod
    def _grab(mem, link_base: int) -> int:
        if not _ptr(link_base):
            return 0
        raw = mem.try_read(link_base + LINK_GRAB_PTR, 4)
        return struct.unpack(">I", raw)[0] if raw is not None and len(raw) == 4 else 0

    @staticmethod
    def _talk(mem) -> tuple:
        """Ids de process des interlocuteurs de NOTRE dialogue en cours (vide hors dialogue)."""
        ev = prog.read_event(mem)
        if ev is None or ev.mode != EVT_TALK:
            return ()
        raw = mem.try_read(prog.SYM_EVT_CTRL + prog.EVT_PT, 8)
        if raw is None or len(raw) < 8:
            return ()
        return tuple(p for p in struct.unpack(">II", raw) if p not in (0, 0xFFFFFFFF))

    def _n_points(self, mem, ppd: int) -> int | None:
        if ppd not in self.paths:
            if len(self.paths) > 256:
                self.paths.clear()
            path = read_path(mem, ppd)
            self.paths[ppd] = path[0] if path else None
        return self.paths[ppd]

    # --- envoi -----------------------------------------------------------------------------------
    def _entry(self, mem, k: str, o: dict) -> dict:
        e = {"k": k, "p": [round(v, 1) for v in o["pos"]], "a": o["a"], "v": round(o["sp"], 2)}
        if o["proc"] == PROC_GENS:
            e["m"] = o["m"]
            n = self._n_points(mem, o["ppd"])
            if n is not None and o["i"] < n and o["d"] <= 1:
                e.update(i=o["i"], d=o["d"], n=n)
        return e

    def _send(self, mem, now, zone, loc, link_pos, grab, talk, claims, auth) -> None:
        # ce que JE porte / à qui JE parle : tout joueur, autorité ou non
        mien = []
        for k, o in loc.items():
            porte = o["proc"] == PROC_COCHON and bool(o["status"] & STATUS_CARRY or (grab and grab == o["base"]))
            if porte:
                self.mine[k] = now + self.CLAIM_KEEP
            if porte or self.mine.get(k, 0.0) > now:
                mien.append((k, o, "c"))
            elif o["pid"] in talk:
                mien.append((k, o, "t"))
        for k in [k for k, t in self.mine.items() if t <= now]:
            del self.mine[k]
        if mien and now - self.t_claim >= self.CLAIM_PERIOD:
            self.t_claim = now
            lst = []
            for k, o, flag in mien[:CLAIM_MAX]:
                e = self._entry(mem, k, o)
                e[flag] = 1
                if flag == "t":
                    e["v"] = 0.0
                    if o["proc"] == PROC_GENS:
                        e["m"] = MODE_PARLE
                lst.append(e)
            self.n["claim"] += 1
            with self.lock:
                self.out.append({"type": "pnj", "zone": zone, "list": lst})
        if not auth or now - self.t_send < self.PERIOD:
            return
        self.t_send = now
        pris = {k for k, _o, _f in mien}
        lp = _pos(link_pos)
        cand = []
        for k, o in loc.items():
            old = self.last.get(k)
            bouge = abs(o["sp"]) > 0.01 or (old is not None and math.dist(old[0], o["pos"]) >= self.MOVE_EPS)
            self.last[k] = (o["pos"], now if bouge else (old[1] if old else -1e9))
            if k in pris or k in claims:
                continue
            # un habitant à chemin et à minuteur est toujours envoyé (son attente aussi doit arriver)
            marcheur = (o["proc"] == PROC_GENS and o["timer"] > 0
                        and self._n_points(mem, o["ppd"]) is not None)
            if bouge or marcheur or now - self.last[k][1] <= self.ACTIVE:
                cand.append((math.dist(lp, o["pos"]) if lp else 0.0, k, o))
        if len(self.last) > 8 * PNJ_MAX:
            for k in [k for k in self.last if k not in loc]:
                del self.last[k]
        if not cand:
            return
        cand.sort(key=lambda c: (c[0], c[1]))
        lst = [self._entry(mem, k, o) for _d, k, o in cand[:PNJ_MAX]]
        self.n["out"] += 1
        with self.lock:
            self.out.append({"type": "pnj", "zone": zone, "list": lst})

    # --- application -----------------------------------------------------------------------------
    def _apply(self, mem, now, o, e, age, link_pos, grab) -> None:
        proc, base = o["proc"], o["base"]
        if o["evt"] or o["status"] & (STATUS_DEMO | STATUS_CARRY) or (grab and grab == base):
            return
        lp = _pos(link_pos)
        if lp is None or math.dist(lp, o["pos"]) < self.NEAR[proc]:
            return                      # notre Link est tout près (ou on ne sait pas où il est) : il va
                                        # peut-être lui parler / le ramasser -> aucune écriture
        porte = bool(e.get("c"))
        gens = proc == PROC_GENS
        if gens:
            if o["m"] == MODE_PARLE or o["near"]:
                return
            if "m" in e and not self._rythme(mem, o, e, now):
                return
        tx, tz = e["p"][0], e["p"][2]
        if not porte and age > 0 and abs(e["v"]) > 0.01 and (not gens or e.get("m") == MODE_MARCHE):
            av = max(-self.EXTRA_MAX, min(self.EXTRA_MAX, e["v"] * FPS * min(age, 1.0)))
            ang = e["a"] * math.pi / 32768.0
            tx, tz = tx + math.sin(ang) * av, tz + math.cos(ang) * av
        # très loin de la position REÇUE : recalage d'un coup (hors de vue seulement), sur cette position
        # exacte, sans extrapolation : la hauteur reçue n'est celle du sol qu'à cet endroit précis
        saut = math.hypot(e["p"][0] - o["pos"][0], e["p"][2] - o["pos"][2]) > self.FAR
        if saut:
            tx, tz = e["p"][0], e["p"][2]
        dx, dz = tx - o["pos"][0], tz - o["pos"][2]
        err = math.hypot(dx, dz)
        dead = self.DEAD_CARRY if porte else self.DEAD
        if err <= dead or not (abs(tx) < self.LIM and abs(tz) < self.LIM):
            return
        if saut:
            if math.dist(lp, o["pos"]) < self.LOIN or math.dist(lp, (tx, lp[1], tz)) < self.LOIN:
                self._once(("loin", e["k"]), f"PNJ : {NOMS[proc]} très loin de sa place chez l'autorité "
                                             f"({err:.0f} u) et près de toi : laissé où il est")
                return
            pas = err                   # hors de vue : recalé d'un coup
        else:
            pas = min(err - dead / 2, max(2.0, min(self.PULL_CARRY if porte else self.PULL_MAX,
                                                   err * self.PULL_GAIN)))
        nx, nz = o["pos"][0] + dx / err * pas, o["pos"][2] + dz / err * pas
        if not self._meme(mem, o):
            return
        try:
            if saut:                    # hors de vue : la hauteur aussi (sinon il arriverait sous le sol
                mem.write(base + OFF_POS, struct.pack(">3f", tx, e["p"][1], tz))    # ou en l'air)
            else:
                mem.write(base + OFF_POS, struct.pack(">f", nx))
                mem.write(base + OFF_POS + 8, struct.pack(">f", nz))
        except Exception:
            return
        self.n["snap" if saut else "pull"] += 1

    @staticmethod
    def _meme(mem, o) -> bool:
        """L'acteur à cette adresse est-il toujours celui qu'on a lu (id de process, proc) ?"""
        raw = mem.try_read(o["base"], 0xC)
        if raw is None or len(raw) < 0xC:
            return False
        bs, pid = struct.unpack_from(">II", raw, 0)
        return bs != 0 and pid == o["pid"] and struct.unpack_from(">h", raw, OFF_PROC)[0] == o["proc"]

    def _rythme(self, mem, o, e, now: float = 0.0) -> bool:
        """Habitant (373) : chemin et marche / arrêt recopiés par les minuteurs natifs. False : le PNJ
        local est dans un état qu'on ne touche pas (ni chemin, ni position).
        Le point du chemin n'est recopié que si l'écart dure depuis IDX_DELAY : le PNJ local qui atteint
        son point un peu AVANT celui de l'autorité (dont le message a jusqu'à PERIOD de retard) passe au
        point suivant de lui-même ; lui réécrire l'ancien point à chaque passe le ferait hésiter sur
        place jusqu'au message suivant."""
        lm, am, base = o["m"], e["m"], o["base"]
        if lm not in (MODE_ATTENTE, MODE_MARCHE, MODE_TOURNE):
            return False
        marche = am in (MODE_MARCHE, MODE_TOURNE)
        try:
            if "n" in e:
                n = self._n_points(mem, o["ppd"])
                if n != e["n"]:
                    self._once(("chemin", e["k"]), "PNJ : un habitant n'a pas le même chemin chez toi "
                                                   "(heure du jour différente ?) : pas de correction")
                    return False
                k = e["k"]
                if o["i"] == e["i"] and o["d"] == e["d"]:
                    self.ecart.pop(k, None)
                elif lm in (MODE_MARCHE, MODE_TOURNE) and o["i"] < n and o["d"] <= 1:
                    if len(self.ecart) > 8 * PNJ_MAX:
                        self.ecart.clear()
                    depuis = self.ecart.setdefault(k, now)
                    if now - depuis >= self.IDX_DELAY and self._meme(mem, o):
                        mem.write(base + G_IDX, bytes([e["i"]]))
                        mem.write(base + G_DIR, bytes([e["d"]]))
                        self.n["idx"] += 1
                        del self.ecart[k]
            if o["timer"] <= 0:
                return True             # pas de minuteur chez ce PNJ : on ne touche pas à son rythme
            if marche and lm == MODE_ATTENTE and o["timer"] > 1 and self._meme(mem, o):
                mem.write(base + G_TIMER, struct.pack(">h", 1))
                self.n["go"] += 1
            elif not marche and lm == MODE_MARCHE and o["timer"] > 1 and self._meme(mem, o):
                mem.write(base + G_TIMER, struct.pack(">h", 1))
                self.n["stop"] += 1
            elif not marche and lm == MODE_ATTENTE and o["timer"] < self.HOLD_MIN and self._meme(mem, o):
                mem.write(base + G_TIMER, struct.pack(">h", self.HOLD))
        except Exception:
            return False
        return True

    # --- affichage -------------------------------------------------------------------------------
    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        n = self.n
        return (f"{self.suivis} suivi(s), {n['out']} envoi(s), {n['pull']} pas de correction, "
                f"{n['idx']} chemin(s) recalé(s), {n['stop']} arrêt(s) / {n['go']} départ(s), "
                f"{n['snap']} recalage(s) hors de vue, {n['claim']} envoi(s) porté / dialogue")


class ScoreMiniJeu:
    """Compteur des mini-jeux : le nôtre (lu, envoyé dans state.mgs) et ceux des autres (annoncés à la
    fin, meilleur de la partie par mini-jeu). Aucune écriture."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.local = (0, 0)                         # (type, dernier compteur lu)
        self.cur: dict[int, tuple[int, int]] = {}   # joueur -> (type, dernier compteur reçu)
        self.records: dict[int, tuple[int, str]] = {}

    def _fin(self, nom: str, mg: int, score: int, libelle) -> list[str]:
        if score <= 0:
            return []
        out = [f"{nom} : {libelle(mg)} terminé, compteur {score}"]
        old = self.records.get(mg)
        if old is None or score > old[0]:
            self.records[mg] = (score, nom)
            if old is not None:
                out.append(f"record de la partie ({libelle(mg)}) : {nom}, {score} (avant : {old[1]}, {old[0]})")
        return out

    def local_step(self, mem, mg: int, state: dict | None, libelle, nom: str = "toi") -> list[str]:
        """mg : type du mini-jeu en cours chez nous (0 = aucun). Ajoute state.mgs pendant le mini-jeu."""
        if not self.enabled:
            return []
        old_mg, old_score = self.local
        if not mg:
            self.local = (0, 0)
            return self._fin(nom, old_mg, old_score, libelle) if old_mg else []
        score = lire_compteur(mem)
        score = old_score if score is None and old_mg == mg else (score or 0)
        self.local = (mg, score)
        if isinstance(state, dict):
            state["mgs"] = score
        return []

    def observe(self, pid: int, nom: str, state: dict, libelle) -> list[str]:
        if not self.enabled or not isinstance(state, dict):
            return []
        mg, s = state.get("mg"), state.get("mgs")
        mg = mg if _int(mg) and 0 < mg < 256 else 0
        old = self.cur.get(pid)
        if mg:
            s = s if _int(s) and 0 <= s <= 9999 else (old[1] if old and old[0] == mg else 0)
            self.cur[pid] = (mg, s)
            return []
        if old is None:
            return []
        del self.cur[pid]
        return self._fin(nom, old[0], old[1], libelle)

    def forget(self, pid: int) -> None:
        self.cur.pop(pid, None)

    def reset(self) -> None:
        self.cur.clear()

    def status(self, libelle) -> str:
        if not self.records:
            return "aucun compteur relevé"
        return ", ".join(f"{libelle(mg)} : {s} ({nom})" for mg, (s, nom) in sorted(self.records.items()))
