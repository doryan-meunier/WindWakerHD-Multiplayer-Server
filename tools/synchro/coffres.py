"""Synchro v2, module 2 : coffres partagés (--share-chests, activé par --coop-donjon, --no-chests).

Étude : docs/etudes/sync2_coffres.md (la section « Vérification » §9 prime sur le texte d'avant).
Ce module couvre les étapes 1 et 2 de l'étude (pack v17, commandes 1-6 seulement) :

* Détection (``ChestWatch``, lecture seule, ~5 Hz) : bits ``mTbox`` des 16 ``mSave.mMemory[i]``
  (copie vivante pour la stage chargée) et bit 4 (réceptacle du boss) de ``mDungeonItem``.
  **Transitions seulement, avec base silencieuse** (vérificateur §9.2 n° 8) : rien n'est rapporté à
  la première lecture stable après un changement de contexte (sauvegarde, stage, index de
  sauvegarde), après un « rejoindre » (A15) ou un chargement ; un bit doit être lu à 1 deux fois de
  suite ; plus de 8 bits passés de 0 à 1 (ou effacés) d'un coup = chargement ou copie de
  sauvegarde : nouvelle base, aucun événement. Les bits posés par nous (``applied``) ne sont jamais
  rapportés (anti-écho).
* Objet du coffre : cache des coffres chargés (``ChestCache``, proc 292, objet = ``home.angle.z >> 8``
  à +0x2FC), contrôle croisé avec ``mEvtCtrl.mGetItemNo`` ; sans cache, ``mGetItemNo`` seulement si
  un événement a eu lieu dans les 5 dernières secondes, marqué « probable » ; sinon ``None``.
* Canal serveur « coffre » (``wwmp_server.handle_coffre``) : premier arrivé, dédoublonnage par
  ``(k, i, n)``, ``full`` à l'inscription, plafond de débit, remis à zéro par « rejoindre ».
* Application chez les autres (``ChestGrant``) : la sauvegarde de chacun fait foi (bit déjà à 1 =
  déjà reçu). **« Bit seul » uniquement quand l'effet de l'objet est reproduit autrement**
  (vérificateur §9.2 n° 1) : carte, boussole, grande clé (bit ``mDungeonItem`` 0/1/2 posé avec),
  coffre vide confirmé (0xFF), petite clé quand le compteur commun est actif (synchro/cles.py : la clé
  elle-même passe par ce module-là, contrat §3.7), niveau local déjà atteint (bourse, sac, carquois).
  Jamais de bit seul pour un objet inconnu, « probable » ou hors liste : ce serait une perte d'objet.
  Hors des donjons (couches de stage différentes possibles), le bit seul attend en plus que le
  coffre de ce joueur confirme le même objet.
* Inventaire partagé (module 3, synchro/inventaire.py, contrat « propriétaire unique » R19) : un objet
  d'inventaire (Grappin, arc, épée, bouclier…) n'arrive QUE par l'inventaire ; quand l'auteur le
  partage (drapeau ``iv`` du rapport) le coffre attend (« arrive par l'inventaire partagé ») puis pose
  le bit SEUL dès que l'objet est chez ce joueur (``ChestGrant.owned``) : jamais de GRANT pour lui
  (une seule voie d'attribution). Flacon vide : bit seul (nombre de flacons partagé). Objets à
  compteur (rubis, quarts de cœur, munitions) : inchangé, pack v19.
* Objets d'inventaire (Grappin, quarts de cœur, flacons…) : il faut le pack v19 (commande CALL 8
  ``GRANT`` : bit + ``execItemGet`` atomiques sur le fil du jeu). Aucun module « inventaire » n'existe
  encore : ils entrent dans la file « à donner » (``GrantQueue``), consommée par ``ChestGrant`` dès
  qu'un ``granter`` est branché (point d'accroche v19, ``granter(kind, idx, bit, item, count,
  need_stage) -> int``) ; en v17 (``granter = None``) le joueur lit « X a ouvert un coffre : Grappin
  (tu le recevras avec le pack v19) » et peut toujours ouvrir son propre coffre (le bit n'est PAS
  posé : rien n'est perdu).
* Coffre affiché chez l'autre (``ChestRefresh``, étape 2) : un coffre chargé dont le bit est posé
  mais encore « fermé » est RECRÉÉ (commande 4 avec l'angle complet, ``mailbox.create(rot_x=…)``) :
  ``CreateInit`` voit ``checkOpen()`` vrai et le coffre naît ouvert ; l'ancien est supprimé
  (commande 5) seulement quand le nouveau est vu en ``actionWait``. Mécanique des remplaçantes d'A14
  (synchro/obstacles.py) : la remplaçante naît dans la couche de Link, elle est suivie (``created``)
  et retirée quand sa salle est déchargée ou quand le jeu a recréé le coffre de la salle (jumelle) ;
  jamais de deuxième remplaçante. Repli « figer » (``+0x6D8 = actionWait``) **seulement si Link est
  à 100 unités ou plus** (vérificateur §9.2 n° 3 : risque de blocage au contact) ; recréation
  seulement au-delà de 150 unités ; au contact, on attend et on prévient « ne rouvre pas ce coffre ».

* Coffre qui APPARAÎT (tours de guet en mer : tkrAOs funcType 8 ; coffres « tuer tous les ennemis ») — bug vu en
  jeu le 2026-11-13 : le coffre de la tour, ouvert par un joueur, s'ouvrait encore chez l'autre, qui pouvait
  recevoir l'objet DEUX fois (GRANT, puis son propre coffre). Ce que dit le jeu [HD-ASM, test_coffres.py
  TestRpxApparition] : l'état « ouvert » d'un coffre funcType 7/8 est le bit tbox ``no`` de mSave.mMemory[1]
  (checkOpen 0x024B4AB4 -> dComIfGs_isSaveTbox(1, no) 0x02520864 ; posé à l'ouverture par 0x025207D8) : sauvegardé,
  jamais effacé au changement de salle (contrairement au switch 0xE7 qui le fait APPARAÎTRE, switch de salle). Ce bit
  n'est lu qu'à la CRÉATION (CreateInit 0x024B5D74 : ouvert -> actionWait, visible et ouvert ; checkOpen n'a que
  quatre appelants, aucun dans les actions). Un coffre déjà créé qui attend son switch (actionSwOnWait 0x024B7514,
  caché : +0x788 |= 3, le dessin saute si bit 0) ou la mort des ennemis (actionGenocide 0x024B75EC) commande son
  apparition (orderEvent, +0xFA |= 2), joue actionDemo2 puis passe en actionOpenWait (0x024B783C) SANS relire le bit :
  il apparaît FERMÉ et ouvrable même si le bit a été posé entre-temps ; actionOpenWait donne l'objet quel que soit le
  bit. Le GRANT (ou le bit seul) n'était bloqué que par un coffre affiché en actionOpenWait : un coffre caché qui
  attendait encore son switch le laissait passer -> objet en double à l'apparition.
  Maintenant : (1) ``pre_grant`` (GRANT ET bit seul) n'accepte que si chaque coffre chargé de ce numéro est en
  actionWait (ouvert, ou figé par nous) : caché, en apparition, en ouverture ou ouvrable -> attente ; (2) un coffre
  « à appliquer » (enregistrement reçu, BIT ou GRANT) ou périmé (bit posé) encore CACHÉ est figé caché
  (``freeze_hidden`` : +0x6D8 = actionWait) à toute distance — il ne s'affiche plus jamais dans cette visite, ne
  propose rien — seulement si aucune apparition n'est commandée (+0xF8 = 0, +0xFA bit 1 à 0, son switch pas posé ;
  compte à rebours de actionGenocide intact) ; puis le GRANT, puis (Link à plus de 150) la recréation : il naît
  OUVERT (checkOpen) ; à la salle suivante aussi ; (3) un coffre ouvrable affiché « à appliquer » est figé à 100 ou
  plus comme avant (le figer AVANT le GRANT manquait : le GRANT n'arrivait jamais tant que le coffre restait chargé) ;
  au contact (moins de 100), ``contact`` est vrai : le client tient le drapeau « coffres fermés » du pack (bit 19,
  wwmp_hcof, boxCheck répond « trop loin » : ni « Ouvrir » ni ouverture) tant qu'il l'est : le coffre n'est plus
  ouvrable, et l'objet arrive dès que Link s'éloigne un peu. Le serveur refuse déjà un second rapport (doublon
  journalisé, rien relayé) ; un arrivant tardif ou un client reconnecté reçoit le registre (« full ») et passe par
  les mêmes règles.

Règle transversale (synchro/cles.py, R19) : jamais ``execItemGet(0x15)`` ici (``assert_not_small_key``).
Hors périmètre (phase 2 de l'étude) : repêchage ``mOcean`` (le protocole accepte déjà le genre
``o`` ; le GRANT v19 sait poser le bit, genre 2). Pack v19 (lot 2) : ``granter`` = ``Mailbox.grant`` et
``ChestGrant.remove_ground`` (réceptacle encore au sol retiré après son GRANT), branchés par le client
seulement si la boîte installée est en v19.
"""
from __future__ import annotations

import math
import struct
import threading
import time
from dataclasses import dataclass, field

from jeu import progress as prog
from synchro import cles
from synchro.obstacles import game_free, room_loaded
from commun.protocole import enemy_key

# ---------------------------------------------------------------------------
# daTbox_c (proc 292 « takara ») sur HD [HD-ASM, sync2_coffres.md §2.1 et §9.1]
# ---------------------------------------------------------------------------
PROC_TBOX = cles.PROC_TBOX         # 292
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_COMMAND = 0xF8                 # u16 eventInfo.mCommand (3 = « porte » : ouverture demandée)
OFF_HOME = 0x2EC                   # home.pos f32[3], home.angle s16[3] (+0xC), home.roomNo s8 (+0x12)
OFF_CUR_POS = 0x314                # current.pos f32[3]
OFF_ROOM_NO = 0x3AC                # s32 mRoomNo = home.angle.x & 0x3F (daTbox_Create 0x024B62C8)
OFF_ACTION = 0x6D4                 # PTMF mActionFunc : s16 0, s16 -1, u32 adresse (+0x6D8)
OFF_ACTION_FN = 0x6D8
OFF_FLAGS = 0x788                  # u16 mFlags ; OPENING = 0x10
FLAG_OPENING = 0x10
FLAG_HIDDEN = 0x01                 # pas dessiné (draw 0x024B50CC) : posé (|= 3) par CreateInit pour actionSwOnWait et
                                   # actionGenocide (0x024B60A4, 0x024B6080), ôté à l'apparition (0x024B70B4)
