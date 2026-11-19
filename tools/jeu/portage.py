"""Le portage natif ZeldaWWHDRecomp INSTALLÉ sur ce PC (lot 7, docs/dev/PORTAGE_NATIF.md §8) : où il est, sa
version, le support des mods de code, ses mods, ses sauvegardes ; installer notre mod (paquet embarqué).

Ce que fait le portage (lu dans ses sources et sa release v0.5.1, rien deviné) :

* release « portable » (zip officiel, ``portable.txt`` à côté du lanceur) : ``<release>/Wind Waker HD.exe``
  (Windows) ou ``<release>/wind-waker-hd`` (Linux), tout le reste dans ``<release>/data`` : ``install.json``
  (version, plateforme, ``code_mods``), ``code-mods-active.json`` (``state``, ``hooks``, ``user_dir``), le jeu
  construit (``bin/wwhd.exe``, ``code-builds/…``), les réglages et les mods dans ``data/user`` ;
  sans ``portable.txt`` (anciennes releases, AppImage) : données dans ``%LOCALAPPDATA%\\WWHD`` /
  ``~/.local/share/wwhd``, réglages dans ``%APPDATA%\\WWHD`` / ``~/.config/wwhd`` ;
* le lanceur démarre le jeu avec ``--save <données>/save`` : la sauvegarde est ``<données>/save/user/cking.sav``
  (et ``cking.sav.backup``), un dossier choisi dans l'onglet Saves n'est PAS pris quand on passe par le lanceur
  (``runtime/src/main.cpp`` : ``--save`` l'emporte) ; un jeu construit avec le randomizer intégré a ses propres
  sauvegardes (``save-randomizer-<empreinte>``) ;
* ``cking.sav`` peut porter, APRÈS les octets du jeu, le « bloc des histoires » des mods (pied de 16 octets
  « WSTB », ``runtime/src/mods/story_block.h``) : ``partie_du_jeu`` le retire quand on a besoin des seuls octets
  du jeu ;
* les mods : ``<réglages>/ModManager/Mods/<id>/`` (``manifest.json`` + fichiers du paquet), relus au démarrage du
  jeu (``packages.cpp`` : ``scan``, un dossier dont le nom est l'``id`` du manifeste ; un nom qui commence par « . »
  est ignoré) ; ``ModManager/profiles.json`` : mods activés par profil et ``native_trust`` = la CONFIRMATION de
  l'utilisateur (« contient du code… »), par mod : empreinte de l'inventaire trié de tous ses fichiers
  (``package_fingerprint``). Un fichier changé = plus de confirmation : le mod reste éteint jusqu'à ce qu'il soit
  rallumé et confirmé de nouveau (dans le jeu, F1 > Mods, ou par le lanceur : ``jeu/profils_portage.py``).

Règles : ici, le lanceur ne fait que COPIER le paquet dans ``Mods/wwmp-natif`` (jeu fermé, ancienne version gardée à
part) et LIRE ``profiles.json`` ; l'allumage et la confirmation de NOS mods (``wwmp-*``, pour l'empreinte exacte
des fichiers installés) sont écrits par ``jeu/profils_portage.py`` seulement (réglage « Allumer et confirmer les
mods WWMP tout seul », lanceur/natif.py ``activer``). Les sauvegardes ne sont que lues ici (l'échange est celui de
Cemu : lanceur/parties.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sys
import time

ID_MOD = "wwmp-natif"
ENV_PRO = "WWHD_PRO_CONTROLLER"           # présente (toute valeur) : Pro Controller, prime sur settings.ini
REGLAGES = "settings.ini"                 # <réglages>/settings.ini : « clé=valeur » par ligne (overlay_sdl.cpp)
ID_PROTOTYPE = "wwmp-fantome-proto"       # prototype « un fantôme qui bouge » : veut aussi le profil 0x1F0
VERSION_MIN = (0, 5, 0)                   # SDK v2 (profils d'acteurs natifs) : catalogue du portage, « minimum 0.5.0 »
VERSION_TESTEE = (0, 5, 1)                # release sur laquelle le mod a été construit et vérifié
LANCEURS = {"win32": ("Wind Waker HD.exe",), "linux": ("wind-waker-hd",)}
NOM_PAQUET = "mod_natif.json"
# paquet de CONTENU des décors (modèles originaux, natif/decors/paquet ; mod natif 0.52.0) : installé à côté de
# wwmp-natif, jamais nécessaire (sans lui, aucun décor n'est dessiné, le reste du mod marche)
ID_DECORS = "wwmp-decors"
NOM_PAQUET_DECORS = "mod_decors.json"             # paquet du mod embarqué sous tools/jeu (liste blanche des mises à jour)
FORMAT_PAQUET = 1
PIED_HISTOIRES = b"WSTB"
GARDER_ANCIENS = 5
PROFONDEUR = 4                            # recherche : <Bureau>/ZeldaWWHDRecomp/_installation/WindWakerHD/<release>
VISITES_MAX = 3000
ID_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ErreurPortage(Exception):
    """Refus propre (fr, en) : rien n'a été changé."""

    def __init__(self, fr: str, en: str | None = None):
        super().__init__(fr)
        self.fr, self.en = fr, en or fr


