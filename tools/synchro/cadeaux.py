"""Synchro v2, module 4 : quarts de cœur au sol partagés, « cadeau » au ramassage (--share-hearts,
activé par --coop-donjon, --no-hearts).

Étude : docs/etudes/sync2_cruches_drops.md §3.3 (la section « Vérification » finale prime : seul le
quart de cœur AU SOL, objet 0x07 avec un bit ``mItem`` de 0 à 0x1F, est traité ici ; le réceptacle
0x08 n'a pas de bit d'objet, il est laissé aux coffres, ``synchro/coffres.py``, STAGE_LIFE).

* Émetteur (``GiftWatch``) : suit les ``daItem`` de la salle avec ``m_itemNo == 7`` et un bit < 0x20.
  Le ramassage est constaté quand le bit d'objet passe à 1 chez nous ET que l'objet est passé par
  ``mItemStatus >= 5`` (démo d'obtention) ou a disparu à moins de 150 u de Link. Message
  ``gift {group, src: "piece:<index>:<bit>", item: 7, i, bit, stage, room}``. Anti-écho : un bit posé
  par ``GiftApply`` (ensemble ``granted``) n'est jamais annoncé (le quart retiré chez nous près de
  Link ressemblerait à un ramassage) ; le serveur ne relaie pas deux fois le même ``src``.
* Serveur (``wwmp_server.handle_gift``) : premier arrivé par ``src``, relais à TOUT le groupe (quelle
  que soit la zone), registre renvoyé à l'inscription (``full``) : un joueur arrivé plus tard reçoit
  aussi les quarts déjà pris.
* Receveur (``GiftApply``) : idempotent par le bit (déjà posé chez nous = déjà reçu, ou ramassé
  nous-mêmes). Jeu libre seulement (ni événement, ni changement de stage, salle stable). Puis :
    - contrat « propriétaire unique » (R19, docs/dev/SYNCHRO.md) : si l'inventaire partagé possède le
      compteur de vie max (``counter_owned()`` vrai : phase « compteurs » de synchro/inventaire.py), le
      quart est déjà compté par lui : on pose le bit SEUL ;
    - pack v19 : ``granter(3, index, bit, 7, 1, False)`` (GRANT « kind 3 » = ``mItem``, bit et
      ``execItemGet`` dans la même image), point d'accroche commun avec les coffres, branché par le
      client (``Mailbox.grant``) seulement si la boîte installée est en v19 ;
    - pack v17 (mode dégradé, aujourd'hui) : compteur en attente ``s16 [0x10474C0E] += 1``
      (``dComIfGp+0x5B5E``, appliqué et plafonné à 80 quarts par le HUD [HD-ASM 0x02594518]) et
      statistique HD ``u32 [gi+0x1928] += 1`` (comme ``item_func_kakera_heart`` 0x0254DBD8), puis le
      bit (copie sauvegardée, et vivante si c'est la stage chargée). ``--no-hearts-fallback`` : pas
      d'écriture du compteur, le quart attend le pack v19 (le bit n'est pas posé : rien n'est perdu).
  Le quart encore au sol chez nous est ensuite retiré (commande 5 et petit nuage, par le client).
* Jamais : un objet hors liste blanche ({0x07}), un bit >= 0x20 (on écrirait dans mVisitedRoom).
"""
from __future__ import annotations

import math
import struct
import threading
import time

from jeu import progress as prog
from synchro.obstacles import game_free

ITEM_PIECE = 0x07
GIFT_ITEMS = frozenset({ITEM_PIECE})        # liste blanche (réceptacle 0x08 : synchro/coffres.py)
BIT_MAX = 0x20                              # mItem : u32 mItem[1] (memBit+0x14), bits 0-31 seulement
MEM_ITEM = 0x14
PENDING_LIFE = prog.SYM_PLAY + 0x5B5E       # s16 mItemMaxLifeCount (0x10474C0E) [HD-ASM]
GI_MAX_LIFE = 0x20                          # u16 mMaxLife (quarts)
GI_PIECE_STAT = 0x1928                      # u32 statistique HD des quarts (gi+0x12C0+0x64C+0x1C) [HD-ASM]
MAX_LIFE_CAP = 80                           # plafond du HUD [HD-ASM 0x0259459C]
KIND_ITEM = 3                               # GRANT v19 : kind 3 = mItem (sync2_cruches_drops.md §3.6)


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def gift_src(idx: int, bit: int) -> str:
    return f"piece:{idx}:{bit}"


