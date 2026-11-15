"""Joueur robot (docs/etudes/joueur_robot.md) : une instance du jeu, sans joueur humain, sur la machine du serveur.

Le robot fait autorité pour les monstres d'UNE zone (stage:salle) partagée ; partout ailleurs, l'autorité reste celle
d'aujourd'hui (``Server.reassign`` : le joueur présent depuis le plus longtemps, ou le PC préféré du lot S1).

    python tools/wwmp_server.py --robot auto --robot-commande "CMD {hote} {port} {cle}"

``--robot off`` (défaut) : rien de tout cela, le serveur se comporte exactement comme avant.

Règles (choisies par l'utilisateur) :

* **(2) Mélange** : le robot prend la zone où il y a le plus de joueurs humains, ROBOT_MIN (2) au moins. Égalité :
  il garde sa zone si elle en fait partie, sinon la zone partagée depuis le plus longtemps (puis le nom). Il change de
  zone avec une hystérésis : une autre zone doit avoir STRICTEMENT plus de joueurs que la sienne, sans interruption
  pendant DELAI_CHANGER s, et il doit être resté SEJOUR_MIN s dans la sienne ; si la sienne passe sous ROBOT_MIN
  joueurs pendant DELAI_QUITTER s, il la quitte (vers la meilleure autre zone, ou le repos). La première zone est
  prise après DELAI_PREMIER s (pas pour deux joueurs qui se croisent sur une porte).
* **(3) Repos** : aucune zone avec ROBOT_MIN joueurs : le robot reçoit « dors » (son jeu ne fait plus rien) ; au bout de
  ``repos_arret`` s de repos (``--robot-repos``, 300 par défaut ; 0 = tout de suite), son processus est ARRÊTÉ (la
  mémoire est rendue) ; il est relancé dès qu'une zone le demande.
* Une seule instance au plus.

Passation propre (pas de doublon, pas de monstre qui réapparaît) : le robot n'est jamais envoyé ailleurs tant qu'il
détient une autorité. Il entre d'abord dans la zone comme un joueur NON autoritaire : ses monstres y sont des
marionnettes que l'autorité humaine actuelle pilote (instantanés relayés par le serveur, comme pour tout joueur). Il ne
prend l'autorité que lorsqu'il s'annonce prêt (salle chargée), qu'il a reçu un instantané de l'autorité actuelle
depuis son arrivée (ou que la zone n'en produit pas depuis SNAP_ATTENTE s : salle sans monstre), que personne n'est en
cinématique et que la zone est calme depuis CALME_PRISE s (au plus PRISE_MAX s d'attente, puis passation quand même,
comme une passation d'aujourd'hui). Pour repartir : l'autorité est d'abord RENDUE aux humains de la zone (règle
habituelle, A11 compris : leurs marionnettes sont à jour, elles sont relâchées), puis seulement l'ordre de départ part.
Un robot qui plante : sa connexion tombe, ``reassign`` donne la zone à un humain présent (chemin habituel du départ
d'une autorité).

Invisibilité : le robot se présente avec ``role: "robot"`` et la clé du serveur (``cle`` du hello). Il n'est pas un
joueur (``Player.joueur`` est faux) : absent de la liste des joueurs, du lobby, des 8 places, du nombre de Link de la
difficulté, du deathlink, du monde persistant ; son « state » n'est relayé à personne (aucun fantôme) ; seuls les
messages d'une autorité de zone sont acceptés de lui (TYPES_ACCEPTES), tout le reste est ignoré (il ne touche jamais
la progression, les coffres, l'inventaire ni les compteurs).

Python pur, sans dépendance (serveur dédié autonome) : ``ChoixZone`` (règles 2 et 3), ``Gestionnaire`` (processus).
"""
from __future__ import annotations

import hmac
import os
import secrets
import shlex
import subprocess
import sys
import time

