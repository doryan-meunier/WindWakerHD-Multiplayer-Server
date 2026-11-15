"""Analyse statique des registres du code PowerPC du ``cking.rpx`` (lecture seule ; contrôles hors ligne du mod natif).

Pourquoi : le compilateur du jeu (GHS) alloue les registres d'une fonction à l'autre. Un appelant peut garder une
valeur dans un registre « volatil » (r0, r3-r12, cr0/1/5/6/7…) à travers un ``bl`` vers une fonction dont il sait
qu'elle n'y touche pas (``mr r11, r3`` ; ``bl 0x026FCEC8`` ; ``lwz r12, 0x54(r11)``), ou lire un registre qu'elle a
écrit en plus de r3. Un ``WWHD_REPLACE`` du mod, compilé en C selon l'ABI, peut détruire tous les volatils : avant de
remplacer une fonction, on vérifie qu'AUCUN appelant direct ne lit un volatil après l'appel (seul r3, le résultat, est
permis) et, dans une fonction réécrite en C, d'où vient chaque registre d'argument passé à ses appelées et quelles
valeurs elle garde à travers un appel (il faut alors prouver que l'appelée n'y touche pas).

Ensembles de registres : entiers de bits (r0..r31 = bits 0..31, f0..f31 = 32..63, champs cr0..cr7 = 64..71, LR 72,
CTR 73, XER 74).

* ``origines(f)`` : analyse LOCALE des définitions qui atteignent chaque instruction de ``f`` (sans suivre les
  appelées). Origine d'un registre : ``ENTREE`` (valeur de l'appelant), l'adresse d'une instruction de ``f``, ou un
  appel : ``("appel", site)`` (l'appelée a pu l'écrire) et ``("garde", origine, site)`` (valeur d'avant l'appel, que
  l'appelée a pu garder). Un appel n'efface donc rien : prudent dans les deux sens.
* ``lectures_a_travers(f)`` : lectures (par une instruction, ou comme argument r3-r10/f1-f8 d'un appel) d'un volatil
  dont une origine passe par un appel : ce que le C doit refaire (et prouver par ``ecrits_possibles``).
* ``ecrits_possibles(g)`` : registres que ``g`` ou ses appelées peuvent écrire (appel indirect ou système : tous les
  volatils) ; une valeur gardée à travers ``g`` n'est sûre que hors de cet ensemble.
* ``appelants_dependants(t)`` : pour chaque appel direct de ``t``, les lectures dans l'appelant d'un volatil (hors
  r3) qui passe par cet appel : vide = un remplacement de ``t`` selon l'ABI ne change rien pour ses appelants.

* ``ecrits_possibles_exacts(t)``, ``inconnu(t)`` : registres que ``t`` ou une fonction qu'elle atteint par appels
  directs peut écrire (parcours complet) ; du code inconnu atteint (appel indirect, hors du texte) : tous les volatils.
  C'est le critère de l'audit des remplacements du mod (tests/test_natif_gardiens.py) : un volatil hors de cet
  ensemble peut être gardé par un appelant à travers l'appel ; le remplacement doit alors être GARDÉ
  (natif/mod_wwmp/gardien.h).
* ``vivants_apres(s)``, ``audit_remplacement(t, resultats)`` : vivacité entre fonctions (registres lus plus loin, en
  traversant chaque appel direct comme le compilateur : entrées réelles de l'appelée, registres qu'elle écrit à coup
  sûr ou qu'elle ne peut pas garder ; au retour, ce que lisent ses appelants). Estimation prudente (chemins non
  distingués) : sert à TROUVER les appelants qui gardent un volatil (0x02007BC8 garde f2, 0x0217F258 garde r5 et f9).

Débuts de fonctions : cibles de ``bl``, adresses du texte prises par une relocation (données, ou ``lis``/``addi`` dans
le code, sauf la base d'une table de sauts), et ``debuts_connus`` (``functions.h`` du SDK du portage : une fonction
atteinte seulement par un saut terminal n'est vue que par lui). Un ``bctr`` de table de sauts est suivi (table de
``b`` ou d'adresses, base dans le registre passé à ``mtctr``, borne ``cmplwi`` + ``bge``) ; un ``mtctr`` d'un pointeur
chargé est un appel terminal indirect. Une instruction inconnue arrête l'analyse avec une erreur : rien n'est supposé.
"""
from __future__ import annotations

import bisect
import struct

GPR = [1 << k for k in range(32)]
FPR = [1 << (32 + k) for k in range(32)]
CR = [1 << (64 + k) for k in range(8)]
LR, CTR, XER = 1 << 72, 1 << 73, 1 << 74
TOUT = (1 << 75) - 1


def masque(*regs):
    m = 0
    for r in regs:
        m |= r
    return m


VOLATILS = masque(GPR[0], *GPR[3:13], *FPR[0:14], CR[0], CR[1], CR[5], CR[6], CR[7], CTR, XER, LR)
GARDES_ABI = masque(GPR[1], GPR[2], GPR[13], *GPR[14:32], *FPR[14:32], CR[2], CR[3], CR[4])
RESULTATS = masque(GPR[3], GPR[4], FPR[1], FPR[2])
ARGUMENTS = masque(*GPR[3:11], *FPR[1:9], GPR[1], GPR[2], GPR[13])


def noms(m: int) -> list[str]:
    out = []
    for k in range(32):
        if m & GPR[k]:
            out.append(f"r{k}")
    for k in range(32):
        if m & FPR[k]:
            out.append(f"f{k}")
    for k in range(8):
        if m & CR[k]:
            out.append(f"cr{k}")
    for b, n in ((LR, "lr"), (CTR, "ctr"), (XER, "xer")):
        if m & b:
            out.append(n)
    return out


class InstructionNonAnalysee(RuntimeError):
    pass


def _s16(v):
    return v - 0x10000 if v & 0x8000 else v