def valid_gift(e) -> dict | None:
    """Enregistrement « gift » validé (None si invalide ou hors liste blanche)."""
    if not isinstance(e, dict):
        return None
    item, i, bit, room = e.get("item"), e.get("i"), e.get("bit"), e.get("room", -1)
    if not (_int(item) and _int(i) and _int(bit) and _int(room)):
        return None
    if item not in GIFT_ITEMS or not 0 <= i < prog.N_MEM or not 0 <= bit < BIT_MAX or not -1 <= room < 64:
        return None
    src = gift_src(i, bit)
    if e.get("src", src) != src:
        return None
    stage = e.get("stage", "")
    stage = stage[:8] if isinstance(stage, str) else ""
    return {"src": src, "item": item, "i": i, "bit": bit, "stage": stage, "room": room}


def valid_record(e) -> dict | None:
    """Enregistrement du canal « gift » validé : quart AU SOL (``valid_gift``) ou quart donné par un personnage
    (``synchro/quarts_pnj.valid_pnj``, src « pnj:<lieu> »). Serveur et monde persistant ; chaque client ne garde que
    son genre (un ancien client ignore les « pnj: » : ``valid_gift`` les refuse)."""
    if isinstance(e, dict) and isinstance(e.get("src"), str) and e["src"].startswith("pnj:"):
        from synchro.quarts_pnj import valid_pnj
        return valid_pnj(e)
    return valid_gift(e)


def piece_byte(bit: int) -> tuple[int, int]:
    """(offset dans dSv_memBit_c, masque) du bit d'objet `bit` (u32 BE mItem[0] à +0x14)."""
    return MEM_ITEM + 3 - (bit >> 3), 1 << (bit & 7)


def piece_addresses(gi: int, idx: int, bit: int, cur_idx) -> list[tuple[int, int]]:
    """Où poser le bit : copie sauvegardée mSave[idx], plus la vivante si c'est la stage chargée."""
    off, mask = piece_byte(bit)
    out = [(gi + prog.GI_MEMORY + idx * prog.MEM_SIZE + off, mask)]
    if idx == cur_idx:
        out.append((gi + prog.GI_CUR_MEMORY + off, mask))
    return out


def piece_taken(mem, gi: int, idx: int, bit: int, cur_idx) -> bool | None:
    """Le bit est-il posé chez nous (vivante pour la stage chargée, sauvegardée sinon) ? None : illisible."""
    off, mask = piece_byte(bit)
    va = gi + (prog.GI_CUR_MEMORY if idx == cur_idx else prog.GI_MEMORY + idx * prog.MEM_SIZE) + off
    raw = mem.try_read(va, 1)
    return None if raw is None else bool(raw[0] & mask)


def max_life(mem, gi: int) -> int | None:
    """Vie max en quarts, attente comprise (gi+0x20 + compteur en attente)."""
    a, b = mem.try_read(gi + GI_MAX_LIFE, 2), mem.try_read(PENDING_LIFE, 2)
    if a is None or b is None:
        return None
    return struct.unpack(">H", a)[0] + max(0, struct.unpack(">h", b)[0])


def give_piece_fallback(mem, gi: int) -> bool:
    """Pack v17 : ce que fait item_func_kakera_heart (0x0254DBD8) : compteur en attente += 1 (le HUD
    l'applique et plafonne à 80) puis statistique HD gi+0x1928 += 1. Lecture-modification-écriture
    sous le verrou d'écriture de progress.py (course possible avec le jeu : fenêtre de quelques µs)."""
    with prog._write_lock:
        raw = mem.try_read(PENDING_LIFE, 2)
        if raw is None:
            return False
        n = struct.unpack(">h", raw)[0]
        if not 0 <= n < 0x50:
            return False                      # valeur aberrante : rien écrit
        mem.write(PENDING_LIFE, struct.pack(">h", n + 1))
        st = mem.try_read(gi + GI_PIECE_STAT, 4)
        if st is not None:
            mem.write(gi + GI_PIECE_STAT, struct.pack(">I", (struct.unpack(">I", st)[0] + 1) & 0xFFFFFFFF))
    return True


