"""Obstacles destructibles des donjons (SYNCHRO.md A14, --share-obstacles, activé par
--coop-donjon, --no-obstacles) : logique testable sans Cemu.

Un obstacle cassé chez un joueur doit l'être aussi chez l'autre. Les obstacles traités ici
SAUVENT un switch quand ils cassent ; ce switch part déjà chez l'autre joueur par la
progression partagée (A1, switches 0x00-0x7F de la stage) ou par le relais des switches
temporaires (A7, 0x80-0xEF), et le serveur le garde pour qui arrive plus tard. Mais l'acteur
déjà chargé ne relit ce switch qu'à sa création : il reste intact jusqu'au prochain
chargement de la salle. Aucun message réseau n'est donc nécessaire : chaque client cherche,
dans SA salle, l'obstacle « intact alors que son switch est posé » et le fait casser
(docs/etudes/obstacles.md). Le jeu fait la même chose au chargement de la salle (l'obstacle
naît cassé, ou n'est pas créé), d'où la sûreté : on n'anticipe que ce qu'il ferait.

  Ksaku / Dsaku (daSaku, proc 398) : barricade de planches (Pic du Dragon, Bois Défendus).
    Deux moitiés, un switch chacune (params bits 8-15 et 16-23). Casse à l'épée, à la bombe,
    brûle au feu (bâton enflammé, flèche de feu). Chez nous : la barricade est RECRÉÉE
    (commande 4, mêmes paramètres, même position et angle) puis l'ancienne est supprimée
    (commande 5) : la nouvelle lit les switches et naît dans l'état de l'autre joueur (moitié
    cassée : restes et collision du jeu ; moitié intacte : intacte). Pas d'éclats ni de son
    du jeu (il faudrait un pack v18, CALL broken() / burn()) : un petit nuage à la place.
    La remplaçante naît dans la couche de Link (fpcBs_Execute : couche courante = celle de
    l'acteur qui exécute le hook), pas dans celle de la salle : d_s_room ne la supprime pas
    au déchargement. Elle est donc suivie (`created`, gardé tant qu'on reste dans la stage)
    et supprimée par nous quand la zone de sa salle n'est plus chargée, ou quand le jeu a
    recréé la barricade de la salle (jumelle cohérente, salle rechargée).
  Ebrock / Ekao / Ebrock2 (daStone2, proc 455) : rochers et blocs qui cassent à la bombe ou
    une fois soulevés et lancés. Switch posé à la casse ET dès qu'on le soulève. Chez nous :
    cassé PAR LE JEU (éclats, fumée, son, vibration) : itemNo = 0x3F (rien ne tombe : A4
    relaie l'objet de l'autre) puis mode m648 = 2 (« lâché ») ; à l'image suivante le jeu
    teste le sol et appelle damaged(2), puis supprime l'acteur à la fin de la fumée. Repli si
    toujours là après BREAK_WAIT s : suppression (commande 5) et petit nuage.
  Wall (daWall, proc 429, Tour des Dieux) : mur fissuré qui casse à la bombe. Chez nous :
    suppression (commande 5, le jeu libère la collision) et petit nuage ; jamais d'écriture
    de mState (sa collision ne serait plus libérée).

Gardes (relues juste avant chaque écriture ou commande) : jeu libre (ni événement, ni
changement de stage), boîte aux lettres sans requête en attente (client), salle stable depuis
ROOM_SETTLE s, même acteur (id de process, proc, paramètres, taille d'instance HD attendue,
champ recopié des paramètres concordant : `consistent`), obstacle encore intact (état relu), switch encore
posé dans la mémoire vivante, état « intact + switch posé » vu depuis au moins CONFIRM s (un
obstacle que NOTRE joueur casse pose son switch dans la même image où il change d'état : une
lecture à cheval ne déclenche rien). Rocher porté par Link (statut CARRY) : jamais touché.
Au plus MAX_TRIES essais (créations comprises) par obstacle et par visite de la salle ; jamais
de deuxième remplaçante tant qu'une jumelle (même proc, paramètres et origine) existe ou
qu'une création est peut-être en attente : on ne fait alors que supprimer l'ancienne.

Switches d'acteur (A7, `owned_switches`) : le jet de lave Yfire00 (daObjFlame, proc 267)
réécrit son switch swSave à chaque image (1 pendant le jet, 0 sinon, ~16 s de période dans
le Pic du Dragon, salle 2 : 0xE0). Le relayer ne sert à rien : ces switches sont signalés à
TempSwitchRelay.set_owned et ne sont ni envoyés ni posés.

Non traités (docs/etudes/obstacles.md) : tonneaux et caisses (rien de sauvé : il faudrait un
message réseau dédié), pont de cordes (état par planche), obstacle sans switch (0xFF),
piliers Hcbh (offsets HD non relevés).
"""
from __future__ import annotations

import math
import struct
import time

