"""Protocole réseau wwmp v2 : une ligne JSON par message, sur TCP.

v2 (étape 5c) : ship.m peut valoir `true`. Un client v1 avec un graphic pack ≤ v9
le passerait tel quel à apply_ghost_models et perdrait la mise à jour du fantôme :
le serveur signale donc toute version différente (au client et dans son journal).

Client -> serveur
  hello    {name, version, pack, client}    (pack : version du graphic pack lue dans
                                             le jeu, ou null ; client : version des
                                             outils, voir CLIENT_VERSION)
           (A16 : jeton = identifiant du processus client ; à la reconnexion, le serveur ferme
            l'ancienne connexion de même jeton au lieu de renommer le joueur « Link 2 »)
           feat (optionnel, A15) : fonctions connues, ex. ["rejoindre"] ; hote = 1 : client
           désigné hôte (--hote) ; role = "rejoindre" : outil wwmp_rejoindre (pas un joueur :
           ni join / leave, ni zone, ni état relayé)
           couleur (optionnel, commun/couleurs.py) : couleur de tenue demandée, 1..8 (vert, rouge, bleu,
           violet, jaune, orange, turquoise, sombre) ou 0 / absent = automatique (celle du numéro de
           joueur). Le serveur répond par la couleur ATTRIBUÉE (jamais deux fois la même : le premier
           arrivé garde la sienne) dans welcome.couleur, welcome.players[i].couleur, join.couleur et
           lobby.players[i].couleur. Ancien serveur : champs absents, le client retombe sur la couleur du
           numéro de joueur, pour lui comme pour les autres (même image sur tous les PC). Un joueur qui
           revient (même jeton ou même nom) en automatique retrouve sa couleur si elle est libre. Ancien client : champs ignorés.
  rejoindre_req  {rid, group, force?}     (A15, outil de J2 : demande la progression de l'hôte ;
                                           rid = 8 chiffres hexadécimaux)
  sauvegarde_hote {rid, fmt, jeu, pack, client, name, stage, room, stage_no, base, len, part,
                   parts, data, sha256}   (A15, client de l'hôte : image brute gi+0x20..0x7C0 en
                                           base64, découpée en morceaux de REJOINDRE_PART octets)
  rejoindre_err  {rid, msg}               (A15, client de l'hôte : refus, partie pas prête…)
  rejoindre_fin  {rid, ok, raison?}       (A15, outil de J2 : copie appliquée et vérifiée, ou pas)
  state    {stage, room, layer, pos[3], rotY, speed, life, max_life, pose, att,
            ship: {pos, rotY, m} | null}
            (ship.m : poses du bateau, ou `true` entre deux images clés = « même pose
             qu'avant » ; un receveur graphic pack ≥ v10 n'utilise que sa présence)
            (ship.v, optionnel : {j, a} = matrices relatives des articulations 5 à 10 de la coque, float16 en
             base64, et angle de la voile ; à chaque envoi ; synchro/voile.py. Ancien client : absent, le receveur
             replie le mât du fantôme. Le serveur le relaie sans le lire)
            busy = 1 (optionnel, A11) : le joueur est en cinématique / dialogue / coffre /
            porte (dEvt_control_c, avec anti-rebond). Absent = libre. Le serveur ne donne pas
            l'autorité d'une zone à un joueur occupé (au plus BUSY_DEFER_MAX s d'attente) et
            le relaie aux autres (« X est en cinématique »). Ancien client : jamais envoyé.
            busy_info = {m, e, p1, p2, s} (optionnel, synchro v2 C1, synchro/cinematiques.py) : détail
            de l'événement, SÉPARÉ de busy (qui reste 1 : le serveur teste busy in (1, True)) ;
            m = mMode (1 dialogue, 2 démo, 3 COMPULSORY), e = mEventId, p1 / p2 = procs de
            mPt1 / mPt2 (-1 inconnu), s = secondes depuis le début. Le receveur affiche « X est
            dans une cinématique (coffre / porte / dialogue / cinématique de boss / scène) »
            seulement si X est dans sa salle et proche. Le serveur le relaie sans le lire.
  enemies  {zone, epoch, list: [{key, proc, pos[3], rotY, hp, max_hp, m?}]}   (autorité seulement)
            (m : pose des modèles, seulement pour les ennemis proches d'un joueur)
           list[i].dead = 1 : « pierre tombale » (5c), ennemi disparu chez l'autorité (mort),
           envoyé 2 s avec hp 0 et sa dernière position
           full = 1 (optionnel) : la liste contient TOUS les ennemis de la salle de l'autorité
           (seules les poses « m » sont filtrées) ; un joueur non autoritaire peut alors
           supprimer chez lui les ennemis qui n'y sont pas (ennemis orphelins). Absent chez un
           ancien client : rien n'est supprimé.
  ping     {t}
  keys     {group, hello: 1, hid, slots: {"3": 1, …}}   (synchro v2, petites clés partagées, voir
           synchro/cles.py : à chaque connexion, après un rechargement ou un « keys reset » : valeur
           effective de chaque donjon, faits non encore accusés retirés ; le serveur ne s'en sert
           que pour un donjon qu'il ne connaît pas encore, base = max des valeurs annoncées)
  key_get  {group, slot, id, seq}   (une petite clé obtenue pendant un événement ; id = t<n> coffre,
                                     i<n> bit d'objet, z<salle>:<n> objet de zone, a<j>:<seq> autre)
  key_use  {group, slot, id, seq}   (une petite clé dépensée : s<sw> porte, a<j>:<seq> autre)
  inv      {group, seq, full?: 1, acq: [itemNo…], bits: [[gi_off, masque]…], cap: {w, am, bm, mm},
            fl: n, sac: [itemNo…]}   (synchro v2, inventaire partagé phase 1, synchro/inventaire.py :
           full = état COMPLET, envoyé à chaque connexion, après un rechargement ou un « inv reset »)
  inv      {group, seq, acq, bits, cap, fl, sac: {add: [...], del: [...]}}   (changements locaux :
           objets / équipement nouveaux, bits OU nouveaux, plafonds montés, nombre de flacons,
           ajouts / retraits du sac de livraison ; jamais de compteur : rubis, munitions, vie max)
  cnt      {group, hello: 1, seq: 0, ack, v: {r, a, b}, cap: {r, a, b}, hote?}   (synchro v2, compteurs
           communs, synchro/compteurs.py : inscription à chaque connexion, rechargement, « cnt reset » ou
           arc / sac obtenu ; v = valeurs AVANT nos événements non accusés ; seuls les compteurs actifs ici)
  cnt      {group, seq, ev: "<sid>:<seq>", d: {r?, a?, b?}, cap}   (gain ou dépense locale ; ev unique :
           jamais compté deux fois par le serveur)
  cnt      {group, sync: 1}                     (total redemandé : sortie de pause, toutes les 5 s)
  pickup   {zone, src, nid, no, g}              (A4 premier arrivé : NOTRE Link a ramassé le drop (src =
           id du joueur chez qui il est né, nid = son pid là-bas) ; g = gain local constaté, 0..valeur)
  hit      {zone, key, dmg}                     (étape 6 : coup porté à une marionnette)
  pnj      {zone, list: [{k, p[3], a, v, m?, i?, d?, n?, c?, t?}]}   (synchro v4, synchro/pnj.py : place des PNJ
           qui se déplacent, par l'autorité de la zone ; c = « je le porte », t = « je lui parle » : par tout
           joueur de la zone ; state.mgs = compteur du mini-jeu en cours, à côté de state.mg)
  progress {group, bits, full}                  (étape 8 : bits de progression passés à 1 ;
                                                 full = premier envoi, demande l'union en retour)
  tsw      {group, hello: 1}                     (A7 : inscription au relais des switches
                                                 temporaires du groupe ; renvoie l'état du stage)
  tsw      {group, stage, ch: [[salle, sw, val]], act: [[salle, setID]]}
           (A7 : switches temporaires changés chez nous, 0x80-0xBF (salle -1 = donjon) ou
            0xC0-0xEF, val 0 ou 1 ; act = ennemis tués (bit mActorFlags 0->1))
  drop     {zone, list: [{nid, no, bit, type, act, room, pos[3], rotY}]}
           (A4 : objets proc 255 apparus chez nous, voir synchro/drops.py ; nid = id de process
            de l'original, no = itemNo, bit = mItemBitNo & 0x7F, type / act = daItem_prm,
            pos = point d'apparition (home) ; seulement les consommables communs)
           kind = "fee" (synchro v2, --share-fairies) : {nid, kind, no: 0x16, room, pos[3], rotY} : fée
           libre (proc 0x168, type 1) née chez nous ; un ancien client la refuse (no hors liste)
  break    {zone, list: [{key, pos[3], keep?, jug?, at?}]}
           (A6 : jarres / pierres cassées chez nous, clé enemy_key(proc, params, origine) ;
            synchro v2, --share-jugs : jug = 1 pour une cruche d'eau, at = point d'impact [3] : le
            receveur y déplace sa cruche puis la fait casser par le jeu)
  magma    {zone, list: [{nid, pos[3], room, age}]}
           (synchro v2, --share-magma : dalle de magma figé (Magrock, proc 42, params 0) née chez
            nous, cruche ou flèche de glace ; age = âge en images (+0x754))
  gift     {group, full: 1}                     (synchro v2, --share-hearts : inscription, demande
                                                 le registre des quarts déjà pris)
  gift     {group, src: "piece:<index>:<bit>", item: 7, i, bit, stage, room}
           (quart de cœur AU SOL ramassé chez nous : bit mItem < 0x20 de mSave[index])
  boss     {group, stage, key: "<proc>:<params 8 hex>", hp, max}
           (synchro v2/v3 B1, --boss-pv, synchro/boss_pv.py : PV d'un boss de la table (Gohma 234 max 10,
            Kalle Demos, Gohdan, Roi Cuirasse, Ganon spectral, Ganon marionnette ×3, Molgera max 4 =
            4 - compteur) chez nous, quand ils baissent sous ce qu'on connaît, et en rappel toutes les 2 s)
  player_hit {to, zone, key, proc, at: {type, atp, spl, se, mark, gat, mtrl}, dmg, vec[3], pos[3], seq}
           (synchro v2 A1, --share-ghost-hits, synchro/coups_fantome.py : chez l'AUTORITÉ de la zone, un
            ennemi synchronisé a frappé le fantôme du joueur `to` ; pos = point d'impact)
  saisie   {to, zone, key, proc, effet: "confus", frames: 1..300, seq}
           (synchro v4, --share-saisie, synchro/saisie.py : chez l'AUTORITÉ de la zone, un Poe possède le
            fantôme du joueur `to` ; les prises des autres espèces partent en player_hit)
  ground_req {zone}                         (A6, optionnel : notre salle a été rechargée sans
                                             changement de zone ; le serveur renvoie drop/break
                                             « snap » de la zone. Ignoré par un ancien serveur)
  stick    {zone, ev, key, kind?, flame?, pos?, rot?, cause?}
           (A3, bâtons partagés, voir synchro/batons.py ; envoi fiable. kind = type du bâton 0/1/7.
            ev : grab (le Link local ramasse le bâton ; pos = position au repos avant la prise),
            rest (le bâton qu'on tenait s'est posé ; pos = translation - 5 en y, rot = shape_angle,
            cause pose/lance ; cause aligne : autorité de la zone, position d'un bâton libre),
            gone (bâton tenu / en vol disparu : magma, abîme), flame (front d'allumage ou
            d'extinction d'un bâton libre), taken (autorité : un Bokoblin a ramassé un bâton libre))
  state.stk (optionnel, A3) : {k: clé, h: 1, m: matrice relative R = B⁻¹·S (12 float16 base64),
            f: flamme} quand on tient un bâton, {k, h: 0, w: [12 flottants, matrice monde], f}
            quand il est en vol ; absent sinon

Serveur -> client
  welcome  {id, version, players: [{id, name, pack}], name?, outils?}
           (A16 : name = nom effectif, rendu unique par le serveur, « Link 2 » ; outils =
            CLIENT_VERSION du serveur ; absents chez un ancien serveur. Rôle « rejoindre » :
            hote_en_jeu = vrai si le client de l'hôte est connecté, pour le compagnon du joueur 2
            sans découverte UDP)
  bye      {msg}                            (A16 : le serveur s'arrête, « l'hôte a fermé la partie » ;
                                             un ancien client l'ignore)
  join     {id, name, pack}
  leave    {id}
  state    {id, ...state}
  authority {zone, holder, epoch}          (à tous ; holder = id ou null si zone vide)
  enemies  {from, zone, epoch, list, full?} (aux autres joueurs de la zone ; le dernier
                                            est renvoyé tout de suite à qui entre dans la zone)
  pong     {t}
  hit      {from, zone, key, dmg}           (à l'autorité de la zone seulement)
  pnj      {from, zone, list}               (synchro v4 : aux autres joueurs de la zone ; d'un joueur qui
                                            n'a pas l'autorité, ses seules entrées c / t ; rien n'est gardé)
  progress {from, group, bits, full}        (bits nouveaux pour l'union du groupe, aux autres
                                             membres ; full = union complète, au nouvel arrivant)
  tsw      {from, group, stage, ch: [[salle, sw, val, seq]], act, snap?}
           (A7 : à tous les inscrits du groupe présents dans le stage, ÉMETTEUR COMPRIS : l'écho
            numéroté sert d'accusé, le plus grand seq gagne ; snap = 1, from = null : état gardé
            par le serveur pour TOUT le stage, envoyé à chaque changement de salle, en
            messages de 256 entrées au plus)
  drop     {from, zone, list, snap?}       (A4 : aux autres joueurs de la zone ; snap = 1 :
                                            objets de moins de DROP_KEEP s, à qui entre)
  break    {from, zone, list, snap?}       (A6 : idem ; snap = 1, from = null : jarres cassées
                                            gardées tant qu'un joueur est dans la zone)
  magma    {from, zone, list, snap?}        (synchro v2 : aux autres joueurs de la zone ; snap = 1 :
                                            dalles de moins de 11 s, âge corrigé, à qui entre)
  gift     {from, nom, group, src, item, i, bit, stage, room}   (synchro v2 : premier arrivé par src,
                                            aux AUTRES membres du groupe, quelle que soit la zone)
  gift     {from: null, group, full: 1, list: [{src, item, i, bit, stage, room, by}]}   (registre)
  gift     {from: null, group, reset: 1}       (synchro v2 : registre vidé par un rejoindre, A15)
  stick    {from, zone, ev, key, …}         (A3 : relais aux autres joueurs de la zone ; le serveur
                                            arbitre : premier grab reçu gagne)
  stick    {from: null, zone, ev: "deny", key, holder}   (A3 : au perdant d'un conflit seulement)
  stick    {from: null, zone, ev: "snap", list: [{key, holder, kind, flame, pos, rot}]}
           (A3 : à qui entre dans la zone et sur ground_req)
  stick    {from: null, zone, ev: "rest", …, cause: "porteur_parti"}  (A3 : porteur déconnecté)
  stick    {from, zone, ev: "gone", key, cause: "parti"}   (A3 : le porteur a quitté la zone ;
                                            la nouvelle zone reçoit un grab de sa part)
  rejoindre_req  {rid, from, name}        (A15 : à l'hôte choisi, voir wwmp_server.choisir_hote)
  sauvegarde_hote {…, from}               (A15 : relais tel quel à J2, sans décoder)
  rejoindre_err  {rid, msg}               (A15 : à J2 : refus du serveur ou de l'hôte)
  rejoindre_fin  {rid, from, name, ok}    (A15 : à l'hôte, pour information)
  keys     {from: null, group, full: 1, slots: {"3": [cible, rev], …}, hid?}   (réponse à hello : hid
           du hello pour l'auteur seul ; sans hid = diffusion après le hello d'un autre membre)
  keys     {from, group, slot, target, rev, why: get|use, id, by, seq?}  (à tout le groupe ; seq
           seulement pour l'auteur : accusé de réception du fait)
  keys     {from: null, group, slot, target, rev, why: dup_get|dup_use, id, by, seq}  (à l'auteur
           seul : fait déjà compté, by = qui l'a déclaré en premier ; le client converge vers la cible)
  keys     {from: null, group, reset: 1}   (A15 : après un rejoindre réussi ; le client renvoie hello)
  progress {from: null, group, bits: {}, full: 1, reset: 1}
           (A15 : après un rejoindre réussi, union du groupe vidée ; un client récent oublie son
            état réseau (ProgressSync.reset_network) et renvoie tout ; un ancien ne fait rien)
  inv      {from, nom, group, acq, bits, cap, fl, sac?}   (synchro v2 : ajouts à l'union du groupe, aux
           AUTRES membres ; sac = liste complète du sac de livraison quand elle a changé)
  inv      {from: null, group, ack, sac, snap?: 1, acq?, bits?, cap?, fl?}   (à l'auteur : ack = plus grand
           seq intégré ; snap = 1 : état commun complet, réponse à full)
  inv      {from: null, group, reset: 1}   (A15 : après un rejoindre réussi ; le client renvoie full)
  cnt      {from, group, tot: {r, a, b}, cap, ack, snap?, d?, nom?, perdu?, gel?}   (synchro v2 : total
           commun ; ack = plus grand seq intégré DE CE DESTINATAIRE (le client ne corrige que si ack couvre
           ses événements) ; from = id de l'auteur d'un changement (d = appliqué) ; gel = 1 : rejoindre en
           cours, rien n'est compté ni corrigé)
  cnt      {from: null, group, reset: 1, ack}   (fin d'un rejoindre : chacun renvoie hello puis ses
           événements non accusés, gardés pendant la copie ; le total est gardé)
  gone     {zone, src, nid, no, nom, from}  (A4 premier arrivé : un autre joueur a ramassé ce drop ; ton
           exemplaire est retiré si tu partages ce compteur)
  boss     {from, group, stage, key, hp, max, nom}   (synchro v2 B1 : nouveau minimum, aux autres
           membres du groupe présents dans la stage ; from = null : réponse à un rapport plus haut
           que le minimum, à l'auteur seul ; oublié quand plus aucun membre n'est dans la stage)
  player_hit {from, to, zone, key, proc, at, dmg, vec, pos, seq}   (synchro v2 A2 : à la victime seule,
           si `from` est l'autorité actuelle de la zone, victime dans la même zone et pas en cinématique,
           4 par seconde et par paire au plus ; rejoué chez elle par le pack v19, CALL 9)
  saisie   {from, to, zone, key, proc, effet, frames, seq}   (synchro v4 : à la victime seule, mêmes règles
           que player_hit, 2 par seconde et par paire au plus ; commandes inversées `frames` images chez
           elle, pack v19 avec le bit 9)
  error    {msg, code?}                     (code = "plein" : partie pleine, MAX_PLAYERS joueurs ;
                                            la connexion est fermée)

Une zone est "stage:salle" (ex. "A_mori:0", "sea:44").

Compatibilité : les champs `pack`/`client` de hello et `pack` de welcome/join sont
optionnels (ajoutés sans changer VERSION) ; un ancien client ou serveur les ignore.
Le client envoie un ping par seconde (KEEPALIVE, depuis une tâche à part : jamais
bloqué par la lecture du jeu ni par un chargement de stage) et le serveur répond. Sans
aucun message pendant TIMEOUT s, le serveur coupe ; sans aucun message pendant
CLIENT_TIMEOUT s, le client se reconnecte. TIMEOUT < CLIENT_TIMEOUT : le serveur a déjà
retiré l'ancienne connexion (et son autorité) quand le client revient. Un Ctrl+C ferme
la connexion : le départ est vu tout de suite, sans attendre TIMEOUT.
Le champ `full` des snapshots est optionnel (ajouté sans changer VERSION) : un ancien
serveur ne le relaie pas, un ancien client ne l'envoie pas -> aucune suppression.

Bits de progression : {clé: [[offset, masque], ...]}, voir tools/jeu/progress.py.
Un serveur ancien ignore simplement les messages progress (type inconnu).
Messages tsw : un serveur ancien les ignore ; un client qui ne s'est pas inscrit (hello)
n'en reçoit jamais.
Messages drop / break : un serveur ancien les ignore, un client ancien aussi (type inconnu).
Messages stick et champ state.stk (A3) : un serveur ancien ignore stick et relaie stk ; un
client récent ne crée de marionnette de bâton qu'après un grab relayé, donc rien ne se passe
avec un ancien serveur. Un client ancien n'envoie ni stick ni stk et ignore les deux.
Messages keys / key_get / key_use (synchro v2) : un serveur ancien les ignore ; le client ne
corrige alors jamais son compteur (clés par joueur, comme avant). Un client ancien ne les envoie pas.
Messages inv (synchro v2) : un serveur ancien les ignore (rien n'est partagé, aucune écriture : le
client n'applique rien avant la réponse « snap ») ; un client ancien ne les envoie pas.
Messages cnt / pickup / gone (synchro v2, compteurs communs) : un serveur ancien les ignore (aucune
correction : le client attend la réponse « snap » ; drops « butin par joueur » comme avant) ; un client
ancien ne les envoie pas et ignore « gone » (il garde sa copie : un gain en double possible, le sien).
A15 (rejoindre) : un ancien serveur ignore rejoindre_req (l'outil abandonne après
REJOINDRE_ATTENTE s, rien n'est écrit) ; un hôte sans feat « rejoindre » est refusé tout de suite.
Messages boss et champ state.busy_info (synchro v2, module 5) : un serveur ancien ignore boss
(aucune écriture : le client n'écrit qu'un minimum reçu) et relaie busy_info sans le lire ; un
client ancien ignore les deux (il affiche « X est en cinématique » comme avant).
Messages player_hit (synchro v2, fantôme touchable) : un serveur ancien les ignore, un client ancien
aussi ; un client sans le pack v19 de la branche les reçoit et les jette (sauf --degats-directs).
Messages saisie (synchro v4) : un serveur ancien les ignore, un client ancien aussi.
Joueurs : MAX_PLAYERS = 8 au plus. Le graphic pack v17 n'a que 4 emplacements de fantômes
(jeu/mailbox.py N_SLOTS) : au-delà, le client affiche les plus proches (client/fantomes.py) ; le
pack v19 en a 7 (N_SLOTS_V19 : 8 joueurs = 7 fantômes), choisi par la version lue dans la boîte.
Messages mob / boom / lift (synchro v2, lot 3, synchro/mobiles.py) : bloc poussé (push {dir, pull, at, to, y}
en positions monde, cell, snap et deny du serveur), bombe-fleur vidée (pick), explosion (boom {pos,
kind}), plateformes de l'autorité de la zone (lift) ; un serveur ancien les ignore, un client ancien
aussi. La case de départ d'un push s'appelle « at » : « from » est l'émetteur, ajouté par le relais.
Messages carry et champ state.car (synchro v2, lot 3, suite, synchro/portage.py, objets portés) : carry
{zone, ev: grab {key, proc, pos?, cause?: "rappel"} | rest {key, pos, rot} | gone {key, pos, cause:
casse/eau/explose/perdu}} ; clé « c:<proc>/<params sans itemNo ni bit 31>/<home> » (jarre, pierre) ou
« b:<joueur>:<id> » (bombe) ; le serveur relaie à la zone, premier grab gagnant, « deny » {key, holder}
au perdant seulement. state.car : {k, h: 1, o: [position dans le repère de Link], r: angles} (tenu),
{k, h: 0, w: [position monde], r} (en vol) ; absent sinon. Un serveur ancien ignore carry et relaie car,
un client ancien ignore les deux (il ne voit rien de porté, comme avant).
Messages cycle / rare (synchro v3, mécanismes rares, synchro/rares.py) : cycle {zone, stage, bit, timer}
(horloge des cycles du stage, 1 Hz ; le serveur ne relaie que celle du propriétaire du stage, aux autres
joueurs du même stage) ; rare {zone, ev: pillar, key, yaw} (pilier Hcbh cassé, relayé à la zone). Un
serveur ancien et un client ancien les ignorent (chacun son horloge et ses piliers, comme avant).
"""
from __future__ import annotations

