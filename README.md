# WindWakerHD-Multiplayer-Server

Le serveur de partie de **Wind Waker Coop** (WindWakerHD-Multiplayer), seul, pour l'héberger avec Docker.
Aucun fichier du jeu : le serveur ne fait que relayer et fusionner l'état des joueurs.

> Dépôt **généré** depuis le dépôt du mod (`outils/extraire_serveur.py`, commit source : `8d901d9`).
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

Déjà installé avec `git clone` et `--build` (dossier `serveur_dedie/docker` du dépôt du mod ou de celui-ci) ? Même nom
de projet (`wwmp`) et même volume : dans un dossier qui contient ce docker-compose.yml (et ton ancien `.env`, s'il y en
avait un), `docker compose up -d` remplace le conteneur et garde le monde. Pas à pas :
[docs/hotes/JOUER_PAR_INTERNET.md](docs/hotes/JOUER_PAR_INTERNET.md), « Passer au docker-compose.yml seul ».

## Le fichier .env (facultatif)

Sans `.env`, tout marche : port 7777, compte admin et mot de passe de partie créés au hasard au premier démarrage.
Pour choisir toi-même, crée un fichier `.env` **à côté du docker-compose.yml**, puis `docker compose up -d` :

```bash
WWMP_PORT=7777
WWMP_ADMIN_UTILISATEUR=admin
WWMP_ADMIN_MOT_DE_PASSE=un-long-mot-de-passe-admin
WWMP_MOT_DE_PASSE=le-mot-de-passe-a-donner-aux-amis
```

| Variable | Rôle | Vide ou absente |
|---|---|---|
| `WWMP_PORT` | port TCP ouvert sur la machine (celui à rediriger sur la box) | 7777 |
| `WWMP_ADMIN_UTILISATEUR` | utilisateur admin (lanceur : « Administrer le serveur ») | `admin` |
| `WWMP_ADMIN_MOT_DE_PASSE` | mot de passe admin, **différent** de celui de la partie ; tant qu'il est dans le `.env`, il l'emporte | créé au hasard : `docker compose exec wwmp cat /donnees/admin.txt` |
| `WWMP_MOT_DE_PASSE` | mot de passe de la partie, lu au **premier** démarrage seulement ; ensuite il se change dans le lanceur | créé au hasard : `docker compose exec wwmp cat /donnees/mot_de_passe.txt` |
| `WWMP_UID` / `WWMP_GID` | utilisateur et groupe (numéros) qui font tourner le serveur, défini dans le docker-compose.yml (`user:`), jamais root | 10001 / 10001 |

L'image ne définit aucun utilisateur : c'est le `user:` du docker-compose.yml qui le choisit. Pour changer d'UID sur un
volume déjà créé, donne-lui d'abord le volume :
`docker compose run --rm --user 0 --entrypoint chown wwmp -R 1000:1000 /donnees` (avec tes numéros).

Rien d'autre à mettre : le monde, le randomizer, le mode Hanteurs, les joueurs max, le deathlink et la difficulté se
règlent depuis le lanceur (Héberger › « Sur mon serveur dédié » › « Administrer le serveur »). Garde ce `.env` pour toi
(il contient des mots de passe) et évite les caractères `$`, `#` et les espaces dans les mots de passe.

Construire soi-même au lieu de prendre l'image : `git clone` de ce dépôt, puis
`cd serveur_dedie/docker && docker compose up -d --build`.

La configuration (monde, randomizer, mode Hanteurs, joueurs max…) se règle depuis le lanceur des
joueurs : Héberger › « Sur mon serveur dédié » › « Administrer le serveur ».

Pas à pas complet : [docs/hotes/JOUER_PAR_INTERNET.md](docs/hotes/JOUER_PAR_INTERNET.md). Le joueur robot (expérimental) a
besoin du client de jeu : il se construit depuis le dépôt du mod (`docs/hotes/ESSAYER_LE_ROBOT.md` de
[WindWakerHD-Multiplayer](https://github.com/doryan-meunier/WindWakerHD-Multiplayer)).

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
