"""Le jeu sur le portage natif ZeldaWWHDRecomp (au lieu de Cemu) : mémoire du jeu et boîte aux lettres du mod C.

Le portage met la mémoire de la Wii U à une adresse hôte FIXE (``runtime/include/ppc.h`` du portage :
``PPC_MEM_BASE`` = 0x200000000000 sous Windows, Linux x86-64 et macOS ; 0x1000000000 sous Android et Linux arm64),
réservée et engagée d'un bloc de 4 Gio (``runtime/src/core.cpp``) ; le code et les données du RPX y sont chargés aux
mêmes adresses que sous Cemu (même jeu USA v0). Une adresse invitée ``ea`` est donc à ``base + ea`` dans le processus
du jeu (« Wind Waker HD.exe ») : pas de recherche de base comme avec Cemu. On réutilise ``jeu.cemu.GuestMemory``
(lecture et écriture big-endian par-dessus winmem / linmem) avec cette base : tout le client marche tel quel.

Le mod C (``natif/mod_wwmp``, Mod SDK v2 du portage) expose la MÊME boîte aux lettres que le pack Cemu WWMP_Mailbox
(magie WWMPMBX1, version 19, mêmes offsets), en globale du mod, dans la zone des mods (0x7F000000-0x80000000, une
part alignée à 64 Kio par mod). On la trouve par sa magie dans cette zone, et on ne l'accepte que si la zone propre
au portage qui la suit (« WWMPNATF », +0x1AE0) porte son adresse (écrite par le mod au démarrage du jeu) : une copie
de la magie ailleurs, ou une boîte d'avant le démarrage, est ignorée.

Linux (lu dans les sources v0.5.1 : ``tools/installer/gui/setup_gui.cpp`` ``launch_game``, ``runtime/src/platform/
host.h``) : ``wind-waker-hd`` (zip) ou l'AppImage (runtime de type 2 -> ``AppRun`` -> ``wind-waker-hd``) REMPLACE
son processus par le jeu (``execv`` de ``<données>/bin/wwhd`` ou ``<données>/code-builds/<id>/bin/wwhd``) : le pid
lancé devient ``wwhd`` (``comm``) ; avec ``APPIMAGE_EXTRACT_AND_RUN`` (sans FUSE), le jeu est un ENFANT du pid
lancé. Le processus du jeu s'appelle donc ``wwhd`` ; ``wind-waker-hd`` n'est que la fenêtre de préparation (pas de
mémoire du jeu) : ``wwhd`` est cherché d'abord.

``kernel.yama.ptrace_scope`` = 1 (Arch, Ubuntu… par défaut) : seul un ANCÊTRE du jeu peut ouvrir sa mémoire. Le
lanceur WWMP qui a lancé le jeu l'est ; le client (son autre enfant) ne l'est pas. Le lanceur ouvre donc
``/proc/<pid>/mem`` lui-même et le client en hérite (``ENV_FD`` / ``ENV_PID``, ``pass_fds``) : le droit n'est
vérifié qu'à l'ouverture. Aucun réglage du système n'est touché.
"""
from __future__ import annotations

import os
import platform
import struct
import sys

from jeu import procmem
from jeu.cemu import GuestMemory

BASE_HOTE = 0x2000_0000_0000            # PPC_MEM_BASE : Windows, Linux x86-64, macOS
BASE_HOTE_ARM64 = 0x10_0000_0000        # Android, Linux arm64
ZONE_MODS = (0x7F00_0000, 0x8000_0000)
if sys.platform == "win32":
    NOMS_NATIF = ("Wind Waker HD.exe", "wwhd.exe")
else:                                   # le jeu (« wwhd », après l'execv du lanceur du portage), puis ce lanceur
    NOMS_NATIF = ("wwhd", "wind-waker-hd")  # (zip : « wind-waker-hd » ; AppImage : AppRun -> wind-waker-hd -> wwhd)
ENV_FD = "WWMP_NATIF_MEM_FD"            # Linux : descripteur de /proc/<pid>/mem ouvert par le lanceur (ancêtre du jeu)
ENV_PID = "WWMP_NATIF_PID"              # … et le pid du jeu qu'il désigne

