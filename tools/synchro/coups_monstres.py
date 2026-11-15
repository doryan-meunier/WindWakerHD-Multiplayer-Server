"""Coups d'un joueur distant sur un monstre : réaction NATIVE chez l'autorité et retour immédiat chez l'attaquant.

Lot L4 de la latence (docs/etudes/rollback_netcode.md §4, A4) = étape « 6b » de docs/etudes/COMBAT.md (option D,
« coup injecté d'une image »). Options du client : ``--coups-injectes`` et ``--retour-coup`` (activés par
``--coop-donjon``), ``--no-coups-injectes``, ``--no-retour-coup``. Il faut le pack v19 à jour (bit 25 des capacités,
pack Cemu ``WWMP_Mailbox`` ou mod natif du portage) ; sans lui, tout est comme avant.

Avant : la lame de notre Link touche une marionnette (détection du client, ``CemuSampler.detect_hits``), le message
``hit`` part à l'autorité, qui ÉCRIT les PV (+0x3A1) : ni recul, ni garde, ni gel, ni son ; et rien chez l'attaquant
avant l'instantané suivant (~105 ms, sans aucune réaction).

Maintenant :

1. **Chez l'attaquant**, au moment même du coup : le ``hit`` porte un champ optionnel ``inj`` : point d'impact relatif
   au monstre (``rel`` : base du cylindre d'attaque, ``RAYON`` / ``HAUTEUR``), attributs de l'attaque lus dans le
   collider de la lame de notre Link (``at`` : type, Atp, Spl, son, marque ; repli : épée, ``AT_EPEE``), coupe
   (``cut`` : mCutType, mCutCount, Link+0x3AC) et direction (``vec``). Et, si ``--retour-coup`` : la RECETTE 7 de CALL
   11 (étincelle 0x0D, son 0x2803 d'un coup d'épée sur un ennemi : synchro/effets_pack.py) au point d'impact, tout de
   suite (aucun aller-retour : le coup se voit et s'entend à 0 ms).
2. Le serveur relaie ``inj`` (``champs_hit``), validé ; un ancien serveur le retire (repli : PV écrits).
3. **Chez l'autorité** : si son pack a le bit 25 et que le fantôme de l'attaquant est là (sa copie du joueur sert de
   propriétaire à l'attaque), CALL 14 COUP_MONSTRE inscrit pendant UNE image un AT sur le corps du vrai monstre ; le jeu
   fait tout le reste comme en solo (garde au bouclier, recul, gel, étourdissement, son, mort et sa routine). Sinon
   (pack ancien, fantôme absent, monstre en laisse aux PV imposés, refus) : les PV écrits, comme avant. CALL 14 refuse
   moins de 2 images après une autre injection : un seul nouvel essai, ``ESSAI_TOT`` s plus tard.

Rien n'a été vu en jeu (docs/dev/tests/PLAN_TEST_SYNCHRO.md, cas ZL17 à ZL24).
"""
from __future__ import annotations

import math
import struct
import time

RAYON = 40.0                # u : cylindre d'attaque injecté (le pack accepte 5 à 100)
HAUTEUR = 80.0              # u : (10 à 300) ; centré sur le point d'impact
REL_XZ = 150.0              # u : point d'impact relatif au monstre, à plat (au-delà : borné)
REL_Y = (-50.0, 250.0)      # u : en hauteur (pieds à tête d'un Moblin)
REL_MAX = 200.0             # u : base du cylindre, bornes du pack (|c| <= 200)
ATP_MAX, SPL_MAX = 20, 15
ESSAI_TOT = 0.07            # s : CALL 14 « trop tôt » (-5) -> un nouvel essai après ce délai, puis les PV écrits
RETOUR_GAP = 0.1            # s entre deux retours locaux (recette 7)
# collider de la lame de Link (mAtCps[0], +0x7B1C) puis de l'attaque tournoyante (mAtCyl, +0x78BC) [LIVE, COMBAT.md §1.2]
LINK_AT_CPS, LINK_AT_CYL, LINK_CUT = 0x7B1C, 0x78BC, 0x3AC
AT_EPEE = {"type": 0x2, "atp": 1, "spl": 0, "se": 1, "mark": 0xD, "gat": 0, "mtrl": 0}
R_OK, R_TOT = 1, -5


