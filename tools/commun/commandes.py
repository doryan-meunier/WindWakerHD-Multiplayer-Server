"""Commandes WWMP configurables (onglet « Commandes » du lanceur, sous-onglets « Hanteur » et « Multi ») : la TABLE
des commandes, leurs touches et combinaisons de manette par défaut, la lecture et la vérification des réglages.

Partagé par le lanceur (affichage, réglage gardé dans compagnon.json, clé ``commandes``) et le client (lecture des
touches et de la manette pendant la partie : ``synchro/hanteurs.Touches``). Le lanceur passe la table RÉSOLUE au
client dans son fichier de contrôle (``--control-file``, clé ``commandes``), relu au plus une fois par seconde : un
changement fait dans le lanceur compte en pleine partie, sans relancer.

Ajouter une commande : une ligne dans ``COMMANDES`` (identifiant = l'action que ``HanteurSync.commande`` reçoit,
ou celle d'un autre module pour le groupe « multi »), ses textes FR/EN, sa touche par défaut (Cemu et portage natif)
et sa combinaison de manette par défaut. Rien d'autre à toucher : le lanceur l'affiche, détecte ses conflits, et le
client la lit. Le module qui fait l'action reçoit son identifiant (``Touches.lire_commandes``).

Touches : noms du portage natif (``runtime/src/input_map.cpp`` kKeys : « F4 », « PageUp », « K », « Keypad5 »…),
avec des modificateurs « Ctrl+ », « Shift+ », « Alt+ » (ordre fixe). Une touche ne compte qu'avec EXACTEMENT ses
modificateurs (comme les raccourcis du portage : Maj+F4 n'est pas F4).

Manette : boutons de la Wii U (« L », « ZR », « DpadDown »… : ceux que le jeu reçoit, après l'affectation de l'onglet
Link), joints par « + ». Lus par le mod natif (zone WWMPMAN1, ``natif/mod_wwmp/manette.c``, filtre d'entrée du SDK) :
une combinaison COMPLÈTE est retirée au jeu (il ne la voit pas) jusqu'au relâchement de ses boutons. Cemu : pas de
mod, pas de manette (touches seulement).
"""
from __future__ import annotations

from dataclasses import dataclass