MODES = ("off", "auto")
DEFAUT = "off"
ROLE = "robot"
NOM = "robot du serveur"
ROBOT_MIN = 2              # joueurs humains au moins dans une zone pour que le robot la prenne
DELAI_PREMIER = 3.0        # s : une zone partagée l'est depuis… avant que le robot (au repos) y aille
DELAI_CHANGER = 8.0        # s : une zone plus peuplée que la sienne l'est sans interruption depuis… avant de changer
SEJOUR_MIN = 20.0          # s : au moins… dans sa zone avant de partir pour une zone plus peuplée
DELAI_QUITTER = 5.0        # s : sa zone sous ROBOT_MIN joueurs depuis… -> il la quitte
REPONSE_MAX = 5.0          # s : un joueur sans « state » depuis… ne compte pas
SNAP_ATTENTE = 3.0         # s : prêt, aucun instantané de l'autorité depuis son arrivée pendant… : zone sans monstre
CALME_PRISE = 3.0          # s : sans combat dans la zone avant que le robot prenne l'autorité…
PRISE_MAX = 15.0           # s : … au plus (ensuite, passation quand même)
ORDRE_RAPPEL = 5.0         # s : ordre renvoyé tant qu'il n'est pas suivi
TRAJET_MAX = 60.0          # s : envoyé vers une zone, pas prêt après… -> zone mise de côté
ECHEC_PAUSE = 120.0        # s : zone mise de côté (le robot n'y arrive pas) pendant…
SUIVI_PAS = 1.0            # s : position des joueurs de sa zone envoyée au robot (salles chargées autour d'eux)
REPOS_ARRET = 300.0        # s : défaut de --robot-repos
# Jamais pire qu'aujourd'hui : le robot ne prend une zone que s'il y GAGNE quelque chose (Server.robot_gain)
GAIN_RTT = 50.0            # ms : l'autorité joueur a un aller-retour plus long que celui du robot d'au moins…
GIGUE = 25.0               # ms : … ou instable (écart type de ses derniers aller-retours) d'au moins…
BASCULES_MAX = 3           # … ou l'autorité de la zone a changé de joueur au moins… fois…
BASCULES_FENETRE = 120.0   # s : … en…
GAIN_DELAI = 5.0           # s : gain constaté sans interruption depuis… avant la prise
RETOUR_DELAI = 30.0        # s : plus de gain depuis… -> autorité rendue au joueur (hystérésis)
REPRISE_APRES = 60.0       # s : après un retour « plus de gain », pas de reprise de cette zone avant…
ROBOT_RTT_DEFAUT = 2.0     # ms : robot sur la machine du serveur, s'il n'annonce rien
ROBOT_SILENCE = 2.0        # s : robot qui tient une zone sans « state » depuis… -> retour forcé
FPS_MIN = 25.0             # images/s : robot qui rame (rapport « etat ») -> retour forcé
RTT_HIST = 20              # aller-retours gardés par joueur (gigue)
TYPES_ACCEPTES = {"state", "enemies", "epos", "player_hit", "saisie", "tsw", "drop", "pnj", "lift", "cycle", "rare",
                  "ping", "robot", "ground_req"}
DIFFUSION = {"authority", "join", "leave", "regles"}   # messages diffusés aux joueurs que le robot reçoit aussi


def commande_defaut(commande, portage: str | None, tools: str):
    """``--robot-commande`` si elle est donnée ; sinon, avec ``--robot-portage``, le lanceur du dépôt
    (tools/client/robot.py : portage caché + client --robot) s'il est là (absent de l'image Docker du serveur)."""
    if commande:
        return commande
    script = os.path.join(tools, "client", "robot.py")
    if portage and os.path.isfile(script):
        return [sys.executable, script, "--hote", "{hote}", "--port", "{port}", "--portage", portage]
    return None


def normaliser(mode) -> str:
    return mode if mode in MODES else DEFAUT


def cle_valide(attendue: str | None, donnee) -> bool:
    return bool(attendue) and isinstance(donnee, str) and hmac.compare_digest(attendue, donnee[:128])


def nouvelle_cle() -> str:
    return secrets.token_hex(16)


