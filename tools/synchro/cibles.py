"""Synchro v2/v3 : les monstres choisissent de viser l'autre joueur (A3), --share-ghost-aggro (activé par
--coop-donjon), --no-ghost-aggro ; désactivable à chaud (fichier de contrôle du client, bouton du lanceur).
Extension « tous les monstres » : --share-boss-aggro (activé par --coop-donjon), --no-boss-aggro, et
--aggro-experimental (désactivée par défaut) pour les espèces risquées. Saisie transmise (``synchro/saisie.py``, pack
avec le bit 9) : les espèces qui saisissent et les boss de ``BOSS_SAISIE`` sont ouverts par défaut tant que le
bouclier du pack est armé (``AggroSync.saisie``) ; sans lui, ils restent derrière --aggro-experimental.

Études : docs/etudes/sync2_ia_boss_cine.md §3.a « a3 » et docs/etudes/sync3_aggro_tous.md (tableau espèce par
espèce ; la section « Vérification » finale de chaque étude prime) ; docs/etudes/mode_hanteurs.md (autorité
par ZONE, pas par ennemi).

Ce que les joueurs voient : chez l'autorité de la zone (le PC où les monstres réfléchissent), un monstre dont le
joueur le plus proche est le fantôme d'un autre joueur se tourne vers lui, avance et l'attaque. Le coup touche le
fantôme (A1) et part vers la victime, qui perd de la vie chez elle (A2, ``synchro/coups_fantome.py``). Quand le
fantôme s'éloigne, le monstre revient vers le Link local. Un BOSS, lui, ne fait que « se détourner » vers le
fantôme quelques secondes (alternance) : ses coups sur le fantôme ne blessent personne, parce que chacun combat
son propre boss (A9) et qu'un double coup serait injuste.

Comment (pack v19, bit 1 des capacités, ``Mailbox.ghost_aggro``) : le PC écrit une table de 16 entrées
{id de l'ennemi, id du fantôme}. Pendant l'execute de CET ennemi seulement (``wwmp_aggro_filter``, accroché sur
les deux appels de ``fpcMtd_Execute`` de ``fopAc_Execute``), le jeu met dans le VRAI Link la position du
fantôme (old.pos, current.pos, eyePos), appelle l'execute, puis remet les valeurs de Link. Aucun pointeur ne
change : les méthodes du joueur agissent sur le vrai Link, comme sans le mod. Le jeu refuse lui-même l'échange
pendant un événement, si Medli ou Makar est contrôlée, si le fantôme n'existe plus ou si son TG (A1) n'est pas
prêt. Bit 4 des capacités (``Mailbox.aggro_all``, zone ``wwmp_aggro2`` en fin de pack) : bitmap des espèces
permises (``procs_pack()``) et GARDES évaluées par le jeu à chaque image avant l'échange (``GARDES``) : état de
l'ennemi (Moblin « prison », Morth collé, ReDead qui crie ou saisit…) et distance minimale au fantôme. Sans le
bit 4 (pack v19 plus ancien) : les 4 espèces d'origine seulement (mode dégradé).

Ce que le PC décide (``AggroSync.choose``) :

* seulement chez l'autorité de la zone, hors cinématique, avec le TG des fantômes allumé (A1) ;
* espèces relues dans le décompilé et l'asm HD (``ESPECES``) avec leurs filtres de type ; jamais un boss par
  ce chemin (``boss.py``) ; les boss passent par ``BOSS_GROUPES`` (règle de l'arène et alternance) ;
* Forteresse Maudite : plus exclue en bloc. Seul le Moblin à lanterne (type 1) reste exclu tant que la
  sauvegarde n'a pas d'épée depuis ``EPEE_HOLD`` s (``rouya_mode`` du jeu : sans épée, un Moblin à lanterne qui
  « voit » le fantôme enverrait le Link LOCAL en cellule) ; le pack refuse aussi l'échange pendant tout état
  « prison » du Moblin ; le projecteur (``Search``, proc 66) n'est jamais visé ;
* le joueur le plus proche (distance 3D) parmi le Link local et les fantômes de la zone (joueur pas en
  cinématique, en vie, position connue) ; hystérésis : on ne change que pour un joueur 30 % plus près et si la
  cible tient depuis 2 s, ou tout de suite si la cible disparaît ; le Link local reprend toujours la main à
  moins de ``MELEE`` unités (300 pour le Darknut) ;
* au plus 16 ennemis visent un fantôme : les boss d'abord, puis les plus proches de leur fantôme.
"""
from __future__ import annotations

