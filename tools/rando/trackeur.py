"""Trackeur du randomizer : moteur (étape 1, sans interface).

Étude : docs/etudes/trackeur_randomizer.md (la section 13 « Vérification » prime). Ce module ne touche NI au jeu
NI au réseau (il n'importe ni ``wwlib`` ni ``socket``) : il calcule, à partir de ce que le serveur possède déjà,
l'état commun du trackeur, et le met en forme pour la page du lanceur.

Ce que le trackeur dit :
* les OBJETS de l'équipe, avec leur niveau (épée 1 à 4, arc 1 à 3, bourse, carquois, chants, perles, Triforce,
  cartes, clés de donjon), tirés de l'inventaire commun (``inv``), de la réserve de petites clés, du registre des
  coffres ouverts et, s'il existe, du complément lu par les clients (vie maximale) ;
* les LIEUX par île (grille 7 × 7 de la mer) et par donjon : total, faits, ACCESSIBLES. « Accessible » vient du
  moteur de logique de la branche (``rando_moteur.Logique.explorer``, réutilisé tel quel) lancé avec un contenu VIDE
  et les objets réels de l'équipe : il ne dépend donc pas de la table, et ne la laisse pas deviner ;
* les corrections MANUELLES (cocher un lieu, fixer un niveau), gardées à part : elles ne touchent jamais le
  registre des coffres, l'inventaire ni les clés, et ne partent jamais vers un client de jeu.

Règle anti-spoiler : ``etat()`` n'a AUCUN champ de contenu par lieu ; ``vue()`` ne montre l'objet d'un lieu que
s'il est fait ET que le lobby le publie déjà (coffre ouvert par l'équipe, champ ``pris``).

Dispositions prouvées (désassemblage HD + décompilation GameCube, d_save / d_item) :
* chants gi+0xDD : objet 0x6D + n -> bit n ; fragments de Triforce gi+0xDE : objet 0x61 + n -> bit n ;
* perles gi+0xDF : bit 0 Nayru (0x69), bit 1 Din (0x6A), bit 2 Farore (0x6B) ;
* cartes : objet ``id`` -> bit b = 0xFE - id du tableau de u32 gros-boutistes à gi+0xF4 (0x02550310 appelle
  0x025B7EF4 avec gi+0xE4 et b ; ce dernier écrit le mot ``+0x10 + 4 * (b >> 5)``) ; par octet :
  offset = 0xF4 + 4 * (b >> 5) + 3 - ((b & 31) >> 3), masque = 1 << (b & 7).

Hypothèses restantes (tests en jeu TK du plan) : justesse de « accessible » (couche « scénario vanilla » du moteur,
jamais éprouvée en jeu), numéros 0x77 / 0x78 de la voile et rang des objets « progressifs » donnés par un coffre
quand l'inventaire commun est coupé.
"""
from __future__ import annotations

import threading

from rando import noyau as rnd

VERSION = 1                 # champ « v » de trackeur_etat et de la section du monde persistant
MARQUES_MAX = 512           # corrections manuelles gardées au plus (lieux + objets)
PSEUDO_MAX = 20
VALEUR_MAX = 99
VIE_DEPART, VIE_MAX = 12, 80          # quarts de cœur (3 cœurs au départ, plafond du HUD à 20 cœurs)
LIEUX_LUS_MAX = 2048        # entrées lues au plus dans une liste venue du réseau
FILTRES = ("melanges", "coffres", "tous")

# --- zones ----------------------------------------------------------------------------------------------------
# Secteurs 1 à 49 de la mer, ligne par ligne (case = divmod(secteur - 1, 7)). Égal à l'ordre des sorties de la zone
# « The Great Sea » de rando_logique/monde.json, sans les deux intérieurs (recoupé par test_trackeur).
SECTEURS = (
    "Forsaken Fortress Sector", "Star Island", "Northern Fairy Island", "Gale Isle", "Crescent Moon Island",
    "Seven Star Isles", "Overlook Island",
    "Four Eye Reef", "Mother & Child Isles", "Spectacle Island", "Windfall Island", "Pawprint Isle",
    "Dragon Roost Island", "Flight Control Platform",
    "Western Fairy Island", "Rock Spire Isle", "Tingle Island", "Northern Triangle Island", "Eastern Fairy Island",
    "Fire Mountain", "Star Belt Archipelago",
    "Three Eye Reef", "Greatfish Isle", "Cyclops Reef", "Six Eye Reef", "Tower of the Gods Sector",
    "Eastern Triangle Island", "Thorned Fairy Island",
    "Needle Rock Isle", "Islet of Steel", "Stone Watcher Island", "Southern Triangle Island", "Private Oasis",
    "Bomb Island", "Birds Peak Rock",
    "Diamond Steppe Island", "Five Eye Reef", "Shark Island", "Southern Fairy Island", "Ice Ring Isle",
    "Forest Haven", "Cliff Plateau Isles",
    "Horseshoe Island", "Outset Island", "Headstone Island", "Two Eye Reef", "Angular Isles", "Boating Course",
    "Five Star Isles",
)
DONJONS = ("Dragon Roost Cavern", "Forbidden Woods", "Tower of the Gods", "Forsaken Fortress", "Earth Temple",
           "Wind Temple")
HORS_CARTE = DONJONS + ("Great Sea", "Mailbox", "Hyrule Castle", "Ganon's Tower")
ZONE_HISTOIRE = "Story"
ZONES = SECTEURS + HORS_CARTE
_ZONES = frozenset(ZONES)

# Noms courts des 59 zones : (français, anglais), écrits à la main (les noms longs des données sont raccourcis).
NOMS_COURTS = {
    "Forsaken Fortress Sector": ("Sect. Forteresse", "Fortress Sector"), "Star Island": ("Île Étoilée", "Star Isl."),
    "Northern Fairy Island": ("Fées Nord", "N. Fairy"), "Gale Isle": ("Île du Vent", "Gale Isle"),
    "Crescent Moon Island": ("Croissant", "Crescent Moon"), "Seven Star Isles": ("Ursa Major", "Seven Star"),
    "Overlook Island": ("Trois Tours", "Overlook"), "Four Eye Reef": ("Face Quatre", "Four Eye"),
    "Mother & Child Isles": ("Île Géminée", "Mother & Child"), "Spectacle Island": ("Binocles", "Spectacle"),
    "Windfall Island": ("Mercantîle", "Windfall"), "Pawprint Isle": ("Pas de Chat", "Pawprint"),
    "Dragon Roost Island": ("Île du Dragon", "Dragon Roost"), "Flight Control Platform": ("Tour de l'Envol", "Flight Ctrl"),
    "Western Fairy Island": ("Fées Ouest", "W. Fairy"), "Rock Spire Isle": ("Rocaille", "Rock Spire"),
    "Tingle Island": ("Île de Tingle", "Tingle Isl."), "Northern Triangle Island": ("Triangle Nord", "N. Triangle"),
    "Eastern Fairy Island": ("Fées Est", "E. Fairy"), "Fire Mountain": ("Mont. de Feu", "Fire Mountain"),
    "Star Belt Archipelago": ("Orion", "Star Belt"), "Three Eye Reef": ("Face Trois", "Three Eye"),
    "Greatfish Isle": ("Île du Poisson", "Greatfish"), "Cyclops Reef": ("Face Une", "Cyclops"),
    "Six Eye Reef": ("Face Six", "Six Eye"), "Tower of the Gods Sector": ("Sect. Tour des Dieux", "Tower Sector"),
    "Eastern Triangle Island": ("Triangle Est", "E. Triangle"), "Thorned Fairy Island": ("Fées Épineux", "Thorned Fairy"),
    "Needle Rock Isle": ("Serrure de Pierre", "Needle Rock"), "Islet of Steel": ("Canonnière", "Islet of Steel"),
    "Stone Watcher Island": ("Tête de Pierre", "Stone Watcher"),
    "Southern Triangle Island": ("Triangle Sud", "S. Triangle"), "Private Oasis": ("Île de Qui-ça", "Private Oasis"),
    "Bomb Island": ("Île de la Bombe", "Bomb Isl."), "Birds Peak Rock": ("Nichoirs", "Bird's Peak"),
    "Diamond Steppe Island": ("Losange", "Diamond Steppe"), "Five Eye Reef": ("Face Cinq", "Five Eye"),
    "Shark Island": ("Île du Requin", "Shark Isl."), "Southern Fairy Island": ("Fées Sud", "S. Fairy"),
    "Ice Ring Isle": ("Île Gelée", "Ice Ring"), "Forest Haven": ("Île aux Forêts", "Forest Haven"),
    "Cliff Plateau Isles": ("Saut de Roc", "Cliff Plateau"), "Horseshoe Island": ("Fer à Cheval", "Horseshoe"),
    "Outset Island": ("Île de l'Aurore", "Outset"), "Headstone Island": ("Île de la Terre", "Headstone"),
    "Two Eye Reef": ("Face Deux", "Two Eye"), "Angular Isles": ("Kif Kif", "Angular"),
    "Boating Course": ("Batodrome", "Boating Course"), "Five Star Isles": ("Cassiopée", "Five Star"),
    "Dragon Roost Cavern": ("Caverne du Dragon", "Dragon Roost Cavern"),
    "Forbidden Woods": ("Bois Défendus", "Forbidden Woods"), "Tower of the Gods": ("Tour des Dieux", "Tower of the Gods"),
    "Forsaken Fortress": ("Forteresse Maudite", "Forsaken Fortress"),
    "Earth Temple": ("Temple de la Terre", "Earth Temple"), "Wind Temple": ("Temple du Vent", "Wind Temple"),
    "Great Sea": ("Océan", "Great Sea"), "Mailbox": ("Boîte aux lettres", "Mailbox"),
    "Hyrule Castle": ("Château d'Hyrule", "Hyrule Castle"), "Ganon's Tower": ("Tour de Ganon", "Ganon's Tower"),
}

