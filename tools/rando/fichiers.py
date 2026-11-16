#!/usr/bin/env python3
"""Randomizer par fichiers générés localement : générateur, empreintes, pack WWMP_Rando (étape 1).

Plan : docs/etudes/rando_fichiers.md. Les formats et décalages viennent de TWWHD-Randomizer (SuperDude88 et
contributeurs, licence MIT, Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt) ; tout
fichier produit porte cette mention.

Règles (elles ne se discutent pas) :

* le dump de l'utilisateur est LU SEULEMENT (aucun fichier n'y est ouvert en écriture ; la sortie ne peut être ni
  le dump, ni dedans, ni contenir le dump) ;
* aucun fichier du jeu n'est commité, envoyé ou distribué : chaque PC génère SES fichiers depuis SON dump ; même
  table + mêmes options + mêmes outils + dump USA v0 = mêmes octets partout (empreinte vérifiée à la connexion) ;
* le générateur ne s'installe JAMAIS lui-même dans le dossier graphicPacks de Cemu (refus de toute sortie dans un
  dossier ``graphicPacks``) : il écrit dans un dossier de sortie ; l'installation est faite par le lanceur, sur le
  clic de l'utilisateur, Cemu fermé (``installer``) ;
* écriture atomique : dossier temporaire voisin, puis renommage ; une sortie existante qui n'est pas à nous (sans
  ``manifeste.json`` du générateur) n'est jamais remplacée.

Le générateur NE TIRE RIEN : il applique la table que le serveur a calculée (message « rando » du randomizer
complet), d'où les mêmes fichiers chez tous.

Deux empreintes (§2.4 de l'étude) :

* ``plan`` : SHA-256 du JSON canonique {table sans graine, version du générateur, format du pack, empreinte de la
  liste empreintes_usa_v0.json}. Le serveur la calcule seul (sans dump) ; elle est aussi écrite dans la zone
  ``WWMPRNDG`` du pack généré, que le client relit dans la mémoire du jeu (preuve que Cemu a chargé CE pack) ;
* ``fichiers`` : SHA-256 de la liste triée (chemin, taille, SHA-256) de TOUS les fichiers du pack généré (sauf le
  manifeste). Le serveur la retient au premier joueur et l'exige des suivants (gardée dans le monde persistant).

Ligne de commande :

    python tools/rando/fichiers.py --jeu <dump> --table table.json [--sortie genere/rando]
    python tools/rando/fichiers.py --jeu <dump> --table table.json --copie-complete D:\\WWHD_rando
    python tools/rando/fichiers.py --verifier genere/rando/WWMP_Rando
    python tools/rando/fichiers.py --relever-empreintes <dump>     (outil de développement)

Le fichier ``--table`` est le message « rando » du serveur (JSON) ou la réponse « rando_plan » du lobby.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import time

ICI = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(ICI)
RACINE = os.path.dirname(TOOLS)

from jeu.fichiers import ErreurFormat, chemin as fj_chemin, dump as fj_dump  # noqa: E402
from rando import lot6 as rando_lot6, lot7 as rando_lot7, lot8 as rando_lot8, lot9 as rando_lot9  # noqa: E402

VERSION_GENERATEUR = "wwmp-rando-fichiers-1"
FORMAT_PACK = 1                     # mot de format de la zone WWMPRNDR (code fixe du pack)
FORMAT_MANIFESTE = 1
NOM_PACK = "WWMP_Rando"
PACK_DEPOT = os.path.join(RACINE, "graphicpack", NOM_PACK)
SORTIE_DEFAUT = os.path.join(RACINE, "genere", "rando")
LICENCE = os.path.join(ICI, "logique", "LICENSE_TWWHD.txt")
MANIFESTE = "manifeste.json"
MAGIE_R = b"WWMPRNDR"
MAGIE_G = b"WWMPRNDG"
DEBUT_ZONE = "; --- DEBUT EMPREINTE DU PLAN (remplacee par le generateur) ---"
FIN_ZONE = "; --- FIN EMPREINTE DU PLAN ---"
CHAMPS_TABLE = ("v", "algo", "donnees", "options", "t", "emp")

# options du moteur qui exigent des fichiers générés (toutes coupées par défaut) : l'option de test de l'étape 1 et
# les familles du lot 6 (rando/lot6.py : trésors, événements, boss, fées, objets posés, personnages, Beedle)
OPTIONS_FICHIERS = ("fichiers_demo",) + rando_lot6.OPTIONS + rando_lot7.OPTIONS_BOOL + rando_lot8.OPTIONS_BOOL \
    + rando_lot9.OPTIONS_FICHIERS            # étape 5 : correctif « épuisé » de la boutique (le reste : données)
# (lot 7, rando/lot7.py : en plus, « monde » = « ouvert » et « depart » = une île ; lot 8, rando/lot8.py :
# « entrees » ; voir options_fichiers)

# option de TEST « fichiers_demo » : texte « Appuyez sur un bouton » de l'écran titre, dans les trois langues US
# (toujours les trois : même empreinte quelle que soit la langue de la console). Même taille que l'original.
TEXTE_DEMO = "\ue0a0 Randomizer WWMP \ue0a1"
LANGUES_US = ("English", "French", "Spanish")


def chemin_titre(langue: str) -> str:
    return (f"content/Common/Pack/permanent_2d_Us{langue}.pack@SARC@Title_00_msbt.szs@YAZ0@SARC@"
            "Title_00.msbt@MSBT")


class ErreurGenerateur(Exception):
    """Refus du générateur : ``fr`` et ``en`` sont les messages pour l'utilisateur."""

    def __init__(self, fr: str, en: str | None = None):
        super().__init__(fr)
        self.fr, self.en = fr, en or fr


# ---------------------------------------------------------------------------------------------------------------
# Options et empreintes
# ---------------------------------------------------------------------------------------------------------------
def options_fichiers(options) -> dict:
    """Options « fichiers » ALLUMÉES de ces options du moteur (vide : rien à générer, rien d'exigé)."""
    if not isinstance(options, dict):
        return {}
    out = {k: True for k in OPTIONS_FICHIERS if options.get(k) is True}
    out.update(rando_lot7.options_fichiers(options))
    out.update(rando_lot8.options_fichiers(options))
    return out


def table_de(msg: dict) -> dict:
    """Le message « rando » (ou la réponse « rando_plan », qui le porte dans « table ») réduit à ses champs signés,
    sans graine. Lève ErreurGenerateur si ce n'est pas une table du randomizer complet."""
    if isinstance(msg, dict) and isinstance(msg.get("table"), dict):
        msg = msg["table"]
    if not isinstance(msg, dict) or not isinstance(msg.get("t"), list) or not isinstance(msg.get("emp"), str):
        raise ErreurGenerateur("table du randomizer illisible (fichier de la partie abîmé ?)",
                               "unreadable randomizer table (damaged game file?)")
    return {k: msg.get(k) for k in CHAMPS_TABLE}


def fichiers_requis(msg_ou_options) -> bool:
    """Cette table (ou ces options) exige-t-elle le pack WWMP_Rando généré chez tous ?"""
    if isinstance(msg_ou_options, dict) and ("t" in msg_ou_options or "table" in msg_ou_options):
        try:
            return bool(options_fichiers(table_de(msg_ou_options).get("options")))
        except ErreurGenerateur:
            return False
    return bool(options_fichiers(msg_ou_options))


def _canon(x) -> bytes:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def empreinte_plan(msg: dict, liste: str | None = None) -> str:
    """Empreinte du plan (64 caractères hexadécimaux). Le serveur la calcule sans dump."""
    doc = {"table": table_de(msg), "generateur": VERSION_GENERATEUR, "pack": FORMAT_PACK,
           "empreintes": liste if liste is not None else fj_dump.empreinte_liste()}
    return hashlib.sha256(b"WWMP-RANDO-PLAN|" + _canon(doc)).hexdigest()


def empreinte_fichiers(liste: list) -> str:
    """Empreinte des fichiers d'un pack : ``liste`` de dicts {chemin, taille, sha256} (ordre indifférent)."""
    lst = sorted([str(e["chemin"]), int(e["taille"]), str(e["sha256"])] for e in liste)
    return hashlib.sha256(b"WWMP-RANDO-FICHIERS|" + _canon(lst)).hexdigest()


def est_empreinte(x) -> bool:
    return isinstance(x, str) and re.fullmatch(r"[0-9a-f]{64}", x) is not None


