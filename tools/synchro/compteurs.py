"""Synchro v2 : compteurs communs (rubis, flèches, bombes) et A4 « premier arrivé ».

Options client : --share-rupees (bourse commune) et --share-ammo (flèches et bombes communes), toutes deux
activées par --coop-donjon (donc par le lanceur), --no-rupees / --no-ammo pour couper. Serveur :
--no-compteurs (alias --no-counters). Étude : docs/etudes/sync2_inventaire.md (la section « Vérification »
prime) : §2.2 (compteurs « en attente » de dComIfGp), §3.4 et §3.6 (serveur par groupe, accusés, pas
d'écho), §3.8 (commande 9 COUNT du pack v19), risques R1-R5, R11, R15, R18-R21.

Ce que les joueurs voient : PC1 ramasse un rubis bleu, PC2 voit sa bourse monter de 5 (animée par le jeu) ;
PC2 achète chez Beedle, la bourse de PC1 descend du prix ; PC1 tire 3 flèches, PC2 en a 3 de moins ; une
bombe posée chez l'un en retire une chez tous. Les plafonds (bourse, carquois, sac de bombes) restent ceux
de l'inventaire partagé : personne ne dépasse le sien ; aucun compteur ne passe sous 0.

Mécanisme (côté client, ``CompteursSync``, ~5 Hz) :
* lecture ``L = sauvegarde + attente`` (rubis gi+0x24 u16 + play+0x5B48 s32, flèches gi+0x89 u8 +
  play+0x5B68 s16, bombes gi+0x8A u8 + play+0x5B6C s16) : un ramassage ou une dépense passe d'abord par
  l'attente (d_meter l'applique à l'image suivante) ; L voit les deux ;
* ``delta = L - base`` -> événement ``cnt {seq, ev, d}`` (``ev`` unique : le serveur ne compte jamais deux
  fois un même événement, renvoyé après une coupure) ; ``base := L`` ;
* réception du total commun ``tot`` avec l'accusé ``ack`` : correction seulement si ``ack`` couvre tous nos
  événements envoyés (sinon le total ne les contient pas encore), en jeu libre ; cible = min(tot, plafond
  local) ; ``corr = cible - L`` AJOUTÉ à l'attente (pack v19 : commande 9 COUNT sur le fil du jeu ; pack v17,
  mode dégradé : écriture Python de l'attente, seulement si elle a été lue nulle deux fois et relue nulle
  juste avant) ; ``base := cible`` : pas d'écho, et un ramassage fait entre la lecture et l'écriture est gardé
  (on ajoute, on n'impose pas) et part au tick suivant ;
* flèches (bombes) : seulement quand le carquois (le sac) existe ici (plafond > 0) : sans arc le jeu garde
  l'attente sans l'appliquer ; à l'obtention, ``hello`` (le serveur fait foi s'il connaît déjà le compteur) ;
* pauses (changement de stage + 2 s, combat de souvenir ``Xboss0-3`` qui remplace munitions et rubis,
  Link absent) : rien n'est lu ; à la sortie, une HAUSSE est envoyée (gain), une BAISSE est ignorée (le jeu
  efface l'attente au chargement, ``itemInit`` : correction perdue, R21) et le total du serveur recorrige
  (``sync``) ; un total redemandé toutes les 5 s (filet de sécurité) ;
* rechargement de la partie (gi, ``mDateIPL``, écran titre) ou saut absurde (plus de 1000 rubis, plus de
  99 munitions d'un coup) : nouvelle base, ``hello``, aucun delta (R2, R3).

Côté serveur (``CompteurPool``) : par groupe, total borné à [0, plafond] (plafond = max annoncé), somme des
deltas idempotente par ``ev`` ; ``hello`` : le premier fixe la valeur d'un compteur encore inconnu, ensuite le
serveur fait foi, sauf le premier ``hello`` d'un hôte (``--hote``), comme l'inventaire ; « dépense en trop »
(deux achats simultanés) journalisée ; remis à zéro par un rejoindre (A15) et gelé pendant la copie
(sinon la copie partirait en gain) : le PC du joueur 2 est ignoré, les autres gardent leurs événements et
les renvoient après le « cnt reset » (total gardé). Rien n'est « partagé » côté client avant une première
réponse du serveur (``srv_ok``) : sans serveur compatible, coffres et drops restent comme avant.

A4 « premier arrivé » (R18, ``PremierArrive``) : tant que A4 recopie un rubis lâché chez chacun, la bourse
commune compterait chaque drop deux fois. Pour les drops de rubis, flèches et bombes (seulement les compteurs
partagés ICI) : le premier joueur qui ramasse SA copie (ou l'original) l'annonce (``pickup``) ; le serveur
garde le premier, envoie ``gone`` aux autres joueurs de la zone, qui suppriment leur copie (commande 5, petit
nuage ; jamais une copie en cours de ramassage) et oublient une copie pas encore créée ; un second ramassage
quasi simultané (moins que la latence) est compté une fois : le serveur retire au total le plus petit des deux
gains (``ajuster``). Ramassage détecté : objet suivi (original publié par nous ou copie créée par nous)
disparu à moins de ``PICK_DIST`` u de Link (positions de Link des dernières 1,5 s) ou vu en démo d'obtention,
CONFIRMÉ par un gain local du même compteur dans la fenêtre (sinon : disparition par fin de vie, rien n'est
envoyé ; bourse pleine : annoncé avec un gain nul). Cœurs, magie, fées : toujours « butin par joueur ».

Coffres (contrat « propriétaire unique », R19) : un coffre à rubis / flèches / bombes ouvert par un joueur qui
compte ce compteur (drapeau ``cc`` du rapport) et reçu par un joueur qui le compte aussi ne pose que le BIT
(``coffres.translate(counted=True)``) : jamais de GRANT v19 ni d'objet à reprendre dans son coffre. Deux
ouvertures quasi simultanées du même coffre (avant que le bit ne passe) : le serveur des coffres retire au
total commun la valeur de l'objet (doublon, deux ``cc``).

Hors périmètre (étude) : butin du sac à butin (le jeu retire l'objet à 0, R25 : présence à gérer), vie max
(quarts : déjà partagés par les cadeaux et le GRANT, ne pas doubler), magie max (plafond de l'inventaire),
vie et magie actuelles (à chacun). Récompenses de PNJ et mini-jeux : chacun les reçoit dans sa partie (doublon
connu, comme le rechargement d'une sauvegarde qui rouvre un coffre).
"""
from __future__ import annotations