from jeu import progress as prog
from commun.protocole import enemy_key

# --- fopAc_ac_c (HD = GC + 0x11C) -------------------------------------------------------------
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_STATUS = 0x2E0         # u32 actor_status [HD-ASM]
STATUS_CARRY = 0x2000      # fopAcStts_CARRY_e : porté par Link
OFF_HOME = 0x2EC           # home : pos f32[3], angle s16[3] (+0xC), roomNo s8 (+0x12)
OFF_SCALE = 0x330          # f32[3] scale (GC 0x214 + 0x11C)
_HEAD = 0x33C              # une lecture : en-tête, paramètres, statut, home, échelle

# --- daSaku (proc 398, Ksaku / Dsaku), instance HD 0x1024 = GC 0xF08 + 0x11C [HD-ASM + LIVE] ---
PROC_SAKU = 398
SAKU_SIZE = 0x1024
SAKU_STATE = 0x1014        # s32[2] par moitié : 0 absente, 1 intacte, 2 brûle, 3 cassée
SAKU_SW = 0x101C           # u32[2] switch de la moitié basse / haute (recopie des params)
SAKU_INTACT = 1

# --- daStone2 (proc 455, Ebrock / Ekao / Ebrock2), instance HD 0x7C4 (champs GC + 0x118) -----
PROC_STONE2 = 455
STONE2_SIZE = 0x7C4
STONE2_TYPE = 0x75C        # s32 m644 : 0-2 Ebrock, 3 Ekao, 4 Ebrock2 [LIVE 2 / 4]
STONE2_MODE = 0x760        # s32 m648 : 0 posé, 1 porté, 2 lâché, 3 cassé [HD-ASM lwz 0x760]
STONE2_DEMO = 0x764        # s32 m64C : 0 pas d'événement demandé
STONE2_WAIT, STONE2_DROP, STONE2_FINE = 0, 2, 3
STONE2_ITEM_MASK = 0x3F    # itemNo (6 bits bas des paramètres) ; 0x3F = rien

# --- daWall (proc 429, Tour des Dieux), instance HD 0x700 = GC 0x5E4 + 0x11C [HD-ASM partiel] ---
PROC_WALL = 429
WALL_SIZE = 0x700
WALL_STATE = 0x6C8         # u8 mState : 0 intact (collision enregistrée), 1 cassé
WALL_SW = 0x6F8            # s32 mSwitchNo (GC 0x5DC + 0x11C) = params & 0xFF : vérification croisée

# --- daObjFlame (proc 267, Yfire00 : jet de lave), instance HD 0x59C [LIVE + HD-ASM] ----------
PROC_FLAME = 267
FLAME_SIZE = 0x59C

ARMED, FAIL, RACE = "arme", "echec", "course"     # résultats de break_stone2

SIZES = {PROC_SAKU: SAKU_SIZE, PROC_STONE2: STONE2_SIZE, PROC_WALL: WALL_SIZE}
PROCS = (PROC_SAKU, PROC_STONE2, PROC_WALL, PROC_FLAME)      # relevés par le client
NAMES = {PROC_SAKU: "barricade de planches", PROC_STONE2: "rocher", PROC_WALL: "mur fissuré"}
NO_SWITCH = 0xFF


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def saku_switches(params: int) -> tuple[int, int]:
    """daSaku_prm : switch de la moitié basse (bits 8-15), de la moitié haute (bits 16-23)."""
    return (params >> 8) & 0xFF, (params >> 16) & 0xFF


def stone2_switch(params: int) -> int:
    return (params >> 8) & 0xFF


def wall_switch(params: int) -> int:
    return params & 0xFF


def flame_switch(params: int) -> int:
    return (params >> 16) & 0xFF


def owned_switches(actors) -> dict:
    """A7 : switches temporaires réécrits à chaque image par un acteur chargé (jet de lave
    Yfire00). actors : [(id, adresse, proc, params, salle d'origine, taille)] -> {(salle, sw): nom}
    (salle -1 pour un switch de donjon 0x80-0xBF)."""
    out = {}
    for a in actors or []:
        if len(a) < 5 or a[2] != PROC_FLAME or not _int(a[3]) or not _int(a[4]):
            continue
        sw = flame_switch(a[3])
        if prog.TSW_DAN_FIRST <= sw < prog.TSW_ZONE_FIRST:
            out[(-1, sw)] = "jet de lave Yfire00"
        elif prog.TSW_ZONE_FIRST <= sw < prog.TSW_END and 0 <= a[4] < 64:
            out[(a[4], sw)] = "jet de lave Yfire00"
    return out


