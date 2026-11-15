"""Randomizer par fichiers sur le PORTAGE NATIF : le code des lots 6 à 9 traduit en paramètres du mod natif.

Sous Cemu, le pack WWMP_Rando GÉNÉRÉ (rando/fichiers.py) réécrit des mots du programme (``patch_rando.asm`` :
« sites » au milieu des fonctions, code ajouté dans la cave, données). Le portage natif (ZeldaWWHDRecomp) a traduit
le programme une fois pour toutes : ces mots n'y font rien. Le mod natif (natif/mod_wwmp/rando_code*.c) refait
chaque FRAGMENT en C (accroches d'entrée ou de retour, remplacements de fonctions, docs/dev/PORTAGE_NATIF.md §3.14) ;
il lui faut savoir quels fragments la partie demande, avec leurs valeurs propres à la table (objets des Grandes Fées,
listes du crochet de création…) : c'est le BLOC DE CODE de ``rando_plan.bin`` (format 2), que ce module tire du
``patch_rando.asm`` du pack généré lui-même, la vérité que joue Cemu :

* le texte est assemblé (``jeu.emu_ppc.Assembleur``, comme les tests) ; chaque mot réécrit hors de la cave doit
  appartenir à UN fragment connu, avec, pour les fragments de TWWHD, toutes leurs lignes recopiées telles quelles
  du dépôt (``rando/lot*_asm``) ; sinon : refus (``ErreurNatif``), jamais un paquet à moitié porté ;
* un fragment reconnu mais pas encore porté par le mod (``PORTES``) : refus qui nomme les options et dit pourquoi ;
* les paramètres sont relus dans l'image assemblée (étiquettes de la cave, mots des sites, données).

Un joueur Cemu et un joueur du portage jouent donc la même partie : mêmes fichiers (même empreinte), et le même code,
une fois en mots du programme (Cemu), une fois en C (portage). Ce module ne touche ni au jeu ni au réseau.
"""
from __future__ import annotations

import os
import re
import struct

from rando import lot6 as L6, lot7 as L7, lot8 as L8, lot9 as L9

MAGIE = b"WWMPRNDC"
FORMAT_PLAN = 2                      # rando_plan.bin avec bloc de code (format 1 : 48 octets, sans code)

# fragments (bit du mot « fragments » du bloc, comme FRAG_* de natif/mod_wwmp/rando_code.h)
FRAGMENTS = ("bouclier", "creation", "epuise", "champ", "tres", "evt", "boss", "fees", "sol", "pnj", "beedle",
             "cine_courtes", "ouvert", "cine_ouvert", "depart", "bombes", "sans_epee", "entrees")
BIT = {n: 1 << k for k, n in enumerate(FRAGMENTS)}
# fragments que le mod natif sait refaire (wwmp-natif 0.10.0) ; un autre fragment demandé : refus avant la génération
PORTES = frozenset({"bouclier", "creation", "epuise", "champ", "sol", "evt", "tres", "fees", "boss", "beedle",
                    "cine_courtes"})
# fragments que le mod natif ne refait pas en C : la GREFFE (rando/greffe.py, second mod de code « wwmp-rando-code »
# généré sur ce PC depuis le dump) recopie les fonctions du jeu qui les portent, avec les mots du pack généré
GREFFES = frozenset({"pnj", "ouvert", "cine_ouvert", "depart", "bombes", "sans_epee", "entrees"})
ID_MOD_CODE = "wwmp-natif"
# bits que le bloc de code peut porter (= FRAG_ACCEPTES de rando_code.h) : wwmp-natif refait les PORTES et note les
# GREFFES (mot +0x30 de WWMPNATF), que le client exige alors de trouver chargées (marque WWMPGRF1)
ACCEPTES = PORTES | GREFFES

# pourquoi un fragment n'est pas (encore) porté : affiché par Jouer (FR, EN)
RAISON_NON_PORTE = (
    "leurs sites sont au milieu de grandes fonctions du jeu (branchements forcés, immédiats) : il faut réécrire ces "
    "fonctions en C une à une (docs/dev/PORTAGE_NATIF.md §3.14)",
    "their sites are in the middle of large game functions (forced branches, immediates): each of these functions has "
    "to be rewritten in C (docs/dev/PORTAGE_NATIF.md §3.14)")

