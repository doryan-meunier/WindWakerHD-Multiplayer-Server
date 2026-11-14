#!/usr/bin/env python3
"""Serveur de partie. Point d'entrée : le code est dans tools/serveur/principal.py.

Lancé par le lanceur (Héberger), lancer_serveur.bat / .sh et l'image Docker
(serveur_dedie/docker : ENTRYPOINT /app/wwmp_server.py) : chemin FIGÉ.
Les options sont celles de ``serveur.principal.main`` (``python tools/wwmp_server.py --help``).
"""
import sys

from serveur.principal import main

if __name__ == "__main__":
    sys.exit(main())
