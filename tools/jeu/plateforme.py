"""Différences Windows / Linux du côté joueur (dossiers, Cemu, ouverture de fichiers, presse-papiers…).

Sous Windows, chaque fonction rend EXACTEMENT ce que le code rendait avant (``%LOCALAPPDATA%``,
``%APPDATA%\\Cemu``, ``os.startfile``, ``DETACHED_PROCESS``…). Sous Linux (et les autres POSIX) :

- dossier de données du mod : ``$XDG_DATA_HOME/WWMP`` (défaut ``~/.local/share/WWMP``) ;
- Cemu natif / AppImage : ``settings.xml`` dans ``$XDG_CONFIG_HOME/Cemu`` (``~/.config/Cemu``), données
  (``mlc01``, ``graphicPacks``) dans ``$XDG_DATA_HOME/Cemu`` (``~/.local/share/Cemu``) ; anciennes versions :
  tout dans ``~/.local/share/Cemu`` ; mode portable : dossier ``portable`` à côté de l'AppImage ;
- Cemu Flatpak (``info.cemu.Cemu``) : ``~/.var/app/info.cemu.Cemu/config/Cemu`` et ``…/data/Cemu`` ;
  dans les réglages du lanceur, le chemin de Cemu vaut alors ``flatpak`` (lancé par ``flatpak run``).

Aucune fonction ne change un réglage du système ni ne demande de droits (jamais de sudo).
``forcer("linux")`` (tests) fait comme si l'on était sous Linux.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys

FLATPAK_ID = "info.cemu.Cemu"
FLATPAK_VALEURS = ("flatpak", "flatpak:" + FLATPAK_ID.lower(), FLATPAK_ID.lower())   # comparés en minuscules
DETACHED = 0x00000008               # DETACHED_PROCESS (Windows)
NOUVEAU_GROUPE = 0x00000200          # CREATE_NEW_PROCESS_GROUP (Windows)

_force: str | None = None


def systeme() -> str:
    """« windows », « linux » ou « autre » (macOS, BSD : traité comme Linux pour les chemins)."""
    if _force:
        return _force
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "autre"


def est_windows() -> bool:
    return systeme() == "windows"


def est_linux() -> bool:
    return systeme() == "linux"


@contextlib.contextmanager
def forcer(nom: str | None):
    """Tests : ``with forcer("linux"): …`` (chemins et choix Linux, sans rien toucher au système)."""
    global _force
    avant, _force = _force, nom
    try:
        yield
    finally:
        _force = avant


# ---------------------------------------------------------------------------
# Dossiers
# ---------------------------------------------------------------------------
def maison() -> str:
    """Dossier personnel (``$HOME`` d'abord sous Linux : les tests peuvent le changer)."""
    if not est_windows():
        h = os.environ.get("HOME")
        if h:
            return h
    return os.path.expanduser("~")


def donnees_locales() -> str:
    """Windows : ``%LOCALAPPDATA%`` (sinon ``~``, comme avant). Linux : ``$XDG_DATA_HOME`` ou ``~/.local/share``."""
    if est_windows():
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_DATA_HOME") or os.path.join(maison(), ".local", "share")


def config_utilisateur() -> str:
    """Windows : ``%APPDATA%`` (sinon ``~``). Linux : ``$XDG_CONFIG_HOME`` ou ``~/.config``."""
    if est_windows():
        return os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(maison(), ".config")


def dossier_wwmp() -> str:
    """``%LOCALAPPDATA%\\WWMP`` (Windows) ou ``~/.local/share/WWMP`` (Linux)."""
    return os.path.join(donnees_locales(), "WWMP")


def est_flatpak(cemu_exe: str | None) -> bool:
    """Le chemin de Cemu choisi désigne le Flatpak (``flatpak``, ``info.cemu.Cemu``) ?"""
    return isinstance(cemu_exe, str) and cemu_exe.strip().lower() in FLATPAK_VALEURS


def dossiers_cemu(cemu_exe: str | None = None, appdata: str | None = None) -> list[tuple[str, str]]:
    """Couples (dossier de settings.xml, dossier des données mlc01/graphicPacks) possibles, dans l'ordre.

    Windows : mode portable (``portable`` à côté de Cemu.exe), puis ``%APPDATA%\\Cemu`` (les deux dans le même
    dossier, comme avant). Linux : portable (à côté de l'AppImage ou du binaire), Flatpak en premier si Cemu est
    le Flatpak, XDG (Cemu 2.x), ancien dossier unique ``~/.local/share/Cemu``, puis Flatpak."""
    out: list[tuple[str, str]] = []
    if cemu_exe and not est_flatpak(cemu_exe):
        port = os.path.join(os.path.dirname(cemu_exe), "portable")
        if os.path.isdir(port):
            out.append((port, port))
    if est_windows():
        a = appdata if appdata is not None else os.environ.get("APPDATA", "")
        out.append((os.path.join(a, "Cemu"), os.path.join(a, "Cemu")))
        return out
    if appdata is not None:                  # tests / option explicite : un seul dossier de Cemu
        out.append((os.path.join(appdata, "Cemu"), os.path.join(appdata, "Cemu")))
        return out
    conf = os.environ.get("XDG_CONFIG_HOME") or os.path.join(maison(), ".config")
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(maison(), ".local", "share")
    fp = os.path.join(maison(), ".var", "app", FLATPAK_ID)
    flat = (os.path.join(fp, "config", "Cemu"), os.path.join(fp, "data", "Cemu"))
    natif = (os.path.join(conf, "Cemu"), os.path.join(data, "Cemu"))
    ancien = (os.path.join(data, "Cemu"), os.path.join(data, "Cemu"))
    if est_flatpak(cemu_exe):
        out += [flat, natif, ancien]
    else:
        out += [natif, ancien, flat]
    return out


def dossier_cemu(cemu_exe: str | None = None, appdata: str | None = None) -> tuple[str, str]:
    """Le couple (config, données) de Cemu à utiliser : le premier qui a un settings.xml, sinon le premier.
    Windows : toujours le premier (portable s'il existe, sinon ``%APPDATA%\\Cemu``), comme avant ; Linux : le
    dossier ``portable`` s'il existe (Cemu l'utilise alors d'office), sinon le premier qui a un settings.xml."""
    cands = dossiers_cemu(cemu_exe, appdata)
    if est_windows() or os.path.basename(cands[0][0]) == "portable":
        return cands[0]
    for c, d in cands:
        if os.path.isfile(os.path.join(c, "settings.xml")):
            return c, d
    return cands[0]


# ---------------------------------------------------------------------------
# Cemu : présence, lancement
# ---------------------------------------------------------------------------
def flatpak_installe() -> bool:
    """Le Flatpak de Cemu est installé (dossier de l'application, utilisateur ou système) ? Lecture seule."""
    for d in (os.path.join(maison(), ".local", "share", "flatpak", "app", FLATPAK_ID),
              os.path.join("/var/lib/flatpak/app", FLATPAK_ID)):
        if os.path.isdir(d):
            return True
    return False


def cemu_present(cemu_exe: str | None) -> bool:
    """Le Cemu choisi existe ? Windows : fichier (comme avant). Linux : fichier, ou Flatpak installé."""
    if not cemu_exe:
        return False
    if est_flatpak(cemu_exe) and not est_windows():
        return flatpak_installe()
    return os.path.isfile(cemu_exe)


def cemu_defauts() -> tuple[str, ...]:
    """Emplacements habituels de Cemu (le premier qui existe est pris par défaut)."""
    if est_windows():
        return (r"C:\Program Files\Cemu_2.6\Cemu.exe", r"C:\Program Files\Cemu\Cemu.exe")
    h = maison()
    out = []
    for d in (os.path.join(h, "Applications"), os.path.join(h, "Téléchargements"), os.path.join(h, "Downloads"),
              os.path.join(h, ".local", "bin"), os.path.join(h, "Cemu"), h):
        try:
            noms = sorted(os.listdir(d), reverse=True)
        except OSError:
            continue
        out += [os.path.join(d, n) for n in noms if n.lower().startswith("cemu") and n.lower().endswith(".appimage")]
    out += ["/usr/bin/cemu", "/usr/local/bin/cemu", "/usr/bin/Cemu"]
    return tuple(out)


def argv_cemu(cemu_exe: str, rpx: str | None) -> list[str]:
    """Ligne de commande de Cemu (option ``-g`` = jeu à lancer). Flatpak : ``flatpak run info.cemu.Cemu``."""
    if est_flatpak(cemu_exe) and not est_windows():
        base = ["flatpak", "run", FLATPAK_ID]
    else:
        base = [cemu_exe]
    return base + (["-g", rpx] if rpx else [])


def cwd_cemu(cemu_exe: str) -> str | None:
    if est_flatpak(cemu_exe) and not est_windows():
        return None
    return os.path.dirname(cemu_exe) or None


def options_detache() -> dict:
    """Arguments de ``subprocess.Popen`` pour un programme indépendant du lanceur (Cemu, fenêtre…)."""
    if est_windows():
        return {"creationflags": DETACHED | NOUVEAU_GROUPE}
    return {"start_new_session": True}


def ouvrir(chemin: str, popen=subprocess.Popen) -> None:
    """Ouvre un fichier ou un dossier avec le programme par défaut (Explorateur / ``xdg-open``)."""
    if est_windows():
        os.startfile(chemin)          # type: ignore[attr-defined]
        return
    outil = "open" if sys.platform == "darwin" else "xdg-open"
    if not shutil.which(outil):
        raise OSError(f"« {outil} » introuvable : ouvre toi-même {chemin}")
    popen([outil, chemin], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          start_new_session=True, close_fds=True)


# ---------------------------------------------------------------------------
# Presse-papiers et langue (Linux : outils du bureau s'ils sont là, sinon rien)
# ---------------------------------------------------------------------------
def _outil_presse_papiers(ecrire: bool) -> list[str] | None:
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    choix = []
    if wayland:
        choix.append(["wl-copy"] if ecrire else ["wl-paste", "--no-newline"])
    choix += [["xclip", "-selection", "clipboard"] + ([] if ecrire else ["-o"]),
              ["xsel", "--clipboard", "--input" if ecrire else "--output"]]
    for c in choix:
        if shutil.which(c[0]):
            return c
    return None


def presse_papiers_ecrire_posix(texte: str) -> bool:
    c = _outil_presse_papiers(True)
    if c is None:
        return False
    try:
        subprocess.run(c, input=texte.encode("utf-8"), timeout=3, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def presse_papiers_lire_posix() -> str | None:
    c = _outil_presse_papiers(False)
    if c is None:
        return None
    try:
        r = subprocess.run(c, capture_output=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.decode("utf-8", "replace")[:256] if r.returncode == 0 else None


def langue_posix(env=None) -> str:
    """« fr » si la langue du bureau est le français (LANGUAGE, LC_ALL, LC_MESSAGES, LANG), sinon « en »."""
    env = os.environ if env is None else env
    for k in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = (env.get(k) or "").strip()
        if v and v not in ("C", "POSIX", "C.UTF-8"):
            return "fr" if v.lower().startswith("fr") else "en"
    return "fr"


def message(titre: str, texte: str, erreur: bool = False) -> None:
    """Boîte de message (Windows) ; ailleurs : ``notify-send`` s'il existe, et toujours la sortie d'erreur."""
    if est_windows():
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, texte, titre, 0x10 if erreur else 0x40)
            return
        except Exception:
            pass
    print(f"{titre} : {texte}", file=sys.stderr)
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", titre, texte], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
