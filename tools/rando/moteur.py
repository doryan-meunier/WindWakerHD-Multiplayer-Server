"""Randomizer complet, étape 1 : moteur de logique et de remplissage (Python pur, côté serveur).

Ce module ne touche NI au jeu NI au réseau : il calcule, pour une graine et des options, une permutation des
objets des coffres applicables en mémoire, PROUVE que la partie reste finissable depuis un début de jeu
d'origine, et produit le spoiler (sphères) et les indices. Le palier 1 « sûr » (`rando/noyau.py`) n'est pas
modifié et reste le seul mode branché sur le client.

Données (dossier `rando_logique/`) :
- `monde.json`, `macros.json`, `lieux.json`, `objets.json` : logique de TWWHD-Randomizer (SuperDude88, MIT,
  voir `LICENSE_TWWHD.txt`), convertie par `outils/rando_logique_import.py` ;
- `application.json` : ce qui est applicable en mémoire (154 coffres, 13 cadeaux de personnages) et pourquoi
  le reste ne l'est pas ;
- `scenario_vanilla.json` : NOS verrous d'histoire du jeu d'origine (le jeu n'est pas modifié sur disque) ;
- `objets_vanilla.json` : NOTRE correspondance objets de logique <-> objets du jeu d'origine.

Principe de sûreté : un lieu que l'on ne sait pas changer sans risque RESTE D'ORIGINE et la logique le compte
comme un lieu fixe ; la table produite n'est qu'une permutation d'identifiants relus dans les coffres du jeu.

La preuve de finissabilité est refaite par `rando/verif.py`, écrit séparément (autre analyseur, autre
parcours) : `calculer()` refuse de rendre une table que le vérificateur indépendant n'accepte pas.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import threading
import zlib

from rando.noyau import ErreurRando, Flux, nettoyer_graine
from rando.noyau import OBJETS_CONFLIT, SITES_CONFLIT
from rando.noyau import FAMILLES_PROG, OBJETS_PROG
from rando.noyau import MODES_DONJON, MODES_HORS, OBJET_HORS, TYPE_HORS, hors_actif
from rando.noyau import PERMIS2, SITES_DONNEES, OBJETS_DON_SEULS
from rando import lot6 as L6
from rando import lot7 as L7
from rando import lot8 as L8
from rando import lot9 as L9

ALGO = "wwmp-rando-v2"
FORMAT = 2
DOSSIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logique")
FICHIERS = ("monde", "macros", "lieux", "objets", "application", "scenario_vanilla", "objets_vanilla")
ESSAIS_MAX = 64
VARIANTES_TIREES_MAX = 32            # lot 8 : variantes de logique gardées en cache pour des tirages d'entrées / cartes
TYPE_COFFRE = 0                      # coffre : [0, index de sauvegarde, numéro de coffre, origine, nouveau]
TYPE_N2 = 1                          # cadeau d'un personnage (étape 3) : [1, site de rando.SITES_N2, 0, origine, nouveau]
TYPE_DONNEES = 2                     # lieu « donnée » (portage TWWHD, lot 1 : courrier, boutique, enchère, personnage
#                                      à table) : [2, site de rando.SITES_DONNEES, 0, origine, nouveau]
TYPE_CONFLIT = 3                     # coffre « en conflit » (portage TWWHD, lot 2 : clé partagée entre deux niveaux,
#                                      zone wwmp_rnd4 du pack) : [3, site de rando.SITES_CONFLIT, 0, origine, nouveau]
TYPE_FICHIER = L6.TYPE_FICHIER       # lieu du lot 6 (randomizer par fichiers, pack WWMP_Rando GÉNÉRÉ) :
#                                      [5, site de rando_lot6.SITES, 0, origine, nouveau]
TYPE_DEPART = L7.TYPE_DEPART         # case de départ du monde ouvert vidée (option « depart_melange ») :
#                                      [6, case de rando_lot7.DEPART_MELANGEABLES, 0, objet de départ, rubis reçu]
TYPES = (TYPE_COFFRE, TYPE_N2, TYPE_DONNEES, TYPE_CONFLIT, TYPE_FICHIER, TYPE_DEPART)
# lot 6 : verrou d'histoire de tout lieu du lot HORS d'un donjon (même règle prudente que les cadeaux N2 et les lieux
# « donnée » : le moment où le jeu d'origine l'ouvre n'est pas prouvé ; un lieu de donjon suit les verrous du donjon)
VERROU_FICHIERS = "'Helmaroc_King_Defeated'"
# (portage TWWHD, lot 5 : rando.TYPE_HORS = 4 n'est PAS un lieu : c'est l'entrée de plus qui dit qu'un coffre porte un
# objet de donjon étranger, [4, index, coffre, objet réel, donjon de destination] ; voir rando.py)
# groupe d'un lieu « donnée » -> option qui le mélange (toutes éteintes par défaut : jamais essayées en jeu)
OPTION_GROUPE = {"courrier": "lieux_courrier", "boutiques": "lieux_boutiques", "encheres": "lieux_encheres",
                 "pnj": "lieux_pnj", **L9.GROUPES}      # étape 5 du randomizer par fichiers : Kane, lettres, enchère 100
_CLES_DONNEES = frozenset(OPTION_GROUPE.values())
# options ajoutées par le portage, ABSENTES de la forme publiée quand elles sont coupées (voir options_publiees)
_CLES_PORTAGE = _CLES_DONNEES | {"coffres_conflit", "progressifs", "fichiers_demo"} | set(L6.OPTIONS) \
    | set(L7.OPTIONS_BOOL) | set(L8.OPTIONS_BOOL) | set(L9.OPTIONS_BOOL)
# lots 7 et 8 : options à valeur, absentes de la forme publiée quand elles ont leur valeur par défaut (tirages du lot
# 8 : absents tant qu'ils ne sont pas faits)
_DEFAUTS_PORTAGE = {"monde": "histoire", "entrees": L8.ENTREES_DEFAUT, "tirage_entrees": None, "tirage_cartes": None,
                    L7.OPTION_DEPART: []}
METHODES = {"coffre": TYPE_COFFRE, "n2": TYPE_N2}

# Catégories de lieux (clé d'option -> catégorie TWWHD). Seules celles qui portent un lieu applicable (coffre, ou
# cadeau de personnage avec l'option « cadeaux »). Un lieu n'est mélangé que si TOUTES ses catégories sont choisies.
CATEGORIES = {
    "donjons": "Dungeon", "grottes_enigme": "Puzzle Secret Cave", "grottes_combat": "Combat Secret Cave",
    "divers": "Misc", "plateformes": "Platform", "radeaux": "Raft", "sous_marins": "Submarine",
    "recifs": "Eye Reef Chests", "enigmes_iles": "Island Puzzle", "coffres_tingle": "Tingle Chest",
    "quetes_courtes": "Short Side Quest", "labyrinthe": "Savage Labyrinth",
    "quetes_longues": "Long Side Quest", "mini_jeux": "Minigame", "dons": "Free Gift",
    "achats_chers": "Expensive Purchase",
}
LIEUX_DEFAUT = ("donjons", "grottes_enigme", "divers")
OPTIONS_DEFAUT = {
    # début : « origine » (celui du jeu d'origine), ou (lot 7, monde ouvert seulement) le nom anglais d'une île de
    # wwmp/rando_lot7.DEPARTS, ou « hasard » (île tirée depuis la graine ; la table porte l'île choisie)
    "depart": "origine",
    "lieux": sorted(LIEUX_DEFAUT),    # catégories de coffres mélangées
    # petites clés : "origine" | "donjon" (mélangées dans leur donjon) | "tout_donjon" (dans un coffre de n'importe
    # quel donjon) | "partout" (dans n'importe quel coffre mélangé). Les deux dernières (portage TWWHD, lot 5) passent
    # par le serveur (rando.TYPE_HORS) et n'ont jamais été essayées en jeu.
    "cles": "origine",
    "grandes_cles": "origine",        # idem
    "cartes_donjon": "origine",       # cartes et boussoles : idem
    "triforce": "origine",            # fragments et cartes de la Triforce : "origine" | "melange"
    "cadeaux": False,                 # étape 3 : cadeaux de personnages et prix (stub N2 du pack, bit 7) mélangés
    # portage TWWHD, lot 1 : lieux changés par une écriture de données du client (rando/donnees.py). Un lieu d'un
    # groupe est mélangé dès que son option est allumée, quelles que soient les catégories de l'option « lieux ».
    "lieux_courrier": False,          # 6 lettres avec objet (mère de Baito, père de Komali, Orca, Mémé, Arielle, Baito)
    "lieux_boutiques": False,         # 2 étals de la boutique de l'Île du Pic (500 et 950 rubis)
    "lieux_encheres": False,          # 4 enchères (40, 5, 60, 80 rubis) par la table du jeu (au lieu du stub N2)
    "lieux_pnj": False,               # Sam, Linda et Anton, Kreeb (table partagée avec les photos de Mercantîle)
    # portage TWWHD, lot 2 : les dix coffres dont la clé est partagée avec le coffre d'un autre niveau (zone wwmp_rnd4
    # du pack). Coupée par défaut : jamais essayée en jeu. Ils suivent alors les catégories de l'option « lieux ».
    "coffres_conflit": False,
    # portage TWWHD, lot 4 : objets progressifs (rando.FAMILLES_PROG ; aujourd'hui l'arc : arc du héros puis flèches de
    # lumière). Le coffre des flèches de lumière devient mélangeable, et le Ne lieu de la famille ouvert par le groupe
    # donne le palier N (zone wwmp_prg du pack, compte tenu par le serveur). Coupée par défaut : jamais essayée en jeu.
    "progressifs": False,
    # randomizer par fichiers générés localement, étape 1 (docs/etudes/rando_fichiers.md) : option de TEST, absente
    # des préréglages ; coupée, elle n'est ni dans le germe ni dans la table (tables d'avant) ; cochée, elle entre dans
    # le germe comme toute option publiée (autre table). Elle ne place aucun objet : le pack WWMP_Rando généré change le
    # texte « Appuyez sur un bouton » de l'écran titre (EN/FR/ES), pour vérifier en jeu que Cemu sert bien les
    # fichiers générés. Exige chez tous le même pack généré (rando/fichiers.py). Coupée : rien ne change.
    "fichiers_demo": False,
    # randomizer par fichiers, étape 2 (lot 6, rando/lot6.py) : une option par famille de lieux changés par le
    # pack WWMP_Rando GÉNÉRÉ sur chaque PC (fichiers du jeu et lignes de code). Coupées : ni dans le germe ni dans la
    # table (tables d'avant), rien de généré. Jamais essayées en jeu.
    "lieux_tresors": False,           # 57 trésors engloutis (et Big Octo, canonnières, terre noire)
    "lieux_evenements": False,        # 8 objets donnés par une liste d'événements (chants compris)
    "lieux_boss": False,              # 6 réceptacles des boss
    "lieux_fees": False,              # 6 Grandes Fées (bourse, sacs et carquois gardent leur palier d'inventaire)
    "lieux_sol": False,               # 12 objets posés, pots et têtes de pierre
    "lieux_pnj_code": False,          # 15 personnages dont l'objet est dans le code, potions, Zunari
    "lieux_beedle": False,            # Beedle à 20 rubis, carte à 900 rubis de l'Île du Pic
    # randomizer par fichiers, étape 3 (lot 7, rando/lot7.py) : pack WWMP_Rando GÉNÉRÉ (fichiers du jeu et code).
    # Coupées : ni dans le germe ni dans la table (tables d'avant), rien de généré. Jamais essayées en jeu.
    "monde": "histoire",              # « ouvert » : monde ouvert de TWWHD, objets de l'histoire donnés au départ
    "coffres_cinematiques": False,    # coffres à la place de cinématiques (Reine des fées ; + 3 en monde ouvert)
    "cinematiques_courtes": False,    # Zéphos, enchères, petites caméras, chants, Tott, tornade, Tingle
    "pas_de_recombats": False,        # combats de souvenir de la Tour de Ganon déjà faits
    "textes": False,                  # bulles du jeu (vendeurs, enchères, Beedle, cartes) en trois langues
    # boucliers progressifs (rando_lot7.OPTION_BOUCLIER) : l'item_func du bouclier du héros donne le miroir à qui a déjà
    # un bouclier (pack WWMP_Rando GÉNÉRÉ). La logique compte déjà deux « Progressive Shield » (bouclier du héros, coffre
    # du miroir, fixe) : rien d'autre ne change ; elle permet la case « bouclier » de depart_melange
    "boucliers_progressifs": False,
    # randomizer par fichiers, étape 4 (lot 8, rando/lot8.py) : pack WWMP_Rando GÉNÉRÉ. Coupées : ni dans le germe
    # ni dans la table (tables d'avant), rien de généré. Jamais essayées en jeu.
    "sans_epee": False,               # aucune épée (exige le monde ouvert)
    "entrees": "aucune",              # « donjons » ou « donjons_grottes » : entrées mélangées (exige le monde ouvert)
    "cartes_melangees": False,        # chaque carte au trésor mène à un autre trésor (exige lieux_tresors)
    # randomizer par fichiers, étape 5 (rando/lot9.py). Coupées : ni dans le germe ni dans la table (tables
    # d'avant). Jamais essayées en jeu. Les trois premières sont des lieux « donnée » (octets écrits par le client,
    # comme le lot 1) ; la dernière exige le pack WWMP_Rando GÉNÉRÉ.
    "lieux_kane": False,              # 4 récompenses de Kane (décorations de Mercantîle)
    "courrier_tout": False,           # les 6 autres lettres avec objet (exige lieux_courrier)
    "lieux_encheres_100": False,      # enchère à 100 rubis (voile rapide), bit « déjà gagné » 0x6B01
    "boutique_epuise": False,         # étals de l'Île du Pic « épuisés » par emplacement (exige lieux_boutiques)
    # objets de départ du monde ouvert mélangés (wwmp/rando_lot7.DEPART_MELANGEABLES) : liste de noms courts parmi
    # « bombes », « perle_din », « perle_farore », « hymne_terre », « hymne_vent », « baguette », « requiem »,
    # « sac_butin », « sac_facteur », « flacon ». Vide (défaut) : ni dans le germe ni dans la table (tables d'avant).
    # Exige le monde ouvert et une famille du lot 6 qui sache les donner (hors de la liste des stubs du pack Mailbox,
    # sauf le flacon). Baguette ou Requiem : logique « la voile suffit pour naviguer » (rando_lot7.VARIANTE_VOILE). La
    # voile, le bouclier, le Talisman et les épées restent au départ (voir rando_lot7). Jamais essayé en jeu.
    "depart_melange": [],
    # tirages du lot 8, faits par le moteur depuis la graine et ÉCRITS dans la table (le générateur ne tire rien) :
    # destination derrière chaque porte (ordre de rando_lot8.POOLS), île d'origine de la carte de chaque île
    "tirage_entrees": None,
    "tirage_cartes": None,
    "exclus": [],                     # lieux (nom anglais) qui gardent leur objet
    "tous_lieux": True,               # exiger que TOUT ce qui est atteignable dans le jeu d'origine le reste
    "indices": {"chemin": 3, "sterile": 3, "objet": 3, "lieu": 2},
    "indices_clairs": True,
}
INDICES_MAX = 16

# Étape 4 : préréglages du lanceur. « decouverte » = palier 1 « sûr » (rando/noyau.py : aucun objet de progression
# ne bouge, donc aucune option) ; les deux autres = randomizer complet. Les options absentes sont celles par défaut.
PREREGLAGES = {
    "decouverte": {"mode": "sur", "options": {}},
    "standard": {"mode": "complet", "options": {}},
    "tout": {"mode": "complet",
             "options": {"lieux": sorted(CATEGORIES), "cles": "donjon", "grandes_cles": "donjon",
                         "cartes_donjon": "donjon", "triforce": "melange", "cadeaux": True}},
}
CHAINE_PREFIXE = "WWMP1."            # chaîne de réglages partageable : préfixe + base64url(zlib(JSON compact))
CHAINE_MAX = 4096                    # caractères acceptés en entrée
CHAINE_JSON_MAX = 16384              # octets de JSON après décompression (borne la décompression)
EXCLUS_MAX = 64                      # lieux exclus au plus (au-delà, on change de catégories)


# ------------------------------------------------------------------------------------------------ analyse
def _decouper(texte: str) -> list[str]:
    """Découpe sur les espaces du niveau de parenthèses zéro (un 'Nom d événement' entre apostrophes reste entier)."""
    out, cur, niv, cit = [], [], 0, False
    for pos, ch in enumerate(texte):
        if ch == "'" and niv == 0 and not cit and not cur:
            cit = True                              # début d'un nom d'événement
        elif ch == "'" and cit and texte[pos + 1:pos + 2] in ("", " "):
            cit = False                             # fin (TWWHD a une faute : « 'Nom'' », tolérée)
        elif cit:
            pass
        elif ch == "(":
            niv += 1
        elif ch == ")":
            niv -= 1
            if niv < 0:
                raise ErreurRando(f"parenthèse fermante en trop : {texte!r}")
        if ch == " " and niv == 0 and not cit:
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if niv or cit:
        raise ErreurRando(f"parenthèse ou apostrophe non fermée : {texte!r}")
    if cur:
        out.append("".join(cur))
    return out


def _entoure(t: str) -> bool:
    """Vrai si la première parenthèse de `t` se ferme sur son dernier caractère."""
    if not (t.startswith("(") and t.endswith(")")):
        return False
    niv = 0
    for i, ch in enumerate(t):
        niv += (ch == "(") - (ch == ")")
        if niv == 0:
            return i == len(t) - 1
    return False


def _cle(nom: str) -> str:
    """Nom tel qu'il apparaît dans une exigence : espaces -> '_', apostrophes retirées."""
    return nom.replace(" ", "_").replace("'", "")


