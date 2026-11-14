"""Mécanismes rares (synchro v3 ; étude docs/etudes/sync3_mecanismes_rares.md, la section
« Vérification » de l'étude prime) : logique testable sans Cemu.

Deux synchros livrées, chacune avec son option, toutes deux activées par --coop-donjon :

  Horloge des cycles du stage (--share-cycles, --no-cycles) : le jeu fait avancer un « bit de
    programme » global (g_env_light.mSchbit, u8 0x80 -> 0x01 puis de nouveau 0x80) d'un cran toutes
    les SchSec secondes (octet +0x0F du STAG du stage) depuis le CHARGEMENT DU STAGE (dKy_Create,
    remise à 0). Il pilote : la marée de la Tour des Dieux (WLvTag -> daObjTide « Humi »), les jets de
    lave et les chutes de pierres de la Caverne du Dragon (Yfire00, frock), le vent du Temple du Vent
    (WindTag), les tags de lumière (LTag), les séismes (Quake) [HD-ASM : les 9 appelants de
    dKy_get_schbit 0x025602A8]. Deux PC qui ne sont pas entrés au même moment ont donc la marée et les
    jets de lave décalés. Le PREMIER joueur du stage qui envoie son horloge en est le propriétaire
    (serveur, CycleOwners) ; chez les autres, la phase (cran × période + minuteur) est comparée à
    celle reçue, avancée de son âge ; écart > CYCLE_TOL images -> minuteur puis bit réécrits (deux
    champs de données, lus par le jeu seulement à travers des masques : rien qui puisse planter).
    Seulement pour les stages de CYCLE_STAGES (lus dans les fichiers du jeu, SchSec > 0).
  Piliers de la Tour des Dieux (--share-pillars, --no-pillars) : daObjHcbh (proc 150, « Hcbh »,
    pilier au visage de statue, salles 14, 21, 22) cassé par une bombe, un Darknut, un Armos ou le
    marteau. Aucun switch (params 0x001FFFxx : swSave 0xFF) : rien ne passe par la progression. Chez
    celui où il casse : mBrokenBy (+0xEC0) passe de 0 à 1-4 -> « rare » {ev: pillar, key, yaw}. Chez
    les autres : pilier intact (mAppear +0xEE4 = 1, mBrokenBy = 0, procédure d'attente 0x02352BFC) ->
    itemNo des params mis à 0x3F (le butin arrive par A4 : jamais en double), mBdlYaw / mFallTimer
    comme setup_break_condition, mYaw reçu (il tombe du même côté), puis mBrokenBy = 3 EN DERNIER :
    le jeu fait le reste (éclats, son, chute, suppression) [HD-ASM 0x023527A0, 0x02352BFC].

Étudiés, non codés (voir l'étude) : miroirs Mmrr (ils ne tournent pas : bloc poussé TYPE_MIRROR,
déjà couvert par --share-blocks ; le rayon est local), statues Os (A1 relaie réveil et arrivée ;
chaque PC a sa statue), Kokiie, Mflft, Klft, Kita, Mswing, Blift, ponts de cordes.

Compatible pack v17 : écritures Python seulement (aucune commande de la boîte aux lettres).
"""
from __future__ import annotations

import math
import struct
import threading
import time

