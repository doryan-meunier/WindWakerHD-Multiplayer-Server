"""Cache-cache des Killer Bees à plusieurs (Mercantîle ; --share-cachecache, --no-cachecache ; PAS activé par
--coop-donjon tant que l'étape de docs/dev/tests/PLAN_TEST_SYNCHRO.md n'est pas passée en jeu). Logique testable sans Cemu.

Le jeu d'origine [HD-ASM, cking.rpx USA v0 relu en lecture seule ; DECOMP zeldaret/tww d_a_npc_mk.cpp, d_a_npc_uk.cpp
non décompilé, relu dans le RPX] :

* Acteurs : Ivan = daNpc_Mk_c (proc 170 ; « Mk » chef qui se promène, « Mk2 » type mini-jeu, perché sur sa cachette),
  ses trois amis = daNpc_Uk_c (proc 368 ; « UkB/UkC/UkD » qui se promènent, « UkB2/UkC2/UkD2 » cachés ; type = bits
  16-19 des paramètres, 0/1/2 = B/C/D [HD-ASM getStaffName 0x022F3360]).
* Lancement : Ivan, choix « oui » au message 0x26CA (talk01 0x02298348) : événement MK_GAMESTART, onTmpBit(0x0040)
  (0x022983D0), onEventBit(0x2201). Le choix n'existe qu'avec 0x1380 posé et 0x1340 (prix déjà reçu) pas posé : après
  le prix, Ivan ne propose plus le jeu (une seule fois par sauvegarde).
* État : flags TEMPORAIRES mTmp (gi+0x1178, dSv_event_c, jamais sauvegardés : remis à zéro au chargement d'une
  sauvegarde, dSv_info_c::init), codés 0xOOMM (octet OO, masque MM) comme mEvent [HD-ASM onEventBit 0x025B8B68] :
    octet 0 : 0x40 partie en cours ; 0x20 Ivan attrapé, 0x10 / 0x08 / 0x04 enfant B / C / D attrapé
              [getCaughtFlag 0x022F1068] ; 0x02 Ivan tombé de son perchoir (trouvé), 0x01 enfant B trouvé ;
    octet 1 : 0x80 / 0x40 enfant C / D trouvé [getFoundFlag 0x022F26B8] ; 0x20 partie finie (posé par GAMESET).
  « Trouvé » = l'enfant repéré s'enfuit (événement UkX_FOUND, ou MK_DROP quand Link fait une roulade contre le
  perchoir d'Ivan) ; « attrapé » = rattrapé, puis la parole : getMsg pose le bit (Ivan 0x022973DC, enfants
  0x022F2A88). Aucun compteur ni minuteur : le jeu ne compte que ces quatre bits.
* État de chaque acteur : lu dans ces bits À SA CRÉATION seulement (init : partie en cours et pas attrapé -> caché
  qui attend, sinon promeneur ; chkGameStart 0x022F10B8 = 0x40 ET pas attrapé). Pendant la partie, l'acteur caché
  (hind_action) ne relit que chkGameStart à chaque image (0x022F5EE8) ; l'acteur qui fuit (seek_action 0x022F6360)
  ne relit rien.
* Fin : après la parole d'un enfant attrapé, talk02 (Ivan 0x02298484, enfants 0x022F325C) appelle
  daNpc_Mk_Static_c::chkGameSet (0x0229B54C : les quatre bits « attrapé », ses DEUX seuls appelants) ; vrai ->
  événement MK_GAMESET : l'action MAKEITEM crée le prix (0x02298C14 bl createItemForPresentDemo, quart de cœur 0x07
  d'origine ; c'est le lieu « Windfall Island - Ivan Catch Killer Bees » du randomizer) et pose 0x1340 (0x02298C38) ;
  l'action GAMESET ôte 0x0040 et pose 0x0120 (0x02298C50 / 0x02298C60). Faux -> l'enfant retourne trépigner, on
  peut lui reparler. Un enfant attrapé reste là ; caché ou promeneur, les autres ne se montrent qu'à leur création.
* Quitter l'île : rien ne change (mTmp survit aux changements de stage) : la partie reprend au retour. Minuit
  (dKankyo_DayProc 0x025542BC, appelée par setDaytime 0x0255B520 et par le saut d'heure 0x025607B0) ôte TOUS ces
  bits (0x025544C4-0x0255457C) : partie annulée. Recharger la sauvegarde aussi.

Synchro (ce module) : le serveur garde UNE partie par groupe, celle du LANCEUR (le premier qui l'annonce) :
  « cc » {ev: lance | attrape | fin | etat} du client ; {etat: partie | fin | aucune} du serveur, à tout le groupe.
* Lanceur : son jeu est la vraie partie. Bits « attrapé » reçus des autres : posés dans son octet 0 (écriture d'un
  octet relu juste avant, jeu libre). Effet prouvé : chkGameSet les compte ; l'acteur déjà chargé de cet enfant
  continue sa vie (s'il le rattrape quand même, l'enfant dit « déjà attrapé » et talk02 refait le compte, sans
  double) ; à sa prochaine création il ne se cache plus (et le promeneur d'Ivan attrapé l'attend chez lui). Les
  quatre attrapés : parler à Ivan (ou à un enfant attrapé) lance MK_GAMESET chez le lanceur : le PRIX est donné une
  seule fois, chez lui. Puis 0x1340 (octet 0x13 de mEvent, partagé par la progression) arrive chez les autres :
  personne ne peut relancer le jeu. Règle des cadeaux du mod (synchro/cadeaux.py) : seuls les quarts de cœur AU SOL
  sont donnés à tous ; un prix de PNJ reste à celui qui le reçoit (la vie max reste à chacun ; un objet
  d'inventaire placé là par le randomizer, lui, passe par l'inventaire partagé).
* Ceux qui aident : la même partie est « lancée » dans leur jeu (0x40 + bits attrapés de la partie) seulement
  quand AUCUN acteur 170/368 n'est chargé (hors de Mercantîle, ou dans une maison ; pas dans la salle 11 de la mer)
  : à leur création, les enfants s'y cachent aux mêmes places que chez le lanceur (placements du fichier de stage),
  sans doublon avec les promeneurs. Ils les trouvent et les attrapent avec le jeu d'origine ; chaque bit « attrapé »
  qui apparaît chez eux part au serveur, qui l'ajoute à la partie. Jamais de prix chez eux : le pack (v19, bit 23,
  zone wwmp_cache) fait répondre « pas fini » à chkGameSet tant que le PC tient le drapeau (posé AVANT toute
  écriture, sans échéance). Fin de la partie (prix, minuit, déconnexion ou rechargement du lanceur) : leurs bits
  sont ôtés (octet 0 & 0x80, octet 1 & 0x1F, comme DayProc), toujours sans acteur chargé, puis le drapeau retiré.
* Sans l'option : rien n'est lu ni écrit, aucun message.
"""
from __future__ import annotations