import math
import struct
import time

from synchro import boss

HYST = 0.30             # un autre joueur doit être 30 % plus près…
HOLD = 2.0              # … et la cible tenir depuis 2 s
MELEE = 250.0           # unités : Link local à cette distance = toujours la cible
RANGE_MAX = 4000.0      # unités : un fantôme plus loin n'est jamais choisi
N_MAX = 16              # entrées de la table du pack (Mailbox.N_AGGRO)
CFG_REFRESH = 5.0       # s : table réécrite même sans changement (jeu relancé)
EYE_DY = 100.0          # unités : hauteur des yeux par défaut (Link : eyePos.y - pos.y, relu en jeu si possible)
EYE_DY_MIN, EYE_DY_MAX = 20.0, 250.0
EPEE_HOLD = 3.0         # s : épée vue dans la sauvegarde sans interruption avant d'ouvrir le Moblin à lanterne

# Forteresse Maudite : stages connues ([DECOMP] + chaînes .rodata HD). Gardée pour le mode Hanteurs
# (invocation et possession y restent interdites) ; A3 ne l'utilise plus (filtre précis du Moblin à lanterne).
STAGES_FORTERESSE = frozenset({"MajyuE", "majroom", "ma2room", "ma3room", "Mjtower"})
STAGES_EXCLUES = STAGES_FORTERESSE | boss.BOSS_STAGES          # Hanteurs (inchangé)
STAGES_EXCLUES_A3 = boss.BOSS_STAGES                           # ennemis ordinaires : jamais dans une stage de boss


# --- filtres de type (paramètres de l'acteur) ------------------------------------------------------------
def _bk(params: int, epee: bool) -> str | None:
    """Bokoblin (d_a_bk.cpp) : type = params & 0xF. 5 porté par un Kargaroc, 8 générateur (waki_set),
    0xA scripté (demo_camera, statut BOSS). Aucun chemin « prison » (pas de setNextStage dans d_a_bk)."""
    t = params & 0xF
    return {5: "porté", 8: "générateur", 0xA: "scripté"}.get(t)


def _mo2(params: int, epee: bool) -> str | None:
    """Moblin (d_a_mo2.cpp) : mode = params & 0xFF. 5 porté, 0xF statue figée ; 1 lanterne (Forteresse) exclu
    tant que la sauvegarde n'a pas d'épée (``rouya_mode`` = !isCollect(0, 0..3), recalculé à chaque image par le
    Moblin, l.3468 : sans épée, NAGE -10 -> E3_DEMO -> setNextStage("majroom") sur le Link LOCAL)."""
    t = params & 0xFF
    if t == 1 and not epee:
        return "lanterne sans épée (prison)"
    return {5: "porté", 0xF: "statue"}.get(t)


def _cc(params: int, epee: bool) -> str | None:
    """Chuchu (d_a_cc.cpp) : comportement = params & 0xFF ; 2 = scène (deku_come_demo, événements)."""
    return "scène" if params & 0xFF == 2 else None


def _gm(params: int, epee: bool) -> str | None:
    """Mothula (d_a_gm, non décompilé ; RPX : create 0x02150844 range (params >> 8) & 0xFF dans +0x3E9, 0xFF ramené
    à 0 ; execute 0x0214D1A4 : +0x3E9 != 0 = une AILE coupée, acteur à part du même proc, qui tombe et disparaît)."""
    return "aile coupée" if (params >> 8) & 0xFF not in (0, 0xFF) else None


def _aucun(params: int, epee: bool) -> str | None:
    """Aucun type à exclure (lectures seules du joueur hors événement, voir l'étude)."""
    return None


