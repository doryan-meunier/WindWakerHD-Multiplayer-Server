"""Accès mémoire d'un autre processus sous Linux (/proc, aucune dépendance) : même interface que winmem.

    Process(pid, write=False) : read, write, try_read, query, regions, close, ``with``
    list_processes(), find_pids(nom), MemoryReadError, Region, MEM_COMMIT / MEM_RESERVE / MEM_FREE

Carte mémoire : ``/proc/<pid>/maps`` ; lecture / écriture : ``/proc/<pid>/mem`` (le décalage dans le fichier EST
l'adresse). Par défaut : lecture seule ; l'écriture n'est possible qu'avec ``Process(pid, write=True)`` (boîte aux
lettres), comme sous Windows.

Correspondance avec la carte Windows (VirtualQueryEx) utilisée par ``wwlib.cemu`` :
- une ligne de maps avec des droits (``r``) = région « committed » (MEM_COMMIT) ;
- une ligne ``---p`` (réservée, PROT_NONE) = MEM_RESERVE ; un trou entre deux lignes = MEM_FREE ;
- Linux n'a pas d'« AllocationBase » : les lignes ANONYMES contiguës (sans fichier, ``[anon:…]``, ``/memfd:…``)
  forment une même « allocation » dont la base est le début. Cemu réserve d'un bloc les 4 Gio de la RAM émulée
  (mmap anonyme PROT_NONE) puis rend lisibles les morceaux utilisés : on retrouve donc une allocation de 4 Gio.
  Comme une autre réservation anonyme pourrait être collée juste avant, ``alloc_approx = True`` demande à
  ``wwlib.cemu`` d'essayer aussi la base « fin - 4 Gio ».

Permissions (jamais contournées par le code, jamais de sudo) : lire /proc/<pid>/mem demande le droit « ptrace »
sur Cemu : même utilisateur, et ``/proc/sys/kernel/yama/ptrace_scope`` à 0 (à 1, seul un processus PARENT de
Cemu peut le lire). ``diagnostiquer(pid)`` explique ce qui bloque et comment le régler soi-même.

Mode léger (``LEGER_DEFAUT``, réglage --linux-leger du client, actif par défaut) : process_vm_readv / writev
(un appel système par lot, lectures et écritures regroupées), repli pread / pwrite sur le fichier mem gardé
ouvert ; ``leger=False`` : l'accès d'avant (seek + read). ``mesure()`` : compteurs pour le diagnostic.

``racine`` (défaut ``/proc``), ``mem`` (objet fichier) et ``vm`` (AccesVm) sont injectables : les tests utilisent un
faux /proc.
"""
from __future__ import annotations

import bisect
import os
import sys
import threading
from dataclasses import dataclass
from typing import Iterator

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_FREE = 0x10000
PAGE_NOACCESS = 0x01
PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_GUARD = 0x100
MEM_PRIVATE = 0x20000
MEM_MAPPED = 0x40000

USER_SPACE_END = 0x7FFF_FFFF_FFFF
PTRACE_SCOPE = "/proc/sys/kernel/yama/ptrace_scope"
MAPS_DUREE = 0.5          # secondes pendant lesquelles une lecture de maps est réutilisée (query, lectures)
MAPS_RELIRE = 0.1         # lecture hors carte : maps relue si elle a plus de 0,1 s (Cemu a pu rendre la page utilisable)


from jeu.memerreur import MemoryReadError  # noqa: E402,F401  (commune à winmem et linmem)


@dataclass(frozen=True)
class Region:
    base: int
    size: int
    alloc_base: int
    state: int
    protect: int
    type: int
    perms: str = ""
    path: str = ""

    @property
    def end(self) -> int:
        return self.base + self.size

    @property
    def readable(self) -> bool:
        return (self.state == MEM_COMMIT
                and not (self.protect & PAGE_GUARD)
                and not (self.protect & PAGE_NOACCESS)
                and self.protect != 0)


@dataclass(frozen=True)
class Ligne:
    debut: int
    fin: int
    perms: str
    chemin: str


def lire_maps(texte: str) -> list[Ligne]:
    """Lignes de /proc/<pid>/maps (``debut-fin perms offset dev inode [chemin]``), triées ; lignes illisibles
    ignorées."""
    out = []
    for ligne in texte.splitlines():
        champs = ligne.split(None, 5)
        if len(champs) < 5:
            continue
        a, _, b = champs[0].partition("-")
        try:
            debut, fin = int(a, 16), int(b, 16)
        except ValueError:
            continue
        if fin <= debut:
            continue
        out.append(Ligne(debut, fin, champs[1], champs[5].strip() if len(champs) > 5 else ""))
    out.sort(key=lambda l: l.debut)
    return out


