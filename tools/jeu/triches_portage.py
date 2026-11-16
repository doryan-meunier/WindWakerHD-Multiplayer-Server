"""Triches du portage natif AVANT le lancement : ce que le lanceur voit (lecture seule) et ce que « Les désactiver »
change, jeu FERMÉ (étape « le jeu (portage natif) » de Jouer, lanceur/jouer.py ; docs/dev/PORTAGE_NATIF.md §8.10).

Seules deux choses survivent d'un démarrage du jeu à l'autre (sources v0.5.1, voir ``commun/triches.py``) :

* l'option « Run/swim speed » (mod intégré ``move-speed``) : ``settings.ini`` ``mod.move-speed.enabled`` puis,
  s'il y a un booléen, ``profiles.json`` ``profiles[<actif>].builtins["move-speed"]`` (appliqué après) ; la
  variable ``WWHD_MOD_MOVE_SPEED`` l'emporte sur les deux (présente : ``atoi`` != 0 décide) ;
* les variables d'environnement des triches (``WWHD_CHEAT_INFINITE``, ``WWHD_CHEAT``, ``WWHD_STORY_ITEM``) que le
  jeu hérite du lanceur.

Les interrupteurs « Infinite … » du menu F1 ne sont jamais sauvegardés : ils ne se voient que pendant la partie
(journal du jeu, ``commun/triches.SuiviJournal``, lu par le client).

« Les désactiver » : ``mod.move-speed.enabled=1`` devient ``=0`` (seule cette ligne change, comme
``portage.manette_gamepad``) et ``builtins["move-speed"]`` ``true`` devient ``false`` (modifié sur place, le reste
de profiles.json octet pour octet, comme ``profils_portage``) ; copie de l'ancien fichier gardée ; jeu fermé (vérifié
avant de lire et juste avant de remplacer) ; les variables sont retirées de l'environnement du jeu par le lanceur
(rien n'est écrit pour elles).
"""
from __future__ import annotations

import json
import os
import time

from commun import triches as tr
from jeu import profils_portage as pp

CLE_VITESSE = b"mod.move-speed.enabled"
ID_VITESSE = "move-speed"
GARDER = 10


class ErreurTriches(Exception):
    def __init__(self, fr: str, en: str | None = None):
        super().__init__(fr)
        self.fr, self.en = fr, en or fr


def chemin_reglages(inst: dict, env=None) -> str:
    env = os.environ if env is None else env
    if env.get(tr.ENV_REGLAGES):
        return env[tr.ENV_REGLAGES]
    return os.path.join(inst.get("config") or "", "settings.ini") if inst.get("config") else ""


def _lignes_vitesse(brut: bytes) -> tuple[list[bytes], list[int], bytes | None]:
    """(lignes, index des lignes « mod.move-speed.enabled= », dernière valeur) ; « # » et vides ignorées, la
    dernière gagne (``load_locked``)."""
    lignes = brut.split(b"\n")
    idx, val = [], None
    for i, ligne in enumerate(lignes):
        s = ligne.rstrip(b"\r")
        if s.startswith(CLE_VITESSE + b"="):
            idx.append(i)
            val = s[len(CLE_VITESSE) + 1:]
    return lignes, idx, val


def _lire(p: str) -> bytes | None:
    try:
        with open(p, "rb") as f:
            return f.read(pp.TAILLE_MAX)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise ErreurTriches(f"{os.path.basename(p)} illisible ({e.__class__.__name__})",
                            f"unreadable {os.path.basename(p)} ({e.__class__.__name__})") from None


def _profil_vitesse(texte: str) -> bool | None:
    """``builtins["move-speed"]`` du profil actif (None : absent, pas un booléen ou fichier d'un format inconnu)."""
    try:
        r = pp.analyser(texte)
        actif = pp.valider(r)
    except pp.ErreurProfils:
        return None
    b = r.get("profiles").get(actif).get("builtins")
    v = b.get(ID_VITESSE) if b is not None and b.type == "obj" else None
    return v.valeur if v is not None and v.type == "bool" else None