# ---------------------------------------------------------------------------
# Émetteur
# ---------------------------------------------------------------------------
class GiftWatch:
    GRACE = 5.0        # s : bit attendu après la disparition (ou la démo) du quart
    NEAR = 150.0       # u : disparu près de Link = ramassé (sinon : salle déchargée, A5…)

    def __init__(self):
        self.tracked: dict[int, dict] = {}       # pid -> {bit, pos, got, near}
        self.cands: dict[int, dict] = {}         # pid -> {bit, t, got, near}
        self.granted: set[tuple[int, int]] = set()   # (index, bit) posés par GiftApply : anti-écho
        self.sent: set[tuple[int, int]] = set()
        self.ctx = None
        self.notes: list[str] = []
        self.n_sent = self.n_echo = 0

    def observe(self, now: float, ctx, items, link_pos, taken) -> list[dict]:
        """ctx = (gi, stage, index de sauvegarde, salle) ; items : relevé des proc 255 ; link_pos :
        position de Link ; taken(index, bit) -> True / False / None (bit posé chez nous ?). Renvoie
        les enregistrements « gift » (sans type ni groupe)."""
        if ctx is None or ctx[2] is None:
            self.tracked.clear()
            self.cands.clear()
            self.ctx = None
            return []
        gi, stage, idx, room = ctx
        if self.ctx is None or self.ctx[:3] != (gi, stage, idx):
            self.tracked.clear()              # autre stage, autre partie : rien de suivi n'est valable
            self.cands.clear()
        self.ctx = ctx
        if items is not None:                 # relevé raté : on garde ce qu'on suit
            present = {}
            for it in items:
                bit = it.get("bit")
                if it.get("no") != ITEM_PIECE or not _int(bit) or not 0 <= bit < BIT_MAX:
                    continue
                pid = it.get("pid")
                pos = it.get("pos") or (0.0, 0.0, 0.0)
                near = link_pos is not None and math.dist([float(v) for v in pos],
                                                          [float(v) for v in link_pos]) <= self.NEAR
                t = self.tracked.get(pid) or {"bit": bit, "got": False}
                t.update(pos=pos, near=near, got=t["got"] or it.get("status", 0) >= 5)
                present[pid] = t
                if t["got"] and pid not in self.cands:
                    self.cands[pid] = {"bit": bit, "t": now, "got": True, "near": near}
            for pid, t in self.tracked.items():
                if pid not in present and pid not in self.cands:
                    self.cands[pid] = {"bit": t["bit"], "t": now, "got": t["got"], "near": t["near"]}
            self.tracked = present
        out = []
        for pid, c in list(self.cands.items()):
            key = (idx, c["bit"])
            if key in self.granted:
                self.n_echo += 1
                del self.cands[pid]
                continue
            if key in self.sent:
                del self.cands[pid]
                continue
            b = taken(idx, c["bit"])
            if b and (c["got"] or c["near"]):
                del self.cands[pid]
                self.sent.add(key)
                self.n_sent += 1
                out.append({"src": gift_src(idx, c["bit"]), "item": ITEM_PIECE, "i": idx, "bit": c["bit"],
                            "stage": str(stage)[:8], "room": room if _int(room) and -1 <= room < 64 else -1})
                self.notes.append("quart de cœur ramassé : les autres joueurs le reçoivent aussi")
            elif now - c["t"] > self.GRACE:
                del self.cands[pid]           # pas ramassé (salle déchargée, objet retiré…)
        return out


