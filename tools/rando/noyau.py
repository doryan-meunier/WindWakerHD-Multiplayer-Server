"""Chantier E, palier 1 « sûr » du randomizer coop (docs/etudes/sync3_randomizer.md, recommandation C, §4 et §7).

Ce palier NE PEUT PAS BLOQUER LA PARTIE : seuls des objets sans valeur de progression changent de coffre (rubis,
quarts de cœur, fioles de magie, recharges de bombes et de flèches, cartes au trésor numérotées). Jamais : objet
de progression ou d'inventaire, petite clé, grande clé, carte ou boussole de donjon, réceptacle 0x08, objet
progressif (bourse, sacs, carquois, magie), fragment ou carte de la Triforce, cartes spéciales (Vaisseau
Fantôme, Tingle, sous-marins…), statues de Tingle, butin. Chaque objet reste obtenable UNE fois (permutation des
objets du pool entre les coffres du pool), et le contenu est identique pour tous (table calculée par le serveur,
empreinte vérifiée par chaque client).

* Données : ``rando_coffres.json`` (outils/rando_map.py, relu dans le dump : clé runtime (index de sauvegarde,
  tboxNo) et objet vanilla de chaque coffre ; noms des lieux : TWWHD-Randomizer, MIT). Un coffre « en conflit »
  (même clé ailleurs avec un autre objet) n'entre jamais dans le pool.
* Tirage déterministe : graine effective = sha256(ALGO | version des données | graine) ; flux sha256 en mode
  compteur ; Fisher-Yates sur le pool trié par clé (aucune dépendance à ``random``, à PYTHONHASHSEED ni à la
  version de Python).
* Application (pack v19, bit 5 des capacités, zone « WWMPRND1 » en fin de pack) : stub N1 à la création du coffre
  (0x024B62C8, ``lha r12,0x2f8(r31)`` réexécuté) qui remplace l'octet fort de home.angle.z (+0x2FC, relu par
  actionOpenWait 0x024B73D0) SEULEMENT si l'octet vaut l'objet d'origine attendu ET si les deux objets sont dans
  la liste permise du pack (``PERMIS``, recopiée dans l'asm). Aucun effet tant que la table n'est pas chargée
  (état 1, écrit en dernier par le PC).
* Filet Python (``--rando-filet``, DÉSACTIVÉ par défaut) : coffre déjà créé avant le chargement de la table
  (course au démarrage) ; voir ``FiletCoffres``.

Le spoiler n'est jamais envoyé aux clients : le serveur l'écrit dans un fichier (``ecrire_spoiler``). La table en
mémoire de chaque joueur reste lisible par qui fouille la RAM (acceptable en coop, documenté).
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import threading
import time

ALGO = "wwmp-rando-palier1-v1"
FORMAT = 1
PALIER = 1
FICHIER_DONNEES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coffres.json")
GRAINE_MAX = 64
N_IDX, N_TBOX = 16, 32
TABLE_OCTETS = N_IDX * N_TBOX * 2          # (objet d'origine, nouvel objet) par clé ; 0xFF 0xFF = rien
VIDE = 0xFF
POOL_MAX = 256

# --- objets qui peuvent changer de coffre (recopiés dans l'asm : wwmp_rnd_permis, comparés par test_rando) ----------
RUBIS = frozenset({0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x0F})
QUART = 0x07
MAGIE = frozenset({0x09, 0x0A})
RECHARGES = frozenset({0x0B, 0x0C, 0x0D, 0x0E, 0x10, 0x11, 0x12})
# cartes au trésor numérotées (item_data.yaml de TWWHD : « Treasure Chart N ») ; jamais 0xC2-0xCB (cartes
# spéciales), 0xDB (Vaisseau Fantôme), 0xDC (Tingle), 0xFA / 0xFC / 0xFE (cartes de la Triforce)
CARTES_TRESOR = {0xCC: 7, 0xCD: 27, 0xCE: 21, 0xCF: 13, 0xD0: 32, 0xD1: 19, 0xD2: 41, 0xD3: 26, 0xD4: 8, 0xD5: 37,
                 0xD6: 25, 0xD7: 17, 0xD8: 36, 0xD9: 22, 0xDA: 9, 0xDD: 14, 0xDE: 10, 0xDF: 40, 0xE0: 3, 0xE1: 4,
                 0xE2: 28, 0xE3: 16, 0xE4: 18, 0xE5: 34, 0xE6: 29, 0xE7: 1, 0xE8: 35, 0xE9: 12, 0xEA: 6, 0xEB: 24,
                 0xEC: 39, 0xED: 38, 0xEE: 2, 0xEF: 33, 0xF0: 31, 0xF1: 23, 0xF2: 5, 0xF3: 20, 0xF4: 30, 0xF5: 15,
                 0xF6: 11, 0xF7: 46, 0xF8: 45, 0xF9: 44, 0xFB: 43, 0xFD: 42}
PERMIS = frozenset(RUBIS | {QUART} | MAGIE | RECHARGES | set(CARTES_TRESOR))
# documentation et tests : ce qui ne doit JAMAIS entrer dans PERMIS (liste non exhaustive : PERMIS est une
# liste blanche, tout le reste est refusé)
JAMAIS = frozenset({0x00, 0x08, 0x15, 0x16, 0x1F, 0x4C, 0x4D, 0x4E, 0xFF} | set(range(0x20, 0x4C))
                   | set(range(0x50, 0x5A)) | set(range(0x61, 0x69)) | set(range(0xA3, 0xC2))
                   | set(range(0xC2, 0xCC)) | {0xDB, 0xDC, 0xFA, 0xFC, 0xFE})

# Randomizer complet, étape 2 (coffres avec progression ; docs/etudes/rando_complet.md §6.3). Liste blanche du MODE
# COMPLET (état 2 du pack, bit 6), recopiée dans l'asm (wwmp_rnd2_permis) : exactement les objets que le jeu d'origine
# met déjà dans un lieu mélangeable (tests : égale aux objets des lieux « applicables » de rando_logique : coffres et,
# depuis l'étape 3, cadeaux de personnages, qui n'ajoutent que des cartes au trésor numérotées). Donc chaque objet est
# donné par un coffre ou une démo de cadeau comme dans le jeu d'origine (même item_func). Jamais : 0x00, 0x08
# (réceptacle), 0x36 (flèches de lumière : case de l'arc), 0xFF, objet à paliers (épée, bouclier, bourse, sacs,
# carquois, magie, voile), objet absent des coffres du jeu d'origine.
PETITE_CLE, GRANDE_CLE, CARTE_DONJON, BOUSSOLE = 0x15, 0x4E, 0x4C, 0x4D
OBJETS_DE_DONJON = frozenset({PETITE_CLE, GRANDE_CLE, CARTE_DONJON, BOUSSOLE})
PERMIS2 = frozenset({0x03, 0x04, 0x05, 0x06, 0x07, 0x0F,              # rubis, quart de cœur
                     PETITE_CLE, GRANDE_CLE, CARTE_DONJON, BOUSSOLE,  # objets de donjon (jamais hors de leur donjon)
                     0x1F, 0x45, 0x46, 0x47, 0x48,                    # butin
                     0x23, 0x27, 0x28, 0x29, 0x33, 0x43, 0x50,        # boîte à images, arc, bracelets, bottes, masse,
                                                                      # amulette du héros, flacon
                     0x62, 0x64, 0x66, 0x68,                          # fragments de la Triforce (coffres)
                     0xA3, 0xA4, 0xA5, 0xA6, 0xA7,                    # statues de Tingle
                     0xC2, 0xC4, 0xDB, 0xFC}                          # cartes spéciales, carte de la Triforce 2
                    | set(range(0xCD, 0xD7)) | {0xD8, 0xD9, 0xDA, 0xDD, 0xDE, 0xDF, 0xE0, 0xE2, 0xE3, 0xE6, 0xE7, 0xE8,
                                                0xE9, 0xEA, 0xEC} | set(range(0xF2, 0xF8)) | {0xF9, 0xFB}
                    # étape 3 : cartes au trésor données par un personnage ou gagnées (cadeaux N2)
                    | {0xD7, 0xE4, 0xE5, 0xEB, 0xED, 0xEF, 0xF0})
JAMAIS2 = frozenset({0x00, 0x08, 0x36, 0xFF})        # jamais dans PERMIS2, quelle que soit l'évolution des données
ALGO2, FORMAT2, ETAT_SUR, ETAT_COMPLET = "wwmp-rando-v2", 2, 1, 2

# Randomizer complet, étape 3 (docs/etudes/rando_complet.md §6.4) : cadeaux de personnages et prix, donnés par
# ``fopAcM_createItemForPresentDemo`` (0x025D7DEC). Le stub N2 du pack (bit 7, accroche 0x025D7DFC) y remplace l'objet
# quand (adresse de retour de l'appel, objet d'origine) est dans la table chargée par le PC, et seulement entre deux
# objets de la liste du pack. Liste blanche à NOUS : l'index dans ce tuple est le « site » des entrées de type 1 de la
# table ; un message ne porte jamais d'adresse. Chaque site est relu dans le RPX par les tests (``bl 0x025D7DEC`` juste
# avant l'adresse de retour, objet d'origine dans la constante ou la table du jeu). Deux sites peuvent partager un
# appel s'ils n'ont pas le même objet d'origine ; jamais deux sites avec le même couple.
TYPE_COFFRE, TYPE_N2 = 0, 1
SITES_N2 = (
    (0x02298C18, 0x07, "Windfall Island - Ivan Catch Killer Bees"),
    (0x0227C2F8, 0x07, "Windfall Island - Maggie Delivery Reward"),
    (0x02269008, 0x07, "Spectacle Island - Barrel Shooting First Prize"),
    (0x02269008, 0xD7, "Spectacle Island - Barrel Shooting Second Prize"),
    (0x022E5FB4, 0xE5, "Great Sea - Salvage Corp Gift"),
    (0x022C103C, 0xEB, "Windfall Island - Pompie & Vera Secret Meeting Photo"),
    (0x022C103C, 0xEF, "Windfall Island - Minenco Miss Windfall Picture"),
    (0x022C103C, 0xF0, "Windfall Island - Kamo Full Moon Picture"),
    (0x02059AF0, 0x1F, "Windfall Island - Auction 40 Rupee"),
    (0x02059AF0, 0xE4, "Windfall Island - Auction 5 Rupee"),
    (0x02059AF0, 0xED, "Windfall Island - Auction 60 Rupee"),
    (0x02059AF0, 0x07, "Windfall Island - Auction 80 Rupee"),
    (0x022D4688, 0x07, "Great Sea - Goron Trading Reward"),
)
N2_MAX = 32                                  # entrées de la zone wwmp_rnd3 du pack

# Portage TWWHD, lot 1 (docs/etudes/portage_twwhd.md) : lieux dont l'objet est une DONNÉE du jeu (un octet d'une table
# du programme), changée par le client en mémoire : courrier, boutique de Terry à l'Île du Pic, enchères, personnages
# « à table » de Mercantîle. Adresses et objets d'origine repris de TWWHD-Randomizer (logic/data/location_data.yaml,
# Copyright (c) SuperDude88, licence MIT : tools/rando/logique/LICENSE_TWWHD.txt), puis RELUS UN PAR UN dans le
# RPX USA v0 par les tests. L'index dans ce tuple est le « site » des entrées de type 2 de la table ; un message ne
# porte jamais d'adresse. Par site : groupe (son option : OPTION_GROUPE), nom de la logique, objet d'origine,
# adresses de l'octet d'objet (deux pour la lettre de la mère de Baito : le jeu l'a en double), octets
# d'accompagnement (adresse, valeur d'origine, valeur écrite quand l'objet change), objets INTERDITS à cet endroit.
#   - boutique : le jeu d'origine marque « épuisé » d'après le NUMÉRO de l'objet (flacon, quart de cœur, cartes de
#     Terry) : un tel objet posé à un autre étal y paraîtrait épuisé trop tôt ou jamais ; l'étal à 950 rubis porte en
#     plus le bit 0x02 « objet unique », retiré (comme TWWHD) quand son objet change ;
#   - personnages à table : même appel que les trois photos (sites N2 5 à 7) ; leur objet d'origine posé ici serait
#     re-remplacé par le stub N2 du pack.
TYPE_DONNEES = 2
OPTION_GROUPE = {"courrier": "lieux_courrier", "boutiques": "lieux_boutiques", "encheres": "lieux_encheres",
                 "pnj": "lieux_pnj",
                 # randomizer par fichiers, étape 5 (rando/lot9.py) : mêmes écritures gardées du client
                 "kane": "lieux_kane", "courrier2": "courrier_tout", "enchere100": "lieux_encheres_100"}
_INT_BOUTIQUE = frozenset({0x50, 0x07, 0xE1, 0x2C})
_INT_PNJ = frozenset({0xEB, 0xEF, 0xF0})
SITES_DONNEES = (
    ("courrier", "Mailbox - Letter from Baito's Mother", 0x07, (0x1003150C, 0x1003156C), (), frozenset()),
    ("courrier", "Mailbox - Letter from Komali's Father", 0x07, (0x10031518,), (), frozenset()),
    ("courrier", "Mailbox - Letter from Orca", 0x04, (0x10031530,), (), frozenset()),
    ("courrier", "Mailbox - Letter from Grandma", 0x04, (0x1003153C,), (), frozenset()),
    ("courrier", "Mailbox - Letter from Aryll", 0x04, (0x10031560,), (), frozenset()),
    ("courrier", "Mailbox - Letter from Baito", 0x04, (0x10031590,), (), frozenset()),
    ("boutiques", "Rock Spire Isle - Beedle 500 Rupee Item", 0x50, (0x101EAF57,), (), _INT_BOUTIQUE),
    ("boutiques", "Rock Spire Isle - Beedle 950 Rupee Item", 0x07, (0x101EAF67,), ((0x101EAF70, 0x22, 0x20),),
     _INT_BOUTIQUE),
    ("encheres", "Windfall Island - Auction 40 Rupee", 0x1F, (0x1019016D,), (), frozenset()),
    ("encheres", "Windfall Island - Auction 5 Rupee", 0xE4, (0x10190175,), (), frozenset()),
    ("encheres", "Windfall Island - Auction 60 Rupee", 0xED, (0x1019017D,), (), frozenset()),
    ("encheres", "Windfall Island - Auction 80 Rupee", 0x07, (0x10190185,), (), frozenset()),
    ("pnj", "Windfall Island - Sam Decorate Island", 0x07, (0x101C399B,), (), _INT_PNJ),
    ("pnj", "Windfall Island - Linda and Anton", 0x07, (0x101C39A7,), (), _INT_PNJ),
    ("pnj", "Windfall Island - Kreeb Light the Lighthouse", 0x07, (0x101C39AB,), (), _INT_PNJ),
    # --- étape 5 du randomizer par fichiers (docs/etudes/rando_fichiers.md, rando/lot9.py) : AJOUTÉS EN FIN
    # (les sites 0 à 14 gardent leur numéro : une table d'avant se relit). Repris de TWWHD (location_data.yaml,
    # Copyright (c) SuperDude88, MIT), relus dans le RPX USA v0 par tools/tests/test_rando_lot9.py.
    # Kane (« Joe ») : table de quatre mots 0x101C3870 lue par 0x022C639C, objet donné par createItemForPresentDemo
    # (retour 0x022C1068 : ce n'est PAS un site du stub N2, aucun objet interdit)
    ("kane", "Windfall Island - Kane Decorate Shop Guru", 0x05, (0x101C3873,), (), frozenset()),
    ("kane", "Windfall Island - Kane Decorate Postman Statue", 0x05, (0x101C3877,), (), frozenset()),
    ("kane", "Windfall Island - Kane Decorate Six Flags", 0x04, (0x101C387B,), (), frozenset()),
    ("kane", "Windfall Island - Kane Decorate Six Idols", 0x04, (0x101C387F,), (), frozenset()),
    # les six autres lettres (même table que le lot 1 ; objets hors de PERMIS2 : ils ne vont que dans un lieu
    # « donnée », voir rando_lot9.OBJETS_DON_SEULS)
    ("courrier2", "Mailbox - Letter Advertising Bombs", 0xC3, (0x10031524,), (), frozenset()),
    ("courrier2", "Mailbox - Letter Advertising Rock Spire Shop Ship", 0x01, (0x10031548,), (), frozenset()),
    ("courrier2", "Mailbox - Letter from Tingle", 0xCB, (0x10031554,), (), frozenset()),
    ("courrier2", "Mailbox - Beedle's Silver Membership Reward", 0x9D, (0x10031578,), (), frozenset()),
    ("courrier2", "Mailbox - Letter from Hoskit's Girlfriend", 0x3F, (0x10031584,), (), frozenset()),
    ("courrier2", "Mailbox - Beedle's Gold Membership Reward", 0x9E, (0x1003159C,), (), frozenset()),
    # enchère à 100 rubis : voile rapide. Le jeu ne la marque « gagnée » que par la voile possédée
    # [HD-ASM 0x0205A608 : champ +6 nul et objet 0x77 -> octet 0x5D de l'inventaire] ; un autre objet s'y gagnerait
    # sans fin. Comme TWWHD (flexible_item_locations.asm), le champ +6 reçoit le bit inutilisé 0x6B01 quand l'objet
    # change. Objets interdits : ceux des quatre autres lots (même appel 0x02059AF0 que les sites N2 des enchères :
    # posés ici, le stub N2 les remplacerait par l'objet de leur propre lot)
    ("enchere100", "Windfall Island - Auction 100 Rupee", 0x77, (0x1019018D,),
     ((0x10190192, 0x00, 0x6B), (0x10190193, 0x00, 0x01)), frozenset({0x1F, 0xE4, 0xED, 0x07})),
)
N_DONNEES_LOT1 = 15                          # sites 0 à 14 : lot 1 du portage ; au-delà : étape 5
GROUPES_LOT9 = frozenset({"kane", "courrier2", "enchere100"})
# objets des lieux de l'étape 5 qui ne sont dans aucune liste du pack (lettres : carte de Beedle, rubis vert, super
# carte, ticket flatteur, quart de cœur « 2 », ticket de plein ; enchère : voile rapide). Mélangés, ils ne vont que
# dans un lieu « donnée » (octet écrit par le client : aucune liste blanche du pack n'intervient)
OBJETS_DON_SEULS = frozenset(s[2] for s in SITES_DONNEES if s[0] in GROUPES_LOT9) - PERMIS2
# Portage TWWHD, lot 2 (docs/etudes/portage_twwhd.md) : coffres « en conflit ». Deux coffres de deux NIVEAUX différents
# y partagent la même clé (index de sauvegarde, numéro de coffre) : la table du stub N1 ne les distingue pas, ils
# gardaient donc leur objet. Le pack sait maintenant les reconnaître par (condensé du nom de niveau, clé) : zone
# « WWMPRND4 », au plus 16 entrées. L'index dans ce tuple est le « site » des entrées de type 3 de la table ; un message
# ne porte jamais de nom de niveau. Par site : niveau du coffre, index de sauvegarde, numéro de coffre, objet d'origine,
# nom de la logique. Relevé : tools/rando/coffres.json (dump du jeu), relu par les tests. Rien n'est repris de
# TWWHD ici (il réécrit les fichiers de niveau, nous non). Le coffre du bouclier miroir (« Earth Temple - Stalfos Mini
# Boss », M_DaiMB, clé (6, 7)) est en conflit lui aussi mais RESTE FIXE : deux corrections de portes du scénario
# (scenario_vanilla.json, « suppose_fixes ») comptent sur lui, et le bouclier est un objet à paliers (hors liste).
TYPE_CONFLIT = 3
SITES_CONFLIT = (
    ("kinMB", 4, 10, 0x2D, "Forbidden Woods - Mothula Mini Boss Chest"),
    ("kaze", 7, 0, 0x06, "Wind Temple - Between Dirt Patches Chest"),
    ("kazeMB", 7, 5, 0x2F, "Wind Temple - Wizzrobe Mini Boss"),
    ("kaze", 7, 11, 0x4C, "Wind Temple - Cyclones Room Chest"),
    ("kaze", 7, 13, 0x4D, "Wind Temple - Hub Room Center Chest"),
    ("ShipD", 12, 4, 0xFE, "Islet of Steel - Interior Chest"),
    ("Cave03", 12, 7, 0x1F, "Cliff Plateau Isles - Cave Chest"),
    ("TF_01", 12, 10, 0x67, "Stone Watcher Island - Cave Chest"),
    ("TF_06", 13, 0, 0x05, "Dragon Roost Island - Cave Chest"),
    ("Asoko", 13, 5, 0x31, "Windfall Island - Pirate Ship Chest"),
)
CONFLIT_MAX = 16                             # entrées de la zone wwmp_rnd4 du pack
# objets que seuls ces coffres portent (absents de PERMIS2) : boomerang, grappin-griffe, bombes, fragment 7 de la
# Triforce, carte de la Triforce 1. Ils ne vont QUE dans un coffre (stub N1 avec la liste wwmp_rnd4_permis) : jamais
# chez un personnage, dans une lettre, une boutique ou une enchère (le stub N2 et les écritures de données gardent
# PERMIS2). PERMIS4 est recopiée dans l'asm (wwmp_rnd4_permis).
OBJETS_CONFLIT = frozenset({0x2D, 0x2F, 0x31, 0x67, 0xFE})
PERMIS4 = PERMIS2 | OBJETS_CONFLIT

# Portage TWWHD, lot 4 (option « progressifs ») : objets progressifs. Une famille est une suite de paliers (objets du
# jeu, du premier au dernier) ; les coffres qui en portent un sont ses « lieux progressifs ». Le Ne lieu ouvert PAR LE
# GROUPE (compte tenu par le serveur, par lieu : rien de perdu ni de doublé) donne le palier N, quel que soit le coffre.
# Le pack remplace l'objet à la création du coffre par ``carte[objet]`` (zone wwmp_prg, tenue à jour par le client).
# Seule famille aujourd'hui : l'arc (arc du héros 0x27, puis flèches de lumière 0x36 ; les flèches de feu et de glace,
# 0x35, restent le don de la Reine des fées). Les autres familles de TWWHD (épée, bouclier, bourse, carquois, sac de
# bombes, magie, voile, boîte à images) n'ont pas deux paliers dans des coffres : leurs lieux sont des dons de
# l'histoire ou des grandes fées, pas encore mélangés. Un palier ne va QUE dans un coffre ordinaire (type 0 : clé
# propre à un seul coffre, que le serveur sait compter), et seuls ces objets-là, que le jeu donne déjà par un coffre.
# Recopiés dans l'asm (wwmp_prg_permis) et dans rando_verif (les tests comparent).
FAMILLES_PROG = (("arc", (0x27, 0x36)),)
OBJETS_PROG = frozenset(o for _nom, _paliers in FAMILLES_PROG for o in _paliers)
PROG_NOMS = {"arc": ("arc progressif (arc du héros, puis flèches de lumière)",
                     "progressive bow (Hero's Bow, then Light Arrows)")}
PROG_COMPTE_MAX = 8                          # borne d'un compte reçu du réseau (bien plus que de paliers)

# Portage TWWHD, lot 5 : petites clés, grandes clés, cartes et boussoles de donjon HORS de leur donjon. Les options
# « cles », « grandes_cles » et « cartes_donjon » prennent quatre valeurs : « origine » (pas mélangé), « donjon »
# (mélangé dans son donjon, comme avant), « tout_donjon » (dans un coffre de n'importe quel donjon) et « partout »
# (dans n'importe quel coffre mélangé). Les deux dernières sont nouvelles, jamais essayées en jeu.
#
# Le jeu compte une petite clé pour le donjon du NIVEAU où l'on se trouve [HD-ASM item_func_small_key 0x0254DFA4],
# et pose la grande clé, la carte et la boussole dans la mémoire du niveau courant : donnés tels quels ailleurs, ils
# iraient au mauvais donjon. Un objet de donjon placé hors du sien passe donc par le SERVEUR, sans aucun crochet de
# plus dans le pack (rien à vérifier dans le jeu, donc aucun risque de plantage nouveau) :
#   - le coffre « étranger » porte dans la table un objet d'attente sans effet, OBJET_HORS (rubis jaune, déjà dans la
#     liste blanche du pack) : le pack ne voit jamais la clé, il ne peut donc pas l'ajouter au donjon courant ;
#   - une entrée de plus, de type TYPE_HORS, dit ce que ce coffre vaut vraiment : [4, index de sauvegarde, numéro de
#     coffre, objet réel, donjon de destination (index de sauvegarde du donjon de l'objet)] ; elle est couverte par
#     l'empreinte de la table ;
#   - quand le groupe ouvre ce coffre (registre des coffres partagés du serveur), le serveur crédite la petite clé au
#     compteur commun du bon donjon (synchro/cles.py, identifiant « r<index>:<coffre> » : compté une seule fois), et
#     annonce les grandes clés, cartes et boussoles trouvées (message « rando_hors ») : chaque client pose alors le
#     bit du bon donjon dans sa sauvegarde.
# Ce qui n'est PAS fait, et pourquoi : le coffre montre un rubis jaune (modèle et texte), pas la clé. Montrer la clé
# demanderait de détourner les fonctions d'objet du jeu (item_func_small_key et ses sœurs) pour un donjon autre que
# le courant, ce qui ne se vérifie pas hors du jeu ; le client écrit à la place une ligne dans son journal.
TYPE_HORS = 4
OBJET_HORS = 0x03
MODES_DONJON = ("origine", "donjon", "tout_donjon", "partout")
MODES_HORS = ("tout_donjon", "partout")
OPTIONS_HORS = {"cles": (PETITE_CLE,), "grandes_cles": (GRANDE_CLE,), "cartes_donjon": (CARTE_DONJON, BOUSSOLE)}
# bit de l'octet « objets de donjon » de la mémoire d'un niveau (coffres.DUN_MAP, DUN_COMPASS, DUN_BOSS_KEY)
BIT_HORS = {CARTE_DONJON: 0, BOUSSOLE: 1, GRANDE_CLE: 2}
NOMS_HORS = {PETITE_CLE: ("petite clé", "small key"), GRANDE_CLE: ("grande clé", "big key"),
             CARTE_DONJON: ("carte du donjon", "dungeon map"), BOUSSOLE: ("boussole", "compass")}
# index de sauvegarde -> donjon (pour les messages ; 2 = Forteresse maudite : carte et boussole seulement)
DONJONS_HORS = {2: "Forteresse maudite", 3: "Caverne du Dragon", 4: "Bois défendus", 5: "Tour des Dieux",
                6: "Temple de la Terre", 7: "Temple du Vent"}
HORS_MAX = 64                                # bornes d'un message « rando_hors » reçu du réseau

# Randomizer par fichiers, étape 2 (lot 6, rando/lot6.py) : lieux dont l'objet est écrit par le pack WWMP_Rando
# GÉNÉRÉ sur chaque PC (fichiers du jeu, lignes de code). Entrée [5, site de rando_lot6.SITES, 0, origine, nouveau] ;
# ni le client ni le pack Mailbox ne les appliquent (le pack généré, identique chez tous, le fait). Chaque site garde
# sa liste d'objets permis (rando_lot6.accepte).
TYPE_FICHIER = 5
# Monde ouvert, objets de départ mélangés (option « depart_melange », wwmp/rando_lot7.DEPART_MELANGEABLES) : case de
# départ vidée, entrée [6, case, 0, objet de départ, rubis]. Ni le client ni le pack Mailbox ne l'appliquent : le pack
# WWMP_Rando généré ne donne plus ces objets à la création (le rubis n'est pas donné) ; l'entrée garde la table
# lisible comme une permutation (l'objet de départ est dans un lieu du lot 6).
TYPE_DEPART = 6


def mode_hors(options, objet):
    """Mode (« tout_donjon » ou « partout ») de cet objet de donjon avec ces options, ou None s'il ne sort pas de
    son donjon. Tolérant : options absentes ou d'avant le lot = jamais hors du donjon."""
    if not isinstance(options, dict):
        return None
    for cle, objets in OPTIONS_HORS.items():
        if objet in objets:
            return options.get(cle) if options.get(cle) in MODES_HORS else None
    return None