OFF_COND = 0xFA                    # u16 eventInfo : bit 1 posé par orderEvent (apparition commandée, 0x024B75CC)
COND_ORDERED = 0x02
OFF_GENO_COUNT = 0x790             # u8 compte à rebours de actionGenocide (0x41 à la création, 0x024B6068)
GENO_COUNT_INIT = 0x41
PTMF_HEAD = 0x0000FFFF
_READ = OFF_GENO_COUNT + 1         # une lecture par coffre

ACT_WAIT = 0x024B79AC              # actionWait (ouvert : li r3,1 ; blr)
ACT_OPEN_WAIT = 0x024B7394         # actionOpenWait (fermé, ouvrable)
ACT_DEMO = 0x024B7708              # actionDemo (ouverture en cours)
ACT_SW_ON_WAIT = 0x024B7514        # actionSwOnWait (attend son switch, apparition avec caméra)
ACT_SW_ON_WAIT2 = 0x024B6334       # actionSwOnWait2 (attend son switch, sans démo)
ACT_GENOCIDE = 0x024B75EC          # actionGenocide (attend la mort des ennemis)
ACT_DEMO2 = 0x024B7804             # actionDemo2 (caméra d'apparition : JAMAIS touché)
ACTIONS = {ACT_WAIT: "ouvert", ACT_OPEN_WAIT: "fermé", ACT_DEMO: "ouverture", ACT_SW_ON_WAIT: "attend switch",
           ACT_SW_ON_WAIT2: "attend switch", ACT_GENOCIDE: "attend ennemis", ACT_DEMO2: "apparition"}
CLOSED = frozenset({ACT_OPEN_WAIT, ACT_SW_ON_WAIT, ACT_SW_ON_WAIT2, ACT_GENOCIDE})
HIDDEN_WAIT = frozenset({ACT_SW_ON_WAIT, ACT_GENOCIDE})   # caché, apparaîtra FERMÉ (actionDemo2 -> actionOpenWait)

FUNC_EXTRA = frozenset({7, 8})     # funcType EXTRA_SAVE_INFO(_SPAWN) : bit dans mMemory[1] (STAGE_SEA2)
SEA2_IDX = 1
MEM_TBOX, MEM_DUNGEON = 0x00, 0x21
DUN_MAP, DUN_COMPASS, DUN_BOSS_KEY, DUN_HEART = 0, 1, 2, 4
ADDR_GET_ITEM_NO = cles.ADDR_GET_ITEM_NO   # u8 mEvtCtrl.mGetItemNo (0x10474354), jamais remis à 0

# Niveaux « progressifs » [HD-ASM gi+0x32 / 0x8F / 0x90 ; rôle num / max HYPO, étude §2.5]
GI_WALLET, GI_ARROW_MAX, GI_BOMB_MAX = 0x32, 0x8F, 0x90
UPGRADES = {0xAB: ("wallet", 1), 0xAC: ("wallet", 2), 0xAD: ("bomb_max", 60), 0xAE: ("bomb_max", 99),
            0xAF: ("arrow_max", 60), 0xB0: ("arrow_max", 99)}

ITEM_NONE = 0xFF
ITEM_HEART = 0x08
ITEM_BOTTLE = 0x50
DUNGEON_ITEMS = {0x4C: DUN_MAP, 0x4D: DUN_COMPASS, 0x4E: DUN_BOSS_KEY}
# Objets que le GRANT v19 pourra donner (table §3.4 corrigée par le vérificateur). 0x15 jamais (R19).
GRANT_ITEMS = frozenset(set(range(0x01, 0x13)) | {0x1E} | (set(range(0x20, 0x37)) - {0x2E}) | {0x42, 0x43}
                        | set(range(0x45, 0x4C)) | set(range(0x50, 0x5A)) | set(range(0x61, 0x69))
                        | set(range(0xC2, 0xFF)))
# rubis, bombes, flèches : comptés par les compteurs communs (wwmp/compteurs.VALEURS, vérifié par les tests)
COUNTER_ITEMS = frozenset(set(range(0x01, 0x07)) | {0x0F, 0x1A} | set(range(0x0B, 0x0F)) | {0x10, 0x11, 0x12})
NEED_STAGE_ITEMS = frozenset({ITEM_HEART})   # lié à la stage chargée (onDungeonItem(gi+0x798, 4))
KIND_CODE = {"t": 0, "d": 1, "o": 2}         # « kind » du GRANT v19 (§3.8)

BIT, GRANT, WAIT, NO = "bit", "donner", "attente", "non"

ITEM_NAMES_FR = {
    0x01: "Rubis vert", 0x02: "Rubis bleu", 0x03: "Rubis jaune", 0x04: "Rubis rouge", 0x05: "Rubis violet",
    0x06: "Rubis orange", 0x07: "Quart de cœur", 0x08: "Réceptacle de cœur", 0x09: "Petite fiole de magie",
    0x0A: "Grande fiole de magie", 0x0B: "5 bombes", 0x0C: "10 bombes", 0x0D: "20 bombes", 0x0E: "30 bombes",
    0x0F: "Rubis argenté", 0x10: "10 flèches", 0x11: "20 flèches", 0x12: "30 flèches", 0x15: "Petite clé",
    0x16: "Fée", 0x1E: "Trois cœurs", 0x1F: "Pendentif de joie", 0x20: "Longue-vue", 0x21: "Tingle-tuner",
    0x22: "Baguette du vent", 0x23: "Boîte à images", 0x24: "Sac à butin", 0x25: "Grappin",
    0x26: "Boîte à images couleur", 0x27: "Arc", 0x28: "Bracelets de force", 0x29: "Bottes de plomb",
    0x2A: "Armure magique", 0x2C: "Sac à appâts", 0x2D: "Boomerang", 0x2F: "Grappin-griffe",
    0x30: "Sac de livraison", 0x31: "Sac à bombes", 0x33: "Masse", 0x34: "Feuille Mojo",
    0x35: "Flèches de feu et de glace", 0x36: "Flèches de lumière", 0x42: "Pierre des pirates",
    0x43: "Pendentif du héros", 0x45: "Collier de crâne", 0x46: "Graine de Mojo", 0x47: "Plume dorée",
    0x48: "Blason de chevalier", 0x49: "Gelée rouge", 0x4A: "Gelée verte", 0x4B: "Gelée bleue",
    0x4C: "Carte du donjon", 0x4D: "Boussole", 0x4E: "Grande clé", 0x50: "Flacon vide",
    0x51: "Potion rouge", 0x52: "Potion verte", 0x53: "Potion bleue", 0x54: "Demi-soupe", 0x55: "Soupe",
    0x56: "Flacon d'eau", 0x57: "Fée en flacon", 0x58: "Luciole en flacon", 0x59: "Eau de la forêt",
    0xAB: "Bourse (1000)", 0xAC: "Bourse (5000)", 0xAD: "Sac à bombes (60)", 0xAE: "Sac à bombes (99)",
    0xAF: "Carquois (60)", 0xB0: "Carquois (99)", 0xB2: "Magie doublée", ITEM_NONE: "rien (coffre vide)",
}
for _k in range(8):
    ITEM_NAMES_FR[0x61 + _k] = f"Fragment de Triforce {_k + 1}"
for _k in range(0xC2, 0xFF):
    ITEM_NAMES_FR.setdefault(_k, "Carte au trésor")


def item_name(item) -> str:
    if item is None:
        return "objet inconnu"
    return ITEM_NAMES_FR.get(item, f"objet 0x{item:02X}")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


# ---------------------------------------------------------------------------
# Paramètres d'un coffre [DECOMP d_a_tbox.h ; HD-ASM getFuncType 0x024B435C, tboxNo 0x024B6410]
# ---------------------------------------------------------------------------
def func_type(params: int) -> int:
    return params & 0x7F


def tbox_no(params: int) -> int:
    return (params >> 7) & 0x1F


def item_of(angle_z: int) -> int:
    """Objet du coffre : (home.angle.z >> 8) & 0xFF (actionOpenWait 0x024B73D0)."""
    return (angle_z >> 8) & 0xFF


def open_switch(angle_z: int) -> int:
    """Switch posé à l'ouverture (0xFF = aucun ; OpenInit_com 0x024B63F4)."""
    return angle_z & 0xFF


def save_idx(params: int, cur_idx) -> int | None:
    """Index de sauvegarde du bit du coffre : 1 (STAGE_SEA2) si funcType 7/8, sinon la stage chargée."""
    if func_type(params) in FUNC_EXTRA:
        return SEA2_IDX
    return cur_idx if _int(cur_idx) and 0 <= cur_idx < prog.N_MEM else None


def read_tbox(mem, base: int) -> dict | None:
    """Relit un coffre (une lecture) : None si l'acteur n'existe plus ou n'est pas un coffre."""
    if not _int(base) or not 0x10000000 <= base < 0x50000000:
        return None
    raw = mem.try_read(base, _READ)
    if raw is None or len(raw) < _READ:
        return None
    bs_type, pid = struct.unpack_from(">II", raw, 0)
    proc, = struct.unpack_from(">h", raw, OFF_PROC)
    if bs_type == 0 or proc != PROC_TBOX:
        return None
    angle = struct.unpack_from(">3h", raw, OFF_HOME + 0xC)
    return {"base": base, "pid": pid, "proc": proc,
            "params": struct.unpack_from(">I", raw, OFF_PARAMS)[0],
            "cmd": struct.unpack_from(">H", raw, OFF_COMMAND)[0],
            "home": struct.unpack_from(">3f", raw, OFF_HOME),
            "angle": angle,
            "room": struct.unpack_from(">b", raw, OFF_HOME + 0x12)[0],
            "room_no": struct.unpack_from(">i", raw, OFF_ROOM_NO)[0],
            "head": struct.unpack_from(">I", raw, OFF_ACTION)[0],
            "action": struct.unpack_from(">I", raw, OFF_ACTION_FN)[0],
            "flags": struct.unpack_from(">H", raw, OFF_FLAGS)[0],
            "cond": struct.unpack_from(">H", raw, OFF_COND)[0], "geno": raw[OFF_GENO_COUNT],
            "item": item_of(angle[2] & 0xFFFF), "no": tbox_no(struct.unpack_from(">I", raw, OFF_PARAMS)[0])}


