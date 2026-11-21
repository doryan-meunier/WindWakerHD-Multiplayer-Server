"""Tenues à pouvoir (Triforce Heroes), partie B : les deux FUSIONS (attaques à deux) ; ce que le serveur
(serveur/fusions.py), le client (client/fusions.py) et le lanceur partagent. Aucune entrée/sortie ;
docs/etudes/autres_zelda.md §6, docs/dev/PORTAGE_NATIF.md §3.51.

Décisions de l'utilisateur (2026-11-17) :

* **Fusion tournoyante** (sombre + turquoise) : les deux chargent l'attaque tournoyante (épée maintenue) et se
  collent (``DISTANCE``, 2 m) : ils fusionnent en UN Link contrôlé à deux pendant ``DUREE`` (30 s). Le premier qui a
  chargé DÉPLACE Link (stick, roulade, saut) ; l'autre ATTAQUE (épée, gadgets) et tient la caméra. x3 sur TOUS les
  coups.
* **Méga bombe** (orange + violet) : les deux tiennent une bombe allumée ; juste avant l'explosion (bombe tenue depuis
  ``BOMBE_MURE``), les deux la lancent à ``ECART_LANCERS`` (0,5 s) au plus : même fusion (le premier qui lance
  déplace), qui commence par une grosse explosion autour d'eux (``anneau``) avec la lumière « apparition » ; x3 sur les
  objets qui consomment de la magie (flèches de feu, de glace, de lumière : ``OBJETS_MAGIE``).
* Couleurs : bonnet à la couleur de celui qui déplace, tunique à la couleur de celui qui attaque (les bottes ne sont
  pas teintables : docs/dev/PORTAGE_NATIF.md §3.51).
* Seulement entre les deux membres du duo, en jeu, même salle ; jamais pendant une cinématique, au menu, porté ou
  porteur, en course, en mode Hanteurs, ni avec le robot. Fin : 30 s, départ, changement d'étape ou de salle, mort,
  cinématique ; les deux Links se séparent à la place du porteur (jamais dans un mur).

VRAIE fusion (2026-11-17, mod natif 0.50.0, docs/dev/PORTAGE_NATIF.md §3.54) : UN SEUL Link, celui du PILOTE (celui
qui déplace). Les entrées de l'ATTAQUANT (``BOUTONS_ATTAQUE`` : B l'épée, X, Y, R les trois objets ; stick droit : la
caméra ; stick gauche seulement pendant les visées) sont lues sur son PC à ``ENTREES_HZ`` (flux dédié « fen », envoyé
au changement et rappelé toutes les ``rappel_s``), relayées par le serveur au pilote et injectées dans sa manette par le
mod ; le mod les tient jusqu'à l'état suivant et relâche tout après ``PEREMPTION_MS`` sans nouvelles. Répartition :

* PILOTE : stick gauche (marcher, courir), A (roulade, saut, actions : parler, ouvrir, soulever, grimper), L / ZL
  (verrouillage, qui change ce que fait le stick : pas de côté, salto), ZR (bouclier, s'accroupir, ramper), la croix,
  +, -, L3 / R3 ; ses B, X, Y, R et son stick droit sont retirés au jeu.
* ATTAQUANT : B, X, Y, R et le stick droit (+ le stick gauche pendant arc, grappin, boomerang, corde, vue subjective,
  longue-vue : c'est lui qui vise). Chez lui, son Link est caché, porté sur le fantôme du pilote (CALL 22) et ne reçoit
  rien (sauf +, - et son stick droit : sa propre caméra, qui suit le fantôme du pilote).

Pourquoi : tout ce qui place Link (où, dans quel sens, à quelle posture) reste à celui qui le voit en direct, sans
retard ; tout ce qui FRAPPE est à l'attaquant, si bien que le x3 suit exactement ses boutons.
"""
from __future__ import annotations

import math

from commun import couleurs

FEAT = "fusions2"           # hello : ce PC sait fusionner en VRAIE fusion (mod natif 0.50.0 ; « fusions » : 0.47.0,
                            # jamais apparié avec celui-ci)
