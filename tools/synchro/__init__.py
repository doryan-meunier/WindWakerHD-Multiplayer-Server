"""Mécaniques synchronisées entre les joueurs, un module par mécanique (coffres, clés, objets au sol, monstres,
mode Hanteurs…) : chaque module contient la logique pure partagée par le client (lecture / écriture du jeu)
et par le serveur (validation, fusion, relais). Dépend de ``commun`` et ``jeu``."""
