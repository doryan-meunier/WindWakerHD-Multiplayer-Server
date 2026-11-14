"""Objets au sol (SYNCHRO.md A4, A5, A6) : logique pure, testable sans Cemu.

A4 — drops (objets apparus), version 1 « butin par joueur »
  Le PC où un objet (daItem_c, proc 255) NAÎT le publie : ennemi tué chez l'autorité,
  jarre cassée, herbe coupée, Iball (seuls les objets qu'il libère sont publiés). Les autres
  joueurs de la zone en créent une copie (commande 4 de la boîte aux lettres, mêmes
  paramètres que fopAcM_createItem). Chacun ramasse la sienne : les messages « ramassé »
  (version 2, « premier arrivé ») ne sont pas faits.
  Jamais publiés :
    - les objets vus au chargement de la salle (objets placés) ;
    - les objets créés par NOTRE commande 4 (copies reçues) : pas d'écho ;
    - les objets nés pendant un événement (A0 : coffre, cinématique, objet donné par un
      script ; critique §12), avec une action de boss (5, 12) ou en démo d'obtention ;
    - les objets dont l'archive n'est pas sûrement chargée chez l'autre (critique §19) :
      seulement les consommables communs (cœurs, rubis, magie, bombes, flèches) ; ni petite
      clé (A8), ni quart / réceptacle de cœur, ni fée (proc 0x168), ni butin d'ennemi ;
    - les objets d'une autre salle que la nôtre.
A5 — objet unique pris par l'autre joueur (bit mem:item / zone:item reçu et posé par la
  progression partagée) : la copie encore affichée chez nous est supprimée.
A6 — jarres et pierres (proc 453 kotubo/ootubo/Kmtub…, 454 koisi) : une jarre qui disparaît
  chez nous (cassée), hors chargement, est annoncée dans la zone de sa salle d'origine (de notre
  salle, ou d'une autre si Link l'a portée ou lancée dans les 3 s) ; le serveur relaie à tout le
  stage. Chez les autres, la même jarre (clé proc/params/origine) est cassée PAR LE JEU, avec
  ses éclats et son son (--pot-fx casse, défaut : break_pot() vide son contenu, itemNo = 0x3F,
  puis arme son minuteur de casse m800 = 1 ; voir docs/etudes/jarres_casse.md). Repli si elle
  est encore là 1 s après (ou petite pierre proc 454, sans minuteur) : suppression (commande 5)
  et petit nuage. --pot-fx fumee : toujours suppression + nuage ; rien : suppression seule. Le
  receveur ne lâche jamais d'objet : le contenu arrive par A4. La cruche d'eau (type 2) est
  exclue : la supprimer ne refroidirait pas le magma (désynchronisation connue, C7).
  Jamais relayée non plus : une jarre dont le contenu n'est pas un consommable commun (petite
  clé, quart de cœur, fée… : A4 ne le copierait pas, l'autre joueur le perdrait) ; chacun casse
  alors la sienne (désynchronisation connue). Chez le receveur, jamais touchée : une jarre
  portée par Link ou en mouvement (lancée), ni pendant un événement (A0) : elle attend (15 s).
  Journal : chaque casse non relayée (raison), chaque jarre retirée, introuvable (raison) ou
  refusée par le jeu. Après une coupure, les casses des 30 dernières secondes sont renvoyées.

Synchro v2, module 4 (docs/etudes/sync2_cruches_drops.md, la section « Vérification » prime) :
  Cruches d'eau (--share-jugs, activé par --coop-donjon, --no-jugs) : la cruche cassée chez nous
    est relayée avec ``jug`` et ``at`` (point d'impact : la dalle de magma née chez nous dans les
    0,6 s à moins de 250 u, sinon la dernière position vue). Chez les autres, la même cruche est
    déplacée à ``at`` puis cassée PAR LE JEU (break_jug_at : m800 = 1) : éclaboussure, son, eau qui
    éteint le feu et, au-dessus de la lave, sa propre dalle au même endroit. Une dalle « à nous »
    (commande 4) à moins de 110 u : la cruche est retirée sans effet (sinon deuxième dalle à côté).
  Magma figé (--share-magma, activé par --coop-donjon, --no-magma) : l'acteur Magrock (proc 42,
    params 0 : cruche ou flèche de glace) né chez nous est publié (« magma ») ; chez les autres, une
    dalle locale récente (< 3 s) à moins de 110 u est associée, sinon (après 1 s d'attente d'une
    cruche relayée) la dalle est créée par la commande 4, seulement si le paquet de magma de la
    stage existe (u32 [0x10474B64] != 0). --magma-recale : recalage du minuteur (+0x750) [HYPO].
  Fées libres (--share-fairies, activé par --coop-donjon, --no-fairies) : fée proc 0x168 de type 1
    (TIMER, params 1 : lâchée par une jarre, de l'herbe, un ennemi) née chez nous : entrée « drop »
    kind « fee » ; copie chez les autres par la commande 4 (archive Always). Butin par joueur :
    chacun soigne ou capture la sienne. Jamais une fée de flacon (types 2, 5) ni placée (0, 3, 4, 6).
  Consommables nés pendant un événement (--share-event-drops, activé par --coop-donjon,
    --no-event-drops) : mis en file au lieu d'être ignorés, publiés à la fin de l'événement s'ils
    existent encore (jamais un objet à switch, de boss, une clé, ni en cours de ramassage).
"""
from __future__ import annotations

import math
import struct
import threading

# --- daItem_c (proc 255) sur HD [LIVE + ASM HD, voir docs/archives/synchro_drops.md §2] --------
PROC_ITEM = 255
ITEM_FIELDS_AT, ITEM_FIELDS_SIZE = 0x740, 0x48     # une seule lecture : +0x740..+0x787
ITEM_BIT_NO = 0x744        # s32 mItemBitNo (critique §3 : mot de 32 bits, octet utile +0x747)
ITEM_NO = 0x74E            # u8 m_itemNo
ITEM_TYPE = 0x781          # u8 mType   : `rlwinm r12,r0,8,30,31 ; stb r12,0x781` [ASM HD 0x0218026C]
ITEM_ACTION = 0x782        # u8 mAction : `rlwinm r8,r0,6,26,31 ; stb r8,0x782`  [ASM HD 0x021802A0]
ITEM_STATUS = 0x785        # u8 mItemStatus (5 = ramassé, 7-9 démo d'obtention, 0xA-0xB boss)
BIT_NONE = 0x7F            # pas de bit d'objet unique (masqué & 0x7F à la création [DECOMP])
MEMORY_ITEM, ZONE_ITEM = 0x40, 0x10    # dSv_info_c::isItem : < 0x40 stage, 0x40-0x4F zone [DECOMP]

# Consommables communs, archive toujours chargée [HYPO raisonnable : déjà créés par les tables
# d'objets de tous les ennemis] : cœur, rubis vert à orange, petite et grande magie, bombes,
# flèches, triple cœur (chaque cœur est un acteur : copié un pour un).
DROP_ITEMS = frozenset([0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x09, 0x0A,
                        0x0B, 0x0C, 0x0D, 0x0E, 0x10, 0x11, 0x12, 0x1E])
BOSS_ACTIONS = frozenset([5, 12])                  # daItemAct_BOSS_DISAPPEAR_e, daItemAct_BOSS_e
NO_PUBLISH_STATUS = frozenset([7, 8, 9, 0xA, 0xB])  # démo d'obtention, attente de boss

# --- jarres (daTsubo, proc 453) et petites pierres (proc 454) [LIVE noms ; DECOMP d_a_tsubo] --
POT_PROCS = (453, 454)
POT_TYPE_SHIFT, WATER_JUG = 24, 2                  # prm type (4 bits @24) ; TYPE_WATER_JUG [DECOMP]
# Petit nuage (repli A6, A5) : daDisappear (proc 400), type 1 = fumée + éclair SANS objet ; c'est
# une fumée de mort d'ennemi (tous les types le sont), d'où la casse par le jeu en A6. Le type 4 du plan
# (petite fumée « puchi ») appelle fopAcM_createIball 58 images plus tard [DECOMP
# d_a_disappear.cpp] : objet aléatoire chez le receveur, qu'A4 republierait. Écarté.
PUFF_PROC, PUFF_TYPE, PUFF_SCALE = 400, 1, 5
# Contenu d'une jarre [DECOMP d_a_tsubo.h Prm_e, d_a_tsubo.cpp damaged()] : itemNo (6 bits @0)
# passé à fopAcM_createItemFromTable : < 0x20 objet précis, 0x20-0x3E table d'objets aléatoires
# (consommables), 0x3F rien. itemSave (7 bits @16) n'est PAS regardé : en direct, les jarres de
# la Caverne du Dragon ont des bits de zone 0x40-0x44 avec un simple cœur ou rubis
# (params 7043FF00, 7044FFC1 [LIVE]). Pierres (proc 454) : même disposition supposée [HYPO].
POT_ITEM_MASK, POT_ITEM_TABLE = 0x3F, 0x20

# --- synchro v2, module 4 : cruches, magma figé, fées [HD-ASM, sync2_cruches_drops.md §2] --------
ITEM_FAIRY = 0x16                  # RECOVER_FAIRY : fopAcM_createItem -> fopAcM_create(0x168, 1, …)
PROC_FAIRY = 0x168                 # Sfairy (daNpc_Fa1_c) [LIVE table des noms ; HD-ASM 0x025D8970]
FAIRY_PARAMS = 1                   # Type_TIMER_e : lâchée, s'enfuit seule [DECOMP d_a_npc_fa1.h]
FAIRY_TIMER_TYPE = 1
FAIRY_MODE, FAIRY_TYPE = 0x8AC, 0x8B0     # u8 mMode, u8 mType (GC + 0x11C) [HD-ASM 0x02233128-…]
PROC_MAGROCK = 42                  # Obj_Magmarock « Magrock » [LIVE ; HD-ASM 0x025D3D44 li r3,0x2A]
MAGROCK_PROC_FN = 0x3FC            # u32 fonction de proc courante (PMF +0x3F8)
MAGROCK_TIMER = 0x750              # s32 minuteur (décrémenté à chaque image)
MAGROCK_AGE = 0x754                # s32 âge en images (+1 par image)
MAGROCK_WAIT_FN = 0x0236E800       # proc « attente » (300 images, solide)
MAGROCK_LIFE = 465                 # images de vie d'une dalle de cruche (30 + 300 + 45 + 90)
MAGROCK_APPEAR = 30                # images d'apparition (avant l'attente)
MAGROCK_WAIT_TIMER = 300
MAGMA_PACKET = 0x10474B64          # u32 dComIfGp+0x5AB4 (mpMagmaPacket [HYPO]) : 0 = pas de magma,
                                   # le Create du Magrock resterait bloqué (cPhs_INIT) [HD-ASM 0x0236E9C4]
TSUBO_TYPE = 0x790                 # s32 mType de daTsubo (2 = cruche d'eau) [HD-ASM 0x024CC154]
POS_OLD, POS_CUR, SPEED = 0x300, 0x314, 0x33C    # old.pos, current.pos, speed (3 × f32) [HD-ASM]


def puff_params(scale: int = PUFF_SCALE) -> int:
    return (0xFF << 16) | ((scale & 0xFF) << 8) | PUFF_TYPE


# Effets du mode Hanteurs (invocation, possession, relâche, potion, échange) : la même fumée, en deux types SEULEMENT.
# 1 = fumée + éclair ; 3 = fumée + volutes + éclair. Aucun des deux ne lâche d'objet [DECOMP d_a_disappear.cpp
# daDisItem_NONE1_e / NONE3_e ; HD-ASM execute 0x0212451C : « cmpwi r11,1 / beq » et « cmpwi r11,3 / beq » vers
# la sortie]. Tous les autres types créent un objet (cœur, rubis, table aléatoire) 58 images plus tard : triche
# et désynchro, donc refusés en dur ici, quel que soit l'appelant.
EFFET_TYPES = (1, 3)
EFFET_ECHELLE_MAX = 20              # × 0,1 dans le jeu : au plus 2 fois la taille de base


