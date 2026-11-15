# WindWakerHD-Multiplayer-Server

Le serveur de partie de **Wind Waker Coop** (WindWakerHD-Multiplayer), seul, pour l'héberger avec Docker.
Aucun fichier du jeu : le serveur ne fait que relayer et fusionner l'état des joueurs.

> Dépôt **généré** depuis le dépôt du mod (`outils/extraire_serveur.py`, commit source : `714a633`).
> Ne pas le modifier à la main : toute correction se fait dans WindWakerHD-Multiplayer, puis on régénère.

## Démarrer : seulement le docker-compose.yml

Rien à cloner ni à construire : GitHub publie l'image du serveur (ghcr.io/doryan-meunier/windwakerhd-multiplayer-server) à chaque mise à jour de ce dépôt.

```bash
mkdir wwmp && cd wwmp
curl -fsSLO https://raw.githubusercontent.com/doryan-meunier/WindWakerHD-Multiplayer-Server/main/docker-compose.yml
docker compose up -d
docker compose logs -f        # le compte admin s'affiche au premier démarrage
```

Mettre à jour : `docker compose pull && docker compose up -d` (le monde reste dans le volume `wwmp-donnees`).

Déjà installé avec `git clone` et `--build` ? Même nom de projet (`wwmp`) et même volume : lance ces deux commandes
dans un dossier qui contient ce docker-compose.yml, le monde est gardé. L'ancien dossier cloné peut ensuite partir.

Construire soi-même au lieu de prendre l'image : `git clone` de ce dépôt, puis
`cd serveur_dedie/docker && docker compose up -d --build`.

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
| `docker-compose.yml` | le serveur depuis l'image publiée (le seul fichier à prendre) |
| `serveur_dedie/docker/` | Dockerfile et docker-compose.yml pour construire soi-même |
| `.github/workflows/image.yml` | construit et publie l'image à chaque push |

Sans Docker (Python 3.10+, aucune dépendance) : `python tools/wwmp_server.py --help`.

## Licence

[MIT](LICENSE), pour le code de ce dépôt. Il ne contient aucun fichier de *The Legend of Zelda: The Wind Waker HD*,
qui reste la propriété de Nintendo.
