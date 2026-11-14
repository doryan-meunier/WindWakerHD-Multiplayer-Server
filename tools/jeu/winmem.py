"""Accès mémoire d'un autre processus Windows (ctypes, aucune dépendance).

Par défaut : lecture seule (OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION),
ReadProcessMemory, VirtualQueryEx). L'écriture (WriteProcessMemory) n'est
possible qu'avec Process(pid, write=True), réservé à la boîte aux lettres.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from dataclasses import dataclass
from typing import Iterator

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("winmem ne fonctionne que sous Windows")

if ctypes.sizeof(ctypes.c_void_p) != 8:
    raise ImportError("Python 64 bits requis (Cemu est un processus 64 bits)")

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_FREE = 0x10000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100

USER_SPACE_END = 0x7FFF_FFFF_FFFF


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.OpenProcess.restype = wt.HANDLE
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.CloseHandle.restype = wt.BOOL
_k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.ReadProcessMemory.restype = wt.BOOL
_k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_char_p,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.WriteProcessMemory.restype = wt.BOOL
_k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32FirstW.restype = wt.BOOL
_k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.restype = wt.BOOL


from jeu.memerreur import MemoryReadError  # noqa: E402,F401  (commune à winmem et linmem)


@dataclass(frozen=True)
class Region:
    base: int
    size: int
    alloc_base: int
    state: int
    protect: int
    type: int

    @property
    def end(self) -> int:
        return self.base + self.size

    @property
    def readable(self) -> bool:
        return (self.state == MEM_COMMIT
                and not (self.protect & PAGE_GUARD)
                and not (self.protect & PAGE_NOACCESS)
                and self.protect != 0)


def list_processes() -> list[tuple[int, str]]:
    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    out = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((entry.th32ProcessID, entry.szExeFile))
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snap)
    return out


def find_pids(exe_name: str) -> list[int]:
    exe_name = exe_name.lower()
    return [pid for pid, name in list_processes() if name.lower() == exe_name]


class Process:
    """Poignée sur un processus : lecture seule par défaut, écriture seulement
    si write=True (utilisé uniquement par la boîte aux lettres, étape 3)."""

    def __init__(self, pid: int, write: bool = False):
        self.pid = pid
        self.writable = write
        access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
        if write:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.handle = _k32.OpenProcess(access, False, pid)
        if not self.handle:
            err = ctypes.get_last_error()
            raise PermissionError(
                f"OpenProcess({pid}) a échoué (erreur Windows {err}). "
                "Lance le terminal avec les mêmes droits que Cemu (admin si Cemu l'est).")

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def read(self, addr: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = _k32.ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got))
        if not ok or got.value != size:
            raise MemoryReadError(f"lecture impossible à 0x{addr:X} ({size} octets)")
        return buf.raw

    def write(self, addr: int, data: bytes) -> None:
        if not self.writable:
            raise PermissionError("processus ouvert en lecture seule")
        got = ctypes.c_size_t(0)
        ok = _k32.WriteProcessMemory(self.handle, ctypes.c_void_p(addr), data, len(data), ctypes.byref(got))
        if not ok or got.value != len(data):
            raise MemoryReadError(f"écriture impossible à 0x{addr:X} ({len(data)} octets)")

    def try_read(self, addr: int, size: int) -> bytes | None:
        try:
            return self.read(addr, size)
        except MemoryReadError:
            return None

    def query(self, addr: int) -> Region | None:
        mbi = MEMORY_BASIC_INFORMATION()
        n = _k32.VirtualQueryEx(self.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if n == 0:
            return None
        return Region(mbi.BaseAddress or 0, mbi.RegionSize, mbi.AllocationBase or 0,
                      mbi.State, mbi.Protect, mbi.Type)

    def regions(self, start: int = 0, end: int = USER_SPACE_END) -> Iterator[Region]:
        addr = start
        while addr < end:
            r = self.query(addr)
            if r is None or r.size == 0:
                break
            yield r
            addr = r.base + r.size
