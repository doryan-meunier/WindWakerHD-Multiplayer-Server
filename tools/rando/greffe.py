"""Randomizer par fichiers sur le PORTAGE NATIF : la GREFFE, second mod de code généré sur ce PC.

Le mod natif ``wwmp-natif`` refait en C une partie du code des lots 6 à 9 (rando/natif_code.py, ``PORTES``). Le reste
(monde ouvert et ses cinématiques, île de départ, bombes de départ, sans épée, entrées, personnages à immédiat :
``GREFFES``) réécrit des mots au milieu de 92 fonctions du jeu (de 5 à 3 349 instructions) : les refaire une à une en
C n'est pas raisonnable. La greffe fait EXACTEMENT ce que fait Cemu, autrement : chaque fonction du jeu qui porte un
mot réécrit est recopiée, AVEC les mots du pack généré (le ``patch_rando.asm`` que joue Cemu), dans un ELF PowerPC
relogeable du Mod SDK v2, avec le code ajouté (« cave ») des fragments ; un ``WWHD_REPLACE`` (descripteur de
``.wwhd_hooks``) remplace la fonction d'origine par sa copie. Le portage traduit et compile ce mod comme le nôtre
(``build_guest_mod`` : même traducteur que pour le jeu, ``tools/recomp/ppc2c.py``).

Relogement (tout le reste est copié mot pour mot) :

* saut ou appel (``b``, ``bl``) vers une fonction du jeu (cible d'un ``bl`` du programme ou pointeur relogé des
  données) : relocation ``R_PPC_REL24`` contre ``__wwhd_game_0x<ADR>`` : appel par le répartiteur du portage, comme le
  ferait le jeu (les accroches des mods s'appliquent, nos remplacements compris) ;
* saut, conditionnel ou non, vers le milieu d'une fonction recopiée ou vers la cave : déplacement recalculé dans la
  section (même section : pas de relocation) ;
* paire ``lis``/``addis`` + ``addi``/``addic``/``ori`` qui forme une adresse au milieu d'une fonction recopiée (tables de
  sauts des ``switch``, ``bctr``) : relocations ``ADDR16_HA``/``ADDR16_LO`` vers la copie, et chaque case de la table
  (suite de ``b``) citée par un mot de ``.data`` (``ADDR32``) : le traducteur en fait des points d'entrée ;
* étiquettes de la cave en ``@ha``/``@l`` : relocations vers la cave recopiée.

Refus (``ErreurGreffe``, jamais une greffe à moitié juste) : saut conditionnel vers une autre fonction, saut vers le
milieu d'une fonction qui n'est pas recopiée, pointeur des données vers le milieu d'une fonction recopiée, cave dont un
mot ``.int`` saute hors d'elle, adresse de cave ou de site inattendue.

Le paquet (``<réglages>/ModManager/Mods/wwmp-rando-code`` : ``manifest.json``, ``mod.elf``) est GÉNÉRÉ sur ce PC
depuis le dump (comme le mod de contenu ``wwmp-rando``), jamais distribué ; il dépend de ``wwmp-natif`` (dépendance
déclarée) et, comme lui, ne s'allume que dans le jeu (F1 > Mods > Switch on, confirmer le code natif). La section
``.data`` porte une marque ``WWMPGRF1`` (format, nombre de fonctions, empreinte du plan) que le client cherche dans
la zone des mods : une partie qui demande la greffe n'est jouée que si CETTE greffe est chargée.
"""
from __future__ import annotations

import bisect
import json
import os
import struct

from rando import natif_code as nc

ID = "wwmp-rando-code"
MAGIE = b"WWMPGRF1"
FORMAT = 1
TEXTE = (0x02000020, 0x02900000)              # section de code du cking.rpx USA v0
IMPORTS = 0xC0000000                          # symboles importés des RPL (sections .fimport_ / .dimport_)
# imports que le Mod SDK v2 offre en services de l'hôte, même nom, mêmes arguments (docs/mod-sdk-v2.md, « Services »)
SERVICES = frozenset({"memcpy", "memmove", "memset"})
SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_RELA = 1, 2, 3, 4
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 1, 2, 4
R_PPC_ADDR32, R_PPC_ADDR16_LO, R_PPC_ADDR16_HA, R_PPC_REL24 = 1, 4, 6, 10


class ErreurGreffe(ValueError):
    pass