import json
import re

VERSION = 2
CLIENT_VERSION = "2026-11-10b"   # outils (client/serveur) : affiché dans les journaux du serveur
DEFAULT_PORT = 7777
MAX_PLAYERS = 8        # joueurs au plus par partie (serveur, annonce de découverte, lanceurs)
KEEPALIVE = 1.0        # s entre deux pings du client
TIMEOUT = 5.0          # s sans message reçu -> le serveur coupe la connexion (3 s avant : trop
                       #   court pour un ancien client figé par un chargement de stage)
CLIENT_TIMEOUT = 6.0   # s sans message du serveur -> le client se reconnecte
MAX_LINE = 256 * 1024
# A15 : rejoindre la partie (docs/etudes/rejoindre.md)
FEATURES = ("rejoindre", "hanteurs", "deathlink", "image_monde", "monde_tot", "rando", "difficulte")   # hanteurs : client qui sait jouer un rôle du mode Hanteurs ;
                                                  # deathlink : client qui sait suivre la règle (synchro v3) ;
                                                  # image_monde : client qui fournit l'image du monde persistant
                                                  # (image_req / image_monde) ; monde_tot : client qui envoie « tot »
                                                  # avec sa progression (garde de contenu du monde persistant) ;
                                                  # rando : client qui sait charger la table du randomizer
