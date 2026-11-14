"""Formats de fichiers de Wind Waker HD, en Python pur (randomizer par fichiers générés localement).

Portage partiel des lecteurs-écrivains C++ de TWWHD-Randomizer (``filetypes/``), de SuperDude88 et de ses
contributeurs, sous licence MIT, « Copyright (c) 2024 SuperDude88 » (texte complet :
tools/rando/logique/LICENSE_TWWHD.txt). Plan : docs/etudes/rando_fichiers.md §2.1.

Modules :

* ``yaz0``        : décodeur, encodeur déterministe, recompression de la FIN seulement (préfixe d'origine gardé) ;
* ``sarc``        : archive SARC, reconstruction qui garde ordre, hachages, tables et alignements ;
* ``bfres_inclus``: fichiers inclus d'un BFRES (groupe d'index 11), remplacement à décalages recalculés ;
* ``dzx``         : salles et stages (``room.dzr`` / ``stage.dzs``), chunks et entrées ;
* ``events``      : ``event_list.dat``, lecture et éditions à taille égale (actions retirées comprises) ;
* ``msbt``        : textes LMS (MSBT), lecture, remplacement d'un texte de n'importe quelle taille ;
* ``charts``      : ``cmapdat.bin`` (cartes de la carte marine), édition à taille égale (lot 8, cartes mélangées) ;
* ``chemin``      : chemins ``a.pack@SARC@b.szs@YAZ0@SARC@c.msbt@MSBT``, arbre de cache, remballage, feuille BRUT ;
* ``dump``        : contrôle du dump USA v0 (meta.xml, empreintes SHA-256 de ``empreintes_usa_v0.json``) ;
* ``gx2``         : textures Wii U : déswizzle GX2 (AddrLib), décodeurs BC1/BC3/BC4/BC5/RGBA8, PNG (icônes du trackeur) ;
* ``bflim``       : images BFLIM des mises en page (pied FLIM/imag, pitch contrôlé), converties en RGBA.

Règles : aucune écriture dans le dump (tout est lu, rien n'est ouvert en écriture) ; aucun horodatage ni ordre de
dictionnaire non fixé : mêmes entrées = mêmes octets, sur tous les PC.
"""

LICENCE = ("Portage de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT, "
           "Copyright (c) 2024 SuperDude88 : tools/rando/logique/LICENSE_TWWHD.txt")


class ErreurFormat(ValueError):
    """Fichier du jeu illisible ou édition impossible (jamais d'écriture à l'aveugle)."""
