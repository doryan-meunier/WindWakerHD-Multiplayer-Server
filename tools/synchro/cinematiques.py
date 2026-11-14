"""Synchro v2, module 5 (C1) : dire QUELLE cinématique joue un autre joueur, seulement s'il est proche.

Étude : docs/etudes/sync2_ia_boss_cine.md §3.c « c1 » (la section « Vérification » finale prime :
``state.busy`` RESTE ``1`` — le serveur fait ``msg.get("busy") in (1, True)`` pour A11 — et le
détail part dans un champ SÉPARÉ ``state.busy_info``).

Choix de l'utilisateur : les cinématiques ne sont PAS jouées chez les autres, ni gelées ; on se
contente d'afficher « <pseudo> est dans une cinématique (<libellé>) », et seulement quand ce
joueur est dans la même stage ET la même salle, à moins de ``DIST_DEFAUT`` unités (``--cine-dist``).

* Émetteur (``read_busy_info``, lecture seule) : pendant un événement (A11 occupé), on lit
  ``dEvt_control_c`` (``0x10474280``) : ``mMode`` (+0xC2 : 1 dialogue, 2 démo, 3 COMPULSORY),
  ``mEventId`` (+0xD8), ``mPt1`` / ``mPt2`` (+0xC4 / +0xC8, ids de process). Les ids sont
  convertis en numéros de proc par la liste d'acteurs déjà lue par l'échantillonneur
  (``convPId`` côté Python). ``mPt1/mPt2`` ne sont JAMAIS remis à -1 (A0) : on ne les lit que si
  ``mMode != 0``. Champ envoyé : ``busy_info = {m, e, p1, p2, s}`` (s = secondes depuis le début).
* Receveur (``CineNotice``) : un message par épisode (et par changement de libellé), seulement si
  le joueur est proche ; « fin de cinématique » seulement si le début avait été annoncé.
  Un ancien client n'envoie pas ``busy_info`` : libellé générique « cinématique ».

Rien n'est écrit dans le jeu.
"""
from __future__ import annotations

import math
import struct

from jeu import progress as prog
from synchro.boss import BOSS_PROCS

PROC_TBOX = 292                       # daTbox_c [LIVE] (synchro/cles.py)
DOOR_PROCS = frozenset({300, 301})    # daDoor10_c, daDoor12_c [LIVE] (synchro/vivants.py)
MODE_TALK, MODE_DEMO, MODE_COMPULSORY = 1, 2, 3
DIST_DEFAUT = 2500.0                  # unités du jeu (Link mesure ~117) : « proche » = même salle et < 25 m environ
NO_PROC = -1
GENERIQUE = "cinématique"            # libellé sans détail (ancien client, fin d'événement)

# synchro v3 (monde vivant, docs/etudes/sync3_monde_vivant.md §4) : à qui parle-t-on ? Pendant un dialogue
# (mMode 1), mPt1 / mPt2 sont Link et son interlocuteur (dEvt_control_c::setParam via fopAcM_orderTalkEvent /
# fopAcM_orderSpeakEvent [DECOMP d_event.cpp, f_op_actor_mng.cpp]). Procs HD lus dans la table des noms du jeu
# (0x101EBFDC, cking.rpx) ; noms français d'après la classe zeldaret/tww (d_a_npc_<nom>) [HYPO : à voir en jeu].
PROC_LINK = 168
PNJ: dict[int, str] = {
    165: "le Lion Rouge",       # Ship (le bateau parle)
    318: "Orca",                # Ji1
    321: "Arielle",             # Ls1 (petite sœur)
    331: "Grand-mère",          # Ob1
    325: "Tingle",              # Tc
    367: "Médolie",             # Md1
    334: "Makar",               # Cb1
    345: "Terry",               # Bs1 / Bs2 (marchand)
    426: "Tetra",               # Zl1
    369: "la Grande Fée",       # BigElf
    116: "l'Arbre Mojo",        # De1
    322: "un pirate",           # P1a/P1b/P1c
    323: "un pirate",           # P2a/P2b/P2c
    373: "un habitant",         # Uo/Ub/Uw/Um/Sa/Ug (habitants de Mercantîle)
    314: "une statue",          # Os / Os1 / Os2 (Tour des Dieux)
}


def _proc(pid: int, procs: dict | None) -> int:
    if pid in (0, 0xFFFFFFFF) or not procs:
        return NO_PROC
    p = procs.get(pid)
    return p if isinstance(p, int) and not isinstance(p, bool) else NO_PROC


def read_busy_info(mem, procs: dict | None, seconds: float = 0.0) -> dict | None:
    """Détail de l'événement en cours (lecture seule) ; None si illisible ou si mMode == 0
    (fin d'événement : mbEndProc seul, ou anti-rebond A0)."""
    ev = prog.read_event(mem)
    if ev is None or ev.mode == 0:
        return None
    raw = mem.try_read(prog.SYM_EVT_CTRL + prog.EVT_PT, 8)
    p1, p2 = struct.unpack(">II", raw) if raw is not None else (0xFFFFFFFF, 0xFFFFFFFF)
    return {"m": int(ev.mode), "e": int(ev.event_id), "p1": _proc(p1, procs), "p2": _proc(p2, procs),
            "s": max(0, min(9999, int(seconds)))}


