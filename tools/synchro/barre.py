"""Mode Hanteurs : barre de compétences du monstre possédé (étude docs/etudes/barre_competences.md, la section
« Vérification » prime). Ce module est le MODÈLE, en Python pur, sans fenêtre ni Cemu :

* ``modele(vue, ...)`` : de l'état du Hanteur (``HanteurSync.vue()``, lui-même tenu par le serveur) aux cases de la
  barre : nom court, touche, recharge (temps restant, fraction), état « prêt » / « recharge » / « indisponible »,
  plus le nom de l'espèce, sa vie et le temps de possession restant. Hors possession : boss aux boutons, invocation,
  possession (avec coût et recharge).
* ``placer(...)`` : où poser la barre sur la zone d'image de la fenêtre du jeu (position et taille réglables).
* ``dessin(...)`` : la liste d'opérations à peindre (rectangles et textes), indépendante de la façon de peindre.

L'affichage (fenêtre posée sur Cemu) est dans ``barre_fenetre.py``. Rien ici ne lit ni n'écrit la mémoire du jeu.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from synchro import ordres

LANGUES = ("fr", "en")
POSITIONS = ("bas", "bas-gauche", "bas-droite", "haut", "haut-gauche", "haut-droite")
TAILLE_MIN, TAILLE_MAX, TAILLE_DEFAUT = 50, 200, 100       # % de la taille de base (image de 720 lignes)
PRET, RECHARGE, INDISPONIBLE = "pret", "recharge", "indisponible"
ROLE_HANTEUR = "hanteur"

TEXTES = {
    "fr": {
        "attaque": "Attaque", "lourde": "Attaque lourde", "garde": "Garde", "elan": "Élan", "capacite": "Capacité",
        "monter": "Monter", "descendre": "Descendre", "lacher": "Relâcher",
        "invoquer": "Invoquer", "espece": "Espèce suivante", "posseder": "Posséder",
        "t.epee": "Épée", "t.charge": "Épée chargée", "t.pgup": "PagePréc", "t.pgdn": "PageSuiv",
        "indisponible": "indisponible", "geste": "geste en cours", "points": "{} pts", "manque": "il faut {} pts",
        "aucune": "aucune espèce ici", "colle": "caméra collée (F8)", "pv": "PV {}/{}", "hanteur": "Hanteur", "solde": "{} points",
        "p.pilote": "piloté", "p.geste": "geste en cours", "p.laisse": "en laisse", "p.attente": "en attente",
        "r.pack": "pack de l'autorité pas à jour", "r.espece": "espèce non pilotable", "r.option": "pilotage coupé",
        "r.evenement": "cinématique en cours", "r.essai": "espèce à l'essai",
        "boss": "Boss : {}",
    },
    "en": {
        "attaque": "Attack", "lourde": "Heavy attack", "garde": "Guard", "elan": "Lunge", "capacite": "Ability",
        "monter": "Climb", "descendre": "Descend", "lacher": "Release",
        "invoquer": "Summon", "espece": "Next species", "posseder": "Possess",
        "t.epee": "Sword", "t.charge": "Charged sword", "t.pgup": "PageUp", "t.pgdn": "PageDown",
        "indisponible": "unavailable", "geste": "mid-move", "points": "{} pts", "manque": "needs {} pts",
        "aucune": "no species here", "colle": "camera locked (F8)", "pv": "HP {}/{}", "hanteur": "Haunter", "solde": "{} points",
        "p.pilote": "piloted", "p.geste": "mid-move", "p.laisse": "on a leash", "p.attente": "waiting",
        "r.pack": "authority's pack is outdated", "r.espece": "species cannot be piloted",
        "r.option": "piloting is off", "r.evenement": "cutscene playing", "r.essai": "species on trial",
        "boss": "Boss: {}",
    },
}
# touche de chaque commande (les touches F et Page sont celles de wwmp/hanteurs.TOUCHES ; le test les recoupe)
TOUCHES_BARRE = {"attaque": "t.epee", "lourde": "t.charge", "capacite": "F2", "garde": "F3", "monter": "t.pgup",
                 "descendre": "t.pgdn", "lacher": "F4", "posseder": "F4", "invoquer": "F6", "espece": "F7"}
# portage natif : F3-F6 seules y chargent un état de sauvegarde, le client ne les prend qu'avec Ctrl
# (synchro/hanteurs.CONFLITS_NATIF) ; ``regler_natif`` (appelé par le client quand le jeu est natif) change l'affichage
TOUCHES_NATIF = {k: f"Ctrl+{v}" for k, v in TOUCHES_BARRE.items() if v in ("F2", "F3", "F4", "F5", "F6")}
_natif = False


def regler_natif(on: bool) -> None:
    global _natif
    _natif = bool(on)


_reglees: dict = {}


def regler_touches(table: dict) -> None:
    """Touches réglées dans le lanceur (onglet Commandes) : {commande: touche} ; celles qui gardent leur défaut
    restent affichées comme avant (« t.pgup » traduit, etc.)."""
    global _reglees
    _reglees = {k: v for k, v in table.items() if isinstance(k, str) and isinstance(v, str)}


def touche_de(commande: str) -> str:
    """Touche affichée d'une commande (« Ctrl+F4 » sur le portage natif ; celle réglée dans le lanceur)."""
    c = "posseder" if commande == "lacher" else commande
    if c in _reglees:
        from commun import commandes as cm
        defaut = next((x.defaut_clavier(_natif) for x in cm.COMMANDES if x.id == c), None)
        if _reglees[c] != defaut:
            return _reglees[c] or "—"
    return (TOUCHES_NATIF.get(commande) if _natif else None) or TOUCHES_BARRE.get(commande, "")