def _sext(v: int, bits: int) -> int:
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def cible_branche(adr: int, w: int):
    """(genre, cible) d'un saut relatif (``b``/``bl`` : 18, ``bc`` : 16), None sinon (ou saut absolu : « abs »)."""
    op = w >> 26
    if op == 18:
        if w & 2:
            return "abs", None
        return 18, (adr + _sext(w & 0x03FFFFFC, 26)) & 0xFFFFFFFF
    if op == 16:
        if w & 2:
            return "abs", None
        return 16, (adr + _sext(w & 0xFFFC, 16)) & 0xFFFFFFFF
    return None


class Programme:
    """Le code du cking.rpx (lu seulement), découpé en fonctions EXACTEMENT comme le portage
    (ZeldaWWHDRecomp tools/recomp/analyze.py, ``Program.discover``) : points d'entrée = le point d'entrée de l'ELF,
    les cibles des ``bl`` et ``bcl`` dans le code (hors appels d'imports), le code dont l'adresse est prise par une
    relocation ``ADDR32`` ou ``ADDR16_LO`` (vtables, pointeurs, paires lis/addi), sauf les tables de sauts (adresse
    formée juste avant, précédée d'un ``bctr``) ; une fonction va de son entrée à l'entrée suivante. Un
    ``WWHD_REPLACE`` et un appel par le répartiteur ne visent que ces entrées."""

    def __init__(self, rpx):
        secs = getattr(rpx, "sections", None) or rpx.secs       # rando.lot6.Rpx ou jeu.rpx.Rpx
        s = next(s for s in secs if s["addr"] == TEXTE[0])
        self.lo, self.data = s["addr"], s["data"]
        self.hi = self.lo + len(self.data)
        n = len(self.data) // 4
        mots = struct.unpack(f">{n}I", self.data[:4 * n])
        self.mots = mots
        self.pointeurs = {}                       # cible dans le code -> sites de relocation hors du code
        self.relocs = {}                          # mot du code -> (type, valeur du symbole, addend, nom d'import, +)
        refs = {}                                 # cible dans le code -> sites (ADDR32 / ADDR16_LO, hors imports)
        appels_imports = set()
        for sec in secs:
            if sec.get("typ") != 4:
                continue
            symtab = secs[sec["link"]]
            sym = symtab["data"]
            noms = secs[symtab["link"]]["data"] if symtab.get("link") is not None else b""
            d = sec["data"]
            for k in range(0, len(d) - 11, 12):
                off, inf, add = struct.unpack_from(">IIi", d, k)
                typ = inf & 0xFF
                nm, val = struct.unpack_from(">II", sym, (inf >> 8) * 16)
                importe = val >= IMPORTS
                if self.lo <= off < self.hi:
                    nom = ""
                    if importe and noms:
                        nom = noms[nm:noms.index(b"\0", nm)].decode("ascii", "replace")
                    self.relocs[off & ~3] = (typ, val, add, nom, off & 3)
                    if importe and typ == R_PPC_REL24:
                        appels_imports.add(off & ~3)
                if importe or typ not in (R_PPC_ADDR32, R_PPC_ADDR16_LO):
                    continue
                t = (val + add) & 0xFFFFFFFF
                if self.lo <= t < self.hi:
                    refs.setdefault(t, []).append(off)
                    if not self.lo <= off < self.hi:
                        self.pointeurs.setdefault(t, []).append(off)
        entrees = set()
        if getattr(rpx, "entree", None):
            entrees.add(rpx.entree)
        for i, w in enumerate(mots):
            a = self.lo + 4 * i
            if (w >> 26 == 18 and w & 1 and a not in appels_imports) or (w >> 26 == 16 and w & 1):
                c = cible_branche(a, w)
                if c and c[0] != "abs" and self.lo <= c[1] < self.hi:
                    entrees.add(c[1])
        self.tables = {}                          # table de sauts -> nombre de cases (comme le portage)
        for t, sites in refs.items():
            if not any(self.lo <= x < self.hi and t - 40 <= x < t for x in sites):
                continue
            if t - 4 < self.lo or self.mot(t - 4) != 0x4E800420:
                continue
            nb = None
            for k in range(2, 12):
                if self.lo <= t - 4 * k and self.mot(t - 4 * k) >> 26 == 10:
                    nb = (self.mot(t - 4 * k) & 0xFFFF) + 1
                    break
            if nb is None:
                nb = 0
                while self.lo <= t + 4 * nb < self.hi and self.mot(t + 4 * nb) >> 26 == 18 \
                        and not self.mot(t + 4 * nb) & 3:
                    nb += 1
            self.tables[t] = nb
        entrees |= set(refs) - set(self.tables)
        entrees = {e for e in entrees if self.lo <= e < self.hi}
        # puis, comme le traducteur (tools/recomp/recomp.py, _fixpoint) : la cible d'un saut sans lien (b, bc) qui
        # tombe dans une autre fonction devient une entrée, jusqu'à stabilité
        sauts = []
        for i, w in enumerate(mots):
            op = w >> 26
            if op in (16, 18) and not w & 1:
                a = self.lo + 4 * i
                t = (_sext(w & (0x03FFFFFC if op == 18 else 0xFFFC), 26 if op == 18 else 16)
                     + (0 if w & 2 else a)) & 0xFFFFFFFF
                if self.lo <= t < self.hi:
                    sauts.append((a, t))
        while True:
            tri = sorted(entrees)
            neuves = set()
            for a, t in sauts:
                if t in entrees:
                    continue
                if bisect.bisect_right(tri, t) != bisect.bisect_right(tri, a):
                    neuves.add(t)
            if not neuves:
                break
            entrees |= neuves
        self.entrees = entrees
        self.debuts = sorted(self.entrees)

    def mot(self, a: int) -> int:
        return self.mots[(a - self.lo) >> 2]

    def fonction(self, a: int) -> tuple[int, int]:
        k = bisect.bisect_right(self.debuts, a) - 1
        if k < 0:
            raise ErreurGreffe(f"0x{a:08X} : avant le code")
        return self.debuts[k], (self.debuts[k + 1] if k + 1 < len(self.debuts) else self.hi)


