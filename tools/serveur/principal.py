#!/usr/bin/env python3
"""Serveur relais wwmp (étape 2) : salons, positions, autorité par zone.

    python tools/wwmp_server.py                 # écoute sur 0.0.0.0:7777
    python tools/wwmp_server.py --port 7777
    python tools/wwmp_server.py --stats 10      # débit par joueur toutes les 10 s

Le serveur ne lit pas le jeu et ne simule rien : il relaie les états des
joueurs, désigne un joueur autoritaire par zone (stage:salle) et ne relaie les
snapshots d'ennemis que de ce joueur-là. Aucune dépendance externe.

Au démarrage, il affiche les adresses IP de ce PC à donner aux autres joueurs.
Il journalise chaque connexion (nom, IP, version du protocole, du graphic pack
et des outils) et prévient si un client parle un autre protocole ou si les
joueurs n'ont pas la même version du graphic pack.

Étape 8 : il garde aussi, par « groupe de sauvegarde », l'union (OU) des bits de
progression reçus, rediffuse les bits nouveaux aux autres membres du groupe et
envoie l'union complète à chaque nouvel arrivant. L'union vit en mémoire : elle
repart de zéro quand on relance le serveur.

A7 (switches temporaires, message « tsw ») : il numérote chaque changement de switch de
donjon (0x80-0xBF) ou de zone (0xC0-0xEF) et le renvoie à tous les joueurs du groupe
présents dans le stage, émetteur compris (le dernier écrivain gagne). Il garde l'état du
stage (switches de donjon et de zone, ennemis tués de toutes les salles) tant qu'un joueur du
groupe est dans le stage, les switches de salle (0xE0-0xEF) tant qu'un joueur est dans la
salle, et envoie tout l'état du stage à chaque changement de salle. --no-tsw : messages tsw
ignorés.

A4/A6 (objets au sol, messages « drop » et « break ») : il relaie aux autres joueurs de la
zone les objets apparus chez un joueur, et à tout le stage les jarres qu'il a cassées (une
jarre est annoncée dans la zone de sa salle d'origine, une salle du stage de l'émetteur). Il garde les objets
DROP_KEEP s et les jarres cassées tant qu'un joueur est dans la zone, et les envoie à qui y
entre (jarres : à tous, casseur compris ; objets : sauf à leur émetteur, reconnu aussi par son
nom après une reconnexion) ou les redemande (« ground_req », salle rechargée). --no-drops :
messages drop, break et ground_req ignorés.

A3 (bâtons partagés, message « stick ») : il arbitre les ramassages de bâtons (le premier
« grab » reçu gagne, le second reçoit « deny »), relaie prises, poses (« rest »), disparitions
et flammes aux autres joueurs de la zone, déplace la clé d'un porteur qui change de zone
(« gone » dans l'ancienne, « grab » dans la nouvelle), fait tomber le bâton d'un porteur
déconnecté (« rest » cause porteur_parti) et envoie l'état de la zone (« snap ») à qui y entre.
--no-sticks : messages stick ignorés.

A15 (rejoindre la partie, outil wwmp_rejoindre.py du joueur 2) : il choisit l'hôte (--hote NOM,
sinon le client lancé avec --hote, sinon le client de ce PC ; jamais « le seul autre client » :
mauvais sens possible), refuse un outil lancé depuis le PC du serveur sans hôte désigné, lui
transmet la demande, relaie sans la décoder l'image de sa sauvegarde, puis, quand le joueur 2
annonce la copie faite, vide l'union de progression du groupe et envoie « progress reset » à ses
membres (chacun renvoie tout son état). Refus si un client de jeu du PC du joueur 2 est connecté.

Synchro v2 (petites clés partagées, messages « keys », « key_get », « key_use », synchro/cles.py) :
il tient pour chaque groupe et chaque donjon les ENSEMBLES de clés obtenues (coffre, clé au sol…)
et de portes payées, avec cible = max(0, base + obtenues - payées), sans dette (une porte payée
sans clé commune ne retire pas la clé ramassée ensuite) ; un même identifiant ne compte qu'une fois (doublon : « dup » à l'auteur seul, qui revient à la cible). base = max des valeurs
annoncées à la première vue du donjon. Remis à zéro avec la progression après un rejoindre (A15).
En mémoire comme le reste ; --keys-file CHEMIN le garde aussi dans un fichier JSON (un redémarrage
ne recompte pas un coffre déjà vidé). --no-keys : messages ignorés (chacun garde ses clés).

Synchro v2 (coffres partagés, message « coffre », synchro/coffres.py) : registre par groupe des coffres
ouverts (et réceptacles du boss pris), clé (genre, index de sauvegarde, numéro). Le premier rapport
est gardé et envoyé aux AUTRES membres du groupe ; un même coffre rapporté une seconde fois n'est pas
rediffusé (« doublon », cas normal de deux joueurs qui ouvrent en même temps), sauf pour compléter un
objet inconnu ou seulement « probable ». {"full": 1} (inscription) renvoie tout le registre. Au plus
16 rapports par seconde et par joueur, 1024 par groupe. Vidé avec la progression après un rejoindre
(A15, « coffre reset »). En mémoire seulement. --no-coffres (alias --no-chests) : messages ignorés.

Synchro v2 (inventaire partagé phase 1, message « inv », synchro/inventaire.py) : état commun par groupe :
union ordonnée des objets (jetons A/C), OU des bits (chants, Triforce, perles, cartes, îles, drapeaux des
sacs : liste blanche d'offsets), max des plafonds (bourse, carquois, sac de bombes, magie max) et du nombre
de flacons, sac de livraison répliqué (ajout d'un objet absent, retrait d'un objet présent ; retrait d'un
objet absent ignoré et journalisé : deux échanges simultanés, le premier gagne). Les ajouts sont envoyés
aux AUTRES membres ; l'auteur reçoit un accusé (ack = son seq) avec le sac courant. {"full": 1} (connexion,
rechargement) : union, le sac du serveur fait foi sauf pour un hôte (--hote) tant qu'aucun hôte n'a envoyé
son état ; réponse « snap » complète. Vidé par un rejoindre (A15, « inv reset »). Aucun compteur (rubis,
munitions, vie max) : phase suivante. --no-inventaire (alias --no-inventory) : messages ignorés.

Synchro v2 (module 4, cruches / magma / fées / quarts, synchro/drops.py et synchro/cadeaux.py) : les
entrées « break » peuvent porter jug / at (cruche d'eau cassée à l'impact) et « drop » kind « fee »
(fée libre) : relayées comme avant. Message « magma » (dalle de magma figé née chez un joueur) :
relais aux autres joueurs de la zone, gardé MAGMA_KEEP s, renvoyé à qui entre (dalles de moins de
MAGMA_PUSH s, âge corrigé), oublié quand la zone se vide (--no-drops : ignoré aussi). Message
« gift » (quart de cœur au sol ramassé) : premier arrivé par src, relais à TOUT le groupe, registre
renvoyé à l'inscription ({"full": 1}) ; un même src n'est jamais relayé deux fois ; vidé par un
rejoindre (A15, « gift reset »). --no-cadeaux
(alias --no-gifts) : messages gift ignorés.

Cache-cache des Killer Bees à plusieurs (synchro/cachecache.py) : message « cc » (ev lance / attrape / fin / etat). Une
partie par groupe, celle du premier qui l'annonce (le lanceur) : bits « attrapé » unis, relais à tout le groupe ;
fin seulement par le lanceur, ou à sa déconnexion. Toujours actif (rien sans client --share-cachecache).

Synchro v2, module 5 et v3 (B1, synchro/boss_pv.py) : message « boss » (PV d'un boss de la table) :
minimum par (groupe, stage, clé) ; une baisse est relayée aux autres membres du groupe présents dans
la stage ; un rapport plus haut reçoit le minimum en réponse ; oublié quand plus aucun membre du
groupe n'est dans la stage. --no-boss-pv : messages boss ignorés. C1 (``state.busy_info``, libellé de
la cinématique) passe tel quel avec ``state`` : le serveur ne regarde que ``busy``.

Synchro v2 (fantôme touchable A1/A2, synchro/coups_fantome.py) : message « player_hit » (un monstre a
frappé le fantôme d'un joueur chez l'autorité de la zone) relayé à la victime seulement si l'émetteur
est l'autorité actuelle de SA zone, que la victime est un autre joueur de la même zone et pas en
cinématique, au plus PLAYER_HIT_RATE par seconde et par paire. Rien n'est gardé (événement).
--no-ghost-hits : messages ignorés.

Synchro v2, lot 3 (objets mobiles, synchro/mobiles.py) : message « mob » (bloc poussé « push » / « cell »,
bombe-fleur vidée « pick ») : verrou de MobTable.LOCK s par bloc (le premier « push » gagne ; le second
reçoit « deny » seul, la poussée et la case finale du gagnant le recalent ; un « push » dont la case de
départ n'est pas la dernière connue reçoit « deny » puis la dernière « cell »), dernière case gardée par zone et envoyée (« snap ») à qui entre dans la zone ou redemande
l'état (ground_req), oubliée quand la zone se vide ; relais aux autres joueurs de la zone. Message
« boom » (explosion d'une bombe d'un joueur) : relais à la zone, au plus MobTable.BOOM_RATE par seconde
et par joueur, rien n'est gardé. Message « tir » (boulet du canon du bateau, synchro/boulets.py) : même
relais et même débit, rien n'est gardé. Message « lift » (plateformes Hmlif) : accepté de l'autorité de la
zone seulement, relayé. --no-mobiles : messages ignorés.

Trackeur du randomizer (rando/trackeur.py, docs/etudes/trackeur_randomizer.md) : partie avec randomizer seulement.
La section « rando » du lobby porte « trk » {v, rev}. À « trackeur » {rev} (toute connexion présentée), le serveur
répond « trackeur_etat » : objets de l'équipe et niveaux (recalculés depuis l'inventaire commun, la réserve de
clés et le registre des coffres, LUS seulement), lieux faits, lieux accessibles (moteur de logique, contenu vide),
corrections manuelles ; jamais le contenu d'un lieu, la table ni la graine. « trackeur_cmd » {op: lieu | objet |
zone | annuler | raz | verrou} (outil lobby ; raz et verrou : lanceur de l'hôte ; zone : « lieux » = les lieux
montrés par la page, seuls marqués) -> « trackeur_rep » ; ces
corrections ne touchent que le trackeur et ne partent jamais vers un client de jeu. « trk » {vie} : complément lu
par un client de jeu (vie maximale), gardé pour le seul trackeur. TRK_DEBIT messages par seconde et par connexion.
--no-trackeur : rien de tout cela. --trackeur-deduire : cadeaux déduits (coupé par défaut).

Synchro v3, mécanismes rares (synchro/rares.py, docs/etudes/sync3_mecanismes_rares.md) : message « cycle »
{stage, bit, timer} (horloge des cycles du stage : marée de la Tour des Dieux, jets de lave…) accepté du
PROPRIÉTAIRE de l'horloge du stage (le premier joueur du stage qui l'envoie, tant qu'il y reste ;
CycleOwners), relayé aux autres joueurs du même stage. Message « rare » {ev: pillar, key, yaw} (pilier
Hcbh de la Tour des Dieux cassé) relayé aux autres joueurs de la zone ; rien n'est gardé (le jeu
recrée ces piliers à chaque chargement de salle). « rare » {ev: canon, key} (canon Ocanon des plateformes
en mer détruit, synchro/canons.py) : même relais, et la clé est GARDÉE pour la salle ; « rare » {ev: sw_mort, sw}
(switch de mort 0xC0-0xEF d'un Bokoblin / d'une canonnière posé chez l'émetteur) : gardé, jamais relayé. Une salle
gardée (SallesDetruites) est renvoyée ({ev: salle, canons, sw, rappel: 1}) à qui y entre ou la recharge, tant
qu'un joueur l'a encore (il y est, ou c'est la dernière salle qu'il a quittée dans ce stage), GRACE s après une
déconnexion ou un redémarrage (monde persistant, section « salles »). --no-rares : messages ignorés.

Synchro v4, PNJ qui se déplacent (synchro/pnj.py, docs/etudes/sync3_monde_vivant.md §4) : message « pnj »
{zone, list: [{k, p, a, v, m?, i?, d?, n?, c?, t?}]} relayé aux autres joueurs de la zone : toutes les
entrées de l'AUTORITÉ de la zone ; d'un autre joueur, ses seules entrées c (cochon porté) et t (PNJ auquel
il parle), 2 au plus. 8 messages par seconde et par joueur au plus ; rien n'est gardé. --no-pnj : ignoré.

Synchro v3, monde vivant (synchro/monde.py, docs/etudes/sync3_monde_vivant.md) : message « monde » {group, t, d,
run, tp, sea, w} (heure, jour, météo de la mer) relayé aux autres membres du groupe seulement s'il vient de la
RÉFÉRENCE du groupe (le client lancé avec --hote, sinon le plus petit id parmi ceux qui envoient « monde »).
Message « heure » {group, t, d} (Chant du temps qui passe joué par un autre que la référence) : passé à la
référence seule, qui l'applique ; les autres la suivent. Message « vent » {group, x, y, on, init} : registre par groupe (initialisé par l'hôte ; un changement, Requiem
des vents, gagne toujours), renvoyé avec un seq croissant à tous les membres, l'émetteur compris ; un nouveau
venu dont le vent diffère reçoit le registre. --no-monde : messages ignorés.

Synchro v3, herbe coupée et deathlink (synchro/herbe.py, synchro/deathlink.py, docs/etudes/sync3_deathlink_herbe.md) :
message « herbe » {zone, c: [[x, z], …]} (touffes coupées chez l'émetteur, sa zone seulement) gardé pour la zone
tant qu'elle a quelqu'un (envoyé à qui y entre ou recharge la salle) et relayé aux autres de la zone ;
--no-herbe : ignoré. Deathlink, règle de la PARTIE (--deathlink, désactivée par défaut ; --no-deathlink) :
annoncée dans le welcome (« deathlink »), changée à chaud par le lanceur de l'hôte (« regles_cmd », PC du
serveur, ou serveur dédié avec mot de passe : outil lobby authentifié qui se déclare hôte ; diffusée par « regles ») ; message « mort » d'un Link -> « mort » {n, from, name} à tous les
autres Link (une vague ; morts fusionnées pendant 10 s, ignorées pendant 8 s après la connexion du mort ; un
Hanteur ne compte pas).

Vie partagée (synchro/vie_partagee.py), règle de la PARTIE (--vie-partagee, désactivée par défaut ;
--no-vie-partagee ; jamais avec --mode-hanteurs) : annoncée dans le welcome et chaque « regles » (« vie_partagee »),
changée à chaud comme le deathlink (« regles_cmd » {vie_partagee}). Message « vie » : hello {v, max, ack}, sync, ou
delta {seq, ev, d, max, fee?, fl?, zero?} ; une valeur par groupe, deltas appliqués dans l'ordre d'arrivée, bornés à
[0, vie max du groupe], idempotents par « ev » ; réponse {v, max, ack, snap?} (« attente » au lieu de « v » pendant
les 10 s où la fée en flacon du joueur tombé à 0 peut relever le groupe), relayée aux autres membres. Gardée avec
le monde persistant (règle et valeur par groupe).

Difficulté (commun/difficulte.py, docs/etudes/difficulte.md), règle de la PARTIE : dégâts reçus par les Link ×1 à ×4
(--degats) et vie des monstres selon le nombre de Link (--pv-monstres off, demi, link) ; --no-difficulte : jeu
normal, réglage refusé. Annoncée dans le welcome (« difficulte » {degats, pv_monstres, links}), changée à chaud par
le lanceur de l'hôte (« regles_cmd » {difficulte: {…}}), diffusée par « regles » (toujours avec « deathlink ») à
chaque changement de réglage ET du nombre de Link (arrivée, départ, changement de rôle du mode Hanteurs). « links » :
joueurs en jeu qui ne sont pas Hanteurs, toute la partie (pas la salle), 1 à 8. Gardée avec le monde persistant
(jamais « links »). Mode Hanteurs : le score compte le coup d'origine (perte de vie divisée par le multiplicateur).

Synchro v2, lot 3, suite (objets portés, synchro/portage.py) : message « carry » (grab / rest / gone d'une
jarre, d'une pierre ou d'une bombe tenue par un joueur) relayé aux autres joueurs de la zone de
l'émetteur ; un porteur par objet (le premier « grab » gagne, le second reçoit « deny » seul ; verrou
renouvelé par les « grab » de rappel, libéré par rest / gone, la déconnexion, le changement de salle ou
CarryTable.LOCK_TTL s sans rappel). Rien n'est gardé pour les arrivants : le porteur renvoie « grab »
toutes les 3 s. --no-carry : messages ignorés.

Jusqu'à MAX_PLAYERS (8) joueurs (--max-joueurs pour moins) : le suivant reçoit « partie pleine » et
sa connexion est fermée (les outils, role rejoindre, ne comptent pas).

Synchro v2 (compteurs communs, messages « cnt », « pickup », « gone », synchro/compteurs.py) : total des
rubis, flèches et bombes par groupe, somme des deltas idempotente par identifiant d'événement, bornée à
[0, plafond annoncé] ; hello : le serveur fait foi pour un compteur connu (sauf premier hello d'un hôte) ;
chaque réponse porte l'accusé du destinataire (un événement refusé pour débit n'est jamais accusé par le
suivant ; retenue de doublon trop grande : dette sur les gains suivants). Gelé pendant un rejoindre (A15 :
PC du joueur 2 ignoré, les autres gardent leurs événements), puis « cnt reset » (total gardé). A4
« premier arrivé » : le premier « pickup » d'un drop est gardé et « gone » part aux autres joueurs de la
zone qui comptent ce compteur dans le même total ; un second ramassage (ou un même coffre à compteur ouvert deux fois, deux « cc ») retire la valeur une
fois au total. --no-compteurs : messages cnt ignorés (drops « butin par joueur » ; --no-drops coupe pickup).
A16 (menu Coop) : les noms des joueurs sont rendus UNIQUES (« Link », « Link 2 »…) et le nom
effectif est renvoyé dans ``welcome`` (champ ``name``, avec ``outils`` = version du serveur) ; à
l'arrêt (Ctrl+C, CTRL_BREAK du compagnon, ou --arret-fichier), le serveur annonce
« l'hôte a fermé la partie » (message ``bye``) avant de couper.

Mode Hanteurs, phase 0 (synchro/hanteurs.py, --mode-hanteurs, désactivé par défaut ; --hanteurs-config FICHIER) :
rôles Link / Hanteur répartis quand tout le monde est là (Hanteurs choisis d'abord, le reste au hasard, au
moins un Link), jamais d'autorité de zone pour un Hanteur, sa position relayée aux seuls Hanteurs (champ « hv »
du state, Hanteur manifesté que les autres Hanteurs dessinent : retiré du state de tout autre émetteur), ses
messages « monde » ignorés (et ceux d'un ancien Hanteur pendant 3 s) ; points (gain passif, dégâts de ses
monstres sur les Link, monstres tués par les Link), invocations validées puis diffusées (« hspawn ») à la
zone, rotation (crawl, minuterie, mixte). Commandes de l'hôte (« hanteurs_cmd » : répartir, rotation, config)
acceptées seulement du lanceur (outil lobby) de l'hôte : PC du serveur, ou, avec un mot de passe de partie,
outil lobby authentifié qui se déclare hôte (Server.lanceur_hote).
Phase 2 (synchro/potion.py, réglage « potion » de la partie, activé par défaut) : « hviser » (Link visé par le
monstre possédé, ajouté au « hlaisse ») ; potion de possession d'un Link : « hpotion » d'un Hanteur (refus avec
raison), pendant la potion le state du Hanteur est relayé à tous sous l'identité du Link possédé (avec son
équipement ; marqué « clone » pour le possédé lui-même), celui du possédé à personne, le possédé n'a jamais
l'autorité ; « pvp » (coups d'épée vus par Python) : clone -> Link relayé à la victime (« pvp_coup »), Link -> clone =
exorcisme ; « player_hit » vers un possédé gardé pour la fin ; « hresister », « hpotion_stop ».
Phase 1 (synchro/laisse.py, réglage « possession » de la partie, activé par défaut) : « hposseder » d'un Hanteur
(monstre possédable le plus proche, vu vivant chez l'autorité de sa salle, payé en points), puis chaque state du
Hanteur part à l'autorité de la zone (« hlaisse » : position, orientation, compteur d'ordres « hordre ») ; fin sur
« hlacher », mort du monstre, salle quittée, trop loin, durée maximale ou changement de rôle ; « hmanif » : indice
« une présence rôde » aux Link de la salle.
Effets de fumée (réglage « effets » de la partie : tous / hanteurs / coupes, « tous » par défaut) : « hfx {quoi:
possede | lache | potion | libere | echange | apparition, pos, stage, room, key ou cible} », émis par le serveur
SEULEMENT (un « hfx » venu d'un client n'est ni lu ni relayé), aux joueurs de la salle qui annoncent « effets », hors
cinématique, state de moins de 5 s ; au plus 4 par seconde et par salle. Échange de Crawl : « echange » (là où le
Link est tombé, jamais à lui) au moment où l'échange a vraiment lieu, « apparition » (nouveau Link, aussi à la
rotation) quand son state de Link arrive vraiment dans la salle. « hspawn » renvoyé à qui entre dans la salle :
« rappel: 1 ».
Phases 3 et 4 (synchro/ordres.py) : « hordre {ordre: attaque | lourde | garde} » d'un Hanteur qui possède : le dernier
ordre part dans le « hlaisse » (« ord »), l'autorité le joue par CALL 10 ; « hinfluence {ordre} » d'un Hanteur
(réglage « influence » de la partie, activé par défaut) : attaque d'un boss choisie, payée, envoyée (« hboss ») aux
Link qui combattent ce boss et dont le client sait l'appliquer (fonction « influence ») ; « hboss_res » de chaque
Link (appliqué ou pas) : note au Hanteur, points rendus si personne n'a pu.

A17 (lobby du lanceur) : à ``{"type": "lobby"}``, il répond la liste des joueurs connectés (nom,
hôte, stage/salle, cinématique, pack, ping, depuis quand). Le lanceur se présente en outil
(``role: rejoindre``, ``outil: lobby``) : ni join, ni diffusion. Le ping de chaque joueur est
celui que son client joint à ses « ping » (champ ``rtt``, ignoré s'il manque).

Pause vivante (pack WWMP Pause, OBLIGATOIRE, synchro/pause_vivante.py) : chaque client annonce dans son hello ce que
son pack fait (``pause`` : masque, -1 pack pas chargé ; ``pause_fmt`` : format de sa zone). Un client de jeu sans le
pack, avec un pack trop ancien (v7), réglé sur « Figé » pour « Monde pendant le menu », ou qui n'annonce rien (outils
anciens) est refusé (erreur ``code: pause``, ``raison``, ``msg`` / ``msg_en`` qui disent quoi faire) : premier
hello, rejoindre en cours de partie, reconnexion, serveur dédié, monde persistant, mode Hanteurs. Le serveur rend le
masque dans le lobby, le welcome et le join, et prévient seulement quand la sauvegarde, les options, le Game Over ou
la Ballade figent le monde chez certains joueurs (réglages libres).

Jouer par Internet (commun/acces.py, docs/hotes/JOUER_PAR_INTERNET.md) : --mot-de-passe-fichier F, la
variable WWMP_MOT_DE_PASSE ou --donnees DOSSIER (serveur dédié : clés et mot de passe gardés dans
ce dossier, mot de passe créé au premier lancement) protègent la partie : défi / preuve HMAC dans
« hello », refus après un délai, adresse bloquée après trop d'erreurs, connexions limitées par IP.
Sans mot de passe (défaut, réseau local), rien ne change ; « hote » n'est alors accepté que d'une
adresse locale ou de VPN. Le mot de passe n'est jamais journalisé. Aucune dépendance (ni numpy, ni
Cemu) : le serveur tourne seul sur une machine sans joueur (Docker : dépôt WindWakerHD-Multiplayer-Server).

Serveur dédié Docker (serveur/admin.py) : --config /donnees/config.json garde la configuration de la partie (monde,
randomizer, mode Hanteurs, joueurs max, deathlink, difficulté) dans le volume ; un compte admin (WWMP_ADMIN_*, sinon
admin.txt du volume) la change depuis le lanceur (« admin_defi » / « admin », défi HMAC distinct du mot de passe de
partie) : à chaud quand c'est sûr, sinon redémarrage propre (Docker relance). config.json l'emporte sur les options.

Monde persistant (serveur/monde_persistant.py, docs/etudes/monde_persistant.md) : --monde NOM_OU_ID (automatique avec
--donnees, sauf --sans-monde) : tout l'état partagé (progression, clés, coffres, quarts, inventaire, compteurs, vent,
deathlink, joueurs, image de sauvegarde du monde) est relu au démarrage et écrit de façon atomique (au plus toutes
les --monde-ecriture s, vite après un gain, à l'arrêt) ; aucun client n'impose plus ses valeurs (pas d'hôte) ;
« rejoindre » est servi par le serveur avec l'image du monde, sans remise à zéro ; le hello d'un autre monde est
refusé ; une partie qui ne ressemble pas au monde est mise en quarantaine ; mode Hanteurs permis (--mode-hanteurs) :
session temporaire au-dessus du monde, seuls ses réglages sont gardés (regles.hanteurs), la manche jamais ; rien d'un
Hanteur n'entre dans le monde, son verdict vient d'un « progress » marqué « jug » (jamais fusionné), l'image n'est
jamais demandée à un Hanteur, un Link possédé ou un ancien Hanteur (--monde-image-anciens-hanteurs pour ces
derniers, désactivé par défaut) ; image demandée (« image_req ») au joueur
le plus avancé, jamais en recul. Le welcome porte l'époque du serveur (un client resté lancé renvoie toute sa
progression à un serveur redémarré).

Hôte d'autorité préféré (lot S1, serveur/autorite.py, docs/etudes/serveur_dedie_jeu.md §9) : --autorite-preferee NOMS
(pseudos dans l'ordre, ou « @hote ») ; config.json « autorite_preferee » sur le serveur dédié, changé à chaud. Le PC
préféré est choisi d'abord à une passation obligée, reprend l'autorité d'une zone calme où il est installé
(hystérésis, jamais en combat ni en cinématique, jamais un Hanteur) et la rend s'il ne répond plus (repli). Sans
réglage : rien ne change.

Validation côté serveur (lot S2, serveur/validation.py) : --validation journal (défaut) | refus | off ; config.json
« validation » sur le serveur dédié, changé à chaud. Vitesse plausible des Link (sauts et vitesses expliqués par les
téléportations légitimes : scène, salle, cinématique, salle rechargée, vie, point de reprise, Hanteurs), distance des
« hit » au monstre et des « player_hit » à la victime, débits par message ; journal d'anomalies lu dans « Administrer le
serveur » (« anomalies » de lire). « refus » : coups impossibles et messages en trop non traités.

Joueur robot (serveur/robot.py, docs/etudes/joueur_robot.md) : --robot off (défaut) | auto. Une instance du jeu sans
joueur, lancée par le serveur (--robot-commande, relancée après un plantage, au plus 3 fois en 10 min ; arrêtée après
--robot-repos s de repos), se présente avec role « robot » et la clé du serveur (--robot-cle ou WWMP_ROBOT_CLE, sinon
tirée au hasard et passée à la commande). Elle prend l'autorité de la zone qui a le plus de joueurs (2 au moins ;
hystérésis), après y être entrée comme non-autorité (marionnettes à jour : pas de doublon), et la rend aux joueurs avant
de partir ; aucune zone à 2 : « dors ». Jamais un joueur : ni liste, ni lobby, ni place, ni fantôme, ni monde ; seuls
les messages d'une autorité de zone sont acceptés d'elle. État dans « Administrer le serveur » (« robot » de lire) et le
lobby. Désactivé avec le mode Hanteurs. --robot essai (serveur hébergé sur le PC du joueur, jamais en dédié) : essai
solo, le robot prend la zone du joueur même seul, sans gain exigé ni repos, et le suit ; --robot-essai-latence MS ajoute
MS d'aller-retour entre le serveur et le robot (un gérant éloigné).
Serveur de jeu (docs/etudes/serveur_jeu_v2.md, docs/hotes/SERVEUR_DE_JEU.md) : --robot hote = le serveur est l'hôte de
TOUTE zone occupée, même par un joueur seul, sans gain exigé ; --robots N = N robots (un jeu caché par zone, jamais
deux dans la même ; hello « rang » ; sans robot libre, la zone garde un joueur comme hôte) ; --monde-vivant = l'heure
commune avance aussi quand il n'y a personne.

Mises à jour du mod (serveur/maj.py, docs/joueurs/MISES_A_JOUR.md) : --mises-a-jour DOSSIER (ou, serveur
dédié, DONNEES/mises_a_jour) : le serveur propose le paquet signé de ce dossier aux
lanceurs (messages maj_info / maj_part sur la même connexion, après le mot de passe), après avoir
vérifié sa signature ; un paquet faux ou abîmé n'est pas distribué.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import math
import os
import socket
import sys
import time

from commun.chemins import TOOLS  # noqa: E402
from commun.protocole import SIGNAL_ENTRE, valid_signal  # noqa: E402  (signal « viens ici », mod natif 0.33.0)
from commun.protocole import (CLIENT_VERSION, DEFAULT_PORT, MAX_LINE, MAX_PLAYERS, REJOINDRE_MAX, REJOINDRE_PART,
    REJOINDRE_TIMEOUT, TIMEOUT, VERSION, decode, encode, zone_of)
from jeu.progress import bits_count, bits_or, bits_sub, from_wire, to_wire  # noqa: E402
from synchro.drops import DropWatch, MagmaWatch, valid_break, valid_drop, valid_magma, zone_stage  # noqa: E402
from synchro.cadeaux import valid_record  # noqa: E402
from synchro.cachecache import CacheCacheServeur  # noqa: E402
from commun import latence  # noqa: E402
from synchro.boss_pv import BossPvTable, valid_boss  # noqa: E402
from synchro import boss_miroir  # noqa: E402
from synchro import boss_phase  # noqa: E402
from synchro import boss_unique  # noqa: E402
from synchro import boss_volee  # noqa: E402
from synchro.boss import BOSS_STAGES  # noqa: E402

# Tours de la Forteresse Maudite : un jeu caché envoyé là se fige dès le chargement (plus rien ne bouge, sans erreur),
# même sans aucun de nos mods (docs/etudes/serveur_jeu_v2.md §12.9). Le jeu du serveur n'y va donc pas : les joueurs y
# gardent l'autorité, comme sans serveur de jeu.
STAGES_FIGENT_LE_JEU_CACHE = frozenset({"Mjtower", "M2tower"})
from synchro.coups_fantome import valid_player_hit  # noqa: E402
from synchro import coups_monstres  # noqa: E402
from synchro import coups_projectiles  # noqa: E402
from synchro.saisie import valid_saisie  # noqa: E402
from synchro.mobiles import MobTable, valid_boom, valid_lift, valid_mob  # noqa: E402
from synchro.boulets import valid_tir  # noqa: E402
from synchro.rares import CycleOwners, valid_cycle, valid_rare  # noqa: E402
from synchro.canons import SallesDetruites, valid_canon, valid_sw_mort  # noqa: E402
from synchro.monde import MondeServeur  # noqa: E402
from synchro.herbe import HerbeTable, valid_herbe  # noqa: E402
from synchro.pnj import filtre_serveur as pnj_filtre, valid_pnj  # noqa: E402
from synchro.deathlink import MortsServeur  # noqa: E402
from synchro.vie_partagee import VieServeur, valid_vie  # noqa: E402
from synchro import coups_joueurs as cj_mod  # noqa: E402  (coups entre joueurs : docs/etudes/autres_zelda.md §8)
from synchro import pause_vivante as pvmod  # noqa: E402
from commun import difficulte as dif_mod  # noqa: E402
from synchro.portage import CarryTable, valid_carry  # noqa: E402
from synchro.batons import STICK_MAX, valid_stick  # noqa: E402
from commun import arret  # noqa: E402
from commun import acces  # noqa: E402
from commun import triches as triches_mod  # noqa: E402
from serveur import dedie as serveur_dedie  # noqa: E402
from serveur import admin as adm  # noqa: E402  (serveur dédié Docker : compte admin, config.json du volume)
from serveur import autorite  # noqa: E402  (lot S1 : hôte d'autorité préféré)
from serveur import validation as valid_mod  # noqa: E402  (lot S2 : validation côté serveur, journal d'anomalies)
from serveur import robot as robot_mod  # noqa: E402  (joueur robot : docs/etudes/joueur_robot.md)
from serveur import maj as maj_serveur  # noqa: E402
from serveur import medailles as med_mod  # noqa: E402  (médailles de fin de donjon : docs/etudes/autres_zelda.md §2)
from serveur import ralliement as ral_mod  # noqa: E402  (chant de ralliement : docs/etudes/autres_zelda.md §7)
from serveur import porter as por_mod  # noqa: E402  (porter un coéquipier, lot P3 : docs/etudes/porter_coequipier.md)
from commun import porter as por_commun  # noqa: E402
from serveur import courses as crs_mod  # noqa: E402  (courses en bateau : docs/etudes/autres_zelda.md §3)
from serveur import bonheur as bon_mod  # noqa: E402  (Fragments du Bonheur : docs/etudes/autres_zelda.md §4)
from serveur import tenues as ten_mod  # noqa: E402  (tenues à pouvoir : docs/etudes/autres_zelda.md §6)
from commun import tenues as ten_commun  # noqa: E402
from serveur import fusions as fus_mod  # noqa: E402  (tenues à pouvoir, partie B : les fusions)
from serveur import releve as rel_mod  # noqa: E402  (relever un coéquipier : docs/etudes/autres_zelda.md §5)
from serveur import camouflage as cam_mod  # noqa: E402  (camouflage de la tenue jaune : autres_zelda §6)
from commun import camouflage as cam_commun  # noqa: E402
from commun import fusions as fus_commun  # noqa: E402
from synchro.cles import KeyPool  # noqa: E402
from synchro.compteurs import VALEURS as CNT_VALEURS, CompteurPool, valid_cnt, valid_pickup  # noqa: E402
from commun.protocole import valid_coffre, valid_inv, valid_key_id, valid_key_slot  # noqa: E402
from commun.protocole import horodatage, valid_epos  # noqa: E402  (lot L2 de la latence)
from synchro import hanteurs as hmod  # noqa: E402
from synchro import potion as potion_mod  # noqa: E402
from synchro import enfermement as enf_mod  # noqa: E402
from serveur import monde_persistant as mpm  # noqa: E402
from rando import noyau as rnd  # noqa: E402
from commun import couleurs  # noqa: E402
from rando import trackeur as tk  # noqa: E402
from rando import moteur as rm  # noqa: E402
from rando import fichiers as rfi  # noqa: E402  (randomizer par fichiers générés localement)
from rando import lot9 as L9  # noqa: E402  (randomizer, étape 5 : signaux du trackeur)

COULEURS_VUES_MAX = 64  # couleurs retenues (jeton ou nom -> couleur) pour les joueurs qui reviennent
BUSY_DEFER_MAX = 10.0  # s : A11, passation d'autorité différée au plus tant que les présents sont en cinématique
SNAPSHOT_KEEP = 2.0  # s : âge max du dernier snapshot renvoyé à qui entre dans la zone
# Client trop lent (tampon d'envoi plein) : on saute les messages remplaçables (état, snapshot),
# puis on coupe. Les messages d'événements (autorité, hit, join/leave…) passent toujours.
SLOW_SKIP = 1 << 20
SLOW_DROP = 16 << 20
CARTE_PAS = 2.0            # s : carte marine partagée, entre deux envois des positions en mer
CARTE_MER_MAX = 350000.0   # la mer : 7 x 7 carrés de 100000, centrés sur 0
DROPPABLE = {"state", "enemies", "epos", "fen"}   # epos : lot L2 de la latence (positions remplaçables) ; fen : états
                                                 # complets des entrées de l'attaquant d'une vraie fusion (le suivant
                                                 # remplace, le mod du pilote relâche tout sans nouvelles)
TSW_MAX = 256          # A7 : changements au plus par message tsw
DROP_KEEP = 8.0        # s : A4, âge max d'un objet apparu renvoyé à qui entre dans la zone
BREAK_MAX = 64         # A6 : jarres cassées gardées au plus par zone
NAME_MAX = 24          # longueur maximale d'un nom
KEYS_RATE = 10         # synchro v2 : faits de clés acceptés au plus par seconde et par joueur
COFFRES_RATE = 16      # synchro v2 : rapports de coffres acceptés au plus par seconde et par joueur
COFFRES_MAX = 1024     # synchro v2 : coffres gardés au plus par groupe
MUETS_MAX = 4096       # randomizer, étape 5 : coffres muets gardés au plus par joueur (comme le client)
MUETS_JOUEURS_MAX = 64  # randomizer, étape 5 : joueurs dont les coffres muets sont gardés
RANDO_INDICE_TOUS_LES = 10   # randomizer complet, étape 4 : un indice de plus tous les N coffres ouverts par l'équipe
TRK_PERIODE = 0.25            # trackeur : l'état commun est recalculé au plus 4 fois par seconde
TRK_DEBIT = 10                # trackeur : demandes et corrections par seconde et par connexion
RANDO_LOBBY_PRIS_MAX = 1024   # coffres ouverts publiés au plus dans le lobby (onglet « Indices » ; ~14 Ko)
INV_RATE = 20          # synchro v2 : messages « inv » acceptés au plus par seconde et par joueur
MAGMA_KEEP = 15.0      # s : synchro v2, dalles de magma gardées par zone
MAGMA_PUSH = 11.0      # s : âge max (réception) d'une dalle renvoyée à qui entre (il lui reste >= 4 s)
MAGMA_MAX = 32         # dalles gardées au plus par zone
GIFT_RATE = 8          # synchro v2 : cadeaux (quarts) acceptés au plus par seconde et par joueur
GIFTS_MAX = 512        # cadeaux gardés au plus par groupe
SAISIE_RATE = 2        # synchro v4 (saisie transmise) : messages « saisie » relayés au plus par seconde et par paire
PLAYER_HIT_RATE = 4    # synchro v2 (A2) : coups « player_hit » relayés au plus par seconde et par paire
BOSS_RATE = 12         # synchro v2 : messages « boss » acceptés au plus par seconde et par joueur
CNT_RATE = 30          # synchro v2 : messages « cnt » (compteurs communs) acceptés au plus par seconde et par joueur
PICKUP_KEEP = 30.0     # s : A4 premier arrivé, ramassages gardés (un drop disparaît seul en ~10 s)
PICKUP_MAX = 4096      # ramassages gardés au plus
PICKUP_RATE = 10       # « pickup » acceptés au plus par seconde et par joueur
BYE_MSG = "l'hôte a fermé la partie"
BYE_REDEMARRAGE = "le serveur redémarre (nouvelle configuration), reconnexion dans quelques secondes"


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


class Player:
    def __init__(self, pid: int, writer: asyncio.StreamWriter):
        self.id = pid
        self.name = f"joueur{pid}"
        self.writer = writer
        self.zone: str | None = None
        self.zone_since = 0.0
        self.zone_prev: str | None = None     # dernière zone quittée dans le même stage (salle encore « chez lui »)
        self.last_seen = time.monotonic()
        self.connected = time.monotonic()
        self.peer = "?"
        self.pack: int | None = None     # version du graphic pack annoncée (hello), None si inconnue
        # pack WWMP Pause (menu vivant, obligatoire) : masque des réglages, -1 pack pas chargé, None inconnu (outils
        # anciens) ; format de sa zone (None : pas annoncé) ; synchro/pause_vivante.py
        self.pause: int | None = None
        self.pause_fmt: int | None = None
        self.triches: list[str] = []     # triches du portage natif actives chez lui (« triches », commun/triches.py)
        self.couleur = 0                 # couleur de tenue attribuée au hello (1..8 ; 0 : pas encore), commun/couleurs.py
        self.hello = False
        self.bytes_in = 0
        self.bytes_out = 0
        self.skipped = 0
        self.group: str | None = None     # étape 8 : groupe de progression
        self.busy = False                 # A11 : en cinématique (champ optionnel « busy » de state)
        self.nc = False                   # coups entre joueurs : intouchable (champ « nc » de state : menu, porté, porteur)
        self.prtbl = False                # porter un coéquipier : ce Link peut être porté (champ « prtbl » de state)
        self.tsw_group: str | None = None  # A7 : groupe des switches temporaires (None = pas inscrit)
        self.pos: list | None = None      # A3 : dernière position de Link (state), pour lâcher son bâton
        self.carried: list = []           # A3 : bâtons tenus pendant un passage sans zone
        self.auth = True                  # Internet : False tant que le mot de passe n'est pas prouvé
        self.role = "jeu"                 # A15 : "rejoindre" = outil wwmp_rejoindre (pas un joueur)
        self.feat: set[str] = set()       # A15 : fonctions annoncées dans hello (feat)
        self.hote = False                 # A15 : client lancé avec --hote
        self.ip = "?"                     # A15 : adresse sans port (même PC ?)
        self.jeton: str | None = None     # A16 : jeton du processus client (reconnexion = même joueur)
        self.keys_group: str | None = None  # synchro v2 : groupe des petites clés (None = pas inscrit)
        self.key_times: list[float] = []  # synchro v2 : heures des derniers faits (débit)
        self.coffres_group: str | None = None  # synchro v2 : groupe des coffres (None = pas inscrit)
        self.coffre_times: list[float] = []    # synchro v2 : heures des derniers rapports (débit)
        self.inv_group: str | None = None      # synchro v2 : groupe de l'inventaire (None = pas inscrit)
        self.inv_ack = 0                       # synchro v2 : plus grand seq « inv » intégré
        self.inv_times: list[float] = []
        self.gift_group: str | None = None     # synchro v2 : groupe des cadeaux (quarts de cœur)
        self.gift_times: list[float] = []
        self.boss_group: str | None = None     # synchro v2 (B1) : groupe des PV de boss partagés
        self.boss_times: list[float] = []
        self.cnt_group: str | None = None      # synchro v2 : groupe des compteurs communs (None = pas inscrit)
        self.cnt_ks: set[str] = set()          # compteurs partagés par ce joueur (hello)
        self.cnt_ack = 0                       # plus grand seq « cnt » intégré
        self.cnt_trou: int | None = None       # seq du premier événement refusé (débit) : rien d'autre accusé
        self.cnt_times: list[float] = []
        self.vie_group: str | None = None      # vie partagée : groupe (None = pas inscrit)
        self.vie_ack = 0                       # vie partagée : plus grand seq « vie » intégré
        self.vie_trou: int | None = None       # vie partagée : seq du premier événement refusé (débit)
        self.vie_times: list[float] = []
        self.signal_t = float("-inf")          # signal « viens ici » : heure du dernier relayé
        self.mer: tuple[float, float] | None = None   # carte marine partagée : dernière position en mer (x, z)
        self.medailles_mort_t = float("-inf")  # médailles : dernier Game Over compté (« mort » du deathlink)
        self.pickup_times: list[float] = []
        self.hit_times: dict[int, list[float]] = {}   # synchro v2 (A2) : victime -> heures des coups relayés
        self.saisie_times: dict[int, list[float]] = {}   # synchro v4 (saisie transmise) : victime -> heures relayées
        self.rtt: float | None = None     # A17 : ping (ms) annoncé par le client dans ses « ping »
        self.outil = ""                   # A17 : outil annoncé dans hello (« lobby » = lanceur)
        # monde persistant (serveur/monde_persistant.py) : verdict de la garde de contenu, messages gardés en
        # attendant, progression annoncée (tot) et dernière activité (choix du joueur qui fournit l'image)
        self.mp_verdict = "ok"
        self.mp_tampon: list[dict] = []
        self.ex_hanteur = False        # hello « ex_hanteur » : a été Hanteur (même si ce serveur tourne sans le mode)
        self.mp_image_refus: float | None = None   # monde persistant : image refusée par son client, jusqu'à…
        self.mp_t0 = 0.0
        self.mp_tot: int | None = None
        self.mp_actif = 0.0
        # lot S1 (serveur/autorite.py) : heure du dernier « state », début de la suite de « state » sans trou
        self.state_t: float | None = None
        self.frais_depuis = 0.0

    @property
    def joueur(self) -> bool:
        """Client de jeu présenté (hello), par opposition à une sonde ou à l'outil rejoindre."""
        return self.hello and self.role == "jeu"

    def where(self) -> tuple[str | None, int | None]:
        """(stage, salle) tirés de la zone « stage:salle »."""
        if not self.zone:
            return None, None
        stage, _, room = self.zone.rpartition(":")
        try:
            return stage, int(room)
        except ValueError:
            return stage, None

    def send(self, msg: dict) -> None:
        if self.writer.is_closing():
            return
        try:
            buffered = self.writer.transport.get_write_buffer_size()
        except (AttributeError, RuntimeError):
            buffered = 0
        if buffered > SLOW_DROP:
            log(f"{self.name} : {buffered} octets en attente, connexion coupée")
            self.writer.close()
            return
        if buffered > SLOW_SKIP and msg.get("type") in DROPPABLE:
            self.skipped += 1
            return
        data = encode(msg)
        self.bytes_out += len(data)
        self.writer.write(data)


class Server:
    def __init__(self, busy_defer: bool = True, tsw: bool = True, drops: bool = True, sticks: bool = True,
                 hote: str | None = None, keys: bool = True, keys_file: str | None = None,
                 coffres: bool = True, inventaire: bool = True, cadeaux: bool = True, boss: bool = True,
                 max_players: int = MAX_PLAYERS, ghost_hits: bool = True, mobiles: bool = True,
                 compteurs: bool = True, carry: bool = True, mode_hanteurs: bool = False,
                 hanteurs_cfg: dict | None = None, rares: bool = True, monde: bool = True,
                 herbe: bool = True, deathlink: bool = False, mot_de_passe: str | None = None,
                 monde_persistant: "mpm.Monde | None" = None, rando_graine: str | None = None,
                 difficulte: dict | None = None, difficulte_on: bool = True,
                 rando_mode: str = "sur", rando_options: dict | None = None,
                 trackeur: bool = True, trackeur_deduire: bool = False, pnj: bool = True,
                 autorite_preferee: str | None = None, validation: str = valid_mod.DEFAUT,
                 robot: str = robot_mod.DEFAUT, robot_cle: str | None = None, robot_commande=None,
                 robot_repos: float = robot_mod.REPOS_ARRET, robot_journal: str | None = None,
                 vie_partagee: bool = False, robot_latence: int = 0, robots: int = 1, medailles: bool = True,
                 monde_vivant: bool = False,
                 coups_joueurs: bool = False,
                 ralliement: bool = True, courses: bool = True, porter: bool = True,
                 bonheur: bool = True, essai_bonheur: bool = False,
                 bonheur_quarts_donjon: int = bon_mod.QUARTS_DONJON,
                 bonheur_quarts_partie: int = bon_mod.QUARTS_PARTIE, tenues: bool = True,
                 essai_tenues: bool = False, tenues_distance: float = ten_commun.DISTANCE,
                 essai_fusions: bool = False, releve: bool = True, essai_relever: bool = False,
                 camouflage: bool = True, camouflage_duree: float = cam_commun.DUREE, essai_camouflage: bool = False,
                 fusion_entrees_hz: int = fus_commun.ENTREES_HZ, fusion_peremption_ms: int = fus_commun.PEREMPTION_MS):
        self.players: dict[int, Player] = {}
        # camouflage de la tenue jaune (serveur/camouflage.py) : règle de la PARTIE, ALLUMÉE par défaut (--no-camouflage),
        # qui ne joue qu'avec les tenues à pouvoir ; durée --camouflage-duree (4 s). ESSAI (--essai-camouflage,
        # Réglages › Essais) : un client d'essai est traité en jaune ; à retirer après validation
        self.camouflage = cam_mod.CamouflageServeur(on=camouflage, essai=essai_camouflage, duree=camouflage_duree)
        self._camouflage_t = 0.0
        if self.camouflage.essai:
            log("camouflage de la tenue jaune : ESSAI allumé (--essai-camouflage) : un client d'essai est traité en "
                "jaune et peut se camoufler à la demande (Réglages › Essais du lanceur)")
        # relever un coéquipier (serveur/releve.py) : règle de la PARTIE, ALLUMÉE par défaut (--no-releve), qui ne JOUE
        # qu'avec le deathlink (jamais avec la vie partagée ni en mode Hanteurs). ESSAI (--essai-relever, Réglages ›
        # Essais) : faux coéquipier « Essai Secours » d'un client d'essai ; à retirer après validation
        self.releve = rel_mod.ReleveServeur(on=releve, essai=essai_relever and not mode_hanteurs)
        self._releve_t = 0.0
        if self.releve.essai:
            log("relever un coéquipier : ESSAI allumé (--essai-relever) : faux coéquipier « Essai Secours » d'un client "
                "d'essai qui te relève ou tombe (Réglages › Essais du lanceur)")
        # tenues à pouvoir, partie B : les FUSIONS (serveur/fusions.py) : avec la règle des tenues, jamais en mode
        # Hanteurs ni avec le robot. ESSAI (--essai-fusions, Réglages › Essais) : faux coéquipier d'un client d'essai ;
        # à retirer après validation
        # VRAIE fusion (mod 0.50.0) : états « fen » de l'attaquant relayés tout de suite au pilote (--fusion-entrees-hz,
        # --fusion-peremption-ms : donnés dans le « debut », pas de changement à chaud)
        self.fusions = fus_mod.FusionsServeur(on=tenues, essai=essai_fusions, entrees_hz=fusion_entrees_hz,
                                              peremption_ms=fusion_peremption_ms)
        self._fusions_t = 0.0
        if self.fusions.essai:
            log("fusions de tenues : ESSAI allumé (--essai-fusions) : faux coéquipier d'un client d'essai qui charge sa "
                "tournoyante ou tient une bombe (Réglages › Essais du lanceur)")
        # tenues à pouvoir (serveur/tenues.py) : règle de la PARTIE, ALLUMÉE par défaut (--no-tenues) ; aussi en mode
        # Hanteurs (un Hanteur n'a pas de tenue, un Link possédé garde la sienne) ; jamais le robot. ESSAI
        # (--essai-tenues, Réglages › Essais) : couleur de pouvoir changée à chaud, faux coéquipier de duo d'un client
        # d'essai ; à retirer après validation
        self.tenues = ten_mod.TenuesServeur(on=tenues, essai=essai_tenues, distance=tenues_distance)
        self._tenues_t = 0.0
        if self.tenues.essai:
            log("tenues à pouvoir : ESSAI allumé (--essai-tenues) : couleur de pouvoir changée à chaud et faux "
                "coéquipier de duo d'un client d'essai (Réglages › Essais du lanceur)")
        # Fragments du Bonheur (serveur/bonheur.py) : règle de la PARTIE, ALLUMÉE par défaut (--no-bonheur), toujours
        # coupée en mode Hanteurs ; poches gardées dans le monde persistant. ESSAI (--essai-bonheur, Réglages › Essais) :
        # tirage à 1 sur 2, faux coéquipier assorti d'un client d'essai, récompenses forcées ; à retirer après validation
        if (bonheur or essai_bonheur) and mode_hanteurs:
            log("fragments du Bonheur : coupés avec le mode Hanteurs")
        self.bonheur = bon_mod.BonheurServeur(on=bonheur and not mode_hanteurs,
                                              essai=essai_bonheur and not mode_hanteurs,
                                              quarts_donjon=bonheur_quarts_donjon,
                                              quarts_partie=bonheur_quarts_partie)
        if self.bonheur.essai:
            log("fragments du Bonheur : ESSAI allumé (--essai-bonheur) : 1 chance sur 2 par monstre, faux coéquipier "
                "« Essai Assorti » d'un client d'essai, récompenses forcées (Réglages › Essais du lanceur)")
        # courses en bateau (serveur/courses.py) : lancées par le lanceur de l'hôte (« course_cmd »), jamais en mode
        # Hanteurs. Essai solo retiré le 2026-11-17 (validé en jeu)
        self.courses = crs_mod.CoursesServeur(on=courses and not mode_hanteurs)
        # chant de ralliement (serveur/ralliement.py) : règle de la PARTIE, ALLUMÉE par défaut (--no-ralliement),
        # toujours coupée en mode Hanteurs. Essai solo retiré le 2026-11-17 (validé en jeu)
        if ralliement and mode_hanteurs:
            log("chant de ralliement : coupé avec le mode Hanteurs")
        self.ralliement = ral_mod.RalliementServeur(on=ralliement and not mode_hanteurs)
        # porter un coéquipier (lot P3, serveur/porter.py) : règle de la PARTIE, ALLUMÉE par défaut (--no-porter),
        # jamais en mode Hanteurs ; arbitrage de la prise et relais seulement
        self.porter = por_mod.PorterServeur(on=porter and not mode_hanteurs)
        self._porter_t = 0.0
        # coups entre joueurs (synchro/coups_joueurs.py) : règle de la PARTIE, coupée par défaut, jamais en mode
        # Hanteurs (son PvP reste le sien) ; recul seul (aucun dégât). Essai solo retiré le 2026-11-17 (validé en jeu)
        if coups_joueurs and mode_hanteurs:
            log("coups entre joueurs : désactivés avec le mode Hanteurs (son PvP reste le sien)")
        self.coups = cj_mod.CoupsServeur(on=coups_joueurs and not mode_hanteurs)
        # médailles de fin de donjon (serveur/medailles.py) : pour la gloire seule, classement montré à la fin du donjon
        # (bandeau des clients, trackeur du lanceur). Essai solo retiré le 2026-11-17 (validé en jeu)
        self.medailles = med_mod.Medailles(on=medailles)
        # joueur robot (serveur/robot.py) : autorité d'UNE zone partagée tenue par une instance sans joueur ; « off » :
        # rien ne change. Jamais avec le mode Hanteurs (le Link caché du robot serait visé comme celui d'un Hanteur).
        if robot_mod.actif(robot) and mode_hanteurs:
            log("robot : désactivé avec le mode Hanteurs (ses monstres viseraient son Link caché)")
            robot = "off"
        # --robots N (docs/etudes/serveur_jeu_v2.md, J2) : N robots, un par zone, même clé ; « self.robot » : le premier
        # (rang 0 ; avec N = 1, tout est comme avant). L'essai solo n'en a jamais qu'un.
        cle = robot_cle or (robot_mod.nouvelle_cle() if robot_mod.actif(robot) else None)
        n_robots = 1 if robot == "essai" else robot_mod.nombre_ok(robots)
        self.robots = [robot_mod.Robot(robot, cle=cle, commande=robot_commande, repos_arret=robot_repos,
                                       log=lambda m: log(m), horloge=lambda: time.monotonic(), rang=i)
                       for i in range(n_robots)]
        self.robot = self.robots[0]
        for r in self.robots:
            if r.gest is not None and robot_journal:
                base, ext = os.path.splitext(robot_journal)
                r.gest.journal = robot_journal if not r.rang else f"{base}_{r.rang + 1}{ext}"
        if self.robot.essai:
            self.robot.regler_latence(robot_latence)   # essai solo : un gérant éloigné (0 : aucune latence ajoutée)
        self.robot_bascules: dict[str, list[float]] = {}   # zone -> heures des changements d'autorité entre joueurs
        # lot S2 : validation côté serveur (vitesse des Link, distance des coups, débits), journal d'anomalies lu par
        # l'administrateur ; « journal » par défaut (rien n'est refusé), « refus » à n'allumer qu'après un essai
        self.valid = valid_mod.Validation(validation, log=lambda m: log(m))
        # lot S1 : PC d'autorité préféré (serveur/autorite.py) ; zone -> heure de la dernière passation entre deux
        # joueurs, du dernier signe de combat, de la dernière revue ; PV vus dans les instantanés de l'autorité
        self.pref = autorite.Preference(autorite_preferee)
        self.auth_t: dict[str, float] = {}
        self.combat_t: dict[str, float] = {}
        self.pref_vu: dict[str, float] = {}
        self.ennemis_pv: dict[str, dict] = {}
        # époque du processus (welcome) : un client resté lancé voit qu'il parle à un serveur redémarré et renvoie
        # toute sa progression (sinon il ne renvoyait que ses bits nouveaux, étude monde_persistant §2)
        self.epoque = os.urandom(4).hex()
        self.couleurs_vues: dict[str, int] = {}   # « j:jeton » / « n:nom » -> couleur attribuée (joueur qui revient)
        # Internet : mot de passe de partie, adresses bloquées, connexions par IP (commun/acces.py)
        self.acces = acces.Garde(mot_de_passe)
        # serveur dédié Docker (--config) : compte admin et config.json du volume (serveur/admin.py) ; redémarrage propre
        self.admin: adm.Admin | None = None
        self.redemarrage = False
        self.tache_principale: asyncio.Task | None = None
        self.avert_public = False
        self.pause_avert_ecrans: tuple = ()  # pause vivante : joueurs dont les écrans figent encore le monde
        self.maj: maj_serveur.Distributeur | None = None   # mises à jour du mod (--mises-a-jour)
        # synchro v3 : herbe coupée (touffes par zone, oubliées quand la zone se vide) et deathlink (règle de la
        # partie, choisie par l'hôte, imposée à tous)
        self.herbe_enabled = herbe
        self.herbe = HerbeTable()
        self.morts = MortsServeur(deathlink)
        # vie partagée (synchro/vie_partagee.py) : une barre de vie par groupe, règle de la partie comme le deathlink ;
        # jamais avec le mode Hanteurs (ses règles de K.O. et de points restent les siennes)
        if vie_partagee and mode_hanteurs:
            log("vie partagée : désactivée avec le mode Hanteurs (ses règles de vie restent les siennes)")
        self.vies = VieServeur(bool(vie_partagee) and not mode_hanteurs)
        # difficulté (commun/difficulte.py) : dégâts reçus ×1..×4, vie des monstres selon le nombre de Link de la partie
        self.difficulte_on = bool(difficulte_on)
        self.difficulte = dif_mod.normaliser(difficulte) if self.difficulte_on else dif_mod.defaut()
        self.dif_links_vus = 1                  # dernier nombre de Link diffusé
        # synchro v3 : monde vivant (heure, vent, météo de la mer : référence par groupe, registre du vent)
        self.monde_enabled = monde
        # --monde-vivant (docs/etudes/serveur_jeu_v2.md, J3) : l'heure commune passe aussi quand il n'y a personne
        self.monde = MondeServeur(vivant=monde_vivant)
        # cache-cache des Killer Bees à plusieurs (synchro/cachecache.py) : une partie par groupe, celle du lanceur
        self.cache = CacheCacheServeur()
        # synchro v3 : mécanismes rares (horloge des cycles du stage, piliers Hcbh)
        self.rares_enabled = rares
        # canons détruits et switches de mort par salle, rejoués à qui y entre (synchro/canons.py, §3)
        self.salles = SallesDetruites()
        # synchro v4 : position des PNJ qui se déplacent (relais seul, rien n'est gardé)
        self.pnj_enabled = pnj
        self.pnj_t: dict[int, list[float]] = {}
        self.cycle_owners = CycleOwners()
        # mode Hanteurs, phase 0 (None : mode coop normal)
        self.hanteurs: hmod.Partie | None = hmod.Partie(hanteurs_cfg, log=log) if mode_hanteurs else None
        self.h_tick_t = float("-inf")
        self.h_ignores = 0
        self.h_fx_t: dict[str, list[float]] = {}    # effets de fumée : heures des « hfx » par salle (plafond)
        self.h_fx_envoyes = self.h_fx_jetes = 0
        # Hanteurs dont le dernier state relayé aux autres Hanteurs portait « hv » (voir relais_clone)
        self.hv_vus: set[int] = set()
        # synchro v2, lot 3 (suite) : objets portés (un porteur par objet et par zone)
        self.carry_enabled = carry
        self.carry = CarryTable()
        # synchro v2 : compteurs communs (rubis, flèches, bombes) par groupe, et A4 « premier arrivé »
        self.cnt_enabled = compteurs
        self.cnt = CompteurPool()
        # rid d'un rejoindre en cours -> {groupes gelés, ip du PC du joueur 2 (sa copie n'est pas un gain)}
        self.cnt_gel: dict[str, dict] = {}
        self.pickups: dict[tuple[int, int], dict] = {}  # (émetteur du drop, nid) -> premier ramassage
        # synchro v2, lot 3 : objets mobiles (blocs poussés, explosions, bombes-fleurs, plateformes)
        self.mobiles_enabled = mobiles
        self.mob = MobTable()
        # synchro v2 (A2) : coups des monstres sur le fantôme d'un joueur, relayés à la victime
        self.ghost_hits_enabled = ghost_hits
        self.player_hits = 0
        self.saisies = 0
        self.max_players = max_players
        # synchro v2 (B1) : PV de boss partagés, minimum par (groupe, stage, clé)
        self.boss_enabled = boss
        self.boss = BossPvTable()
        self.boss_unique = boss_unique.BossUniqueTable()   # boss unique (Gohma) : rochers et mort communs
        self.boss_miroir = boss_miroir.MiroirTable()       # miroir des attaques : la référence de chaque boss
        self.boss_volee = boss_volee.VoleeTable()          # Ganon spectral : une boule par Link, tous doivent renvoyer
        # synchro v2 : quarts de cœur au sol ramassés, groupe -> {src: enregistrement}
        self.cadeaux_enabled = cadeaux
        self.gifts: dict[str, dict[str, dict]] = {}
        # synchro v2 : dalles de magma figé, zone -> [(heure, émetteur, nom, entrée)]
        self.magmas: dict[str, list[tuple[float, int, str, dict]]] = {}
        # synchro v2 : inventaire partagé, groupe -> état commun (voir handle_inv)
        self.inv_enabled = inventaire
        self.inv: dict[str, dict] = {}
        # synchro v2 : coffres ouverts, groupe -> {(genre, index, numéro): enregistrement}
        self.coffres_enabled = coffres
        self.coffres: dict[str, dict[tuple, dict]] = {}
        # synchro v2 : petites clés partagées (ensembles idempotents par groupe et par donjon)
        self.keys_enabled = keys
        self.keys_file = keys_file
        self.keys = KeyPool.load(keys_file) if (keys and keys_file) else KeyPool()
        # A15 : rejoindre la partie. rid -> {j2, hote, t, group, recu, relaye}
        self.hote_nom = hote
        self.rejoindre: dict[str, dict] = {}
        self.rejoindre_timeout = REJOINDRE_TIMEOUT
        self.local_addrs: set[str] | None = None   # adresses de ce PC (calculées au premier besoin)
        # A3 : zone -> {clé: {holder, type, flame, pos, rot}} bâtons partagés
        self.sticks_enabled = sticks
        self.sticks: dict[str, dict[str, dict]] = {}
        # A4/A6 : zone -> [(heure, émetteur, entrée)] objets apparus ; zone -> {clé: (émetteur, entrée)}
        self.drops_enabled = drops
        self.drops: dict[str, list[tuple[float, int, str, dict]]] = {}
        self.breaks: dict[str, dict[str, tuple[int, dict]]] = {}
        # A7 : (groupe, stage) -> {"dan": {sw: (val, seq)}, "rooms": {salle: {"sw": {...}, "act": set()}}}
        self.tsw_enabled = tsw
        self.temp: dict[tuple[str, str], dict] = {}
        self.tsw_seq = itertools.count(1)
        # A11 : ne pas donner l'autorité d'une zone à un joueur en cinématique
        self.busy_defer = busy_defer
        self.busy_defer_max = BUSY_DEFER_MAX
        self.deferred: dict[str, float] = {}   # zone -> début de la passation différée
        self.ids = itertools.count(1)
        # zone -> (holder id, époque)
        self.authority: dict[str, tuple[int | None, int]] = {}
        # étape 7 : zone -> (heure, dernier snapshot relayé), pour qui arrive en plein combat
        self.last_enemies: dict[str, tuple[float, dict]] = {}
        self.progress: dict[str, dict] = {}   # groupe -> union des bits de progression
        # trackeur du randomizer (rando/trackeur.py) : corrections manuelles, « vus », complément des clients. Le
        # reste de l'état commun est RECALCULÉ à la demande depuis inv / coffres / clés (jamais l'inverse).
        self.trackeur_enabled = trackeur
        self.trackeur_deduire = trackeur_deduire
        self.trackeur = tk.neuf()
        self.trackeur_doc = None                  # section « trackeur » relue du monde (validée par trk_restaurer)
        self.trk_rev = 0
        self._trk_cache: dict | None = None       # dernier trackeur_etat calculé
        self._trk_heure = 0.0
        self._trk_panne = ""
        # randomizer, lot 5 (relecture adverse) : après « Rejoindre la partie de l'hôte », par groupe et par donjon,
        # la cible d'avant l'oubli de la réserve et les clés de coffres étrangers qu'elle comptait déjà (voir
        # ``rando_hors_retenir`` / ``rando_hors_cles``)
        self.rando_hors_report: dict[str, dict[int, dict]] = {}
        # randomizer, étape 5 (mutisme persistant, docs/etudes/rando_fichiers.md élément 30) : par joueur (pseudo),
        # coffres étrangers vus ouverts chez lui pendant que le monde lui était fermé (Hanteur, possédé, quarantaine)
        # : {"emp": empreinte de la table, "c": {(index, numéro)}}. Le serveur les garde (et le monde persistant les
        # écrit) : un client relancé les reçoit avec l'état des coffres étrangers et ne les signale jamais ; le
        # serveur refuse de toute façon le rapport « coffre » de CE joueur pour ces coffres (aucune clé en trop).
        self.rando_muets: dict[str, dict] = {}
        # objets progressifs de TWWHD (rando/plus.py) : par groupe, par session de jeu (un démarrage du jeu d'un
        # joueur), le plus grand compte reçu de dons progressifs, par famille : {groupe: {session: {famille: n}}}.
        # Total d'un groupe = somme des sessions ; gardé par le monde persistant (section « rando_plus »)
        self.rando_plus: dict[str, dict[int, dict[str, int]]] = {}
        self.rando_plus_emp = ""
        # monde persistant (--monde) : tout l'état partagé relu du disque, écrit périodiquement et à l'arrêt
        self.mp = monde_persistant
        if self.mp is not None:
            if self.hanteurs is not None:
                # fusion (possession d'un joueur + monde persistant) : les dépenses du clone ne touchent jamais les
                # compteurs d'un monde gardé (synchro/potion.py ``depense_interdite``) ; le réglage reste gardé
                self.hanteurs.depense_interdite = True
            # mode Hanteurs sur un monde persistant : une partie Hanteurs est une SESSION au-dessus du monde. Seuls
            # ses RÉGLAGES sont gardés (regles.hanteurs) ; la manche (rôles, points, possessions, potion, ordres)
            # n'est jamais écrite et repart de zéro avec le processus. Ligne de commande (--hanteurs-config, écrit
            # par le lanceur) > réglages gardés dans le monde > défauts
            if self.hanteurs is not None and hanteurs_cfg is None and self.mp.hanteurs_regles is not None:
                self.hanteurs.cfg = hmod.valider_config(self.mp.hanteurs_regles)
                self.hanteurs.potion_config()
                log("Hanteurs : réglages repris du monde (aucun --hanteurs-config)")
            if not self.mp.neuf:
                self.keys = KeyPool()             # monde existant : ses clés font foi, pas un ancien cles.json
            self.mp.restaurer(self)
            self.rando_muets = mpm.muets_relus(getattr(self.mp, "rando_muets_doc", None))
            self.rando_plus_emp, self.rando_plus = mpm.plus_relus(getattr(self.mp, "rando_plus_doc", None))
            self.mp.migrer_cles(self, keys_file if keys else None)   # cles.json repris une fois (monde neuf)
            self.keys_file = None                 # les clés sont gardées dans le monde
        # chantier E (randomizer, palier 1 sûr) : table des coffres de la partie (None : pas de randomizer)
        # randomizer complet, étape 2 (--rando-mode complet) : rnd.Table2, coffres avec progression (pack bit 6)
        self.rando: "rnd.Table | rnd.Table2 | None" = None
        self.rando_spoiler: str | None = None     # chemin du spoiler écrit (jamais envoyé aux clients)
        # relecture adverse : monde dont la table du randomizer COMPLET n'a pas pu être relue (texte du refus). Ses
        # coffres déjà ouverts ont donné les objets de la table : y jouer avec les coffres d'origine perdrait ou
        # doublerait des objets de progression. Aucun joueur n'entre tant que la table n'est pas relue.
        self.rando_bloque = ""
        self.rando_coffres_refuses: set = set()   # (groupe, clé, joueur) : rapports refusés déjà signalés
        # étape 4 : indices dévoilés (nombre ; ne redescend jamais pendant que le serveur tourne) et leur ordre
        self.rando_indices_n = 0
        self.rando_indices_ordre: list[int] = []
        # randomizer par fichiers (rando/fichiers.py) : empreinte du plan (None : la table n'exige aucun fichier
        # généré) et empreinte des fichiers retenue au premier joueur (gardée dans le monde persistant)
        self.rando_plan: str | None = None
        self.rando_fichiers: str | None = None
        self.rando_init(rando_graine, rando_mode, rando_options)
        self.rando_fichiers_init()
        self.trk_restaurer()

    def rando_init(self, graine: str | None, mode: str = "sur", options: dict | None = None) -> None:
        """Table de la partie. Monde persistant : sa table est relue TELLE QUELLE (jamais recalculée, même si les
        outils ont changé) ; la graine, le mode et les options de la ligne de commande ne comptent qu'à sa première
        partie rando. Une table relue invalide n'est pas appliquée (coffres d'origine) mais reste dans le monde
        (rien n'est effacé). ``mode`` : « sur » (palier 1, objets sans progression) ou « complet » (moteur de
        logique ; ``options`` : réglages de rnd.options_completes)."""
        graine = rnd.nettoyer_graine(graine or "")
        mp = self.mp
        doc = getattr(mp, "rando_doc", None) if mp is not None else None
        if doc is not None and getattr(mp, "rando_off", False):
            log("randomizer : coupé (--no-rando) ; la table du monde reste dans son fichier, elle n'est pas envoyée")
            return
        if doc is not None:
            try:
                self.rando = rnd.table_depuis_message(doc)
            except rnd.ErreurRando as e:
                if rnd.message_complet(doc):
                    self.rando_bloque = str(e)[:200]
                    log(f"RANDOMIZER COMPLET : la table du monde n'a pas pu être relue ({e}). Ce monde a déjà donné des "
                        "objets de progression par ses coffres mélangés : y jouer avec les coffres d'origine en perdrait "
                        "ou en doublerait. AUCUN JOUEUR N'ENTRE tant que les outils de l'hôte ne savent pas la relire "
                        "(mets-les à jour) ; le monde garde sa table telle quelle. Pour jouer quand même avec les "
                        "coffres d'origine, à tes risques : --no-rando")
                    return
                log(f"randomizer : table du monde NON appliquée ({e}) : coffres d'origine pour tous ; le monde la garde "
                    "telle quelle (mets les outils à jour)")
                return
            if graine and graine != self.rando.graine:
                log(f"randomizer : le monde a déjà sa graine (« {self.rando.graine} ») : la graine demandée "
                    f"(« {graine} ») est ignorée")
            if graine and mode in rnd.MODES and mode != self.rando.mode:
                log(f"randomizer : le monde a déjà sa table en mode « {self.rando.mode} » : le mode demandé "
                    f"(« {mode} ») est ignoré")
            return
        if not graine:
            return
        try:
            options = dict(options or {})
            if options.get("reglages"):
                # chaîne de réglages partageable : elle porte le mode ET les options, et remplace tout le reste
                from rando import moteur as rando_moteur
                mode = rando_moteur.reglages_depuis_chaine(options["reglages"])[0]
                log(f"randomizer : réglages pris de la chaîne partagée (mode « {mode} »)")
            if mode == "complet":
                self.rando = rnd.calculer_complet(graine, rnd.options_completes(**options))
            elif mode == "sur":
                self.rando = rnd.calculer(graine)
            else:
                raise rnd.ErreurRando(f"mode inconnu : {mode!r} (sur, complet)")
        except (rnd.ErreurRando, TypeError) as e:
            log(f"randomizer : désactivé ({e})")
            return
        if mp is not None:
            mp.rando_doc = self.rando.document()
            mp.marquer(rapide=True)
            if not mp.neuf:
                log("randomizer : ATTENTION, ajouté à un monde déjà commencé : seuls les coffres pas encore ouverts "
                    "changent (une nouvelle partie est conseillée)")

    def rando_fichiers_init(self) -> None:
        """Randomizer par fichiers : le serveur calcule seul l'empreinte du plan (sans dump) si la table a une option
        « fichiers » ; l'empreinte des fichiers d'un monde persistant n'est reprise que pour CE plan (des outils
        mis à jour donnent un autre plan : le premier joueur la fixe de nouveau)."""
        self.rando_plan = self.rando_fichiers = None
        t = self.rando
        if t is None or t.mode != "complet" or self.rando_bloque:
            return
        try:
            msg = t.message()
            if not rfi.fichiers_requis(msg):
                return
            self.rando_plan = rfi.empreinte_plan(msg)
        except Exception as e:                  # données d'empreintes absentes : personne n'entrerait, on le dit
            log(f"randomizer par fichiers : empreinte du plan impossible ({e}) : les joueurs seront refusés")
            self.rando_plan = "?"
            return
        mp = self.mp
        garde = getattr(mp, "rando_fichiers", None) if mp is not None else None
        if isinstance(garde, dict) and garde.get("plan") == self.rando_plan and rfi.est_empreinte(garde.get("fichiers")):
            self.rando_fichiers = garde["fichiers"]
        log(f"randomizer par fichiers : plan {self.rando_plan[:16]} (pack WWMP_Rando généré exigé chez chaque joueur"
            + (f", fichiers {self.rando_fichiers[:16]} repris du monde)" if self.rando_fichiers else ")"))

    def rando_fichiers_hello(self, p: Player, r: dict) -> tuple[str, str] | None:
        """(texte FR, texte EN) du refus d'un joueur dont les fichiers générés ne vont pas, sinon None (et retient
        l'empreinte des fichiers du premier joueur)."""
        requis = self.rando_plan is not None
        code, retenir = rfi.controle_hello(r, requis, self.rando_plan, self.rando_fichiers)
        if code is not None:
            return rfi.REFUS[code]
        if retenir:
            self.rando_fichiers = retenir
            log(f"randomizer par fichiers : empreinte des fichiers {retenir[:16]} retenue (premier joueur : {p.name}) ; "
                "elle est exigée des suivants")
            if self.mp is not None:
                self.mp.rando_fichiers = {"plan": self.rando_plan, "fichiers": retenir}
                self.mp.marquer(rapide=True)
        return None

    def rando_plan_message(self) -> dict:
        """Réponse « rando_plan » (lanceur, avant le jeu) : la table telle que les clients la reçoivent (ni graine, ni
        spoiler), le plan et l'empreinte des fichiers déjà retenue. Sans fichiers exigés : « aucun »."""
        t = self.rando
        if self.rando_plan is None or t is None:
            return {"type": "rando_plan", "aucun": 1}
        return {"type": "rando_plan", "plan": self.rando_plan, "fichiers": self.rando_fichiers,
                "table": t.message()}

    def rando_spoiler_ecrire(self, dossier: str | None) -> None:
        if self.rando is None:
            return
        if not dossier:
            log("randomizer : pas de dossier pour le spoiler (--rando-spoiler DOSSIER) : spoiler non écrit")
            return
        try:
            self.rando_spoiler = rnd.ecrire_spoiler(self.rando, dossier)
            log(f"randomizer : spoiler écrit dans {self.rando_spoiler} (jamais envoyé aux joueurs)")
        except (OSError, rnd.ErreurRando) as e:
            log(f"randomizer : spoiler non écrit ({e})")

    def rando_hello(self, p: Player, msg: dict) -> str | None:
        """Hello d'un client de jeu : refus (texte) s'il exige une autre empreinte (--rando-empreinte) ; sinon None
        (la table part juste après le welcome)."""
        r = msg.get("rando")
        r = r if isinstance(r, dict) else {}
        voulu = r.get("voulu")
        emp = r.get("emp")
        p.rando_emp = emp[:16].lower() if isinstance(emp, str) else ""
        p.rando_cap = r.get("cap") in (1, True)
        p.rando_cap2 = r.get("cap2") in (1, True)
        p.rando_cap3 = r.get("cap3") in (1, True)
        p.rando_don = r.get("don") in (1, True)
        p.rando_cap4 = r.get("cap4") in (1, True)
        p.rando_cap5 = r.get("cap5") in (1, True)
        p.rando_cap7 = r.get("cap7") in (1, True)        # objets progressifs de TWWHD : mod natif 0.40.0
        p.rando_cap6 = r.get("cap6") in (1, True)
        p.rando_don9 = r.get("don9") in (1, True)        # étape 5 : Kane, autres lettres, enchère à 100 rubis
        if self.rando_bloque:
            return ("randomizer complet : le serveur de l'hôte n'a pas pu relire la table de ce monde "
                    f"({self.rando_bloque}) ; personne n'entre tant que ses outils ne sont pas à jour (sinon des objets "
                    "de progression seraient perdus)")
        if self.rando is not None and self.rando.mode == "complet":
            # randomizer complet : les coffres donnent des objets de PROGRESSION. Un joueur dont le jeu garderait ses
            # coffres d'origine y prendrait l'objet d'origine, et les autres perdraient celui de la table : il
            # n'entre pas tant que ses outils et son pack ne savent pas l'appliquer (le client réessaie tout seul).
            if "rando" not in p.feat or not isinstance(msg.get("rando"), dict):
                return ("randomizer complet : cette partie mélange les objets de progression des coffres ; tes outils "
                        "ne savent pas le faire ou le randomizer est coupé sur ce PC (--no-rando) : mets-les à jour "
                        "et lance la partie depuis le lanceur")
            if not p.rando_cap2:
                return ("randomizer complet : ton graphic pack ne sait pas (ou pas encore) changer les objets de "
                        "progression des coffres : installe le pack v19 du dépôt (python tools/install_graphicpack.py), "
                        "relance Cemu ; si c'est déjà fait, le client réessaie tout seul dès que le jeu est lancé")
            if getattr(self.rando, "n2_requis", False) and not p.rando_cap3:
                # étape 3 : la table change des cadeaux de personnages (stub N2 du pack, bit 7). Sans lui, ce joueur
                # recevrait les cadeaux d'origine pendant que ses coffres donnent ceux de la table : refusé de même.
                return ("randomizer complet : cette partie mélange aussi les cadeaux de personnages et ton graphic pack "
                        "(ou tes outils) ne sait pas les changer : mets les outils à jour, installe le pack v19 du "
                        "dépôt (python tools/install_graphicpack.py) et relance Cemu ; le client réessaie tout seul")
            if getattr(self.rando, "don_requis", False) and not p.rando_don:
                # portage TWWHD, lot 1 : la table change des lettres, des étals de boutique, des enchères ou des
                # cadeaux lus dans une table du jeu (octets écrits par le client). Un client qui ne sait pas les écrire
                # (outils plus anciens, ou tables du jeu non reconnues) y prendrait l'objet d'origine : refusé de même.
                return ("randomizer complet : cette partie mélange aussi le courrier, la boutique de l'Île du Pic, "
                        "les enchères ou des cadeaux de Mercantîle, et tes outils ne savent pas les changer dans ton "
                        "jeu (outils plus anciens que ceux de l'hôte, ou jeu qui n'est pas la version USA v0) : mets "
                        "les outils à jour et relance Cemu ; le client réessaie tout seul")
            if getattr(self.rando, "don9_requis", False) and not p.rando_don9:
                # étape 5 (rando/lot9.py) : Kane, autres lettres, enchère à 100 rubis. Un client d'avant ne
                # connaît pas ces lieux : il refuserait la table et jouerait les objets d'origine.
                return ("randomizer complet : cette partie mélange aussi Kane, les autres lettres ou l'enchère à 100 "
                        "rubis, et tes outils sont plus anciens que ceux de l'hôte : mets-les à jour et relance le "
                        "client. / Full randomizer: this game also shuffles Kane, the other letters or the 100 rupee "
                        "auction, and your tools are older than the host's: update them and restart the client.")
            if getattr(self.rando, "conflit_requis", False) and not p.rando_cap4:
                # portage TWWHD, lot 2 : la table change des coffres « en conflit » (zone wwmp_rnd4 du pack). Sans elle,
                # ce joueur y prendrait l'objet d'origine et son jeu refuserait leurs objets placés ailleurs : refusé.
                return ("randomizer complet : cette partie mélange aussi les coffres « en conflit » (clé partagée "
                        "entre deux niveaux) et ton graphic pack (ou tes outils) ne sait pas les changer : mets les "
                        "outils à jour, installe le pack v19 du dépôt (python tools/install_graphicpack.py) et "
                        "relance Cemu ; le client réessaie tout seul")
            if getattr(self.rando, "prog_requis", False):
                # portage TWWHD, lot 4 : objets progressifs. Le compte commun vit dans le registre des coffres partagés
                # et l'inventaire partagé corrige chaque jeu : sans eux (serveur ou PC), un palier serait perdu.
                if not (self.coffres_enabled and self.inv_enabled):
                    return ("randomizer complet : cette partie a des objets progressifs et le serveur de l'hôte a été "
                            "lancé sans le partage des coffres ou de l'inventaire (--no-chests, --no-inventory) : "
                            "l'hôte doit relancer le serveur sans ces options (personne n'entre en attendant)")
                if not p.rando_cap5:
                    return ("randomizer complet : cette partie a des objets progressifs (arc puis flèches de lumière, "
                            "compte commun au groupe) et ton graphic pack (ou tes outils) ne sait pas les donner, ou "
                            "ce PC ne partage pas les coffres et l'inventaire (--no-chests, --no-inventory) : mets "
                            "les outils à jour, installe le pack v19 du dépôt (python tools/install_graphicpack.py) "
                            "et relance Cemu ; le client réessaie tout seul")
            if getattr(self.rando, "plus_requis", False):
                # objets progressifs de TWWHD (rando/plus.py) : le jeu donne le palier suivant celui qu'il a déjà
                # (mod natif 0.40.0) ; le compte du groupe met l'inventaire commun au niveau du total : sans
                # l'inventaire partagé, deux dons au même instant perdraient un palier
                if not self.inv_enabled:
                    return ("randomizer complet : cette partie a des objets progressifs de TWWHD (épée, magie, bourse…) "
                            "et le serveur de l'hôte a été lancé sans le partage de l'inventaire (--no-inventory) : "
                            "l'hôte doit relancer le serveur sans cette option (personne n'entre en attendant)")
                if not p.rando_cap7:
                    return ("randomizer complet : cette partie a des objets progressifs de TWWHD (épée, magie, "
                            "bourse…) : il faut le portage natif avec le mod WWMP 0.40.0 ou plus (Cemu ne sait pas "
                            "les donner), et ce PC doit partager les coffres et l'inventaire (--no-chests, "
                            "--no-inventory) : mets les outils à jour et relance la partie depuis le lanceur ; le "
                            "client réessaie tout seul. / Full randomizer: this game has TWWHD progressive items: it "
                            "needs the native port with WWMP mod 0.40.0 or newer.")
            if getattr(self.rando, "hors_requis", False):
                # portage TWWHD, lot 5 : objets de donjon hors de leur donjon. La petite clé d'un coffre étranger est
                # créditée par le compteur commun des clés, quand le registre des coffres partagés voit le coffre
                # ouvert : sans eux (serveur ou PC), la clé serait perdue et une porte resterait fermée.
                if not (self.coffres_enabled and self.keys_enabled):
                    return ("randomizer complet : cette partie place des clés ou des cartes de donjon hors de leur "
                            "donjon et le serveur de l'hôte a été lancé sans le partage des coffres ou des petites "
                            "clés (--no-chests, --no-keys) : l'hôte doit relancer le serveur sans ces options "
                            "(personne n'entre en attendant)")
                if not p.rando_cap6:
                    return ("randomizer complet : cette partie place des clés ou des cartes de donjon hors de leur "
                            "donjon (données par le serveur au bon donjon, pour tout le groupe) et tes outils ne "
                            "savent pas le faire, ou ce PC ne partage pas les coffres et les petites clés "
                            "(--no-chests, --no-keys) : mets les outils à jour et relance la partie depuis le "
                            "lanceur ; le client réessaie tout seul")
        p.rando_refus_en = ""
        refus = self.rando_fichiers_hello(p, r)
        if refus is not None:
            p.rando_refus_en = refus[1]
            return refus[0]
        if isinstance(voulu, str) and voulu.strip():
            voulu = voulu.strip().lower()[:16]
            emp = self.rando.empreinte if self.rando is not None else ""
            if not emp or not emp.startswith(voulu):
                return (f"randomizer : ce serveur a {'la table ' + emp if emp else 'une partie SANS randomizer'}, "
                        f"ton client attend la table {voulu} (autre partie ?)")
        return None

    def rando_coffre_d_origine(self, rec: dict) -> tuple[int, int] | None:
        """Randomizer complet (relecture adverse) : ce rapport de coffre porte-t-il l'objet D'ORIGINE d'un coffre que
        la table change ? Le stub du pack change tout coffre (index, numéro, objet d'origine) à sa création : un tel
        rapport vient donc d'un coffre créé AVANT le chargement de la table chez son auteur (client lancé dans une
        salle déjà chargée). Renvoie (objet d'origine, objet de la table) ou None."""
        t = self.rando
        if t is None or t.mode != "complet" or rec.get("k") != "t" or rec.get("item") is None:
            return None
        for k, s in enumerate(rnd.SITES_CONFLIT):
            # lot 2 : coffre « en conflit » (clé partagée avec un coffre d'un autre niveau) ouvert dans SON niveau
            if (s[1], s[2]) == (rec["i"], rec["n"]) and rec.get("stage") == s[0]:
                e = getattr(t, "entrees_conflit", {}).get(k)
                if e is not None:
                    return e if e[0] != e[1] and rec["item"] == e[0] else None
        e = t.entrees.get((rec["i"], rec["n"]))
        if e is None or e[0] == e[1] or rec["item"] != e[0]:
            return None
        lieu = t.prog_lieu(rec["i"], rec["n"]) if hasattr(t, "prog_lieu") else None
        if lieu is not None and e[0] in lieu[1]:
            # lot 4 : lieu progressif dont l'objet d'origine est lui-même un palier de la famille (coffre de l'arc ou
            # des flèches de lumière). Le pack y pose le palier du compte commun, qui peut être l'objet d'origine :
            # ce rapport est normal, il compte comme les autres
            return None
        return e

    # --- randomizer, portage TWWHD, lot 5 : objets de donjon hors de leur donjon -----------------------------
    def rando_hors_active(self):
        """La table de la partie si elle place un objet de donjon hors du sien, sinon None."""
        t = self.rando
        if t is None or t.mode != "complet" or not getattr(t, "hors_requis", False) or not self.coffres_enabled:
            return None
        return t

    def rando_hors_ouverts(self, group: str) -> list:
        """Coffres étrangers de la table que ce groupe a ouverts : [(index, numéro)], lus dans le registre des
        coffres partagés (premier arrivé par coffre, gardé par le monde persistant)."""
        t = self.rando_hors_active()
        if t is None:
            return []
        reg = self.coffres.get(group, {})
        return sorted(k for k in t.hors if ("t", k[0], k[1]) in reg)

    def rando_hors_message(self, groupes, tout: bool = False) -> dict:
        """« rando_hors » : coffres étrangers ouverts, par groupe. Chaque client pose dans sa sauvegarde le bit du
        bon donjon pour ceux qui valent une grande clé, une carte ou une boussole ; les petites clés passent par le
        compteur commun (``rando_hors_cles``). La liste sert aussi au client à signaler un coffre étranger ouvert
        chez lui que le registre ne connaît pas (ouvert hors connexion : ``RandoClient.hors_a_signaler``)."""
        t = self.rando_hors_active()
        msg = {"type": "rando_hors", "emp": t.empreinte,
               "groupes": {g: [list(k) for k in self.rando_hors_ouverts(g)][:rnd.HORS_MAX]
                           for g in list(groupes)[:64]}}
        if tout:
            msg["tout"] = 1
        return msg

    def rando_hors_envoyer(self, p: Player) -> None:
        """État complet à un joueur (juste après la table), avec ses coffres muets (étape 5)."""
        if self.rando_hors_active() is None or "rando" not in p.feat:
            return
        msg = self.rando_hors_message(list(self.coffres), tout=True)
        muets = self.rando_muets_de(p.name)
        if muets:
            msg["muets"] = [list(k) for k in sorted(muets)][:MUETS_MAX]
        p.send(msg)

    # --- randomizer, étape 5 : mutisme des coffres étrangers gardé par le serveur ---------------------------
    def rando_muets_de(self, nom: str) -> set:
        """Coffres muets de ce joueur pour la table de la partie (vide si la table a changé)."""
        t = self.rando_hors_active()
        d = self.rando_muets.get(str(nom)[:24])
        if t is None or not isinstance(d, dict) or d.get("emp") != t.empreinte:
            return set()
        return d["c"]

    def rando_muet(self, nom: str, idx, tbox) -> bool:
        return (idx, tbox) in self.rando_muets_de(nom)

    def handle_rando_muet(self, p: Player, msg: dict) -> None:
        """« rando_muet » d'un client de jeu : coffres étrangers vus ouverts chez lui pendant que le monde lui était
        fermé. Gardés par pseudo et pour CETTE table (empreinte), bornés ; seuls les coffres étrangers de la table
        sont retenus, et jamais un coffre que le registre du groupe connaît déjà (il a déjà compté, légitimement)."""
        t = self.rando_hors_active()
        if t is None or not p.hello or not p.joueur or msg.get("emp") != t.empreinte:
            return
        lst = msg.get("c")
        if not isinstance(lst, list):
            return
        group = str(msg.get("group") or p.coffres_group or "default")[:32]
        reg = self.coffres.get(group, {})
        nom = p.name[:24]
        d = self.rando_muets.get(nom)
        if not isinstance(d, dict) or d.get("emp") != t.empreinte:
            d = self.rando_muets[nom] = {"emp": t.empreinte, "c": set()}
        neufs = 0
        for c in lst[:64]:
            if not (isinstance(c, list) and len(c) == 2 and all(self._int(x) for x in c)):
                continue
            k = (c[0], c[1])
            if k in t.hors and ("t", k[0], k[1]) not in reg and k not in d["c"] and len(d["c"]) < MUETS_MAX:
                d["c"].add(k)
                neufs += 1
        if neufs:
            while len(self.rando_muets) > MUETS_JOUEURS_MAX:
                self.rando_muets.pop(next(iter(self.rando_muets)))
            log(f"randomizer : {neufs} coffre(s) étranger(s) de {p.name} gardé(s) muet(s) (ouverts pendant que le "
                "monde lui était fermé : ils ne donneront jamais de clé)")
            if self.mp is not None:
                self.mp.rando_muets_doc = mpm.muets_document(self.rando_muets)
                self.mp_marquer()

    def rando_hors_diffuser(self, group: str) -> None:
        """Les coffres étrangers ouverts d'un groupe ont pu changer (coffre ouvert, registre remis à zéro)."""
        if self.rando_hors_active() is None:
            return
        msg = self.rando_hors_message([group])
        for o in self.players.values():
            if o.joueur and "rando" in o.feat:
                o.send(msg)

    def rando_hors_retenir(self, group: str) -> None:
        """À appeler JUSTE AVANT d'oublier la réserve de clés et le registre des coffres d'un groupe (« Rejoindre la
        partie de l'hôte »). Les jeux vont réannoncer leurs compteurs, qui contiennent déjà les petites clés des
        coffres étrangers ouverts, puis signaler de nouveau ces coffres (ils sont ouverts dans la sauvegarde
        copiée) : sans mémoire, chaque coffre étranger déjà ouvert redonnait une clé à chaque « rejoindre ». On
        retient par donjon la cible d'avant et les identifiants déjà comptés ; ``rando_hors_cles`` s'en sert pour ne
        jamais dépasser cette cible avec ces coffres-là (et rendre quand même la clé qu'aucun jeu n'avait encore
        reçue). Mémoire vive seulement : un serveur relancé entre-temps retombe sur une clé en trop, jamais en
        moins."""
        self.rando_hors_report.pop(group, None)
        t = self.rando_hors_active()
        if t is None or not self.keys_enabled:
            return
        memo = {}
        for (idx, tbox), slot in sorted(t.hors_cles().items()):
            e = self.keys.groups.get(group, {}).get(slot)
            kid = rnd.id_cle_hors(idx, tbox)
            if e is None or not (kid in e["gets"] or kid in e.get("inclus", ())):
                continue
            memo.setdefault(slot, {"cible": KeyPool.target_of(e), "ids": set(), "rendus": 0})["ids"].add(kid)
        if memo:
            self.rando_hors_report[group] = memo

    def rando_hors_cles(self, group: str) -> int:
        """Crédite au compteur commun des petites clés (synchro/cles.py) chaque petite clé d'un coffre étranger que ce
        groupe a ouvert : fait « get » au donjon de LA CLÉ, identifiant « r<index>:<numéro> ». Idempotent : le même
        coffre signalé deux fois, par un ou par plusieurs joueurs, ne donne qu'une clé (le registre garde le premier
        arrivé, et le compteur refuse un identifiant déjà compté) ; deux coffres ouverts au même instant donnent
        deux clés. Un donjon dont aucun joueur n'a encore annoncé le compteur attend (comme tout fait de clé : compté
        sur base 0, il fausserait la base annoncée ensuite) : repris au prochain hello des clés. Renvoie le nombre
        de clés créditées."""
        t = self.rando_hors_active()
        if t is None or not self.keys_enabled:
            return 0
        reg = self.coffres.get(group, {})
        n = 0
        for (idx, tbox), slot in sorted(t.hors_cles().items()):
            rec = reg.get(("t", idx, tbox))
            if rec is None or not valid_key_slot(slot) or self.keys.target(group, slot) is None:
                continue
            kid = rnd.id_cle_hors(idx, tbox)
            par = str((rec.get("by") if isinstance(rec, dict) else None) or "?")[:24]
            memo = self.rando_hors_report.get(group, {}).get(slot)
            if memo is not None and kid in memo["ids"]:
                # coffre déjà compté avant un « rejoindre » : sa clé est dans les compteurs réannoncés, sauf si
                # aucun jeu ne l'avait encore reçue (base annoncée sous la cible d'avant : on la rend)
                memo["ids"].discard(kid)
                ent = self.keys.groups[group][slot]
                if memo["cible"] - ent["base"] - memo["rendus"] <= 0:
                    if self.keys.inclure(group, slot, kid, par):
                        log(f"clés [{group}] donjon {slot} : RANDOMIZER, coffre {tbox} (index {idx}) signalé de "
                            f"nouveau après « rejoindre » : sa petite clé est déjà dans le compteur ("
                            f"{KeyPool.target_of(ent)}), pas comptée deux fois")
                        self.keys_save()
                    continue
                memo["rendus"] += 1
            try:
                new, e = self.keys.fact(group, "get", slot, kid, par)
            except ValueError as exc:
                log(f"clés [{group}] : clé du randomizer {kid} refusée ({exc})")
                continue
            if not new:
                continue
            n += 1
            target, rev = KeyPool.target_of(e), e["rev"]
            log(f"clés [{group}] donjon {slot} : RANDOMIZER, {par} a ouvert le coffre {tbox} (index {idx}) qui "
                f"contient une petite clé de ce donjon ({rnd.DONJONS_HORS.get(slot, '?')}) -> {target}")
            out = {"type": "keys", "from": None, "group": group, "slot": slot, "target": target, "rev": rev,
                   "why": "get", "id": kid, "by": par}
            for o in self.players.values():
                if o.keys_group == group:
                    o.send(out)
        if n:
            self.keys_save()
        return n

    # --- randomizer : objets progressifs de TWWHD (rando/plus.py ; dons comptés par le mod natif 0.40.0) --------
    def rando_plus_active(self):
        """La table de la partie si elle a une famille de progressifs_plus, sinon None."""
        t = self.rando
        return t if t is not None and t.mode == "complet" and getattr(t, "plus_requis", False) else None

    def rando_plus_totaux(self, group: str) -> dict:
        """{famille: dons progressifs reçus par tous les jeux du groupe} (somme des sessions ; chaque session garde son
        plus grand compte : un message en double ou en retard ne compte jamais deux fois)."""
        t = self.rando_plus_active()
        if t is None or self.rando_plus_emp != t.empreinte:
            return {}
        out = {}
        for c in self.rando_plus.get(group, {}).values():
            for f, n in c.items():
                out[f] = out.get(f, 0) + n
        return out

    def handle_rando_plus(self, p: Player, msg: dict) -> None:
        """« rando_plus » d'un client de jeu : {group, emp, session, recus: {famille: n}}. Gardé si la table l'annonce,
        champs bornés ; puis l'inventaire commun du groupe est mis au niveau (``rando_plus_inventaire``)."""
        from rando import plus as rando_plus
        t = self.rando_plus_active()
        if t is None or not p.hello or not p.joueur or not self.inv_enabled or msg.get("emp") != t.empreinte:
            return
        ses, recus = msg.get("session"), msg.get("recus")
        if not (self._int(ses) and 0 < ses < 1 << 32 and isinstance(recus, dict)):
            return
        familles = t.plus_familles()
        group = str(msg.get("group") or p.inv_group or "default")[:32]
        if self.rando_plus_emp != t.empreinte:
            self.rando_plus_emp, self.rando_plus = t.empreinte, {}
        sessions = self.rando_plus.setdefault(group, {})
        c = sessions.get(ses)
        if c is None:
            if len(sessions) >= rando_plus.SESSIONS_MAX:
                log(f"randomizer : {p.name} : trop de sessions de jeu pour les objets progressifs : ignoré")
                return
            c = sessions[ses] = {}
        change = False
        for f, n in list(recus.items())[:8]:
            if f in familles and self._int(n) and 0 <= n <= rando_plus.COMPTE_MAX and n > c.get(f, 0):
                c[f] = n
                change = True
        if not change:
            return
        log(f"randomizer : {p.name} : dons progressifs de TWWHD de sa session {ses} : "
            + ", ".join(f"{f} {n}" for f, n in sorted(c.items())))
        if self.mp is not None:
            self.mp.rando_plus_doc = mpm.plus_document(self.rando_plus_emp, self.rando_plus)
            self.mp_marquer()
        self.rando_plus_inventaire(group)

    def rando_plus_inventaire(self, group: str) -> None:
        """Inventaire commun mis au niveau : pour chaque famille, rang = paliers de la création + dons progressifs du
        groupe (ou le rang que l'inventaire a déjà, s'il est plus haut), borné au dernier palier. Épée et boîte à
        images : paliers acquis ; bourse, carquois, sac de bombes, magie : plafond. Deux joueurs qui reçoivent le
        même palier au même instant ont chacun le palier suivant le leur : le total en compte deux, et le second
        arrive ici à tous. Jamais de retrait."""
        from rando import plus as rando_plus
        t = self.rando_plus_active()
        if t is None or not self.inv_enabled:
            return
        totaux = self.rando_plus_totaux(group)
        if not any(totaux.values()):
            return
        st = self.inv.setdefault(group, self.inv_vide())
        ajout, caps = [], {}
        for nom in t.plus_familles():
            rang = rando_plus.rang_commun(nom, totaux.get(nom, 0), rando_plus.BASE.get(nom, 0), st["acq"], st["cap"])
            acq, cap = rando_plus.a_donner(nom, rang)
            for objet in acq:
                if objet not in st["acq"]:
                    st["acq"].append(objet)
                    ajout.append(objet)
            for k, v in cap.items():
                if st["cap"].get(k, 0) < v:
                    st["cap"][k] = v
                    caps[k] = v
        if not (ajout or caps):
            return
        self.mp_marquer(gain=True)
        log(f"inventaire [{group}] : objets progressifs de TWWHD, dons du groupe "
            + ", ".join(f"{f} {n}" for f, n in sorted(totaux.items()))
            + (" : " + ", ".join(f"0x{o:02X}" for o in ajout) + " acquis pour tous" if ajout else "")
            + (" ; plafonds " + ", ".join(f"{k} {v}" for k, v in sorted(caps.items())) if caps else ""))
        out = {"type": "inv", "from": None, "group": group}
        if ajout:
            out["acq"] = ajout
        if caps:
            out["cap"] = dict(st["cap"])
        for o in self.players.values():
            if o.joueur and o.inv_group == group:
                o.send(out)

    # --- randomizer, portage TWWHD, lot 4 : objets progressifs (compte commun au groupe) ---------------------
    def rando_prog_active(self):
        """La table de la partie si elle a une famille progressive active, sinon None."""
        t = self.rando
        return t if t is not None and t.mode == "complet" and getattr(t, "prog_requis", False) else None

    def rando_prog_comptes(self, group: str) -> dict:
        """{famille: nombre de ses lieux progressifs ouverts par ce groupe}. Lu dans le registre des coffres partagés
        (premier arrivé par lieu, gardé par le monde persistant) : le même lieu signalé deux fois, par un ou par
        plusieurs joueurs, ne compte qu'une fois ; deux lieux ouverts au même instant comptent pour deux."""
        t = self.rando_prog_active()
        if t is None:
            return {}
        reg = self.coffres.get(group, {})
        return t.prog_comptes({(k[1], k[2]) for k in list(reg) if isinstance(k, tuple) and len(k) == 3
                               and k[0] == "t"})

    def rando_prog_effectifs(self, group: str) -> dict:
        """Compte envoyé aux jeux (c'est lui qui choisit le palier du prochain lieu) : le compte du registre, ou le
        rang du plus haut palier que l'inventaire commun du groupe possède déjà s'il est plus grand. Relecture
        adverse : un lieu ouvert hors connexion n'est jamais signalé (au retour, les coffres déjà ouverts servent de
        base muette), et le registre est oublié par un serveur relancé sans monde persistant ou par « rejoindre » ;
        l'arc, lui, revient par l'inventaire partagé. Sans ceci le lieu suivant redonnait l'arc et les flèches de
        lumière n'arrivaient jamais. Un maximum, pas une somme : rien n'est doublé, et le compte ne dépasse jamais
        ce que le groupe possède ou a ouvert."""
        comptes = self.rando_prog_comptes(group)
        t = self.rando_prog_active()
        st = self.inv.get(group) if (t is not None and self.inv_enabled) else None
        acq = st.get("acq") if isinstance(st, dict) else None
        if not acq:
            return comptes
        for nom, (paliers, _cles) in t.prog_familles().items():
            rang = max((r for r, o in enumerate(paliers, 1) if o in acq), default=0)
            if rang > comptes.get(nom, 0):
                comptes[nom] = rang
        return comptes

    def rando_prog_message(self, groupes, tout: bool = False) -> dict:
        t = self.rando_prog_active()
        msg = {"type": "rando_prog", "emp": t.empreinte,
               "groupes": {g: self.rando_prog_effectifs(g) for g in list(groupes)[:64]}}
        if tout:
            msg["tout"] = 1
        return msg

    def rando_prog_envoyer(self, p: Player) -> None:
        """État complet des comptes à un joueur (juste après la table : son client ne la charge qu'ensuite)."""
        if self.rando_prog_active() is None or "rando" not in p.feat:
            return
        p.send(self.rando_prog_message(list(dict.fromkeys(list(self.coffres) + list(self.inv))), tout=True))

    def rando_prog_diffuser(self, group: str) -> None:
        """Le compte d'un groupe a pu changer (coffre ouvert, registre remis à zéro) : à tous les clients de jeu,
        puis l'inventaire commun du groupe est mis au niveau du compte."""
        if self.rando_prog_active() is None:
            return
        msg = self.rando_prog_message([group])
        for o in self.players.values():
            if o.joueur and "rando" in o.feat:
                o.send(msg)
        self.rando_prog_inventaire(group)

    def rando_prog_inventaire(self, group: str) -> None:
        """Inventaire commun mis au niveau du compte : N lieux ouverts -> les N premiers paliers de la famille sont
        acquis par le groupe (et le carquois de 30 flèches avec l'arc), quel que soit l'objet que chaque coffre a
        vraiment donné. C'est ce qui rattrape un coffre créé avant un changement de compte (deux lieux ouverts au même
        moment donnent deux fois le palier 1 : le compte vaut 2, le palier 2 arrive ici). Jamais de retrait."""
        t = self.rando_prog_active()
        if t is None or not self.inv_enabled:
            return
        comptes = self.rando_prog_comptes(group)
        if not any(comptes.values()):
            return
        st = self.inv.setdefault(group, self.inv_vide())
        ajout, cap_up = [], False
        for nom, (paliers, _cles) in t.prog_familles().items():
            for objet in paliers[:max(0, comptes.get(nom, 0))]:
                if objet not in st["acq"]:
                    st["acq"].append(objet)
                    ajout.append(objet)
            if nom == "arc" and comptes.get(nom, 0) >= 1 and st["cap"].get("am", 0) < 30:
                st["cap"]["am"] = 30
                cap_up = True
        if not (ajout or cap_up):
            return
        self.mp_marquer(gain=bool(ajout))
        log(f"inventaire [{group}] : objets progressifs, compte du groupe "
            + ", ".join(f"{f} {n}" for f, n in sorted(comptes.items()))
            + (" : " + ", ".join(f"0x{o:02X}" for o in ajout) + " acquis pour tous" if ajout else ""))
        out = {"type": "inv", "from": None, "group": group}
        if ajout:
            out["acq"] = ajout
        if cap_up:
            out["cap"] = dict(st["cap"])
        for o in self.players.values():
            if o.joueur and o.inv_group == group:
                o.send(out)

    # --- randomizer, étape 4 : indices dévoilés au fil de la partie, suivi des lieux pour le lanceur ---------
    def rando_coffres_pris(self) -> dict:
        """{(index de sauvegarde, numéro de coffre): objet trouvé ou None} : coffres ouverts par l'équipe, tous groupes
        confondus (registre des coffres partagés, gardé par le monde persistant). Lecture seule."""
        pris = {}
        for reg in self.coffres.values():
            for (k, i, n), rec in reg.items():
                if k == "t" and isinstance(i, int) and isinstance(n, int):
                    item = rec.get("item") if isinstance(rec, dict) else None
                    if pris.get((i, n)) is None:
                        pris[(i, n)] = item if isinstance(item, int) and not isinstance(item, bool) else None
        return pris

    def rando_indices_total(self) -> int:
        return len(getattr(self.rando, "indices", None) or [])

    def rando_indices_voulus(self) -> int:
        """Nombre d'indices que l'avancée de l'équipe dévoile : 1 dès le début, 1 de plus pour le premier coffre
        ouvert dans chaque donjon ou zone (index de sauvegarde), 1 de plus tous les RANDO_INDICE_TOUS_LES coffres.
        Fonction du seul registre des coffres : rien de plus à garder, et un monde relu retrouve ses indices."""
        total = self.rando_indices_total()
        if not total:
            return 0
        pris = self.rando_coffres_pris()
        return min(total, 1 + len({i for i, _n in pris}) + len(pris) // RANDO_INDICE_TOUS_LES)

    def rando_indices_liste(self, n: int | None = None) -> list[dict]:
        """Les ``n`` premiers indices dévoilés (défaut : tous ceux dévoilés), dans l'ordre de dévoilement."""
        t = self.rando
        if not self.rando_indices_total():
            return []
        if len(self.rando_indices_ordre) != len(t.indices):
            self.rando_indices_ordre = t.ordre_indices()
        n = self.rando_indices_n if n is None else n
        return [{"fr": t.indices[k], "en": t.indices_en[k]} for k in self.rando_indices_ordre[:n]]

    def rando_indices_maj(self, annoncer: bool = True) -> None:
        """Dévoile les indices que l'avancée de l'équipe a gagnés (jamais l'inverse : une copie de partie vide le
        registre des coffres, les indices déjà dits restent dits) et les annonce aux joueurs de la partie."""
        voulus = self.rando_indices_voulus()
        if voulus <= self.rando_indices_n:
            return
        avant, self.rando_indices_n = self.rando_indices_n, voulus
        total = self.rando_indices_total()
        neufs = self.rando_indices_liste()[avant:]
        for k, ind in enumerate(neufs, avant + 1):
            log(f"randomizer : indice {k}/{total} dévoilé : {ind['fr']}")
        if not annoncer:
            return
        out = {"type": "rando_indice", "n": voulus, "total": total, "liste": neufs}
        for o in self.players.values():
            if o.joueur and "rando" in o.feat and not self.rando_sans_indices(o):
                o.send(out)

    def rando_sans_indices(self, p: Player) -> bool:
        """Le Hanteur (mode Hanteurs) joue contre l'équipe : il ne reçoit pas les indices en jeu."""
        return self.hanteurs is not None and self.hanteurs.est_hanteur(p.id)

    def rando_lobby(self) -> dict | None:
        """Section « rando » du lobby (lanceur : bandeau, onglet « Indices »). Ni graine, ni table, ni spoiler : les
        indices DÉJÀ dévoilés et les coffres DÉJÀ ouverts, que toute l'équipe connaît."""
        t = self.rando
        if t is None:
            return None
        pris = self.rando_coffres_pris()
        out = {"emp": t.empreinte, "n": len(t.entrees), "mode": t.mode,
               "pris": sorted([i, n, -1 if item is None else item]
                              for (i, n), item in pris.items())[:RANDO_LOBBY_PRIS_MAX]}
        if self.rando_plan is not None:
            out["fichiers"] = {"plan": self.rando_plan, "empreinte": self.rando_fichiers}
        if t.mode == "complet":
            out.update({"options": t.options, "donnees": t.version, "n2": len(t.entrees_n2),
                        "don": len(getattr(t, "entrees_don", ())),
                        "conflit": len(getattr(t, "entrees_conflit", ())),
                        "indices": self.rando_indices_liste(), "indices_total": self.rando_indices_total()})
            if getattr(t, "entrees_fich", None):        # lot 6 (randomizer par fichiers) : seulement s'il y en a
                out["fich"] = len(t.entrees_fich)
        e = self.trk_etat()
        if e is not None:
            out["trk"] = {"v": tk.VERSION, "rev": e["rev"]}   # trackeur : le lanceur demande l'état s'il a changé
        return out

    # --- trackeur du randomizer (docs/etudes/trackeur_randomizer.md) -----------------------------------------
    def trk_actif(self) -> bool:
        return self.trackeur_enabled and self.rando is not None and not self.rando_bloque

    def trk_restaurer(self) -> None:
        """Valide la section « trackeur » relue du monde persistant (les index de lieux ne valent que pour les
        données de logique de ces outils). Trackeur coupé ou partie sans randomizer : la section est gardée telle
        quelle et réécrite inchangée (rien n'est perdu)."""
        if self.trackeur_doc is None or not self.trk_actif():
            return
        try:
            self.trackeur, ignores = tk.restaurer(rm.logique(), self.trackeur_doc)
        except Exception as e:  # noqa: BLE001 : une section abîmée ne doit jamais empêcher le serveur de démarrer
            log(f"trackeur : section du monde illisible ({e}) : trackeur remis à neuf")
            self.trackeur = tk.neuf()
            return
        man = self.trackeur["man"]
        log(f"trackeur : {len(man['lieux'])} lieu(x) et {len(man['objets'])} objet(s) corrigés à la main relus du "
            f"monde" + (f", {ignores} entrée(s) abîmée(s) ou d'autres données ignorée(s)" if ignores else ""))

    def trk_document(self) -> dict | None:
        """Section « trackeur » du monde persistant (None : rien à écrire)."""
        if not self.trk_actif():
            return self.trackeur_doc if isinstance(self.trackeur_doc, dict) else None
        try:
            return tk.exporter(rm.logique(), self.trackeur)
        except Exception:  # noqa: BLE001 : l'écriture du monde ne doit jamais échouer à cause du trackeur
            return self.trackeur_doc if isinstance(self.trackeur_doc, dict) else None

    def trk_etat(self, frais: bool = False) -> dict | None:
        """Message ``trackeur_etat`` courant (None : pas de trackeur). Recalculé au plus toutes les TRK_PERIODE
        secondes depuis l'inventaire commun, la réserve de clés, le registre des coffres (LUS seulement) et les
        corrections ; ``rev`` ne monte que si le contenu a changé. Toute erreur coupe le trackeur sans toucher au
        reste du serveur."""
        if not self.trk_actif():
            return None
        now = time.monotonic()
        if not frais and self._trk_heure and now - self._trk_heure < TRK_PERIODE:
            return self._trk_cache
        self._trk_heure = now
        try:
            lg = rm.logique()
            t = self.rando
            trk = self.trackeur
            coffres = tk.coffres_du_registre(self.coffres if self.coffres_enabled else {})
            inv = list(self.inv.values()) if self.inv_enabled else []
            cles = tk.cles_obtenues(self.keys.groups) if self.keys_enabled else {}
            auto, donjons, suivi = tk.compteurs_auto(lg, inv, cles, coffres, trk["comp"],
                                                     hors=getattr(t, "hors", None))   # lot 5
            if tk.monter_vus(trk["vus"], auto):
                self.mp_marquer()
            obj = tk.compteurs_equipe(lg, auto, trk["vus"], trk["man"]["objets"])
            exact = tk.table_exacte(lg, t.mode, getattr(t, "version", None), getattr(t, "options", None))
            deduits = ()
            if self.trackeur_deduire and exact and t.mode == "complet":
                deduits = tk.deduire(lg, t.entrees, t.entrees_n2, auto, coffres,
                                     getattr(t, "entrees_don", None), getattr(t, "entrees_conflit", None),
                                     getattr(t, "entrees_fich", None))
            # étape 5 : lieux faits d'après les bits d'événement partagés (enchères, Kane, étals de l'Île du Pic, Ivan)
            signaux = L9.lieux_signales(lg, list(self.progress.values()))
            # coffres en conflit ouverts dans LEUR niveau (lot 2), lettres et cadeaux signalés par les clients de jeu
            lieux_auto = tk.coffres_conflit(lg, self.coffres if self.coffres_enabled else {},
                                            getattr(t, "entrees_conflit", None) if t.mode == "complet" else None)
            for i, par in tk.lieux_recus(lg, trk.get("recus")).items():
                lieux_auto.setdefault(i, par)
            if exact and t.mode == "complet":             # accessibilité : avec les verrous des options jouées
                lg = lg.variante(getattr(t, "options", None))
            e = tk.etat(lg, rev=0, emp=t.empreinte, mode=t.mode, exact=exact, suivi=suivi, obj=obj,
                        donjons=donjons, coffres=coffres, man=trk["man"], deduits=deduits,
                        verrou=trk.get("verrou", 0), signaux=signaux, lieux_auto=lieux_auto)
        except Exception as ex:  # noqa: BLE001 : jamais d'effet sur la synchro du jeu
            texte = f"{type(ex).__name__} : {ex}"[:200]
            if texte != self._trk_panne:
                self._trk_panne = texte
                log(f"trackeur : état incalculable ({texte}) : trackeur coupé tant que l'erreur dure")
            self._trk_cache = None
            return None
        self._trk_panne = ""
        avant = self._trk_cache
        if avant is None or dict(avant, rev=0) != e:
            self.trk_rev += 1
        e["rev"] = self.trk_rev
        self._trk_cache = e
        return e

    def _trk_debit(self, p: Player) -> bool:
        now = time.monotonic()
        heures = [h for h in getattr(p, "trk_times", []) if now - h < 1.0]
        ok = len(heures) < TRK_DEBIT
        if ok:
            heures.append(now)
        p.trk_times = heures
        return ok

    def handle_trackeur(self, p: Player, msg: dict) -> None:
        """Demande d'état du trackeur (lanceur). ``rev`` = dernière révision connue : rien n'est renvoyé en entier
        si elle est à jour."""
        if not p.hello or not self._trk_debit(p):
            return
        e = self.trk_etat()
        if e is None:
            p.send({"type": "trackeur_etat", "v": tk.VERSION, "rev": 0, "off": 1})
        elif msg.get("rev") == e["rev"]:
            p.send({"type": "trackeur_etat", "v": tk.VERSION, "rev": e["rev"], "idem": 1})
        else:
            p.send(e)

    def handle_trackeur_cmd(self, p: Player, msg: dict) -> None:
        """Correction manuelle du trackeur par un lanceur (outil lobby). Ne change QUE ``self.trackeur`` : ni le
        registre des coffres, ni l'inventaire commun, ni les clés ; rien n'est envoyé à un client de jeu."""
        if not p.hello or p.role != "rejoindre" or p.outil != "lobby":
            return

        def refus(texte: str) -> None:
            p.send({"type": "trackeur_rep", "ok": 0, "msg": texte, "op": str(msg.get("op"))[:16]})

        if not self._trk_debit(p):
            refus("trop de corrections à la fois : réessaie dans une seconde")
            return
        if not self.trk_actif():
            refus("ce serveur ne tient pas de trackeur (partie sans randomizer, ou --no-trackeur)")
            return
        try:
            lg = rm.logique()
            cmd = tk.valider_cmd(lg, msg)
            if cmd is None:
                refus("correction refusée (lieu, objet ou valeur hors bornes)")
                return
            change, texte = tk.appliquer_cmd(lg, self.trackeur, cmd, p.name, self.lanceur_hote(p))
        except Exception as ex:  # noqa: BLE001
            log(f"trackeur : correction de {p.name} en erreur ({type(ex).__name__} : {ex})")
            refus("erreur du serveur")
            return
        if texte:
            refus(texte)
            return
        if change:
            self.mp_marquer(rapide=True)
            log(f"trackeur : correction de {p.name} ({cmd['op']})")
        e = self.trk_etat(frais=True)
        p.send({"type": "trackeur_rep", "ok": 1, "rev": e["rev"] if e else 0, "op": cmd["op"]})

    def handle_trk(self, p: Player, msg: dict) -> None:
        """Complément du trackeur lu par un client de jeu : vie maximale (quarts de cœur) et lieux reçus dans SON jeu
        (« l » : lettres, cadeaux ; rando/trackeur_client.py). Ne sert qu'au trackeur : jamais relayé, jamais mêlé à
        l'inventaire commun ni aux synchros. Un Hanteur n'arrive jamais ici (« trk » est un message « monde »)."""
        if not p.joueur or not self.trk_actif() or not self._trk_debit(p):
            return
        v = tk.valider_trk(msg)
        comp = self.trackeur["comp"]
        if v is not None and v["vie"] > comp.get("vie", 0):
            comp["vie"] = v["vie"]
            self.mp_marquer()
        recus = self.trackeur.setdefault("recus", {"don": {}, "n2": {}})
        neufs = []
        for genre, site in tk.valider_recus(msg):
            d = recus.setdefault(genre, {})
            if site not in d:
                d[site] = p.name
                neufs.append(f"{genre} {site}")
        if neufs:
            self.mp_marquer()
            log(f"trackeur : {p.name} a reçu dans son jeu : {', '.join(neufs)}")

    def rando_envoyer(self, p: Player) -> None:
        t = self.rando
        if t is None:
            return
        if "rando" not in p.feat:
            log(f"ATTENTION : {p.name} a des outils sans randomizer : ses coffres restent ceux du jeu (mets son PC à "
                "jour) ; ce qu'il ouvre est quand même partagé")
            return
        p.send(t.message())
        self.rando_prog_envoyer(p)                    # lot 4 : comptes des objets progressifs, juste après la table
        self.rando_hors_envoyer(p)                    # lot 5 : coffres étrangers déjà ouverts (grandes clés, cartes)
        if self.rando_indices_total() and not self.rando_sans_indices(p):
            # rappel à l'arrivée : combien d'indices sont déjà dévoilés (leur texte est dans l'onglet « Indices »)
            p.send({"type": "rando_indice", "n": self.rando_indices_n, "total": self.rando_indices_total(),
                    "liste": []})
        if not getattr(p, "rando_cap", False):
            log(f"randomizer : {p.name} n'a pas (encore lu) le pack avec le randomizer (bit 5) : sans lui, ses coffres "
                "restent ceux du jeu (tools/install_graphicpack.py, puis relancer Cemu)")
        elif p.rando_emp and not t.empreinte.startswith(p.rando_emp):
            log(f"randomizer : {p.name} avait une autre table chargée ({p.rando_emp}) : remplacée par {t.empreinte}")

    def prio_hote(self, p: Player) -> bool:
        """Priorité de l'hôte (sac, compteurs, vent, heure) : jamais dans un monde persistant (le monde fait foi)."""
        return p.hote and self.mp is None

    def mp_marquer(self, rapide: bool = False, gain: bool = False) -> None:
        if self.mp is not None:
            self.mp.marquer(rapide=rapide, gain=gain)

    def broadcast(self, msg: dict, exclude: int | None = None) -> None:
        for p in list(self.players.values()):
            if p.id != exclude and p.role == "jeu" and p.auth:  # A15 : l'outil rejoindre ne reçoit rien ;
                # Internet : ni une connexion qui n'a pas encore donné le mot de passe
                p.send(msg)
        if msg.get("type") in robot_mod.DIFFUSION:
            for r in self.robots:             # les robots : autorités, arrivées et départs, règles de la partie
                rp = self.robot_joueur(r)
                if rp is not None and rp.id != exclude:
                    rp.send(msg)

    # --- autorité ----------------------------------------------------------
    def members(self, zone: str) -> list[Player]:
        return [p for p in self.players.values() if p.zone == zone]

    def reassign(self, zone: str) -> None:
        holder, epoch = self.authority.get(zone, (None, 0))
        r = next((x for x in self.robots if x.zone_autorite == zone), None)
        rp = self.robot_joueur(r) if r is not None else None
        if rp is not None and rp.zone == zone:
            # joueur robot (serveur/robot.py) : la zone lui est donnée par robot_tick, une fois la passation sûre
            self.deferred.pop(zone, None)
            if holder != rp.id:
                self.pref_changer(zone, holder, epoch, rp.id, time.monotonic(), "robot du serveur")
            return
        self._reassign(zone)

    def _reassign(self, zone: str, robot_id: int | None = None) -> None:
        """Si la zone n'a pas d'autorité présente, la donner au joueur présent
        depuis le plus longtemps. Chaque changement incrémente l'époque.
        Lot S1 (serveur/autorite.py) : avec un PC d'autorité préféré, il est choisi d'abord à une passation obligée,
        reprend l'autorité d'une zone calme où il est présent (hystérésis) et la rend s'il ne répond plus."""
        holder, epoch = self.authority.get(zone, (None, 0))
        # le robot n'est jamais un candidat ordinaire : sans la zone du robot, il rend l'autorité (passation obligée)
        robots = self.robot_ids() | ({robot_id} if robot_id is not None else set())
        present = [p for p in self.members(zone) if p.id not in robots]
        if self.hanteurs is not None:
            # mode Hanteurs : un Hanteur n'a jamais l'autorité (ses monstres viseraient un Link caché) ; une zone
            # sans Link n'a pas d'autorité (« personne »)
            present = [p for p in present if not self.hanteurs.hors_jeu(p.id)]   # phase 2 : ni un Link possédé
        now = time.monotonic()
        if not present:
            for d in (self.auth_t, self.combat_t, self.pref_vu, self.ennemis_pv):
                d.pop(zone, None)       # zone vide : rien à retenir (la première attribution n'est pas une passation)
        raison = ""
        if holder is not None and any(p.id == holder for p in present):
            self.deferred.pop(zone, None)
            new, raison = self.pref_reprise(zone, holder, present, now) if self.pref.actif else (None, "")
            if new is None:
                return
            self.pref_changer(zone, holder, epoch, new, now, raison)
            return
        candidates = present
        if self.busy_defer and present:
            # A11 : on préfère un joueur libre ; si tous sont en cinématique, on attend (au plus
            # busy_defer_max s) : l'ancienne autorité reste inscrite, ses snapshots sont refusés
            # (elle n'est plus dans la zone) et les marionnettes des autres se relâchent seules
            free = [p for p in present if not p.busy]
            now = time.monotonic()
            if free:
                candidates = free
            elif now - self.deferred.setdefault(zone, now) < self.busy_defer_max:
                if holder is not None:
                    return
            else:
                log(f"autorité {zone} : joueurs toujours en cinématique après {self.busy_defer_max:.0f} s, "
                    "passation quand même")
        self.deferred.pop(zone, None)
        new = None
        if candidates and self.pref.actif:
            new = self.pref_meilleur(candidates, now)
            if new is not None:
                raison = "PC d'autorité préféré"
        if new is None and candidates and self.pref.actif:
            # S1 : un joueur qui ne répond plus (préféré en repli) n'est pas repris d'office s'il y a mieux
            candidates = [p for p in candidates if self.pref_repond(p, now)] or candidates
        if new is None:
            new = min(candidates, key=lambda p: (p.zone_since, p.id)).id if candidates else None
        if new == holder:
            return
        self.pref_changer(zone, holder, epoch, new, now, raison)

    def coups_proj_ok(self, holder) -> bool:
        """Bit 26 (mod natif) : l'autorité ``holder`` sait rejouer un coup de projectile (« coups_proj » dans hello)."""
        o = self.players.get(holder) if holder is not None else None
        return o is not None and coups_projectiles.FEAT in o.feat

    def msg_autorite(self, zone: str, holder, epoch: int) -> dict:
        """Message ``authority`` ; ``cp`` = 1 (optionnel, ignoré d'un ancien client) si l'autorité rejoue les coups de
        projectiles (synchro/coups_projectiles.py) : les attaquants n'envoient ``injp`` qu'alors."""
        m = {"type": "authority", "zone": zone, "holder": holder, "epoch": epoch}
        if self.coups_proj_ok(holder):
            m["cp"] = 1
        if holder is not None and holder in self.robot_ids():
            m["robot"] = 1          # joueur robot : affiché « le robot du serveur » (ignoré d'un ancien client)
        return m

    def pref_changer(self, zone: str, holder: int | None, epoch: int, new: int | None, now: float,
                     raison: str = "") -> None:
        epoch += 1
        self.authority[zone] = (new, epoch)
        if holder is not None and new is not None:
            self.auth_t[zone] = now             # S1 : passation entre deux joueurs (hystérésis des reprises)
            if self.robot.actif and not ({holder, new} & self.robot_ids()):
                # joueur robot : une autorité qui bascule souvent entre joueurs est une raison de la prendre
                b = [t for t in self.robot_bascules.get(zone, []) if now - t < robot_mod.BASCULES_FENETRE]
                self.robot_bascules[zone] = b + [now]
                self.robot.mesures.bascules_vues += 1
        self.ennemis_pv.pop(zone, None)         # S1 : les PV de la nouvelle autorité ne se comparent pas aux anciens
        who = self.players[new].name if new is not None else "personne"
        log(f"autorité {zone} -> {who} (époque {epoch})" + (f" : {raison}" if raison else ""))
        self.broadcast(self.msg_autorite(zone, new, epoch))

    def autorite_msg(self, zone: str, holder: int | None, epoch: int) -> dict:
        return self.msg_autorite(zone, holder, epoch)

    # --- lot S1 : hôte d'autorité préféré (serveur/autorite.py) ----------------------------------------------
    def pref_rang(self, p: Player) -> int | None:
        hote = p.hote or (self.admin is None and p.ip in self._addrs_locales())
        return self.pref.rang(p.name, hote)

    @staticmethod
    def pref_repond(p: Player, now: float) -> bool:
        """Un « state » est arrivé de lui depuis moins de PREF_SILENCE s."""
        return p.state_t is not None and now - p.state_t <= autorite.PREF_SILENCE

    def pref_meilleur(self, candidates: list[Player], now: float, installe: bool = False) -> int | None:
        """Le meilleur PC préféré parmi ``candidates`` (rang, puis le plus ancien présent), libre et qui répond ;
        ``installe`` : en plus, dans la zone et répondant depuis PREF_ENTREE s (reprise). None s'il n'y en a pas."""
        best = None
        for p in candidates:
            r = self.pref_rang(p)
            if r is None or p.busy or not self.pref_repond(p, now):
                continue
            if installe and now - max(p.zone_since, p.frais_depuis) < autorite.PREF_ENTREE:
                continue
            k = (r, p.zone_since, p.id)
            if best is None or k < best[0]:
                best = (k, p.id)
        return best[1] if best is not None else None

    def pref_reprise(self, zone: str, holder: int, present: list[Player], now: float) -> tuple[int | None, str]:
        """Autorité présente : (nouvelle autorité, raison) si le PC préféré doit la prendre, ou la rendre (repli) ;
        (None, "") sinon."""
        h = self.players.get(holder)
        if h is None:
            return None, ""
        if self.pref_rang(h) is not None:
            if self.pref_repond(h, now) or h.state_t is None:
                return None, ""             # un préféré qui répond la garde (jamais repris par un autre préféré)
            # repli : le préféré ne répond plus (connexion qui décroche, jeu figé). Pas de calme exigé : ses
            # monstres sont figés chez les autres de toute façon
            autres = [p for p in present if p.id != holder and not p.busy and self.pref_repond(p, now)]
            if not autres:
                return None, ""
            new = self.pref_meilleur(autres, now)
            if new is None:
                new = min(autres, key=lambda p: (p.zone_since, p.id)).id
            return new, f"repli, {h.name} ne répond plus depuis {now - h.state_t:.1f} s"
        if now - self.auth_t.get(zone, float("-inf")) < autorite.PREF_APRES:
            return None, ""                 # hystérésis : passation récente dans cette zone
        if any(p.busy for p in present):
            return None, ""                 # A11 : jamais pendant une cinématique
        if now - self.combat_t.get(zone, float("-inf")) < autorite.PREF_CALME:
            return None, ""                 # jamais au milieu d'un combat
        h_mod = self.hanteurs
        if h_mod is not None and any(ps.get("zone") == zone for ps in h_mod.possessions.values()):
            return None, ""                 # un monstre en laisse vit chez l'autorité actuelle
        new = self.pref_meilleur([p for p in present if p.id != holder], now, installe=True)
        if new is None:
            return None, ""
        return new, "PC d'autorité préféré (zone calme)"

    def pref_combat(self, zone: str | None, now: float | None = None) -> None:
        """S1 : signe de combat dans la zone (coup porté, reçu, monstre blessé ou tué) : pas de reprise avant
        PREF_CALME s."""
        if zone:
            self.combat_t[zone] = time.monotonic() if now is None else now

    def pref_ennemis(self, zone: str, lst: list) -> None:
        """S1 : un monstre blessé ou tué dans l'instantané de l'autorité = combat."""
        vus = self.ennemis_pv.get(zone)
        neuf = {}
        combat = False
        for e in lst[:256]:
            if not isinstance(e, dict) or not isinstance(e.get("key"), str):
                continue
            hp = e.get("hp")
            hp = 0 if e.get("dead") in (1, True) else hp
            if not isinstance(hp, (int, float)) or isinstance(hp, bool):
                continue
            neuf[e["key"]] = hp
            if vus is not None:
                avant = vus.get(e["key"])
                if (avant is not None and hp < avant) or (avant is None and hp <= 0):
                    combat = True
        if vus is not None:
            vus.update(neuf)
            if len(vus) > 512:
                vus = neuf
            self.ennemis_pv[zone] = vus
        else:
            self.ennemis_pv[zone] = neuf
        if combat:
            self.pref_combat(zone)

    def pref_revoir(self, zone: str, now: float) -> None:
        """S1 : reprise et repli revus au plus toutes les PREF_PAS s par zone (sur les « state » de ses membres)."""
        if now - self.pref_vu.get(zone, float("-inf")) < autorite.PREF_PAS:
            return
        self.pref_vu[zone] = now
        self.reassign(zone)

    def admin_autorite(self, valeur, qui: str = "l'administrateur") -> bool:
        """Réglage changé à chaud (administration) ; les zones sont revues tout de suite (mêmes règles : une zone
        en combat attend d'être calme)."""
        if not self.pref.regler(valeur):
            return False
        log(f"PC d'autorité préféré changé par {qui} : {self.pref.texte()}")
        for zone in sorted(z for z, (h, _e) in self.authority.items() if h is not None):
            self.reassign(zone)
        return True

    def autorites_vue(self) -> list[dict]:
        """Autorité de chaque zone occupée (administration : lecture seule)."""
        out = []
        for zone, (holder, epoch) in sorted(self.authority.items()):
            o = self.players.get(holder) if holder is not None else None
            if o is None:
                continue
            robots = self.robot_ids()
            robot = o.id in robots
            d = {"zone": zone, "joueur": o.name, "epoque": epoch,
                 "preferee": not robot and self.pref_rang(o) is not None,
                 "presents": len([m for m in self.members(zone) if m.id not in robots])}
            if robot:
                d["robot"] = True                # joueur robot (absent sinon : sortie d'avant inchangée)
            out.append(d)
        return out[:64]

    # --- joueur robot (serveur/robot.py, docs/etudes/joueur_robot.md) ------------------------------------------
    def robot_joueur(self, r=None) -> Player | None:
        """La connexion du robot ``r`` (défaut : le premier ; None : pas connecté, ou --robot off)."""
        pid = (r or self.robot).pid
        return self.players.get(pid) if pid is not None else None

    def robot_ids(self) -> set:
        """Identifiants des connexions de robots (--robots N : jusqu'à N)."""
        return {r.pid for r in self.robots if r.pid is not None}

    def robot_de(self, p: Player | None):
        """Le robot dont ``p`` est la connexion (None : ``p`` n'est pas un robot connecté)."""
        if p is None or p.role != robot_mod.ROLE:
            return None
        return next((r for r in self.robots if r.pid == p.id), None)

    def robot_est(self, p: Player | None) -> bool:
        return self.robot_de(p) is not None

    def agit(self, p: Player) -> bool:
        """Joueur de la partie, ou le robot (pour les seuls messages d'une autorité de zone)."""
        return p.joueur or self.robot_est(p)

    def robot_hello(self, p: Player, msg: dict) -> None:
        rang = msg.get("rang", 0)
        rang = rang if isinstance(rang, int) and not isinstance(rang, bool) else -1
        r = self.robots[rang] if 0 <= rang < len(self.robots) else None
        if r is None or not r.actif or not robot_mod.cle_valide(r.cle, msg.get("cle")):
            log(f"connexion robot ({p.peer}) refusée : "
                + ("rang inconnu (--robots)" if r is None else
                   "robot désactivé (--robot off)" if not r.actif else "clé du robot fausse"))
            p.send({"type": "error", "code": "robot", "msg": "robot refusé"})
            p.writer.close()
            return
        ancien = self.robot_joueur(r)
        if ancien is not None and ancien.id != p.id:
            log(f"{r.pre} : ancienne connexion (id {ancien.id}) remplacée par la nouvelle (id {p.id})")
            ancien.role = "remplace"          # son départ ne touchera plus l'état du robot
            ancien.writer.close()
        p.role, p.hello, p.name = robot_mod.ROLE, True, r.nom
        feat = msg.get("feat")
        p.feat = {str(f)[:24] for f in feat[:32]} if isinstance(feat, list) else set()
        r.pid = p.id
        r.zone_autorite = None
        r.changer_cible(None)
        r.dernier_evt = "connecté"
        if r.essai and r.latence > 0:
            # essai solo : un gérant éloigné. Tout ce qui part vers le robot attend latence/2 (dans l'ordre) ; ce qui
            # vient de lui aussi (handle). Le relevé L2 (rtt de ses « ping ») mesure donc la latence ajoutée
            envoyer = p.send
            p.send = lambda m, _e=envoyer: r.vers_robot.mettre(lambda: _e(m))
        log(f"{r.pre} connecté (id {p.id}, {p.peer}, outils {msg.get('client', '?')}) : il n'est pas un joueur "
            "(ni place, ni fantôme, ni liste)" + (f" ; essai solo, {r.latence} ms d'aller-retour ajoutées"
                                                 if r.essai and r.latence > 0 else ""))
        p.send({"type": "welcome", "id": p.id, "version": VERSION, "name": p.name, "outils": CLIENT_VERSION,
                "robot": 1, "couleur": 0,
                "players": [{"id": o.id, "name": o.name, "pack": o.pack, "couleur": o.couleur, "pause": o.pause}
                            for o in self.players.values() if o.joueur],
                "deathlink": int(self.morts.on), "vie_partagee": int(self.vies.on), "difficulte": self.dif_vue(),
                "epoque": self.epoque, **({"monde": self.mp.bref()} if self.mp is not None else {})})
        for zone, (holder, epoch) in self.authority.items():
            p.send(self.autorite_msg(zone, holder, epoch))

    def robot_message(self, p: Player, msg: dict) -> None:
        """Ce que le robot peut envoyer : ce qu'envoie une autorité de zone (TYPES_ACCEPTES), ses « state » (jamais
        relayés) et ses rapports. Le reste est ignoré : il ne touche jamais au monde."""
        t = msg.get("type")
        r = self.robot_de(p)
        if r is None:
            return
        if t not in robot_mod.TYPES_ACCEPTES:
            r.ignores = getattr(r, "ignores", 0) + 1
            return
        if t == "state":
            stage, room = msg.get("stage"), msg.get("room")
            zone = zone_of(stage, room) if isinstance(stage, str) and stage and isinstance(room, int) else None
            pos = msg.get("pos")
            if isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, (int, float)) for v in pos):
                p.pos = pos
            p.state_t = time.monotonic()
            if r.zone_autorite is not None and zone != r.zone_autorite:
                # il quitte (ou recharge) la zone qu'il tient : retour forcé, set_zone la rend aux joueurs
                z, r.zone_autorite = r.zone_autorite, None
                if r.tenue_t is not None:
                    r.rendues += 1
                    log(r.mesurer_rendu(z, "robot sorti de la zone", True, p.state_t))
            self.set_zone(p, zone)
            if zone is not None and zone == r.cible:
                if r.arrive_t is None:
                    r.arrive_t = p.state_t
            else:
                r.arrive_t = r.snap_t = None
            return
        if t == "robot":
            self.robot_rapport(p, msg)
            return
        self._handle(p, msg)

    def robot_rapport(self, p: Player, msg: dict) -> None:
        r = self.robot_de(p)
        if r is None:
            return
        ev = msg.get("ev")
        zone = msg.get("zone")
        if ev == "pret" and isinstance(zone, str) and len(zone) <= 40:
            if r.pret != zone:
                log(f"{r.pre} : prêt dans {zone}")
            r.pret = zone
        elif ev == "etat":
            garde = {}
            for k in ("phase", "zone", "fps", "mem_mo", "cpu", "jeu", "version"):
                v = msg.get(k)
                if isinstance(v, (int, float, str)) and not isinstance(v, bool) and len(str(v)) <= 40:
                    garde[k] = v
            r.rapport = garde
            t = time.monotonic()
            if t - getattr(r, "etat_log_t", float("-inf")) >= 60.0 and garde.get("phase") != "repos":
                r.etat_log_t = t                 # une ligne par minute : de quoi voir si le jeu du serveur suit
                log(f"{r.pre} : état {garde.get('phase', '?')} {garde.get('zone') or ''}"
                    + (f", {garde['fps']:.0f} pas par seconde" if isinstance(garde.get("fps"), (int, float)) else ""))
        elif ev == "erreur":
            log(f"{r.pre} : erreur signalée : {str(msg.get('msg', ''))[:200]}")
            r.dernier_evt = "erreur : " + str(msg.get("msg", ""))[:80]
            if isinstance(zone, str) and zone == r.cible:
                r.choix.ecarter(zone, time.monotonic())     # il n'y arrive pas : zone mise de côté un moment
                r.changer_cible(None)

    def robot_snap(self, zone: str, src: Player) -> None:
        """Un instantané de l'autorité humaine de ``zone`` vient d'être relayé : le robot qui y attend l'a reçu."""
        for r in self.robots:
            rp = self.robot_joueur(r)
            if rp is not None and src.id != rp.id and rp.zone == zone and zone == r.cible \
                    and r.arrive_t is not None:
                t = time.monotonic()
                if r.snap_t is None:
                    r.snap1_t = t               # premier instantané depuis son arrivée (SNAP_REGLAGE)
                r.snap_t = t

    def robot_relais(self, p: Player, out: dict) -> None:
        """« state » d'un joueur : au robot s'il est dans sa zone (ses monstres visent les fantômes, A3)."""
        for r in self.robots:
            rp = self.robot_joueur(r)
            if rp is not None and p.id != rp.id and p.zone is not None and rp.zone == p.zone:
                rp.send(out)

    def robot_comptes(self, now: float) -> dict:
        """Zone -> (joueurs humains présents qui répondent, heure depuis laquelle elle est partagée)."""
        par: dict[str, list[float]] = {}
        for o in self.players.values():
            if not o.joueur or o.zone is None:
                continue
            if self.hanteurs is not None and self.hanteurs.hors_jeu(o.id):
                continue
            if o.state_t is None or now - o.state_t > robot_mod.REPONSE_MAX:
                continue
            if o.zone.rsplit(":", 1)[0] in BOSS_STAGES:
                continue        # salle de boss : aucun monstre n'y est synchronisé (chacun sa Gohma, état commun
                                # tenu par le serveur : synchro/boss_unique.py) : un jeu du serveur n'y sert à rien
            if o.zone.rsplit(":", 1)[0] in STAGES_FIGENT_LE_JEU_CACHE:
                continue        # le jeu caché du serveur s'y fige (banc du 2026-11-21, même sans aucun mod)
            par.setdefault(o.zone, []).append(o.zone_since)
        out = {}
        for z, ts in par.items():
            ts.sort()
            out[z] = (len(ts), ts[1] if len(ts) >= 2 else ts[0])
        return out

    def robot_centre(self, zone: str) -> list | None:
        pts = [o.pos for o in self.players.values() if o.joueur and o.zone == zone and o.pos]
        if not pts:
            return None
        return [round(sum(q[i] for q in pts) / len(pts), 1) for i in range(3)]

    def robot_ordre(self, rp: Player, ordre: dict, now: float, rappel: float = robot_mod.ORDRE_RAPPEL,
                    r=None) -> None:
        r = r or self.robot_de(rp) or self.robot
        cle = {k: v for k, v in ordre.items() if k != "pos"}
        if r.ordre is not None and {k: v for k, v in r.ordre.items() if k != "pos"} == cle \
                and now - r.ordre_t < rappel:
            return
        if r.ordre is None or r.ordre.get("ordre") != ordre.get("ordre") or r.ordre.get("zone") != ordre.get("zone"):
            log(f"{r.pre} : ordre « {ordre.get('ordre')} »" + (f" {ordre['zone']}" if ordre.get("zone") else ""))
        r.ordre, r.ordre_t = dict(ordre), now
        rp.send({"type": "robot", **ordre})

    def robot_tick(self, now: float | None = None) -> None:
        """Choix de la zone (règles 2 et 3), processus, ordres et passations. Appelé toutes les 0,5 s
        (robot_boucle) ; les tests l'appellent avec une horloge simulée. --robots N : chaque robot à son tour, le
        premier choisit d'abord ; une zone visée par un robot n'est jamais proposée à un autre (un robot par zone)."""
        now = time.monotonic() if now is None else now
        # les robots dont le jeu tourne déjà choisissent d'abord (essai du 2026-11-20 avec un vrai jeu : le joueur
        # change de zone, le robot en place attendait de quitter la sienne et un 2e jeu était démarré, 65 s)
        for r in sorted(self.robots, key=lambda x: (x.pid is None, x.rang)):
            if r.actif:
                self._robot_tick(r, now)

    def _robot_tick(self, r, now: float) -> None:
        if r.latence:
            r.vers_robot.vider(now)          # essai solo : messages retardés dus (la boucle asyncio le fait aussi)
            r.du_robot.vider(now)
        if now - getattr(r, "bilan_t", now) >= 300.0:
            log(f"{r.pre} : BILAN {r.mesures.vue()}")     # de quoi décider après l'essai : le garder ou l'enlever
            r.bilan_t = now
        elif not hasattr(r, "bilan_t"):
            r.bilan_t = now
        comptes = self.robot_comptes(now)
        if len(self.robots) > 1:
            tous = comptes
            prises = {x.cible for x in self.robots if x is not r and x.cible is not None}
            comptes = {z: v for z, v in comptes.items() if z not in prises}
            if r.pid is None and r.cible is None:
                # un jeu déjà lancé et libre (au repos, ou dont la zone vient de se vider) passe avant un jeu à
                # démarrer : ce robot-ci ne prend que les zones qu'aucun robot en marche ne peut prendre
                libres = [x for x in self.robots if x is not r and x.pid is not None
                          and (x.cible is None or tous.get(x.cible, (0, 0.0))[0] < x.choix.minimum)]
                if libres:
                    comptes = {z: v for z, v in comptes.items() if all(z in x.choix.ecartees for x in libres)}
        cible = r.choix.revoir(comptes, now)
        r.changer_cible(cible)
        rp = self.robot_joueur(r)
        if r.gest is not None:
            if cible is not None:
                r.repos_t = None
            elif r.repos_t is None:
                r.repos_t = now
            garder = r.gest.en_marche() and r.repos_t is not None and now - r.repos_t < r.repos_arret
            # essai solo : jamais de repos, le processus tourne tant que le serveur tourne (l'amorçage est long)
            r.gest.tick(cible is not None or garder or r.essai)
        if rp is None:
            r.zone_autorite = None
            return
        tenue = next((z for z, (h, _e) in self.authority.items() if h == rp.id), None)
        if tenue is not None:
            # retour automatique à l'autorité joueur : robot qui rame, se tait ou charge une autre zone (forcé,
            # tout de suite, zone mise de côté) ; plus rien à gagner depuis RETOUR_DELAI s
            force = ""
            fps = r.rapport.get("fps")
            if rp.state_t is None or now - rp.state_t > robot_mod.ROBOT_SILENCE:
                force = "robot muet"
            elif rp.zone != tenue:
                force = "robot en chargement"
            elif isinstance(fps, (int, float)) and fps < r.fps_min \
                    and (r.arrive_t is None or now - r.arrive_t >= robot_mod.FPS_GRACE):
                force = f"robot trop lent ({fps:.0f} i/s)"
            if force:
                r.choix.ecarter(tenue, now, r.pause_echec)
                r.changer_cible(None)
                self.robot_rendre(tenue, force, True, now, r)
                return
            if tenue == cible:
                ok, _raison = self.robot_gain(tenue, now, r)
                if ok:
                    r.sans_gain_t = None
                else:
                    r.sans_gain_t = now if r.sans_gain_t is None else r.sans_gain_t
                    if now - r.sans_gain_t >= robot_mod.RETOUR_DELAI:
                        r.pas_avant[tenue] = now + robot_mod.REPRISE_APRES
                        self.robot_rendre(tenue, "plus de gain", False, now, r)
                        return
        if tenue is not None and tenue != cible:
            # il doit partir : d'abord RENDRE l'autorité aux joueurs de la zone (A11 compris), l'ordre suit ensuite
            self.robot_rendre(tenue, "autre zone" if cible else "repos", False, now, r)
            return
        if tenue is not None:
            if now - r.suivi_t >= robot_mod.SUIVI_PAS:
                r.suivi_t = now
                centre = self.robot_centre(tenue)
                if centre is not None:
                    rp.send({"type": "robot", "ordre": "suis", "zone": tenue, "pos": centre})
            return
        if cible is None:
            r.zone_autorite = None
            self.robot_ordre(rp, {"ordre": "dors"}, now, rappel=30.0, r=r)
            return
        stage, _, room = cible.rpartition(":")
        if rp.zone != cible or r.pret != cible:
            demarre = rp.zone is None or rp.zone.split(":", 1)[0] in robot_mod.HORS_PARTIE
            if r.envoye_t is None:
                r.envoye_t = r.demarrage_t = now
            elif demarre and now - getattr(r, "demarrage_t", now) < robot_mod.DEMARRAGE_MAX:
                r.envoye_t = now             # son jeu démarre encore (écran titre) : le trajet n'a pas commencé
            elif now - r.envoye_t > robot_mod.TRAJET_MAX:
                log(f"{r.pre} : pas prêt dans {cible} après {robot_mod.TRAJET_MAX:.0f} s, zone mise de côté")
                r.dernier_evt = f"{cible} mise de côté"
                r.choix.ecarter(cible, now)
                r.changer_cible(None)
                return
            ordre = {"ordre": "va", "zone": cible, "stage": stage, "room": int(room) if room.isdigit() else room}
            centre = self.robot_centre(cible)
            if centre is not None:
                ordre["pos"] = centre
            self.robot_ordre(rp, ordre, now, r=r)
            return
        # dans la zone, prêt, pas encore l'autorité. Jamais pire qu'aujourd'hui : il ne la prend que s'il y gagne
        # (robot_gain) depuis GAIN_DELAI s ; sinon il reste en veille, l'autorité reste au joueur (0 ms pour lui)
        ok, raison = self.robot_gain(cible, now, r)
        if now < r.pas_avant.get(cible, float("-inf")):
            ok, raison = False, "reprise trop tôt"
        if ok != r.gain[0]:
            log(f"{r.pre} : {cible} : " + (f"gain ({raison}), prise dès que sûre" if ok
                                         else f"rien à gagner ({raison}), l'autorité reste au joueur"))
            if not ok:
                r.mesures.sans_gain += 1
        r.gain = (ok, raison)
        if not ok:
            r.gain_t = r.attente_t = None
            return
        if r.gain_t is None:
            r.gain_t = now
        if now - r.gain_t < robot_mod.GAIN_DELAI and not (r.essai or r.hote):
            return
        if r.attente_t is None:
            r.attente_t = now
        holder, _e = self.authority.get(cible, (None, 0))
        if r.snap_t is not None:
            # instantanés reçus : le jeu du robot doit avoir eu le temps d'y ranger ses monstres (déjà tués : supprimés)
            snap_ok = now - getattr(r, "snap1_t", r.snap_t) >= robot_mod.SNAP_REGLAGE
        else:
            snap_ok = holder is None or (r.arrive_t is not None and now - r.arrive_t >= robot_mod.SNAP_ATTENTE)
        humains = [o for o in self.members(cible) if o.joueur]
        occupe = any(o.busy for o in humains)
        calme = now - self.combat_t.get(cible, float("-inf")) >= robot_mod.CALME_PRISE
        force = now - r.attente_t >= robot_mod.PRISE_MAX
        if not snap_ok or occupe or not (calme or force):
            return
        avant = self.players.get(holder) if holder is not None else None
        r.zone_autorite = cible
        self.reassign(cible)
        if self.authority.get(cible, (None, 0))[0] == rp.id:
            r.prises += 1
            r.dernier_evt = f"autorité de {cible} prise ({raison})" + ("" if calme else ", combat en cours")
            log(r.mesurer_prise(cible, raison, now - r.attente_t, avant.rtt if avant is not None else None,
                                self.robot_rtt(rp), now))
            if r.essai:
                log(f"ESSAI ROBOT : autorité de {cible} -> le robot du serveur (tes monstres y sont des marionnettes"
                    + (f", {r.latence} ms d'aller-retour ajoutées" if r.latence else "") + ")")

    def robot_rtt(self, rp: Player | None) -> float:
        return rp.rtt if rp is not None and rp.rtt is not None else robot_mod.ROBOT_RTT_DEFAUT

    def robot_gain(self, zone: str, now: float, r=None) -> tuple[bool, str]:
        """Le robot améliore-t-il la zone ? (oui, raison) seulement si l'autorité joueur (actuelle, ou celle qui la
        reprendrait) a un aller-retour nettement plus long que le robot ou instable, si l'autorité bascule souvent
        entre joueurs, ou si aucun joueur présent n'est un bon candidat (muet). Sinon (non, raison) : l'autorité
        reste au joueur, qui garde ses monstres natifs. RTT inconnu : non (on ne change rien sans mesure)."""
        r = r or self.robot
        if r.essai:
            return True, "essai solo"      # --robot essai : on veut VOIR le robot faire autorité, même sans gain
        if r.hote:
            return True, "serveur hôte"    # --robot hote : les monstres sont toujours ceux du serveur
        rp = self.robot_joueur(r)
        humains = [o for o in self.members(zone) if o.joueur and not o.busy]
        holder, _e = self.authority.get(zone, (None, 0))
        bascules =[t for t in self.robot_bascules.get(zone, []) if now - t < robot_mod.BASCULES_FENETRE]
        if len(bascules) >= robot_mod.BASCULES_MAX:
            return True, f"{len(bascules)} bascules en {robot_mod.BASCULES_FENETRE:.0f} s"
        cand = next((o for o in humains if o.id == holder), None)
        if cand is None:
            # le robot tient la zone (ou elle est en passation) : le joueur qui la reprendrait (règle habituelle)
            cand = min(humains, key=lambda o: (o.zone_since, o.id)) if humains else None
        if cand is None:
            return False, "aucun joueur libre"
        if not self.pref_repond(cand, now):
            return True, f"{cand.name} ne répond plus"
        if cand.rtt is None:
            return False, f"aller-retour de {cand.name} inconnu"
        ecart = cand.rtt - self.robot_rtt(rp)
        if ecart >= robot_mod.GAIN_RTT:
            return True, f"aller-retour {cand.name} {cand.rtt:.0f} ms, robot {self.robot_rtt(rp):.0f} ms"
        hist = getattr(cand, "rtt_hist", None) or []
        if len(hist) >= 5:
            moy = sum(hist) / len(hist)
            ecart_type = (sum((x - moy) ** 2 for x in hist) / len(hist)) ** 0.5
            if ecart_type >= robot_mod.GIGUE:
                return True, f"aller-retour de {cand.name} instable (écart type {ecart_type:.0f} ms)"
        return False, f"{cand.name} est une bonne autorité ({cand.rtt:.0f} ms)"

    def robot_rendre(self, zone: str, raison: str, force: bool, now: float, r=None) -> None:
        """Rend l'autorité de ``zone`` aux joueurs présents (règle habituelle, A11 compris). Jamais de zone sans
        autorité : s'il reste un joueur, il l'a ; l'ordre de départ ne part qu'après."""
        if r is None:
            h = self.authority.get(zone, (None, 0))[0]
            r = next((x for x in self.robots if x.zone_autorite == zone or (h is not None and x.pid == h)),
                     self.robot)
        rp = self.robot_joueur(r)
        r.zone_autorite = None
        self.reassign(zone)
        if rp is None or self.authority.get(zone, (None, 0))[0] != rp.id:
            r.rendues += 1
            r.sans_gain_t = None
            r.dernier_evt = f"autorité de {zone} rendue ({raison})"
            log(r.mesurer_rendu(zone, raison, force, now))
            if r.essai:
                h = self.authority.get(zone, (None, 0))[0]
                log(f"ESSAI ROBOT : autorité de {zone} -> "
                    + (self.players[h].name if h in self.players else "personne") + f" ({raison})")

    def robot_depart(self, p: Player) -> None:
        r = next((x for x in self.robots if x.pid == p.id), None)
        if r is None:
            return
        log(f"{r.pre} déconnecté (id {p.id})" + (f" : il tenait {r.zone_autorite}, rendue aux joueurs"
                                                 if r.zone_autorite else ""))
        if r.tenue_t is not None:
            log(r.mesurer_rendu(r.zone_autorite or "?", "robot déconnecté", True, time.monotonic()))
        r.pid = None
        r.zone_autorite = None
        r.changer_cible(None)
        r.dernier_evt = "déconnecté"

    def admin_robot(self, mode, qui: str = "l'administrateur"):
        """Joueur robot à chaud : True si changé ; un texte (avertissement) si refusé. « off » : la zone tenue est
        rendue aux joueurs tout de suite, le processus arrêté, le robot déconnecté."""
        if robot_mod.actif(mode) and self.hanteurs is not None:
            return "robot : impossible avec le mode Hanteurs"
        if not self.robot.regler(mode):
            return False
        log(f"robot : « {self.robot.mode} » (changé par {qui})")
        for r in self.robots:
            if r is not self.robot:
                r.regler(mode)
                r.cle = self.robot.cle            # une seule clé pour tous les robots
                if r.gest is not None:
                    r.gest.cle = r.cle or ""
            if r.mode == "off":
                rp = self.robot_joueur(r)
                tenue = next((z for z, (h, _e) in self.authority.items() if rp is not None and h == rp.id), None)
                if tenue is not None:
                    self.robot_rendre(tenue, "robot coupé", True, time.monotonic(), r)
                if r.gest is not None:
                    r.gest.arreter("robot coupé")
                if rp is not None:
                    rp.writer.close()
                r.changer_cible(None)
                r.choix.cible = None
        return True

    def robot_vue(self) -> dict | None:
        """État du robot (administration, lobby) ; None : --robot off. --robots N : celui du premier, plus
        « robots » (la liste de tous, absente avec un seul : sortie d'avant inchangée)."""
        if not self.robot.actif:
            return None
        v = self._robot_vue(self.robot)
        if len(self.robots) > 1:
            v["robots"] = [dict(self._robot_vue(r), rang=r.rang) for r in self.robots]
        return v

    def _robot_vue(self, r) -> dict:
        rp = self.robot_joueur(r)
        tenue = next((z for z, (h, _e) in self.authority.items() if rp is not None and h == rp.id), None)
        return {"mode": r.mode, "phase": r.phase(rp.zone if rp else None, tenue),
                "connecte": rp is not None, "zone": rp.zone if rp else None, "cible": r.cible, "autorite": tenue,
                "prises": r.prises, "rendues": r.rendues, "evenement": r.dernier_evt, "rapport": dict(r.rapport),
                "repos_arret": r.repos_arret, "processus": r.gest.vue() if r.gest is not None else None,
                "gain": {"oui": r.gain[0], "raison": r.gain[1]}, "mesures": r.mesures.vue(),
                **({"latence_ms": r.latence} if r.essai else {})}

    # --- lot S2 : validation côté serveur (serveur/validation.py) ----------------------------------------------
    def valid_exempt(self, p: Player) -> bool:
        """Jamais mesuré : Hanteur (son Link caché suit les Link, se téléporte), Link possédé, clone de la potion."""
        h = self.hanteurs
        return bool(h is not None and p.joueur and (h.hors_jeu(p.id) or p.id in h.potions))

    def admin_validation(self, mode, qui: str = "l'administrateur") -> bool:
        if not self.valid.regler(mode):
            return False
        log(f"validation côté serveur : « {mode} » (changé par {qui})")
        return True

    def set_zone(self, p: Player, zone: str | None) -> None:
        if zone == p.zone:
            return
        old, p.zone, p.zone_since = p.zone, zone, time.monotonic()
        log(f"{p.name} : {old} -> {zone}")
        # salle quittée : sa zone vit encore chez lui jusqu'au changement de salle suivant (zoneCountCheck) ; un
        # passage sans zone (chargement) ne compte pas ; un autre stage efface toutes les zones (initZone)
        if old:
            p.zone_prev = old
        if zone and p.zone_prev and zone_stage(p.zone_prev) != zone_stage(zone):
            p.zone_prev = None
        moved = p.carried + self.sticks_take(p, old)   # A3 : bâtons du porteur, suivis de zone en zone
        self.carry.moved(p.id, old)                    # objets portés : verrous de l'ancienne zone libérés
        p.carried = [] if zone else moved
        if old:
            self.reassign(old)
            self.ground_gc(old)
        if zone:
            self.reassign(zone)
            self.send_last_snapshot(p, zone)
            self.ground_push(p, zone)
            self.sticks_give(p, zone, moved)
            self.sticks_push(p, zone)
            self.mob_push(p, zone)
            self.herbe_push(p, zone)
            if self.hanteurs is not None and p.joueur:
                self.hanteurs.entree_zone(p.id, zone)   # monstres invoqués encore là : créés chez lui
        self.salles_gc()
        if zone:
            self.salles_push(p, zone)
        if p.tsw_group is not None:
            self.tsw_gc()
            self.tsw_push(p)
        if p.boss_group is not None:
            self.boss_gc()

    def send_last_snapshot(self, p: Player, zone: str) -> None:
        """Étape 7 : qui entre dans une zone en plein combat reçoit tout de suite le dernier
        snapshot de l'autorité actuelle (sinon il attend le prochain, jusqu'à 100 ms ou plus)."""
        holder, epoch = self.authority.get(zone, (None, 0))
        t, snap = self.last_enemies.get(zone, (0.0, None))
        if snap and holder not in (None, p.id) and snap["from"] == holder and snap["epoch"] == epoch \
                and time.monotonic() - t < SNAPSHOT_KEEP:
            p.send(snap)

    # --- messages ------------------------------------------------------------
    def check_packs(self) -> None:
        """Prévient si les joueurs connectés n'ont pas la même version du graphic pack."""
        known = [o for o in self.players.values() if o.joueur and o.pack is not None]
        if len({o.pack for o in known}) > 1:
            detail = ", ".join(f"{o.name} v{o.pack}" for o in known)
            log(f"ATTENTION : versions du graphic pack différentes ({detail}). Installez le même pack "
                "partout (tools/install_graphicpack.py) puis relancez le jeu.")

    def check_pause(self) -> None:
        """Pause vivante : le monde vivant est exigé au hello (``refus_pause``) ; les écrans restent des réglages
        libres : prévient (une fois par changement) quand la sauvegarde, les options, le Game Over ou la Ballade
        figent le monde chez certains joueurs seulement (synchro/pause_vivante.py)."""
        figes = tuple(pvmod.ecrans_figes([(o.name, o.pause) for o in self.players.values() if o.joueur]))
        if figes and figes != self.pause_avert_ecrans:
            log("ATTENTION : " + pvmod.avertissement_ecrans(list(figes)))
        self.pause_avert_ecrans = figes

    def refus_pause(self, p: Player, voulu: str) -> bool:
        """Menu vivant obligatoire : refuse (erreur ``pause``, connexion fermée) un client de jeu dont le pack WWMP
        Pause n'est pas chargé, est trop ancien, est réglé sur « Figé », ou qui n'annonce rien. True si refusé."""
        raison = pvmod.refus(p.pause, p.pause_fmt)
        if raison is None:
            return False
        log(f"{voulu} ({p.peer}) refusé : menu vivant obligatoire ({pvmod.texte(p.pause)}, raison « {raison} ») : "
            + pvmod.message_refus(raison))
        p.send({"type": "error", "code": "pause", "raison": raison, "msg": pvmod.message_refus(raison, "fr"),
                "msg_en": pvmod.message_refus(raison, "en")})
        p.writer.close()
        return True

    def nom_unique(self, voulu: str, p: Player, ignorer: set | None = None) -> str:
        """A16 : nom libre parmi les joueurs connectés (« Link », puis « Link 2 », « Link 3 »…),
        24 caractères au plus. Les outils (role rejoindre) ne comptent pas, ni ``ignorer`` (ids :
        ancienne connexion du même client, remplacée)."""
        ignorer = ignorer or set()
        pris = {o.name for o in self.players.values() if o.id != p.id and o.joueur and o.id not in ignorer}
        if voulu not in pris:
            return voulu
        for n in itertools.count(2):
            suffixe = f" {n}"
            cand = voulu[:NAME_MAX - len(suffixe)].rstrip() + suffixe
            if cand not in pris:
                return cand
        return voulu                                       # pragma: no cover

    def handle(self, p: Player, msg: dict) -> None:
        if p.role == robot_mod.ROLE:
            if self.robot_est(p):
                if self.robot.essai and self.robot.latence > 0 and self.robot.pid == p.id:
                    # essai solo : ce qui vient du robot attend latence/2, dans l'ordre (serveur/robot.py Retard)
                    self.robot.du_robot.mettre(lambda: self.robot_message(p, msg) if self.robot_est(p) else None)
                else:
                    self.robot_message(p, msg)    # joueur robot : messages d'une autorité de zone seulement
            return
        if p.role == "remplace":
            return                            # ancienne connexion du robot, en train de fermer
        try:
            if msg.get("type") == "progress" and msg.get("jug") in (1, True):
                # monde persistant + Hanteurs : première progression d'un Hanteur, envoyée POUR LE VERDICT seulement
                # (garde de contenu). Jamais fusionnée dans le monde, quel que soit le rôle vu d'ici.
                self.mp_juger_hanteur(p, msg)
                return
            if self.hanteurs is not None and p.joueur and self.hanteurs.bloque_monde(p.id):
                m2 = hmod.filtre_monde(msg)
                if m2 is None:
                    # potion : le Hanteur qui joue un Link dépense le pot commun du possédé (rubis, flèches,
                    # bombes ; baisses seulement, réglage ``potion_depense``) ; tout le reste reste bloqué
                    cible = self.hanteurs.clone_depense(p.id) if msg.get("type") == "cnt" else None
                    if cible is not None:
                        self.handle_cnt(p, hmod.filtre_inscription(msg), clone=cible)
                    else:
                        self.h_ignores += 1    # Hanteur (ou ancien Hanteur en quarantaine) : n'agit pas sur le monde
                        if msg.get("type") == "progress":
                            # relecture adverse : première progression partie SANS « jug » (le client ne savait
                            # pas encore qu'il était Hanteur : rôle donné ou repris à la reconnexion pendant
                            # l'envoi). Elle était jetée ici et le joueur n'était jamais jugé (accepté sans
                            # jugement au retour en Link). Jugée comme un « jug » : jamais fusionnée
                            self.mp_juger_hanteur(p, msg)
                    return
                msg = m2
            if self.hanteurs is not None and p.joueur and self.hanteurs.ancien_hanteur(p.id):
                msg = hmod.filtre_inscription(msg)   # ce qu'il a ramassé en Hanteur ne passe pas par un full
            if self.mp is not None and p.joueur:
                # monde persistant : attente du verdict (messages gardés), quarantaine (filtrés), image du monde
                for m in self.mp.entree(self, p, msg):
                    self._handle(p, m)
                return
            self._handle(p, msg)
        finally:
            if self.cnt_gel:
                self.cnt_degeler()
            if self.hanteurs is not None:
                self.h_flush()
            if self.courses.course is not None:
                self.courses_tick()              # courses en bateau : phases (aussi courses_boucle)
            if self.bonheur.props:
                self.bonheur_envoyer(self.bonheur.tick(self.bonheur_joueur))   # Fragments : délais, ESSAI accepte
            self.tenues_tick()                   # tenues à pouvoir : partage des duos (au plus 5 fois par seconde)
            self.fusions_tick()                  # fusions (partie B) : débuts et fins (au plus 5 fois par seconde)
            self.porter_tick()                   # porter un coéquipier : départs, salles (au plus 5 fois par seconde)
            self.releve_tick()                   # relever un coéquipier : délais, releveurs proches (5 fois par s)
            self.camouflage_tick()               # camouflage de la tenue jaune : entrées de zone, fins (5 fois par s)

    def mp_juger_hanteur(self, p: Player, msg: dict) -> None:
        """« progress » marqué ``jug`` : un client bloqué par le mode Hanteurs (Hanteur, ou quarantaine de 3 s après
        l'être redevenu Link) donne sa première progression pour que la garde de contenu rende son verdict comme
        pour un Link. Sans cela, il restait « en attente » toute la manche, puis était accepté sans jugement au
        retour en Link (partie solo chargée par erreur : elle serait entrée dans le monde). Rien n'est fusionné."""
        if self.mp is None or not p.joueur or getattr(p, "mp_verdict", "ok") != "attente":
            return
        tot, bits = msg.get("tot"), msg.get("bits")
        if not (self._int(tot) and 0 <= tot < 1 << 20 and isinstance(bits, dict)):
            return
        self.mp.juger(self, p, msg)
        # messages gardés avant de devenir Hanteur (faits de Link) : rejoués, filtrés si quarantaine
        self.mp.verdict(self, p, p.mp_verdict)

    # --- difficulté ---------------------------------------------------------------------
    def dif_links(self) -> int:
        """Link de la PARTIE : joueurs en jeu qui ne sont pas Hanteurs (au moins 1, au plus 8)."""
        h = self.hanteurs
        return dif_mod.compter_links(bool(h is not None and h.est_hanteur(o.id))
                                     for o in self.players.values() if o.joueur)

    def dif_vue(self) -> dict:
        return {**self.difficulte, "links": self.dif_links()}

    def dif_diffuser(self, force: bool = False) -> None:
        """« regles » à tous quand le réglage (``force``) ou le nombre de Link a changé. Le deathlink y est
        TOUJOURS : un client lit ce champ à chaque « regles » (absent, il le couperait)."""
        n = self.dif_links()
        if not force and n == self.dif_links_vus:
            return
        self.dif_links_vus = n
        if not force and self.difficulte["pv_monstres"] == "off":
            return                              # le nombre de Link ne change rien : pas de message
        self.broadcast(self.regles_msg(difficulte=True))

    # --- mode Hanteurs ------------------------------------------------------------------
    def h_joueurs(self) -> None:
        if self.hanteurs is not None:
            self.hanteurs.joueurs({o.id: o.name for o in self.players.values() if o.joueur})

    def h_flush(self) -> None:
        """Messages de la partie Hanteurs envoyés ; autorité réattribuée après un changement de rôle."""
        h = self.hanteurs
        if h is None:
            return
        changes = h.prendre_changes()
        if h.t_diffusion == float("-inf") and h.noms:
            h.diffuser()
        for dest, msg in h.prendre_sortie():
            if isinstance(dest, tuple) and dest[0] == "fx":
                self.h_fx(dest[1], msg, dest[2] if len(dest) > 2 else None)
            elif isinstance(dest, tuple) and dest[0] == "zone":
                for o in self.members(dest[1]):
                    if o.joueur:
                        o.send(msg)
            elif isinstance(dest, tuple) and dest[0] == "autorite":
                # phase 1 (laisse) : à l'autorité de la zone seulement, si elle y est encore (jamais un Hanteur)
                holder, _e = self.authority.get(dest[1], (None, 0))
                o = self.players.get(holder) if holder is not None else None
                if o is not None and o.joueur and o.zone == dest[1] and not h.est_hanteur(o.id):
                    o.send(msg)
            else:
                o = self.players.get(dest)
                if o is not None and o.joueur:
                    o.send(msg)
        if changes:
            zones = {self.players[c].zone for c in changes if c in self.players and self.players[c].zone}
            zones |= {z for z, (holder, _e) in self.authority.items() if holder is not None and h.hors_jeu(holder)}
            for z in sorted(zones):
                self.reassign(z)
            self.dif_diffuser()                 # un changement de rôle change le nombre de Link

    def h_fx(self, zone: str, msg: dict, sauf: int | None = None) -> None:
        """Effet de fumée (« hfx ») : aux joueurs de la salle dont le client sait le jouer (« effets » dans feat),
        hors cinématique, dont le dernier state a moins de ETAT_FRAIS s (pas en chargement). Réglage « hanteurs » :
        aux seuls Hanteurs. Au plus FX_PAR_SALLE par seconde et par salle : le surplus est jeté, jamais gardé pour
        plus tard (un effet en retard ne veut plus rien dire)."""
        h = self.hanteurs
        regle = h.cfg.get("effets", "tous")
        if regle == "coupes":
            return
        now = h.clock()
        t = [x for x in self.h_fx_t.get(zone, []) if now - x < 1.0]
        if len(t) >= hmod.FX_PAR_SALLE:
            self.h_fx_t[zone] = t
            self.h_fx_jetes += 1
            return
        t.append(now)
        self.h_fx_t[zone] = t
        if len(self.h_fx_t) > 64:               # salles quittées : rien à garder
            self.h_fx_t = {z: v for z, v in self.h_fx_t.items() if v and now - v[-1] < 1.0}
        for o in self.members(zone):
            if not o.joueur or o.id == sauf or "effets" not in o.feat or o.busy:
                continue
            if regle == "hanteurs" and not h.est_hanteur(o.id):
                continue
            vu = (h.etat.get(o.id) or {}).get("t")
            if vu is None or now - vu > hmod.ETAT_FRAIS:
                continue
            o.send(msg)
            self.h_fx_envoyes += 1

    def handle_hsummon(self, p: Player, msg: dict) -> None:
        h = self.hanteurs
        if h is None or not p.joueur:
            return
        holder, _epoch = self.authority.get(p.zone, (None, 0)) if p.zone else (None, 0)
        o = self.players.get(holder) if holder is not None else None
        raison = h.invoquer(p.id, msg, (holder if o is not None and o.zone == p.zone else None,
                                        bool(o is not None and o.busy)))
        if raison:
            log(f"{p.name} : invocation refusée ({raison})")

    def handle_henfermer(self, p: Player, msg: dict) -> None:
        """Mode Hanteurs, enfermement (synchro/enfermement.py) : ``henfermer`` d'un Hanteur. Seuls les Link dont le
        client sait être enfermé (« enfermement » dans feat) le sont ; refusé si l'autorité de la salle est en
        cinématique."""
        h = self.hanteurs
        if h is None or not p.joueur:
            return
        holder, _epoch = self.authority.get(p.zone, (None, 0)) if p.zone else (None, 0)
        o = self.players.get(holder) if holder is not None else None

        def peut(pid):
            x = self.players.get(pid)
            return x is not None and x.joueur and enf_mod.FEAT in x.feat
        raison = h.enfermer(p.id, msg, (holder if o is not None and o.zone == p.zone else None,
                                        bool(o is not None and o.zone == p.zone and o.busy)), peut)
        if raison:
            log(f"{p.name} : enfermement refusé ({raison})")

    def handle_hpilote(self, p: Player, msg: dict) -> None:
        """Possession complète : retour de l'AUTORITÉ de la zone (un Link, jamais un Hanteur) au Hanteur qui pilote
        un monstre de cette zone. Tout autre expéditeur est ignoré."""
        h = self.hanteurs
        if h is None or not p.joueur or not p.zone or h.est_hanteur(p.id):
            return
        holder, _epoch = self.authority.get(p.zone, (None, 0))
        if holder != p.id:
            return
        h.retour_pilote(p.zone, msg)

    def handle_possession(self, p: Player, msg: dict) -> None:
        """Mode Hanteurs, phase 1 (laisse) : hposseder, hlacher, hordre, hmanif d'un joueur de jeu."""
        h = self.hanteurs
        if h is None or not p.joueur:
            return
        t = msg.get("type")
        if t == "hposseder":
            holder, _epoch = self.authority.get(p.zone, (None, 0)) if p.zone else (None, 0)
            o = self.players.get(holder) if holder is not None else None
            if o is not None and o.zone == p.zone and "laisse" not in o.feat:
                h.sortie.append((p.id, {"type": "hrefus", "quoi": "possession",
                                        "raison": f"le PC de {o.name} (autorité de ta salle) ne tient pas de monstre "
                                                  "en laisse (outils pas à jour, ou --no-possession)"}))
                return
            raison = h.posseder(p.id, msg, (holder if o is not None and o.zone == p.zone else None,
                                            bool(o is not None and o.busy)))
            if raison:
                log(f"{p.name} : possession refusée ({raison})")
        elif t == "hlacher":
            h.lacher(p.id, "relâché")
        elif t == "hordre":
            h.ordre(p.id, msg)
        elif t == "hmanif":
            h.manifester(p.id, msg.get("on"))

    def handle_influence(self, p: Player, msg: dict) -> None:
        """Mode Hanteurs, phase 4 (synchro/ordres.py) : hinfluence d'un Hanteur, hboss_res d'un Link."""
        h = self.hanteurs
        if h is None or not p.joueur:
            return
        if msg.get("type") == "hinfluence":
            def peut(pid):
                o = self.players.get(pid)
                return o is not None and o.joueur and "influence" in o.feat
            raison = h.influencer(p.id, msg, peut)
            if raison:
                log(f"{p.name} : influence refusée ({raison})")
        else:
            h.influence_res(p.id, msg)

    def handle_potion(self, p: Player, msg: dict) -> None:
        """Mode Hanteurs, phase 2 (synchro/potion.py) : hpotion, hpotion_stop, hresister, pvp, hviser d'un joueur de jeu."""
        h = self.hanteurs
        if h is None or not p.joueur:
            return
        t = msg.get("type")
        if t == "hpotion":
            def a_potion(pid):
                o = self.players.get(pid)
                return o is not None and o.joueur and "potion" in o.feat
            raison = h.boire(p.id, msg, a_potion)
            if raison:
                log(f"{p.name} : potion refusée ({raison})")
        elif t == "hpotion_stop":
            h.arreter(p.id)
        elif t == "hresister":
            h.resister(p.id)
        elif t == "hviser":
            h.viser(p.id, msg)
        elif t == "pvp":
            self.pref_combat(p.zone)
            h.pvp(p.id, msg)

    def relais_clone(self, p: Player, out: dict) -> bool:
        """Phase 2 (potion) : state d'un Hanteur qui joue un Link (relayé à tous sous l'identité du possédé, avec
        son équipement ; au possédé, sous l'identité du Hanteur, marqué « clone ») ou d'un Link possédé (relayé à
        personne : le clone joue son corps). Renvoie True si le relais est fait (ou volontairement omis)."""
        h = self.hanteurs
        if h is None:
            return False
        po = h.potions.get(p.id)
        if po is not None:
            cible = po["cible"]
            spec = (h.etat.get(cible) or {}).get("spec")
            o_c = self.players.get(cible)
            nom = o_c.name if o_c is not None else h.noms.get(cible, "?")
            vu = potion_mod.etat_clone(out, cible, nom, spec, False)
            pour_possede = potion_mod.etat_clone(out, p.id, p.name, spec, True)
            for o in list(self.players.values()):
                if o.id != p.id and o.role == "jeu":
                    o.send(pour_possede if o.id == cible else vu)
            return True
        return h.possede_par(p.id) is not None

    def handle_hanteurs_cmd(self, p: Player, msg: dict) -> None:
        """Commandes de l'hôte (lanceur, outil lobby, PC du serveur seulement) : répartir, rotation, config,
        manche (nouvelle manche des conditions de victoire, synchro/victoire.py)."""
        h = self.hanteurs
        cmd = msg.get("cmd")
        if h is None:
            p.send({"type": "hanteurs_rep", "ok": 0, "cmd": cmd, "msg": "serveur lancé sans --mode-hanteurs"})
            return
        if not self.lanceur_hote(p):
            log(f"{p.name} ({p.peer}) : commande Hanteurs refusée (seul le lanceur de l'hôte peut : PC du serveur, ou hôte avec le mot de passe)")
            p.send({"type": "hanteurs_rep", "ok": 0, "cmd": cmd, "msg": "seul le lanceur de l'hôte peut (PC du serveur, ou hôte avec le mot de passe de partie)"})
            return
        ok = True
        if cmd == "repartir":
            h.repartir(raison="demande de l'hôte")
        elif cmd == "rotation":
            ok = h.rotation(raison="demande de l'hôte")
        elif cmd == "ganon":                       # secours : l'hôte déclare Ganon vaincu (fin « comme dans Crawl »)
            ok = h.fin_ganon(raison="Ganon est vaincu (déclaré par l'hôte)")
        elif cmd == "manche":
            ok = bool(h.repartie and not h.v_finie and h.cfg["victoire"] != "libre")
            if ok:
                h.victoire_debut(raison="demande de l'hôte")
        elif cmd == "config":
            self.hanteurs_config(msg.get("cfg"), "l'hôte")
        else:
            ok = False
        p.send({"type": "hanteurs_rep", "ok": int(ok), "cmd": cmd})

    def hanteurs_config(self, cfg, qui: str) -> None:
        """Réglages du mode Hanteurs changés à chaud (lanceur de l'hôte, ou administrateur du serveur dédié)."""
        h = self.hanteurs
        h.cfg = hmod.valider_config(cfg)
        if not h.cfg["possession"]:
            for hid in list(h.possessions):
                h.lacher(hid, f"possession coupée par {qui}")
        h.potion_config()
        h.t_diffusion = float("-inf")
        self.mp_marquer(rapide=True)       # monde persistant : les réglages (pas la manche) sont gardés
        log(f"Hanteurs : réglages changés par {qui} ({h.cfg})")

    # --- serveur dédié Docker : administration (serveur/admin.py) -----------------------------------------
    def admin_deathlink(self, on: bool) -> None:
        if bool(on) != self.morts.on:
            self.morts.on = bool(on)
            self.mp_marquer(rapide=True)
            log(f"deathlink {'activé' if on else 'désactivé'} par l'administrateur")
            self.broadcast(self.regles_msg())

    def admin_vie_partagee(self, on: bool) -> None:
        if bool(on) != self.vies.on:
            if on and self.hanteurs is not None:
                log("vie partagée : refusée par le serveur (mode Hanteurs)")
                return
            self.vies.on = bool(on)
            self.mp_marquer(rapide=True)
            log(f"vie partagée {'activée' if on else 'désactivée'} par l'administrateur")
            self.broadcast(self.regles_msg())

    def admin_difficulte(self, d: dict) -> None:
        nd = dif_mod.fusionner(self.difficulte, d)
        if nd is not None and nd != self.difficulte:
            self.difficulte = nd
            self.mp_marquer(rapide=True)
            log("difficulté changée par l'administrateur : " + dif_mod.texte(nd, self.dif_links()))
            self.dif_diffuser(force=True)

    def redemarrer(self, raison: str, delai: float = 0.5) -> None:
        """Arrêt propre (monde écrit, « bye » : les clients se reconnectent) ; ``main`` rend la main, et Docker
        (restart: unless-stopped) relance le serveur avec la nouvelle configuration. ``delai`` : la réponse à
        l'administrateur part d'abord."""
        if self.redemarrage:
            return
        self.redemarrage = True
        log(f"REDÉMARRAGE du serveur dans {delai:.1f} s : {raison}")
        t = self.tache_principale
        if t is not None and not t.done():
            asyncio.get_running_loop().call_later(delai, t.cancel)

    def _handle(self, p: Player, msg: dict) -> None:
        t = msg["type"]
        if self.rejoindre:
            self.rejoindre_expire()
        if t == "hello" and msg.get("role") == robot_mod.ROLE:
            self.robot_hello(p, msg)          # joueur robot (serveur/robot.py) : jamais un joueur
            return
        if t == "hello":
            voulu = str(msg.get("name", p.name)).strip()[:NAME_MAX] or p.name
            p.name = voulu
            pack = msg.get("pack")
            p.pack = pack if isinstance(pack, int) and not isinstance(pack, bool) else None
            p.pause = pvmod.normaliser(msg.get("pause"))
            p.pause_fmt = pvmod.normaliser_format(msg.get("pause_fmt"))
            feat = msg.get("feat")
            p.feat = {str(f)[:24] for f in feat[:32]} if isinstance(feat, list) else set()
            p.hote = msg.get("hote") in (1, True)
            if p.hote and not self.acces.hote_permis(p.ip):
                log(f"{p.name} ({p.peer}) se dit hôte depuis Internet sans mot de passe de partie : refusé "
                    "(il reste un joueur normal)")
                p.hote = False
            p.role = "rejoindre" if msg.get("role") == "rejoindre" else "jeu"
            jeton = msg.get("jeton")
            p.jeton = jeton[:32] if isinstance(jeton, str) and jeton else None
            if p.role == "jeu" and self.mp is not None:
                # monde persistant : un client de jeu lancé pour un AUTRE monde (ou sans monde, --monde-strict) est
                # refusé : sa partie entrerait pour toujours dans celui-ci (étude §6.3)
                mid = msg.get("monde")
                mid = mid[:32] if isinstance(mid, str) and mid else None
                if (mid is not None and mid != self.mp.id) or (mid is None and self.mp.strict):
                    log(f"{voulu} ({p.peer}) refusé : partie du monde {mid or '(aucun)'}, ce serveur garde le monde "
                        f"« {self.mp.nom} » ({self.mp.id})")
                    p.send({"type": "error", "code": "monde",
                            "msg": f"ce serveur garde le monde « {self.mp.nom} » : lance-le depuis le lanceur, avec "
                                   "la partie coop de ce monde (une autre partie n'y entre pas)"})
                    p.writer.close()
                    return
                if mid is None:
                    log(f"ATTENTION : {voulu} n'annonce aucun monde (client lancé à la main ou outils anciens) : "
                        f"accepté, sa partie doit être celle du monde « {self.mp.nom} »")
            if p.role == "jeu" and self.refus_pause(p, voulu):
                return                          # menu vivant obligatoire (pack WWMP Pause), avant tout le reste
            if p.role == "jeu":
                refus_rando = self.rando_hello(p, msg)
                if refus_rando:
                    log(f"{voulu} ({p.peer}) refusé : {refus_rando}")
                    err = {"type": "error", "code": "rando", "msg": refus_rando}
                    if getattr(p, "rando_refus_en", ""):
                        err["msg_en"] = p.rando_refus_en      # randomizer par fichiers : refus FR et EN
                    p.send(err)
                    p.writer.close()
                    return
            if p.role == "jeu":
                # A16 (revue) : un client qui se reconnecte avant que le serveur ait vu tomber son
                # ancienne connexion (coupure dans un seul sens) garde son nom : l'ancienne
                # connexion, même jeton, est fermée au lieu de le rebaptiser « Link 2 »
                anciens = {o.id for o in self.players.values()
                           if p.jeton and o.id != p.id and o.joueur and getattr(o, "jeton", None) == p.jeton}
                autres = [o for o in self.players.values() if o.id != p.id and o.joueur and o.id not in anciens]
                if len(autres) >= self.max_players:
                    # partie pleine : refus AVANT de fermer une ancienne connexion (rien ne change pour les autres)
                    log(f"{voulu} ({p.peer}) refusé : partie pleine ({len(autres)}/{self.max_players} joueurs)")
                    p.send({"type": "error", "code": "plein",
                            "msg": f"partie pleine : {self.max_players} joueurs au plus"})
                    p.writer.close()
                    return
                for oid in anciens:
                    o = self.players[oid]
                    log(f"{o.name} (id {o.id}) remplacé par sa nouvelle connexion (id {p.id})")
                    o.writer.close()
                p.name = self.nom_unique(voulu, p, anciens)
                if p.name != voulu:
                    log(f"nom « {voulu} » déjà pris : ce joueur s'appellera « {p.name} »")
                # une couleur de tenue par joueur : jamais deux fois la même ; le premier arrivé garde la sienne,
                # une reconnexion en « automatique » retrouve celle de son ancienne connexion
                # (relecture) : y compris quand le serveur a déjà vu partir l'ancienne connexion (coupure puis
                # retour, client relancé) : la couleur attribuée est retenue par jeton et par nom, sinon le
                # nouveau numéro de joueur donnait une AUTRE couleur au même joueur. Un second hello sur la même
                # connexion ne change rien : une couleur ne bouge pas tant que le joueur est connecté.
                if not p.couleur:
                    demandee = couleurs.normaliser(msg.get("couleur"))
                    cles = ([f"j:{p.jeton}"] if p.jeton else []) + [f"n:{p.name}"]
                    heritee = next((self.players[o].couleur for o in sorted(anciens) if self.players[o].couleur), 0)                         or next((self.couleurs_vues[k] for k in cles if k in self.couleurs_vues), 0)
                    p.couleur = couleurs.attribuer(demandee or heritee, p.id, {o.couleur for o in autres})
                    for k in cles:
                        self.couleurs_vues.pop(k, None)
                        self.couleurs_vues[k] = p.couleur
                    while len(self.couleurs_vues) > COULEURS_VUES_MAX:
                        del self.couleurs_vues[next(iter(self.couleurs_vues))]
                    if demandee and p.couleur != demandee:
                        log(f"couleur « {couleurs.code(demandee)} » déjà prise : {p.name} sera en "
                            f"« {couleurs.code(p.couleur)} »")
            p.hello = True
            outil = msg.get("outil")
            p.outil = outil[:16] if isinstance(outil, str) else ""
            if p.role == "rejoindre":
                # A15 : outil du joueur 2, pas un joueur : ni join, ni autorité, ni état
                log(f"{p.name} (id {p.id}, {p.peer}) connecté : outil {p.outil or 'rejoindre'}, "
                    f"outils {msg.get('client', '?')}")
                # A16 (accord n° 2) : le compagnon du joueur 2, sans découverte UDP, apprend ici si
                # le client de l'hôte est connecté (« Copie de l'hôte » possible)
                hote, _err = self.choisir_hote(p, msg.get("force") in (1, True))
                w = {"type": "welcome", "id": p.id, "version": VERSION, "name": p.name, "outils": CLIENT_VERSION,
                     "players": [], "hote_en_jeu": hote is not None, "epoque": self.epoque}
                if self.mp is not None:
                    # monde persistant : la copie vient du serveur (image du monde), pas d'un hôte en jeu
                    w["monde"] = self.mp.bref()
                    w["hote_en_jeu"] = self.mp.image is not None
                p.send(w)
                return
            log(f"{p.name} (id {p.id}, {p.peer}) connecté : protocole {msg.get('version')}, graphic pack "
                f"{'v' + str(p.pack) if p.pack is not None else 'non lu'}, outils {msg.get('client', '?')}, "
                f"menu : {pvmod.texte(p.pause)}")
            if msg.get("version") != VERSION:
                p.send({"type": "error", "msg": f"version {msg.get('version')} != serveur {VERSION} : "
                        "tous les joueurs doivent utiliser la même version du client"})
                log(f"ATTENTION : {p.name} parle le protocole {msg.get('version')}, ce serveur le {VERSION}. "
                    "Mettez le dépôt à jour des deux côtés.")
            if self.mp is not None:
                self.mp.arrivee(self, p)          # verdict de la garde de contenu, liste des joueurs du monde
            p.send({"type": "welcome", "id": p.id, "version": VERSION, "name": p.name, "outils": CLIENT_VERSION,
                    "couleur": p.couleur,
                    "players": [{"id": o.id, "name": o.name, "pack": o.pack, "couleur": o.couleur, "pause": o.pause}
                                for o in self.players.values() if o.id != p.id and o.joueur],
                    "deathlink": int(self.morts.on),      # synchro v3 : règle de la partie (absent = non)
                    "vie_partagee": int(self.vies.on),    # vie partagée : règle de la partie (absent = non)
                    "coups_joueurs": int(self.coups.on),  # coups entre joueurs : règle de la partie (absent = non)
                    "ralliement": int(self.ralliement.on),  # chant de ralliement : règle de la partie (absent = non)
                    "porter": int(self.porter.on),        # porter un coéquipier : règle de la partie (absent = non)
                    "bonheur": int(self.bonheur.on),      # Fragments du Bonheur : règle de la partie (absent = non)
                    "bonheur_poche": self.bonheur.poche(p.name),   # Fragments du Bonheur : ses moitiés (monde)
                    "tenues": int(self.tenues.on),        # tenues à pouvoir : règle de la partie (absent = non)
                    "releve": int(self.releve.on),        # relever un coéquipier : option (joue avec le deathlink)
                    # camouflage de la tenue jaune : règle, durée, camouflés en ce moment
                    "camouflage": int(self.camouflage.on), "camouflage_duree": self.camouflage.duree,
                    "camoufles": self.camouflage.liste(),
                    "difficulte": self.dif_vue(),         # dégâts reçus, vie des monstres, nombre de Link
                    "epoque": self.mp.epoque(self, p) if self.mp is not None else self.epoque,
                    **({"monde": self.mp.bref()} if self.mp is not None else {}),
                    # chantier E : empreinte de la table (absent = partie sans randomizer) ; la table suit (« rando »)
                    **({"rando": {"emp": self.rando.empreinte, "n": len(self.rando.entrees)}}
                       if self.rando is not None else {}),
                    # trackeur : le serveur accepte le complément « trk » (absent = ne rien envoyer) ; « trk_l » : et
                    # les lieux reçus (lettres, cadeaux) que le client voit dans son jeu
                    **({"trk": 1, "trk_l": 1} if self.trk_actif() else {})})
            self.rando_envoyer(p)
            if self.morts.on and "deathlink" not in p.feat:
                log(f"ATTENTION : {p.name} a des outils sans deathlink : ses morts ne partent pas et celles des "
                    "autres ne le touchent pas ; mettez le dépôt à jour")
            if self.vies.on and "vie_partagee" not in p.feat:
                log(f"ATTENTION : {p.name} a des outils sans la vie partagée : sa vie reste la sienne ; mettez le "
                    "dépôt à jour")
            if self.difficulte != dif_mod.defaut() and "difficulte" not in p.feat:
                log(f"ATTENTION : {p.name} a des outils sans le réglage de difficulté : chez lui les dégâts et la "
                    "vie des monstres restent ceux du jeu ; mettez le dépôt à jour")
            for zone, (holder, epoch) in self.authority.items():
                p.send(self.msg_autorite(zone, holder, epoch))
            self.broadcast({"type": "join", "id": p.id, "name": p.name, "pack": p.pack, "couleur": p.couleur,
                            "pause": p.pause}, exclude=p.id)
            self.check_packs()
            self.check_pause()
            self.dif_diffuser()                 # un Link de plus
            p.ex_hanteur = msg.get("ex_hanteur") in (1, True)   # gardé même sans le mode (image du monde)
            if self.hanteurs is not None and msg.get("ex_hanteur") in (1, True):
                self.hanteurs.anciens.add(p.id)        # a été Hanteur avant une reconnexion : inscriptions filtrées
            if self.hanteurs is not None:
                for oid in sorted(anciens):
                    self.hanteurs.heriter(p.id, oid)   # reconnexion : même rôle (un Hanteur ne revient pas en Link)
            self.h_joueurs()
            if self.hanteurs is not None and "hanteurs" not in p.feat:
                log(f"ATTENTION : {p.name} a des outils sans le mode Hanteurs : en Hanteur, son Link restera visible "
                    "chez lui et il ne pourra pas invoquer ; mettez le dépôt à jour")
        elif t == "state":
            stage, room = msg.get("stage"), msg.get("room")
            zone = zone_of(stage, room) if isinstance(stage, str) and stage and isinstance(room, int) else None
            busy = msg.get("busy") in (1, True)
            pos = msg.get("pos")
            if isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, (int, float)) for v in pos):
                p.pos = pos
                if stage == "sea" and all(math.isfinite(v) and abs(v) <= CARTE_MER_MAX for v in (pos[0], pos[2])):
                    p.mer = (round(float(pos[0])), round(float(pos[2])))    # carte marine partagée (carte_tick)
            if busy != p.busy:
                p.busy = busy
                log(f"{p.name} : {'en cinématique' if busy else 'fin de cinématique'}")
            p.nc = msg.get("nc") in (1, True)    # coups entre joueurs : menu, porté ou porteur chez lui
            p.prtbl = msg.get("prtbl") in (1, True)   # porter : ce Link peut être porté maintenant
            t_state = time.monotonic()
            if p.state_t is None or t_state - p.state_t > autorite.PREF_SILENCE:
                p.frais_depuis = t_state     # S1 : il répond de nouveau (ou pour la première fois)
            p.state_t = t_state
            self.valid.etat(p, msg, zone, busy, self.valid_exempt(p), t_state)   # S2 : vitesse, sauts (journal)
            self.medailles_etat(p, msg, busy)    # médailles : fenêtre du donjon, quarts perdus (sans vie partagée)
            self.set_zone(p, zone)
            if zone and zone in self.deferred:
                self.reassign(zone)          # A11 : passation différée, à retenter
            elif zone and self.pref.actif:
                self.pref_revoir(zone, t_state)   # S1 : reprise par le PC préféré, repli s'il ne répond plus
            out = dict(msg, id=p.id, name=p.name)
            out.pop("clone", None)           # phase 2 : seule la potion (serveur) marque un clone
            h = self.hanteurs
            # « hv » (Hanteur manifesté, vu des autres Hanteurs) : gardé seulement d'un Hanteur qui ne joue pas
            # un Link (potion). Le relais des Hanteurs non manifestés n'est PAS coupé : le client qui filtre retire
            # le fantôme au tick suivant, alors qu'un state qui n'arrive plus le laisserait figé 3 s.
            if "hv" in out:
                if h is not None and p.joueur and h.est_hanteur(p.id) and p.id not in h.potions \
                        and out["hv"] in (1, True):
                    out["hv"] = 1
                else:
                    del out["hv"]
            if h is not None and p.joueur:
                now = time.monotonic()
                # difficulté : le score compte le coup d'origine. Seulement chez un Link qui applique la règle :
                # un client ancien ou lancé avec --no-difficulte prend les dégâts du jeu, rien à diviser
                h.degats_mult = self.difficulte["degats"] if "difficulte" in p.feat else 1
                h.state(p.id, msg, zone, busy, now)
                if now - self.h_tick_t >= 0.25:
                    self.h_tick_t = now
                    h.tick(now)
            if h is not None and p.joueur and self.relais_clone(p, out):
                # phase 2 (potion) : clone ou Link possédé. Le state sous son propre numéro ne part plus : si
                # les autres Hanteurs le voyaient manifesté (hv), un DERNIER state sans hv leur retire le
                # spectre tout de suite (sinon il resterait figé 3 s à côté du clone)
                if p.id in self.hv_vus:
                    self.hv_vus.discard(p.id)
                    for o in list(self.players.values()):
                        if o.id != p.id and o.role == "jeu" and h.est_hanteur(o.id):
                            o.send(out)
            elif h is not None and h.est_hanteur(p.id):
                # un Hanteur est invisible : sa position ne part qu'aux autres Hanteurs
                if "hv" in out:
                    self.hv_vus.add(p.id)
                else:
                    self.hv_vus.discard(p.id)
                for o in list(self.players.values()):
                    if o.id != p.id and o.role == "jeu" and h.est_hanteur(o.id):
                        o.send(out)
            else:
                self.hv_vus.discard(p.id)
                self.broadcast(out, exclude=p.id)
                self.robot_relais(p, out)        # joueur robot de la zone : ses monstres visent ce Link
        elif t == "enemies":
            zone = msg.get("zone")
            if not isinstance(zone, str):
                return
            holder, epoch = self.authority.get(zone, (None, 0))
            if holder != p.id or msg.get("epoch") != epoch or p.zone != zone:
                return  # pas (ou plus) l'autorité : ignoré
            lst = msg.get("list", [])
            out = {"type": "enemies", "from": p.id, "zone": zone, "epoch": epoch,
                   "list": lst if isinstance(lst, list) else []}
            if msg.get("full") in (1, True) and isinstance(lst, list)                     and not (self.hanteurs is not None and self.hanteurs.liste_douteuse(p.id, zone)):
                # liste complète : les autres peuvent supprimer leurs ennemis orphelins. Jamais celle d'un ancien
                # Hanteur dans la salle que son jeu a chargée en Hanteur (synchro/hanteurs.Partie.liste_douteuse)
                out["full"] = 1
            out.update(horodatage(msg))   # lot L2 de la latence : image, heure, aller-retour (optionnels)
            self.last_enemies[zone] = (time.monotonic(), out)
            if self.pref.actif or self.robot.actif:
                self.pref_ennemis(zone, out["list"])     # S1 : monstre blessé ou tué = combat (pas de reprise)
            if self.robot.actif:
                self.robot_snap(zone, p)                 # robot en attente dans la zone : marionnettes à jour
            if self.hanteurs is not None:
                self.hanteurs.snapshot(zone, out["list"], bool(out.get("full")))
            if self.medailles.on and self.medailles_compte(p):
                self.medailles.ennemis(zone, p.name, out["list"])   # médailles : monstres vaincus (dernier coup)
            if self.bonheur.actif():
                self.bonheur_ennemis(zone, p, out["list"])          # Fragments du Bonheur : tirage d'une moitié
            for o in self.members(zone):
                if o.id != p.id:
                    o.send(out)
        elif t == "epos":
            # lot L2 de la latence : positions intermédiaires des ennemis proches, de l'autorité actuelle seulement ;
            # relayées à la zone, rien n'est gardé (le prochain instantané complet fait foi)
            zone = msg.get("zone")
            if not isinstance(zone, str):
                return
            holder, epoch = self.authority.get(zone, (None, 0))
            if holder != p.id or msg.get("epoch") != epoch or p.zone != zone:
                return
            lst = valid_epos(msg.get("list"))
            if not lst:
                return
            out = {"type": "epos", "from": p.id, "zone": zone, "epoch": epoch,
                   "list": [[e["key"], *e["pos"], e["rotY"]] for e in lst], **horodatage(msg)}
            for o in self.members(zone):
                if o.id != p.id:
                    o.send(out)
        elif t == "hit":
            # étape 6 : un joueur non autoritaire a touché une marionnette -> l'autorité décide
            zone = msg.get("zone")
            if not isinstance(zone, str):
                return
            holder, _epoch = self.authority.get(zone, (None, 0))
            # A11 : pendant une passation différée, l'autorité inscrite a peut-être quitté la zone :
            # elle ne trouverait pas l'ennemi ; le coup est jeté ici plutôt que perdu chez elle
            if p.zone == zone:
                self.pref_combat(zone)           # S1 : coup porté dans la zone = combat
            if p.zone == zone and not self.valid.coup(p, zone, msg.get("key"), self.last_enemies.get(zone),
                                                      self.valid_exempt(p)):
                return                           # S2, mode « refus » : coup de trop loin du monstre
            if p.zone == zone and self.medailles.on and self.medailles_compte(p):
                self.medailles.coup(zone, msg.get("key"), p.name)    # médailles : le dernier coup compte (3 s)
            if p.zone == zone and p.joueur:
                self.bonheur.coup(zone, msg.get("key"), p.id)        # Fragments du Bonheur : même attribution
                self.camouflage_envoyer(self.camouflage.coup(p.id, p.name))   # camouflage : frapper le révèle
            if holder is not None and holder != p.id and holder in self.players and p.zone == zone \
                    and self.players[holder].zone == zone:
                # lot L4 (A4) : « inj » (de quoi rejouer le coup chez l'autorité : synchro/coups_monstres.py), validé
                self.players[holder].send({"type": "hit", "from": p.id, "zone": zone,
                                           "key": msg.get("key"), "dmg": int(msg.get("dmg", 1)),
                                           **coups_monstres.champs_hit(msg),
                                           # tenues à pouvoir : épée, gadgets, dos de celui qui frappe (CALL 14)
                                           **self.tenues.champ_hit(p.id),
                                           # fusions (partie B) : x3 de celui qui frappe s'il est fusionné (CALL 14)
                                           **self.fusions.champ_hit(p.id),
                                           # bit 26 : coup d'un projectile, seulement à une autorité qui le rejoue
                                           **coups_projectiles.champ_serveur(msg, self.coups_proj_ok(holder))})
                log(f"{p.name} touche {msg.get('key')} ({zone}) -> autorité {self.players[holder].name}")
        elif t == "progress":
            self.handle_progress(p, msg)
        elif t == "tsw":
            self.handle_tsw(p, msg)
        elif t in ("keys", "key_get", "key_use"):
            self.handle_keys(p, msg)
        elif t == "coffre":
            self.handle_coffre(p, msg)
        elif t == "inv":
            self.handle_inv(p, msg)
        elif t == "cnt":
            self.handle_cnt(p, msg)
        elif t == "pickup":
            self.handle_pickup(p, msg)
        elif t in ("drop", "break"):
            self.handle_ground(p, msg)
        elif t == "magma":
            self.handle_magma(p, msg)
        elif t == "gift":
            self.handle_gift(p, msg)
        elif t == "boss":
            self.handle_boss(p, msg)
        elif t == "bossu":
            self.handle_bossu(p, msg)
        elif t == "bossv":
            self.handle_bossv(p, msg)
        elif t == "bossp":
            self.handle_bossp(p, msg)
        elif t == "bossm":
            self.handle_bossm(p, msg)
        elif t == "player_hit":
            self.handle_player_hit(p, msg)
        elif t == "saisie":
            self.handle_saisie(p, msg)
        elif t in ("mob", "boom", "lift", "tir"):
            self.handle_mobile(p, msg)
        elif t in ("cycle", "rare"):
            self.handle_rare(p, msg)
        elif t in ("monde", "vent", "heure"):
            self.handle_monde(p, msg)
        elif t == "cc":
            self.handle_cc(p, msg)
        elif t == "carry":
            self.handle_carry(p, msg)
        elif t == "herbe":
            self.handle_herbe(p, msg)
        elif t == "pnj":
            self.handle_pnj(p, msg)
        elif t == "mort":
            self.handle_mort(p)
        elif t == "vie":
            self.handle_vie(p, msg)
        elif t == "signal":
            self.handle_signal(p, msg)
        elif t == "regles_cmd":
            self.handle_regles_cmd(p, msg)
        elif t == "hsummon":
            self.handle_hsummon(p, msg)
        elif t == "henfermer":
            self.handle_henfermer(p, msg)
        elif t in ("hposseder", "hlacher", "hordre", "hmanif"):
            self.handle_possession(p, msg)
        elif t == "hpilote":
            self.handle_hpilote(p, msg)
        elif t in ("hinfluence", "hboss_res"):
            self.handle_influence(p, msg)
        elif t == "course_cmd":
            self.handle_course_cmd(p, msg)       # courses en bateau : lancer / annuler (lanceur de l'hôte)
        elif t in ("course_passage", "course_faux", "course_dq", "course_abandon"):
            self.handle_course(p, msg)           # courses en bateau : passages, faux départ, disqualification…
        elif t == "ralliement":
            self.handle_ralliement(p, msg)       # chant de ralliement : un joueur appelle l'équipe
        elif t == "porter":
            self.handle_porter(p, msg)           # porter un coéquipier : prise, o, fin, descente
        elif t == "bonheur":
            self.handle_bonheur(p, msg)          # Fragments du Bonheur : assembler, annuler (menu), ESSAI
        elif t == "tenue":
            self.handle_tenue(p, msg)            # tenues à pouvoir : ESSAI seulement (couleur, faux coéquipier)
        elif t == "fusion":
            self.handle_fusion(p, msg)           # fusions (partie B) : charge, bombe, lancer, fin ; ESSAI
        elif t == "fen":
            self.handle_fen(p, msg)              # vraie fusion : entrées de l'attaquant, relayées tout de suite au pilote
        elif t == "releve":
            self.handle_releve(p, msg)           # relever un coéquipier : à terre, debout, mort, commande ; ESSAI
        elif t == "camouflage":
            self.handle_camouflage(p, msg)       # camouflage de la tenue jaune : fin dite par le client ; ESSAI
        elif t == "ralliement_rep":
            self.handle_ralliement_rep(p, msg)   # chant de ralliement : refus ou arrivée d'un appelé
        elif t == "pvp" and self.hanteurs is None:
            self.handle_coups(p, msg)            # coups entre joueurs (hors mode Hanteurs)
        elif t in ("hpotion", "hpotion_stop", "hresister", "pvp", "hviser"):
            self.handle_potion(p, msg)
        elif t == "hanteurs_cmd":
            self.handle_hanteurs_cmd(p, msg)
        elif t == "monde_cmd":
            self.handle_monde_cmd(p, msg)
        elif t == "ground_req":
            # salle rechargée sans changement de zone (chute, Game Over) : jarres et objets à refaire
            if self.drops_enabled and isinstance(msg.get("zone"), str) and msg["zone"] == p.zone:
                self.ground_push(p, p.zone)
            if isinstance(msg.get("zone"), str) and msg["zone"] == p.zone:
                self.valid.recharge(p)           # S2 : salle rechargée (chute, Game Over) : un saut est légitime
                self.sticks_push(p, p.zone)
                self.mob_push(p, p.zone)
                self.herbe_push(p, p.zone)       # synchro v3 : salle rechargée, herbe repoussée chez lui
                self.salles_recharge(p, p.zone)  # salle rechargée : rejouée, ou oubliée s'il était seul à l'avoir
        elif t == "stick":
            self.handle_stick(p, msg)
        elif t == "rejoindre_req":
            self.rejoindre_req(p, msg)
        elif t == "sauvegarde_hote":
            self.sauvegarde_hote(p, msg)
        elif t == "rejoindre_err":
            self.rejoindre_err_hote(p, msg)
        elif t == "rejoindre_fin":
            self.rejoindre_fin(p, msg)
        elif t == "ping":
            rtt = msg.get("rtt")
            if isinstance(rtt, (int, float)) and not isinstance(rtt, bool) and 0 <= rtt < 60000:
                p.rtt = float(rtt)                     # A17 : affiché dans le lobby du lanceur
                hist = getattr(p, "rtt_hist", None)    # joueur robot : gigue de l'autorité (robot_gain)
                if hist is None:
                    hist = p.rtt_hist = []
                hist.append(p.rtt)
                del hist[:-robot_mod.RTT_HIST]
            p.send({"type": "pong", "t": msg.get("t")})
        elif t == "lobby":
            if p.hello:
                p.send(self.lobby())
        elif t == "triches":
            self.handle_triches(p, msg)
        elif t == "rando_plan":
            if p.hello:
                p.send(self.rando_plan_message())     # randomizer par fichiers : lanceur, avant de lancer le jeu
        elif t == "rando_muet":
            self.handle_rando_muet(p, msg)
        elif t == "rando_plus":
            self.handle_rando_plus(p, msg)
        elif t == "trackeur":
            self.handle_trackeur(p, msg)
        elif t == "trackeur_cmd":
            self.handle_trackeur_cmd(p, msg)
        elif t == "trk":
            self.handle_trk(p, msg)
        elif t in maj_serveur.TYPES:
            maj_serveur.traiter(self.maj, p, msg)

    def lobby(self) -> dict:
        """A17 : liste des joueurs connectés pour le lobby du lanceur (lecture seule ; tout client
        présenté peut la demander, en pratique l'outil rejoindre avec ``outil: lobby``)."""
        hote, _err = self.choisir_hote(Player(0, None), force=True) if self.players else (None, "")
        now = time.monotonic()
        lst = []
        for o in self.players.values():
            if not o.joueur:
                continue
            stage, room = o.where()
            d = {"id": o.id, "name": o.name, "hote": hote is not None and o.id == hote.id,
                 "stage": stage, "room": room, "busy": o.busy, "pack": o.pack, "couleur": o.couleur or None,
                 "pause": o.pause,
                 "rtt": round(o.rtt) if o.rtt is not None else None,
                 "depuis": round(now - o.connected), "silence": round(now - o.last_seen, 1)}
            if o.triches:
                d["triches"] = list(o.triches)     # triches du portage chez lui (absent : aucune ; ancien lanceur : ignoré)
            if self.tenues.partage.get(o.id):
                d["tenue_partage"] = 1             # tenues à pouvoir : partage du duo actif (absent : non)
            if self.hanteurs is not None:
                d["role"] = self.hanteurs.role(o.id)
                d["points"] = int(self.hanteurs.points.get(o.id, 0.0)) if self.hanteurs.est_hanteur(o.id) else None
            lst.append(d)
        lst.sort(key=lambda d: (not d["hote"], d["id"]))
        out = {"type": "lobby", "players": lst, "outils": CLIENT_VERSION, "version": VERSION,
               "deathlink": self.morts.on, "vie_partagee": self.vies.on, "coups_joueurs": self.coups.on,
               "ralliement": self.ralliement.on, "porter": self.porter.on, "bonheur": self.bonheur.on,
               "tenues": self.tenues.on,
               "releve": self.releve.on, "difficulte": self.dif_vue(), "difficulte_on": self.difficulte_on,
               "rando": self.rando_lobby()}
        if self.bonheur.actif() or self.bonheur.poches:
            # Fragments du Bonheur : poches, derniers assemblages, plafond des quarts (trackeur ; ancien : ignoré)
            out["fragments"] = self.bonheur.vue(o.name for o in self.players.values()
                                                if o.joueur and not self.robot_est(o))
        if self.medailles.on:
            out["medailles"] = self.medailles.vue()   # trackeur du lanceur : donjons FINIS seulement (ancien : ignoré)
        if self.courses.on:
            out["courses"] = self.courses.vue()       # courses en bateau : en cours, dernières, records (trackeur)
        if self.mp is not None:
            out["monde"] = self.mp.lobby(self)        # onglet « Mon monde » du lanceur
        if self.hanteurs is not None:
            out["hanteurs"] = self.hanteurs.resume_lobby()
        rv = self.robot_vue()
        if rv is not None:
            # joueur robot : jamais dans « players » ; un résumé à part (ancien lanceur : ignoré)
            out["robot"] = {k: rv[k] for k in ("phase", "zone", "autorite", "connecte")}
            if "robots" in rv:
                out["robot"]["robots"] = [{k: x[k] for k in ("phase", "zone", "autorite", "connecte")}
                                          for x in rv["robots"]]
        return out

    def handle_triches(self, p: Player, msg: dict) -> None:
        """« triches » d'un client de jeu : triches du portage natif actives chez lui (vie infinie, rubis infinis…,
        commun/triches.py). Rien n'est corrigé ni refusé : gardé pour le lobby du lanceur, qui prévient les joueurs, et
        journalisé à chaque changement. Ids inconnus ignorés, liste bornée."""
        if not p.hello or not p.joueur:
            return
        ids = triches_mod.normaliser(msg.get("actives"))
        if ids is None or ids == p.triches:
            return
        p.triches = ids
        log(f"{p.name} : triches du portage " + (", ".join(ids) if ids else "plus aucune"))

    def handle_progress(self, p: Player, msg: dict) -> None:
        """Étape 8 : OU des bits reçus dans l'union du groupe ; seuls les bits nouveaux
        sont rediffusés (idempotent, l'ordre d'arrivée ne compte pas)."""
        group = str(msg.get("group") or "default")[:32]
        bits = from_wire(msg.get("bits"))
        union = self.progress.get(group, {})
        new = bits_sub(bits, union)
        tot = msg.get("tot")
        if self._int(tot) and 0 <= tot < 1 << 20:
            p.mp_tot = tot                    # monde persistant : le plus avancé fournit l'image du monde
        if new:
            union = bits_or(union, new)
            self.progress[group] = union
            p.mp_actif = time.monotonic()
            log(f"progression [{group}] : +{bits_count(new)} bit(s) de {p.name} (union {bits_count(union)})")
            self.mp_marquer(gain=mpm.gain_important(new))
            out = {"type": "progress", "from": p.id, "group": group, "bits": to_wire(new)}
            for o in self.players.values():
                if o.id != p.id and o.group == group:
                    o.send(out)
            for n in med_mod.fins_de_donjon(new):
                self.medailles_fin(n, f"boss vaincu ({p.name})")   # médailles : bit « boss vaincu » posé la 1re fois
        if msg.get("full") or p.group != group:
            p.group = group
            p.send({"type": "progress", "from": None, "group": group, "bits": to_wire(union), "full": True})

    # --- synchro v2 : petites clés partagées ---------------------------------------
    def keys_save(self) -> None:
        self.mp_marquer()                     # monde persistant : les clés sont dans le monde
        if self.keys_file:
            try:
                self.keys.save(self.keys_file)
            except OSError as e:
                log(f"clés : fichier {self.keys_file} non écrit ({e})")

    def handle_keys(self, p: Player, msg: dict) -> None:
        """hello (valeurs annoncées) -> table complète ; key_get / key_use -> ajout idempotent à
        l'ensemble du donjon, diffusion au groupe (accusé seq pour l'auteur) ou « dup » à l'auteur."""
        if not self.keys_enabled:
            return
        group = str(msg.get("group") or "default")[:32]
        p.keys_group = group
        if msg["type"] == "keys":
            if not msg.get("hello"):
                return
            slots = msg.get("slots")
            vals = {}
            for k, v in (list(slots.items())[:16] if isinstance(slots, dict) else []):
                try:
                    vals[int(k)] = v
                except (TypeError, ValueError):
                    continue
            created = self.keys.hello(group, vals)
            # lot 5 : petites clés de coffres étrangers déjà ouverts, en attente d'un donjon enfin annoncé
            self.rando_hors_cles(group)
            tab = {"type": "keys", "from": None, "group": group, "full": 1, "slots": self.keys.table(group)}
            if created:
                log(f"clés [{group}] : valeurs de départ de {p.name} : "
                    + ", ".join(f"donjon {s} -> {t}" for s, t in created))
                self.keys_save()
                # un donjon créé ou remonté (base = max) change la cible des autres membres
                for o in self.players.values():
                    if o.id != p.id and o.keys_group == group:
                        o.send(tab)
            hid = msg.get("hid")
            # réponse à l'auteur : « hid » renvoyé, le client ne converge qu'après la réponse à SON hello
            p.send(dict(tab, hid=hid) if self._int(hid) else tab)
            return
        slot, kid, seq = msg.get("slot"), msg.get("id"), msg.get("seq")
        if not (valid_key_slot(slot) and valid_key_id(kid) and self._int(seq)):
            log(f"clés : fait invalide de {p.name} ignoré ({str(kid)[:24]!r}, donjon {slot!r})")
            return
        now = time.monotonic()
        p.key_times = [t for t in p.key_times if now - t < 1.0]
        if len(p.key_times) >= KEYS_RATE:
            log(f"clés : {p.name} envoie trop de faits (> {KEYS_RATE}/s) : {kid} ignoré")
            return
        p.key_times.append(now)
        kind = "get" if msg["type"] == "key_get" else "use"
        if self.keys.target(group, slot) is None:
            # donjon jamais annoncé (fait parti avant un « keys reset », A15) : compté sur base 0, il
            # empêcherait le hello qui suit de fixer la vraie base (clé perdue). Pas d'accusé : le
            # client le renvoie après son hello (sa valeur annoncée ne le contient pas).
            log(f"clés [{group}] : {kid} de {p.name} avant toute valeur de départ du donjon {slot} : renvoi attendu")
            return
        try:
            new, e = self.keys.fact(group, kind, slot, kid, p.name)
        except ValueError as exc:
            log(f"clés : {kid} de {p.name} refusé ({exc})")
            return
        target, rev = KeyPool.target_of(e), e["rev"]
        if not new:
            first = (e["gets"] if kind == "get" else e["uses"]).get(kid, "?")
            log(f"clés [{group}] donjon {slot} : {p.name} : {kid} déjà compté (par {first}) -> {target}")
            p.send({"type": "keys", "from": None, "group": group, "slot": slot, "target": target, "rev": rev,
                    "why": "dup_" + kind, "id": kid, "by": first, "seq": seq})
            return
        what = "a pris une clé" if kind == "get" else "a payé une porte"
        log(f"clés [{group}] donjon {slot} : {p.name} {what} ({kid}) -> {target}")
        self.keys_save()
        out = {"type": "keys", "from": p.id, "group": group, "slot": slot, "target": target, "rev": rev,
               "why": kind, "id": kid, "by": p.name}
        for o in self.players.values():
            if o.keys_group == group:
                o.send(dict(out, seq=seq) if o is p else out)

    # --- synchro v2 : coffres partagés ---------------------------------------------
    def handle_coffre(self, p: Player, msg: dict) -> None:
        """Premier arrivé par (genre, index, numéro), diffusé aux autres membres du groupe ; doublon
        journalisé seulement ; {"full": 1} -> tout le registre à l'auteur."""
        if not self.coffres_enabled:
            return
        group = str(msg.get("group") or "default")[:32]
        p.coffres_group = group
        reg = self.coffres.setdefault(group, {})
        if msg.get("full") in (1, True):
            p.send({"type": "coffre", "from": None, "group": group, "full": 1, "list": list(reg.values())})
            return
        rec = valid_coffre(msg)
        if rec is None:
            log(f"coffres : rapport invalide de {p.name} ignoré ({str(msg.get('k'))[:4]!r}, {msg.get('i')!r}, "
                f"{msg.get('n')!r}, objet {msg.get('item')!r})")
            return
        now = time.monotonic()
        p.coffre_times = [t for t in p.coffre_times if now - t < 1.0]
        if len(p.coffre_times) >= COFFRES_RATE:
            log(f"coffres : {p.name} envoie trop de rapports (> {COFFRES_RATE}/s) : ignoré")
            return
        p.coffre_times.append(now)
        key = (rec["k"], rec["i"], rec["n"])
        what = (f"coffre {rec['n']} (index {rec['i']}, {rec['stage'] or '?'})" if rec["k"] == "t"
                else f"{rec['k']} {rec['i']}/{rec['n']}")
        origine = self.rando_coffre_d_origine(rec)
        if origine is not None:
            # randomizer complet : coffre resté d'origine chez l'auteur. Enregistré et relayé, il poserait le bit chez
            # tous les autres et l'objet de la table (peut-être de progression) serait perdu pour tout le monde. Le
            # rapport est refusé : les autres gardent leur coffre fermé, avec l'objet de la table.
            vu = (group, key, p.name)
            if vu not in self.rando_coffres_refuses:
                if len(self.rando_coffres_refuses) >= COFFRES_MAX:
                    self.rando_coffres_refuses.clear()
                self.rando_coffres_refuses.add(vu)
                log(f"coffres [{group}] : RANDOMIZER COMPLET, {p.name} a ouvert {what} resté d'origine (objet "
                    f"0x{origine[0]:02X} au lieu de 0x{origine[1]:02X} : coffre chargé avant la table) : rapport "
                    "refusé, les autres joueurs gardent ce coffre fermé avec l'objet de la table")
                p.send({"type": "error", "code": "rando_coffre",
                        "msg": f"randomizer complet : ton {what} contenait encore son objet d'origine (chargé avant la "
                               "table) ; il n'est pas partagé, les autres joueurs y trouveront l'objet prévu. Au "
                               "lancement, attends « table chargée dans le jeu » avant d'entrer dans une salle"})
            return
        if rec["k"] == "t" and self.rando_muet(p.name, rec["i"], rec["n"]):
            # étape 5 : coffre étranger ouvert par CE joueur pendant que le monde lui était fermé (Hanteur, possédé,
            # quarantaine), signalé de nouveau (client relancé, retour en Link) : refusé, aucune clé donnée
            log(f"coffres [{group}] : {p.name} signale {what}, ouvert pendant que le monde lui était fermé : "
                "ignoré (coffre muet)")
            return
        old = reg.get(key)
        if old is not None:
            better = (old.get("item") is None or old.get("prob")) and rec["item"] is not None and not rec.get("prob")
            if not better:
                if old.get("item") is not None and rec["item"] is not None and old["item"] != rec["item"]:
                    log(f"coffres [{group}] : ATTENTION, {p.name} a ouvert {what} avec l'objet 0x{rec['item']:02X}, "
                        f"{old.get('by')} avec 0x{old['item']:02X} (sauvegardes ou couches différentes)")
                elif old.get("by") != p.name:
                    log(f"coffres [{group}] : doublon : {p.name} a aussi ouvert {what} (premier : {old.get('by')})")
                    self.cnt_doublon_coffre(p, group, old, rec, what)
                return
            log(f"coffres [{group}] : objet de {what} complété par {p.name} : 0x{rec['item']:02X}")
        elif len(reg) >= COFFRES_MAX:
            log(f"coffres [{group}] : registre plein ({COFFRES_MAX}) : {what} de {p.name} ignoré")
            return
        rec["by"] = p.name
        reg[key] = rec
        if old is None and rec["k"] == "t" and self.medailles_compte(p):
            self.medailles.coffre(p.name)             # médailles : coffre ouvert (premier rapport)
        self.mp_marquer()
        item = "inconnu" if rec["item"] is None else f"0x{rec['item']:02X}" + (" (probable)" if rec.get("prob") else "")
        log(f"coffres [{group}] : {p.name} a ouvert {what}, objet {item}")
        out = dict(rec, type="coffre", group=group, nom=p.name, **{"from": p.id})
        for o in self.players.values():
            if o.id != p.id and o.joueur and o.coffres_group == group:
                o.send(out)
        if self.rando is not None:
            self.rando_indices_maj()                  # étape 4 : un coffre de plus peut dévoiler un indice
            if rec["k"] == "t" and self.rando_prog_active() is not None \
                    and self.rando.prog_lieu(rec["i"], rec["n"]) is not None:
                self.rando_prog_diffuser(group)       # lot 4 : un lieu progressif de plus pour ce groupe
            if rec["k"] == "t" and self.rando_hors_active() is not None \
                    and (rec["i"], rec["n"]) in self.rando.hors:
                # lot 5 : ce coffre porte un objet de donjon étranger : petite clé au compteur commun du bon donjon,
                # grande clé / carte / boussole annoncée à tous les jeux
                self.rando_hors_cles(group)
                self.rando_hors_diffuser(group)

    # --- synchro v2 : quarts de cœur au sol (cadeaux) ----------------------------------
    def handle_gift(self, p: Player, msg: dict) -> None:
        """Premier arrivé par src, relais à tous les AUTRES membres du groupe (quelle que soit la
        zone) ; {"full": 1} -> registre complet à l'auteur."""
        if not self.cadeaux_enabled:
            return
        group = str(msg.get("group") or "default")[:32]
        p.gift_group = group
        reg = self.gifts.setdefault(group, {})
        if msg.get("full") in (1, True):
            p.send({"type": "gift", "from": None, "group": group, "full": 1, "list": list(reg.values())})
            return
        rec = valid_record(msg)     # quart au sol (« piece: ») ou donné par un personnage (« pnj: », quarts_pnj.py)
        if rec is None:
            log(f"cadeaux : message invalide de {p.name} ignoré (src {str(msg.get('src'))[:24]!r}, objet "
                f"{msg.get('item')!r}, bit {msg.get('bit')!r})")
            return
        now = time.monotonic()
        p.gift_times = [t for t in p.gift_times if now - t < 1.0]
        if len(p.gift_times) >= GIFT_RATE:
            log(f"cadeaux : {p.name} envoie trop de messages (> {GIFT_RATE}/s) : ignoré")
            return
        p.gift_times.append(now)
        old = reg.get(rec["src"])
        if old is not None:
            if old.get("by") != p.name:
                log(f"cadeaux [{group}] : doublon : {p.name} a aussi pris {rec['src']} (premier : {old.get('by')})")
            return
        if len(reg) >= GIFTS_MAX:
            log(f"cadeaux [{group}] : registre plein ({GIFTS_MAX}) : {rec['src']} de {p.name} ignoré")
            return
        rec["by"] = p.name
        reg[rec["src"]] = rec
        self.mp_marquer()
        if "lieu" in rec:
            quoi = "a obtenu un quart de cœur" if rec["quart"] else "a fait le lieu (sans quart)"
            log(f"cadeaux [{group}] : {p.name} {quoi} ({rec['src']}, {rec['stage'] or '?'})")
        else:
            log(f"cadeaux [{group}] : {p.name} a ramassé un quart de cœur ({rec['src']}, {rec['stage'] or '?'})")
        out = dict(rec, type="gift", group=group, nom=p.name, **{"from": p.id})
        for o in self.players.values():
            if o.id != p.id and o.joueur and o.gift_group == group:
                o.send(out)

    # --- synchro v2, module 5 (B1) : PV de boss partagés -----------------------------
    def handle_boss(self, p: Player, msg: dict) -> None:
        """Minimum par (groupe, stage, clé) ; baisse -> autres membres du groupe dans la stage ;
        rapport plus haut -> minimum renvoyé à l'auteur. Seulement pour la stage où est l'auteur."""
        if not self.boss_enabled or not p.joueur:
            return
        group = str(msg.get("group") or "default")[:32]
        rec = valid_boss(msg)
        if rec is None or p.where()[0] != rec["stage"]:
            return
        now = time.monotonic()
        p.boss_times = [t for t in p.boss_times if now - t < 1.0]
        if len(p.boss_times) >= BOSS_RATE:
            return
        p.boss_times.append(now)
        # monde persistant : rien d'une partie en quarantaine (ni en attente du verdict) ne compte, donc son Ganon
        # ne finit pas la partie de tous (relecture de la fusion : « boss » passe le filtre de quarantaine pour la
        # synchro des PV, qui n'est pas gardée ; la fin « comme dans Crawl », elle, touche tout le monde)
        sain = self.mp is None or getattr(p, "mp_verdict", "ok") == "ok"
        if sain and self.hanteurs is not None and self.hanteurs.boss_rapport(p.id, rec):
            self.h_flush()                         # Hanteurs « comme dans Crawl » : Ganon vaincu, fin annoncée
        if p.boss_group != group:
            p.boss_group = group
            self.boss_gc()
        genre, cur = self.boss.report(group, rec, p.name)
        if genre in ("nouveau", "bas"):
            if genre == "bas":
                self.pref_combat(p.zone)
                log(f"boss [{group}] : {rec['key']} ({rec['stage']}) à {rec['hp']}/{rec['max']} PV ({p.name})")
                # boss unique : celui qui vient de la frapper devient la référence (c'est lui qu'elle vise)
                if self.boss_miroir.frappe(group, rec["stage"], rec["key"], p.id, now):
                    log(f"boss [{group}] : {rec['key']} ({rec['stage']}) suit maintenant les attaques du boss de "
                        f"{p.name} (dernier à l'avoir frappé)")
                    out_m = {"type": "bossm", "group": group, "stage": rec["stage"], "key": rec["key"],
                             "ref": p.id, "from": None}
                    for o in self.players.values():
                        if o.joueur and o.boss_group == group and o.where()[0] == rec["stage"]:
                            o.send(out_m)
                vise = self.boss_unique.frappe(group, rec["stage"], rec["key"], p.id, now)
                if vise is not None:
                    log(f"boss unique [{group}] : {rec['key']} ({rec['stage']}) vise maintenant {p.name} "
                        "(dernier à l'avoir frappée)")
                    out_ref = dict(vise, type="bossu", group=group, **{"from": None})
                    for o in self.players.values():
                        if o.joueur and o.boss_group == group and o.where()[0] == rec["stage"]:
                            o.send(out_ref)
            out = dict(cur, type="boss", group=group, **{"from": p.id})
            for o in self.players.values():
                if o.id != p.id and o.joueur and o.boss_group == group and o.where()[0] == rec["stage"]:
                    o.send(out)
        elif genre == "haut":
            p.send(dict(cur, type="boss", group=group, **{"from": None}))
        elif genre == "max":
            log(f"boss [{group}] : {p.name} annonce {rec['key']} avec {rec['max']} PV max : ignoré")

    def handle_bossm(self, p: Player, msg: dict) -> None:
        """Miroir des attaques (synchro/boss_miroir.py) : le serveur tient la référence de chaque boss ; les
        messages de la référence (attaques, battement) sont relayés aux autres membres du groupe dans la stage."""
        if not self.boss_enabled or not p.joueur:
            return
        group = str(msg.get("group") or "default")[:32]
        rec = boss_miroir.valide(msg, strict=False)
        if rec is None or p.where()[0] != rec["stage"]:
            return
        now = time.monotonic()
        p.boss_times = [t for t in p.boss_times if now - t < 1.0]
        if len(p.boss_times) >= BOSS_RATE:
            return
        p.boss_times.append(now)
        if p.boss_group != group:
            p.boss_group = group
            self.boss_gc()
        autres = [o for o in self.players.values()
                  if o.id != p.id and o.joueur and o.boss_group == group and o.where()[0] == rec["stage"]]
        ref, change = self.boss_miroir.rapport(group, rec, p.id, {p.id} | {o.id for o in autres})
        base = {"type": "bossm", "group": group, "stage": rec["stage"], "key": rec["key"], "ref": ref}
        p.send(dict(base, **{"from": None}))                 # l'auteur apprend (ou se voit rappeler) la référence
        if change:
            log(f"boss [{group}] : {rec['key']} ({rec['stage']}) : le boss de {p.name} donne les attaques")
        if ref == p.id and "att" in rec and (self.mp is None or getattr(p, "mp_verdict", "ok") == "ok"):
            out = dict(base, att=rec["att"], nom=p.name, **{"from": p.id})
        elif ref == p.id:
            out = dict(base, nom=p.name, **{"from": p.id})   # battement de la référence
        elif change:
            out = dict(base, **{"from": None})
        else:
            return
        for o in autres:
            o.send(out)

    def handle_bossp(self, p: Player, msg: dict) -> None:
        """Phases communes des boss (synchro/boss_phase.py) : un changement de phase annoncé par un joueur est
        relayé aux autres membres du groupe dans la même stage. Le serveur ne garde rien."""
        if not self.boss_enabled or not p.joueur:
            return
        group = str(msg.get("group") or "default")[:32]
        rec = boss_phase.valide(msg, strict=False)
        if rec is None or p.where()[0] != rec["stage"]:
            return
        now = time.monotonic()
        p.boss_times = [t for t in p.boss_times if now - t < 1.0]
        if len(p.boss_times) >= BOSS_RATE:
            return
        p.boss_times.append(now)
        if self.mp is not None and getattr(p, "mp_verdict", "ok") != "ok":
            return                                 # partie en quarantaine : elle ne change pas le boss des autres
        if p.boss_group != group:
            p.boss_group = group
            self.boss_gc()
        autres = [o for o in self.players.values()
                  if o.id != p.id and o.joueur and o.boss_group == group and o.where()[0] == rec["stage"]]
        if "val" not in rec:
            log(f"boss [{group}] ({rec['stage']}) : {rec['ev']} chez {p.name}, relayé à {len(autres)} joueur(s)")
        out = dict(rec, type="bossp", group=group, nom=p.name, **{"from": p.id})
        for o in autres:
            o.send(out)

    def handle_bossv(self, p: Player, msg: dict) -> None:
        """Ganon spectral à plusieurs (synchro/boss_volee.py) : la volée de chaque joueur de la stage ; tous ont
        renvoyé assez : ordre « tombe » ; un seul a manqué : ordre « rate » ; le tir de la référence part aux autres."""
        if not self.boss_enabled or not p.joueur:
            return
        group = str(msg.get("group") or "default")[:32]
        rec = boss_volee.valide(msg)
        if rec is None or p.where()[0] != rec["stage"]:
            return
        now = time.monotonic()
        p.boss_times = [t for t in p.boss_times if now - t < 1.0]
        if len(p.boss_times) >= BOSS_RATE:
            return
        p.boss_times.append(now)
        if p.boss_group != group:
            p.boss_group = group
            self.boss_gc()
        # la SALLE, pas seulement la stage : chaque salle du dédale de la Tour a son Ganon spectral
        autres = [o for o in self.players.values()
                  if o.id != p.id and o.joueur and o.boss_group == group and o.zone == p.zone]
        t, ordre = self.boss_volee.rapport(group, rec, p.id, {p.id} | {o.id for o in autres}, now, lieu=p.zone)
        out = self.boss_volee.message(group, rec, t, ordre)
        vu = (len(t["joueurs"]), t["ref"])
        tir = "tir" in rec and t["ref"] == p.id and p.id in t["joueurs"]
        p.send(dict(out, **{"from": None}))
        if ordre is not None or tir or t.get("dit") != vu:
            t["dit"] = vu
            if ordre is not None:
                log(f"Ganon spectral [{group}] ({rec['stage']}) : "
                    + ("tout le monde a tenu l'échange, il va rater la boule" if ordre == "tombe"
                       else f"{p.name} a manqué sa boule, volée perdue pour tous"))
            for o in autres:
                if o.id in t["joueurs"]:
                    o.send(dict(out, **({"tir": 1} if tir else {}), **{"from": p.id}))

    def handle_bossu(self, p: Player, msg: dict) -> None:
        """Boss unique (synchro/boss_unique.py) : maximum par (groupe, stage, clé) des rochers tombés et de la mort ;
        l'état commun qui avance -> autres membres du groupe dans la stage ; un rapport en retard -> l'état commun
        renvoyé à l'auteur. Seulement pour la stage où est l'auteur."""
        if not self.boss_enabled or not p.joueur:
            return
        group = str(msg.get("group") or "default")[:32]
        rec = boss_unique.valide(msg)
        if rec is None or p.where()[0] != rec["stage"]:
            return
        now = time.monotonic()
        p.boss_times = [t for t in p.boss_times if now - t < 1.0]
        if len(p.boss_times) >= BOSS_RATE:
            return
        p.boss_times.append(now)
        if self.mp is not None and getattr(p, "mp_verdict", "ok") != "ok":
            return                                 # partie en quarantaine : elle ne tue pas le boss des autres
        if p.boss_group != group:
            p.boss_group = group
            self.boss_gc()
        autres = [o for o in self.players.values()
                  if o.id != p.id and o.joueur and o.boss_group == group and o.where()[0] == rec["stage"]]
        avant = self.boss_unique.reference(group, rec["stage"], rec["key"])
        genre, cur = self.boss_unique.rapport(group, rec, p.name, pid=p.id, presents={p.id} | {o.id for o in autres})
        if genre == "haut":
            self.pref_combat(p.zone)
            ref = self.players.get(cur.get("ref"))
            log(f"boss unique [{group}] : {rec['key']} ({rec['stage']}) : {cur['roc']} rocher(s)"
                + (", VAINCU" if cur["mort"] else "") + f" ({p.name})"
                + (f" ; elle vise {ref.name} (ses attaques suivent celles de sa Gohma)" if ref is not None
                   and cur.get("ref") != avant else ""))
            out = dict(cur, type="bossu", group=group, **{"from": p.id})
            for o in autres:
                o.send(out)
            if cur.get("ref") != avant or rec.get("ref") != cur.get("ref"):
                p.send(dict(cur, type="bossu", group=group, **{"from": None}))     # l'auteur apprend la référence
        elif genre == "retard":
            p.send(dict(cur, type="bossu", group=group, **{"from": None}))
        elif autres and self.boss_unique.reference(group, rec["stage"], rec["key"]) == p.id and "att" not in rec:
            # battement de la référence : son rappel (toutes les 2 s) va aux autres, qui savent ainsi qu'elle est
            # toujours là et gardent leur Gohma sur ses attaques (banc du 2026-11-20 : sans lui, une Gohma qui suit
            # reprenait ses propres attaques 5 s après la dernière attaque de la référence)
            etat0 = self.boss_unique.etat[(group, rec["stage"], rec["key"])]
            out = dict(etat0, type="bossu", group=group, **{"from": p.id})
            if "ang" in rec:
                out["ang"] = rec["ang"]            # son orientation (commune avec le mod natif 0.63.0)
            for o in autres:
                o.send(out)
        # miroir des attaques : l'attaque que commence la Gohma de RÉFÉRENCE part aux autres (un événement, rien
        # n'est gardé) ; celle d'un autre joueur est ignorée
        etat = self.boss_unique.etat.get((group, rec["stage"], rec["key"]))
        if "att" in rec and etat is not None and etat.get("ref") == p.id and autres:
            out = dict(etat, type="bossu", group=group, att=rec["att"], **{"from": p.id})
            if "ang" in rec:
                out["ang"] = rec["ang"]
            for o in autres:
                o.send(out)

    def handle_saisie(self, p: Player, msg: dict) -> None:
        """Synchro v4 (saisie transmise, synchro/saisie.py) : chez l'autorité de la zone, un monstre a « possédé » le
        fantôme d'un joueur (Poe). Mêmes règles que ``player_hit`` : émetteur = autorité actuelle de la zone,
        victime = autre joueur de la même zone, pas en cinématique ; au plus SAISIE_RATE par seconde et par paire.
        Un événement : rien n'est gardé ni rejoué."""
        if not self.ghost_hits_enabled or not self.agit(p):
            return
        rec = valid_saisie(msg)
        if rec is None or p.zone is None or rec["zone"] != p.zone:
            return
        holder, _epoch = self.authority.get(p.zone, (None, 0))
        if holder != p.id:
            return
        v = self.players.get(rec["to"])
        if v is None or v.id == p.id or not v.joueur or v.zone != p.zone or v.busy:
            return
        now = time.monotonic()
        times = [x for x in p.saisie_times.get(v.id, []) if now - x < 1.0]
        if len(times) >= SAISIE_RATE:
            p.saisie_times[v.id] = times
            return
        times.append(now)
        p.saisie_times[v.id] = times
        self.saisies += 1
        self.pref_combat(p.zone)
        v.send(dict(rec, type="saisie", **{"from": p.id}))
        log(f"{p.name} : {rec['key']} possède le fantôme de {v.name} ({rec['zone']}, {rec['frames']} images)")

    def handle_player_hit(self, p: Player, msg: dict) -> None:
        """Synchro v2 (A2, fantôme touchable) : un monstre de la zone a frappé le fantôme d'un joueur chez
        l'autorité. Relais à la victime seulement si l'émetteur EST l'autorité actuelle de la zone (c'est
        chez lui que les monstres réfléchissent ; docs/etudes/mode_hanteurs.md : autorité par zone), que la
        victime est un autre joueur de la même zone et pas en cinématique, au plus PLAYER_HIT_RATE par
        seconde et par paire. Un événement : rien n'est gardé ni rejoué."""
        if not self.ghost_hits_enabled or not self.agit(p):
            return
        rec = valid_player_hit(msg)
        if rec is None or p.zone is None or rec["zone"] != p.zone:
            return
        holder, _epoch = self.authority.get(p.zone, (None, 0))
        if holder != p.id:
            return
        v = self.players.get(rec["to"])
        if v is None or v.id == p.id or not v.joueur or v.zone != p.zone or v.busy:
            return
        if self.hanteurs is not None and self.hanteurs.coup_clone(v.id, rec["dmg"]):
            # phase 2 (potion) : ce fantôme est le clone joué par un Hanteur ; le possédé perdra ces dégâts à la fin
            log(f"{p.name} : {rec['key']} a touché le clone de {v.name} ({rec['dmg']} quart(s), gardé pour la fin)")
            return
        if not self.valid.coup_fantome(p, v, rec):
            return                              # S2, mode « refus » : impact trop loin de la victime
        now = time.monotonic()
        times = [x for x in p.hit_times.get(v.id, []) if now - x < 1.0]
        if len(times) >= PLAYER_HIT_RATE:
            p.hit_times[v.id] = times
            return
        times.append(now)
        p.hit_times[v.id] = times
        self.player_hits += 1
        self.pref_combat(p.zone)                            # S1 : coup reçu dans la zone = combat
        if self.hanteurs is not None:
            self.hanteurs.coup_relaye(v.id, rec["key"])     # mode Hanteurs : quel monstre a blessé (points)
        v.send(dict(rec, type="player_hit", **{"from": p.id}))
        log(f"{p.name} : {rec['key']} a touché le fantôme de {v.name} ({rec['zone']}, {rec['dmg']} quart(s))")

    # --- synchro v3 : mécanismes rares ---------------------------------------------
    def handle_rare(self, p: Player, msg: dict) -> None:
        """cycle : propriétaire de l'horloge du stage seulement, relayé au stage ; rare (pilier) : relayé
        aux autres joueurs de la zone de l'émetteur."""
        if not self.rares_enabled or not self.agit(p) or p.zone is None or msg.get("zone") != p.zone:
            return
        if msg["type"] == "cycle":
            v = valid_cycle(msg)
            stage = zone_stage(p.zone)
            if v is None or v["stage"] != stage:
                return
            present = {o.id for o in self.players.values()
                       if o.joueur and o.zone is not None and zone_stage(o.zone) == stage}
            self.cycle_owners.forget(set(self.players))
            if not self.cycle_owners.accept(stage, p.id, present):
                return
            out = dict(v, type="cycle", zone=p.zone, **{"from": p.id})
            for o in list(self.players.values()):
                if o.id != p.id and o.joueur and o.zone is not None and zone_stage(o.zone) == stage:
                    o.send(out)
            return
        sw = valid_sw_mort(msg)
        if sw is not None:
            # switch de mort d'un Bokoblin / d'une canonnière posé chez lui : gardé pour la salle, jamais relayé
            # (les autres joueurs de la salle le posent eux-mêmes à la mort de leur marionnette)
            if self.salles.add_sw(p.zone, sw["sw"]):
                log(f"salle {p.zone} : switch de mort 0x{sw['sw']:02X} retenu ({p.name})")
                self.salles_marquer()
            return
        v = valid_rare(msg) or valid_canon(msg)      # pilier Hcbh, ou canon Ocanon détruit (synchro/canons.py)
        if v is None:
            return
        out = dict(v, type="rare", zone=p.zone, **{"from": p.id})
        if v["ev"] == "canon":
            log(f"canon {v['key']} ({p.zone}) détruit chez {p.name}")
            if self.salles.add_canon(p.zone, v["key"]):
                self.salles_marquer()
        else:
            log(f"pilier {v['key']} ({p.zone}) cassé chez {p.name}")
        for o in self.members(p.zone):
            if o.id != p.id and o.joueur:
                o.send(out)

    # --- salles retenues : canons détruits et switches de mort (synchro/canons.py, §3) -----------
    def salles_marquer(self) -> None:
        if self.mp is not None:
            self.mp.marquer()

    def salles_gc(self, depart: bool = False) -> None:
        """Oublie une salle que plus aucun joueur « n'a » (il y est, ou c'est la dernière qu'il a quittée dans
        ce stage) ; après une déconnexion, elle est gardée SallesDetruites.GRACE s (reconnexion)."""
        kept = set()
        for o in self.players.values():
            if o.joueur:
                kept.update(z for z in (o.zone, o.zone_prev) if z)
        gone = self.salles.tenus(kept, depart=depart)
        for z in gone:
            log(f"salle {z} : plus aucun joueur ne l'a, canons et ennemis reviendront (comme dans le jeu)")
        if gone:
            self.salles_marquer()

    def salles_recharge(self, p: Player, zone: str) -> None:
        """Salle rechargée chez ``p`` sans changement de zone (Game Over, chute) : le jeu l'a remise à neuf chez lui.
        Si personne d'autre ne l'a, elle est réinitialisée partout : oubliée, comme en solo. Sinon, rejouée."""
        if zone not in self.salles.zones:
            return
        autres = any(o.joueur and o.id != p.id and zone in (o.zone, o.zone_prev) for o in self.players.values())
        if autres:
            self.salles_push(p, zone)
        else:
            del self.salles.zones[zone]
            log(f"salle {zone} rechargée chez {p.name}, seul à l'avoir : canons et ennemis reviennent "
                "(comme dans le jeu)")
            self.salles_marquer()

    def salles_push(self, p: Player, zone: str) -> None:
        """Rappel à qui entre dans la zone (ou la recharge) : canons détruits et switches de mort de la salle."""
        if not self.rares_enabled or not p.joueur:
            return
        snap = self.salles.snap(zone)
        if snap is not None:
            p.send(dict(snap, type="rare", rappel=1, **{"from": None}))

    # --- synchro v4 : position des PNJ qui se déplacent ---------------------------------
    PNJ_RATE = 8        # messages « pnj » relayés au plus par seconde et par joueur

    def handle_pnj(self, p: Player, msg: dict) -> None:
        """pnj : positions des PNJ de la zone. Tout de l'AUTORITÉ de la zone ; d'un autre joueur, ses seules
        entrées « je le porte » (c) / « je lui parle » (t). Relayé aux autres joueurs de la zone, rien n'est
        gardé (voir synchro/pnj.py)."""
        if not self.pnj_enabled or not self.agit(p) or p.zone is None or msg.get("zone") != p.zone:
            return
        lst = valid_pnj(msg)
        if lst is None:
            return
        lst = pnj_filtre(lst, self.authority.get(p.zone, (None, 0))[0] == p.id)
        if not lst:
            return
        now = time.monotonic()
        ts = [t for t in self.pnj_t.get(p.id, ()) if now - t < 1.0]
        if len(ts) >= self.PNJ_RATE:
            self.pnj_t[p.id] = ts
            return
        ts.append(now)
        self.pnj_t[p.id] = ts
        if len(self.pnj_t) > 4 * MAX_PLAYERS:
            self.pnj_t = {k: v for k, v in self.pnj_t.items() if k in self.players}
        out = {"type": "pnj", "zone": p.zone, "from": p.id, "list": lst}
        for o in self.members(p.zone):
            if o.id != p.id and o.joueur:
                o.send(out)

    # --- synchro v3 : herbe coupée et deathlink -----------------------------------------
    def handle_herbe(self, p: Player, msg: dict) -> None:
        """Touffes coupées chez l'émetteur (sa zone seulement) : gardées pour la zone, relayées aux autres."""
        if not self.herbe_enabled or not p.joueur or p.zone is None or msg.get("zone") != p.zone:
            return
        v = valid_herbe(msg)
        if v is None or len(v) > 64:
            return
        new = self.herbe.add(p.zone, v)
        if not new:
            return
        out = {"type": "herbe", "from": p.id, "zone": p.zone, "c": new}
        for o in self.members(p.zone):
            if o.id != p.id and o.joueur:
                o.send(out)

    def herbe_push(self, p: Player, zone: str) -> None:
        """À l'entrée dans une zone (ou salle rechargée) : touffes déjà coupées par les autres."""
        if not self.herbe_enabled or not p.joueur:
            return
        lst = self.herbe.snap(zone)
        for i in range(0, len(lst), 256):
            p.send({"type": "herbe", "from": None, "zone": zone, "c": lst[i:i + 256], "snap": 1})

    def handle_mort(self, p: Player) -> None:
        """Deathlink : une mort d'un Link part à tous les autres Link (une vague ; voir synchro/deathlink.py)."""
        if not p.joueur:
            return
        hanteur = self.hanteurs is not None and self.hanteurs.est_hanteur(p.id)
        if self.medailles_compte(p) and p.medailles_mort_t + 10.0 < time.monotonic():
            p.medailles_mort_t = time.monotonic()
            self.medailles.mort(p.name)                # médailles : Game Over dans le donjon, plus de prudence
        n, raison = self.morts.mort(time.monotonic(), p.connected, hanteur)
        if n is None:
            if self.morts.on:
                log(f"deathlink : mort de {p.name} ignorée ({raison})")
            return
        dest = [o for o in self.players.values() if o.id != p.id and o.joueur
                and not (self.hanteurs is not None and self.hanteurs.est_hanteur(o.id))]
        log(f"deathlink : {p.name} est mort, vague {n} vers {len(dest)} joueur(s)")
        for o in dest:
            o.send({"type": "mort", "n": n, "from": p.id, "name": p.name})
        if self.hanteurs is not None:
            self.hanteurs.victoire_vague([o.id for o in dest])    # victoire « survie » : une vague = UN K.O.

    # --- vie partagée (synchro/vie_partagee.py) -------------------------------------------
    def vie_msg(self, o: Player, group: str, **extra) -> dict:
        st = self.vies.etat(group)
        m = {"type": "vie", "from": None, "group": group, "max": st["max"], "ack": o.vie_ack}
        if st["attente"] is not None:
            m["attente"] = 1                   # fée attendue : aucune valeur imposée
        elif st["v"] is not None:
            m["v"] = st["v"]
        m.update(extra)
        return m

    def vie_diffuser(self, group: str, src: Player | None, **extra) -> None:
        """Valeur commune aux membres du groupe (chacun avec SON accusé), sauf ``src``."""
        for o in self.players.values():
            if o.joueur and o.vie_group == group and (src is None or o.id != src.id):
                o.send(self.vie_msg(o, group, **({"from": src.id, "nom": src.name} if src is not None else {}),
                                    **extra))

    def vie_echeances(self) -> None:
        for g in self.vies.echeance(time.monotonic()):
            log(f"vie partagée [{g}] : aucune fée n'a relevé le groupe : vie commune à 0, tout le monde meurt")
            self.vie_diffuser(g, None, mort=1)

    # --- médailles de fin de donjon (serveur/medailles.py, docs/etudes/autres_zelda.md §2) ------------------------
    def medailles_compte(self, p: Player) -> bool:
        """Un Link qui compte : joueur, jamais un Hanteur (il a son propre score), ni un joueur au monde fermé."""
        if not self.medailles.on or not p.joueur:
            return False
        h = self.hanteurs
        return h is None or not (h.est_hanteur(p.id) or h.bloque_monde(p.id))

    def medailles_etat(self, p: Player, msg: dict, busy: bool) -> None:
        if not self.medailles_compte(p):
            return
        # vie partagée : la vie lue bouge aussi des coups des autres ; seuls les deltas « vie » de CE joueur comptent
        self.medailles.etat(p.name, msg.get("stage"), msg.get("life"), msg.get("max_life"), busy,
                            compter_degats=not self.vies.on)

    def medailles_fin(self, n: int, raison: str) -> dict | None:
        """Fin du donjon ``n`` : classement calculé, envoyé à tous les Link (bandeau), gardé (trackeur, monde)."""
        res = self.medailles.fin(n)
        if res is not None:
            self.medailles_diffuser(res, raison)
        return res

    def medailles_diffuser(self, res: dict, raison: str) -> None:
        lignes = med_mod.lignes_bandeau(res)
        out = {"type": "medailles", "donjon": res["donjon"], "nom": res["nom"], "lignes": [[t, c] for t, c in lignes],
               "or": res["or"]}
        k = 0
        for o in list(self.players.values()):
            if o.joueur and not (self.hanteurs is not None and self.hanteurs.est_hanteur(o.id)):
                o.send(out)
                k += 1
        log(f"médailles : {res['nom']} fini ({raison}) : " + " | ".join(t for t, _c in lignes[1:])
            + f" ; envoyé à {k} joueur(s)")
        self.mp_marquer(rapide=True)

    def coups_joueur(self, pid) -> dict | None:
        """Ce que les coups entre joueurs savent d'un joueur (synchro/coups_joueurs.CoupsServeur)."""
        o = self.players.get(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if o is None:
            return None
        return {"joueur": o.joueur, "zone": o.zone, "busy": o.busy, "nc": o.nc, "pos": o.pos, "t": o.state_t,
                "feat": o.feat}

    def handle_coups(self, p: Player, msg: dict) -> None:
        """« pvp » hors mode Hanteurs : coup d'épée d'un Link sur un coéquipier (règle de la partie). Accepté :
        « coup_joueur » à la victime (recul par CALL 9, aucun dégât)."""
        if not p.joueur:
            return
        now = time.monotonic()
        raison, dest, out = self.coups.pvp(p.id, msg, self.coups_joueur, now, hanteurs=self.hanteurs is not None,
                                           robot_essai=bool(self.robot.essai))
        if raison:
            if self.coups.on and raison != "trop de coups" and "invincible" not in raison:
                log(f"coups entre joueurs : {p.name} -> {msg.get('to')} refusé ({raison})")
            return
        o = self.players.get(dest)
        if o is None:
            return
        self.pref_combat(p.zone)
        o.send(out)
        log(f"coups entre joueurs : {p.name} frappe {o.name} : accepté, recul envoyé")

    def ralliement_joueur(self, pid) -> dict | None:
        """Ce que le chant de ralliement sait d'un joueur (serveur/ralliement.RalliementServeur)."""
        o = self.players.get(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if o is None:
            return None
        return {"nom": o.name, "joueur": o.joueur and not self.robot_est(o), "zone": o.zone, "busy": o.busy,
                "nc": o.nc, "pos": o.pos, "t": o.state_t, "feat": o.feat, "role": None, "possede": False}

    # --- porter un coéquipier, lot P3 (serveur/porter.py, docs/etudes/porter_coequipier.md §5) -------------------------
    def porter_joueur(self, pid) -> dict | None:
        """Ce que le port de coéquipier sait d'un joueur."""
        o = self.players.get(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if o is None:
            return None
        h = self.hanteurs
        return {"nom": o.name, "joueur": bool(o.joueur), "robot": self.robot_est(o),
                "hanteur": bool(h is not None and h.est_hanteur(o.id)), "zone": o.zone, "busy": bool(o.busy),
                "prtbl": bool(getattr(o, "prtbl", False)), "pos": o.pos, "t": o.state_t, "feat": o.feat}

    def porter_envoyer(self, envois) -> None:
        for pid, m in envois:
            o = self.players.get(pid)
            if o is not None and por_commun.FEAT in o.feat:
                o.send(m)
        for ligne in self.porter.journal:
            log(ligne)
        self.porter.journal.clear()

    def porter_tick(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._porter_t < 0.2:
            return
        self._porter_t = now
        self.porter.hanteurs = self.hanteurs is not None
        if self.porter.ports:
            self.porter_envoyer(self.porter.tick(self.porter_joueur, now))

    def handle_porter(self, p: Player, msg: dict) -> None:
        """« porter » d'un client : prise (porteur), o, fin (porteur), descend (porté)."""
        if not p.joueur or self.robot_est(p):
            return
        self.porter.hanteurs = self.hanteurs is not None
        self.porter_envoyer(self.porter.message(p.id, msg, self.porter_joueur))

    def handle_ralliement(self, p: Player, msg: dict) -> None:
        """« ralliement » : le morceau 6 de la Baguette joué (mod natif 0.45.0). Accepté : « ralliement_appel » à chaque
        appelé (déplacé par son client après 10 s sans refus) et « ralliement_rep » à l'appelant ; sinon la raison."""
        if not p.joueur or self.robot_est(p):
            return
        now = time.monotonic()
        raison, rep, envois = self.ralliement.chant(p.id, msg, self.ralliement_joueur, lambda: list(self.players),
                                                    now, hanteurs=self.hanteurs is not None,
                                                    cache=bool(self.cache.parties),
                                                    course=self.courses.en_cours())
        quoi = p.name
        if raison:
            log(f"chant de ralliement : {quoi} refusé ({raison})")
            p.send({"type": "ralliement_rep", "ok": 0, "raison": raison[:120]})
            return
        for dest, m in envois:
            o = self.players.get(dest)
            if o is not None:
                o.send(m)
        p.send(rep)
        noms = [self.players[d].name for d, _m in envois if d in self.players]
        log(f"chant de ralliement : {quoi} appelle "
            + (", ".join(noms) if noms else "personne")
            + (f" ; exclus : {rep['exclus']}" if rep.get("exclus") else ""))

    def handle_ralliement_rep(self, p: Player, msg: dict) -> None:
        """« ralliement_rep » d'un appelé : refus (10 s) ou arrivée ; l'appelant est prévenu (« ralliement_info »)."""
        if not p.joueur:
            return
        raison, out = self.ralliement.reponse(p.id, msg, p.name, time.monotonic())
        quoi = "refuse" if msg.get("refus") in (1, True) else "arrive"
        if raison:
            log(f"chant de ralliement : {p.name} {quoi} : ignoré ({raison})")
            return
        log(f"chant de ralliement : {p.name} {quoi}")
        if out is not None:
            o = self.players.get(out[0])
            if o is not None:
                o.send(out[1])

    # --- Fragments du Bonheur (serveur/bonheur.py, docs/etudes/autres_zelda.md §4) ---------------------------------
    def bonheur_joueur(self, pid) -> dict | None:
        """Ce que les Fragments du Bonheur savent d'un joueur (serveur/bonheur.BonheurServeur)."""
        o = self.players.get(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if o is None:
            return None
        h = self.hanteurs
        return {"nom": o.name, "joueur": o.joueur and not self.robot_est(o), "zone": o.zone, "busy": o.busy,
                "pos": o.pos, "t": o.state_t, "feat": o.feat,
                "hanteur": bool(h is not None and (h.est_hanteur(o.id) or h.possede_par(o.id) is not None))}

    def bonheur_envoyer(self, envois) -> None:
        for dest, m in envois or ():
            o = self.players.get(dest)
            if o is not None:
                o.send(m)
        for ligne in self.bonheur.prendre_journal():
            log(ligne)
        if self.bonheur.changes:
            self.bonheur.changes = False
            self.mp_marquer()                         # monde persistant : poches, quarts donnés

    def bonheur_ennemis(self, zone: str, p: Player, lst) -> None:
        """Instantané « enemies » de l'autorité ``p`` : un monstre vaincu peut donner une moitié (1 sur 12)."""
        h = self.hanteurs
        # monstres possédés de la zone (mode Hanteurs : les Fragments y sont déjà coupés ; garde de principe)
        poss = [ps.get("key") for ps in (getattr(h, "possessions", None) or {}).values()
                if ps.get("zone") == zone] if h is not None else ()
        self.bonheur_envoyer(self.bonheur.ennemis(zone, p.id, lst, self.bonheur_joueur, possedes=poss))

    def handle_bonheur(self, p: Player, msg: dict) -> None:
        """« bonheur » d'un client : ``assembler`` (proposer ou accepter), ``annule`` (menu ouvert) ; ESSAI
        (--essai-bonheur seulement) : ``essai_propose`` (le faux coéquipier propose), ``essai_forcer`` (récompense)."""
        if not p.joueur or self.robot_est(p):
            return
        ev, bh, now = msg.get("ev"), self.bonheur, time.monotonic()
        raison, envois = "", []
        if ev == "assembler":
            raison, envois = bh.assembler(p.id, msg, self.bonheur_joueur, lambda: list(self.players), now,
                                          hanteurs=self.hanteurs is not None)
        elif ev == "annule":
            envois = bh.annuler(p.id, "menu ouvert" if msg.get("raison") == "menu" else "annulé")
        elif ev == "essai_propose" and bh.essai:
            raison, envois = bh.essai_proposer(p.id, msg, self.bonheur_joueur, now)
        elif ev == "essai_forcer" and bh.essai:
            raison, envois = bh.essai_forcer(p.id, msg, self.bonheur_joueur, now)
        else:
            return
        if raison:
            log(f"fragments du Bonheur : {p.name} ({ev}) refusé ({raison})")
            envois = list(envois) + [(p.id, {"type": "bonheur", "ev": "refus", "raison": raison[:120]})]
        self.bonheur_envoyer(envois)

    # --- tenues à pouvoir (serveur/tenues.py, docs/etudes/autres_zelda.md §6) ----------------------------------------
    def tenues_joueur(self, pid) -> dict | None:
        """Ce que les tenues à pouvoir savent d'un joueur (serveur/tenues.TenuesServeur)."""
        o = self.players.get(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if o is None:
            return None
        h = self.hanteurs
        return {"nom": o.name, "couleur": o.couleur, "joueur": bool(o.joueur), "robot": self.robot_est(o),
                "hanteur": bool(h is not None and h.est_hanteur(o.id)), "zone": o.zone, "pos": o.pos,
                "t": o.state_t, "feat": o.feat, "leaving": bool(getattr(o, "leaving", False))}

    def tenues_tick(self, force: bool = False) -> None:
        """Tenue effective de chaque joueur (partage des duos, hystérésis) ; messages « tenue » aux clients qui savent
        les appliquer (feat « tenues »), au plus 5 fois par seconde."""
        now = time.monotonic()
        if not force and now - self._tenues_t < 0.2:
            return
        self._tenues_t = now
        tn = self.tenues
        if not tn.actif() and not tn.envoye:
            return
        ids = [pid for pid, o in self.players.items() if o.joueur]
        for pid, m in tn.tick(ids, self.tenues_joueur, now):
            o = self.players.get(pid)
            if o is not None and ten_commun.FEAT in o.feat:
                o.send(m)
        for ligne in tn.journal:
            log(ligne)
        tn.journal.clear()

    def handle_tenue(self, p: Player, msg: dict) -> None:
        """« tenue » d'un client : ESSAI seulement (``essai_couleur``, ``essai_faux``) ; refus dit au client."""
        if not p.joueur or self.robot_est(p):
            return
        raison = self.tenues.essai_message(p.id, msg, self.tenues_joueur)
        if raison:
            p.send({"type": "tenue_rep", "ok": 0, "raison": raison[:120]})
            return
        self.tenues_tick(force=True)

    # --- relever un coéquipier (serveur/releve.py, docs/etudes/autres_zelda.md §5) ----------------------------------
    def releve_joueur(self, pid) -> dict | None:
        """Ce que le relevé sait d'un joueur (serveur/releve.ReleveServeur)."""
        o = self.players.get(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if o is None:
            return None
        h = self.hanteurs
        return {"nom": o.name, "joueur": o.joueur and not self.robot_est(o), "zone": o.zone, "pos": o.pos,
                "t": o.state_t, "feat": o.feat, "hanteur": bool(h is not None and h.est_hanteur(o.id))}

    def releve_actif(self) -> bool:
        return self.releve.actif(self.morts.on, self.vies.on, self.hanteurs is not None)

    def releve_envoyer(self, envois) -> None:
        for dest, m in envois:
            if dest == "tous":
                src = m.get("id")
                for o in self.players.values():
                    if o.joueur and o.id != src and rel_mod.FEAT in o.feat:
                        o.send(m)
                continue
            o = self.players.get(dest)
            if o is not None:
                o.send(m)
        for ligne in self.releve.journal:
            log(ligne)
        self.releve.journal.clear()

    def releve_tick(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._releve_t < 0.2:
            return
        self._releve_t = now
        rv = self.releve
        # ESSAI (--essai-relever) : le serveur d'essai suit aussi sans deathlink (le client d'essai force la règle chez
        # lui seulement ; un client sans l'essai garde sa règle effective et ignore « possible »)
        if not self.releve_actif() and not (rv.essai and self.hanteurs is None):
            if rv.terre or rv.faux or rv.possibles:
                self.releve_envoyer(rv.couper())
            return
        ids = [pid for pid, o in self.players.items() if o.joueur]
        self.releve_envoyer(rv.tick(lambda: ids, self.releve_joueur, now))

    def handle_releve(self, p: Player, msg: dict) -> None:
        """« releve » d'un client : à terre, debout, mort, vivant, commande « Relever » ; ESSAI (mode, faux coéquipier)."""
        if not p.joueur or self.robot_est(p):
            return
        essai = self.releve.essai and msg.get("essai") in (1, True) and self.hanteurs is None
        if not self.releve_actif() and not essai:
            if msg.get("e") == "terre":
                # règle coupée entre-temps : sa vraie mort (le client coupe aussi cfg ; le mod le tue de lui-même)
                p.send({"type": "releve_ordre", "e": "abandon", "raison": "règle coupée"})
            return
        self.releve_envoyer(self.releve.message(p.id, msg, self.releve_joueur, time.monotonic()))
        self.releve_tick(force=True)

    # --- fusions des tenues, partie B (serveur/fusions.py, docs/etudes/autres_zelda.md §6) ----------------------------
    def fusions_joueur(self, pid) -> dict | None:
        """Ce que les fusions savent d'un joueur : celui des tenues, plus cinématique, menu ou port (nc), course, et la
        couleur de POUVOIR (ESSAI des tenues compris)."""
        i = self.tenues_joueur(pid)
        if i is None:
            return None
        o = self.players.get(pid)
        i["couleur"] = self.tenues.couleur_de(pid, i)
        i["busy"], i["nc"] = bool(o.busy), bool(o.nc)
        i["course"] = self.courses.coureur(pid) is not None
        return i

    def fusions_envoyer(self, envois) -> None:
        for pid, m in envois:
            o = self.players.get(pid)
            if o is not None and fus_commun.FEAT in o.feat:
                o.send(m)
        for ligne in self.fusions.journal:
            log(ligne)
        self.fusions.journal.clear()

    def fusions_autorite(self, zone):
        return self.authority.get(zone, (None, 0))[0]

    def fusions_tick(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._fusions_t < 0.2:
            return
        self._fusions_t = now
        fs = self.fusions
        hanteurs = self.hanteurs is not None
        if hanteurs and not fs.hanteurs:
            fs.hanteurs = True
            self.fusions_envoyer(fs.couper())
        fs.hanteurs = hanteurs
        if not fs.fusions and not fs.etats:
            return
        ids = [pid for pid, o in self.players.items() if o.joueur]
        self.fusions_envoyer(fs.tick(ids, self.fusions_joueur, self.fusions_autorite, now))

    def handle_fen(self, p: Player, msg: dict) -> None:
        """VRAIE fusion : un état des entrées de l'attaquant, relayé TOUT DE SUITE au pilote de sa fusion (chemin le plus
        court : ni tick, ni journal) ; rien d'autre n'en est fait."""
        if not p.joueur:
            return
        for pid, m in self.fusions.entrees(p.id, msg):
            o = self.players.get(pid)
            if o is not None and fus_commun.FEAT in o.feat:
                o.send(m)

    def handle_fusion(self, p: Player, msg: dict) -> None:
        """« fusion » d'un client : état (charge, bombe), lancer, fin ; ESSAI (faux coéquipier, boutons)."""
        if not p.joueur or self.robot_est(p):
            return
        self.fusions.hanteurs = self.hanteurs is not None
        self.fusions_envoyer(self.fusions.message(p.id, msg, self.fusions_joueur, self.fusions_autorite))

    # --- camouflage de la tenue jaune (serveur/camouflage.py, docs/etudes/autres_zelda.md §6) ---------------------------
    def camouflage_joueur(self, pid) -> dict | None:
        """Ce que le camouflage sait d'un joueur : celui des tenues, avec la couleur de POUVOIR (ESSAI des tenues
        compris)."""
        i = self.tenues_joueur(pid)
        if i is None:
            return None
        i["couleur"] = self.tenues.couleur_de(pid, i)
        return i

    def camouflage_envoyer(self, envois) -> None:
        """« camouflage » au joueur concerné ; « camoufles » (liste) à tous les joueurs."""
        for dest, m in envois:
            if dest == "tous":
                for o in self.players.values():
                    if o.joueur:
                        o.send(m)
                continue
            o = self.players.get(dest)
            if o is not None and cam_commun.FEAT in o.feat:
                o.send(m)
        for ligne in self.camouflage.journal:
            log(ligne)
        self.camouflage.journal.clear()

    def camouflage_tick(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._camouflage_t < 0.2:
            return
        self._camouflage_t = now
        ids = [pid for pid, o in self.players.items() if o.joueur]
        self.camouflage_envoyer(self.camouflage.tick(ids, self.camouflage_joueur, self.tenues.actif(), now))

    def handle_camouflage(self, p: Player, msg: dict) -> None:
        """« camouflage » d'un client : fin (expiré, révélé chez lui) ; ESSAI (traité en jaune, camouflage demandé)."""
        if not p.joueur or self.robot_est(p):
            return
        self.camouflage_envoyer(self.camouflage.message(p.id, msg, self.camouflage_joueur, self.tenues.actif()))

    # --- courses en bateau (serveur/courses.py, docs/etudes/autres_zelda.md §3) ----------------------------------------
    def courses_joueur(self, pid) -> dict | None:
        """Ce que les courses savent d'un joueur (serveur/courses.CoursesServeur)."""
        o = self.players.get(pid) if isinstance(pid, int) and not isinstance(pid, bool) else None
        if o is None:
            return None
        return {"nom": o.name, "joueur": o.joueur and not self.robot_est(o), "zone": o.zone, "pos": o.pos,
                "t": o.state_t, "feat": o.feat}

    def courses_envoyer(self) -> None:
        """Messages et journal des courses : à un joueur, ou à tous les joueurs de jeu (None)."""
        cs = self.courses
        for dest, m in cs.prendre_envois():
            if dest is None:
                for o in list(self.players.values()):
                    if o.joueur and not self.robot_est(o):
                        o.send(m)
            else:
                o = self.players.get(dest)
                if o is not None:
                    o.send(m)
        for ligne in cs.prendre_journal():
            log(ligne)
        if cs.records_changes:
            cs.records_changes = False
            self.mp_marquer()                         # monde persistant : un record de plus à garder

    def courses_tick(self) -> None:
        self.courses.tick(self.courses_joueur, time.monotonic())
        self.courses_envoyer()

    def handle_course_cmd(self, p: Player, msg: dict) -> None:
        """« course_cmd » du lanceur de l'hôte (comme « regles_cmd ») : {action: "lancer", parcours} ou "annuler"."""
        if not self.lanceur_hote(p):
            log(f"{p.name} ({p.peer}) : course refusée (seul le lanceur de l'hôte peut)")
            p.send({"type": "course_rep", "ok": 0, "raison": "seul le lanceur de l'hôte peut lancer une course"})
            return
        action = msg.get("action")
        if action == "annuler":
            ok = self.courses.annuler("annulée par l'hôte")
            p.send({"type": "course_rep", "ok": int(ok), "raison": "" if ok else "pas de course en cours"})
        elif action == "lancer":
            cand = [o.id for o in self.players.values() if o.joueur and not self.robot_est(o)
                    and crs_mod.FEAT in (o.feat or ())]
            raison = self.courses.lancer(msg.get("parcours"), "l'hôte", cand, time.monotonic(),
                                         hanteurs=self.hanteurs is not None, cache=bool(self.cache.parties))
            if raison:
                log(f"course refusée : {raison}")
            p.send({"type": "course_rep", "ok": 0 if raison else 1, "raison": raison[:120]})
        else:
            p.send({"type": "course_rep", "ok": 0, "raison": "action inconnue"})
        self.courses_envoyer()

    def handle_course(self, p: Player, msg: dict) -> None:
        """Messages d'un coureur : ``course_passage``, ``course_faux``, ``course_dq``, ``course_abandon``."""
        if not p.joueur or self.robot_est(p):
            return
        cs, t, now = self.courses, msg.get("type"), time.monotonic()
        if t == "course_passage":
            raison = cs.passage(p.id, msg, p.rtt, now)
            if raison and raison not in ("déjà passée", "pas de course", "autre course"):
                log(f"course : passage de {p.name} refusé ({raison})")
        elif t == "course_faux":
            cs.faux_depart(p.id, msg, now)
        elif t == "course_dq":
            cs.disqualifier(p.id, msg, now)
        elif t == "course_abandon":
            cs.abandon(p.id, msg, now)
        self.courses_envoyer()

    def carte_tick(self) -> int:
        """Carte marine partagée (client/carte_marine.py) : la dernière position en mer de chaque joueur, envoyée à
        tous les joueurs (toutes les CARTE_PAS s, dès qu'ils sont deux). Un Hanteur est invisible des Link : il n'y
        figure pas. Renvoie le nombre de destinataires."""
        h = self.hanteurs
        joueurs = [p for p in self.players.values() if p.joueur]
        if len(joueurs) < 2:
            return 0
        liste = [[p.id, p.mer[0], p.mer[1]] for p in joueurs
                 if p.mer is not None and not (h is not None and h.est_hanteur(p.id))
                 and self.robot_de(p) is None]          # le robot du serveur n'est pas un joueur à retrouver
        if not liste:
            return 0
        out = {"type": "carte", "j": liste}
        for p in joueurs:
            p.send(out)
        return len(joueurs)

    def handle_signal(self, p: Player, msg: dict) -> None:
        """Signal « viens ici » (client/reperes.py) : relayé avec « from » à tous les autres joueurs, au plus un toutes
        les SIGNAL_ENTRE s par joueur. Un Hanteur est invisible des Link : son signal ne part qu'aux autres Hanteurs."""
        if not p.joueur:
            return
        v = valid_signal(msg)
        now = time.monotonic()
        if v is None or now - p.signal_t < SIGNAL_ENTRE:
            return
        p.signal_t = now
        out = dict(v, type="signal", **{"from": p.id})
        h = self.hanteurs
        hanteur = h is not None and h.est_hanteur(p.id)
        n = 0
        for o in list(self.players.values()):
            if o.id == p.id or not o.joueur or (hanteur and not h.est_hanteur(o.id)):
                continue
            o.send(out)
            n += 1
        if "case" in v:
            log(f"case {v['case']} marquée sur la carte marine par {p.name}, relayée à {n} joueur(s)")
        else:
            log(f"signal « viens ici » de {p.name} ({v['stage']}, salle {v['room']}) relayé à {n} joueur(s)")

    def handle_vie(self, p: Player, msg: dict) -> None:
        """Vie commune par groupe : deltas appliqués dans l'ordre d'arrivée, bornés à [0, vie max du groupe],
        idempotents par identifiant d'événement ; hello : inscription (le serveur fait foi pour un groupe connu) ;
        sync : valeur renvoyée. Un Hanteur (mode coupé de toute façon) ou le joueur robot : jamais."""
        if not self.vies.on or not p.joueur:
            return
        if self.hanteurs is not None and (self.hanteurs.est_hanteur(p.id) or self.hanteurs.bloque_monde(p.id)):
            return
        self.vie_echeances()
        v = valid_vie(msg)
        if v is None:
            log(f"vie partagée : message invalide de {p.name} ignoré (seq {str(msg.get('seq'))[:12]!r})")
            return
        now = time.monotonic()
        p.vie_times = [t for t in p.vie_times if now - t < 1.0]
        delta = not v["hello"] and not v["sync"]
        if len(p.vie_times) >= CNT_RATE and not v["hello"]:
            log(f"vie partagée : {p.name} envoie trop de messages (> {CNT_RATE}/s) : ignoré")
            if delta and (p.vie_trou is None or v["seq"] < p.vie_trou):
                p.vie_trou = v["seq"]          # comme les compteurs : rien d'autre accusé avant son renvoi
            return
        p.vie_times.append(now)
        group = v["group"]
        p.vie_group = group
        if v["hello"]:
            p.vie_ack = max(p.vie_ack, v["ack"])
            p.vie_trou = None
            changed = self.vies.hello(group, v["v"], v["max"], self.prio_hote(p))
            st = self.vies.etat(group)
            log(f"vie partagée [{group}] : {p.name} inscrit ({v['v']}/{v['max']} quarts) ; commune "
                f"{st['v']}/{st['max']}")
            p.send(self.vie_msg(p, group, snap=1))
            if changed:
                self.mp_marquer()
                self.vie_diffuser(group, p)
            return
        if v["sync"]:
            p.send(self.vie_msg(p, group, snap=1))
            return
        if p.vie_trou is not None:
            if v["seq"] > p.vie_trou:
                return                         # après un trou : ni compté ni accusé (renvoyé dans l'ordre)
            p.vie_trou = None
        p.vie_ack = max(p.vie_ack, v["seq"])
        res = self.vies.delta(group, v["ev"], v["d"], v["max"], v["fee"], v["fl"], v["zero"], now)
        if res is None:
            p.send(self.vie_msg(p, group))     # déjà compté (renvoi après une coupure)
            return
        if v["d"] < 0 and self.medailles_compte(p):
            self.medailles.degats(p.name, -v["d"])     # médailles, vie partagée : SES quarts perdus (deltas propres)
        if res["raison"]:
            log(f"vie partagée [{group}] : {p.name} : {v['d']:+d} ignoré ({res['raison']})")
        self.mp_marquer()
        extra = {"mort": 1} if res["mort"] else {}
        if res["mort"]:
            log(f"vie partagée [{group}] : {p.name} : {v['d']:+d} : vie commune à 0, tout le monde meurt")
        elif res["attente"] and res["app"]:
            log(f"vie partagée [{group}] : {p.name} tombe à 0 avec une fée en flacon : attente de sa fée")
        elif v["fee"] and res["app"]:
            log(f"vie partagée [{group}] : la fée de {p.name} relève tout le groupe ({res['v']} quarts)")
        p.send(self.vie_msg(p, group, **extra))
        if res["app"] or res["mort"] or res["attente"]:
            self.vie_diffuser(group, p, d=res["app"], **extra)

    def lanceur_hote(self, p: Player) -> bool:
        """Outil lobby du lanceur de l'hôte, seul autorisé à changer la partie (« regles_cmd », « hanteurs_cmd ») :
        - sur le PC du serveur (adresse locale de ce PC), toujours ;
        - Internet, serveur dédié : le lanceur de l'hôte est ailleurs. Il est accepté si le serveur a un mot de
          passe de partie (connexion authentifiée par le défi HMAC, commun/acces.py) ET si son hello se déclare
          hôte (« hote », case « Je suis l'hôte de la partie »). Qui connaît le mot de passe peut donc changer le
          deathlink : c'est voulu, le mot de passe ne se donne qu'aux joueurs de la partie. Sans mot de passe,
          jamais à distance (même d'un réseau local : n'importe quel lanceur pourrait se dire hôte)."""
        if not p.hello or p.role != "rejoindre" or p.outil != "lobby":
            return False
        return p.ip in self._addrs_locales() or (self.acces.actif and p.auth and p.hote)

    def handle_regles_cmd(self, p: Player, msg: dict) -> None:
        """Réglage de la partie changé à chaud par le lanceur de l'hôte (PC du serveur, ou hôte authentifié par le
        mot de passe sur un serveur dédié : voir lanceur_hote)."""
        if not self.lanceur_hote(p):
            log(f"{p.name} ({p.peer}) : réglage de la partie refusé (seul le lanceur de l'hôte peut : PC du serveur, ou hôte avec le mot de passe)")
            p.send({"type": "regles_rep", "ok": 0, "msg": "seul le lanceur de l'hôte peut (PC du serveur, ou hôte avec le mot de passe de partie)"})
            return
        if "deathlink" in msg:
            on = msg.get("deathlink") in (1, True)
            if on != self.morts.on:
                self.morts.on = on
                self.mp_marquer(rapide=True)
                log(f"deathlink {'activé' if on else 'désactivé'} par l'hôte")
                self.broadcast(self.regles_msg())
        if "vie_partagee" in msg:
            on = msg.get("vie_partagee") in (1, True)
            if on and self.hanteurs is not None:
                p.send({"type": "regles_rep", "ok": 0, **self.regles_rep(),
                        "msg": "vie partagée impossible avec le mode Hanteurs (ses règles de vie restent les siennes)"})
                return
            if on != self.vies.on:
                self.vies.on = on
                self.mp_marquer(rapide=True)
                log(f"vie partagée {'activée' if on else 'désactivée'} par l'hôte")
                self.broadcast(self.regles_msg())
        if "ralliement" in msg:
            on = msg.get("ralliement") in (1, True)
            if on and self.hanteurs is not None:
                p.send({"type": "regles_rep", "ok": 0, **self.regles_rep(),
                        "msg": "chant de ralliement impossible avec le mode Hanteurs"})
                return
            if on != self.ralliement.on:
                self.ralliement.on = on
                self.ralliement.vu = True
                log(f"chant de ralliement {'activé' if on else 'désactivé'} par l'hôte")
                self.broadcast(self.regles_msg())
        if "porter" in msg:
            on = msg.get("porter") in (1, True)
            if on and self.hanteurs is not None:
                p.send({"type": "regles_rep", "ok": 0, **self.regles_rep(),
                        "msg": "port de coéquipier impossible avec le mode Hanteurs"})
                return
            if on != self.porter.on:
                self.porter.on = on
                self.porter.vu = True
                log(f"porter un coéquipier {'activé' if on else 'désactivé'} par l'hôte")
                self.broadcast(self.regles_msg())
                self.porter_tick(force=True)
        if "releve" in msg:
            on = msg.get("releve") in (1, True)
            if on != self.releve.on:
                self.releve.on = on
                self.releve.vu = True
                log(f"relever un coéquipier {'activé' if on else 'désactivé'} par l'hôte"
                    + (" (ne joue qu'avec le deathlink, sans la vie partagée)" if on else ""))
                self.broadcast(self.regles_msg())
        if "camouflage" in msg:
            on = msg.get("camouflage") in (1, True)
            if on != self.camouflage.on:
                self.camouflage.on = on
                self.camouflage.vu = True
                if not on:
                    self.camouflage_envoyer(self.camouflage.couper())
                log(f"camouflage de la tenue jaune {'activé' if on else 'désactivé'} par l'hôte"
                    + (" (joue avec les tenues à pouvoir)" if on else ""))
                self.broadcast(self.regles_msg())
        if "bonheur" in msg:
            on = msg.get("bonheur") in (1, True)
            if on and self.hanteurs is not None:
                p.send({"type": "regles_rep", "ok": 0, **self.regles_rep(),
                        "msg": "Fragments du Bonheur impossibles avec le mode Hanteurs"})
                return
            if on != self.bonheur.on:
                self.bonheur.on = on
                self.bonheur.vu = True
                log(f"fragments du Bonheur {'activés' if on else 'désactivés'} par l'hôte")
                self.broadcast(self.regles_msg())
        if "tenues" in msg:
            on = msg.get("tenues") in (1, True)
            if on != self.tenues.on:
                self.tenues.on = on
                self.tenues.vu = True
                self.tenues.reemettre()
                self.fusions.on = on            # fusions (partie B) : même règle
                if not on:
                    self.fusions_envoyer(self.fusions.couper())
                log(f"tenues à pouvoir {'activées' if on else 'désactivées'} par l'hôte")
                self.broadcast(self.regles_msg())
        if "coups_joueurs" in msg:
            on = msg.get("coups_joueurs") in (1, True)
            if on and self.hanteurs is not None:
                p.send({"type": "regles_rep", "ok": 0, **self.regles_rep(),
                        "msg": "coups entre joueurs impossibles avec le mode Hanteurs (son PvP reste le sien)"})
                return
            if on != self.coups.on:
                self.coups.on = on
                self.coups.vu = self.coups.vu or on
                log(f"coups entre joueurs {'activés' if on else 'désactivés'} par l'hôte (recul seul, aucun dégât)")
                self.broadcast(self.regles_msg())
        if "difficulte" in msg:
            if not self.difficulte_on:
                p.send({"type": "regles_rep", "ok": 0, **self.regles_rep(),
                        "msg": "ce serveur tourne avec --no-difficulte : le réglage de difficulté est coupé"})
                return
            d = dif_mod.fusionner(self.difficulte, msg.get("difficulte"))
            if d is None:
                log(f"{p.name} : réglage de difficulté refusé (valeur inconnue : {str(msg.get('difficulte'))[:80]})")
                p.send({"type": "regles_rep", "ok": 0, **self.regles_rep(),
                        "msg": "réglage de difficulté inconnu (dégâts : 1 à 4 ; vie des monstres : off, demi, link)"})
                return
            if d != self.difficulte:
                self.difficulte = d
                self.mp_marquer(rapide=True)
                log("difficulté changée par l'hôte : " + dif_mod.texte(d, self.dif_links()))
                self.dif_diffuser(force=True)
        p.send({"type": "regles_rep", "ok": 1, **self.regles_rep()})

    def regles_rep(self) -> dict:
        return {"deathlink": int(self.morts.on), "vie_partagee": int(self.vies.on),
                "coups_joueurs": int(self.coups.on), "ralliement": int(self.ralliement.on),
                "porter": int(self.porter.on), "bonheur": int(self.bonheur.on), "tenues": int(self.tenues.on), "releve": int(self.releve.on),
                "camouflage": int(self.camouflage.on), "difficulte": self.dif_vue()}

    def regles_msg(self, difficulte: bool = False) -> dict:
        """« regles » : le deathlink TOUJOURS (un client lit ce champ à chaque « regles »), la vie partagée dès qu'elle
        a été allumée une fois dans ce processus (absente, un client la garde telle quelle : message d'avant sinon)."""
        m = {"type": "regles", "deathlink": int(self.morts.on)}
        if self.vies.vu:
            m["vie_partagee"] = int(self.vies.on)
        if self.ralliement.vu:
            m["ralliement"] = int(self.ralliement.on)  # chant de ralliement, dès qu'il a changé une fois (welcome sinon)
        if self.porter.vu:
            m["porter"] = int(self.porter.on)          # porter un coéquipier, dès qu'il a changé une fois (welcome sinon)
        if self.bonheur.vu:
            m["bonheur"] = int(self.bonheur.on)        # Fragments du Bonheur, dès qu'ils ont changé (welcome sinon)
        if self.tenues.vu:
            m["tenues"] = int(self.tenues.on)          # tenues à pouvoir, dès qu'elles ont changé (welcome sinon)
        if self.releve.vu:
            m["releve"] = int(self.releve.on)          # relever un coéquipier, dès qu'il a changé (welcome sinon)
        if self.camouflage.vu:
            m["camouflage"] = int(self.camouflage.on)  # camouflage de la tenue jaune, dès qu'il a changé (welcome sinon)
        if self.coups.vu:
            m["coups_joueurs"] = int(self.coups.on)   # coups entre joueurs, dès qu'ils ont été allumés une fois
        if difficulte:
            m["difficulte"] = self.dif_vue()
        return m

    def handle_monde_cmd(self, p: Player, msg: dict) -> None:
        """Monde persistant : commande de l'administrateur du monde (lanceur de l'hôte : PC du serveur, ou mot de
        passe + case hôte ; voir lanceur_hote) : libérer un joueur de la quarantaine, reprendre l'image d'un joueur,
        renommer, écrire maintenant."""
        if self.mp is None:
            p.send({"type": "monde_rep", "ok": 0, "msg": "ce serveur ne garde pas de monde persistant"})
            return
        if not self.lanceur_hote(p):
            log(f"{p.name} ({p.peer}) : commande du monde refusée (seul l'administrateur : PC du serveur, ou mot de passe "
                "et case hôte)")
            p.send({"type": "monde_rep", "ok": 0, "msg": "seul l'administrateur du monde peut (PC du serveur, ou mot "
                                                         "de passe de partie et case « je suis l'hôte »)"})
            return
        rep = self.mp.commande(self, p, msg)
        p.send(dict(rep, type="monde_rep", cmd=str(msg.get("cmd"))[:16]))

    def monde_envoyer(self) -> None:
        """Horloge commune (synchro/monde.py) : réponses et diffusions en attente ; les joueurs figés par leur nom."""
        for dest, m in self.monde.prendre_envois():
            figes = [self.players[i].name if i in self.players else str(i) for i in m.get("figes", [])]
            m = dict(m, figes=figes)
            for pid in dest:
                o = self.players.get(pid)
                if o is not None and o.joueur:
                    o.send(m)

    def monde_depart(self, p: Player) -> None:
        """Un joueur part : il n'arrête plus l'heure des autres (diffusion si l'horloge repart)."""
        if not self.monde_enabled:
            return
        avant = {g: c["run"] for g, c in self.monde.horloges.items()}
        self.monde.forget(set(self.players))
        for g, c in self.monde.horloges.items():
            if avant.get(g) is False and c["run"]:
                log(f"heure : {p.name} parti, l'heure commune repart (groupe {g})")
        self.monde_envoyer()

    # --- cache-cache des Killer Bees à plusieurs (synchro/cachecache.py) ------------------
    def cc_envoyer(self, sorties) -> None:
        for dest, m in sorties:
            for pid in dest:
                o = self.players.get(pid)
                if o is not None and o.joueur:
                    o.send(m)

    def handle_cc(self, p: Player, msg: dict) -> None:
        """« cc » : partie du groupe (lance, attrape, fin, etat) ; relais à tout le groupe (inscrit par son premier
        « cc »). Rien n'est gardé au-delà de la partie en cours ; un client sans --share-cachecache n'envoie rien."""
        if not p.joueur:
            return
        avant = self.cache.parties.get(str(msg.get("group") or "default")[:32])
        sorties = self.cache.message(p.id, p.name, msg)
        apres = self.cache.parties.get(str(msg.get("group") or "default")[:32])
        if apres is not None and apres is not avant:
            log(f"cache-cache : {p.name} a lancé la partie des Killer Bees (n° {apres['gen']})")
        elif avant is not None and apres is None:
            log(f"cache-cache : partie n° {avant['gen']} de {avant['nom']} finie ({msg.get('raison')})")
        elif sorties and msg.get("ev") == "attrape":
            log(f"cache-cache : {p.name} a attrapé un enfant ({bin(apres['bits'] if apres else 0).count('1')}/4)")
        self.cc_envoyer(sorties)

    def monde_vivant_tick(self) -> None:
        """--monde-vivant : l'heure commune avance sans joueur ; le monde persistant en garde la dernière valeur (elle
        est écrite avec le reste : à l'arrêt du serveur, ou à la prochaine écriture)."""
        for groupe in list(self.monde.horloges):
            if self.monde.horloge_maj(groupe):
                self.monde._diffuser(groupe, self.monde.clock())
        self.monde_envoyer()
        c = self.monde.horloges.get("default") or next(iter(self.monde.horloges.values()), None)
        if c is not None and self.mp is not None and not self.monde.seen:
            self.mp.noter_heure(c["t"], c["d"], "le serveur")

    # --- synchro v3 : monde vivant ----------------------------------------------------
    def handle_monde(self, p: Player, msg: dict) -> None:
        """monde : relayé au groupe s'il vient de la référence ; vent : registre du groupe (voir synchro/monde.py)."""
        if not self.monde_enabled or not p.joueur:
            return
        self.monde.forget(set(self.players))
        groupe = str(msg.get("group") or "default")[:32]
        if msg["type"] == "monde":
            # horloge commune : une horloge neuve part de l'heure gardée par le monde persistant
            h = self.mp.heure if self.mp is not None else None
            avant = self.monde.horloges.get(groupe, {}).get("run")
            out, dest = self.monde.monde(p.id, self.prio_hote(p), msg, init=h,
                                         robot=p.id in self.robot_ids())
            reply = None
            c = self.monde.horloges.get(groupe)
            if c is not None:
                if avant is not None and avant != c["run"]:
                    noms = ", ".join(self.players[i].name for i in c["figes"] if i in self.players)
                    log("heure : l'heure commune repart" if c["run"] else
                        f"heure : l'heure commune s'arrête ({noms or 'personne en jeu'})")
                if self.mp is not None:
                    self.mp.noter_heure(c["t"], c["d"], p.name)        # monde persistant : l'heure commune
        elif msg["type"] == "heure":
            # Chant du temps qui passe : l'horloge commune saute en avant, tout le groupe la reçoit
            seq = self.monde.horloges.get(groupe, {}).get("seq")
            out, dest = self.monde.heure(p.id, msg)
            reply = None
            if self.monde.horloges.get(groupe, {}).get("seq") != seq:
                log(f"heure : saut de {p.name} (Chant du temps qui passe) repris par l'heure commune")
        else:
            interdit, remettre = self.courses.requiem(p.id) if msg.get("init") is not True else (False, False)
            if interdit:
                # courses en bateau : Requiem interdit à un coureur ; rien n'est relayé, et le vent du groupe lui est
                # renvoyé avec un numéro plus grand (son client le reprend : synchro/monde.py garde le plus grand)
                reg = self.monde.vent.get(groupe)
                if reg is not None and remettre:
                    reg["seq"] += 1
                    p.send(self.monde._out(reg))
                self.courses_envoyer()
                return
            out, dest, reply = self.monde.vent_msg(p.id, self.prio_hote(p), msg)
            if out is not None:
                self.mp_marquer()
                log(f"vent : direction de {p.name} gardée pour le groupe ({out['x']}, {out['y']}, "
                    f"{'réglé' if out['on'] else 'jamais réglé'}, seq {out['seq']})")
        if reply is not None:
            p.send(reply)
        self.monde_envoyer()
        if out is None:
            return
        for pid in dest:
            o = self.players.get(pid)
            if o is not None and o.joueur:
                o.send(out)

    # --- synchro v2, lot 3 : objets mobiles -----------------------------------------
    def handle_mobile(self, p: Player, msg: dict) -> None:
        """mob (blocs : verrou, dernière case ; bombes-fleurs), boom (débit), lift (autorité seulement) :
        relais aux autres joueurs de la zone de l'émetteur (voir le docstring du module)."""
        if not self.mobiles_enabled or not self.agit(p) or p.zone is None or msg.get("zone") != p.zone:
            return
        zone, t, now = p.zone, msg["type"], time.monotonic()
        others = [o for o in self.members(zone) if o.id != p.id and o.joueur]
        if t == "boom":
            v = valid_boom(msg)
            if v is None or not self.mob.boom_ok(p.id, now):
                return
            out = dict(v, type="boom", zone=zone, **{"from": p.id})
        elif t == "tir":
            # boulet du canon du bateau (synchro/boulets.py) : relais à la zone, débit borné comme les explosions
            v = valid_tir(msg)
            if v is None or not self.mob.tir_ok(p.id, now):
                return
            out = dict(v, type="tir", zone=zone, **{"from": p.id})
        elif t == "lift":
            if self.authority.get(zone, (None, 0))[0] != p.id:
                return                       # seule l'autorité de la zone fait foi pour les plateformes
            lst = valid_lift(msg)
            if lst is None:
                return
            out = {"type": "lift", "zone": zone, "from": p.id, "list": lst}
        else:
            v = valid_mob(msg)
            if v is None:
                return
            key = v["key"]

            def alive(pid):
                return pid in self.players
            if v["ev"] == "push":
                ok, holder, known = self.mob.push(zone, key, p.id, v["at"], now, alive)
                if not ok:
                    p.send({"type": "mob", "from": None, "zone": zone, "ev": "deny", "key": key, "holder": holder})
                    # case connue renvoyée seulement si la case de départ est périmée : verrou tenu par un
                    # autre, elle est d'AVANT sa glissade (le perdant reculerait) ; sa poussée et sa case
                    # finale, relayées, recalent le perdant
                    if known is not None and holder is None:
                        p.send({"type": "mob", "from": None, "zone": zone, "ev": "cell", "key": key,
                                "cell": known["cell"], "y": known["y"]})
                    who = f"tenu par {self.players[holder].name}" if holder in self.players \
                        else "case de départ périmée"
                    log(f"bloc {key} ({zone}) : poussée de {p.name} refusée ({who})")
                    return
            elif v["ev"] == "cell":
                if not self.mob.cell(zone, key, p.id, v["cell"], v["y"], now, alive):
                    return
            out = dict(v, type="mob", zone=zone, **{"from": p.id})
        for o in others:
            o.send(out)

    def handle_carry(self, p: Player, msg: dict) -> None:
        """carry (objets portés) : arbitrage du porteur, relais aux autres joueurs de la zone de l'émetteur."""
        if not self.carry_enabled or not p.joueur or p.zone is None or msg.get("zone") != p.zone:
            return
        v = valid_carry(msg)
        if v is None:
            return
        zone, now, key = p.zone, time.monotonic(), v["key"]

        def alive(pid):
            o = self.players.get(pid)
            return o is not None and o.zone == zone
        if v["ev"] == "grab":
            ok, holder = self.carry.grab(zone, key, p.id, now, alive)
            if not ok:
                if v.get("cause") != "rappel":
                    who = self.players[holder].name if holder in self.players else "?"
                    log(f"objet porté {key} ({zone}) : prise de {p.name} refusée (tenu par {who})")
                p.send({"type": "carry", "from": None, "zone": zone, "ev": "deny", "key": key, "holder": holder})
                return
        elif not self.carry.release(zone, key, p.id, now, alive):
            return
        out = dict(v, type="carry", zone=zone, **{"from": p.id})
        for o in self.members(zone):
            if o.id != p.id and o.joueur:
                o.send(out)

    def mob_push(self, p: Player, zone: str) -> None:
        """« snap » des cases de blocs connues de la zone, à qui y entre (ou redemande l'état)."""
        if not self.mobiles_enabled:
            return
        lst = self.mob.snap(zone)
        if lst:
            p.send({"type": "mob", "from": None, "zone": zone, "ev": "snap", "list": lst})

    def boss_gc(self) -> None:
        """Oublie les PV d'un boss quand plus aucun membre du groupe n'est dans sa stage."""
        if not self.boss.hp and not self.boss_unique.etat and not self.boss_volee.etat and not self.boss_miroir.ref:
            return
        present = {(o.boss_group, o.where()[0]) for o in self.players.values() if o.boss_group is not None}
        self.boss_unique.gc(present)
        self.boss_miroir.gc(present)
        self.boss_volee.gc(present | {(o.boss_group, o.zone) for o in self.players.values()
                                      if o.boss_group is not None and o.zone is not None})
        for g, stage, key in self.boss.gc(present):
            log(f"boss [{g}] : {key} ({stage}) oublié (plus personne dans la stage)")

    # --- synchro v2 : dalles de magma figé ------------------------------------------
    def handle_magma(self, p: Player, msg: dict) -> None:
        """Relais aux autres joueurs de la zone de l'émetteur, mémoire MAGMA_KEEP s."""
        if not self.drops_enabled:
            return
        zone, lst = msg.get("zone"), msg.get("list")
        if not isinstance(zone, str) or not isinstance(lst, list) or p.zone is None or zone != p.zone:
            return
        entries = [e for e in (valid_magma(x) for x in lst[:MagmaWatch.MAX_PER_MSG]) if e]
        if not entries:
            return
        now = time.monotonic()
        kept = [m for m in self.magmas.get(zone, []) if now - m[0] < MAGMA_KEEP]
        self.magmas[zone] = (kept + [(now, p.id, p.name, e) for e in entries])[-MAGMA_MAX:]
        log(f"magma : {len(entries)} dalle(s) née(s) chez {p.name} ({zone})")
        out = {"type": "magma", "from": p.id, "zone": zone, "list": entries}
        for o in self.members(zone):
            if o.id != p.id:
                o.send(out)

    # --- synchro v2 : inventaire partagé ------------------------------------------
    @staticmethod
    def inv_vide() -> dict:
        return {"acq": [], "bits": {}, "cap": {}, "fl": 0, "sac": None, "hote": False}

    @staticmethod
    def inv_snap(st: dict) -> dict:
        out = {"acq": list(st["acq"]), "bits": [[o, m] for o, m in sorted(st["bits"].items())],
               "cap": dict(st["cap"]), "fl": st["fl"]}
        if st["sac"] is not None:             # sac pas encore connu (aucun full) : jamais « vide » imposé
            out["sac"] = list(st["sac"])
        return out

    def handle_inv(self, p: Player, msg: dict) -> None:
        """Union / OU / max / sac répliqué par groupe (§3.4, sans compteurs) ; ajouts aux autres membres,
        accusé à l'auteur ; full -> snap complet."""
        if not self.inv_enabled or not p.joueur:
            return
        group = str(msg.get("group") or "default")[:32]
        v = valid_inv(msg)
        if v is None:
            log(f"inventaire : message invalide de {p.name} ignoré (seq {str(msg.get('seq'))[:12]!r})")
            return
        now = time.monotonic()
        p.inv_times = [t for t in p.inv_times if now - t < 1.0]
        if len(p.inv_times) >= INV_RATE and not v["full"]:
            log(f"inventaire : {p.name} envoie trop de messages (> {INV_RATE}/s) : ignoré")
            return
        p.inv_times.append(now)
        p.inv_group = group
        self.rando_prog_inventaire(group)             # lot 4 : commun au niveau du compte (monde relu, groupe neuf)
        self.rando_plus_inventaire(group)             # progressifs de TWWHD : idem (dons comptés par les jeux)
        st = self.inv.setdefault(group, self.inv_vide())
        new_acq = [no for no in v["acq"] if no not in st["acq"]]
        prog_avant = self.rando_prog_effectifs(group) if new_acq else None
        st["acq"] += new_acq
        if new_acq and self.rando_prog_effectifs(group) != prog_avant:
            # relecture adverse du lot 4 : palier arrivé par l'inventaire sans lieu signalé (ouvert hors connexion,
            # registre oublié) : le compte envoyé aux jeux monte, la carte du pack est refaite chez chacun
            self.rando_prog_diffuser(group)
        new_bits = {}
        for off, m in v["bits"].items():
            n = m & ~st["bits"].get(off, 0)
            if n:
                new_bits[off] = n
                st["bits"][off] = st["bits"].get(off, 0) | n
        cap_up = False
        for k, val in v["cap"].items():
            if val > st["cap"].get(k, 0):
                st["cap"][k] = val
                cap_up = True
        fl_up = v["fl"] is not None and v["fl"] > st["fl"]
        if fl_up:
            st["fl"] = v["fl"]
        sac_avant = list(st["sac"] or [])
        if v["full"]:
            if st["sac"] is None or (self.prio_hote(p) and not st["hote"]):
                st["sac"] = sorted(v["sac"] or [])
            if self.prio_hote(p):
                st["hote"] = True
        sac = list(st["sac"] or [])
        for no in v["add"]:
            if no not in sac and len(sac) < 8:
                sac.append(no)
        for no in v["del"]:
            if no in sac:
                sac.remove(no)
            elif st["sac"] is not None:
                log(f"inventaire [{group}] : {p.name} a donné l'objet d'échange 0x{no:02X} déjà donné ailleurs "
                    "(deux échanges simultanés : le premier gagne)")
        if st["sac"] is not None:
            # un changement reçu AVANT tout full (message en vol au moment d'un « inv reset ») ne crée pas
            # le sac commun : sinon ce sac partiel « ferait foi » contre le full suivant (objets retirés)
            st["sac"] = sorted(sac)
        sac_change = list(st["sac"] or []) != sac_avant
        p.inv_ack = max(p.inv_ack, v["seq"])
        if new_acq:
            log(f"inventaire [{group}] : {p.name} a obtenu " + ", ".join(f"0x{no:02X}" for no in new_acq)
                + f" ({len(st['acq'])} objet(s) en commun)")
        if sac_change:
            log(f"inventaire [{group}] : sac de livraison -> "
                + (" ".join(f"0x{x:02X}" for x in st["sac"]) or "vide") + f" ({p.name})")
        if v["full"] or v["sync"]:
            p.send(dict(self.inv_snap(st), type="inv", group=group, ack=p.inv_ack, snap=1, **{"from": None}))
        else:
            ack = {"type": "inv", "from": None, "group": group, "ack": p.inv_ack}
            if st["sac"] is not None:
                ack["sac"] = list(st["sac"])
            p.send(ack)
        if not (new_acq or new_bits or cap_up or fl_up or sac_change):
            return
        p.mp_actif = time.monotonic()
        self.mp_marquer(gain=bool(new_acq))
        out = {"type": "inv", "from": p.id, "nom": p.name, "group": group}
        if new_acq:
            out["acq"] = new_acq
            st_j, rm_j = p.where()               # journal de la partie (client/journal.py) : où il les a trouvés
            if st_j and rm_j is not None:
                out["stage"], out["room"] = st_j, rm_j
        if new_bits:
            out["bits"] = [[o, m] for o, m in sorted(new_bits.items())]
        if cap_up:
            out["cap"] = dict(st["cap"])
        if fl_up:
            out["fl"] = st["fl"]
        if sac_change:
            out["sac"] = list(st["sac"])
        for o in self.players.values():
            if o.id != p.id and o.joueur and o.inv_group == group:
                o.send(out)

    # --- synchro v2 : compteurs communs (rubis, flèches, bombes) -----------------------
    def cnt_msg(self, o: Player, group: str, **extra) -> dict:
        st = self.cnt.etat(group)
        return dict({"type": "cnt", "from": None, "group": group, "tot": dict(st["tot"]), "cap": dict(st["cap"]),
                     "ack": o.cnt_ack}, **extra)

    def cnt_diffuser(self, group: str, src: Player | None, d: dict) -> None:
        """Nouveau total aux membres du groupe (chacun avec SON accusé), sauf ``src``."""
        for o in self.players.values():
            if o.joueur and o.cnt_group == group and (src is None or o.id != src.id):
                extra = {"d": d} if d else {}
                if src is not None:
                    extra.update({"from": src.id, "nom": src.name})
                o.send(self.cnt_msg(o, group, **extra))

    def cnt_groupes_geles(self) -> set[str]:
        return {g for r in self.cnt_gel.values() for g in r["groupes"]}

    def cnt_pc_j2(self, p: Player) -> bool:
        """``p`` joue sur le PC d'un joueur 2 en cours de rejoindre (client lancé avec --force) : ce qu'il
        compte pendant la copie est la copie elle-même."""
        return any(self.meme_pc(p.ip, r["ip"]) for r in self.cnt_gel.values())

    def handle_cnt(self, p: Player, msg: dict, clone: int | None = None) -> None:
        """Total par groupe, somme des deltas idempotente par identifiant d'événement (synchro/compteurs.py) ;
        hello : inscription (le serveur fait foi pour un compteur déjà connu) ; sync : total renvoyé.

        ``clone`` (mode Hanteurs, potion) : ``p`` est un Hanteur qui joue un Link et possède le joueur ``clone`` ;
        seul le pot commun du possédé est touché, et seulement à la baisse (synchro/potion.py ``depense_clone``) :
        son inscription n'apporte ni valeur, ni plafond, ni compteur nouveau, ses gains ne comptent pas."""
        if not self.cnt_enabled or not p.joueur:
            return
        v = valid_cnt(msg)
        if v is None:
            log(f"compteurs : message invalide de {p.name} ignoré (seq {str(msg.get('seq'))[:12]!r})")
            return
        if clone is not None:
            o = self.players.get(clone)
            if o is None or not o.joueur or o.cnt_group is None or o.cnt_group != v["group"]:
                return                         # pas le pot commun du possédé : ni compté ni accusé
            v["cap"] = {}
            if v["hello"]:
                connus = self.cnt.etat(v["group"])["tot"]
                v["v"] = {k: x for k, x in v["v"].items() if k in connus}
        now = time.monotonic()
        p.cnt_times = [t for t in p.cnt_times if now - t < 1.0]
        delta = not v["hello"] and not v["sync"]
        if len(p.cnt_times) >= CNT_RATE and not v["hello"]:
            log(f"compteurs : {p.name} envoie trop de messages (> {CNT_RATE}/s) : ignoré")
            if delta and (p.cnt_trou is None or v["seq"] < p.cnt_trou):
                # relecture adverse : un événement refusé ne doit pas être accusé par le suivant (ack = plus
                # grand seq), sinon le client l'oublie sans qu'il soit compté : plus rien n'est accusé tant
                # qu'il n'est pas renvoyé (le client renvoie ce qui n'est pas accusé, CompteursSync.RESEND)
                p.cnt_trou = v["seq"]
            return
        p.cnt_times.append(now)
        group = v["group"]
        p.cnt_group = group
        if v["hello"]:
            p.cnt_ack = max(p.cnt_ack, v["ack"])
            p.cnt_ks = set(v["v"])
            p.cnt_trou = None                  # nouvelle inscription : la file du client repart de son accusé
        if delta and p.cnt_trou is not None:
            if v["seq"] > p.cnt_trou:
                return                         # après un trou : ni compté ni accusé (renvoyé dans l'ordre)
            p.cnt_trou = None
        if group in self.cnt_groupes_geles():
            # rejoindre en cours : aucun total imposé. PC du joueur 2 : rien n'est compté (sa copie n'est pas
            # un gain), accusé ; autres joueurs : ni compté ni accusé, leur client le garde et le renvoie après
            # le « cnt reset » (relecture adverse : un achat fait pendant la copie était perdu ou remboursé)
            if delta and self.cnt_pc_j2(p):
                p.cnt_ack = max(p.cnt_ack, v["seq"])
                log(f"compteurs [{group}] : {p.name} : {v['d']} ignoré (PC du joueur 2, copie en cours)")
            elif delta:
                log(f"compteurs [{group}] : {p.name} : {v['d']} gardé par son client (rejoindre en cours)")
            p.send({"type": "cnt", "from": None, "group": group, "ack": p.cnt_ack, "gel": 1})
            return
        if delta:
            p.cnt_ack = max(p.cnt_ack, v["seq"])
        if v["hello"]:
            # mode Hanteurs (relecture adverse) : un ancien Hanteur hôte n'impose pas les compteurs de son jeu
            hote = self.prio_hote(p) and clone is None \
                and not (self.hanteurs is not None and self.hanteurs.ancien_hanteur(p.id))
            changed = self.cnt.hello(group, v["v"], v["cap"], hote)
            if changed:
                self.mp_marquer()
            st = self.cnt.etat(group)
            log(f"compteurs [{group}] : {p.name} inscrit ("
                + (", ".join(f"{k} {v['v'][k]}" for k in sorted(v["v"])) or "aucun compteur actif") + ") ; total "
                + (", ".join(f"{k} {st['tot'][k]}" for k in sorted(st["tot"])) or "vide"))
            p.send(self.cnt_msg(p, group, snap=1))
            if changed:
                self.cnt_diffuser(group, p, {})
            return
        if v["sync"]:
            p.send(self.cnt_msg(p, group, snap=1))
            return
        if clone is not None and v["ev"] not in self.cnt.etat(group)["evs"]:
            # dépense du clone : liste blanche, plancher 0, plafond de la possession ; une seule fois par événement
            v["d"] = self.hanteurs.depense_clone(p.id, v["d"], self.cnt.etat(group)["tot"])
            if v["d"]:
                log(f"compteurs [{group}] : {p.name} (Hanteur, possède {self.players[clone].name}) dépense "
                    + ", ".join(f"{k} {v['d'][k]}" for k in sorted(v["d"])))
        res = self.cnt.delta(group, v["ev"], v["d"], v["cap"], now)
        if res is None:
            log(f"compteurs [{group}] : événement {v['ev']} de {p.name} déjà compté (renvoi) : ignoré")
            p.send(self.cnt_msg(p, group))
            return
        app, lost = res
        if clone is None and self.medailles_compte(p):
            self.medailles.rubis(p.name, v["d"].get("r", 0))    # médailles : rubis ramassés par CE joueur
        self.mp_marquer()                      # total et événement compté (idempotence après un redémarrage)
        extra = {"perdu": lost} if any(x < 0 for x in lost.values()) else {}
        if extra:
            log(f"compteurs [{group}] : dépense en trop de {p.name} ({lost}) : total à 0")
        p.send(self.cnt_msg(p, group, **extra))
        if app:
            self.cnt_diffuser(group, p, app)

    def cnt_ajuster(self, group: str, k: str, d: int, raison: str) -> None:
        if group in self.cnt_groupes_geles():
            return
        app, _lost = self.cnt.ajuster(group, k, d, time.monotonic())
        self.mp_marquer()
        st = self.cnt.etat(group)
        log(f"compteurs [{group}] : {raison} : {k} {d:+d} -> {st['tot'].get(k)}")
        if app:
            self.cnt_diffuser(group, None, app)

    @staticmethod
    def cnt_membre(p: Player | None, group: str, k: str) -> bool:
        return p is not None and p.cnt_group == group and k in p.cnt_ks

    def cnt_doublon_coffre(self, p: Player, group: str, old: dict, rec: dict, what: str) -> None:
        """Le même coffre à compteur ouvert par deux joueurs qui le comptent (avant que le bit passe) : le
        total ne garde qu'une fois sa valeur (contrat « propriétaire unique »)."""
        item = rec.get("item")
        if not self.cnt_enabled or old.get("item") != item or item not in CNT_VALEURS:
            return
        k, val = CNT_VALEURS[item]
        if not (rec.get("cc") and old.get("cc") and self.cnt_membre(p, group, k)):
            return
        vus = old.setdefault("cnt_doublons", [])
        if p.name in vus:
            return                             # même joueur, rapport renvoyé (reconnexion)
        vus.append(p.name)
        self.cnt_ajuster(group, k, -val, f"{what} ouvert aussi par {p.name}, compté une fois")

    def handle_pickup(self, p: Player, msg: dict) -> None:
        """A4 premier arrivé : le premier ramassage d'un drop est gardé et annoncé (« gone ») aux autres
        joueurs de la zone, qui retirent leur exemplaire ; un second ramassage (plus rapide que la latence)
        retire au total commun le plus petit des deux gains."""
        if not self.drops_enabled or not self.cnt_enabled or not p.joueur:
            return                             # sans compteurs communs : drops « butin par joueur »
        v = valid_pickup(msg)
        if v is None or v["zone"] != p.zone or not self.cnt_membre(p, p.cnt_group, v["k"]):
            return                             # pas de total commun pour ce compteur chez l'auteur
        now = time.monotonic()
        p.pickup_times = [t for t in p.pickup_times if now - t < 1.0]
        if len(p.pickup_times) >= PICKUP_RATE:
            log(f"objets : {p.name} envoie trop de ramassages (> {PICKUP_RATE}/s) : ignoré")
            return
        p.pickup_times.append(now)
        for key in [k for k, r in self.pickups.items() if now - r["t"] > PICKUP_KEEP]:
            del self.pickups[key]
        key = (v["src"], v["nid"])
        old = self.pickups.get(key)
        if old is not None:
            if old["pid"] == p.id or p.id in old["aussi"]:
                return
            old["aussi"].add(p.id)
            k, group = v["k"], p.cnt_group
            if old["group"] == group and k in old["ks"]:
                comp = min(old["g"], v["g"])
                old["g"] = max(old["g"], v["g"])
                if comp:
                    self.cnt_ajuster(group, k, -comp, f"drop {key[0]}:{key[1]} ramassé aussi par {p.name} "
                                                       f"(premier : {old['nom']}), compté une fois")
                    return
            log(f"objets : drop {key[0]}:{key[1]} ramassé aussi par {p.name} (premier : {old['nom']})")
            return
        if len(self.pickups) >= PICKUP_MAX:
            self.pickups.pop(next(iter(self.pickups)))
        self.pickups[key] = {"t": now, "pid": p.id, "nom": p.name, "g": v["g"], "group": p.cnt_group,
                             "ks": set(p.cnt_ks), "k": v["k"], "aussi": set()}
        zone = v["zone"]
        log(f"objets : drop {key[0]}:{key[1]} (0x{v['no']:02X}) ramassé par {p.name} ({zone}) : retiré chez "
            "les autres")
        out = {"type": "gone", "zone": zone, "src": v["src"], "nid": v["nid"], "no": v["no"], "nom": p.name,
               "from": p.id}
        for o in self.members(zone):
            # relecture adverse : seulement ceux qui comptent ce compteur dans le même total (les autres
            # gardent leur exemplaire : butin par joueur)
            if o.id != p.id and self.cnt_membre(o, p.cnt_group, v["k"]):
                o.send(out)

    def drop_pris(self, p: Player, src: int, e: dict) -> bool:
        """A4 premier arrivé : le drop (src, nid) a été ramassé par un joueur qui partage son compteur avec
        ``p`` : pas de copie pour ``p`` qui entre ensuite dans la zone (les autres gardent la leur)."""
        r = self.pickups.get((src, e.get("nid")))
        return bool(r and r["pid"] != p.id and self.cnt_membre(p, r["group"], r["k"])
                    and time.monotonic() - r["t"] <= PICKUP_KEEP)

    def cnt_degeler(self) -> None:
        """Fin d'un rejoindre (réussi, refusé, expiré, joueur parti) : « cnt reset » aux membres des groupes
        gelés : chacun se réinscrit et renvoie ses événements gardés pendant la copie. Le total est GARDÉ
        (relecture adverse : l'oublier laissait le premier hello reçu l'imposer, et les achats / gains des
        autres joueurs faits pendant la copie étaient perdus ou remboursés) ; la copie a mis chez le joueur 2
        les valeurs de l'hôte, déjà corrigées sur ce total."""
        for rid in [r for r in self.cnt_gel if r not in self.rejoindre]:
            groupes = self.cnt_gel.pop(rid)["groupes"]
            encore = self.cnt_groupes_geles()
            for g in groupes:
                if g in encore:
                    continue
                n = 0
                for o in self.players.values():
                    if o.joueur and o.cnt_group == g:
                        o.send({"type": "cnt", "from": None, "group": g, "reset": 1, "ack": o.cnt_ack})
                        n += 1
                log(f"compteurs [{g}] : rejoindre [{rid}] terminé : {n} membre(s) se réinscrivent et renvoient "
                    "leurs événements gardés")

    # --- A15 : rejoindre la partie ------------------------------------------------
    def _addrs_locales(self) -> set[str]:
        if self.local_addrs is None:
            self.local_addrs = {"127.0.0.1", "::1", "localhost"} | set(local_ips())
        return self.local_addrs

    def meme_pc(self, a: str | None, b: str | None) -> bool:
        """Deux adresses du même PC : égales, ou toutes deux locales au serveur (127.0.0.1 et
        l'IP LAN / VPN du PC du serveur, par exemple)."""
        if a is None or b is None:
            return False
        loc = self._addrs_locales()
        return a == b or (a in loc and b in loc)

    def choisir_hote(self, j2: Player, force: bool = False) -> tuple[Player | None, str]:
        """(hôte, erreur). Ordre : --hote NOM du serveur ; client lancé avec --hote ; client de
        jeu de ce PC. Deux candidats au même rang : ambigu. Pas de « seul autre client » (revue
        A15 n° 4 : la partie de l'hôte pourrait être remplacée par celle du joueur 2)."""
        jeux = [o for o in self.players.values() if o.joueur and o.id != j2.id]
        if not force:
            jeux = [o for o in jeux if not self.meme_pc(o.ip, j2.ip)]
        if self.hote_nom:
            c = [o for o in jeux if o.name == self.hote_nom]
            if not c:
                return None, f"l'hôte {self.hote_nom} (--hote du serveur) n'est pas connecté"
            return (c[0], "") if len(c) == 1 else (None, f"plusieurs joueurs s'appellent {self.hote_nom}")
        for rang in ([o for o in jeux if o.hote], [o for o in jeux if o.ip in self._addrs_locales()]):
            if len(rang) == 1:
                return rang[0], ""
            if len(rang) > 1:
                return None, "hôte ambigu (" + ", ".join(o.name for o in rang) + ") : utilise --hote"
        if jeux:
            return None, ("pas d'hôte désigné (" + ", ".join(o.name for o in jeux) + " connecté(s) ailleurs) : "
                          "lance le client de l'hôte sur le PC du serveur, ou avec --hote, ou le serveur "
                          "avec --hote NOM")
        return None, "pas d'hôte : aucun client de jeu connecté (lance le client sur le PC de l'hôte)"

    def rejoindre_req(self, p: Player, msg: dict) -> None:
        rid = msg.get("rid")
        if not (isinstance(rid, str) and len(rid) == 8 and all(c in "0123456789abcdef" for c in rid)):
            return
        group = str(msg.get("group") or "default")[:32]
        force = msg.get("force") in (1, True)

        def refus(m: str) -> None:
            log(f"rejoindre [{rid}] : {p.name} refusé : {m}")
            p.send({"type": "rejoindre_err", "rid": rid, "msg": m})

        if any(r["j2"] == p.id for r in self.rejoindre.values()):
            return refus("une autre demande est déjà en cours")
        if self.mp is not None:
            # monde persistant : le SERVEUR répond avec l'image du monde (aucun hôte, rien remis à zéro ensuite)
            meme = [o.name for o in self.players.values() if o.joueur and o.id != p.id and self.meme_pc(o.ip, p.ip)]
            if meme and not force:
                return refus(f"client de jeu connecté depuis ton PC ({', '.join(meme)}) : ferme d'abord "
                             "wwmp_client sur ce PC (Ctrl+C)")
            err = self.mp.servir(self, p, rid, group)
            if err:
                return refus(err)
            return
        meme = [o.name for o in self.players.values() if o.joueur and o.id != p.id and self.meme_pc(o.ip, p.ip)]
        if meme and not force:
            return refus(f"client de jeu connecté depuis ton PC ({', '.join(meme)}) : ferme d'abord "
                         "wwmp_client sur ce PC (Ctrl+C)")
        explicite = bool(self.hote_nom) or any(o.joueur and o.hote for o in self.players.values())
        if p.ip in self._addrs_locales() and not explicite and not force:
            # revue A15 n° 4 : l'hôte par défaut est CE PC ; un « rejoindre » lancé ici remplacerait
            # la partie de ce PC par celle d'un autre
            return refus("tu es sur le PC du serveur, qui est l'hôte par défaut : « rejoindre » se lance sur "
                         "le PC du joueur 2 (ou désigne l'hôte : serveur --hote NOM / client --hote)")
        h, err = self.choisir_hote(p, force)
        if h is None:
            return refus(err)
        if "rejoindre" not in h.feat:
            return refus(f"l'hôte {h.name} a des outils trop anciens (pas de « rejoindre ») : mets à jour son PC")
        self.rejoindre[rid] = {"j2": p.id, "hote": h.id, "t": time.monotonic(), "group": group,
                               "groupe_hote": h.group, "recu": 0, "relaye": False}
        if self.cnt_enabled:
            # synchro v2 : compteurs communs gelés pendant la copie (un client du joueur 2 encore lancé
            # verrait la copie comme un gain) ; « cnt reset » à la fin, réussie ou non (cnt_degeler)
            self.cnt_gel[rid] = {"groupes": [g for g in dict.fromkeys([group, h.group]) if g], "ip": p.ip}
        log(f"rejoindre : {p.name} ({p.ip}) -> hôte {h.name} ({h.ip}) [rid {rid}], groupe {group}"
            + (f", groupe de l'hôte {h.group}" if h.group and h.group != group else ""))
        h.send({"type": "rejoindre_req", "rid": rid, "from": p.id, "name": p.name})

    def sauvegarde_hote(self, p: Player, msg: dict) -> None:
        """Relais tel quel (sans décoder) des morceaux de l'image de l'hôte vers J2."""
        rid = msg.get("rid")
        r = self.rejoindre.get(rid) if isinstance(rid, str) else None
        if not r or r["hote"] != p.id or r["j2"] not in self.players:
            return
        data, ln = msg.get("data"), msg.get("len")
        j2 = self.players[r["j2"]]
        if not isinstance(data, str) or not isinstance(ln, int) or isinstance(ln, bool) or ln > REJOINDRE_MAX \
                or len(data) > (REJOINDRE_PART + 2) // 3 * 4 \
                or r["recu"] + len(data) > (REJOINDRE_MAX + 2) // 3 * 4:
            del self.rejoindre[rid]
            log(f"rejoindre [{rid}] : image de {p.name} refusée (trop grande)")
            j2.send({"type": "rejoindre_err", "rid": rid, "msg": "image de l'hôte trop grande"})
            return
        r["recu"] += len(data)
        out = dict(msg)
        out["from"] = p.id
        j2.send(out)
        if msg.get("part") == msg.get("parts", 1) - 1:
            r["relaye"] = True
            log(f"rejoindre [{rid}] : image de {p.name} relayée à {j2.name} ({ln} octets)")

    def rejoindre_err_hote(self, p: Player, msg: dict) -> None:
        rid = msg.get("rid")
        r = self.rejoindre.get(rid) if isinstance(rid, str) else None
        if not r or r["hote"] != p.id:
            return
        del self.rejoindre[rid]
        log(f"rejoindre [{rid}] : refus de l'hôte {p.name} : {msg.get('msg')}")
        if r["j2"] in self.players:
            self.players[r["j2"]].send({"type": "rejoindre_err", "rid": rid,
                                        "msg": f"l'hôte {p.name} : {str(msg.get('msg'))[:200]}"})

    def rejoindre_fin(self, p: Player, msg: dict) -> None:
        rid = msg.get("rid")
        r = self.rejoindre.get(rid) if isinstance(rid, str) else None
        if not r or r["j2"] != p.id:
            return
        del self.rejoindre[rid]
        ok = msg.get("ok") in (1, True)
        if r.get("source") == "monde":
            # copie du monde : RIEN n'est remis à zéro (ce serait effacer le monde) ; sa partie neuve n'a pas
            # d'anciens bits, les synchros habituelles l'amènent ensuite à l'état exact du monde
            log(f"rejoindre [{rid}] : {p.name} : copie du monde " + ("appliquée" if ok else
                f"non appliquée ({str(msg.get('raison') or '?')[:120]})"))
            return
        if ok and not r["relaye"]:
            # revue A15 n° 7 (usage) : pas d'image relayée, rien n'a pu être copié
            log(f"rejoindre [{rid}] : fin « ok » de {p.name} sans image relayée : ignorée")
            ok = False
        raison = str(msg.get("raison") or "")[:200]
        h = self.players.get(r["hote"])
        if h:
            h.send({"type": "rejoindre_fin", "rid": rid, "from": p.id, "name": p.name, "ok": ok,
                    **({} if ok else {"raison": raison or "?"})})
        if not ok:
            log(f"rejoindre [{rid}] : {p.name} n'a rien appliqué ({raison[:120] or '?'})")
            return
        # revue A15 n° 4 (usage) : le groupe demandé par J2 ET celui de l'hôte
        groupes = [r["group"]] + [g for g in (r.get("groupe_hote"),) if g and g != r["group"]]
        n = 0
        for g in groupes:
            self.progress[g] = {}
            membres = [o for o in self.players.values() if o.joueur and o.group == g]
            n += len(membres)
            for o in membres:
                o.send({"type": "progress", "from": None, "group": g, "bits": {}, "full": 1, "reset": 1})
                if "rejoindre" not in o.feat:
                    log(f"rejoindre [{rid}] : ATTENTION, {o.name} a des outils anciens et ne gère pas reset")
        if self.tsw_enabled:
            self.tsw_gc()
        if self.keys_enabled:
            # synchro v2 : compteurs de clés oubliés, chaque client renvoie ses valeurs (hello)
            for g in groupes:
                self.rando_hors_retenir(g)            # lot 5 : clés de coffres étrangers déjà comptées
                self.keys.reset(g)
                for o in self.players.values():
                    if o.joueur and o.keys_group == g:
                        o.send({"type": "keys", "from": None, "group": g, "reset": 1})
            self.keys_save()
        if self.coffres_enabled:
            # synchro v2 : la copie a aligné coffres et inventaire ; registre oublié
            for g in groupes:
                self.coffres[g] = {}
                for o in self.players.values():
                    if o.joueur and o.coffres_group == g:
                        o.send({"type": "coffre", "from": None, "group": g, "reset": 1})
        if self.inv_enabled:
            # synchro v2 : la copie a aligné les inventaires ; état commun oublié, chacun renvoie full
            for g in groupes:
                self.inv.pop(g, None)
                for o in self.players.values():
                    if o.joueur and o.inv_group == g:
                        o.send({"type": "inv", "from": None, "group": g, "reset": 1})
        if self.coffres_enabled:
            for g in groupes:
                self.rando_prog_diffuser(g)           # lot 4 : registre oublié, compte des objets progressifs à zéro
                self.rando_hors_diffuser(g)           # lot 5 : idem pour les coffres étrangers ouverts
        if self.cadeaux_enabled:
            # synchro v2 (quarts de cœur au sol) : la copie a aligné les bits d'objet ; registre oublié
            # (sinon un registre d'une autre partie donnerait des quarts jamais pris dans celle-ci)
            for g in groupes:
                self.gifts.pop(g, None)
                for o in self.players.values():
                    if o.joueur and o.gift_group == g:
                        o.send({"type": "gift", "from": None, "group": g, "reset": 1})
        log(f"rejoindre [{rid}] : appliqué chez {p.name} ; progression [{', '.join(groupes)}] remise à zéro "
            f"({n} membre(s) prévenu(s))")

    def rejoindre_expire(self, now: float | None = None) -> None:
        """Demande sans réponse de l'hôte depuis rejoindre_timeout s : abandonnée. Une fois
        l'image relayée, la demande reste ouverte jusqu'à rejoindre_fin (J2 confirme) ou au départ
        de J2 / de l'hôte."""
        now = time.monotonic() if now is None else now
        for rid, r in list(self.rejoindre.items()):
            if not r["relaye"] and now - r["t"] > self.rejoindre_timeout:
                del self.rejoindre[rid]
                log(f"rejoindre [{rid}] : pas de réponse de l'hôte en {self.rejoindre_timeout:.0f} s")
                if r["j2"] in self.players:
                    self.players[r["j2"]].send({"type": "rejoindre_err", "rid": rid,
                                                "msg": f"l'hôte n'a pas répondu en {self.rejoindre_timeout:.0f} s"})

    def rejoindre_depart(self, p: Player) -> None:
        for rid, r in list(self.rejoindre.items()):
            if p.id in (r["j2"], r["hote"]):
                del self.rejoindre[rid]
                if p.id == r["hote"] and r["j2"] in self.players:
                    self.players[r["j2"]].send({"type": "rejoindre_err", "rid": rid,
                                                "msg": f"l'hôte {p.name} s'est déconnecté"})
                log(f"rejoindre [{rid}] : abandonné ({p.name} déconnecté)")

    # --- A7 : switches temporaires --------------------------------------------
    @staticmethod
    def _valid_stage(stage) -> bool:
        return isinstance(stage, str) and 0 < len(stage) <= 8 and stage.replace("_", "").isalnum()

    @staticmethod
    def _int(x) -> bool:
        return isinstance(x, int) and not isinstance(x, bool)

    def handle_tsw(self, p: Player, msg: dict) -> None:
        """Changements de switches temporaires et ennemis tués d'un joueur : numérotés, gardés
        pour la salle, renvoyés à tout le groupe présent dans le stage (émetteur compris)."""
        if not self.tsw_enabled:
            return
        group = str(msg.get("group") or "default")[:32]
        p.tsw_group = group
        if msg.get("hello"):
            self.tsw_push(p)
            return
        stage = msg.get("stage")
        if not self._valid_stage(stage):
            return
        st = self.temp.setdefault((group, stage), {"dan": {}, "rooms": {}})
        out_ch, out_act = [], []
        ch, act = msg.get("ch"), msg.get("act")
        for e in (ch if isinstance(ch, list) else [])[:TSW_MAX]:
            if not (isinstance(e, list) and len(e) == 3 and all(self._int(x) for x in e)):
                continue
            room, sw, val = e
            if not (0x80 <= sw < 0xF0 and val in (0, 1)):
                continue
            if sw < 0xC0:
                room = -1
            elif not 0 <= room < 64:
                continue
            seq = next(self.tsw_seq)
            if room == -1:
                st["dan"][sw] = (val, seq)
            else:
                st["rooms"].setdefault(room, {"sw": {}, "act": set()})["sw"][sw] = (val, seq)
            out_ch.append([room, sw, val, seq])
        for e in (act if isinstance(act, list) else [])[:TSW_MAX]:
            if not (isinstance(e, list) and len(e) == 2 and all(self._int(x) for x in e)):
                continue
            room, i = e
            if not (0 <= room < 64 and 0 <= i < 512):
                continue
            known = st["rooms"].setdefault(room, {"sw": {}, "act": set()})["act"]
            if i not in known:
                known.add(i)
                out_act.append([room, i])
        if out_ch or out_act:
            desc = ", ".join(f"0x{sw:02X}{'' if r == -1 else '@' + str(r)}={v}" for r, sw, v, _ in out_ch[:4])
            if out_act:
                desc += (", " if desc else "") + f"{len(out_act)} ennemi(s) tué(s)"
            log(f"switches temporaires [{group}] {stage} de {p.name} : {desc}")
            out = {"type": "tsw", "from": p.id, "group": group, "stage": stage, "ch": out_ch, "act": out_act}
            for o in list(self.players.values()):
                if o.tsw_group == group and (o is p or o.where()[0] == stage):
                    o.send(out)
        self.tsw_gc()

    def tsw_gc(self) -> None:
        """Oublie l'état d'un stage sans joueur du groupe. Dans un stage occupé, on garde les
        switches de zone (0xC0-0xDF) et les ennemis tués de TOUTES les salles : une zone reste
        chargée dans les salles voisines, et un joueur peut arriver bien après le départ de celui
        qui a ouvert la grille. Seuls les switches de salle (0xE0-0xEF) d'une salle sans joueur
        sont oubliés (le jeu les efface à chaque changement de salle)."""
        for key in list(self.temp):
            group, stage = key
            here = [o.where()[1] for o in self.players.values() if o.tsw_group == group and o.where()[0] == stage]
            if not here:
                del self.temp[key]
                continue
            for room, r in self.temp[key]["rooms"].items():
                if room not in here:
                    for sw in [sw for sw in r["sw"] if sw >= 0xE0]:
                        del r["sw"][sw]

    def tsw_push(self, p: Player) -> None:
        """Instantané (« snap ») de tout l'état connu du stage où `p` se trouve (switches de
        donjon, switches et ennemis tués de chaque salle), en messages de TSW_MAX entrées au plus."""
        if p.tsw_group is None:
            return
        stage, _room = p.where()
        st = self.temp.get((p.tsw_group, stage)) if stage else None
        if not st:
            return
        ch = [[-1, sw, v, seq] for sw, (v, seq) in sorted(st["dan"].items())]
        act = []
        for room, r in sorted(st["rooms"].items()):
            ch += [[room, sw, v, seq] for sw, (v, seq) in sorted(r["sw"].items())]
            act += [[room, i] for i in sorted(r["act"])]
        while ch or act:
            p.send({"type": "tsw", "from": None, "group": p.tsw_group, "stage": stage, "ch": ch[:TSW_MAX],
                    "act": act[:TSW_MAX], "snap": 1})
            ch, act = ch[TSW_MAX:], act[TSW_MAX:]

    # --- A4/A6 : objets apparus et jarres cassées --------------------------------
    def handle_ground(self, p: Player, msg: dict) -> None:
        """Relais aux autres joueurs de la zone de l'émetteur (et mémoire pour les arrivants)."""
        if not self.drops_enabled:
            return
        zone = msg.get("zone")
        lst = msg.get("list")
        if not isinstance(zone, str) or not isinstance(lst, list) or p.zone is None:
            return
        # A6 : une jarre est annoncée dans la zone de SA salle d'origine (portée puis lancée dans
        # une autre salle) : n'importe quelle salle du stage de l'émetteur ; objets : sa zone
        if zone != p.zone and (msg["type"] == "drop" or zone_stage(zone) != zone_stage(p.zone)):
            return
        now = time.monotonic()
        if msg["type"] == "drop":
            entries = [e for e in (valid_drop(x) for x in lst[:DropWatch.MAX_PER_MSG]) if e]
            if not entries:
                return
            kept = [d for d in self.drops.get(zone, []) if now - d[0] < DROP_KEEP]
            self.drops[zone] = kept + [(now, p.id, p.name, e) for e in entries]
            log(f"objets : {len(entries)} apparu(s) chez {p.name} ({zone})")
            out = {"type": "drop", "from": p.id, "zone": zone, "list": entries}
        else:
            entries = [e for e in (valid_break(x) for x in lst[:DropWatch.MAX_PER_MSG]) if e]
            if not entries:
                return
            if zone == p.zone or self.members(zone):     # zone vide : le jeu y recréera ses jarres
                known = self.breaks.setdefault(zone, {})
                for e in entries:
                    if e["key"] in known or len(known) < BREAK_MAX:
                        known[e["key"]] = (p.id, e)
            keys = ", ".join(e["key"] for e in entries[:3]) + (" …" if len(entries) > 3 else "")
            log(f"jarres : {len(entries)} cassée(s) chez {p.name} ({zone} : {keys})")
            out = {"type": "break", "from": p.id, "zone": zone, "list": entries}
            # tout le stage : un joueur d'une salle voisine a peut-être cette jarre chargée
            for o in self.players.values():
                if o.id != p.id and o.zone is not None and zone_stage(o.zone) == zone_stage(zone):
                    o.send(out)
            return
        for o in self.members(zone):
            if o.id != p.id:
                o.send(out)

    def ground_push(self, p: Player, zone: str) -> None:
        """À l'entrée dans une zone : objets apparus récents et jarres cassées des autres."""
        if not self.drops_enabled:
            return
        now = time.monotonic()
        by: dict[int, list] = {}
        for t, src, name, e in self.drops.get(zone, []):
            # pas ses propres objets, même revenu sous un autre id (reconnexion : même nom, ancien
            # id parti) : il en créerait un doublon
            mine = src == p.id or (src not in self.players and name == p.name)
            if now - t < DROP_KEEP and not mine and not self.drop_pris(p, src, e):
                by.setdefault(src, []).append(e)
        for src, entries in by.items():
            p.send({"type": "drop", "from": src, "zone": zone, "list": entries, "snap": 1})
        # jarres : à tous, casseur compris (revenu dans la zone, son jeu a recréé la jarre)
        brk = [e for _src, e in self.breaks.get(zone, {}).values()]
        if brk:
            p.send({"type": "break", "from": None, "zone": zone, "list": brk, "snap": 1})
        # synchro v2 : dalles de magma encore utiles (âge corrigé de leur attente au serveur)
        mg: dict[int, list] = {}
        for t, src, name, e in self.magmas.get(zone, []):
            mine = src == p.id or (src not in self.players and name == p.name)
            if now - t < MAGMA_PUSH and not mine:
                mg.setdefault(src, []).append(dict(e, age=int(e["age"] + (now - t) * 30)))
        for src, entries in mg.items():
            p.send({"type": "magma", "from": src, "zone": zone, "list": entries, "snap": 1})

    def ground_gc(self, zone: str) -> None:
        """Zone vide : le jeu y recrée jarres et objets au prochain chargement, on oublie tout."""
        if not self.members(zone):
            self.drops.pop(zone, None)
            self.breaks.pop(zone, None)
            self.magmas.pop(zone, None)        # synchro v2 : les dalles ne sont pas sauvegardées
            # A3 : bâtons libres oubliés (le jeu les recrée ou non au rechargement) ; un bâton
            # tenu suit son porteur (sticks_take), il ne reste donc ici que des bâtons libres
            self.sticks.pop(zone, None)
            self.mob.forget(zone)              # synchro v2, lot 3 : blocs remis en place par le jeu
            self.herbe.forget(zone)            # synchro v3 : l'herbe repousse au prochain chargement

    # --- A3 : bâtons partagés --------------------------------------------------------
    def handle_stick(self, p: Player, msg: dict) -> None:
        """Arbitrage et relais des bâtons (voir le docstring du module et synchro/batons.py)."""
        if not self.sticks_enabled:
            return
        zone = msg.get("zone")
        if not isinstance(zone, str) or zone != p.zone:
            return
        v = valid_stick(msg)
        if v is None:
            return
        key, ev = v["key"], v["ev"]
        zs = self.sticks.setdefault(zone, {})
        rec = zs.get(key)
        holder = rec.get("holder") if rec else None
        if holder is not None and holder not in self.players:
            holder = None                    # détenteur parti : la clé est libre
        mine_or_free = holder in (None, p.id)
        if ev == "grab":
            if not mine_or_free:
                p.send({"type": "stick", "from": None, "zone": zone, "ev": "deny", "key": key, "holder": holder})
                log(f"bâton {key} ({zone}) : {p.name} refusé, déjà tenu par {self.players[holder].name}")
                return
            if rec is None and len(zs) >= STICK_MAX:
                return
            rec = zs.setdefault(key, {})
            rec.update(holder=p.id, type=v.get("kind", rec.get("type", 0)), flame=v.get("flame", rec.get("flame", 0)),
                       pos=v["pos"], rot=rec.get("rot", [0, 0, 0]))
            if holder != p.id:               # pas à chaque rappel (grab renvoyé toutes les 3 s)
                log(f"bâton {key} ({zone}) ramassé par {p.name}")
        elif ev == "rest":
            if not mine_or_free:
                return
            if v.get("cause") == "aligne":
                auth = self.authority.get(zone, (None, 0))[0]
                if auth != p.id or holder is not None:
                    return                   # alignement : l'autorité seule, bâton libre seulement
            if rec is None and len(zs) >= STICK_MAX:
                return
            rec = zs.setdefault(key, {})
            rec.update(holder=None, type=v.get("kind", rec.get("type", 0)), flame=v.get("flame", rec.get("flame", 0)),
                       pos=v["pos"], rot=v.get("rot", rec.get("rot", [0, 0, 0])))
            if v.get("cause") != "aligne":
                log(f"bâton {key} ({zone}) posé par {p.name}")
        elif ev == "gone":
            if not mine_or_free:
                return
            zs.pop(key, None)
            log(f"bâton {key} ({zone}) disparu chez {p.name}")
        elif ev == "taken":
            if self.authority.get(zone, (None, 0))[0] != p.id or holder is not None:
                return
            zs.pop(key, None)
        elif ev == "flame":
            if not mine_or_free or rec is None:
                return
            rec["flame"] = v.get("flame", 0)
        out = dict(v, type="stick", zone=zone)
        out["from"] = p.id
        for o in self.members(zone):
            if o.id != p.id:
                o.send(out)

    def sticks_take(self, p: Player, old: str | None) -> list[tuple[str, dict]]:
        """Le porteur quitte `old` : ses bâtons y sont retirés (« gone » cause parti aux membres)."""
        if not old or not self.sticks_enabled:
            return []
        zs = self.sticks.get(old, {})
        moved = [(k, r) for k, r in zs.items() if r.get("holder") == p.id]
        for k, _r in moved:
            del zs[k]
            for o in self.members(old):
                if o.id != p.id:
                    o.send({"type": "stick", "from": p.id, "zone": old, "ev": "gone", "key": k, "cause": "parti"})
        return moved

    def sticks_give(self, p: Player, zone: str, moved) -> None:
        """…et arrivent avec lui dans `zone` (« grab » aux membres : marionnette dans la main de son fantôme)."""
        if not moved:
            return
        zs = self.sticks.setdefault(zone, {})
        for k, r in moved:
            if k in zs and zs[k].get("holder") not in (None, p.id) and zs[k].get("holder") in self.players:
                continue                     # même clé tenue par un autre ici (salle voisine) : on laisse
            zs[k] = dict(r, holder=p.id)
            out = {"type": "stick", "from": p.id, "zone": zone, "ev": "grab", "key": k, "kind": r.get("type", 0),
                   "flame": r.get("flame", 0), "pos": r.get("pos") or [0.0, 0.0, 0.0]}
            for o in self.members(zone):
                if o.id != p.id:
                    o.send(out)

    def sticks_push(self, p: Player, zone: str) -> None:
        """« snap » : tous les bâtons connus de la zone, avec leur détenteur, à qui y entre."""
        if not self.sticks_enabled:
            return
        zs = self.sticks.get(zone)
        if not zs:
            return
        lst = [{"key": k, "holder": r.get("holder") if r.get("holder") in self.players else None,
                "kind": r.get("type", 0), "flame": r.get("flame", 0), "pos": r.get("pos"), "rot": r.get("rot")}
               for k, r in zs.items() if r.get("pos") is not None]
        if lst:
            p.send({"type": "stick", "from": None, "zone": zone, "ev": "snap", "list": lst})

    def sticks_left(self, p: Player) -> None:
        """Déconnexion : chaque bâton tenu tombe à la dernière position connue du porteur + 50."""
        if not self.sticks_enabled:
            return
        for zone, zs in self.sticks.items():
            for k, r in zs.items():
                if r.get("holder") != p.id:
                    continue
                pos = list(p.pos) if p.pos else (r.get("pos") or [0.0, 0.0, 0.0])
                pos = [float(pos[0]), float(pos[1]) + 50.0, float(pos[2])]
                r.update(holder=None, pos=pos)
                out = {"type": "stick", "from": None, "zone": zone, "ev": "rest", "key": k, "kind": r.get("type", 0),
                       "flame": r.get("flame", 0), "pos": pos, "rot": r.get("rot") or [0, 0, 0],
                       "cause": "porteur_parti"}
                for o in self.members(zone):
                    if o.id != p.id:
                        o.send(out)

    def acces_entree(self, writer: asyncio.StreamWriter) -> str | None:
        """Internet : adresse de la connexion si elle est acceptée (comptée dans la garde), None
        sinon (adresse bloquée, trop de connexions : fermée tout de suite)."""
        peer = writer.get_extra_info("peername")
        ip = str(peer[0]) if isinstance(peer, tuple) and peer else str(peer)
        ip = ip[7:] if ip.startswith("::ffff:") else ip
        refus = self.acces.ouvrir(ip)
        if refus is not None:
            if refus != "bloquee":
                log(f"connexion de {ip} refusée : {refus}")
            writer.close()
            return None
        if not self.acces.actif and not self.avert_public and not acces.ip_locale_ou_vpn(ip):
            self.avert_public = True
            log(f"ATTENTION : connexion depuis Internet ({ip}) et ce serveur n'a PAS de mot de passe. Protège "
                "la partie : --mot-de-passe-fichier, WWMP_MOT_DE_PASSE ou --donnees (docs/hotes/JOUER_PAR_INTERNET.md)")
        return ip

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        ip_acces = self.acces_entree(writer)
        if ip_acces is None:
            return
        p = Player(next(self.ids), writer)
        self.players[p.id] = p
        # latence : TCP_NODELAY (une connexion ACCEPTÉE ne l'a pas sous Windows : le relais d'une jarre cassée
        # pouvait attendre l'accusé du message précédent, jusqu'à 40 à 200 ms)
        latence.sans_delai(writer)
        peer = writer.get_extra_info("peername")
        p.peer = f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) and len(peer) >= 2 else str(peer)
        p.ip = ip_acces
        log(f"connexion de {p.peer} -> id {p.id}")
        p.auth = not self.acces.actif          # Internet : défi envoyé, hello avec preuve attendu
        if not p.auth:
            p.defi = self.acces.nouveau_defi()
            p.send({"type": "defi", "v": acces.V, "nonce": p.defi})
        reason = "connexion fermée par le client"
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=TIMEOUT)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    reason = "message trop long"
                    break
                p.last_seen = time.monotonic()
                p.bytes_in += len(line)
                msg = decode(line)
                if isinstance(msg, dict) and not self.valid.debit(p, msg.get("type"), len(line)):
                    continue                       # lot S2, mode « refus » : au-delà des débits, pas traité
                if isinstance(msg, dict) and msg.get("type") in ("admin_defi", "admin"):
                    # administration du serveur dédié : son propre défi (compte admin, pas le mot de passe de partie)
                    if not await adm.recevoir(self, p, msg, log):
                        reason = "administration refusée"
                        break
                    await writer.drain()
                    continue
                if not p.auth and isinstance(msg, dict) and msg.get("type") == "hello" \
                        and msg.get("role") == robot_mod.ROLE and self.robot.actif \
                        and robot_mod.cle_valide(self.robot.cle, msg.get("cle")):
                    p.auth = True                  # joueur robot : sa clé (128 bits) vaut le mot de passe de partie
                if not p.auth and not await acces.controler_hello(self.acces, p, msg, log):
                    reason = "mot de passe refusé"
                    break
                if msg is not None:
                    try:
                        self.handle(p, msg)
                    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as e:
                        log(f"{p.name} : message mal formé ignoré ({type(e).__name__}: {e})")
                if writer.is_closing():
                    break
                await writer.drain()
        except asyncio.TimeoutError:
            reason = f"aucun message depuis {TIMEOUT:.0f} s (réseau coupé ou client figé)"
        except (asyncio.LimitOverrunError, ValueError):
            reason = "message trop long"
        except (ConnectionError, OSError) as e:
            reason = f"erreur réseau ({e.__class__.__name__})"
        finally:
            self.acces.fermer(ip_acces)
            del self.players[p.id]
            self.robot_depart(p)               # joueur robot : sa zone revient aux joueurs (reassign plus bas)
            self.valid.depart(p.id)
            self.hv_vus.discard(p.id)
            self.rejoindre_depart(p)
            if self.mp is not None and p.joueur:
                self.mp.depart(self, p)
            if p.hello and p.role != "jeu":
                log(f"{p.name} (id {p.id}, {p.peer}) déconnecté (outil {p.role}) : {reason}")
            elif p.hello:
                log(f"{p.name} (id {p.id}, {p.peer}) déconnecté : {reason}, après "
                    f"{time.monotonic() - p.connected:.0f} s ; {len(self.players)} joueur(s) restant(s)")
                self.broadcast({"type": "leave", "id": p.id})
                self.dif_diffuser()             # un Link de moins
                self.check_pause()              # pause vivante : l'avertissement suit les joueurs présents
            elif getattr(p, "admin_vu", False):
                log(f"{p.peer} (id {p.id}) : connexion d'administration fermée ({reason})")
            else:   # sonde (diagnostic.py) ou connexion qui n'a jamais dit bonjour
                log(f"{p.peer} (id {p.id}) déconnecté sans s'être présenté (sonde de diagnostic ?)")
            self.sticks_left(p)
            self.mob.left(p.id)
            self.carry.left(p.id)
            self.coups.oublier(p.id)            # coups entre joueurs : cadences et invincibilité oubliées
            self.ralliement.oublier(p.id)       # chant de ralliement : délai et appels en cours oubliés
            self.porter_envoyer(self.porter.oublier(p.id, self.porter_joueur))   # porter : port défait des deux côtés
            self.bonheur_envoyer(self.bonheur.oublier(p.id))   # Fragments du Bonheur : propositions annulées
            self.tenues.oublier(p.id)           # tenues à pouvoir : partage et essai oubliés (le duo est recalculé)
            self.fusions_envoyer(self.fusions.oublier(p.id))   # fusions : celle du partant finie pour les deux
            self.releve_envoyer(self.releve.oublier(p.id))     # relever : plus à terre, plus releveur
            self.camouflage_envoyer(self.camouflage.oublier(p.id))     # camouflage : plus camouflé
            self.courses.oublier(p.id)          # courses en bateau : un coureur qui part abandonne
            self.courses_envoyer()
            self.monde_depart(p)                # horloge commune : un joueur figé qui part ne l'arrête plus
            sorties = self.cache.depart(p.id)   # cache-cache : sa partie s'arrête pour les autres
            if sorties:
                log(f"cache-cache : {p.name} parti, sa partie s'arrête")
                self.cc_envoyer(sorties)
            if self.cnt_gel:
                self.cnt_degeler()
            if p.zone:
                zone, p.zone = p.zone, None
                self.reassign(zone)
                self.ground_gc(zone)
            self.salles_gc(depart=True)          # salles qu'il était seul à avoir : gardées le temps d'un retour
            if p.tsw_group is not None:
                self.tsw_gc()
            self.boss_gc()
            if self.hanteurs is not None and p.hello and p.role == "jeu":
                self.h_joueurs()
                self.h_flush()
            writer.close()

    async def au_revoir(self, delai: float = 1.0) -> int:
        """A16 : « bye » à tous les joueurs, puis attente (au plus ``delai`` s) que les tampons
        d'envoi se vident. Renvoie le nombre de joueurs prévenus."""
        dest = [p for p in self.players.values() if p.hello]
        for p in dest:
            p.send({"type": "bye", "msg": BYE_REDEMARRAGE if self.redemarrage else BYE_MSG})

        async def vider(p: Player) -> None:
            try:
                await p.writer.drain()
            except (ConnectionError, OSError, RuntimeError):
                pass
        if dest:
            try:
                await asyncio.wait_for(asyncio.gather(*(vider(p) for p in dest)), delai)
            except asyncio.TimeoutError:
                pass
        for p in dest:
            try:
                p.writer.close()
            except (OSError, RuntimeError):
                pass
        return len(dest)

    async def stats_loop(self, every: float) -> None:
        """Débit par joueur (octets reçus de lui / envoyés vers lui), toutes les `every` s."""
        last = {pid: (p.bytes_in, p.bytes_out) for pid, p in self.players.items()}
        while True:
            await asyncio.sleep(every)
            parts = []
            for pid, p in list(self.players.items()):
                bi, bo = last.get(pid, (0, 0))
                parts.append(f"{p.name} reçu {(p.bytes_in - bi) / every / 1024:.1f} "
                             f"envoyé {(p.bytes_out - bo) / every / 1024:.1f}"
                             + (f" ({p.skipped} sautés)" if p.skipped else ""))
            last = {pid: (p.bytes_in, p.bytes_out) for pid, p in self.players.items()}
            if parts:
                try:
                    log("débit Ko/s : " + ", ".join(parts))
                except UnicodeError:
                    pass


async def robot_boucle(srv: Server, pas: float = 0.5) -> None:
    """Joueur robot : choix de zone, processus, ordres et passations (toutes les 0,5 s)."""
    while True:
        await asyncio.sleep(pas)
        try:
            srv.robot_tick()
        except Exception as e:                 # jamais fatal : le serveur continue de relayer
            log(f"robot : erreur ignorée ({type(e).__name__}: {e})")


async def carte_boucle(srv: Server, pas: float = CARTE_PAS) -> None:
    """Carte marine partagée : positions en mer de tous les joueurs, toutes les 2 s."""
    while True:
        await asyncio.sleep(pas)
        try:
            srv.carte_tick()
        except Exception as e:                 # jamais fatal : le serveur continue de relayer
            log(f"carte marine : erreur ignorée ({type(e).__name__}: {e})")


async def courses_boucle(srv: Server, pas: float = 0.25) -> None:
    """Courses en bateau : décompte, fin de course, même sans message reçu."""
    while True:
        await asyncio.sleep(pas)
        try:
            if srv.courses.course is not None:
                srv.courses_tick()
        except Exception as e:                 # jamais fatal : le serveur continue de relayer
            log(f"courses : erreur ignorée ({type(e).__name__}: {e})")


async def monde_boucle(srv: Server, pas: float = 1.0) -> None:
    """Monde persistant : écriture périodique, demandes d'image, fin d'attente des verdicts (chaque seconde)."""
    while True:
        await asyncio.sleep(pas)
        try:
            if srv.monde.vivant:
                srv.monde_vivant_tick()
            if srv.mp is not None:
                srv.mp.tick(srv)
        except Exception as e:                 # jamais fatal : le serveur continue de relayer
            log(f"monde : erreur ignorée ({type(e).__name__}: {e})")


def local_ips() -> list[str]:
    """Adresses IPv4 de ce PC. Rien n'est envoyé sur le réseau."""
    ips: list[str] = []
    try:
        ips += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    try:   # interface de la route par défaut (socket UDP « connecté » : aucun paquet émis)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    out: list[str] = []
    for ip in ips:
        if ip not in out and not ip.startswith(("127.", "169.254.")) and ip != "0.0.0.0":
            out.append(ip)
    return out


def ip_hint(ip: str) -> str:
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except ValueError:
        return "?"
    if a == 100 and 64 <= b < 128:
        return "VPN Tailscale ?"
    if a == 26:
        return "Radmin VPN ?"
    if a == 10 or (a == 172 and 16 <= b < 32) or (a == 192 and b == 168):
        return "réseau local ou VPN de jeu"
    return "publique ?"


async def start(host: str, port: int, busy_defer: bool = True,
                tsw: bool = True, drops: bool = True, sticks: bool = True,
                hote: str | None = None, keys: bool = True,
                keys_file: str | None = None, coffres: bool = True,
                inventaire: bool = True, cadeaux: bool = True, boss: bool = True,
                max_players: int = MAX_PLAYERS, ghost_hits: bool = True,
                mobiles: bool = True, compteurs: bool = True,
                carry: bool = True, mode_hanteurs: bool = False,
                hanteurs_cfg: dict | None = None, rares: bool = True,
                monde: bool = True, herbe: bool = True,
                deathlink: bool = False, mot_de_passe: str | None = None,
                monde_persistant: "mpm.Monde | None" = None,
                rando_graine: str | None = None, difficulte: dict | None = None,
                difficulte_on: bool = True, rando_mode: str = "sur",
                rando_options: dict | None = None, trackeur: bool = True,
                trackeur_deduire: bool = False,
                pnj: bool = True, autorite_preferee: str | None = None,
                **opts) -> tuple[Server, asyncio.base_events.Server]:
    """Démarre le serveur (port 0 = libre) ; utilisé aussi par les tests."""
    srv = Server(busy_defer=busy_defer, tsw=tsw, drops=drops, sticks=sticks, hote=hote, keys=keys,
                 keys_file=keys_file, coffres=coffres, inventaire=inventaire, cadeaux=cadeaux, boss=boss,
                 max_players=max_players, ghost_hits=ghost_hits, mobiles=mobiles, compteurs=compteurs,
                 carry=carry, mode_hanteurs=mode_hanteurs, hanteurs_cfg=hanteurs_cfg, rares=rares, monde=monde,
                 herbe=herbe, deathlink=deathlink, mot_de_passe=mot_de_passe, monde_persistant=monde_persistant,
                 rando_graine=rando_graine, difficulte=difficulte, difficulte_on=difficulte_on,
                 rando_mode=rando_mode, rando_options=rando_options,
                 trackeur=trackeur, trackeur_deduire=trackeur_deduire, pnj=pnj,
                 autorite_preferee=autorite_preferee, **opts)
    server = await asyncio.start_server(srv.client, host, port, limit=MAX_LINE)
    return srv, server


def rando_annonce_complet(t) -> str:
    """Ligne de démarrage du randomizer complet. Les options d'une table relue « telle quelle » (monde persistant
    écrit par d'autres outils, ou fichier abîmé) ne sont PAS validées : rien ici ne suppose leur forme (une liste
    « lieux » qui n'en est pas une arrêtait le serveur au démarrage, table pourtant acceptée)."""
    o = t.options if isinstance(t.options, dict) else {}
    lieux = o.get("lieux")
    lieux = ", ".join(str(x)[:32] for x in lieux[:16]) if isinstance(lieux, (list, tuple)) else ""

    def v(cle: str) -> str:
        return str(o.get(cle, "?"))[:32]
    return (f"RANDOMIZER COMPLET (coffres avec objets de progression, logique anti-blocage ; jamais essayé en "
            f"jeu) : graine « {t.graine} », empreinte {t.empreinte}, {len(t.entrees)} coffres dans le pool, "
            f"{t.changes()} changés ; lieux : {lieux or '?'} ; petites clés : "
            f"{v('cles')}, grandes clés : {v('grandes_cles')}, cartes et boussoles : "
            f"{v('cartes_donjon')}, Triforce : {v('triforce')}"
            f" ; cadeaux de personnages : {len(t.entrees_n2)}"
            + (" (pack avec le stub N2, bit 7, exigé chez chaque joueur)" if t.n2_requis else "")
            + f" ; courrier, boutique, enchères et personnages par écriture de données : {len(t.entrees_don)}"
            + (" (client à jour exigé chez chaque joueur)" if t.don_requis else "")
            + (f" ; lieux du randomizer par fichiers (lot 6) : {len(t.entrees_fich)} (pack WWMP_Rando généré "
               "exigé chez chaque joueur)" if getattr(t, "entrees_fich", None) else "")
            + (f" ; coffres en conflit : {len(t.entrees_conflit)}"
               + (" (pack avec la zone des coffres en conflit exigé chez chaque joueur)" if t.conflit_requis else "")
               if getattr(t, "entrees_conflit", None) else "")
            + (" ; objets progressifs : " + ", ".join(
                f"{nom} ({len(cles)} lieux, compte commun tenu par le serveur)"
                for nom, (_p, cles) in sorted(t.prog_familles().items()))
               + " (pack avec la zone des objets progressifs exigé chez chaque joueur, coffres et inventaire partagés)"
               if getattr(t, "prog_requis", False) else "")
            + (f" ; objets de donjon hors de leur donjon : {len(t.hors)} coffres (donnés par le serveur au bon "
               "donjon ; petites clés et coffres partagés exigés chez chaque joueur)"
               if getattr(t, "hors_requis", False) else "")
            + ("" if t.res is not None else " ; table d'autres données de logique, gardée telle quelle"))


async def amain(host: str, port: int, stats: float = 0.0, busy_defer: bool = True, tsw: bool = True,
                drops: bool = True, sticks: bool = True, hote: str | None = None,
                arret_fichier: str | None = None, keys: bool = True, keys_file: str | None = None,
                coffres: bool = True, inventaire: bool = True, cadeaux: bool = True, boss: bool = True,
                max_players: int = MAX_PLAYERS, ghost_hits: bool = True, mobiles: bool = True,
                compteurs: bool = True, carry: bool = True, mode_hanteurs: bool = False,
                hanteurs_cfg: dict | None = None, rares: bool = True, monde: bool = True, herbe: bool = True,
                deathlink: bool = False, mot_de_passe: str | None = None, mdp_source: str = "",
                mises_a_jour: str | None = None, donnees: str | None = None,
                monde_persistant: "mpm.Monde | None" = None, rando_graine: str | None = None,
                rando_spoiler: str | None = None, difficulte: dict | None = None,
                difficulte_on: bool = True, rando_mode: str = "sur",
                rando_options: dict | None = None, trackeur: bool = True,
                trackeur_deduire: bool = False, pnj: bool = True,
                admin: "adm.Admin | None" = None, autorite_preferee: str | None = None,
                **opts) -> str | None:
    """Renvoie « redemarrer » si l'administrateur a demandé un redémarrage (serveur/admin.py), sinon None."""
    try:
        srv, server = await start(host, port, busy_defer, tsw, drops, sticks, hote, keys, keys_file, coffres,
                                  inventaire, cadeaux, boss, max_players, ghost_hits=ghost_hits, mobiles=mobiles,
                                  compteurs=compteurs, carry=carry, mode_hanteurs=mode_hanteurs,
                                  hanteurs_cfg=hanteurs_cfg, rares=rares, monde=monde, herbe=herbe,
                                  deathlink=deathlink, mot_de_passe=mot_de_passe,
                                  monde_persistant=monde_persistant, rando_graine=rando_graine,
                                  difficulte=difficulte, difficulte_on=difficulte_on,
                                  rando_mode=rando_mode, rando_options=rando_options,
                                  trackeur=trackeur, trackeur_deduire=trackeur_deduire, pnj=pnj,
                                  autorite_preferee=autorite_preferee, **opts)
    except OSError as e:
        log(f"ÉCHEC : impossible d'écouter sur {host}:{port} ({e}). Un autre serveur tourne déjà sur ce "
            "port ? Ferme-le, ou choisis un autre --port.")
        raise SystemExit(1)
    srv.maj = maj_serveur.ouvrir(mises_a_jour, donnees, TOOLS, log)
    srv.admin = admin
    srv.tache_principale = asyncio.current_task()
    # arrêt propre (CTRL_BREAK, fichier d'arrêt) installé AVANT la ligne « en écoute » : le lanceur et les tests
    # attendent cette ligne pour pouvoir arrêter le serveur ; un CTRL_BREAK reçu entre la ligne et l'installation
    # tuait le processus sans « bye » ni écriture du monde (vu sous charge : tests d'arrêt propre instables)
    surveillance = arret.installer(asyncio.current_task(), arret_fichier)  # noqa: F841 (référence gardée)
    addrs = ", ".join(str(s.getsockname()) for s in server.sockets)
    log(f"serveur wwmp v{VERSION} (outils {CLIENT_VERSION}) en écoute sur {addrs} (Ctrl+C pour arrêter)")
    ips = local_ips()
    if ips:
        log("adresses de ce PC à donner aux autres joueurs : " + ", ".join(f"{ip} ({ip_hint(ip)})" for ip in ips))
    log("sur ce PC lui-même, le client se connecte à 127.0.0.1")
    if srv.acces.actif:
        log(f"mot de passe de partie : activé ({mdp_source or '?'}) ; il n'est jamais écrit dans ce journal")
        if srv.acces.longueur < acces.MDP_CONSEILLE:
            log(f"ATTENTION : mot de passe court ({srv.acces.longueur} caractères) : {acces.MDP_CONSEILLE} ou plus "
                "conseillés si le port est ouvert sur Internet")
    else:
        log("pas de mot de passe de partie (réseau local / VPN). Pour Internet : docs/hotes/JOUER_PAR_INTERNET.md")
    if hote:
        log(f"rejoindre (A15) : l'hôte est le joueur {hote}")
    log(f"validation côté serveur : « {srv.valid.mode} » ("
        + {"journal": "anomalies notées seulement, rien n'est refusé", "refus": "coups impossibles et messages en trop "
           "REFUSÉS", "off": "désactivée"}[srv.valid.mode] + ")")
    if srv.pref.actif:
        log(f"PC d'autorité préféré : {srv.pref.texte()} ; il prend l'autorité des zones calmes où il est, et la "
            "rend s'il ne répond plus (serveur/autorite.py)")
    if admin is not None:
        log(f"administration à distance : compte « {admin.utilisateur} » ({admin.source_compte}) ; configuration "
            f"{admin.chemin}")
    mp = srv.mp
    if mp is not None:
        log(f"MONDE PERSISTANT « {mp.nom} » (id {mp.id}) : {mp.chemin} ; écrit toutes les {mp.ecriture_min:.0f} s au "
            "plus et à l'arrêt ; « Copier la partie » donne l'image du monde"
            + ("" if mp.quarantaine_on else " ; quarantaine désactivée")
            + (" ; clients sans identifiant de monde refusés" if mp.strict else "")
            + " ; heure commune reprise du monde")
        if srv.hanteurs is not None:
            log("MODE HANTEURS sur ce monde : session temporaire. Ce que les Link gagnent entre dans le monde comme "
                "en coop normale ; rien d'un Hanteur n'y entre ; la manche (rôles, points, possessions) n'est jamais "
                "gardée et repart de zéro au redémarrage ; seuls les réglages Hanteurs sont gardés"
                + (" ; image du monde permise aux anciens Hanteurs (--monde-image-anciens-hanteurs)"
                   if mp.image_anciens else ""))
        if mp.neuf:
            mp.ecrire(srv, "création")
    if srv.rando is not None:
        t = srv.rando
        if t.mode == "complet":
            log(rando_annonce_complet(t))
            log("randomizer complet : un joueur sans le pack v19 à jour (bit 6) ou avec --no-rando est refusé (ses "
                "coffres resteraient ceux du jeu et des objets de progression seraient perdus)")
            srv.rando_indices_maj(annoncer=False)     # monde relu : indices déjà gagnés par les coffres ouverts
            log(f"randomizer complet : {srv.rando_indices_total()} indices, {srv.rando_indices_n} dévoilé(s) ; un de "
                "plus au premier coffre ouvert dans chaque donjon ou zone, et tous les "
                f"{RANDO_INDICE_TOUS_LES} coffres (onglet « Indices » du lanceur)")
        else:
            log(f"RANDOMIZER (palier 1, objets sans progression entre coffres) : graine « {t.graine} », empreinte "
                f"{t.empreinte}, {len(t.entrees)} coffres dans le pool, {t.changes()} changés")
        srv.rando_spoiler_ecrire(rando_spoiler or (mp.dossier if mp is not None else donnees))
        if mp is not None and mp.sale:
            mp.ecrire(srv, "randomizer")
    if not keys:
        log("petites clés partagées : désactivées (--no-keys)")
    elif keys_file:
        log(f"petites clés partagées : état gardé dans {keys_file}")
    if not coffres:
        log("coffres partagés : désactivés (--no-coffres)")
    if not inventaire:
        log("inventaire partagé : désactivé (--no-inventaire)")
    if not cadeaux:
        log("quarts de cœur partagés : désactivés (--no-cadeaux)")
    if not boss:
        log("PV de boss partagés : désactivés (--no-boss-pv)")
    if not ghost_hits:
        log("fantôme touchable : coups des monstres sur les fantômes non relayés (--no-ghost-hits)")
    if not mobiles:
        log("objets mobiles (blocs, explosions, bombes-fleurs, plateformes) : non relayés (--no-mobiles)")
    if not compteurs:
        log("compteurs communs (rubis, flèches, bombes) : désactivés (--no-compteurs)")
    if not carry:
        log("objets portés (jarres, pierres, bombes au-dessus du fantôme) : non relayés (--no-carry)")
    if srv.hanteurs is not None:
        c = srv.hanteurs.cfg
        log(f"MODE HANTEURS : {c['liens']} Link et {c['hanteurs']} Hanteur(s) attendus"
            + (f" (Hanteurs choisis : {', '.join(c['choisis'])})" if c["choisis"] else " (tirés au hasard)")
            + f", rotation {c['rotation']}" + (f" ({c['rotation_min']} min)" if c["rotation"] in ("minuterie", "mixte")
                                               else "")
            + f", points « {c['points']} », espèces : "
            + ", ".join(e["nom"] for e in hmod.especes_permises(c).values()))
    log(f"jusqu'à {max_players} joueurs (fantômes affichés : 7 avec le graphic pack v19, 4 avec le v17, "
        "les plus proches)")
    stats_task = asyncio.create_task(srv.stats_loop(stats)) if stats > 0 else None  # noqa: F841 (référence gardée)
    monde_task = asyncio.create_task(monde_boucle(srv)) if srv.mp is not None or srv.monde.vivant else None  # noqa: F841
    if srv.monde.vivant:
        log("MONDE VIVANT (--monde-vivant) : l'heure commune continue d'avancer quand il n'y a personne "
            "(un jour de jeu dure 10 minutes)")
    for r in srv.robots:
        if r.gest is not None:
            r.gest.hote = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
            r.gest.port = server.sockets[0].getsockname()[1] if server.sockets else port
    g = srv.robot.gest
    if srv.robot.essai:
        log("ESSAI ROBOT (--robot essai, docs/etudes/joueur_robot.md §11) : une 2e instance du jeu, cachée, fait "
            "autorité dans ta zone même si tu es seul, et te suit ; tes monstres y sont des marionnettes pilotées par "
            f"elle ; latence ajoutée {srv.robot.latence} ms d'aller-retour ; "
            + ("processus : " + (g.argv()[0] if g.argv() else "?") if g is not None
               else "aucune --robot-commande : le serveur attend un robot lancé à part (clé : --robot-cle)"))
    elif srv.robot.hote:
        log(f"SERVEUR HÔTE (--robot hote, docs/etudes/serveur_jeu_v2.md) : le serveur fait tourner le jeu pour chaque "
            f"zone occupée, même par un seul joueur ({len(srv.robots)} zone(s) à la fois au plus, --robots ; au-delà, "
            f"un joueur reste l'hôte de sa zone) ; un jeu arrêté après {srv.robot.repos_arret:.0f} s de repos ; "
            + ("processus : " + (g.argv()[0] if g.argv() else "?") if g is not None
               else "aucune --robot-commande : le serveur attend des robots lancés à part (clé : --robot-cle)"))
    elif srv.robot.actif:
        log("JOUEUR ROBOT (--robot auto, docs/etudes/joueur_robot.md) : il prend l'autorité de la zone qui a le plus de "
            f"joueurs (2 au moins), dort sinon ; arrêté après {srv.robot.repos_arret:.0f} s de repos ; "
            + ("processus : " + (g.argv()[0] if g.argv() else "?") if g is not None
               else "aucune --robot-commande : le serveur attend un robot lancé à part (clé : --robot-cle)"))
    # toujours : le robot peut être allumé à chaud depuis « Administrer le serveur » (robot_tick ne fait rien sur off)
    robot_task = asyncio.create_task(robot_boucle(srv))  # noqa: F841 (référence gardée)
    courses_task = asyncio.create_task(courses_boucle(srv))  # noqa: F841 (courses en bateau : phases, faux rivaux)
    carte_task = asyncio.create_task(carte_boucle(srv))  # noqa: F841 (carte marine partagée : positions en mer)
    # A16 : on n'utilise pas serve_forever() : annulé, il attend la fermeture de TOUTES les
    # connexions (Python ≥ 3.12) avant qu'on puisse envoyer « bye ». Le serveur écoute déjà.
    try:
        await asyncio.get_running_loop().create_future()      # jusqu'à Ctrl+C / CTRL_BREAK / fichier
    except asyncio.CancelledError:
        tache = asyncio.current_task()
        if hasattr(tache, "uncancel"):
            tache.uncancel()
        if srv.mp is not None:
            for p in list(srv.players.values()):
                if p.joueur:
                    srv.mp.depart(srv, p)               # dernière connexion, temps de jeu, lieu des présents
            srv.mp.ecrire(srv, "arrêt du serveur")      # avant le « bye » : rien ne change plus après
        n = await srv.au_revoir()
        log(f"arrêt du serveur : {n} joueur(s) prévenu(s) (« {BYE_REDEMARRAGE if srv.redemarrage else BYE_MSG} »)")
    finally:
        if srv.mp is not None and srv.mp.sale:
            srv.mp.ecrire(srv, "arrêt")
        if robot_task is not None:
            robot_task.cancel()
        for r in srv.robots:
            if r.gest is not None:
                r.gest.arreter("arrêt du serveur")
        server.close()
        try:
            await asyncio.wait_for(server.wait_closed(), 2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
    return "redemarrer" if srv.redemarrage else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--stats", type=float, default=0.0, help="affiche le débit par joueur toutes les N s")
    ap.add_argument("--no-busy-defer", action="store_true",
                    help="A11 désactivé : l'autorité d'une zone peut passer à un joueur en cinématique")
    ap.add_argument("--no-tsw", action="store_true",
                    help="A7 désactivé : les switches temporaires et ennemis tués (tsw) ne sont pas relayés")
    ap.add_argument("--no-drops", action="store_true",
                    help="A4/A6 désactivés : objets apparus (drop) et jarres cassées (break) non relayés")
    ap.add_argument("--no-sticks", action="store_true",
                    help="A3 désactivé : les bâtons (stick : prise, pose, conflit) ne sont ni arbitrés ni relayés")
    ap.add_argument("--arret-fichier", default=None, metavar="CHEMIN",
                    help="A16 : s'arrête proprement (message bye) quand ce fichier apparaît (compagnon, tests)")
    ap.add_argument("--hote", metavar="NOM", default=None,
                    help="A15 : nom du joueur dont la progression est copiée chez qui lance wwmp_rejoindre "
                         "(défaut : le client lancé avec --hote, sinon celui de ce PC)")
    ap.add_argument("--no-keys", "--no-share-keys", dest="no_keys", action="store_true",
                    help="synchro v2 désactivée : les petites clés ne sont pas partagées (chacun son compteur)")
    ap.add_argument("--keys-file", metavar="CHEMIN", default=None,
                    help="synchro v2 : garde les clés comptées (coffres, portes) dans ce fichier JSON ; sans "
                         "lui, tout est en mémoire et repart de zéro au redémarrage (au pire une clé de trop)")
    ap.add_argument("--no-coffres", "--no-chests", dest="no_coffres", action="store_true",
                    help="synchro v2 désactivée : les coffres ouverts ne sont ni gardés ni relayés (chacun ouvre "
                         "les siens)")
    ap.add_argument("--no-inventaire", "--no-inventory", dest="no_inventaire", action="store_true",
                    help="synchro v2 désactivée : l'inventaire (objets, équipement, chants, plafonds, sac de "
                         "livraison) n'est ni gardé ni relayé (chacun le sien)")
    ap.add_argument("--no-cadeaux", "--no-gifts", dest="no_cadeaux", action="store_true",
                    help="synchro v2 désactivée : les quarts de cœur au sol ramassés ne sont ni gardés ni relayés "
                         "(chacun ramasse le sien)")
    ap.add_argument("--no-boss-pv", "--no-boss", dest="no_boss_pv", action="store_true",
                    help="synchro v2 désactivée : les PV de boss ne sont ni gardés ni relayés (chacun son "
                         "boss, comme avant)")
    ap.add_argument("--no-ghost-hits", action="store_true",
                    help="synchro v2 désactivée : les coups des monstres sur le fantôme d'un joueur (chez "
                         "l'autorité de la zone) ne sont pas relayés à ce joueur (player_hit)")
    ap.add_argument("--no-rares", action="store_true",
                    help="synchro v3 désactivée : horloge des cycles du stage (marée, jets de lave) et piliers "
                         "de la Tour des Dieux (cycle, rare) ni arbitrés ni relayés")
    ap.add_argument("--no-monde", action="store_true",
                    help="synchro v3 désactivée : heure, vent et météo de la mer (monde, vent) ni arbitrés ni "
                         "relayés")
    ap.add_argument("--no-pnj", action="store_true",
                    help="synchro v4 désactivée : positions des PNJ qui se déplacent (pnj) non relayées")
    ap.add_argument("--no-herbe", action="store_true",
                    help="synchro v3 désactivée : touffes d'herbe coupées (herbe) ni gardées ni relayées")
    ap.add_argument("--deathlink", action="store_true",
                    help="règle de la partie : si un joueur meurt (Game Over), tout le monde meurt ; imposée à tous "
                         "les clients (mode Hanteurs : seuls les Link comptent). Désactivée par défaut")
    ap.add_argument("--no-deathlink", action="store_true",
                    help="deathlink désactivé même avec --deathlink")
    ap.add_argument("--vie-partagee", action="store_true",
                    help="règle de la partie : une seule barre de vie pour tout le groupe (chaque dégât et chaque "
                         "soin d'un joueur vaut pour tous ; à 0, tout le monde meurt) ; imposée à tous les clients ; "
                         "jamais avec le mode Hanteurs. Désactivée par défaut")
    ap.add_argument("--no-vie-partagee", action="store_true",
                    help="vie partagée désactivée même avec --vie-partagee")
    ap.add_argument("--degats", type=int, choices=list(dif_mod.DEGATS), default=1,
                    help="difficulté, règle de la partie : dégâts reçus par les Link multipliés (1 = jeu normal, "
                         "défaut ; 2, 3 ou 4). Demande le graphic pack à jour chez chaque joueur")
    ap.add_argument("--pv-monstres", choices=list(dif_mod.PV_MODES), default="off",
                    help="difficulté, règle de la partie : vie des monstres selon le nombre de Link de la partie "
                         "(off = jeu normal, défaut ; demi = +50 %% par Link en plus ; link = ×1 par Link) ; "
                         "jamais les boss, jamais plus de 127")
    ap.add_argument("--no-difficulte", action="store_true",
                    help="difficulté coupée : jeu normal, --degats et --pv-monstres ignorés, réglage du lanceur "
                         "refusé")
    ap.add_argument("--rando-graine", metavar="GRAINE", default=None,
                    help="randomizer coop, palier 1 (expérimental, désactivé par défaut) : mélange les objets SANS "
                         "progression (rubis, quarts de cœur, cartes au trésor numérotées) entre les coffres, même "
                         "contenu pour tous ; la table part aux clients avec son empreinte. Monde persistant : gardée "
                         "dans le monde à sa première partie rando, jamais recalculée")
    ap.add_argument("--rando-mode", choices=list(rnd.MODES), default="sur",
                    help="randomizer : « sur » (défaut : palier 1, objets sans progression) ou « complet » "
                         "(EXPÉRIMENTAL, jamais essayé en jeu : objets de progression des coffres mélangés avec une "
                         "logique anti-blocage ; tous les joueurs doivent avoir le pack v19 à jour, sinon ils sont "
                         "refusés). Un monde persistant garde le mode de sa première partie rando")
    ap.add_argument("--rando-lieux", metavar="LISTE", default=None,
                    help="randomizer complet : catégories de lieux mélangées, séparées par des virgules, ou « tout » "
                         "(défaut : donjons, grottes_enigme, divers ; aussi : grottes_combat, plateformes, radeaux, "
                         "sous_marins, recifs, enigmes_iles, coffres_tingle, quetes_courtes, labyrinthe ; pour les "
                         "cadeaux de --rando-cadeaux : quetes_longues, mini_jeux, dons, achats_chers)")
    ap.add_argument("--rando-cadeaux", action="store_true",
                    help="randomizer complet : cadeaux de personnages et prix mélangés avec les coffres (13 lieux : "
                         "enchères, tir au canon, photos de Lenzo, Ivan, Maggie, Gorons, équipe de plongeurs ; ceux "
                         "dont la catégorie est dans --rando-lieux ; jamais essayé en jeu ; défaut : d'origine)")
    ap.add_argument("--rando-lieux-courrier", action="store_true",
                    help="randomizer complet : objets de 6 lettres mélangés (mère de Baito, père de Komali, Orca, "
                         "Mémé, Arielle, Baito ; octets de la table du courrier écrits par le client ; jamais essayé "
                         "en jeu ; défaut : d'origine)")
    ap.add_argument("--rando-lieux-boutiques", action="store_true",
                    help="randomizer complet : étals à 500 et 950 rubis de la boutique de l'Île du Pic mélangés "
                         "(octets écrits par le client ; jamais essayé en jeu ; défaut : d'origine)")
    ap.add_argument("--rando-lieux-encheres", action="store_true",
                    help="randomizer complet : enchères à 40, 5, 60 et 80 rubis mélangées par la table du jeu "
                         "(octets écrits par le client, au lieu du stub N2 de --rando-cadeaux ; jamais essayé en jeu)")
    ap.add_argument("--rando-lieux-pnj", action="store_true",
                    help="randomizer complet : cadeaux de Sam, de Linda et Anton et de Kreeb mélangés (octets écrits "
                         "par le client ; jamais essayé en jeu ; défaut : d'origine)")
    ap.add_argument("--rando-coffres-conflit", action="store_true",
                    help="randomizer complet : les dix coffres dont la clé est partagée avec le coffre d'un autre "
                         "niveau sont mélangés aussi, selon les catégories de --rando-lieux (pack v19 à jour exigé "
                         "chez chaque joueur ; jamais essayé en jeu ; défaut : d'origine)")
    ap.add_argument("--rando-progressifs", action="store_true",
                    help="randomizer complet : objets progressifs. L'arc et les flèches de lumière deviennent deux "
                         "paliers : le premier de leurs coffres ouvert par le groupe donne l'arc, le second les "
                         "flèches de lumière, et le coffre des flèches de lumière est mélangé (pack v19 à jour, "
                         "coffres et inventaire partagés exigés chez chaque joueur ; jamais essayé en jeu ; défaut : "
                         "d'origine)")
    ap.add_argument("--rando-fichiers-demo", action="store_true",
                    help="randomizer complet, option de TEST du randomizer par fichiers : le texte « Appuyez sur un "
                         "bouton » de l'écran titre est changé par le pack WWMP_Rando GÉNÉRÉ sur chaque PC (lanceur : "
                         "Randomizer, Générer, Installer) ; chaque joueur doit avoir le même pack généré (sinon "
                         "refusé) ; jamais essayé en jeu ; défaut : non")
    # randomizer par fichiers, étape 3 (lot 7, rando/lot7.py) : pack WWMP_Rando GÉNÉRÉ, jamais essayé en jeu
    ap.add_argument("--rando-monde", choices=("histoire", "ouvert"), default=None,
                    help="randomizer complet, par fichiers : « ouvert » = monde ouvert de TWWHD (mer libre dès le "
                         "départ, objets de l'histoire donnés à la création de la partie, coffres à la place des "
                         "cinématiques) ; partie NEUVE exigée ; jamais essayé en jeu ; défaut : histoire")
    ap.add_argument("--rando-depart", metavar="ILE", default=None,
                    help="randomizer complet, monde ouvert : île de départ (nom anglais de TWWHD, ex. « Windfall "
                         "Island »), « hasard » (tirée depuis la graine) ou « origine » (défaut)")
    ap.add_argument("--rando-coffres-cinematiques", action="store_true",
                    help="randomizer complet, par fichiers : coffres à la place des cinématiques (Reine des fées ; "
                         "en monde ouvert aussi Médolie, Jabu, Épée de légende) ; jamais essayé en jeu")
    ap.add_argument("--rando-cinematiques-courtes", action="store_true",
                    help="randomizer complet, par fichiers : Zéphos, enchères et petites caméras raccourcis, chants "
                         "sans animation, Tott une fois ; jamais essayé en jeu")
    ap.add_argument("--rando-pas-de-recombats", action="store_true",
                    help="randomizer complet, par fichiers : combats de souvenir de la Tour de Ganon déjà faits "
                         "(partie neuve exigée) ; jamais essayé en jeu")
    ap.add_argument("--rando-textes", action="store_true",
                    help="randomizer complet, par fichiers : bulles du jeu (vendeurs, enchères, Beedle, numéro des "
                         "cartes) en anglais, français et espagnol ; jamais essayé en jeu")
    # randomizer par fichiers, étape 4 (lot 8, rando/lot8.py) : pack WWMP_Rando GÉNÉRÉ, jamais essayé en jeu
    ap.add_argument("--rando-sans-epee", action="store_true",
                    help="randomizer complet, par fichiers, monde ouvert : aucune épée (barrière d'Hyrule levée, "
                         "Spectre-Ganon blessé par la Masse) ; partie neuve exigée ; jamais essayé en jeu")
    ap.add_argument("--rando-entrees", choices=("aucune", "donjons", "donjons_grottes"), default=None,
                    help="randomizer complet, par fichiers, monde ouvert : entrées mélangées (donjons entre eux ; et "
                         "grottes entre elles), couplées ; le tirage est dans la table, même chez tous ; jamais essayé "
                         "en jeu ; défaut : aucune")
    ap.add_argument("--rando-cartes", "--rando-cartes-melangees", dest="rando_cartes", action="store_true",
                    help="randomizer complet, par fichiers : cartes au trésor mélangées (exige "
                         "--rando-lieux-tresors) ; jamais essayé en jeu")
    ap.add_argument("--rando-depart-melange", default=None, metavar="OBJETS",
                    help="randomizer complet, par fichiers, monde ouvert : objets de départ mélangés dans le monde, "
                         "séparés par des virgules : bombes, perle_din, perle_farore, hymne_terre, hymne_vent, "
                         "baguette, requiem, sac_butin, sac_facteur, flacon, bouclier (exige une famille du lot 6 : "
                         "trésors, événements, boss, objets posés ou personnages à immédiat ; Baguette ou Requiem : la "
                         "logique compte qu'on navigue avec la seule voile ; bouclier : exige "
                         "--rando-boucliers-progressifs ; la voile, le Talisman et les épées restent au départ) ; "
                         "partie neuve exigée ; jamais essayé en jeu")
    ap.add_argument("--rando-progressifs-plus", default=None, metavar="FAMILLES",
                    help="randomizer complet, par fichiers, portage natif (mod 0.40.0) : objets progressifs de TWWHD, "
                         "séparés par des virgules : epee, magie (monde ouvert), boite, bourse, carquois, bombes "
                         "(Grandes Fées mélangées) ; chaque palier trouvé donne le suivant, le serveur compte les dons "
                         "du groupe (inventaire partagé exigé) ; jamais essayé en jeu")
    ap.add_argument("--rando-boucliers-progressifs", action="store_true",
                    help="randomizer complet, par fichiers : boucliers progressifs (le bouclier du héros donne le "
                         "bouclier miroir à qui a déjà un bouclier, jamais de rétrogradation ; le coffre du miroir "
                         "reste fixe) ; permet --rando-depart-melange bouclier ; jamais essayé en jeu")
    # randomizer, étape 5 (rando/lot9.py), jamais essayé en jeu ; défaut : d'origine
    ap.add_argument("--rando-lieux-kane", action="store_true",
                    help="randomizer complet : les quatre récompenses de Kane (décorations de Mercantîle) mélangées "
                         "(octets écrits par le client)")
    ap.add_argument("--rando-courrier-tout", action="store_true",
                    help="randomizer complet : les six autres lettres avec objet mélangées (exige "
                         "--rando-lieux-courrier ; octets écrits par le client)")
    ap.add_argument("--rando-lieux-encheres-100", action="store_true",
                    help="randomizer complet : l'enchère à 100 rubis (voile rapide) mélangée (octets écrits par le "
                         "client ; bit « déjà gagné » 0x6B01 partagé)")
    ap.add_argument("--rando-boutique-epuise", action="store_true",
                    help="randomizer complet, par fichiers : étals de l'Île du Pic « épuisés » par emplacement (l'étal "
                         "à 950 rubis ne se rachète plus ; exige --rando-lieux-boutiques ; pack WWMP_Rando GÉNÉRÉ)")
    for _fam, _aide in (("tresors", "57 trésors engloutis (et Big Octo, canonnières, terre noire)"),
                        ("evenements", "8 objets donnés par une liste d'événements, chants compris"),
                        ("boss", "6 réceptacles des boss"),
                        ("fees", "6 Grandes Fées (bourse, sacs de bombes et carquois échangés entre elles)"),
                        ("sol", "12 objets posés, pots et têtes de pierre"),
                        ("pnj-code", "15 personnages dont l'objet est dans le code, potions du Dr Bandam, Zunari"),
                        ("beedle", "Beedle à 20 rubis et carte à 900 rubis de l'Île du Pic")):
        ap.add_argument(f"--rando-lieux-{_fam}", action="store_true",
                        help=f"randomizer complet, randomizer par fichiers (lot 6) : {_aide} mélangés par le pack "
                             "WWMP_Rando GÉNÉRÉ sur chaque PC (lanceur : Randomizer, Générer, Installer ; même pack "
                             "exigé chez chaque joueur) ; jamais essayé en jeu ; défaut : d'origine")
    ap.add_argument("--rando-cles", action="store_true",
                    help="randomizer complet : petites clés mélangées entre les coffres de LEUR donjon (défaut : à "
                         "leur place d'origine)")
    ap.add_argument("--rando-grandes-cles", action="store_true",
                    help="randomizer complet : grandes clés mélangées entre les coffres de leur donjon")
    ap.add_argument("--rando-cartes-donjon", action="store_true",
                    help="randomizer complet : cartes et boussoles de donjon mélangées entre les coffres de leur donjon")
    for _nom, _quoi in (("cles", "petites clés"), ("grandes-cles", "grandes clés"),
                        ("cartes-donjon", "cartes et boussoles de donjon")):
        ap.add_argument(f"--rando-{_nom}-portee", choices=("donjon", "tout_donjon", "partout"), default=None,
                        help=f"randomizer complet : où vont les {_quoi} mélangées : « donjon » (dans leur donjon, "
                             "défaut), « tout_donjon » (dans un coffre de n'importe quel donjon) ou « partout » (dans "
                             "n'importe quel coffre mélangé). Hors de son donjon, l'objet est donné par le serveur "
                             "au bon donjon, pour tout le groupe (petites clés et coffres partagés exigés chez chaque "
                             "joueur ; le coffre montre un rubis jaune ; jamais essayé en jeu)")
    ap.add_argument("--rando-triforce", action="store_true",
                    help="randomizer complet : fragments de la Triforce des coffres mélangés (défaut : à leur place)")
    ap.add_argument("--rando-exclus", metavar="LIEUX", default=None,
                    help="randomizer complet : lieux qui gardent leur objet (noms du spoiler, séparés par « ; »)")
    ap.add_argument("--rando-indices", metavar="NOMBRES", default=None,
                    help="randomizer complet : nombres d'indices par genre, « chemin=3,sterile=3,objet=3,lieu=2 » "
                         "(défaut ; de 0 à 16 chacun), ou « aucun ». Les indices sont dévoilés au fil des coffres "
                         "ouverts et affichés dans l'onglet « Indices » du lanceur ; ils changent la table (ils font "
                         "partie des options)")
    ap.add_argument("--rando-indices-obscurs", action="store_true",
                    help="randomizer complet : indices tournés en rumeurs (« On raconte que... ») ; même information")
    ap.add_argument("--rando-reglages", metavar="CHAINE", default=None,
                    help="randomizer : chaîne de réglages partageable (« WWMP1.… », donnée par le lanceur ou le "
                         "spoiler) ; elle REMPLACE --rando-mode et toutes les options --rando-* de mélange, pour "
                         "refaire la même partie avec la même graine")
    ap.add_argument("--no-rando", action="store_true",
                    help="randomizer désactivé même avec --rando-graine (un monde qui a déjà sa table la garde dans "
                         "son fichier, mais elle n'est pas envoyée)")
    ap.add_argument("--rando-spoiler", metavar="DOSSIER", default=None,
                    help="randomizer : dossier du spoiler (spoiler_<empreinte>.txt/.json ; défaut : dossier du monde, "
                         "sinon --donnees ; jamais envoyé aux joueurs)")
    ap.add_argument("--no-medailles", action="store_true",
                    help="médailles de fin de donjon coupées (docs/etudes/autres_zelda.md §2 : rien n'est compté)")
    ap.add_argument("--coups-joueurs", action="store_true",
                    help="règle de la partie : un coup d'épée sur un coéquipier le fait reculer (aucun dégât, 1 s "
                         "d'invincibilité) ; jamais avec le mode Hanteurs. Désactivée par défaut")
    ap.add_argument("--no-releve", action="store_true",
                    help="règle de la partie : relever un coéquipier coupé (allumé par défaut, mais seulement avec le "
                         "deathlink et sans la vie partagée : un Link à 0 cœur reste à terre 20 s ; un coéquipier qui "
                         "reste 2 s à moins de 1,5 m le relève avec 2 cœurs, sinon sa vraie mort ; jamais en mode "
                         "Hanteurs)")
    ap.add_argument("--no-camouflage", action="store_true",
                    help="règle de la partie : camouflage de la tenue jaune coupé (allumé par défaut, avec les tenues à "
                         "pouvoir : un Link en tenue jaune qui entre dans une zone est invisible pour les monstres "
                         "--camouflage-duree secondes, ou jusqu'à son premier coup)")
    ap.add_argument("--camouflage-duree", type=float, default=cam_commun.DUREE,
                    help=f"durée du camouflage de la tenue jaune, en secondes (défaut {cam_commun.DUREE:g}, bornée "
                         f"{cam_commun.DUREE_MIN:g} à {cam_commun.DUREE_MAX:g})")
    ap.add_argument("--essai-camouflage", action="store_true",
                    help="ESSAI (Réglages › Essais du lanceur) : un client --essai-camouflage est traité en tenue jaune "
                         "et peut se camoufler à la demande ; pour essayer seul, jamais dans une vraie partie")
    ap.add_argument("--essai-relever", action="store_true",
                    help="ESSAI (Réglages › Essais du lanceur) : un client --essai-relever a un faux coéquipier "
                         "« Essai Secours » qui vient le relever (ou pas) et qui peut tomber à son tour ; pour essayer "
                         "seul, jamais dans une vraie partie")
    ap.add_argument("--no-ralliement", action="store_true",
                    help="règle de la partie : chant de ralliement coupé (allumé par défaut : le morceau 6 de la "
                         "Baguette, 3/4 haut, bas, droite, appelle l'équipe ; une fois toutes les 5 min ; jamais avec "
                         "le mode Hanteurs)")
    ap.add_argument("--no-porter", action="store_true",
                    help="règle de la partie : porter un coéquipier coupé (allumé par défaut : « Soulever » (A) sur le "
                         "fantôme d'un coéquipier ; porté, L3 pour descendre ; portage natif, mod 0.41.0 ; jamais avec "
                         "le mode Hanteurs)")
    ap.add_argument("--no-courses", action="store_true",
                    help="pas de courses en bateau (lancées par le lanceur de l'hôte : parcours tracés en jeu)")
    ap.add_argument("--no-bonheur", action="store_true",
                    help="règle de la partie : Fragments du Bonheur coupés (allumés par défaut : un monstre vaincu donne "
                         "parfois une moitié de médaillon ; deux joueurs à moins de 3 m avec des moitiés assorties les "
                         "assemblent pour une récompense ; jamais avec le mode Hanteurs)")
    ap.add_argument("--bonheur-quarts-donjon", type=int, default=bon_mod.QUARTS_DONJON,
                    help="Fragments du Bonheur : plafond de sécurité des quarts de cœur par donjon (mer et îles : un "
                         "seul « donjon » à part) ; au-delà, le quart tiré devient une pluie de rubis (défaut 1)")
    ap.add_argument("--bonheur-quarts-partie", type=int, default=bon_mod.QUARTS_PARTIE,
                    help="Fragments du Bonheur : plafond de sécurité des quarts de cœur par partie (défaut 4)")
    ap.add_argument("--essai-bonheur", action="store_true",
                    help="ESSAI (Réglages › Essais du lanceur) : 1 chance sur 2 par monstre ; le faux coéquipier d'un "
                         "client --essai-bonheur a toujours la moitié assortie, accepte et propose ; récompenses "
                         "forcées ; quart de cœur simulé ; pour essayer seul, jamais dans une vraie partie")
    ap.add_argument("--no-tenues", action="store_true",
                    help="règle de la partie : tenues à pouvoir coupées (allumées par défaut : chaque couleur de tenue a "
                         "ses pouvoirs ; duos orange + violet et sombre + turquoise partagés quand proches)")
    ap.add_argument("--tenues-distance", type=float, default=ten_commun.DISTANCE,
                    help="tenues à pouvoir : distance (unités du jeu, 100 = 1 m) en dessous de laquelle les deux membres "
                         "d'un duo partagent leurs pouvoirs (défaut 2000 = 20 m ; bornée à 3..100 m)")
    ap.add_argument("--essai-tenues", action="store_true",
                    help="ESSAI (Réglages › Essais du lanceur) : un client --essai-tenues change sa couleur de pouvoir à "
                         "chaud et pose un faux coéquipier de duo ; pour essayer seul, jamais dans une vraie partie")
    ap.add_argument("--essai-fusions", action="store_true",
                    help="ESSAI (Réglages › Essais du lanceur) : un client --essai-fusions pose un faux coéquipier qui "
                         "charge sa tournoyante ou tient une bombe, pour essayer seul les deux fusions des tenues ; "
                         "jamais dans une vraie partie")
    ap.add_argument("--fusion-entrees-hz", type=int, default=fus_commun.ENTREES_HZ,
                    help="vraie fusion des tenues : relevés par seconde des entrées de l'attaquant sur son PC (défaut "
                         f"{fus_commun.ENTREES_HZ} ; borné à {fus_commun.ENTREES_HZ_MIN}..{fus_commun.ENTREES_HZ_MAX}) ; "
                         "envoyées au pilote à chaque changement, et rappelées au moins toutes les 100 ms")
    ap.add_argument("--fusion-peremption-ms", type=int, default=fus_commun.PEREMPTION_MS,
                    help="vraie fusion des tenues : sans nouvel état de l'attaquant pendant ce temps (ms), le jeu du "
                         f"pilote relâche ses boutons et sa caméra (défaut {fus_commun.PEREMPTION_MS} ; borné à "
                         f"{fus_commun.PEREMPTION_MS_MIN}..{fus_commun.PEREMPTION_MS_MAX})")
    # essais « coups entre joueurs » (--essai-coups), « bandeau et médailles » (--essai-medailles), « chant de
    # ralliement » (--essai-ralliement) et « course en bateau » (--essai-course) retirés le 2026-11-17 après validation
    # en jeu : ces options sont refusées (le lanceur ne les passe plus)
    ap.add_argument("--no-trackeur", action="store_true",
                    help="trackeur du randomizer désactivé : le serveur ne tient ni ne publie l'état commun (le "
                         "lanceur ne coche alors que les coffres ouverts)")
    ap.add_argument("--trackeur-deduire", action="store_true",
                    help="trackeur : coche aussi les cadeaux de personnages déduits des objets uniques de l'équipe "
                         "(coupé par défaut : pas encore éprouvé en jeu ; ne coche qu'après l'obtention)")
    ap.add_argument("--no-mobiles", action="store_true",
                    help="synchro v2 désactivée : blocs poussés, explosions, bombes-fleurs et plateformes (mob, "
                         "boom, lift) ne sont ni arbitrés ni relayés")
    ap.add_argument("--no-compteurs", "--no-counters", dest="no_compteurs", action="store_true",
                    help="synchro v2 désactivée : rubis, flèches et bombes ne sont ni totalisés ni relayés (chacun "
                         "les siens ; les drops restent « butin par joueur »)")
    ap.add_argument("--no-carry", action="store_true",
                    help="synchro v2 désactivée : les objets portés (jarre, pierre, bombe au-dessus du fantôme : "
                         "carry) ne sont ni arbitrés ni relayés")
    ap.add_argument("--mode-hanteurs", action="store_true",
                    help="mode de jeu Hanteurs (phase 0) : rôles Link / Hanteur, points, invocations, rotation ; "
                         "désactivé par défaut (coop normale)")
    ap.add_argument("--hanteurs-config", metavar="FICHIER", default=None,
                    help="mode Hanteurs : réglages JSON (liens, hanteurs, choisis, rotation, rotation_min, points, "
                         "especes_hypo, vie_manche, max_invoques, possession, possession_max, pilotage, "
                         "recharge_attaques de 0.5 à 3, pilotage_essai, ...) ; écrit par le lanceur")
    ap.add_argument("--pilotage-essai", metavar="LISTE", default=None,
                    help="mode Hanteurs, possession complète : espèces À L'ESSAI que les Hanteurs peuvent posséder "
                         "et piloter (noms ou numéros séparés par des virgules : stalfos, darknut, keese, kargaroc, "
                         "rat, wizzrobe, poe, magtail, redead, armos, armos knight, octorok, boko baba ; « toutes ») ; désactivé par défaut ; remplace le réglage pilotage_essai du fichier")
    ap.add_argument("--autorite-preferee", metavar="NOMS", default=None,
                    help="lot S1 : PC d'autorité préféré : pseudo(s) séparés par des virgules (ordre de priorité), ou "
                         "« @hote » (client lancé avec --hote, sinon celui de ce PC) ; il prend l'autorité des "
                         "monstres des zones calmes où il est présent, au lieu du joueur présent depuis le plus "
                         "longtemps, et la rend s'il part ou ne répond plus. Défaut : aucun (comme avant). Serveur "
                         "dédié : réglé dans « Administrer le serveur » (config.json)")
    ap.add_argument("--validation", choices=list(valid_mod.MODES), default=valid_mod.DEFAUT,
                    help="lot S2 : validation côté serveur (vitesse plausible des Link, distance des coups, débits par "
                         "message) : « journal » (défaut : anomalies notées, rien n'est refusé ; lanceur : « Administrer "
                         "le serveur »), « refus » (coups impossibles et messages en trop refusés ; après un essai à "
                         "plusieurs PC seulement) ou « off ». Serveur dédié : réglé dans config.json")
    ap.add_argument("--robot", choices=list(robot_mod.MODES), default=robot_mod.DEFAUT,
                    help="joueur robot (docs/etudes/joueur_robot.md) : « auto » = une instance du jeu sans joueur, sur "
                         "ce serveur, fait autorité dans la zone qui a le plus de joueurs (2 au moins) et dort sinon ; "
                         "« off » (défaut) : comme avant ; « essai » : essai solo sur un serveur hébergé sur ce PC (le "
                         "robot prend la zone du joueur même seul, sans gain exigé ni repos, et le suit ; jamais en "
                         "serveur dédié)")
    ap.add_argument("--robots", type=int, default=1, metavar="N",
                    help=f"nombre de robots (1 à {robot_mod.ROBOTS_MAX}, défaut 1) : un jeu caché par zone tenue, "
                         "environ 1 Go de mémoire chacun (docs/etudes/serveur_jeu_v2.md) ; « --robot hote » : le "
                         "serveur est l'hôte de toute zone occupée, même par un seul joueur")
    ap.add_argument("--robot-essai-latence", type=int, default=0, metavar="MS",
                    help=f"--robot essai : ms d'aller-retour ajoutées entre le serveur et le robot (MS/2 dans chaque "
                         f"sens ; 0 à {robot_mod.LATENCE_MAX}, défaut 0) : simule un gérant éloigné")
    ap.add_argument("--robot-commande", metavar="CMD", default=None,
                    help="commande qui lance le robot ({hote}, {port}, {cle}, {python} remplacés ; la clé est aussi "
                         "dans WWMP_ROBOT_CLE) ; sans elle, le serveur attend un robot lancé à part")
    ap.add_argument("--robot-portage", metavar="DOSSIER", default=None,
                    help="dossier de la release du portage pour le robot : la commande est alors celle du dépôt "
                         "(tools/client/robot.py : portage caché + client --robot ; docs/hotes/ESSAYER_LE_ROBOT.md du dépôt du mod)")
    ap.add_argument("--robot-cle", metavar="CLE", default=None,
                    help="clé du robot (sinon WWMP_ROBOT_CLE, sinon tirée au hasard) ; jamais journalisée")
    ap.add_argument("--robot-repos", type=float, default=robot_mod.REPOS_ARRET, metavar="S",
                    help=f"s de repos avant d'arrêter le processus du robot (défaut {robot_mod.REPOS_ARRET:.0f} ; 0 : "
                         "tout de suite)")
    ap.add_argument("--robot-journal", metavar="FICHIER", default=None,
                    help="sortie du processus du robot ajoutée à ce fichier (défaut : jetée)")
    ap.add_argument("--max-joueurs", type=int, default=MAX_PLAYERS, metavar="N",
                    help=f"nombre de joueurs au plus (défaut {MAX_PLAYERS}, de 1 à {MAX_PLAYERS})")
    ap.add_argument("--mot-de-passe", metavar="MDP", default=None,
                    help="Internet : mot de passe de la partie (visible dans la liste des processus : préfère "
                         "--mot-de-passe-fichier ou la variable WWMP_MOT_DE_PASSE)")
    ap.add_argument("--mot-de-passe-fichier", metavar="CHEMIN", default=None,
                    help="Internet : lit le mot de passe de la partie dans ce fichier (première ligne)")
    ap.add_argument("--donnees", metavar="DOSSIER", default=None,
                    help="serveur dédié : dossier de données (clés partagées cles.json, mot_de_passe.txt créé au "
                         "premier lancement s'il n'y a pas d'autre mot de passe)")
    ap.add_argument("--sans-mot-de-passe", action="store_true",
                    help="pas de mot de passe, même avec --donnees ou WWMP_MOT_DE_PASSE (réseau local ou VPN)")
    ap.add_argument("--generer-mot-de-passe", action="store_true",
                    help="affiche un mot de passe au hasard, facile à dicter, et quitte")
    ap.add_argument("--journal", metavar="FICHIER", default=None,
                    help="serveur dédié : journal dans ce fichier au lieu de la console (tâche planifiée "
                         "Windows) ; au-delà de 5 Mo, l'ancien devient FICHIER.1")
    ap.add_argument("--mises-a-jour", metavar="DOSSIER", default=None,
                    help="distribue aux lanceurs le paquet signé de ce dossier (dernier.json + zip, fait par "
                         "wwmp_publier.py) ; défaut avec --donnees : DONNEES/mises_a_jour (relu à chaque demande)")
    ap.add_argument("--monde", metavar="NOM_OU_ID", default=None,
                    help="monde persistant (docs/etudes/monde_persistant.md) : le serveur garde tout l'état partagé "
                         "(progression, clés, coffres, quarts, inventaire, rubis/flèches/bombes, vent, deathlink, image "
                         "du monde) dans DOSSIER_DES_MONDES/<id>/monde.json, le relit au démarrage, et « Copier la "
                         "partie » donne l'image du monde, sans hôte. Créé s'il n'existe pas. Automatique avec "
                         "--donnees (monde « monde ») sauf --sans-monde")
    ap.add_argument("--monde-nom", metavar="NOM", default=None,
                    help="monde persistant : nom d'un monde CRÉÉ maintenant (--monde ID donné par le lanceur) ; sans effet "
                         "sur un monde existant")
    ap.add_argument("--monde-dossier", metavar="DOSSIER", default=None,
                    help="dossier des mondes (défaut : DONNEES/mondes avec --donnees, sinon ./mondes)")
    ap.add_argument("--sans-monde", action="store_true",
                    help="pas de monde persistant, même avec --donnees (ancien comportement : tout en mémoire)")
    ap.add_argument("--monde-strict", action="store_true",
                    help="monde persistant : refuse aussi les clients de jeu qui n'annoncent aucun monde (lancés à la "
                         "main) ; ceux d'un autre monde sont toujours refusés")
    ap.add_argument("--no-monde-quarantaine", action="store_true",
                    help="monde persistant : pas de garde de contenu (une partie qui ne ressemble pas au monde y entre)")
    ap.add_argument("--monde-heure", action="store_true",
                    help="sans effet (gardée pour les anciens réglages) : l'horloge commune du serveur repart TOUJOURS de "
                         "l'heure gardée par le monde persistant")
    ap.add_argument("--monde-vivant", action="store_true",
                    help="le monde vit sans joueur (docs/etudes/serveur_jeu_v2.md) : l'heure commune continue d'avancer "
                         "quand il n'y a personne (un jour de jeu dure 10 minutes) ; sans cette option elle s'arrête "
                         "quand le dernier joueur part")
    ap.add_argument("--monde-image-anciens-hanteurs", action="store_true",
                    help="monde persistant + mode Hanteurs : l'image du monde peut être demandée à un joueur qui a été "
                         "Hanteur puis est redevenu Link (désactivé par défaut : sa sauvegarde contient ce que son Link "
                         "caché a ramassé en Hanteur ; jamais essayé en jeu)")
    ap.add_argument("--monde-ecriture", type=float, default=mpm.ECRITURE_MIN, metavar="S",
                    help=f"monde persistant : secondes au plus entre deux écritures (défaut {mpm.ECRITURE_MIN:.0f})")
    ap.add_argument("--config", metavar="FICHIER", default=None,
                    help="serveur dédié Docker : configuration de la partie gardée dans ce fichier (config.json du "
                         "volume) et réglable à distance avec le compte admin (lanceur : Héberger, « Administrer le "
                         "serveur » ; compte : WWMP_ADMIN_UTILISATEUR / WWMP_ADMIN_MOT_DE_PASSE, sinon admin.txt créé "
                         "dans le dossier de données). Créée au premier démarrage depuis --monde, --rando-graine, "
                         "--rando-reglages, --mode-hanteurs, --hanteurs-config, --max-joueurs, --deathlink, --degats, "
                         "--pv-monstres ; ensuite, elle l'emporte sur ces options. Le mot de passe de partie est alors "
                         "celui du fichier mot_de_passe.txt du dossier de données (WWMP_MOT_DE_PASSE ne sert qu'à le "
                         "créer)")
    ap.add_argument("--lister-mondes", action="store_true",
                    help="affiche les mondes du dossier des mondes et quitte")
    args = ap.parse_args(argv)
    if args.generer_mot_de_passe:
        print(acces.generer())
        return 0
    dossier_mondes = args.monde_dossier or (os.path.join(args.donnees, "mondes") if args.donnees
                                            else os.path.abspath("mondes"))
    if args.lister_mondes:
        lst = mpm.lister(dossier_mondes)
        print(f"mondes de {dossier_mondes} : {len(lst)}")
        for m in lst:
            print(f"  {m['id']}  « {m.get('nom')} »  modifié {m.get('modifie') or '?'}  joueurs : "
                  f"{', '.join(m.get('joueurs') or []) or 'aucun'}" + (f"  ERREUR {m['erreur']}" if m.get("erreur") else ""))
        return 0
    if args.journal:
        serveur_dedie.ouvrir_journal(args.journal)
    args.max_joueurs = max(1, min(MAX_PLAYERS, args.max_joueurs))
    hcfg = None
    if args.hanteurs_config:
        import json
        try:
            with open(args.hanteurs_config, encoding="utf-8") as f:
                hcfg = json.load(f)
        except (OSError, ValueError) as e:
            print(f"erreur : réglages Hanteurs illisibles ({args.hanteurs_config}) : {e}", file=sys.stderr)
            return 2
    admin_srv = None
    if args.robot == "essai" and (args.config or args.donnees):
        # l'essai solo lance une 2e instance du jeu sur CE PC : jamais sur un serveur dédié (config.json ne le connaît pas)
        log("robot : --robot essai refusé sur un serveur dédié (--config / --donnees) : robot coupé")
        args.robot = "off"
    if args.config:
        # serveur dédié Docker : config.json du volume (créé depuis les options au premier démarrage, puis il l'emporte)
        f_mdp = serveur_dedie.fichier_mdp_volume(args)
        if not os.path.exists(args.config) and f_mdp and acces.mdp_env():
            # migration : jusqu'ici WWMP_MOT_DE_PASSE (.env) l'emportait sur mot_de_passe.txt ; il y est repris pour que
            # le mot de passe de partie ne change pas (ensuite, le fichier du volume l'emporte : réglé par le lanceur)
            try:
                acces.ecrire_fichier(f_mdp, acces.mdp_env())
            except OSError as e:
                print(f"erreur : mot de passe : {e}", file=sys.stderr)
                return 2
        try:
            cfg_vol, hcfg = adm.preparer(args.config, args, ap.parse_args([]), hcfg, MAX_PLAYERS, log=log)
        except (adm.ConfigIllisible, OSError) as e:
            log(f"ÉCHEC : configuration : {e}")
            print(f"erreur : configuration : {e}", file=sys.stderr)
            return 2
        args.max_joueurs = max(1, min(MAX_PLAYERS, args.max_joueurs))
        try:
            u_admin, m_admin, src_admin = adm.compte(args.donnees or os.path.dirname(os.path.abspath(args.config)),
                                                     log=log)
        except OSError as e:
            print(f"erreur : compte admin : {e}", file=sys.stderr)
            return 2
        admin_srv = adm.Admin(u_admin, m_admin, args.config, cfg_vol, dossier_mondes=dossier_mondes,
                              source_compte=src_admin)
        del m_admin
        if args.donnees and not args.mises_a_jour:
            # tout est dans le volume : les mises à jour du mod s'y copient (docker compose cp, docs/joueurs/MISES_A_JOUR.md)
            os.makedirs(os.path.join(args.donnees, "mises_a_jour"), exist_ok=True)
    if args.pilotage_essai is not None:
        hcfg = dict(hcfg) if isinstance(hcfg, dict) else {}
        hcfg["pilotage_essai"] = args.pilotage_essai
    try:
        mdp, mdp_source = serveur_dedie.mot_de_passe_serveur(args, volume=bool(args.config))
    except (OSError, ValueError) as e:
        print(f"erreur : mot de passe : {e}", file=sys.stderr)
        return 2
    try:
        sys.stdout.reconfigure(errors="replace")   # console Windows : jamais d'erreur d'encodage
    except AttributeError:
        pass
    mp = None
    nom_monde = args.monde
    if not nom_monde and args.donnees:
        # serveur dédié sans --monde : le monde du dossier (le plus récent, même renommé), sinon « monde »
        # relecture adverse : un monde ILLISIBLE compte aussi (par la date de ses fichiers) ; s'il est le plus récent,
        # son ouverture échoue et le serveur refuse de démarrer, au lieu de créer en silence un monde neuf (vide)
        # que les relances suivantes auraient repris
        existants = mpm.lister(dossier_mondes)
        nom_monde = existants[0]["id"] if existants else "monde"
    if nom_monde and not args.sans_monde:
        try:
            mp = mpm.Monde.ouvrir(dossier_mondes, nom_monde, log=log, nom_neuf=args.monde_nom,
                                  deathlink=args.deathlink and not args.no_deathlink,
                                  vie_partagee=args.vie_partagee and not args.no_vie_partagee,
                                  difficulte={"degats": args.degats, "pv_monstres": args.pv_monstres},
                                  quarantaine=not args.no_monde_quarantaine, strict=args.monde_strict,
                                  heure_auto=args.monde_heure, ecriture_min=args.monde_ecriture)
        except (mpm.MondeIllisible, mpm.MondeTropRecent) as e:
            log(f"ÉCHEC : monde « {nom_monde} » : {e}")
            print(f"erreur : monde : {e}", file=sys.stderr)
            return 2
        mp.rando_off = bool(args.no_rando)
        if admin_srv is not None and admin_srv.config.get("monde") != mp.id:
            admin_srv.config["monde"] = mp.id         # config.json : l'id du monde (un nom peut changer)
            admin_srv.enregistrer()
    if admin_srv is not None:
        admin_srv.mdp_fichier = serveur_dedie.fichier_mdp_volume(args)
        admin_srv.mdp_source = mdp_source
        mp.image_anciens = bool(args.monde_image_anciens_hanteurs)
    try:
        r = asyncio.run(amain(args.host, args.port, args.stats, not args.no_busy_defer, not args.no_tsw,
                          not args.no_drops, not args.no_sticks, args.hote, args.arret_fichier,
                          not args.no_keys, args.keys_file, not args.no_coffres, not args.no_inventaire,
                          not args.no_cadeaux, not args.no_boss_pv, args.max_joueurs,
                          ghost_hits=not args.no_ghost_hits, mobiles=not args.no_mobiles,
                          compteurs=not args.no_compteurs, carry=not args.no_carry,
                          mode_hanteurs=args.mode_hanteurs, hanteurs_cfg=hcfg, rares=not args.no_rares,
                          monde=not args.no_monde, herbe=not args.no_herbe,
                          deathlink=args.deathlink and not args.no_deathlink,
                          vie_partagee=args.vie_partagee and not args.no_vie_partagee,
                          mot_de_passe=mdp, mdp_source=mdp_source,
                          mises_a_jour=args.mises_a_jour, donnees=args.donnees, monde_persistant=mp,
                          rando_graine=None if args.no_rando else args.rando_graine,
                          rando_spoiler=args.rando_spoiler, rando_mode=args.rando_mode,
                          difficulte={"degats": args.degats, "pv_monstres": args.pv_monstres},
                          difficulte_on=not args.no_difficulte,
                          rando_options={"lieux": args.rando_lieux,
                                         # lot 5 : une portée donnée vaut aussi « mélangé »
                                         "cles": args.rando_cles_portee or args.rando_cles,
                                         "grandes_cles": args.rando_grandes_cles_portee or args.rando_grandes_cles,
                                         "cartes_donjon": args.rando_cartes_donjon_portee or args.rando_cartes_donjon,
                                         "triforce": args.rando_triforce, "exclus": args.rando_exclus,
                                         "cadeaux": args.rando_cadeaux, "indices": args.rando_indices,
                                         "lieux_courrier": args.rando_lieux_courrier,
                                         "lieux_boutiques": args.rando_lieux_boutiques,
                                         "lieux_encheres": args.rando_lieux_encheres,
                                         "lieux_pnj": args.rando_lieux_pnj,
                                         "coffres_conflit": args.rando_coffres_conflit,
                                         "progressifs": args.rando_progressifs,
                                         "fichiers_demo": args.rando_fichiers_demo,
                                         **{"lieux_" + f: getattr(args, "rando_lieux_" + f) for f in
                                            ("tresors", "evenements", "boss", "fees", "sol", "pnj_code", "beedle")},
                                         # lot 7 (randomizer par fichiers, étape 3)
                                         "monde": args.rando_monde, "depart": args.rando_depart,
                                         "coffres_cinematiques": args.rando_coffres_cinematiques,
                                         "cinematiques_courtes": args.rando_cinematiques_courtes,
                                         "pas_de_recombats": args.rando_pas_de_recombats,
                                         "textes": args.rando_textes,
                                         # lot 8 (randomizer par fichiers, étape 4)
                                         "sans_epee": args.rando_sans_epee, "entrees": args.rando_entrees,
                                         "cartes_melangees": args.rando_cartes,
                                         "depart_melange": args.rando_depart_melange,
                                         "boucliers_progressifs": args.rando_boucliers_progressifs,
                                         "progressifs_plus": args.rando_progressifs_plus,
                                         # étape 5
                                         "lieux_kane": args.rando_lieux_kane,
                                         "courrier_tout": args.rando_courrier_tout,
                                         "lieux_encheres_100": args.rando_lieux_encheres_100,
                                         "boutique_epuise": args.rando_boutique_epuise,
                                         "indices_clairs": False if args.rando_indices_obscurs else None,
                                         "reglages": args.rando_reglages},
                          trackeur=not args.no_trackeur, trackeur_deduire=args.trackeur_deduire,
                          pnj=not args.no_pnj, admin=admin_srv,
                          autorite_preferee=autorite.normaliser(args.autorite_preferee),
                          validation=args.validation if args.validation in valid_mod.MODES else valid_mod.DEFAUT,
                          robot=args.robot, robot_commande=robot_mod.commande_defaut(
                              args.robot_commande, args.robot_portage, TOOLS, essai=args.robot == "essai",
                              journal=args.robot_journal if args.robot == "essai" else None),
                          robot_cle=args.robot_cle or os.environ.get("WWMP_ROBOT_CLE") or None,
                          robot_repos=args.robot_repos, robot_journal=args.robot_journal,
                          robot_latence=robot_mod.latence_ok(args.robot_essai_latence),
                          robots=robot_mod.nombre_ok(args.robots), monde_vivant=args.monde_vivant,
                          medailles=not args.no_medailles,
                          coups_joueurs=args.coups_joueurs,
                          ralliement=not args.no_ralliement, porter=not args.no_porter,
                          courses=not args.no_courses,
                          bonheur=not args.no_bonheur, essai_bonheur=args.essai_bonheur,
                          bonheur_quarts_donjon=max(0, args.bonheur_quarts_donjon),
                          bonheur_quarts_partie=max(0, args.bonheur_quarts_partie),
                          tenues=not args.no_tenues, essai_tenues=args.essai_tenues,
                          tenues_distance=args.tenues_distance, essai_fusions=args.essai_fusions,
                          releve=not args.no_releve, essai_relever=args.essai_relever,
                          camouflage=not args.no_camouflage, camouflage_duree=args.camouflage_duree,
                          essai_camouflage=args.essai_camouflage, fusion_entrees_hz=args.fusion_entrees_hz,
                          fusion_peremption_ms=args.fusion_peremption_ms))
        if r == "redemarrer":
            log("redémarrage : le serveur s'arrête ; Docker le relance avec la nouvelle configuration (restart: "
                "unless-stopped). Lancé à la main : relance-le")
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
