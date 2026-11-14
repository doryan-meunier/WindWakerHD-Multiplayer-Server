"""Synchro v2 : petites clés de donjon partagées (--share-keys, activé par --coop-donjon, --no-keys).

Étude : docs/etudes/sync2_cles.md (la section « Vérification » prime). Résumé :

* Un seul compteur par donjon pour tout le groupe. Le serveur (``KeyPool``) ne tient pas un
  nombre mais des ENSEMBLES d'identifiants par groupe et par numéro de sauvegarde (``slot`` =
  ``mDan.mStageNo``) : ``cible = max(0, base + |gets| - |uses| + credit)`` (``credit`` : portes
  payées sans clé commune, pas de dette, §3.1). Idempotent (un même coffre, une
  même clé au sol ou une même porte ne comptent qu'une fois, quels que soient le nombre de joueurs,
  l'ordre et les renvois après reconnexion) et commutatif tant que le compteur ne passe pas sous 0
  (sinon ``credit`` : une clé de trop plutôt qu'une absorbée). ``base`` = max des valeurs annoncées
  par les clients (``hello``) à la première vue du donjon.
* Le client (``KeySync``) déclare des FAITS, jamais des nombres :
    - ``key_get`` : la valeur effective ``E = mKeyNum + pending`` monte PENDANT un événement
      (démo d'obtention, ou dans les EVENT_TAIL s qui suivent) ;
    - ``key_use`` : ``E`` baisse pendant un événement (porte à cadenas, ``keyInit``).
  Un changement HORS événement (continuer après une mort, outil externe…) n'est jamais un fait :
  convergence seule.
* Identifiants (§3.2) : ``t<n>`` coffre (bit ``mTbox`` n passé à 1 ICI, ou coffre de
  ``mEvtCtrl.mPt2``), ``i<n>`` clé au sol (bit ``mItem``), ``z<salle>:<n>`` clé au sol à bit de
  zone, ``s<sw>`` porte (switch sauvegardé passé à 1 ici, ou door10 voisine avec ``m20`` = 1),
  ``a<joueur>:<seq>`` sinon (unique : au pire une clé de TROP, jamais une de moins). Les bits posés
  par la progression partagée (A1, ``note_applied``) sont EXCLUS de l'attribution (R1 : sinon la clé
  d'un autre coffre serait prise pour un doublon et perdue). L'attribution par bit exige
  ``mEvtCtrl.mGetItemNo == 0x15``.
* Convergence (§3.6) : hors événement, ``pending`` (s16 ``0x10474C0C``) = ``cible - E`` (borné à
  ±9), le jeu l'applique lui-même (``dMeter_keyMove`` : borne 0..99, animation du chiffre). Gardes
  relues juste avant l'unique écriture : jeu libre depuis STABLE s, ``nextStage.enabled == 0``,
  ``pending == 0``, ``E`` stable, aucune door10 en animation de cadenas, aucun fait du donjon non
  accusé, rien en vol. La détection n'est JAMAIS suspendue : l'écriture déplace ``E`` de ``d``
  d'un coup, la ligne de base est décalée d'autant (correction du vérificateur, R6). Échéance
  FLIGHT s : si ``pending`` vaut encore ``d`` (menu pause, stage sans affichage des clés), il est
  REMIS À 0 (obligatoire, R8 : sinon il pourrait s'appliquer dans un autre donjon).
* Autre donjon (le joueur est ailleurs) : écriture directe de l'octet ``mKeyNum`` de la copie
  sauvegardée ``gi+0x3A0+slot*0x24+0x20`` (le jeu ne la lit qu'en entrant dans la stage).
* Porte payée deux fois (A13 en échec) : ``s<sw>`` déjà dans ``uses`` -> ``dup_use`` -> la
  cible n'a pas bougé -> la convergence rend la clé (« porte déjà payée par X : clé rendue »).

RÈGLE TRANSVERSALE (R19) : aucun autre module ne donne JAMAIS l'objet 0x15 (petite clé) par
``execItemGet`` (pack v18/v19 : coffres, drops, inventaire…). Une petite clé donnée ainsi serait
comptée deux fois (par ce module et par l'autre) et ``item_func_small_key`` écrirait ``pending``
hors événement, en course avec la convergence. Les petites clés passent UNIQUEMENT par ce module.
Voir ``ITEM_NEVER_GIVE`` et ``assert_not_small_key``.

Pack v19 : ``KeySync.key_add`` est un point d'accroche ``key_add(delta, slot) -> bool`` (commande
10 KEYADD exécutée par le jeu, atomique : True = ajouté, False = refusé par le jeu : événement ou
autre stage, ou requête annulée faute de réponse), branché par le client (``Mailbox.key_add``)
seulement si la boîte installée est en v19. Avec le pack v17 (cmd 1-6 seulement), il reste None :
écriture Python gardée (mode dégradé, course résiduelle R7/R20 de quelques µs). Le pack v19 refuse
lui-même de donner 0x15 (``wwmp_never_give``, ``jeu/mailbox.NEVER_GIVE``).
"""
from __future__ import annotations

import itertools
import json
import os
import random
import struct
import time
from dataclasses import dataclass, field

from jeu import progress as prog
from commun.protocole import KEY_MAX, KEY_SLOTS, valid_key_id, valid_key_slot