# ---------------------------------------------------------------------------------------------------------------
# Ce que la greffe recopie
# ---------------------------------------------------------------------------------------------------------------
def fragments(analyse: dict) -> list[str]:
    """Fragments de la partie que la greffe porte (ordre de natif_code.FRAGMENTS)."""
    return [n for n in nc.FRAGMENTS if n in analyse["fragments"] and n in nc.GREFFES]


# sites refaits en C par le mod natif (0.58.0, rando_beedle.c) : la fonction qui les porte n'est plus recopiée.
# daNpcPeople_c::getMsg (0x022C6C68) : le carnet du portage la remplace aussi, et un seul mod peut remplacer une
# fonction ; recopiée, elle empêchait le carnet (et avec lui les pages en plus du Sac de livraison) de s'installer.
SITES_EN_C = frozenset({0x022C7A4C, 0x022C7D4C, 0x022C7E40})


def sites(analyse: dict) -> list[int]:
    """Adresses des mots du programme réécrits par les fragments greffés (sans ceux refaits en C : SITES_EN_C)."""
    fr = set(fragments(analyse))
    out = set()
    for nom in fr:
        if nom in nc.DOSSIERS:
            out |= set(nc.fragment(nom)["adresses"])
    for a, nom in nc._sites_fixes().items():
        if nom in fr:
            out.add(a)
    return sorted(out - SITES_EN_C)


def _lignes_cave(analyse: dict) -> list[str]:
    from rando import lot6 as L6
    out = []
    if "pnj" in fragments(analyse):
        # potions du Dr Bandam : code WWMP du lot 6 (sites 0x022313A8 et 0x02231414), objets de la table
        out += L6.BANDAM_CAVE.splitlines() + [f"    .int 0x{analyse['potions'].hex().upper()}0000"]
    for nom in fragments(analyse):
        if nom not in nc.DOSSIERS:
            continue
        for ligne in nc.fragment(nom)["cave"]:
            if "{{zunari}}" in ligne:
                ligne = ligne.replace("{{zunari}}", f".int 0x{analyse['zunari']:02X}000000")
            out.append(ligne)
    return out