def hors_actif(options) -> bool:
    """Ces options sortent-elles un objet de donjon de son donjon ?"""
    return isinstance(options, dict) and any(options.get(cle) in MODES_HORS for cle in OPTIONS_HORS)


def nom_hors(objet: int, donjon: int, langue: str = "fr") -> str:
    """« petite clé (Caverne du Dragon) » : objet de donjon et son donjon, pour un message ou le spoiler."""
    noms = NOMS_HORS.get(objet)
    nom = noms[1 if langue == "en" else 0] if noms else f"objet 0x{objet:02X}"
    return f"{nom} ({DONJONS_HORS.get(donjon, 'donjon ' + str(donjon))})"


def id_cle_hors(idx: int, tbox: int) -> str:
    """Identifiant, dans le compteur commun des clés, de la petite clé d'un coffre étranger (compté une fois)."""
    return f"r{int(idx)}:{int(tbox)}"


def famille_prog(objet):
    """(nom, paliers) de la famille progressive de cet objet, ou None."""
    for nom, paliers in FAMILLES_PROG:
        if objet in paliers:
            return nom, paliers
    return None


def palier_prog(paliers, compte: int) -> int:
    """Objet que donne le prochain lieu progressif quand ``compte`` lieux de la famille sont déjà ouverts."""
    return paliers[max(0, min(int(compte), len(paliers) - 1))]


def carte_prog(familles, comptes=None) -> bytes:
    """Carte de la zone wwmp_prg du pack (256 octets) : pour chaque palier d'une famille ACTIVE (``familles`` : noms),
    l'objet que le prochain lieu progressif doit donner ; 0 ailleurs (objet jamais permis : le pack ne change rien)."""
    b = bytearray(256)
    comptes = comptes if isinstance(comptes, dict) else {}
    for nom, paliers in FAMILLES_PROG:
        if nom in familles:
            c = comptes.get(nom, 0)
            c = c if _int(c) and c > 0 else 0
            for o in paliers:
                b[o] = palier_prog(paliers, c)
    return bytes(b)


def pack_permis_prog_octets() -> bytes:
    """Liste des paliers du pack (wwmp_prg_permis, 256 octets, 1 = palier d'un objet progressif), copie exacte de
    OBJETS_PROG."""
    return bytes(1 if k in OBJETS_PROG else 0 for k in range(256))


def condense_niveau(nom) -> int:
    """Condensé 32 bits d'un nom de niveau, comme le calcule le pack (wwmp_rnd4_nom) : pour chaque octet du nom
    (8 au plus, arrêt au premier octet nul), rotation à gauche de 5 bits puis addition de l'octet."""
    if isinstance(nom, str):
        nom = nom.encode("ascii")
    h = 0
    for c in bytes(nom)[:8]:
        if c == 0:
            break
        h = (((h << 5) | (h >> 27)) + c) & 0xFFFFFFFF
    return h