def anonyme(chemin: str) -> bool:
    """Mémoire sans fichier : ce que Cemu réserve pour la RAM émulée."""
    return (chemin == "" or chemin.startswith("[anon") or chemin.startswith("/memfd:")
            or chemin.startswith("/dev/zero") or chemin.startswith("/SYSV"))


def protection(perms: str) -> int:
    r, w, x = (len(perms) > i and perms[i] != "-" for i in range(3))
    if not r:
        return PAGE_NOACCESS
    if x:
        return PAGE_EXECUTE_READWRITE if w else PAGE_EXECUTE_READ
    return PAGE_READWRITE if w else PAGE_READONLY


def regions_de(lignes: list[Ligne], start: int = 0, end: int = USER_SPACE_END) -> list[Region]:
    """Régions « à la Windows » (trous compris) couvrant [start, end)."""
    out: list[Region] = []
    alloc = None
    prec = None
    bases: list[int] = []
    for l in lignes:                       # base d'« allocation » : début de la suite de lignes anonymes contiguës
        if anonyme(l.chemin) and prec is not None and anonyme(prec.chemin) and prec.fin == l.debut:
            pass
        else:
            alloc = l.debut
        bases.append(alloc)
        prec = l
    addr = start
    for l, ab in zip(lignes, bases):
        if l.fin <= addr:
            continue
        if l.debut >= end:
            break
        if l.debut > addr:
            out.append(Region(addr, l.debut - addr, 0, MEM_FREE, PAGE_NOACCESS, 0))
            addr = l.debut
        prot = protection(l.perms)
        etat = MEM_COMMIT if prot != PAGE_NOACCESS else MEM_RESERVE
        typ = MEM_PRIVATE if anonyme(l.chemin) else MEM_MAPPED
        out.append(Region(addr, l.fin - addr, ab, etat, prot if etat == MEM_COMMIT else 0, typ, l.perms, l.chemin))
        addr = l.fin
        if addr >= end:
            break
    if addr < end:
        out.append(Region(addr, end - addr, 0, MEM_FREE, PAGE_NOACCESS, 0))
    return out


# ---------------------------------------------------------------------------
# Processus
# ---------------------------------------------------------------------------
def _lire(chemin: str, n: int = 1 << 16) -> bytes | None:
    try:
        with open(chemin, "rb") as f:
            return f.read(n)
    except OSError:
        return None


def nom_processus(pid: int, racine: str = "/proc") -> str:
    """``comm`` (15 caractères au plus), sinon le nom du premier argument."""
    c = _lire(os.path.join(racine, str(pid), "comm"))
    if c:
        return c.decode("utf-8", "replace").strip()
    a = arguments(pid, racine)
    return os.path.basename(a[0]) if a else ""


def arguments(pid: int, racine: str = "/proc") -> list[str]:
    c = _lire(os.path.join(racine, str(pid), "cmdline"), 1 << 20) or b""
    return [x.decode("utf-8", "replace") for x in c.split(b"\0") if x]


def list_processes(racine: str = "/proc") -> list[tuple[int, str]]:
    out = []
    try:
        noms = os.listdir(racine)
    except OSError:
        return out
    for n in noms:
        if not n.isdigit():
            continue
        nom = nom_processus(int(n), racine)
        if nom:
            out.append((int(n), nom))
    out.sort()
    return out


def _sans_exe(nom: str) -> str:
    nom = nom.strip().lower()
    return nom[:-4] if nom.endswith(".exe") else nom


def est_zombie(pid: int, racine: str = "/proc") -> bool:
    """État « Z » (zombie) ou « X » (mort) dans /proc/<pid>/stat : le processus est fini."""
    s = _lire(os.path.join(racine, str(pid), "stat"), 4096)
    if not s:
        return False
    i = s.rfind(b")")                  # le nom (entre parenthèses) peut contenir des espaces
    etat = s[i + 2:i + 3] if i >= 0 else b""
    return etat in (b"Z", b"X")