import math
import os
import re
import struct
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from jeu import progress as prog
from jeu import savecopy

# ---------------------------------------------------------------------------
# Compteurs [HD-ASM : item_func 0x01..0x12 relues dans le RPX, tests/test_compteurs.py ; DECOMP d_item.cpp]
# ---------------------------------------------------------------------------
KS = ("r", "a", "b")                    # rubis, flèches, bombes
NOMS = {"r": "rubis", "a": "flèches", "b": "bombes"}
MAILBOX_NOM = {"r": "rubis", "a": "fleches", "b": "bombes"}    # jeu/mailbox.COUNTERS (commande 9)
GI_RUPEE = 0x24                         # u16 mRupee
GI_ARROWS, GI_BOMBS = savecopy.GI_ARROWS, savecopy.GI_BOMBS   # u8 0x89, 0x8A
GI_WALLET, GI_ARROW_MAX, GI_BOMB_MAX = 0x32, 0x8F, 0x90
GI_BASE, GI_LEN = 0x20, 0x71            # une lecture gi+0x20 .. gi+0x90
P_RUPEE, P_ARROW, P_BOMB = 0x5B48, 0x5B68, 0x5B6C   # attentes : s32, s16, s16 (play = 0x1046F0B0)
PLAY = prog.SYM_PLAY
PLAY_BASE = prog.SYM_PLAY_ITEMS         # play+0x5B40
PLAY_LEN = 0x30                         # .. play+0x5B70
P_OFF = {"r": (P_RUPEE, 4), "a": (P_ARROW, 2), "b": (P_BOMB, 2)}
WALLET_CAP = {0: 200, 1: 1000, 2: 5000}  # dMeter_rupyMove (bourse 0/1/2)
CAPS_OK = {"r": (200, 1000, 5000), "a": (30, 60, 99), "b": (30, 60, 99)}
VAL_MAX = {"r": 5000, "a": 99, "b": 99}
DELTA_MAX = {"r": 5000, "a": 99, "b": 99}      # bornes de la commande 9 (wwmp_cnt_tab) : jamais dépassées
GARDE = {"r": 1000, "a": 99, "b": 99}          # saut d'une lecture à l'autre au-delà : rechargement / copie
STAGES_SOUVENIRS = frozenset({"Xboss0", "Xboss1", "Xboss2", "Xboss3"})

# objet -> (compteur, valeur) [item_func HD : addi de la valeur puis st sur play+0x5B48 / 0x5B68 / 0x5B6C]
VALEURS: dict[int, tuple[str, int]] = {
    0x01: ("r", 1), 0x02: ("r", 5), 0x03: ("r", 10), 0x04: ("r", 20), 0x05: ("r", 50), 0x06: ("r", 100),
    0x0F: ("r", 200), 0x1A: ("r", 10),
    0x0B: ("b", 5), 0x0C: ("b", 10), 0x0D: ("b", 20), 0x0E: ("b", 30),
    0x10: ("a", 10), 0x11: ("a", 20), 0x12: ("a", 30)}

EV_RE = re.compile(r"^[0-9a-z]{1,16}:\d{1,10}$")
SEQ_MAX = 2 ** 31


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def borne(k: str, v: int, cap: int | None) -> int:
    hi = cap if _int(cap) and cap > 0 else VAL_MAX[k]
    return max(0, min(int(v), hi))


# ---------------------------------------------------------------------------
# Validation des messages (serveur et client)
# ---------------------------------------------------------------------------
def _dict_k(x, lo_fn, hi_fn) -> dict | None:
    if x is None:
        return {}
    if not isinstance(x, dict):
        return None
    out = {}
    for k, v in x.items():
        if k not in KS or not _int(v) or not lo_fn(k) <= v <= hi_fn(k):
            return None
        out[k] = v
    return out


def valid_caps(x) -> dict:
    """Plafonds annoncés : seules les valeurs du jeu sont gardées (les autres ignorées, pas d'erreur)."""
    if not isinstance(x, dict):
        return {}
    return {k: v for k, v in x.items() if k in KS and _int(v) and v in CAPS_OK[k]}


def valid_cnt(msg) -> dict | None:
    """Message « cnt » d'un client, nettoyé ; None s'il est invalide.
    hello : {hello: 1, v: {k: valeur}, cap, ack, hote?} ; sync : {sync: 1} ; delta : {seq, ev, d, cap}."""
    if not isinstance(msg, dict):
        return None
    out = {"group": str(msg.get("group") or "default")[:32], "hello": msg.get("hello") in (1, True),
           "sync": msg.get("sync") in (1, True), "cap": valid_caps(msg.get("cap")),
           "hote": msg.get("hote") in (1, True)}
    seq = msg.get("seq", 0)
    if not (_int(seq) and 0 <= seq < SEQ_MAX):
        return None
    out["seq"] = seq
    if out["hello"]:
        v = _dict_k(msg.get("v"), lambda k: 0, lambda k: VAL_MAX[k])
        ack = msg.get("ack", 0)
        if v is None or not (_int(ack) and 0 <= ack < SEQ_MAX):
            return None
        out["v"], out["ack"] = v, ack
        return out
    if out["sync"]:
        return out
    ev, d = msg.get("ev"), _dict_k(msg.get("d"), lambda k: -DELTA_MAX[k], lambda k: DELTA_MAX[k])
    if not (isinstance(ev, str) and EV_RE.match(ev)) or not d or seq < 1:
        return None
    out["ev"], out["d"] = ev, {k: v for k, v in d.items() if v}
    return out if out["d"] else None


