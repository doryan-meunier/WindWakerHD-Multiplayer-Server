"""Herbe coupée (synchro v3 ; étude docs/etudes/sync3_deathlink_herbe.md, la section « Vérification » prime) : une
touffe d'herbe coupée chez un joueur est coupée aussi chez les autres joueurs de la salle, sans butin en double (le
butin éventuel passe déjà par A4, « objets apparus »). Logique testable sans Cemu.

Comment le jeu gère l'herbe [DECOMP d_grass.cpp + HD-ASM] : pas d'acteur par touffe. L'acteur « kusax »/Grass
(d_a_grass) crée les touffes à partir de tables de décalages puis disparaît ; elles vivent dans un seul paquet
global ``dGrass_packet_c`` (pointeur u32 play+0x5AB8). Chaque touffe est un ``dGrass_data_c`` de 0x44 octets
(1500 dans le paquet, à +0x9C) : +0 mState (0 libre), +1 mInitFlags (bit 2 : hors champ), +2 mAnimIdx (s8 :
0-7 au repos sous le vent, 8-103 en train de plier, -1 COUPÉE), +3 mItemIdx (table de butin), +4 mPos (3 f32),
+0x40 mpNextData. Une liste chaînée par salle : ``mGrassRoom[64]`` à +0x1A5CC (têtes u32). Couper = WorkAt
(HD 0x0254A7C8) : libère l'animation éventuelle, écrit mAnimIdx = -1 (stb à 0x0254A870), particules, son, et
crée le butin (fopAcM_createItemFromTable). Ensuite calc (HD ~0x0254C88C, salle mStayNo) ne teste plus jamais la
touffe (mAnimIdx < 0), update et draw dessinent le modèle « coupé ». La salle rechargée recrée toutes ses touffes
(deleteRoom puis l'acteur Grass) : l'herbe repousse, comme dans le jeu seul.

Ce qu'on fait chez les autres : écrire mAnimIdx = -1 (un octet) dans la touffe à la même position (x, z), et
SEULEMENT si elle est au repos (0-7, aucune animation à libérer) ; relue juste avant l'écriture. Tous les chemins
du jeu qui lisent mAnimIdx le testent (< 0, < 8 ou >= 8) : -1 n'indexe jamais le tableau des animations. Pas de
particules, pas de son (il faudrait appeler du code), pas de butin (voulu : A4 l'apporte). Une écriture perdue
dans une course avec le jeu (la touffe se met à plier dans la même image) est simplement retentée ; au pire une
animation reste réservée (le jeu en a 96 : la touffe ne plie plus, rien ne casse).

Réseau : message « herbe » {zone, c: [[x, z], …]} (positions arrondies), relayé aux autres joueurs de la zone ; le
serveur garde les touffes coupées de chaque zone tant que quelqu'un y est (envoyées à qui entre, ou recharge la
salle), et les oublie quand la zone se vide (l'herbe repousse chez tous, comme dans le jeu). Options :
--share-grass (activé par --coop-donjon), --no-grass ; serveur --no-herbe. Mode Hanteurs : un Hanteur ne coupe
rien chez les autres (message bloqué). Aucune commande de la boîte aux lettres : compatible pack v17.
"""
from __future__ import annotations

import struct
import threading
import time

from jeu import progress as prog

# --- adresses HD [HD-ASM, relues dans cking.rpx par tests/test_deathlink_herbe.py] ------------------------
PACKET_PTR = prog.SYM_PLAY + 0x5AB8   # u32 dComIfGp_getGrass() (hitCheck 0x0254AAE8 : lwz r7, 0x5AB8(play))
PK_NEXT_IDX = 0x98          # u16 mNextIdx (setData 0x0254D244 : sth r28, 0x98(r26)) ; décalage HD = GC + 0x88
PK_DATA = 0x9C              # dGrass_data_c mGrassData[1500]
N_DATA = 1500
DATA_SZ = 0x44
PK_ANM = 0x18F0C            # dGrass_anm_c mGrassAnm[104] (hitCheck 0x0254AAF0 : addi r26, r26, -0x70F4 après lis 2)
N_ANM = 104
ANM_SZ = 0x38               # (mulli r12, r0, 0x38)
PK_ROOMS = 0x1A5CC          # dGrass_room_c mGrassRoom[64] (calc 0x0254C894 : lwz r29, -0x5A34(r6) après addis 2)
N_ROOMS = 64
D_STATE, D_FLAGS, D_ANIM, D_ITEM, D_POS, D_NEXT = 0x00, 0x01, 0x02, 0x03, 0x04, 0x40
ANIM_REPOS = range(0, 8)    # animations partagées du vent : rien à libérer
COUPEE = -1