# --- horloge des cycles : g_env_light (statique HD) [HD-ASM] ------------------------------------------
# dKy_getEnvlight 0x02555D0C : lis r31, 0x1047 ; addi r3, r31, 0x5A68 -> 0x10475A68
# SetSchbit 0x0255BD4C : lbz/stb 0x109D (mSchbit), lwz 0x1084 / stw 0x1084 (mSchbitTimer),
#   SchSec = lbz 0xF(stag), startSch = (lwz 0x10(stag) >> 16) & 0xFF ; dKy_get_schbit 0x025602A8 : lbz 0x109D
ENV_LIGHT = 0x10475A68
SCH_TIMER_OFF = 0x1084      # s32 mSchbitTimer (images depuis le dernier cran)
SCH_BIT_OFF = 0x109D        # u8 mSchbit (0x80 >> cran ; 0 avant la première image du stage)
SCH_TIMER = ENV_LIGHT + SCH_TIMER_OFF
SCH_BIT = ENV_LIGHT + SCH_BIT_OFF
FPS = 30
# Stages dont le STAG a startSch != 0 et SchSec > 0 (lus dans content/Common/Stage/*_Stage.szs, STAG
# +0x0F et +0x11 ; startSch 0xFF avec SchSec 0 -- M_Dai, kindan, la mer… -- : un cran par image, rien
# à synchroniser). Valeur : SchSec (secondes par cran).
CYCLE_STAGES = {
    "Siren": 15,        # Tour des Dieux : marée (WLvTag 0x55 -> Humi*z), un cran sur deux
    "M_NewD2": 8,       # Caverne du Dragon : jets de lave Yfire00, chutes de pierres frock
    "M_Dra09": 8,       # salle de Gohma
    "kaze": 10,         # Temple du Vent : WindTag
    "GanonB": 3,
    "Cave08": 10,
    "I_SubAN": 20,
    "Adanmae": 2,
    "majroom": 6,       # Forteresse Maudite (salles intérieures)
    "ma2room": 6,
    "ma3room": 6,
}
CYCLE_BITS = tuple(0x80 >> k for k in range(8))

# --- daObjHcbh_c (proc 150), champs GC + 0x11C (prouvé par chaque accès ci-dessous) [HD-ASM] -----------
PROC_HCBH = 150
HCBH_SIZE = 0xEF0           # profil 0x101C9E40
PROCS = (PROC_HCBH,)
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_HOME, OFF_POS = 0x2EC, 0x314
HC_BDL_YAW = 0xE9C          # s16[4] mBdlYaw   (setup_break_condition 0x023527FC : addi r12, r31, 0xE9C)
HC_FALL_TIMER = 0xEAC       # s32[4] mFallTimer (0x023527E0 : addi r5, r31, 0xEA8 ; stwu 4(r5))
HC_YAW = 0xEBC              # s16 mYaw          (0x0235280C sth ; set_mtx 0x02351EA4 lha)
HC_BROKEN = 0xEC0           # s32 mBrokenBy     (checkCollision 0x023528E4 stw 3 ; wait_act_proc 0x02352C14 lwz)
HC_APPEAR = 0xEE4           # u8 mAppear        (create 0x023522D4 stb ; 0x02352534 lbz)
HC_PROC_FN = 0xEEC          # ptmf mProcFunc : {s16 adj +0xEE8, s16 idx +0xEEA, fonction +0xEEC}
HC_READ = 0xEF0
HC_WAIT_FN = 0x02352BFC     # wait_act_proc (create 0x023524A8-0x023524CC ; execute 0x023526D4)
HC_FALL_FN = 0x02352CA0     # fall_act_proc (wait_act_proc 0x02352C78-0x02352C88)
HC_BOMB = 3                 # mBrokenBy « bombe » : pas de particule de coup (particle_set)
HC_SET_ANGLE = (-0x8000, -0x4000, 0x4000, 0x0000)   # set_anglY, 0x101C9E14 [HD-ASM, DECOMP]
HC_FALL_GAIN = 15.0         # HIO 0x2C (constante 0x1002A22C = 15.0), sans terme ajouté en HD
ITEM_NONE = 0x3F            # fopAcM_createItemFromTable(itemNo 0x3F) : rien [HD-ASM 0x025D82C4]

KEY_MAX = 96


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ---------------------------------------------------------------------------------------------------
# Horloge des cycles : arithmétique de phase
# ---------------------------------------------------------------------------------------------------
def cycle_period(stage) -> int | None:
    """Images par cran pour ce stage, ou None (pas de cycle à synchroniser)."""
    s = CYCLE_STAGES.get(stage) if isinstance(stage, str) else None
    return s * FPS if s else None


