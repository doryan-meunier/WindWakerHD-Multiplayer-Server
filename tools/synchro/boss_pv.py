"""Synchro v2, module 5 (B1) et synchro v3 : PV de boss partagés, chacun son boss (--boss-pv, activé
par --coop-donjon, --no-boss-pv). Table : Gohma, Kalle Demos, Gohdan (tête), Roi Cuirasse, Ganon
spectral, Ganon marionnette (trois formes), Molgera (compteur de grosses blessures).

Études : docs/etudes/sync2_ia_boss_cine.md §3.b « b1 » et docs/etudes/sync3_boss_pv.md (pour chaque
boss : proc, champ, création, test de mort vérifiés dans le HD ; la section « Vérification » finale
de chaque étude prime). Exclus et pourquoi (Jalhalla, Ganondorf, Grand Octo, Mothula, Darknut,
mains de Gohdan, lianes de Kalle Demos, larves, petits Poe) : même étude.

Ce que les joueurs voient : chacun combat SON boss (filtre A9 inchangé : le boss n'est jamais une
marionnette), mais un coup qui retire des PV chez l'un les retire chez tous. **Aucun boss ne meurt
d'une écriture** : on n'écrit jamais en dessous du ``plancher`` du boss, et la mort n'est testée par
le jeu que dans son propre traitement des coups. Quand un autre l'a vaincu, il reste chez soi un
« coup de grâce » à donner (``conseil`` de la table : briser la carapace de Gohma puis l'œil, etc.).

* PV : ``fopAc_ac_c`` ``max_health`` +0x3A0, ``health`` +0x3A1 (s8) [HD-ASM : écrits par le create de
  chaque boss de la table, voir l'étude]. ``plancher`` 1 quand le jeu fait ``if (health != 0)
  { health--; if (health == 0) fin }`` (Ganon marionnette : 0 bloquerait le combat) et pour rester
  dans les valeurs que le jeu produit lui-même (Kalle Demos, Gohdan, Roi Cuirasse, Ganon spectral) ;
  Gohma garde 0 (module 5, ``health -= dégâts ; if (health <= 0)``).
* Molgera : ``health`` ne sert à rien (jamais testé) ; sa mort vient du compteur s8 de grosses
  blessures +0x1E2D (``>= 4`` : mort, ``>= 2`` : phase 2) [HD-ASM]. PV partagés = 4 - compteur, le
  compteur n'est écrit que vers le HAUT et jamais au-delà de 3 (plancher 1).
* Plusieurs acteurs du même proc : seul celui dont ``max_health`` vaut la valeur attendue est
  partagé (tête de Gohdan 3, pas les mains 4 ; Ganon spectral 30, pas les clones 0 ni les
  apparitions à 100) ; les autres maxima connus (``autres_max``) sont ignorés sans message (aussi le
  Roi Cuirasse à 0 pendant la montée de la tour, avant que ``t_down`` ne pose 20/20). Quand un autre
  l'a vaincu, « X est à 1 PV chez toi … » est dit une fois, même si le nôtre y était déjà.
* Client (``BossPvSync``) : lit les PV des boss de la table (même filtrés par A9), envoie
  ``boss {group, stage, key, hp, max}`` quand la valeur baisse sous ce que l'on connaît, et un
  rappel toutes les ``RAPPEL`` s. Reçoit le minimum du serveur et écrit ``max(plancher, reçu)`` :
  jamais vers le haut, seulement si l'acteur a les PV max attendus, hors événement (``mMode == 0``
  et ``mbEndProc == 0``, relu juste avant) et hors changement de stage, boss présent localement
  (en-tête relu : ``mBsType``, id de process, proc), PV locaux au-dessus du plancher. Pas d'écho :
  une valeur écrite (ou reçue) n'est jamais renvoyée comme un coup. Un boss dont les PV remontent
  tout seuls (Ganon marionnette réactivé à 3, masque du Roi Cuirasse remis à 20) est ramené au
  minimum commun au tick suivant (hors événement).
* Serveur (``BossPvTable``) : minimum par (groupe, stage, clé) ; une baisse est relayée aux autres
  membres du groupe présents dans la stage ; un rapport plus haut que le minimum reçoit le minimum
  en réponse (joueur arrivé plus tard, boss recréé après un Game Over) ; oublié quand plus aucun
  membre du groupe n'est dans la stage (le boss revit si personne ne l'a battu).
* Seule écriture mémoire de ce module : un octet (``health`` +0x3A1, ou le compteur de Molgera
  +0x1E2D) d'un boss de la table.
* Pack : rien (Python seul, compatible pack v17).

Synchro v4 (boss restants, étude §« Synchro v4 ») : trois boss de plus dans la table, avec une
**garde** propre (fenêtre d'écriture et plancher qui dépendent de la phase locale, relus dans
l'acteur juste avant chaque écriture) :

* Jalhalla (211, corps : type +0x4F8 == 0) : ``health`` = nombre de petits Poe restants (15). On
  n'écrit que corps entier (action +0x560 à 0 MOVE ou 1 ATTACK : jamais pendant la chute, la
  séparation, le regroupement ni une démo) et jamais sous le plancher de la phase locale (+0x512 :
  11, 6 puis 1) : le prochain Poe tué chez soi fait franchir le seuil au jeu lui-même, qui change
  de phase tout seul. Jamais 0 (le jeu divise par ``health``).
* Ganondorf (246) : ``health`` 75, forcés à 50 puis 25 par le jeu aux changements de phase (octet
  +0x60C : 0, 1, 2). Planchers 51 (phase 0) et 26 (phase 1) ; **rien en phase 2** (``health < 25``
  y déclenche la fin scénarisée : elle reste au jeu de chacun).
* Yeux du Grand Octo (226) : 4 PV par œil, plancher 1 ; clé = salle + rang de l'œil dans la table
  du Grand Octo (225, ids des yeux +0x25C0), parce que tous les yeux ont les mêmes paramètres.
* Mothula (204) et Darknut (191) ne sont pas des boss A9 : ce sont des ennemis partagés par
  l'autorité (un seul monstre pour tous). ``FIN_LOCALE`` : un coup distant ne les fait jamais passer
  sous 1 PV, le dernier coup revient au joueur chez qui le monstre vit, pour que sa vraie routine de
  mort tourne (armure, ailes, switch de la porte, objet lâché).
* Lianes de Kalle Demos, larves de Molgera : toujours exclues (étude : pas de PV lus par le jeu pour
  les lianes ; larves créées à la volée, sans identité commune entre les joueurs).
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass

from jeu import progress as prog

OFF_BSTYPE, OFF_PC_ID, OFF_PROC = 0x00, 0x04, 0x08
OFF_MAX_HEALTH, OFF_HEALTH = 0x3A0, 0x3A1      # s8 [HD-ASM : create de chaque boss de la table]
OFF_MOLGERA_COUPS = 0x1E2D                     # s8, GC m1BB5 + 0x278 [HD-ASM 0x021000A4 / 0x02100150]
# --- synchro v4 [HD-ASM, voir tests/test_boss_pv_v4.py] ---
OFF_JAL_TYPE = 0x4F8            # u8 : 0 corps (seul à recevoir 15/15, 0x020D63DC)
OFF_JAL_PHASE = 0x512           # s16 : 0, 1, 2 (seuils 10, 5, 0 : table 0x1000B3BC, lbzx 0x020D8984)
OFF_JAL_ACTION = 0x560          # s16 : 0 MOVE, 1 ATTACK (aiguillage 0x020D3D84), 4 séparation (0x020D8510)
JAL_PLANCHERS = (11, 6, 1)      # seuil de la phase + 1 : le jeu franchit lui-même le seuil
JAL_PLAFONDS = (15, 10, 5)
OFF_GND_PHASE = 0x60C           # s8 : 0, 1, 2 (0x02156700, 0x02156950, 0x02159E40)
GND_PLANCHERS = (51, 26)        # phase 2 : aucune écriture (health < 25 lance la fin, 0x02159300)
GND_PLAFONDS = (75, 50)
OFF_PARENT_ID = 0x2E8           # u32 : id de process du parent [HD-ASM œil 0x0211D924]
OFF_OCTA_YEUX = 0x25C0          # 12 x u32 : ids des yeux, dans le Grand Octo [HD-ASM 0x02119C08]
OCTA_NB_YEUX = 12
PROC_OCTA, PROC_OEIL = 225, 226
PROC_POE = 212                           # petits Poe (ceux de Jalhalla : synchro/boss_mort.py les fait mourir avec lui)
PROCS_PARENTS = frozenset({PROC_OCTA, PROC_POE})   # acteurs à fournir à ``tick`` en plus des boss
# Ennemis partagés par l'autorité dont un coup DISTANT ne donne jamais le dernier coup (apply_hit du
# client) : Darknut (191 : armure et PV réécrits autour des coups, switch à la mort) et Mothula (204)
FIN_LOCALE = frozenset({191, 204})
PROCS_V4 = frozenset({211, 246, PROC_OEIL})   # coupés ensemble par --no-boss-restants
RAPPEL = 2.0                                   # s : rappel de notre valeur (arrivée tardive, serveur)
# Messages « boss » émis au plus par seconde (fenêtre glissante). Le serveur en accepte BOSS_RATE = 8 par seconde
# et par joueur et JETTE le reste sans rien dire ; un Grand Octo a jusqu'à 12 yeux, donc 12 clés d'un coup
# (relecture adverse : 4 yeux n'arrivaient jamais, et un coup donné dans la seconde suivant une salve non plus).
# Strictement sous 8 pour garder de la marge avec la gigue du réseau et rester compatible avec un ancien serveur.
DEBIT = 5
KEY_MAX = 24


@dataclass(frozen=True)
class BossPv:
    nom: str
    max_pv: int                 # PV max partagés (valeur de création ; compteur : sa limite)
    offset: int = OFF_HEALTH    # champ des PV (s8)
    conseil: str = ""           # comment l'achever chez soi quand un autre l'a vaincu
    plancher: int = 0           # valeur la plus basse jamais écrite (la mort reste au jeu)
    max_acteur: int | None = None          # max_health (+0x3A0) attendu ; None : max_pv
    autres_max: frozenset = frozenset()    # max_health d'autres parties du même proc (ignorées)
    compteur: bool = False      # PV = max_pv - octet à ``offset`` (compteur de coups qui monte)
    vaincu: int = 0             # un minimum <= vaincu : un autre joueur l'a vaincu (ou en est à la fin)

    @property
    def ident(self) -> int:
        return self.max_pv if self.max_acteur is None else self.max_acteur


_MARIONNETTE = "une flèche dans son point faible bleu chez toi (à 1 PV, une seule suffit)"

# Liste blanche : un boss n'y entre qu'après lecture de son décompilé ET vérification dans le code HD
# (create, champ, test de mort, autres écritures du champ) : docs/etudes/sync3_boss_pv.md. Procs HD :
# table des noms du RPX 0x101EBFDC et profils d'acteurs (Bmdhand/Bgn2/Bgn3 n'y ont pas de nom).
BOSS_PV: dict[int, BossPv] = {
    234: BossPv("Gohma", 10, OFF_HEALTH,
                "brise sa carapace chez toi (fais tomber le bloc du plafond avec la queue du dragon), "
                "puis un coup à l'œil pour l'achever"),
    235: BossPv("Kalle Demos", 15, conseil="coupe toutes ses lianes chez toi pour le faire tomber, "
                "puis un coup dans son cœur", plancher=1),
    240: BossPv("Gohdan", 3, conseil="crève ses deux yeux et assomme ses deux mains chez toi, puis fais-lui "
                "avaler une bombe", plancher=1, autres_max=frozenset({4})),
    # max 0 pendant la montée de la tour (create : m2B4 forcé à 1, branche T_FLY sans PV, 0x02069D40) et
    # après sa défaite (AFTER_FIGHT) : 20/20 posés par t_down (0x0206D5EC) au début du vrai combat
    238: BossPv("Roi Cuirasse", 20, conseil="brise son masque chez toi (marteau), puis un coup sur sa crête",
                plancher=1, autres_max=frozenset({0})),
    241: BossPv("Ganon spectral", 30, conseil="étourdis-le chez toi (renvoie sa boule d'énergie), puis un coup "
                "d'Épée de légende", plancher=1, autres_max=frozenset({0, 100})),
    243: BossPv("Ganon marionnette", 3, conseil=_MARIONNETTE, plancher=1, autres_max=frozenset({0})),
    244: BossPv("Ganon marionnette (araignée)", 3, conseil=_MARIONNETTE, plancher=1),
    245: BossPv("Ganon marionnette (serpent)", 3, conseil=_MARIONNETTE, plancher=1),
    217: BossPv("Molgera", 4, OFF_MOLGERA_COUPS, "attrape sa langue au grappin et frappe-la chez toi jusqu'à "
                "ce qu'il retombe", plancher=1, max_acteur=12, compteur=True),
    # --- synchro v4 : gardes dans GARDES (fenêtre d'écriture et plancher selon la phase locale) ---
    # autres parties du proc big_pow (lanterne, boule, Poe accroché : type +0x4F8 != 0) : max 0
    211: BossPv("Jalhalla", 15, conseil="éclaire-le, lance-le contre les piques chez toi et tue le dernier petit "
                "Poe", plancher=1, autres_max=frozenset({0})),
    246: BossPv("Ganondorf", 75, conseil="la fin du combat reste à faire chez toi (la flèche de Zelda renvoyée "
                "par ton bouclier, puis la parade)", plancher=25, vaincu=24),
    226: BossPv("Œil du Grand Octo", 4, conseil="un seul projectile dans cet œil chez toi suffit", plancher=1),
}


def _s16(mem, addr: int) -> int | None:
    raw = mem.try_read(addr, 2)
    return None if raw is None else struct.unpack(">h", raw)[0]


def _s8(mem, addr: int) -> int | None:
    raw = mem.try_read(addr, 1)
    return None if raw is None else struct.unpack(">b", raw)[0]


def garde_jalhalla(mem, base: int) -> int | None:
    """Plancher d'écriture de Jalhalla maintenant, None si ce n'est pas le moment : corps (type 0),
    entier (action 0 ou 1), phase connue, PV dans la plage de cette phase."""
    typ = mem.try_read(base + OFF_JAL_TYPE, 1)
    act, phase, hp = _s16(mem, base + OFF_JAL_ACTION), _s16(mem, base + OFF_JAL_PHASE), _s8(mem, base + OFF_HEALTH)
    if typ is None or typ[0] != 0 or act not in (0, 1) or phase not in (0, 1, 2) or hp is None:
        return None
    if not JAL_PLANCHERS[phase] <= hp <= JAL_PLAFONDS[phase]:
        return None
    return JAL_PLANCHERS[phase]


def garde_ganondorf(mem, base: int) -> int | None:
    """Plancher d'écriture de Ganondorf maintenant ; None en phase 2 (fin scénarisée) ou si les PV ne
    sont pas dans la plage de la phase locale (changement de phase en cours)."""
    phase, hp = _s8(mem, base + OFF_GND_PHASE), _s8(mem, base + OFF_HEALTH)
    if phase not in (0, 1) or hp is None:
        return None
    if not GND_PLANCHERS[phase] <= hp <= GND_PLAFONDS[phase]:
        return None
    return GND_PLANCHERS[phase]


GARDES = {211: garde_jalhalla, 246: garde_ganondorf}


def cle_oeil(mem, b: dict, acteurs: list) -> int | None:
    """Pseudo-paramètres d'un œil du Grand Octo : (salle << 8) | rang dans la table des yeux de SON
    Grand Octo (les yeux ont tous les mêmes paramètres). None si le rang est introuvable."""
    raw = mem.try_read(b["base"] + OFF_PARENT_ID, 4)
    if raw is None:
        return None
    parent = struct.unpack(">I", raw)[0]
    for o in acteurs:
        if o.get("proc") != PROC_OCTA or o.get("pid") != parent:
            continue
        ids = mem.try_read(o["base"] + OFF_OCTA_YEUX, 4 * OCTA_NB_YEUX)
        if ids is None:
            return None
        ids = struct.unpack(f">{OCTA_NB_YEUX}I", ids)
        if ids.count(b["pid"]) != 1:
            return None
        return ((int(b.get("room", 0)) & 0xFF) << 8) | ids.index(b["pid"])
    return None


def remote_floor(proc: int) -> int:
    """PV minimum laissés par un coup DISTANT à un ennemi partagé par l'autorité (0 : il peut le tuer)."""
    return 1 if proc in FIN_LOCALE else 0


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def boss_key(proc: int, params: int) -> str:
    return f"{proc}:{params & 0xFFFFFFFF:08x}"


