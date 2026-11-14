"""Armes orphelines (SYNCHRO.md A2) : logique pure, testable sans Cemu.

Le problème
  Un Bokoblin (proc 189) crée son bâton (daBoko_c, proc 463) et le marque CARRY
  (`actor_status +0x2E0 |= 0x2000`, mode 2 « porté par un ennemi »). Seul son execute le
  relâche (yari_off_check -> fopAcM_cancelCarryNow), et sa suppression ne le libère pas.
  Quand le Bokoblin disparaît sans passer par là, le bâton reste CARRY pour toujours :
  figé, souvent caché en (-10000, -10000), et impossible à ramasser. Ça arrive :
    - chez le non-autoritaire : la marionnette (IA suspendue) est supprimée par la
      commande 5 à sa mort ;
    - chez l'autorité : un Bokoblin achevé par un coup distant (apply_hit -> commande 5) ;
    - partout : un ennemi « orphelin » supprimé (absent chez l'autorité).
  Plutôt que d'ajouter un appel à chacun de ces endroits, on balaie : un bâton CARRY en
  mode 2 dont aucun Bokoblin vivant ne détient l'id est orphelin, quelle qu'en soit la cause.

Adresses (docs/etudes/recherche_batons.md, lues dans le code HD [HD-ASM]) :
  - bit CARRY : u32 +0x2E0 & 0x2000 (fopAcM_setCarryNow 0x025D9D0C) ;
  - mode du bâton : u8 +0x430 (0 posé, 1 chute, 2 ennemi, 3 Link, 6 lancé) ;
  - flamme : s16 +0x43C (900 = allumé ; ne décompte pas en mode 2) ;
  - modèle : +0x3B4, translation de la matrice de base à modèle +0xD4/+0xE4/+0xF4 ;
  - id du bâton chez le Bokoblin : u32 bk+0x1360 (-1 = aucun) ;
  - objet porté par Link : pointeur Link+0x65A4 (mActorKeepGrab : seulement PENDANT
    l'animation de ramassage d'un bâton ; une fois en main, il est dans mActorKeepEquip,
    Link+0x6594, avec mEquipItem u16 Link+0x69B0 = 0x101). Sans effet sur A2, qui exige le
    mode 2 ; A3 (synchro/batons.py) lit les deux pointeurs.
  La critique (points 1 et 2) se trompait sur ces deux champs ; la recherche l'a montré
  dans le code HD. Un bâton qui TOMBE a aussi le bit CARRY (mode 1) : on exige mode == 2.

Correctif (--weapon-fix-mode)
  recreer (défaut) : un bâton libre neuf est créé (commande 4, proc 463, params 0, 1 ou 7
    = bâton allumé si sa flamme brûlait) là où était son Bokoblin (+50 en y), PUIS
    l'orphelin est supprimé (commande 5). Tout passe par la boîte aux lettres, donc par le
    thread du jeu ; créer d'abord garde l'archive « Boko » chargée. Le bâton neuf naît en
    procWait, sans sol sous lui : il tombe, se pose (procWait_init) et devient ramassable
    [DECOMP daBoko_c::create / procWait].
  liberer : les deux écritures de la recherche (§6) sur le bâton lui-même : position
    voulue dans la matrice de base du modèle, puis bit 0x2000 effacé. Le jeu le fait alors
    tomber puis appelle cancelCarryNow à l'atterrissage. [HYPO, jamais essayé en jeu]
  journal : détection seule, une ligne de journal par bâton orphelin.
  --no-weapon-fix désactive tout.

Lance de Moblin : pas concernée. Le Moblin ne pose jamais CARRY, l'acteur lance n'existe
qu'une fois lâchée (recherche §5) ; le type 4 est donc exclu.
"""
from __future__ import annotations

import math
import struct

PROC_BOKO = 463            # daBoko_c (bâton, machette, masse, lance…)
PROC_BK = 189              # bk_class (Bokoblin)

