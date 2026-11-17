"""Boîte aux lettres WWMP (étape 3) : côté PC.

Le graphic pack `graphicpack/WWMP_Mailbox` place une structure dans le codecave
de Cemu et l'interroge à chaque frame depuis le code de Link. Ce module la
retrouve (par sa magie) et y dépose des requêtes, exécutées sur le thread du jeu.

Pack v19 (synchro v2, lot 2) : commande 7 CALL en liste blanche (index 3 ``execItemGet``,
index 8 ``GRANT``), commande 9 ``COUNT``, commande 10 ``KEYADD``, 7 emplacements de fantômes
(8 joueurs) à +0x610. La version lue dans la boîte décide de tout : avec un pack v17 installé,
``n_slots`` vaut 4 (emplacements à +0x60) et les méthodes v19 lèvent ``MailboxError`` (le client
reste en mode dégradé). Contrat vérifié hors ligne par ``tests/test_pack_mailbox.py``.

Fantôme touchable (A1/A2, ``synchro/coups_fantome.py``) : capacité annoncée par le mot +0x79C (bit 0), à 0
dans un pack v19 plus ancien : ``Mailbox.ghost_hits``. TG des fantômes allumé par le PC (``set_ghost_tg``),
coups reçus lus dans une file de 8 entrées (``read_ghost_hits``), coup rejoué sur Link par CALL 9
(``coup_link``).

Monstres qui visent le fantôme (A3, ``synchro/cibles.py``) : bit 1 du même mot (``Mailbox.ghost_aggro``). Le PC
écrit la table des cibles (``set_aggro``) ; le jeu met la position du fantôme dans Link pendant l'execute de
chaque ennemi de la table seulement. La hauteur des yeux du fantôme part dans l'emplacement (+0x1C).

Mode Hanteurs, phases 3 et 4 (``synchro/ordres.py``) : bit 3 du même mot (``Mailbox.ordres``) : CALL 10 ``ordre`` force
l'état de l'IA d'un acteur depuis un état libre, d'après une liste blanche constante du pack (le PC ne choisit
qu'un index).
Bit 4 (``Mailbox.aggro_all``) : toutes les espèces (bitmap) et gardes par espèce évaluées par le jeu, zone
``wwmp_aggro2`` ajoutée en FIN de pack et trouvée par sa magie (``aggro_gardes``).
Bit 12 (``Mailbox.couleurs``) : une couleur de tenue par joueur (``commun/couleurs.py``), zone ``wwmp_coul`` en fin
de pack, trouvée par sa magie ; le PC n'y écrit que des masques de matériaux et des facteurs (``set_teinte``).
"""
from __future__ import annotations

import os
import re
import math
import struct
import threading
import time

from jeu.cemu import GuestMemory

MAGIC = b"WWMPMBX1"
SEARCH_LO, SEARCH_HI = 0x0000_0000, 0x1000_0000   # codecave + code, hors MEM2

OFF_VERSION, OFF_FRAMES, OFF_REQ, OFF_ACK = 0x08, 0x0C, 0x10, 0x14
OFF_CMD, OFF_ARG0, OFF_RESULT = 0x18, 0x1C, 0x2C

CMD_PING, CMD_IS_EVENT, CMD_ON_EVENT, CMD_CREATE, CMD_DELETE = 1, 2, 3, 4, 5
CMD_DISAPPEAR = 6                            # v17 : fopAcM_createDisappear
DISAPPEAR_MIN_VERSION = 17
# Fumée de mort (daDisappear, proc 400 sur HD [LIVE, désassemblé]) : paramètres
# (bit d'objet << 16) | (échelle << 8) | type. Types [DECOMP] : 0 objet tiré de la table de
# l'ennemi (itemTableIdx), 1 et 3 aucun objet (3 = mêmes effets que 0 sans le premier
# nuage), 2 réceptacle de cœur de boss. Échelle 10 = 1.0.
PROC_DISAPPEAR = 400
DROP_TABLE, DROP_NONE = 0, 3
OFF_CREATE_POS, OFF_CREATE_ANGLE = 0x40, 0x4C
OFF_SLOTS, SLOT_SIZE, N_SLOTS = 0x60, 0x20, 4          # emplacements de fantômes jusqu'au pack v18
OFF_SLOTS_V19, N_SLOTS_V19 = 0x610, 7                 # pack v19 : 8 joueurs = 7 fantômes
N_SLOTS_MAX = N_SLOTS_V19
ERROR_PROCESS_ID = 0xFFFFFFFF
OFF_ENEMY_SLOTS, N_ENEMY_SLOTS = 0xF0, 16   # v7

# --- pack v19 (docs/archives/recherche_pack_v18.md, sync2_coffres.md §3.8, sync2_cles.md §3.7,
# sync2_inventaire.md §3.8, sync2_cruches_drops.md §3.6 ; vérifications comprises) ---
V19 = 19
CMD_NOP, CMD_CALL, CMD_COUNT, CMD_KEYADD = 0, 7, 9, 10   # 8 : réservée (CREATE_FULL du contrat v18, non codée)
CALL_ITEM_GET, CALL_GRANT = 3, 8                      # seuls index CALL codés en v19 (les autres : -1)
GRANT_TBOX, GRANT_DUNGEON, GRANT_OCEAN, GRANT_ITEM = 0, 1, 2, 3
# genre 4 (mod natif 0.24.0, bit 31 CAP_GRANT_EVT ; jamais un pack Cemu) : drapeau d'événement mSave.mEvent, idx = octet,
# « bit » = masque entier (0xXXYY comme onEventBit) ; quarts de cœur des personnages (synchro/quarts_pnj.py)
GRANT_EVENT = 4
# codes communs des commandes v19
R_DONE, R_HAD, R_REFUSED, R_NO_GI, R_BUSY, R_STAGE, R_NOP = 1, 0, -1, -2, -3, -4, -6
COUNT_REFUSED, COUNT_BUSY = 0x80000000, 0x80000001
OFF_V19_DEBUG = 0x798                                 # dernier index CALL reçu (mise au point)
# --- pack v19, fantôme touchable (A1/A2, docs/etudes/sync2_ia_boss_cine.md §3.a ; synchro/coups_fantome.py) ---
OFF_CAPS = 0x79C                 # capacités (u32) : 0 dans un pack v19 plus ancien
CAP_GHOST_HITS = 0x1             # TG des fantômes, file des coups reçus, CALL 9 coup_link
CAP_GHOST_AGGRO = 0x2            # A3 : table des cibles, échange de position dans wwmp_exec_filter
CAP_ENEMY_HP_FREE = 0x4          # mode Hanteurs (laisse) : bit 1 de +0x17 d'un emplacement d'ennemi = PV non imposés
CAP_ORDRES = 0x8                 # mode Hanteurs, phases 3 et 4 : CALL 10 ORDRE (liste blanche wwmp_ordres, synchro/ordres.py)
# mode Hanteurs : CALL 11 EFFET (recettes constantes wwmp_effet_recettes, synchro/effets_pack.py) ; arg1 = recette,
# arg2 bit 0 = sans son, position en +0x40. Résultat : bits 0-2 = effet 1, effet 2, son ; -1 recette ou gestionnaire
# d'effets absent, -2 Link absent, -3 événement, -4 position refusée
CAP_EFFETS = 0x800               # bit 11
CALL_EFFET = 11
# mode Hanteurs, « barre dans le jeu » : CALL 12 JAUGE (zone wwmp_jauge, synchro/jauge.py). Le pack écrit air, max et
# status de dComIfGp (jauge circulaire du HUD, celle de la nage) : aucun appel du jeu, aucune accroche. arg1 = 1 poser
# (arg2 = images restantes, arg3 = images en tout) ou 0 rendre. Poser : 1 prise, 2 rafraîchie, -1 arguments, -2 Link
# absent, -3 événement, -4 jauge prise par le jeu (nage) ; rendre : 0 remise à zéro, -4 plus à nous (rien écrit)
CAP_BARRE_JEU = 0x4000           # bit 14
CALL_JAUGE = 12
# max <= 899 images : le facteur de vitesse de nage (1 - air / 900, 0x023F8858) reste dans (0, 1) quoi qu'il arrive,
# et max n'est jamais 900, la valeur posée par la nage (0x023F900C) qui sert à voir une écriture étrangère
JAUGE_MAX = 899
ORDRES_BASE = 18                 # index >= 18 : seconde table (wwmp_ordres2), seulement avec CAP_FORMES (bit 17)
CALL_ORDRE = 10
R_ACTEUR_ABSENT = -4             # CALL 10 : acteur introuvable
ENEMY_SUSPEND, ENEMY_HP_FREE = 0x01, 0x02   # octet +0x17 d'un emplacement d'ennemi
ENEMY_PILOTE = 0x04              # idem, bit 2 : monstre piloté (possession complète, CAP_PILOTE seulement)
OFF_HITS = 0x16A0                # cfg (PC), seq, inscriptions du TG, coups ignorés
HITS_CFG_TG = 0x1                # cfg bit 0 : TG des fantômes inscrit à chaque image
OFF_HITQ, N_HITQ, HITQ_SIZE = 0x16B0, 8, 0x40
HITS_SPAN = OFF_HITQ - OFF_HITS + N_HITQ * HITQ_SIZE
OFF_COUP = 0x18B0                # paramètres de CALL 9
CALL_COUP_LINK = 9
R_NO_OWNER = -4                  # CALL 9 : propriétaire (marionnette de l'ennemi) introuvable
COUP_ATP_MAX, COUP_SPL_MAX = 20, 15
_HIT_FMT = ">IIIIBBBBIBBH3f3fII4x"   # une entrée de la file (0x40 octets)
# --- pack v19, monstres qui visent le fantôme (A3, docs/etudes/sync2_ia_boss_cine.md §3.a « a3 » ; synchro/cibles.py) ---
OFF_AGGRO = 0x1A44               # cfg (PC), échanges, refus, 4 espèces permises (s16)
AGGRO_CFG_ON = 0x1
OFF_AGGRO_SEQ = OFF_AGGRO + 0x04   # séquence du jeu : impaire pendant un échange (Link porte la position d'un
                                   # fantôme), +2 par échange ; échanges faits = séquence // 2
OFF_AGGRO_PROCS = OFF_AGGRO + 0x0C
AGGRO_PROCS = (188, 189, 206, 215)   # Moblin, Bokoblin, Chuchu, Keese (recopiés dans l'asm)
OFF_AGGRO_TAB, N_AGGRO = OFF_AGGRO + 0x14, 16
# --- A3 pour toutes les espèces (bit 4 ; docs/etudes/sync3_aggro_tous.md) : zone en fin de pack, trouvée par sa
# magie (son décalage dépend de la taille du code : jamais lu à un décalage fixe) ---
CAP_AGGRO_ALL = 0x10              # bit 4 (le bit 3 est CAP_ORDRES, mode Hanteurs)
MAGIC_AGGRO2 = b"WWMPAGR2"
AGGRO2_FORMAT = 1
OFF2_GARDES_N = 0x0C             # refus par garde (jeu)
OFF2_GARDE_DERNIERE = 0x10       # dernière garde : proc << 16 | n° d'entrée
OFF2_PROCS = 0x18                # bitmap 64 octets (bit 0x80 >> (n & 7) de l'octet n >> 3)
OFF2_GARDES = 0x58               # 16 × 8 octets {proc s16, décalage u16, opération u8, 0, valeur u16}
AGGRO2_SPAN = 0x400              # le code de wwmp_aggro2 tient dans ce qui suit (recherche bornée au pack)
# --- une couleur de tenue par joueur (bit 12 ; docs/etudes/couleurs_link.md) : zone en fin de pack, trouvée par
# sa magie. Les bits 0 à 10 sont pris (5-7 : randomizer ; 8-10 : poids, saisie, gel) ; bit 11 : effets (CALL 11). ---
CAP_COULEURS = 0x1000
MAGIC_COUL = b"WWMPCOUL"
COUL_FORMAT = 1
OFF_COUL_N = 0x0C                # dessins teintés (jeu, mise au point)
OFF_COUL_SOI = 0x10              # notre Link : masque des matériaux (u32) + facteurs R, G, B (3 × f32)
OFF_COUL_SLOTS = 0x20            # 7 blocs de 0x10 octets, un par emplacement de fantôme
# mod natif 0.18.0 (réservés à 0 dans le pack Cemu) : compteurs de preuve écrits par le jeu
OFF_COUL_N_SOI = 0x94            # dessins de NOTRE Link teints
OFF_COUL_N_FANTOMES = 0x98       # dessins de fantômes teints
OFF_COUL_N_KONST0 = 0x9C         # matériaux dont konst0 (la lumière que lit le shader de Link) a été multiplié
COUL_COMPTEURS_MOD = 18          # NATIF_VERSION_MOD qui tient ces compteurs
# mod natif 0.20.0 (natif/mod_wwmp/couleurs.c : la texture de la tunique recolorée, posée sur le modèle) : mot +0x1C
# des sauvegardes 0 à 3 (0 dans le pack Cemu)
OFF_COUL_TEXTURES = 0xBC         # matériaux de la tenue dont la texture a été remplacée par sa copie recolorée
OFF_COUL_COPIES = 0xDC           # textures recolorées (une par couleur, 8 au plus)
OFF_COUL_KONST0_GPU = 0xFC       # repli : blocs « Mat » du modèle où konst0 teint a été écrit
OFF_COUL_REFUS = 0x11C           # refus vus (bits) : 1 tas plein, 2 konst0 sans place, 4 modèle sans blocs, 8 texture
COUL_TEXTURE_MOD = 20            # NATIF_VERSION_MOD qui teint par la texture
OFF_NATIF = 0x1AE0               # zone WWMPNATF du mod natif (natif/mod_wwmp/boite.h ; version du mod à +0x20)
COUL_BLOC = 0x10
N_COUL_SLOTS = 7
COUL_FACTEUR_MAX = 4.0
# --- difficulté : dégâts reçus multipliés (bit 13 ; docs/etudes/difficulte.md) : zone en fin de pack, trouvée par
# sa magie ; accroche 0x02594584 (compteur de vie de l'ATH) ---
CAP_DIFFICULTE = 0x2000
MAGIC_DIF = b"WWMPDIF1"
DIF_FORMAT = 1
OFF_DIF_N = 0x0C                 # multiplicateur (PC) : 2..4, toute autre valeur = jeu normal
OFF_DIF_COMPTE = 0x10            # pertes multipliées (jeu, mise au point)
DIF_N_MAX = 4
# --- poids du fantôme (bit 8 ; docs/etudes/sync3_mecanismes_rares.md §2.6, synchro/poids.py) : zone en fin de pack,
# trouvée par sa magie. Le jeu appelle le rappel « quelqu'un monte » du sol qui porte un fantôme (plateformes à
# poids, ponts de cordes, interrupteurs au sol), Link placé à la position du fantôme le temps de l'appel ---
CAP_POIDS = 0x100                # bit 8 (bits 5-7 : randomizer)
MAGIC_POIDS = b"WWMPPOID"
POIDS_FORMAT = 1
OFFP_CFG = 0x0C                  # PC : bit 0 = armé
OFFP_VIE = 0x10                  # PC : durée de vie en images, décomptée par le jeu (0 = inerte)
OFFP_FAITS = 0x14                # jeu : rappels faits
OFFP_REFUS = 0x18                # jeu : sols porteurs d'une espèce hors bitmap
OFFP_DERNIER = 0x1C              # jeu : proc << 16 | n° d'emplacement
OFFP_HAUT = 0x20                 # marge haute (entier, unités du jeu)
OFFP_BAS = 0x24                  # marge basse
OFFP_PROCS = 0x28                # bitmap 64 octets (bit 0x80 >> (n & 7) de l'octet n >> 3)
POIDS_CFG_ON = 1
# bit 15 : emplacements sans poids (spectres de Hanteurs) : bits 8 à 14 du cfg, un par emplacement 0 à 6, lus en tête
# de la boucle de wwmp_poids_run (un pack sans le bit ne lit que le bit 0 : ces bits y sont ignorés)
CAP_POIDS_SLOTS = 0x8000         # bit 15
POIDS_SANS_SHIFT = 8
POIDS_SANS_MASK = 0x7F           # 7 emplacements (N_SLOTS_V19)
POIDS_VIE_MAX = 600              # jamais plus de 20 s d'avance : client mort = jeu de nouveau comme avant
# --- saisie transmise (bit 9 ; docs/etudes/sync3_aggro_tous.md §8, synchro/saisie.py) : zone en fin de pack, trouvée
# par sa magie. Bouclier autour de l'execute d'un ennemi qui vise un fantôme : 7 méthodes de Link rendues sans
# effet, drapeaux de Link remis ; chaque appel bloqué est noté dans un anneau que le PC lit et transmet ---
CAP_SAISIE = 0x200               # bit 9
MAGIC_SAISIE = b"WWMPSAIS"
SAISIE_FORMAT = 1
OFFS_CFG = 0x0C                  # PC : bit 0 = bouclier armé
OFFS_BATTEMENT = 0x10            # PC : images ; le jeu décompte ; 1 -> 0 : cfg de wwmp_aggro remis à 0
OFFS_LEVE = 0x14                 # jeu : 1 pendant un execute protégé
OFFS_EVTS_N = 0x24               # jeu : événements écrits (total, publié après l'entrée)
OFFS_BLOQUES = 0x28              # jeu : appels bloqués et mots remis
OFFS_CONFUS = 0x2C               # PC (victime) : images de confusion restantes ; le jeu décompte
OFFS_CONFUS_POSE = 0x30          # jeu : bit de confusion posé par le pack
OFFS_VERROUS_N = 0x34            # jeu : verrous actifs
OFFS_COUPURES = 0x38             # jeu : coupures de wwmp_aggro par le battement
OFFS_VERROUS = 0x40              # 8 × {id de l'ennemi, id du fantôme, images restantes, procName}
OFFS_EVTS = 0xC0                 # 8 × {n°, genre, id de l'ennemi, procName, id du fantôme, a0, a1, répétitions}
N_SAISIE_EVTS = 8
N_SAISIE_VERROUS = 8
SAISIE_VERROU_SIZE = 0x10
SAISIE_VERROU_FRAMES = 90        # images posées par le pack à chaque appel bloqué (wwmp_saisie_lock)
SAISIE_EVT_SIZE = 0x20
SAISIE_CFG_ON = 1
SAISIE_BATTEMENT_MAX = 600       # jamais plus de 20 s d'avance
SAISIE_CONFUS_MAX = 600          # borne du pack (wwmp_saisie_f_borne)
# genres d'événement : 1-7 = méthode de Link bloquée ; 10-15 = mots de Link remis après l'execute
SAISIE_GENRES = {1: "setOutPower", 2: "setPlayerPosAndAngle (pos, angle)", 3: "setPlayerPosAndAngle (pos, csXyz)",
                 4: "setPlayerPosAndAngle (matrice)", 5: "setThrowDamage", 6: "morsure (dégâts)",
                 7: "morsure (prise)", 10: "drapeaux +0x3BC", 11: "mot +0x3B4", 12: "drapeaux +0x3B8",
                 13: "drapeaux +0x3C0", 14: "orientation", 15: "mode de démo"}
# --- Link gelé (bit 10 ; mode Hanteurs, synchro/gel.py) : zone en fin de pack, trouvée par sa magie. Tant que le PC
# la tient armée, l'execute de Link n'est pas appelé (le hook de la boîte l'est à sa place) : Link possédé par la
# potion ou Hanteur spectateur « collé » ne peut plus rien faire dans son jeu. Jamais pendant un événement ---
CAP_GEL = 0x400                  # bit 10
MAGIC_GEL = b"WWMPGEL1"
GEL_FORMAT = 1
OFFG_CFG = 0x0C                  # PC : bit 0 = armé
OFFG_VIE = 0x10                  # PC : durée de vie en images, décomptée par le jeu (0 = Link libre)
OFFG_GELEES = 0x14               # jeu : images où l'execute de Link a été sauté
OFFG_EVT = 0x18                  # jeu : images laissées au jeu pendant un événement
GEL_CFG_ON = 1
GEL_VIE_MAX = 600                # borne du pack (wwmp_gel_borne)
# --- Possession complète (bit 16 ; mode Hanteurs, synchro/pilotage.py) : zone en fin de pack, trouvée par sa magie.
# Un emplacement d'ennemi dont le bit 2 de +0x17 est posé est épinglé AVANT et APRÈS l'execute du monstre ; le
# temps de cet execute, Link peut valoir une « carotte » (point que l'IA prend pour le joueur). Rien ne se passe
# sans une échéance repoussée par le PC (compteur d'images de la boîte + PILOTE_IMAGES) ---
CAP_PILOTE = 0x10000             # bit 16 (11 : effets ; 12 et 13 : couleurs, difficulté ; 14 : jauge ; 15 : poids par emplacement)
MAGIC_PILOTE = b"WWMPPILO"
PILOTE_FORMAT = 1
OFFPI_ECHEANCE = 0x0C            # PC : compteur d'images jusqu'auquel le pilotage vaut
OFFPI_ECHANGES = 0x10            # jeu : échanges de Link (carotte) faits
OFFPI_EPINGLAGES = 0x14          # jeu : executes encadrés
OFFPI_REFUS = 0x18               # jeu : refus (événement, échéance, espèce, Link)
OFFPI_TAB, PILOTE_ENTREE = 0x20, 0x20      # 16 entrées, même index que les emplacements d'ennemis
PILOTE_CAROTTE = 1               # état d'une entrée, bit 0
# Formes spéciales (bit 17 des capacités) : autres bits de l'état d'une entrée. Un pack sans le bit 17 les
# ignorerait (il épinglerait toujours) : ``set_pilote`` refuse donc de les écrire sans la capacité.
CAP_FORMES = 0x20000             # bit 17 : drapeaux ci-dessous, ordres étendus (CALL 10, index >= 18)
PILOTE_SANS_EPINGLE = 0x02       # carotte seule : le monstre n'est jamais replacé (ni par wwmp_enemies)
PILOTE_XZ = 0x04                 # épinglage de x et z seulement (la hauteur reste celle du jeu)
PILOTE_VITESSES = 0x08           # vitesses non remises à zéro après l'execute
PILOTE_GARDE = 0x20              # épinglage gardé : rien d'imposé à l'image où la garde d'action ne tient pas
PILOTE_GARDE_U8 = 0x40           # la garde lit un octet (et non un s16) au décalage d'action
PILOTE_FORMES = PILOTE_SANS_EPINGLE | PILOTE_XZ | PILOTE_VITESSES | PILOTE_GARDE | PILOTE_GARDE_U8
PILOTE_IMAGES = 30               # avance de l'échéance écrite à chaque tick (1 s à 30 images/s)
PILOTE_IMAGES_MAX = 600          # borne du pack (échéance - images dans 1..600)
PILOTE_DECALAGE_MAX = 0x1000     # borne du pack sur le décalage du champ d'action
SLOT_EYE = 0x1C                  # emplacement de fantôme : hauteur des yeux (f32), A3
# --- randomizer coop, palier 1 (bit 5 ; docs/etudes/sync3_randomizer.md, rando/noyau.py) : zone en fin de pack,
# trouvée par sa magie ; stub N1 à la création des coffres (0x024B62C8) ---
CAP_RANDO = 0x20
MAGIC_RANDO = b"WWMPRND1"
RANDO_FORMAT = 1
OFFR_ETAT, OFFR_EMP, OFFR_RECHERCHES, OFFR_CHANGES, OFFR_DERNIER = 0x0C, 0x10, 0x14, 0x18, 0x1C
OFFR_PERMIS, OFFR_TABLE = 0x20, 0x120
RANDO_TABLE = 0x400              # 16 × 32 × 2 octets
RANDO_ETAT_CHARGE = 1
RANDO_SPAN = 0xC00               # zones + code (recherche bornée au pack ; étape 3 : zone et stub des cadeaux)
# randomizer complet, étape 2 (bit 6 ; docs/etudes/rando_complet.md §6.3) : même table, ÉTAT 2 = le stub prend la
# liste d'objets de la zone wwmp_rnd2 (magie « WWMPRND2 », constantes du pack, jamais écrites par le PC)
CAP_RANDO2 = 0x40
MAGIC_RANDO2 = b"WWMPRND2"
RANDO2_FORMAT = 1
OFFR2_PERMIS = 0x10
RANDO_ETAT_COMPLET = 2
RANDO_ETATS = (RANDO_ETAT_CHARGE, RANDO_ETAT_COMPLET)
# randomizer complet, étape 3 (bit 7 ; docs/etudes/rando_complet.md §6.4) : cadeaux de personnages et prix, stub N2
# dans fopAcM_createItemForPresentDemo (0x025D7DFC), zone wwmp_rnd3 (magie « WWMPRND3 », après le stub N1)
CAP_RANDO3 = 0x80
MAGIC_RANDO3 = b"WWMPRND3"
RANDO3_FORMAT = 1
OFFR3_ETAT, OFFR3_N, OFFR3_EMP, OFFR3_VUS, OFFR3_CHANGES, OFFR3_DERNIER_LR, OFFR3_DERNIER = (
    0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24)
