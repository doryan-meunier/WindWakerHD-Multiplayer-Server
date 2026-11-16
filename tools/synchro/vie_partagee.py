"""Vie partagée : tout le groupe a la même barre de vie, chacun prend les dégâts (et les soins) des autres.

Réglage de la PARTIE, comme le deathlink : l'hôte le choisit dans les réglages du lanceur, le serveur le reçoit
(--vie-partagee, --no-vie-partagee ; à chaud : « regles_cmd » {vie_partagee} du lanceur de l'hôte, ou l'admin du
serveur dédié) et l'impose à tous (champ ``vie_partagee`` du ``welcome`` et de chaque ``regles``). Côté client,
--vie-partagee (activé par --coop-donjon) dit seulement « ce PC accepte la règle » ; --no-vie-partagee la refuse
sur ce PC. Jamais avec le mode Hanteurs (ses règles restent les siennes : K.O., points par quart perdu) ; jamais
pour le joueur robot ; jamais pour un Hanteur ni pendant une possession. Logique testable sans jeu.

Ce que les joueurs voient : PC1 prend un coup d'un cœur, PC2 perd un cœur aussi ; PC2 ramasse un cœur, PC1 en
regagne un ; la vie commune tombe à 0 : tout le monde meurt (Game Over). Une fée en flacon qui relève un joueur
rend la vie à tous, une seule fois.

Mécanisme (même règle que les compteurs communs, synchro/compteurs.py : on AJOUTE un delta, on n'impose jamais
une valeur brute qui écraserait un dégât local pas encore relevé) :

* lecture (5 Hz) ``L = vie (gi+0x22) + variation en attente (f32 play+0x5B44, tronquée comme fctiwz)``, bornée à
  [0, vie max (gi+0x20)] : un coup ou un soin passe souvent par l'attente que l'ATH vide à l'image suivante
  (updateHudStatus 0x02593B10, natif/mod_wwmp/difficulte.c) ; L voit les deux ;
* ``delta = L - base`` -> événement ``vie {seq, ev, d, max}`` (``ev`` unique : le serveur ne compte jamais deux
  fois un même événement renvoyé après une coupure) ; ``base := L`` ;
* le serveur (``VieServeur``) applique les deltas DANS L'ORDRE D'ARRIVÉE à une valeur par groupe, bornée à
  [0, vie max du groupe] (le plus grand maximum annoncé : la vie max est déjà commune quand les réceptacles et
  les quarts sont partagés, --share-hearts / --coop-donjon, synchro/cadeaux.py ; sinon chaque PC borne en plus
  à SA vie max) ; jamais « le dernier qui écrit gagne » ;
* correction : total ``v`` reçu avec l'accusé ``ack`` ; seulement si l'accusé couvre tous nos événements, en jeu
  libre (aucun événement, pas de changement de salle, salle stable depuis 2 s, pas de menu ni de message, pas
  de mini-jeu), Link vivant (pas DEMO_DEAD, vie > 0) et l'attente vide : ``corr = min(v, vie max) - L`` AJOUTÉ à
  la vie. Mod natif 0.30.0 (bit 31, CALL 18 VIE, natif/mod_wwmp/vie.c) : l'ajout est fait par le jeu, sur son
  fil, avec ses gardes (aucune course avec l'ATH) ; sans lui (Cemu, mod plus ancien) : écriture Python gardée
  (attente lue nulle deux fois et relue nulle juste avant, vie relue juste avant, puis vie + corr) et une
  écriture perdue (le jeu a réécrit la vie pendant ce temps) est reconnue et n'est jamais prise pour un soin ;
* ``base := cible`` : pas d'écho ; un coup pris entre la lecture et l'écriture reste dans la vie et part au tick
  suivant.

Mort : la vie commune à 0 -> chaque PC reçoit 0, la vie est écrite à 0 et le jeu lance lui-même la mort (comme le
deathlink). Avec le deathlink aussi : une mort causée par cette correction n'est jamais renvoyée en « mort »
(``mort_induite``), une vague reçue ne retire rien de plus (la valeur est déjà bornée à 0) : pas de double
comptage. Fée en flacon (règle du jeu : changeDeadProc lâche la fée et relève Link) : le delta qui suit une vie
à 0 part marqué ``fee`` ; le serveur ne l'applique que si le groupe est à 0 (la première fée relève tout le monde,
les suivantes ne soignent pas une seconde fois). Si le joueur qui fait tomber le groupe à 0 a lui-même une fée
en flacon (``fl``, ``zero``), le serveur attend ``FEE_ATTENTE`` s avant d'imposer 0 aux autres (``attente`` : aucune
correction pendant ce temps) : sa fée relève alors tout le groupe avant que les autres ne meurent.

Groupe mort puis Game Over « Continuer » (ou partie rechargée) : la vie de reprise du jeu (3 cœurs) part dans un
``hello`` ; un groupe à 0 la prend (sinon le premier relevé retomberait aussitôt à 0) ; un groupe vivant fait foi.

Chargements (jamais comptés comme un soin ni un dégât) : partie (re)chargée (gi, ``mDateIPL``, écran titre), état
de sauvegarde du portage (``card_to_memory`` en pleine partie : compteur ``chargements`` du mod natif 0.23.0,
jeu/natif.chargements, dans le contexte comme pour synchro/compteurs.py et inventaire.py), sauvegarde rechargée vue
par la progression (``ProgressSync.reloads``) : nouvelle base, ``hello``, aucun delta. Au retour d'une pause
(changement de salle, Link absent) : une HAUSSE n'est
jamais envoyée (Continuer, état chargé : nouvelle base), une BAISSE n'est envoyée que jusqu'à ``PERTE_PAUSE`` quarts
(chute dans le vide, lave : le cœur retiré au retour dans la salle), au-delà c'est un chargement.

Triche « vie infinie » du portage (la vie remise au maximum à chaque image) : une baisse écrite par ce PC
aussitôt effacée (vie relue au maximum) deux fois de suite -> ce PC cesse de partager (ni soin envoyé, ni
correction) jusqu'à ce qu'un dégât reste ; sinon il soignerait tout le groupe à chaque coup reçu par les autres.

Monde persistant : la valeur commune (et la vie max du groupe) est gardée avec le monde (section « vie » du
groupe) ; relue, elle fait foi.
"""
from __future__ import annotations

