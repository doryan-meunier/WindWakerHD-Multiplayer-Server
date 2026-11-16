"""Deathlink (synchro v3 ; étude docs/etudes/sync3_deathlink_herbe.md, la section « Vérification » prime) : si un
joueur meurt, tout le monde meurt. Réglage de la PARTIE : l'hôte le choisit dans les réglages du lanceur, le
serveur le reçoit (--deathlink, --no-deathlink ; à chaud : « regles_cmd » du lanceur de l'hôte) et l'impose à tous
(champ ``deathlink`` du ``welcome``, message ``regles``). Côté client, --deathlink (activé par --coop-donjon) dit
seulement « ce PC accepte la règle » ; --no-deathlink la refuse sur ce PC (dépannage). Logique testable sans Cemu.

Mort détectée (jamais une simple chute dans le vide, qui ne retire qu'un cœur) : Link entre dans le proc
DEMO_DEAD (``mCurProc`` u32 Link+0x65F0 == 0xB2, HD-ASM : checkGameOverStart 0x02443664, dProcDead_init
0x023F7580) avec la vie à 0 (gameInfo+0x22). Seul changeDeadProc (HD 0x023F7820, unique appelant de
dProcDead_init) y mène, et seulement vie == 0 sans fée en flacon : c'est le chemin du Game Over. Une transition
« pas mort -> mort » = une mort, envoyée une fois (message ``mort``).

Mort appliquée chez les autres (écriture gardée de la vie) : vie courante mise à 0, relue juste avant, seulement
jeu libre (aucun événement : mMode/mbEndProc de dEvt à 0 ; pas de cinématique A11, pas de changement de salle,
salle stable depuis 2 s), Link présent, vie > 0, pas déjà mort, pas pendant notre mini-jeu. Le jeu fait le reste
lui-même : changeDeadProc voit la vie à 0 et lance la mort et le Game Over (exactement comme après un coup).
FÉE EN FLACON : décision, elle SAUVE (règle du jeu, sans code de plus) : changeDeadProc lâche la fée, vide le
flacon et Link se relève. Le journal le dit (« une fée t'a sauvé »). C'est la seule façon de ne pas suivre un
deathlink, et elle coûte la fée.

Anti-boucle : une mort qui suit une mort reçue (dans la fenêtre de surveillance, jusqu'à DEAD ou jusqu'à ce qu'une
fée relève Link) n'est jamais renvoyée ; le serveur fusionne de plus les morts à moins de FENETRE s d'une vague
(deux joueurs tués par le même boss ne font qu'une vague). Délai de grâce : GRACE s après chaque (re)connexion, le
client n'envoie ni n'applique rien, et le serveur ignore la mort d'un joueur connecté depuis moins de GRACE s (un
Game Over déjà à l'écran au retour n'est pas une nouvelle mort ; la transition est relevée à partir d'un état de
départ). Mode Hanteurs : seuls les Link comptent (un Hanteur n'envoie ni ne reçoit de mort ; le serveur filtre
aussi).

Aucune commande de la boîte aux lettres : compatible pack v17.
"""
from __future__ import annotations

import struct
import threading
import time

from jeu import progress as prog

# --- adresses HD [HD-ASM, relues dans cking.rpx par tests/test_deathlink_herbe.py] ------------------------
LINK_CUR_PROC = 0x65F0      # u32 daPy_lk_c::mCurProc (checkGameOverStart 0x02443664 : lwz r0, 0x65F0(r3))
PROC_DEAD = 0xB2            # daPyProc_DEMO_DEAD_e (cmpwi r0, 0xB2)
GI_LIFE = 0x22              # u16 vie courante en quarts (changeDeadProc 0x023F785C : lhz r0, 0x22(gi))
GI_MAX_LIFE = 0x20          # u16 vie max
FAIRY_BOTTLE = 0x57         # dItemNo_FAIRY_BOTTLE_e (changeDeadProc 0x023F7878 : li r4, 0x57)

GRACE = 8.0                 # s après une (re)connexion : rien envoyé ni appliqué (serveur : morts ignorées)
FENETRE = 10.0              # s : morts fusionnées avec la vague précédente (serveur)
PENDING_TTL = 60.0          # s : mort reçue jamais applicable (longue cinématique) -> abandonnée
WATCH = 20.0                # s : après une mort appliquée, sa mort (ou la fée) attendue
SETTLE = 2.0                # s de salle stable avant d'appliquer


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def lire(mem) -> tuple[int, int] | None:
    """(vie, proc de Link) ; None si pas de partie ou pas de Link lisible."""
    from jeu import game
    gi = game.gameinfo_ptr(mem)
    if gi is None:
        return None
    link = game.player_ptrs(mem)[0]
    if not link:
        return None
    raw = mem.try_read(gi + GI_LIFE, 2)
    rp = mem.try_read(link + LINK_CUR_PROC, 4)
    if raw is None or rp is None:
        return None
    return struct.unpack(">H", raw)[0], struct.unpack(">I", rp)[0]


