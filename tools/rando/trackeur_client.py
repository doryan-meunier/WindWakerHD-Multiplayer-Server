"""Trackeur du randomizer, côté client de jeu : complément LU dans le jeu (docs/etudes/trackeur_randomizer.md §8).

Lecture seule : ce module n'appelle que ``mem.try_read`` et la lecture du journal des dons du pack (``trk_lire``) ;
il n'écrit jamais dans la mémoire du jeu ni dans la boîte aux lettres, et marche donc avec n'importe quel pack (v17
compris) comme sans pack.

Ce qui est envoyé au serveur (message « trk ») :

* la VIE MAXIMALE (gi+0x20, u16, quarts de cœur ; [HD-ASM], déjà lue par synchro/cadeaux.py et synchro/deathlink.py) : le
  serveur en tire réceptacles et quarts de cœur du trackeur ;
* les LIEUX REÇUS (champ « l », seulement si le welcome annonce « trk_l ») : lettres du courrier et cadeaux de
  personnages vus DANS CE JEU au moment où le jeu les donne. Le serveur les coche « fait » dans le trackeur.

Lettres (12 lieux « donnée » du courrier, rando.SITES_DONNEES) : chaque entrée de la table du courrier du jeu
(0x10031508, 12 octets : +4 objet, +6 registre d'événement u16) porte un registre de 2 bits de gi+0x644. L'acteur du
facteur (Tpost) ne le met à 3 (« reçue ») qu'à la FIN de l'événement de remise, après la démo de l'objet
[HD-ASM 0x023A50CC-0x023A510C : `endCheckOld("DEFAULT_POST")` puis 0x02586E04 = setEventReg(gi+0x644, reg, 3) ;
0x023A510C est le SEUL appelant de 0x02586E04]. Ces octets (0x7A à 0xB5) sont hors de la synchro de la progression
(``ev:0x00-0x5F``) : seul le jeu les écrit. Le client signale une lettre quand son registre PASSE à 3, vu deux
lectures de suite, dans le même contexte (même sauvegarde chargée, Link présent) ; la première lecture d'un contexte
est une BASE silencieuse ; plusieurs lettres passées à 3 d'un coup, ou un registre qui baisse : sauvegarde changée,
nouvelle base sans rien signaler. JAMAIS ajoutés à la synchro : l'union OU d'un état 1 et d'un état 2 ferait un 3.
PREUVE EN PLUS (journal du pack, ci-dessous) : un passage à 3 ne compte que si le journal a noté, dans le même
contexte et depuis moins de PREUVE_MAX secondes, le don d'une lettre par le facteur (appel 0x023A4744 de
createItemForPresentDemo, retour 0x023A4748 : coupure PRESENT, AVANT la fin de l'événement). Une copie de sauvegarde
écrite dans le jeu pendant que le client tourne (« rejoindre », fortement déconseillé) ne coche donc rien. Sans le
bit 18 du pack, aucune lettre n'est signalée.

Cadeaux (pack v19 avec le bit 18 des capacités, zone « WWMPTRK1 » format 2) : le stub N2 du pack note chaque appel
de fopAcM_createItemForPresentDemo dans un petit journal (retour de l'appelant, objet d'origine, r11 de l'appelant,
numéro, objet FINAL que le jeu donne), compteur écrit en dernier. Le client reconnaît un lieu par le même couple (retour, objet d'origine) que le
stub N2 (rando.SITES_N2), et Sam, Linda et Anton, Kreeb (même appel 0x022C1038 que les trois photos) par r11 =
ItemNo × 4 [HD-ASM 0x022C1020 ; table d'objets 0x101C3998 : 0 Sam, 1 Pompie et Vera, 2 Minenco, 3 Linda, 4 Kreeb,
5 Kamo]. Première lecture (ou compteur qui baisse : Cemu relancé) : base silencieuse.

OBJET VÉRIFIÉ (relecture adverse) : un lieu (lettre comprise : objet noté par la preuve du facteur) n'est signalé que
si l'objet FINAL noté par le pack est celui que la table de la partie met à ce lieu (``attendu``, d'après la table
connue du client au moment de la lecture). Un don fait AVANT que la table soit chargée (client lancé en cours de
partie, octet « donnée » pas encore écrit pendant un dialogue, table N2 pas encore posée) donne l'objet d'origine :
le lieu n'est PAS signalé (``n_autre_objet``, à cocher à la main). Table inconnue (pas encore reçue) : rien n'est
signalé.

Mode Hanteurs : un lieu vu pendant que le monde est fermé à ce joueur (Hanteur, possédé, quarantaine) est JETÉ au
moment où il est vu, jamais mis en file : il ne part pas plus tard, au retour en Link.

Le message « trk » ne part que si le serveur l'a demandé (« trk » dans son welcome : serveur récent, partie avec
randomizer, trackeur non coupé), au plus une fois toutes les PERIODE secondes.
"""
from __future__ import annotations

