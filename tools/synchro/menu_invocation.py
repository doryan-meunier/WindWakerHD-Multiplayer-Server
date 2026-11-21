"""Mode Hanteurs : le MENU D'INVOCATION à la manette (mod natif 0.60.0, zone WWMPMNU1, natif/mod_wwmp/menu.c).

Ce que voit le Hanteur : Select (−, appui court : tenu, il ouvre le menu du portage) ouvre une bande en bas de
l'écran avec TOUS les monstres invocables de la partie ; le choisi est au milieu, ses voisins de chaque côté ; L et R
font défiler (de droite à gauche), A invoque le choisi et ferme le menu, B ou Select ferme. Un monstre pas encore vu
vivant dans cette stage est grisé (le serveur l'y refuse : il lui faut les paramètres d'un exemplaire vivant).
Tant que le menu est ouvert, seuls Select, L, R, A et B sont lus (et retirés au jeu par le mod) ; les autres
commandes des Hanteurs reviennent à sa fermeture. Aucune entrée/sortie ici (tests/test_menu_invocation.py).
"""
from __future__ import annotations

N = 12                  # monstres au plus dans le menu (MNU_N de boite.h)
NOM = 20                # octets d'un nom (Latin-1)
TITRE = 44
COMMANDE = "menu_invocation"
# combinaisons lues pendant que le menu est ouvert (une touche chacune, retirées au jeu)
TOUCHES_MENU = (("menu_invocation", "Minus"), ("menu_gauche", "L"), ("menu_droite", "R"), ("menu_ok", "A"),
                ("menu_fermer", "B"))
ACTIONS = frozenset(k for k, _b in TOUCHES_MENU)
PRISON = 0xFFFE         # (0.62.0) entrée « Prison » : A enferme les Link proches (commande « enfermer ») ; le mod la
                        # dessine avec l'image du paquet « prison.png » et sans coût (natif/mod_wwmp/menu.c MNU_PRISON)
NOM_PRISON = "Prison"


def latin1(s: str, n: int) -> bytes:
    """Texte borné en Latin-1 (caractères absents remplacés), au plus ``n`` octets."""
    return str(s).encode("latin-1", "replace")[:n]


class MenuInvocation:
    def __init__(self):
        self.ouvert = False
        self.choix = 0                  # index dans ``liste``
        self.ecrit = None               # dernier état écrit au mod (rien de réécrit s'il n'a pas changé)

    @staticmethod
    def liste(especes: dict, ici, prison=None) -> list[tuple[int, str, int, bool]]:
        """[(proc, nom, coût, invocable ici)] : les espèces de la partie, par coût puis nom, puis (``prison`` non
        None : l'enfermement est permis dans la partie ; vrai : possible maintenant) la Prison ; au plus N."""
        ici = set(ici or ())
        out = [(p, str(e.get("nom") or "?"), int(e.get("cout") or 0), p in ici) for p, e in especes.items()
               if p != PRISON]
        out.sort(key=lambda x: (x[2], x[1], x[0]))
        if prison is not None:
            out = out[:N - 1] + [(PRISON, NOM_PRISON, 0, bool(prison))]
        return out[:N]

    def action(self, a: str, especes: dict, ici, choisi=None, prison=None) -> int | None:
        """Une touche du menu. Rend le proc à invoquer (A sur un monstre invocable ici ; PRISON sur la Prison), sinon
        None. ``choisi`` : proc de l'espèce déjà choisie (F7), où le menu s'ouvre."""
        lst = self.liste(especes, ici, prison)
        if a == COMMANDE:
            if self.ouvert or not lst:
                self.ouvert = False
                return None
            self.ouvert = True
            procs = [x[0] for x in lst]
            self.choix = procs.index(choisi) if choisi in procs else next(
                (k for k, x in enumerate(lst) if x[3]), 0)
            return None
        if not self.ouvert:
            return None
        if not lst:
            self.ouvert = False
            return None
        self.choix = min(self.choix, len(lst) - 1)
        if a == "menu_gauche":
            self.choix = max(0, self.choix - 1)
        elif a == "menu_droite":
            self.choix = min(len(lst) - 1, self.choix + 1)
        elif a == "menu_fermer":
            self.ouvert = False
        elif a == "menu_ok":
            proc, _nom, _cout, dispo = lst[self.choix]
            self.ouvert = False
            return proc if dispo else None
        return None

    def fermer(self) -> None:
        self.ouvert = False

    def etat(self, especes: dict, ici, points: int, prison=None) -> dict:
        """Ce que le mod dessine : {ouvert, choix, titre, noms, couts, dispo, procs} (procs : portraits, 0.62.0)."""
        lst = self.liste(especes, ici, prison) if self.ouvert else []
        if not lst:
            return {"ouvert": 0, "choix": 0, "titre": b"", "noms": [], "couts": [], "dispo": 0, "procs": []}
        c = min(self.choix, len(lst) - 1)
        return {"ouvert": 1, "choix": c, "titre": latin1(f"{int(points)} points", TITRE),
                "noms": [latin1(x[1], NOM) for x in lst], "couts": [max(0, min(0xFFFF, x[2])) for x in lst],
                "dispo": sum(1 << k for k, x in enumerate(lst) if x[3]),
                "procs": [max(0, min(0xFFFF, int(x[0]))) for x in lst]}