# Témoins : régions du programme autour des octets ci-dessus, telles que le RPX USA v0 les contient (relues par les
# tests). Le client n'écrit RIEN tant qu'elles ne sont pas exactement là (hors des octets qu'il écrit lui-même) : autre
# version du jeu, autre jeu, adresse fausse -> refus, jamais une écriture à l'aveugle. Tables entières pour le courrier
# (13 lettres de 12 octets), les enchères (5 lots de 8 octets) et les personnages (6 mots) ; pour la boutique, les
# trois mots (objet, copie de l'objet, objet lié) de chaque étal écrit, et le mot de drapeaux de l'étal à 950 rubis.
TEMOINS = (
    (0x10031508, "00001aaf0700ac030000000000000cf90700b5030000000000000cfac3007d030000000000000cfc04007b03000000"
                 "000000080504009d030000000000000cfd01007a030100000000000db6cb00b203000000000000114804008b03000000"
                 "0000001aaf0700ac030100000000000f769d00b00300000000000019a63f00ae030100000000000cfb04007c03010000"
                 "0000000f779e00af0300000000"),
    # (étape 5 : six lots, jusqu'au bout du lot qui suit l'enchère à 100 rubis, pour entourer son champ 0x10190192)
    (0x1019016C, "001f1d1000280f0100e41d110005108000ed1d12003c104000071d130050102000771d140064000000961d14001e1008"),
    (0x101C3998, "00000007000000eb000000ef0000000700000007000000f0"),
    (0x101EAF54, "000000500000005000000050"),
    (0x101EAF64, "0000000700000007ffffffff22000000"),
    # étape 5 (rando/lot9.py) : la table des récompenses de Kane, et sa table de bits « déjà donné » (lue au même
    # endroit, 0x022C6358) qui prouve que c'est bien elle
    (0x101C3870, "00000005000000050000000400000004"),
    (0x101C305C, "2410240824042402"),
)
MODES = ("sur", "complet")

NOMS_EN = {0x01: "Green Rupee", 0x02: "Blue Rupee", 0x03: "Yellow Rupee", 0x04: "Red Rupee", 0x05: "Purple Rupee",
           0x06: "Orange Rupee", 0x07: "Piece of Heart", 0x09: "Small Magic Jar", 0x0A: "Large Magic Jar",
           0x0B: "5 Bombs", 0x0C: "10 Bombs", 0x0D: "20 Bombs", 0x0E: "30 Bombs", 0x0F: "Silver Rupee",
           0x10: "10 Arrows", 0x11: "20 Arrows", 0x12: "30 Arrows"}


class ErreurRando(ValueError):
    pass


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def nom_objet(item: int, langue: str = "fr") -> str:
    if item in CARTES_TRESOR:
        n = CARTES_TRESOR[item]
        return f"Treasure Chart {n}" if langue == "en" else f"Carte au trésor {n}"
    if langue == "en" and item in NOMS_EN:
        return NOMS_EN[item]
    from synchro import coffres
    return coffres.item_name(item)


def nettoyer_graine(g) -> str:
    """Graine choisie par l'hôte : texte libre, espaces de bord retirés, caractères imprimables, 64 au plus.
    "" = randomizer désactivé."""
    if not isinstance(g, str):
        return ""
    g = "".join(c for c in g.strip() if c.isprintable())
    return g[:GRAINE_MAX]


# --- données ----------------------------------------------------------------------------------------------------
class Donnees:
    def __init__(self, doc: dict):
        if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("coffres"), list):
            raise ErreurRando("rando_coffres.json : format inconnu")
        self.coffres: list[dict] = []
        for c in doc["coffres"]:
            if not (isinstance(c, dict) and _int(c.get("idx")) and _int(c.get("tbox")) and _int(c.get("objet"))
                    and 0 <= c["idx"] < N_IDX and 0 <= c["tbox"] < N_TBOX and 0 <= c["objet"] <= 0xFF):
                raise ErreurRando(f"rando_coffres.json : coffre invalide {c!r:.80}")
            self.coffres.append(c)
        par_cle: dict[tuple, list] = {}
        for c in self.coffres:
            par_cle.setdefault((c["idx"], c["tbox"]), []).append(c)
        # pool : objet permis, aucun conflit, clé portée par un seul lieu
        self.pool = sorted((c for c in self.coffres if c["objet"] in PERMIS and not c.get("conflit")
                            and len(par_cle[(c["idx"], c["tbox"])]) == 1),
                           key=lambda c: (c["idx"], c["tbox"]))
        if len(self.pool) > POOL_MAX:
            raise ErreurRando("pool trop grand")
        self.par_cle = {(c["idx"], c["tbox"]): c for c in self.pool}
        canon = json.dumps([[c["idx"], c["tbox"], c["objet"]] for c in self.pool], separators=(",", ":"))
        self.version = hashlib.sha256(canon.encode("ascii")).hexdigest()[:16]

    def lieu(self, idx: int, tbox: int) -> dict | None:
        return self.par_cle.get((idx, tbox))


_DONNEES: Donnees | None = None
_VERROU = threading.Lock()


def donnees(chemin: str | None = None) -> Donnees:
    """Données des coffres (lues une fois). Lève ErreurRando si le fichier manque ou est abîmé."""
    global _DONNEES
    if chemin is not None:
        with open(chemin, encoding="utf-8") as f:
            return Donnees(json.load(f))
    with _VERROU:
        if _DONNEES is None:
            try:
                with open(FICHIER_DONNEES, encoding="utf-8") as f:
                    _DONNEES = Donnees(json.load(f))
            except (OSError, ValueError) as e:
                raise ErreurRando(f"données du randomizer illisibles ({FICHIER_DONNEES}) : {e}") from None
        return _DONNEES


# --- tirage déterministe --------------------------------------------------------------------------------------------
class Flux:
    """Octets pseudo-aléatoires : sha256(clé || compteur 64 bits) bout à bout."""

    def __init__(self, cle: bytes):
        self.cle, self.n, self.tampon = cle, 0, b""

    def octets(self, k: int) -> bytes:
        while len(self.tampon) < k:
            self.tampon += hashlib.sha256(self.cle + self.n.to_bytes(8, "big")).digest()
            self.n += 1
        out, self.tampon = self.tampon[:k], self.tampon[k:]
        return out

    def sous(self, borne: int) -> int:
        """Entier uniforme dans [0, borne) (rejet : aucun biais)."""
        if borne <= 1:
            return 0
        bits = (borne - 1).bit_length()
        nb = (bits + 7) // 8
        masque = (1 << bits) - 1
        while True:
            v = int.from_bytes(self.octets(nb), "big") & masque
            if v < borne:
                return v


def graine_effective(graine: str, d: Donnees) -> bytes:
    return hashlib.sha256(f"{ALGO}|{d.version}|{graine}".encode("utf-8")).digest()


class Table:
    """Placement : {(idx, tbox): (objet d'origine, nouvel objet)} (seulement les coffres du pool)."""

    mode = "sur"
    etat = ETAT_SUR             # état du pack qui applique cette table (liste d'objets du palier 1)

    def __init__(self, entrees: dict, graine: str | None = None, version: str = ""):
        self.entrees = dict(entrees)
        self.graine = graine
        self.version = version
        self.empreinte = empreinte(self.entrees)

    @property
    def emp_u32(self) -> int:
        return int(self.empreinte[:8], 16)

    def changes(self) -> int:
        return sum(1 for o, n in self.entrees.values() if o != n)

    def octets_pack(self) -> bytes:
        """Table du pack : 16 × 32 entrées de 2 octets (origine, nouveau), 0xFF 0xFF = rien."""
        b = bytearray([VIDE]) * TABLE_OCTETS
        for (i, t), (o, n) in self.entrees.items():
            k = 2 * (i * N_TBOX + t)
            b[k], b[k + 1] = o, n
        return bytes(b)

    def message(self) -> dict:
        """Message « rando » du serveur (sans la graine ni les noms : seulement ce que le pack doit savoir)."""
        return {"type": "rando", "v": FORMAT, "emp": self.empreinte, "donnees": self.version,
                "t": [[i, t, o, n] for (i, t), (o, n) in sorted(self.entrees.items())]}

    def document(self) -> dict:
        """Section « rando » du monde persistant : relue telle quelle, jamais recalculée."""
        return dict(self.message(), type=None, graine=self.graine, algo=ALGO, palier=PALIER)

    def spoiler(self, d: Donnees, langue: str = "fr") -> list[tuple[str, str, str]]:
        """[(lieu, nouvel objet, objet d'origine)] trié par lieu."""
        out = []
        for (i, t), (o, n) in self.entrees.items():
            c = d.lieu(i, t) or {}
            lieu = (c.get("nom_en") if langue == "en" else c.get("nom")) or f"coffre ({i}, {t})"
            out.append((lieu, nom_objet(n, langue), nom_objet(o, langue)))
        return sorted(out)

    def spoiler_texte(self, d: Donnees) -> str:
        l = ["WWMP — randomizer coop, palier 1 (objets sans progression, coffres seulement)",
             f"Graine : {self.graine if self.graine is not None else '(inconnue)'}",
             f"Empreinte : {self.empreinte}    Données : {self.version}    Algorithme : {ALGO}",
             f"Écrit le {time.strftime('%Y-%m-%d %H:%M:%S')} ; {len(self.entrees)} coffres dans le pool, "
             f"{self.changes()} changés.", "",
             "=== Français : lieu -> objet (objet d'origine) ==="]
        for lieu, n, o in self.spoiler(d, "fr"):
            l.append(f"{lieu} : {n}" + ("" if n == o else f"   (à l'origine : {o})"))
        l += ["", "=== English: location -> item (original item) ==="]
        for lieu, n, o in self.spoiler(d, "en"):
            l.append(f"{lieu}: {n}" + ("" if n == o else f"   (originally: {o})"))
        return "\n".join(l) + "\n"


def empreinte(entrees: dict) -> str:
    h = hashlib.sha256(b"WWMP-RANDO|1|")
    for (i, t), (o, n) in sorted(entrees.items()):
        h.update(bytes((i, t, o, n)))
    return h.hexdigest()[:16]


def calculer(graine: str, d: Donnees | None = None) -> Table:
    """Table de la graine (déterministe). Lève ErreurRando pour une graine vide."""
    d = d or donnees()
    graine = nettoyer_graine(graine)
    if not graine:
        raise ErreurRando("graine vide")
    lieux = d.pool                               # déjà trié par (idx, tbox)
    objets = [c["objet"] for c in lieux]
    f = Flux(graine_effective(graine, d))
    for i in range(len(objets) - 1, 0, -1):
        j = f.sous(i + 1)
        objets[i], objets[j] = objets[j], objets[i]
    entrees = {(c["idx"], c["tbox"]): (c["objet"], n) for c, n in zip(lieux, objets)}
    verifier(entrees, d)
    return Table(entrees, graine, d.version)


def verifier(entrees: dict, d: Donnees) -> None:
    """Garde-fous (aussi sur une table REÇUE ou RELUE) : chaque clé est un coffre du pool de CES données avec le
    même objet d'origine ; les deux objets sont permis ; les nouveaux objets sont une permutation des objets
    d'origine (chaque objet obtenable une fois). Lève ErreurRando."""
    if len(entrees) > POOL_MAX:
        raise ErreurRando("table trop grande")
    for (i, t), (o, n) in entrees.items():
        c = d.lieu(i, t)
        if c is None:
            raise ErreurRando(f"coffre ({i}, {t}) hors du pool de ces données")
        if c["objet"] != o:
            raise ErreurRando(f"coffre ({i}, {t}) : objet d'origine 0x{o:02X} != 0x{c['objet']:02X}")
        if o not in PERMIS or n not in PERMIS:
            raise ErreurRando(f"coffre ({i}, {t}) : objet non permis")
    if sorted(o for o, _n in entrees.values()) != sorted(n for _o, n in entrees.values()):
        raise ErreurRando("table : les nouveaux objets ne sont pas une permutation des objets d'origine")


def depuis_message(msg: dict, d: Donnees | None = None) -> Table:
    """Table reçue du serveur ou relue du monde persistant. Lève ErreurRando (rien n'est appliqué)."""
    d = d or donnees()
    if not isinstance(msg, dict) or msg.get("v") != FORMAT or not isinstance(msg.get("t"), list):
        raise ErreurRando("message rando : format inconnu")
    if len(msg["t"]) > POOL_MAX:
        raise ErreurRando("message rando : table trop grande")
    entrees = {}
    for e in msg["t"]:
        if not (isinstance(e, list) and len(e) == 4 and all(_int(x) for x in e)
                and 0 <= e[0] < N_IDX and 0 <= e[1] < N_TBOX and 0 <= e[2] <= 0xFF and 0 <= e[3] <= 0xFF):
            raise ErreurRando("message rando : entrée invalide")
        if (e[0], e[1]) in entrees:
            raise ErreurRando("message rando : clé en double")
        entrees[(e[0], e[1])] = (e[2], e[3])
    verifier(entrees, d)
    g = msg.get("graine")
    t = Table(entrees, g if isinstance(g, str) else None, str(msg.get("donnees") or "")[:32])
    if t.empreinte != msg.get("emp"):
        raise ErreurRando("message rando : empreinte fausse")
    return t