def effet_params(type_: int = PUFF_TYPE, scale: int = PUFF_SCALE) -> int:
    """Paramètres de la fumée d'effet. ``ValueError`` pour tout type hors de ``EFFET_TYPES`` (jamais créé) ;
    l'échelle est bornée à 1..EFFET_ECHELLE_MAX."""
    if isinstance(type_, bool) or not isinstance(type_, int) or type_ not in EFFET_TYPES:
        raise ValueError(f"fumée : type {type_!r} refusé (permis : {EFFET_TYPES})")
    if isinstance(scale, bool) or not isinstance(scale, int):
        scale = PUFF_SCALE
    scale = max(1, min(EFFET_ECHELLE_MAX, scale))
    return (0xFF << 16) | (scale << 8) | type_


def item_params(no: int, bit: int, type_: int, action: int) -> int:
    """Paramètres d'un daItem, comme MAKE_ITEM_PARAMS de fopAcM_createItem [DECOMP + LIVE
    0x025D8870] : itemNo | bit << 8 | 0xFF << 16 (pas de switch) | type << 24 | action << 26."""
    return ((no & 0xFF) | ((bit & 0xFF) << 8) | (0xFF << 16) | ((type_ & 0x3) << 24)
            | ((action & 0x3F) << 26)) & 0xFFFFFFFF


def parse_item_fields(raw: bytes) -> dict:
    """Champs utiles d'un daItem lus en un bloc à base + ITEM_FIELDS_AT."""
    o = ITEM_FIELDS_AT
    return {"bit": struct.unpack_from(">i", raw, ITEM_BIT_NO - o)[0], "no": raw[ITEM_NO - o],
            "type": raw[ITEM_TYPE - o], "act": raw[ITEM_ACTION - o], "status": raw[ITEM_STATUS - o]}


def is_water_jug(proc: int, params: int) -> bool:
    return proc == 453 and (params >> POT_TYPE_SHIFT) & 0xF == WATER_JUG


def pot_relayable(proc: int, params: int, cruches: bool = False, fees: bool = False) -> bool:
    """Une jarre cassée peut-elle être supprimée chez les autres ? La cruche d'eau seulement avec
    --share-jugs (``cruches`` : cassée à l'impact chez l'autre) ; une jarre à fée seulement avec
    --share-fairies (``fees`` : la fée arrive par A4) ; jamais une jarre qui contient autre chose
    qu'un consommable commun ou une table d'objets (clé, quart de cœur…)."""
    if is_water_jug(proc, params):
        return cruches
    no = params & POT_ITEM_MASK
    return no >= POT_ITEM_TABLE or no in DROP_ITEMS or (fees and no == ITEM_FAIRY)


def publishable(it: dict) -> bool:
    """Un objet apparu peut-il être copié chez les autres ? (voir l'en-tête du module)"""
    params = int(it.get("params", 0))
    return (it.get("no") in DROP_ITEMS and it.get("act") not in BOSS_ACTIONS
            and it.get("status") not in NO_PUBLISH_STATUS
            and (params >> 16) & 0xFF == 0xFF)       # créé par fopAcM_(fast)createItem : pas de switch


def fairy_publishable(f: dict) -> bool:
    """Fée née chez nous copiable chez les autres : type TIMER (1) et params 1 seulement (jamais une
    fée de flacon, types 2 et 5 : elle tourne autour du Link LOCAL ; ni placée ou de fontaine)."""
    return (f.get("proc", PROC_FAIRY) == PROC_FAIRY and f.get("type") == FAIRY_TIMER_TYPE
            and int(f.get("params", -1)) == FAIRY_PARAMS)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def valid_drop(e) -> dict | None:
    """Entrée « drop » reçue du réseau, validée (None si invalide)."""
    if not isinstance(e, dict):
        return None
    if e.get("kind") == "fee":
        return _valid_fairy(e)
    nid, no, bit, typ, act, room, rot = (e.get(k) for k in ("nid", "no", "bit", "type", "act", "room", "rotY"))
    pos = e.get("pos")
    if not (all(_int(x) for x in (nid, no, bit, typ, act, room)) and isinstance(pos, list) and len(pos) == 3
            and all(_num(v) and abs(v) < 1e6 for v in pos)):
        return None
    if no not in DROP_ITEMS or act in BOSS_ACTIONS or not 0 <= typ <= 3 or not 0 <= act < 64 \
            or not 0 <= bit <= BIT_NONE or not 0 <= room < 64 or not 0 <= nid < 1 << 32:
        return None
    return {"nid": nid, "no": no, "bit": bit, "type": typ, "act": act, "room": room,
            "pos": [float(v) for v in pos], "rotY": int(rot) if _int(rot) and -32768 <= rot < 32768 else 0}


def _pos3(pos) -> bool:
    return isinstance(pos, list) and len(pos) == 3 and all(_num(v) and abs(v) < 1e6 for v in pos)


def _valid_fairy(e: dict) -> dict | None:
    """Entrée « drop » kind « fee » (synchro v2) : fée libre lâchée chez l'émetteur."""
    nid, room, rot, no = e.get("nid"), e.get("room"), e.get("rotY"), e.get("no", ITEM_FAIRY)
    if not (_int(nid) and _int(room) and _pos3(e.get("pos")) and no == ITEM_FAIRY):
        return None
    if not 0 <= room < 64 or not 0 <= nid < 1 << 32:
        return None
    return {"nid": nid, "kind": "fee", "no": ITEM_FAIRY, "bit": BIT_NONE, "type": 0, "act": 0, "room": room,
            "pos": [float(v) for v in e["pos"]], "rotY": int(rot) if _int(rot) and -32768 <= rot < 32768 else 0}


def valid_magma(e) -> dict | None:
    """Entrée « magma » (synchro v2) : {nid, pos[3], room, age (images)}."""
    if not isinstance(e, dict):
        return None
    nid, room, age = e.get("nid"), e.get("room"), e.get("age", 0)
    if not (_int(nid) and _int(room) and _int(age) and _pos3(e.get("pos"))):
        return None
    if not 0 <= nid < 1 << 32 or not 0 <= room < 64 or not 0 <= age < 1 << 20:
        return None
    return {"nid": nid, "pos": [float(v) for v in e["pos"]], "room": room, "age": age}


def valid_break(e) -> dict | None:
    if not isinstance(e, dict):
        return None
    key, pos = e.get("key"), e.get("pos")
    if not (isinstance(key, str) and 0 < len(key) <= 64 and isinstance(pos, list) and len(pos) == 3
            and all(_num(v) and abs(v) < 1e6 for v in pos)):
        return None
    try:
        proc = int(key.split("/", 1)[0])
    except ValueError:
        return None
    if proc not in POT_PROCS:
        return None
    out = {"key": key, "pos": [float(v) for v in pos]}
    if e.get("keep") is True:
        out["keep"] = True    # A6 : le receveur laisse l'objet de sa jarre (pas de copie A4 attendue)
    if e.get("jug") is True and proc == 453:
        out["jug"] = True     # synchro v2 : cruche d'eau, cassée à l'impact « at » chez le receveur
        at = e.get("at")
        out["at"] = [float(v) for v in at] if _pos3(at) else list(out["pos"])
    return out


class _Bounded(set):
    """Ensemble d'ids de process qui ne grandit pas sans fin (les ids ne sont pas réutilisés)."""

    def __init__(self, limit: int = 20000):
        super().__init__()
        self.limit = limit

    def add(self, x) -> None:
        if len(self) >= self.limit:
            self.clear()
        super().add(x)


# ---------------------------------------------------------------------------
# A4 : source (on publie ce qui naît chez nous)
# ---------------------------------------------------------------------------
class DropWatch:
    """Repère les objets proc 255 apparus chez nous à publier (voir l'en-tête)."""

    LOAD_WINDOW = 2.5      # s après l'entrée dans la zone : objets « placés » (chargés avec la salle)
    MAX_PER_MSG = 16
    EVENT_AFTER = 5.0      # s après la fin d'un événement : objets mis en file encore publiables
    EVENT_KEEP = 60.0      # s : un objet en file plus vieux que cela est oublié (événement très long)
    EVENT_MAX = 64

    def __init__(self, enabled: bool = True, load_window: float | None = None, items: bool = True,
                 fees: bool = False, event_queue: bool = False):
        self.enabled = enabled
        self.items = items            # A4 : objets proc 255 (consommables communs)
        self.fees = fees              # synchro v2 : fées libres (proc 0x168, type 1)
        self.event_queue = event_queue    # synchro v2 : objets nés pendant un événement, publiés après
        self.load_window = self.LOAD_WINDOW if load_window is None else load_window
        self.seen = _Bounded()        # ids déjà vus (publiés ou non)
        self.mine = _Bounded()        # ids créés par NOTRE commande 4 (copies reçues)
        self.zone: str | None = None
        self.zone_t = 0.0
        self.queued: dict[int, tuple[float, str]] = {}   # pid -> (heure, "objet" | "fee") nés en événement
        self.busy_end: float | None = None               # fin du dernier événement (file à vider)
        self.was_busy = False
        self.sent = self.skipped_busy = self.skipped_kind = 0
        self.sent_fees = self.sent_late = 0

    def created(self, pid: int) -> None:
        self.mine.add(pid)

    def _entry(self, it: dict, room: int) -> dict:
        home = it.get("home") or it.get("pos")
        return {"nid": int(it["pid"]) & 0xFFFFFFFF, "no": it["no"], "bit": int(it["bit"]) & BIT_NONE,
                "type": it["type"] & 3, "act": it["act"] & 0x3F, "room": room,
                "pos": [round(float(v), 1) for v in home], "rotY": int(it.get("rotY", 0))}

    @staticmethod
    def _fee(f: dict, room: int) -> dict:
        return {"nid": int(f["pid"]) & 0xFFFFFFFF, "kind": "fee", "no": ITEM_FAIRY, "room": room,
                "pos": [round(float(v), 1) for v in f["pos"]], "rotY": int(f.get("rotY", 0))}

    def _late_ok(self, it: dict, kind: str) -> bool:
        """Objet né pendant l'événement, relu à la fin : toujours publiable (pas ramassé, pas
        d'action de boss, pas de switch, pas de clé) ?"""
        if kind == "fee":
            return fairy_publishable(it)
        return publishable(it) and it.get("status", 0) < 5

    def observe(self, now: float, zone: str | None, room: int, items, busy: bool,
                room_t: float | None = None, fairies=None) -> list[dict]:
        """items : [{pid, no, bit, type, act, status, params, home, room, rotY}] ; fairies :
        [{pid, type, mode, params, pos, room, rotY}] (proc 0x168) ; renvoie les entrées « drop » à
        envoyer. room_t : heure du dernier (re)chargement de la salle vu par l'échantillonneur
        (rechargement sans changement de zone : chute, Game Over)."""
        if not self.enabled:
            return []
        if zone != self.zone:
            self.zone, self.zone_t = zone, now
            self.queued.clear()               # autre salle : les objets en file n'y sont plus
        if room_t is not None and room_t > self.zone_t:
            self.zone_t = room_t
            self.queued.clear()
        if busy:
            self.busy_end = None
        elif self.was_busy:
            self.busy_end = now               # fin de l'événement : la file est vidée dans les EVENT_AFTER s
        self.was_busy = busy
        if zone is None or (items is None and fairies is None):
            return []
        loading = now - self.zone_t < self.load_window
        out = []
        lists = []
        if self.items and items is not None:
            lists.append(("objet", items))
        if self.fees and fairies is not None:
            lists.append(("fee", fairies))
        for kind, lst in lists:
            for it in lst:
                pid = it.get("pid")
                if pid in self.seen:
                    continue
                self.seen.add(pid)
                if pid in self.mine or loading or it.get("room") != room:
                    continue
                ok = fairy_publishable(it) if kind == "fee" else publishable(it)
                if busy:
                    self.skipped_busy += 1         # critique §12 : objet d'un coffre, d'un script…
                    if self.event_queue and ok and len(self.queued) < self.EVENT_MAX:
                        self.queued[pid] = (now, kind)     # §3.5 : publié à la fin de l'événement
                    continue
                if not ok:
                    self.skipped_kind += 1
                    continue
                if kind == "fee":
                    out.append(self._fee(it, room))
                    self.sent_fees += 1
                else:
                    out.append(self._entry(it, room))
        out += self._flush_queue(now, room, items, fairies, busy)
        self.sent += len(out)
        return out[:self.MAX_PER_MSG]

    def _flush_queue(self, now: float, room: int, items, fairies, busy: bool) -> list[dict]:
        """§3.5 : à la fin de l'événement, objets nés pendant celui-ci et toujours là."""
        for pid in [p for p, (t, _k) in self.queued.items() if now - t > self.EVENT_KEEP]:
            del self.queued[pid]
        if not self.queued or busy or self.busy_end is None:
            return []
        if now - self.busy_end > self.EVENT_AFTER:
            self.queued.clear()               # pas revus dans les EVENT_AFTER s : disparus
            return []
        now_items = {it.get("pid"): it for it in (items or [])}
        now_fees = {f.get("pid"): f for f in (fairies or [])}
        out = []
        for pid, (_t, kind) in list(self.queued.items()):
            it = (now_fees if kind == "fee" else now_items).get(pid)
            if it is None:
                continue                      # pas encore relu (ou disparu : oublié à EVENT_AFTER)
            del self.queued[pid]
            if it.get("room") != room or not self._late_ok(it, kind):
                continue
            out.append(self._fee(it, room) if kind == "fee" else self._entry(it, room))
            self.sent_late += 1
            if kind == "fee":
                self.sent_fees += 1
        return out

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        extra = f", {self.skipped_busy} né(s) pendant un événement" if self.skipped_busy else ""
        if self.sent_late:
            extra += f" (dont {self.sent_late} envoyé(s) à la fin)"
        if self.sent_fees:
            extra += f", {self.sent_fees} fée(s)"
        return f"{self.sent} objet(s) apparu(s) chez toi envoyé(s){extra}"