MAGIE_BOITE = b"WWMPMBX1"
VERSION_MIN = 19
# zone propre au portage (natif/mod_wwmp/boite.h : NATIF_*, ZoneNatif ; tests/test_natif_boite.py le vérifie)
NATIF_OFF = 0x1AE0
MAGIE_NATIF = b"WWMPNATF"
NATIF_FORMAT = 1
NATIF_TAILLE = 0x40
OFFN_FORMAT, OFFN_BOITE, OFFN_SOCLE, OFFN_PROFIL_FANTOME, OFFN_PROFIL_MODELE = 0x08, 0x0C, 0x10, 0x14, 0x18
OFFN_PAS, OFFN_VERSION_MOD, OFFN_REQUETES, OFFN_FILTREES = 0x1C, 0x20, 0x24, 0x28
SOCLE = {0x01: "commandes", 0x02: "fantômes", 0x04: "WwmpModel", 0x08: "marionnettes", 0x10: "IA suspendue",
         0x20: "yeux (ZOn)", 0x40: "passe des yeux", 0x80: "pause vivante", 0x100: "randomizer", 0x200: "confort",
         0x400: "code du randomizer par fichiers"}
PROC_FANTOME, PROC_MODELE = 0x1F0, 0x1F1
REFUS_PROFIL = {-1: "profil refusé (descripteur invalide)", -2: "numéro ou nom déjà pris (un autre mod WWMP actif ?)",
                -3: "enregistrement fermé (pas dans l'accroche de GameMain)",
                -4: "profils natifs indisponibles (support des mods de code ?)", -5: "64 profils déjà pris"}


def base_hote(machine: str | None = None, systeme: str | None = None) -> int:
    """Base hôte de la mémoire invitée du portage pour ce système."""
    machine = (machine if machine is not None else platform.machine()).lower()
    systeme = systeme if systeme is not None else sys.platform
    if systeme != "win32" and machine in ("aarch64", "arm64") and systeme != "darwin":
        return BASE_HOTE_ARM64
    return BASE_HOTE


def find_natif_pids(robot: bool = False) -> list[int]:
    """PID du jeu natif, sans doublon. Sans ``robot`` : SANS le jeu caché du joueur robot (``jeu.pid`` de son
    dossier, client/robot.py) : sur le PC qui héberge l'essai du robot, deux « wwhd.exe » tournent et le client, le
    lanceur, la détection « jeu ouvert » du JOUEUR ne doivent voir que le sien. Aucun fichier : rien n'est filtré."""
    out: list[int] = []
    for nom in NOMS_NATIF:
        for p in procmem.find_pids(nom):
            if p not in out:
                out.append(p)
    if not robot and out:
        caches = pids_robot()
        if caches:
            out = [p for p in out if p not in caches]
    return out


# --- jeu caché du joueur robot (client/robot.py, docs/etudes/joueur_robot.md) ---------------------------------------
ENV_ROBOT_DOSSIER = "WWMP_ROBOT_DOSSIER"     # dossier du robot (défaut : %LOCALAPPDATA%\WWMP\robot) ; les tests


def dossier_robot(env=None) -> str:
    e = os.environ if env is None else env
    if e.get(ENV_ROBOT_DOSSIER):
        return e[ENV_ROBOT_DOSSIER]
    if sys.platform == "win32":
        base = e.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "WWMP", "robot")
    return os.path.join(e.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
                        "wwmp", "robot")


def fichier_pid_robot(env=None) -> str:
    return os.path.join(dossier_robot(env), "jeu.pid")


