"""Ganon spectral à plusieurs : une boule par Link, et il ne la prend que si TOUS l'ont renvoyée
(docs/etudes/serveur_jeu_v2.md §12).

Le combat du jeu est fait pour un seul joueur : Ganon spectral lance une boule d'énergie, Link la renvoie à l'épée, il
la renvoie à son tour, et au bout de quelques échanges il la rate, tombe, et Link peut le frapper. À plusieurs, chacun
garde SON Ganon spectral (le filtre A9 ne change pas) ; la règle voulue par l'utilisateur :

* **une boule par Link, en même temps** : le serveur désigne un Ganon de RÉFÉRENCE (celui du premier joueur du groupe
  entré dans la stage ; il part : le suivant). Quand il commence un tir, tous les autres tirent aussi ; ils ne tirent
  jamais d'eux-mêmes (leur attente avant le tir est tenue au-dessus de zéro) ;
* **il ne rate jamais la boule tout seul** : tant que la volée commune n'est pas réussie, « il la rate » (``m689``)
  est remis à 0 chez chacun : il renvoie toujours ;
* **tous doivent réussir** : quand TOUS les joueurs présents ont une boule en jeu et l'ont renvoyée au moins
  ``SEUIL`` fois, le serveur donne l'ordre « tombe » : chez chacun ``m689`` passe à 1, et le prochain renvoi le touche
  (il tombe, comme dans le jeu) ;
* **un seul rate, la volée est perdue pour tous** : la boule d'un joueur s'arrête (elle l'a touché, il l'a manquée)
  avant l'ordre « tombe » : le serveur donne l'ordre « rate », les boules encore en jeu s'éteignent (``m408 = 35``,
  l'état « fin de la boule » du jeu) et un nouveau tir commun viendra.

Seul dans la stage : rien de tout cela (le combat du jeu, tel quel).

QUI COMPTE (2026-11-20, demande de l'utilisateur : un joueur qui ne joue pas ne doit pas bloquer l'équipe) : seulement
les joueurs **dans la zone du boss**, c'est-à-dire ceux que LEUR Ganon spectral combat. C'est le jeu qui le décide :
son Ganon est en attente (``mAction == 0``, ``standby``) tant que Link n'est pas dans sa zone (distance à sa place,
hauteur à la Forteresse : d_a_fganon.cpp ``standby`` et ``move``). Un Link à terre (vie à 0) ne compte pas non plus.
Un joueur qui ne compte pas l'annonce (``ici`` = 0) : le serveur l'oublie, les autres ne l'attendent pas, et chez lui
rien n'est écrit (son Ganon reste celui du jeu). Seul à compter : le combat du jeu, tel quel.

Tout est fait par des écritures que le jeu fait lui-même [DECOMP d_a_fganon.cpp ``fly``, ``shot``,
``energy_ball_move`` ; HD-ASM relu par tests/test_boss_volee.py ; les champs à partir de 0x386 sont 0x234 plus loin
dans le jeu HD] :

* tir : ``fly`` écrit ``mAction = 6 ; mMode = 0`` quand son attente ``m3A4[1]`` arrive à 0 (0x0213C454) ;
* il rate : ``shot`` écrit ``m689 = 1`` (0x0213C910) quand le joueur a renvoyé 7 fois, ou au hasard après 4 échanges ;
  ``m689`` lu à 0x0213C80C : à 1, il ne frappe pas la boule et elle le touche ;
* fin de la boule : ``energy_ball_move`` écrit ``m408 = 35`` (0x02137168), puis éteint ses effets et la remet à 0.

Messages (type « bossv ») : client -> serveur ``{group, stage, key, n, en, tir?}`` (``n`` renvois du joueur dans la
volée, ``en`` : sa boule est en jeu, ``tir`` : la référence commence un tir) ; serveur -> membres du groupe dans la
stage ``{..., joueurs, ref, ordre?, seq?, tir?, from}``. Un ancien client ignore le message (son combat reste celui
du jeu, et il ne compte pas : le serveur ne compte que les joueurs qui envoient « bossv »).
"""
from __future__ import annotations

import struct
import time

from jeu import progress as prog
from synchro import boss_pv

