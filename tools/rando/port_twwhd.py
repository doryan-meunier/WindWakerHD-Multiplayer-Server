#!/usr/bin/env python3
"""Outil de portage : correctifs de TWWHD-Randomizer -> lignes de pack graphique Cemu.

Les correctifs repris ici (fichiers ``asm/patch_diffs/*_diff.yaml`` et ``asm/custom_symbols.yaml``)
viennent de TWWHD-Randomizer, de SuperDude88 et de ses contributeurs, sous licence MIT,
« Copyright (c) 2024 SuperDude88 » (texte : tools/rando/logique/LICENSE_TWWHD.txt). Toute
sortie de cet outil porte cette mention en tête. Plan : docs/etudes/portage_twwhd.md.

OUTIL DE DÉVELOPPEMENT. Il ne lit que des fichiers (la copie de TWWHD-Randomizer, cking.rpx en
lecture seule, nos packs), n'écrit rien dans le jeu et ne touche pas à Cemu. Il ne modifie pas le
pack : il écrit des lignes à relire puis à coller à la main dans patch_mailbox.asm, où
tests/test_pack_mailbox.py les vérifiera comme le reste.

Ce que TWWHD fait et ce que nous faisons :

* TWWHD réécrit cking.rpx. Son code ajouté est lié à une adresse fixe, à la fin du .text
  (0x028F87F4 .. 0x028FB6F4), et les sites « en place » y sautent.
* Nous ne modifions aucun fichier du jeu : le code ajouté va dans le codecave du pack, dont
  l'adresse n'est pas celle de TWWHD. Tout ce qui dépend de l'adresse doit donc redevenir
  symbolique : sauts du jeu vers le code ajouté, sauts du code ajouté vers le jeu, adresses
  internes chargées par ``lis`` / ``addi`` (relocations HA / LO) ou posées en ``.int`` (ADDR32).

Règles de sortie (les mêmes que le pack v19) :

* dans le codecave, un mot qui ne dépend pas de l'adresse sort en ``.int 0x...`` (octets exacts,
  aucune mnémonique nouvelle demandée à Cemu) ; un mot qui en dépend sort avec une mnémonique déjà
  acceptée (``b``, ``bl``, ``lis x@ha``, ``addi x@l``, ``lwz x@l(rA)``...) ;
* un site en place sort avec une mnémonique déjà acceptée, et seulement si le mini assembleur de
  test (tools/jeu/emu_ppc.py) redonne exactement le mot de TWWHD ; sinon le site est REFUSÉ
  (à porter à la main par un saut vers le codecave) ;
* chaque site en place porte en commentaire le mot d'origine lu dans cking.rpx (``avant=``) ;
  ``verifier_avant`` relit ces commentaires et les compare au RPX ;
* un site qui tombe sur un mot déjà accroché par un de nos packs est une COLLISION ;
* ce que l'outil ne sait pas reloger sans risque est refusé avec la raison, jamais deviné : cible
  interne non alignée sur 4 octets, cible hors de la sélection, branchement conditionnel qui
  sort de son bloc, mot qui ressemble autant à du texte qu'à un saut.

    python tools/rando/port_twwhd.py SOURCE_TWWHD --bilan
    python tools/rando/port_twwhd.py SOURCE_TWWHD --site 0x0221543C --site 0x02215450
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import struct
import sys

_ICI = os.path.dirname(os.path.abspath(__file__))
from jeu import emu_ppc as emu  # noqa: E402

LIBRE_DEBUT = 0x028F87F4            # fin du .text de cking.rpx = début du code ajouté par TWWHD
LIBRE_FIN = 0x02900000              # borne large : rien du jeu au-delà dans le .text
TEXTE_DEBUT = 0x02000000
DONNEES_DEBUT = 0x10000000
BASES_SYMBOLE = {1: TEXTE_DEBUT, 2: DONNEES_DEBUT}      # r_info >> 8 : section de la cible
R_ADDR32, R_LO, R_HA = 1, 4, 6                           # r_info & 0xFF
TAILLE_MAX_ZONE = 0x8000                                 # limite d'une zone du pack

ATTRIBUTION = (
    "; Code repris de TWWHD-Randomizer (SuperDude88 et contributeurs), licence MIT,\n"
    "; « Copyright (c) 2024 SuperDude88 ». Texte : tools/wwmp/rando_logique/LICENSE_TWWHD.txt.\n"
    "; Converti par tools/wwmp/rando_port_twwhd.py : relu à la main avant d'entrer dans le pack.\n")

_D_NOMS = {v: k for k, v in emu.D_FORM.items()}
_F_NOMS = {v: k for k, v in emu.F_FORM.items()}
_COND_NOMS = {v: k for k, v in emu.COND.items()}


class ErreurPortage(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# lecture des fichiers de TWWHD (format régulier : pas besoin de PyYAML)

def lire_diff(texte: str) -> tuple[dict[int, bytes], list[tuple[int, int, int]]]:
    """Lit un ``*_diff.yaml`` : ({adresse: octets}, [(r_offset, r_info, r_addend)])."""
    coupe = re.search(r"^Relocations:", texte, re.M)
    partie_data = texte[:coupe.start()] if coupe else texte
    partie_rel = texte[coupe.start():] if coupe else ""
    if not re.search(r"^Data:", partie_data, re.M):
        raise ErreurPortage("diff sans section Data")
    data: dict[int, bytes] = {}
    for m in re.finditer(r"^\s+(0x[0-9A-Fa-f]+|\d+):\s*\[([^\]]*)\]", partie_data, re.M):
        adr = int(m.group(1), 0)
        try:
            octets = bytes(int(x, 0) for x in m.group(2).replace("\n", " ").split(",") if x.strip())
        except ValueError as e:
            raise ErreurPortage(f"octets illisibles à {adr:#x} : {e}") from None
        if adr in data:
            raise ErreurPortage(f"adresse {adr:#x} donnée deux fois dans le même fichier")
        data[adr] = octets
    restant = re.sub(r"^\s+(0x[0-9A-Fa-f]+|\d+):\s*\[[^\]]*\]", "", partie_data, flags=re.M)
    restant = re.sub(r"^Data:\s*(\{\})?", "", restant, flags=re.M).strip()
    if restant:
        raise ErreurPortage(f"section Data : texte non compris : {restant[:60]!r}")
    relocs = []
    for ligne in partie_rel.splitlines()[1:]:
        if not ligne.strip():
            continue
        m = re.fullmatch(r"\s*-\s*\{(.*)\}\s*", ligne)
        if not m:
            raise ErreurPortage(f"relocation illisible : {ligne!r}")
        champs = {}
        for morceau in m.group(1).split(","):
            cle, _, val = morceau.partition(":")
            champs[cle.strip()] = int(val.strip(), 0)
        if set(champs) != {"r_addend", "r_info", "r_offset"}:
            raise ErreurPortage(f"relocation incomplète : {ligne!r}")
        relocs.append((champs["r_offset"], champs["r_info"], champs["r_addend"]))
    return data, relocs


def lire_symboles(texte: str) -> dict[str, int]:
    """Lit ``custom_symbols.yaml`` : {nom: adresse}."""
    out = {}
    for ligne in texte.splitlines():
        if not ligne.strip() or ligne.lstrip().startswith("#"):
            continue
        m = re.fullmatch(r"(\w+):\s*(0x[0-9A-Fa-f]+|\d+)\s*", ligne)
        if not m:
            raise ErreurPortage(f"symbole illisible : {ligne!r}")
        out[m.group(1)] = int(m.group(2), 0)
    return out


def accroches_pack(texte_asm: str) -> dict[int, str]:
    """Mots du jeu qu'un de nos packs remplace : {adresse: instruction} (lignes ``0x... = instr``)."""
    out = {}
    for brut in texte_asm.splitlines():
        ligne = brut.split(";", 1)[0].strip()
        m = re.fullmatch(r"(0x[0-9A-Fa-f]+)\s*=\s*(.+)", ligne)
        if m and not re.fullmatch(r"\w+:", m.group(2).strip()):
            out[int(m.group(1), 16)] = m.group(2).strip()
    return out