ST_FIELD = 0x2E0           # u32 actor_status [HD-ASM 0x025D9D0C]
ST_CARRY = 0x2000          # fopAcStts_CARRY_e
BOKO_BLOCK_AT = 0x3B4      # une seule lecture : +0x3B4..+0x43E
BOKO_MODEL = 0x3B4         # J3DModel* mpModel
BOKO_MODE = 0x430          # u8 mNowMode [HD-ASM execute : lbz 0x430 ; cmpwi 2]
BOKO_FLAME = 0x43C         # s16 mFlameTimer
BOKO_BLOCK_SIZE = BOKO_FLAME + 2 - BOKO_BLOCK_AT
MODEL_TRANS = (0xD4, 0xE4, 0xF4)   # translation de la matrice de base (+0xC8 + 0x0C/0x1C/0x2C) [LIVE]
BK_WEAPON_ID = 0x1360      # u32 m1200 : id de process du bâton [HD-ASM 0x020A0010]
LINK_GRAB_PTR = 0x65A4     # fopAc_ac_c* mActorKeepGrab [HD-ASM] : bâton en cours de ramassage, jarres
LINK_EQUIP_ID = 0x6590     # A3 : id de mActorKeepEquip (bâton tenu) [HD-ASM]
LINK_EQUIP_PTR = 0x6594    # A3 : fopAc_ac_c* mActorKeepEquip [HD-ASM]
LINK_EQUIP_ITEM = 0x69B0   # A3 : u16 mEquipItem (0x100 rien, 0x101 bâton, 0x103 épée) [HD-ASM, LIVE]
ITEM_BOKO = 0x101
BOKO_HIDE = 0x431          # u8 : ≠ 0, pas dessiné (checkNoDraw)
BOKO_THROW = 0x432         # u8 mbThrow : JAMAIS écrit (le lancer prend l'angle du Link local)
BOKO_FLOOR = 0x434         # u8 mbFloorFlg
BOKO_ANGLE = 0x328         # 3×s16 shape_angle
MODEL_BASE_MTX = 0xC8      # J3DModel : matrice de base 3×4 (48 octets)
BP_PC_ID = 0x04            # base_process_class::mBsPcId

MODE_WAIT, MODE_MOVE, MODE_ENEMY, MODE_PLAYER, MODE_THROW = 0, 1, 2, 3, 6
TYPE_STICK, TYPE_MACHETE, TYPE_LIT = 0, 1, 7
WEAPON_TYPES = {TYPE_STICK: "bâton", TYPE_MACHETE: "machette", TYPE_LIT: "bâton allumé"}
NO_ID = (0, 0xFFFFFFFF)

DROP_Y = 50.0              # le bâton neuf naît 50 u au-dessus des pieds du Bokoblin, puis tombe
MODES = ("recreer", "liberer", "journal")


def _finite(p) -> bool:
    return p is not None and len(p) == 3 and all(isinstance(v, (int, float)) and math.isfinite(v)
                                                  and abs(v) < 1e5 for v in p)


def hidden(p) -> bool:
    """Position « parquée » par le Bokoblin (matrice à (-10000, -10000), bloc m1214) ou invalide."""
    return not _finite(p) or (abs(p[0] + 10000.0) < 1000.0 and abs(p[2] + 10000.0) < 1000.0)


def parse_stick(status_raw: bytes, block_raw: bytes) -> dict:
    """Champs d'un daBoko_c : status_raw = 4 octets à +0x2E0, block_raw = BOKO_BLOCK_SIZE à +0x3B4."""
    o = BOKO_BLOCK_AT
    return {"status": struct.unpack(">I", status_raw[:4])[0],
            "model": struct.unpack_from(">I", block_raw, BOKO_MODEL - o)[0],
            "mode": block_raw[BOKO_MODE - o],
            "flame": struct.unpack_from(">h", block_raw, BOKO_FLAME - o)[0]}


def stick_params(type_: int, flame: int) -> int:
    """Paramètres du bâton neuf : 7 = bâton allumé (daBoko_c::create : flamme 900, puis type 0)."""
    if type_ in (TYPE_STICK, TYPE_LIT) and flame > 0:
        return TYPE_LIT
    return TYPE_STICK if type_ == TYPE_LIT else type_


def is_enemy_carried(s: dict) -> bool:
    """Bâton de Bokoblin tenu par un ennemi : bit CARRY ET mode 2 (un bâton qui tombe a aussi le bit)."""
    return (bool(s.get("status", 0) & ST_CARRY) and s.get("mode") == MODE_ENEMY
            and (s.get("params", 0) & 0xFF) in WEAPON_TYPES)


