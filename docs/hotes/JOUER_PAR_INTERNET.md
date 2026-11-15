# Jouer par Internet

Ce guide explique comment jouer avec des amis qui ne sont **pas chez toi**. Pas besoin d'être
informaticien : suis les étapes dans l'ordre, et coche au fur et à mesure.

Rappel : **chacun joue sur son propre PC, avec son propre jeu** (son Cemu, son dump de Wind
Waker HD). Rien dans ce projet ne fournit le jeu, et le serveur n'a besoin d'aucun fichier du
jeu.

## En bref : trois façons

| Façon | Pour qui | Port à ouvrir sur la box | Difficulté |
|---|---|---|---|
| **A. Tailscale ou ZeroTier** (réseau privé virtuel) | tout le monde, pour commencer | aucun | facile |
| **B. Ton serveur à la maison** (toujours allumé) + un port ouvert | tu as un serveur, tu veux que tes amis jouent même quand tu n'es pas là | **un** port TCP | moyen |
| **C. Chez un joueur** (son PC héberge pendant qu'il joue) | dépannage, pas de serveur | **un** port TCP | moyen |

Conseil : commence par **A**. Si tout marche, passe à **B** quand tu veux un serveur qui tourne
tout le temps. Les deux se combinent (serveur à la maison **dans** Tailscale : aucun port ouvert,
le plus sûr).

Ce qu'il faut savoir avant :

* Le jeu passe par **un seul port TCP** : **7777** (modifiable). Le port UDP 7778 ne sert qu'à
  « Chercher sur le réseau » dans une même maison : **inutile par Internet**, ne l'ouvre pas.
* Par Internet, **mets un mot de passe de partie** (façons B et C). Le serveur refuse quiconque
  ne le connaît pas ; le mot de passe ne passe jamais tel quel sur le réseau (le serveur envoie
  un défi, le lanceur répond avec une preuve). Le trafic du jeu (positions, pseudos) n'est pas
  chiffré : avec Tailscale ou ZeroTier, il l'est.
* Jusqu'à **8 joueurs** par partie.
* Tout le monde doit avoir **la même version** des outils (même `git log -1 --oneline`). Le
  serveur aussi : quand tu mets à jour les PC, mets aussi à jour le serveur (voir « Mettre à
  jour le serveur »).

---

## Façon A — Tailscale ou ZeroTier (aucun port à ouvrir)

Tailscale crée un petit réseau privé entre vos PC, à travers Internet, comme si vous étiez dans
la même maison. Gratuit pour un usage personnel.

### A1. Installer Tailscale (chaque joueur)

1. Va sur **tailscale.com**, « Download », installe, connecte-toi (compte Google, Microsoft…).
2. **L'hôte** invite ses amis dans son réseau : sur la page d'administration de Tailscale
   (« Machines »), menu « … » de son PC → « Share… » (partager sa machine), ou « Users » →
   « Invite users ». Chaque ami accepte l'invitation.
3. Chaque PC a maintenant une adresse Tailscale qui commence par **100.** (icône Tailscale près
   de l'horloge → « This device : 100.x.y.z »).

ZeroTier marche pareil : crée un réseau sur **my.zerotier.com**, chacun installe ZeroTier et
« Join » l'identifiant du réseau (16 caractères), puis l'hôte coche « Auth » pour chaque membre
dans la page du réseau.

### A2. Jouer

1. **L'hôte** : lanceur → **Jouer en ligne → Héberger** → « Sur ce PC » → choisis la partie → **Lancer**. Pas
   besoin de mot de passe (le réseau Tailscale est privé). Le lanceur affiche l'adresse : prends
   celle qui commence par **100.** (ou 26. pour Radmin VPN, 25. pour Hamachi).
2. **Les amis** : lanceur → **Jouer en ligne → Rejoindre** → tape cette adresse (ex. `100.101.102.103`) → `Tester`
   → **Lancer**. « Chercher sur le réseau » ne marche pas à travers Tailscale : tape l'adresse.
3. **Pare-feu Windows de l'hôte** : à la première partie, Windows demande d'autoriser Python :
   accepte. Si un ami n'arrive pas à se connecter, autorise le port dans une invite de commandes
   ouverte « en tant qu'administrateur » (clic droit sur « Invite de commandes ») :

   ```bat
   netsh advfirewall firewall add rule name="WWMP" dir=in action=allow protocol=TCP localport=7777
   ```

Avec ton serveur à la maison : installe Tailscale **aussi sur le serveur** (façon B, étape B1,
mais saute les étapes B3 à B5 : aucun port à ouvrir). Tes amis utilisent alors l'adresse 100.x du
serveur.

---

## Façon B — Ton serveur à la maison

Le serveur (`wwmp_server.py`) tourne seul, sans Cemu ni joueur, toute la journée, dans un
conteneur **Docker**. Il garde le **monde persistant** (toute la partie commune, voir **B7**), le
mot de passe et toute sa configuration dans un **volume Docker**. Quand quelqu'un « héberge », il se
connecte simplement à ce serveur. **Un serveur à la maison = un monde persistant** : chacun rejoint
et quitte quand il veut, même quand l'hôte d'origine ne joue pas, comme sur un serveur Palworld.

Il faut Docker sur le serveur : Linux (Debian, Ubuntu, Raspberry Pi OS : `sudo apt install
docker.io docker-compose-v2`, ou la méthode de docs.docker.com), NAS (Synology, Unraid…) qui le
propose déjà.

### B1. Installer le serveur (Docker)

Un seul fichier à prendre, le `docker-compose.yml` de ce dépôt (l'image est publiée par GitHub, rien à cloner ni à
construire) :

```bash
mkdir wwmp && cd wwmp
curl -fsSLO https://raw.githubusercontent.com/doryan-meunier/WindWakerHD-Multiplayer-Server/main/docker-compose.yml
docker compose up -d
docker compose logs wwmp | grep "COMPTE ADMIN"            # compte admin créé au premier démarrage
docker compose exec wwmp cat /donnees/mot_de_passe.txt    # le mot de passe de la partie
```

Construire l'image soi-même au lieu de la télécharger : `git clone https://github.com/doryan-meunier/WindWakerHD-Multiplayer-Server.git`,
puis `cd WindWakerHD-Multiplayer-Server/serveur_dedie/docker && docker compose up -d --build` (même projet `wwmp`, même
volume : on peut passer de l'un à l'autre sans perdre le monde).

Image `python:3.12-slim`, aucune autre dépendance ; redémarrage automatique. **Rien à éditer dans
`docker-compose.yml`** : tout ce qui se règle vit dans le volume `wwmp-donnees` (il survit à
`docker compose down` et aux mises à jour) :

| Dans le volume (`/donnees`) | Contenu |
|---|---|
| `config.json` | la configuration de la partie : monde, graine et réglages du randomizer, mode Hanteurs et ses réglages, joueurs max, deathlink, difficulté (écrit par le serveur, réglé depuis le lanceur : **B1b**) |
| `admin.txt` | le compte admin (utilisateur, mot de passe), s'il n'est pas dans `.env` |
| `mot_de_passe.txt` | le mot de passe de partie (celui que tu donnes à tes amis) |
| `mondes/` | les mondes persistants (et leurs spoilers du randomizer, leurs sauvegardes) |
| `cles.json`, `mises_a_jour/` | anciennes clés partagées ; paquets de mise à jour du mod ([MISES_A_JOUR.md](https://github.com/doryan-meunier/WindWakerHD-Multiplayer/blob/main/docs/joueurs/MISES_A_JOUR.md)) |

Fichier **`.env`** facultatif, à côté de `docker-compose.yml` (modèle : `serveur_dedie/docker/.env.exemple` de ce dépôt) :

| Variable | Rôle |
|---|---|
| `WWMP_PORT=7790` | autre port que 7777 sur le serveur |
| `WWMP_ADMIN_UTILISATEUR=` / `WWMP_ADMIN_MOT_DE_PASSE=` | ton compte admin. Vide : créé au hasard au premier démarrage, écrit dans `admin.txt` et affiché une fois dans le journal. Rempli : il l'emporte sur `admin.txt` tant qu'il est là |
| `WWMP_MOT_DE_PASSE=` | mot de passe de partie au **premier** démarrage seulement ; ensuite il se change dans le lanceur (le fichier du volume l'emporte) |

Après un changement de `.env` : `docker compose up -d`. Journal : `docker compose logs -f`.

> **Docker sous Windows ou Mac (Docker Desktop), ou Docker « rootless » :** le serveur y voit
> toutes les connexions venir de la même adresse (celle de Docker), pas de celle de chaque joueur.
> Conséquences : après 10 mauvais mots de passe tapés par n'importe qui, **tout le monde** est
> bloqué 15 minutes (5 mauvais mots de passe admin bloquent l'administration), et la limite de
> 16 connexions par adresse vaut pour tous les joueurs ensemble. Sur un serveur Linux avec Docker
> normal (installé par `apt`, NAS…), pas de souci. Sur un PC Windows sans Docker, la façon **C**
> (le lanceur héberge « Sur ce PC ») reste possible.

### B1b. Régler le serveur depuis le lanceur (« Administrer le serveur »)

Lanceur → **Jouer en ligne → Héberger** → « **Sur mon serveur dédié** » → écris l'adresse du serveur → panneau
**« Administrer le serveur »** en bas de la page → **utilisateur** et **mot de passe admin** (ceux de
`admin.txt` ou de `.env` : ce n'est **pas** le mot de passe de partie) → **Se connecter**. Le
lanceur les retient pour cette adresse. Tu règles alors :

* le **monde** : en choisir un autre, en **créer** un (« Nouveau monde… »), **renommer** celui qui tourne ;
* le **randomizer** : graine et chaîne de réglages (bouton « Prendre mes réglages de l'onglet
  Réglages »). Un monde garde la table de sa première partie randomizer : pour une nouvelle
  graine, crée un nouveau monde ;
* le **mode Hanteurs** (on/off) et ses réglages (bouton « Prendre mes réglages Hanteurs ») ;
* le **nombre de joueurs** max, le **mot de passe de partie** (« Générer » en propose un), le
  **deathlink**, la **difficulté** (dégâts reçus, vie des monstres) ;
* le **PC d'autorité préféré** : le pseudo du joueur dont le PC fait tourner les monstres des zones où
  il est (le plus puissant, ou le plus près du serveur), ou `@hote` pour celui qui a coché « Je suis
  l'hôte » ; plusieurs pseudos séparés par des virgules, dans l'ordre. Vide (défaut) : comme avant, le
  premier arrivé dans la zone. Il ne prend la main que dans une zone **calme** (jamais en plein combat
  ni pendant une cinématique, et pas plus d'une fois toutes les 10 s par zone) et la rend tout seul s'il
  quitte la zone, se déconnecte ou ne répond plus. La liste « Autorité des zones occupées » montre qui
  tient chaque zone ;
* les **contrôles du serveur** et le **journal d'anomalies** : le serveur vérifie la vitesse des Link,
  la distance des coups (sur un monstre, sur le fantôme d'un joueur) et le nombre de messages de
  chacun. Les téléportations normales ne comptent pas (porte, chargement, cinématique, Ballade des
  vents, cyclone, chute dans le vide, Game Over, bateau et voile rapide, grappin, mode Hanteurs).
  « **Journal seulement** » (défaut, conseillé) : rien n'est refusé, les anomalies s'affichent dans le
  panneau (heure, joueur, quoi, ×N) et dans le journal du serveur (`ANOMALIE …`) ; « Effacer le
  journal » le vide (il repart de zéro à chaque redémarrage). « **Refuser** » : les coups impossibles
  et les messages en trop ne sont plus traités (la vitesse et les sauts restent seulement notés) ;
  ne l'allume qu'après une partie d'essai sans anomalie injustifiée. « Coupés » : rien n'est vérifié.

**Enregistrer et appliquer** écrit `config.json`. Joueurs max, mot de passe de partie (les joueurs
déjà connectés restent), deathlink, difficulté, nom du monde, réglages Hanteurs, PC d'autorité
préféré et contrôles du serveur s'appliquent **tout de suite**. Changer de monde, de graine ou allumer/couper le mode Hanteurs demande un
**redémarrage** : le serveur écrit le monde, prévient les joueurs (leurs clients se reconnectent
seuls), s'arrête, et Docker le relance (`restart: unless-stopped`) avec la nouvelle
configuration ; le lanceur relit la configuration une fois le serveur revenu. Bouton
« Redémarrer le serveur » pour le faire à la main.

Sécurité de l'administration : le mot de passe admin ne passe jamais sur le réseau (défi/réponse
HMAC, comme le mot de passe de partie) ; chaque demande est liée à un défi neuf et à son contenu
(rejouée ou modifiée en route, elle est refusée) ; la réponse est signée ; le mot de passe de
partie transporté est chiffré. Un refus attend 1 s ; 5 erreurs en 10 minutes bloquent
l'administration 15 minutes pour cette adresse. Le serveur n'exécute **aucune** commande : il ne
connaît que « lire », « écrire la configuration » (valeurs vérifiées une à une) et « redémarrer ».

**Qui l'emporte ?** `config.json`. Les options de ligne de commande (`--monde`, `--rando-graine`,
`--rando-reglages`, `--mode-hanteurs`, `--hanteurs-config`, `--max-joueurs`, `--deathlink`,
`--degats`, `--pv-monstres`, `--autorite-preferee`, `--validation`) ne servent qu'à **créer** `config.json` au premier
démarrage (une clé absente d'un ancien `config.json` est ajoutée depuis l'option, sinon vide) ;
ensuite elles sont ignorées (le journal le dit). Pour repartir des options : supprime
`config.json` du volume (`docker compose exec wwmp rm /donnees/config.json`) et redémarre.

### B1c. Tu avais un `docker-compose.override.yml` (ancienne façon de régler)

Avant, on réglait le monde et le randomizer avec un fichier `docker-compose.override.yml` (ligne
`command: [... "--monde", "rando1", "--rando-graine", ..., "--rando-reglages", ...]`). Il n'est
plus nécessaire :

1. Mets à jour (`git pull`, ou le docker-compose.yml seul : voir « Passer au docker-compose.yml seul » ; garde
   l'override **à côté**) **sans** supprimer l'override, puis `docker compose up -d --build` (`docker compose up -d`
   avec l'image publiée) : au premier démarrage, le serveur crée `config.json` **avec les
   options de l'override** (le journal liste celles qu'il a reprises). Si `.env` contient
   `WWMP_MOT_DE_PASSE`, il est repris dans `mot_de_passe.txt` : le mot de passe ne change pas.
2. Vérifie : `docker compose exec wwmp cat /donnees/config.json`, et note le compte admin
   (`docker compose logs wwmp | grep "COMPTE ADMIN"` ou `docker compose exec wwmp cat /donnees/admin.txt`).
3. Supprime `docker-compose.override.yml`, puis `docker compose up -d` : rien ne change (le
   monde, la graine et les réglages viennent maintenant de `config.json`).
4. Si tu avais un dossier `mises_a_jour` à côté de `docker-compose.yml`, copie son contenu dans le
   volume : `docker compose cp mises_a_jour/. wwmp:/donnees/mises_a_jour/`, puis supprime le dossier.

### B2. Pare-feu du serveur (à faire toi-même)

Autorise le port TCP du jeu (7777) en entrée, et rien d'autre :

* **Docker** : rien de plus en général (Docker ouvre lui-même le port publié).
* Si le serveur filtre quand même (ufw, firewalld) : `sudo ufw allow 7777/tcp`, ou
  `sudo firewall-cmd --permanent --add-port=7777/tcp && sudo firewall-cmd --reload`.

### B3. Donner une adresse fixe au serveur dans la maison

Dans l'interface de ta box (souvent http://192.168.1.1, ou l'appli de l'opérateur), cherche
« DHCP » → « baux statiques » / « réservation d'adresse » et réserve une adresse pour ton serveur
(ex. `192.168.1.50`). Sinon, l'adresse peut changer après un redémarrage et la redirection de
l'étape B4 ne marchera plus. (Pour connaître l'adresse actuelle : `ip -4 addr` sous Linux,
`ipconfig` sous Windows.)

### B4. Ouvrir UN port sur la box (redirection de port)

Toujours dans l'interface de la box, cherche « Redirection de ports », « NAT/PAT », « Port
forwarding » ou « Gestion des ports » (Freebox : Paramètres → Gestion des ports ; Livebox :
Réseau → NAT/PAT ; SFR et Bbox : rubrique NAT). Ajoute **une seule** règle :

| Champ | Valeur |
|---|---|
| Protocole | **TCP** (pas UDP) |
| Port externe | 7777 |
| Port interne / de destination | 7777 |
| Appareil / IP de destination | l'adresse de ton serveur (ex. 192.168.1.50) |

Attention : certaines connexions **partagent** leur adresse IPv4 entre plusieurs clients (Free
en « IP partagée », certaines box 4G/5G, fibre de petits opérateurs). La redirection ne marche
alors pas. Il faut demander une « IPv4 full-stack » / « IP fixe » à l'opérateur (gratuit chez
Free, dans l'espace abonné), ou utiliser la façon A.

### B5. Un nom à la place de l'adresse (DNS dynamique)

L'adresse publique de ta box peut changer. Un **DNS dynamique** donne un nom fixe
(`monserveur.duckdns.org`) qui suit l'adresse tout seul.

* **Dans la box** si elle le propose (rubrique « DynDNS » / « DNS dynamique » : No-IP, DynDNS…) :
  remplis ton compte, c'est tout.
* **Sinon, DuckDNS** (gratuit) : sur **duckdns.org**, connecte-toi, crée un sous-domaine, note le
  « token ». Sur le serveur Linux, ajoute la mise à jour toutes les 5 minutes (`crontab -e`) :

  ```
  */5 * * * * curl -fsS "https://www.duckdns.org/update?domains=monserveur&token=TON-TOKEN&ip=" >/dev/null
  ```

  (Sous Windows : l'appli « DuckDNS for Windows », ou une tâche planifiée qui ouvre la même
  adresse.)

L'adresse à donner à tes amis devient : `monserveur.duckdns.org:7777`. (Sans DNS dynamique :
ton adresse IP publique, visible dans l'interface de ta box.)

### B6. Jouer

* **Toi (l'hôte de la partie)** : lanceur → **Jouer en ligne → Héberger** → « **Sur mon serveur dédié** » →
  adresse (`monserveur.duckdns.org:7777` ; sans « :port », le lanceur prend 7777 ou le port déjà
  connu pour ce serveur) et **mot de passe** (rempli tout seul après « Administrer le serveur ») → laisse cochée « **Je suis l'hôte
  de la partie** » → choisis la partie coop → **Lancer**. Le lanceur vérifie d'abord l'adresse et
  le mot de passe, puis démarre Cemu.
* **Packs de Cemu** : rien à cocher. Juste avant de démarrer Cemu (fermé), le lanceur coche « WWMP
  Mailbox » et « WWMP Pause » (« Vivant »), décoche « WWMP Coop », et coche « WWMP Rando » seulement si la
  partie du serveur l'exige (il le demande au serveur ; décoché si elle n'en veut pas). La Console dit ce
  qui a changé (réglage « Cocher les packs WWMP tout seul avant de lancer », onglet Réglages).
* **Tes amis** : lanceur → **Jouer en ligne → Rejoindre** → même adresse et même mot de passe → `Tester` →
  **Lancer**. Un nouveau joueur coche « Copier la partie de l'hôte » avec une **partie coop neuve** :
  il reçoit le **monde** (B7), même si personne d'autre n'est en jeu.
* **Plus simple** : l'**assistant** du lanceur (premier lancement, ou Réglages › « Refaire la configuration »),
  écran « Comment tu joues » : « J'ai un serveur dédié » (toi : adresse, mot de passe, compte admin facultatif,
  « Je suis l'hôte ») ou « Je rejoins un ami » (tes amis : adresse, mot de passe, `Tester`). Ensuite un seul bouton,
  **« Jouer »** : il vérifie l'adresse et le mot de passe (un mot de passe refusé se corrige sur place), retrouve la
  partie coop de ce serveur (ou en crée une et y copie le monde pour un nouveau joueur), prépare le randomizer et les
  packs, puis démarre Cemu.
* Le **Lobby** (onglet Jouer en ligne) marche à distance (adresse + mot de passe, bouton Observer).
* Le lanceur **retient** l'adresse et le mot de passe (fichier
  `%LOCALAPPDATA%\WWMP\compagnon.json`, sur ton PC seulement).

« Je suis l'hôte » : avec un monde persistant (le cas normal d'un serveur à la maison), il n'y a
plus d'hôte pour la progression, c'est le **monde** qui fait foi ; la case sert seulement à
**administrer** depuis ton lanceur (deathlink, onglet « Mes parties → Mon monde » : libérer un joueur en
quarantaine, reprendre une image, renommer). Sur un serveur lancé avec `--sans-monde` (ancien
fonctionnement), elle désigne la progression copiée quand un ami « copie la partie de l'hôte » :
une seule personne la coche (deux hôtes = refus « hôte ambigu »), ou l'option `--hote PSEUDO`.

**Depuis la maison** (même box que le serveur) : utilise l'**adresse locale** du serveur
(`192.168.1.50:7777`), pas le nom DuckDNS : beaucoup de box ne savent pas « revenir » vers
l'intérieur, et deux joueurs de la même maison passant par l'adresse publique seraient vus avec
la même adresse par le serveur (« Copier la partie de l'hôte » les prendrait pour le même PC).

### B7. Le monde persistant (automatique sur un serveur à la maison)

Avec un dossier de données (`--donnees`, ce que fait l'installation Docker B1), le serveur garde
**un monde** : la progression partagée, les petites clés, les coffres ouverts, les quarts de cœur,
l'inventaire commun, les rubis / flèches / bombes, le vent, la règle du deathlink, la liste des
joueurs et une **image de sauvegarde** du monde. Tout est écrit dans
`DONNÉES/mondes/<identifiant>/monde.json`, relu au redémarrage. Rien ne se perd quand le serveur
redémarre ou quand l'hôte d'origine ne joue pas.

* **Choisir, créer, renommer le monde** : lanceur, « Administrer le serveur » (**B1b**). Au premier
  démarrage sans configuration : le monde déjà présent dans le volume, sinon « monde ». Un monde
  renommé se retrouve aussi par son ancien nom. Un serveur fait tourner **un** monde à la fois (les
  autres restent dans le volume : on passe de l'un à l'autre depuis le lanceur).
* **Nouveau joueur** : partie coop neuve → Rejoindre → « Copier la partie de l'hôte » : le serveur
  lui donne l'image du monde (dans un Journal vide), puis les synchros l'amènent à l'état exact du
  monde en jeu. Monde tout neuf (personne n'a encore joué) : il commence une partie neuve dans le jeu,
  et sa partie devient celle du monde.
* **Joueur qui revient** : il lance **sa** partie du monde, les synchros le rattrapent. S'il est très
  en retard, le lanceur (onglet **Mes parties → Mon monde**) propose « Mettre ma partie au niveau du monde »
  (Cemu fermé ; jamais automatique).
* **Protection** : la partie coop est liée à son monde au premier lancement ; le serveur refuse le
  client d'une partie liée à un autre monde. Une partie qui ne ressemble pas du tout au monde (partie
  solo chargée par erreur) est mise **en quarantaine** : le joueur voit le monde, mais rien de sa
  partie n'y entre (l'administrateur peut le libérer, onglet Mes parties → Mon monde).
* **Écriture** : au plus toutes les 30 s, en 5 s après un boss, une grande clé ou un objet, et à
  l'arrêt (`docker compose stop`, redémarrage demandé depuis le lanceur, Ctrl+C). La version précédente
  est gardée (`monde.json.1`), plus une copie par heure (24) et par jour (14) dans `sauvegardes/`.
  Un fichier abîmé n'est **jamais** écrasé : le serveur reprend la copie précédente, ou refuse de
  démarrer avec un message clair. **Revenir à une copie** : arrête le serveur, copie le fichier voulu
  de `sauvegardes/` sur `monde.json`, redémarre.
* **Voir les mondes** : la liste « Monde » de « Administrer le serveur »,
  `docker compose exec wwmp python /app/wwmp_server.py --donnees /donnees --lister-mondes`, ou l'onglet
  **Mes parties → Mon monde** du lanceur (nom, joueurs, dernière connexion, cœurs, objets, perles, donjons finis).
* **Ancien fonctionnement** (tout en mémoire, perdu au redémarrage) : `--sans-monde`.
* **Mode Hanteurs sur le monde** : permis (interrupteur « Mode Hanteurs » de « Administrer le serveur »). C'est une session
  temporaire au-dessus du monde : ce que les Link gagnent entre dans le monde, rien de ce que fait un
  Hanteur n'y entre, les rôles et les points ne sont jamais gardés (seuls les réglages du mode le sont).
  Pour finir la session, coupe le mode dans « Administrer le serveur » (le serveur redémarre). Détail : `docs/dev/SYNCHRO.md`
  du dépôt du mod, « Hanteurs sur un monde persistant ».
* Ancien `cles.json` : repris une fois dans le monde, puis renommé `cles.json.migre`.

### Mettre à jour le serveur

Quand tu mets à jour les PC de jeu, mets à jour le serveur, dans le dossier de son `docker-compose.yml` :

```bash
docker compose pull && docker compose up -d
```

(Image construite toi-même : `git pull` dans ce dépôt, puis `cd serveur_dedie/docker && docker compose up -d --build`.)
Le volume (configuration, mondes, mots de passe) est gardé tel quel.

Ton serveur peut aussi **distribuer les mises à jour du mod à tes amis** : leur lanceur les
propose tout seul à la connexion. Il suffit de copier le paquet signé dans son dossier
`mises_a_jour` du volume : voir [MISES_A_JOUR.md](https://github.com/doryan-meunier/WindWakerHD-Multiplayer/blob/main/docs/joueurs/MISES_A_JOUR.md), étape 3.

### Passer au docker-compose.yml seul (serveur installé depuis `serveur_dedie/docker` du dépôt du mod)

Le dépôt du mod n'a plus de dossier `serveur_dedie/` : le serveur dédié vit ici. Si ton serveur tourne depuis
`…/WindWakerHD-Multiplayer/serveur_dedie/docker`, **rien ne presse** : le conteneur continue de tourner après le
`git pull` du mod (qui retire les fichiers suivis de ce dossier, mais garde ton `.env`, non suivi). Pour passer au
`docker-compose.yml` seul, sans perdre le monde (même nom de projet `wwmp`, même volume `wwmp-donnees`) :

```bash
mkdir -p /opt/wwmp && cd /opt/wwmp
curl -fsSLO https://raw.githubusercontent.com/doryan-meunier/WindWakerHD-Multiplayer-Server/main/docker-compose.yml
cp /opt/WindWakerHD-Multiplayer/serveur_dedie/docker/.env . 2>/dev/null || true    # ton ancien .env, s'il y en avait un
docker compose up -d          # télécharge l'image publiée, remplace le conteneur wwmp-serveur, garde le volume
docker compose logs -f        # le même monde, le même compte admin, le même mot de passe
```

(Adapte `/opt/WindWakerHD-Multiplayer` au dossier de ton clone.) Vérifier que c'est bien le même volume :
`docker volume ls | grep wwmp-donnees` ne montre qu'**un** volume, `wwmp_wwmp-donnees`. Ensuite, l'ancien dossier
`serveur_dedie/docker` (où ne reste que `.env`) peut partir ; le clone du mod n'est plus utile au serveur.

---

## Façon C — Héberger chez un joueur (son PC)

Comme la façon B, mais le serveur est celui que le lanceur ouvre sur le PC de l'hôte pendant
qu'il joue. Pratique si tu n'as pas de serveur ; la partie s'arrête quand l'hôte ferme Cemu.
L'interrupteur **« Monde persistant »** d'Héberger garde aussi le monde sur ce PC (dossier
`%LOCALAPPDATA%\WWMP\mondes`, relu au prochain lancement), mais il ne vit que pendant que l'hôte
héberge.

1. L'hôte fait les étapes **B3** (adresse fixe de **son PC**), **B4** (port TCP 7777 vers **son
   PC**) et, si possible, **B5**.
2. Pare-feu Windows de son PC : la commande `netsh` de l'étape A2 (ou accepter la question de
   Windows pour Python, réseaux privés **et** publics).
3. Lanceur → **Jouer en ligne → Héberger** → « Sur ce PC » → **Mot de passe** : clique « Générer » (ou tape le
   tien) → **Lancer**. Donne aux amis l'adresse publique (ou le nom DuckDNS) **et** le mot de
   passe.
4. Les amis : **Jouer en ligne → Rejoindre**, adresse et mot de passe.

Si le PC de l'hôte est le plus puissant de la partie, coche aussi « **Mon PC fait tourner les monstres
des zones où je suis** » : il prend l'autorité des monstres des zones calmes où l'hôte est présent, et
la rend s'il part ou si son jeu ne répond plus (décoché : le premier arrivé dans la zone, comme avant ;
pris en compte au prochain lancement).

Sans mot de passe, un serveur joignable depuis Internet accepte n'importe qui : le journal du
serveur l'écrit en « ATTENTION » à la première connexion venue d'Internet, et une adresse
publique ne peut alors pas se déclarer « hôte ».

---

## Sécurité : ce que fait le serveur

* **Mot de passe de partie** (`--mot-de-passe-fichier FICHIER`, variable `WWMP_MOT_DE_PASSE`, ou
  `--donnees DOSSIER` qui le crée au hasard dans `DOSSIER/mot_de_passe.txt`). Le mot de passe
  n'apparaît **jamais** dans les journaux ni dans la ligne de commande des processus lancés par
  le lanceur. Évite l'option `--mot-de-passe` en ligne de commande sur une machine partagée
  (visible dans la liste des processus).
* **Compte admin** (serveur Docker) : distinct du mot de passe de partie, il ne sert qu'à « Administrer
  le serveur » (B1b) ; même principe de défi, 5 erreurs en 10 minutes bloquent l'administration
  15 minutes pour l'adresse. Il n'est écrit dans le journal qu'une fois, quand le serveur le crée.
* **Anti-force brute** : chaque refus attend 1 seconde ; une adresse qui se trompe 10 fois en
  10 minutes est **bloquée 15 minutes**. Un lanceur sans mot de passe n'est pas compté comme une
  attaque. Le lanceur et le client n'insistent pas après un refus (le client réessaie au plus une
  fois par minute) : corrige le mot de passe et relance.
* **Limites** : 16 connexions simultanées par adresse, 64 en tout ; messages de 256 Ko au plus ;
  débit limité par type de message ; client trop lent coupé ; 5 s sans message = coupure.
* **Contrôles** (`--validation journal|refus|off`, « Administrer le serveur ») : vitesse plausible des
  Link, distance des coups, débits par message et par joueur ; journal d'anomalies pour
  l'administrateur. Par défaut ils ne refusent rien (« journal seulement »).
* **Rôle d'hôte** : accepté seulement d'un joueur qui a donné le mot de passe (ou, sans mot de
  passe, d'une adresse locale ou de VPN).
* Le serveur ne lit ni n'écrit aucun fichier du jeu ; il ne garde que le monde persistant
  (`mondes/`), sa configuration (`config.json`), le compte admin et le mot de passe dans son volume. L'image du monde vient des clients
  (lecture seule de leur sauvegarde, jamais pendant un événement).

## Rapport d'essai du serveur

Après un essai, pour envoyer au développeur tout ce qui sert au diagnostic côté serveur (le pendant du bouton
« Rapport d'essai » du lanceur, Réglages › Console) :

Dans le dossier de ton `docker-compose.yml` :

```bash
docker compose exec wwmp python -m serveur.rapport            # écrit /donnees/rapports/WWMP_rapport_serveur_<date>.zip
docker compose cp wwmp:/donnees/rapports/WWMP_rapport_serveur_<date>.zip .   # le nom exact est affiché juste avant
```

Avec le journal de Docker en plus (serveur Linux ou macOS ; le zip sort directement sur la sortie standard) :

```bash
docker compose logs --no-color --tail 20000 wwmp | docker compose exec -T wwmp python -m serveur.rapport --journal-stdin --stdout > rapport_serveur.zip
```

(Sous Windows PowerShell, n'utilise pas `--stdout` : `>` y abîme un fichier binaire ; garde la première forme.)

Le zip contient `rapport.txt` (versions, joueurs, réglages appliqués, anomalies, erreurs trouvées), la configuration
du volume, l'état du serveur et son **journal d'anomalies** (lot S2, lu par l'administration du serveur sur 127.0.0.1
dans le conteneur, avec le compte admin du volume ou de `.env`, jamais créé par cette commande), la liste des mondes
(jamais leur contenu), la fin des journaux. **Jamais** dedans : `admin.txt`, `mot_de_passe.txt`, `cles.json`, les
spoilers, le contenu des mondes ; mots de passe, adresses e-mail et adresses IP publiques des joueurs sont masqués
(192.168.1.x garde son début). `--sans-connexion` : rien que les fichiers. Les 10 derniers rapports sont gardés dans
le volume.

## Dépannage

| Ce que tu vois | Pourquoi, et quoi faire |
|---|---|
| « mot de passe incorrect » | Faute de frappe (le mot de passe tient compte des majuscules ; les espaces autour sont ignorés). Recopie-le depuis `mot_de_passe.txt`. |
| « mot de passe requis » | Le serveur est protégé et le champ mot de passe est vide. |
| « trop d'essais… adresse bloquée 15 minutes » | 10 erreurs : attends 15 minutes (ou redémarre le serveur). |
| « utilisateur ou mot de passe admin incorrect » | Ce n'est pas le mot de passe de partie : relis `docker compose exec wwmp cat /donnees/admin.txt` (ou `WWMP_ADMIN_*` de `.env`, qui l'emporte). 5 erreurs bloquent l'administration 15 minutes. |
| « ce serveur n'a pas de compte d'administration » | Serveur lancé sans `--config` (à la main, ou « Sur ce PC ») : seul le serveur Docker s'administre à distance. |
| Après « Enregistrer », « il ne répond pas encore » | Le serveur redémarre (Docker le relance) : clique « Relire » dans un moment ; sinon `docker compose logs wwmp`. |
| « serveur … injoignable » depuis l'extérieur | Serveur arrêté ? pare-feu du serveur (B2) ? redirection de la box (B4, en **TCP**, vers la bonne adresse) ? IP partagée chez l'opérateur (B4) ? Teste d'abord depuis la maison avec l'adresse locale. |
| Marche depuis l'extérieur, pas depuis la maison | Normal avec certaines box : depuis la maison, utilise l'adresse locale du serveur. |
| « hôte ambigu » / « pas d'hôte désigné » en copiant la partie | Une seule personne doit cocher « Je suis l'hôte de la partie », et être en jeu. |
| Le lobby dit « mot de passe refusé » | Corrige le mot de passe à côté de l'adresse, puis Observer. |
| « Packs Cemu : … rien n'est changé » dans la Console | Cemu était ouvert, ou ses réglages sont illisibles (ou absents : Cemu jamais lancé) : ferme Cemu et relance ; sinon coche à la main (Cemu › Options › Graphic packs › Mods : « WWMP Mailbox », « WWMP Pause » avec « Vivant »). |
| Versions différentes | Mettre à jour tous les PC **et** le serveur (même `git log -1 --oneline`). |
| « ce serveur garde le monde X : lance-le depuis le lanceur » | Ta partie coop est liée à un autre monde (ou tu as lancé le client à la main avec un autre `--monde`) : crée une partie coop neuve pour ce monde. |
| « ta partie ne ressemble pas au monde » (quarantaine) | Mauvaise partie chargée (solo ?) : quitte, charge la partie de ce monde. Si c'est bien la bonne, l'administrateur la libère (onglet Mon monde, « Libérer »). |
| « le monde n'a pas encore d'image » | Personne n'a encore joué dans ce monde : commence une partie neuve dans le jeu. |
| Le serveur ne démarre pas : « aucune copie lisible du monde » | Le disque a abîmé `monde.json` et ses copies : remets un fichier de `sauvegardes/` (voir B7). Rien n'a été écrasé. |

## Ports, en résumé

| Port | Protocole | Sert à | Par Internet |
|---|---|---|---|
| 7777 (modifiable) | TCP | le jeu (serveur) | **à ouvrir** (façons B et C), sauf avec Tailscale/ZeroTier |
| 7778 | UDP | « Chercher sur le réseau » (même maison) | inutile, ne pas ouvrir |