REJOINDRE_PART = 48 * 1024     # octets bruts au plus par message sauvegarde_hote (avant base64)
REJOINDRE_MAX = 64 * 1024      # taille max de l'image (len)
REJOINDRE_ATTENTE = 20.0       # s : l'outil abandonne sans réponse (ancien serveur)
REJOINDRE_TIMEOUT = 60.0       # s : le serveur abandonne une demande sans réponse de l'hôte


# Synchro v2 : petites clés partagées (docs/etudes/sync2_cles.md §3.2 / §3.5)
# (« r<index>:<coffre> » : petite clé d'un coffre du randomizer placée hors de son donjon, créditée par le SERVEUR
# quand le groupe ouvre ce coffre ; portage TWWHD, lot 5, rando/noyau.py)
KEY_ID_RE = re.compile(r"^(?:[ti]\d{1,2}|s\d{1,3}|a\d{1,9}:\d{1,9}|z\d{1,2}:\d{1,2}|p\d{1,4}:[0-9A-F]{8}:\d{1,2}"
                       r"|r\d{1,2}:\d{1,2})$")
KEY_ID_MAX = 24
# numéros de sauvegarde (mDan.mStageNo) des donjons à petites clés : 2 Forteresse Maudite,
# 3 Caverne du Dragon [LIVE], 4 Bois Défendus, 5 Tour des Dieux, 6 Temple de la Terre, 7 Temple du
# Vent, 8 Tour de Ganon [HYPO, comme progress.TSW_DUNGEON_SAVE_NOS]
KEY_SLOTS = frozenset(range(2, 9))
KEY_MAX = 99                     # borne du jeu (dMeter_keyMove : 0..0x63)