# ---------------------------------------------------------------------------------------------------------------
# Randomizer complet, étape 2 : table du moteur de logique (rando/moteur.py), coffres avec progression
# ---------------------------------------------------------------------------------------------------------------
class Table2:
    """Table du randomizer complet (format 2). Même interface que ``Table`` pour le serveur, le client et le pack
    (même zone, même disposition : seuls l'état du pack et sa liste d'objets changent).

    ``res`` : résultat du moteur (table revérifiée : forme, permutation, objets de donjon dans leur donjon,
    finissabilité) ; None si la table a été relue « telle quelle » parce que les données de logique de CE PC ne sont
    pas celles qui l'ont calculée (monde persistant gardé après une mise à jour des outils) : forme, liste blanche et
    permutation sont alors seules revérifiées, et les gardes du pack font le reste."""

    mode = "complet"
    etat = ETAT_COMPLET

    def __init__(self, doc: dict, res=None):
        self.doc = {k: doc[k] for k in CHAMPS2 if k in doc}
        self.res = res
        # ``entrees`` : COFFRES seulement (clé (index de sauvegarde, numéro de coffre) : table du stub N1, veilles des
        # coffres) ; ``entrees_n2`` : cadeaux de personnages (clé : site de SITES_N2 ; table du stub N2)
        self.entrees = {(e[1], e[2]): (e[3], e[4]) for e in doc["t"] if e[0] == TYPE_COFFRE}
        self.entrees_n2 = {e[1]: (e[3], e[4]) for e in doc["t"] if e[0] == TYPE_N2}
        # lot 1 du portage : lieux « donnée » (clé : site de SITES_DONNEES ; octets écrits par le client)
        self.entrees_don = {e[1]: (e[3], e[4]) for e in doc["t"] if e[0] == TYPE_DONNEES}
        # lot 2 du portage : coffres « en conflit » (clé : site de SITES_CONFLIT ; table de la zone wwmp_rnd4). Jamais
        # dans ``entrees`` : leur clé (index, numéro) désigne aussi le coffre d'un autre niveau.
        self.entrees_conflit = {e[1]: (e[3], e[4]) for e in doc["t"] if e[0] == TYPE_CONFLIT}
        # lot 5 du portage : coffres ordinaires qui portent un objet de donjon ÉTRANGER, clé (index, numéro) ->
        # (objet réel, index de sauvegarde de son donjon). Dans ``entrees``, ces coffres portent l'objet d'attente.
        self.hors = {(e[1], e[2]): (e[3], e[4]) for e in doc["t"] if e[0] == TYPE_HORS}
        # lot 6 (randomizer par fichiers) : site de rando_lot6.SITES -> (origine, nouveau). Appliqué par le pack
        # WWMP_Rando généré (le même chez tous, vérifié à la connexion), jamais par le client.
        self.entrees_fich = {e[1]: (e[3], e[4]) for e in doc["t"] if e[0] == TYPE_FICHIER}
        # monde ouvert, objets de départ mélangés : case -> (objet de départ, rubis jamais donné). Le pack généré
        # suit les options de la table (rando_lot7.listes_creation), jamais le client.
        self.entrees_depart = {e[1]: (e[3], e[4]) for e in doc["t"] if e[0] == TYPE_DEPART}
        self.graine = doc.get("graine") if isinstance(doc.get("graine"), str) else None
        self.version = str(doc.get("donnees") or "")[:32]
        self.options = doc.get("options") if isinstance(doc.get("options"), dict) else {}
        self.empreinte = str(doc["emp"])
        # indices (étape 4) : textes français et anglais, dans l'ordre du moteur ; jamais dans le message « rando »
        # (le serveur les dévoile un par un, voir ``ordre_indices``). Table relue du monde : textes gardés par le monde.
        ind = [i for i in (getattr(res, "indices", None) or []) if isinstance(i, dict)]
        self.indices = [str(i.get("texte")) for i in ind]
        self.indices_en = [str(i.get("texte_en") or i.get("texte")) for i in ind]
        if not self.indices and isinstance(doc.get("indices"), list):
            self.indices = [str(x)[:300] for x in doc["indices"][:64]]
            en = doc.get("indices_en")
            self.indices_en = ([str(x)[:300] for x in en] if isinstance(en, list) and len(en) == len(self.indices)
                               else list(self.indices))

    @property
    def emp_u32(self) -> int:
        return int(self.empreinte[:8], 16)

    def changes(self) -> int:
        return sum(1 for o, n in list(self.entrees.values()) + list(self.entrees_n2.values())
                   + list(self.entrees_don.values()) + list(self.entrees_conflit.values())
                   + list(self.entrees_fich.values()) + list(self.entrees_depart.values()) if o != n) \
            + sum(1 for k in self.hors if k in self.entrees and self.entrees[k][0] == self.entrees[k][1])

    @property
    def hors_requis(self) -> bool:
        """La table place-t-elle au moins un objet de donjon hors du sien (lot 5 : serveur et clients qui partagent
        les petites clés ET les coffres exigés : sans eux, la clé d'un coffre étranger serait perdue) ?"""
        return bool(self.hors)

    def hors_cles(self) -> dict:
        """{(index, numéro): donjon} des coffres étrangers qui valent une PETITE CLÉ (créditée par le serveur)."""
        return {k: s for k, (o, s) in self.hors.items() if o == PETITE_CLE}

    def hors_bits(self) -> dict:
        """{(index, numéro): (donjon, bit de l'octet d'objets de donjon, objet)} des coffres étrangers qui valent
        une grande clé, une carte ou une boussole (bit posé par chaque client dans sa sauvegarde)."""
        return {k: (s, BIT_HORS[o], o) for k, (o, s) in self.hors.items() if o in BIT_HORS}

    octets_pack = Table.octets_pack

    def octets_n2(self) -> bytes:
        """Entrées de la zone wwmp_rnd3 du pack : 8 octets par cadeau CHANGÉ (adresse de retour de l'appel u32, 0 u16,
        objet d'origine u8, nouvel objet u8), dans l'ordre des sites. Vide : aucun cadeau changé (le bit 7 du pack
        n'est alors pas nécessaire)."""
        return b"".join(struct.pack(">IHBB", SITES_N2[k][0], 0, o, n)
                        for k, (o, n) in sorted(self.entrees_n2.items()) if o != n)

    @property
    def n2_requis(self) -> bool:
        """La table change-t-elle au moins un cadeau de personnage (stub N2 du pack exigé chez chaque joueur) ?"""
        return any(o != n for o, n in self.entrees_n2.values())

    def octets_conflit(self) -> bytes:
        """Entrées de la zone wwmp_rnd4 du pack : 8 octets par coffre en conflit CHANGÉ (condensé du nom de niveau
        u32, clé u16 = index * 32 + numéro, objet d'origine u8, nouvel objet u8), dans l'ordre des sites. Vide : aucun
        de ces coffres n'est changé (la zone n'est alors pas nécessaire : aucun de leurs objets n'a bougé)."""
        return b"".join(struct.pack(">IHBB", condense_niveau(SITES_CONFLIT[k][0]),
                                    SITES_CONFLIT[k][1] * N_TBOX + SITES_CONFLIT[k][2], o, n)
                        for k, (o, n) in sorted(self.entrees_conflit.items()) if o != n)

    @property
    def conflit_requis(self) -> bool:
        """La table change-t-elle au moins un coffre « en conflit » (zone wwmp_rnd4 du pack exigée chez chaque
        joueur : sans elle, ces coffres et ceux qui reçoivent leurs objets resteraient d'origine) ?"""
        return any(o != n for o, n in self.entrees_conflit.values())

    def prog_familles(self) -> dict:
        """Lot 4 du portage : familles progressives ACTIVES de cette table, {nom: (paliers, clés (index, numéro) de
        ses lieux progressifs, triées)}. Active : option « progressifs » allumée ET chaque palier de la famille est
        l'objet d'au moins un coffre ordinaire de la table. Vide sinon (rien ne change : chaque coffre donne
        exactement l'objet de la table)."""
        out = getattr(self, "_prog_familles", None)
        if out is not None:
            return out
        out = {}
        if self.options.get("progressifs") is True:
            for nom, paliers in FAMILLES_PROG:
                cles = sorted(k for k, (_o, n) in self.entrees.items() if n in paliers)
                if {self.entrees[k][1] for k in cles} == set(paliers):
                    out[nom] = (paliers, tuple(cles))
        self._prog_familles = out            # la table ne change jamais après sa construction
        return out

    @property
    def prog_requis(self) -> bool:
        """La table a-t-elle une famille progressive active (zone wwmp_prg du pack exigée chez chaque joueur : sans
        elle, les flèches de lumière ouvertes avant l'arc seraient perdues) ?"""
        return bool(self.prog_familles())

    def prog_lieu(self, idx, tbox):
        """(nom de famille, paliers) si le coffre (index, numéro) est un lieu progressif actif, sinon None."""
        for nom, (paliers, cles) in self.prog_familles().items():
            if (idx, tbox) in cles:
                return nom, paliers
        return None

    def prog_comptes(self, ouverts) -> dict:
        """{famille: nombre de ses lieux progressifs parmi ``ouverts``} (ensemble de clés (index, numéro)). Compte
        par LIEU : le même coffre signalé deux fois ne compte qu'une fois."""
        return {nom: sum(1 for k in cles if k in ouverts) for nom, (_p, cles) in self.prog_familles().items()}

    def octets_prog(self, comptes=None) -> bytes:
        """Carte de la zone wwmp_prg pour ces comptes (voir ``carte_prog``)."""
        return carte_prog(set(self.prog_familles()), comptes)

    @property
    def boucliers_prog(self) -> bool:
        """Boucliers progressifs (option « boucliers_progressifs ») : le client remet le bouclier porté au plus haut
        palier acquis (filet, voir wwmp/inventaire.reparer_bouclier)."""
        from rando import lot7 as rando_lot7
        return rando_lot7.boucliers_progressifs(self.options)

    @property
    def don_requis(self) -> bool:
        """La table change-t-elle au moins un lieu « donnée » (courrier, boutique, enchère, personnage à table :
        client qui sait écrire ces octets exigé chez chaque joueur) ?"""
        return any(o != n for o, n in self.entrees_don.values())

    @property
    def don9_requis(self) -> bool:
        """Étape 5 (rando/lot9.py) : la table a-t-elle un lieu « donnée » ajouté après le lot 1 (Kane, autres
        lettres, enchère à 100 rubis) ? Un client d'avant ne connaît pas ces sites : il refuserait la table."""
        return any(k >= N_DONNEES_LOT1 for k in self.entrees_don)

    def message(self) -> dict:
        """Message « rando » du serveur : la table telle que le moteur la signe (options comprises : chaque client
        revérifie tout, finissabilité comprise). Ni graine, ni spoiler, ni indices."""
        return dict({k: v for k, v in self.doc.items() if k != "graine"}, type="rando")

    def document(self) -> dict:
        """Section « rando » du monde persistant : relue telle quelle, jamais recalculée."""
        return dict(self.doc, type=None, palier=2, indices=list(self.indices), indices_en=list(self.indices_en))

    def ordre_indices(self) -> list[int]:
        """Ordre dans lequel le serveur dévoile les indices (positions dans ``indices``). Le moteur les range par
        genre (chemin, stérile, objet, lieu) ; les dévoiler dans cet ordre donnerait tous les « chemin » d'abord.
        Ordre fixe, tiré de l'empreinte seule : le même à chaque relance du serveur et pour un monde relu."""
        f = Flux(hashlib.sha256(("indices|" + self.empreinte).encode("utf-8")).digest())
        o = list(range(len(self.indices)))
        for i in range(len(o) - 1, 0, -1):
            j = f.sous(i + 1)
            o[i], o[j] = o[j], o[i]
        return o

    def spoiler(self, d=None, langue: str = "fr") -> list[tuple[str, str, str]]:
        r = self.res
        out = []
        if r is None:
            for (i, t), (o, n) in self.entrees.items():
                if (i, t) in self.hors:              # lot 5 : l'objet réel du coffre étranger, et son donjon
                    n = self.hors[(i, t)][0]
                    out.append((f"coffre ({i}, {t})", nom_hors(n, self.hors[(i, t)][1], langue),
                                nom_objet(o, langue)))
                    continue
                out.append((f"coffre ({i}, {t})", nom_objet(n, langue), nom_objet(o, langue)))
            for k, (o, n) in self.entrees_n2.items():
                out.append((SITES_N2[k][2], nom_objet(n, langue), nom_objet(o, langue)))
            for k, (o, n) in self.entrees_don.items():
                out.append((SITES_DONNEES[k][1], nom_objet(n, langue), nom_objet(o, langue)))
            for k, (o, n) in self.entrees_conflit.items():
                out.append((SITES_CONFLIT[k][4], nom_objet(n, langue), nom_objet(o, langue)))
            from rando import lot6 as rando_lot6
            for k, (o, n) in self.entrees_fich.items():
                nom = rando_lot6.SITES[k]["nom"] if 0 <= k < len(rando_lot6.SITES) else f"lot 6, site {k}"
                out.append((nom, nom_objet(n, langue), nom_objet(o, langue)))
            from rando import lot7 as rando_lot7
            for k, (o, n) in self.entrees_depart.items():
                nom = (rando_lot7.DEPART_MELANGEABLES[k][2] if 0 <= k < len(rando_lot7.DEPART_MELANGEABLES)
                       else f"départ, case {k}")
                out.append((nom, nom_objet(n, langue), nom_objet(o, langue)))
            return sorted(out)
        lg = r.lg
        for k, p in r.place.items():
            c = lg.lieux[k]
            if langue == "en":
                out.append((c["nom"], lg.lieux[p]["objet"], c["objet"]))
            else:
                out.append((c["nom_fr"], lg.nom_fr(lg.origine[p]), lg.nom_fr(lg.origine[k])))
        return sorted(out)

    def spoiler_texte(self, d=None) -> str:
        r = self.res
        if r is None:
            l = ["WWMP — randomizer complet (coffres et cadeaux de personnages avec progression)",
                 f"Graine : {self.graine if self.graine is not None else '(inconnue)'}",
                 f"Empreinte : {self.empreinte}    Données : {self.version} (autres que celles de ces outils : table "
                 "gardée telle quelle, sans sphères ni lieux fixes)", ""]
            l += [f"{lieu} : {n}" + ("" if n == o else f"   (à l'origine : {o})") for lieu, n, o in self.spoiler()]
            return "\n".join(l) + "\n"
        from rando import moteur as rando_moteur
        l = [r.spoiler_texte().rstrip("\n")]
        if not r.indices and self.indices:           # table relue (monde persistant) : indices gardés dans le monde
            l += self.indices
        l += ["", "== Tous les lieux mélangés (coffres, cadeaux de personnages) : lieu -> objet (objet d'origine) =="]
        l += [f"{lieu} : {n}" + ("" if n == o else f"   (à l'origine : {o})") for lieu, n, o in self.spoiler()]
        l += ["", "== All shuffled locations (chests, character gifts): location -> item (original item) =="]
        l += [f"{lieu}: {n}" + ("" if n == o else f"   (originally: {o})") for lieu, n, o in self.spoiler(langue="en")]
        l += ["", "== Lieux qui gardent leur objet, et pourquoi =="]
        l += [f"{nom} : {raison}" for nom, raison in rando_moteur.lieux_fixes(self.options, r.lg)]
        return "\n".join(l) + "\n"


CHAMPS2 = ("v", "algo", "donnees", "options", "graine", "t", "emp")
TABLES = (Table, Table2)