import os
import re
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from jeu import progress as prog
from jeu import savecopy

# --- adresses HD [HD-ASM, mêmes que synchro/deathlink.py et natif/mod_wwmp/difficulte.c] -----------------
GI_MAX_LIFE = 0x20              # u16 vie max en quarts (1..80)
GI_LIFE = 0x22                  # u16 vie courante en quarts
PLAY_PENDING = 0x5B44           # f32 mItemLifeCount : variation de vie en attente (vidée par l'ATH)
PLAY_LINK = 0x5B34              # mpPlayerPtr[0] : Link
LINK_CUR_PROC = 0x65F0          # u32 daPy_lk_c::mCurProc
PROC_DEAD = 0xB2                # daPyProc_DEMO_DEAD_e
FAIRY_BOTTLE = 0x57             # dItemNo_FAIRY_BOTTLE_e
SLOTS_FLACONS = range(14, 18)

VIE_MAX = 80                    # 20 cœurs
DELTA_MAX = 80                  # un événement ne change jamais la vie de plus de 20 cœurs
PERTE_PAUSE = 16                # quarts : baisse vue au retour d'une pause encore comptée (chute ×4 au plus)
FEE_ATTENTE = 10.0              # s : le serveur attend la fée du joueur qui fait tomber le groupe à 0
EV_RE = re.compile(r"^[0-9a-z]{1,16}:\d{1,10}$")
SEQ_MAX = 2 ** 31
ROLE_EXCLUS = ("robot",)


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def borne(v: int, vmax: int | None) -> int:
    hi = vmax if _int(vmax) and 0 < vmax <= VIE_MAX else VIE_MAX
    return max(0, min(int(v), hi))


def tronquer_f32(raw: bytes) -> int | None:
    """fctiwz d'un f32 big-endian (troncature vers 0) ; None : NaN, infini ou absurde (> 1000 en valeur)."""
    x = struct.unpack(">f", raw)[0]
    if x != x or abs(x) > 1000.0:
        return None
    return int(x)


# ---------------------------------------------------------------------------
# Validation des messages
# ---------------------------------------------------------------------------
def valid_vie(msg) -> dict | None:
    """Message « vie » d'un client, nettoyé ; None s'il est invalide.
    hello : {hello: 1, v, max, ack, hote?} ; sync : {sync: 1} ; delta : {seq, ev, d, max, fee?, fl?, zero?}."""
    if not isinstance(msg, dict):
        return None
    vmax = msg.get("max")
    out = {"group": str(msg.get("group") or "default")[:32], "hello": msg.get("hello") in (1, True),
           "sync": msg.get("sync") in (1, True), "hote": msg.get("hote") in (1, True),
           "max": vmax if _int(vmax) and 1 <= vmax <= VIE_MAX else 0}
    seq = msg.get("seq", 0)
    if not (_int(seq) and 0 <= seq < SEQ_MAX):
        return None
    out["seq"] = seq
    if out["hello"]:
        v, ack = msg.get("v"), msg.get("ack", 0)
        if not (_int(v) and 0 <= v <= VIE_MAX and _int(ack) and 0 <= ack < SEQ_MAX):
            return None
        out["v"], out["ack"] = v, ack
        return out
    if out["sync"]:
        return out
    ev, d = msg.get("ev"), msg.get("d")
    if not (isinstance(ev, str) and EV_RE.match(ev)) or not _int(d) or not d or abs(d) > DELTA_MAX or seq < 1:
        return None
    out.update(ev=ev, d=d, fee=msg.get("fee") in (1, True), fl=msg.get("fl") in (1, True),
               zero=msg.get("zero") in (1, True))
    return out