class ChoixZone:
    """Règles 2 (mélange) et 3 (repos), avec hystérésis. ``revoir`` prend, par zone, (joueurs humains présents,
    heure depuis laquelle la zone est partagée) et rend la zone voulue pour le robot, ou None (repos)."""

    def __init__(self):
        self.cible: str | None = None
        self.depuis = 0.0                    # heure à laquelle la cible a été choisie
        self.candidat: str | None = None
        self.candidat_t = 0.0
        self.sous_t: float | None = None     # sa zone est sous ROBOT_MIN depuis…
        self.ecartees: dict[str, float] = {}   # zone -> jusqu'à quand elle est mise de côté (robot pas arrivé)

    def ecarter(self, zone: str, now: float, duree: float = ECHEC_PAUSE) -> None:
        self.ecartees[zone] = now + duree
        if self.cible == zone:
            self.cible, self.sous_t, self.candidat = None, None, None

    def _choisir(self, comptes: dict, exclure: str | None = None) -> tuple[str | None, int]:
        best, kbest = None, None
        for z, (n, depuis) in comptes.items():
            if n < ROBOT_MIN or z == exclure:
                continue
            k = (-n, 0 if z == self.candidat else 1, depuis, z)
            if kbest is None or k < kbest:
                best, kbest = z, k
        return best, (-kbest[0] if kbest else 0)

    def _changer(self, zone: str | None, now: float) -> str | None:
        self.cible, self.depuis, self.sous_t = zone, now, None
        self.candidat = None
        return zone

    def revoir(self, comptes: dict, now: float) -> str | None:
        self.ecartees = {z: t for z, t in self.ecartees.items() if t > now}
        comptes = {z: v for z, v in comptes.items() if z not in self.ecartees}
        cur = self.cible
        n_cur = comptes.get(cur, (0, 0.0))[0] if cur else 0
        if cur is not None and n_cur < ROBOT_MIN:
            if self.sous_t is None:
                self.sous_t = now
            if now - self.sous_t < DELAI_QUITTER:
                return cur
            best, _n = self._choisir(comptes, exclure=cur)
            return self._changer(best, now)
        self.sous_t = None
        best, n_best = self._choisir(comptes, exclure=cur)
        if best is None or (cur is not None and n_best <= n_cur):
            self.candidat = None
            return cur
        if best != self.candidat:
            self.candidat, self.candidat_t = best, now
        if cur is None:
            if now - self.candidat_t >= DELAI_PREMIER:
                return self._changer(best, now)
            return None
        if now - self.candidat_t >= DELAI_CHANGER and now - self.depuis >= SEJOUR_MIN:
            return self._changer(best, now)
        return cur


class Gestionnaire:
    """Processus du robot : démarrer, arrêter, relancer après un plantage (au plus RELANCES_MAX fois en
    RELANCES_FENETRE s, attente croissante), puis abandon jusqu'à ``reprendre``. ``commande`` : texte (découpé comme un
    shell POSIX) ou liste ; ``{hote}``, ``{port}``, ``{cle}`` y sont remplacés. La clé est aussi passée dans la
    variable WWMP_ROBOT_CLE (jamais journalisée)."""

    RELANCES_MAX = 3
    RELANCES_FENETRE = 600.0
    RELANCE_DELAI = 2.0        # s, doublé à chaque plantage rapproché
    ARRET_DELAI = 5.0          # s : terminate, puis kill

    def __init__(self, commande, hote: str = "127.0.0.1", port: int = 0, cle: str = "", log=print,
                 horloge=time.monotonic, journal: str | None = None):
        self.commande = commande
        self.hote, self.port, self.cle = hote, port, cle
        self.log, self.horloge = log, horloge
        self.journal = journal
        self.priorite_basse = True    # le robot ne passe jamais avant les joueurs ni le serveur
        self.proc: subprocess.Popen | None = None
        self.plantages: list[float] = []
        self.prochain = 0.0
        self.abandon = False
        self.demarrages = 0
        self.dernier_code: int | None = None
        self.demarre_t: float | None = None
        self._fichier = None

    def argv(self) -> list[str]:
        cmd = self.commande
        if isinstance(cmd, str):
            # Windows : pas de découpage POSIX (il mangerait les « \ » des chemins) ; guillemets retirés à la main
            parts = shlex.split(cmd, posix=os.name != "nt")
            if os.name == "nt":
                parts = [a[1:-1] if len(a) >= 2 and a[0] == a[-1] and a[0] in "\"'" else a for a in parts]
        else:
            parts = list(cmd or [])
        rep = {"hote": self.hote, "port": str(self.port), "cle": self.cle, "python": sys.executable}
        out = []
        for a in parts:
            try:
                out.append(str(a).format(**rep))
            except (KeyError, IndexError, ValueError):
                out.append(str(a))
        return out

    @property
    def configure(self) -> bool:
        return bool(self.argv())

    def en_marche(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def demarrer(self) -> bool:
        if self.en_marche():
            return True
        argv = self.argv()
        if not argv:
            return False
        env = dict(os.environ, WWMP_ROBOT_CLE=self.cle, WWMP_ROBOT_HOTE=self.hote, WWMP_ROBOT_PORT=str(self.port))
        sortie = subprocess.DEVNULL
        if self.journal:
            try:
                self._fichier = open(self.journal, "ab")
                sortie = self._fichier
            except OSError:
                self._fichier = None
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if self.priorite_basse:      # hérité par le portage que lance le client du robot
                kw["creationflags"] |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)
        elif self.priorite_basse:
            kw["preexec_fn"] = lambda: os.nice(10)
        try:
            self.proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=sortie,
                                         stderr=subprocess.STDOUT, **kw)
        except OSError as e:
            self.log(f"robot : impossible de lancer « {argv[0]} » ({e})")
            self._plante(None)
            return False
        self.demarrages += 1
        self.demarre_t = self.horloge()
        self.log(f"robot : processus lancé (pid {self.proc.pid}, démarrage n° {self.demarrages})")
        return True

    def arreter(self, raison: str = "") -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(self.ARRET_DELAI)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(self.ARRET_DELAI)
                except subprocess.TimeoutExpired:
                    pass
        self.dernier_code = p.returncode
        self.demarre_t = None
        self._fermer_journal()
        self.log("robot : processus arrêté" + (f" ({raison})" if raison else ""))

    def _fermer_journal(self) -> None:
        if self._fichier is not None:
            try:
                self._fichier.close()
            except OSError:
                pass
            self._fichier = None

    def _plante(self, code: int | None) -> None:
        now = self.horloge()
        self.dernier_code = code
        self.plantages = [t for t in self.plantages if now - t < self.RELANCES_FENETRE] + [now]
        if len(self.plantages) > self.RELANCES_MAX:
            self.abandon = True
            self.log(f"robot : {len(self.plantages)} plantages en {self.RELANCES_FENETRE:.0f} s, plus de relance "
                     "(autorité laissée aux joueurs ; redémarre le serveur ou « reprendre » après correction)")
            return
        attente = self.RELANCE_DELAI * (2 ** (len(self.plantages) - 1))
        self.prochain = now + attente
        self.log(f"robot : processus terminé (code {code}), relance dans {attente:.0f} s")

    def reprendre(self) -> None:
        self.abandon, self.plantages, self.prochain = False, [], 0.0

    def tick(self, voulu: bool) -> None:
        """``voulu`` : le processus doit tourner. Relève un plantage, relance si permis."""
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self.demarre_t = None
            self._fermer_journal()
            self._plante(code)
        if not voulu:
            if self.en_marche():
                self.arreter("repos")
            return
        if self.en_marche() or self.abandon or self.horloge() < self.prochain:
            return
        self.demarrer()

    def etat(self) -> str:
        if self.abandon:
            return "abandon"
        if self.en_marche():
            return "en_marche"
        return "relance" if self.plantages and self.horloge() < self.prochain else "arrete"

    def vue(self) -> dict:
        return {"etat": self.etat(), "pid": self.proc.pid if self.en_marche() else None,
                "demarrages": self.demarrages, "plantages": len(self.plantages), "dernier_code": self.dernier_code,
                "commande": bool(self.configure)}