# ---------------------------------------------------------------------------
# Adresses [HD-ASM + LIVE, docs/etudes/sync2_cles.md §2]
# ---------------------------------------------------------------------------
MEM_KEYNUM = 0x20                  # u8 dSv_memBit_c.mKeyNum
MEM_TBOX, MEM_ITEM = 0x00, 0x14    # u32 BE mTbox, mItem[0]
GI_KEYNUM_LIVE = prog.GI_CUR_MEMORY + MEM_KEYNUM    # gi+0x7B8 (dMeter_keyMove 0x0259C7FC)
ADDR_KEY_PENDING = prog.SYM_KEY_PENDING   # s16 mItemKeyNumCount 0x10474C0C (play+0x5B5C)
EVT_GET_ITEM_NO = 0xD4             # u8 dEvt_control_c::mGetItemNo [LIVE : 0x15 après un coffre à clé]
ADDR_GET_ITEM_NO = prog.SYM_EVT_CTRL + EVT_GET_ITEM_NO
ADDR_EVT_PT2 = prog.SYM_EVT_CTRL + prog.EVT_PT + 4   # u32 mPt2 : id du dernier coffre [LIVE]
ITEM_SMALL_KEY = 0x15
ITEM_NEVER_GIVE = frozenset({ITEM_SMALL_KEY})       # R19 : jamais donné par execItemGet ailleurs
PROC_TBOX = 292                    # daTbox_c « takara » [LIVE, sync2_coffres.md §2.1]
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
PROC_DOOR10 = 300
D10_KEY_ANIM = 0x4C0               # u8 mKeyLock.m20 (animation du cadenas, keyInit)
ZONE_ITEM = prog.ZONE_BITS + 0x06  # u16 BE dSv_zoneBit_c.mItem (bits 0x40-0x4F)
PENDING_MAX = 9                    # |d| au plus par écriture de convergence

# HD-ASM, documentation seule
HD_KEY_MOVE = 0x0259C7FC           # dMeter_keyMove : applique pending, borne 0..99
HD_ITEM_FUNC_SMALL_KEY = 0x0254DFA4  # item_func_small_key : pending += 1
HD_KEY_INIT = 0x0252B4A4           # dDoor_key2_c::keyInit : onSwitch + pending -= 1


def assert_not_small_key(item_no: int) -> None:
    """R19 : à appeler par tout module qui donnerait un objet par execItemGet (pack v18+)."""
    if item_no in ITEM_NEVER_GIVE:
        raise ValueError("la petite clé (0x15) n'est jamais donnée par execItemGet : "
                         "elle passe uniquement par synchro/cles.py (compteur commun)")


def _bits32(word: int) -> set[int]:
    return {n for n in range(32) if word >> n & 1}


def _bits16(word: int) -> set[int]:
    return {n for n in range(16) if word >> n & 1}


