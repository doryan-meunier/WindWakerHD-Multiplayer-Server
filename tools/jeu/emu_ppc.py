"""Mini assembleur + interpréteur PowerPC pour vérifier HORS LIGNE le pack v18 (WWMP_Coop).

Il ne connaît que le sous-ensemble de mnémoniques permis dans les packs (celles déjà présentes
dans des packs qui marchent sous Cemu 2.6) et la syntaxe Cemu utilisée par le pack :
étiquettes ``nom:``, ``.int`` (nombre, étiquette ou ``$variable``), ``nom@ha`` / ``nom@l``,
symboles ``0xADRESSE = nom:``, patchs ``0xADRESSE = instruction``, ``.origin = codecave``.

Ce n'est PAS l'assembleur de Cemu : il sert à exécuter la logique de l'asm contre un faux jeu
(tests/test_pack_coop.py) et à refuser toute mnémonique hors liste. Les encodages suivent
l'ISA PowerPC (vérifiés sur des instructions connues du .text du jeu dans les tests).
"""
from __future__ import annotations

import re
import struct

CODECAVE = 0x0180_0000
RETOUR_MAGIQUE = 0x0000_0F00          # adresse de retour fictive d'un appel depuis Python

# mnémoniques permises (relevées dans patch_mailbox.asm v17 et le pack communautaire Mega Cheats)
PERMISES = {"lwz", "stw", "lbz", "stb", "lhz", "sth", "lha", "stwu", "lwzu", "addi", "li", "lis",
            "ori", "andi.", "cmpwi", "cmpw", "cmplwi", "and", "subf", "add", "mr", "mflr", "mtlr",
            "b", "bl", "bla", "beq", "bne", "blt", "bge", "ble", "bgt", "blr"}
INTERDITES = {"mfcr", "mtcrf", "mfctr", "mtctr", "mulli", "bdnz", "bctr", "bctrl"}
# pack WWMP_Mailbox (tests/test_pack_mailbox.py) : en plus, les mnémoniques que la boîte aux
# lettres v17 utilise déjà et que Cemu 2.6 accepte (pack chargé et joué en direct)
PERMISES_MAILBOX = PERMISES | {"rlwinm", "srw", "lbzx", "lwzx", "extsh", "lfs", "stfs", "lfd", "stfd"}
F_FORM = {"lfs": 48, "lfd": 50, "stfs": 52, "stfd": 54}

D_FORM = {"lwz": 32, "lwzu": 33, "lbz": 34, "stw": 36, "stwu": 37, "stb": 38, "lhz": 40, "lha": 42, "sth": 44}
COND = {"blt": (12, 0), "bgt": (12, 1), "beq": (12, 2), "bge": (4, 0), "ble": (4, 1), "bne": (4, 2)}


class ErreurAsm(Exception):
    pass


def _reg(s: str) -> int:
    m = re.fullmatch(r"r(\d+)", s.strip())
    if not m or int(m.group(1)) > 31:
        raise ErreurAsm(f"registre invalide : {s}")
    return int(m.group(1))