def _symboles_jeu(analyse: dict) -> dict[str, int]:
    """Noms des fonctions du jeu citées par les fragments greffés (section [symboles] de leurs fichiers)."""
    from rando import lot6 as L6
    out = {n: a for a, n in L6.BANDAM_SYMBOLES.items()} if "pnj" in fragments(analyse) else {}
    for nom in fragments(analyse):
        if nom not in nc.DOSSIERS:
            continue
        with open(os.path.join(nc.DOSSIERS[nom], nom + ".asm"), encoding="utf-8") as f:
            partie = None
            for ligne in f.read().splitlines():
                s = ligne.strip()
                if s.startswith("; ["):
                    partie = s
                    continue
                if partie == "; [symboles]" and s and not s.startswith(";"):
                    a, n = s.rstrip(":").split("=")
                    out[n.strip()] = int(a.strip(), 16)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------------------------------------------
def construire(rpx, texte: str, plan: str, analyse: dict | None = None, programme: Programme | None = None) -> dict:
    """La greffe de ce ``patch_rando.asm`` généré : {elf (bytes), fonctions [(début, fin)], fragments, taille} ; None
    si la partie ne demande aucun fragment greffé. ``rpx`` : le RPX du dump (lu seulement)."""
    from jeu import emu_ppc as emu
    an = analyse if analyse is not None else nc.analyser(texte)
    if not fragments(an):
        return None
    pg = programme or Programme(rpx)
    plein = emu.Assembleur(texte, {}, permises=emu.PERMISES_MAILBOX).assembler()
    # (les immédiats des personnages ne sont réécrits que si l'objet change : un site absent garde son mot ; les
    # fragments de TWWHD, eux, sont complets : natif_code.analyser l'a vérifié)
    a_sites = [a for a in sites(an) if a in plein.mots]
    patch = {}
    sources = {adr: ins for adr, ins, _n in plein.patchs}
    for a in a_sites:
        if not pg.lo <= a < pg.hi:
            raise ErreurGreffe(f"site 0x{a:08X} hors du code du jeu")
        patch[a] = plein.mots[a]
    # cave des fragments greffés, assemblée seule (adresses de ses mots : Assembleur.code)
    syms = _symboles_jeu(an)
    lignes = [".origin = codecave"] + [f"0x{v:08X} = {k}:" for k, v in sorted(syms.items())] + _lignes_cave(an)
    cave = emu.Assembleur("\n".join(lignes), {}, permises=emu.PERMISES_MAILBOX).assembler()
    c0 = emu.CODECAVE
    c1 = cave.fin if cave.code else c0
    etiquettes_cave = {k: v for k, v in cave.labels.items() if k not in syms}
    # fonctions recopiées (sauf celles dont wwmp-natif refait les sites en C)
    fonctions = sorted({pg.fonction(a) for a in a_sites})
    a_sites = [a for a in a_sites if any(d <= a < f for d, f in fonctions)]
    for (d, f), (d2, _f2) in zip(fonctions, fonctions[1:]):
        if f > d2:
            raise ErreurGreffe(f"fonctions 0x{d:08X} et 0x{d2:08X} chevauchées")
    for d, _f in fonctions:
        if d not in pg.entrees:
            raise ErreurGreffe(f"début de fonction inconnu : 0x{d:08X}")
    # la cave : seulement ce que les sites recopiés atteignent ; le CODE (atteint en suivant les sauts) va dans .text,
    # les DONNÉES (citées en @ha / @l, jusqu'à l'étiquette suivante) dans .data : le traducteur du portage traduit
    # chaque mot de .text en instruction
    code_cave = {adr: ins for adr, ins, _n in cave.code}
    debuts_etiquettes = set(etiquettes_cave.values())
    pile = []
    for a in a_sites:
        mn = sources.get(a, "").split(None, 1)
        if len(mn) > 1 and mn[1].strip() in etiquettes_cave:
            pile.append(etiquettes_cave[mn[1].strip()])
    code, donnees = set(), set()
    while pile:
        x = pile.pop()
        if x in code or x not in code_cave:
            continue
        code.add(x)
        ins, w = code_cave[x], cave.mots[x]
        if "@" in ins:
            nom = ins.split("@")[0].split(",")[-1].strip()
            if nom not in etiquettes_cave:
                raise ErreurGreffe(f"cave 0x{x:08X} : {nom} n'est pas une étiquette de la cave")
            y = etiquettes_cave[nom]
            while y in code_cave:
                donnees.add(y)
                y += 4
                if y in debuts_etiquettes:
                    break
        cb = cible_branche(x, w)
        if cb and cb[0] != "abs" and c0 <= cb[1] < c1:
            pile.append(cb[1])
        if not (w in (0x4E800020, 0x4E800420) or (w >> 26 == 18 and not w & 1)):
            pile.append(x + 4)
    if code & donnees:
        raise ErreurGreffe(f"cave : mots à la fois code et données ({', '.join(f'0x{x:08X}' for x in sorted(code & donnees)[:4])})")
    place_cave, place_donnee = {}, {}
    for k, x in enumerate(sorted(code)):
        place_cave[x] = 4 * k
    entete = 0x30
    for k, x in enumerate(sorted(donnees)):
        place_donnee[x] = entete + 4 * k
    # disposition de .text : le code de la cave, puis chaque fonction
    neuf = {}
    pos = 4 * len(code)                            # (pas de bourrage : chaque mot de .text est traduit)
    for d, f in fonctions:
        neuf[d] = pos
        pos += f - d
    taille = pos

    def nouvelle(adr: int) -> int | None:
        """Décalage dans .text de l'adresse du jeu (ou du code de la cave) ``adr`` recopiée, None sinon."""
        if adr in place_cave:
            return place_cave[adr]
        for d, f in fonctions:
            if d <= adr < f:
                return neuf[d] + adr - d
        return None

    def debut_recopie(adr: int) -> bool:
        return any(adr == d for d, _f in fonctions)
    texte_b = bytearray(taille)
    data = bytearray(entete + 4 * len(donnees))
    stats = {}                                     # relocations du RPX vues dans les fonctions recopiées, par type
    rel_texte = []                                 # (décalage, type, symbole, addend)
    rel_data = []
    cases = set()                                  # cases de tables de sauts (décalages dans .text)

    def poser(off: int, w: int) -> None:
        struct.pack_into(">I", texte_b, off, w & 0xFFFFFFFF)

    def brancher(off: int, w: int, genre, cible: int, ou: str) -> None:
        """Mot de saut ``w`` posé à ``off`` vers l'adresse du jeu ``cible`` (ou de la cave)."""
        if genre == "abs":
            raise ErreurGreffe(f"{ou} : saut absolu")
        n = nouvelle(cible)
        # le début d'une fonction (recopiée ou non) : par le répartiteur (accroches des mods, remplacements) ; le
        # milieu d'une fonction recopiée ou la cave : dans la section
        interne = n is not None and not (genre == 18 and (debut_recopie(cible) or cible in pg.entrees))
        if interne:
            dd = n - off
            if genre == 18:
                poser(off, (w & ~0x03FFFFFC) | (dd & 0x03FFFFFC))
            else:
                if not -0x8000 <= dd < 0x8000:
                    raise ErreurGreffe(f"{ou} : saut conditionnel hors de portée")
                poser(off, (w & ~0xFFFC) | (dd & 0xFFFC))
            return
        if genre != 18:
            raise ErreurGreffe(f"{ou} : saut conditionnel vers 0x{cible:08X}, hors des fonctions recopiées")
        if cible not in pg.entrees and not debut_recopie(cible):
            raise ErreurGreffe(f"{ou} : saut vers 0x{cible:08X}, ni fonction du jeu ni fonction recopiée")
        poser(off, w & ~0x03FFFFFC)
        rel_texte.append((off, R_PPC_REL24, ("jeu", cible), 0))
    for x in sorted(donnees):
        struct.pack_into(">I", data, place_donnee[x], cave.mots[x])
    for adr in sorted(code):
        ins = code_cave[adr]
        off = place_cave[adr]
        w = cave.mots[adr]
        poser(off, w)
        mn = ins.split(None, 1)
        op = mn[1].strip() if len(mn) > 1 else ""
        cb = cible_branche(adr, w)
        if "@ha" in ins or "@l" in ins:
            nom = ins.split("@")[0].split(",")[-1].strip()
            y = etiquettes_cave[nom]
            if y in place_donnee:
                rel_texte.append((off + 2, R_PPC_ADDR16_HA if "@ha" in ins else R_PPC_ADDR16_LO, ("data", None),
                                  place_donnee[y]))
            elif y in place_cave:
                rel_texte.append((off + 2, R_PPC_ADDR16_HA if "@ha" in ins else R_PPC_ADDR16_LO, ("text", None),
                                  place_cave[y]))
            else:
                raise ErreurGreffe(f"cave 0x{adr:08X} : {nom} ni code ni données de la cave")
            poser(off, w & ~0xFFFF)
        elif cb is None:
            continue
        elif mn[0].startswith("b") and op in syms:
            brancher(off, w, *cb, f"cave 0x{adr:08X}")
        elif cb[0] == "abs" or not c0 <= cb[1] < c1:
            raise ErreurGreffe(f"cave 0x{adr:08X} : saut hors de la cave ({ins})")
        elif cb[1] not in place_cave:
            raise ErreurGreffe(f"cave 0x{adr:08X} : saut vers des données de la cave ({ins})")
        else:
            brancher(off, w, *cb, f"cave 0x{adr:08X}")
    # les fonctions
    for d, f in fonctions:
        for adr in range(d, f, 4):
            off = neuf[d] + adr - d
            w = patch.get(adr, pg.mot(adr))
            poser(off, w)
            src = sources.get(adr, "") if adr in patch else ""
            mn = src.split(None, 1)
            if adr in patch and mn and mn[0].startswith("b") and len(mn) > 1 and mn[1].strip() in cave.labels \
                    and mn[1].strip() not in syms:
                nom = mn[1].strip()
                if nom not in etiquettes_cave:
                    raise ErreurGreffe(f"site 0x{adr:08X} : {nom} absent de la cave greffée")
                genre = 18 if w >> 26 == 18 else 16
                cible = etiquettes_cave[nom]
                if cible not in place_cave:
                    raise ErreurGreffe(f"site 0x{adr:08X} : {nom} n'est pas du code de la cave")
                dd = place_cave[cible] - off
                poser(off, (w & ~0x03FFFFFC) | (dd & 0x03FFFFFC) if genre == 18 else (w & ~0xFFFC) | (dd & 0xFFFC))
                if genre == 16 and not -0x8000 <= dd < 0x8000:
                    raise ErreurGreffe(f"site 0x{adr:08X} : saut conditionnel hors de portée")
                continue
            if adr not in patch and adr in pg.relocs:
                typ, val, add, nom, sous = pg.relocs[adr]
                stats[typ] = stats.get(typ, 0) + 1
                if nom:
                    if typ == R_PPC_REL24 and sous == 0 and w >> 26 == 18:
                        # appel d'une fonction importée d'un RPL : service du SDK de même nom (memcpy…), sinon par
                        # l'adresse de son « import slot », que le répartiteur du portage connaît (runtime core.cpp :
                        # « imported functions are reachable through their import slot addresses »)
                        poser(off, w & ~0x03FFFFFC)
                        rel_texte.append((off, R_PPC_REL24, ("svc", nom) if nom in SERVICES else ("jeu", val), 0))
                        continue
                    raise ErreurGreffe(f"0x{adr:08X} : donnée importée {nom} (relocation {typ})")
                s = (val + add) & 0xFFFFFFFF
                attendu = {R_PPC_ADDR16_LO: s & 0xFFFF, 5: s >> 16, R_PPC_ADDR16_HA: ((s + 0x8000) >> 16) & 0xFFFF}
                if typ in attendu and sous == 2 and w & 0xFFFF != attendu[typ]:
                    raise ErreurGreffe(f"0x{adr:08X} : relocation {typ} pas déjà appliquée dans le RPX")
                if typ == R_PPC_REL24 and (cible_branche(adr, w) or (0, -1))[1] != s:
                    raise ErreurGreffe(f"0x{adr:08X} : appel relogé vers 0x{s:08X}, mot différent")
            cb = cible_branche(adr, w)
            if cb is None:
                continue
            genre, cible = cb
            if genre != "abs" and d <= cible < f:
                continue                                    # même fonction : même déplacement
            brancher(off, w, genre, cible, f"0x{adr:08X}")
        # pointeurs des données vers le milieu de la fonction : refus
        for t, ou in pg.pointeurs.items():
            if d < t < f:
                raise ErreurGreffe(f"0x{t:08X} (milieu de 0x{d:08X}) cité par les données en 0x{ou[0]:08X}")
        # paires lis/addi qui forment une adresse au milieu d'une fonction recopiée (tables de sauts)
        for adr in range(d, f, 4):
            w = patch.get(adr, pg.mot(adr))
            if w >> 26 != 15:
                continue
            rx, hi = (w >> 21) & 31, w & 0xFFFF
            for k in range(1, 7):
                b = adr + 4 * k
                if b >= f:
                    break
                w2 = patch.get(b, pg.mot(b))
                op2, rd2, ra2 = w2 >> 26, (w2 >> 21) & 31, (w2 >> 16) & 31
                if op2 in (14, 12) and ra2 == rx:
                    v = ((hi << 16) + _sext(w2 & 0xFFFF, 16)) & 0xFFFFFFFF
                elif op2 == 24 and rd2 == rx:
                    v = (hi << 16) | (w2 & 0xFFFF)
                else:
                    if (op2 in (14, 15, 32, 34, 40, 42) and rd2 == rx) or (op2 == 24 and ra2 == rx):
                        break                               # rx récrit
                    continue
                n = nouvelle(v)
                if n is not None and not debut_recopie(v) and not c0 <= v < c1:
                    rel_texte.append((neuf[d] + adr - d + 2, R_PPC_ADDR16_HA if op2 != 24 else 5, ("text", None), n))
                    rel_texte.append((neuf[d] + b - d + 2, R_PPC_ADDR16_LO, ("text", None), n))
                    poser(neuf[d] + adr - d, w & ~0xFFFF)
                    poser(neuf[d] + b - d, w2 & ~0xFFFF)
                    t = v
                    while nouvelle(t) is not None and (patch.get(t, pg.mot(t)) >> 26 == 18
                                                       and not patch.get(t, pg.mot(t)) & 3):
                        cases.add(nouvelle(t))
                        t += 4
                    if not any(c == n for c in cases):
                        raise ErreurGreffe(f"0x{adr:08X} : adresse 0x{v:08X} dans une fonction recopiée, sans table")
                break
    # .data : marque (0x30 octets), données de la cave, puis les cases des tables de sauts
    fonctions_txt = [(d, f) for d, f in fonctions]
    data[:entete] = MAGIE + struct.pack(">II", FORMAT, len(fonctions)) + bytes.fromhex(plan)
    for c in sorted(cases):
        rel_data.append((len(data), R_PPC_ADDR32, ("text", None), c))
        data += b"\0\0\0\0"
    # .wwhd_hooks : un remplacement par fonction recopiée
    hooks = bytearray()
    rel_hooks = []
    for d, _f in fonctions:
        rel_hooks.append((len(hooks) + 8, R_PPC_ADDR32, ("text", None), neuf[d]))
        hooks += struct.pack(">IIII", 1, d, 0, 0)
    elf = _ecrire_elf(bytes(texte_b), bytes(data), bytes(hooks), rel_texte, rel_data, rel_hooks,
                      [(f"wwmp_greffe_{d:08X}", neuf[d], f - d) for d, f in fonctions])
    return {"elf": elf, "fonctions": fonctions_txt, "fragments": fragments(an), "taille": taille,
            "cases": len(cases), "imports": len({r[2][1] for r in rel_texte if r[2][0] == "jeu"}),
            "services": sorted({r[2][1] for r in rel_texte if r[2][0] == "svc"}), "relocs_rpx": stats,
            "carte_cave": dict(place_cave), "carte_donnees": dict(place_donnee)}


