"""Mode Hanteurs, phase 0 (« Hanteurs spectateurs invocateurs »), pack v17 ou v19, Python seul.

Étude : docs/etudes/mode_hanteurs.md (la section « Vérification » finale prime), §0, §2, §3, §4, §7, §8, §9, §11.
Serveur : ``--mode-hanteurs`` (désactivé par défaut) et ``--hanteurs-config FICHIER`` (JSON écrit par le lanceur).
Client : ``--share-hanteurs`` (activé par ``--coop-donjon``, donc par le lanceur), ``--no-hanteurs`` pour couper ;
sans serveur en mode Hanteurs, le client ne fait rien de plus.

Ce que les joueurs voient. Dans le lobby du lanceur, l'hôte choisit le nombre de Link et de Hanteurs (et,
s'il veut, qui sera Hanteur), la rotation et le barème des points. Quand tout le monde est là, le serveur
répartit les rôles. Un Hanteur :

* est invisible pour les Link (le serveur ne leur relaie plus sa position, leur client retire son fantôme) ;
* n'est vu des autres Hanteurs que MANIFESTÉ (champ ``hv`` de son ``state``, ``HanteurSync.champ_hv``) : ni
  spectateur, ni pendant qu'il pilote un monstre (on voit le monstre), et alors avec la teinte « spectre »
  (``HanteurSync.visibles`` / ``spectres``, ``commun/couleurs.py``) ;
* ne voit plus son propre Link (bit ``NO_DRAW`` de ``mNoResetFlg0``, ``Link+0x3B8 & 0x08000000``, lu par
  ``checkPlayerNoDraw`` en 0x02444E58 [HD-ASM, vérifié dans le RPX par test_hanteurs.TestRpx]) ; sa vie est
  remontée dès qu'elle baisse (invulnérable) ;
* n'agit pas sur le monde : le serveur ignore tout ce qu'il annonce (progression, coffres, clés, inventaire,
  compteurs, objets, jarres, blocs, coups…) et ne lui donne jamais l'autorité d'une zone ; il reçoit quand même
  la progression des Link (portes, switches) ;
* suit le Link de son choix (F5 ou bouton du lanceur) : son Link caché est téléporté sur la cible, dans la même
  salle, seulement quand elle est au sol ; « suivi continu » (F8) le recolle dès qu'il est à plus de 400 u ;
* invoque des monstres (F6, F7 pour changer d'espèce, ou boutons du lanceur) avec des points tenus par le
  serveur : gain passif, gain quand un de ses monstres blesse un Link, gain quand les Link tuent un monstre.
  Seulement des espèces déjà vues vivantes dans la stage, au moins 500 u de tout Link, jamais pendant une
  cinématique de l'autorité. Le serveur diffuse ``hspawn`` : chaque PC de la zone crée le même monstre par la
  commande 4 (même position arrondie, donc même clé réseau), et la synchro existante prend le relais (IA chez
  l'autorité de la zone, marionnettes ailleurs ; A1/A2/A3 : il blesse aussi les autres Link).

Rotation (§7, réglage « changement des rôles » du lobby) : « crawl » (comme dans Crawl : le Link tombé à 0 cœur
échange son rôle avec le Hanteur qui lui a fait le dernier dégât dans les 5 s ; garde-fous et fin de la partie à
la mort de Ganon : §7.4), « minuterie » (toutes les N minutes, le Hanteur le plus riche devient Link et le Link le
plus ancien devient Hanteur), « mixte » (les deux), « aucune ». À l'échange (§7.3) : le nouveau Hanteur reste où
il est ; le nouveau Link réapparaît là où était son Link caché, ou là où le Link est tombé (règle crawl, même
salle), avec la vie de manche ; ses actions de Hanteur ne sont jamais partagées (rien d'envoyé pendant qu'il
était Hanteur, files locales oubliées à l'échange, 3 s de quarantaine serveur et client) ; inventaire partagé :
rien à faire ; la sauvegarde est jetable (le lanceur demande une partie coop dédiée).

Phase 1 (possession par laisse : se manifester, posséder un monstre, élan) : ``synchro/laisse.py`` ; côté serveur,
``Partie.posseder`` / ``lacher`` / ``ordre`` / ``manifester`` ici. Phase 2 : le Hanteur choisit le Link que vise son
monstre possédé (``Partie.viser``, F7 pendant une possession) ; potion de possession d'un Link et PvP :
``synchro/potion.py`` (``PotionPartie``, mêlée à ``Partie``) ; côté client, le clone et le Link possédé ici
(``HanteurSync``).

Limites de la phase 0 (étude §11) : suivi limité à la salle
de la cible (pas de changement de salle forcé : risque de chute hors d'une salle non chargée), Chuchu et Keese
derrière une option (``especes_hypo``, paramètres non relus dans le décompilé).
"""
from __future__ import annotations

import math
import random
import struct
import sys
import threading
import time

from commun import difficulte as _dif
from synchro import menu_invocation  # noqa: E402  (menu d'invocation à la manette, mod natif 0.60.0)
from synchro import capacites, enfermement, laisse, ordres, pilotage, potion, victoire
from synchro import effets_pack
from commun import commandes as cmdes
from commun.protocole import enemy_key, zone_of

ROLE_LINK, ROLE_HANTEUR = "link", "hanteur"
ROLES = (ROLE_LINK, ROLE_HANTEUR)
ROTATIONS = ("aucune", "minuterie", "crawl", "mixte")
ROTATIONS_CRAWL = ("crawl", "mixte")              # « comme dans Crawl » : le Hanteur qui tue un Link prend sa place
# Mort d'un Link que personne n'a causée (chute, monstre sauvage) en mode Crawl : « aucun » = pas d'échange (défaut),
# « meilleur » = le Hanteur le plus riche prend la place. Une mort reçue du deathlink n'échange jamais.
SANS_TUEUR = ("aucun", "meilleur")
# Effets de fumée (invocation, possession, relâche, potion, échange) : réglage de la partie. « tous » : les Link les voient
# aussi (défaut) ; « hanteurs » : seulement les Hanteurs ; « coupes » : personne. Les Link qui voient la fumée savent
# quel monstre est possédé : c'est un choix d'équilibre, d'où le réglage.
EFFETS = ("tous", "hanteurs", "coupes")
# ``hfx`` (serveur -> clients de la salle) : ce qui vient de se passer. Type et échelle de la fumée (daDisappear,
# types 1 et 3 seulement, voir drops.effet_params) choisis ICI, jamais lus dans le message.
# Échange « comme dans Crawl » : « echange » = grosse fumée là où le Link est tombé (chez les AUTRES : son PC à lui
# est en Game Over ou en rechargement) ; « apparition » = fumée là où le nouveau Link (l'ancien Hanteur) apparaît
# vraiment (aussi à la rotation par minuterie, qui échange aussi les rôles).
EFFETS_FUMEE = {"invoque": (1, 5), "possede": (3, 12), "lache": (1, 5), "potion": (3, 12), "libere": (1, 8),
                "echange": (3, 14), "apparition": (1, 8)}
EFFETS_QUOI = ("possede", "lache", "potion", "libere", "echange", "apparition")   # ce qu'un ``hfx`` peut annoncer
FX_PAR_SALLE = 4              # serveur : au plus 4 ``hfx`` par seconde et par salle (surplus jeté)
FX_AGE = 1.5                  # client : un effet reçu depuis plus longtemps est jeté, jamais rejoué en retard
FX_PAR_S = 2                  # client : au plus 2 fumées par seconde sur ce PC
FX_FILE = 8                   # client : effets en attente (au-delà, les plus vieux sont jetés)
FX_LINK_MAX = 3000.0          # client : position reçue d'un Link acceptée à moins de cette distance du nôtre
FX_SPAWN_AGE = 10.0           # client : pas de fumée d'invocation pour un ``hspawn`` plus vieux (monstre déjà là)
JOUEURS_MAX = 8

# --- Link (HD) -------------------------------------------------------------------------------------------
# mNoResetFlg0 : GC +0x29C, HD +0x3B8. checkPlayerNoDraw (0x02444E58) : ``lwz r0, 0x3b8(r30) ;
# rlwinm. r11, r0, 0,4,4`` = bit 0x08000000 (daPyFlg0_NO_DRAW) [HD-ASM]. Le jeu le pose et l'efface lui-même
# (cinématiques, Floormaster : ``oris 0x800`` / ``rlwinm 0,5,3`` sur +0x3B8) : on le repose hors événement.
LINK_FLG0 = 0x3B8
NO_DRAW_BYTE = 0x08           # octet de poids fort du mot (gros-boutiste) : seul cet octet est écrit
OFF_PC_ID = 0x04              # base_process_class::mBsPcId (wwlib.game.BP_PC_ID)
OFF_PROC_NAME = 0x08          # s16 procName (wwlib.game.BP_PROC_NAME)
PROC_LINK = 168               # daPy_lk_c ; Medli / Makar contrôlés : autre acteur, aucune écriture
OFF_OLD_POS = 0x300           # old.pos (GC 0x1E4 + 0x11C)
OFF_CUR_POS = 0x314           # current.pos (GC 0x1F8 + 0x11C)
OFF_SPEED = 0x33C             # speed (GC 0x220 + 0x11C)
OFF_SPEEDF = 0x370            # speedF (GC 0x254 + 0x11C)

# --- règles ----------------------------------------------------------------------------------------------
QUARANTAINE = 3.0             # s : après Hanteur -> Link, rien de « monde » n'est accepté (serveur)
QUARANTAINE_CLIENT = 3.5      # s : le client attend un peu plus avant de se réinscrire
KO_DELAI = 3.0                # s à 0 cœur avant de compter un KO (une fée le relève avant)
DERNIER_COUP = 5.0            # s : le Hanteur crédité du dernier dégât gagne le KO
GEL_MARGE = 5.0               # s : sans nouvelle du serveur, un Link possédé est rendu après la fin annoncée
DIST_LINK_MIN = 500.0         # u : pas d'invocation plus près d'un Link
DIST_ATTRIB = 400.0           # u : un monstre invoqué à cette distance d'un Link blessé est la cause
SNAP_AGE = 2.0                # s : âge maximal d'un snapshot de l'autorité pour attribuer un dégât
JAMAIS_VU = 12.0              # s : monstre jamais vu chez l'autorité après l'invocation : remboursé
POINTS_MAX = 9999
SUMMON_RATE = 2               # invocations demandées au plus par seconde et par Hanteur
DEGAT_MAX = 20                # quarts au plus comptés pour une baisse de vie (rechargement, game over)
DIFFUSION = 1.0               # s entre deux messages « hanteurs » (état) à chaque joueur
SUIVI_DIST = 400.0            # u : suivi continu, téléportation au-delà
SUIVI_PERIODE = 1.0           # s entre deux téléportations de suivi
SOL_FENETRE = 0.6             # s : la cible est « au sol » si sa hauteur n'a pas bougé de SOL_DY pendant ce temps
SOL_DY = 4.0
CREATION_ESSAIS = 3
CREATION_ATTENTE = 4.0        # s entre deux essais de création (rescan de 1-3 s compris)
SALLE_STABLE = 2.0            # s dans la salle avant de créer un monstre
VISER_RATE = 4                # hviser (phase 2) acceptés au plus par seconde (chacun fait diffuser l'état à tous)
JEU_ATTENTE = 10.0           # s : redevenu Link, vie de manche et position appliquées au plus tard (jeu libre)
# --- échange « comme dans Crawl » (garde-fous) ---
IMMUNITE_ECHANGE = 10         # s (réglage crawl_immunite) : après un échange, ni l'un ni l'autre n'est rééchangé
ECHANGE_ATTENTE = 15.0        # s : tueur en chargement ou en cinématique : l'échange attend au plus ce temps
ETAT_FRAIS = 5.0              # s : sans state plus récent, le tueur est « en chargement »
FX_CHUTE_DY = 200.0           # u : Link mort plus bas que son dernier sol sûr (vide, lave) : fumée sur ce sol sûr
FX_APPARITION_ATTENTE = JEU_ATTENTE + 2.0   # s : le nouveau Link doit apparaître avant, sinon pas de fumée
FX_APPARITION_DIST = 200.0    # u : il est « apparu » quand son state est à moins de ça de la position annoncée
GANON_PROC = 246              # Ganondorf (synchro/boss_pv.py)
GANON_FIN_PV = 25             # PV < 25 en phase 2 : la fin scénarisée est lancée [HD-ASM 0x021592F8 à 0x02159310]
GANON_CONFIRME = 1.0          # s : la valeur doit tenir (deux rapports) : jamais une lecture de passage

PRESETS = {
    # points/s, par quart de cœur blessé, par monstre tué par les Link, coup de grâce, multiplicateur des coûts
    "doux": {"passif": 0.5, "degat": 3, "kill": 2, "ko": 30, "cout": 1.5},
    "normal": {"passif": 1.0, "degat": 5, "kill": 3, "ko": 50, "cout": 1.0},
    "cruel": {"passif": 2.0, "degat": 8, "kill": 5, "ko": 80, "cout": 0.75},
}

# proc -> espèce (étude §4.3). Paramètres « nus » (§4.2, règle 2) : relus dans le décompilé GameCube pour le
# Bokoblin (d_a_bk.cpp daBk_Create : type & 0xF, 0x10 = apparition sur switch, 0x20 et 0xC0 arme,
# >>8 portée de détection, >>16 chemin, >>24 switch ; angle.z = switch de mort, 0 ici) et le Moblin (d_a_mo2.cpp :
# mode = octet bas, >>8 pose figée, >>16 chemin, >>24 switch d'apparition). Chuchu et Keese : non relus
# (option especes_hypo, désactivée par défaut), paramètres de l'exemplaire vu, types scriptés refusés.
ESPECES = {
    189: {"nom": "Bokoblin", "cout": 25, "recharge": 10.0, "hypo": False},
    188: {"nom": "Moblin", "cout": 60, "recharge": 25.0, "hypo": False},
    206: {"nom": "Chuchu", "cout": 15, "recharge": 8.0, "hypo": True},
    215: {"nom": "Keese", "cout": 10, "recharge": 5.0, "hypo": True},
}
BK_TYPES_REFUSES = {5, 6, 8, 9, 0xA, 0xB, 0xF}   # porté, switch, générateur, chemin, scripté, figé
MO2_MODES_REFUSES = {1, 5, 0xF}                  # lanterne, porté, statue



def a_bord(st) -> bool:
    """Link à la barre de son bateau : ``abord`` de l'état (``mModeFlg & IN_SHIP``, synchro/houle.py). Jamais ``ship`` :
    ce champ porte le bateau du joueur dès qu'il est chargé dans la salle (amarré à l'île, Link à pied), si bien que
    toutes les îles en mer passaient pour « en bateau » (banc d'essai du 2026-11-18 : enfermement refusé sur sea:27)."""
    return isinstance(st, dict) and st.get("abord") in (1, True)

def params_invocation(proc: int, vu: int) -> int | None:
    """Paramètres de l'invocation à partir de ceux d'un exemplaire vivant de la stage (``vu``). None : refus."""
    try:
        vu = int(vu) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None
    if proc == 189:
        t = vu & 0xF
        if t in BK_TYPES_REFUSES:
            t = 0
        return 0xFFFFFF00 | (vu & 0xE0) | t       # 0x10 effacé ; portée par défaut, sans chemin ni switch
    if proc == 188:
        mode = vu & 0xFF
        if mode in MO2_MODES_REFUSES:
            mode = 0
        return 0xFFFFFF00 | mode                  # pose 0xFF (pas figé), sans chemin ni switch
    if proc in (206, 215):
        from synchro import cibles
        return vu if cibles.eligible(proc, vu, None) else None
    return None


def config_par_defaut() -> dict:
    return {"liens": 1, "hanteurs": 1, "choisis": [], "rotation": "crawl", "rotation_min": 5,
            "points": "normal", "especes_hypo": False, "vie_manche": 12, "max_invoques": 6,
            "possession": True, "possession_max": laisse.DUREE,
            "potion": True, "potion_duree": potion.DUREE, "pvp_degats": potion.PVP_DEGATS,
            "potion_depense": potion.DEPENSE_DEFAUT, "potion_plafond": potion.PLAFOND, "potion_resister": False,
            "influence": True, "pilotage": True, "recharge_attaques": 1.0, "pilotage_essai": [],
            "crawl_sans_tueur": "aucun", "crawl_immunite": IMMUNITE_ECHANGE, "crawl_fin_ganon": True,
            "effets": "tous",
            **capacites.defauts(), **enfermement.DEFAUT, **victoire.DEFAUT}


def _entier(v, lo: int, hi: int, defaut: int) -> int:
    if isinstance(v, bool):
        return defaut
    try:
        v = int(v)
    except (TypeError, ValueError, OverflowError):      # OverflowError : infini (1e999 en JSON)
        return defaut
    return max(lo, min(hi, v))


def valider_config(d) -> dict:
    """Configuration nettoyée (valeurs bornées, champs inconnus ignorés)."""
    c = config_par_defaut()
    if not isinstance(d, dict):
        return c
    c["liens"] = _entier(d.get("liens"), 1, JOUEURS_MAX - 1, c["liens"])
    c["hanteurs"] = _entier(d.get("hanteurs"), 1, JOUEURS_MAX - 1, c["hanteurs"])
    if c["liens"] + c["hanteurs"] > JOUEURS_MAX:
        c["hanteurs"] = JOUEURS_MAX - c["liens"]
    ch = d.get("choisis")
    if isinstance(ch, str):
        ch = ch.split(",")
    if isinstance(ch, list):
        noms = []
        for n in ch[:JOUEURS_MAX]:
            n = " ".join(str(n).split())[:24]
            if n and n not in noms:
                noms.append(n)
        c["choisis"] = noms
    if d.get("rotation") in ROTATIONS:
        c["rotation"] = d["rotation"]
    c["rotation_min"] = _entier(d.get("rotation_min"), 1, 60, c["rotation_min"])
    # échange « comme dans Crawl » : mort sans tueur, immunité après un échange, fin à la mort de Ganon
    if d.get("crawl_sans_tueur") in SANS_TUEUR:
        c["crawl_sans_tueur"] = d["crawl_sans_tueur"]
    c["crawl_immunite"] = _entier(d.get("crawl_immunite"), 0, 60, c["crawl_immunite"])
    c["crawl_fin_ganon"] = d.get("crawl_fin_ganon") not in (0, False)
    if d.get("points") in PRESETS:
        c["points"] = d["points"]
    c["especes_hypo"] = d.get("especes_hypo") in (1, True)
    c["vie_manche"] = _entier(d.get("vie_manche"), 4, 80, c["vie_manche"])
    c["max_invoques"] = _entier(d.get("max_invoques"), 1, 12, c["max_invoques"])
    c["possession"] = d.get("possession") not in (0, False)
    c["possession_max"] = _entier(d.get("possession_max"), 10, 180, c["possession_max"])
    c["potion"] = d.get("potion") not in (0, False)
    c["potion_duree"] = _entier(d.get("potion_duree"), potion.DUREE_MIN, potion.DUREE_MAX, c["potion_duree"])
    c["pvp_degats"] = _entier(d.get("pvp_degats"), 1, 8, c["pvp_degats"])
    # un Hanteur qui possède un joueur peut dépenser : rien / rubis et munitions / avec un plafond par possession
    if d.get("potion_depense") in potion.DEPENSES:
        c["potion_depense"] = d["potion_depense"]
    c["potion_plafond"] = _entier(d.get("potion_plafond"), potion.PLAFOND_MIN, potion.PLAFOND_MAX, c["potion_plafond"])
    c["potion_resister"] = d.get("potion_resister") in (1, True)     # décoché : le possédé regarde, durée fixe
    c["influence"] = d.get("influence") not in (0, False)     # phase 4 : influence sur les boss (synchro/ordres.py)
    # possession complète (synchro/pilotage.py) : le Hanteur pilote le monstre ; recharge des gestes (× 0,5 à × 3)
    c["pilotage"] = d.get("pilotage") not in (0, False)
    c["recharge_attaques"] = pilotage.multiplicateur(d.get("recharge_attaques"), c["recharge_attaques"])
    # espèces à l'essai (possession complète, étape 2) : True = toutes, ou liste de process / de noms ; rangé en
    # liste triée de process (wwmp/pilotage.essais). Désactivé par défaut : elles restent hors de la possession.
    c["pilotage_essai"] = pilotage.essais(d.get("pilotage_essai"))
    c["effets"] = lire_effets(d.get("effets"), c["effets"])   # fumées : tous / hanteurs / coupes
    # capacité propre de chaque espèce possédée (synchro/capacites.py) : activée, espèces coupées, recharge, garde
    # du Darknut, durée des frénésies ; la durée de possession reste ``possession_max`` (durée fixe)
    capacites.valider(d, c)
    victoire.valider(d, c, _entier)                           # conditions de victoire (synchro/victoire.py)
    enfermement.valider(d, c, _entier)                        # enfermement des Link (synchro/enfermement.py)
    return c


def lire_effets(v, defaut: str = "tous") -> str:
    """Réglage « effets » nettoyé. Une case à cocher (vrai / faux) vaut « tous » / « coupes » ; tout le reste,
    champ absent compris (ancien serveur, ancien fichier de réglages), garde ``defaut``."""
    if v is True:
        return "tous"
    if v is False:
        return "coupes"
    return v if isinstance(v, str) and v in EFFETS else defaut


def cout_possession(cfg: dict) -> int:
    return max(1, int(round(laisse.COUT * PRESETS[cfg["points"]]["cout"])))


def especes_permises(cfg: dict) -> dict[int, dict]:
    """Espèces invocables et leurs coûts effectifs (barème de points)."""
    mult = PRESETS[cfg["points"]]["cout"]
    return {proc: dict(e, cout=max(1, int(round(e["cout"] * mult))))
            for proc, e in ESPECES.items() if cfg["especes_hypo"] or not e["hypo"]}


def _pos3(v) -> tuple[float, float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError, OverflowError):      # OverflowError : entier JSON démesuré (10**400)
        return None
    return p if all(math.isfinite(x) and abs(x) < 1e6 for x in p) else None


def parse_key(key) -> tuple[int, int, tuple[int, int, int]] | None:
    """Clé réseau d'un ennemi (``enemy_key`` : ``proc/PARAMS/x,y,z``) -> (proc, params, home)."""
    if not isinstance(key, str) or key.count("/") != 2:
        return None
    a, b, c = key.split("/")
    try:
        proc, params = int(a), int(b, 16)
        home = tuple(int(x) for x in c.split(","))
    except ValueError:
        return None
    return (proc, params, home) if len(home) == 3 else None