DUREE = 30.0                # s
DISTANCE = 200.0            # u (2 m) : collés
CHARGE_MIN = 1.0            # s : B tenu sans rien porter = tournoyante chargée [HYPO : la charge du jeu dure moins]
BOMBE_MURE = 2.5            # s : bombe tenue depuis au moins ce temps au lancer (« juste avant l'explosion ») [HYPO :
                            # mèche de 5 s, comme le minuteur de Bomb2, 150 images]
ECART_LANCERS = 0.5         # s : les deux lancers à moins de ce temps l'un de l'autre
ECART_RELACHE = 0.5         # s (2026-11-19) : tournoyante : les deux relâchent B à moins de ça l'un de l'autre, sinon
                            # ils sont étourdis (l'étourdissement de fin de l'Attaque Tourbillon)
ARME_MAX = 30.0             # s : une paire armée qui ne relâche pas est oubliée
REPOS = 10.0                # s : pas de nouvelle fusion pour ce duo juste après la fin
ETAT_AGE = 5.0              # s : état plus vieux : le joueur n'est plus « en jeu » pour une fusion
FACTEUR = 24                # x3 (huitièmes)
TOURNOYANTE, MEGA_BOMBE = 1, 2
GENRES = {TOURNOYANTE: "tournoyante", MEGA_BOMBE: "méga bombe"}
NOMS_GENRES_EN = {TOURNOYANTE: "spin fusion", MEGA_BOMBE: "mega bomb"}
DEPLACE, ATTAQUE = 1, 2
ROLES = {DEPLACE: "déplace", ATTAQUE: "attaque"}
DUO_GENRE = {frozenset(("sombre", "turquoise")): TOURNOYANTE, frozenset(("orange", "violet")): MEGA_BOMBE}
OBJETS_MAGIE = ("flèches de feu", "flèches de glace", "flèches de lumière")
# manette (mod : FUS_BOUTONS_PERMIS = B, X, Y, R, ZR ; FUS_STICK_* ; FEN_BOUTONS = B, X, Y, R)
BOUTONS_PORTEUR = 0x4000 | 0x2000 | 0x1000 | 0x0010     # B, X, Y, R : l'épée et les trois gadgets
BOUTONS_ATTAQUE = BOUTONS_PORTEUR                        # ceux de l'attaquant, injectés chez le pilote
STICK_G, STICK_D = 0x1, 0x2
# flux des entrées de l'attaquant (« fen ») : options du serveur --fusion-entrees-hz, --fusion-peremption-ms
ENTREES_HZ = 120            # relevés par seconde de ses entrées sur son PC (le jeu les lit 30 fois par seconde)
ENTREES_HZ_MIN, ENTREES_HZ_MAX = 30, 500
PEREMPTION_MS = 250         # ms sans état nouveau : le mod du pilote relâche tout
PEREMPTION_MS_MIN, PEREMPTION_MS_MAX = 70, 2000
LECTURE_MS = 1000.0 / 30.0  # une lecture de la manette par le jeu
RAPPEL_MAX = 0.1            # s : état inchangé renvoyé au moins aussi souvent (et 2,5 fois par péremption)
FEN_MAX_PAR_S = 300         # serveur : états relayés au plus par seconde et par attaquant (au-delà : jetés)
# méga bombe : explosions (Bomb2 « explose tout de suite », commande 4) au centre et en anneau
ANNEAU_N = 6
ANNEAU_RAYON = 250.0        # u : souffle de Bomb2 200 u : de 50 à 450 u autour du centre
BOUCLIER = 45               # images (1,5 s) d'invincibilité de nos Links pendant le souffle
RAISONS_FIN = {"duree": "30 s écoulées", "depart": "un joueur est parti", "zone": "étape ou salle changée",
               "mort": "un Link est mort", "cinematique": "cinématique", "l3": "L3 : l'attaquant se détache",
               "regle": "règle coupée", "port": "le Link de l'attaquant ne tient plus sur le pilote (ou mod < 0.50.0)"}


def borne_hz(v) -> int:
    """--fusion-entrees-hz : entier borné (hors bornes ou illisible : ENTREES_HZ)."""
    return v if isinstance(v, int) and not isinstance(v, bool) and ENTREES_HZ_MIN <= v <= ENTREES_HZ_MAX else ENTREES_HZ