# --- grille des objets ----------------------------------------------------------------------------------------
# (clé = nom d'objet de logique, niveau max, pictogramme, groupe). Le pictogramme est un simple nom que la page
# associe à un dessin maison (aucune image du jeu). « Progressive Bow » est une famille : son niveau est la longueur
# de la chaîne arc -> flèches feu/glace -> flèches de lumière.
FAMILLE_ARC = "Progressive Bow"
_OBJETS = (
    ("Wind Waker", 1, "baguette"), ("Progressive Sail", 2, "voile"), ("Progressive Sword", 4, "epee"),
    ("Progressive Shield", 2, "bouclier"), ("Grappling Hook", 1, "grappin_griffe"), ("Boomerang", 1, "boomerang"),
    ("Deku Leaf", 1, "feuille"), ("Bombs", 1, "bombe"), (FAMILLE_ARC, 3, "arc"), ("Hookshot", 1, "grappin"),
    ("Skull Hammer", 1, "masse"), ("Iron Boots", 1, "bottes"), ("Power Bracelets", 1, "bracelets"),
    ("Progressive Picto Box", 2, "boite_images"), ("Spoils Bag", 1, "sac_butin"), ("Bait Bag", 1, "sac_appats"),
    ("Delivery Bag", 1, "sac_facteur"), ("Progressive Wallet", 2, "bourse"), ("Progressive Quiver", 2, "carquois"),
    # sac de bombes : 60 puis 99 (Grandes Fées ; objet progressif de TWWHD, option « progressifs_plus »)
    ("Progressive Bomb Bag", 2, "sac_bombes"), ("Progressive Magic Meter", 2, "magie"), ("Empty Bottle", 4, "flacon"), ("Heart Container", 17, "coeur"),
    ("Piece of Heart", 44, "quart_coeur"),
)
_CHANTS = ("Wind's Requiem", "Ballad of Gales", "Command Melody", "Earth God's Lyric", "Wind God's Aria",
           "Song of Passing")                                   # bit n de gi+0xDD
_PERLES = ("Nayru's Pearl", "Din's Pearl", "Farore's Pearl")    # bit n de gi+0xDF
_QUETE = (("Note to Mom", 1, "lettre"), ("Maggie's Letter", 1, "lettre"), ("Moblin's Letter", 1, "lettre"),
          ("Cabana Deed", 1, "titre"), ("Blue Chu Jelly", 23, "gelee"), ("Dragon Tingle Statue", 1, "statue"),
          ("Forbidden Tingle Statue", 1, "statue"), ("Goddess Tingle Statue", 1, "statue"),
          ("Earth Tingle Statue", 1, "statue"), ("Wind Tingle Statue", 1, "statue"))
_PETITES_MAX = {"Dragon Roost Cavern": 4, "Forbidden Woods": 1, "Tower of the Gods": 2, "Earth Temple": 3,
                "Wind Temple": 2}
GRILLE_OBJETS = tuple(
    [(n, m, p, "objets") for n, m, p in _OBJETS]
    + [(n, 1, "chant", "chants") for n in _CHANTS]
    + [(n, 1, "perle", "perles") for n in _PERLES]
    + [(f"Triforce Shard {k}", 1, "triforce", "triforce") for k in range(1, 9)]
    + [(f"Triforce Chart {k}", 1, "carte_triforce", "cartes") for k in range(1, 4)]
    + [("Ghost Ship Chart", 1, "carte_fantome", "cartes")]
    + [(f"Treasure Chart {k}", 1, "carte", "cartes") for k in range(1, 47)]
    + [(n, m, p, "quete") for n, m, p in _QUETE]
    + [(f"{d} Small Key", m, "petite_cle", "donjons") for d, m in _PETITES_MAX.items()]
    + [(f"{d} Big Key", 1, "grande_cle", "donjons") for d in _PETITES_MAX])
NIVEAU_MAX = {n: m for n, m, _p, _g in GRILLE_OBJETS}

# --- inventaire commun -> compteurs ----------------------------------------------------------------------------
# Rangs : numéros d'objet du jeu, du plus bas au plus haut ; le rang est celui du plus haut numéro acquis.
RANGS = {"Progressive Sword": (0x38, 0x39, 0x3A, 0x3E), "Progressive Shield": (0x3B, 0x3C),
         "Progressive Sail": (0x78, 0x77), "Progressive Picto Box": (0x23, 0x26)}
ARC = (("Progressive Bow", 0x27), ("Fire Ice Arrows", 0x35), ("Light Arrow", 0x36))
SIMPLES = ("Wind Waker", "Grappling Hook", "Boomerang", "Deku Leaf", "Bombs", "Hookshot", "Skull Hammer",
           "Iron Boots", "Power Bracelets", "Spoils Bag", "Bait Bag", "Delivery Bag")
SAC = ("Note to Mom", "Maggie's Letter", "Moblin's Letter", "Cabana Deed")
OFF_CHANTS, OFF_TRIFORCE, OFF_PERLES, OFF_CARTES = 0xDD, 0xDE, 0xDF, 0xF4
CARTE_ID_MIN, CARTE_ID_MAX = 0xC2, 0xFE
CAP_CARQUOIS = {30: 0, 60: 1, 99: 2}
CAP_MAGIE = {16: 1, 32: 2}
CAP_BOMBES = {30: 0, 60: 1, 99: 2}
ITEM_PETITE, ITEM_GRANDE, ITEM_CARTE, ITEM_BOUSSOLE = 0x15, 0x4E, 0x4C, 0x4D
# objets que l'inventaire commun suffit à connaître (un coffre ouvert ne les compte donc pas une seconde fois)
PAR_INVENTAIRE = frozenset(
    set(RANGS) | {n for n, _i in ARC} | set(SIMPLES) | set(SAC) | set(_CHANTS) | set(_PERLES)
    | {"Progressive Wallet", "Progressive Quiver", "Progressive Bomb Bag", "Progressive Magic Meter", "Empty Bottle",
       "Ghost Ship Chart"}
    | {f"Triforce Shard {k}" for k in range(1, 9)} | {f"Triforce Chart {k}" for k in range(1, 4)}
    | {f"Treasure Chart {k}" for k in range(1, 47)})