def _f(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _f3(v, borne: float) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    out = [_f(x) for x in v]
    if any(x is None or abs(x) > borne for x in out):
        return None
    return out


def _int(v, lo: int, hi: int) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        return None
    return v


def valid_at(at) -> dict | None:
    if not isinstance(at, dict):
        return None
    out = {"type": _int(at.get("type"), 1, 0xFFFFFFFF), "atp": _int(at.get("atp"), 0, ATP_MAX),
           "spl": _int(at.get("spl", 0), 0, SPL_MAX), "se": _int(at.get("se", 0), 0, 0xFF),
           "mark": _int(at.get("mark", 0), 0, 0xFF), "gat": _int(at.get("gat", 0), 0, 0xFFFFFFFF),
           "mtrl": _int(at.get("mtrl", 0), 0, 0xFF)}
    return None if any(v is None for v in out.values()) else out


def valid_inj(inj) -> dict | None:
    """Champ ``inj`` d'un ``hit`` (serveur et autorité) ; None s'il manque ou sort des bornes (repli : PV écrits)."""
    if not isinstance(inj, dict):
        return None
    rel, vec, at = _f3(inj.get("rel"), REL_MAX), _f3(inj.get("vec"), 1e5), valid_at(inj.get("at"))
    cut = inj.get("cut")
    if cut is not None:
        if not isinstance(cut, (list, tuple)) or len(cut) != 2 or any(_int(c, 0, 0xFF) is None for c in cut):
            return None
        cut = [int(cut[0]), int(cut[1])]
    if rel is None or vec is None or at is None:
        return None
    return {"rel": rel, "vec": vec, "at": at, "cut": cut}


def champs_hit(msg) -> dict:
    """Serveur : ce qu'on ajoute au ``hit`` relayé à l'autorité (``inj`` validé), ou rien (ancien client)."""
    inj = valid_inj(msg.get("inj")) if isinstance(msg, dict) else None
    return {"inj": inj} if inj is not None else {}


# --- chez l'attaquant -------------------------------------------------------------------------------------
def lire_attaque(mem, link_base: int) -> tuple[dict, list[int] | None]:
    """(attributs de l'attaque, coupe) de NOTRE Link au moment du coup : collider de la lame s'il est inscrit (bit 1 de
    l'AT mSPrm), sinon celui de l'attaque tournoyante, sinon l'épée par défaut. Lecture seule ; jamais d'exception."""
    at = None
    for off in (LINK_AT_CPS, LINK_AT_CYL):
        try:
            raw = mem.try_read(link_base + off, 0x70)
        except Exception:
            raw = None
        if raw is None or len(raw) < 0x70:
            continue
        sprm, = struct.unpack_from(">I", raw, 0x00)
        typ, = struct.unpack_from(">I", raw, 0x10)
        if not sprm & 1 or not typ:
            continue
        gat, = struct.unpack_from(">I", raw, 0x50)
        at = valid_at({"type": typ, "atp": min(raw[0x14], ATP_MAX), "spl": min(raw[0x6F], SPL_MAX), "se": raw[0x6C],
                       "mark": raw[0x6E], "gat": gat, "mtrl": raw[0x6D]})
        if at is not None:
            break
    cut = None
    try:
        r = mem.try_read(link_base + LINK_CUT, 2)
    except Exception:
        r = None
    if r is not None and len(r) == 2:
        cut = [r[0], r[1]]
    return (at or dict(AT_EPEE)), cut


def point_relatif(point, pos_monstre) -> list[float]:
    """Base du cylindre injecté, relative à la position du monstre : le point d'impact borné (``REL_XZ``, ``REL_Y``),
    descendu de la demi-hauteur (cylindre centré sur l'impact), dans les bornes du pack."""
    dx = max(-REL_XZ, min(REL_XZ, float(point[0]) - float(pos_monstre[0])))
    dy = max(REL_Y[0], min(REL_Y[1], float(point[1]) - float(pos_monstre[1])))
    dz = max(-REL_XZ, min(REL_XZ, float(point[2]) - float(pos_monstre[2])))
    return [round(dx, 1), round(max(-REL_MAX, min(REL_MAX, dy - HAUTEUR / 2)), 1), round(dz, 1)]


def direction(de, vers) -> list[float]:
    """Vecteur d'attaque : de l'attaquant vers le monstre, à plat, unitaire (nul s'ils se confondent)."""
    dx, dz = float(vers[0]) - float(de[0]), float(vers[2]) - float(de[2])
    n = math.hypot(dx, dz)
    return [0.0, 0.0, 0.0] if n < 1e-3 else [round(dx / n, 4), 0.0, round(dz / n, 4)]


def info_coup(mem, link_base: int, point, pos_monstre, pos_link) -> dict:
    """Ce que l'attaquant joint à son ``hit`` (``inj``) et garde pour le retour local (``pt``)."""
    at, cut = lire_attaque(mem, link_base)
    return {"rel": point_relatif(point, pos_monstre), "at": at, "cut": cut, "vec": direction(pos_link, pos_monstre),
            "pt": [round(float(v), 1) for v in point]}


def champ_inj(info: dict | None) -> dict | None:
    """``inj`` à envoyer (sans le point absolu, local) ; None si l'info est illisible."""
    if not isinstance(info, dict):
        return None
    return valid_inj({k: info.get(k) for k in ("rel", "at", "cut", "vec")})


class RetourLocal:
    """``--retour-coup`` : recette 7 de CALL 11 au point d'impact de nos coups, au plus un toutes les ``RETOUR_GAP``
    s (un coup d'épée peut toucher plusieurs marionnettes à la même image : une étincelle suffit)."""

    def __init__(self, enabled: bool = False, clock=time.monotonic):
        self.enabled = bool(enabled)
        self.clock = clock
        self.t = float("-inf")
        self.joues = self.refus = 0

    def jouer(self, sampler, mb, points) -> int:
        """``points`` : points d'impact des coups de ce tour. Rien sans le bit 25 (la recette 7 n'existe pas avant)."""
        if not self.enabled or not points or mb is None or not getattr(mb, "coup_monstre_ok", False):
            return 0
        now = self.clock()
        if now - self.t < RETOUR_GAP:
            return 0
        from synchro import effets_pack
        self.t = now
        try:
            r = sampler.effet_pack(points[0], effets_pack.RECETTE_COUP, son=True)
        except Exception:
            r = None
        if r is not None and r >= 0:
            self.joues += 1
            return 1
        self.refus += 1
        return 0


# --- chez l'autorité --------------------------------------------------------------------------------------
class CoupsInjectes:
    """``--coups-injectes`` : un ``hit`` qui porte ``inj`` est joué par CALL 14 (réaction native) ; sinon, ou en cas de
    refus, l'appelant écrit les PV comme avant (``apply_hit``)."""

    def __init__(self, enabled: bool = False, sleep=time.sleep):
        self.enabled = bool(enabled)
        self.sleep = sleep
        self.injectes = self.replis = self.tot = 0
        self.dernier: str | None = None

    def jouer(self, mb, ennemi_pid: int | None, fantome_pid: int | None, inj: dict | None) -> int | None:
        """Code de CALL 14 (1 : injecté), ou None si l'injection n'est pas tentée (option, capacité, fantôme ou ennemi
        inconnus, champ absent) ou si la boîte n'a pas répondu : l'appelant écrit alors les PV."""
        if not self.enabled or inj is None or mb is None or not getattr(mb, "coup_monstre_ok", False) \
                or not ennemi_pid or not fantome_pid:
            return None
        at = inj["at"]
        extra = {}
        if inj.get("arme") is not None:         # coup d'un projectile (synchro/coups_projectiles.py) : bit 26 seulement
            from synchro import coups_projectiles as cp
            if not getattr(mb, "coups_proj_ok", False) or inj["arme"] not in cp.ARMES:
                return None
            extra = {"arme": cp.ARMES[inj["arme"]], "src": inj.get("src") or (0.0, 0.0, 0.0)}

        def appel():
            return mb.coup_monstre(ennemi_pid, fantome_pid, at["type"], at["atp"], at["spl"], at["se"], at["mark"],
                                   at["gat"], at["mtrl"], cut=inj.get("cut"), vec=inj["vec"], rel=inj["rel"],
                                   rayon=RAYON, hauteur=HAUTEUR, **extra)
        try:
            r = appel()
            if r == R_TOT:
                self.tot += 1
                self.sleep(ESSAI_TOT)
                r = appel()
        except Exception as exc:                # boîte occupée, sans réponse (rien n'est fait) : PV écrits
            self.dernier = f"{type(exc).__name__}"
            return None
        if r == R_OK:
            self.injectes += 1
        return r

    def status(self) -> str:
        return (f"coups distants injectés : {self.injectes}, PV écrits faute de mieux : {self.replis}"
                + (f", trop tôt : {self.tot}" if self.tot else ""))