def a_l_ecran_titre(mem) -> bool:
    """Écran titre ou choix du fichier (prog.TITLE_STAGES) : pas en partie. Stage illisible : False (inconnu)."""
    raw = mem.try_read(prog.SYM_CUR_STAGE, 8)
    if raw is None:
        return False
    return raw.split(b"\0", 1)[0].decode("ascii", "replace") in prog.TITLE_STAGES


def est_mort(lu) -> bool:
    return lu is not None and lu[1] == PROC_DEAD and lu[0] == 0


def appliquer_mort(mem) -> str:
    """Vie courante mise à 0 (le jeu lance lui-même la mort, ou la fée du flacon). Gardes relues juste avant :
    partie chargée, Link présent, aucun événement, vie > 0 et ≤ 80 quarts, Link pas déjà mort.
    Renvoie « ok », « attente » (à retenter : événement) ou « inutile » (déjà mort, pas de partie)."""
    from jeu import game
    gi = game.gameinfo_ptr(mem)
    if gi is None or not game.player_ptrs(mem)[0]:
        return "inutile"
    ev = mem.try_read(prog.SYM_EVT_CTRL + prog.EVT_MODE, 2)
    if ev is None or ev != bytes(2):
        return "attente"                 # mMode ou mbEndProc : événement en cours (dialogue, porte, sauvegarde…)
    lu = lire(mem)
    raw = mem.try_read(gi + GI_MAX_LIFE, 4)
    if lu is None or raw is None:
        return "inutile"
    vmax, vie = struct.unpack(">HH", raw)
    if vie == 0 or lu[1] == PROC_DEAD:
        return "inutile"
    if vie > 80 or vmax > 80:
        return "inutile"                 # lecture aberrante : rien écrit
    mem.write(gi + GI_LIFE, b"\0\0")
    return "ok"