def symboles_pack(texte_asm: str) -> dict[int, str]:
    """Fonctions du jeu déjà nommées par un de nos packs : {adresse: nom} (``0x... = nom:``)."""
    out = {}
    for brut in texte_asm.splitlines():
        m = re.fullmatch(r"(0x[0-9A-Fa-f]+)\s*=\s*(\w+):", brut.split(";", 1)[0].strip())
        if m:
            out.setdefault(int(m.group(1), 16), m.group(2))
    return out


# ---------------------------------------------------------------------------------------------
# petit désassembleur : ne sert qu'à proposer un texte, toujours revérifié par l'assembleur de test

def _s16(x: int) -> int:
    return x - 0x10000 if x & 0x8000 else x


def cible_saut(mot: int, pc: int) -> int | None:
    """Cible d'un ``b`` / ``bl`` relatif (opcode 18, AA = 0), sinon None."""
    if mot >> 26 != 18 or mot & 2:
        return None
    li = mot & 0x03FFFFFC
    if li & 0x02000000:
        li -= 0x04000000
    return (pc + li) & 0xFFFFFFFF


def cible_cond(mot: int, pc: int) -> int | None:
    """Cible d'un branchement conditionnel relatif (opcode 16, AA = 0), sinon None."""
    if mot >> 26 != 16 or mot & 2:
        return None
    return (pc + _s16(mot & 0xFFFC)) & 0xFFFFFFFF