# ---------------------------------------------------------------------------------------------------------------
# Plan d'édition (ordre fixe)
# ---------------------------------------------------------------------------------------------------------------
def entrees_lot6(msg: dict) -> dict:
    """{site: (origine, nouveau)} des entrées de type 5 (lot 6) de la table, contrôlées (site connu, famille mélangée
    par les options de la table, objet d'origine du relevé, objet accepté à cet endroit)."""
    table = table_de(msg)
    familles = rando_lot6.familles_actives(table.get("options"))
    from rando import plus as rando_plus
    libres = rando_plus.libres_fees(table.get("options"))
    out = {}
    for e in table.get("t") or []:
        if not (isinstance(e, list) and len(e) == 5 and e[0] == rando_lot6.TYPE_FICHIER):
            continue
        _t, k, z, o, n = e
        if not (isinstance(k, int) and not isinstance(k, bool) and 0 <= k < len(rando_lot6.SITES) and z == 0
                and k not in out and rando_lot6.SITES[k]["famille"] in familles and o == rando_lot6.SITES[k]["id"]
                and isinstance(n, int) and not isinstance(n, bool) and rando_lot6.accepte(k, n, libres)):
            raise ErreurGenerateur("table du randomizer : lieu du lot 6 invalide (outils différents de ceux de "
                                   "l'hôte ?)", "randomizer table: invalid lot 6 location (tools differ from the "
                                   "host's?)")
        out[k] = (o, n)
    return out


def plan_editions(msg: dict) -> list[tuple]:
    """Liste ordonnée des éditions de FICHIERS de la table : (chemin, opération, arguments). Le code du lot 6 (lignes
    du pack) est à part : ``code_lot6``. Vide : aucun fichier à réécrire."""
    table = table_de(msg)
    opt = options_fichiers(table.get("options"))
    out = []
    if opt.get("fichiers_demo"):
        for lg in LANGUES_US:
            out.append((chemin_titre(lg), "texte_meme_taille", ("T_Press_00", TEXTE_DEMO)))
    familles = rando_lot6.familles_actives(table.get("options"))
    if familles:
        out += rando_lot6.editions(entrees_lot6(table), familles)
    out += rando_lot7.editions(table.get("options"))
    if isinstance(table.get("options"), dict) and table["options"].get("textes") is True:
        objets, origine = objets_textes(table)
        for lg, et, (genre, octets) in rando_lot7.editions_textes(objets, origine):
            out.append((rando_lot7.chemin_message(lg, ""), "texte_lot7", (lg, et, genre, octets)))
    # lot 8 (sans épée, entrées, cartes) : APRÈS les lots 6 et 7 (coffre de l'épée ajouté par le lot 7 ; trésors du
    # lot 6, dont seul l'index de carte change ici)
    try:
        out += rando_lot8.editions(table.get("options"))
    except rando_lot8.ErreurLot8 as e:
        raise ErreurGenerateur(f"table du randomizer : lot 8 invalide ({e})",
                               f"randomizer table: invalid lot 8 ({e})") from None
    return out


# objet d'origine de l'enchère à 100 rubis (0x1019018D dans le RPX USA v0 : voile rapide), pour l'affiche des enchères
ENCHERE_100 = ("Windfall Island - Auction 100 Rupee", 0x77)


def objets_textes(msg: dict) -> tuple[dict, dict]:
    """({lieu: objet de la table}, {lieu: objet d'origine}) des lieux que les textes du lot 7 nomment : étals et
    enchères (lieux « donnée » du lot 1), Beedle et calmar géant (lot 6)."""
    from rando import noyau as rando
    table = table_de(msg)
    origine, par_site2, par_site5 = {}, {}, {}
    for k, s in enumerate(rando.SITES_DONNEES):
        if s[0] in ("boutiques", "encheres", "enchere100"):      # (étape 5 : enchère à 100 rubis mélangée)
            origine[s[1]] = s[2]
            par_site2[k] = s[1]
    origine[ENCHERE_100[0]] = ENCHERE_100[1]
    for k, s in enumerate(rando_lot6.SITES):
        if s["nom"] in rando_lot7.LIEUX_TEXTES_LOT6:
            origine[s["nom"]] = s["id"]
            par_site5[k] = s["nom"]
    objets = {}
    for e in table.get("t") or []:
        if not (isinstance(e, list) and len(e) == 5):
            continue
        if e[0] == 2 and e[1] in par_site2:
            objets[par_site2[e[1]]] = e[4]
        elif e[0] == rando_lot6.TYPE_FICHIER and e[1] in par_site5:
            objets[par_site5[e[1]]] = e[4]
    return objets, origine


def code_lot6(msg: dict, rpx) -> tuple[str, str]:
    """(symboles, code et sites) du lot 6 pour cette table ("", "" si aucune famille du lot 6). ``rpx`` : RPX du dump
    (rando_lot6.Rpx, lu seulement)."""
    table = table_de(msg)
    familles = rando_lot6.familles_actives(table.get("options"))
    if not familles:
        return "", ""
    return rando_lot6.texte_code(rando_lot6.code(entrees_lot6(table), familles, rpx))


def code_pack(msg: dict, rpx) -> tuple[str, str]:
    """(symboles, code et sites) du pack généré : lot 6 seul exactement comme avant (``code_lot6``), ou lots 6 et 7
    réunis (une adresse = un nom ; deux lignes n'écrivent jamais la même adresse). Avec l'option « progressifs_plus »
    (rando/plus.py), une donnée de plus à la fin de la cave : l'étiquette ``wwr_plus`` (octet des familles), que le
    lanceur recopie dans le plan du mod natif (rando/natif_code.py) ; elle ne réécrit aucun mot du jeu (sous Cemu,
    sans effet : le serveur refuse un jeu sans la zone WWMPPRG2)."""
    from rando import plus as rando_plus
    sym, code = _code_pack(msg, rpx)
    b = rando_plus.octet(table_de(msg).get("options"))
    if b:
        if not code:
            raise ErreurGenerateur("objets progressifs de TWWHD sans randomizer par fichiers",
                                   "TWWHD progressive items without the file randomizer")
        code = code.rstrip("\n") + "\n" + "\n".join(lignes_plus(b)) + "\n"
    return sym, code


def lignes_plus(b: int) -> list[str]:
    """Lignes de la cave qui portent l'octet des familles de progressifs_plus (lu par rando/natif_code.py)."""
    return ["; --- objets progressifs de TWWHD (WWMP) : familles pour le mod natif 0.40.0 (donnee seule, sans site) ---",
            "wwr_plus:", f"    .int 0x{b & 0xFF:08X}"]


def _code_pack(msg: dict, rpx) -> tuple[str, str]:
    table = table_de(msg)
    opt = table.get("options")
    familles = rando_lot6.familles_actives(opt)
    c9 = rando_lot9.code(opt, rpx, familles)          # étape 5 : correctif « épuisé » (vide sans l'option)
    if not rando_lot7.actif(opt) and not rando_lot8.fragments_actifs(opt) and not c9["sites"]:
        return code_lot6(table, rpx)
    c6 = rando_lot6.code(entrees_lot6(table), familles, rpx) if familles else {"symboles": {}, "cave": [],
                                                                                "sites": []}
    c7 = rando_lot7.code(opt, rpx)
    c8 = rando_lot8.code(opt, rpx)
    sym = dict(c6["symboles"])
    for a, n in list(c7["symboles"].items()) + list(c8["symboles"].items()) + list(c9["symboles"].items()):
        if sym.get(a, n) != n or (n in sym.values() and sym.get(a) != n):
            raise rando_lot7.ErreurLot7(f"symbole {n} nommé autrement dans les lots 6 à 9")
        sym[a] = n
    sites = c6["sites"] + c7["sites"] + c8["sites"] + c9["sites"]
    adrs = [int(l.split(" ", 1)[0], 16) for l in sites if l.startswith("0x")]
    if len(adrs) != len(set(adrs)):
        raise rando_lot7.ErreurLot7("les lots 6 à 8 écrivent la même adresse")
    lots = "lots 6 a 9" if c9["sites"] else "lots 6 a 8" if c8["sites"] else "lots 6 et 7"   # (textes d'avant)
    avant = "".join(f"0x{a:08X} = {n}:\n" for a, n in sorted(sym.items()))
    apres = "\n".join([f"; --- {lots} (randomizer par fichiers) : code repris de TWWHD-Randomizer (SuperDude88",
                       ";     et contributeurs), licence MIT, Copyright (c) 2024 SuperDude88 (LICENSE_TWWHD.txt), et",
                       ";     code WWMP. Genere sur ce PC pour cette table : ne pas distribuer. ---",
                       "; --- code ajoute (cave) ---"] + c6["cave"] + c7["cave"] + c8["cave"]
                      + ["; --- sites en place et donnees ---"] + sites) + "\n"
    return avant, apres


def appliquer(session: fj_chemin.Session, editions: list[tuple]) -> None:
    for ch, op, args in editions:
        if op == "texte_lot7":
            lg, et, genre, octets = args
            rando_lot7.appliquer_textes(session, [(lg, et, (genre, octets))])
        elif op in rando_lot7.OPERATIONS:
            rando_lot7.appliquer(session, ch, op, args)
        elif op in rando_lot8.OPERATIONS:
            rando_lot8.appliquer(session, ch, op, args)
        elif op == "texte_meme_taille":
            session.ouvrir(ch).remplacer_meme_taille(*args)
        elif op == "parametre_objet":
            rando_lot6.poser_objet(session.ouvrir(ch), *args)
        elif op == "objet_evenement":
            rando_lot6.objet_evenement(session.ouvrir(ch), *args)
        elif op == "drapeaux_aiguille":
            rando_lot6.drapeaux_aiguille(session.ouvrir(ch))
        else:
            raise ErreurFormat(f"opération inconnue : {op}")


