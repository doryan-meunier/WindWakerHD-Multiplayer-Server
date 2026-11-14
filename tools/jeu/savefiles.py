"""A15 : fichiers de sauvegarde de Cemu et copies de sécurité (docs/etudes/rejoindre.md §7, §8).

Les originaux ne sont JAMAIS modifiés ni déplacés, sauf par ``restaurer_fichiers`` (annulation
explicite, Cemu fermé), qui fait d'abord une copie « avant_annulation » et remplace chaque
fichier par écriture dans ``nom.tmp``, vérification sha256, puis ``os.replace``.

Copies : ``%LOCALAPPDATA%\\WWMP\\sauvegardes\\AAAAMMJJ-HHMMSS_<raison>\\`` :
  user_<id>\\…     tout le dossier ``…\\usr\\save\\00050000\\10143500\\user\\<id>\\``
  meta\\meta.xml   pour information
  <fichiers>      gi_avant.bin, image_hote.bin… (fournis par l'appelant)
  infos.json      date, raison, compte, sha256 de chaque fichier, « origine »…

A16 (menu Coop, docs/archives/menu_coop.md §8.3) : deux JEUX de sauvegardes dans le même dossier
de compte, ``cking*`` (solo) et ``ckcop*`` (coop). Les copies de sécurité du menu ont une base
PAR JEU (``sauvegardes\\solo\\``, ``sauvegardes\\coop\\``), chacune avec sa rotation et sa copie
« origine » ; une copie peut ne prendre que les fichiers d'un jeu (``prefixe``) et la restauration
peut être filtrée de même (jamais un ``ckcop*`` remis par une restauration solo, et inversement).
Les copies A15 (sans jeu) restent à la racine, comme avant.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET

from jeu import plateforme

TITLE_PATH = ("usr", "save", "00050000", "10143500")
GARDER = 10                        # copies gardées (plus la toute première de chaque compte)
NOM_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?_[a-z_]+$")
JEUX = {"solo": "cking", "coop": "ckcop"}   # A16 : préfixe des fichiers de chaque jeu


def prefixe_jeu(jeu: str | None) -> str:
    """« solo » -> « cking », « coop » -> « ckcop » (None -> « cking », comportement d'avant)."""
    if jeu is None:
        return "cking"
    try:
        return JEUX[jeu]
    except KeyError:
        raise ValueError(f"jeu de sauvegardes inconnu : {jeu} (solo ou coop)") from None


def fichier_principal(jeu: str | None = None) -> str:
    """Nom du fichier des 3 Journaux : cking.sav (solo) ou ckcop.sav (coop)."""
    return prefixe_jeu(jeu) + ".sav"


def du_jeu(nom: str, prefixe: str) -> bool:
    """``nom`` appartient au jeu ``prefixe`` : « cking.sav », « cking_pic0_1.sav »… mais jamais
    « ckcop… » pour « cking » (ni « ckingX.sav », ni un « .tmp »)."""
    n = os.path.basename(nom).lower()
    p = prefixe.lower()
    return n.startswith(p) and len(n) > len(p) and n[len(p)] in "._" and not n.endswith(".tmp")


def sha256_fichier(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Où sont les sauvegardes (§7.2)
# ---------------------------------------------------------------------------
def fichier_settings(appdata: str | None = None, cemu_exe: str | None = None) -> str:
    """settings.xml de Cemu : mode portable (dossier ``portable`` à côté de Cemu.exe), sinon
    ``%APPDATA%\\Cemu\\settings.xml``. Linux : voir ``jeu/plateforme.dossiers_cemu`` (~/.config/Cemu,
    ~/.local/share/Cemu, Flatpak ~/.var/app/info.cemu.Cemu/config/Cemu, portable à côté de l'AppImage)."""
    if not plateforme.est_windows():
        return os.path.join(plateforme.dossier_cemu(cemu_exe, appdata)[0], "settings.xml")
    appdata = appdata if appdata is not None else os.environ.get("APPDATA", "")
    if cemu_exe:
        port = os.path.join(os.path.dirname(cemu_exe), "portable")
        if os.path.isdir(port):
            return os.path.join(port, "settings.xml")
    return os.path.join(appdata, "Cemu", "settings.xml")


def chemin_mlc(appdata: str | None = None, cemu_exe: str | None = None) -> str:
    """``mlc_path`` de settings.xml ; sinon mode portable (dossier ``portable`` à côté de
    Cemu.exe) ; sinon ``%APPDATA%\\Cemu\\mlc01``. Linux : ``mlc01`` du dossier des DONNÉES de Cemu
    (~/.local/share/Cemu/mlc01, Flatpak …/data/Cemu/mlc01)."""
    conf = fichier_settings(appdata, cemu_exe)
    mlc = _mlc_settings(conf)
    if not plateforme.est_windows():
        return mlc or os.path.join(plateforme.dossier_cemu(cemu_exe, appdata)[1], "mlc01")
    return mlc or os.path.join(os.path.dirname(conf), "mlc01")


def dossier_graphicpacks(cemu_exe: str | None = None, appdata: str | None = None) -> str:
    """graphicPacks de Cemu : ``portable\\graphicPacks`` à côté de Cemu.exe, sinon ``%APPDATA%\\Cemu\\graphicPacks``
    (Windows, comme avant) ; Linux : dans le dossier des données de Cemu (~/.local/share/Cemu/graphicPacks,
    Flatpak ~/.var/app/info.cemu.Cemu/data/Cemu/graphicPacks, ou portable)."""
    if plateforme.est_windows():
        if cemu_exe:
            port = os.path.join(os.path.dirname(cemu_exe), "portable")
            if os.path.isdir(port):
                return os.path.join(port, "graphicPacks")
        a = appdata if appdata is not None else os.environ.get("APPDATA", "")
        return os.path.join(a, "Cemu", "graphicPacks")
    return os.path.join(plateforme.dossier_cemu(cemu_exe, appdata)[1], "graphicPacks")


def compte_actif(appdata: str | None = None, cemu_exe: str | None = None) -> str | None:
    """Compte Cemu actif : ``<Account><PersistentId>`` de settings.xml (décimal) -> nom du
    dossier ``user\\<id>`` (8 chiffres hexadécimaux, ex. 2147483649 -> 80000001). None sinon."""
    try:
        root = ET.parse(fichier_settings(appdata, cemu_exe)).getroot()
    except (OSError, ET.ParseError):
        return None
    el = root.find(".//Account/PersistentId")
    try:
        v = int((el.text or "").strip(), 0) if el is not None else None
    except ValueError:
        return None
    return f"{v:08x}" if v is not None and 0 < v <= 0xFFFFFFFF else None


def _mlc_settings(path: str) -> str | None:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    el = root.find(".//mlc_path")
    v = (el.text or "").strip() if el is not None else ""
    return v or None


def dossier_jeu(mlc: str) -> str:
    return os.path.join(mlc, *TITLE_PATH)


def comptes(racine: str, jeu: str | None = None) -> list[tuple[str, str, float]]:
    """[(id, dossier, date de cking.sav)] sous ``racine\\user``, plus récent d'abord.
    ``jeu="coop"`` : comptes qui ont un ckcop.sav (date de ckcop.sav)."""
    principal = fichier_principal(jeu)
    user = os.path.join(racine, "user")
    out = []
    try:
        noms = os.listdir(user)
    except OSError:
        return []
    for n in noms:
        d = os.path.join(user, n)
        f = os.path.join(d, principal)
        if n != "common" and os.path.isfile(f):
            out.append((n, d, os.path.getmtime(f)))
    out.sort(key=lambda x: -x[2])
    return out


def choisir_compte(racine: str, compte: str | None = None, actif: str | None = None,
                   jeu: str | None = None) -> tuple[str, str]:
    """(id, dossier). ``compte`` (--compte) ; sinon ``actif`` (compte chargé dans Cemu, voir
    ``compte_actif``) s'il a une sauvegarde ; sinon le cking.sav le plus récent (plusieurs de
    moins d'un jour : --compte exigé). ``jeu="coop"`` : mêmes règles sur ckcop.sav ; si aucun
    compte n'a encore de ckcop.sav, on retombe sur les comptes qui ont un cking.sav.
    A16 (revue R9) : le compte ``actif`` est pris dès qu'il a une sauvegarde de Wind Waker HD (solo
    OU coop), même sans ckcop.sav (première coop de ce compte) : sinon la coop viserait le compte
    d'un autre joueur du PC."""
    if compte is None and actif is not None and actif.lower() != "common":
        d = os.path.join(racine, "user", actif)
        if any(os.path.isfile(os.path.join(d, fichier_principal(j))) for j in JEUX):
            return actif, d
    lst = comptes(racine, jeu)
    if not lst and jeu == "coop":
        lst = comptes(racine, "solo")
    if compte is not None:
        for n, d, _t in lst:
            if n.lower() == compte.lower():
                return n, d
        raise RuntimeError(f"compte {compte} introuvable (comptes : {', '.join(n for n, _d, _t in lst) or 'aucun'})")
    if not lst:
        raise RuntimeError(f"aucune sauvegarde de Wind Waker HD dans {racine}")
    if actif is not None:
        for n, d, _t in lst:
            if n.lower() == actif.lower():
                return n, d
    recents = [x for x in lst if lst[0][2] - x[2] < 86400]
    if len(recents) > 1:
        raise RuntimeError("plusieurs comptes ont une sauvegarde récente ("
                           + ", ".join(n for n, _d, _t in recents) + ") : choisis avec --compte ID")
    return lst[0][0], lst[0][1]


# ---------------------------------------------------------------------------
# Copies de sécurité (§7.1, §7.3)
# ---------------------------------------------------------------------------
def dossier_copies(base: str | None = None, jeu: str | None = None) -> str:
    """Base des copies : ``base`` ou %LOCALAPPDATA%\\WWMP\\sauvegardes ; avec ``jeu`` (A16), le
    sous-dossier ``solo`` ou ``coop`` (rotation et « origine » propres à chaque jeu)."""
    if base:
        racine = base
    else:
        racine = os.path.join(plateforme.dossier_wwmp(), "sauvegardes")   # Linux : ~/.local/share/WWMP
    if jeu is not None:
        prefixe_jeu(jeu)                 # jeu inconnu : ValueError
        return os.path.join(racine, jeu)
    return racine


def _arbre(src: str) -> list[str]:
    out = []
    for dp, _dn, fn in os.walk(src):
        for f in fn:
            out.append(os.path.relpath(os.path.join(dp, f), src))
    return sorted(out)


def faire_copie(racine: str, compte: str, raison: str, extra: dict[str, bytes] | None = None,
                infos: dict | None = None, base: str | None = None, maintenant=None,
                jeu: str | None = None, prefixe: str | None = None) -> str:
    """Copie tout ``user\\<compte>`` (+ meta\\meta.xml) et les fichiers ``extra`` ; vérifie le
    sha256 de chaque copie. Lève OSError / RuntimeError en cas d'échec (rien n'est alors écrit
    dans le jeu par l'appelant). Renvoie le dossier de la copie.
    A16 : ``jeu`` choisit la base (``solo`` / ``coop``) ; ``prefixe`` (« cking » / « ckcop »)
    limite la copie aux fichiers de ce jeu (``du_jeu``)."""
    base = dossier_copies(base, jeu)
    os.makedirs(base, exist_ok=True)
    t = maintenant if maintenant is not None else time.time()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t))
    nom, i = f"{stamp}_{raison}", 1
    while os.path.exists(os.path.join(base, nom)):
        i += 1
        nom = f"{stamp}-{i}_{raison}"
    dst = os.path.join(base, nom)
    tmp = dst + ".partiel"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    src = os.path.join(racine, "user", compte)
    hashes: dict[str, str] = {}
    try:
        for rel in _arbre(src):
            if prefixe is not None and not du_jeu(rel, prefixe):
                continue
            a = os.path.join(src, rel)
            b = os.path.join(tmp, f"user_{compte}", rel)
            os.makedirs(os.path.dirname(b), exist_ok=True)
            shutil.copyfile(a, b)           # original ouvert en lecture seule
            ha, hb = sha256_fichier(a), sha256_fichier(b)
            if ha != hb:
                raise RuntimeError(f"copie différente de l'original : {rel} (fichier en cours d'écriture ?)")
            hashes[os.path.join(f"user_{compte}", rel)] = hb
        meta = os.path.join(racine, "meta", "meta.xml")
        if os.path.isfile(meta):
            os.makedirs(os.path.join(tmp, "meta"))
            shutil.copyfile(meta, os.path.join(tmp, "meta", "meta.xml"))
            hashes[os.path.join("meta", "meta.xml")] = sha256_fichier(os.path.join(tmp, "meta", "meta.xml"))
        for n, data in (extra or {}).items():
            with open(os.path.join(tmp, n), "wb") as f:
                f.write(data)
            hashes[n] = hashlib.sha256(data).hexdigest()
        origine = not any(i.get("compte") == compte for i in lister(base))
        doc = dict(infos or {})
        doc.update({"date": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), "horodatage": t,
                    "raison": raison, "compte": compte, "racine": racine, "origine": origine,
                    "cking_mtime": os.path.getmtime(os.path.join(src, "cking.sav"))
                    if os.path.isfile(os.path.join(src, "cking.sav")) else None,
                    "ckcop_mtime": os.path.getmtime(os.path.join(src, "ckcop.sav"))
                    if os.path.isfile(os.path.join(src, "ckcop.sav")) else None,
                    "jeu": jeu, "prefixe": prefixe,
                    "fichiers": hashes})
        with open(os.path.join(tmp, "infos.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
        os.replace(tmp, dst)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dst


def lire_infos(copie: str) -> dict:
    with open(os.path.join(copie, "infos.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def ajouter_fichier(copie: str, nom: str, data: bytes) -> None:
    """Ajoute un fichier à une copie existante (et son sha256 dans infos.json)."""
    tmp = os.path.join(copie, nom + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(copie, nom))
    doc = lire_infos(copie)
    doc.setdefault("fichiers", {})[nom] = hashlib.sha256(data).hexdigest()
    maj_infos(copie, fichiers=doc["fichiers"])


def a_change(inf: dict) -> bool:
    """La tentative de cette copie a-t-elle (peut-être) changé la partie ? Faux seulement si
    l'outil a noté « rien écrit » ou « état d'avant remis »."""
    if inf.get("resultat") == "ok":
        return True
    if inf.get("defait"):
        return False
    return inf.get("ecrit") is not False


def maj_infos(copie: str, **champs) -> None:
    doc = lire_infos(copie)
    doc.update(champs)
    tmp = os.path.join(copie, "infos.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    os.replace(tmp, os.path.join(copie, "infos.json"))


def taille(copie: str) -> int:
    return sum(os.path.getsize(os.path.join(copie, r)) for r in _arbre(copie))


def lister(base: str | None = None) -> list[dict]:
    """Copies, plus récente d'abord : infos.json + ``chemin`` + ``nom``."""
    base = dossier_copies(base)
    out = []
    try:
        noms = os.listdir(base)
    except OSError:
        return []
    for n in noms:
        d = os.path.join(base, n)
        if not NOM_RE.match(n) or not os.path.isfile(os.path.join(d, "infos.json")):
            continue
        try:
            i = lire_infos(d)
        except (OSError, ValueError):
            continue
        i["chemin"], i["nom"] = d, n
        out.append(i)
    out.sort(key=lambda i: (i.get("horodatage", 0), i["nom"]), reverse=True)
    return out


def verifier_copie(copie: str) -> list[str]:
    """Fichiers manquants ou différents de leur sha256 noté."""
    bad = []
    for rel, h in lire_infos(copie).get("fichiers", {}).items():
        p = os.path.join(copie, rel)
        if not os.path.isfile(p) or sha256_fichier(p) != h:
            bad.append(rel)
    return bad


def rotation(base: str | None = None, garder: int = GARDER, log=None) -> list[str]:
    """Garde les ``garder`` copies les plus récentes ; ne supprime jamais une copie
    « origine » (toute première copie d'un compte). Renvoie les dossiers supprimés."""
    supprimees = []
    for i in lister(base)[garder:]:
        if i.get("origine"):
            continue
        shutil.rmtree(i["chemin"], ignore_errors=True)
        supprimees.append(i["chemin"])
        if log:
            log(f"ancienne copie supprimée : {i['nom']}")
    return supprimees


def trouver_copie(nom: str | None, base: str | None = None, raisons=("rejoindre", "fichier")) -> dict:
    lst = lister(base)
    if nom:
        for i in lst:
            if i["nom"] == nom or i["chemin"] == nom:
                return i
        raise RuntimeError(f"copie « {nom} » introuvable dans {dossier_copies(base)}")
    for i in lst:
        if i.get("raison") in raisons and a_change(i):
            return i
    raise RuntimeError(f"aucune copie à annuler dans {dossier_copies(base)} (les tentatives qui n'ont "
                       "rien changé sont ignorées ; « sauvegardes » les liste toutes)")


# ---------------------------------------------------------------------------
# Restauration (§8 cas 3 : Cemu fermé)
# ---------------------------------------------------------------------------
def cemu_tourne() -> bool:
    try:
        from jeu import procmem
        return bool(procmem.find_cemu_pids())
    except Exception:
        return False


def restaurer_fichiers(copie: str, base: str | None = None, cemu=cemu_tourne, log=None,
                       prefixe: str | None = None, jeu: str | None = None) -> str:
    """Remet les fichiers de ``copie`` dans le dossier d'origine (Cemu fermé seulement).
    Fait d'abord une copie « avant_annulation ». Renvoie le dossier de cette copie.
    A16 : ``prefixe`` (« cking » / « ckcop ») ne remet QUE les fichiers de ce jeu ; ``jeu`` choisit
    la base de la copie « avant_annulation »."""
    if cemu():
        raise RuntimeError("Cemu tourne : ferme-le d'abord (il pourrait réécrire la sauvegarde "
                           "depuis sa mémoire)")
    inf = lire_infos(copie)
    bad = verifier_copie(copie)
    if bad:
        raise RuntimeError("copie abîmée (sha256) : " + ", ".join(bad))
    racine, compte = inf["racine"], inf["compte"]
    avant = faire_copie(racine, compte, "avant_annulation", base=base,
                        infos={"restaure": os.path.basename(copie)}, jeu=jeu, prefixe=prefixe)
    dst_dir = os.path.join(racine, "user", compte)
    pref = f"user_{compte}" + os.sep
    for rel, h in sorted(inf["fichiers"].items()):
        if not rel.startswith(pref):
            continue                        # meta.xml, gi_avant.bin… : pas remis
        if prefixe is not None and not du_jeu(rel, prefixe):
            continue                        # A16 : fichier de l'autre jeu : jamais remis
        src = os.path.join(copie, rel)
        dst = os.path.join(dst_dir, rel[len(pref):])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        shutil.copyfile(src, tmp)
        if sha256_fichier(tmp) != h:
            os.remove(tmp)
            raise RuntimeError(f"copie temporaire différente : {rel}")
        os.replace(tmp, dst)
        if log:
            log(f"remis : {rel[len(pref):]}")
    return avant


def ecrire_fichier_sur(path: str, data: bytes, verif=None) -> None:
    """Écrit ``path`` via ``path.tmp`` + relecture (+ ``verif(octets)``) + ``os.replace``."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())             # sur le disque AVANT le remplacement (coupure de courant)
        with open(tmp, "rb") as f:
            back = f.read()
        if back != data or (verif and not verif(back)):
            raise RuntimeError(f"relecture de {tmp} différente")
        os.replace(tmp, path)
    except BaseException:
        try:                                 # jamais de .tmp laissé dans le dossier de sauvegarde
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# A16 : empreintes et copies « stables » (menu_coop.md §5.1, §8.3.6)
# ---------------------------------------------------------------------------
def fichiers_jeu(dossier: str, prefixe: str) -> list[str]:
    """Noms des fichiers du jeu ``prefixe`` dans ``dossier`` (sans sous-dossiers)."""
    try:
        noms = os.listdir(dossier)
    except OSError:
        return []
    return sorted(n for n in noms if du_jeu(n, prefixe) and os.path.isfile(os.path.join(dossier, n)))


def empreintes(dossier: str, prefixe: str) -> dict[str, dict]:
    """{nom: {mtime, taille, sha256}} des fichiers du jeu (lecture seule ; illisible : sha256 None)."""
    out = {}
    for n in fichiers_jeu(dossier, prefixe):
        p = os.path.join(dossier, n)
        try:
            st = os.stat(p)
            h = sha256_fichier(p)
        except OSError:
            st, h = None, None
        out[n] = {"mtime": st.st_mtime if st else None, "taille": st.st_size if st else None, "sha256": h}
    return out


def differences(avant: dict[str, dict], apres: dict[str, dict]) -> list[str]:
    """Fichiers ajoutés, retirés ou modifiés (sha256) entre deux ``empreintes``."""
    out = []
    for n in sorted(set(avant) | set(apres)):
        a, b = avant.get(n), apres.get(n)
        if a is None:
            out.append(f"{n} (nouveau)")
        elif b is None:
            out.append(f"{n} (disparu)")
        elif a.get("sha256") != b.get("sha256"):
            out.append(f"{n} (modifié)")
    return out


def dates(dossier: str, prefixe: str | None = None) -> dict[str, float]:
    """{nom: date de modification} (fichiers du jeu ``prefixe``, ou tous)."""
    out = {}
    try:
        noms = os.listdir(dossier)
    except OSError:
        return out
    for n in noms:
        if prefixe is not None and not du_jeu(n, prefixe):
            continue
        try:
            out[n] = os.path.getmtime(os.path.join(dossier, n))
        except OSError:
            pass
    return out


def copie_stable(racine: str, compte: str, raison: str, jeu: str | None = None, prefixe: str | None = None,
                 occupe=lambda: False, stable: float = 1.0, essais: int = 3, base: str | None = None,
                 infos: dict | None = None, sleep=time.sleep, clock=time.monotonic,
                 attente_max: float = 10.0) -> str:
    """Copie de sécurité prise à un moment calme (§8.3.6) : ``occupe()`` faux (SaveMgr+0x1C == 0,
    lu en RAM par l'appelant) et dates des fichiers inchangées pendant ``stable`` s ; réessaie
    ``essais`` fois si un fichier change pendant la copie. Lève RuntimeError sinon."""
    dossier = os.path.join(racine, "user", compte)
    derniere: Exception | None = None
    for _ in range(max(1, essais)):
        fin = clock() + attente_max
        while True:
            if clock() > fin:
                raise RuntimeError("sauvegarde du jeu en cours trop longtemps : copie de sécurité impossible")
            if occupe():
                sleep(0.2)
                continue
            d0 = dates(dossier, prefixe)
            sleep(stable)
            if not occupe() and dates(dossier, prefixe) == d0:
                break
        try:
            return faire_copie(racine, compte, raison, base=base, infos=infos, jeu=jeu, prefixe=prefixe)
        except (OSError, RuntimeError) as e:     # fichier en cours d'écriture : on recommence
            derniere = e
            sleep(0.5)
    raise RuntimeError(f"copie de sécurité impossible : {derniere}")


def emplacements_vides(fichier: bytes, n_slots: int = 3, stride: int = 0xA94, date_off: int = 0x018) -> list[int]:
    """Emplacements dont mDateIPL est nul (Journal vide), dans l'ordre."""
    out = []
    for k in range(n_slots):
        a = k * stride
        if len(fichier) < a + stride:
            break
        if not any(fichier[a + date_off:a + date_off + 8]):
            out.append(k)
    return out