def groupes_actifs(options) -> frozenset:
    """Groupes de lieux « donnée » (portage TWWHD, lot 1) que ces options mélangent. Tolérant : une option absente
    (table d'avant le lot, ou forme publiée) vaut « coupée »."""
    if not isinstance(options, dict):
        return frozenset()
    return frozenset(g for g, cle in OPTION_GROUPE.items() if options.get(cle) is True)


def options_publiees(opt: dict) -> dict:
    """Forme PUBLIÉE des options (message de la table, chaîne de réglages, germe du tirage) : une option du lot 1
    COUPÉE n'y figure pas. Avec ces options coupées, le germe, la table, l'empreinte et la chaîne de réglages sont
    donc exactement ceux d'avant le lot, et un client d'avant le lot (qui refuse toute clé inconnue) lit la table."""
    return {k: v for k, v in opt.items()
            if not (k in _CLES_PORTAGE and v is False) and not (k in _DEFAUTS_PORTAGE and v == _DEFAUTS_PORTAGE[k])}


def _empreinte_docs(docs: dict) -> str:
    return hashlib.sha256(json.dumps({n: docs[n] for n in FICHIERS}, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _docs_sans_donnees(docs: dict) -> dict:
    """Les données SANS ce que le lot 1 du portage a ajouté (« donnees » d'un lieu, « description_donnees »,
    « verrous_donnees » du scénario) : ce que les options coupées laissent jouer, octet pour octet comme avant."""
    out = dict(docs)
    ap, sc = docs["application"], docs["scenario_vanilla"]
    if isinstance(ap, dict):
        ap = {k: v for k, v in ap.items() if k != "description_donnees"}
        if isinstance(ap.get("lieux"), list):
            ap["lieux"] = [{k: v for k, v in a.items() if k != "donnees"} if isinstance(a, dict) else a
                           for a in ap["lieux"]]
    if isinstance(sc, dict):
        sc = {k: v for k, v in sc.items() if k != "verrous_donnees"}
    out["application"], out["scenario_vanilla"] = ap, sc
    return out


class Logique:
    """Monde + scénario d'origine + exigences compilées. Indépendant de la graine. Les verrous des lieux « donnée »
    (lot 1) ne s'appliquent qu'aux groupes de ``groupes`` : ``variante(options)`` rend la logique d'une partie."""

    def __init__(self, docs: dict, groupes=frozenset(), familles=frozenset(), lot7=(), lot8=()):
        for n in FICHIERS:
            if n not in docs:
                raise ErreurRando(f"données de logique : {n}.json manquant")
        self.docs = docs
        self.groupes = frozenset(groupes)
        if self.groupes - set(OPTION_GROUPE):
            raise ErreurRando(f"groupes de lieux « donnée » inconnus : {sorted(self.groupes - set(OPTION_GROUPE))}")
        # lot 6 : familles de lieux du randomizer par fichiers mélangées (objet réel du jeu et verrou d'histoire de
        # leurs lieux ; aucune : cette logique est exactement celle d'avant le lot)
        self.familles6 = frozenset(familles)
        if self.familles6 - set(L6.FAMILLES):
            raise ErreurRando(f"familles du lot 6 inconnues : {sorted(self.familles6 - set(L6.FAMILLES))}")
        # lot 7 : ce qui change la logique (monde ouvert, île de départ, re-combats ; () : rien)
        self.lot7 = tuple(lot7)
        if self.lot7:
            try:
                docs = L7.docs_logique(docs, self.lot7)
            except L7.ErreurLot7 as ex:
                raise ErreurRando(str(ex)) from None
        # lot 8 : sans épée, entrées et cartes TIRÉES (rando_lot8.cle_logique ; () : rien), après le lot 7
        self.lot8 = tuple(lot8)
        if self.lot8:
            try:
                docs = L8.docs_logique(docs, self.lot8)
            except L8.ErreurLot8 as ex:
                raise ErreurRando(str(ex)) from None
        self._base, self._variantes = self, {(self.groupes, self.familles6, self.lot7, self.lot8): self}
        # deux versions : celle des données sans le lot 1 (toute partie dont ces options sont coupées : inchangée
        # depuis avant le lot) et celle des données entières (partie qui mélange au moins un lieu « donnée »)
        self.version = _empreinte_docs(_docs_sans_donnees(docs))
        self.version_don = _empreinte_docs(docs)
        sc, ov = docs["scenario_vanilla"], docs["objets_vanilla"]
        self.reglages = {k: str(v).lower() for k, v in sc["reglages"].items()}
        self.equiv = dict(ov["equivalences"])
        self.familles = {k: list(v) for k, v in ov["familles"].items()}
        self.coeurs = int(ov["coeurs_depart"])
        self._receptacle, self._quart = ov["receptacle"], ov["quart"]

        # --- objets ---------------------------------------------------------------------------------
        self.objets = {o["nom"]: o for o in docs["objets"]["objets"]}
        self._obj_cle = {}
        for nom in self.objets:
            self._obj_cle.setdefault(_cle(nom), nom)
        for a, b in self.equiv.items():
            if a not in self.objets or b not in self.objets:
                raise ErreurRando(f"objets_vanilla : équivalence inconnue {a!r} -> {b!r}")
        for f, membres in self.familles.items():
            for m in [f] + membres:
                if m not in self.objets:
                    raise ErreurRando(f"objets_vanilla : famille {f!r}, objet inconnu {m!r}")

        # --- zones, sorties, événements --------------------------------------------------------------
        zones = docs["monde"]["zones"]
        self.zones = [z["nom"] for z in zones]
        self.iz = {n: i for i, n in enumerate(self.zones)}
        if len(self.iz) != len(self.zones):
            raise ErreurRando("monde : zone en double")
        self.donjons = sorted({z["donjon"] for z in zones if z.get("donjon")})
        dz = {z["nom"]: z.get("donjon") for z in zones}
        if sc["depart"] not in self.iz:
            raise ErreurRando("scénario : zone de départ inconnue")
        self.depart = self.iz[sc["depart"]]

        macros = dict(docs["macros"]["macros"])
        for k, v in sc.get("macros", {}).items():
            if k not in macros:
                raise ErreurRando(f"scénario : macro inconnue {k!r}")
            macros[k] = v
        self._macros_txt = {_cle(k): v for k, v in macros.items()}
        self._macro_fn, self._pile = {}, []
        self._ns = {"__builtins__": {}, "_F": self._famille, "_V": self._vie}
        self.references: set[str] = set()         # objets cités par au moins une exigence
        self.evts_cites: set[str] = set()
        self._cache = {}

        vg = sc["verrou_general"]
        libres = set(vg["zones_libres"])
        for z in libres:
            if z not in self.iz:
                raise ErreurRando(f"scénario : zone libre inconnue {z!r}")

        def libre(nom):
            return (nom in libres or any(nom.startswith(p) for p in vg["prefixes_libres"])
                    or dz[nom] in vg["donjons_libres"])
        vz = {}
        for v in sc.get("verrous_zones", ()):
            if v["zone"] not in self.iz:
                raise ErreurRando(f"scénario : verrou sur une zone inconnue {v['zone']!r}")
            vz.setdefault(v["zone"], []).append(v["exigence"])
        vd = {}
        for v in sc.get("verrous_donjons", ()):
            if v["donjon"] not in self.donjons:
                raise ErreurRando(f"scénario : verrou sur un donjon inconnu {v['donjon']!r}")
            vd.setdefault(v["donjon"], []).append(v["exigence"])

        sc_sorties = {}
        for v in sc.get("sorties_corrigees", ()):
            if v["de"] not in self.iz or v["vers"] not in self.iz:
                raise ErreurRando(f"scénario : sortie corrigée inconnue {v['de']!r} -> {v['vers']!r}")
            sc_sorties[(v["de"], v["vers"])] = v["exigence"]
        sc_vus = set()
        self.sorties, self.evenements = [], []
        evts_definis = set()
        for z in zones:
            src = self.iz[z["nom"]]
            for dst, ex in z["sorties"].items():
                if dst not in self.iz:
                    raise ErreurRando(f"monde : sortie vers une zone inconnue {dst!r}")
                if (z["nom"], dst) in sc_sorties:
                    ex = sc_sorties[(z["nom"], dst)]
                    sc_vus.add((z["nom"], dst))
                verrous = list(vz.get(dst, ()))
                if libre(z["nom"]) and not libre(dst):
                    verrous.append(vg["exigence"])
                if dz[dst] and dz[dst] != dz[z["nom"]]:
                    verrous.extend(vd.get(dz[dst], ()))
                for v in verrous:
                    ex = f"({ex}) and ({v})"
                self.sorties.append((src, self.iz[dst], self.compiler(ex), ex))
            for nom, ex in z["evenements"].items():
                evts_definis.add(_cle(nom))
                self.evenements.append((src, _cle(nom), self.compiler(ex), ex))
        if sc_vus != set(sc_sorties):
            raise ErreurRando(f"scénario : sorties corrigées absentes du monde {sorted(set(sc_sorties) - sc_vus)}")
        for e in sc.get("evenements", ()):
            if e["zone"] not in self.iz:
                raise ErreurRando(f"scénario : événement dans une zone inconnue {e['zone']!r}")
            evts_definis.add(_cle(e["nom"]))
            self.evenements.append((self.iz[e["zone"]], _cle(e["nom"]), self.compiler(e["exigence"]), e["exigence"]))

        # --- lieux ----------------------------------------------------------------------------------
        appli = {a["nom"]: a for a in docs["application"]["lieux"]}
        corr = sc.get("objets_corriges", {})
        par_id = {int(k): v for k, v in ov["par_id"].items()}
        jamais = {int(k): v for k, v in ov["jamais_deplaces"].items()}
        self.ids_interdits = frozenset(int(x) for x in ov["ids_interdits"])
        self.id_petite, self.id_grande = int(ov["petite_cle"]), int(ov["grande_cle"])
        self.id_carte, self.id_boussole = int(ov["carte_donjon"]), int(ov["boussole"])
        self.ids_triforce = frozenset(int(x) for x in ov["triforce"])
        self.lieux, self.il = [], {}
        for l in docs["lieux"]["lieux"]:
            a = appli.get(l["nom"])
            if a is None:
                raise ErreurRando(f"application.json : lieu manquant {l['nom']!r}")
            prefixe = l["nom"].split(" - ")[0]
            donjon = prefixe if prefixe in self.donjons else None
            objet = corr.get(l["nom"], l["objet"])
            methode = a.get("methode", "coffre")
            if methode not in METHODES:
                raise ErreurRando(f"application.json : méthode inconnue {methode!r} pour {l['nom']!r}")
            e = {"nom": l["nom"], "nom_fr": l["nom_fr"], "type": l["type"], "categories": tuple(l["categories"]),
                 "priorite": l["priorite"], "donjon": donjon, "applicable": bool(a["applicable"]),
                 "typ": METHODES[methode],
                 "idx": a.get("site") if methode == "n2" else a.get("idx"),
                 "tbox": 0 if methode == "n2" else a.get("tbox"), "id": a.get("objet"),
                 "reste": a.get("reste", ""), "histoire": False, "don": None, "interdits": frozenset()}
            dn = a.get("donnees")
            if dn is not None:
                if isinstance(dn, dict) and isinstance(dn.get("reste"), str):
                    e["reste"] = dn["reste"]                # la raison à jour (le « reste » du lieu date d'avant)
                # lieu « donnée » : mélangeable par l'option de son groupe, même sans être « applicable »
                if not (isinstance(dn, dict) and dn.get("groupe") in OPTION_GROUPE and donjon is None
                        and l["type"] == "RPX" and isinstance(dn.get("site"), int) and 0 <= dn["site"] <= 0xFF
                        and isinstance(dn.get("objet"), int) and 0 < dn["objet"] < 0xFF
                        and isinstance(dn.get("interdits"), list)
                        and all(isinstance(x, int) for x in dn["interdits"])):
                    raise ErreurRando(f"application.json : lieu « donnée » invalide {l['nom']!r}")
                if e["applicable"] and e["id"] != dn["objet"]:
                    raise ErreurRando(f"application.json : deux objets d'origine pour {l['nom']!r}")
                if dn["objet"] in self.ids_interdits or dn["objet"] in jamais:
                    raise ErreurRando(f"application.json : objet jamais déplacé dans le lieu « donnée » {l['nom']!r}")
                e["don"], e["interdits"] = (dn["groupe"], dn["site"]), frozenset(dn["interdits"])
                if not e["applicable"]:
                    e["id"] = dn["objet"]
                    if e["id"] in par_id:
                        objet = par_id[e["id"]]
                    elif self.objets[objet]["id"] != e["id"]:
                        raise ErreurRando(f"{l['nom']!r} : la table du jeu contient 0x{e['id']:02X}, la logique "
                                          f"attend {objet!r} (0x{self.objets[objet]['id']:02X})")
            if e["applicable"] and e["typ"] == TYPE_N2 and (donjon is not None or l["type"] != "RPX"):
                raise ErreurRando(f"application.json : cadeau N2 dans un donjon ou de type inattendu {l['nom']!r}")
            if e["applicable"]:
                if not (isinstance(e["idx"], int) and isinstance(e["tbox"], int) and isinstance(e["id"], int)
                        and 0 <= e["idx"] <= 0xFF and 0 <= e["tbox"] <= 0xFF and 0 < e["id"] < 0xFF):
                    raise ErreurRando(f"application.json : clé ou objet invalide pour {l['nom']!r}")
                if e["id"] in self.ids_interdits:
                    raise ErreurRando(f"application.json : identifiant interdit dans {l['nom']!r}")
                if e["id"] in par_id:
                    objet = par_id[e["id"]]
                elif self.objets[objet]["id"] != e["id"]:
                    raise ErreurRando(f"{l['nom']!r} : le coffre contient 0x{e['id']:02X}, la logique attend "
                                      f"{objet!r} (0x{self.objets[objet]['id']:02X}) : ajouter une règle par_id")
                if e["id"] in jamais:
                    e["applicable"], e["reste"] = False, jamais[e["id"]]
                    e["jamais"] = True       # coffre applicable que seule cette règle retient (voir progressifs)
            elif self.objets[objet]["id"] in par_id and e["type"] in ("Chest", "Actor"):
                objet = par_id[self.objets[objet]["id"]]
            if objet.startswith("@ "):
                if donjon is None:
                    raise ErreurRando(f"{l['nom']!r} : clé hors d'un donjon")
                objet = f"{donjon}{objet[1:]}"
            objet = self.equiv.get(objet, objet)
            if objet not in self.objets:
                raise ErreurRando(f"{l['nom']!r} : objet de logique inconnu {objet!r}")
            e["objet"] = objet
            self.il[e["nom"]] = len(self.lieux)
            self.lieux.append(e)
        for h in sc.get("lieux_histoire", ()):
            objet = self.equiv.get(h["objet"], h["objet"])
            if objet not in self.objets or h["nom"] in self.il:
                raise ErreurRando(f"scénario : lieu d'histoire invalide {h['nom']!r}")
            self.il[h["nom"]] = len(self.lieux)
            self.lieux.append({"nom": h["nom"], "nom_fr": h["nom_fr"], "type": "Histoire", "categories": (),
                               "priorite": "Never", "donjon": None, "applicable": False, "typ": TYPE_COFFRE,
                               "idx": None,
                               # case de départ du monde ouvert que l'option « depart_melange » peut vider : l'objet du
                               # jeu (le crochet de création le donne) ; None pour tout autre lieu d'histoire
                               "tbox": None, "id": L7.ID_DEPART.get(h["nom"]), "objet": objet, "histoire": True,
                               "don": None,
                               "interdits": frozenset(),
                               "reste": "objet donné par l'histoire du jeu d'origine"})
        cles = [(e["typ"], e["idx"], e["tbox"]) for e in self.lieux if e["applicable"]]
        if len(cles) != len(set(cles)):
            raise ErreurRando("application.json : deux lieux applicables partagent la même clé")
        # coffres « en conflit » (lot 2 du portage) : lieu -> site de rando.SITES_CONFLIT. Chacun doit être un coffre
        # NON applicable dont la clé et l'objet sont ceux du relevé, et aucun coffre applicable ne porte sa clé (la
        # table du stub N1 n'a donc jamais d'entrée à cette clé : l'autre niveau de la paire garde son objet)
        self.conflit = {}
        for site, (_niv, idx, tbox, objet, nom) in enumerate(SITES_CONFLIT):
            i = self.il.get(nom)
            e = self.lieux[i] if i is not None else None
            if e is None or e["applicable"] or e["don"] or e["histoire"] or e["typ"] != TYPE_COFFRE \
                    or (e["idx"], e["tbox"], e["id"]) != (idx, tbox, objet) or objet in self.ids_interdits \
                    or objet in jamais or self.objets[e["objet"]].get("id", objet) != objet \
                    or (TYPE_COFFRE, idx, tbox) in cles:
                raise ErreurRando(f"coffre en conflit {nom!r} : les données de logique ne sont pas celles du relevé")
            self.conflit[i] = site
        if len(self.conflit) != len(SITES_CONFLIT):
            raise ErreurRando("coffres en conflit : deux sites pour un même lieu")
        # objets progressifs (lot 4 du portage) : lieu -> nom de famille de rando.FAMILLES_PROG, pour chaque coffre
        # ordinaire qui porte un palier (applicable, ou retenu par la seule règle « jamais déplacé » : le coffre des
        # flèches de lumière, que l'option « progressifs » rend mélangeable). Chaque palier a exactement un coffre,
        # dont la clé n'est celle d'aucun autre lieu.
        self.progressifs = {}
        cles_conflit = {(s[1], s[2]) for s in SITES_CONFLIT}
        for nom_f, paliers in FAMILLES_PROG:
            for objet in paliers:
                lx = [i for i, e in enumerate(self.lieux)
                      if e["id"] == objet and e["typ"] == TYPE_COFFRE and (e["applicable"] or e.get("jamais"))]
                if len(lx) != 1:
                    raise ErreurRando(f"objets progressifs : le palier 0x{objet:02X} de la famille {nom_f!r} n'a pas "
                                      "exactement un coffre dans les données de logique")
                e = self.lieux[lx[0]]
                if e["don"] or e["histoire"] or lx[0] in self.conflit or (e["idx"], e["tbox"]) in cles_conflit \
                        or not (isinstance(e["idx"], int) and isinstance(e["tbox"], int)
                                and 0 <= e["idx"] < 16 and 0 <= e["tbox"] < 32) \
                        or objet in self.ids_interdits or self.objets[e["objet"]].get("id", objet) != objet \
                        or (not e["applicable"] and (TYPE_COFFRE, e["idx"], e["tbox"]) in cles):
                    raise ErreurRando(f"objets progressifs : coffre du palier 0x{objet:02X} mal décrit "
                                      f"({e['nom']!r})")
                self.progressifs[lx[0]] = nom_f
        sites = [e["don"][1] for e in self.lieux if e["don"]]
        if len(sites) != len(set(sites)):
            raise ErreurRando("application.json : deux lieux « donnée » partagent le même site")
        # lot 6 (randomizer par fichiers) : lieu -> (famille, site de rando_lot6.SITES). Chacun est un lieu NON
        # applicable de la logique (ni coffre du stub N1, ni cadeau N2, ni lieu « donnée », ni coffre en conflit ou
        # progressif : une seule voie par lieu). Famille mélangée : son objet de logique devient celui que le jeu y met
        # vraiment (relu dans le dump : TWWHD note parfois un autre objet d'origine), avec l'identifiant du jeu.
        self.fich = {}
        for k, s in enumerate(L6.SITES):
            i = self.il.get(s["nom"])
            e = self.lieux[i] if i is not None else None
            if e is None or e["applicable"] or e["don"] or e["histoire"] or i in self.conflit \
                    or i in self.progressifs or e.get("jamais"):
                raise ErreurRando(f"lot 6 : lieu {s['nom']!r} inconnu ou déjà mélangé par une autre voie")
            e["fich"] = (s["famille"], k)
            self.fich[i] = k
            if s["famille"] in self.familles6:
                objet = self.equiv.get(s["objet"], s["objet"])
                if objet not in self.objets:
                    raise ErreurRando(f"lot 6 : objet de logique inconnu {objet!r} ({s['nom']!r})")
                e["objet"], e["id"] = objet, s["id"]
        # étape 5 (rando/lot9.py) : lieux « donnée » ajoutés après le lot 1 (Kane, autres lettres, enchère à 100
        # rubis), décrits dans rando.SITES_DONNEES et non dans application.json (la version des données d'avant ne
        # change donc pas). Chacun est un lieu NON applicable, d'aucune autre voie ; son objet de logique est celui que
        # le jeu y met (relu dans le RPX par les tests). Le lieu n'est mélangé que si l'option de son groupe l'est.
        for k, s in enumerate(SITES_DONNEES):
            if s[0] not in L9.GROUPES:
                continue
            i = self.il.get(s[1])
            e = self.lieux[i] if i is not None else None
            if e is None or e["applicable"] or e["don"] or e["histoire"] or e.get("fich") or i in self.conflit \
                    or i in self.progressifs or e.get("jamais") or e["donjon"] is not None or e["type"] != "RPX":
                raise ErreurRando(f"étape 5 : lieu {s[1]!r} inconnu ou déjà mélangé par une autre voie")
            base_obj = next((l["objet"] for l in docs["lieux"]["lieux"] if l["nom"] == s[1]), None)
            if base_obj not in self.objets or self.objets[base_obj].get("id") != s[2]:
                raise ErreurRando(f"étape 5 : {s[1]!r} : la table du jeu contient 0x{s[2]:02X}, la logique attend "
                                  f"{base_obj!r}")
            e["don"], e["interdits"], e["id"] = (s[0], k), frozenset(s[5]), s[2]
            e["reste"] = L9.RAISONS[s[0]]

        deplaces = {d["lieu"]: d for d in sc.get("lieux_deplaces", ())}
        for d in deplaces.values():
            if d["lieu"] not in self.il or d["zone"] not in self.iz:
                raise ErreurRando(f"scénario : déplacement invalide {d['lieu']!r}")
        # verrous par lieu (scénario) : exigence AJOUTÉE à celle de TWWHD (jamais à sa place)
        vl = {}
        for v in sc.get("verrous_lieux", ()):
            if v["lieu"] not in self.il or self.lieux[self.il[v["lieu"]]]["histoire"]:
                raise ErreurRando(f"scénario : verrou sur un lieu inconnu {v['lieu']!r}")
            vl.setdefault(v["lieu"], []).append(v["exigence"])
        # verrous des lieux « donnée » : SEULEMENT pour les groupes mélangés. Option coupée, le lieu garde son objet
        # et son exigence d'avant le lot (sinon une vieille graine ne redonnerait plus la même table).
        vd = set()
        for v in sc.get("verrous_donnees", ()):
            e = self.lieux[self.il[v["lieu"]]] if v["lieu"] in self.il else None
            if e is None or not e["don"]:
                raise ErreurRando(f"scénario : verrou « donnée » sur un lieu qui n'en est pas un {v['lieu']!r}")
            vd.add(v["lieu"])
            if e["don"][0] in self.groupes:
                vl.setdefault(v["lieu"], []).append(v["exigence"])
        for s in SITES_DONNEES:                    # étape 5 : même verrou, seulement pour les groupes mélangés
            if s[0] in L9.GROUPES:
                vd.add(s[1])
                if s[0] in self.groupes:
                    vl.setdefault(s[1], []).append(L9.VERROU)
        for e in self.lieux:
            if e.get("fich") and e["fich"][0] in self.familles6 and e["donjon"] is None and e["fich"][0] != "boss":
                vl.setdefault(e["nom"], []).append(VERROU_FICHIERS)
        if "pnj_code" in self.familles6:
            # Ankle : le jeu regarde les coffres des statues ouverts, pas les statues (rando_lot6.ANKLE)
            vl.setdefault(L6.ANKLE, []).append(L6.VERROU_ANKLE)
        for e in self.lieux:
            if e["applicable"] and e["typ"] == TYPE_N2 and e["nom"] not in vl:
                raise ErreurRando(f"scénario : le cadeau {e['nom']!r} n'a pas de verrou d'histoire (verrous_lieux)")
            if e["don"] and e["nom"] not in vl and e["nom"] not in vd:
                raise ErreurRando(f"scénario : le lieu « donnée » {e['nom']!r} n'a pas de verrou d'histoire "
                                  "(verrous_lieux ou verrous_donnees)")

        def verrouille(nom, ex):
            for v in vl.get(nom, ()):
                ex = f"({ex}) and ({v})"
            return ex
        self.acces = []
        places = set()
        for z in zones:
            for nom, ex in z["lieux"].items():
                if nom not in self.il:
                    raise ErreurRando(f"monde : lieu inconnu {nom!r}")
                if nom in deplaces:
                    continue
                places.add(nom)
                ex = verrouille(nom, ex)
                self.acces.append((self.iz[z["nom"]], self.il[nom], self.compiler(ex), ex))
        for d in deplaces.values():
            places.add(d["lieu"])
            ex = verrouille(d["lieu"], d["exigence"])
            self.acces.append((self.iz[d["zone"]], self.il[d["lieu"]], self.compiler(ex), ex))
        for h in sc.get("lieux_histoire", ()):
            if h["zone"] not in self.iz:
                raise ErreurRando(f"scénario : lieu d'histoire dans une zone inconnue {h['zone']!r}")
            places.add(h["nom"])
            self.acces.append((self.iz[h["zone"]], self.il[h["nom"]], self.compiler(h["exigence"]), h["exigence"]))
        if places != set(self.il):
            raise ErreurRando(f"lieux sans zone : {sorted(set(self.il) - places)[:5]}")
        if "pnj_code" in self.familles6:
            # lot 6, Ankle : un événement par coffre de statue, vrai quand le coffre est atteint (même zone, même
            # exigence, verrous compris)
            par_lieu = {t[1]: t for t in self.acces}
            for nom, ev in zip(L6.COFFRES_STATUES, L6.EVTS_STATUES):
                if nom not in self.il or self.il[nom] not in par_lieu:
                    raise ErreurRando(f"lot 6 : coffre de statue inconnu {nom!r}")
                t = par_lieu[self.il[nom]]
                evts_definis.add(_cle(ev))
                self.evenements.append((t[0], _cle(ev), t[2], t[3]))
        for v in sc.get("sorties_corrigees", ()):
            for nom in v.get("suppose_fixes", ()):
                if nom not in self.il or self.lieux[self.il[nom]]["applicable"] or self.lieux[self.il[nom]]["don"] \
                        or self.il[nom] in self.conflit or self.il[nom] in self.progressifs or self.il[nom] in self.fich:
                    raise ErreurRando(f"scénario : la correction {v['de']!r} -> {v['vers']!r} suppose que "
                                      f"{nom!r} reste fixe, or ce lieu est applicable")
        manquants = self.evts_cites - evts_definis
        if manquants:
            raise ErreurRando(f"événements cités mais jamais définis : {sorted(manquants)[:5]}")
        if sc["fin"] not in self.il:
            raise ErreurRando("scénario : lieu de fin inconnu")
        self.fin = self.il[sc["fin"]]
        self.origine = [e["objet"] for e in self.lieux]
        self.progression = set(self.references)
        for f, membres in self.familles.items():
            if f in self.references:
                self.progression.update(membres)
        for cle in (self._receptacle, self._quart):
            if cle not in self.objets:
                raise ErreurRando(f"objets_vanilla : objet de vie inconnu {cle!r}")
        self._reference = None
        self._refs8 = {}                # lot 8 : références « tous les lieux » par ensemble de lieux placés

    # ---------------------------------------------------------------------------------- compilation
    def _famille(self, c, f):
        n = 0
        for m in self.familles[f]:
            if c.get(m, 0) <= 0:
                break
            n += 1
        return n

    def _vie(self, c):
        return self.coeurs + c.get(self._receptacle, 0) + c.get(self._quart, 0) // 4

    def compiler(self, texte: str):
        """Exigence (texte TWWHD) -> fonction (compteurs, événements, zones) -> bool."""
        fn = self._cache.get(texte)
        if fn is None:
            fn = self._cache[texte] = eval("lambda c, e, z: " + self._source(texte), self._ns)  # noqa: S307
        return fn

    def _compte(self, nom_cle: str, n: int, ctx: str) -> str:
        nom = self._obj_cle.get(nom_cle)
        if nom is None:
            raise ErreurRando(f"objet inconnu {nom_cle!r} dans {ctx!r}")
        nom = self.equiv.get(nom, nom)
        self.references.add(nom)
        if nom in self.familles:
            return f"(_F(c, {nom!r}) >= {n})"
        return f"(c.get({nom!r}, 0) >= {n})"

    def _source(self, texte: str) -> str:
        """Source Python d'une exigence. Tout nom inconnu est une erreur de chargement, jamais « faux »."""
        toks = _decouper(texte.strip())
        if not toks:
            raise ErreurRando("exigence vide")
        if len(toks) == 1:
            t = toks[0]
            if _entoure(t):
                return "(" + self._source(t[1:-1]) + ")"
            if t == "Nothing":
                return "True"
            if t == "Impossible":
                return "False"
            if len(t) > 2 and t[0] == "'" and t[-1] == "'":
                self.evts_cites.add(_cle(t))
                return f"({_cle(t)!r} in e)"
            if t.startswith("can_access(") and t.endswith(")"):
                zone = t[len("can_access("):-1]
                if zone not in self.iz:
                    raise ErreurRando(f"can_access : zone inconnue {zone!r}")
                return f"({self.iz[zone]} in z)"
            if t.startswith("count(") and t.endswith(")"):
                args = [a.strip() for a in t[len("count("):-1].split(",")]
                if len(args) != 2 or not args[0].isdigit() or int(args[0]) < 1:
                    raise ErreurRando(f"count mal formé : {t!r}")
                return self._compte(args[1], int(args[0]), texte)
            if t.startswith("health(") and t.endswith(")"):
                n = t[len("health("):-1]
                if not n.isdigit():
                    raise ErreurRando(f"health mal formé : {t!r}")
                self.references.update((self._receptacle, self._quart))
                return f"(_V(c) >= {int(n)})"
            if t in self._macros_txt:
                return f"{self._macro(t)}(c, e, z)"
            return self._compte(t, 1, texte)
        if len(toks) == 3 and toks[1] in ("==", "!="):
            if toks[0] not in self.reglages:
                raise ErreurRando(f"réglage inconnu {toks[0]!r} dans {texte!r}")
            return str((self.reglages[toks[0]] == toks[2].lower()) == (toks[1] == "=="))
        # un test de réglage (« Opt == valeur ») fait trois mots : on le regroupe en un seul terme
        groupes, i = [], 0
        while i < len(toks):
            if i + 2 < len(toks) and toks[i + 1] in ("==", "!="):
                groupes.append(" ".join(toks[i:i + 3]))
                i += 3
            else:
                groupes.append(toks[i])
                i += 1
        toks = groupes
        ops = set(toks[1::2])
        if len(toks) % 2 == 0 or len(ops) != 1 or not ops <= {"and", "or"} \
                or any(t in ("and", "or") for t in toks[0::2]):
            raise ErreurRando(f"exigence mal formée (« and » et « or » mêlés au même niveau ?) : {texte!r}")
        return "(" + f" {ops.pop()} ".join(self._source(t) for t in toks[0::2]) + ")"

    def _macro(self, cle: str) -> str:
        nom = self._macro_fn.get(cle)
        if nom is None:
            if cle in self._pile:
                raise ErreurRando(f"macro récursive : {cle!r}")
            self._pile.append(cle)
            src = self._source(self._macros_txt[cle])
            self._pile.pop()
            nom = self._macro_fn[cle] = f"m{len(self._macro_fn)}"
            self._ns[nom] = eval("lambda c, e, z: " + src, self._ns)  # noqa: S307
        return nom

    # ------------------------------------------------------------------------------------ parcours
    def explorer(self, contenu, bonus=None):
        """Parcours à point fixe depuis la zone de départ, inventaire vide (+ `bonus`).

        `contenu[i]` est l'objet de logique du lieu i (None = lieu vide). Rend (compteurs, pris, sphères) :
        `pris[i]` vrai si le lieu i est atteint, `sphères` la liste des vagues de lieux atteints.
        """
        c = {k: v for k, v in (bonus or {}).items() if v > 0}
        e, z = set(), {self.depart}
        ps, pe, pl = self.sorties, self.evenements, self.acces
        pris = [False] * len(self.lieux)
        spheres = []
        while True:
            change = True
            while change:
                change = False
                reste = []
                for t in ps:
                    if t[1] in z:
                        continue
                    if t[0] in z and t[2](c, e, z):
                        z.add(t[1])
                        change = True
                    else:
                        reste.append(t)
                ps = reste
                reste = []
                for t in pe:
                    if t[1] in e:
                        continue
                    if t[0] in z and t[2](c, e, z):
                        e.add(t[1])
                        change = True
                    else:
                        reste.append(t)
                pe = reste
            vague, reste = [], []
            for t in pl:
                if pris[t[1]]:
                    continue
                if t[0] in z and t[2](c, e, z):
                    pris[t[1]] = True
                    vague.append(t[1])
                else:
                    reste.append(t)
            pl = reste
            if not vague:
                return c, pris, spheres
            spheres.append(vague)
            for i in vague:
                o = contenu[i]
                if o:
                    c[o] = c.get(o, 0) + 1

    def variante(self, options) -> "Logique":
        """La logique de la partie jouée avec ces options : les mêmes données, avec les verrous des groupes de lieux
        « donnée » qu'elles mélangent (aucun : cette logique-ci, celle d'avant le lot 1). Gardée en cache."""
        try:
            g = (groupes_actifs(options), L6.familles_actives(options), L7.cle_logique(options),
                 L8.cle_logique(options))
        except L8.ErreurLot8 as ex:
            raise ErreurRando(str(ex)) from None
        base = self._base
        with _VERROU_VARIANTES:
            v = base._variantes.get(g)
            if v is None:
                v = Logique(base.docs, *g)
                # même version des données que la logique de base (les lots 7 et 8 ont leur couche dans donnees_de)
                v.version, v.version_don = base.version, base.version_don
                v._base, v._variantes = base, base._variantes
                if g[3] and (g[3][2] or g[3][3]):
                    # lot 8 : une variante par tirage (entrées, cartes) : on n'en garde que les dernières
                    tirees = [k for k in base._variantes if k[3] and (k[3][2] or k[3][3])]
                    for k in tirees[:max(0, len(tirees) - VARIANTES_TIREES_MAX + 1)]:
                        del base._variantes[k]
                base._variantes[g] = v
            return v

    def donnees_de(self, options) -> str:
        """Version des données annoncée par une table de ces options (celle d'avant le lot 1 si elles n'en mélangent
        aucun lieu « donnée »). Avec l'option « coffres_conflit » (lot 2) : cette version ET le relevé des coffres en
        conflit, pour que deux outils qui ne les connaissent pas pareil ne partagent jamais une table."""
        base = self.version_don if groupes_actifs(options) else self.version
        if isinstance(options, dict) and options.get("coffres_conflit") is True:
            releve = json.dumps([[s[4], s[1], s[2], s[3]] for s in SITES_CONFLIT], ensure_ascii=False)
            base = hashlib.sha256(f"{base}|coffres_conflit|{releve}".encode("utf-8")).hexdigest()[:16]
        if isinstance(options, dict) and options.get("progressifs") is True:
            # lot 4 : et les familles d'objets progressifs (deux outils qui ne les connaissent pas pareil ne partagent
            # jamais une table)
            releve = json.dumps([[n, list(p)] for n, p in FAMILLES_PROG], ensure_ascii=False)
            base = hashlib.sha256(f"{base}|progressifs|{releve}".encode("utf-8")).hexdigest()[:16]
        if hors_actif(options):
            # lot 5 : et la façon d'écrire un objet de donjon hors de son donjon (type d'entrée, objet d'attente)
            base = hashlib.sha256(f"{base}|hors|{TYPE_HORS}|{OBJET_HORS}".encode("utf-8")).hexdigest()[:16]
        if L6.actif(options):
            # lot 6 : et le relevé des lieux du randomizer par fichiers (sites, objets réels) et leur verrou
            # (et la règle d'Ankle : coffres des statues atteints, rando_lot6.VERROU_ANKLE)
            base = hashlib.sha256(f"{base}|lot6|{L6.releve()}|{VERROU_FICHIERS}|{L6.VERROU_ANKLE}"
                                  .encode("utf-8")).hexdigest()[:16]
        r7 = L7.releve_logique(options)
        if r7:
            # lot 7 : et la seconde couche de scénario (monde ouvert), l'île de départ, les re-combats
            base = hashlib.sha256(f"{base}|lot7|{r7}".encode("utf-8")).hexdigest()[:16]
        try:
            r8 = L8.releve_logique(options)
        except L8.ErreurLot8:
            r8 = "tirage-invalide"
        if r8:
            # lot 8 : et les règles du sans épée, de la table des entrées et des cartes (le tirage est dans les options)
            base = hashlib.sha256(f"{base}|lot8|{r8}".encode("utf-8")).hexdigest()[:16]
        rd = L7.releve_depart(options)
        if rd:
            # objets de départ mélangés : cases, rubis reçus, familles qui les donnent, correctif des bombes
            base = hashlib.sha256(f"{base}|depart|{rd}".encode("utf-8")).hexdigest()[:16]
        rb = L7.releve_bouclier(options)
        if rb:
            # boucliers progressifs : site du crochet, suites, octet lu, mots du jeu relus
            base = hashlib.sha256(f"{base}|boucliers|{rb}".encode("utf-8")).hexdigest()[:16]
        r9 = L9.releve(options)
        if r9:
            # étape 5 : et les lieux « donnée » ajoutés (Kane, lettres, enchère à 100), leur verrou, le correctif
            # « épuisé » de la boutique
            base = hashlib.sha256(f"{base}|lot9|{r9}".encode("utf-8")).hexdigest()[:16]
        return base

    def memes_donnees(self, donnees, options) -> bool:
        """Une table qui annonce ``donnees`` avec ces options vient-elle des données de ces outils ?"""
        return isinstance(donnees, str) and donnees == self.donnees_de(options)

    def reference(self, mobiles=None):
        """Lieux atteignables dans le jeu d'origine (tout à sa place). Sert de référence « tous les lieux ».

        Lot 8 (sans épée, entrées ou cartes tirées) : le placement d'origine n'a plus de sens (sans épée, la Masse est
        derrière Spectre-Ganon, que seule la Masse blesse ; une entrée mélangée peut cacher un objet d'origine derrière
        lui-même). La référence est alors, dans l'esprit de « all locations reachable » de TWWHD, ce qu'on atteint
        avec en main TOUS les objets des lieux ``mobiles`` (ceux que la table place), les autres lieux gardant le leur
        : le meilleur cas de tout remplissage ; elle doit contenir la fin (sinon aucun remplissage ne finit)."""
        if not self.lot8:
            if self._reference is None:
                _c, pris, _s = self.explorer(self.origine)
                if not pris[self.fin]:
                    raise ErreurRando("le jeu d'origine n'est pas finissable selon la logique : scénario à corriger")
                self._reference = pris
            return self._reference
        cle = frozenset(mobiles) if mobiles is not None else None
        refs = self._refs8
        if cle not in refs:
            contenu, bonus = list(self.origine), {}
            for i in (cle if cle is not None else range(len(self.lieux))):
                o = contenu[i]
                contenu[i] = None
                if o:
                    bonus[o] = bonus.get(o, 0) + 1
            _c, pris, _s = self.explorer(contenu, bonus)
            if not pris[self.fin]:
                raise ErreurRando("lot 8 : la fin n'est pas atteignable, même avec en main tous les objets placés")
            if len(refs) > 8:
                refs.clear()
            refs[cle] = pris
        return refs[cle]

    def stock(self) -> dict:
        """Tous les objets de la partie, comptés : le contenu d'origine de tous ses lieux (lot 8 : « tout en main »)."""
        out = {}
        for o in self.origine:
            if o:
                out[o] = out.get(o, 0) + 1
        return out

    def region(self, i: int) -> str:
        return self.lieux[i]["nom_fr"].split(" - ")[0]

    def nom_fr(self, objet: str) -> str:
        return self.objets[objet].get("nom_fr") or objet


_VERROU = threading.Lock()
_VERROU_VARIANTES = threading.Lock()
_LOGIQUE = {}


def charger_docs(dossier: str | None = None) -> dict:
    dossier = dossier or DOSSIER
    docs = {}
    for n in FICHIERS:
        try:
            with open(os.path.join(dossier, n + ".json"), encoding="utf-8") as f:
                docs[n] = json.load(f)
        except (OSError, ValueError) as ex:
            raise ErreurRando(f"données de logique illisibles ({n}.json) : {ex}") from ex
    return docs


def logique(dossier: str | None = None) -> Logique:
    """Logique livrée (chargée une fois)."""
    cle = os.path.abspath(dossier or DOSSIER)
    with _VERROU:
        if cle not in _LOGIQUE:
            _LOGIQUE[cle] = Logique(charger_docs(cle))
        return _LOGIQUE[cle]


# ------------------------------------------------------------------------------------------------ options
def normaliser_options(options: dict | None, lg: Logique | None = None) -> dict:
    """Valide et complète les options (structure fermée : toute clé ou valeur inconnue est refusée)."""
    if options is not None and not isinstance(options, dict):
        raise ErreurRando("options : objet JSON attendu (ex. {\"lieux\": [\"donjons\"]})")
    o = dict(options or {})
    inconnues = set(o) - set(OPTIONS_DEFAUT)
    if inconnues:
        raise ErreurRando(f"options inconnues : {sorted(inconnues)}")
    out = {}
    out["depart"] = o.get("depart", "origine")
    out["monde"] = o.get("monde", "histoire")
    lieux = o.get("lieux", OPTIONS_DEFAUT["lieux"])
    if not isinstance(lieux, (list, tuple)) or not all(isinstance(x, str) for x in lieux):
        raise ErreurRando("option lieux : liste de catégories attendue")
    if set(lieux) - set(CATEGORIES):
        raise ErreurRando(f"option lieux : catégories inconnues {sorted(set(lieux) - set(CATEGORIES))} "
                          f"(connues : {sorted(CATEGORIES)})")
    out["lieux"] = sorted(set(lieux))
    for cle in ("cles", "grandes_cles", "cartes_donjon"):
        out[cle] = o.get(cle, "origine")
        if not isinstance(out[cle], str) or out[cle] not in MODES_DONJON:
            raise ErreurRando(f"option {cle} : « origine », « donjon », « tout_donjon » ou « partout »")
    out["triforce"] = o.get("triforce", "origine")
    if out["triforce"] not in ("origine", "melange"):
        raise ErreurRando("option triforce : « origine » ou « melange »")
    for cle in ("cadeaux",) + tuple(OPTION_GROUPE.values()) + ("coffres_conflit", "progressifs", "fichiers_demo") \
            + L6.OPTIONS + L7.OPTIONS_BOOL + L8.OPTIONS_BOOL + (L9.OPTION_EPUISE,):
        out[cle] = o.get(cle, False)
        if not isinstance(out[cle], bool):
            raise ErreurRando(f"option {cle} : booléen attendu")
    out["entrees"] = o.get("entrees", L8.ENTREES_DEFAUT)
    try:
        out[L7.OPTION_DEPART] = L7.normaliser_depart(o.get(L7.OPTION_DEPART))
    except L7.ErreurLot7 as ex:
        raise ErreurRando(str(ex)) from None
    for cle in L8.TIRAGES:
        v = o.get(cle)
        out[cle] = list(v) if isinstance(v, (list, tuple)) else v
    try:
        L7.controler_options(out)
        L8.controler_options(out)
    except (L7.ErreurLot7, L8.ErreurLot8) as ex:
        raise ErreurRando(str(ex)) from None
    L9.controler_options(out)
    exclus = o.get("exclus", [])
    if not isinstance(exclus, (list, tuple)) or not all(isinstance(x, str) for x in exclus):
        raise ErreurRando("option exclus : liste de noms de lieux attendue")
    if lg is not None:
        inconnus = [x for x in exclus if x not in lg.il]
        if inconnus:
            raise ErreurRando(f"option exclus : lieux inconnus {inconnus[:5]}")
    if len(set(exclus)) > EXCLUS_MAX:
        raise ErreurRando(f"option exclus : {EXCLUS_MAX} lieux au plus")
    out["exclus"] = sorted(set(exclus))
    try:
        L7.controler_exclus_depart(out)
    except L7.ErreurLot7 as ex:
        raise ErreurRando(str(ex)) from None
    for cle in ("tous_lieux", "indices_clairs"):
        out[cle] = o.get(cle, OPTIONS_DEFAUT[cle])
        if not isinstance(out[cle], bool):
            raise ErreurRando(f"option {cle} : booléen attendu")
    ind = o.get("indices", OPTIONS_DEFAUT["indices"])
    if not isinstance(ind, dict) or set(ind) - set(OPTIONS_DEFAUT["indices"]):
        raise ErreurRando("option indices : clés chemin, sterile, objet, lieu")
    out["indices"] = {}
    for cle, defaut in OPTIONS_DEFAUT["indices"].items():
        v = ind.get(cle, defaut)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= INDICES_MAX:
            raise ErreurRando(f"option indices.{cle} : entier de 0 à {INDICES_MAX}")
        out["indices"][cle] = v
    return out


def _json(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def prereglage(nom: str, lg: Logique | None = None) -> tuple[str, dict]:
    """(mode, options normalisées) d'un préréglage du lanceur. Lève ErreurRando si le nom est inconnu."""
    p = PREREGLAGES.get(nom)
    if p is None:
        raise ErreurRando(f"préréglage inconnu : {nom!r} (connus : {sorted(PREREGLAGES)})")
    return p["mode"], normaliser_options(p["options"], lg)


def prereglage_de(mode: str, options: dict | None, lg: Logique | None = None) -> str:
    """Nom du préréglage qui donne exactement ce mode et ces options, sinon "" (réglages personnalisés)."""
    if mode == "sur":
        return "decouverte"
    try:
        opt = normaliser_options(options, lg)
    except ErreurRando:
        return ""
    for nom, p in PREREGLAGES.items():
        if p["mode"] == mode and normaliser_options(p["options"], lg) == opt:
            return nom
    return ""


def chaine_reglages(mode: str, options: dict | None = None, lg: Logique | None = None) -> str:
    """Chaîne de réglages partageable : mode et options (PAS la graine, qui se donne à part). Deux hôtes qui
    entrent la même chaîne et la même graine, avec les mêmes outils, obtiennent la même table (même empreinte)."""
    if mode not in ("sur", "complet"):
        raise ErreurRando(f"mode inconnu : {mode!r} (sur, complet)")
    o = {}
    if mode == "complet":
        # lot 8 : les tirages (entrées, cartes) ne sont pas des réglages : la même graine les refait à l'identique
        o = {k: v for k, v in options_publiees(normaliser_options(options, lg)).items() if k not in L8.TIRAGES}
    doc = {"m": mode, "o": o}
    brut = zlib.compress(_json(doc).encode("utf-8"), 9)
    return CHAINE_PREFIXE + base64.urlsafe_b64encode(brut).decode("ascii").rstrip("=")


def reglages_depuis_chaine(chaine, lg: Logique | None = None) -> tuple[str, dict]:
    """(mode, options normalisées) d'une chaîne de réglages. Tout est revalidé (la chaîne vient d'un autre joueur) :
    taille bornée, décompression bornée, structure fermée des options. Lève ErreurRando, sans rien appliquer."""
    if not isinstance(chaine, str):
        raise ErreurRando("chaîne de réglages : texte attendu")
    c = "".join(chaine.split())
    if not c.startswith(CHAINE_PREFIXE):
        raise ErreurRando(f"chaîne de réglages : elle doit commencer par « {CHAINE_PREFIXE} »")
    if len(c) > CHAINE_MAX:
        raise ErreurRando("chaîne de réglages : trop longue")
    corps = c[len(CHAINE_PREFIXE):]
    try:
        brut = base64.b64decode(corps + "=" * (-len(corps) % 4), altchars=b"-_", validate=True)
        d = zlib.decompressobj()
        texte = d.decompress(brut, CHAINE_JSON_MAX + 1)
        if len(texte) > CHAINE_JSON_MAX or d.unconsumed_tail or not d.eof or d.unused_data:
            raise ErreurRando("chaîne de réglages : contenu trop grand ou tronqué")
        doc = json.loads(texte.decode("utf-8"))
    except ErreurRando:
        raise
    except (binascii.Error, zlib.error, ValueError, RecursionError) as ex:
        raise ErreurRando("chaîne de réglages : illisible (copie incomplète ?)") from ex
    if not isinstance(doc, dict) or set(doc) != {"m", "o"} or doc["m"] not in ("sur", "complet") \
            or not isinstance(doc["o"], dict):
        raise ErreurRando("chaîne de réglages : contenu inattendu (faite par des outils plus récents ?)")
    if doc["m"] == "sur":
        if doc["o"]:
            raise ErreurRando("chaîne de réglages : le mode « sur » n'a pas d'option")
        return "sur", {}
    opt = normaliser_options(doc["o"], lg if lg is not None else logique())   # lieux exclus : noms connus
    if doc["o"] not in (options_publiees(opt), opt):      # forme publiée (option du lot 1 coupée : absente)
        raise ErreurRando("chaîne de réglages : options incomplètes (faite par d'autres outils ?)")
    return "complet", opt


def indices_depuis_texte(texte) -> dict | None:
    """Nombres d'indices depuis la ligne de commande : « chemin=3,sterile=3,objet=3,lieu=2 » (les genres absents
    gardent leur défaut), ou « aucun » / « 0 » (aucun indice). None ou "" : défaut du moteur. Lève ErreurRando."""
    if texte is None or isinstance(texte, dict):
        return texte
    t = str(texte).strip().lower()
    if not t:
        return None
    if t in ("aucun", "none", "0"):
        return {k: 0 for k in OPTIONS_DEFAUT["indices"]}
    out = dict(OPTIONS_DEFAUT["indices"])
    for bout in t.replace(";", ",").split(","):
        cle, sep, v = bout.strip().partition("=")
        cle = cle.strip()
        if not sep or cle not in out or not v.strip().isdigit():
            raise ErreurRando(f"indices : « {bout.strip()} » incompris (attendu : chemin=N,sterile=N,objet=N,lieu=N, "
                              f"N de 0 à {INDICES_MAX})")
        out[cle] = int(v.strip())
    return out


# ------------------------------------------------------------------------------------------- remplissage
class Plan:
    """Ce que les options rendent mobile : coffres mélangés, et restriction de chaque pièce."""

    def __init__(self, lg: Logique, opt: dict):
        cats = {CATEGORIES[k] for k in opt["lieux"]}
        exclus = set(opt["exclus"])
        self.mobiles, self.raisons = [], {}
        # lieu d'origine de la pièce -> (donjon, index de sauvegarde) imposé : une clé ramassée compte pour
        # le donjon du NIVEAU où l'on est, donc elle ne doit pas quitter l'index de sauvegarde de son coffre
        self.restriction = {}
        # clé de table de chaque lieu mobile (type, index, coffre) : celle du lieu, ou (2, site, 0) quand il est
        # mélangé par une écriture de données ; objets interdits à un lieu (boutique, personnages à table)
        self.cle, self.interdits = {}, {}
        # lot 2 du portage (option « coffres_conflit ») : lieux mobiles qui ne sont PAS des coffres (cadeau du stub
        # N2, écriture de données). Les objets de rando.OBJETS_CONFLIT n'y vont jamais : seule la liste du stub N1
        # (wwmp_rnd4_permis) les accepte. Vide quand l'option est coupée.
        self.sans_conflit = set()
        conflits = opt.get("coffres_conflit") is True
        # lot 4 du portage (option « progressifs ») : lieux mobiles qui ne sont pas des coffres ORDINAIRES (cadeau,
        # écriture de données, coffre en conflit) : un palier d'objet progressif n'y va jamais (le serveur compte les
        # lieux ouverts par leur clé de coffre). ``prog`` : coffres progressifs que l'option rend mobiles ; une
        # famille n'est libérée qu'ENTIÈRE (si un de ses coffres reste fixe, exclu ou hors des catégories, ceux que
        # la règle « jamais déplacé » retenait restent fixes aussi : des flèches de lumière mélangées sans l'arc
        # pourraient être ouvertes avant lui et rétrograder la case). Vides quand l'option est coupée.
        self.sans_prog, self.prog = set(), set()
        # lot 5 du portage : pièce (objet de donjon mobile) -> « tout_donjon » ou « partout » quand son option la
        # laisse sortir de son donjon ; ``hors_ok`` : lieux mobiles qui peuvent porter un objet de donjon ÉTRANGER
        # (coffres ordinaires seulement : le serveur reconnaît le coffre ouvert par sa clé (index, numéro), qu'un
        # cadeau, une écriture de données ou un coffre en conflit n'ont pas). Vides avec les anciens modes.
        self.portee, self.hors_ok = {}, set()
        # lot 6 : pièces qui ne peuvent aller QUE dans un lieu du lot (objet hors de la liste des stubs du pack Mailbox),
        # et parmi elles celles qui ne vont que chez une Grande Fée (paliers de bourse, sacs, carquois). Vides quand
        # aucune famille du lot n'est mélangée.
        self.fich_seul, self.fee_seul, self.fich, self.fees = set(), set(), set(), set()
        # étape 5 : pièces qui ne peuvent aller QUE dans un lieu « donnée » (objets des lettres et de l'enchère à 100
        # rubis hors de la liste du pack : seul un octet écrit par le client les donne), et lieux « donnée » mobiles.
        # Vides quand aucune option de l'étape n'est allumée (aucun tirage de plus : tables d'avant).
        self.don_seul, self.dons = set(), set()
        # objets de départ mélangés (option « depart_melange », rando_lot7) : cases de départ vidées (lieux mobiles qui
        # ne reçoivent qu'un rubis, jamais donné), et numéros de ces objets : TOUTE pièce de ce numéro ne va que dans un
        # lieu du lot 6 (hors de la liste des stubs ; le coffre du bateau pirate porte aussi les bombes). Sauf un objet
        # de la liste du pack (le flacon, rando.PERMIS2), qui va partout comme les autres flacons. Vides quand l'option
        # est coupée (aucun tirage de plus : tables d'avant).
        self.depart = set()
        cases = {l: k for k, (n, _o, l) in enumerate(L7.DEPART_MELANGEABLES) if n in L7.departs_melanges(opt)}
        ids_dep = frozenset(x for x in L7.ids_depart_melanges(opt) if x not in PERMIS2)
        don9 = bool(L9.groupes_actifs(opt))
        familles = L6.familles_actives(opt)
        modes = {lg.id_petite: opt["cles"], lg.id_grande: opt["grandes_cles"],
                 lg.id_carte: opt["cartes_donjon"], lg.id_boussole: opt["cartes_donjon"]}
        progs = opt.get("progressifs") is True
        if progs:
            for nom_f, _paliers in FAMILLES_PROG:
                lx = [i for i, f in lg.progressifs.items() if f == nom_f]
                if not any(self._fixe(lg, lg.lieux[i], opt, cats, exclus, False, False, True) for i in lx):
                    self.prog.update(lx)
        for i, e in enumerate(lg.lieux):
            if e["histoire"] and e["nom"] in cases:
                if e["id"] != L7.DEPART_MELANGEABLES[cases[e["nom"]]][1] or e["nom"] in exclus:
                    raise ErreurRando(f"objets de départ mélangés : case {e['nom']!r} mal décrite ou exclue")
                self.mobiles.append(i)
                self.cle[i] = (TYPE_DEPART, cases[e["nom"]], 0)
                self.depart.add(i)
                if e["id"] in ids_dep:
                    self.fich_seul.add(i)
                self.interdits[i] = frozenset(x for x in range(256) if x not in L7.SANS_VALEUR_DEPART)
                if progs:
                    self.sans_prog.add(i)
                continue
            don = bool(e["don"]) and opt[OPTION_GROUPE[e["don"][0]]]
            conflit = conflits and i in lg.conflit
            fich = bool(e.get("fich")) and e["fich"][0] in familles
            r = self._fixe(lg, e, opt, cats, exclus, don, conflit, i in self.prog, fich)
            if r:
                self.raisons[i] = r
                continue
            self.mobiles.append(i)
            if fich:
                site = e["fich"][1]
                self.cle[i] = (TYPE_FICHIER, site, 0)
                self.fich.add(i)
                if L6.SITES[site]["voie"] == "fee":
                    self.fees.add(i)
                self.interdits[i] = frozenset(x for x in range(256) if not L6.accepte(site, x))
                if e["id"] in L6.PALIERS_FEES:
                    self.fee_seul.add(i)
                elif e["id"] not in PERMIS2:
                    self.fich_seul.add(i)
                if progs:
                    self.sans_prog.add(i)
                continue
            if don:
                self.cle[i] = (TYPE_DONNEES, e["don"][1], 0)
                if e["interdits"]:
                    self.interdits[i] = e["interdits"]
                if don9:
                    self.dons.add(i)
                    if e["id"] in OBJETS_DON_SEULS:
                        self.don_seul.add(i)
            elif conflit:
                self.cle[i] = (TYPE_CONFLIT, lg.conflit[i], 0)
            else:
                self.cle[i] = (e["typ"], e["idx"], e["tbox"])
            if conflits and self.cle[i][0] in (TYPE_N2, TYPE_DONNEES):
                self.sans_conflit.add(i)
            if progs and self.cle[i][0] != TYPE_COFFRE:
                self.sans_prog.add(i)
            if e["id"] in (lg.id_petite, lg.id_grande, lg.id_carte, lg.id_boussole):
                self.restriction[i] = (e["donjon"], e["idx"])
                if modes[e["id"]] in MODES_HORS:
                    self.portee[i] = modes[e["id"]]
            if self.cle[i][0] == TYPE_COFFRE:
                self.hors_ok.add(i)
        if not self.portee:
            self.hors_ok = set()
        if len(self.depart) != len(cases):
            raise ErreurRando("objets de départ mélangés : exigent le scénario du monde ouvert (cases de départ "
                              "introuvables)")
        for i in self.mobiles:
            if lg.lieux[i]["id"] in ids_dep and i not in self.fees:
                self.fich_seul.add(i)        # même numéro qu'un objet de départ mélangé (bombes du bateau pirate)

    def etranger(self, lg, p, coffre) -> bool:
        """La pièce ``p`` (lieu d'origine) est-elle un objet de donjon qui, posé dans ``coffre``, serait hors du
        sien ? (Il y passe alors par le serveur : objet d'attente dans le coffre, entrée TYPE_HORS dans la table.)"""
        d = self.restriction.get(p)
        return d is not None and (lg.lieux[coffre]["donjon"], lg.lieux[coffre]["idx"]) != d

    def hors_permis(self, lg, p, coffre) -> bool:
        """Un objet de donjon étranger est-il permis dans ce coffre avec le mode de son option ?"""
        mode = self.portee.get(p)
        if mode is None or coffre not in self.hors_ok:
            return False
        return mode == "partout" or lg.lieux[coffre]["donjon"] is not None

    @staticmethod
    def _fixe(lg, e, opt, cats, exclus, don=False, conflit=False, prog=False, fich=False) -> str:
        if fich:
            # lieu du lot 6 dont la famille est mélangée : son option décide (ni catégories ni « cadeaux ») ; la
            # Triforce suit son option comme partout
            if e["nom"] in exclus:
                return "lieu exclu par l'option exclus"
            if e["id"] in lg.ids_triforce and opt["triforce"] == "origine":
                return "Triforce d'origine (option triforce)"
            return ""
        if e.get("fich") and not e["applicable"]:
            # lot 6, famille coupée : la raison à jour (l'ancienne disait « impossible sans toucher aux fichiers »)
            return (f"lieu du randomizer par fichiers : option {L6.OPTION[e['fich'][0]]} coupée (pack WWMP_Rando "
                    "généré sur chaque PC ; jamais essayé en jeu)")
        if don:
            # lieu « donnée » dont l'option est allumée : elle seule décide (ni « cadeaux » ni catégories)
            return "lieu exclu par l'option exclus" if e["nom"] in exclus else ""
        if not e["applicable"] and not conflit and not prog:
            # (un coffre « en conflit » dont l'option est allumée suit les règles des coffres, ci-dessous ; de même
            # un coffre d'objet progressif dont la famille est libérée par l'option « progressifs »)
            return e["reste"] or "non applicable en mémoire"
        if e["nom"] in exclus:
            return "lieu exclu par l'option exclus"
        if e["typ"] == TYPE_N2 and not opt["cadeaux"]:
            return "cadeau de personnage ou prix : non mélangé (option cadeaux désactivée)"
        if not set(e["categories"]) <= cats:
            return "catégorie de lieu non mélangée (option lieux)"
        i = e["id"]
        if i in (lg.id_petite, lg.id_grande, lg.id_carte, lg.id_boussole) and e["donjon"] is None:
            return "objet de donjon hors d'un donjon"
        if i == lg.id_petite and opt["cles"] == "origine":
            return "petites clés d'origine (option cles)"
        if i == lg.id_grande and opt["grandes_cles"] == "origine":
            return "grandes clés d'origine (option grandes_cles)"
        if i in (lg.id_carte, lg.id_boussole) and opt["cartes_donjon"] == "origine":
            return "cartes et boussoles d'origine (option cartes_donjon)"
        if i in lg.ids_triforce and opt["triforce"] == "origine":
            return "Triforce d'origine (option triforce)"
        return ""


def _melanger(liste: list, flux: Flux) -> None:
    for i in range(len(liste) - 1, 0, -1):
        j = flux.sous(i + 1)
        liste[i], liste[j] = liste[j], liste[i]


def _remplir(lg: Logique, plan: Plan, flux: Flux):
    """Un essai de remplissage « assumed fill ». Rend {coffre: pièce (coffre d'origine)} ou None."""
    contenu = list(lg.origine)
    for i in plan.mobiles:
        contenu[i] = None
    pieces = list(plan.mobiles)
    _melanger(pieces, flux)
    prog = [p for p in pieces if lg.origine[p] in lg.progression]
    reste_p = [p for p in pieces if lg.origine[p] not in lg.progression]
    # pièces contraintes d'abord (elles ont le moins de coffres possibles) ; tri stable : l'ordre tiré reste
    prog.sort(key=lambda p: (p not in plan.restriction, p not in plan.fee_seul, p not in plan.fich_seul,
                             p not in plan.don_seul))
    libres = set(plan.mobiles)
    place = {}
    en_main = {}
    for p in prog:
        en_main[lg.origine[p]] = en_main.get(lg.origine[p], 0) + 1

    poses = set()

    def poser(p, coffre):
        place[coffre] = p
        poses.add(p)
        libres.discard(coffre)
        contenu[coffre] = lg.origine[p]

    def permis(p, coffre):
        if (p in plan.fich_seul or p in plan.fee_seul) and coffre not in plan.fich:
            return False                 # lot 6 : objet hors de la liste des stubs : seulement dans un lieu du lot
        if p in plan.don_seul and coffre not in plan.dons:
            return False                 # étape 5 : objet hors de la liste du pack : seulement dans un lieu « donnée »
        if coffre in plan.depart:
            # case de départ vidée (option « depart_melange ») : un rubis seulement (jamais donné : rien de logique)
            return p not in plan.restriction and lg.lieux[p]["id"] in L7.SANS_VALEUR_DEPART
        if coffre in plan.dons and p not in plan.don_seul and plan.don_seul:
            # étape 5 : garder assez de lieux « donnée » pour les objets qui ne peuvent aller que là
            reste = sum(1 for q in plan.don_seul if q not in poses)
            if reste and sum(1 for k in libres if k in plan.dons) - 1 < reste:
                return False
        if coffre in plan.fich and coffre not in plan.fees and p not in plan.fich_seul and plan.fich_seul:
            # lot 6 : garder assez de lieux du lot pour les objets qui ne peuvent aller que là
            reste = sum(1 for q in plan.fich_seul if q not in poses)
            if reste and sum(1 for k in libres if k in plan.fich and k not in plan.fees) - 1 < reste:
                return False
        d = plan.restriction.get(p)
        if d is not None and (lg.lieux[coffre]["donjon"], lg.lieux[coffre]["idx"]) != d:
            # objet de donjon hors du sien : jamais avec les anciens modes ; avec « tout_donjon » ou « partout »
            # (lot 5), dans un coffre ordinaire seulement (le coffre porte un objet d'attente, le serveur crédite)
            return plan.hors_permis(lg, p, coffre)
        if coffre in plan.sans_conflit and lg.lieux[p]["id"] in OBJETS_CONFLIT:
            return False                 # objet d'un coffre en conflit : dans un coffre seulement (liste du stub N1)
        if coffre in plan.sans_prog and lg.lieux[p]["id"] in OBJETS_PROG:
            return False                 # palier d'objet progressif : dans un coffre ordinaire seulement
        if coffre in plan.interdits:
            # lieu « donnée » : jamais d'objet de donjon (clé réservée aux coffres), ni d'objet interdit à cet
            # endroit, sauf le sien (qui n'y change rien)
            return d is None and (lg.lieux[p]["id"] == lg.lieux[coffre]["id"]
                                  or lg.lieux[p]["id"] not in plan.interdits[coffre])
        return True

    for n, p in enumerate(prog):
        en_main[lg.origine[p]] -= 1
        _c, pris, _s = lg.explorer(contenu, en_main)
        cands = [k for k in sorted(libres) if pris[k] and permis(p, k)]
        if not cands:
            return None
        poser(p, cands[flux.sous(len(cands))])
        if n + 1 == len(prog) or (p in plan.restriction and prog[n + 1] not in plan.restriction):
            # juste après les pièces de progression contraintes : les pièces sans valeur contraintes
            for q in [q for q in reste_p if q in plan.restriction]:
                cands = [k for k in sorted(libres) if permis(q, k)]
                if not cands:
                    return None
                poser(q, cands[flux.sous(len(cands))])
            reste_p = [q for q in reste_p if q not in plan.restriction]
    for q in [q for q in reste_p if q in plan.restriction]:      # aucune pièce de progression du tout
        cands = [k for k in sorted(libres) if permis(q, k)]
        if not cands:
            return None
        poser(q, cands[flux.sous(len(cands))])
    reste_p = [q for q in reste_p if q not in plan.restriction]
    # lot 6 : pièces sans valeur qui ne vont que dans un lieu du lot (paliers des fées d'abord) ; aucun tirage de plus
    # quand aucune famille n'est mélangée
    for q in [q for q in reste_p if q in plan.fee_seul] + [q for q in reste_p if q in plan.fich_seul] \
            + [q for q in reste_p if q in plan.don_seul]:     # (étape 5 : vide sans ses options)
        cands = [k for k in sorted(libres) if permis(q, k)]
        if not cands:
            return None
        poser(q, cands[flux.sous(len(cands))])
    reste_p = [q for q in reste_p if q not in plan.fee_seul and q not in plan.fich_seul and q not in plan.don_seul]
    # lieux qui ne sont pas des coffres, seulement s'il reste une pièce sans valeur qu'ils refusent (objet d'un coffre
    # en conflit) : ensemble vide quand l'option « coffres_conflit » est coupée
    etroits = plan.sans_conflit if any(lg.lieux[q]["id"] in OBJETS_CONFLIT for q in reste_p) else ()
    if plan.sans_prog and any(lg.lieux[q]["id"] in OBJETS_PROG for q in reste_p):
        etroits = set(etroits) | plan.sans_prog      # (option « progressifs » : paliers sans valeur de logique)
    for coffre in sorted(k for k in libres if k in plan.interdits or k in etroits):
        # lieux à objets interdits encore libres : une pièce sans valeur qui y est permise (aucun tirage de plus
        # quand aucun lieu de ce genre n'est mélangé : les tables des autres options ne changent pas)
        cands = [q for q in reste_p if permis(q, coffre)]
        if not cands:
            return None
        q = cands[flux.sous(len(cands))]
        reste_p.remove(q)
        poser(q, coffre)
    coffres = sorted(libres)
    if len(coffres) != len(reste_p):
        return None
    for coffre, q in zip(coffres, reste_p):       # reste_p est déjà dans un ordre tiré au sort
        poser(q, coffre)
    return place


class Resultat:
    """Table calculée : permutation des objets des coffres mobiles, avec ses preuves."""

    def __init__(self, lg: Logique, graine: str, options: dict, place: dict, essai: int, plan: Plan | None = None):
        self.lg, self.graine, self.options, self.essai = lg, graine, options, essai
        self.place = dict(place)                       # coffre -> coffre d'origine de la pièce
        plan = plan or Plan(lg, options)
        # lot 5 : un objet de donjon hors du sien n'est PAS écrit dans son coffre (le pack le compterait pour le
        # donjon courant) : le coffre porte l'objet d'attente, et une entrée TYPE_HORS dit l'objet réel et son donjon
        self.hors = {}                                 # coffre -> (objet réel, index de sauvegarde de son donjon)
        table = []
        for k, p in place.items():
            nouveau = lg.lieux[p]["id"]
            if plan.etranger(lg, p, k):
                if not plan.hors_permis(lg, p, k):
                    raise ErreurRando(f"table : objet de donjon hors du sien dans {lg.lieux[k]['nom']!r}")
                self.hors[k] = (nouveau, plan.restriction[p][1])
                table.append([TYPE_HORS, plan.cle[k][1], plan.cle[k][2], nouveau, plan.restriction[p][1]])
                nouveau = OBJET_HORS
            table.append(list(plan.cle[k]) + [lg.lieux[k]["id"], nouveau])
        self.table = sorted(table)
        self.contenu = list(lg.origine)
        for k, p in place.items():
            self.contenu[k] = lg.origine[p]
        self.emp = empreinte(self.table)
        self.spheres = []
        self.indices = []

    def changes(self) -> int:
        return sum(1 for k, p in self.place.items()
                   if self.lg.lieux[k]["id"] != self.lg.lieux[p]["id"] or k in self.hors)

    def message(self) -> dict:
        return {"v": FORMAT, "algo": ALGO, "donnees": self.lg.donnees_de(self.options),
                "options": options_publiees(self.options),
                "graine": self.graine, "t": [list(t) for t in self.table], "emp": self.emp}

    def spoiler_texte(self) -> str:
        lg = self.lg
        out = [f"Randomizer complet WWMP ({ALGO}) - graine : {self.graine}",
               f"Données : {lg.donnees_de(self.options)} - empreinte : {self.emp} - lieux mélangés : {len(self.place)} "
               f"(dont {self.changes()} changés ; cadeaux de personnages : "
               f"{sum(1 for t in self.table if t[0] == TYPE_N2)} ; courrier, boutique, enchères et personnages "
               f"par écriture de données : {sum(1 for t in self.table if t[0] == TYPE_DONNEES)}"
               + (f" ; coffres en conflit : {sum(1 for t in self.table if t[0] == TYPE_CONFLIT)}"
                  if self.options.get("coffres_conflit") else "")
               + (" ; objets progressifs : le Ne lieu ouvert d'une famille donne son palier N, quel que soit le "
                  "coffre (" + ", ".join(f"{n} : " + " puis ".join(f"0x{o:02X}" for o in p)
                                         for n, p in FAMILLES_PROG) + ")"
                  if self.options.get("progressifs") else "")
               + (" ; boucliers progressifs : le bouclier du héros donne le bouclier miroir à qui a déjà un bouclier "
                  "(le coffre du Mini-Boss Stalfos reste fixe et donne toujours le miroir)"
                  if L7.boucliers_progressifs(self.options) else "")
               + (f" ; objets de donjon hors de leur donjon : {len(self.hors)} (le coffre montre un rubis jaune, le "
                  "serveur donne l'objet au bon donjon pour tout le groupe)" if self.hors else "")
               + (f" ; lieux du randomizer par fichiers (pack WWMP_Rando généré) : "
                  f"{sum(1 for t in self.table if t[0] == TYPE_FICHIER)}"
                  if L6.actif(self.options) else "") + ")",
               "Options : " + _json(self.options),
               "Chaîne de réglages (à donner avec la graine pour refaire la même partie) : "
               + chaine_reglages("complet", self.options, lg), "", "== Coffres et cadeaux =="]
        for k in sorted(self.place, key=lambda k: lg.lieux[k]["nom_fr"]):
            p = self.place[k]
            if lg.lieux[k]["id"] != lg.lieux[p]["id"] or k in self.hors:
                out.append(f"{lg.lieux[k]['nom_fr']} : {lg.nom_fr(lg.origine[k])} -> {lg.nom_fr(lg.origine[p])}"
                           + (" [hors de son donjon : donné par le serveur]" if k in self.hors else ""))
        out += L8.spoiler(self.options)          # lot 8 : sans épée, entrées et cartes tirées (rien si coupé)
        if L7.departs_melanges(self.options):
            out += ["", "== Objets de départ mélangés (option depart_melange) ==",
                    "Plus donnés à la création de la partie : ils sont dans le monde (lieux du randomizer par fichiers). "
                    "Le rubis tiré pour chaque case de départ vidée n'est pas donné. La voile, le Talisman, les épées "
                    "(sauf option sans épée) et le bouclier (sauf boucliers progressifs) restent au départ."]
            if L7.voile_seule(self.options):
                out.append("Baguette ou Requiem à trouver : la logique compte qu'on navigue avec la seule voile (sans "
                           "choisir le vent : on avance partout sauf face au vent, en tirant des bords) ; jouer un "
                           "chant et choisir un vent précis exigent toujours la Baguette et le Requiem.")
            for k in sorted(i for i in self.place if lg.lieux[i]["histoire"]):
                out.append(f"{lg.lieux[k]['nom_fr']} : retiré du départ ({lg.nom_fr(lg.origine[k])})")
        out += ["", "== Sphères (objets de progression, dans l'ordre où la logique les atteint) =="]
        for n, vague in enumerate(self.spheres):
            for i, objet in vague:
                marque = "" if i in self.place else " [fixe]"
                out.append(f"{n:2d}. {lg.lieux[i]['nom_fr']} : {lg.nom_fr(objet)}{marque}")
        out += ["", "== Indices =="]
        out += [f"[{ind['type']}] {ind['texte']}" for ind in self.indices] or ["(aucun)"]
        if self.indices:
            out += ["", "== Hints (English) =="]
            out += [f"[{ind['type']}] {ind.get('texte_en') or ind['texte']}" for ind in self.indices]
        return "\n".join(out) + "\n"


def empreinte(table) -> str:
    return hashlib.sha256(_json([list(t) for t in table]).encode("utf-8")).hexdigest()[:16]


def _spheres(lg: Logique, contenu) -> list:
    _c, _pris, vagues = lg.explorer(contenu)
    out = []
    for vague in vagues:
        ligne = [(i, contenu[i]) for i in sorted(vague, key=lambda i: lg.lieux[i]["nom"])
                 if contenu[i] in lg.progression]
        out.append(ligne)
    while out and not out[-1]:
        out.pop()
    return out


def _indices(lg: Logique, res: Resultat, flux: Flux) -> list:
    """Indices déterministes : chemin (région indispensable), stérile, objet, lieu. Chaque indice porte son texte
    français (``texte``) et anglais (``texte_en``) ; les tirages ne dépendent pas de la langue."""
    opt, out = res.options["indices"], []
    # (cases de départ vidées par l'option « depart_melange » : pas des lieux à fouiller, jamais dans un indice)
    mobiles = sorted(k for k in res.place if not lg.lieux[k]["histoire"])
    majeurs = [k for k in mobiles if res.contenu[k] in lg.progression]
    en = {}                                           # région (nom français) -> nom anglais
    for k in mobiles:
        en.setdefault(lg.region(k), lg.lieux[k]["nom"].split(" - ")[0])

    def objet_en(o):
        return lg.objets[o].get("nom") or o

    # régions « sur le chemin » : retirer l'objet du coffre rend la partie infinissable
    chemin = []
    for k in majeurs:
        essai = list(res.contenu)
        essai[k] = None
        if not lg.explorer(essai)[1][lg.fin]:
            chemin.append(k)
    regions_chemin = sorted({lg.region(k) for k in chemin})
    _melanger(regions_chemin, flux)
    for r in regions_chemin[:opt["chemin"]]:
        out.append({"type": "chemin", "region": r,
                    "texte": f"{r} est sur le chemin du héros : un objet indispensable s'y trouve.",
                    "texte_en": f"{en[r]} is on the path of the hero: a required item is there."})
    steriles = sorted({lg.region(k) for k in mobiles} - {lg.region(k) for k in majeurs})
    _melanger(steriles, flux)
    for r in steriles[:opt["sterile"]]:
        out.append({"type": "sterile", "region": r,
                    "texte": f"{r} : aucun coffre mélangé n'y contient d'objet de progression.",
                    "texte_en": f"{en[r]}: no shuffled chest there holds a progression item."})
    dits = set(regions_chemin[:opt["chemin"]])
    cands = [k for k in majeurs if res.place[k] != k and lg.lieux[k]["id"] not in (lg.id_petite, lg.id_grande)
             and lg.lieux[res.place[k]]["id"] not in (lg.id_petite, lg.id_grande) and lg.region(k) not in dits]
    _melanger(cands, flux)
    for k in cands[:opt["objet"]]:
        out.append({"type": "objet", "region": lg.region(k), "objet": res.contenu[k],
                    "texte": f"{lg.nom_fr(res.contenu[k])} se trouve dans la région : {lg.region(k)}.",
                    "texte_en": f"{objet_en(res.contenu[k])} is in the region: {en[lg.region(k)]}."})
    deja = set(cands[:opt["objet"]])
    cands = [k for k in mobiles if k not in deja and res.place[k] != k
             and lg.lieux[k]["priorite"] in ("Always", "Sometimes")]
    cands.sort(key=lambda k: (lg.lieux[k]["priorite"] != "Always", lg.lieux[k]["nom"]))
    toujours = [k for k in cands if lg.lieux[k]["priorite"] == "Always"]
    autres = [k for k in cands if lg.lieux[k]["priorite"] != "Always"]
    _melanger(toujours, flux)
    _melanger(autres, flux)
    for k in (toujours + autres)[:opt["lieu"]]:
        out.append({"type": "lieu", "lieu": lg.lieux[k]["nom"], "objet": res.contenu[k],
                    "texte": f"{lg.lieux[k]['nom_fr']} contient : {lg.nom_fr(res.contenu[k])}.",
                    "texte_en": f"{lg.lieux[k]['nom']} holds: {objet_en(res.contenu[k])}."})
    if not res.options["indices_clairs"]:
        for ind in out:
            ind["texte"] = _voiler(ind["texte"])
            ind["texte_en"] = _voiler(ind["texte_en"], "en")
    return out


def _voiler(texte: str, langue: str = "fr") -> str:
    """Indice « obscur » : même information, tournure du vieil Ho Ho."""
    return ("Rumor has it... " if langue == "en" else "On raconte que... ") + texte


def verifier_logique(lg: Logique, contenu, tous_lieux: bool, mobiles=None) -> list:
    """Preuve interne : la fin est atteinte, et (option) tout ce qui l'est dans le jeu d'origine le reste (lot 8 : tout
    ce qu'on atteint avec en main les objets des lieux ``mobiles``, voir ``Logique.reference``)."""
    _c, pris, _s = lg.explorer(contenu)
    pb = []
    if not pris[lg.fin]:
        pb.append("la partie n'est pas finissable (Ganondorf hors d'atteinte)")
    if tous_lieux:
        ref = lg.reference(mobiles)
        perdus = [lg.lieux[i]["nom"] for i in range(len(pris)) if ref[i] and not pris[i]]
        if perdus:
            pb.append(f"{len(perdus)} lieux deviennent inaccessibles (ex. {perdus[:3]})")
    return pb


def calculer(graine: str, options: dict | None = None, lg: Logique | None = None,
             croiser: bool = True) -> Resultat:
    """Table du randomizer complet pour une graine et des options. Déterministe ; lève ErreurRando sinon.

    `croiser` (défaut) fait revérifier la table par `rando_verif` (code séparé) avec les données LIVRÉES.
    """
    lg = lg or logique()
    graine = nettoyer_graine(graine)
    if not graine:
        raise ErreurRando("graine vide : le randomizer est désactivé")
    opt = normaliser_options(options, lg)
    if opt["depart"] == L7.DEPART_HASARD:
        # lot 7 : île tirée depuis la graine (pas depuis le flux du remplissage) ; la table porte l'île choisie
        opt["depart"] = L7.ile_au_hasard(graine)
    # lot 8 : entrées et cartes tirées depuis la graine (pas depuis le flux du remplissage), validées par la logique ;
    # la table porte le tirage. Options coupées (ou tirages déjà donnés) : un seul candidat, les options elles-mêmes.
    base_lg = lg
    refus_depart, passe = None, False
    for opt in _candidats_lot8(base_lg, opt, graine):
        lg = base_lg.variante(opt)
        plan = Plan(lg, opt)
        lg.reference(plan.mobiles)
        if len(plan.mobiles) < 2:
            raise ErreurRando("moins de deux coffres mélangés avec ces options")
        if plan.depart:
            # objets de départ mélangés : options sans place pour eux (ou, lot 8, tirage d'entrées qui cache tous les
            # lieux possibles derrière l'objet lui-même) : candidat suivant, et la raison si aucun ne convient
            try:
                controler_depart(lg, plan)
            except ErreurRando as ex:
                refus_depart = refus_depart or ex
                continue
        passe = True
        # germe : la forme publiée des options et la version qui leur correspond. Options du lot 1 coupées : le germe
        # d'avant le lot, donc la même table pour une même graine
        base = f"{ALGO}|{lg.donnees_de(opt)}|{_json(options_publiees(opt))}|{graine}"
        for essai in range(ESSAIS_MAX):
            flux = Flux(hashlib.sha256(f"{base}|{essai}".encode("utf-8")).digest())
            place = _remplir(lg, plan, flux)
            if place is None:
                continue
            res = Resultat(lg, graine, opt, place, essai, plan)
            if verifier_logique(lg, res.contenu, opt["tous_lieux"], plan.mobiles):
                continue
            verifier_table(lg, opt, res.table)
            res.spheres = _spheres(lg, res.contenu)
            res.indices = _indices(lg, res, Flux(hashlib.sha256(f"{base}|indices".encode("utf-8")).digest()))
            if croiser:
                from rando import verif as rando_verif
                pb = rando_verif.verifier_message(res.message())
                if pb:
                    raise ErreurRando("le vérificateur indépendant refuse la table : " + " ; ".join(pb[:3]))
            return res
    if refus_depart is not None and not passe:
        raise refus_depart
    raise ErreurRando(f"aucun remplissage finissable en {ESSAIS_MAX} essais (options trop contraintes ?)")


def controler_depart(lg: Logique, plan: Plan) -> None:
    """Objets de départ mélangés : deux conditions NÉCESSAIRES vérifiées avant tout tirage, pour refuser avec la raison
    plutôt qu'après 64 essais vains. Lève ErreurRando.

    1. Place : les pièces qui ne vont que dans un lieu du lot 6 (objets hors de la liste des stubs : ceux de la famille
       elle-même, réceptacles des boss, chants, lettres…, plus les objets de départ) doivent tenir dans les lieux du lot
       6 mélangés qui savent donner un objet quelconque (ni Grande Fée, ni étal de Beedle).
    2. Accès : chaque objet de départ mélangé doit pouvoir tomber dans un lieu du lot 6 atteignable SANS lui, tous les
       autres objets en main. Les bombes : hors des donjons, chaque lieu du lot 6 attend « Helmaroc_King_Defeated »
       (verrou prudent de ces lieux), et la Forteresse Maudite exige les bombes (rando_logique/monde.json : « Forsaken
       Fortress Sector » -> « Forsaken Fortress Inner Courtyard » : Bombs) : il leur faut un lieu du lot 6 DANS un donjon
       (pots des objets posés, réceptacles des boss). Le flacon (liste du pack) va aussi dans un coffre : pas contrôlé.
       La Baguette et le Requiem : avec la logique de la partie (variante « la voile suffit », rando_lot7.VARIANTE_VOILE)."""
    libres = [k for k in plan.fich if k not in plan.fees
              and L6.SITES[lg.lieux[k]["fich"][1]]["voie"] != "boutique"]
    seuls = [p for p in plan.fich_seul if p not in plan.fee_seul]
    if len(seuls) > len(libres):
        raise ErreurRando(f"objets de départ mélangés : {len(seuls)} objets ne peuvent aller que dans un lieu du "
                          f"randomizer par fichiers et ces options n'en mélangent que {len(libres)} qui sachent les "
                          "donner : coche une famille de plus (trésors engloutis, objets posés, personnages à "
                          "immédiat…)")
    stock = {}
    for i in plan.mobiles:
        o = lg.origine[i]
        if o:
            stock[o] = stock.get(o, 0) + 1
    contenu = list(lg.origine)
    for i in plan.mobiles:
        contenu[i] = None
    for p in sorted(plan.depart):
        objet, ident = lg.origine[p], lg.lieux[p]["id"]
        if p not in plan.fich_seul:
            continue                     # objet de la liste du pack (flacon) : il va aussi dans un coffre
        main = dict(stock, **{objet: stock.get(objet, 0) - sum(1 for q in plan.mobiles if lg.origine[q] == objet)})
        _c, pris, _s = lg.explorer(contenu, main)
        if not any(pris[k] and L6.accepte(lg.lieux[k]["fich"][1], ident) for k in libres):
            raise ErreurRando(f"objets de départ mélangés : {lg.nom_fr(objet)} n'a aucun lieu du randomizer par "
                              "fichiers atteignable sans lui"
                              + (" (hors des donjons, ces lieux attendent le Roi Cuirasse vaincu, et la Forteresse "
                                 "exige les bombes : coche les objets posés, pots des donjons, ou les réceptacles des "
                                 "boss avec une autre famille)" if ident == 0x31 else " : coche une autre famille"))


def entrees_valides(base: Logique, opt: dict) -> str:
    """Lot 8 : "" si le tirage d'entrées de ``opt`` est jouable, sinon la raison. Comme validateWorld de TWWHD : avec
    tous les objets de la partie, tout lieu atteignable sans le mélange le reste ; et, les lieux que la table ne
    place pas gardant leur objet, la fin est atteinte avec en main les objets placés (sinon aucun remplissage ne
    finit : un objet fixe peut se retrouver derrière une porte qu'il ouvre lui-même)."""
    sans = dict(opt, tirage_entrees=None)
    v0 = base.variante(sans)
    v = base.variante(opt)
    tout = [None] * len(v.lieux)
    pris0 = v0.explorer(tout, v0.stock())[1]
    pris = v.explorer(tout, v.stock())[1]
    perdus = [v.lieux[i]["nom"] for i in range(len(pris)) if pris0[i] and not pris[i]]
    if perdus:
        return f"{len(perdus)} lieux hors d'atteinte avec tous les objets (ex. {perdus[:2]})"
    try:
        v.reference(Plan(v, opt).mobiles)
    except ErreurRando as ex:
        return str(ex)
    return ""


def _candidats_lot8(lg: Logique, opt: dict, graine: str):
    """Options candidates de la partie : ``opt`` telle quelle si le lot 8 n'a rien à tirer ; sinon, cartes tirées
    une fois, puis un tirage d'entrées valide par essai (au plus rando_lot8.ESSAIS_ENTREES)."""
    if not L8.a_tirer(opt):
        if L8.mode_entrees(opt) != L8.ENTREES_DEFAUT:
            r = entrees_valides(lg, opt)
            if r:
                raise ErreurRando(f"tirage des entrées donné injouable : {r}")
        yield opt
        return
    o = dict(opt)
    if L8.cartes(o) and o.get("tirage_cartes") is None:
        o["tirage_cartes"] = L8.tirer_cartes(graine)
    if L8.mode_entrees(o) == L8.ENTREES_DEFAUT or o.get("tirage_entrees") is not None:
        yield o
        return
    trouve = False
    zones = lg._base.docs["monde"]["zones"]
    for essai in range(L8.ESSAIS_ENTREES):
        t = L8.tirer_entrees(o["entrees"], graine, essai)
        # tri rapide sur le graphe seul (boucles fermées du Plateau, de l'Île de Glace…), puis la logique entière
        if not L8.structure_ok(zones, dict(zip(L8.POOLS[o["entrees"]], t))):
            continue
        c = dict(o, tirage_entrees=t)
        if not entrees_valides(lg, c):
            trouve = True
            yield c
    if not trouve:
        raise ErreurRando(f"aucun tirage d'entrées jouable en {L8.ESSAIS_ENTREES} essais")


def verifier_table(lg: Logique, opt: dict, table) -> dict:
    """Contrôle de forme d'une table (reçue ou calculée). Rend {coffre: coffre d'origine de la pièce}.

    Refuse : entrée mal formée, lieu (coffre ou cadeau) non mobile avec ces options, objet d'origine différent du relevé,
    objets perdus ou dupliqués (ce n'est pas une permutation), objet de donjon sorti de son donjon,
    identifiant interdit.

    Lot 5 : une entrée [TYPE_HORS, index, coffre, objet réel, donjon] dit qu'un coffre ordinaire porte un objet de
    donjon étranger ; son entrée de coffre doit alors porter l'objet d'attente, et la permutation se compte avec
    l'objet réel. Refusée si l'option de cet objet ne le laisse pas sortir, si le coffre n'est pas un coffre
    ordinaire mélangé (ou pas dans un donjon avec « tout_donjon »), ou si l'objet y serait en fait chez lui.
    """
    plan = Plan(lg, opt)
    par_cle = {plan.cle[i]: i for i in plan.mobiles}
    if len(par_cle) != len(plan.mobiles):
        raise ErreurRando("table : deux lieux mélangés partagent la même clé")
    vus, origines, nouveaux = {}, [], []
    hors = {}                                    # (index, coffre) -> (objet réel, donjon de destination)
    # lot 6 : objets des lieux du lot hors de la liste des stubs du pack (jamais ailleurs que dans un lieu du lot)
    seuls = {lg.lieux[i]["id"] for i in plan.fich_seul | plan.fee_seul}
    # étape 5 : objets qui ne vont que dans un lieu « donnée ». Un même numéro peut être réservé aux deux genres de
    # lieux (rubis vert : pot du lot 6 et lettre de l'étape 5) : chacun des deux genres sait le donner, il est alors
    # permis dans l'un ou l'autre (les pièces sont interchangeables)
    seuls9 = {lg.lieux[i]["id"] for i in plan.don_seul}
    if not isinstance(table, (list, tuple)):
        raise ErreurRando("table : liste attendue")
    for t in table:
        if not (isinstance(t, (list, tuple)) and len(t) == 5
                and all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 0xFF for x in t)):
            raise ErreurRando(f"table : entrée mal formée {t!r}")
        typ, idx, tbox, avant, apres = t
        if typ == TYPE_HORS:
            if not plan.portee:
                raise ErreurRando("table : objet de donjon hors de son donjon sans l'option qui le permet")
            if (idx, tbox) in hors:
                raise ErreurRando(f"table : coffre étranger ({idx}, {tbox}) en double")
            hors[(idx, tbox)] = (avant, apres)
            continue
        if typ not in TYPES:
            raise ErreurRando(f"table : type de lieu {typ} non géré")
        i = par_cle.get((typ, idx, tbox))
        if i is None:
            raise ErreurRando(f"table : lieu ({typ}, {idx}, {tbox}) non mélangé avec ces options")
        if i in vus:
            raise ErreurRando(f"table : lieu ({typ}, {idx}, {tbox}) en double")
        if avant != lg.lieux[i]["id"]:
            raise ErreurRando(f"table : objet d'origine faux pour le lieu ({typ}, {idx}, {tbox})")
        if apres in lg.ids_interdits or apres in (0, 0xFF):
            raise ErreurRando(f"table : identifiant interdit 0x{apres:02X}")
        if typ == TYPE_DONNEES and (apres in (lg.id_petite, lg.id_grande, lg.id_carte, lg.id_boussole)
                                    or (apres != avant and apres in plan.interdits.get(i, ()))):
            raise ErreurRando(f"table : objet 0x{apres:02X} interdit dans {lg.lieux[i]['nom']!r}")
        if apres in OBJETS_CONFLIT and typ not in (TYPE_COFFRE, TYPE_CONFLIT, TYPE_FICHIER):
            raise ErreurRando(f"table : objet 0x{apres:02X} d'un coffre en conflit hors d'un coffre "
                              f"({lg.lieux[i]['nom']!r})")
        if typ == TYPE_FICHIER and not L6.accepte(idx, apres):
            raise ErreurRando(f"table : objet 0x{apres:02X} interdit dans {lg.lieux[i]['nom']!r}")
        if typ == TYPE_DEPART and apres not in L7.SANS_VALEUR_DEPART:
            raise ErreurRando(f"table : la case de départ {lg.lieux[i]['nom']!r} ne reçoit qu'un rubis (0x{apres:02X})")
        if typ != TYPE_FICHIER and apres in seuls and apres != avant \
                and not (typ == TYPE_DONNEES and apres in seuls9):
            raise ErreurRando(f"table : objet 0x{apres:02X} d'un lieu du randomizer par fichiers hors d'un tel lieu "
                              f"({lg.lieux[i]['nom']!r})")
        if typ != TYPE_DONNEES and apres in seuls9 and apres != avant and not (typ == TYPE_FICHIER and apres in seuls):
            raise ErreurRando(f"table : objet 0x{apres:02X} d'une lettre ou de l'enchère à 100 rubis hors d'un lieu "
                              f"« donnée » ({lg.lieux[i]['nom']!r})")
        if apres in OBJETS_PROG and i in plan.sans_prog:
            raise ErreurRando(f"table : palier d'objet progressif 0x{apres:02X} hors d'un coffre ordinaire "
                              f"({lg.lieux[i]['nom']!r})")
        vus[i] = apres
        origines.append(avant)
        nouveaux.append(apres)
    if len(vus) != len(plan.mobiles):
        raise ErreurRando("table : il manque des coffres mélangés")
    reel = {}                                    # lieu -> (objet réel, donjon de destination) d'un coffre étranger
    for (idx, tbox), (objet, slot) in sorted(hors.items()):
        i = par_cle.get((TYPE_COFFRE, idx, tbox))
        if i is None or i not in plan.hors_ok:
            raise ErreurRando(f"table : coffre étranger ({idx}, {tbox}) qui n'est pas un coffre ordinaire mélangé")
        if vus[i] != OBJET_HORS:
            raise ErreurRando(f"table : coffre étranger ({idx}, {tbox}) sans son objet d'attente")
        if objet not in (lg.id_petite, lg.id_grande, lg.id_carte, lg.id_boussole):
            raise ErreurRando(f"table : coffre étranger ({idx}, {tbox}) : 0x{objet:02X} n'est pas un objet de donjon")
        reel[i] = (objet, slot)
        nouveaux.remove(OBJET_HORS)
        nouveaux.append(objet)
    if sorted(origines) != sorted(nouveaux):
        raise ErreurRando("table : objets perdus ou dupliqués (pas une permutation)")
    # retrouver une pièce d'origine pour chaque coffre (les pièces de même objet logique sont équivalentes)
    stock = {}
    for i in sorted(plan.mobiles):
        stock.setdefault((lg.lieux[i]["id"], plan.restriction.get(i)), []).append(i)
    place = {}
    # (objet de donjon, index de sauvegarde de son donjon) -> clé de stock, pour les pièces qui peuvent sortir
    sortants = {(lg.lieux[p]["id"], plan.restriction[p][1]): (lg.lieux[p]["id"], plan.restriction[p])
                for p in plan.portee}
    for i in sorted(vus):
        if i in reel:
            cle = sortants.get(reel[i])
            if cle is None or not stock.get(cle):
                raise ErreurRando(f"table : {lg.lieux[i]['nom']!r} : objet de donjon étranger inconnu ou en trop")
            p = stock[cle][0]
            if not plan.etranger(lg, p, i):
                raise ErreurRando(f"table : {lg.lieux[i]['nom']!r} : objet de donjon noté étranger dans son donjon")
            if not plan.hors_permis(lg, p, i):
                raise ErreurRando(f"table : {lg.lieux[i]['nom']!r} ne peut pas porter cet objet de donjon étranger")
            place[i] = stock[cle].pop(0)
            continue
        apres = vus[i]
        cle = (apres, (lg.lieux[i]["donjon"], lg.lieux[i]["idx"])
               if apres in (lg.id_petite, lg.id_grande, lg.id_carte, lg.id_boussole) else None)
        if not stock.get(cle):
            raise ErreurRando(f"table : {lg.lieux[i]['nom']!r} reçoit un objet de donjon qui n'est pas du sien")
        place[i] = stock[cle].pop(0)
    return place


def depuis_message(msg: dict, lg: Logique | None = None) -> Resultat:
    """Reconstruit et REVÉRIFIE une table reçue (forme, permutation, finissabilité). Lève ErreurRando."""
    lg = lg or logique()
    if not isinstance(msg, dict) or msg.get("v") != FORMAT or msg.get("algo") != ALGO:
        raise ErreurRando("message de randomizer complet : version ou algorithme inconnu")
    opt = normaliser_options(msg.get("options"), lg)
    if msg.get("options") not in (options_publiees(opt), opt):  # forme publiée, ou forme complète
        raise ErreurRando("message de randomizer complet : options non normalisées")
    if opt["depart"] == L7.DEPART_HASARD:
        raise ErreurRando("message de randomizer complet : île de départ non tirée (« hasard » dans une table)")
    if L8.a_tirer(opt):
        raise ErreurRando("message de randomizer complet : entrées ou cartes non tirées (option sans son tirage)")
    if not lg.memes_donnees(msg.get("donnees"), opt):
        raise ErreurRando("message de randomizer complet : données de logique différentes")
    lg = lg.variante(opt)
    graine = nettoyer_graine(msg.get("graine"))
    place = verifier_table(lg, opt, msg.get("t"))
    plan = Plan(lg, opt)
    res = Resultat(lg, graine, opt, place, -1, plan)
    if res.emp != msg.get("emp"):
        raise ErreurRando("message de randomizer complet : empreinte fausse")
    pb = verifier_logique(lg, res.contenu, opt["tous_lieux"], plan.mobiles)
    if pb:
        raise ErreurRando("table refusée : " + " ; ".join(pb))
    res.spheres = _spheres(lg, res.contenu)
    return res


def lieux_fixes(options: dict | None = None, lg: Logique | None = None) -> list:
    """[(nom français du lieu, raison)] : tout ce qui garde son objet avec ces options, et pourquoi."""
    lg = lg or logique()
    opt = normaliser_options(options, lg)
    lg = lg.variante(opt)
    plan = Plan(lg, opt)
    return [(lg.lieux[i]["nom_fr"], r) for i, r in sorted(plan.raisons.items())]


def main(argv=None) -> int:
    """Outil hors ligne : `python -m wwmp.rando_moteur --graine X [--options JSON] [--spoiler FICHIER]`."""
    import argparse
    ap = argparse.ArgumentParser(description="Randomizer complet WWMP : calcul hors ligne (aucun effet en jeu)")
    ap.add_argument("--graine", required=True)
    ap.add_argument("--options", default="{}", help="options en JSON (voir OPTIONS_DEFAUT)")
    ap.add_argument("--prereglage", choices=[k for k, p in PREREGLAGES.items() if p["mode"] == "complet"],
                    help="préréglage du lanceur (remplace --options)")
    ap.add_argument("--chaine", help="chaîne de réglages partageable (remplace --options)")
    ap.add_argument("--spoiler", help="fichier où écrire le spoiler (sinon sortie standard)")
    ap.add_argument("--fixes", action="store_true", help="lister les lieux qui gardent leur objet, avec la raison")
    a = ap.parse_args(argv)
    try:
        options = json.loads(a.options)
        if a.prereglage:
            options = prereglage(a.prereglage)[1]
        if a.chaine:
            mode, options = reglages_depuis_chaine(a.chaine)
            if mode != "complet":
                raise ErreurRando("cette chaîne est celle du mode « sur » (palier 1) : pas de calcul de logique")
        if a.fixes:
            for nom, raison in lieux_fixes(options):
                print(f"{nom} : {raison}")
            return 0
        res = calculer(a.graine, options)
    except (ErreurRando, ValueError) as ex:
        print(f"Erreur : {ex}")
        return 2
    texte = res.spoiler_texte()
    if a.spoiler:
        with open(a.spoiler, "w", encoding="utf-8", newline="\n") as f:
            f.write(texte)
        print(f"Spoiler écrit : {a.spoiler} (empreinte {res.emp}, essai {res.essai})")
    else:
        print(texte)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
