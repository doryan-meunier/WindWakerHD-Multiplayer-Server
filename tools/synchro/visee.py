"""Visée L/ZL entre Hanteurs, Link possédés et monstres (mode Hanteurs ; actif avec le mode, --no-visee pour couper).

Demande : « pouvoir cibler les Link pour les Hanteurs, et les Link doivent pouvoir cibler les monstres et les autres
Link possédés par les Hanteurs ». Cibler = la visée L/ZL du jeu (dAttention_c) : réticule, caméra centrée, Link
tourné vers la cible.

Le jeu ne vise que les acteurs dont les drapeaux d'attention (+0x39C) ont un bit de visée. Un fantôme (proc 0x1F0)
n'en a aucun ; une marionnette à IA suspendue garde ceux de sa dernière image d'IA, à une position figée. Le graphic
pack (v19, bit 20 des capacités, zone ``wwmp_visee``) recopie à chaque image ce que ce module écrit dans les
emplacements :

- fantômes : deux octets par emplacement (drapeaux, ligne de distance). Le pack ne garde que LOCKON_MISC (1) et
  LOCKON_BATTLE (4), jamais parole ni action ; position visée = yeux du fantôme. Par défaut 4 et la ligne 0x22 (celle
  du Bokoblin : portée 500 / 600) ; ``--visee-misc`` : 1 et la ligne 3 (repli si la musique de combat gêne) ;
- marionnettes d'ennemis à IA suspendue : y visé (pieds + hauteur de l'espèce) ; le pack pose 4 si les PV sont > 0 et
  si l'emplacement n'est pas dans le masque, sinon 0. Chez un Hanteur, toutes ses marionnettes sont dans le masque :
  il ne vise que les Link, jamais son propre monstre piloté.

Règle (``regle``), recalculée à chaque tick et appliquée au même tick (un emplacement absent de la règle reçoit 0 :
fin de potion, Crawl, rotation, changement de salle effacent la visée tout de suite) :

==========================================  =========================================================
PC / rôle                                   fantômes visables
==========================================  =========================================================
hors mode Hanteurs                          aucun, cfg à 0 (rien ne change)
Link non possédé                            les Link possédés (``hs.possedes`` : leur fantôme est le clone)
Link possédé (``hs.envoute``, gelé)         aucun
Hanteur qui joue un Link (potion, clone)    les Link de sa zone, sauf sa cible et les possédés
Hanteur manifesté (F9) ou pilote            les Link de sa zone, non possédés
Hanteur spectateur (caché ou collé)         aucun : il doit se manifester
fantôme d'un Hanteur (spectre)              jamais
==========================================  =========================================================

Les Link normaux ne sont jamais visables entre Link. Marionnettes : chez un Link (mode Hanteurs, pas possédé) y visé
pour les espèces de ``HAUTEUR_VISEE`` ; chez un Hanteur, toutes masquées ; hors mode Hanteurs rien
(``--visee-marionnettes`` pour l'avoir aussi en coop : la visée d'une marionnette suivrait alors sa position). Une
marionnette masquée un jour est rendue visable (y de repli, ``replis``) quand le masque tombe : rotation ou Crawl
vers Link, ou fin du mode (cfg bit 1 gardé ``EXTINCTION`` puis coupé). Un état laissé par une session tuée net est
éteint au démarrage, même synchro désactivée (coop normale, --no-visee).

Hors périmètre (docs/dev/SYNCHRO.md) : lecture du verrou du Hanteur pour diriger son monstre (``hviser`` automatique ;
formule d'indexation de dAttention_c non vérifiée, F7 reste la seule commande) ; vrais coups sur un fantôme visé
(le PvP reste compté par la lame, synchro/potion.py) ; réglage de partie côté serveur.

Pack sans le bit 20 : rien n'est écrit, une note. Vérifié hors ligne (tests/test_visee.py) ; RIEN vu en jeu.
"""
from __future__ import annotations

import time

from synchro import hanteurs
from commun.protocole import zone_of

