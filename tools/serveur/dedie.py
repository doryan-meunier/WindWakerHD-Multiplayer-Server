"""Serveur dédié (docs/hotes/JOUER_PAR_INTERNET.md, Docker : dépôt WindWakerHD-Multiplayer-Server) : wwmp_server.py qui tourne seul, sans
joueur ni Cemu, dans un conteneur Docker (le serveur reste lançable à la main : le lanceur s'en sert pour « Sur ce PC »).

- ``mot_de_passe_serveur(args)`` : d'où vient le mot de passe de la partie (options, variable
  d'environnement, dossier de données) ; ``--donnees DOSSIER`` y range aussi les clés partagées
  (cles.json) et y crée mot_de_passe.txt au premier lancement. Avec ``--donnees``, le serveur garde
  aussi un MONDE PERSISTANT (DOSSIER/mondes/<id>/monde.json, serveur/monde_persistant.py ; cles.json y
  est repris une fois), sauf ``--sans-monde``. Avec ``--config`` (Docker, serveur/admin.py), le fichier
  mot_de_passe.txt du volume l'emporte sur WWMP_MOT_DE_PASSE (qui ne sert qu'à le créer) : l'administrateur le
  change depuis le lanceur.
- ``ouvrir_journal(chemin)`` : journal dans un fichier (``--journal``) au lieu de la console, gardé sous
  JOURNAL_MAX octets (l'ancien devient « .1 »).
"""
from __future__ import annotations

import os
import sys

from commun import acces

JOURNAL_MAX = 5 << 20


def fichier_mdp_volume(args) -> str | None:
    """--config : DONNEES/mot_de_passe.txt quand il est LA source du mot de passe de partie (réglable par
    l'administrateur) ; None si une option (--mot-de-passe, --mot-de-passe-fichier, --sans-mot-de-passe) l'emporte."""
    d = getattr(args, "donnees", None)
    if not d or getattr(args, "sans_mot_de_passe", False) or acces.nettoyer(getattr(args, "mot_de_passe", None)) \
            or getattr(args, "mot_de_passe_fichier", None):
        return None
    return os.path.join(d, acces.FICHIER)


def mot_de_passe_serveur(args, volume: bool = False) -> tuple[str | None, str]:
    """(mot de passe, d'où il vient — jamais le mot de passe lui-même). Ordre : --mot-de-passe,
    --mot-de-passe-fichier, WWMP_MOT_DE_PASSE, DOSSIER/mot_de_passe.txt (--donnees : créé s'il
    manque) ; --sans-mot-de-passe l'emporte. Fixe aussi --keys-file dans le dossier de données.
    ``volume`` (--config, Docker) : DOSSIER/mot_de_passe.txt passe AVANT WWMP_MOT_DE_PASSE, qui ne sert qu'à le
    créer (l'administrateur change ensuite le mot de passe depuis le lanceur).
    Lève OSError / ValueError."""
    d = getattr(args, "donnees", None)
    if d:
        os.makedirs(d, exist_ok=True)
        if not getattr(args, "keys_file", None):
            args.keys_file = os.path.join(d, "cles.json")
    if getattr(args, "sans_mot_de_passe", False):
        return None, ""
    m = acces.nettoyer(getattr(args, "mot_de_passe", None))
    if m:
        return m, "option --mot-de-passe"
    f = getattr(args, "mot_de_passe_fichier", None)
    if f:
        m = acces.lire_fichier(f)
        if not m:
            raise ValueError(f"fichier {f} absent ou vide")
        return m, f"fichier {f}"
    if volume and d:
        f = os.path.join(d, acces.FICHIER)
        m = acces.lire_fichier(f)
        if m:
            if acces.mdp_env() and acces.mdp_env() != m:
                print(f"note : {acces.ENV} ignorée : le mot de passe de partie est celui de {f} (réglable depuis le "
                      "lanceur, « Administrer le serveur ») ; retire-la du fichier .env", flush=True)
            return m, f"fichier {f}"
        if acces.mdp_env():
            acces.ecrire_fichier(f, acces.mdp_env())
            return acces.mdp_env(), f"variable {acces.ENV}, reprise dans {f}"
    if acces.mdp_env():
        return acces.mdp_env(), f"variable {acces.ENV}"
    if d:
        f = os.path.join(d, acces.FICHIER)
        m = acces.lire_fichier(f)
        if m:
            return m, f"fichier {f}"
        acces.ecrire_fichier(f, acces.generer())
        return acces.lire_fichier(f), f"fichier {f}, créé maintenant : ouvre-le pour lire le mot de passe"
    return None, ""


class Journal:
    """Fichier journal qui ne grossit pas sans fin : au-delà de ``maxi`` octets (vérifié aussi
    PENDANT que le serveur tourne, pas seulement au démarrage : un serveur dédié reste lancé
    des mois, et chaque sonde venue d'Internet écrit deux lignes), l'ancien devient « .1 »."""

    def __init__(self, chemin: str, maxi: int = JOURNAL_MAX):
        self.chemin, self.maxi = chemin, maxi
        self.f = None
        self._ouvrir()

    def _ouvrir(self) -> None:
        try:
            if os.path.getsize(self.chemin) > self.maxi:
                os.replace(self.chemin, self.chemin + ".1")
        except OSError:
            pass
        self.f = open(self.chemin, "a", encoding="utf-8", errors="replace", buffering=1)

    def write(self, texte: str) -> int:
        n = self.f.write(texte)
        try:
            trop = self.f.tell() > self.maxi
        except (OSError, ValueError):
            trop = False
        if trop:
            self.f.close()
            self._ouvrir()
        return n

    def flush(self) -> None:
        self.f.flush()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"

    def close(self) -> None:
        self.f.close()


def ouvrir_journal(chemin: str) -> Journal:
    """Sorties standard et d'erreur -> ``chemin`` (ajout, UTF-8, ligne par ligne, taille bornée)."""
    os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
    j = Journal(chemin)
    sys.stdout = sys.stderr = j
    return j
