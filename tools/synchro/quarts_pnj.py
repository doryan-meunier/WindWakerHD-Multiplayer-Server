"""Quarts de cœur donnés par un personnage ou un mini-jeu, partagés (--share-pnj-hearts, activé par --coop-donjon,
--no-pnj-hearts ; jusqu'à 8 joueurs).

Les quarts des coffres (synchro/coffres.py) et ceux posés au sol (synchro/cadeaux.py) sont déjà partagés. Ceux que
donne un personnage (Ivan, Kreeb, Sam, Linda et Anton, Goron, arbres flétris, concours de l'Envol, Beedle, enchères,
lettres du courrier) ne l'étaient pas : le bit « déjà donné » arrivait chez les autres par la progression (octets
``ev:0x00-0x5F``), qui ne redonnaient donc rien, et le quart restait à celui qui l'avait reçu (vie max à chacun).

Principe (même contrat que les coffres et les quarts au sol : le DRAPEAU est la preuve, il voyage AVEC l'objet) :

* Table ``LIEUX`` : chaque lieu a un drapeau de ``mSave.mEvent`` (gi+0x644), 0xXXYY comme onEventBit, RELU dans le
  cking.rpx USA v0 (``preuve`` : l'``onEventBit`` du jeu qui le pose, ou la table des enchères), et l'objet d'origine.
  Adresses des lieux reprises des tables du randomizer (rando/noyau.py ``SITES_DONNEES``, ``SITES_N2``, lot 6) ; bits
  des lieux donnés par TWWHD-Randomizer et la logique Archipelago, recalculés en 0xXXYY et revérifiés dans le RPX HD
  (tests/test_quarts_pnj.py, avec le dump). Lettres : registre de 2 bits mis à 3 en fin de remise (trackeur_client).
* La progression générique ne partage plus ces drapeaux (``regles_refusees``) : ce module les porte.
* Émetteur (``PnjWatch``) : un drapeau passé à 1 chez nous (deux lectures de suite, même partie, pas posé par nous),
  le CONTENU du lieu (``contenu`` : objet d'origine, ou celui de la table du randomizer active) et une PREUVE du quart
  (statistique HD des quarts gi+0x1928 ou vie max, attente comprise, qui monte dans la fenêtre du don) :
    - contenu = quart de cœur (0x07, 0x3F) ET preuve -> ``gift {src: "pnj:<code>", lieu, quart: 1}`` ;
    - autre contenu (le randomizer y a mis autre chose) -> ``quart: 0`` (drapeau seul : l'objet passe par
      l'inventaire ou les compteurs, comme avant) ; JAMAIS parti comme un quart ;
    - quart sans preuve après ``PREUVE_APRES`` s -> ``quart: 0`` (rien n'est donné deux fois, quitte à en perdre un) ;
    - lettre (registre hors de la progression) : seulement un quart prouvé ; sinon rien (comme avant).
  Plusieurs drapeaux d'un coup, ou un drapeau qui retombe : sauvegarde chargée, nouvelle base sans rien envoyer.
* Serveur : canal « gift » des quarts au sol (premier arrivé par ``src``, relais à tout le groupe, registre renvoyé
  à l'inscription, gardé dans le monde persistant, vidé par un rejoindre). ``valid_pnj`` valide.
* Receveur (``PnjApply``) : idempotent par le drapeau (déjà posé = déjà reçu ou fait soi-même). Jeu libre. Quart :
  mod natif 0.24.0 (bit 31) -> CALL 8 GRANT genre 4 : drapeau PUIS ``execItemGet(7)`` dans la même image ; sinon
  (pack Cemu, mod plus ancien) repli Python : compteur en attente += 1 et statistique (comme cadeaux.py), PUIS le
  drapeau. Drapeau seul : GRANT genre 4 sans objet, ou écriture du drapeau. Contrat « propriétaire unique » : si
  l'inventaire compte la vie max, drapeau seul.

Mini-jeux à compteur de prix (genre ``cpt``) : le jeu ne pose pas de bit, il compte les prix gagnés dans un registre
(valeur brute dans les bits du masque, getEventReg HD sans décalage 0x025B8BB0) et donne ``table[compteur]`` APRÈS
l'avoir augmenté ; compteur >= 1 = premier prix (le quart) déjà donné. « Fait » : registre non nul ; le receveur ne
pose que le bit de poids faible (``pose`` : 0 -> 1, jamais 3 ; GRANT genre 4 inchangé, il OU le masque passé) et
seulement si le registre est à 0 (relu juste avant). Hors de la progression (octets >= 0x60) : comme une lettre, seul
un quart prouvé part.
  - tir aux tonneaux (Kg2, Île des Binocles) : registre 0xB703, seul écrivain 0x0226A73C setEventReg (fin de
    KG2_CLEAR_DEMO, plafond 3), puis KG2_GETDEMO -> evn_createItem_init (0x02269004) donne 0x1001C194[reg] =
    07 07 D7 (reg > 2 : 0x06) ; TWWHD met le premier prix aux octets 0 ET 1 de cette table ;
  - Bataille navale (Kg1, Mercantîle) : registre 0xFE07, seul écrivain 0x02267884 setEventReg (message de victoire,
    plafond 3), puis wait_action (0x022671FC) donne {07, CC, 05}[reg - 1].
Orca (500 coups) : bit 0x0F10, seul ``onEventBit`` à 0x02258738 juste avant le quart (createItem de Ji1) ; le
même bit ferme la récompense des coups (battleGuardCheck 0x0225AA0C).

Non couverts : Maggie (livraison de la lettre du Moblin) : le jeu ne garde AUCUN drapeau ni registre de la remise
(Kp1 n'appelle ni onEventBit ni un setEventReg utile ; getMsg 0x0227D1E0-0x0227D200 déduit « livrée » de la lettre
0x9B obtenue une fois, bit 15 de mGetBagItem, et absente du sac) : marquer « fait » chez les autres voudrait dire
retirer une lettre qu'ils n'ont peut-être pas encore, pas propre. Photos de Lenzo : aucun quart dans la version HD
(Lenzo et les photos donnent boîte à images, pendentif, cartes : déjà des cadeaux N2). Le reste des 44 quarts :
coffres, sol, trésors engloutis (coffres), Gros Octo.
"""
from __future__ import annotations