# ---------------------------------------------------------------------------
# Receveur
# ---------------------------------------------------------------------------
class GiftApply:
    RETRY = 1.0
    MAX_RECORDS = 1024

    def __init__(self, fallback: bool = True, clock=time.monotonic):
        self.fallback = fallback
        self.clock = clock
        self.pending: dict[str, dict] = {}
        self.done: set[str] = set()
        self.granted: set[tuple[int, int]] = set()   # partagé avec GiftWatch (anti-écho)
        self.granter = None              # pack v19 : granter(kind, idx, bit, item, count, need_stage) -> int
        self.counter_owned = None        # () -> bool : l'inventaire possède la vie max (contrat R19)
        self.next_try: dict[str, float] = {}
        self.notes: list[str] = []
        self.noted: set = set()
        self.n_given = self.n_bit_only = self.n_already = self.n_refused = 0
        self.why = ""

    def _once(self, tag, s: str) -> None:
        if tag not in self.noted and len(self.noted) < 2048:
            self.noted.add(tag)
            self.notes.append(s)

    def receive(self, rec: dict) -> bool:
        src = rec["src"]
        if src in self.done or src in self.pending or len(self.pending) >= self.MAX_RECORDS:
            return False
        self.pending[src] = rec
        return True

    def reset(self) -> None:
        self.pending.clear()
        self.done.clear()
        self.next_try.clear()
        self.noted.clear()

    def _finish(self, src: str) -> None:
        self.pending.pop(src, None)
        self.next_try.pop(src, None)
        self.done.add(src)

    def tick(self, mem, v, can_write: bool, settled: bool, now: float | None = None) -> list[tuple[int, int]]:
        """v : coffres.ChestView (gi, stage, cur_idx, busy). Renvoie les (index, bit) posés chez nous
        (le client retire le quart encore au sol dans la stage chargée)."""
        now = self.clock() if now is None else now
        done: list[tuple[int, int]] = []
        self.why = ""
        if not self.pending:
            return done
        if not v.ok:
            self.why = v.reason
            return done
        for src, rec in list(self.pending.items()):
            i, bit, item = rec["i"], rec["bit"], rec["item"]
            who = str(rec.get("nom") or "un joueur")[:24]
            if item not in GIFT_ITEMS or not 0 <= bit < BIT_MAX:
                self._finish(src)             # garde-fou (valid_gift l'a déjà refusé)
                self.n_refused += 1
                continue
            taken = piece_taken(mem, v.gi, i, bit, v.cur_idx)
            if taken is None:
                self.why = "sauvegarde illisible"
                continue
            if taken:
                self._finish(src)             # ramassé ici aussi, ou déjà reçu
                self.n_already += 1
                continue
            if now < self.next_try.get(src, 0.0):
                continue
            owned = bool(self.counter_owned()) if self.counter_owned is not None else False
            if not owned and self.granter is None and not self.fallback:
                self._once(("v19", src), f"quart de cœur ramassé par {who} : tu le recevras avec le pack v19")
                continue
            if not can_write:
                self.why = "écriture impossible (lecture seule ou --progress-no-apply)"
                continue
            if not settled or v.busy:
                self.why = v.busy or "attente de stabilité"
                continue
            busy = game_free(mem)             # relu juste avant d'écrire
            if busy:
                self.why = busy
                continue
            life = max_life(mem, v.gi)
            if owned:
                how = "bit seul : la vie max est partagée par l'inventaire"
                self.n_bit_only += 1
            elif self.granter is not None:
                try:
                    res = int(self.granter(KIND_ITEM, i, bit, item, 1, False))
                except Exception as exc:      # boîte aux lettres sans réponse : idempotent, on réessaie
                    self.next_try[src] = now + self.RETRY * 5
                    self._once(("err", src), f"quart de cœur de {who} pas encore donné ({exc}) : nouvel essai")
                    continue
                if res == -3:
                    self.next_try[src] = now + self.RETRY
                    continue
                if res not in (0, 1):
                    self._finish(src)
                    self.n_refused += 1
                    self.notes.append(f"quart de cœur de {who} refusé par le jeu (code {res})")
                    continue
                self.granted.add((i, bit))
                self._finish(src)
                if res == 1:
                    self.n_given += 1
                    self.notes.append(f"quart de cœur ramassé par {who} : +1 chez toi")
                    if i == v.cur_idx:
                        done.append((i, bit))
                continue
            else:
                if not give_piece_fallback(mem, v.gi):
                    self.next_try[src] = now + self.RETRY
                    self.why = "compteur de vie illisible"
                    continue
                how = "+1 chez toi"
                self.n_given += 1
            self.granted.add((i, bit))
            for va, mask in piece_addresses(v.gi, i, bit, v.cur_idx):
                prog._rmw(mem, va, mask)
            self._finish(src)
            cap = " (vie max déjà au plafond de 20 cœurs)" if (life is not None and life >= MAX_LIFE_CAP
                                                               and not owned) else ""
            self.notes.append(f"quart de cœur ramassé par {who} : {how}{cap}")
            if i == v.cur_idx:
                done.append((i, bit))
        return done