import struct
import time

from rando import noyau as rnd

GI_MAX_LIFE = 0x20          # u16 mMaxLife (quarts de cœur)
VIE_MIN, VIE_MAX = 12, 80   # 3 à 20 cœurs : toute autre valeur est une lecture aberrante (ignorée)
PERIODE = 2.0               # s entre deux envois au plus
LIEUX_FILE_MAX = 64         # lieux en attente d'envoi au plus

# --- lettres -------------------------------------------------------------------------------------------------
GI_EVENT = 0x644            # dSv_event_c (u8[0x100]) [RANDO + LIVE]
# site de rando.SITES_DONNEES -> registre d'événement u16 (octet << 8 | masque), relu au champ +6 de l'entrée de la
# table du courrier (octet d'objet + 2) par tests/test_trackeur_auto.py
LETTRES = {0: 0xAC03, 1: 0xB503, 2: 0x7B03, 3: 0x9D03, 4: 0x8B03, 5: 0x7C03,
           19: 0x7D03, 20: 0x7A03, 21: 0xB203, 22: 0xB003, 23: 0xAE03, 24: 0xAF03}
LETTRE_RECUE = 3
OCTET_MIN = min(r >> 8 for r in LETTRES.values())
OCTET_FIN = max(r >> 8 for r in LETTRES.values()) + 1
LECTURES = 2                # lectures de suite à 3 avant de signaler

# --- journal des dons (pack, bit 18) -------------------------------------------------------------------------
RETOUR_PEOPLE = 0x022C103C  # d_a_npc_people : appel 0x022C1038 (objet = table 0x101C3998[ItemNo])
RETOUR_ENCHERES = 0x02059AF0
RETOUR_FACTEUR = 0x023A4748  # Tpost, coupure PRESENT : appel 0x023A4744 (objet = table du courrier [index courant])
PREUVE = ("facteur", -1)     # entrée du journal : une lettre vient d'être donnée (preuve, jamais un lieu)
PREUVE_MAX = 300.0           # s entre le don (PRESENT) et le passage du registre à 3 (fin de l'événement), au plus
PEOPLE = {0: ("don", 12), 1: ("n2", 5), 2: ("n2", 6), 3: ("don", 13), 4: ("don", 14), 5: ("n2", 7)}
# (retour, objet d'origine) -> lieu, pour les cadeaux N2 hors d_a_npc_people et hors enchères (déjà cochées par leur
# bit d'événement partagé, wwmp/rando_lot9.SIGNAUX)
N2_PAR_APPEL = {(lr, o): ("n2", k) for k, (lr, o, _nom) in enumerate(rnd.SITES_N2)
                if lr not in (RETOUR_PEOPLE, RETOUR_ENCHERES)}
JOURNAL_N = 8
# lieux que ce module peut signaler (le serveur accepte exactement ceux-là)
RECUS = {"don": frozenset(LETTRES) | frozenset(s for g, s in PEOPLE.values() if g == "don"),
         "n2": frozenset(s for g, s in N2_PAR_APPEL.values()) | frozenset(s for g, s in PEOPLE.values() if g == "n2")}


