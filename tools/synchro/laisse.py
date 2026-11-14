"""Mode Hanteurs, phase 1 : « possession par laisse » (étude docs/etudes/mode_hanteurs.md §1 (c), §5, §11 phase 1 ;
la section « Vérification » finale prime). Client : ``--share-possession`` (activé par ``--coop-donjon``, comme le
mode Hanteurs lui-même), ``--no-possession`` pour couper. Serveur : réglage ``possession`` de la partie Hanteurs
(activé par défaut avec ``--mode-hanteurs``), voir ``hanteurs.Partie.posseder``.

Ce que les joueurs voient. Un Hanteur **se manifeste** (F9, ou bouton du lanceur) : le suivi continu s'arrête, il
marche librement avec son Link caché, et les Link de sa salle reçoivent « une présence rôde… ». Près d'un monstre
(Bokoblin, Moblin ; Chuchu avec l'option des espèces non vérifiées), il le **possède** (F4) : pendant au plus
``possession_max`` secondes (45 par défaut), le monstre le suit « en laisse » (vitesse plafonnée par espèce), se
tourne vers le Link le plus proche quand il en a un à portée, et chaque coup d'épée du Hanteur le lance vers ce Link
(« élan »). La possession coûte des points ; les dégâts que fait le monstre possédé rapportent au Hanteur ; la mort
du monstre, F4 de nouveau, un changement de salle, plus de 1 500 u entre le Hanteur et le monstre ou la durée
maximale y mettent fin.

Comment (architecture (c) de l'étude : un seul exemplaire vivant, chez l'autorité de la zone) :

* le serveur relaie la position et l'orientation du Hanteur (son ``state``, 20 Hz) à l'**autorité de la zone**
  (``hlaisse``), avec un compteur d'ordres (coups d'épée du Hanteur, lus dans ``mCutType`` Link+0x3AC [LIVE,
  ``cc_pl_cut_bit_get`` 0x02518B28 : ``lbz r0, 0x3ac(r12)``], remis à 0 par le jeu dès que Link n'attaque plus
  [DECOMP ``d_a_player_main.cpp`` : ``if (checkResetFlg0(NOT_ATTACKING)) mCutType = CUT_TYPE_NONE``]) ;
* chez l'autorité, ``LaisseHote`` met le monstre dans un **emplacement d'ennemi du pack** (le même que les
  marionnettes, ``wwmp_enemies``), mais avec l'IA **active** (octet +0x17 bit 0 à 0) : à chaque image le jeu
  impose la position tenue en laisse et l'orientation ; l'IA native décide quand et comment frapper ; les dégâts
  sur le Link autorité sont natifs, ceux sur les autres Link passent par A3 (cible forcée : le Link visé par la
  laisse, ``cibles.AggroSync.choose(force=...)``) puis A1/A2 (TG du fantôme, ``player_hit``, CALL 9) ;
* PV : avec le pack v19 à jour (bit 2 des capacités, ``Mailbox.enemy_hp_free``), l'emplacement **n'impose pas** les
  PV (bit 0x02 de +0x17) : les coups natifs du Link autorité comptent. Pack v17 ou v19 plus ancien (mode dégradé,
  étude §1 « parade v17 ») : les PV imposés suivent la plus basse valeur lue dans le jeu (un coup natif vu entre deux
  lectures compte) et les coups d'épée du Link autorité sont aussi détectés par la lame (``detect_hits``) ;
* partout ailleurs (autres Link, Hanteurs), le monstre est la marionnette à pose existante (pose relayée de
  l'autorité) : rien de nouveau ;
* sécurité : laisse relâchée (emplacement vidé, IA libre) dès que l'autorité n'a plus de ``hlaisse`` depuis
  ``TIMEOUT`` s, change de zone, entre en cinématique, perd l'autorité, ou que le monstre meurt (PV <= 0 : sa mort
  native se joue sans rien d'imposé), disparaît ou devient une marionnette. Jamais de déplacement vers un Hanteur à
  un autre étage (écart de hauteur > ``ETAGE``) ; la hauteur ne monte que vers celle du Hanteur, et ne descend vers
  elle que quand le monstre est presque sur lui (même sol) ; sinon la gravité du jeu fait le reste.

Phase 2 (étude §11) : le Hanteur **choisit le Link visé** (F7 pendant la possession, ou bouton « Viser … » du
lobby ; ``hlaisse.cible`` = id du joueur, None = le plus proche). Chez l'autorité, ce Link est visé (orientation,
élan, cible A3 forcée) s'il est visable par A3 (fantôme de la zone, pack v19) ou si c'est l'autorité elle-même ; le
Link local au corps à corps (``MELEE``) reste toujours prioritaire, comme A3 (le recul de son épée se calcule depuis
sa position). Ainsi les coups du monstre possédé touchent **tous** les Link : l'autorité en natif, les autres par
A3 (le monstre poursuit leur fantôme) puis A1/A2 (TG du fantôme, ``player_hit``, CALL 9). Sans pack v19 (ou sans
A3) : seul le Link autorité peut être visé, et blessé (mode dégradé, inchangé).

Phase 3 (« ordres », ``synchro/ordres.py``, pack v19 bit 3 des capacités) : le ``hlaisse`` porte aussi le dernier
ordre du Hanteur (``ord = [seq, geste]`` : « attaque », « lourde », « garde »). Ici, chez l'autorité, l'ordre est
envoyé au jeu par CALL 10 (``EnemyPuppets.ordre``) quand le monstre est au contact du Link visé (ou à la fin de
l'élan) : son IA passe dans l'état d'attaque natif, depuis un état libre seulement (refus du jeu : nouvel essai
toutes les ``ORDRE_ESSAI`` s pendant ``ORDRE_VIE`` s). Un ordre réussi qui fait bondir ou plonger le monstre
(``Ordre.libre``) relâche la laisse pendant ce temps, puis elle reprend. Keese (volant : la laisse le tient
``vol`` u au-dessus du Hanteur) et Darknut (jamais de cible A3 : il ne vise que le Link de l'autorité, son recul
d'armure se calcule depuis la position du vrai Link ; cible forcée sur le Link local tant qu'il est tenu, car « A3
pour tous les monstres », bit 4 du pack, le tournerait sinon vers un fantôme) sont possédables avec l'option des
espèces non vérifiées.
Sans la capacité (pack v17, v19 plus ancien, --no-pack-v19) : l'élan seul, comme avant.

Limites honnêtes (étude §1, §11 phase 1) : sans ordre (ou pack sans la capacité), c'est l'IA qui déclenche
l'attaque ; l'« élan » ne fait que placer le monstre au contact, face au Link, plus vite. Le monstre peut
« patiner » (course sur place) quand son IA veut aller ailleurs. Vitesses plafond et distances de contact : [HYPO],
à mesurer (``speedF`` +0x370, ``wwhd.py hanteurs``).
"""
from __future__ import annotations