def regle_lue(msg: dict) -> bool | None:
    """Champ ``vie_partagee`` d'un welcome / regles : True, False, ou None (absent : ancien serveur, inchangé)."""
    if not isinstance(msg, dict) or "vie_partagee" not in msg:
        return None
    return msg.get("vie_partagee") in (1, True)


# ---------------------------------------------------------------------------
# Serveur : une valeur par groupe
# ---------------------------------------------------------------------------
class VieServeur:
    """Vie commune par groupe : {v (None : inconnue), max, evs (identifiants déjà comptés), fixe (un hôte ou le
    monde persistant l'a fixée), attente (fée attendue : {fin, src}), morts}. Logique pure (le serveur s'occupe
    des envois). ``on`` : règle de la partie."""
    EV_MAX = 4096

    def __init__(self, on: bool = False):
        self.vu = False                 # la règle a été allumée au moins une fois (« regles » la porte alors toujours)
        self.on = bool(on)
        self.groups: dict[str, dict] = {}

    @property
    def on(self) -> bool:
        return self._on

    @on.setter
    def on(self, v) -> None:
        self._on = bool(v)
        self.vu = self.vu or self._on

    def etat(self, group: str) -> dict:
        return self.groups.setdefault(group, {"v": None, "max": 0, "evs": OrderedDict(), "fixe": False,
                                              "attente": None, "morts": 0})

    def oublier(self, group: str) -> None:
        self.groups.pop(group, None)

    @staticmethod
    def _max(st: dict, vmax: int) -> None:
        if _int(vmax) and st["max"] < vmax <= VIE_MAX:
            st["max"] = vmax
            if st["v"] is not None:
                st["v"] = borne(st["v"], st["max"])

    def hello(self, group: str, v: int, vmax: int, hote: bool = False) -> bool:
        """Inscription. Valeur encore inconnue : celle annoncée ; premier hello d'un hôte (hors monde persistant) :
        imposée ; groupe à 0 (tous morts, ou monde gardé pendant un Game Over) hors attente d'une fée : la vie de
        reprise du jeu (Continuer, partie rechargée) est prise ; sinon le serveur fait foi. Renvoie True si la
        valeur a changé."""
        st = self.etat(group)
        self._max(st, vmax)
        old = st["v"]
        new = borne(v, st["max"] or vmax)
        if old is None or (hote and not st["fixe"]) or (old == 0 and new > 0 and st["attente"] is None):
            st["v"] = new
        if hote:
            st["fixe"] = True
        return st["v"] != old

    def delta(self, group: str, ev: str, d: int, vmax: int = 0, fee: bool = False, fl: bool = False,
              zero: bool = False, now: float | None = None) -> dict | None:
        """Événement ``ev`` (delta ``d`` en quarts) appliqué à la suite des précédents ; None si déjà compté (renvoi).
        Renvoie {app (appliqué), v, mort (le groupe vient de tomber à 0), attente (fée attendue : rien d'imposé),
        raison (ignoré)}."""
        st = self.etat(group)
        if ev in st["evs"]:
            return None
        st["evs"][ev] = 1
        while len(st["evs"]) > self.EV_MAX:
            st["evs"].popitem(last=False)
        now = time.monotonic() if now is None else now
        self._max(st, vmax)
        res = {"app": 0, "v": st["v"], "mort": False, "attente": st["attente"] is not None, "raison": ""}
        if st["v"] is None:
            res["raison"] = "pas encore inscrit"
            return res
        if fee and d > 0 and st["v"] > 0 and st["attente"] is None:
            res["raison"] = "fée : le groupe est déjà relevé (pas de second soin)"
            return res
        old = st["v"]
        new = borne(old + d, st["max"] or vmax)
        st["v"] = new
        res.update(app=new - old, v=new)
        if new > 0 and st["attente"] is not None:
            st["attente"] = None              # la fée (ou un soin) a relevé le groupe : personne ne meurt
        elif new == 0 and old > 0:
            if fl and zero:
                st["attente"] = {"fin": now + FEE_ATTENTE}
            else:
                st["morts"] += 1
                res["mort"] = True
        res["attente"] = st["attente"] is not None
        return res

    def echeance(self, now: float | None = None) -> list[str]:
        """Attentes de fée échues (aucune fée n'a relevé le groupe) : groupes à 0 maintenant imposés (morts)."""
        now = time.monotonic() if now is None else now
        out = []
        for g, st in self.groups.items():
            a = st["attente"]
            if a is not None and now >= a["fin"]:
                st["attente"] = None
                if st["v"] == 0:
                    st["morts"] += 1
                    out.append(g)
        return out

    # --- monde persistant -------------------------------------------------------------------------
    def document(self, group: str) -> dict | None:
        st = self.groups.get(group)
        if st is None or st["v"] is None:
            return None
        return {"v": st["v"], "max": st["max"], "evs": list(st["evs"])[-512:]}

    def restaurer(self, group: str, doc) -> bool:
        """Section « vie » relue : la valeur fait foi (fixée). False : entrée abîmée (ignorée)."""
        if not isinstance(doc, dict):
            return False
        v, vmax = doc.get("v"), doc.get("max", 0)
        if not (_int(v) and 0 <= v <= VIE_MAX and _int(vmax) and 0 <= vmax <= VIE_MAX):
            return False
        st = self.etat(group)
        st["max"] = vmax
        st["v"] = borne(v, vmax or VIE_MAX)
        st["fixe"] = True
        evs = doc.get("evs") if isinstance(doc.get("evs"), list) else []
        st["evs"] = OrderedDict((e, 1) for e in evs[-self.EV_MAX:] if isinstance(e, str) and EV_RE.match(e))
        return True