class Mesures:
    """Compteurs pour décider après l'essai s'il faut garder le robot (administration, journal)."""

    def __init__(self):
        self.prises = 0
        self.rendus = 0
        self.retours_forces = 0
        self.sans_gain = 0                       # fois où le robot, prêt, n'a PAS pris (aucun gain)
        self.raisons: dict[str, int] = {}
        self.attentes: list[float] = []          # s : prêt -> prise (passation)
        self.gains_rtt: list[float] = []         # ms : aller-retour du joueur d'autorité - celui du robot, à la prise
        self.temps_autorite = 0.0                # s cumulées
        self.bascules_vues = 0                   # changements d'autorité entre joueurs (toutes zones)

    def _r(self, cle: str) -> None:
        self.raisons[cle] = self.raisons.get(cle, 0) + 1

    def prise(self, zone, raison, attente, rtt_joueur, rtt_robot) -> str:
        self.prises += 1
        self._r("prise:" + raison)
        self.attentes = (self.attentes + [round(attente, 2)])[-50:]
        if isinstance(rtt_joueur, (int, float)):
            self.gains_rtt = (self.gains_rtt + [round(rtt_joueur - rtt_robot, 1)])[-50:]
        return (f"robot : MESURE prise {zone} (raison {raison}, passation {attente:.1f} s, aller-retour joueur "
                f"{'?' if rtt_joueur is None else f'{rtt_joueur:.0f}'} ms, robot {rtt_robot:.0f} ms)")

    def rendu(self, zone, raison, force, duree) -> str:
        self.rendus += 1
        if force:
            self.retours_forces += 1
        self._r(("retour_force:" if force else "rendu:") + raison)
        self.temps_autorite += duree
        return f"robot : MESURE {'retour forcé' if force else 'rendu'} {zone} (raison {raison}, tenue {duree:.0f} s)"

    def vue(self) -> dict:
        moy = (lambda xs: round(sum(xs) / len(xs), 2) if xs else None)
        return {"prises": self.prises, "rendus": self.rendus, "retours_forces": self.retours_forces,
                "sans_gain": self.sans_gain, "raisons": dict(sorted(self.raisons.items())),
                "passation_moy_s": moy(self.attentes), "passation_max_s": max(self.attentes) if self.attentes else None,
                "gain_rtt_moy_ms": moy(self.gains_rtt), "temps_autorite_s": round(self.temps_autorite),
                "bascules_joueurs": self.bascules_vues}


