"""Monde vivant (synchro v3 ; étude docs/etudes/sync3_monde_vivant.md, la section « Vérification » de l'étude
prime) : heure du jour, vent, météo de la mer, mini-jeux, ennemis « à garder chez soi ». Logique testable sans
Cemu.

Trois synchros, chacune avec son option, toutes activées par --coop-donjon :

  Heure du jour (--share-time, --no-time) : l'heure est dans la sauvegarde (dSv_player_status_b_c.mTime, f32
    gi+0x44, 0 à 360 = 24 h ; jour mDate u16 gi+0x48). setDaytime (HD 0x0255B300) la relit au début de chaque
    image, l'avance de mTimeAdv (0,02 par image) et la réécrit à la fin : une écriture dans gi+0x44 est donc
    reprise dès l'image suivante. Le joueur de référence (l'hôte : client lancé avec --hote, sinon le premier
    connecté du groupe) envoie son heure ; le serveur ne relaie que la sienne. Chez les autres, recalage
    seulement quand l'heure TOURNE chez les deux (mer et autres salles « temps qui passe » : octet
    dStage_roomControl_c::m_time_pass 0x101EBFD8, jeu libre, heure qui avance d'elle-même) : jamais sur une
    île où l'heure est figée, ni pendant un événement, ni de nuit forcée. Minuit franchi en avant : on écrit
    359,9 et c'est le jeu qui passe minuit (jour + 1, dKankyo_DayProc) ; minuit en arrière : on attend. Le
    jour (phases de la lune, Vaisseau fantôme) est recopié loin de minuit. Chant du temps qui passe joué par
    un AUTRE que l'hôte (relecture adverse : sans ça, l'invité retrouvait l'heure de l'hôte une seconde après
    son chant) : un saut d'heure en avant vu chez soi, en mer, dans la même salle, part au serveur (« heure »),
    qui le passe à la référence ; elle l'applique (jamais en arrière, minuit passé par le jeu) et tout le
    groupe la suit. Pendant ce temps (JUMP_HOLD), l'auteur du chant n'est pas recalé.
    Ce qui précède (référence = l'hôte) ne sert plus qu'avec un ANCIEN serveur. Constat en jeu (2026-11-15, deux PC
    sur le portage natif) : l'heure et la nuit n'étaient pas les mêmes. Cause : on ne recalait que quand l'heure
    tournait chez les DEUX ; or setDaytime (0x0255B300) ne l'avance pas sur une île ou à l'intérieur (m_time_pass
    nul : seulement de 0 h à 11 h et de 18 h à 23 h), ni pendant un événement (play+0x5292), ni avant l'objet
    d'histoire (isGetItem(2, 0)) : dès qu'un joueur y passait du temps, l'autre continuait seul et les heures
    divergeaient pour de bon. Désormais : HORLOGE COMMUNE gardée par le serveur (MondeServeur), une par groupe. Chaque
    client dit s'il est « figé » (``fig`` dans « monde » : son heure ne bouge plus depuis FIG_DT s, jeu libre, salle
    stable ; c'est le jeu qui le décide, rien n'est recopié de sa logique) ; l'heure commune n'avance que si PERSONNE
    n'est figé. Chez un client dont l'heure avance : heure commune en marche -> recalage au-delà de TOL (comme
    avant) ; heure commune arrêtée -> heure TENUE à la valeur commune (écart HOLD_TOL). Un client figé n'écrit
    jamais (le jeu choisit son heure dans ces lieux) : en sortant, il reprend l'heure commune. Le Chant du temps
    qui passe fait sauter l'horloge commune (même arrêtée). Joueur parti ou muet : il ne compte plus.
  Vent (--share-wind, --no-wind) : direction du Requiem des vents, dans la sauvegarde (mTactWindAngleX/Y s16
    gi+0x4A/+0x4C, -1/-1 = jamais réglé) ET dans g_env_light.mWind (+0xA24/+0xA26, drapeaux +0xA2C), que
    dKyw_wind_set (chaque image) lit pour le vecteur du vent qui pousse la voile ; même écriture que
    dKyw_tact_wind_set (HD 0x0257E490). « Le dernier qui joue le Requiem gagne » : un changement local part au
    serveur, qui garde un registre par groupe (initialisé par l'hôte) et le renvoie aux autres. Jamais pendant
    un vent d'événement (mEvtWindSet +0xA2D). Le vent tourne en douceur chez l'autre (cLib_addCalc du jeu).
    Constat en jeu (2026-11-15, natif) : le Requiem joué chez l'un n'était pas repris chez l'autre. Cause : la
    VOILE RAPIDE de la version HD (daShip_c::procSteerMove 0x0247E9E8..0x0247E9FC, play+0x5D2C) appelle aussi
    dKyw_tact_wind_set à chaque pas pour mettre le vent dans le sens du bateau : la sauvegarde change sans cesse en
    mer, chaque cap partait comme un « Requiem » (dernier qui gagne = dernier qui navigue) et écrasait le vrai
    Requiem chez les deux ; et le receveur réécrivait le registre à chaque tour contre sa propre voile. Avec le mod
    natif 0.22.0 (zone WWMPVEN1, natif/mod_wwmp/vent.c) : seul un vrai Requiem part (compteur du jeu, accroche de
    retour de Wind_CommitWind), et un vent reçu est appliqué UNE fois par le jeu (dKyw_tact_wind_set, comme un
    Requiem joué ici). Sans le mod (Cemu) : la lecture de la sauvegarde d'avant.
  Météo de la mer (--share-weather, --no-weather) : le tirage du temps qu'il fait en mer (dKy_event_proc HD
    0x0255D130, « dice weather ») est aléatoire sur chaque PC. On recopie l'état de son automate depuis la
    référence (moment du prochain tirage +0xFC0, fin d'étape +0xFC4, compteur +0x1068, mode +0x1093, état
    +0x1094, motif +0x1095) ; la pluie et les couleurs suivent d'elles-mêmes. Mode > 5 = arrêt du jeu
    (assertion 0x0255D740) : toute valeur reçue est bornée (mode 0-5, état 0-3, motif 0-3, compteur dans son
    motif) et l'écriture se fait en deux temps pour qu'aucune image ne voie un mélange (voir _weather_step).

Mini-jeux (--share-minigames, --no-minigames) : type du mini-jeu en cours (dComIfGp.mMiniGameType, u8
  play+0x5CEA [HD-ASM : startMiniGame / endMiniGame inlinés]) envoyé dans state.mg ; chez les autres « X joue :
  vente aux enchères » au début, « X : fin du mini-jeu » à la fin (partout). Pendant TON mini-jeu, les
  corrections venues des autres (rubis/flèches/bombes communs, inventaire) et les écritures « monde » de ce
  module attendent la fin : un mini-jeu n'est jamais perturbé par la partie d'un autre ; tes propres gains et
  dépenses partent normalement.

Ennemis gardés chez soi (sans option : comme le filtre des boss, --no-boss-filter les remet) : Orca (Ji1,
  groupe « ennemi » du jeu à cause de son entraînement : une marionnette à l'IA suspendue bloquerait le
  dialogue et le mini-jeu) et les Gyorgs (Gy, créés à la volée par GyCtrl à des positions tirées au hasard :
  leurs clés ne correspondent jamais d'un PC à l'autre).

Aucune commande de la boîte aux lettres : compatible pack v17 (l'asm ne change pas). Seul le mod natif 0.22.0 ajoute
une zone de données (WWMPVEN1, vent), sans commande ni bit de capacité.
"""
from __future__ import annotations

