"""Support des mods de code du portage natif ZeldaWWHDRecomp, construit par le lanceur, jeu FERMÉ, en lançant
l'outil du portage EXACTEMENT comme le fait le jeu quand on allume un mod de code (F1 > Mods > Switch on ; plus de
« la première fois, dans le jeu ») (docs/dev/PORTAGE_NATIF.md §8.5).

Ce que fait le jeu (lu dans les sources v0.5.1, lecture seule ; rien deviné) :

* ``runtime/src/mods/code_mods.cpp`` ``startup`` : le dossier d'installation est le parent du dossier du jeu
  construit (``<données>/bin/wwhd.exe`` -> ``<données>``) ; ``WWHD_INSTALL_DIR`` et ``WWHD_GUEST_BUILD_CONFIG``
  (= ``<données>/guest-sdk.json``) sont posées pour la suite ;
* ``begin`` : lit ``guest-sdk.json`` ; ``python`` (liste non vide de textes, le premier résolu comme une commande),
  ``setup`` et ``data_dir`` résolus contre le dossier de ce fichier par ``guestmods::resolve_path``
  (``mods/guest_build.h`` : « $RELEASE/… » contre ``$APPDIR`` de l'AppImage qui tourne, chemin relatif contre le
  dossier du fichier, chemin absolu d'un ancien montage « .mount_XXXXXX » reporté sur l'image qui tourne) ; un des
  deux vide : « Code-mod setup tools are missing; run setup again ». Puis efface
  ``<data>/code-mods-status.json`` et ``<data>/code-mods-cancel`` et lance ::

      <python…> <setup> --data-dir <data> --rebuild-code-mods --code-mods 1
          --code-mods-status <data>/code-mods-status.json --code-mods-cancel <data>/code-mods-cancel --jobs 4

  pendant la construction, le statut (publié atomiquement par ``tools/installer/code_mods.py`` ``rebuild``) vaut
  ``{"state": "building", "stage", "done", "total"}`` ; annuler = écrire « cancel » dans le fichier d'annulation ;
  à la fin : réussi si le code de sortie est 0 ET ``state`` == "ready" (sinon le message du statut, « Code-mod
  rebuild failed; see setup.log. Previous build retained. » par défaut) ; alors ``hostui::set("code-mods", "1")`` ;
* ``hostui::set`` (``gfx/vulkan/overlay_sdl.cpp``) : ``<réglages>/settings.ini`` (ou ``WWHD_SETTINGS``) relu
  (lignes « clé=valeur », vides et « # » ignorées, la dernière valeur d'une clé gagne), la clé posée, puis tout
  réécrit : « # Wind Waker HD settings (settings overlay, Esc or F1) » puis chaque « clé=valeur » triée
  (``std::map``), par ``settings.ini.tmp`` puis remplacement (flux texte : « \\r\\n » sous Windows) ;
* ``selected`` (au démarrage, le binaire choisi) : ``code-mods-active.json`` ``state`` "ready", son ``exe`` dans
  ``code-builds/<16 premiers chiffres de fingerprint>/bin`` (ou les 64 chiffres : releases jusqu'à 0.2.12), et
  ``ready.json`` de ce dossier avec les mêmes ``fingerprint`` et ``hooks`` ; ``enabled`` : réglage « code-mods » = "1".

Ce n'est PAS le lanceur qui compile : il lance l'outil du portage (son Python, son setup.py, son compilateur).
Jeu ouvert, ``guest-sdk.json`` absent ou illisible, outil introuvable : ErreurSupport (Jouer retombe alors sur les
instructions manuelles, avec la raison).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import sys
import threading
import time

NOM_CONFIG = "guest-sdk.json"
NOM_STATUT = "code-mods-status.json"
NOM_ANNULER = "code-mods-cancel"
NOM_ACTIF = "code-mods-active.json"
MARQUEUR = "$RELEASE/"
CLE_REGLAGE = "code-mods"
ENTETE = b"# Wind Waker HD settings (settings overlay, Esc or F1)"
JOBS = "4"
PAUSE = 0.5               # s entre deux lectures du statut (le jeu le relit à chaque image)
MESSAGE_DEFAUT = "Code-mod rebuild failed; see setup.log. Previous build retained."
CREATE_NO_WINDOW = 0x08000000


class ErreurSupport(Exception):
    """Refus propre (fr, en) : le support n'est pas construit, l'ancien jeu est gardé."""

    def __init__(self, fr: str, en: str | None = None, annule: bool = False):
        super().__init__(fr)
        self.fr, self.en, self.annule = fr, en or fr, annule


def _lire_json(chemin: str, limite: int = 1 << 20):
    try:
        if os.path.getsize(chemin) > limite:
            return None
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# guestmods::resolve_path (mods/guest_build.h)
# ---------------------------------------------------------------------------
def _parties(chemin: str) -> list[str]:
    """Les composants de ``relative_path()`` (sans la racine ni le lecteur)."""
    _lect, reste = os.path.splitdrive(chemin)
    return [p for p in reste.replace("\\", "/").split("/") if p]


def resoudre(valeur: str, base: str, release: str, commande: bool = False) -> str:
    """Un chemin stocké de ``guest-sdk.json``, résolu comme le jeu : le marqueur « $RELEASE/ » contre ``release``
    ($APPDIR ; resté tel quel sans AppImage), un chemin relatif contre ``base`` (le dossier du fichier ; un nom de
    programme nu reste une recherche dans PATH quand ``commande``), un chemin absolu d'un montage « .mount_… » reporté
    dans ``release`` s'il y existe."""
    if not valeur:
        return valeur
    if valeur.startswith(MARQUEUR):
        return valeur if not release else os.path.normpath(os.path.join(release, valeur[len(MARQUEUR):]))
    if not os.path.isabs(valeur):
        if not base or (commande and not os.path.dirname(valeur)):
            return valeur
        return os.path.normpath(os.path.join(base, valeur))
    if not release:
        return valeur
    queue, monte = [], False
    for p in _parties(valeur):
        if p.startswith(".mount_"):
            queue, monte = [], True
        elif monte:
            queue.append(p)
    if not monte or not queue:
        return valeur
    deplace = os.path.normpath(os.path.join(release, *queue))
    return deplace if os.path.exists(deplace) else valeur