class Robot:
    """État du robot vu du serveur (la logique de passation est dans ``Server.robot_tick``)."""

    def __init__(self, mode: str = DEFAUT, cle: str | None = None, commande=None, repos_arret: float = REPOS_ARRET,
                 log=print, horloge=time.monotonic):
        self.mode = normaliser(mode)
        self.cle = cle or (nouvelle_cle() if self.mode == "auto" else None)
        self.choix = ChoixZone()
        self.repos_arret = max(0.0, float(repos_arret))
        self.gest = Gestionnaire(commande, cle=self.cle or "", log=log, horloge=horloge) if commande else None
        self.pid: int | None = None          # id de la connexion du robot (None : pas connecté)
        self.zone_autorite: str | None = None   # zone où le robot doit (ou va) tenir l'autorité
        self.cible: str | None = None
        self.ordre: dict | None = None
        self.ordre_t = float("-inf")
        self.envoye_t: float | None = None      # heure de l'ordre « va » vers la cible actuelle
        self.arrive_t: float | None = None      # heure d'arrivée dans la cible (state)
        self.pret: str | None = None            # zone annoncée prête par le robot
        self.snap_t: float | None = None        # instantané de l'autorité reçu depuis l'arrivée
        self.attente_t: float | None = None     # prêt depuis…, en attente d'une zone calme
        self.repos_t: float | None = None
        self.suivi_t = float("-inf")
        self.rapport: dict = {}
        self.prises = self.rendues = 0
        self.ignores = 0                         # messages du robot ignorés (hors TYPES_ACCEPTES)
        self.gain: tuple = (None, "")         # dernier verdict de Server.robot_gain pour la cible
        self.gain_t: float | None = None         # gain constaté sans interruption depuis…
        self.sans_gain_t: float | None = None    # (zone tenue) plus de gain depuis…
        self.pas_avant: dict[str, float] = {}    # zone -> pas de reprise avant… (retour « plus de gain »)
        self.tenue_t: float | None = None        # zone tenue depuis…
        self.mesures = Mesures()
        self.dernier_evt = ""

    def mesurer_prise(self, zone: str, raison: str, attente: float, rtt_joueur, rtt_robot: float, now: float):
        self.tenue_t = now
        return self.mesures.prise(zone, raison, attente, rtt_joueur, rtt_robot)

    def mesurer_rendu(self, zone: str, raison: str, force: bool, now: float):
        duree = now - self.tenue_t if self.tenue_t is not None else 0.0
        self.tenue_t = None
        return self.mesures.rendu(zone, raison, force, duree)

    @property
    def actif(self) -> bool:
        return self.mode == "auto"

    def regler(self, mode) -> bool:
        """Changement à chaud (administration). True si le mode a changé. Le processus reste celui de la ligne de
        commande (--robot-commande / --robot-portage) : sans commande, le serveur attend un robot lancé à part."""
        mode = normaliser(mode)
        if mode == self.mode:
            return False
        self.mode = mode
        if mode == "auto" and not self.cle:
            self.cle = nouvelle_cle()
            if self.gest is not None:
                self.gest.cle = self.cle
        return True

    def changer_cible(self, cible: str | None) -> None:
        if cible != self.cible:
            self.cible = cible
            self.envoye_t = self.arrive_t = self.attente_t = self.snap_t = None
            self.gain_t = self.sans_gain_t = None
            self.gain = (None, "")
            self.pret = None
            self.ordre = None

    def phase(self, zone_robot: str | None, tenue: str | None) -> str:
        """off, absent (pas connecté), repos, en_route, veille (dans la zone, rien à gagner : l'autorité reste au
        joueur), prise (dans la zone, gain constaté, attend une passation sûre), autorite, rend (tient encore une zone
        qu'il doit quitter)."""
        if not self.actif:
            return "off"
        if self.pid is None:
            return "absent"
        if tenue is not None:
            return "autorite" if tenue == self.cible else "rend"
        if self.cible is None:
            return "repos"
        if zone_robot != self.cible:
            return "en_route"
        return "prise" if self.gain[0] else "veille"