import math
import struct
import threading
import time

from jeu import progress as prog
from synchro import rares

# --- adresses HD [HD-ASM, vérifiées dans cking.rpx par tests/test_monde.py] ------------------------------
GI_TIME = 0x44              # f32 mTime (setDaytime 0x0255B328 lfs, 0x0255B84C stfs)
GI_DATE = 0x48              # u16 mDate (0x0255B340 lhz, 0x0255B858 sth)
GI_WIND_X = 0x4A            # s16 mTactWindAngleX (dKyw_tact_wind_set 0x0257E4CC sth ; wind_init 0x02577BB4)
GI_WIND_Y = 0x4C            # s16 mTactWindAngleY (0x0257E4D8 sth)
GI_READ = 0x0C              # gi+0x44 .. gi+0x4F

ENV = rares.ENV_LIGHT       # g_env_light 0x10475A68
E_CUR_TIME = 0x1020         # f32 mCurTime (copie de gi+0x44 à chaque image ; lu seulement)
E_DAY = 0x108A              # u16 mDayOfWeek (idem gi+0x48)
E_WIND_X = 0xA24            # s16 mWind.mTactWindAngleX
E_WIND_Y = 0xA26            # s16 mWind.mTactWindAngleY
E_WIND_FLAGS = 0xA2C        # u8 mTactWindAngleFlags (bit 0 : réglé ; bit 7 : sens de rotation de la flèche)
E_EVT_WIND = 0xA2D          # u8 mEvtWindSet (0 ou 0xFF : pas de vent d'événement)
E_W_CHANGE = 0xFC0          # f32 mDiceWeatherChangeTime
E_W_TIME = 0xFC4            # f32 mDiceWeatherTime (fin de l'étape en cours)
E_W_COUNTER = 0x1068        # s32 mDiceWeatherCounter (indice dans le motif)
E_W_MODE = 0x1093           # u8 mDiceWeatherMode (0-5 ; autre valeur : assertion, le jeu s'arrête)
E_W_STATE = 0x1094          # u8 mDiceWeatherState (0 attente, 1 tirage, 2 déroulé, 3 fin)
E_W_PATTERN = 0x1095        # u8 mDiceWeatherCurrPattern (0-3)
E_LO, E_HI = E_WIND_X, E_W_PATTERN + 1   # une seule lecture de g_env_light

TIME_PASS = 0x101EBFD8      # u8 dStage_roomControl_c::m_time_pass (setDaytime 0x0255B49C, event_proc 0x0255D2B0)
MINIGAME = prog.SYM_PLAY + 0x5CEA   # u8 mMiniGameType (endMiniGame : 0x0205A500, 0x02109AC4…)

DAY = 360.0
FPS = 30
W_MODES = range(6)
W_STATES = range(4)
W_PATTERN_LEN = {0: 4, 1: 4, 2: 4, 3: 2}   # S_wether_mode_pat01..04 (fin 0xFF comprise)

# mini-jeux : type -> libellé (acteur HD qui appelle startMiniGame) [HD-ASM ; libellés à confirmer en jeu]
MINIJEUX = {
    1: "course aux drapeaux",       # Gflag (291)
    3: "tir au canon",              # Canon (61)
    5: "vente aux enchères",        # Auction (382)
    6: "entraînement d'Orca",       # Ji1 (318)
    7: "mini-jeu des Piafs",        # Bmsw (348), Btsw (349)
    8: "mini-jeu",                  # NpcSo (118)
}

# ennemis (groupe 2 du jeu) jamais envoyés, ni marionnettes, ni orphelins : chacun garde les siens
ENNEMIS_LOCAUX: dict[int, str] = {
    318: "Ji1",     # Orca : PNJ classé « ennemi » (entraînement) ; marionnette = dialogue et mini-jeu bloqués
    228: "Gy",      # Gyorg : créés à la volée par GyCtrl, positions tirées au hasard (clés différentes)
}


def sans_ennemis_locaux(actors, proc_of=lambda a: a.proc_name) -> list:
    return [a for a in actors if proc_of(a) not in ENNEMIS_LOCAUX]


def libelle_minijeu(t) -> str:
    if not _int(t) or t <= 0:
        return "mini-jeu"
    return MINIJEUX.get(t, f"mini-jeu (type {t})")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _time_ok(x) -> bool:
    return _num(x) and 0.0 <= x < DAY


def circ(a: float, b: float) -> float:
    """a - b ramené dans [-180, 180)."""
    return (a - b + DAY / 2) % DAY - DAY / 2


def s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


# ---------------------------------------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------------------------------------
def valid_weather(w) -> tuple | None:
    """(change, fin, compteur, mode, état, motif) bornés, ou None."""
    if not isinstance(w, (list, tuple)) or len(w) != 6:
        return None
    chg, fin, cnt, mode, state, pat = w
    if not (_time_ok(chg) and _time_ok(fin) and _int(cnt) and _int(mode) and _int(state) and _int(pat)):
        return None
    if mode not in W_MODES or state not in W_STATES or pat not in W_PATTERN_LEN:
        return None
    if not 0 <= cnt < W_PATTERN_LEN[pat]:
        return None
    return (float(chg), float(fin), cnt, mode, state, pat)


def lire(mem) -> dict | None:
    """Heure, jour, vent, météo et « temps qui passe », lecture seule ; None si illisible (chargement)."""
    ptr = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if ptr is None:
        return None
    gi, = struct.unpack(">I", ptr)
    if not 0x10000000 <= gi < 0x50000000:
        return None
    raw = mem.try_read(gi + GI_TIME, GI_READ)
    env = mem.try_read(ENV + E_LO, E_HI - E_LO)
    tp = mem.try_read(TIME_PASS, 1)
    if raw is None or env is None or tp is None or len(raw) < GI_READ or len(env) < E_HI - E_LO:
        return None
    t, = struct.unpack_from(">f", raw, 0)
    d, wx, wy = struct.unpack_from(">Hhh", raw, GI_DATE - GI_TIME)
    if not _time_ok(t):
        return None

    def e(fmt, off):
        return struct.unpack_from(fmt, env, off - E_LO)[0]
    w = (e(">f", E_W_CHANGE), e(">f", E_W_TIME), e(">i", E_W_COUNTER), e(">B", E_W_MODE), e(">B", E_W_STATE),
         e(">B", E_W_PATTERN))
    return {"gi": gi, "t": float(t), "d": d, "wx": wx, "wy": wy, "ex": e(">h", E_WIND_X), "ey": e(">h", E_WIND_Y),
            "fl": e(">B", E_WIND_FLAGS), "evt": e(">B", E_EVT_WIND), "w": w, "tp": bool(tp[0])}


def lire_minijeu(mem) -> int | None:
    raw = mem.try_read(MINIGAME, 1)
    return None if raw is None else raw[0]