# Coffres « en conflit » (portage TWWHD, lot 2) : leur clé (index de sauvegarde, numéro) est AUSSI celle d'un coffre
# d'un autre niveau (tools/rando/coffres.json, champ « conflit »). La clé seule ne dit donc pas quel coffre a été
# ouvert ; le rapport du client porte aussi le niveau où le bit a changé (``stage`` : un bit de coffre ne change dans
# un niveau qu'en ouvrant un coffre de CE niveau ; les bits posés par le client, ou reçus d'un autre joueur, ne sont
# jamais rapportés). Clé -> (niveau du coffre, nom du lieu) : les dix sites de rando.SITES_CONFLIT, plus le coffre du
# bouclier miroir, en conflit lui aussi mais jamais mélangé (rando.py, avant SITES_CONFLIT).
CONFLIT_FIXE = (("M_DaiMB", 6, 7, "Earth Temple - Stalfos Mini Boss"),)
CONFLITS = {(i, n): (stage, nom) for stage, i, n, _o, nom in rnd.SITES_CONFLIT}
CONFLITS.update({(i, n): (stage, nom) for stage, i, n, nom in CONFLIT_FIXE})

# Lieux « reçus » signalés par les clients de jeu (rando/trackeur_client.py : lettres et cadeaux) : genre -> sites
# acceptés (rando.SITES_DONNEES pour « don », rando.SITES_N2 pour « n2 »). Égal à trackeur_client.RECUS (test).
RECUS = {"don": frozenset({0, 1, 2, 3, 4, 5, 12, 13, 14, 19, 20, 21, 22, 23, 24}),
         "n2": frozenset({0, 1, 2, 3, 4, 5, 6, 7, 12})}

_VERROU = threading.Lock()
_CACHE: dict = {}
_TABLES: dict = {}