def find_orphans(sticks, bokos, held_ptr=0, skip=()) -> list[dict]:
    """Bâtons tenus par un ennemi que plus aucun Bokoblin vivant ne détient.
    held_ptr : adresse (ou ensemble d'adresses) d'objets tenus par Link ; skip : ids à ignorer
    (A3 : bâtons-marionnettes qui passent un instant par le mode 2, voir synchro/batons.py)."""
    owned = {b.get("weapon") for b in bokos or () if b.get("weapon") not in NO_ID}
    held = set(held_ptr) if isinstance(held_ptr, (set, frozenset, list, tuple)) else {held_ptr}
    held.discard(0)
    skip = set(skip or ())
    return [s for s in sticks or () if is_enemy_carried(s) and s.get("pid") not in owned
            and s.get("base") not in held and s.get("pid") not in skip]


class WeaponFix:
    """Repère les armes orphelines (observe, à chaque échantillon) et dit quoi faire (step).
    L'exécution (execute) passe par un objet qui sait créer / supprimer des acteurs et écrire
    dans un bâton (CemuSampler) ; les résultats reviennent par result()."""

    CONFIRM = 1.5          # s : orphelin vu sans interruption avant d'agir (rescan, Bokoblin en création…)
    ROOM_SETTLE = 2.0      # s après l'entrée dans la zone
    RETRY = 2.0            # s entre deux essais sur le même bâton
    MAX_TRIES = 3
    MAX_PER_TICK = 2
    OWNER_TTL = 120.0      # s : dernière position connue du Bokoblin d'un bâton

    def __init__(self, enabled: bool = True, mode: str = "recreer"):
        if mode not in MODES:
            raise ValueError(f"mode inconnu : {mode} (attendu : {', '.join(MODES)})")
        self.enabled = enabled
        self.mode = mode
        self.zone: str | None = None
        self.owners: dict[int, tuple] = {}     # id du bâton -> (pos, salle, heure) de son Bokoblin
        self.first: dict[int, float] = {}      # id du bâton orphelin -> première fois vu orphelin
        self.cands: dict[int, dict] = {}       # orphelins de ce balayage
        self.tries: dict[int, tuple[int, float]] = {}   # id -> (essais, prochain essai)
        self.done: set[int] = set()            # traités (ou abandonnés)
        self.pending_delete: dict[int, float] = {}   # recreer : bâton neuf créé, orphelin pas encore supprimé
        self.logged: set[int] = set()
        self.recreated = self.released = self.detected = self.failures = 0
        self.last = ""

    def reset(self) -> None:
        self.owners.clear()
        self.first.clear()
        self.cands.clear()
        self.tries.clear()
        self.done.clear()
        self.pending_delete.clear()
        self.logged.clear()

    def observe(self, now: float, zone: str | None, sticks, bokos, held_ptr=0, skip=()) -> None:
        """sticks : [{pid, base, params, status, mode, flame, model, pos, room}] ;
        bokos : [{pid, base, weapon, pos, room}] ; None = pas de relevé (chargement).
        skip : ids de bâtons à ne jamais traiter (A3 : marionnettes en transition)."""
        if not self.enabled:
            return
        if zone != self.zone:
            self.zone = zone
            self.reset()                       # nouvelle zone : acteurs neufs
        if zone is None or sticks is None or bokos is None:
            self.cands = {}
            return
        for b in bokos:
            w = b.get("weapon")
            if w not in NO_ID and _finite(b.get("pos")):
                self.owners[w] = (tuple(b["pos"]), b.get("room", -1), now)
        present = {s.get("pid") for s in sticks}
        for pid in [p for p in self.pending_delete if p not in present]:
            del self.pending_delete[pid]       # l'orphelin a disparu : suppression faite
        self.cands = {s["pid"]: s for s in find_orphans(sticks, bokos, held_ptr, skip) if s["pid"] not in self.done}
        for pid in list(self.first):
            if pid not in self.cands:
                del self.first[pid]            # de nouveau détenu (ou disparu) : on repart de zéro
        for pid in self.cands:
            self.first.setdefault(pid, now)
        for pid in [p for p, o in self.owners.items() if now - o[2] > self.OWNER_TTL and p not in present]:
            del self.owners[pid]

    def drop_pos(self, s: dict, fallback=None):
        """Où faire tomber le bâton : sur son Bokoblin (dernière position vue), sinon là où il
        est s'il est visible, sinon sur Link (fallback). Renvoie (pos, salle, origine)."""
        o = self.owners.get(s.get("pid"))
        if o is not None:
            p = o[0]
            return (p[0], p[1] + DROP_Y, p[2]), o[1] if o[1] >= 0 else s.get("room", 0), "Bokoblin"
        p = s.get("pos")
        if not hidden(p):
            return (p[0], p[1] + 10.0, p[2]), s.get("room", 0), "bâton"
        if fallback is not None and _finite(fallback[0]):
            p = fallback[0]
            return (p[0], p[1] + DROP_Y, p[2]), fallback[1], "Link"
        return None, s.get("room", 0), "inconnue"

    def step(self, now: float, eligible: bool, can_write: bool, fallback=None) -> list[dict]:
        """Travaux à faire ce tick : {kind: 'recreer'|'liberer'|'journal'|'delete', pid, …}.
        eligible : zone stable, pas d'événement ; can_write : boîte aux lettres disponible
        (sinon : journal seul). fallback : (position de Link, salle)."""
        if not self.enabled or not eligible:
            return []
        jobs = []
        for pid, t in list(self.pending_delete.items()):
            if now >= t:
                jobs.append({"kind": "delete", "pid": pid})
        for pid in sorted(self.cands):
            if len(jobs) >= self.MAX_PER_TICK:
                break                          # la suite au prochain tick (rien n'est marqué)
            if pid in self.done or now - self.first.get(pid, now) < self.CONFIRM:
                continue
            _n, t_next = self.tries.get(pid, (0, 0.0))
            if now < t_next or pid in self.pending_delete:
                continue
            s = self.cands[pid]
            typ = s.get("params", 0) & 0xFF
            params = stick_params(typ, s.get("flame", 0))
            desc = f"{WEAPON_TYPES.get(params, '?')} {PROC_BOKO}/{s.get('params', 0):08X}"
            first_time = pid not in self.logged
            if first_time:
                self.logged.add(pid)
                self.detected += 1
            if self.mode == "journal" or not can_write:
                # journal : une ligne par bâton ; sans boîte aux lettres, on réessaiera si elle revient
                if self.mode == "journal":
                    self.done.add(pid)
                if first_time:
                    jobs.append({"kind": "journal", "pid": pid, "desc": desc})
                continue
            pos, room, src = self.drop_pos(s, fallback)
            if pos is None:
                self.done.add(pid)
                self.failures += 1
                jobs.append({"kind": "journal", "pid": pid, "desc": desc + " (position inconnue, laissé)"})
                continue
            jobs.append({"kind": self.mode, "pid": pid, "base": s.get("base"), "params": params, "pos": pos,
                         "room": room, "src": src, "desc": desc})
        return jobs[:self.MAX_PER_TICK]

    def result(self, job: dict, ok: bool, now: float, deleted: bool = True) -> None:
        """ok : création (recreer) ou écritures (liberer) faites ; deleted : orphelin supprimé
        (recreer seulement ; sinon nouvel essai de suppression plus tard)."""
        pid, kind = job["pid"], job["kind"]
        if kind == "journal":
            return
        if kind == "delete":
            if ok:
                self.pending_delete.pop(pid, None)
                self.done.add(pid)
            else:
                n = self.tries.get(pid, (0, 0.0))[0] + 1
                self.tries[pid] = (n, now + self.RETRY)
                if n >= self.MAX_TRIES:
                    self.pending_delete.pop(pid, None)
                    self.done.add(pid)
                    self.failures += 1
                else:
                    self.pending_delete[pid] = now + self.RETRY
            return
        if ok:
            if kind == "recreer":
                self.recreated += 1
                if deleted:
                    self.done.add(pid)
                else:
                    self.pending_delete[pid] = now + self.RETRY
            else:
                self.released += 1
                self.done.add(pid)
            self.last = f"{job['desc']} ({job['src']})"
            return
        n = self.tries.get(pid, (0, 0.0))[0] + 1
        self.tries[pid] = (n, now + self.RETRY)
        if n >= self.MAX_TRIES:
            self.done.add(pid)
            self.failures += 1

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = (f"mode {self.mode} : {self.detected} orpheline(s) vue(s), {self.recreated} recréée(s), "
             f"{self.released} libérée(s)")
        if self.failures:
            s += f", {self.failures} échec(s)"
        return s + (f" ; dernière : {self.last}" if self.last else "")


