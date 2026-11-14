"""Point d'entrée unique de l'accès à la mémoire d'un autre processus : winmem (Windows) ou linmem (Linux).

Tout le code du mod passe par ici (``from wwlib import procmem``) ; sous Windows, les noms exportés SONT ceux de
winmem (mêmes classes, mêmes exceptions : rien ne change). Aussi : les noms du processus de Cemu selon le système
et ``cemu_tourne()``.
"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    from jeu.winmem import (MEM_COMMIT, MEM_FREE, MEM_RESERVE, MemoryReadError, Process, Region, USER_SPACE_END,
        find_pids, list_processes)
    SYSTEME = "windows"
    NOMS_CEMU = ("Cemu.exe",)
else:
    from jeu.linmem import (MEM_COMMIT, MEM_FREE, MEM_RESERVE, MemoryReadError, Process, Region, USER_SPACE_END,
        find_pids, list_processes)
    SYSTEME = "linux"
    # AppImage officielle et Flatpak (info.cemu.Cemu) : le processus s'appelle « Cemu » ; paquets des
    # distributions : « cemu »  (find_pids ignore la casse et un éventuel « .exe »)
    NOMS_CEMU = ("Cemu",)


def find_cemu_pids() -> list[int]:
    """PID de Cemu (Cemu.exe sous Windows, Cemu sous Linux), dans l'ordre de find_pids, sans doublon."""
    out: list[int] = []
    for n in NOMS_CEMU:
        for p in find_pids(n):
            if p not in out:
                out.append(p)
    return out


def cemu_tourne() -> bool:
    try:
        return bool(find_cemu_pids())
    except Exception:
        return False


def expliquer_refus(pid: int) -> str:
    """Pourquoi la mémoire de Cemu n'est pas accessible et quoi faire (texte vide sous Windows)."""
    if SYSTEME != "linux":
        return ""
    from jeu import linmem
    return " ".join(linmem.diagnostiquer(pid))