# =========================================================================================================
# Serveur : partie (rôles, points, invocations, rotation). Aucune entrée/sortie : les messages à envoyer sont
# rangés dans ``sortie`` [(destinataire, message)] ; destinataire = id d'un joueur, ou ("zone", zone).
# =========================================================================================================
class Partie(ordres.InfluencePartie, potion.PotionPartie, victoire.VictoirePartie, enfermement.EnfermementPartie):
    def __init__(self, cfg=None, rng=None, clock=time.monotonic, log=None):
        self.cfg = valider_config(cfg)
        self.rng = rng or random.Random()
        self.clock = clock
        self.log = log or (lambda m: None)
        self.repartie = False
        self.degats_mult = 1                       # difficulté : dégâts reçus ×N (le score compte le coup d'origine)
        self.roles: dict[int, str] = {}
        self.noms: dict[int, str] = {}
        self.points: dict[int, float] = {}
        self.depuis_role: dict[int, float] = {}
        self.quarantaine: dict[int, float] = {}
        self.etat: dict[int, dict] = {}            # id -> dernier state utile (zone, pos, rot, vie, busy)
        self.vie: dict[int, dict] = {}             # id -> {"v": vie, "zero": début du 0, "ko": déjà compté}
        self.dernier_degat: dict[int, tuple[int, float]] = {}   # victime -> (Hanteur, heure)
        self.coups: dict[int, tuple[str, float]] = {}            # victime -> (clé du player_hit, heure)
        self.invoques: dict[str, dict] = {}
        self.recharges: dict[tuple[int, int], float] = {}
        self.summon_t: dict[int, list[float]] = {}
        self.especes: dict[str, dict[int, int]] = {}             # stage -> {proc: params d'un exemplaire vivant}
        self.snaps: dict[str, tuple[float, dict]] = {}           # zone -> (heure, {clé: entrée})
        self.vivants: dict[str, set] = {}                        # zone -> clés vues vivantes
        self.t_passif: float | None = None
        self.t_rotation = clock()
        self.t_diffusion = float("-inf")
        self.sortie: list[tuple] = []
        self.changes: list[int] = []               # joueurs dont le rôle a changé (le serveur réattribue)
        self.anciens: set[int] = set()             # joueurs qui ont été Hanteurs : inscriptions filtrées
        # id -> zone où se trouvait son jeu quand il était Hanteur (marquée à chaque state de Hanteur, gardée quand il
        # redevient Link, oubliée dès qu'il change de zone) : son jeu n'y a peut-être plus les monstres des Link, sa
        # liste d'ennemis n'y est jamais relayée « full » (``liste_douteuse`` ; banc d'essai du 2026-11-18)
        self.doute: dict[int, str] = {}
        self.n_invoques = self.n_refus = self.n_ignores = self.n_echanges = 0
        # échange « comme dans Crawl » : immunité après un échange (id -> fin), échanges qui attendent que le tueur
        # sorte d'un chargement ou d'une cinématique (victime -> {tueur, t, fin, pos, zone, raison})
        self.immunite: dict[int, float] = {}
        self.echanges_attente: dict[int, dict] = {}
        # fumée d'apparition du nouveau Link (échange de Crawl, rotation) : jouée quand son state de Link arrive
        # vraiment (hors cinématique, bonne salle, téléportation faite). Nouveau Link -> {t, fin, zone, pos}
        self.apparitions: dict[int, dict] = {}
        self.n_echanges_refuses = 0
        self.n_capacites = 0                        # capacités acceptées (synchro/capacites.py)
        self.n_gestes_refuses = 0                   # gestes refusés pendant leur recharge (synchro/pilotage.py)
        # phase 1 : possession par laisse (synchro/laisse.py). Hanteur -> {key, zone, stage, proc, t0, fin, atk, t_atk,
        # vu} ; une clé n'est possédée que par un Hanteur à la fois
        self.possessions: dict[int, dict] = {}
        self.poss_t: dict[int, list[float]] = {}
        self.manif_t: dict[int, float] = {}
        self.n_possessions = 0
        self.n_fx = 0                              # effets de fumée annoncés (``hfx``)
        self._potion_init()                        # phase 2 : potion de possession d'un Link (synchro/potion.py)
        self._influence_init()                     # phase 4 : influence sur les boss (synchro/ordres.py)
        self._victoire_init()                      # conditions de victoire (synchro/victoire.py)
        self._enfermement_init()                   # enfermement des Link (synchro/enfermement.py)

    # --- rôles ------------------------------------------------------------------------------------------
    def role(self, pid) -> str:
        return self.roles.get(pid, ROLE_LINK)

    def est_hanteur(self, pid) -> bool:
        return self.roles.get(pid) == ROLE_HANTEUR

    def bloque_monde(self, pid, now: float | None = None) -> bool:
        """Vrai : ce joueur n'agit pas sur le monde (Hanteur, ancien Hanteur en quarantaine, Link possédé par la
        potion : son Link caché n'est pas celui que les autres voient, puis quarantaine à sa libération)."""
        if self.est_hanteur(pid) or self.possede_par(pid) is not None:
            return True
        fin = self.quarantaine.get(pid)
        if fin is None:
            return False
        now = self.clock() if now is None else now
        if now >= fin:
            del self.quarantaine[pid]
            return False
        return True

    def _hanteurs(self) -> list[int]:
        return sorted(p for p, r in self.roles.items() if r == ROLE_HANTEUR)

    def _liens(self) -> list[int]:
        return sorted(p for p, r in self.roles.items() if r == ROLE_LINK)

    def _changer(self, pid: int, role: str, now: float, raison: str, pos=None, zone=None) -> None:
        if self.roles.get(pid) == role:
            return
        ancien = self.roles.get(pid)
        self.apparitions.pop(pid, None)     # rôle changé encore : l'ancienne apparition ne veut plus rien dire
        self.lacher(pid, "changement de rôle", now)
        self.potion_fin_joueur(pid, "changement de rôle", now)
        self.enf_fin_joueur(pid, "changement de rôle", now)
        self.roles[pid] = role
        self.depuis_role[pid] = now
        if role == ROLE_LINK:
            self.points[pid] = 0.0          # sa mise repart de zéro quand il redeviendra Hanteur
            if ancien == ROLE_HANTEUR:
                self.quarantaine[pid] = now + QUARANTAINE
                z = self.etat.get(pid, {}).get("zone")
                if isinstance(z, str):
                    self.doute[pid] = z             # sa salle de Hanteur : liste_douteuse
            for inv in self.invoques.values():
                if inv["owner"] == pid:
                    inv["owner"] = None     # ses monstres restent, sans propriétaire
        else:
            self.points.setdefault(pid, 0.0)
            self.quarantaine.pop(pid, None)
            self.anciens.add(pid)
        self.vie.pop(pid, None)
        self.t_diffusion = float("-inf")     # état diffusé tout de suite à tous
        msg = {"type": "hdevient", "role": role, "raison": raison, "vie": self.cfg["vie_manche"]}
        if pos is not None and isinstance(zone, str) and ":" in zone:
            stage, _, room = zone.rpartition(":")
            msg["pos"], msg["stage"], msg["room"] = [round(v, 1) for v in pos], stage, int(room)
        self.sortie.append((pid, msg))
        if ancien is not None:
            self.changes.append(pid)
        self.log(f"Hanteurs : {self.noms.get(pid, pid)} devient {'Hanteur' if role == ROLE_HANTEUR else 'Link'}"
                 f" ({raison})")

    def joueurs(self, presents: dict[int, str], now: float | None = None) -> None:
        """Arrivées et départs (id -> nom des joueurs de jeu connectés)."""
        now = self.clock() if now is None else now
        for pid in [p for p in self.roles if p not in presents]:
            self.lacher(pid, "départ", now)
            self.potion_fin_joueur(pid, "départ", now)
            self.enf_fin_joueur(pid, "départ", now)
            for d in (self.roles, self.depuis_role, self.quarantaine, self.etat, self.vie, self.dernier_degat,
                      self.coups, self.summon_t, self.poss_t, self.manif_t, self.exo_t, self.resist_t,
                      self.potion_t, self.vie_ignore, self.immunite, self.v_ganon, self.apparitions, self.doute):
                d.pop(pid, None)
            # échange en attente : le tué ou le tueur est parti, il n'aura pas lieu (le nombre de Link ne bouge pas)
            for victime, a in list(self.echanges_attente.items()):
                if pid in (victime, a["tueur"]):
                    del self.echanges_attente[victime]
                    self._pas_echange(victime, f"{self.noms.get(pid, 'un joueur')} est parti", now, a["tueur"])
            for k in [k for k in self.pvp_t if pid in k]:
                del self.pvp_t[k]
            self.v_scores.pop(pid, None)
            self.anciens.discard(pid)
            for inv in self.invoques.values():
                if inv["owner"] == pid:
                    inv["owner"] = None
        self.noms = dict(presents)
        self.anciens = {p for p in self.anciens if p in presents}
        if not presents:
            return
        attendus = self.cfg["liens"] + self.cfg["hanteurs"]
        if not self.repartie:
            for pid in presents:
                if pid not in self.roles:
                    self.roles[pid] = ROLE_LINK
                    self.depuis_role[pid] = now
            if len(presents) >= attendus:
                self.repartir(now, "tout le monde est là")
                return
        else:
            for pid in sorted(presents):
                if pid in self.roles:
                    continue
                nh, nl = len(self._hanteurs()), len(self._liens())
                choisi = presents[pid] in self.cfg["choisis"]
                if (choisi or nl >= self.cfg["liens"]) and nh < self.cfg["hanteurs"] and nl >= 1:
                    self.roles[pid] = ROLE_HANTEUR
                    self.points[pid] = 0.0
                    self.anciens.add(pid)
                else:
                    self.roles[pid] = ROLE_LINK
                self.depuis_role[pid] = now
                self.sortie.append((pid, {"type": "hdevient", "role": self.roles[pid], "raison": "arrivée",
                                          "vie": self.cfg["vie_manche"]}))
        self._au_moins_un_link(now)
        self.t_diffusion = float("-inf")

    def liste_douteuse(self, pid, zone) -> bool:
        """Vrai : la liste d'ennemis de ``pid`` (autorité de ``zone``) ne doit pas être relayée « full » : son jeu a
        chargé cette salle quand il était Hanteur (le jeu d'un Hanteur n'est pas le monde des Link : il a pu y
        supprimer des monstres sur la foi d'une liste fausse) et il n'en est pas sorti depuis. Relayée « full », une
        liste vide ferait supprimer leurs vrais monstres aux autres joueurs (redevenu Link : plus aucun Link, échange
        de Crawl, « Rotation maintenant », fin de manche, reconnexion)."""
        return isinstance(zone, str) and self.doute.get(pid) == zone

    def ancien_hanteur(self, pid) -> bool:
        """Vrai : ce joueur a été Hanteur (ses inscriptions passent par ``filtre_inscription``)."""
        return pid in self.anciens

    def heriter(self, nouveau: int, ancien: int) -> None:
        """Reconnexion (même jeton, nouvel id) : le joueur garde son rôle, ses points, sa quarantaine et ses
        monstres. Sans cela, un Hanteur dont la connexion saute revenait en Link (« arrivée » : le quota de
        Hanteurs est encore rempli par son ancienne connexion) et la partie restait sans Hanteur."""
        if ancien not in self.roles or nouveau == ancien:
            return
        self.potion_fin_joueur(ancien, "reconnexion", self.clock())   # potion : le clone ou le possédé est parti
        self.enf_heriter(nouveau, ancien)          # enfermement : son Hanteur ou ses Link suivent la reconnexion
        self.roles[nouveau] = self.roles[ancien]
        for d in (self.points, self.depuis_role, self.quarantaine, self.v_scores, self.immunite, self.doute):
            if ancien in d:
                d[nouveau] = d[ancien]
        self.apparitions.pop(ancien, None)         # reconnecté = rechargé : pas de fumée d'apparition en retard
        for ap in self.apparitions.values():
            if ap.get("sauf") == ancien:
                ap["sauf"] = nouveau               # relecture adverse : le Link tombé reconnecté reste exclu
        if ancien in self.echanges_attente:        # échange de Crawl en attente : il suit le joueur reconnecté
            self.echanges_attente[nouveau] = self.echanges_attente.pop(ancien)
        for a in self.echanges_attente.values():
            if a["tueur"] == ancien:
                a["tueur"] = nouveau
        for (h, proc), t in list(self.recharges.items()):
            if h == ancien:
                self.recharges[(nouveau, proc)] = t
        if ancien in self.anciens:
            self.anciens.add(nouveau)
        for inv in self.invoques.values():
            if inv["owner"] == ancien:
                inv["owner"] = nouveau
        for victime, (h, t) in list(self.dernier_degat.items()):
            if h == ancien:
                self.dernier_degat[victime] = (nouveau, t)
        if ancien in self.possessions:
            self.possessions[nouveau] = self.possessions.pop(ancien)
            self.sortie.append((nouveau, self._msg_possede(self.possessions[nouveau], self.clock())))
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : {self.noms.get(ancien, ancien)} reconnecté (id {nouveau}) : reste "
                 f"{'Hanteur' if self.roles[nouveau] == ROLE_HANTEUR else 'Link'}")

    def _au_moins_un_link(self, now: float) -> None:
        if self.roles and not self._liens():
            h = max(self._hanteurs(), key=lambda p: (self.points.get(p, 0.0), -p))
            self._changer(h, ROLE_LINK, now, "plus aucun Link")

    def repartir(self, now: float | None = None, raison: str = "répartition") -> None:
        """Nouvelle répartition : les Hanteurs choisis d'abord, le reste tiré au hasard ; au moins un Link."""
        now = self.clock() if now is None else now
        ids = sorted(self.noms)
        if not ids:
            return
        cible = min(self.cfg["hanteurs"], max(0, len(ids) - 1))
        choisis = [p for p in ids if self.noms[p] in self.cfg["choisis"]][:cible]
        reste = [p for p in ids if p not in choisis]
        hanteurs = set(choisis) | set(self.rng.sample(reste, max(0, cible - len(choisis))))
        self.repartie = True
        for pid in ids:
            self.roles.setdefault(pid, ROLE_LINK)
        for pid in ids:
            self._changer(pid, ROLE_HANTEUR if pid in hanteurs else ROLE_LINK, now, raison)
        for pid in ids:
            self.points[pid] = 0.0
        self.immunite.clear()
        self.echanges_attente.clear()
        self.apparitions.clear()
        self.v_finie = False                       # partie finie par la mort de Ganon : l'hôte en relance une
        self.v_ganon.clear()
        self.t_rotation = now
        self.t_passif = now
        self.t_diffusion = float("-inf")
        self.log("Hanteurs : répartition (" + ", ".join(f"{self.noms[p]} = {self.role(p)}" for p in ids) + ")")
        self.victoire_debut(now, raison)           # une répartition ouvre une manche (compteurs à zéro)

    # --- états des joueurs ------------------------------------------------------------------------------
    def state(self, pid: int, msg: dict, zone: str | None, busy: bool, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        pos = _pos3(msg.get("pos"))
        rot = msg.get("rotY")
        e = self.etat.setdefault(pid, {})
        e.update(zone=zone, busy=bool(busy), stage=msg.get("stage") if isinstance(msg.get("stage"), str) else None,
                 room=msg.get("room") if isinstance(msg.get("room"), int) else None, t=now,
                 ship=a_bord(msg))
        if isinstance(zone, str):
            if self.roles.get(pid) == ROLE_HANTEUR:
                self.doute[pid] = zone              # voir liste_douteuse
            elif self.doute.get(pid) not in (None, zone):
                del self.doute[pid]                 # Link entré dans une autre zone : salle chargée en Link
        if pos is not None:
            e["pos"] = pos
            # dernier sol sûr (échange Crawl) : là où il se tenait en vie, hors cinématique et hors bateau, sa
            # hauteur stable depuis SOL_FENETRE. Le nouveau Link y est posé plutôt que là où le Link est tombé
            # (un Link mort en chute libre est au-dessus du vide).
            vie = msg.get("life")
            vivant = isinstance(vie, int) and not isinstance(vie, bool) and vie > 0
            ref = e.get("sol_ref")
            if zone is None or busy or e["ship"] or not vivant:
                e["sol_ref"] = None
            elif ref is None or ref[2] != zone or abs(pos[1] - ref[1]) > SOL_DY:
                e["sol_ref"] = (now, pos[1], zone)
            elif now - ref[0] >= SOL_FENETRE:
                e["sol"] = (pos, zone)
        if isinstance(rot, int) and not isinstance(rot, bool):
            e["rot"] = rot
        att = msg.get("att")
        if isinstance(att, dict) and isinstance(att.get("spec"), list) and len(att["spec"]) <= 16:
            e["spec"] = list(att["spec"])          # potion : le clone prend l'équipement du possédé
        if pid in self.possessions:
            self._pilote_state(pid, msg, pos, bool(busy), now)     # peut finir la possession (saut refusé)
        if pid in self.possessions:
            self._laisse(pid, e, now)
        if pid in self.apparitions:
            self._apparition_state(pid, e, pos, now)
        self.potion_state(pid, e, now)
        self.victoire_state(pid, zone, now)        # mode course : salle traversée par un Link
        life = msg.get("life")
        if not self.repartie or not isinstance(life, int) or isinstance(life, bool) or life < 0 \
                or self.role(pid) != ROLE_LINK:
            return
        v = self.vie.setdefault(pid, {"v": life, "zero": None, "ko": False})
        prev = v["v"]
        v["v"] = life
        # potion : un possédé ne prend pas de coups comptés (son Link est caché) ; sa vie baissée à la libération
        # (dégâts reçus par le clone) n'est le dégât de personne. Relecture adverse : la libération peut être
        # appliquée jusqu'à JEU_ATTENTE s après (cinématique) ; on retire donc de la baisse au plus les quarts
        # annoncés (``vie_ignore`` : [fin, quarts restants]) au lieu d'ignorer tout pendant une durée fixe.
        if life < prev and not busy and self.possede_par(pid) is None and self.quarantaine.get(pid, 0.0) <= now:
            perte = prev - life
            ig = self.vie_ignore.get(pid)
            if ig is not None and now < ig[0] and ig[1] > 0:
                pris = min(perte, ig[1])
                ig[1] -= pris
                perte -= pris
            if perte > 0:
                perte = _dif.perte_d_origine(perte, self.degats_mult)   # difficulté : pas le multiplicateur
                self._degat(pid, min(DEGAT_MAX, perte), zone, pos, now)
        if life > 0:
            v["zero"], v["ko"] = None, False
            v.pop("chute", None)
        elif v["zero"] is None:
            v["zero"] = now
            # fumée d'échange : là où il tombe, relevé MAINTENANT (son PC sera ensuite en Game Over, puis rechargé)
            v["chute"] = self._lieu_chute(e, zone)

    @staticmethod
    def _lieu_chute(e: dict, zone) -> tuple | None:
        """Où montrer la fumée d'échange de ce Link qui vient de tomber à 0 cœur : sa position de ce state (relevée
        par le serveur), ou son dernier sol sûr de la même salle s'il est bien plus bas (vide, lave) ou sans
        position. None sans salle connue (chargement). Une cinématique ne compte pas ici : la mort peut en lancer
        une chez lui, et la fumée est jouée chez les AUTRES, avec leurs propres gardes."""
        if zone is None:
            return None
        pos, sol = e.get("pos"), e.get("sol")
        if sol is not None and sol[1] == zone and (pos is None or pos[1] < sol[0][1] - FX_CHUTE_DY):
            pos = sol[0]
        return (tuple(pos), zone) if pos is not None else None

    def coup_relaye(self, victime: int, key: str, now: float | None = None) -> None:
        """player_hit relayé par le serveur (A2) : sa clé désigne le monstre qui a frappé."""
        self.coups[victime] = (key, self.clock() if now is None else now)

    def responsable(self, key) -> int | None:
        """Hanteur à créditer pour ce monstre : celui qui le possède, sinon celui qui l'a invoqué. Potion : clé
        ``pvp:<Hanteur>`` (coup d'épée du clone)."""
        if isinstance(key, str) and key.startswith("pvp:"):
            try:
                return int(key[4:])
            except ValueError:
                return None
        for h, ps in self.possessions.items():
            if ps["key"] == key:
                return h
        inv = self.invoques.get(key)
        return inv["owner"] if inv is not None else None

    def _degat(self, victime: int, quarts: int, zone, pos, now: float) -> None:
        owner, key = None, None
        k, t = self.coups.get(victime, (None, 0.0))
        if k is not None and now - t < 1.5 and self.responsable(k) is not None:
            key, owner = k, self.responsable(k)
        elif zone is not None and pos is not None:
            ts, snap = self.snaps.get(zone, (0.0, {}))
            if now - ts <= SNAP_AGE:
                best = None
                cles = {k2 for k2, inv in self.invoques.items() if inv["zone"] == zone}
                cles |= {ps["key"] for ps in self.possessions.values() if ps["zone"] == zone}
                for k2 in sorted(cles):
                    ent, h = snap.get(k2), self.responsable(k2)
                    if h is None or ent is None:
                        continue
                    d = math.dist(ent["pos"], pos)
                    if d <= DIST_ATTRIB and (best is None or d < best[0]):
                        best = (d, k2, h)
                if best:
                    _d, key, owner = best
        if owner is None or not self.est_hanteur(owner):
            return
        gain = PRESETS[self.cfg["points"]]["degat"] * quarts
        self._gagner(owner, gain)
        self.dernier_degat[victime] = (owner, now)
        self.victoire_degat(owner, quarts, now)    # mode score
        self.sortie.append((owner, {"type": "hnote", "msg": f"ton {self._nom_espece(key)} a blessé "
                                    f"{self.noms.get(victime, '?')} (+{gain} points)"}))

    def _nom_espece(self, key) -> str:
        if isinstance(key, str) and key.startswith("pvp:"):
            return "clone"
        k = parse_key(key)
        if not k:
            return "monstre"
        return ESPECES.get(k[0], laisse.POSSEDABLES.get(k[0], {})).get("nom", "monstre")

    def _gagner(self, pid: int, n: float) -> None:
        self.points[pid] = max(0.0, min(float(POINTS_MAX), self.points.get(pid, 0.0) + n))

    # --- snapshots de l'autorité ------------------------------------------------------------------------
    def snapshot(self, zone: str, lst, full: bool, now: float | None = None) -> None:
        """Snapshot d'ennemis accepté de l'autorité de ``zone`` : espèces vues, monstres invoqués vus ou morts,
        monstres tués par les Link (points)."""
        now = self.clock() if now is None else now
        if not isinstance(lst, list):
            return
        stage = zone.rpartition(":")[0]
        par_cle = {}
        vivants = self.vivants.setdefault(zone, set())
        for e in lst[:256]:
            if not isinstance(e, dict) or not isinstance(e.get("key"), str):
                continue
            pos = _pos3(e.get("pos"))
            if pos is None:
                continue
            key = e["key"]
            hp, max_hp = e.get("hp", 1), e.get("max_hp", 0)
            mort = e.get("dead") in (1, True) or (isinstance(max_hp, int) and max_hp > 0
                                                   and isinstance(hp, int) and hp <= 0)
            par_cle[key] = {"pos": pos, "mort": mort}
            k = parse_key(key)
            if k is None:
                continue
            if not mort:
                if k[0] in ESPECES and key not in self.invoques:
                    self.especes.setdefault(stage, {})[k[0]] = k[1]
                vivants.add(key)
                inv = self.invoques.get(key)
                if inv is not None:
                    inv["vu"] = now
                for ps in self.possessions.values():
                    if ps["key"] == key and ps["zone"] == zone:
                        ps["vu"], ps["mpos"] = now, pos
            elif key in vivants:
                vivants.discard(key)
                self._tue(key, now)
        if full:
            for key, inv in list(self.invoques.items()):
                if inv["zone"] == zone and inv.get("vu") is not None and key not in par_cle \
                        and now - inv["vu"] > 3.0:
                    del self.invoques[key]          # disparu sans pierre tombale (chute, salle rechargée)
        self.snaps[zone] = (now, par_cle)

    def _tue(self, key: str, now: float) -> None:
        possesseur = next((h for h, ps in self.possessions.items() if ps["key"] == key), None)
        if possesseur is not None:
            # pas de fumée de relâche : la fumée de mort du monstre joue déjà
            self.lacher(possesseur, f"ton {self._nom_espece(key)} est mort", now, effet=False)
        inv = self.invoques.pop(key, None)
        owner = inv["owner"] if inv else None
        gain = PRESETS[self.cfg["points"]]["kill"]
        for h in self._hanteurs():
            if h != owner and h != possesseur:      # le possesseur (et l'invocateur) perd sa mise, rien de plus
                self._gagner(h, gain)
        if inv:
            self.log(f"Hanteurs : {self._nom_espece(key)} invoqué tué ({key})")

    # --- invocation -------------------------------------------------------------------------------------
    def invoquer(self, pid: int, msg: dict, autorite: tuple[int | None, bool], now: float | None = None) -> str:
        """``hsummon`` d'un joueur. ``autorite`` = (id de l'autorité de sa zone, en cinématique). Renvoie ""
        (accepté : ``hspawn`` diffusé à la zone) ou la raison du refus (envoyée au Hanteur)."""
        now = self.clock() if now is None else now
        raison = self._refus(pid, msg, autorite, now)
        if raison:
            self.n_refus += 1
            self.sortie.append((pid, {"type": "hrefus", "proc": msg.get("proc"), "raison": raison}))
            return raison
        e = self.etat[pid]
        proc = msg["proc"]
        esp = especes_permises(self.cfg)[proc]
        params = params_invocation(proc, self.especes[e["stage"]][proc])
        pos = tuple(float(round(v)) for v in e["pos"])
        key = enemy_key(proc, params, pos)
        self.points[pid] -= esp["cout"]
        self.recharges[(pid, proc)] = now + esp["recharge"]
        inv = {"owner": pid, "zone": e["zone"], "stage": e["stage"], "room": e["room"], "proc": proc,
               "params": params, "pos": list(pos), "rotY": int(e.get("rot", 0)), "t": now, "vu": None,
               "cout": esp["cout"]}
        self.invoques[key] = inv
        self.n_invoques += 1
        self.sortie.append((("zone", e["zone"]), self.msg_spawn(key, inv)))
        self.log(f"Hanteurs : {self.noms.get(pid, pid)} invoque un {esp['nom']} ({key}, -{esp['cout']} points)")
        return ""

    @staticmethod
    def msg_spawn(key: str, inv: dict) -> dict:
        return {"type": "hspawn", "key": key, "proc": inv["proc"], "params": inv["params"], "pos": inv["pos"],
                "room": inv["room"], "stage": inv["stage"], "rotY": inv["rotY"], "owner": inv["owner"]}

    def _refus(self, pid: int, msg: dict, autorite, now: float) -> str:
        if not self.repartie:
            return "la partie n'a pas commencé (répartition des rôles en attente)"
        if not self.est_hanteur(pid):
            return "seuls les Hanteurs invoquent"
        t = [x for x in self.summon_t.get(pid, []) if now - x < 1.0]
        if len(t) >= SUMMON_RATE:
            self.summon_t[pid] = t
            return "trop de demandes"
        t.append(now)
        self.summon_t[pid] = t
        if pid in self.potions:
            return "tu joues un Link (potion)"
        proc = msg.get("proc")
        permises = especes_permises(self.cfg)
        if not isinstance(proc, int) or isinstance(proc, bool) or proc not in permises:
            return "espèce non invocable"
        esp = permises[proc]
        e = self.etat.get(pid) or {}
        zone, stage, pos = e.get("zone"), e.get("stage"), e.get("pos")
        if zone is None or stage is None or pos is None or e.get("room") is None or now - e.get("t", 0) > 3.0:
            return "position inconnue (jeu pas prêt ?)"
        from synchro import boss, cibles
        if boss.is_boss(proc, stage) or stage in cibles.STAGES_EXCLUES:
            return "pas d'invocation dans cette stage"
        if e.get("busy"):
            return "tu es en cinématique"
        if e.get("ship"):
            return "pas en bateau"
        vu = self.especes.get(stage, {}).get(proc)
        if vu is None or params_invocation(proc, vu) is None:
            return f"aucun {esp['nom']} vu vivant dans cette stage"
        liens = [o for o in self._liens() if (self.etat.get(o) or {}).get("zone") == zone]
        if not liens:
            return "aucun Link dans ta salle"
        for o in liens:
            p2 = self.etat[o].get("pos")
            if p2 is not None and math.dist(p2, pos) < DIST_LINK_MIN:
                return f"trop près de {self.noms.get(o, '?')} ({DIST_LINK_MIN:.0f} u au moins)"
        holder, busy = autorite
        if holder is None or self.est_hanteur(holder):
            return "pas d'autorité Link dans ta salle"
        if busy:
            return "l'autorité de la salle est en cinématique"
        n = sum(1 for inv in self.invoques.values() if inv["zone"] == zone)
        if n >= self.cfg["max_invoques"]:
            return f"déjà {n} monstres invoqués dans cette salle"
        r = self.recharges.get((pid, proc), 0.0) - now
        if r > 0:
            return f"{esp['nom']} : recharge, encore {r:.0f} s"
        if self.points.get(pid, 0.0) < esp["cout"]:
            return f"pas assez de points ({int(self.points.get(pid, 0.0))}/{esp['cout']})"
        key = enemy_key(proc, params_invocation(proc, vu), tuple(float(round(v)) for v in pos))
        if key in self.invoques:
            return "un monstre est déjà là"
        return ""

    def entree_zone(self, pid: int, zone: str | None) -> None:
        """Un joueur entre dans une zone : les monstres invoqués encore là lui sont renvoyés (création chez lui)."""
        if zone is None:
            return
        self.entree_zone_enf(pid, zone)
        for key, inv in self.invoques.items():
            if inv["zone"] == zone:
                # « rappel » : le monstre est déjà là pour les autres, pas de fumée d'invocation chez l'arrivant
                self.sortie.append((pid, dict(self.msg_spawn(key, inv), rappel=1)))

    # --- effets de fumée ---------------------------------------------------------------------------------
    def _apparition_state(self, pid: int, e: dict, pos, now: float) -> None:
        """State du nouveau Link (échange, rotation) : fumée d'apparition dès qu'il est vraiment là, une fois.
        Il faut : rôle Link, salle connue, hors cinématique, state reçu APRÈS l'échange ; dans la salle attendue
        (une autre salle : il a rechargé ailleurs, rien) ; s'il devait être téléporté, déjà près de cet endroit
        (sinon on attend, jusqu'à FX_APPARITION_ATTENTE). Position = celle de CE state, relevée par le serveur."""
        ap = self.apparitions.get(pid)
        if ap is None:
            return
        if self.roles.get(pid) != ROLE_LINK or now >= ap["fin"]:
            del self.apparitions[pid]
            return
        zone = e.get("zone")
        if zone is None or e.get("busy") or pos is None or now <= ap["t"]:
            return
        if ap["zone"] is not None and zone != ap["zone"]:
            del self.apparitions[pid]
            return
        if ap["pos"] is not None and math.dist(pos, ap["pos"]) > FX_APPARITION_DIST:
            return
        del self.apparitions[pid]
        self._fx("apparition", zone, pos, cible=pid, sauf=ap.get("sauf"))

    def _fx(self, quoi: str, zone, pos, key: str | None = None, cible: int | None = None,
            sauf: int | None = None) -> None:
        """Annonce un effet (``hfx``) aux joueurs de ``zone`` (le serveur filtre : fonction « effets », hors
        cinématique, state récent, réglage « hanteurs »). Rien si le réglage est « coupes », ni sans position sûre.
        ``pos`` vient toujours du serveur (relevé de l'autorité, state du Link), jamais du Hanteur qui agit."""
        if self.cfg.get("effets", "tous") == "coupes" or quoi not in EFFETS_QUOI:
            return
        p = _pos3(pos)
        if p is None or not isinstance(zone, str) or ":" not in zone:
            return
        stage, _, room = zone.rpartition(":")
        try:
            room = int(room)
        except ValueError:
            return
        msg = {"type": "hfx", "quoi": quoi, "pos": [round(v, 1) for v in p], "stage": stage, "room": room}
        if key is not None:
            msg["key"] = key
        if cible is not None:
            msg["cible"] = cible
        self.n_fx += 1
        self.sortie.append((("fx", zone, sauf), msg))

    # --- horloge ----------------------------------------------------------------------------------------
    def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if not self.repartie:
            if now - self.t_diffusion >= DIFFUSION:
                self.diffuser(now)
            return
        p = PRESETS[self.cfg["points"]]
        if self.t_passif is not None:
            dt = max(0.0, min(5.0, now - self.t_passif))
            for h in self._hanteurs():
                self._gagner(h, p["passif"] * dt)
        self.t_passif = now
        # monstres jamais vus chez l'autorité : création ratée, points rendus
        for key, inv in list(self.invoques.items()):
            if inv.get("vu") is None and now - inv["t"] > JAMAIS_VU:
                del self.invoques[key]
                if inv["owner"] is not None and self.est_hanteur(inv["owner"]):
                    self._gagner(inv["owner"], inv["cout"])
                    self.sortie.append((inv["owner"], {"type": "hnote", "msg": "invocation jamais vue chez "
                                                       f"l'autorité : {inv['cout']} points rendus"}))
        # possessions : durée maximale, monstre disparu ou trop loin
        for h, ps in list(self.possessions.items()):
            e = self.etat.get(h) or {}
            if now >= ps["fin"]:
                self.lacher(h, "durée maximale", now)
            elif now - ps["vu"] > laisse.VU_MAX:
                self.lacher(h, "monstre disparu", now, effet=False)
            elif e.get("pos") is not None and ps.get("mpos") is not None \
                    and math.dist(e["pos"], ps["mpos"]) > laisse.LAISSE_MAX:
                self.lacher(h, "trop loin de ton monstre", now)
        self.potion_tick(now)
        self._enfermement_tick(now)                # enfermements : durée, Hanteur parti, Link sortis, monstres battus
        self._influence_tick(now)                  # phase 4 : ordres de boss sans réponse (remboursés si personne)
        self._ko_tick(now, p)                      # K.O. des Link : points, victoire, échange « comme dans Crawl »
        self._echanges_tick(now)
        for pid, ap in list(self.apparitions.items()):
            if now >= ap["fin"] or self.roles.get(pid) != ROLE_LINK:
                del self.apparitions[pid]          # jamais vu apparaître à temps : pas de fumée en retard
        # minuterie
        if self.cfg["rotation"] in ("minuterie", "mixte") and not self.v_finie \
                and now - self.t_rotation >= self.cfg["rotation_min"] * 60:
            self.rotation(now, "minuterie")
        self._victoire_tick(now)                   # conditions de victoire : minuteur, fin de manche, pause
        if now - self.t_diffusion >= DIFFUSION:
            self.diffuser(now)

    # --- K.O. et échange « comme dans Crawl » -----------------------------------------------------------
    def _ko_tick(self, now: float, p: dict) -> None:
        """Link à 0 cœur depuis KO_DELAI (une fée le relève avant) : un K.O., compté une fois par mort. Les morts
        du même instant sont traitées dans l'ordre où elles sont arrivées (puis par id) : résultat déterministe."""
        morts = sorted((v["zero"], victime) for victime, v in self.vie.items()
                       if v["zero"] is not None and not v["ko"] and now - v["zero"] >= KO_DELAI)
        # Relecture adverse : avec « gagnent ceux qui sont Link à la fin », le K.O. qui finit la manche (survie,
        # score) était jugé AVANT l'échange de Crawl qu'il déclenche : le Link tout juste tué était déclaré
        # vainqueur, et son tueur, qui prend sa place de Link dans le même tick, perdait. Les K.O. sont donc
        # comptés pour la victoire après les échanges prêts. Sans ce réglage, l'ordre d'origine est gardé (la
        # règle crédite le camp des Hanteurs tel qu'il était au moment du K.O.).
        apres = self.link_fin()
        comptes = []
        for _zero, victime in morts:
            v = self.vie.get(victime)
            if v is None or v["ko"] or self.role(victime) != ROLE_LINK:
                continue
            v["ko"] = True
            h, t = self.dernier_degat.get(victime, (None, 0.0))
            attribue = h is not None and t >= v["zero"] - DERNIER_COUP
            credite = attribue and self.est_hanteur(h)
            vague = self.v_vague.get(victime, 0.0) >= now      # mort reçue du deathlink (lu avant victoire_ko)
            if apres:
                comptes.append((victime, h if credite else None))
            else:
                self.victoire_ko(victime, h if credite else None, now)   # toute mort d'un Link compte
            if credite:
                self._gagner(h, p["ko"])
                self.log(f"Hanteurs : {self.noms.get(h, h)} a mis {self.noms.get(victime, victime)} K.O.")
            self._crawl(victime, h if attribue else None, credite, vague, now)
        if comptes:
            self._echanges_tick(now)
            for victime, h in comptes:
                self.victoire_ko(victime, h, now)

    def crawl_actif(self) -> bool:
        return self.cfg["rotation"] in ROTATIONS_CRAWL

    def _pas_echange(self, victime: int, raison: str, now: float, tueur: int | None = None) -> None:
        """Un échange de Crawl n'a pas lieu : compté, écrit au journal, dit au tué et au tueur."""
        self.n_echanges_refuses += 1
        self.log(f"Hanteurs : pas d'échange pour le K.O. de {self.noms.get(victime, victime)} ({raison})")
        for pid in (victime, tueur):
            if pid is not None and pid in self.noms:
                self.sortie.append((pid, {"type": "hnote", "msg": f"pas d'échange de rôle : {raison}"}))

    def _crawl(self, victime: int, h: int | None, credite: bool, vague: bool, now: float) -> None:
        """Règle de Crawl pour ce K.O. ``h`` : le Hanteur du dernier dégât attribué (None : personne) ; ``credite`` :
        il est encore Hanteur. L'échange est inscrit (un seul par mort) et fait dès que le tueur est prêt."""
        if not self.crawl_actif() or self.v_finie:
            return
        if vague:
            # deathlink : la mort d'un autre, jamais un échange. Relecture adverse : même « créditée ». La vie d'un
            # Link qui reçoit la vague tombe d'un coup à 0 ; cette baisse est attribuée comme un dégât au monstre
            # invoqué ou possédé le plus proche (``_degat``), et chaque Link de la vague aurait changé de place
            # avec un Hanteur différent (échanges en chaîne pour une seule vraie mort).
            if credite:
                self._pas_echange(victime, "mort reçue du deathlink", now, h)
            return
        tueur, raison = (h, "K.O. (règle de Crawl)") if credite else (None, "")
        a = self.echanges_attente.get(victime)
        if a is not None:
            # Relecture adverse : il s'est relevé puis a été retué pendant qu'un échange l'attendait encore (son
            # premier tueur est en chargement ou en cinématique). Un AUTRE Hanteur vient de le tuer : c'est lui
            # qui prend la place (sinon cette mort n'échangeait rien, et la première expirait). Sinon rien de neuf.
            if tueur is None or tueur == a["tueur"]:
                return
            del self.echanges_attente[victime]
            self.n_echanges_refuses += 1
            if a["tueur"] in self.noms:
                self.sortie.append((a["tueur"], {"type": "hnote", "msg": "pas d'échange de rôle : "
                                                 f"{self.noms.get(tueur, '?')} a tué "
                                                 f"{self.noms.get(victime, '?')} entre-temps"}))
        if tueur is None:
            if h is not None and h in self.roles:
                # son tueur est déjà redevenu Link (il a tué deux Link presque en même temps) : une seule place
                self._pas_echange(victime, f"{self.noms.get(h, '?')} a déjà pris une place de Link", now)
                return
            if vague:
                return                             # deathlink : la mort d'un autre, jamais un échange
            if self.cfg["crawl_sans_tueur"] != "meilleur":
                return                             # chute, monstre sauvage : il reste Link (règle par défaut)
            pris = {a["tueur"] for a in self.echanges_attente.values()}
            cands = [x for x in self._hanteurs() if x not in pris and self.immunite.get(x, 0.0) <= now]
            if not cands:
                self._pas_echange(victime, "aucun Hanteur disponible", now)
                return
            tueur = max(cands, key=lambda x: (self.points.get(x, 0.0), -x))
            raison = "K.O. sans tueur (meilleur Hanteur)"
        if self.immunite.get(victime, 0.0) > now or self.immunite.get(tueur, 0.0) > now:
            self._pas_echange(victime, "échange trop récent (immunité)", now, tueur)
            return
        if any(a["tueur"] == tueur for a in self.echanges_attente.values()):
            self._pas_echange(victime, f"{self.noms.get(tueur, '?')} prend déjà une autre place de Link", now)
            return
        e = self.etat.get(victime) or {}
        sol = e.get("sol")
        pos, zone = e.get("pos"), e.get("zone")
        if sol is not None and sol[1] == zone:
            pos = sol[0]                           # dernier sol sûr dans la salle où il est tombé
        self.echanges_attente[victime] = {"tueur": tueur, "t": now, "fin": now + ECHANGE_ATTENTE, "pos": pos,
                                          "zone": zone, "raison": raison,
                                          "chute": (self.vie.get(victime) or {}).get("chute")}

    def _tueur_pret(self, tueur: int, now: float) -> bool:
        """Le futur Link peut-il changer de rôle maintenant ? Pas pendant un chargement (plus de state récent, ou
        state sans salle) ni une cinématique : sa vie de manche et sa position ne seraient pas appliquées (le
        client les abandonne après JEU_ATTENTE) et il resterait caché à lui-même jusqu'à la fin de l'événement."""
        e = self.etat.get(tueur) or {}
        return e.get("zone") is not None and now - e.get("t", float("-inf")) <= ETAT_FRAIS and not e.get("busy")

    def _echanges_tick(self, now: float) -> None:
        for victime, a in sorted(self.echanges_attente.items(), key=lambda x: (x[1]["t"], x[0])):
            tueur = a["tueur"]
            if not self.crawl_actif() or self.v_finie:
                del self.echanges_attente[victime]
                continue
            if not self.est_hanteur(tueur) or self.roles.get(victime) != ROLE_LINK:
                # rôles changés entre-temps (minuterie, hôte, départ) : cette mort n'échange plus rien
                del self.echanges_attente[victime]
                self._pas_echange(victime, "les rôles ont changé entre-temps", now, tueur)
                continue
            if not self._tueur_pret(tueur, now):
                if now >= a["fin"]:
                    del self.echanges_attente[victime]
                    self._pas_echange(victime, f"{self.noms.get(tueur, '?')} est resté en chargement ou en "
                                      "cinématique", now, tueur)
                continue
            del self.echanges_attente[victime]
            pos, zone = a["pos"], a["zone"]
            from synchro import boss
            if isinstance(zone, str) and zone.rpartition(":")[0] in boss.BOSS_STAGES:
                pos = None                         # arène de boss : il reste où était son Link caché (pas de saut
                #                                    dans la lave, la gueule du boss ou la zone d'une cinématique)
            # ``tombe`` : le Link tué ne reçoit pas non plus la fumée d'apparition (son PC est en Game Over)
            self.echanger(tueur, victime, now, a["raison"], pos, zone, tombe=victime)
            chute = a.get("chute")
            if chute is not None:
                # grosse fumée là où le Link est tombé, MAINTENANT (l'échange a vraiment lieu), pour les autres
                # joueurs de cette salle ; jamais chez lui (Game Over, rechargement)
                self._fx("echange", chute[1], chute[0], cible=victime, sauf=victime)
            fin = now + self.cfg["crawl_immunite"]
            self.immunite[tueur] = self.immunite[victime] = fin
            if self.cfg["rotation"] == "mixte":
                self.t_rotation = now              # la minuterie repart : jamais deux échanges coup sur coup
            note = (f"{self.noms.get(tueur, '?')} prend la place de Link de {self.noms.get(victime, '?')}, "
                    "qui devient Hanteur")
            for pid in sorted(self.noms):
                self.sortie.append((pid, {"type": "hnote", "msg": note}))

    # --- fin de la partie « comme dans Crawl » : la mort de Ganon ---------------------------------------
    def fin_ganon_active(self) -> bool:
        return (self.crawl_actif() or self.link_fin()) and bool(self.cfg["crawl_fin_ganon"])

    def boss_rapport(self, pid: int, rec: dict, now: float | None = None) -> bool:
        """Rapport ``boss`` validé d'un joueur (wwmp/boss_pv.valid_boss). Ganondorf sous GANON_FIN_PV chez un Link
        qui agit sur le monde, valeur tenue GANON_CONFIRME s (rappels du client toutes les 2 s) : sa fin scénarisée
        est lancée, la partie « comme dans Crawl » est finie. True si la fin vient d'être annoncée."""
        from synchro import boss_pv
        now = self.clock() if now is None else now
        hp = rec.get("hp")
        if boss_pv.key_proc(rec.get("key")) != GANON_PROC or not isinstance(hp, int) or isinstance(hp, bool):
            return False
        if hp >= GANON_FIN_PV:
            self.v_ganon.pop(pid, None)
            self.v_ganon_arme = True               # un Ganondorf debout : ce combat pourra finir la partie
            return False
        if not self.repartie or self.roles.get(pid) != ROLE_LINK or self.bloque_monde(pid, now):
            return False
        t0 = self.v_ganon.setdefault(pid, now)
        if now - t0 < GANON_CONFIRME or not self.v_ganon_arme:
            return False
        return self.fin_ganon(now, f"Ganon est vaincu ({self.noms.get(pid, '?')})")

    def fin_ganon(self, now: float | None = None, raison: str = "Ganon est vaincu") -> bool:
        """Fin de la partie « comme dans Crawl » : gagnent ceux qui sont Link maintenant. Une seule fois ; plus
        d'échange, de rotation ni de manche ensuite (l'hôte relance par « Nouvelle répartition »)."""
        now = self.clock() if now is None else now
        if not self.repartie or self.v_finie or not self.fin_ganon_active():
            return False
        self.v_ganon_arme = False                  # réarmé seulement par un Ganondorf revu au-dessus de 25 PV
        self.v_ganon.clear()
        self.echanges_attente.clear()
        self.victoire_finale([x for x in self._liens() if x in self.noms], raison, now)
        return True

    def rotation(self, now: float | None = None, raison: str = "rotation") -> bool:
        """Le Hanteur le plus riche devient Link, le Link le plus ancien devient Hanteur."""
        now = self.clock() if now is None else now
        self.t_rotation = now
        hs, ls = self._hanteurs(), self._liens()
        if not hs or not ls or self.v_finie:       # partie finie (Ganon vaincu) : les gagnants restent Link
            return False
        h = max(hs, key=lambda x: (self.points.get(x, 0.0), -x))
        link = min(ls, key=lambda x: (self.depuis_role.get(x, 0.0), x))
        self.echanger(h, link, now, raison)
        return True

    def echanger(self, hanteur: int, link: int, now: float, raison: str, pos=None, zone=None,
                 tombe: int | None = None) -> None:
        """Le Hanteur devient Link (téléporté en ``pos`` s'il est dans la même salle ``zone``), le Link devient
        Hanteur (il reste où il est). Fumée d'apparition du nouveau Link armée (``apparitions``) ; ``tombe`` : le
        Link tué (échange de Crawl), qui ne la reçoit pas."""
        h_zone = (self.etat.get(hanteur) or {}).get("zone")
        teleport = pos is not None and zone is not None and zone == h_zone
        self._changer(hanteur, ROLE_LINK, now, raison, pos if teleport else None, zone if teleport else None)
        self._changer(link, ROLE_HANTEUR, now, raison)
        if self.roles.get(hanteur) == ROLE_LINK:
            # fumée d'apparition du nouveau Link : attendue dans sa salle (celle du tué s'il y est téléporté), jouée
            # quand son state de Link y arrive vraiment (``_apparition_state``)
            self.apparitions[hanteur] = {"t": now, "fin": now + FX_APPARITION_ATTENTE,
                                         "zone": zone if teleport else h_zone,
                                         "pos": _pos3(pos) if teleport else None, "sauf": tombe}
        self.n_echanges += 1
        self.t_diffusion = float("-inf")

    # --- diffusion --------------------------------------------------------------------------------------
    def vue_publique(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        rot = self.cfg["rotation"]
        dans = None
        if self.repartie and rot in ("minuterie", "mixte"):
            dans = max(0, int(self.cfg["rotation_min"] * 60 - (now - self.t_rotation)))
        esp = especes_permises(self.cfg)
        return {"type": "hanteurs", "on": 1, "attente": not self.repartie,
                "roles": {str(p): r for p, r in sorted(self.roles.items())},
                "points": {str(p): int(self.points.get(p, 0.0)) for p in self._hanteurs()},
                "noms": {str(p): n for p, n in sorted(self.noms.items())},
                "especes": {str(proc): {"nom": e["nom"], "cout": e["cout"], "recharge": e["recharge"]}
                            for proc, e in esp.items()},
                "vues": {st: sorted(p for p in procs if p in esp) for st, procs in self.especes.items()},
                "rotation": rot, "rotation_dans": dans, "points_bareme": self.cfg["points"],
                "liens_voulus": self.cfg["liens"], "hanteurs_voulus": self.cfg["hanteurs"],
                "possession": int(self.cfg["possession"]), "possession_cout": cout_possession(self.cfg),
                "possession_max": self.cfg["possession_max"],
                "potion": int(self.cfg["potion"]), "potion_cout": potion.cout_potion(self.cfg, PRESETS),
                "potion_duree": self.cfg["potion_duree"], "pvp_degats": self.cfg["pvp_degats"],
                "potion_depense": self.depense_reglee(), "potion_plafond": self.cfg["potion_plafond"],
                "potion_resister": int(bool(self.cfg["potion_resister"])),
                "possedes": [str(p) for p in self.possedes()],
                "pilotage": int(self.cfg["pilotage"]), "recharge_attaques": self.cfg["recharge_attaques"],
                "pilotage_essai": list(self.cfg["pilotage_essai"]),
                "capacites": int(bool(self.cfg["capacites"])), "recharge_capacites": self.cfg["recharge_capacites"],
                "capacites_coupees": list(self.cfg["capacites_coupees"]), "garde_fer": self.cfg["garde_fer"],
                "rage_duree": self.cfg["rage_duree"],
                "crawl": {"sans_tueur": self.cfg["crawl_sans_tueur"], "immunite": self.cfg["crawl_immunite"],
                          "fin_ganon": int(self.fin_ganon_active())},
                "effets": self.cfg["effets"],
                **self.vue_influence(now), **self.vue_victoire(now), **self.vue_enfermement(now)}

    def diffuser(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        self.t_diffusion = now
        pub = self.vue_publique(now)
        for pid in sorted(self.noms):
            ps = self.possessions.get(pid)
            moi = {"role": self.role(pid), "points": int(self.points.get(pid, 0.0)),
                   "quarantaine": round(max(0.0, self.quarantaine.get(pid, now) - now), 1),
                   "immunite": round(max(0.0, self.immunite.get(pid, now) - now), 1),
                   "recharges": {str(proc): round(t - now, 1) for (h, proc), t in self.recharges.items()
                                 if h == pid and t > now and isinstance(proc, int)},
                   "possede": ps["key"] if ps else None,
                   "recharge_possession": round(max(0.0, self.recharges.get((pid, "possession"), now) - now), 1),
                   "cible_laisse": ps.get("cible") if ps else None,
                   # recharge de chaque geste du monstre possédé (s restantes ; rien : prêt)
                   "recharges_gestes": ({g: round(t - now, 1) for g, t in ps.get("rech", {}).items() if t > now}
                                        if ps else {})}
            moi.update(self.vue_potion(pid, now))
            moi.update(self.moi_influence(pid, now))
            moi.update(self.moi_enfermement(pid, now))
            self.sortie.append((pid, dict(pub, moi=moi)))

    # --- phase 1 : possession par laisse (synchro/laisse.py) -----------------------------------------------
    def manifester(self, pid: int, on, now: float | None = None) -> None:
        """``hmanif`` : un Hanteur se manifeste. Les Link de sa salle reçoivent un indice (« une présence
        rôde… », au plus toutes les 15 s par Hanteur) ; redevenir spectateur relâche la possession."""
        now = self.clock() if now is None else now
        if not self.repartie or not self.est_hanteur(pid) or pid in self.potions:
            return
        if on not in (1, True):
            self.lacher(pid, "redevenu spectateur", now)
            return
        e = self.etat.get(pid) or {}
        if e.get("zone") is None or now - self.manif_t.get(pid, float("-inf")) < 15.0:
            return
        self.manif_t[pid] = now
        for o in self._liens():
            if (self.etat.get(o) or {}).get("zone") == e["zone"]:
                self.sortie.append((o, {"type": "hnote", "msg": "une présence rôde dans la salle…"}))

    def posseder(self, pid: int, msg: dict, autorite: tuple[int | None, bool], now: float | None = None) -> str:
        """``hposseder`` : le Hanteur prend le contrôle du monstre possédable le plus proche (ou ``key`` s'il est
        assez près), vu vivant dans le dernier snapshot de l'autorité de sa salle. Renvoie "" (accepté :
        ``hpossede`` au Hanteur) ou la raison du refus (``hrefus`` avec ``quoi: possession``)."""
        now = self.clock() if now is None else now
        raison, key = self._refus_possession(pid, msg, autorite, now)
        if raison:
            self.n_refus += 1
            self.sortie.append((pid, {"type": "hrefus", "quoi": "possession", "raison": raison}))
            return raison
        e = self.etat[pid]
        cout = cout_possession(self.cfg)
        self.points[pid] -= cout
        ts, snap = self.snaps[e["zone"]]
        ps = {"key": key, "zone": e["zone"], "stage": e["stage"], "proc": parse_key(key)[0], "t0": now,
              "fin": now + self.cfg["possession_max"], "atk": 0, "t_atk": float("-inf"), "vu": now, "ord": None,
              "mpos": snap[key]["pos"], "cout": cout}
        self.possessions[pid] = ps
        self.n_possessions += 1
        self.sortie.append((pid, self._msg_possede(ps, now)))
        self._fx("possede", ps["zone"], ps["mpos"], key=key)       # position = relevé de l'autorité
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : {self.noms.get(pid, pid)} possède un {self._nom_espece(key)} ({key}, -{cout} points)")
        return ""

    def _msg_possede(self, ps: dict, now: float) -> dict:
        mult = self.cfg["recharge_attaques"]
        return {"type": "hpossede", "key": ps["key"], "nom": self._nom_espece(ps["key"]),
                "duree": round(max(0.0, ps["fin"] - now), 1),
                # possession complète : le serveur accepte le pilotage de cette espèce ; recharge de chaque geste
                "pilotage": int(bool(self.cfg["pilotage"]
                                     and pilotage.pilotable(ps["proc"], self.cfg["pilotage_essai"]))),
                "recharges": {g: pilotage.recharge(ps["proc"], g, mult) for g in ordres.GESTES},
                # capacité propre de l'espèce (synchro/capacites.py) : nom et ce qu'elle fait, ou absente
                "capacite": self._vue_capacite(ps["proc"])}

    def _vue_capacite(self, proc) -> dict | None:
        c = capacites.capacite(proc, self.cfg)
        if c is None:
            return None
        return {"cle": c.cle, "nom": c.nom, "en": c.en, "aide": c.aide, "genre": c.genre,
                "recharge": capacites.recharge(proc, self.cfg)}

    def _refus_possession(self, pid: int, msg: dict, autorite, now: float) -> tuple[str, str | None]:
        if not self.repartie:
            return "la partie n'a pas commencé", None
        if not self.est_hanteur(pid):
            return "seuls les Hanteurs possèdent", None
        if not self.cfg["possession"]:
            return "possession coupée dans cette partie", None
        t = [x for x in self.poss_t.get(pid, []) if now - x < 1.0]
        if len(t) >= SUMMON_RATE:
            self.poss_t[pid] = t
            return "trop de demandes", None
        t.append(now)
        self.poss_t[pid] = t
        if pid in self.possessions:
            return "tu possèdes déjà un monstre (F4 pour le relâcher)", None
        if pid in self.potions:
            return "tu joues un Link (potion)", None
        e = self.etat.get(pid) or {}
        zone, stage, pos = e.get("zone"), e.get("stage"), e.get("pos")
        if zone is None or stage is None or pos is None or now - e.get("t", 0) > 3.0:
            return "position inconnue (jeu pas prêt ?)", None
        from synchro import boss, cibles
        if stage in cibles.STAGES_EXCLUES or stage in boss.BOSS_STAGES:
            return "pas de possession dans cette stage", None
        if e.get("busy"):
            return "tu es en cinématique", None
        if e.get("ship"):
            return "pas en bateau", None
        holder, busy = autorite
        if holder is None or self.est_hanteur(holder):
            return "pas d'autorité Link dans ta salle", None
        if busy:
            return "l'autorité de la salle est en cinématique", None
        r = self.recharges.get((pid, "possession"), 0.0) - now
        if r > 0:
            return f"possession : recharge, encore {r:.0f} s", None
        cout = cout_possession(self.cfg)
        if self.points.get(pid, 0.0) < cout:
            return f"pas assez de points ({int(self.points.get(pid, 0.0))}/{cout})", None
        ts, snap = self.snaps.get(zone, (0.0, {}))
        if now - ts > SNAP_AGE:
            return "aucun relevé récent des monstres de ta salle", None
        permis = laisse.possedables(self.cfg["especes_hypo"], self.cfg["pilotage_essai"],
                                    formes=bool(self.cfg["pilotage"]))
        pris = {ps["key"] for ps in self.possessions.values()}
        voulu = msg.get("key") if isinstance(msg.get("key"), str) else None
        best, deja = None, False
        for key, ent in snap.items():
            k = parse_key(key)
            if ent["mort"] or k is None or k[0] not in permis or boss.is_boss(k[0], stage) \
                    or not laisse.eligible(k[0], k[1], stage):
                continue
            if voulu is not None and key != voulu:
                continue
            d = laisse.distance_possession(k[0], ent["pos"], pos)
            if d > laisse.RAYON:
                continue
            if key in pris:
                deja = True
                continue
            if best is None or (d, key) < best:
                best = (d, key)
        if best is None:
            if deja:
                return "ce monstre est déjà possédé", None
            return f"aucun monstre possédable à moins de {laisse.RAYON:.0f} u", None
        return "", best[1]

    def lacher(self, pid: int, raison: str, now: float | None = None, effet: bool = True) -> bool:
        """Fin de la possession de ``pid`` (s'il en a une) : recharge, ``hlache`` au Hanteur, ``hlaisse`` « fin » à
        l'autorité de la zone (elle relâche aussi seule, faute d'ordres)."""
        ps = self.possessions.pop(pid, None)
        if ps is None:
            return False
        now = self.clock() if now is None else now
        self.recharges[(pid, "possession")] = now + laisse.RECHARGE
        self.sortie.append((pid, {"type": "hlache", "key": ps["key"], "raison": raison}))
        self.sortie.append((("autorite", ps["zone"]), {"type": "hlaisse", "key": ps["key"], "fin": 1}))
        if effet:                                  # petite fumée de relâche sur le monstre (pas s'il est mort)
            self._fx("lache", ps["zone"], ps.get("mpos"), key=ps["key"])
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : {self.noms.get(pid, pid)} relâche {ps['key']} ({raison})")
        return True

    def ordre(self, pid: int, msg: dict, now: float | None = None) -> None:
        """``hordre {ordre}`` : geste du Hanteur qui possède un monstre (au plus un toutes les ORDRE_PERIODE s).
        « attaque » (coup d'épée) et « lourde » (attaque tournoyante, coup sauté) : élan (phase 1) et ordre ; « garde »
        (F3) : ordre seul. Phase 3 (synchro/ordres.py) : le dernier ordre part à l'autorité dans le ``hlaisse``
        (``ord = [séquence, geste]``), qui le joue par CALL 10 si son pack sait."""
        now = self.clock() if now is None else now
        ps = self.possessions.get(pid)
        g = msg.get("ordre")
        if ps is None or (g not in ordres.GESTES and g != capacites.GESTE) \
                or now - ps["t_atk"] < laisse.ORDRE_PERIODE:
            return
        if g == capacites.GESTE:
            self._capacite(pid, ps, now)
            return
        # espèce sans coup lourd : la tournoyante du Hanteur joue SON attaque chez l'autorité (``ordres.choisir``
        # retombe sur « attaque », l'appât prend les deux) ; c'est donc le geste « attaque », avec SA recharge.
        # Sinon, en alternant coup d'épée et tournoyante, la même attaque partirait deux fois par recharge.
        if g == "lourde" and "lourde" not in ordres.gestes_espece(ps["proc"], True):
            g = "attaque"
        # recharge du geste (par espèce, × réglage « recharge des attaques »), tenue ici : un geste en recharge
        # n'est ni compté ni relayé ; le Hanteur l'apprend (au plus un refus toutes les 0,5 s)
        rech = ps.setdefault("rech", {})
        reste = rech.get(g, float("-inf")) - now
        if reste > 0:
            self.n_gestes_refuses += 1
            if now - ps.get("t_refus", float("-inf")) >= 0.5:
                ps["t_refus"] = now
                self.sortie.append((pid, {"type": "hrefus", "quoi": "geste", "geste": g, "reste": round(reste, 1),
                                          "raison": f"{g} : recharge, encore {reste:.1f} s"}))
            return
        duree = pilotage.recharge(ps["proc"], g, self.cfg["recharge_attaques"])
        # frénésie en cours (capacité du Bokoblin, du Keese) : ses attaques natives reviennent plus vite
        duree = capacites.frenesie(ps["proc"], g, duree, now < ps.get("frenesie_fin", float("-inf")))
        if duree > 0:
            rech[g] = now + duree
            self.sortie.append((pid, {"type": "hrecharge", "key": ps["key"], "geste": g, "duree": duree,
                                      "gestes": {k: round(t - now, 1) for k, t in rech.items() if t > now}}))
        ps["t_atk"] = now
        if g in ("attaque", "lourde"):
            ps["atk"] = (ps["atk"] + 1) & 0xFFFF
        seq = ((ps["ord"][0] if ps.get("ord") else 0) + 1) & 0xFFFF
        ps["ord"] = [seq, g]
        # lot L3 (A6, docs/etudes/rollback_netcode.md §4) : l'ordre part tout de suite à l'autorité, dans un
        # ``hlaisse`` refait depuis le dernier état du Hanteur (avant : au prochain ``state``, 0 à 50 ms plus tard)
        e = ps.get("e_laisse")
        if e is not None and pid in self.possessions:
            self._laisse(pid, e, now)

    def _capacite(self, pid: int, ps: dict, now: float) -> None:
        """``hordre {ordre: "capacite"}`` : la capacité propre de l'espèce possédée (synchro/capacites.py), avec sa
        recharge (tenue ici, × « recharge des capacités »). Genre « jeu » : part à l'autorité de la salle dans le
        ``hlaisse`` (CALL 20 chez elle) ; genre « frenesie » : les recharges des attaques natives sont réduites ici
        pendant ``rage_duree`` s. Aucune trace chez les Link (le Hanteur reste caché) ; la possession garde sa durée."""
        c = capacites.capacite(ps["proc"], self.cfg)
        rech = ps.setdefault("rech", {})
        if c is None:
            if now - ps.get("t_refus", float("-inf")) >= 0.5:
                ps["t_refus"] = now
                if not self.cfg["capacites"]:
                    raison = "capacités coupées dans cette partie"
                elif ps["proc"] in self.cfg["capacites_coupees"]:
                    raison = "capacité de cette espèce coupée dans cette partie"
                elif ps["proc"] in capacites.CAPACITES:
                    raison = "espèce à l'essai : sa capacité demande le réglage « espèces à l'essai »"
                else:
                    raison = capacites.raison_absente(ps["proc"])
                self.sortie.append((pid, {"type": "hrefus", "quoi": "geste", "geste": capacites.GESTE, "reste": 0,
                                          "raison": f"capacité : {raison}"[:160]}))
            return
        reste = rech.get(capacites.GESTE, float("-inf")) - now
        if reste > 0:
            self.n_gestes_refuses += 1
            if now - ps.get("t_refus", float("-inf")) >= 0.5:
                ps["t_refus"] = now
                self.sortie.append((pid, {"type": "hrefus", "quoi": "geste", "geste": capacites.GESTE,
                                          "reste": round(reste, 1),
                                          "raison": f"{c.nom} : recharge, encore {reste:.1f} s"}))
            return
        duree = capacites.recharge(ps["proc"], self.cfg)
        rech[capacites.GESTE] = now + duree
        ps["t_atk"] = now
        self.n_capacites += 1
        self.sortie.append((pid, {"type": "hrecharge", "key": ps["key"], "geste": capacites.GESTE, "duree": duree,
                                  "gestes": {k: round(t - now, 1) for k, t in rech.items() if t > now}}))
        if c.genre == "frenesie":
            ps["frenesie_fin"] = now + self.cfg["rage_duree"]
            self.sortie.append((pid, {"type": "hnote", "msg": f"{c.nom} : {self.cfg['rage_duree']:g} s"}))
            return
        seq = ((ps["ord"][0] if ps.get("ord") else 0) + 1) & 0xFFFF
        ps["ord"] = [seq, capacites.GESTE]
        e = ps.get("e_laisse")
        if e is not None and pid in self.possessions:
            self._laisse(pid, e, now)

    def viser(self, pid: int, msg: dict, now: float | None = None) -> None:
        """Phase 2, ``hviser {cible}`` : le Link que vise le monstre possédé (id d'un Link de la salle, ou None :
        le plus proche). Appliqué par l'autorité si ce Link est visable (A3) et à portée."""
        now = self.clock() if now is None else now
        ps = self.possessions.get(pid)
        if ps is None:
            return
        # relecture adverse : chaque hviser fait diffuser l'état de la partie à tous ; au plus VISER_RATE par seconde
        t = [x for x in ps.get("t_vis", []) if now - x < 1.0]
        if len(t) >= VISER_RATE:
            ps["t_vis"] = t
            return
        ps["t_vis"] = t + [now]
        c = msg.get("cible")
        ok = (isinstance(c, int) and not isinstance(c, bool) and self.role(c) == ROLE_LINK
              and self.possede_par(c) is None and (self.etat.get(c) or {}).get("zone") == ps["zone"])
        ps["cible"] = c if ok else None
        nom = self.noms.get(c, "?") if ok else "le Link le plus proche"
        self.sortie.append((pid, {"type": "hnote", "msg": f"ton {self._nom_espece(ps['key'])} vise {nom}"}))
        self.t_diffusion = float("-inf")

    def _pilote_state(self, pid: int, msg: dict, pos, busy: bool, now: float) -> None:
        """Possession complète : le ``state`` d'un Hanteur qui pilote porte ``pil: [vx, vz]`` (vitesse de son Link
        caché ; ``[vx, vz, hauteur]`` pour un volant : commande de hauteur, bornée). Vitesse nettoyée et plafonnée (``VIT_PLAFOND`` × vmax de l'espèce) ; un saut de plus de ``SAUT_MAX``
        entre deux états finit la possession, sauf s'il arrive près du monstre (recollage du client). Sans le
        champ, réglage coupé ou espèce non pilotée : ``vit`` None (laisse classique)."""
        ps = self.possessions[pid]
        ps["vit"] = None
        ps["haut"] = 0.0
        if busy or pos is None:
            ps["hpos"] = None                      # cinématique : la position du Link caché n'est plus suivie
            return
        prev, ps["hpos"] = ps.get("hpos"), pos
        esp = pilotage.espece(ps["proc"])
        if not self.cfg["pilotage"] or "pil" not in msg or esp is None \
                or not pilotage.pilotable(ps["proc"], self.cfg["pilotage_essai"]):
            return
        vit = pilotage.champ_pil(msg.get("pil"), esp.vmax)
        if vit is None:
            return
        if prev is not None and math.dist(prev, pos) > pilotage.SAUT_MAX:
            # un volant est tenu AU-DESSUS du Link caché : l'écart au monstre se mesure à l'horizontale
            mp = _pos3(ps.get("mpos"))
            if mp is None or laisse.distance_possession(ps["proc"], mp, pos) > pilotage.RECOLLAGE_SERVEUR:
                self.lacher(pid, "saut de position refusé (pilotage)", now)
                return
        ps["vit"] = vit
        if pilotage.hauteur_vol(esp):
            ps["haut"] = pilotage.champ_haut(msg.get("pil"))

    def retour_pilote(self, zone: str | None, msg: dict, now: float | None = None) -> bool:
        """``hpilote`` de l'AUTORITÉ de ``zone`` (vérifiée par le serveur) : relayé au Hanteur qui possède ce
        monstre dans cette zone, nettoyé, au plus ~25 par seconde. True s'il est parti."""
        now = self.clock() if now is None else now
        key = msg.get("key")
        pos = _pos3(msg.get("pos"))
        if zone is None or not isinstance(key, str) or pos is None \
                or not all(math.isfinite(v) and abs(v) < 1e7 for v in pos):
            return False
        for pid, ps in self.possessions.items():
            if ps["key"] == key and ps["zone"] == zone:
                break
        else:
            return False
        if now - ps.get("t_ret", float("-inf")) < 0.04:
            return False
        ps["t_ret"] = now
        out = {"type": "hpilote", "key": key, "ok": int(msg.get("ok") in (1, True)),
               "libre": int(msg.get("libre") in (1, True)), "pos": [round(v, 1) for v in pos]}
        if msg.get("raison") in pilotage.RAISONS:
            out["raison"] = msg["raison"]
        self.sortie.append((pid, out))
        return True

    def _laisse(self, pid: int, e: dict, now: float) -> None:
        """State d'un Hanteur qui possède : sa position et son orientation partent à l'autorité de la zone. Il a
        quitté la salle (ou est en bateau) : fin ; en cinématique : rien (l'autorité relâche faute d'ordres)."""
        ps = self.possessions[pid]
        ps["e_laisse"] = e                         # lot L3 (A6) : un ordre reçu entre deux états repart d'ici
        if e.get("zone") != ps["zone"]:
            self.lacher(pid, "tu as quitté la salle", now)
            return
        if e.get("ship"):
            self.lacher(pid, "bateau", now)
            return
        if e.get("busy") or e.get("pos") is None:
            return
        m = {"type": "hlaisse", "key": ps["key"], "zone": ps["zone"], "owner": pid,
             "pos": [round(v, 1) for v in e["pos"]], "rotY": int(e.get("rot", 0)), "atk": ps["atk"],
             "cible": ps.get("cible"), "ord": ps.get("ord")}
        if ps.get("ord") and ps["ord"][1] == capacites.GESTE:   # réglages de la capacité, lus par le mod (CALL 20)
            m["cap"] = [capacites.masque_coupees(self.cfg), capacites.garde_images(self.cfg)]
        if ps.get("vit") is not None:              # possession complète : l'autorité pilote au lieu de tirer
            m["pil"] = 1
            m["vit"] = [round(v, 1) for v in ps["vit"]]
            if ps.get("haut"):                     # volant : commande de hauteur du pilote
                m["haut"] = round(ps["haut"], 1)
            if getattr(pilotage.espece(ps["proc"]), "essai", False):
                m["essai"] = 1                     # espèce à l'essai permise par le réglage de la partie
        self.sortie.append((("autorite", ps["zone"]), m))

    def prendre_sortie(self) -> list[tuple]:
        out, self.sortie = self.sortie, []
        return out

    def prendre_changes(self) -> list[int]:
        out, self.changes = self.changes, []
        return out

    def resume_lobby(self, now: float | None = None) -> dict:
        v = self.vue_publique(now)
        out = {k: v[k] for k in ("on", "attente", "rotation", "rotation_dans", "points_bareme", "liens_voulus",
                                 "hanteurs_voulus", "victoire", "crawl", "effets")}
        # dépense du clone : réglage qui vaut vraiment (« rien » sur un monde persistant) et s'il est ignoré
        out["potion_depense"] = v["potion_depense"]
        out["depense_ignoree"] = int(bool(self.depense_interdite))
        return out


def resume_nettoye(h):
    """Résumé ``hanteurs`` reçu d'un serveur (lobby), pour l'interface du lanceur : les champs ``victoire``,
    ``effets`` et ``depense_ignoree`` sont nettoyés (types et bornes) avant d'être affichés ; le reste est inchangé.
    Champ absent (ancien serveur) : ``effets`` None (rien d'affiché), ``depense_ignoree`` 0."""
    if not isinstance(h, dict):
        return None
    return dict(h, victoire=victoire.lire(h.get("victoire")) or None,
                effets=lire_effets(h.get("effets"), None) if isinstance(h.get("effets"), str) else None,
                depense_ignoree=1 if h.get("depense_ignoree") in (1, True) else 0)


# Messages « monde » qu'un Hanteur (ou un ancien Hanteur en quarantaine) n'a pas le droit d'envoyer.
MONDE_BLOQUE = frozenset({"keys", "key_get", "key_use", "inv", "cnt", "pickup", "drop", "break", "magma", "boss",
                          "player_hit", "mob", "boom", "tir", "lift", "carry", "stick", "hit", "herbe", "trk",
                          "pnj", "rando_plus"})


def filtre_monde(msg: dict) -> dict | None:
    """Ce qui reste d'un message « monde » d'un Hanteur : les seules demandes d'inscription (recevoir la
    progression, les switches, le registre des coffres et des quarts), jamais un fait. None : ignoré."""
    t = msg.get("type")
    if t == "progress":
        return dict(msg, bits={}) if msg.get("full") in (1, True) else None
    if t == "tsw":
        return {"type": "tsw", "group": msg.get("group"), "hello": 1} if msg.get("hello") else None
    if t in ("coffre", "gift"):
        return {"type": t, "group": msg.get("group"), "full": 1} if msg.get("full") in (1, True) else None
    if t in MONDE_BLOQUE:
        return None
    return msg


def filtre_inscription(msg: dict) -> dict:
    """Ancien Hanteur (relecture adverse) : ses inscriptions ne portent plus l'état de SON jeu, qui contient ce
    qu'il a ramassé ou ouvert en Hanteur (objet d'un coffre, petite clé, rubis). Sans ce filtre, le ``full`` de
    l'inventaire (union), le ``hello`` des clés (base = max tant qu'aucun fait) et le ``hello`` des compteurs (un
    hôte impose ses valeurs) le donnaient aux Link au retour en Link ou à la reconnexion. Le serveur connaît déjà
    tout ce qui est légitime (il a joué en Link connecté avant) : ``inv`` full -> ``sync`` vide (même réponse
    « snap ») ; ``keys`` hello sans valeurs ; ``cnt`` hello sans ``hote`` ni plafonds. Le reste est inchangé."""
    t = msg.get("type")
    if t == "inv" and msg.get("full") in (1, True):
        return {"type": "inv", "group": msg.get("group"), "seq": msg.get("seq", 0), "sync": 1}
    if t == "keys" and msg.get("hello") in (1, True):
        return dict(msg, slots={})
    if t == "cnt" and msg.get("hello") in (1, True):
        return {k: v for k, v in msg.items() if k not in ("hote", "cap")}
    return msg


# =========================================================================================================
# Client : accès au jeu (écritures gardées) et logique du Hanteur
# =========================================================================================================
def _read(mem, addr: int, n: int) -> bytes | None:
    try:
        r = mem.try_read(addr, n)
    except Exception:
        return None
    return r if r is not None and len(r) == n else None


def link_ok(mem, base: int, pid: int) -> bool:
    """L'acteur à ``base`` est toujours le Link lu à l'échantillon : id de process et procName (168, daPy_lk_c)
    relus maintenant. Jamais d'écriture sur Medli ou Makar (getPlayer(0) quand on les contrôle) : +0x3B8 n'y est
    pas mNoResetFlg0."""
    if not base or pid is None:
        return False
    r = _read(mem, base + OFF_PC_ID, 6)
    return r is not None and struct.unpack(">Ih", r) == (pid & 0xFFFFFFFF, PROC_LINK)


def link_cache(mem, base: int) -> bool | None:
    r = _read(mem, base + LINK_FLG0, 1)
    return None if r is None else bool(r[0] & NO_DRAW_BYTE)


def cacher_link(mem, base: int, pid: int, on: bool) -> bool:
    """Pose (ou efface) NO_DRAW sur Link. Un seul octet écrit (celui du bit), relu juste avant, seulement s'il
    faut le changer. Renvoie True si l'octet a été écrit."""
    if not link_ok(mem, base, pid):
        return False
    r = _read(mem, base + LINK_FLG0, 1)
    if r is None:
        return False
    b = r[0]
    nb = (b | NO_DRAW_BYTE) if on else (b & ~NO_DRAW_BYTE & 0xFF)
    if nb == b:
        return False
    mem.write(base + LINK_FLG0, bytes([nb]))
    return True


def teleporter(mem, base: int, pid: int, pos) -> bool:
    """Link (relu) en ``pos`` : vitesses à 0, old.pos puis current.pos (étude §3, S0)."""
    p = _pos3(pos)
    if p is None or not link_ok(mem, base, pid):
        return False
    mem.write(base + OFF_SPEED, struct.pack(">3f", 0.0, 0.0, 0.0))
    mem.write(base + OFF_SPEEDF, struct.pack(">f", 0.0))
    mem.write(base + OFF_OLD_POS, struct.pack(">3f", *p))
    mem.write(base + OFF_CUR_POS, struct.pack(">3f", *p))
    return True


def baisser_vie(mem, v: int) -> bool:
    """Vie de manche (§7.3) : vie courante baissée à ``v`` (jamais vers le haut, jamais sous 1 quart, jamais
    pendant un événement), relue juste avant ; comme ``wwmp_client.write_life``."""
    from jeu import game
    from jeu import progress as prog
    gi = game.gameinfo_ptr(mem)
    if gi is None:
        return False
    ev = _read(mem, prog.SYM_EVT_CTRL + prog.EVT_MODE, 2)
    if ev is None or ev != bytes(2):
        return False
    raw = _read(mem, gi + game.GI_SAVE + game.SV_LIFE, 2)
    if raw is None:
        return False
    cur = struct.unpack(">H", raw)[0]
    v = int(v)
    if not 1 <= v < cur:
        return False
    mem.write(gi + game.GI_SAVE + game.SV_LIFE, struct.pack(">H", v))
    return True


def remonter_vie(mem, cible: int | None = None) -> bool:
    """Vie courante (u16, gameInfo+0x22) remontée à ``cible`` (défaut : vie max), jamais au-dessus de la vie
    max, jamais depuis 0 (game over en cours), jamais pendant un événement. Relue juste avant."""
    from jeu import game
    from jeu import progress as prog
    gi = game.gameinfo_ptr(mem)
    if gi is None:
        return False
    ev = _read(mem, prog.SYM_EVT_CTRL + prog.EVT_MODE, 2)
    if ev is None or ev != bytes(2):
        return False
    raw = _read(mem, gi + game.GI_SAVE + game.SV_MAX_LIFE, 4)
    if raw is None:
        return False
    vmax, cur = struct.unpack(">HH", raw)
    v = vmax if cible is None else min(vmax, int(cible))
    if not 0 < cur < v or vmax > 80:
        return False
    mem.write(gi + game.GI_SAVE + game.SV_LIFE, struct.pack(">H", v))
    return True


# Touches (clavier, lues quand la fenêtre de Cemu est au premier plan ; rien n'est envoyé au jeu). Phase 1 : F9 se
# manifester / redevenir spectateur, F4 posséder / relâcher (F10 ouvre le menu de la fenêtre, F11 et F12 sont pris
# par Cemu : plein écran, capture). Phase 2 : F7 pendant une possession = viser le Link suivant ; F4 pendant la
# potion = l'arrêter ; Link possédé : F4 = résister. Phase 3 (synchro/ordres.py) : F3 pendant une possession = « garde »
# (les coups d'épée du Link caché donnent déjà « attaque » et « lourde »).
# Possession complète, étape 2 : PagePréc / PageSuiv = monter / descendre (volant piloté : Peahat, Keese, Kargaroc).
VK = {"F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78,
      "PGDN": 0x22, "PGUP": 0x21, "FIN": 0x23}
TOUCHES = {"F2": "capacite", "F3": "garde", "F4": "posseder", "F5": "suivre", "F6": "invoquer",
           "F7": "espece", "F8": "suivi", "F9": "manifester", "PGUP": "monter", "PGDN": "descendre",
           "FIN": "enfermer"}                 # Fin : enfermement (synchro/enfermement.py)
# Portage natif (audit du 2026-11-16) : F2-F6 SEULES y chargent un état de sauvegarde (raccourcis par défaut du
# portage, runtime/src/input_map.cpp kShortcuts : Load1 F6, Load2 F2, Load3 F3, Load4 F4, Load5 F5 ; Maj+F1-F5
# l'enregistrent), même en pleine partie (overlay.cpp activate_shortcut -> ss::request_load). Sur le natif, ces
# touches ne comptent donc qu'avec Ctrl (Ctrl+F3…F6 : aucun raccourci du portage, qui exige les mêmes modificateurs ;
# Ctrl n'est lié à aucun bouton par défaut). F7-F9 et PagePréc / PageSuiv restent seules.
CONFLITS_NATIF = frozenset({"F2", "F3", "F4", "F5", "F6"})
VK_CTRL = 0x11
RAISON_NATIF = ("portage natif : Ctrl+F2, Ctrl+F3, Ctrl+F4, Ctrl+F5, Ctrl+F6 (F2-F6 seules y chargent un état de "
                "sauvegarde du portage) ; F7, F8, F9, PagePréc et PageSuiv inchangées")


def nom_touche(k: str, natif: bool = False) -> str:
    """Nom affiché d'une touche du mode Hanteurs (« Ctrl+F4 » sur le portage natif pour F3-F6)."""
    return f"Ctrl+{k}" if natif and k in CONFLITS_NATIF else k


# Possession complète : pourquoi le monstre possédé n'est pas piloté (``hpilote.raison``, wwmp/pilotage.RAISONS)
RAISONS_PILOTAGE = {
    "pack": "le pack du joueur qui fait autorité dans la salle n'est pas à jour (WWMP_Mailbox v19 avec le pilotage)",
    "espece": "cette espèce n'est pas pilotable (elle suit en laisse)",
    "option": "le pilotage est coupé (--no-pilotage) chez toi ou chez le joueur qui fait autorité dans la salle",
    "evenement": "une cinématique est en cours chez le joueur qui fait autorité dans la salle",
    "essai": "cette espèce est à l'essai : son pilotage demande le réglage « espèces à l'essai » de la partie",
}


class Touches:
    """Commandes du mode Hanteurs au clavier (fronts montants, seulement si la fenêtre au premier plan appartient au
    processus ``pid`` du jeu) et à la manette (mod natif, zone WWMPMAN1 : ``lire_manette``). Touches et
    combinaisons : table de ``commun/commandes.py``, réglée dans l'onglet Commandes du lanceur et reçue à chaud
    (``regler``) ; sans table, ses défauts (Cemu : F3-F9, PagePréc / PageSuiv ; natif : Ctrl+F3-F6). ``lire(vk) ->
    bool`` et ``premier_plan() -> pid`` injectables (tests)."""

    def __init__(self, pid: int | None, lire=None, premier_plan=None, natif: bool = False, table=None):
        self.pid = pid
        self.natif = bool(natif)         # portage natif : F3-F6 seulement avec Ctrl (CONFLITS_NATIF) par défaut
        self.lire, self.premier_plan = lire, premier_plan
        self.raison = ""                 # Linux : pourquoi les touches sont inactives (Wayland, python-xlib absent)
        vk_de = None
        if (lire is None or premier_plan is None) and sys.platform != "win32":
            # Linux : X11 / XWayland par python-xlib, sinon rien (boutons du lanceur) : synchro/touches_linux.py
            try:
                from synchro import touches_linux
                l2, p2, self.raison = touches_linux.creer()
            except Exception as e:       # noqa: BLE001 : jamais d'arrêt du client pour des touches
                l2, p2, self.raison = (lambda vk: False), (lambda: None), f"touches inactives ({e})"
            self.lire = self.lire or l2
            self.premier_plan = self.premier_plan or p2
            vk_de = (lambda nom: cmdes.TOUCHES[nom][0] if nom in cmdes.TOUCHES else None)
        elif lire is None or premier_plan is None:
            try:
                import ctypes
                u32 = ctypes.windll.user32
                if lire is None:
                    self.lire = lambda vk: bool(u32.GetAsyncKeyState(vk) & 0x8000)
                if premier_plan is None:
                    def fp():
                        h = u32.GetForegroundWindow()
                        out = ctypes.c_ulong(0)
                        u32.GetWindowThreadProcessId(h, ctypes.byref(out))
                        return out.value
                    self.premier_plan = fp
            except (AttributeError, OSError):
                self.lire = self.lire or (lambda vk: False)
                self.premier_plan = self.premier_plan or (lambda: None)
        elif lire is not None:
            vk_de = (lambda nom: cmdes.TOUCHES[nom][0] if nom in cmdes.TOUCHES else None)   # essais : codes fixes
        self.lecteur = cmdes.Lecteur(self.lire, cmdes.du_groupe(table or cmdes.table_defaut(self.natif), "hanteur"),
                                     vk_de)
        # manette (mod natif) : combinaisons écrites au mod, commande de chacune, derniers compteurs de fronts
        self.masques_ecrits: tuple | None = None
        self.ids_manette: list[str] = []
        self.fronts: list[int] | None = None

    @property
    def table(self) -> dict:
        return self.lecteur.table

    def regler(self, table: dict | None) -> None:
        """Nouvelle table (lanceur, à chaud) ; None : les défauts."""
        self.lecteur.regler(cmdes.du_groupe(table or cmdes.table_defaut(self.natif), "hanteur"))
        self.masques_ecrits = None       # combinaisons réécrites au prochain ``lire_manette``

    def lire_commandes(self) -> list[str]:
        try:
            actif = self.pid is not None and self.premier_plan() == self.pid
        except Exception:                # noqa: BLE001
            actif = False
        return self.lecteur.lire_commandes(actif)

    def lire_manette(self, mb, actif: bool = True) -> list[str]:
        """Combinaisons de manette (mod natif 0.37.0, zone WWMPMAN1) : écrit au mod les masques de la table quand
        ``actif`` (Hanteur, ou Link possédé), des zéros sinon (le jeu reçoit alors tout) ; rend les commandes dont
        la combinaison a été COMPLÉTÉE depuis le dernier appel (compteurs de fronts tenus par le mod : aucun appui
        perdu entre deux tours). Sans la zone (Cemu, mod plus ancien) : rien."""
        if mb is None or not getattr(mb, "manette_ok", False):
            return []
        combos = []
        if actif and getattr(self, "menu_ouvert", False):
            # menu d'invocation ouvert (synchro/menu_invocation.py) : Select, L, R, A, B seulement
            for k, ma in menu_invocation.TOUCHES_MENU:
                combos.append((k, cmdes.compact(cmdes.bits_manette(ma))))
        elif actif:
            for k, (_cl, ma) in self.lecteur.table.items():
                m = cmdes.compact(cmdes.bits_manette(ma))
                if m and len(combos) < cmdes.N_COMBOS:
                    combos.append((k, m))
        masques = tuple(m for _k, m in combos)
        if masques != self.masques_ecrits:
            mb.manette_masques(list(masques))
            self.masques_ecrits = masques
            self.ids_manette = [k for k, _m in combos]
            self.fronts = None
        if not combos:
            return []
        fronts = mb.manette_fronts()
        avant, self.fronts = self.fronts, fronts
        if avant is None:
            return []
        return [self.ids_manette[i] for i in range(len(self.ids_manette)) if (fronts[i] - avant[i]) & 0xFF]


def _boite(c):
    """Boîte aux lettres du client ``c`` (None : pas de mod, lecture seule)."""
    get = getattr(getattr(c, "sampler", None), "_mailbox", None)
    return get() if callable(get) else None


def _proc_cle(key) -> int | None:
    """Numéro de proc d'une clé d'ennemi (``proc/PARAMS/x,y,z``), None sinon."""
    k = parse_key(key) if isinstance(key, str) else None
    return k[0] if k else None


class HanteurSync:
    """Côté client (fil de travail pour ``tick``, boucle réseau pour ``receive`` / ``take_out``)."""

    def __init__(self, enabled: bool = False, cacher: bool = True, teleport: bool = True, clock=time.monotonic):
        self.enabled = bool(enabled)
        self.cacher, self.teleport = cacher, teleport
        self.clock = clock
        self._lock = threading.Lock()
        self._nlock = threading.Lock()      # notes (écrites des deux fils)
        self.on = False                     # le serveur est en mode Hanteurs
        self.attente = True
        self.roles: dict[int, str] = {}
        self.noms: dict[int, str] = {}
        self.points = 0
        self.points_tous: dict[int, int] = {}
        self.role = ROLE_LINK
        self.role_applique = ROLE_LINK      # rôle pour lequel le jeu a été réglé (tick)
        self.fin_quarantaine = 0.0
        self.especes: dict[int, dict] = {}
        self.vues: dict[str, list[int]] = {}
        self.recharges: dict[int, float] = {}
        self.rotation, self.rotation_dans = "aucune", None
        self.victoire: dict = {}            # conditions de victoire (état public du serveur, synchro/victoire.py)
        self.t_victoire = 0.0
        self.choix = 0                      # indice de l'espèce choisie (F7)
        self.cible: int | None = None       # Link suivi
        # camouflage de la tenue jaune (client/camouflage.py, serveur/camouflage.py) : Link camouflés en ce moment ;
        # un Hanteur ne les voit pas du tout (pas de fantôme) et ne peut pas les suivre en spectateur
        self.camoufles: frozenset = frozenset()
        self.suivi = True                   # suivi continu (F8)
        self.saut_demande = False           # téléportation demandée (F5, bouton)
        self.t_saut = float("-inf")
        self.devient: dict | None = None    # dernier hdevient pas encore appliqué
        self.a_creer: dict[str, dict] = {}  # clé -> {msg, essais, t}
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.touches: Touches | None = None
        self.table_commandes: dict | None = None    # touches et manette réglées dans le lanceur (onglet Commandes)
        self.hist: dict[int, list[tuple[float, float]]] = {}   # joueur -> [(heure, y)]
        self.n_cache = self.n_vie = self.n_sauts = self.n_crees = 0
        self.a_montrer = False              # NO_DRAW posé par nous : à effacer quand on redevient Link
        self.jeu_attente: tuple[dict, float] | None = None   # redevenu Link : vie et position, jeu libre
        self.derniere_note = ""
        self.cmd_n: int | None = None       # dernière commande du lanceur exécutée (None : fichier pas encore lu)
        self.a_ete_hanteur = False          # a été Hanteur : inscriptions sans l'état de son jeu
        # phase 1 (possession par laisse, synchro/laisse.py)
        self.manifeste = False              # manifesté : marche libre (pas de suivi continu), peut posséder
        self.suivi_avant = True             # suivi continu à remettre en redevenant spectateur
        self.possede: dict | None = None    # {key, nom, fin (heure locale)} : possession en cours (serveur)
        self.possession_on = False          # la partie permet la possession
        self.possession_cout = 0
        self.recharge_possession = 0.0      # heure locale de fin de recharge (état du serveur)
        self.coup_avant: tuple[int, int] | None = None   # (mCutType, mCutCount) au tick précédent
        self.t_ordre = float("-inf")
        self.n_ordres = 0
        # lot L3 (A6, docs/etudes/rollback_netcode.md §4) : coup d'épée du Link caché lu aussi à CHAQUE IMAGE par le
        # fil des images (``ordres_image``) ; l'ordre part tout de suite. Tour et fil des images : sous ce verrou
        self._ordres_lock = threading.Lock()
        self.ctx_ordres: tuple | None = None   # (mem, base, pid, stage, heure) posé par le tour, None = rien à lire
        self.n_ordres_image = 0                # ordres détectés par le fil des images (avant le tour)
        self.ordres_image_on = True            # --latence-prudente : non
        # possession complète (synchro/pilotage.py) : le Hanteur pilote le monstre possédé
        self.pilote_on = False              # --share-pilotage (posé par le client)
        self.prediction = True              # --no-pilotage-local : pas de prédiction locale de la marionnette
        self.pilote: dict | None = None     # dernier ``hpilote`` : {key, ok, libre, pos, t, raison}
        self.pil_vit: tuple[float, float] | None = None   # vitesse du Link caché à envoyer (None : pas de pilotage)
        self.pil_prev: tuple | None = None  # (position, heure) du tick précédent
        self.pil_colle = False              # Link caché déjà recollé sur le monstre depuis le début de la possession
        self.pil_libre = False              # le dernier retour disait « geste en cours »
        self.pil_haut = 0.0                 # volant piloté : commande de hauteur (u, PagePréc / PageSuiv)
        self.t_recolle = float("-inf")
        self.n_recollages = 0
        self.pilote_note: str | None = None  # raison du repli déjà annoncée (une note par raison et par possession)
        self.recharges_gestes: dict[str, float] = {}      # geste -> heure locale de fin de recharge (serveur)
        self.durees_gestes: dict[str, float] = {}         # geste -> durée de sa recharge (``hpossede``)
        # décors (client/decors.py) : (genre, pos ou None = notre Link, salle ou None = la nôtre), notés à la réception,
        # joués par l'étape des décors du client (fil de travail)
        self.decors_evts: list = []
        self.recul_potion: dict | None = None   # potion lancée sur nous : {t, par} ; le recul est joué à ``t``
        self.menu = menu_invocation.MenuInvocation()    # menu d'invocation à la manette (mod natif 0.60.0)
        # phase 2 : Link visé par le monstre possédé ; potion de possession d'un Link (synchro/potion.py)
        self.cible_laisse: int | None = None
        self.potion_on = False
        self.potion_cout = 0
        self.potion_duree = 0
        # inventaire et argent du possédé (synchro/potion.py) : réglages du lobby reçus du serveur ; ``depense_on`` :
        # option du client (--share-depense, --no-depense pour couper)
        self.depense_on = True
        self.potion_depense = "rien"
        self.potion_plafond = 0
        self.potion_resister = False
        self.recharge_potion = 0.0          # heure locale de fin de recharge (état du serveur)
        self.possedes: set[int] = set()     # Link possédés en ce moment (leur fantôme est un clone)
        self.clone: dict | None = None      # Hanteur : {cible, nom, fin, t, pos, stage, room, place, exo}
        self.envoute: dict | None = None    # Link possédé : {par, fin, t}
        self.liberation: dict | None = None  # Link libéré : {pos, stage, room, degats, t} à appliquer (jeu libre)
        self.n_resist = self.n_clone_sauts = self.n_suivi_clone = 0
        # Link gelé (synchro/gel.py, pack v19 bit 10) : Link possédé inerte ; Hanteur spectateur collé au Link suivi
        self.monde_envoute = False          # possédé (puis jusqu'à la quarantaine posée par tick) : monde filtré
        # caméra spectateur (gelé et collé au Link suivi) : À COCHER. Collé, le Hanteur est SUR le Link : il ne
        # peut ni invoquer (refus « trop près », DIST_LINK_MIN) ni marcher jusqu'à un monstre à posséder.
        self.colle = False
        self.gel_voulu = False              # ce que tick demande au client (gel_step arme la zone du pack)
        self.n_colle = 0
        # phases 3 et 4 (synchro/ordres.py) : gestes envoyés, influence sur les boss
        self.n_gestes: dict[str, int] = {g: 0 for g in ordres.GESTES}
        self.influence_on = False
        self.influences: list[dict] = []    # [{i, cle, boss, nom, en, cout, recharge}] (état du serveur)
        self.combats: dict[str, list[int]] = {}   # boss -> Link qui le combattent
        self.recharges_infl: dict[str, float] = {}  # clé d'ordre -> heure locale de fin de recharge
        self.n_influences = 0
        # effets de fumée (invocation, possession, relâche, potion, échange) : daDisappear types 1 et 3 par la commande 4
        self.effets_on = False              # --share-effets (posé par le client)
        self.effets_regle = "tous"          # réglage de la partie (serveur) ; ancien serveur : « tous »
        self.fx: list[dict] = []            # effets à jouer : {quoi, pos, stage, room, key?, cible?, t, local?}
        self.fx_t: list[float] = []         # heures des fumées jouées (plafond FX_PAR_S)
        self.n_fx = self.n_fx_jetes = 0
        # effets propres aux Hanteurs joués par le pack (CALL 11, bit 11 ; synchro/effets_pack.py) : COUPÉS par défaut,
        # --effets-pack ; sans l'option, sans le bit ou sur un refus du pack, la fumée ci-dessus
        self.effets_pack_on = False         # --effets-pack (posé par le client)
        self.effets_pack_son = True         # --effets-pack-muet : particules seules
        self.n_fx_pack = self.n_fx_pack_refus = 0
        # enfermement (synchro/enfermement.py) : réglages du serveur (ancien serveur : champs absents = coupé)
        self.enf_on = False
        self.enf_duree = self.enf_rayon = 0
        self.recharge_enf = 0.0             # heure locale de fin de recharge (état du serveur)
        self.enfermements: list[dict] = []  # en cours (état du serveur) : {id, par, zone, reste, liens, monstres}
        self.n_enfermer = 0

    # --- réseau -----------------------------------------------------------------------------------------
    def receive(self, msg: dict, me) -> None:
        if not self.enabled:
            return
        t = msg.get("type")
        now = self.clock()
        with self._lock:
            if t == "hanteurs":
                self.on = True
                self.attente = bool(msg.get("attente"))
                self.roles = {int(k): v for k, v in (msg.get("roles") or {}).items() if v in ROLES}
                self.noms = {int(k): str(v)[:24] for k, v in (msg.get("noms") or {}).items()}
                self.points_tous = {int(k): int(v) for k, v in (msg.get("points") or {}).items()}
                esp = {}
                for k, e in (msg.get("especes") or {}).items():
                    try:
                        esp[int(k)] = {"nom": str(e["nom"])[:24], "cout": int(e["cout"]),
                                       "recharge": float(e["recharge"])}
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                self.especes = esp
                self.vues = {str(s): [int(p) for p in v if isinstance(p, int)]
                             for s, v in (msg.get("vues") or {}).items() if isinstance(v, list)}
                self.rotation, self.rotation_dans = msg.get("rotation"), msg.get("rotation_dans")
                self.victoire, self.t_victoire = victoire.lire(msg.get("victoire")), now
                self.possession_on = msg.get("possession") in (1, True)
                c = msg.get("possession_cout")
                self.possession_cout = c if isinstance(c, int) and not isinstance(c, bool) else 0
                self.potion_on = msg.get("potion") in (1, True)
                for champ in ("potion_cout", "potion_duree", "potion_plafond"):
                    v = msg.get(champ)
                    setattr(self, champ, v if isinstance(v, int) and not isinstance(v, bool) else 0)
                dep = msg.get("potion_depense")      # ancien serveur : champ absent = « rien »
                self.potion_depense = dep if dep in potion.DEPENSES else "rien"
                self.potion_resister = msg.get("potion_resister") in (1, True)
                self.effets_regle = lire_effets(msg.get("effets"))      # ancien serveur : champ absent = « tous »
                # enfermement : ancien serveur, champs absents (coupé)
                self.enf_on = msg.get("enfermement") in (1, True)
                for champ in ("enf_duree", "enf_rayon"):
                    v = msg.get(champ)
                    setattr(self, champ, v if isinstance(v, int) and not isinstance(v, bool) else 0)
                enfs = []
                for d in (msg.get("enfermements") or [])[:16]:
                    try:
                        enfs.append({"id": int(d["id"]), "par": int(d["par"]), "zone": str(d["zone"])[:64],
                                     "reste": float(d["reste"]), "liens": [int(x) for x in d["liens"]][:8],
                                     "monstres": int(d.get("monstres", 0)), "t": now})
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                self.enfermements = enfs
                self.possedes = {int(x) for x in (msg.get("possedes") or []) if str(x).isdigit()}
                # phase 4 : influence sur les boss
                self.influence_on = msg.get("influence") in (1, True)
                infl = []
                for d in (msg.get("influences") or [])[:32]:
                    try:
                        i = int(d["i"])
                        if (0 <= i < ordres.N_ORDRES and ordres.ORDRES[i].cle == d.get("cle")
                                and ordres.ORDRES[i].genre == "boss"):
                            infl.append({"i": i, "cle": d["cle"], "boss": str(d["boss"])[:24], "nom": str(d["nom"])[:60],
                                         "en": str(d.get("en") or d["nom"])[:60], "cout": int(d["cout"]),
                                         "recharge": float(d["recharge"])})
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue
                self.influences = infl
                self.combats = {str(b): [int(p) for p in v if str(p).isdigit()]
                                for b, v in (msg.get("combats") or {}).items() if isinstance(v, list)}
                moi = msg.get("moi") or {}
                self.recharges_infl = {}
                for k, v in (moi.get("recharges_infl") or {}).items():
                    try:
                        self.recharges_infl[str(k)] = now + float(v)
                    except (TypeError, ValueError, OverflowError):
                        continue
                if moi.get("role") in ROLES and moi["role"] != self.role:
                    self._nouveau_role(moi["role"], {"raison": "état du serveur"}, now)
                c = moi.get("cible_laisse")
                self.cible_laisse = c if isinstance(c, int) and not isinstance(c, bool) else None
                ren = moi.get("recharge_enf")
                self.recharge_enf = now + (float(ren) if pilotage._nombre(ren) else 0.0)
                rpo = moi.get("recharge_potion")
                self.recharge_potion = now + (float(rpo) if pilotage._nombre(rpo)
                                              else 0.0)
                po = moi.get("potion")
                if isinstance(po, dict) and self.clone is not None and isinstance(po.get("exo"), int):
                    self.clone["exo"] = po["exo"]
                if po is None and self.clone is not None and now - self.clone["t"] > 2.0:
                    self.clone = None                # fin manquée : l'état du serveur fait foi
                if moi.get("possede_par") is None and self.envoute is not None and now - self.envoute["t"] > 2.0:
                    self.envoute = None              # fin manquée (hlibere perdu) : Link montré au prochain tick
                self.points = int(moi.get("points") or 0)
                rp = moi.get("recharge_possession")
                self.recharge_possession = now + (float(rp) if pilotage._nombre(rp)
                                                  else 0.0)
                if moi.get("possede") is None and self.possede is not None and now - self.possede.get("t", now) > 2.0:
                    self.possede = None             # fin manquée (hlache perdu) : l'état du serveur fait foi
                rg = moi.get("recharges_gestes")
                if isinstance(rg, dict):            # recharge des gestes : l'état du serveur fait foi
                    self.recharges_gestes = self._gestes(rg, now)
                self.recharges = {}
                for k, v in (moi.get("recharges") or {}).items():
                    try:
                        self.recharges[int(k)] = now + float(v)
                    except (TypeError, ValueError, OverflowError):
                        continue
            elif t == "hdevient" and msg.get("role") in ROLES:
                self.on = True
                self._nouveau_role(msg["role"], msg, now)
            elif t == "hspawn":
                key = msg.get("key")
                if _pos3(msg.get("pos")) is not None and isinstance(msg.get("room"), int):   # décors : la volute
                    self.decors_evts = (self.decors_evts + [("invocation", list(_pos3(msg["pos"])), msg["room"])])[-8:]
                if isinstance(key, str) and key not in self.a_creer and len(self.a_creer) < 64 \
                        and _pos3(msg.get("pos")) is not None and isinstance(msg.get("proc"), int) \
                        and isinstance(msg.get("params"), int) and isinstance(msg.get("room"), int):
                    self.a_creer[key] = {"msg": msg, "essais": 0, "t": float("-inf"), "recu": now,
                                         "recu_mur": time.time()}
            elif t == "hfx":
                self._recevoir_fx(msg, now)
            elif t == "henf" and self.role == ROLE_HANTEUR and isinstance(msg.get("liens"), list):
                # enfermement (synchro/enfermement.py) : le Link enfermé est réglé par EnfermementSync ; ici les notes
                noms = ", ".join(self.noms.get(x, "?") for x in msg["liens"][:8] if isinstance(x, int))
                qui = "tu enfermes" if msg.get("par") == me else f"{self.noms.get(msg.get('par'), 'un Hanteur')} enferme"
                self._note(f"{qui} {noms or 'personne'} : invoque tes monstres dans le cercle !")
            elif t == "henf_fin" and self.role == ROLE_HANTEUR:
                self.enfermements = [d for d in self.enfermements if d["id"] != msg.get("id")]
                self._note(f"enfermement fini ({str(msg.get('raison') or '?')[:80]})")
            elif t == "hrefus" and msg.get("quoi") == "geste":
                # geste en recharge (serveur) : message clair, et l'heure de fin recalée
                self.recharges_gestes.update(self._gestes({msg.get("geste"): msg.get("reste")}, now))
                self._note(f"geste refusé : {str(msg.get('raison'))[:120]}")
            elif t == "hrecharge":
                if self.possede is not None and msg.get("key") in (None, self.possede["key"]) \
                        and isinstance(msg.get("gestes"), dict):
                    self.recharges_gestes = self._gestes(msg["gestes"], now)
            elif t == "hpilote":
                self._recevoir_pilote(msg, now)
            elif t == "hrefus":
                quoi = msg.get("quoi") if msg.get("quoi") in ("possession", "potion", "influence",
                                                              "enfermement") else "invocation"
                self._note(f"{quoi} refusée : {str(msg.get('raison'))[:120]}")
            elif t == "hpossede" and isinstance(msg.get("key"), str):
                k = parse_key(msg["key"])              # décors : la volute descend sur le monstre possédé
                if k is not None:
                    self.decors_evts = (self.decors_evts + [("possession", [float(v) for v in k[2]], None)])[-8:]
                duree = msg.get("duree")
                duree = float(duree) if pilotage._nombre(duree) else 0.0
                pil = bool(msg.get("pilotage") in (1, True) and self.pilote_on)
                self.possede = {"key": msg["key"], "nom": str(msg.get("nom") or "monstre")[:24],
                                "fin": now + max(0.0, min(600.0, duree)), "t": now, "pilotage": pil}
                self.coup_avant = None
                self.manifeste = True
                self._fin_pilotage()
                self.durees_gestes = self._gestes(msg.get("recharges"), 0.0)
                cap = msg.get("capacite")
                if isinstance(cap, dict) and isinstance(cap.get("nom"), str):
                    self.possede["capacite"] = {"nom": cap["nom"][:24], "en": str(cap.get("en") or "")[:24],
                                                "aide": str(cap.get("aide") or "")[:160],
                                                "genre": cap.get("genre") if cap.get("genre") in ("jeu", "frenesie")
                                                else "jeu"}
                    self.durees_gestes.update(self._gestes({capacites.GESTE: cap.get("recharge")}, 0.0))
                autre = pilotage.AUTRES.get(_proc_cle(msg["key"]))
                if pil and autre is not None and autre.forme == "cible":
                    quoi = "il vise l'endroit où TU te tiens : place-toi sur ta cible"
                    if ordres.gestes_espece(_proc_cle(msg["key"]), True):
                        quoi += ", coup d'épée = attaque"
                    self._note(f"tu possèdes un {self.possede['nom']} : TU LE DIRIGES ({quoi}) ; F4 pour le relâcher")
                elif pil and autre is not None:
                    quoi = "stick = il se déplace"
                    if autre.vol:
                        quoi += ", PagePréc / PageSuiv = monter / descendre"
                    if ordres.gestes_espece(_proc_cle(msg["key"]), True) \
                            or pilotage.gestes_appat(_proc_cle(msg["key"])):
                        quoi += ", coup d'épée = attaque"
                    self._note(f"tu possèdes un {self.possede['nom']} : TU LE PILOTES ({quoi}) ; F4 pour le relâcher")
                elif pil:
                    self._note(f"tu possèdes un {self.possede['nom']} : TU LE PILOTES (stick = il marche et court, "
                               "coup d'épée = attaque, coup chargé ou sauté = attaque lourde, F3 = garde) ; F4 pour "
                               "le relâcher")
                else:
                    self._note(f"tu possèdes un {self.possede['nom']} : marche, il te suit ; coup d'épée = élan vers "
                               "le Link le plus proche ; F4 pour le relâcher")
            elif t == "hlache":
                if self.possede is not None and msg.get("key") in (None, self.possede["key"]):
                    self.possede = None
                    self._fin_pilotage()            # fin de possession : recharges des gestes remises à zéro
                    self._note(f"possession terminée ({str(msg.get('raison') or '?')[:80]})")
            elif t == "hnote":
                self._note(str(msg.get("msg"))[:160])
            elif t == "hfin":
                self._note(victoire.annonce(msg))
            elif t == "hpotion_debut" and isinstance(msg.get("cible"), int) and self.role == ROLE_HANTEUR:
                duree = msg.get("duree")
                duree = float(duree) if pilotage._nombre(duree) else 0.0
                self.clone = {"cible": msg["cible"], "nom": str(msg.get("nom") or "?")[:24],
                              "fin": now + max(0.0, min(120.0, duree)), "t": now, "pos": _pos3(msg.get("pos")),
                              "stage": msg.get("stage"), "room": msg.get("room"), "place": False, "exo": 0,
                              "depense": msg.get("depense") if msg.get("depense") in potion.DEPENSES else "rien"}
                self.possede, self.manifeste = None, False
                self._note(f"potion bue : tu joues {self.clone['nom']} ! Les Link te voient comme lui ; frappe-les "
                           "à l'épée ; F4 pour arrêter")
                if self.clone["depense"] != "rien" and self.depense_on:
                    self._note(f"tu as les rubis, les flèches et les bombes de {self.clone['nom']} : ce que tu "
                               "dépenses est perdu pour son équipe"
                               + (f" (au plus {self.potion_plafond} rubis)"
                                  if self.clone["depense"] == "plafond" and self.potion_plafond else ""))
            elif t == "hpotion_fin":
                if self.clone is not None:
                    perdu = potion.texte_depense(msg.get("depense"))
                    self._note(f"potion terminée ({str(msg.get('raison') or '?')[:80]}) : tu redeviens invisible"
                               + (f" ; tu lui as fait perdre {perdu}" if perdu else ""))
                self.clone = None
            elif t == "hpotion_lancer":
                # potion lancée (synchro/potion.py VOL) : la volute vole du Hanteur au Link visé, chez tous ceux de
                # la salle ; chez le Link visé, il recule quand elle le touche (``recul`` : joué par client/principal)
                de, vers = _pos3(msg.get("de")), _pos3(msg.get("vers"))
                room, cible = msg.get("room"), msg.get("cible")
                vol = msg.get("frappe")
                if de is not None and vers is not None and isinstance(room, int) and not isinstance(room, bool)                         and isinstance(cible, int) and not isinstance(cible, bool) and pilotage._nombre(vol)                         and 0.1 <= vol <= 5.0:
                    self.decors_evts = (self.decors_evts + [("lancer", (list(de), list(vers), cible, float(vol)),
                                                             room)])[-8:]
                    if cible == me and self.role == ROLE_LINK:
                        self.recul_potion = {"t": now + float(vol), "par": msg.get("par")}
                        self._note("un Hanteur te lance la potion de possession !")
            elif t == "hpossede_link" and isinstance(msg.get("par"), int) and self.role == ROLE_LINK:
                self.decors_evts = (self.decors_evts + [("possession", None, None)])[-8:]   # sur nous-mêmes
                duree = msg.get("duree")
                duree = float(duree) if pilotage._nombre(duree) else 0.0
                # ``resister`` absent (ancien serveur) : la touche « Résister » existe ; 0 : durée fixe, on regarde
                self.envoute = {"par": msg["par"], "fin": now + max(0.0, min(120.0, duree)), "t": now,
                                "resister": msg.get("resister", 1) in (1, True),
                                "depense": msg.get("depense") if msg.get("depense") in potion.DEPENSES else "rien"}
                self.liberation = None
                # possédé : plus rien du monde ne part (son Link caché n'agit pas pour les autres), et ses
                # inscriptions ne porteront plus l'état de son jeu (comme un ancien Hanteur)
                self.monde_envoute = True
                self.a_ete_hanteur = True
                self._note("un Hanteur a bu la potion : il joue TON Link ! Tu le vois comme un fantôme ; "
                           + ("appuie sur F4 (ou « Résister ») pour le chasser plus vite"
                              if self.envoute["resister"] else
                              f"tu regardes jusqu'à la fin de la potion ({int(round(duree))} s)"))
                if self.envoute["depense"] != "rien":
                    self._note("il a tes rubis, tes flèches et tes bombes (ceux de l'équipe) : ce qu'il dépense "
                               "est perdu")
            elif t == "hlibere":
                if self.envoute is not None or msg.get("pos") is not None:
                    d = msg.get("degats")
                    self.liberation = {"pos": _pos3(msg.get("pos")), "stage": msg.get("stage"),
                                       "room": msg.get("room"), "t": now,
                                       "degats": d if isinstance(d, int) and not isinstance(d, bool) else 0}
                self.envoute = None
                perdu = potion.texte_depense(msg.get("depense"))
                self._note(f"libéré ({str(msg.get('raison') or '?')[:80]})"
                           + (f" ; le Hanteur a dépensé {perdu}" if perdu else ""))

    def _nouveau_role(self, role: str, msg: dict, now: float) -> None:
        if role == self.role and msg.get("pos") is None:
            return
        ancien, self.role = self.role, role
        if role != ROLE_HANTEUR:
            self.possede, self.manifeste = None, False
            self.clone = None
        if role != ROLE_LINK:
            self.envoute, self.liberation = None, None
        self.devient = dict(msg, role=role)
        if role == ROLE_HANTEUR:
            self.a_ete_hanteur = True       # pour toute la session (reconnexions comprises) : filtre_inscription
        if role == ROLE_LINK and ancien == ROLE_HANTEUR:
            self.fin_quarantaine = now + QUARANTAINE_CLIENT
        if role != ancien:
            self._note("tu es maintenant " + ("un HANTEUR (invisible pour les Link)" if role == ROLE_HANTEUR
                                              else "un LINK") + f" ({msg.get('raison', '?')})")

    def reset_network(self) -> None:
        """Connexion perdue : le mode s'arrête chez nous (Link visible au prochain tick)."""
        with self._lock:
            self.on = False
            self.roles.clear()
            if self.role == ROLE_HANTEUR:
                self.devient = {"role": ROLE_LINK, "raison": "connexion perdue"}
            self.role = ROLE_LINK
            self.a_creer.clear()
            self.out.clear()
            self.possede, self.manifeste = None, False
            self.clone, self.envoute, self.liberation = None, None, None   # Link montré au prochain tick
            self.possedes = set()
            self.monde_envoute = False
            self.fx.clear()

    # --- effets de fumée --------------------------------------------------------------------------------
    def _effets_permis(self) -> bool:
        """Ce PC joue-t-il les fumées ? Option du client, puis réglage de la partie : « coupes » personne,
        « hanteurs » seulement un Hanteur."""
        if not self.effets_on or self.effets_regle == "coupes":
            return False
        return self.effets_regle != "hanteurs" or self.role == ROLE_HANTEUR

    def _recevoir_fx(self, msg: dict, now: float) -> None:
        """``hfx`` du serveur (sous verrou) : rangé dans la file s'il est bien formé ; joué par ``tick``. Type et
        taille de la fumée ne sont PAS dans le message (table EFFETS_FUMEE) ; la position est revalidée au moment
        de jouer."""
        if not self.on or not self._effets_permis():
            return
        quoi, stage, room = msg.get("quoi"), msg.get("stage"), msg.get("room")
        pos = _pos3(msg.get("pos"))
        if quoi not in EFFETS_QUOI or pos is None or not isinstance(stage, str) or not 0 < len(stage) <= 8 \
                or isinstance(room, bool) or not isinstance(room, int) or not 0 <= room < 64:
            self.n_fx_jetes += 1
            return
        f = {"quoi": quoi, "pos": pos, "stage": stage, "room": room, "t": now}
        if quoi in ("possede", "lache"):
            key = msg.get("key")
            if not isinstance(key, str) or len(key) > 64 or parse_key(key) is None:
                self.n_fx_jetes += 1
                return
            f["key"] = key
        else:
            cible = msg.get("cible")
            if isinstance(cible, bool) or not isinstance(cible, int):
                self.n_fx_jetes += 1
                return
            f["cible"] = cible
        self._fx_ajouter(f)

    def _fx_ajouter(self, f: dict) -> None:
        self.fx.append(f)
        if len(self.fx) > FX_FILE:
            self.n_fx_jetes += len(self.fx) - FX_FILE
            del self.fx[:len(self.fx) - FX_FILE]

    def _fx_position(self, f: dict, c, sm, st, others):
        """Où jouer cet effet CHEZ NOUS, ou None (rien à montrer ici). Monstre : la position de NOTRE acteur
        (absent chez nous : rien). Link possédé : nous-mêmes, ou son fantôme dans notre salle ; sinon, et pour la
        libération (l'endroit du retour), la position reçue. Invocation : là où nous venons de créer le monstre.
        Échange de Crawl : la position reçue (relevée par le serveur à la chute ; jamais chez le Link tombé).
        Apparition du nouveau Link : nous-mêmes si c'est nous, sinon la position reçue (son state qui a prouvé
        l'apparition). Dans tous les cas, rien à plus de FX_LINK_MAX de notre Link."""
        moi = _pos3(st.get("pos"))
        if f.get("local"):
            p = _pos3(f.get("pos"))
        elif "key" in f:
            lire = getattr(sm, "enemy_pos", None)
            p = _pos3(lire(f["key"])) if lire is not None else None
        else:
            p, cible = None, f.get("cible")
            if f["quoi"] == "echange" and cible == getattr(c, "me", None):
                return None                     # notre propre chute : jamais chez nous (le serveur ne l'envoie pas)
            if f["quoi"] == "apparition" and cible == getattr(c, "me", None):
                p = moi                         # nous venons d'apparaître : notre position, relue chez nous
            elif f["quoi"] == "potion":
                if cible == getattr(c, "me", None):
                    p = moi
                else:
                    o = others.get(cible) if isinstance(others, dict) else None
                    if isinstance(o, dict) and o.get("stage") == st.get("stage") and o.get("room") == st.get("room"):
                        p = _pos3(o.get("pos"))
            if p is None:
                p = f["pos"]
        if p is None or moi is None or math.dist(p, moi) > FX_LINK_MAX:
            return None
        return p

    def _effets(self, c, st, others, busy, now) -> None:
        """Joue au plus UNE fumée par tick. Un effet trop vieux (FX_AGE) est jeté, jamais rejoué en retard ; un
        effet d'une autre salle aussi. Cinématique, salle pas encore stable, boîte aux lettres occupée, plafond
        atteint : gardé pour le tick suivant (il tombera seul par son âge)."""
        if not self.fx:
            return
        with self._lock:
            file, self.fx = self.fx, []
        frais = [f for f in file if 0.0 <= now - f["t"] <= FX_AGE]
        jetes = len(file) - len(frais)
        try:
            sm = c.sampler
            if not frais or not self._effets_permis() or not hasattr(sm, "effet"):
                jetes += len(frais)
                frais = []
                return
            if st is None or busy or not getattr(sm, "can_write", True) or not self._salle_stable(c, sm, st):
                return
            if st.get("life") == 0:
                return                          # notre Link à 0 cœur (mort, Game Over) : rien (tombe par l'âge)
            ici = [f for f in frais if f["stage"] == st.get("stage") and f["room"] == st.get("room")]
            jetes += len(frais) - len(ici)
            frais = ici
            if not frais:
                return
            self.fx_t = [x for x in self.fx_t if 0.0 <= now - x < 1.0]
            if len(self.fx_t) >= FX_PAR_S:
                return
            libre = getattr(sm, "mailbox_idle", None)
            if libre is not None and not libre():
                return
            f = frais.pop(0)
            p = self._fx_position(f, c, sm, st, others)
            if p is None:
                jetes += 1
                return
            type_, echelle = EFFETS_FUMEE[f["quoi"]]
            self.fx_t.append(now)
            if self._effet_pack(sm, f["quoi"], p):
                self.n_fx += 1
            elif sm.effet(p, f["room"], type_, echelle):
                self.n_fx += 1
            else:
                jetes += 1
        except Exception as exc:                     # jamais fatal : au pire l'effet manque
            jetes += len(frais)
            frais = []
            self._note(f"effet ignoré ({type(exc).__name__}: {exc})")
        finally:
            self.n_fx_jetes += jetes
            if frais:
                with self._lock:
                    self.fx[:0] = frais

    def _effet_pack(self, sm, quoi: str, p) -> bool:
        """Effet propre aux Hanteurs par le pack (CALL 11) : seulement avec --effets-pack ET le bit 11 (le
        sampler rend None sans la capacité). Faux : rien n'a été joué, l'appelant joue la fumée (repli silencieux,
        aussi sur un refus du pack, résultat < 0). Les erreurs de la boîte remontent (effet ignoré, comme la fumée)."""
        if not self.effets_pack_on:
            return False
        jouer = getattr(sm, "effet_pack", None)
        r = effets_pack.recette_de(quoi)
        if jouer is None or r is None or not effets_pack.position_ok(p):
            return False
        res = jouer(p, r, son=self.effets_pack_son)
        if res is None:
            return False
        if res < 0:
            self.n_fx_pack_refus += 1
            return False
        self.n_fx_pack += 1
        return True

    @staticmethod
    def _salle_stable(c, sm, st) -> bool:
        """Nous sommes dans cette salle, côté réseau et côté jeu, depuis au moins SALLE_STABLE s."""
        zone = zone_of(st.get("stage"), st.get("room"))
        if getattr(c, "cur_zone", zone) != zone:
            return False
        t = max(getattr(c, "zone_t", 0.0) or 0.0, getattr(sm, "room_t", 0.0) or 0.0)
        return time.time() - t >= SALLE_STABLE

    def _fx_invocation(self, c, sm, job: dict, now: float) -> None:
        """Notre création d'un monstre invoqué vient de réussir : petite fumée à cet endroit. Pas pour un monstre
        déjà là avant nous : ``rappel`` (serveur qui renvoie le ``hspawn`` à qui entre), ``hspawn`` vieux de plus
        de FX_SPAWN_AGE, ou arrivé alors que nous venions d'entrer dans la salle (ancien serveur, sans ``rappel``)."""
        m = job["msg"]
        if job.get("fx"):                    # création retentée (monstre pas encore vu ici) : jamais deux fumées
            return
        job["fx"] = True
        if not self._effets_permis() or m.get("rappel") or now - job["recu"] > FX_SPAWN_AGE:
            return
        entree = max(getattr(c, "zone_t", 0.0) or 0.0, getattr(sm, "room_t", 0.0) or 0.0)
        if job.get("recu_mur", 0.0) - entree < SALLE_STABLE:
            return
        with self._lock:
            self._fx_ajouter({"quoi": "invoque", "pos": _pos3(m.get("pos")), "stage": m.get("stage"),
                              "room": m.get("room"), "t": now, "local": True})

    def take_out(self) -> list[dict]:
        with self._lock:
            out, self.out = self.out, []
        return out

    def take_notes(self) -> list[str]:
        with self._nlock:
            out, self.notes = self.notes, []
        return out

    def _note(self, s: str) -> None:
        self.derniere_note = s
        with self._nlock:
            if len(self.notes) < 32:
                self.notes.append("Hanteurs : " + s)

    # --- possession complète (synchro/pilotage.py) -------------------------------------------------------
    @staticmethod
    def _gestes(d, now: float) -> dict[str, float]:
        """{geste: secondes} reçu du serveur -> {geste: ``now`` + secondes} (gestes connus, durées bornées)."""
        out = {}
        if isinstance(d, dict):
            for g, v in d.items():
                if (g in ordres.GESTES or g == capacites.GESTE) and pilotage._nombre(v) and v > 0:
                    out[g] = now + min(600.0, float(v))
        return out

    def _fin_pilotage(self) -> None:
        self.pilote = None
        self.pil_vit = self.pil_prev = None
        self.pil_colle = self.pil_libre = False
        self.pil_haut = 0.0
        self.pilote_note = None
        self.recharges_gestes = {}
        self.durees_gestes = {}

    def _recevoir_pilote(self, msg: dict, now: float) -> None:
        """``hpilote`` (autorité de la zone, relayé par le serveur) : le monstre est-il piloté là-bas, où est-il,
        un geste se joue-t-il. Un repli sur la laisse est annoncé une fois, avec sa raison."""
        pos = _pos3(msg.get("pos"))
        if self.possede is None or msg.get("key") != self.possede["key"] or pos is None \
                or not all(math.isfinite(v) and abs(v) < 1e7 for v in pos):
            return
        ok = msg.get("ok") in (1, True)
        raison = msg.get("raison") if msg.get("raison") in pilotage.RAISONS else None
        self.pilote = {"key": msg["key"], "ok": ok, "libre": msg.get("libre") in (1, True), "pos": pos, "t": now,
                       "raison": raison}
        if not ok and raison != self.pilote_note:
            self.pilote_note = raison
            self._note("pilotage impossible pour l'instant, le monstre te suit en laisse : "
                       + RAISONS_PILOTAGE.get(raison, "raison inconnue"))
        elif ok and self.pilote_note is not None:
            self.pilote_note = None
            self._note("tu pilotes de nouveau le monstre")

    def champ_pil(self) -> list[float] | None:
        """Champ ``pil`` du ``state`` (boucle réseau) : vitesse horizontale du Link caché, ou None (pas de
        pilotage : pas Hanteur, pas de possession pilotable, option coupée)."""
        v = self.pil_vit
        if v is None or self.possede is None or not self.possede.get("pilotage"):
            return None
        if self.pil_haut:                            # volant : commande de hauteur (troisième valeur)
            return [round(v[0], 1), round(v[1], 1), round(self.pil_haut, 1)]
        return [round(v[0], 1), round(v[1], 1)]

    def champ_hv(self, now: float | None = None) -> int | None:
        """Champ ``hv`` du ``state`` (boucle réseau) : 1 quand les autres Hanteurs doivent nous voir, sinon None
        (champ absent). Vu : Hanteur MANIFESTÉ, libre ou avec un monstre en laisse (il marche à côté). Pas vu :
        spectateur, clone d'une potion (relayé sous l'identité du Link possédé), possession PILOTÉE (on voit le
        monstre, pas un Link dedans) ; si l'autorité annonce le repli sur la laisse, on est de nouveau vu."""
        if not self.hanteur() or not self.manifeste or self.clone is not None:
            return None
        po = self.possede
        if po is not None and po.get("pilotage"):
            vp = self._vue_pilotage(self.clock() if now is None else now)
            if vp is None or vp.get("etat") != "laisse":
                return None
        return 1

    def _vol(self) -> float:
        """Hauteur de vol (u) du monstre piloté au-dessus de notre Link caché ; 0 : marcheur, ou pas de pilotage."""
        po = self.possede
        if po is None or not po.get("pilotage"):
            return 0.0
        return pilotage.hauteur_vol(pilotage.espece(_proc_cle(po.get("key"))), self.pil_haut)

    def _hauteur(self, sens: int) -> None:
        """PagePréc / PageSuiv : le volant piloté monte ou descend d'un cran (bornes de l'espèce)."""
        po = self.possede
        esp = pilotage.espece(_proc_cle(po.get("key"))) if po is not None and po.get("pilotage") else None
        if esp is None or not pilotage.hauteur_vol(esp):
            if po is not None:
                self._note("monter / descendre : seulement pour un monstre volant que tu pilotes")
            return
        h = self.pil_haut + sens * pilotage.HAUT_PAS
        self.pil_haut = max(esp.vol_min - esp.vol, min(esp.vol_max - esp.vol, h))
        self._note(f"hauteur de vol : {pilotage.hauteur_vol(esp, self.pil_haut):.0f} u au-dessus de toi")

    def _pilotage(self, c, st, mem, base, pid, can, busy, now) -> None:
        """Chaque tick : vitesse du Link caché (pour le ``state``), recollage du Link caché sur le monstre (début
        du pilotage, fin d'un geste, écart trop grand) et prédiction locale de NOTRE marionnette du monstre
        (``EnemyPuppets.pilote_local``). Tout s'arrête dès que la possession, l'option ou le retour de l'autorité
        manque."""
        pp = getattr(getattr(c, "sampler", None), "puppets", None)
        pos = _pos3(st.get("pos")) if st else None
        po = self.possede
        actif = (self.pilote_on and self.role == ROLE_HANTEUR and self.clone is None and po is not None
                 and po.get("pilotage") and pos is not None and not busy and not a_bord(st))
        if not actif:
            self.pil_vit = self.pil_prev = None
            if pp is not None and getattr(pp, "pilote_local", None) is not None:
                pp.pilote_local = None
            return
        prev, self.pil_prev = self.pil_prev, (pos, now)
        self.pil_vit = pilotage.mesurer(prev[0], pos, now - prev[1]) if prev is not None else (0.0, 0.0)
        r = self.pilote
        frais = r is not None and r["key"] == po["key"] and now - r["t"] <= pilotage.RETOUR_MAX
        if frais and r["ok"] and math.dist(pos, r["pos"]) > pilotage.RECOLLAGE_MAX:
            frais = False                           # retour fou (autre bout de la carte) : jamais de téléport là-bas
        proc = _proc_cle(po.get("key"))
        dr = pilotage.drapeaux(proc)
        if pilotage.forme_cible(proc):
            # forme « cible » : le monstre n'est jamais replacé, il vise le point où nous sommes. Notre Link caché
            # reste libre : aucun recollage sur lui, aucune prédiction (sa marionnette suit le relais ordinaire).
            if frais and r["ok"]:
                self.pil_libre = r["libre"]
            if pp is not None and getattr(pp, "pilote_local", None) is not None:
                pp.pilote_local = None
            return
        if frais and r["ok"]:
            fin_geste = self.pil_libre and not r["libre"]
            self.pil_libre = r["libre"]
            # volant : le retour est la position RÉELLE du monstre, tenu ``vol`` u au-dessus de nous : l'écart se
            # mesure à plat, et le recollage vise ce point-là (espace libre : le monstre y est), jamais ce point
            # moins la hauteur de vol (sous le sol quand le monstre vole bas) ; le Link caché retombe de lui-même
            if self._vol() or dr & pilotage.DR_XZ:    # x et z seulement (Poe, ReDead) : à plat aussi
                ecart = math.hypot(pos[0] - r["pos"][0], pos[2] - r["pos"][2])
            else:
                ecart = math.dist(pos, r["pos"])
            besoin = not r["libre"] and (not self.pil_colle or fin_geste or ecart > pilotage.RECOLLAGE)
            if fin_geste:
                self.pil_colle = False              # recollage dû, même si la période n'est pas écoulée
            if besoin and self.teleport and can and mem is not None \
                    and now - self.t_recolle >= pilotage.RECOLLAGE_PERIODE:
                self.pil_colle = True
                if ecart > 30.0:
                    try:
                        if teleporter(mem, base, pid, (r["pos"][0], r["pos"][1] + 5.0, r["pos"][2])):
                            self.n_recollages += 1
                            self.t_recolle = now
                            self.pil_prev = None    # le saut n'est pas une vitesse
                            self.pil_vit = (0.0, 0.0)
                            pos = (r["pos"][0], r["pos"][1] + 5.0, r["pos"][2])
                    except Exception as exc:
                        self._note(f"recollage refusé ({type(exc).__name__}: {exc})")
        # épinglage de x et z seulement : la hauteur du monstre n'est pas la nôtre, pas de prédiction locale
        predire = bool(frais and r["ok"] and not r["libre"] and self.prediction and not dr)
        if pp is not None:
            rot = st.get("rotY")
            vol = self._vol()                       # volant : la marionnette est dessinée à sa hauteur de vol
            dessin = [pos[0], pos[1] + vol, pos[2]] if vol else list(pos)
            pp.pilote_local = ((po["key"], dessin, rot if isinstance(rot, int) and not isinstance(rot, bool) else 0)
                               if predire else None)

    # --- règles locales ---------------------------------------------------------------------------------
    def actif(self) -> bool:
        return self.enabled and self.on

    def hanteur(self) -> bool:
        return self.actif() and self.role == ROLE_HANTEUR

    def monde_ok(self, now: float | None = None) -> bool:
        """Faux : on n'envoie rien qui touche le monde (Hanteur, ou quarantaine juste après être redevenu Link)."""
        if not self.actif():
            return True
        now = self.clock() if now is None else now
        return self.role == ROLE_LINK and not self.monde_envoute and now >= self.fin_quarantaine

    def depense_clone(self) -> bool:
        """Vrai : Hanteur qui joue un Link (potion) avec le droit de dépenser (réglage du lobby, option du
        client) : ses compteurs communs (rubis, flèches, bombes), et eux seuls, partent au serveur, qui n'en
        garde que les baisses (synchro/potion.py)."""
        clone = self.clone
        return bool(self.hanteur() and self.depense_on and clone is not None
                    and clone.get("depense") in ("tout", "plafond"))

    def est_hanteur(self, pid) -> bool:
        return self.actif() and self.roles.get(pid) == ROLE_HANTEUR

    def cache_par_potion(self) -> bool:
        """Vrai : notre Link est possédé (potion) : caché et téléporté derrière le clone. Monde persistant : jamais
        d'image du monde lue pendant ce temps."""
        return self.actif() and self.role == ROLE_LINK and self.envoute is not None

    def visibles(self, others: dict) -> dict:
        """Joueurs à montrer en fantôme : un Link ne voit jamais un Hanteur, sauf le clone qui joue son propre Link
        (potion : état marqué ``clone`` par le serveur). Un Hanteur voit les Link (clones de potion compris : le
        serveur les relaie sous l'identité du Link possédé) et, parmi les autres Hanteurs, seulement ceux qui sont
        MANIFESTÉS (``hv`` dans leur ``state``, voir ``champ_hv``) : ni spectateur, ni pilote d'un monstre, ni
        ancien client. Un Hanteur gardé est rendu en COPIE sans ``car`` ni ``stk`` : pas de marionnette d'objet
        porté ni de bâton pour un spectre. Camouflage de la tenue jaune : un Hanteur ne voit pas du tout un Link
        camouflé (``camoufles``)."""
        if not self.actif():
            return others
        if self.role != ROLE_HANTEUR:
            return {pid: o for pid, o in others.items() if self.roles.get(pid) != ROLE_HANTEUR or o.get("clone")}
        out = {}
        for pid, o in others.items():
            if pid in self.camoufles and self.roles.get(pid) != ROLE_HANTEUR:
                continue                            # camouflé : invisible pour les Hanteurs
            if self.roles.get(pid) != ROLE_HANTEUR or o.get("clone"):
                out[pid] = o
            elif o.get("hv") in (1, True):
                out[pid] = {k: v for k, v in o.items() if k not in ("car", "stk")}
        return out

    def spectres(self, vus: dict) -> frozenset:
        """Parmi ``vus`` (résultat de ``visibles``) : les Hanteurs manifestés, à dessiner avec la teinte « spectre »
        et à servir APRÈS les Link quand les emplacements de fantômes manquent. Vide chez un Link (le seul Hanteur
        qu'il voit est le clone de sa potion, qui a l'apparence d'un Link)."""
        if not self.hanteur():
            return frozenset()
        return frozenset(pid for pid, o in vus.items()
                         if self.roles.get(pid) == ROLE_HANTEUR and not o.get("clone"))

    def accepte_etat(self, pid, msg: dict) -> bool:
        """``state`` d'un autre joueur gardé ? Un Link ne garde jamais celui d'un Hanteur, sauf celui du clone
        qui joue son Link (marqué ``clone`` par le serveur, seulement pour le possédé)."""
        return not (self.est_hanteur(pid) and not self.hanteur() and not msg.get("clone"))

    def liens(self) -> list[int]:
        return sorted(p for p, r in self.roles.items() if r == ROLE_LINK)

    def _menu_mod(self, mb, stage) -> None:
        """Le menu d'invocation tel que le mod le dessine (zone WWMPMNU1), réécrit seulement s'il a changé."""
        etat = self.menu.etat(self.especes, self.especes_ici(stage) if stage else [], self.points)
        if etat == self.menu.ecrit or mb is None or not callable(getattr(mb, "menu_ecrire", None)):
            return
        try:
            if mb.menu_ecrire(etat):
                self.menu.ecrit = etat
        except Exception as exc:                          # noqa: BLE001 : jamais bloquant
            self._note(f"menu d'invocation : écriture ignorée ({type(exc).__name__}: {exc})")

    def especes_ici(self, stage) -> list[int]:
        return [p for p in sorted(self.especes) if p in self.vues.get(stage, [])]

    # --- commandes (touches, lanceur) -------------------------------------------------------------------
    def regler_commandes(self, d) -> bool:
        """Table des commandes reçue du lanceur (fichier de contrôle, clé ``commandes`` : ``commun/commandes``
        ``table_client``), appliquée à chaud. Rend True si elle a changé."""
        t = cmdes.lire_table_client(d)
        if t is None or t == self.table_commandes:
            return False
        self.table_commandes = t
        if self.touches is not None:
            self.touches.regler(t)
        try:
            from synchro import barre
            barre.regler_touches({k: v[0] for k, v in t.items()})
        except Exception:                # noqa: BLE001 : affichage seulement
            pass
        self._note("commandes du lanceur appliquées : " + ", ".join(
            f"{k} {v[0] or '-'}{' / ' + v[1] if v[1] else ''}" for k, v in t.items()))
        return True

    def commande(self, action: str, arg=None, me=None, stage=None) -> None:
        if action == "resister":
            if self.actif() and self.role == ROLE_LINK and self.envoute is not None \
                    and self.envoute.get("resister", True):
                with self._lock:
                    self.n_resist += 1
                    self._sortir({"type": "hresister"})
            return
        if not self.hanteur():
            return
        if action == menu_invocation.COMMANDE or action in menu_invocation.ACTIONS:
            with self._lock:
                if self.clone is not None or self.possede is not None:
                    self.menu.fermer()
                    if action == menu_invocation.COMMANDE:
                        self._note("menu d'invocation : pas pendant une possession ou une potion")
                    return
                ici = self.especes_ici(stage)
                actuel = ici[self.choix % len(ici)] if ici else None
                proc = self.menu.action(action, self.especes, ici, actuel)
                if action == "menu_ok" and proc is None and self.menu.liste(self.especes, ici):
                    self._note("pas encore vu vivant ici : le serveur le refuse (il faut un exemplaire vivant)")
            if proc is not None:
                if proc in ici:
                    self.choix = ici.index(proc)
                self.commande("invoquer", arg=proc, me=me, stage=stage)
            return
        with self._lock:
            if self.clone is not None and action in ("posseder", "lacher", "potion", "manifester", "invoquer",
                                                     "suivre"):
                if action in ("posseder", "potion", "lacher"):
                    self._sortir({"type": "hpotion_stop"})      # F4 / bouton pendant la potion : l'arrêter
                else:
                    self._note("pendant la potion, tu joues un Link (F4 pour l'arrêter)")
                return
            if action == "espece" and self.possede is not None and arg is None:
                action, arg = "viser", arg                       # F7 pendant une possession : viser le Link suivant
            if action in ("monter", "descendre"):                # PagePréc / PageSuiv : hauteur d'un volant piloté
                self._hauteur(1 if action == "monter" else -1)
                return
            if action == "garde" and self.possede is None:       # F3 sans possession : attaque du boss
                self._bouton_boss("garde", stage, self.clock())
                return
            if action == "garde":                                # F3 (phase 3) : ordre « garde »
                action, arg = "ordre", ordres.GESTES.index("garde")
            if action == capacites.GESTE:                        # F2 : capacité propre de l'espèce possédée
                if self.possede is None:
                    self._note("la capacité (F2) se joue pendant une possession")
                    return
                self.n_gestes[capacites.GESTE] = self.n_gestes.get(capacites.GESTE, 0) + 1
                self._sortir({"type": "hordre", "ordre": capacites.GESTE})
                return
            if action == "ordre":
                # phase 3 : bouton du lanceur (0 attaque, 1 lourde, 2 garde) ; seulement pendant une possession
                if self.possede is None or not isinstance(arg, int) or isinstance(arg, bool) \
                        or not 0 <= arg < len(ordres.GESTES):
                    return
                g = ordres.GESTES[arg]
                self.n_gestes[g] += 1
                self._sortir({"type": "hordre", "ordre": g})
                return
            if action == "influencer":
                # phase 4 : attaque d'un boss (index dans ordres.ORDRES)
                if not self.influence_on:
                    self._note("l'influence sur les boss est coupée dans cette partie")
                    return
                if not isinstance(arg, int) or isinstance(arg, bool) or not 0 <= arg < ordres.N_ORDRES \
                        or ordres.ORDRES[arg].genre != "boss":
                    return
                self.n_influences += 1
                self._sortir({"type": "hinfluence", "ordre": ordres.ORDRES[arg].cle})
                return
            if action == "enfermer":
                # enfermement (synchro/enfermement.py) : le serveur choisit le Link le plus proche (ou ``arg``)
                if not self.enf_on:
                    self._note("l'enfermement est coupé dans cette partie (ou serveur pas à jour)")
                    return
                self.n_enfermer += 1
                self._sortir({"type": "henfermer", "cible": arg} if isinstance(arg, int) and not isinstance(arg, bool)
                             else {"type": "henfermer"})
                return
            if action == "potion":
                if not self.potion_on:
                    self._note("la potion est coupée dans cette partie")
                    return
                self._sortir({"type": "hpotion", "cible": arg} if isinstance(arg, int) and not isinstance(arg, bool)
                             else {"type": "hpotion"})
                return
            if action == "viser":
                if self.possede is None:
                    return
                liens = [p for p in self.liens() if p != me and p not in self.possedes]
                if isinstance(arg, int) and not isinstance(arg, bool) and (arg in liens or arg == -1):
                    c = None if arg == -1 else arg
                elif not liens:
                    c = None
                elif self.cible_laisse in liens:
                    i = liens.index(self.cible_laisse) + 1
                    c = liens[i] if i < len(liens) else None      # après le dernier : le plus proche
                else:
                    c = liens[0]
                self.cible_laisse = c
                self._sortir({"type": "hviser", "cible": c})
                return
            if action == "suivre":
                if self.manifeste:                  # suivre = redevenir spectateur (possession relâchée)
                    self._manifester(False)
                liens = [p for p in self.liens() if p != me and p not in self.camoufles]   # camouflés : jamais
                if not liens:
                    self._note("aucun Link à suivre" + (" (camouflé)" if self.camoufles else ""))
                    return
                if isinstance(arg, int) and arg in liens:
                    self.cible = arg
                elif self.cible in liens:
                    self.cible = liens[(liens.index(self.cible) + 1) % len(liens)]
                else:
                    self.cible = liens[0]
                self.saut_demande = True
                self._note(f"tu suis {self.noms.get(self.cible, self.cible)}")
            elif action == "suivi":
                self.suivi = not self.suivi if arg is None else bool(arg)
                self._note("suivi continu " + ("activé" if self.suivi else "coupé"))
            elif action == "camera":
                self.colle = not self.colle if arg is None else bool(arg)
                self._note("caméra spectateur " + ("collée au Link suivi (ton Link ne bouge plus ; F9 pour te "
                                                   "manifester)" if self.colle else "libre (tu marches, caché)"))
            elif action == "espece":
                ici = self.especes_ici(stage) or sorted(self.especes)
                if ici:
                    if isinstance(arg, int) and arg in ici:
                        self.choix = ici.index(arg)
                    else:
                        self.choix = (self.choix + 1) % len(ici)
                    self._note(f"espèce choisie : {self.especes[ici[self.choix]]['nom']}")
            elif action == "invoquer":
                ici = self.especes_ici(stage)
                proc = arg if isinstance(arg, int) else (ici[self.choix % len(ici)] if ici else None)
                if proc is None:
                    self._note("aucune espèce invocable vue ici")
                    return
                if self.gel_voulu:
                    # collé sur un Link, le monstre naîtrait SUR lui : le serveur refuse toujours (DIST_LINK_MIN)
                    self._note("caméra collée : décoche-la (ou F8) et éloigne-toi du Link pour invoquer")
                    return
                if len(self.out) < 8:
                    self.out.append({"type": "hsummon", "proc": proc})
            elif action == "manifester":
                voulu = (not self.manifeste) if arg is None else bool(arg)
                if voulu == self.manifeste:
                    return
                self._manifester(voulu)
                if voulu:
                    self._note("tu te manifestes : tu marches librement (plus de suivi continu) ; approche-toi d'un "
                               "monstre et appuie sur F4 pour le posséder")
                else:
                    self._note("tu redeviens spectateur")
            elif action in ("posseder", "lacher"):
                if self.possede is not None:
                    self._sortir({"type": "hlacher"})
                    return
                if action == "lacher":
                    return
                if not self.possession_on:
                    self._note("la possession est coupée dans cette partie")
                    return
                if not self.manifeste:
                    self._manifester(True)
                self._sortir({"type": "hposseder"})

    def _sortir(self, msg: dict) -> None:
        if len(self.out) < 8:
            self.out.append(msg)

    def _manifester(self, on: bool) -> None:
        """Manifesté : plus de suivi continu (sinon la téléportation arracherait le Hanteur à son monstre) ;
        spectateur : suivi continu remis, possession relâchée. Le serveur prévient les Link de la salle."""
        self.manifeste = on
        if on:
            self.suivi_avant, self.suivi = self.suivi, False
        else:
            self.suivi = self.suivi_avant
            if self.possede is not None:
                self._sortir({"type": "hlacher"})
        self._sortir({"type": "hmanif", "on": int(on)})

    # --- fil de travail ---------------------------------------------------------------------------------
    def tick(self, c, local: dict | None, others: dict, now: float | None = None) -> None:
        """``c`` : le Client (échantillonneur, mémoire, cinématique, purge des synchros). Jamais fatal."""
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        sm = c.sampler
        mem = getattr(sm, "progress_mem", None)
        st = local["state"] if local else None
        busy = bool(c.busy) or bool(getattr(sm, "leaving", False))
        base, pid = getattr(sm, "link_base", 0), getattr(sm, "link_id", None)
        can = mem is not None and _writable(mem) and not getattr(c.args, "progress_no_apply", False)
        for p, o in others.items():
            if isinstance(o.get("pos"), list) and len(o["pos"]) == 3:
                h = [x for x in self.hist.get(p, []) if now - x[0] <= SOL_FENETRE + 0.5]
                h.append((now, float(o["pos"][1])))
                self.hist[p] = h[-40:]
        # changement de rôle à appliquer au jeu (une fois)
        with self._lock:
            dev, self.devient = self.devient, None
            role = self.role if self.actif() else ROLE_LINK
        if dev is not None:
            self._transition(c, dev, role, now)
        if self.jeu_attente is not None and role == ROLE_LINK:
            self._jeu_link(st, mem, base, pid, can, busy, now)
        with self._lock:
            if self.envoute is not None and now >= self.envoute["fin"] + GEL_MARGE:
                # fin jamais reçue (« hlibere » perdu ET serveur muet) : Link rendu au joueur, comme le clone plus
                # bas. Le serveur ne fait que raccourcir la potion : jamais plus long que la durée annoncée.
                self.envoute = None
            clone, envoute = self.clone, (self.envoute if role == ROLE_LINK else None)
            libere = self.monde_envoute and envoute is None
            if libere:
                # fin de possession : quarantaine AVANT de rouvrir le monde (même verrou que ``receive``)
                if role == ROLE_LINK and self.actif():
                    self.fin_quarantaine = max(self.fin_quarantaine, now + QUARANTAINE_CLIENT)
                self.monde_envoute = False
        if libere and role == ROLE_LINK and self.actif():
            c.monde_reprise(self.fin_quarantaine)    # ce que le Link caché a fait chez lui est oublié
        if clone is not None and now >= clone["fin"] + 5.0:
            clone = self.clone = None                # fin jamais reçue : le Hanteur redevient invisible
        gele = bool(getattr(getattr(c, "gel", None), "gele", lambda: False)())
        self.gel_voulu = self._gel_voulu(role, clone, envoute, st, others, busy, now)
        # Link caché : Hanteur (sauf pendant la potion : il joue un Link visible) ou Link possédé (son corps est joué
        # par le clone ; il est spectateur)
        cacher = self.cacher and ((role == ROLE_HANTEUR and clone is None) or envoute is not None)
        if not cacher and self.a_montrer and st is not None and mem is not None and can and not busy:
            try:
                if link_ok(mem, base, pid):          # Link recréé (autre stage) : le bit est déjà parti
                    cacher_link(mem, base, pid, False)
                    self.a_montrer = False
            except Exception as exc:
                self._note(f"Link pas remontré ({type(exc).__name__}: {exc})")
        if cacher and st is not None and mem is not None and can and not busy:
            try:
                if cacher_link(mem, base, pid, True):
                    self.n_cache += 1
                    self.a_montrer = True
            except Exception as exc:
                self._note(f"écriture refusée ({type(exc).__name__}: {exc})")
        if role == ROLE_HANTEUR and st is not None and mem is not None and can and not busy:
            try:
                if remonter_vie(mem):
                    self.n_vie += 1
            except Exception as exc:
                self._note(f"écriture refusée ({type(exc).__name__}: {exc})")
        if role == ROLE_HANTEUR or envoute is not None:
            if self.touches is None and hasattr(mem, "proc"):
                natif = getattr(mem, "hote", None) == "natif"
                self.touches = Touches(getattr(mem.proc, "pid", None), natif=natif, table=self.table_commandes)
                if self.touches.raison:
                    self._note(self.touches.raison)       # Linux : touches inactives, et pourquoi (une fois)
                if natif and self.table_commandes is None:
                    self._note(RAISON_NATIF)
            if self.touches is not None:
                if role != ROLE_HANTEUR or self.clone is not None or self.possede is not None:
                    self.menu.fermer()
                self.touches.menu_ouvert = self.menu.ouvert
                actions = self.touches.lire_commandes()
                try:                                      # combinaisons de manette (mod natif 0.37.0)
                    actions += self.touches.lire_manette(_boite(c), True)
                except Exception as exc:                  # noqa: BLE001 : boîte occupée ou illisible
                    self._note(f"manette : lecture ignorée ({type(exc).__name__}: {exc})")
                for action in actions:
                    if role != ROLE_HANTEUR:
                        if action == "posseder":     # Link possédé : F4 = résister
                            self.commande("resister")
                        continue
                    self.commande(action, me=c.me, stage=st["stage"] if st else None)
                self._menu_mod(_boite(c), st["stage"] if st else None)
        elif self.touches is not None and self.touches.masques_ecrits:
            self.menu.fermer()
            self._menu_mod(_boite(c), None)
            try:                                          # redevenu Link : la manette entière rendue au jeu
                self.touches.lire_manette(_boite(c), False)
            except Exception:                             # noqa: BLE001
                pass
        if role == ROLE_HANTEUR:
            if clone is not None:
                self.ctx_ordres = None
                self._placer_clone(clone, st, mem, base, pid, can, busy)
            else:
                self._ordres(mem, base, pid, busy, now, st["stage"] if st else None)
                # lot L3 (A6) : le fil des images relit le coup entre deux tours (rien en cinématique, sans jeu)
                self.ctx_ordres = ((mem, base, pid, st["stage"] if st else None, now)
                                   if mem is not None and not busy and st is not None else None)
                if not self.manifeste:
                    if gele and self.gel_voulu:
                        self._coller(self.cible, st, others, mem, base, pid, can, busy)
                    else:
                        self._suivre(st, others, mem, base, pid, can, busy, now)
        elif envoute is not None:
            self._vie_envoute(envoute, st, mem, can, busy)
            if gele and self.gel_voulu:
                self._coller(envoute["par"], st, others, mem, base, pid, can, busy)
            else:
                self._suivre_clone(envoute, st, others, mem, base, pid, can, busy, now)
        elif self.liberation is not None:
            self._liberer(st, mem, base, pid, can, busy, now)
        try:
            self._pilotage(c, st, mem, base, pid, can, busy, now)
        except Exception as exc:                     # jamais fatal : plus de prédiction, la laisse fait le reste
            self.pil_vit = None
            self._note(f"pilotage : erreur ignorée ({type(exc).__name__}: {exc})")
        self._creer(c, st, busy, now)
        self._effets(c, st, others, busy, now)

    def _gel_voulu(self, role, clone, envoute, st, others, busy, now) -> bool:
        """Link à geler maintenant ? Link possédé : toujours (sauf en bateau : le bateau a besoin de son Link).
        Hanteur spectateur : caméra collée (``colle`` et suivi continu), Link suivi visible dans NOTRE salle, pas
        manifesté, pas de monstre possédé, pas de potion. Jamais sans état lu (le pack rend de toute façon Link
        au jeu pendant un événement)."""
        if not self.actif() or st is None or a_bord(st):
            return False
        if role == ROLE_LINK:
            return envoute is not None
        if role != ROLE_HANTEUR or clone is not None or self.manifeste or self.possede is not None:
            return False
        if not (self.colle and self.suivi) or self.cible is None or self.roles.get(self.cible) != ROLE_LINK:
            return False
        if self.cible in self.camoufles:
            return False                            # camouflé (tenue jaune) : pas de caméra collée sur lui

        o = others.get(self.cible)
        return bool(o is not None and o.get("stage") == st.get("stage") and o.get("room") == st.get("room")
                    and _pos3(o.get("pos")) is not None and not a_bord(o))

    def _coller(self, qui, st, others, mem, base, pid, can, busy) -> None:
        """Link GELÉ (zone du pack armée par nous) posé chaque tick sur le joueur ``qui`` (clone ou Link suivi),
        même salle : la caméra le suit. Le Link gelé ne tombe pas, ne touche rien : aucune condition de sol."""
        o = others.get(qui)
        if o is None or st is None or mem is None or not can or busy or not self.teleport:
            return
        if o.get("stage") != st.get("stage") or o.get("room") != st.get("room") or a_bord(st):
            return
        p = _pos3(o.get("pos"))
        if p is None or math.dist(p, st["pos"]) < 1.0:
            return
        from synchro import gel
        try:
            if gel.coller(mem, base, pid, p):
                self.n_colle += 1
        except Exception as exc:
            self._note(f"écriture refusée ({type(exc).__name__}: {exc})")

    def _placer_clone(self, clone, st, mem, base, pid, can, busy) -> None:
        """Potion bue : le Link du Hanteur (plus caché) est téléporté une fois sur la dernière position au sol du
        possédé (vue par le serveur), dans la même salle, jeu libre."""
        if clone["place"] or st is None or mem is None or not can or busy or not self.teleport:
            return
        clone["place"] = True
        pos = clone.get("pos")
        if pos is None or clone.get("stage") != st.get("stage") or clone.get("room") != st.get("room") \
                or a_bord(st):
            return
        try:
            if teleporter(mem, base, pid, (pos[0], pos[1] + 5.0, pos[2])):
                self.n_clone_sauts += 1
        except Exception as exc:
            self._note(f"téléportation refusée ({type(exc).__name__}: {exc})")

    def _vie_envoute(self, envoute, st, mem, can, busy) -> None:
        """Relecture adverse : Link possédé, son Link caché (téléporté sur le clone, manette toujours active) peut
        être blessé chez lui (environnement, marionnette pas encore posée) ; ces dégâts s'ajouteraient à ceux du
        clone, perdus à la libération (double compte, game over de spectateur). Sa vie est gardée à la valeur du début
        de la possession (remontée seulement jusque-là, jamais depuis 0, jamais en événement), comme le Hanteur."""
        life = st.get("life") if st is not None else None
        if envoute.get("vie") is None:
            if isinstance(life, int) and not isinstance(life, bool) and life > 0:
                envoute["vie"] = life
            return
        if not isinstance(life, int) or life >= envoute["vie"] or mem is None or not can or busy:
            return
        try:
            if remonter_vie(mem, envoute["vie"]):
                self.n_vie += 1
        except Exception as exc:
            self._note(f"écriture refusée ({type(exc).__name__}: {exc})")

    def _suivre_clone(self, envoute, st, others, mem, base, pid, can, busy, now) -> None:
        """Link possédé : son Link caché suit le clone (le fantôme du Hanteur, état marqué ``clone``) dans la
        même salle, au sol, au-delà de SUIVI_DIST, au plus toutes les SUIVI_PERIODE s : la caméra suit son corps."""
        o = others.get(envoute["par"])
        if o is None or not o.get("clone") or st is None or not self.teleport:
            return
        if o.get("stage") != st.get("stage") or o.get("room") != st.get("room"):
            return
        p = _pos3(o.get("pos"))
        if p is None or busy or not can or mem is None or a_bord(st) or o.get("busy") in (1, True) \
                or not self.au_sol(envoute["par"], now):
            return
        if math.dist(p, st["pos"]) <= SUIVI_DIST or now - self.t_saut < SUIVI_PERIODE:
            return
        try:
            if teleporter(mem, base, pid, (p[0], p[1] + 5.0, p[2])):
                self.n_suivi_clone += 1
                self.t_saut = now
        except Exception as exc:
            self._note(f"téléportation refusée ({type(exc).__name__}: {exc})")

    def _liberer(self, st, mem, base, pid, can, busy, now) -> None:
        """Link libéré : vie baissée des dégâts reçus par le clone (plancher 1 cœur, jamais vers le haut), puis
        téléporté là où le clone était au sol (même salle). Une fois, jeu libre, au plus JEU_ATTENTE s après."""
        lib = self.liberation
        if now - lib["t"] > JEU_ATTENTE:
            self.liberation = None
            return
        if st is None or mem is None or not can or busy:
            return
        self.liberation = None
        try:
            life = st.get("life")
            if isinstance(life, int) and lib["degats"] > 0:
                nv = potion.vie_liberation(life, lib["degats"])
                if nv < life:
                    baisser_vie(mem, nv)
            pos = lib.get("pos")
            if pos is not None and self.teleport and lib.get("stage") == st.get("stage") \
                    and lib.get("room") == st.get("room") and not a_bord(st):
                if teleporter(mem, base, pid, (pos[0], pos[1] + 5.0, pos[2])):
                    self.n_sauts += 1
        except Exception as exc:
            self._note(f"libération : écriture refusée ({type(exc).__name__}: {exc})")

    def _bouton_boss(self, geste: str, stage, now: float) -> bool:
        """Boss aux boutons : hors possession, dans une stage de boss commandable, un geste du Hanteur (coup
        d'épée, coup chargé, F3) devient la demande ``hinfluence`` de l'attaque de boss que ``ordres.BOUTONS_BOSS``
        lui associe. Le serveur garde toutes ses règles (coût, recharge, pause du boss, combattants) ; ici, au plus
        une demande par ``BOUTON_BOSS_PERIODE``. Appelant : ``_lock`` tenu ou fil de travail (``_sortir``)."""
        if not self.hanteur() or not self.influence_on or self.possede is not None or self.clone is not None:
            return False
        if now - self.__dict__.get("t_bouton_boss", float("-inf")) < ordres.BOUTON_BOSS_PERIODE:
            return False
        rang = self.__dict__.get("n_boutons_boss", 0)
        cle = ordres.bouton_boss(stage, geste, rang)
        if cle is None:
            return False
        self.t_bouton_boss = now
        self.n_boutons_boss = rang + 1
        self.n_influences += 1
        self._sortir({"type": "hinfluence", "ordre": cle})
        return True

    def _ordres(self, mem, base, pid, busy, now, stage=None) -> None:
        """Possession : un coup d'épée du Link caché (mCutType qui passe de 0 à une attaque, ou change de type ou
        de rang de combo) devient un ordre ``hordre`` (élan vers le Link le plus proche, chez l'autorité). Lecture
        seule, Link relu (id et procName) juste avant. Sans possession, dans une stage de boss : le même coup
        d'épée commande une attaque du boss (``_bouton_boss``)."""
        boss_ici = self.possede is None and self.influence_on and ordres.boss_de(stage) is not None
        if (self.possede is None and not boss_ici) or mem is None or busy or not link_ok(mem, base, pid):
            self.coup_avant = None
            return
        from synchro.laisse import ORDRE_PERIODE, lire_coup
        cut = lire_coup(mem, base)
        if cut is None:
            return
        with self._ordres_lock:
            avant, self.coup_avant = self.coup_avant, cut
            if cut[0] == 0 or avant is None or cut == avant or now - self.t_ordre < ORDRE_PERIODE:
                return
            self.t_ordre = now if not boss_ici else self.t_ordre
        self._ordre_detecte(cut, boss_ici, stage, now)

    def _ordre_detecte(self, cut, boss_ici: bool, stage, now: float) -> None:
        """Nouveau coup d'épée du Link caché (tour ou fil des images) -> ordre ``hordre`` (ou attaque du boss)."""
        g = ordres.geste(cut[0]) or "attaque"       # phase 3 : tournoyante, coup sauté -> « lourde »
        if boss_ici:
            with self._lock:
                self._bouton_boss(g, stage, now)
            return
        self.n_ordres += 1
        self.n_gestes[g] += 1
        with self._lock:
            self._sortir({"type": "hordre", "ordre": g})

    def ordres_image(self, now: float | None = None) -> bool:
        """Lot L3 (A6), fil des images : relit (mCutType, mCutCount) du Link caché (2 octets, une lecture) à chaque
        image ; si le coup a changé, joue ``_ordres`` (Link relu, mêmes gardes que le tour) tout de suite. Contexte du
        dernier tour seulement (plus de 0,5 s, cinématique, potion, sans possession ni boss : rien). True si un
        message est parti dans la file (à envoyer sans attendre le tour : ``take_ordres``)."""
        ctx = self.ctx_ordres
        if ctx is None or not self.ordres_image_on:
            return False
        mem, base, pid, stage, t = ctx
        now = self.clock() if now is None else now
        if now - t > 0.5:
            return False
        from synchro.laisse import lire_coup
        cut = lire_coup(mem, base)
        if cut is None or cut == self.coup_avant:
            return False
        n = len(self.out)
        self._ordres(mem, base, pid, False, now, stage)
        if len(self.out) != n:
            self.n_ordres_image += 1
            return True
        return False

    def ordres_image_actif(self) -> bool:
        ctx = self.ctx_ordres
        return ctx is not None and self.ordres_image_on and self.clock() - ctx[4] <= 0.5

    def take_ordres(self) -> list[dict]:
        """Lot L3 (A6), boucle réseau : seulement les ordres (``hordre``, ``hinfluence``) de la file, envoyés sans
        attendre le tour ; le reste attend le tour, dans son ordre."""
        with self._lock:
            out = [m for m in self.out if m.get("type") in ("hordre", "hinfluence")]
            self.out = [m for m in self.out if m.get("type") not in ("hordre", "hinfluence")]
        return out

    def _transition(self, c, dev, role, now) -> None:
        """Partie réseau du changement de rôle, tout de suite (même pendant un événement : la quarantaine ne doit
        pas finir avant que les files soient vidées). La partie jeu (vie de manche, position) attend le jeu libre."""
        if role == ROLE_HANTEUR:
            c.monde_pause()
            self.cible, self.saut_demande = None, False
            self.jeu_attente = None
            return
        if dev.get("raison") == "connexion perdue":
            self.jeu_attente = None
            return
        c.monde_reprise(self.fin_quarantaine)
        self.jeu_attente = (dict(dev), now)

    def _jeu_link(self, st, mem, base, pid, can, busy, now) -> None:
        """Redevenu Link : vie de manche (vers le bas) et position (là où le Link est tombé, même salle), une fois,
        jeu libre, au plus JEU_ATTENTE s après l'échange."""
        dev, t = self.jeu_attente
        if now - t > JEU_ATTENTE:
            self.jeu_attente = None
            return
        if st is None or mem is None or not can or busy:
            return
        self.jeu_attente = None
        try:
            vie = dev.get("vie")
            if isinstance(vie, int) and not isinstance(vie, bool) and st.get("life", 0) > vie:
                baisser_vie(mem, vie)
            pos = _pos3(dev.get("pos"))
            meme_salle = dev.get("stage") == st.get("stage") and dev.get("room") == st.get("room")
            if pos is not None and meme_salle and self.teleport and not a_bord(st):
                if teleporter(mem, base, pid, (pos[0], pos[1] + 5.0, pos[2])):
                    self.n_sauts += 1
        except Exception as exc:
            self._note(f"changement de rôle : écriture refusée ({type(exc).__name__}: {exc})")

    def au_sol(self, p: int, now: float) -> bool:
        h = [y for t, y in self.hist.get(p, []) if now - t <= SOL_FENETRE]
        old = [t for t, _y in self.hist.get(p, []) if now - t >= SOL_FENETRE - 0.2]
        return len(h) >= 3 and bool(old) and max(h) - min(h) <= SOL_DY

    def _suivre(self, st, others, mem, base, pid, can, busy, now) -> None:
        if self.cible is None or st is None or not self.teleport:
            return
        if self.cible in self.camoufles:            # camouflé (tenue jaune) : pas suivi tant que ça dure
            if self.saut_demande:
                self._note(f"{self.noms.get(self.cible, '?')} est camouflé : impossible de le suivre pour l'instant")
                self.saut_demande = False
            return
        o = others.get(self.cible)
        if o is None or self.roles.get(self.cible) != ROLE_LINK:
            if self.saut_demande:
                self._note(f"{self.noms.get(self.cible, '?')} n'est pas visible (autre stage ?)")
                self.saut_demande = False
            return
        demande = self.saut_demande
        if o.get("stage") != st["stage"] or o.get("room") != st["room"]:
            if demande:
                self._note(f"{self.noms.get(self.cible, '?')} est dans une autre salle ({o.get('stage')} "
                           f"{o.get('room')}) : rejoins-le à pied")
                self.saut_demande = False
            return
        pos = _pos3(o.get("pos"))
        if pos is None or busy or not can or mem is None or a_bord(st) or a_bord(o) \
                or o.get("busy") in (1, True) or not self.au_sol(self.cible, now):
            return                                  # on réessaiera (demande gardée)
        loin = math.dist(pos, st["pos"]) > SUIVI_DIST
        if not (demande or (self.suivi and loin)) or now - self.t_saut < SUIVI_PERIODE:
            return
        self.saut_demande = False
        if math.dist(pos, st["pos"]) < 60.0:
            return
        try:
            if teleporter(mem, base, pid, (pos[0], pos[1] + 5.0, pos[2])):
                self.n_sauts += 1
                self.t_saut = now
        except Exception as exc:
            self._note(f"téléportation refusée ({type(exc).__name__}: {exc})")

    def _creer(self, c, st, busy, now) -> None:
        if not self.a_creer:
            return
        sm = c.sampler
        local = getattr(sm, "local_enemies", None) or {}
        for key, job in list(self.a_creer.items()):
            m = job["msg"]
            if key in local:
                del self.a_creer[key]
                continue
            if now - job["recu"] > 60.0 or job["essais"] >= CREATION_ESSAIS:
                if job["essais"] >= CREATION_ESSAIS:
                    self._note(f"monstre invoqué jamais vu ici après {CREATION_ESSAIS} essais ({key})")
                del self.a_creer[key]
                continue
            if st is None or st.get("stage") != m.get("stage") or st.get("room") != m.get("room"):
                continue                     # créé quand on sera dans sa salle (le serveur le renvoie aussi)
            room_t = max(getattr(c, "zone_t", 0.0) if c.cur_zone == zone_of(st["stage"], st["room"]) else now,
                         getattr(sm, "room_t", 0.0) or 0.0)
            if busy or time.time() - room_t < SALLE_STABLE or now - job["t"] < CREATION_ATTENTE:
                continue
            if not getattr(sm, "can_write", True) or not hasattr(sm, "create_actor"):
                continue
            job["essais"] += 1
            job["t"] = now
            try:
                new = sm.create_actor(m["proc"], m["params"] & 0xFFFFFFFF, tuple(m["pos"]), m["room"],
                                      int(m.get("rotY", 0)))
            except Exception as exc:
                new = None
                self._note(f"création refusée ({type(exc).__name__}: {exc})")
            if new is not None:
                self.n_crees += 1
                if hasattr(sm, "request_rescan"):
                    sm.request_rescan()
                try:
                    self._fx_invocation(c, sm, job, now)
                except Exception as exc:             # jamais fatal : au pire la fumée manque
                    self._note(f"effet ignoré ({type(exc).__name__}: {exc})")

    # --- affichage --------------------------------------------------------------------------------------
    def vue(self, me=None, stage=None) -> dict | None:
        """Pour le lanceur (fichier d'état du client)."""
        if not self.actif():
            return None
        now = self.clock()
        ici = set(self.especes_ici(stage))
        esp = [{"proc": p, "nom": e["nom"], "cout": e["cout"], "ici": p in ici,
                "recharge": round(max(0.0, self.recharges.get(p, now) - now), 1)}
               for p, e in sorted(self.especes.items())]
        return {"role": self.role, "attente": self.attente, "points": self.points,
                "cible": self.cible, "cible_nom": self.noms.get(self.cible) if self.cible is not None else None,
                "suivi": self.suivi, "colle": self.colle, "gel": self.gel_voulu, "especes": esp,
                "choix": (sorted(ici)[self.choix % len(ici)] if ici else None),
                "liens": [{"id": p, "nom": self.noms.get(p, "?")} for p in self.liens() if p != me],
                "joueurs": [{"id": p, "nom": self.noms.get(p, "?"), "role": r, "points": self.points_tous.get(p)}
                            for p, r in sorted(self.roles.items())],
                "rotation": self.rotation, "rotation_dans": self.rotation_dans,
                "victoire": victoire.vieillir(self.victoire, now - self.t_victoire),
                "quarantaine": round(max(0.0, self.fin_quarantaine - now), 1), "note": self.derniere_note,
                "touches": dict(TOUCHES), "manifeste": self.manifeste, "possession_on": self.possession_on,
                "possession_cout": self.possession_cout,
                "recharge_possession": round(max(0.0, self.recharge_possession - now), 1),
                # proc et duree : pour la barre de compétences (synchro/barre.py)
                "possede": ({"nom": self.possede["nom"], "reste": round(max(0.0, self.possede["fin"] - now), 1),
                             "proc": _proc_cle(self.possede.get("key")),
                             "duree": round(max(0.0, self.possede["fin"] - self.possede.get("t", now)), 1)}
                            if self.possede is not None else None),
                # possession complète : état du pilotage et recharge de chaque geste (temps restant, durée)
                "pilotage": self._vue_pilotage(now),
                "recharges_gestes": ({g: {"reste": round(max(0.0, self.recharges_gestes.get(g, now) - now), 1),
                                          "duree": round(self.durees_gestes.get(g, 0.0), 1)}
                                      for g in ordres.GESTES + ((capacites.GESTE,) if self.possede.get("capacite")
                                                                 else ())}
                                     if self.possede is not None else {}),
                # capacité propre de l'espèce possédée (synchro/capacites.py) : nom, aide, genre ; None sans elle
                "capacite": self.possede.get("capacite") if self.possede is not None else None,
                # phase 2 : Link visé par le monstre possédé ; potion
                "cible_laisse": self.cible_laisse,
                "potion_on": self.potion_on, "potion_cout": self.potion_cout, "potion_duree": self.potion_duree,
                "recharge_potion": round(max(0.0, self.recharge_potion - now), 1),
                "clone": ({"nom": self.clone["nom"], "reste": round(max(0.0, self.clone["fin"] - now), 1),
                           "exo": self.clone.get("exo", 0), "depense": int(self.depense_clone())}
                          if self.clone is not None else None),
                "envoute": ({"reste": round(max(0.0, self.envoute["fin"] - now), 1),
                             "resister": int(bool(self.envoute.get("resister", True))),
                             "depense": int(self.envoute.get("depense", "rien") != "rien")}
                            if self.envoute is not None else None),
                "potion_depense": self.potion_depense, "potion_plafond": self.potion_plafond,
                "potion_resister": int(self.potion_resister),
                "possedes": [{"id": p, "nom": self.noms.get(p, "?")} for p in sorted(self.possedes)],
                # phases 3 et 4 (synchro/ordres.py)
                "gestes": ((ordres.gestes_espece(_proc_cle(self.possede.get("key")), True)
                            or (pilotage.gestes_appat(_proc_cle(self.possede.get("key")))
                                if self.possede.get("pilotage") else []))
                           if self.possede is not None else []),
                "vol": round(self._vol()),
                "enf_on": self.enf_on, "enf_duree": self.enf_duree, "enf_rayon": self.enf_rayon,
                "recharge_enf": round(max(0.0, self.recharge_enf - now), 1),
                "enfermements": [{"id": d["id"], "par": d["par"], "par_nom": self.noms.get(d["par"], "?"),
                                  "reste": round(max(0.0, d["reste"] - (now - d["t"])), 1),
                                  "liens": [self.noms.get(x, "?") for x in d["liens"]], "monstres": d["monstres"]}
                                 for d in self.enfermements],
                "influence_on": self.influence_on,
                "influences": [dict(d, reste=round(max(0.0, self.recharges_infl.get(d["cle"], now) - now), 1),
                                    combattants=len(self.combats.get(d["boss"], [])))
                               for d in self.influences]}

    def _vue_pilotage(self, now: float) -> dict | None:
        """None hors possession ; sinon {"etat": "pilote" | "geste" | "laisse" | "attente", "raison": texte,
        "code": "" | "pack" | "espece" | "option" | "evenement"} (le code sert au lanceur, qui traduit)."""
        po = self.possede
        if po is None:
            return None
        if not po.get("pilotage"):
            proc = _proc_cle(po.get("key"))
            if not self.pilote_on:
                return {"etat": "laisse", "raison": RAISONS_PILOTAGE["option"], "code": "option"}
            autre = pilotage.AUTRES.get(proc)
            if autre is not None and autre.essai:
                return {"etat": "laisse", "code": "essai", "raison": RAISONS_PILOTAGE["essai"]}
            return {"etat": "laisse", "code": "espece",
                    "raison": pilotage.NON_PILOTEES.get(proc, RAISONS_PILOTAGE["espece"])}
        r = self.pilote
        if r is None or r["key"] != po["key"] or now - r["t"] > pilotage.RETOUR_MAX:
            return {"etat": "attente", "raison": "", "code": ""}
        if not r["ok"]:
            code = r["raison"] if r["raison"] in RAISONS_PILOTAGE else ""
            return {"etat": "laisse", "raison": RAISONS_PILOTAGE.get(code, ""), "code": code}
        return {"etat": "geste" if r["libre"] else "pilote", "raison": "", "code": ""}

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        if not self.on:
            return "serveur hors mode Hanteurs"
        if self.attente:
            return "en attente de la répartition des rôles"
        s = f"tu es {'HANTEUR' if self.role == ROLE_HANTEUR else 'Link'}"
        if self.role == ROLE_HANTEUR:
            s += (f", {self.points} points, suivi : {self.noms.get(self.cible, 'personne')}, Link caché "
                  f"{self.n_cache} fois, vie remontée {self.n_vie} fois")
            if self.clone is not None:
                s += f", POTION : tu joues {self.clone['nom']}" + (" (tu dépenses son argent et ses munitions)"
                                                                 if self.depense_clone() else "")
            elif self.possede is not None:
                s += f", possède un {self.possede['nom']} ({self.n_ordres} ordre(s))"
                vp = self._vue_pilotage(self.clock())
                if vp is not None:
                    s += f", pilotage : {vp['etat']}, {self.n_recollages} recollage(s)"
            elif self.manifeste:
                s += ", manifesté"
        elif self.envoute is not None:
            s += ", POSSÉDÉ par un Hanteur" + (f" ({self.n_resist} appui(s) pour résister)"
                                              if self.envoute.get("resister", True) else " (tu regardes)")
        if self.possedes and self.role == ROLE_LINK and self.envoute is None:
            s += ", possédé(s) : " + ", ".join(self.noms.get(p, "?") for p in sorted(self.possedes))
        s += f" ; {self.n_crees} monstre(s) invoqué(s) créé(s) ici"
        if self.effets_on:
            s += f", {self.n_fx} fumée(s) d'effet (réglage : {self.effets_regle})"
            if self.effets_pack_on:
                s += f", dont {self.n_fx_pack} par le pack ({self.n_fx_pack_refus} refus, repli sur la fumée)"
        return s


def _writable(mem) -> bool:
    proc = getattr(mem, "proc", None)
    return bool(getattr(proc, "writable", True)) if proc is not None else hasattr(mem, "write")


def oublier_monde(c) -> int:
    """Hanteur -> Link : les faits « monde » faits pendant qu'il était Hanteur ne partent jamais. Files locales
    des synchros à accusé (clés, coffres, quarts, compteurs, inventaire) vidées, puis réinscription comme après
    une reconnexion (le serveur fait foi pour les clés et les compteurs). Renvoie le nombre d'éléments oubliés."""
    n = 0
    k = getattr(c, "keys", None)
    if k is not None and getattr(k, "enabled", False):
        n += len(getattr(k, "facts", {}))
        for a in ("facts", "sent", "tries"):
            d = getattr(k, a, None)
            if hasattr(d, "clear"):
                d.clear()
        k.on_connect()
    for mod in (getattr(c, "chests", None), getattr(c, "gifts", None)):
        if mod is None or not getattr(mod, "enabled", False):
            continue
        lock = getattr(mod, "_out_lock", None) or getattr(mod, "_lock", None) or threading.Lock()
        with lock:
            n += len(getattr(mod, "mine", {})) + len(getattr(mod, "out", []))
            getattr(mod, "mine", {}).clear()
            mod.out = []
        mod.on_connect()
    for mod in (getattr(c, "cnt", None), getattr(c, "inv", None)):
        if mod is None or not getattr(mod, "enabled", False):
            continue
        with mod._lock:
            n += len(getattr(mod, "queue", []))
            mod.queue = []
            if hasattr(mod, "seq_delta"):
                # Relecture adverse : un événement oublié n'est jamais accusé ; sans cela ``seq_delta`` restait
                # au-dessus de l'accusé et le jeu de l'ex-Hanteur n'était plus recalé sur le pot commun (rubis et
                # munitions de son temps de Hanteur affichés) jusqu'à son prochain événement accusé.
                mod.seq_delta = min(mod.seq_delta, getattr(mod, "ack", 0))
            if hasattr(getattr(mod, "gains", None), "clear"):
                mod.gains.clear()
        mod.on_connect()
    return n


def rapport(mem, actors, link) -> list[str]:
    """Mesures de la phase 0 (LECTURE SEULE, ``wwhd.py hanteurs``, étude §11) : NO_DRAW de Link (+0x3B8), vie et
    vie max (gameInfo+0x22 / +0x20), position et vitesse, événement en cours, puis chaque ennemi des espèces
    invocables avec ses paramètres et ceux qu'une invocation utiliserait."""
    from jeu import game
    from jeu import progress as prog
    rows = []
    flg = _read(mem, link.base + LINK_FLG0, 4)
    flg = struct.unpack(">I", flg)[0] if flg else None
    ok = link_ok(mem, link.base, link.proc_id)
    rows.append(f"Link 0x{link.base:08X} id {link.proc_id} proc {link.proc_name}{'' if ok else ' (PAS daPy_lk_c)'} : "
                f"+0x3B8 = " + (f"0x{flg:08X} NO_DRAW {'oui' if flg & 0x08000000 else 'non'}" if flg is not None
                                else "illisible"))
    gi = game.gameinfo_ptr(mem)
    vie = _read(mem, gi + game.GI_SAVE + game.SV_MAX_LIFE, 4) if gi else None
    ev = _read(mem, prog.SYM_EVT_CTRL + prog.EVT_MODE, 2)
    rows.append("  vie " + (f"{struct.unpack('>HH', vie)[1]}/{struct.unpack('>HH', vie)[0]} quarts" if vie else "illisible")
                + f", pos ({link.pos[0]:.0f}, {link.pos[1]:.0f}, {link.pos[2]:.0f}), speedF {link.speedF:.1f}"
                + (", événement en cours" if ev and ev != bytes(2) else ""))
    for a in actors:
        if a.proc_name not in ESPECES or a.group != 2:
            continue
        p = params_invocation(a.proc_name, a.params)
        rows.append(f"  {ESPECES[a.proc_name]['nom']:9} {enemy_key(a.proc_name, a.params, a.home_pos)} salle "
                    f"{a.home_room} PV {a.health}/{a.max_health} -> invocation "
                    + (f"0x{p:08X}" if p is not None else "refusée"))
    return rows
