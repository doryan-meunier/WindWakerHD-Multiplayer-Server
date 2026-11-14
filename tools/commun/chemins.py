"""Chemins du mod, calculés depuis l'emplacement de ce fichier (aucun réglage, aucune écriture).

``TOOLS``   dossier du code : ``tools/``. Son nom et sa place sont FIGÉS : les lanceurs déjà installés
            n'acceptent dans un paquet de mise à jour que des fichiers sous ``tools/``, ``graphicpack/``,
            ``docs/*.md`` et ``serveur_dedie/`` (commun/maj_paquet.chemin_autorise), lancent
            ``tools/wwmp_lanceur.py`` de la version installée, et « Wind Waker Coop.exe » vise
            ``R/tools/wwmp_lanceur.py``.
``RACINE``  dossier du mod (le dépôt, ou une version installée ``R/versions/<nom>``) : parent de ``tools/``.

Les scripts de ``tools/`` (wwmp_lanceur.py, wwmp_server.py…) sont les points d'entrée : lancés comme
fichiers, ils mettent ``tools/`` en tête de ``sys.path`` (comportement normal de Python, et le .pth du
Python embarqué de l'installateur fait de même), donc tous les paquets s'importent en absolu
(``from commun import protocole``) sans rien bricoler.
"""
from __future__ import annotations

import os

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RACINE = os.path.dirname(TOOLS)
UI = os.path.join(TOOLS, "lanceur", "ui")                 # pages du lanceur (pywebview)
GRAPHICPACK = os.path.join(RACINE, "graphicpack")       # packs Cemu WWMP_* (nom figé, recopiés par les mises à jour)