def creation_processus(pid: int) -> int | None:
    """Heure de création du processus (unité du système), pour qu'un pid réutilisé ne soit jamais pris pour le jeu
    du robot ; None si illisible (le pid seul compte alors)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, int(pid))        # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return None
            try:
                t = [wintypes.FILETIME() for _ in range(4)]
                if not k32.GetProcessTimes(h, *[ctypes.byref(x) for x in t]):
                    return None
                return (t[0].dwHighDateTime << 32) | t[0].dwLowDateTime
            finally:
                k32.CloseHandle(h)
        with open(f"/proc/{int(pid)}/stat", "rb") as f:
            champs = f.read().rsplit(b")", 1)[1].split()
        return int(champs[19])                                   # starttime (22e champ)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def pids_robot(fichier: str | None = None) -> set[int]:
    """Pid(s) du jeu caché du robot encore valables (« pid création » par ligne ; création différente : pid réutilisé
    par un autre programme, ignoré)."""
    out: set[int] = set()
    try:
        with open(fichier or fichier_pid_robot(), encoding="ascii", errors="replace") as f:
            lignes = f.read(4096).split("\n")
    except OSError:
        return out
    for ligne in lignes:
        mots = ligne.split()
        try:
            pid = int(mots[0])
            cree = int(mots[1]) if len(mots) > 1 else None
        except (ValueError, IndexError):
            continue
        if pid > 0 and (cree is None or creation_processus(pid) in (None, cree)):
            out.add(pid)
    return out


def ouvrir_fd(pid: int, racine: str = "/proc") -> int:
    """Linux : ``/proc/<pid>/mem`` ouvert en lecture-écriture (non héritable : ``pass_fds`` le transmet au client).
    Lève OSError (PermissionError : pas ancêtre du jeu et ptrace_scope > 0, autre compte…)."""
    return os.open(os.path.join(racine, str(int(pid)), "mem"), os.O_RDWR | getattr(os, "O_CLOEXEC", 0))


def fd_herite(pid: int | None = None, env=None) -> tuple[int, int] | None:
    """(pid, fd) transmis par le lanceur (``ENV_PID`` / ``ENV_FD``), ou None (rien transmis, valeurs illisibles, ou
    ``pid`` demandé différent)."""
    e = os.environ if env is None else env
    try:
        p, fd = int(e.get(ENV_PID) or ""), int(e.get(ENV_FD) or "")
    except ValueError:
        return None
    if p <= 0 or fd < 0 or (pid is not None and pid != p):
        return None
    return p, fd


def _process_herite(pid: int | None, write: bool, env=None):
    """Process sur le descripteur hérité du lanceur (une COPIE : chaque ouverture a la sienne), ou None."""
    h = fd_herite(pid, env) if procmem.SYSTEME == "linux" else None
    if h is None:
        return None
    try:
        copie = os.dup(h[1])
    except OSError:                             # descripteur pas (ou plus) là
        return None
    try:
        return procmem.Process(h[0], write=write, fd=copie)     # la copie est à lui : fermée par lui, même sur échec
    except (OSError, TypeError, ValueError):    # jeu fermé depuis (ProcessLookupError)…
        return None


def natif_tourne() -> bool:
    try:
        return bool(find_natif_pids())
    except Exception:                           # noqa: BLE001
        return False


class MemoireNatif(GuestMemory):
    """Mémoire du jeu natif : base fixe ; la boîte aux lettres se cherche dans la zone des mods."""
    hote = "natif"
    zone_boite = ZONE_MODS


def lire_zone(mem, boite: int) -> dict | None:
    """Zone WWMPNATF qui suit la boîte (dict), ou None si elle n'y est pas (ou pas au bon format)."""
    raw = mem.try_read(boite + NATIF_OFF, NATIF_TAILLE)
    if raw is None or raw[:8] != MAGIE_NATIF:
        return None
    fmt, adr, socle, pf, pm, pas, vmod, req, filt, confort, code, charg = struct.unpack_from(">IIIiiIIIIIII", raw, 8)
    if fmt != NATIF_FORMAT:
        return None
    return {"boite": adr, "socle": socle, "profil_fantome": pf, "profil_modele": pm, "pas": pas,
            "version_mod": vmod, "requetes": req, "filtrees": filt, "confort": confort, "rando_code": code,
            "chargements": charg if vmod >= VERSION_CHARGEMENTS else None}


# mod 0.23.0 (chargements.c) : mot +0x34 de la zone = appels de dSv_info_c::card_to_memory (état de sauvegarde du portage
# chargé en pleine partie, Journal chargé, « Continuer » après un Game Over) ; avant : 0 (réservé)
OFFN_CHARGEMENTS = 0x34
VERSION_CHARGEMENTS = 23


def chargements(mem) -> int | None:
    """Compteur des parties remplacées (mod natif 0.23.0 ou plus), ou None (Cemu, mod plus ancien, boîte absente).
    Synchro/compteurs.py, inventaire.py et cadeaux.py le mettent dans leur contexte : un état de sauvegarde du portage
    chargé en pleine partie garde gameInfo et mDateIPL, seul ce compteur le trahit. La boîte trouvée est gardée sur la
    mémoire (``_wwmp_boite``) et revalidée à chaque lecture (une lecture de 0x40 octets)."""
    zone = getattr(mem, "zone_boite", None)
    if not isinstance(zone, tuple):
        return None
    b = getattr(mem, "_wwmp_boite", None)
    z = lire_zone(mem, b) if b else None
    if z is None or z["boite"] != b:
        b = trouver_boite(mem, *zone)
        try:
            mem._wwmp_boite = b
        except AttributeError:
            pass
        z = lire_zone(mem, b) if b else None
    return None if z is None else z["chargements"]


def boite_valide(mem, h: int) -> bool:
    if h % 16:
        return False
    raw = mem.try_read(h, 12)
    if raw is None or raw[:8] != MAGIE_BOITE or struct.unpack_from(">I", raw, 8)[0] < VERSION_MIN:
        return False
    z = lire_zone(mem, h)
    return z is not None and z["boite"] == h