import struct
import threading
import time
from dataclasses import dataclass

from jeu import progress as prog
from synchro.cadeaux import give_piece_fallback, max_life
from synchro.obstacles import game_free

QUARTS = frozenset({0x07, 0x3F})       # « Piece of Heart » et « Piece of Heart 2 » (rando/logique/objets.json)
ITEM_PIECE = 0x07
ITEM_NONE = 0xFF
GI_PIECE_STAT = 0x1928                 # u32 statistique HD des quarts (synchro/cadeaux.py)
SRC_PREFIXE = "pnj:"


@dataclass(frozen=True)
class Lieu:
    code: str            # identifiant court, stable (message, monde persistant)
    nom: str             # nom de la logique (rando/logique/lieux.json)
    drapeau: int         # 0xXXYY de mSave.mEvent : octet XX, masque YY (tous les bits du masque = fait)
    genre: str           # "bit" (octet partagé par la progression : relayé toujours), "reg" (lettre : quart seul) ou
                         # "cpt" (compteur de prix d'un mini-jeu : fait = non nul ; quart seul)
    origine: int         # objet du jeu d'origine
    preuve: str          # où le jeu pose le drapeau (cking.rpx USA v0)

    @property
    def octet(self) -> int:
        return self.drapeau >> 8

    @property
    def masque(self) -> int:
        return self.drapeau & 0xFF

    @property
    def pose(self) -> int:
        """Bits posés chez le receveur : le masque entier, sauf compteur : son bit de poids faible (0 -> 1)."""
        m = self.masque
        return m & -m if self.genre == "cpt" else m

    @property
    def drapeau_pose(self) -> int:
        """0xXXYY passé au GRANT genre 4 (le mod OU ces bits ; « déjà posé » quand ils le sont tous)."""
        return self.octet << 8 | self.pose

    def fait(self, octet: int) -> bool:
        """Le lieu est fait d'après la valeur de son octet de mSave.mEvent."""
        if self.genre == "cpt":
            return (octet & self.masque) != 0
        return (octet & self.masque) == self.masque

    @property
    def src(self) -> str:
        return SRC_PREFIXE + self.code


