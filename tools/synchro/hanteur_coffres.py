"""Coffres fermés à un Hanteur (mode Hanteurs ; actif avec le mode, --no-hanteur-coffres pour le couper).

Avant : un Hanteur est invisible, invulnérable, et le serveur ignore tout ce qu'il fait ; mais dans SON jeu il pouvait
encore ouvrir un coffre : l'objet arrivait chez lui et le coffre restait ouvert dans sa sauvegarde locale.

Maintenant (graphic pack v19 à jour, bit 19 des capacités, zone ``wwmp_hcof`` en fin de pack) : tant que ce module
tient le drapeau posé, tout coffre fermé (acteur daTbox : petits et grands coffres, coffres à grande clé, sombres, de
Tingle, coffres qui apparaissent) répond au jeu « Link est trop loin » : « Ouvrir » ne s'affiche plus sur A, A fait ce
qu'il fait ailleurs, le coffre reste fermé et dessiné. Rien d'autre ne change (Link n'est pas bloqué).

Aussi hors du mode Hanteurs (coffres partagés, synchro/coffres.py) : tant qu'un coffre ouvert par un autre joueur est
affiché ouvrable au contact de Link (``ChestSync.contact``), le même drapeau le rend inouvrable (bug du 2026-11-13 :
coffre d'une tour de guet rouvert chez l'autre joueur) ; retiré dès que Link s'éloigne (le coffre est alors figé).

Qui pose le drapeau : le PC dont le monde est fermé (``HanteurSync.monde_ok()`` faux), et lui seul. Donc le
Hanteur tant qu'il l'est (spectateur, manifesté, monstre possédé, et pendant la potion : il y joue le Link X dans SON
jeu, où ce qu'il ouvrirait ne compterait pas) ; puis, redevenu Link par un échange Crawl ou une rotation, pendant la
quarantaine (3,5 s) où rien de ce qu'il ferait ne compterait encore (relecture adverse) : « Ouvrir » revient ensuite.
Le PC du Link X possédé par la potion le pose aussi, pendant la possession (son Link est gelé ; sans gel il est libre
mais son monde est fermé) et la quarantaine qui suit. Fin de partie, connexion perdue : retiré au tick suivant.

Le drapeau ne vaut que jusqu'à une échéance (compteur d'images de la boîte + HCOF_IMAGES) repoussée à chaque tick :
client arrêté, planté, option coupée, mode quitté : coffres rendus en 2 s de jeu au plus. Le compteur avance avec
l'execute de Link : un chargement de salle ou la pause ne mangent pas l'échéance.

Pack sans le bit 19 (v17, v19 plus ancien) : rien n'est écrit, une note demande de mettre le pack à jour ; ce que le
Hanteur ouvre reste chez lui (le serveur l'ignore, comme avant).

Vérifié hors ligne (tests/test_hanteur_coffres.py : RPX, exécution emu_ppc de l'accroche dans le code du jeu,
client) ; pas encore essayé en jeu (docs/dev/tests/PLAN_TEST_SYNCHRO.md).
"""
from __future__ import annotations

PERIODE = 0.25       # échéance repoussée au moins toutes les 0,25 s (chaque tick en pratique)
NOTE_PACK = ("coffres fermés aux Hanteurs : graphic pack sans cette capacité (bit 19) ; en Hanteur tu peux encore "
             "ouvrir un coffre dans TON jeu (rien n'est partagé) : réinstalle le pack et relance Cemu")
# Le compteur du pack avance à chaque image où un coffre fermé est chargé (pas à chaque appui sur A) : la note dit
# qu'un coffre est là, pas qu'il a été essayé (relecture adverse).
NOTE_REFUS = ("un coffre est là, mais il ne s'ouvrira pas dans ton jeu tant que tu es Hanteur "
              "(ni quelques secondes après ton retour en Link)")


class CoffresHanteurSync:
    """Tient le drapeau « Hanteur » de la zone ``wwmp_hcof`` posé tant que le joueur de ce PC est Hanteur."""

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self.on = False              # ce que nous avons écrit en dernier
        self.t = None                # dernière recharge
        self.supported = None        # None : pas encore vu de boîte
        self.nettoye = False         # zone remise à 0 une fois (drapeau laissé par une session tuée net)
        self.refus = 0               # dernier compteur lu (images x coffres fermés vus drapeau posé, pas des appuis)
        self.refus_base = None       # compteur au moment où le drapeau a été posé
        self.n_pose = 0
        self.note_refus = False      # NOTE_REFUS déjà dite pour cette période de Hanteur
        self.note_pack = False       # NOTE_PACK déjà dite pour cette période de Hanteur
        self.notes: list[str] = []

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def actif(self) -> bool:
        """Vrai : le drapeau est posé par nous (coffres fermés dans ce jeu)."""
        return self.enabled and self.on

    def step(self, mb, now: float, want: bool = False, note: bool = True, note_pack: bool = True) -> None:
        """``mb`` : Mailbox en écriture ou None ; ``want`` : le monde est fermé à ce PC maintenant (Hanteur,
        quarantaine, possédé), ou un coffre ouvert par un autre joueur est affiché ouvrable au contact de Link
        (synchro/coffres.py, ``ChestSync.contact``) ; ``note`` faux : pas de NOTE_REFUS (Link possédé : il ne joue pas,
        ou drapeau tenu pour un coffre seulement) ; ``note_pack`` faux : pas de NOTE_PACK (texte propre aux Hanteurs)."""
        if not self.enabled:
            return
        ok = bool(mb is not None and getattr(mb, "hcof", False))
        if mb is not None:
            self.supported = ok
        if not want:
            self.note_refus = self.note_pack = False
        if not ok:
            self.on = False
            if want and note_pack and mb is not None and not self.note_pack:
                self.note_pack = True
                self.notes.append(NOTE_PACK)
            return
        if not want:
            if self.on or not self.nettoye:
                mb.set_hcof(False)
                self.on, self.nettoye = False, True
            return
        if not self.on or self.t is None or now - self.t >= PERIODE or now < self.t:
            if not self.on:
                self.n_pose += 1
                self.refus_base = None
            mb.set_hcof(True)
            self.on, self.t, self.nettoye = True, now, True
        r = int(mb.hcof_counters().get("refus", 0))
        if self.refus_base is None:
            self.refus_base = r
        self.refus = r
        if r != self.refus_base and note and not self.note_refus:
            self.note_refus = True
            self.notes.append(NOTE_REFUS)

    def stop(self, mb) -> None:
        """Arrêt du client : coffres rendus tout de suite (sinon l'échéance passe seule en 2 s de jeu)."""
        try:
            if mb is not None and getattr(mb, "hcof", False):
                mb.set_hcof(False)
        finally:
            self.on = False

    def status(self) -> str:
        if self.supported is None:
            return "en attente du jeu"
        if not self.supported:
            return "pack sans la capacité (un Hanteur peut encore ouvrir un coffre chez lui)"
        return (("coffres FERMÉS (Hanteur ou retour en Link)" if self.on else "coffres ouverts (Link)")
                + f", {self.refus} images x coffres fermés")