# ---------------------------------------------------------------------------
# A4 : réception (copies créées chez nous)
# ---------------------------------------------------------------------------
class DropApply:
    TTL = 8.0              # s : un objet de table disparaît en ~10 s ; au-delà, inutile de le créer
    ROOM_SETTLE = 1.0      # s dans la zone avant de créer quoi que ce soit (acteurs de la salle chargés)
    MAX_PER_STEP = 4       # créations par tick (chacune attend une image du jeu)
    MAX_PENDING = 64

    def __init__(self, enabled: bool = True, items: bool = True, fees: bool = False):
        self.enabled = enabled
        self.items = items                # A4 : objets proc 255
        self.fees = fees                  # synchro v2 : fées libres (entrées kind « fee »)
        self.pending: dict[tuple, tuple[float, str, dict]] = {}   # (émetteur, nid) -> (t, zone, entrée)
        self.done: set[tuple] = set()
        self._lock = threading.Lock()     # receive() : boucle réseau ; step() : thread de travail
        self.created = self.skipped_bit = self.failed = self.expired = 0

    def receive(self, sender, zone, entries, now: float) -> int:
        if not self.enabled or not isinstance(zone, str) or not isinstance(entries, list):
            return 0
        n = 0
        with self._lock:
            for raw in entries[:DropWatch.MAX_PER_MSG]:
                e = valid_drop(raw)
                if e is None or not (self.fees if e.get("kind") == "fee" else self.items):
                    continue
                key = (sender, e["nid"])
                if key in self.done or key in self.pending:
                    continue
                if len(self.pending) >= self.MAX_PENDING:
                    break
                self.pending[key] = (now, zone, e)
                n += 1
        return n

    def forget(self, sender, nid) -> bool:
        """Synchro v2 (A4 « premier arrivé », synchro/compteurs.py) : l'objet (sender, nid) a été ramassé par
        un autre joueur : sa copie n'est plus créée ici. True si une création en attente est oubliée."""
        key = (sender, nid)
        with self._lock:
            self.done.add(key)
            return self.pending.pop(key, None) is not None

    def step(self, now: float, my_zone: str | None, zone_age: float, busy: bool, taken=None) -> list[tuple]:
        """Renvoie [(clé, entrée)] à créer maintenant. taken(bit, salle) -> True si l'objet
        unique est déjà pris chez nous (le jeu refuserait la création)."""
        with self._lock:
            return self._step(now, my_zone, zone_age, busy, taken)

    def _step(self, now, my_zone, zone_age, busy, taken) -> list[tuple]:
        for key in [k for k, (t, _z, _e) in self.pending.items() if now - t > self.TTL]:
            del self.pending[key]
            self.done.add(key)
            self.expired += 1
        if my_zone is None or busy or zone_age < self.ROOM_SETTLE:
            return []
        out = []
        for key, (_t, zone, e) in list(self.pending.items()):
            if zone != my_zone:
                continue
            del self.pending[key]
            self.done.add(key)
            if e["bit"] != BIT_NONE and taken is not None and taken(e["bit"], e["room"]):
                self.skipped_bit += 1
                continue
            out.append((key, e))
            if len(out) >= self.MAX_PER_STEP:
                break
        if len(self.done) > 20000:
            self.done.clear()
        return out

    def result(self, ok: bool) -> None:
        if ok:
            self.created += 1
        else:
            self.failed += 1

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = f"{self.created} copie(s) créée(s) chez toi"
        if self.skipped_bit:
            s += f", {self.skipped_bit} déjà pris(s)"
        if self.failed:
            s += f", {self.failed} échec(s)"
        if self.pending:
            s += f", {len(self.pending)} en attente"
        return s


# ---------------------------------------------------------------------------
# A5 : objets uniques pris ailleurs
# ---------------------------------------------------------------------------
def item_bits(bits: dict, stage: str, cur_idx: int | None) -> list[tuple[int, int | None]]:
    """Bits d'objets uniques contenus dans un ensemble de bits de progression ({clé: {off: masque}}).
    Renvoie [(bit, salle)] : salle None pour un bit de stage (mem<cur_idx> octets 0x14-0x17,
    u32 BE mItem[0] : bits 0-31), salle r pour un bit de zone (zone:<stage>:<r> octets 0x06-0x07,
    u16 BE mItem : bits 0x40-0x4F) [DECOMP d_save.cpp isItem]."""
    out = []
    if cur_idx is not None:
        for off, m in (bits.get(f"mem{cur_idx}") or {}).items():
            if 0x14 <= off < 0x18:
                for b in range(8):
                    if m >> b & 1:
                        out.append(((3 - (off - 0x14)) * 8 + b, None))
    prefix = f"zone:{stage}:"
    for key, offs in bits.items():
        if not key.startswith(prefix):
            continue
        try:
            room = int(key[len(prefix):])
        except ValueError:
            continue
        for off, m in offs.items():
            if off in (0x06, 0x07):
                for b in range(8):
                    if m >> b & 1:
                        out.append((MEMORY_ITEM + (8 if off == 0x06 else 0) + b, room))
    return sorted(out, key=lambda x: (x[0], -1 if x[1] is None else x[1]))


def item_taken(mem, bit: int, room: int | None) -> bool:
    """dSv_info_c::isItem en lecture : l'objet unique `bit` est-il déjà pris chez nous ?
    (bits < 0x40 : mItem de la copie vivante mMemory ; 0x40-0x4F : mItem de la zone de `room`).
    Bits 32-63 : mItem n'a qu'un u32, le mot lu est mVisitedRoom[0]. C'est voulu : le jeu fait
    exactement la même lecture (dSv_memBit_c::isItem, ``mItem[i_no >> 5]`` avec i_no < 64
    [DECOMP d_save.cpp]) ; on reproduit donc sa décision de refuser ou non la création."""
    from jeu import progress as prog
    if not 0 <= bit < MEMORY_ITEM + ZONE_ITEM:
        return False
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return False
    gi = struct.unpack(">I", raw)[0]
    if not 0x10000000 <= gi < 0x50000000:
        return False
    if bit < MEMORY_ITEM:
        w = mem.try_read(gi + prog.GI_CUR_MEMORY + 0x14 + 4 * (bit >> 5), 4)
        return w is not None and bool(struct.unpack(">I", w)[0] >> (bit & 0x1F) & 1)
    base = prog._zone_base(mem, gi, room if room is not None else -1)
    if base is None:
        return False
    w = mem.try_read(base + prog.ZONE_BITS + 0x6, 2)
    return w is not None and bool(struct.unpack(">H", w)[0] >> (bit - MEMORY_ITEM) & 1)


def unique_targets(bits: list[tuple[int, int | None]], items) -> list[dict]:
    """Objets proc 255 encore affichés dont le bit vient d'être posé (pris ailleurs). Comparaison
    sur le s32 +0x744 entier (critique §3) ; jamais un objet en train d'être ramassé chez nous."""
    want = {b: r for b, r in bits if 0 <= b < BIT_NONE}
    out = []
    for it in items or []:
        bit = it.get("bit")
        if bit not in want or it.get("status", 0) >= 5:
            continue
        room = want[bit]
        if room is not None and it.get("room") != room:
            continue
        out.append(it)
    return out



# ---------------------------------------------------------------------------
# A6 : jarres cassées
# ---------------------------------------------------------------------------
# Casse par le jeu (daTsubo seulement, voir docs/etudes/jarres_casse.md). Instance HD 0x93C =
# GC 0x820 + 0x11C [LIVE] : les champs propres à daTsubo sont décalés de +0x11C, vérifié dans le
# code HD [HD-ASM] :
#   _execute 0x024CE71C : lha 0x91C (m800 != 0 => exécutée même hors champ) ;
#   damage_kill_proc 0x024CD3CC : lha r12,0x91C ; si > 0 : r12-1 ; sth 0x91C ; à 0 : damaged(1, NULL)
#     (0x024CC238 : objet de la table, éclats eff_break_tsubo, son se_break), puis _execute appelle
#     fopAcM_delete ;
#   damaged lit itemNo (PrmAbstract 0x02048038 : lwz 0xB0 = mParameters) AU MOMENT de la casse ;
#   fopAcM_createItemFromTable ne crée rien pour itemNo 0x3F (0x025D82C4 cmpwi 0x3F).
#   _execute 0x024CE78C : lwz r0,0x794 ; cmpwi 2 (m678, le mode de la jarre) [HD-ASM, revue sûreté].
TSUBO_PROC = 453
TSUBO_MODE = 0x794         # s32 m678 (GC 0x678) : 0 cachée, 1 apparition, 2 posée, 3 roule, 4 portée,
                           # 5 lancée, 6 coule, 7 flotte [DECOMP d_a_tsubo.cpp mode_*_init]
TSUBO_KILL_TIMER = 0x91C   # s16 m800 (GC 0x800) [HD-ASM + LIVE : 0 sur les jarres au repos]
ACTOR_PARAMS = 0xB0        # u32 base_process_class::mParameters
POT_NO_ITEM = 0x3F
POT_BREAK_MODES = (2, 3, 7)   # posée, roule au sol, flotte : on peut la casser. Les autres : attendre
                              # (cachée en attente d'un switch, dans les mains, en vol, qui coule)
POT_SPEC_SWITCH = 5        # prm spec 5 sans son switch : _execute ne fait RIEN (m800 ne bouge pas)
POT_FX = ("casse", "fumee", "rien")    # --pot-fx : vraie casse (repli sans effet) / fumée / rien
# Résultats de break_pot
ARMED, WAIT, FAIL, RACE = "armee", "attendre", "echec", "course"
_POT_SNAP = TSUBO_KILL_TIMER + 2       # une seule lecture : en-tête, mParameters, mode et minuteur