_ON = "onEventBit"
LIEUX = (
    Lieu("ivan", "Windfall Island - Ivan Catch Killer Bees", 0x1340, "bit", 0x07, f"0x02298C38 {_ON}"),
    Lieu("kreeb", "Windfall Island - Kreeb Light the Lighthouse", 0x1B20, "bit", 0x07, f"0x022C7860 {_ON}"),
    Lieu("sam", "Windfall Island - Sam Decorate Island", 0x1B10, "bit", 0x07, f"0x022C7130 {_ON}"),
    Lieu("linda", "Windfall Island - Linda and Anton", 0x2280, "bit", 0x07, f"0x022C7718 {_ON}"),
    Lieu("envol", "Flight Control Platform - First Prize", 0x2B40, "bit", 0x07, f"0x02206228 {_ON}"),
    Lieu("goron", "Great Sea - Goron Trading Reward", 0x3E04, "bit", 0x07, f"0x022D6418 {_ON}"),
    Lieu("arbres", "Great Sea - Withered Trees", 0x2E20, "bit", 0x07, f"0x0217EFF4 {_ON}"),
    Lieu("beedle950", "Rock Spire Isle - Beedle 950 Rupee Item", 0x2010, "bit", 0x07, f"0x02215618 {_ON}"),
    Lieu("beedle500", "Rock Spire Isle - Beedle 500 Rupee Item", 0x2020, "bit", 0x50, f"0x02215604 {_ON}"),
    Lieu("enchere80", "Windfall Island - Auction 80 Rupee", 0x1020, "bit", 0x07, "table des enchères 0x1019016C"),
    Lieu("enchere5", "Windfall Island - Auction 5 Rupee", 0x1080, "bit", 0xE4, "table des enchères 0x1019016C"),
    Lieu("enchere40", "Windfall Island - Auction 40 Rupee", 0x0F01, "bit", 0x1F, "table des enchères 0x1019016C"),
    Lieu("enchere60", "Windfall Island - Auction 60 Rupee", 0x1040, "bit", 0xED, "table des enchères 0x1019016C"),
    Lieu("orca", "Outset Island - Orca Hit 500 Times", 0x0F10, "bit", 0x07, f"0x02258738 {_ON}"),
    # compteurs de prix (registre augmenté AVANT le prix ; preuve : son seul setEventReg, tests/test_quarts_pnj_rpx.py)
    Lieu("tonneaux", "Spectacle Island - Barrel Shooting First Prize", 0xB703, "cpt", 0x07, "0x0226A73C setEventReg"),
    Lieu("calmar", "Windfall Island - Battle Squid First Prize", 0xFE07, "cpt", 0x07, "0x02267884 setEventReg"),
    # lettres : registre de 2 bits mis à 3 en fin de remise (0x023A510C setEventReg, rando/trackeur_client.py) ; site
    # du courrier = index de rando.SITES_DONNEES (même clé que trackeur_client.LETTRES)
    Lieu("lettre0", "Mailbox - Letter from Baito's Mother", 0xAC03, "reg", 0x07, "0x023A510C setEventReg"),
    Lieu("lettre1", "Mailbox - Letter from Komali's Father", 0xB503, "reg", 0x07, "0x023A510C setEventReg"),
    Lieu("lettre2", "Mailbox - Letter from Orca", 0x7B03, "reg", 0x04, "0x023A510C setEventReg"),
    Lieu("lettre3", "Mailbox - Letter from Grandma", 0x9D03, "reg", 0x04, "0x023A510C setEventReg"),
    Lieu("lettre4", "Mailbox - Letter from Aryll", 0x8B03, "reg", 0x04, "0x023A510C setEventReg"),
    Lieu("lettre5", "Mailbox - Letter from Baito", 0x7C03, "reg", 0x04, "0x023A510C setEventReg"),
    Lieu("lettre19", "Mailbox - Letter Advertising Bombs", 0x7D03, "reg", 0xC3, "0x023A510C setEventReg"),
    Lieu("lettre20", "Mailbox - Letter Advertising Rock Spire Shop Ship", 0x7A03, "reg", 0x01,
         "0x023A510C setEventReg"),
    Lieu("lettre21", "Mailbox - Letter from Tingle", 0xB203, "reg", 0xCB, "0x023A510C setEventReg"),
    Lieu("lettre22", "Mailbox - Beedle's Silver Membership Reward", 0xB003, "reg", 0x9D, "0x023A510C setEventReg"),
    Lieu("lettre23", "Mailbox - Letter from Hoskit's Girlfriend", 0xAE03, "reg", 0x3F, "0x023A510C setEventReg"),
    Lieu("lettre24", "Mailbox - Beedle's Gold Membership Reward", 0xAF03, "reg", 0x9E, "0x023A510C setEventReg"),
)
PAR_CODE = {l.code: l for l in LIEUX}
PAR_SRC = {l.src: l for l in LIEUX}


