"""Effets propres aux Hanteurs par le graphic pack (CALL 11, bit 11 des capacités ; --effets-pack).

Avant (et toujours sans l'option ou sans le bit) : l'invocation, la possession, la relâche, la potion, la libération et
l'échange « comme dans Crawl » sont montrés par la fumée de mort d'un monstre (daDisappear, synchro/hanteurs.py
EFFETS_FUMEE). Un Link peut croire que le monstre vient de mourir.

Avec le pack v19 à jour (bit 11) ET l'option ``--effets-pack`` du client : le pack joue une RECETTE constante, la même
table que ci-dessous, recopiée dans l'asm (``wwmp_effet_recettes``) : jusqu'à deux effets de particules du jeu
(``dPa_control_c::set`` ou ``setStripes``) et un son (``mDoAud_seStart``), à la position écrite en +0x40 de la boîte. Le
PC n'envoie que le NUMÉRO de recette : jamais un numéro d'effet, de son, ni un pointeur.

Choix des effets (relevés dans le cking.rpx sur les 1255 appels de ``set`` et les 1374 de ``mDoAud_seStart``) :

- 0x3EB / 0x3EC + son 0x692F : apparition d'un coffre (daTbox_c::demoInitAppear 0x024B67E4, seul site).
- 0x15 par setStripes : volutes d'esprit seules de daDisappear (0x0212480C), sans fumée ni éclair.
- 0x1F1 / 0x1F4 : lumières d'ouverture de coffre (daTbox 0x024B6494).
- Sons 0x5932, 0x5933, 0x107F : possession du Poe (0x0244FBD4, 0x0244FFF8, 0x0244FF14). Banque d'ennemi : sans
  doute MUETS hors des stages où un Poe est chargé [HYPO, non vu en jeu] ; ``--effets-pack-muet`` coupe tous les sons.

Refusés (INTERDITS, vérifié par les tests) : les étincelles de coup (0x0C, 0x0D, 0x0F, 0x10, 0x27B : un Link croirait le
monstre frappé), les effets de mort de daDisappear (0x13, 0x14, 0x16 : c'est ce qu'on veut éviter), 0x1F5 (gardé et
piloté par le coffre, peut-être sans fin) et tout effet >= 0x4000 (rappel par défaut, ou banque de la scène).

Tous les sites d'origine jettent le pointeur rendu par le jeu : ce sont des effets finis. Leur durée exacte et leur
aspect n'ont PAS été vus en jeu (docs/dev/tests/PLAN_TEST_SYNCHRO.md, EP1 à EP8).
"""
from __future__ import annotations

import struct

# n° de recette -> (effet 1, effet 2, son, drapeaux) ; 0 = rien ; drapeau STRIPES : effet 2 par setStripes
STRIPES = 0x01
RECETTES = (
    (0x03EB, 0x03EC, 0x692F, 0),          # 0 invocation
    (0x0000, 0x0015, 0x5932, STRIPES),    # 1 possession d'un monstre
    (0x0000, 0x0015, 0x5933, STRIPES),    # 2 relâche
    (0x01F4, 0x0015, 0x107F, STRIPES),    # 3 potion sur un Link
    (0x01F1, 0x01F4, 0x692F, 0),          # 4 libération
    (0x03EB, 0x0015, 0x5933, STRIPES),    # 5 échange Crawl (chute)
    (0x03EB, 0x03EC, 0x0000, 0),          # 6 apparition du nouveau Link
)
N_RECETTES = len(RECETTES)
# ce qui arrive (synchro/hanteurs.py EFFETS_FUMEE) -> recette
RECETTE = {"invoque": 0, "possede": 1, "lache": 2, "potion": 3, "libere": 4, "echange": 5, "apparition": 6}
# jamais dans une recette (voir l'en-tête)
INTERDITS = frozenset({0x0C, 0x0D, 0x0F, 0x10, 0x27B, 0x13, 0x14, 0x16, 0x1F5})
EFFET_MAX = 0x4000                        # effets < 0x4000 seulement (banque commune, sans rappel par défaut)
SONS = frozenset({0x692F, 0x5932, 0x5933, 0x107F})
COORD_MAX = float(1 << 20)                # le pack refuse |c| >= 2^20 (-4)
SANS_SON = 0x1                            # arg2 de CALL 11


def table_pack(recettes=RECETTES) -> bytes:
    """Octets de ``wwmp_effet_recettes`` : 8 octets par recette {u16, u16, u16, u8 drapeaux, u8 0}."""
    out = bytearray()
    for fx1, fx2, son, dr in recettes:
        for fx in (fx1, fx2):
            if fx and (fx in INTERDITS or not 0 < fx < EFFET_MAX):
                raise ValueError(f"effet 0x{fx:X} refusé")
        if son and son not in SONS:
            raise ValueError(f"son 0x{son:X} hors liste")
        if dr & ~STRIPES or (dr & STRIPES and not fx2):
            raise ValueError("drapeaux invalides")
        out += struct.pack(">HHHBB", fx1, fx2, son, dr, 0)
    return bytes(out)


def recette_de(quoi: str) -> int | None:
    """Recette du pack pour un effet de synchro/hanteurs.py, ou None (pas de recette : fumée)."""
    r = RECETTE.get(quoi)
    return r if r is not None and 0 <= r < N_RECETTES else None


def position_ok(pos) -> bool:
    """La position passera les gardes du pack (trois nombres finis, |c| < 2^20)."""
    try:
        p = [float(v) for v in pos]
    except (TypeError, ValueError):
        return False
    return len(p) == 3 and all(abs(v) < COORD_MAX for v in p)      # NaN : comparaison fausse, refusé