def valid_key_id(x) -> bool:
    return isinstance(x, str) and len(x) <= KEY_ID_MAX and KEY_ID_RE.match(x) is not None


def valid_key_slot(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x in KEY_SLOTS


# Synchro v2 : coffres partagés (docs/etudes/sync2_coffres.md §3.3). Genres : t = coffre (bit mTbox n
# de mMemory[i]), d = réceptacle du boss (bit 4 de mDungeonItem de mMemory[i]), o = repêchage
# (mOcean[i] bit n, phase 2 : accepté, pas encore produit).
COFFRE_BOUNDS = {"t": (16, range(32)), "d": (16, range(4, 5)), "o": (50, range(16))}
COFFRE_STAGE_RE = re.compile(r"^[A-Za-z0-9_]{1,8}$")


def _int_ok(x, lo: int, hi: int) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi


def valid_coffre(msg) -> dict | None:
    """Enregistrement « coffre » nettoyé (bornes du §3.3), ou None s'il est invalide.
    {"k", "i", "n", "item" (0..255 ou None), "stage", "room", ["prob": 1], ["kc": 1], ["src": {...}]}."""
    if not isinstance(msg, dict):
        return None
    k, i, n = msg.get("k"), msg.get("i"), msg.get("n")
    if k not in COFFRE_BOUNDS:
        return None
    n_idx, bits = COFFRE_BOUNDS[k]
    if not (_int_ok(i, 0, n_idx - 1) and _int_ok(n, 0, 31) and n in bits):
        return None
    item = msg.get("item")
    if item is not None and not _int_ok(item, 0, 255):
        return None
    stage = msg.get("stage")
    rec = {"k": k, "i": i, "n": n, "item": item,
           "stage": stage if isinstance(stage, str) and COFFRE_STAGE_RE.match(stage) else "",
           "room": msg.get("room") if _int_ok(msg.get("room"), -1, 63) else -1}
    if msg.get("prob") in (1, True):
        rec["prob"] = 1
    if msg.get("kc") in (1, True):
        rec["kc"] = 1                     # petites clés comptées par le compteur commun chez l'auteur
    if msg.get("iv") in (1, True):
        rec["iv"] = 1                     # inventaire partagé actif chez l'auteur (synchro/inventaire.py)
    if msg.get("cc") in (1, True):
        rec["cc"] = 1                     # compteur commun de l'objet (rubis, munitions) compté chez l'auteur
    src = msg.get("src")
    if isinstance(src, dict):
        pos, ang = src.get("pos"), src.get("ang")
        if (_int_ok(src.get("proc"), 0, 0x7FFF) and _int_ok(src.get("params"), 0, 0xFFFFFFFF)
                and isinstance(pos, list) and len(pos) == 3
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) and abs(x) < 1e6 for x in pos)
                and isinstance(ang, list) and len(ang) == 3 and all(_int_ok(x, -32768, 32767) for x in ang)
                and _int_ok(src.get("room"), -1, 63)):
            rec["src"] = {"proc": src["proc"], "params": src["params"], "pos": [float(x) for x in pos],
                          "ang": list(ang), "room": src["room"]}
    return rec


