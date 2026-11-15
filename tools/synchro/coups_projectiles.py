"""Coups des PROJECTILES de notre Link (flèches, bombes, boomerang, grappins) sur les monstres distants : réaction
NATIVE chez l'autorité, retour immédiat chez l'attaquant. MOD NATIF DU PORTAGE SEULEMENT.

Suite du lot L4 de la latence (synchro/coups_monstres.py, docs/etudes/COMBAT.md « 6b », docs/etudes/rollback_netcode.md
§5). Le développement pour Cemu est arrêté : aucun pack Cemu n'annonce le bit 26 ; avec Cemu, des deux côtés ou d'un
seul, tout reste comme avant (aucun coup de projectile sur une marionnette n'est vu). Option du client
``--coups-projectiles`` (activée par ``--coop-donjon``), ``--no-coups-projectiles``.

1. **Chez l'attaquant** (mod natif, bit 26 : natif/mod_wwmp/projectiles.c) : chaque marionnette à IA suspendue reçoit un
   TG (cylindre de ``taille_tg`` sur sa position) qui n'accepte que ``MASQUE`` (flèches, bombes, boomerang, grappins ;
   jamais l'épée ni le marteau, qui gardent la lame lue par le client). Le jeu fait le croisement : la flèche s'y
   plante, le boomerang revient, la bombe souffle, et l'étincelle du coup est celle du jeu. Le mod pousse chaque coup
   dans une file et joue tout de suite le son du coup (la fonction que cc_at_check utilise) : le retour local, à ~0 ms.
   Le client lit la file (``Detecteur.pas``) et envoie un ``hit`` à l'autorité : ``dmg`` (repli : les PV écrits) et,
   seulement si l'autorité de la zone l'annonce (``cp`` du message ``authority`` : le serveur le pose quand son client
   a annoncé « coups_proj » dans hello, c'est-à-dire un jeu avec le bit 26), le champ optionnel ``injp`` : le ``inj``
   du lot L4 (point d'impact relatif, attributs de l'attaque lus dans l'objet d'attaque du jeu, direction) plus
   ``arme`` et ``src`` (position du projectile relative au monstre : le recul part de là).
2. Le serveur relaie ``injp`` validé, et seulement à une autorité qui l'a annoncé (``champ_serveur``) ; un ancien
   serveur le retire.
3. **Chez l'autorité** (bit 26) : CALL 14 avec l'arme : un faux acteur typé (Arrow 472, Bomb2 295, Boom 432, Himo2 446,
   neutre pour le grappin à crochet) pour propriétaire ; at_power_check, cc_at_check et la réaction de l'espèce font
   comme en solo : étourdi par le boomerang, gelé par la flèche de glace, brûlé par celle de feu, soufflé par la bombe,
   objet volé par le grappin (créé chez l'autorité). Sinon (pas le bit 26, pas d'``injp``, refus) : les PV écrits.

Rien n'a été vu en jeu (docs/dev/tests/PLAN_TEST_SYNCHRO.md, cas ZP1 à ZP12).
"""
from __future__ import annotations

import math
import time

from synchro import coups_monstres as cm

# --- protocole et bornes (identiques à jeu/mailbox.py, sans l'importer : le serveur n'en dépend pas) ---------------
ARMES = {"fleche": 1, "bombe": 2, "boomerang": 3, "grappin": 4, "crochet": 5}
SRC_MAX = 400.0                 # u : source du coup relative au monstre (le mod refuse au-delà)
SRC_DEFAUT = 120.0              # u : source reconstituée depuis la direction du coup quand le projectile est illisible
FEAT = "coups_proj"             # hello : ce client sait rejouer ``injp`` ET son jeu a le bit 26

# --- types d'attaque (dCcD_ObjAtType, COMBAT.md §1.1) et propriétaires (branches d'at_power_check, 0x02518DB0) ------
AT_BOMBE, AT_BOOMERANG, AT_FLECHE, AT_CROCHET = 0x20, 0x40, 0x4000, 0x8000
AT_FEU_FLECHE, AT_GLACE_FLECHE, AT_LUMIERE_FLECHE = 0x40000, 0x80000, 0x100000
AT_GRAPPIN = 0x8000000          # [HYPO] type de la corde du grappin (Himo2) : classé par son propriétaire (446) d'abord
FLECHES = AT_FLECHE | AT_FEU_FLECHE | AT_GLACE_FLECHE | AT_LUMIERE_FLECHE
MASQUE = FLECHES | AT_BOMBE | AT_BOOMERANG | AT_CROCHET | AT_GRAPPIN
INTERDITS = 0x00010002          # épée, marteau : le mod les retire du masque
PROC_ARROW, PROC_BOMB, PROC_BOMB2, PROC_BOOM, PROC_HIMO2, PROC_LINK = 472, 294, 295, 432, 446, 168
PAR_PROC = {PROC_ARROW: "fleche", PROC_BOMB: "bombe", PROC_BOMB2: "bombe", PROC_BOOM: "boomerang",
            PROC_HIMO2: "grappin"}

