"""Touches F3-F9, PagePréc / PageSuiv du mode Hanteurs sous Linux (wwmp/hanteurs.Touches), en lecture seule.

- X11 (ou XWayland) avec python-xlib installé : état du clavier par ``XQueryKeymap`` et fenêtre au premier plan
  par ``_NET_ACTIVE_WINDOW`` -> ``_NET_WM_PID`` (même règle que sous Windows : les touches ne comptent que si la
  fenêtre de Cemu est devant). Rien n'est envoyé au jeu, rien n'est intercepté.
- Wayland pur : un programme ne peut pas lire le clavier global (choix de sécurité de Wayland) ; XWayland ne voit
  les touches que si la fenêtre devant est elle-même une fenêtre X11. Repli : les MÊMES actions sont des boutons
  du lanceur (onglet Lobby, panneau Mode Hanteurs, y compris « Monter » / « Descendre » sous Linux).
- python-xlib absent : même repli (``pip install python-xlib`` ou paquet python3-xlib).

``creer(env, xlib)`` rend ``(lire(vk) -> bool, premier_plan() -> pid | None, raison)`` ; ``raison`` vide quand les
touches marchent, sinon le texte à montrer au joueur. Codes ``vk`` : ceux de Windows (wwmp/hanteurs.VK).
"""
from __future__ import annotations

import os

# code de touche Windows (hanteurs.VK) -> keysym X11
KEYSYMS = {0x72: 0xFFC0, 0x73: 0xFFC1, 0x74: 0xFFC2, 0x75: 0xFFC3, 0x76: 0xFFC4, 0x77: 0xFFC5, 0x78: 0xFFC6,
           0x21: 0xFF55, 0x22: 0xFF56}      # F3…F9, Page_Up (Prior), Page_Down (Next)

RAISON_WAYLAND = ("session Wayland sans affichage X11 : les touches F3-F9 ne peuvent pas être lues ; utilise les "
                  "boutons du lanceur (onglet Lobby, Mode Hanteurs)")
RAISON_XLIB = ("python-xlib absent : touches F3-F9 inactives (pip install python-xlib, ou le paquet python3-xlib) ; "
               "les boutons du lanceur (onglet Lobby, Mode Hanteurs) font les mêmes actions")


def _rien(_vk=None):
    return False


def _personne():
    return None


def session(env=None) -> str:
    """« x11 », « xwayland » (Wayland avec DISPLAY), « wayland » ou « aucune »."""
    env = os.environ if env is None else env
    way = bool(env.get("WAYLAND_DISPLAY")) or (env.get("XDG_SESSION_TYPE") or "").lower() == "wayland"
    x = bool(env.get("DISPLAY"))
    if way:
        return "xwayland" if x else "wayland"
    return "x11" if x else "aucune"


def creer(env=None, xlib=None):
    s = session(env)
    if s in ("wayland", "aucune"):
        return _rien, _personne, RAISON_WAYLAND if s == "wayland" else "pas d'affichage graphique : touches inactives"
    try:
        if xlib is None:
            from Xlib import X, display as xdisplay  # type: ignore[import-not-found]
        else:
            X, xdisplay = xlib.X, xlib.display
        d = xdisplay.Display()
    except Exception:
        return _rien, _personne, RAISON_XLIB
    racine = d.screen().root
    a_actif = d.intern_atom("_NET_ACTIVE_WINDOW")
    a_pid = d.intern_atom("_NET_WM_PID")
    codes = {vk: d.keysym_to_keycode(ks) for vk, ks in KEYSYMS.items()}

    def lire(vk) -> bool:
        kc = codes.get(vk)
        if not kc:
            return False
        carte = d.query_keymap()
        return bool(carte[kc // 8] & (1 << (kc % 8)))

    def premier_plan():
        p = racine.get_full_property(a_actif, X.AnyPropertyType)
        if not p or not p.value:
            return None
        f = d.create_resource_object("window", int(p.value[0]))
        q = f.get_full_property(a_pid, X.AnyPropertyType)
        return int(q.value[0]) if q and q.value else None

    raison = "" if s == "x11" else ("XWayland : les touches ne comptent que si Cemu est une fenêtre X11 "
                                    "(GDK_BACKEND=x11) ; sinon utilise les boutons du lanceur")
    return lire, premier_plan, raison