# ---------------------------------------------------------------------------------------------
# Lecture (une seule passe par acteur)
# ---------------------------------------------------------------------------------------------
def read_obstacle(mem, base: int) -> dict | None:
    """Relit un obstacle : None si l'acteur n'existe plus (mBsType 0) ou n'est pas lisible."""
    if not _int(base) or not 0x10000000 <= base < 0x50000000:
        return None
    raw = mem.try_read(base, _HEAD)
    if raw is None or len(raw) < _HEAD:
        return None
    bs_type, pid = struct.unpack_from(">II", raw, 0)
    proc, = struct.unpack_from(">h", raw, OFF_PROC)
    if bs_type == 0 or proc not in SIZES:
        return None
    o = {"base": base, "pid": pid, "proc": proc,
         "params": struct.unpack_from(">I", raw, OFF_PARAMS)[0],
         "status": struct.unpack_from(">I", raw, OFF_STATUS)[0],
         "home": struct.unpack_from(">3f", raw, OFF_HOME),
         "angle": struct.unpack_from(">3h", raw, OFF_HOME + 0xC),
         "room": struct.unpack_from(">b", raw, OFF_HOME + 0x12)[0],
         "scale": struct.unpack_from(">3f", raw, OFF_SCALE)}
    if proc == PROC_SAKU:
        r = mem.try_read(base + SAKU_STATE, 0x10)
        if r is None:
            return None
        o["state"] = list(struct.unpack(">2i", r[:8]))
        o["sw"] = list(struct.unpack(">2I", r[8:]))
    elif proc == PROC_STONE2:
        r = mem.try_read(base + STONE2_TYPE, 0xC)
        if r is None:
            return None
        o["type"], o["mode"], o["demo"] = struct.unpack(">3i", r)
    elif proc == PROC_WALL:
        r = mem.try_read(base + WALL_STATE, WALL_SW + 4 - WALL_STATE)
        if r is None:
            return None
        o["state"] = r[0]
        o["sw"] = struct.unpack_from(">i", r, WALL_SW - WALL_STATE)[0]
    return o


def consistent(o: dict) -> bool:
    """Vérification croisée de la disposition HD : un champ que le jeu recopie des paramètres
    à la création doit concorder (sinon offsets faux pour cette version : jamais touché)."""
    proc = o.get("proc")
    if proc == PROC_SAKU:
        return tuple(o["sw"]) == saku_switches(o["params"]) and all(0 <= v <= 3 for v in o["state"])
    if proc == PROC_STONE2:
        return o["type"] == (o["params"] >> 24) & 7 and 0 <= o["mode"] <= 3
    if proc == PROC_WALL:
        return o["sw"] == wall_switch(o["params"]) and o["state"] in (0, 1)
    return False


def switch_on(mem, gi: int, sw: int, room: int):
    """fopAcM_isSwitch(acteur, sw) relu dans la mémoire VIVANTE (salle d'origine de l'acteur) :
    True / False, None si illisible ou sans switch (0xFF, 0xF0-0xFE)."""
    try:
        if 0 <= sw < 0x80:
            raw = mem.try_read(gi + prog.GI_CUR_MEMORY + 0x04 + 4 * (sw >> 5) + 3 - ((sw & 31) >> 3), 1)
            return None if raw is None else bool(raw[0] >> (sw & 7) & 1)
        if prog.TSW_DAN_FIRST <= sw < prog.TSW_END:
            kind, off, mask = prog.tsw_byte(sw)
            if kind == "dan":
                raw = mem.try_read(gi + prog.GI_DAN, prog.DAN_SWITCH + prog.DAN_SWITCH_SIZE)
                if raw is None or not 0 <= struct.unpack_from(">b", raw, 0)[0] < prog.N_MEM:
                    return None
                return bool(raw[off] & mask)
            base = prog._zone_base(mem, gi, room)
            if base is None:
                return None
            raw = mem.try_read(base + off, 1)
            return None if raw is None else bool(raw[0] & mask)
    except Exception:
        return None
    return None


def stale_parts(o: dict, is_on) -> list[int]:
    """Parties d'un obstacle intactes alors que leur switch est posé (is_on(sw) -> bool|None).
    daSaku : moitiés [0 basse, 1 haute] ; autres : [0] ; [] = rien à faire."""
    proc = o.get("proc")
    if proc == PROC_SAKU:
        out = []
        for i in (0, 1):
            sw = saku_switches(o["params"])[i]
            if o["state"][i] == SAKU_INTACT and sw != NO_SWITCH and is_on(sw) is True:
                out.append(i)
        return out
    if proc == PROC_STONE2:
        sw = stone2_switch(o["params"])
        if o["mode"] == STONE2_WAIT and not o["status"] & STATUS_CARRY and sw != NO_SWITCH \
                and is_on(sw) is True:
            return [0]
        return []
    if proc == PROC_WALL:
        sw = wall_switch(o["params"])
        if o["state"] == 0 and sw != NO_SWITCH and is_on(sw) is True:
            return [0]
    return []