def key_proc(key) -> int | None:
    """Numéro de proc d'une clé ``<proc>:<params>`` de la table, None sinon."""
    if not isinstance(key, str) or len(key) > KEY_MAX:
        return None
    head, sep, tail = key.partition(":")
    if not sep or len(tail) != 8 or not head.isdigit():
        return None
    try:
        int(tail, 16)
    except ValueError:
        return None
    proc = int(head)
    return proc if proc in BOSS_PV else None


def valid_boss(msg) -> dict | None:
    """Message ``boss`` validé (None si invalide ou boss hors table)."""
    if not isinstance(msg, dict):
        return None
    stage, key, hp, mx = msg.get("stage"), msg.get("key"), msg.get("hp"), msg.get("max")
    if not isinstance(stage, str) or not 0 < len(stage) <= 8:
        return None
    proc = key_proc(key)
    if proc is None or not (_int(hp) and _int(mx)):
        return None
    if not 0 < mx <= 127 or mx != BOSS_PV[proc].max_pv or not -128 <= hp <= 127:
        return None
    return {"stage": stage, "key": key, "hp": max(0, min(hp, mx)), "max": mx}


def read_header(mem, base: int) -> tuple[int, int, int] | None:
    """(id de process, proc, PV) relus à l'adresse ``base`` ; None si l'acteur a été détruit."""
    head = mem.try_read(base + OFF_BSTYPE, OFF_PROC + 2)
    hp = mem.try_read(base + OFF_MAX_HEALTH, 2)
    if head is None or hp is None or struct.unpack_from(">I", head, OFF_BSTYPE)[0] == 0:
        return None
    return (struct.unpack_from(">I", head, OFF_PC_ID)[0], struct.unpack_from(">h", head, OFF_PROC)[0],
            struct.unpack_from(">b", hp, 1)[0])