# options du randomizer -> fragments qu'elles demandent (pour nommer les options refusées)
NOMS_OPTIONS = {
    "lieux_tresors": ("trésors engloutis", "sunken treasures"),
    "lieux_evenements": ("objets d'événements", "event items"),
    "lieux_boss": ("réceptacles des boss", "boss heart containers"),
    "lieux_fees": ("Grandes Fées", "Great Fairies"),
    "lieux_sol": ("objets posés", "standing items"),
    "lieux_pnj_code": ("personnages à immédiat", "hard-coded characters"),
    "lieux_beedle": ("Beedle à 20 rubis et carte à 900 rubis", "Beedle 20 rupee item and 900 rupee chart"),
    "monde": ("monde ouvert", "open world"),
    "depart": ("autre île de départ", "other starting island"),
    "depart_melange": ("objets de départ mélangés", "shuffled starting items"),
    "cinematiques_courtes": ("cinématiques courtes", "short cutscenes"),
    "pas_de_recombats": ("pas de recombats", "no boss rematches"),
    "coffres_cinematiques": ("coffres à la place des cinématiques", "chests instead of cutscenes"),
    "boucliers_progressifs": ("boucliers progressifs", "progressive shields"),
    "sans_epee": ("sans épée", "swordless"),
    "entrees": ("entrées mélangées", "shuffled entrances"),
    "boutique_epuise": ("correctif « épuisé » de la boutique", "shop « sold out » fix"),
}


class ErreurNatif(ValueError):
    """Code du pack généré que le mod natif ne refait pas (``fragments`` : ceux qui manquent, ``inconnus`` : mots
    qu'aucun fragment connu n'explique)."""

    def __init__(self, texte: str, fragments=(), inconnus=()):
        super().__init__(texte)
        self.fragments = tuple(fragments)
        self.inconnus = tuple(inconnus)


# ---------------------------------------------------------------------------------------------------------------
# Catalogue des sites (adresse de mot -> fragment)
# ---------------------------------------------------------------------------------------------------------------
DOSSIERS = {"champ": L6.DOSSIER_ASM, "tres": L6.DOSSIER_ASM, "evt": L6.DOSSIER_ASM, "boss": L6.DOSSIER_ASM,
            "sol": L6.DOSSIER_ASM, "pnj": L6.DOSSIER_ASM, "beedle": L6.DOSSIER_ASM,
            "cine_courtes": L7.DOSSIER_ASM, "ouvert": L7.DOSSIER_ASM, "cine_ouvert": L7.DOSSIER_ASM,
            "sans_epee": L8.DOSSIER_ASM, "entrees": L8.DOSSIER_ASM}


def _fragment_brut(nom: str) -> dict:
    with open(os.path.join(DOSSIERS[nom], nom + ".asm"), encoding="utf-8") as f:
        t = f.read()
    out = {"cave": [], "sites": [], "adresses": []}
    partie = None
    for ligne in t.splitlines():
        s = ligne.strip()
        if s in ("; [symboles]", "; [cave]", "; [sites]"):
            partie = s[3:-1]
            continue
        if partie in ("cave", "sites") and s and not s.startswith(";"):
            out[partie].append(ligne.rstrip())
            if partie == "sites":
                out["adresses"].append(int(s.split(" ", 1)[0], 16))
    return out


_CACHE = {}


def fragment(nom: str) -> dict:
    if nom not in _CACHE:
        _CACHE[nom] = _fragment_brut(nom)
    return _CACHE[nom]


# mots que les 7 `stw` effacés (NOPS_CHAMP, constructeur statique 0x02551CA4 de item_resources, r17 = 0x101E4674)
# écriraient : +0 des ressources des chants 0x6D..0x72 et de 0xAA
CIBLES_NOPS_CHAMP = tuple(L6.ITEM_RESOURCES + o for o in (0x1008, 0xFE4, 0xFC0, 0xF9C, 0xF78, 0xF54, 0x17E8))
# régions de données que le pack réécrit (adresse USA v0) : le mod les écrit au démarrage du jeu, mot à mot
REGIONS = (
    ("champ", L6.ITEM_RESOURCES, L6.ITEM_INFO + 4 * 0x100),             # item_resources, field_item_resources, item_info
    ("beedle", L6.SHOP_ITEM_DATA, L6.SHOP_ITEM_DATA + 0x10 * 0x20),      # étals (Item_set_data)
    ("beedle", L6.BEEDLE_RECHANGE[0], L6.BEEDLE_RECHANGE[0] + 4),        # Item_set_data3 : liste de rechange
    ("sol", 0x101CA100, 0x101CA104),                                     # fix_stone_head_bugs
)
MAX_DONNEES = 800
# un mot de données dans le bloc : index (u16) = région (3 bits, ordre de REGIONS) << 13 | mot dans la région, et le mot
# (u32), en deux tableaux
MAX_OBJETS, MAX_BITS, MAX_MOTS, MAX_DONJONS, MAX_OCTETS = 31, 63, 15, 7, 15