GROUPES = ("hanteur", "multi")
MODIFICATEURS = ("Ctrl", "Shift", "Alt")
VK_MODIFICATEURS = {"Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12}


@dataclass(frozen=True)
class Commande:
    id: str
    groupe: str                  # « hanteur » ou « multi »
    fr: str
    en: str
    clavier: str                 # touche par défaut (Cemu)
    clavier_natif: str | None    # touche par défaut sur le portage natif (None : la même)
    manette: str                 # combinaison par défaut ("" : aucune)
    aide_fr: str = ""
    aide_en: str = ""
    manette_fr: str = ""         # non vide : manette PAS réglable ici (lue ailleurs), ce texte dit comment
    manette_en: str = ""

    def defaut_clavier(self, natif: bool) -> str:
        return self.clavier_natif if natif and self.clavier_natif is not None else self.clavier


# Manette : L tenu d'abord. Pourquoi L : en partie, le jeu ne reçoit pas le L de la Wii U (portage, réglage « L turns
# the GamePad page » coupé par défaut : input_map::play_buttons) ; L ne fait que tourner la page du GamePad hors partie.
# Bas de la croix est aussi libre en partie. Et le mod retire la combinaison entière au jeu tant qu'elle est tenue.
# Évités : X, Y, R (objets ; l'anneau d'objets du portage les lit avant les mods), Home (menu du portage), − (tenu :
# menu du portage). A et B ne servent qu'avec L tenu d'abord (combinaison retirée au jeu : pas de coup d'épée).
# --- table (à compléter ; je fusionne) -------------------------------------------------------------------------------
COMMANDES: tuple[Commande, ...] = (
    Commande("posseder", "hanteur", "Posséder / relâcher un monstre",
             "Possess / release a monster", "F4", "Ctrl+F4", "L+DpadDown",
             "Aussi : arrêter la potion ; Link possédé : résister.", "Also: stop the potion; possessed Link: resist."),
    Commande("capacite", "hanteur", "Capacité de l'espèce possédée", "Possessed species' ability",
             "F2", "Ctrl+F2", "L+B", "Grand coup du Moblin, garde du Darknut, flaque du Chuchu.",
             "Moblin's big swing, Darknut's guard, Chuchu's puddle."),
    Commande("garde", "hanteur", "Ordre « garde » (boss : attaque)", "“Guard” order (boss: attack)",
             "F3", "Ctrl+F3", "L+ZR"),
    Commande("suivre", "hanteur", "Suivre le Link suivant (manifesté : redevenir spectateur)",
             "Follow the next Link (manifested: back to spectator)", "F5", "Ctrl+F5", "L+DpadRight"),
    Commande("invoquer", "hanteur", "Invoquer l'espèce choisie", "Summon the chosen species",
             "F6", "Ctrl+F6", "L+DpadLeft"),
    Commande("espece", "hanteur", "Espèce suivante (en possession : viser le Link suivant)",
             "Next species (while possessing: target the next Link)", "F7", None, "L+RightStickClick"),
    Commande("suivi", "hanteur", "Suivi continu de la caméra (on / off)", "Continuous camera follow (on / off)",
             "F8", None, "L+LeftStickClick"),
    Commande("manifester", "hanteur", "Se manifester / redevenir spectateur", "Manifest / back to spectator",
             "F9", None, "L+DpadUp"),
    Commande("monter", "hanteur", "Monter (volant piloté)", "Fly up (piloted flyer)", "PageUp", None, "L+Plus",
             "Tiens L d'abord : + seul ouvre le menu du jeu.", "Hold L first: + alone opens the game menu."),
    Commande("descendre", "hanteur", "Descendre (volant piloté)", "Fly down (piloted flyer)", "PageDown", None,
             "L+ZL"),
    Commande("enfermer", "hanteur", "Enfermer les Link proches (cercle)", "Trap nearby Links (circle)", "End", None,
             "L+A"),
    # multijoueur (client/reperes.py) : touche réglable ; la manette (double clic du stick gauche) est relevée par le
    # mod (reperes.c, zone WWMPSIG1), pas par cette table
    Commande("signal", "multi", "Signal « viens ici »", "“Come here” signal", "Insert", None, "",
             "Un signal à ta position, vu par les autres 8 s (portage natif : aussi sur leurs cartes).",
             "A signal at your position, seen by the others for 8 s (native port: on their maps too).",
             "double clic du stick gauche (relevé par le mod, pas réglable ici)",
             "double click of the left stick (read by the mod, not set here)"),
    # chant de ralliement (client/ralliement.py) : refuser un appel pendant ses 10 s ; touche seulement
    Commande("refuser_ralliement", "multi", "Refuser le ralliement", "Refuse the rally call", "ForwardDelete", None, "",
             "Pendant les 10 s d'un chant de ralliement d'un coéquipier : tu restes où tu es.",
             "During the 10 s of a teammate's rally song: you stay where you are.",
             "pas de manette : la touche, ou ouvre le menu du jeu (un joueur au menu n'est pas déplacé)",
             "no controller: the key, or open the game menu (a player in the menu is not moved)"),
    # Fragments du Bonheur (client/bonheur.py) : proposer d'assembler deux moitiés, ou accepter ; touche seulement
    Commande("assembler", "multi", "Assembler (Fragments du Bonheur)", "Fuse (Kinstones)", "Ctrl+Insert", None, "",
             "À moins de 3 m d'un coéquipier qui a la moitié assortie : propose d'assembler ; il appuie à son tour "
             "dans les 10 s pour accepter. Une récompense tombe pour les deux.",
             "Within 3 m of a teammate who has the matching half: offer to fuse; they press it too within 10 s to "
             "accept. A reward drops for both.",
             "pas de manette : la touche seulement", "no controller: the key only"),
    # courses en bateau (client/courses.py) : tracer un parcours en naviguant, abandonner ; touches seulement
    Commande("poser_balise", "multi", "Poser une balise (tracé d'une course)", "Drop a buoy (race layout)", "HomeKey",
             None, "",
             "En mer : la position du bateau. Le premier point est le départ, les suivants les balises (lanceur : "
             "Réglages › Courses en bateau).",
             "At sea: the boat's position. The first point is the start, the next ones the buoys (launcher: "
             "Settings › Boat races).",
             "pas de manette : la touche seulement", "no controller: the key only"),
    Commande("abandon_course", "multi", "Abandonner la course", "Give up the race", "Ctrl+HomeKey", None, "",
             "Pendant une course en bateau : tu sors du classement (quitter la mer fait de même).",
             "During a boat race: you leave the ranking (leaving the sea does the same).",
             "pas de manette : la touche seulement", "no controller: the key only"),
    # relever un coéquipier (client/releve.py) : relever tout de suite un coéquipier à terre à moins de 1,5 m ; touche
    Commande("relever", "multi", "Relever un coéquipier", "Revive a teammate", "Ctrl+End", None, "",
             "Deathlink : un coéquipier tombé à 0 cœur reste à terre 20 s ; à moins de 1,5 m de lui, la touche le relève "
             "tout de suite (sinon reste 2 s près de lui).",
             "Deathlink: a teammate at 0 hearts stays down for 20 s; within 1.5 m of them, the key revives them right "
             "away (otherwise stay 2 s next to them).",
             "pas de manette : la touche, ou reste 2 s près de lui", "no controller: the key, or stay 2 s next to them"),
)
# Commandes visibles mais pas configurables ici (pourquoi : ``aide``)
LECTURE_SEULE: tuple[dict, ...] = (
    {"id": "attaque", "groupe": "hanteur", "fr": "Attaque (monstre possédé)", "en": "Attack (possessed monster)",
     "touche_fr": "coup d'épée du Link caché", "touche_en": "the hidden Link's sword swing",
     "aide_fr": "Lu dans l'état du Link caché (pas une touche) : se règle dans l'onglet Link (épée).",
     "aide_en": "Read from the hidden Link's state (not a key): set it in the Link tab (sword)."},
    {"id": "lourde", "groupe": "hanteur", "fr": "Attaque lourde", "en": "Heavy attack",
     "touche_fr": "attaque tournoyante ou coup sauté", "touche_en": "spin attack or jump attack",
     "aide_fr": "Idem : un geste du Link caché.", "aide_en": "Same: a move of the hidden Link."},
    {"id": "piloter", "groupe": "hanteur", "fr": "Déplacer le monstre piloté", "en": "Move the piloted monster",
     "touche_fr": "stick gauche (déplacement de Link)", "touche_en": "left stick (Link's movement)",
     "aide_fr": "Le monstre suit le Link caché : ses touches de déplacement (onglet Link).",
     "aide_en": "The monster follows the hidden Link: its movement keys (Link tab)."},
)

# Boutons de la Wii U (bits VPAD de ``wwhd_input_state.buttons``), dans l'ordre d'affichage. Home exclu (menu du
# portage). Code compact 16 bits (zone WWMPMAN1) : bits VPAD 0x0004..0xFFFC tels quels, clics des sticks (0x40000,
# 0x20000) aux bits 0 et 1 (libres : 0x1 est la synchro, 0x2 Home, jamais demandés).
BOUTONS: dict[str, int] = {
    "A": 0x8000, "B": 0x4000, "X": 0x2000, "Y": 0x1000, "L": 0x0020, "R": 0x0010, "ZL": 0x0080, "ZR": 0x0040,
    "Plus": 0x0008, "Minus": 0x0004, "DpadUp": 0x0200, "DpadDown": 0x0100, "DpadLeft": 0x0800,
    "DpadRight": 0x0400, "LeftStickClick": 0x40000, "RightStickClick": 0x20000,
}
NOMS_BOUTONS = {"A": "A", "B": "B", "X": "X", "Y": "Y", "L": "L", "R": "R", "ZL": "ZL", "ZR": "ZR", "Plus": "+",
                "Minus": "−", "DpadUp": "Croix ↑", "DpadDown": "Croix ↓", "DpadLeft": "Croix ←",
                "DpadRight": "Croix →", "LeftStickClick": "Clic stick G", "RightStickClick": "Clic stick D"}
N_COMBOS = 16            # combinaisons lues par le mod (zone WWMPMAN1)


def compact(bits: int) -> int:
    """Bits VPAD -> code compact 16 bits du mod (manette.c ``compact``)."""
    return (bits & 0xFFFC) | ((bits >> 17) & 0x3)


# Touches : nom du portage -> (code virtuel Windows, scancode (jeu de codes 1, None : code virtuel fixe), keysym X11).
# Lettres, chiffres et ponctuation : le scancode (touche PHYSIQUE, comme le portage et event.code du navigateur) est
# converti en code virtuel par Windows (MapVirtualKeyW) : AZERTY ou QWERTY, c'est la même touche que dans le jeu.
_LETTRES_SC = dict(zip("QWERTYUIOP", range(0x10, 0x1A)))
_LETTRES_SC.update(zip("ASDFGHJKL", range(0x1E, 0x27)))
_LETTRES_SC.update(zip("ZXCVBNM", range(0x2C, 0x33)))
TOUCHES: dict[str, tuple[int, int | None, int]] = {}
for _l, _sc in _LETTRES_SC.items():
    TOUCHES[_l] = (ord(_l), _sc, ord(_l.lower()))
for _i, _c in enumerate("1234567890"):
    TOUCHES[_c] = (ord(_c), 0x02 + _i, ord(_c))
for _i in range(1, 13):
    TOUCHES[f"F{_i}"] = (0x6F + _i, None, 0xFFBD + _i)
for _i in range(13, 17):
    TOUCHES[f"F{_i}"] = (0x6F + _i, None, 0xFFBD + _i)
for _i in range(10):
    TOUCHES[f"Keypad{_i}"] = (0x60 + _i, None, 0xFFB0 + _i)
TOUCHES.update({
    "Insert": (0x2D, None, 0xFF63),
    "Up": (0x26, None, 0xFF52), "Down": (0x28, None, 0xFF54), "Left": (0x25, None, 0xFF51),
    "Right": (0x27, None, 0xFF53), "HomeKey": (0x24, None, 0xFF50), "End": (0x23, None, 0xFF57),
    "PageUp": (0x21, None, 0xFF55), "PageDown": (0x22, None, 0xFF56), "Space": (0x20, None, 0x20),
    "Return": (0x0D, None, 0xFF0D), "Tab": (0x09, None, 0xFF09), "Backspace": (0x08, None, 0xFF08),
    "ForwardDelete": (0x2E, None, 0xFFFF), "Escape": (0x1B, None, 0xFF1B),
    "KeypadDecimal": (0x6E, None, 0xFFAE), "KeypadMultiply": (0x6A, None, 0xFFAA),
    "KeypadPlus": (0x6B, None, 0xFFAB), "KeypadMinus": (0x6D, None, 0xFFAD), "KeypadDivide": (0x6F, None, 0xFFAF),
    "Minus": (0xBD, 0x0C, 0x2D), "Equal": (0xBB, 0x0D, 0x3D), "LeftBracket": (0xDB, 0x1A, 0x5B),
    "RightBracket": (0xDD, 0x1B, 0x5D), "Backslash": (0xDC, 0x2B, 0x5C), "Semicolon": (0xBA, 0x27, 0x3B),
    "Quote": (0xDE, 0x28, 0x27), "Comma": (0xBC, 0x33, 0x2C), "Period": (0xBE, 0x34, 0x2E),
    "Slash": (0xBF, 0x35, 0x2F), "Grave": (0xC0, 0x29, 0x60), "Section": (0xE2, 0x56, 0x3C),
})
KEYSYMS_MODIFICATEURS = {"Ctrl": (0xFFE3, 0xFFE4), "Shift": (0xFFE1, 0xFFE2), "Alt": (0xFFE9, 0xFFEA)}

# Raccourcis du portage natif (runtime/src/input_map.cpp kShortcuts, valeurs par défaut de la v0.5.1) : une touche du
# mode Hanteurs identique (mêmes modificateurs) ferait aussi l'action du portage (états de sauvegarde !).
RACCOURCIS_PORTAGE: tuple[tuple[str, str], ...] = (
    ("Escape", "Réglages du portage"), ("F1", "Réglages du portage"), ("F11", "Plein écran"),
    ("Alt+Return", "Plein écran"), ("F10", "Capture d'écran"),
    ("Shift+F1", "Sauver l'état 1"), ("Shift+F2", "Sauver l'état 2"), ("Shift+F3", "Sauver l'état 3"),
    ("Shift+F4", "Sauver l'état 4"), ("Shift+F5", "Sauver l'état 5"),
    ("F6", "Charger l'état 1"), ("F2", "Charger l'état 2"), ("F3", "Charger l'état 3"), ("F4", "Charger l'état 4"),
    ("F5", "Charger l'état 5"), ("R", "Résolution interne"), ("O", "Occlusion ambiante"),
    ("M", "Occlusion pleine taille"), ("N", "Filtrage anisotrope"), ("6", "Interpolation d'images"), ("8", "FXAA"),
)


def analyser_touche(texte) -> tuple[frozenset, str] | None:
    """« Ctrl+F4 » -> (frozenset({"Ctrl"}), "F4") ; "" -> (frozenset(), "") (aucune) ; illisible -> None."""
    if not isinstance(texte, str):
        return None
    t = texte.strip()
    if not t:
        return frozenset(), ""
    morceaux = t.split("+")
    cle = morceaux[-1]
    mods = morceaux[:-1]
    if cle not in TOUCHES or len(set(mods)) != len(mods) or any(m not in MODIFICATEURS for m in mods):
        return None
    return frozenset(mods), cle


def normaliser_touche(texte) -> str | None:
    a = analyser_touche(texte)
    if a is None:
        return None
    mods, cle = a
    return "+".join([m for m in MODIFICATEURS if m in mods] + [cle]) if cle else ""


def analyser_manette(texte) -> frozenset | None:
    """« L+DpadDown » -> frozenset({"L", "DpadDown"}) ; "" -> frozenset() ; illisible -> None (4 boutons au plus)."""
    if not isinstance(texte, str):
        return None
    t = texte.strip()
    if not t:
        return frozenset()
    b = t.split("+")
    if len(set(b)) != len(b) or len(b) > 4 or any(x not in BOUTONS for x in b):
        return None
    return frozenset(b)


def normaliser_manette(texte) -> str | None:
    a = analyser_manette(texte)
    if a is None:
        return None
    return "+".join(x for x in BOUTONS if x in a)


def bits_manette(texte) -> int:
    a = analyser_manette(texte) or frozenset()
    v = 0
    for x in a:
        v |= BOUTONS[x]
    return v


def par_id() -> dict[str, Commande]:
    return {c.id: c for c in COMMANDES}


def du_groupe(table: dict, groupe: str) -> dict:
    """Les entrées de ``table`` de ce groupe (une commande inconnue de cette table : gardée, groupe « hanteur »)."""
    connus = par_id()
    return {k: v for k, v in table.items() if (connus[k].groupe if k in connus else "hanteur") == groupe}


def normaliser(reglages) -> dict:
    """Réglages gardés (compagnon.json ``commandes``) nettoyés : {id: {"clavier": str, "manette": str}} pour les
    seules valeurs CHANGÉES (une valeur illisible ou une commande inconnue est oubliée : retour au défaut)."""
    out: dict = {}
    if not isinstance(reglages, dict):
        return out
    connus = par_id()
    for k, v in reglages.items():
        if k not in connus or not isinstance(v, dict):
            continue
        e = {}
        if "clavier" in v:
            t = normaliser_touche(v["clavier"])
            if t is not None:
                e["clavier"] = t
        if "manette" in v and not connus[k].manette_fr:
            m = normaliser_manette(v["manette"])
            if m is not None:
                e["manette"] = m
        if e:
            out[k] = e
    return out


def resoudre(reglages, natif: bool) -> dict:
    """Table en vigueur : {id: {"clavier": str, "manette": str, "defaut_clavier", "defaut_manette"}}."""
    r = normaliser(reglages)
    out = {}
    for c in COMMANDES:
        dc, dm = c.defaut_clavier(natif), c.manette
        e = r.get(c.id, {})
        out[c.id] = {"clavier": e.get("clavier", dc), "manette": e.get("manette", dm),
                     "defaut_clavier": dc, "defaut_manette": dm}
    return out


def table_client(reglages, natif: bool) -> dict:
    """Ce que le client reçoit (fichier de contrôle) : {"format": 1, "natif": bool, "cmds": {id: [clavier, manette]}}."""
    t = resoudre(reglages, natif)
    return {"format": 1, "natif": bool(natif), "cmds": {k: [v["clavier"], v["manette"]] for k, v in t.items()}}


def lire_table_client(d) -> dict[str, tuple[str, str]] | None:
    """Table reçue du lanceur, vérifiée (None : absente ou illisible -> le client garde les défauts)."""
    if not isinstance(d, dict) or d.get("format") != 1 or not isinstance(d.get("cmds"), dict):
        return None
    out = {}
    for k, v in d["cmds"].items():
        if not (isinstance(k, str) and isinstance(v, list) and len(v) == 2):
            continue
        cl, ma = normaliser_touche(v[0]), normaliser_manette(v[1])
        out[k] = (cl or "", ma or "")
    return out


def table_defaut(natif: bool) -> dict[str, tuple[str, str]]:
    return {c.id: (c.defaut_clavier(natif), c.manette) for c in COMMANDES}


def conflits(table: dict, natif: bool, touches_jeu: dict | None = None) -> list[dict]:
    """Conflits d'une table résolue ({id: {"clavier", "manette"}} ou {id: (clavier, manette)}) :
    * deux commandes sur la même touche (mêmes modificateurs) ou la même combinaison ;
    * une combinaison incluse dans une autre (la petite partirait aussi) ;
    * portage natif : une touche identique à un raccourci du portage (``RACCOURCIS_PORTAGE``) ;
    * ``touches_jeu`` ({nom de touche: [actions de Link]}, onglet Link) : une touche sans Ctrl ni Alt que le jeu lit
      aussi (le jeu ne regarde pas les modificateurs).
    Liste de {"ids": [...], "genre": "clavier" | "manette" | "portage" | "jeu", "valeur": str, "avec": str}."""
    def val(v, i):
        return v[i] if isinstance(v, (list, tuple)) else v["clavier" if i == 0 else "manette"]
    out = []
    ids = [c.id for c in COMMANDES if c.id in table]
    vus: dict[str, str] = {}
    for i in ids:
        t = normaliser_touche(val(table[i], 0))
        if not t:
            continue
        if t in vus:
            out.append({"ids": [vus[t], i], "genre": "clavier", "valeur": t, "avec": ""})
        else:
            vus[t] = i
        if natif:
            for r, quoi in RACCOURCIS_PORTAGE:
                if r == t:
                    out.append({"ids": [i], "genre": "portage", "valeur": t, "avec": quoi})
        if touches_jeu:
            mods, cle = analyser_touche(t)
            if not mods & {"Ctrl", "Alt"} and touches_jeu.get(cle):
                out.append({"ids": [i], "genre": "jeu", "valeur": t, "avec": ", ".join(touches_jeu[cle])})
    combos = [(i, analyser_manette(val(table[i], 1)) or frozenset()) for i in ids]
    for a in range(len(combos)):
        for b in range(a + 1, len(combos)):
            (ia, ca), (ib, cb) = combos[a], combos[b]
            if ca and cb and (ca <= cb or cb <= ca):
                out.append({"ids": [ia, ib], "genre": "manette", "valeur": normaliser_manette("+".join(ca)) or "",
                            "avec": normaliser_manette("+".join(cb)) or ""})
    return out


class Lecteur:
    """Fronts montants des commandes au clavier : ``lire(vk) -> bool`` (code virtuel Windows, ou celui de
    ``TOUCHES`` sous Linux), ``vk_de(nom) -> int | None`` (conversion du nom de touche ; défaut : ``vk_windows``).
    Une commande part quand sa touche ET exactement ses modificateurs sont tenus (front montant)."""

    def __init__(self, lire, table: dict[str, tuple[str, str]], vk_de=None):
        self.lire = lire
        self.vk_de = vk_de or vk_windows
        self.avant: set[str] = set()
        self.regler(table)

    def regler(self, table: dict[str, tuple[str, str]]) -> None:
        self.table = dict(table)
        self.liaisons: list[tuple[str, int, frozenset]] = []
        for k, (cl, _ma) in self.table.items():
            a = analyser_touche(cl)
            if not a or not a[1]:
                continue
            vk = self.vk_de(a[1])
            if vk:
                self.liaisons.append((k, vk, a[0]))

    def tenues(self, actif: bool = True) -> set[str]:
        if not actif:
            return set()
        mods = {m for m, vk in VK_MODIFICATEURS.items() if self.lire(vk)} if self.liaisons else set()
        return {k for k, vk, m in self.liaisons if m == mods and self.lire(vk)}

    def lire_commandes(self, actif: bool = True) -> list[str]:
        try:
            bas = self.tenues(actif)
        except Exception:                # noqa: BLE001 : jamais d'arrêt du client pour des touches
            bas = set()
        nouveaux = bas - self.avant
        self.avant = bas
        return [k for k in self.table if k in nouveaux]       # ordre de la table


_cache_vk: dict[str, int] = {}


def vk_windows(nom: str) -> int | None:
    """Code virtuel Windows de la touche ``nom`` : par son scancode (touche physique, MapVirtualKeyW) quand il en a
    un, sinon le code fixe."""
    e = TOUCHES.get(nom)
    if e is None:
        return None
    vk, sc, _ks = e
    if sc is None:
        return vk
    if nom in _cache_vk:
        return _cache_vk[nom]
    v = vk
    try:
        import ctypes
        r = ctypes.windll.user32.MapVirtualKeyW(sc, 1)       # MAPVK_VSC_TO_VK
        if r:
            v = int(r)
    except (AttributeError, OSError):
        pass
    _cache_vk[nom] = v
    return v
