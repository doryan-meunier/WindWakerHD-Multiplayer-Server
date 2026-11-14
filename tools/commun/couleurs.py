"""Une couleur de tenue par joueur (docs/etudes/couleurs_link.md).

Trois étages, chacun utilisable sans le suivant :

1. Le choix. Chaque joueur demande une couleur de la palette (1 à 8) ou « automatique » (0) dans son
   ``hello`` (champ ``couleur``). Le serveur attribue la couleur EFFECTIVE (``attribuer``) : deux joueurs
   n'ont jamais la même. Règle : le premier arrivé garde la sienne ; le suivant qui demande une couleur
   déjà prise (ou « automatique ») reçoit celle de son numéro de joueur si elle est libre, sinon la
   première libre de la palette. La couleur d'un joueur ne change plus tant qu'il reste connecté.
2. L'affichage hors du jeu : pastille de couleur dans le lobby du lanceur (toujours, même avec un vieux
   pack).
3. La tenue en jeu : le pack « WWMP Mailbox » qui annonce le bit 12 de ses capacités multiplie les deux
   couleurs d'éclairage des matériaux de la tunique par trois facteurs (``Teintes``, écrits dans la zone
   ``wwmp_coul``). Sans ce bit : rien n'est écrit, tout le monde reste vert.

Compatibilité : un ancien client n'envoie pas ``couleur`` (le serveur lit « automatique ») et ignore le
champ qu'il reçoit ; un ancien serveur ne renvoie rien, le client retombe alors sur ``par_numero`` pour
tout le monde, lui compris (``effective``) : son choix n'a pas voyagé, tous les PC doivent voir la même chose.

Les facteurs sont des hypothèses de rendu (rien ne permet de voir l'image hors ligne) : ``--couleurs-table``
charge un fichier JSON qui les remplace sans toucher au code ni au pack (``charger_table``).
"""
from __future__ import annotations

import json
import math
import struct

AUTO = 0
N = 8
# code (protocole et fichiers : le NUMÉRO 1..8 voyage, pas le nom), nom français, nom anglais, couleur de
# la pastille du lanceur, facteurs R, G, B appliqués aux couleurs d'éclairage de la tunique
PALETTE = (
    ("vert", "Vert", "Green", "#3aa655", (1.0, 1.0, 1.0)),          # tenue d'origine : aucune teinte
    ("rouge", "Rouge", "Red", "#d64541", (2.2, 0.35, 0.35)),
    ("bleu", "Bleu", "Blue", "#3b7dd8", (0.35, 0.6, 2.4)),
    ("violet", "Violet", "Purple", "#8e5bd0", (1.6, 0.4, 2.2)),
    ("jaune", "Jaune", "Yellow", "#e6c833", (2.2, 1.3, 0.3)),
    ("orange", "Orange", "Orange", "#e8862e", (2.4, 0.7, 0.2)),
    ("turquoise", "Turquoise", "Teal", "#2fb8b0", (0.3, 1.2, 2.0)),
    ("sombre", "Sombre", "Dark", "#4a4a55", (0.35, 0.35, 0.35)),
)
CODES = tuple(p[0] for p in PALETTE)
# matériaux de la tenue dans le modèle « cl » de Link (24 matériaux ; relevé du fichier de modèle,
# docs/etudes/couleurs_link.md §3) : 0 torse, 2 bonnet, 23 manches et bas de la tunique
MASQUE_TENUE = (1 << 0) | (1 << 2) | (1 << 23)
# teinte « spectre » (mode Hanteurs : un Hanteur manifesté vu par un autre Hanteur) : la couleur du joueur sur
# TOUT le corps, pas seulement la tunique. 0 torse, 1 cheveux, 2 bonnet, 3 collant et bottes, 4 et 5 mains,
# 14 visage, 23 manches : 8 matériaux, le plus que le pack sache teindre pour un dessin (8 sauvegardes)
MASQUE_SPECTRE = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5) | (1 << 14) | (1 << 23)
N_MATERIAUX_MAX = 8
# le vert (facteurs 1, 1, 1 : tenue d'origine) ne distinguerait pas un spectre d'un Link : repli vert pâle
SPECTRE_VERT = (0.4, 1.6, 0.6)
FACTEUR_MAX = 4.0
BLOC_VIDE = bytes(16)


def normaliser(v) -> int:
    """Valeur reçue (réseau, fichier, ligne de commande) -> 0 (automatique) ou 1..8. Tout le reste : 0."""
    if isinstance(v, bool):
        return AUTO
    if isinstance(v, str):
        s = v.strip().lower()
        if s in CODES:
            return CODES.index(s) + 1
        # chiffres ASCII seulement, et courts : « ² » ou « ٣ » passent isdigit() mais font lever int() (ou
        # donnent un chiffre que personne n'a tapé), et int() refuse les nombres de plus de 4300 chiffres
        if len(s) <= 3 and s.isascii() and s.isdigit():
            v = int(s)
        else:
            return AUTO
    if isinstance(v, int) and 1 <= v <= N:
        return v
    return AUTO