import math
import struct
import threading
import time

from synchro import ordres as ordres_mod
from synchro import pilotage


# proc -> espèce possédable (étude §5 : marcheurs seulement en phase 1). vmax en u/s [HYPO : Link court à ~17 u
# par image à 30 images/s ; un Bokoblin moins vite], contact = distance où l'élan s'arrête (portée d'attaque
# approximative), hypo = derrière l'option ``especes_hypo`` de la partie (Chuchu : paramètres non relus).
# Phase 3 : Keese (« vol » : hauteur tenue au-dessus du Hanteur) et Darknut (« a3 » faux : jamais de cible forcée
# vers un fantôme ; il ne vise que le Link de l'autorité), derrière la même option.
# Possession complète, étape 2 (wwmp/pilotage.AUTRES) : Chuchu sorti de l'option (états relus dans le RPX), Peahat
# et Miniblin ; « essai » : possédable seulement si le réglage « espèces à l'essai » de la partie la nomme (Stalfos,
# Kargaroc, Rat ; le Keese et le Darknut gardent en plus leur option d'origine) ; « garde » : même en laisse
# classique (pack v17, --no-pilotage), rien n'est imposé hors de la liste blanche d'états de l'espèce.
POSSEDABLES = {
    189: {"nom": "Bokoblin", "vmax": 420.0, "contact": 110.0, "hypo": False},
    188: {"nom": "Moblin", "vmax": 330.0, "contact": 170.0, "hypo": False},
    206: {"nom": "Chuchu", "vmax": 200.0, "contact": 70.0, "hypo": False, "garde": True},
    215: {"nom": "Keese", "vmax": 500.0, "contact": 60.0, "hypo": True, "vol": 150.0},
    191: {"nom": "Darknut", "vmax": 300.0, "contact": 150.0, "hypo": True, "a3": False},
    209: {"nom": "Peahat", "vmax": 300.0, "contact": 120.0, "hypo": False, "vol": 150.0, "garde": True},
    247: {"nom": "Miniblin", "vmax": 400.0, "contact": 80.0, "hypo": False, "garde": True},
    190: {"nom": "Stalfos", "vmax": 300.0, "contact": 150.0, "hypo": False, "essai": True, "garde": True},
    181: {"nom": "Kargaroc", "vmax": 450.0, "contact": 100.0, "hypo": False, "essai": True, "vol": 200.0,
          "garde": True},
    198: {"nom": "Rat", "vmax": 600.0, "contact": 60.0, "hypo": False, "essai": True, "garde": True},
    # Étape 3, formes spéciales (« formes ») : possédables seulement à l'essai ET pilotées seulement par un pack
    # qui a le bit 17 (carotte seule, épinglage gardé) ; sans lui, ou sans pilotage, RIEN ne leur est imposé
    # (jamais la laisse classique : elle épinglerait un monstre articulé, fixe ou qui se téléporte).
    202: {"nom": "Armos Knight", "vmax": 500.0, "contact": 150.0, "hypo": False, "essai": True, "formes": True},
    203: {"nom": "Armos", "vmax": 500.0, "contact": 120.0, "hypo": False, "essai": True, "formes": True},
    208: {"nom": "Wizzrobe", "vmax": 500.0, "contact": 400.0, "hypo": False, "essai": True, "formes": True},
    212: {"nom": "Poe", "vmax": 250.0, "contact": 120.0, "hypo": False, "essai": True, "formes": True},
    214: {"nom": "Boko Baba", "vmax": 500.0, "contact": 150.0, "hypo": False, "essai": True, "formes": True},
    216: {"nom": "Magtail", "vmax": 500.0, "contact": 150.0, "hypo": False, "essai": True, "formes": True},
    224: {"nom": "ReDead", "vmax": 90.0, "contact": 120.0, "hypo": False, "essai": True, "formes": True},
    227: {"nom": "Octorok", "vmax": 500.0, "contact": 600.0, "hypo": False, "essai": True, "formes": True},
}
RAYON = 250.0            # u : distance max Hanteur -> monstre pour le posséder (étude : 150 ; Link caché, marge)
LAISSE_MAX = 1500.0      # u : au-delà, la possession finit (serveur)
DUREE = 45               # s : durée max par défaut (réglage ``possession_max``)
COUT = 15                # points (× multiplicateur du barème)
RECHARGE = 8.0           # s entre la fin d'une possession et la suivante
TIMEOUT = 0.6            # s sans ``hlaisse`` : l'autorité relâche
VU_MAX = 5.0             # s sans le monstre dans les snapshots de l'autorité : la possession finit (serveur)
ORDRE_PERIODE = 0.3      # s entre deux ordres acceptés
ELAN = 0.9               # s : durée d'un élan (coup d'épée du Hanteur)
ELAN_VITESSE = 1.6       # × vmax pendant l'élan
ELAN_PORTEE = 700.0      # u : élan seulement vers un Link à cette distance du monstre
VISEE = 450.0            # u : un Link à cette distance : le monstre le regarde
MELEE = 250.0            # u : comme cibles.MELEE (le Link local reprend toujours la main de près)
ETAGE = 250.0            # u : écart de hauteur Hanteur/monstre au-delà duquel la laisse ne tire plus
MEME_SOL = 150.0         # u : distance horizontale sous laquelle le monstre peut descendre vers la hauteur du Hanteur
ARRET = 40.0             # u : plus près du Hanteur, le monstre ne bouge plus
DT_MAX = 0.2             # s : pas de temps maximal d'un tick (pas de saut après une pause)
OUBLI = 5.0              # s : un ``hlaisse`` plus vieux est oublié (sa « fin » a pu partir à une autre autorité)
FENETRE_COUP = 0.45      # s (mode dégradé) : un coup natif vu et un coup de lame dans cette fenêtre = un seul coup
OFF_PC_ID, OFF_PROC, OFF_POS, OFF_HP = 0x04, 0x08, 0x314, 0x3A1     # base_process_class / fopAc_ac_c (HD) [LIVE]
OFF_CUT = 0x3AC          # Link : mCutType (u8), mCutCount (u8) +0x3AD [LIVE]
# phase 3 (ordres) : un ordre reçu est tenté au plus ORDRE_VIE s, un essai toutes les ORDRE_ESSAI s ; « attaque » et
# « lourde » partent quand le monstre est à CONTACT_ORDRE × contact du Link visé, ou à la fin de l'élan
ORDRE_VIE = 1.5
ORDRE_ESSAI = 0.1
CONTACT_ORDRE = 1.6
TIMEOUT_ORDRE = 0.5      # s : délai d'un CALL 10 (le fil de travail n'attend jamais plus)
ST_HAUT_DU_CORPS = 14    # Stalfos (d_a_st.cpp, params & 0xF ; create HD 0x02495CF8) : haut du corps coupé en deux
NZ_ORDINAIRE = {0, 0xFF}  # Rat (create HD 0x0230F394 : (params >> 8) & 0xFF, 0xFF ramené à 0) ; autre : rat à bombes
TN_COMPORTEMENTS = {0}   # Darknut (d_a_tn.cpp, params & 0xF) : 0 « rôde » seulement (4 garde une zone, 12-14
                         # scènes et mini-boss, 15 figé dans le temps)


