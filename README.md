# WindWakerHD-Multiplayer-Server

Le serveur de partie de **Wind Waker Coop** (WindWakerHD-Multiplayer), seul, pour l'héberger avec Docker.
Aucun fichier du jeu : le serveur ne fait que relayer et fusionner l'état des joueurs.

> Dépôt **généré** depuis le dépôt du mod (`outils/extraire_serveur.py`, commit source : `aa76ce8`).
> Ne pas le modifier à la main : toute correction se fait dans WindWakerHD-Multiplayer, puis on régénère.

## Démarrer

```bash
git clone https://github.com/doryan-meunier/WindWakerHD-Multiplayer-Server.git
cd WindWakerHD-Multiplayer-Server/serveur_dedie/docker
docker compose up -d --build
docker compose logs -f        # le compte admin s'affiche au premier démarrage
```

Mettre à jour : `git pull` puis `docker compose up -d --build` (le monde reste dans le volume `wwmp-donnees`).

La configuration (monde, randomizer, mode Hanteurs, joueurs max…) se règle depuis le lanceur des
joueurs : Héberger › « Sur mon serveur dédié » › « Administrer le serveur ».

Pas à pas complet : [docs/hotes/JOUER_PAR_INTERNET.md](docs/hotes/JOUER_PAR_INTERNET.md).

## Contenu

| Dossier | Rôle |
|---|---|
| `tools/wwmp_server.py` | point d'entrée |
| `tools/serveur/` | le serveur (partie, monde persistant, administration, mises à jour) |
| `tools/commun/` | protocole et version, mots de passe, signatures |
| `tools/synchro/` | fusion côté serveur de chaque mécanique synchronisée |
| `tools/rando/` | tirage du randomizer et trackeur |
| `tools/jeu/` | tables du jeu (progression, symboles) utilisées par la synchro |
| `serveur_dedie/docker/` | Dockerfile et docker-compose.yml |

Sans Docker (Python 3.10+, aucune dépendance) : `python tools/wwmp_server.py --help`.