class DeathLink:
    """Client : règle reçue du serveur, détection de NOTRE mort, application des morts reçues."""

    def __init__(self, accepte: bool = False, clock=time.time):
        self.accepte = accepte           # --deathlink / --coop-donjon, sauf --no-deathlink
        self.regle = False               # règle de la partie (serveur)
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.t_connexion: float | None = None
        self.prev: bool | None = None    # mort au relevé précédent (None : état de départ à relever)
        self.pending: dict | None = None
        self.watch: float | None = None  # fin de la surveillance d'une mort appliquée
        self.vu_mort_watch = False
        self.n = {"envoyees": 0, "recues": 0, "appliquees": 0, "fees": 0, "ignorees": 0}

    @property
    def enabled(self) -> bool:
        return self.accepte and self.regle

    # --- réseau (boucle asyncio) -------------------------------------------------------------------
    def _note(self, s: str) -> None:
        with self.lock:
            self.notes = (self.notes + ["deathlink : " + s])[-32:]

    def take_notes(self) -> list[str]:
        with self.lock:
            out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def set_regle(self, on, source: str = "serveur") -> None:
        on = on in (1, True)
        if on != self.regle:
            self.regle = on
            if on and not self.accepte:
                self._note("la partie a le deathlink, mais ce PC le refuse (--no-deathlink, ou client sans "
                           "--coop-donjon) : tes morts ne partent pas, celles des autres ne te touchent pas")
            elif on:
                self._note(f"activé par le {source} : si un joueur meurt, tout le monde meurt")
            else:
                self._note(f"désactivé par le {source}")
        if not self.enabled:
            with self.lock:
                self.inbox.clear()
            self.pending = None

    def on_welcome(self, msg: dict, now: float | None = None) -> None:
        self.t_connexion = self.clock() if now is None else now
        self.prev = None
        self.set_regle(msg.get("deathlink"))

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "regles":
            self.set_regle(msg.get("deathlink"), "serveur (réglage de l'hôte)")
            return
        if msg.get("type") != "mort" or not self.enabled:
            return
        if me is not None and msg.get("from") == me:
            return
        with self.lock:
            self.inbox = (self.inbox + [dict(msg, _t=self.clock() if now is None else now)])[-8:]

    def mort_induite(self, now: float | None = None) -> None:
        """Vie partagée (synchro/vie_partagee.py) : la vie commune vient d'être écrite à 0 chez nous ; la mort qui suit
        est surveillée comme une mort appliquée (jamais renvoyée : pas de double comptage avec le deathlink)."""
        now = self.clock() if now is None else now
        self.pending = None
        self.watch = now + WATCH
        self.vu_mort_watch = False

    def reset_network(self) -> None:
        with self.lock:
            self.inbox.clear()
            self.out.clear()
        self.pending = None
        self.t_connexion = None
        self.prev = None

    # --- étape (thread de travail) ------------------------------------------------------------------
    def step(self, mem, now: float, free: bool, settled: bool, can: bool, connected: bool,
             hanteur: bool = False, minijeu: int = 0) -> None:
        with self.lock:
            inbox, self.inbox = self.inbox, []
        if not self.enabled:
            self.prev, self.pending = None, None
            return
        en_grace = self.t_connexion is None or now - self.t_connexion < GRACE
        for m in inbox:
            self.n["recues"] += 1
            nom = str(m.get("name") or m.get("from") or "?")[:24]
            if hanteur:
                self.n["ignorees"] += 1
                self._note(f"{nom} est mort ; tu es Hanteur : rien")
            elif en_grace:
                self.n["ignorees"] += 1
                self._note(f"{nom} est mort pendant ton délai de grâce (reconnexion) : rien")
            else:
                self.pending = {"nom": nom, "t": m["_t"]}
        if self.pending is not None and mem is not None and a_l_ecran_titre(mem):
            # pas en partie (écran titre, choix du fichier) : la mort n'attend pas qu'on charge une partie (sinon
            # on mourrait 2 s après le chargement, pour une mort survenue pendant qu'on était au menu)
            self.n["ignorees"] += 1
            self._note(f"{self.pending['nom']} est mort pendant que tu étais à l'écran titre : rien")
            self.pending = None
        lu = lire(mem) if mem is not None else None
        mort = est_mort(lu)
        # notre mort : transition pas mort -> mort (relevée avant de clore la surveillance)
        if lu is None:
            self.prev = None
        else:
            if self.prev is False and mort:
                if self.watch is not None:
                    self._note("mort par deathlink : pas renvoyée")
                elif hanteur:
                    pass
                elif en_grace or not connected:
                    self._note("mort pendant le délai de grâce ou hors connexion : pas envoyée")
                else:
                    with self.lock:
                        self.out = (self.out + [{"type": "mort"}])[-4:]
                    self.n["envoyees"] += 1
                    self._note("tu es mort : tout le monde meurt")
                self.pending = None      # déjà mort : une mort reçue n'a plus d'objet
            self.prev = mort
        # surveillance d'une mort appliquée : DEAD (mort induite, jamais renvoyée) ou Link relevé par une fée
        if self.watch is not None and lu is not None:
            debut = self.watch - WATCH
            if mort:
                self.vu_mort_watch = True
            elif self.vu_mort_watch:
                self.watch = None        # Game Over passé (ou continue) : surveillance finie
            elif lu[0] > 0 and now - debut > 1.0:
                self.n["fees"] += 1
                self._note("tu t'es relevé : une fée en flacon t'a sauvé (le flacon est vide)")
                self.watch = None
        if self.watch is not None and now > self.watch and not mort and (lu is None or lu[0] > 0):
            self.watch = None            # vie encore à 0 : le jeu va lancer la mort, surveillance prolongée
        # mort reçue à appliquer
        p = self.pending
        if p is None:
            return
        if hanteur:
            self.pending = None
            return
        if now - p["t"] > PENDING_TTL:
            self.pending = None
            self.n["ignorees"] += 1
            self._note(f"mort de {p['nom']} abandonnée : jamais de moment sûr pendant {PENDING_TTL:.0f} s")
            return
        if mort or (lu is not None and lu[0] == 0):
            self.pending = None          # déjà à 0 : le jeu s'en charge
            return
        if not (free and settled and can and connected) or minijeu or lu is None:
            return
        r = appliquer_mort(mem)
        if r == "ok":
            self.pending = None
            self.n["appliquees"] += 1
            self.watch = now + WATCH
            self.vu_mort_watch = False
            self._note(f"{p['nom']} est mort : tu meurs aussi")
        elif r == "inutile":
            self.pending = None

    def status(self) -> str:
        if not self.regle:
            return "désactivé (réglage de la partie)"
        if not self.accepte:
            return "refusé sur ce PC (--no-deathlink)"
        n = self.n
        return (f"actif : {n['envoyees']} mort(s) envoyée(s), {n['recues']} reçue(s), {n['appliquees']} "
                f"appliquée(s), {n['fees']} sauvé(s) par une fée, {n['ignorees']} ignorée(s)"
                + (" ; mort reçue en attente d'un moment sûr" if self.pending else ""))


class MortsServeur:
    """Serveur : règle de la partie, vagues de morts (fusion, délai de grâce)."""

    def __init__(self, on: bool = False):
        self.on = bool(on)
        self.t_vague = float("-inf")
        self.n = 0

    def mort(self, now: float, connecte_depuis: float, hanteur: bool) -> tuple[int | None, str]:
        """(numéro de vague, raison) ; None : ignorée."""
        if not self.on:
            return None, "deathlink désactivé"
        if hanteur:
            return None, "Hanteur (seuls les Link comptent)"
        if now - connecte_depuis < GRACE:
            return None, "délai de grâce après connexion"
        if now - self.t_vague < FENETRE:
            return None, "fusionnée avec la vague précédente"
        self.t_vague = now
        self.n += 1
        return self.n, ""
