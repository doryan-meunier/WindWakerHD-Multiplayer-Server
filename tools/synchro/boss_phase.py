"""Phases communes des boss (2026-11-20, suite de la mort commune : synchro/boss_mort.py).

Ce que les joueurs voient : quand le boss devient vulnérable chez l'un (Kalle Demos tombe, une main de Gohdan
s'endort, sa tête tombe, Jalhalla se sépare, le masque du Roi Cuirassé se fend), il le devient chez TOUS au même
moment : tout le monde peut frapper ensemble, au lieu que chacun doive refaire la manœuvre chez soi.

Comment, sans rien inventer : chacun garde SON boss (filtre A9 inchangé). Le client de celui chez qui la phase
change l'annonce (message « bossp », un événement nommé) ; le serveur le relaie aux joueurs du groupe dans la même
stage ; chez eux le client écrit sur leur boss exactement ce que le jeu écrit lui-même à ce moment (relu dans le code
HD : tests/test_boss_phase.py, classe TestRpx), et le jeu déroule la phase. Jamais pendant un événement, un
chargement, ni depuis un état d'où le jeu ne sait pas lancer cette phase ; un événement reçu qui ne peut pas être
appliqué dans les ``EV_AGE`` secondes est abandonné (jamais en retard : la phase serait déjà finie chez l'autre).

Un événement appliqué chez nous n'est PAS réannoncé (pas d'écho : ``attendu``). Les compteurs (coups sur le masque)
voyagent comme une valeur (``val``) qui ne fait que monter, avec un rappel pour les arrivées tardives.

Messages (type « bossp ») : client -> serveur ``{group, stage, key, ev, val?}`` ; serveur -> autres membres du groupe
dans la stage ``{..., from, nom}``. Le serveur ne garde rien. Un ancien client ignore le message.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Callable

from synchro import boss_mort, boss_pv

EV_AGE = 5.0                # s : un événement reçu plus vieux n'est plus appliqué
PAS_ECRITURE = 0.7          # s entre deux écritures du MÊME événement sur le même boss
RAPPEL = 3.0                # s : rappel des valeurs (compteurs)
DEBIT = 4                   # messages par seconde au plus
EVS = ("chute", "main1", "main2", "tete", "separation", "masque", "phase", "langue", "repare") \
    + tuple(f"fil{i}" for i in range(8))
SYM_LINK = 0x10474BE4       # pointeur de l'acteur de Link (joueur 0)
OFF_POS = 0x314             # 3 f32 : position d'un acteur
FAIT, ATTENDRE, INUTILE = "fait", "attendre", "inutile"
_s16, _u8 = boss_mort._s16, boss_mort._u8


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def valide(msg, strict: bool = True) -> dict | None:
    """``strict`` (client) : le boss de la clé doit avoir cet événement dans NOS recettes. Le serveur relaie sans
    cette exigence (un serveur plus ancien que les clients relaie quand même leurs nouveaux événements connus)."""
    if not isinstance(msg, dict):
        return None
    stage, key, ev = msg.get("stage"), msg.get("key"), msg.get("ev")
    if not isinstance(stage, str) or not 0 < len(stage) <= 8 or not isinstance(key, str) \
            or not 0 < len(key) <= boss_pv.KEY_MAX or ev not in EVS:
        return None
    proc = boss_pv.key_proc(key)
    if proc not in boss_pv.BOSS_PV or (strict and (proc not in PHASES or ev not in PHASES[proc].evs)):
        return None
    out = {"stage": stage, "key": key, "ev": ev}
    if "val" in msg:
        if not _int(msg["val"]) or not 0 <= msg["val"] <= 255:
            return None
        out["val"] = msg["val"]
    return out


# ---------------------------------------------------------------------------
# Roi Cuirassé (238) : les coups de marteau sur le masque [DECOMP d_a_bdk.cpp damage_check ; HD 0x02066E7C]
# m8F8 (u8 +0xA08) compte les coups (0 à 3) ; au suivant le jeu lance lui-même le bris (4). Le compteur est commun :
# il ne fait que monter.
# LE BRIS (valeur 4) est commun aussi : chez un autre joueur le masque est brisé par la scène du jeu, avec ses
# écritures au 4e coup (+0xA08 = 3, action 9, sous-état 0 : 0x02066E9C..EA8), mais SEULEMENT quand son Roi Cuirassé
# est posé (attente au sol, bond, bec planté ; hauteur sous 9810, +0x2682 != 0) : la scène ne déplace pas le boss et
# tient Link à côté de sa tête ; écrite sur un boss en vol, elle tiendrait Link dans le vide. En vol : on attend qu'il
# se pose (la valeur reste en attente).
# ---------------------------------------------------------------------------
RC_MASQUE_MAX = 3
RC_POSE = 0x2682            # s8 m2586 : != 0 = au sol
RC_ACTION_BRIS = 9          # KAMEN_DEMO
RC_ACTIONS_POSE = (3, 4, 5)


def roi_lire(mem, b, bosses) -> dict | None:
    m = _u8(mem, b["base"] + boss_mort.RC_MASQUE)
    return None if m is None or m > 4 else {"masque": m}


def roi_evenements(avant: dict, e: dict) -> list:
    return []


def roi_valeurs(e: dict) -> dict:
    return {"masque": min(e["masque"], boss_mort.RC_MASQUE_BRISE)} if e["masque"] else {}


def roi_appliquer(mem, b, bosses, ev: str, val: int | None) -> str:
    if ev != "masque" or val is None:
        return INUTILE
    base = b["base"]
    m = _u8(mem, base + boss_mort.RC_MASQUE)
    val = min(val, boss_mort.RC_MASQUE_BRISE)
    if m is None or m >= val:
        return INUTILE
    if boss_mort.roi_etat(mem, b, bosses) != boss_mort.VIVANT:
        return ATTENDRE
    if val <= RC_MASQUE_MAX:
        mem.write(base + boss_mort.RC_MASQUE, bytes([val]))
        return FAIT
    # le bris : seulement posé
    action, pose, y = _s16(mem, base + boss_mort.RC_ACTION), _u8(mem, base + RC_POSE), \
        mem.try_read(base + boss_mort.RC_POS_Y, 4)
    if action is None or pose is None or y is None or len(y) != 4:
        return ATTENDRE
    y = struct.unpack(">f", y)[0]
    if action not in RC_ACTIONS_POSE or pose == 0 or not y < boss_mort.RC_HAUT:
        return ATTENDRE
    mem.write(base + boss_mort.RC_MASQUE, bytes([RC_MASQUE_MAX]))
    mem.write(base + boss_mort.RC_ACTION, struct.pack(">hh", RC_ACTION_BRIS, 0))
    return FAIT


# ---------------------------------------------------------------------------
# Kalle Demos (235) : la chute [DECOMP d_a_bmd.cpp damage_check, fondu dans move en HD : 0x020B3B1C..0x020B3B40]
# Toutes les lianes coupées (m331 == 0), le jeu écrit mMode = 3, m302 = 0 : il tombe, s'ouvre (cœur à découvert),
# se referme et remonte. Chez les autres : m331 = 0 puis les deux mêmes écritures. Les lianes encore attachées se
# détachent toutes seules (elles lisent m332 == 3, que pose la chute) et se rattachent à la fin, comme dans le jeu.
# Une liane en repousse peut refaire m331 = 1 dans les premières images : il est tenu à 0 tant que la chute commence
# (mMode 3, sous-état 0 ou 1), jamais après.
# ---------------------------------------------------------------------------
KD_LIANES = 0x451           # s8 m331 : lianes attachées
KD_MODE_TOMBE = 3
KD_MODES_CHUTE = (0, 1, 2)  # attente, attaques ; jamais tombé (3), Link avalé (5), introduction (10), fin (0xB)


def kalle_lire(mem, b, bosses) -> dict | None:
    mode, sous = _s16(mem, b["base"] + boss_mort.KD_MODE), _s16(mem, b["base"] + boss_mort.KD_SOUS)
    return None if mode is None or sous is None else {"mode": mode, "sous": sous}


def kalle_evenements(avant: dict, e: dict) -> list:
    return ["chute"] if avant["mode"] in KD_MODES_CHUTE and e["mode"] == KD_MODE_TOMBE else []


def kalle_appliquer(mem, b, bosses, ev: str, val) -> str:
    mode = _s16(mem, b["base"] + boss_mort.KD_MODE)
    if ev != "chute" or mode is None or mode == KD_MODE_TOMBE or mode == boss_mort.KD_MODE_FIN:
        return INUTILE
    if mode not in KD_MODES_CHUTE:
        return ATTENDRE
    mem.write(b["base"] + KD_LIANES, b"\x00")
    mem.write(b["base"] + boss_mort.KD_MODE, struct.pack(">hh", KD_MODE_TOMBE, 0))
    return FAIT


def kalle_tenir(mem, b, e: dict) -> None:
    if e["mode"] == KD_MODE_TOMBE and e["sous"] in (0, 1) and _u8(mem, b["base"] + KD_LIANES) not in (0, None):
        mem.write(b["base"] + KD_LIANES, b"\x00")


# ---------------------------------------------------------------------------
# Gohdan (240) : une main s'endort, la tête tombe [DECOMP d_a_bst.cpp ; HD : main 0x020EC338..0x020EC36C,
# tête 0x020EA8CC..0x020EA8F0]
# * main : à 0 PV, 30 images après le coup, le jeu écrit action = 6, minuterie +0x1332 = 300, sous-état = 0 ; l'action 6
#   fait le reste (œil fermé, insensible, réveil à la fin de la minuterie, PV remis à 4). Les deux mains endormies, la
#   tête passe d'elle-même en phase 12 (elle ouvre les yeux) : jamais écrit par nous.
# * tête : ses deux yeux à 0, le jeu écrit sous-état = 0, action = 7 ; l'action 7 pose ses minuteries, tombe, ouvre
#   la bouche et attend la bombe. Si nos mains sont encore éveillées (phase 11), elles sont endormies d'abord, sinon
#   elles attaqueraient pendant que la tête est à terre.
# ---------------------------------------------------------------------------
GD_MINUTERIE = 0x1332       # s16 m10FC[1] : sommeil de la main (300 images)
GD_YEUX = 0x30A6            # deux s8 mEyeHealth : 2 chacun, 0 = crevé
GD_DORT, GD_A_TERRE, GD_SOMMEIL = 6, 7, 300
GD_MAIN_ACTIONS = (1, 5, 10, 11, 12, 13)    # vol, touchée, attaques ; jamais encastrée (0), endormie (6), fin (22)
GD_TETE_ACTIONS = (1, 5, 14)                # vol, touchée, rayon ; jamais à terre (7), qui se relève (8), 0, 20, 22


def _gohdan_main(mem, b, bosses, typ: int):
    return next((o for o in boss_mort._gohdan_mains(mem, b, bosses)
                 if _u8(mem, o["base"] + boss_mort.GD_TYPE) == typ), None)


def gohdan_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    if _u8(mem, base + boss_mort.GD_TYPE) != 0:
        return None
    e = {"tete": _s16(mem, base + boss_mort.GD_ACTION), "phase": _u8(mem, base + boss_mort.GD_PHASE),
         "scene": _u8(mem, base + boss_mort.GD_SCENE)}
    for typ in (1, 2):
        o = _gohdan_main(mem, b, bosses, typ)
        e[f"main{typ}"] = None if o is None else _s16(mem, o["base"] + boss_mort.GD_ACTION)
    return None if None in e.values() else e


def gohdan_evenements(avant: dict, e: dict) -> list:
    out = [f"main{t}" for t in (1, 2)
           if avant[f"main{t}"] in GD_MAIN_ACTIONS and e[f"main{t}"] == GD_DORT and avant["phase"] == 11]
    if avant["tete"] in GD_TETE_ACTIONS and e["tete"] == GD_A_TERRE:
        out.append("tete")
    return out


def _gohdan_endormir(mem, o) -> None:
    mem.write(o["base"] + boss_pv.OFF_HEALTH, b"\x00")
    mem.write(o["base"] + GD_MINUTERIE, struct.pack(">h", GD_SOMMEIL))
    mem.write(o["base"] + boss_mort.GD_SOUS, struct.pack(">h", 0))
    mem.write(o["base"] + boss_mort.GD_ACTION, struct.pack(">h", GD_DORT))


def gohdan_appliquer(mem, b, bosses, ev: str, val) -> str:
    e = gohdan_lire(mem, b, bosses)
    if e is None:
        return ATTENDRE
    if e["scene"] != 0 or e["phase"] not in boss_mort.GD_PHASES_COMBAT or e["tete"] in (0, 20):
        return ATTENDRE if e["tete"] != boss_mort.GD_ACTION_FIN else INUTILE
    mains = {t: _gohdan_main(mem, b, bosses, t) for t in (1, 2)}
    for o in mains.values():
        h = boss_pv.read_header(mem, o["base"])
        if h is None or h[0] != o["pid"] or h[1] != o["proc"]:
            return ATTENDRE
    if ev in ("main1", "main2"):
        t = int(ev[-1])
        if e[ev] == GD_DORT or e["phase"] != 11:
            return INUTILE
        if e[ev] not in GD_MAIN_ACTIONS:
            return ATTENDRE
        _gohdan_endormir(mem, mains[t])
        return FAIT
    if ev != "tete" or e["tete"] in (GD_A_TERRE, 8):
        return INUTILE
    if e["tete"] not in GD_TETE_ACTIONS:
        return ATTENDRE
    if e["phase"] == 11:
        if any(e[f"main{t}"] not in GD_MAIN_ACTIONS + (GD_DORT,) for t in (1, 2)):
            return ATTENDRE
        for t in (1, 2):
            if e[f"main{t}"] != GD_DORT:
                _gohdan_endormir(mem, mains[t])
    base = b["base"]
    mem.write(base + GD_YEUX, b"\x00\x00")
    mem.write(base + boss_mort.GD_SOUS, struct.pack(">h", 0))
    mem.write(base + boss_mort.GD_ACTION, struct.pack(">h", GD_A_TERRE))
    return FAIT


# ---------------------------------------------------------------------------
# Jalhalla (211) : la séparation en petits Poe [DECOMP d_a_bpw.cpp action_damage ; HD : lumière 0x020D6E40..6C,
# saut 0x020D816C, séparation 0x020D8510]
# Dans le jeu : la lumière le fige (action 3, états 0x50 à 0x53), Link le porte et le lance (0x54, 0x55), contre les
# piques il saute (0x64), retombe (0x65) et se sépare en autant de Poe que de PV. Un seul joueur le lance ; chez les
# autres, en deux temps, avec les écritures du jeu :
# 1. « touché par la lumière » : type de coup +0x4FB = 0xB, action 3, état 0x50 : le jeu le fige lui-même à l'image
#    suivante (opaque, lanterne lâchée, flammes éteintes, état 0x52). Le type de coup est remis à 0 par le jeu à
#    chaque image : s'il n'a pas pris, on recommence ;
# 2. une fois figé (0x52, 0x53) : état 0x64, le saut, qui finit seul par la séparation (Poe créés par le jeu).
# Jamais pendant que NOTRE Link le porte ou le lance (0x54, 0x55), ni déjà séparé (action 4), ni l'introduction.
# ---------------------------------------------------------------------------
JA_COUP = 0x4FB             # u8 mHitType : 0xB = flèche de lumière (10 : lumière du bouclier)
JA_TOUCHE, JA_E_LUMIERE, JA_E_SAUT, JA_E_RETOMBE = 3, 0x50, 0x64, 0x65
JA_E_FIGE = (0x52, 0x53)


def jalhalla_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    typ, action, etat = _u8(mem, base + boss_pv.OFF_JAL_TYPE), _s16(mem, base + boss_mort.JA_ACTION), \
        _s16(mem, base + boss_mort.JA_ETAT)
    return None if None in (typ, action, etat) or typ != 0 else {"action": action, "etat": etat}


def _ja_saute(e: dict) -> bool:
    return e["action"] == JA_TOUCHE and e["etat"] in (JA_E_SAUT, JA_E_RETOMBE)


def jalhalla_evenements(avant: dict, e: dict) -> list:
    return ["separation"] if _ja_saute(e) and not _ja_saute(avant) else []


def jalhalla_appliquer(mem, b, bosses, ev: str, val) -> str:
    e = jalhalla_lire(mem, b, bosses)
    if ev != "separation" or e is None:
        return INUTILE
    base, action, etat = b["base"], e["action"], e["etat"]
    if action == boss_mort.JA_SEPARATION or _ja_saute(e):
        return INUTILE
    if action in boss_mort.JA_ACTIONS_ENTIER:
        mem.write(base + JA_COUP, b"\x0b")
        mem.write(base + boss_mort.JA_ACTION, struct.pack(">hh", JA_TOUCHE, JA_E_LUMIERE))
        return ATTENDRE                                 # premier temps : le jeu le fige à l'image suivante
    if action == JA_TOUCHE and etat in (JA_E_LUMIERE, JA_E_LUMIERE + 1):
        mem.write(base + JA_COUP, b"\x0b")
        return ATTENDRE
    if action == JA_TOUCHE and etat in JA_E_FIGE:
        mem.write(base + boss_mort.JA_ETAT, struct.pack(">h", JA_E_SAUT))
        return FAIT
    return ATTENDRE


# ---------------------------------------------------------------------------
# Ganondorf (246) : les phases et la fin [HD seulement, le décompilé est vide : damage_check 0x021594B0, damage
# 0x02158F10, attack_last 0x02158134, finish 0x02158E80 ; relu instruction par instruction]
# Une seule valeur qui ne fait que monter, l'« avancée » du combat :
#   1 = Ganondorf assomme Zelda (dans le jeu : une flèche de lumière à 50 PV ou moins ; mode 4, puis phase 1) ;
#   2 = Zelda se relève, phase du bouclier miroir (25 PV ou moins en phase 1 ; phase 2) ;
#   3 = le coup final est donné (mode 30 : la scène de fin, qui place elle-même Link et charge la suite).
# Chez un joueur en retard, on rattrape UNE marche à la fois, hors cinématique, avec les écritures du jeu :
#   vers 1 : phase demandée +0x60D = 1, garde +0x172E = 0, mode 0xB, sous-état 0 : le jeu enchaîne seul (mode 4,
#            cinématique, phase +0x60C = 1 à la fin) ;
#   vers 2 : PV 25, phase +0x60C = 2, phase demandée 2, garde 0, mode 0xB, sous-état 0 : la cinématique vient après ;
#   vers 3 : seulement en phase 2 chez nous : garde 0, invulnérabilité +0x428 = 5, mode 0x1E, sous-état 0.
# Jamais pendant une cinématique (+0x18BE != 0), ni sur le Ganondorf de décor (+0x3D4 == 0).
# ---------------------------------------------------------------------------
GN_DECOR = 0x3D4            # u8 : 0 = Ganondorf de décor (pas le combat)
GN_MODE = 0x3EA             # s16 : 0 attente, 1 2 3 attaques, 4 il frappe Zelda, 5 coup final proposé, 10 parade,
                            # 11 touché/étourdi, 20 21 attente scénarisée, 30 fin
GN_SOUS = 0x3EC             # s16
GN_INVULN = 0x428           # s16
GN_PHASE = boss_pv.OFF_GND_PHASE    # s8 +0x60C : 0, 1, 2
GN_DEMANDE = 0x60D          # s8 : phase demandée (1 ou 2), consommée par le mode 0xB
GN_GARDE = 0x172E           # s8 : 0 ouvert, 1 pare
GN_CINE = 0x18BE            # s16 : étape de cinématique (0 aucune)
GN_M_ZELDA, GN_M_ETOURDI, GN_M_FIN = 4, 0xB, 0x1E
GN_MODES_SURS = (0, 1, 2, 3, 10, 11)
GN_PV_PHASE2 = 25


def gnd_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    e = {"decor": _u8(mem, base + GN_DECOR), "mode": _s16(mem, base + GN_MODE), "phase": _u8(mem, base + GN_PHASE),
         "demande": _u8(mem, base + GN_DEMANDE), "cine": _s16(mem, base + GN_CINE)}
    if None in e.values() or e["decor"] == 0 or e["phase"] > 2:
        return None
    e["avancee"] = 3 if e["mode"] == GN_M_FIN else max(e["phase"], 1 if e["mode"] == GN_M_ZELDA else 0)
    return e


def gnd_valeurs(e: dict) -> dict:
    return {"phase": e["avancee"]} if e["avancee"] else {}


def gnd_appliquer(mem, b, bosses, ev: str, val) -> str:
    e = gnd_lire(mem, b, bosses)
    if ev != "phase" or val is None or e is None:
        return INUTILE
    if e["avancee"] >= min(val, 3):
        return INUTILE
    fin = min(val, 3) == 3 and e["phase"] == 2
    if e["cine"] != 0 or e["demande"] != 0 or e["mode"] not in GN_MODES_SURS + ((5,) if fin else ()):
        return ATTENDRE
    base = b["base"]
    if e["phase"] == 0:
        mem.write(base + GN_DEMANDE, b"\x01")
        mem.write(base + GN_GARDE, b"\x00")
        mem.write(base + GN_MODE, struct.pack(">hh", GN_M_ETOURDI, 0))
    elif e["phase"] == 1:
        mem.write(base + boss_pv.OFF_HEALTH, bytes([GN_PV_PHASE2]))
        mem.write(base + GN_PHASE, b"\x02")
        mem.write(base + GN_DEMANDE, b"\x02")
        mem.write(base + GN_GARDE, b"\x00")
        mem.write(base + GN_MODE, struct.pack(">hh", GN_M_ETOURDI, 0))
    else:
        mem.write(base + GN_GARDE, b"\x00")
        mem.write(base + GN_INVULN, struct.pack(">h", 5))
        mem.write(base + GN_MODE, struct.pack(">hh", GN_M_FIN, 0))
    return FAIT


def _pos(mem, base: int):
    raw = mem.try_read(base + OFF_POS, 12)
    if raw is None or len(raw) != 12:
        return None
    p = struct.unpack(">3f", raw)
    return p if all(v == v and abs(v) < 1e7 for v in p) else None


def _pos_link(mem):
    raw = mem.try_read(SYM_LINK, 4)
    if raw is None or len(raw) != 4:
        return None
    link = struct.unpack(">I", raw)[0]
    return _pos(mem, link) if link else None


# ---------------------------------------------------------------------------
# Molgera (217) : la langue tirée [DECOMP d_a_bwd.cpp sita_move / sita_hit ; HD 0x020FB0F8..0x020FB238, 0x020FFFB0]
# Dans le jeu : le grappin accroche la langue (mode 3) ; tant que la minuterie +0x1E29 n'est pas à 0, la langue reste
# tendue vers son ancre (+0x1E0C, 3 f32) et prend les coups d'épée. Un seul joueur l'accroche ; chez les autres, sans
# grappin : l'ancre posée à côté de LEUR Link (à 100 vers le boss, 30 plus haut), la minuterie à 120 (la durée du
# jeu), puis mode 3, sous-état 0 : le jeu tire lui-même le bout de la langue vers l'ancre, compte les coups, et
# revient seul en mode 1. Jamais si notre Link est à plus de 1200 de son Molgera (le jeu ramènerait l'ancre vers lui :
# la langue serait hors de portée), ni si la langue est déjà prise chez nous.
# ---------------------------------------------------------------------------
MO_ANCRE = 0x1E0C           # 3 f32
MO_LANGUE_PRISE = 0x1E28    # u8 : le grappin la tient
MO_LANGUE_T = 0x1E29        # s8 : la langue est frappable tant que ce n'est pas 0
MO_LANGUE_DUREE, MO_LANGUE_LOIN, MO_LANGUE_DEVANT, MO_LANGUE_HAUT = 120, 1200.0, 100.0, 30.0
MO_M_SABLE, MO_M_LANGUE = 1, 3


def molgera_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    e = {"mode": _s16(mem, base + boss_mort.MO_MODE), "sous": _s16(mem, base + boss_mort.MO_SOUS),
         "demo": _s16(mem, base + boss_mort.MO_DEMO), "prise": _u8(mem, base + MO_LANGUE_PRISE),
         "t": _u8(mem, base + MO_LANGUE_T)}
    return None if None in e.values() else e


def molgera_evenements(avant: dict, e: dict) -> list:
    return ["langue"] if avant["mode"] != MO_M_LANGUE and e["mode"] == MO_M_LANGUE else []


def molgera_appliquer(mem, b, bosses, ev: str, val) -> str:
    e = molgera_lire(mem, b, bosses)
    if ev != "langue" or e is None or e["mode"] == MO_M_LANGUE or e["prise"] or e["t"]:
        return INUTILE
    if e["mode"] != MO_M_SABLE or e["sous"] != 2 or e["demo"] != 0:
        return ATTENDRE if e["mode"] in (MO_M_SABLE, 2) else INUTILE
    link, boss = _pos_link(mem), _pos(mem, b["base"])
    if link is None or boss is None:
        return ATTENDRE
    dx, dz = boss[0] - link[0], boss[2] - link[2]
    d = (dx * dx + dz * dz) ** 0.5
    if d > MO_LANGUE_LOIN:
        return ATTENDRE                                 # notre Link est trop loin : s'il s'approche à temps, oui
    k = MO_LANGUE_DEVANT / d if d > MO_LANGUE_DEVANT else 0.0
    base = b["base"]
    mem.write(base + MO_ANCRE, struct.pack(">3f", link[0] + dx * k, link[1] + MO_LANGUE_HAUT, link[2] + dz * k))
    mem.write(base + MO_LANGUE_T, bytes([MO_LANGUE_DUREE]))
    mem.write(base + boss_mort.MO_MODE, struct.pack(">hh", MO_M_LANGUE, 0))
    return FAIT


# ---------------------------------------------------------------------------
# Ganon marionnette, première forme (243) : les fils [DECOMP d_a_bgn.cpp ; la coupe est dans l'accroche ``ATT``,
# HD 0x020588C8..0x02058978 ; la réparation dans head_recover, HD 0x02084C6C..0x02084CA8]
# Huit fils (0, 1 la tête ; 2 le bassin ; 3, 4 les bras ; 5, 6 les jambes ; 7 la queue), chacun avec un octet
# « coupé » (+0x12EF0 + i × 0x30C) d'où le jeu recalcule tout à chaque image (corde, membre qui pend, choix de
# l'attaque). Un fil coupé chez l'un est coupé chez tous : l'octet à 1 et l'ondulation de la corde (+0x34 dans le
# fil = 6000.0), comme le jeu ; si les deux fils de la tête sont alors coupés, le délai de réparation (+0x14F1C =
# 600), comme le jeu. La réparation (la marionnette remonte et rattache les fils 0, 1, 3, 4, 7) est commune aussi :
# octet à 0 et coups restants (+0x38 dans le fil) à 2, les écritures du jeu.
# ---------------------------------------------------------------------------
PG_FIL0 = 0x12EF0           # u8 m2D0 du fil 0
PG_FIL_PAS = 0x30C
PG_FIL_ONDE = 0x34          # f32 m304, dans le fil : 6000.0 à la coupe
PG_FIL_COUPS = 0x38         # s8 m308, dans le fil : coups restants (2 après réparation)
PG_T_TETE = 0x14F1C         # s16 : délai avant la réparation quand les deux fils de la tête sont coupés
PG_FILS_REPARES = (0, 1, 3, 4, 7)


def _pg_fil(i: int) -> int:
    return PG_FIL0 + i * PG_FIL_PAS


def pg_lire(mem, b, bosses) -> dict | None:
    mc = boss_mort._marionnette(mem, b, bosses)
    etat = _s16(mem, b["base"] + boss_mort.PG1_ETAT)
    if mc is None or mc != (0, 0) or etat is None or not 0 <= etat <= 5:
        return None
    fils = [_u8(mem, b["base"] + _pg_fil(i)) for i in range(8)]
    return None if None in fils else {"fils": tuple(int(bool(f)) for f in fils)}


def pg_evenements(avant: dict, e: dict) -> list:
    out = [f"fil{i}" for i in range(8) if not avant["fils"][i] and e["fils"][i]]
    if any(avant["fils"][i] and not e["fils"][i] for i in PG_FILS_REPARES):
        out.append("repare")
    return out


def pg_appliquer(mem, b, bosses, ev: str, val) -> str:
    e = pg_lire(mem, b, bosses)
    if e is None:
        return ATTENDRE
    base = b["base"]
    if ev == "repare":
        coupes = [i for i in PG_FILS_REPARES if e["fils"][i]]
        if not coupes:
            return INUTILE
        for i in coupes:
            mem.write(base + _pg_fil(i) + PG_FIL_COUPS, b"\x02")
            mem.write(base + _pg_fil(i), b"\x00")
        return FAIT
    if not ev.startswith("fil") or not ev[3:].isdigit() or not 0 <= int(ev[3:]) < 8:
        return INUTILE
    i = int(ev[3:])
    if e["fils"][i]:
        return INUTILE
    mem.write(base + _pg_fil(i) + PG_FIL_ONDE, struct.pack(">f", 6000.0))
    mem.write(base + _pg_fil(i), b"\x01")
    if i in (0, 1) and e["fils"][1 - i]:
        mem.write(base + PG_T_TETE, struct.pack(">h", 600))
    return FAIT


@dataclass(frozen=True)
class Phase:
    evs: tuple              # événements que ce boss peut annoncer
    lire: Callable          # (mem, b, bosses) -> état (dict) ou None
    evenements: Callable    # (avant, état) -> [événements qui viennent de commencer chez nous]
    appliquer: Callable     # (mem, b, bosses, ev, val) -> FAIT | ATTENDRE | INUTILE
    valeurs: Callable = lambda e: {}        # état -> {ev: val} des compteurs (qui ne font que monter)
    textes: dict | None = None              # ev -> phrase pour le journal
    tenir: Callable | None = None           # (mem, b, état) : à chaque tick juste après une phase appliquée chez nous


PHASES: dict[int, Phase] = {
    217: Phase(("langue",), molgera_lire, molgera_evenements, molgera_appliquer,
               textes={"langue": "sa langue est tirée : frappe-la"}),
    243: Phase(tuple(f"fil{i}" for i in range(8)) + ("repare",), pg_lire, pg_evenements, pg_appliquer,
               textes=dict({f"fil{i}": "un de ses fils est coupé" for i in range(8)},
                           repare="elle remonte et rattache ses fils")),
    246: Phase(("phase",), gnd_lire, lambda avant, e: [], gnd_appliquer, gnd_valeurs,
               {"phase": "le combat avance (Zelda assommée, puis relevée, puis le coup final)"}),
    211: Phase(("separation",), jalhalla_lire, jalhalla_evenements, jalhalla_appliquer,
               textes={"separation": "il se sépare en petits Poe"}),
    240: Phase(("main1", "main2", "tete"), gohdan_lire, gohdan_evenements, gohdan_appliquer,
               textes={"main1": "sa main gauche s'endort", "main2": "sa main droite s'endort",
                       "tete": "sa tête tombe, bouche ouverte"}),
    235: Phase(("chute",), kalle_lire, kalle_evenements, kalle_appliquer,
               textes={"chute": "il tombe, son cœur est à découvert"}, tenir=kalle_tenir),
    238: Phase(("masque",), roi_lire, roi_evenements, roi_appliquer, roi_valeurs,
               {"masque": "son masque prend un coup de marteau (au quatrième il se brise)"}),
}


class BossPhaseSync:
    """Côté client. ``tick(mem, now, stage, bosses, free, can)`` à chaque échantillon, après les PV ;
    ``receive(msg, me)`` au fil des messages ; ``take_out()`` / ``take_notes()`` au tick."""

    def __init__(self, enabled: bool = False, group: str = "default", clock=time.monotonic,
                 exclus: frozenset = frozenset()):
        self.enabled, self.group, self.clock = bool(enabled), group, clock
        self.exclus = frozenset(exclus)
        self.stage: str | None = None
        self.avant: dict[str, dict] = {}                # clé -> dernier état lu
        self.pids: dict[str, int] = {}
        self.recus: dict[tuple[str, str], dict] = {}    # (clé, ev) -> {t, val, nom}
        self.attendu: dict[tuple[str, str], float] = {}  # (clé, ev) appliqué chez nous : pas d'écho avant cette heure
        self.val_envoyee: dict[tuple[str, str], tuple[int, float]] = {}
        self.essai_t: dict[tuple[str, str], float] = {}
        self.out: list[dict] = []
        self.emis: list[float] = []
        self.notes: list[str] = []
        self.annonces = self.appliques = self.perdus = 0

    def take_out(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        self.emis = [t for t in self.emis if now - t < 1.0]
        n = max(0, DEBIT - len(self.emis))
        out, self.out = self.out[:n], self.out[n:]
        self.emis += [now] * len(out)
        return out

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def receive(self, msg: dict, me: int | None = None) -> int:
        if not self.enabled or msg.get("group", self.group) != self.group:
            return 0
        if me is not None and msg.get("from") == me:
            return 0
        rec = valide(msg)
        if rec is None or rec["stage"] != self.stage or boss_pv.key_proc(rec["key"]) in self.exclus:
            return 0
        k = (rec["key"], rec["ev"])
        nom = msg.get("nom")
        vieux = self.recus.get(k)
        if "val" in rec and vieux is not None and vieux.get("val") is not None and vieux["val"] >= rec["val"]:
            vieux["t"] = self.clock()                   # rappel d'une valeur déjà connue
            return 0
        self.recus[k] = {"t": self.clock(), "val": rec.get("val"),
                         "nom": nom[:24] if isinstance(nom, str) and nom else "un autre joueur"}
        return 1

    def reset_network(self) -> None:
        self.recus.clear()
        self.val_envoyee.clear()
        self.out.clear()

    def _nouvelle_stage(self, stage: str | None) -> None:
        self.stage = stage
        self.avant.clear()
        self.pids.clear()
        self.recus.clear()
        self.attendu.clear()
        self.val_envoyee.clear()
        self.essai_t.clear()
        self.out.clear()

    def _envoyer(self, key: str, ev: str, val: int | None = None) -> None:
        msg = {"type": "bossp", "group": self.group, "stage": self.stage, "key": key, "ev": ev}
        if val is not None:
            msg["val"] = val
        self.out = [m for m in self.out if (m["key"], m["ev"]) != (key, ev)] + [msg]

    def tick(self, mem, now: float | None, stage: str | None, bosses: list | None, free: bool, can: bool) -> int:
        """Renvoie le nombre de phases écrites chez nous."""
        if not self.enabled:
            return 0
        now = self.clock() if now is None else now
        if stage != self.stage:
            self._nouvelle_stage(stage)
        if stage is None or not bosses:
            return 0
        ecrits = 0
        for b in bosses:
            ph = PHASES.get(b.get("proc"))
            if ph is None or b["proc"] in self.exclus:
                continue
            t = boss_pv.BOSS_PV[b["proc"]]
            if b.get("max") != t.ident:
                continue
            key = boss_pv.boss_key(b["proc"], b.get("params", 0))
            e = ph.lire(mem, b, bosses)
            if e is None:
                continue
            if self.pids.get(key) != b["pid"]:
                self.pids[key] = b["pid"]               # nouvelle instance : on repart de son état, sans annonce
                self.avant.pop(key, None)
            avant, self.avant[key] = self.avant.get(key), e
            if ph.tenir is not None and free and can \
                    and any(now < self.attendu.get((key, ev), -1e9) for ev in ph.evs):
                ph.tenir(mem, b, e)
            # 1. ce qui commence chez nous -> le serveur (sauf ce que nous venons d'appliquer nous-mêmes)
            if avant is not None:
                for ev in ph.evenements(avant, e):
                    if now < self.attendu.get((key, ev), -1e9):
                        continue
                    self._envoyer(key, ev)
                    self.annonces += 1
            for ev, val in ph.valeurs(e).items():
                v0, t0 = self.val_envoyee.get((key, ev), (-1, -1e9))
                if val > v0 or now - t0 >= RAPPEL:
                    self.val_envoyee[(key, ev)] = (max(val, v0), now)
                    self._envoyer(key, ev, val)
            # 2. ce qui a commencé chez un autre -> notre boss
            # la tête d'abord (elle endort elle-même les mains encore éveillées), puis dans l'ordre d'arrivée
            for (k, ev), r in sorted(self.recus.items(), key=lambda x: (x[0][1] != "tete", x[1]["t"])):
                if k != key:
                    continue
                if r["val"] is None and now - r["t"] > EV_AGE:
                    del self.recus[(k, ev)]
                    self.perdus += 1
                    continue
                if not (free and can) or now - self.essai_t.get((key, ev), -1e9) < PAS_ECRITURE:
                    continue
                if not boss_mort._ecriture_permise(mem, b):
                    continue
                res = ph.appliquer(mem, b, bosses, ev, r["val"])
                if res == ATTENDRE:
                    continue
                if r["val"] is None:
                    del self.recus[(k, ev)]
                if res != FAIT:
                    continue
                self.essai_t[(key, ev)] = now
                self.attendu[(key, ev)] = now + EV_AGE + 2.0
                if r["val"] is not None:
                    self.val_envoyee[(key, ev)] = (r["val"], now)
                self.avant.pop(key, None)               # l'état vient de changer par notre écriture : pas d'annonce
                self.appliques += 1
                ecrits += 1
                quoi = (ph.textes or {}).get(ev, ev)
                self.notes.append(f"{t.nom} : {quoi} chez {r['nom']} : chez toi aussi")
        return ecrits

    def status(self) -> str:
        if not self.enabled:
            return "désactivées"
        return f"{self.annonces} annoncée(s), {self.appliques} appliquée(s), {self.perdus} abandonnée(s)"