def consistent(o: dict) -> bool:
    """Vérification croisée de la disposition HD (sinon jamais touché) : mRoomNo recopié de
    home.angle.x, PTMF d'action (0, -1, adresse connue de daTbox)."""
    return (o["room_no"] == o["angle"][0] & 0x3F and o["head"] == PTMF_HEAD and o["action"] in ACTIONS
            and all(math.isfinite(v) and abs(v) < 1e6 for v in o["home"]))


def chest_key(o: dict) -> str:
    return enemy_key(PROC_TBOX, o["params"], o["home"])


def link_xz_dist(mem, home) -> float | None:
    """Distance XZ de Link (relue à l'instant) à `home` ; None si illisible."""
    raw = mem.try_read(prog.SYM_PLAYER, 4)
    if raw is None:
        return None
    lb = struct.unpack(">I", raw)[0]
    if not 0x10000000 <= lb < 0x50000000:
        return None
    p = mem.try_read(lb + OFF_CUR_POS, 12)
    if p is None:
        return None
    x, _y, z = struct.unpack(">3f", p)
    if not (math.isfinite(x) and math.isfinite(z)):
        return None
    return math.hypot(x - home[0], z - home[2])


# ---------------------------------------------------------------------------
# Lecture des bits (une lecture de gi+0x3A0 à gi+0x7BC)
# ---------------------------------------------------------------------------
@dataclass
class ChestView:
    ok: bool
    reason: str = ""
    gi: int = 0
    stage: str = ""
    room: int = -1
    cur_idx: int | None = None
    tbox: set = field(default_factory=set)        # {(i, n)}
    dungeon: set = field(default_factory=set)     # {(i, bit)}
    busy: str = ""                                # "" = jeu libre (lecture brute)
    get_item: int = ITEM_NONE                     # mEvtCtrl.mGetItemNo
    levels: dict = field(default_factory=dict)    # niveaux progressifs locaux

    @property
    def context(self) -> tuple:
        return (self.gi, self.stage, self.cur_idx)

    def keys(self) -> set:
        """Clés d'événements : ("t", i, n) et ("d", i, 4)."""
        return {("t", i, n) for i, n in self.tbox} | {("d", i, b) for i, b in self.dungeon if b == DUN_HEART}

    def has(self, key) -> bool:
        k, i, n = key
        if k == "t":
            return (i, n) in self.tbox
        if k == "d":
            return (i, n) in self.dungeon
        return False


def read_chest_bits(mem) -> ChestView:
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return ChestView(False, "gameInfo illisible")
    gi = struct.unpack(">I", raw)[0]
    if not 0x10000000 <= gi < 0x50000000:
        return ChestView(False, "pas de sauvegarde chargée")
    st = mem.try_read(prog.SYM_CUR_STAGE, 12)
    if st is None:
        return ChestView(False, "stage illisible")
    stage = st[:8].split(b"\0", 1)[0].decode("ascii", "replace")
    room = struct.unpack_from(">b", st, 10)[0]
    if stage in prog.TITLE_STAGES:
        return ChestView(False, f"écran titre ({stage or 'vide'})", gi, stage)
    blob = mem.try_read(gi + prog.GI_MEMORY, prog.GI_DAN + 1 - prog.GI_MEMORY)
    player = mem.try_read(prog.SYM_PLAYER, 4)
    if blob is None or player is None:
        return ChestView(False, "sauvegarde illisible", gi, stage)
    if struct.unpack(">I", player)[0] == 0:
        return ChestView(False, "Link absent (chargement)", gi, stage)

    def at(off: int, n: int) -> bytes:
        return blob[off - prog.GI_MEMORY: off - prog.GI_MEMORY + n]

    v = ChestView(True, "", gi, stage, room)
    dan_no = struct.unpack(">b", at(prog.GI_DAN, 1))[0]
    v.cur_idx = dan_no if 0 <= dan_no < prog.N_MEM else None
    cur = at(prog.GI_CUR_MEMORY, prog.MEM_SIZE)
    for i in range(prog.N_MEM):
        data = cur if i == v.cur_idx else at(prog.GI_MEMORY + i * prog.MEM_SIZE, prog.MEM_SIZE)
        word = struct.unpack_from(">I", data, MEM_TBOX)[0]
        v.tbox |= {(i, n) for n in range(32) if word >> n & 1}
        d = data[MEM_DUNGEON]
        v.dungeon |= {(i, b) for b in range(8) if d >> b & 1}
    if v.cur_idx is not None:
        saved = at(prog.GI_MEMORY + v.cur_idx * prog.MEM_SIZE, prog.MEM_SIZE)
        if any(saved[j] & ~cur[j] for j in range(MEM_TBOX, MEM_TBOX + 4)):
            v.ok, v.reason = False, "copie vivante incohérente (chargement de stage ?)"
    g = mem.try_read(ADDR_GET_ITEM_NO, 1)
    v.get_item = g[0] if g else ITEM_NONE
    lv = mem.try_read(gi + GI_WALLET, GI_BOMB_MAX + 1 - GI_WALLET)
    if lv is not None:
        v.levels = {"wallet": lv[0], "arrow_max": lv[GI_ARROW_MAX - GI_WALLET], "bomb_max": lv[GI_BOMB_MAX - GI_WALLET]}
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    v.busy = "changement de stage en cours" if nxt is None or nxt[0] else prog.event_busy(mem)
    return v


def bit_addresses(v: ChestView, k: str, i: int, n: int) -> list[tuple[int, int]]:
    """[(adresse, masque)] où poser le bit (k, i, n) : copie sauvegardée, plus la vivante si c'est la
    stage chargée (comme dComIfGs_onStageTbox 0x025207D8 et progress.addresses)."""
    if k == "t":
        off, mask = prog.tbox_byte(n)
    elif k == "d":
        off, mask = prog.dungeon_byte(n)
    else:
        return []
    out = [(v.gi + prog.GI_MEMORY + i * prog.MEM_SIZE + off, mask)]
    if i == v.cur_idx:
        out.append((v.gi + prog.GI_CUR_MEMORY + off, mask))
    return out


def _add_written(written: dict, k: str, i: int, n: int) -> None:
    """Bits posés, au format de la progression ({"mem<i>": {off: masque}}) : KeySync.note_applied."""
    off, mask = prog.tbox_byte(n) if k == "t" else prog.dungeon_byte(n)
    d = written.setdefault(f"mem{i}", {})
    d[off] = d.get(off, 0) | mask


# ---------------------------------------------------------------------------
# Traduction de l'objet (fonction pure, §3.4 corrigée par le vérificateur)
# ---------------------------------------------------------------------------
@dataclass
class Translation:
    action: str                    # BIT | GRANT | WAIT | NO
    item: int | None = None
    dungeon_bit: int | None = None   # bit mDungeonItem posé avec le bit du coffre (carte…)
    need_stage: bool = False
    note: str = ""


INV_WAIT = "arrive par l'inventaire partagé"


def translate(k: str, item, confirmed: bool = True, keys_shared: bool = True, levels: dict | None = None,
              dungeon: bool = True, local_same: bool = False, inv: bool | None = None,
              bottle_inv: bool = False, counted: bool = False) -> Translation:
    """Que faire chez un autre joueur d'un coffre ouvert ?
    confirmed : objet lu dans un cache (pas « probable ») ; dungeon : index de sauvegarde d'un donjon ;
    local_same : le coffre de CE joueur a été vu avec le même objet (couches de stage identiques) ;
    inv : inventaire partagé actif ici et objet = jeton de l'inventaire (synchro/inventaire.py) : True s'il
    est déjà chez nous, False sinon, None si ce n'est pas le cas (contrat « propriétaire unique », R19 :
    l'objet n'arrive QUE par l'inventaire, le coffre ne pose que le bit, une fois l'objet arrivé) ;
    bottle_inv : flacon vide, inventaire partagé actif ici ET chez celui qui a ouvert (nombre de flacons
    partagé, « max des comptes ») : bit seul.
    counted : rubis / flèches / bombes comptés par le compteur commun ICI et chez celui qui a ouvert
    (drapeau ``cc``, synchro/compteurs.py) : bit seul, le gain est déjà dans le total commun (sinon +2 au lieu
    de +1, contrat « propriétaire unique » R19)."""
    if k == "d":
        return Translation(GRANT, ITEM_HEART, need_stage=True, note="réceptacle du boss (dans le donjon)")
    if k != "t":
        return Translation(NO, item, note="repêchage : phase 2")
    if item is None or not confirmed:
        return Translation(WAIT, item, note="objet inconnu : attend que ton coffre soit vu")

    def bit_only(**kw) -> Translation:
        if not dungeon and not local_same:
            return Translation(WAIT, item, note="hors donjon : attend que ton coffre confirme le même objet")
        return Translation(BIT, item, **kw)

    if item == ITEM_NONE:
        return bit_only(note="coffre vide")
    if item in DUNGEON_ITEMS:
        return bit_only(dungeon_bit=DUNGEON_ITEMS[item])
    if item == cles.ITEM_SMALL_KEY:
        if keys_shared:
            return bit_only(note="la clé passe par le compteur commun")
        # compteur commun pas actif ici ou chez celui qui a ouvert (--no-keys, serveur --no-keys) :
        # le bit seul ferait perdre la clé. En attente (revu si le compteur devient actif).
        return Translation(WAIT, item, note="petites clés non partagées : ouvre le tien")
    if item in UPGRADES:
        what, lvl = UPGRADES[item]
        cur = (levels or {}).get(what)
        if _int(cur) and cur >= lvl:
            return bit_only(note="niveau déjà atteint")
        return Translation(GRANT, item)
    if counted and item in COUNTER_ITEMS:
        return bit_only(note="compté par le total commun")
    if inv is True:
        return bit_only(note="déjà dans ton inventaire (inventaire partagé)")
    if inv is False:
        return Translation(WAIT, item, note=INV_WAIT)
    if item == ITEM_BOTTLE and bottle_inv:
        return bit_only(note="flacon : nombre partagé par l'inventaire")
    if item in GRANT_ITEMS:
        return Translation(GRANT, item, need_stage=item in NEED_STAGE_ITEMS)
    return Translation(NO, item, note="objet non partagé : ouvre le tien")