# lieux du lot 6 dont l'objet est un immédiat du code (ordre FIXE : place dans le bloc, comme rando_code.h)
IMMEDIATS = tuple(k for k, s in enumerate(L6.SITES) if s["voie"] in ("immediat", "zunari"))
BOSS = tuple(k for k, s in enumerate(L6.SITES) if s["voie"] == "boss")


def _region(adr: int):
    for nom, lo, hi in REGIONS:
        if lo <= adr < hi:
            return nom
    return None


def _sites_fixes() -> dict:
    """{adresse: fragment} des sites qui ne viennent pas d'un fichier de fragment de TWWHD."""
    out = {}
    for a in L6.NOPS_CHAMP:
        out[a] = "champ"
    out[0x02095AF8] = "fees"
    out[0x022313A8] = out[0x022313A8 + 0x6C] = "pnj"           # potions (0x02231414)
    for k in IMMEDIATS:
        for a in L6.SITES[k]["adresses"]:
            out[a & ~3] = "pnj"
    for k in BOSS:
        for a, _v in L6.SITES[k]["octets_rpx"]:
            out[a & ~3] = "boss"
    out[L7.SITE_CREATION] = "creation"
    out[L7.SITE_BOUCLIER] = "bouclier"
    for a, _avant, _q in L7.SITES_DEPART:
        out[a] = "depart"
    for a, _avant in L7.SITES_BOMBES:
        out[a] = "bombes"
    out[L8.SITE_SPECTRE[0]] = "sans_epee"
    return out


# ---------------------------------------------------------------------------------------------------------------
# Analyse du patch_rando.asm généré
# ---------------------------------------------------------------------------------------------------------------
def _assembler(texte: str):
    from jeu import emu_ppc as emu
    return emu.Assembleur(texte, {}, permises=emu.PERMISES_MAILBOX).assembler()


def _octets(a, adr: int, n: int) -> bytes:
    out = bytearray()
    for k in range(0, n, 4):
        w = a.mots.get(adr + k)
        if w is None:
            raise ErreurNatif(f"mot attendu à 0x{adr + k:08X} (cave du pack généré)")
        out += struct.pack(">I", w)
    return bytes(out[:n])


def _liste(a, etiquette: str, pas: int, fin_nulle: bool = True) -> list:
    """Entrées (tuples de 2 champs) d'une table de la cave terminée par un champ nul."""
    if etiquette not in a.labels:
        raise ErreurNatif(f"table {etiquette} absente du crochet de création")
    adr, out = a.labels[etiquette], []
    while True:
        w = _octets(a, adr, pas)
        x = struct.unpack(">HH" if pas == 4 else ">II", w)
        if x[0] == 0:
            return out
        out.append(x)
        adr += pas
        if len(out) > 64:
            raise ErreurNatif(f"table {etiquette} trop longue")