def _texte_ascii(mot: int) -> bool:
    return all(0x20 <= b <= 0x7E for b in struct.pack(">I", mot))


def proposer(mot: int, pc: int, nom_cible=None) -> str | None:
    """Texte d'assembleur possible pour ``mot`` (None si hors du sous-ensemble connu).

    ``nom_cible(adresse)`` rend le nom à écrire pour une cible de saut (ou None)."""
    op, rd, ra, rb = mot >> 26, (mot >> 21) & 31, (mot >> 16) & 31, (mot >> 11) & 31
    imm = mot & 0xFFFF
    if mot == 0x60000000:
        return "ori r0, r0, 0"
    if mot == 0x4E800020:
        return "blr"
    if op in _D_NOMS:
        return f"{_D_NOMS[op]} r{rd}, {_s16(imm)}(r{ra})"
    if op in _F_NOMS:
        return f"{_F_NOMS[op]} f{rd}, {_s16(imm)}(r{ra})"
    if op == 14:
        return f"li r{rd}, {_s16(imm)}" if ra == 0 else f"addi r{rd}, r{ra}, {_s16(imm)}"
    if op == 15 and ra == 0:
        return f"lis r{rd}, 0x{imm:X}"
    if op == 24:
        return f"ori r{ra}, r{rd}, 0x{imm:X}"
    if op == 28:
        return f"andi. r{ra}, r{rd}, 0x{imm:X}"
    if op == 11 and rd == 0:
        return f"cmpwi r{ra}, {_s16(imm)}"
    if op == 10 and rd == 0:
        return f"cmplwi r{ra}, 0x{imm:X}"
    if op == 21 and not mot & 1:
        return f"rlwinm r{ra}, r{rd}, {rb}, {(mot >> 6) & 31}, {(mot >> 1) & 31}"
    if op == 18 and not mot & 2:
        c = cible_saut(mot, pc)
        nom = nom_cible(c) if nom_cible else None
        return f"{'bl' if mot & 1 else 'b'} {nom or f'0x{c:08X}'}"
    if op == 16 and not mot & 3:
        cle = (rd, ra)
        if cle in _COND_NOMS:
            c = cible_cond(mot, pc)
            nom = nom_cible(c) if nom_cible else None
            return f"{_COND_NOMS[cle]} {nom or f'0x{c:08X}'}"
    if op == 31:
        xo = (mot >> 1) & 0x3FF
        if mot & 1:
            return None
        if xo == 444 and rd == rb:
            return f"mr r{ra}, r{rd}"
        if xo == 266:
            return f"add r{rd}, r{ra}, r{rb}"
        if xo == 40:
            return f"subf r{rd}, r{ra}, r{rb}"
        if xo == 28:
            return f"and r{ra}, r{rd}, r{rb}"
        if xo == 536:
            return f"srw r{ra}, r{rd}, r{rb}"
        if xo == 87 and ra:
            return f"lbzx r{rd}, r{ra}, r{rb}"
        if xo == 23 and ra:
            return f"lwzx r{rd}, r{ra}, r{rb}"
        if xo == 922 and rb == 0:
            return f"extsh r{ra}, r{rd}"
        if xo == 0 and rd == 0:
            return f"cmpw r{ra}, r{rb}"
        if xo == 339 and (rb << 5 | ra) == 8:
            return f"mflr r{rd}"
        if xo == 467 and (rb << 5 | ra) == 8:
            return f"mtlr r{rd}"
    return None