def options_completes(lieux=None, cles=None, grandes_cles=None, cartes_donjon=None, triforce=None,
                      exclus=None, cadeaux=None, indices=None, indices_clairs=None, reglages=None,
                      lieux_courrier=None, lieux_boutiques=None, lieux_encheres=None, lieux_pnj=None,
                      coffres_conflit=None, progressifs=None, fichiers_demo=None, lieux_tresors=None,
                      lieux_evenements=None, lieux_boss=None, lieux_fees=None, lieux_sol=None, lieux_pnj_code=None,
                      lieux_beedle=None, monde=None, depart=None, coffres_cinematiques=None,
                      cinematiques_courtes=None, pas_de_recombats=None, textes=None, sans_epee=None, entrees=None,
                      cartes_melangees=None, lieux_kane=None, courrier_tout=None, lieux_encheres_100=None,
                      boutique_epuise=None, depart_melange=None, boucliers_progressifs=None) -> dict:
    """Options du moteur à partir des réglages de l'hôte (ligne de commande du serveur, lanceur). ``lieux`` : liste
    de catégories, texte « a,b,c », « tout », ou None / "" (défaut du moteur) ; les autres : texte du moteur
    (« origine », « donjon », « melange »), booléen (True = mélangé) ou None (défaut : d'origine) ; ``cadeaux`` :
    booléen (cadeaux de personnages et prix mélangés, étape 3 ; défaut : non) ; ``exclus`` : noms de lieux du
    spoiler, français ou anglais, séparés par « ; » ; ``indices`` : nombres d'indices (dictionnaire, ou texte
    « chemin=3,sterile=3,objet=3,lieu=2 », ou « aucun ») ; ``indices_clairs`` : booléen (False : tournure obscure).
    ``reglages`` : chaîne de réglages partageable (étape 4) ; elle REMPLACE toutes les autres options, pour que deux
    hôtes qui l'entrent obtiennent la même table. ``lieux_courrier``, ``lieux_boutiques``, ``lieux_encheres``,
    ``lieux_pnj`` : booléens (lot 1 du portage TWWHD : lieux changés par écriture de données ; défaut : non).
    ``coffres_conflit`` : booléen (lot 2 : dix coffres dont la clé est partagée avec un autre niveau ; défaut : non).
    ``progressifs`` : booléen (lot 4 : objets progressifs, compte commun tenu par le serveur ; défaut : non).
    ``fichiers_demo`` : booléen (randomizer par fichiers, étape 1 : option de TEST, texte de l'écran titre changé
    par le pack WWMP_Rando généré sur chaque PC ; défaut : non). ``lieux_tresors``, ``lieux_evenements``,
    ``lieux_boss``, ``lieux_fees``, ``lieux_sol``, ``lieux_pnj_code``, ``lieux_beedle`` : booléens (randomizer par
    fichiers, lot 6 : familles de lieux changées par le pack WWMP_Rando généré ; défaut : non). ``monde`` (« histoire »
    ou « ouvert »), ``depart`` (« origine », « hasard » ou le nom anglais d'une île), ``coffres_cinematiques``,
    ``cinematiques_courtes``, ``pas_de_recombats``, ``textes`` : randomizer par fichiers, lot 7 (défaut : d'origine) ;
    le monde ouvert coche de lui-même les coffres à la place des cinématiques (il les exige). ``sans_epee``,
    ``cartes_melangees`` (booléens) et ``entrees`` (« aucune », « donjons », « donjons_grottes ») : randomizer par
    fichiers, lot 8 (défaut : d'origine). ``lieux_kane``, ``courrier_tout``, ``lieux_encheres_100``,
    ``boutique_epuise`` : booléens (randomizer par fichiers, étape 5, rando/lot9.py ; défaut : non).
    ``depart_melange`` : objets de départ du monde ouvert mélangés (liste, ou texte « bombes,perle_din,… » ; noms de
    rando_lot7.NOMS_DEPART ; défaut : aucun ; « bouclier » exige ``boucliers_progressifs``). ``boucliers_progressifs`` :
    booléen (l'item_func du bouclier du héros donne le miroir à qui a déjà un bouclier ; défaut : non). Lève
    ErreurRando."""
    from rando import moteur as rm
    lg = rm.logique()
    if reglages:
        mode, opt = rm.reglages_depuis_chaine(reglages, lg)
        if mode != "complet":
            raise ErreurRando("chaîne de réglages : c'est celle du mode « sur » (lance le serveur avec "
                              "--rando-mode sur, ou sans --rando-mode)")
        return opt
    o = {}
    if isinstance(lieux, str):
        lieux = [x.strip() for x in lieux.replace(";", ",").split(",") if x.strip()]
    if lieux:
        o["lieux"] = sorted(rm.CATEGORIES) if [str(x).lower() for x in lieux] in (["tout"], ["all"]) else list(lieux)
    for cle, v, oui in (("cles", cles, "donjon"), ("grandes_cles", grandes_cles, "donjon"),
                        ("cartes_donjon", cartes_donjon, "donjon"), ("triforce", triforce, "melange")):
        if isinstance(v, bool):
            v = oui if v else "origine"
        if v not in (None, ""):
            o[cle] = v
    if isinstance(exclus, str):
        exclus = [x.strip() for x in exclus.split(";") if x.strip()]
    if exclus:
        o["exclus"] = lieux_exclus(exclus, lg)
    if cadeaux is not None:
        o["cadeaux"] = cadeaux
    for cle, v in (("lieux_courrier", lieux_courrier), ("lieux_boutiques", lieux_boutiques),
                   ("lieux_encheres", lieux_encheres), ("lieux_pnj", lieux_pnj),
                   ("coffres_conflit", coffres_conflit), ("progressifs", progressifs),
                   ("fichiers_demo", fichiers_demo), ("lieux_tresors", lieux_tresors),
                   ("lieux_evenements", lieux_evenements), ("lieux_boss", lieux_boss), ("lieux_fees", lieux_fees),
                   ("lieux_sol", lieux_sol), ("lieux_pnj_code", lieux_pnj_code), ("lieux_beedle", lieux_beedle),
                   ("cinematiques_courtes", cinematiques_courtes), ("pas_de_recombats", pas_de_recombats),
                   ("textes", textes), ("coffres_cinematiques", coffres_cinematiques),
                   ("sans_epee", sans_epee), ("cartes_melangees", cartes_melangees),
                   ("lieux_kane", lieux_kane), ("courrier_tout", courrier_tout),
                   ("lieux_encheres_100", lieux_encheres_100), ("boutique_epuise", boutique_epuise),
                   ("boucliers_progressifs", boucliers_progressifs)):
        if v is not None:
            o[cle] = v
    if entrees not in (None, ""):
        o["entrees"] = entrees
    if monde not in (None, ""):
        o["monde"] = monde
    if depart not in (None, ""):
        o["depart"] = depart
    if depart_melange not in (None, "", []):
        o["depart_melange"] = depart_melange
    if o.get("monde") == "ouvert":
        o["coffres_cinematiques"] = True      # lot 7 : le monde ouvert les exige (cinématiques retirées)
    indices = rm.indices_depuis_texte(indices)
    if indices is not None:
        o["indices"] = indices
    if indices_clairs is not None:
        o["indices_clairs"] = indices_clairs
    return rm.normaliser_options(o, lg)


def lieux_exclus(noms, lg=None) -> list[str]:
    """Noms de lieux de l'option « exclus », ramenés aux noms de la logique (anglais). Un nom est accepté tel que le
    spoiler l'écrit, en français ou en anglais, sans tenir compte de la casse ni des espaces autour. Lève ErreurRando
    en nommant ce qui n'a pas été reconnu (rien n'est exclu « à peu près »)."""
    from rando import moteur as rm
    lg = lg or rm.logique()
    connus = {}
    for e in lg.lieux:
        connus.setdefault(e["nom"].strip().casefold(), e["nom"])
    for e in lg.lieux:                                 # un nom français ne masque jamais un nom anglais
        connus.setdefault(str(e.get("nom_fr") or "").strip().casefold(), e["nom"])
    out, inconnus = [], []
    for n in noms:
        if not isinstance(n, str):
            raise ErreurRando("option exclus : liste de noms de lieux attendue")
        c = connus.get(n.strip().casefold())
        if c is None:
            inconnus.append(n.strip()[:60])
        elif c not in out:
            out.append(c)
    if inconnus:
        raise ErreurRando(f"option exclus : lieux inconnus {inconnus[:5]} (recopie le nom du spoiler ou de l'onglet "
                          "« Indices », en entier)")
    return out


def _chaine_de(table) -> str | None:
    """Chaîne de réglages d'une table (None si ses options ne sont plus celles que ces outils comprennent)."""
    from rando import moteur as rm
    try:
        return rm.chaine_reglages(table.mode, table.options if table.mode == "complet" else None)
    except ErreurRando:
        return None


def calculer_complet(graine: str, options: dict | None = None) -> Table2:
    """Table du randomizer complet (moteur de logique, table revérifiée par le vérificateur indépendant), puis
    contrôle de la liste blanche du pack : un objet hors de PERMIS2 ne serait pas appliqué par le jeu (coffre resté
    d'origine, objet perdu) : la table est alors refusée en entier. Lève ErreurRando."""
    from rando import moteur as rm
    res = rm.calculer(nettoyer_graine(graine), options)
    doc = res.message()
    _forme2(doc)
    return Table2(doc, res)


def _forme2(msg: dict) -> None:
    """Contrôle de forme d'une table de format 2, SANS les données de logique : entrées (coffres : type 0 ; cadeaux
    de personnages : type 1, site de SITES_N2 et son objet d'origine), clés uniques, objets de la liste blanche du
    pack, permutation (rien de perdu ni de dupliqué), objets de donjon restés dans leur index de sauvegarde (jamais
    chez un personnage), empreinte. Lève ErreurRando."""
    from rando import moteur as rm
    if not isinstance(msg, dict) or msg.get("v") != FORMAT2 or msg.get("algo") != ALGO2 \
            or not isinstance(msg.get("t"), list):
        raise ErreurRando("message rando (complet) : format inconnu")
    t = msg["t"]
    from rando import lot6 as rando_lot6
    from rando import lot7 as rando_lot7
    if len(t) > N_IDX * N_TBOX + len(SITES_N2) + len(SITES_DONNEES) + len(SITES_CONFLIT) + HORS_MAX \
            + len(rando_lot6.SITES) + len(rando_lot7.DEPART_MELANGEABLES):
        raise ErreurRando("message rando (complet) : table trop grande")
    # lot 2 du portage : les objets des coffres en conflit ne sont permis que dans une table qui l'annonce (option
    # « coffres_conflit »), et seulement dans un coffre
    o = msg.get("options")
    conflit = isinstance(o, dict) and o.get("coffres_conflit") is True
    # lot 4 : les paliers d'un objet progressif (dont les flèches de lumière, hors de PERMIS2) ne sont permis que dans
    # une table qui l'annonce (option « progressifs »), et seulement dans un coffre ordinaire
    prog = isinstance(o, dict) and o.get("progressifs") is True
    cles_conflit = {(s[1], s[2]) for s in SITES_CONFLIT}
    familles6 = rando_lot6.familles_actives(o)
    # étape 5 (rando/lot9.py) : groupes de lieux « donnée » annoncés par les options ; avec l'un d'eux, les objets
    # de ses lieux (hors de PERMIS2 : lettres, voile rapide) sont permis, mais seulement dans un lieu « donnée »
    groupes9 = {g for g, cle in OPTION_GROUPE.items() if isinstance(o, dict) and o.get(cle) is True}
    don_seuls = OBJETS_DON_SEULS if groupes9 & GROUPES_LOT9 else frozenset()
    # monde ouvert, objets de départ mélangés : cases annoncées par les options ; ces objets ne vont que dans un lieu
    # du lot 6 (le stub N1 de la zone des coffres en conflit accepterait les bombes : refusé ici aussi)
    departs = rando_lot7.departs_melanges(o)
    ids_depart = rando_lot7.ids_depart_melanges(o) - PERMIS2         # (le flacon, de la liste du pack : partout)
    if "bouclier" in departs and not rando_lot7.boucliers_progressifs(o):
        raise ErreurRando("message rando (complet) : bouclier de départ mélangé sans les boucliers progressifs")
    vus, avant, apres = set(), [], []
    # lot 5 : coffres étrangers annoncés (index, numéro) -> (objet réel, donjon) ; objet posé dans chaque coffre
    hors, dans_coffre = {}, {}
    for e in t:
        if not (isinstance(e, list) and len(e) == 5 and all(_int(x) for x in e)):
            raise ErreurRando("message rando (complet) : entrée invalide")
        if e[0] == TYPE_HORS:
            # objet de donjon hors du sien : seulement si l'option de CET objet l'annonce, dans un coffre ordinaire
            # (jamais un coffre en conflit), pour un donjon qui existe. L'entrée ne porte pas un objet à donner : ni
            # liste blanche ni permutation ici (comptée plus bas avec l'entrée du coffre).
            if not (0 <= e[1] < N_IDX and 0 <= e[2] < N_TBOX) or (conflit and (e[1], e[2]) in cles_conflit):
                raise ErreurRando("message rando (complet) : clé de coffre étranger invalide")
            if e[3] not in OBJETS_DE_DONJON or mode_hors(o, e[3]) is None or e[4] not in DONJONS_HORS \
                    or (e[3] in (PETITE_CLE, GRANDE_CLE) and e[4] == 2):
                raise ErreurRando("message rando (complet) : objet de donjon hors de son donjon sans l'option qui le "
                                  "permet, ou donjon inconnu")
            if len(hors) >= HORS_MAX or (e[1], e[2]) in hors:
                raise ErreurRando("message rando (complet) : coffre étranger en double, ou trop de coffres étrangers")
            hors[(e[1], e[2])] = (e[3], e[4])
            continue
        if e[0] == TYPE_COFFRE:
            dans_coffre[(e[1], e[2])] = e[4]
            if not (0 <= e[1] < N_IDX and 0 <= e[2] < N_TBOX):
                raise ErreurRando("message rando (complet) : clé de coffre invalide")
            if conflit and (e[1], e[2]) in cles_conflit:
                raise ErreurRando("message rando (complet) : coffre en conflit donné comme un coffre ordinaire")
        elif e[0] == TYPE_CONFLIT:
            if not (conflit and 0 <= e[1] < len(SITES_CONFLIT) and e[2] == 0 and e[3] == SITES_CONFLIT[e[1]][3]):
                raise ErreurRando("message rando (complet) : coffre en conflit inconnu de ces outils, objet d'origine "
                                  "différent de celui du jeu, ou option « coffres_conflit » absente")
        elif e[0] == TYPE_N2:
            if not (0 <= e[1] < len(SITES_N2) and e[2] == 0 and e[3] == SITES_N2[e[1]][1]):
                raise ErreurRando("message rando (complet) : cadeau de personnage inconnu de ces outils, ou objet "
                                  "d'origine différent de celui du jeu")
            if e[3] in OBJETS_DE_DONJON or e[4] in OBJETS_DE_DONJON:
                raise ErreurRando("message rando (complet) : objet de donjon donné par un personnage")
        elif e[0] == TYPE_FICHIER:
            # lot 6 : lieu du randomizer par fichiers, site de NOTRE relevé, famille annoncée par les options, objet
            # d'origine du relevé, objet permis à cet endroit (jamais d'objet de donjon : il irait au donjon de la salle)
            if not (0 <= e[1] < len(rando_lot6.SITES) and e[2] == 0 and e[3] == rando_lot6.SITES[e[1]]["id"]
                    and rando_lot6.SITES[e[1]]["famille"] in familles6 and rando_lot6.accepte(e[1], e[4])):
                raise ErreurRando("message rando (complet) : lieu du randomizer par fichiers inconnu de ces outils, "
                                  "objet d'origine différent, option absente ou objet interdit à cet endroit")
        elif e[0] == TYPE_DEPART:
            # case de départ vidée : case annoncée par les options, objet de départ du relevé, un rubis reçu
            if not (0 <= e[1] < len(rando_lot7.DEPART_MELANGEABLES) and e[2] == 0
                    and rando_lot7.DEPART_MELANGEABLES[e[1]][0] in departs
                    and e[3] == rando_lot7.DEPART_MELANGEABLES[e[1]][1] and e[4] in rando_lot7.SANS_VALEUR_DEPART):
                raise ErreurRando("message rando (complet) : case de départ inconnue de ces outils, option absente, ou "
                                  "objet reçu autre qu'un rubis")
        elif e[0] == TYPE_DONNEES:
            # lot 1 du portage : lieu « donnée » (courrier, boutique, enchère, personnage à table). Site de NOTRE
            # liste et son objet d'origine ; jamais d'objet de donjon ; jamais un objet interdit à cet endroit.
            if not (0 <= e[1] < len(SITES_DONNEES) and e[2] == 0 and e[3] == SITES_DONNEES[e[1]][2]):
                raise ErreurRando("message rando (complet) : lieu « donnée » inconnu de ces outils, ou objet "
                                  "d'origine différent de celui du jeu")
            if SITES_DONNEES[e[1]][0] in GROUPES_LOT9 and SITES_DONNEES[e[1]][0] not in groupes9:
                raise ErreurRando("message rando (complet) : lieu « donnée » de l'étape 5 sans l'option qui le "
                                  "mélange")
            if e[3] in OBJETS_DE_DONJON or e[4] in OBJETS_DE_DONJON:
                raise ErreurRando("message rando (complet) : objet de donjon dans une lettre, une boutique ou une "
                                  "enchère")
            if e[4] != e[3] and e[4] in SITES_DONNEES[e[1]][5]:
                raise ErreurRando(f"message rando (complet) : objet 0x{e[4]:02X} interdit à cet endroit "
                                  f"({SITES_DONNEES[e[1]][1]})")
        else:
            raise ErreurRando("message rando (complet) : type de lieu inconnu (seuls les coffres, les cadeaux de "
                              "personnages, les lieux « donnée » et les coffres en conflit sont gérés : outils plus "
                              "anciens que ceux de l'hôte ?)")
        if tuple(e[:3]) in vus:
            raise ErreurRando("message rando (complet) : clé en double")
        vus.add(tuple(e[:3]))
        liste = PERMIS4 if conflit and e[0] in (TYPE_COFFRE, TYPE_CONFLIT) else PERMIS2
        if prog and e[0] == TYPE_COFFRE:
            liste = liste | OBJETS_PROG
        elif prog and e[4] in OBJETS_PROG:
            raise ErreurRando(f"message rando (complet) : palier d'objet progressif 0x{e[4]:02X} hors d'un coffre "
                              "ordinaire")
        if e[0] == TYPE_DONNEES and don_seuls:
            # étape 5 : octet écrit par le client, aucune liste du pack en jeu ; ailleurs (coffre, cadeau, coffre en
            # conflit) ces objets restent refusés par la liste du pack ci-dessous
            liste = liste | don_seuls
        if e[0] not in (TYPE_FICHIER, TYPE_DEPART) and e[4] != e[3] and e[4] in ids_depart:
            raise ErreurRando(f"message rando (complet) : objet de départ 0x{e[4]:02X} hors d'un lieu du randomizer "
                              "par fichiers")
        if e[0] in (TYPE_FICHIER, TYPE_DEPART):
            pass                             # liste propre au site ou à la case (contrôlée plus haut)
        elif e[3] not in liste or e[4] not in liste:
            raise ErreurRando(f"message rando (complet) : objet hors de la liste du pack (0x{e[3]:02X} -> "
                              f"0x{e[4]:02X})")
        idx = SITES_CONFLIT[e[1]][1] if e[0] == TYPE_CONFLIT else e[1]     # index de sauvegarde du coffre
        if e[0] in (TYPE_FICHIER, TYPE_DEPART):
            idx = -1                         # jamais d'objet de donjon dans un lieu du lot 6 ni une case de départ
        avant.append((e[3], idx if e[3] in OBJETS_DE_DONJON else -1))
        apres.append((e[4], idx if e[4] in OBJETS_DE_DONJON else -1))
    for cle, (objet, donjon) in hors.items():
        # le coffre porte l'objet d'attente ; pour la permutation il vaut l'objet réel, dans le donjon annoncé
        if dans_coffre.get(cle) != OBJET_HORS:
            raise ErreurRando("message rando (complet) : coffre étranger sans son objet d'attente")
        apres.remove((OBJET_HORS, -1))
        apres.append((objet, donjon))
    if sorted(avant) != sorted(apres):
        raise ErreurRando("message rando (complet) : objets perdus, dupliqués, ou objet de donjon sorti de son donjon")
    if not isinstance(msg.get("emp"), str) or rm.empreinte(t) != msg["emp"]:
        raise ErreurRando("message rando (complet) : empreinte fausse")