def borne_peremption(v) -> int:
    """--fusion-peremption-ms : entier borné (hors bornes ou illisible : PEREMPTION_MS)."""
    return v if isinstance(v, int) and not isinstance(v, bool) and PEREMPTION_MS_MIN <= v <= PEREMPTION_MS_MAX \
        else PEREMPTION_MS


def peremption_lectures(ms) -> int:
    """Péremption en lectures de la manette (ce que le mod compte) : arrondie au-dessus, bornée 2..60."""
    n = math.ceil(borne_peremption(ms) / LECTURE_MS - 1e-9)
    return max(2, min(60, n))


def rappel_s(peremption_ms) -> float:
    """Écart maximal entre deux envois de l'attaquant quand rien ne change : RAPPEL_MAX, et au moins 2,5 envois par
    péremption (un envoi perdu ou en retard ne la déclenche pas)."""
    return min(RAPPEL_MAX, borne_peremption(peremption_ms) / 2500.0)


def _stick(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if not math.isfinite(f):
        return None
    return max(-1.0, min(1.0, round(f, 3)))


def entrees(msg) -> dict | None:
    """Un état « fen » lisible : {f (fusion), n (numéro), b (boutons bornés à BOUTONS_ATTAQUE), s [lx, ly, rx, ry],
    a (retard local de l'attaquant, ms) , r (son aller-retour, ms)} ; None sinon."""
    if not isinstance(msg, dict):
        return None
    f, n, b, s = msg.get("f"), msg.get("n"), msg.get("b"), msg.get("s")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (f, n, b)) or not 0 <= n < 1 << 32:
        return None
    if not isinstance(s, list) or len(s) != 4:
        return None
    st = [_stick(v) for v in s]
    if any(v is None for v in st):
        return None
    out = {"f": f, "n": n, "b": b & BOUTONS_ATTAQUE, "s": st}
    for k in ("a", "r"):
        v = msg.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v < 10000:
            out[k] = round(float(v), 1)
    return out


def message_entrees(fusion: int, n: int, boutons: int, lx, ly, rx, ry, a=None, r=None) -> dict:
    """L'état « fen » que l'attaquant envoie (petit : ~80 octets)."""
    m = {"type": "fen", "f": int(fusion), "n": int(n) & 0xFFFFFFFF, "b": int(boutons) & BOUTONS_ATTAQUE,
         "s": [_stick(v) or 0.0 for v in (lx, ly, rx, ry)]}
    if a is not None:
        m["a"] = round(float(a), 1)
    if r is not None:
        m["r"] = round(float(r), 1)
    return m


def retard(mesures: dict) -> dict:
    """Retard des entrées de l'attaquant jusqu'au Link du pilote, par étape (ms) : ``lecture_jeu`` (le jeu de
    l'attaquant lit sa manette 30 fois par seconde : en moyenne une demi-lecture avant la publication),
    ``releve`` (demi-période du relevé à ENTREES_HZ), ``envoi`` (relevé -> écrit sur la connexion, mesuré),
    ``reseau`` (aller simple attaquant -> serveur -> pilote : moitié de chacun des deux allers-retours mesurés),
    ``application`` (réception -> écrit dans le jeu du pilote, mesuré), ``prise`` (demi-lecture : le jeu du pilote prend
    l'état à sa prochaine lecture) ; ``hors_reseau`` et ``total``. Les absents comptent 0 (``reseau`` : None si un
    aller-retour manque)."""
    hz = mesures.get("hz") or ENTREES_HZ
    out = {"lecture_jeu": LECTURE_MS / 2.0, "releve": 500.0 / hz, "envoi": float(mesures.get("envoi") or 0.0),
           "application": float(mesures.get("application") or 0.0), "prise": LECTURE_MS / 2.0}
    ra, rp = mesures.get("rtt_attaquant"), mesures.get("rtt_pilote")
    out["reseau"] = (ra + rp) / 2.0 if ra is not None and rp is not None else None
    out["hors_reseau"] = sum(out[k] for k in ("lecture_jeu", "releve", "envoi", "application", "prise"))
    out["total"] = out["hors_reseau"] + (out["reseau"] or 0.0)
    return {k: (round(v, 1) if v is not None else None) for k, v in out.items()}