# Synchro v2 : inventaire partagé, phase 1 (docs/etudes/sync2_inventaire.md §3.2-§3.4, synchro/inventaire.py).
# Jetons (numéros d'objet, jamais d'adresse) : objets d'emplacement A (rangs), équipement C. Jamais 0x08
# (réceptacle), 0x15 (petite clé), 0x2E (main nue), 0x3D, 0x4C-0x4E (objets de donjon) : voir
# inventaire.INTERDITS.
INV_ITEMS = frozenset({0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x29, 0x2A, 0x2C, 0x2D, 0x2F, 0x30, 0x31,
                       0x33, 0x34, 0x35, 0x36, 0x77, 0x78,                      # A : emplacements
                       0x38, 0x39, 0x3A, 0x3E, 0x3B, 0x3C, 0x28, 0x42, 0x43})   # C : mCollect
# D : octets OU (offset depuis gi -> masque autorisé). mReserveFlags/mBeastFlags/mBaitFlags (0xB0-0xB5),
# chants (0xDD, bits 0-5), Triforce (0xDE), perles (0xDF, bits 0-2), cartes marines (0xE4-0x123), îles
# cartographiées (0x124-0x154). gi+0xB6-0xBB (octets HD inconnus) : jamais.
INV_BIT_MASKS = {**{o: 0xFF for o in range(0xB0, 0xB6)}, 0xDD: 0x3F, 0xDE: 0xFF, 0xDF: 0x07,
                 **{o: 0xFF for o in range(0xE4, 0x155)}}