DRAPEAUX_COMBAT, LIGNE_COMBAT = 4, 0x22      # LOCKON_BATTLE, ligne du Bokoblin (500 / 600)
DRAPEAUX_MISC, LIGNE_MISC = 1, 3             # --visee-misc : LOCKON_MISC, ligne 3 [HYPO : portée non mesurée]
FRAIS = 1.0                                  # s : état d'un autre joueur plus vieux : pas visable (comme le PvP)
EXTINCTION = 1.0                             # s : cfg gardé, emplacements à 0, avant de couper (fin du mode)
RAFRAICHIR = 2.0                             # s : tout est réécrit au moins aussi souvent
# hauteur (u) de la position visée au-dessus des pieds, par espèce [HYPO : à mesurer en jeu, PLAN_TEST_SYNCHRO]
HAUTEUR_VISEE = {189: 110.0, 188: 200.0, 206: 50.0, 215: 20.0, 191: 150.0, 190: 150.0, 247: 50.0, 203: 100.0,
                 224: 140.0, 181: 60.0}
HAUTEUR_DEFAUT = 80.0                        # espèce hors liste, chez un Hanteur seulement (toujours masquée)
NOTE_PACK = ("visée L/ZL des Hanteurs : graphic pack sans cette capacité (bit 20) ; réinstalle le pack et relance "
             "Cemu (sans elle, rien ne change)")


def regle(hs, others: dict, zone, moi=None, busy: bool = False, misc: bool = False,
          t: float | None = None) -> dict[int, tuple[int, int]] | None:
    """Joueurs dont le fantôme est visable dans CE jeu : {joueur: (drapeaux, ligne)}. None : hors mode Hanteurs
    (rien n'est écrit). Mêmes filtres que les cibles du PvP : même zone, pas en événement, état de moins de
    ``FRAIS`` s."""
    if not (hs.enabled and hs.actif()):
        return None
    out: dict[int, tuple[int, int]] = {}
    if zone is None or busy:
        return out
    t = time.time() if t is None else t
    val = (DRAPEAUX_MISC, LIGNE_MISC) if misc else (DRAPEAUX_COMBAT, LIGNE_COMBAT)
    if hs.role == hanteurs.ROLE_LINK:
        if hs.envoute is not None:
            return out                          # Link possédé : gelé, il ne vise rien
        voulus = set(hs.possedes)               # leur fantôme est le clone du Hanteur
    elif hs.role == hanteurs.ROLE_HANTEUR:
        clone = hs.clone
        if clone is None and not hs.manifeste and hs.possede is None:
            return out                          # spectateur : il doit se manifester (F9)
        voulus = {p for p, r in hs.roles.items() if r == hanteurs.ROLE_LINK and p not in hs.possedes}
        if clone is not None:
            voulus.discard(clone.get("cible"))
    else:
        return out
    voulus.discard(moi)
    for p, o in dict(others).items():
        if p not in voulus or not isinstance(o, dict):
            continue
        try:
            if zone_of(o.get("stage"), o.get("room")) != zone:
                continue
        except Exception:
            continue
        if o.get("busy") in (1, True) or t - o.get("_t", 0) > FRAIS:
            continue
        out[p] = val
    return out


def mode_marionnettes(hs, hors_mode: bool = False) -> str | None:
    """"link" (y visé pour les espèces connues), "hanteur" (toutes masquées) ou None (rien d'écrit)."""
    if hs.enabled and hs.actif():
        if hs.role == hanteurs.ROLE_HANTEUR:
            return "hanteur"
        return "link" if hs.envoute is None else None
    return "link" if hors_mode else None


def marionnettes(mode: str | None, infos: dict) -> tuple[dict[int, float], int] | None:
    """``infos`` : {emplacement: (proc, y des pieds)} des marionnettes. Rend ({emplacement: y visé}, masque) ou None
    (``mode`` None : rien d'écrit)."""
    if mode is None:
        return None
    ys: dict[int, float] = {}
    masque = 0
    for i, (proc, y) in dict(infos).items():
        if not isinstance(i, int) or not 0 <= i < 16:
            continue
        h = HAUTEUR_VISEE.get(proc)
        if mode == "hanteur":
            masque |= 1 << i
            h = HAUTEUR_DEFAUT if h is None else h
        if h is None:
            continue
        try:
            v = float(y) + h
        except (TypeError, ValueError):
            continue
        ys[i] = v if v != 0.0 else 0.01
    return ys, masque