def regles_refusees() -> list[str]:
    """Règles ``deny`` de la progression générique : ces drapeaux passent par ce module (avec le quart)."""
    return [f"ev:0x{l.octet:02X}/0x{l.masque:02X}" for l in LIEUX]


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def valid_pnj(e) -> dict | None:
    """Enregistrement « gift » d'un quart de personnage validé (None sinon). Pas d'adresse dans le message : le
    receveur relit le drapeau dans SA table."""
    if not isinstance(e, dict):
        return None
    lieu = PAR_SRC.get(e.get("src")) if isinstance(e.get("src"), str) else None
    if lieu is None or e.get("lieu", lieu.code) != lieu.code:
        return None
    q = e.get("quart")
    if q not in (0, 1) or isinstance(q, bool):
        return None
    stage = e.get("stage", "")
    stage = stage[:8] if isinstance(stage, str) else ""
    room = e.get("room", -1)
    room = room if _int(room) and -1 <= room < 64 else -1
    return {"src": lieu.src, "lieu": lieu.code, "quart": q, "stage": stage, "room": room}


# ---------------------------------------------------------------------------
# Contenu d'un lieu (randomizer)
# ---------------------------------------------------------------------------
INCONNU = object()


def _index_sites():
    from rando import noyau as rnd
    don = {s[1]: (k, s[2]) for k, s in enumerate(rnd.SITES_DONNEES)}
    n2 = {s[2]: (k, s[1]) for k, s in enumerate(rnd.SITES_N2)}
    fich = {}
    try:
        from rando import lot6
        for k, s in enumerate(lot6.SITES):
            if isinstance(s, dict) and isinstance(s.get("nom"), str):
                fich[s["nom"]] = (k, s.get("id"))
    except Exception:                         # données du lot 6 absentes : ses lieux restent d'origine
        pass
    return don, n2, fich


_SITES = None


def contenu(table, lieu: Lieu):
    """Objet que donne ce lieu dans cette partie : ``lieu.origine`` sans randomizer (table None), celui de la table
    sinon (lieux « donnée », cadeaux N2, lot 6) ; None si on ne peut pas le savoir (table pas encore reçue : passer
    ``INCONNU`` ou ``rando.RandoClient.INCONNU``, ou entrée qui ne part pas de l'objet d'origine)."""
    global _SITES
    if table is None:
        return lieu.origine
    if not hasattr(table, "entrees"):           # INCONNU (ici ou celui de RandoClient) : pas encore de welcome
        return None
    if _SITES is None:
        _SITES = _index_sites()
    don, n2, fich = _SITES
    for idx, attr in ((don, "entrees_don"), (n2, "entrees_n2"), (fich, "entrees_fich")):
        site = idx.get(lieu.nom)
        if site is None:
            continue
        entrees = getattr(table, attr, None)
        if isinstance(entrees, dict) and site[0] in entrees:
            o, n = entrees[site[0]]
            return n if o == site[1] or o == lieu.origine else None
    return lieu.origine                         # lieu non mélangé dans cette partie