def depuis_message2(msg: dict) -> Table2:
    """Table de format 2 reçue du serveur ou relue du monde persistant. Avec les MÊMES données de logique que celles
    qui l'ont calculée : tout est revérifié (moteur : coffres mélangeables avec ces options, permutation,
    finissabilité). Avec d'autres données (outils mis à jour entre-temps) : forme seulement (``_forme2``), parce
    qu'une table déjà jouée ne doit jamais être recalculée ni perdue. Lève ErreurRando (rien n'est appliqué)."""
    from rando import moteur as rm
    _forme2(msg)
    if not rm.logique().memes_donnees(msg.get("donnees"), msg.get("options")):
        return Table2(msg, None)
    return Table2(msg, rm.depuis_message({k: msg.get(k) for k in CHAMPS2}))


def table_depuis_message(msg: dict):
    """Table du serveur ou du monde persistant, quel que soit son format (1 : palier 1 ; 2 : randomizer complet)."""
    if isinstance(msg, dict) and msg.get("v") == FORMAT2:
        return depuis_message2(msg)
    return depuis_message(msg)


def message_complet(msg) -> bool:
    """Ce message « rando » annonce-t-il autre chose qu'une table du palier 1 (randomizer complet, ou un format plus
    récent que ces outils) ? Une telle table REFUSÉE ne se joue pas avec les coffres d'origine : ils donneraient les
    objets d'origine et ceux de la table, de progression, seraient perdus pour toute la partie."""
    return isinstance(msg, dict) and msg.get("v") != FORMAT


def pack_permis2_octets() -> bytes:
    """Liste d'objets du mode complet du pack (256 octets, 1 = permis), copie exacte de PERMIS2."""
    return bytes(1 if k in PERMIS2 else 0 for k in range(256))


def pack_permis4_octets() -> bytes:
    """Liste d'objets du stub N1 quand la zone des coffres en conflit est chargée (wwmp_rnd4_permis du pack, 256
    octets, 1 = permis), copie exacte de PERMIS4."""
    return bytes(1 if k in PERMIS4 else 0 for k in range(256))


def ecrire_spoiler(table, dossier: str, d: Donnees | None = None) -> str:
    """Spoiler (texte + JSON) dans ``dossier`` : spoiler_<empreinte>.txt / .json. Renvoie le chemin du .txt."""
    d = d or donnees()
    os.makedirs(dossier, exist_ok=True)
    base = os.path.join(dossier, f"spoiler_{table.empreinte}")
    for ext, contenu in ((".txt", table.spoiler_texte(d)),
                         (".json", json.dumps({"graine": table.graine, "empreinte": table.empreinte,
                                               "donnees": table.version,
                                               "algo": ALGO2 if table.mode == "complet" else ALGO,
                                               **({"options": table.options, "indices": table.indices,
                                                   "indices_en": table.indices_en,
                                                   "reglages": _chaine_de(table)}
                                                  if table.mode == "complet" else {}),
                                               "lieux": [{"lieu": l, "objet": n, "origine": o}
                                                         for l, n, o in table.spoiler(d, "fr")]},
                                              ensure_ascii=False, indent=1) + "\n")):
        tmp = base + ext + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(contenu)
        os.replace(tmp, base + ext)
    return base + ".txt"


def dernier_spoiler(dossier: str, emp: str | None = None) -> str | None:
    """Spoiler de l'empreinte ``emp`` dans ``dossier`` (sinon le plus récent) ; None s'il n'y en a pas."""
    try:
        noms = [n for n in os.listdir(dossier) if n.startswith("spoiler_") and n.endswith(".txt")]
    except OSError:
        return None
    if emp:
        n = f"spoiler_{emp}.txt"
        return os.path.join(dossier, n) if n in noms else None
    if not noms:
        return None
    return max((os.path.join(dossier, n) for n in noms), key=lambda p: os.path.getmtime(p))