# proc -> (nom, filtre, expérimental). Procs : table des noms d'acteurs du RPX (0x101EBFDC, vérifiée par
# test_pack_mailbox.TestRpxToutesEspeces) ; procs dérivés (sans nom) : étude sync3_aggro_tous.md.
# Les 4 premières : liste d'origine du pack (wwmp_aggro +0x0C, Mailbox.AGGRO_PROCS), seules permises par un pack
# v19 sans le bit 4.
SPECIES = {189: ("Bokoblin", _bk), 188: ("Moblin", _mo2), 206: ("Chuchu", _cc), 215: ("Keese", _aucun)}
ESPECES_ETENDUES = {
    191: ("Darknut", _aucun),           # d_a_tn : lectures (garde, coupe) ; MELEE 300
    190: ("Stalfos", _aucun),           # d_a_st : corps et tête (même proc)
    181: ("Kargaroc", _aucun),          # d_a_bb : porte des Bokoblins, pas Link
    202: ("Armos Knight", _aucun),      # d_a_am  (avale les bombes, casse les piliers : pilotage.PREUVES_ARMOS)
    203: ("Armos", _aucun),             # d_a_am2
    216: ("Magtail", _aucun),           # non décompilé : HD, checkPlayerGuard seul [à voir en jeu]
    208: ("Wizzrobe", _aucun),          # non décompilé : démo du mini-boss dans kazeMB (stage exclue)
    209: ("Peahat / Seahat", _aucun),   # non décompilé : HD, rien sur le joueur [à voir en jeu]
    227: ("Octorok", _aucun),           # non décompilé : HD, rien [à voir en jeu]
    247: ("Miniblin", _aucun),          # non décompilé : HD, garde lue seulement [à voir en jeu]
    198: ("Rat", _aucun),               # non décompilé : vol de rubis à la collision avec le vrai Link
    207: ("Bubble", _aucun),            # non décompilé : malédiction par la collision (A2)
    228: ("Gyorg", _aucun),             # non décompilé : vise surtout le bateau (position non échangée)
    205: ("Morth", _aucun),             # d_a_ks : garde du pack, jamais collé (mAction +0x547 == 4). SANS EFFET
                                        # aujourd'hui : son profil est du groupe 3 (0x101B8958 +0x2C), or le relais
                                        # et A3 ne prennent que le groupe 2 ; lignes gardées car la table de gardes
                                        # est recopiée à l'identique dans le pack (les retirer changerait l'asm)
    224: ("ReDead", _aucun),            # d_a_rd : gardes du pack (modes 0/4/8, fantôme hors d'un carré de 700 u)
}
# Espèces qui SAISISSENT le joueur : ouvertes par --aggro-experimental, ou par défaut quand le bouclier de la saisie
# transmise est armé (pack bit 9 : leurs appels aux méthodes de Link sont bloqués, ses drapeaux remis).
ESPECES_EXPERIMENTALES = {
    119: ("Floormaster", _aucun),       # d_a_fm : saisie (setOutPower hors événement) ; gardes modes 3/7 + 800 u
    212: ("Poe", _aucun),               # d_a_pw (non décompilé) : possession ; garde action +0x484 != 0x51
    204: ("Mothula", _gm),              # d_a_gm (non décompilé) : souffle d'ailes, démo du mini-boss ; ailes
                                        # coupées (acteurs du même proc) refusées
    214: ("Boko Baba", _aucun),         # d_a_bo (non décompilé) : morsure, setDamagePoint direct
    252: ("Dexivine", _aucun),          # d_a_sss : setOutPower hors événement (lianes du Bois Défendu)
    251: ("Ss", _aucun),                # d_a_ss (non décompilé, rôle inconnu)
}
ESPECES = {**SPECIES, **ESPECES_ETENDUES, **ESPECES_EXPERIMENTALES}

# jamais visés, même en expérimental (pas des monstres, ou tout est scripté) : documentation et tests
NOT_SPECIES = {66: "projecteur de la Forteresse (Search : prison de son propre PC)", 220: "cochon (Pig)",
               194: "mouette", 167: "Helmaroc PNJ (Dk, scripté)", 213: "Baba Bud", 239: "décor de Helmaroc",
               219: "sable de Molgera", 226: "yeux du Grand Octo (ne visent personne)",
               237: "tentacules du sol de Kalle Demos (ne lisent jamais le joueur)", 222: "Valoo", 223: "Valoo (Dr2)"}

MELEE_ESPECE = {191: 300.0}            # Darknut : le Link local reprend la main avant la portée de parade
STAGES_ESPECE = {208: frozenset({"kazeMB"})}   # Wizzrobe : démo du mini-boss (setPlayerPosAndAngle)
# distance minimale (carré XZ, comme la garde du pack) entre l'ennemi et le fantôme visé : Python ne garde pas
# un ennemi inutile dans la table ; le pack refuse de toute façon en dessous de sa propre garde (700 / 800)
DIST_MIN_FANTOME = {224: 750.0, 119: 850.0}