def vent_de(cur: dict) -> tuple[int, int, bool]:
    """(x, y, réglé) tel que la sauvegarde le garde (-1/-1 = jamais réglé)."""
    on = not (cur["wx"] == -1 and cur["wy"] == -1)
    return (cur["wx"], cur["wy"], True) if on else (-1, -1, False)


# ---------------------------------------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------------------------------------
GROUP_MAX = 32


def _group(g) -> str:
    return str(g or "default")[:GROUP_MAX]


def valid_monde(msg) -> dict | None:
    """« monde » {group, t, d, run, tp, sea, w, fig} : None si invalide. ``fig`` (facultatif : un ancien client ne
    l'envoie pas) : True = l'heure est arrêtée chez ce joueur (lieu où le temps ne passe pas), False = elle avance,
    None = pas encore vu."""
    if not isinstance(msg, dict):
        return None
    t, d = msg.get("t"), msg.get("d")
    if not _time_ok(t) or not _int(d) or not 0 <= d <= 0xFFFF:
        return None
    flags = {k: msg.get(k) for k in ("run", "tp", "sea")}
    if not all(isinstance(v, bool) for v in flags.values()):
        return None
    w = msg.get("w")
    w = None if w is None else valid_weather(w)
    if msg.get("w") is not None and w is None:
        return None
    fig = msg.get("fig")
    if fig is not None and not isinstance(fig, bool):
        return None
    return {"group": _group(msg.get("group")), "t": float(t), "d": d, **flags, "w": list(w) if w else None,
            "fig": fig}


def valid_horloge(msg) -> dict | None:
    """« horloge » {t, d, run, seq, figes} (serveur -> client : l'heure commune du groupe) : None si invalide."""
    if not isinstance(msg, dict):
        return None
    t, d, run, seq = msg.get("t"), msg.get("d"), msg.get("run"), msg.get("seq", 0)
    if not _time_ok(t) or not _int(d) or not 0 <= d <= 0xFFFF or not isinstance(run, bool) or not _int(seq):
        return None
    figes = msg.get("figes") or []
    if not isinstance(figes, list):
        return None
    return {"t": float(t), "d": d, "run": run, "seq": seq, "figes": [str(x)[:24] for x in figes[:8]]}


def valid_heure(msg) -> dict | None:
    """« heure » {group, t, d} (saut d'heure local : Chant du temps qui passe) : None si invalide."""
    if not isinstance(msg, dict):
        return None
    t, d = msg.get("t"), msg.get("d")
    if not _time_ok(t) or not _int(d) or not 0 <= d <= 0xFFFF:
        return None
    return {"group": _group(msg.get("group")), "t": float(t), "d": d}


def valid_vent(msg) -> dict | None:
    """« vent » {group, x, y, on, init} : None si invalide."""
    if not isinstance(msg, dict):
        return None
    x, y, on = msg.get("x"), msg.get("y"), msg.get("on")
    if not (_int(x) and _int(y) and isinstance(on, bool)) or not (-0x8000 <= x <= 0x7FFF and -0x8000 <= y <= 0x7FFF):
        return None
    if not on:
        x = y = -1
    elif x == -1 and y == -1:
        return None                     # « réglé » avec la valeur « jamais réglé » : incohérent
    return {"group": _group(msg.get("group")), "x": x, "y": y, "on": on, "init": msg.get("init") is True}