def switches_of(data: bytes) -> set[int]:
    """mSwitch[4] (0x10 octets, u32 BE) -> switches sauvegardés 0x00-0x7F à 1."""
    out = set()
    for b, v in enumerate(data):
        for j in range(8):
            if v >> j & 1:
                out.add((b // 4) * 32 + (3 - b % 4) * 8 + j)
    return out


# ---------------------------------------------------------------------------
# Serveur : réserve de clés (logique pure)
# ---------------------------------------------------------------------------
class KeyPool:
    """Ensembles d'identifiants par groupe et par donjon (§3.1, §3.5)."""

    MAX_IDS = 256          # par donjon et par ensemble
    MAX_GROUPS = 64

    def __init__(self):
        self.groups: dict[str, dict[int, dict]] = {}
        self.rev = 0

    def _new(self, base: int) -> dict:
        self.rev += 1
        return {"base": max(0, min(KEY_MAX, int(base))), "gets": {}, "uses": {}, "credit": 0, "rev": self.rev}

    @staticmethod
    def _raw(e: dict) -> int:
        return e["base"] + len(e["gets"]) - len(e["uses"]) + e.get("credit", 0)

    @staticmethod
    def target_of(e: dict) -> int:
        """cible = max(0, base + |gets| - |uses| + credit). ``credit`` = portes payées SANS clé
        commune (deux portes ouvertes en même temps avec une seule clé, §3.1 / R4) : pas de dette,
        la clé ramassée ensuite est en surplus (sinon elle serait absorbée : donjon bloqué)."""
        return max(0, min(KEY_MAX, KeyPool._raw(e)))

    def target(self, group: str, slot: int) -> int | None:
        e = self.groups.get(group, {}).get(slot)
        return None if e is None else self.target_of(e)

    def hello(self, group: str, slots: dict) -> list[tuple[int, int]]:
        """Valeurs annoncées {slot: E}. Un donjon inconnu du groupe naît avec base = valeur
        annoncée ; un second hello du même instant (même rev) le remonte au max. Un donjon déjà
        vivant (au moins un fait) n'est jamais modifié : le serveur fait autorité.
        Renvoie [(slot, cible)] des donjons créés ou remontés."""
        if group not in self.groups and len(self.groups) >= self.MAX_GROUPS:
            return []
        g = self.groups.setdefault(group, {})
        out = []
        for slot, val in slots.items():
            if not valid_key_slot(slot) or not isinstance(val, int) or isinstance(val, bool):
                continue
            val = max(0, min(KEY_MAX, val))
            e = g.get(slot)
            if e is None:
                g[slot] = self._new(val)
                out.append((slot, self.target_of(g[slot])))
            elif not e["gets"] and not e["uses"] and val > e["base"]:
                # base = max des valeurs annoncées tant qu'aucun fait n'a été compté (R11)
                e["base"] = val
                self.rev += 1
                e["rev"] = self.rev
                out.append((slot, self.target_of(e)))
        return out

    def fact(self, group: str, kind: str, slot: int, kid: str, by: str) -> tuple[bool, dict]:
        """kind = "get" | "use". Renvoie (nouveau ?, entrée). Un donjon jamais annoncé naît avec
        base 0 (le client envoie toujours hello avant ses faits)."""
        if kind not in ("get", "use"):
            raise ValueError(kind)
        g = self.groups.setdefault(group, {})
        e = g.get(slot)
        if e is None:
            e = g[slot] = self._new(0)
        s = e["gets" if kind == "get" else "uses"]
        if kid in s or (kind == "get" and kid in e.get("inclus", ())):
            return False, e
        if len(s) >= self.MAX_IDS:
            raise ValueError("trop d'identifiants pour ce donjon")
        s[kid] = str(by)[:24]
        raw = self._raw(e)
        if raw < 0:                     # pas de dette (§3.1) : cette porte n'a rien coûté au groupe
            e["credit"] = e.get("credit", 0) - raw
        self.rev += 1
        e["rev"] = self.rev
        return True, e

    def inclure(self, group: str, slot: int, kid: str, by: str) -> bool:
        """Note qu'une clé (identifiant ``kid``) est DÉJÀ comprise dans la base annoncée de ce donjon : elle ne
        sera plus jamais comptée (``fact`` la refuse), la cible ne bouge pas. Sert au randomizer (lot 5) après
        « Rejoindre la partie de l'hôte » : la réserve est oubliée, les jeux réannoncent des compteurs qui
        contiennent déjà la clé du coffre étranger, et ce coffre est signalé de nouveau. Renvoie True si noté."""
        e = self.groups.get(group, {}).get(slot)
        if e is None or not valid_key_id(kid) or kid in e["gets"]:
            return False
        inc = e.setdefault("inclus", {})
        if kid in inc or len(inc) >= self.MAX_IDS:
            return False
        inc[kid] = str(by)[:24]
        return True

    def table(self, group: str) -> dict:
        return {str(slot): [self.target_of(e), e["rev"]] for slot, e in sorted(self.groups.get(group, {}).items())}

    def reset(self, group: str) -> bool:
        return self.groups.pop(group, None) is not None

    # --- persistance (optionnelle : --keys-file) -----------------------------
    def to_json(self) -> dict:
        return {"rev": self.rev, "groups": {g: {str(s): e for s, e in d.items()} for g, d in self.groups.items()}}

    @classmethod
    def from_json(cls, obj) -> "KeyPool":
        """Décode et VALIDE (fichier abîmé : entrées ignorées)."""
        kp = cls()
        if not isinstance(obj, dict):
            return kp
        groups = obj.get("groups")
        for g, d in (groups.items() if isinstance(groups, dict) else []):
            if not isinstance(g, str) or not isinstance(d, dict):
                continue
            for s, e in d.items():
                try:
                    slot = int(s)
                except (TypeError, ValueError):
                    continue
                if not valid_key_slot(slot) or not isinstance(e, dict):
                    continue
                base, gets, uses = e.get("base"), e.get("gets"), e.get("uses")
                if not isinstance(base, int) or not isinstance(gets, dict) or not isinstance(uses, dict):
                    continue
                ent = {"base": max(0, min(KEY_MAX, base)),
                       "gets": {k: str(v)[:24] for k, v in list(gets.items())[:cls.MAX_IDS] if valid_key_id(k)},
                       "uses": {k: str(v)[:24] for k, v in list(uses.items())[:cls.MAX_IDS] if valid_key_id(k)},
                       "rev": e.get("rev") if isinstance(e.get("rev"), int) else 0}
                credit = e.get("credit", 0)
                ent["credit"] = (max(0, min(cls.MAX_IDS, credit))
                                 if isinstance(credit, int) and not isinstance(credit, bool) else 0)
                inclus = e.get("inclus")
                if isinstance(inclus, dict) and inclus:    # clés déjà comprises dans la base (voir ``inclure``)
                    inc = {k: str(v)[:24] for k, v in list(inclus.items())[:cls.MAX_IDS]
                           if valid_key_id(k) and k not in ent["gets"]}
                    if inc:
                        ent["inclus"] = inc
                kp.groups.setdefault(g[:32], {})[slot] = ent
        rev = obj.get("rev")
        kp.rev = max([rev if isinstance(rev, int) else 0]
                     + [e["rev"] for d in kp.groups.values() for e in d.values()])
        return kp

    def save(self, path: str) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "KeyPool":
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_json(json.load(f))
        except FileNotFoundError:
            return cls()


# ---------------------------------------------------------------------------
# Client : lecture du jeu (lecture seule)
# ---------------------------------------------------------------------------
@dataclass
class KeyView:
    ok: bool
    reason: str = ""
    gi: int = 0
    stage: str = ""
    slot: int | None = None            # mDan.mStageNo (None = invalide)
    keynum: int = 0                    # mKeyNum vivant
    pending: int = 0                   # s16 mItemKeyNumCount
    busy: bool = False                 # mMode != 0 ou mbEndProc != 0 (brut)
    next_stage: bool = False           # dStage_nextStage_c::enabled
    get_item: int = 0                  # mGetItemNo
    pt2: int = 0xFFFFFFFF              # mPt2
    tbox: set = field(default_factory=set)
    items: set = field(default_factory=set)
    switches: set = field(default_factory=set)
    zitems: set = field(default_factory=set)      # {(salle, n)}
    zone_rooms: set = field(default_factory=set)  # salles dont la zone est chargée
    saved: dict = field(default_factory=dict)     # slot -> mKeyNum de la copie sauvegardée

    @property
    def effective(self) -> int:
        return self.keynum + self.pending

    @property
    def keyed(self) -> bool:
        return self.slot is not None and self.slot in KEY_SLOTS


def read_keys(mem) -> KeyView:
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return KeyView(False, "gameInfo illisible")
    gi = struct.unpack(">I", raw)[0]
    if not 0x10000000 <= gi < 0x50000000:
        return KeyView(False, "pas de sauvegarde chargée")
    st = mem.try_read(prog.SYM_CUR_STAGE, 0x20)
    if st is None:
        return KeyView(False, "stage illisible")
    stage = st[:8].split(b"\0", 1)[0].decode("ascii", "replace")
    if stage in prog.TITLE_STAGES:
        return KeyView(False, f"écran titre ({stage or 'vide'})", gi, stage)
    player = mem.try_read(prog.SYM_PLAYER, 4)
    if player is None or struct.unpack(">I", player)[0] == 0:
        return KeyView(False, "Link absent (chargement)", gi, stage)
    blob = mem.try_read(gi + prog.GI_MEMORY, prog.GI_ZONE + prog.ZONE_SIZE * prog.N_ZONES - prog.GI_MEMORY)
    pend = mem.try_read(ADDR_KEY_PENDING, 2)
    ev = prog.read_event(mem)
    extra = mem.try_read(prog.SYM_EVT_CTRL + prog.EVT_PT, EVT_GET_ITEM_NO + 1 - prog.EVT_PT)
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if blob is None or pend is None or ev is None or extra is None or nxt is None:
        return KeyView(False, "mémoire illisible", gi, stage)

    def at(off: int, n: int) -> bytes:
        return blob[off - prog.GI_MEMORY: off - prog.GI_MEMORY + n]

    v = KeyView(True, "", gi, stage)
    dan = struct.unpack(">b", at(prog.GI_DAN + prog.DAN_STAGE_NO, 1))[0]
    v.slot = dan if 0 <= dan < prog.N_MEM else None
    cur = at(prog.GI_CUR_MEMORY, prog.MEM_SIZE)
    v.keynum = cur[MEM_KEYNUM]
    v.pending = struct.unpack(">h", pend)[0]
    v.busy = ev.busy
    v.next_stage = bool(nxt[0])
    v.pt2 = struct.unpack_from(">I", extra, 4)[0]
    v.get_item = extra[EVT_GET_ITEM_NO - prog.EVT_PT]
    v.tbox = _bits32(struct.unpack_from(">I", cur, MEM_TBOX)[0])
    v.items = _bits32(struct.unpack_from(">I", cur, MEM_ITEM)[0])
    v.switches = switches_of(cur[0x04:0x14])
    for z in range(prog.N_ZONES):
        zb = prog.GI_ZONE + z * prog.ZONE_SIZE
        room = struct.unpack(">b", at(zb, 1))[0]
        if room >= 0:
            v.zone_rooms.add(room)
            for n in _bits16(struct.unpack(">H", at(zb + ZONE_ITEM, 2))[0]):
                v.zitems.add((room, n))
    for s in KEY_SLOTS:
        v.saved[s] = at(prog.GI_MEMORY + s * prog.MEM_SIZE + MEM_KEYNUM, 1)[0]
    return v


def read_door_anims(mem, doors) -> list[tuple[int, int]]:
    """door10 chargées -> [(switch, m20)] (lecture seule ; acteur relu : même id de process)."""
    out = []
    for pid, base, proc in doors or []:
        if proc != PROC_DOOR10:
            continue
        raw = mem.try_read(base + OFF_PC_ID, 4)
        prm = mem.try_read(base + OFF_PARAMS, 4)
        anim = mem.try_read(base + D10_KEY_ANIM, 1)
        if raw is None or prm is None or anim is None or struct.unpack(">I", raw)[0] != pid:
            continue
        out.append((struct.unpack(">I", prm)[0] & 0xFF, anim[0]))
    return out


def tbox_number(mem, tboxes, pid: int) -> int | None:
    """Coffre d'id de process ``pid`` parmi les acteurs chargés -> getTboxNo() = params >> 7 & 0x1F
    [DECOMP d_a_tbox.h] ; None si absent ou si ce n'est pas un daTbox."""
    for apid, base in tboxes or []:
        if apid != pid:
            continue
        raw = mem.try_read(base + OFF_PC_ID, 8)
        prm = mem.try_read(base + OFF_PARAMS, 4)
        if raw is None or prm is None:
            return None
        if struct.unpack_from(">I", raw, 0)[0] != pid or struct.unpack_from(">h", raw, 4)[0] != PROC_TBOX:
            return None
        return (struct.unpack(">I", prm)[0] >> 7) & 0x1F
    return None


# ---------------------------------------------------------------------------
# Client : détection, file fiable, convergence
# ---------------------------------------------------------------------------
class KeySync:
    STABLE = 0.5          # s : jeu libre, contexte et E stables avant une écriture
    FLIGHT = 1.5          # s : échéance d'une écriture de pending
    EVENT_TAIL = 2.0      # s après un événement pendant lesquels un changement de E est un fait
    TBOX_WINDOW = 15.0    # s : bit de coffre local candidat pour la clé qui suit
    ITEM_WINDOW = 5.0     # s : bit d'objet (mItem / zone) local candidat
    USE_WINDOW = 3.0      # s : switch local / animation de cadenas candidats pour une porte
    APPLIED_WINDOW = 5.0  # s : un bit posé par A1 n'est jamais « local » pendant ce temps
    OTHER_SETTLE = 1.0    # s de contexte stable avant d'écrire la copie sauvegardée d'un autre donjon
    RETRY = (3.0, 6.0, 10.0)
    MAX_FACTS = 256
    ACK_TIMEOUT = 10.0    # s sans accusé (connecté) avant de renvoyer un fait
    MAX_TRIES = 3         # envois d'un fait au plus pendant une même connexion

    def __init__(self, enabled: bool = True, group: str = "default", clock=time.monotonic,
                 pnum: int | None = None):
        self.enabled, self.group, self.clock = enabled, group, clock
        self.pnum = pnum if pnum is not None else random.randrange(1, 10 ** 9)
        self.seq = itertools.count(1)
        self.key_add = None                 # pack v19 : key_add(delta, slot) -> bool (None = v17)
        self.active = False                 # le serveur a répondu au hello au moins une fois
        self.synced = False                 # table complète reçue pendant cette connexion
        self.targets: dict[int, tuple[int, int]] = {}   # slot -> (cible, rev)
        self.facts: dict[int, dict] = {}    # seq -> message key_get / key_use non accusé
        self.sent: dict[int, float] = {}    # seq envoyés pendant cette connexion -> heure
        self.tries: dict[int, int] = {}     # seq -> envois sans accusé (connexion active)
        self.need_hello = True
        self.hello_vals: dict[int, int] | None = None
        self.hello_n = 0                    # numéro du dernier hello envoyé (« hid », renvoyé par le serveur)
        self.applied: dict[tuple, float] = {}   # bit posé par A1 -> heure
        self.fresh: dict[tuple, float] = {}     # bit passé à 1 localement -> heure
        self.used: set[str] = set()             # ids déjà déclarés dans ce contexte
        self.door_anim: dict[int, float] = {}   # switch -> dernière heure vue m20 = 1
        self.anim_now = False
        self.pt2 = None
        self.pt2_t = float("-inf")
        self.ctx = None
        self.ctx_t = float("-inf")
        self.base_e: int | None = None
        self.e_last: int | None = None
        self.e_t = float("-inf")
        self.prev: frozenset | None = None
        self.prev_rooms: set = set()
        self.last_busy = float("-inf")
        self.flight: dict | None = None
        self.retry_at = float("-inf")
        self.fails = 0
        self.notes: list[str] = []
        self.status_text = "en attente du jeu"
        self.view: KeyView | None = None
        self.n_get = self.n_use = self.n_dup = self.n_writes = 0

    # --- réseau -------------------------------------------------------------------
    def on_connect(self) -> None:
        self.synced = False
        self.sent.clear()
        self.tries.clear()
        self.need_hello = True

    def on_disconnect(self) -> None:
        self.on_connect()

    def take_out(self) -> list[dict]:
        """Messages à envoyer maintenant (connecté) : hello d'abord, puis les faits non accusés
        pas encore envoyés pendant cette connexion (renvoi après reconnexion : sans danger,
        le serveur est idempotent)."""
        if not self.enabled:
            return []
        out = []
        if self.need_hello:
            if self.hello_vals is None:
                return []                    # jeu pas encore lu : rien (même pas les faits)
            vals = dict(self.hello_vals)
            for f in self.facts.values():   # valeurs SANS nos faits non comptés (sinon double)
                if f["slot"] in vals:
                    vals[f["slot"]] -= 1 if f["type"] == "key_get" else -1
            self.hello_n += 1
            out.append({"type": "keys", "group": self.group, "hello": 1, "hid": self.hello_n,
                        "slots": {str(s): max(0, min(KEY_MAX, v)) for s, v in sorted(vals.items())}})
            self.need_hello = False
        now = self.clock()
        for seq, f in list(self.facts.items()):
            t = self.sent.get(seq)
            if t is not None and now - t > self.ACK_TIMEOUT and self.synced:
                # serveur joignable mais pas d'accusé (débit dépassé…) : renvoi, puis abandon
                if self.tries.get(seq, 0) >= self.MAX_TRIES:
                    del self.facts[seq]
                    self.sent.pop(seq, None)
                    self.tries.pop(seq, None)
                    self._note(f"clés : {f['id']} jamais accusé par le serveur : abandonné")
                    continue
                t = None
            if t is None:
                self.sent[seq] = now
                self.tries[seq] = self.tries.get(seq, 0) + 1
                out.append(f)
        return out

    def receive(self, msg: dict, me=None) -> None:
        if not self.enabled or msg.get("group") != self.group:
            return
        if msg.get("reset") in (1, True) and msg.get("from") is None:
            self.targets.clear()
            self.synced = False
            self.need_hello = True
            self._note("clés : compteurs du serveur remis à zéro (rejoindre) : nouvelles valeurs envoyées")
            return
        if msg.get("full") in (1, True):
            slots = msg.get("slots")
            for s, tr in (slots.items() if isinstance(slots, dict) else []):
                try:
                    slot = int(s)
                except (TypeError, ValueError):
                    continue
                if valid_key_slot(slot) and isinstance(tr, list) and len(tr) == 2 \
                        and all(isinstance(x, int) and not isinstance(x, bool) for x in tr):
                    self.targets[slot] = (max(0, min(KEY_MAX, tr[0])), tr[1])
            if not self.active:
                self._note("clés : compteur commun actif (serveur compatible)")
            self.active = True
            # Synchronisé seulement par la réponse à NOTRE dernier hello : une table diffusée pour
            # le hello d'un autre (après un « keys reset », A15) ne contient pas encore nos valeurs ;
            # converger vers elle ferait annoncer ensuite une valeur déjà abaissée (clé perdue).
            if msg.get("hid") == self.hello_n and not self.need_hello:
                self.synced = True
            return
        slot, target, rev = msg.get("slot"), msg.get("target"), msg.get("rev")
        if not (valid_key_slot(slot) and isinstance(target, int) and isinstance(rev, int)):
            return
        if rev >= self.targets.get(slot, (0, -1))[1]:
            self.targets[slot] = (max(0, min(KEY_MAX, target)), rev)
        seq = msg.get("seq")
        mine = isinstance(seq, int) and seq in self.facts and self.facts[seq].get("id") == msg.get("id")
        if mine:
            del self.facts[seq]
            self.sent.pop(seq, None)
            self.tries.pop(seq, None)
        why, by, kid = msg.get("why"), str(msg.get("by") or "?")[:24], msg.get("id")
        what = _describe_id(kid)
        if why == "dup_get" and mine:
            self.n_dup += 1
            self._note(f"clé déjà comptée ({what}, prise par {by}) : ton compteur revient à {target}")
        elif why == "dup_use" and mine:
            self.n_dup += 1
            self._note(f"porte déjà payée par {by} ({what}) : clé rendue, {target} clé(s)")
        elif why == "get" and not mine:
            self._note(f"clé obtenue par {by} ({what}) : {target} clé(s)")
        elif why == "use" and not mine:
            self._note(f"{by} a ouvert une porte ({what}) : {target} clé(s)")

    # --- entrées locales -------------------------------------------------------------
    def note_applied(self, bits: dict, cur_idx, now: float | None = None) -> None:
        """Bits posés par la progression partagée (A1) : jamais pris pour des bits locaux (R1)."""
        if cur_idx is None:
            return
        now = self.clock() if now is None else now
        for off, m in (bits.get(f"mem{cur_idx}") or {}).items():
            for j in range(8):
                if not m >> j & 1:
                    continue
                if MEM_TBOX <= off < MEM_TBOX + 4:
                    self.applied[("t", (3 - (off - MEM_TBOX)) * 8 + j)] = now
                elif MEM_ITEM <= off < MEM_ITEM + 4:
                    self.applied[("i", (3 - (off - MEM_ITEM)) * 8 + j)] = now
                elif 0x04 <= off < 0x14:
                    self.applied[("s", ((off - 0x04) // 4) * 32 + (3 - (off - 0x04) % 4) * 8 + j)] = now
        for key, offs in bits.items():
            if key.startswith("zone:"):
                _z, _st, room = key.split(":")
                for off, m in offs.items():
                    if off in (0x06, 0x07):
                        for j in range(8):
                            if m >> j & 1:
                                self.applied[("z", int(room), (1 - (off - 0x06)) * 8 + j)] = now

    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-20:]

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def _unacked(self, slot: int) -> int:
        # copie : receive() / take_out() (fil asyncio) retirent des faits pendant tick() (fil de travail)
        return sum(1 for f in list(self.facts.values()) if f["slot"] == slot)

    def _fact(self, kind: str, slot: int, kid: str, now: float) -> None:
        seq = next(self.seq)
        self.facts[seq] = {"type": kind, "group": self.group, "slot": slot, "id": kid, "seq": seq}
        while len(self.facts) > self.MAX_FACTS:
            self.facts.pop(next(iter(self.facts)))
        self.used.add(kid)
        if kind == "key_get":
            self.n_get += 1
            self._note(f"clé obtenue ({_describe_id(kid)}) : envoyée au groupe")
        else:
            self.n_use += 1
            self._note(f"clé utilisée ({_describe_id(kid)}) : envoyée au groupe")

    def _anon(self) -> str:
        return f"a{self.pnum}:{next(self.seq)}"

    def _take_fresh(self, kind: str, window: float, now: float, ok=lambda k: True) -> str | None:
        cands = sorted(((t, k) for k, t in self.fresh.items() if k[0] == kind and now - t <= window and ok(k)),
                       reverse=True)
        for _t, k in cands:
            kid = f"{k[0]}{k[1]}" if kind != "z" else f"z{k[1]}:{k[2]}"
            if kid in self.used or not valid_key_id(kid):
                continue
            del self.fresh[k]
            return kid
        return None

    def _get_id(self, v: KeyView, mem, tboxes, now: float) -> str:
        if v.get_item == ITEM_SMALL_KEY:
            for kind, win in (("t", self.TBOX_WINDOW), ("i", self.ITEM_WINDOW), ("z", self.ITEM_WINDOW)):
                kid = self._take_fresh(kind, win, now)
                if kid:
                    return kid
            # coffre au bit déjà posé (venu d'ailleurs) : coffre de l'événement (mPt2, §3.3)
            if now - self.pt2_t <= self.TBOX_WINDOW and v.pt2 not in (0, 0xFFFFFFFF) and mem is not None:
                n = tbox_number(mem, tboxes, v.pt2)
                if n is not None and f"t{n}" not in self.used:
                    return f"t{n}"
        return self._anon()

    def _use_id(self, now: float) -> str:
        kid = self._take_fresh("s", self.USE_WINDOW, now, lambda k: k[1] < 0x80)
        if kid:
            return kid
        for sw, t in sorted(self.door_anim.items(), key=lambda x: -x[1]):
            kid = f"s{sw}"
            if now - t <= self.USE_WINDOW and sw < 0x80 and kid not in self.used:
                return kid
        return self._anon()

    def _new_bits(self, v: KeyView, now: float) -> None:
        cur = (frozenset(("t", n) for n in v.tbox) | frozenset(("i", n) for n in v.items)
               | frozenset(("s", n) for n in v.switches) | frozenset(("z", r, n) for r, n in v.zitems))
        if self.prev is not None:
            for k in cur - self.prev:
                if now - self.applied.get(k, float("-inf")) <= self.APPLIED_WINDOW:
                    continue                    # posé par A1 : pas un bit local (R1)
                if k[0] == "z" and k[1] not in self.prev_rooms:
                    continue                    # zone qui vient d'être chargée : bits anciens
                self.fresh[k] = now
        self.prev = cur
        self.prev_rooms = set(v.zone_rooms)
        for d in (self.fresh, self.applied):
            for k in [k for k, t in d.items() if now - t > self.TBOX_WINDOW + self.APPLIED_WINDOW]:
                del d[k]

    # --- tick ---------------------------------------------------------------------
    def tick(self, mem, doors=None, tboxes=None, reloads: int = 0, can_write: bool = True,
             connected: bool = True, now: float | None = None) -> None:
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        try:
            v = read_keys(mem)
        except Exception as e:                  # mémoire illisible pendant un chargement
            v = KeyView(False, f"lecture impossible : {e}")
        self.view = v
        if not v.ok:
            self.ctx, self.base_e, self.prev = None, None, None
            self.status_text = v.reason
            return
        if v.busy:
            self.last_busy = now
        in_event = v.busy or now - self.last_busy < self.EVENT_TAIL
        if v.next_stage:
            # changement de stage : recopies getSave/putSave en cours, rien ne veut rien dire.
            # R8 : notre pending pas encore appliqué est annulé AVANT la stage suivante (le jeu ne
            # le remet pas à 0 en HD, M9 : il s'appliquerait au mauvais donjon)
            fl = self.flight
            if fl is not None:
                if can_write and self._cancel_pending(mem, fl["d"]):
                    self._note("clés : correction non appliquée avant le changement de stage : annulée")
                self.flight = None
            self.base_e, self.prev, self.ctx_t = None, None, now
            self.status_text = "changement de stage en cours"
            return
        ctx = (v.gi, v.stage, v.slot, reloads)
        if ctx != self.ctx:
            self._new_context(mem, v, ctx, now, can_write)
        # hello : valeurs effectives de chaque donjon (vivante pour la stage chargée)
        vals = dict(v.saved)
        if v.keyed:
            vals[v.slot] = v.effective
        self.hello_vals = vals
        if not v.keyed:
            self.status_text = "hors donjon"
            self.flight = None
            self._converge_other(mem, v, now, can_write, connected)
            return
        # --- détection (jamais suspendue) ---
        self._new_bits(v, now)
        self.anim_now = False
        for sw, m20 in read_door_anims(mem, doors):
            if m20:
                self.door_anim[sw] = now
                self.anim_now = True
        for sw in [s for s, t in self.door_anim.items() if now - t > self.USE_WINDOW]:
            del self.door_anim[sw]
        if v.pt2 != self.pt2:
            self.pt2, self.pt2_t = v.pt2, now
        e = v.effective
        if e != self.e_last:
            self.e_last, self.e_t = e, now
        if self.base_e is None:
            self.base_e = e
        delta = e - self.base_e
        if delta:
            if in_event:
                for _ in range(max(0, delta)):
                    self._fact("key_get", v.slot, self._get_id(v, mem, tboxes, now), now)
                for _ in range(max(0, -delta)):
                    self._fact("key_use", v.slot, self._use_id(now), now)
            else:
                self._note(f"clés : compteur {self.base_e} -> {e} hors événement (mort, rechargement ?) : "
                           "pas de fait, retour à la valeur commune")
            self.base_e = e
        self._converge_live(mem, v, now, in_event, can_write, connected)
        self._converge_other(mem, v, now, can_write, connected)

    @staticmethod
    def _cancel_pending(mem, d: int) -> bool:
        """Remet pending à 0 seulement s'il vaut encore ``d`` (notre écriture), relu juste avant."""
        raw = mem.try_read(ADDR_KEY_PENDING, 2)
        if raw is None or struct.unpack(">h", raw)[0] != d:
            return False
        mem.write(ADDR_KEY_PENDING, struct.pack(">h", 0))
        return True

    def _new_context(self, mem, v: KeyView, ctx, now: float, can_write: bool = True) -> None:
        old = self.ctx
        fl = self.flight
        if fl is not None and v.pending == fl["d"] and fl["slot"] != v.slot and can_write:
            # R8 : notre pending n'a pas été appliqué avant le changement de stage : jamais dans
            # un autre donjon (relu juste avant)
            if self._cancel_pending(mem, fl["d"]):
                v.pending = 0
                self._note("clés : correction non appliquée avant le changement de stage : annulée")
        self.flight = None
        self.ctx, self.ctx_t = ctx, now
        self.base_e = v.effective if v.keyed else None
        self.e_last, self.e_t = (v.effective, now)
        self.prev = None
        self.fresh.clear()
        self.used.clear()
        self.door_anim.clear()
        self.pt2, self.pt2_t = v.pt2, float("-inf")
        if old is None or old[0] != ctx[0] or old[3] != ctx[3]:
            self.need_hello = True           # sauvegarde (re)chargée : nouvelles valeurs
        self.retry_at = float("-inf")

    # --- convergence --------------------------------------------------------------
    def _converge_live(self, mem, v: KeyView, now: float, in_event: bool, can_write: bool, connected: bool) -> None:
        slot = v.slot
        tgt = self.targets.get(slot)
        fl = self.flight
        if fl is not None:
            if v.pending == 0 and v.keynum == fl["expected"]:
                self.flight, self.fails = None, 0
                self._note(f"clés : compteur commun appliqué ({fl['expected']})")
            elif v.pending not in (0, fl["d"]):
                self.flight = None               # le jeu a ajouté par-dessus : déjà vu comme fait
            elif v.pending == 0:
                self.flight = None               # appliqué, et autre chose a changé (fait vu)
            elif now >= fl["deadline"] and self._event_now(mem, v):
                # événement commencé pendant le vol (porte, coffre, cinématique) : le jeu peut écrire
                # pending lui-même (keyInit, item_func_small_key) : aucune écriture maintenant (R20) ;
                # le jeu applique le reste à la fin, sinon remise à 0 après l'événement ou au
                # changement de stage (R8)
                pass
            elif now >= fl["deadline"]:
                # R8 : pas appliqué (pause, menu…) : remis à 0, seulement s'il vaut encore d
                if can_write and self._cancel_pending(mem, fl["d"]):
                    self.base_e -= fl["d"]
                    self.e_last, self.e_t = self.base_e, now
                self.flight = None
                self.fails += 1
                self.retry_at = now + self.RETRY[min(self.fails - 1, len(self.RETRY) - 1)]
                self._note("clés : correction pas appliquée par le jeu (pause ?) : annulée, nouvel essai")
            self._status(v, tgt)
            return
        self._status(v, tgt)
        if tgt is None or not (self.synced and connected and can_write):
            return
        e = v.effective
        d = max(-PENDING_MAX, min(PENDING_MAX, max(0, min(KEY_MAX, tgt[0])) - e))
        if d == 0:
            self.fails = 0
            return
        if (in_event or now < self.retry_at or now - self.ctx_t < self.STABLE or now - self.e_t < self.STABLE
                or v.pending != 0 or v.next_stage or self.anim_now or self._unacked(slot)):
            return
        if not self._recheck(mem, v):
            return
        ok = None
        if self.key_add is not None:            # pack v19 : ajout atomique exécuté par le jeu
            try:
                ok = bool(self.key_add(d, slot))
            except Exception:
                ok = None                       # commande indisponible : écriture Python
            if ok is False:
                return
        if ok is None:
            mem.write(ADDR_KEY_PENDING, struct.pack(">h", d))
        self.n_writes += 1
        self.base_e += d                         # E inclut pending : notre écriture n'est pas un fait
        self.e_last, self.e_t = self.base_e, now
        self.flight = {"slot": slot, "d": d, "expected": max(0, min(KEY_MAX, v.keynum + d)),
                       "deadline": now + self.FLIGHT}

    @staticmethod
    def _event_now(mem, v: KeyView) -> bool:
        """Événement en cours (lecture du tick, puis relue juste avant une écriture)."""
        if v.busy:
            return True
        ev = prog.read_event(mem)
        return ev is None or ev.busy

    @staticmethod
    def _recheck(mem, v: KeyView) -> bool:
        """Gardes relues juste avant l'écriture : pending, événement, changement de stage, slot."""
        raw = mem.try_read(ADDR_KEY_PENDING, 2)
        ev = prog.read_event(mem)
        nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
        dan = mem.try_read(v.gi + prog.GI_DAN + prog.DAN_STAGE_NO, 1)
        return (raw is not None and struct.unpack(">h", raw)[0] == 0 and ev is not None and not ev.busy
                and nxt is not None and nxt[0] == 0 and dan is not None
                and struct.unpack(">b", dan)[0] == v.slot)

    def _converge_other(self, mem, v: KeyView, now: float, can_write: bool, connected: bool) -> None:
        """Donjons où le joueur n'est pas : copie sauvegardée, lue par le jeu en y entrant."""
        if not (self.synced and connected and can_write) or now - self.ctx_t < self.OTHER_SETTLE or v.next_stage:
            return
        for slot, (tgt, _rev) in sorted(self.targets.items()):
            if slot == v.slot or slot not in KEY_SLOTS or self._unacked(slot):
                continue
            want = max(0, min(KEY_MAX, tgt))
            if v.saved.get(slot) == want:
                continue
            va = v.gi + prog.GI_MEMORY + slot * prog.MEM_SIZE + MEM_KEYNUM
            nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
            dan = mem.try_read(v.gi + prog.GI_DAN + prog.DAN_STAGE_NO, 1)
            cur = struct.unpack(">b", dan)[0] if dan is not None else None
            if nxt is None or nxt[0] or cur is None or (cur if 0 <= cur < prog.N_MEM else None) != v.slot:
                return                               # entrée dans un donjon : tick suivant (R9)
            mem.write(va, bytes([want]))
            self.n_writes += 1
            v.saved[slot] = want
            self._note(f"clés : donjon {slot} (pas chargé) mis à {want}")

    def _status(self, v: KeyView, tgt) -> None:
        srv = f"serveur {tgt[0]}, rev {tgt[1]}" if tgt else ("serveur : pas encore" if self.active
                                                             else "serveur sans partage des clés ?")
        self.status_text = (f"{v.stage} (donjon {v.slot}) {v.effective} ({srv}) ; "
                            f"faits en attente : {len(self.facts)}"
                            + (" ; correction en cours" if self.flight else ""))

    def status(self) -> str:
        return (f"{self.status_text} ; {self.n_get} obtenue(s), {self.n_use} utilisée(s), "
                f"{self.n_dup} doublon(s), {self.n_writes} écriture(s)")


def _describe_id(kid) -> str:
    if not isinstance(kid, str) or not kid:
        return "?"
    if kid[0] == "t":
        return f"coffre {kid[1:]}"
    if kid[0] == "r":
        # randomizer, portage TWWHD lot 5 : clé placée hors de son donjon, créditée par le serveur
        return f"coffre du randomizer {kid[1:]}, hors de son donjon"
    if kid[0] == "i":
        return f"clé au sol {kid[1:]}"
    if kid[0] == "z":
        return f"clé au sol {kid[1:]}"
    if kid[0] == "s":
        try:
            return f"porte 0x{int(kid[1:]):02X}"
        except ValueError:
            return kid
    if kid[0] == "p":
        return "jarre"
    return "sans repère"