def phase_of(bit: int, timer: int, period: int) -> int | None:
    """Phase en images dans le cycle de 8 crans, ou None si l'état lu n'est pas cohérent."""
    if bit not in CYCLE_BITS or not _int(timer) or not 0 <= timer < period:
        return None
    return CYCLE_BITS.index(bit) * period + timer


def from_phase(ph: int, period: int) -> tuple[int, int]:
    ph %= 8 * period
    return CYCLE_BITS[ph // period], ph % period


def phase_diff(a: int, b: int, period: int) -> int:
    """a - b ramené dans (-cycle/2, cycle/2]."""
    c = 8 * period
    d = (a - b) % c
    return d - c if d > c // 2 else d


def read_clock(mem) -> tuple[int, int] | None:
    raw = mem.try_read(SCH_TIMER, SCH_BIT_OFF - SCH_TIMER_OFF + 1)
    if raw is None or len(raw) < SCH_BIT_OFF - SCH_TIMER_OFF + 1:
        return None
    return raw[SCH_BIT_OFF - SCH_TIMER_OFF], struct.unpack_from(">i", raw, 0)[0]


def valid_cycle(msg) -> dict | None:
    """« cycle » {stage, bit, timer} (client -> serveur et relais) : None si invalide."""
    if not isinstance(msg, dict):
        return None
    stage, bit, timer = msg.get("stage"), msg.get("bit"), msg.get("timer")
    p = cycle_period(stage)
    if p is None or not _int(bit) or not _int(timer) or phase_of(bit, timer, p) is None:
        return None
    return {"stage": stage, "bit": bit, "timer": timer}


# ---------------------------------------------------------------------------------------------------
# Piliers : clés, validation, lecture
# ---------------------------------------------------------------------------------------------------
def pillar_key(room: int, params: int, home) -> str:
    """Comme mobiles.rkey : proc, salle, params, home x / z arrondis à 10 u (position du fichier)."""
    return (f"{PROC_HCBH}:{room}:{params & 0xFFFFFFFF:08X}:"
            f"{round(home[0] / 10) * 10:.0f},{round(home[2] / 10) * 10:.0f}")


def valid_rare(msg) -> dict | None:
    """« rare » {ev: pillar, key, yaw} : None si invalide."""
    if not isinstance(msg, dict) or msg.get("ev") != "pillar":
        return None
    key, yaw = msg.get("key"), msg.get("yaw")
    if not isinstance(key, str) or not 0 < len(key) <= KEY_MAX or not key.startswith(f"{PROC_HCBH}:"):
        return None
    if not _int(yaw) or not -0x8000 <= yaw <= 0x7FFF:
        return None
    return {"ev": "pillar", "key": key, "yaw": yaw}


def s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def sin_s16(a: int) -> float:
    return math.sin((a & 0xFFFF) * math.pi / 32768.0)


def fall_timers(yaw: int) -> list[int]:
    """setup_break_condition : |sin((set_anglY[i] - mYaw) >> 1)| × 15 + 0, tronqué (fctiwz)."""
    return [int(abs(sin_s16(((a - yaw) >> 1) & 0xFFFF)) * HC_FALL_GAIN) for a in HC_SET_ANGLE]


HC_TAIL = HC_BDL_YAW       # deux lectures : en-tête (0 .. +0x320) et queue (+0xE9C .. +0xEF0)


def read_pillar(mem, base: int) -> dict | None:
    head = mem.try_read(base, OFF_POS + 12)
    if head is None or len(head) < OFF_POS + 12:
        return None
    pid, = struct.unpack_from(">I", head, OFF_PC_ID)
    proc, = struct.unpack_from(">h", head, OFF_PROC)
    if proc != PROC_HCBH:
        return None
    tail = mem.try_read(base + HC_TAIL, HC_READ - HC_TAIL)
    if tail is None or len(tail) < HC_READ - HC_TAIL:
        return None

    def at(off):
        return off - HC_TAIL
    adj, idx, fn = struct.unpack_from(">hhI", tail, at(HC_PROC_FN - 4))
    return {"pid": pid, "params": struct.unpack_from(">I", head, OFF_PARAMS)[0],
            "home": struct.unpack_from(">3f", head, OFF_HOME),
            "broken": struct.unpack_from(">i", tail, at(HC_BROKEN))[0],
            "yaw": struct.unpack_from(">h", tail, at(HC_YAW))[0],
            "appear": tail[at(HC_APPEAR)], "fn": fn if idx < 0 else None}


def pillar_intact(p: dict | None) -> bool:
    return p is not None and p["appear"] == 1 and p["broken"] == 0 and p["fn"] == HC_WAIT_FN


# ---------------------------------------------------------------------------------------------------
# Serveur : propriétaire de l'horloge de chaque stage
# ---------------------------------------------------------------------------------------------------
class CycleOwners:
    """Le premier joueur du stage qui envoie son horloge en est le propriétaire, tant qu'il y reste."""

    def __init__(self):
        self.owners: dict[str, int] = {}

    def accept(self, stage: str, pid: int, present) -> bool:
        owner = self.owners.get(stage)
        if owner is None or owner not in present:
            self.owners[stage] = owner = pid
        return owner == pid

    def forget(self, alive) -> None:
        for st, pid in list(self.owners.items()):
            if pid not in alive:
                del self.owners[st]


# ---------------------------------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------------------------------
class RareSync:
    ROOM_SETTLE = 2.0       # s dans la salle (et le stage) avant toute écriture
    CYCLE_PERIOD = 1.0      # s entre deux envois de l'horloge
    CYCLE_TOL = 30          # images d'écart tolérées (1 s)
    CYCLE_GAP = 5.0         # s au moins entre deux corrections
    CYCLE_TTL = 2.5         # s : horloge reçue trop vieille -> ignorée
    CYCLE_RUN_MIN = 0.5     # s : deux horloges reçues plus rapprochées ne prouvent pas qu'elle tourne
    CYCLE_RUN_TOL = 0.5     # fraction : avance de l'horloge reçue = temps écoulé × 30 à ±50 % près
    PILLAR_TTL = 3.0        # s : pilier reçu non appliqué -> abandonné
    ECHO = 20.0             # s : pilier cassé par nous -> jamais renvoyé

    def __init__(self, cycles: bool = False, pillars: bool = False, clock=time.time):
        self.cycles_on, self.pillars_on = cycles, pillars
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.noted: set = set()
        self.zone: str | None = None
        self.stage: str | None = None
        # horloge
        self.cycle_t = float("-inf")
        self.cycle_in: dict | None = None
        self.cycle_last: tuple | None = None  # (émetteur, stage, phase, heure) de la dernière horloge reçue
        self.cycle_fix_t = float("-inf")
        # piliers
        self.prev: dict[str, int] = {}        # clé -> mBrokenBy vu au relevé précédent
        self.pending: list[dict] = []
        self.ours: dict[str, float] = {}      # clé -> heure : cassé par nous (anti-écho)
        self.n = {"cycle_fix": 0, "pillar_out": 0, "pillar_in": 0}

    @property
    def enabled(self) -> bool:
        return self.cycles_on or self.pillars_on

    # --- files --------------------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 256:
            self.noted.add(tag)
            self._note(s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def _send(self, msg: dict) -> None:
        msg.setdefault("zone", self.zone)
        with self.lock:
            self.out = (self.out + [msg])[-64:]

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        """Boucle réseau : rangé, appliqué au prochain step (thread de travail)."""
        if not isinstance(msg, dict) or (me is not None and msg.get("from") == me and msg.get("from") is not None):
            return
        m = dict(msg, _t=self.clock() if now is None else now)
        with self.lock:
            self.inbox = (self.inbox + [m])[-128:]

    def reset_network(self) -> None:
        with self.lock:
            self.inbox.clear()
            self.out.clear()
        self.pending.clear()
        self.cycle_in = None
        self.cycle_last = None

    def _drain(self) -> None:
        with self.lock:
            inbox, self.inbox = self.inbox, []
        for m in inbox:
            t = m.get("type")
            if t == "cycle" and self.cycles_on:
                v = valid_cycle(m)
                if v is not None:
                    self.cycle_in = dict(v, t=m["_t"]) if self._running(v, m.get("from"), m["_t"]) else None
            elif t == "rare" and self.pillars_on:
                v = valid_rare(m)
                if v is not None and isinstance(m.get("zone"), str):
                    self.pending = (self.pending + [dict(v, zone=m["zone"], t=m["_t"])])[-32:]

    def _running(self, v: dict, sender, t: float) -> bool:
        """L'horloge du propriétaire tourne-t-elle ? Comparée à la précédente du même émetteur : elle doit
        avoir avancé du temps écoulé (× 30 images, à ±50 %). Sinon (menu pause, émulation figée ou ralentie
        de moitié, stage rechargé chez lui, première horloge reçue) : pas de recalage sur elle. Sans cette
        garde, un propriétaire en pause figeait la marée et les jets de lave des autres (recalés en arrière
        toutes les 5 s, dans une fenêtre de 5 s) : un jet allumé pouvait barrer le passage tout ce temps."""
        p = cycle_period(v["stage"])
        ph = phase_of(v["bit"], v["timer"], p)
        last, self.cycle_last = self.cycle_last, (sender, v["stage"], ph, t)
        if last is None or last[0] != sender or last[1] != v["stage"]:
            return False
        dt = t - last[3]
        if not self.CYCLE_RUN_MIN <= dt <= self.CYCLE_TTL:
            return False
        want = dt * FPS
        return abs(phase_diff(ph, last[2], p) - want) <= self.CYCLE_RUN_TOL * want

    # --- étape (thread de travail) --------------------------------------------------------------------
    def step(self, mem, now: float, zone: str | None, stage: str | None, actors, link_pos=None,
             room: int = -1, free: bool = True, settled: bool = False, can: bool = True) -> None:
        """actors : [(id, adresse, proc, params, salle, taille, home)] (procs de PROCS), relevés ce tick."""
        if not self.enabled:
            return
        if zone != self.zone:
            self.zone = zone
            self.prev.clear()
            self.pending = [e for e in self.pending if e["zone"] == zone]
        if stage != self.stage:
            self.stage = stage
            self.cycle_in = None
            self.cycle_t = float("-inf")
        self._drain()
        if mem is None or zone is None:
            return
        ok_write = free and settled and can
        if self.cycles_on:
            self._cycle_step(mem, now, stage, ok_write)
        if self.pillars_on:
            self._pillar_step(mem, now, zone, actors or [], room, free, ok_write)

    # --- horloge ----------------------------------------------------------------------------------------
    def _cycle_step(self, mem, now: float, stage, ok_write: bool) -> None:
        p = cycle_period(stage)
        if p is None:
            return
        cur = read_clock(mem)
        if cur is None:
            return
        ph = phase_of(cur[0], cur[1], p)
        if ph is None:
            return                            # premier instant du stage (bit 0) ou lecture incohérente
        if now - self.cycle_t >= self.CYCLE_PERIOD:
            self.cycle_t = now
            self._send({"type": "cycle", "stage": stage, "bit": cur[0], "timer": cur[1]})
        e = self.cycle_in
        if e is None or e["stage"] != stage:
            return
        age = now - e["t"]
        if age > self.CYCLE_TTL or age < 0:
            self.cycle_in = None
            return
        if not ok_write or now - self.cycle_fix_t < self.CYCLE_GAP:
            return
        want = phase_of(e["bit"], e["timer"], p) + int(round(age * FPS))
        d = phase_diff(ph, want, p)
        if abs(d) <= self.CYCLE_TOL:
            return
        bit, timer = from_phase(want, p)
        try:
            mem.write(SCH_TIMER, struct.pack(">i", timer))
            mem.write(SCH_BIT, bytes([bit]))
        except Exception:
            return
        self.cycle_fix_t = now
        self.cycle_in = None
        self.n["cycle_fix"] += 1
        self._note(f"cycles du stage : horloge recalée sur celle du premier joueur arrivé ({d / FPS:+.1f} s ; "
                   f"marée, jets de lave, vent)")

    # --- piliers ----------------------------------------------------------------------------------------
    def _pillars(self, mem, actors, room: int) -> dict:
        out = {}
        for a in actors:
            pid, base, proc, params, hroom = a[0], a[1], a[2], a[3], a[4]
            home = a[6] if len(a) > 6 else None
            if proc != PROC_HCBH or hroom not in (room, -1) or home is None:
                continue
            k = pillar_key(room, params, home)
            if k in out:
                out[k] = None                 # deux piliers de même clé : ignorés
                continue
            out[k] = {"pid": pid, "base": base}
        return {k: v for k, v in out.items() if v is not None}

    def _pillar_step(self, mem, now: float, zone: str, actors, room: int, free: bool, ok_write: bool) -> None:
        pillars = self._pillars(mem, actors, room)
        for k in [k for k, t in self.ours.items() if now - t > self.ECHO]:
            del self.ours[k]
        seen = {}
        for k, o in pillars.items():
            cur = read_pillar(mem, o["base"])
            if cur is None or cur["pid"] != o["pid"]:
                continue
            seen[k] = cur["broken"]
            if self.prev.get(k) == 0 and cur["broken"] != 0 and k not in self.ours:
                self._send({"type": "rare", "ev": "pillar", "key": k, "yaw": cur["yaw"]})
                self.n["pillar_out"] += 1
        self.prev = seen
        keep = []
        for e in self.pending:
            if now - e["t"] > self.PILLAR_TTL:
                self._note("piliers : pilier cassé ailleurs, pas chez toi (salle pas prête)")
                continue
            if e["zone"] != zone:
                continue
            o = pillars.get(e["key"])
            if o is None:
                continue                      # déjà tombé et supprimé, ou pas chargé : rien à faire
            if not ok_write:
                keep.append(e)
                continue
            r = self._break(mem, o, e["yaw"])
            if r is None:
                keep.append(e)
            elif r:
                self.ours[e["key"]] = now
                self.prev[e["key"]] = HC_BOMB
                self.n["pillar_in"] += 1
                self._note("piliers : un pilier cassé chez un autre joueur tombe chez toi")
        self.pending = keep

    def _break(self, mem, o: dict, yaw: int) -> bool | None:
        """Ordre : params (itemNo 0x3F), mBdlYaw, mFallTimer, mYaw, puis mBrokenBy EN DERNIER.
        True : fait ; False : rien à faire (déjà cassé, autre acteur) ; None : réessayer."""
        cur = read_pillar(mem, o["base"])
        if cur is None or cur["pid"] != o["pid"]:
            return False
        if not pillar_intact(cur):
            return False
        b = o["base"]
        try:
            mem.write(b + OFF_PARAMS, struct.pack(">I", cur["params"] | ITEM_NONE))
            mem.write(b + HC_BDL_YAW, struct.pack(">4h", *HC_SET_ANGLE))
            mem.write(b + HC_FALL_TIMER, struct.pack(">4i", *fall_timers(yaw)))
            mem.write(b + HC_YAW, struct.pack(">h", s16(yaw)))
            mem.write(b + HC_BROKEN, struct.pack(">i", HC_BOMB))
        except Exception:
            return None
        return True

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        parts = []
        if self.cycles_on:
            parts.append(f"cycles {self.n['cycle_fix']} recalage(s)")
        if self.pillars_on:
            parts.append(f"piliers {self.n['pillar_out']} cassé(s) / {self.n['pillar_in']} rejoué(s)")
        return ", ".join(parts)