def _entier(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def pseudo_propre(x) -> str:
    """Pseudo d'auteur : texte imprimable, sans retour à la ligne, 20 caractères au plus."""
    if not isinstance(x, str):
        return ""
    return "".join(ch for ch in x if ch.isprintable()).strip()[:PSEUDO_MAX]


def zone_de(lieu: dict) -> str:
    """Zone d'un lieu de la logique : préfixe anglais de son nom (avant « - »)."""
    return lieu["nom"].split(" - ")[0]


def carte_bit(item: int) -> tuple[int, int]:
    """(offset depuis gi, masque d'octet) du bit « carte obtenue » de l'objet ``item`` (0xC2 à 0xFE)."""
    b = CARTE_ID_MAX - item
    return OFF_CARTES + 4 * (b >> 5) + 3 - ((b & 31) >> 3), 1 << (b & 7)


def tables(lg) -> dict:
    """Tables dérivées des données de logique (par version) : lieux par zone, donjon d'un index de sauvegarde,
    objet de logique d'un numéro d'objet, lieu d'un coffre."""
    with _VERROU:
        t = _TABLES.get(lg.version)
    if t is not None:
        return t
    zones = {z: [] for z in ZONES}
    coffre, cadeau, donnee, visibles = {}, {}, {}, []
    par_idx: dict = {}          # index de sauvegarde -> donjons de TOUS ses coffres (None : hors donjon)
    for e in lg.lieux:
        if e.get("applicable") and e.get("typ") == 0 and _entier(e.get("idx")):
            par_idx.setdefault(e["idx"], set()).add(e.get("donjon") or None)
    # un index ne désigne un donjon que si tous ses coffres en sont (l'index 0 de la mer porte aussi un coffre de
    # la Forteresse : une clé trouvée en mer ne doit pas lui être comptée)
    idx_donjon = {i: next(iter(ds)) for i, ds in par_idx.items() if len(ds) == 1 and None not in ds}
    for i, e in enumerate(lg.lieux):
        z = zone_de(e)
        if e.get("histoire") or z == ZONE_HISTOIRE or z not in zones:
            continue
        zones[z].append(i)
        visibles.append(i)
        if e.get("applicable") and _entier(e.get("idx")):
            if e.get("typ") == 0 and _entier(e.get("tbox")):
                coffre[(e["idx"], e["tbox"])] = i
            elif e.get("typ") == 1:
                cadeau[e["idx"]] = i
        elif i in getattr(lg, "progressifs", {}) and _entier(e.get("idx")) and _entier(e.get("tbox")):
            # portage, lot 4 : coffre d'objet progressif que seule la règle « jamais déplacé » retient (flèches de
            # lumière) ; l'option « progressifs » le mélange, il se coche donc comme les autres coffres (sa clé
            # n'est celle d'aucun autre lieu : vérifié au chargement des données de logique)
            coffre.setdefault((e["idx"], e["tbox"]), i)
        if e.get("don"):                                # lieu « donnée » (courrier, boutique, enchère, personnage)
            donnee[e["don"][1]] = i
    par_id = {}
    for nom in sorted(lg.progression):
        no = lg.objets[nom].get("id")
        if _entier(no):
            par_id.setdefault(no, nom)
    for a, b in lg.equiv.items():                       # Mirror Shield -> Progressive Shield, etc.
        no = lg.objets[a].get("id")
        if _entier(no) and b in lg.progression:
            par_id.setdefault(no, b)
    for nom, nos in RANGS.items():
        for no in nos:
            par_id.setdefault(no, nom)
    conflit_cle = {}                                    # coffres en conflit : clé -> (niveau, lieu)
    for cle, (stage, nom) in CONFLITS.items():
        i = lg.il.get(nom) if hasattr(lg, "il") else None
        if i is not None and i in visibles and cle not in coffre:
            conflit_cle[cle] = (stage, i)
    uniques = {}
    for o in lg.origine:
        uniques[o] = uniques.get(o, 0) + 1
    t = {"zones": zones, "coffre": coffre, "cadeau": cadeau, "donnee": donnee, "visibles": frozenset(visibles),
         "idx_donjon": idx_donjon, "par_id": par_id, "conflit_cle": conflit_cle,
         "uniques": frozenset(o for o, n in uniques.items() if n == 1)}
    with _VERROU:
        _TABLES[lg.version] = t
    return t


# ---------------------------------------------------------------------------------------------------------
# Objets de l'équipe
# ---------------------------------------------------------------------------------------------------------
def coffres_du_registre(registres) -> dict:
    """``Server.coffres`` (groupe -> {(genre, index, numéro): enregistrement}) -> {(index, numéro): (objet ou None,
    pseudo)}, tous groupes confondus. Lecture seule ; toute entrée mal formée est ignorée."""
    out = {}
    if not isinstance(registres, dict):
        return out
    for reg in registres.values():
        if not isinstance(reg, dict):
            continue
        for cle, rec in reg.items():
            if not (isinstance(cle, tuple) and len(cle) == 3 and cle[0] == "t" and _entier(cle[1])
                    and _entier(cle[2])):
                continue
            item = rec.get("item") if isinstance(rec, dict) else None
            item = item if _entier(item) and 0 <= item <= 0xFF else None
            par = pseudo_propre(rec.get("by")) if isinstance(rec, dict) else ""
            k = (cle[1], cle[2])
            if k not in out or (out[k][0] is None and item is not None):
                out[k] = (item, par)
    return out


def coffres_conflit(lg, registres, entrees_conflit=None) -> dict:
    """Coffres « en conflit » ouverts (lot 2) : {index de lieu: pseudo} d'après ``Server.coffres``. Un coffre n'est
    compté que si le rapport gardé (premier arrivé de sa clé) vient de SON niveau ; un rapport du niveau partenaire,
    sans niveau, ou portant l'objet d'origine alors que la table ``entrees_conflit`` ({site: (origine, nouveau)})
    change ce coffre (coffre chargé avant la table) ne coche rien : le lieu reste à cocher à la main."""
    t = tables(lg)
    out = {}
    if not isinstance(registres, dict):
        return out
    sites = {s[4]: k for k, s in enumerate(rnd.SITES_CONFLIT)}
    ec = entrees_conflit if isinstance(entrees_conflit, dict) else {}
    for reg in registres.values():
        if not isinstance(reg, dict):
            continue
        for cle, (stage, i) in t["conflit_cle"].items():
            rec = reg.get(("t",) + cle)
            if not isinstance(rec, dict) or rec.get("stage") != stage or i in out:
                continue
            e = ec.get(sites.get(lg.lieux[i]["nom"]))
            if isinstance(e, tuple) and len(e) == 2 and e[0] != e[1] and rec.get("item") == e[0]:
                continue
            out[i] = pseudo_propre(rec.get("by"))
    return out


def valider_recus(msg) -> list:
    """Champ « l » d'un message ``trk`` : [(genre, site)] acceptés (RECUS), sans doublon ; [] sinon."""
    brut = msg.get("l") if isinstance(msg, dict) else None
    out = []
    if not isinstance(brut, list):
        return out
    for e in brut[:64]:
        if isinstance(e, list) and len(e) == 2 and isinstance(e[0], str) and e[0] in RECUS and _entier(e[1]) and e[1] in RECUS[e[0]] \
                and (e[0], e[1]) not in out:
            out.append((e[0], e[1]))
    return out


def lieux_recus(lg, recus) -> dict:
    """Lieux « reçus » gardés par le serveur ({"don": {site: pseudo}, "n2": {...}}) -> {index de lieu: pseudo}."""
    t = tables(lg)
    out = {}
    if not isinstance(recus, dict):
        return out
    for genre, cle in (("don", "donnee"), ("n2", "cadeau")):
        d = recus.get(genre)
        for site, par in (d.items() if isinstance(d, dict) else ()):
            i = t[cle].get(site)
            if i is not None and site in RECUS[genre]:
                out.setdefault(i, pseudo_propre(par))
    return out


def cles_obtenues(groupes) -> dict:
    """``KeyPool.groups`` -> {donjon (index de sauvegarde): petites clés OBTENUES} = base + faits « get » (pas le
    solde : la logique compte les clés trouvées). Maximum sur les groupes."""
    out = {}
    if not isinstance(groupes, dict):
        return out
    for g in groupes.values():
        if not isinstance(g, dict):
            continue
        for slot, e in g.items():
            if not _entier(slot) or not isinstance(e, dict):
                continue
            base, gets = e.get("base"), e.get("gets")
            n = (base if _entier(base) and base > 0 else 0) + (len(gets) if isinstance(gets, (dict, list, set)) else 0)
            out[slot] = max(out.get(slot, 0), min(VALEUR_MAX, n))
    return out


def _inv_un(lg, st: dict, c: dict) -> None:
    """Compteurs tirés d'UN état d'inventaire commun (maximum avec ce que ``c`` contient déjà)."""
    def monte(nom, n):
        if n > c.get(nom, 0):
            c[nom] = n

    acq = st.get("acq")
    acq = {x for x in acq[:256] if _entier(x)} if isinstance(acq, list) else set()
    for nom, nos in RANGS.items():
        monte(nom, max((r for r, no in enumerate(nos, 1) if no in acq), default=0))
    rang = max((r for r, (_n, no) in enumerate(ARC, 1) if no in acq), default=0)
    for nom, _no in ARC[:rang]:
        monte(nom, 1)
    for nom in SIMPLES:
        if lg.objets.get(nom, {}).get("id") in acq:
            monte(nom, 1)
    bits = st.get("bits")
    bits = bits if isinstance(bits, dict) else {}

    def octet(off):
        v = bits.get(off, bits.get(str(off)))
        return v if _entier(v) else 0

    for n, nom in enumerate(_CHANTS):
        monte(nom, octet(OFF_CHANTS) >> n & 1)
    for n in range(8):
        monte(f"Triforce Shard {n + 1}", octet(OFF_TRIFORCE) >> n & 1)
    for n, nom in enumerate(_PERLES):
        monte(nom, octet(OFF_PERLES) >> n & 1)
    for nom in _noms_cartes():
        no = lg.objets.get(nom, {}).get("id")
        if _entier(no) and CARTE_ID_MIN <= no <= CARTE_ID_MAX:
            off, masque = carte_bit(no)
            monte(nom, 1 if octet(off) & masque else 0)
    cap = st.get("cap")
    cap = cap if isinstance(cap, dict) else {}
    if cap.get("w") in (1, 2):
        monte("Progressive Wallet", cap["w"])
    monte("Progressive Quiver", CAP_CARQUOIS.get(cap.get("am"), 0))
    monte("Progressive Bomb Bag", CAP_BOMBES.get(cap.get("bm"), 0))
    monte("Progressive Magic Meter", CAP_MAGIE.get(cap.get("mm"), 0))
    fl = st.get("fl")
    if _entier(fl) and 0 < fl <= 4:
        monte("Empty Bottle", fl)
    sac = st.get("sac")
    sac = {x for x in sac[:64] if _entier(x)} if isinstance(sac, list) else set()
    for nom in SAC:
        if lg.objets.get(nom, {}).get("id") in sac:
            monte(nom, 1)


def _noms_cartes() -> list[str]:
    return [n for n, _m, _p, g in GRILLE_OBJETS if g == "cartes"]


def compteurs_auto(lg, inv, cles, coffres, comp=None, hors=None) -> tuple[dict, dict, str]:
    """Ce que le serveur sait de lui-même. ``inv`` : états d'inventaire commun (un par groupe ; vide ou None :
    inventaire commun coupé) ; ``cles`` : ``cles_obtenues`` ; ``coffres`` : ``coffres_du_registre`` ; ``comp`` :
    complément des clients ({"vie": quarts}). Rend (compteurs {nom de logique: nombre}, donjons {donjon: [grande
    clé, carte, boussole]}, suivi « auto » ou « partiel »).

    ``hors`` (portage TWWHD, lot 5) : coffres de la table qui portent un objet de donjon ÉTRANGER, {(index,
    numéro): (objet réel, index de sauvegarde de son donjon)} (``Table2.hors``). Un tel coffre OUVERT compte pour
    le donjon de l'objet, pas pour celui du coffre ; un coffre fermé ne compte pour rien (aucun spoiler)."""
    t = tables(lg)
    c: dict = {}
    etats = [st for st in (inv or []) if isinstance(st, dict)]
    for st in etats[:64]:
        _inv_un(lg, st, c)
    suivi = "auto" if etats else "partiel"
    donjons = {d: [0, 0, 0] for d in lg.donjons}
    petites: dict = {}
    for (idx, _n), (item, _par) in list((coffres or {}).items())[:LIEUX_LUS_MAX * 4]:
        if item is None:
            continue
        d = t["idx_donjon"].get(idx)
        if item in (ITEM_GRANDE, ITEM_CARTE, ITEM_BOUSSOLE):
            if d is not None:
                donjons[d][(ITEM_GRANDE, ITEM_CARTE, ITEM_BOUSSOLE).index(item)] = 1
            continue
        if item == ITEM_PETITE:
            if d is not None:
                petites[d] = petites.get(d, 0) + 1
            continue
        nom = t["par_id"].get(item)
        if nom is None or (etats and nom in PAR_INVENTAIRE):
            continue
        c[nom] = min(VALEUR_MAX, c.get(nom, 0) + 1)
    for cle, paire in list(hors.items())[:LIEUX_LUS_MAX] if isinstance(hors, dict) else []:
        # lot 5 : coffre étranger ouvert (quel que soit l'objet signalé : le coffre montre un objet d'attente)
        if cle not in (coffres or {}) or not (isinstance(paire, tuple) and len(paire) == 2):
            continue
        objet, slot = paire
        d = t["idx_donjon"].get(slot)
        if d is None:
            continue
        if objet in (ITEM_GRANDE, ITEM_CARTE, ITEM_BOUSSOLE):
            donjons[d][(ITEM_GRANDE, ITEM_CARTE, ITEM_BOUSSOLE).index(objet)] = 1
        elif objet == ITEM_PETITE:
            petites[d] = petites.get(d, 0) + 1
    if not etats:
        # inventaire coupé : l'arc trouvé dans un coffre porte un des trois numéros ; on reconstitue la chaîne
        rang = max((r for r, (n, _no) in enumerate(ARC, 1) if c.get(n, 0) > 0), default=0)
        for nom, _no in ARC[:rang]:
            c[nom] = max(1, c.get(nom, 0))
        for nom, m in NIVEAU_MAX.items():
            if nom in c and nom not in ("Piece of Heart", "Heart Container"):
                c[nom] = min(c[nom], m)
    par_slot = cles if isinstance(cles, dict) else {}
    for d in lg.donjons:
        slots = [s for s, dj in t["idx_donjon"].items() if dj == d]
        # clés génériques trouvées dans le donjon + clés « de ce donjon » trouvées ailleurs (objets propres de la
        # table), ou la réserve commune si elle en compte davantage
        n = max([par_slot.get(s, 0) for s in slots if _entier(par_slot.get(s, 0))]
                + [petites.get(d, 0) + c.get(f"{d} Small Key", 0)])
        if n > 0 and f"{d} Small Key" in lg.objets:
            c[f"{d} Small Key"] = min(VALEUR_MAX, n)
        if (donjons[d][0] or c.get(f"{d} Big Key", 0)) and f"{d} Big Key" in lg.objets:
            c[f"{d} Big Key"] = 1
            donjons[d][0] = 1
    vie = comp.get("vie") if isinstance(comp, dict) else None
    if _entier(vie) and VIE_DEPART <= vie <= VIE_MAX:
        # vie maximale lue en jeu (quarts) : même total de cœurs pour la logique que réceptacles + quarts
        c["Heart Container"] = max(0, vie // 4 - lg.coeurs)
        c["Piece of Heart"] = vie % 4
    return {k: v for k, v in c.items() if v > 0 and k in lg.objets}, donjons, suivi


def monter_vus(vus: dict, auto: dict) -> bool:
    """« vus » = plus haut niveau jamais observé par objet (jamais de baisse : une copie de partie ne fait pas
    reculer le trackeur). Rend vrai si quelque chose a monté."""
    change = False
    for nom, n in auto.items():
        if nom == "Piece of Heart":
            continue                    # quarts en cours : retombent à 0 quand un cœur se complète
        if _entier(n) and n > vus.get(nom, 0):
            vus[nom] = min(VALEUR_MAX, n)
            change = True
    return change


def compteurs_equipe(lg, auto: dict, vus: dict | None = None, manuel: dict | None = None) -> dict:
    """Fusion par objet : max(automatique, vus), puis la correction manuelle REMPLACE la valeur tant qu'elle
    existe. ``manuel`` : {nom: [valeur, pseudo]} ; la famille de l'arc se corrige par son niveau (0 à 3)."""
    c = {}
    for src in (auto or {}), (vus or {}):
        for nom, n in src.items():
            if nom in lg.objets and _entier(n) and n > c.get(nom, 0):
                c[nom] = min(VALEUR_MAX, n)
    for nom, e in (manuel or {}).items():
        v = e[0] if isinstance(e, (list, tuple)) and e else e
        if nom not in NIVEAU_MAX or nom not in lg.objets or not _entier(v) or not 0 <= v <= NIVEAU_MAX[nom]:
            continue
        if nom == FAMILLE_ARC:
            for r, (membre, _no) in enumerate(ARC, 1):
                c[membre] = 1 if r <= v else 0
        else:
            c[nom] = v
    return {k: v for k, v in c.items() if v > 0}


def niveau(c: dict, nom: str) -> int:
    """Niveau affiché d'un objet de la grille (la famille de l'arc : longueur de sa chaîne)."""
    if nom == FAMILLE_ARC:
        n = 0
        for membre, _no in ARC:
            if c.get(membre, 0) <= 0:
                break
            n += 1
        return n
    v = c.get(nom, 0)
    return v if _entier(v) and v > 0 else 0


# ---------------------------------------------------------------------------------------------------------
# État commun (côté serveur)
# ---------------------------------------------------------------------------------------------------------
def neuf() -> dict:
    """État gardé par le serveur (``srv.trackeur``) : corrections, « vus », complément des clients, verrou."""
    return {"man": {"lieux": {}, "objets": {}}, "vus": {}, "comp": {}, "verrou": 0, "recus": {"don": {}, "n2": {}}}


def table_exacte(lg, mode: str, donnees, options) -> bool:
    """La table de la partie vient-elle des mêmes données de logique que ces outils (et d'options comprises) ?
    Sinon : ni accessibilité, ni marque « mélangé » (objets et coffres seulement)."""
    if mode != "complet":
        return True
    if not lg.memes_donnees(donnees, options):
        return False
    from rando import moteur as rm
    try:
        rm.normaliser_options(options, lg)
    except rnd.ErreurRando:
        return False
    return True


def deduire(lg, entrees, entrees_n2, auto: dict, coffres: dict, entrees_don=None, entrees_conflit=None,
            entrees_fich=None) -> set:
    """Cadeaux de personnages reçus, déduits par unicité (option du serveur, coupée par défaut) : la table place
    dans ce cadeau un objet UNIQUE du jeu, connu par l'inventaire commun, que l'équipe possède, et qu'aucun coffre
    ouvert n'a donné. Rend l'ensemble des index de lieux. Ne coche qu'APRÈS l'obtention : aucun spoiler.
    ``entrees_don`` : lieux « donnée » de la table (lettres, étals, enchères, personnages à table), déduits de même.
    ``entrees_conflit`` : coffres « en conflit » de la table ; ils ne sont jamais déduits (ce sont des coffres) mais
    leurs objets comptent pour l'unicité (un objet placé aussi dans l'un d'eux n'est pas unique). ``entrees_fich`` :
    lieux du randomizer par fichiers (lot 6) : jamais déduits (étape 5 de l'étude), mais comptés pour l'unicité."""
    t = tables(lg)
    if not isinstance(entrees_n2, dict) or not isinstance(entrees, dict):
        return set()
    if not isinstance(entrees_don, dict):
        entrees_don = {}
    if not isinstance(entrees_conflit, dict):
        entrees_conflit = {}
    if not isinstance(entrees_fich, dict):
        entrees_fich = {}
    places: dict = {}
    for _o, n in list(entrees.values()) + list(entrees_n2.values()) + list(entrees_don.values()) \
            + [p for p in entrees_conflit.values() if isinstance(p, tuple) and len(p) == 2] \
            + [p for p in entrees_fich.values() if isinstance(p, tuple) and len(p) == 2]:
        places[n] = places.get(n, 0) + 1
    donnes = {item for item, _par in (coffres or {}).values() if item is not None}
    out = set()
    for site, paire, genre in ([(s, p, "cadeau") for s, p in entrees_n2.items()]
                               + [(s, p, "donnee") for s, p in entrees_don.items()]):
        if not (isinstance(paire, tuple) and len(paire) == 2):
            continue
        o, n = paire
        i = t[genre].get(site)
        nom = t["par_id"].get(n)
        if i is None or o == n or nom is None or places.get(n) != 1 or n in donnes:
            continue
        if nom in t["uniques"] and nom in PAR_INVENTAIRE and nom not in RANGS and auto.get(nom, 0) > 0:
            out.add(i)
    return out


def etat(lg, *, rev: int, emp: str, mode: str, exact: bool, suivi: str, obj: dict, donjons: dict, coffres: dict,
         man: dict, deduits=(), verrou: int = 0, signaux=(), lieux_auto=None) -> dict:
    """Message ``trackeur_etat`` (étude §4.2). ``obj`` : compteurs fusionnés ; ``donjons`` : {donjon: [grande clé,
    carte, boussole]} du registre ; ``coffres`` : ``coffres_du_registre`` ; ``man`` : corrections ({"lieux": {i:
    [v, pseudo]}, "objets": {nom: [v, pseudo]}}). AUCUN contenu de lieu : ni objet, ni table, ni graine.
    ``signaux`` (étape 5, wwmp/rando_lot9.lieux_signales) : lieux faits d'après un bit d'événement partagé que le jeu
    pose en donnant l'objet (enchères, Kane, étals de l'Île du Pic, Ivan) ; cochés « auto », sans objet montré.
    ``lieux_auto`` : {index de lieu: pseudo} faits d'après un autre signal sûr (coffres en conflit ouverts dans leur
    niveau, lettres et cadeaux signalés par les clients) ; cochés « auto », sans objet montré."""
    t = tables(lg)
    n = len(lg.lieux)
    ml = man.get("lieux") if isinstance(man, dict) and isinstance(man.get("lieux"), dict) else {}
    mo = man.get("objets") if isinstance(man, dict) and isinstance(man.get("objets"), dict) else {}
    auto = {}
    for k, (_item, par) in (coffres or {}).items():
        i = t["coffre"].get(k)
        if i is not None:
            auto[i] = par
    for i, par in (lieux_auto or {}).items():
        if _entier(i) and i in t["visibles"]:
            auto.setdefault(i, pseudo_propre(par))
    faits, faits_set = [], set()
    for i in sorted(t["visibles"]):
        m = ml.get(i)
        mv = m[0] if isinstance(m, (list, tuple)) and m else None
        if mv == 0:
            continue                                # décoché à la main : reste décoché
        if i in auto:
            faits.append([i, "a", auto[i]])
        elif i in signaux:
            faits.append([i, "a", ""])                # étape 5 : bit d'événement partagé posé par le jeu
        elif i in deduits:
            faits.append([i, "d", ""])
        elif mv == 1:
            faits.append([i, "m", pseudo_propre(m[1]) if len(m) > 1 else ""])
        else:
            continue
        faits_set.add(i)
    out = {"type": "trackeur_etat", "v": VERSION, "rev": rev, "emp": str(emp)[:64], "donnees": lg.version,
           "exact": int(bool(exact)), "suivi": "auto" if suivi == "auto" else "partiel",
           "obj": {k: min(VALEUR_MAX, v) for k, v in sorted(obj.items()) if k in lg.objets and _entier(v) and v > 0},
           "cles": {d: [min(VALEUR_MAX, niveau(obj, f"{d} Small Key")), 1 if niveau(obj, f"{d} Big Key") else 0,
                        1 if (donjons.get(d) or [0, 0, 0])[1] else 0, 1 if (donjons.get(d) or [0, 0, 0])[2] else 0]
                    for d in lg.donjons},
           "faits": faits,
           "man": {"lieux": [[i, e[0], pseudo_propre(e[1])] for i, e in sorted(ml.items())
                             if _entier(i) and 0 <= i < n],
                   "objets": [[nom, e[0], pseudo_propre(e[1])] for nom, e in sorted(mo.items())]},
           "verrou": int(bool(verrou))}
    if exact:
        _c, pris, _s = lg.explorer([None] * n, bonus=out["obj"])
        out["acc"] = [i for i in sorted(t["visibles"]) if pris[i] and i not in faits_set]
    return out


# ---------------------------------------------------------------------------------------------------------
# Corrections manuelles
# ---------------------------------------------------------------------------------------------------------
def valider_cmd(lg, msg) -> dict | None:
    """Commande ``trackeur_cmd`` nettoyée, ou None si elle sort des bornes (étude §4.3 et §6)."""
    if not isinstance(msg, dict):
        return None
    op, v = msg.get("op"), msg.get("v")
    t = tables(lg)
    if op == "lieu":
        i = msg.get("i")
        if _entier(i) and i in t["visibles"] and v in (0, 1) and _entier(v):
            return {"op": "lieu", "i": i, "v": v}
    elif op == "objet":
        nom = msg.get("nom")
        if isinstance(nom, str) and nom in NIVEAU_MAX and nom in lg.objets and _entier(v) \
                and 0 <= v <= NIVEAU_MAX[nom]:
            return {"op": "objet", "nom": nom, "v": v}
    elif op == "zone":
        zone = msg.get("zone")
        if isinstance(zone, str) and zone in _ZONES and v in (0, 1) and _entier(v):
            out = {"op": "zone", "zone": zone, "v": v}
            if "lieux" in msg:
                # la page ne marque que les lieux qu'elle MONTRE (son filtre) : sans cette liste, toute la zone
                lieux = msg["lieux"]
                dans = set(t["zones"][zone])
                if not isinstance(lieux, list) or len(lieux) > len(dans)                         or not all(_entier(i) and i in dans for i in lieux):
                    return None
                out["lieux"] = sorted(set(lieux))
            return out
    elif op == "annuler":
        i, nom, zone = msg.get("i"), msg.get("nom"), msg.get("zone")
        if _entier(i) and i in t["visibles"]:
            return {"op": "annuler", "i": i}
        if isinstance(nom, str) and nom in NIVEAU_MAX:
            return {"op": "annuler", "nom": nom}
        if isinstance(zone, str) and zone in _ZONES:
            return {"op": "annuler", "zone": zone}
    elif op == "raz":
        return {"op": "raz"}
    elif op == "verrou":
        if v in (0, 1) and _entier(v):
            return {"op": "verrou", "v": v}
    return None


def appliquer_cmd(lg, trk: dict, cmd: dict, pseudo: str, hote: bool) -> tuple[bool, str]:
    """Applique une commande VALIDÉE (``valider_cmd``) à l'état du serveur. Rend (changé ?, message de refus).
    ``raz`` et ``verrou`` sont réservés à l'hôte ; sous verrou, toute correction l'est aussi. Ne touche QUE
    ``trk`` (corrections et verrou)."""
    man = trk["man"]
    lieux, objets = man["lieux"], man["objets"]
    pseudo = pseudo_propre(pseudo)
    op = cmd["op"]
    if op in ("raz", "verrou") and not hote:
        return False, "réservé à l'hôte de la partie"
    if trk.get("verrou") and not hote:
        return False, "corrections verrouillées par l'hôte"
    if op == "verrou":
        if int(bool(trk.get("verrou"))) == cmd["v"]:
            return False, ""
        trk["verrou"] = cmd["v"]
        return True, ""
    if op == "raz":
        if not lieux and not objets:
            return False, ""
        lieux.clear()
        objets.clear()
        return True, ""
    if op == "annuler":
        if "i" in cmd:
            return (lieux.pop(cmd["i"], None) is not None), ""
        if "nom" in cmd:
            return (objets.pop(cmd["nom"], None) is not None), ""
        avant = len(lieux)
        for i in tables(lg)["zones"][cmd["zone"]]:
            lieux.pop(i, None)
        return len(lieux) != avant, ""
    if op == "objet":
        if cmd["nom"] not in objets and len(lieux) + len(objets) >= MARQUES_MAX:
            return False, "trop de corrections (annule-en d'abord)"
        if objets.get(cmd["nom"], [None])[0] == cmd["v"]:
            return False, ""
        objets[cmd["nom"]] = [cmd["v"], pseudo]
        return True, ""
    if op == "lieu":
        cibles = [cmd["i"]]
    elif "lieux" in cmd:
        cibles = list(cmd["lieux"])     # lieux montrés par la page (filtre du joueur), déjà bornés à la zone
    else:
        cibles = list(tables(lg)["zones"][cmd["zone"]])
    neufs = [i for i in cibles if i not in lieux]
    if len(lieux) + len(objets) + len(neufs) > MARQUES_MAX:
        return False, "trop de corrections (annule-en d'abord)"
    change = False
    for i in cibles:
        if lieux.get(i, [None])[0] != cmd["v"]:
            lieux[i] = [cmd["v"], pseudo]
            change = True
    return change, ""


def valider_trk(msg) -> dict | None:
    """Complément ``trk`` d'un client de jeu, nettoyé : {"vie": quarts} (seul champ dont l'adresse est prouvée ;
    tout autre champ est ignoré). None : rien d'utilisable."""
    if not isinstance(msg, dict):
        return None
    vie = msg.get("vie")
    if _entier(vie) and VIE_DEPART <= vie <= VIE_MAX:
        return {"vie": vie}
    return None


# ---------------------------------------------------------------------------------------------------------
# Persistance (section « trackeur » du monde persistant)
# ---------------------------------------------------------------------------------------------------------
def exporter(lg, trk: dict) -> dict:
    """Section du monde persistant. ``donnees`` date les index de lieux (ils ne valent que pour ces données)."""
    man = trk.get("man") or {}
    return {"v": VERSION, "donnees": lg.version,
            "man": {"lieux": [[i, e[0], pseudo_propre(e[1])] for i, e in sorted((man.get("lieux") or {}).items())],
                    "objets": [[n, e[0], pseudo_propre(e[1])] for n, e in sorted((man.get("objets") or {}).items())]},
            "vus": {k: v for k, v in sorted((trk.get("vus") or {}).items())},
            "comp": dict(trk.get("comp") or {}), "verrou": int(bool(trk.get("verrou"))),
            # lieux reçus (lettres, cadeaux) : par SITE (stable d'une version des données de logique à l'autre)
            "recus": {g: [[k, pseudo_propre(p)] for k, p in sorted(((trk.get("recus") or {}).get(g) or {}).items())]
                      for g in sorted(RECUS)}}


def restaurer(lg, doc) -> tuple[dict, int]:
    """Section relue du disque -> (état validé, nombre d'entrées abîmées ignorées). Un document sans section, ou
    d'une autre version, rend un état neuf. Les marques de lieux d'AUTRES données de logique sont abandonnées (leurs
    index ne désignent plus les mêmes lieux) ; objets, « vus » et verrou sont gardés."""
    trk, ignores = neuf(), 0
    if not isinstance(doc, dict) or doc.get("v") != VERSION:
        return trk, (1 if doc is not None else 0)
    t = tables(lg)
    man = doc.get("man") if isinstance(doc.get("man"), dict) else {}
    lieux = man.get("lieux") if isinstance(man.get("lieux"), list) else []
    if doc.get("donnees") == lg.version:
        for e in lieux[:LIEUX_LUS_MAX]:
            if isinstance(e, list) and len(e) == 3 and _entier(e[0]) and e[0] in t["visibles"] \
                    and e[1] in (0, 1) and _entier(e[1]) and len(trk["man"]["lieux"]) < MARQUES_MAX:
                trk["man"]["lieux"][e[0]] = [e[1], pseudo_propre(e[2])]
            else:
                ignores += 1
    else:
        ignores += len(lieux[:LIEUX_LUS_MAX])
    objets = man.get("objets") if isinstance(man.get("objets"), list) else []
    for e in objets[:LIEUX_LUS_MAX]:
        if isinstance(e, list) and len(e) == 3 and isinstance(e[0], str) and e[0] in NIVEAU_MAX \
                and e[0] in lg.objets and _entier(e[1]) and 0 <= e[1] <= NIVEAU_MAX[e[0]] \
                and len(trk["man"]["lieux"]) + len(trk["man"]["objets"]) < MARQUES_MAX:
            trk["man"]["objets"][e[0]] = [e[1], pseudo_propre(e[2])]
        else:
            ignores += 1
    vus = doc.get("vus") if isinstance(doc.get("vus"), dict) else {}
    for k, v in list(vus.items())[:LIEUX_LUS_MAX]:
        if isinstance(k, str) and k in lg.objets and _entier(v) and 0 < v <= VALEUR_MAX:
            trk["vus"][k] = v
        else:
            ignores += 1
    comp = valider_trk(doc.get("comp"))
    if comp:
        trk["comp"] = comp
    trk["verrou"] = 1 if doc.get("verrou") in (1, True) else 0
    recus = doc.get("recus") if isinstance(doc.get("recus"), dict) else {}
    for genre in sorted(RECUS):
        for e in (recus.get(genre) if isinstance(recus.get(genre), list) else [])[:LIEUX_LUS_MAX]:
            if isinstance(e, list) and len(e) == 2 and _entier(e[0]) and e[0] in RECUS[genre]:
                trk["recus"][genre][e[0]] = pseudo_propre(e[1])
            else:
                ignores += 1
    return trk, ignores


# ---------------------------------------------------------------------------------------------------------
# Vue pour la page du lanceur
# ---------------------------------------------------------------------------------------------------------
def etat_de_la_partie(detail, etat_recu) -> bool:
    """Vrai si ``etat_recu`` (``trackeur_etat``) est bien celui de la partie du lobby ``detail`` (même empreinte).
    Après une reconnexion à une autre partie, l'ancien état reste en main un instant : il ne vaut plus rien."""
    if not isinstance(etat_recu, dict):
        return False
    emp = etat_recu.get("emp")
    return not isinstance(emp, str) or not isinstance(detail, dict) or not isinstance(detail.get("emp"), str)         or emp == detail["emp"][:64]


def lire_etat(lg, e) -> dict | None:
    """``trackeur_etat`` reçu du réseau, revalidé en entier (tout ce qui est mal formé est ignoré). None : message
    inutilisable (autre version, autres données de logique)."""
    if not isinstance(e, dict) or e.get("v") != VERSION or e.get("donnees") != lg.version:
        return None
    t = tables(lg)
    n = len(lg.lieux)
    out = {"rev": e.get("rev") if _entier(e.get("rev")) else 0, "exact": e.get("exact") in (1, True),
           "suivi": "auto" if e.get("suivi") == "auto" else "partiel", "verrou": e.get("verrou") in (1, True),
           "obj": {}, "cles": {}, "faits": {}, "acc": set(), "man_lieux": {}, "man_objets": {}}
    obj = e.get("obj")
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:LIEUX_LUS_MAX]:
            if isinstance(k, str) and k in lg.objets and _entier(v) and 0 < v <= VALEUR_MAX:
                out["obj"][k] = v
    cles = e.get("cles")
    if isinstance(cles, dict):
        for d in lg.donjons:
            c = cles.get(d)
            if isinstance(c, list) and len(c) == 4 and all(_entier(x) and 0 <= x <= VALEUR_MAX for x in c):
                out["cles"][d] = [c[0], int(bool(c[1])), int(bool(c[2])), int(bool(c[3]))]
    faits = e.get("faits")
    if isinstance(faits, list):
        for f in faits[:LIEUX_LUS_MAX]:
            if isinstance(f, list) and len(f) == 3 and _entier(f[0]) and 0 <= f[0] < n and f[0] in t["visibles"] \
                    and f[1] in ("a", "m", "d"):
                out["faits"][f[0]] = (f[1], pseudo_propre(f[2]))
    acc = e.get("acc")
    if out["exact"] and isinstance(acc, list):
        out["acc"] = {i for i in acc[:LIEUX_LUS_MAX] if _entier(i) and i in t["visibles"]} - set(out["faits"])
    man = e.get("man") if isinstance(e.get("man"), dict) else {}
    for f in (man.get("lieux") if isinstance(man.get("lieux"), list) else [])[:LIEUX_LUS_MAX]:
        if isinstance(f, list) and len(f) == 3 and _entier(f[0]) and f[0] in t["visibles"] and f[1] in (0, 1) \
                and _entier(f[1]):
            out["man_lieux"][f[0]] = (f[1], pseudo_propre(f[2]))
    for f in (man.get("objets") if isinstance(man.get("objets"), list) else [])[:LIEUX_LUS_MAX]:
        if isinstance(f, list) and len(f) == 3 and isinstance(f[0], str) and f[0] in NIVEAU_MAX and _entier(f[1]) \
                and 0 <= f[1] <= NIVEAU_MAX[f[0]]:
            out["man_objets"][f[0]] = (f[1], pseudo_propre(f[2]))
    return out


def _plan(lg, detail: dict):
    """({index de lieu mélangé} ou None, exact) pour la section « rando » du lobby (comme rando_suivi._plan)."""
    from rando import moteur as rm
    if detail.get("mode") != "complet":
        return None, True
    if not lg.memes_donnees(detail.get("donnees"), detail.get("options")):
        return None, False
    try:
        opt = rm.normaliser_options(detail.get("options"), lg)
    except rnd.ErreurRando:
        return None, False
    return set(rm.Plan(lg, opt).mobiles), True


def contenu_hote(detail, graine, lg=None) -> dict | None:
    """Spoiler de l'HÔTE (réglage du lanceur, coupé par défaut ; étude §7.6) : {index de lieu: numéro d'objet}
    recalculé SUR CE PC à partir de la graine que seul l'hôte a dans sa configuration. Rien ne circule : le
    résultat n'est rendu que si la table recalculée a l'empreinte que le lobby annonce (sinon None : mauvaise
    graine, autres outils, ou simple invité)."""
    if not isinstance(detail, dict) or not isinstance(graine, str) or not graine.strip():
        return None
    try:
        if lg is None:
            from rando import moteur as rm
            lg = rm.logique()
        if detail.get("mode") == "complet":
            if not lg.memes_donnees(detail.get("donnees"), detail.get("options")):
                return None
            table = rnd.calculer_complet(graine, detail.get("options"))
        else:
            table = rnd.calculer(graine)
        if not isinstance(detail.get("emp"), str) or table.empreinte != detail["emp"]:
            return None
        t = tables(lg)
        out = {}
        hors = getattr(table, "hors", {})                 # lot 5 : l'objet réel d'un coffre étranger
        for k, (_o, n) in table.entrees.items():
            if k in t["coffre"]:
                out[t["coffre"][k]] = hors[k][0] if k in hors else n
        for site, (_o, n) in getattr(table, "entrees_n2", {}).items():
            if site in t["cadeau"]:
                out[t["cadeau"][site]] = n
        for site, (_o, n) in getattr(table, "entrees_don", {}).items():
            if site in t["donnee"]:
                out[t["donnee"][site]] = n
        conflit = {site: i for i, site in getattr(lg, "conflit", {}).items()}      # coffres « en conflit » (lot 2)
        for site, (_o, n) in getattr(table, "entrees_conflit", {}).items():
            if site in conflit:
                out[conflit[site]] = n
        fich = {site: i for i, site in getattr(lg, "fich", {}).items()}            # lieux du lot 6
        for site, (_o, n) in getattr(table, "entrees_fich", {}).items():
            if site in fich:
                out[fich[site]] = n
        return out
    except (rnd.ErreurRando, KeyError, TypeError, ValueError, AttributeError):
        return None


def vue(detail, etat_recu, langue: str = "fr", filtre: str = "melanges", contenu: dict | None = None) -> dict | None:
    """Données de la page du trackeur (None : partie sans randomizer). ``detail`` : section « rando » du lobby ;
    ``etat_recu`` : dernier ``trackeur_etat`` (None : serveur ancien ou trackeur coupé -> mode dégradé, lieux =
    coffres cochés depuis ``pris``, ni objets ni accessibilité).

    Résultat : mode, emp, rev, exact, suivi, verrou, degrade ("" | "ancien" | "donnees" | "logique"), filtre,
    objets [{cle, nom, niveau, max, picto, groupe, manuel, par}], donjons [{zone, nom, petites, petites_max, grande,
    carte, boussole, faits, total, acc}], mer (49 cases {secteur, ligne, col, zone, nom, faits, total, acc}),
    hors_carte [{zone, nom, faits, total, acc}], zones {zone: {nom, faits, total, acc, lieux: [{i, nom, etat,
    origine, par, objet, melange, auto}]}}, stats {faits, total, acc}. ``objet`` n'existe que pour un lieu FAIT
    dont le lobby publie déjà le contenu (coffre ouvert). ``contenu`` (``contenu_hote`` : spoiler de l'hôte,
    jamais donné par défaut) ajoute ``contenu`` à chaque lieu, fait ou non."""
    if not isinstance(detail, dict) or not isinstance(detail.get("emp"), str):
        return None
    langue = "en" if langue == "en" else "fr"
    filtre = filtre if filtre in FILTRES else FILTRES[0]
    from rando import suivi as suivi_mod
    pris = suivi_mod._pris(detail)
    cle = (detail.get("emp"), detail.get("mode"), detail.get("donnees"), repr(detail.get("options")), langue, filtre,
           tuple(sorted(pris.items(), key=str)), id(contenu))
    with _VERROU:
        if _CACHE.get("cle") == cle and _CACHE.get("etat") is etat_recu and _CACHE.get("contenu") is contenu:
            return _CACHE["vue"]
    try:
        v = _construire(detail, etat_recu, langue, filtre, pris, contenu if isinstance(contenu, dict) else None)
    except (rnd.ErreurRando, KeyError, TypeError, ValueError, IndexError, AttributeError) as ex:
        v = {"mode": "complet" if detail.get("mode") == "complet" else "sur", "emp": detail["emp"][:16], "rev": 0,
             "exact": False, "suivi": "partiel", "verrou": False, "degrade": "logique", "filtre": filtre,
             "objets": [], "donjons": [], "mer": [], "hors_carte": [], "zones": {},
             "stats": {"faits": 0, "total": 0, "acc": 0}, "erreur": str(ex)[:200]}
    with _VERROU:
        _CACHE.update({"cle": cle, "etat": etat_recu, "contenu": contenu, "vue": v})
    return v


def _construire(detail: dict, etat_recu, langue: str, filtre: str, pris: dict, contenu: dict | None) -> dict:
    from rando import moteur as rm
    lg = rm.logique()
    t = tables(lg)
    en = langue == "en"
    mode = "complet" if detail.get("mode") == "complet" else "sur"
    mobiles, exact_plan = _plan(lg, detail)
    if isinstance(etat_recu, dict) and not etat_de_la_partie(detail, etat_recu):
        etat_recu = None                # état d'une AUTRE partie (reconnexion) : jamais affiché sur celle-ci
    e = lire_etat(lg, etat_recu)
    degrade = ""
    if e is None:
        degrade = "ancien" if etat_recu is None else "donnees"
        e = {"rev": 0, "exact": False, "suivi": "partiel", "verrou": False, "obj": {}, "cles": {}, "acc": set(),
             "man_lieux": {}, "man_objets": {},
             "faits": {t["coffre"][k]: ("a", "") for k in pris if k in t["coffre"]}}
    exact = bool(e["exact"]) and exact_plan
    if not exact:
        e["acc"] = set()                # table d'autres données : pas d'accessibilité (elle serait fausse)

    def coffre(i) -> bool:
        x = lg.lieux[i]
        return bool(x.get("applicable")) and x.get("typ") == rm.TYPE_COFFRE

    if filtre == "tous":
        montres = set(t["visibles"])
    else:
        montres = {i for i in t["visibles"] if coffre(i)} if (filtre == "coffres" or mobiles is None) else set()
        if mobiles is not None:
            montres |= {i for i in mobiles if i in t["visibles"]}
    montres |= {i for i in e["faits"] if e["faits"][i][0] != "a" or filtre != "melanges" or mobiles is None
                or i in montres}

    zones = {}
    for z in ZONES:
        lieux = []
        for i in t["zones"][z]:
            if i not in montres:
                continue
            x = lg.lieux[i]
            nom = x["nom"] if en else x["nom_fr"]
            court = nom.partition(" - ")[2] or nom
            fait = e["faits"].get(i)
            ligne = {"i": i, "nom": court, "etat": "fait" if fait else ("acc" if i in e["acc"] else "non"),
                     "origine": fait[0] if fait else "", "par": fait[1] if fait else "",
                     "melange": bool(mobiles is not None and i in mobiles), "auto": coffre(i)}
            if fait and coffre(i):
                objet = pris.get((x["idx"], x["tbox"]))
                if objet is not None:
                    ligne["objet"] = rnd.nom_objet(objet, langue)
            if contenu is not None and _entier(contenu.get(i)):
                ligne["contenu"] = rnd.nom_objet(contenu[i], langue)
            if i in e["man_lieux"]:
                ligne["manuel"] = e["man_lieux"][i][0]
                ligne["manuel_par"] = e["man_lieux"][i][1]
            lieux.append(ligne)
        lieux.sort(key=lambda l: l["nom"])
        zones[z] = {"nom": NOMS_COURTS[z][1 if en else 0], "lieux": lieux, "total": len(lieux),
                    "faits": sum(1 for l in lieux if l["etat"] == "fait"),
                    "acc": sum(1 for l in lieux if l["etat"] == "acc")}

    def resume(z, **plus):
        return dict({"zone": z, "nom": zones[z]["nom"], "faits": zones[z]["faits"], "total": zones[z]["total"],
                     "acc": zones[z]["acc"]}, **plus)

    objets = []
    if not degrade:
        for nom, niveau_max, picto, groupe in GRILLE_OBJETS:
            if nom not in lg.objets:
                continue
            o = lg.objets[nom]
            d = {"cle": nom, "nom": nom if en else (o.get("nom_fr") or nom), "niveau": niveau(e["obj"], nom),
                 "max": niveau_max, "picto": picto, "groupe": groupe, "manuel": nom in e["man_objets"],
                 "par": e["man_objets"][nom][1] if nom in e["man_objets"] else ""}
            objets.append(d)
    donjons = []
    for d in DONJONS:
        c = e["cles"].get(d, [0, 0, 0, 0])
        donjons.append(resume(d, petites=c[0], petites_max=_PETITES_MAX.get(d, 0), grande=c[1], carte=c[2],
                              boussole=c[3], cles_suivies=not degrade))
    return {"mode": mode, "emp": detail["emp"][:16], "rev": e["rev"], "exact": exact, "suivi": e["suivi"],
            "verrou": bool(e["verrou"]), "degrade": degrade, "filtre": filtre,
            "filtre_effectif": filtre if (mobiles is not None or filtre != "melanges") else "coffres",
            "objets": objets, "donjons": donjons,
            "mer": [resume(z, secteur=s, ligne=(s - 1) // 7, col=(s - 1) % 7) for s, z in enumerate(SECTEURS, 1)],
            "hors_carte": [resume(z) for z in HORS_CARTE if z not in DONJONS],
            "zones": zones,
            "stats": {"faits": sum(z["faits"] for z in zones.values()),
                      "total": sum(z["total"] for z in zones.values()),
                      "acc": sum(z["acc"] for z in zones.values())}}