def _same_actor(p: dict, head: bytes | None) -> bool:
    """L'en-tête relu est-il toujours celui de la jarre `p` ? (le jeu remet mBsType à 0 à la
    destruction ; la mémoire libérée peut être réutilisée par un autre acteur)"""
    if head is None or len(head) < 0x0A:
        return False
    bs_type, pc_id = struct.unpack_from(">II", head, 0)
    proc, = struct.unpack_from(">h", head, 8)
    return bs_type != 0 and pc_id == p.get("pid") and proc == p.get("proc")


def pot_spec(params: int) -> int:
    return (params >> 8) & 0x3F


def break_pot(mem, p: dict, keep_item: bool = False) -> str:
    """A6, --pot-fx casse : fait casser la jarre `p` (relevé de CemuSampler._ground) PAR LE JEU,
    avec ses éclats et son son. Une seule lecture (en-tête, mParameters, mode, minuteur), puis :
      - mode pas dans POT_BREAK_MODES (portée, lancée, cachée…) : rien écrit, WAIT (réessayer) ;
      - sinon : itemNo (6 bits bas de mParameters) = 0x3F, sauf keep_item (le contenu arrive par
        A4, ou pas du tout : on laisse alors son objet), puis m800 (minuteur de casse) = 1 : à la
        prochaine exécution, damage_kill_proc() le passe à 0 et appelle damaged(1), puis
        _execute supprime la jarre. En-tête relu après l'écriture : RACE si la jarre a disparu
        pendant l'écriture (casse simultanée chez nous ; mémoire peut-être réutilisée).
    Aucun pointeur écrit, aucune commande de boîte aux lettres. FAIL (rien écrit, ou seulement
    itemNo, sans effet tant qu'elle ne casse pas) : pas cette daTsubo vivante, ou écriture
    impossible ; l'appelant se replie sur la suppression."""
    if mem is None or p.get("proc") != TSUBO_PROC or not _int(p.get("base")) or not _int(p.get("pid")):
        return FAIL
    base = p["base"]
    try:
        raw = mem.try_read(base, _POT_SNAP)
        if raw is None or len(raw) < _POT_SNAP or not _same_actor(p, raw):
            return FAIL                       # supprimée entre-temps (mémoire libérée ou réutilisée)
        params, = struct.unpack_from(">I", raw, ACTOR_PARAMS)
        mode, = struct.unpack_from(">i", raw, TSUBO_MODE)
        if mode not in POT_BREAK_MODES:
            return WAIT
        if not keep_item and params & POT_ITEM_MASK != POT_NO_ITEM:
            mem.write(base + ACTOR_PARAMS, struct.pack(">I", params | POT_ITEM_MASK))
        mem.write(base + TSUBO_KILL_TIMER, struct.pack(">h", 1))   # après itemNo : lu à la casse
        head = mem.try_read(base, 0x10)
    except Exception:                         # mémoire ouverte en lecture seule…
        return FAIL
    return ARMED if _same_actor(p, head) else RACE


def unarm_pot(mem, p: dict) -> bool:
    """Défait break_pot (repli refusé plusieurs fois) : minuteur à 0 et itemNo d'origine, si c'est
    toujours la même jarre. Vrai si remise dans son état."""
    if mem is None or p.get("proc") != TSUBO_PROC or not _int(p.get("base")):
        return False
    base = p["base"]
    try:
        raw = mem.try_read(base, _POT_SNAP)
        if raw is None or len(raw) < _POT_SNAP or not _same_actor(p, raw):
            return False
        mem.write(base + TSUBO_KILL_TIMER, struct.pack(">h", 0))
        params, = struct.unpack_from(">I", raw, ACTOR_PARAMS)
        orig = int(p.get("params", params)) & POT_ITEM_MASK
        if params & POT_ITEM_MASK != orig:
            mem.write(base + ACTOR_PARAMS, struct.pack(">I", (params & ~POT_ITEM_MASK) | orig))
        home = p.get("home_pos")
        if p.get("jug") and isinstance(home, (list, tuple)) and len(home) == 3 \
                and all(_num(v) and abs(v) < 1e6 for v in home):
            # synchro v2 : cruche déplacée à l'impact par break_jug_at : remise là où elle était posée
            xyz = struct.pack(">3f", *(float(v) for v in home))
            mem.write(base + POS_CUR, xyz)
            mem.write(base + POS_OLD, xyz)
    except Exception:
        return False
    return True


JUG_SNAP = TSUBO_KILL_TIMER + 2


def break_jug_at(mem, p: dict, at) -> str:
    """Synchro v2 (cruches) : fait casser la cruche d'eau `p` PAR LE JEU au point d'impact `at`
    (sync2_cruches_drops.md §3.1). Une lecture (en-tête, mParameters, mType, mode, minuteur), puis
    dans l'ordre : current.pos (+0x314), old.pos (+0x300) = at (même valeur : pas de traversée de mur
    pour Acch), speed (+0x33C) = 0, itemNo = 0x3F (rien à lâcher), m800 = 1. À l'image suivante,
    damage_kill_proc -> damaged(1) -> fopKyM_create(0x17, salle, &current.pos) : éclaboussure, son,
    eau qui éteint le feu et, au-dessus de la lave, la dalle du jeu au même (x, z).
    Résultats comme break_pot : ARMED, WAIT (pas posée : portée, lancée, cachée…, rien écrit),
    RACE (disparue pendant l'écriture), FAIL (pas cette cruche vivante, `at` invalide, écriture
    impossible : l'appelant la retire par la commande 5)."""
    if mem is None or p.get("proc") != TSUBO_PROC or not _int(p.get("base")) or not _int(p.get("pid")):
        return FAIL
    if not (isinstance(at, (list, tuple)) and len(at) == 3 and all(_num(v) and abs(v) < 1e6 for v in at)):
        return FAIL
    base = p["base"]
    try:
        raw = mem.try_read(base, JUG_SNAP)
        if raw is None or len(raw) < JUG_SNAP or not _same_actor(p, raw):
            return FAIL
        jtype, = struct.unpack_from(">i", raw, TSUBO_TYPE)
        if jtype != WATER_JUG:
            return FAIL                       # pas une cruche d'eau (mémoire réutilisée ?)
        mode, = struct.unpack_from(">i", raw, TSUBO_MODE)
        if mode != 2:
            return WAIT                       # seulement posée (étude §4 : m678 != 2 refusé)
        params, = struct.unpack_from(">I", raw, ACTOR_PARAMS)
        p["home_pos"] = struct.unpack_from(">3f", raw, POS_CUR)   # pour unarm_pot (repli refusé)
        xyz = struct.pack(">3f", *(float(v) for v in at))
        mem.write(base + POS_CUR, xyz)
        mem.write(base + POS_OLD, xyz)
        mem.write(base + SPEED, struct.pack(">3f", 0.0, 0.0, 0.0))
        if params & POT_ITEM_MASK != POT_NO_ITEM:
            mem.write(base + ACTOR_PARAMS, struct.pack(">I", params | POT_ITEM_MASK))
        mem.write(base + TSUBO_KILL_TIMER, struct.pack(">h", 1))
        head = mem.try_read(base, 0x10)
    except Exception:                         # mémoire ouverte en lecture seule…
        return FAIL
    return ARMED if _same_actor(p, head) else RACE


def pot_state(mem, p: dict) -> tuple[bool, int | None] | None:
    """Jarre armée par break_pot : (encore vivante ?, minuteur m800 ou None). None : lecture ratée."""
    if mem is None or not _int(p.get("base")):
        return None
    try:
        head = mem.try_read(p["base"], 0x10)
        if head is None:
            return None
        if not _same_actor(p, head):
            return False, None
        raw = mem.try_read(p["base"] + TSUBO_KILL_TIMER, 2) if p.get("proc") == TSUBO_PROC else None
        return True, (struct.unpack(">h", raw)[0] if raw else None)
    except Exception:
        return None


def pot_gone(mem, p: dict) -> bool:
    """Latence : la jarre `p`, absente du dernier relevé, est-elle PROUVÉE détruite ? Une seule lecture
    (en-tête) : True seulement si la mémoire se lit ET que ce n'est plus cet acteur (mBsType remis à 0 par
    le jeu, ou mémoire reprise par un autre). Lecture ratée ou acteur toujours là (position NaN d'un
    relevé) : False, la confirmation longue de PotWatch s'applique. Lecture seule."""
    if mem is None or not _int(p.get("base")):
        return False
    try:
        head = mem.try_read(p["base"], 0x10)
    except Exception:
        return False
    return head is not None and len(head) >= 0x0A and not _same_actor(p, head)


def zone_stage(zone) -> str | None:
    return zone.rsplit(":", 1)[0] if isinstance(zone, str) and ":" in zone else None


def break_messages(zone: str | None, entries: list[dict]) -> list[dict]:
    """Messages « break » : une jarre est annoncée dans la zone de SA salle d'origine (« room »
    de l'entrée), pas forcément la nôtre (jarre portée puis lancée dans une autre salle)."""
    stage = zone_stage(zone)
    if stage is None:
        return []
    by: dict[str, list] = {}
    for e in entries:
        r = e.get("room")
        z = f"{stage}:{r}" if _int(r) else zone
        w = {"key": e["key"], "pos": e["pos"]}
        if e.get("keep"):
            w["keep"] = True
        if e.get("jug"):
            w["jug"] = True
            w["at"] = e.get("at") or e["pos"]
        by.setdefault(z, []).append(w)
    return [{"type": "break", "zone": z, "list": lst} for z, lst in by.items()]


def _short(key: str) -> str:
    return key if len(key) <= 40 else key[:40] + "…"


