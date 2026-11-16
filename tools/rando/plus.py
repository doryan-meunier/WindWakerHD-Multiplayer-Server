"""Randomizer : les autres objets progressifs de TWWHD (option « progressifs_plus », jamais essayée en jeu).

TWWHD-Randomizer (SuperDude88, MIT) rend progressifs : l'arc (fait ici par l'option « progressifs », lot 4 :
rando/noyau.py FAMILLES_PROG), le bouclier (option « boucliers_progressifs », lot 7), et ceux de ce module :

=========  ========================================  =====================================================
famille    paliers (objets du jeu, dans l'ordre)     d'où viennent les pièces de la partie
=========  ========================================  =====================================================
epee       0x38 héros, 0x39 Excalibur sans pouvoir,  monde ouvert : l'épée du héros reste au départ ; Excalibur
           0x3A à moitié, 0x3E pleine puissance      sans pouvoir (départ) et le palier « pleine puissance » de la
                                                     chambre de l'épée (histoire) vont dans le monde ; le coffre de
                                                     la chambre de l'épée (fixe) donne UN palier
magie      0xB1 petite jauge, 0xB2 grande            monde ouvert : la petite jauge du départ va dans le monde ; la
                                                     Grande Fée du Big Octo (fixe) reste
boite      0x23 boîte à images, 0x26 couleur         boîte simple (coffre de la prison de Mercantîle, déjà mélangée) ;
                                                     couleur : Lenzo (fixe : son lieu n'est pas porté)
bourse     0xAB 1000 rubis, 0xAC 5000                les Grandes Fées (lot 6, « lieux_fees ») : dans n'importe quel
carquois   0xAF 60 flèches, 0xB0 99                  lieu mélangé (coffre ordinaire ou lieu du randomizer par
bombes     0xAD 60 bombes, 0xAE 99                   fichiers), et une fée peut alors donner n'importe quel objet
=========  ========================================  =====================================================

Dans le jeu (mod natif 0.40.0, natif/mod_wwmp/rando_plus.c) : execItemGet est remplacé ; un palier d'une famille
active donne le palier SUIVANT celui que le joueur a déjà (comme les item_func progressives de TWWHD), quel que soit
le palier écrit dans le lieu. Les familles actives viennent du PLAN (étiquette ``wwr_plus`` du pack généré, octet
+0x1E du bloc de code : rando/natif_code.py) : le jeu les joue dès son démarrage. Chaque don progressif est compté
dans la zone WWMPPRG2 ; le client envoie ces comptes au serveur, qui met l'inventaire commun du groupe au niveau du
TOTAL (deux joueurs qui reçoivent le même palier au même instant : rien n'est perdu ; voir ``rang_commun``).

Option coupée (liste vide, défaut) : rien ne change, même table et même chaîne de réglages qu'avant.
Ce module ne touche ni au jeu ni au réseau.
"""
from __future__ import annotations

import hashlib
import json

OPTION = "progressifs_plus"
# ordre FIXE : bit k de l'octet du plan et index des comptes de la zone WWMPPRG2 (natif/mod_wwmp/boite.h)
FAMILLES = (("epee", (0x38, 0x39, 0x3A, 0x3E)), ("magie", (0xB1, 0xB2)), ("boite", (0x23, 0x26)),
            ("bourse", (0xAB, 0xAC)), ("carquois", (0xAF, 0xB0)), ("bombes", (0xAD, 0xAE)))
NOMS = tuple(n for n, _p in FAMILLES)
PALIERS = dict(FAMILLES)
BIT = {n: 1 << k for k, n in enumerate(NOMS)}
FAMILLE_DE = {o: n for n, p in FAMILLES for o in p}
NOMS_FR_EN = {"epee": ("épée progressive (héros, Excalibur sans pouvoir, à moitié, pleine puissance)",
                       "progressive sword (Hero's, Master Sword powerless, half power, full power)"),
              "magie": ("jauge de magie progressive (petite, grande)", "progressive magic meter (small, large)"),
              "boite": ("boîte à images progressive (simple, couleur)", "progressive picto box (simple, deluxe)"),
              "bourse": ("bourse progressive (1000, 5000 rubis)", "progressive wallet (1000, 5000 rupees)"),
              "carquois": ("carquois progressif (60, 99 flèches)", "progressive quiver (60, 99 arrows)"),
              "bombes": ("sac de bombes progressif (60, 99 bombes)", "progressive bomb bag (60, 99 bombs)")}