import struct
import threading
import time

from jeu import progress as prog

# --- adresses et bits [HD-ASM] -----------------------------------------------------------------------------
GI_TMP = 0x1178             # dSv_info_c::mTmp (dSv_event_c, 0x100 octets) depuis gameInfo
EN_COURS = 0x40             # octet 0 : partie en cours (onTmpBit 0x0040 à 0x022983D0)
ATTRAPE_IVAN = 0x20         # octet 0
ATTRAPE_B, ATTRAPE_C, ATTRAPE_D = 0x10, 0x08, 0x04      # octet 0 (getCaughtFlag 0x022F1068 : type 0, 1, 2)
ATTRAPES = ATTRAPE_IVAN | ATTRAPE_B | ATTRAPE_C | ATTRAPE_D   # 0x3C : ce que chkGameSet compte
TROUVE_IVAN, TROUVE_B = 0x02, 0x01                      # octet 0
TROUVE_C, TROUVE_D, FINI = 0x80, 0x40, 0x20             # octet 1 (getFoundFlag 0x022F26B8 ; GAMESET 0x02298C60)
OCTET0_JEU = 0x7F           # ce que dKankyo_DayProc ôte dans l'octet 0
OCTET1_JEU = TROUVE_C | TROUVE_D | FINI                  # et dans l'octet 1 (0x0104 n'est pas à ce jeu)
EV_PRIX = 0x1340            # mEvent : prix d'Ivan reçu (0x02298C30-0x02298C38)
EV_LANCE = 0x2201           # mEvent : partie déjà lancée une fois (0x022983D8)
PROC_IVAN, PROC_ENFANTS = 170, 368
PROCS = frozenset({PROC_IVAN, PROC_ENFANTS})
CHKGAMESET = 0x0229B54C     # accroche du pack (bit 23)
MER, SALLE_MERCANTILE = "sea", 11