TOL = 2.0                   # unités : même touffe d'un PC à l'autre (positions tirées des mêmes tables)
MAX_MSG = 64                # touffes au plus par message
MAX_ZONE = 2048             # touffes gardées au plus par zone (serveur)
POS_MAX = 1.0e6


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def valid_herbe(msg) -> list[list[int]] | None:
    """[[x, z], …] (entiers bornés, MAX_ZONE au plus) ; None si invalide."""
    c = msg.get("c") if isinstance(msg, dict) else None
    if not isinstance(c, list) or not c or len(c) > MAX_ZONE:
        return None
    out = []
    for e in c:
        if not (isinstance(e, list) and len(e) == 2 and all(_int(v) and abs(v) < POS_MAX for v in e)):
            return None
        out.append([e[0], e[1]])
    return out


def cle(x: float, z: float) -> tuple[int, int]:
    return int(round(x)), int(round(z))


def packet(mem) -> int | None:
    from jeu import game
    raw = mem.try_read(PACKET_PTR, 4)
    if raw is None:
        return None
    p = struct.unpack(">I", raw)[0]
    return p if p and game.in_mem2(p) and game.in_mem2(p + PK_ROOMS + 4 * N_ROOMS) else None


def lire_salle(mem, room: int) -> list[dict] | None:
    """Touffes de la salle ``room`` (liste chaînée du jeu) ; None si pas de paquet ou liste incohérente."""
    if not _int(room) or not 0 <= room < N_ROOMS:
        return None
    pk = packet(mem)
    if pk is None:
        return None
    raw = mem.try_read(pk + PK_ROOMS + 4 * room, 4)
    if raw is None:
        return None
    a = struct.unpack(">I", raw)[0]
    if not a:
        return []
    base = pk + PK_DATA
    blob = mem.try_read(base, N_DATA * DATA_SZ)
    if blob is None:
        return None
    out, vus = [], set()
    while a:
        o = a - base
        if o < 0 or o >= len(blob) or o % DATA_SZ or a in vus or len(out) >= N_DATA:
            return None                  # pointeur hors du tableau ou boucle : on ne touche à rien
        vus.add(a)
        st, an = blob[o], struct.unpack_from(">b", blob, o + D_ANIM)[0]
        x, y, z = struct.unpack_from(">3f", blob, o + D_POS)
        if st and all(abs(v) < POS_MAX for v in (x, y, z)):
            out.append({"a": a, "x": x, "y": y, "z": z, "anim": an, "state": st})
        a = struct.unpack_from(">I", blob, o + D_NEXT)[0]
    return out


def couper(mem, a: int, x: float, z: float) -> bool | None:
    """mAnimIdx = -1 dans la touffe ``a`` si elle est toujours là (même position), au repos (0-7). Relue juste
    avant. True : coupée ; False : déjà coupée ; None : pas maintenant (elle plie, ou elle a changé)."""
    raw = mem.try_read(a, D_POS + 12)
    if raw is None or not raw[D_STATE]:
        return None
    an = struct.unpack_from(">b", raw, D_ANIM)[0]
    px, _py, pz = struct.unpack_from(">3f", raw, D_POS)
    if abs(px - x) > TOL or abs(pz - z) > TOL:
        return None
    if an < 0:
        return False
    if an not in ANIM_REPOS:
        return None
    mem.write(a + D_ANIM, struct.pack(">b", COUPEE))
    return True


