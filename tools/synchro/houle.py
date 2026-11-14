"""Houle locale : le bateau fantôme (et le fantôme de Link à la nage ou à bord) suit la surface de la mer LOCALE.

Chaque jeu calcule sa houle lui-même (grille de l_cloth, temps local) : deux PC n'ont jamais la même mer au même
instant. Avant, le fantôme reprenait le y envoyé par l'autre PC, posé sur SA vague : il flottait parfois au-dessus
de l'eau (ou s'y enfonçait). Désormais :

- l'émetteur envoie, quand son bateau est posé sur la mer, l'écart ``dy = y - h`` entre le bateau et la surface
  sous lui (``ship.dy``) ; quand Link nage, ``eau = y - h`` sous Link ; quand Link est à la barre (mode bateau),
  ``abord = 1`` ;
- le receveur demande au jeu la hauteur de SA mer sous chaque fantôme et pose ``y = h_local + dy`` ; un Link à
  bord suit le même décalage que son bateau. Sans ``dy`` (à terre, en l'air : saut du bateau, grappin, cyclone,
  hors de la mer de la salle) : le y reçu, comme avant.

La hauteur de la mer vient du pack (zone ``wwmp_houle``, ``Mailbox.houle_*``), qui appelle daSea_ChkArea /
daSea_calcWave du jeu (aucun calcul de houle refait ici). Une vraie synchro des vagues (même temps de houle chez
tous) n'est pas retenue : la houle dépend aussi du vent et de l'état du jeu de chacun, et il faudrait écrire dans
la mer du jeu ; le recalage local suffit pour l'affichage.
"""
from __future__ import annotations

import math

# daPy_lk_c::mModeFlg (HD +0x6A70 [HD-ASM, déduit des tests de bits du code de Link : SWIM 0x40000 testé 28 fois,
# IN_SHIP 0x2000, HANG, ROPE... tous sur ce mot ; GC +0x3618 dans la décompilation])
MODE_FLG = 0x6A70
SWIM = 0x40000
IN_SHIP = 0x2000
SEUIL_BATEAU = 50.0      # [HYPO] |y - h| au-delà : bateau en l'air (saut), pas de recalage
SEUIL_NAGE = 80.0        # [HYPO] garde-fou : Link « nage » mais loin de la surface calculée -> pas de recalage
DIST_MAX = 300.0         # point sondé au tick précédent trop loin de la position actuelle : pas de recalage
DY_MAX = 200.0           # écart reçu plausible (sinon ignoré)

ETAT_OK = 1


def _fini(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def ecarts_emetteur(lecture: dict | None, mode_flg: int | None) -> dict:
    """Champs à ajouter à l'état envoyé : {"dy_bateau": f, "eau": f, "abord": 1}, chacun seulement s'il s'applique.
    ``lecture`` = Mailbox.houle_lire() (y et h de la même image), ``mode_flg`` = daPy+0x6A70 (None : inconnu)."""
    out: dict = {}
    if mode_flg is not None and mode_flg & IN_SHIP:
        out["abord"] = 1
    if not lecture:
        return out
    e, _x, _z, h, y = lecture["bateau"]
    if e == ETAT_OK and _fini(h) and _fini(y) and abs(y - h) <= SEUIL_BATEAU:
        out["dy_bateau"] = round(y - h, 1)
    e, _x, _z, h, y = lecture["link"]
    if (mode_flg is not None and mode_flg & SWIM and e == ETAT_OK and _fini(h) and _fini(y)
            and abs(y - h) <= SEUIL_NAGE):
        out["eau"] = round(y - h, 1)
    return out


def _dy(v) -> float | None:
    return float(v) if _fini(v) and abs(v) <= DY_MAX else None


class Sondes:
    """Côté receveur : points sondés (un par fantôme concerné), résultats lus au tick suivant."""

    def __init__(self):
        self.cles: list[tuple] = []          # clés des entrées 2.. au dernier envoi
        self.xz: list[tuple[float, float]] = []
        self.images: int | None = None       # compteur du pack au dernier tick (il doit avancer)

    def _hauteurs(self, lecture: dict | None) -> dict:
        if not lecture:
            return {}
        vivant = self.images is not None and lecture["images"] != self.images
        self.images = lecture["images"]
        if not vivant:
            return {}
        out = {}
        for i, cle in enumerate(self.cles):
            if i >= len(lecture["points"]):
                break
            e, x, z, h = lecture["points"][i]
            px, pz = self.xz[i]
            if e == ETAT_OK and _fini(h) and abs(x - px) < 1.0 and abs(z - pz) < 1.0:
                out[cle] = (h, px, pz)
        return out

    def corrections(self, mb, lecture: dict | None, shown: dict) -> dict:
        """{("B", pid): décalage du bateau, ("L", pid): décalage de Link} à AJOUTER au y reçu ; écrit les points
        à sonder pour le tick suivant."""
        hauteurs = self._hauteurs(lecture)
        out: dict = {}
        demandes: list[tuple[tuple, float, float]] = []

        def sonde(cle, pos):
            if not (isinstance(pos, (list, tuple)) and len(pos) == 3 and all(_fini(v) for v in pos)):
                return None
            demandes.append((cle, float(pos[0]), float(pos[2])))
            r = hauteurs.get(cle)
            if r is None or math.hypot(pos[0] - r[1], pos[2] - r[2]) > DIST_MAX:
                return None
            return r[0]

        for pid, o in shown.items():
            sh = o.get("ship") or {}
            dy = _dy(sh.get("dy"))
            if dy is not None:
                h = sonde(("B", pid), sh.get("pos"))
                if h is not None:
                    out[("B", pid)] = h + dy - float(sh["pos"][1])
            eau = _dy(o.get("eau"))
            if eau is not None:
                h = sonde(("L", pid), o.get("pos"))
                if h is not None:
                    out[("L", pid)] = h + eau - float(o["pos"][1])
            elif o.get("abord") in (1, True) and ("B", pid) in out:
                out[("L", pid)] = out[("B", pid)]      # à la barre : même décalage que son bateau
        demandes = demandes[:14]
        try:
            if mb.houle_sonder([(x, z) for _c, x, z in demandes]):
                self.cles = [c for c, _x, _z in demandes]
                self.xz = [(x, z) for _c, x, z in demandes]
        except Exception:
            self.cles, self.xz = [], []
        return out