def commande(donnees: str, release: str = "") -> dict:
    """La commande que lance le jeu (``code_mods.cpp`` ``begin``) pour le dossier d'installation ``donnees`` :
    {argv, statut, annuler, data, setup, config}. ``release`` : ``$APPDIR`` d'un AppImage monté (sinon ""). Lève
    ErreurSupport (« lance le setup du portage de nouveau » : ce que dit le jeu)."""
    config = os.path.join(donnees, NOM_CONFIG)
    cfg = _lire_json(config)
    if not os.path.isfile(config):
        raise ErreurSupport(f"{NOM_CONFIG} absent du portage ({donnees}) : outils de construction inconnus (relance "
                            "la préparation du portage)", f"{NOM_CONFIG} missing from the port ({donnees}): build tools "
                            "unknown (run the port's setup again)")
    if not isinstance(cfg, dict):
        raise ErreurSupport(f"{NOM_CONFIG} illisible", f"unreadable {NOM_CONFIG}")
    base = os.path.dirname(os.path.abspath(config))
    py = cfg.get("python")
    if not isinstance(py, list) or not py or any(not isinstance(a, str) or not a or "\0" in a for a in py):
        raise ErreurSupport(f"{NOM_CONFIG} : commande Python invalide", f"{NOM_CONFIG}: invalid Python command")
    argv = list(py)
    argv[0] = resoudre(argv[0], base, release, True)

    def chemin(nom):
        v = cfg.get(nom)
        p = resoudre(v if isinstance(v, str) else "", base, release)
        if not p:
            raise ErreurSupport(f"{NOM_CONFIG} : « {nom} » manquant (outils de construction du portage absents : relance "
                                "sa préparation)", f"{NOM_CONFIG}: “{nom}” missing (the port's build tools are "
                                "missing: run its setup again)")
        return p
    data, setup = chemin("data_dir"), chemin("setup")
    if setup.startswith(MARQUEUR) or data.startswith(MARQUEUR):
        raise ErreurSupport("portage en AppImage : l'image doit être montée pour trouver ses outils",
                            "AppImage port: the image must be mounted to find its tools")
    statut, annuler = os.path.join(data, NOM_STATUT), os.path.join(data, NOM_ANNULER)
    argv += [setup, "--data-dir", data, "--rebuild-code-mods", "--code-mods", "1", "--code-mods-status", statut,
             "--code-mods-cancel", annuler, "--jobs", JOBS]
    return {"argv": argv, "statut": statut, "annuler": annuler, "data": data, "setup": setup, "config": config}


