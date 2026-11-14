"""Objets « vivants » : mise à jour en direct des acteurs chargés qui ne lisent leur switch
qu'à leur initialisation (--live-refresh, activé par --coop-donjon, --no-live-refresh).

Cas traité : la PORTE À CADENAS (daDoor10_c, proc 300 : door10, keyshut, porte du boss…).
Quand l'autre joueur l'ouvre avec sa clé, son switch arrive chez nous (progression partagée
A1 « mem:switch », ou A7 pour un switch de donjon 0x80-0xBF), mais la porte déjà chargée garde
son cadenas : elle ne relit le switch que dans actionInit -> setKey [DECOMP d_a_door10.cpp,
HD-ASM 0x02127634 / 0x02127554]. Le joueur local pourrait alors dépenser sa clé pour rien
(keyInit décrémente le compteur même si le switch est déjà posé).

Le jeu repasse lui-même par actionInit à CHAQUE changement de salle : execute (HD 0x021277D0)
appelle checkExecute (0x0252AC98), qui renvoie 0 quand l'octet mRoomNo2 de la porte (+0x3AC)
diffère de la salle courante (stayNo 0x1047E6C8) ; execute met alors l'action à 0 (Init) et
recopie stayNo dans mRoomNo2 en fin de frame. checkExecute teste AVANT la salle le statut démo
(+0x2E0 & 0x1000) et la commande d'événement (+0xF8 = 2 ou 3) : une porte en cours d'ouverture
ou de traversée ignore donc notre octet, que le jeu réécrase à la fin de la frame.

Méthodes (--door-fix) :
  auto (défaut)  porte SANS barreaux (switch 2 = 0xFF et pas de modèle de barreaux) et Link à
                 plus de NEAR_REINIT unités : « réinit » = un octet 0x7F à +0x3AC ; la frame
                 suivante, le jeu fait ce qu'il fait à un changement de salle (setKey relit le
                 switch -> keyOff, setStop, actionWait). Sinon « cadenas » (ci-dessous) : porte
                 AVEC barreaux (ne pas rejouer leur fermeture, setStop + chkStopClose), ou Link
                 tout près (il pourrait appuyer sur A dans la frame même : actionInit force
                 l'action 1 après actionWait et écraserait un début d'ouverture).
  cadenas        un octet 0 à mKeyLock.mbEnabled (+0x424) : l'état final de keyProc/keyOff.
                 Le cadenas n'est plus dessiné, setEventPrm n'exige plus de clé, keyInit ne
                 décrémente plus rien. Pas d'animation de chute (il faudrait un pack v18).
  journal        rien n'est écrit : « cadenas encore affiché : quitte et reviens dans la salle
                 par une autre sortie ».
Repli : un essai « réinit » sans effet (écriture écrasée par la recopie de fin de frame) est
suivi d'essais « cadenas ». Un octet 0x7F encore présent à la relecture veut dire que le jeu
n'a pas exécuté la porte (menu pause, jeu figé) : on attend, sans réécrire ni compter d'essai.

Gardes (toutes relues juste avant l'unique écriture) : jeu libre (A0/A11 : ni événement ni
changement de stage), salle stable depuis ROOM_SETTLE s, porte en attente (action 1), pas
d'animation de cadenas (m20 = 0), ni démo ni commande de porte sur l'acteur, même id de
process et mêmes paramètres, switch posé dans la mémoire vivante, cadenas présent depuis
CONFIRM s. Au plus MAX_TRIES essais par porte ; ensuite seulement le journal.

Portes non voisines (mFrontCheck = 2 : Link n'est dans aucune de leurs deux salles) : action 0
en permanence, elles relisent leur switch seules quand Link entre dans une de leurs salles
(checkExecute, DECOMP d_door.cpp l.156-171) : rien à écrire, comptées « en attente de ta
visite ». Porte voisine périmée jamais vue au repos : une note à la détection (avec la raison)
et une seconde après WARN_AFTER s (« quitte et reviens… »).

door12 (proc 301, autres donjons) : même logique dans le jeu, mais offsets HD non relevés :
journal seul quand un switch reçu correspond à une de ses portes à clé.

Les autres objets du donjon relisent leur switch à chaque frame (barreaux de porte, shutter,
coffres qui apparaissent, torches, TagEv…) : rien à faire (docs/etudes/portes_vivantes.md).
"""
from __future__ import annotations