# ---------------------------------------------------------------------------
# Cache des coffres chargés (pour connaître l'objet d'un bit qui passe à 1)
# ---------------------------------------------------------------------------
class ChestCache:
    KEEP = 10.0     # s : entrées d'une stage quittée gardées (le coffre est encore chargé au bit)
    MAX = 256

    def __init__(self):
        self.entries: dict[tuple, dict] = {}   # (stage, idx, n) -> {item, params, pos, ang, room, t, pid, base}

    def update(self, mem, tboxes, stage: str, cur_idx, now: float) -> None:
        for e in [k for k, x in self.entries.items() if k[0] != stage and now - x["t"] > self.KEEP]:
            del self.entries[e]
        for a in tboxes or []:
            if len(a) < 2:
                continue
            o = read_tbox(mem, a[1])
            if o is None or o["pid"] != a[0] or not consistent(o):
                continue
            idx = save_idx(o["params"], cur_idx)
            if idx is None:
                continue
            if len(self.entries) >= self.MAX and (stage, idx, o["no"]) not in self.entries:
                continue
            self.entries[(stage, idx, o["no"])] = {
                "item": o["item"], "params": o["params"], "pos": [round(v, 2) for v in o["home"]],
                "ang": list(o["angle"]), "room": o["room"], "t": now, "pid": o["pid"], "base": o["base"]}

    def find(self, stage: str, idx: int, n: int, now: float) -> dict | None:
        e = self.entries.get((stage, idx, n))
        if e is not None:
            return e
        for (st, i, m), x in self.entries.items():
            if i == idx and m == n and st != stage and now - x["t"] <= self.KEEP:
                return x
        return None

    def get(self, stage: str, idx: int, n: int) -> dict | None:
        return self.entries.get((stage, idx, n))


# ---------------------------------------------------------------------------
# Détection (chez celui qui ouvre)
# ---------------------------------------------------------------------------
class ChestWatch:
    SETTLE = 2.0          # s de contexte stable avant la base
    RELOAD = 8            # bits effacés (ou apparus) d'un coup au-delà desquels : chargement
    PROBABLE = 5.0        # s : mGetItemNo accepté (« probable ») si un événement a eu lieu depuis

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.ctx = None
        self.since = 0.0
        self.base: set | None = None
        self.prev: set | None = None
        self.applied: dict = {}        # clés posées par nous (bit seul, GRANT) : jamais rapportées
        self.reloads = self.n_events = self.n_bursts = 0
        self.status = "pas encore lu"
        self.notes: list[str] = []

    def rebase(self) -> None:
        """« rejoindre » (A15), reconnexion… : nouvelle base silencieuse au prochain tick stable."""
        self.base = self.prev = None
        self.since = self.clock()

    def tick(self, v: ChestView, cache: ChestCache | None, last_busy: float = float("-inf"),
             now: float | None = None, keys_counted: bool = False, inv_counted: bool = False,
             counted=None) -> list[dict]:
        now = self.clock() if now is None else now
        if not v.ok:
            self.ctx, self.base, self.prev = None, None, None
            self.status = v.reason
            return []
        if v.context != self.ctx:
            self.ctx, self.since, self.base, self.prev = v.context, now, None, None
        if v.busy.startswith("changement de stage"):
            self.prev, self.since = None, now
            self.status = v.busy
            return []
        cur = v.keys()
        if now - self.since < self.SETTLE:
            self.prev = cur
            self.status = "attente de stabilité"
            return []
        if self.base is None:
            self.base, self.prev = set(cur), cur
            self.status = f"base : {len(cur)} coffre(s) déjà ouvert(s)"
            return []
        cleared = self.base - cur
        if len(cleared) >= self.RELOAD:
            self.reloads += 1
            self.base, self.prev = set(cur), cur
            self.status = "sauvegarde rechargée : nouvelle base"
            return []
        self.base -= cleared
        cand = (cur & (self.prev or set())) - self.base
        self.prev = cur
        self.status = "à l'écoute"
        if not cand:
            return []
        self.base |= cand
        if len(cand) > self.RELOAD:
            self.n_bursts += 1
            self.status = f"{len(cand)} coffres apparus d'un coup (chargement) : nouvelle base"
            return []
        out = []
        for key in sorted(cand):
            if key in self.applied:
                continue
            ev = self._event(v, key, cache, last_busy, now)
            if keys_counted:
                ev["kc"] = 1                   # notre KeySync compte la petite clé (contrat « clés »)
            if inv_counted:
                ev["iv"] = 1                   # notre inventaire partagé propage l'objet (contrat R19)
            if counted is not None and _int(ev.get("item")) and counted(ev["item"]):
                ev["cc"] = 1                   # notre compteur commun a compté ce gain (synchro/compteurs.py)
            out.append(ev)
        self.n_events += len(out)
        return out

    def _event(self, v: ChestView, key, cache, last_busy: float, now: float) -> dict:
        k, i, n = key
        ev = {"k": k, "i": i, "n": n, "item": None, "stage": v.stage, "room": v.room}
        if k == "d":
            ev["item"] = ITEM_HEART
            return ev
        e = cache.find(v.stage, i, n, now) if cache is not None else None
        if e is not None:
            ev["item"] = e["item"]
            ev["src"] = {"proc": PROC_TBOX, "params": e["params"], "pos": list(e["pos"]),
                         "ang": list(e["ang"]), "room": e["room"]}
            if v.get_item != e["item"]:
                self.notes.append(f"coffres : coffre {n} (index {i}) : objet du coffre 0x{e['item']:02X}, "
                                  f"mGetItemNo 0x{v.get_item:02X} (le coffre fait foi)")
        elif now - last_busy <= self.PROBABLE and v.get_item != ITEM_NONE:
            ev["item"], ev["prob"] = v.get_item, 1
        return ev


# ---------------------------------------------------------------------------
# File « à donner » (objets d'inventaire : pack v19, GRANT)
# ---------------------------------------------------------------------------
@dataclass
class GrantItem:
    key: tuple                   # (k, i, n)
    kind: int                    # 0 mTbox, 1 mDungeonItem, 2 mOcean (§3.8)
    idx: int
    bit: int
    item: int
    count: int = 1
    need_stage: bool = False
    by: str = "?"
    t: float = 0.0
    next_try: float = 0.0
    tries: int = 0


class GrantQueue:
    """Objets à donner par la commande GRANT du pack v19 (CALL 8 : bit + execItemGet atomiques).
    Remplie dès aujourd'hui (pack v17), consommée par ``ChestGrant`` quand un ``granter`` est branché.
    Jamais la petite clé (R19)."""
    MAX = 256

    def __init__(self):
        self.items: dict[tuple, GrantItem] = {}

    def push(self, g: GrantItem) -> bool:
        cles.assert_not_small_key(g.item)
        if g.key in self.items or len(self.items) >= self.MAX:
            return False
        self.items[g.key] = g
        return True

    def get(self, key) -> GrantItem | None:
        return self.items.get(key)

    def remove(self, key) -> None:
        self.items.pop(key, None)

    def clear(self) -> None:
        self.items.clear()

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(list(self.items.values()))


