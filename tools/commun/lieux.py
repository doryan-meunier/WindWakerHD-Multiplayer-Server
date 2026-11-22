"""Noms français des lieux du jeu, pour les textes montrés aux joueurs (journal de la partie, client/journal.py).

``ILES`` : les 49 carrés de la mer dans l'ordre des salles de la stage « sea » (salle 1 en haut à gauche, 7 par
rangée ; même ordre que ``rando/lot7.ILES`` et que la grille du trackeur, lanceur/ui/trackeur.js). ``STAGES`` : les
stages connues (donjons : mêmes listes que serveur/medailles.py). Une stage inconnue n'a pas de nom : le texte se passe
alors du lieu plutôt que de montrer un nom interne.
"""
from __future__ import annotations

ILES = (
    "Sect. Forteresse", "Île Étoilée", "Fées Nord", "Île du Vent", "Croissant", "Ursa Major", "Trois Tours",
    "Face Quatre", "Île Géminée", "Binocles", "Mercantîle", "Pas de Chat", "Île du Dragon", "Tour de l'Envol",
    "Fées Ouest", "Rocaille", "Île de Tingle", "Triangle Nord", "Fées Est", "Mont. de Feu", "Orion",
    "Face Trois", "Île du Poisson", "Face Une", "Face Six", "Sect. Tour des Dieux", "Triangle Est", "Fées Épineux",
    "Serrure de Pierre", "Canonnière", "Tête de Pierre", "Triangle Sud", "Île de Qui-ça", "Île de la Bombe", "Nichoirs",
    "Losange", "Face Cinq", "Île du Requin", "Fées Sud", "Île Gelée", "Île aux Forêts", "Saut de Roc",
    "Fer à Cheval", "Île de l'Aurore", "Île de la Terre", "Face Deux", "Kif Kif", "Batodrome", "Cassiopée",
)
STAGES_MER = ("sea", "sea_T")

_GROUPES = (
    ("Forteresse Maudite", ("MajyuE", "Majroom", "majroom", "ma2room", "ma3room", "Mjtower", "M2tower")),
    ("Caverne du Dragon", ("M_NewD2", "M_DragB")),
    ("Bois Défendus", ("kindan", "kinBOSS")),
    ("Tour des Dieux", ("Siren", "SirenB")),
    ("Temple de la Terre", ("M_Dai", "M_DaiB")),
    ("Temple du Vent", ("kaze", "kazeB")),
    ("Tour de Ganon", ("GanonA", "GanonB", "GanonC", "GanonD", "GanonE", "GanonJ", "GanonK", "GanonL", "GanonM",
                       "GanonN", "GTower", "Xboss0", "Xboss1", "Xboss2", "Xboss3")),
    ("Château sous la mer", ("Hyrule", "Hyroom")),
    ("Chez Link", ("LinkRM",)),
    ("Navire", ("Kaisen",)),
)
STAGES = {s: nom for nom, stages in _GROUPES for s in stages}


def ile(room) -> str:
    """Nom du carré de la mer ``room`` (1..49), "" sinon."""
    if isinstance(room, int) and not isinstance(room, bool) and 1 <= room <= len(ILES):
        return ILES[room - 1]
    return ""


def nom(stage, room=None) -> str:
    """Nom du lieu (carré de la mer, donjon, lieu connu), "" s'il est inconnu."""
    if not isinstance(stage, str) or not stage:
        return ""
    if stage in STAGES_MER:
        return ile(room)
    return STAGES.get(stage, "")