class PotWatch:
    """Repère les jarres et pierres disparues (cassées) chez nous. Chaque disparition non
    relayée laisse sa raison dans `notes` (journal du client)."""

    SETTLE = 1.5     # s dans la zone avant de croire une disparition (chargement de la salle)
    HOLD = 0.6       # s de confirmation : annulée si la jarre revient (relevé raté), un chargement…
    HOLD_SURE = 0.09 # s (latence) : confirmation courte (2 relevés à 20 Hz) quand la suppression est PROUVÉE
                     # (en-tête relu : acteur détruit) ET qu'une autre jarre de notre salle est encore là (pas
                     # un déchargement : il les emporte toutes) ; sinon HOLD. Voir docs/etudes/latence.md.
    FAST_RESCAN = 0.25   # s (latence) : au plus un relevé complet des acteurs (~45 ms en jeu) demandé par les
                         # casses rapides tous les FAST_RESCAN s (rafale : pas un par tour de boucle)
    MASS = 4         # plus de N disparitions d'un coup : déchargement si surtout d'autres salles,
                     # ou s'il ne reste aucune jarre de notre salle (salle déchargée avant que le
                     # numéro de salle change, ordre pas vérifié [HYPO])
    MASS_MAX = 10    # … et toujours au-delà de N (aucune bombe n'en casse autant)
    MAX_PER_MSG = 16
    NONE_GRACE = 1.0     # s : lectures ratées tolérées sans oublier les jarres connues
    CARRIED = 3.0        # s : jarre portée ou en mouvement il y a moins de CARRIED s : sa disparition
                         # hors de notre salle est une casse (portée puis lancée dans une autre salle)
    RESEND = 30.0        # s : casses renvoyées après une reconnexion (perdues dans la coupure)
    JUG_MAGMA_D = 250.0  # u (x, z) : dalle née près de la dernière position vue de la cruche = impact
    JUG_MAGMA_BEFORE = 1.0   # s : dalle vue au plus tôt JUG_MAGMA_BEFORE s avant la disparition

    def __init__(self, enabled: bool = True, jugs: bool = False, fees: bool = False):
        self.enabled = enabled
        self.jugs = jugs                  # synchro v2 : cruches d'eau relayées (à l'impact)
        self.fees = fees                  # synchro v2 : jarres à fée relayées (la fée arrive par A4)
        self.magma_first: dict[int, tuple[float, tuple]] = {}   # dalle -> (première vue, position)
        self.magma_used: set[int] = set()
        self.rescan_wanted = False        # cruche disparue : rescan des acteurs (voir la dalle tout de suite)
        self.sent_jugs = self.jugs_at_magma = 0
        self.zone: str | None = None
        self.zone_t = 0.0
        self.known: dict[int, dict] = {}
        self.ignore = _Bounded()          # ids supprimés par nous (jarre cassée ailleurs)
        self.held: list[tuple[float, dict, int]] = []    # (heure, entrée, pid) en confirmation
        self.backlog: list[dict] = []     # confirmées au-delà de MAX_PER_MSG : message suivant
        self.carried: dict[int, float] = {}
        self.none_t: float | None = None
        self.recent: list[tuple[float, str, dict]] = []    # (heure, zone, entrée) envoyées
        self.notes: list[str] = []
        self.sent = self.skipped_jug = self.skipped_content = self.skipped_mass = 0
        self.came_back = 0                # absentes d'un relevé puis revenues : pas cassées
        self.fast = 0                     # latence : casses confirmées en HOLD_SURE (suppression prouvée)
        self.delays: list[float] = []     # latence : détection -> envoi (s) des dernières casses (mesure)
        self.seen_t: dict[int, float] = {}    # pid en confirmation -> heure réelle de la disparition
        self.fast_rescan_due = False      # latence : relevé complet à demander (objet d'une jarre cassée, A4)
        self.fast_rescan_t = float("-inf")

    def deleted(self, pid: int) -> None:
        self.ignore.add(pid)

    def restored(self, pid: int) -> None:
        """Jarre armée puis remise dans son état (repli refusé) : sa casse par nous sera relayée."""
        self.ignore.discard(pid)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def _drop_held(self, why: str) -> None:
        n = len(self.held) + len(self.backlog)
        if n:
            self.notes.append(f"jarres : {n} casse(s) en attente abandonnée(s) ({why})")

    def _reset(self, zone=None, t=0.0, known=None) -> None:
        self.zone, self.zone_t, self.known, self.held, self.backlog = zone, t, known or {}, [], []
        self.carried = {}

    def _track_magma(self, now: float, magmas, fresh: bool) -> None:
        """Dalles de magma vues (proc 42) : heure de première vue ; au chargement : « anciennes »."""
        if magmas is None:
            return
        cur = {}
        for m in magmas:
            pid = m.get("pid")
            old = self.magma_first.get(pid)
            cur[pid] = old if old is not None else (-1e9 if fresh else now, tuple(m.get("pos") or (0, 0, 0)))
        self.magma_first = cur
        self.magma_used &= set(cur)

    def _impact(self, pos, t: float) -> list:
        """§3.1 : point d'impact d'une cruche disparue à t : la dalle née chez nous la plus proche
        (x, z < JUG_MAGMA_D), sinon la dernière position vue de la cruche."""
        best = None
        for pid, (t0, mp) in self.magma_first.items():
            if pid in self.magma_used or t0 < t - self.JUG_MAGMA_BEFORE:
                continue
            d = math.hypot(float(mp[0]) - float(pos[0]), float(mp[2]) - float(pos[2]))
            if d <= self.JUG_MAGMA_D and (best is None or d < best[0]):
                best = (d, pid, mp)
        if best is None:
            return [round(float(v), 1) for v in pos]
        self.magma_used.add(best[1])
        self.jugs_at_magma += 1
        return [round(float(v), 1) for v in best[2]]

    def observe(self, now: float, zone: str | None, room: int, pots, leaving: bool = False,
                room_t: float | None = None, held: int = 0, magmas=None, sure=None) -> list[dict]:
        """pots : [{pid, key, pos, home_room, proc, params, base?, moving?}] ; renvoie les entrées
        {key, pos, room, keep?, jug?, at?} à annoncer (voir break_messages). leaving : changement de
        stage en cours ; room_t : dernier (re)chargement de la salle ; held : adresse de l'objet
        porté ; magmas : dalles de magma (proc 42) vues chez nous, sans nos copies (commande 4).
        sure (latence) : sure(p) -> True si l'acteur de la jarre disparue est PROUVÉ détruit (en-tête
        relu, voir pot_gone) ; None : toujours la confirmation longue HOLD (--latence-prudente)."""
        if not self.enabled:
            return []
        if self.jugs:
            fresh = leaving or zone != self.zone or (room_t is not None and room_t > self.zone_t)
            self._track_magma(now, magmas if zone is not None else None, fresh)
        if leaving:
            self._drop_held("changement de stage")
            self._reset()
            self.none_t = None
            return []
        if zone is None or pots is None:
            # lecture ratée ou écran de chargement : on garde ce qu'on sait NONE_GRACE s (un
            # échantillon raté ne doit pas faire oublier une casse en cours de confirmation)
            if self.none_t is None:
                self.none_t = now
            if now - self.none_t > self.NONE_GRACE and self.zone is not None:
                self._drop_held("lecture du jeu impossible")
                self._reset()
            return []
        self.none_t = None
        cur = {p["pid"]: p for p in pots}
        if zone != self.zone or (room_t is not None and room_t > self.zone_t):
            carry: list[dict] = []
            if zone != self.zone and self.zone is not None and zone_stage(zone) == zone_stage(self.zone):
                # changement de salle juste après une casse (porte franchie pendant la confirmation) :
                # elle part quand même, dans la zone de sa salle d'origine (pas revenue entre-temps)
                carry = self.backlog + [e for _, e, pid in self.held if pid not in cur and "room" in e]
            else:
                self._drop_held("salle rechargée" if zone == self.zone else "changement de zone")
            self._reset(zone, max(now, room_t or 0.0), cur)
            self.backlog = carry
            return self._out(now, zone)
        for pid, p in cur.items():
            if p.get("moving") or (held and p.get("base") == held):
                self.carried[pid] = now
        # jarre absente d'un seul relevé (lecture ratée, position NaN) puis revenue : pas cassée
        back = [h for h in self.held if h[2] in cur]
        if back:
            self.came_back += len(back)
            self.held = [h for h in self.held if h[2] not in cur]
        gone = [p for pid, p in self.known.items() if pid not in cur]
        mine = [p for p in gone if p.get("home_room") == room and p["pid"] not in self.ignore]
        # déchargement : surtout d'autres salles, ou TOUTES les jarres de notre salle (une bombe en
        # laisse), ou trop d'un coup. Une casse multiple (bombe, boomerang) est relayée.
        rest = any(p.get("home_room") == room for p in cur.values())
        if not rest and self.held:
            # latence : plus aucune jarre de notre salle (le témoin d'une confirmation courte a disparu avant
            # l'annonce : déchargement étalé sur deux relevés ?) : retour à la confirmation longue, comme avant
            self.held = [(self.seen_t.get(pid, t), e, pid) for t, e, pid in self.held]
        if len(gone) > self.MASS and (2 * len(mine) <= len(gone) or not rest or len(gone) > self.MASS_MAX):
            self.held = []                    # salle ou stage déchargé
            if mine:
                self.skipped_mass += len(mine)
                self.notes.append(f"jarres : {len(gone)} disparues d'un coup (dont {len(mine)} de ta salle) : "
                                  "déchargement supposé, non relayées")
        elif now - self.zone_t < self.SETTLE:
            if mine:
                self.notes.append(f"jarres : {len(mine)} disparue(s) pendant le chargement de la salle : "
                                  "non relayée(s)")
        else:
            for p in gone:
                if p["pid"] in self.ignore:
                    continue                  # supprimée par nous (cassée chez un autre)
                proc, params = p.get("proc", 0), p.get("params", 0)
                carried = now - self.carried.get(p["pid"], -1e9) <= self.CARRIED
                if p.get("home_room") != room and not carried:
                    continue                  # salle voisine déchargée : silencieux
                jug = is_water_jug(proc, params)
                if jug and not self.jugs:
                    self.skipped_jug += 1
                    self.notes.append("cruche d'eau cassée chez toi : non relayée (le magma ne refroidirait "
                                      "pas chez l'autre ; --share-jugs)")
                    continue
                if not pot_relayable(proc, params, self.jugs, self.fees):
                    self.skipped_content += 1         # clé, quart de cœur… : chacun casse la sienne
                    self.notes.append(f"jarre à contenu précieux (objet 0x{params & POT_ITEM_MASK:02X}) cassée "
                                      "chez toi : non relayée, chacun casse la sienne")
                    continue
                e = {"key": p["key"], "pos": [round(float(v), 1) for v in p["pos"]]}
                if _int(p.get("home_room")) and 0 <= p["home_room"] < 64:
                    e["room"] = p["home_room"]
                if p.get("home_room") != room:
                    e["keep"] = True          # cassée hors de sa salle : A4 ne publie pas son objet
                if jug:
                    e["jug"] = True           # « at » calculé à la fin de la confirmation (dalle née ?)
                    self.rescan_wanted = True
                t_held = now
                if sure is not None and not jug and rest and p.get("home_room") == room and "room" in e:
                    # latence : suppression prouvée et une autre jarre de la salle encore vivante : ni relevé
                    # raté, ni déchargement. Confirmation courte (l'heure est avancée, pas la règle : retour
                    # de la jarre, changement de stage, salle rechargée et disparition en masse annulent encore)
                    try:
                        ok = bool(sure(p))
                    except Exception:
                        ok = False
                    if ok:
                        t_held = now - (self.HOLD - self.HOLD_SURE)
                        self.fast += 1
                        self.fast_rescan_due = True   # son objet (A4) vu au prochain relevé, pas 1 s après
                self.held.append((t_held, e, p["pid"]))
                self.seen_t[p["pid"]] = now
        self.known = cur
        for pid in [k for k, t in self.carried.items() if now - t > self.CARRIED and k not in cur]:
            del self.carried[pid]
        return self._out(now, zone)

    def _out(self, now: float, zone: str) -> list[dict]:
        if self.fast_rescan_due and now - self.fast_rescan_t >= self.FAST_RESCAN:
            # latence : relevé complet demandé, borné (une rafale de casses n'en demande pas un par tour)
            self.fast_rescan_due, self.fast_rescan_t, self.rescan_wanted = False, now, True
        for t, e, _ in self.held:
            if now - t >= self.HOLD and e.get("jug") and "at" not in e:
                e["at"] = self._impact(e["pos"], t)
                self.sent_jugs += 1
        for e in self.backlog:
            if e.get("jug") and "at" not in e:
                e["at"] = list(e["pos"])      # changement de salle pendant la confirmation
                self.sent_jugs += 1
        out = self.backlog + [e for t, e, _ in self.held if now - t >= self.HOLD]
        for t, _e, pid in self.held:          # latence (mesure) : attente réelle détection -> envoi
            if now - t >= self.HOLD:
                self.delays.append(now - self.seen_t.get(pid, t))
        del self.delays[:-32]
        self.held = [h for h in self.held if now - h[0] < self.HOLD]
        self.seen_t = {h[2]: self.seen_t[h[2]] for h in self.held if h[2] in self.seen_t}
        out, self.backlog = out[:self.MAX_PER_MSG], out[self.MAX_PER_MSG:]
        self.sent += len(out)
        if out:
            self.recent = [r for r in self.recent if now - r[0] <= self.RESEND] + [(now, zone, e) for e in out]
        return out

    def resend(self, now: float) -> list[dict]:
        """Après une coupure : messages « break » des casses des RESEND dernières secondes (une
        casse écrite dans une connexion déjà morte est perdue sans erreur). Le serveur les range
        par clé et le receveur ignore une clé déjà traitée : renvoyer est sans risque. Marquées
        « keep » : l'objet apparu (A4) a pu être perdu dans la coupure, le receveur garde le sien."""
        by: dict[str, list] = {}
        for t, zone, e in self.recent:
            if now - t <= self.RESEND:
                by.setdefault(zone, []).append(dict(e, keep=True))
        self.recent = []                      # une fois par coupure (la file du client les garde)
        out = []
        for zone, entries in by.items():
            out += break_messages(zone, entries)
        return out

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        jug = f", {self.skipped_jug} cruche(s) d'eau non relayée(s)" if self.skipped_jug else ""
        if self.sent_jugs:
            jug += f", {self.sent_jugs} cruche(s) d'eau (dont {self.jugs_at_magma} à la dalle)"
        if self.skipped_content:
            jug += f", {self.skipped_content} à contenu précieux non relayée(s)"
        if self.skipped_mass:
            jug += f", {self.skipped_mass} disparue(s) en masse non relayée(s)"
        return f"{self.sent} cassée(s) chez toi envoyée(s){jug}"