# ---------------------------------------------------------------------------
# Application chez les autres joueurs
# ---------------------------------------------------------------------------
class ChestGrant:
    RETRY_BUSY = 1.0     # s : GRANT -3 (jeu occupé)
    RETRY_ERROR = 5.0    # s : boîte aux lettres sans réponse (la requête est idempotente)
    MAX_RECORDS = 1024

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.pending: dict[tuple, dict] = {}
        self.known: dict[tuple, dict] = {}
        self.queue = GrantQueue()
        self.granter = None              # pack v19 : granter(kind, idx, bit, item, count, need_stage) -> int
        self.pre_grant = None            # (idx, n) -> bool : coffre affiché figé ou absent (ChestRefresh)
        # pack v19 (étude §3.5 étape 6) : (item) -> int, retire le réceptacle encore au sol après son GRANT
        self.remove_ground = None
        # relecture adverse du lot 2 : index de sauvegarde dont le réceptacle du boss est déjà chez nous par
        # le canal (GRANT « d » fait, ou enregistrement reçu alors que le bit 4 était déjà posé). Tant que le
        # bit 4 (STAGE_LIFE) est posé dans la copie vivante de cette stage, tout réceptacle au sol est un
        # doublon : la mort du boss le crée SANS tester STAGE_LIFE [HD-ASM daDisappear_Execute 0x02124548 ->
        # fopAcM_createItemForBoss 0x025D8A5C ; DECOMP d_a_disappear.cpp l.26-28] et le ramasser donne
        # +4 quarts sans condition [HD-ASM item_func_utuwa_heart 0x0254DC1C]. Surveillé à chaque tick.
        self.heart_watch: set[int] = set()
        self.heart_watch_t = float("-inf")
        self.n_hearts_removed = 0
        # inventaire partagé (synchro/inventaire.py, contrat « propriétaire unique ») : owned(item) -> True /
        # False pour un jeton de l'inventaire (déjà chez nous ou pas), None sinon ; None = pas d'inventaire
        self.owned = None
        # compteurs communs (synchro/compteurs.py) : counted(item) -> True si rubis / flèches / bombes de cet
        # objet sont comptés par le total commun ICI ; None = compteurs coupés
        self.counted = None
        # randomizer, objets progressifs (lot 4, relecture adverse) : prog(index, numéro) -> paliers de la famille
        # si ce coffre est un lieu progressif de la table chargée, sinon None ; None = pas d'objets progressifs.
        # Le même lieu ne porte pas le même palier chez deux joueurs (le pack pose celui du compte commun au moment
        # où le coffre est créé) : ce n'est pas « deux sauvegardes différentes »
        self.prog = None
        self.noted: set = set()
        self.notes: list[str] = []
        self.n_bits = self.n_given = self.n_refused = 0
        self.why = ""
        self.a_appliquer: set = set()      # (index, numéro) des coffres « t » dont le BIT ou le GRANT reste à faire

    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 2048:
            self.noted.add(tag)
            self._note(s)

    def receive(self, rec: dict) -> bool:
        key = (rec["k"], rec["i"], rec["n"])
        old = self.pending.get(key) or self.known.get(key)
        if old is not None:
            if (old.get("item") is None or old.get("prob")) and rec.get("item") is not None and not rec.get("prob"):
                self.known.pop(key, None)
                self.pending[key] = rec          # objet complété par le serveur
                return True
            return False
        if len(self.pending) + len(self.known) >= self.MAX_RECORDS:
            return False
        self.pending[key] = rec
        return True

    def reset(self) -> None:
        self.pending.clear()
        self.known.clear()
        self.queue.clear()
        self.noted.clear()
        self.heart_watch.clear()

    def reload(self) -> None:
        """Sauvegarde rechargée : tout ``known`` est revérifié (bit revenu à 0 = objet perdu aussi)."""
        self.pending.update(self.known)
        self.known.clear()
        self.noted.clear()

    def _done(self, key, rec) -> None:
        self.pending.pop(key, None)
        self.known[key] = rec
        self.queue.remove(key)

    @staticmethod
    def _who(rec) -> str:
        return str(rec.get("nom") or rec.get("by") or "un joueur")[:24]

    def tick(self, mem, v: ChestView, cache: ChestCache | None, keys_shared: bool, can_write: bool,
             settled: bool, applied: dict, now: float | None = None) -> dict:
        """Traite les enregistrements reçus. Renvoie les bits posés ({"mem<i>": {off: masque}}) pour
        le domaine « clés » (contrat §3.4 n° 5 : jamais pris pour des bits locaux)."""
        now = self.clock() if now is None else now
        written: dict = {}
        self.why = ""
        if not v.ok:
            self.why = v.reason
            return written
        self.a_appliquer = set()
        for key, rec in list(self.pending.items()):
            k, i, n = key
            if v.has(key):
                self._done(key, rec)          # déjà reçu (ouvert ici, ou posé par nous)
                if k == "d" and n == DUN_HEART:
                    self.heart_watch.add(i)   # client relancé après un GRANT : surveillance reprise
                continue
            item, confirmed = rec.get("item"), rec.get("item") is not None and not rec.get("prob")
            local = cache.get(rec.get("stage", ""), i, n) if (cache is not None and k == "t") else None
            paliers = self.prog(i, n) if (self.prog is not None and self.owned is not None and k == "t") else None
            meme = False
            if paliers and local is not None and local["item"] in paliers and (not confirmed or item in paliers):
                # lieu progressif : mon coffre montre le palier du compte d'aujourd'hui, l'autre joueur a reçu celui
                # du compte d'alors. C'est le même lieu : il se ferme ici dès que le palier reçu là-bas est arrivé
                # par l'inventaire partagé (sinon je l'ouvrirais à mon tour : un lieu, deux paliers)
                item, confirmed, meme = (item if confirmed else paliers[0]), True, True
            elif local is not None:
                if confirmed and local["item"] != item:
                    self._done(key, rec)
                    self.n_refused += 1
                    self._note(f"coffres : {self._who(rec)} a ouvert le coffre {n} (index {i}) : "
                               f"{item_name(item)}, mais le tien contient {item_name(local['item'])} "
                               "(sauvegardes différentes) : non partagé")
                    continue
                item, confirmed = local["item"], True
            # petite clé : le compteur commun doit être actif ici ET chez celui qui a ouvert (« kc »),
            # sinon personne ne compte la clé et le bit seul la ferait perdre
            inv = self.owned(item) if (self.owned is not None and confirmed and _int(item)) else None
            if inv is False and not rec.get("iv"):
                inv = None                    # l'auteur ne partage pas son inventaire : voie GRANT (v19)
            tr = translate(k, item, confirmed, keys_shared and bool(rec.get("kc")), v.levels, dungeon=i in prog.TSW_DUNGEON_SAVE_NOS,
                           local_same=meme or (local is not None and local["item"] == item), inv=inv,
                           bottle_inv=self.owned is not None and bool(rec.get("iv")),
                           counted=bool(rec.get("cc")) and confirmed and _int(item) and self.counted is not None
                           and bool(self.counted(item)))
            who = self._who(rec)
            if tr.action == NO:
                self._done(key, rec)
                self.n_refused += 1
                self._once(("non", key), f"coffres : {who} a ouvert un coffre : {item_name(item)} ({tr.note})")
            elif tr.action == WAIT and tr.note == INV_WAIT:
                self._once(("inv", key), f"{who} a ouvert un coffre : {item_name(item)} ({INV_WAIT}, puis le "
                                         "coffre s'ouvre chez toi)")
            elif tr.action == WAIT:
                self._once(("attente", key), f"coffres : {who} a ouvert un coffre : {item_name(item)} "
                                             f"({tr.note}) ; ouvre le tien toi-même si tu le trouves")
            elif tr.action == BIT:
                if k == "t":
                    self.a_appliquer.add((i, n))
                self._bit_only(mem, v, key, rec, tr, can_write, settled, applied, written, who)
            else:
                if k == "t" and tr.item != cles.ITEM_SMALL_KEY and self.granter is not None:
                    # pack v17 (pas de GRANT) : le coffre reste ouvrable chez lui, rien n'est perdu (jamais figé)
                    self.a_appliquer.add((i, n))
                self._grant(mem, v, key, rec, tr, can_write, settled, applied, written, who, now)
            if v.has(key) or key in self.known:
                self.a_appliquer.discard((i, n))
        self._watch_hearts(v, now)
        return written

    HEART_WATCH_PERIOD = 0.5   # s entre deux recherches de réceptacle en double

    def _watch_hearts(self, v: ChestView, now: float) -> None:
        """Réceptacle déjà reçu par le canal : tout réceptacle au sol de cette stage est supprimé, à chaque
        passage, tant que le joueur y est et que le bit 4 est posé chez lui (cas B2 du plan de test : son
        boss meurt APRÈS le GRANT et en crée un ; ou réceptacle apparu moins d'un rescan avant le GRANT).
        Seulement la stage chargée (``cur_idx``) ; bit 4 revenu à 0 (sauvegarde rechargée) : rien."""
        i = v.cur_idx
        if self.remove_ground is None or i is None or i not in self.heart_watch:
            return
        if not v.has(("d", i, DUN_HEART)) or now - self.heart_watch_t < self.HEART_WATCH_PERIOD:
            return
        self.heart_watch_t = now
        try:
            n = int(self.remove_ground(ITEM_HEART) or 0)
        except Exception as exc:              # jamais fatal ; nouvel essai au passage suivant
            self._once(("heart_err", i), f"coffres : réceptacle en double non retiré ({exc}) : nouvel essai")
            return
        if n > 0:
            self.n_hearts_removed += n
            self._note("coffres : réceptacle en double retiré (déjà reçu d'un autre joueur)")

    def _free(self, v: ChestView, can_write: bool, settled: bool) -> str:
        if not can_write:
            return "écriture impossible (mémoire en lecture seule ou --progress-no-apply)"
        if not settled:
            return "attente de stabilité"
        if v.busy:
            return v.busy
        return ""

    def _bit_only(self, mem, v, key, rec, tr, can_write, settled, applied, written, who) -> None:
        why = self._free(v, can_write, settled)
        if why:
            self.why = why
            return
        k, i, n = key
        if self.pre_grant is not None and k == "t" and not self.pre_grant(i, n):
            # comme le GRANT : un bit seul sous un coffre encore ouvrable chez nous (affiché, ou caché qui apparaîtra
            # fermé) le laisserait ouvrir une seconde fois (petite clé, rubis comptés : en double)
            self.why = "coffre affiché à côté de toi : éloigne-toi un peu"
            return
        if game_free(mem):                    # relu juste avant d'écrire
            self.why = "événement en cours"
            return
        applied[key] = self.clock()
        targets = [(k, n)] + ([("d", tr.dungeon_bit)] if tr.dungeon_bit is not None else [])
        for kk, nn in targets:
            if kk == "d":
                applied[("d", i, nn)] = self.clock()
            for va, mask in bit_addresses(v, kk, i, nn):
                prog._rmw(mem, va, mask)
            _add_written(written, kk, i, nn)
        self.n_bits += 1
        self._done(key, rec)
        extra = {DUN_MAP: " (carte du menu)", DUN_COMPASS: " (boussole)", DUN_BOSS_KEY: " (porte du boss)"}
        self._note(f"coffres : {who} a ouvert un coffre : {item_name(tr.item)} ; coffre ouvert chez toi aussi"
                   + extra.get(tr.dungeon_bit, ""))

    def _grant(self, mem, v, key, rec, tr, can_write, settled, applied, written, who, now) -> None:
        k, i, n = key
        if tr.item == cles.ITEM_SMALL_KEY:     # R19 : jamais (translate ne le propose pas)
            self._done(key, rec)
            return
        g = self.queue.get(key)
        if g is None:
            g = GrantItem(key, KIND_CODE[k], i, n, tr.item, 1, tr.need_stage, who, now)
            self.queue.push(g)
        if self.granter is None:
            # pack v17 : le bit n'est PAS posé (rien n'est perdu : le coffre reste ouvrable chez lui)
            self._once(("v19", key), f"{who} a ouvert un coffre : {item_name(tr.item)} "
                                     "(tu le recevras avec le pack v19)")
            return
        if now < g.next_try:
            return
        if tr.need_stage and i != v.cur_idx:
            self._once(("stage", key), f"coffres : {item_name(tr.item)} de {who} : entre dans le donjon "
                                       "pour le recevoir")
            return
        why = self._free(v, can_write, settled)
        if why:
            self.why = why
            return
        if self.pre_grant is not None and k == "t" and not self.pre_grant(i, n):
            self.why = "coffre affiché à côté de toi : éloigne-toi un peu"
            return
        cles.assert_not_small_key(tr.item)
        applied[key] = self.clock()
        try:
            res = int(self.granter(g.kind, i, n, tr.item, g.count, g.need_stage))
        except Exception as exc:              # boîte aux lettres sans réponse : idempotent, on réessaie
            g.next_try, g.tries = now + self.RETRY_ERROR, g.tries + 1
            self._once(("err", key), f"coffres : {item_name(tr.item)} pas encore donné ({exc}) : nouvel essai")
            return
        if res == 1:
            self.n_given += 1
            self._done(key, rec)
            _add_written(written, k, i, n)
            if k == "d":
                self._note(f"Réceptacle de cœur ramassé par {who} : reçu chez toi")
                if n == DUN_HEART:
                    self.heart_watch.add(i)   # le réceptacle de NOTRE boss, s'il apparaît plus tard, sera retiré
                if tr.item == ITEM_HEART and self.remove_ground is not None:
                    try:
                        if self.remove_ground(ITEM_HEART):
                            self._note("coffres : réceptacle encore au sol retiré (déjà reçu)")
                    except Exception as exc:      # jamais fatal : _watch_hearts réessaie aux ticks suivants
                        self._note(f"coffres : réceptacle au sol non retiré ({exc})")
            else:
                self._note(f"Coffre ouvert par {who} : {item_name(tr.item)} reçu")
        elif res == 0:
            self._done(key, rec)              # déjà posé chez nous entre-temps
        elif res == -3:
            g.next_try = now + self.RETRY_BUSY
        elif res == -4:
            g.next_try = now + self.RETRY_BUSY
            self._once(("stage", key), f"coffres : {item_name(tr.item)} de {who} : entre dans le donjon "
                                       "pour le recevoir")
        else:
            self._done(key, rec)
            self.n_refused += 1
            self._note(f"coffres : {item_name(tr.item)} de {who} refusé par le jeu (code {res})")

    def status(self) -> str:
        s = f"{self.n_bits} coffre(s) ouvert(s) chez toi, {self.n_given} objet(s) reçu(s)"
        if self.n_refused:
            s += f", {self.n_refused} non partagé(s)"
        if len(self.queue):
            s += f", {len(self.queue)} objet(s) à donner (pack v19)"
        if self.why and self.pending:
            s += f" ; en attente : {self.why}"
        return s