def _encode(texte: str, pc: int, labels: dict[str, int]) -> int | None:
    a = emu.Assembleur("", permises=emu.PERMISES_MAILBOX)
    a.labels = dict(labels)
    try:
        return a._encoder(texte, pc)
    except (emu.ErreurAsm, ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------------------------

class Source:
    """Les correctifs de TWWHD, tous fichiers confondus."""

    def __init__(self, diffs: dict[str, tuple[dict[int, bytes], list]], symboles: dict[str, int] | None = None):
        self.symboles = dict(symboles or {})
        self.libre = {}                 # adresse d'octet -> valeur (code ajouté)
        self.sites: dict[int, tuple[str, bytes]] = {}        # sites en place du .text
        self.donnees: dict[int, tuple[str, bytes]] = {}      # écritures dans les données du jeu
        self.relocs: dict[int, tuple[int, int]] = {}         # r_offset -> (type, cible)
        self.doubles: list[str] = []
        occupe: dict[int, str] = {}
        for nom in sorted(diffs):
            data, relocs = diffs[nom]
            for adr in sorted(data):
                octets = data[adr]
                for k in range(len(octets)):
                    if adr + k in occupe:
                        self.doubles.append(f"{adr + k:#010x} : écrit par {occupe[adr + k]} et par {nom}")
                    occupe[adr + k] = nom
                if LIBRE_DEBUT <= adr < LIBRE_FIN:
                    for k, b in enumerate(octets):
                        self.libre[adr + k] = b
                elif TEXTE_DEBUT <= adr < LIBRE_DEBUT:
                    if adr & 3 or len(octets) & 3:
                        raise ErreurPortage(f"site {adr:#010x} ({nom}) : pas un nombre entier de mots")
                    self.sites[adr] = (nom, octets)
                elif adr >= DONNEES_DEBUT:
                    self.donnees[adr] = (nom, octets)
                else:
                    raise ErreurPortage(f"adresse {adr:#x} ({nom}) hors du jeu")
            for off, info, addend in relocs:
                base = BASES_SYMBOLE.get(info >> 8)
                if base is None or info & 0xFF not in (R_ADDR32, R_LO, R_HA):
                    raise ErreurPortage(f"relocation inconnue {info:#x} à {off:#x} ({nom})")
                self.relocs[off] = (info & 0xFF, (base + addend) & 0xFFFFFFFF)
        self.libre_fin = max(self.libre) + 1 if self.libre else LIBRE_DEBUT
        self._bornes = sorted({a for a in self.symboles.values() if LIBRE_DEBUT <= a < LIBRE_FIN}
                              | {LIBRE_DEBUT, self.libre_fin})

    @classmethod
    def depuis_dossier(cls, dossier: str) -> "Source":
        asm = dossier if os.path.isdir(os.path.join(dossier, "patch_diffs")) else os.path.join(dossier, "asm")
        chemins = sorted(glob.glob(os.path.join(asm, "patch_diffs", "*_diff.yaml")))
        if not chemins:
            raise ErreurPortage(f"aucun *_diff.yaml sous {asm}")
        diffs = {}
        for p in chemins:
            with open(p, encoding="utf-8") as f:
                diffs[os.path.basename(p)[:-len("_diff.yaml")]] = lire_diff(f.read())
        with open(os.path.join(asm, "custom_symbols.yaml"), encoding="utf-8") as f:
            return cls(diffs, lire_symboles(f.read()))

    def interne(self, adr: int) -> bool:
        return LIBRE_DEBUT <= adr < self.libre_fin

    def bloc(self, adr: int) -> tuple[int, int]:
        """Bloc du code ajouté qui contient ``adr`` : du symbole qui précède au symbole qui suit,
        arrondi à des mots entiers."""
        if not self.interne(adr):
            raise ErreurPortage(f"{adr:#010x} n'est pas dans le code ajouté")
        debut = max(b for b in self._bornes if b <= adr)
        fin = min(b for b in self._bornes if b > adr)
        return debut & ~3, (fin + 3) & ~3

    def mot_libre(self, adr: int) -> int:
        return struct.unpack(">I", bytes(self.libre.get(adr + k, 0) for k in range(4)))[0]

    def reloc_du_mot(self, adr: int) -> tuple[int, int] | None:
        """Relocation qui touche le mot à ``adr`` : (type, cible)."""
        if adr in self.relocs and self.relocs[adr][0] == R_ADDR32:
            return self.relocs[adr]
        if adr + 2 in self.relocs and self.relocs[adr + 2][0] in (R_LO, R_HA):
            return self.relocs[adr + 2]
        for k in (1, 3):
            if adr + k in self.relocs:
                raise ErreurPortage(f"relocation à cheval sur le mot {adr:#010x}")
        if adr in self.relocs or adr + 2 in self.relocs:
            raise ErreurPortage(f"relocation mal placée dans le mot {adr:#010x}")
        return None

    def nom_de(self, adr: int) -> str | None:
        for nom, a in self.symboles.items():
            if a == adr:
                return nom
        return None


class Conversion:
    """Résultat d'une conversion : lignes de pack, refus, collisions."""

    def __init__(self):
        self.symboles: dict[int, str] = {}       # fonctions du jeu à déclarer (0x... = nom:)
        self.cave: list[str] = []                # code ajouté, à mettre dans une zone du codecave
        self.sites: list[tuple[int, int | None, int, str]] = []   # (adresse, avant, après, texte)
        self.donnees: list[tuple[int, bytes | None, bytes, str]] = []   # écritures de données (au client)
        self.refus: list[str] = []
        self.collisions: list[str] = []
        self.notes: list[str] = []
        self.taille = 0                          # octets de codecave

    @property
    def propre(self) -> bool:
        return not self.refus and not self.collisions

    def texte(self, forcer: bool = False) -> str:
        if not self.propre and not forcer:
            raise ErreurPortage("conversion refusée :\n  " + "\n  ".join(self.refus + self.collisions))
        out = [ATTRIBUTION]
        if not self.propre:
            out += [f"; NON UTILISABLE TEL QUEL : {x}" for x in self.refus + self.collisions]
        out += [f"; note : {x}" for x in self.notes]
        if self.symboles:
            out.append("; fonctions du jeu appelées (à mettre avec les autres symboles, avant .origin)")
            out += [f"0x{a:08X} = {n}:" for a, n in sorted(self.symboles.items())]
        if self.cave:
            out.append(f"; code ajouté : {self.taille} octets de codecave")
            out.append(".origin = codecave")
            out += self.cave
        if self.sites:
            out.append("; sites en place (à mettre avec les autres accroches, en fin de fichier)")
            out += [f"0x{a:08X} = {t:<28} ; twwhd avant={'????????' if av is None else f'{av:08X}'} "
                    f"apres={ap:08X}" for a, av, ap, t in self.sites]
        for a, av, ap, nom in self.donnees:
            out.append(f"; donnée (écriture par le client, pas par le pack) {a:#010x} : "
                       f"{'?' if av is None else av.hex()} -> {ap.hex()} ({nom})")
        return "\n".join(out) + "\n"


def convertir(src: Source, sites, rpx=None, packs: dict[str, str] | None = None,
              prefixe: str = "twp_", blocs_en_plus=()) -> Conversion:
    """Convertit les sites en place ``sites`` (adresses de début, telles que dans les diffs) et
    tout le code ajouté dont ils dépendent.

    ``rpx`` : objet avec ``octets(adresse, n)`` (tests/test_pack_coop.Rpx), pour lire les mots
    d'origine. ``packs`` : {nom: texte asm} de nos packs, pour les collisions et les noms déjà
    donnés aux fonctions du jeu. ``blocs_en_plus`` : adresses du code ajouté à embarquer même si
    aucun site choisi n'y saute."""
    conv = Conversion()
    packs = packs or {}
    accroches: dict[int, str] = {}
    deja_nommes: dict[int, str] = {}
    for nom, texte in packs.items():
        for a, ins in accroches_pack(texte).items():
            accroches[a] = f"{nom} ({ins})"
        for a, n in symboles_pack(texte).items():
            deja_nommes.setdefault(a, n)

    choisis = []
    for s in sites:
        if s in src.sites:
            choisis.append(s)
        elif s in src.donnees:
            nom, octets = src.donnees[s]
            avant = None
            if rpx is not None:
                try:
                    avant = rpx.octets(s, len(octets))
                except KeyError:
                    conv.refus.append(f"donnée {s:#010x} ({nom}) : hors des sections du RPX")
            if any(s <= off < s + len(octets) for off in src.relocs):
                conv.notes.append(f"donnée {s:#010x} : contient une adresse (relocation), à vérifier")
            conv.donnees.append((s, avant, octets, nom))
        else:
            conv.refus.append(f"{s:#010x} : pas un début de site de TWWHD")

    # 1. fermeture : blocs du code ajouté atteints depuis les sites choisis
    blocs: set[tuple[int, int]] = set()
    a_voir = []

    def demander(adr: int, depuis: str) -> None:
        if not src.interne(adr):
            return
        b = src.bloc(adr)
        if b not in blocs:
            blocs.add(b)
            a_voir.append(b)

    for b in blocs_en_plus:
        demander(b, "demande")
    for s in choisis:
        nom, octets = src.sites[s]
        for k in range(0, len(octets), 4):
            mot, = struct.unpack_from(">I", octets, k)
            for c in (cible_saut(mot, s + k), cible_cond(mot, s + k)):
                if c is not None:
                    demander(c, f"site {s + k:#x}")
            r = src.reloc_du_mot(s + k)
            if r:
                demander(r[1], f"site {s + k:#x}")
    while a_voir:
        debut, fin = a_voir.pop()
        for adr in range(debut, fin, 4):
            mot = src.mot_libre(adr)
            r = src.reloc_du_mot(adr)
            if r:
                demander(r[1], f"bloc {debut:#x}")
            elif not _texte_ascii(mot):
                c = cible_saut(mot, adr)
                if c is not None:
                    demander(c, f"bloc {debut:#x}")

    # blocs fusionnés en plages contiguës, dans l'ordre des adresses
    plages: list[list[int]] = []
    for debut, fin in sorted(blocs):
        if plages and debut <= plages[-1][1]:
            plages[-1][1] = max(plages[-1][1], fin)
        else:
            plages.append([debut, fin])

    def plage_de(adr: int):
        for p in plages:
            if p[0] <= adr < p[1]:
                return p
        return None

    # 2. étiquettes : une par mot du code ajouté embarqué qui est visé de quelque part
    cibles: set[int] = {p[0] for p in plages}
    refus_cibles: set[int] = set()

    def viser(adr: int, qui: str) -> bool:
        if adr & 3:
            if adr not in refus_cibles:
                refus_cibles.add(adr)
                conv.refus.append(f"{qui} : cible interne {adr:#010x} non alignée sur 4 octets "
                                  f"(à reloger à la main)")
            return False
        if plage_de(adr) is None:
            conv.refus.append(f"{qui} : cible interne {adr:#010x} hors de la sélection")
            return False
        cibles.add(adr)
        return True

    def etiquette(adr: int) -> str:
        return f"{prefixe}{adr:08X}"

    def nom_jeu(adr: int) -> str:
        if adr not in conv.symboles:
            conv.symboles[adr] = deja_nommes.get(adr) or f"{prefixe}jeu_{adr:08X}"
        return conv.symboles[adr]

    def dans_jeu(adr: int) -> bool:
        return TEXTE_DEBUT <= adr < LIBRE_DEBUT and not adr & 3

    def mot_symbolique(mot: int, adr: int, qui: str, en_place: bool) -> str | None:
        """Texte d'un mot qui dépend de l'adresse, None s'il n'en dépend pas ; '' si refusé."""
        r = src.reloc_du_mot(adr)
        if r:
            typ, cible = r
            if not src.interne(cible):
                return None                              # adresse du jeu : fixe sous Cemu
            if not viser(cible, qui):
                return ""
            lab = etiquette(cible)
            op, rd, ra = mot >> 26, (mot >> 21) & 31, (mot >> 16) & 31
            if typ == R_ADDR32:
                if en_place:
                    conv.refus.append(f"{qui} : adresse interne posée en place (.int non prouvé hors codecave)")
                    return ""
                return f".int {lab}"
            if typ == R_HA and op == 15 and ra == 0:
                return f"lis r{rd}, {lab}@ha"
            if typ == R_LO and op == 14 and ra != 0:
                return f"addi r{rd}, r{ra}, {lab}@l"
            if typ == R_LO and op in _D_NOMS:
                return f"{_D_NOMS[op]} r{rd}, {lab}@l(r{ra})"
            if typ == R_LO and op in _F_NOMS:
                return f"{_F_NOMS[op]} f{rd}, {lab}@l(r{ra})"
            conv.refus.append(f"{qui} : relocation de type {typ} sur une instruction non prévue ({mot:08X})")
            return ""
        if not en_place and _texte_ascii(mot):
            if mot >> 26 in (16, 18):
                conv.notes.append(f"{qui} : {mot:08X} gardé tel quel (texte « {struct.pack('>I', mot).decode()} », "
                                  f"pas un saut)")
            return None
        c = cible_saut(mot, adr)
        if c is not None:
            mn = "bl" if mot & 1 else "b"
            if src.interne(c):
                return f"{mn} {etiquette(c)}" if viser(c, qui) else ""
            if dans_jeu(c):
                return f"{mn} {nom_jeu(c)}"
            if en_place:
                conv.refus.append(f"{qui} : saut vers {c:#010x}, hors du jeu")
                return ""
            conv.notes.append(f"{qui} : {mot:08X} gardé tel quel (cible {c:#010x} hors du jeu : donnée ?)")
            return None
        c = cible_cond(mot, adr)
        if c is not None and not en_place:
            p = plage_de(adr)
            if p[0] <= c < p[1]:
                return None                              # relatif à l'intérieur du bloc : inchangé
            if src.interne(c) or dans_jeu(c):
                conv.refus.append(f"{qui} : branchement conditionnel vers {c:#010x}, hors de son bloc")
                return ""
        return None

    # 3. le code ajouté
    lignes_cave: list[tuple[int, str, str]] = []         # (adresse d'origine, texte, commentaire)
    for debut, fin in plages:
        for adr in range(debut, fin, 4):
            mot = src.mot_libre(adr)
            qui = f"code ajouté {adr:#010x}"
            t = mot_symbolique(mot, adr, qui, en_place=False)
            com = proposer(mot, adr) or ""
            if t is None or t == "":
                t = f".int 0x{mot:08X}"
            else:
                com = f"{mot:08X}"
            lignes_cave.append((adr, t, com))
        conv.taille += fin - debut

    # 4. les sites en place
    for s in choisis:
        nom, octets = src.sites[s]
        for k in range(0, len(octets), 4):
            adr = s + k
            mot, = struct.unpack_from(">I", octets, k)
            qui = f"site {adr:#010x} ({nom})"
            avant = None
            if rpx is not None:
                try:
                    avant, = struct.unpack(">I", rpx.octets(adr, 4))
                except KeyError:
                    conv.refus.append(f"{qui} : hors des sections du RPX")
            if adr in accroches:
                conv.collisions.append(f"{qui} : mot déjà accroché par {accroches[adr]}")
            t = mot_symbolique(mot, adr, qui, en_place=True)
            if t == "":
                continue
            if t is None:
                c = cible_cond(mot, adr)
                if c is not None and dans_jeu(c):
                    t = proposer(mot, adr, nom_jeu)
                else:
                    t = proposer(mot, adr)
                if t is None:
                    conv.refus.append(f"{qui} : instruction {mot:08X} hors des mnémoniques acceptées "
                                      f"(à porter par un saut vers le codecave)")
                    continue
            conv.sites.append((adr, avant, mot, t))

    # 5. mise en forme du code ajouté + contrôle : réassemblé à une autre adresse, tout doit suivre
    for adr, t, com in lignes_cave:
        if adr in cibles:
            nom = src.nom_de(adr)
            conv.cave.append(f"{etiquette(adr)}:" + (f"    ; {nom}" if nom else ""))
        conv.cave.append(f"    {t:<28}" + (f" ; {com}" if com else ""))
    if conv.taille > TAILLE_MAX_ZONE:
        conv.notes.append(f"code ajouté de {conv.taille} octets : plus qu'une zone du pack "
                          f"({TAILLE_MAX_ZONE:#x}), à couper en plusieurs zones")
    if conv.propre:
        _controler(src, conv, plages, prefixe)
    return conv


def _controler(src: Source, conv: Conversion, plages, prefixe: str) -> None:
    """Réassemble la sortie à une AUTRE adresse que celle de TWWHD et compare mot par mot : un mot
    indépendant de l'adresse doit être identique, un mot dépendant doit viser la même chose."""
    base = emu.CODECAVE + 0x1230
    texte = "\n".join([f"0x{a:08X} = {n}:" for a, n in conv.symboles.items()] + [".origin = codecave"]
                      + conv.cave + [f"0x{a:08X} = {t}" for a, _, _, t in conv.sites])
    try:
        a = emu.Assembleur(texte, base=base, permises=emu.PERMISES_MAILBOX).assembler()
    except emu.ErreurAsm as e:
        conv.refus.append(f"la sortie ne s'assemble pas : {e}")
        return
    nouvelle: dict[int, int] = {}                        # adresse TWWHD -> adresse dans le codecave
    pc = base
    for debut, fin in plages:
        for adr in range(debut, fin, 4):
            nouvelle[adr] = pc
            pc += 4

    def meme(mot_o: int, adr_o: int, mot_n: int, adr_n: int, qui: str) -> None:
        r = src.reloc_du_mot(adr_o)
        if r and src.interne(r[1]):
            typ, cible = r
            vise = nouvelle.get(cible)
            if typ == R_ADDR32:
                bon = mot_n == vise
            elif typ == R_HA:
                bon = mot_n >> 16 == mot_o >> 16 and mot_n & 0xFFFF == ((vise + 0x8000) >> 16) & 0xFFFF
            else:
                bon = mot_n >> 16 == mot_o >> 16 and mot_n & 0xFFFF == vise & 0xFFFF
        else:
            co = cible_saut(mot_o, adr_o)
            if co is not None and mot_n != mot_o:
                cn = cible_saut(mot_n, adr_n)
                bon = cn == nouvelle.get(co, co) and (mot_n & 1) == (mot_o & 1)
            elif cible_cond(mot_o, adr_o) is not None and mot_n != mot_o:
                bon = cible_cond(mot_n, adr_n) == cible_cond(mot_o, adr_o) and mot_n >> 16 == mot_o >> 16
            else:
                bon = mot_n == mot_o
        if not bon:
            conv.refus.append(f"{qui} : le mot réassemblé {mot_n:08X} ne correspond pas à {mot_o:08X}")

    for adr_o, adr_n in nouvelle.items():
        meme(src.mot_libre(adr_o), adr_o, a.mots[adr_n], adr_n, f"code ajouté {adr_o:#010x}")
    for adr, _, mot, _ in conv.sites:
        meme(mot, adr, a.mots[adr], adr, f"site {adr:#010x}")


_RE_AVANT = re.compile(r"^\s*(0x[0-9A-Fa-f]+)\s*=\s*[^;]+;.*\btwwhd avant=([0-9A-Fa-f]{8})\b", re.M)


def verifier_avant(texte_asm: str, rpx) -> list[str]:
    """Relit les commentaires ``twwhd avant=XXXXXXXX`` d'un asm et les compare au RPX.

    Rend la liste des écarts (vide si tout va bien). Sert au pack une fois les lignes collées :
    si un jour le RPX visé n'est plus le bon, le test le dit avant le jeu."""
    ecarts = []
    for m in _RE_AVANT.finditer(texte_asm):
        adr, attendu = int(m.group(1), 16), int(m.group(2), 16)
        try:
            lu, = struct.unpack(">I", rpx.octets(adr, 4))
        except KeyError:
            ecarts.append(f"{adr:#010x} : hors des sections du RPX")
            continue
        if lu != attendu:
            ecarts.append(f"{adr:#010x} : le RPX contient {lu:08X}, le pack attend {attendu:08X}")
    return ecarts


def bilan(src: Source, rpx=None, packs: dict[str, str] | None = None) -> dict:
    """Vue d'ensemble de TOUT TWWHD : tailles, refus et collisions si l'on portait tout."""
    conv = convertir(src, sorted(src.sites) + sorted(src.donnees), rpx, packs)
    return {
        "sites": len(src.sites),
        "mots_en_place": sum(len(o) for _, o in src.sites.values()) // 4,
        "donnees": len(src.donnees),
        "code_ajoute": src.libre_fin - LIBRE_DEBUT,
        "code_embarque": conv.taille,
        "relocations": len(src.relocs),
        "doubles": list(src.doubles),
        "refus": list(conv.refus),
        "collisions": list(conv.collisions),
        "notes": list(conv.notes),
    }


# ---------------------------------------------------------------------------------------------

def _rpx(chemin: str | None):
    chemin = chemin or os.environ.get("WWMP_RPX")
    if not chemin or not os.path.isfile(chemin):
        return None
    from jeu.rpx import Rpx
    return Rpx(chemin)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Convertit des correctifs de TWWHD-Randomizer (MIT, SuperDude88) "
                                             "en lignes de pack Cemu. N'écrit rien dans le jeu ni dans le pack.")
    ap.add_argument("source", help="dossier de TWWHD-Randomizer (ou son sous-dossier asm)")
    ap.add_argument("--rpx", help="cking.rpx d'origine, lu seulement (défaut : variable WWMP_RPX)")
    ap.add_argument("--pack", action="append", default=[], help="asm d'un de nos packs (collisions) ; répétable")
    ap.add_argument("--site", action="append", default=[], help="adresse d'un site de TWWHD à convertir ; répétable")
    ap.add_argument("--bilan", action="store_true", help="résumé de tout TWWHD : tailles, refus, collisions")
    ap.add_argument("--forcer", action="store_true", help="écrit la sortie même refusée (marquée non utilisable)")
    args = ap.parse_args(argv)
    try:
        src = Source.depuis_dossier(args.source)
        packs = {}
        chemins = args.pack or sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(_ICI)),
                                                             "graphicpack", "*", "patch*.asm")))
        for p in chemins:
            with open(p, encoding="utf-8") as f:
                packs[os.path.basename(os.path.dirname(p))] = f.read()
        rpx = _rpx(args.rpx)
        if args.bilan:
            b = bilan(src, rpx, packs)
            for cle in ("sites", "mots_en_place", "donnees", "code_ajoute", "code_embarque", "relocations"):
                print(f"{cle:16} {b[cle]}")
            for cle in ("doubles", "collisions", "refus", "notes"):
                print(f"{cle} : {len(b[cle])}")
                for x in b[cle]:
                    print("   ", x)
            return 0
        if not args.site:
            ap.error("donner --bilan ou au moins un --site")
        conv = convertir(src, [int(s, 0) for s in args.site], rpx, packs)
        sys.stdout.write(conv.texte(forcer=args.forcer))
        return 0 if conv.propre else 1
    except ErreurPortage as e:
        print(f"erreur : {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
