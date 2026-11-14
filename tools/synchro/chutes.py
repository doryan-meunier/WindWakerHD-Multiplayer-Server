"""Chutes en direct (synchro v4 ; --share-chutes, activé par --coop-donjon, --no-share-chutes).

Deux décors tombent quand un joueur coupe leurs attaches, et le jeu ne le rejouait chez les autres qu'au
rechargement de la salle (le switch, lui, arrivait déjà par la progression partagée) :

  Mflft (proc 91, plateforme suspendue au-dessus de la lave, Caverne du Dragon) : trois cordes, m2D4[3]
    (s8, +0x3F0..+0x3F2). Les trois coupées -> le jeu passe m29A (s16, +0x3B6) à 1, pose le bit d'événement
    0x2A10 et le switch (params >> 24, recopié en +0x3BD), cherche la lave et fait tomber la plateforme.
  Kokiie (proc 98, maison suspendue du Bois Défendu) : cinq lianes, m32C[5] (+0x448). Toutes coupées -> health
    (+0x3A1) = 0, m29A (+0x3B6) = 1, cinématique m378 (+0x494) ; le switch (params >> 24, +0x3BE) est posé
    27 images plus tard.

Chez un joueur de la même salle où l'acteur est encore à l'état 0 alors que SON switch est posé dans la
mémoire vivante (posé par un autre joueur, arrivé par la progression), ce module écrit ce que le jeu écrit
lui-même :

  Mflft : les trois cordes à 1 (exactement ce que fait la création de l'acteur quand le switch est déjà
    posé). À l'image suivante le jeu fait la bascule lui-même (état 1, recherche de la lave, chute).
  Kokiie : health = 0 puis m29A = 1 EN DERNIER (ce que fait la création quand le switch est posé), SANS la
    cinématique (m378 reste 0 : la caméra de l'autre joueur n'est pas prise). La maison tombe et se pose ;
    pas de son de craquement ni d'éclaboussures (liés au compteur de la cinématique), le son et la secousse
    de l'atterrissage sont joués par le jeu. Les cinq lianes (acteurs SHAND) se détachent d'elles-mêmes :
    chacune relit le health de la maison à chaque image et passe à « coupée » quand il vaut 0
    (d_a_shand.cpp, hand_move) ; rien n'est écrit dans ces acteurs.

Rien n'est écrit si l'acteur n'est pas exactement celui attendu (id de process, proc, paramètres, taille
d'instance du profil HD, octet de switch = params >> 24, état 0 ou 1), si le jeu n'est pas libre (événement,
changement de stage), si la salle n'est pas stable depuis ROOM_SETTLE s ou si l'état « 0 + switch posé » n'a
pas été vu pendant CONFIRM s (chez celui qui coupe, l'état passe à 1 avant ou avec le switch : jamais touché).
Aucun message réseau, aucune commande de la boîte aux lettres : compatible pack v17.

Vérifié dans le RPX (tests/test_chutes.py, TestRpx) ; pas encore essayé en jeu
(docs/dev/tests/PLAN_TEST_SYNCHRO.md).
"""
from __future__ import annotations

import struct

from synchro import obstacles

# --- fopAc_ac_c HD -----------------------------------------------------------------------------------
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_HOME_ROOM = 0x2EC + 0x12   # s8 home.roomNo
OFF_HEALTH = 0x3A1             # s8 health   (Kokiie 0x021A5948 : stb r29, 0x3A1(r31))
OFF_STATE = 0x3B6              # s16 m29A    (Mflft 0x021C28AC lha / 0x021C296C sth ; Kokiie 0x021A57FC / 0x021A594C)
_HEAD = 0x3C0                  # une lecture : en-tête, paramètres, home, état, octet de switch

PROC_MFLFT, PROC_KOKIIE = 91, 98
SIZES = {PROC_MFLFT: 0x9E4, PROC_KOKIIE: 0x4C0}      # taille d'instance des profils 0x101BA544 / 0x101B8798
SW_OFF = {PROC_MFLFT: 0x3BD, PROC_KOKIIE: 0x3BE}     # u8 switch = params >> 24 (0x021C2A48 / 0x021A5C04 lbz)
MF_ROPES = 0x3F0               # s8 m2D4[3]  (0x021C2908 : addi r15, r24, 0x3F0 ; 0x021C2944 : cmpwi r7, 7)
PROCS = tuple(SIZES)
NAMES = {PROC_MFLFT: "plateforme suspendue (Mflft)", PROC_KOKIIE: "maison suspendue (Kokiie)"}
NO_SWITCH = 0xFF
HANGING, FALLEN = 0, 1

