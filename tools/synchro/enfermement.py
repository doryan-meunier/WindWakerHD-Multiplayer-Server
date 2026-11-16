"""Mode Hanteurs : l'ENFERMEMENT (« bloquer une zone et enfermer les gens dedans pour les obliger à affronter les mobs des
Hanteurs »). Étude : docs/etudes/mode_hanteurs.md §13. Mod natif 0.35.0 : natif/mod_wwmp/enfermement.c (CALL 21, zone
WWMPENF1 sans bit de capacité), jeu/mailbox.py ``enfermer`` / ``liberer_enf`` / ``enf_counters``.

Ce que les joueurs voient. Un Hanteur appuie sur Fin (ou le bouton « Enfermer » du lanceur) : un cercle (900 u par
défaut) se ferme autour du Link le plus proche de lui, dans sa salle ; tous les Link de cette salle qui sont dans le
cercle sont enfermés. Chez chacun, un cercle de volutes d'esprit apparaît et le son des barreaux de porte du jeu joue ;
il bute ensuite contre un mur invisible au bord du cercle (volutes et son à chaque arrivée contre la barrière). Le
Hanteur peut invoquer ses monstres dans le cercle. Le verrou tombe à la fin de la durée (35 s par défaut) OU dès que
les monstres des Hanteurs entrés dans le cercle sont tous battus (réglage), et la recharge (120 s) commence.

Garde-fous (le verrou se défait toujours) :

* serveur autoritaire : lui seul décide qui est enfermé, où et jusqu'à quand ; il annonce le début (``henf``) et la fin
  (``henf_fin``) ; un ancien serveur ignore ``henfermer`` (rien ne se passe), un ancien client ignore ``henf`` ;
* jamais pendant une cinématique (refusé si le Hanteur, le Link visé ou l'autorité de la salle y est ; le mod ne pousse
  rien pendant un événement du jeu), jamais en bateau, jamais pour un Link possédé ni à 0 cœur, jamais dans une stage
  exclue des invocations ; un seul enfermement par salle ;
* le mod ne touche que la position de Link : ni porte, ni switch, ni drapeau, ni sauvegarde. Rien ne peut rester
  fermé ;
* fin : durée (même sans nouvelle du serveur, le client lâche à la durée annoncée + 2 s), Hanteur parti ou redevenu
  Link, Link sorti de la salle, mort, possédé ou déconnecté (retiré), plus aucun Link enfermé ; côté jeu, le mod lâche
  seul si le client ne l'entretient plus (10 s au plus, 1,5 s en pratique), si Link change de salle ou d'étape, meurt,
  réapparaît loin ou n'est plus le joueur.

Réglages (lobby, config Hanteurs) : ``enfermement`` (activé), ``enf_duree`` (s), ``enf_recharge`` (s), ``enf_rayon``
(u), ``enf_fin_monstres`` (fin anticipée quand les monstres sont battus).
"""
from __future__ import annotations

import math

# --- réglages ---------------------------------------------------------------------------------------------------------
DUREE, DUREE_MIN, DUREE_MAX = 35, 10, 90            # s
RECHARGE, RECHARGE_MIN, RECHARGE_MAX = 120, 30, 600  # s
RAYON, RAYON_MIN, RAYON_MAX = 900, 400, 3000         # u (le mod accepte 100..5000)
DEFAUT = {"enfermement": True, "enf_duree": DUREE, "enf_recharge": RECHARGE, "enf_rayon": RAYON,
          "enf_fin_monstres": True}

# --- serveur ----------------------------------------------------------------------------------------------------------
PORTEE = 2500.0          # u : le Link visé est à moins de ça du Hanteur (son Link caché le suit)
ETAT_MAX = 3.0           # s : position d'un joueur plus vieille : inconnue
MARGE_MONSTRES = 300.0   # u : un monstre invoqué à moins de rayon + ça du centre compte pour l'enfermement
DEMANDES_PAR_S = 1       # henfermer acceptés au plus par seconde et par Hanteur
FEAT = "enfermement"     # fonction annoncée par le client (hello) : seul un tel Link est enfermé
STAGE_MER = "sea"        # en mer, la salle suit le secteur : salle -1 côté mod

# --- client -----------------------------------------------------------------------------------------------------------
VIE = 45                 # images armées d'avance chez le mod (1,5 s)
PERIODE = 0.5            # s entre deux entretiens
MARGE_FIN = 2.0          # s : sans fin reçue, le client lâche à la durée annoncée + ça
REFUS_DEFINITIFS = {-5: "tu n'es plus dans la salle", -6: "tu étais déjà hors du cercle",
                    -7: "changement d'étape", -8: "ton Link n'est pas là", -10: "ton Link est à terre",
                    -1: "refusé par le mod (joueur robot ?)", -4: "centre refusé par le mod"}


