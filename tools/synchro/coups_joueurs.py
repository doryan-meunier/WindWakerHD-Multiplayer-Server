"""Coups entre joueurs (Four Swords), lot P1 de docs/etudes/autres_zelda.md §8 : un coup d'ÉPÉE sur un coéquipier le
fait RECULER, sans aucun dégât.

Décisions de l'utilisateur (définitives) : recul SEUL (aucun dégât, Q7.1), ÉPÉE SEULE (Q7.2), option de partie
COUPÉE par défaut ; coupée en mode Hanteurs (le PvP du mode Hanteurs, synchro/potion.py, reste le sien) ; avec la vie
partagée : permis (de toute façon aucun dégât, Q7.3). Invincibilité d'une seconde après un coup. Jamais pendant une
cinématique, jamais sur un Link porté ou porteur (porter.c / porte.c, zones WWMPPOR1 / WWMPPRT1), jamais pendant le
menu pause (pause_link.c), jamais avec le robot.

Chemin d'un coup (aucun C : le mod natif 0.33.0 et suivants suffisent, CALL 9 ``coup_link`` de coups.c) :

1. **attaquant** : la lame de son Link (matrices de l'épée, ``Sampler.detect_hits`` : seule l'épée a ces matrices,
   donc ni flèche, ni bombe, ni boomerang, ni marteau) passe vite à moins de 90 u du centre du fantôme d'un coéquipier
   de la même salle -> ``pvp {to, zone}`` (le même message que le mode Hanteurs : le serveur le route selon le mode) ;
2. **serveur** (``CoupsServeur``) : règle de la partie allumée (``--coups-joueurs``, case du lobby, ``regles_cmd``),
   jamais en mode Hanteurs, jamais avec le robot d'essai, deux joueurs de jeu de la même salle, positions de moins de
   ``ETAT_AGE`` s, aucun en cinématique (``busy``) ni « intouchable » (champ ``nc`` du state : menu, porté, porteur),
   à moins de ``DIST`` u, au plus ``RATE`` coups par seconde et par paire, victime touchée il y a plus de ``INVULN``
   s, victime qui annonce ``FEAT`` -> ``coup_joueur {from, zone, pos, seq}`` à la victime ;
3. **victime** (``CoupsClient.victime_tick``) : jeu libre, pas intouchable, coup frais et pas d'esquive (son Link à
   moins de ``ESQUIVE`` u de l'attaquant), une fois par ``INVULN`` s -> CALL 9 avec ``Atp = 0`` : l'AT de l'épée
   (type 0x2) inscrit pour une image, propriétaire = le fantôme de l'attaquant chez la victime. Le jeu fait la garde
   au bouclier, le recul (procDamage_init, ou setDashDamage en courant), l'invincibilité (mDamageWaitTimer) ; AUCUNE
   vie n'est perdue : changeDamageProc (0x023FA578) passe ``m_dmg`` (= Atp) à setDamagePoint (0x023F51D0), qui ajoute
   ``-0`` à la vie en attente (doublé en mode Héros : toujours 0). Preuve : tests/test_coups_joueurs_rpx.py (les vraies
   fonctions du cking.rpx exécutées sous CpuEtendu). Sans le mod natif (Cemu) : RIEN (on n'écrit jamais la vie).

Essai solo (ESSAI, Réglages › Essais › « Essai : coups entre joueurs », client et serveur ``--essai-coups`` ; à
retirer après validation) : un faux coéquipier (``ID_ESSAI``, « Essai Cogneur ») se tient devant Link, sur ce PC
seulement. (1) Le frapper à l'épée envoie un ``pvp {to: ID_ESSAI, essai: 1}`` que le serveur d'essai juge comme un
vrai (salle, cinématique, intouchable, distance, cadence) et accepte (``coup_rep`` + journal) ; (2) toutes les
``ESSAI_PERIODE`` s, Link assez près, le faux coéquipier « frappe » Link : ``pvp {to: moi, essai: 1, de: [x, y, z]}``,
renvoyé par le serveur d'essai en ``coup_joueur {from: ID_ESSAI}``, appliqué comme un vrai coup (recul, invincibilité).
"""
from __future__ import annotations