def analyser(texte: str) -> dict:
    """{fragments: set, params…} du code d'un patch_rando.asm généré. Lève ErreurNatif si un mot réécrit n'est
    expliqué par aucun fragment connu (ou un fragment de TWWHD pas recopié tel quel)."""
    a = _assembler(texte)
    patches = {}
    for adr, ins, _n in a.patchs:
        if adr in patches:
            raise ErreurNatif(f"mot 0x{adr:08X} réécrit deux fois")
        patches[adr] = ins
    lignes = set(l.rstrip() for l in texte.splitlines())
    fixes = _sites_fixes()
    pris, frags = {}, set()
    # fragments recopiés de TWWHD (fichiers du dépôt)
    for nom in DOSSIERS:
        fr = fragment(nom)
        vus = [x for x in fr["adresses"] if x in patches]
        if not vus:
            continue
        if nom == "beedle" and set(vus) == set(L9.EPUISE_SITES):
            continue                                   # lot 9 seul (boutique_epuise) : plus bas
        manque = [l for l in fr["cave"] + fr["sites"] if l not in lignes and "{{zunari}}" not in l]
        if len(vus) != len(fr["adresses"]) or manque:
            raise ErreurNatif(f"fragment {nom} incomplet ou différent de celui du dépôt", inconnus=vus)
        frags.add(nom)
        for x in fr["adresses"]:
            pris[x] = nom
    if "beedle" in frags or all(x in patches for x in L9.EPUISE_SITES):
        frags.add("epuise")
        for x in L9.EPUISE_SITES:
            pris[x] = "epuise"
    elif any(x in patches for x in L9.EPUISE_SITES):
        raise ErreurNatif("correctif « épuisé » incomplet", inconnus=[x for x in L9.EPUISE_SITES if x in patches])
    donnees, inconnus = [], []
    for adr in sorted(patches):
        if adr in pris:
            continue
        nom = fixes.get(adr) or _region(adr)
        if nom is None:
            inconnus.append(adr)
            continue
        pris[adr] = nom
        frags.add(nom)
        if _region(adr):
            donnees.append((adr, a.mots[adr]))
    if inconnus:
        raise ErreurNatif("mots du programme réécrits par aucun fragment connu : "
                          + ", ".join(f"0x{x:08X}" for x in inconnus[:8]), inconnus=inconnus)
    if "champ" in frags and not all(x in patches for x in L6.NOPS_CHAMP):
        raise ErreurNatif("« tout objet posable au sol » incomplet")
    if "champ" in frags and not set(CIBLES_NOPS_CHAMP) <= {adr for adr, _w in donnees}:
        # les 7 `stw` effacés du constructeur de item_resources (0x02551CA4) : le mod ne les efface pas, il réécrit
        # après eux (démarrage du jeu) ces 7 mots, qui doivent donc être dans les données du pack
        raise ErreurNatif("« tout objet posable au sol » : mots des nops absents des données")
    for x in L6.NOPS_CHAMP:
        if x in patches and a.mots[x] != 0x60000000:
            raise ErreurNatif(f"0x{x:08X} : nop attendu")
    out = {"fragments": frags, "donnees": donnees, "fees": bytes(6), "potions": bytes([0x52, 0x53]), "zunari": 0x2A,
           "salle": L7.SALLE_ORIGINE, "immediats": bytes(L6.SITES[k]["id"] for k in IMMEDIATS),
           "boss": bytes(L6.SITES[k]["id"] for k in BOSS), "objets": b"", "bits": [], "mots": [], "donjons": [],
           "octets": []}
    if "fees" in frags:
        out["fees"] = _octets(a, a.labels["wwr_fees"], 6)
    if "pnj" in frags:
        if 0x022313A8 in patches:
            out["potions"] = _octets(a, a.labels["wwr_potions"], 2)
        out["zunari"] = _octets(a, a.labels["wwr_pnj_028FA2FC"], 1)[0]
    imm = bytearray(out["immediats"])
    for i, k in enumerate(IMMEDIATS):
        for b in L6.SITES[k]["adresses"]:
            w = a.mots.get(b & ~3)
            if w is not None:
                imm[i] = (w >> (8 * (3 - (b & 3)))) & 0xFF
    out["immediats"] = bytes(imm)
    bo = bytearray(out["boss"])
    for i, k in enumerate(BOSS):
        for b, _v in L6.SITES[k]["octets_rpx"]:
            w = a.mots.get(b & ~3)
            if w is not None:
                bo[i] = (w >> (8 * (3 - (b & 3)))) & 0xFF
    out["boss"] = bytes(bo)
    if "creation" in frags:
        adr = a.labels.get("wwr7_objets")
        if adr is None:
            raise ErreurNatif("table wwr7_objets absente du crochet de création")
        obj = bytearray()
        while True:
            x = _octets(a, adr + (len(obj) & ~3), 4)[len(obj) & 3]
            if x == 0xFF:
                break
            obj.append(x)
            if len(obj) > MAX_OBJETS:
                raise ErreurNatif("trop d'objets de départ")
        out["objets"] = bytes(obj)
        out["bits"] = _liste(a, "wwr7_bits", 4)
        out["mots"] = _liste(a, "wwr7_mots", 8)
        out["donjons"] = _liste(a, "wwr7_donjons", 4)
        out["octets"] = _liste(a, "wwr7_octets", 4)
        for cle, maxi in (("bits", MAX_BITS), ("mots", MAX_MOTS), ("donjons", MAX_DONJONS), ("octets", MAX_OCTETS)):
            if len(out[cle]) > maxi:
                raise ErreurNatif(f"crochet de création : trop de {cle}")
    if "depart" in frags:
        w = a.mots[L7.SITES_DEPART[0][0]]
        if w >> 16 != 0x3940 or any(a.mots.get(x) != (0x39400000 | (w & 0xFFFF) if q == "salle" else 0x38000000)
                                    for x, _av, q in L7.SITES_DEPART):
            raise ErreurNatif("départ : sites inattendus")
        out["salle"] = w & 0xFFFF
    if len(donnees) > MAX_DONNEES:
        raise ErreurNatif("trop de mots de données")
    return out