# --- boss : groupes (toutes les parties d'un boss visent la même cible) ------------------------------------
# nom -> (procs dans l'ordre du « chef », rayon de l'arène, y minimal du Link local ou None, expérimental)
BOSS_GROUPES = {
    "gohma": ((234,), 4000.0, None, False),                    # Btd : écritures dans Link seulement en démo
    "gohdan": ((240,), 4000.0, None, False),                   # Bst : tête et deux mains
    "helmaroc": ((238,), 5000.0, 9800.0, False),               # Bdk : combat lancé à y >= 9800 (start)
    "ganon_spectral": ((241, 242), 5000.0, None, False),       # Fganon et ses boules (Fgmahou)
    "ganon_marionnette": ((243, 244, 245), 5000.0, None, False),   # Bgn, Bgn2, Bgn3
    "kalle_demos": ((235, 236), 4000.0, None, True),           # Bkm mange le Link local (eat, hors événement)
    "molgera": ((217, 218), 5000.0, None, True),               # Bwd mange / aspire ; Bwds larves
    "jalhalla": ((211,), 4000.0, None, True),                  # big_pow : setOutPower (aspiration, souffle)
    "grand_octo": ((225,), 6000.0, None, True),                # Daiocta : aspire le bateau local
    "ganondorf": ((246,), 4000.0, None, True),                 # Gnd : duel scripté avec Zelda (non décompilé)
}
BOSS_PROC = {p: nom for nom, (procs, *_r) in BOSS_GROUPES.items() for p in procs}
# Boss « expérimentaux » ouverts par défaut sous le bouclier de la saisie transmise : leur danger relu (setOutPower,
# drapeaux et mode de démo de Link écrits hors événement) passe entièrement par le bouclier. Les autres restent
# derrière --aggro-experimental, preuves dans docs/etudes/sync3_aggro_tous.md §8 : Molgera (sa morsure mange le
# Link local dès que son AT touche le TG d'un fantôme, même sans échange ; appel direct 0x020FB14C -> 0x024435FC,
# hors bouclier), Grand Octo (il aspire le BATEAU local, qui n'est pas Link), Ganondorf (setNextStage en
# 0x02157348 et duel scripté, non décompilé : rien ne peut être bloqué sans risquer de figer le combat).
BOSS_SAISIE = frozenset({"kalle_demos", "jalhalla"})
ARENA_DELAY = 8.0       # s : Link local dans l'arène, hors cinématique, avant que le boss puisse se détourner
BOSS_PERIOD = 15.0      # s : période de l'alternance…
BOSS_ON = 6.0           # … dont 6 s où le boss vise le fantôme
BOSS_MELEE = 500.0      # u : Link local à moins de cette distance du chef = le boss le vise lui
LARVE_LOCAL = 1500.0    # u : larve de Molgera (218) : seulement loin du Link local (portée du grappin)


# Réveil (relecture « jeu ») : certains boss se réveillent quand LE JOUEUR entre dans une zone, et leur intro
# téléporte alors le joueur (setPlayerPosAndAngle). Pendant un échange, ce « joueur » est le FANTÔME : le boss se
# réveillerait d'après l'autre joueur et l'intro emporterait le Link LOCAL. Les 8 s dans l'arène ne le garantissent
# pas (Link local resté près de la porte, l'autre joueur déjà au centre). Un groupe de cette table ne vise donc
# jamais le fantôme tant que le Link LOCAL n'est pas entré lui-même dans la zone de réveil depuis l'arrivée dans
# la zone (stage + salle ; ``reset(zone=True)``). Sans les données nécessaires (home, params) : jamais réveillé.
def _eveil_gohdan(lp, membres) -> bool:
    """Gohdan (d_a_bst l.1806) : ``player->current.pos.abs() < 700`` (origine de la salle), intro l.2121."""
    return math.sqrt(lp[0] * lp[0] + lp[1] * lp[1] + lp[2] * lp[2]) < 700.0


def _eveil_fganon(lp, membres) -> bool:
    """Ganon spectral (d_a_fganon l.1078 standby, l.1119 start) : distance XZ à home < (params >> 8 & 0xFF) × 10 ;
    intro l.1686. Seul l'acteur 241 porte la règle (les boules n'ont pas de réveil)."""
    for m in membres:
        if m[0] != 241:
            continue
        home, params = _pos(m[3].get("home")), m[3].get("params")
        if home is None or not isinstance(params, int) or isinstance(params, bool):
            continue
        r = ((params >> 8) & 0xFF) * 10.0
        if math.hypot(lp[0] - home[0], lp[2] - home[2]) < r:
            return True
    return False