def eligible(proc, params, stage) -> bool:
    """Monstre possédable (en plus de l'espèce) : les règles d'A3 (``cibles.eligible``), sauf le Darknut, exclu d'A3
    mais possédable (il ne vise que l'autorité) : comportement « rôde » seulement, jamais dans une stage exclue."""
    from synchro import boss, cibles
    if (POSSEDABLES.get(proc) or {}).get("formes"):
        # formes spéciales : plusieurs sont écartées d'A3 (Poe, Boko Baba : espèces non vérifiées) mais possédables
        # à l'essai ; jamais un boss, jamais dans une stage exclue (boss, Forteresse) ni dans celle que l'espèce
        # s'interdit (Wizzrobe : démo du mini-boss)
        if boss.is_boss(proc, stage):
            return False
        return stage is None or not (stage in cibles.STAGES_EXCLUES or stage in cibles.STAGES_ESPECE.get(proc, ()))
    if proc == 191:
        if boss.is_boss(proc, stage) or (stage is not None and stage in cibles.STAGES_EXCLUES):
            return False
        try:
            return (int(params) & 0xF) in TN_COMPORTEMENTS
        except (TypeError, ValueError):
            return False
    if proc in (190, 198):
        try:
            p = int(params) & 0xFFFFFFFF
        except (TypeError, ValueError):
            return False
        if proc == 190 and p & 0xF == ST_HAUT_DU_CORPS:
            return False                          # Stalfos : le haut du corps coupé (second acteur du même proc)
        if proc == 198 and (p >> 8) & 0xFF not in NZ_ORDINAIRE:
            return False                          # Rat à bombes : il porte et lance un autre acteur
    return cibles.eligible(proc, params, stage)


def distance_possession(proc, mpos, hpos) -> float:
    """Distance Hanteur -> monstre pour la possession : 3D, sauf pour un volant (horizontale : il vole au-dessus)."""
    if (POSSEDABLES.get(proc) or {}).get("vol"):
        return dist_xz(mpos, hpos)
    return math.dist(mpos, hpos)


def possedables(especes_hypo: bool, essai=(), formes: bool = True) -> dict[int, dict]:
    """Espèces possédables dans cette partie. ``essai`` : process du réglage « espèces à l'essai » (True : toutes) ;
    une espèce nommée là est possédable même sans ``especes_hypo``. ``formes`` faux (partie sans pilotage) : les
    formes spéciales ne sont pas possédables du tout (elles n'existent que pilotées)."""
    def nomme(p):
        return essai is True or (isinstance(essai, (list, tuple, set, frozenset)) and p in essai)
    return {p: e for p, e in POSSEDABLES.items()
            if (formes or not e.get("formes"))
            and (nomme(p) or (not e.get("essai") and (especes_hypo or not e["hypo"])))}


def _pos3(v) -> tuple[float, float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError, OverflowError):      # OverflowError : entier JSON démesuré (10**400)
        return None
    return p if all(math.isfinite(x) and abs(x) < 1e6 for x in p) else None


def angle_vers(src, dst) -> int | None:
    """Angle Y (s16, convention du jeu : avant = (sin, cos) en x, z) de ``src`` vers ``dst`` ; None si confondus."""
    dx, dz = dst[0] - src[0], dst[2] - src[2]
    if dx * dx + dz * dz < 1e-6:
        return None
    a = int(round(math.atan2(dx, dz) * 32768.0 / math.pi)) & 0xFFFF
    return a - 0x10000 if a >= 0x8000 else a


def s16(v) -> int:
    v = int(v) & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def dist_xz(a, b) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