# ---------------------------------------------------------------------------------------------------------
# Serveur : référence par groupe, registre du vent
# ---------------------------------------------------------------------------------------------------------
class MondeServeur:
    """Référence = client lancé avec --hote (le seul du groupe), sinon le plus petit id ; parmi les joueurs du
    groupe qui ont envoyé « monde » depuis moins de FRESH s (elle ne sert plus qu'à la météo de la mer). Vent :
    registre par groupe.

    Horloge commune (une par groupe) : le serveur garde l'heure de référence (t, jour) et la fait avancer au rythme du
    jeu (RATE : 0,02 par pas, 30 pas par seconde). Chaque joueur dit dans « monde » si l'heure est arrêtée chez lui
    (``fig`` : île figée, intérieur, événement…). Règle : l'heure n'avance que si AUCUN joueur frais du groupe n'est
    figé ; un joueur qui part, ou qui se tait depuis FRESH s, ne compte plus. Personne : l'horloge s'arrête (monde
    persistant : elle reprend où elle était). Chaque « monde » reçoit l'horloge en réponse ; un changement d'état
    (arrêt, reprise, saut du Chant du temps qui passe) part à tout le groupe. Les envois attendent dans ``envois``
    ([(destinataires, message)], vidé par ``prendre_envois``). La première horloge d'un groupe part de ``init`` (heure
    gardée par le monde persistant) ou, sans elle, de l'heure du premier joueur ; un nouveau venu se range ensuite."""

    FRESH = 5.0
    MIN_GAP = 0.2               # s entre deux « monde » d'un même joueur
    VENT_MIN_GAP = 0.5
    HEURE_MIN_GAP = 1.0
    RATE = FPS * 0.02           # unités d'heure par seconde (mTimeAdv 0,02 par pas de jeu)
    SAUT_MIN = 3.0              # Chant du temps qui passe : saut en avant plus grand que l'écart toléré...
    SAUT_MAX = 185.0            # ... et de 12 h au plus (jusqu'à 6 h ou 18 h ; un peu de marge pour la latence)

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.seen: dict[int, tuple[str, float, bool]] = {}   # joueur -> (groupe, heure, hôte)
        self.vent: dict[str, dict] = {}                      # groupe -> {x, y, on, seq, kind, hote, from}
        self.last: dict[int, float] = {}
        self.last_vent: dict[int, float] = {}
        self.last_heure: dict[int, float] = {}
        self.fig: dict[int, bool | None] = {}                # joueur -> heure arrêtée chez lui (None : inconnu)
        self.horloges: dict[str, dict] = {}                  # groupe -> {t, d, run, base, seq, figes}
        self.envois: list[tuple[list[int], dict]] = []

    def forget(self, alive) -> None:
        partis = {p for d in (self.seen, self.fig) for p in d if p not in alive}
        groupes = {self.seen[p][0] for p in partis if p in self.seen}
        for d in (self.seen, self.last, self.last_vent, self.last_heure, self.fig):
            for pid in [p for p in d if p not in alive]:
                del d[pid]
        now = self.clock()
        for g in groupes:                       # un joueur figé qui part : l'heure repart pour les autres
            if self.horloge_maj(g, now):
                self._diffuser(g, now)

    def prendre_envois(self) -> list[tuple[list[int], dict]]:
        out, self.envois = self.envois, []
        return out

    # --- horloge commune -------------------------------------------------------------------------------------------
    def _avancer(self, c: dict, group: str, now: float) -> None:
        if c["run"]:
            # jamais au-delà du moment où le dernier joueur du groupe était encore frais (personne : arrêtée)
            vus = [t for g, t, _h in self.seen.values() if g == group]
            fin = min(now, max(vus) + self.FRESH) if vus else c["base"]
            t = c["t"] + max(0.0, fin - c["base"]) * self.RATE
            n = int(t // DAY)
            c["t"], c["d"] = t - n * DAY, (c["d"] + n) & 0xFFFF
        c["base"] = now

    def horloge_maj(self, group: str, now: float | None = None) -> bool:
        """Avance l'horloge du groupe, puis refait son état (arrêtée si un joueur frais est figé, ou si personne).
        True si l'état a changé (à diffuser)."""
        now = self.clock() if now is None else now
        c = self.horloges.get(group)
        if c is None:
            return False
        self._avancer(c, group, now)
        membres = self.members(group, now)
        figes = sorted(p for p in membres if self.fig.get(p) is True)
        run = bool(membres) and not figes
        c["figes"] = figes
        if run != c["run"]:
            c["run"] = run
            c["seq"] += 1
            return True
        return False

    def horloge_de(self, group: str) -> dict | None:
        """{t, d, run, seq, figes} de l'horloge du groupe (à l'instant), ou None."""
        g = _group(group)
        if g not in self.horloges:
            return None
        self.horloge_maj(g)
        c = self.horloges[g]
        return {k: c[k] for k in ("t", "d", "run", "seq")} | {"figes": list(c["figes"])}

    def _msg_horloge(self, group: str) -> dict:
        c = self.horloges[group]
        return {"type": "horloge", "group": group, "t": round(c["t"], 3), "d": c["d"], "run": c["run"],
                "seq": c["seq"], "figes": list(c["figes"])}

    def _diffuser(self, group: str, now: float, aussi=()) -> None:
        dest = sorted(set(self.members(group, now)) | set(aussi))
        if dest:
            self.envois.append((dest, self._msg_horloge(group)))

    def _horloge(self, pid: int, v: dict, now: float, init=None, robot: bool = False) -> None:
        g = v["group"]
        self.fig[pid] = None if robot else v.get("fig")     # le joueur robot n'arrête jamais l'heure des autres
        if g not in self.horloges:
            t, d = (init["t"], init["d"]) if init else (v["t"], v["d"])
            self.horloges[g] = {"t": float(t) % DAY, "d": int(d) & 0xFFFF, "run": False, "base": now, "seq": 0,
                                "figes": []}
        if self.horloge_maj(g, now):
            self._diffuser(g, now, aussi=(pid,))
        else:
            self.envois.append(([pid], self._msg_horloge(g)))

    def monde(self, pid: int, hote: bool, msg: dict, init=None, robot: bool = False) -> tuple[dict | None, list[int]]:
        """(message à relayer, destinataires) ; (None, []) si rien. L'horloge (réponse, ou diffusion si son état
        change) va dans ``envois``. ``init`` : {t, d} du monde persistant pour une horloge neuve ; ``robot`` : le
        joueur robot (jamais figé)."""
        v = valid_monde(msg)
        if v is None:
            return None, []
        now = self.clock()
        if now - self.last.get(pid, float("-inf")) < self.MIN_GAP:
            return None, []
        self.last[pid] = now
        if v["group"] in self.horloges:
            self.horloge_maj(v["group"], now)   # AVANT de le compter : un groupe resté vide n'a pas avancé
        self.seen[pid] = (v["group"], now, bool(hote))
        self._horloge(pid, v, now, init=init, robot=robot)
        if self.ref(v["group"], now) != pid:
            return None, []
        out = dict(v, type="monde", ref=True, **{"from": pid})
        out.pop("fig", None)
        return out, [o for o in self.members(v["group"], now) if o != pid]

    def heure(self, pid: int, msg: dict) -> tuple[dict | None, list[int]]:
        """Saut d'heure d'un joueur (Chant du temps qui passe) : l'horloge du groupe saute en avant jusqu'à son
        heure (minuit franchi : jour + 1), même arrêtée, et tout le groupe la reçoit (``envois``). Rien à relayer."""
        v = valid_heure(msg)
        if v is None:
            return None, []
        now = self.clock()
        if now - self.last_heure.get(pid, float("-inf")) < self.HEURE_MIN_GAP:
            return None, []
        self.last_heure[pid] = now
        g = v["group"]
        c = self.horloges.get(g)
        if c is None:
            return None, []
        self.horloge_maj(g, now)
        saut = (v["t"] - c["t"]) % DAY
        if not self.SAUT_MIN < saut <= self.SAUT_MAX:
            return None, []                     # déjà là, ou en arrière : un chant n'avance que (12 h au plus)
        if v["t"] < c["t"]:
            c["d"] = (c["d"] + 1) & 0xFFFF
        c["t"] = v["t"]
        c["seq"] += 1
        self._diffuser(g, now, aussi=(pid,))
        return None, []

    def ref(self, group: str, now: float | None = None) -> int | None:
        now = self.clock() if now is None else now
        cands = [(pid, h) for pid, (g, t, h) in self.seen.items() if g == group and now - t <= self.FRESH]
        if not cands:
            return None
        hotes = [pid for pid, h in cands if h]
        if len(hotes) == 1:
            return hotes[0]
        return min(pid for pid, _h in cands)

    def members(self, group: str, now: float | None = None) -> list[int]:
        now = self.clock() if now is None else now
        return [pid for pid, (g, t, _h) in self.seen.items() if g == group and now - t <= self.FRESH]

    def vent_msg(self, pid: int, hote: bool, msg: dict) -> tuple[dict | None, list[int], dict | None]:
        """(message à relayer aux autres, destinataires, réponse à l'émetteur)."""
        v = valid_vent(msg)
        if v is None:
            return None, [], None
        now = self.clock()
        if now - self.last_vent.get(pid, float("-inf")) < self.VENT_MIN_GAP:
            return None, [], None
        self.last_vent[pid] = now
        g = v["group"]
        reg = self.vent.get(g)
        if v["init"]:
            take = reg is None or (bool(hote) and reg["kind"] == "init" and not reg["hote"])
            if not take:
                if reg is not None and (reg["x"], reg["y"], reg["on"]) != (v["x"], v["y"], v["on"]):
                    return None, [], self._out(reg)          # le nouveau venu se range au registre
                return None, [], None
            kind = "init"
        else:
            kind = "change"
            if reg is not None and (reg["x"], reg["y"], reg["on"]) == (v["x"], v["y"], v["on"]):
                return None, [], None
        seq = (reg["seq"] + 1) if reg else 1
        self.vent[g] = reg = {"x": v["x"], "y": v["y"], "on": v["on"], "seq": seq, "kind": kind,
                              "hote": bool(hote), "from": pid}
        # à TOUS les membres, l'émetteur compris : chacun garde le plus grand seq (deux Requiem joués en même
        # temps : tout le monde finit sur le second arrivé au serveur, jamais chacun sur celui de l'autre)
        dest = sorted(set(self.members(g, now)) | {pid})
        return self._out(reg), dest, None

    @staticmethod
    def _out(reg: dict) -> dict:
        return {"type": "vent", "x": reg["x"], "y": reg["y"], "on": reg["on"], "seq": reg["seq"],
                "from": reg["from"]}

    def vent_courant(self, group: str) -> dict | None:
        reg = self.vent.get(_group(group))
        return self._out(reg) if reg else None


# ---------------------------------------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------------------------------------
class MondeSync:
    PERIOD = 1.0            # s entre deux « monde »
    TTL = 2.5               # s : référence trop vieille -> ignorée
    RUN_DT = 0.4            # s entre deux lectures pour juger que l'heure avance
    RUN_MAX = 5.0           # saut d'heure au-delà duquel ce n'est plus « l'heure qui tourne »
    RUN_KEEP = 1.5          # s : « tourne » reste vrai ce temps après la dernière avance vue
    TOL = 3.0               # écart d'heure toléré (≈ 5 s de jeu en mer, 12 minutes de l'horloge du jeu)
    GAP = 5.0               # s entre deux recalages d'heure
    MIDNIGHT = 359.9        # on laisse le jeu passer minuit lui-même (jour + 1, DayProc)
    DATE_MARGIN = 5.0       # jour recopié seulement loin de minuit (chez les deux)
    WIND_STABLE = 0.4       # s : un vent local doit être lu deux fois avant de partir
    W_GAP = 10.0            # s entre deux recopies de la météo
    W_SETTLE = 0.2          # s entre les deux temps de la recopie (plusieurs images)
    W_AHEAD = 2.0           # prochain tirage repoussé de 2 unités pendant la recopie (≈ 3 s en mer)
    W_TOL = 0.5
    W_DIFF = 4.0            # s d'écart continu avant une recopie (deux PC changent d'étape à ±TOL près)
    W_MARGIN = 0.5          # le prochain tirage doit rester devant l'heure pendant le 2e temps
    JUMP_MIN = 6.0          # saut d'heure local (au-delà de l'avance normale) qui part comme « heure »
    JUMP_HOLD = 20.0        # s : l'auteur du saut n'est pas recalé tant que la référence ne l'a pas suivi
    JUMP_TTL = 20.0         # s : un saut pas encore envoyé, ou reçu et pas encore appliqué, est oublié
    RATE = MondeServeur.RATE
    H_TTL = 3.0             # s : horloge du serveur trop vieille (le serveur répond à chaque « monde », 1 par s)
    HOLD_TOL = 0.25         # heure tenue (arrêtée chez un autre) : écart toléré (≈ 0,4 s de jeu, 1 minute du jeu)
    FIG_DT = 1.0            # s d'heure immobile (jeu libre, salle stable) pour se dire « figé »

    def __init__(self, time_on: bool = False, wind: bool = False, weather: bool = False,
                 minigames: bool = False, group: str = "default", clock=time.time):
        self.time_on, self.wind_on, self.weather_on, self.mg_on = time_on, wind, weather, minigames
        self.group = _group(group)
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.cur: dict | None = None
        self.ref: dict | None = None          # dernier « monde » de la référence (+ _t)
        self.send_t = float("-inf")
        self.run_last: tuple[float, float] | None = None   # (heure du jeu, heure réelle)
        self.run_t = float("-inf")             # dernière avance vue
        self.fix_t = float("-inf")
        self.date_t = float("-inf")
        # saut d'heure (Chant du temps qui passe) : vu chez nous / reçu (référence)
        self.j_prev: tuple | None = None       # (heure, zone, temps qui passe, heure réelle) de la lecture d'avant
        self.j_out: float | None = None        # heure réelle du saut vu chez nous, à envoyer
        self.j_hold = float("-inf")            # pas de recalage avant (sauf heures déjà proches)
        self.j_in: tuple | None = None         # (heure, jour, reçu à) à appliquer (référence)
        # horloge commune (serveur) et « figé » chez nous
        self.h: dict | None = None             # dernière horloge reçue (+ _t)
        self.h_vue = False                     # le serveur a l'horloge commune (sinon : référence, comme avant)
        self.fig: bool | None = None           # l'heure est arrêtée chez nous (None : pas encore vu)
        self.f_prev: tuple | None = None       # (heure, heure réelle, zone) de la lecture d'avant
        self.f_still = 0.0                     # heure réelle depuis laquelle l'heure ne bouge plus
        self.tenue = False                     # heure tenue (arrêtée chez un autre) : la note part une fois
        # vent
        self.v_known: tuple | None = None      # dernier vent vu, envoyé ou écrit
        self.v_prev: tuple | None = None
        self.v_prev_t = 0.0
        self.v_reg: tuple | None = None        # registre du serveur
        self.v_seq = 0                         # plus grand seq du registre reçu
        self.v_init_sent = False
        self.v_req_n: int | None = None        # mod natif (WWMPVEN1) : compteur des Requiem déjà vu
        self.v_mien: tuple | None = None       # direction de notre dernier Requiem (son écho n'est pas réappliqué)
        self.v_seq_fait = 0                    # plus grand seq du registre déjà appliqué (une fois chacun)
        self.v_natif = False                   # zone WWMPVEN1 trouvée (Requiem relevé par le jeu)
        # météo
        self.w_phase: float | None = None
        self.w_fix_t = float("-inf")
        self.w_diff_t: float | None = None     # début de l'écart de météo en cours
        # mini-jeu local
        self.minijeu = 0
        self.n = {"time": 0, "date": 0, "wind_out": 0, "wind_in": 0, "weather": 0, "jump_out": 0, "jump_in": 0,
                  "hold": 0}

    @property
    def enabled(self) -> bool:
        return self.time_on or self.wind_on or self.weather_on

    # --- files ------------------------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def _send(self, msg: dict) -> None:
        msg["group"] = self.group
        with self.lock:
            self.out = (self.out + [msg])[-16:]

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        if not isinstance(msg, dict) or (me is not None and msg.get("from") == me):
            return
        m = dict(msg, _t=self.clock() if now is None else now)
        with self.lock:
            self.inbox = (self.inbox + [m])[-32:]

    def reset_network(self) -> None:
        with self.lock:
            self.inbox.clear()
            self.out.clear()
        self.ref = None
        self.v_reg = None
        self.v_seq = 0
        self.v_init_sent = False
        self.v_seq_fait = 0
        self.h = None
        self.h_vue = False
        self.w_phase = None
        self.j_out = self.j_in = None
        self.j_hold = float("-inf")

    def _drain(self) -> None:
        with self.lock:
            inbox, self.inbox = self.inbox, []
        for m in inbox:
            t = m.get("type")
            if t == "monde":
                v = valid_monde(m)
                if v is not None and v["group"] == self.group:
                    self.ref = dict(v, _t=m["_t"])
            elif t == "horloge" and self.time_on:
                v = valid_horloge(m)
                if v is None:
                    continue
                avant = self.h
                self.h = dict(v, _t=m["_t"])
                self.h_vue = True
                if avant is not None and avant["run"] != v["run"]:
                    if v["run"]:
                        self._note("heure : elle repart pour tout le monde")
                    else:
                        qui = ", ".join(v["figes"])
                        self._note("heure : arrêtée pour tout le monde" +
                                   (f" ({qui} : lieu où le temps ne passe pas)" if qui else ""))
            elif t == "heure" and self.time_on:
                v = valid_heure(dict(m, group=self.group))
                if v is not None:
                    self.j_in = (v["t"], v["d"], m["_t"])
            elif t == "vent" and self.wind_on:
                v = valid_vent(dict(m, group=self.group, init=False))
                seq = m.get("seq")
                if v is None or not _int(seq) or seq <= self.v_seq:
                    continue                    # plus ancien que le registre déjà reçu
                self.v_seq = seq
                self.v_reg = (v["x"], v["y"], v["on"])

    # --- étape (thread de travail) ----------------------------------------------------------------------
    def step(self, mem, now: float, stage: str | None, free: bool = True, settled: bool = False,
             can: bool = True, connected: bool = True, minijeu: int = 0, zone=None, mb=None) -> None:
        """Un tour. ``mb`` : la boîte aux lettres (mod natif : zone WWMPVEN1 du vent), ou None."""
        self.minijeu = minijeu if _int(minijeu) else 0
        if not self.enabled:
            return
        self._drain()
        if mem is None:
            return
        try:
            cur = lire(mem)
        except Exception:
            cur = None
        if cur is None:
            self.cur = None
            self.run_last = None
            self.w_phase = None
            self.j_prev = None
            self.f_prev = None
            return
        cur["sea"] = stage == "sea"
        cur["run"] = self._running(cur["t"], now)
        self.cur = cur
        zone = stage if zone is None else zone
        if self.time_on:
            self._jump_seen(cur, now, zone)
            self._fig_vu(cur, now, zone, free and settled)
        self.j_prev = (cur["t"], zone, cur["tp"], now)
        if now - self.send_t >= self.PERIOD:
            self.send_t = now
            msg = {"type": "monde", "t": round(cur["t"], 3), "d": cur["d"], "run": cur["run"], "tp": cur["tp"],
                   "sea": cur["sea"],
                   "w": list(cur["w"]) if self.weather_on and cur["sea"] and valid_weather(cur["w"]) else None}
            if self.time_on:
                msg["fig"] = self.fig
            self._send(msg)
        ok = free and settled and can and not self.minijeu
        ref = self.ref
        if ref is not None and not 0 <= now - ref["_t"] <= self.TTL:
            ref = self.ref = None
        h = self.h
        if h is not None and not 0 <= now - h["_t"] <= self.H_TTL:
            h = None
        if self.time_on:
            self._jump_step(mem, now, cur, ok, connected, zone)
        if self.time_on and self.h_vue:
            if h is not None:
                self._horloge_step(mem, now, cur, h, ok, zone)
        elif self.time_on and ref is not None:
            self._time_step(mem, now, cur, ref, ok)         # ancien serveur : la référence, comme avant
        if self.wind_on:
            self._wind_step(mem, now, cur, ok, connected, mb)
        if self.weather_on:
            self._weather_step(mem, now, cur, ref, ok)
        else:
            self.w_phase = None

    def _running(self, t: float, now: float) -> bool:
        last = self.run_last
        if last is None:
            self.run_last = (t, now)
            return False
        if now - last[1] >= self.RUN_DT:
            dt = circ(t, last[0])
            if 0.0 < dt < self.RUN_MAX:
                self.run_t = now
            elif dt != 0.0:
                self.run_t = float("-inf")          # saut (chant, écriture, chargement) : on réobserve
            self.run_last = (t, now)
        return now - self.run_t <= self.RUN_KEEP

    # --- saut d'heure (Chant du temps qui passe) ------------------------------------------------------
    def _jump_seen(self, cur: dict, now: float, zone) -> None:
        """Saut en avant de l'heure chez nous, en mer, sans changer de salle (pas un chargement de stage à
        heure imposée) et au-delà de l'avance normale du temps écoulé : Chant du temps qui passe. Nos propres
        écritures remettent j_prev à la valeur écrite : elles ne comptent jamais."""
        prev = self.j_prev
        if prev is None or prev[1] != zone or not (prev[2] and cur["tp"] and cur["sea"]):
            return
        dt = circ(cur["t"], prev[0])
        if dt - max(0.0, now - prev[3]) * FPS * 0.02 >= self.JUMP_MIN:
            self.j_out = now

    def _jump_step(self, mem, now: float, cur: dict, ok: bool, connected: bool, zone) -> None:
        if self.j_out is not None:
            if now - self.j_out > self.JUMP_TTL:
                self.j_out = None
            elif ok and connected:
                # heure ACTUELLE (l'événement du chant est fini) : la référence l'avance de l'âge du message
                self._send({"type": "heure", "t": round(cur["t"], 3), "d": cur["d"]})
                self.j_out = None
                self.j_hold = now + self.JUMP_HOLD
                self.n["jump_out"] += 1
                self._note(f"heure : ton saut ({hhmm(cur['t'])}, Chant du temps qui passe) est envoyé à l'hôte")
        ji = self.j_in
        if ji is None:
            return
        if not 0 <= now - ji[2] <= self.JUMP_TTL:
            self.j_in = None
            return
        if not (ok and cur["run"] and cur["tp"]) or self.w_phase is not None:
            return                              # mêmes gardes que le recalage : on attend
        want = (ji[0] + (now - ji[2]) * FPS * 0.02) % DAY
        d = circ(want, cur["t"])
        if d <= self.TOL:
            self.j_in = None                    # déjà là (ou en arrière : un chant n'avance que)
            return
        target = want if want > cur["t"] else self.MIDNIGHT       # minuit : le jeu le passe, on finit après
        if abs(target - cur["t"]) <= self.TOL / 2:
            return
        try:
            mem.write(cur["gi"] + GI_TIME, struct.pack(">f", target))
        except Exception:
            return
        if target == want:
            self.j_in = None
        self.fix_t = now
        self.run_last = (target, now)
        self.j_prev = (target, zone, cur["tp"], now)   # notre écriture n'est pas un chant
        self.f_prev = (target, now, zone)              # ni une avance du jeu
        self.n["jump_in"] += 1
        self._note(f"heure : saut d'un autre joueur repris ({hhmm(cur['t'])} -> {hhmm(target)}, Chant du temps "
                   "qui passe)")

    # --- heure ------------------------------------------------------------------------------------------
    def _time_step(self, mem, now: float, cur: dict, ref: dict, ok: bool) -> None:
        if not (ok and cur["run"] and cur["tp"] and ref["run"] and ref["tp"]):
            return
        if self.w_phase is not None or self.fix_t == now:
            return                              # météo en cours de recopie (tirage), ou heure déjà écrite
        want = (ref["t"] + max(0.0, now - ref["_t"]) * FPS * 0.02) % DAY   # avancée de son âge (mTimeAdv)
        d = circ(want, cur["t"])
        if abs(d) <= self.TOL:
            self.j_hold = float("-inf")         # la référence a suivi notre chant
            self._date_step(mem, now, cur, ref, want)
            return
        if now < self.j_hold or self.j_out is not None:
            return                              # notre chant : attendre que la référence le suive
        if now - self.fix_t < self.GAP:
            return
        if d > 0:
            target = want if want > cur["t"] else self.MIDNIGHT    # minuit en avant : le jeu le passe
        else:
            if want > cur["t"]:
                return                                             # minuit en arrière : on attend
            target = want
        if abs(target - cur["t"]) <= self.TOL / 2:
            return
        try:
            mem.write(cur["gi"] + GI_TIME, struct.pack(">f", target))
        except Exception:
            return
        self._ecrit(target, now)
        self.n["time"] += 1
        self._note(f"heure recalée sur celle de l'hôte ({hhmm(cur['t'])} -> {hhmm(target)})")

    def _ecrit(self, target: float, now: float) -> None:
        """Après une écriture de l'heure : ni « l'heure tourne », ni un chant, ni une avance du jeu."""
        self.fix_t = now
        self.run_last = (target, now)
        if self.j_prev is not None:
            self.j_prev = (target,) + self.j_prev[1:3] + (now,)
        if self.f_prev is not None:
            self.f_prev = (target, now, self.f_prev[2])

    def _date_step(self, mem, now: float, cur: dict, ref: dict, want: float, qui: str = "l'hôte") -> None:
        if ref["d"] == cur["d"] or now - self.date_t < self.GAP:
            return
        lo, hi = self.DATE_MARGIN, DAY - self.DATE_MARGIN
        if not (lo <= cur["t"] <= hi and lo <= want <= hi):
            return
        try:
            mem.write(cur["gi"] + GI_DATE, struct.pack(">H", ref["d"]))
        except Exception:
            return
        self.date_t = now
        self.n["date"] += 1
        self._note(f"jour recalé sur celui de {qui} (jour {cur['d']} -> {ref['d']} ; lune, Vaisseau fantôme)")

    # --- horloge commune (serveur) ---------------------------------------------------------------------
    def _fig_vu(self, cur: dict, now: float, zone, libre: bool) -> None:
        """« Figé » chez nous : l'heure ne bouge plus depuis FIG_DT s, jeu libre et salle stable (île à l'heure
        arrêtée, intérieur…) ; elle avance : pas figé. Hors jeu libre (événement, menu, chargement) : la valeur
        d'avant reste. Nos propres écritures ne comptent jamais (``_ecrit``)."""
        p = self.f_prev
        self.f_prev = (cur["t"], now, zone)
        if not libre or p is None or p[2] != zone:
            self.f_still = now
            return
        d = circ(cur["t"], p[0])
        if 0.0 < d < self.RUN_MAX:
            self.fig = False
            self.f_still = now
        elif d == 0.0:
            if now - self.f_still >= self.FIG_DT:
                self.fig = True
        else:
            self.f_still = now                  # saut (chant, chargement à heure imposée) : on réobserve

    def _horloge_step(self, mem, now: float, cur: dict, h: dict, ok: bool, zone) -> None:
        """L'heure commune du serveur : elle avance (personne n'est figé) -> recalage au-delà de TOL, comme avant ;
        elle est arrêtée (un joueur figé) -> heure TENUE à la valeur commune tant que la nôtre avance. Jamais quand
        l'heure est arrêtée chez nous (le jeu décide de son heure dans ces lieux), ni hors jeu libre."""
        if not (ok and self.fig is False and cur["run"]) or self.w_phase is not None or self.fix_t == now:
            return
        want = (h["t"] + max(0.0, now - h["_t"]) * self.RATE) % DAY if h["run"] else h["t"]
        d = circ(want, cur["t"])
        if abs(d) <= (self.TOL if h["run"] else self.HOLD_TOL):
            if abs(d) <= self.TOL:
                self.j_hold = float("-inf")     # le serveur a suivi notre chant
            self._date_step(mem, now, cur, h, want, "tout le monde")
            if h["run"]:
                self.tenue = False
            return
        if now < self.j_hold or self.j_out is not None:
            return                              # notre chant : attendre que le serveur le reprenne
        date = None
        if d > 0:
            target = want if want > cur["t"] else self.MIDNIGHT    # minuit en avant : le jeu le passe
        else:
            if want > cur["t"]:
                if h["run"]:
                    return                      # minuit en arrière : on attend
                date = h["d"]                   # tenue : notre jeu a passé minuit, pas l'heure commune
            target = want
        if h["run"] and now - self.fix_t < self.GAP:
            return
        if abs(target - cur["t"]) <= (self.TOL if h["run"] else self.HOLD_TOL) / 2 and date is None:
            return
        try:
            mem.write(cur["gi"] + GI_TIME, struct.pack(">f", target))
            if date is not None and date != cur["d"]:
                mem.write(cur["gi"] + GI_DATE, struct.pack(">H", date))
        except Exception:
            return
        self._ecrit(target, now)
        if h["run"]:
            self.n["time"] += 1
            self._note(f"heure recalée sur l'heure commune ({hhmm(cur['t'])} -> {hhmm(target)})")
        else:
            self.n["hold"] += 1
            if not self.tenue:
                self.tenue = True
                qui = ", ".join(h["figes"])
                self._note(f"heure tenue à {hhmm(target)} : arrêtée chez un autre joueur" + (f" ({qui})" if qui else ""))

    # --- vent -------------------------------------------------------------------------------------------
    def _wind_step(self, mem, now: float, cur: dict, ok: bool, connected: bool, mb=None) -> None:
        z = None
        if mb is not None:
            try:
                z = mb.vent_lire()
            except Exception:
                z = None
        if z is not None:
            self.v_natif = True
            self._wind_natif(mb, z, now, cur, ok, connected)
            return
        self._wind_save(mem, now, cur, ok, connected)

    def _wind_natif(self, mb, z: dict, now: float, cur: dict, ok: bool, connected: bool) -> None:
        """Mod natif (zone WWMPVEN1) : seul un VRAI Requiem joué ici part (compteur du jeu, accroche de
        Wind_CommitWind) ; la voile rapide, qui remet le vent dans le sens du bateau à chaque pas, ne compte jamais.
        Un vent reçu (nouveau seq du registre) est appliqué UNE fois, par le jeu (dKyw_tact_wind_set)."""
        n = z["requiem"]
        if self.v_req_n is None:
            self.v_req_n = n                    # Requiem d'avant notre arrivée : déjà dans la sauvegarde (init)
        elif n != self.v_req_n:
            self.v_req_n = n
            v = (z["x"], z["y"], True)
            self.v_known = self.v_reg = self.v_mien = v
            self._send({"type": "vent", "x": v[0], "y": v[1], "on": True, "init": False})
            self.n["wind_out"] += 1
            self._note("vent : ta direction est envoyée aux autres (Requiem des vents)")
            return
        if self.v_known is None:
            self.v_known = vent_de(cur)
        if connected and not self.v_init_sent:
            self.v_init_sent = True
            k = vent_de(cur)
            self._send({"type": "vent", "x": k[0], "y": k[1], "on": k[2], "init": True})
        reg = self.v_reg
        if reg is None or self.v_seq <= self.v_seq_fait:
            return
        if reg == self.v_mien or reg == vent_de(cur):
            self.v_seq_fait = self.v_seq        # l'écho de notre Requiem, ou déjà ce vent : rien à écrire
            return
        if not ok or cur["evt"] not in (0, 0xFF):
            return
        try:
            mb.vent_appliquer(reg[0], reg[1], reg[2])
        except Exception:
            return
        self.v_seq_fait = self.v_seq
        self.v_known = reg
        self.n["wind_in"] += 1
        self._note("vent : direction d'un autre joueur reprise (Requiem des vents)" if reg[2] else
                   "vent : remis comme avant le Requiem des vents (direction de l'hôte)")

    def _wind_save(self, mem, now: float, cur: dict, ok: bool, connected: bool) -> None:
        """Sans le mod natif (Cemu, mod plus ancien) : tout changement de la sauvegarde part, comme avant."""
        v = vent_de(cur)
        if v != self.v_prev:
            self.v_prev, self.v_prev_t = v, now
        stable = now - self.v_prev_t >= self.WIND_STABLE
        if self.v_known is None:
            if stable:
                self.v_known = v
        elif v != self.v_known and stable and ok:
            # changement local (Requiem des vents) : il part, le dernier qui joue gagne
            self.v_known = v
            self.v_reg = v                     # gardé jusqu'à l'écho du serveur (seq)
            self._send({"type": "vent", "x": v[0], "y": v[1], "on": v[2], "init": False})
            self.n["wind_out"] += 1
            self._note("vent : ta direction est envoyée aux autres (Requiem des vents)")
            return
        if connected and not self.v_init_sent and self.v_known is not None:
            self.v_init_sent = True
            k = self.v_known
            self._send({"type": "vent", "x": k[0], "y": k[1], "on": k[2], "init": True})
        reg = self.v_reg
        if reg is None or reg == v or not ok or cur["evt"] not in (0, 0xFF):
            return
        x, y, on = reg
        fl = ((cur["fl"] & 0x80) | 1) if on else 0
        try:
            mem.write(cur["gi"] + GI_WIND_X, struct.pack(">hh", x, y) if on else struct.pack(">hh", -1, -1))
            mem.write(ENV + E_WIND_X, struct.pack(">hh", x, y) if on else struct.pack(">hh", 0, 0))
            mem.write(ENV + E_WIND_FLAGS, bytes([fl]))
        except Exception:
            return
        self.v_known = self.v_prev = reg
        self.v_prev_t = now
        self.n["wind_in"] += 1
        self._note("vent : direction d'un autre joueur reprise (Requiem des vents)" if on else
                   "vent : remis comme avant le Requiem des vents (direction de l'hôte)")

    # --- météo ------------------------------------------------------------------------------------------
    def _weather_step(self, mem, now: float, cur: dict, ref: dict | None, ok: bool) -> None:
        rw = valid_weather(ref.get("w")) if ref is not None else None
        if rw is not None and rw[4] == 1:
            rw = None                           # tirage en cours chez la référence (une image) : on attend
        if not (ok and rw is not None and ref["sea"] and cur["sea"] and ref["run"] and cur["run"]
                and ref["tp"] and cur["tp"]):
            self.w_phase = None
            return
        lw = cur["w"]
        env = ENV
        if self.w_phase is not None:
            if now - self.w_phase < self.W_SETTLE:
                return
            self.w_phase = None
            if lw[4] != 0 or circ(lw[0], cur["t"]) < self.W_MARGIN:
                # le jeu a bougé entre-temps (fin d'étape, ou heure qui a rattrapé le tirage repoussé : un
                # tirage pendant nos écritures mêlerait son motif à notre compteur) : on recommence plus tard
                return
            chg, fin, cnt, mode, state, pat = rw
            try:
                # état 0 depuis plusieurs images, tirage repoussé : rien ne lit motif ni compteur ici
                mem.write(env + E_W_PATTERN, bytes([pat]))
                mem.write(env + E_W_COUNTER, struct.pack(">i", cnt))
                mem.write(env + E_W_TIME, struct.pack(">f", fin))
                mem.write(env + E_W_MODE, bytes([mode]))
                mem.write(env + E_W_STATE, bytes([state]))
                mem.write(env + E_W_CHANGE, struct.pack(">f", chg))
            except Exception:
                return
            self.w_fix_t = now
            self.w_diff_t = None
            self.n["weather"] += 1
            self._note("météo de la mer recalée sur celle de l'hôte")
            return
        if self._same_weather(lw, rw):
            self.w_diff_t = None
            return
        if self.w_diff_t is None:
            self.w_diff_t = now
        if now - self.w_diff_t < self.W_DIFF or now - self.w_fix_t < self.W_GAP or self.fix_t == now:
            return                              # écart bref, recopie récente, ou heure écrite à ce tick
        # 1er temps : prochain tirage repoussé, puis automate à l'arrêt (état 0). Chaque valeur écrite est
        # valable seule ; le 2e temps attend plusieurs images pour qu'aucune image ne lise un motif nouveau
        # avec un compteur ancien (lecture hors du tableau -> mode > 5 -> assertion du jeu).
        try:
            mem.write(env + E_W_CHANGE, struct.pack(">f", (cur["t"] + self.W_AHEAD) % DAY))
            mem.write(env + E_W_STATE, b"\x00")
        except Exception:
            return
        self.w_phase = now

    def _same_weather(self, lw, rw) -> bool:
        if tuple(lw[2:]) != tuple(rw[2:]):
            return False
        return abs(circ(lw[0], rw[0])) <= self.W_TOL and abs(circ(lw[1], rw[1])) <= self.W_TOL

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        parts = []
        if self.cur is not None:
            parts.append(f"{hhmm(self.cur['t'])} jour {self.cur['d']}")
        if self.time_on:
            parts.append(f"heure {self.n['time']} recalage(s)" + (f", jour {self.n['date']}" if self.n["date"] else ""))
            if self.h_vue and self.h is not None:
                parts.append("heure commune " + ("en marche" if self.h["run"] else
                                                 "arrêtée (" + (", ".join(self.h["figes"]) or "personne") + ")")
                             + f", tenue {self.n['hold']} fois")
            parts.append("ici : " + {True: "heure arrêtée", False: "l'heure avance", None: "pas encore vu"}[self.fig])
        if self.wind_on:
            parts.append(f"vent {self.n['wind_out']} envoyé(s) / {self.n['wind_in']} repris"
                         + (" (Requiem relevé par le mod natif)" if self.v_natif else ""))
        if self.weather_on:
            parts.append(f"météo {self.n['weather']} recopie(s)")
        if self.minijeu:
            parts.append(f"mini-jeu en cours ({libelle_minijeu(self.minijeu)}) : corrections en attente")
        parts.append("référence : " + ("reçue" if self.ref else "aucune (tu es l'hôte, ou personne)"))
        return ", ".join(parts)


def hhmm(t: float) -> str:
    m = int(t / 15.0 * 60) % (24 * 60)
    return f"{m // 60:02d}h{m % 60:02d}"


# ---------------------------------------------------------------------------------------------------------
# Mini-jeux des autres (affichage)
# ---------------------------------------------------------------------------------------------------------
class MiniJeuNotice:
    """« X joue : vente aux enchères » au début du mini-jeu d'un autre joueur, « X : fin du mini-jeu » à la fin."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.cur: dict[int, int] = {}

    def observe(self, pid: int, nom: str, state: dict) -> list[str]:
        if not self.enabled or not isinstance(state, dict):
            return []
        mg = state.get("mg")
        mg = mg if _int(mg) and 0 < mg < 256 else 0
        old = self.cur.get(pid, 0)
        if mg == old:
            return []
        if mg:
            self.cur[pid] = mg
            return [f"{nom} joue : {libelle_minijeu(mg)} (rien ne change chez toi)"]
        self.cur.pop(pid, None)
        return [f"{nom} : fin du mini-jeu"]

    def visible(self, state: dict) -> str | None:
        mg = state.get("mg") if self.enabled and isinstance(state, dict) else None
        return libelle_minijeu(mg) if _int(mg) and 0 < mg < 256 else None

    def forget(self, pid: int) -> None:
        self.cur.pop(pid, None)

    def reset(self) -> None:
        self.cur.clear()