NOMS = {ATTRAPE_IVAN: "Ivan", ATTRAPE_B: "l'enfant B", ATTRAPE_C: "l'enfant C", ATTRAPE_D: "l'enfant D"}
GROUP_MAX = 32
EVS = ("etat", "lance", "attrape", "fin")
RAISONS = ("gagne", "abandon")


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _group(g) -> str:
    return str(g or "default")[:GROUP_MAX]


def nombre(bits: int) -> int:
    return bin(bits & ATTRAPES).count("1")


def noms(bits: int) -> str:
    l = [NOMS[b] for b in (ATTRAPE_IVAN, ATTRAPE_B, ATTRAPE_C, ATTRAPE_D) if bits & b]
    if not l:
        return "personne"
    return l[0] if len(l) == 1 else ", ".join(l[:-1]) + " et " + l[-1]


# ---------------------------------------------------------------------------------------------------------
# Lecture / écriture (mTmp)
# ---------------------------------------------------------------------------------------------------------
def lire(mem) -> dict | None:
    """{gi, b0, b1, prix} lus en lecture seule ; None : pas de sauvegarde chargée (écran titre, chargement)."""
    ptr = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if ptr is None or len(ptr) < 4:
        return None
    gi, = struct.unpack(">I", ptr)
    if not 0x10000000 <= gi < 0x50000000:
        return None
    raw = mem.try_read(gi + GI_TMP, 2)
    ev = mem.try_read(gi + prog.GI_EVENT + (EV_PRIX >> 8), 1)
    if raw is None or ev is None or len(raw) < 2 or len(ev) < 1:
        return None
    return {"gi": gi, "b0": raw[0], "b1": raw[1], "prix": bool(ev[0] & (EV_PRIX & 0xFF))}


def ecrire_octet(mem, gi: int, octet: int, poser: int = 0, oter: int = 0) -> int | None:
    """Octet ``octet`` de mTmp relu JUSTE avant, bits posés / ôtés, écrit seul (un octet), relu. Rend la valeur relue
    (None : illisible). Verrou des écritures octet par octet du client (progress._write_lock)."""
    if octet not in (0, 1):
        raise ValueError(octet)
    a = gi + GI_TMP + octet
    with prog._write_lock:
        raw = mem.try_read(a, 1)
        if raw is None or len(raw) < 1:
            return None
        v = raw[0]
        nv = (v | poser) & ~oter & 0xFF
        if nv != v:
            mem.write(a, bytes([nv]))
        raw = mem.try_read(a, 1)
    return None if raw is None else raw[0]


# ---------------------------------------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------------------------------------
def valid_cc(msg) -> dict | None:
    """« cc » du client {group, ev, bits?, gen?, raison?} : None si invalide."""
    if not isinstance(msg, dict) or msg.get("ev") not in EVS:
        return None
    ev = msg["ev"]
    out = {"group": _group(msg.get("group")), "ev": ev}
    if ev in ("lance", "attrape"):
        b = msg.get("bits", 0)
        if not _int(b) or not 0 <= b <= 0xFF:
            return None
        out["bits"] = b & ATTRAPES
    if ev in ("attrape", "fin"):
        g = msg.get("gen")
        if not _int(g) or g < 1:
            return None
        out["gen"] = g
    if ev == "fin":
        if msg.get("raison") not in RAISONS:
            return None
        out["raison"] = msg["raison"]
    if ev == "attrape" and not out["bits"]:
        return None
    return out