def valid_pickup(msg) -> dict | None:
    """Message « pickup » (A4 premier arrivé) : {zone, src, nid, no, g}."""
    if not isinstance(msg, dict):
        return None
    zone, src, nid, no, g = (msg.get(k) for k in ("zone", "src", "nid", "no", "g"))
    if not (isinstance(zone, str) and 0 < len(zone) <= 40 and _int(src) and 0 <= src < SEQ_MAX
            and _int(nid) and 0 <= nid < 1 << 32 and no in VALEURS and _int(g)):
        return None
    k, val = VALEURS[no]
    if not 0 <= g <= val:
        return None
    return {"zone": zone, "src": src, "nid": nid, "no": no, "k": k, "g": g}


# ---------------------------------------------------------------------------
# Serveur : total commun par groupe
# ---------------------------------------------------------------------------
class CompteurPool:
    """Totaux par groupe : {tot, cap, hote (compteurs fixés par un hôte), evs (identifiants déjà comptés),
    dette}. Logique pure (le serveur s'occupe des envois).

    Dette (relecture adverse) : une retenue de doublon (coffre ouvert deux fois, drop ramassé deux fois) arrive
    souvent AVANT les gains qu'elle compense (le rapport du coffre part à l'ouverture, le gain à la fin de la
    démo d'obtention) ; bornée à 0, elle était en partie perdue et le gain comptait deux fois. La part non
    retirée devient une dette, retirée des gains suivants du même compteur pendant ``DETTE_TTL`` s."""
    EV_MAX = 8192
    DETTE_TTL = 15.0

    def __init__(self):
        self.groups: dict[str, dict] = {}

    def etat(self, group: str) -> dict:
        return self.groups.setdefault(group, {"tot": {}, "cap": {}, "hote": set(), "evs": OrderedDict(),
                                              "dette": {}})

    def oublier(self, group: str) -> None:
        self.groups.pop(group, None)

    @staticmethod
    def _caps(st: dict, cap: dict) -> None:
        for k, v in cap.items():
            if v > st["cap"].get(k, 0):
                st["cap"][k] = v

    def hello(self, group: str, v: dict, cap: dict, hote: bool = False) -> list[str]:
        """Inscription : un compteur encore inconnu prend la valeur annoncée ; le premier hello d'un hôte
        l'impose ; sinon le serveur fait foi. Renvoie les compteurs dont le total a changé."""
        st = self.etat(group)
        self._caps(st, cap)
        changed = []
        for k, val in v.items():
            new = borne(k, val, st["cap"].get(k))
            if k not in st["tot"] or (hote and k not in st["hote"]):
                if st["tot"].get(k) != new:
                    changed.append(k)
                st["tot"][k] = new
            if hote:
                st["hote"].add(k)
        return changed

    def delta(self, group: str, ev: str, d: dict, cap: dict | None = None,
              now: float | None = None) -> tuple[dict, dict] | None:
        """Événement ``ev`` (deltas d) ; None si déjà compté (renvoi). Renvoie (appliqué, perdu) par
        compteur : perdu < 0 = dépense en trop (total à 0), > 0 = au-delà du plafond."""
        st = self.etat(group)
        if ev in st["evs"]:
            return None
        st["evs"][ev] = 1
        while len(st["evs"]) > self.EV_MAX:
            st["evs"].popitem(last=False)
        self._caps(st, cap or {})
        now = time.monotonic() if now is None else now
        d = dict(d)
        for k, dv in list(d.items()):
            dette = st["dette"].get(k)
            if dette is None or dv <= 0 or k not in st["tot"]:
                continue
            if now - dette[1] > self.DETTE_TTL:
                del st["dette"][k]
                continue
            use = min(dv, dette[0])
            d[k] = dv - use
            dette[0] -= use
            if dette[0] <= 0:
                del st["dette"][k]
        return self._ajouter(st, d)

    def ajuster(self, group: str, k: str, d: int, now: float | None = None) -> tuple[dict, dict]:
        """Correction du serveur (doublon de ramassage ou de coffre) : sans identifiant. Une retenue qui ne
        passe pas en entier (total trop bas : les gains qu'elle compense ne sont pas encore arrivés) laisse
        une dette sur les gains suivants."""
        st = self.etat(group)
        app, lost = self._ajouter(st, {k: d})
        if d < 0 and lost.get(k, 0) < 0 and k in st["tot"]:
            now = time.monotonic() if now is None else now
            old = st["dette"].get(k)
            reste = -lost[k] + (old[0] if old is not None and now - old[1] <= self.DETTE_TTL else 0)
            st["dette"][k] = [reste, now]
        return app, lost

    @staticmethod
    def _ajouter(st: dict, d: dict) -> tuple[dict, dict]:
        app, lost = {}, {}
        for k, dv in d.items():
            if k not in st["tot"] or not dv:
                continue                      # compteur pas encore inscrit (hello) : rien à quoi l'ajouter
            old = st["tot"][k]
            new = borne(k, old + dv, st["cap"].get(k))
            st["tot"][k] = new
            if new != old:
                app[k] = new - old
            if old + dv != new:
                lost[k] = old + dv - new
        return app, lost