EVEIL_BOSS = {"gohdan": _eveil_gohdan, "ganon_spectral": _eveil_fganon}

# Molgera (eat_attack, d_a_bwd.cpp l.702-705) : une sphère du corps qui touche QUOI QUE CE SOIT fait « manger »
# le Link local. Le TG des fantômes (A1) reste donc éteint dans ses stages, sauf --aggro-experimental. Revanche de la
# Tour de Ganon : Xboss2 d'après la recherche, Xboss3 si l'ordre suit les donjons [HYPO] -> les deux.
STAGES_TG_INTERDIT = frozenset({"kazeB", "Xboss2", "Xboss3"})

# --- gardes du pack (bit 4 des capacités, wwmp_aggro2_gardes) ----------------------------------------------
# (proc, décalage dans l'acteur, opération, valeur) ; recopiées à l'identique dans l'asm (test_pack_mailbox).
G_EQ8, G_EQ16, G_NZ8, G_NZ16, G_MASK32, G_NEAR = 1, 2, 3, 4, 5, 6
N_GARDES = 16
GARDES = (
    # Moblin : jamais pendant une alerte « prison » ni une démo (offsets HD vérifiés dans le RPX)
    (188, 0x8C2, G_EQ16, 6),        # dr.mAction == NAGE (alerte)
    (188, 0x8C2, G_EQ16, 40),       # dr.mAction == E3_DEMO (sth 0x28, 0x021C9F40)
    (188, 0x8C2, G_EQ16, 30),       # CARRY
    (188, 0x8C2, G_EQ16, 31),       # CARRY
    (188, 0x2E4C, G_NZ16, 0),       # m2A48 (alerte en cours)
    (188, 0x2E4E, G_NZ8, 0),        # m2A4A
    (188, 0x2E4F, G_NZ8, 0),        # m2A4B (cellule demandée : setNextStage("majroom"), 0x021C9DE0)
    (188, 0x2E50, G_NZ16, 0),       # m2A4C
    # ReDead : seulement en WAIT (0), MOVE (4), RETURN (8) ; fantôme hors d'un carré de 700 u (cri : 500)
    (224, 0x3C8, G_MASK32, 0x0111),
    (224, 0, G_NEAR, 700),
    # Morth : jamais collé à Link (action_omoi, mAction == 4 ; stb 4, 0x547 en 0x021A74A4). Entrée morte tant que
    # le Morth (groupe 3) n'est pas relayé : gardée pour que la table reste identique à celle du pack v19
    (205, 0x547, G_EQ8, 4),
    # Poe (expérimental) : jamais en possession (action 0x51, lha 0x484 en 0x0244DDDC)
    (212, 0x484, G_EQ16, 0x51),
    # Floormaster (expérimental) : seulement en PATH_MOVE (3) ou WAIT (7) ; fantôme hors d'un carré de 800 u
    (119, 0x3C8, G_MASK32, 0x0088),
    (119, 0, G_NEAR, 800),
)


def procs_pack() -> frozenset[int]:
    """Espèces du bitmap du pack (``wwmp_aggro2_procs``) : tout ce que Python peut un jour mettre dans la table
    (espèces de toutes catégories et boss) ; c'est Python qui choisit, le pack ne fait que refuser le reste."""
    return frozenset(ESPECES) | frozenset(BOSS_PROC)


def bitmap_pack() -> bytes:
    """64 octets : bit 0x80 >> (n & 7) de l'octet n >> 3 pour chaque proc permis (comme wwmp_give_ok)."""
    b = bytearray(64)
    for n in procs_pack():
        b[n >> 3] |= 0x80 >> (n & 7)
    return bytes(b)


def gardes_pack() -> bytes:
    """Table des gardes du pack : ``N_GARDES`` × 8 octets {proc s16, décalage u16, opération u8, 0, valeur u16}."""
    raw = b"".join(struct.pack(">hHBBH", p, off, op, 0, val) for p, off, op, val in GARDES)
    return raw + bytes(8 * N_GARDES - len(raw))