OFFR3_TABLE = 0x30
RANDO3_MAX = 32                  # entrées de 8 octets { adresse de retour u32, 0 u16, origine u8, nouveau u8 }
RANDO3_TABLE = RANDO3_MAX * 8
# portage TWWHD, lot 2 (coffres « en conflit » : clé partagée entre deux niveaux ; rando/noyau.py SITES_CONFLIT) : zone
# wwmp_rnd4 (magie « WWMPRND4 », après la zone wwmp_rnd3). AUCUN bit de capacité : la zone trouvée, au bon format et
# avec la liste d'objets attendue, EST la capacité (un pack plus ancien ne l'a pas : mode dégradé, voir rando.py)
MAGIC_RANDO4 = b"WWMPRND4"
RANDO4_FORMAT = 1
OFFR4_ETAT, OFFR4_N, OFFR4_EMP, OFFR4_VUS = 0x0C, 0x10, 0x14, 0x18
OFFR4_TABLE = 0x20
RANDO4_MAX = 16                  # entrées de 8 octets { condensé du nom de niveau u32, clé u16, origine u8, nouveau u8 }
RANDO4_TABLE = RANDO4_MAX * 8
OFFR4_PERMIS = OFFR4_TABLE + RANDO4_TABLE        # 0xA0 : liste d'objets du stub N1 quand la zone est chargée
# portage TWWHD, lot 4 (objets progressifs ; rando/noyau.py FAMILLES_PROG) : zone wwmp_prg (magie « WWMPPRG1 », à la fin
# du pack). AUCUN bit de capacité : la zone trouvée, au bon format et avec la liste de paliers attendue, EST la capacité
MAGIC_PROG = b"WWMPPRG1"
PROG_FORMAT = 1
OFFP_ETAT, OFFP_EMP, OFFP_VUS = 0x0C, 0x10, 0x14
OFFP_PERMIS = 0x20               # 256 octets (constantes du pack) : 1 = palier d'un objet progressif
OFFP_CARTE = 0x120               # 256 octets (PC) : carte[palier] = objet que le prochain lieu de la famille donne
ITEM_NONE = 0xFF
# trackeur du randomizer : journal des dons (bit 18 ; rando/trackeur_client.py). Zone wwmp_trk (magie « WWMPTRK1 »,
# avant wwmp_prg) que le stub N2 remplit à CHAQUE appel de fopAcM_createItemForPresentDemo : format 2, 8 entrées de
# 32 octets { retour de l'appelant, objet d'origine, r11 de l'appelant, numéro, objet FINAL (donné par le jeu, après
# le remplacement N2 éventuel), 3 mots nuls } ; numéro écrit d'abord, objet final à la sortie du stub, puis le
# compteur (+0x0C) en dernier. Le PC ne fait que LIRE cette zone. Format 1 (sans objet final) : refusé.
CAP_TRK = 0x40000                # bit 18
MAGIC_TRK = b"WWMPTRK1"
TRK_FORMAT = 2
OFFT_COMPTEUR, OFFT_TAB = 0x0C, 0x10
TRK_N, TRK_ENTREE = 8, 32
# coffres fermés à un Hanteur (bit 19 ; mode Hanteurs, synchro/hanteur_coffres.py). Zone wwmp_hcof (magie « WWMPHCO1 »,
# avant wwmp_prg) ; accroche 0x024B74AC (`bl boxCheck` de daTbox::actionOpenWait) : tant que cfg bit 0 est posé et que
# l'échéance (compteur d'images de la boîte) n'est pas passée, un coffre fermé répond « Link hors de portée ». Rien
# sans une échéance repoussée par le PC (client arrêté : coffres rendus en HCOF_IMAGES images au plus)
CAP_HCOF = 0x80000               # bit 19
MAGIC_HCOF = b"WWMPHCO1"
HCOF_FORMAT = 1
OFFH_CFG, OFFH_ECHEANCE, OFFH_REFUS = 0x0C, 0x10, 0x14   # refus : images x coffres fermés chargés, drapeau valable (pas des appuis)
HCOF_CFG_ON = 1
HCOF_IMAGES = 60                 # avance de l'échéance écrite à chaque tick (2 s à 30 images/s)
HCOF_IMAGES_MAX = 600            # borne du pack (échéance - images dans 1..600)
# visée L/ZL (bit 20 ; mode Hanteurs, synchro/visee.py). Zone wwmp_visee (magie « WWMPVISE », avant wwmp_prg) ; deux
# sauts de même taille dans wwmp_ghost_slot et wwmp_enemies. Le pack recopie, à chaque image, des champs de visée
# (dAttention_c) dans l'acteur : fantôme (cfg bit 0) d'après +0x16 (drapeaux, ET 0x05 imposé par le pack) et +0x17
# (ligne de distance, 3 ou 0x22 seulement) de son emplacement ; marionnette à IA suspendue (cfg bit 1, +0x17 == 1)
# d'après +0x1C (y visé absolu, f32 ; 0 = rien) de son emplacement, drapeaux 4 si PV > 0 et bit du masque à 0
CAP_VISEE = 0x100000             # bit 20
# tête du Lion Rouge sur le bateau fantôme (bit 21 ; docs/etudes/BATEAU_TETE.md §5) : wwmp_model_execute fait pointer le
# +0x3B8 de la copie de l'acteur source vers un faux McaMorf dont +0x90 = la tête du WwmpModel. Rien à régler
# côté PC : le bit dit seulement que le pack installé contient la correction (mot complet 0x3FFFFF)
CAP_TETE_BATEAU = 0x200000       # bit 21
# voile, mât et gouvernail du bateau fantôme (bit 22 ; docs/etudes/BATEAU_TETE.md §6, synchro/voile.py) : wwmp_voile_bateau
# isole la coque du fantôme de notre bateau, wwmp_voile_cb (accroche 0x024718B0) pose ses articulations 5 à 10 d'après
# les matrices relatives que le PC écrit dans le WwmpModel (+0xD700 « VOIL », +0xD710). Mot complet 0xFFFFFF
CAP_VOILE_BATEAU = 0x400000      # bit 22
# cache-cache des Killer Bees à plusieurs (bit 23 ; synchro/cachecache.py). Zone wwmp_cache (magie « WWMPCCH1 », avant
# wwmp_prg) ; accroche 0x0229B54C (1re instruction de daNpc_Mk_Static_c::chkGameSet) : tant que cfg bit 0 est posé, la
# partie ne peut pas finir dans CE jeu (joueur qui aide la partie d'un autre : jamais de prix chez lui). PAS d'échéance
# (un client arrêté ne doit jamais rendre possible un second prix) : retiré par le PC, ou au redémarrage du jeu
CAP_CACHE = 0x800000             # bit 23
MAGIC_CACHE = b"WWMPCCH1"
CACHE_FORMAT = 1
OFFK_CFG, OFFK_REFUS, OFFK_APPELS = 0x0C, 0x10, 0x14
CACHE_CFG_ON = 1
# canon détruit chez un autre joueur (bit 24 ; synchro/canons.py) : CALL 13 CANON, arg1 = id de process d'un canon
# Ocanon (proc 63). Le pack appelle setMode(this, 0, 2) du canon (0x02330178) après PV = 0 et le son 0x2828 : l'init du
# mode 2 du jeu pose le switch, joue les particules de destruction et efface l'attention. Aucune zone, aucune accroche
CAP_CANON = 0x1000000            # bit 24 (le bit 22 est pris ailleurs)
CALL_CANON = 13
R_CANON_EFFETS = -5              # CALL 13 : gestionnaire de particules absent (rien n'est fait)
# coup d'un joueur distant INJECTÉ chez l'autorité (bit 25 ; synchro/coups_monstres.py, docs/etudes/COMBAT.md 6b, lot L4
# de la latence) : CALL 14 COUP_MONSTRE, paramètres dans la zone wwmp_cpe (magie « WWMPCPE1 », avant wwmp_prg). Pendant
# une image, un AT (copie du mCyl de Link, Set | VsEnemy) est posé sur le corps de l'ennemi, la copie du joueur du
# fantôme de l'attaquant pour propriétaire ; le jeu fait recul, garde, gel, son, mort. Accroches sur la 1re instruction
# d'at_power_check et de cc_at_check (sans effet hors de notre objet). Avec ce bit, la recette 7 de CALL 11 existe.
CAP_COUP_MONSTRE = 0x2000000     # bit 25
CALL_COUP_MONSTRE = 14
MAGIC_CPE = b"WWMPCPE1"
CPE_FORMAT = 1
OFFC_INJ, OFFC_APC, OFFC_CAC, OFFC_IMAGE, OFFC_SANS, OFFC_PRM = 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20
OFFC_EN_COURS, OFFC_STTS, OFFC_CYL = 0x58, 0x60, 0xA0
CPE_TAILLE = 0x1D0
CPE_RAYON = (5.0, 100.0)
CPE_HAUTEUR = (10.0, 300.0)
CPE_REL_MAX = 200.0
R_CPE_TOT = -5                   # CALL 14 : moins de 2 images après la précédente (rien n'est fait, à réessayer)
# coups des PROJECTILES (bit 26, MOD NATIF SEULEMENT : aucun pack Cemu ne l'annonce ; synchro/coups_projectiles.py,
# natif/mod_wwmp/projectiles.c). Chez l'attaquant : chaque marionnette à IA suspendue reçoit un TG (flèches, bombes,
# boomerang, grappins ; jamais l'épée ni le marteau), ses coups vont dans une file. Chez l'autorité : CALL 14 avec l'arme
# (bits 4 à 7 de l'octet des drapeaux) prend un FAUX ACTEUR typé pour propriétaire (Arrow, Bomb2, Boom, Himo2, neutre),
# placé à l'ennemi + la source écrite en +0x20 de la zone wwmp_prj (magie « WWMPPRJ1 »).
CAP_COUPS_PROJ = 0x4000000       # bit 26
MAGIC_PRJ = b"WWMPPRJ1"
PRJ_FORMAT = 1
OFFJ_CFG, OFFJ_MASQUE, OFFJ_SEQ, OFFJ_INSCR, OFFJ_IGNORES = 0x0C, 0x10, 0x14, 0x18, 0x1C
OFFJ_SRC, OFFJ_TYPEES, OFFJ_SONS, OFFJ_TG, OFFJ_FILE, OFFJ_FAUX = 0x20, 0x2C, 0x30, 0x40, 0x140, 0x3C0
PRJ_CFG_TG, PRJ_CFG_SON = 0x1, 0x2
PRJ_N_FILE, PRJ_ENTREE, PRJ_TAILLE = 8, 0x50, 0x2AC0
PRJ_RAYON = (5.0, 200.0)
PRJ_HAUTEUR = (10.0, 400.0)
PRJ_DY_MAX = 200.0
PRJ_SRC_MAX = 400.0
PRJ_TYPES_INTERDITS = 0x00010002  # épée, marteau : retirés du masque par le mod (chemin du lot L4)
ARMES_PRJ = {"fleche": 1, "bombe": 2, "boomerang": 3, "grappin": 4, "crochet": 5}   # arme -> code de CALL 14
# joueur ROBOT (bit 27, MOD NATIF SEULEMENT ; natif/mod_wwmp/robot.c, client/robot.py, docs/etudes/joueur_robot.md) :
# zone wwmp_rob (magie « WWMPROB1 ») ; CALL 15 ROBOT, arg1 = sous-commande 0 ÉTAT, 1 ALLER (étape, salle, point ou
# position écrits dans la zone), 2 PLACER (position du Link caché) ; cfg bit 0 = mode robot (Link caché, invulnérable).
CAP_ROBOT = 0x8000000            # bit 27
CALL_ROBOT = 15
MAGIC_ROB = b"WWMPROB1"
ROB_FORMAT = 1
OFFR_CFG, OFFR_ETAPE, OFFR_SALLE, OFFR_POINT, OFFR_POS, OFFR_ANGLE, OFFR_DRAP = 0x0C, 0x10, 0x18, 0x1C, 0x20, 0x2C, 0x30
OFFR_IMAGES = 0x34
ROB_TAILLE = 0x60
# canon du bateau fantôme (bit 28, MOD NATIF SEULEMENT, 0.14.0 ; natif/mod_wwmp/modele.c, jeu/models.py) : en mode
# recalcul, les emplacements 3 à 7 du WwmpModel sont dessinés SANS recalcul (modelEntryDL, matrices écrites par le PC) ;
# le PC y met le canon (daShip_c+0x418) du bateau distant quand il est sorti. Le pack Cemu les recalculerait (rappels
# du canon nourris par un userArea nul) : le client n'y met rien sans ce bit.
CAP_CANON_BATEAU = 0x10000000    # bit 28
# les autres joueurs sur la carte du jeu (bit 30, MOD NATIF SEULEMENT, 0.19.0 ; natif/mod_wwmp/carte.c, client/carte.py,
# docs/etudes/mini_carte.md) : zone wwmp_car (magie « WWMPCAR1 ») ; le PC écrit la couleur (RRGGBBAA) du marqueur de
# chaque emplacement de fantôme, le mod dessine les marqueurs (ballon d'un carré, carte marine, carte du donjon : 0.27.0)
# par l'API HUD du portage.
CAP_CARTE = 0x40000000           # bit 30
# vie partagée (MOD NATIF SEULEMENT, 0.30.0 ; natif/mod_wwmp/vie.c, synchro/vie_partagee.py) : SANS bit de capacité (le
# mot est plein) : la zone wwmp_vie (magie « WWMPVIE1 », format 1) trouvée dans la boîte EST la capacité. CALL 18 VIE :
# la correction de la vie commune ajoutée à la vie de Link par le jeu (bornée à [0, vie max]) ; résultat : la vie après,
# ou un refus (aucune écriture) : -1 arguments / mode robot, -2 pas de partie, -3 événement, -7 changement d'étape,
# -8 Link absent, -9 message ou menu, -10 Link mort ou vie à 0, -11 variation de vie en attente, -12 vie incohérente.
CALL_VIE = 18
MAGIC_VIE = b"WWMPVIE1"
VIE_FORMAT = 1
OFFV_APPELS, OFFV_FAITS, OFFV_REFUS, OFFV_DERNIER = 0x0C, 0x10, 0x14, 0x18
# capacités des monstres possédés (MOD NATIF SEULEMENT, 0.34.0 ; natif/mod_wwmp/capacites.c, synchro/capacites.py) :
# SANS bit de capacité : la zone wwmp_cap (magie « WWMPCAP1 », format 1, à +0x6A00) trouvée dans la boîte EST la
# capacité. CALL 20 CAPACITE(id de l'acteur, index) : l'action native propre à l'espèce, depuis un état « libre » ;
# résultat 1 fait, 0 pas libre (rien écrit), -1 index ou espèce, -2 capacité coupée, -3 événement, -4 introuvable,
# -8 Link absent (Moblin). Le PC écrit +0x10 (capacités coupées, bit par index) et +0x14 (garde du Darknut, images).
CALL_CAP = 20
MAGIC_CAP = b"WWMPCAP1"
CAPA_FORMAT = 1
CAPA_N = 3
OFFA_NOMBRE, OFFA_COUPEES, OFFA_GARDE, OFFA_APPELS = 0x0C, 0x10, 0x14, 0x18   # capacités (pas OFFK_ : cache-cache)
CAPA_GARDE_MIN, CAPA_GARDE_MAX = 15, 120
# enfermement des Hanteurs (MOD NATIF SEULEMENT, 0.35.0 ; natif/mod_wwmp/enfermement.c, synchro/enfermement.py) : SANS bit
# de capacité, la zone wwmp_enf (magie « WWMPENF1 », format 1, à 0x6A80) trouvée dans la boîte EST la capacité. CALL 21
# ENF(vie en images 0..300, rayon 100..5000, salle -1..63), centre à +0x40 : 1 armé, 2 entretenu, 0 libéré ; refus :
# -1 arguments / robot, -2 pas de partie, -3 événement, -4 centre refusé, -5 autre salle, -6 Link hors du cercle
# (+ 300), -7 changement d'étape, -8 Link absent, -10 Link mort.
CALL_ENF = 21
MAGIC_ENF = b"WWMPENF1"
ENF_FORMAT = 1
ENF_VIE_MAX = 300
ENF_RAYON_MIN, ENF_RAYON_MAX = 100, 5000
OFFE_ARME = 0x0C                 # jeu : 1 armé ; puis vie (s32), centre (3 f32), rayon (f32), salle (s32)
OFFE_APPELS = 0x38               # jeu : appels, armements, refus, dernier, repoussés, chocs, libérations, raison, evt,
                                 # effets, sons
ENF_RAISONS = {0: "", 1: "durée écoulée", 2: "salle changée", 3: "changement d'étape", 4: "Link mort",
               5: "Link réapparu loin", 6: "Link absent", 7: "libéré"}
# commandes à la manette (MOD NATIF SEULEMENT, 0.37.0 ; natif/mod_wwmp/manette.c, commun/commandes.py, synchro/hanteurs
# Touches) : zone wwmp_man (magie « WWMPMAN1 », AUCUN bit : la zone trouvée EST la capacité), 0x40 octets à 0x6B00.
# Filtre d'entrée du SDK : le mod relève les boutons tenus (wwhd_input_read, avant les règles du portage : L compris),
# compte chaque combinaison COMPLÉTÉE (fronts, u8 qui tourne) et la retire au jeu jusqu'au relâchement de ses boutons.
# Masques en code compact 16 bits (commandes.compact) écrits par le PC ; 0 = emplacement libre.
MAGIC_MAN = b"WWMPMAN1"
MAN_FORMAT = 1
OFFM_TENUS, OFFM_FRONTS, OFFM_MASQUES = 0x0C, 0x10, 0x20
MAN_N, MAN_TAILLE = 16, 0x40
# fantôme soulevable par Link (MOD NATIF SEULEMENT, 0.38.0 ; natif/mod_wwmp/porter.c, client/essai_porter.py, lot P0 de
# docs/etudes/porter_coequipier.md) : zone wwmp_por (magie « WWMPPOR1 », AUCUN bit), 0x80 octets à 0x6C00. Le PC lève
# le bit de l'emplacement d'un fantôme « portable » (le mod lui pose les champs d'attention de Médolie : « Soulever » au
# bouton A) ; le jeu le saisit, le porte, le pose, le lance ; le mod compte (prises, posés, lancés, lâchés, pas tenus).
# 0.39.0 (lot P1 « porteur ») : zone agrandie à 0xC0 (format toujours 1, champs de 0.38.0 inchangés) : o mesuré (position
# du fantôme tenu dans le repère de Link), refus par emplacement (CARRY retiré), raison et nombre des fins, ligne de
# distance réglable, cible du bouton A de Link (distance, priorité face à une jarre).
MAGIC_POR = b"WWMPPOR1"
POR_FORMAT = 1
OFFPO_PORTABLES, OFFPO_HAUTEUR, OFFPO_TENUS = 0x0C, 0x10, 0x14   # OFFPO_ : OFFP_ est pris par le poids (OFFP_REFUS = 0x18)
POR_TAILLE = 0x80
POR_CLES = ("tenus", "tenu_id", "emplacement", "souleves", "poses", "lances", "laches", "images_tenu", "images_prise",
            "proc", "fin_proc", "marques", "retires")       # mots de +0x14 à +0x47, dans l'ordre
POR_P1_MOD = 39                    # NATIF_VERSION_MOD qui a la partie P1 (zone de 0xC0 octets)
POR_P1_TAILLE = 0xC0
OFFPO_O, OFFPO_REFUS, OFFPO_LIGNE, OFFPO_CIBLE = 0x58, 0x74, 0x7C, 0x80
POR_LIGNE_MAX = 0x2D               # le mod prend 0x27 (Médolie) pour 0 ou au-delà
POR_FINS = {0: "aucune", 1: "posé", 2: "lancé", 3: "autre", 4: "absent", 5: "retiré"}   # fin_raison (POR_FIN_*)
# objets progressifs de TWWHD du randomizer (MOD NATIF SEULEMENT, 0.40.0 ; natif/mod_wwmp/rando_plus.c, rando/plus.py) :
# zone wwmp_prg2 (magie « WWMPPRG2 », AUCUN bit), 0x40 octets à 0x6CC0. Les familles actives viennent du PLAN du paquet
# généré (le jeu les recopie en +0x0C) ; le jeu compte chaque don progressif par famille (+0x20, total EN DERNIER en
# +0x1C) ; le PC n'écrit que la session (+0x10 : un numéro tiré par lui, la zone revenant à 0 à chaque démarrage du jeu)
MAGIC_PRG2 = b"WWMPPRG2"
PRG2_FORMAT = 1
OFFQ_FAMILLES, OFFQ_SESSION, OFFQ_BRUTS, OFFQ_DERNIER, OFFQ_TOTAL, OFFQ_RECUS = 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20
PRG2_N, PRG2_TAILLE = 6, 0x40
PRG2_MOD = 40                      # NATIF_VERSION_MOD qui a la zone
POR_P2_MOD = 41                    # 0.41.0 : o retenu aux pas stables (GrabWait), o_brut, moyenne (o_somme / stables)
OFFPO_O_BRUT = 0x98
# le Link porté (MOD NATIF SEULEMENT, 0.41.0 ; natif/mod_wwmp/porte.c, client/essai_porte.py, lot P2 de
# docs/etudes/porter_coequipier.md) : zone wwmp_prt (magie « WWMPPRT1 », AUCUN bit), 0x100 octets à 0x6E00. CALL 22
# PRT(vie 1..300, emplacement du fantôme du porteur 0..6, drapeaux | angle relatif << 16), o à +0x40 (3 f32, ±300) :
# 1 armé, 2 entretenu, 0 libéré ; refus : -1 arguments / robot, -2 pas de partie, -3 événement, -4 o refusé, -5 porteur
# absent, -6 Link trop loin de la cible (400 u), -7 changement d'étape, -8 Link absent, -9 procédure exclue, -10 Link
# mort, -11 Link enfermé (Hanteurs).
CALL_PRT = 22
MAGIC_PRT = b"WWMPPRT1"
PRT_FORMAT = 1
PRT_VIE_MAX = 300
PRT_ANGLE_GARDE, PRT_STICK_LIBRE = 0x1, 0x2
OFFT_ARME = 0x0C
OFFT_APPELS = 0x48
OFFT_PROCS = 0x90
PRT_RAISONS = {0: "", 1: "durée écoulée", 2: "porteur absent", 3: "salle changée", 4: "changement d'étape",
               5: "événement", 6: "Link mort", 7: "dégâts", 8: "grappin", 9: "descendu (PC)", 10: "Link loin de la cible",
               11: "plafond", 12: "chute", 13: "sortie (eau, échelle, corde, bateau…)", 14: "Link absent",
               15: "mode robot ou enfermement", 16: "L3 : descendu"}
PRT_REFUS = {-1: "arguments ou mode robot", -2: "pas de partie", -3: "événement", -4: "o refusé", -5: "porteur absent",
             -6: "Link trop loin du porteur", -7: "changement d'étape", -8: "Link absent", -9: "procédure exclue",
             -10: "Link mort", -11: "Link enfermé"}
# GRANT genre 4 (bit 31, MOD NATIF SEULEMENT, 0.24.0 ; natif/mod_wwmp/commandes.c, synchro/quarts_pnj.py) : CALL 8 pose
# un drapeau d'événement (octet + masque entier de mSave.mEvent) PUIS donne l'objet, dans la même image, hors événement
CAP_GRANT_EVT = 0x80000000       # bit 31
# noms au-dessus des têtes et signal « viens ici » (MOD NATIF SEULEMENT, 0.33.0 ; natif/mod_wwmp/reperes.c,
# client/reperes.py) : SANS bit (le mot est plein) : la zone wwmp_sig (magie « WWMPSIG1 », format 1) EST la capacité.
# Le PC écrit cfg, le nom (Latin-1, 15 octets au plus) de chaque emplacement avec l'id de process de SON fantôme, et les
# signaux reçus (durée en pas EN DERNIER) ; le jeu relève le double clic du stick gauche (position puis compteur).
MAGIC_SIG = b"WWMPSIG1"
SIG_FORMAT = 1
SIG_NOMS, SIG_SIGNAUX, SIG_MANETTE = 0x1, 0x2, 0x4
OFFS_CFG, OFFS_DEMANDES, OFFS_POS, OFFS_IDS, OFFS_VUS, OFFS_NOMS, OFFS_SIG = 0x0C, 0x10, 0x14, 0x20, 0x3C, 0x40, 0xB0
SIG_N, SIG_NOM, SIG_ENTREE = 4, 16, 0x14
SIG_MOD = 33                       # NATIF_VERSION_MOD qui a la zone
# bandeau (MOD NATIF SEULEMENT, 0.44.0 ; natif/mod_wwmp/bandeau.c, client/bandeau.py ; lot 0 de
# docs/etudes/autres_zelda.md §1.3) : SANS bit : la zone wwmp_ban (magie « WWMPBAN1 », format 1), 0x100 octets à 0x7100,
# EST la capacité. Le PC écrit les 4 lignes {rgba, taille, Latin-1 44 octets}, la durée (pas) et l'ancre, puis seq EN
# DERNIER ; le jeu prend le seq nouveau et décompte (suspendu en cinématique, menu, fondu, changement d'étape).
MAGIC_BAN = b"WWMPBAN1"
BAN_FORMAT = 1
BAN_N, BAN_TEXTE, BAN_LIGNE = 4, 44, 0x34
BAN_ALLUME = 0x1
BAN_HAUT, BAN_DROITE, BAN_BAS = 0, 1, 2
BAN_DUREE_MAX = 9000               # pas (5 min), borne du mod
OFFN_CFG, OFFN_SEQ, OFFN_DUREE, OFFN_ANCRE, OFFN_VU, OFFN_RESTE = 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20
OFFN_MONTRES, OFFN_DESSINS, OFFN_ATTENTES, OFFN_LIGNES = 0x24, 0x28, 0x2C, 0x30
BAN_MOD = 44                       # NATIF_VERSION_MOD qui a la zone
# chant de ralliement (MOD NATIF SEULEMENT, 0.45.0 ; natif/mod_wwmp/ralliement.c, client/ralliement.py ;
# docs/etudes/autres_zelda.md §7) : SANS bit : la zone wwmp_tac (magie « WWMPTAC1 », format 1), 0x40 octets à 0x7280, EST
# la capacité. Le PC écrit cfg (TAC_ALLUME) et le morceau (6 ou 7) ; le jeu compte chaque morceau voulu joué (position,
# étape, salle, angle, puis « demandes » EN DERNIER) et rend le résultat du jugement INCHANGÉ (le jeu le refuse).
MAGIC_TAC = b"WWMPTAC1"
TAC_FORMAT = 1
TAC_ALLUME = 0x1
TAC_MORCEAUX = (6, 7)              # morceaux inutilisés du jeu (6 : 3/4 haut, bas, droite ; 7 : 4/4)
OFFL_CFG, OFFL_MORCEAU, OFFL_DEMANDES, OFFL_POS, OFFL_ETAPE, OFFL_SALLE = 0x0C, 0x10, 0x14, 0x18, 0x24, 0x2C
OFFL_JUGES, OFFL_IGNORES, OFFL_ANGLE = 0x30, 0x38, 0x3C
TAC_MOD = 45                       # NATIF_VERSION_MOD qui a la zone
# relever un coéquipier (MOD NATIF SEULEMENT, 0.48.0 ; natif/mod_wwmp/releve.c, client/releve.py ;
# docs/etudes/autres_zelda.md §5 ; décalages OFFRL_* : OFFR_* est pris par le randomizer et le robot) : SANS bit : la zone wwmp_rel (magie « WWMPREL1 », format 1), 0x80 octets à 0x7200, EST
# la capacité. Le PC écrit cfg (REL_ALLUME, seulement avec le deathlink), le délai, « possible » (rechargé) et air_max ;
# le jeu tient Link À TERRE au lieu de la mort (changeDeadProc remplacé), CALL 19 : 0 ÉTAT, 1 RELEVER(vie), 2 ABANDON.
MAGIC_REL = b"WWMPREL1"
REL_FORMAT = 1
REL_ALLUME = 0x1
CALL_REL = 19
REL_DELAI_MIN, REL_DELAI_MAX = 90, 1800      # pas (bornes du mod)
REL_POSSIBLE_MAX = 300
REL_AIR_MAX = 150
REL_DEBOUT, REL_CHUTE, REL_A_TERRE, REL_RELEVE, REL_MORT = range(5)
REL_RAISONS = {1: "fée en flacon", 2: "personne pour te relever", 3: "à la nage", 4: "à bord", 5: "accroché, échelle, "
               "escalade ou corde", 6: "chute trop longue", 7: "pas le joueur (robot)", 8: "délai écoulé",
               9: "abandon (serveur)", 10: "règle coupée", 11: "étape rechargée"}
OFFRL_CFG, OFFRL_DELAI, OFFRL_POSSIBLE, OFFRL_ETAT, OFFRL_RESTE, OFFRL_CHUTES = 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20
OFFRL_POS, OFFRL_TERRE, OFFRL_AIR_MAX, OFFRL_SECONDES, OFFRL_AUTRES = 0x4C, 0x58, 0x59, 0x5C, 0x64
REL_MOD = 48                       # NATIF_VERSION_MOD qui a la zone
# tenues à pouvoir, partie A (MOD NATIF SEULEMENT, 0.46.0 ; natif/mod_wwmp/tenues.c, client/tenues.py ;
# docs/etudes/autres_zelda.md §6) : SANS bit : la zone wwmp_ten (magie « WWMPTEN1 », format 1), 0x40 octets à 0x72C0, EST
# la capacité. Le PC écrit les facteurs (huitièmes, 8 = x1, 0 lu comme 8 ; le mod les borne), la couleur et le duo, puis
# cfg EN DERNIER ; avant chaque CALL 14, les facteurs de l'attaquant distant (pris par le mod au CALL). Le jeu compte.
MAGIC_TEN = b"WWMPTEN1"
TEN_FORMAT = 1
TEN_OFF = 0x72C0
TEN_ALLUME, TEN_MAGIE, TEN_MUNITIONS = 0x1, 0x2, 0x4
OFFU_CFG, OFFU_COULEUR, OFFU_DUO, OFFU_FACTEURS, OFFU_DISTANT, OFFU_COMPTEURS = 0x0C, 0x10, 0x11, 0x14, 0x20, 0x24
# facteurs (octets à partir de +0x14), dans l'ordre de la zone
TEN_FACTEURS = ("recu", "epee", "gadget", "dos", "sol", "nage", "bateau", "rubis", "prix", "esquive")
# compteurs du jeu (u16 à partir de +0x24)
TEN_COMPTEURS = ("esquives", "jets", "coups", "dans_dos", "rubis_plus", "rubis_rendus", "magie_gardee",
                 "munitions_gardees", "recus", "pas_vite", "pas_bateau", "distants")
TEN_MOD = 46                       # NATIF_VERSION_MOD qui a la zone
# tenues à pouvoir, partie B : les FUSIONS (0.47.0 ; natif/mod_wwmp/fusions.c, client/fusions.py) : champs réservés de
# WWMPTEN1 (+0x12 fusion en cours, +0x13 rôle, +0x1E facteur de fusion de tous nos coups, +0x1F des objets à magie ;
# avant CALL 14 : +0x23 et +0x3C pour l'attaquant distant) ; zone wwmp_fus (magie « WWMPFUS1 », format 1), 0x80 octets
# à 0x6F00 : manette partagée, fantôme caché, Link caché, bouclier, bonnet ; « vie » rechargée, cfg EN DERNIER.
OFFU_FUSION, OFFU_ROLE, OFFU_F_FUSION, OFFU_F_MAGIE, OFFU_D_FUSION, OFFU_D_MAGIE = 0x12, 0x13, 0x1E, 0x1F, 0x23, 0x3C
TEN_FUS_TOURNOYANTE, TEN_FUS_MEGA_BOMBE = 1, 2
TEN_FUS_MOD = 47
MAGIC_FUS = b"WWMPFUS1"
FUS_FORMAT = 1
FUS_OFF = 0x6F00
FUS_ALLUME = 0x1
FUS_VIE_MAX = 300                  # images (10 s), borne du mod
FUS_BOUTONS_PERMIS = 0x7050        # B, X, Y, R, ZR : seuls boutons que le mod accepte de retirer
FUS_STICK_G, FUS_STICK_D = 0x1, 0x2
FUS_BOUCLIER_MAX = 90
FUS_BONNET = 0x4                   # matériau 2 (bonnet) de notre Link
OFFF_CFG, OFFF_GENRE, OFFF_BOUTONS, OFFF_STICKS, OFFF_BOUCLIER, OFFF_BONNET, OFFF_VIE = (0x0C, 0x10, 0x14, 0x18, 0x1C,
                                                                                       0x20, 0x30)
OFFF_COMPTEURS = 0x34
FUS_COMPTEURS = ("pas", "filtres", "caches_vus", "bouclier_pas", "bonnets", "fins", "soi_caches", "coups",
                 "coups_magie", "distants", "etat", "retenus")