def vitesse(inst: dict, env=None) -> dict:
    """{active, source} de « Run/swim speed » au prochain démarrage : source « env », « profil », « reglages » ou
    None (coupée par défaut)."""
    env = os.environ if env is None else env
    if env.get(tr.ENV_VITESSE) is not None:
        return {"active": tr._atoi(env.get(tr.ENV_VITESSE)) != 0, "source": "env"}
    active, source = False, None
    p = chemin_reglages(inst, env)
    if p:
        try:
            brut = _lire(p)
        except ErreurTriches:
            brut = None
        if brut is not None:
            _l, _i, val = _lignes_vitesse(brut.replace(b"\r\n", b"\n"))
            if val in (b"0", b"1"):
                active, source = val == b"1", "reglages"
    if inst.get("config"):
        try:
            texte = pp.lire_texte(pp.chemin(inst["config"]))
        except pp.ErreurProfils:
            texte = None
        v = _profil_vitesse(texte) if texte is not None else None
        if v is not None:
            active, source = v, "profil"
    return {"active": active, "source": source}


def detecter(inst: dict, env=None) -> dict:
    """Ce que le jeu aura au démarrage : {genantes: [ids], env: {id: variable}, vitesse: {active, source},
    variables: [variables présentes à retirer], fichiers: True si « Les désactiver » écrira un fichier}."""
    env = os.environ if env is None else env
    par_env = tr.depuis_env(env)
    v = vitesse(inst, env)
    ids = set(par_env)
    if v["active"]:
        ids.add("vitesse")
    return {"genantes": tr.genantes(ids), "env": par_env, "vitesse": v, "variables": variables_a_retirer(env),
            "fichiers": bool(v["active"] and v["source"] in ("reglages", "profil"))}


def variables_a_retirer(env) -> list[str]:
    """Variables de triche présentes dans ``env`` que « Les désactiver » retire de l'environnement du jeu. Pas
    ``WWHD_MOD_MOVE_SPEED`` à 0 : elle COUPE l'option (la retirer rendrait la main au réglage sauvegardé)."""
    return [n for n in tr.VARIABLES if env.get(n) is not None
            and (n != tr.ENV_VITESSE or tr._atoi(env.get(n)) != 0)]


def texte(det: dict, en: bool = False) -> str:
    """Phrase de l'avertissement (étape Jouer)."""
    parts = []
    for i in det["genantes"]:
        d = det["env"].get(i)
        ou = ""
        if d:
            ou = f" (variable {d})"
        elif i == "vitesse":
            ou = " (option saved by the game)" if en else " (option gardée par le jeu)"
        parts.append(f"{tr.libelle(i, en)}{ou} : {tr.casse(i, en)}" if not en else
                     f"{tr.libelle(i, en)}{ou}: {tr.casse(i, en)}")
    return " ; ".join(parts)


def _copie(p: str, brut: bytes, base: str, prefixe: str, ext: str) -> str:
    os.makedirs(base, exist_ok=True)
    nom = f"{prefixe}-{time.strftime('%Y%m%d-%H%M%S')}-triches"
    dst, k = os.path.join(base, nom + ext), 1
    while os.path.exists(dst):
        k += 1
        dst = os.path.join(base, f"{nom}-{k}{ext}")
    with open(dst, "wb") as f:
        f.write(brut)
        f.flush()
        os.fsync(f.fileno())
    with open(dst, "rb") as f:
        if f.read() != brut:
            raise ErreurTriches("copie de sécurité ratée : rien n'est changé", "backup failed: nothing is changed")
    anciens = sorted((n for n in os.listdir(base) if n.startswith(prefixe + "-") and n.endswith(ext)), reverse=True)
    for n in anciens[GARDER:]:
        try:
            os.remove(os.path.join(base, n))
        except OSError:
            pass
    return dst