class Assembleur:
    def __init__(self, texte: str, variables: dict[str, int] | None = None, base: int = CODECAVE,
                 permises: set[str] | None = None):
        self.texte = texte
        self.permises = PERMISES if permises is None else permises
        self.vars = {k.lstrip("$"): v for k, v in (variables or {}).items()}
        self.base = base
        self.labels: dict[str, int] = {}
        self.code: list[tuple[int, str, int]] = []        # (adresse, texte, n° de ligne)
        self.patchs: list[tuple[int, str, int]] = []
        self.mots: dict[int, int] = {}
        self.mnemoniques: set[str] = set()

    # -- valeurs
    def valeur(self, s: str) -> int:
        s = s.strip()
        m = re.fullmatch(r"(\w+)@(ha|l|h)", s)
        if m:
            v = self.valeur(m.group(1))
            if m.group(2) == "l":
                lo = v & 0xFFFF
                return lo - 0x10000 if lo & 0x8000 else lo
            if m.group(2) == "h":
                return (v >> 16) & 0xFFFF
            return ((v + 0x8000) >> 16) & 0xFFFF
        if s.startswith("$"):
            if s[1:] not in self.vars:
                raise ErreurAsm(f"variable de preset inconnue : {s}")
            return self.vars[s[1:]]
        if re.fullmatch(r"-?(0x[0-9A-Fa-f]+|\d+)", s):
            return int(s, 0)
        if s in self.labels:
            return self.labels[s]
        raise ErreurAsm(f"valeur inconnue : {s}")

    # -- passes
    def assembler(self) -> "Assembleur":
        pc = None
        for n, brut in enumerate(self.texte.splitlines(), 1):
            ligne = brut.split(";", 1)[0].strip()
            if not ligne or ligne.startswith("[") or ligne.startswith("moduleMatches"):
                continue
            if ligne.startswith(".origin"):
                if ligne.split("=", 1)[1].strip() != "codecave":
                    raise ErreurAsm(f"ligne {n} : seul .origin = codecave est prévu")
                pc = self.base
                continue
            m = re.fullmatch(r"(0x[0-9A-Fa-f]+)\s*=\s*(\w+):", ligne)
            if m:
                self._label(m.group(2), int(m.group(1), 16), n)
                continue
            m = re.fullmatch(r"(0x[0-9A-Fa-f]+)\s*=\s*(.+)", ligne)
            if m:
                self.patchs.append((int(m.group(1), 16), m.group(2).strip(), n))
                continue
            m = re.fullmatch(r"(\w+):\s*(.*)", ligne)
            if m:
                if pc is None:
                    raise ErreurAsm(f"ligne {n} : étiquette avant .origin")
                self._label(m.group(1), pc, n)
                ligne = m.group(2).strip()
                if not ligne:
                    continue
            if pc is None:
                raise ErreurAsm(f"ligne {n} : code avant .origin : {ligne}")
            self.code.append((pc, ligne, n))
            pc += 4
        for adr, ins, n in self.code:
            self.mots[adr] = self.encoder(ins, adr, n)
        for adr, ins, n in self.patchs:
            self.mots[adr] = self.encoder(ins, adr, n)
        self.fin = pc
        return self

    def _label(self, nom: str, adr: int, n: int) -> None:
        if nom in self.labels:
            raise ErreurAsm(f"ligne {n} : étiquette {nom} définie deux fois")
        self.labels[nom] = adr

    def encoder(self, ins: str, adr: int, n: int) -> int:
        try:
            return self._encoder(ins, adr)
        except ErreurAsm as e:
            raise ErreurAsm(f"ligne {n} : {e} ({ins})") from None

    def _encoder(self, ins: str, adr: int) -> int:
        m = re.fullmatch(r"(\.?[a-z]+\.?)\s*(.*)", ins)
        if not m:
            raise ErreurAsm("instruction illisible")
        op, reste = m.group(1), m.group(2)
        ops = [x.strip() for x in reste.split(",")] if reste else []
        if op == ".int":
            if len(ops) != 1:
                raise ErreurAsm(".int : une valeur par ligne")
            return self.valeur(ops[0]) & 0xFFFFFFFF
        if op in INTERDITES:
            raise ErreurAsm(f"mnémonique interdite : {op}")
        if op not in self.permises:
            raise ErreurAsm(f"mnémonique hors liste : {op}")
        self.mnemoniques.add(op)
        if op in D_FORM:
            rd = _reg(ops[0])
            mm = re.fullmatch(r"(.+)\((r\d+)\)", ops[1])
            if not mm:
                raise ErreurAsm("forme d(rA) attendue")
            d, ra = self.valeur(mm.group(1)), _reg(mm.group(2))
            if not -0x8000 <= d < 0x8000:
                raise ErreurAsm("déplacement hors 16 bits")
            return D_FORM[op] << 26 | rd << 21 | ra << 16 | (d & 0xFFFF)
        if op in F_FORM:
            mm = re.fullmatch(r"f(\d+)", ops[0])
            m2 = re.fullmatch(r"(.+)\((r\d+)\)", ops[1])
            if not mm or int(mm.group(1)) > 31 or not m2:
                raise ErreurAsm("forme fN, d(rA) attendue")
            d, ra = self.valeur(m2.group(1)), _reg(m2.group(2))
            if not -0x8000 <= d < 0x8000:
                raise ErreurAsm("déplacement hors 16 bits")
            return F_FORM[op] << 26 | int(mm.group(1)) << 21 | ra << 16 | (d & 0xFFFF)
        if op == "rlwinm":
            ra, rs = _reg(ops[0]), _reg(ops[1])
            sh, mb, me = (self.valeur(x) for x in ops[2:5])
            if not (0 <= sh < 32 and 0 <= mb < 32 and 0 <= me < 32):
                raise ErreurAsm("rlwinm : champs hors 0..31")
            return 21 << 26 | rs << 21 | ra << 16 | sh << 11 | mb << 6 | me << 1
        if op == "srw":
            a, s_, b = (_reg(x) for x in ops)
            return 31 << 26 | s_ << 21 | a << 16 | b << 11 | 536 << 1
        if op in ("lbzx", "lwzx"):
            d, a, b = (_reg(x) for x in ops)
            if a == 0:
                raise ErreurAsm("forme indexée avec rA = r0 (vaut 0) : interdit")
            return 31 << 26 | d << 21 | a << 16 | b << 11 | (87 if op == "lbzx" else 23) << 1
        if op == "extsh":
            a, s_ = _reg(ops[0]), _reg(ops[1])
            return 31 << 26 | s_ << 21 | a << 16 | 922 << 1
        if op in ("addi", "li", "lis"):
            if op == "li":
                rd, ra, v = _reg(ops[0]), 0, self.valeur(ops[1])
                code = 14
            elif op == "lis":
                rd, ra, v = _reg(ops[0]), 0, self.valeur(ops[1])
                code = 15
                if not 0 <= v <= 0x7FFF and not ops[1].endswith("@ha"):
                    raise ErreurAsm("lis : valeur positive < 0x8000 seulement")
            else:
                rd, ra, v = _reg(ops[0]), _reg(ops[1]), self.valeur(ops[2])
                code = 14
                if ra == 0:
                    raise ErreurAsm("addi rD, r0, … vaut li : interdit")
            if code == 14 and not -0x8000 <= v < 0x8000:
                raise ErreurAsm("immédiat hors 16 bits signés")
            return code << 26 | rd << 21 | ra << 16 | (v & 0xFFFF)
        if op in ("ori", "andi."):
            ra, rs, v = _reg(ops[0]), _reg(ops[1]), self.valeur(ops[2])
            if not 0 <= v <= 0xFFFF:
                raise ErreurAsm("immédiat hors 16 bits non signés")
            return (24 if op == "ori" else 28) << 26 | rs << 21 | ra << 16 | v
        if op in ("cmpwi", "cmplwi"):
            ra, v = _reg(ops[0]), self.valeur(ops[1])
            if op == "cmpwi" and not -0x8000 <= v < 0x8000:
                raise ErreurAsm("cmpwi : immédiat hors 16 bits signés")
            if op == "cmplwi" and not 0 <= v <= 0xFFFF:
                raise ErreurAsm("cmplwi : immédiat hors 16 bits")
            return (11 if op == "cmpwi" else 10) << 26 | ra << 16 | (v & 0xFFFF)
        if op == "cmpw":
            return 31 << 26 | _reg(ops[0]) << 16 | _reg(ops[1]) << 11
        if op in ("and", "subf", "add"):
            a, b, c = (_reg(x) for x in ops)
            if op == "and":
                return 31 << 26 | b << 21 | a << 16 | c << 11 | 28 << 1
            if op == "subf":
                return 31 << 26 | a << 21 | b << 16 | c << 11 | 40 << 1
            return 31 << 26 | a << 21 | b << 16 | c << 11 | 266 << 1
        if op == "mr":
            a, s = _reg(ops[0]), _reg(ops[1])
            return 31 << 26 | s << 21 | a << 16 | s << 11 | 444 << 1
        if op == "mflr":
            return 31 << 26 | _reg(ops[0]) << 21 | 8 << 16 | 339 << 1
        if op == "mtlr":
            return 31 << 26 | _reg(ops[0]) << 21 | 8 << 16 | 467 << 1
        if op == "blr":
            return 0x4E800020
        if op in ("b", "bl", "bla"):
            cible = self.valeur(ops[0])
            if op == "bla":
                if cible & 3 or not 0 <= cible < 0x0200_0000:
                    raise ErreurAsm("bla : cible absolue hors portée")
                return 18 << 26 | (cible & 0x03FFFFFC) | 3
            dep = cible - adr
            if dep & 3 or not -0x0200_0000 <= dep < 0x0200_0000:
                raise ErreurAsm("b/bl : cible hors de ±32 Mo")
            return 18 << 26 | (dep & 0x03FFFFFC) | (1 if op == "bl" else 0)
        if op in COND:
            bo, bi = COND[op]
            dep = self.valeur(ops[0]) - adr
            if dep & 3 or not -0x8000 <= dep < 0x8000:
                raise ErreurAsm("branchement conditionnel hors de ±32 Ko")
            return 16 << 26 | bo << 21 | bi << 16 | (dep & 0xFFFC)
        raise ErreurAsm(f"non encodée : {op}")