PROC = 241                  # Fganon
MAX_PV = 30                 # le combat principal (m2BC == 0) ; clones et apparitions de la Tour : autres maxima
# variante (m2BC) -> PV max attendus : le combat de la Forteresse (0, 30 PV) et les apparitions de la Tour de Ganon
# (1, 100 PV : même code pour la boule ; c'est sur elles que la mécanique a été essayée avec deux vrais jeux, le
# 2026-11-20). La variante 2 (la boule le fait disparaître au lieu de le faire tomber) n'est jamais touchée.
VARIANTES = {0: MAX_PV, 1: 100}
# [HD-ASM, voir le docstring]
OFF_M2BC = 0x3D8            # u8 : 0 combat principal (docs/etudes/sync3_boss_pv.md)
OFF_ACTION = 0x5BA          # s16 mAction (5 vol, 6 tir, 8 à terre), puis s16 mMode (+0x5BC)
OFF_ATTENTE_TIR = 0x5DA     # s16 m3A4[1] : attente avant le tir (0 : ``fly`` tire)
OFF_BOULE = 0x63C           # s8 m408 : 0 aucune, 1-2 en formation, 3 vers Link, 4 renvoyée par Ganon, 5 par Link
OFF_RENVOIS = 0x8BC         # s8 m688 : renvois du joueur dans cet échange
OFF_RATE = 0x8BD            # s8 m689 : 1 = il ne renverra pas la prochaine (elle le touche)
ACTION_ATTENTE = 0          # standby : Link n'est pas dans la zone du boss
GI_VIE = 0x22               # u16 : vie de Link (quarts de cœur) dans la sauvegarde (gi + 0x20 : max, puis vie)
ACTION_VOL, ACTION_TIR, ACTION_A_TERRE = 5, 6, 8
BOULE_FIN = 35
BOULES_EN_JEU = (3, 4, 5)
SEUIL = 3                   # renvois que CHAQUE joueur doit avoir faits pour que Ganon rate la boule
ATTENTE_TENUE, ATTENTE_SEUIL = 60, 30
TIR_AGE = 1.0               # s : un tir annoncé depuis plus longtemps n'est plus lancé
REF_SILENCE = 5.0
RAPPEL = 1.0
FRAIS = 4.0                 # s : un joueur sans nouvelle depuis plus longtemps ne compte plus (serveur)
DEBIT = 5


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def valide(msg) -> dict | None:
    if not isinstance(msg, dict):
        return None
    stage, key, n, en = msg.get("stage"), msg.get("key"), msg.get("n"), msg.get("en")
    if not isinstance(stage, str) or not 0 < len(stage) <= 8 or boss_pv.key_proc(key) != PROC:
        return None
    if not _int(n) or not 0 <= n <= 127 or en not in (0, 1):
        return None
    out = {"stage": stage, "key": key, "n": n, "en": en}
    if msg.get("tir") in (1, True):
        out["tir"] = 1
    if msg.get("ici") in (0, False):
        out["ici"] = 0                              # pas dans la zone du boss (ou à terre) : il ne compte pas
    return out


def link_vivant(mem) -> bool:
    """Vie de Link > 0 (lue dans la sauvegarde en mémoire) ; illisible : vivant (rien ne change)."""
    try:
        gi = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
        if gi is None:
            return True
        vie = mem.try_read(struct.unpack(">I", gi)[0] + GI_VIE, 2)
        return vie is None or struct.unpack(">H", vie)[0] > 0
    except Exception:                               # noqa: BLE001
        return True


def lire(mem, base: int, variantes=None) -> dict | None:
    """État du Ganon spectral à ``base`` ; None si illisible ou si ce n'est pas une variante permise."""
    m2bc = mem.try_read(base + OFF_M2BC, 1)
    act = mem.try_read(base + OFF_ACTION, 4)
    att = mem.try_read(base + OFF_ATTENTE_TIR, 2)
    boule = mem.try_read(base + OFF_BOULE, 1)
    r = mem.try_read(base + OFF_RENVOIS, 2)
    if None in (m2bc, act, att, boule, r) or m2bc[0] not in (VARIANTES if variantes is None else variantes):
        return None
    action, mode = struct.unpack(">hh", act)
    b = struct.unpack(">b", boule)[0]
    n, rate = struct.unpack(">bb", r)
    if not 0 <= action <= 30 or not 0 <= b <= 40 or not 0 <= n <= 127 or rate not in (0, 1):
        return None
    return {"action": action, "mode": mode, "attente": struct.unpack(">h", att)[0], "boule": b,
            "en": int(b in BOULES_EN_JEU), "n": n, "rate": rate, "variante": m2bc[0]}