def par_numero(pid) -> int:
    """Couleur « automatique » : celle du numéro de joueur (1 vert, 2 rouge, … 8 sombre, 9 vert…)."""
    try:
        return (int(pid) - 1) % N + 1
    except (TypeError, ValueError):
        return 1


def attribuer(voulue, pid, prises) -> int:
    """Couleur effective (1..8) d'un joueur qui arrive. ``prises`` : couleurs des joueurs déjà là.

    La couleur demandée si elle est libre ; sinon (ou en automatique) celle du numéro de joueur si elle est
    libre ; sinon la première libre en partant de celle-là. Palette pleine (plus de 8 joueurs : ne devrait
    pas arriver, le serveur en accepte 8) : celle du numéro, quitte à faire un doublon."""
    prises = {c for c in prises if isinstance(c, int)}
    voulue = normaliser(voulue)
    if voulue and voulue not in prises:
        return voulue
    depart = par_numero(pid)
    for k in range(N):
        c = (depart - 1 + k) % N + 1
        if c not in prises:
            return c
    return depart


def effective(pid, recue) -> int:
    """Côté client : couleur donnée par le serveur si elle est valide ; sinon (ancien serveur) celle du
    numéro de joueur, pour soi comme pour les autres : chaque PC calcule ainsi la même couleur pour le même
    joueur, et deux joueurs n'ont pas la même (jusqu'à 8)."""
    return normaliser(recue) or par_numero(pid)


def code(c) -> str:
    c = normaliser(c)
    return CODES[c - 1] if c else "auto"


def nom(c, langue: str = "fr") -> str:
    c = normaliser(c)
    if not c:
        return "Automatic" if langue == "en" else "Automatique"
    return PALETTE[c - 1][2 if langue == "en" else 1]


def pastille(c) -> str | None:
    """Couleur CSS de la pastille du lanceur ; None si la couleur est inconnue (ancien serveur)."""
    c = normaliser(c)
    return PALETTE[c - 1][3] if c else None


def palette_vue(langue: str = "fr") -> list[dict]:
    """Palette pour l'interface du lanceur : « automatique » puis les 8 couleurs."""
    return [{"n": 0, "code": "auto", "nom": nom(0, langue), "css": None}] + [
        {"n": i + 1, "code": p[0], "nom": nom(i + 1, langue), "css": p[3]} for i, p in enumerate(PALETTE)]


class ErreurTable(ValueError):
    pass


def _facteurs(v) -> tuple[float, float, float]:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise ErreurTable("une couleur = trois facteurs [R, V, B]")
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= FACTEUR_MAX:
            raise ErreurTable(f"facteur {x!r} hors de 0..{FACTEUR_MAX:g}")
        out.append(float(x))
    return tuple(out)


class Teintes:
    """Table couleur (1..8) -> bloc de 16 octets de la zone ``wwmp_coul`` : masque des matériaux (u32) et
    trois facteurs f32. Facteurs (1, 1, 1) ou couleur inconnue : bloc nul (masque 0 = le jeu ne fait rien)."""

    def __init__(self, masque: int = MASQUE_TENUE, facteurs: dict[int, tuple] | None = None,
                 masque_spectre: int = MASQUE_SPECTRE):
        for m in (masque, masque_spectre):
            if isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= 0xFFFFFFFF:
                raise ErreurTable("masque des matériaux : entier de 32 bits")
        if bin(masque_spectre).count("1") > N_MATERIAUX_MAX:
            raise ErreurTable(f"masque du spectre : {N_MATERIAUX_MAX} matériaux au plus (limite du pack)")
        self.masque = masque
        self.masque_spectre = masque_spectre
        self.facteurs = {i + 1: p[4] for i, p in enumerate(PALETTE)}
        for c, f in (facteurs or {}).items():
            c = normaliser(c)
            if not c:
                raise ErreurTable("couleur inconnue dans la table")
            self.facteurs[c] = _facteurs(f)

    def bloc(self, c) -> bytes:
        c = normaliser(c)
        if not c or not self.masque:
            return BLOC_VIDE
        f = self.facteurs[c]
        if f == (1.0, 1.0, 1.0):
            return BLOC_VIDE
        return struct.pack(">I3f", self.masque, *f)

    def bloc_spectre(self, c) -> bytes:
        """Teinte « spectre » d'un Hanteur manifesté : la couleur du joueur sur tout le corps
        (``masque_spectre``). Vert ou couleur inconnue : ``SPECTRE_VERT`` (jamais la tenue d'origine, sinon rien
        ne le distinguerait d'un Link). Facteurs toujours ramenés dans 0..``FACTEUR_MAX``. Masque 0 : bloc nul."""
        if not self.masque_spectre:
            return BLOC_VIDE
        c = normaliser(c)
        f = self.facteurs[c] if c else SPECTRE_VERT
        if tuple(f) == (1.0, 1.0, 1.0):
            f = SPECTRE_VERT
        f = [min(FACTEUR_MAX, max(0.0, float(x))) if math.isfinite(x) else 1.0 for x in f]
        return struct.pack(">I3f", self.masque_spectre, *f)