# ---------------------------------------------------------------------------
# Lecture du jeu
# ---------------------------------------------------------------------------
@dataclass
class Lu:
    ok: bool
    raison: str = ""
    gi: int = 0
    date: bytes = b""
    pause: str = ""
    vie: int = 0
    vmax: int = 0
    pend: int = 0
    proc: int = 0
    flacon: bool = False
    charg: int | None = None        # parties remplacées (mod natif 0.23.0, chargements.c ; None : Cemu, mod ancien)

    @property
    def L(self) -> int:
        return borne(self.vie + self.pend, self.vmax)

    @property
    def mort(self) -> bool:
        return self.proc == PROC_DEAD

    @property
    def contexte(self) -> tuple:
        return (self.gi, self.date, self.charg)


def lire(mem) -> Lu:
    r = savecopy.partie_chargee(mem)
    if r:
        return Lu(False, r)
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    gi = struct.unpack(">I", raw)[0] if raw else 0
    blob = mem.try_read(gi + GI_MAX_LIFE, 4)
    items = mem.try_read(gi + savecopy.GI_ITEM, savecopy.ITEM_SIZE)
    date = mem.try_read(gi + savecopy.GI_DATE_IPL, 8)
    pend = mem.try_read(prog.SYM_PLAY + PLAY_PENDING, 4)
    lk = mem.try_read(prog.SYM_PLAY + PLAY_LINK, 4)
    if blob is None or pend is None or lk is None or items is None or date is None:
        return Lu(False, "sauvegarde illisible", gi)
    link = struct.unpack(">I", lk)[0]
    if not 0x10000000 <= link < 0x50000000:
        return Lu(False, "Link absent (chargement)", gi)
    rp = mem.try_read(link + LINK_CUR_PROC, 4)
    if rp is None:
        return Lu(False, "Link illisible", gi)
    vmax, vie = struct.unpack(">HH", blob)
    if not 1 <= vmax <= VIE_MAX or vie > VIE_MAX:
        return Lu(False, f"vie incohérente ({vie}/{vmax})", gi)
    p = tronquer_f32(pend)
    if p is None:
        return Lu(False, "variation de vie en attente illisible", gi)
    lu = Lu(True, "", gi, date, "", vie, vmax, p, struct.unpack(">I", rp)[0],
            any(items[s] == FAIRY_BOTTLE for s in SLOTS_FLACONS))
    # état de sauvegarde du portage chargé en pleine partie (card_to_memory) : gameInfo et mDateIPL ne changent pas,
    # seul le compteur du mod le trahit (même contexte que synchro/compteurs.py et inventaire.py)
    lu.charg = _chargements(mem)
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        lu.pause = "changement de salle en cours"
    return lu


def _chargements(mem):
    """Compteur des parties remplacées (jeu.natif.chargements, audit natif) ; importé ici seulement (le serveur importe
    ce module sans winmem ni procmem)."""
    try:
        from jeu import natif
        return natif.chargements(mem)
    except Exception:                  # boîte illisible : comme sans le mod
        return None