class PotApply:
    """Jarres cassées chez les autres : la nôtre (même clé) est cassée par le jeu (--pot-fx casse),
    ou supprimée avec une fumée (fumee) ou sans rien (rien). Chaque décision laisse une ligne
    dans `notes` : retirée, introuvable (avec la raison), suppression refusée."""

    TTL = 15.0           # s : jarre annoncée gardée en attente (acteurs vus au rescan suivant)
    MAX_WAIT = 120.0     # s : … gelée pendant une cinématique, mais jamais plus que MAX_WAIT
    ROOM_SETTLE = 1.0
    MAX_PER_STEP = 4
    BREAK_WAIT = 1.0     # s : jarre armée encore là -> repli (suppression sans effet)
    PAUSE_WAIT = 30.0    # s : … sauf si son minuteur n'a pas bougé (jeu en pause) : on attend
    RESCAN_WAIT = 2.5    # s : sans relecture de la mémoire, le rescan des acteurs (1 s) fait foi
    SNAP_QUIET = 5.0     # s après l'entrée (ou le rechargement) : jarres « déjà cassées » retirées sans effet
    REPLI_TRIES = 3      # suppressions refusées avant de remettre la jarre armée dans son état

    def __init__(self, enabled: bool = True, fx: str = "casse", jugs: bool = False, fees: bool = False):
        self.enabled = enabled
        self.fx = fx if fx in POT_FX else "casse"
        self.jugs = jugs                  # synchro v2 : cruches d'eau cassées à l'impact (break_jug_at)
        self.fees = fees                  # synchro v2 : jarres à fée (la fée arrive par A4)
        self.jugs_native = self.jugs_removed = 0
        self.pending: dict[tuple[str, str], dict] = {}   # (zone, clé) -> {t, t_recv, pos, src, snap, keep, why}
        self.delays: list[float] = []     # latence : réception -> application (s) des dernières casses (mesure)
        self.done: set[tuple[str, str]] = set()          # déjà traitées depuis l'entrée dans la salle
        self.done_zone: str | None = None
        self.breaking: dict[int, dict] = {}              # pid armé -> {t, p, pos, src, t0, tries}
        self.removed = self.native = self.forced = self.expired = self.refused = self.waited = 0
        self.none_t: float | None = None
        self.last_t: float | None = None
        self.notes: list[str] = []
        self._lock = threading.Lock()

    def take_notes(self) -> list[str]:
        with self._lock:
            out, self.notes = self.notes, []
        return out

    def reloaded(self) -> None:
        """Salle rechargée sans changement de zone (chute) : jarres recréées, l'état renvoyé par
        le serveur doit être appliqué de nouveau ; les jarres armées n'existent plus."""
        with self._lock:
            self.done.clear()
            self.breaking.clear()

    def handled(self, zone, keys) -> None:
        """Objets portés (synchro/portage.py) : la marionnette de ces clés a déjà été cassée, retirée ou noyée
        chez nous ; la casse annoncée pour elles (A6) est oubliée sans note « introuvable »."""
        if not isinstance(zone, str):
            return
        with self._lock:
            for k in list(keys or [])[:8]:
                if isinstance(k, str):
                    self.pending.pop((zone, k), None)
                    self.done.add((zone, k))

    def receive(self, zone, entries, now: float, src: str | None = None, snap: bool = False) -> int:
        if not self.enabled or not isinstance(zone, str) or not isinstance(entries, list):
            return 0
        n = 0
        with self._lock:
            for raw in entries[:PotWatch.MAX_PER_MSG * 4]:
                e = valid_break(raw)
                if e is None:
                    continue
                k = (zone, e["key"])
                if k in self.done:
                    continue                  # déjà traitée (renvoi après une coupure, écho)
                if k in self.pending:
                    d = self.pending[k]
                    d.update(t=now, pos=e["pos"])
                    d["snap"] = d["snap"] or bool(snap)    # état de la salle : retirée sans effet
                    d["keep"] = d["keep"] or bool(e.get("keep"))
                    if e.get("jug"):
                        d.update(jug=True, at=e["at"])
                    continue
                n += 1
                self.pending[k] = {"t": now, "t_recv": now, "pos": e["pos"], "src": src or "un autre joueur",
                                   "snap": bool(snap), "keep": bool(e.get("keep")), "why": "pas encore vue"}
                if e.get("jug"):
                    self.pending[k].update(jug=True, at=e["at"])
        return n

    def step(self, now: float, my_zone: str | None, zone_age: float, pots, held: int = 0,
             busy: bool = False, alive=None) -> list[tuple[dict, list]]:
        """Renvoie [(jarre locale, position)] à traiter ; la jarre porte « fx » : casse (à armer),
        fumee, rien ou repli (armée mais toujours là). held : adresse de l'objet porté par Link
        (0 = rien) ; busy : événement ou changement de stage chez nous (jeu pas libre) ;
        alive(p) -> pot_state(...) : relecture d'une jarre armée (None : rescan seulement)."""
        with self._lock:
            return self._step(now, my_zone, zone_age, pots, held, busy, alive)

    def _expire(self, now: float) -> None:
        for k in [k for k, d in self.pending.items()
                  if now - d["t"] > self.TTL or now - d.get("t_recv", d["t"]) > self.MAX_WAIT]:
            d = self.pending.pop(k)
            self.done.add(k)
            if d["snap"] or d.get("far"):
                continue                      # état de la salle (souvent déjà cassée) ; salle lointaine
            self.expired += 1
            self.notes.append(f"jarre cassée chez {d['src']} introuvable chez toi après {self.TTL:.0f} s "
                              f"({d['why']}) : {_short(k[1])}")

    def _zone(self, now: float, my_zone: str | None) -> None:
        """Changement de zone, lecture ratée : ce qu'on oublie (et ce qu'on garde)."""
        if my_zone is None:
            # un échantillon raté ne fait pas oublier les jarres armées (NONE_GRACE comme l'émetteur)
            if self.none_t is None:
                self.none_t = now
            if now - self.none_t > PotWatch.NONE_GRACE:
                self.breaking.clear()         # stage quittée : ces acteurs n'existent plus
            return
        self.none_t = None
        if my_zone == self.done_zone:
            return
        if self.done_zone is not None and self.breaking:
            self.notes.append(f"jarres : {len(self.breaking)} armée(s) plus suivie(s) (changement de salle)")
        self.breaking.clear()                 # salle quittée : leur disparition n'est plus une casse
        old, new = zone_stage(self.done_zone), zone_stage(my_zone)
        if old is not None and old != new:   # autre stage : le serveur a oublié l'ancien (règle J5)
            for k in [k for k in self.pending if zone_stage(k[0]) != new]:
                del self.pending[k]
        self.done_zone = my_zone
        self.done.clear()

    def _step(self, now, my_zone, zone_age, pots, held=0, busy=False, alive=None) -> list[tuple[dict, list]]:
        self._zone(now, my_zone)
        why = ("chargement" if my_zone is None else "salle en cours de chargement" if zone_age < self.ROOM_SETTLE
               else "cinématique ou événement en cours" if busy else None)
        last, self.last_t = self.last_t, now
        if why is not None:
            for d in self.pending.values():
                d["why"] = why
                if why.startswith("cinématique") and last is not None and now > last:
                    d["t"] += now - last      # attente gelée pendant la cinématique (MAX_WAIT au plus)
            self._expire(now)
            return []
        pots = pots or []                     # aucune jarre : les armées se terminent quand même
        out: list[tuple[dict, list]] = []
        listed = {p["pid"] for p in pots}
        for pid, b in list(self.breaking.items()):
            st = alive(b["p"]) if alive is not None else None
            if st is None:                    # pas de relecture : le rescan des acteurs fait foi
                st = (pid in listed if now - b["t"] >= self.RESCAN_WAIT else True, None)
            # spec 5 sans switch : le jeu n'exécute pas la jarre, m800 = 1 ne veut pas dire « pause »
            frozen = st[1] == 1 and pot_spec(b["p"].get("params", 0)) != POT_SPEC_SWITCH
            if not st[0]:
                del self.breaking[pid]
                self.native += 1
                what = "cruche d'eau" if b["p"].get("jug") else "jarre"
                where = " à l'impact" if b["p"].get("jug") else ""
                if b["p"].get("jug"):
                    self.jugs_native += 1
                self.notes.append(f"{what} cassée chez {b['src']} : cassée chez toi aussi par le jeu{where} "
                                  f"({now - b['t0']:.1f} s après réception)")
            elif now - b["t"] >= self.BREAK_WAIT and not (frozen and now - b["t"] < self.PAUSE_WAIT):
                del self.breaking[pid]        # minuteur passé (ou attente trop longue) : repli
                self.forced += 1
                out.append((dict(b["p"], fx="repli", armed=True, tries=b.get("tries", 0)), b["pos"]))
        stage = zone_stage(my_zone)
        by_key: dict[str, list[dict]] = {}
        why_key: dict[str, str] = {}
        skip = set(self.breaking) | {q["pid"] for q, _ in out}
        for p in pots:
            if p["pid"] in skip:
                continue                      # armée : le jeu va la casser (ou repli ci-dessus)
            if not pot_relayable(p.get("proc", 0), p.get("params", 0), self.jugs, self.fees):
                why_key[p["key"]] = "cruche d'eau ou contenu précieux chez toi"
            elif held and p.get("base") == held:
                why_key[p["key"]] = "dans les mains de Link"
            elif p.get("moving"):
                why_key[p["key"]] = "en mouvement (lancée)"
            else:
                by_key.setdefault(p["key"], []).append(p)
        for (zone, key), d in list(self.pending.items()):
            if zone_stage(zone) != stage:
                d["why"] = f"tu étais ailleurs ({my_zone})"
                continue
            if key not in by_key:
                d["far"] = False
                if key in why_key:
                    d["why"] = why_key[key]
                elif zone == my_zone:
                    d["why"] = "absente de ta salle" + self._nearest(key, d["pos"], pots)
                elif not any(f"{stage}:{p.get('home_room')}" == zone for p in pots):
                    d["why"], d["far"] = f"salle {zone} pas chargée chez toi", True
                else:
                    d["why"] = f"absente de la salle {zone}" + self._nearest(key, d["pos"], pots)
                continue
            if len(out) >= self.MAX_PER_STEP:
                break
            del self.pending[(zone, key)]
            self.done.add((zone, key))
            quiet = d["snap"] and zone_age < self.SNAP_QUIET
            local = dict(by_key[key].pop(0), fx="rien" if quiet else self.fx, quiet=quiet,
                         src=d["src"], t0=d["t"], zone=zone, snap=d["snap"], keep=d["keep"],
                         t_recv=d.get("t_recv", d["t"]))
            if d.get("jug"):
                local.update(jug=True, at=d.get("at") or d["pos"])
            out.append((local, d["pos"]))
            if not by_key[key]:
                del by_key[key]
        self._expire(now)
        if len(self.done) > 20000:
            self.done.clear()
        return out

    @staticmethod
    def _nearest(key: str, pos, pots) -> str:
        """Jarre locale de même proc la plus proche de la casse : révèle une clé différente
        (paramètres changés : bit stick effacé, |0xC000 de moveBg ou spec 5)."""
        proc = key.split("/", 1)[0]
        best = None
        for p in pots:
            if str(p.get("proc")) != proc or not p.get("pos"):
                continue
            d = sum((float(u) - float(v)) ** 2 for u, v in zip(p["pos"], pos)) ** 0.5
            if best is None or d < best[0]:
                best = (d, p["key"])
        return f" ; la plus proche chez toi : {_short(best[1])} à {best[0]:.0f}" if best else ""

    def armed(self, p: dict, pos, now: float, tries: int = 0) -> None:
        """break_pot() a armé la jarre `p` : le jeu la casse à l'image suivante."""
        with self._lock:
            if not tries and "t_recv" in p:
                self.delays.append(now - p["t_recv"])     # latence (mesure) : réception -> application
                del self.delays[:-32]
            self.breaking[p["pid"]] = {"t": now, "p": p, "pos": pos, "src": p.get("src", "?"),
                                       "t0": p.get("t0", now), "tries": tries}

    def wait_again(self, p: dict, pos, now: float) -> None:
        """Jarre pas posée (portée, lancée, cachée, qui apparaît) : remise en attente, sans effet
        ni écriture ; réessayée à chaque relevé jusqu'à TTL."""
        with self._lock:
            k = (p.get("zone"), p.get("key"))
            self.waited += 1
            t0 = p.get("t0", now)
            if now - t0 > self.TTL or now - p.get("t_recv", t0) > self.MAX_WAIT:
                self.expired += 1             # reste dans `done` : plus réessayée
                self.notes.append(f"jarre cassée chez {p.get('src', '?')} : pas posée chez toi après "
                                  f"{self.TTL:.0f} s (portée, lancée, cachée ou en apparition), laissée : "
                                  f"{_short(str(k[1]))}")
                return
            self.done.discard(k)
            if k not in self.pending:
                self.pending[k] = {"t": p.get("t0", now), "t_recv": p.get("t_recv", now), "pos": pos,
                                   "src": p.get("src", "?"), "snap": bool(p.get("snap")),
                                   "keep": bool(p.get("keep")),
                                   "why": "pas posée chez toi (portée, lancée, cachée ou en apparition)"}
                if p.get("jug"):
                    self.pending[k].update(jug=True, at=p.get("at") or pos)

    def result(self, ok: bool, p: dict | None = None, how: str = "", now: float | None = None) -> None:
        with self._lock:
            if ok:
                self.removed += 1
                if p is not None:
                    if now is not None and "t_recv" in p and not p.get("armed"):
                        self.delays.append(now - p["t_recv"])     # latence (mesure) : réception -> application
                        del self.delays[:-32]
                    self.notes.append(f"jarre cassée chez {p.get('src', '?')} : retirée chez toi{how}")
            else:
                self.refused += 1
                if p is not None:
                    self.notes.append(f"jarre cassée chez {p.get('src', '?')} : suppression refusée par le jeu "
                                      f"({_short(str(p.get('key', '?')))}, id {p.get('pid')})")

    def _repli_refused(self, mem, p: dict, pos, now: float, on_restored) -> None:
        """Repli (commande 5) refusé pour une jarre armée : réessayé, puis la jarre est remise dans
        son état (itemNo d'origine, minuteur 0) pour qu'elle reste normale chez nous."""
        tries = int(p.get("tries", 0)) + 1
        if tries < self.REPLI_TRIES:
            self.armed(p, pos, now, tries)
            return
        ok = unarm_pot(mem, p)
        if ok and on_restored is not None:
            on_restored(p["pid"])
        self.result(False, p)
        with self._lock:
            self.notes.append(f"jarre armée non supprimée après {tries} essais : "
                              + ("remise dans son état" if ok else "introuvable ou pas relue"))

    HOW = {"repli": " (pas cassée par le jeu en 1 s : supprimée sans effet)", "fumee": " (fumée)", "rien": ""}

    def run(self, sm, mem, now: float, my_zone, zone_age: float, pots, held: int, busy: bool,
            room: int, on_deleted, room_t: float | None = None, on_restored=None,
            magma_near=None, on_jug=None) -> None:
        """Thread de travail du client : step() puis exécution (armer, supprimer, fumée).
        sm : échantillonneur (delete_actor, puff) ; mem : mémoire du jeu (None : pas de casse par
        le jeu, repli direct) ; on_deleted(pid) : notre suppression n'est pas une casse (PotWatch) ;
        room_t : dernier (re)chargement de la salle (chute, Game Over : âge de la salle) ;
        on_restored(pid) : jarre armée remise dans son état (sa casse redevient la nôtre) ;
        cruches (synchro v2) : magma_near(at) -> True si une dalle créée par NOTRE commande 4 est à
        moins de 110 u de l'impact (la cruche est alors retirée sans effet) ; on_jug(at, now) : cruche
        armée à l'impact (la dalle qui va naître chez nous n'est ni publiée ni recréée)."""
        if room_t:
            zone_age = min(zone_age, max(0.0, now - room_t))
        alive = (lambda p: pot_state(mem, p)) if mem is not None else None
        for p, pos in self.step(now, my_zone, zone_age, pots, held, busy, alive):
            fx = p.get("fx", self.fx)
            how = None
            try:
                if fx == "casse" and p.get("proc") != TSUBO_PROC:
                    fx, how = "rien", " (petite pierre : retirée sans effet)"   # pas de casse connue
                elif fx == "casse" and p.get("jug") and self.jugs \
                        and is_water_jug(p.get("proc", 0), p.get("params", 0)):
                    at = p.get("at") or pos
                    if magma_near is not None and magma_near(at):
                        fx, how = "rien", " (cruche : une dalle copiée est déjà là, retirée sans effet)"
                    else:
                        r = break_jug_at(mem, p, at)
                        if r == ARMED:
                            on_deleted(p["pid"])
                            self.armed(p, at, now)
                            if on_jug is not None:
                                on_jug(at, now)
                            continue
                        if r == WAIT:
                            self.wait_again(p, pos, now)
                            continue
                        if r == RACE:
                            on_deleted(p["pid"])
                            with self._lock:
                                self.native += 1
                                self.notes.append(f"cruche cassée chez {p.get('src', '?')} : disparue pendant "
                                                  f"l'écriture (cassée au même instant chez toi ?), id {p['pid']}")
                            continue
                        fx = "repli"
                        with self._lock:
                            self.forced += 1
                elif fx == "casse":
                    r = break_pot(mem, p, keep_item=bool(p.get("keep")))
                    if r == ARMED:
                        on_deleted(p["pid"])        # sa disparition n'est pas une casse chez nous
                        self.armed(p, pos, now)
                        continue
                    if r == WAIT:
                        self.wait_again(p, pos, now)
                        continue
                    if r == RACE:
                        on_deleted(p["pid"])
                        with self._lock:
                            self.native += 1
                            self.notes.append(f"jarre cassée chez {p.get('src', '?')} : disparue pendant "
                                              f"l'écriture (cassée au même instant chez toi ?), id {p['pid']}")
                        continue
                    fx = "repli"
                    with self._lock:
                        self.forced += 1
                ok = sm.delete_actor(p["pid"])
                if ok:
                    on_deleted(p["pid"])
                    if p.get("jug"):
                        with self._lock:
                            self.jugs_removed += 1
                    if fx == "fumee":
                        sm.puff(pos, room)
                elif p.get("armed"):
                    self._repli_refused(mem, p, pos, now, on_restored)
                    continue
                if how is None:
                    how = " (déjà cassée : retirée sans effet)" if p.get("quiet") else self.HOW.get(fx, "")
                    if fx == "repli" and not p.get("armed"):
                        how = " (casse par le jeu impossible : supprimée sans effet)"
                self.result(ok, p, how, now)
            except Exception as exc:          # boîte aux lettres occupée, chargement…
                if p.get("armed"):
                    self._repli_refused(mem, p, pos, now, on_restored)
                    continue
                self.result(False)
                with self._lock:
                    self.notes.append(f"jarre non supprimée : {exc}")

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = f"{self.native} cassée(s) chez toi par le jeu, {self.removed} supprimée(s)"
        s += f" (dont {self.forced} au repli)" if self.forced else ""
        if self.expired:
            s += f", {self.expired} introuvable(s)"
        if self.refused:
            s += f", {self.refused} refusée(s)"
        if self.jugs_native or self.jugs_removed:
            s += f", cruches : {self.jugs_native} cassée(s) à l'impact, {self.jugs_removed} retirée(s)"
        return s + (f", {len(self.pending)} en attente" if self.pending else "")


