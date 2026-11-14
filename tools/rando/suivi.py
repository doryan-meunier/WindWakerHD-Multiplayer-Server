"""Randomizer, étape 4 : onglet « Indices » du lanceur (indices dévoilés + suivi des lieux, « traqueur intégré »).

Ce module ne touche NI au jeu NI au réseau : il met en forme, pour la page du lanceur, la section « rando » que le
serveur publie dans son lobby (`Server.rando_lobby`) :

- les indices DÉJÀ dévoilés (le serveur les dévoile au fil des coffres ouverts par l'équipe) ;
- la liste des lieux par région, cochés d'eux-mêmes quand un joueur de l'équipe a ouvert le coffre (registre des
  coffres partagés du serveur), avec l'objet qu'il contenait.

Ce que le suivi sait et ne sait pas :
- COFFRES (154 lieux de la logique) : suivis, mélangés ou non (un coffre non mélangé est marqué « d'origine ») ;
- CADEAUX DE PERSONNAGES, LETTRES, ÉTALS, ENCHÈRES et COFFRES EN CONFLIT mélangés : cochés d'après l'état du
  TRACKEUR quand le serveur en tient un (``faits`` : lieux faits, automatiquement ou à la main, dans l'onglet
  « Trackeur ») ; sans trackeur (serveur ancien, --no-trackeur), listés mais NON cochés (« cadeau, non suivi ») ;
- tous les autres lieux du jeu gardent leur objet d'origine : ils ne sont pas listés.

Le spoiler n'est jamais lu ici : la page ne montre que ce que l'équipe a déjà trouvé.
"""
from __future__ import annotations

import threading

from rando import noyau as rnd

LIEUX_MAX = 2048           # coffres ouverts lus au plus dans le lobby (le serveur en garde 1024 par groupe)
INDICES_LUS_MAX = 64
_VERROU = threading.Lock()
_CACHE: dict = {}


