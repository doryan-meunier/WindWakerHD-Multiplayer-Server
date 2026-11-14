"""Filtre des boss (SYNCHRO.md A9) : un boss n'est jamais synchronisé.

Chacun combat son propre boss (scripts et cinématiques propres à chaque boss : risque de
blocage si l'un des deux le pilotait comme une marionnette). Seul le RÉSULTAT est partagé,
par la progression (``mem:dungeon`` bit 3, boss vaincu).

Filtre principal = listes de procs et de stages (la critique a montré que le bit
``fopAcStts_BOSS_e`` à ``+0x2E0`` n'est pas fiable en HD : le champ d'état n'y est
peut-être pas, voir docs/archives/SYNCHRO_critique.md §1). Le bit d'état n'est donc PAS
utilisé tant qu'il n'a pas été vérifié en direct.

Un ennemi filtré n'est :
  * jamais envoyé dans le snapshot ``enemies`` (chez l'autorité) ;
  * jamais transformé en marionnette, ni supprimé comme « orphelin » (chez les autres),
    puisqu'il n'est plus dans la liste des ennemis locaux.
"""
from __future__ import annotations

# proc -> nom. Numéros lus en direct (table des noms d'acteurs du jeu HD, lecture seule,
# 2026-11-08) ; rôle de chaque acteur d'après le décompilé zeldaret/tww (d_a_<nom>.cpp).
BOSS_PROCS: dict[int, str] = {
    234: "Btd",      # Gohma (Caverne du Dragon)
    235: "Bkm",      # Kalle Demos (Bois Défendu) [synchro v3 : profil HD = d_a_bmd, docs/etudes/sync3_boss_pv.md]
    236: "Bmdhand",  # lianes du plafond de Kalle Demos (profil HD, sans nom dans la table)
    237: "Bmdfoot",  # tentacules du sol de Kalle Demos (profil HD, sans nom dans la table)
    217: "Bwd",      # Molgera (Temple du Vent)
    218: "Bwds",     # larves de Molgera
    240: "Bst",      # Gohdan (Tour des Dieux)
    238: "Bdk",      # Roi Cuirasse / Helmaroc King (Forteresse Maudite)
    239: "Bdkobj",   # décor du combat contre Helmaroc King
    211: "big_pow",  # Jalhalla (Temple de la Terre)
    241: "Fganon",   # Ganon spectral (Phantom Ganon)
    243: "Bgn",      # Ganon marionnette (Puppet Ganon), 1re forme
    244: "Bgn2",     # Ganon marionnette, 2e forme (araignée) [profil HD, sans nom dans la table]
    245: "Bgn3",     # Ganon marionnette, 3e forme (serpent) [profil HD, sans nom dans la table]
    246: "Gnd",      # Ganondorf
    225: "Daiocta",  # Grand Octo (mer)
    226: "Daioeye",  # yeux du Grand Octo
}

# Stages de combat de boss [DECOMP / noms de stage connus du jeu ; HYPO : pas tous relus
# en direct]. Dans ces stages, AUCUN ennemi n'est synchronisé (boss, renforts, larves).
BOSS_STAGES: frozenset[str] = frozenset({
    "M_DragB",   # Gohma
    "kinBOSS",   # Kalle Demos
    "SirenB",    # Gohdan
    "M_DaiB",    # Jalhalla
    "kazeB",     # Molgera
    "Xboss0", "Xboss1", "Xboss2", "Xboss3",   # revanches de la Tour de Ganon
    "GanonK",    # Tour de Ganon : Ganon marionnette [HYPO]
    "GTower",    # sommet de la Tour : Ganondorf [HYPO]
})


def is_boss(proc: int, stage: str | None = None) -> bool:
    """Vrai si l'ennemi (numéro de proc) ou la stage relève d'un combat de boss."""
    return proc in BOSS_PROCS or (stage is not None and stage in BOSS_STAGES)


def without_bosses(actors, stage: str | None, proc_of=lambda a: a.proc_name) -> list:
    """Liste des acteurs sans les boss (et vide dans une stage de boss)."""
    if stage is not None and stage in BOSS_STAGES:
        return []
    return [a for a in actors if proc_of(a) not in BOSS_PROCS]