# ---------------------------------------------------------------------------
# Coffre affiché chez l'autre : recréation (ouvert) ou repli « figer » (étape 2)
# ---------------------------------------------------------------------------
def freeze(mem, expect: dict, min_dist: float) -> tuple[bool, str]:
    """Repli « figer » : +0x6D8 = actionWait, après revalidation complète (même id, mêmes paramètres,
    PTMF 0/-1, actionOpenWait, aucune commande, pas OPENING, jeu libre, Link à min_dist ou plus)."""
    o = read_tbox(mem, expect["base"])
    if o is None or o["pid"] != expect["pid"] or o["params"] != expect["params"] or not consistent(o):
        return False, "acteur changé"
    if o["head"] != PTMF_HEAD or o["action"] != ACT_OPEN_WAIT or o["cmd"] != 0 or o["flags"] & FLAG_OPENING:
        return False, "coffre plus en attente d'ouverture"
    why = game_free(mem)
    if why:
        return False, why
    d = link_xz_dist(mem, o["home"])
    if d is None or d < min_dist:
        return False, "Link trop près"
    try:
        mem.write(expect["base"] + OFF_ACTION_FN, struct.pack(">I", ACT_WAIT))
    except Exception as exc:
        return False, f"écriture impossible ({exc})"
    return True, ""


def is_switch(mem, gi: int, sw: int, room: int) -> bool | None:
    """dSv_info_c::isSwitch(sw, room) relu (lecture seule) : None si aucun switch (0xFF) ou illisible."""
    try:
        if not 0 <= sw < prog.TSW_END:
            return None
        if sw < prog.TSW_DAN_FIRST:
            raw = mem.try_read(gi + prog.GI_CUR_MEMORY + 0x04 + 4 * (sw >> 5) + 3 - ((sw & 31) >> 3), 1)
            return None if raw is None else bool(raw[0] >> (sw & 7) & 1)
        kind, off, mask = prog.tsw_byte(sw)
        if kind == "dan":
            no = mem.try_read(gi + prog.GI_DAN + prog.DAN_STAGE_NO, 1)
            if no is None or not 0 <= struct.unpack(">b", no)[0] < prog.N_MEM:
                return None
            raw = mem.try_read(gi + prog.GI_DAN + off, 1)
        else:
            base = prog._zone_base(mem, gi, room)
            raw = None if base is None else mem.try_read(base + off, 1)
        return None if raw is None else bool(raw[0] & mask)
    except Exception:
        return None


def hidden_ok(mem, o: dict) -> str:
    """'' si le coffre caché ``o`` peut être figé : aucune apparition commandée (commande, condition, switch,
    compte à rebours), sinon la raison."""
    if o["action"] not in HIDDEN_WAIT or not o["flags"] & FLAG_HIDDEN:
        return "coffre pas caché"
    if o["cmd"] != 0 or o["cond"] & COND_ORDERED or o["flags"] & FLAG_OPENING:
        return "apparition commandée"
    if o["action"] == ACT_GENOCIDE:
        return "" if o["geno"] == GENO_COUNT_INIT else "apparition imminente (ennemis tués)"
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    gi = struct.unpack(">I", raw)[0] if raw is not None else 0
    if not 0x10000000 <= gi < 0x50000000:
        return "sauvegarde illisible"
    on = is_switch(mem, gi, (o["params"] >> 12) & 0xFF, o["room_no"])
    if on is None:
        return "switch illisible"
    return "apparition imminente (switch posé)" if on else ""


def freeze_hidden(mem, expect: dict) -> tuple[bool, str]:
    """Coffre CACHÉ qui attend son switch ou la mort des ennemis, ouvert ailleurs : +0x6D8 = actionWait (li r3, 1 ;
    blr). Il reste caché (bit 0 de +0x788) et ne propose rien ; il naîtra ouvert à la prochaine création (bit tbox).
    Toute distance (il n'est ni dessiné ni ouvrable) ; revalidation complète juste avant d'écrire."""
    o = read_tbox(mem, expect["base"])
    if o is None or o["pid"] != expect["pid"] or o["params"] != expect["params"] or not consistent(o):
        return False, "acteur changé"
    if o["head"] != PTMF_HEAD:
        return False, "acteur changé"
    why = hidden_ok(mem, o) or game_free(mem)
    if why:
        return False, why
    try:
        mem.write(expect["base"] + OFF_ACTION_FN, struct.pack(">I", ACT_WAIT))
    except Exception as exc:
        return False, f"écriture impossible ({exc})"
    return True, ""