def non_portes(analyse: dict) -> list[str]:
    """Fragments demandés que le portage ne refait pas, ni en C (wwmp-natif) ni par la greffe (ordre de FRAGMENTS)."""
    return [n for n in FRAGMENTS if n in analyse["fragments"] and n not in ACCEPTES]


def greffes(analyse: dict) -> list[str]:
    """Fragments demandés que porte la greffe (rando/greffe.py), ordre de FRAGMENTS."""
    return [n for n in FRAGMENTS if n in analyse["fragments"] and n in GREFFES]


# ---------------------------------------------------------------------------------------------------------------
# Bloc de code (rando_plan.bin, format 2) : disposition de PlanCode (natif/mod_wwmp/rando_code.h)
# ---------------------------------------------------------------------------------------------------------------
OFF_FEES, OFF_POTIONS, OFF_ZUNARI, OFF_SALLE = 0x10, 0x18, 0x1C, 0x1D
OFF_IMMEDIATS, OFF_BOSS, OFF_OBJETS = 0x20, 0x40, 0x48
OFF_BITS, OFF_MOTS, OFF_DONJONS, OFF_OCTETS, OFF_DONNEES = 0x68, 0x168, 0x1E8, 0x208, 0x248
OFF_MOTS_DONNEES = OFF_DONNEES + 2 * MAX_DONNEES
TAILLE_BLOC = OFF_MOTS_DONNEES + 4 * MAX_DONNEES


def index_donnee(adr: int) -> int | None:
    """Index d'un mot de données dans le bloc (région << 13 | mot), None hors des régions."""
    for k, (_nom, lo, hi) in enumerate(REGIONS):
        if lo <= adr < hi and not adr & 3 and (adr - lo) >> 2 < 0x2000:
            return k << 13 | (adr - lo) >> 2
    return None


def adresse_donnee(index: int) -> int | None:
    k, mot = index >> 13, index & 0x1FFF
    if k >= len(REGIONS):
        return None
    _nom, lo, hi = REGIONS[k]
    a = lo + 4 * mot
    return a if a < hi else None


def bloc(analyse: dict) -> bytes:
    """Le bloc de code (taille fixe TAILLE_BLOC) de cette analyse."""
    b = bytearray(TAILLE_BLOC)
    frags = 0
    for n in analyse["fragments"]:
        frags |= BIT[n]
    struct.pack_into(">8sII", b, 0, MAGIE, frags, len(analyse["donnees"]))
    b[OFF_FEES:OFF_FEES + 6] = analyse["fees"]
    b[OFF_POTIONS:OFF_POTIONS + 2] = analyse["potions"]
    b[OFF_ZUNARI] = analyse["zunari"]
    b[OFF_SALLE] = analyse["salle"]
    if len(IMMEDIATS) > 32 or len(BOSS) > 8:
        raise ErreurNatif("bloc de code : trop de lieux à immédiat")
    b[OFF_IMMEDIATS:OFF_IMMEDIATS + len(IMMEDIATS)] = analyse["immediats"]
    b[OFF_BOSS:OFF_BOSS + len(BOSS)] = analyse["boss"]
    b[OFF_OBJETS:OFF_OBJETS + 32] = (bytes(analyse["objets"]) + b"\xff" * 32)[:32]
    for i, (o, bit) in enumerate(analyse["bits"]):
        struct.pack_into(">HH", b, OFF_BITS + 4 * i, o, bit)
    for i, (o, v) in enumerate(analyse["mots"]):
        struct.pack_into(">II", b, OFF_MOTS + 8 * i, o, v)
    for i, (o, bit) in enumerate(analyse["donjons"]):
        struct.pack_into(">HH", b, OFF_DONJONS + 4 * i, o, bit)
    for i, (o, v) in enumerate(analyse["octets"]):
        struct.pack_into(">HH", b, OFF_OCTETS + 4 * i, o, v)
    for i, (adr, w) in enumerate(analyse["donnees"]):
        struct.pack_into(">H", b, OFF_DONNEES + 2 * i, index_donnee(adr))
        struct.pack_into(">I", b, OFF_MOTS_DONNEES + 4 * i, w)
    return bytes(b)