def _systeme(systeme: str | None = None) -> str:
    s = systeme or sys.platform
    return "win32" if s == "win32" else "linux"


def _lire_json(chemin: str, limite: int = 1 << 20):
    try:
        if os.path.getsize(chemin) > limite:
            return None
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _lire_texte(chemin: str, limite: int = 4096) -> str:
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limite)
    except OSError:
        return ""


def version_tuple(texte) -> tuple[int, ...] | None:
    """« v0.5.1 » (install.json) ou « … port v0.5.1 (windows-x86_64) » (VERSION.txt) -> (0, 5, 1)."""
    m = re.search(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(texte or ""))
    if not m:
        return None
    return tuple(int(x) for x in m.groups(default="0"))


# ---------------------------------------------------------------------------
# Où est le portage
# ---------------------------------------------------------------------------
def est_appimage(chemin: str) -> bool:
    return str(chemin or "").lower().endswith(".appimage")


def dossiers_par_utilisateur(env=None, systeme: str | None = None, appimage: str = "") -> tuple[str, str]:
    """(données, réglages) d'une installation NON portable (releases d'avant 0.2, AppImage) : setup.py
    ``legacy_data_dir`` / ``legacy_config_dirs``, ``host::config_dir`` (``$XDG_DATA_HOME/wwhd``,
    ``$XDG_CONFIG_HOME/wwhd``). ``appimage`` : un dossier « <AppImage>.home » à côté de l'image devient le $HOME du
    jeu (mode portable du runtime AppImage, README du portage) ; les variables XDG gardent la priorité."""
    e = os.environ if env is None else env
    if _systeme(systeme) == "win32":
        loc = e.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        app = e.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(loc, "WWHD"), os.path.join(app, "WWHD")
    maison = e.get("HOME") or os.path.expanduser("~")
    if appimage and os.path.isdir(appimage + ".home"):
        maison = appimage + ".home"
    return (os.path.join(e.get("XDG_DATA_HOME") or os.path.join(maison, ".local", "share"), "wwhd"),
            os.path.join(e.get("XDG_CONFIG_HOME") or os.path.join(maison, ".config"), "wwhd"))


def _lanceur_dans(dossier: str, systeme: str | None = None) -> str:
    for n in LANCEURS[_systeme(systeme)]:
        p = os.path.join(dossier, n)
        if os.path.isfile(p):
            return p
    return ""