def _ecrire_elf(texte: bytes, data: bytes, hooks: bytes, rel_t, rel_d, rel_h, fonctions) -> bytes:
    """ELF PowerPC 32 bits gros-boutiste relogeable (ET_REL), comme ``ld.lld -r`` : .text, .data, .wwhd_hooks, leurs
    .rela, .symtab (symboles de section, fonctions, imports __wwhd_game_0x<ADR>), .strtab, .shstrtab."""
    noms_sec = [b"", b".text", b".data", b".wwhd_hooks", b".rela.text", b".rela.data", b".rela.wwhd_hooks",
                b".symtab", b".strtab", b".shstrtab"]
    shstr = bytearray(b"\0")
    off_nom = []
    for n in noms_sec:
        if not n:
            off_nom.append(0)
            continue
        off_nom.append(len(shstr))
        shstr += n + b"\0"
    strtab = bytearray(b"\0")
    syms = [struct.pack(">IIIBBH", 0, 0, 0, 0, 0, 0)]
    for idx in (1, 2, 3):                               # symboles de section (locaux)
        syms.append(struct.pack(">IIIBBH", 0, 0, 0, 3, 0, idx))
    for nom, val, taille in fonctions:                  # fonctions recopiées (locales)
        o = len(strtab)
        strtab += nom.encode() + b"\0"
        syms.append(struct.pack(">IIIBBH", o, val, taille, 2, 0, 1))
    premier_global = len(syms)
    imports = {}
    for rel in rel_t:
        if rel[2][0] in ("jeu", "svc") and rel[2] not in imports:
            o = len(strtab)
            nom = f"__wwhd_game_0x{rel[2][1]:08X}" if rel[2][0] == "jeu" else rel[2][1]
            strtab += nom.encode() + b"\0"
            imports[rel[2]] = len(syms)
            syms.append(struct.pack(">IIIBBH", o, 0, 0, 0x10, 0, 0))

    def rela(liste) -> bytes:
        out = bytearray()
        for off, typ, (genre, v), addend in liste:
            s = imports[(genre, v)] if genre in ("jeu", "svc") else {"text": 1, "data": 2}[genre]
            out += struct.pack(">IIi", off, (s << 8) | typ, addend)
        return bytes(out)
    corps = [(texte, SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 0, 0, 16, 0),
             (data, SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, 0, 0, 4, 0),
             (hooks, SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, 0, 0, 4, 0),
             (rela(rel_t), SHT_RELA, 0, 7, 1, 4, 12),
             (rela(rel_d), SHT_RELA, 0, 7, 2, 4, 12),
             (rela(rel_h), SHT_RELA, 0, 7, 3, 4, 12),
             (b"".join(syms), SHT_SYMTAB, 0, 8, premier_global, 4, 16),
             (bytes(strtab), SHT_STRTAB, 0, 0, 0, 1, 0),
             (bytes(shstr), SHT_STRTAB, 0, 0, 0, 1, 0)]
    out = bytearray(52)
    entetes = [bytes(40)]
    for k, (contenu, typ, flags, link, info, align, entsize) in enumerate(corps, 1):
        while len(out) % max(align, 4):
            out += b"\0"
        entetes.append(struct.pack(">IIIIIIIIII", off_nom[k], typ, flags, 0, len(out), len(contenu), link, info,
                                   align, entsize))
        out += contenu
    while len(out) % 4:
        out += b"\0"
    shoff = len(out)
    out += b"".join(entetes)
    struct.pack_into(">4sBBBB8sHHIIIIIHHHHHH", out, 0, b"\x7fELF", 1, 2, 1, 0, bytes(8), 1, 20, 1, 0, 0, shoff, 0, 52,
                     0, 0, 40, len(entetes), len(entetes) - 1)
    return bytes(out)