# VRAIE fusion (MOD NATIF SEULEMENT, 0.50.0 ; natif/mod_wwmp/fusions.c, client/fusions.py ; docs/dev/PORTAGE_NATIF.md
# §3.54 ; décalages OFFFN_*, noms FEN_* : propres à cette zone) : SANS bit : la zone wwmp_fen (magie « WWMPFEN1 »,
# format 1), 0x80 octets à 0x6F80, EST la capacité. PILOTE : le PC écrit l'état distant de l'attaquant (boutons, sticks)
# puis un numéro NOUVEAU EN DERNIER, dès sa réception ; le jeu l'injecte dans la manette, le tient jusqu'au suivant et
# relâche tout après « peremption » lectures sans état nouveau. ATTAQUANT : le jeu publie à chaque lecture ce que le
# joueur presse (numéro EN DERNIER), que le PC lit à haute fréquence, et le retire à son Link.
MAGIC_FEN = b"WWMPFEN1"
FEN_FORMAT = 1
FEN_OFF = 0x6F80
FEN_PILOTE, FEN_ATTAQUANT = 0x1, 0x2
FEN_BOUTONS = 0x7010                # B, X, Y, R : seuls boutons distants que le mod injecte
FEN_NEUTRES = 0x0006FFF0            # retirés chez l'attaquant (jamais +, -, Home)
FEN_PEREMPTION_DEF, FEN_PEREMPTION_MIN, FEN_PEREMPTION_MAX = 8, 2, 60     # lectures de la manette (bornes du mod)
OFFFN_CFG, OFFFN_PEREMPTION, OFFFN_D, OFFFN_D_SEQ, OFFFN_M, OFFFN_M_SEQ = 0x0C, 0x10, 0x14, 0x28, 0x2C, 0x44
OFFFN_COMPTEURS = 0x48
FEN_COMPTEURS = ("age", "frais", "etats", "injections", "peremptions", "neutres", "visees", "appliques", "attendus")
FEN_MOD = 50                       # NATIF_VERSION_MOD qui a la zone
# camouflage de la tenue jaune (MOD NATIF SEULEMENT, 0.49.0 ; natif/mod_wwmp/camouflage.c, client/camouflage.py ;
# docs/dev/PORTAGE_NATIF.md §3.53 ; décalages OFFCM_*, noms CAM_* : propres à cette zone) : SANS bit : la zone wwmp_cam
# (magie « WWMPCAM1 », format 1), 0x80 octets à 0x6D00, EST la capacité. Le PC écrit cfg (CAM_ALLUME), « duree » (images)
# puis un numéro NOUVEAU EN DERNIER : le jeu arme le camouflage de notre Link ; « transparents » : fantômes camouflés
# dessinés une image sur deux. Le jeu décompte, finit au premier coup porté (révélé) et compte.
MAGIC_CAM = b"WWMPCAM1"
CAM_FORMAT = 1
CAM_OFF = 0x6D00
CAM_ALLUME = 0x1
CAM_DUREE_MIN, CAM_DUREE_MAX = 15, 600       # images (bornes du mod)
CAM_AUCUN, CAM_ACTIF, CAM_REVELE, CAM_EXPIRE, CAM_COUPE = range(5)
OFFCM_CFG, OFFCM_DUREE, OFFCM_NUMERO, OFFCM_TRANSPARENTS, OFFCM_VU, OFFCM_RESTE, OFFCM_ETAT = (0x0C, 0x10, 0x14, 0x18,
                                                                                              0x1C, 0x20, 0x24)
OFFCM_COMPTEURS = 0x28
CAM_COMPTEURS = ("debuts", "revelations", "expirations", "masques", "laisses", "pas", "evt", "cc_rendus", "sautes",
                 "dessines", "coupes", "dernier_proc")
CAM_MOD = 49                       # NATIF_VERSION_MOD qui a la zone
MAGIC_CARTE = b"WWMPCAR1"
CARTE_FORMAT = 1
OFFC_COUPE, OFFC_COULEURS, OFFC_DESSINS = 0x0C, 0x10, 0x2C
OFFC_MOI = 0x5C                    # 0.31.0 : RRGGBBAA du bonnet de NOTRE icône (0 : icône du jeu telle quelle)
CARTE_MOI_MOD = 31                 # NATIF_VERSION_MOD qui lit +0x5C (avant : mot de réserve, jamais relu)
CARTE_TAILLE = 0x80                # 0.27.0 : + compteurs de la carte du donjon (+0x60..+0x6C)
# boulets du canon du bateau (bit 29, MOD NATIF SEULEMENT, 0.17.0 ; natif/mod_wwmp/boulets.c, synchro/boulets.py) : zone
# wwmp_bou (magie « WWMPBOU1 »). Chez le tireur : file des tirs de NOTRE bateau (cfg bit 0). Chez les autres : CALL 16
# BOULET (cfg bit 1) crée une copie inerte du boulet (départ et vitesses du jeu) ; « fin » d'un emplacement la retire.
CAP_BOULETS = 0x20000000         # bit 29
CALL_BOULET = 16
MAGIC_BOU = b"WWMPBOU1"
BOU_FORMAT = 1
OFFB_CFG, OFFB_SEQ, OFFB_COPIES, OFFB_RETIREES, OFFB_INTERCEPTEES, OFFB_REFUS = 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20
OFFB_SONS = 0x24                 # (jeu) sons du tir joués par CALL 16 (mod 0.28.0)
OFFB_POS, OFFB_ANGLE, OFFB_SALLE, OFFB_COPIE, OFFB_TIR = 0x30, 0x3C, 0x44, 0x50, 0xD0
BOU_CFG_TIRS, BOU_CFG_COPIES = 0x1, 0x2
BOU_N_COPIES, BOU_N_TIRS, BOU_COPIE, BOU_TIR, BOU_TAILLE = 8, 8, 0x10, 0x20, 0x1D0
BOU_POS_MAX = 1e6
R_BOULET_PLEIN, R_BOULET_CREATION = -5, -4
ROB_CFG_MODE, ROB_A_POSITION = 0x1, 0x1
ROB_ETAT_LINK, ROB_ETAT_LIBRE, ROB_ETAT_CHANGEMENT, ROB_ETAT_MODE, ROB_ETAT_EVENEMENT = 0x1, 0x2, 0x4, 0x8, 0x10
R_ROBOT_CHANGEMENT, R_ROBOT_LINK, R_ROBOT_MENU = -7, -8, -9
ROB_POS_MAX = 1e6
# vent du Requiem (MOD NATIF SEULEMENT, 0.22.0 ; natif/mod_wwmp/vent.c, synchro/monde.py) : zone wwmp_ven (magie
# « WWMPVEN1 », AUCUN bit de capacité : la zone trouvée EST la capacité). Chez celui qui joue : compteur des Requiem
# (accroche de retour de Wind_CommitWind, écrit en dernier) et la direction que le jeu vient d'écrire ; chez les autres :
# direction demandée par le PC (numéro « demande » écrit en dernier), appliquée UNE fois par dKyw_tact_wind_set.
MAGIC_VENT = b"WWMPVEN1"
VENT_FORMAT = 1
OFFV2_REQUIEM, OFFV2_REQ, OFFV2_DEMANDE, OFFV2_XY, OFFV2_ON = 0x0C, 0x10, 0x14, 0x18, 0x1C
OFFV2_FAIT, OFFV2_APPLIQUES, OFFV2_ATTENTES = 0x20, 0x24, 0x28
VENT_TAILLE = 0x40
# ouverture d'un coffre vue chez les autres (MOD NATIF SEULEMENT, 0.25.0 ; natif/mod_wwmp/ouverture.c, synchro/coffres.py
# ChestRefresh) : zone wwmp_ouv (magie « WWMPOUV1 », AUCUN bit de capacité : la zone trouvée EST la capacité). CALL 17
# OUVERTURE(id du coffre) : couvercle, éclat, lumières et sons du jeu, sans objet, événement ni caméra ; le coffre reste
# en actionWait (« ouvert », comme figé). Résultat : emplacement 1..4, ou un refus (R_OUV_*).
MAGIC_OUV = b"WWMPOUV1"
OUV_FORMAT = 1
CALL_OUVERTURE = 17
OFFO_LANCEES, OFFO_FINIES, OFFO_INTERROMPUES, OFFO_REFUS, OFFO_IMAGES, OFFO_DERNIER = 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20
OFFO_OUV = 0x40                  # 4 x {id, adresse, images au départ, état}
OUV_N, OUV_ENTREE, OUV_TAILLE = 4, 0x10, 0x80
OUV_ACTIF, OUV_LUMIERES, OUV_COUVERCLE, OUV_FUMEE, OUV_SOL = 0x1, 0x2, 0x4, 0x8, 0x10
R_OUV_SANS_GI, R_OUV_EVENEMENT, R_OUV_ABSENT, R_OUV_ETAT, R_OUV_PROCHE, R_OUV_PLEIN = -2, -3, -4, -5, -6, -7

# états des marionnettes (MOD NATIF SEULEMENT, 0.29.0 ; natif/mod_wwmp/etats.c, synchro/etats_marionnettes.py) : zone
# wwmp_eta (magie « WWMPETA1 », AUCUN bit : la zone trouvée EST la capacité). Une entrée de 0x18 octets par emplacement
# d'ennemi (même index) : genre, a, b, c, image, décalage (PC, 0x14 octets écrits d'un coup), puis « vu » (jeu).
MAGIC_ETATS = b"WWMPETA1"
ETATS_FORMAT = 1
OFFE_COMPTEURS, OFFE_ENTREES, ETAT_ENTREE = 0x0C, 0x30, 0x18
ETATS_TAILLE = 0x1B0
ETATS_COMPTEURS = ("appliques", "refus", "ailes", "allumees", "eteintes", "retirees")


def lire_entree_prj(e: bytes) -> dict:
    """Une entrée (0x50 octets) de la file des coups portés de ``wwmp_prj`` (natif/mod_wwmp/projectiles.c)."""
    seq, pid, slot, typ = struct.unpack_from(">4I", e, 0)
    atp, spl, se, mark, gat, dmg, mtrl = struct.unpack_from(">BBBBIBB", e, 0x10)
    proc, = struct.unpack_from(">h", e, 0x1A)
    rvec = list(struct.unpack_from(">3f", e, 0x1C))
    pos = list(struct.unpack_from(">3f", e, 0x28))
    image, apid, objet = struct.unpack_from(">3I", e, 0x34)
    src = list(struct.unpack_from(">3f", e, 0x40))
    prop, = struct.unpack_from(">I", e, 0x4C)
    return {"seq": seq, "pid": pid, "slot": slot, "type": typ, "atp": atp, "spl": spl, "se": se, "mark": mark,
            "gat": gat, "dmg": dmg, "mtrl": mtrl, "proc": proc, "rvec": rvec, "pos": pos, "image": image,
            "apid": apid, "objet": objet, "src": src if prop else None, "proprio": prop}
# houle locale (synchro/houle.py) : zone wwmp_houle (magie « WWMPHOUL », avant wwmp_prg, AUCUN bit de capacité). Chaque
# image, le pack calcule la hauteur de la mer (daSea_calcWave 0x0246BA0C, si daSea_ChkArea 0x0246B6A4) sous Link
# (entrée 0) et sous le bateau (entrée 1), avec leur y de la même image, puis aux n points écrits par le PC (entrées 2+)
MAGIC_HOULE = b"WWMPHOUL"
HOULE_FORMAT = 1
OFFW_N, OFFW_IMAGES, OFFW_Y_LINK, OFFW_Y_BATEAU, OFFW_PTS = 0x0C, 0x10, 0x14, 0x18, 0x20
HOULE_PC_MAX = 14                # entrées 2 à 15
HOULE_HORS, HOULE_OK, HOULE_REFUS = 0, 1, 2   # état d'une entrée
MAGIC_VISEE = b"WWMPVISE"
VISEE_FORMAT = 1
OFFV_CFG, OFFV_MASQUE, OFFV_N_FANTOMES, OFFV_N_MARIONNETTES = 0x0C, 0x10, 0x14, 0x18
VISEE_FANTOMES, VISEE_MARIONNETTES = 0x1, 0x2      # bits de cfg
SLOT_VISEE = 0x16                # emplacement de fantôme : drapeaux (u8), ligne de distance (u8)
ENEMY_VISEE = 0x1C               # emplacement d'ennemi : y visé (f32)
VISEE_DRAPEAUX = frozenset({0, 1, 4})        # 0 rien, 1 LOCKON_MISC, 4 LOCKON_BATTLE (le pack garde 0x05 de toute façon)
VISEE_LIGNES = frozenset({0, 3, 0x22})       # 0 : lignes de l'acteur gardées ; le pack n'accepte que 3 et 0x22
VISEE_Y_MAX = 2097152.0          # borne du pack (|y| < 2^21)
# objets permis à CALL 3 (sync2_inventaire.md §3.8, liste corrigée par le vérificateur : sans 0x2E,
# 0x34, 0xAB-0xB0, 0xB2) ; recopiés dans l'asm (wwmp_give_ok), comparés par test_pack_mailbox
GIVE_OK = frozenset(set(range(0x1F, 0x2B)) | {0x2C, 0x2D} | set(range(0x2F, 0x32)) | {0x33, 0x35, 0x36}
                    | set(range(0x38, 0x3D)) | {0x3E, 0x42, 0x43} | set(range(0x45, 0x4C)) | {0x50}
                    | set(range(0x61, 0x6C)) | set(range(0x6D, 0x73)) | {0x77, 0x78, 0x82, 0x83}
                    | set(range(0x8C, 0x9F)))
# objets JAMAIS donnés par le pack (CALL 3 et GRANT) : 0x15 petite clé (synchro/cles.py R19 : compteur
# commun seulement), 0x2E main nue (écrase l'équipement des bracelets) ; asm : wwmp_never_give
NEVER_GIVE = frozenset({0x15, 0x2E})
# compteurs de la commande 9 : (nom, décalage dans dComIfGp, taille, delta min, delta max) [HD-ASM]
COUNTERS = (("rubis", 0x5B48, 4, -5000, 5000), ("fleches", 0x5B68, 2, -99, 99),
            ("bombes", 0x5B6C, 2, -99, 99), ("vie_max", 0x5B5E, 2, 1, 8), ("magie_max", 0x5B64, 2, 16, 32)) \
    + tuple((f"butin{k}", 0x5B70 + 2 * k, 2, -99, 99) for k in range(8))
COUNTER_INDEX = {c[0]: i for i, c in enumerate(COUNTERS)}
KEYADD_MAX = 9
TIMEOUT_V19 = 2.0
CANCEL_GRACE = 0.05     # s : après neutralisation, temps laissé au hook pour finir une requête déjà lue


def n_slots_for(version: int | None) -> int:
    """Emplacements de fantômes d'un pack de cette version (4 jusqu'à la v18, 7 depuis la v19)."""
    return N_SLOTS_V19 if (version or 0) >= V19 else N_SLOTS


def slots_offset_for(version: int | None) -> int:
    return OFF_SLOTS_V19 if (version or 0) >= V19 else OFF_SLOTS


# Version du graphic pack livrée avec ces outils (`.int N ; +0x08 version` dans
# graphicpack/WWMP_Mailbox/patch_mailbox.asm). Lue dans le fichier si possible, pour
# rester synchronisée ; la constante sert de repli.
PACK_VERSION = 19
ASM_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "graphicpack", "WWMP_Mailbox", "patch_mailbox.asm")


def expected_version() -> int:
    """Version du pack attendue par ces outils (celle du dépôt)."""
    try:
        with open(ASM_PATH, encoding="utf-8") as f:
            m = re.search(r"^\s*\.int\s+(\d+)\s*;\s*\+0x08 version", f.read(), re.M)
        if m:
            return int(m.group(1))
    except OSError:
        pass
    return PACK_VERSION


def _s16(v) -> int:
    """Angle s16 (accepte aussi 0..0xFFFF : 0xFF -> 255, 0xFFFF -> -1)."""
    v = int(v) & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


class MailboxError(RuntimeError):
    pass


class MailboxCancelled(MailboxError):
    """Requête v19 sans réponse dans le délai : neutralisée (commande 0) avant d'être exécutée.
    Elle ne s'exécutera pas plus tard : rien n'a été fait."""


def find(mem: GuestMemory) -> int | None:
    """Adresse de la boîte. Cemu : codecave (0x00000000-0x10000000). Portage natif (``mem.zone_boite``, jeu/natif.py) :
    globale du mod C dans la zone des mods, même disposition, validée par sa zone WWMPNATF."""
    if isinstance(getattr(mem, "zone_boite", None), tuple):
        from jeu import natif
        lo, hi = mem.zone_boite
        return natif.trouver_boite(mem, lo, hi)
    hits = [h for h in mem.find_bytes(MAGIC, SEARCH_LO, SEARCH_HI, limit=8) if h % 4 == 0]
    return hits[0] if hits else None