def texte_retard(r: dict) -> str:
    net = f"{r['reseau']:.0f} ms" if r.get("reseau") is not None else "inconnu"
    return (f"~{r['total']:.0f} ms (hors réseau {r['hors_reseau']:.0f} ms : lecture du jeu {r['lecture_jeu']:.0f}, "
            f"relevé {r['releve']:.1f}, envoi {r['envoi']:.1f}, application {r['application']:.1f}, prise par le jeu "
            f"{r['prise']:.0f} ; réseau {net})")


def code(c) -> str | None:
    n = couleurs.normaliser(c)
    return couleurs.CODES[n - 1] if n else None


def genre_de(c1, c2) -> int:
    """Fusion possible entre ces deux couleurs (1..8 ou codes) : TOURNOYANTE, MEGA_BOMBE ou 0."""
    a, b = code(c1), code(c2)
    if not a or not b or a == b:
        return 0
    return DUO_GENRE.get(frozenset((a, b)), 0)


def couleurs_du_genre(genre: int) -> tuple[int, int]:
    """Les deux couleurs (numéros) d'un genre de fusion."""
    for paire, g in DUO_GENRE.items():
        if g == genre:
            a, b = sorted(paire)
            return couleurs.CODES.index(a) + 1, couleurs.CODES.index(b) + 1
    return 0, 0


def facteurs(genre: int) -> tuple[int, int]:
    """(tous les coups, objets à magie) en huitièmes : tournoyante (24, 8), méga bombe (8, 24), sinon (8, 8)."""
    if genre == TOURNOYANTE:
        return FACTEUR, 8
    if genre == MEGA_BOMBE:
        return 8, FACTEUR
    return 8, 8


def valid_tenf(v) -> list[int] | None:
    """Champ « tenf » d'un coup reçu (facteurs de fusion de l'attaquant) : deux entiers 8..24, sinon None."""
    if not isinstance(v, list) or len(v) != 2:
        return None
    if not all(isinstance(x, int) and not isinstance(x, bool) and 8 <= x <= 24 for x in v):
        return None
    return None if v == [8, 8] else list(v)


def pos3(v):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    return p if all(math.isfinite(x) and abs(x) < 1e6 for x in p) else None


def colles(a, b, distance: float = DISTANCE) -> bool:
    """Positions finies à moins de ``distance`` (3D)."""
    pa, pb = pos3(a), pos3(b)
    return pa is not None and pb is not None and math.dist(pa, pb) <= distance


def anneau(centre, n: int = ANNEAU_N, rayon: float = ANNEAU_RAYON) -> list[list[float]]:
    """Explosions de la méga bombe : le centre, puis ``n`` points sur un cercle horizontal de ``rayon``."""
    c = pos3(centre)
    if c is None:
        return []
    out = [[round(c[0], 2), round(c[1], 2), round(c[2], 2)]]
    for k in range(max(0, int(n))):
        a = 2.0 * math.pi * k / n
        out.append([round(c[0] + rayon * math.sin(a), 2), round(c[1], 2), round(c[2] + rayon * math.cos(a), 2)])
    return out


def texte(genre: int, role: int, langue: str = "fr") -> str:
    en = langue == "en"
    g = (NOMS_GENRES_EN if en else GENRES).get(genre, "?")
    if en:
        r = {DEPLACE: "you MOVE (left stick, A roll, L/ZL target, ZR shield)",
             ATTAQUE: "you ATTACK (B sword, X Y R items, right stick camera, aiming)"}.get(role, "?")
        bonus = "x3 on every hit" if genre == TOURNOYANTE else "x3 with magic items (fire, ice, light arrows)"
    else:
        r = {DEPLACE: "tu DÉPLACES (stick gauche, A roulade et actions, L/ZL visée, ZR bouclier)",
             ATTAQUE: "tu ATTAQUES (B épée, X Y R objets, stick droit caméra, visées)"}.get(role, "?")
        bonus = "x3 sur tous les coups" if genre == TOURNOYANTE else "x3 avec les objets à magie (flèches de feu, de " \
                                                                     "glace, de lumière)"
    return f"{g} : {r} ; {bonus}"