def recreatable(o: dict) -> str:
    """daSaku : la commande 4 ne passe ni angle x / z ni échelle : '' si la recréation donne la
    même barricade, sinon la raison."""
    if any(a != 0 for a in (o["angle"][0], o["angle"][2])):
        return "barricade inclinée (angle x / z)"
    if any(not math.isfinite(v) or abs(v - 1.0) > 1e-3 for v in o["scale"]):
        return "barricade mise à l'échelle"
    if not all(math.isfinite(v) and abs(v) < 1e6 for v in o["home"]) or not 0 <= o["room"] < 64:
        return "position d'origine invalide"
    return ""


def game_free(mem) -> str:
    """Relecture brute juste avant d'écrire : '' = libre."""
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        return "changement de stage en cours"
    return prog.event_busy(mem)


def _gi(mem) -> int | None:
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return None
    gi = struct.unpack(">I", raw)[0]
    return gi if 0x10000000 <= gi < 0x50000000 else None


def _same(o: dict | None, expect: dict) -> bool:
    return o is not None and o["pid"] == expect["pid"] and o["proc"] == expect["proc"] \
        and o["params"] == expect["params"] and consistent(o)


def break_stone2(mem, gi: int, expect: dict) -> tuple[str, str]:
    """Rocher : revalidation puis itemNo = 0x3F et mode = 2 (le jeu le casse à l'image suivante).
    Renvoie (ARMED | FAIL | RACE, raison)."""
    o = read_obstacle(mem, expect["base"])
    if not _same(o, expect):
        return FAIL, "acteur changé"
    if not stale_parts(o, lambda sw: switch_on(mem, gi, sw, o["room"])):
        return FAIL, "plus à casser (porté, déjà cassé ou switch effacé)"
    why = game_free(mem)
    if why:
        return FAIL, why
    try:
        if o["params"] & STONE2_ITEM_MASK != STONE2_ITEM_MASK:
            mem.write(o["base"] + OFF_PARAMS, struct.pack(">I", o["params"] | STONE2_ITEM_MASK))
        mem.write(o["base"] + STONE2_MODE, struct.pack(">i", STONE2_DROP))
    except Exception as exc:                  # mémoire en lecture seule…
        return FAIL, f"écriture impossible ({exc})"
    o2 = read_obstacle(mem, expect["base"])
    if o2 is None or o2["pid"] != expect["pid"]:
        return RACE, "disparu pendant l'écriture"
    return ARMED, ""



# ---------------------------------------------------------------------------------------------
# État (thread de travail du client)
# ---------------------------------------------------------------------------------------------
def _stage_of(ctx):
    """Stage d'un contexte : (zone « stage:salle », heure de salle) ou zone seule."""
    z = ctx[0] if isinstance(ctx, tuple) and ctx else ctx
    return z.rsplit(":", 1)[0] if isinstance(z, str) else z


def room_loaded(mem, gi: int, room: int) -> bool | None:
    """Zone de la salle `room` chargée (relue à l'instant) ; None si illisible. Une zone n'est
    créée qu'avec sa salle (d_s_room) : zone absente = salle déchargée (l'inverse n'est pas vrai,
    la zone survit un moment à sa salle)."""
    if not 0 <= room < 64:
        return None
    raw = mem.try_read(gi + prog.GI_ZONE, prog.ZONE_SIZE * prog.N_ZONES)
    if raw is None:
        return None
    return any(struct.unpack_from(">b", raw, z * prog.ZONE_SIZE)[0] == room for z in range(prog.N_ZONES))