# --- TG des marionnettes : (rayon, hauteur, dy) par espèce [ESTIMÉ, à régler en jeu : PLAN_TEST_SYNCHRO ZP2] ----------
TAILLE_DEFAUT = (50.0, 150.0, 0.0)
TAILLES = {
    188: (75.0, 230.0, 0.0),    # Moblin
    189: (50.0, 150.0, 0.0),    # Bokoblin
    215: (35.0, 60.0, -20.0),   # Keese (vole : centré sur son corps)
}
COOLDOWN = 0.25                 # s : un même projectile sur la même marionnette (en plus du NoConHit du jeu)


def taille_tg(proc) -> tuple[float, float, float]:
    return TAILLES.get(proc, TAILLE_DEFAUT)


def classer(e: dict) -> str | None:
    """Arme d'une entrée de la file (``jeu.mailbox.lire_entree_prj``), d'après le propriétaire de l'attaque puis son
    type ; None : rien de notre Link (le coup est ignoré, comme avant)."""
    proc, typ = e.get("proc"), int(e.get("type") or 0)
    if proc in PAR_PROC:
        return PAR_PROC[proc]
    if typ & INTERDITS:
        return None
    if typ & FLECHES:
        return "fleche"
    if typ & AT_BOMBE:
        return "bombe"
    if typ & AT_BOOMERANG:
        return "boomerang"
    if typ & AT_CROCHET:
        return "crochet"
    if typ & AT_GRAPPIN:
        return "grappin"
    return None


def _fini(v) -> list[float] | None:
    try:
        out = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    return out if len(out) == 3 and all(math.isfinite(x) for x in out) else None


def source(e: dict, pos_monstre, pos_link=None) -> list[float]:
    """Position d'où part le coup, relative au monstre (le recul de cc_at_check part de là) : le projectile lui-même
    (bombe : son centre ; flèche, boomerang : là où il touche), à au moins 5 u ; sinon à SRC_DEFAUT u à rebours de la
    direction du coup ; sinon de notre Link. Bornée à SRC_MAX."""
    m = _fini(pos_monstre) or [0.0, 0.0, 0.0]
    p = _fini(e.get("src")) if e.get("src") is not None else None
    if p is not None:
        d = [p[k] - m[k] for k in range(3)]
        if math.hypot(d[0], d[2]) >= 5.0:
            return _borner(d)
    v = _fini(e.get("rvec")) or [0.0, 0.0, 0.0]
    n = math.hypot(v[0], v[2])
    if n > 1e-3:
        return _borner([-v[0] / n * SRC_DEFAUT, 0.0, -v[2] / n * SRC_DEFAUT])
    lk = _fini(pos_link) if pos_link is not None else None
    if lk is not None:
        d = [lk[0] - m[0], 0.0, lk[2] - m[2]]
        n = math.hypot(d[0], d[2])
        if n > 1e-3:
            return _borner([d[0] / n * SRC_DEFAUT, 0.0, d[2] / n * SRC_DEFAUT])
    return [0.0, 0.0, 0.0]


def _borner(d) -> list[float]:
    n = max(abs(x) for x in d)
    if n > SRC_MAX:
        d = [x * SRC_MAX / n for x in d]
    return [round(max(-SRC_MAX, min(SRC_MAX, x)), 1) for x in d]


def info_coup(e: dict, pos_monstre, pos_link=None) -> dict | None:
    """``injp`` d'une entrée de la file (sans validation de réseau : voir ``valid_injp``) ; None si illisible."""
    arme = classer(e)
    if arme is None:
        return None
    pt = _fini(e.get("pos"))
    m = _fini(pos_monstre)
    if pt is None or m is None:
        return None
    v = _fini(e.get("rvec")) or [0.0, 0.0, 0.0]
    if any(abs(x) >= 1e5 for x in v):
        v = [0.0, 0.0, 0.0]
    at = cm.valid_at({"type": int(e.get("type") or 0) or AT_FLECHE, "atp": min(int(e.get("atp") or 0), cm.ATP_MAX),
                      "spl": min(int(e.get("spl") or 0), cm.SPL_MAX), "se": int(e.get("se") or 0) & 0xFF,
                      "mark": int(e.get("mark") or 0) & 0xFF, "gat": int(e.get("gat") or 0) & 0xFFFFFFFF,
                      "mtrl": int(e.get("mtrl") or 0) & 0xFF})
    if at is None:
        return None
    return {"rel": cm.point_relatif(pt, m), "at": at, "cut": None, "vec": [round(x, 4) for x in v],
            "arme": arme, "src": source(e, m, pos_link), "pt": [round(x, 1) for x in pt]}


def degats(e: dict) -> int:
    """PV du repli (autorité sans le bit 26 ou ancien serveur) : m_dmg du TG (Atp, 0 si paré), sinon l'Atp ; 0 pour le
    boomerang et les grappins, qui n'en font pas au jeu (étourdi, objet volé)."""
    if classer(e) in ("boomerang", "grappin", "crochet"):
        return 0
    return max(0, min(cm.ATP_MAX, int(e.get("dmg") or e.get("atp") or 0)))