# ---------------------------------------------------------------------------
# Synchro v2 : dalles de magma figé (Magrock, proc 42), cruches ET flèches de glace
# ---------------------------------------------------------------------------
def _dist(a, b) -> float:
    return math.dist([float(v) for v in a], [float(v) for v in b])


def magma_ready(mem) -> bool:
    """§2.2 : u32 [0x10474B64] != 0 (paquet de magma de la stage) ; sinon le Create du Magrock
    reste en cPhs_INIT jusqu'au déchargement : aucune commande 4 pour le proc 42."""
    if mem is None:
        return False
    try:
        raw = mem.try_read(MAGMA_PACKET, 4)
    except Exception:
        return False
    return raw is not None and struct.unpack(">I", raw)[0] != 0


def parse_magma(raw_fn: bytes | None, raw_timer: bytes | None) -> dict:
    """Champs d'un Magrock lus par CemuSampler._ground : fonction de proc, minuteur, âge."""
    out = {}
    if raw_fn is not None and len(raw_fn) >= 4:
        out["fn"] = struct.unpack(">I", raw_fn[:4])[0]
    if raw_timer is not None and len(raw_timer) >= 8:
        out["timer"], out["age"] = struct.unpack(">ii", raw_timer[:8])
    return out


def recale_magma(mem, p: dict, timer: int) -> bool:
    """--magma-recale [HYPO, étude §3.2 n° 3] : minuteur (+0x750, s32) de la dalle `p` écrit à
    `timer`, seulement si c'est toujours la même dalle et qu'elle est en proc d'attente
    (+0x3FC == 0x0236E800). Une course avec le décrément du jeu coûte au pire une image."""
    if mem is None or p.get("proc", PROC_MAGROCK) != PROC_MAGROCK or not _int(p.get("base")) \
            or not _int(p.get("pid")) or not _int(timer) or not 1 <= timer <= MAGROCK_WAIT_TIMER:
        return False
    base = p["base"]
    try:
        head = mem.try_read(base, 0x10)
        fn = mem.try_read(base + MAGROCK_PROC_FN, 4)
        if head is None or fn is None or not _same_actor(dict(p, proc=PROC_MAGROCK), head) \
                or struct.unpack(">I", fn)[0] != MAGROCK_WAIT_FN:
            return False
        mem.write(base + MAGROCK_TIMER, struct.pack(">i", timer))
    except Exception:
        return False
    return True