def est_quart(objet) -> bool:
    return objet in QUARTS


# ---------------------------------------------------------------------------
# Lectures
# ---------------------------------------------------------------------------
def lire_drapeaux(mem, gi: int) -> dict | None:
    """{code: fait} des lieux (tous les bits du masque posés), ou None (lecture impossible)."""
    raw = mem.try_read(gi + prog.GI_EVENT, prog.EVENT_SIZE)
    if raw is None or len(raw) != prog.EVENT_SIZE:
        return None
    return {l.code: l.fait(raw[l.octet]) for l in LIEUX}


def lire_preuve(mem, gi: int) -> tuple[int, int] | None:
    """(statistique des quarts, vie max attente comprise) ou None."""
    st = mem.try_read(gi + GI_PIECE_STAT, 4)
    vie = max_life(mem, gi)
    if st is None or vie is None:
        return None
    return struct.unpack(">I", st)[0], vie


def poser_drapeau(mem, gi: int, lieu: Lieu) -> bool:
    """Repli Python : les bits ``lieu.pose`` posés dans mSave.mEvent (OU, lecture-modification-écriture gardée ;
    compteur : 0 -> 1)."""
    return bool(prog._rmw(mem, gi + prog.GI_EVENT + lieu.octet, lieu.pose))


# ---------------------------------------------------------------------------
# Émetteur
# ---------------------------------------------------------------------------
class PnjWatch:
    LECTURES = 2            # lectures de suite à 1 avant de croire un drapeau
    SAUT_MAX = 3            # plus de drapeaux d'un coup : sauvegarde chargée (nouvelle base)
    PREUVE_AVANT = {"bit": 60.0, "reg": 300.0, "cpt": 60.0}   # s : le quart peut précéder le drapeau (lettre : fin
                                                               # de remise)
    PREUVE_APRES = {"bit": 30.0, "reg": 30.0, "cpt": 90.0}    # s : le quart peut suivre le drapeau (démo d'obtention ;
                                                               # compteur : augmenté au message de victoire, prix après)
    CONTENU_MAX = 300.0     # s à attendre la table du randomizer au plus
    HIST_MAX = 400.0

    def __init__(self):
        self.base: dict | None = None
        self.gi = None
        self.vus: dict[str, int] = {}          # code -> lectures de suite à 1 (pas encore cru)
        self.cands: dict[str, dict] = {}       # code -> {t, preuve0}
        self.hist: list[tuple[float, int, int]] = []   # (t, stat, vie)
        self.granted: set[str] = set()         # codes posés par PnjApply : anti-écho (partagé)
        self.sent: set[str] = set()
        self.notes: list[str] = []
        self.n_sent = self.n_bit = self.n_echo = self.n_jetes = self.n_bases = 0

    def nouvelle_base(self, flags: dict | None) -> None:
        self.base = dict(flags) if flags is not None else None
        self.vus.clear()
        self.cands.clear()
        self.n_bases += 1

    def _min_preuve(self, t0: float, avant: float):
        pts = [(s, v) for t, s, v in self.hist if t0 - avant <= t <= t0]
        if not pts:
            return None
        return min(p[0] for p in pts), min(p[1] for p in pts)

    def observe(self, now: float, ctx, flags: dict | None, preuve, table) -> list[dict]:
        """ctx : (gameInfo, rechargements vus par la progression) ou None (pas de partie) ; flags : ``lire_drapeaux`` ;
        preuve : ``lire_preuve`` ; table : table du randomizer connue (None sans randomizer, INCONNU avant le
        welcome). Renvoie les enregistrements (sans type ni groupe)."""
        if ctx is None or flags is None:
            self.base, self.gi = None, None
            self.vus.clear()
            self.cands.clear()
            return []
        recul = (preuve is not None and self.hist and (preuve[0] < self.hist[-1][1] or preuve[1] < self.hist[-1][2]))
        if recul:
            self.hist.clear()                 # statistique ou vie qui baisse : autre sauvegarde
        if preuve is not None:
            self.hist.append((now, preuve[0], preuve[1]))
            self.hist = [h for h in self.hist if now - h[0] <= self.HIST_MAX]
        if self.base is None or ctx != self.gi or recul:
            self.gi = ctx
            self.nouvelle_base(flags)
            return []
        tombes = [c for c, f in flags.items() if self.base.get(c) and not f]
        montes = [c for c, f in flags.items() if f and not self.base.get(c)]
        if tombes or len(montes) > self.SAUT_MAX:
            self.nouvelle_base(flags)          # autre sauvegarde : rien n'est envoyé
            return []
        for c in list(self.vus):
            if c not in montes:
                del self.vus[c]
        for c in montes:
            self.vus[c] = self.vus.get(c, 0) + 1
            if self.vus[c] < self.LECTURES:
                continue
            del self.vus[c]
            self.base[c] = True
            if c in self.granted:
                self.n_echo += 1
                continue
            if c in self.sent or c in self.cands:
                continue
            lieu = PAR_CODE[c]
            self.cands[c] = {"t": now, "p0": self._min_preuve(now, self.PREUVE_AVANT[lieu.genre])}
        out = []
        for c, cand in list(self.cands.items()):
            lieu = PAR_CODE[c]
            obj = contenu(table, lieu)
            if obj is None:
                if now - cand["t"] > self.CONTENU_MAX:
                    del self.cands[c]
                    self.n_jetes += 1
                    self.notes.append(f"{lieu.nom} : contenu inconnu (table du randomizer pas reçue), pas partagé")
                continue
            quart = None
            if est_quart(obj):
                p0, p = cand["p0"], preuve
                if p0 is not None and p is not None and (p[0] > p0[0] or p[1] > p0[1]):
                    quart = 1
                elif now - cand["t"] > self.PREUVE_APRES[lieu.genre]:
                    quart = 0
                    self.notes.append(f"{lieu.nom} : fait, mais aucun quart de cœur vu chez toi : lieu marqué fait "
                                      "chez les autres, rien ne leur est donné")
                else:
                    continue                  # le quart peut encore arriver (démo d'obtention)
            else:
                quart = 0
            del self.cands[c]
            if quart == 0 and lieu.genre != "bit":
                self.n_jetes += 1             # lettre ou compteur sans quart : hors de la synchro, comme avant
                continue
            self.sent.add(c)
            if quart:
                self.n_sent += 1
                self.notes.append(f"{lieu.nom} : quart de cœur reçu, les autres joueurs le reçoivent aussi")
            else:
                self.n_bit += 1
            out.append({"src": lieu.src, "lieu": c, "quart": quart})
        return out