INV_CAPS = {"w": (0, 1, 2), "am": (0, 30, 60, 99), "bm": (0, 30, 60, 99), "mm": (0, 16, 32)}
INV_SAC = range(0x8C, 0x9F)       # H : sac de livraison (objets d'échange uniques)
INV_SAC_MAX = 8
INV_FLACONS_MAX = 4               # B : emplacements 14-17
INV_SEQ_MAX = 2 ** 31


def valid_inv(msg) -> dict | None:
    """Message « inv » nettoyé, ou None s'il est invalide (§3.3) :
    {"seq", "full", "sync", "acq": [...], "bits": {off: masque}, "cap": {...}, "fl": n | None,
     "sac": [...] | None (état complet), "add": [...], "del": [...]}. Les entrées hors liste blanche
    sont retirées (jamais une adresse : seulement des numéros d'objet et des offsets connus)."""
    if not isinstance(msg, dict):
        return None
    seq = msg.get("seq", 0)
    if not _int_ok(seq, 0, INV_SEQ_MAX):
        return None
    out = {"seq": seq, "full": msg.get("full") in (1, True), "sync": msg.get("sync") in (1, True),
           "acq": [], "bits": {}, "cap": {}, "fl": None, "sac": None, "add": [], "del": []}
    acq = msg.get("acq")
    if isinstance(acq, list):
        for no in acq[:64]:
            if _int_ok(no, 0, 255) and no in INV_ITEMS and no not in out["acq"]:
                out["acq"].append(no)
    bits = msg.get("bits")
    if isinstance(bits, list):
        for e in bits[:256]:
            if isinstance(e, list) and len(e) == 2 and _int_ok(e[0], 0, 0xFFF) and _int_ok(e[1], 1, 255) \
                    and e[0] in INV_BIT_MASKS:
                m = e[1] & INV_BIT_MASKS[e[0]]
                if m:
                    out["bits"][e[0]] = out["bits"].get(e[0], 0) | m
    cap = msg.get("cap")
    if isinstance(cap, dict):
        for k, ok in INV_CAPS.items():
            v = cap.get(k)
            if _int_ok(v, 0, 255) and v in ok:
                out["cap"][k] = v
    if _int_ok(msg.get("fl"), 0, INV_FLACONS_MAX):
        out["fl"] = msg["fl"]

    def items(x) -> list:
        res = []
        for no in (x[:INV_SAC_MAX] if isinstance(x, list) else []):
            if _int_ok(no, 0, 255) and no in INV_SAC and no not in res:
                res.append(no)
        return res
    sac = msg.get("sac")
    if out["full"] and isinstance(sac, list):
        out["sac"] = sorted(items(sac))
    elif isinstance(sac, dict):
        out["add"], out["del"] = items(sac.get("add")), items(sac.get("del"))
    return out