def espece_permise(proc, etendu: bool = True, experimental: bool = False) -> bool:
    if not isinstance(proc, int) or isinstance(proc, bool):
        return False
    if proc in SPECIES:
        return True
    if not etendu:
        return False
    return proc in ESPECES_ETENDUES or (experimental and proc in ESPECES_EXPERIMENTALES)


def eligible(proc, params, stage: str | None, epee: bool = False, etendu: bool = True,
             experimental: bool = False) -> bool:
    """Ennemi ordinaire (pas un boss) qui peut viser un fantôme. ``epee`` : la sauvegarde a une épée (tenue depuis
    ``EPEE_HOLD`` s) ; ``etendu`` : le pack a le bit 4 (sinon les 4 espèces d'origine) ; ``experimental`` :
    --aggro-experimental."""
    if not espece_permise(proc, etendu, experimental) or boss.is_boss(proc, stage):
        return False
    if stage is not None and (stage in STAGES_EXCLUES_A3 or stage in STAGES_ESPECE.get(proc, ())):
        return False
    try:
        return ESPECES[proc][1](int(params) & 0xFFFFFFFF, bool(epee)) is None
    except (TypeError, ValueError):
        return False


def tg_permis(stage: str | None, experimental: bool = False) -> bool:
    """TG des fantômes (A1) permis dans cette stage (éteint contre Molgera, sauf --aggro-experimental)."""
    return experimental or stage is None or stage not in STAGES_TG_INTERDIT


def lire_epee(mem) -> bool | None:
    """Épée dans la sauvegarde (mCollect[0] bits 0-3 = dComIfGs_isCollect(0, 0..3), gameInfo +0xD4) ; None si
    illisible. Lecture seule."""
    try:
        raw = mem.try_read(0x101F84DC, 4)
        if not raw:
            return None
        gi = struct.unpack(">I", raw)[0]
        if not gi:
            return None
        b = mem.try_read(gi + 0xD4, 1)
        return None if not b else bool(b[0] & 0x0F)
    except Exception:
        return None


