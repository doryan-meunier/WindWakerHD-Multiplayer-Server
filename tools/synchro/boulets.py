"""Boulets du canon du bateau (bit 29, MOD NATIF 0.17.0 SEULEMENT ; natif/mod_wwmp/boulets.c, zone WWMPBOU1,
docs/dev/PORTAGE_NATIF.md §3.31, docs/etudes/BATEAU_TETE.md §9). Logique testable sans le jeu.

Constat en jeu (2026-11-15) : quand un joueur tire au canon depuis son bateau, l'autre ne voit ni le boulet ni son
explosion. Le boulet est une Bomb (proc 294, paramètres 0x80020004) créée par le bateau : il ne passe jamais par la main
de Link, et la synchro des explosions (``mobiles.py``, --share-bombs) ne suit que les bombes passées par la main.

Avec --share-bombs (et le bit 29 des deux côtés), activé par --coop-donjon :

  Chez le TIREUR : le mod met chaque tir de NOTRE bateau dans une file (cfg bit 0) : id, adresse, position et angle de
    départ, salle. Le client l'annonce aux autres de la zone (« tir » {id, pos, ang, room}) et donne le boulet à
    ``MobileSync.track_bomb`` : son explosion part alors en « boom » comme celle d'une bombe à pied (même relais, même
    rejeu : Bomb2 « explose tout de suite » chez les autres). S'il tombe à l'eau, il coule sans exploser, partout.

  Chez les AUTRES (cfg bit 1) : « tir » reçu depuis moins de TIR_TTL s -> CALL 16 : le mod crée une copie INERTE du
    boulet (même départ ; vitesses et gravité calculées comme le bateau du jeu) : le jeu la fait voler (fumée,
    sifflement) sur la trajectoire du tireur. Elle n'explose JAMAIS : quand elle le voudrait (sol, mur, coup), le mod
    la retire sans effet. Le « boom » du tireur (même id) la retire (``on_boom`` -> fin de son emplacement) et
    l'explosion est rejouée par ``mobiles.py`` : une seule explosion, dégâts selon les règles des bombes à pied.

Sans le bit 29 chez le receveur (pack Cemu, mod plus ancien) : « tir » ignoré, l'explosion arrive quand même (boom).
Sans le bit 29 chez le tireur : rien n'est vu (comme avant).
"""
from __future__ import annotations

import math
import threading
import time

PROC_BOMB = 294
TIR = "tir"
POS_MAX = 1e6
ANG_MASK = 0xFFFF


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def valid_tir(msg) -> dict | None:
    """Message « tir » (client -> serveur et relais) : None si invalide. ``ang`` rendu en s16 non signés (0..0xFFFF)."""
    if not isinstance(msg, dict):
        return None
    bid, pos, ang, room = msg.get("id"), msg.get("pos"), msg.get("ang"), msg.get("room")
    if not (_int(bid) and 0 < bid <= 0xFFFFFFFF):
        return None
    if not (isinstance(pos, (list, tuple)) and len(pos) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) <= POS_MAX
                    for v in pos)):
        return None
    if not (isinstance(ang, (list, tuple)) and len(ang) == 3 and all(_int(v) and -0x8000 <= v <= 0xFFFF for v in ang)):
        return None
    if not (_int(room) and 0 <= room <= 63):
        return None
    return {"id": bid, "pos": [float(v) for v in pos], "ang": [v & ANG_MASK for v in ang], "room": room}