# ---------------------------------------------------------------------------------------------------------
# Serveur : une partie par groupe, celle du lanceur
# ---------------------------------------------------------------------------------------------------------
class CacheCacheServeur:
    RATE = 8                    # messages « cc » par seconde et par joueur, au plus

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.parties: dict[str, dict] = {}       # groupe -> {gen, lanceur, nom, bits, par}
        self.groupes: dict[int, str] = {}        # joueur -> groupe (inscrit par son premier « cc »)
        self.gen = 0
        self.vus: dict[int, list[float]] = {}

    def membres(self, groupe: str) -> list[int]:
        return sorted(pid for pid, g in self.groupes.items() if g == groupe)

    @staticmethod
    def vue(p: dict, **extra) -> dict:
        return dict({"type": "cc", "etat": "partie", "gen": p["gen"], "lanceur": p["lanceur"], "nom": p["nom"],
                     "bits": p["bits"], "par": {str(b): n for b, n in sorted(p["par"].items())}}, **extra)

    def message(self, pid: int, nom: str, msg: dict) -> list[tuple[list[int], dict]]:
        """(destinataires, message) à envoyer ; [] si rien."""
        v = valid_cc(msg)
        if v is None:
            return []
        now = self.clock()
        l = [t for t in self.vus.get(pid, []) if now - t < 1.0]
        if len(l) >= self.RATE:
            self.vus[pid] = l
            return []
        self.vus[pid] = l + [now]
        g = v["group"]
        self.groupes[pid] = g
        p = self.parties.get(g)
        ev = v["ev"]
        if ev == "etat":
            return [([pid], self.vue(p) if p else {"type": "cc", "etat": "aucune"})]
        if ev == "lance":
            if p is None:
                self.gen += 1
                p = self.parties[g] = {"gen": self.gen, "lanceur": pid, "nom": str(nom)[:32], "bits": v["bits"],
                                       "par": {b: str(nom)[:32] for b in NOMS if v["bits"] & b}}
                return [(self.membres(g), self.vue(p, nouveau=v["bits"], qui=p["nom"]))]
            if p["lanceur"] == pid:
                return self._ajout(g, p, v["bits"], nom) or [([pid], self.vue(p))]
            return [([pid], self.vue(p))]          # une partie est déjà en cours : il aide
        if p is None or v["gen"] != p["gen"]:
            return [([pid], self.vue(p) if p else {"type": "cc", "etat": "aucune"})]
        if ev == "attrape":
            return self._ajout(g, p, v["bits"], nom)
        # fin : seulement le lanceur
        if pid != p["lanceur"]:
            return []
        del self.parties[g]
        return [(self.membres(g), {"type": "cc", "etat": "fin", "gen": p["gen"], "raison": v["raison"],
                                   "nom": p["nom"], "bits": p["bits"]})]

    def _ajout(self, g: str, p: dict, bits: int, nom: str) -> list:
        neufs = bits & ATTRAPES & ~p["bits"]
        if not neufs:
            return []
        p["bits"] |= neufs
        for b in NOMS:
            if neufs & b:
                p["par"][b] = str(nom)[:32]
        return [(self.membres(g), self.vue(p, nouveau=neufs, qui=str(nom)[:32]))]

    def depart(self, pid: int) -> list[tuple[list[int], dict]]:
        """Joueur parti : sa partie (s'il l'a lancée) s'arrête pour les autres."""
        self.vus.pop(pid, None)
        self.groupes.pop(pid, None)
        out = []
        for grp, p in list(self.parties.items()):
            if p["lanceur"] == pid:
                del self.parties[grp]
                out.append((self.membres(grp), {"type": "cc", "etat": "fin", "gen": p["gen"], "raison": "deco",
                                                "nom": p["nom"], "bits": p["bits"]}))
        return out


