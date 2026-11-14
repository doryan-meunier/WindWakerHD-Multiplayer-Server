"""Poids du fantôme (synchro v4 ; --share-poids, activé par --coop-donjon, --no-share-poids).

Avant : le fantôme d'un autre joueur ne pesait rien. Une plateforme à poids (Klft, Kita, Mswing, Mflft, Blift),
un pont de cordes ou un interrupteur au sol ne réagissait qu'au joueur du PC ; chez les autres, il restait
immobile sous le fantôme.

Maintenant (graphic pack v19 à jour, bit 8 des capacités, zone ``wwmp_poids`` en fin de pack) : à chaque image,
le jeu cherche le sol sous chaque fantôme et, si ce sol appartient à une espèce de la liste ci-dessous, appelle
son rappel « quelqu'un monte » comme si le joueur du PC y était (Link placé à la position du fantôme le temps de
l'appel, remis aussitôt). Chaque jeu simule donc le poids des deux joueurs : aucun message réseau, aucune
autorité, la position du fantôme suffit. Le fantôme n'est jamais « lourd » : le pack retire le bit des bottes de
plomb de notre Link le temps de l'appel (l'interrupteur lourd s'enfonce dans le jeu de celui qui porte les bottes
et arrive par les switches).

Ce module ne fait que tenir la zone armée : cfg à 1 et une « durée de vie » en images que le jeu décompte et que
le client recharge. Client arrêté, planté ou option coupée : la durée de vie tombe à 0 et le jeu redevient
exactement celui d'avant. Rien n'est écrit sans la capacité (pack v17 ou v19 plus ancien : mode dégradé, le
fantôme ne pèse rien, comme avant).

Mode Hanteurs (bit 15 des capacités) : le fantôme d'un Hanteur manifesté (spectre, vu seulement par les autres
Hanteurs) ne pèse pas. Le client passe à ``step`` le masque des emplacements 0 à 6 qui montrent un spectre ; il est
écrit dans les bits 8 à 14 du cfg, que le pack lit en tête de sa boucle (emplacement sauté, comme s'il était vide).
Chez un Link le masque est toujours nul (aucun spectre) : cfg reste exactement 1. Pack sans le bit 15 : rien de
nouveau n'est écrit (et un pack v19 plus ancien ne lit de toute façon que le bit 0). --no-spectre-sans-poids : masque
jamais posé.

Vérifié dans le RPX (tests/test_pack_mailbox.py, TestRpxPoids) ; pas encore essayé en jeu
(docs/dev/tests/PLAN_TEST_SYNCHRO.md).
"""
from __future__ import annotations

# espèces porteuses permises : procName -> nom (rappels relus dans le RPX ; recopiées dans l'asm, wwmp_poids_procs)
PORTEURS = {
    27: "interrupteur au sol",
    28: "interrupteur au sol lourd",
    89: "pont de cordes",
    90: "plateforme suspendue (Mswing)",
    91: "plateforme flottante (Mflft)",
    92: "plateforme à poids (Klft)",
    97: "plateforme à poids (Kita)",
    111: "balance (Blift)",
}
VIE = 150            # images armées d'avance (environ 5 s à 30 images/s)
PERIODE = 1.0        # rechargée une fois par seconde
N_PROCS = 512


def bitmap_pack(procs=PORTEURS) -> bytes:
    """Bitmap de 64 octets du pack : bit 0x80 >> (n & 7) de l'octet n >> 3."""
    bm = bytearray(N_PROCS // 8)
    for n in procs:
        if not 0 <= int(n) < N_PROCS:
            raise ValueError(f"procName hors bitmap : {n}")
        bm[n >> 3] |= 0x80 >> (n & 7)
    return bytes(bm)


class PoidsSync:
    """Tient armée la zone ``wwmp_poids`` du pack tant que l'option est active et que le jeu est jouable."""

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.active = True           # bascule à chaud éventuelle
        self.on = False              # ce que nous avons écrit en dernier
        self.t = None                # dernière recharge
        self.supported = None        # None : pas encore vu de boîte
        self.faits = self.refus = 0
        self.dernier = None          # (proc, emplacement) du dernier rappel ou refus
        self.sans = 0                # masque des emplacements sans poids écrit en dernier (bit 15 du pack)
        self.notes: list[str] = []

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def step(self, mb, now: float, want: bool = True, sans: int = 0) -> None:
        """``mb`` : Mailbox en écriture ou None ; ``want`` : jeu jouable (pas de transition de stage) ; ``sans`` :
        masque des emplacements de fantômes 0 à 6 qui ne pèsent pas (spectres de Hanteurs), ignoré sans le bit 15
        du pack. Un masque qui change est écrit tout de suite (sans attendre la recharge) ; la recharge le
        réécrit avec le cfg, sinon elle l'effacerait."""
        if not self.enabled:
            return
        ok = bool(mb is not None and getattr(mb, "poids", False))
        if mb is not None and ok != self.supported:
            self.supported = ok
            self.notes.append("poids du fantôme : actif (plateformes, ponts de cordes, interrupteurs au sol)" if ok
                              else "poids du fantôme : graphic pack sans cette capacité, le fantôme ne pèse rien "
                                   "(mets le pack à jour)")
        if not ok:
            self.on = False
            self.sans = 0
            return
        sans = int(sans) & 0x7F if getattr(mb, "poids_slots", False) else 0
        want = bool(want and self.active)
        if not want:
            if self.on:
                mb.set_poids(False)
                self.on = False
            self.sans = 0
            return
        if not self.on or self.t is None or now - self.t >= PERIODE or now < self.t or sans != self.sans:
            mb.set_poids(True, VIE, sans)
            self.on, self.t, self.sans = True, now, sans
            c = mb.poids_counters()
            self.faits, self.refus = int(c.get("faits", 0)), int(c.get("refus", 0))
            if c.get("faits") or c.get("refus"):
                self.dernier = (int(c.get("proc", 0)), int(c.get("slot", 0)))

    def stop(self, mb) -> None:
        """Arrêt du client : zone coupée tout de suite (sinon elle s'éteint seule en VIE images)."""
        try:
            if mb is not None and getattr(mb, "poids", False):
                mb.set_poids(False)
        finally:
            self.on = False
            self.sans = 0

    def status(self) -> str:
        if self.supported is None:
            return "en attente du jeu"
        if not self.supported:
            return "pack sans la capacité (le fantôme ne pèse rien)"
        s = ("armé" if self.on else "au repos") + f", {self.faits} appui(s)"
        if self.refus:
            s += f", {self.refus} sol(s) mobile(s) hors liste"
        if self.sans:
            s += f", {bin(self.sans).count('1')} spectre(s) sans poids"
        if self.dernier:
            p = self.dernier[0]
            s += f", dernier : {PORTEURS.get(p, 'espèce ' + str(p))}"
        return s