def lire(chemin: str, env=None, systeme: str | None = None) -> dict:
    """Ce qu'on sait du portage désigné par ``chemin`` (dossier de la release, son dossier ``data``, ou le lanceur
    lui-même, ou un AppImage). Lecture seule. Dict : ok (bool), fr / en (raison si pas ok), racine, donnees,
    config, lanceur, version (texte), version_t, plateforme, portable, code_mods (True / False / None : inconnu),
    randomizer (empreinte du jeu randomisé intégré, ""), mods, sauvegardes, attention [(fr, en)]."""
    out = {"ok": False, "fr": "", "en": "", "chemin": str(chemin or ""), "racine": "", "donnees": "", "config": "",
           "lanceur": "", "version": "", "version_t": None, "plateforme": "", "portable": False, "code_mods": None,
           "randomizer": "", "mods": "", "sauvegardes": "", "attention": [], "reglage_code_mods": None}
    c = os.path.normpath(str(chemin or "").strip().strip('"')) if str(chemin or "").strip() else ""
    if not c or not os.path.exists(c):
        out["fr"], out["en"] = "dossier introuvable", "folder not found"
        return out
    lanceur = ""
    if os.path.isfile(c):
        lanceur, c = c, os.path.dirname(c)
    donnees = ""
    if os.path.isfile(os.path.join(c, "data", "install.json")):           # dossier de la release
        racine, donnees = c, os.path.join(c, "data")
    elif os.path.isfile(os.path.join(c, "install.json")):                 # son dossier data (ou données par utilisateur)
        donnees = c
        p = os.path.dirname(c)
        racine = p if os.path.basename(c).lower() == "data" and _lanceur_dans(p, systeme) else ""
    else:
        racine = c
        d_util, _cfg = dossiers_par_utilisateur(env, systeme, lanceur if est_appimage(lanceur) else "")
        if (lanceur or _lanceur_dans(c, systeme)) and os.path.isfile(os.path.join(d_util, "install.json")):
            donnees = d_util                                             # AppImage / release non portable
    if not donnees:
        out["fr"] = ("ce n'est pas une installation du portage natif (pas de data/install.json) : lance-le une fois "
                     "(il prépare le jeu depuis ton dump), puis choisis son dossier")
        out["en"] = ("this is not an installation of the native port (no data/install.json): start it once (it "
                     "prepares the game from your dump), then choose its folder")
        return out
    out["racine"], out["donnees"] = racine, donnees
    out["lanceur"] = lanceur or (_lanceur_dans(racine, systeme) if racine else "")
    inst = _lire_json(os.path.join(donnees, "install.json"))
    if not isinstance(inst, dict):
        out["fr"], out["en"] = "install.json illisible", "unreadable install.json"
        return out
    out["portable"] = inst.get("portable") is True or (bool(racine) and not est_appimage(lanceur)
                                                       and os.path.isfile(os.path.join(racine, "portable.txt")))
    texte_version = str(inst.get("version") or "") or _lire_texte(os.path.join(racine, "VERSION.txt")) if racine \
        else str(inst.get("version") or "")
    out["version"] = (version_tuple(texte_version) and "v" + ".".join(map(str, version_tuple(texte_version)))) or ""
    out["version_t"] = version_tuple(texte_version)
    out["plateforme"] = str(inst.get("platform") or "")
    actif = _lire_json(os.path.join(donnees, "code-mods-active.json"))
    user = actif.get("user_dir") if isinstance(actif, dict) and isinstance(actif.get("user_dir"), str) else ""
    if not (user and os.path.isdir(user)):
        image = out["lanceur"] if est_appimage(out["lanceur"]) else ""
        user = os.path.join(donnees, "user") if out["portable"] else dossiers_par_utilisateur(env, systeme, image)[1]
    out["config"] = user
    out["mods"] = os.path.join(user, "ModManager", "Mods")
    # le binaire que le jeu choisit au démarrage (code_mods.cpp ``selected``) : avec le support des mods de code ?
    # (install.json ``code_mods`` reste celui de la PREMIÈRE préparation : une reconstruction ne le change pas)
    from jeu import support_mods
    if support_mods.construit(donnees):
        out["code_mods"] = True
    elif inst.get("code_mods") is False or isinstance(actif, dict):
        out["code_mods"] = False
    out["reglage_code_mods"] = support_mods.reglage(user, _systeme(systeme))
    rnd = inst.get("randomizer") if isinstance(inst.get("randomizer"), dict) else {}
    ident = str(rnd.get("rpx_sha256") or "")
    out["randomizer"] = ident if HEX64.match(ident) else ""
    racine_sav = os.path.join(donnees, "save") + (f"-randomizer-{out['randomizer']}" if out["randomizer"] else "")
    out["sauvegardes"] = os.path.join(racine_sav, "user")
    v = out["version_t"]
    if v is None:
        out["fr"], out["en"] = "version du portage inconnue (install.json)", "unknown port version (install.json)"
        return out
    if v < VERSION_MIN:
        m = ".".join(map(str, VERSION_MIN))
        out["fr"] = f"portage {out['version']} trop ancien : il faut la v{m} ou plus récente (profils d'acteurs natifs)"
        out["en"] = f"port {out['version']} too old: v{m} or newer is needed (native actor profiles)"
        return out
    if not out["lanceur"]:
        out["fr"] = ("lanceur du portage introuvable (« Wind Waker HD.exe » / « wind-waker-hd ») : choisis le dossier "
                     "de la release, ou le fichier lui-même (AppImage)")
        out["en"] = ("the port's launcher was not found (“Wind Waker HD.exe” / “wind-waker-hd”): choose the release "
                     "folder, or the file itself (AppImage)")
        return out
    if v > VERSION_TESTEE:
        t = ".".join(map(str, VERSION_TESTEE))
        out["attention"].append((f"portage {out['version']} : le mod a été construit pour la v{t} (pas encore essayé "
                                 "sur celle-ci)", f"port {out['version']}: the mod was built for v{t} (not tried on "
                                 "this one yet)"))
    if out["code_mods"] is not True:
        out["attention"].append(("support des mods de code pas encore construit : Jouer le construit avec l'outil du "
                                 "portage (environ 5 minutes, une fois)",
                                 "code-mod support not built yet: Play builds it with the port's tool (about 5 "
                                 "minutes, once)"))
    if out["randomizer"]:
        out["attention"].append(("jeu construit avec le randomizer INTÉGRÉ du portage : le mod WWMP suppose le jeu "
                                 "d'origine (sauvegardes à part, compatibilité inconnue)",
                                 "game built with the port's BUILT-IN randomizer: the WWMP mod assumes the original "
                                 "game (separate saves, unknown compatibility)"))
    out["ok"] = True
    return out