# ---------------------------------------------------------------------------
# Lecture (une lecture de la sauvegarde, une des attentes)
# ---------------------------------------------------------------------------
@dataclass
class Lu:
    ok: bool
    raison: str = ""
    gi: int = 0
    date: bytes = b""
    stage: str = ""
    pause: str = ""
    save: dict = field(default_factory=dict)
    pend: dict = field(default_factory=dict)
    cap: dict = field(default_factory=dict)    # plafond local (0 = pas d'arc / de sac)

    @property
    def contexte(self) -> tuple:
        return (self.gi, self.date)

    def L(self, k: str) -> int:
        return self.save[k] + self.pend[k]


def decoder(blob: bytes, play: bytes) -> tuple[dict, dict, dict]:
    """(sauvegarde, attente, plafond) depuis gi+0x20..0x90 et play+0x5B40..0x5B70."""
    def g(off):
        return blob[off - GI_BASE]
    save = {"r": struct.unpack_from(">H", blob, GI_RUPEE - GI_BASE)[0], "a": g(GI_ARROWS), "b": g(GI_BOMBS)}
    pend = {"r": struct.unpack_from(">i", play, P_RUPEE - 0x5B40)[0],
            "a": struct.unpack_from(">h", play, P_ARROW - 0x5B40)[0],
            "b": struct.unpack_from(">h", play, P_BOMB - 0x5B40)[0]}
    cap = {"r": WALLET_CAP.get(g(GI_WALLET), -1)}
    for k, off in (("a", GI_ARROW_MAX), ("b", GI_BOMB_MAX)):
        v = g(off)
        cap[k] = v if v in CAPS_OK[k] or v == 0 else -1
    return save, pend, cap


def lire(mem) -> Lu:
    r = savecopy.partie_chargee(mem)
    if r:
        return Lu(False, r)
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    gi = struct.unpack(">I", raw)[0] if raw else 0
    st = mem.try_read(prog.SYM_CUR_STAGE, 12)
    stage = st[:8].split(b"\0", 1)[0].decode("ascii", "replace") if st else ""
    blob = mem.try_read(gi + GI_BASE, GI_LEN)
    play = mem.try_read(PLAY_BASE, PLAY_LEN)
    player = mem.try_read(prog.SYM_PLAYER, 4)
    if blob is None or play is None or player is None:
        return Lu(False, "sauvegarde illisible", gi)
    if struct.unpack(">I", player)[0] == 0:
        return Lu(False, "Link absent (chargement)", gi)
    save, pend, cap = decoder(blob, play)
    lu = Lu(True, "", gi, blob[savecopy.GI_DATE_IPL - GI_BASE:savecopy.GI_DATE_IPL - GI_BASE + 8], stage, "",
            save, pend, cap)
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        lu.pause = "changement de stage en cours"
    elif stage in STAGES_SOUVENIRS:
        lu.pause = f"combat de souvenir ({stage}) : munitions du jeu remplacées, rien n'est lu ni écrit"
    return lu


def lire_attente(mem, k: str) -> int:
    off, n = P_OFF[k]
    return struct.unpack(">i" if n == 4 else ">h", mem.read(PLAY + off, n))[0]