class ChestRefresh:
    PERIOD = 0.5
    ROOM_SETTLE = 2.0      # s dans la salle avant d'agir (acteurs de la salle tous créés)
    CONFIRM = 0.4          # s : « bit posé + coffre fermé » vu depuis au moins CONFIRM s
    RECREATE_DIST = 150.0  # recréation seulement au-delà (étude §3.5)
    FREEZE_DIST = 100.0    # figer seulement à cette distance ou plus (vérificateur : blocage)
    CREATE_WAIT = 1.0      # s : la remplaçante doit paraître en actionWait (sinon repli)
    CREATED_GRACE = 3.0
    CREATED_TTL = 15.0
    MAYBE_TTL = 60.0
    MAX_TRIES = 3
    RETRY = 1.0

    def __init__(self, enabled: bool = True, clock=time.time):
        self.enabled, self.clock = enabled, clock
        self.ctx = None
        self.stage = None
        self.last = float("-inf")
        self.seen: dict[int, float] = {}
        self.tries: dict[str, int] = {}
        self.next_try: dict[str, float] = {}
        self.given_up: set = set()
        self.frozen: set = set()          # ids figés par nous (recréés plus tard si Link s'éloigne)
        self.waiting: dict[int, dict] = {}   # remplaçante créée -> {old, t, key}
        self.created: dict[int, dict] = {}   # remplaçantes (couche de Link) : id -> {key, room, t, seen, n}
        self.maybe: dict[str, tuple] = {}
        self.deleted: set = set()
        self.noted: set = set()
        self.notes: list[str] = []
        self.recreated = self.n_frozen = self.cleaned = self.failed = 0
        self.why = ""
        self.objs: dict[int, dict] = {}
        self.a_appliquer: set = set()        # (index, numéro) reçus d'un autre joueur, BIT ou GRANT à venir (ChestGrant)
        self.contact = False                 # coffre ouvert ailleurs affiché ouvrable à moins de FREEZE_DIST de Link
        self.contact_t = float("-inf")
        self.n_hidden = 0

    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 512:
            self.noted.add(tag)
            self._note(s)

    def reset(self, stage: bool = True) -> None:
        self.seen.clear()
        self.tries.clear()
        self.next_try.clear()
        self.given_up.clear()
        self.frozen.clear()
        self.noted = {t for t in self.noted if not (isinstance(t, tuple) and t[0] in ("pres", "bloque"))}
        self.why = ""
        if stage:
            self.waiting.clear()
            self.created.clear()
            self.maybe.clear()
            self.deleted.clear()

    def pre_grant(self, idx: int, n: int) -> bool:
        """Ordre du vérificateur : figer -> GRANT (ou bit seul) -> recréer. True si aucun coffre (idx, n) chargé chez
        nous ne peut encore s'ouvrir : absent, ou en actionWait (ouvert, ou figé par nous). Un coffre caché qui attend
        son switch ou les ennemis (il apparaîtrait FERMÉ, le bit n'est relu qu'à la création), en apparition, en
        ouverture ou ouvrable : False (bug du 2026-11-13, objet en double à l'apparition)."""
        for pid, o in self.objs.items():
            if o["no"] == n and o.get("idx") == idx and o["action"] != ACT_WAIT:
                return False
        return True

    def _delete(self, sm, o: dict) -> bool:
        try:
            ok = bool(sm.delete_actor(o["pid"]))
        except Exception as exc:
            self._note(f"coffres : suppression impossible ({exc})")
            return False
        if ok:
            self.deleted.add(o["pid"])
            self.created.pop(o["pid"], None)
        return ok

    def step(self, sm, mem, tboxes, v: ChestView, now: float | None = None, ctx=None, free: bool = True,
             settled: bool = True, can: bool = True, can_mem: bool = True) -> list[tuple[int, str]]:
        """tboxes : [(id, adresse)] relevés par le client (None : relevé raté) ; ctx : zone ; free : jeu
        libre ; settled : salle stable ; can : boîte aux lettres utilisable ; can_mem : écriture mémoire
        (repli « figer »). Renvoie [(id, « recree » | « fige » | « retire » | « supprime »)]."""
        if not self.enabled:
            return []
        now = self.clock() if now is None else now
        if ctx != self.ctx:
            st = ctx[0].rsplit(":", 1)[0] if isinstance(ctx, tuple) and ctx and isinstance(ctx[0], str) else ctx
            if ctx is not None and st != self.stage:
                self.reset(stage=True)
                self.stage = st
            else:
                self.reset(stage=False)
            self.ctx = ctx
        if ctx is None or tboxes is None or mem is None or not v.ok:
            self.objs = {}
            return []
        if now - self.last < self.PERIOD:
            return []
        self.last = now
        self.why = ""
        done: list[tuple[int, str]] = []
        objs: dict[int, dict] = {}
        present = set()
        for a in tboxes:
            if len(a) < 2:
                continue
            present.add(a[0])
            if a[0] in self.deleted:
                continue
            o = read_tbox(mem, a[1])
            if o is None or o["pid"] != a[0]:
                continue
            if not consistent(o):
                self._once(("dispo",), "coffres : disposition HD inattendue d'un coffre (proc 292) : ignoré")
                continue
            o["idx"] = save_idx(o["params"], v.cur_idx)
            objs[a[0]] = o
        self.objs = objs
        self.contact = False
        self._adopt(objs, now)
        self._follow(sm, mem, objs, now, free, can, done)
        self._replacements(sm, mem, v, objs, present, now, free, can, done)
        for pid, o in objs.items():
            if pid in self.waiting or any(w["old"]["pid"] == pid for w in self.waiting.values()):
                continue
            c = self.created.get(pid)
            if c is not None:
                continue                      # notre remplaçante : jamais retraitée
            if o["idx"] is None:
                self.seen.pop(pid, None)
                continue
            bit = (o["idx"], o["no"]) in v.tbox
            if not bit and (o["idx"], o["no"]) not in self.a_appliquer:
                self.seen.pop(pid, None)
                continue
            if not bit and (o["action"] not in CLOSED or pid in self.frozen):
                self.seen.pop(pid, None)      # à appliquer, déjà figé (ou en apparition / ouverture) : le GRANT suit
                continue
            stale = (o["action"] in CLOSED or (o["action"] == ACT_WAIT and pid in self.frozen)) \
                and o["cmd"] == 0 and not o["flags"] & FLAG_OPENING
            if not stale:
                self.seen.pop(pid, None)
                continue
            t0 = self.seen.setdefault(pid, now)
            k = chest_key(o)
            if now - t0 < self.CONFIRM or k in self.given_up or now < self.next_try.get(k, 0.0):
                continue
            if not (free and settled):
                self.why = "cinématique ou salle en chargement"
                continue
            why = game_free(mem)
            if why:
                self.why = why
                continue
            act = self._act(sm, mem, o, objs, now, can, can_mem, bit)
            if act:
                done.append((pid, act))
        for pid in [p for p in self.seen if p not in present]:
            del self.seen[pid]
        self.frozen &= present
        self.deleted &= present
        return done

    def _act(self, sm, mem, o: dict, objs: dict, now: float, can: bool, can_mem: bool, bit: bool = True) -> str:
        """``bit`` : bit tbox déjà posé chez nous (recréation possible : le coffre naîtra ouvert) ; sinon le coffre
        est seulement « à appliquer » (enregistrement reçu) : on le rend inouvrable AVANT le GRANT, jamais recréé."""
        k = chest_key(o)
        where = f"coffre {o['no']} ({o['home'][0]:.0f}, {o['home'][1]:.0f}, {o['home'][2]:.0f})"
        d = link_xz_dist(mem, o["home"])
        if d is None:
            self.why = "position de Link illisible"
            return ""
        if o["action"] in HIDDEN_WAIT and o["flags"] & FLAG_HIDDEN and o["pid"] not in self.frozen \
                and (not bit or d <= self.RECREATE_DIST or not can):
            # caché (il apparaîtrait FERMÉ) : figé caché à toute distance, avant le GRANT ; recréé ouvert plus tard
            if not can_mem:
                return ""
            ok, why = freeze_hidden(mem, o)
            if ok:
                self.frozen.add(o["pid"])
                self.n_hidden += 1
                self._note(f"coffres : {where} ouvert par un autre joueur : il n'apparaîtra pas fermé chez toi "
                           "(il paraîtra ouvert dès que tu t'en éloignes, ou au prochain passage)")
                return "fige"
            self.why = why
            return ""
        if not bit:
            if o["action"] != ACT_OPEN_WAIT or o["pid"] in self.frozen:
                return ""                     # en apparition, en ouverture : on attend qu'il soit ouvrable
            if d >= self.FREEZE_DIST:
                if can_mem:
                    ok, why = freeze(mem, o, self.FREEZE_DIST)
                    if ok:
                        self.frozen.add(o["pid"])
                        self.n_frozen += 1
                        self._note(f"coffres : {where} ouvert par un autre joueur : il ne s'ouvre plus chez toi")
                        return "fige"
                    self.why = why
                return ""
            self.contact = True
            self.contact_t = now
            self.why = "Link au contact du coffre"
            self._once(("pres", o["pid"]), f"coffres : {where} déjà ouvert par un autre joueur : il ne s'ouvre pas "
                                           "chez toi (éloigne-toi un peu : il sera mis à jour et l'objet arrivera)")
            return ""
        if d > self.RECREATE_DIST and can:
            twin = any(q != o["pid"] and x["no"] == o["no"] and x["idx"] == o["idx"] and chest_key(x) == k
                       and x["action"] == ACT_WAIT and q not in self.frozen for q, x in objs.items())
            if twin:                          # remplaçante déjà là : on supprime seulement l'ancien
                if self._delete(sm, o):
                    self.recreated += 1
                    self._note(f"coffres : ancien {where} supprimé (coffre ouvert déjà là)")
                    return "supprime"
                return ""
            if k in self.maybe:
                self.next_try[k] = now + self.RETRY
                return ""
            if not hasattr(sm, "create_actor"):
                return ""
            ax, ay, az = o["angle"]
            try:
                new = sm.create_actor(PROC_TBOX, o["params"], o["home"], o["room"], int(ay), rot_x=int(ax),
                                      rot_z=int(az))
            except Exception as exc:          # requête peut-être restée en attente (menu pause)
                self.maybe[k] = (now, {q for q, x in objs.items() if chest_key(x) == k})
                self.next_try[k] = now + self.RETRY
                self._note(f"coffres : {where} peut-être pas recréé ({exc}) : on attend qu'il paraisse")
                return ""
            self.next_try[k] = now + self.RETRY   # un échec (création refusée ou ratée) compte un essai
            if new is None:
                self._fail(o, now, "création refusée", mem, can_mem)
                return ""
            self.waiting[new] = {"old": o, "t": now, "key": k, "n": 0}
            self.created[new] = {"key": k, "room": o["room"], "t": now, "seen": False, "n": 0}
            if hasattr(sm, "request_rescan"):
                sm.request_rescan()
            return "cree"
        if d >= self.FREEZE_DIST:
            if o["action"] == ACT_OPEN_WAIT and o["pid"] not in self.frozen and can_mem:
                ok, why = freeze(mem, o, self.FREEZE_DIST)
                if ok:
                    self.frozen.add(o["pid"])
                    self.n_frozen += 1
                    self._note(f"coffres : {where} ouvert ailleurs : il ne s'ouvre plus chez toi "
                               "(il paraîtra ouvert au prochain passage dans la salle)")
                    return "fige"
                self.why = why
            return ""
        if o["action"] == ACT_OPEN_WAIT and o["pid"] not in self.frozen:
            self.contact = True
            self.contact_t = now
        self.why = "Link au contact du coffre"
        self._once(("pres", o["pid"]), f"coffres : {where} déjà ouvert par un autre joueur : il ne s'ouvre pas "
                                       "chez toi (éloigne-toi un peu, il sera mis à jour)")
        return ""

    def _fail(self, o: dict, now: float, why: str, mem=None, can_mem: bool = False) -> None:
        k = chest_key(o)
        n = self.tries.get(k, 0) + 1
        self.tries[k] = n
        self.next_try[k] = now + self.RETRY
        if n >= self.MAX_TRIES and k not in self.given_up:
            self.given_up.add(k)
            self.failed += 1
            msg = f"coffres : coffre {o['no']} pas recréé après {n} essais ({why})"
            if mem is not None and can_mem and o["action"] == ACT_OPEN_WAIT:
                ok, _w = freeze(mem, o, self.FREEZE_DIST)
                if ok:
                    self.frozen.add(o["pid"])
                    self.n_frozen += 1
                    msg += " : figé (il ne s'ouvre plus, paraîtra ouvert au prochain passage)"
            self._note(msg)

    def _adopt(self, objs: dict, now: float) -> None:
        for k, (t, known) in list(self.maybe.items()):
            new = [q for q, x in objs.items() if chest_key(x) == k and q not in known and q not in self.created]
            if new:
                for q in new:
                    self.created[q] = {"key": k, "room": objs[q]["room"], "t": now, "seen": True, "n": 0}
                del self.maybe[k]
                self._note("coffres : coffre recréé avec retard (boîte aux lettres) : suivi")
            elif now - t > self.MAYBE_TTL:
                del self.maybe[k]

    def _follow(self, sm, mem, objs: dict, now: float, free: bool, can: bool, done: list) -> None:
        """Remplaçantes en attente : vue ouverte -> l'ancien coffre est supprimé (création AVANT
        suppression : si la création échoue, l'ancien reste et on passe au repli)."""
        for new, w in list(self.waiting.items()):
            old = w["old"]
            x = objs.get(new)
            if x is not None and x["action"] == ACT_WAIT:
                cur = read_tbox(mem, old["base"])
                if cur is None or cur["pid"] != old["pid"]:
                    del self.waiting[new]     # ancien déjà parti
                    continue
                if not (free and can) or game_free(mem):
                    continue
                if self._delete(sm, old):
                    del self.waiting[new]
                    self.recreated += 1
                    done.append((old["pid"], "recree"))
                    self._note(f"coffres : coffre {old['no']} ouvert ailleurs : affiché ouvert chez toi")
                    continue
                w["n"] += 1
                if w["n"] >= self.MAX_TRIES:
                    del self.waiting[new]
                    self.given_up.add(w["key"])   # jamais de nouvelle remplaçante cette visite
                    self.failed += 1
                    self._note(f"coffres : ancien coffre {old['no']} non supprimé après {w['n']} essais : deux "
                               "coffres superposés jusqu'au rechargement de la salle")
                continue
            if now - w["t"] < self.CREATE_WAIT:
                continue
            del self.waiting[new]
            if x is not None:                 # créée mais pas ouverte : jamais deux coffres fermés
                if free and can and not game_free(mem):
                    self._delete(sm, x)
                self._fail(old, now, "coffre recréé pas ouvert", mem, True)
            else:
                self._fail(old, now, "coffre recréé jamais vu", mem, True)

    def _replacements(self, sm, mem, v, objs, present, now, free, can, done) -> None:
        for pid, c in list(self.created.items()):
            o = objs.get(pid)
            if o is None:
                if pid in present and pid not in self.deleted:
                    continue
                if c["seen"] or now - c["t"] > self.CREATED_TTL:
                    del self.created[pid]
                continue
            c["seen"] = True
            if now - c["t"] < self.CREATED_GRACE or pid in self.waiting:
                continue
            reason = ""
            if room_loaded(mem, v.gi, c["room"]) is False:
                reason = "salle déchargée"
            else:
                for q, x in objs.items():
                    if q != pid and q not in self.created and chest_key(x) == c["key"] \
                            and x["action"] == ACT_WAIT and q not in self.frozen:
                        reason = "coffre de la salle recréé ouvert par le jeu"
                        break
            if not reason or not (free and can) or game_free(mem):
                continue
            if self._delete(sm, o):
                self.cleaned += 1
                done.append((pid, "retire"))
                self._note(f"coffres : coffre recréé par nous retiré ({reason})")
                continue
            c["n"] += 1
            if c["n"] >= self.MAX_TRIES:
                del self.created[pid]
                self.failed += 1

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        s = f"{self.recreated} coffre(s) affiché(s) ouvert(s), {self.n_frozen} figé(s)"
        if self.n_hidden:
            s += f", {self.n_hidden} caché(s) figé(s) avant d'apparaître"
        if self.cleaned:
            s += f", {self.cleaned} remplaçant(s) retiré(s)"
        if self.failed:
            s += f", {self.failed} échec(s)"
        if self.why:
            s += f" ; en attente : {self.why}"
        return s