def find_pids(exe_name: str, racine: str = "/proc") -> list[int]:
    """PID dont le nom (``comm``), le premier argument ou le lien ``exe`` vaut ``exe_name`` (sans « .exe »,
    casse ignorée : « Cemu.exe » trouve aussi « Cemu » de l'AppImage ou du Flatpak)."""
    voulu = _sans_exe(exe_name)
    out = []
    for pid, nom in list_processes(racine):
        if est_zombie(pid, racine):
            continue                    # processus terminé, pas encore récupéré par son parent : Cemu est fermé
        noms = {_sans_exe(nom)}
        a = arguments(pid, racine)
        if a:
            noms.add(_sans_exe(os.path.basename(a[0])))
        try:
            noms.add(_sans_exe(os.path.basename(os.readlink(os.path.join(racine, str(pid), "exe")))))
        except OSError:
            pass
        if voulu in noms:
            out.append(pid)
    return out


# ---------------------------------------------------------------------------
# Diagnostic des permissions (lecture seule, rien n'est changé)
# ---------------------------------------------------------------------------
def ptrace_scope(chemin: str = PTRACE_SCOPE) -> int | None:
    """Valeur de kernel.yama.ptrace_scope (None : Yama absent, pas de restriction de ce côté)."""
    c = _lire(chemin, 16)
    try:
        return int(c.decode().strip()) if c else None
    except ValueError:
        return None


def est_flatpak(pid: int, racine: str = "/proc") -> bool:
    """Le processus tourne dans un Flatpak (bac à sable) ? D'après son cgroup ou son chemin (/app/…)."""
    cg = (_lire(os.path.join(racine, str(pid), "cgroup")) or b"").decode("utf-8", "replace")
    if "flatpak" in cg:
        return True
    a = arguments(pid, racine)
    return bool(a) and (a[0].startswith("/app/") or "info.cemu.Cemu" in " ".join(a))


def proprietaire(pid: int, racine: str = "/proc") -> int | None:
    try:
        return os.stat(os.path.join(racine, str(pid))).st_uid
    except OSError:
        return None


def mon_uid() -> int | None:
    return os.getuid() if hasattr(os, "getuid") else None


def diagnostiquer(pid: int, racine: str = "/proc", scope_chemin: str = PTRACE_SCOPE,
                  uid: int | None = None, dans_flatpak: bool | None = None) -> list[str]:
    """Raisons possibles d'un refus d'accès à la mémoire de ``pid``, avec ce que l'utilisateur peut faire
    LUI-MÊME. Liste vide : rien de suspect trouvé."""
    out = []
    uid = mon_uid() if uid is None else uid
    if dans_flatpak is None:
        dans_flatpak = os.path.exists("/.flatpak-info")
    proprio = proprietaire(pid, racine)
    if uid is not None and proprio is not None and proprio != uid:
        out.append(f"Cemu (pid {pid}) appartient à un autre utilisateur (uid {proprio}, toi {uid}) : lance Cemu et "
                   "le lanceur avec le MÊME compte (sans sudo).")
    if uid == 0:
        out.append("Tu lances le mod en root : inutile et déconseillé ; lance-le avec ton compte habituel.")
    if est_flatpak(pid, racine):
        out.append("Cemu tourne en Flatpak (info.cemu.Cemu) : son bac à sable bloque souvent la lecture de sa "
                   "mémoire depuis l'extérieur. Solution conseillée : utiliser l'AppImage officielle de Cemu "
                   "(docs/joueurs/JOUER_SOUS_LINUX.md).")
    if dans_flatpak:
        out.append("Le mod lui-même tourne dans un bac à sable Flatpak : il ne voit pas les processus du système. "
                   "Lance-le hors du Flatpak (python3 depuis un terminal).")
    s = ptrace_scope(scope_chemin)
    if s == 1:
        out.append("kernel.yama.ptrace_scope vaut 1 : seul un processus parent de Cemu peut lire sa mémoire. "
                   "Pour cette session (jusqu'au redémarrage), tape TOI-MÊME : "
                   "sudo sysctl -w kernel.yama.ptrace_scope=0   (docs/joueurs/JOUER_SOUS_LINUX.md explique le réglage "
                   "et comment le remettre : sudo sysctl -w kernel.yama.ptrace_scope=1).")
    elif s == 2:
        out.append("kernel.yama.ptrace_scope vaut 2 : seul root (CAP_SYS_PTRACE) peut lire la mémoire d'un autre "
                   "processus. Remets-le à 0 toi-même (sudo sysctl -w kernel.yama.ptrace_scope=0) si tu l'acceptes ; "
                   "voir docs/joueurs/JOUER_SOUS_LINUX.md.")
    elif s == 3:
        out.append("kernel.yama.ptrace_scope vaut 3 : la lecture de la mémoire d'un autre processus est interdite "
                   "jusqu'au redémarrage (réglage de sécurité de ce système). Change la configuration du système "
                   "(/etc/sysctl.d) puis redémarre, ou joue depuis un autre PC.")
    return out