# ---------------------------------------------------------------------------------------------------------------
# Sorties permises
# ---------------------------------------------------------------------------------------------------------------
def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _dans(p: str, d: str) -> bool:
    p, d = _norm(p), _norm(d)
    return p == d or p.startswith(d.rstrip("\\/") + os.sep)


def dossiers_graphicpacks() -> list[str]:
    """Dossiers graphicPacks de Cemu connus sur ce PC (pour les REFUSER comme sortie)."""
    out = [os.path.join(os.environ.get("APPDATA", ""), "Cemu", "graphicPacks")]
    if os.name != "nt":
        # Linux : tous les dossiers de Cemu possibles (natif, ancien, Flatpak, portable)
        from jeu import plateforme
        out = [os.path.join(d, "graphicPacks") for _c, d in plateforme.dossiers_cemu()]
    try:
        with open(os.path.join(TOOLS, "wwhd_config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        for k in ("cemu_exe", "cemu", "cemu_dir"):
            v = cfg.get(k) if isinstance(cfg, dict) else None
            if isinstance(v, str) and v:
                d = v if os.path.isdir(v) else os.path.dirname(v)
                out += [os.path.join(d, "graphicPacks"), os.path.join(d, "portable", "graphicPacks")]
    except (OSError, ValueError):
        pass
    return out


def verifier_sortie(sortie: str, jeu: str | None, interdits: list[str] | None = None) -> None:
    """Refus d'une sortie dangereuse : le dump (ou dedans, ou qui le contient), un dossier graphicPacks."""
    if jeu and (_dans(sortie, jeu) or _dans(jeu, sortie)):
        raise ErreurGenerateur(f"sortie refusée : {sortie} est le dossier du jeu, est dedans ou le contient (le "
                               "dump reste en lecture seule)",
                               f"output refused: {sortie} is the game folder, is inside it or contains it (the "
                               "dump stays read-only)")
    parts = [x.lower() for x in re.split(r"[\\/]+", _norm(sortie))]
    if "graphicpacks" in parts:
        raise ErreurGenerateur(f"sortie refusée : {sortie} est dans un dossier graphicPacks (le générateur ne "
                               "s'installe jamais lui-même ; installe avec le bouton « Installer » du lanceur)",
                               f"output refused: {sortie} is inside a graphicPacks folder (the generator never "
                               "installs itself; use the launcher's Install button)")
    for d in (interdits if interdits is not None else dossiers_graphicpacks()):
        if d and _dans(sortie, d):
            raise ErreurGenerateur(f"sortie refusée : {sortie} est dans le dossier graphicPacks de Cemu",
                                   f"output refused: {sortie} is inside Cemu's graphicPacks folder")


def _est_a_nous(d: str) -> bool:
    try:
        with open(os.path.join(d, MANIFESTE), encoding="utf-8") as f:
            m = json.load(f)
        return isinstance(m, dict) and m.get("generateur", "").startswith("wwmp-rando-fichiers")
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------------------------------------------
# Pack
# ---------------------------------------------------------------------------------------------------------------
def asm_genere(plan: str, source: str = PACK_DEPOT, lot6: tuple[str, str] = ("", "")) -> str:
    """patch_rando.asm du dépôt, la zone de l'empreinte du plan remplie (8 mots) et le drapeau « généré » à 1 ; avec
    ``lot6`` = (symboles, code) : les symboles juste avant ``.origin = codecave``, le code après la zone."""
    with open(os.path.join(source, "patch_rando.asm"), encoding="utf-8") as f:
        t = f.read()
    if t.count(DEBUT_ZONE) != 1 or t.count(FIN_ZONE) != 1 or t.count(".int 0              ; +0x18 genere") != 1:
        raise ErreurFormat("patch_rando.asm du dépôt : zone de l'empreinte introuvable")
    a, b = t.index(DEBUT_ZONE) + len(DEBUT_ZONE), t.index(FIN_ZONE)
    mots = struct.unpack(">8I", bytes.fromhex(plan))
    zone = "\n" + "".join(f"    .int 0x{w:08X}\n" for w in mots)
    t = t[:a] + zone + t[b:]
    t = t.replace(".int 0              ; +0x18 genere", ".int 1              ; +0x18 genere")
    symboles, code = lot6
    if symboles or code:
        if t.count("\n.origin = codecave\n") != 1:
            raise ErreurFormat("patch_rando.asm du dépôt : « .origin = codecave » introuvable")
        t = t.replace("\n.origin = codecave\n", "\n; --- lot 6 : fonctions du jeu appelees ---\n" + symboles
                      + "\n.origin = codecave\n")
        t = t.rstrip("\n") + "\n\n" + code
    return t


def rules_genere(plan: str, source: str = PACK_DEPOT) -> str:
    with open(os.path.join(source, "rules.txt"), encoding="utf-8") as f:
        t = f.read()
    t = re.sub(r"(?m)^name = .*$", "name = WWMP Rando (fichiers generes)", t, count=1)
    return re.sub(r"(?m)^description = (.*)$",
                  lambda m: f"description = Pack GENERE sur ce PC pour la partie {plan[:12]} (fichiers du jeu "
                            "remplaces, jamais distribues). " + m.group(1), t, count=1)


def _ecrire(p: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def generer(jeu: str, msg: dict, sortie: str = SORTIE_DEFAUT, progres=None, interdits: list[str] | None = None,
            empreintes: dict | None = None, source: str = PACK_DEPOT) -> dict:
    """Génère ``<sortie>/WWMP_Rando`` pour cette table. Renvoie le manifeste. Lève ErreurGenerateur (refus, avec
    messages FR/EN) ; rien n'est écrit tant que tout n'a pas réussi (dossier temporaire puis renommage)."""
    dire = progres or (lambda _t: None)
    jeu = os.path.abspath(jeu)
    sortie = os.path.abspath(sortie)
    verifier_sortie(sortie, jeu, interdits)
    t0 = time.monotonic()
    try:
        fj_dump.verifier_meta(jeu)
    except fj_dump.ErreurDump as e:
        raise ErreurGenerateur(e.fr, e.en) from None
    table = table_de(msg)
    editions = plan_editions(table)
    lot6 = rando_lot6.actif(table.get("options")) or rando_lot7.actif(table.get("options")) \
        or bool(rando_lot8.fragments_actifs(table.get("options"))) or rando_lot9.epuise(table.get("options"))
    if not editions and not lot6:
        raise ErreurGenerateur("cette partie n'a aucune option « fichiers » : rien à générer (aucun pack exigé)",
                               "this game has no « files » option: nothing to generate (no pack required)")
    doc_emp = empreintes if empreintes is not None else fj_dump.charger_empreintes()
    plan = empreinte_plan(table, fj_dump.empreinte_liste(doc_emp))
    final = os.path.join(sortie, NOM_PACK)
    if os.path.exists(final) and not _est_a_nous(final):
        raise ErreurGenerateur(f"sortie refusée : {final} existe et n'a pas été écrit par ce générateur",
                               f"output refused: {final} exists and was not written by this generator")
    dire("lecture du dump et application des éditions")
    # relecture adverse : l'empreinte de chaque fichier est vérifiée dès sa lecture, AVANT toute analyse (un fichier
    # d'une autre version ou abîmé = refus « pas l'USA v0 », jamais un lecteur qui plante dessus)
    s = fj_chemin.Session(jeu, verifier=lambda rel, data: fj_dump.verifier_fichier(rel, data, doc_emp))
    try:
        code6 = ("", "")
        if lot6:
            dire("lecture du programme du jeu (code/cking.rpx) et code des lots 6 et 7")
            code6 = code_pack(table, rando_lot6.Rpx.depuis_dump(jeu))
        appliquer(s, editions)
        dire("recompression")
        res = s.resultats()
    except fj_dump.ErreurDump as e:
        raise ErreurGenerateur(e.fr, e.en) from None
    except (rando_lot6.ErreurLot6, rando_lot7.ErreurLot7, rando_lot8.ErreurLot8) as e:
        raise ErreurGenerateur(f"génération impossible (le dump n'est pas l'USA v0 attendu ?) : {e}",
                               f"generation failed (is the dump the expected USA v0?): {e}") from None
    except (ErreurFormat, struct.error, IndexError, UnicodeDecodeError) as e:
        raise ErreurGenerateur(f"génération impossible : {e}", f"generation failed: {e}") from None
    os.makedirs(sortie, exist_ok=True)
    tmp = os.path.join(sortie, f".{NOM_PACK}.tmp-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        dire("écriture du pack")
        _ecrire(os.path.join(tmp, "rules.txt"), rules_genere(plan, source).encode("utf-8"))
        _ecrire(os.path.join(tmp, "patch_rando.asm"), asm_genere(plan, source, code6).encode("utf-8"))
        with open(LICENCE, encoding="utf-8") as f:       # fins de ligne LF partout (même empreinte sur tous les PC)
            _ecrire(os.path.join(tmp, "LICENSE_TWWHD.txt"), f.read().encode("utf-8"))
        for rel, data in res.items():
            _ecrire(os.path.join(tmp, *rel.split("/")), data)
        liste = lister_pack(tmp)
        man = {"format": FORMAT_MANIFESTE, "generateur": VERSION_GENERATEUR, "pack": FORMAT_PACK, "plan": plan,
               "table_emp": table["emp"], "donnees": table.get("donnees"),
               "options_fichiers": options_fichiers(table.get("options")),
               "fichiers": liste, "empreinte_fichiers": empreinte_fichiers(liste),
               "origines": {rel: fj_dump.sha256_octets(s.lus[rel]) for rel in sorted(res)},
               "licence": "Contient des formats et des données de TWWHD-Randomizer (SuperDude88, MIT) : "
                          "LICENSE_TWWHD.txt. Fichiers du jeu générés depuis le dump de ce PC : ne pas distribuer."}
        _ecrire(os.path.join(tmp, MANIFESTE), json.dumps(man, indent=1, ensure_ascii=False, sort_keys=True)
                .encode("utf-8"))
        ancien = os.path.join(sortie, f".{NOM_PACK}.ancien-{os.getpid()}")
        if os.path.exists(final):
            os.replace(final, ancien)
        os.replace(tmp, final)
        shutil.rmtree(ancien, ignore_errors=True)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErreurGenerateur(f"écriture impossible dans {sortie} ({e})", f"cannot write to {sortie} ({e})") \
            from None
    man["duree"] = round(time.monotonic() - t0, 2)
    man["dossier"] = final
    dire(f"fini : {final}")
    return man


def lister_pack(d: str) -> list[dict]:
    """[{chemin, taille, sha256}] de tous les fichiers du pack (sauf le manifeste), chemins avec « / », triés."""
    out = []
    for dp, dn, fn in os.walk(d):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, d).replace("\\", "/")
            if rel == MANIFESTE:
                continue
            out.append({"chemin": rel, "taille": os.path.getsize(p), "sha256": fj_dump.sha256_fichier(p)})
    return sorted(out, key=lambda e: e["chemin"])


def verifier_pack(d: str) -> dict:
    """Relit un pack généré (installé ou non) : {ok, plan, fichiers (empreinte recalculée), problemes}. Toute
    différence avec son manifeste (fichier changé, ajouté, manquant) le rend « pas ok »."""
    out = {"ok": False, "plan": "", "fichiers": "", "problemes": [], "dossier": d}
    try:
        with open(os.path.join(d, MANIFESTE), encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, ValueError):
        out["problemes"].append("manifeste absent ou illisible")
        return out
    if not isinstance(man, dict) or not est_empreinte(man.get("plan")):
        out["problemes"].append("manifeste inattendu")
        return out
    try:
        liste = lister_pack(d)
    except OSError as e:
        out["problemes"].append(f"lecture impossible ({e})")
        return out
    attendu = {e["chemin"]: e for e in man.get("fichiers") or [] if isinstance(e, dict)}
    vus = {e["chemin"]: e for e in liste}
    for c in sorted(set(attendu) | set(vus)):
        if c not in vus:
            out["problemes"].append(f"manquant : {c}")
        elif c not in attendu:
            out["problemes"].append(f"en trop : {c}")
        elif (vus[c]["taille"], vus[c]["sha256"]) != (attendu[c].get("taille"), attendu[c].get("sha256")):
            out["problemes"].append(f"changé : {c}")
    out["plan"] = man["plan"]
    out["fichiers"] = empreinte_fichiers(liste)
    if out["fichiers"] != man.get("empreinte_fichiers"):
        out["problemes"].append("empreinte des fichiers différente du manifeste")
    out["ok"] = not out["problemes"]
    out["table_emp"] = man.get("table_emp")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Installation (lanceur, sur clic, Cemu fermé) et copie complète (repli)
# ---------------------------------------------------------------------------------------------------------------
def installer(pack: str, graphicpacks: str, cemu_ouvert: bool) -> str:
    """Copie le pack généré ``pack`` dans ``graphicpacks``/WWMP_Rando. APPELÉ SEULEMENT sur un clic de l'utilisateur
    (bouton « Installer » du lanceur). Refus si Cemu tourne, si le pack ne se relit pas, ou si un WWMP_Rando
    installé n'est pas à nous. Renvoie le dossier installé."""
    if cemu_ouvert:
        raise ErreurGenerateur("ferme Cemu avant d'installer (le pack est lu au lancement du jeu)",
                               "close Cemu before installing (the pack is read when the game starts)")
    v = verifier_pack(pack)
    if not v["ok"]:
        raise ErreurGenerateur("pack généré abîmé (" + "; ".join(v["problemes"][:3]) + ") : génère-le à nouveau",
                               "damaged generated pack (" + "; ".join(v["problemes"][:3]) + "): generate it again")
    dest = os.path.join(graphicpacks, NOM_PACK)
    if os.path.exists(dest) and not _est_a_nous(dest) and not _est_pack_depot(dest):
        raise ErreurGenerateur(f"{dest} existe et n'est pas un pack WWMP_Rando : rien n'est remplacé",
                               f"{dest} exists and is not a WWMP_Rando pack: nothing is replaced")
    tmp = dest + ".nouveau"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(graphicpacks, exist_ok=True)
    shutil.copytree(pack, tmp)
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.replace(tmp, dest)
    return dest


def _est_pack_depot(d: str) -> bool:
    try:
        with open(os.path.join(d, "patch_rando.asm"), encoding="utf-8") as f:
            return "WWMP_Rando_US" in f.read()
    except OSError:
        return False


def desinstaller(graphicpacks: str, cemu_ouvert: bool) -> bool:
    """Retire graphicPacks/WWMP_Rando (sur clic, Cemu fermé), seulement s'il est à nous. True si retiré."""
    if cemu_ouvert:
        raise ErreurGenerateur("ferme Cemu avant de désinstaller", "close Cemu before uninstalling")
    dest = os.path.join(graphicpacks, NOM_PACK)
    if not os.path.isdir(dest):
        return False
    if not (_est_a_nous(dest) or _est_pack_depot(dest)):
        raise ErreurGenerateur(f"{dest} n'est pas un pack WWMP_Rando : rien n'est retiré",
                               f"{dest} is not a WWMP_Rando pack: nothing is removed")
    shutil.rmtree(dest)
    return True


# marque de la copie complète : plan et empreinte des fichiers générés qu'elle porte (le lanceur refuse d'y jouer
# si le pack de la partie a changé depuis : relecture adverse de l'étape 5)
MARQUE_COPIE = ".wwmp_copie_complete"


def copie_complete(jeu: str, pack: str, dest: str, interdits: list[str] | None = None, progres=None) -> str:
    """Repli (si Cemu ne sert pas le dossier content/ du pack) : copie code/, content/ et meta/ du dump dans
    ``dest`` (choisi par l'utilisateur), en y écrasant les seuls fichiers générés de ``pack``. Le jeu se lance
    alors par ``cemu -g <dest>/code/cking.rpx`` ; le code reste dans le pack WWMP_Rando. Renvoie le chemin du RPX."""
    dire = progres or (lambda _t: None)
    jeu, dest = os.path.abspath(jeu), os.path.abspath(dest)
    verifier_sortie(dest, jeu, interdits)
    v = verifier_pack(pack)
    if not v["ok"]:
        raise ErreurGenerateur("pack généré abîmé : génère-le à nouveau", "damaged generated pack: generate it again")
    if os.path.isdir(dest) and os.listdir(dest) and not os.path.isfile(os.path.join(dest, MARQUE_COPIE)):
        raise ErreurGenerateur(f"{dest} n'est pas vide : choisis un dossier vide",
                               f"{dest} is not empty: pick an empty folder")
    try:
        fj_dump.verifier_meta(jeu)
    except fj_dump.ErreurDump as e:
        raise ErreurGenerateur(e.fr, e.en) from None
    with open(os.path.join(pack, MANIFESTE), encoding="utf-8") as f:
        generes = [e["chemin"] for e in json.load(f)["fichiers"] if e["chemin"].startswith("content/")]
    tmp = dest + f".tmp-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for sous in ("code", "content", "meta"):
            dire(f"copie de {sous}/")
            shutil.copytree(os.path.join(jeu, sous), os.path.join(tmp, sous))
        for c in generes:
            shutil.copyfile(os.path.join(pack, *c.split("/")), os.path.join(tmp, *c.split("/")))
        with open(os.path.join(tmp, MARQUE_COPIE), "w", encoding="utf-8") as f:
            f.write(json.dumps({"plan": v["plan"], "fichiers": v["fichiers"]}))
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        os.replace(tmp, dest)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErreurGenerateur(f"copie impossible ({e})", f"copy failed ({e})") from None
    return os.path.join(dest, "code", "cking.rpx")


# ---------------------------------------------------------------------------------------------------------------
# Portage natif (lot 4, docs/dev/PORTAGE_NATIF.md §3.8) : les fichiers générés en MOD DE CONTENU du portage
# ---------------------------------------------------------------------------------------------------------------
# Le portage ne charge pas de patch Cemu : son gestionnaire de mods sert des fichiers de remplacement (mod de contenu,
# ``"kind": "content"``, docs/mod-manager.md du portage) et le code vient de notre mod natif (wwmp-natif). Le paquet
# de contenu porte EXACTEMENT les fichiers générés du pack Cemu (content/…, LICENSE_TWWHD.txt) et le manifeste du
# générateur ; il n'a pas le code Cemu (rules.txt, patch_rando.asm), qui n'a rien à faire sur le portage SAUF s'il
# porte du code des lots 6 à 9 (TWWHD) : une telle partie est refusée sur le portage (code du jeu réécrit : le portage
# a traduit le jeu une fois pour toutes ; seul un mod de code pourrait le refaire, fonction par fonction).
# L'empreinte des fichiers reste celle du pack Cemu (mêmes entrées du manifeste, rules.txt et patch_rando.asm relus
# tels que le générateur les écrit pour ce plan, sans code) : un joueur Cemu et un joueur du portage jouent ensemble.
# La preuve « ce paquet est chargé » (zone WWMPRNDR) est le fichier rando_plan.bin du dossier de données du mod de
# code, écrit APRÈS le paquet (et effacé AVANT de le changer), lu par le mod au démarrage du jeu (rando_fichiers.c).
# Ce module n'écrit jamais profiles.json : le paquet est allumé par le lanceur juste avant le lancement
# (lanceur/natif.py ``activer``, jeu/profils_portage.py), ou dans le jeu (F1 > Mods), comme le mod de code.
ID_NATIF = "wwmp-rando"
ID_MOD_CODE = "wwmp-natif"
ID_GREFFE = "wwmp-rando-code"
MANIFESTE_PORTAGE = "manifest.json"
FICHIER_PLAN = "rando_plan.bin"
MAGIE_PLAN = b"WWMPRNDF"
FORMAT_PLAN = 1
HORS_CONTENU = ("rules.txt", "patch_rando.asm")      # code Cemu du pack : jamais dans le mod de contenu
# pack avec du code des lots 6 à 9 porté par le mod natif (rando/natif_code.py) : son patch_rando.asm est COPIÉ dans le
# paquet de contenu sous ce nom (hors de content/ : jamais servi au jeu), pour que le paquet installé se revérifie sans
# le dump (empreinte du manifeste) et que le bloc de code de rando_plan.bin se recalcule depuis lui
CODE_CEMU = "code_cemu.asm"
# code que le mod natif ne refait pas en C (monde ouvert, sans épée, entrées, personnages à immédiat…) : GREFFE, second
# mod de code généré sur ce PC depuis le dump (rando/greffe.py), installé à côté du mod de contenu
# (<réglages>/ModManager/Mods/wwmp-rando-code) ; le mod de contenu garde son empreinte dans ce fichier (à sa racine,
# hors de content/), pour que le lanceur et le client le revérifient sans le dump
GREFFE_JSON = "greffe.json"


def fichier_plan(plan: str) -> bytes:
    """rando_plan.bin (48 octets) : « WWMPRNDF », format, 0, empreinte du plan."""
    if not est_empreinte(plan):
        raise ErreurGenerateur("empreinte du plan invalide")
    return MAGIE_PLAN + struct.pack(">II", FORMAT_PLAN, 0) + bytes.fromhex(plan)


def lire_fichier_plan(data) -> str:
    """Plan porté par un rando_plan.bin (même lecture que le mod : format 1 sans code, format 2 avec le bloc de code
    des lots 6 à 9, rando/natif_code.py), "" sinon."""
    from rando import natif_code as nc
    return nc.lire_fichier_plan(data)[0]


def _lignes_utiles(texte: str) -> list[str]:
    return [ligne.strip() for ligne in texte.splitlines() if ligne.strip() and not ligne.strip().startswith(";")]


def lignes_de_code(msg: dict, rpx) -> int:
    """Lignes de code (lots 6 à 9) que le pack généré pour cette table porterait : 0 = fichiers seuls (portage natif
    possible). ``rpx`` : RPX du dump (rando_lot6.Rpx, lu seulement)."""
    avant, apres = code_pack(table_de(msg), rpx)
    return len(_lignes_utiles(avant)) + len(_lignes_utiles(apres))


def code_non_porte(msg: dict, rpx) -> list[str]:
    """Options de cette table (noms courts, rando/natif_code.NOMS_OPTIONS) dont le code des lots 6 à 9 n'est pas
    encore refait par le mod natif : [] = la partie se joue sur le portage (sans code, ou code porté). ``rpx`` : RPX
    du dump (lu seulement). Une ligne de code qu'aucun fragment connu n'explique : ["?"]."""
    from rando import natif_code as nc
    table = table_de(msg)
    code = code_pack(table, rpx)
    if not _lignes_utiles(code[0]) and not _lignes_utiles(code[1]):
        return []
    try:
        an = nc.analyser(asm_genere("0" * 64, PACK_DEPOT, code))
    except nc.ErreurNatif:
        return ["?"]
    np = nc.non_portes(an)
    if not np:
        return []
    return nc.options_de(np, table.get("options")) or ["?"]


def pack_a_du_code(pack: str, source: str = PACK_DEPOT) -> bool:
    """Le pack généré ``pack`` porte-t-il du code Cemu au-delà de la zone du plan ? (True aussi s'il est illisible.)"""
    try:
        with open(os.path.join(pack, MANIFESTE), encoding="utf-8") as f:
            plan = json.load(f)["plan"]
        with open(os.path.join(pack, "patch_rando.asm"), encoding="utf-8") as f:
            texte = f.read()
        return _lignes_utiles(texte) != _lignes_utiles(asm_genere(plan, source))
    except (OSError, ValueError, KeyError, TypeError, ErreurFormat):
        return True


def manifeste_portage(plan: str) -> dict:
    return {"format_version": 1, "id": ID_NATIF, "name": "WWMP Rando (generated game files)", "version": "1.0.0",
            "game_id": "wwhd", "minimum_manager_version": "1.5.0", "kind": "content", "content_dir": "content",
            "author": "WWMP",
            "description": f"Game files generated on this PC from your own dump for the co-op game {plan[:12]} "
                           "(file formats from TWWHD-Randomizer, SuperDude88, MIT). Never share this folder."}


def _octets_hors_contenu(chemin: str, plan: str, source: str = PACK_DEPOT) -> bytes:
    if chemin == "rules.txt":
        return rules_genere(plan, source).encode("utf-8")
    return asm_genere(plan, source).encode("utf-8")


def verifier_natif(d: str, source: str = PACK_DEPOT) -> dict:
    """Relit un paquet de contenu du portage (installé ou non) : {ok, plan, fichiers, problemes, dossier,
    table_emp}. ``fichiers`` est l'empreinte du pack Cemu équivalent (celle que le serveur compare)."""
    out = {"ok": False, "plan": "", "fichiers": "", "problemes": [], "dossier": d}
    try:
        with open(os.path.join(d, MANIFESTE), encoding="utf-8") as f:
            man = json.load(f)
        with open(os.path.join(d, MANIFESTE_PORTAGE), encoding="utf-8") as f:
            portage = json.load(f)
    except (OSError, ValueError):
        out["problemes"].append("manifestes absents ou illisibles")
        return out
    if not isinstance(man, dict) or not est_empreinte(man.get("plan")) or not isinstance(portage, dict) \
            or portage.get("id") != ID_NATIF or portage.get("kind") != "content":
        out["problemes"].append("manifestes inattendus")
        return out
    plan = man["plan"]
    attendu = {e["chemin"]: e for e in man.get("fichiers") or [] if isinstance(e, dict) and "chemin" in e}
    try:
        vus = {e["chemin"]: e for e in lister_pack(d) if e["chemin"] not in (MANIFESTE_PORTAGE, CODE_CEMU, GREFFE_JSON)}
    except OSError as e:
        out["problemes"].append(f"lecture impossible ({e})")
        return out
    for c in sorted(set(attendu) | set(vus)):
        a = attendu.get(c)
        if c in HORS_CONTENU:
            if c in vus:
                out["problemes"].append(f"en trop : {c}")
            if a is None:
                out["problemes"].append(f"manquant au manifeste : {c}")
                continue
            try:
                brut = _octets_hors_contenu(c, plan, source)
            except (OSError, ErreurFormat) as e:
                out["problemes"].append(f"{c} : {e}")
                continue
            if (len(brut), sha256_octets(brut)) != (a.get("taille"), a.get("sha256")):
                code = _code_cemu(d, a) if c == "patch_rando.asm" else None
                if code is None:
                    out["problemes"].append(f"{c} : pas celui d'un pack sans code, ni la copie {CODE_CEMU} (lots 6 "
                                            "à 9)")
                else:
                    out["code"] = code
        elif c not in vus:
            out["problemes"].append(f"manquant : {c}")
        elif a is None:
            out["problemes"].append(f"en trop : {c}")
        elif (vus[c]["taille"], vus[c]["sha256"]) != (a.get("taille"), a.get("sha256")):
            out["problemes"].append(f"changé : {c}")
    if os.path.exists(os.path.join(d, CODE_CEMU)) and "code" not in out:
        out["problemes"].append(f"en trop : {CODE_CEMU}")
    g = _lire_greffe_json(d)
    besoin = _greffe_requise(out.get("code"))
    if besoin and g is None:
        out["problemes"].append(f"manquant : {GREFFE_JSON} (code greffé)")
    elif g is not None and not besoin:
        out["problemes"].append(f"en trop : {GREFFE_JSON}")
    elif g is not None:
        out["greffe"] = g
    out["plan"] = plan
    out["fichiers"] = empreinte_fichiers(list(attendu.values())) if attendu else ""
    if out["fichiers"] != man.get("empreinte_fichiers"):
        out["problemes"].append("empreinte des fichiers différente du manifeste")
    out["ok"] = not out["problemes"]
    out["table_emp"] = man.get("table_emp")
    if out["ok"]:
        try:
            out["fichier_plan"] = _fichier_plan_natif(plan, out.get("code"), man)
        except ErreurGenerateur as e:
            out["problemes"].append(e.fr)
            out["ok"] = False
    return out


def _code_cemu(d: str, entree: dict) -> str | None:
    """Texte de la copie CODE_CEMU du paquet ``d`` si c'est bien le patch_rando.asm du manifeste (``entree``)."""
    try:
        with open(os.path.join(d, CODE_CEMU), "rb") as f:
            brut = f.read()
    except OSError:
        return None
    if (len(brut), sha256_octets(brut)) != (entree.get("taille"), entree.get("sha256")):
        return None
    try:
        return brut.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _fichier_plan_natif(plan: str, code: str | None, man: dict) -> bytes:
    """rando_plan.bin de ce paquet : format 1 sans code ; format 2 avec le bloc de code tiré de ``code`` (le
    patch_rando.asm du pack Cemu). Lève ErreurGenerateur si ce code n'est pas (encore) refait par le mod natif."""
    from rando import natif_code as nc
    if code is None:
        return fichier_plan(plan)
    try:
        an = nc.analyser(code)
    except nc.ErreurNatif as e:
        raise ErreurGenerateur(f"code du pack généré que le mod natif ne sait pas refaire ({e})",
                               f"generated pack code that the native mod cannot redo ({e})") from None
    np = nc.non_portes(an)
    if np:
        opts = nc.options_de(np, man.get("options_fichiers")) or np
        raise ErreurGenerateur(
            "cette partie demande du code du randomizer que le mod natif ne refait pas encore ("
            + nc.nommer(opts) + " : " + nc.RAISON_NON_PORTE[0] + ") : joue-la avec Cemu (Réglages → Jeu), ou "
            "retire ces options", "this game needs randomizer code that the native mod does not redo yet ("
            + nc.nommer(opts, en=True) + ": " + nc.RAISON_NON_PORTE[1] + "): play it with Cemu (Settings → Game), "
            "or remove these options")
    return nc.fichier_plan(plan, an)


def sha256_octets(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _greffe_requise(code: str | None) -> list[str]:
    """Fragments du code ``code`` (patch_rando.asm du pack Cemu) que porte la greffe ([] : pas de greffe)."""
    if not code:
        return []
    from rando import natif_code as nc
    try:
        return nc.greffes(nc.analyser(code))
    except nc.ErreurNatif:
        return []


def _lire_greffe_json(d: str) -> dict | None:
    try:
        with open(os.path.join(d, GREFFE_JSON), encoding="utf-8") as f:
            g = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(g, dict) or not est_empreinte(g.get("plan")) or not all(
            isinstance(g.get(k), str) and len(g[k]) == 64 for k in ("elf_sha256", "manifeste_sha256")):
        return None
    return g


def dossier_greffe(config: str) -> str:
    return os.path.join(config, "ModManager", "Mods", ID_GREFFE)


def _greffe_a_nous(d: str) -> bool:
    try:
        with open(os.path.join(d, MANIFESTE_PORTAGE), encoding="utf-8") as f:
            m = json.load(f)
        return isinstance(m, dict) and m.get("id") == ID_GREFFE and m.get("kind") == "guest"
    except (OSError, ValueError):
        return False


def etat_greffe(config: str, attendu: dict | None) -> dict:
    """Paquet de la greffe installé : {installe, ok, probleme}. ``attendu`` : le greffe.json du mod de contenu (None :
    aucune greffe attendue ; ok si aucune n'est installée). Lecture seule."""
    d = dossier_greffe(config)
    out = {"installe": os.path.isdir(d), "ok": False, "probleme": "", "dossier": d}
    if attendu is None:
        out["ok"] = not out["installe"]
        out["probleme"] = "" if out["ok"] else "code greffé d'une autre partie installé"
        return out
    try:
        with open(os.path.join(d, "mod.elf"), "rb") as f:
            elf = f.read()
        with open(os.path.join(d, MANIFESTE_PORTAGE), "rb") as f:
            man = f.read()
    except OSError:
        out["probleme"] = "code greffé absent"
        return out
    if (sha256_octets(elf), sha256_octets(man)) != (attendu["elf_sha256"], attendu["manifeste_sha256"]):
        out["probleme"] = "code greffé d'une autre partie ou abîmé"
        return out
    out["ok"] = True
    return out


def chemins_natif(config: str) -> tuple[str, str, str]:
    """(dossier des mods, dossier du paquet de contenu, rando_plan.bin) du portage dont ``config`` est le dossier des
    réglages (``jeu.portage.lire(...)["config"]``)."""
    mm = os.path.join(config, "ModManager")
    return os.path.join(mm, "Mods"), os.path.join(mm, "Mods", ID_NATIF), os.path.join(mm, "Data", ID_MOD_CODE,
                                                                                       FICHIER_PLAN)


def _refus_jeu(jeu_tourne) -> None:
    if jeu_tourne is not None and jeu_tourne():
        raise ErreurGenerateur("ferme le jeu (portage natif) avant d'installer : il lit ses mods au démarrage",
                               "close the game (native port) before installing: it reads its mods at startup")


def _ecrire_atomique(p: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def installer_natif(pack: str, config: str, jeu_tourne=None, source: str = PACK_DEPOT, rpx=None) -> dict:
    """Copie le pack généré ``pack`` en mod de contenu du portage (``<config>/ModManager/Mods/wwmp-rando``) puis écrit
    rando_plan.bin. APPELÉ SEULEMENT par le lanceur, jeu FERMÉ. Refus : jeu ouvert, pack abîmé, pack avec du code
    (lots 6 à 9) que ni le mod natif ni la greffe ne refont, dossier existant qui n'est pas à nous. Dossier préparé
    sous un nom en « . » (ignoré par le jeu) puis renommé ; rando_plan.bin effacé AVANT de changer le paquet et écrit
    APRÈS ; profiles.json pas touché ici (lanceur/natif.py ``activer``). Code greffé (rando/greffe.py) : le
    paquet ``wwmp-rando-code`` est généré
    depuis ``rpx`` (le RPX du dump, obligatoire alors) et installé de la même façon ; sans greffe, celui d'une autre
    partie est retiré. Renvoie {dossier, plan, fichiers, greffe}."""
    _refus_jeu(jeu_tourne)
    v = verifier_pack(pack)
    if not v["ok"]:
        raise ErreurGenerateur("pack généré abîmé (" + "; ".join(v["problemes"][:3]) + ") : génère-le à nouveau",
                               "damaged generated pack (" + "; ".join(v["problemes"][:3]) + "): generate it again")
    code = None
    if pack_a_du_code(pack, source):
        # code des lots 6 à 9 : refait par le mod natif s'il connaît chaque fragment (rando/natif_code.py), sinon refus
        with open(os.path.join(pack, MANIFESTE), encoding="utf-8") as f:
            man = json.load(f)
        with open(os.path.join(pack, "patch_rando.asm"), encoding="utf-8") as f:
            code = f.read()
        _fichier_plan_natif(v["plan"], code, man)
    greffe = None
    if _greffe_requise(code):
        from rando import greffe as rg
        if callable(rpx):
            rpx = rpx()                            # (lecture du dump seulement quand la partie en a besoin)
        if rpx is None:
            raise ErreurGenerateur("cette partie demande du code greffé, généré depuis le dump : indique le dossier du "
                                   "jeu (Réglages)", "this game needs grafted code, generated from the dump: set the "
                                   "game folder (Settings)")
        try:
            greffe = rg.paquet(rpx, code, v["plan"])
        except (rg.ErreurGreffe, ValueError) as e:
            raise ErreurGenerateur(f"code greffé impossible à générer ({e}) : joue cette partie avec Cemu",
                                   f"cannot generate the grafted code ({e}): play this game with Cemu") from None
    mods, dest, f_plan = chemins_natif(config)
    dg = dossier_greffe(config)
    if os.path.islink(dg) or (os.path.exists(dg) and not (os.path.isdir(dg) and _greffe_a_nous(dg))):
        raise ErreurGenerateur(f"{dg} existe et n'est pas le code greffé WWMP : rien n'est remplacé",
                               f"{dg} exists and is not the WWMP grafted code: nothing is replaced")
    if os.path.islink(dest) or (os.path.exists(dest) and not (os.path.isdir(dest) and _est_a_nous(dest))):
        raise ErreurGenerateur(f"{dest} existe et n'est pas un paquet WWMP Rando : rien n'est remplacé",
                               f"{dest} exists and is not a WWMP Rando package: nothing is replaced")
    os.makedirs(mods, exist_ok=True)
    neuf, vieux = os.path.join(mods, f".{ID_NATIF}.neuf"), os.path.join(mods, f".{ID_NATIF}.ancien")
    gneuf, gvieux = os.path.join(mods, f".{ID_GREFFE}.neuf"), os.path.join(mods, f".{ID_GREFFE}.ancien")
    for d in (neuf, vieux, gneuf, gvieux):
        shutil.rmtree(d, ignore_errors=True)
    with open(os.path.join(pack, MANIFESTE), encoding="utf-8") as f:
        entrees = json.load(f)["fichiers"]
    try:
        for e in [{"chemin": MANIFESTE}] + entrees:
            c = e["chemin"]
            if c in HORS_CONTENU:
                continue
            p = os.path.join(neuf, *c.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            shutil.copyfile(os.path.join(pack, *c.split("/")), p)
        _ecrire(os.path.join(neuf, MANIFESTE_PORTAGE),
                json.dumps(manifeste_portage(v["plan"]), indent=1, ensure_ascii=True).encode("utf-8"))
        if code is not None:
            shutil.copyfile(os.path.join(pack, "patch_rando.asm"), os.path.join(neuf, CODE_CEMU))
        if greffe is not None:
            os.makedirs(gneuf)
            for nom in ("manifest.json", "mod.elf"):
                _ecrire(os.path.join(gneuf, nom), greffe[nom])
            _ecrire(os.path.join(neuf, GREFFE_JSON), json.dumps(
                {"plan": v["plan"], "elf_sha256": sha256_octets(greffe["mod.elf"]),
                 "manifeste_sha256": sha256_octets(greffe["manifest.json"]),
                 "fonctions": len(greffe["_greffe"]["fonctions"]), "fragments": greffe["_greffe"]["fragments"]},
                indent=1).encode("utf-8"))
        w = verifier_natif(neuf, source)
        if not w["ok"] or w["fichiers"] != v["fichiers"] or w["plan"] != v["plan"]:
            raise ErreurGenerateur("copie du paquet ratée (" + "; ".join(w["problemes"][:3]) + ") : rien n'est remplacé",
                                   "package copy failed (" + "; ".join(w["problemes"][:3]) + "): nothing is replaced")
        _refus_jeu(jeu_tourne)
        if os.path.exists(f_plan):
            _reessayer(os.remove, f_plan)           # d'abord : pendant le changement, aucun plan n'est annoncé
        # renommages réessayés : sous Windows, un fichier peut être tenu un instant (antivirus, indexation)
        if os.path.isdir(dest):
            _reessayer(os.replace, dest, vieux)
        _reessayer(os.replace, neuf, dest)
        if os.path.isdir(dg):
            _reessayer(os.replace, dg, gvieux)
        if greffe is not None:
            _reessayer(os.replace, gneuf, dg)
        _ecrire_atomique(f_plan, w["fichier_plan"])
    except OSError as e:
        raise ErreurGenerateur(f"installation impossible dans {mods} ({e})",
                               f"cannot install into {mods} ({e})") from None
    finally:
        for d in (neuf, vieux, gneuf, gvieux):
            if os.path.isdir(d):
                _effacer_reste(d)                   # caché du jeu (nom en « . ») ; un reste tenu partira la fois suivante
    return {"dossier": dest, "plan": v["plan"], "fichiers": v["fichiers"],
            "greffe": dg if greffe is not None else None}


ESSAIS_VERROU = 20          # Windows : un fichier tenu un instant (antivirus, indexation) : on réessaie…
PAUSE_VERROU = 0.25         # …toutes les 0,25 s, environ 5 s en tout


def _reessayer(fn, *args):
    """``fn(*args)`` ; sur PermissionError (WinError 32/5 : fichier tenu un instant par un autre processus),
    réessaie ESSAIS_VERROU fois. La dernière erreur remonte."""
    for i in range(ESSAIS_VERROU):
        try:
            return fn(*args)
        except PermissionError:
            if i == ESSAIS_VERROU - 1:
                raise
            time.sleep(PAUSE_VERROU)


def _effacer_reste(d: str) -> bool:
    """Efface ``d`` en réessayant fichier par fichier ; ce qui reste tenu est laissé (nom caché « .…retire », ignoré
    par le jeu, retiré à la prochaine fois). True si tout est parti."""
    def onerror(fn, chemin, _exc):
        try:
            _reessayer(fn, chemin)
        except OSError:
            pass
    shutil.rmtree(d, **({"onexc": onerror} if sys.version_info >= (3, 12) else {"onerror": onerror}))
    return not os.path.exists(d)


def retirer_dossier(d: str) -> None:
    """Retire un de NOS paquets du portage sans jamais le laisser à moitié visible : d'abord RENOMMÉ en
    ``.<nom>.retire`` (caché du jeu ; renommage réessayé si un fichier est tenu un instant), puis effacé. Vu le
    2026-11-15 : « shutil.rmtree » s'arrêtait sur WinError 32 (fichier tenu par l'antivirus) en laissant un paquet
    à moitié effacé. Si le renommage reste impossible, PermissionError remonte (rien n'a été effacé)."""
    parent, nom = os.path.split(os.path.normpath(d))
    for vieux in os.listdir(parent):                     # restes d'une fois précédente
        if vieux.startswith(f".{nom}.") and vieux.endswith(".retire"):
            _effacer_reste(os.path.join(parent, vieux))
    corbeille = os.path.join(parent, f".{nom}.retire")
    n = 1
    while os.path.exists(corbeille):
        n += 1
        corbeille = os.path.join(parent, f".{nom}.{n}.retire")
    _reessayer(os.replace, d, corbeille)
    _effacer_reste(corbeille)


def desinstaller_natif(config: str, jeu_tourne=None) -> bool:
    """Retire le paquet de contenu (s'il est à nous) et rando_plan.bin, jeu fermé. True si quelque chose est retiré."""
    try:
        return _desinstaller_natif(config, jeu_tourne)
    except PermissionError as e:
        raise ErreurGenerateur(f"un autre programme tient un fichier du paquet depuis plus de 5 s (antivirus, "
                               f"explorateur ouvert dessus ?) : rien n'est à moitié retiré, réessaie ({e})",
                               f"another program has held a package file for over 5 s (antivirus, file explorer "
                               f"open on it?): nothing is half removed, try again ({e})") from None


def _desinstaller_natif(config: str, jeu_tourne=None) -> bool:
    _refus_jeu(jeu_tourne)
    _mods, dest, f_plan = chemins_natif(config)
    fait = False
    if os.path.isfile(f_plan):
        _reessayer(os.remove, f_plan)
        fait = True
    dg = dossier_greffe(config)
    if os.path.isdir(dg) and not os.path.islink(dg) and _greffe_a_nous(dg):
        retirer_dossier(dg)
        fait = True
    if os.path.isdir(dest) and not os.path.islink(dest):
        if not _est_a_nous(dest):
            raise ErreurGenerateur(f"{dest} n'est pas un paquet WWMP Rando : rien n'est retiré",
                                   f"{dest} is not a WWMP Rando package: nothing is removed")
        retirer_dossier(dest)
        fait = True
    return fait


def etat_natif(config: str) -> dict:
    """Paquet de contenu installé dans le portage : {installe, plan (manifeste), plan_fichier (rando_plan.bin), ok,
    fichiers, problemes}. Lecture seule."""
    _mods, dest, f_plan = chemins_natif(config)
    out = {"installe": os.path.isdir(dest), "plan": "", "plan_fichier": "", "ok": False, "fichiers": "",
           "problemes": [], "dossier": dest}
    brut = None
    try:
        with open(f_plan, "rb") as f:
            brut = f.read(0x10000)
        out["plan_fichier"] = lire_fichier_plan(brut)
    except OSError:
        pass
    if out["installe"]:
        v = verifier_natif(dest)
        out.update(plan=v["plan"], fichiers=v["fichiers"], problemes=v["problemes"])
        out["code"] = "code" in v
        out["ok"] = v["ok"] and out["plan_fichier"] == v["plan"] and brut == v.get("fichier_plan")
        if v["ok"] and out["plan_fichier"] != v["plan"]:
            out["problemes"].append("rando_plan.bin absent ou d'un autre paquet")
        elif v["ok"] and brut != v.get("fichier_plan"):
            out["problemes"].append("rando_plan.bin : bloc de code différent de celui du paquet")
        out["greffe"] = v.get("greffe") is not None
        g = etat_greffe(os.path.dirname(os.path.dirname(os.path.dirname(dest))), v.get("greffe")) if v["ok"] else None
        if g is not None and not g["ok"]:
            out["ok"] = False
            out["problemes"].append(g["probleme"])
    return out


# ---------------------------------------------------------------------------------------------------------------
# Côté client : zones du pack dans la mémoire du jeu (LECTURE SEULE)
# ---------------------------------------------------------------------------------------------------------------
RECHERCHE = (0x0000_0000, 0x1000_0000)    # codecave + code, comme la boîte aux lettres


def greffe_chargee(mem, plan: str) -> bool | None:
    """Portage natif : la partie du mod demande-t-elle la greffe (mot +0x30 de WWMPNATF, rando_code) et CETTE greffe
    (marque WWMPGRF1 avec ce plan, dans la zone des mods) est-elle chargée ? None : pas de greffe demandée (ou pas le
    portage) ; True / False sinon. Lecture seule."""
    from jeu import natif as jn
    from rando import greffe as rg, natif_code as nc
    zone = getattr(mem, "zone_boite", None)
    if not isinstance(zone, tuple):
        return None
    b = jn.trouver_boite(mem, *zone)
    z = jn.lire_zone(mem, b) if b is not None else None
    if not z or not z.get("rando_code") or not z["rando_code"] & sum(nc.BIT[x] for x in nc.GREFFES):
        return None
    for h in mem.find_bytes(rg.MAGIE, zone[0], zone[1], limit=8):
        m = rg.lire_marque(bytes(mem.read(h, 0x30)))
        if m and m["format"] == rg.FORMAT and m["plan"] == plan:
            return True
    return False


def lire_pack_memoire(mem, plage: tuple[int, int] | None = None) -> dict | None:
    """{format, genere, plan} lus dans la zone WWMPRNDR / WWMPRNDG du pack chargé par Cemu ; None si absente.
    ``mem`` : objet à ``find_bytes(magie, lo, hi, limit)`` et ``read(adresse, n)`` (jeu/cemu.GuestMemory). Portage
    natif : la zone est dans la boîte du mod (``jeu.natif.plage_zones`` ; lot 4, rando_fichiers.c), cherchée là."""
    if plage is None:
        from jeu import natif as jn
        plage = jn.plage_zones(mem) or RECHERCHE
    hits = [h for h in mem.find_bytes(MAGIE_R, plage[0], plage[1], limit=4) if h % 4 == 0]
    if len(hits) != 1:
        return None                     # absente, ou plusieurs (deux packs WWMP_Rando actifs ?) : rien de sûr
    a = hits[0]
    d = bytes(mem.read(a, 0x40))
    if len(d) < 0x40 or d[:8] != MAGIE_R or d[0x10:0x18] != MAGIE_G:
        return None
    fmt = struct.unpack_from(">I", d, 8)[0]
    genere = struct.unpack_from(">I", d, 0x18)[0] == 1
    plan = d[0x20:0x40].hex() if genere else ""
    return {"format": fmt, "genere": genere, "plan": plan, "adresse": a}


# ---------------------------------------------------------------------------------------------------------------
# Serveur : contrôle du hello (pur, testé hors ligne)
# ---------------------------------------------------------------------------------------------------------------
REFUS = {
    "ancien": ("randomizer par fichiers : tes outils sont trop anciens pour cette partie (fichiers du jeu générés). "
               "Mets-les à jour, puis dans le lanceur : Randomizer → Indices → Fichiers du jeu → Récupérer, Générer, Installer, et relance le jeu.",
               "randomizer with files: your tools are too old for this game (generated game files). Update them, "
               "then in the launcher: Randomizer → Hints → Game files → Fetch, Generate, Install, and restart the game."),
    "pack": ("randomizer par fichiers : le pack WWMP_Rando généré n'est pas chargé dans Cemu. Dans le lanceur : "
             "Randomizer → Indices → Fichiers du jeu → Récupérer, Générer, puis Installer, puis relancez le jeu (pack coché dans Cemu).",
             "randomizer with files: the generated WWMP_Rando pack is not loaded in Cemu. In the launcher: "
             "Randomizer → Hints → Game files → Fetch, Generate, then Install, then restart the game (pack ticked in Cemu)."),
    "plan": ("Vos fichiers générés ne correspondent pas à cette partie. Dans le lanceur : Randomizer → Indices → Fichiers du jeu → Récupérer, Générer, "
             "puis Installer, puis relancez le jeu.",
             "Your generated files do not match this game. In the launcher: Randomizer → Hints → Game files → Fetch, Generate, then Install, "
             "then restart the game."),
    "fichiers": ("Vos fichiers générés diffèrent de ceux des autres joueurs (dump différent, outils différents ou "
                 "fichiers abîmés). Dans le lanceur : Randomizer → Indices → Fichiers du jeu → Récupérer, Générer, puis Installer, puis relancez le jeu.",
                 "Your generated files differ from the other players' (different dump, different tools or damaged "
                 "files). In the launcher: Randomizer → Hints → Game files → Fetch, Generate, then Install, then restart the game."),
    "inutile": ("Le pack WWMP_Rando généré est actif dans Cemu, mais cette partie n'en a pas besoin. Dans le "
                "lanceur : Randomizer → Indices → Fichiers du jeu → Désinstaller (ou décoche-le dans Cemu), puis relancez le jeu.",
                "The generated WWMP_Rando pack is active in Cemu, but this game does not need it. In the launcher: "
                "Randomizer → Hints → Game files → Uninstall (or untick it in Cemu), then restart the game."),
}


def controle_hello(r: dict, requis: bool, plan: str | None, fichiers_connus: str | None) -> tuple[str | None, str | None]:
    """(code de refus ou None, empreinte des fichiers à retenir ou None). ``r`` : section « rando » du hello ;
    ``requis`` : la table exige le pack généré ; ``plan`` : empreinte du plan du serveur ; ``fichiers_connus`` :
    empreinte retenue au premier joueur (None : pas encore)."""
    r = r if isinstance(r, dict) else {}
    if not requis:
        rp = r.get("rplan")
        return ("inutile" if isinstance(rp, str) and rp else None), None
    if "rfich" not in r or "rpack" not in r:
        return "ancien", None
    rpack = r.get("rpack")
    if isinstance(rpack, bool) or not isinstance(rpack, int) or rpack < FORMAT_PACK:
        return "pack", None
    if r.get("rplan") != plan:
        return ("pack" if not r.get("rplan") else "plan"), None
    rf = r.get("rfich")
    if not est_empreinte(rf):
        return "fichiers", None
    if fichiers_connus is None:
        return None, rf
    return ("fichiers" if rf != fichiers_connus else None), None


# ---------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--jeu", help="dossier du dump (code, content, meta), LU SEULEMENT")
    ap.add_argument("--table", help="table de la partie (JSON : message « rando » ou réponse « rando_plan »)")
    ap.add_argument("--sortie", default=SORTIE_DEFAUT, help="dossier de sortie (défaut : genere/rando du dépôt)")
    ap.add_argument("--copie-complete", metavar="DOSSIER", help="repli : copie complète du jeu modifiée")
    ap.add_argument("--verifier", metavar="PACK", help="relit un pack généré et donne ses empreintes")
    ap.add_argument("--relever-empreintes", metavar="DUMP", help="(développement) relève empreintes_usa_v0.json")
    ap.add_argument("--vers", help="avec --relever-empreintes : fichier écrit (défaut : celui des outils)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    try:
        if a.relever_empreintes:
            doc = fj_dump.relever(a.relever_empreintes)
            vers = a.vers or fj_dump.FICHIER_EMPREINTES
            verifier_sortie(vers, a.relever_empreintes, [])
            with open(vers, "w", encoding="utf-8", newline="\n") as f:
                json.dump(doc, f, indent=1, sort_keys=True)
                f.write("\n")
            print(f"{len(doc['fichiers'])} empreintes écrites dans {vers}")
            return 0
        if a.verifier:
            v = verifier_pack(a.verifier)
            print(json.dumps(v, indent=1, ensure_ascii=False))
            return 0 if v["ok"] else 1
        if not a.jeu or not a.table:
            ap.error("--jeu et --table sont exigés")
        with open(a.table, encoding="utf-8") as f:
            msg = json.load(f)
        man = generer(a.jeu, msg, a.sortie, progres=print)
        print(f"plan {man['plan']}\nfichiers {man['empreinte_fichiers']}\n{len(man['fichiers'])} fichiers, "
              f"{man['duree']} s")
        if a.copie_complete:
            rpx = copie_complete(a.jeu, man["dossier"], a.copie_complete, progres=print)
            print(f"copie complète : lance Cemu avec -g \"{rpx}\"")
        return 0
    except ErreurGenerateur as e:
        print(f"refus : {e.fr}", file=sys.stderr)
        return 2
    except (OSError, ValueError, ErreurFormat) as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