def read_pv(mem, base: int, t: BossPv) -> int | None:
    """PV partagés de l'acteur (``health``, ou ``max_pv - compteur``) ; None si illisible ou aberrant."""
    raw = mem.try_read(base + t.offset, 1)
    if raw is None:
        return None
    v = struct.unpack(">b", raw)[0]
    if t.compteur:
        if not 0 <= v <= t.max_pv:
            return None
        return t.max_pv - v
    return v


def write_health(mem, base: int, pid: int, proc: int, value: int, plancher: int | None = None) -> str:
    """Écrit les PV partagés = max(plancher, value) si l'acteur est toujours ce boss, avec les PV max
    attendus, et si la valeur BAISSE. ``plancher`` : plancher du moment, jamais sous celui de la
    table. Un boss à garde est relu ici, juste avant l'écriture. Renvoie '' si écrit, sinon la raison."""
    t = BOSS_PV.get(proc)
    if t is None:
        return "boss hors table"
    h = read_header(mem, base)
    if h is None or h[0] != pid or h[1] != proc:
        return "boss introuvable (détruit ou remplacé)"
    raw = mem.try_read(base + OFF_MAX_HEALTH, 1)
    if raw is None or struct.unpack(">b", raw)[0] != t.ident:
        return "PV max inattendus"
    sol = t.plancher if plancher is None else max(t.plancher, plancher)
    garde = GARDES.get(proc)
    if garde is not None:
        g = garde(mem, base)          # relu juste avant d'écrire : action, phase, plage de PV
        if g is None:
            return "pas le moment (phase ou action du boss)"
        sol = max(sol, g)
    value = max(sol, value)
    cur = read_pv(mem, base, t)
    if cur is None:
        return "valeur illisible ou aberrante"
    if cur <= sol:
        return "déjà à 0 chez toi" if sol == 0 else "déjà au dernier PV chez toi"
    if value >= cur:
        return "pas plus bas que chez toi"
    if t.compteur:
        mem.write(base + t.offset, struct.pack(">b", t.max_pv - value))   # compteur : seulement vers le haut
    else:
        mem.write(base + t.offset, struct.pack(">b", value))
    return ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class BossPvSync:
    """Côté client. ``tick(mem, now, stage, bosses, free, can)`` à chaque échantillon (thread de
    travail) : ``bosses`` = [{pid, base, proc, params, hp, max}] des boss de la table chargés ;
    ``free`` = jeu libre (anti-rebond A11, pas de changement de stage) ; ``can`` = écriture permise.
    ``receive(msg, me)`` au fil des messages ; ``take_out()`` / ``take_notes()`` au tick."""

    def __init__(self, enabled: bool = False, group: str = "default", clock=time.monotonic,
                 exclus: frozenset = frozenset()):
        self.enabled, self.group, self.clock = enabled, group, clock
        self.exclus = frozenset(exclus)       # procs de la table non partagés ici (--no-boss-restants)
        self.communs: frozenset = frozenset()  # procs dont la mort est commune (synchro/boss_unique.py) : jamais
                                               # de « coup de grâce à donner chez toi » pour eux
        self.stage: str | None = None
        self.known: dict[str, int] = {}       # clé -> plus petite valeur connue (envoyée ou reçue)
        self.target: dict[str, int] = {}      # clé -> minimum reçu du serveur
        self.pids: dict[str, int] = {}        # clé -> id de process de notre boss (nouvelle instance : on oublie)
        self.sent_t: dict[str, float] = {}
        self.by: dict[str, str] = {}          # clé -> nom du dernier joueur qui l'a fait baisser
        self.out: list[dict] = []             # en attente : les baisses d'abord, puis les rappels
        self.urgent: set[str] = set()         # clés de ``out`` dont la valeur est une baisse (pas un rappel)
        self.emis: list[float] = []           # instants des derniers messages rendus par take_out (DEBIT)
        self.notes: list[str] = []
        self.intro: set[str] = set()          # clés déjà présentées dans cette stage
        self.zero_said: set[str] = set()
        self.max_bad: set[str] = set()
        self.palier_said: set[tuple[str, int]] = set()   # (clé, plancher de phase) déjà annoncés
        self.written = 0
        self.sent = 0
        self.received = 0
        self.last: dict[str, tuple[int, int]] = {}   # clé -> (pv, max) vus chez nous

    # --- réseau ------------------------------------------------------------
    def take_out(self, now: float | None = None) -> list[dict]:
        """Au plus ``DEBIT`` messages par seconde glissante ; le reste attend (une valeur par clé, les baisses
        devant). Le rappel d'une clé compte à partir de son émission réelle."""
        if not self.out:
            return []
        now = self.clock() if now is None else now
        self.emis = [t for t in self.emis if 0 <= now - t < 1.0]
        n = max(0, DEBIT - len(self.emis))
        out, self.out = self.out[:n], self.out[n:]
        for m in out:
            self.emis.append(now)
            self.urgent.discard(m["key"])
            self.sent_t[m["key"]] = now
        return out

    def take_notes(self) -> list[str]:
        n, self.notes = self.notes, []
        return n

    def _send(self, key: str, hp: int, mx: int, now: float, urgent: bool = True) -> None:
        msg = {"type": "boss", "group": self.group, "stage": self.stage, "key": key, "hp": hp, "max": mx}
        for i, m in enumerate(self.out):
            if m["key"] == key:               # une seule valeur en attente par clé ; elle garde sa place,
                if not urgent or key in self.urgent:                 # sauf un rappel qui devient une baisse
                    self.out[i] = msg
                    self.sent_t[key] = now
                    self.sent += 1
                    return
                del self.out[i]
                break
        if urgent:
            self.urgent.add(key)
            self.out.insert(sum(1 for m in self.out if m["key"] in self.urgent), msg)
        else:
            self.out.append(msg)
        self.sent_t[key] = now
        self.sent += 1

    def receive(self, msg: dict, me: int | None = None) -> int:
        """Minimum reçu ; renvoie 1 s'il fait baisser ce qu'on connaissait."""
        if not self.enabled or msg.get("group", self.group) != self.group:
            return 0
        if me is not None and msg.get("from") == me:
            return 0
        rec = valid_boss(msg)
        if rec is None or rec["stage"] != self.stage or key_proc(rec["key"]) in self.exclus:
            return 0
        key, hp = rec["key"], rec["hp"]
        old = self.target.get(key)
        if old is not None and hp >= old:
            return 0
        self.target[key] = hp
        self.known[key] = min(self.known.get(key, hp), hp)
        self.received += 1
        nom = BOSS_PV[key_proc(key)].nom
        who = msg.get("nom")
        if isinstance(who, str) and who and hp < rec["max"]:
            self.by[key] = who[:24]
            self.notes.append(f"{nom} : {hp}/{rec['max']} PV ({who[:24]} a touché)")
        return 1

    def reset_network(self) -> None:
        """Connexion perdue : minimums oubliés ; notre valeur repart au premier tick."""
        self.target.clear()
        self.known.clear()
        self.sent_t.clear()
        self.out.clear()
        self.urgent.clear()

    # --- jeu -----------------------------------------------------------------
    def _new_stage(self, stage: str | None) -> None:
        self.stage = stage
        for d in (self.known, self.target, self.pids, self.sent_t, self.by, self.last):
            d.clear()
        self.intro.clear()
        self.zero_said.clear()
        self.palier_said.clear()
        self.out.clear()
        self.urgent.clear()

    def tick(self, mem, now: float | None, stage: str | None, bosses: list | None, free: bool, can: bool) -> int:
        """Renvoie le nombre d'écritures faites."""
        if not self.enabled:
            return 0
        now = self.clock() if now is None else now
        if stage != self.stage:
            self._new_stage(stage)
        if stage is None or not bosses:
            return 0
        writes = 0
        for b in bosses:
            t = BOSS_PV.get(b.get("proc"))
            if t is None or b["proc"] in self.exclus:
                continue
            params = b.get("params", 0)
            if b["proc"] == PROC_OEIL:
                params = cle_oeil(mem, b, bosses)     # rang de l'œil dans son Grand Octo
                if params is None:
                    continue
            key = boss_key(b["proc"], params)
            if b.get("max") != t.ident:
                if b.get("max") in t.autres_max:
                    continue                  # autre partie du même proc (mains de Gohdan, clones…)
                if key not in self.max_bad:
                    self.max_bad.add(key)
                    self.notes.append(f"{t.nom} : PV max {b.get('max')} au lieu de {t.ident}, PV non partagés")
                continue
            if self.pids.get(key) != b["pid"]:
                # nouvelle instance (entrée, Game Over) : ce qu'on avait envoyé ne vaut plus pour elle
                self.pids[key] = b["pid"]
                self.known.pop(key, None)
                self.sent_t.pop(key, None)
                self.zero_said.discard(key)
                self.palier_said = {x for x in self.palier_said if x[0] != key}
            if key not in self.intro:
                self.intro.add(key)
                fin = "à 0 PV" if t.plancher == 0 else "quand un autre l'a vaincu"
                if b["proc"] in self.communs:
                    self.notes.append(f"PV de {t.nom} partagés : chaque coup qui compte chez l'un compte pour tous")
                else:
                    self.notes.append(f"PV de {t.nom} partagés (--boss-pv) : chaque coup qui compte chez l'un "
                                      f"compte "
                                      f"pour tous ; {fin}, {t.conseil}")
            if t.compteur:
                hp = read_pv(mem, b["base"], t)       # Molgera : le compteur, pas health
                if hp is None:
                    continue
            else:
                hp = b["hp"]
            hp = max(0, min(int(hp), t.max_pv))
            self.last[key] = (hp, t.max_pv)
            # 1. notre valeur
            known = self.known.get(key)
            if known is None or hp < known:
                self.known[key] = hp
                # une valeur entamée passe devant ; l'annonce d'un boss intact attend son tour comme un rappel
                self._send(key, hp, t.max_pv, now, urgent=hp < t.max_pv)
            elif now - self.sent_t.get(key, -1e9) >= RAPPEL:
                self._send(key, hp, t.max_pv, now, urgent=False)
            # 2. le minimum des autres
            raw = self.target.get(key)
            if raw is None:
                continue
            if raw <= t.vaincu and 0 < hp <= t.plancher:
                # déjà au dernier PV chez nous (coup précédent partagé) : rien à écrire, mais le coup de
                # grâce reste à donner, on le dit (relecture adverse : sinon jamais dit pour 3 -> 2 -> 1 -> 0)
                self._said_zero(key, t, hp)
            sol = t.plancher
            garde = GARDES.get(b["proc"])
            if garde is not None:
                g = garde(mem, b["base"])
                if g is None:
                    continue                          # chute, séparation, changement de phase, fin scénarisée
                sol = max(sol, g)
            want = max(sol, raw)                      # jamais sous le plancher : la mort reste au jeu
            if want >= hp or hp <= sol or not free or not can:
                continue
            ev = prog.read_event(mem)
            if ev is None or ev.busy:
                continue
            nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
            if nxt is None or nxt[0]:
                continue
            why = write_health(mem, b["base"], b["pid"], b["proc"], want, sol)
            if why:
                continue
            writes += 1
            self.written += 1
            self.last[key] = (want, t.max_pv)
            if raw <= t.vaincu and want <= t.plancher:
                self._said_zero(key, t, want)
            elif want > raw and (key, want) not in self.palier_said:
                self.palier_said.add((key, want))
                self.notes.append(f"{t.nom} : {raw}/{t.max_pv} PV chez les autres, {want} chez toi jusqu'à ce que "
                                  f"tu passes sa phase (le prochain coup qui compte la termine)")
        return writes

    def _said_zero(self, key: str, t: BossPv, hp: int) -> None:
        """Une fois par instance : un autre a vaincu le boss, le nôtre est à ``hp`` (plancher)."""
        if key in self.zero_said or key_proc(key) in self.communs:
            return
        self.zero_said.add(key)
        self.notes.append(f"{t.nom} est à {hp} PV chez toi ({self.by.get(key, 'un autre joueur')} a donné "
                          f"le dernier coup) : {t.conseil}")

    def status(self) -> str:
        if not self.last:
            return f"aucun boss partagé ici ({self.sent} envoi(s), {self.received} reçu(s), {self.written} écrit(s))"
        parts = []
        for key, (hp, mx) in sorted(self.last.items()):
            nom = BOSS_PV[key_proc(key)].nom
            mn = self.target.get(key)
            parts.append(f"{nom} {hp}/{mx} PV" + (f" (minimum commun {mn})" if mn is not None else ""))
        return ", ".join(parts) + f" ; {self.written} écriture(s)"