def execute(job: dict, ex) -> tuple[bool, bool, str]:
    """Exécute un travail de WeaponFix.step avec `ex` (create_actor, delete_actor, release_stick).
    Renvoie (ok, supprimé, message pour le journal). En mode recreer, l'id du bâton neuf est
    rangé dans job["new"] (A3 : la clé réseau du bâton passe de l'orphelin au bâton neuf)."""
    kind, pid = job["kind"], job["pid"]
    if kind == "journal":
        return True, False, f"arme orpheline (non ramassable) : {job['desc']}"
    if kind == "delete":
        ok = bool(ex.delete_actor(pid))
        return ok, ok, "" if ok else f"arme orpheline non supprimée (id {pid:08X})"
    pos = job["pos"]
    where = f"({pos[0]:.0f}, {pos[1]:.0f}, {pos[2]:.0f})"
    if kind == "recreer":
        new = ex.create_actor(PROC_BOKO, job["params"], pos, job["room"])
        job["new"] = new
        if new is None:
            return False, False, f"arme orpheline : bâton neuf non créé ({job['desc']})"
        deleted = bool(ex.delete_actor(pid))
        return True, deleted, (f"arme libérée : {job['desc']} recréée en {where}, sur le {job['src']}"
                               + ("" if deleted else " ; ancienne pas encore supprimée"))
    ok = bool(ex.release_stick(pid, job["base"], pos))
    return ok, False, (f"arme libérée : {job['desc']} relâchée en {where}, sur le {job['src']}" if ok
                       else f"arme orpheline : libération refusée ({job['desc']})")