# ---------------------------------------------------------------------------------------------
class Memoire:
    """Mémoire invitée big-endian, par pages de 4 Kio créées à la demande (lecture d'une page
    absente : 0, et noté dans ``lectures_hors``)."""

    def __init__(self):
        self.pages: dict[int, bytearray] = {}
        self.ecritures: list[tuple[int, int]] = []
        self.lectures_hors: list[int] = []
        self.trace_ecritures = False

    def _page(self, a: int, creer: bool) -> bytearray | None:
        p = self.pages.get(a >> 12)
        if p is None and creer:
            p = self.pages[a >> 12] = bytearray(4096)
        return p

    def lire(self, a: int, n: int) -> bytes:
        out = bytearray()
        for i in range(n):
            p = self._page(a + i, False)
            if p is None:
                self.lectures_hors.append(a + i)
                out.append(0)
            else:
                out.append(p[(a + i) & 0xFFF])
        return bytes(out)

    def ecrire(self, a: int, data: bytes) -> None:
        if self.trace_ecritures:
            self.ecritures.append((a, len(data)))
        for i, b in enumerate(data):
            self._page(a + i, True)[(a + i) & 0xFFF] = b

    def u32(self, a): return struct.unpack(">I", self.lire(a, 4))[0]
    def s32(self, a): return struct.unpack(">i", self.lire(a, 4))[0]
    def u16(self, a): return struct.unpack(">H", self.lire(a, 2))[0]
    def u8(self, a): return self.lire(a, 1)[0]
    def w32(self, a, v): self.ecrire(a, struct.pack(">I", v & 0xFFFFFFFF))
    def w16(self, a, v): self.ecrire(a, struct.pack(">H", v & 0xFFFF))

    def u16s(self, a: int, maxi: int = 200) -> str:
        out = []
        for i in range(maxi):
            c = self.u16(a + 2 * i)
            if c == 0:
                break
            out.append(chr(c))
        return "".join(out)

    def poser_u16s(self, a: int, texte: str) -> None:
        self.ecrire(a, texte.encode("utf-16-be") + b"\0\0")

    # interface « GuestMemory » minimale (menu_zone.ZoneMenu, find)
    def read(self, va: int, n: int) -> bytes:
        return self.lire(va, n)

    def write(self, va: int, data: bytes) -> None:
        self.ecrire(va, data)

    def find_bytes(self, needle: bytes, lo: int = 0, hi: int = 1 << 32, limit: int = 200) -> list[int]:
        out = []
        for k in sorted(self.pages):
            base = k << 12
            if not lo <= base < hi:
                continue
            d = bytes(self.pages[k]) + bytes(self.pages.get(k + 1, b""))[:len(needle) - 1]
            i = d.find(needle)
            while 0 <= i < 4096 and len(out) < limit:
                out.append(base + i)
                i = d.find(needle, i + 1)
        return out