def pas_laisse(cur, cible, hauteur_ref, vmax: float, dt: float) -> tuple[float, float, float]:
    """Prochaine position du monstre : vers ``cible`` (x, z) à ``vmax`` u/s au plus. Hauteur : monte vers
    ``hauteur_ref`` (y du Hanteur, posé au sol) au plus à la même vitesse ; ne descend vers elle que si le monstre
    est presque sur la cible (même sol) ; jamais de déplacement si l'écart de hauteur dépasse ``ETAGE`` (autre
    étage : le monstre reste où il est)."""
    dt = max(0.0, min(DT_MAX, dt))
    x, y, z = cur
    if abs(hauteur_ref - y) > ETAGE:
        return (x, y, z)
    pas = vmax * dt
    d = dist_xz(cur, cible)
    if d > ARRET and pas > 0:
        k = min(1.0, pas / d)
        x, z = x + (cible[0] - x) * k, z + (cible[2] - z) * k
    dy = hauteur_ref - y
    if dy > 0:
        y += min(dy, pas)
    elif dy < 0 and dist_xz((x, y, z), cible) <= MEME_SOL:
        y += max(dy, -pas)
    return (x, y, z)


def pas_vol(cur, cible, hauteur, vmax: float, dt: float) -> tuple[float, float, float]:
    """Volant : vers ``cible`` (x, z) et vers la ``hauteur`` voulue, chacun à ``vmax`` u/s au plus (pas de règle
    d'étage : il vole)."""
    dt = max(0.0, min(DT_MAX, dt))
    x, y, z = cur
    pas = vmax * dt
    d = dist_xz(cur, cible)
    if d > ARRET and pas > 0:
        k = min(1.0, pas / d)
        x, z = x + (cible[0] - x) * k, z + (cible[2] - z) * k
    dy = hauteur - y
    y += max(-pas, min(pas, dy))
    return (x, y, z)


def choisir_victime(mpos, local_pos, autres: dict, voulu: int | None = None
                    ) -> tuple[int, tuple[float, float, float], float] | None:
    """Link visé par un monstre en laisse : (joueur, position, distance), joueur 0 = Link local. Le Link local de
    près (``MELEE``) d'abord, comme A3 ; sinon ``voulu`` (choisi par le Hanteur, phase 2) s'il est candidat ; sinon le
    plus proche (3D). ``autres`` : joueur -> position (fantômes visables)."""
    cands = {}
    lp = _pos3(local_pos)
    if lp is not None:
        cands[0] = lp
    for j, p in (autres or {}).items():
        p = _pos3(p)
        if p is not None and isinstance(j, int) and j != 0:
            cands[j] = p
    if not cands:
        return None
    if 0 in cands and math.dist(mpos, cands[0]) <= MELEE:
        return 0, cands[0], math.dist(mpos, cands[0])
    if voulu in cands:
        return voulu, cands[voulu], math.dist(mpos, cands[voulu])
    j = min(cands, key=lambda k: (math.dist(mpos, cands[k]), k))
    return j, cands[j], math.dist(mpos, cands[j])


def _ord(m: dict) -> tuple[int, str] | None:
    """Ordre de phase 3 porté par un ``hlaisse`` : (séquence, geste) ou None."""
    o = m.get("ord")
    if (isinstance(o, (list, tuple)) and len(o) == 2 and isinstance(o[0], int) and not isinstance(o[0], bool)
            and o[1] in ordres_mod.GESTES):
        return int(o[0]), str(o[1])
    return None


def lire_acteur(mem, base: int):
    """(id de process, procName, position, PV) relus maintenant ; None si illisible ou aberrant."""
    try:
        a = mem.try_read(base + OFF_PC_ID, 6)
        p = mem.try_read(base + OFF_POS, 12)
        h = mem.try_read(base + OFF_HP, 1)
    except Exception:
        return None
    if not a or not p or not h or len(a) != 6 or len(p) != 12 or len(h) != 1:
        return None
    pid, proc = struct.unpack(">Ih", a)
    pos = _pos3(struct.unpack(">3f", p))
    if pos is None:
        return None
    return pid, proc, pos, struct.unpack(">b", h)[0]


def lire_coup(mem, base: int) -> tuple[int, int] | None:
    """(mCutType, mCutCount) de Link ; None si illisible."""
    try:
        r = mem.try_read(base + OFF_CUT, 2)
    except Exception:
        return None
    return (r[0], r[1]) if r is not None and len(r) == 2 else None