def _remplacer(p: str, avant: bytes, neuf: bytes, jeu_tourne) -> None:
    tmp = os.path.join(os.path.dirname(p), "." + os.path.basename(p) + ".wwmp-triches.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(neuf)
            f.flush()
            os.fsync(f.fileno())
        _refuser(jeu_tourne)
        with open(p, "rb") as f:
            if f.read(pp.TAILLE_MAX) != avant:
                raise ErreurTriches(f"{os.path.basename(p)} a changé pendant la préparation : recommence",
                                    f"{os.path.basename(p)} changed during the preparation: try again")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _refuser(jeu_tourne) -> None:
    if jeu_tourne is not None and jeu_tourne():
        raise ErreurTriches("le jeu (portage natif) est ouvert : ferme-le d'abord, ses réglages ne sont changés que "
                            "jeu fermé", "the game (native port) is open: close it first, its settings are only "
                            "changed with the game closed")


def couper_reglages(p: str, base_copies: str, jeu_tourne=None) -> str:
    """settings.ini : chaque ligne ``mod.move-speed.enabled=<non 0>`` devient ``=0`` (rien d'autre). Renvoie le chemin
    de la copie, "" si rien à changer."""
    _refuser(jeu_tourne)
    brut = _lire(p) if p else None
    if brut is None:
        return ""
    lignes, idx, _val = _lignes_vitesse(brut)
    a_changer = [i for i in idx if lignes[i].rstrip(b"\r") != CLE_VITESSE + b"=0"]
    if not a_changer:
        return ""
    for i in a_changer:
        lignes[i] = CLE_VITESSE + b"=0" + (b"\r" if lignes[i].endswith(b"\r") else b"")
    neuf = b"\n".join(lignes)
    copie = _copie(p, brut, base_copies, "settings", ".ini")
    _remplacer(p, brut, neuf, jeu_tourne)
    return copie


def couper_profil(config: str, base_copies: str, jeu_tourne=None) -> str:
    """profiles.json : ``builtins["move-speed"]`` ``true`` du profil actif devient ``false`` (sur place). Renvoie le
    chemin de la copie, "" si rien à changer (absent, déjà false, format inconnu : rien n'est touché)."""
    _refuser(jeu_tourne)
    p = pp.chemin(config)
    if os.path.islink(p):
        return ""
    try:
        texte = pp.lire_texte(p)
    except pp.ErreurProfils:
        return ""
    if _profil_vitesse(texte) is not True:
        return ""
    actif = pp.valider(pp.analyser(texte))
    neuf = pp._poser(texte, ("profiles", actif, "builtins", ID_VITESSE), "false")
    # contrôle : seule cette valeur a changé
    a, b = json.loads(texte), json.loads(neuf)
    b["profiles"][actif]["builtins"][ID_VITESSE] = True
    if a != b or _profil_vitesse(neuf) is not False:
        raise ErreurTriches("modification de profiles.json ratée (contrôle) : rien n'est changé",
                            "profiles.json change failed (check): nothing is changed")
    brut = texte.encode("utf-8")
    copie = _copie(p, brut, base_copies, "profiles", ".json")
    _remplacer(p, brut, neuf.encode("utf-8"), jeu_tourne)
    return copie


def desactiver(inst: dict, base_copies: str, jeu_tourne=None, env=None) -> dict:
    """« Les désactiver » : fichiers du portage (jeu fermé, copies gardées). Renvoie {copies: [chemins], ecrit}. Les
    variables d'environnement sont l'affaire de l'appelant (retirées de l'environnement du jeu). Lève ErreurTriches."""
    copies = []
    c = couper_reglages(chemin_reglages(inst, env), base_copies, jeu_tourne)
    if c:
        copies.append(c)
    if inst.get("config"):
        c = couper_profil(inst["config"], base_copies, jeu_tourne)
        if c:
            copies.append(c)
    return {"copies": copies, "ecrit": bool(copies)}