# ---------------------------------------------------------------------------
# État : binaire choisi (selected) et réglage « code-mods » (enabled)
# ---------------------------------------------------------------------------
def _meme(a: str, b: str) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def construit(donnees: str) -> bool:
    """Le jeu démarrera-t-il le code AVEC le support des mods de code (``selected`` + ``hooks`` vrai) ?"""
    actif = _lire_json(os.path.join(donnees, NOM_ACTIF))
    if not isinstance(actif, dict) or actif.get("state") != "ready" or actif.get("hooks") is not True:
        return False
    exe, emp = actif.get("exe"), actif.get("fingerprint")
    if not isinstance(exe, str) or not isinstance(emp, str) or not emp:
        return False
    cache = os.path.join(donnees, "code-builds", emp[:16])
    if not os.path.isdir(cache):
        cache = os.path.join(donnees, "code-builds", emp)
    if not os.path.isfile(exe) or not _meme(os.path.dirname(exe), os.path.join(cache, "bin")):
        return False
    rec = _lire_json(os.path.join(cache, "ready.json"))
    return isinstance(rec, dict) and rec.get("fingerprint") == emp and rec.get("hooks") is True


def chemin_reglages(config: str) -> str:
    return os.path.join(config, "settings.ini")


def _lire_reglages(brut: bytes, systeme: str) -> dict[bytes, bytes]:
    """``load_locked`` : « clé=valeur » par ligne ; vides et « # » ignorées ; la dernière gagne. Flux texte sous
    Windows : « \\r\\n » lu comme « \\n »."""
    if systeme == "win32":
        brut = brut.replace(b"\r\n", b"\n")
    vals: dict[bytes, bytes] = {}
    for ligne in brut.split(b"\n"):
        if not ligne or ligne.startswith(b"#"):
            continue
        i = ligne.find(b"=")
        if i >= 0:
            vals[ligne[:i]] = ligne[i + 1:]
    return vals


def reglage(config: str, systeme: str | None = None) -> str | None:
    """Valeur de « code-mods » dans settings.ini (None : absent, le jeu le prend pour « non »)."""
    try:
        with open(chemin_reglages(config), "rb") as f:
            v = _lire_reglages(f.read(1 << 20), systeme or sys.platform).get(CLE_REGLAGE.encode())
    except OSError:
        return None
    return None if v is None else v.decode("utf-8", "replace")


def ecrire_reglage(config: str, base_copies: str, jeu_tourne=None, systeme: str | None = None,
                   valeur: str = "1") -> bool:
    """``hostui::set("code-mods", valeur)`` : settings.ini relu, la clé posée, réécrit trié avec l'en-tête du jeu.
    Jeu FERMÉ ; copie de l'ancien fichier dans ``base_copies`` ; fichier temporaire à nous puis remplacement. Déjà
    la bonne valeur : rien. True si écrit. Lève ErreurSupport."""
    sy = systeme or sys.platform
    p = chemin_reglages(config)
    try:
        with open(p, "rb") as f:
            brut = f.read(1 << 20)
    except FileNotFoundError:
        brut = None
    except OSError as e:
        raise ErreurSupport(f"settings.ini illisible ({e.__class__.__name__})",
                            f"unreadable settings.ini ({e.__class__.__name__})") from None
    vals = _lire_reglages(brut or b"", sy)
    cle = CLE_REGLAGE.encode()
    if vals.get(cle) == valeur.encode():
        return False
    if jeu_tourne is not None and jeu_tourne():
        raise ErreurSupport("le jeu est ouvert : settings.ini n'est écrit que jeu fermé",
                            "the game is open: settings.ini is only written with the game closed")
    vals[cle] = valeur.encode()
    fin = b"\r\n" if sy == "win32" else b"\n"
    neuf = ENTETE + fin + b"".join(k + b"=" + v + fin for k, v in sorted(vals.items()))
    os.makedirs(config, exist_ok=True)
    if brut is not None:
        os.makedirs(base_copies, exist_ok=True)
        copie = os.path.join(base_copies, f"settings-{time.strftime('%Y%m%d-%H%M%S')}-code-mods.ini")
        with open(copie, "wb") as f:
            f.write(brut)
        anciens = sorted((n for n in os.listdir(base_copies) if n.startswith("settings-")), reverse=True)
        for n in anciens[10:]:
            try:
                os.remove(os.path.join(base_copies, n))
            except OSError:
                pass
    tmp = p + ".wwmp.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(neuf)
            f.flush()
            os.fsync(f.fileno())
        if jeu_tourne is not None and jeu_tourne():
            raise ErreurSupport("le jeu s'est ouvert : settings.ini pas écrit",
                                "the game was opened: settings.ini not written")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return True