# ---------------------------------------------------------------------------
# Receveur
# ---------------------------------------------------------------------------
class PnjApply:
    RETRY = 1.0
    MAX_RECORDS = 256

    def __init__(self, fallback: bool = True, clock=time.monotonic):
        self.fallback = fallback
        self.clock = clock
        self.pending: dict[str, dict] = {}
        self.done: set[str] = set()
        self.granted: set[str] = set()         # partagé avec PnjWatch (anti-écho)
        self.granter = None                    # mod natif 0.24.0 : fn(flag, item, count) -> int (GRANT genre 4)
        self.counter_owned = None              # () -> bool : l'inventaire possède la vie max (contrat R19)
        self.next_try: dict[str, float] = {}
        self.notes: list[str] = []
        self.noted: set = set()
        self.n_given = self.n_bit_only = self.n_already = self.n_refused = 0
        self.why = ""

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 1024:
            self.noted.add(tag)
            self.notes.append(s)

    def receive(self, rec: dict) -> bool:
        src = rec["src"]
        if src in self.done or src in self.pending or len(self.pending) >= self.MAX_RECORDS:
            return False
        self.pending[src] = rec
        return True

    def reset(self) -> None:
        self.pending.clear()
        self.done.clear()
        self.next_try.clear()
        self.noted.clear()

    def _finish(self, src: str) -> None:
        self.pending.pop(src, None)
        self.next_try.pop(src, None)
        self.done.add(src)

    def tick(self, mem, v, table, can_write: bool, settled: bool, now: float | None = None) -> list[str]:
        """v : coffres.ChestView ; table : comme ``contenu``. Renvoie les codes des lieux marqués faits ici."""
        now = self.clock() if now is None else now
        faits: list[str] = []
        self.why = ""
        if not self.pending:
            return faits
        if not v.ok:
            self.why = v.reason
            return faits
        flags = lire_drapeaux(mem, v.gi)
        if flags is None:
            self.why = "sauvegarde illisible"
            return faits
        for src, rec in list(self.pending.items()):
            lieu = PAR_SRC[src]
            who = str(rec.get("nom") or "un joueur")[:24]
            if flags[lieu.code]:
                self._finish(src)             # fait ici aussi, ou déjà reçu
                self.n_already += 1
                continue
            if now < self.next_try.get(src, 0.0):
                continue
            quart = rec["quart"] == 1
            if quart:
                obj = contenu(table, lieu)
                if obj is None:
                    self.why = "table du randomizer pas encore reçue"
                    continue
                if not est_quart(obj):
                    # notre table ne met pas de quart ici : jamais donné comme un quart
                    if lieu.genre != "bit":
                        self._finish(src)
                        self.n_refused += 1
                        continue
                    quart = False
            owned = bool(self.counter_owned()) if self.counter_owned is not None else False
            give = quart and not owned
            if give and self.granter is None and not self.fallback:
                self._once(("natif", src), f"{lieu.nom} : quart de cœur de {who} : il te faut le mod natif 0.24.0")
                continue
            if not can_write:
                self.why = "écriture impossible (lecture seule ou --progress-no-apply)"
                continue
            if not settled or v.busy:
                self.why = v.busy or "attente de stabilité"
                continue
            busy = game_free(mem)             # relu juste avant d'écrire
            if busy:
                self.why = busy
                continue
            if self.granter is not None:
                try:
                    res = int(self.granter(lieu.drapeau_pose, ITEM_PIECE if give else ITEM_NONE, 1))
                except Exception as exc:      # boîte sans réponse : idempotent, on réessaie
                    self.next_try[src] = now + self.RETRY * 5
                    self._once(("err", src), f"{lieu.nom} : pas encore appliqué ({exc}) : nouvel essai")
                    continue
                if res == -3 or res == -2:
                    self.next_try[src] = now + self.RETRY
                    continue
                if res not in (0, 1):
                    self._finish(src)
                    self.n_refused += 1
                    self.notes.append(f"{lieu.nom} : refusé par le jeu (code {res})")
                    continue
                self.granted.add(lieu.code)
                self._finish(src)
                if res == 0:
                    self.n_already += 1
                    continue
            else:
                if give and not give_piece_fallback(mem, v.gi):
                    self.next_try[src] = now + self.RETRY
                    self.why = "compteur de vie illisible"
                    continue
                self.granted.add(lieu.code)
                poser_drapeau(mem, v.gi, lieu)
                self._finish(src)
            faits.append(lieu.code)
            if give:
                self.n_given += 1
                self.notes.append(f"{lieu.nom} : quart de cœur obtenu par {who} : +1 chez toi")
            else:
                self.n_bit_only += 1
                why = " (la vie max est partagée par l'inventaire)" if quart and owned else ""
                self.notes.append(f"{lieu.nom} : fait par {who}, marqué fait chez toi{why}")
        return faits


