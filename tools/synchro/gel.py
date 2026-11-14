"""Link gelé (mode Hanteurs ; --share-gel, activé par --coop-donjon, --no-gel).

Avant : le Link caché d'un joueur possédé par la potion (et celui d'un Hanteur spectateur) restait un vrai Link,
manette active. Caché ne veut pas dire inerte : il pouvait pousser un bloc, marcher sur un interrupteur, ramasser
une clé, frapper, se faire toucher. Seul le réseau était filtré (rien de ce qu'il faisait n'était partagé).

Maintenant (graphic pack v19 à jour, bit 10 des capacités, zone ``wwmp_gel`` en fin de pack) : tant que ce module
tient la zone armée, le jeu n'appelle plus l'execute de Link. Link ne lit plus la manette, ne bouge plus, ne pose
plus ses colliders (ni coup donné, ni coup reçu), ne ramasse rien, ne pousse rien, ne parle à personne, ne change
pas de salle. Le reste du jeu tourne (la boîte aux lettres est appelée à la place de l'execute). Le jeu saute déjà
lui-même l'execute des acteurs (pause, mStopStatus) : c'est le même chemin. Jamais pendant un événement du jeu
(Link doit jouer sa cinématique) : le pack rend alors Link au jeu, image par image.

Deux usages (synchro/hanteurs.py) :
  - Link possédé par la potion : gelé pendant toute la possession, collé chaque tick sur le clone (la caméra suit
    son corps, joué par le Hanteur) ;
  - Hanteur spectateur « collé » (caméra spectateur) : gelé et collé sur le Link suivi tant qu'ils sont dans la
    même salle ; se manifester (F9) le dégèle.

Ce module ne fait que tenir la zone armée : cfg à 1 et une durée de vie en images que le jeu décompte et que le
client recharge. Client arrêté, planté, option coupée : la durée de vie tombe à 0 en moins de 2 s et Link revit.
Rien n'est écrit sans la capacité (pack v17 ou v19 plus ancien : mode dégradé, comme avant : Link caché mais
libre, filtre réseau seul).

Vérifié hors ligne (tests/test_gel.py : statique, RPX, exécution emu_ppc) ; pas encore essayé en jeu
(docs/dev/tests/PLAN_TEST_SYNCHRO.md).
"""
from __future__ import annotations

import struct

VIE = 45             # images armées d'avance (1,5 s à 30 images/s)
PERIODE = 0.4        # rechargée toutes les 0,4 s
OFF_OLD_POS = 0x300  # old.pos
OFF_CUR_POS = 0x314  # current.pos
OFF_SPEED = 0x33C    # speed
OFF_SPEEDF = 0x370   # speedF
OFF_EYE = 0x37C      # eyePos (échangé avec les deux autres par A3 : même trio)
OFF_PC_ID = 4
PROC_LINK = 168
POS_MAX = 1.0e6


def coller(mem, base: int, pid: int, pos) -> bool:
    """Link GELÉ (relu : id de process et procName 168) posé en ``pos`` : vitesses à 0, old.pos, current.pos, et
    eyePos déplacé du même vecteur (la caméra vise les yeux). Lecture aberrante ou position folle : rien."""
    try:
        p = tuple(float(v) for v in pos)
    except (TypeError, ValueError):
        return False
    if len(p) != 3 or not all(v == v and abs(v) < POS_MAX for v in p) or not base or pid is None:
        return False
    try:
        r = mem.try_read(base + OFF_PC_ID, 6) if hasattr(mem, "try_read") else mem.read(base + OFF_PC_ID, 6)
        if r is None or struct.unpack(">Ih", bytes(r)) != (pid & 0xFFFFFFFF, PROC_LINK):
            return False
        cur = struct.unpack(">3f", bytes(mem.read(base + OFF_CUR_POS, 12)))
        eye = struct.unpack(">3f", bytes(mem.read(base + OFF_EYE, 12)))
    except Exception:
        return False
    if not all(v == v and abs(v) < POS_MAX for v in cur + eye):
        return False
    d = tuple(e - c for e, c in zip(eye, cur))
    if not all(abs(v) < 400.0 for v in d):
        d = (0.0, 100.0, 0.0)                   # yeux incohérents : hauteur d'un Link debout
    mem.write(base + OFF_SPEED, struct.pack(">3f", 0.0, 0.0, 0.0))
    mem.write(base + OFF_SPEEDF, struct.pack(">f", 0.0))
    mem.write(base + OFF_OLD_POS, struct.pack(">3f", *p))
    mem.write(base + OFF_CUR_POS, struct.pack(">3f", *p))
    mem.write(base + OFF_EYE, struct.pack(">3f", *(a + b for a, b in zip(p, d))))
    return True


class GelSync:
    """Tient armée la zone ``wwmp_gel`` du pack tant que le mode Hanteurs le demande."""

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.on = False              # ce que nous avons écrit en dernier
        self.t = None                # dernière recharge
        self.supported = None        # None : pas encore vu de boîte
        self.gelees = self.evt = 0
        self.n_arme = 0
        self.notes: list[str] = []

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def gele(self) -> bool:
        """Vrai : la zone est armée par nous (Link ne s'exécute plus, hors événement du jeu)."""
        return self.enabled and self.on

    def step(self, mb, now: float, want: bool = False) -> None:
        """``mb`` : Mailbox en écriture ou None ; ``want`` : le mode Hanteurs veut Link gelé maintenant."""
        if not self.enabled:
            return
        ok = bool(mb is not None and getattr(mb, "gel", False))
        if mb is not None and ok != self.supported:
            self.supported = ok
            if not ok:
                self.notes.append("Link gelé : graphic pack sans cette capacité ; un Link possédé reste libre de "
                                  "bouger chez lui, caché (mets le pack à jour)")
        if not ok:
            self.on = False
            return
        if not want:
            if self.on:
                mb.set_gel(False)
                self.on = False
            return
        if not self.on or self.t is None or now - self.t >= PERIODE or now < self.t:
            if not self.on:
                self.n_arme += 1
            mb.set_gel(True, VIE)
            self.on, self.t = True, now
            c = mb.gel_counters()
            self.gelees, self.evt = int(c.get("gelees", 0)), int(c.get("evt", 0))

    def stop(self, mb) -> None:
        """Arrêt du client : Link rendu tout de suite (sinon la zone s'éteint seule en VIE images)."""
        try:
            if mb is not None and getattr(mb, "gel", False):
                mb.set_gel(False)
        finally:
            self.on = False

    def status(self) -> str:
        if self.supported is None:
            return "en attente du jeu"
        if not self.supported:
            return "pack sans la capacité (Link caché mais libre)"
        return ("GELÉ" if self.on else "libre") + f", {self.gelees} image(s) gelée(s), {self.evt} rendue(s) au jeu " \
                                                    f"pendant un événement"