def release_stick(mem, pid: int, base: int, pos) -> bool:
    """Mode liberer (recherche §6) : vérifie que `base` est toujours ce bâton orphelin, écrit la
    position voulue dans la matrice de base de son modèle, puis efface le bit CARRY. [HYPO]"""
    raw = mem.try_read(base, 8)
    if raw is None or struct.unpack_from(">I", raw, BP_PC_ID)[0] != pid or struct.unpack_from(">I", raw, 0)[0] == 0:
        return False                                      # acteur supprimé ou réutilisé
    st_raw, blk = mem.try_read(base + ST_FIELD, 4), mem.try_read(base + BOKO_BLOCK_AT, BOKO_BLOCK_SIZE)
    if st_raw is None or blk is None:
        return False
    s = parse_stick(st_raw, blk)
    if not (s["status"] & ST_CARRY) or s["mode"] != MODE_ENEMY or not 0x10000000 <= s["model"] < 0x50000000:
        return False
    for off, v in zip(MODEL_TRANS, pos):
        mem.write(s["model"] + off, struct.pack(">f", float(v)))
    st = struct.unpack(">I", mem.try_read(base + ST_FIELD, 4) or st_raw)[0]
    mem.write(base + ST_FIELD, struct.pack(">I", st & ~ST_CARRY & 0xFFFFFFFF))
    return True


# ---------------------------------------------------------------------------
# A3 : bâtons partagés (marionnettes de bâton), écritures gardées. Voir synchro/batons.py et
# docs/etudes/batons_partages.md §7. Chaque fonction relit l'acteur juste avant d'écrire.
# ---------------------------------------------------------------------------
def check_stick(mem, pid: int, base: int) -> dict | None:
    """Relit le bâton : id de process == pid, acteur vivant (mot 0 non nul), statut et bloc
    lisibles, modèle plausible. Renvoie les champs (parse_stick) ou None."""
    if not base:
        return None
    raw = mem.try_read(base, 8)
    if raw is None or struct.unpack_from(">I", raw, BP_PC_ID)[0] != pid or struct.unpack_from(">I", raw, 0)[0] == 0:
        return None                                       # acteur supprimé ou réutilisé
    st_raw, blk = mem.try_read(base + ST_FIELD, 4), mem.try_read(base + BOKO_BLOCK_AT, BOKO_BLOCK_SIZE)
    if st_raw is None or blk is None:
        return None
    s = parse_stick(st_raw, blk)
    if not 0x10000000 <= s["model"] < 0x50000000:
        return None
    return s


def _finite12(m) -> bool:
    return m is not None and len(m) == 12 and all(isinstance(v, (int, float)) and math.isfinite(v)
                                                   and abs(v) < 1e6 for v in m)


def _set_status(mem, base: int, set_bits: int = 0, clear_bits: int = 0) -> None:
    raw = mem.try_read(base + ST_FIELD, 4)
    if raw is None:
        raise RuntimeError("statut illisible")
    st = struct.unpack(">I", raw)[0]
    mem.write(base + ST_FIELD, struct.pack(">I", ((st | set_bits) & ~clear_bits) & 0xFFFFFFFF))


