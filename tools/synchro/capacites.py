"""Mode Hanteurs : la CAPACITÉ propre de chaque espèce possédée (étude docs/etudes/mode_hanteurs.md §14 ; elle prime).

Pendant une possession, la touche F2 (Ctrl+F2 sur le portage natif : F2 seule y charge un état de sauvegarde) ou le
bouton « Capacité » du lanceur donne l'ordre « capacite » au monstre possédé. Chaque capacité passe par un mécanisme
NATIF du monstre, jamais par une écriture inventée :

* genre « jeu » (mod natif 0.34.0, CALL 20, zone WWMPCAP1 sans bit : natif/mod_wwmp/capacites.c) : l'autorité de la
  salle fait jouer au monstre une action de son propre code, depuis un état « libre », avec les écritures et les
  appels que son code fait lui-même (``PREUVES``, relues dans le cking.rpx par tests/test_natif_capacites.py, qui
  exécute aussi le vrai code du jeu après le CALL) ;
* genre « frenesie » (serveur seul) : pendant ``duree`` s, les recharges des attaques natives de l'espèce (CALL 10,
  ``synchro/ordres.py``, déjà prouvées) sont multipliées par ``facteur``. Rien n'est écrit dans le monstre.

Réglages de la partie (``valider``, serveur ``--hanteurs-config``, panneau « Mode Hanteurs » du lanceur) :
``capacites`` (vrai par défaut), ``capacites_coupees`` (espèces sans capacité), ``recharge_capacites`` (× 0,5 à × 3),
``garde_fer`` (durée de la garde du Darknut, s), ``rage_duree`` (durée des frénésies, s). La durée de possession reste
celle de la partie (``possession_max`` : décision « possession de durée fixe ») : une capacité ne la change jamais. Un
Hanteur reste caché : une capacité n'émet rien vers les Link (aucune note, aucun effet attaché au Hanteur).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

GESTE = "capacite"
CALL_CAP = 20
IMAGES_PAR_S = 30


@dataclass(frozen=True)
class Capacite:
    cle: str
    proc: int
    espece: str                 # nom de l'espèce (FR)
    nom: str                    # nom de la capacité (FR)
    en: str                     # nom de la capacité (EN)
    aide: str                   # ce qu'elle fait (FR)
    genre: str                  # "jeu" (CALL 20) ou "frenesie" (serveur)
    recharge: float             # s, × réglage « recharge des capacités »
    index: int = -1             # genre « jeu » : index dans la table du mod (capacites.c)
    libre: float = 0.0          # genre « jeu » : s sans épinglage après le CALL (le monstre joue son action)
    facteur: float = 1.0        # genre « frenesie » : × recharges de l'attaque et du coup lourd
    essai: bool = False         # espèce « à l'essai » du pilotage : capacité avec le même réglage


CAPACITES: dict[int, Capacite] = {
    189: Capacite("bk.rage", 189, "Bokoblin", "Rage", "Rage",
                  "pendant 5 s, ses attaques natives reviennent 2,5 fois plus vite (il frappe plus souvent)",
                  "frenesie", 15.0, facteur=0.4),
    188: Capacite("mo2.grand_coup", 188, "Moblin", "Grand coup", "Mighty blow",
                  "son grand balayage de lance, le plus fort de ses coups (sans lance : coup de poing ou charge)",
                  "jeu", 12.0, index=0, libre=2.5),
    191: Capacite("tn.garde_fer", 191, "Darknut", "Garde de fer", "Iron guard",
                  "il lève sa garde et pare les coups pendant la durée réglée (1,5 s par défaut)",
                  "jeu", 8.0, index=1, libre=0.0, essai=True),
    206: Capacite("cc.flaque", 206, "Chuchu", "Flaque", "Puddle",
                  "il s'aplatit : l'épée ne le touche plus, il rampe puis se relève de lui-même",
                  "jeu", 10.0, index=2, libre=0.0),
    215: Capacite("ki.rafale", 215, "Keese", "Rafale", "Flurry",
                  "pendant 5 s, ses piqués natifs reviennent 2,5 fois plus vite",
                  "frenesie", 15.0, facteur=0.4, essai=True),
}
INDEX = {c.cle: c.index for c in CAPACITES.values() if c.genre == "jeu"}
N_JEU = 3                       # = CAP_N du mod
assert sorted(INDEX.values()) == list(range(N_JEU))

# Espèces possédables sans capacité, et pourquoi (affiché au Hanteur, repris dans l'étude §14).
NON_FAITES = {
    181: "Kargaroc : sa seule action propre est la saisie (il emporte Link et le lâche ailleurs), qui changerait la "
         "salle du Link de l'autorité ; son mode est un octet à un décalage impair, sans liste blanche de saisie",
    247: "Miniblin : pas de décompilé ; son mode est distribué par comparaisons, aucune écriture native relue pour "
         "une action autre que l'attaque (déjà l'appât)",
    209: "Peahat : pas de décompilé ; sa seule action est la charge hélice en avant (déjà l'appât)",
    190: "Stalfos : sa vie tient sur plusieurs acteurs (haut du corps, tête, masse) non synchronisés",
    198: "Rat : il vole de vrais rubis au Link de l'autorité ; aucune action propre sûre à commander",
    216: "Magtail : se dresser est déjà son attaque ; se rouler en boule est une réaction aux coups (mode non borné "
         "pour un appelant extérieur)",
    208: "Wizzrobe : téléportation et invocation demandent d'écrire son mode, non prouvé borné pour un appelant "
         "extérieur ; le tir est déjà son attaque",
    212: "Poe : disparaître et posséder Link sont des scènes ; l'attaque est déjà son geste",
    224: "ReDead : son mode (+0x3C8) est rangé sans borne par le jeu : jamais écrit",
    202: "Armos Knight : carotte seule, rôle de ses modes non vu en jeu",
    203: "Armos : carotte seule, rôle de ses modes non vu en jeu",
    227: "Octorok : fixe, son tir est décidé par son IA (carotte seule)",
    214: "Boko Baba : plante fixe, sa morsure est décidée par son IA (carotte seule)",
}

# Preuves dans le code HD (adresse, mot attendu) : chaque écriture et chaque appel du mod recopie ce que fait le jeu.
PREUVES = {
    "mo2.grand_coup": (
        (0x021D2C8C, 0xA99F08C0),   # lha r12, 0x8c0(r31)      Mo2_move, cas 5 (combat) : switch (mMode)
        (0x021D2CBC, 0x38800000),   # li r4, 0                 cas 0 : type 0 (tiré au hasard)
        (0x021D2CC0, 0x4BFF6651),   # bl attack_set            0x021C9310
        (0x021D2CC8, 0x38800006),   # li r4, 6
        (0x021D2CCC, 0x4BFF369D),   # bl tex_anm_set           0x021C6368
        (0x021D2CD0, 0x38000001),   # li r0, 1
        (0x021D2CD4, 0x38A00008),   # li r5, 8
        (0x021D2CD8, 0xB01F08C0),   # sth r0, 0x8c0(r31)       mMode = 1
        (0x021D2CDC, 0xB0BF084C),   # sth r5, 0x84c(r31)       m05A4[2] = 8
        (0x021D1F84, 0xB09F08C2),   # sth r4, 0x8c2(r31)       fight_run : mAction = 5
        (0x021C9350, 0x7C9F2378),   # mr r31, r4               attack_set : le type, entier
        (0x021C94A0, 0x2C1F0002),   # cmpwi r31, 2             type 2 demandé
        (0x021C93A4, 0x88DE10B0),   # lbz r6, 0x10b0(r30)      la lance (sans lance : coup de poing ou charge)
    ),
    "tn.garde_fer": (
        (0x024C41D8, 0xA91E0596),   # lha r8, 0x596(r30)       Tn_move : switch (mAction)
        (0x024C41F0, 0x28060019),   # cmplwi r6, 0x19          0..25, sinon défaut
        (0x024C423C, 0x480026F0),   # cas 10 -> 0x024C692C
        (0x024C6930, 0x4BFFBB01),   # bl defence               0x024C2430
        (0x024C56BC, 0x3920000A),   # li r9, 10                fight_run : Link frappe -> garde
        (0x024C56C0, 0xB13E0596),   # sth r9, 0x596(r30)       mAction = 10
        (0x024C56C4, 0xB37E0594),   # sth r27, 0x594(r30)      mMode = 0
        (0x024C56D0, 0x3900001E),   # li r8, 30
        (0x024C56D8, 0x3900000F),   # li r8, 15
        (0x024C56E0, 0xB11E04FA),   # sth r8, 0x4fa(r30)       minuteur de la garde
        (0x024C2574, 0xA81F04FA),   # lha r0, 0x4fa(r31)       defence : minuteur
        (0x024C25B0, 0xB01F0596),   # sth r0, 0x596(r31)       defence : retour à fight_run (4)
    ),
    "cc.flaque": (
        (0x0210E060, 0x88BF03FD),   # lbz r5, 0x3fd(r31)       action_nomal_move : switch (m2F5)
        (0x0210E068, 0x2806000B),   # cmplwi r6, 0xb
        (0x0210E09C, 0x480004B8),   # cas 6 -> 0x0210E554
        (0x0210E4DC, 0x39200006),   # li r9, 6                 comportement 2 : m2F5 = 6 (le jeu lui-même)
        (0x0210E4F0, 0x993F03FD),   # stb r9, 0x3fd(r31)
        (0x0210E548, 0x897F03FD),   # lbz r11, 0x3fd(r31)      cas 5 -> 6 : m2F5 + 1
        (0x0210E550, 0x997F03FD),   # stb r11, 0x3fd(r31)
        (0x0210E58C, 0x3C00089D),   # lis r0, 0x89d            cas 6 : masque de coups réduit 0x089DC060
        (0x0210E598, 0x6000C060),   # ori r0, r0, 0xc060
        (0x0210E5A4, 0x901F07EC),   # stw r0, 0x7ec(r31)       (bombes, flèches, boomerang, grappin, marteau,
        (0x0210E5AC, 0x3880001F),   # li r4, 31                 lumière : plus l'épée) ; anim TACHI2HUSE
        (0x0210E5D8, 0x98FF03FD),   # stb r7, 0x3fd(r31)       m2F5 = 7
    ),
}

# Réglages de la partie
RECHARGE_MULTS = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
GARDE_MIN, GARDE_MAX, GARDE_DEFAUT = 0.5, 4.0, 1.5      # s (mod : 15..120 images)
RAGE_MIN, RAGE_MAX, RAGE_DEFAUT = 2.0, 10.0, 5.0        # s


def defauts() -> dict:
    return {"capacites": True, "capacites_coupees": [], "recharge_capacites": 1.0, "garde_fer": GARDE_DEFAUT,
            "rage_duree": RAGE_DEFAUT}


def _flottant(v, lo: float, hi: float, defaut: float) -> float:
    if isinstance(v, bool):
        return defaut
    try:
        x = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError, OverflowError):
        return defaut
    if not math.isfinite(x):
        return defaut
    return round(max(lo, min(hi, x)), 2)


def coupees(v) -> list[int]:
    """Réglage « capacités coupées » nettoyé : liste de process ou de noms d'espèces (texte séparé par des virgules
    accepté) ; rend la liste triée des process qui ont une capacité."""
    if isinstance(v, str):
        v = v.replace(";", ",").split(",")
    if not isinstance(v, (list, tuple, set, frozenset)):
        return []
    noms = {c.espece.lower(): p for p, c in CAPACITES.items()}
    out = set()
    for x in list(v)[:32]:
        if isinstance(x, bool):
            continue
        if isinstance(x, int) and x in CAPACITES:
            out.add(x)
        elif isinstance(x, str):
            t = x.strip().lower()
            if t in noms:
                out.add(noms[t])
            elif t.isdigit() and int(t) in CAPACITES:
                out.add(int(t))
    return sorted(out)


def valider(d, c: dict) -> dict:
    """Recopie dans ``c`` les réglages des capacités lus dans ``d`` (nettoyés, bornés) ; ``c`` doit avoir les défauts."""
    if not isinstance(d, dict):
        return c
    c["capacites"] = d.get("capacites", c["capacites"]) not in (0, False)
    if "capacites_coupees" in d:
        c["capacites_coupees"] = coupees(d.get("capacites_coupees"))
    c["recharge_capacites"] = _flottant(d.get("recharge_capacites"), 0.5, 3.0, c["recharge_capacites"])
    c["garde_fer"] = _flottant(d.get("garde_fer"), GARDE_MIN, GARDE_MAX, c["garde_fer"])
    c["rage_duree"] = _flottant(d.get("rage_duree"), RAGE_MIN, RAGE_MAX, c["rage_duree"])
    return c


def capacite(proc, cfg: dict | None = None):
    """Capacité de l'espèce ``proc`` permise par la partie (``cfg`` : réglages validés ; None : défauts), ou None.
    Une espèce « à l'essai » du pilotage n'a sa capacité qu'avec le réglage de la partie qui la permet."""
    c = CAPACITES.get(proc)
    if c is None:
        return None
    cfg = cfg if cfg is not None else defauts()
    if not cfg.get("capacites", True) or proc in cfg.get("capacites_coupees", ()):
        return None
    if c.essai:
        essai = cfg.get("pilotage_essai", [])
        if essai is not True and proc not in (essai or ()):
            return None
    return c


