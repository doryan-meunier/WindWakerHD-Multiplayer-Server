"""Latence des effets du monde (jarres, herbe, monstres, interrupteurs) : cadences et mesure.

Voir docs/etudes/latence.md. Ce module ne lit ni n'écrit rien dans le jeu : il porte

  - les cadences « rapides » (défaut) et « prudentes » (--latence-prudente : celles d'avant) ;
  - le réveil anticipé de la boucle du client quand un effet à appliquer vient d'arriver (borné :
    jamais avant MIN_TICK après le début du tick précédent, au plus REVEILS_MAX par seconde) ;
  - la mesure affichée par le client (lecture seule) : durée d'un tour de boucle, délai
    « détection -> envoi » de nos effets et « réception -> application » des effets reçus.

Le délai de bout en bout d'un effet est : détection chez l'émetteur (un tour de boucle au plus)
+ confirmation (« envoi ») + réseau (ping / 2 par sens) + application chez le receveur
(« application ») + une image du jeu. Chaque client n'affiche que ses deux moitiés.

Lot L1 (docs/etudes/rollback_netcode.md §4, A1 et A2) :

  - les coups (``player_hit``, ``saisie``, ``pvp_coup``) et un ORDRE nouveau d'un Hanteur (``hlaisse`` dont l'élan ou
    l'ordre a changé ; pas ses positions, qui arrivent 20 fois par seconde) réveillent aussi la boucle, avec leur propre
    budget (``REVEILS_COUPS_MAX`` par seconde) : une rafale de jarres ne retarde jamais un coup, et inversement ;
  - mesures en lecture seule, dans la ligne « Latence » et le fichier d'état (lanceur) : âge du fantôme affiché et des
    marionnettes (``ages``), délai d'un ``player_hit`` (file -> envoi chez l'autorité, réception -> CALL 9 chez la
    victime), délai d'un ordre (réception -> CALL 10 chez l'autorité), aller-retour.
"""
from __future__ import annotations

import statistics
import sys
import time

BUDGET = 0.30          # s : budget de bout en bout d'un effet courant en réseau local (tests hors ligne)
MIN_TICK = 0.035       # s : jamais deux débuts de tick plus rapprochés (réveil anticipé). PLUS d'une image du
                       # jeu (IMAGE) : deux relevés de suite ne voient jamais la même image, les gardes comptées en
                       # relevés (batons.FREE_SCANS, fantôme de bateau recréé après 3 relevés) gardent leur sens
REVEILS_MAX = 6        # réveils anticipés au plus par seconde (au pire : hz + REVEILS_MAX ticks/s)
MIN_CONFIRM = 0.05     # s : écart minimal entre deux lectures de la sauvegarde (un bit local est confirmé par deux)
IMAGE = 0.033          # s : une image du jeu (30 images/s), comptée dans l'estimation
# messages dont l'application attend le prochain tick de la boucle : leur arrivée la réveille
REVEIL_TYPES = frozenset({"break", "drop", "herbe", "tsw", "progress", "magma", "hit",
                          "player_hit", "saisie", "pvp_coup", "hlaisse"})
# … parmi eux, les coups et les ordres (lot L1, A2) : budget de réveils à part, ``hlaisse`` filtré (ordre nouveau)
COUPS_TYPES = frozenset({"player_hit", "saisie", "pvp_coup", "hlaisse"})
REVEILS_COUPS_MAX = 4  # réveils « coup » au plus par seconde (au pire : hz + REVEILS_MAX + REVEILS_COUPS_MAX ticks/s)
HLAISSE_MEMO = 16      # monstres possédés retenus pour reconnaître un ordre nouveau
AGES_GARDE = 5.0       # s : âges mesurés (fantôme, marionnettes) gardés au plus ; au-delà, « pas de mesure »
HERBE_SCAN = 0.1       # s entre deux relevés de l'herbe (prudent : HerbeSync.SCAN, 0,25 s)
SWITCH_HZ = 10         # relevés par seconde des interrupteurs et de la progression (prudent : 5)
SWITCH_HZ_PRUDENT = 5
NOMS = {"jarres": "jarres", "herbe": "herbe", "tsw": "interrupteurs", "progress": "progression",
        "portes": "portes et obstacles"}