def grab_puppet(mem, pid: int, base: int, mode: int = MODE_PLAYER) -> bool:
    """Prise d'un bâton au repos par la marionnette : mode D'ABORD, puis CARRY. procWait voit
    CARRY, passe en procCarry, garde le mode 3 (sinon force le mode 2) et efface l'attention
    0x10 (plus ramassable). mode 3 (défaut) : la flamme décompte comme chez le porteur ; mode 2
    (--stick-puppet-mode ennemi) : le bâton reste dessiné quand le Link local est caché (vue
    subjective), mais la flamme ne décompte pas. [DECOMP l.381-393, HD-ASM 0x020C3B08 ; HYPO en jeu]"""
    if mode not in (MODE_PLAYER, MODE_ENEMY):
        return False
    s = check_stick(mem, pid, base)
    if s is None or s["mode"] != MODE_WAIT or s["status"] & ST_CARRY:
        return False
    mem.write(base + BOKO_MODE, bytes([mode]))
    _set_status(mem, base, set_bits=ST_CARRY)
    return True


def release_held(mem, pid: int, base: int) -> bool:
    """Conflit perdu (A3, deny) : le bâton quitte la main du Link LOCAL sans commande 5. Mode 2
    D'ABORD (pas de test de ligne depuis les pieds de Link, d_a_boko l.686), puis CARRY effacé :
    procCarry passe en procMove (le bâton tombe), et setActorPointer, à la frame suivante, voit
    !fopAcM_checkCarryNow et vide mActorKeepEquip (d_a_player_main l.2120-2124). Rien à faire
    (True) si le bâton n'est déjà plus tenu (mode ≠ 3 ou pas CARRY). [DECOMP ; HYPO en jeu]"""
    s = check_stick(mem, pid, base)
    if s is None:
        return False
    if s["mode"] != MODE_PLAYER or not s["status"] & ST_CARRY:
        return True
    mem.write(base + BOKO_MODE, bytes([MODE_ENEMY]))
    _set_status(mem, base, clear_bits=ST_CARRY)
    return True


def set_stick_matrix(mem, pid: int, base: int, w) -> bool:
    """Pilotage : écrit la matrice de base 3×4 (12 flottants, ligne par ligne) du modèle. Le bâton
    en procCarry relit sa position et son angle dans cette matrice."""
    s = check_stick(mem, pid, base)
    if s is None or s["mode"] not in (MODE_PLAYER, MODE_ENEMY) or not s["status"] & ST_CARRY or not _finite12(w):
        return False
    mem.write(s["model"] + MODEL_BASE_MTX, struct.pack(">12f", *(float(v) for v in w)))
    return True


def drop_puppet(mem, pid: int, base: int, pos, rot=(0, 0, 0), floor: bool = False) -> bool:
    """Relâchement d'une marionnette : matrice de repos T(pos)·ZXYrot(rot), mode 2 (le test de
    ligne depuis les pieds du Link LOCAL n'est fait qu'en mode 3), puis CARRY effacé : le bâton
    tombe (procMove), se pose et procWait_init le rend ramassable. floor : mbFloorFlg = 1 avant
    (procWait_init direct, sans chute). [DECOMP + HD-ASM ; HYPO en jeu]"""
    from synchro.batons import rest_matrix
    s = check_stick(mem, pid, base)
    if s is None or s["mode"] not in (MODE_PLAYER, MODE_ENEMY) or not s["status"] & ST_CARRY or not _finite(pos):
        return False
    mem.write(s["model"] + MODEL_BASE_MTX, struct.pack(">12f", *rest_matrix(pos, rot)))
    mem.write(base + BOKO_MODE, bytes([MODE_ENEMY]))
    if floor:
        mem.write(base + BOKO_FLOOR, b"\x01")
    _set_status(mem, base, clear_bits=ST_CARRY)
    return True


def set_flame(mem, pid: int, base: int, value: int) -> bool:
    """Flamme du bâton (s16 +0x43C) : 900 allume [HYPO : setFlameEffect relance l'effet] ; pour
    éteindre, écrire 1 (jamais 0) : le jeu décompte à 0 et fait le nettoyage lui-même."""
    s = check_stick(mem, pid, base)
    if s is None:
        return False
    v = max(-1, min(900, int(value)))
    mem.write(base + BOKO_FLAME, struct.pack(">h", v))
    return True