switch_on = obstacles.switch_on        # remplaçables dans les tests
game_free = obstacles.game_free
_gi = obstacles._gi


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def read_chute(mem, base) -> dict | None:
    """Relit une plateforme / maison suspendue : None si l'acteur n'existe plus, n'est pas lisible ou n'est
    pas cohérent (octet de switch différent de params >> 24, état inconnu)."""
    if not _int(base) or not 0x10000000 <= base < 0x50000000:
        return None
    raw = mem.try_read(base, _HEAD)
    if raw is None or len(raw) < _HEAD:
        return None
    bs_type, pid = struct.unpack_from(">II", raw, 0)
    proc, = struct.unpack_from(">h", raw, OFF_PROC)
    if bs_type == 0 or proc not in SIZES:
        return None
    params, = struct.unpack_from(">I", raw, OFF_PARAMS)
    state, = struct.unpack_from(">h", raw, OFF_STATE)
    sw = raw[SW_OFF[proc]]
    if sw != params >> 24 or state not in (HANGING, FALLEN):
        return None
    o = {"base": base, "pid": pid, "proc": proc, "params": params, "state": state, "sw": sw,
         "room": struct.unpack_from(">b", raw, OFF_HOME_ROOM)[0], "health": raw[OFF_HEALTH]}
    if proc == PROC_MFLFT:
        r = mem.try_read(base + MF_ROPES, 3)
        if r is None or len(r) < 3:
            return None
        o["ropes"] = bytes(r)
    return o


def _same(o: dict | None, expect: dict) -> bool:
    return o is not None and all(o[k] == expect[k] for k in ("pid", "proc", "params", "sw", "room"))


def drop(mem, gi: int, expect: dict) -> str:
    """Revalidation puis écriture. Renvoie '' si écrit, sinon la raison du refus."""
    o = read_chute(mem, expect["base"])
    if not _same(o, expect):
        return "acteur changé"
    if o["state"] != HANGING:
        return "déjà tombé"
    if switch_on(mem, gi, o["sw"], o["room"]) is not True:
        return "switch effacé"
    why = game_free(mem)
    if why:
        return why
    try:
        if o["proc"] == PROC_MFLFT:
            mem.write(o["base"] + MF_ROPES, b"\x01\x01\x01")     # le jeu bascule lui-même à l'image suivante
        else:
            mem.write(o["base"] + OFF_HEALTH, b"\x00")
            mem.write(o["base"] + OFF_STATE, struct.pack(">h", FALLEN))   # déclencheur : en dernier
    except Exception as exc:                  # mémoire en lecture seule…
        return f"écriture impossible ({exc})"
    return ""


class ChuteSync:
    """État du thread de travail : un acteur n'est touché qu'après CONFIRM s d'état « 0 + switch posé »."""

    CONFIRM = 0.6         # s (au moins deux relevés)
    ROOM_SETTLE = 2.0     # s dans la salle avant d'agir (acteurs de la salle tous créés)
    RETRY = 2.0           # s entre deux essais sur le même acteur
    MAX_TRIES = 3

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.ctx = None
        self.seen: dict = {}          # pid -> première vue de « 0 + switch posé »
        self.tries: dict = {}         # pid -> (essais, prochain essai)
        self.done = 0
        self.last = None
        self.notes: list[str] = []

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def step(self, mem, actors, now: float, ctx, free: bool, settled: bool, can: bool) -> list:
        """actors : [(id, adresse, proc, params, salle d'origine, taille)] ou None. Renvoie les procs lancés."""
        if not self.enabled:
            return []
        if ctx != self.ctx:
            self.ctx, self.seen, self.tries = ctx, {}, {}
        if mem is None or not actors or ctx is None or not (free and settled and can):
            self.seen = {}                    # la confirmation repart de zéro
            return []
        gi = _gi(mem)
        if gi is None:
            self.seen = {}
            return []
        out, alive = [], set()
        for a in actors:
            if len(a) < 6 or a[2] not in SIZES or a[5] != SIZES[a[2]]:
                continue
            o = read_chute(mem, a[1])
            if o is None or o["pid"] != a[0] or o["params"] != a[3] or o["sw"] == NO_SWITCH:
                continue
            pid = o["pid"]
            if o["state"] != HANGING or switch_on(mem, gi, o["sw"], o["room"]) is not True:
                continue
            alive.add(pid)
            t0 = self.seen.setdefault(pid, now)
            n, nxt = self.tries.get(pid, (0, 0.0))
            if now < t0:
                self.seen[pid] = now
                continue
            if now - t0 < self.CONFIRM or n >= self.MAX_TRIES or now < nxt:
                continue
            why = drop(mem, gi, o)
            self.tries[pid] = (n + 1, now + self.RETRY)
            name = NAMES[o["proc"]]
            if why:
                self.notes.append(f"chutes : {name} non lancée ({why})")
                continue
            self.done += 1
            self.last = o["proc"]
            out.append(o["proc"])
            self.notes.append(f"chutes : {name} tombée chez un autre joueur -> elle tombe ici")
        for pid in [p for p in self.seen if p not in alive]:
            del self.seen[pid]
        return out

    def status(self) -> str:
        s = f"{self.done} chute(s) rejouée(s)"
        if self.last is not None:
            s += f", dernière : {NAMES[self.last]}"
        return s
