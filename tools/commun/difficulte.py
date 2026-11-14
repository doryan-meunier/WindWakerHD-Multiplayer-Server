"""Difficulté de la partie : deux réglages du salon, tenus par le serveur, les mêmes pour tous.

Python pur (aucun accès au jeu ici : la mémoire est passée par l'appelant). Étude et preuves :
docs/etudes/difficulte.md.

A. DÉGÂTS REÇUS ×1 (défaut), ×2, ×3, ×4. Chaque perte de vie de Link est multipliée DANS LE JEU par le
   graphic pack (bit 13 des capacités, zone ``wwmp_dif``) : le PC n'écrit qu'un nombre (2, 3 ou 4 ; 0 = jeu
   normal). Les soins ne sont jamais multipliés. Sans le pack à jour : rien n'est multiplié (message).

B. VIE DES MONSTRES selon le nombre de Link de la PARTIE (pas de la salle) :
   - ``off`` (défaut) : vie du jeu ;
   - ``demi`` : +50 % par Link en plus du premier (2 Link = ×1,5 ; 3 = ×2 ; 4 = ×2,5…) ;
   - ``link`` : ×1 par Link (2 Link = ×2 ; 3 = ×3…).
   Le nombre de Link est compté par le serveur (joueurs en jeu, Hanteurs exclus) et diffusé : tous les PC ont
   le même. Seul le PC qui fait autorité sur la salle écrit, une seule fois par monstre, quand il vient
   d'apparaître (vie pleine), jamais au-delà de 127 (octet signé du jeu), jamais sur un boss.
"""
from __future__ import annotations

import time

DEGATS = (1, 2, 3, 4)
PV_MODES = ("off", "demi", "link")
LINKS_MAX = 8                   # 8 joueurs au plus dans une partie
PV_PLAFOND = 127                # s8 du jeu : jamais plus
PV_BASE_MAX = 126               # 127 = « invincible » chez certains monstres (Wizzrobe du boss) : jamais touché

# fopAc_ac_c (HD) : max_health s8 +0x3A0, health s8 +0x3A1, puis deux octets de remplissage (+0x3A2, +0x3A3)
OFF_PV_MAX, OFF_PV, OFF_MARQUE = 0x3A0, 0x3A1, 0x3A2

PERIODE_PACK = 2.0              # s : relecture / réécriture du multiplicateur dans le pack (Cemu relancé = 0)
SANS_BOITE = 10.0               # s sans zone du pack accessible avant de prévenir (jeu qui se lance : silence)
STABLE = 0.5                    # s : un monstre doit garder la même vie pleine ce temps avant d'être multiplié
ECRITURES_PAR_PASSE = 16
ETRANGERS_MAX = 4096            # anciennes marionnettes retenues (numéros d'acteur)
SANS_JEU = 5.0                  # s sans partie chargée avant de les oublier (jeu relancé)


def defaut() -> dict:
    return {"degats": 1, "pv_monstres": "off"}