def _ecriture_permise(mem, b: dict, variantes=None) -> bool:
    h = boss_pv.read_header(mem, b["base"])
    if h is None or h[0] != b["pid"] or h[1] != PROC:
        return False
    mx = mem.try_read(b["base"] + boss_pv.OFF_MAX_HEALTH, 1)
    m2bc = mem.try_read(b["base"] + OFF_M2BC, 1)
    v = VARIANTES if variantes is None else variantes
    if mx is None or m2bc is None or v.get(m2bc[0]) != mx[0]:
        return False
    ev = prog.read_event(mem)
    if ev is None or ev.busy:
        return False
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    return nxt is not None and not nxt[0]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class VoleeSync:
    """Côté client. ``tick(mem, now, stage, bosses, free, can)`` (mêmes ``bosses`` que boss_pv) ; ``receive``,
    ``take_out``, ``take_notes``."""

    def __init__(self, enabled: bool = False, group: str = "default", clock=time.monotonic, variantes=None):
        self.enabled, self.group, self.clock = bool(enabled), group, clock
        self.variantes = dict(VARIANTES if variantes is None else variantes)
        self.stage: str | None = None
        self.me: int | None = None
        self.joueurs: dict[str, int] = {}       # clé -> joueurs qui participent (dit par le serveur)
        self.ref: dict[str, int] = {}
        self.ref_t: dict[str, float] = {}
        self.envoye: dict[str, tuple] = {}
        self.envoye_t: dict[str, float] = {}
        self.pids: dict[str, int] = {}
        self.action_avant: dict[str, int] = {}
        self.tir_recu: dict[str, float] = {}
        self.tir_envoyer: set = set()
        self.ordres_vus: dict[str, int] = {}
        self.tombe: dict[str, bool] = {}        # clé -> l'ordre « tombe » vaut pour la boule en jeu
        self.rate: dict[str, bool] = {}         # clé -> l'ordre « rate » reste à appliquer
        self.out: list[dict] = []
        self.emis: list[float] = []
        self.notes: list[str] = []
        self.dit: set = set()
        self.tirs_suivis = self.tombes = self.rates = self.tenues = 0
        self.ici: dict[str, int] = {}           # clé -> nous comptons (dans la zone du boss, Link debout)
        self.vu: dict[str, dict] = {}

    def take_out(self, now: float | None = None) -> list[dict]:
        if not self.out:
            return []
        now = self.clock() if now is None else now
        self.emis = [t for t in self.emis if 0 <= now - t < 1.0]
        n = max(0, DEBIT - len(self.emis))
        out, self.out = self.out[:n], self.out[n:]
        self.emis += [now] * len(out)
        return out

    def take_notes(self) -> list[str]:
        n, self.notes = self.notes, []
        return n

    def receive(self, msg: dict, me: int | None = None) -> None:
        if not self.enabled or msg.get("group", self.group) != self.group or msg.get("stage") != self.stage:
            return
        key = msg.get("key")
        if boss_pv.key_proc(key) != PROC:
            return
        if me is not None:
            self.me = me
        now = self.clock()
        j, ref = msg.get("joueurs"), msg.get("ref")
        if _int(j) and 0 <= j <= 64:
            self.joueurs[key] = j
        if _int(ref) and ref > 0:
            self.ref[key] = ref
            self.ref_t[key] = now
        if msg.get("tir") in (1, True) and msg.get("from") is not None and msg.get("from") == self.ref.get(key) \
                and msg.get("from") != self.me:
            self.tir_recu[key] = now
        ordre, seq = msg.get("ordre"), msg.get("seq")
        if ordre in ("tombe", "rate") and _int(seq) and self.ordres_vus.get(key) != seq:
            self.ordres_vus[key] = seq
            if ordre == "tombe":
                self.tombe[key] = True
                self.rate[key] = False
            else:
                self.rate[key] = True
                self.tombe[key] = False

    def reset_network(self) -> None:
        for d in (self.joueurs, self.ref, self.ref_t, self.envoye, self.envoye_t, self.tir_recu, self.ordres_vus,
                  self.tombe, self.rate):
            d.clear()
        self.tir_envoyer.clear()
        self.out.clear()

    def _nouvelle_stage(self, stage) -> None:
        self.stage = stage
        self.reset_network()
        for d in (self.pids, self.action_avant, self.vu):
            d.clear()
        self.dit.clear()

    def tick(self, mem, now: float | None, stage: str | None, bosses: list | None, free: bool, can: bool) -> int:
        if not self.enabled:
            return 0
        now = self.clock() if now is None else now
        if stage != self.stage:
            self._nouvelle_stage(stage)
        if stage is None or not bosses:
            return 0
        ecrits = 0
        for b in bosses:
            if b.get("proc") != PROC or b.get("max") not in self.variantes.values():
                continue
            key = boss_pv.boss_key(PROC, b.get("params", 0))
            e = lire(mem, b["base"], self.variantes)
            if e is None or self.variantes.get(e["variante"]) != b.get("max"):
                continue
            if self.pids.get(key) != b["pid"]:
                self.pids[key] = b["pid"]
                self.envoye.pop(key, None)
                self.tombe[key] = self.rate[key] = False
            self.vu[key] = e
            # la référence annonce le tir qu'elle commence
            avant, self.action_avant[key] = self.action_avant.get(key), e["action"]
            suis_ref = self.me is not None and self.ref.get(key) == self.me
            if suis_ref and avant == ACTION_VOL and e["action"] == ACTION_TIR:
                self.tir_envoyer.add(key)
            # dans la zone du boss ? (son Ganon le combat, et Link n'est pas à terre)
            ici = int(e["action"] != ACTION_ATTENTE and link_vivant(mem))
            self.ici[key] = ici
            if not ici:
                self.tir_envoyer.discard(key)
            # notre volée vers le serveur
            etat = (e["n"] if e["en"] and ici else 0, e["en"] if ici else 0, ici)
            if self.envoye.get(key) != etat or now - self.envoye_t.get(key, -1e9) >= RAPPEL or key in self.tir_envoyer:
                msg = {"type": "bossv", "group": self.group, "stage": self.stage, "key": key, "n": etat[0],
                       "en": etat[1]}
                if not ici:
                    msg["ici"] = 0
                if key in self.tir_envoyer:
                    self.tir_envoyer.discard(key)
                    msg["tir"] = 1
                self.out = [m for m in self.out if m["key"] != key or "tir" in m] + [msg]
                self.envoye[key], self.envoye_t[key] = etat, now
            if not e["en"]:
                self.tombe[key] = False                  # la boule est finie : l'ordre « tombe » ne vaut plus
                self.rate[key] = False
            # seul à compter, hors de la zone du boss, à terre (ou serveur muet) : le combat du jeu, tel quel
            if not ici or self.joueurs.get(key, 0) < 2 or now - self.ref_t.get(key, -1e9) >= REF_SILENCE:
                continue
            self._dire(("intro", key), "Ganon spectral à plusieurs : une boule par Link, et il ne la rate que si tout "
                                       f"le monde l'a renvoyée {SEUIL} fois ; un seul la manque, la volée est perdue")
            if not (free and can):
                continue
            # 1. un seul a manqué : notre boule s'éteint
            if self.rate.get(key) and e["en"] and e["action"] != ACTION_A_TERRE:
                if _ecriture_permise(mem, b, self.variantes):
                    mem.write(b["base"] + OFF_BOULE, struct.pack(">b", BOULE_FIN))
                    self.rate[key] = False
                    self.rates += 1
                    ecrits += 1
                    self.notes.append("Ganon spectral : un coéquipier a manqué sa boule, la volée est perdue")
                continue
            # 2. il ne rate jamais seul ; il rate quand tous ont réussi
            voulu = 1 if (self.tombe.get(key) and e["en"]) else 0
            if e["rate"] != voulu and e["action"] == ACTION_TIR:
                if _ecriture_permise(mem, b, self.variantes):
                    mem.write(b["base"] + OFF_RATE, struct.pack(">b", voulu))
                    ecrits += 1
                    if voulu:
                        self.tombes += 1
                        self.notes.append("Ganon spectral : tout le monde a tenu l'échange, renvoie-la : il va la "
                                          "prendre !")
            # 3. un Ganon qui suit tire avec la référence, jamais de lui-même
            if not suis_ref and e["action"] == ACTION_VOL:
                tir = self.tir_recu.get(key)
                if tir is not None and now - tir > TIR_AGE:
                    del self.tir_recu[key]
                    tir = None
                if tir is not None and e["mode"] in (0, 1):
                    if _ecriture_permise(mem, b, self.variantes):
                        mem.write(b["base"] + OFF_ACTION, struct.pack(">hh", ACTION_TIR, 0))
                        del self.tir_recu[key]
                        self.tirs_suivis += 1
                        ecrits += 1
                elif 0 <= e["attente"] < ATTENTE_SEUIL and _ecriture_permise(mem, b, self.variantes):
                    mem.write(b["base"] + OFF_ATTENTE_TIR, struct.pack(">h", ATTENTE_TENUE))
                    self.tenues += 1
        return ecrits

    def _dire(self, quoi, texte: str) -> None:
        if quoi not in self.dit:
            self.dit.add(quoi)
            self.notes.append(texte)

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        if not self.vu:
            return "pas de Ganon spectral ici"
        return ", ".join(("" if self.ici.get(k) else "hors de la zone du boss, ")
                         + f"boule {'en jeu' if e['en'] else 'absente'}, {e['n']} renvoi(s), "
                         f"{self.joueurs.get(k, 0)} joueur(s) dans la zone" for k, e in sorted(self.vu.items())) \
            + f" ; {self.tirs_suivis} tir(s) suivi(s), {self.tombes} volée(s) réussie(s), {self.rates} perdue(s)"