def trouver_boite(mem, lo: int = ZONE_MODS[0], hi: int = ZONE_MODS[1]) -> int | None:
    """Adresse invitée de la boîte aux lettres du mod natif, ou None (mod absent, jeu pas encore démarré)."""
    for h in mem.find_bytes(MAGIE_BOITE, lo, hi, limit=32):
        if boite_valide(mem, h):
            return h
    return None


def plage_zones(mem) -> tuple[int, int] | None:
    """Portage natif : plage où chercher par leur magie les zones d'autres packs Cemu que le mod porte (WWMPPAU1 de
    WWMP_Pause…) : les 0x8000 octets de la boîte du mod, comme les zones ajoutées en fin de pack ; None sans boîte.
    Mémoire Cemu (sans ``zone_boite``) : None aussi (chaque module garde sa plage du codecave)."""
    zone = getattr(mem, "zone_boite", None)
    if not isinstance(zone, tuple):
        return None
    b = trouver_boite(mem, *zone)
    return None if b is None else (b, b + 0x8000)


def diagnostic(mem, boite: int) -> str:
    """Une ligne : ce que le mod natif sait faire, et ce qui coince (profils refusés…)."""
    z = lire_zone(mem, boite)
    if z is None:
        return "zone WWMPNATF absente : ce n'est pas la boîte du mod natif"
    parts = [f"mod natif v{z['version_mod']}", "socle : " + (", ".join(n for b, n in SOCLE.items() if z["socle"] & b)
                                                            or "rien")]
    for nom, cle, proc in (("WwmpLink", "profil_fantome", PROC_FANTOME), ("WwmpModl", "profil_modele", PROC_MODELE)):
        v = z[cle]
        if v != proc:
            parts.append(f"profil {nom} " + ("pas encore enregistré" if v == 0 else
                                             f"refusé ({v}) : {REFUS_PROFIL.get(v, 'erreur inconnue')}"))
    if z.get("rando_code"):
        from rando import natif_code as nc
        parts.append("code du randomizer : " + ", ".join(n for n in nc.FRAGMENTS if z["rando_code"] & nc.BIT[n]))
    carte = lire_carte(mem, boite)
    if carte is not None:
        parts.append("carte : " + ("coupée" if carte["coupe"] else
                                   f"{carte['marques']} marqueur(s), écran {ECRANS_CARTE.get(carte['ecran'], '?')}, "
                                   f"écart de la flèche de Link {carte['ecart_link']:.1f}"
                                   + (", bonnet absent (anneau)" if carte["bonnet"] == 0xFFFFFFFF else "")
                                   + (", flèche absente (flèche rouge du jeu)" if carte["fleche_png"] == 0xFFFFFFFF
                                      else "")
                                   + (f", {carte['fleches_soi']} flèche(s) de notre icône (flèches du jeu cachées : "
                                      f"{carte['fleches_cachees']:#x})" if carte["fleches_soi"] else "")
                                   + (f", {carte['refus']} hors de l'écran" if carte["refus"] else "")
                                   + (f", donjon : {carte['vues_donjon']} passage(s), {carte['refus_etage']} à un "
                                      f"autre étage, {carte['refus_cadre']} hors du cadre, écart de l'icône de Link "
                                      f"{carte['ecart_donjon']:.1f}" if carte["vues_donjon"] else "")))
    sig = lire_reperes(mem, boite)
    if sig is not None:
        parts.append(f"noms et signaux : cfg {sig['cfg']}, {sig['noms_vus']} nom(s) et {sig['signaux_vus']} "
                     f"signal(aux) dessinés, {sig['demandes']} double(s) clic(s) du stick gauche")
    parts.append(f"pas de Link {z['pas']}")
    return " | ".join(parts)


# les autres joueurs sur la carte du jeu (mod 0.19.0, carte du donjon 0.27.0, bit 30 ; natif/mod_wwmp/carte.c, zone WWMPCAR1)
CARTE_OFF, MAGIE_CARTE = 0x6000, b"WWMPCAR1"
ECRANS_CARTE = {0: "aucun", 1: "télé", 2: "GamePad"}