def message_refus(pid: int, erreur: Exception | None = None, racine: str = "/proc",
                  scope_chemin: str = PTRACE_SCOPE) -> str:
    raisons = diagnostiquer(pid, racine, scope_chemin)
    tete = f"Accès à la mémoire de Cemu (pid {pid}) refusé"
    if erreur is not None:
        tete += f" ({erreur.__class__.__name__}: {getattr(erreur, 'strerror', None) or erreur})"
    if not raisons:
        raisons = ["Vérifie que Cemu et le mod tournent avec le même compte, et kernel.yama.ptrace_scope "
                   "(cat /proc/sys/kernel/yama/ptrace_scope ; voir docs/joueurs/JOUER_SOUS_LINUX.md)."]
    return tete + ". " + " ".join(raisons)


# ---------------------------------------------------------------------------
# Accès « léger » (réglage --linux-leger, actif par défaut) : process_vm_readv / process_vm_writev
# ---------------------------------------------------------------------------
# Vu en jeu le 2026-11-13 (premier joueur sous Linux) : « ses commandes partent en retard » dans son propre jeu.
# Chaque accès à /proc/<pid>/mem coûte deux appels système (lseek + read/write) et le noyau y copie la mémoire de
# Cemu PAGE PAR PAGE en reprenant à chaque page le verrou de sa carte mémoire (mmap lock) ; le client en fait des
# milliers par seconde (compteur d'images du lissage toutes les 2 ms, acteurs relus à chaque tick, rescan de ~96 Mo).
# Mode léger : un seul appel système par lot (process_vm_readv / writev : plusieurs morceaux d'un coup, copie
# directe par paquets de pages), écritures d'une image regroupées (``lot()``), carte relue moins souvent.
# Repli automatique : pread / pwrite sur /proc/<pid>/mem gardé ouvert (un seul fd), puis l'accès d'avant.
LEGER_DEFAUT = True        # réglage du client : --no-linux-leger le coupe (comportement d'avant, seek + read)
MAPS_DUREE_LEGER = 2.0     # s : carte réutilisée plus longtemps en mode léger (régions() la relit toujours)
IOV_MAX = 1024             # morceaux par appel (limite du noyau)
# numéros d'erreur de Linux (pas ceux du module errno : les tests tournent aussi sous Windows)
EPERM, ESRCH, EACCES, EFAULT, EINVAL, ENOSYS = 1, 3, 13, 14, 22, 38
ERRNO_INDISPO = (EPERM, EACCES, EINVAL, ENOSYS)   # l'appel n'est pas utilisable ici -> repli sur /proc/<pid>/mem


class VmIndisponible(OSError):
    """process_vm_readv / writev refusé par le système (noyau, bac à sable) : on se replie sur /proc/<pid>/mem."""