def _small_int(x, lo: int, hi: int, default: int) -> int:
    return x if isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi else default


def valid_info(raw) -> dict | None:
    """``busy_info`` reçu du réseau, types vérifiés (None si absent ou invalide)."""
    if not isinstance(raw, dict):
        return None
    m = _small_int(raw.get("m"), 0, 255, -1)
    if m < 0:
        return None
    return {"m": m, "e": _small_int(raw.get("e"), -32768, 32767, -1),
            "p1": _small_int(raw.get("p1"), -1, 0x7FFF, NO_PROC), "p2": _small_int(raw.get("p2"), -1, 0x7FFF, NO_PROC),
            "s": _small_int(raw.get("s"), 0, 9999, 0)}


def label(info: dict | None) -> str:
    """Libellé court (§3.c) : dialogue, coffre, porte, cinématique de boss, scène, cinématique."""
    if not info:
        return "cinématique"
    m, procs = info.get("m"), (info.get("p1"), info.get("p2"))
    if m == MODE_TALK:
        return "dialogue"
    if m == MODE_DEMO:
        if PROC_TBOX in procs:
            return "coffre"
        if any(p in DOOR_PROCS for p in procs):
            return "porte"
        for p in procs:
            if p in BOSS_PROCS:
                return f"cinématique de boss : {BOSS_PROCS[p]}"
        return "cinématique"
    if m == MODE_COMPULSORY:
        return "scène"
    return "cinématique"


def partenaire(info: dict | None) -> str | None:
    """Nom de l'interlocuteur d'un dialogue (None : pas un dialogue, ou personnage inconnu)."""
    if not info or info.get("m") != MODE_TALK:
        return None
    for p in (info.get("p2"), info.get("p1")):
        if p != PROC_LINK and p in PNJ:
            return PNJ[p]
    return None


def message(nom: str, info: dict | None, lab: str) -> str:
    """« X parle à Tingle » pour un dialogue avec un personnage connu, sinon « X est dans une cinématique (…) »."""
    qui = partenaire(info)
    return f"{nom} parle à {qui}" if qui else f"{nom} est dans une cinématique ({lab})"


def near(me: dict | None, other: dict | None, dist: float) -> bool:
    """Même stage, même salle et à moins de ``dist`` unités (dist < 0 : partout, ancien comportement)."""
    if not other:
        return False
    if dist < 0:
        return True                               # partout (même avant notre premier échantillon)
    if not me:
        return False
    if other.get("stage") != me.get("stage") or other.get("room") != me.get("room"):
        return False
    a, b = me.get("pos"), other.get("pos")
    if not (isinstance(a, list) and isinstance(b, list) and len(a) == 3 and len(b) == 3):
        return False
    try:
        return math.dist(a, b) <= dist
    except TypeError:
        return False


class CineNotice:
    """Messages « X est dans une cinématique (libellé) » des AUTRES joueurs, filtrés par la distance.

    ``observe(pid, nom, state, me)`` à chaque ``state`` reçu ; renvoie la liste des messages à
    afficher (au plus un par appel)."""

    def __init__(self, enabled: bool = True, dist: float = DIST_DEFAUT):
        self.enabled, self.dist = enabled, dist
        self.announced: dict[int, str] = {}     # joueur -> libellé annoncé pendant son épisode en cours
        self.shown = 0

    def observe(self, pid: int, nom: str, state: dict, me: dict | None) -> list[str]:
        if not self.enabled:
            return []
        if not state.get("busy"):
            if self.announced.pop(pid, None) is not None:
                return [f"{nom} : fin de cinématique"]
            return []
        info = valid_info(state.get("busy_info"))
        lab = label(info)
        qui = partenaire(info)
        if qui:
            lab = f"{lab} avec {qui}"             # libellé distinct : un nouvel interlocuteur est redit
        if not near(me, state, self.dist):
            return []                             # trop loin : rien (épisode non annoncé)
        old = self.announced.get(pid)
        if old == lab or (old is not None and lab == GENERIQUE):
            # même libellé, ou libellé générique en cours d'épisode (fin d'événement pendant l'anti-
            # rebond A0 : mMode déjà à 0, plus de busy_info) : on ne redit pas « cinématique »
            return []
        self.announced[pid] = lab
        self.shown += 1
        return [message(nom, info, lab)]

    def forget(self, pid: int) -> None:
        self.announced.pop(pid, None)

    def visible(self, state: dict, me: dict | None) -> str | None:
        """Libellé à afficher dans le tableau des joueurs (None : libre ou trop loin)."""
        if not self.enabled or not state.get("busy") or not near(me, state, self.dist):
            return None
        info = valid_info(state.get("busy_info"))
        qui = partenaire(info)
        return f"parle à {qui}" if qui else label(info)

    def reset(self) -> None:
        self.announced.clear()