def _entier(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def valider(d) -> dict | None:
    """Réglages complets et exacts ({"degats": 1..4, "pv_monstres": "off"|"demi"|"link"}), sinon None."""
    if not isinstance(d, dict):
        return None
    deg, pv = d.get("degats"), d.get("pv_monstres")
    if not _entier(deg) or deg not in DEGATS or not isinstance(pv, str) or pv not in PV_MODES:
        return None
    return {"degats": deg, "pv_monstres": pv}


def fusionner(actuel: dict, d) -> dict | None:
    """Commande du salon : un ou deux champs ; les champs absents gardent leur valeur. None : refus (champ
    inconnu, valeur hors liste, rien à changer n'est PAS un refus)."""
    if not isinstance(d, dict) or not d or any(k not in ("degats", "pv_monstres") for k in d):
        return None
    return valider({**normaliser(actuel), **d})


def normaliser(d) -> dict:
    """Lecture tolérante (message du serveur, monde enregistré) : toute valeur douteuse = réglage par défaut."""
    out = defaut()
    if isinstance(d, dict):
        if _entier(d.get("degats")) and d["degats"] in DEGATS:
            out["degats"] = d["degats"]
        if isinstance(d.get("pv_monstres"), str) and d["pv_monstres"] in PV_MODES:
            out["pv_monstres"] = d["pv_monstres"]
    return out


def borner_links(n) -> int:
    """Nombre de Link de la partie, borné à 1..8 (une valeur douteuse vaut 1 : vie du jeu)."""
    if not _entier(n):
        return 1
    return max(1, min(LINKS_MAX, n))


def facteur_pv(mode: str, links) -> tuple[int, int]:
    """Facteur de vie des monstres, en fraction (numérateur, dénominateur)."""
    n = borner_links(links)
    if mode == "link":
        return n, 1
    if mode == "demi":
        return n + 1, 2
    return 1, 1


def pv_multiplie(base: int, facteur: tuple[int, int]) -> int:
    """Vie d'un monstre après le facteur : arrondie au plus proche (0,5 vers le haut), jamais moins que la vie
    du jeu, jamais plus que 127. Une vie hors de 1..126 n'est jamais changée."""
    if not _entier(base) or not 1 <= base <= PV_BASE_MAX:
        return base
    num, den = facteur
    if not (_entier(num) and _entier(den)) or den <= 0 or num <= den:
        return base
    return max(base, min(PV_PLAFOND, (2 * base * num + den) // (2 * den)))


def degats_directs(quarts: int, n: int) -> int:
    """Coup appliqué par écriture directe de la vie (mode dégradé sans le pack à jour, PvP direct) : le pack ne
    le voit pas, on le multiplie ici."""
    return int(quarts) * (n if _entier(n) and n in DEGATS else 1)


def perte_d_origine(quarts: int, n: int) -> int:
    """Mode Hanteurs : le score compte le coup du monstre, pas le multiplicateur. Perte de vie vue par le serveur
    ramenée au coup d'origine (arrondi vers le haut, au moins 1 pour une perte réelle)."""
    if not _entier(n) or n not in DEGATS or n == 1 or quarts <= 0:
        return quarts
    return -(-quarts // n)


def compter_links(roles) -> int:
    """``roles`` : un booléen par joueur en jeu (True = Hanteur). Nombre de Link, au moins 1."""
    return borner_links(sum(1 for h in roles if not h))


def texte_facteur(facteur: tuple[int, int]) -> str:
    num, den = facteur
    return f"×{num // den}" if num % den == 0 else f"×{num / den:.1f}".replace(".", ",")


def texte(regle: dict, links: int) -> str:
    """Une ligne en français simple (terminal du client, journal du serveur)."""
    r = normaliser(regle)
    bouts = [f"dégâts reçus ×{r['degats']}"]
    if r["pv_monstres"] == "off":
        bouts.append("vie des monstres normale")
    else:
        f = facteur_pv(r["pv_monstres"], links)
        mode = "×1 par Link" if r["pv_monstres"] == "link" else "+50 % par Link en plus"
        bouts.append(f"vie des monstres {texte_facteur(f)} ({mode}, {borner_links(links)} Link)")
    return ", ".join(bouts)


class Difficulte:
    """Côté client : la règle reçue du serveur et le multiplicateur de dégâts posé dans le pack.

    ``accepte`` : --share-difficulte (ou --coop-donjon) sans --no-difficulte. Sans lui, la règle du serveur est
    notée mais rien n'est appliqué, et un multiplicateur laissé dans le jeu par une session précédente est remis
    à ×1."""

    def __init__(self, accepte: bool = False, periode: float = PERIODE_PACK):
        self.accepte = bool(accepte)
        self.regle = defaut()
        self.links = 1
        self.connu = False            # le serveur a parlé de difficulté (False : ancien serveur)
        self.periode = periode
        self.t_verif = -1e9
        self.pose = None              # dernier multiplicateur posé dans le pack (None : inconnu)
        self.cap = None               # le pack sait multiplier (None : pas encore regardé)
        self.compte = 0               # pertes multipliées par le jeu (mise au point)
        self._prevenu_cap = False
        self.t_sans_boite = None      # depuis quand la zone du pack n'est pas accessible (None : elle l'est)
        self._prevenu_boite = False
        self.notes: list[str] = []

    # --- règle ---
    @property
    def enabled(self) -> bool:
        return self.accepte and self.connu

    @property
    def degats(self) -> int:
        return self.regle["degats"] if self.enabled else 1

    @property
    def facteur(self) -> tuple[int, int]:
        return facteur_pv(self.regle["pv_monstres"], self.links) if self.enabled else (1, 1)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def set_regle(self, d) -> bool:
        """Règle reçue du serveur ({"degats", "pv_monstres", "links"}). Rend True si quelque chose a changé."""
        if not isinstance(d, dict):
            return False
        avant = (self.connu, dict(self.regle), self.links)
        self.regle = normaliser(d)
        self.links = borner_links(d.get("links"))
        self.connu = True
        change = avant != (self.connu, self.regle, self.links)
        if change:
            self._prevenu_cap = False
            self._prevenu_boite = False
            self.t_verif = -1e9                     # appliqué au prochain pas
            if (avant[0], avant[1]) != (True, self.regle):
                if self.accepte:
                    self.notes.append("difficulté de la partie : " + texte(self.regle, self.links))
                elif self.regle != defaut():
                    self.notes.append("difficulté de la partie : " + texte(self.regle, self.links)
                                      + " — pas appliquée chez toi (lance le client avec --share-difficulte "
                                        "ou --coop-donjon)")
        return change

    def on_welcome(self, msg: dict) -> None:
        d = msg.get("difficulte") if isinstance(msg, dict) else None
        if isinstance(d, dict):
            self.set_regle(d)
        else:                                      # ancien serveur : jeu normal
            self.connu = False
            self.regle, self.links = defaut(), 1
            self.t_verif = -1e9

    def on_message(self, msg: dict) -> None:
        """Message ``regles`` : le champ ``difficulte`` est optionnel (absent = rien ne change)."""
        if isinstance(msg, dict) and isinstance(msg.get("difficulte"), dict):
            self.set_regle(msg["difficulte"])

    def reset_network(self) -> None:
        """Connexion perdue : jeu normal jusqu'au prochain ``welcome``."""
        self.connu = False
        self.regle, self.links = defaut(), 1
        self.t_verif = -1e9

    # --- pack ---
    def step(self, mb, now: float | None = None, actif: bool = True) -> None:
        """Pose le multiplicateur de dégâts dans la zone du pack (donnée seule). ``actif`` : connecté et Link
        (un Hanteur n'a pas de vie à perdre : ×1). Relu toutes les ``periode`` secondes : Cemu relancé = zone à
        zéro = ×1 jusqu'à la réécriture."""
        now = time.monotonic() if now is None else now
        if mb is None:
            # pas de zone du pack (--no-ghosts, mémoire en lecture seule, pack absent, jeu fermé) : rien n'est
            # posé, et on ne sait plus ce qui l'était. Sans cela le client affichait « dégâts reçus ×N » et
            # multipliait les coups directs alors que le jeu ne multipliait rien.
            self.pose, self.cap = None, None
            self.t_verif = -1e9                     # boîte revenue : appliqué tout de suite
            if self.t_sans_boite is None:
                self.t_sans_boite = now
            voulu = self.degats if actif else 1
            if voulu > 1 and not self._prevenu_boite and now - self.t_sans_boite >= SANS_BOITE:
                self._prevenu_boite = True
                self.notes.append(f"dégâts ×{voulu} : ce client n'a pas accès au graphic pack du jeu (jeu fermé, "
                                  "pack pas installé, option --no-ghosts ou lecture seule) ; chez toi les dégâts "
                                  "restent ceux du jeu")
            return
        self.t_sans_boite = None
        if now - self.t_verif < self.periode:
            return
        self.t_verif = now
        cap = False
        try:
            cap = bool(getattr(mb, "difficulte", False))
        except Exception:
            cap = False
        self.cap = cap
        voulu = self.degats if actif else 1
        if not cap:
            self.pose = None
            if voulu > 1 and not self._prevenu_cap:
                self._prevenu_cap = True
                self.notes.append(f"dégâts ×{voulu} : le graphic pack installé ne sait pas multiplier les dégâts "
                                  "(il faut le pack à jour : python tools/install_graphicpack.py, puis relancer "
                                  "Cemu) ; chez toi les dégâts restent ceux du jeu")
            return
        lu, self.compte = mb.degats()
        if lu != voulu:
            mb.set_degats(voulu)
            self.notes.append(f"dégâts reçus ×{voulu} posés dans le jeu" if voulu > 1
                              else "dégâts reçus : retour au jeu normal (×1)")
        self.pose = voulu

    def fermer(self, mb) -> bool:
        """Sortie du client : jeu normal. Rend True si le multiplicateur a été remis à ×1."""
        try:
            if mb is not None and getattr(mb, "difficulte", False) and mb.degats()[0] != 1:
                mb.set_degats(1)
                self.pose = 1
                return True
        except Exception:
            pass
        return False

    def status(self) -> str:
        if not self.connu:
            return "jeu normal (le serveur n'en parle pas)"
        if not self.accepte:
            return "coupée chez toi" + ("" if self.regle == defaut() else " (partie : " + texte(self.regle, self.links) + ")")
        s = texte(self.regle, self.links)
        if self.regle["degats"] > 1 and self.cap is False:
            s += " — dégâts NON multipliés : pack à mettre à jour"
        elif self.regle["degats"] > 1 and self.t_sans_boite is not None:
            s += " — dégâts NON multipliés : pas d'accès au graphic pack (jeu fermé, --no-ghosts…)"
        return s


class MonstresPV:
    """Vie des monstres multipliée, chez le PC qui fait autorité sur la salle seulement.

    Règles (toutes nécessaires) pour écrire dans un monstre :
    - il n'est pas un boss (``exclus`` : numéros de process), ni une marionnette (liste donnée par l'appelant) ;
    - sa marque (+0x3A2) et l'octet suivant valent 0 : jamais multiplié, et ces octets sont bien du remplissage ;
    - vie maximale du jeu entre 1 et 126, vie == vie maximale (jamais blessé) ;
    - même lecture depuis ``STABLE`` secondes (un acteur se construit sur plusieurs frames) ;
    - relu juste avant l'écriture : mêmes quatre octets, et ``valide(base, pid)`` dit que l'acteur est toujours là ;
    - il n'a jamais été la marionnette d'un autre PC (``marionnettes``) : ce monstre est né sous une autre autorité,
      qui l'a déjà renforcé (ou a choisi de ne pas le faire). Sa marque est dans la mémoire de l'AUTRE PC ; chez nous
      il a la vie reçue par le relais et la vie maximale du jeu : blessé jusqu'à retomber pile sur sa vie d'origine,
      il aurait l'air neuf et serait renforcé une deuxième fois, en plein combat, à la passation d'autorité.
    Écriture unique de 3 octets : vie maximale, vie, marque (= vie maximale d'origine). Un monstre marqué n'est plus
    jamais touché (ni si le nombre de Link change, ni si le client est relancé : la marque est dans le jeu)."""

    def __init__(self, exclus=(), stable: float = STABLE, par_passe: int = ECRITURES_PAR_PASSE):
        self.exclus = frozenset(exclus)
        self.stable = stable
        self.par_passe = par_passe
        self.vus: dict[tuple[int, int], tuple[bytes, float]] = {}     # (base, pid) -> (4 octets, depuis)
        self.faits = 0
        self.dernier: tuple | None = None       # (clé, vie d'origine, vie posée)
        self.etrangers: set[int] = set()        # numéros d'acteur vus comme marionnettes d'un autre PC : jamais touchés
        self.t_sans_jeu: float | None = None

    def oublier(self) -> None:
        self.vus.clear()

    def marionnettes(self, pids) -> None:
        """À chaque pas, autorité ou non : numéros d'acteur (uniques tant que le jeu tourne) des marionnettes en
        cours. Retenus jusqu'à la fin du jeu : une ancienne marionnette n'est jamais renforcée par ce PC."""
        self.t_sans_jeu = None
        neufs = {p for p in pids if _entier(p)}
        if len(self.etrangers) + len(neufs) > ETRANGERS_MAX:
            self.etrangers.clear()              # borne mémoire (des heures de jeu) : on garde les marionnettes en cours
        self.etrangers |= neufs

    def sans_jeu(self, now: float) -> None:
        """Pas de partie chargée (écran titre, Cemu fermé). Après ``SANS_JEU`` secondes d'affilée, les numéros
        d'acteur retenus sont oubliés : un jeu relancé recompte les siens depuis zéro."""
        self.vus.clear()
        if self.t_sans_jeu is None:
            self.t_sans_jeu = now
        elif now - self.t_sans_jeu >= SANS_JEU:
            self.etrangers.clear()

    def passe(self, mem, ennemis, facteur: tuple[int, int], now: float, valide=None) -> list[tuple]:
        """``ennemis`` : [(clé, numéro de process, pid, base)] des monstres locaux de la salle (sans marionnette).
        Rend [(clé, vie d'origine, vie posée)] écrits pendant cette passe."""
        num, den = facteur
        if num <= den:                          # ×1 : rien à faire, rien à retenir
            self.vus.clear()
            return []
        presents, ecrits = set(), []
        for key, proc, pid, base in ennemis:
            if proc in self.exclus or not _entier(base) or base <= 0 or pid in self.etrangers:
                continue
            k = (base, pid)
            presents.add(k)
            try:
                brut = bytes(mem.read(base + OFF_PV_MAX, 4))
            except Exception:
                self.vus.pop(k, None)
                continue
            if len(brut) != 4 or not self._eligible(brut):
                self.vus.pop(k, None)
                continue
            vu = self.vus.get(k)
            if vu is None or vu[0] != brut:
                self.vus[k] = (brut, now)
                continue
            if now - vu[1] < self.stable or len(ecrits) >= self.par_passe:
                continue
            base_pv = brut[0]
            cible = pv_multiplie(base_pv, facteur)
            if cible <= base_pv:
                continue
            try:
                if valide is not None and not valide(base, pid):
                    self.vus.pop(k, None)
                    continue
                if bytes(mem.read(base + OFF_PV_MAX, 4)) != brut:
                    self.vus.pop(k, None)
                    continue
                mem.write(base + OFF_PV_MAX, bytes((cible, cible, base_pv)))
            except Exception:
                self.vus.pop(k, None)
                continue
            self.vus.pop(k, None)
            self.faits += 1
            self.dernier = (key, base_pv, cible)
            ecrits.append(self.dernier)
        for k in [k for k in self.vus if k not in presents]:
            del self.vus[k]
        return ecrits

    @staticmethod
    def _eligible(brut: bytes) -> bool:
        pv_max, pv, marque, suite = brut
        return marque == 0 and suite == 0 and 1 <= pv_max <= PV_BASE_MAX and pv == pv_max