# ---------------------------------------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------------------------------------
class CacheCacheSync:
    LANCE_GAP = 2.0             # s entre deux annonces « lance » sans réponse
    GARDE_REDIT = 5.0           # s : drapeau du pack réécrit (pack rechargé, autre client)

    def __init__(self, enabled: bool = False, group: str = "default", clock=time.monotonic):
        self.enabled = bool(enabled)
        self.group = _group(group)
        self.clock = clock
        self.lock = threading.Lock()
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.partie: dict | None = None          # vue du serveur (etat « partie »)
        self.connecte = False
        self.me: int | None = None
        # notre jeu
        self.gi: int | None = None
        self.prev_run: bool | None = None
        self.local_gen: int | None = None        # partie que représentent nos bits de mTmp (None : aucune / inconnue)
        self.a_nettoyer = False                  # nos bits viennent de la partie d'un autre : à ôter à la fin
        self.perdu_gen: int | None = None        # partie annulée chez nous par le jeu (minuit) : pas réinjectée
        self.envoyes = 0                         # bits « attrapé » déjà annoncés pour local_gen
        self.lance_t = float("-inf")
        self.garde_pose: bool | None = None
        self.garde_t = float("-inf")
        self.herite_vu = False                   # drapeau du pack laissé par un client précédent regardé
        self.dits: set = set()                   # notes « une fois »
        self.finis: set = set()                  # parties finies (relais en retard ignorés ; oubliées à la connexion)
        self.n = {"lance": 0, "attrape": 0, "fin": 0, "poses": 0, "lances_chez_moi": 0, "nettoyages": 0}

    # --- files ------------------------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def _une_fois(self, cle, s: str) -> None:
        if cle not in self.dits:
            self.dits.add(cle)
            self._note(s)

    def take_notes(self) -> list[str]:
        with self.lock:
            n, self.notes = self.notes, []
        return n

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            o, self.out = self.out, []
        return o

    def _send(self, msg: dict) -> None:
        self.out.append(dict(msg, type="cc", group=self.group))

    # --- réseau -----------------------------------------------------------------------------------------
    def on_connect(self) -> None:
        if not self.enabled:
            return
        with self.lock:
            self.connecte = True
            self.lance_t = float("-inf")
            self.finis.clear()                   # serveur peut-être redémarré : ses numéros repartent de 1
            self._send({"ev": "etat"})

    def on_disconnect(self) -> None:
        with self.lock:
            self.connecte = False
            self.me = None
            self.envoyes = 0                     # prises renvoyées à la reconnexion (le serveur ne compte qu'une fois)
            if self.partie is not None and self.enabled:
                self._note("cache-cache : connexion perdue, la partie à plusieurs s'arrête ici")
            self.partie = None

    def on_message(self, msg: dict, me: int | None) -> None:
        if not self.enabled or not isinstance(msg, dict):
            return
        with self.lock:
            self.me = me
            e = msg.get("etat")
            if e == "partie":
                gen, lanceur, bits = msg.get("gen"), msg.get("lanceur"), msg.get("bits")
                if not (_int(gen) and gen >= 1 and _int(lanceur) and _int(bits)) or gen in self.finis:
                    return
                nom = str(msg.get("nom") or "?")[:32]
                neuf = self.partie is None or self.partie["gen"] != gen
                self.partie = {"gen": gen, "lanceur": lanceur, "nom": nom, "bits": bits & ATTRAPES}
                moi = me is not None and lanceur == me
                if neuf:
                    if moi:
                        self._note("cache-cache : ta partie des Killer Bees est annoncée, les autres peuvent "
                                   "chercher les enfants avec toi ; chaque enfant attrapé compte chez toi")
                    else:
                        self._note(f"cache-cache : {nom} a lancé le cache-cache des Killer Bees à Mercantîle : "
                                   "cherche les enfants avec lui ! (c'est lui qui recevra le prix)")
                nv, qui = msg.get("nouveau"), msg.get("qui")
                if _int(nv) and nv & ATTRAPES and not neuf:
                    qui = str(qui or "?")[:32]
                    self._note(f"cache-cache : {qui} a attrapé {noms(nv)} ({nombre(self.partie['bits'])}/4)")
                if nombre(self.partie["bits"]) == 4:
                    if moi:
                        self._une_fois(("fin_dispo", gen), "cache-cache : les quatre sont attrapés ! Parle à Ivan "
                                       "(ou à un enfant attrapé) pour finir ; s'il n'y a personne, entre dans une "
                                       "maison et ressors")
                    else:
                        self._une_fois(("fin_dispo", gen), f"cache-cache : les quatre sont attrapés ; {nom} doit "
                                       "parler à Ivan pour recevoir le prix")
            elif e == "fin":
                gen = msg.get("gen")
                if _int(gen):
                    self.finis.add(gen)
                if self.partie is not None and gen == self.partie["gen"]:
                    nom = self.partie["nom"]
                    r = msg.get("raison")
                    if r == "gagne":
                        self._note(f"cache-cache : {nom} a fini la partie et reçu le prix d'Ivan")
                    elif r == "deco":
                        self._note(f"cache-cache : {nom} s'est déconnecté, la partie à plusieurs s'arrête")
                    else:
                        self._note(f"cache-cache : la partie de {nom} est annulée (minuit passé ou partie rechargée)")
                    self.partie = None
            elif e == "aucune":
                self.partie = None

    # --- tick (thread de travail) -------------------------------------------------------------------------
    def role(self, me=None) -> str:
        me = self.me if me is None else me
        p = self.partie
        if p is None:
            return ""
        return "lanceur" if me is not None and p["lanceur"] == me else "aide"

    def step(self, mem, mb, now: float | None = None, me: int | None = None, stage: str | None = None,
             room: int = -1, kids: int | None = None, free: bool = False, can: bool = False) -> None:
        """Une passe : lecture de mTmp, annonces, drapeau du pack, écritures. ``kids`` : nombre d'acteurs 170/368
        chargés selon un relevé récent (None : inconnu -> ni lancement chez nous ni nettoyage)."""
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        with self.lock:
            if me is not None:
                self.me = me
            self._step(mem, mb, now, stage, room, kids, free, can)

    def _step(self, mem, mb, now, stage, room, kids, free, can) -> None:
        if not self.herite_vu and mb is not None and getattr(mb, "cache", False):
            # drapeau laissé posé par un client précédent arrêté pendant une partie aidée : ses bits sont peut-être
            # encore dans le jeu ; on les ôte (quand c'est sûr) AVANT de retirer le drapeau
            self.herite_vu = True
            try:
                cfg = int(mb.cache_counters().get("cfg", 0)) if hasattr(mb, "cache_counters") else 0
            except Exception:
                cfg = 0
            if cfg & 1 and not self.a_nettoyer:
                self.a_nettoyer, self.local_gen = True, None
                self._note("cache-cache : fin bloquée laissée par un client précédent : les enfants cachés d'une "
                           "partie aidée seront rendus au prochain passage dans une maison")
        cur = lire(mem) if mem is not None else None
        p = self.partie
        role = self.role()
        if cur is None:
            self._garde(mb, role == "aide" or self.a_nettoyer, now)
            return
        if self.gi is not None and cur["gi"] != self.gi:
            # sauvegarde rechargée : mTmp remis à zéro par le jeu (dSv_info_c::init)
            if role == "lanceur" and p is not None:
                self._fin(p["gen"], "abandon")
                self.partie, p, role = None, None, ""
            self.prev_run, self.local_gen, self.a_nettoyer, self.envoyes, self.perdu_gen = None, None, False, 0, None
        self.gi = cur["gi"]
        b0, b1 = cur["b0"], cur["b1"]
        run = bool(b0 & EN_COURS)
        hors_ile = kids == 0 and not (stage == MER and room == SALLE_MERCANTILE)
        ecrire = bool(can and free)

        # 1. lanceur
        if role == "lanceur":
            if self.local_gen != p["gen"]:
                self.local_gen, self.envoyes = p["gen"], b0 & ATTRAPES & p["bits"]
            if not run:
                # fin chez nous (MK_GAMESET : 0x40 ôté, 0x0120 posé, prix), minuit, ou partie déjà finie à la
                # reconnexion : la partie du serveur s'arrête
                self._fin(p["gen"], "gagne" if (b1 & FINI or cur["prix"]) else "abandon")
                self.partie, role, p = None, "", None
            elif run and ecrire:
                manque = p["bits"] & ATTRAPES & ~b0
                if manque:
                    v = ecrire_octet(mem, self.gi, 0, poser=manque)
                    if v is not None:
                        self.n["poses"] += 1
                        b0 = v
        elif role == "" and run and not self.a_nettoyer and self.connecte and now - self.lance_t >= self.LANCE_GAP:
            self.lance_t = now
            self.n["lance"] += 1
            self._send({"ev": "lance", "bits": b0 & ATTRAPES})

        # 2. aide
        garde = self._garde(mb, role == "aide" or self.a_nettoyer, now)
        if role == "aide":
            if self.local_gen != p["gen"]:
                if run and not self.a_nettoyer:
                    # partie lancée chez nous aussi (second lanceur) : elle devient la sienne, nettoyée à la fin
                    self.local_gen, self.a_nettoyer, self.envoyes = p["gen"], True, 0
                    self._une_fois(("second", p["gen"]), f"cache-cache : {p['nom']} avait lancé sa partie avant "
                                   "toi : vous jouez la sienne (tes enfants attrapés comptent, le prix sera chez lui)")
            if not garde:
                if mb is None or not getattr(mb, "cache", False):
                    self._une_fois(("pack", p["gen"]), "cache-cache : pack WWMP_Mailbox sans le bit 23 : tu vois la "
                                   "partie mais tu ne peux pas chercher (réinstalle le pack v19 du dépôt)")
            elif self.local_gen == p["gen"]:
                if run:
                    manque = p["bits"] & ATTRAPES & ~b0
                    if manque and ecrire:
                        v = ecrire_octet(mem, self.gi, 0, poser=manque)
                        if v is not None:
                            self.n["poses"] += 1
                            b0 = v
                elif self.perdu_gen != p["gen"]:
                    self.perdu_gen = p["gen"]
                    self._note("cache-cache : la partie s'est arrêtée dans ton jeu (minuit ?) ; plus rien à chercher")
            elif self.perdu_gen != p["gen"]:
                restes = (b0 & OCTET0_JEU) or (b1 & OCTET1_JEU)
                if ecrire and hors_ile:
                    if restes:
                        self._nettoyer(mem)              # restes d'une autre partie : ôtés d'abord
                    else:
                        v = ecrire_octet(mem, self.gi, 0, poser=EN_COURS | (p["bits"] & ATTRAPES))
                        if v is not None and v & EN_COURS:
                            b0, run = v, True
                            self.local_gen, self.a_nettoyer, self.envoyes = p["gen"], True, v & ATTRAPES
                            self.n["lances_chez_moi"] += 1
                            self._note(f"cache-cache : la partie de {p['nom']} est lancée dans ton jeu : les enfants "
                                       f"se cachent à Mercantîle ({nombre(p['bits'])}/4 déjà attrapés)")
                elif kids:
                    self._une_fois(("entre", p["gen"]), "cache-cache : pour chercher aussi, entre dans une maison de "
                                   "Mercantîle et ressors (les enfants se cachent à leur apparition)")

        # 3. nos prises
        if p is not None and self.local_gen == p["gen"] and run:
            neufs = b0 & ATTRAPES & ~p["bits"] & ~self.envoyes
            if neufs:
                self.envoyes |= neufs
                self.n["attrape"] += 1
                self._send({"ev": "attrape", "gen": p["gen"], "bits": neufs})
                self._note(f"cache-cache : tu as attrapé {noms(neufs)}")

        # 4. fin de la partie d'un autre : nos bits ôtés (sans acteur chargé), puis le drapeau retiré
        if role != "aide" and self.a_nettoyer:
            if not ((b0 & OCTET0_JEU) or (b1 & OCTET1_JEU)):
                self._propre()
            elif ecrire and hors_ile:
                self._nettoyer(mem)
            elif kids:
                self._une_fois(("menage", self.local_gen), "cache-cache : partie finie ; les enfants cachés chez toi "
                               "partiront quand tu entreras dans une maison")
        self.prev_run = run
        self._garde(mb, self.role() == "aide" or self.a_nettoyer, now)   # retiré dès la passe du nettoyage

    def _nettoyer(self, mem) -> None:
        v0 = ecrire_octet(mem, self.gi, 0, oter=OCTET0_JEU)
        v1 = ecrire_octet(mem, self.gi, 1, oter=OCTET1_JEU)
        if v0 is not None and v1 is not None and not (v0 & OCTET0_JEU) and not (v1 & OCTET1_JEU):
            self.n["nettoyages"] += 1
            self._propre()

    def _propre(self) -> None:
        self.a_nettoyer, self.local_gen, self.envoyes = False, None, 0

    def _fin(self, gen: int, raison: str) -> None:
        self.n["fin"] += 1
        self.finis.add(gen)
        self._send({"ev": "fin", "gen": gen, "raison": raison})
        self._note("cache-cache : tu as fini la partie, le prix est à toi" if raison == "gagne"
                   else "cache-cache : ta partie est annulée (minuit passé ou partie rechargée)")

    def _garde(self, mb, want: bool, now: float) -> bool:
        """Drapeau du pack (bit 23) : posé tant qu'on aide ou qu'il reste des bits à ôter. Vrai si posé."""
        if mb is None or not getattr(mb, "cache", False):
            return False
        if want != self.garde_pose or now - self.garde_t >= self.GARDE_REDIT:
            try:
                ok = mb.set_cache(want)
            except Exception:
                ok = False
            if not ok:
                self.garde_pose = None
                return False
            self.garde_pose, self.garde_t = want, now
        return bool(want and self.garde_pose)

    def fin(self, mem, mb, kids: int | None = None, stage: str | None = None, room: int = -1,
            free: bool = False, can: bool = False) -> None:
        """Arrêt du client : bits d'une partie d'un autre ôtés si c'est sûr maintenant, puis drapeau retiré ; sinon
        le drapeau RESTE (jamais de prix chez qui aidait, même client arrêté)."""
        if not self.enabled:
            return
        with self.lock:
            self.partie = None
            cur = lire(mem) if mem is not None else None
            if cur is not None and self.a_nettoyer and (cur["gi"] == self.gi):
                if not ((cur["b0"] & OCTET0_JEU) or (cur["b1"] & OCTET1_JEU)):
                    self._propre()
                elif can and free and kids == 0 and not (stage == MER and room == SALLE_MERCANTILE):
                    self._nettoyer(mem)
            elif cur is not None and cur["gi"] != self.gi:
                self._propre()
            if self.a_nettoyer:
                self._note("cache-cache : client arrêté pendant une partie aidée : la fin reste bloquée dans ton jeu "
                           "jusqu'au prochain client (ou au redémarrage du jeu)")
                return
            if mb is not None and getattr(mb, "cache", False):
                try:
                    mb.set_cache(False)
                except Exception:
                    pass
            self.garde_pose = False

    def status(self) -> str:
        p = self.partie
        if p is None:
            s = "aucune partie"
        else:
            s = f"partie de {p['nom']} ({self.role() or '?'}), {nombre(p['bits'])}/4 attrapés"
        if self.a_nettoyer:
            s += ", bits à ôter"
        if self.garde_pose:
            s += ", fin bloquée chez toi"
        return s
