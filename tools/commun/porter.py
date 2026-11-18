"""Porter un coéquipier, lot P3 « réseau » (docs/etudes/porter_coequipier.md §5) : ce que le serveur
(serveur/porter.py) et le client (client/porter.py) partagent. Aucune entrée/sortie.

Le totem est réparti, un seul Link par jeu :

* chez le PORTEUR, le jeu soulève, porte, pose et lance le FANTÔME du porté (mod natif 0.38.0+, zone WWMPPOR1 :
  emplacements « portables », masque des fantômes tenus, o mesuré, fins, refus) ;
* chez le PORTÉ, son vrai Link est tenu sur les mains du fantôme du porteur (mod natif 0.41.0+, zone WWMPPRT1,
  CALL 22 : sol factice, position imposée, stick coupé hors visée, L3 pour descendre).

Le serveur arbitre (premier « prise » reçu gagne) et relaie ; il ne simule rien.

Messages ``porter`` (champ ``ev``) :

* porteur -> serveur : ``prise {cible, o}`` (le jeu tient le fantôme de la cible), ``o {o}`` (o stable mesuré),
  ``fin {raison}`` (le jeu ne le tient plus : posé, lancé, autre, absent, retiré) ;
* porté -> serveur : ``descend {raison}`` (L3, dégâts, grappin, chute… : le mod l'a libéré) ;
* serveur -> porteur : ``ok {cible}``, ``refus {cible, raison}`` (le client lève le refus de l'emplacement : le jeu
  lâche le fantôme), ``lacher {cible, raison}`` (le porté est descendu ou parti : même chose) ;
* serveur -> porté : ``porte {par, o}`` (monter), ``o {par, o}``, ``fin {par, raison}`` (descendre).

Dans le ``state`` : ``prtbl`` = 1 quand ce Link peut être porté (règle et mod là, au sol, ni bateau, ni nage, ni
cinématique, ni porteur, ni porté) ; le porteur ne rend soulevables que les fantômes ``prtbl`` de sa salle.
"""
from __future__ import annotations

import math

FEAT = "porter"             # hello : ce PC sait porter et être porté (mod natif 0.41.0 ou plus)
VIE = 60                    # images armées d'avance chez le porté (2 s) : le mod lâche seul sans entretien
ENTRETIEN = 0.5             # s entre deux CALL 22 d'entretien
O_DEFAUT = (0.0, 108.0, 16.0)   # o mesuré en jeu (P1, 2026-11-16) : 108 u au-dessus des pieds du porteur, 16 devant
O_MAX = 300.0               # borne du mod (±300)
O_SEUIL = 5.0               # u : un o stable plus loin que ça du dernier envoyé est renvoyé
REFUS_DUREE = 1.5           # s : refus levé chez le porteur après un refus, une descente ou un départ
ARMER_DELAI = 3.0           # s : le porté n'arrive pas à monter (fantôme du porteur absent, refus du mod) : descend
PORTEE = 400.0              # u : le porteur et la cible à moins de ça (horizontal) au moment de la prise
PROCHE = 300.0              # u : seul un fantôme à moins de ça de notre Link est rendu soulevable (porteur)
LIBRE = 2.0                 # s : notre Link libre (hors cinématique) depuis au moins ça avant tout « soulevable » ou
                            # « portable » : une cinématique qui commence avec un fantôme marqué a planté le jeu au
                            # banc (OSPanic d_event_data.cpp « demo_data », 2026-11-18, intro d'une partie neuve)
ETAT_AGE = 3.0              # s : position plus vieille : inconnue
FINS = {1: "posé", 2: "lancé", 3: "autre", 4: "absent", 5: "retiré"}   # raisons du mod (porteur, POR_FINS)


def pid_ok(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def o_valide(o) -> list[float] | None:
    """o (x local, hauteur, devant) borné comme le mod ; None s'il est illisible ou hors bornes."""
    if not isinstance(o, (list, tuple)) or len(o) != 3:
        return None
    out = []
    for v in o:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) >= O_MAX:
            return None
        out.append(round(float(v), 1))
    return out


def raison_txt(r, n: int = 80) -> str:
    return str(r)[:n] if r is not None else ""


def dist_xz(a, b) -> float | None:
    try:
        return math.hypot(float(a[0]) - float(b[0]), float(a[2]) - float(b[2]))
    except (TypeError, ValueError, IndexError):
        return None