# ---------------------------------------------------------------------------
# Façade pour le client
# ---------------------------------------------------------------------------
class ChestSync:
    PERIOD = 0.2           # s entre deux lectures (~5 Hz)
    SETTLE = 2.0           # s de contexte stable avant d'écrire
    MINE_MAX = 256
    SEND_RATE = 12         # rapports envoyés au plus par seconde (serveur : COFFRES_RATE = 16, sinon
                           # le renvoi de ``mine`` à la reconnexion serait en partie ignoré)

    def __init__(self, enabled: bool = True, group: str = "default", refresh: bool = True,
                 clock=time.monotonic, wall=time.time):
        self.enabled, self.group = enabled, group
        self.clock = clock
        self.cache = ChestCache()
        self.watch = ChestWatch(clock)
        self.grant = ChestGrant(clock)
        self.refresh = ChestRefresh(enabled=refresh, clock=wall)
        self.grant.pre_grant = self.refresh.pre_grant
        self.out: list[dict] = []
        self._out_lock = threading.Lock()      # tick (fil de travail) / take_out, on_connect (asyncio)
        self.sent_t: list[float] = []          # heures des derniers rapports envoyés (débit)
        self.mine: dict[tuple, dict] = {}      # nos événements (renvoyés à chaque connexion)
        self.notes: list[str] = []
        self.last = float("-inf")
        self.ctx = None
        self.ctx_t = 0.0
        self.reloads_seen = 0
        self.view: ChestView = ChestView(False, "pas encore lu")

    @property
    def granter(self):
        return self.grant.granter

    @granter.setter
    def granter(self, fn) -> None:
        """Pack v19 : ``fn(kind, idx, bit, item, count, need_stage) -> int`` (mailbox.grant)."""
        self.grant.granter = fn

    def on_connect(self) -> None:
        if not self.enabled:
            return
        with self._out_lock:
            self.out = [m for m in self.out if not m.get("full")]
            self.out.insert(0, {"type": "coffre", "group": self.group, "full": 1})
            for ev in list(self.mine.values()):
                if ev not in self.out:
                    self.out.append(ev)

    def take_out(self, now: float | None = None) -> list[dict]:
        """Messages à envoyer maintenant : la demande du registre toujours, les rapports au plus
        ``SEND_RATE`` par seconde (le reste attend le prochain appel, dans l'ordre)."""
        now = self.clock() if now is None else now
        self.sent_t = [t for t in self.sent_t if now - t < 1.0]
        out = []
        with self._out_lock:
            keep = []
            for m in self.out:
                if m.get("full"):
                    out.append(m)
                elif len(self.sent_t) < self.SEND_RATE:
                    self.sent_t.append(now)
                    out.append(m)
                else:
                    keep.append(m)
            self.out = keep
        return out

    def take_notes(self) -> list[str]:
        out = self.notes + self.watch.notes + self.grant.notes + self.refresh.notes
        self.notes, self.watch.notes, self.grant.notes, self.refresh.notes = [], [], [], []
        return out

    def receive(self, msg: dict, me=None) -> int:
        """Message « coffre » du serveur. Renvoie le nombre d'enregistrements nouveaux."""
        if not self.enabled or msg.get("group") not in (None, self.group):
            return 0
        if msg.get("reset") in (1, True) and msg.get("from") is None:
            self.grant.reset()
            self.watch.rebase()
            self.mine.clear()                  # sauvegardes alignées par la copie : rien à renvoyer
            self.notes.append("coffres : registre du serveur remis à zéro (rejoindre)")
            return 0
        from commun.protocole import valid_coffre
        recs = msg.get("list") if msg.get("full") else [msg]
        n = 0
        for raw in (recs if isinstance(recs, list) else [])[:1024]:
            if not isinstance(raw, dict):
                continue
            if me is not None and raw.get("from") == me and not msg.get("full"):
                continue
            rec = valid_coffre(raw)
            if rec is None:
                continue
            rec["nom"] = str(raw.get("nom") or raw.get("by") or "?")[:24]
            n += self.grant.receive(rec)
        return n

    def tick(self, mem, sm=None, tboxes=None, ctx=None, free: bool = True, room_settled: bool = True,
             can_write: bool = True, can_mailbox: bool = True, keys_shared: bool = False,
             last_busy: float = float("-inf"), reloads: int = 0, now: float | None = None,
             keys_counted: bool | None = None) -> dict:
        """Un tick (~5 Hz). Renvoie les bits posés ({"mem<i>": {off: masque}}) pour KeySync.note_applied.
        keys_shared : compteur commun des petites clés actif ICI (bit seul d'un coffre à clé permis) ;
        keys_counted : notre KeySync compte nos clés (drapeau « kc » de nos rapports ; défaut keys_shared).
        Inventaire partagé : ``self.grant.owned`` branché (drapeau « iv » de nos rapports)."""
        if keys_counted is None:
            keys_counted = keys_shared
        if not self.enabled or mem is None:
            return {}
        now = self.clock() if now is None else now
        if now - self.last < self.PERIOD:
            return {}
        self.last = now
        try:
            v = read_chest_bits(mem)
        except Exception as exc:
            v = ChestView(False, f"lecture impossible : {exc}")
        self.view = v
        if v.ok and v.context != self.ctx:
            self.ctx, self.ctx_t = v.context, now
        r = reloads + self.watch.reloads      # ProgressSync (A1) ou notre propre détection
        if r > self.reloads_seen:
            self.reloads_seen = r
            self.grant.reload()
        if v.ok and tboxes is not None:
            self.cache.update(mem, tboxes, v.stage, v.cur_idx, now)
        for ev in self.watch.tick(v, self.cache, last_busy, now, keys_counted=keys_counted,
                                  inv_counted=self.grant.owned is not None, counted=self.grant.counted):
            msg = dict(ev, type="coffre", group=self.group)
            key = (ev["k"], ev["i"], ev["n"])
            if len(self.mine) < self.MINE_MAX:
                self.mine[key] = msg
            with self._out_lock:
                self.out.append(msg)
            self.notes.append(f"coffres : tu as ouvert le coffre {ev['n']} (index {ev['i']}) : "
                              f"{item_name(ev['item'])}{' (probable)' if ev.get('prob') else ''} ; envoyé")
        settled = v.ok and now - self.ctx_t >= self.SETTLE and not v.busy
        written = self.grant.tick(mem, v, self.cache, keys_shared, can_write, settled, self.watch.applied, now)
        self.refresh.a_appliquer = set(self.grant.a_appliquer)
        if sm is not None:
            try:
                self.refresh.step(sm, mem, tboxes, v, ctx=ctx, free=free and not v.busy,
                                  settled=room_settled and settled, can=can_mailbox, can_mem=can_write)
            except Exception as exc:          # lecture aberrante : jamais fatale
                self.notes.append(f"coffres : erreur ignorée ({type(exc).__name__}: {exc})")
        return written

    def contact(self, now: float | None = None) -> bool:
        """Un coffre ouvert ailleurs est affiché ouvrable au contact de Link : le client tient alors le drapeau
        « coffres fermés » du pack (bit 19) pour qu'il ne puisse pas être rouvert. Vu il y a moins de CONTACT_TTL s
        (ChestRefresh tourne à 2 Hz)."""
        if not self.enabled or not self.refresh.enabled:
            return False
        now = time.time() if now is None else now
        return now - self.refresh.contact_t <= self.CONTACT_TTL

    CONTACT_TTL = 1.5

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        v = self.view
        where = f"{v.stage} (index {v.cur_idx})" if v.ok else v.reason
        s = f"{where} ; {self.watch.n_events} ouvert(s) ici ; {self.grant.status()}"
        if self.refresh.enabled:
            s += f" ; {self.refresh.status()}"
        if self.grant.granter is None and len(self.grant.queue):
            s += " ; pack v19 requis pour les objets"
        return s