import math
import threading
import time

FEAT = "coups"              # hello : ce PC sait recevoir « coup_joueur » (recul par CALL 9)
DIST = 350.0                # u : attaquant et victime plus éloignés (positions du serveur) : refusé
RATE = 2                    # coups au plus par seconde et par paire
INVULN = 1.0                # s : invincibilité après un coup (serveur ET victime)
ETAT_AGE = 3.0              # s : position plus vieille : pas de jugement
FRESH = 1.0                 # s : coup reçu plus vieux -> jeté
ESQUIVE = 450.0             # u : Link de la victime plus loin de l'attaquant -> esquivé (latence comprise)
CENTRE_Y = 60.0             # u : centre du corps au-dessus des pieds (comme potion.CENTRE_Y)
ATP = 0                     # recul seul : AUCUN dégât (Q7.1)
AT_EPEE, SE_EPEE, MARQUE_EPEE = 0x2, 1, 0xD     # AT de la lame de Link (COMBAT §1.2, comme potion.py)
# ESSAI : faux coéquipier (ce PC seulement), qui frappe Link toutes les ESSAI_PERIODE s quand il est assez près
ID_ESSAI = 96               # pas ceux des essais « porter » (98) et « être porté » (97)
NOM_ESSAI = "Essai Cogneur"
ESSAI_DISTANCE = 110.0      # u devant Link, reposé à chaque changement de salle
ESSAI_PERIODE = 6.0
ESSAI_PORTEE = 250.0        # u : Link plus loin du faux coéquipier : il ne frappe pas


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pos3(v) -> tuple[float, float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(_num(x) for x in v):
        return None
    p = tuple(float(x) for x in v)
    return p if all(abs(x) < 1e6 for x in p) else None


def _id(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# =========================================================================================================
# Serveur : règle de la partie, contrôles, relais. Aucune entrée/sortie (testé hors ligne).
# =========================================================================================================
class CoupsServeur:
    """``joueur(pid)`` des appels : dict {joueur, zone, busy, nc, pos, t, feat} (ou None : inconnu)."""

    def __init__(self, on: bool = False, essai: bool = False):
        self.on = bool(on)
        self.vu = self.on                        # allumés une fois dans ce processus : « regles » les annonce
        self.essai = bool(essai)                 # ESSAI : coups vers / depuis le faux coéquipier acceptés
        self.paires: dict[tuple[int, int], list[float]] = {}
        self.touche_t: dict[int, float] = {}     # victime -> heure du dernier coup relayé (invincibilité)
        self.n_acceptes = self.n_refus = self.n_essai = 0
        self.seq = 0

    def oublier(self, pid: int) -> None:
        for k in [k for k in self.paires if pid in k]:
            del self.paires[k]
        self.touche_t.pop(pid, None)

    @staticmethod
    def _libre(j: dict | None, zone: str, now: float) -> str:
        """"" si le joueur ``j`` peut frapper / être frappé dans ``zone`` maintenant, sinon la raison."""
        if j is None or not j.get("joueur"):
            return "pas un joueur de jeu (robot, outil)"
        if j.get("zone") != zone:
            return "pas dans cette salle"
        if j.get("busy"):
            return "en cinématique"
        if j.get("nc"):
            return "intouchable (menu, porté ou porteur)"
        if _pos3(j.get("pos")) is None or j.get("t") is None or now - j["t"] > ETAT_AGE:
            return "position trop vieille"
        return ""

    def _cadence(self, src: int, to: int, now: float) -> str:
        t = [x for x in self.paires.get((src, to), []) if now - x < 1.0]
        if len(t) >= RATE:
            self.paires[(src, to)] = t
            return "trop de coups"
        t.append(now)
        self.paires[(src, to)] = t
        return ""

    def pvp(self, src: int, msg: dict, joueur, now: float, hanteurs: bool = False,
            robot_essai: bool = False) -> tuple[str, int | None, dict | None]:
        """``pvp`` de ``src`` hors mode Hanteurs. Renvoie (raison du refus ou "", destinataire, message) : message
        ``coup_joueur`` pour la victime, ou ``coup_rep`` (essai : le faux coéquipier touché) pour l'attaquant."""
        r, dest, out = self._pvp(src, msg, joueur, now, hanteurs, robot_essai)
        if r:
            self.n_refus += 1
        else:
            self.n_acceptes += 1
        return r, dest, out

    def _pvp(self, src, msg, joueur, now, hanteurs, robot_essai):
        if hanteurs:
            return "mode Hanteurs (son PvP reste le sien)", None, None
        if robot_essai:
            return "jamais avec le robot d'essai", None, None
        essai = self.essai and msg.get("essai") in (1, True)
        if not self.on and not essai:
            return "règle de la partie coupée", None, None
        to, zone = msg.get("to"), msg.get("zone")
        if not _id(to) or not isinstance(zone, str) or not zone or len(zone) > 64:
            return "message invalide", None, None
        es = joueur(src)
        r = self._libre(es, zone, now)
        if r:
            return "attaquant " + r, None, None
        if essai and to == ID_ESSAI:                     # ESSAI (1) : notre Link frappe le faux coéquipier
            de = _pos3(msg.get("de"))
            if de is not None and math.dist(de, es["pos"]) > DIST:
                return "trop loin du faux coéquipier", None, None
            r = self._cadence(src, to, now)
            if r:
                return r, None, None
            self.n_essai += 1
            return "", src, {"type": "coup_rep", "ok": 1, "to": ID_ESSAI, "zone": zone, "essai": 1}
        if essai and to == src:                          # ESSAI (2) : le faux coéquipier frappe notre Link
            de = _pos3(msg.get("de"))
            if de is None or math.dist(de, es["pos"]) > DIST:
                return "faux coéquipier trop loin", None, None
            if FEAT not in (es.get("feat") or ()):
                return "victime sans « coups » (client ancien)", None, None
            if now - self.touche_t.get(src, float("-inf")) < INVULN:
                return "victime invincible (coup de moins d'une seconde)", None, None
            r = self._cadence(ID_ESSAI, src, now)
            if r:
                return r, None, None
            self.touche_t[src] = now
            self.seq += 1
            self.n_essai += 1
            return "", src, {"type": "coup_joueur", "from": ID_ESSAI, "zone": zone,
                             "pos": [round(v, 1) for v in de], "seq": self.seq, "essai": 1}
        if not self.on:
            return "règle de la partie coupée", None, None
        if to == src:
            return "cible invalide", None, None
        ev = joueur(to)
        r = self._libre(ev, zone, now)
        if r:
            return "victime " + r, None, None
        if FEAT not in (ev.get("feat") or ()):
            return "victime sans « coups » (client ancien)", None, None
        if math.dist(_pos3(ev["pos"]), _pos3(es["pos"])) > DIST:
            return "trop loin", None, None
        if now - self.touche_t.get(to, float("-inf")) < INVULN:
            return "victime invincible (coup de moins d'une seconde)", None, None
        r = self._cadence(src, to, now)
        if r:
            return r, None, None
        self.touche_t[to] = now
        self.seq += 1
        return "", to, {"type": "coup_joueur", "from": src, "zone": zone,
                        "pos": [round(float(v), 1) for v in es["pos"]], "seq": self.seq}

    def vue(self) -> dict:
        return {"on": self.on, "essai": self.essai, "acceptes": self.n_acceptes, "refus": self.n_refus}


# =========================================================================================================
# Client : cibles de la lame, coups envoyés, coups reçus (victime), faux coéquipier d'essai.
# =========================================================================================================
def ancre_essai(state: dict, distance: float = ESSAI_DISTANCE) -> dict:
    """Place du faux coéquipier : ``distance`` devant Link (angle y du jeu : 0 = +Z, 0x4000 = +X), tourné vers lui."""
    x, y, z = (float(v) for v in state["pos"][:3])
    rot = int(state.get("rotY") or 0) & 0xFFFF
    a = rot * math.pi / 32768.0
    face = (rot + 0x8000) & 0xFFFF
    return {"cle": (state.get("stage"), state.get("room")),
            "pos": [round(x + distance * math.sin(a), 2), round(y, 2), round(z + distance * math.cos(a), 2)],
            "rotY": face - 0x10000 if face >= 0x8000 else face}


class CoupsClient:
    """Côté client. ``accepte`` : ce PC joue la règle (pas le robot, pas --no-coups-joueurs) ; ``on`` : règle du
    serveur (welcome / regles) ; ``essai`` : ESSAI, faux coéquipier sur ce PC."""

    def __init__(self, accepte: bool = True, essai: bool = False, clock=time.monotonic):
        self.accepte = bool(accepte)
        self.essai = bool(essai) and self.accepte
        self.on = False
        self.clock = clock
        self._lock = threading.Lock()
        self.inbox: list[tuple[float, dict]] = []
        self.sortie: list[dict] = []
        self.vus: list[int] = []
        self.dernier = float("-inf")        # dernier coup appliqué chez nous (invincibilité)
        self.notes: list[str] = []
        self.place: dict | None = None      # ESSAI : {"cle", "pos", "rotY"}
        self.essai_t = None                 # ESSAI : heure du dernier coup du faux coéquipier
        self.n_envoyes = self.n_recus = self.n_appliques = self.n_refuses = self.n_acceptes_essai = 0
        self.refus_vus: set = set()

    # -- règle de la partie ------------------------------------------------------------------------------------------
    def regles(self, msg: dict) -> None:
        """welcome / regles : champ « coups_joueurs » (absent : inchangé, serveur ancien = coupé au départ)."""
        if "coups_joueurs" in msg:
            on = msg.get("coups_joueurs") in (1, True)
            if on != self.on and self.accepte:
                self._note("coups entre joueurs " + ("ALLUMÉS par la partie : un coup d'épée fait reculer un "
                                                     "coéquipier (aucun dégât)" if on else "coupés"))
            self.on = on

    def actif(self) -> bool:
        return self.accepte and (self.on or self.essai)

    def _note(self, s: str) -> None:
        if len(self.notes) < 16:
            self.notes.append("coups entre joueurs : " + s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self._lock:
            out, self.sortie = self.sortie, []
        return out

    # -- ESSAI : le faux coéquipier --------------------------------------------------------------------------------
    def injecter(self, others: dict, local: dict | None, now_wall: float) -> dict:
        """``others`` plus le faux coéquipier (nouveau dict). Rien sans l'essai ni partie chargée."""
        if not self.essai or not local:
            return others
        st = local.get("state") or {}
        if st.get("stage") is None or not st.get("pos"):
            return others
        cle = (st.get("stage"), st.get("room"))
        if self.place is None or self.place["cle"] != cle:
            self.place = ancre_essai(st)
            x, y, z = self.place["pos"]
            self._note(f"ESSAI : faux coéquipier « {NOM_ESSAI} » posé devant Link ({x:.0f}, {y:.0f}, {z:.0f}) ; "
                       f"frappe-le à l'épée (Console : « coup accepté ») ; toutes les {ESSAI_PERIODE:.0f} s il te "
                       f"frappe (recul, 1 s d'invincibilité, aucun dégât)")
        out = dict(others)
        out[ID_ESSAI] = {"stage": st.get("stage"), "room": st.get("room"), "pos": list(self.place["pos"]),
                         "rotY": self.place["rotY"], "_t": now_wall, "essai": True}
        return out

    def essai_frappe(self, now: float, zone, libre: bool, nc: bool, link_pos, me) -> bool:
        """ESSAI (2) : toutes les ESSAI_PERIODE s, Link libre et à moins de ESSAI_PORTEE u, le faux coéquipier frappe
        Link : ``pvp`` vers soi (le serveur d'essai le renvoie en ``coup_joueur``)."""
        if not self.essai or self.place is None or zone is None or not libre or nc or not _id(me):
            return False
        lp = _pos3(link_pos)
        if lp is None or math.dist(lp, tuple(self.place["pos"])) > ESSAI_PORTEE:
            return False
        if self.essai_t is not None and now - self.essai_t < ESSAI_PERIODE:
            return False
        if self.essai_t is None:
            self.essai_t = now                 # premier passage : il attend une période avant de frapper
            return False
        self.essai_t = now
        with self._lock:
            self.sortie.append({"type": "pvp", "to": int(me), "zone": zone, "essai": 1,
                                "de": [round(float(v), 1) for v in self.place["pos"]]})
        return True

    # -- attaquant ---------------------------------------------------------------------------------------------------
    def cibles(self, others: dict, zone, libre: bool, nc: bool, t_now: float, y_off: float = 0.0) -> dict:
        """Fantômes visés par la lame : coéquipiers de la même salle (règle allumée), libres, state frais ; le faux
        coéquipier d'essai. Rien si nous ne sommes pas libres (cinématique, menu, porté, porteur)."""
        if not self.actif() or zone is None or not libre or nc:
            return {}
        out = {}
        for j, o in dict(others).items():
            essai = j == ID_ESSAI and self.essai and o.get("essai")
            if not essai and not self.on:
                continue
            if zone_de(o) != zone or o.get("busy") in (1, True) or o.get("nc") in (1, True) \
                    or t_now - o.get("_t", 0) > 1.0:
                continue
            p = _pos3(o.get("pos"))
            if p is not None:
                out[j] = (p[0], p[1] + y_off + CENTRE_Y, p[2])
        return out

    def touches(self, hits, zone, me) -> int:
        """Coups vus par la lame -> ``pvp`` (dans la file de sortie, envoyée après le state)."""
        n = 0
        for j in hits or ():
            if not _id(j) or zone is None or j == me:
                continue
            m = {"type": "pvp", "to": int(j), "zone": zone}
            if j == ID_ESSAI:
                if not self.essai or self.place is None:
                    continue
                m.update(essai=1, de=[round(float(v), 1) for v in self.place["pos"]])
                self._note(f"ESSAI : coup d'épée sur « {NOM_ESSAI} » envoyé au serveur")
            with self._lock:
                self.sortie.append(m)
            self.n_envoyes += 1
            n += 1
        return n

    def reponse(self, msg: dict) -> None:
        """``coup_rep`` du serveur (essai : faux coéquipier touché, accepté)."""
        if msg.get("ok") in (1, True) and msg.get("to") == ID_ESSAI:
            self.n_acceptes_essai += 1
            self._note(f"ESSAI : coup accepté par le serveur ({self.n_acceptes_essai})")

    # -- victime -----------------------------------------------------------------------------------------------------
    def receive(self, msg: dict) -> bool:
        if not self.accepte:
            return False
        frm, zone, pos, seq = msg.get("from"), msg.get("zone"), _pos3(msg.get("pos")), msg.get("seq")
        if not (_id(frm) and isinstance(zone, str) and pos is not None):
            return False
        if frm == ID_ESSAI and not self.essai:
            return False
        if not self.on and not (self.essai and frm == ID_ESSAI):
            return False
        with self._lock:
            if _id(seq) and seq in self.vus:
                return False
            if _id(seq):
                self.vus = (self.vus + [seq])[-64:]
            self.n_recus += 1
            self.inbox = (self.inbox + [(self.clock(), {"from": frm, "zone": zone, "pos": pos})])[-8:]
        return True

    def victime_tick(self, now: float, zone, libre: bool, nc: bool, link_pos, ghost_pid, inject, noms=None) -> int:
        """``ghost_pid(joueur)`` -> id de process de son fantôme chez nous (ou None) ; ``inject(owner, vec)`` -> code
        de CALL 9 (Atp 0) ou None (pas de mod natif : rien, jamais la vie écrite)."""
        with self._lock:
            box, self.inbox = self.inbox, []
        n = 0
        noms = noms or {}
        for t, c in box:
            qui = NOM_ESSAI if c["from"] == ID_ESSAI else noms.get(c["from"], "?")
            if now - t > FRESH or c["zone"] != zone or not libre or nc:
                self.n_refuses += 1
                continue
            lp = _pos3(link_pos)
            if lp is None or math.dist(lp, c["pos"]) > ESQUIVE:
                self.n_refuses += 1
                self._note(f"coup de {qui} esquivé (tu étais déjà loin)")
                continue
            if now - self.dernier < INVULN:
                self.n_refuses += 1
                continue
            owner = ghost_pid(c["from"]) if ghost_pid is not None else None
            if inject is None or owner is None:
                self.n_refuses += 1
                cle = "natif" if inject is None else "fantome"
                if cle not in self.refus_vus:
                    self.refus_vus.add(cle)
                    self._note(f"coup de {qui} reçu mais pas rejoué : " + (
                        "il faut le portage natif avec le mod (CALL 9)" if inject is None
                        else "son fantôme n'est pas chez toi"))
                continue
            d = (lp[0] - c["pos"][0], 0.0, lp[2] - c["pos"][2])
            nrm = math.hypot(d[0], d[2]) or 1.0
            code = inject(owner, [d[0] / nrm, 0.0, d[2] / nrm])
            if code == 1:
                self.dernier = now
                n += 1
                self.n_appliques += 1
                self._note(f"touché par {qui} : recul (aucun dégât)")
            else:
                self.n_refuses += 1
                if code not in self.refus_vus:
                    self.refus_vus.add(code)
                    self._note(f"CALL 9 refusé ({code}) : pas de recul")
        return n

    def status(self) -> str:
        return (f"coups entre joueurs : {self.n_envoyes} envoyé(s), {self.n_recus} reçu(s), {self.n_appliques} "
                f"appliqué(s)" + (f", essai : {self.n_acceptes_essai} accepté(s)" if self.essai else ""))

    def vue(self) -> dict:
        return {"on": self.on, "essai": self.essai, "envoyes": self.n_envoyes, "recus": self.n_recus,
                "appliques": self.n_appliques, "refuses": self.n_refuses, "acceptes_essai": self.n_acceptes_essai,
                "place": self.place["pos"] if self.place else None}


def zone_de(o: dict) -> str | None:
    st, rm = o.get("stage"), o.get("room")
    if not isinstance(st, str) or not st or not _id(rm):
        return None
    return f"{st}:{rm}"


def intouchable(mem, mb) -> bool:
    """Menu ouvert (dMenu_flag), Link porté (zone WWMPPRT1 armée) ou porteur (zone WWMPPOR1 : un fantôme tenu).
    Lecture seule ; une lecture ratée vaut « libre » pour le menu (le mod rend Link intouchable sous le menu de
    toute façon : pause_link.c) et « libre » pour le port (zones absentes : pas de port possible)."""
    try:
        from jeu import savecopy
        if mem is not None and savecopy.menu_ouvert(mem):
            return True
    except Exception:
        pass
    if mb is None:
        return False
    try:
        if mb.porte_addr() is not None and mb.porte_compteurs().get("arme"):
            return True
    except Exception:
        pass
    try:
        if mb.porter_addr() is not None and mb.porter_compteurs().get("tenu_id"):
            return True
    except Exception:
        pass
    return False