def racines_recherche(env=None, systeme: str | None = None) -> list[str]:
    """Dossiers où chercher une release (Bureau, Téléchargements, Documents, Programmes, dossier personnel)."""
    e = os.environ if env is None else env
    h = e.get("USERPROFILE") or e.get("HOME") or os.path.expanduser("~")
    out = [os.path.join(h, d) for d in ("Desktop", "Bureau", "Downloads", "Téléchargements", "Documents", "Games",
                                         "Jeux", "Applications")]
    if _systeme(systeme) == "win32":
        out += [e.get("ProgramFiles", ""), e.get("ProgramFiles(x86)", ""),
                os.path.join(e.get("LOCALAPPDATA", ""), "Programs") if e.get("LOCALAPPDATA") else ""]
    out.append(h)
    vus, res = set(), []
    for d in out:
        if d and os.path.normcase(d) not in vus:
            vus.add(os.path.normcase(d))
            res.append(d)
    return res


# WindWakerHD-v0.5.1-linux-x86_64.AppImage (nom de la release ; renommé : « Wind_Waker_HD-x86_64.AppImage »…)
APPIMAGE_RE = re.compile(r"^wind[ _.-]?waker[ _.-]?hd.*\.appimage$", re.IGNORECASE)


def _chercher(racine: str, systeme: str | None, profondeur: int = PROFONDEUR) -> list[str]:
    """Releases sous ``racine`` (un lanceur ET data/install.json ; Linux : aussi les fichiers
    « WindWakerHD-….AppImage »), bornées en profondeur et en visites."""
    out, pile, n = [], [(racine, 0)], 0
    linux = _systeme(systeme) != "win32"
    while pile and n < VISITES_MAX:
        d, k = pile.pop()
        n += 1
        if os.path.isfile(os.path.join(d, "data", "install.json")) and _lanceur_dans(d, systeme):
            out.append(d)
            continue
        try:
            noms = sorted(os.listdir(d), reverse=True)
        except OSError:
            continue
        if linux:
            out += [os.path.join(d, nom) for nom in sorted(noms) if APPIMAGE_RE.match(nom)
                    and os.path.isfile(os.path.join(d, nom))]
        if k >= profondeur:
            continue
        for nom in noms:
            if nom.startswith((".", "$")) or nom.lower() in ("windows", "system volume information", "node_modules",
                                                              "appdata", "data", "game", "code-objects"):
                continue
            p = os.path.join(d, nom)
            if os.path.isdir(p) and not os.path.islink(p):
                pile.append((p, k + 1))
    return out


def candidats(choisi: str = "", racines: list[str] | None = None, env=None, systeme: str | None = None) -> list[dict]:
    """Installations trouvées ({chemin, source, ok, version, fr, en}), celles qui conviennent d'abord. Lecture seule.
    ``racines`` : None = dossiers habituels de ce PC (les essais passent les leurs)."""
    vus, out = set(), []

    def ajouter(c, source):
        info = lire(c, env, systeme)
        cle = os.path.normcase(os.path.abspath(info["lanceur"] if est_appimage(info["lanceur"]) else
                                               (info["racine"] or info["donnees"] or c)))
        if cle in vus or not (info["racine"] or info["donnees"]):
            return
        vus.add(cle)
        appimage = est_appimage(info["lanceur"])            # l'AppImage elle-même (son dossier n'est pas le portage)
        out.append({"chemin": info["lanceur"] if appimage else (info["racine"] or info["lanceur"] or c),
                    "source": source, "ok": info["ok"], "version": info["version"], "fr": info["fr"],
                    "en": info["en"]})
    if choisi:
        ajouter(choisi, "reglages")
    for r in (racines_recherche(env, systeme) if racines is None else racines):
        for d in _chercher(r, systeme):
            ajouter(d, "dossier")
    d_util, _cfg = dossiers_par_utilisateur(env, systeme)
    if racines is None and os.path.isfile(os.path.join(d_util, "install.json")):
        ajouter(d_util, "utilisateur")
    out.sort(key=lambda x: not x["ok"])
    return out