# ---------------------------------------------------------------------------
# Ensemble (client)
# ---------------------------------------------------------------------------
class PnjSync:
    SEND_RATE = 4

    def __init__(self, enabled: bool = True, group: str = "default", fallback: bool = True, clock=time.monotonic):
        self.enabled, self.group, self.clock = enabled, group, clock
        self.watch = PnjWatch()
        self.apply = PnjApply(fallback=fallback, clock=clock)
        self.watch.granted = self.apply.granted
        self.mine: dict[str, dict] = {}        # nos lieux (renvoyés à chaque connexion)
        self.out: list[dict] = []
        self.sent_t: list[float] = []
        self._lock = threading.Lock()
        self.notes: list[str] = []

    @property
    def granter(self):
        return self.apply.granter

    @granter.setter
    def granter(self, fn) -> None:
        self.apply.granter = fn

    def on_connect(self) -> None:
        """Le registre vient avec celui des quarts au sol (« gift full » de GiftSync) ; nos lieux sont renvoyés."""
        if not self.enabled:
            return
        with self._lock:
            for m in self.mine.values():
                if m not in self.out:
                    self.out.append(m)

    def take_out(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        self.sent_t = [t for t in self.sent_t if now - t < 1.0]
        out, keep = [], []
        with self._lock:
            for m in self.out:
                if len(self.sent_t) < self.SEND_RATE:
                    self.sent_t.append(now)
                    out.append(m)
                else:
                    keep.append(m)
            self.out = keep
        return out

    def take_notes(self) -> list[str]:
        out = self.notes + self.watch.notes + self.apply.notes
        self.notes, self.watch.notes, self.apply.notes = [], [], []
        return out

    def receive(self, msg: dict, me=None) -> int:
        """Message « gift » du serveur (enregistrement, registre ``full`` ou ``reset``) : seuls les « pnj: » comptent."""
        if not self.enabled or msg.get("group") not in (None, self.group):
            return 0
        if msg.get("reset") in (1, True) and msg.get("from") is None:
            with self._lock:
                self.out.clear()
                self.mine.clear()
            self.apply.reset()
            self.apply.granted.clear()
            self.watch.sent.clear()
            self.watch.cands.clear()
            return 0
        recs = msg.get("list") if msg.get("full") else [msg]
        n = 0
        for raw in (recs if isinstance(recs, list) else [])[:1024]:
            if not isinstance(raw, dict):
                continue
            if me is not None and raw.get("from") == me and not msg.get("full"):
                continue
            rec = valid_pnj(raw)
            if rec is None or rec["src"] in self.mine:
                continue
            rec["nom"] = str(raw.get("nom") or raw.get("by") or "?")[:24]
            n += self.apply.receive(rec)
        return n

    def tick(self, mem, v, table, can_write: bool, settled: bool, now: float | None = None,
             reloads: int = 0) -> list[str]:
        """Une passe : nos lieux faits -> file ; lieux faits ailleurs -> drapeau (et quart) ici. ``reloads`` :
        rechargements de sauvegarde vus par la progression (nouvelle base de l'émetteur)."""
        if not self.enabled or mem is None:
            return []
        now = self.clock() if now is None else now
        gi = v.gi if v.ok else None
        flags = lire_drapeaux(mem, gi) if gi else None
        preuve = lire_preuve(mem, gi) if gi else None
        recs = self.watch.observe(now, (gi, reloads) if gi else None, flags, preuve, table)
        with self._lock:
            for r in recs:
                m = dict(r, type="gift", group=self.group, stage=str(v.stage)[:8],
                         room=v.room if _int(v.room) and -1 <= v.room < 64 else -1)
                self.mine[r["src"]] = m
                self.apply.done.add(r["src"])
                self.out.append(m)
        return self.apply.tick(mem, v, table, can_write, settled, now)

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        a, w = self.apply, self.watch
        s = f"{w.n_sent} quart(s) de personnage partagé(s), {a.n_given} reçu(s)"
        if w.n_bit or a.n_bit_only:
            s += f", {w.n_bit + a.n_bit_only} lieu(x) marqué(s) sans quart"
        if a.n_already:
            s += f", {a.n_already} déjà fait(s)"
        if a.pending:
            mode = "mod natif" if a.granter is not None else ("repli Python" if a.fallback else "attend le mod 0.24.0")
            s += f", {len(a.pending)} en attente ({a.why or mode})"
        return s
