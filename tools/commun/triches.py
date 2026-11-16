"""Triches et options du portage natif ZeldaWWHDRecomp qui faussent la synchro coop (docs/dev/PORTAGE_NATIF.md §8.10,
audit : docs/dev/AUDIT_NATIF.md §3.2).

Ce que fait le portage v0.5.1 (lu dans ses sources, lecture seule ; rien deviné) :

* ``runtime/src/mods/cheats.cpp`` : F1 > Mods > More > Cheats. Les interrupteurs « Infinite … » (``g_infinite``,
  ``set_infinite``) réécrivent la sauvegarde EN HAUT DE CHAQUE IMAGE (``cheats_service``) : vie au max (« Infinite
  health », ligne 491), flèches et bombes au max (« Infinite arrows and bombs »), bourse remplie par le compteur du
  HUD (« Infinite rupees », ``keep_rupees``), jauge d'air remise au max (« Infinite swimming », ``keep_breath``,
  play+0x5B4C), plus « Infinite magic », « Infinite bait and spoils », « Moon jump » et « No fall damage after moon
  jump ». Ces interrupteurs ne sont JAMAIS sauvegardés : ils repartent coupés à chaque démarrage, sauf la variable
  ``WWHD_CHEAT_INFINITE`` (health, magic, ammo, moonjump, nofall, rupees, bags, breath ; cherchés comme
  sous-chaînes, ``parse_env``). Chaque bascule est écrite au journal du jeu : « [cheats] infinite <nom> on|off ».
  Les triches ponctuelles (« Give all items », « 20 hearts, double magic, 5000 rupees », « Fill the bags », chants,
  Triforce, carte/boussole/clé, figurines, objets d'histoire) écrivent « [cheats] applied <bits> in … » (ou
  « [cheats] story item grant … ») ; variables ``WWHD_CHEAT`` et ``WWHD_STORY_ITEM``.
* ``runtime/src/mods/manager.cpp`` : le mod intégré « move-speed » (« Run/swim speed », facteurs 1 à 4) EST
  sauvegardé : ``<réglages>/settings.ini`` ``mod.move-speed.enabled=1`` (``hostui::set``) et
  ``ModManager/profiles.json`` ``profiles[<actif>].builtins["move-speed"]`` (``remember_builtin`` ; appliqué après
  settings.ini au démarrage, ``apply_saved_builtin_preferences``) ; la variable ``WWHD_MOD_MOVE_SPEED`` (atoi != 0)
  l'emporte sur les deux. Bascule au journal : « [mods] run/swim speed on|off » (``mods.cpp`` ``note``).
* Le journal du jeu (``main.cpp`` ``start_log_file``) : ``<données>/captures/wwhd.log``, tronqué à chaque démarrage
  (l'ancien devient ``wwhd-previous.log``), lignes du format ``LOG`` sans horodatage, écrit quand
  ``WWHD_LOG_FILE`` vaut 1 (le lanceur la pose, ``lanceur/natif.py``) ; coupé à 64 Mio.

Ce module (Python pur, sans accès au jeu) : la liste des triches qui gênent la coop et ce qu'elles cassent, la
lecture des variables d'environnement, le suivi du journal du jeu (``SuiviJournal`` : lecture seule, par morceaux)
et la validation du champ ``triches`` (client -> serveur -> lobby ; un ancien serveur ignore le message).
"""
from __future__ import annotations

import os
import re

# id -> (libellé fr, libellé en, ce que ça casse fr, en). Ordre d'affichage.
GENANTES: dict[str, tuple[str, str, str, str]] = {
    "vie": ("vie infinie", "infinite health",
            "le deathlink ne tue plus ce joueur et la vie partagée est faussée (vie remise au max à chaque image)",
            "deathlink no longer kills this player and shared health is skewed (health refilled every frame)"),
    "rubis": ("rubis infinis", "infinite rupees",
              "bourse commune : chaque recharge part comme un gain pour tout le groupe",
              "shared wallet: every refill goes out as a gain for the whole group"),
    "munitions": ("flèches et bombes infinies", "infinite arrows and bombs",
                  "flèches et bombes communes : chaque recharge part comme un gain pour tout le groupe",
                  "shared arrows and bombs: every refill goes out as a gain for the whole group"),
    "nage": ("nage infinie", "infinite swimming",
             "la jauge d'air de la barre en jeu reste pleine et affichée",
             "the in-game bar's air meter stays full and shown"),
    "saut_lune": ("saut lunaire", "moon jump",
                  "Link vole sans vitesse lue : son fantôme saute chez les autres",
                  "Link flies with no readable speed: his ghost jumps on the others' screens"),
    "sans_chute": ("pas de dégâts de chute", "no fall damage",
                   "vie partagée et deathlink : les chutes ne coûtent plus rien à ce joueur",
                   "shared health and deathlink: falls no longer cost this player anything"),
    "objets": ("triches d'objets (ponctuelles)", "item cheats (one-shot)",
               "objets, rubis, chants, Triforce… donnés par la triche partent chez tout le groupe (inventaire commun)",
               "items, rupees, songs, Triforce… given by the cheat go to the whole group (shared inventory)"),
    "vitesse": ("vitesse de course/nage (Run/swim speed)", "run/swim speed",
                "Link va plus vite que sa vitesse lue : les fantômes lissés reculent un peu au freinage",
                "Link moves faster than his readable speed: smoothed ghosts step back a little when braking"),
}
IDS = tuple(GENANTES)
# sans effet sur la synchro (magie actuelle et compteurs des sacs : à chacun) : connues, jamais signalées
SANS_EFFET = ("magie", "sacs")

