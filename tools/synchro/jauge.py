"""Barre dans le jeu du mode Hanteurs (--barre-jeu, COUPÉE par défaut ; bit 14 du pack, CALL 12 JAUGE).

Le dessin d'une vraie barre de compétences par le pack (textes, cases, recharges) a été cherché et écarté : aucune
fonction d'affichage du jeu n'a pu être appelée ou prouvée hors ligne, et une erreur dans le pack plante le jeu chez
tous les joueurs (docs/etudes/barre_competences.md). Ce qui est livré est la voie la plus sûre trouvée : la jauge
circulaire du HUD du jeu, celle du temps de nage (G_SwimTimeMeter_00), pilotée par DONNÉES seules. Le pack écrit trois
champs de dComIfGp (air, max, status) ; le HUD la dessine lui-même dans sa passe 2D, la cache pendant un événement
ou un menu, la décompte d'une unité par image et la ferme à 0. Rien n'est appelé, aucune accroche n'est posée.

Elle montre UNE grandeur : les 30 DERNIÈRES secondes de la possession (compte à rebours : elle apparaît pleine quand
il reste 30 s, ou dès le début d'une possession plus courte, et se vide jusqu'à la fin). Comme sous l'eau, sous la moitié de
max le HUD la fait clignoter et joue l'alarme d'air (son 0x840, 0x026E6774) à intervalles réguliers : les 15
dernières secondes d'une possession longue bipent. Pourquoi 30 s : le jeu lit
aussi ces champs pendant la nage (facteur de vitesse 1 - air / 900) ; le pack refuse toute valeur au-delà de 899
images, si bien que même un Link qui nagerait sous notre jauge garderait un facteur normal. Les noms des gestes, leurs touches, leurs recharges, la vie du monstre restent dans la fenêtre posée sur Cemu
(client/barre_fenetre.py), qui garde son propre réglage : les deux coexistent.

Repli silencieux : sans --barre-jeu, sans le bit 14 (pack v19 plus ancien), sans possession, ou si le pack refuse
(jauge prise par la nage de Link, événement en cours, Link absent), rien n'est écrit et seule la fenêtre reste.
Client tué : la jauge se vide seule (le HUD la décompte) et disparaît. Arrêt normal : rendue tout de suite.

Rien n'a été vu en jeu (docs/dev/tests/PLAN_TEST_SYNCHRO.md, barre dans le jeu, BJ0 à BJ8).
"""
from __future__ import annotations

import math

IMAGES_PAR_S = 30            # Wind Waker HD tourne à 30 images/s (le HUD décompte une unité par image) [HYPO en jeu]
JAUGE_MAX = 899              # borne du pack (images) : moins de 30 s, jamais 900 (valeur de la nage)
PERIODE = 2.0                # re-pose au plus toutes les 2 s (recale la jauge sur l'horloge du client)
TIMEOUT = 0.3                # attente de la réponse du pack (une image suffit ; au-delà : requête neutralisée)
PAUSE_ERREUR = 5.0           # boîte occupée ou sans réponse (menu, chargement) : on réessaie plus tard
# résultats du pack (CALL 12)
PRISE, RAFRAICHIE, RENDUE = 1, 2, 0
R_ARGS, R_LINK, R_EVENEMENT, R_PRISE_PAR_LE_JEU = -1, -2, -3, -4
RAISONS = {R_ARGS: "refusée (arguments)", R_LINK: "Link absent", R_EVENEMENT: "événement en cours",
           R_PRISE_PAR_LE_JEU: "jauge prise par le jeu (nage)"}