import math
import struct
import time
from dataclasses import dataclass

from jeu import progress as prog

PROC_DOOR10 = 300          # daDoor10_c : door10, door11, door20, door21, Zenshut, keyshut, K_Zshut
PROC_DOOR12 = 301          # daDoor12_c : door12, keyS12, door13… (journal seul)
DOOR_PROCS = (PROC_DOOR10, PROC_DOOR12)

# fopAc_ac_c / base_process_class (HD)
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_EVT_CMD = 0xF8         # u16 eventInfo.mCommand (GC 0xF4 + 4) [DECOMP + LIVE : 0 au repos]
CMD_INDEMO, CMD_INDOOR = 2, 3
OFF_STATUS = 0x2E0         # u32 actor_status [HD-ASM]
STATUS_DEMO = 0x1000       # fopAcStts_UNK1000_e : checkExecute renvoie 1 (démo)
OFF_HOME_ANGLE_X = 0x2F8   # s16 home.angle.x : salle avant (& 0x3F), arrière (>> 6 & 0x3F)
OFF_POS = 0x314            # f32[3] current.pos (GC 0x1F8 + 0x11C)
NEAR_REINIT = 500.0        # u : Link plus près de la porte -> méthode « cadenas » (pas de réinit)

# daDoor10_c (instance HD 0x500) [HD-ASM + LIVE 2026-11-08, voir docs/dev/MEMORY_MAP.md]
DOOR10_SIZE = 0x500
D10_ROOM_NO2 = 0x3AC       # s8 mRoomNo2 (recopie de stayNo à chaque frame)
D10_FRONT = 0x3BC          # u8 mFrontCheck (0 avant, 1 arrière, 2 aucune des deux salles)
D10_KEY_EN = 0x424         # u8 mKeyLock.mbEnabled (cadenas présent)
D10_KEY_MODEL = 0x428      # u32 mKeyLock.mpModel
D10_KEY_ANIM = 0x4C0       # u8 mKeyLock.m20 (animation d'ouverture en cours)
D10_KEY_BOSS = 0x4C1       # u8 mKeyLock.mbIsBossDoor
D10_STOP_MODEL = 0x4C4     # u32 mStopBars.mpModel
D10_STOP_ON = 0x4CC        # u8 mStopBars.m8 (barreaux fermés)
D10_ACTION = 0x4EC         # u8 m354 : 0 Init, 1 Wait, 2 StopClose, 3 Demo, 4 Hkyo
ACT_INIT, ACT_WAIT = 0, 1
REINIT_ROOM = 0x7F         # valeur écrite à mRoomNo2 : jamais une salle (0-63)
FRONT_NONE = 2             # mFrontCheck : Link dans aucune des deux salles de la porte

KEY_TYPES_D10 = (4, 5)     # chkMakeKey == 1 (petite clé) ; type 1 = porte du boss
BOSS_TYPE_D10 = 1
KEY_TYPES_D12 = (1, 3)     # door12 : 1 petite clé, 3 porte du boss

MODES = ("auto", "cadenas", "journal")
ADVICE = ("quitte et reviens dans la salle par une autre sortie, pas par cette porte "
          "(n'utilise pas ta clé)")
# Synchro v2 (petites clés partagées, synchro/cles.py) : une porte déjà payée par un autre joueur et
# payée une seconde fois chez nous rend la clé (key_use refusé en double par le serveur)
ADVICE_KEYS = ("quitte et reviens dans la salle par une autre sortie, pas par cette porte "
               "(si tu utilises ta clé, elle te sera rendue)")