# WWHD_CHEAT_INFINITE (cheats.cpp parse_env, strstr) -> id
ENV_INFINIS = (("health", "vie"), ("magic", "magie"), ("ammo", "munitions"), ("moonjump", "saut_lune"),
               ("nofall", "sans_chute"), ("rupees", "rubis"), ("bags", "sacs"), ("breath", "nage"))
ENV_INFINI = "WWHD_CHEAT_INFINITE"
ENV_PONCTUEL = "WWHD_CHEAT"
ENV_HISTOIRE = "WWHD_STORY_ITEM"
ENV_HISTOIRE_OK = "WWHD_STORY_ITEM_ACK"
ENV_VITESSE = "WWHD_MOD_MOVE_SPEED"
ENV_LOG = "WWHD_LOG_FILE"
ENV_REGLAGES = "WWHD_SETTINGS"
PONCTUELS = ("items", "sword", "stats", "songs", "triforce", "dungeon", "key", "bags", "figurines")
HISTOIRE = ("nayru", "din", "farore", "sword")
# variables que « Les désactiver » retire de l'environnement du jeu
VARIABLES = (ENV_INFINI, ENV_PONCTUEL, ENV_HISTOIRE, ENV_HISTOIRE_OK, ENV_VITESSE)

# journal du jeu : noms de set_infinite (cheats.cpp:378-380) -> id
NOMS_JOURNAL = {"health": "vie", "magic": "magie", "ammo": "munitions", "moon jump": "saut_lune",
                "no fall damage": "sans_chute", "rupees": "rubis", "bait and spoils": "sacs", "swimming": "nage"}
RE_INFINI = re.compile(r"^\[cheats\] infinite (.+) (on|off)$")
RE_APPLIQUE = re.compile(r"^\[cheats\] applied (\d+) in ")
RE_HISTOIRE = re.compile(r"^\[cheats\] story item grant ")
RE_VITESSE = re.compile(r"^\[mods\] run/swim speed (on|off)$")

# client : WWMP_NATIF_JOURNAL (chemin du journal du jeu), WWMP_NATIF_DEBUT (heure du lancement du jeu, time.time()),
# WWMP_NATIF_TRICHES (ids actives par l'environnement du jeu, séparés par des virgules ; posée par le lanceur, qui
# peut avoir retiré les variables : elle prime sur l'environnement du client)
ENV_CLIENT_JOURNAL = "WWMP_NATIF_JOURNAL"
ENV_CLIENT_DEBUT = "WWMP_NATIF_DEBUT"
ENV_CLIENT_TRICHES = "WWMP_NATIF_TRICHES"

MAX_IDS = 16
MARGE_DEBUT = 2.0          # s : un journal modifié avant le lancement du jeu (moins cette marge) est l'ancien
MORCEAU = 1 << 20          # octets lus au plus par appel de SuiviJournal.lire


def _atoi(v: str | None) -> int:
    """``atoi`` de la libc : blancs, signe, chiffres ; le reste ignoré ; 0 si rien."""
    m = re.match(r"\s*([+-]?\d+)", v or "")
    try:
        return int(m.group(1)) if m else 0
    except ValueError:
        return 0


def depuis_env(env) -> dict[str, str]:
    """Triches qu'un jeu lancé avec ``env`` aura dès le démarrage : {id: variable}. Toutes, gênantes ou non."""
    out: dict[str, str] = {}
    e = env.get(ENV_INFINI)
    if e is not None:
        for nom, ident in ENV_INFINIS:
            if nom in e:
                out[ident] = ENV_INFINI
    e = env.get(ENV_PONCTUEL)
    if e is not None and any(n in e for n in PONCTUELS):
        out["objets"] = ENV_PONCTUEL
    e = env.get(ENV_HISTOIRE)
    if e is not None and any(n in e for n in HISTOIRE) and env.get(ENV_HISTOIRE_OK) == "1":
        out.setdefault("objets", ENV_HISTOIRE)
    if env.get(ENV_VITESSE) is not None and _atoi(env.get(ENV_VITESSE)) != 0:
        out["vitesse"] = ENV_VITESSE
    return out


def genantes(ids) -> list[str]:
    """Les ids connus et gênants de ``ids``, dans l'ordre d'affichage."""
    s = set(ids or ())
    return [i for i in IDS if i in s]


