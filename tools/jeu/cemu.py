"""Attache à Cemu et lit la RAM émulée de la Wii U (big-endian).

Hypothèse de travail (cohérente avec le code source de Cemu, à confirmer sur ta
machine via `python outils/wwhd.py calibrate`) : Cemu réserve d'un bloc 4 Gio
d'espace d'adressage (`memory_base`) et une adresse virtuelle Wii U `va`
correspond à `memory_base + va` dans le processus Cemu.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from jeu import procmem as winmem   # winmem (Windows) ou linmem (Linux), même interface : voir procmem.py
from jeu.procmem import MEM_FREE, Process, Region

CEMU_EXE_NAMES = winmem.NOMS_CEMU   # ("Cemu.exe",) sous Windows ; ("Cemu",) sous Linux (AppImage, Flatpak)
GUEST_SPACE = 0x1_0000_0000
MEM2_START = 0x1000_0000          # données / tas de l'application
MEM2_END = 0x5000_0000
CODE_START = 0x0200_0000          # .text de l'RPX


@dataclass
class BaseCandidate:
    base: int
    span: int
    committed: int
    code_ok: bool
    mem2_ok: bool

    @property
    def score(self) -> int:
        return (2 if self.code_ok else 0) + (2 if self.mem2_ok else 0) + (1 if self.span >= GUEST_SPACE else 0)


def attach(pid: int | None = None, write: bool = False) -> Process:
    nom = CEMU_EXE_NAMES[0]
    if pid is None:
        pids = []
        for name in CEMU_EXE_NAMES:
            pids += winmem.find_pids(name)
        if not pids:
            raise RuntimeError(f"{nom} introuvable : lance Cemu et charge Wind Waker HD d'abord (ou le portage natif « Wind Waker HD » avec le mod wwmp-natif).")
        if len(pids) > 1:
            print(f"[!] plusieurs {nom} ({pids}), j'utilise {pids[0]} (option --pid pour choisir)")
        pid = pids[0]
    return Process(pid, write=write)


def find_base_candidates(proc: Process) -> list[BaseCandidate]:
    """Regroupe les régions par AllocationBase et garde les réservations >= 3 Gio."""
    allocs: dict[int, list[Region]] = {}
    for r in proc.regions():
        if r.state == MEM_FREE or not r.alloc_base:
            continue
        allocs.setdefault(r.alloc_base, []).append(r)
    out = []
    for ab, regs in allocs.items():
        span = max(r.end for r in regs) - ab
        if span < 0xC000_0000:
            continue
        bases = [ab]
        if getattr(proc, "alloc_approx", False) and span > GUEST_SPACE:
            # Linux (linmem) : la « base d'allocation » est le début d'une suite de mappings anonymes contigus ;
            # si une autre réservation est collée devant celle de Cemu, la vraie base est plutôt fin - 4 Gio
            bases.append(ab + span - GUEST_SPACE)
        for b in bases:
            fin = b + min(span - (b - ab), GUEST_SPACE) if len(bases) > 1 else None
            committed = sum(max(0, min(r.end, fin) - max(r.base, b)) if fin is not None else r.size
                            for r in regs if r.readable)
            code_ok = proc.try_read(b + CODE_START, 4) is not None
            mem2_ok = proc.try_read(b + MEM2_START, 4) is not None
            c = BaseCandidate(b, span - (b - ab), committed, code_ok, mem2_ok)
            if len(bases) > 1:
                # départage (Linux) : la vraie base commence une ligne de maps (Cemu réserve d'un bloc à cet endroit)
                c.bord = any(r.base == b for r in regs)
            out.append(c)
    if any(hasattr(c, "bord") for c in out):
        out.sort(key=lambda c: (c.score, getattr(c, "bord", True), c.committed), reverse=True)
    else:
        out.sort(key=lambda c: (c.score, c.committed), reverse=True)
    return out


class GuestMemory:
    """Lecture d'adresses virtuelles Wii U (big-endian) dans le processus Cemu (ou du portage natif : jeu/natif.py,
    même classe avec une base fixe)."""
    hote = "cemu"           # « natif » : jeu.natif.MemoireNatif
    zone_boite = None       # natif : (lo, hi) où chercher la boîte aux lettres du mod C (mailbox.find)

    def __init__(self, proc: Process, base: int):
        self.proc = proc
        self.base = base

    # --- brut -------------------------------------------------------------
    def read(self, va: int, size: int) -> bytes:
        if not (0 <= va and va + size <= GUEST_SPACE):
            raise winmem.MemoryReadError(f"adresse invitée hors plage : 0x{va:X}")
        return self.proc.read(self.base + va, size)

    def try_read(self, va: int, size: int) -> bytes | None:
        try:
            return self.read(va, size)
        except winmem.MemoryReadError:
            return None

    # --- mode léger (Linux, linmem : --linux-leger) ----------------------------
    @property
    def leger(self) -> bool:
        """Accès groupés possibles (linmem en mode léger) ? Toujours False sous Windows (winmem) : rien n'y change."""
        return bool(getattr(self.proc, "leger", False)) and hasattr(self.proc, "read_lot")

    # Sans mode léger (Windows : ReadProcessMemory, un appel par lecture) : les morceaux proches sont lus d'un bloc.
    # Mesuré sous Windows 11 (docs/etudes/rollback_netcode.md §4 ter) : ~2,2 µs l'appel pour 4 octets, ~3 µs pour
    # 8 Kio, ~6 µs pour 64 Kio : relire 16 Kio de trop coûte moins qu'un appel de plus.
    LOT_ECART = 0x4000          # octets au plus entre deux morceaux lus du même bloc
    LOT_BLOC = 0x40000          # taille au plus d'un bloc
    lecture_groupee = True      # read_lot regroupe (ActorScanner.read_all s'en sert, même sans mode léger)

    def read_lot(self, morceaux: list[tuple[int, int]]) -> list[bytes | None]:
        """Plusieurs (va, taille) d'un coup ; None pour un morceau illisible ou hors plage. Mode léger (Linux) : un
        seul appel système. Sinon : les morceaux à moins de LOT_ECART octets l'un de l'autre sont lus en un bloc (au plus
        LOT_BLOC octets) ; un bloc illisible est relu morceau par morceau (même résultat qu'une lecture par morceau)."""
        if not self.leger:
            return self._read_lot_blocs(morceaux)
        ok = [0 <= va and va + n <= GUEST_SPACE for va, n in morceaux]
        lus = iter(self.proc.read_lot([(self.base + va, n) for (va, n), b in zip(morceaux, ok) if b]))
        return [next(lus) if b else None for b in ok]

    def _read_lot_blocs(self, morceaux: list[tuple[int, int]]) -> list[bytes | None]:
        out: list[bytes | None] = [None] * len(morceaux)
        ordre = sorted((i for i, (va, n) in enumerate(morceaux) if n > 0 and 0 <= va and va + n <= GUEST_SPACE),
                       key=lambda i: morceaux[i][0])
        for i, (va, n) in enumerate(morceaux):
            if n <= 0 and 0 <= va <= GUEST_SPACE:
                out[i] = b""
        k = 0
        while k < len(ordre):
            debut = morceaux[ordre[k]][0]
            fin = debut + morceaux[ordre[k]][1]
            j = k + 1
            while j < len(ordre):
                va, n = morceaux[ordre[j]]
                if va - fin > self.LOT_ECART or max(fin, va + n) - debut > self.LOT_BLOC:
                    break
                fin = max(fin, va + n)
                j += 1
            groupe = ordre[k:j]
            bloc = self.try_read(debut, fin - debut) if len(groupe) > 1 else None
            for i in groupe:
                va, n = morceaux[i]
                out[i] = bloc[va - debut:va - debut + n] if bloc is not None else self.try_read(va, n)
            k = j
        return out

    def lot(self):
        """``with mem.lot():`` écritures regroupées (mode léger) ; sans effet sinon."""
        f = getattr(self.proc, "lot", None) if self.leger else None
        if f is None:
            import contextlib
            return contextlib.nullcontext(self)
        return f()

    def write(self, va: int, data: bytes) -> None:
        if not (0 <= va and va + len(data) <= GUEST_SPACE):
            raise winmem.MemoryReadError(f"adresse invitée hors plage : 0x{va:X}")
        self.proc.write(self.base + va, data)

    def w_u32(self, va: int, value: int) -> None:
        self.write(va, struct.pack(">I", value & 0xFFFFFFFF))

    # --- typés (big-endian) ----------------------------------------------
    def _u(self, fmt: str, va: int, size: int):
        return struct.unpack(fmt, self.read(va, size))[0]

    def u8(self, va):  return self._u(">B", va, 1)
    def s8(self, va):  return self._u(">b", va, 1)
    def u16(self, va): return self._u(">H", va, 2)
    def s16(self, va): return self._u(">h", va, 2)
    def u32(self, va): return self._u(">I", va, 4)
    def s32(self, va): return self._u(">i", va, 4)
    def f32(self, va): return self._u(">f", va, 4)

    def vec3f(self, va) -> tuple[float, float, float]:
        return struct.unpack(">3f", self.read(va, 12))

    def vec3s(self, va) -> tuple[int, int, int]:
        return struct.unpack(">3h", self.read(va, 6))

    def cstr(self, va, maxlen=32) -> str:
        raw = self.read(va, maxlen).split(b"\0", 1)[0]
        return raw.decode("ascii", "replace")

    # --- régions ------------------------------------------------------------
    def committed_ranges(self, lo: int = MEM2_START, hi: int = MEM2_END) -> list[tuple[int, int]]:
        """Plages [va_debut, va_fin) lisibles dans [lo, hi), fusionnées."""
        out: list[list[int]] = []
        for r in self.proc.regions(self.base + lo, self.base + hi):
            if not r.readable:
                continue
            a = max(r.base, self.base + lo) - self.base
            b = min(r.end, self.base + hi) - self.base
            if b <= a:
                continue
            if out and out[-1][1] == a:
                out[-1][1] = b
            else:
                out.append([a, b])
        return [(a, b) for a, b in out]

    def iter_chunks(self, lo: int = MEM2_START, hi: int = MEM2_END, chunk: int = 32 << 20):
        """Itère (va, bytes) par blocs sur la mémoire lisible."""
        for a, b in self.committed_ranges(lo, hi):
            va = a
            while va < b:
                n = min(chunk, b - va)
                data = self.try_read(va, n)
                if data is not None:
                    yield va, data
                va += n

    def find_u32(self, value: int, lo: int = MEM2_START, hi: int = MEM2_END) -> list[int]:
        """Toutes les adresses alignées sur 4 contenant `value` (big-endian)."""
        needle = struct.pack(">I", value)
        hits = []
        for va, data in self.iter_chunks(lo, hi):
            i = data.find(needle)
            while i != -1:
                if (va + i) % 4 == 0:
                    hits.append(va + i)
                i = data.find(needle, i + 1)
        return hits

    def find_bytes(self, needle: bytes, lo: int = MEM2_START, hi: int = MEM2_END, limit=200) -> list[int]:
        hits = []
        for va, data in self.iter_chunks(lo, hi):
            i = data.find(needle)
            while i != -1 and len(hits) < limit:
                hits.append(va + i)
                i = data.find(needle, i + 1)
        return hits