class ObstacleSync:
    PERIOD = 0.5          # s entre deux relevés
    CONFIRM = 0.4         # s : « intact + switch posé » vu depuis au moins CONFIRM s (deux relevés)
    # latence (docs/etudes/latence.md) : un switch vient d'être posé chez nous par la synchro (kick) : pendant
    # FAST_FOR s, relevés toutes les PERIOD_FAST s et action après CONFIRM_FAST s, soit TROIS relevés
    # concordants (0 ; 0,1 ; 0,2 s) au lieu de deux espacés de 0,5 s. Toutes les autres gardes sont inchangées.
    PERIOD_FAST, CONFIRM_FAST, FAST_FOR = 0.1, 0.15, 3.0
    FAST_N = 3            # relevés concordants comptés avant d'agir en cadence rapide
    ROOM_SETTLE = 2.0     # s dans la salle avant d'agir (acteurs de la salle tous créés)
    RETRY = 1.0           # s entre deux essais sur le même obstacle
    MAX_TRIES = 3
    BREAK_WAIT = 1.0      # s : rocher armé encore intact -> repli (suppression)
    ARMED_MAX = 10.0      # s : rocher armé suivi au plus (fumée finie bien avant)
    PUFF_UP = 60.0        # u au-dessus de l'origine pour le petit nuage
    CREATED_GRACE = 3.0   # s : remplaçante jamais touchée (le temps de naître et d'être relevée)
    CREATED_TTL = 15.0    # s : remplaçante jamais vue au relevé -> oubliée (création perdue)
    MAYBE_TTL = 60.0      # s : création peut-être restée en attente (exception) -> attendue au plus
    BLOCK_NOTE = 10.0     # s : obstacle à casser bloqué depuis -> une note au journal

    def __init__(self, enabled: bool = True, puff: bool = True, clock=time.time):
        self.enabled, self.puff, self.clock = enabled, puff, clock
        self.ctx = None
        self.stage = None
        self.last = float("-inf")
        self.fast_until = float("-inf")           # latence : relevés rapprochés jusqu'à cette heure (kick)
        # par visite de la salle (oubliés à chaque changement de contexte)
        self.seen: dict[int, float] = {}          # id -> première fois vu « intact + switch »
        self.seen_n: dict[int, tuple[float, int]] = {}   # latence : id -> (t0 de `seen`, relevés concordants)
        self.tries: dict[str, int] = {}           # clé -> essais (créations comprises) pendant cette visite
        self.next_try: dict[str, float] = {}
        self.given_up: set = set()
        self.breaking: dict[int, dict] = {}       # rochers armés : id -> {t, o}
        self.blocked: dict[str, float] = {}       # clé -> début du blocage (cinématique, boîte occupée…)
        # pour toute la stage (les acteurs créés par nous ne partent pas avec leur salle)
        self.to_delete: dict[int, dict] = {}      # barricades remplacées à supprimer : id -> {o, n}
        self.created: dict[int, dict] = {}        # remplaçantes créées par nous : id -> {key, room, t, seen, n}
        self.maybe: dict[str, tuple] = {}         # clé -> (heure, ids connus) : création peut-être en attente
        self.deleted: set = set()                 # ids supprimés par nous (la commande 5 est différée)
        self.noted: set = set()                   # notes uniques
        self.notes: list[str] = []
        self.native = self.removed = self.recreated = self.failed = self.skipped = self.cleaned = 0
        self.why = ""

    def _releves(self, pid: int, t0: float) -> int:
        """Latence : nombre de relevés concordants de suite depuis t0 (repart de 1 quand `seen` a été recompté)."""
        old = self.seen_n.get(pid)
        n = old[1] + 1 if old is not None and old[0] == t0 else 1
        if len(self.seen_n) > 512:
            self.seen_n = {k: v for k, v in self.seen_n.items() if k in self.seen}
        self.seen_n[pid] = (t0, n)
        return n

    def _confirme(self, pid: int, t0: float, now: float, fast: bool) -> bool:
        """Assez vu pour agir ? Règle d'avant : CONFIRM s écoulées. Rapide (kick) : CONFIRM_FAST s ET au moins
        FAST_N relevés concordants COMPTÉS (un tick en retard ne réduit pas la garde à deux relevés)."""
        n = self._releves(pid, t0)
        return now - t0 >= self.CONFIRM or (fast and n >= self.FAST_N and now - t0 >= self.CONFIRM_FAST)

    def kick(self, now: float | None = None) -> None:
        """Latence : un switch vient d'être posé chez nous par la synchro : relevés rapprochés FAST_FOR s."""
        self.fast_until = (self.clock() if now is None else now) + self.FAST_FOR

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 256:
            self.noted.add(tag)
            self._note(s)

    def reset(self, stage: bool = True) -> None:
        """Nouvelle visite (stage=False) ou nouvelle stage (tout oublié)."""
        self.seen.clear()
        self.tries.clear()
        self.next_try.clear()
        self.given_up.clear()
        self.breaking.clear()
        self.blocked.clear()
        self.noted = {t for t in self.noted if not (isinstance(t, tuple) and t[0] == "bloque")}
        self.why = ""
        if stage:
            self.to_delete.clear()
            self.created.clear()
            self.maybe.clear()
            self.deleted.clear()

    def _key(self, o: dict) -> str:
        return enemy_key(o["proc"], o["params"], o["home"])

    def _fail(self, o: dict, now: float, why: str) -> None:
        k = self._key(o)
        n = self.tries.get(k, 0) + 1
        self.tries[k] = n
        self.next_try[k] = now + self.RETRY
        if n >= self.MAX_TRIES and k not in self.given_up:
            self.given_up.add(k)
            self.failed += 1
            self._note(f"obstacles : {NAMES[o['proc']]} cassé(e) ailleurs non cassé(e) chez toi après {n} "
                       f"essais ({why}) : quitte et reviens dans la salle")

    def _block(self, o: dict, why: str, now: float, blocked_now: set) -> None:
        """Obstacle à casser mais bloqué : raison affichée par status() ; une note après BLOCK_NOTE s."""
        k = self._key(o)
        blocked_now.add(k)
        self.why = why
        t0 = self.blocked.setdefault(k, now)
        if now - t0 >= self.BLOCK_NOTE:
            self._once(("bloque", k), f"obstacles : {NAMES[o['proc']]} cassé(e) ailleurs, toujours pas cassé(e) "
                                     f"chez toi après {self.BLOCK_NOTE:g} s : {why}")

    def step(self, sm, mem, actors, now: float | None = None, ctx=None, free: bool = True,
             settled: bool = True, can: bool = True) -> list[tuple[int, str]]:
        """actors : [(id, adresse, proc, params, salle d'origine, taille d'instance)] relevés par le
        client (None : relevé raté) ; ctx : zone (un changement oublie la visite ; un changement
        de stage oublie tout) ; free : jeu libre (A0/A11) ; settled : salle stable ; can : écriture
        permise (boîte aux lettres présente et sans requête en attente).
        Renvoie les actions faites [(id, « casse » | « supprime » | « recree » | « retire »)]."""
        if not self.enabled:
            return []
        now = self.clock() if now is None else now
        if ctx != self.ctx:
            if self.ctx is not None and self.breaking:
                self._note(f"obstacles : {len(self.breaking)} rocher(s) armé(s) plus suivi(s) (changement de salle)")
            st = _stage_of(ctx)
            if ctx is not None and st != self.stage:
                self.reset(stage=True)
                self.stage = st
            else:
                self.reset(stage=False)
            self.ctx = ctx
        if ctx is None or actors is None or mem is None:
            return []
        fast = now < self.fast_until          # latence : switch reçu il y a moins de FAST_FOR s (kick)
        if now - self.last < (self.PERIOD_FAST if fast else self.PERIOD):
            return []
        self.last = now
        gi = _gi(mem)
        if gi is None:
            return []
        self.why = ""
        done: list[tuple[int, str]] = []
        self._follow(sm, mem, now, free, can, done)
        objs: dict[int, dict] = {}
        present = set()
        for a in actors:
            if len(a) < 6:
                continue
            pid, base, proc, _params, _room, size = a[:6]
            if proc not in SIZES:
                continue
            present.add(pid)
            if pid in self.deleted:
                continue
            if size != SIZES[proc]:
                self._once(("taille", proc, size), f"obstacles : {NAMES[proc]} (proc {proc}) : taille d'instance "
                                                   f"inattendue 0x{size:X} (0x{SIZES[proc]:X} attendue) : ignoré(e)")
                continue                      # pas la disposition HD connue : jamais touché
            o = read_obstacle(mem, base)
            if o is None or o["pid"] != pid or o["proc"] != proc:
                continue
            if not consistent(o):
                self._once(("dispo", proc), f"obstacles : {NAMES[proc]} (proc {proc}) : champ recopié des "
                                            "paramètres différent (disposition HD inattendue) : ignoré(e)")
                continue
            objs[pid] = o
        self._adopt(objs, now)
        self._replacements(sm, mem, gi, objs, present, now, free, can, done)
        blocked_now: set = set()
        for pid, o in objs.items():
            if pid in self.breaking or pid in self.to_delete:
                continue
            c = self.created.get(pid)
            if c is not None and now - c["t"] < self.CREATED_GRACE:
                continue                      # remplaçante qui vient de naître
            parts = stale_parts(o, lambda sw, _o=o: switch_on(mem, gi, sw, _o["room"]))
            if not parts:
                self.seen.pop(pid, None)
                continue
            t0 = self.seen.setdefault(pid, now)
            k = self._key(o)
            if not self._confirme(pid, t0, now, fast) or k in self.given_up or now < self.next_try.get(k, 0.0):
                continue
            if not (free and settled and can):
                self._block(o, "écriture impossible (boîte aux lettres absente, occupée ou --progress-no-apply)"
                            if not can else "salle en cours de chargement" if not settled
                            else "cinématique ou événement", now, blocked_now)
                continue
            why = game_free(mem)
            if why:
                self._block(o, why, now, blocked_now)
                continue
            act = self._act(sm, mem, gi, o, parts, now, objs)
            if act:
                done.append((pid, act))
        self.blocked = {k: t for k, t in self.blocked.items() if k in blocked_now}
        for pid in [p for p in self.seen if p not in present]:
            del self.seen[pid]
        self.deleted &= present
        return done

    # --- remplaçantes (barricades recréées par nous) --------------------------------------------
    def _adopt(self, objs: dict, now: float) -> None:
        """Création restée en attente (exception) puis exécutée : la nouvelle barricade (id inconnu
        au moment de l'appel) est reconnue et suivie comme une remplaçante."""
        for k, (t, known) in list(self.maybe.items()):
            new = [q for q, x in objs.items() if x["proc"] == PROC_SAKU and self._key(x) == k
                   and q not in known and q not in self.created]
            if new:
                for q in new:
                    self.created[q] = {"key": k, "room": objs[q]["room"], "t": now, "seen": True, "n": 0}
                del self.maybe[k]
                self._note("obstacles : barricade créée avec retard (boîte aux lettres) : suivie")
            elif now - t > self.MAYBE_TTL:
                del self.maybe[k]

    def _replacements(self, sm, mem, gi, objs, present, now, free, can, done) -> None:
        """Remplaçantes : dans la couche de Link, elles ne partent pas avec leur salle. Supprimées
        quand la zone de leur salle n'est plus chargée, ou quand le jeu a recréé la barricade de
        la salle (jumelle cohérente : salle rechargée)."""
        for pid, c in list(self.created.items()):
            o = objs.get(pid)
            if o is None:
                if pid in present and pid not in self.deleted:
                    continue                  # encore listée mais illisible cette fois : on attend
                if c["seen"] or now - c["t"] > self.CREATED_TTL:
                    del self.created[pid]     # partie (supprimée, stage changée) ou création perdue
                continue
            c["seen"] = True
            if now - c["t"] < self.CREATED_GRACE:
                continue
            reason = ""
            if room_loaded(mem, gi, c["room"]) is False:
                reason = "salle déchargée"
            else:
                for q, x in objs.items():
                    if q != pid and q not in self.created and x["proc"] == PROC_SAKU and self._key(x) == c["key"] \
                            and q not in self.to_delete \
                            and not stale_parts(x, lambda sw, _x=x: switch_on(mem, gi, sw, _x["room"])):
                        reason = "barricade de la salle recréée par le jeu"
                        break
            if not reason or not (free and can) or game_free(mem):
                continue
            if self._delete(sm, o):
                self.created.pop(pid, None)
                self.cleaned += 1
                done.append((pid, "retire"))
                self._note(f"obstacles : barricade recréée par nous retirée ({reason})")
                continue
            c["n"] += 1
            if c["n"] >= self.MAX_TRIES:
                del self.created[pid]
                self.failed += 1
                self._note(f"obstacles : barricade recréée par nous non retirée ({reason}) après {c['n']} essais")

    def _follow(self, sm, mem, now, free, can, done) -> None:
        """Rochers armés (cassés par le jeu ou repli) et barricades remplacées à supprimer."""
        for pid, b in list(self.breaking.items()):
            o = read_obstacle(mem, b["o"]["base"])
            if o is None or o["pid"] != pid or o.get("mode") == STONE2_FINE:
                del self.breaking[pid]
                self.native += 1
                self._note(f"obstacles : rocher cassé ailleurs, cassé chez toi aussi par le jeu "
                           f"({b['o']['home'][0]:.0f}, {b['o']['home'][1]:.0f}, {b['o']['home'][2]:.0f})")
                continue
            if o.get("mode") != STONE2_DROP or o["status"] & STATUS_CARRY or not consistent(o):
                # saisi par Link dans la même image que notre écriture (mode_carry_init : m648 = 1),
                # ou acteur inattendu : jamais de commande 5 (rocher peut-être dans la main de Link)
                del self.breaking[pid]
                self._note("obstacles : rocher armé saisi ou remis par le jeu : laissé tel quel")
                continue
            if now - b["t"] < self.BREAK_WAIT:
                continue
            if now - b["t"] > self.ARMED_MAX:
                del self.breaking[pid]
                self._fail(b["o"], now, "rocher armé jamais cassé ni supprimé")
                continue
            if not (free and can) or game_free(mem):
                continue
            if self._delete(sm, b["o"]):
                del self.breaking[pid]
                self.removed += 1
                done.append((pid, "supprime"))
                self._note("obstacles : rocher cassé ailleurs, pas cassé par le jeu en "
                           f"{self.BREAK_WAIT:g} s : supprimé chez toi")
        for pid, d in list(self.to_delete.items()):
            o = read_obstacle(mem, d["o"]["base"])
            if not _same(o, d["o"]):
                del self.to_delete[pid]       # déjà parti (salle rechargée…)
                continue
            if not (free and can) or game_free(mem):
                continue
            if self._delete(sm, d["o"]):
                del self.to_delete[pid]
                self.recreated += 1
                done.append((pid, "recree"))
                self._note("obstacles : ancienne barricade supprimée (deuxième essai)")
                continue
            d["n"] += 1
            if d["n"] >= self.MAX_TRIES:
                del self.to_delete[pid]
                self.failed += 1
                self.given_up.add(self._key(d["o"]))   # jamais de nouvelle remplaçante cette visite
                self._note("obstacles : ancienne barricade non supprimée après "
                           f"{d['n']} essais : deux barricades superposées jusqu'au rechargement de la salle")

    def _delete(self, sm, o: dict) -> bool:
        try:
            ok = bool(sm.delete_actor(o["pid"]))
            if ok:
                self.deleted.add(o["pid"])    # encore listé une image ou deux : plus jamais touché
                self.created.pop(o["pid"], None)
            return ok
        except Exception as exc:              # boîte aux lettres occupée, chargement…
            self._note(f"obstacles : suppression impossible ({exc})")
            return False

    def _puff(self, sm, o: dict) -> None:
        if not self.puff or not hasattr(sm, "puff"):
            return
        h = o["home"]
        try:
            sm.puff((h[0], h[1] + self.PUFF_UP, h[2]), o["room"])
        except Exception:
            pass                              # décoratif

    def _act(self, sm, mem, gi: int, o: dict, parts: list[int], now: float, objs: dict) -> str:
        name = NAMES[o["proc"]]
        where = f"({o['home'][0]:.0f}, {o['home'][1]:.0f}, {o['home'][2]:.0f})"
        if o["proc"] == PROC_STONE2:
            r, why = break_stone2(mem, gi, o)
            if r == ARMED:
                self.breaking[o["pid"]] = {"t": now, "o": o}
                return "casse"
            if r == RACE:
                self.native += 1
                return ""
            self._fail(o, now, why)
            return ""
        # revalidation juste avant la commande (barricade, mur)
        cur = read_obstacle(mem, o["base"])
        if not _same(cur, o) or stale_parts(cur, lambda sw: switch_on(mem, gi, sw, cur["room"])) != parts:
            self.seen.pop(o["pid"], None)
            return ""
        if o["proc"] == PROC_WALL:
            if not self._delete(sm, o):
                self._fail(o, now, "suppression refusée")
                return ""
            self.removed += 1
            self._puff(sm, o)
            self._note(f"obstacles : {name} cassé ailleurs : supprimé chez toi {where}")
            return "supprime"
        # barricade : recréée (lit les switches) puis l'ancienne supprimée
        k = self._key(o)
        twin = any(q != o["pid"] and x["proc"] == PROC_SAKU and self._key(x) == k for q, x in objs.items())
        coming = any(c["key"] == k and not c["seen"] and now - c["t"] < self.CREATED_TTL
                     for c in self.created.values())
        if twin or coming:
            # remplaçante déjà là (ou créée, pas encore relevée) : jamais une deuxième, on supprime l'ancienne
            if self._delete(sm, o):
                self.recreated += 1
                self._note(f"obstacles : ancienne {name} supprimée (remplaçante déjà là) {where}")
                return "recree"
            self._fail(o, now, "suppression refusée")
            return ""
        if k in self.maybe:
            self.next_try[k] = now + self.RETRY
            return ""                         # création peut-être en attente : on attend qu'elle paraisse
        why = recreatable(o)
        if why:
            if k not in self.given_up:
                self.given_up.add(k)
                self.skipped += 1
                self._note(f"obstacles : {name} cassée ailleurs, pas recréée chez toi ({why}) : "
                           "quitte et reviens dans la salle")
            return ""
        if not hasattr(sm, "create_actor"):
            return ""
        try:
            new = sm.create_actor(PROC_SAKU, o["params"], o["home"], o["room"], int(o["angle"][1]))
        except Exception as exc:
            # délai dépassé (menu pause…) : la requête est peut-être restée en attente et sera
            # exécutée plus tard. Pas de nouvelle création pendant MAYBE_TTL s ; la barricade qui
            # paraîtra est reconnue (_adopt) et l'ancienne supprimée alors (jumelle).
            self.maybe[k] = (now, {q for q, x in objs.items() if x["proc"] == PROC_SAKU and self._key(x) == k})
            self.next_try[k] = now + self.RETRY
            self._note(f"obstacles : barricade peut-être pas recréée ({exc}) : on attend qu'elle paraisse")
            return ""
        if new is None:
            self._fail(o, now, "création refusée")
            return ""
        self.tries[k] = self.tries.get(k, 0) + 1        # une création compte comme un essai
        self.next_try[k] = now + self.RETRY
        self.created[new] = {"key": k, "room": o["room"], "t": now, "seen": False, "n": 0}
        if hasattr(sm, "request_rescan"):
            sm.request_rescan()               # remplaçante vue dès le prochain relevé
        self._puff(sm, o)
        half = " et ".join(("moitié basse", "moitié haute")[i] for i in parts)
        if self._delete(sm, o):
            self.recreated += 1
            self._note(f"obstacles : {name} cassée ailleurs ({half}) : remplacée chez toi par une barricade "
                       f"cassée {where}")
            return "recree"
        self.to_delete[o["pid"]] = {"o": o, "n": 1}
        self._note(f"obstacles : {name} recréée, ancienne pas encore supprimée (réessai)")
        return ""

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = f"{self.native} cassé(s) par le jeu, {self.recreated} barricade(s) remplacée(s), {self.removed} supprimé(s)"
        if self.cleaned:
            s += f", {self.cleaned} remplaçante(s) retirée(s)"
        if self.failed:
            s += f", {self.failed} échec(s)"
        if self.skipped:
            s += f", {self.skipped} laissé(s)"
        if self.breaking or self.to_delete:
            s += f", {len(self.breaking) + len(self.to_delete)} en cours"
        if self.why:
            s += f" ; en attente : {self.why}"
        return s