class LaisseHote:
    """Côté autorité de la zone (un Link). ``receive`` (boucle réseau) garde le dernier ``hlaisse`` de chaque
    monstre ; ``tick`` (fil de travail) pose, met à jour ou relâche les laisses dans les emplacements d'ennemis."""

    def __init__(self, enabled: bool = False, clock=time.monotonic):
        self.enabled = bool(enabled)
        self.clock = clock
        self._lock = threading.Lock()
        self.msgs: dict[str, dict] = {}            # clé -> dernier hlaisse (+ "_t" : heure de réception)
        self.etat: dict[str, dict] = {}            # clé -> {atk, elan, t, hp, pid}
        self.victimes: dict[int, int] = {}         # id de process du monstre -> joueur visé (0 = Link local) : A3
        self.notes: list[str] = []
        self.n_poses = self.n_relaches = self.n_elans = self.n_coups = 0
        self.mode = ""                             # "pv libres" / "dégradé" (affichage)
        self.moi: int | None = None                # notre id (phase 2 : le Hanteur vise l'autorité elle-même)
        self.n_visees = 0
        # phase 3 (ordres, synchro/ordres.py)
        self.ordres_on = True                      # --no-ordres : jamais d'ordre joué ici (l'élan seul)
        self.n_ordres_faits = self.n_ordres_perimes = self.n_ordres_sans = self.n_ordres_refus = 0
        self.n_ordres_retenus = 0             # ordres étendus retenus : forme hors de sa liste blanche
        self.n_ordres_erreurs = 0
        # possession complète (synchro/pilotage.py) : le Hanteur pilote le monstre
        self.pilote_on = True                      # --no-pilotage : toujours la laisse classique ici
        self.essai = False                         # espèces à l'essai permises d'office ici (True, ou des process) ;
                                                   # sinon le ``hlaisse`` du serveur le dit (réglage de la partie)
        self.n_appats = 0                          # attaques demandées par l'appât (carotte rapprochée)
        self.out: list[dict] = []                  # ``hpilote`` à envoyer au serveur (retour au Hanteur)
        self._retours: dict[str, tuple] = {}       # clé -> (heure, état) du dernier ``hpilote``
        self.n_pilotes = self.n_carottes = self.n_replis = 0

    def take_out(self) -> list[dict]:
        """Messages à envoyer au serveur (boucle réseau) : retours ``hpilote`` au Hanteur qui pilote."""
        with self._lock:
            out, self.out = self.out, []
        return out

    def _retour(self, key, m, ok: int, pos, libre: int, raison, now: float) -> None:
        """Retour au Hanteur qui pilote ``key`` : ``ok`` (piloté ici), position du monstre, ``libre`` (un geste ou
        une réaction se joue : son IA est libre), ``raison`` du repli sur la laisse. Au plus un par
        ``PERIODE_RETOUR`` (1 s pour un repli), tout de suite si l'état change."""
        etat = (ok, libre, raison)
        t, avant = self._retours.get(key, (float("-inf"), None))
        if etat == avant and now - t < (pilotage.PERIODE_RETOUR if ok else 1.0):
            return
        self._retours[key] = (now, etat)
        msg = {"type": "hpilote", "key": key, "zone": m.get("zone"), "ok": ok, "libre": libre,
               "pos": [round(float(x), 1) for x in pos]}
        if raison:
            msg["raison"] = raison
        with self._lock:
            if len(self.out) < 32:
                self.out.append(msg)

    def _repli(self, proc, puppets, hp_free, essai: bool = False) -> str | None:
        """Pourquoi ce monstre n'est PAS piloté ici alors que le Hanteur le demande (None : il l'est). ``essai`` :
        le serveur dit que la partie permet cette espèce à l'essai."""
        if not self.pilote_on:
            return "option"
        if not pilotage.pilotable(proc, True if essai else self.essai):
            return "espece"
        if not hp_free or not getattr(puppets, "pilote_ok", False):
            return "pack"
        if pilotage.drapeaux(proc) and not getattr(puppets, "formes_ok", False):
            return "pack"                         # forme spéciale : il faut le bit 17 du pack
        return None

    # --- réseau ------------------------------------------------------------------------------------------
    def receive(self, msg: dict) -> None:
        if not self.enabled:
            return
        key = msg.get("key")
        if not isinstance(key, str) or len(key) > 64:
            return
        with self._lock:
            if msg.get("fin") in (1, True):
                self.msgs.pop(key, None)
                return
            if _pos3(msg.get("pos")) is None or not isinstance(msg.get("zone"), str):
                return
            if key not in self.msgs and len(self.msgs) >= 8:
                self._oublier(self.clock())
                if len(self.msgs) >= 8:
                    return
            rot = msg.get("rotY")
            self.msgs[key] = dict(msg, rotY=rot if isinstance(rot, int) and not isinstance(rot, bool) else 0,
                                  _t=self.clock())

    def _oublier(self, now: float) -> None:
        """Ordres périmés oubliés (sous ``_lock``) : la « fin » d'une possession part à l'autorité du moment ; si
        nous ne l'étions plus, son dernier ``hlaisse`` resterait ici et, à 8, plus aucune laisse ne serait tenue."""
        for k in [k for k, m in self.msgs.items() if now - m["_t"] > OUBLI]:
            del self.msgs[k]

    def reset(self) -> None:
        """Connexion perdue : plus aucun ordre (le fil de travail relâche au prochain tick)."""
        with self._lock:
            self.msgs.clear()
            self.out.clear()

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def _note(self, s: str) -> None:
        if len(self.notes) < 16:
            self.notes.append("Hanteurs (laisse) : " + s)

    # --- fil de travail ----------------------------------------------------------------------------------
    def tick(self, puppets, mem, local_enemies: dict, zone: str | None, autorite: bool, libre: bool,
             local_pos, autres: dict, hits=(), apply_hit=None, now: float | None = None) -> None:
        """``puppets`` : EnemyPuppets (emplacements) ; ``local_enemies`` : clé -> (id, adresse) ; ``autorite`` :
        nous sommes l'autorité de ``zone`` ; ``libre`` : pas de cinématique ni de changement de salle ;
        ``local_pos`` : notre Link ; ``autres`` : joueur -> position des Link visables par A3 dans la zone ;
        ``hits`` : clés en laisse touchées par notre épée (mode dégradé) ; ``apply_hit(clé, dmg)`` : coup appliqué
        (mort comprise) chez nous. Jamais fatal : une erreur relâche la laisse concernée."""
        if not self.enabled or puppets is None:
            return
        now = self.clock() if now is None else now
        with self._lock:
            self._oublier(now)
            msgs = {k: dict(m) for k, m in self.msgs.items()}
        actives = set(getattr(puppets, "leashes", {}) or {})
        ok_global = autorite and libre and zone is not None and mem is not None
        hp_free = bool(getattr(puppets, "leash_hp_free", False))
        self.mode = "PV libres" if hp_free else "dégradé (PV imposés)"
        vict: dict[int, int] = {}
        for key in actives | set(msgs):
            m = msgs.get(key)
            raison = None
            if not ok_global:
                raison = "plus l'autorité, ou cinématique"
            elif m is None or now - m["_t"] > TIMEOUT:
                raison = "plus d'ordres du Hanteur"
            elif m.get("zone") != zone:
                raison = "autre zone"
            elif key not in local_enemies:
                raison = "monstre absent ici"
            elif key in (getattr(puppets, "slots", {}) or {}) or key in (getattr(puppets, "dying", {}) or {}):
                raison = "devenu marionnette"
            if raison is None:
                try:
                    raison = self._tenir(key, m, puppets, mem, local_enemies[key], hp_free, local_pos, autres,
                                         key in hits, apply_hit, vict, now)
                except Exception as exc:          # lecture aberrante, boîte refusée : on relâche
                    raison = f"erreur ({type(exc).__name__}: {exc})"
            if raison is not None:
                if key in actives or key in (getattr(puppets, "leashes", {}) or {}):
                    try:
                        puppets.unleash(key)
                    except Exception:
                        pass
                    self.n_relaches += 1
                    if not raison.startswith("plus d'ordres"):
                        self._note(f"{key} relâché ({raison})")
                elif raison.startswith("erreur"):
                    self._note(f"{key} pas tenu en laisse ({raison})")
                self.etat.pop(key, None)          # l'ordre reçu reste : il peut redevenir valable (rescan, retour)
                # pilotage demandé mais cinématique chez nous : le Hanteur le sait (sa prédiction s'arrête)
                if m is not None and m.get("pil") in (1, True) and autorite and not libre \
                        and m.get("zone") == zone and now - m["_t"] <= TIMEOUT:
                    p = _pos3(m.get("pos"))
                    if p is not None:
                        self._retour(key, m, 0, p, 0, "evenement", now)
        self.victimes = vict
        for key in list(self.etat):
            if key not in msgs:
                self.etat.pop(key, None)
        for key in [k for k in self._retours if k not in msgs]:
            del self._retours[key]

    def _tenir(self, key, m, puppets, mem, info, hp_free, local_pos, autres, touche, apply_hit, vict, now):
        pid, base = info
        a = lire_acteur(mem, base)
        if a is None or a[0] != (pid & 0xFFFFFFFF):
            return "acteur relu différent"
        _pid, proc, mpos, hp = a
        esp = POSSEDABLES.get(proc)
        if esp is None:
            return "espèce non possédable"
        if hp <= 0:
            return "mort (sa mort se joue seule)"
        e = self.etat.get(key)
        if e is None:
            # un ordre déjà reçu avant que la laisse soit tenue ici n'est pas rejoué (comme l'élan)
            e = self.etat[key] = {"atk": m.get("atk", 0), "elan": float("-inf"), "t": now, "hp": hp,
                                  "t_natif": float("-inf"), "t_lame": float("-inf"),
                                  "ord_seq": _ord(m)[0] if _ord(m) else None, "ordre": None,
                                  "libre_fin": float("-inf")}
        dt = now - e["t"]
        e["t"] = now
        # ordre : coup d'épée du Hanteur -> élan vers le Link visé
        atk = m.get("atk", 0)
        if isinstance(atk, int) and not isinstance(atk, bool) and atk != e["atk"]:
            e["atk"] = atk
            e["elan"] = now
            self.n_elans += 1
        # phase 3 : dernier ordre du Hanteur (attaque, lourde, garde) -> CALL 10 au bon moment
        od = _ord(m)
        if od is not None and od[0] != e.get("ord_seq"):
            e["ord_seq"] = od[0]
            e["ordre"] = {"geste": od[1], "t": now, "essai": float("-inf")}
        hpos = _pos3(m["pos"])
        a3 = esp.get("a3", True)
        voulu = m.get("cible")
        if not isinstance(voulu, int) or isinstance(voulu, bool):
            voulu = None
        elif voulu == self.moi:
            voulu = 0                             # le Hanteur vise le Link de l'autorité (nous)
        if not a3:
            autres, voulu = {}, None              # Darknut : seulement le Link de l'autorité (jamais de fantôme)
        v = choisir_victime(mpos, local_pos, autres, voulu)
        if voulu is not None and v is not None and v[0] == voulu:
            self.n_visees += 1
        vmax = esp["vmax"]
        vol = esp.get("vol")
        cible, rot, visee = hpos, m.get("rotY", 0), None
        # élan seulement vers un Link du même étage (sinon le monstre foncerait au-dessus de lui, hors du rebord) ;
        # un volant n'a pas d'étage
        if v is not None and now - e["elan"] < ELAN and v[2] <= ELAN_PORTEE \
                and (vol or abs(v[1][1] - mpos[1]) <= ETAGE):
            j, vp, d = v
            u = (mpos[0] - vp[0], mpos[2] - vp[2])
            n = math.hypot(*u) or 1.0
            cible = (vp[0] + u[0] / n * esp["contact"], vp[1], vp[2] + u[1] / n * esp["contact"])
            vmax *= ELAN_VITESSE
            rot = angle_vers(mpos, vp)
            visee = j
            href = vp[1] + 60.0 if vol else max(vp[1], mpos[1])
        else:
            if v is not None and v[2] <= VISEE:
                rot = angle_vers(mpos, v[1])
                visee = v[0]
            href = hpos[1] + vol if vol else hpos[1]
        if rot is None:
            rot = m.get("rotY", 0)
        if not a3:
            # Darknut : cible forcée sur le Link local (l'autorité) même loin de lui. Sans cela, « A3 pour tous les
            # monstres » (bit 4 du pack, où le Darknut est une espèce visable) le tournerait vers un fantôme proche.
            visee = 0
        # possession complète : piloté si le Hanteur le demande (champ ``pil``) et que tout le permet ici ; sinon
        # la laisse classique ci-dessous, et le Hanteur apprend pourquoi

        def lire(off, taille):
            return ordres_mod.lire_champ(mem, base, off, taille)

        demande = m.get("pil") in (1, True)
        repli = self._repli(proc, puppets, hp_free, m.get("essai") in (1, True)) if demande else None
        e["pil"] = demande and repli is None
        if esp.get("formes") and not e["pil"]:
            # forme spéciale non pilotée ici (pack sans le bit 17, pilotage coupé, Hanteur sans pilotage) : rien
            # n'est imposé au monstre, ni laisse, ni ordre ; la possession continue (le Hanteur sait pourquoi)
            e["ordre"] = None
            e["hp"] = hp
            if key in (getattr(puppets, "leashes", {}) or {}):
                puppets.unleash(key)
            if demande:
                self.n_replis += 1
                self._retour(key, m, 0, mpos, 0, repli, now)
            return None
        self._ordre(e, proc, pid, esp, puppets, v, now, lire=lire)
        if e["pil"]:
            return self._piloter(key, m, e, puppets, pid, proc, mpos, hp, hpos, v, visee, vict, now, dt, lire)
        if demande:
            self.n_replis += 1
            self._retour(key, m, 0, mpos, 0, repli, now)
        garde = pilotage.AUTRES.get(proc) if esp.get("garde") else None
        if now < e["libre_fin"] or (garde is not None and not pilotage.etat_permis(garde, lire)):
            # l'ordre fait bondir / plonger le monstre : pas de laisse pendant ce temps (son IA est libre) ; de même
            # pour une espèce « gardée » hors de sa liste blanche d'états (touchée, cachée, en scène, mourante…)
            if key in (getattr(puppets, "leashes", {}) or {}):
                puppets.unleash(key)
            if visee is not None:
                vict[pid] = visee
            e["hp"] = hp
            return None
        npos = pas_vol(mpos, cible, href, vmax, dt) if vol else pas_laisse(mpos, cible, href, vmax, dt)
        # PV : libres (pack à jour) ou imposés à la plus basse valeur vue (dégradé)
        if hp_free:
            e["hp"] = hp
        else:
            # un même coup d'épée peut être vu deux fois : PV natifs baissés (lus avant que le jeu remette les PV
            # imposés) ET lame près du monstre, au même tick ou au suivant ; un seul compte (FENETRE_COUP)
            vu_baisse = hp < e["hp"]
            if vu_baisse and now - e["t_lame"] < FENETRE_COUP:
                vu_baisse = False                 # le coup de lame déjà compté : la baisse native est la même
            elif vu_baisse:
                e["hp"], e["t_natif"] = hp, now
            hors_fenetre = now - e["t_natif"] >= FENETRE_COUP and now - e["t_lame"] >= FENETRE_COUP
            if touche and not vu_baisse and hors_fenetre and apply_hit is not None:
                self.n_coups += 1
                e["t_lame"] = now
                res = apply_hit(key, 1)
                if isinstance(res, str) and "retiré" in res:
                    return "tué par ton épée"
                e["hp"] = max(0, e["hp"] - 1)
            if e["hp"] <= 0:
                return "mort (PV à 0)"
        if not puppets.leash(key, pid, npos, s16(rot), e["hp"], hp_free):
            return "pas d'emplacement d'ennemi libre"
        if visee is not None:
            vict[pid] = visee                     # A3 : seulement un monstre vraiment tenu en laisse
        self.n_poses += 1
        return None

    def _piloter(self, key, m, e, puppets, pid, proc, mpos, hp, hpos, v, visee, vict, now, dt, lire):
        """Possession complète : le monstre est épinglé là où le Link caché du Hanteur se trouve (interpolé, jamais
        plus vite que nature), tourné comme lui ; le temps de son execute, son IA voit la « carotte » (devant lui
        quand le pilote avance : il court ; devant lui quand un ordre attend : il se met en combat). Rien d'imposé
        tant qu'un geste se joue (``libre_fin``) ou que son état sort de la liste blanche (réaction à un coup,
        scène…) : son IA est alors libre, comme pour la laisse. Rend la raison d'une relâche, ou None."""
        pesp = pilotage.espece(proc)
        if pesp is None:
            return "espèce non pilotable"
        autre = isinstance(pesp, pilotage.Autre)
        # volant : tenu à sa hauteur de vol au-dessus du Link caché (réglée par le pilote, bornée ici). Le retour
        # au Hanteur porte la position RÉELLE du monstre, jamais « sa position moins la hauteur de vol » : un
        # volant qui vole plus bas que cette hauteur (premier tick, geste, état refusé) donnerait un point SOUS le
        # sol, où le Hanteur recollerait son Link caché. Le Hanteur compare à plat (``HanteurSync._pilotage``).
        vol = pilotage.hauteur_vol(pesp, m.get("haut"))
        vit = pilotage.vitesse(m.get("vit"), pesp.vmax) or (0.0, 0.0)
        e["hp"] = hp
        if now < e["libre_fin"] or not pilotage.etat_permis(pesp, lire):
            if key in (getattr(puppets, "leashes", {}) or {}):
                puppets.unleash(key)
            if visee is not None:
                vict[pid] = visee
            self._retour(key, m, 1, mpos, 1, None, now)
            return None
        rot = s16(m.get("rotY", 0))
        proche = v is not None and v[2] <= MELEE
        dr = pesp.drapeaux if autre else 0
        if autre and pesp.forme == "cible":
            # carotte SEULE : le monstre n'est jamais replacé (emplacement = sa position réelle, que le pack
            # n'écrit pas) ; le temps de son execute, son IA prend le point où se tient le Hanteur pour le joueur
            # (elle y saute, y rampe, s'y téléporte, y tire), sauf un vrai Link au contact (comme A3)
            if proche and v[0] != 0:
                car = tuple(v[1])
            elif proche:
                car = None
            else:
                car = tuple(hpos)
            yeux = car[1] + pilotage.YEUX if car is not None else 0.0
            if not puppets.leash(key, pid, mpos, rot, hp, True,
                                 pilote=(proc, pesp.off_action, pesp.masque, car, yeux, dr)):
                return "pas d'emplacement d'ennemi libre (ou pack sans les formes spéciales)"
            self.n_poses += 1
            self.n_pilotes += 1
            if car is not None:
                self.n_carottes += 1
            self._retour(key, m, 1, mpos, 0, None, now)
            return None
        if dr & pilotage.DR_XZ:
            # épinglage de x et z seulement : la hauteur imposée n'existe pas (celle du monstre est recopiée)
            if abs(hpos[1] - mpos[1]) > (pesp.etage or ETAGE):
                npos = tuple(mpos)                # pilote à un autre étage : le monstre ne bouge pas du tout
            else:
                npos = pilotage.interpoler(mpos, (hpos[0], mpos[1], hpos[2]), vit, dt, pesp.vmax,
                                           pilotage.ETAGE_VOL)
        else:
            but = (hpos[0], hpos[1] + vol, hpos[2]) if vol else hpos
            npos = pilotage.interpoler(mpos, but, vit, dt, pesp.vmax, pilotage.ETAGE_VOL if vol else ETAGE)
        if autre and pesp.forme != "carotte":
            car = None                            # épinglage seul : le pack n'a aucune garde pour cette espèce
        elif proche and v[0] != 0:
            car = tuple(v[1])                     # un autre Link au contact : l'IA le voit lui (comme A3)
        elif proche:
            car = None                            # notre Link au contact : l'IA voit le vrai joueur
        elif autre:
            car = pilotage.carotte_espece(npos, rot, vit, pesp, ordre_en_attente=e.get("ordre") is not None,
                                          appat=now < e.get("appat_fin", float("-inf")))
        else:
            car = pilotage.carotte(npos, rot, vit, pesp, ordre_en_attente=e.get("ordre") is not None)
        yeux = car[1] + pilotage.YEUX if car is not None else 0.0
        pil = (proc, pesp.off_action, pesp.masque, car, yeux)
        if not puppets.leash(key, pid, npos, rot, hp, True, pilote=pil + (dr,) if dr else pil):
            return "pas d'emplacement d'ennemi libre"
        self.n_poses += 1
        self.n_pilotes += 1
        if car is not None:
            self.n_carottes += 1
        self._retour(key, m, 1, npos, 0, None, now)
        return None

    def _ordre(self, e, proc, pid, esp, puppets, v, now, lire=None) -> None:
        """Phase 3 : l'ordre en attente part au jeu (CALL 10) quand le monstre est prêt ; jamais fatal. ``lire`` :
        lecture seule des champs de l'acteur (arme, bouclier : ``ordres.choisir``)."""
        od = e.get("ordre")
        if od is None:
            return
        if now - od["t"] > ORDRE_VIE:
            e["ordre"] = None
            self.n_ordres_perimes += 1
            return
        # espèce pilotée sans ordre CALL 10 : « attaque » par l'appât (la carotte vient à portée devant le monstre,
        # son IA attaque d'elle-même) ; aucun appel au jeu, aucune écriture d'état
        etendus = bool(getattr(puppets, "formes_ok", False))   # seconde table d'ordres : bit 17 du pack
        if e.get("pil") and self.ordres_on and od["geste"] in ("attaque", "lourde") \
                and pilotage.gestes_appat(proc) and not ordres_mod.gestes_espece(proc, etendus):
            e["ordre"] = None
            e["appat_fin"] = now + pilotage.AUTRES[proc].t_appat
            self.n_appats += 1
            return
        if not self.ordres_on or not getattr(puppets, "ordres_ok", False):
            e["ordre"] = None                     # pack sans la capacité : l'élan seul (phase 1)
            self.n_ordres_sans += 1
            return
        g = od["geste"]
        # piloté : le geste part tout de suite (le Hanteur a mis le monstre où il le veut)
        pret = (e.get("pil") or g == "garde" or v is None or v[2] <= esp["contact"] * CONTACT_ORDRE
                or now - e["elan"] >= ELAN)
        if not pret or now - od["essai"] < ORDRE_ESSAI:
            return
        # « lourde » sans arme -> « attaque » ; « garde » sans bouclier -> rien (choisi à chaque essai : l'état change)
        idx = ordres_mod.choisir(proc, g, lire, etendus)
        if idx is None:
            e["ordre"] = None
            self.n_ordres_sans += 1
            return
        if idx >= ordres_mod.N_ORDRES:
            # ordre étendu (forme spéciale) : jamais hors de la liste blanche de l'espèce. La garde du pack ne lit
            # que trois champs de valeurs 0..31 : elle ignore par exemple le s32 +0x488 du Poe (-1 exigé par le jeu
            # lui-même avant d'attaquer). L'ordre attend le retour de l'état, puis se périme (ORDRE_VIE).
            pesp = pilotage.AUTRES.get(proc)
            if pesp is None or lire is None or not pilotage.etat_permis(pesp, lire):
                self.n_ordres_retenus += 1
                return
        od["essai"] = now
        try:
            r = puppets.ordre(pid, idx)
        except Exception as exc:                  # boîte occupée, délai : nouvel essai (dans ORDRE_VIE)
            if self.n_ordres_erreurs < 3:
                self._note(f"ordre {ordres_mod.TOUS[idx].cle} : {type(exc).__name__}: {exc}")
            self.n_ordres_erreurs += 1
            return
        if r == 1:
            e["ordre"] = None
            self.n_ordres_faits += 1
            lib = ordres_mod.TOUS[idx].libre
            if lib > 0:
                e["libre_fin"] = now + lib
        elif r in (-1, -4):                       # espèce refusée par le pack, acteur introuvable : abandon
            e["ordre"] = None
            self.n_ordres_refus += 1
        # 0 (pas libre) ou -3 (événement) : nouvel essai tant que l'ordre est frais

    def status(self) -> str:
        if not self.enabled:
            return "désactivée"
        s = (f"{len(self.etat)} monstre(s) en laisse ({self.mode or '?'}), {self.n_poses} poses, "
             f"{self.n_elans} élan(s), {self.n_relaches} relâché(s)")
        if self.n_ordres_faits or self.n_ordres_perimes or self.n_ordres_sans or self.n_ordres_refus:
            s += (f", ordres : {self.n_ordres_faits} fait(s), {self.n_ordres_perimes} trop tard, "
                  f"{self.n_ordres_sans} sans effet (pack ou espèce), {self.n_ordres_refus} refusé(s)")
        if self.n_pilotes or self.n_replis:
            s += (f", pilotage : {self.n_pilotes} image(s) pilotée(s) dont {self.n_carottes} avec carotte, "
                  f"{self.n_appats} appât(s), {self.n_replis} repli(s) sur la laisse")
        return s