# familles qui demandent le monde ouvert (leurs pièces viennent des cases de départ du monde ouvert)
MONDE_OUVERT = frozenset({"epee", "magie"})
# familles dont les pièces sont les paliers des Grandes Fées (lot 6, option « lieux_fees »)
FEES = frozenset({"bourse", "carquois", "bombes"})
# cases de départ (rando/lot7.DEPART_MELANGEABLES, ajoutées à la fin) qu'une famille vide : elles ne se cochent pas
# dans « depart_melange », elles suivent la famille
CASES = {"epee": ("epee_legende", "epee_pleine"), "magie": ("magie",)}
# paliers à la création de la partie (comptés par le serveur comme déjà là) : l'épée du héros du monde ouvert
BASE = {"epee": 1}
COMPTE_MAX = 64                              # borne d'un compte reçu du réseau (bien plus que de lieux)
SESSIONS_MAX = 64                            # sessions gardées par groupe (une par démarrage du jeu)
FAMILLES_FICHIERS = ("tresors", "evenements", "boss", "fees", "sol", "pnj_code", "beedle")


class ErreurPlus(ValueError):
    pass


def normaliser(v) -> list:
    """Valeur normalisée de l'option (liste dans l'ordre de FAMILLES). Lève ErreurPlus."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [x.strip() for x in v.replace(";", ",").split(",") if x.strip()]
    if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
        raise ErreurPlus("option progressifs_plus : liste de noms attendue (" + ", ".join(NOMS) + ")")
    for x in v:
        if x not in NOMS:
            raise ErreurPlus(f"option progressifs_plus : famille inconnue {x[:30]!r} (connues : " + ", ".join(NOMS)
                             + ")")
    return [n for n in NOMS if n in v]


def familles(options) -> tuple:
    """Familles actives de ces options (ordre de FAMILLES) ; () sans l'option. Tolérant (ne contrôle rien)."""
    if not isinstance(options, dict):
        return ()
    v = options.get(OPTION)
    if not isinstance(v, (list, tuple)):
        return ()
    return tuple(n for n in NOMS if n in v)


def actif(options) -> bool:
    return bool(familles(options))


def octet(options) -> int:
    """Octet des familles actives (bit k = famille k), tel que le plan le porte."""
    return sum(BIT[n] for n in familles(options))


def familles_de_octet(b: int) -> tuple:
    return tuple(n for n in NOMS if b & BIT[n])


def paliers(options) -> frozenset:
    """Objets du jeu des familles actives."""
    return frozenset(o for n in familles(options) for o in PALIERS[n])


def libres_fees(options) -> frozenset:
    """Paliers des Grandes Fées que ces options sortent des fées (ils vont dans n'importe quel lieu mélangé)."""
    return frozenset(o for n in familles(options) if n in FEES for o in PALIERS[n])


def cases_depart(options) -> tuple:
    """Noms des cases de départ (rando/lot7) que ces options vident (monde ouvert seulement)."""
    if not (isinstance(options, dict) and options.get("monde") == "ouvert"):
        return ()
    return tuple(c for n in familles(options) for c in CASES.get(n, ()))