def _pos(v) -> tuple[float, float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    return p if all(math.isfinite(x) and abs(x) < 1e6 for x in p) else None


def _carre_xz(a, b) -> float:
    return max(abs(a[0] - b[0]), abs(a[2] - b[2]))


def eye_dy(link_y, eye_y) -> float:
    """Hauteur des yeux de Link (eyePos.y - pos.y) si elle est plausible, sinon ``EYE_DY``."""
    try:
        d = float(eye_y) - float(link_y)
    except (TypeError, ValueError):
        return EYE_DY
    return d if math.isfinite(d) and EYE_DY_MIN <= d <= EYE_DY_MAX else EYE_DY


def _pid_ok(pid) -> bool:
    return isinstance(pid, int) and not isinstance(pid, bool) and 0 < pid < 0xFFFFFFFF


class AggroSync:
    def __init__(self, enabled: bool = False, clock=time.monotonic, boss: bool = False, experimental: bool = False):
        self.enabled = bool(enabled)      # option (--share-ghost-aggro / --coop-donjon, sauf --no-ghost-aggro)
        self.active = True                # bascule à chaud (fichier de contrôle) ; sans effet si pas enabled
        self.boss = bool(boss)            # --share-boss-aggro (par --coop-donjon), sauf --no-boss-aggro
        self.experimental = bool(experimental)   # --aggro-experimental (désactivée par défaut)
        self.saisie = False               # bouclier de la saisie transmise armé (relu à chaque tick par le client)
        self.etendu = False               # pack avec le bit 4 (relu à chaque tick par le client)
        self.clock = clock
        self.cur: dict[int, list] = {}    # id de l'ennemi -> [cible (0 = Link local, sinon id du joueur), depuis]
        self.arena: dict[str, float] = {}   # groupe de boss -> instant d'entrée du Link local dans l'arène
        self.eveil: set[str] = set()      # groupes (EVEIL_BOSS) réveillés par le Link LOCAL dans cette zone
        self.epee_since: float | None = None
        self.written: tuple | None = None   # dernière table écrite (None = jamais, () = coupé)
        self.written_t = -1e9
        self.zone: str | None = None
        self.swaps = self.refused = 0     # compteurs du jeu (affichage)
        self.gardes = 0                   # refus par garde d'espèce (bit 4)
        self.chasing = 0                  # ennemis qui visent un fantôme (dernière table)
        self.boss_chasing = 0
        self._notes: list[str] = []

    def take_notes(self) -> list[str]:
        n, self._notes = self._notes, []
        return n

    @staticmethod
    def want(enabled: bool, active: bool, supported: bool, authority: bool, busy: bool, tg_on: bool,
             others_here: bool) -> bool:
        """Table des cibles active : option + bascule, pack capable, autorité de la zone, hors cinématique, TG des
        fantômes allumé (sans lui le jeu refuse l'échange), au moins un autre joueur dans la zone."""
        return bool(enabled and active and supported and authority and not busy and tg_on and others_here)

    def reset(self, zone: bool = False) -> None:
        """Cinématique, table coupée : cibles oubliées, arène recomptée. ``zone`` : autre zone (stage + salle), le
        réveil des boss (``EVEIL_BOSS``) est oublié aussi (une cinématique, elle, le garde : c'est souvent l'intro)."""
        self.cur.clear()
        self.arena.clear()               # cinématique (intro du boss), autre zone : l'arène se recompte
        if zone:
            self.eveil.clear()
        self.chasing = self.boss_chasing = 0

    def epee_ok(self, epee: bool | None, now: float) -> bool:
        """Épée vue sans interruption depuis ``EPEE_HOLD`` s (illisible = pas d'épée)."""
        if epee is not True:
            self.epee_since = None
            return False
        if self.epee_since is None:
            self.epee_since = now
        return now - self.epee_since >= EPEE_HOLD

    def _boss_table(self, bosses, lp, cand: dict, gpid: dict, now: float) -> list[tuple[float, int, int]]:
        """Boss : même cible pour tout un groupe ; seulement quand le Link local est dans l'arène depuis
        ``ARENA_DELAY`` s (intro passée : une cinématique remet le compte à zéro), pendant ``BOSS_ON`` s toutes
        les ``BOSS_PERIOD`` s, et si le Link local n'est pas au corps à corps du chef."""
        if not (self.boss and self.etendu):
            self.arena.clear()
            return []
        groupes: dict[str, list] = {}
        for b in bosses or ():
            proc, pid, p = b.get("proc"), b.get("pid"), _pos(b.get("pos"))
            nom = BOSS_PROC.get(proc) if isinstance(proc, int) and not isinstance(proc, bool) else None
            if nom is None or not _pid_ok(pid) or p is None:
                continue
            if BOSS_GROUPES[nom][3] and not (self.experimental or (self.saisie and nom in BOSS_SAISIE)):
                continue
            groupes.setdefault(nom, []).append((proc, pid, p, b))
        for nom in list(self.arena):
            if nom not in groupes:
                del self.arena[nom]
        out: list[tuple[float, int, int]] = []
        for nom, membres in groupes.items():
            procs, rayon, y_min, _exp = BOSS_GROUPES[nom]
            chef = min(membres, key=lambda m: (procs.index(m[0]), m[1]))
            if lp is None:
                self.arena.pop(nom, None)
                continue
            eveil = EVEIL_BOSS.get(nom)
            if eveil is not None and nom not in self.eveil:
                if not eveil(lp, membres):
                    continue                 # réveil pas encore fait par le Link local : le fantôme le ferait
                self.eveil.add(nom)
            dl = math.dist(chef[2], lp)
            if dl > rayon or (y_min is not None and lp[1] < y_min):
                self.arena.pop(nom, None)
                continue
            t0 = self.arena.setdefault(nom, now)
            t = now - t0 - ARENA_DELAY
            if t < 0 or t % BOSS_PERIOD >= BOSS_ON or dl <= BOSS_MELEE:
                continue
            best = min(((math.dist(chef[2], p), player) for player, p in cand.items()
                        if math.dist(chef[2], p) <= RANGE_MAX), default=None)
            if best is None:
                continue
            for proc, pid, p, _b in membres:
                if proc == 218 and math.dist(p, lp) <= LARVE_LOCAL:
                    continue
                out.append((best[0], pid, gpid[best[1]]))
        return out

    def choose(self, enemies: list[dict], local_pos, ghosts: dict[int, dict], now: float,
               stage: str | None = None, force: dict[int, int] | None = None, bosses: list[dict] | None = None,
               epee: bool | None = None) -> list[tuple[int, int]]:
        """Table des cibles. ``enemies`` : [{pid, proc, params, pos}] (ennemis non marionnettes de la zone) ;
        ``local_pos`` : position du Link local ; ``ghosts`` : id du joueur -> {"pid": id du fantôme, "pos"}
        (seulement les joueurs visables : même zone, pas en cinématique, en vie). ``bosses`` : [{pid, proc, pos}]
        (parties de boss de la zone, même filtrées par A9) ; ``epee`` : épée dans la sauvegarde (lue par le
        client). Renvoie [(id de l'ennemi, id du fantôme)], au plus ``N_MAX``, les boss d'abord, puis les plus
        proches de leur fantôme. ``force`` (mode Hanteurs, laisse) : id de l'ennemi -> joueur visé (0 = Link
        local), appliqué tout de suite (sans hystérésis) s'il est visable ; le Link local garde la main de près."""
        lp = _pos(local_pos)
        epee_ok = self.epee_ok(epee, now)
        cand: dict[int, tuple[float, float, float]] = {}
        gpid: dict[int, int] = {}
        for player, g in (ghosts or {}).items():
            p, pid = _pos(g.get("pos")), g.get("pid")
            if p is not None and _pid_ok(pid) and isinstance(player, int) and player != 0:
                cand[player], gpid[player] = p, pid
        seen: set[int] = set()
        out: list[tuple[float, int, int]] = []
        for e in enemies or ():
            pid = e.get("pid")
            ep = _pos(e.get("pos"))
            proc = e.get("proc")
            if (not _pid_ok(pid) or pid in seen or ep is None
                    or not eligible(proc, e.get("params", 0), stage, epee_ok, self.etendu,
                                    self.experimental or self.saisie)):
                continue
            seen.add(pid)
            d = {0: math.dist(ep, lp) if lp is not None else math.inf}
            dmin = DIST_MIN_FANTOME.get(proc, 0.0)
            for player, p in cand.items():
                dg = math.dist(ep, p)
                if dg <= RANGE_MAX and _carre_xz(ep, p) >= dmin:
                    d[player] = dg
            target, since = self.cur.get(pid, [0, -1e9])
            if target not in d:
                target, since = 0, now            # le fantôme visé a disparu : retour au Link local tout de suite
            best = min(d, key=lambda k: (d[k], k))
            voulu = (force or {}).get(pid)
            if d[0] <= MELEE_ESPECE.get(proc, MELEE):
                if target != 0:
                    target, since = 0, now
            elif isinstance(voulu, int) and voulu in d:
                if target != voulu:
                    target, since = voulu, now
            elif best != target and d[best] < (1.0 - HYST) * d[target] and now - since >= HOLD:
                target, since = best, now
            self.cur[pid] = [target, since]
            if target != 0:
                out.append((d[target], pid, gpid[target]))
        for pid in list(self.cur):
            if pid not in seen:
                del self.cur[pid]
        bt = [x for x in self._boss_table(bosses, lp, cand, gpid, now) if x[1] not in seen]
        bt.sort()
        out.sort()
        table: list[tuple[int, int]] = []
        pris: set[int] = set()
        for _d, pid, g in bt + out:
            if pid in pris or len(table) >= N_MAX:
                continue
            pris.add(pid)
            table.append((pid, g))
        self.boss_chasing = sum(1 for pid, _g in table if pid in {x[1] for x in bt})
        self.chasing = len(table)
        return table

    def sync(self, want: bool, table: list[tuple[int, int]], write, now: float) -> bool:
        """Écrit la table (``write(on, entrées)``) quand elle change, et toutes les ``CFG_REFRESH`` s.
        Coupé : écrit une fois (cfg à 0, table vidée) et oublie les cibles. Renvoie True si écrit."""
        key = tuple(table) if want else ()
        if not want:
            self.reset()
        if key == self.written and now - self.written_t < CFG_REFRESH:
            return False
        write(bool(want), list(table) if want else [])
        self.written, self.written_t = key, now
        return True

    def status(self) -> str:
        etat = "actif" if self.active else "coupé à chaud"
        txt = (f"monstres qui visent le fantôme : {etat}, {self.chasing} ennemi(s) sur un fantôme, "
               f"{self.swaps} échange(s), {self.refused} refus du jeu")
        if self.etendu:
            txt += f", {self.gardes} garde(s)"
            if self.boss:
                txt += f", {self.boss_chasing} partie(s) de boss"
        if self.saisie:
            txt += ", espèces qui saisissent ouvertes (bouclier)"
        return txt