class HerbeSync:
    """Client : touffes coupées chez nous -> « herbe » ; reçues -> coupées dans notre salle."""
    SCAN = 0.25             # s entre deux relevés de la salle
    PENDING_TTL = 15.0      # s : touffe reçue introuvable (salle pas encore chargée) -> abandonnée
    ECHO_MAX = 4096

    def __init__(self, enabled: bool = False, clock=time.time):
        self.enabled = enabled
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.zone: str | None = None
        self.room = -1
        self.base = False                # premier relevé de la zone fait (état de départ)
        self.prev: dict[int, tuple[tuple[int, int], int]] = {}   # adresse -> (clé, mAnimIdx)
        self.ours: set[tuple[int, int]] = set()                  # coupées par nous sur demande : jamais renvoyées
        self.pending: dict[tuple[int, int], tuple[int, int, float]] = {}   # clé -> (x, z, heure)
        self.t_scan = float("-inf")
        self.n = {"envoyees": 0, "recues": 0, "coupees": 0}
        self.urgent = False              # latence : relevé tout de suite quand une touffe reçue arrive
        self.delays: list[float] = []    # latence (mesure) : réception -> coupe (s) des dernières touffes

    # --- files -------------------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        with self.lock:
            self.notes = (self.notes + ["herbe : " + s])[-32:]

    def take_notes(self) -> list[str]:
        with self.lock:
            out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        if not self.enabled or not isinstance(msg, dict) or msg.get("type") != "herbe":
            return
        if me is not None and msg.get("from") == me and msg.get("from") is not None:
            return
        with self.lock:
            self.inbox = (self.inbox + [dict(msg, _t=self.clock() if now is None else now)])[-64:]

    def reset_network(self) -> None:
        with self.lock:
            self.inbox.clear()
            self.out.clear()
        self.pending.clear()

    # --- étape (thread de travail) ------------------------------------------------------------------
    def step(self, mem, now: float, zone: str | None, room: int, free: bool, settled: bool, can: bool,
             monde_ok: bool = True) -> None:
        if not self.enabled:
            return
        if zone != self.zone:
            self.zone, self.room = zone, room
            self.base = False
            self.prev.clear()
            self.ours.clear()
            self.pending.clear()
            self.t_scan = float("-inf")
        with self.lock:
            inbox, self.inbox = self.inbox, []
        for m in inbox:
            if m.get("zone") != zone or zone is None:
                continue
            v = valid_herbe(m)
            if v is None:
                continue
            for x, z in v:
                self.pending.setdefault((x, z), (x, z, m["_t"]))
            self.n["recues"] += len(v)
            if self.urgent:
                # latence : touffe reçue -> relevé à ce tick-ci (lecture ; la coupe garde toutes ses gardes)
                self.t_scan = float("-inf")
        if mem is None or zone is None or now - self.t_scan < self.SCAN:
            return
        self.t_scan = now
        touffes = lire_salle(mem, room)
        if touffes is None:
            self.prev.clear()
            self.base = False
            return
        # 1) touffes coupées chez nous depuis le relevé précédent (jamais celles qu'on a coupées sur demande)
        coupees = []
        if self.base:
            for t in touffes:
                k = cle(t["x"], t["z"])
                old = self.prev.get(t["a"])
                if old is not None and old[0] == k and old[1] >= 0 and t["anim"] < 0 and k not in self.ours:
                    coupees.append(list(k))
        # 2) touffes reçues : coupées ici (jeu libre, salle stable)
        for k, (_x, _z, t0) in list(self.pending.items()):
            if now - t0 > self.PENDING_TTL:
                del self.pending[k]
        if self.pending and free and settled and can:
            grille: dict[tuple[int, int], list[dict]] = {}     # touffes par position arrondie (recherche rapide)
            for t in touffes:
                grille.setdefault(cle(t["x"], t["z"]), []).append(t)
            r_tol = int(TOL) + 1
            for k, (x, z, _t0) in list(self.pending.items()):
                cand = [t for dx in range(-r_tol, r_tol + 1) for dz in range(-r_tol, r_tol + 1)
                        for t in grille.get((x + dx, z + dz), ())
                        if abs(t["x"] - x) <= TOL and abs(t["z"] - z) <= TOL]
                if not cand:
                    continue
                t = min(cand, key=lambda t: (t["x"] - x) ** 2 + (t["z"] - z) ** 2)
                if t["anim"] < 0:
                    del self.pending[k]
                    continue
                r = couper(mem, t["a"], t["x"], t["z"])
                if r is None:
                    continue             # elle plie : retentée au prochain relevé
                del self.pending[k]
                if len(self.ours) < self.ECHO_MAX:
                    self.ours.add(cle(t["x"], t["z"]))
                if r:
                    t["anim"] = COUPEE
                    self.n["coupees"] += 1
                    self.delays = (self.delays + [now - _t0])[-32:]
        self.prev = {t["a"]: (cle(t["x"], t["z"]), t["anim"]) for t in touffes}
        self.base = True
        if coupees and monde_ok:
            for i in range(0, len(coupees), MAX_MSG):
                with self.lock:
                    self.out = (self.out + [{"type": "herbe", "zone": zone, "c": coupees[i:i + MAX_MSG]}])[-32:]
            self.n["envoyees"] += len(coupees)

    def status(self) -> str:
        n = self.n
        return (f"{n['envoyees']} touffe(s) coupée(s) ici et envoyée(s), {n['recues']} reçue(s), {n['coupees']} "
                f"coupée(s) ici sur demande" + (f", {len(self.pending)} en attente" if self.pending else ""))


class HerbeTable:
    """Serveur : touffes coupées par zone, gardées tant que la zone a quelqu'un."""

    def __init__(self):
        self.zones: dict[str, dict[tuple[int, int], None]] = {}

    def add(self, zone: str, coords: list[list[int]]) -> list[list[int]]:
        z = self.zones.setdefault(zone, {})
        new = []
        for x, y in coords:
            k = (x, y)
            if k in z or len(z) >= MAX_ZONE:
                continue
            z[k] = None
            new.append([x, y])
        return new

    def snap(self, zone: str) -> list[list[int]]:
        return [list(k) for k in self.zones.get(zone, {})]

    def forget(self, zone: str) -> int:
        return len(self.zones.pop(zone, {}) or {})