class Mailbox:
    # une seule requête à la fois, même entre threads (réception des coups / échantillonnage).
    # RLock : create()/disappear() le gardent autour de l'écriture de +0x40 ET de la requête
    # (le hook lit +0x40 à l'exécution, jusqu'à une image plus tard).
    _lock = threading.RLock()

    def __init__(self, mem: GuestMemory, addr: int | None = None):
        self.mem = mem
        self.addr = addr if addr is not None else find(mem)
        if self.addr is None:
            if getattr(mem, "hote", None) == "natif":
                raise MailboxError(
                    "boîte aux lettres introuvable : le mod « wwmp-natif » est-il activé dans le portage natif (F1 > "
                    "Mods, support des mods de code actif) et le jeu redémarré depuis ? (natif/README.md)")
            raise MailboxError(
                "boîte aux lettres introuvable : le graphic pack « WWMP Mailbox » est-il activé "
                "dans Cemu (Options → Graphic packs) et le jeu relancé depuis ?")
        # la version ne change pas tant que le jeu tourne (un autre pack = relancer le jeu = autre
        # boîte) : lue une fois, elle fixe la disposition des emplacements de fantômes
        try:
            self.pack = self.version()
        except Exception:
            self.pack = 0
        # fantôme touchable : mot de capacités, lu seulement en v19 (avant, +0x79C est du code)
        try:
            self.caps = self.u32(OFF_CAPS) if self.pack >= V19 else 0
        except Exception:
            self.caps = 0

    @property
    def n_slots(self) -> int:
        """Emplacements de fantômes de la boîte (4 avec le pack v17, 7 avec le v19)."""
        return n_slots_for(self.pack)

    @property
    def v19(self) -> bool:
        return self.pack >= V19

    @property
    def ghost_hits(self) -> bool:
        """Pack v19 avec le fantôme touchable (TG des fantômes, file des coups, CALL 9)."""
        return self.pack >= V19 and bool(self.caps & CAP_GHOST_HITS)

    @property
    def ghost_aggro(self) -> bool:
        """Pack v19 avec les monstres qui visent le fantôme (A3 ; demande aussi le fantôme touchable)."""
        return self.ghost_hits and bool(self.caps & CAP_GHOST_AGGRO)

    @property
    def aggro_all(self) -> bool:
        """Pack v19 dont A3 vaut pour toutes les espèces (bitmap + gardes, bit 4). Sans lui : les 4 espèces
        d'origine seulement (mode dégradé)."""
        return self.ghost_aggro and bool(self.caps & CAP_AGGRO_ALL)

    def aggro2_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_aggro2`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not self.aggro_all:
            return None
        a = getattr(self, "_aggro2", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_AGGRO2, self.addr, self.addr + 0x8000, limit=4)
                    if h % 4 == 0]
            if len(hits) != 1:
                return None
            a = self._aggro2 = hits[0]
        return a

    @property
    def couleurs(self) -> bool:
        """Pack v19 qui sait teinter la tenue de Link et de ses fantômes (bit 12 des capacités) et dont la zone
        ``wwmp_coul`` est trouvée, au bon format."""
        return self.couleurs_addr() is not None

    def couleurs_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_coul`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not (self.pack >= V19 and self.caps & CAP_COULEURS):
            return None
        a = getattr(self, "_coul", False)
        if a is False:
            a = None
            try:
                hits = [h for h in self.mem.find_bytes(MAGIC_COUL, self.addr, self.addr + 0x8000, limit=4)
                        if h % 4 == 0]
                if len(hits) == 1 and self.mem.u32(hits[0] + 8) == COUL_FORMAT:
                    a = hits[0]
            except Exception:
                a = None
            self._coul = a
        return a

    @property
    def difficulte(self) -> bool:
        """Pack v19 qui sait multiplier les dégâts reçus par Link (bit 13) et dont la zone ``wwmp_dif`` est
        trouvée, au bon format."""
        return self.difficulte_addr() is not None

    def difficulte_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_dif`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not (self.pack >= V19 and self.caps & CAP_DIFFICULTE):
            return None
        a = getattr(self, "_dif", False)
        if a is False:
            a = None
            try:
                hits = [h for h in self.mem.find_bytes(MAGIC_DIF, self.addr, self.addr + 0x8000, limit=4)
                        if h % 4 == 0]
                if len(hits) == 1 and self.mem.u32(hits[0] + 8) == DIF_FORMAT:
                    a = hits[0]
            except Exception:
                a = None
            self._dif = a
        return a

    def degats(self) -> tuple[int, int]:
        """(multiplicateur lu dans le jeu, nombre de pertes multipliées). (1, 0) sans la zone ; un multiplicateur
        hors de 2..4 vaut 1 (c'est ce que fait le jeu)."""
        a = self.difficulte_addr()
        if a is None:
            return 1, 0
        n, compte = struct.unpack(">2I", self.mem.read(a + OFF_DIF_N, 8))
        return (n if 2 <= n <= DIF_N_MAX else 1), compte

    def set_degats(self, n: int) -> bool:
        """Multiplicateur des dégâts reçus par Link : 1 (jeu normal, écrit 0) à 4. Donnée seule (pas de requête) :
        le jeu la lit à chaque perte de vie. Rien n'est écrit si la valeur y est déjà. Rend True si une écriture a
        eu lieu."""
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= DIF_N_MAX:
            raise ValueError(f"multiplicateur de dégâts {n!r} hors de 1..{DIF_N_MAX}")
        a = self.difficulte_addr()
        if a is None:
            raise MailboxError("ce graphic pack ne sait pas multiplier les dégâts (capacité « difficulté » absente)")
        voulu = 0 if n == 1 else n
        if self.mem.u32(a + OFF_DIF_N) == voulu:
            return False
        self.mem.w_u32(a + OFF_DIF_N, voulu)
        return True

    @staticmethod
    def verifier_teinte(bloc: bytes) -> bytes:
        """Bloc de teinte de 16 octets (masque u32, facteurs R, G, B f32) : facteurs finis dans 0..4, sinon
        ValueError (rien n'est écrit). Masque 0 : bloc nul."""
        if not isinstance(bloc, (bytes, bytearray)) or len(bloc) != COUL_BLOC:
            raise ValueError("bloc de teinte : 16 octets")
        masque, r, g, b = struct.unpack(">I3f", bytes(bloc))
        if not masque:
            return bytes(COUL_BLOC)
        for f in (r, g, b):
            if f != f or not 0.0 <= f <= COUL_FACTEUR_MAX:
                raise ValueError(f"facteur de teinte {f!r} hors de 0..{COUL_FACTEUR_MAX:g}")
        return bytes(bloc)

    def set_teinte(self, cible, bloc: bytes) -> None:
        """Teinte de la tenue : ``cible`` None = notre Link, 0..6 = fantôme de cet emplacement. Données seules
        (pas de requête) : le jeu les lit à chaque dessin. Le masque passe par 0 pendant l'écriture des facteurs
        (jamais de facteurs à moitié écrits avec un masque actif)."""
        a = self.couleurs_addr()
        if a is None:
            raise MailboxError("ce graphic pack ne sait pas teinter la tenue (capacité « couleurs » absente)")
        if cible is None:
            a += OFF_COUL_SOI
        else:
            if isinstance(cible, bool) or not isinstance(cible, int) or not 0 <= cible < N_COUL_SLOTS:
                raise ValueError(f"emplacement de teinte {cible!r} hors 0..{N_COUL_SLOTS - 1}")
            a += OFF_COUL_SLOTS + cible * COUL_BLOC
        bloc = self.verifier_teinte(bloc)
        self.mem.w_u32(a, 0)
        if bloc[:4] != bytes(4):
            self.mem.write(a + 4, bloc[4:])
            self.mem.write(a, bloc[:4])

    def teintes(self) -> dict:
        """Relecture (mise au point) : {"dessins", "soi", "fantomes"} ; blocs bruts de 16 octets."""
        a = self.couleurs_addr()
        if a is None:
            return {"dessins": 0, "soi": bytes(COUL_BLOC), "fantomes": []}
        brut = self.mem.read(a, OFF_COUL_SLOTS + N_COUL_SLOTS * COUL_BLOC)
        return {"dessins": struct.unpack(">I", brut[OFF_COUL_N:OFF_COUL_N + 4])[0],
                "soi": brut[OFF_COUL_SOI:OFF_COUL_SOI + COUL_BLOC],
                "fantomes": [brut[OFF_COUL_SLOTS + i * COUL_BLOC:OFF_COUL_SLOTS + (i + 1) * COUL_BLOC]
                             for i in range(N_COUL_SLOTS)]}

    def compteurs_teinte(self) -> dict | None:
        """Preuves écrites par le jeu (mod natif 0.18.0) : {"dessins", "soi", "fantomes", "konst0"} : dessins teints
        (tous, notre Link, fantômes) et matériaux dont konst0 a été multiplié. None sans la zone. Pack Cemu : les trois
        derniers restent à 0 (mots réservés)."""
        a = self.couleurs_addr()
        if a is None:
            return None
        n, = struct.unpack(">I", self.mem.read(a + OFF_COUL_N, 4))
        soi, fantomes, konst0 = struct.unpack(">3I", self.mem.read(a + OFF_COUL_N_SOI, 12))
        v = self.version_mod_natif()
        out = {"dessins": n, "soi": soi, "fantomes": fantomes, "konst0": konst0, "par_cible": v >= COUL_COMPTEURS_MOD}
        if v >= COUL_TEXTURE_MOD:       # mod 0.20.0 : la texture
            for cle, off in (("textures", OFF_COUL_TEXTURES), ("copies", OFF_COUL_COPIES),
                             ("konst0_gpu", OFF_COUL_KONST0_GPU), ("refus", OFF_COUL_REFUS)):
                out[cle], = struct.unpack(">I", self.mem.read(a + off, 4))
        return out

    def version_mod_natif(self) -> int:
        """NATIF_VERSION_MOD du mod natif (zone WWMPNATF qui suit la partie fixe), 0 pour le pack Cemu."""
        v = getattr(self, "_vmod", None)
        if v is None:
            v = 0
            try:
                raw = self.mem.read(self.addr + OFF_NATIF, 0x24)
                if raw[:8] == b"WWMPNATF":
                    v = struct.unpack_from(">I", raw, 0x20)[0]
            except Exception:
                v = 0
            self._vmod = v
        return v

    @property
    def poids(self) -> bool:
        """Poids du fantôme (bit 8 des capacités) et zone ``wwmp_poids`` trouvée, au bon format."""
        return self.poids_addr() is not None

    def poids_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_poids`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not (self.pack >= V19 and self.caps & CAP_POIDS):
            return None
        a = getattr(self, "_poids", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_POIDS, self.addr, self.addr + 0x8000, limit=4)
                    if h % 4 == 0]
            if len(hits) != 1 or self.mem.u32(hits[0] + 8) != POIDS_FORMAT:
                return None
            a = self._poids = hits[0]
        return a

    @property
    def poids_slots(self) -> bool:
        """Emplacements de fantômes sans poids (bit 15 des capacités) : la zone ``wwmp_poids`` lit les bits 8 à 14
        de son cfg."""
        return bool(self.caps & CAP_POIDS_SLOTS) and self.poids

    def set_poids(self, on: bool, vie: int = 0, sans: int = 0) -> bool:
        """Arme (cfg puis durée de vie, en images) ou coupe (durée de vie à 0 d'abord, puis cfg) le poids du
        fantôme. ``sans`` : masque des emplacements 0 à 6 qui ne pèsent pas (spectres de Hanteurs) ; écrit
        seulement si le pack a le bit 15, sinon cfg vaut exactement 1 comme avant. Données seules ; False sans la
        zone (pack plus ancien : rien n'est écrit)."""
        a = self.poids_addr()
        if a is None:
            return False
        if on:
            cfg = POIDS_CFG_ON
            if sans and self.caps & CAP_POIDS_SLOTS:
                cfg |= (int(sans) & POIDS_SANS_MASK) << POIDS_SANS_SHIFT
            self.mem.w_u32(a + OFFP_CFG, cfg)
            self.mem.w_u32(a + OFFP_VIE, max(0, min(int(vie), POIDS_VIE_MAX)))
        else:
            self.mem.w_u32(a + OFFP_VIE, 0)
            self.mem.w_u32(a + OFFP_CFG, 0)
        return True

    def poids_counters(self) -> dict:
        """Mise au point : {"cfg", "vie", "faits", "refus", "proc", "slot", "sans"} ; zéros sans la zone.
        ``sans`` : masque des emplacements sans poids lu dans cfg (bits 8 à 14)."""
        a = self.poids_addr()
        if a is None:
            return {"cfg": 0, "vie": 0, "faits": 0, "refus": 0, "proc": 0, "slot": 0, "sans": 0}
        cfg, vie, faits, refus, last = struct.unpack(">5I", self.mem.read(a + OFFP_CFG, 20))
        return {"cfg": cfg, "vie": vie, "faits": faits, "refus": refus, "proc": last >> 16, "slot": last & 0xFFFF,
                "sans": (cfg >> POIDS_SANS_SHIFT) & POIDS_SANS_MASK}

    def poids_procs(self) -> bytes:
        """Bitmap des espèces porteuses permises, tel que le pack le porte (lecture seule)."""
        a = self.poids_addr()
        return bytes(self.mem.read(a + OFFP_PROCS, 64)) if a is not None else b""

    @property
    def gel(self) -> bool:
        """Link gelé (bit 10 des capacités) et zone ``wwmp_gel`` trouvée, au bon format."""
        return self.gel_addr() is not None

    def gel_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_gel`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not (self.pack >= V19 and self.caps & CAP_GEL):
            return None
        a = getattr(self, "_gel", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_GEL, self.addr, self.addr + 0x8000, limit=4)
                    if h % 4 == 0]
            if len(hits) != 1 or self.mem.u32(hits[0] + 8) != GEL_FORMAT:
                return None
            a = self._gel = hits[0]
        return a

    def set_gel(self, on: bool, vie: int = 0) -> bool:
        """Gèle Link (cfg puis durée de vie, en images) ou le libère (durée de vie à 0 d'abord, puis cfg).
        Données seules ; False sans la zone (pack plus ancien : rien n'est écrit)."""
        a = self.gel_addr()
        if a is None:
            return False
        if on:
            self.mem.w_u32(a + OFFG_CFG, GEL_CFG_ON)
            self.mem.w_u32(a + OFFG_VIE, max(0, min(int(vie), GEL_VIE_MAX)))
        else:
            self.mem.w_u32(a + OFFG_VIE, 0)
            self.mem.w_u32(a + OFFG_CFG, 0)
        return True

    def gel_counters(self) -> dict:
        """Mise au point : {"cfg", "vie", "gelees", "evt"} ; zéros sans la zone."""
        a = self.gel_addr()
        if a is None:
            return {"cfg": 0, "vie": 0, "gelees": 0, "evt": 0}
        cfg, vie, gelees, evt = struct.unpack(">4I", self.mem.read(a + OFFG_CFG, 16))
        return {"cfg": cfg, "vie": vie, "gelees": gelees, "evt": evt}

    @property
    def saisie(self) -> bool:
        """Saisie transmise (bit 9 des capacités) et zone ``wwmp_saisie`` trouvée, au bon format."""
        return self.saisie_addr() is not None

    def saisie_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_saisie`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not (self.pack >= V19 and self.caps & CAP_SAISIE):
            return None
        a = getattr(self, "_saisie", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_SAISIE, self.addr, self.addr + 0x8000, limit=4)
                    if h % 4 == 0]
            if len(hits) != 1 or self.mem.u32(hits[0] + 8) != SAISIE_FORMAT:
                return None
            a = self._saisie = hits[0]
        return a

    def set_saisie(self, on: bool, battement: int = 0) -> bool:
        """Arme le bouclier (cfg) et recharge le battement (images), ou baisse le bouclier (cfg 0) ; le battement
        est écrit tel quel : 0 écrit ici n'est pas un passage 1 -> 0 vu par le pack, il ne coupe pas wwmp_aggro.
        Données seules ; False sans la zone."""
        a = self.saisie_addr()
        if a is None:
            return False
        if on:
            self.mem.w_u32(a + OFFS_CFG, SAISIE_CFG_ON)
            self.mem.w_u32(a + OFFS_BATTEMENT, max(0, min(int(battement), SAISIE_BATTEMENT_MAX)))
        else:                # bouclier baissé ; le battement reste tenu si demandé (A3 actif, --no-saisie)
            self.mem.w_u32(a + OFFS_BATTEMENT, max(0, min(int(battement), SAISIE_BATTEMENT_MAX)))
            self.mem.w_u32(a + OFFS_CFG, 0)
        return True

    def set_confusion(self, frames: int) -> bool:
        """Chez la victime : commandes inversées (possession du Poe) pendant ``frames`` images (0 : fin tout de
        suite ; le pack retire le bit qu'il a posé). Données seules ; False sans la zone."""
        a = self.saisie_addr()
        if a is None:
            return False
        self.mem.w_u32(a + OFFS_CONFUS, max(0, min(int(frames), SAISIE_CONFUS_MAX)))
        return True

    @staticmethod
    def parse_saisie_events(total: int, raw: bytes) -> list[dict]:
        """Anneau de 8 événements -> liste triée par n° (entrées vides ou plus récentes que ``total`` écartées)."""
        out = []
        for i in range(N_SAISIE_EVTS):
            n, genre, enemy, proc, ghost, a0, a1, rep = struct.unpack_from(">8I", raw, i * SAISIE_EVT_SIZE)
            if 0 < n <= total and n > total - N_SAISIE_EVTS and (n - 1) % N_SAISIE_EVTS == i:
                out.append({"n": n, "genre": genre, "enemy": enemy, "proc": proc, "ghost": ghost, "a0": a0,
                            "a1": a1, "rep": rep})
        return sorted(out, key=lambda e: e["n"])

    def read_saisie_events(self) -> tuple[int, list[dict]]:
        """(total, événements encore dans l'anneau). Lecture encadrée par le total : une entrée que le jeu a pu
        écrire pendant la lecture (n° au-delà du premier total lu) est laissée pour la lecture suivante."""
        a = self.saisie_addr()
        if a is None:
            return 0, []
        t1 = self.mem.u32(a + OFFS_EVTS_N)
        raw = self.mem.read(a + OFFS_EVTS, N_SAISIE_EVTS * SAISIE_EVT_SIZE)
        t2 = self.mem.u32(a + OFFS_EVTS_N)
        evts = self.parse_saisie_events(t1, raw)
        if t2 != t1:                       # le jeu a écrit pendant la lecture : l'entrée recouverte est écartée
            perdu = {(n - 1) % N_SAISIE_EVTS for n in range(t1 + 1, t2 + 1)}
            evts = [e for e in evts if (e["n"] - 1) % N_SAISIE_EVTS not in perdu]
        return t1, evts

    def saisie_verrous(self) -> list[dict]:
        """Verrous ACTIFS du bouclier (images restantes non nulles) : [{"i", "enemy", "ghost", "frames", "proc"}] ;
        liste vide sans la zone."""
        a = self.saisie_addr()
        if a is None:
            return []
        raw = self.mem.read(a + OFFS_VERROUS, N_SAISIE_VERROUS * SAISIE_VERROU_SIZE)
        out = []
        for i in range(N_SAISIE_VERROUS):
            enemy, ghost, frames, proc = struct.unpack_from(">4I", raw, i * SAISIE_VERROU_SIZE)
            if frames:
                out.append({"i": i, "enemy": enemy, "ghost": ghost, "frames": frames, "proc": proc})
        return out

    def clear_saisie_verrou(self, i: int, enemy: int) -> bool:
        """Éteint le verrou n° ``i`` s'il est encore celui de ``enemy`` (entrée relue juste avant) : images
        restantes à 0, ce que le pack lit comme une entrée libre (il recompte ses verrous à l'image suivante).
        Données seules ; False sans la zone, hors bornes, ou si l'entrée a changé de main."""
        a = self.saisie_addr()
        if a is None or not isinstance(i, int) or not 0 <= i < N_SAISIE_VERROUS:
            return False
        e = a + OFFS_VERROUS + i * SAISIE_VERROU_SIZE
        if self.mem.u32(e) != enemy or self.mem.u32(e + 8) == 0:
            return False
        self.mem.w_u32(e + 8, 0)
        return True

    def saisie_counters(self) -> dict:
        """Mise au point : cfg, battement, levé, événements, bloqués, confusion, verrous, coupures ; zéros sans
        la zone."""
        noms = ("cfg", "battement", "leve", "ennemi", "proc", "fantome", "evts", "bloques", "confus",
                "confus_pose", "verrous", "coupures")
        a = self.saisie_addr()
        if a is None:
            return dict.fromkeys(noms, 0)
        return dict(zip(noms, struct.unpack(">12I", self.mem.read(a + OFFS_CFG, 48))))

    def aggro_gardes(self) -> dict:
        """Refus par garde d'espèce (bit 4) : {"gardes", "proc", "entree"} ; zéros sans la zone."""
        a = self.aggro2_addr()
        if a is None:
            return {"gardes": 0, "proc": 0, "entree": 0}
        n, last = struct.unpack(">II", self.mem.read(a + OFF2_GARDES_N, 8))
        return {"gardes": n, "proc": last >> 16, "entree": last & 0xFFFF}

    # --- randomizer coop, palier 1 (bit 5) ----------------------------------------------------------------------
    @property
    def rando(self) -> bool:
        """Pack v19 avec le stub N1 du randomizer (bit 5 des capacités). Sans lui : coffres d'origine."""
        return self.pack >= V19 and bool(self.caps & CAP_RANDO)

    def rando_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_rnd`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not self.rando:
            return None
        a = getattr(self, "_rando", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_RANDO, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            raw = self.mem.read(hits[0] + 8, 4)
            if struct.unpack(">I", raw)[0] != RANDO_FORMAT:
                return None
            a = self._rando = hits[0]
        return a

    def rando_lire(self) -> dict:
        """En-tête de la zone : état, empreinte, coffres créés / changés, dernier changement. Lève MailboxError sans
        la zone."""
        a = self.rando_addr()
        if a is None:
            raise MailboxError("graphic pack sans randomizer (bit 5) : réinstalle la v19 du dépôt")
        raw = self.mem.read(a, 0x20)
        etat, emp, rech, chg, dernier = struct.unpack(">5I", raw[OFFR_ETAT:0x20])
        return {"etat": etat, "emp": emp, "emp_hex": f"{emp:08x}", "recherches": rech, "remplacements": chg,
                "dernier": dernier}

    def rando_table(self) -> bytes:
        a = self.rando_addr()
        if a is None:
            raise MailboxError("graphic pack sans randomizer (bit 5)")
        return bytes(self.mem.read(a + OFFR_TABLE, RANDO_TABLE))

    def rando_permis(self) -> bytes:
        a = self.rando_addr()
        if a is None:
            raise MailboxError("graphic pack sans randomizer (bit 5)")
        return bytes(self.mem.read(a + OFFR_PERMIS, 0x100))

    def rando_vider(self) -> None:
        """État 0 : le stub ne fait plus rien (les coffres créés ensuite gardent leur objet d'origine)."""
        a = self.rando_addr()
        if a is not None:
            self.mem.w_u32(a + OFFR_ETAT, 0)

    @property
    def rando2(self) -> bool:
        """Pack v19 dont le stub N1 connaît l'état 2 (randomizer complet, bit 6 des capacités). Sans lui, l'état 2
        ne fait rien (coffres d'origine)."""
        return self.rando and bool(self.caps & CAP_RANDO2)

    def rando2_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_rnd2`` (magie cherchée une fois, juste après ``wwmp_rnd``)."""
        if not self.rando2:
            return None
        a = getattr(self, "_rando2", None)
        if a is None:
            base = self.rando_addr()
            if base is None:
                return None
            # même recherche que wwmp_rnd (tout le pack), puis position exigée : juste après la table du palier 1
            hits = [h for h in self.mem.find_bytes(MAGIC_RANDO2, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1 or hits[0] != base + OFFR_TABLE + RANDO_TABLE:
                return None
            if struct.unpack(">I", self.mem.read(hits[0] + 8, 4))[0] != RANDO2_FORMAT:
                return None
            a = self._rando2 = hits[0]
        return a

    def rando2_permis(self) -> bytes | None:
        """Liste d'objets du randomizer complet telle que le pack installé la porte (256 octets), ou None."""
        a = self.rando2_addr()
        return None if a is None else bytes(self.mem.read(a + OFFR2_PERMIS, 0x100))

    @property
    def rando3(self) -> bool:
        """Pack v19 avec le stub N2 (cadeaux de personnages du randomizer complet, bit 7 des capacités)."""
        return self.rando2 and bool(self.caps & CAP_RANDO3)

    def rando3_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_rnd3`` (magie cherchée une fois ; exigée APRÈS la zone wwmp_rnd2)."""
        if not self.rando3:
            return None
        a = getattr(self, "_rando3", None)
        if a is None:
            base = self.rando2_addr()
            if base is None:
                return None
            hits = [h for h in self.mem.find_bytes(MAGIC_RANDO3, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1 or hits[0] < base + OFFR2_PERMIS + 0x100:
                return None
            if struct.unpack(">I", self.mem.read(hits[0] + 8, 4))[0] != RANDO3_FORMAT:
                return None
            a = self._rando3 = hits[0]
        return a

    def rando3_lire(self) -> dict | None:
        """En-tête et table de la zone N2 : état, nombre, empreinte, cadeaux vus / changés, dernier changement,
        octets des entrées annoncées. None sans la zone."""
        a = self.rando3_addr()
        if a is None:
            return None
        etat, n, emp, vus, chg, lr, dernier = struct.unpack(">7I", self.mem.read(a + OFFR3_ETAT, 28))
        return {"etat": etat, "n": n, "emp": emp, "vus": vus, "remplacements": chg, "dernier_lr": lr,
                "dernier": dernier, "table": bytes(self.mem.read(a + OFFR3_TABLE, RANDO3_TABLE))}

    def rando3_vider(self) -> None:
        """État 0 de la zone N2 : le stub N2 ne fait plus rien (cadeaux d'origine)."""
        a = self.rando3_addr()
        if a is not None:
            self.mem.w_u32(a + OFFR3_ETAT, 0)

    def _rando3_ecrire(self, entrees: bytes, emp: int) -> bool:
        """Table N2 : état 0, entrées (le reste de la table à zéro), nombre, empreinte, relus, puis état 1. Sans
        entrée : état 0 seulement. Appelée par ``rando_charger`` pendant que l'état de wwmp_rnd est à 0."""
        a = self.rando3_addr()
        if a is None:
            return not entrees
        if not entrees:
            if self.mem.u32(a + OFFR3_ETAT) != 0:
                self.mem.w_u32(a + OFFR3_ETAT, 0)
            return True
        self.mem.w_u32(a + OFFR3_ETAT, 0)
        plein = bytes(entrees) + bytes(RANDO3_TABLE - len(entrees))
        n = len(entrees) // 8
        self.mem.write(a + OFFR3_TABLE, plein)
        self.mem.w_u32(a + OFFR3_N, n)
        self.mem.w_u32(a + OFFR3_EMP, emp & 0xFFFFFFFF)
        if bytes(self.mem.read(a + OFFR3_TABLE, RANDO3_TABLE)) != plein or self.mem.u32(a + OFFR3_N) != n:
            return False
        self.mem.w_u32(a + OFFR3_ETAT, 1)
        return True

    def trk_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_trk`` (journal des dons du trackeur ; bit 18 des capacités ; magie cherchée une
        fois, unique, alignée, dans le pack, format attendu). None : pack sans le bit 18 ou zone douteuse."""
        if not self.caps & CAP_TRK:
            return None
        a = getattr(self, "_trk", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_TRK, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", self.mem.read(hits[0] + 8, 4))[0] != TRK_FORMAT:
                return None
            a = self._trk = hits[0]
        return a

    def trk_lire(self) -> tuple[int, list] | None:
        """Journal des dons, LU en une fois : (compteur, [(retour, objet d'origine, r11, numéro, objet final)] × 8).
        Lecture seule. None sans la zone."""
        a = self.trk_addr()
        if a is None:
            return None
        brut = bytes(self.mem.read(a + OFFT_COMPTEUR, 4 + TRK_N * TRK_ENTREE))
        if len(brut) != 4 + TRK_N * TRK_ENTREE:
            return None
        n = struct.unpack_from(">I", brut, 0)[0]
        return n, [struct.unpack_from(">5I", brut, 4 + TRK_ENTREE * k) for k in range(TRK_N)]

    @property
    def hcof(self) -> bool:
        """Coffres fermés à un Hanteur (bit 19 des capacités) et zone ``wwmp_hcof`` trouvée, au bon format."""
        return self.hcof_addr() is not None

    def hcof_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_hcof`` (magie cherchée une fois, unique, alignée, dans le pack, format attendu).
        None : pack sans le bit 19 ou zone douteuse (rien n'est alors écrit)."""
        if not (self.pack >= V19 and self.caps & CAP_HCOF):
            return None
        a = getattr(self, "_hcof", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_HCOF, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != HCOF_FORMAT:
                return None
            a = self._hcof = hits[0]
        return a

    def set_hcof(self, on: bool, images: int = HCOF_IMAGES) -> bool:
        """Drapeau « Hanteur » : posé (échéance = images de la boîte + ``images``, PUIS cfg) ou retiré (cfg à 0
        d'abord, puis échéance à 0). Données seules ; False sans la zone (pack plus ancien : rien n'est écrit)."""
        a = self.hcof_addr()
        if a is None:
            return False
        if on:
            n = max(1, min(int(images), HCOF_IMAGES_MAX))
            self.mem.w_u32(a + OFFH_ECHEANCE, (self.frames() + n) & 0xFFFFFFFF)
            self.mem.w_u32(a + OFFH_CFG, HCOF_CFG_ON)
        else:
            self.mem.w_u32(a + OFFH_CFG, 0)
            self.mem.w_u32(a + OFFH_ECHEANCE, 0)
        return True

    def hcof_counters(self) -> dict:
        """Mise au point : {"cfg", "echeance", "refus"} ; zéros sans la zone."""
        a = self.hcof_addr()
        if a is None:
            return {"cfg": 0, "echeance": 0, "refus": 0}
        cfg, e, r = struct.unpack(">3I", bytes(self.mem.read(a + OFFH_CFG, 12)))
        return {"cfg": cfg, "echeance": e, "refus": r}

    @property
    def canon(self) -> bool:
        """Pack v19 avec CALL 13 CANON (bit 24 des capacités : canon détruit par le jeu lui-même)."""
        return self.pack >= V19 and bool(self.caps & CAP_CANON)

    def canon_detruire(self, proc_id: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 13 : le canon ``proc_id`` (Ocanon, proc 63) passe en mode 2 par son propre setMode, sur le fil du jeu :
        switch, particules de destruction et son, comme quand il est détruit chez nous. Résultat : 1 détruit, 0 déjà
        détruit (rien fait), -1 pas un canon ou mode inconnu, -2 pas de sauvegarde, -3 événement en cours, -4 acteur
        introuvable, -5 particules indisponibles (rien fait) ; MailboxCancelled sans réponse (rien n'est fait).
        MailboxError sans la capacité, ValueError hors bornes (rien n'est envoyé)."""
        if not self.canon:
            raise MailboxError(f"graphic pack v{self.pack} sans la destruction des canons (CALL 13, bit 24) : "
                               "réinstalle la v19 du dépôt (tools/install_graphicpack.py), puis relance le jeu")
        if isinstance(proc_id, bool) or not isinstance(proc_id, int) or not 0 < proc_id < 0xFFFFFFFF:
            raise ValueError("canon : id de process hors bornes")
        return self._v19(CMD_CALL, CALL_CANON, proc_id, timeout=timeout)

    @property
    def coup_monstre_ok(self) -> bool:
        """Pack v19 avec le coup injecté (bit 25) et sa zone trouvée."""
        return self.cpe_addr() is not None

    def cpe_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_cpe`` (magie cherchée une fois, unique, alignée). None sans le bit 25."""
        if not (self.pack >= V19 and self.caps & CAP_COUP_MONSTRE):
            return None
        a = getattr(self, "_cpe", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_CPE, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != CPE_FORMAT:
                return None
            a = self._cpe = hits[0]
        return a

    def coup_monstre(self, ennemi_pid: int, fantome_pid: int, at_type: int, atp: int, spl: int = 0, se: int = 0,
                     mark: int = 0, gat: int = 0, mtrl: int = 0, cut=None, vec=(0.0, 0.0, 0.0), rel=(0.0, 0.0, 0.0),
                     rayon: float = 40.0, hauteur: float = 120.0, timeout: float = TIMEOUT_V19, arme: int = 0,
                     src=None) -> int:
        """CALL 14 (bit 25) : coup d'un joueur distant injecté sur l'ennemi ``ennemi_pid`` chez nous (l'autorité), avec
        la copie du joueur du fantôme ``fantome_pid`` (WwmpLink de l'attaquant) pour propriétaire. ``cut`` : (mCutType,
        mCutCount) de l'attaquant ou None ; ``rel`` : base du cylindre relative à la position de l'ennemi (lue par le
        jeu à l'image de l'injection). ``arme`` (1 à 5, ``ARMES_PRJ`` ; bit 26, mod natif seulement) : coup d'un
        projectile, un faux acteur typé pour propriétaire, placé à l'ennemi + ``src`` (|c| <= 400) ; sans le bit 26 :
        MailboxError (un pack Cemu le jouerait comme un coup d'épée). Résultat : 1 inscrit, -1 refusé, -2 Link absent,
        -3 événement en cours, -4 ennemi ou fantôme introuvable, -5 trop tôt (à réessayer) ; MailboxCancelled sans
        réponse (rien n'est fait). MailboxError sans la capacité, ValueError hors bornes (rien n'est envoyé)."""
        a = self.cpe_addr()
        if a is None:
            raise MailboxError(f"graphic pack v{self.pack} sans le coup injecté (CALL 14, bit 25) : réinstalle la v19 "
                               "du dépôt (tools/install_graphicpack.py), puis relance le jeu")
        if isinstance(arme, bool) or not isinstance(arme, int) or not 0 <= arme <= max(ARMES_PRJ.values()):
            raise ValueError("coup_monstre : arme hors bornes")
        zp = None
        if arme:
            zp = self.prj_addr()
            if zp is None:
                raise MailboxError("coup d'un projectile (bit 26) : seulement le mod natif du portage à jour")
            src = [float(v) for v in (src if src is not None else (0.0, 0.0, 0.0))]
            if len(src) != 3 or not all(abs(v) <= PRJ_SRC_MAX for v in src):     # NaN : comparaison fausse, refusé
                raise ValueError("coup_monstre : source hors bornes")
        vals = [ennemi_pid, fantome_pid, at_type, atp, spl, se, mark, gat, mtrl]
        if any(isinstance(v, bool) or not isinstance(v, int) for v in vals):
            raise ValueError("coup_monstre : entiers attendus")
        vec, rel = [float(v) for v in vec], [float(v) for v in rel]
        rayon, hauteur = float(rayon), float(hauteur)
        coupe = None
        if cut is not None:
            coupe = (int(cut[0]), int(cut[1]))
        if not (0 < ennemi_pid < 0xFFFFFFFF and 0 < fantome_pid < 0xFFFFFFFF and 0 < at_type <= 0xFFFFFFFF
                and 0 <= atp <= COUP_ATP_MAX and 0 <= spl <= COUP_SPL_MAX and 0 <= se <= 0xFF and 0 <= mark <= 0xFF
                and 0 <= mtrl <= 0xFF and 0 <= gat <= 0xFFFFFFFF and len(vec) == 3 and len(rel) == 3
                and all(abs(v) < 1e6 for v in vec) and all(abs(v) <= CPE_REL_MAX for v in rel)
                and CPE_RAYON[0] <= rayon <= CPE_RAYON[1] and CPE_HAUTEUR[0] <= hauteur <= CPE_HAUTEUR[1]
                and (coupe is None or all(0 <= v <= 0xFF for v in coupe))):
            raise ValueError("coup_monstre : arguments hors bornes")
        ct, cc, drap = (coupe[0], coupe[1], 1) if coupe is not None else (0, 0, 0)
        drap |= arme << 4
        buf = struct.pack(">IIIBBBBIBBBB3f3fff", ennemi_pid, fantome_pid, at_type, atp, spl, se, mark, gat, mtrl, ct,
                          cc, drap, *vec, *rel, rayon, hauteur)
        with Mailbox._lock:
            self._check_idle()
            if zp is not None:
                self.mem.write(zp + OFFJ_SRC, struct.pack(">3f", *src))
            self.mem.write(a + OFFC_PRM, buf)
            return self._call(CMD_CALL, CALL_COUP_MONSTRE, timeout=timeout, cancel=True)

    # --- coups des projectiles (bit 26, mod natif seulement) -------------------------------------------------
    @property
    def coups_proj_ok(self) -> bool:
        """Mod natif avec les coups des projectiles (bit 26) et sa zone ``wwmp_prj`` trouvée (jamais un pack Cemu)."""
        return self.prj_addr() is not None

    def prj_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_prj`` (magie cherchée une fois, unique, alignée, format attendu). None sans le bit
        26 ou sans le coup injecté (bit 25, dont il se sert)."""
        if not (self.pack >= V19 and self.caps & CAP_COUPS_PROJ and self.caps & CAP_COUP_MONSTRE):
            return None
        a = getattr(self, "_prj", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_PRJ, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != PRJ_FORMAT:
                return None
            a = self._prj = hits[0]
        return a

    def prj_regler(self, cfg: int, masque: int) -> None:
        """Chez l'attaquant : bit 0 de ``cfg`` = TG des marionnettes inscrits, bit 1 = son du coup joué tout de suite ;
        ``masque`` : types d'attaque acceptés (le mod retire toujours l'épée et le marteau). Sans la zone : rien."""
        a = self.prj_addr()
        if a is None:
            return
        if isinstance(cfg, bool) or not isinstance(cfg, int) or not 0 <= cfg <= 3 \
                or isinstance(masque, bool) or not isinstance(masque, int) or not 0 <= masque <= 0xFFFFFFFF:
            raise ValueError("prj_regler : hors bornes")
        self.mem.write(a + OFFJ_MASQUE, struct.pack(">I", masque))
        self.mem.write(a + OFFJ_CFG, struct.pack(">I", cfg))

    def prj_tg(self, i: int, rayon: float, hauteur: float, dy: float = 0.0) -> None:
        """Cylindre du TG de la marionnette de l'emplacement ``i`` (0 à 15). Sans la zone : rien."""
        a = self.prj_addr()
        if a is None:
            return
        rayon, hauteur, dy = float(rayon), float(hauteur), float(dy)
        if not (0 <= i < 16 and PRJ_RAYON[0] <= rayon <= PRJ_RAYON[1] and PRJ_HAUTEUR[0] <= hauteur <= PRJ_HAUTEUR[1]
                and abs(dy) <= PRJ_DY_MAX):
            raise ValueError("prj_tg : hors bornes")
        self.mem.write(a + OFFJ_TG + 0x10 * i, struct.pack(">3f", rayon, hauteur, dy))

    def prj_seq(self) -> int:
        a = self.prj_addr()
        return 0 if a is None else struct.unpack(">I", bytes(self.mem.read(a + OFFJ_SEQ, 4)))[0]

    def prj_file(self, depuis: int) -> tuple[int, list[dict], int]:
        """Coups portés poussés depuis le compteur ``depuis`` : (nouveau compteur, entrées dans l'ordre, perdus). Une
        entrée n'est rendue que si son numéro est le bon (pas réécrite pendant la lecture) ; au plus 8."""
        a = self.prj_addr()
        if a is None:
            return depuis, [], 0
        seq = self.prj_seq()
        n = (seq - depuis) & 0xFFFFFFFF
        if n == 0 or n > 0x7FFFFFFF:
            return seq, [], 0
        k = min(n, PRJ_N_FILE)
        perdus = n - k
        out = []
        for j in range(k):
            s = (seq - k + 1 + j) & 0xFFFFFFFF
            e = bytes(self.mem.read(a + OFFJ_FILE + (s & (PRJ_N_FILE - 1)) * PRJ_ENTREE, PRJ_ENTREE))
            d = lire_entree_prj(e)
            if d["seq"] != s:
                perdus += 1
                continue
            out.append(d)
        return seq, out, perdus

    def prj_counters(self) -> dict:
        """Mise au point : {"seq", "inscriptions", "ignores", "typees", "sons"} ; zéros sans la zone."""
        a = self.prj_addr()
        if a is None:
            return {"seq": 0, "inscriptions": 0, "ignores": 0, "typees": 0, "sons": 0}
        s, i, g = struct.unpack(">3I", bytes(self.mem.read(a + OFFJ_SEQ, 12)))
        t, so = struct.unpack(">2I", bytes(self.mem.read(a + OFFJ_TYPEES, 8)))
        return {"seq": s, "inscriptions": i, "ignores": g, "typees": t, "sons": so}

    # --- joueur robot (bit 27, mod natif seulement) ---------------------------------------------------------
    @property
    def robot_ok(self) -> bool:
        """Mod natif avec le joueur robot (bit 27) et sa zone ``wwmp_rob`` trouvée (jamais un pack Cemu)."""
        return self.rob_addr() is not None

    def rob_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_rob`` (magie cherchée une fois, unique, alignée, format attendu)."""
        if not (self.pack >= V19 and self.caps & CAP_ROBOT):
            return None
        a = getattr(self, "_rob", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_ROB, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != ROB_FORMAT:
                return None
            a = self._rob = hits[0]
        return a

    def _rob_ou_erreur(self) -> int:
        a = self.rob_addr()
        if a is None:
            raise MailboxError("joueur robot (bit 27) : seulement le mod natif du portage à jour (0.12.0 ou plus)")
        return a

    def robot_mode(self, on: bool) -> None:
        """Mode robot : Link caché (NO_DRAW) et ses pertes de vie annulées à chaque pas, tant qu'il est allumé."""
        self.mem.write(self._rob_ou_erreur() + OFFR_CFG, struct.pack(">I", ROB_CFG_MODE if on else 0))

    def robot_etat(self, timeout: float = TIMEOUT_V19) -> int:
        """CALL 15, ÉTAT : bits ROB_ETAT_* (Link présent, libre, changement d'étape en cours, mode, événement)."""
        self._rob_ou_erreur()
        return self._v19(CMD_CALL, CALL_ROBOT, 0, timeout=timeout) & 0xFFFFFFFF

    @staticmethod
    def _pos_robot(pos) -> list[float]:
        p = [float(v) for v in pos]
        if len(p) != 3 or not all(abs(v) <= ROB_POS_MAX for v in p):          # NaN : comparaison fausse, refusé
            raise ValueError("robot : position hors bornes")
        return p

    def robot_aller(self, etape: str, salle: int, point: int = 0, pos=None, angle: int = 0,
                    timeout: float = TIMEOUT_V19) -> int:
        """CALL 15, ALLER : changement d'étape écrit par le jeu (comme le warp du portage) vers ``etape`` (1 à 7
        caractères), ``salle`` (0..63), par le point d'entrée ``point`` ou, avec ``pos`` (et ``angle``), à cette
        position. Résultat : 1 écrit (l'étape change au pas suivant), -1 refusé, -2 pas de partie, -3 événement,
        -7 changement déjà en cours, -8 Link absent ou pas contrôlé, -9 message ou menu ; ValueError hors bornes."""
        a = self._rob_ou_erreur()
        nom = etape.encode("ascii") if isinstance(etape, str) else b""
        if not (1 <= len(nom) <= 7 and all(0x21 <= c <= 0x7E and c not in b":/" for c in nom)):
            raise ValueError("robot_aller : nom d'étape invalide")
        if isinstance(salle, bool) or not isinstance(salle, int) or not 0 <= salle <= 63:
            raise ValueError("robot_aller : salle hors bornes")
        if isinstance(point, bool) or not isinstance(point, int) or not 0 <= point <= 0x7FFF:
            raise ValueError("robot_aller : point hors bornes")
        if isinstance(angle, bool) or not isinstance(angle, int) or not -0x8000 <= angle <= 0x7FFF:
            raise ValueError("robot_aller : angle hors bornes")
        p = self._pos_robot(pos) if pos is not None else [0.0, 0.0, 0.0]
        buf = (nom.ljust(8, b"\0") + struct.pack(">ii3fiI", salle, point, *p, angle,
                                                  ROB_A_POSITION if pos is not None else 0))
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(a + OFFR_ETAPE, buf)
            return self._call(CMD_CALL, CALL_ROBOT, 1, timeout=timeout, cancel=True)

    def robot_placer(self, pos, timeout: float = TIMEOUT_V19) -> int:
        """CALL 15, PLACER : le Link caché est posé à ``pos`` dans sa salle (vitesses à 0). Mêmes résultats."""
        a = self._rob_ou_erreur()
        p = self._pos_robot(pos)
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(a + OFFR_POS, struct.pack(">3f", *p))
            return self._call(CMD_CALL, CALL_ROBOT, 2, timeout=timeout, cancel=True)

    def robot_counters(self) -> dict:
        """Mise au point : {"cfg", "images", "allers", "refus", "annules", "places", "cache"} ; zéros sans la zone."""
        a = self.rob_addr()
        if a is None:
            return {k: 0 for k in ("cfg", "images", "allers", "refus", "annules", "places", "cache")}
        cfg, = struct.unpack(">I", bytes(self.mem.read(a + OFFR_CFG, 4)))
        im, al, re, an, pl, ca = struct.unpack(">6I", bytes(self.mem.read(a + OFFR_IMAGES, 24)))
        return {"cfg": cfg, "images": im, "allers": al, "refus": re, "annules": an, "places": pl, "cache": ca}

    def cpe_counters(self) -> dict:
        """Mise au point : {"injections", "lus", "hitstops", "image", "sans_fantome"} ; zéros sans la zone."""
        a = self.cpe_addr()
        if a is None:
            return {"injections": 0, "lus": 0, "hitstops": 0, "image": 0, "sans_fantome": 0}
        i, l, h, im, s = struct.unpack(">5I", bytes(self.mem.read(a + OFFC_INJ, 20)))
        return {"injections": i, "lus": l, "hitstops": h, "image": im, "sans_fantome": s}

    @property
    def cache(self) -> bool:
        """Cache-cache à plusieurs (bit 23 des capacités) et zone ``wwmp_cache`` trouvée, au bon format."""
        return self.cache_addr() is not None

    def cache_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_cache`` (magie cherchée une fois, unique, alignée, dans le pack, format attendu).
        None : pack sans le bit 23 ou zone douteuse (rien n'est alors écrit)."""
        if not (self.pack >= V19 and self.caps & CAP_CACHE):
            return None
        a = getattr(self, "_cache", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_CACHE, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != CACHE_FORMAT:
                return None
            a = self._cache = hits[0]
        return a

    def set_cache(self, on: bool) -> bool:
        """Drapeau « fin bloquée » (joueur qui aide la partie d'un autre) : cfg = 1 ou 0. Données seules ; False sans
        la zone (pack plus ancien : rien n'est écrit)."""
        a = self.cache_addr()
        if a is None:
            return False
        self.mem.w_u32(a + OFFK_CFG, CACHE_CFG_ON if on else 0)
        return True

    def cache_counters(self) -> dict:
        """{"cfg", "refus", "appels"} ; zéros sans la zone."""
        a = self.cache_addr()
        if a is None:
            return {"cfg": 0, "refus": 0, "appels": 0}
        cfg, r, n = struct.unpack(">3I", bytes(self.mem.read(a + OFFK_CFG, 12)))
        return {"cfg": cfg, "refus": r, "appels": n}

    # --- les autres joueurs sur la carte du jeu (bit 30, mod natif seulement) ------------------------------------------
    def carte_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_car`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans le bit."""
        if not (self.pack >= V19 and self.caps & CAP_CARTE):
            return None
        a = getattr(self, "_carte", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_CARTE, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != CARTE_FORMAT:
                return None
            a = self._carte = hits[0]
        return a

    # --- vie partagée (0.30.0, mod natif seulement, sans bit : la zone est la capacité) ---------------------------
    def vie_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_vie`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_vie", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_VIE, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != VIE_FORMAT:
                return None
            a = self._vie = hits[0]
        return a

    @property
    def vie_ok(self) -> bool:
        return self.vie_addr() is not None

    def vie_ajouter(self, delta: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 18 VIE : ``delta`` quarts (-80..80, non nul) ajoutés à la vie de Link par le jeu. Renvoie la vie après
        (0..80) ou un code de refus (< 0 : rien n'a été écrit). ValueError avant tout envoi hors bornes ;
        MailboxError sans la zone ; MailboxCancelled sans réponse (requête neutralisée : rien n'a été fait)."""
        if self.vie_addr() is None:
            raise MailboxError("vie partagée : seulement le mod natif du portage à jour (0.30.0 ou plus)")
        if isinstance(delta, bool) or not isinstance(delta, int) or not -80 <= delta <= 80 or delta == 0:
            raise ValueError(f"VIE : delta {delta!r} hors -80..80 (non nul)")
        return self._call(CMD_CALL, CALL_VIE, delta, timeout=timeout, cancel=True)

    def vie_counters(self) -> dict:
        a = self.vie_addr()
        if a is None:
            return {"appels": 0, "faits": 0, "refus": 0, "dernier": 0}
        n, f, r, d = struct.unpack(">3Ii", bytes(self.mem.read(a + OFFV_APPELS, 16)))
        return {"appels": n, "faits": f, "refus": r, "dernier": d}

    # --- capacités des monstres possédés (0.34.0, mod natif seulement, sans bit : la zone est la capacité) ---------
    def capacites_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_cap`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_cap", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_CAP, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != CAPA_FORMAT:
                return None
            a = self._cap = hits[0]
        return a

    @property
    def capacites_ok(self) -> bool:
        return self.capacites_addr() is not None

    def capacites_regler(self, coupees: int = 0, garde: int = 0) -> bool:
        """Réglages lus par le jeu à chaque CALL 20 : ``coupees`` (bit k : capacité k refusée) et ``garde`` (images de
        la garde du Darknut, bornées 15..120 ici comme par le mod ; 0 : la valeur du mod). Rien n'est écrit si les
        valeurs y sont déjà ; False sans la zone."""
        a = self.capacites_addr()
        if a is None:
            return False
        c = int(coupees) & ((1 << CAPA_N) - 1)
        g = int(garde)
        g = 0 if g <= 0 else max(CAPA_GARDE_MIN, min(CAPA_GARDE_MAX, g))
        if struct.unpack(">2I", bytes(self.mem.read(a + OFFA_COUPEES, 8))) != (c, g):
            self.mem.write(a + OFFA_COUPEES, struct.pack(">2I", c, g))
        return True

    def capacite(self, proc_id: int, index: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 20 CAPACITE : joue la capacité ``index`` (synchro/capacites.py INDEX) du monstre ``proc_id``, sur le fil du
        jeu, s'il est « libre ». Résultat : 1 fait, 0 pas libre, -1 index ou espèce, -2 coupée, -3 événement,
        -4 introuvable, -8 Link absent. MailboxError sans la zone, ValueError hors bornes (rien n'est envoyé) ;
        MailboxCancelled sans réponse (requête neutralisée : rien n'a été fait)."""
        if self.capacites_addr() is None:
            raise MailboxError("capacités des monstres : seulement le mod natif du portage à jour (0.34.0 ou plus)")
        if isinstance(index, bool) or isinstance(proc_id, bool):
            raise ValueError("capacité : arguments hors bornes")
        proc_id, index = int(proc_id), int(index)
        if not (0 < proc_id < 0xFFFFFFFF and 0 <= index < CAPA_N):
            raise ValueError("capacité : arguments hors bornes")
        return self._call(CMD_CALL, CALL_CAP, proc_id, index, timeout=timeout, cancel=True)

    def capacites_counters(self) -> dict:
        a = self.capacites_addr()
        if a is None:
            return {"nombre": 0, "coupees": 0, "garde": 0, "appels": 0, "faites": 0, "refus": 0, "dernier": 0,
                    "derniere": 0}
        n, c, g, ap, f, r, d, i = struct.unpack(">6IiI", bytes(self.mem.read(a + OFFA_NOMBRE, 32)))
        return {"nombre": n, "coupees": c, "garde": g, "appels": ap, "faites": f, "refus": r, "dernier": d,
                "derniere": i}
    # --- noms au-dessus des têtes et signal « viens ici » (0.33.0, mod natif seulement, sans bit) ----------------
    def sig_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_sig`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_sig", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_SIG, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != SIG_FORMAT:
                return None
            a = self._sig = hits[0]
        return a

    def set_sig_cfg(self, cfg: int) -> bool:
        a = self.sig_addr()
        if a is None:
            return False
        self.mem.w_u32(a + OFFS_CFG, int(cfg) & (SIG_NOMS | SIG_SIGNAUX | SIG_MANETTE))
        return True

    def set_nom(self, slot: int, pid: int, nom: bytes) -> bool:
        """Nom (Latin-1 déjà nettoyé, 15 octets au plus) du fantôme ``pid`` de l'emplacement ``slot`` : id à 0 d'abord,
        puis le nom, puis l'id (le jeu ne montre jamais un nom avec un autre fantôme). ``pid`` 0 : plus de nom."""
        a = self.sig_addr()
        if a is None:
            return False
        if not 0 <= int(slot) < 7:
            raise ValueError(f"nom : emplacement {slot!r}")
        n = bytes(nom or b"")[:SIG_NOM - 1]
        self.mem.w_u32(a + OFFS_IDS + 4 * slot, 0)
        self.mem.write(a + OFFS_NOMS + SIG_NOM * slot, n.ljust(SIG_NOM, b"\0"))
        if pid:
            self.mem.w_u32(a + OFFS_IDS + 4 * slot, int(pid) & 0xFFFFFFFF)
        return True

    def poser_signal(self, k: int, pos, rgba: int, duree: int) -> bool:
        """Signal ``k`` (0..3) : durée à 0 d'abord, position et couleur, puis la durée (pas de Link) EN DERNIER."""
        a = self.sig_addr()
        if a is None:
            return False
        if not 0 <= int(k) < SIG_N:
            raise ValueError(f"signal : entrée {k!r}")
        e = a + OFFS_SIG + SIG_ENTREE * k
        self.mem.w_u32(e, 0)
        self.mem.write(e + 4, struct.pack(">3fI", *[float(v) for v in pos], int(rgba) & 0xFFFFFFFF))
        if duree > 0:
            self.mem.w_u32(e, min(int(duree), 0xFFFF))
        return True

    def lire_demande_signal(self) -> tuple[int, tuple[float, float, float]] | None:
        """(compteur des doubles clics du stick gauche, position de Link au dernier) ; None sans la zone."""
        a = self.sig_addr()
        if a is None:
            return None
        n, x, y, z = struct.unpack(">I3f", bytes(self.mem.read(a + OFFS_DEMANDES, 16)))
        return n, (x, y, z)

    def sig_counters(self) -> dict:
        a = self.sig_addr()
        if a is None:
            return {"cfg": 0, "demandes": 0, "noms_vus": 0, "signaux_vus": 0}
        cfg, n = struct.unpack(">2I", bytes(self.mem.read(a + OFFS_CFG, 8)))
        nv, sv = struct.unpack(">2H", bytes(self.mem.read(a + OFFS_VUS, 4)))
        return {"cfg": cfg, "demandes": n, "noms_vus": nv, "signaux_vus": sv}
    # --- bandeau (0.44.0, mod natif seulement, sans bit : la zone est la capacité) -----------------------------------
    def ban_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_ban`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_ban", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_BAN, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != BAN_FORMAT:
                return None
            a = self._ban = hits[0]
        return a

    def poser_bandeau(self, lignes, duree: int, ancre: int = BAN_HAUT, cfg: int = BAN_ALLUME) -> bool:
        """Bandeau : ``lignes`` = jusqu'à 4 (texte Latin-1 déjà nettoyé, rgba, taille) ; lignes, durée (pas, 0 : efface),
        ancre et cfg, puis le numéro EN DERNIER (le jeu prend le bandeau à son prochain pas). False sans la zone."""
        a = self.ban_addr()
        if a is None:
            return False
        lignes = list(lignes or [])[:BAN_N]
        bloc = b""
        for k in range(BAN_N):
            txt, rgba, taille = (lignes[k] + (0, 0))[:3] if k < len(lignes) else (b"", 0, 0)
            bloc += struct.pack(">II", int(rgba) & 0xFFFFFFFF, max(0, min(int(taille), 255))) \
                + bytes(txt or b"")[:BAN_TEXTE].ljust(BAN_TEXTE, b"\0")
        self.mem.write(a + OFFN_LIGNES, bloc)
        self.mem.write(a + OFFN_CFG, struct.pack(">I", int(cfg) & BAN_ALLUME))
        self.mem.write(a + OFFN_DUREE, struct.pack(">II", max(0, min(int(duree), BAN_DUREE_MAX)),
                                                    int(ancre) if ancre in (BAN_HAUT, BAN_DROITE, BAN_BAS) else 0))
        seq = (struct.unpack(">I", bytes(self.mem.read(a + OFFN_SEQ, 4)))[0] + 1) & 0xFFFFFFFF
        self.mem.w_u32(a + OFFN_SEQ, seq)
        return True

    def ban_counters(self) -> dict:
        a = self.ban_addr()
        if a is None:
            return {"cfg": 0, "seq": 0, "vu": 0, "reste": 0, "montres": 0, "dessins": 0, "attentes": 0}
        cfg, seq, _d, _a, vu, reste, mo, de, at = struct.unpack(">9I", bytes(self.mem.read(a + OFFN_CFG, 36)))
        return {"cfg": cfg, "seq": seq, "vu": vu, "reste": reste, "montres": mo, "dessins": de, "attentes": at}
    # --- chant de ralliement (0.45.0, mod natif seulement, sans bit : la zone est la capacité) ----------------------
    def tac_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_tac`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_tac", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_TAC, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != TAC_FORMAT:
                return None
            a = self._tac = hits[0]
        return a

    def tac_regler(self, on: bool, morceau: int = 6) -> bool:
        """Chant de ralliement : morceau (6 ou 7) puis cfg ; False sans la zone."""
        a = self.tac_addr()
        if a is None:
            return False
        m = morceau if morceau in TAC_MORCEAUX else TAC_MORCEAUX[0]
        self.mem.write(a + OFFL_MORCEAU, struct.pack(">I", m))
        self.mem.write(a + OFFL_CFG, struct.pack(">I", TAC_ALLUME if on else 0))
        return True

    def tac_counters(self) -> dict:
        """{"cfg", "morceau", "demandes", "pos", "etape", "salle", "juges", "ignores", "angle"} ; zéros sans la zone.
        « demandes » est lu D'ABORD (le jeu l'écrit en dernier)."""
        a = self.tac_addr()
        if a is None:
            return {"cfg": 0, "morceau": 0, "demandes": 0, "pos": (0.0, 0.0, 0.0), "etape": "", "salle": -1,
                    "juges": [0] * 8, "ignores": 0, "angle": 0}
        n, = struct.unpack(">I", bytes(self.mem.read(a + OFFL_DEMANDES, 4)))
        cfg, mo = struct.unpack(">2I", bytes(self.mem.read(a + OFFL_CFG, 8)))
        x, y, z = struct.unpack(">3f", bytes(self.mem.read(a + OFFL_POS, 12)))
        brut = bytes(self.mem.read(a + OFFL_ETAPE, 8))
        etape = brut.split(b"\0", 1)[0].decode("latin-1", "replace")
        salle, = struct.unpack(">i", bytes(self.mem.read(a + OFFL_SALLE, 4)))
        juges = list(bytes(self.mem.read(a + OFFL_JUGES, 8)))
        ig, ang = struct.unpack(">Ii", bytes(self.mem.read(a + OFFL_IGNORES, 8)))
        return {"cfg": cfg, "morceau": mo, "demandes": n, "pos": (x, y, z), "etape": etape, "salle": salle,
                "juges": juges, "ignores": ig, "angle": ang}
    # --- relever un coéquipier (0.48.0, mod natif seulement, sans bit : la zone est la capacité) ---------------------
    def rel_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_rel`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_rel", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_REL, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != REL_FORMAT:
                return None
            a = self._rel = hits[0]
        return a

    def rel_regler(self, on: bool, delai: int = 600, possible: int = 0, air_max: int = 45) -> bool:
        """Relever : délai (pas), air_max (pas), « possible » (pas, rechargé par le PC), puis cfg EN DERNIER ; tout est
        borné ici comme par le mod. False sans la zone."""
        a = self.rel_addr()
        if a is None:
            return False
        d = max(REL_DELAI_MIN, min(REL_DELAI_MAX, int(delai)))
        p = max(0, min(REL_POSSIBLE_MAX, int(possible)))
        am = max(0, min(REL_AIR_MAX, int(air_max)))
        self.mem.write(a + OFFRL_DELAI, struct.pack(">Ii", d, p))
        self.mem.write(a + OFFRL_AIR_MAX, bytes([am]))
        self.mem.write(a + OFFRL_CFG, struct.pack(">I", REL_ALLUME if on else 0))
        return True

    def rel_possible(self, pas: int) -> bool:
        """« Relève possible » rechargé pour ``pas`` pas (0 : plus possible, la prochaine chute est une vraie mort)."""
        a = self.rel_addr()
        if a is None:
            return False
        self.mem.write(a + OFFRL_POSSIBLE, struct.pack(">i", max(0, min(REL_POSSIBLE_MAX, int(pas)))))
        return True

    def rel_noms(self, terre: dict) -> bool:
        """« À TERRE » au-dessus des fantômes : {emplacement 0..6: secondes restantes (0 : sans nombre)}."""
        a = self.rel_addr()
        if a is None:
            return False
        bits, sec = 0, bytearray(8)
        for k, v in (terre or {}).items():
            if isinstance(k, int) and 0 <= k <= 6:
                bits |= 1 << k
                sec[k] = max(0, min(99, int(v or 0)))
        self.mem.write(a + OFFRL_SECONDES, bytes(sec))
        self.mem.write(a + OFFRL_TERRE, bytes([bits]))
        return True

    def rel_counters(self) -> dict:
        """Zone du relevé : réglages, état, compteurs ; zéros sans la zone. « chutes » est lu D'ABORD."""
        a = self.rel_addr()
        if a is None:
            return {"cfg": 0, "delai": 0, "possible": 0, "etat": 0, "reste": 0, "chutes": 0, "releves": 0,
                    "expires": 0, "abandons": 0, "normales": 0, "raison": 0, "appels": 0, "refus": 0, "dernier": 0,
                    "gelees": 0, "air": 0, "pos": (0.0, 0.0, 0.0), "air_max": 0, "autres": 0}
        ch, = struct.unpack(">I", bytes(self.mem.read(a + OFFRL_CHUTES, 4)))
        cfg, de, po, et, re = struct.unpack(">IIiIi", bytes(self.mem.read(a + OFFRL_CFG, 20)))
        rv, ex, ab, no, ra, ap, rf, dr, ge, ai = struct.unpack(">7IiII", bytes(self.mem.read(a + 0x24, 40)))
        x, y, z = struct.unpack(">3f", bytes(self.mem.read(a + OFFRL_POS, 12)))
        am = bytes(self.mem.read(a + OFFRL_AIR_MAX, 1))[0]
        au, = struct.unpack(">I", bytes(self.mem.read(a + OFFRL_AUTRES, 4)))
        return {"cfg": cfg, "delai": de, "possible": po, "etat": et, "reste": re, "chutes": ch, "releves": rv,
                "expires": ex, "abandons": ab, "normales": no, "raison": ra, "appels": ap, "refus": rf, "dernier": dr,
                "gelees": ge, "air": ai, "pos": (x, y, z), "air_max": am, "autres": au}

    def _rel_call(self, sous: int, vie: int = 0, timeout: float = TIMEOUT_V19) -> int:
        if self.rel_addr() is None:
            raise MailboxError("relever un coéquipier : seulement le mod natif du portage à jour (0.48.0 ou plus)")
        return self._call(CMD_CALL, CALL_REL, sous, vie, timeout=timeout, cancel=True)

    def releve_etat(self, timeout: float = TIMEOUT_V19) -> int:
        """CALL 19 ÉTAT : 0 debout, 1 chute, 2 à terre, 3 relevé, 4 mort demandée."""
        return self._rel_call(0, timeout=timeout)

    def relever(self, vie: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 19 RELEVER : ``vie`` quarts (1..80) ; la vie écrite (bornée à la vie max) ou un refus (< 0 : rien écrit ;
        -3 événement, -4 pas à terre, -7 changement d'étape, -8 Link absent, -9 message ou menu, -11 variation de vie en
        attente : à retenter). ValueError avant tout envoi hors bornes."""
        if isinstance(vie, bool) or not isinstance(vie, int) or not 1 <= vie <= 80:
            raise ValueError(f"RELEVER : vie {vie!r} hors 1..80")
        return self._rel_call(1, vie, timeout=timeout)

    def releve_abandon(self, timeout: float = TIMEOUT_V19) -> int:
        """CALL 19 ABANDON : la vraie mort à l'image suivante (1) ; -4 pas à terre."""
        return self._rel_call(2, timeout=timeout)
    # --- camouflage de la tenue jaune (0.49.0, mod natif seulement, sans bit : la zone est la capacité) -----------------
    def cam_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_cam`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_cam", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_CAM, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != CAM_FORMAT:
                return None
            a = self._cam = hits[0]
        return a

    def cam_regler(self, on: bool, transparents: int = 0) -> bool:
        """Règle du camouflage sur ce PC (cfg ; coupée : le camouflage en cours finit) et fantômes transparents (bits
        0..6). False sans la zone."""
        a = self.cam_addr()
        if a is None:
            return False
        t = transparents if isinstance(transparents, int) and not isinstance(transparents, bool) else 0
        self.mem.write(a + OFFCM_TRANSPARENTS, bytes([t & 0x7F]))
        self.mem.write(a + OFFCM_CFG, struct.pack(">I", CAM_ALLUME if on else 0))
        return True

    def cam_armer(self, numero: int, duree: int) -> bool:
        """Camouflage de notre Link : ``duree`` images (bornées comme par le mod), puis ``numero`` EN DERNIER (un numéro
        nouveau l'arme ; cfg doit être allumé AVANT). False sans la zone."""
        a = self.cam_addr()
        if a is None:
            return False
        d = max(CAM_DUREE_MIN, min(CAM_DUREE_MAX, int(duree)))
        self.mem.write(a + OFFCM_DUREE, struct.pack(">I", d))
        self.mem.write(a + OFFCM_NUMERO, struct.pack(">I", int(numero) & 0xFFFFFFFF))
        return True

    def cam_counters(self) -> dict:
        """{"cfg", "duree", "numero", "transparents", "vu", "reste", "etat", compteurs...} ; zéros sans la zone."""
        a = self.cam_addr()
        vide = {"cfg": 0, "duree": 0, "numero": 0, "transparents": 0, "vu": 0, "reste": 0, "etat": 0,
                **{k: 0 for k in CAM_COMPTEURS}}
        if a is None:
            return vide
        b = bytes(self.mem.read(a, 0x58))
        cfg, du, nu = struct.unpack_from(">III", b, OFFCM_CFG)
        vu, re, et = struct.unpack_from(">IiI", b, OFFCM_VU)
        out = {"cfg": cfg, "duree": du, "numero": nu, "transparents": b[OFFCM_TRANSPARENTS], "vu": vu, "reste": re,
               "etat": et}
        for k, nom in enumerate(CAM_COMPTEURS):
            out[nom], = struct.unpack_from(">i" if nom == "dernier_proc" else ">I", b, OFFCM_COMPTEURS + 4 * k)
        return out
    # --- tenues à pouvoir, partie A (0.46.0, mod natif seulement, sans bit : la zone est la capacité) ----------------
    def ten_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_ten`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_ten", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_TEN, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != TEN_FORMAT:
                return None
            a = self._ten = hits[0]
        return a

    def ten_regler(self, cfg: int, couleur: int = 0, duo: int = 0, facteurs: dict | None = None) -> bool:
        """Tenues : facteurs (huitièmes, 0..255 ; absents : 0 = x1), couleur et duo, puis cfg (TEN_*) EN DERNIER ; False
        sans la zone."""
        a = self.ten_addr()
        if a is None:
            return False
        f = facteurs or {}

        def octet(v):
            return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 else 0
        self.mem.write(a + OFFU_FACTEURS, bytes(octet(f.get(k, 0)) for k in TEN_FACTEURS))
        self.mem.write(a + OFFU_COULEUR, bytes([octet(couleur) if 0 <= octet(couleur) <= 8 else 0,
                                               octet(duo) if 0 <= octet(duo) <= 8 else 0]))
        self.mem.write(a + OFFU_CFG, struct.pack(">I", cfg & (TEN_ALLUME | TEN_MAGIE | TEN_MUNITIONS)))
        return True

    def ten_distant(self, epee: int = 0, gadget: int = 0, dos: int = 0, fusion: int = 0, magie: int = 0) -> bool:
        """Avant un CALL 14 : facteurs de l'attaquant distant (huitièmes ; 0 = x1), et (0.47.0) ses facteurs de fusion
        (tous les coups, objets à magie) ; False sans la zone."""
        a = self.ten_addr()
        if a is None:
            return False

        def octet(v):
            return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 else 0
        self.mem.write(a + OFFU_DISTANT, bytes([octet(epee), octet(gadget), octet(dos), octet(fusion)]))
        self.mem.write(a + OFFU_D_MAGIE, bytes([octet(magie)]))
        return True

    def ten_fusion(self, genre: int = 0, role: int = 0, f_fusion: int = 0, f_magie: int = 0) -> bool:
        """0.47.0 : fusion en cours chez nous (genre TEN_FUS_*, 0 : aucune ; rôle 1 déplace, 2 attaque) et ses
        facteurs (huitièmes : tous nos coups, nos objets à magie ; 0 = x1). Facteurs d'abord, genre EN DERNIER (le mod
        ne lit les facteurs que si le genre est non nul). False sans la zone."""
        a = self.ten_addr()
        if a is None:
            return False

        def octet(v):
            return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 else 0
        g = octet(genre) if octet(genre) in (0, TEN_FUS_TOURNOYANTE, TEN_FUS_MEGA_BOMBE) else 0
        if not g:
            self.mem.write(a + OFFU_FUSION, bytes([0, 0]))
        self.mem.write(a + OFFU_F_FUSION, bytes([octet(f_fusion), octet(f_magie)]))
        self.mem.write(a + OFFU_FUSION, bytes([g, octet(role) if octet(role) <= 2 else 0]))
        return True

    # --- fusions des tenues, partie B (0.47.0, mod natif seulement, sans bit : la zone est la capacité) --------------
    def fus_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_fus`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_fus", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_FUS, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != FUS_FORMAT:
                return None
            a = self._fus = hits[0]
        return a

    def fus_regler(self, on: bool, vie: int = 0, genre: int = 0, role: int = 0, caches: int = 0,
                   soi_cache: bool = False, boutons: int = 0, sticks: int = 0, bonnet=None) -> bool:
        """Fusion chez nous : ``vie`` (images, 1..300 ; le jeu la décompte et éteint tout à 0 : à recharger),
        ``caches`` (bits 0..6 : fantômes non dessinés), ``soi_cache`` (notre Link caché), ``boutons`` (bits VPAD
        retirés, bornés à FUS_BOUTONS_PERMIS), ``sticks`` (FUS_STICK_*), ``bonnet`` (None, ou (R, G, B) : facteurs de
        la couleur du bonnet de notre Link) ; cfg EN DERNIER. ``on`` faux : cfg 0, vie 0, tout à zéro. False sans la
        zone."""
        a = self.fus_addr()
        if a is None:
            return False
        if not on:
            self.mem.write(a + OFFF_CFG, struct.pack(">I", 0))
            self.mem.write(a + OFFF_GENRE, bytes(0x0C))
            self.mem.write(a + OFFF_BONNET, bytes(0x14))
            return True

        def entier(v, lo, hi):
            return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else 0
        if bonnet is not None:
            try:
                r, g, b = (float(v) for v in bonnet)
            except (TypeError, ValueError):
                r = g = b = float("nan")
            ok = all(v == v and 0.0 <= v <= 8.0 for v in (r, g, b))
        else:
            ok = False
        bloc = struct.pack(">I3f", FUS_BONNET, r, g, b) if ok else bytes(16)
        self.mem.write(a + OFFF_GENRE, bytes([entier(genre, 0, 2), entier(role, 0, 2), entier(caches, 0, 0x7F),
                                              1 if soi_cache else 0]))
        self.mem.write(a + OFFF_BOUTONS, struct.pack(">II", entier(boutons, 0, 0xFFFFFFFF) & FUS_BOUTONS_PERMIS,
                                                     entier(sticks, 0, 3)))
        self.mem.write(a + OFFF_BONNET, bloc)
        self.mem.write(a + OFFF_VIE, struct.pack(">i", entier(vie, 0, FUS_VIE_MAX)))
        self.mem.write(a + OFFF_CFG, struct.pack(">I", FUS_ALLUME))
        return True

    def fus_bouclier(self, images: int) -> bool:
        """Invincibilité de notre Link pendant ``images`` (1..90) pas, seulement fusion allumée ; False sans zone."""
        a = self.fus_addr()
        if a is None:
            return False
        n = images if isinstance(images, int) and not isinstance(images, bool) and 0 <= images <= FUS_BOUCLIER_MAX \
            else 0
        self.mem.write(a + OFFF_BOUCLIER, struct.pack(">i", n))
        return True

    def fus_counters(self) -> dict:
        """{"cfg", "genre", "role", "caches", "soi_cache", "boutons", "sticks", "bouclier", "vie", compteurs...} ;
        zéros sans la zone."""
        a = self.fus_addr()
        vide = {"cfg": 0, "genre": 0, "role": 0, "caches": 0, "soi_cache": 0, "boutons": 0, "sticks": 0,
                "bouclier": 0, "vie": 0, **{k: 0 for k in FUS_COMPTEURS}}
        if a is None:
            return vide
        b = bytes(self.mem.read(a, 0x64))
        out = {"cfg": struct.unpack_from(">I", b, OFFF_CFG)[0], "genre": b[0x10], "role": b[0x11], "caches": b[0x12],
               "soi_cache": b[0x13]}
        out["boutons"], out["sticks"], out["bouclier"] = struct.unpack_from(">IIi", b, OFFF_BOUTONS)
        out["vie"], = struct.unpack_from(">i", b, OFFF_VIE)
        for k, nom in enumerate(FUS_COMPTEURS):
            out[nom], = struct.unpack_from(">I", b, OFFF_COMPTEURS + 4 * k)
        return out

    # --- VRAIE fusion (0.50.0, mod natif seulement, sans bit : la zone est la capacité) --------------------------------
    def fen_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_fen`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_fen", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_FEN, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != FEN_FORMAT:
                return None
            a = self._fen = hits[0]
        return a

    def fen_regler(self, cfg: int, peremption: int = FEN_PEREMPTION_DEF) -> bool:
        """Rôle dans la vraie fusion : FEN_PILOTE, FEN_ATTAQUANT ou 0 (rien) ; ``peremption`` en lectures (hors bornes :
        FEN_PEREMPTION_DEF ; le mod borne aussi). cfg EN DERNIER ; à 0, l'état distant est aussi effacé (boutons et
        sticks, pas le numéro). False sans la zone."""
        a = self.fen_addr()
        if a is None:
            return False
        c = cfg if cfg in (0, FEN_PILOTE, FEN_ATTAQUANT) else 0
        p = peremption if isinstance(peremption, int) and not isinstance(peremption, bool) and \
            FEN_PEREMPTION_MIN <= peremption <= FEN_PEREMPTION_MAX else FEN_PEREMPTION_DEF
        if not c:
            self.mem.write(a + OFFFN_CFG, struct.pack(">I", 0))
            self.mem.write(a + OFFFN_D, bytes(0x14))
            return True
        self.mem.write(a + OFFFN_PEREMPTION, struct.pack(">I", p))
        self.mem.write(a + OFFFN_CFG, struct.pack(">I", c))
        return True

    def fen_ecrire(self, boutons: int, lx: float, ly: float, rx: float, ry: float, seq: int) -> bool:
        """PILOTE : état distant de l'attaquant (boutons bornés à FEN_BOUTONS, sticks finis bornés à ±1) et son numéro
        EN DERNIER, en UNE écriture (le numéro est le dernier mot du bloc). False sans la zone."""
        a = self.fen_addr()
        if a is None:
            return False

        def st(v):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return 0.0
            return max(-1.0, min(1.0, f)) if f == f and abs(f) != float("inf") else 0.0
        b = boutons & FEN_BOUTONS if isinstance(boutons, int) and not isinstance(boutons, bool) else 0
        self.mem.write(a + OFFFN_D, struct.pack(">I4fI", b, st(lx), st(ly), st(rx), st(ry), int(seq) & 0xFFFFFFFF))
        return True

    def fen_lire_soi(self) -> dict | None:
        """ATTAQUANT : ce que le joueur presse à la dernière lecture du jeu {seq, boutons, lx, ly, rx, ry, libre} (le
        bloc en une lecture, le numéro relu après : refaite une fois s'il a bougé entre les deux) ; None sans la zone."""
        a = self.fen_addr()
        if a is None:
            return None
        for _essai in range(2):
            b = bytes(self.mem.read(a + OFFFN_M, 0x1C))
            bo, lx, ly, rx, ry, libre, seq = struct.unpack(">I4fII", b)
            seq2, = struct.unpack(">I", bytes(self.mem.read(a + OFFFN_M_SEQ, 4)))
            if seq2 == seq:
                break
        return {"seq": seq, "boutons": bo, "lx": lx, "ly": ly, "rx": rx, "ry": ry, "libre": libre}

    def fen_counters(self) -> dict:
        """{"cfg", "peremption", "d_boutons", "d_seq", "m_seq", compteurs du jeu...} ; zéros sans la zone."""
        a = self.fen_addr()
        vide = {"cfg": 0, "peremption": 0, "d_boutons": 0, "d_seq": 0, "m_seq": 0, **{k: 0 for k in FEN_COMPTEURS}}
        if a is None:
            return vide
        b = bytes(self.mem.read(a, 0x6C))
        out = {"cfg": struct.unpack_from(">I", b, OFFFN_CFG)[0],
               "peremption": struct.unpack_from(">I", b, OFFFN_PEREMPTION)[0],
               "d_boutons": struct.unpack_from(">I", b, OFFFN_D)[0], "d_seq": struct.unpack_from(">I", b, OFFFN_D_SEQ)[0],
               "m_seq": struct.unpack_from(">I", b, OFFFN_M_SEQ)[0]}
        for k, nom in enumerate(FEN_COMPTEURS):
            out[nom], = struct.unpack_from(">I", b, OFFFN_COMPTEURS + 4 * k)
        return out

    def ten_counters(self) -> dict:
        """{"cfg", "couleur", "duo", facteurs..., compteurs du jeu...} ; zéros sans la zone."""
        a = self.ten_addr()
        vide = {"cfg": 0, "couleur": 0, "duo": 0, **{k: 0 for k in TEN_FACTEURS}, **{k: 0 for k in TEN_COMPTEURS}}
        if a is None:
            return vide
        brut = bytes(self.mem.read(a + OFFU_CFG, 0x40 - OFFU_CFG))
        cfg, = struct.unpack_from(">I", brut, 0)
        out = {"cfg": cfg, "couleur": brut[OFFU_COULEUR - OFFU_CFG], "duo": brut[OFFU_DUO - OFFU_CFG]}
        for k, nom in enumerate(TEN_FACTEURS):
            out[nom] = brut[OFFU_FACTEURS - OFFU_CFG + k]
        for k, nom in enumerate(TEN_COMPTEURS):
            out[nom], = struct.unpack_from(">H", brut, OFFU_COMPTEURS - OFFU_CFG + 2 * k)
        return out
    # --- enfermement des Hanteurs (0.35.0, mod natif seulement, sans bit : la zone est la capacité) ------------------
    def enf_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_enf`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_enf", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_ENF, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != ENF_FORMAT:
                return None
            a = self._enf = hits[0]
        return a

    @property
    def enf_ok(self) -> bool:
        return self.enf_addr() is not None

    def enfermer(self, centre, rayon: int, salle: int, vie: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 21 ENF : arme (ou entretient, mêmes centre, rayon et salle) l'enfermement de Link dans le cercle
        (``centre`` x, y, z ; ``rayon`` 100..5000 u ; ``salle`` -1..63) pour ``vie`` images (1..300), décomptées par le
        jeu. Renvoie 1 armé, 2 entretenu, ou un refus (< 0, rien d'armé). ValueError avant tout envoi hors bornes ;
        MailboxError sans la zone ; MailboxCancelled sans réponse (requête neutralisée : rien n'a été fait)."""
        if self.enf_addr() is None:
            raise MailboxError("enfermement : seulement le mod natif du portage à jour (0.35.0 ou plus)")
        for v, lo, hi, nom in ((vie, 1, ENF_VIE_MAX, "vie"), (rayon, ENF_RAYON_MIN, ENF_RAYON_MAX, "rayon"),
                               (salle, -1, 63, "salle")):
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"ENF : {nom} {v!r} hors {lo}..{hi}")
        try:
            x, y, z = (float(v) for v in centre)
        except (TypeError, ValueError):
            raise ValueError("ENF : centre illisible") from None
        if not all(v == v and abs(v) < float(1 << 20) for v in (x, y, z)):
            raise ValueError("ENF : centre refusé")
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(self.addr + OFF_CREATE_POS, struct.pack(">3f", x, y, z))
            return self._call(CMD_CALL, CALL_ENF, vie, rayon, salle, timeout=timeout, cancel=True)

    def liberer_enf(self, timeout: float = TIMEOUT_V19) -> int:
        """CALL 21 ENF avec une vie nulle : Link libéré tout de suite (0). MailboxError sans la zone."""
        if self.enf_addr() is None:
            raise MailboxError("enfermement : seulement le mod natif du portage à jour (0.35.0 ou plus)")
        return self._call(CMD_CALL, CALL_ENF, 0, 0, 0, timeout=timeout, cancel=True)

    def enf_counters(self) -> dict:
        """Mise au point : {"arme", "vie", "appels", "armements", "refus", "dernier", "repousses", "chocs",
        "liberations", "raison", "evt", "effets", "sons"} ; zéros sans la zone."""
        cles = ("appels", "armements", "refus", "dernier", "repousses", "chocs", "liberations", "raison", "evt",
                "effets", "sons")
        a = self.enf_addr()
        if a is None:
            return dict.fromkeys(("arme", "vie") + cles, 0)
        arme, vie = struct.unpack(">Ii", bytes(self.mem.read(a + OFFE_ARME, 8)))
        v = struct.unpack(">3Ii7I", bytes(self.mem.read(a + OFFE_APPELS, 44)))
        return {"arme": arme, "vie": vie, **dict(zip(cles, v))}
    # --- commandes à la manette (0.37.0, mod natif seulement, sans bit : la zone est la capacité) ------------------
    def manette_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_man`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_man", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_MAN, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != MAN_FORMAT:
                return None
            a = self._man = hits[0]
        return a

    @property
    def manette_ok(self) -> bool:
        return self.manette_addr() is not None

    def manette_masques(self, masques) -> bool:
        """Combinaisons à lire (code compact 16 bits, au plus 16 ; le reste mis à 0 : emplacements libres)."""
        a = self.manette_addr()
        if a is None:
            return False
        m = [int(v) & 0xFFFF for v in list(masques)[:MAN_N]]
        m += [0] * (MAN_N - len(m))
        self.mem.write(a + OFFM_MASQUES, struct.pack(">16H", *m))
        return True

    def manette_fronts(self) -> list[int]:
        """Compteurs (u8) de combinaisons complétées, un par emplacement."""
        a = self.manette_addr()
        if a is None:
            return [0] * MAN_N
        return list(bytes(self.mem.read(a + OFFM_FRONTS, MAN_N)))

    def manette_tenus(self) -> int:
        """Boutons VPAD tenus à la dernière lecture du jeu (diagnostic)."""
        a = self.manette_addr()
        return struct.unpack(">I", bytes(self.mem.read(a + OFFM_TENUS, 4)))[0] if a is not None else 0

    # --- objets progressifs de TWWHD (0.40.0, mod natif seulement, sans bit : la zone est la capacité) -------------
    def plus_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_prg2`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_prg2", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_PRG2, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != PRG2_FORMAT:
                return None
            a = self._prg2 = hits[0]
        return a

    def plus_lire(self) -> dict | None:
        """Familles actives (bit k), session, dons bruts, dernier don, total et comptes par famille (PRG2_N) ; None
        sans la zone. Le total est relu APRÈS les comptes (le jeu l'écrit en dernier) : s'il a bougé entre les deux
        lectures, la lecture est refaite (une fois)."""
        a = self.plus_addr()
        if a is None:
            return None
        for _essai in range(2):
            b = bytes(self.mem.read(a + OFFQ_FAMILLES, PRG2_TAILLE - OFFQ_FAMILLES))
            fam, ses, bruts, dernier, total = struct.unpack_from(">5I", b, 0)
            recus = list(struct.unpack_from(f">{PRG2_N}I", b, OFFQ_RECUS - OFFQ_FAMILLES))
            if struct.unpack(">I", bytes(self.mem.read(a + OFFQ_TOTAL, 4)))[0] == total:
                break
        return {"familles": fam, "session": ses, "bruts": bruts, "dernier": dernier, "total": total, "recus": recus}

    def plus_session(self, session: int) -> bool:
        """Écrit la session (non nulle) si la zone n'en a pas encore ; relue. Données seulement."""
        a = self.plus_addr()
        session &= 0xFFFFFFFF
        if a is None or not session:
            return False
        cur = struct.unpack(">I", bytes(self.mem.read(a + OFFQ_SESSION, 4)))[0]
        if cur:
            return cur == session
        self.mem.w_u32(a + OFFQ_SESSION, session)
        return struct.unpack(">I", bytes(self.mem.read(a + OFFQ_SESSION, 4)))[0] == session

    # --- fantôme soulevable par Link (0.38.0, mod natif seulement, sans bit : la zone est la capacité) ---------------
    def porter_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_por`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_por", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_POR, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != POR_FORMAT:
                return None
            a = self._por = hits[0]
        return a

    @property
    def porter_ok(self) -> bool:
        return self.porter_addr() is not None

    @property
    def porter_p1(self) -> bool:
        """La zone a la partie P1 « porteur » (mod 0.39.0 ou plus) : o, refus, fins, ligne, cible du bouton A."""
        return self.porter_addr() is not None and self.version_mod_natif() >= POR_P1_MOD

    def porter_regler(self, portables: int, hauteur: float = 0.0, ligne: int = 0) -> bool:
        """Emplacements dont le fantôme est soulevable (bit i ; 0 : aucun), hauteur du point visé au-dessus de ses
        pieds (0 : défaut du mod) et, mod 0.39.0, ligne de la table des distances (0 : 0x27, celle de Médolie ;
        1..POR_LIGNE_MAX). False sans la zone (mod plus ancien, Cemu)."""
        a = self.porter_addr()
        if a is None:
            return False
        h = float(hauteur) if hauteur == hauteur and abs(float(hauteur)) < 300.0 else 0.0
        self.mem.write(a + OFFPO_PORTABLES, struct.pack(">If", int(portables) & 0x7F, h))
        if self.porter_p1:
            lg = int(ligne or 0)
            self.mem.write(a + OFFPO_LIGNE, struct.pack(">I", lg if 0 < lg <= POR_LIGNE_MAX else 0))
        return True

    def porter_refuser(self, refus: int) -> bool:
        """Refus du serveur (bit i : emplacement dont la prise est refusée) : le mod retire CARRY au fantôme tenu (Link
        le lâche par son propre code) et ne le propose plus au bouton A tant que le bit reste levé. False sans la
        partie P1 (mod plus ancien que 0.39.0)."""
        a = self.porter_addr()
        if a is None or not self.porter_p1:
            return False
        self.mem.write(a + OFFPO_REFUS, struct.pack(">I", int(refus) & 0x7F))
        return True

    def porter_compteurs(self) -> dict:
        """Compteurs du mod (POR_CLES, plus « pos » : position du fantôme au dernier pas tenu) ; {} sans la zone.
        Mod 0.39.0 : aussi « o » (fantôme dans le repère de Link), « incl_z », « ecart_y », « angle_link », « fins »,
        « fin_raison » (nom, POR_FINS), « refus_faits », « cible_images », « cible_dmin » / « cible_dmax » (distance
        horizontale Link-fantôme quand le bouton A le visait), « cible_dy », « proches », « autre_proc »."""
        a = self.porter_addr()
        if a is None:
            return {}
        v = struct.unpack(">13I3f", bytes(self.mem.read(a + OFFPO_TENUS, 13 * 4 + 12)))
        d = dict(zip(POR_CLES, v[:13]))
        d["pos"] = [round(x, 1) for x in v[13:]]
        if self.porter_p1:
            b = bytes(self.mem.read(a + OFFPO_O, POR_P1_TAILLE - OFFPO_O))
            o = struct.unpack_from(">3f", b, 0)
            ang, ang_link, fins, raison, _refus, refus_faits = struct.unpack_from(">6I", b, 0x0C)
            ci, = struct.unpack_from(">I", b, OFFPO_CIBLE - OFFPO_O)
            d2min, d2max, dy = struct.unpack_from(">3f", b, OFFPO_CIBLE - OFFPO_O + 4)
            proches, autre = struct.unpack_from(">2I", b, OFFPO_CIBLE - OFFPO_O + 16)

            def s16(x):
                return x - 0x10000 if x & 0x8000 else x
            d.update({"o": [round(x, 1) for x in o], "incl_z": s16(ang >> 16), "ecart_y": s16(ang & 0xFFFF),
                      "angle_link": ang_link & 0xFFFF, "fins": fins, "fin_raison": POR_FINS.get(raison, raison),
                      "refus_faits": refus_faits, "cible_images": ci,
                      "cible_dmin": round(max(d2min, 0.0) ** 0.5, 1) if ci else None,
                      "cible_dmax": round(max(d2max, 0.0) ** 0.5, 1) if ci else None,
                      "cible_dy": round(dy, 1) if ci else None, "proches": proches, "autre_proc": autre})
            if self.version_mod_natif() >= POR_P2_MOD:
                # 0.41.0 : o (ci-dessus) = dernier pas STABLE (GrabWait) ; o_brut = dernier pas tenu (sens de 0.39.0)
                ob = struct.unpack_from(">3f", b, OFFPO_O_BRUT - OFFPO_O)
                st, = struct.unpack_from(">I", b, OFFPO_O_BRUT - OFFPO_O + 12)
                so = struct.unpack_from(">3f", b, OFFPO_O_BRUT - OFFPO_O + 16)
                d.update({"o_brut": [round(x, 1) for x in ob], "stables": st,
                          "o_moyen": [round(x / st, 1) for x in so] if st else None})
        return d

    # --- le Link porté (0.41.0, mod natif seulement, sans bit : la zone est la capacité) ------------------------------
    def porte_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_prt`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle."""
        if not self.pack >= V19:
            return None
        a = getattr(self, "_prt", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_PRT, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != PRT_FORMAT:
                return None
            a = self._prt = hits[0]
        return a

    @property
    def porte_ok(self) -> bool:
        return self.porte_addr() is not None

    def porter_sur(self, emplacement: int, o, vie: int, drapeaux: int = 0, angle: int = 0,
                   timeout: float = TIMEOUT_V19) -> int:
        """CALL 22 PRT : arme (ou entretient, même emplacement) le port de NOTRE Link sur les mains du fantôme de
        l'emplacement ``emplacement`` (0..6), ``o`` = (x local, hauteur, devant) dans le repère du porteur (±300),
        pour ``vie`` images (1..300) ; ``drapeaux`` PRT_ANGLE_GARDE / PRT_STICK_LIBRE, ``angle`` y relatif au porteur.
        Renvoie 1 armé, 2 entretenu, ou un refus (< 0, PRT_REFUS). ValueError avant tout envoi hors bornes ;
        MailboxError sans la zone ; MailboxCancelled sans réponse (requête neutralisée : rien n'a été fait)."""
        if self.porte_addr() is None:
            raise MailboxError("porté : seulement le mod natif du portage à jour (0.41.0 ou plus)")
        for v, lo, hi, nom in ((vie, 1, PRT_VIE_MAX, "vie"), (emplacement, 0, 6, "emplacement"),
                               (drapeaux, 0, 0xFFFF, "drapeaux")):
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"PRT : {nom} {v!r} hors {lo}..{hi}")
        try:
            x, y, z = (float(v) for v in o)
        except (TypeError, ValueError):
            raise ValueError("PRT : o illisible") from None
        if not all(v == v and abs(v) < 300.0 for v in (x, y, z)):
            raise ValueError("PRT : o refusé (±300)")
        arg3 = (drapeaux & 0xFFFF) | ((int(angle) & 0xFFFF) << 16)
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(self.addr + OFF_CREATE_POS, struct.pack(">3f", x, y, z))
            return self._call(CMD_CALL, CALL_PRT, vie, emplacement, arg3, timeout=timeout, cancel=True)

    def descendre(self, timeout: float = TIMEOUT_V19) -> int:
        """CALL 22 PRT avec une vie nulle : Link n'est plus porté (0). MailboxError sans la zone."""
        if self.porte_addr() is None:
            raise MailboxError("porté : seulement le mod natif du portage à jour (0.41.0 ou plus)")
        return self._call(CMD_CALL, CALL_PRT, 0, 0, 0, timeout=timeout, cancel=True)

    def porte_compteurs(self) -> dict:
        """Mise au point : armé, vie, emplacement, o, cible, glissement, compteurs (appels, armements, refus, dernier,
        libérations, raison en clair, fin_proc, pas imposés, sols corrigés, sols absents, sol du dessous, plafond,
        filtres, visées, proc, evt, écart max, L3), procédures vues (liste) et glissades ; {} sans la zone."""
        a = self.porte_addr()
        if a is None:
            return {}
        b = bytes(self.mem.read(a, 0xB8))
        arme, vie, emp = struct.unpack_from(">IiI", b, OFFT_ARME)
        o = struct.unpack_from(">3f", b, 0x18)
        dr, salle, glisse = struct.unpack_from(">IiI", b, 0x24)
        cible = struct.unpack_from(">3f", b, 0x3C)
        appels, arm, refus, dernier, lib, raison, fin_proc, imposes, sols, absents = struct.unpack_from(">3Ii6I", b,
                                                                                                       OFFT_APPELS)
        dessous, toit = struct.unpack_from(">2f", b, 0x70)
        filtres, visees, proc, evt = struct.unpack_from(">4I", b, 0x78)
        e2, = struct.unpack_from(">f", b, 0x88)
        l3, = struct.unpack_from(">I", b, 0x8C)
        procs = struct.unpack_from(">8I", b, OFFT_PROCS)
        glissades, = struct.unpack_from(">I", b, 0xB0)
        vus = [32 * i + k for i, w in enumerate(procs) for k in range(32) if w >> k & 1]
        return {"arme": arme, "vie": vie, "emplacement": emp, "o": [round(x, 1) for x in o], "drapeaux": dr & 0xFFFF,
                "angle": dr >> 16, "salle": salle, "glisse": glisse, "cible": [round(x, 1) for x in cible],
                "appels": appels, "armements": arm, "refus": refus, "dernier": dernier, "liberations": lib,
                "raison": PRT_RAISONS.get(raison, raison), "raison_n": raison, "fin_proc": fin_proc,
                "pas_imposes": imposes, "sols": sols, "sols_absents": absents, "sol_dessous": round(dessous, 1),
                "toit": round(toit, 1), "filtres": filtres, "visees": visees, "proc": proc, "evt": evt,
                "ecart_max": round(max(e2, 0.0) ** 0.5, 1), "l3": l3, "procs_vus": vus, "glissades": glissades}

    def set_couleurs_carte(self, couleurs) -> bool:
        """Couleurs (7 mots RRGGBBAA, un par emplacement de fantôme ; 0 = blanc) des marqueurs de la carte."""
        a = self.carte_addr()
        if a is None:
            return False
        c = [int(v) & 0xFFFFFFFF for v in couleurs]
        if len(c) != 7:
            raise ValueError("carte : 7 couleurs attendues")
        self.mem.write(a + OFFC_COULEURS, struct.pack(">7I", *c))
        return True

    def set_couleur_moi_carte(self, rgba: int) -> bool:
        """Couleur (RRGGBBAA ; 0 = icône du jeu telle quelle) du bonnet de NOTRE icône sur la carte (mod 0.31.0)."""
        a = self.carte_addr()
        if a is None:
            return False
        self.mem.write(a + OFFC_MOI, struct.pack(">I", int(rgba) & 0xFFFFFFFF))
        return True

    def carte_coupee(self, coupe: bool) -> bool:
        a = self.carte_addr()
        if a is None:
            return False
        self.mem.write(a + OFFC_COUPE, struct.pack(">I", 1 if coupe else 0))
        return True

    def carte_etat(self) -> dict:
        """Compteurs du jeu (mise au point, rapport d'essai)."""
        a = self.carte_addr()
        if a is None:
            return {}
        d = bytes(self.mem.read(a + OFFC_DESSINS, 0x24))
        dessins, listes, ecran, vues, vues_carte = struct.unpack_from(">5I", d, 0)
        ecart, = struct.unpack_from(">f", d, 0x14)
        inscrit, texture, marques = struct.unpack_from(">3I", d, 0x18)
        return {"dessins": dessins, "listes": listes, "ecran": ecran, "vues": vues, "vues_carte": vues_carte,
                "ecart_link": ecart, "inscrit": inscrit, "texture": texture, "marques": marques}

    @property
    def canon_bateau(self) -> bool:
        """Canon du bateau fantôme (bit 28, mod natif 0.14.0 seulement) : emplacements 3 à 7 dessinés sans recalcul."""
        return self.pack >= V19 and bool(self.caps & CAP_CANON_BATEAU)

    # --- GRANT genre 4 : drapeau d'événement (bit 31, mod natif 0.24.0 seulement) -----------------------------
    @property
    def grant_evt_ok(self) -> bool:
        """Mod natif dont le CALL 8 GRANT sait le genre 4 (drapeau d'événement, bit 31) ; jamais un pack Cemu."""
        return bool(self.pack >= V19 and self.caps & CAP_GRANT_EVT)

    def grant_event(self, flag: int, item: int = ITEM_NONE, count: int = 1, timeout: float = TIMEOUT_V19) -> int:
        """GRANT genre 4 : ``flag`` = 0xXXYY (octet XX de mEvent, masque YY entier). Tous les bits du masque posés
        PUIS l'objet donné, dans la même image. 1 posé et donné, 0 déjà posé (rien fait), -1 refusé, -3 occupé.
        MailboxError sans le bit 31 (pack Cemu, mod plus ancien) : rien n'est envoyé."""
        if not self.grant_evt_ok:
            raise MailboxError("GRANT genre 4 (drapeau d'événement) : mod natif 0.24.0 ou plus récent requis")
        flag = int(flag)
        if not 0 <= flag <= 0xFFFF:
            raise ValueError(f"GRANT genre 4 : drapeau 0x{flag:X} hors bornes")
        return self.grant(GRANT_EVENT, flag >> 8, flag & 0xFF, item, count, False, timeout=timeout)

    # --- boulets du canon du bateau (bit 29, mod natif 0.17.0 seulement) -------------------------------------
    @property
    def boulets_ok(self) -> bool:
        """Mod natif avec les boulets du canon (bit 29) et sa zone ``wwmp_bou`` trouvée (jamais un pack Cemu)."""
        return self.bou_addr() is not None

    def bou_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_bou`` (magie cherchée une fois, unique, alignée, format attendu)."""
        if not (self.pack >= V19 and self.caps & CAP_BOULETS):
            return None
        a = getattr(self, "_bou", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_BOU, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != BOU_FORMAT:
                return None
            a = self._bou = hits[0]
        return a

    def bou_regler(self, cfg: int) -> None:
        """``cfg`` : bit 0 = tirs de notre bateau relevés, bit 1 = copies permises (CALL 16). Sans la zone : rien."""
        a = self.bou_addr()
        if a is None:
            return
        if isinstance(cfg, bool) or not isinstance(cfg, int) or not 0 <= cfg <= 3:
            raise ValueError("bou_regler : hors bornes")
        self.mem.write(a + OFFB_CFG, struct.pack(">I", cfg))

    def bou_seq(self) -> int:
        a = self.bou_addr()
        return 0 if a is None else struct.unpack(">I", bytes(self.mem.read(a + OFFB_SEQ, 4)))[0]

    def bou_tirs(self, depuis: int) -> tuple[int, list[dict], int]:
        """Tirs de notre bateau poussés depuis le compteur ``depuis`` : (nouveau compteur, tirs dans l'ordre, perdus).
        Un tir n'est rendu que si son numéro est le bon (pas réécrit pendant la lecture) ; au plus 8. Tir : {"seq",
        "id", "adr", "pos" (3 f32), "ang" (3 s16), "salle"}."""
        a = self.bou_addr()
        if a is None:
            return depuis, [], 0
        seq = self.bou_seq()
        n = (seq - depuis) & 0xFFFFFFFF
        if n == 0 or n > 0x7FFFFFFF:
            return seq, [], 0
        k = min(n, BOU_N_TIRS)
        perdus = n - k
        out = []
        for j in range(k):
            s = (seq - k + 1 + j) & 0xFFFFFFFF
            e = bytes(self.mem.read(a + OFFB_TIR + (s & (BOU_N_TIRS - 1)) * BOU_TIR, BOU_TIR))
            sq, pid, adr, x, y, z, ax, ay, az, salle = struct.unpack(">3I3f3hb", e[:0x1F])
            if sq != s:
                perdus += 1
                continue
            out.append({"seq": sq, "id": pid, "adr": adr, "pos": [x, y, z], "ang": [ax, ay, az], "salle": salle})
        return seq, out, perdus

    def boulet_creer(self, pos, ang, salle: int, source: int = 0, timeout: float = TIMEOUT_V19) -> int:
        """CALL 16 BOULET : copie inerte d'un boulet tiré par un autre joueur, partie de ``pos`` avec l'angle ``ang``
        (hausse, cap, roulis : s16) dans la salle ``salle`` ; vitesses et gravité calculées par le mod comme le bateau
        du jeu. ``source`` : id du boulet chez le tireur (mise au point). Résultat : id de la copie (> 0), -1 refusé
        (cfg bit 1 absent, salle ou position hors bornes), -2 pas de partie, -3 événement, -4 création ratée, -5 plus
        de place (8 copies). MailboxError sans la zone, ValueError hors bornes (rien n'est envoyé)."""
        a = self.bou_addr()
        if a is None:
            raise MailboxError("boulets du canon (bit 29) : seulement le mod natif du portage à jour (0.17.0 ou plus)")
        p = [float(v) for v in pos]
        if len(p) != 3 or not all(abs(v) <= BOU_POS_MAX for v in p):           # NaN : comparaison fausse, refusé
            raise ValueError("boulet_creer : position hors bornes")
        g = list(ang)
        if len(g) != 3 or any(isinstance(v, bool) or not isinstance(v, int) or not -0x8000 <= v <= 0xFFFF for v in g):
            raise ValueError("boulet_creer : angle hors bornes")
        if isinstance(salle, bool) or not isinstance(salle, int) or not 0 <= salle <= 63:
            raise ValueError("boulet_creer : salle hors bornes")
        if isinstance(source, bool) or not isinstance(source, int) or not 0 <= source <= 0xFFFFFFFF:
            raise ValueError("boulet_creer : source hors bornes")
        buf = struct.pack(">3f3HHi", *p, *(v & 0xFFFF for v in g), 0, salle)
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(a + OFFB_POS, buf)
            return self._call(CMD_CALL, CALL_BOULET, source, timeout=timeout, cancel=True)

    def boulet_fin(self, copie: int) -> bool:
        """Copie ``copie`` à retirer (le mod la retire sans explosion à son pas suivant). False : pas trouvée."""
        a = self.bou_addr()
        if a is None or isinstance(copie, bool) or not isinstance(copie, int) or not 0 < copie <= 0xFFFFFFFF:
            return False
        for k in range(BOU_N_COPIES):
            e = a + OFFB_COPIE + k * BOU_COPIE
            if struct.unpack(">I", bytes(self.mem.read(e, 4)))[0] == copie:
                self.mem.write(e + 8, struct.pack(">I", 1))
                return True
        return False

    def bou_counters(self) -> dict:
        """Mise au point : {"cfg", "seq", "copies", "retirees", "interceptees", "refus", "sons"} ; zéros sans la zone
        (« sons » : sons du tir joués par CALL 16, mod 0.28.0 ; 0 avec un mod plus ancien, mot réservé)."""
        a = self.bou_addr()
        noms = ("cfg", "seq", "copies", "retirees", "interceptees", "refus", "sons")
        if a is None:
            return {k: 0 for k in noms}
        return dict(zip(noms, struct.unpack(">7I", bytes(self.mem.read(a + OFFB_CFG, 28)))))

    @property
    def voile_bateau(self) -> bool:
        """Voile, mât et gouvernail du bateau fantôme (bit 22) : le pack lit les matrices écrites par le PC."""
        return self.pack >= V19 and bool(self.caps & CAP_VOILE_BATEAU)

    @property
    def visee(self) -> bool:
        """Visée L/ZL (bit 20 des capacités) et zone ``wwmp_visee`` trouvée, au bon format."""
        return self.visee_addr() is not None

    def visee_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_visee`` (magie cherchée une fois, unique, alignée, dans le pack, format attendu).
        None : pack sans le bit 20 ou zone douteuse (rien n'est alors écrit)."""
        if not (self.pack >= V19 and self.caps & CAP_VISEE):
            return None
        a = getattr(self, "_visee", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_VISEE, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != VISEE_FORMAT:
                return None
            a = self._visee = hits[0]
        return a

    def set_visee_cfg(self, cfg: int, masque: int | None = None) -> bool:
        """cfg (bit 0 fantômes, bit 1 marionnettes) et, si donné, masque des emplacements d'ennemis jamais visables
        (écrit AVANT cfg). Données seules ; False sans la zone (pack plus ancien : rien n'est écrit)."""
        a = self.visee_addr()
        if a is None:
            return False
        if masque is not None:
            self.mem.w_u32(a + OFFV_MASQUE, int(masque) & ((1 << N_ENEMY_SLOTS) - 1))
        self.mem.w_u32(a + OFFV_CFG, int(cfg) & (VISEE_FANTOMES | VISEE_MARIONNETTES))
        return True

    def set_slot_visee(self, i: int, drapeaux: int, ligne: int) -> bool:
        """Emplacement de fantôme ``i`` : drapeaux de visée (0, 1 ou 4) et ligne de distance (0, 3 ou 0x22), deux
        octets en +0x16 (``set_slot`` n'écrit que +0x04..+0x15 : ils ne s'écrasent pas). False sans la zone."""
        if drapeaux not in VISEE_DRAPEAUX or ligne not in VISEE_LIGNES or isinstance(drapeaux, bool):
            raise ValueError(f"visée : drapeaux {drapeaux!r} ou ligne {ligne!r} hors liste")
        if self.visee_addr() is None:
            return False
        self.mem.write(self.slot_addr(i) + SLOT_VISEE, bytes((drapeaux, ligne)))
        return True

    def set_enemy_visee(self, i: int, y: float | None) -> bool:
        """Emplacement d'ennemi ``i`` : y visé (absolu) ou None / 0 (rien n'est écrit par le pack). Un y hors des
        bornes du pack, infini ou NaN est écrit 0. False sans la zone."""
        if not 0 <= i < N_ENEMY_SLOTS:
            raise IndexError(f"emplacement d'ennemi {i} hors 0..{N_ENEMY_SLOTS - 1}")
        if self.visee_addr() is None:
            return False
        brut = bytes(4)
        if y is not None:
            try:
                v = float(y)
            except (TypeError, ValueError):
                v = 0.0
            if v == v and abs(v) < VISEE_Y_MAX and v != 0.0:
                brut = struct.pack(">f", v)
                if struct.unpack(">I", brut)[0] & 0x7FFFFFFF == 0:      # sous-normal arrondi à 0
                    brut = bytes(4)
        self.mem.write(self.enemy_slot_addr(i) + ENEMY_VISEE, brut)
        return True

    def visee_counters(self) -> dict:
        """Mise au point : {"cfg", "masque", "fantomes", "marionnettes"} ; zéros sans la zone."""
        a = self.visee_addr()
        if a is None:
            return {"cfg": 0, "masque": 0, "fantomes": 0, "marionnettes": 0}
        cfg, m, f, e = struct.unpack(">4I", bytes(self.mem.read(a + OFFV_CFG, 16)))
        return {"cfg": cfg, "masque": m, "fantomes": f, "marionnettes": e}

    def houle_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_houle`` (magie cherchée une fois, unique, alignée, dans le pack, format
        attendu). None : pack plus ancien ou zone douteuse (rien n'est alors lu ni écrit)."""
        if self.pack < V19:
            return None
        a = getattr(self, "_houle", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_HOULE, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != HOULE_FORMAT:
                return None
            a = self._houle = hits[0]
        return a

    def houle_lire(self) -> dict | None:
        """{"images", "link": (état, x, z, h, y), "bateau": (état, x, z, h, y), "points": [(état, x, z, h)] * 14}
        (dernier calcul du jeu), ou None sans la zone."""
        a = self.houle_addr()
        if a is None:
            return None
        raw = bytes(self.mem.read(a, OFFW_PTS + 16 * (2 + HOULE_PC_MAX)))
        images, y_link, y_bat = struct.unpack_from(">I2f", raw, OFFW_IMAGES)
        pts = [struct.unpack_from(">3fI", raw, OFFW_PTS + 16 * i) for i in range(2 + HOULE_PC_MAX)]
        ent = [(e, x, z, h) for x, z, h, e in pts]
        return {"images": images, "link": ent[0] + (y_link,), "bateau": ent[1] + (y_bat,), "points": ent[2:]}

    def houle_sonder(self, points: list[tuple[float, float]]) -> bool:
        """Points (x, z) des fantômes, entrées 2 et suivantes (14 au plus ; valeurs non finies refusées : 0, 0),
        puis n. False sans la zone (rien n'est écrit)."""
        a = self.houle_addr()
        if a is None:
            return False
        pts = list(points)[:HOULE_PC_MAX]
        brut = bytearray()
        for x, z in pts:
            try:
                x, z = float(x), float(z)
            except (TypeError, ValueError):
                x = z = 0.0
            if not (abs(x) < 1e7 and abs(z) < 1e7):      # NaN, infini, absurde
                x = z = 0.0
            brut += struct.pack(">2f", x, z)
        for i in range(len(pts)):
            self.mem.write(a + OFFW_PTS + 16 * (2 + i), bytes(brut[8 * i:8 * i + 8]))
        self.mem.w_u32(a + OFFW_N, len(pts))
        return True

    # --- vent du Requiem (mod natif 0.22.0 seulement, zone sans bit) ---------------------------------------------------
    def vent_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_ven`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle
        (pack Cemu, mod plus ancien : le client lit alors la sauvegarde comme avant)."""
        if self.pack < V19:
            return None
        a = getattr(self, "_vent", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_VENT, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != VENT_FORMAT:
                return None
            a = self._vent = hits[0]
        return a

    def vent_lire(self) -> dict | None:
        """{"requiem", "x", "y", "demande", "fait", "appliques", "attentes"} (compteur des Requiem joués ICI et
        direction du dernier ; état des demandes du PC), ou None sans la zone. Le compteur est lu AVANT la direction
        (le jeu l'écrit en dernier)."""
        a = self.vent_addr()
        if a is None:
            return None
        n, = struct.unpack(">I", bytes(self.mem.read(a + OFFV2_REQUIEM, 4)))
        raw = bytes(self.mem.read(a + OFFV2_REQ, OFFV2_ATTENTES + 4 - OFFV2_REQ))
        x, y, dem = struct.unpack_from(">hhI", raw, 0)
        fait, app, att = struct.unpack_from(">3I", raw, OFFV2_FAIT - OFFV2_REQ)
        return {"requiem": n, "x": x, "y": y, "demande": dem, "fait": fait, "appliques": app, "attentes": att}

    def vent_appliquer(self, x: int, y: int, on: bool) -> int | None:
        """Demande au jeu d'appliquer cette direction (une fois, au prochain pas de Link libre) : x, y, on, puis le
        numéro de la demande EN DERNIER. Rend ce numéro, ou None sans la zone (rien n'est écrit)."""
        a = self.vent_addr()
        if a is None:
            return None
        if not (-0x8000 <= int(x) <= 0x7FFF and -0x8000 <= int(y) <= 0x7FFF):
            raise ValueError("vent_appliquer : angle hors bornes")
        dem, = struct.unpack(">I", bytes(self.mem.read(a + OFFV2_DEMANDE, 4)))
        dem = (dem + 1) & 0xFFFFFFFF
        self.mem.write(a + OFFV2_XY, struct.pack(">hhI", int(x), int(y), 1 if on else 0))
        self.mem.w_u32(a + OFFV2_DEMANDE, dem)
        return dem

    # --- ouverture d'un coffre vue chez les autres (mod natif 0.25.0 seulement, zone sans bit, CALL 17) ----------------
    def ouv_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_ouv`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle
        (pack Cemu, mod plus ancien : le coffre est recréé ouvert ou figé, comme avant)."""
        if self.pack < V19:
            return None
        a = getattr(self, "_ouv", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_OUV, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1:
                return None
            if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != OUV_FORMAT:
                return None
            a = self._ouv = hits[0]
        return a

    @property
    def ouverture_ok(self) -> bool:
        return self.ouv_addr() is not None

    def ouverture_coffre(self, pid: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 17 OUVERTURE : le coffre ``pid`` (proc 292, en actionOpenWait, Link à 100 u ou plus) s'ouvre chez nous
        comme dans le jeu (couvercle, éclat, lumières, sons) sans objet, événement ni caméra, et reste en actionWait.
        Résultat : emplacement 1..4, ou -2 pas de partie, -3 événement, -4 coffre absent, -5 coffre pas ouvrable
        (action, commande, ouverture ou éclat en cours, né ouvert), -6 Link trop près (ou CANDOOR posé), -7 plus de
        place. MailboxError sans la zone, ValueError hors bornes (rien n'est envoyé)."""
        if self.ouv_addr() is None:
            raise MailboxError("ouverture des coffres : seulement le mod natif du portage à jour (0.25.0 ou plus)")
        if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("ouverture_coffre : id hors bornes")
        with Mailbox._lock:
            return self._call(CMD_CALL, CALL_OUVERTURE, pid, timeout=timeout, cancel=True)

    def ouv_lire(self) -> dict | None:
        """{"lancees", "finies", "interrompues", "refus", "images", "dernier", "en_cours": [(id, état)]} ; None sans
        la zone."""
        a = self.ouv_addr()
        if a is None:
            return None
        raw = bytes(self.mem.read(a, OUV_TAILLE))
        lan, fin, inter, ref, img = struct.unpack_from(">5I", raw, OFFO_LANCEES)
        dern, = struct.unpack_from(">i", raw, OFFO_DERNIER)
        en_cours = []
        for k in range(OUV_N):
            pid, _adr, _debut, etat = struct.unpack_from(">4I", raw, OFFO_OUV + k * OUV_ENTREE)
            if etat & OUV_ACTIF:
                en_cours.append((pid, etat))
        return {"lancees": lan, "finies": fin, "interrompues": inter, "refus": ref, "images": img, "dernier": dern,
                "en_cours": en_cours}

    # --- états des marionnettes (mod natif 0.29.0 seulement, zone sans bit) ------------------------------------------
    def etats_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_eta`` (magie cherchée une fois, unique, alignée, format attendu) ; None sans elle
        (pack Cemu, mod plus ancien : les marionnettes restent comme avant)."""
        if self.pack < V19:
            return None
        a = getattr(self, "_etats", None)
        if a is None:
            try:
                hits = [h for h in self.mem.find_bytes(MAGIC_ETATS, self.addr, self.addr + 0x8000, limit=4)
                        if h % 4 == 0]
                if len(hits) != 1:
                    return None
                if struct.unpack(">I", bytes(self.mem.read(hits[0] + 8, 4)))[0] != ETATS_FORMAT:
                    return None
            except Exception:
                return None
            a = self._etats = hits[0]
        return a

    def ecrire_etat(self, i: int, entree: bytes) -> bool:
        """Entrée ``i`` (0x14 octets de synchro/etats_marionnettes.entree, en une écriture) ; False sans la zone."""
        a = self.etats_addr()
        if a is None:
            return False
        if not 0 <= i < 16 or len(entree) != ETAT_ENTREE - 4:
            raise ValueError("ecrire_etat : emplacement ou entrée invalide")
        self.mem.write(a + OFFE_ENTREES + ETAT_ENTREE * i, bytes(entree))
        return True

    def effacer_etat(self, i: int) -> None:
        """Entrée ``i`` remise à zéro, « vu » compris (la prochaine marionnette de l'emplacement repart de zéro)."""
        a = self.etats_addr()
        if a is not None and 0 <= i < 16:
            self.mem.write(a + OFFE_ENTREES + ETAT_ENTREE * i, bytes(ETAT_ENTREE))

    def etats_lire(self) -> dict | None:
        """Compteurs du jeu (appliqués, refus, ailes coupées, flammes allumées, éteintes, retirées) ou None."""
        a = self.etats_addr()
        if a is None:
            return None
        raw = bytes(self.mem.read(a + OFFE_COMPTEURS, 4 * len(ETATS_COMPTEURS)))
        return dict(zip(ETATS_COMPTEURS, struct.unpack(">%dI" % len(ETATS_COMPTEURS), raw)))

    def rando4_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_rnd4`` (coffres « en conflit » ; magie cherchée une fois, unique, alignée,
        APRÈS la zone wwmp_rnd2, format attendu). None : pack sans cette zone (plus ancien) ou zone douteuse."""
        if not self.rando2:
            return None
        a = getattr(self, "_rando4", None)
        if a is None:
            base = self.rando2_addr()
            if base is None:
                return None
            hits = [h for h in self.mem.find_bytes(MAGIC_RANDO4, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1 or hits[0] < base + OFFR2_PERMIS + 0x100:
                return None
            if struct.unpack(">I", self.mem.read(hits[0] + 8, 4))[0] != RANDO4_FORMAT:
                return None
            a = self._rando4 = hits[0]
        return a

    def rando4_permis(self) -> bytes | None:
        """Liste d'objets du stub N1 quand la zone des coffres en conflit est chargée (256 octets, constantes du
        pack). None sans la zone."""
        a = self.rando4_addr()
        return None if a is None else bytes(self.mem.read(a + OFFR4_PERMIS, 0x100))

    def rando4_lire(self) -> dict | None:
        """En-tête et table de la zone des coffres en conflit : état, nombre, empreinte, coffres reconnus, octets
        des entrées. None sans la zone."""
        a = self.rando4_addr()
        if a is None:
            return None
        etat, n, emp, vus = struct.unpack(">4I", self.mem.read(a + OFFR4_ETAT, 16))
        return {"etat": etat, "n": n, "emp": emp, "vus": vus,
                "table": bytes(self.mem.read(a + OFFR4_TABLE, RANDO4_TABLE))}

    def rando4_vider(self) -> None:
        """État 0 de la zone des coffres en conflit : le stub N1 redevient celui d'avant le lot 2."""
        a = self.rando4_addr()
        if a is not None:
            self.mem.w_u32(a + OFFR4_ETAT, 0)

    def _rando4_ecrire(self, entrees: bytes, emp: int) -> bool:
        """Table des coffres en conflit : état 0, entrées (le reste à zéro), nombre, empreinte, relus, puis état 1.
        Sans entrée : état 0 seulement. Appelée par ``rando_charger`` pendant que l'état de wwmp_rnd est à 0."""
        a = self.rando4_addr()
        if a is None:
            return not entrees
        if not entrees:
            if self.mem.u32(a + OFFR4_ETAT) != 0:
                self.mem.w_u32(a + OFFR4_ETAT, 0)
            return True
        self.mem.w_u32(a + OFFR4_ETAT, 0)
        plein = bytes(entrees) + bytes(RANDO4_TABLE - len(entrees))
        n = len(entrees) // 8
        self.mem.write(a + OFFR4_TABLE, plein)
        self.mem.w_u32(a + OFFR4_N, n)
        self.mem.w_u32(a + OFFR4_EMP, emp & 0xFFFFFFFF)
        if bytes(self.mem.read(a + OFFR4_TABLE, RANDO4_TABLE)) != plein or self.mem.u32(a + OFFR4_N) != n \
                or self.mem.u32(a + OFFR4_EMP) != emp & 0xFFFFFFFF:
            return False
        self.mem.w_u32(a + OFFR4_ETAT, 1)
        return True

    def prog_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_prg`` (objets progressifs ; magie cherchée une fois, unique, alignée, APRÈS la
        zone wwmp_rnd2, format attendu). None : pack sans cette zone (plus ancien) ou zone douteuse."""
        if not self.rando2:
            return None
        a = getattr(self, "_prog", None)
        if a is None:
            base = self.rando2_addr()
            if base is None:
                return None
            hits = [h for h in self.mem.find_bytes(MAGIC_PROG, self.addr, self.addr + 0x8000, limit=4) if h % 4 == 0]
            if len(hits) != 1 or hits[0] < base + OFFR2_PERMIS + 0x100:
                return None
            if struct.unpack(">I", self.mem.read(hits[0] + 8, 4))[0] != PROG_FORMAT:
                return None
            a = self._prog = hits[0]
        return a

    def prog_permis(self) -> bytes | None:
        """Liste des paliers d'objets progressifs du pack (256 octets, constantes). None sans la zone."""
        a = self.prog_addr()
        return None if a is None else bytes(self.mem.read(a + OFFP_PERMIS, 0x100))

    def prog_lire(self) -> dict | None:
        """État, empreinte, coffres à palier traités et carte de la zone des objets progressifs. None sans la zone."""
        a = self.prog_addr()
        if a is None:
            return None
        etat, emp, vus = struct.unpack(">3I", self.mem.read(a + OFFP_ETAT, 12))
        return {"etat": etat, "emp": emp, "vus": vus, "carte": bytes(self.mem.read(a + OFFP_CARTE, 0x100))}

    def prog_vider(self) -> None:
        """État 0 de la zone des objets progressifs : le stub N1 redevient celui d'avant le lot 4."""
        a = self.prog_addr()
        if a is not None and self.mem.u32(a + OFFP_ETAT) != 0:
            self.mem.w_u32(a + OFFP_ETAT, 0)

    def prog_ecrire(self, carte: bytes, emp: int) -> bool:
        """Carte des objets progressifs : état 0, carte et empreinte, relues, puis état 1 EN DERNIER. Une carte relue
        fausse laisse l'état à 0 (aucun effet). La carte ne peut envoyer un palier que vers un palier (ou 0) : toute
        autre carte est refusée ici, et le pack la refuserait aussi (wwmp_prg_permis). Données seulement."""
        carte = bytes(carte)
        if len(carte) != 0x100:
            raise ValueError("carte des objets progressifs : 256 octets attendus")
        a = self.prog_addr()
        if a is None:
            return False
        permis = bytes(self.mem.read(a + OFFP_PERMIS, 0x100))
        if any(v and (permis[k] != 1 or permis[v] != 1) for k, v in enumerate(carte)):
            raise ValueError("carte des objets progressifs : un palier ne mène qu'à un palier du pack")
        emp &= 0xFFFFFFFF
        if self.mem.u32(a + OFFP_ETAT) == 1 and self.mem.u32(a + OFFP_EMP) == emp \
                and bytes(self.mem.read(a + OFFP_CARTE, 0x100)) == carte:
            return True                  # déjà en place : aucune écriture
        self.mem.w_u32(a + OFFP_ETAT, 0)
        self.mem.write(a + OFFP_CARTE, carte)
        self.mem.w_u32(a + OFFP_EMP, emp)
        if bytes(self.mem.read(a + OFFP_CARTE, 0x100)) != carte or self.mem.u32(a + OFFP_EMP) != emp:
            return False
        self.mem.w_u32(a + OFFP_ETAT, 1)
        return True

    def rando_charger(self, table: bytes, emp: int, etat: int = RANDO_ETAT_CHARGE, n2: bytes = b"",
                      conflit: bytes = b"") -> bool:
        """État 0 d'abord, puis table et empreinte, relues, puis l'état (1 = palier 1, 2 = randomizer complet) EN
        DERNIER. Une table relue fausse laisse l'état à 0 (aucun effet). L'état 2 exige le bit 6 (sinon rien n'est
        écrit). ``n2`` : entrées de la table des cadeaux (8 octets chacune, au plus 32 ; mode complet et bit 7
        exigés, sinon rien n'est écrit) ; elle est écrite pendant que l'état est à 0, et vidée si ``n2`` est vide :
        jamais un mélange de deux tables. ``conflit`` : entrées de la table des coffres en conflit (8 octets chacune,
        au plus 16 ; mode complet et zone wwmp_rnd4 exigés, sinon rien n'est écrit), écrite et vidée de même.
        Données seulement : rien n'est exécuté par le jeu ici."""
        if len(conflit) % 8 or len(conflit) > RANDO4_TABLE:
            raise ValueError("table des coffres en conflit du randomizer : au plus 16 entrées de 8 octets")
        if len(table) != RANDO_TABLE:
            raise ValueError("table du randomizer : 1024 octets attendus")
        if etat not in RANDO_ETATS:
            raise ValueError("état du randomizer : 1 ou 2")
        if len(n2) % 8 or len(n2) > RANDO3_TABLE:
            raise ValueError("table des cadeaux du randomizer : au plus 32 entrées de 8 octets")
        a = self.rando_addr()
        if a is None or (etat == RANDO_ETAT_COMPLET and self.rando2_addr() is None):
            return False
        if n2 and (etat != RANDO_ETAT_COMPLET or self.rando3_addr() is None):
            return False
        if conflit and (etat != RANDO_ETAT_COMPLET or self.rando4_addr() is None):
            return False
        self.mem.w_u32(a + OFFR_ETAT, 0)
        if not self._rando3_ecrire(n2, emp):
            return False
        if not self._rando4_ecrire(conflit, emp):
            return False
        self.mem.write(a + OFFR_TABLE, bytes(table))
        self.mem.w_u32(a + OFFR_EMP, emp & 0xFFFFFFFF)
        if bytes(self.mem.read(a + OFFR_TABLE, RANDO_TABLE)) != bytes(table) or self.mem.u32(a + OFFR_EMP) != emp:
            return False
        self.mem.w_u32(a + OFFR_ETAT, etat)
        return True

    @property
    def pilote(self) -> bool:
        """Possession complète (bit 16 des capacités), PV libres (bit 2) et zone ``wwmp_pilote`` trouvée."""
        return self.pilote_addr() is not None

    def pilote_addr(self) -> int | None:
        """Adresse de la zone ``wwmp_pilote`` (magie cherchée une fois dans les 0x8000 octets du pack)."""
        if not (self.pack >= V19 and self.caps & CAP_PILOTE and self.caps & CAP_ENEMY_HP_FREE):
            return None
        a = getattr(self, "_pilote", None)
        if a is None:
            hits = [h for h in self.mem.find_bytes(MAGIC_PILOTE, self.addr, self.addr + 0x8000, limit=4)
                    if h % 4 == 0]
            if len(hits) != 1 or self.mem.u32(hits[0] + 8) != PILOTE_FORMAT:
                return None
            a = self._pilote = hits[0]
        return a

    @property
    def formes(self) -> bool:
        """Formes spéciales du pilotage (bit 17) : drapeaux d'entrée et ordres étendus, zone de pilotage trouvée."""
        return bool(self.caps & CAP_FORMES) and self.pilote

    def set_pilote(self, i: int, proc_name: int, off_action: int, masque: int, carotte=None,
                   yeux: float = 0.0, images: int = PILOTE_IMAGES, drapeaux: int = 0) -> bool:
        """Entrée ``i`` de la zone de pilotage (même index que l'emplacement d'ennemi) + échéance repoussée de
        ``images`` images. ``carotte`` : (x, y, z) fini, ou None (épinglage seul). La carotte n'est armée que si
        la séquence de A3 existe (``ghost_aggro`` : sans elle ``read_stable`` ne protégerait pas les lectures de
        Link) et si le décalage et le masque sont plausibles ; l'état est remis à 0 AVANT de changer le point, armé
        APRÈS. False sans la zone ou hors bornes (rien n'est écrit).
        ``drapeaux`` (formes spéciales, ``PILOTE_FORMES``) : refusés sans le bit 17 (False, rien n'est écrit : un
        pack plus ancien épinglerait toujours). Avec eux l'état n'est JAMAIS remis à 0 entre deux ticks (le jeu
        verrait une image sans drapeau et replacerait le monstre) : il passe aux drapeaux seuls, puis aux drapeaux
        avec la carotte."""
        a = self.pilote_addr()
        if a is None or not 0 <= int(i) < N_ENEMY_SLOTS:
            return False
        drapeaux = int(drapeaux)
        if drapeaux & ~PILOTE_FORMES or (drapeaux and not self.formes):
            return False
        if drapeaux & (PILOTE_GARDE | PILOTE_SANS_EPINGLE) and not (0 < int(off_action) < PILOTE_DECALAGE_MAX
                                                                    and int(masque) & 0xFFFFFFFF):
            return False                          # une forme gardée sans garde lisible : jamais
        proc_name, off_action, masque = int(proc_name), int(off_action), int(masque) & 0xFFFFFFFF
        if not (0 < proc_name < 0x7FFF and proc_name not in (168, 0x1F0)):
            return False
        e = a + OFFPI_TAB + int(i) * PILOTE_ENTREE
        ok = (carotte is not None and self.ghost_aggro and 0 < off_action < PILOTE_DECALAGE_MAX
              and (off_action % 2 == 0 or bool(drapeaux & PILOTE_GARDE_U8)) and masque != 0)
        vals = []
        if ok:
            vals = [float(v) for v in carotte] + [float(yeux)]
            ok = len(vals) == 4 and all(math.isfinite(v) and abs(v) < 1e7 for v in vals)
        self.mem.w_u32(e, drapeaux)
        self.mem.write(e + 0x14, struct.pack(">HhI", off_action if 0 <= off_action < PILOTE_DECALAGE_MAX else 0,
                                             proc_name, masque))
        if ok:
            self.mem.write(e + 0x04, struct.pack(">4f", *vals))
            self.mem.w_u32(e, drapeaux | PILOTE_CAROTTE)
        n = max(1, min(int(images), PILOTE_IMAGES_MAX))
        self.mem.w_u32(a + OFFPI_ECHEANCE, (self.frames() + n) & 0xFFFFFFFF)
        return True

    def clear_pilote(self, i: int | None = None) -> None:
        """Entrée ``i`` remise à zéro (None : toutes, et l'échéance). Sans la zone : rien."""
        a = self.pilote_addr()
        if a is None:
            return
        if i is None:
            self.mem.w_u32(a + OFFPI_ECHEANCE, 0)
            for k in range(N_ENEMY_SLOTS):
                self.mem.w_u32(a + OFFPI_TAB + k * PILOTE_ENTREE, 0)
        elif 0 <= int(i) < N_ENEMY_SLOTS:
            e = a + OFFPI_TAB + int(i) * PILOTE_ENTREE
            self.mem.w_u32(e, 0)
            self.mem.write(e + 0x04, bytes(PILOTE_ENTREE - 4))

    def pilote_counters(self) -> dict:
        """Mise au point : {"echeance", "echanges", "epinglages", "refus"} ; zéros sans la zone."""
        a = self.pilote_addr()
        if a is None:
            return {"echeance": 0, "echanges": 0, "epinglages": 0, "refus": 0}
        e, x, p, r = struct.unpack(">4I", self.mem.read(a + OFFPI_ECHEANCE, 16))
        return {"echeance": e, "echanges": x, "epinglages": p, "refus": r}

    @property
    def enemy_hp_free(self) -> bool:
        """Pack v19 dont les emplacements d'ennemis savent ne PAS imposer les PV (bit 2 des capacités). Sans lui,
        jamais de bit 0x02 dans +0x17 : un pack v19 plus ancien le prendrait pour « IA suspendue »."""
        return self.pack >= V19 and bool(self.caps & CAP_ENEMY_HP_FREE)

    @property
    def ordres(self) -> bool:
        """Pack v19 avec CALL 10 ORDRE (bit 3 des capacités, mode Hanteurs phases 3 et 4)."""
        return self.pack >= V19 and bool(self.caps & CAP_ORDRES)

    def ordre(self, proc_id: int, index: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 10 : force l'état de l'IA de l'acteur ``proc_id`` selon l'entrée ``index`` de la liste blanche du pack
        (synchro/ordres.py ORDRES), sur le fil du jeu, seulement s'il est « libre ». Résultat : 1 fait, 0 pas libre (rien
        écrit), -1 index ou espèce refusés, -3 événement en cours, -4 acteur introuvable ; MailboxCancelled sans
        réponse (neutralisé : rien n'est fait). MailboxError sans la capacité, ValueError hors bornes (rien n'est
        envoyé)."""
        if not self.ordres:
            raise MailboxError(f"graphic pack v{self.pack} sans les ordres du mode Hanteurs (CALL 10) : réinstalle la "
                               "v19 du dépôt (tools/install_graphicpack.py), puis relance le jeu")
        proc_id, index = int(proc_id), int(index)
        if not (0 < proc_id < 0xFFFFFFFF and 0 <= index < 0x100):
            raise ValueError("ordre : arguments hors bornes")
        if index >= ORDRES_BASE and not self.caps & CAP_FORMES:
            raise MailboxError("ordre étendu (formes spéciales) : le graphic pack installé ne le connaît pas "
                               "(bit 17 des capacités) ; réinstalle la v19 du dépôt")
        return self._v19(CMD_CALL, CALL_ORDRE, proc_id, index, timeout=timeout)

    @property
    def effets_pack(self) -> bool:
        """Pack v19 avec CALL 11 EFFET (bit 11 des capacités, effets propres aux Hanteurs)."""
        return self.pack >= V19 and bool(self.caps & CAP_EFFETS)

    def effet(self, recette: int, pos, son: bool = True, timeout: float = TIMEOUT_V19) -> int:
        """CALL 11 : joue la recette ``recette`` (synchro/effets_pack.py RECETTES, recopiée dans le pack) à ``pos``, sur le
        fil du jeu. ``son`` faux : particules seules. Résultat : >= 0 fait (bit 0 effet 1, bit 1 effet 2, bit 2 son ;
        0 si la réserve d'émetteurs est pleine), -1 recette ou gestionnaire d'effets refusés, -2 Link absent, -3
        événement en cours, -4 position refusée ; MailboxCancelled sans réponse (neutralisée : rien n'est fait).
        MailboxError sans la capacité, ValueError hors bornes (rien n'est envoyé)."""
        from synchro import effets_pack
        if not self.effets_pack:
            raise MailboxError(f"graphic pack v{self.pack} sans les effets du mode Hanteurs (CALL 11, bit 11) : "
                               "réinstalle la v19 du dépôt (tools/install_graphicpack.py), puis relance le jeu")
        if isinstance(recette, bool) or not isinstance(recette, int) or not 0 <= recette < effets_pack.N_RECETTES:
            raise ValueError("effet : recette hors table")
        if not effets_pack.position_ok(pos):
            raise ValueError("effet : position refusée")
        x, y, z = (float(v) for v in pos)
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(self.addr + OFF_CREATE_POS, struct.pack(">3f", x, y, z))
            return self._call(CMD_CALL, CALL_EFFET, recette, 0 if son else effets_pack.SANS_SON,
                              timeout=timeout, cancel=True)

    @property
    def barre_jeu(self) -> bool:
        """Pack v19 avec CALL 12 JAUGE (bit 14 des capacités, barre dans le jeu du mode Hanteurs)."""
        return self.pack >= V19 and bool(self.caps & CAP_BARRE_JEU)

    def jauge(self, air: int, maxi: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 12, poser : jauge circulaire du HUD (celle de la nage) à ``air`` images restantes sur ``maxi`` ; le HUD
        la décompte lui-même (une image à la fois) et la ferme à 0. Résultat : 1 prise, 2 rafraîchie (déjà à nous),
        -1 refusée, -2 Link absent, -3 événement en cours, -4 jauge prise par le jeu (nage) ; MailboxCancelled sans
        réponse (rien n'est fait). MailboxError sans la capacité, ValueError hors bornes (rien n'est envoyé)."""
        if not self.barre_jeu:
            raise MailboxError(f"graphic pack v{self.pack} sans la barre dans le jeu (CALL 12, bit 14) : réinstalle "
                               "la v19 du dépôt (tools/install_graphicpack.py), puis relance le jeu")
        if isinstance(air, bool) or isinstance(maxi, bool):
            raise ValueError("jauge : nombres d'images attendus")
        air, maxi = int(air), int(maxi)
        if not 1 <= air <= maxi <= JAUGE_MAX:
            raise ValueError("jauge : hors bornes (1 <= air <= max <= 899)")
        return self._v19(CMD_CALL, CALL_JAUGE, 1, air, maxi, timeout=timeout)

    def jauge_rendre(self, timeout: float = TIMEOUT_V19) -> int:
        """CALL 12, rendre : jauge remise à zéro si elle est encore à nous (0), sinon rien n'est écrit (-4)."""
        if not self.barre_jeu:
            raise MailboxError(f"graphic pack v{self.pack} sans la barre dans le jeu (CALL 12, bit 14)")
        return self._v19(CMD_CALL, CALL_JAUGE, 0, 0, 0, timeout=timeout)

    def u32(self, off: int) -> int:
        return self.mem.u32(self.addr + off)

    def version(self) -> int:
        return self.u32(OFF_VERSION)

    def frames(self) -> int:
        return self.u32(OFF_FRAMES)

    def frame_rate(self, seconds: float = 1.0) -> float:
        a, t = self.frames(), time.time()
        time.sleep(seconds)
        return (self.frames() - a) / (time.time() - t)

    def call(self, cmd: int, *args: int, timeout: float = 2.0) -> int:
        """Dépose une requête et attend que le jeu l'exécute. Renvoie le résultat."""
        with Mailbox._lock:
            return self._call(cmd, *args, timeout=timeout)

    def _check_idle(self) -> None:
        """Requête précédente encore en attente (menu pause, chargement) : refus AVANT toute
        écriture (sinon la position d'une création en attente serait écrasée)."""
        req, ack = self.u32(OFF_REQ), self.u32(OFF_ACK)
        if req != ack:
            raise MailboxError(f"requête {req} toujours en attente (ack {ack}) : le hook ne tourne pas ?")

    def _call(self, cmd: int, *args: int, timeout: float = 2.0, cancel: bool = False) -> int:
        self._check_idle()
        req = self.u32(OFF_REQ)
        a = (list(args) + [0, 0, 0, 0])[:4]
        self.mem.write(self.addr + OFF_CMD, struct.pack(">5I", cmd, *[x & 0xFFFFFFFF for x in a]))
        new = (req + 1) & 0xFFFFFFFF
        self.mem.w_u32(self.addr + OFF_REQ, new)       # en dernier : déclenche l'exécution
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.u32(OFF_ACK) == new:
                return struct.unpack(">i", self.mem.read(self.addr + OFF_RESULT, 4))[0]
            time.sleep(0.005)
        if cancel:
            # v19 : requête non idempotente (KEYADD, COUNT…) : neutralisée (commande 0 = rien, -6) pour
            # qu'elle ne s'exécute pas plus tard, quand le hook reprendra (menu pause). Si le hook l'avait
            # déjà lue, il finit en quelques µs : on laisse CANCEL_GRACE puis on relit l'accusé.
            self.mem.w_u32(self.addr + OFF_CMD, CMD_NOP)
            time.sleep(CANCEL_GRACE)
            if self.u32(OFF_ACK) == new:
                res = struct.unpack(">i", self.mem.read(self.addr + OFF_RESULT, 4))[0]
                if res != R_NOP:
                    return res
            raise MailboxCancelled(f"pas de réponse en {timeout} s : requête annulée (rien n'a été fait)")
        raise MailboxError(
            f"pas de réponse en {timeout} s. Le hook n'est appelé que quand Link est en jeu "
            "(pas au menu, pas pendant un chargement). La requête reste en attente.")

    def ping(self, value: int) -> int:
        return self.call(CMD_PING, value)

    def is_event(self, flag: int) -> bool:
        return bool(self.call(CMD_IS_EVENT, flag))

    def on_event(self, flag: int) -> None:
        self.call(CMD_ON_EVENT, flag)

    # --- acteurs (étape 4) ------------------------------------------------
    def require(self, version: int) -> None:
        if self.version() < version:
            raise MailboxError(f"graphic pack trop ancien (version {self.version()} < {version}) : "
                               "réinstalle-le (tools/install_graphicpack.py) et relance le jeu")

    def create(self, proc: int, params: int, pos, room: int, rot_y: int = 0, rot_z: int = 0,
               rot_x: int = 0) -> int | None:
        """fopAcM_create sur le thread du jeu. Renvoie l'id de process (création asynchrone).
        rot_z : angle.z passé au jeu (objet proc 255 : 0xFF = pas de switch de collision, comme
        fopAcM_createItem ; A4). rot_x : angle.x (synchro v2, coffres : daTbox lit sa salle dans
        angle.x & 0x3F et son objet dans angle.z ; la commande 4 v17 passe déjà l'angle complet,
        lu à +0x4C, docs/etudes/sync2_coffres.md §2.3)."""
        self.require(2)
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(self.addr + OFF_CREATE_POS, struct.pack(">3f3hxx", *pos, _s16(rot_x), _s16(rot_y),
                                                                   _s16(rot_z)))
            pid = self._call(CMD_CREATE, proc, params, room) & 0xFFFFFFFF
        return None if pid == ERROR_PROCESS_ID else pid

    def delete(self, proc_id: int) -> bool:
        self.require(2)
        return bool(self.call(CMD_DELETE, proc_id))

    def disappear(self, proc_id: int, pos, room: int, rot_y: int = 0, drop: int = DROP_NONE,
                  scale: int = 10, bit_no: int = 0xFF) -> bool:
        """Étape 5c : fumée de mort à `pos`, comme à la fin de la mort d'un ennemi.

        Pack v17 : fopAcM_createDisappear(acteur d'id `proc_id`, ...) : immédiat, et avec
        `drop` = DROP_TABLE l'objet lâché vient de la table de l'ennemi, comme en jeu.
        Pack plus ancien : création asynchrone du proc 400 (salle `room`) ; pas d'objet
        lâché possible (sa table d'objets n'est pas renseignée), le type est forcé à DROP_NONE.
        Renvoie True si la fumée a été demandée."""
        self.require(2)
        x, y, z = (float(v) for v in pos)
        new_pack = self.version() >= DISAPPEAR_MIN_VERSION
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(self.addr + OFF_CREATE_POS, struct.pack(">3f3hxx", x, y, z, 0, int(rot_y), 0))
            if new_pack:
                res = self._call(CMD_DISAPPEAR, proc_id, drop & 0xFF, scale & 0xFF, bit_no & 0xFF) & 0xFFFFFFFF
            else:
                params = (0xFF << 16) | ((scale & 0xFF) << 8) | DROP_NONE
                res = self._call(CMD_CREATE, PROC_DISAPPEAR, params, room) & 0xFFFFFFFF
        return res != ERROR_PROCESS_ID

    def slot_addr(self, i: int) -> int:
        """Emplacement de fantôme ``i`` (0..n_slots-1) : +0x60 jusqu'à la v18, +0x610 en v19."""
        if not 0 <= i < self.n_slots:
            raise IndexError(f"emplacement de fantôme {i} hors 0..{self.n_slots - 1} (pack v{self.pack})")
        return self.addr + slots_offset_for(self.pack) + i * SLOT_SIZE

    def set_slot(self, i: int, proc_id: int, pos, rot_y: int, active: bool = True, eye_y: float | None = None) -> None:
        """Le jeu recopie pos/rotY dans l'acteur à chaque frame (données seules, pas de requête). ``eye_y`` (A3,
        pack v19) : hauteur des yeux du fantôme (+0x1C, recopiée dans son eyePos) ; None = rien écrit (le jeu
        prend alors le y du fantôme si le mot vaut 0)."""
        a = self.slot_addr(i)
        self.mem.write(a + 0x04, struct.pack(">I3fh", proc_id, *pos, rot_y))
        if eye_y is not None and self.pack >= V19:
            self.mem.write(a + SLOT_EYE, struct.pack(">f", float(eye_y)))
        self.mem.w_u32(a, 1 if active else 0)

    def clear_slot(self, i: int) -> None:
        self.mem.w_u32(self.slot_addr(i), 0)
        if self.visee:
            # visée L/ZL (bit 20) : l'occupant suivant ne doit pas hériter des octets de visée (relecture D3)
            self.mem.write(self.slot_addr(i) + SLOT_VISEE, bytes(2))

    def slot_found(self, i: int) -> bool:
        return bool(self.mem.u32(self.slot_addr(i) + 0x18))

    # --- marionnettes d'ennemis (étape 5, graphic pack v7) ----------------
    def enemy_slot_addr(self, i: int) -> int:
        return self.addr + OFF_ENEMY_SLOTS + i * SLOT_SIZE

    def set_enemy_slot(self, i: int, proc_id: int, pos, rot_y: int, hp: int, suspend: bool = False,
                       hp_free: bool = False, pilote: bool = False) -> None:
        """Emplacement d'ennemi : le jeu impose position, orientation (et PV) à chaque image. ``hp_free`` (mode
        Hanteurs, laisse) : PV non imposés, seulement si le pack le sait (``enemy_hp_free``), sinon erreur.
        ``pilote`` (possession complète) : épinglage autour de l'execute du monstre, seulement avec la zone
        ``wwmp_pilote`` (jamais posé sur un pack sans le bit 16 : erreur), jamais avec ``suspend``."""
        if hp_free and not self.enemy_hp_free:
            raise MailboxError("PV libres des emplacements d'ennemis : pack v19 à jour requis (bit 2 des capacités)")
        if pilote and (suspend or not self.pilote):
            raise MailboxError("monstre piloté : pack v19 à jour requis (bit 16 des capacités), jamais avec l'IA "
                               "suspendue")
        a = self.enemy_slot_addr(i)
        flags = ((ENEMY_SUSPEND if suspend else 0) | (ENEMY_HP_FREE if hp_free else 0)
                 | (ENEMY_PILOTE if pilote else 0))
        self.mem.write(a + 0x04, struct.pack(">I3fhbB", proc_id, *pos, int(rot_y),
                                             max(-128, min(127, int(hp))), flags))
        self.mem.w_u32(a, 1)

    def set_enemy_slot_hp(self, i: int, hp: int) -> None:
        """PV imposés par l'emplacement ``i`` (un octet, +0x16)."""
        self.mem.write(self.enemy_slot_addr(i) + 0x16, struct.pack(">b", max(-128, min(127, int(hp)))))

    def clear_enemy_slot(self, i: int) -> None:
        self.mem.w_u32(self.enemy_slot_addr(i), 0)
        if getattr(self, "_etats", None) is not None:
            self.effacer_etat(i)            # mod natif 0.29.0 : l'état d'espèce part avec l'emplacement
        if self.visee:
            # visée L/ZL (bit 20) : le monstre suivant ne doit pas hériter du y visé (+0x1C) de celui-ci
            self.mem.write(self.enemy_slot_addr(i) + ENEMY_VISEE, bytes(4))

    def enemy_found(self, i: int) -> bool:
        return bool(self.mem.u32(self.enemy_slot_addr(i) + 0x18))

    # --- pack v19 : CALL en liste blanche, COUNT, KEYADD ------------------
    def require_v19(self) -> None:
        if self.pack < V19:
            raise MailboxError(f"graphic pack v{self.pack} : il faut la v19 (tools/install_graphicpack.py, "
                               "puis relancer le jeu)")

    def _v19(self, cmd: int, *args: int, timeout: float = TIMEOUT_V19) -> int:
        self.require_v19()
        with Mailbox._lock:
            return self._call(cmd, *args, timeout=timeout, cancel=True)

    def whitelist_call(self, index: int, a1: int = 0, a2: int = 0, a3: int = 0,
                       timeout: float = TIMEOUT_V19) -> int:
        """Commande 7 (CALL) brute : index 3 et 8 à 16 (15, 16 : mod natif) ; les autres : -1."""
        return self._v19(CMD_CALL, index, a1, a2, a3, timeout=timeout)

    def item_get(self, item: int, timeout: float = TIMEOUT_V19) -> int:
        """CALL 3 : ``execItemGet(item)`` sur le fil du jeu, hors événement. 1 = donné, -1 refusé
        (hors liste blanche, objet jamais donné), -2 gi nul, -3 événement en cours.
        ValueError avant tout envoi pour un objet que le pack refuserait. Point d'accroche
        ``InventaireSync.item_get``."""
        item = int(item)
        if item in NEVER_GIVE or item not in GIVE_OK:
            raise ValueError(f"objet 0x{item & 0xFFFF:02X} hors de la liste blanche du pack v19")
        return self.whitelist_call(CALL_ITEM_GET, item, timeout=timeout)

    @staticmethod
    def grant_sel(kind: int, idx: int, bit: int, need_stage: bool = False) -> int:
        """Sélecteur GRANT, borné comme dans l'asm (ValueError sinon)."""
        kind, idx, bit = int(kind), int(idx), int(bit)
        if kind in (GRANT_TBOX, GRANT_ITEM):
            ok = 0 <= idx < 16 and 0 <= bit < 32
        elif kind == GRANT_DUNGEON:
            ok = 0 <= idx < 16 and bit in (0, 1, 2, 4)
        elif kind == GRANT_OCEAN:
            ok = 0 <= idx < 0x32 and 0 <= bit < 16
        elif kind == GRANT_EVENT:
            ok = 0 <= idx < 0x100 and 1 <= bit <= 0xFF     # octet de mEvent, MASQUE entier
        else:
            ok = False
        if not ok:
            raise ValueError(f"GRANT : genre {kind}, index {idx}, bit {bit} hors bornes")
        return (1 << 24 if need_stage else 0) | kind << 16 | idx << 8 | bit

    def grant(self, kind: int, idx: int, bit: int, item: int = ITEM_NONE, count: int = 1,
              need_stage: bool = False, timeout: float = TIMEOUT_V19) -> int:
        """CALL 8 GRANT : pose le bit (sauvegarde, et copie vivante si c'est la stage chargée) PUIS donne
        l'objet (``count`` appels à ``execItemGet``, 1..4), dans la même image, hors événement. ``item``
        0xFF = bit seul. Résultat : 1 posé et donné, 0 déjà posé (rien fait), -1 refusé, -3 occupé (aussi
        pour un gi nul, transitoire), -4 ``need_stage`` et autre stage. Points d'accroche
        ``ChestSync.granter`` et ``GiftSync.granter``. ValueError avant tout envoi pour des arguments que
        le pack refuserait. Sans réponse : MailboxCancelled (requête neutralisée, rien n'est fait)."""
        sel = self.grant_sel(kind, idx, bit, need_stage)
        item, count = int(item), int(count)
        if item != ITEM_NONE and (not 0 <= item < ITEM_NONE or item in NEVER_GIVE):
            raise ValueError(f"GRANT : objet 0x{item & 0xFFFF:02X} jamais donné par le pack")
        if not 1 <= count <= 4:
            raise ValueError(f"GRANT : nombre d'appels {count} hors 1..4")
        res = self.whitelist_call(CALL_GRANT, sel, item, count, timeout=timeout)
        return R_BUSY if res == R_NO_GI else res

    def key_add(self, delta: int, slot: int = -1, timeout: float = TIMEOUT_V19) -> bool:
        """Commande 10 KEYADD : ``mItemKeyNumCount += delta`` sur le fil du jeu, si aucun événement et si
        ``mDan.mStageNo == slot`` (-1 : pas de contrôle). True = ajouté ; False = refusé par le jeu
        (événement, autre stage) ou annulé faute de réponse (jamais exécuté plus tard). Point d'accroche
        ``KeySync.key_add``. ValueError avant tout envoi pour un delta ou un slot hors bornes."""
        delta, slot = int(delta), int(slot)
        if not 1 <= abs(delta) <= KEYADD_MAX or not (slot == -1 or 0 <= slot < 0x80):
            raise ValueError(f"KEYADD : delta {delta} ou slot {slot} hors bornes")
        try:
            res = self._v19(CMD_KEYADD, delta, slot, timeout=timeout)
        except MailboxCancelled:
            return False
        if res == R_DONE:
            return True
        if res in (R_BUSY, R_STAGE, R_NO_GI):
            return False
        raise MailboxError(f"KEYADD refusé par le pack (code {res})")

    def count(self, counter, delta: int, timeout: float = TIMEOUT_V19) -> int | None:
        """Commande 9 COUNT : ``delta`` ajouté au compteur en attente ``counter`` (nom de ``COUNTERS`` ou
        index), appliqué par le jeu aux images suivantes. Renvoie la nouvelle valeur en attente, ou None si
        le jeu refuse (événement en cours) ; ValueError avant tout envoi hors bornes. Pas encore branché :
        les compteurs communs sont la phase suivante de sync2_inventaire.md."""
        idx = COUNTER_INDEX[counter] if isinstance(counter, str) else int(counter)
        if not 0 <= idx < len(COUNTERS):
            raise ValueError(f"COUNT : compteur {counter!r} inconnu")
        name, _off, _t, lo, hi = COUNTERS[idx]
        delta = int(delta)
        if not lo <= delta <= hi:
            raise ValueError(f"COUNT : delta {delta} hors {lo}..{hi} pour {name}")
        res = self._v19(CMD_COUNT, idx, delta, timeout=timeout) & 0xFFFFFFFF
        if res in (COUNT_REFUSED, COUNT_BUSY):
            return None
        return res - (1 << 32) if res & 0x80000000 else res

    # --- pack v19 : fantôme touchable (A1/A2) -------------------------------
    def require_ghost_hits(self) -> None:
        if not self.ghost_hits:
            raise MailboxError(f"graphic pack v{self.pack} sans fantôme touchable : réinstalle la v19 du dépôt "
                               "(tools/install_graphicpack.py), puis relance le jeu")

    def set_ghost_tg(self, on: bool) -> None:
        """A1 : TG des fantômes inscrit (True) ou non (False, défaut du pack) à chaque image. Données seules."""
        self.require_ghost_hits()
        self.mem.w_u32(self.addr + OFF_HITS, HITS_CFG_TG if on else 0)

    def ghost_tg_on(self) -> bool:
        return self.ghost_hits and bool(self.u32(OFF_HITS) & HITS_CFG_TG)

    @staticmethod
    def parse_ghost_hits(raw: bytes) -> tuple[int, list[dict]]:
        """(seq, entrées) d'une image de la zone +0x16A0 (``HITS_SPAN`` octets)."""
        seq = struct.unpack_from(">I", raw, 4)[0]                 # +0x16A4
        out = []
        for i in range(N_HITQ):
            v = struct.unpack_from(_HIT_FMT, raw, OFF_HITQ - OFF_HITS + i * HITQ_SIZE)
            if v[0] == 0:
                continue
            out.append({"seq": v[0], "ghost": v[1], "attacker": v[2], "type": v[3], "atp": v[4], "spl": v[5],
                        "se": v[6], "mark": v[7], "gat": v[8], "dmg": v[9], "mtrl": v[10],
                        "vec": [v[12], v[13], v[14]], "pos": [v[15], v[16], v[17]], "frame": v[18]})
        return seq, out

    def read_ghost_hits(self) -> tuple[int, list[dict]]:
        """A1 : file des coups reçus par nos fantômes. Lue deux fois : une entrée n'est gardée que si les deux
        lectures concordent (le jeu peut l'écrire pendant la lecture ; il écrit son seq en dernier)."""
        self.require_ghost_hits()
        a = self.mem.read(self.addr + OFF_HITS, HITS_SPAN)
        b = self.mem.read(self.addr + OFF_HITS, HITS_SPAN)
        sa, ea = self.parse_ghost_hits(a)
        sb, eb = self.parse_ghost_hits(b)
        same = {e["seq"]: e for e in eb}
        return min(sa, sb), [e for e in ea if same.get(e["seq"]) == e]

    def ghost_hit_seq(self) -> int:
        """A1 (lot L1 de la latence) : séquence de la file des coups seule (+0x16A4, un mot, écrit en dernier par le
        jeu), lue à chaque image par le fil des fantômes ; la file entière n'est relue que si elle a bougé."""
        self.require_ghost_hits()
        return self.u32(OFF_HITS + 4)

    def hit_counters(self) -> dict:
        """Compteurs de mise au point de la zone A1 (cfg, coups poussés, inscriptions du TG, ignorés)."""
        cfg, seq, sets, bad = struct.unpack(">4I", self.mem.read(self.addr + OFF_HITS, 16))
        return {"cfg": cfg, "seq": seq, "sets": sets, "ignored": bad}

    def coup_link(self, owner_pid: int, at_type: int, atp: int, spl: int = 0, se: int = 0, mark: int = 0,
                  gat: int = 0, mtrl: int = 0, vec=(0.0, 0.0, 0.0), timeout: float = TIMEOUT_V19) -> int:
        """A2, CALL 9 : rejoue sur Link, pendant une image, le coup reçu par notre fantôme chez l'autorité.
        ``owner_pid`` = id de process de la marionnette locale de l'ennemi (propriétaire de l'attaque). Le jeu
        applique garde, dégâts, recul, invincibilité, fée et game over (changeDamageProc). Résultat : 1 inscrit,
        -1 refusé, -2 Link absent ou partenaire contrôlé, -3 événement en cours, -4 propriétaire introuvable ;
        MailboxCancelled sans réponse (neutralisé : rien n'est fait). ValueError avant tout envoi hors bornes."""
        self.require_ghost_hits()
        owner_pid, at_type, atp, spl = int(owner_pid), int(at_type), int(atp), int(spl)
        se, mark, gat, mtrl = int(se), int(mark), int(gat), int(mtrl)
        vec = [float(v) for v in vec]
        if not (0 < owner_pid < 0xFFFFFFFF and 0 < at_type <= 0xFFFFFFFF and 0 <= atp <= COUP_ATP_MAX
                and 0 <= spl <= COUP_SPL_MAX and 0 <= se <= 0xFF and 0 <= mark <= 0xFF and 0 <= mtrl <= 0xFF
                and 0 <= gat <= 0xFFFFFFFF and len(vec) == 3 and all(abs(v) < 1e6 for v in vec)):
            raise ValueError("coup_link : arguments hors bornes")
        buf = struct.pack(">IIBBBBIB3x3f", owner_pid, at_type, atp, spl, se, mark, gat, mtrl, *vec)
        with Mailbox._lock:
            self._check_idle()
            self.mem.write(self.addr + OFF_COUP, buf)
            return self._call(CMD_CALL, CALL_COUP_LINK, timeout=timeout, cancel=True)

    # --- pack v19 : monstres qui visent le fantôme (A3) --------------------
    def require_ghost_aggro(self) -> None:
        if not self.ghost_aggro:
            raise MailboxError(f"graphic pack v{self.pack} sans « monstres qui visent le fantôme » : réinstalle la v19 du "
                               "dépôt (tools/install_graphicpack.py), puis relance le jeu")

    @staticmethod
    def pack_aggro(entries) -> bytes:
        """Table des cibles (``N_AGGRO`` × 8 octets) : ``entries`` = [(id de l'ennemi, id du fantôme)], au plus
        ``N_AGGRO`` ; ValueError pour un id nul ou hors 32 bits, ou un ennemi en double."""
        entries = list(entries)
        if len(entries) > N_AGGRO:
            raise ValueError(f"table des cibles : {len(entries)} entrées > {N_AGGRO}")
        seen = set()
        raw = b""
        for enemy, ghost in entries:
            enemy, ghost = int(enemy), int(ghost)
            if not (0 < enemy < 0xFFFFFFFF and 0 < ghost < 0xFFFFFFFF) or enemy in seen:
                raise ValueError(f"table des cibles : entrée ({enemy:#x}, {ghost:#x}) refusée")
            seen.add(enemy)
            raw += struct.pack(">II", enemy, ghost)
        return raw + bytes(8 * N_AGGRO - len(raw))

    def set_aggro(self, on: bool, entries=()) -> None:
        """A3 : table des cibles puis cfg (données seules). Coupé : cfg à 0 d'abord, puis table vidée."""
        self.require_ghost_aggro()
        raw = self.pack_aggro(entries if on else ())
        if on:
            self.mem.write(self.addr + OFF_AGGRO_TAB, raw)
            self.mem.w_u32(self.addr + OFF_AGGRO, AGGRO_CFG_ON)
        else:
            self.mem.w_u32(self.addr + OFF_AGGRO, 0)
            self.mem.write(self.addr + OFF_AGGRO_TAB, raw)

    def aggro_counters(self) -> dict:
        """Compteurs de mise au point de A3 (cfg, échanges faits, refus ; ``seq`` : séquence brute)."""
        cfg, seq, refused = struct.unpack(">3I", self.mem.read(self.addr + OFF_AGGRO, 12))
        return {"cfg": cfg, "swaps": seq // 2, "refused": refused, "seq": seq}

    def read_stable(self, read, tries: int = 6):
        """Relecture adverse de A3 : ``read()`` (lecture de Link) hors d'un échange de position. Le PC lit la
        mémoire pendant que le jeu tourne : pendant l'execute d'un ennemi de la table, old.pos, current.pos et
        eyePos de Link sont ceux du fantôme. La séquence du jeu (``OFF_AGGRO_SEQ``) est impaire pendant
        l'échange et augmente de 2 par échange : lecture gardée seulement si la séquence est paire et n'a pas
        bougé. Renvoie (valeur, True), ou (dernière valeur lue ou None, False) après ``tries`` essais. Sans la
        capacité : (read(), True)."""
        if not self.ghost_aggro:
            return read(), True
        val = None
        a = self.addr + OFF_AGGRO_SEQ
        for _ in range(max(1, int(tries))):
            s1 = self.mem.u32(a)
            if s1 & 1:
                continue
            val = read()
            if self.mem.u32(a) == s1:
                return val, True
        return val, False