def lire_vie(mem) -> int | None:
    """Vie maximale en quarts de cœur, ou None (pas de partie chargée, lecture impossible ou aberrante)."""
    from jeu import game
    gi = game.gameinfo_ptr(mem)
    if gi is None:
        return None
    raw = mem.try_read(gi + GI_MAX_LIFE, 2)
    if raw is None or len(raw) != 2:
        return None
    vie = struct.unpack(">H", raw)[0]
    return vie if VIE_MIN <= vie <= VIE_MAX else None


def lire_courrier(mem) -> tuple[int, dict] | None:
    """(gi, {site: état 0 à 3}) des 12 lettres, ou None (pas de partie, lecture impossible)."""
    from jeu import game
    gi = game.gameinfo_ptr(mem)
    if gi is None:
        return None
    raw = mem.try_read(gi + GI_EVENT + OCTET_MIN, OCTET_FIN - OCTET_MIN)
    if raw is None or len(raw) != OCTET_FIN - OCTET_MIN:
        return None
    out = {}
    for site, reg in LETTRES.items():
        masque = reg & 0xFF
        decalage = (masque & -masque).bit_length() - 1
        out[site] = (raw[(reg >> 8) - OCTET_MIN] & masque) >> decalage
    return gi, out


def attendu(table, lieu) -> int | None:
    """Objet que la table de la partie (``RandoClient.voulu``) met au lieu (genre, site), ou None si on ne peut pas
    le savoir (table inconnue, entrée incohérente) : alors rien n'est signalé."""
    if not isinstance(table, rnd.TABLES) or not isinstance(lieu, tuple) or len(lieu) != 2:
        return None
    genre, site = lieu
    if genre == "don" and site in RECUS["don"]:
        origine, entrees = rnd.SITES_DONNEES[site][2], getattr(table, "entrees_don", None)
    elif genre == "n2" and site in RECUS["n2"]:
        origine, entrees = rnd.SITES_N2[site][1], getattr(table, "entrees_n2", None)
    else:
        return None
    if not isinstance(entrees, dict) or site not in entrees:
        return origine                      # lieu non mélangé dans cette partie : l'objet d'origine est le bon
    o, n = entrees[site]
    # entrée qui ne part pas de l'objet du jeu : ni le client (rando_donnees.voulu) ni le stub N2 ne l'appliquent
    return n if o == origine else None


def identifier(retour: int, objet: int, r11: int):
    """Lieu (genre, site) d'une entrée du journal des dons, PREUVE (don d'une lettre par le facteur), ou None (appel
    qui n'est pas un lieu suivi)."""
    if retour == RETOUR_FACTEUR:
        return PREUVE
    if retour == RETOUR_PEOPLE:
        if r11 & 3 or not 0 <= r11 >> 2 < len(PEOPLE):
            return None
        lieu = PEOPLE[r11 >> 2]
        if lieu[0] == "n2" and objet != rnd.SITES_N2[lieu[1]][1]:
            return None                     # photo : même objet d'origine que le stub N2 (table jamais changée)
        return lieu
    return N2_PAR_APPEL.get((retour, objet))