# ---------------------------------------------------------------------------
# Mods (lecture : profiles.json ; écriture : seulement Mods/<id>, jeu fermé)
# ---------------------------------------------------------------------------
def empreinte_dossier(dossier: str) -> str:
    """``package_fingerprint`` du portage : sha256 de « chemin\\0sha256\\n » de chaque fichier, triés."""
    inv = {}
    for d, sous, fichiers in os.walk(dossier):
        sous.sort()
        for n in fichiers:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                inv[os.path.relpath(p, dossier).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    return empreinte_inventaire(inv)


def empreinte_inventaire(inv: dict[str, str]) -> str:
    texte = "".join(f"{k}\0{inv[k]}\n" for k in sorted(inv, key=lambda s: s.encode("utf-8")))
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


def empreinte_fichiers(fichiers: dict[str, bytes]) -> str:
    return empreinte_inventaire({k: hashlib.sha256(v).hexdigest() for k, v in fichiers.items()})


def profils(inst: dict) -> dict | None:
    if not inst.get("config"):
        return None
    p = _lire_json(os.path.join(inst["config"], "ModManager", "profiles.json"), 4 << 20)
    return p if isinstance(p, dict) else None


def etat_mod(inst: dict, ident: str = ID_MOD, paquet: dict | None = None) -> dict:
    """Le mod ``ident`` dans ce portage : installe, version, empreinte, a_jour (mêmes fichiers que ``paquet``),
    active (allumé dans le profil actif), confirme (confirmation de code donnée DANS LE JEU pour ces fichiers-ci),
    probleme (manifeste illisible…). Lecture seule."""
    out = {"id": ident, "installe": False, "version": "", "empreinte": "", "a_jour": None, "active": False,
           "confirme": False, "probleme": "", "dossier": ""}
    if not inst.get("mods") or not ID_RE.match(ident):
        return out
    d = os.path.join(inst["mods"], ident)
    out["dossier"] = d
    if not os.path.isdir(d):
        return out
    out["installe"] = True
    m = _lire_json(os.path.join(d, "manifest.json"))
    if not isinstance(m, dict) or m.get("id") != ident:
        out["probleme"] = "manifest.json illisible ou d'un autre mod"
    else:
        out["version"] = str(m.get("version") or "")
    try:
        out["empreinte"] = empreinte_dossier(d)
    except OSError as e:
        out["probleme"] = out["probleme"] or f"fichiers illisibles ({e.__class__.__name__})"
    if paquet is not None:
        out["a_jour"] = out["empreinte"] == paquet.get("empreinte")
    pr = profils(inst) or {}
    nom = pr.get("active") if isinstance(pr.get("active"), str) else "Default"
    prof = (pr.get("profiles") or {}).get(nom) if isinstance(pr.get("profiles"), dict) else None
    en = prof.get("enabled") if isinstance(prof, dict) and isinstance(prof.get("enabled"), dict) else {}
    out["active"] = en.get(ident) is True
    conf = pr.get("native_trust") if isinstance(pr.get("native_trust"), dict) else {}
    out["confirme"] = bool(out["empreinte"]) and conf.get(ident) == out["empreinte"]
    return out


def chemin_paquet_embarque() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), NOM_PAQUET)


def chemin_paquet_decors() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), NOM_PAQUET_DECORS)


def decors_servis(inst: dict) -> list[str]:
    """Les modèles du paquet ``wwmp-decors`` que le portage SERT en ce moment (le client ne demande que ceux-là : un
    décor dont l'archive n'est pas servie fait chercher au jeu un fichier qui n'existe pas, et le jeu s'arrête) :
    wwmp-decors et le carnet (story-notebook, sans lui le système des modèles de mods est éteint) installés et
    allumés, et chaque modèle porté par un acteur déclaré (le portage ne sert l'archive que de ceux-là). [] au moindre
    doute. Lecture seule."""
    try:
        d = etat_mod(inst, ID_DECORS)
        n = etat_mod(inst, "story-notebook")
        if not (d["installe"] and d["active"] and not d["probleme"] and n["installe"] and n["active"]):
            return []
        m = _lire_json(os.path.join(d["dossier"], "manifest.json"))
        if not isinstance(m, dict):
            return []
        portes = {a.get("model") for a in m.get("actors") or [] if isinstance(a, dict) and a.get("kind") == "mod"
                  and isinstance(a.get("collision"), dict)}
        return sorted(x["id"] for x in m.get("models") or [] if isinstance(x, dict) and isinstance(x.get("id"), str)
                      and x["id"] in portes and os.path.isfile(os.path.join(d["dossier"], str(x.get("source")))))
    except (OSError, KeyError, TypeError, ValueError):
        return []