def fichier_plan(plan: str, analyse: dict | None) -> bytes:
    """rando_plan.bin : format 1 (48 octets) sans code, format 2 (48 octets + bloc) avec."""
    from rando import fichiers as rf
    if analyse is None or not analyse["fragments"]:
        return rf.fichier_plan(plan)
    if non_portes(analyse):
        raise ErreurNatif("fragments pas portés : " + ", ".join(non_portes(analyse)), fragments=non_portes(analyse))
    corps = bloc(analyse)
    return rf.MAGIE_PLAN + struct.pack(">II", FORMAT_PLAN, len(corps)) + bytes.fromhex(plan) + corps


def lire_fichier_plan(data) -> tuple[str, bytes]:
    """(plan, bloc de code) d'un rando_plan.bin (même lecture que le mod) ; ("", b"") s'il n'est pas valable."""
    from rando import fichiers as rf
    if not isinstance(data, (bytes, bytearray)) or len(data) < 48 or data[:8] != rf.MAGIE_PLAN:
        return "", b""
    fmt, n = struct.unpack_from(">II", data, 8)
    if fmt == rf.FORMAT_PLAN and n == 0 and len(data) == 48:
        return bytes(data[16:48]).hex(), b""
    if fmt == FORMAT_PLAN and n == TAILLE_BLOC and len(data) == 48 + n and data[48:56] == MAGIE:
        frags, nd = struct.unpack_from(">II", data, 56)
        if frags & ~sum(BIT[x] for x in ACCEPTES) or not frags or nd > MAX_DONNEES:
            return "", b""
        for k in range(nd):
            adr = adresse_donnee(struct.unpack_from(">H", data, 48 + OFF_DONNEES + 2 * k)[0])
            if adr is None or not frags & BIT[_region(adr)]:
                return "", b""
        return bytes(data[16:48]).hex(), bytes(data[48:])
    return "", b""


# ---------------------------------------------------------------------------------------------------------------
# Options refusées (Jouer, avant la génération)
# ---------------------------------------------------------------------------------------------------------------
def options_de(fragments, options) -> list[str]:
    """Noms courts des options de la table qui demandent ces fragments (pour le message de refus)."""
    fr = set(fragments)
    o = options if isinstance(options, dict) else {}
    out = []
    lot6 = {"tres": "lieux_tresors", "evt": "lieux_evenements", "boss": "lieux_boss", "fees": "lieux_fees",
            "sol": "lieux_sol", "pnj": "lieux_pnj_code", "beedle": "lieux_beedle"}
    if "champ" in fr:
        fr |= {f for f, opt in lot6.items() if o.get(opt) is True}
    for f, opt in lot6.items():
        if f in fr and o.get(opt) is True:
            out.append(opt)
    if fr & {"ouvert", "cine_ouvert", "depart"}:
        out.append("monde")
        if L7.ile_depart(o):
            out.append("depart")
    if "bombes" in fr:
        out.append("depart_melange")
    if "cine_courtes" in fr:
        out.append("cinematiques_courtes")
    if "creation" in fr and not L7.ouvert(o):
        out += [x for x in ("pas_de_recombats", "coffres_cinematiques") if o.get(x) is True]
    if "bouclier" in fr:
        out.append("boucliers_progressifs")
    if "sans_epee" in fr:
        out.append("sans_epee")
    if "entrees" in fr:
        out.append("entrees")
    if "epuise" in fr and o.get("lieux_beedle") is not True:
        out.append("boutique_epuise")
    return list(dict.fromkeys(out))


def nommer(options_courtes, en: bool = False) -> str:
    return ", ".join(NOMS_OPTIONS.get(x, (x, x))[1 if en else 0] for x in options_courtes)
