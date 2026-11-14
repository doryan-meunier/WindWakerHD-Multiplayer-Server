"""A16 : arrêt propre du serveur et du client lancés par le compagnon (tools/lanceur/socle.py).

Deux voies, qui annulent la tâche principale asyncio (le ``finally`` habituel s'exécute : le
client retire ses fantômes, le serveur envoie ``bye``) :
- ``CTRL_BREAK_EVENT`` (Windows, processus lancé avec CREATE_NEW_PROCESS_GROUP dans la même
  console) : gestionnaire SIGBREAK ;
- un FICHIER D'ARRÊT (``--arret-fichier CHEMIN``) : le processus s'arrête quand il apparaît.
  Sert quand il n'y a pas de console commune (tests, compagnon lancé sans fenêtre).
"""
from __future__ import annotations

import asyncio
import os
import signal


def installer(tache: asyncio.Task, fichier: str | None = None) -> asyncio.Task | None:
    """À appeler depuis la boucle, avec la tâche principale. Renvoie la tâche de surveillance du
    fichier (à garder en référence), ou None."""
    loop = asyncio.get_running_loop()
    fait = {"annule": False}

    def annuler() -> None:
        # une seule annulation : le compagnon envoie le fichier ET CTRL_BREAK ; une deuxième
        # annulation pendant le « finally » couperait l'envoi de « bye » (revue A16)
        if not fait["annule"]:
            fait["annule"] = True
            tache.cancel()

    def sur_signal(*_a) -> None:
        loop.call_soon_threadsafe(annuler)

    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, sur_signal)
        except (ValueError, OSError):      # pas dans le fil principal (tests)
            pass
    if os.name != "nt":
        # serveur dédié (systemd, Docker) : « systemctl stop » / « docker stop » envoient SIGTERM ;
        # même arrêt propre (message bye aux joueurs) que Ctrl+C
        try:
            loop.add_signal_handler(signal.SIGTERM, annuler)
        except (ValueError, OSError, RuntimeError, NotImplementedError):
            pass
    if fichier:
        return asyncio.create_task(surveiller(fichier, annuler))
    return None


async def surveiller(fichier: str, annuler, pas: float = 0.2) -> None:
    """``annuler`` : fonction sans argument (ou, pour compatibilité, la tâche à annuler)."""
    while not os.path.exists(fichier):
        await asyncio.sleep(pas)
    if isinstance(annuler, asyncio.Task):
        annuler.cancel()
    else:
        annuler()