def lire_paquet(chemin: str | None = None) -> dict:
    """Paquet du mod embarqué (``tools/jeu/mod_natif.json``, fabriqué par ``outils/paquet_natif.py`` depuis
    ``natif/mod_wwmp/paquet``) : {id, version, empreinte, fichiers: {nom: octets}}, chaque fichier vérifié
    (sha256, taille). Lève ErreurPortage."""
    doc = _lire_json(chemin or chemin_paquet_embarque(), 8 << 20)
    if not isinstance(doc, dict) or doc.get("format") != FORMAT_PAQUET or not isinstance(doc.get("fichiers"), dict):
        raise ErreurPortage("paquet du mod natif absent ou illisible (tools/jeu/mod_natif.json)",
                            "native mod package missing or unreadable (tools/jeu/mod_natif.json)")
    ident = doc.get("id")
    if not isinstance(ident, str) or not ID_RE.match(ident) or ident.startswith("."):
        raise ErreurPortage("paquet du mod natif : identifiant invalide", "native mod package: invalid id")
    fichiers = {}
    for nom, f in doc["fichiers"].items():
        if not isinstance(nom, str) or not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$", nom) \
                or not isinstance(f, dict):
            raise ErreurPortage(f"paquet du mod natif : nom de fichier refusé {str(nom)[:40]!r}")
        try:
            data = base64.b64decode(str(f.get("base64") or ""), validate=True)
        except ValueError:
            raise ErreurPortage(f"paquet du mod natif : {nom} illisible") from None
        if len(data) != f.get("taille") or hashlib.sha256(data).hexdigest() != f.get("sha256"):
            raise ErreurPortage(f"paquet du mod natif : {nom} abîmé (empreinte)",
                                f"native mod package: {nom} damaged (checksum)")
        fichiers[nom] = data
    if "manifest.json" not in fichiers:
        raise ErreurPortage("paquet du mod natif : pas de manifest.json")
    try:
        m = json.loads(fichiers["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ErreurPortage("paquet du mod natif : manifest.json illisible") from None
    if not isinstance(m, dict) or m.get("id") != ident:
        raise ErreurPortage("paquet du mod natif : manifest.json d'un autre mod")
    emp = empreinte_fichiers(fichiers)
    if doc.get("empreinte") not in (None, emp):
        raise ErreurPortage("paquet du mod natif : empreinte d'ensemble fausse")
    return {"id": ident, "version": str(m.get("version") or ""), "nom": str(m.get("name") or ident),
            "empreinte": emp, "fichiers": fichiers}


def fabriquer_paquet(dossier: str) -> dict:
    """Document de ``mod_natif.json`` depuis un dossier de paquet (``natif/mod_wwmp/paquet``). Les fins de ligne de
    manifest.json sont ramenées à LF (git peut le sortir en CRLF sous Windows : même empreinte partout)."""
    fichiers = {}
    for n in sorted(os.listdir(dossier)):
        p = os.path.join(dossier, n)
        if not os.path.isfile(p):
            raise ErreurPortage(f"paquet : sous-dossier inattendu {n}")
        with open(p, "rb") as f:
            data = f.read()
        if n.lower().endswith((".json", ".md", ".txt")):
            data = data.replace(b"\r\n", b"\n")
        fichiers[n] = data
    m = json.loads(fichiers["manifest.json"].decode("utf-8"))
    return {"format": FORMAT_PAQUET, "id": m["id"], "version": m.get("version", ""),
            "source": "natif/mod_wwmp/paquet", "empreinte": empreinte_fichiers(fichiers),
            "fichiers": {n: {"taille": len(d), "sha256": hashlib.sha256(d).hexdigest(),
                             "base64": base64.b64encode(d).decode("ascii")} for n, d in fichiers.items()}}


def _refuser_si_jeu(jeu_tourne) -> None:
    if jeu_tourne is not None and jeu_tourne():
        raise ErreurPortage("le jeu (portage natif) est ouvert : ferme-le d'abord (il garde ses mods ouverts)",
                            "the game (native port) is open: close it first (it keeps its mods open)")


def _copier_arbre_verifie(src: str, dst: str) -> None:
    shutil.copytree(src, dst)
    if empreinte_dossier(src) != empreinte_dossier(dst):
        shutil.rmtree(dst, ignore_errors=True)
        raise ErreurPortage(f"copie de {src} différente de l'original", f"copy of {src} differs from the original")


def _garder_ancien(dossier: str, base_anciens: str, ident: str) -> str:
    """Copie VÉRIFIÉE de ``dossier`` dans ``base_anciens/<id>-<date>`` ; garde les GARDER_ANCIENS plus récentes."""
    os.makedirs(base_anciens, exist_ok=True)
    nom = f"{ident}-{time.strftime('%Y%m%d-%H%M%S')}"
    dst, k = os.path.join(base_anciens, nom), 1
    while os.path.exists(dst):
        k += 1
        dst = os.path.join(base_anciens, f"{nom}-{k}")
    _copier_arbre_verifie(dossier, dst)
    anciens = sorted((n for n in os.listdir(base_anciens) if n.startswith(ident + "-")
                      and os.path.isdir(os.path.join(base_anciens, n))), reverse=True)
    for n in anciens[GARDER_ANCIENS:]:
        shutil.rmtree(os.path.join(base_anciens, n), ignore_errors=True)
    return dst


def installer_paquet(inst: dict, paquet: dict, jeu_tourne=None, base_anciens: str | None = None) -> dict:
    """Copie le paquet dans ``Mods/<id>`` (jeu FERMÉ). Déjà les mêmes fichiers : rien. Sinon le dossier actuel est
    d'abord copié (vérifié) dans ``base_anciens``, puis remplacé d'un coup (dossier préparé à côté sous un nom en
    « . », ignoré par le jeu, puis renommé). ``profiles.json`` n'est pas touché ici : la confirmation de code de
    l'ancienne version ne vaut plus pour ces fichiers (``jeu/profils_portage.py`` la redonne pour les nouveaux, ou
    le jeu la redemande). Renvoie {fait: "installe"|"mis_a_jour"|"deja", ancien,
    dossier}. Lève ErreurPortage."""
    if not inst.get("mods"):
        raise ErreurPortage("dossier des mods du portage inconnu", "the port's mods folder is unknown")
    _refuser_si_jeu(jeu_tourne)
    ident = paquet["id"]
    if not ID_RE.match(ident) or ident.startswith("."):
        raise ErreurPortage("identifiant de mod refusé")
    mods = inst["mods"]
    cible = os.path.join(mods, ident)
    avant = etat_mod(inst, ident, paquet)
    if avant["installe"] and avant["a_jour"]:
        return {"fait": "deja", "ancien": "", "dossier": cible}
    if os.path.islink(cible) or (os.path.exists(cible) and not os.path.isdir(cible)):
        raise ErreurPortage(f"{cible} n'est pas un dossier ordinaire : rien n'est touché")
    os.makedirs(mods, exist_ok=True)
    neuf = os.path.join(mods, f".{ident}.neuf")
    vieux = os.path.join(mods, f".{ident}.ancien")
    for d in (neuf, vieux):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(neuf)
    try:
        for n, data in paquet["fichiers"].items():
            with open(os.path.join(neuf, n), "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        if empreinte_dossier(neuf) != paquet["empreinte"]:
            raise ErreurPortage("vérification du paquet écrit ratée (disque plein ?) : rien n'est remplacé")
        ancien = ""
        if avant["installe"]:
            if base_anciens:
                ancien = _garder_ancien(cible, base_anciens, ident)
            _refuser_si_jeu(jeu_tourne)
            os.replace(cible, vieux)
        try:
            os.replace(neuf, cible)
        except OSError:
            if os.path.isdir(vieux) and not os.path.exists(cible):
                os.replace(vieux, cible)                   # remis tel quel
            raise
    finally:
        shutil.rmtree(neuf, ignore_errors=True)
    shutil.rmtree(vieux, ignore_errors=True)
    return {"fait": "mis_a_jour" if avant["installe"] else "installe", "ancien": ancien, "dossier": cible}


def retirer_mod(inst: dict, ident: str, base_anciens: str, jeu_tourne=None) -> str:
    """Retire ``Mods/<ident>`` (jeu fermé) APRÈS une copie vérifiée dans ``base_anciens`` (rien n'est perdu : la
    remettre suffit). Le jeu oublie de lui-même le mod disparu ; ``profiles.json`` n'est pas touché. Renvoie le
    dossier de la copie. Lève ErreurPortage."""
    if not inst.get("mods") or not ID_RE.match(ident) or ident.startswith("."):
        raise ErreurPortage("mod inconnu")
    _refuser_si_jeu(jeu_tourne)
    d = os.path.join(inst["mods"], ident)
    if not os.path.isdir(d) or os.path.islink(d):
        raise ErreurPortage(f"le mod {ident} n'est pas installé", f"the mod {ident} is not installed")
    copie = _garder_ancien(d, base_anciens, ident)
    vieux = os.path.join(inst["mods"], f".{ident}.retire")
    shutil.rmtree(vieux, ignore_errors=True)
    os.replace(d, vieux)
    shutil.rmtree(vieux, ignore_errors=True)
    return copie


# ---------------------------------------------------------------------------
# Sauvegardes et lancement
# ---------------------------------------------------------------------------
def partie_du_jeu(data: bytes) -> bytes:
    """Les octets du jeu d'un ``cking.sav`` du portage : sans le bloc des histoires des mods (pied « WSTB » : magie,
    longueur u32, version u16, 0 u16, CRC-32 u32 ; ``story_block.h``). Un pied qui ne tient pas dans le fichier n'en
    est pas un (le portage fait pareil)."""
    if len(data) < 16 or data[-16:-12] != PIED_HISTOIRES:
        return data
    n = int.from_bytes(data[-12:-8], "big")
    if n + 16 > len(data):
        return data
    return data[:len(data) - 16 - n]


def copie_securite(dossier: str, base: str, raison: str, garder: int = 10, prefixe: str = "cking") -> str:
    """Copie VÉRIFIÉE (sha256) des ``cking*`` de ``dossier`` dans ``base/<date>_<raison>`` ; garde les ``garder``
    plus récentes. Renvoie la copie ("" si aucun fichier)."""
    try:
        noms = sorted(n for n in os.listdir(dossier) if n.lower().startswith(prefixe)
                      and not n.lower().endswith(".tmp") and os.path.isfile(os.path.join(dossier, n)))
    except OSError:
        return ""
    if not noms:
        return ""
    os.makedirs(base, exist_ok=True)
    nom = f"{time.strftime('%Y%m%d-%H%M%S')}_{raison}"
    dst, k = os.path.join(base, nom), 1
    while os.path.exists(dst):
        k += 1
        dst = os.path.join(base, f"{nom}-{k}")
    tmp = dst + ".partiel"
    os.makedirs(tmp)
    try:
        for n in noms:
            shutil.copyfile(os.path.join(dossier, n), os.path.join(tmp, n))
            with open(os.path.join(dossier, n), "rb") as a, open(os.path.join(tmp, n), "rb") as b:
                if hashlib.sha256(a.read()).digest() != hashlib.sha256(b.read()).digest():
                    raise ErreurPortage(f"copie de {n} différente de l'original (fichier en cours d'écriture ?)")
        os.replace(tmp, dst)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    copies = sorted((n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n))
                     and not n.endswith(".partiel")), reverse=True)
    for n in copies[garder:]:
        shutil.rmtree(os.path.join(base, n), ignore_errors=True)
    return dst


def manette_gamepad(inst: dict, base_copies: str, jeu_tourne=None) -> bool:
    """Ramène au Wii U GamePad le choix sauvegardé du menu Input du jeu (``proController`` de
    ``<réglages>/settings.ini``, lu au démarrage quand ``WWHD_PRO_CONTROLLER`` est absente : ``overlay.cpp``).
    Absent ou déjà « 0 » : rien (le GamePad est le défaut du jeu). Sinon SEULE cette ligne devient
    ``proController=0`` (le reste du fichier gardé octet pour octet), copie de l'ancien dans ``base_copies``,
    fichier temporaire puis remplacement, jeu FERMÉ. True si écrit. Lève ErreurPortage."""
    if not inst.get("config"):
        return False
    p = os.path.join(inst["config"], REGLAGES)
    try:
        with open(p, "rb") as f:
            brut = f.read(1 << 20)
    except FileNotFoundError:
        return False
    except OSError as e:
        raise ErreurPortage(f"settings.ini illisible ({e.__class__.__name__})") from None
    lignes = brut.split(b"\n")
    idx = [i for i, ligne in enumerate(lignes) if ligne.rstrip(b"\r").startswith(b"proController=")]
    if not idx or all(lignes[i].rstrip(b"\r") == b"proController=0" for i in idx):
        return False
    _refuser_si_jeu(jeu_tourne)
    for i in idx:
        fin = b"\r" if lignes[i].endswith(b"\r") else b""
        lignes[i] = b"proController=0" + fin
    neuf = b"\n".join(lignes)
    os.makedirs(base_copies, exist_ok=True)
    copie = os.path.join(base_copies, f"settings-{time.strftime('%Y%m%d-%H%M%S')}.ini")
    with open(copie, "wb") as f:
        f.write(brut)
    tmp = p + ".wwmp.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(neuf)
            f.flush()
            os.fsync(f.fileno())
        _refuser_si_jeu(jeu_tourne)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    anciens = sorted((n for n in os.listdir(base_copies) if n.startswith("settings-")), reverse=True)
    for n in anciens[10:]:
        try:
            os.remove(os.path.join(base_copies, n))
        except OSError:
            pass
    return True


def argv_lancement(inst: dict) -> tuple[list[str], str]:
    """(argv, dossier courant) pour démarrer le jeu par SON lanceur (qui passe --game et --save lui-même)."""
    exe = inst.get("lanceur") or ""
    if not exe or not os.path.isfile(exe):
        raise ErreurPortage("lanceur du portage introuvable", "the port's launcher was not found")
    return [exe], os.path.dirname(exe)


def resume(inst: dict, mod: dict | None = None, proto: dict | None = None, langue: str = "fr") -> list[str]:
    """Lignes lisibles (Réglages, Console) : version, mods de code, état du mod WWMP, prototype."""
    en = langue == "en"
    if not inst.get("ok"):
        return [inst.get("en" if en else "fr") or "?"]
    out = [(f"Native port {inst['version']} ({inst['plateforme'] or '?'}), "
            f"{'portable' if inst['portable'] else 'per-user folders'}") if en else
           (f"Portage natif {inst['version']} ({inst['plateforme'] or '?'}), "
            f"{'portable' if inst['portable'] else 'dossiers par utilisateur'}")]
    out += [a[1] if en else a[0] for a in inst.get("attention") or ()]
    if mod is not None:
        if not mod["installe"]:
            out.append("WWMP mod not installed yet (Play installs it)" if en else
                       "mod WWMP pas encore installé (Jouer l'installe)")
        else:
            etat = ("on" if mod["active"] else "off") if en else ("allumé" if mod["active"] else "éteint")
            conf = ("confirmed" if mod["confirme"] else "to confirm (Play does it)") if en else \
                ("confirmé" if mod["confirme"] else "à confirmer (Jouer le fait)")
            maj = "" if mod["a_jour"] is not False else (", older than the launcher's" if en else
                                                          ", plus ancien que celui du lanceur")
            out.append((f"WWMP mod {mod['version']}: {etat}, {conf}{maj}") if en else
                       (f"mod WWMP {mod['version']} : {etat}, {conf}{maj}"))
    if proto is not None and proto["installe"]:
        out.append("prototype “wwmp-fantome-proto” installed: remove it (both want actor profile 0x1F0)" if en else
                   "prototype « wwmp-fantome-proto » installé : retire-le (les deux veulent le profil 0x1F0)")
    return out