def valid_injp(injp) -> dict | None:
    """Champ ``injp`` d'un ``hit`` (serveur et autorité) : ``inj`` du lot L4 + ``arme`` + ``src`` ; None sinon."""
    if not isinstance(injp, dict):
        return None
    base = cm.valid_inj({k: injp.get(k) for k in ("rel", "vec", "at", "cut")})
    arme = injp.get("arme")
    src = cm._f3(injp.get("src"), SRC_MAX)
    if base is None or not isinstance(arme, str) or arme not in ARMES or src is None:
        return None
    return {**base, "cut": None, "arme": arme, "src": src}


def champ_injp(info: dict | None) -> dict | None:
    """``injp`` à envoyer (sans le point absolu, local)."""
    if not isinstance(info, dict):
        return None
    return valid_injp({k: info.get(k) for k in ("rel", "vec", "at", "arme", "src")})


def champ_serveur(msg, autorite_ok: bool) -> dict:
    """Serveur : ``injp`` validé à ajouter au ``hit`` relayé, seulement si l'autorité a annoncé ``FEAT``."""
    if not autorite_ok or not isinstance(msg, dict):
        return {}
    v = valid_injp(msg.get("injp"))
    return {"injp": v} if v is not None else {}


# --- chez l'attaquant ---------------------------------------------------------------------------------------------
class Detecteur:
    """``--coups-projectiles`` chez l'attaquant : arme le mod (cfg, masque, cylindres des emplacements), lit la file
    des coups portés et rend les ``hit`` à envoyer. Sans le bit 26 (Cemu, mod ancien) : rien, jamais d'écriture."""

    def __init__(self, enabled: bool = False, son: bool = True, clock=time.monotonic):
        self.enabled = bool(enabled)
        self.son = bool(son)
        self.clock = clock
        self.seq: int | None = None
        self.cfg_ecrit: tuple | None = None
        self.tailles: dict[int, tuple] = {}
        self.dernier: dict[tuple, float] = {}
        self.vus = self.ignores = self.perdus = self.inconnus = 0

    def _armer(self, mb, cfg: int) -> None:
        if self.cfg_ecrit != (cfg, MASQUE):
            mb.prj_regler(cfg, MASQUE if cfg else 0)
            self.cfg_ecrit = (cfg, MASQUE)

    def couper(self, mb) -> None:
        """Option coupée, fin de session : TG retirés (le mod remet « réglé » à 0)."""
        if mb is not None and self.cfg_ecrit is not None and getattr(mb, "coups_proj_ok", False):
            try:
                mb.prj_regler(0, 0)
            except Exception:
                pass
        self.cfg_ecrit = None
        self.seq = None
        self.tailles.clear()

    def pas(self, mb, slots: dict, acteur, pos_link=None) -> list[dict]:
        """``slots`` : clé -> (emplacement, id de process) des marionnettes ; ``acteur(clé)`` -> (pos, proc) de la
        marionnette dans notre jeu, ou None. Rend [{key, dmg, arme, injp, pt}] des coups de ce tour."""
        if not self.enabled or mb is None or not getattr(mb, "coups_proj_ok", False):
            return []
        self._armer(mb, 1 | (2 if self.son else 0))
        par_slot = {}
        for key, (i, pid) in list(slots.items()):
            a = acteur(key)
            if a is None:
                continue
            pos, proc = a
            par_slot[i] = (key, pid, pos)
            t = taille_tg(proc)
            if self.tailles.get(i) != t:
                mb.prj_tg(i, *t)
                self.tailles[i] = t
        if self.seq is None:
            self.seq = mb.prj_seq()             # ce qui précède notre armement ne compte pas
            return []
        self.seq, entrees, perdus = mb.prj_file(self.seq)
        self.perdus += perdus
        now = self.clock()
        out = []
        for e in entrees:
            self.vus += 1
            k = par_slot.get(e.get("slot"))
            if k is None or k[1] != e.get("pid"):
                self.inconnus += 1          # marionnette changée entre le coup et la lecture
                continue
            key, _pid, pos = k
            info = info_coup(e, pos, pos_link)
            if info is None:
                self.ignores += 1
                continue
            cle = (key, e.get("apid"))
            if now - self.dernier.get(cle, float("-inf")) < COOLDOWN:
                continue
            self.dernier[cle] = now
            out.append({"key": key, "dmg": degats(e), "arme": info["arme"], "injp": champ_injp(info),
                        "pt": info["pt"]})
        if len(self.dernier) > 256:
            self.dernier = {c: t for c, t in self.dernier.items() if now - t < COOLDOWN}
        return out

    def status(self) -> str:
        return (f"coups de projectiles vus : {self.vus}" + (f", ignorés : {self.ignores}" if self.ignores else "")
                + (f", perdus : {self.perdus}" if self.perdus else ""))


def message_hit(zone, h: dict, autorite_cp: bool) -> dict:
    """Le ``hit`` d'un coup de projectile : ``dmg`` toujours (repli), ``injp`` seulement vers une autorité qui l'annonce."""
    m = {"type": "hit", "zone": zone, "key": h["key"], "dmg": int(h.get("dmg", 0))}
    if autorite_cp and h.get("injp") is not None:
        m["injp"] = h["injp"]
    return m