def etat(inst: dict, systeme: str | None = None) -> dict:
    """{construit, reglage ("1", "0"… ou None), config_sdk (guest-sdk.json présent), pret (construit et réglage
    « 1 »)} pour une installation lue par ``jeu.portage.lire``."""
    d, c = inst.get("donnees") or "", inst.get("config") or ""
    out = {"construit": bool(d) and construit(d), "reglage": reglage(c, systeme) if c else None,
           "config_sdk": bool(d) and os.path.isfile(os.path.join(d, NOM_CONFIG))}
    out["pret"] = out["construit"] and out["reglage"] == "1"
    return out


# ---------------------------------------------------------------------------
# AppImage : l'image montée le temps de la construction ($APPDIR, pour « $RELEASE/… »)
# ---------------------------------------------------------------------------
def est_appimage(inst: dict) -> bool:
    exe = inst.get("lanceur") or ""
    return bool(exe) and exe.lower().endswith(".appimage")


ENV_EXTRAIRE = "APPIMAGE_EXTRACT_AND_RUN"   # runtime AppImage de type 2 : sans FUSE, extrait puis lance


def fuse_disponible(which=shutil.which, dev: str = "/dev/fuse") -> bool:
    """Le runtime AppImage de type 2 du portage (type2-runtime 8f39b89 : libfuse 3 liée statiquement) monte l'image
    par ``fusermount3`` (ou ``fusermount``) et ``/dev/fuse``. Arch : paquet ``fuse3`` (souvent déjà là). Sans eux :
    ``APPIMAGE_EXTRACT_AND_RUN=1`` / ``--appimage-extract``, qui n'ont pas besoin de FUSE."""
    return bool(which("fusermount3") or which("fusermount")) and os.path.exists(dev)