# ---------------------------------------------------------------------------
# Serveur
# ---------------------------------------------------------------------------
class VoleeTable:
    """Par (groupe, stage, clé) : la volée de chaque joueur. ``rapport`` rend (état commun, ordre ou None)."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.etat: dict[tuple[str, str, str], dict] = {}

    def rapport(self, group: str, rec: dict, pid: int, presents, now: float | None = None,
                lieu: str | None = None) -> tuple[dict, str | None]:
        """``lieu`` : la salle (« stage:salle ») où est l'auteur. Dans le dédale de la Tour de Ganon, chaque salle a
        son Ganon spectral, tous avec les mêmes paramètres : sans la salle dans la clé, deux joueurs dans deux salles
        auraient été comptés dans la même volée."""
        now = self.clock() if now is None else now
        k = (group, lieu or rec["stage"], rec["key"])
        t = self.etat.setdefault(k, {"joueurs": {}, "ref": pid, "seq": 0, "tombe": False})
        js = t["joueurs"]
        for q in [q for q, v in js.items() if q not in presents or now - v["t"] > FRAIS]:
            del js[q]                               # parti, ou muet : il ne compte plus
        avant = js.get(pid)
        if rec.get("ici") == 0:
            # hors de la zone du boss (ou à terre) : il ne compte pas, et son départ n'est pas une boule manquée
            js.pop(pid, None)
            avant = None
            if t["ref"] not in js and js:
                t["ref"] = min(js)
        else:
            js[pid] = {"n": rec["n"], "en": rec["en"], "t": now}
            if t["ref"] not in js:
                t["ref"] = pid
        ordre = None
        if len(js) >= 2:
            if all(v["en"] for v in js.values()):
                if not t["tombe"] and min(v["n"] for v in js.values()) >= SEUIL:
                    t["tombe"] = True
                    ordre = "tombe"
            elif avant is not None and pid in js and avant["en"] and not rec["en"] and not t["tombe"] \
                    and any(v["en"] for v in js.values()):
                ordre = "rate"                      # sa boule s'est arrêtée avant l'ordre : volée perdue pour tous
        if not any(v["en"] for v in js.values()):
            t["tombe"] = False                      # plus aucune boule : la volée suivante repart de zéro
        if ordre is not None:
            t["seq"] += 1
        return t, ordre

    def message(self, group: str, rec: dict, t: dict, ordre: str | None) -> dict:
        m = {"type": "bossv", "group": group, "stage": rec["stage"], "key": rec["key"], "joueurs": len(t["joueurs"]),
             "ref": t["ref"]}
        if ordre is not None:
            m.update(ordre=ordre, seq=t["seq"])
        return m

    def gc(self, present: set) -> None:
        for k in [k for k in self.etat if (k[0], k[1]) not in present]:
            del self.etat[k]