class Courrier:
    """Passages à 3 des registres des lettres (voir le docstring du module)."""

    def __init__(self, lire=lire_courrier):
        self._lire = lire
        self.base: dict | None = None
        self.ctx = None
        self.cand: dict = {}
        self.rebases = 0
        self.bases = 0              # bases posées (le complément oublie alors ses preuves)

    def reset(self) -> None:
        self.base, self.cand = None, {}

    def step(self, mem, en_jeu: bool, reloads: int = 0) -> list:
        if not en_jeu or mem is None:
            self.reset()
            return []
        lu = self._lire(mem)
        if lu is None:
            self.reset()
            return []
        gi, vals = lu
        ctx = (gi, reloads)
        if ctx != self.ctx:
            self.ctx = ctx
            self.reset()
        if self.base is None:
            self.base = dict(vals)
            self.bases += 1
            return []
        base = self.base
        if any(vals.get(s, 0) < base.get(s, 0) for s in LETTRES):
            # un registre qui baisse : autre sauvegarde (ou relue) : nouvelle base, rien de signalé
            self.base, self.cand = dict(vals), {}
            self.rebases += 1
            self.bases += 1
            return []
        hausse = [s for s in LETTRES if vals.get(s) == LETTRE_RECUE and base.get(s) != LETTRE_RECUE]
        if len(hausse) > 1:
            # plusieurs lettres reçues d'un coup : impossible en jeu (une remise = un événement) : nouvelle base
            self.base, self.cand = dict(vals), {}
            self.rebases += 1
            self.bases += 1
            return []
        for s in LETTRES:
            if s not in hausse:
                base[s] = vals.get(s, base.get(s))
        self.cand = {s: self.cand.get(s, 0) + 1 for s in hausse}
        out = []
        for s, n in list(self.cand.items()):
            if n >= LECTURES:
                base[s] = LETTRE_RECUE
                del self.cand[s]
                out.append(("don", s))
        return out


class Journal:
    """Nouvelles entrées du journal des dons du pack (bit 18), reconnues comme lieux : [(lieu ou PREUVE, objet
    final)]."""

    def __init__(self):
        self.vu: int | None = None

    def reset(self) -> None:
        self.vu = None

    def step(self, boite) -> list:
        lu = boite.trk_lire() if boite is not None and hasattr(boite, "trk_lire") else None
        if lu is None:
            self.reset()
            return []
        n, entrees = lu
        if self.vu is None or n < self.vu:
            self.vu = n                     # base (ou Cemu relancé : compteur reparti de zéro)
            return []
        out = []
        for num in range(max(self.vu, n - JOURNAL_N), n):
            retour, objet, r11, numero, final = entrees[num % JOURNAL_N]
            if numero != num:
                continue                    # entrée déjà réécrite (plus de 8 dons entre deux lectures)
            lieu = identifier(retour, objet, r11)
            if lieu is not None:
                out.append((lieu, final))
        self.vu = n
        return out