def _entier(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _pris(detail: dict) -> dict:
    """{(index de sauvegarde, numéro de coffre): objet trouvé ou None} depuis le lobby (données du réseau : tout
    est revalidé, ce qui est mal formé est ignoré)."""
    out = {}
    brut = detail.get("pris")
    if not isinstance(brut, list):
        return out
    for e in brut[:LIEUX_MAX]:
        if isinstance(e, list) and len(e) == 3 and all(_entier(x) for x in e) \
                and 0 <= e[0] <= 0xFF and 0 <= e[1] <= 0xFF:
            out[(e[0], e[1])] = e[2] if 0 <= e[2] <= 0xFF else None
    return out


def _indices(detail: dict, langue: str) -> list[str]:
    out = []
    brut = detail.get("indices")
    if not isinstance(brut, list):
        return out
    for i in brut[:INDICES_LUS_MAX]:
        if isinstance(i, dict):
            t = i.get("en" if langue == "en" else "fr") or i.get("fr")
            if isinstance(t, str) and t:
                out.append(t[:300])
    return out


def _plan(detail: dict):
    """(logique, {index de lieu mélangé}, exact) : ``exact`` est faux si la table vient d'autres données de logique
    ou d'options que ces outils ne comprennent pas (on ne sait alors pas dire quel coffre est mélangé)."""
    from rando import moteur as rm
    lg = rm.logique()
    if detail.get("mode") != "complet":
        return lg, None, True
    if not lg.memes_donnees(detail.get("donnees"), detail.get("options")):
        return lg, None, False
    try:
        opt = rm.normaliser_options(detail.get("options"), lg)
    except rnd.ErreurRando:
        return lg, None, False
    return lg, set(rm.Plan(lg, opt).mobiles), True


def vue(detail, langue: str = "fr", faits=None) -> dict | None:
    """Données de l'onglet « Indices » pour la section « rando » du lobby (None : partie sans randomizer).

    Résultat : mode, empreinte, préréglage et chaîne de réglages de la partie, indices dévoilés (dans la langue),
    régions [{nom, pris, total, lieux: [{nom, pris, objet, etat}]}] où ``etat`` vaut « melange », « origine »,
    « cadeau » (mélangé, non suivi) ou « inconnu » (coffre ouvert que la logique ne connaît pas).
    ``faits`` : index des lieux faits d'après le trackeur de la même partie (None : pas de trackeur). Un lieu mélangé
    qui n'est pas un coffre ordinaire est alors suivi (« melange », coché s'il est fait).
    """
    if not isinstance(detail, dict) or not isinstance(detail.get("emp"), str):
        return None
    langue = "en" if langue == "en" else "fr"
    pris = _pris(detail)
    indices = _indices(detail, langue)
    faits = frozenset(i for i in faits if isinstance(i, int)) if isinstance(faits, (set, frozenset, list, tuple, dict)) \
        else None
    cle = (detail.get("emp"), detail.get("mode"), langue, len(indices), tuple(sorted(pris.items(), key=str)), faits)
    with _VERROU:
        if _CACHE.get("cle") == cle:
            return _CACHE["vue"]
    try:
        v = _construire(detail, langue, pris, indices, faits)
    except (rnd.ErreurRando, KeyError, TypeError, ValueError) as ex:
        # données de logique illisibles sur ce PC : la page garde les indices, sans la liste des lieux
        v = _entete(detail, indices)
        v.update({"regions": [], "pris": len(pris), "total": 0, "exact": False, "erreur": str(ex)[:200]})
    with _VERROU:
        _CACHE.update({"cle": cle, "vue": v})
    return v


def _entete(detail: dict, indices: list[str]) -> dict:
    total = detail.get("indices_total")
    mode = "complet" if detail.get("mode") == "complet" else "sur"
    out = {"mode": mode, "emp": str(detail.get("emp"))[:16], "indices": indices,
           "indices_total": total if _entier(total) and 0 <= total <= 4 * 16 else len(indices),
           "prereglage": "", "chaine": "", "cadeaux": 0}
    if "mode" not in detail:                     # serveur d'avant l'étape 4 : empreinte seule
        out["ancien"] = True
    return out


def _construire(detail: dict, langue: str, pris: dict, indices: list[str], faits=None) -> dict:
    from rando import moteur as rm
    lg, mobiles, exact = _plan(detail)
    v = _entete(detail, indices)
    if v["mode"] == "complet" and exact:
        opt = rm.normaliser_options(detail.get("options"), lg)
        v["prereglage"] = rm.prereglage_de("complet", opt, lg)
        v["chaine"] = rm.chaine_reglages("complet", opt, lg)
    elif v["mode"] == "sur":
        v["prereglage"] = "decouverte"
        v["chaine"] = rm.chaine_reglages("sur")
    regions: dict = {}

    def ajouter(region: str, lieu: dict) -> None:
        regions.setdefault(region, []).append(lieu)

    connus = set()
    for i, e in enumerate(lg.lieux):
        melange = mobiles is not None and i in mobiles
        if not e.get("applicable") and not melange:   # un lieu « donnée » (lettre, étal…) n'est listé que mélangé
            continue
        if e.get("histoire"):
            continue           # case de départ vidée (objets de départ mélangés) : rien à fouiller, rien de donné
        nom = e["nom"] if langue == "en" else e["nom_fr"]
        region, _sep, court = nom.partition(" - ")
        if e.get("applicable") and e["typ"] == rm.TYPE_COFFRE:
            k = (e["idx"], e["tbox"])
            connus.add(k)
            objet = pris.get(k)
            ajouter(region, {"nom": court or nom, "pris": k in pris,
                             "objet": rnd.nom_objet(objet, langue) if objet is not None else None,
                             "etat": "melange" if melange else ("origine" if mobiles is not None else "inconnu")})
        elif melange and faits is not None:
            # cadeau, lettre, étal, enchère ou coffre en conflit mélangé, suivi par le trackeur de la partie
            ajouter(region, {"nom": court or nom, "pris": i in faits, "objet": None, "etat": "melange"})
        elif melange:                # cadeau de personnage, lettre, étal ou enchère mélangé : listé, non suivi
            v["cadeaux"] += 1
            ajouter(region, {"nom": court or nom, "pris": None, "objet": None, "etat": "cadeau"})
    autres = sorted(k for k in pris if k not in connus)
    for k in autres:
        objet = pris[k]
        ajouter("Other chests" if langue == "en" else "Autres coffres",
                {"nom": (f"chest {k[1]} (save index {k[0]})" if langue == "en"
                         else f"coffre {k[1]} (index de sauvegarde {k[0]})"), "pris": True,
                 "objet": rnd.nom_objet(objet, langue) if objet is not None else None, "etat": "hors"})
    v["regions"] = [{"nom": r, "lieux": sorted(l, key=lambda x: x["nom"]),
                     "pris": sum(1 for x in l if x["pris"]), "total": sum(1 for x in l if x["pris"] is not None)}
                    for r, l in sorted(regions.items())]
    v["pris"] = sum(r["pris"] for r in v["regions"])
    v["total"] = sum(r["total"] for r in v["regions"])
    v["exact"] = exact
    return v