def langue_ok(langue) -> str:
    return langue if langue in LANGUES else "fr"


def texte(cle: str, langue: str = "fr", *args) -> str:
    t = TEXTES[langue_ok(langue)].get(cle, cle)
    return t.format(*args) if args else t


def taille_ok(v) -> int:
    """Réglage « taille » nettoyé (% ; valeur illisible : 100)."""
    if isinstance(v, bool):
        return TAILLE_DEFAUT
    try:
        v = float(v)
    except (TypeError, ValueError):
        return TAILLE_DEFAUT
    if not math.isfinite(v):
        return TAILLE_DEFAUT
    return int(max(TAILLE_MIN, min(TAILLE_MAX, round(v))))


def position_ok(v) -> str:
    return v if v in POSITIONS else "bas"


def _nombre(v, defaut: float = 0.0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return defaut
    return float(v)


def _court(nom, defaut: str) -> str:
    """« Bokoblin : coup sauté » -> « Coup sauté » (libellé court d'un ordre)."""
    if not isinstance(nom, str):
        return defaut
    t = nom.rsplit(":", 1)[-1].strip()
    return (t[:1].upper() + t[1:])[:24] if t else defaut


@dataclass(frozen=True)
class Case:
    cle: str                 # « attaque », « lourde », « garde », « monter », « lacher », « invoquer »...
    nom: str                 # libellé court
    touche: str              # touche ou bouton
    etat: str = PRET         # PRET, RECHARGE ou INDISPONIBLE
    reste: float = 0.0       # s de recharge restantes (donnée du serveur)
    fraction: float = 0.0    # part de la recharge encore à attendre (0 à 1) : hauteur du voile
    detail: str = ""         # coût, raison de l'indisponibilité


@dataclass(frozen=True)
class Modele:
    mode: str                # « possession », « boss » ou « libre »
    titre: str               # nom de l'espèce possédée, du boss, ou « Hanteur »
    cases: tuple = ()
    pv: tuple | None = None  # (vie, vie maximale) du monstre possédé, si connue
    temps: float | None = None      # s de possession restantes
    part_temps: float | None = None  # part de la possession restante (0 à 1), si la durée est connue
    etat: str = ""           # pilotage : « piloté », « en laisse (raison) »... ou solde de points


class Memoire:
    """Fraction d'une recharge dont le serveur ne donne que le temps restant : la plus grande valeur vue depuis
    le début de la recharge sert de durée. Un seul fil (celui de la barre)."""

    def __init__(self):
        self.maxi: dict[str, float] = {}

    def fraction(self, cle: str, reste: float, duree: float = 0.0) -> float:
        if reste <= 0:
            self.maxi.pop(cle, None)
            return 0.0
        if duree <= 0:
            duree = self.maxi[cle] = max(self.maxi.get(cle, 0.0), reste)
        return max(0.0, min(1.0, reste / duree))


def _case(cle, nom, langue, reste=0.0, duree=0.0, bloque="", memoire=None, detail="", touche=None) -> Case:
    touche = touche if touche is not None else texte(touche_de(cle.rsplit(".", 1)[-1]), langue)
    reste = max(0.0, _nombre(reste))
    if reste > 0:
        f = memoire.fraction(cle, reste, _nombre(duree)) if memoire is not None \
            else (max(0.0, min(1.0, reste / duree)) if _nombre(duree) > 0 else 1.0)
        return Case(cle, nom, touche, RECHARGE, round(reste, 1), f, detail)
    if memoire is not None:
        memoire.fraction(cle, 0.0)
    if bloque:
        return Case(cle, nom, touche, INDISPONIBLE, 0.0, 0.0, bloque)
    return Case(cle, nom, touche, PRET, 0.0, 0.0, detail)


def _nom_geste(proc, geste: str, langue: str) -> str:
    """Libellé du geste pour l'espèce : celui de son ordre (« Coup sauté », « Tir », « Piqué »...) s'il existe."""
    if isinstance(proc, int) and not isinstance(proc, bool):
        for o in ordres.TOUS:
            if o.genre == "monstre" and o.proc == proc and o.geste == geste:
                return _court(o.en if langue == "en" else o.nom, texte(geste, langue))
    return texte(geste, langue)


def _possession(vue: dict, pv, langue: str, memoire) -> Modele:
    po = vue.get("possede") or {}
    proc = po.get("proc")
    pil = vue.get("pilotage") if isinstance(vue.get("pilotage"), dict) else {}
    etat_pil = pil.get("etat") if pil.get("etat") in ("pilote", "geste", "laisse", "attente") else "attente"
    gestes = [g for g in (vue.get("gestes") or []) if g in ordres.GESTES]
    rech = vue.get("recharges_gestes") if isinstance(vue.get("recharges_gestes"), dict) else {}
    en_geste = texte("geste", langue) if etat_pil == "geste" else ""
    cases = []
    for g in ordres.GESTES:
        r = rech.get(g) if isinstance(rech.get(g), dict) else {}
        if g in gestes:
            cases.append(_case(g, _nom_geste(proc, g, langue), langue, r.get("reste"), r.get("duree"), en_geste,
                               memoire))
        elif g == "attaque" and etat_pil == "laisse":
            # laisse classique : le coup d'épée donne l'élan vers le Link le plus proche (même recharge)
            cases.append(_case(g, texte("elan", langue), langue, r.get("reste"), r.get("duree"), "", memoire))
    cap = vue.get("capacite") if isinstance(vue.get("capacite"), dict) else None
    if cap is not None:                                  # capacité propre de l'espèce (synchro/capacites.py)
        r = rech.get("capacite") if isinstance(rech.get("capacite"), dict) else {}
        nom = str((cap.get("en") if langue == "en" else cap.get("nom")) or texte("capacite", langue))[:24]
        cases.append(_case("capacite", nom, langue, r.get("reste"), r.get("duree"), en_geste, memoire))
    if _nombre(vue.get("vol")) > 0:                      # volant piloté : hauteur
        cases.append(_case("monter", texte("monter", langue), langue))
        cases.append(_case("descendre", texte("descendre", langue), langue))
    cases.append(_case("lacher", texte("lacher", langue), langue))
    etat = texte("p." + etat_pil, langue)
    if etat_pil == "laisse" and pil.get("code") in ("pack", "espece", "option", "evenement", "essai"):
        etat += " (" + texte("r." + pil["code"], langue) + ")"
    reste = max(0.0, _nombre(po.get("reste")))
    duree = _nombre(po.get("duree"))
    return Modele("possession", str(po.get("nom") or "?")[:24], tuple(cases), _pv(pv), reste,
                  max(0.0, min(1.0, reste / duree)) if duree > 0 else None, etat)


def _pv(pv) -> tuple | None:
    """(vie, vie max) nettoyé ; None si inconnu ou incohérent."""
    if not isinstance(pv, (tuple, list)) or len(pv) != 2:
        return None
    a, b = pv
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (a, b)) or b <= 0 or b > 9999:
        return None
    return (max(0, min(a, b)), b)


