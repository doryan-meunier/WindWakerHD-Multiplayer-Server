"""Installer et mettre à jour le portage natif ZeldaWWHDRecomp depuis sa release OFFICIELLE GitHub, puis préparer le
jeu avec le setup DU PORTAGE depuis le dump que le joueur choisit (docs/dev/PORTAGE_NATIF.md §8.6, §8.7).

* Versions : ``tools/jeu/portage_versions.json`` — ``epinglee`` (installée par « Installer le portage natif »),
  ``verifiees`` (le mod WWMP y a été construit et vérifié : mise à jour directe ; plus récente = avertissement, le
  joueur peut forcer), ``empreintes`` (sha256 de chaque fichier de la release, recopiés de son SHA256SUMS.txt). Un
  fichier dont l'empreinte n'est pas connue n'est jamais installé : épinglée pour une version de la table ; pour une
  version plus récente FORCÉE, celle que publie la release elle-même (``digest`` de l'API GitHub ou son
  SHA256SUMS.txt), dit comme tel ; aucune des deux : refus.
* Téléchargement : HTTPS, github.com (et ses hôtes de fichiers) seulement, borné en taille, sha256 vérifié AVANT
  toute décompression ; fichier partiel effacé en cas d'échec ou d'annulation.
* Décompression (zip Windows / Linux) : chaque nom vérifié (pas de chemin absolu, de lecteur, de « .. », de « \\ »,
  pas de lien symbolique ni de fichier spécial, un seul dossier de tête = le nom de la release), taille et nombre
  bornés ; dans un dossier caché à côté, renommé à la fin ; un dossier du même nom déjà là : refus. Linux : les
  droits d'exécution du zip sont gardés. AppImage : le fichier copié tel quel, rendu exécutable.
* Préparation du jeu : le setup du portage (``tools/installer/setup.py``), par SON interface machine
  ``--gui-protocol`` (lignes JSON, ``tools/installer/README.md`` : ``hello``, ``probe``, ``check_keys``, ``install``),
  lancé comme le fait son lanceur : Windows ``tools\\python\\python.exe tools\\installer\\setup.py`` (Python livré,
  ``PYTHONDONTWRITEBYTECODE=1``), Linux ``sh tools/setup-in-terminal.sh`` (trouve ou récupère Python lui-même),
  AppImage : l'image montée (``--appimage-mount``), ``APPDIR`` / ``APPIMAGE`` posées. Le lanceur ne transmet QUE le
  chemin du dump choisi (``.wux`` / ``.wud`` / ``.wua`` ou dossier) : il ne lit ni ne touche jamais les clés (le
  setup du portage cherche lui-même ``<image>.key`` et ``common.key`` à côté de l'image). ``code_mods`` vrai : le
  jeu est construit d'emblée avec le support des mods de code.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile

NOM_TABLE = "portage_versions.json"
API = "https://api.github.com/repos/{depot}/releases/latest"
URL_ASSET = "https://github.com/{depot}/releases/download/{tag}/{nom}"
HOTES = ("github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
         "codeload.github.com")
TAG_RE = re.compile(r"^v\d{1,3}\.\d{1,3}\.\d{1,3}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NOM_RE = re.compile(r"^WindWakerHD-v\d{1,3}\.\d{1,3}\.\d{1,3}-[a-z0-9_]+-[a-z0-9_]+\.(zip|AppImage)$")
TAILLE_MAX = 1 << 30            # 1 Gio par fichier téléchargé (release v0.5.1 : ~40 Mo)
DECOMP_MAX = 3 << 30            # 3 Gio décompressés
FICHIERS_MAX = 50000
AGENT = "WWMP-lanceur"          # nom générique (aucune donnée du joueur)
MORCEAU = 1 << 16
ATTENTE_HELLO = 1800.0          # s : Linux peut d'abord récupérer Python (75 Mo)


class ErreurInstall(Exception):
    """Refus propre (fr, en) ; ``annule`` : arrêté à la demande du joueur."""

    def __init__(self, fr: str, en: str | None = None, annule: bool = False):
        super().__init__(fr)
        self.fr, self.en, self.annule = fr, en or fr, annule


def _systeme(systeme: str | None = None) -> str:
    s = systeme or sys.platform
    return "win32" if s == "win32" else "linux"


# ---------------------------------------------------------------------------
# Table des versions
# ---------------------------------------------------------------------------
def chemin_table() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), NOM_TABLE)


def table(chemin: str | None = None) -> dict:
    """La table validée : {depot, epinglee, verifiees: [..], empreintes: {version: {nom: sha256}}}."""
    try:
        with open(chemin or chemin_table(), "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise ErreurInstall(f"{NOM_TABLE} illisible ({e.__class__.__name__})") from None
    if not isinstance(d, dict) or d.get("format") != 1 or not TAG_RE.match(str(d.get("epinglee") or "")) \
            or not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", str(d.get("depot") or "")):
        raise ErreurInstall(f"{NOM_TABLE} : format inconnu")
    emp = {}
    for v, fichiers in (d.get("empreintes") or {}).items():
        if not TAG_RE.match(v) or not isinstance(fichiers, dict):
            raise ErreurInstall(f"{NOM_TABLE} : version {v!r} refusée")
        emp[v] = {}
        for n, h in fichiers.items():
            if not NOM_RE.match(n) or not isinstance(h, str) or not (HEX64.match(h) or h == ""):
                raise ErreurInstall(f"{NOM_TABLE} : {n!r} refusé")
            if h:
                emp[v][n] = h
    ver = [v for v in d.get("verifiees") or () if isinstance(v, str) and TAG_RE.match(v)]
    return {"depot": d["depot"], "epinglee": d["epinglee"], "verifiees": ver, "empreintes": emp}


def version_t(v: str) -> tuple[int, ...]:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def plateforme(systeme: str | None = None, machine: str | None = None) -> str:
    """« windows-x86_64 », « windows-arm64 », « linux-x86_64 » ou « linux-aarch64 » (les noms des releases)."""
    m = (machine or platform.machine() or "").lower()
    arm = m in ("arm64", "aarch64", "armv8", "armv8l")
    if _systeme(systeme) == "win32":
        return "windows-arm64" if arm else "windows-x86_64"
    return "linux-aarch64" if arm else "linux-x86_64"


def nom_asset(version: str, plat: str, appimage: bool = False) -> str:
    return f"WindWakerHD-{version}-{plat}." + ("AppImage" if appimage else "zip")


def empreinte_epinglee(t: dict, version: str, nom: str) -> str | None:
    return (t["empreintes"].get(version) or {}).get(nom)


def compatibilite(t: dict, version: str) -> str:
    """« verifiee » (le mod y a été vérifié), « non_verifiee » (plus récente), « ancienne » (avant l'épinglée)."""
    if version in t["verifiees"]:
        return "verifiee"
    return "ancienne" if version_t(version) < version_t(t["epinglee"]) else "non_verifiee"


def url_asset(t: dict, version: str, nom: str) -> str:
    if not TAG_RE.match(version) or not NOM_RE.match(nom):
        raise ErreurInstall("nom de fichier de release refusé", "release file name refused")
    return URL_ASSET.format(depot=t["depot"], tag=version, nom=urllib.parse.quote(nom))


# ---------------------------------------------------------------------------
# Réseau (``ouvrir`` : urllib.request.urlopen, remplacé dans les essais)
# ---------------------------------------------------------------------------
def hote_ok(url: str) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return u.scheme == "https" and (u.hostname or "").lower() in HOTES and not u.username and not u.password


def _ouvrir(url: str, ouvrir, delai: float = 30.0, accept: str = "*/*"):
    if not hote_ok(url):
        raise ErreurInstall(f"adresse refusée (HTTPS GitHub seulement) : {url[:80]}",
                            f"address refused (GitHub HTTPS only): {url[:80]}")
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": accept})
    try:
        rep = (ouvrir or urllib.request.urlopen)(req, timeout=delai)
    except Exception as e:                         # noqa: BLE001 : URLError, HTTPError, socket…
        raise ErreurInstall(f"téléchargement impossible ({e.__class__.__name__} : {str(e)[:120]})",
                            f"download failed ({e.__class__.__name__}: {str(e)[:120]})") from None
    final = getattr(rep, "geturl", lambda: url)() or url
    if not hote_ok(final):
        try:
            rep.close()
        except Exception:                          # noqa: BLE001
            pass
        raise ErreurInstall(f"redirection refusée : {final[:80]}", f"redirect refused: {final[:80]}")
    return rep


def derniere_release(t: dict, ouvrir=None) -> dict:
    """Dernière release (API GitHub, sans authentification) : {version, assets: {nom: digest sha256 ou ""},
    publiee, page}. Lève ErreurInstall."""
    rep = _ouvrir(API.format(depot=t["depot"]), ouvrir, accept="application/vnd.github+json")
    try:
        brut = rep.read(4 << 20)
    finally:
        rep.close()
    try:
        d = json.loads(brut.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ErreurInstall("réponse de GitHub illisible", "unreadable GitHub answer") from None
    tag = d.get("tag_name") if isinstance(d, dict) else None
    if not isinstance(tag, str) or not TAG_RE.match(tag) or d.get("draft") or d.get("prerelease"):
        raise ErreurInstall("dernière release du portage : version illisible", "latest port release: unreadable "
                            "version")
    assets = {}
    for a in d.get("assets") or ():
        if isinstance(a, dict) and isinstance(a.get("name"), str):
            dg = str(a.get("digest") or "")
            assets[a["name"]] = dg[7:] if dg.startswith("sha256:") and HEX64.match(dg[7:]) else ""
    return {"version": tag, "assets": assets, "publiee": str(d.get("published_at") or "")[:10],
            "page": f"https://github.com/{t['depot']}/releases/tag/{tag}"}


def empreinte_publiee(t: dict, rel: dict, nom: str, ouvrir=None) -> str | None:
    """L'empreinte que la release publie elle-même pour ``nom`` (``digest`` de l'API, sinon son SHA256SUMS.txt)."""
    if rel["assets"].get(nom):
        return rel["assets"][nom]
    if "SHA256SUMS.txt" not in rel["assets"]:
        return None
    rep = _ouvrir(URL_ASSET.format(depot=t["depot"], tag=rel["version"], nom="SHA256SUMS.txt"), ouvrir)
    try:
        texte = rep.read(1 << 20).decode("utf-8", "replace")
    finally:
        rep.close()
    for ligne in texte.splitlines():
        p = ligne.split()
        if len(p) == 2 and p[1].lstrip("*") == nom and HEX64.match(p[0].lower()):
            return p[0].lower()
    return None


def telecharger(url: str, dest: str, sha: str | None, progres=None, annule=None, ouvrir=None,
                taille_max: int = TAILLE_MAX) -> str:
    """``url`` -> ``dest`` (fichier partiel à côté, renommé une fois l'empreinte vérifiée). ``sha`` inconnu : refus
    AVANT tout téléchargement. ``progres(fait, total)``. Lève ErreurInstall."""
    if not sha or not HEX64.match(sha):
        raise ErreurInstall(f"empreinte sha256 inconnue pour {os.path.basename(dest)} : pas installé (le lanceur "
                            "n'installe que des fichiers dont il connaît l'empreinte)",
                            f"unknown sha256 for {os.path.basename(dest)}: not installed (the launcher only installs "
                            "files whose checksum it knows)")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    partiel = dest + ".partiel"
    rep = _ouvrir(url, ouvrir, delai=60.0)
    h = hashlib.sha256()
    fait = 0
    try:
        try:
            total = int(rep.headers.get("Content-Length") or 0) if getattr(rep, "headers", None) else 0
        except (TypeError, ValueError):
            total = 0
        if total > taille_max:
            raise ErreurInstall("fichier trop gros", "file too large")
        with open(partiel, "wb") as f:
            while True:
                if annule is not None and annule():
                    raise ErreurInstall("téléchargement annulé", "download cancelled", annule=True)
                try:
                    bloc = rep.read(MORCEAU)
                except Exception as e:             # noqa: BLE001
                    raise ErreurInstall(f"téléchargement coupé ({e.__class__.__name__})",
                                        f"download interrupted ({e.__class__.__name__})") from None
                if not bloc:
                    break
                fait += len(bloc)
                if fait > taille_max:
                    raise ErreurInstall("fichier trop gros", "file too large")
                h.update(bloc)
                f.write(bloc)
                if progres is not None:
                    progres(fait, total)
            f.flush()
            os.fsync(f.fileno())
        if h.hexdigest() != sha:
            raise ErreurInstall(f"{os.path.basename(dest)} : empreinte sha256 fausse (fichier abîmé ou pas celui de "
                                "la release officielle) : rien n'est installé",
                                f"{os.path.basename(dest)}: wrong sha256 (damaged file or not the official release's): "
                                "nothing is installed")
        os.replace(partiel, dest)
    except BaseException:
        try:
            os.remove(partiel)
        except OSError:
            pass
        raise
    finally:
        try:
            rep.close()
        except Exception:                          # noqa: BLE001
            pass
    return dest


# ---------------------------------------------------------------------------
# Décompression vérifiée
# ---------------------------------------------------------------------------
def verifier_zip(chemin: str, tete: str | None = None) -> tuple[str, list]:
    """(dossier de tête, entrées) d'un zip de release, ou ErreurInstall : noms sûrs, ni lien ni fichier spécial, un
    seul dossier de tête (``tete`` s'il est donné), tailles bornées."""
    try:
        z = zipfile.ZipFile(chemin)
    except (OSError, zipfile.BadZipFile) as e:
        raise ErreurInstall(f"zip illisible ({e.__class__.__name__})", f"unreadable zip ({e.__class__.__name__})") \
            from None
    with z:
        infos = z.infolist()
    if not infos or len(infos) > FICHIERS_MAX:
        raise ErreurInstall("zip vide ou trop de fichiers", "empty zip or too many files")
    tetes, total = set(), 0
    for i in infos:
        n = i.filename
        if not n or "\\" in n or "\0" in n or ":" in n or n.startswith("/") or re.match(r"^[A-Za-z]:", n):
            raise ErreurInstall(f"zip refusé : nom dangereux {n[:60]!r}", f"zip refused: dangerous name {n[:60]!r}")
        parts = n.rstrip("/").split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ErreurInstall(f"zip refusé : chemin qui sort du dossier {n[:60]!r}",
                                f"zip refused: path leaving the folder {n[:60]!r}")
        mode = (i.external_attr >> 16) & 0o170000
        if mode and mode not in (stat.S_IFREG, stat.S_IFDIR):
            raise ErreurInstall(f"zip refusé : lien ou fichier spécial {n[:60]!r}",
                                f"zip refused: link or special file {n[:60]!r}")
        if i.flag_bits & 0x1:
            raise ErreurInstall("zip chiffré refusé", "encrypted zip refused")
        total += i.file_size
        tetes.add(parts[0])
        if len(parts) == 1 and not i.is_dir():
            raise ErreurInstall("zip refusé : fichier hors du dossier de la release",
                                "zip refused: file outside the release folder")
    if total > DECOMP_MAX:
        raise ErreurInstall("zip refusé : trop gros une fois décompressé", "zip refused: too large once unpacked")
    if len(tetes) != 1 or (tete and tetes != {tete}):
        raise ErreurInstall("zip refusé : pas le dossier attendu de la release", "zip refused: not the expected "
                            "release folder")
    return tetes.pop(), infos


def extraire(chemin: str, parent: str, tete: str | None = None, progres=None, annule=None) -> str:
    """Décompresse le zip vérifié dans ``parent/<tête>`` (dossier caché à côté, puis renommé). Lève ErreurInstall."""
    t, infos = verifier_zip(chemin, tete)
    final = os.path.join(parent, t)
    if os.path.lexists(final):
        raise ErreurInstall(f"{final} existe déjà : rien n'est remplacé (choisis un autre dossier, ou utilise ce "
                            "portage-là)", f"{final} already exists: nothing is replaced (choose another folder, or use "
                            "that port)")
    os.makedirs(parent, exist_ok=True)
    tmp = os.path.join(parent, f".{t}.partiel")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    racine = os.path.realpath(tmp)
    try:
        with zipfile.ZipFile(chemin) as z:
            for k, i in enumerate(infos):
                if annule is not None and annule():
                    raise ErreurInstall("décompression annulée", "unpacking cancelled", annule=True)
                parts = i.filename.rstrip("/").split("/")[1:]
                if not parts:
                    continue
                cible = os.path.join(tmp, *parts)
                if os.path.commonpath([racine, os.path.realpath(cible)]) != racine:
                    raise ErreurInstall("zip refusé : chemin qui sort du dossier", "zip refused: path leaving the "
                                        "folder")
                if i.is_dir():
                    os.makedirs(cible, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(cible), exist_ok=True)
                with z.open(i) as src, open(cible, "wb") as dst:
                    shutil.copyfileobj(src, dst, MORCEAU)
                droits = (i.external_attr >> 16) & 0o777
                if os.name != "nt" and droits & 0o111:
                    os.chmod(cible, os.stat(cible).st_mode | 0o755)
                if progres is not None:
                    progres(k + 1, len(infos))
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return final


def poser_appimage(fichier: str, parent: str, nom: str) -> str:
    """L'AppImage téléchargée posée dans ``parent/nom`` (jamais par-dessus un fichier existant), exécutable."""
    final = os.path.join(parent, nom)
    if os.path.lexists(final):
        raise ErreurInstall(f"{final} existe déjà : rien n'est remplacé", f"{final} already exists: nothing is "
                            "replaced")
    os.makedirs(parent, exist_ok=True)
    shutil.copyfile(fichier, final + ".partiel")
    os.chmod(final + ".partiel", 0o755)
    os.replace(final + ".partiel", final)
    return final


# ---------------------------------------------------------------------------
# Le dump choisi (seul son chemin est transmis au setup du portage)
# ---------------------------------------------------------------------------
def nature_dump(chemin: str) -> str:
    """« image » (.wux / .wud), « archive » (.wua), « dossier » ; ErreurInstall sinon. Le contenu n'est pas lu (le
    setup du portage le vérifie lui-même, clés comprises)."""
    c = str(chemin or "").strip().strip('"')
    if c and os.path.isdir(c):
        return "dossier"
    if c and os.path.isfile(c):
        e = os.path.splitext(c)[1].lower()
        if e in (".wux", ".wud"):
            return "image"
        if e == ".wua":
            return "archive"
    raise ErreurInstall("dump introuvable : choisis une image .wux / .wud, une archive Cemu .wua ou le dossier du jeu "
                        "extrait (code, content, meta)", "dump not found: choose a .wux / .wud disc image, a .wua Cemu "
                        "archive or the extracted game folder (code, content, meta)")


# ---------------------------------------------------------------------------
# Le setup du portage (--gui-protocol)
# ---------------------------------------------------------------------------
def argv_setup(racine: str, systeme: str | None = None) -> tuple[list[str], dict]:
    """(argv, variables) qui lancent le setup du portage de ``racine`` (dossier de la release, ou point de montage
    d'un AppImage), comme son lanceur."""
    if _systeme(systeme) == "win32":
        py = os.path.join(racine, "tools", "python", "python.exe")
        if not os.path.isfile(py):
            raise ErreurInstall(f"{py} absent : release incomplète (décompresse-la de nouveau)",
                                f"{py} missing: incomplete release (unpack it again)")
        return [py, os.path.join(racine, "tools", "installer", "setup.py"), "--gui-protocol"], \
            {"PYTHONDONTWRITEBYTECODE": "1"}
    sh = os.path.join(racine, "tools", "setup-in-terminal.sh")
    if not os.path.isfile(sh):
        raise ErreurInstall(f"{sh} absent : release incomplète", f"{sh} missing: incomplete release")
    return ["sh", sh, "--gui-protocol"], {"PYTHONDONTWRITEBYTECODE": "1"}


class Protocole:
    """Le setup du portage en processus fils : requêtes JSON sur son entrée, événements JSON sur sa sortie."""

    def __init__(self, argv, cwd, env, journal: str, progres=None, annule=None, popen=subprocess.Popen,
                 systeme: str | None = None):
        self.progres, self.annule, self.n = progres, annule, 0
        self.q: queue.Queue = queue.Queue()
        os.makedirs(os.path.dirname(journal) or ".", exist_ok=True)
        self.log = open(journal, "ab")
        self.log.write(("\n--- " + time.strftime("%Y-%m-%d %H:%M:%S") + " : " + " ".join(argv) + "\n").encode(
            "utf-8", "replace"))
        options = {"creationflags": 0x08000000} if _systeme(systeme) == "win32" else {}
        try:
            self.proc = popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=self.log, **options)
        except OSError as e:
            self.log.close()
            raise ErreurInstall(f"setup du portage pas lancé ({e.__class__.__name__})",
                                f"the port's setup could not start ({e.__class__.__name__})") from None
        threading.Thread(target=self._lire, daemon=True, name="wwmp-setup-portage").start()

    def _lire(self):
        try:
            for brut in self.proc.stdout:
                ligne = brut.decode("utf-8", "replace").strip() if isinstance(brut, bytes) else str(brut).strip()
                if not ligne:
                    continue
                try:
                    ev = json.loads(ligne)
                except ValueError:
                    ev = None
                if isinstance(ev, dict):
                    self.q.put(ev)
                else:
                    self._journal(ligne)
        finally:
            self.q.put(None)

    def _journal(self, texte: str):
        try:
            self.log.write((texte + "\n").encode("utf-8", "replace"))
            self.log.flush()
        except (OSError, ValueError):
            pass

    def fermer(self, tuer: bool = False):
        p = self.proc
        try:
            if tuer and p.poll() is None:
                p.terminate()
            elif p.poll() is None:
                try:
                    p.stdin.write(b'{"cmd": "quit"}\n')
                    p.stdin.flush()
                except (OSError, ValueError):
                    pass
            try:
                p.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                p.wait(30)
            except Exception:                      # noqa: BLE001
                p.kill()
                p.wait(10)
            try:
                p.stdout.close()
            except (OSError, ValueError):
                pass
        finally:
            try:
                self.log.close()
            except OSError:
                pass

    def attendre(self, accepte, delai: float | None = None) -> dict:
        fin = None if delai is None else time.monotonic() + delai
        while True:
            if self.annule is not None and self.annule():
                raise ErreurInstall("préparation du jeu annulée (relancer reprend où le setup du portage s'est "
                                    "arrêté)", "game preparation cancelled (starting again resumes where the port's "
                                    "setup stopped)", annule=True)
            if fin is not None and time.monotonic() > fin:
                raise ErreurInstall("le setup du portage ne répond pas", "the port's setup does not answer")
            try:
                ev = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if ev is None:
                code = self.proc.poll()
                raise ErreurInstall(f"le setup du portage s'est arrêté (code {code}) : détails dans setup_portage.log",
                                    f"the port's setup stopped (code {code}): details in setup_portage.log")
            e = ev.get("event")
            if e == "log":
                self._journal(str(ev.get("text") or ""))
            elif e == "fatal":
                m = str(ev.get("message") or "?")
                raise ErreurInstall(f"le setup du portage ne peut pas continuer : {m}",
                                    f"the port's setup cannot continue: {m}")
            elif e in ("step", "progress", "plan") and self.progres is not None:
                try:
                    self.progres(ev)
                except Exception:                  # noqa: BLE001
                    pass
            if accepte(ev):
                return ev

    def requete(self, cmd: str, delai: float | None = None, **champs) -> dict:
        self.n += 1
        ident = self.n
        try:
            self.proc.stdin.write((json.dumps(dict(champs, cmd=cmd, id=ident)) + "\n").encode("ascii"))
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass                                   # sa fin est lue par attendre
        return self.attendre(lambda ev: ev.get("event") == "reply" and ev.get("id") == ident, delai)


def _refus(rep: dict, fr: str, en: str) -> ErreurInstall:
    m = str(rep.get("message") or rep.get("problem") or "?")
    return ErreurInstall(f"{fr} : {m}", f"{en}: {m}")


def preparer_jeu(racine: str, dump: str | None, journal: str, progres=None, annule=None, popen=subprocess.Popen,
                 systeme: str | None = None, argv=None, env_extra: dict | None = None, cwd: str | None = None) -> dict:
    """Prépare (ou répare / met à jour) le jeu du portage de ``racine`` avec SON setup. ``dump`` : chemin choisi par
    le joueur, ou None = les fichiers du jeu déjà installés (« installed »). ``argv`` : (argv, variables) à la place
    de ``argv_setup`` (AppImage montée, essais). {hello, reponse}. Lève ErreurInstall."""
    a, variables = argv if argv is not None else argv_setup(racine, systeme)
    env = dict(os.environ, **variables, **(env_extra or {}))
    p = Protocole(a, cwd or racine, env, journal, progres, annule, popen, systeme)
    tuer = True
    try:
        hello = p.attendre(lambda ev: ev.get("event") == "hello", ATTENTE_HELLO)
        if dump is None:
            if not hello.get("game_files"):
                raise ErreurInstall("pas de fichiers du jeu installés dans ce portage : choisis ton dump",
                                    "no game files installed in this port: choose your dump")
            source, chemin = "installed", ""
        else:
            nature_dump(dump)
            r = p.requete("probe", 300.0, path=dump)
            if not r.get("ok"):
                raise _refus(r, "dump refusé par le setup du portage", "dump refused by the port's setup")
            source, chemin = r.get("kind"), str(r.get("path") or dump)
            if source == "image":
                if not r.get("disc_key") or not r.get("common_key"):
                    manque = " et ".join(x for x, ok in (("la clé du disque (<image>.key)", r.get("disc_key")),
                                                         ("la clé commune (common.key)", r.get("common_key"))) if not ok)
                    miss = " and ".join(x for x, ok in (("the disc key (<image>.key)", r.get("disc_key")),
                                                        ("the common key (common.key)", r.get("common_key"))) if not ok)
                    raise ErreurInstall(f"image disque : {manque} introuvable à côté de l'image. Pose-la à côté (même "
                                        "nom que l'image pour la clé du disque), ou choisis une archive .wua ou un "
                                        "dossier extrait (sans clés), ou prépare le jeu avec la fenêtre du portage "
                                        "(« Wind Waker HD »). Le lanceur ne manipule jamais les clés.",
                                        f"disc image: {miss} not found next to the image. Put it next to it (same name "
                                        "as the image for the disc key), or choose a .wua archive or an extracted "
                                        "folder (no keys), or prepare the game with the port's window (“Wind Waker "
                                        "HD”). The launcher never handles keys.")
                r = p.requete("check_keys", 1800.0, image=chemin)
                if not r.get("ok"):
                    raise _refus(r, "clés refusées par le setup du portage", "keys refused by the port's setup")
            elif source not in ("archive", "folder"):
                raise ErreurInstall(f"dump : nature inconnue ({source})", f"dump: unknown kind ({source})")
        champs = {"source": source, "code_mods": True}
        if chemin:
            champs["path"] = chemin
        r = p.requete("install", None, **champs)
        if not r.get("ok"):
            raise _refus(r, "préparation du jeu ratée", "game preparation failed")
        tuer = False
        return {"hello": hello, "reponse": r}
    finally:
        p.fermer(tuer)


# ---------------------------------------------------------------------------
# Mise à jour : ce qui passe de l'ancienne release à la nouvelle
# ---------------------------------------------------------------------------
EXCLUS_USER = ("ModManager/GuestBuild",)     # mods de code construits pour l'ANCIEN jeu : le nouveau les refait


def _inventaire(d: str) -> dict[str, str]:
    out = {}
    for base, sous, fichiers in os.walk(d):
        sous.sort()
        for n in fichiers:
            p = os.path.join(base, n)
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for bloc in iter(lambda: f.read(1 << 20), b""):
                    h.update(bloc)
            out[os.path.relpath(p, d).replace(os.sep, "/")] = h.hexdigest()
    return out


def copier_verifie(src: str, dst: str, exclus=()) -> None:
    """Copie de l'arbre ``src`` dans ``dst`` (absent), vérifiée fichier par fichier ; ``exclus`` : chemins relatifs
    (« / ») laissés de côté. Jamais de déplacement."""
    if os.path.lexists(dst):
        raise ErreurInstall(f"{dst} existe déjà", f"{dst} already exists")

    def ignorer(d, noms):
        rel = os.path.relpath(d, src).replace(os.sep, "/")
        return [n for n in noms if (n if rel == "." else f"{rel}/{n}") in exclus
                or os.path.islink(os.path.join(d, n))]
    shutil.copytree(src, dst, ignore=ignorer)
    a, b = _inventaire(src), _inventaire(dst)
    a = {k: v for k, v in a.items() if not any(k == e or k.startswith(e + "/") for e in exclus)}
    if a != b:
        shutil.rmtree(dst, ignore_errors=True)
        raise ErreurInstall(f"copie de {src} différente de l'original", f"copy of {src} differs from the original")


def reporter_donnees(ancienne: str, nouvelle: str) -> list[str]:
    """Sauvegardes (``save`` et ``save-randomizer-*``) et réglages / manette / mods (``user``, sauf les mods de code
    construits pour l'ancien jeu) du dossier ``data`` de l'ancienne release vers celui de la nouvelle (qui n'en a
    pas encore). Copies vérifiées ; l'ancienne release n'est pas touchée. Renvoie les dossiers copiés."""
    faits = []
    os.makedirs(nouvelle, exist_ok=True)
    for n in sorted(os.listdir(ancienne)):
        src = os.path.join(ancienne, n)
        if not os.path.isdir(src) or os.path.islink(src):
            continue
        if n == "save" or n.startswith("save-randomizer-") or n == "user":
            dst = os.path.join(nouvelle, n)
            if os.path.lexists(dst):
                raise ErreurInstall(f"{dst} existe déjà dans la nouvelle release : rien n'est remplacé",
                                    f"{dst} already exists in the new release: nothing is replaced")
            copier_verifie(src, dst, EXCLUS_USER if n == "user" else ())
            faits.append(n)
    return faits