def valider(d: dict, c: dict, entier) -> None:
    """Réglages de l'enfermement de ``d`` (configuration reçue) nettoyés dans ``c`` (``entier`` : hanteurs._entier)."""
    c["enfermement"] = d.get("enfermement") not in (0, False)
    c["enf_duree"] = entier(d.get("enf_duree"), DUREE_MIN, DUREE_MAX, c["enf_duree"])
    c["enf_recharge"] = entier(d.get("enf_recharge"), RECHARGE_MIN, RECHARGE_MAX, c["enf_recharge"])
    c["enf_rayon"] = entier(d.get("enf_rayon"), RAYON_MIN, RAYON_MAX, c["enf_rayon"])
    c["enf_fin_monstres"] = d.get("enf_fin_monstres") not in (0, False)


def _pos3(v):
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    try:
        p = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    return p if all(x == x and abs(x) < 1e6 for x in p) else None


def _dist_xz(a, b) -> float:
    return math.hypot(a[0] - b[0], a[2] - b[2])


class EnfermementPartie:
    """Mêlée à ``hanteurs.Partie`` (serveur) : demandes, enfermements en cours, fins."""

    def _enfermement_init(self) -> None:
        self.enfermements: dict[int, dict] = {}   # id -> {par, zone, stage, room, salle, pos, rayon, t0, fin, liens,
        #                                            monstres}
        self.enf_t: dict[int, float] = {}          # Hanteur -> heure de sa dernière demande
        self.enf_n = 0
        self.n_enf = self.n_enf_refus = 0

    # --- demande ------------------------------------------------------------------------------------------------
    def enfermer(self, pid: int, msg: dict, autorite: tuple, peut=None, now: float | None = None) -> str:
        """``henfermer`` d'un joueur. ``autorite`` = (id de l'autorité de sa zone, en cinématique) ; ``peut(pid)`` :
        ce Link sait être enfermé (client à jour). Renvoie "" (accepté : ``henf`` diffusé) ou la raison du refus
        (``hrefus`` envoyé au Hanteur)."""
        now = self.clock() if now is None else now
        raison, cible, liens = self._refus_enf(pid, msg, autorite, peut or (lambda p: True), now)
        if raison:
            self.n_enf_refus += 1
            self.sortie.append((pid, {"type": "hrefus", "quoi": "enfermement", "raison": raison}))
            return raison
        e = self.etat[pid]
        zone, stage, room = e["zone"], e["stage"], e["room"]
        centre = tuple(round(v, 1) for v in self.etat[cible]["pos"])
        self.enf_n += 1
        eid = self.enf_n
        en = {"id": eid, "par": pid, "zone": zone, "stage": stage, "room": room,
              "salle": -1 if stage == STAGE_MER else room, "pos": centre, "rayon": self.cfg["enf_rayon"],
              "t0": now, "fin": now + self.cfg["enf_duree"], "liens": set(liens), "monstres": set()}
        self.enfermements[eid] = en
        self.recharges[(pid, "enfermement")] = now + self.cfg["enf_recharge"]
        self.n_enf += 1
        msg = self._msg_enf(en, now)
        self.sortie.append((("zone", zone), msg))
        for o in liens:                          # au cas où sa zone a changé entre-temps chez le serveur
            if (self.etat.get(o) or {}).get("zone") != zone:
                self.sortie.append((o, msg))
        self.t_diffusion = float("-inf")
        noms = ", ".join(str(self.noms.get(o, o)) for o in sorted(liens))
        self.log(f"Hanteurs : {self.noms.get(pid, pid)} enferme {noms} ({en['rayon']} u, {self.cfg['enf_duree']} s, "
                 f"{zone})")
        return ""

    def _msg_enf(self, en: dict, now: float) -> dict:
        return {"type": "henf", "id": en["id"], "par": en["par"], "pos": list(en["pos"]), "rayon": en["rayon"],
                "salle": en["salle"], "stage": en["stage"], "room": en["room"],
                "duree": round(max(0.0, en["fin"] - now), 1), "liens": sorted(en["liens"]),
                "fin_monstres": int(bool(self.cfg["enf_fin_monstres"]))}

    def _frais(self, o: int, now: float) -> dict | None:
        e = self.etat.get(o)
        if not e or e.get("pos") is None or e.get("zone") is None or now - e.get("t", float("-inf")) > ETAT_MAX:
            return None
        return e

    def _vivant(self, o: int) -> bool:
        v = self.vie.get(o)
        return v is None or v.get("v", 1) > 0

    def _refus_enf(self, pid: int, msg: dict, autorite, peut, now: float):
        if not self.cfg.get("enfermement", True):
            return "l'enfermement est coupé dans cette partie", None, ()
        if not self.repartie:
            return "la partie n'a pas commencé (répartition des rôles en attente)", None, ()
        if not self.est_hanteur(pid):
            return "seuls les Hanteurs enferment", None, ()
        if now - self.enf_t.get(pid, float("-inf")) < 1.0 / DEMANDES_PAR_S:
            return "trop de demandes", None, ()
        self.enf_t[pid] = now
        if pid in self.potions:
            return "tu joues un Link (potion)", None, ()
        r = self.recharges.get((pid, "enfermement"), 0.0) - now
        if r > 0:
            return f"enfermement : recharge, encore {math.ceil(r)} s", None, ()
        e = self._frais(pid, now)
        if e is None or e.get("stage") is None or e.get("room") is None:
            return "position inconnue (jeu pas prêt ?)", None, ()
        if e.get("busy"):
            return "tu es en cinématique", None, ()
        zone, stage = e["zone"], e["stage"]
        from synchro import cibles
        if stage in cibles.STAGES_EXCLUES:
            return "pas d'enfermement dans cette stage", None, ()
        if any(en["zone"] == zone for en in self.enfermements.values()):
            return "un enfermement est déjà en cours dans cette salle", None, ()
        holder, busy = autorite
        if busy:
            return "l'autorité de la salle est en cinématique", None, ()
        possedes = set(self.possedes())
        candidats = {}
        for o in self._liens():
            eo = self._frais(o, now)
            if eo is None or eo["zone"] != zone or o in possedes or not self._vivant(o) or not peut(o):
                continue
            candidats[o] = eo
        if not candidats:
            return "aucun Link (à jour) dans ta salle", None, ()
        c = msg.get("cible")
        if isinstance(c, int) and not isinstance(c, bool) and c in candidats:
            cible = c
        else:
            cible = min(candidats, key=lambda o: (_dist_xz(candidats[o]["pos"], e["pos"]), o))
        ec = candidats[cible]
        if _dist_xz(ec["pos"], e["pos"]) > PORTEE:
            return f"{self.noms.get(cible, '?')} est trop loin de toi ({PORTEE:.0f} u au plus)", None, ()
        if ec.get("busy"):
            return f"{self.noms.get(cible, '?')} est en cinématique", None, ()
        if ec.get("ship"):
            return f"{self.noms.get(cible, '?')} est en bateau", None, ()
        rayon = self.cfg["enf_rayon"]
        liens = [o for o, eo in candidats.items() if not eo.get("busy") and not eo.get("ship")
                 and _dist_xz(eo["pos"], ec["pos"]) <= rayon]
        return "", cible, liens

    # --- horloge ------------------------------------------------------------------------------------------------
    def _enfermement_tick(self, now: float) -> None:
        possedes = set(self.possedes()) if self.enfermements else set()
        for eid, en in list(self.enfermements.items()):
            if not self.est_hanteur(en["par"]):
                self.enf_fin(eid, "le Hanteur est parti", now)
                continue
            if now >= en["fin"]:
                self.enf_fin(eid, "durée écoulée", now)
                continue
            for o in list(en["liens"]):
                e = self.etat.get(o) or {}
                if self.roles.get(o) != "link" or o in possedes or not self._vivant(o) \
                        or (e.get("zone") is not None and e["zone"] != en["zone"]):
                    en["liens"].discard(o)
            if not en["liens"]:
                self.enf_fin(eid, "plus aucun Link enfermé", now)
                continue
            # monstres des Hanteurs dans le cercle : retenus dès qu'ils sont vus vivants chez l'autorité
            for key, inv in self.invoques.items():
                p = _pos3(inv.get("pos"))
                if inv.get("zone") == en["zone"] and inv.get("vu") is not None and p is not None \
                        and _dist_xz(p, en["pos"]) <= en["rayon"] + MARGE_MONSTRES:
                    en["monstres"].add(key)
            if self.cfg.get("enf_fin_monstres", True) and en["monstres"] \
                    and not any(k in self.invoques for k in en["monstres"]):
                self.enf_fin(eid, "les monstres des Hanteurs sont battus", now)

    def enf_fin(self, eid: int, raison: str, now: float | None = None) -> bool:
        en = self.enfermements.pop(eid, None)
        if en is None:
            return False
        msg = {"type": "henf_fin", "id": eid, "raison": raison}
        self.sortie.append((("zone", en["zone"]), msg))
        for o in sorted(en["liens"]):
            if (self.etat.get(o) or {}).get("zone") != en["zone"]:
                self.sortie.append((o, msg))
        if (self.etat.get(en["par"]) or {}).get("zone") != en["zone"] and en["par"] in self.noms:
            self.sortie.append((en["par"], msg))
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : fin de l'enfermement {eid} ({raison})")
        return True

    def enf_fin_joueur(self, pid: int, raison: str, now: float) -> None:
        """Départ ou changement de rôle : ses enfermements finissent (Hanteur), il en sort (Link)."""
        for eid, en in list(self.enfermements.items()):
            if en["par"] == pid:
                self.enf_fin(eid, raison, now)
            else:
                en["liens"].discard(pid)
        self.enf_t.pop(pid, None)

    def enf_heriter(self, nouveau: int, ancien: int) -> None:
        for en in self.enfermements.values():
            if en["par"] == ancien:
                en["par"] = nouveau
            if ancien in en["liens"]:
                en["liens"].discard(ancien)
                en["liens"].add(nouveau)

    def entree_zone_enf(self, pid: int, zone) -> None:
        """Un joueur arrive dans une zone : l'enfermement en cours lui est annoncé (un Hanteur le voit dans le lanceur ;
        un Link qui n'en fait pas partie n'est pas enfermé)."""
        now = self.clock()
        for en in self.enfermements.values():
            if en["zone"] == zone:
                self.sortie.append((pid, self._msg_enf(en, now)))

    # --- état diffusé -------------------------------------------------------------------------------------------
    def vue_enfermement(self, now: float) -> dict:
        return {"enfermement": int(bool(self.cfg.get("enfermement", True))), "enf_duree": self.cfg["enf_duree"],
                "enf_recharge": self.cfg["enf_recharge"], "enf_rayon": self.cfg["enf_rayon"],
                "enf_fin_monstres": int(bool(self.cfg.get("enf_fin_monstres", True))),
                "enfermements": [{"id": en["id"], "par": en["par"], "zone": en["zone"],
                                  "reste": round(max(0.0, en["fin"] - now), 1), "liens": sorted(en["liens"]),
                                  "monstres": len(en["monstres"])} for en in self.enfermements.values()]}

    def moi_enfermement(self, pid: int, now: float) -> dict:
        return {"recharge_enf": round(max(0.0, self.recharges.get((pid, "enfermement"), now) - now), 1)}