REASON_PAUSE = "réinitialisation écrite, le jeu ne l'a pas encore exécutée (pause ?)"


def door_info(params: int, angle_x: int) -> dict:
    """Paramètres de dDoor_info_c [DECOMP d_door.cpp] : sw = p & 0xFF, type = (p >> 8) & 0xF,
    sw2 = (p >> 20) & 0xFF ; salles avant/arrière dans home.angle.x."""
    return {"sw": params & 0xFF, "type": (params >> 8) & 0xF, "sw2": (params >> 20) & 0xFF,
            "front_room": angle_x & 0x3F, "back_room": (angle_x >> 6) & 0x3F}


def saved_switch_byte(sw: int) -> tuple[int, int]:
    """Switch sauvegardé 0x00-0x7F -> (octet relatif au dSv_memBit_c, masque) : mSwitch[4] en
    u32 big-endian à +0x04 [DECOMP d_save.h]. Ex. 0x3C -> (0x08, 0x10)."""
    if not 0 <= sw < 0x80:
        raise ValueError(f"switch sauvegardé hors plage : 0x{sw:X}")
    return 0x04 + 4 * (sw >> 5) + 3 - ((sw & 31) >> 3), 1 << (sw & 7)


def bits_to_switches(bits: dict, cur_idx) -> set[int]:
    """Bits posés par la progression partagée -> switches sauvegardés de la stage chargée."""
    out: set[int] = set()
    if cur_idx is None:
        return out
    for off, m in (bits.get(f"mem{cur_idx}") or {}).items():
        if 0x04 <= off < 0x14:
            for j in range(8):
                if m >> j & 1:
                    out.add(((off - 0x04) // 4) * 32 + (3 - (off - 0x04) % 4) * 8 + j)
    return out


def switch_on(mem, gi: int, sw: int):
    """dComIfGs_isSwitch(sw, -1) relu dans la mémoire VIVANTE : True/False, None si non lisible
    ou hors de ce qu'on sait lire (switches de zone 0xC0+ avec salle -1 : ambigus)."""
    if 0 <= sw < 0x80:
        off, mask = saved_switch_byte(sw)
        raw = mem.try_read(gi + prog.GI_CUR_MEMORY + off, 1)
        return None if raw is None else bool(raw[0] & mask)
    if 0x80 <= sw < 0xC0:
        raw = mem.try_read(gi + prog.GI_DAN, prog.DAN_SWITCH + prog.DAN_SWITCH_SIZE)
        if raw is None or not 0 <= struct.unpack_from(">b", raw, 0)[0] < prog.N_MEM:
            return None
        _kind, off, mask = prog.tsw_byte(sw)
        return bool(raw[off] & mask)
    return None


@dataclass
class Door10:
    base: int
    pid: int
    proc: int
    params: int
    sw: int
    type: int
    sw2: int
    front_room: int
    back_room: int
    room_no2: int
    front: int
    key_en: int
    key_model: int
    key_anim: int
    boss: int
    stop_model: int
    stop_on: int
    action: int
    status: int
    evt_cmd: int
    pos: tuple = (0.0, 0.0, 0.0)

    @property
    def key_door(self) -> bool:
        """Porte dont setKey dépend d'un switch qu'on sait relire (comme setKey du jeu)."""
        if self.sw == 0xFF:
            return False
        if self.type in KEY_TYPES_D10:
            return self.sw < 0xC0
        return self.type == BOSS_TYPE_D10 and self.sw < 0x80    # >= 0x80 : toujours verrouillée

    @property
    def has_bars(self) -> bool:
        return self.sw2 != 0xFF or self.stop_model != 0

    def desc(self) -> str:
        what = "grande porte du boss" if self.type == BOSS_TYPE_D10 else "porte à cadenas"
        return f"{what} (switch 0x{self.sw:02X}, salles {self.front_room}/{self.back_room})"


def parse_door10(raw: bytes, base: int) -> Door10 | None:
    if len(raw) < DOOR10_SIZE or struct.unpack_from(">I", raw, 0)[0] == 0:
        return None                     # acteur supprimé (mBsType remis à 0)
    params = struct.unpack_from(">I", raw, OFF_PARAMS)[0]
    ax = struct.unpack_from(">h", raw, OFF_HOME_ANGLE_X)[0]
    i = door_info(params, ax)
    return Door10(base, struct.unpack_from(">I", raw, OFF_PC_ID)[0], struct.unpack_from(">h", raw, OFF_PROC)[0],
                  params, i["sw"], i["type"], i["sw2"], i["front_room"], i["back_room"],
                  struct.unpack_from(">b", raw, D10_ROOM_NO2)[0], raw[D10_FRONT], raw[D10_KEY_EN],
                  struct.unpack_from(">I", raw, D10_KEY_MODEL)[0], raw[D10_KEY_ANIM], raw[D10_KEY_BOSS],
                  struct.unpack_from(">I", raw, D10_STOP_MODEL)[0], raw[D10_STOP_ON], raw[D10_ACTION],
                  struct.unpack_from(">I", raw, OFF_STATUS)[0], struct.unpack_from(">H", raw, OFF_EVT_CMD)[0],
                  struct.unpack_from(">3f", raw, OFF_POS))


def read_door10(mem, base: int) -> Door10 | None:
    raw = mem.try_read(base, DOOR10_SIZE)
    return None if raw is None else parse_door10(raw, base)


def stale_lock(d: Door10, on) -> bool:
    """Cadenas affiché alors que son switch est posé : périmé (setKey ferait keyOff)."""
    return d.key_door and d.key_en == 1 and on is True


def unsafe_reason(d: Door10) -> str:
    """'' si la porte est au repos (on peut y écrire) ; sinon pourquoi pas."""
    if d.proc != PROC_DOOR10:
        return "pas une porte door10"
    if d.action != ACT_WAIT:
        return f"action {d.action} (pas en attente)"
    if d.key_anim:
        return "cadenas en cours d'ouverture"
    if d.status & STATUS_DEMO:
        return "porte en démo"
    if d.evt_cmd in (CMD_INDEMO, CMD_INDOOR):
        return "porte en cours d'ouverture (événement)"
    if d.key_model == 0:
        return "pas de modèle de cadenas"
    return ""


def choose_method(d: Door10, mode: str, link_pos=None) -> str:
    """link_pos None (inconnue) : comme Link tout près."""
    if mode == "journal":
        return "journal"
    if mode == "cadenas" or d.has_bars:
        return "cadenas"
    # distance horizontale (XZ), comme checkArea : plus prudente que la distance 3D
    if link_pos is None or len(link_pos) < 3 or not all(math.isfinite(v) for v in (*link_pos, *d.pos)) \
            or math.hypot(link_pos[0] - d.pos[0], link_pos[2] - d.pos[2]) < NEAR_REINIT:
        return "cadenas"
    return "reinit"


def game_free(mem) -> str:
    """Relecture brute juste avant d'écrire : '' = libre."""
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        return "changement de stage en cours"
    return prog.event_busy(mem)


def apply_fix(mem, gi: int, expect: Door10, method: str) -> tuple[bool, str]:
    """Revalide la porte (même acteur, mêmes paramètres, toujours périmée et au repos, jeu
    libre) puis écrit UN octet. Renvoie (écrit, raison)."""
    d = read_door10(mem, expect.base)
    if d is None or d.pid != expect.pid or d.proc != PROC_DOOR10 or d.params != expect.params:
        return False, "acteur changé"
    if not stale_lock(d, switch_on(mem, gi, d.sw)):
        return False, "plus périmée"
    if d.room_no2 == REINIT_ROOM:
        return False, "réinitialisation déjà en attente"
    why = unsafe_reason(d) or game_free(mem)
    if why:
        return False, why
    if method == "reinit":
        mem.write(d.base + D10_ROOM_NO2, struct.pack(">b", REINIT_ROOM))
    elif method == "cadenas":
        mem.write(d.base + D10_KEY_EN, b"\x00")
    else:
        return False, f"méthode {method}"
    return True, ""


class LiveDoors:
    """État du correctif « portes vivantes » (thread de travail du client)."""

    PERIOD = 0.5          # s entre deux relevés des portes
    CONFIRM = 0.4         # s : cadenas périmé vu au moins deux fois avant d'écrire
    # latence (docs/etudes/latence.md) : un switch vient d'être posé chez nous par la synchro (kick) : pendant
    # FAST_FOR s, relevés toutes les PERIOD_FAST s et écriture après CONFIRM_FAST s, soit TROIS relevés
    # concordants (0 ; 0,1 ; 0,2 s) au lieu de deux espacés de 0,5 s. Toutes les autres gardes sont inchangées.
    PERIOD_FAST, CONFIRM_FAST, FAST_FOR = 0.1, 0.15, 3.0
    FAST_N = 3            # relevés concordants comptés avant d'agir en cadence rapide
    RETRY = 1.0           # s entre deux essais sur la même porte
    MAX_TRIES = 3
    ROOM_SETTLE = 1.5     # s après l'entrée dans la salle (portes en cours d'initialisation)
    WARN_AFTER = 5.0      # s : porte voisine périmée jamais traitée -> note « quitte et reviens »
    RECENT = 120.0        # s pendant lesquels un switch reçu est « récent » (journal)

    def __init__(self, enabled: bool = True, mode: str = "auto", clock=time.monotonic):
        if mode not in MODES:
            raise ValueError(f"mode inconnu : {mode}")
        self.enabled, self.mode, self.clock = enabled, mode, clock
        self.advice = ADVICE                      # ADVICE_KEYS quand les petites clés sont partagées
        self.last = float("-inf")
        self.fast_until = float("-inf")           # latence : relevés rapprochés jusqu'à cette heure (kick)
        self.ctx = None
        self.seen: dict[int, float] = {}          # pid -> 1re fois vue périmée (au repos)
        self.seen_n: dict[int, tuple[float, int]] = {}   # latence : id -> (t0 de `seen`, relevés concordants)
        self.tries: dict[int, tuple[int, float, str]] = {}   # pid -> (essais, heure, méthode)
        self.gave_up: dict[int, str] = {}         # pid -> "echec" | "journal" | "joueur"
        self.pending: dict[int, list] = {}        # pid -> [1re détection, raison, averti, noté]
        self.ctx_t = float("-inf")                # début de la zone courante
        self.recent: dict[int, float] = {}        # switch -> heure de réception (A1/A7)
        self.noted12: set[int] = set()
        self.notes: list[str] = []
        self.n_fixed = self.n_failed = self.n_logged = 0
        self.status_text = "en attente"

    # --- entrées ------------------------------------------------------------
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

    def note_switches(self, sws, now: float | None = None) -> None:
        """Switches posés par la progression partagée ou A7 (journal : « reçu d'un autre joueur »)."""
        now = self.clock() if now is None else now
        for sw in sws:
            if isinstance(sw, int) and 0 <= sw < 0xC0:
                self.recent[sw] = now
        for sw in [s for s, t in self.recent.items() if now - t > self.RECENT]:
            del self.recent[sw]

    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-20:]

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def reset(self) -> None:
        self.seen.clear()
        self.tries.clear()
        self.gave_up.clear()
        self.pending.clear()
        self.noted12.clear()

    def _src(self, sw: int) -> str:
        return " ; switch reçu d'un autre joueur" if sw in self.recent else ""

    # --- tick -----------------------------------------------------------------
    def step(self, mem, doors, now: float | None = None, free: bool = True, settled: bool = True,
             can_write: bool = True, ctx=None, link_pos=None, room=None) -> list[tuple[int, str]]:
        """doors = [(pid, base, proc)] des portes chargées (None : chargement). ctx change à
        chaque zone (stage, salle) : on oublie alors les essais. link_pos : position de Link
        (choix de la méthode) ; room : salle de Link (door12). Renvoie [(pid, méthode)] écrits."""
        if not self.enabled:
            return []
        now = self.clock() if now is None else now
        fast = now < self.fast_until          # latence : switch reçu il y a moins de FAST_FOR s (kick)
        if now - self.last < (self.PERIOD_FAST if fast else self.PERIOD):
            return []
        self.last = now
        if ctx != self.ctx:
            self.ctx, self.ctx_t = ctx, now
            self.reset()
        if doors is None or mem is None:
            self.seen.clear()
            self.status_text = "jeu en chargement"
            return []
        raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
        gi = struct.unpack(">I", raw)[0] if raw else 0
        if not 0x10000000 <= gi < 0x50000000:
            self.status_text = "pas de sauvegarde chargée"
            return []
        present = {pid for pid, _b, _p in doors}
        for dct in (self.seen, self.tries, self.pending, self.gave_up):
            for pid in [p for p in dct if p not in present]:
                del dct[pid]
        written: list[tuple[int, str]] = []
        active = visit = 0
        last_reason = ""
        for pid, base, proc in doors:
            if proc == PROC_DOOR12:
                self._door12(mem, gi, pid, base, room)
                continue
            if proc != PROC_DOOR10:
                continue
            d = read_door10(mem, base)
            if d is None or d.pid != pid:
                continue
            on = switch_on(mem, gi, d.sw) if d.key_door else None
            if not stale_lock(d, on):
                self._resolved(d, pid)
                continue
            if pid in self.gave_up:
                if d.key_anim:               # le joueur ouvre la porte avec sa propre clé
                    self.gave_up[pid] = "joueur"
                continue
            if d.front == FRONT_NONE:
                # Link hors des deux salles : la porte relira son switch à son entrée (actionInit)
                visit += 1
                self.seen.pop(pid, None)
                self.pending.pop(pid, None)
                continue
            active += 1
            pend = self.pending.setdefault(pid, [now, "", False, False])
            if d.room_no2 == REINIT_ROOM:
                why = REASON_PAUSE           # 0x7F encore là : execute pas encore passé
            elif not free:
                why = "jeu occupé (événement ou changement de stage)"
            elif not settled:
                why = "salle pas encore stable"
            else:
                why = unsafe_reason(d)
            pend[1] = why
            if why:
                last_reason = why
                self.seen.pop(pid, None)     # on réessaiera au repos (CONFIRM recompté)
                # pas un état de passage, ni une réinit déjà écrite (son issue sera notée)
                if not pend[3] and why != REASON_PAUSE and now - pend[0] >= self.CONFIRM:
                    pend[3] = True
                    self._note(f"{d.desc()} : cadenas périmé détecté{self._src(d.sw)} ; en attente : {why}")
                if not pend[2] and why != REASON_PAUSE and now - pend[0] >= self.WARN_AFTER:
                    pend[2] = True
                    self._note(f"{d.desc()} : cadenas toujours affiché depuis {now - pend[0]:.0f} s ({why}) ; "
                               f"{self.advice}")
                continue
            first = self.seen.setdefault(pid, now)
            if not self._confirme(pid, first, now, fast):
                continue
            n, last_t, _m = self.tries.get(pid, (0, float("-inf"), ""))
            if now - last_t < self.RETRY:
                continue
            # lecture seule (--progress-no-apply, mémoire non inscriptible) : journal seul
            method = choose_method(d, self.mode if can_write else "journal", link_pos)
            if method == "reinit" and n >= 1:
                method = "cadenas"           # repli : la réinit précédente n'a rien donné
            if method == "journal" or n >= self.MAX_TRIES:
                self.tries.pop(pid, None)
                if method == "journal":
                    self.gave_up[pid] = "journal"
                    self.n_logged += 1
                else:
                    self.gave_up[pid] = "echec"
                    self.n_failed += 1
                self._note(f"{d.desc()} : cadenas encore affiché alors que la porte est ouverte"
                           f"{self._src(d.sw)} ; {self.advice}")
                continue
            try:
                ok, why = apply_fix(mem, gi, d, method)
            except Exception as exc:         # mémoire non inscriptible (lecture seule), chargement…
                self.gave_up[pid] = "echec"
                self.tries.pop(pid, None)
                self.n_failed += 1
                self._note(f"{d.desc()} : cadenas périmé non corrigé (écriture impossible : {exc}) ; {self.advice}")
                continue
            if ok:
                self.tries[pid] = (n + 1, now, method)   # seuls les essais écrits comptent
                written.append((pid, method))
            else:                            # porte changée, ou jeu occupé à la relecture :
                self.seen.pop(pid, None)     # on recompte CONFIRM, sans compter d'essai
        parts = []
        if active:
            parts.append(f"{active} cadenas périmé(s) en cours"
                         + (f" (en attente : {last_reason})" if last_reason else ""))
        if visit:
            parts.append(f"{visit} en attente de ta visite (se corrigent seuls)")
        self.status_text = " ; ".join(parts) or "à jour"
        return written

    def _resolved(self, d: Door10, pid: int) -> None:
        """Porte plus périmée : succès d'un essai, ou d'une porte abandonnée (jeu en pause
        pendant les essais) ; compteurs corrigés."""
        self.seen.pop(pid, None)
        self.pending.pop(pid, None)
        t = self.tries.pop(pid, None)
        kind = self.gave_up.pop(pid, None)
        if not (d.key_door and d.key_en == 0):
            return
        if t is not None:
            self.n_fixed += 1
            how = "porte réinitialisée" if t[2] == "reinit" else "cadenas effacé"
            self._note(f"{d.desc()} : cadenas retiré en direct ({how}){self._src(d.sw)}")
        elif kind == "echec":
            self.n_failed -= 1
            self.n_fixed += 1
            self._note(f"{d.desc()} : cadenas finalement retiré (le jeu a repris){self._src(d.sw)}")
        elif kind == "journal":
            self.n_logged -= 1
        # "joueur" : ouverte avec sa propre clé, reste comptée « à corriger »

    def _door12(self, mem, gi: int, pid: int, base: int, room=None) -> None:
        """door12 : offsets HD inconnus -> journal seul, une fois par porte, si un switch reçu
        PENDANT que Link est dans une de ses deux salles correspond à une de ses portes à clé
        (reçu avant d'y entrer : la porte l'a relu à l'entrée [HYPO, comme door10])."""
        if pid in self.noted12:
            return
        raw = mem.try_read(base + OFF_PARAMS, 4)
        ax = mem.try_read(base + OFF_HOME_ANGLE_X, 2)
        if raw is None or ax is None:
            return
        i = door_info(struct.unpack(">I", raw)[0], struct.unpack(">h", ax)[0])
        if i["type"] not in KEY_TYPES_D12 or i["sw"] not in self.recent or switch_on(mem, gi, i["sw"]) is not True:
            return
        if room is not None and room not in (i["front_room"], i["back_room"]) \
                and 0x3F not in (i["front_room"], i["back_room"]):
            return                           # pas voisine : se corrigera à l'entrée
        if self.recent[i["sw"]] < self.ctx_t:
            return                           # reçu avant l'entrée dans cette salle
        self.noted12.add(pid)
        self.n_logged += 1
        self._note(f"porte door12 (switch 0x{i['sw']:02X}, salles {i['front_room']}/{i['back_room']}) ouverte par un "
                   "autre joueur : si son cadenas est encore affiché, " + ADVICE)   # door12 : clé jamais rendue (R16)

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = f"{self.status_text} ; {self.n_fixed} cadenas retiré(s) en direct"
        if self.n_failed or self.n_logged:
            s += f", {self.n_failed + self.n_logged} à corriger en changeant de salle"
        return s + f" (méthode {self.mode})"