def controler(o: dict) -> None:
    """Contrôle des options DÉJÀ normalisées. Lève ErreurPlus."""
    f = o.get(OPTION, [])
    if f != normaliser(f):
        raise ErreurPlus("option progressifs_plus : liste non normalisée")
    if not f:
        return
    if o.get("mode") not in (None, "complet"):
        raise ErreurPlus("option progressifs_plus : exige le mode complet")
    if not any(o.get("lieux_" + x) is True for x in FAMILLES_FICHIERS):
        raise ErreurPlus("option progressifs_plus : exige le randomizer par fichiers (au moins une famille de lieux "
                         "« FICHIERS — » : le mod natif lit les familles dans le plan du paquet généré)")
    ouv = [n for n in f if n in MONDE_OUVERT]
    if ouv and o.get("monde") != "ouvert":
        raise ErreurPlus(f"option progressifs_plus : {', '.join(ouv)} exige le monde ouvert (monde = « ouvert ») : "
                         "les pièces viennent des objets que le monde ouvert donne au départ")
    if ouv and not any(o.get("lieux_" + x) is True for x in ("tresors", "evenements", "boss", "sol", "pnj_code")):
        raise ErreurPlus(f"option progressifs_plus : {', '.join(ouv)} exige une famille de lieux qui sache donner "
                         "ces objets : trésors engloutis, objets d'événements, réceptacles des boss, objets posés, "
                         "personnages à immédiat")
    if "epee" in f and o.get("sans_epee") is True:
        raise ErreurPlus("option progressifs_plus : l'épée progressive et « sans épée » s'excluent")
    fe = [n for n in f if n in FEES]
    if fe and o.get("lieux_fees") is not True:
        raise ErreurPlus(f"option progressifs_plus : {', '.join(fe)} exige les Grandes Fées mélangées "
                         "(« FICHIERS — Grandes Fées », lieux_fees) : leurs pièces sont les objets des fées")


def releve(options) -> str:
    """Empreinte des règles de cette partie ("" sans l'option) : familles, paliers, cases vidées, fées libérées
    (rando/verif.py refait le même document : les tests comparent)."""
    f = familles(options)
    if not f:
        return ""
    doc = {"familles": [[n, list(PALIERS[n])] for n in f], "cases": list(cases_depart(options)),
           "fees": sorted(libres_fees(options)), "voies": [0, 5]}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------------------------
# Serveur : rang commun d'un groupe
# ---------------------------------------------------------------------------------------------------------------
def rang_inventaire(nom: str, acq, cap) -> int:
    """Rang (0..len) d'une famille d'après l'inventaire commun du serveur (``acq`` : objets acquis, ``cap`` :
    plafonds {"w", "am", "bm", "mm"})."""
    acq = set(acq or ())
    cap = cap if isinstance(cap, dict) else {}
    p = PALIERS[nom]
    if nom in ("epee", "boite"):
        return max((r for r, o in enumerate(p, 1) if o in acq), default=0)
    v = cap.get({"bourse": "w", "carquois": "am", "bombes": "bm", "magie": "mm"}[nom], 0)
    v = v if isinstance(v, int) else 0
    if nom == "bourse":
        return max(0, min(2, v))
    if nom == "magie":
        return 2 if v >= 32 else 1 if v >= 16 else 0
    return 2 if v >= 99 else 1 if v >= 60 else 0


def rang_commun(nom: str, recus: int, base: int, acq=None, cap=None) -> int:
    """Rang qu'un groupe doit avoir : base (paliers donnés à la création) + dons progressifs reçus par tous ses jeux,
    ou le rang que l'inventaire commun a déjà s'il est plus haut ; borné au dernier palier. Un maximum, jamais une
    baisse."""
    n = len(PALIERS[nom])
    return min(n, max(base + max(0, int(recus)), rang_inventaire(nom, acq, cap)))


def a_donner(nom: str, rang: int) -> tuple[list, dict]:
    """(objets à ajouter aux acquis, plafonds minimaux) d'une famille au rang ``rang``."""
    p = PALIERS[nom]
    rang = max(0, min(len(p), rang))
    if nom in ("epee", "boite"):
        return list(p[:rang]), {}
    if rang == 0:
        return [], {}
    if nom == "bourse":
        return [], {"w": rang}
    if nom == "magie":
        return [], {"mm": (16, 32)[rang - 1]}
    return [], {{"carquois": "am", "bombes": "bm"}[nom]: (60, 99)[rang - 1]}
