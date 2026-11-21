"""Miroir des attaques des boss (2026-11-20 ; même idée que pour Gohma, synchro/boss_unique.py, rendue générique).

Ce que les joueurs voient : tous les boss (un par joueur, filtre A9 inchangé) font LA MÊME attaque AU MÊME MOMENT.

Comment : le serveur désigne, par boss, une RÉFÉRENCE : le joueur qui l'a FRAPPÉ EN DERNIER (un coup qui lui retire
des PV) ; avant le premier coup, le premier du groupe à s'être signalé dans la stage ; la référence quitte la stage :
le premier qui se signale la remplace. Pas plus d'un changement par ``REF_CHANGE_MIN`` s. Le boss de la référence
choisit ses attaques comme d'habitude, contre son Link ; son client annonce chaque attaque qui commence (``att``) ;
chez les autres (« suiveurs »), le client :
* RETIENT leur boss : il tient au-dessus de zéro la minuterie que le jeu attend avant d'attaquer (une écriture que le
  jeu fait lui-même, répétée) tant que la référence donne des nouvelles (``REF_SILENCE``) ;
* LANCE la même attaque en écrivant ce que le jeu écrit lui-même quand il la choisit, seulement depuis l'attente, et
  jamais en retard (``ATT_AGE``).
Chaque recette est relue dans le code HD (tests/test_boss_miroir.py, classe TestRpx). Chaque boss vise toujours SON
Link : seul le moment et le type de l'attaque sont communs. Sans nouvelle de la référence, chaque boss reprend ses
propres attaques : rien n'est jamais bloqué.

Messages (type « bossm ») : client -> serveur ``{group, stage, key}`` (présence, toutes les ``RAPPEL`` s) avec
``att: {n, v: [entiers]}`` quand une attaque commence ; serveur -> membres du groupe dans la stage
``{..., ref, from, nom}`` : la référence (à tous quand elle change, à l'auteur en réponse), et les messages de la
référence relayés aux autres (attaques et battement). Oublié quand la stage se vide. Un ancien client ignore tout.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Callable

from synchro import boss_mort, boss_pv

ATT_AGE = 1.5               # s : une attaque annoncée depuis plus longtemps n'est plus lancée (jamais en retard)
REF_SILENCE = 5.0           # s sans nouvelle de la référence : chaque boss reprend ses propres attaques
REF_CHANGE_MIN = 2.0        # s au moins entre deux changements de référence par un coup
RAPPEL = 2.0                # s : présence (et battement de la référence)
PAS_TENUE = 0.25            # s entre deux écritures de retenue
PAUSE_APRES_LANCER = 0.5    # s sans retenue après un lancement (la retenue ne doit jamais défaire ce qu'on vient
                            # de lancer : certaines attaques se lancent en vidant la minuterie même qu'on tient)
DEBIT = 4                   # messages par seconde au plus
V_MAX = 4                   # entiers au plus dans une attaque
FAIT, ATTENDRE, INUTILE = "fait", "attendre", "inutile"
PROC_GOHMA = 234            # elle a son propre miroir (synchro/boss_unique.py)
_s16, _u8 = boss_mort._s16, boss_mort._u8


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def attaque_valide(att) -> dict | None:
    if not isinstance(att, dict) or not _int(att.get("n")) or not 0 < att["n"] < 1 << 31:
        return None
    v = att.get("v")
    if not isinstance(v, list) or not 0 < len(v) <= V_MAX or not all(_int(x) and -32768 <= x <= 32767 for x in v):
        return None
    return {"n": att["n"], "v": list(v)}


def valide(msg, strict: bool = True) -> dict | None:
    if not isinstance(msg, dict):
        return None
    stage, key = msg.get("stage"), msg.get("key")
    if not isinstance(stage, str) or not 0 < len(stage) <= 8 or not isinstance(key, str) \
            or not 0 < len(key) <= boss_pv.KEY_MAX:
        return None
    proc = boss_pv.key_proc(key)
    if proc not in boss_pv.BOSS_PV or proc == PROC_GOHMA or (strict and proc not in MIROIRS):
        return None
    out = {"stage": stage, "key": key}
    if "att" in msg:
        att = attaque_valide(msg["att"])
        if att is None:
            return None
        out["att"] = att
    return out


@dataclass(frozen=True)
class Miroir:
    lire: Callable          # (mem, b, bosses) -> état (dict) ou None
    debut: Callable         # (avant, état) -> liste d'entiers (l'attaque qui vient de commencer) ou None
    lancer: Callable        # (mem, b, bosses, état, v) -> FAIT | ATTENDRE | INUTILE
    tenir: Callable         # (mem, b, bosses, état) -> True si une écriture de retenue a été faite
    noms: dict | None = None    # premier entier de l'attaque -> nom pour le journal


# ---------------------------------------------------------------------------
# Kalle Demos (235) [DECOMP d_a_bmd.cpp wait, HD 0x020B1EC8 ; choix : 0x020B2350 et 0x020B23B8]
# En attente (mMode 0, sous-état >= 0), quand la minuterie m308[2] (s16 +0x42E) arrive à 0, le jeu la relance et,
# 4 fois sur 5, attaque : Link à moins de 1300 -> mMode = 1 (le pied le plus proche de Link fouette), sinon
# mMode = 2 (les pieds s'enterrent et le poursuivent), sous-état 0. Ce sont les pieds qui frappent, en lisant le corps.
# Retenue : la minuterie tenue au-dessus de zéro en attente ET pendant ses attaques et sa chute (banc du 2026-11-20 :
# tenue seulement en attente, elle était déjà à 0 au retour d'une attaque et il réattaquait de lui-même avant notre
# écriture, 3 fois sur 7) ; jamais pendant l'introduction ni la fin (mMode 10, 0xB : la fin s'en sert autrement).
# Lancement : mMode et sous-état d'un seul bloc de 4 octets (le jeu ne doit jamais voir l'un sans l'autre).
# ---------------------------------------------------------------------------
KD_REPOS = 0x42E            # s16 m308[2]
KD_REPOS_TENU, KD_REPOS_SEUIL = 60, 30
KD_ATTAQUES = (1, 2)


def kalle_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    mode, sous, repos = _s16(mem, base + boss_mort.KD_MODE), _s16(mem, base + boss_mort.KD_SOUS), \
        _s16(mem, base + KD_REPOS)
    return None if None in (mode, sous, repos) else {"mode": mode, "sous": sous, "repos": repos}


def kalle_debut(avant: dict, e: dict):
    return [e["mode"]] if avant["mode"] == 0 and e["mode"] in KD_ATTAQUES else None


def kalle_lancer(mem, b, bosses, e: dict, v: list) -> str:
    if v[0] not in KD_ATTAQUES or e["mode"] in (3, 5, 10, boss_mort.KD_MODE_FIN):
        return INUTILE
    if e["mode"] != 0 or e["sous"] < 0:
        return ATTENDRE
    mem.write(b["base"] + boss_mort.KD_MODE, struct.pack(">hh", v[0], 0))
    return FAIT


def kalle_tenir(mem, b, bosses, e: dict) -> bool:
    if e["mode"] not in (0, 1, 2, 3) or not 0 <= e["repos"] < KD_REPOS_SEUIL:
        return False
    mem.write(b["base"] + KD_REPOS, struct.pack(">h", KD_REPOS_TENU))
    return True


# ---------------------------------------------------------------------------
# Molgera (217) [DECOMP d_a_bwd.cpp wait (HD 0x020FFB20) et fly (HD 0x02101148)]
# * sous le sable (mode 1) : quand la minuterie +0x1B54 est à 0, Link à moins de 700 -> il avale (mode 5, sous-état 0 :
#   0x020FFD00) ; à plus de 3000 il replonge pour ressortir près de Link (jamais retenu ni lancé par nous : c'est ce
#   qui le ramène vers un Link éloigné) ;
# * en vol (mode 10, sous-état 1) : quand la minuterie +0x1B52 arrive à 0, il pique sur Link (sous-état 2 : 0x021014F4).
# Retenue : la minuterie de l'état en cours tenue au-dessus de zéro. Lancement : « avale » = les deux écritures du
# jeu ; « pique » = la minuterie mise à 0, le jeu fait la transition lui-même.
# ---------------------------------------------------------------------------
MO_T_VOL = 0x1B52           # s16 m18CC[1] : attente avant le piqué (mode 10, sous-état 1)
MO_T_SABLE = 0x1B54         # s16 m18D0 : attente sous le sable (mode 1)
MO_LANGUE = 0x1E28          # deux u8 : la langue est prise au grappin, tirée
MO_DISTANCE = 0x1E30        # f32 : distance de Link, recalculée à chaque image
MO_AVALE, MO_VOL, MO_REPLONGE_AU_DELA = 5, 10, 3000.0
MO_TENU, MO_SEUIL = 60, 30


def molgera_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    e = {"mode": _s16(mem, base + boss_mort.MO_MODE), "sous": _s16(mem, base + boss_mort.MO_SOUS),
         "t_vol": _s16(mem, base + MO_T_VOL), "t_sable": _s16(mem, base + MO_T_SABLE),
         "demo": _s16(mem, base + boss_mort.MO_DEMO)}
    langue, dist = mem.try_read(base + MO_LANGUE, 2), mem.try_read(base + MO_DISTANCE, 4)
    if None in e.values() or langue is None or dist is None or len(langue) != 2 or len(dist) != 4:
        return None
    e["langue"] = bool(langue[0] or langue[1])
    e["dist"] = struct.unpack(">f", dist)[0]
    return e if e["dist"] == e["dist"] else None


def molgera_debut(avant: dict, e: dict):
    if avant["mode"] == 1 and e["mode"] == MO_AVALE:
        return [MO_AVALE]
    if avant["mode"] == MO_VOL and avant["sous"] == 1 and e["mode"] == MO_VOL and e["sous"] >= 2:
        return [MO_VOL]
    return None


def molgera_lancer(mem, b, bosses, e: dict, v: list) -> str:
    base = b["base"]
    if e["demo"] != 0 or e["langue"]:
        return INUTILE
    if v[0] == MO_AVALE and e["mode"] == 1:
        if e["sous"] != 2:
            return ATTENDRE
        if e["dist"] > MO_REPLONGE_AU_DELA:
            return INUTILE                              # notre Link est loin : il va replonger vers lui de lui-même
        mem.write(base + boss_mort.MO_MODE, struct.pack(">hh", MO_AVALE, 0))
        return FAIT
    if v[0] == MO_VOL and e["mode"] == MO_VOL and e["sous"] == 1:
        mem.write(base + MO_T_VOL, struct.pack(">h", 0))
        return FAIT
    return INUTILE


def molgera_tenir(mem, b, bosses, e: dict) -> bool:
    if e["demo"] != 0 or e["langue"]:
        return False
    if e["mode"] == 1 and e["dist"] <= MO_REPLONGE_AU_DELA and 0 <= e["t_sable"] < MO_SEUIL:
        mem.write(b["base"] + MO_T_SABLE, struct.pack(">h", MO_TENU))
        return True
    if e["mode"] == MO_VOL and e["sous"] == 1 and 0 <= e["t_vol"] < MO_SEUIL:
        mem.write(b["base"] + MO_T_VOL, struct.pack(">h", MO_TENU))
        return True
    return False


# ---------------------------------------------------------------------------
# Gohdan (240) [DECOMP d_a_bst.cpp main_cont, fondu dans execute en HD : 0x020E5A84..0x020E5F6C]
# C'est la TÊTE qui choisit, au hasard, sans regarder Link : en phase 11, quand sa minuterie +0x30B2 est à 0, une
# attaque des mains (action des mains +0x130A, sous-état 0) : 10 poing qui s'écrase (une main), 11 claque à deux
# mains, 12 mains jointes qui s'écrasent, 13 balayage (une main) ; en phase 12, quand +0x30B4 est à 0, son rayon
# (tête : action 14, sous-état 0, +0x1304 = -500). Chaque attaque vise ensuite le Link du joueur.
# Retenue : la minuterie de la phase tenue au-dessus de zéro (elles ne servent à rien d'autre).
# « Mains jointes » (12) : les deux mains doivent se rejoindre exactement ; deux écritures à cheval sur une image
# pourraient les laisser en l'air. Elle est lancée quand même (les quatre écritures d'affilée), avec un garde-fou :
# si les deux mains d'un suiveur sont encore en « mains jointes » après ``GD_JOINTES_MAX`` s, elles sont remises en
# vol avec l'écriture que le jeu fait lui-même sur une main qui attaque quand l'autre est touchée (action 1,
# sous-état 0 : 0x020EAC98..AC).
# ---------------------------------------------------------------------------
GD_T_MAINS = 0x30B2         # s16 m2E7E[0] : repos avant une attaque des mains (phase 11)
GD_T_RAYON = 0x30B4         # s16 m2E7E[1] : repos avant le rayon (phase 12)
GD_RAYON_F = 0x1304         # f32 : -500.0 au départ du rayon
GD_TENU, GD_SEUIL = 100, 40
GD_VOL, GD_RAYON = 1, 14
GD_ATT_MAINS = (10, 11, 12, 13)
GD_JOINTES, GD_JOINTES_MAX = 12, 6.0
_gd_jointes_depuis: dict[int, float] = {}       # tête (adresse) -> heure où ses deux mains ont été vues jointes


def _gd_main(mem, b, bosses, typ: int):
    return next((o for o in boss_mort._gohdan_mains(mem, b, bosses)
                 if _u8(mem, o["base"] + boss_mort.GD_TYPE) == typ), None)


def gohdan_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    if _u8(mem, base + boss_mort.GD_TYPE) != 0:
        return None
    e = {"tete": _s16(mem, base + boss_mort.GD_ACTION), "phase": _u8(mem, base + boss_mort.GD_PHASE),
         "scene": _u8(mem, base + boss_mort.GD_SCENE), "t_mains": _s16(mem, base + GD_T_MAINS),
         "t_rayon": _s16(mem, base + GD_T_RAYON)}
    for typ in (1, 2):
        o = _gd_main(mem, b, bosses, typ)
        e[f"main{typ}"] = None if o is None else _s16(mem, o["base"] + boss_mort.GD_ACTION)
    return None if None in e.values() else e


def gohdan_debut(avant: dict, e: dict):
    if avant["tete"] != GD_RAYON and e["tete"] == GD_RAYON:
        return [GD_RAYON, 0]
    parties = [t for t in (1, 2) if avant[f"main{t}"] < 10 and e[f"main{t}"] in GD_ATT_MAINS]
    if not parties:
        return None
    act = e[f"main{parties[0]}"]
    if act in (11, 12):
        return [act, 3]                                 # les deux mains (lues l'une après l'autre : une suffit)
    return [act, parties[0]]


def gohdan_lancer(mem, b, bosses, e: dict, v: list) -> str:
    if len(v) < 2 or e["scene"] != 0:
        return INUTILE
    base = b["base"]
    if v[0] == GD_RAYON:
        if e["phase"] != 12:
            return INUTILE
        if e["tete"] != GD_VOL:
            return ATTENDRE
        mem.write(base + GD_RAYON_F, struct.pack(">f", -500.0))
        mem.write(base + boss_mort.GD_SOUS, struct.pack(">h", 0))
        mem.write(base + boss_mort.GD_ACTION, struct.pack(">h", GD_RAYON))
        return FAIT
    if v[0] not in GD_ATT_MAINS or v[1] not in (1, 2, 3) or e["phase"] != 11:
        return INUTILE
    act = v[0]
    qui = (1, 2) if act in (11, GD_JOINTES) else (v[1],) if v[1] in (1, 2) else ()
    if not qui:
        return INUTILE
    if any(e[f"main{t}"] in (6, boss_mort.GD_ACTION_FIN) for t in qui):
        return INUTILE
    if any(e[f"main{t}"] != GD_VOL for t in qui):
        return ATTENDRE
    mains = [_gd_main(mem, b, bosses, t) for t in qui]
    for o in mains:
        h = boss_pv.read_header(mem, o["base"]) if o else None
        if h is None or h[0] != o["pid"] or h[1] != o["proc"]:
            return ATTENDRE
    for o in mains:
        mem.write(o["base"] + boss_mort.GD_SOUS, struct.pack(">h", 0))
    for o in mains:
        mem.write(o["base"] + boss_mort.GD_ACTION, struct.pack(">h", act))
    return FAIT


def gohdan_tenir(mem, b, bosses, e: dict) -> bool:
    if e["scene"] != 0:
        return False
    # garde-fou des mains jointes
    if e["main1"] == GD_JOINTES and e["main2"] == GD_JOINTES:
        t0 = _gd_jointes_depuis.setdefault(b["base"], time.monotonic())
        if time.monotonic() - t0 > GD_JOINTES_MAX:
            _gd_jointes_depuis.pop(b["base"], None)
            for t in (1, 2):
                o = _gd_main(mem, b, bosses, t)
                h = boss_pv.read_header(mem, o["base"]) if o else None
                if h is not None and h[0] == o["pid"] and h[1] == o["proc"]:
                    mem.write(o["base"] + boss_mort.GD_SOUS, struct.pack(">h", 0))
                    mem.write(o["base"] + boss_mort.GD_ACTION, struct.pack(">h", GD_VOL))
            return True
    else:
        _gd_jointes_depuis.pop(b["base"], None)
    if e["phase"] == 11 and 0 <= e["t_mains"] < GD_SEUIL:
        mem.write(b["base"] + GD_T_MAINS, struct.pack(">h", GD_TENU))
        return True
    if e["phase"] == 12 and 0 <= e["t_rayon"] < GD_SEUIL:
        mem.write(b["base"] + GD_T_RAYON, struct.pack(">h", GD_TENU))
        return True
    return False


# ---------------------------------------------------------------------------
# Jalhalla (211) [DECOMP d_a_bpw.cpp action_dousa / action_kougeki, fondues dans body_execute en HD (0x020DC704)]
# En déplacement (action 0), état 1 (immobile) puis 3 (approche) ; quand la minuterie +0x56A arrive à 0 en état 3 il
# choisit : phase 0, il RIT (action 1, état 0x14 puis 0x15), puis souffle (0x28), boules de feu (0x3C) ou ligne de
# flammes (0x32) ; phases 1 et 2, aussi le saut (action 2, état 0x46) et la charge (action 0, état 4). Chaque attaque
# vise le Link du joueur.
# Retenue : +0x56A tenue en approche (0/3) et à la fin du rire (1/0x15) ; +0x56E tenue au début du rire (1/0x14).
# Lancement : les écritures du jeu au moment du choix (action et état d'un seul bloc), depuis le déplacement ou le
# rire. Boules et flammes demandent la lanterne en main (+0x568 == 1) : sinon le souffle. La « presse » (il retombe
# sur un Link tout proche) n'est ni retenue ni lancée : elle dépend de la distance de chaque Link.
# ---------------------------------------------------------------------------
JA_T0 = 0x56A               # s16 timers[0] : minuterie de l'état en cours
JA_T2 = 0x56E               # s16 timers[2]
JA_LANTERNE = 0x568         # s16 : 1 = lanterne en main
JA_TENU, JA_SEUIL = 60, 25
JA_RIRE, JA_SOUFFLE, JA_BOULES, JA_FLAMMES, JA_SAUT, JA_CHARGE = 1, 2, 3, 4, 5, 6
JA_ENTREES = {(1, 0x14): JA_RIRE, (1, 0x15): JA_RIRE, (1, 0x28): JA_SOUFFLE, (1, 0x29): JA_SOUFFLE,
              (1, 0x3C): JA_BOULES, (1, 0x3D): JA_BOULES, (1, 0x32): JA_FLAMMES, (1, 0x33): JA_FLAMMES,
              (2, 0x46): JA_SAUT, (2, 0x47): JA_SAUT, (0, 4): JA_CHARGE, (0, 5): JA_CHARGE}
JA_ECRIRE = {JA_RIRE: (1, 0x14), JA_SOUFFLE: (1, 0x28), JA_BOULES: (1, 0x3C), JA_FLAMMES: (1, 0x32),
             JA_SAUT: (2, 0x46), JA_CHARGE: (0, 4)}
JA_DEPUIS = ((0, 1), (0, 3), (1, 0x14), (1, 0x15))


def jalhalla_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    if _u8(mem, base + boss_pv.OFF_JAL_TYPE) != 0:
        return None
    e = {"action": _s16(mem, base + boss_mort.JA_ACTION), "etat": _s16(mem, base + boss_mort.JA_ETAT),
         "t0": _s16(mem, base + JA_T0), "t2": _s16(mem, base + JA_T2), "lanterne": _s16(mem, base + JA_LANTERNE)}
    return None if None in e.values() else e


def jalhalla_debut(avant: dict, e: dict):
    c = JA_ENTREES.get((e["action"], e["etat"]))
    return [c] if c is not None and c != JA_ENTREES.get((avant["action"], avant["etat"])) else None


def jalhalla_lancer(mem, b, bosses, e: dict, v: list) -> str:
    ici = (e["action"], e["etat"])
    att = v[0]
    if att not in JA_ECRIRE or e["action"] in (3, 4, 0x14):
        return INUTILE
    if JA_ENTREES.get(ici) == att:
        return INUTILE                                  # il y est déjà de lui-même
    if ici not in JA_DEPUIS:
        return ATTENDRE
    if att == JA_RIRE and e["action"] == 1:
        return INUTILE
    if att in (JA_BOULES, JA_FLAMMES) and e["lanterne"] != 1:
        att = JA_SOUFFLE
    base = b["base"]
    if att == JA_SOUFFLE:
        mem.write(base + JA_T2, struct.pack(">h", 0))
    mem.write(base + boss_mort.JA_ACTION, struct.pack(">hh", *JA_ECRIRE[att]))
    return FAIT


def jalhalla_tenir(mem, b, bosses, e: dict) -> bool:
    ici = (e["action"], e["etat"])
    if ici in ((0, 3), (1, 0x15)) and 0 <= e["t0"] < JA_SEUIL:
        mem.write(b["base"] + JA_T0, struct.pack(">h", JA_TENU))
        return True
    if ici == (1, 0x14) and 0 <= e["t2"] < JA_SEUIL:
        mem.write(b["base"] + JA_T2, struct.pack(">h", JA_TENU))
        return True
    return False


# ---------------------------------------------------------------------------
# Ganon marionnette (243, 244, 245) [DECOMP d_a_bgn.cpp dance_0 (HD 0x02086404..0x02086A2C), d_a_bgn2.cpp,
# d_a_bgn3.cpp]. Une recette par forme ; seule la forme en cours, hors cinématique, est lue (les autres sont rangées).
#
# 1. La marionnette : en attente (état 0, sous-état 1), quand la minuterie +0x14F18 arrive à 0 et qu'il ne reste plus
#    de Keese, elle agit selon son compteur de cycle (+0x14EC0 : 0 vague de Keese, 1 2 3 une attaque, 4 5 rien) et
#    selon SES FILS COUPÉS (deux poings, un poing, la toupie avec la queue, l'écrasement) : le type de l'attaque
#    dépend donc des fils que chaque joueur a coupés chez lui. Seul le MOMENT est commun : chez un suiveur dont le
#    compteur vaut 1, 2 ou 3, la minuterie est tenue, puis vidée quand la référence attaque : son jeu choisit alors
#    lui-même l'attaque qui convient à ses fils. Les vagues de Keese et la réparation des fils (tête, fils 0 et 1
#    coupés) ne sont jamais retenues.
# 2. L'araignée : un seul geste en boucle (elle saute, plane au-dessus de Link, retombe). La retombée part quand la
#    minuterie +0x620 arrive à 0 : tenue chez un suiveur tant qu'elle plane, mise à 2 quand la référence retombe.
# 3. Le serpent : il tourne dans la salle et ne fonce sur Link qu'au troisième mur touché (compteur +0x11E6E), en
#    armant la minuterie de charge +0x11E6C (60, 40 ou 30 selon ses PV). Suiveur : compteur de murs tenu à 0, charge
#    armée quand la référence charge.
# ---------------------------------------------------------------------------
PG1_REPOS = 0x14F18         # s16 mC7AC[0] : repos en attente (dans les autres états : minuterie de l'attaque)
PG1_CYCLE = 0x14EC0         # s16 mC754 : compteur de cycle 0..5
PG1_FIL0, PG1_FIL1 = 0x12EF0, 0x131FC   # u8 : fils de la tête coupés
PG1_TENU, PG1_SEUIL = 60, 25
PG1_ATTAQUES = (1, 2, 3)    # états : poing(s), écrasement, toupie
PG2_T = 0x620               # s16 m0330[0] : attente avant la retombée (états 1/1 et 1/2)
PG2_VY = 0x340              # f32 speed.y
PG2_TENU, PG2_SEUIL = 40, 20
PG3_VIRAGE_T = 0x11E6C      # s16 m0FD90 : charge en cours (> 1)
PG3_MURS = 0x11E6E          # s16 m0FD92 : murs touchés (au 3e : charge)
PG3_RETOUR = 0x11E8C        # s16 : retour au centre à l'entrée (pas de charge tant qu'il n'est pas à 0)
PG3_CHARGES = {3: 60, 2: 40, 1: 30}


def _pg_forme(mem, b, bosses, rang: int) -> bool:
    mc = boss_mort._marionnette(mem, b, bosses)
    return mc is not None and mc == (rang, 0)


def pg1_lire(mem, b, bosses) -> dict | None:
    if not _pg_forme(mem, b, bosses, 0):
        return None
    base = b["base"]
    e = {"etat": _s16(mem, base + boss_mort.PG1_ETAT), "sous": _s16(mem, base + boss_mort.PG1_SOUS),
         "repos": _s16(mem, base + PG1_REPOS), "cycle": _s16(mem, base + PG1_CYCLE),
         "fil0": _u8(mem, base + PG1_FIL0), "fil1": _u8(mem, base + PG1_FIL1)}
    return None if None in e.values() else e


def pg1_debut(avant: dict, e: dict):
    return [1] if avant["etat"] == 0 and e["etat"] in PG1_ATTAQUES else None


def _pg1_retenable(e: dict) -> bool:
    return e["etat"] == 0 and e["sous"] == 1 and e["cycle"] in (1, 2, 3) and not (e["fil0"] and e["fil1"])


def pg1_lancer(mem, b, bosses, e: dict, v: list) -> str:
    if e["etat"] != 0:
        return INUTILE
    if not _pg1_retenable(e):
        return INUTILE if e["cycle"] not in (1, 2, 3) else ATTENDRE
    mem.write(b["base"] + PG1_REPOS, struct.pack(">h", 0))
    return FAIT


def pg1_tenir(mem, b, bosses, e: dict) -> bool:
    if not _pg1_retenable(e) or not 0 <= e["repos"] < PG1_SEUIL:
        return False
    mem.write(b["base"] + PG1_REPOS, struct.pack(">h", PG1_TENU))
    return True


def pg2_lire(mem, b, bosses) -> dict | None:
    if not _pg_forme(mem, b, bosses, 1):
        return None
    base = b["base"]
    e = {"etat": _s16(mem, base + boss_mort.PG2_ETAT), "sous": _s16(mem, base + boss_mort.PG2_SOUS),
         "t": _s16(mem, base + PG2_T)}
    vy = mem.try_read(base + PG2_VY, 4)
    if None in e.values() or vy is None or len(vy) != 4:
        return None
    e["vy"] = struct.unpack(">f", vy)[0]
    return e if e["vy"] == e["vy"] else None


def _pg2_plane(e: dict) -> bool:
    return e["etat"] == 1 and e["sous"] in (1, 2)


def pg2_debut(avant: dict, e: dict):
    return [1] if _pg2_plane(e) and e["t"] == 0 and _pg2_plane(avant) and avant["t"] > 0 else None


def pg2_lancer(mem, b, bosses, e: dict, v: list) -> str:
    if not _pg2_plane(e) or e["t"] <= 2:
        return INUTILE
    mem.write(b["base"] + PG2_T, struct.pack(">h", 2))      # jamais 1 (le jeu y joue un son de chute)
    return FAIT


def pg2_tenir(mem, b, bosses, e: dict) -> bool:
    if not _pg2_plane(e) or e["vy"] < 0 or not 3 <= e["t"] < PG2_SEUIL:
        return False
    mem.write(b["base"] + PG2_T, struct.pack(">h", PG2_TENU))
    return True


def pg3_lire(mem, b, bosses) -> dict | None:
    if not _pg_forme(mem, b, bosses, 2):
        return None
    base = b["base"]
    e = {"etat": _s16(mem, base + boss_mort.PG3_ETAT), "charge": _s16(mem, base + PG3_VIRAGE_T),
         "murs": _s16(mem, base + PG3_MURS), "retour": _s16(mem, base + PG3_RETOUR),
         "pv": _u8(mem, base + boss_pv.OFF_HEALTH)}
    return None if None in e.values() else e


def _pg3_libre(e: dict) -> bool:
    return e["etat"] == 0 and e["retour"] == 0


def pg3_debut(avant: dict, e: dict):
    return [1] if _pg3_libre(e) and e["charge"] > 1 and avant["charge"] <= 1 else None


def pg3_lancer(mem, b, bosses, e: dict, v: list) -> str:
    if not _pg3_libre(e) or e["charge"] > 1 or e["pv"] not in PG3_CHARGES:
        return INUTILE
    mem.write(b["base"] + PG3_MURS, struct.pack(">h", 0))
    mem.write(b["base"] + PG3_VIRAGE_T, struct.pack(">h", PG3_CHARGES[e["pv"]]))
    return FAIT


def pg3_tenir(mem, b, bosses, e: dict) -> bool:
    if not _pg3_libre(e) or e["murs"] <= 0:
        return False
    mem.write(b["base"] + PG3_MURS, struct.pack(">h", 0))
    return True


# ---------------------------------------------------------------------------
# Ganondorf (246) [HD seulement : gnd_move 0x02159FC8, le choix dans move0 sous-état 2 : 0x0215A2C8..0x0215A42C]
# En attente (mode +0x3EA == 0), Link devant lui entre 100 et 630, quand la minuterie +0x420 est à 0, il attaque :
# mode 1 (il poursuit Link et saute sur lui ; sous-état 0, ou 5 à partir de la phase 1) ou mode 2 (il marche vers
# Link et frappe ; sous-état 0 ou 1). Ses parades, ses esquives et sa contre-attaque (modes 10 et 3) sont des
# RÉACTIONS au joueur : jamais mises en commun. Retenue : +0x420 tenue au-dessus de zéro, seulement en mode 0
# (les autres modes s'en servent). Lancement : mode et sous-état d'un seul bloc, depuis l'attente au sol.
# ---------------------------------------------------------------------------
GN_REPOS = 0x420            # s16
GN_REPOS_TENU, GN_REPOS_SEUIL = 30, 15
GN_ATTAQUES = {(1, 0), (1, 5), (2, 0), (2, 1)}


def gnd_lire(mem, b, bosses) -> dict | None:
    from synchro import boss_phase
    e = boss_phase.gnd_lire(mem, b, bosses)
    if e is None:
        return None
    e["sous"], e["repos"] = _s16(mem, b["base"] + boss_phase.GN_SOUS), _s16(mem, b["base"] + GN_REPOS)
    return None if e["sous"] is None or e["repos"] is None else e


def gnd_debut(avant: dict, e: dict):
    if avant["mode"] != 0 or e["mode"] not in (1, 2):
        return None
    if e["mode"] == 1:
        return [1, 0 if e["sous"] <= 3 else 5]
    return [2, 1 if e["sous"] in (1, 6) else 0]


def _gnd_libre(e: dict) -> bool:
    return e["mode"] == 0 and e["cine"] == 0 and e["demande"] == 0


def gnd_lancer(mem, b, bosses, e: dict, v: list) -> str:
    from synchro import boss_phase
    if len(v) < 2 or (v[0], v[1]) not in GN_ATTAQUES or e["cine"] != 0 or e["mode"] in (4, 5, 20, 21, 30):
        return INUTILE
    if not _gnd_libre(e) or e["sous"] not in (1, 2):
        return ATTENDRE
    mem.write(b["base"] + boss_phase.GN_MODE, struct.pack(">hh", v[0], v[1]))
    return FAIT


def gnd_tenir(mem, b, bosses, e: dict) -> bool:
    if not _gnd_libre(e) or not 0 <= e["repos"] < GN_REPOS_SEUIL:
        return False
    mem.write(b["base"] + GN_REPOS, struct.pack(">h", GN_REPOS_TENU))
    return True


# ---------------------------------------------------------------------------
# Roi Cuirassé (238), le vrai combat en haut de la tour [DECOMP d_a_bdk.cpp wait et fly, fondus dans move en HD
# (0x0206D644) : choix au sol 0x0206E738..0x0206EAA0, décollage 0x0206E4EC..0x0206E504, choix en vol 0x0206DBC4/EC]
# Actions (+0x3DA) : 0 vol, 1 décollage, 2 atterrissage, 3 attente au sol, 4 bond, 5 coup de bec (qui reste planté :
# c'est là qu'on frappe son masque), 6 piétinement, 7 piqué, 8 vent des ailes.
# * au sol (action 3, sous-état 1) : quand la minuterie m2EC[0] (+0x400) est à 0 il choisit selon la distance de Link
#   (piétine, bec, vent, bond) ; il redécolle quand m2EC[3] (+0x406) est à 0. Retenue : les deux minuteries tenues
#   au-dessus de zéro, seulement en attente au sol (ailleurs +0x400 est la minuterie de l'action en cours).
# * en vol (action 0) il n'y a PAS de minuterie (il choisit dès qu'il arrive à son point) : un suiveur en vol n'est
#   pas retenu ; il fait seulement le piqué ou l'atterrissage de la référence s'il vole à ce moment-là.
# Lancement : action et sous-état 0 d'un seul bloc. Chaque boss vise son propre Link.
# Pas essayé au banc (sa salle fige les jeux cachés) : tests hors ligne et relecture du code HD seulement.
# ---------------------------------------------------------------------------
RC_T_SOL = 0x400            # s16 m2EC[0]
RC_T_ENVOL = 0x406          # s16 m2EC[3]
RC_TENU, RC_SEUIL = 60, 25
RC_VOL, RC_ATTENTE = 0, 3
RC_DU_SOL = (1, 4, 5, 6, 8)     # décolle, bond, bec, piétine, vent
RC_DU_VOL = (2, 7)              # atterrit, piqué


def roi_lire(mem, b, bosses) -> dict | None:
    base = b["base"]
    e = {"tour": _u8(mem, base + boss_mort.RC_TOUR), "action": _s16(mem, base + boss_mort.RC_ACTION),
         "sous": _s16(mem, base + boss_mort.RC_SOUS), "demo": _s16(mem, base + boss_mort.RC_DEMO),
         "t_sol": _s16(mem, base + RC_T_SOL), "t_envol": _s16(mem, base + RC_T_ENVOL)}
    return None if None in e.values() or e["tour"] != 0 or not 0 <= e["action"] < 0x64 else e


def roi_debut(avant: dict, e: dict):
    if avant["action"] == RC_ATTENTE and e["action"] in RC_DU_SOL:
        return [e["action"]]
    if avant["action"] == RC_VOL and e["action"] in RC_DU_VOL:
        return [e["action"]]
    return None


def roi_lancer(mem, b, bosses, e: dict, v: list) -> str:
    if e["demo"] != 0 or e["action"] in (9, 0xA, 0xF):
        return INUTILE
    if v[0] in RC_DU_SOL:
        if e["action"] != RC_ATTENTE:
            return INUTILE if e["action"] == RC_VOL else ATTENDRE
        if e["sous"] not in (1, 2):
            return ATTENDRE
    elif v[0] in RC_DU_VOL:
        if e["action"] != RC_VOL:
            return INUTILE
    else:
        return INUTILE
    mem.write(b["base"] + boss_mort.RC_ACTION, struct.pack(">hh", v[0], 0))
    return FAIT


def roi_tenir(mem, b, bosses, e: dict) -> bool:
    if e["demo"] != 0 or e["action"] != RC_ATTENTE:
        return False
    fait = False
    for off, t in ((RC_T_SOL, e["t_sol"]), (RC_T_ENVOL, e["t_envol"])):
        if 0 <= t < RC_SEUIL:
            mem.write(b["base"] + off, struct.pack(">h", RC_TENU))
            fait = True
    return fait


MIROIRS: dict[int, Miroir] = {
    238: Miroir(roi_lire, roi_debut, roi_lancer, roi_tenir,
                {1: "décollage", 2: "atterrissage", 4: "bond", 5: "coup de bec", 6: "piétinement", 7: "piqué",
                 8: "vent"}),
    246: Miroir(gnd_lire, gnd_debut, gnd_lancer, gnd_tenir, {1: "bond", 2: "frappe"}),
    243: Miroir(pg1_lire, pg1_debut, pg1_lancer, pg1_tenir, {1: "attaque"}),
    244: Miroir(pg2_lire, pg2_debut, pg2_lancer, pg2_tenir, {1: "retombée"}),
    245: Miroir(pg3_lire, pg3_debut, pg3_lancer, pg3_tenir, {1: "charge"}),
    240: Miroir(gohdan_lire, gohdan_debut, gohdan_lancer, gohdan_tenir,
                {10: "poing", 11: "claque", 12: "mains jointes", 13: "balayage", 14: "rayon"}),
    211: Miroir(jalhalla_lire, jalhalla_debut, jalhalla_lancer, jalhalla_tenir,
                {1: "rire", 2: "souffle", 3: "boules de feu", 4: "flammes", 5: "saut", 6: "charge"}),
    235: Miroir(kalle_lire, kalle_debut, kalle_lancer, kalle_tenir, {1: "fouet", 2: "pieds sous terre"}),
    217: Miroir(molgera_lire, molgera_debut, molgera_lancer, molgera_tenir, {5: "il avale", 10: "piqué"}),
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class BossMiroirSync:
    """``tick(mem, now, stage, bosses, free, can)`` à chaque échantillon ; ``receive(msg, me)`` ; ``take_out()``."""

    def __init__(self, enabled: bool = False, group: str = "default", clock=time.monotonic,
                 exclus: frozenset = frozenset()):
        self.enabled, self.group, self.clock = bool(enabled), group, clock
        self.exclus = frozenset(exclus)
        self.stage: str | None = None
        self.me: int | None = None
        self.avant: dict[str, dict] = {}
        self.pids: dict[str, int] = {}
        self.ref: dict[str, int] = {}               # clé -> joueur de référence (dit par le serveur)
        self.ref_t: dict[str, float] = {}           # clé -> dernière nouvelle de la référence
        self.att_recue: dict[str, dict] = {}        # clé -> {v, t}
        self.att_vue: dict[str, int] = {}           # clé -> dernier numéro d'attaque reçu
        self.envoye_t: dict[str, float] = {}
        self.tenue_t: dict[str, float] = {}
        self.att_n = 0
        self.out: list[dict] = []
        self.emis: list[float] = []
        self.notes: list[str] = []
        self.dit: set = set()
        self.annoncees = self.lancees = self.perdues = self.tenues = 0

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
        if me is not None:
            self.me = me
        rec = valide(msg)
        if rec is None or rec["stage"] != self.stage or boss_pv.key_proc(rec["key"]) in self.exclus:
            return 0
        key, now = rec["key"], self.clock()
        ref, de = msg.get("ref"), msg.get("from")
        if _int(ref):
            if self.ref.get(key) != ref:
                self.att_recue.pop(key, None)
            self.ref[key] = ref
        if de is not None and de == self.ref.get(key) and de != self.me:
            self.ref_t[key] = now                   # la référence est là
            att = rec.get("att")
            if att is not None and att["n"] != self.att_vue.get(key):
                self.att_vue[key] = att["n"]
                self.att_recue[key] = {"v": att["v"], "t": now}
                return 1
        return 0

    def reset_network(self) -> None:
        self.ref.clear()
        self.ref_t.clear()
        self.att_recue.clear()
        self.envoye_t.clear()
        self.out.clear()

    def _nouvelle_stage(self, stage: str | None) -> None:
        self.stage = stage
        for d in (self.avant, self.pids, self.ref, self.ref_t, self.att_recue, self.att_vue, self.envoye_t,
                  self.tenue_t):
            d.clear()
        self.out.clear()
        self.dit.clear()

    def _envoyer(self, key: str, now: float, att: dict | None = None) -> None:
        msg = {"type": "bossm", "group": self.group, "stage": self.stage, "key": key}
        if att is not None:
            msg["att"] = att
            self.out.append(msg)                    # une attaque n'est jamais remplacée par un battement
        else:
            self.out = [m for m in self.out if m["key"] != key or "att" in m] + [msg]
        self.envoye_t[key] = now

    def suit(self, key: str, now: float) -> bool:
        return (self.me is not None and key in self.ref and self.ref[key] != self.me
                and now - self.ref_t.get(key, -1e9) < REF_SILENCE)

    def tick(self, mem, now: float | None, stage: str | None, bosses: list | None, free: bool, can: bool) -> int:
        """Renvoie le nombre d'attaques lancées chez nous."""
        if not self.enabled:
            return 0
        now = self.clock() if now is None else now
        if stage != self.stage:
            self._nouvelle_stage(stage)
        if stage is None or not bosses:
            return 0
        lancees = 0
        for b in bosses:
            mi = MIROIRS.get(b.get("proc"))
            if mi is None or b["proc"] in self.exclus:
                continue
            t = boss_pv.BOSS_PV[b["proc"]]
            if b.get("max") != t.ident:
                continue
            key = boss_pv.boss_key(b["proc"], b.get("params", 0))
            e = mi.lire(mem, b, bosses)
            if e is None:
                continue
            if self.pids.get(key) != b["pid"]:
                self.pids[key] = b["pid"]
                self.avant.pop(key, None)
            avant, self.avant[key] = self.avant.get(key), e
            suis_ref = self.me is not None and self.ref.get(key) == self.me
            # 1. la référence annonce chaque attaque qui commence ; tout le monde signale sa présence. Seulement en jeu
            # libre : pendant une cinématique (l'introduction du boss chez celui qui vient d'entrer) on se tait, donc
            # on ne devient pas la référence, et si on l'était les autres reprennent leurs attaques après
            # REF_SILENCE au lieu d'attendre devant un boss immobile
            if not free:
                continue
            if suis_ref and avant is not None:
                v = mi.debut(avant, e)
                if v:
                    self.att_n += 1
                    att = attaque_valide({"n": self.att_n, "v": list(v)})
                    if att is not None:
                        self._envoyer(key, now, att)
                        self.annoncees += 1
            if now - self.envoye_t.get(key, -1e9) >= RAPPEL:
                self._envoyer(key, now)
            # 2. un suiveur ne choisit plus ses attaques, et lance celle de la référence
            if not self.suit(key, now) or not (free and can):
                continue
            recue = self.att_recue.get(key)
            if recue is not None and now - recue["t"] > ATT_AGE:
                del self.att_recue[key]
                self.perdues += 1
                recue = None
            if not boss_mort._ecriture_permise(mem, b):
                continue
            if ("suit", key) not in self.dit:
                self.dit.add(("suit", key))
                self.notes.append(f"{t.nom} fait les mêmes attaques au même moment chez tous les joueurs")
            if recue is not None:
                res = mi.lancer(mem, b, bosses, e, recue["v"])
                if res != ATTENDRE:
                    del self.att_recue[key]
                if res == FAIT:
                    self.lancees += 1
                    lancees += 1
                    self.avant.pop(key, None)
                    self.tenue_t[key] = now + PAUSE_APRES_LANCER - PAS_TENUE
                    continue
                if res == INUTILE:
                    self.perdues += 1
            if now - self.tenue_t.get(key, -1e9) >= PAS_TENUE and mi.tenir(mem, b, bosses, e):
                self.tenue_t[key] = now
                self.tenues += 1
        return lancees

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        return (f"{self.annoncees} annoncée(s), {self.lancees} lancée(s), {self.perdues} perdue(s), "
                f"{self.tenues} retenue(s)")