# ---------------------------------------------------------------------------
# Serveur
# ---------------------------------------------------------------------------
class BossPvTable:
    """Minimum par (groupe, stage, clé). ``report`` renvoie (genre, enregistrement) :
    « bas » (nouveau minimum : relais aux autres), « haut » (plus haut que le minimum : le minimum
    est renvoyé à l'auteur), « egal » (rien), « max » (PV max différents : ignoré)."""

    def __init__(self):
        self.hp: dict[tuple[str, str, str], dict] = {}

    def report(self, group: str, rec: dict, nom: str) -> tuple[str, dict | None]:
        k = (group, rec["stage"], rec["key"])
        old = self.hp.get(k)
        if old is None or (rec["max"] == old["max"] and rec["hp"] < old["hp"]):
            new = {"stage": rec["stage"], "key": rec["key"], "hp": rec["hp"], "max": rec["max"], "nom": nom}
            self.hp[k] = new
            return ("bas" if old is not None else "nouveau"), new
        if rec["max"] != old["max"]:
            return "max", None
        if rec["hp"] > old["hp"]:
            return "haut", old
        return "egal", None

    def gc(self, present: set[tuple[str, str]]) -> list[tuple[str, str, str]]:
        """Oublie les boss des stages où plus aucun membre du groupe n'est présent."""
        gone = [k for k in self.hp if (k[0], k[1]) not in present]
        for k in gone:
            del self.hp[k]
        return gone