class AccesVm:
    """process_vm_readv / process_vm_writev par ctypes (libc). ``readv`` / ``writev`` injectables (tests : fonctions
    Python qui reçoivent les mêmes arguments que la libc, tableaux d'iovec compris).

    Comme ReadProcessMemory : une page réservée (PROT_NONE) ou absente donne EFAULT, et l'écriture respecte les
    protections (pas de FOLL_FORCE, contrairement à /proc/<pid>/mem) : la carte n'a pas à être consultée."""

    def __init__(self, pid: int, readv=None, writev=None):
        import ctypes
        self.ctypes = ctypes
        self.pid = pid

        class Iovec(ctypes.Structure):
            _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]
        self.Iovec = Iovec
        if readv is None or writev is None:
            if not sys.platform.startswith("linux"):
                raise VmIndisponible(38, "process_vm_readv : Linux seulement")
            import ctypes.util
            try:
                libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
                readv, writev = libc.process_vm_readv, libc.process_vm_writev
            except (OSError, AttributeError) as e:
                raise VmIndisponible(38, f"process_vm_readv introuvable : {e}") from None
            for f in (readv, writev):
                f.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                              ctypes.c_ulong]
                f.restype = ctypes.c_ssize_t
        self._readv, self._writev = readv, writev
        self.appels = 0

    def _tableau(self, paires):
        t = (self.Iovec * len(paires))()
        for i, (a, n) in enumerate(paires):
            t[i].iov_base, t[i].iov_len = a, n
        return t

    def _appel(self, f, locaux, distants) -> int:
        ct = self.ctypes
        ct.set_errno(0)
        n = f(self.pid, ct.byref(locaux), len(locaux), ct.byref(distants), len(distants), 0)
        self.appels += 1
        if n is None or n < 0:
            e = ct.get_errno()
            if e in ERRNO_INDISPO:
                raise VmIndisponible(e, f"process_vm_readv / writev refusé (errno {e})")
            if e == ESRCH:                             # Cemu fermé
                raise MemoryReadError("processus fermé (ESRCH)")
            return -1                                  # EFAULT (page illisible) ou autre : le premier morceau rate
        return int(n)

    def lire(self, morceaux: list[tuple[int, int]]) -> list[bytes | None]:
        """Lit chaque (adresse, taille) ; None pour un morceau illisible. Un appel par paquet de IOV_MAX morceaux
        (plus un par morceau raté : le noyau s'arrête au premier)."""
        ct = self.ctypes
        res: list[bytes | None] = [None] * len(morceaux)
        a_faire = [i for i, (_a, n) in enumerate(morceaux) if n > 0]
        for i, (_a, n) in enumerate(morceaux):
            if n == 0:
                res[i] = b""
        k = 0
        while k < len(a_faire):
            idx = a_faire[k:k + IOV_MAX]
            bufs = [ct.create_string_buffer(morceaux[i][1]) for i in idx]
            loc = self._tableau([(ct.addressof(b), morceaux[i][1]) for b, i in zip(bufs, idx)])
            dist = self._tableau([morceaux[i] for i in idx])
            n = self._appel(self._readv, loc, dist)
            fait = 0
            if n > 0:
                for b, i in zip(bufs, idx):
                    t = morceaux[i][1]
                    if n < t:
                        break
                    res[i] = b.raw[:t]
                    n -= t
                    fait += 1
            k += fait if fait == len(idx) else fait + 1     # morceau raté (ou lu en partie) : None, on reprend après
        return res

    def ecrire(self, morceaux: list[tuple[int, bytes]]) -> list[bool]:
        """Écrit chaque (adresse, octets) dans l'ordre ; False pour un morceau non écrit."""
        ct = self.ctypes
        res = [len(d) == 0 for _a, d in morceaux]
        a_faire = [i for i, (_a, d) in enumerate(morceaux) if d]
        k = 0
        while k < len(a_faire):
            idx = a_faire[k:k + IOV_MAX]
            bufs = [ct.create_string_buffer(bytes(morceaux[i][1]), len(morceaux[i][1])) for i in idx]
            loc = self._tableau([(ct.addressof(b), len(morceaux[i][1])) for b, i in zip(bufs, idx)])
            dist = self._tableau([(morceaux[i][0], len(morceaux[i][1])) for i in idx])
            n = self._appel(self._writev, loc, dist)
            fait = 0
            if n > 0:
                for i in idx:
                    t = len(morceaux[i][1])
                    if n < t:
                        break
                    res[i] = True
                    n -= t
                    fait += 1
            k += fait if fait == len(idx) else fait + 1
        return res


_REPLI = object()      # process_vm_readv indisponible : repli sur /proc/<pid>/mem


class _Lot(threading.local):
    def __init__(self):
        self.profondeur = 0
        self.ecritures: list[tuple[int, bytes]] = []