class Montage:
    """``<AppImage> --appimage-mount`` (runtime AppImage de type 2) : la première ligne de sa sortie est le point de
    montage, gardé tant que le processus vit. Sans FUSE (``fuse_disponible`` faux) ou montage raté : repli
    ``<AppImage> --appimage-extract`` dans un dossier temporaire (``squashfs-root``, effacé à la fin), qui n'en a pas
    besoin."""

    def __init__(self, appimage: str, popen=subprocess.Popen, delai: float = 60.0, fuse=None, temp: str | None = None):
        self.appimage, self.popen, self.delai = appimage, popen, delai
        self.fuse = fuse_disponible if fuse is None else fuse
        self.temp = temp
        self.proc = None
        self.extrait = ""

    def __enter__(self) -> str:
        point = ""
        if self.fuse():
            try:
                self.proc = self.popen([self.appimage, "--appimage-mount"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            except OSError:
                self.proc = None
            if self.proc is not None:
                lu: list[str] = []
                t = threading.Thread(target=lambda: lu.append(self.proc.stdout.readline()), daemon=True)
                t.start()
                t.join(self.delai)
                point = (lu[0] if lu else "").strip()
                if not point or not os.path.isdir(point):
                    self._fermer()
                    point = ""
        if not point:
            point = self._extraire()
        if not point:
            raise ErreurSupport("AppImage du portage ni montée (--appimage-mount : FUSE ? paquet fuse3) ni extraite "
                                "(--appimage-extract)", "the port's AppImage could neither be mounted "
                                "(--appimage-mount: FUSE? fuse3 package) nor extracted (--appimage-extract)")
        return point

    def _extraire(self) -> str:
        """``--appimage-extract`` dans un dossier temporaire : son ``squashfs-root`` ("" si raté)."""
        try:
            d = tempfile.mkdtemp(prefix="wwmp-appimage-", dir=self.temp)
        except OSError:
            return ""
        self.extrait = d
        try:
            p = self.popen([self.appimage, "--appimage-extract"], cwd=d, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            code = p.wait(600)
        except Exception:                          # noqa: BLE001
            code = -1
        racine = os.path.join(d, "squashfs-root")
        if code == 0 and os.path.isdir(racine):
            return racine
        self._effacer()
        return ""

    def _effacer(self) -> None:
        d, self.extrait = self.extrait, ""
        if d:
            shutil.rmtree(d, ignore_errors=True)

    def _fermer(self) -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.terminate()
            p.wait(10)
        except Exception:                          # noqa: BLE001
            try:
                p.kill()
            except Exception:                      # noqa: BLE001
                pass

    def __exit__(self, *_):
        self._fermer()
        self._effacer()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
def _environnement(donnees: str, config: str, release: str, appimage: str) -> dict:
    env = dict(os.environ)
    env["WWHD_INSTALL_DIR"] = donnees
    env["WWHD_GUEST_BUILD_CONFIG"] = config
    env.pop("WWHD_CODE_MODS", None)
    if release:
        env["APPDIR"], env["APPIMAGE"] = release, appimage
        if appimage and os.path.isdir(appimage + ".home"):   # mode portable du runtime AppImage : son $HOME
            env["HOME"] = appimage + ".home"
    return env


def construire(inst: dict, base_copies: str, journal: str, progres=None, annule=None, jeu_tourne=None,
               popen=subprocess.Popen, monter=Montage, systeme: str | None = None) -> dict:
    """Construit le support des mods de code de ``inst`` avec l'outil du portage, puis pose « code-mods=1 ».
    ``progres(stage, done, total)``, ``annule()`` -> True pour annuler (le fichier d'annulation est écrit : l'outil
    s'arrête au prochain point sûr, l'ancien jeu est gardé). {argv, statut (le dernier), reglage_ecrit, secondes}.
    Lève ErreurSupport (``annule`` vrai si annulé)."""
    donnees = inst.get("donnees") or ""
    if not donnees or not inst.get("config"):
        raise ErreurSupport("installation du portage inconnue", "unknown port installation")
    if jeu_tourne is not None and jeu_tourne():
        raise ErreurSupport("le jeu (portage natif) est ouvert : ferme-le d'abord",
                            "the game (native port) is open: close it first")
    if not os.path.isfile(os.path.join(donnees, NOM_CONFIG)):
        commande(donnees)                          # même refus, même texte
    image = inst.get("lanceur") if est_appimage(inst) else ""
    debut = time.monotonic()
    if image:
        with monter(image) as point:
            r = _executer(inst, commande(donnees, point), point, image, journal, base_copies, progres, annule,
                          jeu_tourne, popen, systeme)
    else:
        r = _executer(inst, commande(donnees), "", "", journal, base_copies, progres, annule, jeu_tourne, popen,
                      systeme)
    r["secondes"] = round(time.monotonic() - debut, 1)
    return r


def _executer(inst, cmd, release, image, journal, base_copies, progres, annule, jeu_tourne, popen, systeme) -> dict:
    statut, annuler = cmd["statut"], cmd["annuler"]
    for f in (statut, annuler):
        try:
            os.remove(f)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise ErreurSupport(f"{os.path.basename(f)} pas effaçable ({e.__class__.__name__})") from None
    sy = systeme or sys.platform
    os.makedirs(os.path.dirname(journal) or ".", exist_ok=True)
    options = {"creationflags": CREATE_NO_WINDOW} if sy == "win32" else {}
    with open(journal, "ab") as log:
        log.write(("\n--- " + time.strftime("%Y-%m-%d %H:%M:%S") + " : " + " ".join(cmd["argv"]) + "\n")
                  .encode("utf-8", "replace"))
        log.flush()
        try:
            proc = popen(cmd["argv"], cwd=inst["donnees"], env=_environnement(inst["donnees"], cmd["config"],
                                                                               release, image),
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options)
        except OSError as e:
            raise ErreurSupport(f"outil du portage pas lancé : {cmd['argv'][0]} ({e.__class__.__name__})",
                                f"the port's tool could not start: {cmd['argv'][0]} ({e.__class__.__name__})") \
                from None
        demande = False
        dernier = None
        while True:
            fini = proc.poll() is not None
            if not demande and annule is not None and annule():
                demande = True
                try:
                    with open(annuler, "w", encoding="ascii") as f:
                        f.write("cancel")
                except OSError:
                    proc.terminate()
            v = _lire_json(statut)
            if isinstance(v, dict) and v != dernier:
                dernier = v
                if progres is not None and v.get("state") == "building":
                    try:
                        progres(str(v.get("stage") or ""), int(v.get("done") or 0), int(v.get("total") or 0))
                    except (TypeError, ValueError):
                        pass
            if fini:
                break
            time.sleep(PAUSE)
        code = proc.wait()
    rapport = _lire_json(statut)
    rapport = rapport if isinstance(rapport, dict) else {}
    if code or rapport.get("state") != "ready":
        msg = str(rapport.get("message") or MESSAGE_DEFAUT)
        if demande:
            raise ErreurSupport("construction du support des mods de code annulée (l'ancien jeu est gardé)",
                                "code-mod support build cancelled (the previous game is kept)", annule=True)
        raise ErreurSupport(f"construction du support des mods de code ratée : {msg}",
                            f"code-mod support build failed: {msg}")
    if not construit(inst["donnees"]):
        raise ErreurSupport("l'outil du portage dit « ready » mais le jeu construit n'est pas choisi "
                            "(code-mods-active.json)", "the port's tool says “ready” but the built game is not "
                            "selected (code-mods-active.json)")
    ecrit = ecrire_reglage(inst["config"], base_copies, jeu_tourne, sy)
    return {"argv": cmd["argv"], "statut": rapport, "reglage_ecrit": ecrit}