def _nombre(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def cible(vue) -> tuple[int, int] | None:
    """(images restantes, images en tout) à montrer pour la vue du Hanteur (``HanteurSync.vue``), ou None : pas
    Hanteur, potion bue (on joue alors un Link, avec le vrai HUD), pas de possession, durée inconnue ou finie, ou plus
    de JAUGE_MAX images restantes (la jauge ne montre que la fin de la possession)."""
    if not isinstance(vue, dict) or vue.get("role") != "hanteur" or vue.get("clone") is not None:
        return None
    po = vue.get("possede")
    if not isinstance(po, dict):
        return None
    reste, duree = _nombre(po.get("reste")), _nombre(po.get("duree"))
    if reste is None or duree is None or duree <= 0 or reste <= 0:
        return None
    maxi = min(JAUGE_MAX, max(1, int(round(duree * IMAGES_PAR_S))))
    air = int(math.ceil(reste * IMAGES_PAR_S))
    if air > JAUGE_MAX:
        return None                                 # pas encore dans les 30 dernières secondes
    air = min(air, maxi)
    if air < 1:
        return None
    return air, maxi


class JaugeSync:
    """Tient la jauge du HUD à jour pendant une possession (thread de travail du client)."""

    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.supported = None        # None : pas encore vu de boîte ; sinon bit 14 présent
        self.posee = False           # le pack l'a acceptée (1 ou 2) et nous ne l'avons pas rendue
        self.maxi = None             # max posé en dernier
        self.t = None                # heure de la dernière pose (ou du dernier essai)
        self.pause_jusqua = 0.0
        self.refus_t = None          # heure du dernier refus du pack (un essai par période après un refus)
        self.coupee = False          # refus -1 du pack (ne devrait pas arriver) : plus rien jusqu'au redémarrage
        self.n_poses = self.n_refus = 0
        self.dernier_refus = None
        self.en_erreur = False
        self.notes: list[str] = []

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def step(self, mb, now: float, vue, want: bool = True) -> None:
        """``mb`` : Mailbox ou None ; ``vue`` : ``HanteurSync.vue(...)`` ; ``want`` : jeu jouable ici (état lu, pas
        de changement de stage). Les erreurs de la boîte (occupée, sans réponse) sont avalées : nouvel essai après
        PAUSE_ERREUR, rien de cassé (la jauge se vide seule)."""
        if not self.enabled or self.coupee:
            return
        ok = bool(mb is not None and getattr(mb, "barre_jeu", False))
        if mb is not None and ok != self.supported:
            self.supported = ok
            self.notes.append("barre dans le jeu : active (jauge du HUD = 30 dernières secondes de possession)" if ok else
                              "barre dans le jeu : graphic pack sans cette capacité (bit 14), seule la fenêtre "
                              "posée sur Cemu reste (mets le pack à jour)")
        if not ok:
            self.posee = False
            return
        if now < self.pause_jusqua:
            return
        c = cible(vue) if want else None
        try:
            if c is None:
                if self.posee:
                    mb.jauge_rendre(timeout=TIMEOUT)        # 0 rendue, -4 plus à nous : rien à faire de plus
                    self.posee, self.maxi, self.t = False, None, None
                return
            air, maxi = c
            if (self.posee and self.maxi == maxi and self.t is not None and 0.0 <= now - self.t < PERIODE):
                return
            if not self.posee and self.t is not None and 0.0 <= now - self.t < PERIODE:
                return                                      # refus récent : pas plus d'un essai par période
            if self.refus_t is not None and 0.0 <= now - self.refus_t < PERIODE:
                return                                      # même si la jauge, refusée, est encore à nous
            self.t = now
            res = mb.jauge(air, maxi, timeout=TIMEOUT)
        except ValueError:
            return
        except Exception as exc:                            # MailboxError, MailboxCancelled, lecture ratée
            self.pause_jusqua = now + PAUSE_ERREUR
            if not self.en_erreur:                          # une seule note par série d'erreurs
                self.notes.append(f"barre dans le jeu : boîte indisponible ({type(exc).__name__}), nouvel essai "
                                  f"toutes les {PAUSE_ERREUR:.0f} s")
            self.en_erreur = True
            return
        self.en_erreur = False
        if res in (PRISE, RAFRAICHIE):
            self.refus_t = None
            if not self.posee:
                self.n_poses += 1
            self.posee, self.maxi = True, maxi
            return
        # -2 (Link absent) et -3 (événement) : le pack n'a rien écrit, une jauge déjà posée reste à nous (le HUD la
        # montre encore) et devra être rendue ; -4 : prise par le jeu (plus à nous) ; -1 : coupée ci-dessous
        if res not in (R_LINK, R_EVENEMENT):
            self.posee, self.maxi = False, None
        self.n_refus += 1
        self.dernier_refus = res
        self.refus_t = now
        if res == R_ARGS:
            self.coupee = True
            self.notes.append("barre dans le jeu : refusée par le pack (arguments), coupée jusqu'au prochain lancement")

    def stop(self, mb) -> None:
        """Arrêt du client : jauge rendue tout de suite (sinon elle se vide seule)."""
        try:
            if self.posee and mb is not None and getattr(mb, "barre_jeu", False):
                mb.jauge_rendre(timeout=TIMEOUT)
        except Exception:
            pass
        finally:
            self.posee, self.maxi = False, None

    def status(self) -> str:
        if not self.enabled:
            return "coupée (--barre-jeu)"
        if self.supported is None:
            return "en attente du jeu"
        if not self.supported:
            return "pack sans le bit 14 : fenêtre seule"
        if self.coupee:
            return "coupée après un refus du pack"
        s = f"{'montrée' if self.posee else 'cachée'} ; {self.n_poses} possession(s) montrée(s)"
        if self.n_refus:
            s += f", {self.n_refus} refus (dernier : {RAISONS.get(self.dernier_refus, self.dernier_refus)})"
        return s