LOG_BASE = "Init Wii U memory space (base: "


def base_du_journal(chemins: list[str] | None = None) -> int | None:
    """Base de la mémoire invitée écrite par Cemu dans son log.txt au démarrage (dernière ligne
    « Init Wii U memory space (base: 0x…) »), None si introuvable. Linux : le log.txt du dossier de données de Cemu
    (portable, ~/.local/share/Cemu, Flatpak)."""
    import os
    if chemins is None:
        chemins = []
        try:
            from jeu import plateforme
            for _c, d in plateforme.dossiers_cemu(None, None):
                chemins.append(os.path.join(d, "log.txt"))
        except Exception:                           # noqa: BLE001
            pass
    for ch in chemins:
        try:
            with open(ch, "r", encoding="utf-8", errors="replace") as f:
                texte = f.read()
        except OSError:
            continue
        i = texte.rfind(LOG_BASE)
        if i < 0:
            continue
        j = texte.find(")", i)
        try:
            return int(texte[i + len(LOG_BASE):j].strip(), 16)
        except ValueError:
            continue
    return None


def _base_valide(proc, bases: list[int], verbose: bool) -> int | None:
    """Première base dont le code du jeu est lisible et ressemble à Wind Waker HD (game.version_check)."""
    from jeu import game
    vues = set()
    for b in bases:
        if b in vues:
            continue
        vues.add(b)
        try:
            ok, _w = game.version_check(GuestMemory(proc, b))
        except Exception:                           # noqa: BLE001
            ok = False
        if ok:
            if verbose:
                print(f"base de la mémoire du jeu : 0x{b:X}")
            return b
    return None