def zone_of(stage: str, room: int) -> str:
    return f"{stage}:{room}"


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes) -> dict | None:
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) and isinstance(msg.get("type"), str) else None


def enemy_key(proc: int, params: int, home: tuple[float, float, float]) -> str:
    """Identité réseau d'un ennemi placé : identique sur toutes les machines qui
    chargent la même salle (type, paramètres de spawn, position d'origine)."""
    hx, hy, hz = (int(round(v)) for v in home)
    return f"{proc}/{params:08X}/{hx},{hy},{hz}"


# --- bande passante (étape 5c) ------------------------------------------------
# La pose des modèles d'un ennemi (« m ») fait ~1,3 Ko par ennemi et par snapshot :
# c'est l'essentiel du débit. On ne l'envoie que pour les ennemis proches d'un
# joueur de la zone ; le receveur garde la dernière pose reçue (EnemyPuppets).
DEFAULT_POSE_DIST = 3000.0


def near_any(pos, centers, dist: float) -> bool:
    """Vrai si `pos` est à moins de `dist` d'un des `centers` (dist <= 0 : toujours vrai)."""
    if dist <= 0:
        return True
    d2 = dist * dist
    for c in centers:
        if sum((a - b) * (a - b) for a, b in zip(pos, c)) <= d2:
            return True
    return False


def limit_enemy_poses(enemies: list[dict], centers, dist: float, with_pose: bool = True) -> tuple[list[dict], int]:
    """Retire la pose « m » des ennemis loin de tous les `centers` (ou de tous si
    `with_pose` est faux). Renvoie (nouvelle liste, nombre de poses gardées).
    Un ennemi sans pose n'a pas de clé « m » (le receveur traite None et absent pareil)."""
    out, n = [], 0
    for e in enemies:
        m = e.get("m")
        if m and with_pose and near_any(e.get("pos") or (0, 0, 0), centers, dist):
            out.append(e)
            n += 1
        else:
            out.append({k: v for k, v in e.items() if k != "m"})
    return out, n


class RateMeter:
    """Débit glissant (octets/s) sur une fenêtre de `window` secondes."""

    def __init__(self, window: float = 2.0):
        self.window = window
        self.events: list[tuple[float, int]] = []
        self.total = 0

    def add(self, n: int, now: float) -> None:
        self.events.append((now, n))
        self.total += n

    def rate(self, now: float) -> float:
        lim = now - self.window
        i = 0
        while i < len(self.events) and self.events[i][0] < lim:
            i += 1
        del self.events[:i]
        return sum(n for _t, n in self.events) / self.window