def recharge(proc, cfg: dict | None = None) -> float:
    c = CAPACITES.get(proc)
    if c is None:
        return 0.0
    mult = (cfg or {}).get("recharge_capacites", 1.0)
    return round(c.recharge * mult, 2)


def garde_images(cfg: dict | None = None) -> int:
    """Durée de la garde du Darknut en images (15..120 : bornes du mod)."""
    s = (cfg or {}).get("garde_fer", GARDE_DEFAUT)
    return max(15, min(120, int(round(float(s) * IMAGES_PAR_S))))


def masque_coupees(cfg: dict | None = None) -> int:
    """Bits des capacités « jeu » coupées par la partie (zone WWMPCAP1 +0x10, bit = index)."""
    cfg = cfg or defauts()
    m = 0
    for p, c in CAPACITES.items():
        if c.genre == "jeu" and capacite(p, dict(cfg, pilotage_essai=True)) is None:
            m |= 1 << c.index
    return m


def frenesie(proc, geste: str, duree_recharge: float, actif: bool) -> float:
    """Recharge d'un geste natif pendant une frénésie de l'espèce (attaque et coup lourd seulement)."""
    c = CAPACITES.get(proc)
    if not actif or c is None or c.genre != "frenesie" or geste not in ("attaque", "lourde"):
        return duree_recharge
    return round(duree_recharge * c.facteur, 2)


def nom(proc, en: bool = False) -> str:
    c = CAPACITES.get(proc)
    return "" if c is None else (c.en if en else c.nom)


def raison_absente(proc) -> str:
    """Pourquoi l'espèce n'a pas de capacité (texte court pour le Hanteur)."""
    if proc in CAPACITES:
        return ""
    return NON_FAITES.get(proc, "cette espèce n'a pas de capacité")
