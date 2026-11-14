"""Scanner de valeurs façon Cheat Engine, en lecture seule, sur la RAM Wii U (big-endian).

Nécessite numpy (`pip install numpy`).
"""
from __future__ import annotations

import time

try:
    import numpy as np
except ImportError as e:  # pragma: no cover
    raise SystemExit("Le mode scan nécessite numpy :  python -m pip install numpy") from e

from jeu.cemu import GuestMemory

TYPES = {
    "u8": np.dtype(">u1"), "s8": np.dtype(">i1"),
    "u16": np.dtype(">u2"), "s16": np.dtype(">i2"),
    "u32": np.dtype(">u4"), "s32": np.dtype(">i4"),
    "f32": np.dtype(">f4"),
}
_BITS = {1: np.dtype(">u1"), 2: np.dtype(">u2"), 4: np.dtype(">u4")}


def _cmp(op: str, new, old, a, b, is_float: bool):
    if op in ("changed", "unchanged"):
        nb, ob = new.view(_BITS[new.itemsize]), old.view(_BITS[old.itemsize])
        return nb != ob if op == "changed" else nb == ob
    if op == "inc":
        return new > old if a is None else np.isclose(new - old, a, atol=1e-3) if is_float else (new - old) == a
    if op == "dec":
        return new < old if a is None else np.isclose(old - new, a, atol=1e-3) if is_float else (old - new) == a
    if op == "eq":
        return np.isclose(new, a, rtol=1e-5, atol=1e-3) if is_float else new == a
    if op == "ne":
        return ~np.isclose(new, a, rtol=1e-5, atol=1e-3) if is_float else new != a
    if op == "gt":
        return new > a
    if op == "lt":
        return new < a
    if op == "between":
        return (new >= a) & (new <= b)
    if op == "near":
        return np.abs(new.astype(np.float64) - a) <= b
    raise ValueError(op)


class Scanner:
    def __init__(self, mem: GuestMemory, lo: int, hi: int):
        self.mem, self.lo, self.hi = mem, lo, hi
        self.dtype: np.dtype | None = None
        self.mode: str | None = None          # "snap" | "list"
        self.snap: list[tuple[int, np.ndarray]] = []
        self.addrs = np.zeros(0, dtype=np.uint64)
        self.vals = None
        self._undo = None

    # -------------------------------------------------------------------
    @property
    def is_float(self) -> bool:
        return self.dtype is not None and self.dtype.kind == "f"

    def count(self) -> int:
        if self.mode == "snap":
            return sum(len(a) for _, a in self.snap)
        return int(len(self.addrs))

    def _arr(self, data: bytes) -> np.ndarray:
        n = len(data) // self.dtype.itemsize * self.dtype.itemsize
        return np.frombuffer(data[:n], dtype=self.dtype)

    def new(self, tname: str, op: str | None = None, a=None, b=None) -> int:
        """Nouvelle recherche. op=None -> valeur inconnue (instantané complet)."""
        self.dtype = TYPES[tname]
        self._undo = None
        t0 = time.time()
        if op is None:
            self.mode, self.snap = "snap", []
            for va, data in self.mem.iter_chunks(self.lo, self.hi):
                self.snap.append((va, self._arr(data).copy()))
        else:
            self.mode = "list"
            addrs, vals = [], []
            for va, data in self.mem.iter_chunks(self.lo, self.hi):
                arr = self._arr(data)
                with np.errstate(invalid="ignore", over="ignore"):
                    m = _cmp(op, arr, arr, a, b, self.is_float)
                idx = np.nonzero(m)[0]
                addrs.append(va + idx.astype(np.uint64) * self.dtype.itemsize)
                vals.append(arr[idx].copy())
            self.addrs = np.concatenate(addrs) if addrs else np.zeros(0, np.uint64)
            self.vals = np.concatenate(vals) if vals else np.zeros(0, self.dtype)
        print(f"  {self.count():,} candidats ({time.time() - t0:.1f} s)")
        return self.count()

    def _read_current(self) -> np.ndarray:
        """Relit les valeurs des candidats en regroupant les lectures."""
        out = np.zeros(len(self.addrs), dtype=self.dtype)
        valid = np.ones(len(self.addrs), dtype=bool)
        if not len(self.addrs):
            return out
        sz = self.dtype.itemsize
        gaps = np.nonzero(np.diff(self.addrs) > 0x10000)[0] + 1
        starts = np.concatenate(([0], gaps))
        ends = np.concatenate((gaps, [len(self.addrs)]))
        for s, e in zip(starts, ends):
            a0, a1 = int(self.addrs[s]), int(self.addrs[e - 1]) + sz
            data = self.mem.try_read(a0, a1 - a0)
            if data is None:
                valid[s:e] = False
                continue
            arr = np.frombuffer(data, dtype=np.uint8)
            offs = (self.addrs[s:e] - a0).astype(np.int64)
            raw = np.stack([arr[offs + k] for k in range(sz)], axis=1).copy()
            out[s:e] = raw.view(self.dtype).reshape(-1)
        self._valid = valid
        return out

    def filter(self, op: str, a=None, b=None) -> int:
        if self.mode is None:
            print("  lance d'abord `new`")
            return 0
        self._undo = (self.mode, self.snap, self.addrs, self.vals)
        t0 = time.time()
        if self.mode == "snap":
            addrs, vals = [], []
            sz = self.dtype.itemsize
            for va, old in self.snap:
                data = self.mem.try_read(va, len(old) * sz)
                if data is None:
                    continue
                new = self._arr(data)
                with np.errstate(invalid="ignore", over="ignore"):
                    m = _cmp(op, new, old, a, b, self.is_float)
                idx = np.nonzero(m)[0]
                addrs.append(va + idx.astype(np.uint64) * sz)
                vals.append(new[idx].copy())
            self.mode, self.snap = "list", []
            self.addrs = np.concatenate(addrs) if addrs else np.zeros(0, np.uint64)
            self.vals = np.concatenate(vals) if vals else np.zeros(0, self.dtype)
        else:
            new = self._read_current()
            with np.errstate(invalid="ignore", over="ignore"):
                m = _cmp(op, new, self.vals, a, b, self.is_float) & self._valid
            self.addrs, self.vals = self.addrs[m], new[m]
        print(f"  {self.count():,} candidats ({time.time() - t0:.1f} s)")
        return self.count()

    def undo(self) -> None:
        if self._undo is None:
            print("  rien à annuler")
            return
        self.mode, self.snap, self.addrs, self.vals = self._undo
        self._undo = None
        print(f"  {self.count():,} candidats")

    def listing(self, n: int = 30) -> list[tuple[int, object, object]]:
        if self.mode != "list":
            return []
        cur = self._read_current()
        return [(int(self.addrs[i]), self.vals[i].item(), cur[i].item())
                for i in range(min(n, len(self.addrs)))]