class Process:
    """Poignée sur un processus : lecture seule par défaut, écriture seulement si write=True.

    ``leger`` (None : ``LEGER_DEFAUT``) : process_vm_readv / writev (``vm`` injectable), sinon pread / pwrite sur le
    fichier mem gardé ouvert, lectures groupées (``read_lot``), écritures regroupées (``lot()``). ``leger=False`` :
    exactement l'accès d'avant (seek + read sur /proc/<pid>/mem). ``mesure()`` : compteurs pour le diagnostic."""

    alloc_approx = True        # bases d'allocation reconstituées (voir l'en-tête) : wwlib.cemu essaie aussi fin - 4 Gio

    def __init__(self, pid: int, write: bool = False, racine: str = "/proc", mem=None,
                 scope_chemin: str = PTRACE_SCOPE, horloge=None, leger: bool | None = None, vm=None):
        import time
        self.pid = pid
        self.writable = write
        self.racine = racine
        self.scope_chemin = scope_chemin
        self.horloge = horloge or time.monotonic
        self._verrou = threading.Lock()
        self._maps: tuple[float, list[Ligne], list[int]] | None = None
        self._propre = mem is None
        self.leger = LEGER_DEFAUT if leger is None else bool(leger)
        self._lot = _Lot()
        self._stats = {"lectures": 0, "ecritures": 0, "appels": 0, "octets": 0, "temps": 0.0, "maps": 0,
                       "lots": 0}
        if not os.path.isdir(os.path.join(racine, str(pid))):
            raise ProcessLookupError(f"processus {pid} introuvable (Cemu fermé ?)")
        if mem is None:
            try:
                mem = open(os.path.join(racine, str(pid), "mem"), "r+b" if write else "rb", buffering=0)
            except PermissionError as e:
                raise PermissionError(message_refus(pid, e, racine, scope_chemin)) from None
            except OSError as e:
                raise PermissionError(message_refus(pid, e, racine, scope_chemin)) from None
        self.mem = mem
        self._vm = None
        if self.leger:
            if vm is not None:
                self._vm = vm
            elif self._propre:
                try:
                    self._vm = AccesVm(pid)
                except (VmIndisponible, OSError):
                    self._vm = None
        try:
            self._lignes(True)
        except PermissionError as e:
            self.close()
            raise PermissionError(message_refus(pid, e, racine, scope_chemin)) from None

    @property
    def mode(self) -> str:
        """« vm » (process_vm_readv), « pread » (fichier mem gardé ouvert, un appel par accès) ou « fichier »
        (seek + read/write : l'accès d'avant)."""
        if self.leger and self._vm is not None:
            return "vm"
        if self.leger and self._fd() is not None:
            return "pread"
        return "fichier"

    def _fd(self) -> int | None:
        if not hasattr(os, "pread") or self.mem is None:
            return None
        try:
            return self.mem.fileno()
        except (AttributeError, OSError, ValueError):
            return None

    def close(self) -> None:
        m, self.mem = getattr(self, "mem", None), None
        if m is not None and self._propre:
            try:
                m.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- diagnostic ---------------------------------------------------------------------------------------------
    def _compter(self, lectures=0, ecritures=0, appels=0, octets=0, temps=0.0, maps=0, lots=0) -> None:
        s = self._stats
        s["lectures"] += lectures
        s["ecritures"] += ecritures
        s["appels"] += appels
        s["octets"] += octets
        s["temps"] += temps
        s["maps"] += maps
        s["lots"] += lots

    def mesure(self) -> dict:
        """Compteurs cumulés : accès (lectures, écritures), appels système estimés (seek + read = 2, pread = 1,
        process_vm_readv = 1 par lot, relecture de maps = 3), octets, temps passé dans ces appels (s)."""
        d = dict(self._stats)
        d["mode"] = self.mode
        return d

    # -- carte mémoire ------------------------------------------------------------------------------------------
    def _lignes(self, frais: bool = False) -> list[Ligne]:
        import time
        t = self.horloge()
        duree = MAPS_DUREE_LEGER if self.leger else MAPS_DUREE
        if not frais and self._maps is not None and t - self._maps[0] < duree:
            return self._maps[1]
        t0 = time.perf_counter()
        with open(os.path.join(self.racine, str(self.pid), "maps"), "r", encoding="utf-8", errors="replace") as f:
            lignes = lire_maps(f.read())
        self._compter(appels=3, maps=1, temps=time.perf_counter() - t0)
        self._maps = (t, lignes, [l.debut for l in lignes])
        return lignes

    def _couvert(self, addr: int, size: int) -> bool:
        """[addr, addr+size) entièrement dans des lignes lisibles de maps ? Comme ReadProcessMemory sous Windows,
        qui échoue sur une page réservée : /proc/<pid>/mem lirait des zéros dans une réservation PROT_NONE (et y
        écrirait), ce qui fausserait la recherche de la base et pourrait abîmer la mémoire que Cemu n'a pas
        encore rendue utilisable. (process_vm_readv échoue de lui-même sur ces pages : pas de vérification.)"""
        for frais in (False, True):
            if frais and self._maps is not None and self.horloge() - self._maps[0] < MAPS_RELIRE:
                return False              # carte toute fraîche : inutile de la relire à chaque lecture ratée
            try:
                lignes = self._lignes(frais)
            except OSError:
                return False
            debuts = self._maps[2]
            i = bisect.bisect_right(debuts, addr) - 1
            a, fin, ok = addr, addr + max(size, 1), True
            while a < fin:
                if i < 0 or i >= len(lignes) or not (lignes[i].debut <= a < lignes[i].fin) \
                        or not lignes[i].perms.startswith("r"):
                    ok = False
                    break
                a = lignes[i].fin
                i += 1
            if ok:
                return True
        return False

    def query(self, addr: int) -> Region | None:
        try:
            lignes = self._lignes()
        except OSError:
            return None
        if not 0 <= addr < USER_SPACE_END:
            return None
        rs = regions_de(lignes, addr, USER_SPACE_END)
        return rs[0] if rs else None

    def regions(self, start: int = 0, end: int = USER_SPACE_END) -> Iterator[Region]:
        try:
            lignes = self._lignes(True)
        except OSError:
            return iter(())
        return iter(regions_de(lignes, start, end))

    # -- lecture / écriture -------------------------------------------------------------------------------------
    def _vm_perdu(self) -> None:
        self._vm = None            # repli définitif sur /proc/<pid>/mem (pread, sinon seek + read)

    def _lire_vm(self, addr: int, size: int):
        """Lecture par process_vm_readv : octets, None (illisible) ou ``_REPLI`` (appel indisponible)."""
        import time
        t0 = time.perf_counter()
        try:
            n0 = self._vm.appels
            data = self._vm.lire([(addr, size)])[0]
        except VmIndisponible:
            self._vm_perdu()
            return _REPLI
        self._compter(lectures=1, appels=self._vm.appels - n0, octets=size if data else 0,
                      temps=time.perf_counter() - t0)
        return data

    def _verifier(self, addr: int, size: int, quoi: str = "lecture") -> None:
        if self.mem is None:
            raise MemoryReadError("processus fermé")
        if addr < 0 or size < 0 or addr + size > USER_SPACE_END + 1:
            raise MemoryReadError(f"{quoi} impossible à 0x{addr:X} ({size} octets)")

    def read(self, addr: int, size: int) -> bytes:
        import time
        self._verifier(addr, size)
        if self._lot.ecritures:
            self._vider_si_chevauche(addr, size)
        if self.leger and self._vm is not None:
            data = self._lire_vm(addr, size)
            if data is not _REPLI:
                if data is None:
                    raise MemoryReadError(f"lecture impossible à 0x{addr:X} ({size} octets) : mémoire non utilisable")
                return data
        if not self._couvert(addr, size):
            raise MemoryReadError(f"lecture impossible à 0x{addr:X} ({size} octets) : mémoire non utilisable")
        fd = self._fd() if self.leger else None
        t0 = time.perf_counter()
        with self._verrou:
            try:
                if fd is not None:
                    data, appels = os.pread(fd, size, addr), 1          # mode léger sans process_vm_readv
                else:
                    self.mem.seek(addr)                                  # accès d'avant (--no-linux-leger)
                    data, appels = self.mem.read(size), 2
            except (OSError, OverflowError, ValueError) as e:
                self._compter(lectures=1, appels=1, temps=time.perf_counter() - t0)
                raise MemoryReadError(f"lecture impossible à 0x{addr:X} ({size} octets) : {e}") from None
        self._compter(lectures=1, appels=appels, octets=size, temps=time.perf_counter() - t0)
        if data is None or len(data) != size:
            raise MemoryReadError(f"lecture impossible à 0x{addr:X} ({size} octets)")
        return bytes(data)

    def try_read(self, addr: int, size: int) -> bytes | None:
        try:
            return self.read(addr, size)
        except MemoryReadError:
            return None

    def read_lot(self, morceaux: list[tuple[int, int]]) -> list[bytes | None]:
        """Plusieurs lectures d'un coup (mode léger : un seul process_vm_readv) ; None pour un morceau illisible."""
        import time
        if self.mem is None:
            raise MemoryReadError("processus fermé")
        if not morceaux:
            return []
        if self._lot.ecritures:
            self._vider()
        ok = [a >= 0 and n >= 0 and a + n <= USER_SPACE_END + 1 for a, n in morceaux]
        if self.leger and self._vm is not None:
            t0 = time.perf_counter()
            try:
                n0 = self._vm.appels
                valides = [m for m, b in zip(morceaux, ok) if b]
                lus = iter(self._vm.lire(valides))
                res = [next(lus) if b else None for b in ok]
                self._compter(lectures=len(morceaux), appels=self._vm.appels - n0,
                              octets=sum(len(r) for r in res if r), temps=time.perf_counter() - t0, lots=1)
                return res
            except VmIndisponible:
                self._vm_perdu()
        return [self.try_read(a, n) if b else None for (a, n), b in zip(morceaux, ok)]

    def write(self, addr: int, data: bytes) -> None:
        if not self.writable:
            raise PermissionError("processus ouvert en lecture seule")
        self._verifier(addr, len(data), "écriture")
        if self.leger and self._lot.profondeur > 0:
            if self._vm is None and not self._couvert(addr, len(data)):
                raise MemoryReadError(f"écriture impossible à 0x{addr:X} ({len(data)} octets) : "
                                      "mémoire non utilisable")
            self._lot.ecritures.append((addr, bytes(data)))     # écrite à la fin du lot (une image : un appel)
            return
        if not self._ecrire_un(addr, bytes(data)):
            raise MemoryReadError(f"écriture impossible à 0x{addr:X} ({len(data)} octets)")

    def _ecrire_un(self, addr: int, data: bytes) -> bool:
        import time
        if self.leger and self._vm is not None:
            t0 = time.perf_counter()
            try:
                n0 = self._vm.appels
                ok = self._vm.ecrire([(addr, data)])[0]
                self._compter(ecritures=1, appels=self._vm.appels - n0, octets=len(data) if ok else 0,
                              temps=time.perf_counter() - t0)
                return ok
            except VmIndisponible:
                self._vm_perdu()
        if not self._couvert(addr, len(data)):
            raise MemoryReadError(f"écriture impossible à 0x{addr:X} ({len(data)} octets) : mémoire non utilisable")
        fd = self._fd() if self.leger else None
        t0 = time.perf_counter()
        with self._verrou:
            try:
                if fd is not None:
                    n = os.pwrite(fd, data, addr)
                    appels = 1
                else:
                    self.mem.seek(addr)
                    n = self.mem.write(data)
                    flush = getattr(self.mem, "flush", None)
                    if flush is not None:
                        flush()
                    appels = 2
            except (OSError, OverflowError, ValueError) as e:
                self._compter(ecritures=1, appels=1, temps=time.perf_counter() - t0)
                raise MemoryReadError(f"écriture impossible à 0x{addr:X} ({len(data)} octets) : {e}") from None
        self._compter(ecritures=1, appels=appels, octets=len(data), temps=time.perf_counter() - t0)
        return n is None or n == len(data)

    # -- écritures regroupées -----------------------------------------------------------------------------------
    def lot(self):
        """``with proc.lot():`` les écritures de ce fil sont gardées puis faites d'un coup à la sortie (mode léger :
        un seul process_vm_writev, dans l'ordre). Une lecture qui chevauche une écriture en attente la fait
        d'abord. Hors mode léger : sans effet (écritures immédiates, comme avant)."""
        import contextlib

        @contextlib.contextmanager
        def _cm():
            if not self.leger:
                yield self
                return
            self._lot.profondeur += 1
            try:
                yield self
            finally:
                self._lot.profondeur -= 1
                if self._lot.profondeur == 0:
                    self._vider()
        return _cm()

    def _vider_si_chevauche(self, addr: int, size: int) -> None:
        fin = addr + max(size, 1)
        if any(a < fin and addr < a + len(d) for a, d in self._lot.ecritures):
            self._vider()

    def _vider(self) -> None:
        import time
        ecr, self._lot.ecritures = self._lot.ecritures, []
        if not ecr or self.mem is None:
            return
        erreurs = []
        if self._vm is not None:
            t0 = time.perf_counter()
            try:
                n0 = self._vm.appels
                oks = self._vm.ecrire(ecr)
                self._compter(ecritures=len(ecr), appels=self._vm.appels - n0, lots=1,
                              octets=sum(len(d) for (_a, d), o in zip(ecr, oks) if o),
                              temps=time.perf_counter() - t0)
                erreurs = [a for (a, _d), o in zip(ecr, oks) if not o]
                ecr = []
            except VmIndisponible:
                self._vm_perdu()
        for a, d in ecr:
            try:
                if not self._ecrire_un(a, d):
                    erreurs.append(a)
            except MemoryReadError:
                erreurs.append(a)
        if erreurs:
            raise MemoryReadError(f"écriture impossible à 0x{erreurs[0]:X} ({len(erreurs)} morceau(x) non écrit(s))")
