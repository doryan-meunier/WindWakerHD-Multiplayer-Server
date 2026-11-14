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
        """Commande 7 (CALL) brute : seuls les index 3, 8, 9, 10, 11, 12 et 13 sont codés (les autres renvoient -1)."""
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