def ecrire_attente(mem, k: str, corr: int) -> bool:
    """Pack v17 (mode dégradé) : ``corr`` dans l'attente de ``k``, seulement si elle est relue nulle juste
    avant (verrou d'écriture du client). Le jeu l'applique à l'image suivante (d_meter)."""
    off, n = P_OFF[k]
    if not -DELTA_MAX[k] <= corr <= DELTA_MAX[k] or corr == 0:
        return False
    with prog._write_lock:
        if lire_attente(mem, k) != 0:
            return False
        mem.write(PLAY + off, struct.pack(">i" if n == 4 else ">h", corr))
    return True


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class CompteursSync:
    PERIOD = 0.2           # s entre deux lectures (~5 Hz)
    PERIOD_TOL = 0.02
    SETTLE = prog.COOP_DONJON_SETTLE
    SEND_RATE = 15         # événements envoyés au plus par seconde (serveur : CNT_RATE = 30)
    QUEUE_MAX = 512
    SYNC_EVERY = 5.0       # s : total redemandé (filet de sécurité, R21)
    GAIN_KEEP = 4.0        # s : gains locaux gardés pour le premier arrivé
    RETRY = 1.0            # s : nouvel essai d'une correction refusée (événement, attente non nulle)
    RETRY_SANS_REPONSE = 4.0   # s : boîte aux lettres sans réponse (menu pause) ou occupée
    RESEND = 3.0           # s sans nouvel accusé : les événements envoyés et non accusés sont renvoyés

    def __init__(self, ks=(), group: str = "default", clock=time.monotonic, hote: bool = False,
                 free_delay: float = prog.EVT_FREE_DELAY):
        self.ks = frozenset(k for k in ks if k in KS)
        self.enabled = bool(self.ks)
        self.group, self.clock, self.hote = group, clock, hote
        self.events = prog.EventWatch(free_delay, clock)
        self.sid = os.urandom(4).hex()      # identifiant des événements de ce client (ev = sid:seq)
        self.count = None                   # pack v19 : fn(nom, delta) -> int | None (Mailbox.count)
        self._lock = threading.Lock()
        self.base: dict | None = None       # k actif -> valeur de référence (L déjà comptée)
        self.ctx = None
        self.since = 0.0
        self.last_tick = float("-inf")
        self.paused = False
        self.lu: Lu | None = None
        self.pend_hist: tuple = (None, None)
        self.seq = 0
        self.seq_delta = 0                  # dernier seq d'un événement (delta)
        self.ack = 0
        self.queue: list[dict] = []         # événements non accusés (renvoyés à la reconnexion)
        self.sent_upto = 0
        self.sent_t: list[float] = []
        self.need_hello = True
        self.hello_msg: dict | None = None
        self.want_sync = False
        self.last_sync = float("-inf")
        self.synced = False                 # réponse au hello reçue (snap)
        # serveur compatible (une réponse « snap » reçue au moins une fois) : sans lui (ancien serveur,
        # --no-compteurs), aucun total commun n'existe, donc rien n'est « partagé » (coffres : GRANT comme
        # avant, drops : butin par joueur ; relecture adverse : un coffre à rubis restait vide chez l'autre)
        self.srv_ok = False
        self.prog_t = float("-inf")         # dernier progrès de l'accusé (ou dernier renvoi)
        self.gel = False                    # rejoindre en cours chez le serveur : pas de correction
        self.tot: dict = {}
        self.cap_srv: dict = {}
        self.retry_t: dict = {}
        self.gains: list[list] = []         # [t, k, reste] gains locaux (premier arrivé)
        self.notes: list[str] = []
        self.noted: set = set()
        self.n_env = self.n_corr = 0
        self.status_txt = "pas encore lu"
        self.why = ""

    # --- notes ----------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 512:
            self.noted.add(tag)
            self._note(s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    # --- pour les autres modules ---------------------------------------------
    def partage(self, k: str) -> bool:
        """Compteur ``k`` partagé ICI en ce moment (option active et compteur actif : arc / sac présents)."""
        base = self.base
        return bool(self.enabled and self.srv_ok and k in self.ks and base is not None and k in base)

    def compte_objet(self, item) -> bool:
        """Pour synchro/coffres.py : l'objet ``item`` (rubis, flèches, bombes) est compté par notre compteur
        commun (contrat « propriétaire unique » : le coffre ne pose alors que le bit)."""
        kv = VALEURS.get(item) if _int(item) else None
        return bool(kv and self.partage(kv[0]))

    def plein(self, k: str) -> bool:
        lu = self.lu
        return bool(lu is not None and lu.ok and k in lu.cap and lu.cap[k] > 0 and lu.L(k) >= lu.cap[k])

    def prendre_gain(self, k: str, maxi: int, t0: float, t1: float) -> int:
        """Premier arrivé : gain local de ``k`` vu entre t0 et t1, consommé (au plus ``maxi``)."""
        got = 0
        with self._lock:
            for g in self.gains:
                if g[1] == k and t0 <= g[0] <= t1 and g[2] > 0 and got < maxi:
                    n = min(g[2], maxi - got)
                    g[2] -= n
                    got += n
        return got

    # --- réseau ---------------------------------------------------------------
    def on_connect(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.need_hello, self.hello_msg, self.sent_upto, self.synced = True, None, 0, False

    def on_disconnect(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.synced = False

    def oublier(self, hello: bool = False) -> int:
        """Mode Hanteurs (potion, synchro/potion.py) : les événements pas encore accusés ne partiront jamais (file
        vidée, gains du « premier arrivé » oubliés) et ne retiennent plus les corrections (``seq_delta`` ramené à
        l'accusé : le total commun recale de nouveau le jeu). ``hello`` : réinscription, comme après une
        reconnexion (début d'une possession : le jeu du Hanteur est recalé sur le pot commun du possédé AVANT que
        ses dépenses comptent). Renvoie le nombre d'événements oubliés."""
        if not self.enabled:
            return 0
        with self._lock:
            n = len(self.queue)
            self.queue = []
            self.seq_delta = min(self.seq_delta, self.ack)
            self.gains.clear()
            if hello:
                self.need_hello, self.hello_msg, self.sent_upto, self.synced = True, None, 0, False
        return n

    def _build_hello(self) -> None:
        """hello : valeurs AVANT nos événements non accusés (le serveur, s'il ne connaît pas encore le
        compteur, prend cette valeur ; les événements renvoyés ensuite s'y ajoutent)."""
        lu = self.lu
        v = dict(self.base or {})
        for m in self.queue:
            for k, d in m["d"].items():
                if k in v:
                    v[k] -= d
        v = {k: borne(k, x, None) for k, x in v.items()}
        cap = {k: lu.cap[k] for k in v if lu is not None and lu.cap.get(k, 0) > 0} if lu else {}
        self.hello_msg = {"type": "cnt", "group": self.group, "hello": 1, "seq": 0, "ack": self.ack,
                          "v": v, "cap": cap, **({"hote": 1} if self.hote else {})}
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
                return []                     # hello pas encore construit (jeu illisible) : on attend
            self.sent_t = [t for t in self.sent_t if now - t < 1.0]
            # relecture adverse : un événement perdu en route (refusé par le débit du serveur) n'était jamais
            # renvoyé avant une reconnexion ; le serveur dédoublonne par ``ev`` : renvoyer est sans danger
            if any(m["seq"] <= self.sent_upto for m in self.queue) and not self.gel \
                    and now - self.prog_t >= self.RESEND:
                self.sent_upto = self.ack
                self.prog_t = now
            for m in self.queue:
                if m["seq"] > self.sent_upto and len(self.sent_t) < self.SEND_RATE:
                    if self.sent_upto <= self.ack:
                        self.prog_t = now             # premier envoi en attente d'accusé
                    out.append(m)
                    self.sent_t.append(now)
                    self.sent_upto = m["seq"]
                elif m["seq"] > self.sent_upto:
                    break                     # dans l'ordre
            if self.want_sync and self.synced:
                out.append({"type": "cnt", "group": self.group, "sync": 1})
                self.want_sync = False
            return out

    def receive(self, msg: dict, me=None) -> None:
        """Message « cnt » du serveur (total commun, accusé, remise à zéro, gel)."""
        if not self.enabled or msg.get("group") not in (None, self.group):
            return
        if msg.get("reset") in (1, True) and msg.get("from") is None:
            # fin d'un rejoindre : réinscription ; les événements non accusés (gardés pendant la copie) sont
            # renvoyés après le hello (relecture adverse : la file était vidée, achats et gains perdus)
            with self._lock:
                ack = msg.get("ack")
                if _int(ack):
                    self.ack = max(self.ack, ack)
                self.queue = [m for m in self.queue if m["seq"] > self.ack]
                self.tot, self.sent_upto = {}, 0
                self.need_hello, self.hello_msg, self.synced, self.gel = True, None, False, False
            self._note("compteurs : rejoindre terminé : réinscription, événements gardés renvoyés")
            return
        with self._lock:
            ack = msg.get("ack")
            if _int(ack):
                if ack > self.ack:
                    self.prog_t = self.clock()
                self.ack = max(self.ack, ack)
                self.queue = [m for m in self.queue if m["seq"] > self.ack]
            self.gel = msg.get("gel") in (1, True)
            tot = msg.get("tot")
            if isinstance(tot, dict) and not self.gel:
                self.tot = {k: v for k, v in tot.items() if k in KS and _int(v) and 0 <= v <= VAL_MAX[k]}
            self.cap_srv = valid_caps(msg.get("cap")) or self.cap_srv
            if msg.get("snap") in (1, True) and msg.get("from") is None and not self.gel:
                self.synced = self.srv_ok = True
        d = msg.get("d")
        if msg.get("from") not in (None, me) and isinstance(d, dict) and _int(d.get("r")) and d["r"] \
                and "r" in self.ks:
            who = str(msg.get("nom") or "un autre joueur")[:24]
            sens = f"+{d['r']}" if d["r"] > 0 else str(d["r"])
            self._note(f"bourse commune : {sens} rubis ({who}) -> {self.tot.get('r', '?')}")
        lost = msg.get("perdu")
        if msg.get("from") is None and isinstance(lost, dict):
            for k, v in lost.items():
                if k in KS and _int(v) and v < 0:
                    self._note(f"compteurs : dépense en trop ({-v} {NOMS[k]}, deux achats en même temps ?) : "
                               "le total commun reste à 0")

    def _push(self, d: dict, now: float) -> None:
        with self._lock:
            self.seq += 1
            self.seq_delta = self.seq
            m = {"type": "cnt", "group": self.group, "seq": self.seq, "ev": f"{self.sid}:{self.seq}", "d": d,
                 "cap": {k: self.lu.cap[k] for k in d if self.lu.cap.get(k, 0) > 0}}
            self.queue.append(m)
            if len(self.queue) > self.QUEUE_MAX:
                self.queue = self.queue[-self.QUEUE_MAX:]
            for k, v in d.items():
                if v > 0:
                    self.gains.append([now, k, v])
            self.gains = [g for g in self.gains if now - g[0] < self.GAIN_KEEP][-64:]
        self.n_env += 1

    def _rebase(self, now: float, why: str) -> None:
        with self._lock:
            self.base = None
            self.queue.clear()
            self.sent_upto = 0
            self.need_hello, self.hello_msg, self.synced = True, None, False
            self.gains.clear()
        self.since = now
        self.pend_hist = (None, None)
        if why:
            self._note(f"compteurs : {why} : valeurs renvoyées, aucun gain ni dépense déduit")

    # --- tick -----------------------------------------------------------------
    def tick(self, mem, can_write: bool = True, connected: bool = True, now: float | None = None) -> int:
        """Lecture, événements -> file, total commun -> correction. Renvoie le nombre de corrections."""
        if not self.enabled or mem is None:
            return 0
        now = self.clock() if now is None else now
        if now - self.last_tick < self.PERIOD - self.PERIOD_TOL:
            return 0
        self.last_tick = now
        try:
            lu = lire(mem)
        except Exception as exc:              # mémoire illisible pendant un chargement
            lu = Lu(False, f"lecture impossible : {exc}")
        if not lu.ok:
            self.since = now
            self.paused = self.base is not None
            self.pend_hist = (None, None)
            if lu.raison.startswith(("écran titre", "pas de sauvegarde", "vie maximale")):
                self.ctx = None               # retour au titre : la prochaine partie est un rechargement
            self.status_txt = lu.raison
            return 0
        if lu.contexte != self.ctx:
            first = self.ctx is None and self.base is None and self.seq == 0
            self.ctx = lu.contexte
            self._rebase(now, "" if first else "partie (re)chargée")
        if lu.pause:
            self.since = now
            self.paused = self.base is not None
            self.pend_hist = (None, None)
            self.status_txt = lu.pause
            return 0
        if now - self.since < self.SETTLE:
            self.status_txt = "attente de stabilité"
            return 0
        self.lu = lu
        self.pend_hist = (self.pend_hist[1], dict(lu.pend))
        active = {k for k in self.ks if lu.cap.get(k, 0) > 0}
        L = {k: lu.L(k) for k in active}
        if self.base is None:
            with self._lock:
                self.base = dict(L)
                self._build_hello()
            self.paused = False
            self.status_txt = "valeurs envoyées, attente du total commun"
            return 0
        d = {}
        with self._lock:
            new = active - set(self.base)
            for k in set(self.base) - active:
                del self.base[k]              # carquois / sac perdus (rechargement partiel ?) : plus suivi
            for k in new:
                self.base[k] = L[k]
            if new:
                self._build_hello()           # arc / sac de bombes obtenu : ce compteur rejoint le total
                self._note("compteurs : " + ", ".join(NOMS[k] for k in sorted(new)) + " partagé(e)s à partir "
                           "de maintenant")
            for k in self.base:
                dv = L[k] - self.base[k]
                if dv and abs(dv) > GARDE[k]:
                    bad = (k, dv)
                    break
            else:
                bad = None
        if bad is not None:
            self._rebase(now, f"saut de {bad[1]:+d} {NOMS[bad[0]]} d'un coup (copie ou rechargement ?)")
            self.status_txt = "nouvelle base"
            return 0
        resync = self.paused
        for k in list(self.base):
            dv = L[k] - self.base[k]
            if dv == 0:
                continue
            if resync and dv < 0:
                continue                      # attente effacée au chargement (R21) : le total recorrige
            d[k] = dv
        with self._lock:
            for k in self.base:
                self.base[k] = L[k]
        if d:
            self._push(d, now)
        with self._lock:
            if self.need_hello and self.hello_msg is None:
                self._build_hello()           # (re)connexion, « cnt reset » : valeurs renvoyées
        if resync:
            self.want_sync = True
            self.paused = False
        if now - self.last_sync >= self.SYNC_EVERY:
            self.last_sync = now
            self.want_sync = True
        return self._corriger(mem, lu, L, can_write, connected, now)

    def _corriger(self, mem, lu: Lu, L: dict, can_write: bool, connected: bool, now: float) -> int:
        self.why = ""
        with self._lock:
            ok = self.synced and not self.gel and not self.need_hello and self.hello_msg is None
            ack_ok = self.ack >= self.seq_delta
            tot = dict(self.tot)
        if not can_write:
            self.why = "écriture impossible (lecture seule ou --progress-no-apply)"
        elif not connected:
            self.why = "hors connexion"
        elif self.gel:
            self.why = "rejoindre en cours (total gelé par le serveur)"
        elif not ok:
            self.why = "attente du total commun"
        elif not ack_ok:
            self.why = "événements pas encore accusés"
        if self.why:
            self.status_txt = self.why
            return 0
        plan = []
        for k in sorted(self.base or {}):
            if k not in tot:
                continue
            cible = borne(k, tot[k], lu.cap[k])
            # bornes de la commande 9 (wwmp_cnt_tab) : un écart plus grand (sauvegarde trafiquée) se corrige
            # en plusieurs passes ; la base suit ce qui est réellement écrit
            corr = max(-DELTA_MAX[k], min(DELTA_MAX[k], cible - L[k]))
            if corr and now >= self.retry_t.get(k, float("-inf")):
                plan.append((k, L[k] + corr, corr))
        if not plan:
            self.status_txt = "à jour"
            return 0
        busy = self.events.poll(mem)
        if busy:
            self.why = self.status_txt = f"en attente : {busy}"
            return 0
        done = 0
        for k, cible, corr in plan:
            if self._ecrire(mem, k, corr, now):
                with self._lock:
                    self.base[k] = cible
                done += 1
                self.n_corr += 1
            else:
                self.retry_t[k] = max(self.retry_t.get(k, now), now + self.RETRY)
        self.status_txt = "à jour" if done == len(plan) else "correction en attente"
        return done

    def _ecrire(self, mem, k: str, corr: int, now: float) -> bool:
        if self.count is not None:
            try:
                res = self.count(MAILBOX_NOM[k], corr)
            except Exception as exc:          # boîte occupée / sans réponse (neutralisée : rien n'est fait)
                self._once(("count", k, type(exc).__name__), f"compteurs : {NOMS[k]} pas encore corrigé(e)s "
                                                             f"({exc}) : nouvel essai")
                # sans réponse (menu pause : le hook ne tourne pas), chaque essai bloque le fil de travail
                # jusqu'au délai de la boîte : on espace davantage
                self.retry_t[k] = now + self.RETRY_SANS_REPONSE
                return False
            return res is not None             # None : événement en cours chez le jeu
        # pack v17 : attente lue nulle aux deux dernières lectures, et relue nulle juste avant
        a, b = self.pend_hist
        if not a or not b or a.get(k) != 0 or b.get(k) != 0:
            return False
        try:
            return ecrire_attente(mem, k, corr)
        except Exception as exc:
            self._once(("ecr", k), f"compteurs : écriture refusée ({exc})")
            return False

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        lu, parts = self.lu, []
        for k in KS:
            if k not in self.ks:
                continue
            if self.base is not None and k in self.base and lu is not None:
                parts.append(f"{NOMS[k]} {lu.L(k)} (commun {self.tot.get(k, '?')})")
            else:
                parts.append(f"{NOMS[k]} pas encore")
        mode = "COUNT (pack v19)" if self.count is not None else "écriture directe (pack v17)"
        return f"{', '.join(parts)} ; {self.n_env} envoi(s), {self.n_corr} correction(s) ; {mode} ; {self.status_txt}"


# ---------------------------------------------------------------------------
# A4 « premier arrivé » (client)
# ---------------------------------------------------------------------------
def _dist(a, b) -> float:
    try:
        return math.dist(tuple(a)[:3], tuple(b)[:3])
    except (TypeError, ValueError):
        return float("inf")


class PremierArrive:
    """Suit les drops de rubis / flèches / bombes relayés par A4 (nos originaux publiés, nos copies créées)
    et annonce celui que NOTRE Link ramasse ; supprime notre exemplaire quand un autre l'a pris."""
    PICK_DIST = 150.0      # u : objet disparu à cette distance d'une position récente de Link = ramassé
    TRAIL = 1.5            # s de positions de Link gardées
    GAIN_BEFORE = 1.6      # s : gain local accepté avant la disparition vue (rescan, lecture à 5 Hz)
    WAIT_GAIN = 1.2        # s : attente d'un gain local après la disparition
    UNSEEN = 3.0           # s : exemplaire jamais vu (pas encore rescanné) au-delà : oublié
    KEEP = 30.0            # s : suivi au plus (un drop disparaît seul en ~10 s)
    MAX = 256

    def __init__(self, compteurs: CompteursSync):
        self.c = compteurs
        self.track: dict[int, dict] = {}   # pid local -> {src, nid, no, t, seen, pos}
        self.cands: list[dict] = []
        self.trail: list[tuple[float, tuple]] = []
        self.zone = None
        self._lock = threading.Lock()
        self.gone_q: list[tuple] = []      # (src, nid, nom) reçus (fil réseau), traités par le fil de travail
        self.n_claims = self.n_gone = 0

    @property
    def enabled(self) -> bool:
        return self.c.enabled

    def actif(self, no) -> bool:
        kv = VALEURS.get(no) if _int(no) else None
        return bool(kv and self.c.partage(kv[0]))

    def publie(self, entries, now: float, zone) -> None:
        """Nos objets publiés par A4 dans ``zone`` (nid = notre pid)."""
        for e in entries or []:
            if e.get("kind") == "fee" or not self.actif(e.get("no")) or len(self.track) >= self.MAX:
                continue
            self.track[int(e["nid"])] = {"src": None, "nid": int(e["nid"]), "no": e["no"], "t": now, "zone": zone,
                                         "seen": True, "pos": tuple(e.get("pos") or (0.0, 0.0, 0.0))}

    def cree(self, pid: int, sender, e: dict, now: float, zone) -> None:
        """Copie créée chez nous (commande 4), dans ``zone``, d'un objet né chez ``sender``."""
        if pid is None or e.get("kind") == "fee" or not self.actif(e.get("no")) or not _int(sender) \
                or len(self.track) >= self.MAX:
            return
        self.track[int(pid)] = {"src": sender, "nid": int(e["nid"]), "no": e["no"], "t": now, "zone": zone,
                                "seen": False, "pos": tuple(e.get("pos") or (0.0, 0.0, 0.0))}

    def observe(self, now: float, zone, items, link_pos, me) -> list[dict]:
        """Un passage (fil de travail, à chaque échantillon) : renvoie les messages « pickup » à envoyer."""
        if zone is not None:                  # relevé manqué (Link absent une image) : rien n'est oublié
            if zone != self.zone:
                self.zone = zone
                self.trail.clear()
            for pid in [p for p, t in self.track.items() if t["zone"] != zone]:
                del self.track[pid]           # autre salle : nos exemplaires ne sont plus chargés
        if link_pos is not None:
            self.trail.append((now, tuple(link_pos)))
        self.trail = [p for p in self.trail if now - p[0] <= self.TRAIL]
        if items is not None and link_pos is not None and zone is not None:
            by = {it.get("pid"): it for it in items}
            for pid, t in list(self.track.items()):
                it = by.get(pid)
                if now - t["t"] > self.KEEP:
                    del self.track[pid]
                    continue
                if it is not None:
                    t["seen"] = True
                    if it.get("pos") is not None:
                        t["pos"] = tuple(it["pos"])
                    if it.get("status", 0) >= 5:          # démo d'obtention / ramassé : c'est nous
                        del self.track[pid]
                        self.cands.append(dict(t, tc=now, sur=True))
                    continue
                if not t["seen"]:
                    if now - t["t"] > self.UNSEEN:
                        del self.track[pid]
                    continue
                del self.track[pid]
                if min((_dist(t["pos"], p) for _t, p in self.trail), default=float("inf")) <= self.PICK_DIST:
                    self.cands.append(dict(t, tc=now, sur=False))
        return self._resoudre(now, zone, me)

    def _resoudre(self, now: float, zone, me) -> list[dict]:
        out, keep = [], []
        for c in self.cands:
            k, val = VALEURS[c["no"]]
            g = self.c.prendre_gain(k, val, c["tc"] - self.GAIN_BEFORE, now)
            src = me if c["src"] is None else c["src"]
            if g == 0 and now - c["tc"] < self.WAIT_GAIN:
                keep.append(c)
                continue
            if g == 0 and not (self.c.plein(k) or c["sur"]):
                continue                      # disparu sans gain : fin de vie de l'objet, pas un ramassage
            if not _int(src) or c.get("zone") is None:
                continue                      # pas d'identifiant réseau (hors connexion) : rien à annoncer
            out.append({"type": "pickup", "zone": c["zone"], "src": src, "nid": c["nid"], "no": c["no"], "g": g})
            self.n_claims += 1
        self.cands = keep
        return out

    def recu_gone(self, msg: dict) -> None:
        """Fil réseau : un autre joueur a ramassé (src, nid)."""
        src, nid, no = msg.get("src"), msg.get("nid"), msg.get("no")
        if _int(src) and _int(nid) and self.actif(no):
            # relecture adverse : un joueur qui ne partage pas ce compteur (--no-rupees, pas d'arc) garde son
            # exemplaire, et sa copie encore à créer (butin par joueur)
            with self._lock:
                self.gone_q = (self.gone_q + [(src, nid, str(msg.get("nom") or "un autre joueur")[:24])])[-64:]

    def a_retirer(self, me) -> list[tuple[int | None, tuple, str]]:
        """Fil de travail : [(pid local ou None, (src, nid), nom)] pour chaque « gone » reçu. pid None : pas
        d'exemplaire chargé ici (copie peut-être encore à créer : à oublier côté DropApply)."""
        with self._lock:
            q, self.gone_q = self.gone_q, []
        out = []
        for src, nid, who in q:
            pid = None
            for p, t in list(self.track.items()):
                if t["nid"] == nid and (t["src"] == src or (t["src"] is None and src == me)):
                    pid = p
                    del self.track[p]
                    break
            self.cands = [c for c in self.cands if not (c["nid"] == nid and (c["src"] == src or
                                                                            (c["src"] is None and src == me)))]
            out.append((pid, (src, nid), who))
            self.n_gone += 1
        return out

    def status(self) -> str:
        return (f"{len(self.track)} drop(s) suivi(s), {self.n_claims} ramassé(s) ici, "
                f"{self.n_gone} pris ailleurs")