def _cout(points, cout, langue: str) -> tuple[str, str]:
    """(détail, blocage) d'une commande payante."""
    cout = int(_nombre(cout))
    if cout <= 0:
        return "", ""
    if _nombre(points) < cout:
        return "", texte("manque", langue, cout)
    return texte("points", langue, cout), ""


def _boss(vue: dict, stage, langue: str, memoire) -> list[Case]:
    if not vue.get("influence_on") or not isinstance(stage, str):
        return []
    boss = ordres.boss_de(stage)
    infl = {d.get("cle"): d for d in (vue.get("influences") or []) if isinstance(d, dict)}
    cases = []
    for g in ordres.GESTES:
        cles = [c for c in ordres.BOUTONS_BOSS.get(boss or "", {}).get(g, ()) if c in infl
                and c in ordres.INDEX and stage in ordres.ORDRES[ordres.INDEX[c]].stages]
        if not cles:
            continue
        if g == "lourde" and cles == [c for c in ordres.BOUTONS_BOSS[boss].get("attaque", ()) if c in infl]:
            continue                                     # même ordre que le coup simple (larves de Molgera)
        d = min((infl[c] for c in cles), key=lambda x: _nombre(x.get("reste")))     # le prochain prêt
        nom = _court(d.get("en") if langue == "en" else d.get("nom"), texte(g, langue)) if len(cles) == 1 \
            else texte(g, langue)
        detail, bloque = _cout(vue.get("points"), d.get("cout"), langue)
        cases.append(_case("boss." + g, nom, langue, d.get("reste"), d.get("recharge"), bloque, memoire, detail))
    return cases