# ---------------------------------------------------------------------------------------------------------------
# Côté client : table chargée dans le pack (jeu/mailbox.py, bit 5 des capacités)
# ---------------------------------------------------------------------------------------------------------------
class RandoClient:
    """Thread de travail du client. ``voulu`` vient du serveur : une Table (partie rando), None (partie sans
    randomizer : la table du pack est vidée, état 0) ou ``INCONNU`` (pas encore de welcome : rien ne bouge).
    ``step(mb)`` relit l'en-tête du pack au plus toutes les ``periode`` secondes et (re)charge la table quand il
    le faut (Cemu relancé : codecave remise à zéro ; autre partie dans le même Cemu : autre empreinte)."""

    INCONNU = object()

    def __init__(self, enabled: bool = True, periode: float = 2.0, cadeaux_perdus: bool = False):
        self.enabled = enabled
        self.periode = periode
        # relecture adverse (étape 3) : ce PC partage la progression (donc les bits « déjà donné » des cadeaux, lus dans
        # le RPX pour les enchères : octets 0x0F et 0x10, dans la plage ev:0x00-0x5F) SANS l'inventaire partagé : un
        # cadeau pris par un autre joueur est alors fermé ici sans que son objet arrive. Le client le dit à la table.
        self.cadeaux_perdus = bool(cadeaux_perdus)
        self.voulu = RandoClient.INCONNU
        self.t_verif = float("-inf")
        self.notes: list[str] = []
        self.etat_pack: dict | None = None    # dernier en-tête lu
        self.cap: bool | None = None          # pack avec la capacité (None : boîte pas encore lue)
        self.cap2: bool | None = None         # pack qui sait le randomizer complet (bit 6, liste d'objets attendue)
        self.cap3: bool | None = None         # pack qui sait changer les cadeaux de personnages (bit 7, zone trouvée)
        self.etat_n2: dict | None = None      # dernier en-tête lu de la zone des cadeaux
        self.cap4: bool | None = None         # pack qui sait les coffres en conflit (zone wwmp_rnd4, liste attendue)
        # lot 4 du portage : objets progressifs. cap5 : pack avec la zone wwmp_prg et la liste de paliers attendue ;
        # prog_partage : ce PC partage coffres ET inventaire (sans eux, le serveur ne compterait pas ses lieux ou ne
        # pourrait pas corriger son inventaire : posé par wwmp_client) ; groupe : groupe de partage de ce PC ;
        # prog_emp / prog_groupes : derniers comptes reçus du serveur ({groupe: {famille: lieux ouverts}})
        self.cap5: bool | None = None
        self.prog_partage = True
        self.groupe = "default"
        self.prog_emp: str | None = None
        self.prog_groupes: dict = {}
        self.etat_prog: dict | None = None    # dernier en-tête lu de la zone des objets progressifs
        # lot 5 du portage : objets de donjon hors de leur donjon. hors_partage : ce PC partage les petites clés ET
        # les coffres (posé par wwmp_client : sans eux, le serveur ne verrait pas ses coffres ou ne pourrait pas lui
        # donner la clé) ; hors_emp / hors_groupes : coffres étrangers déjà ouverts selon le serveur
        # ({groupe: {(index, numéro)}}) ; hors_poses : bits posés par ce client (compte, pour l'état affiché)
        self.hors_partage = True
        self.hors_emp: str | None = None
        self.hors_groupes: dict = {}
        self.hors_poses = 0
        self._hors_dits: set = set()
        # hors_recu : état complet reçu depuis la dernière connexion ; _hors_vus : coffres étrangers ouverts dans MA
        # sauvegarde que le serveur ne connaît pas ({(index, numéro): (premier vu, dernier rapport)})
        self.hors_recu = False
        self._hors_vus: dict = {}
        # mode Hanteurs (fusion) : coffres étrangers vus ouverts ici pendant que le monde persistant m'était fermé
        # (Hanteur, possédé, partie en quarantaine) : jamais signalés par le rattrapage, même après le retour en
        # Link et même après une reconnexion (gardés tant que ce client tourne ; voir ``hors_a_signaler``)
        self._hors_muets: set = set()
        # étape 5 (mutisme persistant) : coffres muets que le serveur garde déjà pour moi (reçus avec l'état complet)
        # et ceux déjà envoyés depuis cet état ; les autres partent dans « rando_muet » (``muets_message``)
        self._muets_serveur: set = set()
        self._muets_envoyes: set = set()
        self.etat_conflit: dict | None = None  # dernier en-tête lu de la zone des coffres en conflit
        self.charges = 0
        self.refus = ""
        self._prevenu_cap = False
        # lot 1 du portage : ce client sait écrire les lieux « donnée » (None : pas encore essayé ; False : témoins
        # du jeu absents, rien n'est écrit : voir rando/donnees.py)
        self.don: bool | None = None
        # randomizer par fichiers (rando/fichiers.py, étape 1) : zone WWMPRNDR du pack WWMP_Rando lue dans la
        # mémoire du jeu (LECTURE SEULE). rpack : mot de format (None : pas encore cherché ; 0 : pack absent) ; rplan :
        # empreinte du plan du pack GÉNÉRÉ ("" : pack du dépôt ou absent) ; rfich : empreinte des fichiers du pack
        # installé, recalculée au lancement du client (posée par wwmp_client, "" : aucun pack généré installé)
        self.rpack: int | None = None
        self.rplan = ""
        self.rfich = ""
        self._rndr_t = float("-inf")
        self._rndr_mb = None

    # -- réseau
    def hello(self) -> dict:
        e = self.etat_pack or {}
        return {"cap": int(bool(self.cap)), "cap2": int(bool(self.cap2)), "cap3": int(bool(self.cap3)),
                "don": int(self.don is not False), "cap4": int(bool(self.cap4)),
                # étape 5 (rando/lot9.py) : ce client connaît les lieux « donnée » ajoutés après le lot 1 (Kane,
                # autres lettres, enchère à 100 rubis) et garde le mutisme des coffres étrangers sur le serveur
                "don9": int(self.don is not False), "muet": 1,
                "cap5": int(bool(self.cap5) and self.prog_partage),
                "cap6": int(bool(self.hors_partage)),
                "rpack": int(self.rpack or 0), "rplan": self.rplan, "rfich": self.rfich,
                "emp": e.get("emp_hex", "") if e.get("etat") in (ETAT_SUR, ETAT_COMPLET) else ""}

    RNDR_PERIODE = 60.0                   # pack WWMP_Rando absent : nouvelle recherche au plus toutes les 60 s

    def lire_rndr(self, mb, now: float) -> None:
        """Zone WWMPRNDR du pack WWMP_Rando (randomizer par fichiers) : cherchée une fois par boîte aux lettres (un
        Cemu relancé = une autre boîte = nouvelle recherche), puis toutes les RNDR_PERIODE s tant qu'elle est absente.
        Lecture seule ; une erreur de lecture vaut « absent »."""
        mem = getattr(mb, "mem", None)
        if mem is None:
            return
        if self._rndr_mb is mb:
            if self.rpack or now - self._rndr_t < self.RNDR_PERIODE:
                return
            # absent : on ne recherche de nouveau que pour une partie qui EXIGE le pack généré (sinon une recherche
            # dans la mémoire du jeu toutes les minutes pour rien)
            from rando import fichiers as rando_fichiers
            v = self.voulu
            if not (isinstance(v, TABLES) and rando_fichiers.fichiers_requis(getattr(v, "options", None))):
                return
        self._rndr_mb, self._rndr_t = mb, now
        try:
            from rando import fichiers as rando_fichiers
            z = rando_fichiers.lire_pack_memoire(mem)
            # portage natif : une partie qui demande du code greffé (rando/greffe.py) n'est jouée que si CETTE greffe
            # est chargée (sinon : comme un pack absent, le serveur refuse avec le message à suivre)
            if z and z.get("genere") and rando_fichiers.greffe_chargee(mem, z["plan"]) is False:
                z = None
        except Exception:
            z = None
        self.rpack = int(z["format"]) if z else 0
        self.rplan = z["plan"] if z else ""

    def on_welcome(self, msg: dict) -> None:
        if not self.enabled:
            return
        r = msg.get("rando")
        if not isinstance(r, dict):
            self.voulu = None                     # partie sans randomizer (ou serveur ancien)
        elif isinstance(self.voulu, TABLES) and self.voulu.empreinte == r.get("emp"):
            pass                                  # même table : rien à refaire
        else:
            self.voulu = RandoClient.INCONNU      # la table arrive dans le message « rando »
        self.t_verif = float("-inf")
        self.hors_recu = False                    # lot 5 : l'état des coffres étrangers est renvoyé après la table
        self._hors_vus = {}

    def on_message(self, msg: dict) -> None:
        if not self.enabled:
            return
        try:
            t = table_depuis_message(msg)
        except ErreurRando as e:
            self.refus = str(e)
            self.voulu = None                     # table refusée : partie jouée SANS randomizer (sûr)
            self.notes.append(f"randomizer : table du serveur REFUSÉE ({e}) ; les coffres restent ceux du jeu "
                              "(mets les outils à jour des deux côtés)")
            self.t_verif = float("-inf")
            return
        if not (isinstance(self.voulu, TABLES) and self.voulu.empreinte == t.empreinte):
            n2 = len(getattr(t, "entrees_n2", ()))
            self.notes.append(f"randomizer : table reçue (empreinte {t.empreinte}, {len(t.entrees)} coffres"
                              + (f", {n2} cadeaux de personnages" if n2 else "")
                              + (", randomizer complet : objets de progression compris" if t.mode == "complet"
                                 else "") + ")")
            if t.mode == "complet" and t.res is None:
                self.notes.append("randomizer : cette table a été calculée avec d'autres données de logique que celles "
                                  "de ce PC : gardée telle quelle (forme et liste d'objets revérifiées seulement)")
            if self.cadeaux_perdus and getattr(t, "n2_requis", False):
                self.notes.append("randomizer : ATTENTION, cette partie mélange des cadeaux de personnages et ce PC "
                                  "partage la progression SANS l'inventaire (--no-inventory) : un cadeau pris par un "
                                  "autre joueur ne te sera plus donné et son objet ne t'arrivera pas (objet de "
                                  "progression perdu pour toi). Retire --no-inventory, ou prends chaque cadeau "
                                  "toi-même AVANT les autres")
        self.refus = ""
        self.voulu = t
        self.t_verif = float("-inf")

    def on_prog(self, msg: dict) -> None:
        """Message « rando_prog » du serveur : nombre de lieux progressifs déjà ouverts, par groupe et par famille
        ({"emp": empreinte, "groupes": {groupe: {famille: n}}, "tout": 1 pour un état complet}). Le serveur fait foi
        (un compte peut redescendre : registre des coffres remis à zéro). Tout champ douteux est ignoré."""
        if not self.enabled or not isinstance(msg, dict):
            return
        emp, groupes = msg.get("emp"), msg.get("groupes")
        if not isinstance(emp, str) or not isinstance(groupes, dict):
            return
        connues = {nom for nom, _p in FAMILLES_PROG}
        propre = {}
        for g, c in list(groupes.items())[:64]:
            if isinstance(g, str) and isinstance(c, dict):
                propre[g[:32]] = {f: n for f, n in c.items()
                                  if f in connues and _int(n) and 0 <= n <= PROG_COMPTE_MAX}
        emp = emp[:16]
        if emp != self.prog_emp or msg.get("tout") in (1, True):
            self.prog_groupes = {}
        self.prog_emp = emp
        self.prog_groupes.update(propre)
        self.t_verif = float("-inf")              # carte du pack refaite au tick suivant

    def on_hors(self, msg: dict) -> None:
        """Message « rando_hors » du serveur (lot 5) : coffres étrangers déjà ouverts par chaque groupe
        ({"emp": empreinte, "groupes": {groupe: [[index, numéro], ...]}, "tout": 1 pour un état complet}). Le serveur
        fait foi. Tout champ douteux est ignoré (rien n'est écrit dans le jeu ici : voir ``hors_tick``)."""
        if not self.enabled or not isinstance(msg, dict):
            return
        emp, groupes = msg.get("emp"), msg.get("groupes")
        if not isinstance(emp, str) or not isinstance(groupes, dict):
            return
        propre = {}
        for g, liste in list(groupes.items())[:64]:
            if isinstance(g, str) and isinstance(liste, list):
                propre[g[:32]] = {(c[0], c[1]) for c in liste[:HORS_MAX]
                                  if isinstance(c, list) and len(c) == 2 and _int(c[0]) and _int(c[1])
                                  and 0 <= c[0] < N_IDX and 0 <= c[1] < N_TBOX}
        emp = emp[:16]
        if emp != self.hors_emp or msg.get("tout") in (1, True):
            self.hors_groupes = {}
        if msg.get("tout") in (1, True):
            # étape 5 : coffres muets gardés par le serveur pour moi (client relancé : il s'en souvient pour moi).
            # Lus AVANT que ``hors_recu`` permette le rattrapage : un coffre muet n'est jamais signalé.
            v = self.voulu
            hors_v = getattr(v, "hors", {}) if isinstance(v, TABLES) and getattr(v, "empreinte", None) == emp else {}
            muets = msg.get("muets")
            self._muets_serveur = set()
            self._muets_envoyes = set()
            for c in (muets if isinstance(muets, list) else [])[:4096]:
                if isinstance(c, list) and len(c) == 2 and _int(c[0]) and _int(c[1]) and (c[0], c[1]) in hors_v:
                    self._muets_serveur.add((c[0], c[1]))
                    self._hors_muets.add((c[0], c[1]))
                    self._hors_vus.pop((c[0], c[1]), None)
            self.hors_recu = True
        self.hors_emp = emp
        self.hors_groupes.update(propre)

    HORS_ATTENTE = 5.0     # s : délai avant de signaler un coffre étranger ouvert ici et inconnu du serveur
    HORS_REDIT = 15.0      # s : entre deux rapports du même coffre, tant que le serveur ne le liste pas

    def hors_a_signaler(self, vue, now: float | None = None, monde_ok: bool = True) -> list:
        """Rapports « coffre » à envoyer pour les coffres étrangers OUVERTS dans ma sauvegarde que le serveur ne
        connaît pas (lot 5). Cas visé : coffre ouvert pendant que le client ne tournait pas (au retour, les coffres
        déjà ouverts servent de base muette aux coffres partagés : jamais signalés), ou rapport perdu. Sans ce
        rattrapage la petite clé qu'il vaut ne serait jamais créditée, et une porte resterait fermée. Le serveur
        garde le premier arrivé par coffre et ne crédite qu'une fois : signaler un coffre déjà connu est sans effet.
        Attend ``HORS_ATTENTE`` (le rapport normal des coffres partagés passe d'abord), puis redit toutes les
        ``HORS_REDIT`` secondes tant que le serveur ne liste pas le coffre. Lecture seule ; rien sans l'état complet
        du serveur pour cette table, ni si la table n'est pas chargée dans le jeu.

        ``monde_ok`` faux (mode Hanteurs : Hanteur, possédé ou partie en quarantaine, voir hanteurs.monde_ok) : rien
        n'est signalé, et tout coffre étranger vu ouvert ici sans que le serveur le connaisse devient MUET pour la
        vie de ce client. Sans cela, un coffre ouvert hors du monde persistant y entrerait au retour en Link par ce
        rattrapage (qui relit la sauvegarde), et déclencherait le don de la clé au groupe."""
        v = self.voulu
        if not self.enabled or not self.hors_partage or not self.hors_recu or not isinstance(v, TABLES) \
                or not getattr(v, "hors_requis", False) or vue is None or not getattr(vue, "ok", False) \
                or self.hors_emp != v.empreinte:
            return []
        e = self.etat_pack or {}
        if e.get("etat") != v.etat or e.get("emp") != v.emp_u32:
            return []
        now = time.monotonic() if now is None else now
        serveur = self.hors_groupes.get(self.groupe, set())
        out = []
        for cle in sorted(v.hors):
            if cle not in vue.tbox or cle in serveur:
                self._hors_vus.pop(cle, None)
                continue
            if not monde_ok:
                self._hors_taire(cle)
                continue
            if cle in self._hors_muets:
                continue
            vu, dit = self._hors_vus.get(cle, (now, None))
            if now - vu >= self.HORS_ATTENTE and (dit is None or now - dit >= self.HORS_REDIT) and len(out) < 2:
                if dit is None:
                    self.notes.append(f"randomizer : le coffre {cle[1]} (index {cle[0]}) est ouvert dans ta "
                                      "sauvegarde et le serveur ne le savait pas : signalé (il contient un objet "
                                      "d'un autre donjon, donné par le serveur)")
                dit = now
                out.append({"type": "coffre", "group": self.groupe, "k": "t", "i": cle[0], "n": cle[1],
                            "item": OBJET_HORS, "stage": "", "room": -1})
            self._hors_vus[cle] = (vu, dit)
        return out

    def _hors_taire(self, cle) -> None:
        self._hors_vus.pop(cle, None)
        if len(self._hors_muets) < 4096:
            self._hors_muets.add(cle)

    def muets_message(self) -> dict | None:
        """Étape 5 (mutisme persistant) : message « rando_muet » des coffres muets que le serveur ne garde pas encore
        pour moi (None : rien à dire). Le serveur les garde par pseudo et pour cette table, les renvoie à chaque
        connexion (``on_hors``) et refuse le rapport « coffre » de ce joueur pour eux : un client relancé ne les
        signale donc jamais (aucune clé donnée au groupe)."""
        v = self.voulu
        if not self.enabled or not self.hors_recu or not isinstance(v, TABLES) \
                or not getattr(v, "hors_requis", False) or self.hors_emp != v.empreinte:
            return None
        neufs = sorted(k for k in self._hors_muets if k in v.hors and k not in self._muets_serveur
                       and k not in self._muets_envoyes)[:64]
        if not neufs:
            return None
        self._muets_envoyes.update(neufs)
        return {"type": "rando_muet", "group": self.groupe, "emp": v.empreinte, "c": [list(k) for k in neufs]}

    def hors_taire(self, vue) -> int:
        """Mode Hanteurs, monde persistant fermé à ce joueur (Hanteur, possédé, quarantaine) : rend MUET, pour la
        vie de ce client, tout coffre étranger de la table voulue vu ouvert dans ``vue`` et que le serveur ne liste
        pas pour mon groupe. À appeler à CHAQUE tick tant que le monde est fermé, même pendant un événement (scène
        d'ouverture du coffre, texte « objet obtenu ») : sinon un coffre ouvert à la fin d'une potion, dont le
        texte reste affiché au-delà de la quarantaine, ne serait vu qu'après le retour en Link et serait signalé par
        le rattrapage (clé donnée au groupe, entrée dans le monde gardé). Moins de gardes que ``hors_a_signaler`` :
        ni l'état complet du serveur ni la table chargée dans le jeu ne sont exigés (un coffre connu du serveur
        n'est de toute façon jamais signalé). Lecture seule ; renvoie le nombre de coffres muets."""
        v = self.voulu
        if not self.enabled or not isinstance(v, TABLES) or not getattr(v, "hors_requis", False) \
                or vue is None or not getattr(vue, "ok", False):
            return len(self._hors_muets)
        serveur = self.hors_groupes.get(self.groupe, set()) if self.hors_emp == v.empreinte else set()
        for cle in v.hors:
            if cle in vue.tbox and cle not in serveur:
                self._hors_taire(cle)
        return len(self._hors_muets)

    def hors_a_poser(self, vue) -> list:
        """Bits d'objets de donjon à poser dans la sauvegarde de ce joueur (lot 5) : [(donjon, bit, objet, (index,
        numéro))] pour chaque coffre étranger de la table voulue qui vaut une grande clé, une carte ou une boussole,
        qui est OUVERT (selon le serveur pour mon groupe, ou dans ma propre sauvegarde : bit du coffre posé) et dont
        le bit du bon donjon manque encore. ``vue`` : coffres.ChestView lue à l'instant. Lecture seule."""
        v = self.voulu
        if not self.enabled or not isinstance(v, TABLES) or not getattr(v, "hors_requis", False) \
                or vue is None or not getattr(vue, "ok", False):
            return []
        e = self.etat_pack or {}
        if e.get("etat") != v.etat or e.get("emp") != v.emp_u32:
            return []                             # table pas (encore) chargée dans ce jeu : rien
        serveur = self.hors_groupes.get(self.groupe, set()) if self.hors_emp == v.empreinte else set()
        out = []
        for cle, (donjon, bit, objet) in sorted(v.hors_bits().items()):
            if (cle in serveur or cle in vue.tbox) and (donjon, bit) not in vue.dungeon:
                out.append((donjon, bit, objet, cle))
        return out

    def hors_tick(self, mem, vue, can_write: bool, settled: bool, applied: dict | None = None) -> dict:
        """Pose dans la sauvegarde les bits des grandes clés, cartes et boussoles de donjon trouvées dans un coffre
        étranger (lot 5). Mêmes gardes que les coffres partagés (ChestGrant._bit_only) : vue cohérente, contexte
        stable, jeu libre relu juste avant d'écrire ; écriture « lire, OU, écrire » d'un seul bit, dans la copie
        sauvegardée du donjon et dans la copie vivante si c'est le niveau chargé. Idempotent (un bit déjà posé n'est
        jamais réécrit). Renvoie les bits posés ({"mem<i>": {décalage: masque}}) pour KeySync.note_applied."""
        written: dict = {}
        a_poser = self.hors_a_poser(vue)
        if not a_poser or mem is None or not can_write or not settled or vue.busy:
            return written
        from jeu import progress as prog
        from synchro import coffres
        if coffres.game_free(mem):                # relu juste avant d'écrire
            return written
        for donjon, bit, objet, cle in a_poser:
            if applied is not None:
                applied[("d", donjon, bit)] = time.monotonic()
            for va, mask in coffres.bit_addresses(vue, "d", donjon, bit):
                prog._rmw(mem, va, mask)
            coffres._add_written(written, "d", donjon, bit)
            self.hors_poses += 1
            if (donjon, bit) not in self._hors_dits and len(self._hors_dits) < 256:
                self._hors_dits.add((donjon, bit))
                self.notes.append(f"randomizer : le coffre {cle[1]} (index {cle[0]}) contenait un objet d'un autre "
                                  f"donjon : {nom_hors(objet, donjon)} ; ajouté à ta sauvegarde")
        return written

    def prog_paliers(self, idx, tbox):
        """Paliers de la famille si le coffre (index de sauvegarde, numéro) est un lieu progressif de la table
        voulue, sinon None (coffres partagés : voir ChestGrant.prog)."""
        v = self.voulu
        if not self.enabled or not isinstance(v, TABLES) or not getattr(v, "prog_requis", False):
            return None
        lieu = v.prog_lieu(idx, tbox)
        return lieu[1] if lieu else None

    def prog_comptes(self) -> dict | None:
        """Comptes de MON groupe pour la table voulue, ou None tant que le serveur ne les a pas envoyés."""
        v = self.voulu
        if not isinstance(v, TABLES) or self.prog_emp != v.empreinte:
            return None
        c = self.prog_groupes.get(self.groupe)
        return dict(c) if isinstance(c, dict) else {}

    def reset_network(self) -> None:
        """Connexion perdue : la table chargée reste (la partie continue), rien n'est vidé."""

    # -- jeu
    def step(self, mb, now: float | None = None) -> None:
        if mb is None:
            return
        now = time.monotonic() if now is None else now
        if now - self.t_verif < self.periode:
            return
        self.t_verif = now
        self.lire_rndr(mb, now)
        cap = bool(getattr(mb, "rando", False))
        if cap:
            try:
                cap = mb.rando_addr() is not None      # bit 5 annoncé mais zone introuvable : traité comme absent
            except Exception:
                cap = False
        self.cap = cap
        cap2 = False
        if cap and getattr(mb, "rando2", False):
            try:                                       # bit 6 annoncé : la liste du pack doit être CELLE de ces outils
                cap2 = mb.rando2_permis() == pack_permis2_octets()
            except Exception:
                cap2 = False
        self.cap2 = cap2
        cap3 = False
        if cap2 and getattr(mb, "rando3", False):
            try:                                       # bit 7 annoncé mais zone introuvable : traité comme absent
                cap3 = mb.rando3_addr() is not None
            except Exception:
                cap3 = False
        self.cap3 = cap3
        cap4 = False
        if cap2 and hasattr(mb, "rando4_addr"):
            try:                                       # zone des coffres en conflit : trouvée ET liste de ces outils
                cap4 = mb.rando4_addr() is not None and mb.rando4_permis() == pack_permis4_octets()
            except Exception:
                cap4 = False
        self.cap4 = cap4
        cap5 = False
        if cap2 and hasattr(mb, "prog_addr"):
            try:                                       # zone des objets progressifs : trouvée ET liste de ces outils
                cap5 = mb.prog_addr() is not None and mb.prog_permis() == pack_permis_prog_octets()
            except Exception:
                cap5 = False
        self.cap5 = cap5
        if not self.enabled:
            # --no-rando (relecture adverse) : une table chargée plus tôt dans CE Cemu (autre session du client) resterait
            # active (état 1) et le stub changerait encore les coffres : on la vide (état 0, données seulement)
            if cap and mb.rando_lire()["etat"] != 0:
                mb.rando_vider()
                self.notes.append("randomizer coupé (--no-rando) : table laissée par une session précédente vidée "
                                  "(coffres d'origine)")
            if cap5:
                mb.prog_vider()
            return
        if not cap:
            if isinstance(self.voulu, TABLES) and not self._prevenu_cap:
                self._prevenu_cap = True
                self.notes.append("randomizer : le graphic pack installé ne sait pas changer les coffres (il faut la "
                                  "v19 du dépôt avec le randomizer : python tools/install_graphicpack.py, puis "
                                  "relancer Cemu) ; tes coffres restent ceux du jeu")
            return
        e = mb.rando_lire()
        self.etat_pack = e
        v = self.voulu
        if v is RandoClient.INCONNU:
            return
        if v is None:
            if e["etat"] != 0:
                mb.rando_vider()
                self.notes.append("randomizer : partie sans randomizer, table du jeu vidée (coffres d'origine)")
            if cap5:
                mb.prog_vider()
            return
        if v.etat == ETAT_COMPLET and not cap2:
            # randomizer complet et pack sans le bit 6 (ou avec une autre liste d'objets) : rien n'est chargé, et une
            # table restée d'une autre partie est vidée (jamais un mélange de deux tables)
            if e["etat"] != 0:
                mb.rando_vider()
                self.etat_pack = mb.rando_lire()
            if not self._prevenu_cap:
                self._prevenu_cap = True
                self.notes.append("randomizer complet : le graphic pack installé ne sait changer que les objets sans "
                                  "progression (il faut la v19 du dépôt avec le randomizer complet : python "
                                  "tools/install_graphicpack.py, puis relancer Cemu) ; tes coffres restent ceux du "
                                  "jeu et le serveur refuse ta partie tant que le pack n'est pas à jour")
            return
        n2 = v.octets_n2() if v.etat == ETAT_COMPLET else b""
        if n2 and not cap3:
            # la table change des cadeaux de personnages et le pack n'a pas le stub N2 : rien n'est chargé (les coffres
            # seuls donneraient une moitié de table : objets perdus ou en double), table d'une autre partie vidée
            if e["etat"] != 0:
                mb.rando_vider()
                self.etat_pack = mb.rando_lire()
            if not self._prevenu_cap:
                self._prevenu_cap = True
                self.notes.append("randomizer complet : cette partie mélange aussi les cadeaux de personnages et le "
                                  "graphic pack installé ne sait pas les changer (il faut la v19 du dépôt à jour : "
                                  "python tools/install_graphicpack.py, puis relancer Cemu) ; rien n'est changé dans "
                                  "ton jeu et le serveur refuse ta partie tant que le pack n'est pas à jour")
            return
        conf = v.octets_conflit() if v.etat == ETAT_COMPLET else b""
        if conf and not cap4:
            # la table change des coffres en conflit et le pack n'a pas la zone wwmp_rnd4 : rien n'est chargé (ces
            # coffres resteraient d'origine et leurs objets, placés ailleurs, seraient refusés par le stub : objets
            # perdus ou en double), table d'une autre partie vidée
            if e["etat"] != 0:
                mb.rando_vider()
                self.etat_pack = mb.rando_lire()
            if not self._prevenu_cap:
                self._prevenu_cap = True
                self.notes.append("randomizer complet : cette partie mélange aussi les coffres « en conflit » (clé "
                                  "partagée entre deux niveaux) et le graphic pack installé ne sait pas les changer "
                                  "(il faut la v19 du dépôt à jour : python tools/install_graphicpack.py, puis "
                                  "relancer Cemu) ; rien n'est changé dans ton jeu et le serveur refuse ta partie "
                                  "tant que le pack n'est pas à jour")
            return
        if v.etat == ETAT_COMPLET and getattr(v, "hors_requis", False) and not self.hors_partage:
            # lot 5 : la table place des objets de donjon hors du leur et ce PC ne partage pas les petites clés et les
            # coffres (le serveur ne verrait pas ses coffres, ou ne pourrait pas lui donner la clé) : rien n'est chargé
            if e["etat"] != 0:
                mb.rando_vider()
                self.etat_pack = mb.rando_lire()
            if not self._prevenu_cap:
                self._prevenu_cap = True
                self.notes.append("randomizer complet : cette partie place des clés ou des cartes de donjon hors de "
                                  "leur donjon (créditées par le serveur) et ce PC ne partage pas les petites clés ET "
                                  "les coffres (retire --no-keys et --no-chests) ; rien n'est changé dans ton jeu et "
                                  "le serveur refuse ta partie en attendant")
            return
        prog = v.etat == ETAT_COMPLET and bool(getattr(v, "prog_requis", False))
        if prog and not (cap5 and self.prog_partage):
            # la table a des objets progressifs et ce PC ne peut pas les jouer (pack sans la zone wwmp_prg, ou coffres
            # ou inventaire non partagés : le compte commun ne tiendrait pas) : rien n'est chargé, comme plus haut
            if e["etat"] != 0:
                mb.rando_vider()
                self.etat_pack = mb.rando_lire()
            if not self._prevenu_cap:
                self._prevenu_cap = True
                self.notes.append("randomizer complet : cette partie a des objets progressifs (arc puis flèches de "
                                  "lumière, compte commun au groupe) et "
                                  + ("le graphic pack installé ne sait pas les donner (il faut la v19 du dépôt à jour : "
                                     "python tools/install_graphicpack.py, puis relancer Cemu)" if not cap5 else
                                     "ce PC ne partage pas les coffres ET l'inventaire (retire --no-chests et "
                                     "--no-inventory)")
                                  + " ; rien n'est changé dans ton jeu et le serveur refuse ta partie en attendant")
            return
        if prog:
            comptes = self.prog_comptes()
            if comptes is None:
                # comptes du serveur pas encore reçus (ils suivent la table) : rien n'est chargé avant, sinon le
                # premier coffre créé donnerait le palier 1 même si le groupe l'a déjà ; table d'une autre partie vidée
                if e["etat"] != 0 and e["emp"] != v.emp_u32:
                    mb.rando_vider()
                    self.etat_pack = mb.rando_lire()
                return
            # carte écrite AVANT la table : tant que l'empreinte de wwmp_rnd n'est pas celle de la carte, le pack
            # l'ignore ; une fois la table chargée, la carte est déjà la bonne
            if not mb.prog_ecrire(v.octets_prog(comptes), v.emp_u32):
                if e["etat"] != 0:
                    mb.rando_vider()
                    self.etat_pack = mb.rando_lire()
                self.notes.append("randomizer : écriture de la carte des objets progressifs refusée ou relue fausse : "
                                  "rien n'est chargé (coffres d'origine), nouvel essai dans un instant")
                return
        elif cap5:
            mb.prog_vider()
        self.etat_prog = mb.prog_lire() if cap5 else None
        e4 = mb.rando4_lire() if cap4 else None
        self.etat_conflit = e4
        if e4 is None or (not conf and e4["etat"] == 0):
            conf_bon = True
        else:
            conf_bon = bool(conf) and e4["etat"] == 1 and e4["n"] == len(conf) // 8 and e4["emp"] == v.emp_u32 \
                and e4["table"] == conf + bytes(len(e4["table"]) - len(conf))
        e3 = mb.rando3_lire() if cap3 else None
        self.etat_n2 = e3
        if e3 is None or (not n2 and e3["etat"] == 0):
            n2_bon = True
        else:
            n2_bon = bool(n2) and e3["etat"] == 1 and e3["n"] == len(n2) // 8 and e3["emp"] == v.emp_u32 \
                and e3["table"] == n2 + bytes(len(e3["table"]) - len(n2))
        if e["etat"] == v.etat and e["emp"] == v.emp_u32 and n2_bon and conf_bon \
                and mb.rando_table() == v.octets_pack():
            return
        ancien = e["etat"] in (ETAT_SUR, ETAT_COMPLET)
        if mb.rando_charger(v.octets_pack(), v.emp_u32, v.etat, **({"n2": n2} if n2 else {}),
                            **({"conflit": conf} if conf else {})):
            self.charges += 1
            self.etat_pack = mb.rando_lire()
            self.etat_n2 = mb.rando3_lire() if cap3 else None
            self.etat_conflit = mb.rando4_lire() if cap4 else None
            self.notes.append(f"randomizer : table {v.empreinte} chargée dans le jeu"
                              + (f" ({len(n2) // 8} cadeaux de personnages changés)" if n2 else "")
                              + (f" ({len(conf) // 8} coffres en conflit changés)" if conf else "")
                              + (" (elle remplace une autre table : les coffres DÉJÀ chargés dans la salle gardent "
                                 "l'ancien contenu jusqu'au prochain changement de salle)" if ancien else ""))
        else:
            self.notes.append("randomizer : écriture de la table refusée ou relue fausse : coffres d'origine")

    def take_notes(self) -> list[str]:
        n, self.notes = self.notes, []
        return n

    def status(self) -> str:
        v = self.voulu
        if not self.enabled:
            return "coupé (--no-rando)"
        if v is RandoClient.INCONNU:
            return "en attente du serveur"
        if v is None:
            return "partie sans randomizer" + (f" (table refusée : {self.refus})" if self.refus else "")
        e = self.etat_pack or {}
        if self.cap is False:
            return f"table {v.empreinte} reçue, pack sans randomizer (coffres d'origine)"
        if v.etat == ETAT_COMPLET and self.cap2 is False:
            return f"table {v.empreinte} reçue, pack sans randomizer complet (coffres d'origine)"
        if v.etat == ETAT_COMPLET and v.n2_requis and self.cap3 is False:
            return f"table {v.empreinte} reçue, pack qui ne sait pas changer les cadeaux de personnages (rien n'est changé)"
        if v.etat == ETAT_COMPLET and getattr(v, "conflit_requis", False) and self.cap4 is False:
            return f"table {v.empreinte} reçue, pack qui ne sait pas changer les coffres en conflit (rien n'est changé)"
        prog = v.etat == ETAT_COMPLET and bool(getattr(v, "prog_requis", False))
        if prog and self.cap5 is False:
            return f"table {v.empreinte} reçue, pack qui ne sait pas les objets progressifs (rien n'est changé)"
        if prog and not self.prog_partage:
            return (f"table {v.empreinte} reçue, objets progressifs impossibles sans partager coffres et inventaire "
                    "(rien n'est changé)")
        if prog and self.prog_comptes() is None:
            return f"table {v.empreinte} reçue, en attente du compte des objets progressifs (serveur)"
        hors = v.etat == ETAT_COMPLET and bool(getattr(v, "hors_requis", False))
        if hors and not self.hors_partage:
            return (f"table {v.empreinte} reçue, objets de donjon hors de leur donjon impossibles sans partager "
                    "petites clés et coffres (rien n'est changé)")
        if e.get("etat") == v.etat and e.get("emp") == v.emp_u32:
            e3 = self.etat_n2 or {}
            return (("randomizer complet, " if v.etat == ETAT_COMPLET else "")
                    + f"table {v.empreinte} chargée ({len(v.entrees)} coffres) ; coffres créés {e.get('recherches', 0)}, "
                    f"changés {e.get('remplacements', 0)}"
                    + (f" ; cadeaux de personnages dans la table {e3.get('n', 0)}, donnés {e3.get('vus', 0)}, changés "
                       f"{e3.get('remplacements', 0)}" if v.etat == ETAT_COMPLET and v.n2_requis else "")
                    + (f" ; coffres en conflit dans la table {(self.etat_conflit or {}).get('n', 0)}, reconnus "
                       f"{(self.etat_conflit or {}).get('vus', 0)}"
                       if v.etat == ETAT_COMPLET and getattr(v, "conflit_requis", False) else "")
                    + (" ; objets progressifs : " + ", ".join(
                        f"{f} {n}/{len(dict(FAMILLES_PROG)[f])}" for f, n in sorted(
                            {**{f: 0 for f in v.prog_familles()}, **(self.prog_comptes() or {})}.items())
                        if f in v.prog_familles())
                       + f", coffres à palier traités {(self.etat_prog or {}).get('vus', 0)}" if prog else "")
                    + (f" ; objets de donjon hors de leur donjon : {len(v.hors)} coffres dans la table (petites clés "
                       f"par le compteur commun), bits posés ici {self.hors_poses}" if hors else ""))
        return f"table {v.empreinte} reçue, chargement dans le jeu en cours"


