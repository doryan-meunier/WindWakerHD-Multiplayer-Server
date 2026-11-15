# Essayer le joueur robot

Le joueur robot est une copie du jeu qui tourne **sur la machine du serveur**, cachée et sans joueur. Il fait tourner
les monstres d'**une** zone partagée, celle où il y a le plus de joueurs (2 au moins). Il ne la prend que s'il fait
**mieux** qu'un joueur, c'est-à-dire si le PC d'autorité a un ping nettement plus haut, ou instable, ou si l'autorité
change sans arrêt. Sinon rien ne change. L'étude complète est dans `docs/etudes/joueur_robot.md`.

C'est un **essai** : le robot est coupé par défaut, et rien de tout cela n'a encore tourné dans le vrai jeu.

## Ce qu'il faut

* le portage natif ZeldaWWHDRecomp installé sur la machine du serveur, avec **nos mods à jour** : `wwmp-natif`
  **0.12.0** ou plus (le lanceur l'installe et l'allume) ;
* une sauvegarde de la partie dans ce portage : le robot en fait une **copie** dans son dossier
  (`%LOCALAPPDATA%\WWMP\robot` sous Windows), au premier lancement seulement (`--recopier` pour la refaire) ; la
  sauvegarde d'origine n'est jamais écrite ;
* Windows avec une carte graphique : c'est le cas le plus simple. Linux et Docker restent expérimentaux (voir plus bas).

## Le lancer (Windows, serveur sur ce PC)

Dans une console, à la racine du dépôt (ou du dossier installé du mod) :

```bat
python tools\wwmp_server.py --robot auto --robot-portage "C:\…\WindWakerHD-v0.5.1-windows-x86_64"
```

Le serveur lance lui-même le robot quand une zone a au moins 2 joueurs : `tools\client\robot.py` démarre le portage
caché, passe l'écran titre et lance le client en mode `--robot`. Il l'arrête après 5 minutes sans zone à 2
(`--robot-repos 300`). Les joueurs se connectent comme d'habitude.

Pour vérifier sans rien lancer : `python tools\client\robot.py --port 7777 --portage "C:\…" --essai` affiche la
commande du jeu, ses variables et celle du client.

Serveur dédié avec le lanceur : **Jouer en ligne → Héberger** › « Sur mon serveur dédié » › « Administrer le serveur » ›
**Joueur robot du serveur** :
« Automatique », ou « Coupé » pour l'arrêter. Le changement s'applique tout de suite.

## Docker / Linux (expérimental, jamais essayé)

Le jeu n'a encore jamais tourné sous Linux. Il faut préparer la release Linux du portage sur la machine, depuis
**ton** dump. Ensuite, dans `serveur_dedie/docker/.env` :

```
WWMP_ROBOT_CLE=<32 caractères hexadécimaux>      # python3 -c "import secrets; print(secrets.token_hex(16))"
WWMP_PORTAGE_LINUX=/chemin/de/la/release/linux
```

puis :

```sh
cd serveur_dedie/docker
docker compose -f docker-compose.yml -f robot/docker-compose.robot.yml up -d --build
```

et, dans le lanceur, « Administrer le serveur » › Joueur robot › « Automatique ». Sans carte graphique, le rendu se
fait sur le CPU (Mesa lavapipe) : compte 1 à 3 cœurs et jusqu'à 1,4 Go de RAM.

## Le couper

* « Administrer le serveur » › Joueur robot › « Coupé » : la zone revient tout de suite à un joueur, et le robot
  s'arrête ;
* ou relance le serveur sans `--robot auto` ; avec Docker : `docker compose … stop wwmp-robot`.

## Quoi regarder

Dans le journal du serveur :

* `robot : MESURE prise <zone> (raison …, passation 1.8 s, aller-retour joueur 150 ms, robot 2 ms)` : il a pris une
  zone, avec la raison ;
* `robot : MESURE rendu …` ou `retour forcé …` : il l'a rendue (plus de gain, robot trop lent, muet ou sorti de la
  zone) ;
* `robot : <zone> : rien à gagner (…), l'autorité reste au joueur` : il reste en veille ;
* `robot : BILAN {…}` toutes les 5 minutes : prises, rendus, retours forcés, durée moyenne de passation, gain moyen
  de ping, temps passé en autorité, bascules d'autorité entre joueurs.

Le même bilan apparaît dans « Administrer le serveur ».

En jeu, regarde trois choses :

* les monstres sautent-ils au moment de la passation ?
* frappent-ils toujours un joueur et jamais le vide (le robot est caché) ?
* le message « autorité … -> le robot du serveur » s'affiche-t-il chez les joueurs de la zone ?

**Pour décider ensuite** : si les retours forcés sont fréquents, si le gain de ping est faible (moins de 50 ms), ou si
la passation se voit, coupe le robot et garde l'autorité joueur.