# --- client -----------------------------------------------------------------------------------------------------------
class EnfermementSync:
    """Chez un Link : tient armé (CALL 21) l'enfermement annoncé par le serveur, puis le lâche. Fil de travail
    (``step``) ; ``receive`` vient de la boucle réseau (le dernier message gagne, appliqué au tick suivant)."""

    def __init__(self, enabled: bool = False, clock=None):
        import threading
        import time
        self.enabled = bool(enabled)
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self.actuel: dict | None = None      # {id, pos, rayon, salle, stage, room, fin, t}
        self.a_lacher = False                # fin reçue (ou abandon) : CALL 21 vie 0 au prochain tick
        self.arme = False                    # armé par nous (résultat 1 ou 2 vu)
        self.t = None                        # dernier entretien
        self.supported: bool | None = None
        self.notes: list[str] = []
        self.n_armes = self.n_entretiens = self.n_refus = self.n_lache = 0
        self.compteurs: dict = {}
        self.dernier_refus = None
        self._dit_sans_mod = False

    def take_notes(self) -> list[str]:
        with self._lock:
            out, self.notes = self.notes, []
        return out

    def _note(self, s: str) -> None:
        if len(self.notes) < 16:
            self.notes.append(s)

    def receive(self, msg: dict, me, now: float | None = None) -> None:
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        t = msg.get("type")
        with self._lock:
            if t == "henf":
                liens = msg.get("liens")
                p = _pos3(msg.get("pos"))
                ray, salle, duree, eid = msg.get("rayon"), msg.get("salle"), msg.get("duree"), msg.get("id")
                if not isinstance(liens, list) or me not in liens or p is None:
                    return
                if not all(isinstance(v, int) and not isinstance(v, bool) for v in (ray, salle, eid)) \
                        or not isinstance(duree, (int, float)) or isinstance(duree, bool):
                    return
                if self.actuel is not None and self.actuel["id"] == eid:
                    return
                if self.actuel is not None:
                    self.a_lacher = True             # un autre enfermement remplace le premier
                self.actuel = {"id": eid, "pos": p, "rayon": max(100, min(5000, ray)), "salle": max(-1, min(63, salle)),
                               "stage": msg.get("stage"), "room": msg.get("room"),
                               "fin": now + max(0.0, min(float(DUREE_MAX), float(duree))) + MARGE_FIN, "t": now}
                self.arme = False
                self.t = None
                fm = " ou jusqu'à ce que ses monstres soient battus" if msg.get("fin_monstres") in (1, True) else ""
                self._note(f"un Hanteur t'enferme ! Tu ne peux pas sortir du cercle pendant {int(duree)} s{fm}")
            elif t == "henf_fin":
                if self.actuel is not None and msg.get("id") == self.actuel["id"]:
                    self.actuel = None
                    self.a_lacher = True
                    self._note(f"tu es libre ({str(msg.get('raison') or 'fin de l’enfermement')[:80]})")

    def _abandon(self, raison: str) -> None:
        if self.actuel is not None:
            self._note(f"enfermement levé chez toi : {raison}")
        self.actuel = None
        self.a_lacher = True

    def step(self, mb, st: dict | None, now: float | None = None, voulu: bool = True) -> None:
        """``mb`` : Mailbox en écriture (ou None) ; ``st`` : notre dernier state (stage, room) ; ``voulu`` faux : rôle
        Hanteur, Link possédé, mode coupé : rien n'est armé (et l'armé est lâché)."""
        if not self.enabled:
            return
        now = self.clock() if now is None else now
        ok = bool(mb is not None and getattr(mb, "enf_ok", False))
        with self._lock:
            if mb is not None and ok != self.supported:
                self.supported = ok
            ac = self.actuel
            if ac is not None and not voulu:
                self._abandon("tu n'es plus un Link libre")
                ac = None
            if ac is not None and now >= ac["fin"]:
                self._abandon("durée écoulée")
                ac = None
            if ac is not None and st is not None and (st.get("stage") != ac["stage"]
                                                      or (ac["salle"] >= 0 and st.get("room") != ac["room"])):
                self._abandon("tu as changé de salle")
                ac = None
            lacher, self.a_lacher = self.a_lacher, False
        if not ok:
            if ac is not None and self.supported is False and not self._dit_sans_mod:
                self._dit_sans_mod = True
                with self._lock:
                    self._note("enfermement : le mod natif n'est pas à jour (0.35.0) : tu n'es pas enfermé")
            self.arme = False
            return
        if lacher and self.arme and ac is None:
            mb.liberer_enf()
            self.n_lache += 1
            self.arme = False
        if ac is None:
            return
        if self.t is not None and 0 <= now - self.t < PERIODE:
            return
        if self.arme and not mb.enf_counters().get("arme"):
            c = mb.enf_counters()
            from jeu import mailbox as mbx
            with self._lock:
                self._abandon(mbx.ENF_RAISONS.get(c.get("raison"), "levé par le jeu") or "levé par le jeu")
            self.arme = False
            return
        r = mb.enfermer(ac["pos"], ac["rayon"], ac["salle"], VIE)
        self.t = now
        if r in (1, 2):
            if r == 1:
                self.n_armes += 1
            else:
                self.n_entretiens += 1
            self.arme = True
            return
        self.n_refus += 1
        self.dernier_refus = r
        if r in REFUS_DEFINITIFS:
            with self._lock:
                self._abandon(REFUS_DEFINITIFS[r])
        # -3 (événement), -2 (pas de partie) : réessayé à l'entretien suivant, jusqu'à la fin annoncée

    def stop(self, mb) -> None:
        """Arrêt du client : Link libéré tout de suite (sinon le mod lâche seul en VIE images)."""
        try:
            if mb is not None and getattr(mb, "enf_ok", False) and self.arme:
                mb.liberer_enf()
        finally:
            self.arme = False
            self.actuel = None

    def enferme(self) -> dict | None:
        """Pour le lanceur : {reste} pendant un enfermement."""
        ac = self.actuel
        if ac is None:
            return None
        return {"reste": round(max(0.0, ac["fin"] - MARGE_FIN - self.clock()), 1), "rayon": ac["rayon"]}

    def status(self) -> str:
        if self.supported is None:
            return "en attente du jeu"
        if not self.supported:
            return "mod natif sans la zone WWMPENF1 (0.35.0)"
        ac = self.actuel
        etat = f"ENFERMÉ (n° {ac['id']}, {ac['rayon']} u)" if ac else "libre"
        return (f"{etat}, {self.n_armes} armement(s), {self.n_entretiens} entretien(s), {self.n_refus} refus, "
                f"{self.n_lache} libération(s)")