def _libre(vue: dict, stage, langue: str, memoire) -> Modele:
    points = int(_nombre(vue.get("points")))
    cases = _boss(vue, stage, langue, memoire)
    mode, titre = "libre", texte("hanteur", langue)
    if cases:
        mode, titre = "boss", texte("boss", langue, ordres.boss_de(stage))
    especes = [e for e in (vue.get("especes") or []) if isinstance(e, dict)]
    ici = [e for e in especes if e.get("ici")]
    choisie = next((e for e in ici if e.get("proc") == vue.get("choix")), None)
    if especes:
        if choisie is None:
            cases.append(_case("invoquer", texte("invoquer", langue), langue, 0.0, 0.0, texte("aucune", langue),
                               memoire))
        else:
            detail, bloque = _cout(points, choisie.get("cout"), langue)
            if vue.get("gel"):                           # caméra collée à un Link : le client refuse d'invoquer
                bloque = texte("colle", langue)
            nom = (texte("invoquer", langue) + " " + str(choisie.get("nom") or "?"))[:28]
            cases.append(_case("invoquer", nom, langue, choisie.get("recharge"), 0.0, bloque, memoire, detail))
        if len(ici) > 1:
            cases.append(_case("espece", texte("espece", langue), langue))
    if vue.get("possession_on"):
        detail, bloque = _cout(points, vue.get("possession_cout"), langue)
        cases.append(_case("posseder", texte("posseder", langue), langue, vue.get("recharge_possession"), 0.0,
                           bloque, memoire, detail))
    return Modele(mode, titre, tuple(cases), None, None, None, texte("solde", langue, points))


def modele(vue, pv=None, stage=None, langue: str = "fr", hors_possession: bool = True,
           memoire: Memoire | None = None) -> Modele | None:
    """Barre à afficher, ou None (rien) : pas en mode Hanteurs, pas Hanteur, rôles en attente, potion en cours (le
    Hanteur joue alors un Link, avec l'interface du jeu), partie finie. ``vue`` : ``HanteurSync.vue()`` ; ``pv`` :
    (vie, vie max) du monstre possédé si le client la connaît ; ``stage`` : stage du Hanteur (boss aux boutons).
    ``hors_possession`` : montrer aussi les commandes hors possession (invocation, possession, boss)."""
    if not isinstance(vue, dict) or vue.get("role") != ROLE_HANTEUR or vue.get("attente"):
        return None
    if vue.get("clone") is not None or vue.get("victoire"):
        return None
    langue = langue_ok(langue)
    if isinstance(vue.get("possede"), dict):
        return _possession(vue, pv, langue, memoire)
    if not hors_possession:
        return None
    m = _libre(vue, stage, langue, memoire)
    return m if m.cases else None