def _cible_b(a, w):
    li = w & 0x03FFFFFC
    if li & 0x02000000:
        li -= 0x04000000
    return (li if w & 2 else a + li) & 0xFFFFFFFF


def _cible_bc(a, w):
    bd = w & 0xFFFC
    if bd & 0x8000:
        bd -= 0x10000
    return (a + bd) & 0xFFFFFFFF


def _cr_bit(bi):
    return CR[bi >> 2]


def decoder(a: int, w: int):
    """(lus, écrits, genre, cible) d'une instruction. Genres : 'seq', 'b', 'bc', 'blr', 'bclr', 'bl', 'bctrl',
    'bctr', 'bcctr'. Les écritures partielles (un bit d'un champ CR, rlwimi, l'octet de XER) comptent aussi comme
    lectures."""
    op = w >> 26
    rd, ra, rb = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
    rA0 = GPR[ra] if ra else 0
    if op == 18:
        return (0, LR if w & 1 else 0, "bl" if w & 1 else "b", _cible_b(a, w))
    if op == 16:
        bo, bi = rd, ra
        u = 0 if bo & 0x10 else _cr_bit(bi)
        d = 0
        if not bo & 4:
            u |= CTR
            d |= CTR
        if w & 1:
            if bo & 0x14 == 0x14:
                return (u, d | LR, "bl", _cible_bc(a, w))
            raise InstructionNonAnalysee(f"bcl conditionnel 0x{a:08X}")
        if bo & 0x14 == 0x14:
            return (u, d, "b", _cible_bc(a, w))
        return (u, d, "bc", _cible_bc(a, w))
    if op == 19:
        xo = (w >> 1) & 0x3FF
        bo, bi = rd, ra
        if xo in (16, 528):
            u = (LR if xo == 16 else CTR) | (0 if bo & 0x10 else _cr_bit(bi))
            d = 0
            if xo == 16 and not bo & 4:
                u |= CTR
                d |= CTR
            cond = bo & 0x14 != 0x14
            if w & 1:
                if cond:
                    raise InstructionNonAnalysee(f"appel conditionnel 0x{a:08X}")
                return (u, LR, "bctrl" if xo == 528 else "blrl", None)
            if xo == 16:
                return (u, d, "bclr" if cond else "blr", None)
            return (u, d, "bcctr" if cond else "bctr", None)
        if xo == 150:
            return (0, 0, "seq", None)
        if xo == 0:
            return (CR[ra >> 2], CR[rd >> 2], "seq", None)
        if xo in (193, 289) and rd == ra == rb:                      # crclr / crset (bit du nombre d'arguments
            return (0, _cr_bit(rd), "seq", None)                      # flottants d'un appel variadique) : écriture
        if xo in (33, 129, 193, 225, 257, 289, 417, 449):
            return (_cr_bit(ra) | _cr_bit(rb) | _cr_bit(rd), _cr_bit(rd), "seq", None)
        raise InstructionNonAnalysee(f"op19 xo={xo} 0x{a:08X}")
    if op in (14, 15):
        return (rA0, GPR[rd], "seq", None)
    if op in (12, 13):
        return (GPR[ra], GPR[rd] | XER | (CR[0] if op == 13 else 0), "seq", None)
    if op in (7, 8):
        return (GPR[ra], GPR[rd] | (XER if op == 8 else 0), "seq", None)
    if op in (24, 25, 26, 27):
        return (GPR[rd], GPR[ra], "seq", None)
    if op in (28, 29):
        return (GPR[rd], GPR[ra] | CR[0], "seq", None)
    if op in (10, 11):
        return (GPR[ra], CR[rd >> 2], "seq", None)
    if op in (20, 21, 23):
        u = GPR[rd] | (GPR[ra] if op == 20 else 0) | (GPR[rb] if op == 23 else 0)
        return (u, GPR[ra] | (CR[0] if w & 1 else 0), "seq", None)
    if op in (32, 34, 40, 42):                                        # lwz lbz lhz lha
        return (rA0, GPR[rd], "seq", None)
    if op in (33, 35, 41, 43):                                        # formes avec mise à jour
        return (GPR[ra], GPR[rd] | GPR[ra], "seq", None)
    if op in (36, 38, 44):
        return (GPR[rd] | rA0, 0, "seq", None)
    if op in (37, 39, 45):
        return (GPR[rd] | GPR[ra], GPR[ra], "seq", None)
    if op == 46:                                                      # lmw
        return (rA0, masque(*GPR[rd:32]), "seq", None)
    if op == 47:
        return (masque(*GPR[rd:32]) | rA0, 0, "seq", None)
    if op in (48, 50):
        return (rA0, FPR[rd], "seq", None)
    if op in (49, 51):
        return (GPR[ra], FPR[rd] | GPR[ra], "seq", None)
    if op in (52, 54):
        return (FPR[rd] | rA0, 0, "seq", None)
    if op in (53, 55):
        return (FPR[rd] | GPR[ra], GPR[ra], "seq", None)
    if op == 56:                                                      # psq_l
        return (rA0, FPR[rd], "seq", None)
    if op == 60:
        return (FPR[rd] | rA0, 0, "seq", None)
    if op == 57 or op == 61:                                          # psq_lu / psq_stu
        return ((FPR[rd] if op == 61 else 0) | GPR[ra], (FPR[rd] if op == 57 else 0) | GPR[ra], "seq", None)
    if op == 3:
        return (GPR[ra], 0, "seq", None)
    if op == 31:
        xo = (w >> 1) & 0x3FF
        xo9 = xo & 0x1FF
        rc = CR[0] if w & 1 else 0
        oe = XER if w & 0x400 else 0
        if xo9 in (266, 40, 235, 491, 459, 75, 11):
            return (GPR[ra] | GPR[rb], GPR[rd] | rc | oe, "seq", None)
        if xo9 in (8, 10):
            return (GPR[ra] | GPR[rb], GPR[rd] | rc | XER, "seq", None)
        if xo9 in (136, 138):
            return (GPR[ra] | GPR[rb] | XER, GPR[rd] | rc | XER, "seq", None)
        if xo9 == 104:
            return (GPR[ra], GPR[rd] | rc | oe, "seq", None)
        if xo9 in (200, 202, 232, 234):
            return (GPR[ra] | XER, GPR[rd] | rc | XER, "seq", None)
        if xo in (28, 444, 316, 60, 412, 124, 284, 476, 24, 536):
            return (GPR[rd] | GPR[rb], GPR[ra] | rc, "seq", None)
        if xo == 792:
            return (GPR[rd] | GPR[rb], GPR[ra] | rc | XER, "seq", None)
        if xo == 824:
            return (GPR[rd], GPR[ra] | rc | XER, "seq", None)
        if xo in (26, 954, 922):
            return (GPR[rd], GPR[ra] | rc, "seq", None)
        if xo in (0, 32):
            return (GPR[ra] | GPR[rb], CR[rd >> 2], "seq", None)
        if xo == 339:
            spr = (rb << 5) | ra
            s = {8: LR, 9: CTR, 1: XER}.get(spr)
            if s is None:
                raise InstructionNonAnalysee(f"mfspr {spr} 0x{a:08X}")
            return (s, GPR[rd], "seq", None)
        if xo == 467:
            spr = (rb << 5) | ra
            s = {8: LR, 9: CTR, 1: XER}.get(spr)
            if s is None:
                raise InstructionNonAnalysee(f"mtspr {spr} 0x{a:08X}")
            return (GPR[rd], s, "seq", None)
        if xo == 19:
            return (masque(*CR), GPR[rd], "seq", None)
        if xo == 144:
            fxm = (w >> 12) & 0xFF
            champs = masque(*[CR[k] for k in range(8) if fxm & (0x80 >> k)])
            return (GPR[rd], champs, "seq", None)
        if xo in (23, 87, 279, 343, 20):
            return (rA0 | GPR[rb], GPR[rd], "seq", None)
        if xo in (55, 119, 311, 375):                                # lwzux lbzux lhzux lhaux
            return (GPR[ra] | GPR[rb], GPR[rd] | GPR[ra], "seq", None)
        if xo in (247, 439):                                          # stbux sthux
            return (GPR[rd] | GPR[ra] | GPR[rb], GPR[ra], "seq", None)
        if xo in (567, 631):                                          # lfsux lfdux
            return (GPR[ra] | GPR[rb], FPR[rd] | GPR[ra], "seq", None)
        if xo in (695, 759):                                          # stfsux stfdux
            return (FPR[rd] | GPR[ra] | GPR[rb], GPR[ra], "seq", None)
        if xo in (534, 790):                                          # lwbrx lhbrx
            return (rA0 | GPR[rb], GPR[rd], "seq", None)
        if xo in (662, 918):                                          # stwbrx sthbrx
            return (GPR[rd] | rA0 | GPR[rb], 0, "seq", None)
        if xo in (371, 83, 595):                                      # mftb mfmsr mfsr
            return (0, GPR[rd], "seq", None)
        if xo in (146, 210):                                          # mtmsr mtsr
            return (GPR[rd], 0, "seq", None)
        if xo == 246:                                                 # dcbtst
            return (rA0 | GPR[rb], 0, "seq", None)
        if xo in (151, 215, 407, 150):
            return (GPR[rd] | rA0 | GPR[rb], CR[0] if xo == 150 else 0, "seq", None)
        if xo == 183:
            return (GPR[rd] | GPR[ra] | GPR[rb], GPR[ra], "seq", None)
        if xo in (535, 599):
            return (rA0 | GPR[rb], FPR[rd], "seq", None)
        if xo in (663, 727, 983):
            return (FPR[rd] | rA0 | GPR[rb], 0, "seq", None)
        if xo in (598, 86, 54, 278, 1014, 982, 470, 854):
            return (rA0 | GPR[rb] if xo not in (598, 854) else 0, 0, "seq", None)
        if xo == 4:
            return (GPR[ra] | GPR[rb], 0, "seq", None)
        if xo in (597, 725):                                          # lswi / stswi
            n = rb or 32
            regs = masque(*[GPR[(rd + k) % 32] for k in range((n + 3) // 4)])
            return ((rA0, regs) if xo == 597 else (regs | rA0, 0)) + ("seq", None)
        raise InstructionNonAnalysee(f"op31 xo={xo} 0x{a:08X}")
    if op in (59, 63):
        xo5 = (w >> 1) & 31
        xo = (w >> 1) & 0x3FF
        rc = CR[1] if w & 1 else 0
        frc = (w >> 6) & 31
        if xo5 in (21, 20, 18):
            return (FPR[ra] | FPR[rb], FPR[rd] | rc, "seq", None)
        if xo5 == 25:
            return (FPR[ra] | FPR[frc], FPR[rd] | rc, "seq", None)
        if xo5 in (29, 28, 31, 30, 23):
            return (FPR[ra] | FPR[frc] | FPR[rb], FPR[rd] | rc, "seq", None)
        if xo5 in (24, 26, 22):
            return (FPR[rb], FPR[rd] | rc, "seq", None)
        if op == 63:
            if xo in (0, 32):
                return (FPR[ra] | FPR[rb], CR[rd >> 2], "seq", None)
            if xo in (72, 40, 264, 136, 12, 14, 15):
                return (FPR[rb], FPR[rd] | rc, "seq", None)
            if xo == 583:
                return (0, FPR[rd] | rc, "seq", None)
            if xo in (711, 70, 38, 134):
                return (FPR[rb] if xo == 711 else 0, rc, "seq", None)
        raise InstructionNonAnalysee(f"flottant op{op} xo={xo} 0x{a:08X}")
    if op == 4:
        xo = (w >> 1) & 0x3FF
        xo5 = (w >> 1) & 31
        frc = (w >> 6) & 31
        rc = CR[1] if w & 1 else 0
        if xo in (528, 560, 592, 624):                                # ps_merge
            return (FPR[ra] | FPR[rb] | FPR[rd], FPR[rd] | rc, "seq", None)
        if xo in (40, 72, 136, 264):                                  # ps_neg, ps_mr, ps_nabs, ps_abs
            return (FPR[rb], FPR[rd] | rc, "seq", None)
        if xo in (0, 32, 64, 96):                                     # ps_cmp*
            return (FPR[ra] | FPR[rb], CR[rd >> 2], "seq", None)
        if xo5 in (18, 20, 21):
            return (FPR[ra] | FPR[rb], FPR[rd] | rc, "seq", None)
        if xo5 in (25, 12, 13):
            return (FPR[ra] | FPR[frc], FPR[rd] | rc, "seq", None)
        if xo5 in (10, 11, 14, 15, 23, 28, 29, 30, 31):
            return (FPR[ra] | FPR[frc] | FPR[rb], FPR[rd] | rc, "seq", None)
        if xo5 in (24, 26):
            return (FPR[rb], FPR[rd] | rc, "seq", None)
        if xo in (6, 7, 38, 39):                                      # psq_lx, psq_stx, …
            if xo in (6, 38):
                return (rA0 | GPR[rb], FPR[rd] | (GPR[ra] if xo == 38 else 0), "seq", None)
            return (FPR[rd] | rA0 | GPR[rb], GPR[ra] if xo == 39 else 0, "seq", None)
        raise InstructionNonAnalysee(f"paired single xo={xo} 0x{a:08X}")
    raise InstructionNonAnalysee(f"op{op} 0x{a:08X} ({w:08X})")


def lire_functions_h(chemin: str) -> list[int]:
    """Adresses de ``#define WWHD_ADDR_<nom> 0x...`` (``sdk/guest/include/wwhd/functions.h`` du portage)."""
    import re
    with open(chemin, encoding="utf-8", errors="replace") as f:
        return [int(m.group(1), 16) for m in re.finditer(r"#define WWHD_ADDR_\w+ (0x[0-9A-Fa-f]+)", f.read())]


class Analyse:

    def __init__(self, rpx, debuts_connus=()):
        """``debuts_connus`` : entrées de fonctions connues par ailleurs (``wwhd/functions.h`` du SDK du portage :
        ``lire_functions_h``), en plus des cibles de ``bl`` et des adresses prises par une relocation (une fonction
        atteinte seulement par un saut terminal ``b`` n'est vue que par elles)."""
        s = next(s for s in rpx.secs if s["addr"] == 0x02000020)
        self.lo, self.data = s["addr"], s["data"]
        self.hi = self.lo + len(self.data)
        self.rpx = rpx
        n = len(self.data) // 4
        self.mots = struct.unpack(f">{n}I", self.data[:4 * n])
        self.sites = {}                  # cible -> [sites bl]
        debuts = set()
        for i, w in enumerate(self.mots):
            if w >> 26 == 18 and w & 1:
                a = self.lo + 4 * i
                t = _cible_b(a, w)
                self.sites.setdefault(t, []).append(a)
                debuts.add(t)
        self.pointees = set()            # adresses du texte prises par une relocation (pointeurs, tables)
        for sec in rpx.secs:
            if sec["typ"] != 4:
                continue
            sym = rpx.secs[sec["link"]]["data"]
            d = sec["data"]
            for k in range(0, len(d), 12):
                off, inf, add = struct.unpack_from(">IIi", d, k)
                genre = inf & 0xFF
                if genre not in (1, 4, 5, 6):  # R_PPC_ADDR32, et ADDR16_LO/HI/HA (adresse formée par lis/addi)
                    continue
                val, = struct.unpack_from(">I", sym, (inf >> 8) * 16 + 4)
                t = (val + add) & 0xFFFFFFFF
                if not (self.lo <= t < self.hi):
                    continue
                if genre == 1 and not (self.lo <= off < self.hi):
                    self.pointees.add(t)
                elif genre != 1 and self.lo <= off < self.hi:
                    # adresse du texte formée dans le code : fonction passée par pointeur, sauf la base d'une
                    # table de sauts (une table de « b » qui suit son « bctr »)
                    if not (self.mot(t) >> 26 == 18 and self.mot(t - 4) == 0x4E800420):
                        self.pointees.add(t)
        debuts |= self.pointees
        debuts |= set(debuts_connus)
        self.debuts = sorted(x for x in debuts if self.lo <= x < self.hi)
        self._possibles, self._origines = {}, {}

    def mot(self, a):
        return self.mots[(a - self.lo) >> 2]

    def fin(self, f):
        k = bisect.bisect_right(self.debuts, f)
        return self.debuts[k] if k < len(self.debuts) else self.hi

    def appelants_directs(self, f):
        return list(self.sites.get(f, ()))

    def fonction_de(self, a):
        k = bisect.bisect_right(self.debuts, a) - 1
        return self.debuts[k]

    # --- graphe d'une fonction ---------------------------------------------------------------------------
    def corps(self, f):
        """Instructions atteintes depuis l'entrée ``f`` : {adresse: (lus, écrits, genre, cible, successeurs)}."""
        fin = self.fin(f)
        out, pile = {}, [f]
        while pile:
            a = pile.pop()
            if a in out:
                continue
            if not (f <= a < fin):
                raise InstructionNonAnalysee(f"0x{a:08X} hors de 0x{f:08X}")
            u, d, g, t = decoder(a, self.mot(a))
            succ = []
            if g in ("seq", "bl", "bctrl", "blrl") and a + 4 == fin:
                if g == "seq":
                    g, t = "terminal", fin                # suite dans la fonction suivante (sauvegarde des registres)
                # sinon : appel sans retour en fin de fonction (abandon, assertion) : pas de successeur
            elif g in ("seq", "bl", "bctrl", "blrl"):
                succ = [a + 4]
            elif g == "b":
                if f <= t < fin:
                    succ = [t]
                else:
                    g = "terminal"
            elif g == "bc":
                if not (f <= t < fin):
                    raise InstructionNonAnalysee(f"branchement conditionnel hors fonction 0x{a:08X}")
                succ = [t, a + 4]
            elif g == "bclr":
                succ = [a + 4]
            elif g == "bctr":
                succ = self.table_de_sauts(f, fin, a)
                if succ is None:
                    g, succ = "terminal_indirect", []
            elif g == "bcctr":
                raise InstructionNonAnalysee(f"saut indirect conditionnel 0x{a:08X} dans 0x{f:08X}")
            out[a] = (u, d, g, t, succ)
            pile.extend(succ)
        return out

    def table_de_sauts(self, f, fin, a):
        """Cibles d'un ``bctr`` de table de sauts : ``cmplwi rI, N`` (borne), base formée par ``lis``/``addis`` puis
        ``addi`` (ou ``addic``) dans le registre passé à ``mtctr`` (``addis rB, rI, hi`` : l'index déjà multiplié par
        4 s'ajoute à la base), table de ``b`` dans le code de la fonction (base + 4 k), ou table d'adresses en
        données (``lwzx``)."""
        borne, hauts, bas, source, sauts = None, {}, {}, None, []
        for k in range(1, 16):
            w = self.mot(a - 4 * k)
            op, rd, ra = w >> 26, (w >> 21) & 31, (w >> 16) & 31
            if op == 16 and borne is None:
                sauts.append(w)                               # branchements entre la borne et le bctr
            if source is None and op == 31 and (w >> 1) & 0x3FF == 467 and ((w >> 11) & 31) << 5 | ra == 9:
                source = rd                                   # mtctr rS
            elif op == 10 and borne is None:
                # cmplwi crF, rI, N : « bge » (BO=4, bit lt du champ) vers le défaut -> N entrées ; sinon (bgt…)
                # N + 1, prudent
                champ = (w >> 23) & 7
                bge = any(x >> 21 & 31 == 4 and (x >> 16) & 31 == 4 * champ for x in sauts)
                borne = (w & 0xFFFF) + (0 if bge else 1)
            elif op == 15:
                hauts.setdefault(rd, (w & 0xFFFF) << 16)
            elif (op == 14 and ra == rd and ra) or (op == 12 and ra == rd):
                bas.setdefault(rd, _s16(w & 0xFFFF))
            elif op == 31 and (w >> 1) & 0x3FF == 23 and source is not None and rd == source:
                source = ("lwzx", ra, (w >> 11) & 31)         # lwzx rS, rA, rB : table d'adresses
            elif op == 32 and isinstance(source, int) and rd == source and source not in bas:
                return None                                   # mtctr d'un pointeur chargé : appel indirect
        if borne is None:
            return None                                   # pas une table : appel terminal indirect
        if isinstance(source, tuple):
            cand = [r for r in source[1:] if r in hauts and r in bas]
        elif source is not None and source in hauts and source in bas:
            cand = [source]
        else:
            cand = [r for r in hauts if r in bas]
        bases = {(hauts[r] + bas[r]) & 0xFFFFFFFF for r in cand}
        if len(bases) != 1:
            raise InstructionNonAnalysee(f"table de sauts non reconnue 0x{a:08X} dans 0x{f:08X}")
        base = bases.pop()
        if f <= base < fin:
            return [base + 4 * k for k in range(borne)]
        cibles = [struct.unpack(">I", self.rpx.octets(base + 4 * k, 4))[0] for k in range(borne)]
        if not all(f <= c < fin for c in cibles):
            raise InstructionNonAnalysee(f"table de sauts hors fonction 0x{a:08X}")
        return cibles

    # --- registres écrits, transitivement ----------------------------------------------------------------
    def ecrits_possibles(self, g):
        """Registres que ``g`` (appelées comprises) peut écrire. Appel indirect, appel système ou cycle : tous les
        volatils (prudent)."""
        if not (self.lo <= g < self.hi):
            return VOLATILS
        if g in self._possibles:
            v = self._possibles[g]
            return VOLATILS if v is None else v
        self._possibles[g] = None
        m = 0
        for a, (u, d, genre, t, succ) in self.corps(g).items():
            m |= d
            if genre in ("bl", "terminal"):
                m |= self.ecrits_possibles(t)
            elif genre in ("bctrl", "blrl", "terminal_indirect"):
                m |= VOLATILS
        self._possibles[g] = m
        return m

    # --- définitions qui atteignent chaque instruction (analyse locale) ----------------------------------
    def origines(self, f, marques=None):
        """(corps, état) : état[a][k] = origines du registre k avant l'instruction a. Un appel marqué (tous si
        ``marques`` vaut None) emballe chaque volatil : ``("garde", origine première, sites traversés)`` ; un appel non
        marqué ajoute seulement ``("appel", site)``."""
        cle = (f, None if marques is None else frozenset(marques))
        if cle in self._origines:
            return self._origines[cle]
        corps = self.corps(f)
        etat = {a: None for a in corps}
        etat[f] = (frozenset([ENTREE]),) * 75
        ordre = sorted(corps)
        change = True
        while change:
            change = False
            for a in ordre:
                e = etat[a]
                if e is None:
                    continue
                u, d, g, t, succ = corps[a]
                s = list(e)
                appel = g in ("bl", "bctrl", "blrl")
                marque = appel and (marques is None or a in marques)
                for k in range(75):
                    if d >> k & 1:
                        s[k] = frozenset([a])
                    elif marque and VOLATILS >> k & 1:
                        s[k] = frozenset([("appel", a)] + [_emballer(o, a) for o in e[k]])
                    elif appel and VOLATILS >> k & 1:
                        s[k] = e[k] | {("appel", a)}
                s = tuple(s)
                for b in succ:
                    if etat[b] is None:
                        etat[b] = s
                        change = True
                    else:
                        n = tuple(x | y for x, y in zip(etat[b], s))
                        if n != etat[b]:
                            etat[b] = n
                            change = True
        self._origines[cle] = (corps, etat)
        return corps, etat

    def arguments(self, f):
        """{site d'appel de ``f``: (genre, cible, {registre: origines})} pour r3-r10 et f1-f8."""
        corps, etat = self.origines(f)
        out = {}
        for a in sorted(corps):
            u, d, g, t, succ = corps[a]
            if g in ("bl", "bctrl", "blrl", "terminal", "terminal_indirect"):
                out[a] = (g, t, {nom_bit(k): etat[a][k] for k in ARG_BITS})
        return out

    def lectures_a_travers(self, f, sites=None):
        """[(adresse, registre, origine)] : lectures d'un volatil (r0, r3-r12, f0-f13, cr0/1/5-7, ctr, xer) dont une
        origine passe par un appel (``sites`` : seulement ces appels ; sinon tous). Par une instruction : toute origine
        (sauf le résultat r3/r4/f1/f2 de l'appelée elle-même). Comme argument r3-r10/f1-f8 d'un appel suivant :
        seulement une valeur que ``f`` a calculée (ou un résultat r3/r4/f1/f2 d'une appelée) gardée à travers l'appel
        (ce qui reste d'avant, ou ce qu'une appelée a laissé ailleurs, n'est pas un argument que le compilateur passe)."""
        corps, etat = self.origines(f, sites)
        out = []
        for a in sorted(corps):
            u, d, g, t, succ = corps[a]
            appel = g in ("bl", "bctrl", "blrl", "terminal", "terminal_indirect")
            for k in range(75):
                if not (VOLATILS >> k & 1) or k == 72:
                    continue
                instr = bool(u >> k & 1)
                arg = appel and k in ARG_BITS
                if not (instr or arg):
                    continue
                for o in etat[a][k]:
                    if not isinstance(o, tuple):
                        continue
                    if o[0] == "appel" and k in RESULTAT_BITS:
                        continue
                    if sites is not None and not any(_passe_par(o, x) for x in sites):
                        continue
                    if not instr:
                        p = premiere_origine(o)
                        if o[0] == "appel" or not (isinstance(p, int) or (isinstance(p, tuple) and k in RESULTAT_BITS)):
                            continue
                    out.append((a, nom_bit(k), o))
        return out

    def appelants_dependants(self, t, permis=frozenset(["r3"])):
        """{site: [(adresse, registre, origine)]} : pour chaque appel direct de ``t``, les lectures d'un volatil de
        l'appelant (hors ``permis`` : r3, le résultat, que le remplacement écrit) qui passent par CET appel (gardé à
        travers lui, ou écrit par lui). Vide partout : un remplacement de ``t`` selon l'ABI ne change rien pour eux."""
        out = {}
        for s in self.appelants_directs(t):
            f = self.fonction_de(s)
            out[s] = [(a, r, o) for a, r, o in self.lectures_a_travers(f, {s}) if r not in permis]
        return out


    # --- vivacité (registres lus plus loin), entre fonctions ----------------------------------------------
    # Ce que le compilateur du jeu SAIT d'une appelée directe, il peut s'en servir : garder un volatil à travers
    # elle, ou lire un registre qu'elle écrit. On calcule donc, d'instruction en instruction, les registres dont la
    # valeur sera LUE plus loin (vivants), en traversant chaque appel direct comme le ferait le compilateur :
    #   avant ``bl g`` : entrees(g) | (vivants après - tues(g)) ;
    #   tues(g) = registres que g écrit sur TOUS ses chemins, plus tous les volatils si g peut mener à du code
    #   inconnu (appel indirect, hors du texte) : aucun compilateur ne garde un volatil à travers du code inconnu.
    # Un appel indirect suit l'ABI : il lit les arguments (r3-r10, f1-f8, ctr) et tue tous les volatils.
    # Au retour d'une fonction, les vivants sont ceux de ses appelants directs juste après leur appel (et, si son
    # adresse est prise ou qu'elle n'a pas d'appelant direct, ceux de l'ABI : les résultats). Récursion : valeur
    # prudente (tout est vivant, rien n'est tué).

    def _cache(self, nom):
        c = self.__dict__.get("_c_" + nom)
        if c is None:
            c = self.__dict__["_c_" + nom] = {}
        return c

    def _appelees(self, g):
        """(appelées directes dans le texte, g fait-il lui-même un appel inconnu : indirect ou hors du texte)."""
        c = self._cache("appelees")
        if g not in c:
            ap, inc = set(), False
            for a, (u, d, genre, t, succ) in self.corps(g).items():
                if genre in ("bctrl", "blrl", "terminal_indirect"):
                    inc = True
                elif genre in ("bl", "terminal"):
                    if self.lo <= t < self.hi:
                        ap.add(t)
                    else:
                        inc = True
            c[g] = (ap, inc)
        return c[g]

    def atteintes(self, g):
        """Toutes les fonctions atteintes depuis ``g`` par des appels directs (``g`` compris)."""
        vus, pile = set(), [g]
        while pile:
            h = pile.pop()
            if h in vus:
                continue
            vus.add(h)
            pile.extend(self._appelees(h)[0])
        return vus

    def inconnu(self, g):
        """``g`` (ou une fonction qu'elle atteint par appels directs) peut-elle exécuter du code inconnu (appel
        indirect, hors du texte) ? Exact (parcours complet, récursion comprise)."""
        if not (self.lo <= g < self.hi):
            return True
        c = self._cache("inconnu")
        if g in c:
            return c[g]
        # parcours complet : trouvé -> vrai pour g ; fini sans rien trouver -> faux pour tout ce qui a été visité
        # (ce que chacun atteint a été visité aussi)
        vus, pile, trouve = set(), [g], False
        while pile and not trouve:
            h = pile.pop()
            if h in vus:
                continue
            vus.add(h)
            if c.get(h) is True or self._appelees(h)[1]:
                trouve = True
                break
            for k in self._appelees(h)[0]:
                if k not in vus and c.get(k) is not False:
                    pile.append(k)
        if trouve:
            c[g] = True
        else:
            for h in vus:
                c[h] = False
        return c[g]

    def ecrits_possibles_exacts(self, g):
        """Registres que ``g`` ou une fonction qu'elle atteint par appels directs peut écrire (récursion comprise) ;
        tous les volatils en plus si du code inconnu est atteint."""
        c = self._cache("possibles_exacts")
        if g not in c:
            if not (self.lo <= g < self.hi):
                c[g] = VOLATILS
            else:
                m = 0
                for h in self.atteintes(g):
                    for a, (u, d, genre, t, succ) in self.corps(h).items():
                        m |= d
                c[g] = m | (VOLATILS if self.inconnu(g) else 0)
        return c[g]

    def ecrits_surs(self, g):
        """Registres que ``g`` écrit sur TOUS ses chemins jusqu'à un retour (appelées directes comprises)."""
        if not (self.lo <= g < self.hi):
            return 0
        c = self._cache("surs")
        if g in c:
            return c[g] or 0                             # None : récursion en cours, rien n'est sûr (prudent)
        c[g] = None
        corps = self.corps(g)
        etat, sorties, travail = {g: 0}, [], [g]
        while travail:
            a = travail.pop()
            u, d, genre, t, succ = corps[a]
            m = etat[a] | d
            if genre == "bl":
                m |= self.ecrits_surs(t)
            if genre in ("blr", "bclr", "terminal_indirect"):
                sorties.append(m)
            elif genre == "terminal":
                sorties.append(m | self.ecrits_surs(t))
            for b in succ:
                if b not in etat:
                    etat[b] = m
                    travail.append(b)
                elif etat[b] & m != etat[b]:
                    etat[b] &= m
                    travail.append(b)
        r = 0
        if sorties:
            r = TOUT
            for m in sorties:
                r &= m
        c[g] = r
        return r

    def tues(self, g):
        """Registres dont un appelant de ``g`` ne peut pas garder la valeur à travers l'appel."""
        return self.ecrits_surs(g) | (VOLATILS if self.inconnu(g) else 0)

    def entrees(self, g):
        """Registres lus par ``g`` (ou ses appelées) avant d'être écrits : ses entrées réelles."""
        if not (self.lo <= g < self.hi):
            return ARGUMENTS | CTR
        c = self._cache("entrees")
        if g in c:
            return TOUT if c[g] is None else c[g]
        c[g] = None
        r = self._vivacite(g, 0)[g]
        c[g] = r
        return r

    def _significatifs(self, f, corps):
        """{adresse: registres dont la valeur AVANT l'instruction peut être significative} : écrite par une
        instruction de ``f``, reçue à l'entrée (arguments, non volatils), ou résultat (r3, r4, f1, f2) d'un appel ;
        une valeur gardée à travers un appel direct le reste. Ce qu'une appelée laisse dans ses autres registres
        n'est pas significatif : un appel indirect (ABI) ne reçoit comme arguments que des valeurs significatives."""
        sig = {f: self.arite(f) | (GARDES_ABI | GPR[1] | GPR[2] | GPR[13])}
        travail = [f]
        while travail:
            a = travail.pop()
            u, d, genre, t, succ = corps[a]
            m = sig[a] | d
            if genre in ("bctrl", "blrl") or (genre == "bl" and not (self.lo <= t < self.hi)):
                m = (m & ~VOLATILS) | RESULTATS
            elif genre == "bl":
                m |= RESULTATS & self.ecrits_possibles(t)
            for b in succ:
                n = sig.get(b, 0) | m
                if n != sig.get(b):
                    sig[b] = n
                    travail.append(b)
        return sig

    def arite(self, f):
        """Registres d'arguments que ``f`` reçoit vraiment (estimation) : ceux qu'elle lit avant de les écrire, par
        une instruction ou comme entrée d'une appelée directe (pas comme simple argument d'un appel indirect ou
        variadique, où elle ne fait que transmettre), complétés vers le bas (r3..rN, f1..fN : l'ABI les range dans
        l'ordre)."""
        c = self._cache("arite")
        if f in c:
            return ARGUMENTS if c[f] is None else c[f]
        c[f] = None
        lus = self._vivacite(f, 0, transmis=False)[f]
        m = 0
        for regs in (GPR[3:11], FPR[1:9]):
            haut = max((k for k, r in enumerate(regs) if lus & r), default=-1)
            for k in range(haut + 1):
                m |= regs[k]
        c[f] = m
        return m

    def _vivacite(self, f, au_retour, transmis=True):
        """{adresse: registres vivants AVANT l'instruction} dans ``f``, ``au_retour`` étant vivants à ses retours.
        Un appel indirect (ou hors du texte) lit, parmi ses arguments possibles, ceux qui portent une valeur
        significative (``_significatifs``), et tue tous les volatils."""
        corps = self.corps(f)
        sig = None
        viv = {a: 0 for a in corps}
        preds = {a: [] for a in corps}
        for a, (u, d, genre, t, succ) in corps.items():
            for b in succ:
                preds[b].append(a)
        travail = sorted(corps)
        dans = set(travail)
        while travail:
            a = travail.pop()
            dans.discard(a)
            u, d, genre, t, succ = corps[a]
            sortie = 0
            for b in succ:
                sortie |= viv[b]
            if genre in ("blr", "bclr"):
                sortie |= au_retour
            terminal = genre in ("terminal", "terminal_indirect")
            suite = au_retour if terminal else sortie
            if genre in ("bctrl", "blrl", "terminal_indirect") or (
                    genre in ("bl", "terminal") and not (self.lo <= t < self.hi)):
                if transmis and sig is None:
                    sig = self._significatifs(f, corps)
                v = u | (ARGUMENTS & sig[a] if transmis else 0) | (suite & ~(VOLATILS | d))
            elif genre in ("bl", "terminal") and self.variadique(t):
                if transmis and sig is None:
                    sig = self._significatifs(f, corps)
                v = (u | (ARGUMENTS & sig[a] if transmis else 0) | (self.entrees(t) & ~ARGUMENTS)
                     | (suite & ~(self.tues(t) | d)))
            elif genre in ("bl", "terminal"):
                v = u | self.entrees(t) | (suite & ~(self.tues(t) | d))
            else:
                v = u | (sortie & ~d)
            if v != viv[a]:
                viv[a] = v
                for p in preds[a]:
                    if p not in dans:
                        dans.add(p)
                        travail.append(p)
        return viv

    def variadique(self, g):
        """Fonction à nombre variable d'arguments : elle teste le bit 6 (cr1) que l'appelant pose (``crclr`` /
        ``crset``) avant d'enregistrer f1-f8 ; elle lit tous les registres d'arguments, mais ne se sert que de ceux
        que l'appelant a passés (traités comme pour un appel indirect)."""
        if not (self.lo <= g < self.hi):
            return False
        c = self._cache("variadique")
        if g not in c:
            c[g] = any(self.mot(a) >> 26 == 16 and (self.mot(a) >> 16) & 31 == 6
                       for a in range(g, min(self.fin(g), g + 4 * 40), 4))
        return c[g]

    def appelants_b(self, f):
        """Sites ``b`` (sauts terminaux) vers ``f`` depuis une autre fonction."""
        c = self._cache("sites_b")
        if not c:
            for i, w in enumerate(self.mots):
                if w >> 26 == 18 and not w & 3:
                    a = self.lo + 4 * i
                    c.setdefault(_cible_b(a, w), []).append(a)
        return [s for s in c.get(f, ()) if self.fonction_de(s) != f]

    def vivants_au_retour(self, f):
        """Volatils lus par les appelants de ``f`` après son retour (ce que ``f`` doit leur rendre)."""
        c = self._cache("au_retour")
        if f in c:
            return VOLATILS if c[f] is None else c[f]
        c[f] = None
        r = 0
        if f in self.pointees or not (self.appelants_directs(f) or self.appelants_b(f)):
            r |= RESULTATS
        for s in self.appelants_directs(f):
            r |= self.vivants_apres(s)
        for s in self.appelants_b(f):
            r |= self.vivants_au_retour(self.fonction_de(s))
        r &= VOLATILS
        c[f] = r
        return r

    def vivants(self, f):
        c = self._cache("vivants")
        if f not in c:
            c[f] = self._vivacite(f, self.vivants_au_retour(f))
        return c[f]

    def vivants_apres(self, s):
        """Registres vivants juste après l'appel en ``s`` (lus plus loin par l'appelant ou par ses appelants)."""
        f = self.fonction_de(s)
        viv = self.vivants(f)
        corps = self.corps(f)
        if s not in corps:
            raise InstructionNonAnalysee(f"appel 0x{s:08X} hors du corps atteint de 0x{f:08X} (début inconnu)")
        r = 0
        for b in corps[s][4]:
            r |= viv[b]
        return r

    def audit_remplacement(self, t, resultats):
        """Remplacement de ``t`` selon l'ABI (``resultats`` : les registres qu'il rend) : {site: registres}.

        Un appelant ne peut compter, après l'appel, que sur ce que ``t`` fait à coup sûr : un volatil que ``t`` ne
        peut pas écrire (gardé à travers l'appel), ou un registre que ``t`` écrit sur tous ses chemins (lu comme
        une sortie). Un volatil que ``t`` écrit seulement parfois, ou que du code inconnu atteint par ``t`` peut
        écrire, ne peut pas être relu par un appelant correct. Sont donc signalés, parmi les volatils vivants après
        l'appel en ``s`` (bl direct, ou retour de la fonction qui saute en ``s`` par un b terminal), ceux qui sont
        gardés ou écrits à coup sûr par ``t``, hors ``resultats`` et LR. Vide : sûr pour tous les appelants."""
        out = {}
        sensibles = VOLATILS & ~self.ecrits_possibles_exacts(t) & ~(resultats | LR)
        if not sensibles:
            return out                                   # t peut écrire tous les volatils : rien à garder
        for s in self.appelants_directs(t):
            m = self.vivants_apres(s) & sensibles
            if m:
                out[s] = m
        for s in self.appelants_b(t):
            m = self.vivants_au_retour(self.fonction_de(s)) & sensibles
            if m:
                out[s] = m
        return out


ENTREE = "entrée"
ARG_BITS = list(range(3, 11)) + [32 + k for k in range(1, 9)]
RESULTAT_BITS = {3, 4, 33, 34}


def nom_bit(k: int) -> str:
    return noms(1 << k)[0]


def _emballer(o, site):
    if isinstance(o, tuple) and o[0] == "garde":
        return ("garde", o[1], o[2] | {site})
    return ("garde", o, frozenset([site]))


def _passe_par(o, s):
    """L'origine ``o`` passe-t-elle par l'appel en ``s`` (écrite par lui, ou gardée à travers lui) ?"""
    if not isinstance(o, tuple):
        return False
    if o[0] == "appel":
        return o[1] == s
    return s in o[2] or _passe_par(o[1], s)


def premiere_origine(o):
    """L'origine première d'une valeur gardée (ENTREE, adresse d'instruction, ou ("appel", site))."""
    return o[1] if isinstance(o, tuple) and o[0] == "garde" else o