def replis(infos: dict) -> dict[int, float]:
    """y visé de repli de TOUTES les marionnettes en cours (espèce hors liste : ``HAUTEUR_DEFAUT``). Sert à rendre
    visables celles que le masque d'un Hanteur a mises à 0 (relecture adverse D1) : leur IA reste suspendue, rien
    d'autre ne réécrirait leurs drapeaux."""
    out: dict[int, float] = {}
    for i, (proc, y) in dict(infos).items():
        if not isinstance(i, int) or not 0 <= i < 16:
            continue
        try:
            v = float(y) + HAUTEUR_VISEE.get(proc, HAUTEUR_DEFAUT)
        except (TypeError, ValueError):
            continue
        out[i] = v if v != 0.0 else 0.01
    return out


class ViseeSync:
    """Écrit la règle dans le pack : octets de visée des 7 emplacements de fantômes, y visé des 16 emplacements
    d'ennemis, masque et cfg de la zone ``wwmp_visee``."""

    def __init__(self, enabled: bool = True, misc: bool = False, hors_mode: bool = False):
        self.enabled = bool(enabled)
        self.misc = bool(misc)
        self.hors_mode = bool(hors_mode)        # --visee-marionnettes
        self.cfg = 0                            # dernier cfg écrit
        self.supported = None
        self.nettoye = False
        self.mb_id = None
        self.f_ecrits: dict[int, tuple[int, int]] = {}
        self.e_ecrits: dict[int, float | None] = {}
        self.masque = 0
        self.t_plein = None
        self.t_off = None                       # début de l'extinction des fantômes
        self.t_off_m = None                     # début de l'extinction des marionnettes
        self.anciens: set[int] = set()          # emplacements d'ennemis masqués un jour (drapeaux 0 posés)
        self.n_vises = 0                        # emplacements de fantômes visables au dernier tick
        self.n_marionnettes = 0
        self.note_pack = False
        self.notes: list[str] = []

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def _reprendre(self, mb) -> None:
        """Nouvelle boîte : état laissé par une session tuée net (relecture adverse D2). Le pack n'a pas
        d'échéance : un cfg non nul resterait appliqué pour toujours. On le reprend tel quel pour que l'extinction
        normale (emplacements à 0 recopiés pendant ``EXTINCTION``, marionnettes masquées rendues) passe avant la
        coupure."""
        try:
            c = mb.visee_counters()
            cfg, masque = int(c.get("cfg", 0)) & 3, int(c.get("masque", 0)) & 0xFFFF
        except Exception:
            cfg, masque = 0, 0
        self.cfg, self.masque = cfg, masque
        if cfg & 2:
            self.anciens |= {i for i in range(16) if masque >> i & 1}
        if not cfg and not self.enabled:
            self.nettoye = True                 # désactivée, zone éteinte : rien ne change, rien n'est écrit

    def step(self, mb, now: float, fantomes: dict[int, tuple[int, int]] | None,
             ys: tuple[dict[int, float], int] | None, n_slots: int = 7, occupants: dict | None = None,
             repli: dict[int, float] | None = None) -> None:
        """``fantomes`` : {emplacement: (drapeaux, ligne)} ou None (hors mode : extinction puis cfg 0) ; ``ys`` :
        ({emplacement d'ennemi: y visé}, masque) ou None ; ``occupants`` : {emplacement de fantôme: acteur} (un
        emplacement réattribué est réécrit tout de suite) ; ``repli`` : ``replis()`` des marionnettes en cours
        (celles masquées un jour sont rendues visables quand le masque tombe). Désactivée (coop normale,
        --no-visee) : seulement l'extinction d'un état laissé par une session tuée net, puis plus rien."""
        if not self.enabled:
            fantomes, ys = None, None
        want = fantomes is not None or ys is not None
        ok = bool(mb is not None and getattr(mb, "visee", False))
        if mb is not None:
            self.supported = ok
        if not ok:
            if want and mb is not None and not self.note_pack:
                self.note_pack = True
                self.notes.append(NOTE_PACK)
            self.cfg = 0
            return
        if not want:
            self.note_pack = False
        if self.mb_id != id(mb):
            self.mb_id, self.nettoye = id(mb), False
            self.f_ecrits, self.e_ecrits, self.t_plein = {}, {}, None
            self.t_off = self.t_off_m = None
            self.anciens = set()
            self._reprendre(mb)
        if not want and self.cfg == 0 and self.nettoye:
            self.n_vises = self.n_marionnettes = 0
            return                              # hors mode Hanteurs, zone déjà à 0 : plus rien n'est écrit
        plein =(self.t_plein is None or now - self.t_plein >= RAFRAICHIR or now < self.t_plein
                 or not self.nettoye)
        if plein:
            self.t_plein = now
            self.f_ecrits, self.e_ecrits = {}, {}
        # fantômes : effacés au même tick (un emplacement absent de la règle reçoit 0) ; le cache suit l'occupant
        # (relecture adverse D3 : Mailbox.clear_slot remet les octets à 0 quand un fantôme quitte l'emplacement)
        f = dict(fantomes or {})
        occ = dict(occupants or {})
        n = 0
        for i in range(n_slots):
            v = f.get(i, (0, 0))
            k = (occ.get(i), v)
            if self.f_ecrits.get(i) != k:
                mb.set_slot_visee(i, *v)
                self.f_ecrits[i] = k
            n += v[0] != 0
        self.n_vises = n
        # marionnettes. Relecture adverse D1 : celles qu'un masque de Hanteur a mises à 0 gardent ce 0 (IA
        # suspendue) ; quand le masque tombe (rotation, Crawl, fin du mode), elles reçoivent le y de repli et le pack
        # repose 4 si PV > 0 (pendant EXTINCTION si le mode s'arrête), comme avant ce lot.
        ye, masque = (dict(ys[0]), int(ys[1])) if ys is not None else ({}, 0)
        if repli is not None:
            self.anciens &= set(repli)          # emplacement rendu : oublié
        self.anciens |= {i for i in range(16) if masque >> i & 1}
        cfg_m = ys is not None
        if ys is not None:
            self.t_off_m = None
        elif self.anciens and (self.cfg & 2):
            if self.t_off_m is None:
                self.t_off_m = now
            if 0 <= now - self.t_off_m < EXTINCTION:
                cfg_m = True
            else:
                self.anciens = set()
        if cfg_m:
            for i in self.anciens:
                if not masque >> i & 1 and i not in ye and repli is not None and i in repli:
                    ye[i] = repli[i]
        for i in range(16):
            v = ye.get(i)
            if self.e_ecrits.get(i, "x") != v:
                mb.set_enemy_visee(i, v)
                self.e_ecrits[i] = v
        self.n_marionnettes = len(ye)
        cfg = 2 if cfg_m else 0
        if fantomes is not None:
            cfg |= 1
            self.t_off = None
        elif self.cfg & 1:
            # fin du mode : emplacements déjà à 0 ; le pack doit les recopier quelques images avant la coupure
            if self.t_off is None:
                self.t_off = now
            if 0 <= now - self.t_off < EXTINCTION:
                cfg |= 1
        if cfg != self.cfg or masque != self.masque or plein:
            mb.set_visee_cfg(cfg, masque)
            self.cfg, self.masque = cfg, masque
        self.nettoye = True

    def stop(self, mb) -> None:
        """Arrêt du client : emplacements à 0 puis cfg à 0 (fantômes et marionnettes sont rendus juste après)."""
        try:
            if mb is not None and getattr(mb, "visee", False):
                for i in range(int(getattr(mb, "n_slots", 7) or 7)):
                    mb.set_slot_visee(i, 0, 0)
                for i in range(16):
                    mb.set_enemy_visee(i, None)
                mb.set_visee_cfg(0, 0)
        finally:
            self.cfg, self.masque = 0, 0
            self.f_ecrits, self.e_ecrits = {}, {}
            self.anciens, self.t_off_m = set(), None

    def status(self) -> str:
        if self.supported is None:
            return "en attente du jeu"
        if not self.supported:
            return "pack sans la capacité (bit 20) : rien ne change"
        if not self.cfg:
            return "coupée (hors mode Hanteurs)"
        return f"{self.n_vises} fantôme(s) visable(s), {self.n_marionnettes} marionnette(s) suivie(s)"