class Complement:
    """État du complément : ``step`` (thread de travail, lecture) puis ``take_outgoing`` (boucle réseau)."""

    def __init__(self, enabled: bool = True, lire=lire_vie, lire_lettres=lire_courrier):
        self.enabled = enabled
        self.serveur = False        # le serveur tient un trackeur (welcome)
        self.serveur_lieux = False  # ... et accepte les lieux reçus (« trk_l »)
        self.vie: int | None = None
        self.envoyee: int | None = None
        self.t_envoi = 0.0
        self._lire = lire
        self.courrier = Courrier(lire_lettres)
        self.journal = Journal()
        self.lieux: list = []       # lieux à envoyer [(genre, site)]
        self.n_lieux = 0            # lieux envoyés
        self.n_jetes = 0            # lieux vus pendant que le monde était fermé (Hanteurs) : jetés
        self.n_sans_preuve = 0      # lettres passées à 3 sans don du facteur au journal : jamais signalées
        self.n_autre_objet = 0      # lieux reçus avec un autre objet que celui de la table (ou table inconnue)
        self.preuves: list = []     # (heure, objet final) des dons de lettres vus au journal, pas encore appariés
        self._bases = 0

    def _oublier(self) -> None:
        self.courrier.reset()
        self.courrier.ctx = None
        self.journal.reset()
        self.lieux = []
        self.preuves = []

    def on_welcome(self, msg: dict) -> None:
        self.serveur = msg.get("trk") in (1, True)
        self.serveur_lieux = self.serveur and msg.get("trk_l") in (1, True)
        self.envoyee = None         # nouvelle connexion : le serveur a pu redémarrer, on redit la valeur
        self._oublier()             # nouvelle base : rien de ce qui s'est passé hors connexion n'est signalé

    def reset_network(self) -> None:
        self.serveur = False
        self.serveur_lieux = False
        self.envoyee = None
        self._oublier()

    def step(self, mem, en_jeu: bool, boite=None, monde_ok: bool = True, reloads: int = 0,
             now: float | None = None, table=None) -> None:
        """Lit la vie maximale (Link présent seulement : à l'écran titre la partie n'est pas chargée), puis, si le
        serveur les accepte, le journal des dons et les lettres reçues. ``monde_ok`` : faux pour un Hanteur, un
        possédé ou une quarantaine (ce qui est vu alors est jeté). ``table`` : table de la partie connue du client
        (``RandoClient.voulu``) ; un lieu n'est signalé que si l'objet donné est celui qu'elle y met."""
        if not self.enabled or not self.serveur or mem is None:
            return
        if en_jeu:
            vie = self._lire(mem)
            if vie is not None:
                self.vie = vie
        if not self.serveur_lieux:
            return
        now = time.monotonic() if now is None else now
        vus = []
        for lieu, final in self.journal.step(boite):
            if lieu == PREUVE:
                self.preuves.append((now, final))
            elif attendu(table, lieu) == final:
                vus.append(lieu)
            else:
                self.n_autre_objet += 1     # objet d'origine (table pas encore chargée) ou autre : jamais signalé
        lettres = self.courrier.step(mem, en_jeu, reloads)
        if self.courrier.bases != self._bases:
            self._bases = self.courrier.bases
            self.preuves = []           # nouvelle base des lettres : les dons vus avant ne prouvent plus rien
        self.preuves = [p for p in self.preuves if now - p[0] <= PREUVE_MAX][-4:]
        for lieu in lettres:
            if not self.preuves:
                self.n_sans_preuve += 1
                continue
            _t, final = self.preuves.pop(0)     # la remise la plus ancienne non appariée : celle de cette lettre
            if attendu(table, lieu) == final:
                vus.append(lieu)
            else:
                self.n_autre_objet += 1
        for lieu in vus:
            if not monde_ok:
                self.n_jetes += 1
            elif lieu not in self.lieux and len(self.lieux) < LIEUX_FILE_MAX:
                self.lieux.append(lieu)

    def take_outgoing(self, now: float) -> list[dict]:
        if not self.enabled or not self.serveur:
            return []
        vie = self.vie is not None and self.vie != self.envoyee
        if not vie and not self.lieux:
            return []
        if now - self.t_envoi < PERIODE:
            return []
        msg: dict = {"type": "trk"}
        if self.vie is not None:
            msg["vie"] = self.vie
        lieux, self.lieux = self.lieux, []      # échange d'abord : le thread de travail ajoute à la nouvelle liste
        if lieux and self.serveur_lieux:
            msg["l"] = [list(x) for x in lieux]
            self.n_lieux += len(lieux)
        if len(msg) == 1:
            return []
        self.t_envoi, self.envoyee = now, self.vie
        return [msg]

    def status(self) -> str:
        if not self.enabled:
            return "désactivé (--no-trackeur)"
        if not self.serveur:
            return "pas de trackeur sur ce serveur"
        texte = "vie maximale partagée" if self.envoyee is not None else "en attente du jeu"
        if self.serveur_lieux:
            texte += f" ; lettres et cadeaux reçus suivis ({self.n_lieux} signalé(s)"
            if self.n_jetes:
                texte += f", {self.n_jetes} jeté(s) pendant le mode Hanteur"
            if self.n_sans_preuve:
                texte += f", {self.n_sans_preuve} lettre(s) sans don du facteur au journal du pack : à cocher à la main"
            if self.n_autre_objet:
                texte += (f", {self.n_autre_objet} reçu(s) avec un autre objet que celui de la graine (table pas "
                          "encore chargée) : à cocher à la main")
            texte += ")"
        return texte