def normaliser(v) -> list[str] | None:
    """Champ ``triches`` reçu (client -> serveur, serveur -> lobby) : liste d'ids gênants connus, None si absent ou
    illisible. Les ids inconnus (outils plus récents) sont ignorés."""
    if not isinstance(v, list) or len(v) > MAX_IDS:
        return None
    return genantes(x for x in v if isinstance(x, str) and len(x) <= 24)


def libelle(ident: str, en: bool = False) -> str:
    g = GENANTES.get(ident)
    return (g[1] if en else g[0]) if g else ident


def casse(ident: str, en: bool = False) -> str:
    g = GENANTES.get(ident)
    return (g[3] if en else g[2]) if g else ""


def texte(ids, en: bool = False) -> str:
    """« vie infinie (le deathlink …) ; rubis infinis (…) » pour les ids gênants de ``ids``."""
    return " ; ".join(f"{libelle(i, en)} ({casse(i, en)})" for i in genantes(ids))


def appliquer_ligne(actives: set, ligne: str) -> bool:
    """Une ligne du journal du jeu ; ``actives`` modifié sur place. True si la ligne parle d'une triche."""
    ligne = ligne.rstrip("\r\n")
    m = RE_INFINI.match(ligne)
    if m:
        ident = NOMS_JOURNAL.get(m.group(1))
        if ident is None:
            return False
        (actives.add if m.group(2) == "on" else actives.discard)(ident)
        return True
    m = RE_APPLIQUE.match(ligne)
    if m:
        if int(m.group(1)) > 0:
            actives.add("objets")           # ponctuelle : utilisée dans cette partie, reste signalée
        return True
    if RE_HISTOIRE.match(ligne):
        actives.add("objets")
        return True
    m = RE_VITESSE.match(ligne)
    if m:
        (actives.add if m.group(1) == "on" else actives.discard)("vitesse")
        return True
    return False


class SuiviJournal:
    """Suit ``captures/wwhd.log`` du jeu en LECTURE SEULE, par morceaux (jamais tout relu) : l'état des triches de
    CE démarrage du jeu. ``graine`` : triches actives par l'environnement du jeu (jamais écrites au journal).

    * journal plus ancien que le lancement (``debut`` - MARGE_DEBUT) : celui de la partie précédente, ignoré (le
      jeu le renomme en wwhd-previous.log au démarrage) ;
    * fichier remplacé (autre identité) ou raccourci : nouveau démarrage du jeu, état repris de la graine ;
    * une ligne coupée en fin de lecture attend la suite."""

    def __init__(self, chemin: str, debut: float = 0.0, graine=()):
        self.chemin, self.debut = chemin, float(debut or 0.0)
        self.graine = frozenset(graine or ())
        self.actives: set[str] = set(self.graine)
        self.pos, self.reste, self.ident = 0, b"", None
        self.lu = False                      # un journal de ce démarrage a été trouvé

    def lire(self, morceau: int = MORCEAU) -> bool:
        """Lit ce qui a été ajouté. True si l'ensemble des triches actives a changé."""
        avant = set(self.actives)
        try:
            st = os.stat(self.chemin)
        except OSError:
            return False
        if self.debut and st.st_mtime < self.debut - MARGE_DEBUT:
            return False
        ident = (st.st_dev, st.st_ino, getattr(st, "st_birthtime", None) or st.st_ctime) \
            if os.name != "nt" else (st.st_dev, st.st_ino)
        if ident != self.ident or st.st_size < self.pos:
            self.ident, self.pos, self.reste = ident, 0, b""
            self.actives = set(self.graine)
        self.lu = True
        if st.st_size > self.pos:
            try:
                with open(self.chemin, "rb") as f:
                    f.seek(self.pos)
                    brut = f.read(max(1, int(morceau)))
            except OSError:
                return self.actives != avant
            self.pos += len(brut)
            lignes = (self.reste + brut).split(b"\n")
            self.reste = lignes.pop()
            if len(self.reste) > 8192:          # ligne démesurée : jetée (le format LOG en fait < 4 Kio)
                self.reste = b""
            for b in lignes:
                appliquer_ligne(self.actives, b.decode("utf-8", "replace"))
        return self.actives != avant

    def vue(self) -> list[str]:
        return genantes(self.actives)


def suivi_client(env) -> SuiviJournal | None:
    """Client : le suivi du journal du jeu si le lanceur en a donné le chemin (portage natif), sinon None."""
    chemin = env.get(ENV_CLIENT_JOURNAL) or ""
    if not chemin:
        return None
    try:
        debut = float(env.get(ENV_CLIENT_DEBUT) or 0.0)
    except ValueError:
        debut = 0.0
    return SuiviJournal(chemin, debut, graine_client(env))


def graine_client(env) -> set[str]:
    """Client : triches de l'environnement du JEU (``WWMP_NATIF_TRICHES`` posée par le lanceur ; sinon celui du
    client, hérité du même lanceur)."""
    v = env.get(ENV_CLIENT_TRICHES)
    if v is not None:
        return {x for x in v.split(",") if x in GENANTES or x in SANS_EFFET}
    return set(depuis_env(env))