def lire_carte(mem, boite: int) -> dict | None:
    """Compteurs de la zone WWMPCAR1 (mod 0.19.0 ou plus), ou None."""
    raw = mem.try_read(boite + CARTE_OFF, 0x80)
    if raw is None or raw[:8] != MAGIE_CARTE or struct.unpack_from(">I", raw, 8)[0] != 1:
        return None
    coupe, = struct.unpack_from(">I", raw, 0x0C)
    dessins, listes, ecran, vues, vues_carte = struct.unpack_from(">5I", raw, 0x2C)
    ecart, = struct.unpack_from(">f", raw, 0x40)
    marques, visage, bonnet, refus = struct.unpack_from(">4I", raw, 0x4C)
    # 0.21.0 : poignées du visage et du bonnet (0 : pas encore chargées, 0xFFFFFFFF : absents), marqueurs refusés
    # 0.27.0 : carte du donjon (passages, fantômes à un autre étage, hors du cadre, écart de l'icône de Link) ; zéro
    # avant (le bourrage qui suit la zone)
    vues_donjon, refus_etage, refus_cadre = struct.unpack_from(">3I", raw, 0x60)
    ecart_donjon, = struct.unpack_from(">f", raw, 0x6C)
    # 0.42.0 : flèche à la couleur du joueur (poignées de carte_fleche.png et de la flèche rouge du jeu, flèches du jeu
    # de notre icône cachées : bit 0 ballon, 1 carte marine, 2 donjon ; flèches de notre icône dessinées)
    fleche_png, fleche_rouge, fleches_cachees, fleches_soi = struct.unpack_from(">4I", raw, 0x70)
    return {"fleche_png": fleche_png, "fleche_rouge": fleche_rouge, "fleches_cachees": fleches_cachees,
            "fleches_soi": fleches_soi,"coupe": coupe, "dessins": dessins, "listes": listes, "ecran": ecran, "vues": vues,
            "vues_carte": vues_carte, "ecart_link": ecart, "marques": marques, "visage": visage, "bonnet": bonnet,
            "refus": refus, "vues_donjon": vues_donjon, "refus_etage": refus_etage, "refus_cadre": refus_cadre,
            "ecart_donjon": ecart_donjon}


# noms au-dessus des têtes et signal « viens ici » (mod 0.33.0, sans bit ; natif/mod_wwmp/reperes.c, zone WWMPSIG1)
SIG_OFF, MAGIE_SIG = 0x6900, b"WWMPSIG1"


def lire_reperes(mem, boite: int) -> dict | None:
    """Compteurs de la zone WWMPSIG1 (mod 0.33.0 ou plus), ou None."""
    raw = mem.try_read(boite + SIG_OFF, 0x40)
    if raw is None or raw[:8] != MAGIE_SIG or struct.unpack_from(">I", raw, 8)[0] != 1:
        return None
    cfg, demandes = struct.unpack_from(">2I", raw, 0x0C)
    noms_vus, signaux_vus = struct.unpack_from(">2H", raw, 0x3C)
    return {"cfg": cfg, "demandes": demandes, "noms_vus": noms_vus, "signaux_vus": signaux_vus}


def open_natif(pid: int | None = None, write: bool = False, verbose: bool = True,
               base: int | None = None) -> MemoireNatif:
    """Mémoire du jeu natif (processus du portage), à sa base fixe ; vérifie que c'est bien Wind Waker HD USA v0."""
    from jeu import game
    b = base if base is not None else base_hote()
    herite = _process_herite(pid, write)
    if herite is not None:                      # Linux : mémoire ouverte par le lanceur (ancêtre du jeu)
        mem = MemoireNatif(herite, b)
        if game.version_check(mem)[0]:
            if verbose:
                print(f"portage natif : mémoire du jeu à 0x{b:X} (base fixe, accès donné par le lanceur)")
            return mem
        herite.close()
    if pid is None:
        pids = find_natif_pids()
        if not pids:
            raise RuntimeError(f"{NOMS_NATIF[0]} introuvable : lance d'abord Wind Waker HD (le portage natif).")
        if len(pids) > 1 and verbose:
            print(f"[!] plusieurs {NOMS_NATIF[0]} ({pids}), j'utilise {pids[0]} (option --pid pour choisir)")
        pid = pids[0]
    proc = procmem.Process(pid, write=write)
    mem = MemoireNatif(proc, b)
    ok, mot = game.version_check(mem)
    if not ok:
        raise RuntimeError(f"mémoire du jeu illisible ou inattendue à 0x{b:X} (code 0x{mot or 0:08X}) : est-ce bien "
                           "le portage natif ZeldaWWHDRecomp avec Wind Waker HD USA v0 ?")
    if verbose:
        print(f"portage natif : mémoire du jeu à 0x{b:X} (base fixe)")
    return mem