class Cpu:
    """Interpréteur du sous-ensemble ci-dessus. ``stubs`` : adresse -> fonction Python (cpu)
    appelée quand le PC y arrive (fonctions du jeu) ; elle revient par LR sauf si elle change pc."""

    def __init__(self, mem: Memoire):
        self.mem = mem
        self.r = [0] * 32
        self.f = [0] * 32                       # registres flottants : bits bruts (copies seulement)
        self.ctr = 0
        self.lr = 0
        self.cr0 = (False, False, False)        # lt, gt, eq
        self.pc = 0
        self.stubs: dict[int, object] = {}
        self.pas = 0

    def _cmp(self, a: int, b: int, signe: bool = True) -> None:
        if signe:
            a = a - (1 << 32) if a & 0x80000000 else a
            b = b - (1 << 32) if b & 0x80000000 else b
        self.cr0 = (a < b, a > b, a == b)

    def appeler(self, adr: int, limite: int = 200000, **regs) -> "Cpu":
        for k, v in regs.items():
            self.r[int(k[1:])] = v & 0xFFFFFFFF
        if self.r[1] == 0:
            self.r[1] = 0x0FF0_0000
        self.lr = RETOUR_MAGIQUE
        self.pc = adr
        n = 0
        while self.pc != RETOUR_MAGIQUE:
            n += 1
            if n > limite:
                raise RuntimeError(f"boucle infinie ? pc = 0x{self.pc:08X}")
            if self.pc in self.stubs:
                retour = self.lr
                self.stubs[self.pc](self)
                if self.pc in self.stubs:      # le stub n'a pas changé pc : retour par LR
                    self.pc = retour
                continue
            self.pas_un()
        self.pas += n
        return self

    def pas_un(self) -> None:
        pc = self.pc
        w = self.mem.u32(pc)
        op = w >> 26
        rd, ra, rb = (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31
        simm = w & 0xFFFF
        simm = simm - 0x10000 if simm & 0x8000 else simm
        uimm = w & 0xFFFF
        r, m = self.r, self.mem
        npc = pc + 4
        if op in (32, 33, 34, 36, 37, 38, 40, 42, 44):
            ea = ((r[ra] if ra else 0) + simm) & 0xFFFFFFFF
            if op in (33, 37) and ra == 0:
                raise RuntimeError("forme avec mise à jour sur r0")
            if op in (32, 33):
                r[rd] = m.u32(ea)
            elif op == 34:
                r[rd] = m.u8(ea)
            elif op == 40:
                r[rd] = m.u16(ea)
            elif op == 42:
                v = m.u16(ea)
                r[rd] = (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF
            elif op in (36, 37):
                m.w32(ea, r[rd])
            elif op == 38:
                m.ecrire(ea, bytes([r[rd] & 0xFF]))
            elif op == 44:
                m.w16(ea, r[rd])
            if op in (33, 37):
                r[ra] = ea
        elif op in (48, 50, 52, 54):
            ea = ((r[ra] if ra else 0) + simm) & 0xFFFFFFFF
            if op == 48:
                self.f[rd] = m.u32(ea)
            elif op == 50:
                self.f[rd] = m.u32(ea) << 32 | m.u32(ea + 4)
            elif op == 52:
                m.w32(ea, self.f[rd] & 0xFFFFFFFF)
            else:
                m.w32(ea, self.f[rd] >> 32)
                m.w32(ea + 4, self.f[rd])
        elif op == 21:
            sh, mb, me = rb, (w >> 6) & 31, (w >> 1) & 31
            v = r[rd]
            rot = ((v << sh) | (v >> (32 - sh))) & 0xFFFFFFFF if sh else v
            masque = 0
            i = mb
            while True:
                masque |= 1 << (31 - i)
                if i == me:
                    break
                i = (i + 1) & 31
            r[ra] = rot & masque
            if w & 1:
                self._cmp(r[ra], 0)
        elif op == 14:
            r[rd] = ((r[ra] if ra else 0) + simm) & 0xFFFFFFFF
        elif op == 15:
            r[rd] = ((r[ra] if ra else 0) + (simm << 16)) & 0xFFFFFFFF
        elif op == 24:
            r[ra] = r[rd] | uimm
        elif op == 28:
            r[ra] = r[rd] & uimm
            self._cmp(r[ra], 0)
        elif op == 11:
            self._cmp(r[ra], simm & 0xFFFFFFFF)
        elif op == 10:
            self._cmp(r[ra], uimm, signe=False)
        elif op == 31:
            xo = (w >> 1) & 0x3FF
            if xo == 0:
                self._cmp(r[ra], r[rb])
            elif xo == 28:
                r[ra] = r[rd] & r[rb]
            elif xo == 40:
                r[rd] = (r[rb] - r[ra]) & 0xFFFFFFFF
            elif xo == 266:
                r[rd] = (r[ra] + r[rb]) & 0xFFFFFFFF
            elif xo == 444:
                r[ra] = r[rd] | r[rb]
            elif xo == 339:
                r[rd] = self.ctr if ra == 9 else self.lr
            elif xo == 467:
                if ra == 9:
                    self.ctr = r[rd]
                else:
                    self.lr = r[rd]
            elif xo == 19:                       # mfcr (écrit en .int dans le pack)
                lt, gt, eq = self.cr0
                r[rd] = (lt << 31) | (gt << 30) | (eq << 29)
            elif xo == 144:                      # mtcrf (idem)
                v = r[rd]
                self.cr0 = (bool(v >> 31 & 1), bool(v >> 30 & 1), bool(v >> 29 & 1))
            elif xo == 536:
                n = r[rb] & 0x3F
                r[ra] = 0 if n > 31 else r[rd] >> n
            elif xo == 87:
                r[rd] = m.u8((r[ra] + r[rb]) & 0xFFFFFFFF)
            elif xo == 23:
                r[rd] = m.u32((r[ra] + r[rb]) & 0xFFFFFFFF)
            elif xo == 922:
                v = r[rd] & 0xFFFF
                r[ra] = (v - 0x10000 if v & 0x8000 else v) & 0xFFFFFFFF
            else:
                raise RuntimeError(f"op 31/{xo} non gérée à 0x{pc:08X}")
        elif op == 18:
            li = w & 0x03FFFFFC
            li = li - 0x04000000 if li & 0x02000000 else li
            cible = li if w & 2 else pc + li
            if w & 1:
                self.lr = npc
            npc = cible & 0xFFFFFFFF
        elif op == 16:
            bo, bi = rd, ra
            bd = w & 0xFFFC
            bd = bd - 0x10000 if bd & 0x8000 else bd
            bit = self.cr0[bi]
            if (bo == 12 and bit) or (bo == 4 and not bit):
                npc = pc + bd
        elif w == 0x4E800020:
            npc = self.lr
        elif op == 59 and (w >> 1) & 31 == 25 and not w & 1:
            # fmuls frD, frA, frC (simple précision) : les deux opérandes doivent venir d'un lfs (32 bits bruts)
            fa, fc = self.f[ra], self.f[(w >> 6) & 31]
            if fa >> 32 or fc >> 32:
                raise RuntimeError(f"fmuls sur un registre non chargé par lfs à 0x{pc:08X}")
            x = struct.unpack(">f", struct.pack(">I", fa))[0] * struct.unpack(">f", struct.pack(">I", fc))[0]
            try:
                self.f[rd] = struct.unpack(">I", struct.pack(">f", x))[0]
            except OverflowError:
                self.f[rd] = 0x7F800000 if x > 0 else 0xFF800000
        else:
            raise RuntimeError(f"instruction non gérée 0x{w:08X} à 0x{pc:08X}")
        self.pc = npc


def charger(texte: str, mem: Memoire, variables: dict[str, int], base: int = CODECAVE,
            permises: set[str] | None = None) -> Assembleur:
    """Assemble et écrit le code + les patchs en mémoire."""
    a = Assembleur(texte, variables, base, permises).assembler()
    for adr, w in a.mots.items():
        mem.w32(adr, w)
    return a