# --- placement ---------------------------------------------------------------------------------------------------
# Unités de base : une image de 720 lignes, taille 100 %.
CASE_L, CASE_H, ECART, MARGE, ENTETE, BARRE_L_MIN = 104, 64, 6, 8, 22, 380
BORD = 0.03                # marge entre la barre et le bord de l'image (part de la hauteur de l'image)
CLIENT_MIN = (320, 180)    # zone du jeu plus petite : rien n'est affiché
RATIO = 16.0 / 9.0         # image du jeu ; la fenêtre peut avoir des bandes noires autour


def image(client) -> tuple[int, int, int, int] | None:
    """Zone d'image 16:9 dans la zone client (x, y, largeur, hauteur) de la fenêtre du jeu ; None : trop petite."""
    try:
        x, y, w, h = (int(v) for v in client)
    except (TypeError, ValueError):
        return None
    if w < CLIENT_MIN[0] or h < CLIENT_MIN[1]:
        return None
    if w / h > RATIO:
        iw, ih = int(round(h * RATIO)), h
    else:
        iw, ih = w, int(round(w / RATIO))
    return (x + (w - iw) // 2, y + (h - ih) // 2, iw, ih)


def taille_barre(n_cases: int, echelle: float) -> tuple[int, int]:
    n = max(1, int(n_cases))
    l = max(BARRE_L_MIN, 2 * MARGE + n * CASE_L + (n - 1) * ECART)
    h = 2 * MARGE + ENTETE + 4 + CASE_H
    return int(round(l * echelle)), int(round(h * echelle))


def placer(client, n_cases: int, position: str = "bas", taille=TAILLE_DEFAUT):
    """Rectangle écran de la barre : (x, y, largeur, hauteur, échelle), ou None si la zone du jeu est trop petite.
    L'échelle suit la hauteur de l'image (donc la taille de la fenêtre et la mise à l'échelle de Windows) ; la barre
    ne sort jamais de l'image (elle est réduite s'il le faut)."""
    img = image(client)
    if img is None:
        return None
    ix, iy, iw, ih = img
    echelle = max(0.4, min(4.0, ih / 720.0 * taille_ok(taille) / 100.0))
    l, h = taille_barre(n_cases, echelle)
    bord = int(round(ih * BORD))
    dispo_l, dispo_h = iw - 2 * bord, ih - 2 * bord
    if l > dispo_l or h > dispo_h:                       # trop grande pour l'image : réduite
        echelle *= min(dispo_l / l, dispo_h / h)
        l, h = taille_barre(n_cases, echelle)
        l, h = min(l, dispo_l), min(h, dispo_h)
    position = position_ok(position)
    y = iy + bord if position.startswith("haut") else iy + ih - bord - h
    if position.endswith("gauche"):
        x = ix + bord
    elif position.endswith("droite"):
        x = ix + iw - bord - l
    else:
        x = ix + (iw - l) // 2
    return (x, y, l, h, echelle)


# --- dessin ------------------------------------------------------------------------------------------------------
# Couleurs (r, v, b). TRANSPARENT : couleur clé de la fenêtre (jamais utilisée pour un tracé).
TRANSPARENT = (1, 2, 3)
C_FOND, C_TEXTE, C_DISCRET, C_TOUCHE = (16, 20, 28), (240, 240, 240), (150, 156, 168), (255, 214, 102)
C_CASE = {PRET: (38, 56, 84), RECHARGE: (34, 38, 50), INDISPONIBLE: (48, 38, 40)}
C_LISERE = {PRET: (96, 208, 128), RECHARGE: (240, 160, 64), INDISPONIBLE: (150, 90, 90)}
C_VOILE, C_VIE, C_VIE_FOND, C_TEMPS = (10, 12, 18), (220, 64, 64), (70, 30, 30), (96, 160, 240)


def duree_texte(s) -> str:
    s = int(max(0.0, _nombre(s)) + 0.999)
    return f"{s // 60}:{s % 60:02d}"


def dessin(m: Modele, largeur: int, hauteur: int, echelle: float, langue: str = "fr") -> list[tuple]:
    """Opérations à peindre dans une surface ``largeur`` x ``hauteur`` (pixels), dans l'ordre :
    ("rect", x, y, l, h, couleur) et ("texte", x, y, l, h, texte, couleur, taille en pixels, gras, alignement
    « g » / « c » / « d »). Tout reste dans la surface."""
    e = max(0.1, float(echelle))
    px = lambda v: max(1, int(round(v * e)))
    ops: list[tuple] = [("rect", 0, 0, largeur, hauteur, TRANSPARENT), ("rect", 0, 0, largeur, hauteur, C_FOND)]
    mg = px(MARGE)
    # en-tête : titre, vie, temps, état
    ey, eh = mg, px(ENTETE)
    dispo = max(1, largeur - 2 * mg)
    tl = dispo * 34 // 100
    ops.append(("texte", mg, ey, tl, eh, m.titre, C_TEXTE, px(15), True, "g"))
    x = mg + tl
    if m.pv is not None:
        vl = dispo * 26 // 100
        bh = max(2, px(5))
        ops.append(("rect", x, ey + eh - bh, vl - px(6), bh, C_VIE_FOND))
        ops.append(("rect", x, ey + eh - bh, max(0, (vl - px(6)) * m.pv[0] // m.pv[1]), bh, C_VIE))
        ops.append(("texte", x, ey, vl - px(6), eh - bh, texte("pv", langue, m.pv[0], m.pv[1]), C_TEXTE, px(12),
                    False, "g"))
        x += vl
    reste_l = mg + dispo - x
    if m.temps is not None:
        tl2 = min(reste_l, px(58))
        ops.append(("texte", mg + dispo - tl2, ey, tl2, eh, duree_texte(m.temps), C_TEXTE, px(15), True, "d"))
        reste_l -= tl2
    if m.etat and reste_l > px(20):
        ops.append(("texte", x, ey, reste_l - px(4), eh, m.etat, C_DISCRET, px(12), False, "d" if m.pv else "g"))
    if m.part_temps is not None:                         # filet : temps de possession restant
        ops.append(("rect", mg, ey + eh + px(1), max(0, int(dispo * m.part_temps)), max(1, px(2)), C_TEMPS))
    # cases
    n = len(m.cases)
    if n:
        cy = ey + eh + px(4)
        ch = max(1, hauteur - cy - mg)
        ec = px(ECART)
        cl = max(1, min(px(CASE_L), (dispo - (n - 1) * ec) // n))
        x0 = mg + max(0, (dispo - (n * cl + (n - 1) * ec)) // 2)
        for i, c in enumerate(m.cases):
            cx = x0 + i * (cl + ec)
            actif = c.etat == PRET
            ops.append(("rect", cx, cy, cl, ch, C_CASE.get(c.etat, C_CASE[PRET])))
            if c.etat == RECHARGE:                       # voile qui se vide avec la recharge
                ops.append(("rect", cx, cy, cl, max(0, min(ch, int(ch * c.fraction))), C_VOILE))
            ops.append(("rect", cx, cy, cl, max(1, px(3)), C_LISERE.get(c.etat, C_LISERE[PRET])))
            ops.append(("texte", cx + px(3), cy + px(4), cl - px(6), px(17), c.nom, C_TEXTE if actif else C_DISCRET,
                        px(12), True, "c"))
            milieu_y, milieu_h = cy + px(21), max(1, ch - px(21) - px(18))
            if c.etat == RECHARGE:
                ops.append(("texte", cx, milieu_y, cl, milieu_h, f"{c.reste:.1f}", C_TEXTE, px(20), True, "c"))
            elif c.etat == INDISPONIBLE:
                ops.append(("texte", cx + px(2), milieu_y, cl - px(4), milieu_h,
                            c.detail or texte("indisponible", langue), C_LISERE[INDISPONIBLE], px(11), False, "c"))
            elif c.detail:
                ops.append(("texte", cx + px(2), milieu_y, cl - px(4), milieu_h, c.detail, C_DISCRET, px(11),
                            False, "c"))
            ops.append(("texte", cx + px(3), cy + ch - px(18), cl - px(6), px(16), c.touche,
                        C_TOUCHE if actif else C_DISCRET, px(12), False, "c"))
    return _borner(ops, largeur, hauteur)


def _borner(ops: list[tuple], largeur: int, hauteur: int) -> list[tuple]:
    """Coupe chaque opération à la surface ; retire celles qui n'ont plus de place."""
    out = []
    for op in ops:
        x, y, l, h = op[1:5]
        x2, y2 = min(largeur, x + l), min(hauteur, y + h)
        x, y = max(0, x), max(0, y)
        if x2 > x and y2 > y and (op[0] != "texte" or op[5]):
            out.append((op[0], x, y, x2 - x, y2 - y) + tuple(op[5:]))
    return out