# ---------------------------------------------------------------------------
# Ensemble (client)
# ---------------------------------------------------------------------------
class GiftSync:
    SEND_RATE = 4

    def __init__(self, enabled: bool = True, group: str = "default", fallback: bool = True,
                 clock=time.monotonic):
        self.enabled, self.group, self.clock = enabled, group, clock
        self.watch = GiftWatch()
        self.apply = GiftApply(fallback=fallback, clock=clock)
        self.watch.granted = self.apply.granted       # anti-écho : même ensemble
        self.mine: dict[str, dict] = {}               # nos ramassages (renvoyés à chaque connexion)
        self.out: list[dict] = []
        self.sent_t: list[float] = []
        self._lock = threading.Lock()
        self.removed = 0
        self.notes: list[str] = []

    @property
    def granter(self):
        return self.apply.granter

    @granter.setter
    def granter(self, fn) -> None:
        """Pack v19 : ``fn(kind, idx, bit, item, count, need_stage) -> int`` (GRANT kind 3)."""
        self.apply.granter = fn

    def on_connect(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.out = [m for m in self.out if not m.get("full")]
            self.out.insert(0, {"type": "gift", "group": self.group, "full": 1})
            for m in self.mine.values():
                if m not in self.out:
                    self.out.append(m)

    def take_out(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        self.sent_t = [t for t in self.sent_t if now - t < 1.0]
        out, keep = [], []
        with self._lock:
            for m in self.out:
                if m.get("full"):
                    out.append(m)
                elif len(self.sent_t) < self.SEND_RATE:
                    self.sent_t.append(now)
                    out.append(m)
                else:
                    keep.append(m)
            self.out = keep
        return out

    def take_notes(self) -> list[str]:
        out = self.notes + self.watch.notes + self.apply.notes
        self.notes, self.watch.notes, self.apply.notes = [], [], []
        return out

    def receive(self, msg: dict, me=None) -> int:
        """Message « gift » du serveur (un enregistrement, ou ``full`` : le registre)."""
        if not self.enabled or msg.get("group") not in (None, self.group):
            return 0
        if msg.get("reset") in (1, True) and msg.get("from") is None:
            # A15 (rejoindre) : sauvegardes alignées par la copie, registre du serveur oublié. Rien à
            # renvoyer ; un quart repris ensuite (sauvegarde de l'hôte sans son bit) repart normalement.
            with self._lock:
                self.out = [m for m in self.out if m.get("full")]
                self.mine.clear()
            self.apply.reset()
            self.apply.granted.clear()
            self.watch.sent.clear()
            self.watch.cands.clear()
            self.notes.append("quarts de cœur : registre du serveur remis à zéro (rejoindre)")
            return 0
        recs = msg.get("list") if msg.get("full") else [msg]
        n = 0
        for raw in (recs if isinstance(recs, list) else [])[:1024]:
            if not isinstance(raw, dict):
                continue
            if me is not None and raw.get("from") == me and not msg.get("full"):
                continue
            rec = valid_gift(raw)
            if rec is None:
                continue
            if rec["src"] in self.mine:
                continue                      # c'est le nôtre
            rec["nom"] = str(raw.get("nom") or raw.get("by") or "?")[:24]
            n += self.apply.receive(rec)
        return n

    def tick(self, mem, v, items, link_pos, can_write: bool, settled: bool,
             now: float | None = None) -> list[tuple[int, int]]:
        """Une passe : nos ramassages -> file ; cadeaux reçus -> appliqués. Renvoie les bits posés."""
        if not self.enabled or mem is None:
            return []
        now = self.clock() if now is None else now
        ctx = (v.gi, v.stage, v.cur_idx, v.room) if v.ok else None
        recs = self.watch.observe(now, ctx, items, link_pos,
                                  lambda i, b: piece_taken(mem, v.gi, i, b, v.cur_idx))
        with self._lock:
            for r in recs:
                m = dict(r, type="gift", group=self.group)
                self.mine[r["src"]] = m
                self.apply.done.add(r["src"])
                self.out.append(m)
        return self.apply.tick(mem, v, can_write, settled, now)

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        a = self.apply
        s = f"{self.watch.n_sent} quart(s) ramassé(s) chez toi partagé(s), {a.n_given} reçu(s)"
        if a.n_bit_only:
            s += f", {a.n_bit_only} bit(s) seul(s) (vie max de l'inventaire)"
        if a.n_already:
            s += f", {a.n_already} déjà là"
        if self.removed:
            s += f", {self.removed} retiré(s) du sol"
        if a.pending:
            mode = "pack v19" if a.granter is not None else ("repli v17" if a.fallback else "attend le pack v19")
            s += f", {len(a.pending)} en attente ({a.why or mode})"
        return s