def ecrire_par_boite(mb):
    """Mod natif 0.30.0 : fn(delta) -> vie après (int), ou None si le jeu refuse (garde : rien n'a été écrit, à
    retenter). None si la boîte n'a pas la zone WWMPVIE1 (Cemu, mod plus ancien : écriture Python gardée)."""
    if mb is None or not getattr(mb, "vie_ok", False):
        return None

    def ecrire(delta: int):
        r = mb.vie_ajouter(int(delta))
        return r if r >= 0 else None
    return ecrire


def menu_ouvert(mem) -> bool:
    raw = mem.try_read(savecopy.SYM_DMENU_FLAG, 1)
    return raw is None or raw[0] != 0


def ecrire_python(mem, corr: int) -> str:
    """Mode sans le mod 0.30.0 : vie relue juste avant (et l'attente nulle), puis vie + corr bornée. Le jeu lance
    lui-même la mort à 0. Renvoie « ok », « attente » (à retenter) ou « inutile »."""
    gi = struct.unpack(">I", mem.read(prog.SYM_GAMEINFO_PTR, 4))[0]
    with prog._write_lock:
        if tronquer_f32(mem.read(prog.SYM_PLAY + PLAY_PENDING, 4)) != 0:
            return "attente"
        vmax, vie = struct.unpack(">HH", mem.read(gi + GI_MAX_LIFE, 4))
        if vie == 0 or not 1 <= vmax <= VIE_MAX or vie > VIE_MAX:
            return "inutile"
        mem.write(gi + GI_LIFE, struct.pack(">H", borne(vie + corr, vmax)))
    return "ok"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class ViePartagee:
    PERIOD = 0.2           # s entre deux lectures (~5 Hz)
    PERIOD_TOL = 0.02
    SETTLE = prog.COOP_DONJON_SETTLE
    SEND_RATE = 15
    QUEUE_MAX = 256
    SYNC_EVERY = 5.0       # s : valeur redemandée (filet de sécurité ; 1 s pendant une attente de fée)
    RETRY = 0.5            # s : nouvel essai d'une correction refusée (événement, attente non vide)
    RESEND = 3.0           # s sans nouvel accusé : événements renvoyés (le serveur dédoublonne)
    TRICHE_SEUIL = 2       # baisses écrites aussitôt effacées d'affilée avant de conclure à la vie infinie

    def __init__(self, accepte: bool = False, group: str = "default", clock=time.monotonic, hote: bool = False):
        self.accepte = bool(accepte)
        self.regle = False
        self.group, self.clock, self.hote = group, clock, hote
        self.events = prog.EventWatch(prog.EVT_FREE_DELAY, clock)
        self.sid = os.urandom(4).hex()
        self.ecrire_jeu = None              # mod 0.30.0 : fn(delta) -> int (vie après) | None (refus : à retenter)
        self.mort_induite = None            # deathlink : fn(), notre mort qui vient ne sera pas renvoyée
        self._lock = threading.Lock()
        self.notes: list[str] = []
        self.n = {"envoyes": 0, "corrections": 0, "chargements": 0, "fees": 0, "perdues": 0}
        self._reset_tout()

    def _reset_tout(self) -> None:
        with self._lock:
            self.base: int | None = None
            self.ctx = None
            self.charg_vus: int | None = None
            self.since = 0.0
            self.last_tick = float("-inf")
            self.paused = False
            self.mort_vue = False
            self.lu: Lu | None = None
            self.pend_hist: tuple = (None, None)
            self.seq = self.seq_delta = self.ack = 0
            self.queue: list[dict] = []
            self.sent_upto = 0
            self.sent_t: list[float] = []
            self.need_hello, self.hello_msg, self.synced, self.srv_ok = True, None, False, False
            self.want_sync = False
            self.last_sync = float("-inf")
            self.prog_t = float("-inf")
            self.tot: int | None = None
            self.max_srv = 0
            self.attente = False
            self.retry_t = float("-inf")
            self.ecrit: dict | None = None      # dernière correction écrite : {avant, cible, corr, python}
            self.triche = False
            self.triche_n = 0
            self.rev = False
            self.status_txt = "pas encore lu"

    @property
    def enabled(self) -> bool:
        return self.accepte and self.regle

    def partage(self) -> bool:
        """Vie partagée ICI en ce moment (règle, serveur compatible, base relevée, pas de triche)."""
        return bool(self.enabled and self.srv_ok and self.base is not None and not self.triche)

    # --- notes ----------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + ["vie partagée : " + s])[-32:]

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    # --- réseau ---------------------------------------------------------------
    def set_regle(self, on, source: str = "serveur") -> None:
        on = on in (1, True)
        if on == self.regle:
            return
        self.regle = on
        if on and not self.accepte:
            self._note("la partie a la vie partagée, mais ce PC la refuse (--no-vie-partagee, ou client sans "
                       "--coop-donjon) : ta vie reste la tienne")
        elif on:
            self._note(f"activée par le {source} : une seule barre de vie pour tout le groupe")
        else:
            self._note(f"désactivée par le {source} : chacun garde la vie qu'il a maintenant")
        self._reset_tout()

    def on_welcome(self, msg: dict) -> None:
        r = regle_lue(msg)
        self.set_regle(bool(r))
        self.on_connect()

    def on_connect(self) -> None:
        with self._lock:
            self.need_hello, self.hello_msg, self.sent_upto, self.synced = True, None, 0, False
            self.attente = False

    def reset_network(self) -> None:
        with self._lock:
            self.synced = False
            self.attente = False
            self.need_hello, self.hello_msg, self.sent_upto = True, None, 0

    def receive(self, msg: dict, me=None) -> None:
        """Message « vie » du serveur (valeur commune, accusé, attente d'une fée) ou « regles »."""
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "regles":
            r = regle_lue(msg)
            if r is not None:
                self.set_regle(r, "serveur (réglage de l'hôte)")
            return
        if not self.enabled or msg.get("type") != "vie" or msg.get("group") not in (None, self.group):
            return
        with self._lock:
            ack = msg.get("ack")
            if _int(ack) and msg.get("from") is None:
                if ack > self.ack:
                    self.prog_t = self.clock()
                self.ack = max(self.ack, ack)
                self.queue = [m for m in self.queue if m["seq"] > self.ack]
            self.attente = msg.get("attente") in (1, True)
            v = msg.get("v")
            if _int(v) and 0 <= v <= VIE_MAX and not self.attente:
                self.tot = v
            vm = msg.get("max")
            if _int(vm) and 0 <= vm <= VIE_MAX:
                self.max_srv = vm
            if msg.get("snap") in (1, True) and msg.get("from") is None:
                self.synced = self.srv_ok = True
        if msg.get("mort") in (1, True):
            self._note("la vie commune est tombée à 0 : tout le monde meurt")
        elif self.attente and msg.get("from") is None and msg.get("d") is not None:
            self._note("vie commune à 0 : une fée en flacon peut encore relever le groupe")

    def _build_hello(self, rev: bool = False) -> None:
        lu = self.lu
        v = self.base if self.base is not None else 0
        for m in self.queue:
            v -= m["d"]
        self.hello_msg = {"type": "vie", "group": self.group, "hello": 1, "seq": 0, "ack": self.ack,
                          "v": borne(v, lu.vmax if lu else None), "max": lu.vmax if lu else 0,
                          **({"hote": 1} if self.hote else {}), **({"rev": 1} if rev else {})}
        self.need_hello = False
        self.synced = False

    def take_out(self, now: float | None = None) -> list[dict]:
        if not self.enabled:
            return []
        now = self.clock() if now is None else now
        with self._lock:
            out = []
            if self.hello_msg is not None:
                out.append(self.hello_msg)
                self.hello_msg = None
            elif self.need_hello:
                return []
            self.sent_t = [t for t in self.sent_t if now - t < 1.0]
            if any(m["seq"] <= self.sent_upto for m in self.queue) and now - self.prog_t >= self.RESEND:
                self.sent_upto = self.ack
                self.prog_t = now
            for m in self.queue:
                if m["seq"] > self.sent_upto and len(self.sent_t) < self.SEND_RATE:
                    if self.sent_upto <= self.ack:
                        self.prog_t = now
                    out.append(m)
                    self.sent_t.append(now)
                    self.sent_upto = m["seq"]
                elif m["seq"] > self.sent_upto:
                    break
            if self.want_sync and self.synced:
                out.append({"type": "vie", "group": self.group, "sync": 1})
                self.want_sync = False
            return out

    def _push(self, d: int, lu: Lu, fee: bool = False) -> None:
        with self._lock:
            self.seq += 1
            self.seq_delta = self.seq
            m = {"type": "vie", "group": self.group, "seq": self.seq, "ev": f"{self.sid}:{self.seq}", "d": d,
                 "max": lu.vmax}
            if fee:
                m["fee"] = 1
            if d < 0 and lu.flacon:
                m["fl"] = 1
            if d < 0 and lu.L == 0:
                m["zero"] = 1
            self.queue = (self.queue + [m])[-self.QUEUE_MAX:]
        self.n["envoyes"] += 1

    def _rebase(self, now: float, why: str, rev: bool = False, vider: bool = False, attendre: bool = True) -> None:
        with self._lock:
            self.base = None
            if vider:
                self.queue.clear()
                self.sent_upto = 0
            self.need_hello, self.hello_msg, self.synced = True, None, False
            self.rev = rev
        if attendre:
            self.since = now
        self.pend_hist = (None, None)
        self.ecrit = None
        if why:
            self.n["chargements"] += 1
            self._note(f"{why} : vie renvoyée au serveur, ni soin ni dégât compté")

    # --- tick -----------------------------------------------------------------
    def tick(self, mem, now: float | None = None, can_write: bool = True, connected: bool = True,
             free: bool = True, settled: bool = True, exclu: str = "", chargements: int = 0) -> int:
        """Lecture, delta -> file, valeur commune -> correction. ``exclu`` : raison de ne pas participer (Hanteur,
        possession, robot) ; ``chargements`` : compteur des états chargés du contexte. Renvoie le nombre de
        corrections écrites."""
        if not self.enabled or mem is None:
            return 0
        now = self.clock() if now is None else now
        if now - self.last_tick < self.PERIOD - self.PERIOD_TOL:
            return 0
        self.last_tick = now
        if exclu:
            if self.base is not None or self.queue:
                self._note(f"{exclu} : ta vie n'est plus partagée")
                self._rebase(now, "", vider=True)
            self.status_txt = exclu
            return 0
        try:
            lu = lire(mem)
        except Exception as exc:              # mémoire illisible pendant un chargement
            lu = Lu(False, f"lecture impossible : {exc}")
        if not lu.ok:
            self.since = now
            self.paused = self.paused or self.base is not None
            self.pend_hist = (None, None)
            if lu.raison.startswith(("écran titre", "pas de sauvegarde", "vie maximale")):
                self.ctx = None
            self.status_txt = lu.raison
            return 0
        if self.charg_vus is None:
            self.charg_vus = chargements
        if lu.contexte != self.ctx:
            first = self.ctx is None and self.base is None and self.seq == 0
            self.ctx = lu.contexte
            self.charg_vus = chargements
            self._rebase(now, "" if first else "partie (re)chargée", vider=True)
        elif chargements != self.charg_vus:
            self.charg_vus = chargements
            self._rebase(now, "état de sauvegarde chargé")
        if lu.mort or lu.L == 0:
            self.mort_vue = True
        if lu.pause:
            self.since = now
            self.paused = self.paused or self.base is not None
            self.pend_hist = (None, None)
            self.status_txt = lu.pause
            return 0
        if now - self.since < self.SETTLE:
            self.status_txt = "attente de stabilité"
            return 0
        self.lu = lu
        self.pend_hist = (self.pend_hist[1], lu.pend)
        L = lu.L
        if self.base is None:
            with self._lock:
                self.base = L
                self._build_hello(rev=self.rev)
                self.rev = False
            self.paused = False
            self.mort_vue = lu.mort or L == 0
            self.status_txt = "vie envoyée, attente de la valeur commune"
            return 0
        dv = L - self.base
        ecrit, self.ecrit = self.ecrit, None
        if ecrit is not None:
            # relecture juste après NOTRE correction : triche « vie infinie », écriture perdue (Python seulement)
            if ecrit["corr"] < 0 and dv > 0 and L == lu.vmax:
                # baisse écrite aussitôt effacée, vie au maximum : jamais envoyée comme un soin (sinon tout le
                # groupe serait soigné à chaque coup reçu par les autres)
                self.triche_n += 1
                if self.triche_n >= self.TRICHE_SEUIL and not self.triche:
                    self.triche = True
                    self._note("vie remise au maximum aussitôt après chaque dégât (triche « vie infinie » du "
                               "portage ?) : ce PC ne partage plus sa vie tant qu'un dégât ne reste pas")
                with self._lock:
                    self.base = L
                self.status_txt = "baisse effacée aussitôt par le jeu (vie infinie ?)"
                return 0
            if ecrit["python"] and L == ecrit["avant"] and dv == -ecrit["corr"]:
                self.n["perdues"] += 1
                with self._lock:
                    self.base = L
                self.status_txt = "correction perdue (le jeu a réécrit la vie) : nouvel essai"
                return 0
            if ecrit["corr"] < 0:
                self.triche_n = 0
        if self.triche and dv < 0 and L < lu.vmax:
            self.triche, self.triche_n = False, 0
            self._note("un dégât est resté : la vie est de nouveau partagée")
        if self.paused:
            self.paused = False
            self.want_sync = True
            if self.mort_vue and L > 0:
                # Game Over, « Continuer » : vie de reprise ; un groupe mort la prend, un groupe vivant fait foi
                self.mort_vue = False
                self._rebase(now, "reprise après la mort", rev=True, attendre=False)
                self.status_txt = "reprise après la mort"
                return 0
            if dv > 0 or -dv > PERTE_PAUSE:
                self._rebase(now, f"vie {dv:+d} quarts au retour d'un chargement", attendre=False)
                self.status_txt = "nouvelle base"
                return 0
        fee = dv > 0 and self.base == 0 and not lu.mort
        if fee:
            self.n["fees"] += 1
            self._note("une fée en flacon t'a relevé : tout le groupe regagne cette vie")
        if dv and not (self.triche and dv > 0):
            self._push(dv, lu, fee=fee)
        with self._lock:
            self.base = L
            if self.need_hello and self.hello_msg is None:
                self._build_hello()
        if L > 0 and not lu.mort:
            self.mort_vue = False
        sync_every = 1.0 if self.attente else self.SYNC_EVERY
        if now - self.last_sync >= sync_every:
            self.last_sync = now
            self.want_sync = True
        return self._corriger(mem, lu, now, can_write, connected, free, settled)

    def _corriger(self, mem, lu: Lu, now: float, can_write: bool, connected: bool, free: bool,
                  settled: bool) -> int:
        with self._lock:
            ok = self.synced and not self.need_hello and self.hello_msg is None
            ack_ok = self.ack >= self.seq_delta
            tot = self.tot
        why = ""
        if not can_write:
            why = "écriture impossible (lecture seule ou --progress-no-apply)"
        elif not connected:
            why = "hors connexion"
        elif not ok or tot is None:
            why = "attente de la valeur commune"
        elif self.attente:
            why = "vie commune à 0 : attente de la fée d'un joueur"
        elif not ack_ok:
            why = "événements pas encore accusés"
        elif self.triche:
            why = "triche « vie infinie » : rien n'est écrit"
        if why:
            self.status_txt = why
            return 0
        cible = borne(tot, lu.vmax)
        corr = cible - lu.L
        if not corr:
            self.status_txt = "à jour"
            return 0
        if now < self.retry_t:
            return 0
        attendre = ""
        if not (free and settled):
            attendre = "jeu occupé (événement, changement de salle)"
        elif lu.mort or lu.vie == 0:
            attendre = "Link meurt : rien n'est écrit"
        elif lu.pend != 0:
            attendre = "variation de vie en cours dans le jeu"
        elif menu_ouvert(mem):
            attendre = "menu ou fenêtre ouverte"
        else:
            attendre = self.events.poll(mem)
        if attendre:
            self.status_txt = f"correction en attente : {attendre}"
            return 0
        if cible == 0 and self.mort_induite is not None:
            self.mort_induite()           # deathlink : la mort qui suit n'est pas renvoyée (pas de double comptage)
        python = self.ecrire_jeu is None
        if python:
            a, b = self.pend_hist
            if a != 0 or b != 0:
                self.retry_t = now + self.RETRY
                self.status_txt = "correction en attente : variation de vie en cours"
                return 0
            try:
                r = ecrire_python(mem, corr)
            except Exception as exc:
                r = "attente"
                self._note(f"écriture refusée ({exc})")
            ok_ecrit = r == "ok"
        else:
            try:
                res = self.ecrire_jeu(corr)
            except Exception as exc:          # boîte occupée / sans réponse : rien n'a été fait
                res = None
                self.status_txt = f"correction en attente ({exc})"
            ok_ecrit = res is not None
        if not ok_ecrit:
            self.retry_t = now + self.RETRY
            if not self.status_txt.startswith("correction en attente ("):
                self.status_txt = "correction refusée par le jeu : nouvel essai"
            return 0
        with self._lock:
            self.base = cible
        self.ecrit = {"avant": lu.L, "cible": cible, "corr": corr, "python": python}
        self.n["corrections"] += 1
        self.status_txt = "à jour"
        return 1

    def status(self) -> str:
        if not self.regle:
            return "désactivée (réglage de la partie)"
        if not self.accepte:
            return "refusée sur ce PC (--no-vie-partagee)"
        lu, n = self.lu, self.n
        vie = f"{lu.L}/{lu.vmax} quarts" if lu is not None and lu.ok else "?"
        mode = "mod natif (CALL 18)" if self.ecrire_jeu is not None else "écriture directe"
        return (f"vie {vie} (commune {self.tot if self.tot is not None else '?'}) ; {n['envoyes']} envoi(s), "
                f"{n['corrections']} correction(s), {n['chargements']} chargement(s) ignoré(s), {n['fees']} fée(s) ; "
                f"{mode} ; {self.status_txt}")