def ordre_hlaisse(msg) -> tuple | None:
    """(élan, séquence de l'ordre) d'un ``hlaisse`` : ce qui fait d'un ``hlaisse`` un ORDRE nouveau. None : illisible."""
    if not isinstance(msg, dict):
        return None
    atk = msg.get("atk")
    o = msg.get("ord")
    seq = o[0] if isinstance(o, (list, tuple)) and len(o) == 2 and isinstance(o[0], int) else None
    return (atk if isinstance(atk, int) and not isinstance(atk, bool) else None, seq)


def sans_delai(writer) -> bool:
    """TCP_NODELAY sur la connexion (petits messages envoyés tout de suite, sans attendre l'accusé du
    précédent : algorithme de Nagle). asyncio le pose déjà côté client, pas toujours côté serveur
    (connexions acceptées sous Windows). Jamais fatal."""
    try:
        import socket
        sock = writer.get_extra_info("socket")
        if sock is None:
            return False
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return True
    except Exception:
        return False


def _med(v) -> float | None:
    return statistics.median(v) if v else None


class Latence:
    """État du client. rapide=False : cadences d'avant (--latence-prudente), mesure toujours active."""

    def __init__(self, rapide: bool = True, clock=time.time):
        self.rapide, self.clock = bool(rapide), clock
        self.travail: list[float] = []        # durées de travail des derniers ticks (s)
        self.periodes: list[float] = []       # écarts entre débuts de tick (s)
        self.t_tick: float | None = None
        self.n_ticks = 0                      # tours de boucle depuis le lancement
        self.reveils: list[float] = []        # heures des derniers réveils anticipés
        self.n_reveils = 0
        self.envoi: dict[str, list[float]] = {}     # sorte -> délais détection -> envoi (s)
        self.appli: dict[str, list[float]] = {}     # sorte -> délais réception -> application (s)
        self.marques: dict[str, float] = {}         # sorte -> heure de la plus vieille réception non appliquée
        self.urgent: set[str] = set()               # étapes à faire au prochain tick, hors cadence
        self.horloge = False
        self.reveils_coups: list[float] = []  # heures des derniers réveils « coup » (budget à part)
        self.n_reveils_coups = 0
        self.attente_coup = False             # le réveil en attente vient d'un coup (budget à part)
        self.hlaisse_vus: dict = {}           # clé du monstre possédé -> (élan, séquence) du dernier hlaisse
        self.ages: dict[str, list[float]] = {}  # sorte -> âges mesurés (s) : fantôme affiché, marionnettes

    # --- cadences ---------------------------------------------------------------
    def diviseur(self, hz: int) -> int:
        """Un relevé des interrupteurs / de la progression tous les N ticks."""
        return max(1, int(hz) // (SWITCH_HZ if self.rapide else SWITCH_HZ_PRUDENT))

    def herbe_scan(self, defaut: float) -> float:
        return min(defaut, HERBE_SCAN) if self.rapide else defaut

    # --- réveil anticipé --------------------------------------------------------
    def recu(self, typ, now: float | None = None, msg: dict | None = None) -> bool:
        """Message reçu du serveur : faut-il réveiller la boucle ? Note aussi l'heure (mesure) et
        l'étape à faire hors cadence. Jamais en mode prudent. ``hlaisse`` : seulement un ordre nouveau (élan ou
        séquence d'ordre changés pour ce monstre, ou fin de possession), pas une simple position."""
        if typ not in REVEIL_TYPES:
            return False
        now = self.clock() if now is None else now
        if typ in ("tsw", "progress"):
            self.marques.setdefault(typ, now)
            if self.rapide:
                self.urgent.add(typ)
        if typ == "hlaisse" and not self._ordre_nouveau(msg):
            return False
        if typ in COUPS_TYPES and self.rapide:
            self.attente_coup = True
        return self.rapide

    def _ordre_nouveau(self, msg) -> bool:
        if not isinstance(msg, dict) or not isinstance(msg.get("key"), str):
            return False
        key = msg["key"]
        if msg.get("fin") in (1, True):
            return self.hlaisse_vus.pop(key, None) is not None
        o = ordre_hlaisse(msg)
        avant = self.hlaisse_vus.get(key)
        if key not in self.hlaisse_vus and len(self.hlaisse_vus) >= HLAISSE_MEMO:
            self.hlaisse_vus.pop(next(iter(self.hlaisse_vus)))
        self.hlaisse_vus[key] = o
        # première vue : la laisse commence (pas un ordre) ; ensuite, un élan ou un ordre qui change
        return avant is not None and o != avant

    def prendre(self, etape: str) -> bool:
        """L'étape doit-elle tourner ce tick-ci hors cadence (un message pour elle vient d'arriver) ?"""
        if etape in self.urgent:
            self.urgent.discard(etape)
            return True
        return False

    def reveil_ok(self, now: float | None = None) -> bool:
        """Un réveil anticipé est-il permis maintenant ? (compté s'il l'est)"""
        if not self.rapide:
            return False
        now = self.clock() if now is None else now
        if self.attente_coup:
            # coup ou ordre : son propre budget (une rafale de jarres ne le retarde pas) ; épuisé : budget commun
            self.attente_coup = False
            self.reveils_coups = [t for t in self.reveils_coups if now - t < 1.0]
            if len(self.reveils_coups) < REVEILS_COUPS_MAX:
                self.reveils_coups.append(now)
                self.n_reveils_coups += 1
                self.n_reveils += 1
                return True
        self.reveils = [t for t in self.reveils if now - t < 1.0]
        if len(self.reveils) >= REVEILS_MAX:
            return False
        self.reveils.append(now)
        self.n_reveils += 1
        return True

    # --- mesure -----------------------------------------------------------------
    def tick(self, debut: float, travail: float) -> None:
        if self.t_tick is not None and 0 < debut - self.t_tick < 5.0:
            self.periodes = (self.periodes + [debut - self.t_tick])[-200:]
        self.t_tick = debut
        self.n_ticks += 1
        self.travail = (self.travail + [max(0.0, travail)])[-200:]

    def note_envoi(self, sorte: str, delais) -> None:
        if delais:
            self.envoi[sorte] = (self.envoi.get(sorte, []) + [float(d) for d in delais])[-32:]

    def note_appli(self, sorte: str, delais) -> None:
        if delais:
            self.appli[sorte] = (self.appli.get(sorte, []) + [float(d) for d in delais])[-32:]

    def note_age(self, sorte: str, ages, now: float | None = None) -> None:
        """Âges mesurés (s) : retard entre ce qu'on affiche et le présent estimé de l'autre PC (lecture seule). Datés :
        seuls ceux des AGES_GARDE dernières secondes comptent (``etat``)."""
        v = [float(a) for a in ages if a is not None and 0.0 <= a < 30.0]
        if v:
            now = self.clock() if now is None else now
            self.ages[sorte] = (self.ages.get(sorte, []) + [(now, a) for a in v])[-64:]

    def _ages(self, sorte: str, now: float | None = None) -> list[float]:
        """Âges récents (essai du 2026-11-15 : les 64 derniers étaient gardés sans limite de temps ; l'autre joueur
        parti, le fichier d'état affichait encore, 7 minutes plus tard, ceux de son départ : 1 s de « retard »)."""
        now = self.clock() if now is None else now
        lst = [(t, a) for t, a in self.ages.get(sorte, []) if 0.0 <= now - t <= AGES_GARDE]
        if lst:
            self.ages[sorte] = lst
        else:
            self.ages.pop(sorte, None)
        return [a for _t, a in lst]

    def applique(self, sorte: str, now: float | None = None) -> None:
        """Des effets de cette sorte viennent d'être posés : délai depuis la plus vieille réception."""
        t = self.marques.pop(sorte, None)
        if t is not None:
            now = self.clock() if now is None else now
            if 0 <= now - t < 30.0:
                self.note_appli(sorte, [now - t])

    def oublier(self, sorte: str, now: float | None = None, apres: float = 5.0) -> None:
        """Rien à poser pour cette réception (déjà là, autre salle…) : la marque ne traîne pas."""
        t = self.marques.get(sorte)
        if t is not None and (self.clock() if now is None else now) - t > apres:
            del self.marques[sorte]

    def etat(self, rtt_ms: float | None = None) -> dict:
        """Résumé pour le fichier d'état et l'affichage (durées en ms, None : pas encore mesuré)."""
        def ms(v):
            return None if v is None else round(v * 1000.0, 1)
        fantome, marionnettes = self._ages("fantome"), self._ages("marionnettes")

        out = {"mode": "rapide" if self.rapide else "prudent",
               "periode_ms": ms(_med(self.periodes)), "travail_ms": ms(_med(self.travail)),
               "travail_max_ms": ms(max(self.travail)) if self.travail else None,
               "reveils": self.n_reveils, "envoi_ms": {k: ms(_med(v)) for k, v in self.envoi.items()},
               "appli_ms": {k: ms(_med(v)) for k, v in self.appli.items()},
               # lot L1 (A1) : âges et délais des chaînes de combat, aller-retour avec le serveur
               "rtt_ms": None if rtt_ms is None else round(float(rtt_ms), 1),
               "reveils_coups": self.n_reveils_coups,
               "fantome_ms": ms(_med(fantome)),
               "fantome_max_ms": ms(max(fantome)) if fantome else None,
               "marionnettes_ms": ms(_med(marionnettes)),
               "player_hit_envoi_ms": ms(_med(self.envoi.get("player_hit"))),
               "player_hit_ms": ms(_med(self.appli.get("player_hit"))),
               "ordre_ms": ms(_med(self.appli.get("ordre")))}
        e, a = _med(self.envoi.get("jarres")), _med(self.appli.get("jarres"))
        if e is not None and a is not None:
            # notre moitié d'émetteur + notre moitié de receveur (l'autre PC a la sienne, proche en LAN)
            per = _med(self.periodes) or 0.05
            out["jarres_estime_ms"] = ms(per / 2 + e + (rtt_ms or 0.0) / 1000.0 + a + IMAGE)
        return out

    def ligne(self, rtt_ms: float | None = None) -> str:
        """Ligne d'état du client (lecture seule)."""
        st = self.etat(rtt_ms)

        def f(v) -> str:
            return "…" if v is None else f"{v:.0f} ms"

        s = (f"Latence ({st['mode']}) : boucle {f(st['periode_ms'])} (travail {f(st['travail_ms'])}, "
             + f"max {f(st['travail_max_ms'])})")
        for k in ("jarres", "herbe", "tsw", "progress", "portes"):
            e, a = st["envoi_ms"].get(k), st["appli_ms"].get(k)
            if e is None and a is None:
                continue
            part = []
            if e is not None:
                part.append(f"envoi {f(e)} après détection")
            if a is not None:
                part.append(f"application {f(a)} après réception")
            s += f" ; {NOMS[k]} : " + ", ".join(part)
        if "jarres_estime_ms" in st:
            s += f" ; jarre cassée -> vue chez l'autre en ~{f(st['jarres_estime_ms'])}"
        # lot L1 (A1) : chaînes de combat
        if st["fantome_ms"] is not None:
            s += f" ; fantôme affiché : retard {f(st['fantome_ms'])} (max {f(st['fantome_max_ms'])})"
        if st["marionnettes_ms"] is not None:
            s += f" ; marionnettes : retard {f(st['marionnettes_ms'])}"
        if st["player_hit_envoi_ms"] is not None:
            s += f" ; coups sur un fantôme : envoyés {f(st['player_hit_envoi_ms'])} après la file"
        if st["player_hit_ms"] is not None:
            s += f" ; coups reçus : rejoués {f(st['player_hit_ms'])} après réception"
        if st["ordre_ms"] is not None:
            s += f" ; ordres de Hanteur : joués {f(st['ordre_ms'])} après réception"
        if st["rtt_ms"] is not None:
            s += f" ; aller-retour {f(st['rtt_ms'])}"
        if st["reveils"]:
            s += f" ; {st['reveils']} réveil(s) anticipé(s)"
            if st["reveils_coups"]:
                s += f" dont {st['reveils_coups']} pour des coups"
        return s

    # --- horloge de Windows -----------------------------------------------------
    def horloge_fine(self, on: bool = True) -> bool:
        """Windows : les attentes d'asyncio sont arrondies au pas de l'horloge du système (15,6 ms : un tick
        « à 20 Hz » durait 62 ms). On demande un pas de 1 ms POUR CE PROCESSUS (timeBeginPeriod, comme le font
        les jeux et les lecteurs vidéo ; rendu à la fin). Mode rapide seulement ; sans effet ailleurs."""
        if sys.platform != "win32" or not self.rapide or on == self.horloge:
            return False
        try:
            import ctypes
            mm = ctypes.windll.winmm
            ok = (mm.timeBeginPeriod(1) if on else mm.timeEndPeriod(1)) == 0
        except Exception:
            return False
        if ok:
            self.horloge = on
        return ok