# ---------------------------------------------------------------------------
# Serveur
# ---------------------------------------------------------------------------
class MiroirTable:
    """La référence par (groupe, stage, clé)."""

    def __init__(self, clock=time.monotonic):
        self.ref: dict[tuple[str, str, str], int] = {}
        self.ref_t: dict[tuple[str, str, str], float] = {}
        self.clock = clock

    def rapport(self, group: str, rec: dict, pid: int, presents) -> tuple[int, bool]:
        """Rend (référence, elle vient de changer). Le premier à se signaler ; absente : l'auteur la remplace."""
        k = (group, rec["stage"], rec["key"])
        old = self.ref.get(k)
        if old is None or old not in presents:
            self.ref[k] = pid
            return pid, old != pid
        return old, False

    def frappe(self, group: str, stage: str, key: str, pid: int, now: float | None = None) -> bool:
        """``pid`` vient de frapper le boss : il devient la référence. True si elle a changé."""
        k = (group, stage, key)
        old = self.ref.get(k)
        if old is None or old == pid:
            return False
        now = self.clock() if now is None else now
        if now - self.ref_t.get(k, float("-inf")) < REF_CHANGE_MIN:
            return False
        self.ref_t[k] = now
        self.ref[k] = pid
        return True

    def gc(self, present: set[tuple[str, str]]) -> None:
        for k in [k for k in self.ref if (k[0], k[1]) not in present]:
            del self.ref[k]
            self.ref_t.pop(k, None)