def charger_table(chemin: str | None) -> Teintes:
    """``--couleurs-table`` : JSON ``{"masque": 8388613, "couleurs": {"rouge": [2.2, 0.35, 0.35], …},
    "masque_spectre": 8405055}`` (toutes les clés sont facultatives ; clés des couleurs : code ou numéro ;
    ``masque_spectre`` : matériaux teints chez un Hanteur manifesté, 8 au plus, 0 = pas de teinte spectre).
    Lève ErreurTable."""
    if not chemin:
        return Teintes()
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise ErreurTable(f"{chemin} illisible : {e}") from None
    if not isinstance(d, dict):
        raise ErreurTable("la table doit être un objet JSON")
    coul = d.get("couleurs", {})
    if not isinstance(coul, dict):
        raise ErreurTable("« couleurs » doit être un objet")
    return Teintes(d.get("masque", MASQUE_TENUE), coul, d.get("masque_spectre", MASQUE_SPECTRE))


class Teinteur:
    """Écrit les blocs de teinte dans le pack, seulement quand ils changent.

    ``mb`` : ``wwlib.mailbox.Mailbox`` (ou un faux avec ``couleurs``, ``n_slots`` et ``set_teinte``). Sans
    la capacité du pack (``mb.couleurs`` faux), rien n'est jamais écrit. Une erreur d'écriture coupe le
    teinteur pour la session (``erreur``) : les couleurs sont un décor, elles ne doivent rien casser."""

    def __init__(self, mb, teintes: Teintes | None = None, soi: bool = True):
        self.mb = mb
        self.teintes = teintes or Teintes()
        self.soi = bool(soi)
        self.actif = bool(getattr(mb, "couleurs", False))
        self.erreur = ""
        self.ecrits: dict = {}          # cible (None = notre Link, 0..6 = emplacement) -> dernier bloc écrit
        self.n_emplacements = min(7, int(getattr(mb, "n_slots", 0) or 0))
        if self.actif:
            self.effacer(force=True)    # blocs laissés par une session interrompue

    def _poser(self, cible, bloc: bytes, force: bool = False) -> None:
        if not force and self.ecrits.get(cible, BLOC_VIDE) == bloc:
            return
        self.mb.set_teinte(cible, bloc)
        self.ecrits[cible] = bloc

    def appliquer(self, emplacements: dict[int, int], couleurs: dict[int, int], ma_couleur=None,
                  spectres=frozenset()) -> None:
        """``emplacements`` : joueur -> emplacement de son fantôme ; ``couleurs`` : joueur -> couleur
        effective ; ``ma_couleur`` : la nôtre (None ou 0 : notre Link reste tel quel) ; ``spectres`` : joueurs
        dessinés avec la teinte « spectre » (Hanteurs manifestés, mode Hanteurs)."""
        if not self.actif:
            return
        try:
            par_slot = {s: (self.teintes.bloc_spectre(couleurs.get(j)) if j in spectres
                            else self.teintes.bloc(couleurs.get(j)))
                        for j, s in emplacements.items()
                        if isinstance(s, int) and 0 <= s < self.n_emplacements}
            for s in range(self.n_emplacements):
                self._poser(s, par_slot.get(s, BLOC_VIDE))
            self._poser(None, self.teintes.bloc(ma_couleur) if self.soi else BLOC_VIDE)
        except Exception as e:
            self.erreur = f"{e.__class__.__name__}: {e}"
            self.actif = False

    def effacer(self, force: bool = False) -> None:
        """Tout remettre à zéro (arrêt du client, option coupée)."""
        if not bool(getattr(self.mb, "couleurs", False)):
            return
        try:
            for s in range(self.n_emplacements):
                self._poser(s, BLOC_VIDE, force)
            self._poser(None, BLOC_VIDE, force)
        except Exception as e:
            self.erreur = f"{e.__class__.__name__}: {e}"
            self.actif = False

    def statut(self) -> str:
        if self.erreur:
            return f"coupées ({self.erreur})"
        if not self.actif:
            return "pack sans couleurs (pastilles du lanceur seulement)"
        n = sum(1 for k, b in self.ecrits.items() if k is not None and b != BLOC_VIDE)
        soi = "notre Link teinté" if self.ecrits.get(None, BLOC_VIDE) != BLOC_VIDE else "notre Link d'origine"
        return f"{n} fantôme(s) teinté(s), {soi}"