def jeu_natif_choisi(pid: int | None = None, jeu: str | None = None) -> bool:
    """Ouvrir le portage natif plutôt que Cemu ? ``jeu`` (ou la variable WWMP_JEU) : « cemu », « natif » ou « auto »
    (défaut). Auto : Cemu dès qu'il tourne (rien ne change pour lui) ; le portage seulement s'il tourne SANS Cemu,
    ou si ``pid`` est celui du portage."""
    import os
    choix = (jeu or os.environ.get("WWMP_JEU") or "auto").strip().lower()
    if choix in ("natif", "recomp", "portage"):
        return True
    if choix == "cemu":
        return False
    try:
        from jeu import natif
        natifs = natif.find_natif_pids(robot=pid is not None)   # un --pid donné peut être le jeu du robot
        if not natifs:
            return False
        if pid is not None:
            return pid in natifs and pid not in winmem.find_cemu_pids()
        return not winmem.find_cemu_pids()
    except Exception:                               # noqa: BLE001  (liste des processus illisible : Cemu, comme avant)
        return False


def open_guest(pid: int | None = None, base: int | None = None, verbose: bool = True,
               write: bool = False, jeu: str | None = None) -> GuestMemory:
    if jeu_natif_choisi(pid, jeu):
        from jeu import natif
        return natif.open_natif(pid, write=write, verbose=verbose, base=base)
    proc = attach(pid, write)
    if base is None and winmem.SYSTEME == "linux":
        # vu le 2026-11-13 (Linux) : la meilleure réservation de 4 Gio n'était pas celle du jeu (« ce n'est pas Wind
        # Waker HD USA v0 (code 0x00000000) »). Cemu écrit la vraie base dans son log.txt (« Init Wii U memory space
        # (base: 0x…) ») ; sinon on garde le premier candidat dont le code est bien celui du jeu.
        base = _base_valide(proc, [b for b in (base_du_journal(),) if b is not None]
                            + [c.base for c in find_base_candidates(proc)], verbose)
    if base is None:
        cands = find_base_candidates(proc)
        if not cands:
            plus = winmem.expliquer_refus(proc.pid) if winmem.SYSTEME == "linux" else ""
            raise RuntimeError(
                "Base de la RAM émulée introuvable (aucune réservation >= 3 Gio). "
                "Le jeu est-il lancé ? Sinon passe --base 0x... (voir README, section Dépannage)."
                + (" " + plus if plus else ""))
        best = cands[0]
        if verbose:
            for c in cands:
                mark = "->" if c is best else "  "
                print(f"{mark} base candidate 0x{c.base:X}  span=0x{c.span:X}  "
                      f"committed={c.committed >> 20} Mio  code={'ok' if c.code_ok else 'non'}  "
                      f"mem2={'ok' if c.mem2_ok else 'non'}")
        base = best.base
    elif verbose:
        print(f"base imposée : 0x{base:X}")
    return GuestMemory(proc, base)