class BouletSync:
    """Les deux côtés (tireur et receveur) ; ``step`` dans le fil de travail, ``on_message`` depuis la boucle réseau."""

    TIR_TTL = 1.0           # s : tir reçu non rejoué -> abandonné (le boulet serait déjà loin)
    MAX_RATE = 4            # copies créées au plus par seconde (le canon tire au plus une fois par seconde)
    COPIES_MAX = 32         # (tireur, id) -> copie gardés au plus
    CFG_PERIODE = 5.0       # s : cfg réécrit (jeu relancé sous le même client : zone remise à 0)

    def __init__(self, enabled: bool = False, clock=time.time):
        self.enabled = enabled
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.noted: set = set()
        self.zone: str | None = None
        self.seq: int | None = None         # dernier tir lu dans la file du mod (None : pas encore armé)
        self.cfg: int | None = None         # cfg écrit dans la zone du mod
        self.cfg_t = float("-inf")
        self.cfg_mb = None                  # boîte à qui il a été écrit (nouvelle boîte : réécrit, file réarmée)
        self.tirs: list[dict] = []          # tirs reçus à rejouer
        self.copies: dict[tuple, dict] = {}  # (tireur, id du boulet) -> {"copie": id chez nous, "t"}
        self.fins: list[int] = []           # copies à retirer (boom du tireur reçu)
        self.faits: list[float] = []
        self.n = {"tir_out": 0, "tir_in": 0, "copies": 0, "fins": 0, "refus": 0, "perdus": 0}

    # --- notes et files ------------------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 64:
            self.noted.add(tag)
            self._note(s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        if not self.enabled or not isinstance(msg, dict) or msg.get("type") != TIR:
            return
        if me is not None and msg.get("from") == me:
            return
        with self.lock:
            self.inbox = (self.inbox + [dict(msg, _t=self.clock() if now is None else now)])[-32:]

    def on_boom(self, src, bid, now: float | None = None) -> None:
        """Rappel de ``MobileSync`` (fil de travail) : « boom » reçu d'un joueur. Si c'est un de ses boulets : sa copie
        est à retirer (l'explosion est rejouée par objets mobiles), et un tir pas encore rejoué ne l'est plus."""
        if not self.enabled or not _int(bid):
            return
        c = self.copies.pop((src, bid), None)
        if c is not None:
            self.fins.append(c["copie"])
        self.tirs = [e for e in self.tirs if not (e["src"] == src and e["id"] == bid)]

    def reset_network(self) -> None:
        with self.lock:
            self.inbox.clear()
            self.out.clear()
        self.tirs.clear()

    # --- passe principale ----------------------------------------------------------------------------------
    def step(self, mb, now: float | None = None, zone: str | None = None, room: int = -1, ok_write: bool = True,
             track=None) -> None:
        """``mb`` : la boîte aux lettres (jeu.mailbox.Mailbox) ; ``ok_write`` : jeu libre, salle stable, boîte sans
        requête en attente ; ``track(id, adresse)`` : le boulet suivi par objets mobiles (son « boom »)."""
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        with self.lock:
            inbox, self.inbox = self.inbox, []
        if zone != self.zone:
            self.tirs.clear()
            self.copies.clear()             # copies d'une autre zone : la salle quittée les a retirées
            self.zone = zone
        for m in inbox:
            v = valid_tir(m)
            if v is None or m.get("zone") != zone or zone is None:
                continue
            self.n["tir_in"] += 1
            self.tirs = (self.tirs + [dict(v, src=m.get("from"), t=m["_t"])])[-8:]
        if mb is None or not getattr(mb, "boulets_ok", False):
            if self.tirs:
                self._once("sans_bit", "boulets du canon : mod natif 0.17.0 absent ici, boulets des autres non "
                                       "montrés (leur explosion arrive quand même)")
            self.tirs.clear()
            return
        cfg = 0x3
        if self.cfg != cfg or self.cfg_mb is not mb or now - self.cfg_t >= self.CFG_PERIODE:
            if self.cfg_mb is not mb:
                self.seq = None             # autre boîte (jeu relancé) : la file repart de son compteur
            mb.bou_regler(cfg)
            self.cfg, self.cfg_mb, self.cfg_t = cfg, mb, now
        self._tirs_out(mb, now, zone, track)
        for c in self.fins:
            if mb.boulet_fin(c):
                self.n["fins"] += 1
        self.fins.clear()
        self._copies(mb, now, room, ok_write)

    def _tirs_out(self, mb, now, zone, track) -> None:
        if self.seq is None:
            self.seq = mb.bou_seq()         # ce qui précède notre armement ne compte pas
            return
        self.seq, tirs, perdus = mb.bou_tirs(self.seq)
        self.n["perdus"] += perdus
        for t in tirs:
            if track is not None:
                try:
                    track(t["id"], t["adr"])
                except Exception:
                    pass
            if zone is None:
                continue
            msg = {"type": TIR, "zone": zone, "id": t["id"], "pos": [round(v, 2) for v in t["pos"]],
                   "ang": [v & ANG_MASK for v in t["ang"]], "room": t["salle"]}
            if valid_tir(msg) is None:
                continue
            with self.lock:
                self.out = (self.out + [msg])[-32:]
            self.n["tir_out"] += 1

    def _copies(self, mb, now, room, ok_write) -> None:
        keep = []
        for e in self.tirs:
            if now - e["t"] > self.TIR_TTL:
                self._note("boulets du canon : boulet d'un autre joueur non montré (cinématique, salle pas prête ou "
                           "boîte aux lettres occupée)")
                continue
            if not ok_write:
                keep.append(e)
                continue
            self.faits = [t for t in self.faits if now - t < 1.0]
            if len(self.faits) >= self.MAX_RATE:
                keep.append(e)
                continue
            salle = e["room"] if 0 <= e["room"] <= 63 else room
            try:
                r = mb.boulet_creer(e["pos"], e["ang"], salle, e["id"])
            except Exception as exc:
                self._note(f"boulets du canon : copie non créée ({type(exc).__name__}: {exc})")
                continue
            self.faits.append(now)
            if r > 0:
                self.n["copies"] += 1
                self.copies[(e["src"], e["id"])] = {"copie": r, "t": now}
                if len(self.copies) > self.COPIES_MAX:
                    self.copies.pop(next(iter(self.copies)))
            else:
                self.n["refus"] += 1
                self._once(("refus", r), f"boulets du canon : copie refusée par le mod ({r})")
        self.tirs = keep

    def status(self) -> str:
        n = self.n
        return (f"tirs {n['tir_out']} envoyés / {n['tir_in']} reçus, copies {n['copies']} (retirées au boom "
                f"{n['fins']}, refus {n['refus']})")