class MagmaWatch:
    """§3.2, émetteur : dalles de magma figé NÉES chez nous (cruche ou flèche de glace), publiées
    dans « magma ». Jamais : une dalle placée (params != 0), vue pendant le chargement de la salle,
    créée par notre commande 4 (``mine``), ou « attendue » (née moins de EXPECT_T s après une cruche
    reçue cassée par nous à moins de EXPECT_D u : copie locale d'une dalle déjà annoncée)."""

    LOAD_WINDOW = 2.5
    EXPECT_T = 2.0
    EXPECT_D = 120.0
    MAX_PER_MSG = 16

    def __init__(self, enabled: bool = True, load_window: float | None = None):
        self.enabled = enabled
        self.load_window = self.LOAD_WINDOW if load_window is None else load_window
        self.seen = _Bounded()
        self.mine = _Bounded()
        self.expects: list[tuple[float, list]] = []
        self.zone: str | None = None
        self.zone_t = 0.0
        self.sent = self.skipped_placed = self.skipped_expected = 0

    def created(self, pid: int) -> None:
        self.mine.add(pid)

    def expect(self, at, now: float) -> None:
        """Cruche reçue cassée chez nous à l'impact `at` : la dalle qui va naître est une copie."""
        self.expects = [x for x in self.expects if now - x[0] <= self.EXPECT_T] + [(now, list(at))]

    def observe(self, now: float, zone: str | None, room: int, magmas, room_t: float | None = None) -> list[dict]:
        """magmas : [{pid, pos, room, params, age?}] ; renvoie les entrées {nid, pos, room, age}."""
        if not self.enabled:
            return []
        if zone != self.zone:
            self.zone, self.zone_t = zone, now
        if room_t is not None and room_t > self.zone_t:
            self.zone_t = room_t
        if zone is None or magmas is None:
            return []
        loading = now - self.zone_t < self.load_window
        self.expects = [x for x in self.expects if now - x[0] <= self.EXPECT_T]
        out = []
        for m in magmas:
            pid = m.get("pid")
            if pid in self.seen:
                continue
            self.seen.add(pid)
            if pid in self.mine or loading:
                continue
            if int(m.get("params", 0)) != 0:
                self.skipped_placed += 1      # dalle placée dans la stage (params >> 24 != 0)
                continue
            pos = m.get("pos")
            if not pos or not all(math.isfinite(float(v)) for v in pos):
                continue
            if any(_dist(at, pos) <= self.EXPECT_D for _t, at in self.expects):
                self.skipped_expected += 1
                continue
            r = m.get("room", room)
            age = m.get("age", 0)
            out.append({"nid": int(pid) & 0xFFFFFFFF, "pos": [round(float(v), 1) for v in pos],
                        "room": r if _int(r) and 0 <= r < 64 else room,
                        "age": age if _int(age) and 0 <= age < 1 << 20 else 0})
        out = out[:self.MAX_PER_MSG]
        self.sent += len(out)
        return out

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = f"{self.sent} dalle(s) née(s) chez toi envoyée(s)"
        if self.skipped_expected:
            s += f", {self.skipped_expected} copie(s) locale(s) d'une cruche reçue"
        return s


class MagmaApply:
    """§3.2, receveur : une dalle annoncée par un autre joueur existe chez nous aussi.
      1. une dalle locale née il y a moins de ASSOC_AGE images (pas une des nôtres) à moins de
         ASSOC_D u est associée : rien à créer (cas normal après une cruche cassée à l'impact) ;
      2. sinon, après WAIT s (une cruche relayée peut encore la produire ; plus longtemps si une
         cruche reçue près de là est attendue), création par la commande 4 (proc 42, params 0),
         seulement si le paquet de magma de la stage existe (``ready``) ;
      3. --magma-recale (option, [HYPO]) : minuteur recalé quand notre copie est en attente ;
      4. rien pendant un événement ni un changement de salle ; abandon après TTL s, ou si la dalle
         est trop vieille pour servir (moins de MIN_LEFT images à vivre)."""

    TTL = 8.0
    WAIT = 1.0
    ASSOC_AGE = 90                    # images (3 s)
    ASSOC_D = 110.0                   # u : la spirale WPOT cherche jusqu'à 100 u
    ROOM_SETTLE = 1.0
    MAX_PER_STEP = 4
    MAX_PENDING = 64
    MINE_KEEP = MAGROCK_LIFE / 30.0 + 1.0     # s : nos copies (commande 4) vivent ~15,5 s
    MIN_LEFT = 45                     # images : en dessous, la dalle fond déjà : pas de copie
    FRESH = 2.0                       # s : copie créée par nous, pas encore vue au relevé
    RECALE_MIN = 45                   # images de retard au-delà desquelles on recale

    def __init__(self, enabled: bool = True, recale: bool = False):
        self.enabled = enabled
        self.recale = recale
        self.pending: dict[tuple, dict] = {}       # (émetteur, nid) -> {t, zone, e, snap, src}
        self.done: set[tuple] = set()
        self.expects: list[tuple[float, list]] = []    # (jusqu'à, position) : cruche attendue
        self.mine: dict[int, tuple[float, list]] = {}  # pid créé par nous -> (heure, position)
        self.linked = _Bounded()                   # dalles locales déjà associées
        self.recales: dict[int, int] = {}          # pid -> retard (images) à rattraper
        self.present: set | None = None            # dalles (pid) du dernier relevé (None : pas encore lu)
        self.notes: list[str] = []
        self.why = ""
        self._lock = threading.Lock()
        self.created_n = self.associated = self.expired = self.failed = self.recaled = 0

    def take_notes(self) -> list[str]:
        with self._lock:
            out, self.notes = self.notes, []
        return out

    def receive(self, sender, zone, entries, now: float, src: str | None = None, snap: bool = False) -> int:
        if not self.enabled or not isinstance(zone, str) or not isinstance(entries, list):
            return 0
        n = 0
        with self._lock:
            for raw in entries[:MagmaWatch.MAX_PER_MSG]:
                e = valid_magma(raw)
                if e is None:
                    continue
                key = (sender, e["nid"])
                # « snap » (entrée dans la zone, ou salle rechargée : chute dans la lave, Game Over) :
                # nos copies d'avant n'existent plus, la dalle encore vivante chez l'autre est à refaire
                if key in self.pending or (key in self.done and not snap):
                    continue
                if len(self.pending) >= self.MAX_PENDING:
                    break
                self.done.discard(key)
                self.pending[key] = {"t": now, "zone": zone, "e": e, "snap": bool(snap),
                                     "src": src or "un autre joueur"}
                n += 1
        return n

    def expect(self, at, now: float, hold: float = 3.0) -> None:
        """Une cruche (reçue) va peut-être casser à `at` : pas de création avant `hold` s."""
        with self._lock:
            self.expects = [x for x in self.expects if x[0] > now] + [(now + hold, list(at))]

    def near_mine(self, at, now: float) -> bool:
        """Une dalle créée par NOTRE commande 4 est-elle encore là, à moins de ASSOC_D u de `at` ?
        Encore là = vue au dernier relevé (``present``), ou créée il y a moins de FRESH s (pas encore
        relevée) : une copie disparue avec la salle (chute, Game Over) ne fait plus retirer de cruche."""
        with self._lock:
            return any(now - t <= self.MINE_KEEP and _dist(pos, at) <= self.ASSOC_D
                       and (self.present is None or pid in self.present or now - t <= self.FRESH)
                       for pid, (t, pos) in self.mine.items())

    def see(self, magmas, now: float) -> None:
        """Relevé des dalles (même sans rien en attente) : nos copies encore là (near_mine)."""
        with self._lock:
            self._see(magmas, now)

    def _see(self, magmas, now: float) -> None:
        if magmas is not None:
            self.present = {m.get("pid") for m in magmas}
            # copie disparue (salle rechargée, fondue) : plus « à nous »
            for pid in [p for p, (t, _pos) in self.mine.items() if p not in self.present and now - t > self.FRESH]:
                del self.mine[pid]
        for pid in [p for p, (t, _pos) in self.mine.items() if now - t > self.MINE_KEEP]:
            del self.mine[pid]

    def step(self, now: float, my_zone: str | None, zone_age: float, busy: bool, magmas,
             ready: bool) -> list[tuple]:
        """Renvoie les tâches : ("creer", clé, entrée) ; ("recaler", dalle, minuteur)."""
        with self._lock:
            return self._step(now, my_zone, zone_age, busy, magmas, ready)

    def _left(self, d: dict, now: float) -> float:
        """Images de vie qui restent à la dalle annoncée (âge + latence)."""
        return MAGROCK_LIFE - (d["e"]["age"] + (now - d["t"]) * 30.0)

    def _step(self, now, my_zone, zone_age, busy, magmas, ready) -> list[tuple]:
        for key in [k for k, d in self.pending.items() if now - d["t"] > self.TTL]:
            d = self.pending.pop(key)
            self.done.add(key)
            self.expired += 1
            if d["zone"] == my_zone and not d["snap"]:
                self.notes.append(f"magma : dalle de {d['src']} pas copiée chez toi en {self.TTL:.0f} s "
                                  f"({self.why or 'pas dans ta salle'})")
        self._see(magmas, now)
        self.expects = [x for x in self.expects if x[0] > now]
        if my_zone is None or busy or zone_age < self.ROOM_SETTLE:
            self.why = "chargement" if my_zone is None else ("cinématique ou événement en cours" if busy
                                                              else "salle en cours de chargement")
            return []
        self.why = ""
        local = [m for m in (magmas or []) if m.get("pid") not in self.mine and m.get("pid") not in self.linked
                 and int(m.get("params", 0)) == 0 and m.get("pos")]
        out: list[tuple] = []
        for key, d in list(self.pending.items()):
            if d["zone"] != my_zone:
                continue
            e = d["e"]
            if self._left(d, now) < self.MIN_LEFT:
                del self.pending[key]
                self.done.add(key)
                continue                      # elle fond déjà chez l'autre
            near = [m for m in local if _dist(m["pos"], e["pos"]) <= self.ASSOC_D
                    and (not _int(m.get("age")) or m["age"] < self.ASSOC_AGE)]
            if not near and d["snap"]:
                # renvoi « snap » alors que notre copie d'avant est toujours chargée (salle voisine
                # restée en mémoire) : pas de deuxième dalle par-dessus
                near = [m for m in (magmas or []) if m.get("pid") in self.mine and m.get("pid") not in self.linked
                        and m.get("pos") and _dist(m["pos"], e["pos"]) <= self.ASSOC_D]
            if near:
                m = min(near, key=lambda m: _dist(m["pos"], e["pos"]))
                self.linked.add(m["pid"])
                if m in local:
                    local.remove(m)
                del self.pending[key]
                self.done.add(key)
                self.associated += 1
                continue
            hold = d["t"] + (0.0 if d["snap"] else self.WAIT)
            for until, at in self.expects:
                if _dist(at, e["pos"]) <= self.ASSOC_D:
                    hold = max(hold, until)
            if now < hold:
                continue
            if not ready:
                self.why = "pas de magma dans cette stage (paquet absent)"
                continue
            if len(out) >= self.MAX_PER_STEP:
                break
            del self.pending[key]
            self.done.add(key)
            out.append(("creer", key, dict(e, delay=int(e["age"] + (now - d["t"]) * 30.0), src=d["src"])))
        if self.recale:
            out += self._recale_jobs(magmas)
        if len(self.done) > 20000:
            self.done.clear()
        return out

    def _recale_jobs(self, magmas) -> list[tuple]:
        jobs = []
        by = {m.get("pid"): m for m in (magmas or [])}
        for pid, delay in list(self.recales.items()):
            m = by.get(pid)
            if m is None:
                continue
            if m.get("fn") != MAGROCK_WAIT_FN or not _int(m.get("age")):
                continue                      # pas encore en attente (apparition) : plus tard
            del self.recales[pid]
            # minuteur local = 300 - (âge - 30) ; la dalle de l'autre a `delay` images d'avance
            target = MAGROCK_WAIT_TIMER - max(0, m["age"] - MAGROCK_APPEAR) - delay
            jobs.append(("recaler", m, max(MAGROCK_APPEAR, target)))
        return jobs

    def created(self, key, e: dict, pid: int | None, now: float) -> None:
        with self._lock:
            if pid is None:
                self.failed += 1
                self.notes.append(f"magma : dalle de {e.get('src', '?')} non créée (boîte aux lettres)")
                return
            self.created_n += 1
            self.mine[pid] = (now, list(e["pos"]))
            if self.recale and e.get("delay", 0) > self.RECALE_MIN:
                self.recales[pid] = int(e["delay"])
            self.notes.append(f"magma : dalle de {e.get('src', '?')} créée chez toi (commande 4)")

    def recaled(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.recaled += 1

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = f"{self.associated} dalle(s) déjà là (cruche), {self.created_n} créée(s)"
        if self.recaled:
            s += f", {self.recaled} recalée(s)"
        if self.expired:
            s += f", {self.expired} abandonnée(s)"
        if self.failed:
            s += f", {self.failed} échec(s)"
        if self.pending:
            s += f", {len(self.pending)} en attente" + (f" ({self.why})" if self.why else "")
        return s