# ---------------------------------------------------------------------------------------------------------------
# Filet Python (--rando-filet, désactivé par défaut) : coffre chargé AVANT la table (course au démarrage)
# ---------------------------------------------------------------------------------------------------------------
OFF_ITEM = 0x2FC            # octet fort de home.angle.z (objet) ; +0x2FD = switch d'ouverture (jamais touché)


def coffre_en_retard(table, tbox: dict, cur_idx) -> tuple[int, int, int, int] | None:
    """Veille du randomizer complet (LECTURE SEULE) : un coffre fermé, présent dans la table, qui porte encore son
    objet d'origine alors que la table lui en donne un autre, a été créé AVANT le chargement de la table (client
    lancé alors que la salle était déjà chargée). L'ouvrir donnerait l'objet d'origine et ferait perdre à tous
    l'objet de progression de la table : le client prévient (sortir de la salle et revenir recrée le coffre).
    ``tbox`` : coffres.read_tbox. Renvoie (index, numéro, objet d'origine, objet de la table) ou None."""
    from synchro import coffres
    if table is None or not isinstance(tbox, dict) or tbox.get("action") != coffres.ACT_OPEN_WAIT:
        return None
    idx = coffres.save_idx(tbox["params"], cur_idx)
    if idx is None:
        return None
    no = coffres.tbox_no(tbox["params"])
    e = table.entrees.get((idx, no))
    if e is None or e[0] == e[1] or tbox.get("item") != e[0]:
        return None
    lieu = table.prog_lieu(idx, no) if hasattr(table, "prog_lieu") else None
    if lieu is not None and e[0] in lieu[1]:
        # lieu progressif dont l'objet d'origine est lui-même un palier de la famille : le pack y pose le palier du
        # compte commun, qui peut être celui d'origine ; ce coffre n'est pas « en retard »
        return None
    return idx, no, e[0], e[1]


def filet_cible(table: Table, tbox: dict, cur_idx, libre: bool) -> int | None:
    """Nouvel objet à écrire dans un coffre déjà créé, ou None. ``tbox`` : coffres.read_tbox ; ``libre`` : aucun
    événement en cours et Link loin du coffre (décidé par l'appelant). Gardes : coffre fermé en actionOpenWait,
    pas d'ouverture demandée (+0xF8 != 3) ni en cours (flag 0x10), clé dans la table, octet actuel = objet
    d'origine attendu, objets permis."""
    from synchro import coffres
    if table is None or not libre or not isinstance(tbox, dict):
        return None
    if tbox.get("action") != coffres.ACT_OPEN_WAIT or tbox.get("cmd") == 3 or (tbox.get("flags", 0) & coffres.FLAG_OPENING):
        return None
    idx = coffres.save_idx(tbox["params"], cur_idx)
    if idx is None:
        return None
    e = table.entrees.get((idx, coffres.tbox_no(tbox["params"])))
    if e is None:
        return None
    o, n = e
    cur = tbox.get("item")
    if cur != o or o == n or o not in PERMIS or n not in PERMIS or o in OBJETS_PROG or n in OBJETS_PROG:
        return None                  # (un palier d'objet progressif n'est jamais posé par le filet)
    return n


def pack_permis_octets() -> bytes:
    """Table des objets permis du pack (256 octets, 1 = permis), copie exacte de PERMIS."""
    return bytes(1 if k in PERMIS else 0 for k in range(256))


def struct_entete(raw: bytes) -> dict:
    fmt, etat, emp, rech, rempl, dernier = struct.unpack(">6I", raw[8:0x20])
    return {"format": fmt, "etat": etat, "emp": emp, "emp_hex": f"{emp:08x}", "recherches": rech,
            "remplacements": rempl, "dernier": dernier}