def manifeste(plan: str, fonctions: int) -> dict:
    return {"format_version": 1, "id": ID, "name": "WWMP Rando (generated game code)", "version": "1.0.0",
            "game_id": "wwhd", "minimum_manager_version": "1.5.0", "kind": "guest",
            "guest": {"api_version": 1, "elf": "mod.elf"},
            "dependencies": [{"id": nc.ID_MOD_CODE, "minimum_version": "0.10.0"}],
            "author": "WWMP",
            "description": f"Game code generated on this PC from your own dump for the co-op game {plan[:12]}: "
                           f"{fonctions} game functions with the randomizer's changes (TWWHD-Randomizer, SuperDude88, "
                           "MIT; WWMP), exactly as the Cemu pack. Never share this folder.",
            "setup": [{"id": "build", "type": "build_guest_mod", "title": "Build the generated game code"}]}


def paquet(rpx, texte: str, plan: str, analyse: dict | None = None) -> dict | None:
    """{"manifest.json": bytes, "mod.elf": bytes, ...} du paquet de la greffe ; None sans fragment greffé."""
    g = construire(rpx, texte, plan, analyse)
    if g is None:
        return None
    man = json.dumps(manifeste(plan, len(g["fonctions"])), indent=1, ensure_ascii=True).encode("utf-8")
    return {"manifest.json": man, "mod.elf": g["elf"], "_greffe": g}


def lire_marque(data: bytes) -> dict | None:
    """{format, fonctions, plan} de la marque WWMPGRF1 (0x30 octets) ; None si ce n'en est pas une."""
    if len(data) < 0x30 or data[:8] != MAGIE:
        return None
    fmt, n = struct.unpack_from(">II", data, 8)
    return {"format": fmt, "fonctions": n, "plan": data[0x10:0x30].hex()}
