"""Tenues à pouvoir, partie B : les deux FUSIONS (docs/etudes/autres_zelda.md §6) ; logique pure du serveur, testable
sans jeu (tests/test_fusions.py).

Avec la règle de la partie « Tenues à pouvoir » (pas de règle à part) : le SERVEUR décide qu'une fusion commence et
finit, et dit à chacun des deux son rôle ; le client applique (client/fusions.py, mod natif 0.47.0).

* État des clients (``fusion`` ev ``etat``) : ``charge`` (B tenu depuis CHARGE_MIN sans rien porter), ``bombe`` (une
  bombe en main) ; ev ``lancer`` (la bombe vient d'être lancée, ``age`` : depuis combien de temps elle était tenue) ;
  ev ``fin`` (le client constate la fin : mort, L3, port perdu).
* **Tournoyante** (sombre + turquoise) : les deux chargent en même temps, collés (même zone, à moins de DISTANCE) :
  la paire est ARMÉE (``arme`` aux deux : leur mod garde B tenu pour le jeu) ; ils doivent alors RELÂCHER B ensemble,
  à moins de ECART_RELACHE (le relâchement est la fin de « charge » de leur état) : la fusion commence (le premier qui
  a chargé déplace) ; sinon (trop tôt, trop tard) les deux sont ÉTOURDIS (``etourdi``) et le duo se repose. **Méga bombe** (orange + violet) : deux lancers à moins de ECART_LANCERS, chaque
  bombe tenue depuis BOMBE_MURE au moins, collés : le premier qui a lancé déplace. (2026-11-19) Comme la
  tournoyante : deux Link collés qui tiennent chacun une bombe sont ARMÉS ; un lancer seul, des lancers trop
  éloignés (ECART_LANCERS) ou une bombe pas mûre : les deux sont ÉTOURDIS.
* Jamais : règle coupée, mode Hanteurs, Hanteur, robot, joueur sans « fusions » (mod 0.47.0), cinématique (busy),
  menu / porté / porteur (nc), coureur d'une course, état vieux, départ en cours, l'un déjà fusionné, duo au repos.
* Fin (tick) : DUREE écoulée, départ, zone changée (étape ou salle), cinématique ; client : mort, L3, port perdu.
* ``relais`` (méga bombe) : l'explosion est créée dans les DEUX jeux ; chez l'autorité de la salle, elle touche les
  vrais monstres ; ailleurs elle touche des marionnettes, dont les coups ne sont relayés que si l'autorité n'est ni l'un
  ni l'autre (et alors seulement chez celui qui déplace) : jamais deux fois.
* Coup relayé d'un fusionné : ``champ_hit`` ajoute ``tenf`` (facteurs de fusion) que l'autorité écrit avant le CALL 14.
* VRAIE fusion (mod 0.50.0, docs/dev/PORTAGE_NATIF.md §3.54) : un seul Link, celui du porteur (le « pilote »). Les
  états « fen » de l'attaquant (``entrees``) sont relayés TOUT DE SUITE au pilote de SA fusion (jamais à un autre, ni
  d'un autre que l'attaquant, ni plus de ``FEN_MAX_PAR_S`` par seconde) ; le « debut » donne ``hz``
  (--fusion-entrees-hz) et ``peremption`` (--fusion-peremption-ms). Ces deux réglages ne changent pas à chaud : pas de
  règle dans « regles ».

ESSAI « fusions de tenues » (``--essai-fusions``, Réglages › Essais ; à retirer après validation, tout ce qui sert à
l'essai est marqué ESSAI) : un client d'essai annonce un faux coéquipier (``essai_faux`` : position, zone) ;
``essai_tournoyante`` : il charge (20 s) et relâche B en même temps que le joueur ; ``essai_rate`` : il charge
mais ne relâche jamais (timing raté : étourdis) ; ``essai_mega_direct`` : la méga bombe commence tout de suite (banc) ; ``essai_bombe`` : il tient une bombe (20 s) et la lance en même temps que
le joueur ; ``essai_role`` : rôle voulu pour le joueur (0 : le premier qui a chargé ou lancé). Le faux coéquipier prend
l'autre couleur du duo ; si la couleur du joueur n'est pas dans le duo du bouton, la fusion d'essai le traite comme
la première couleur de ce duo (couleurs d'affichage seulement : rien d'autre ne change).
"""
from __future__ import annotations

import time

from commun import couleurs
from commun import fusions as fu

VERSION = 1
ID_ESSAI = 92                # ESSAI : faux coéquipier (pas ceux des essais porter 98, porté 97, coups 96, ralliement
                             # 95, Fragments 94, tenues 93)
NOM_ESSAI = "Essai Fusion"
FAUX_AGE = 4.0               # s : position du faux coéquipier plus vieille : il n'est plus là
ESSAI_GESTE = 20.0           # s : le faux coéquipier charge / tient sa bombe pendant ce temps


class FusionsServeur:
    """``info(pid)`` (serveur/principal.py) rend {nom, couleur (de pouvoir), joueur, hanteur, robot, zone, pos, t,
    busy, nc, course, leaving, feat} ou None ; ``autorite(zone)`` rend l'id de l'autorité ou None ; ``hanteurs`` :
    mode Hanteurs. Chaque méthode rend la liste des messages à envoyer [(pid, msg)]."""

    def __init__(self, on: bool = True, essai: bool = False, clock=time.monotonic, entrees_hz: int = fu.ENTREES_HZ,
                 peremption_ms: int = fu.PEREMPTION_MS):
        self.on = bool(on)
        self.essai = bool(essai)
        self.clock = clock
        self.entrees_hz = fu.borne_hz(entrees_hz)
        self.peremption_ms = fu.borne_peremption(peremption_ms)
        self.fen_relayes = 0                  # états « fen » relayés
        self.fen_jetes = 0                    # jetés (pas l'attaquant d'une fusion, illisibles, trop nombreux)
        self.fen_debit: dict[int, list] = {}  # pid -> [début de la seconde, états dans la seconde]
        self.hanteurs = False
        self.etats: dict[int, dict] = {}      # pid -> {charge, charge_t, bombe, bombe_t, lancer_t, age, t}
        self.fusions: dict[int, dict] = {}    # id -> fusion
        self.par_joueur: dict[int, int] = {}  # pid -> id de fusion
        self.repos: dict[frozenset, float] = {}
        self.n = 0
        self.armes: dict = {}                 # frozenset(paire) -> paire armée (tournoyante : relâchements attendus)
        self.n_rates = 0
        self.journal: list[str] = []
        # ESSAI
        self.essai_faux: dict[int, dict] = {}     # pid -> {pos, zone, t, charge_jusqu, bombe_jusqu}
        self.essai_role: dict[int, int] = {}      # pid -> rôle voulu (0 : libre)

    # -- règles --------------------------------------------------------------------------------------------------------
    def actif(self) -> bool:
        return (self.on or self.essai) and not self.hanteurs

    @staticmethod
    def a_une_tenue(i: dict | None) -> bool:
        return bool(i and i.get("joueur") and not i.get("hanteur") and not i.get("robot"))

    def disponible(self, i: dict | None, now: float) -> bool:
        """En jeu et libre pour commencer une fusion."""
        if not self.a_une_tenue(i) or i.get("leaving") or fu.FEAT not in (i.get("feat") or ()):
            return False
        t = i.get("t")
        if not isinstance(t, (int, float)) or now - t > fu.ETAT_AGE:
            return False
        if i.get("busy") or i.get("nc") or i.get("course"):
            return False
        return isinstance(i.get("zone"), str) and fu.pos3(i.get("pos")) is not None

    def en_fusion(self, pid) -> dict | None:
        k = self.par_joueur.get(pid)
        return self.fusions.get(k) if k is not None else None

    # -- messages des clients -----------------------------------------------------------------------------------------
    def message(self, pid, msg: dict, info, autorite=lambda z: None, now: float | None = None) -> list:
        """``fusion`` d'un client : etat, lancer, fin ; ESSAI : essai_faux, essai_tournoyante, essai_bombe,
        essai_role. Rend les envois (refus d'essai compris)."""
        now = self.clock() if now is None else now
        ev = msg.get("ev")
        if ev == "etat":
            e = self.etats.setdefault(pid, {"charge": False, "charge_t": None, "bombe": False, "bombe_t": None,
                                            "lancer_t": None, "age": 0.0})
            ch, bo = msg.get("charge") in (1, True), msg.get("bombe") in (1, True)
            relache = e["charge"] and not ch
            if ch and not e["charge"]:
                e["charge_t"] = now
            if not ch:
                e["charge_t"] = None
            if bo and not e["bombe"]:
                e["bombe_t"] = now
            e["charge"], e["bombe"], e["t"] = ch, bo, now
            envois = self._relache(pid, now) if relache else []
            return envois + self.tick([pid] + [q for q in self.etats if q != pid], info, autorite, now)
        if ev == "lancer":
            e = self.etats.setdefault(pid, {"charge": False, "charge_t": None, "bombe": False, "bombe_t": None,
                                            "lancer_t": None, "age": 0.0})
            age = msg.get("age")
            e["lancer_t"] = now
            e["age"] = float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age < 60 else 0.0
            e["bombe"], e["t"] = False, now
            return self._mega(pid, info, autorite, now) + self.tick([pid] + [q for q in self.etats if q != pid],
                                                                     info, autorite, now)
        if ev == "fin":
            f = self.en_fusion(pid)
            if f is None or msg.get("id") not in (None, f["id"]):
                return []
            r = msg.get("raison")
            return self.finir(f, r if r in fu.RAISONS_FIN else "port", now)
        if ev and str(ev).startswith("essai_"):
            raison = self.essai_message(pid, msg, info, now)
            if raison:
                return [(pid, {"type": "fusion", "ev": "refus", "raison": raison[:120]})]
            return self.tick([pid], info, autorite, now)
        return []

    # -- début ---------------------------------------------------------------------------------------------------------
    def _couleur(self, pid, i: dict, genre_voulu: int = 0) -> int:
        c = couleurs.normaliser(i.get("couleur"))
        if self.essai and pid in self.essai_faux and genre_voulu:
            a, b = fu.couleurs_du_genre(genre_voulu)
            if c not in (a, b):
                c = a                               # ESSAI : la première couleur du duo du bouton
        return c

    def _partenaires(self, pid, info, now):
        """(partenaire, son info, couleur du joueur, couleur du partenaire, genre) possibles pour ``pid``."""
        i = info(pid)
        if not self.disponible(i, now) or self.en_fusion(pid):
            return []
        out = []
        faux = self._faux(pid, now)
        if faux is not None:                        # ESSAI : le faux coéquipier, de l'autre couleur du duo
            g = faux.get("genre", 0)
            c = self._couleur(pid, i, g)
            a, b = fu.couleurs_du_genre(g)
            cf = b if c == a else a
            fi = {"zone": faux["zone"], "pos": faux["pos"]}
            if g and fi["zone"] == i.get("zone") and fu.colles(i.get("pos"), fi["pos"]):
                out.append((ID_ESSAI, fi, c, cf, g))
            return out
        c = self._couleur(pid, i)
        for q in self.etats:
            if q == pid or self.en_fusion(q):
                continue
            j = info(q)
            if not self.disponible(j, now):
                continue
            g = fu.genre_de(c, self._couleur(q, j))
            if not g or j.get("zone") != i.get("zone") or not fu.colles(i.get("pos"), j.get("pos")):
                continue
            if now < self.repos.get(frozenset((pid, q)), -1e9):
                continue
            out.append((q, j, c, self._couleur(q, j), g))
        return out

    def _etat_faux(self, pid, now) -> dict:
        """ESSAI : l'état simulé du faux coéquipier."""
        f = self._faux(pid, now) or {}
        return {"charge": now < f.get("charge_jusqu", -1e9), "charge_t": f.get("charge_t"),
                "bombe": now < f.get("bombe_jusqu", -1e9)}

    def tick(self, ids, info, autorite=lambda z: None, now: float | None = None) -> list:
        """Débuts des fusions tournoyantes, fins (durée, départ, zone, cinématique)."""
        now = self.clock() if now is None else now
        envois = []
        for f in list(self.fusions.values()):
            envois += self._verifier(f, info, now)
        if not self.actif():
            return envois
        envois += self._armer_mega(ids, info, now)
        envois += self._armes_tick(info, autorite, now)
        if self.essai:                              # ESSAI : méga bombe directe (sans vraie bombe : banc, vidéo)
            for pid in list(ids):
                f = self.essai_faux.get(pid) or {}
                if now > f.get("mega_direct", -1e9) or self.en_fusion(pid):
                    f.pop("mega_direct", None)
                    continue
                for q, j, c, cq, g in self._partenaires(pid, info, now):     # réessayé 5 s (faux pas encore là)
                    if q == ID_ESSAI and g == fu.MEGA_BOMBE:
                        f.pop("mega_direct", None)
                        envois += self.commencer(g, pid, q, {pid: c, q: cq}, info, autorite, now)
                        break
        for pid in list(ids):
            e = self.etats.get(pid)
            if not e or not e.get("charge") or self.en_fusion(pid):
                continue
            for q, j, c, cq, g in self._partenaires(pid, info, now):
                if g != fu.TOURNOYANTE:
                    continue
                eq = self._etat_faux(pid, now) if q == ID_ESSAI else self.etats.get(q, {})
                if not eq.get("charge"):
                    continue
                cle = frozenset((pid, q))
                if cle in self.armes:
                    break
                tp, tq = e.get("charge_t") or now, eq.get("charge_t") or now
                porteur = pid if tp <= tq else q
                self.armes[cle] = {"g": g, "porteur": porteur, "passager": q if porteur == pid else pid,
                                   "cols": {pid: c, q: cq}, "t": now, "rel": {}}
                for x, autre in ((pid, q), (q, pid)):
                    if x != ID_ESSAI:
                        envois.append((x, {"type": "fusion", "ev": "arme", "partenaire": autre,
                                           "ecart": fu.ECART_RELACHE,
                                           **({"essai": 1} if ID_ESSAI in (pid, q) else {})}))
                self.journal.append(f"fusions : tournoyante armée ({(info(pid) or {}).get('nom', pid)}) : "
                                    f"relâchez B ensemble ({fu.ECART_RELACHE:g} s)")
                break
        return envois

    # -- tournoyante : le relâchement des deux --------------------------------------------------------------------
    def _relache(self, pid, now) -> list:
        """``pid`` vient de relâcher B (fin de « charge ») : noté dans sa paire armée ; ESSAI : le faux coéquipier
        relâche en même temps (ou jamais, « essai_rate »)."""
        for cle, a in self.armes.items():
            if pid not in cle or pid in a["rel"]:
                continue
            a["rel"][pid] = now
            autre = next(iter(cle - {pid}))
            if autre == ID_ESSAI and not (self.essai_faux.get(pid) or {}).get("rate"):
                a["rel"][autre] = now
            break
        return []

    def _armes_tick(self, info, autorite, now) -> list:
        envois = []
        for cle, a in list(self.armes.items()):
            rel = a["rel"]
            ecart = fu.ECART_LANCERS if a["g"] == fu.MEGA_BOMBE else fu.ECART_RELACHE
            if len(rel) >= 2:
                t1, t2 = sorted(rel.values())[:2]
                del self.armes[cle]
                mures = a["g"] != fu.MEGA_BOMBE or all(v >= fu.BOMBE_MURE for v in a.get("ages", {}).values())
                if t2 - t1 <= ecart and mures:
                    porteur, passager = a["porteur"], a["passager"]
                    if a["g"] == fu.MEGA_BOMBE:             # le premier qui a lancé déplace
                        porteur = min(rel, key=rel.get)
                        passager = next(iter(cle - {porteur}))
                    envois += self.commencer(a["g"], porteur, passager, a["cols"], info, autorite, now)
                else:
                    envois += self._rate(cle, now, "écart" if mures else "trop tôt")
            elif len(rel) == 1 and now - next(iter(rel.values())) > ecart:
                del self.armes[cle]
                envois += self._rate(cle, now, "seul")
            elif now - a["t"] > fu.ARME_MAX:
                del self.armes[cle]
                envois += [(x, {"type": "fusion", "ev": "desarme"}) for x in cle if x != ID_ESSAI]
        return envois

    def _rate(self, cle, now, pourquoi) -> list:
        """Timing raté : les deux étourdis, le duo se repose."""
        self.n_rates += 1
        self.repos[cle] = now + fu.REPOS
        self.journal.append(f"fusions : tournoyante ratée ({pourquoi}) : les deux sont étourdis")
        return [(x, {"type": "fusion", "ev": "etourdi", "raison": pourquoi,
                     **({"essai": 1} if ID_ESSAI in cle else {})}) for x in cle if x != ID_ESSAI]

    def _mega(self, pid, info, autorite, now) -> list:
        """``pid`` vient de lancer sa bombe : noté dans sa paire armée (méga bombe) avec l'âge de sa bombe ; ESSAI :
        le faux coéquipier lance en même temps, une bombe mûre (ou jamais, « essai_bombe_rate »)."""
        if not self.actif():
            return []
        e = self.etats.get(pid) or {}
        for cle, a in self.armes.items():
            if pid not in cle or a["g"] != fu.MEGA_BOMBE or pid in a["rel"]:
                continue
            a["rel"][pid] = now
            a.setdefault("ages", {})[pid] = e.get("age", 0.0)
            autre = next(iter(cle - {pid}))
            if autre == ID_ESSAI and not (self.essai_faux.get(pid) or {}).get("rate"):
                a["rel"][autre] = now
                a["ages"][autre] = fu.BOMBE_MURE
            if self.essai and e.get("age", 0.0) < fu.BOMBE_MURE:
                self.journal.append(f"fusions : ESSAI, lancer trop tôt ({e.get('age', 0.0):.1f} s < "
                                    f"{fu.BOMBE_MURE:.1f} s)")
            break
        return []

    def _armer_mega(self, ids, info, now) -> list:
        """Deux Link collés qui tiennent chacun une bombe : la paire est armée (méga bombe)."""
        envois = []
        for pid in list(ids):
            e = self.etats.get(pid)
            if not e or not e.get("bombe") or self.en_fusion(pid):
                continue
            for q, j, c, cq, g in self._partenaires(pid, info, now):
                if g != fu.MEGA_BOMBE:
                    continue
                eq = self._etat_faux(pid, now) if q == ID_ESSAI else self.etats.get(q, {})
                cle = frozenset((pid, q))
                if not eq.get("bombe") or cle in self.armes:
                    continue
                self.armes[cle] = {"g": g, "porteur": None, "passager": None, "cols": {pid: c, q: cq}, "t": now,
                                   "rel": {}, "ages": {}}
                for x, autre in ((pid, q), (q, pid)):
                    if x != ID_ESSAI:
                        envois.append((x, {"type": "fusion", "ev": "arme", "partenaire": autre, "genre": g,
                                           "ecart": fu.ECART_LANCERS,
                                           **({"essai": 1} if ID_ESSAI in (pid, q) else {})}))
                self.journal.append(f"fusions : méga bombe armée ({(info(pid) or {}).get('nom', pid)}) : "
                                    f"lancez ensemble ({fu.ECART_LANCERS:g} s)")
                break
        return envois

    def commencer(self, genre, porteur, passager, cols: dict, info, autorite, now) -> list:
        # ESSAI : rôle voulu pour le joueur réel
        for pid in (porteur, passager):
            r = self.essai_role.get(pid, 0) if self.essai else 0
            if r == fu.DEPLACE and pid == passager or r == fu.ATTAQUE and pid == porteur:
                porteur, passager = passager, porteur
                break
        self.n += 1
        ip, iq = info(porteur) or {}, info(passager) or {}
        reel = ip if porteur != ID_ESSAI else iq
        zone = reel.get("zone")
        pp, pq = fu.pos3(ip.get("pos")), fu.pos3(iq.get("pos"))
        if porteur == ID_ESSAI or passager == ID_ESSAI:
            pid_reel = passager if porteur == ID_ESSAI else porteur
            fx = self._faux(pid_reel, now) or {}
            if porteur == ID_ESSAI:
                pp = fu.pos3(fx.get("pos"))
            else:
                pq = fu.pos3(fx.get("pos"))
        centre = [round((a + b) / 2.0, 2) for a, b in zip(pp, pq)] if pp and pq else list(pp or pq or (0, 0, 0))
        f = {"id": self.n, "genre": genre, "porteur": porteur, "passager": passager, "debut": now,
             "fin": now + fu.DUREE, "zone": zone, "deplace": cols.get(porteur, 0), "attaque": cols.get(passager, 0),
             "centre": centre}
        self.fusions[f["id"]] = f
        self.par_joueur[porteur] = self.par_joueur[passager] = f["id"]
        for pid in (porteur, passager):
            e = self.etats.get(pid)
            if e:
                e["charge"], e["charge_t"], e["lancer_t"] = False, None, None
        h = autorite(zone) if zone else None
        envois = []
        for pid, role in ((porteur, fu.DEPLACE), (passager, fu.ATTAQUE)):
            if pid == ID_ESSAI:
                continue
            autre = passager if pid == porteur else porteur
            relais = int(genre == fu.MEGA_BOMBE and h not in (porteur, passager) and pid == porteur)
            envois.append((pid, {"type": "fusion", "ev": "debut", "id": f["id"], "genre": genre, "role": role,
                                 "partenaire": autre, "duree": fu.DUREE, "deplace": f["deplace"],
                                 "attaque": f["attaque"], "centre": centre, "relais": relais,
                                 "hz": self.entrees_hz, "peremption": self.peremption_ms,
                                 **({"essai": 1} if ID_ESSAI in (porteur, passager) else {})}))
        noms = [(info(x) or {}).get("nom", NOM_ESSAI if x == ID_ESSAI else x) for x in (porteur, passager)]
        self.journal.append(f"fusions : {fu.GENRES[genre]} n° {f['id']} : {noms[0]} déplace, {noms[1]} attaque"
                            f" ({zone})")
        return envois

    # -- fin -----------------------------------------------------------------------------------------------------------
    def _verifier(self, f, info, now) -> list:
        if now >= f["fin"]:
            return self.finir(f, "duree", now)
        if not self.actif():
            return self.finir(f, "regle", now)
        for pid in (f["porteur"], f["passager"]):
            if pid == ID_ESSAI:
                other = f["passager"] if pid == f["porteur"] else f["porteur"]
                if self._faux(other, now) is None:
                    return self.finir(f, "depart", now)
                continue
            i = info(pid)
            if i is None or i.get("leaving") or not self.a_une_tenue(i):
                return self.finir(f, "depart", now)
            if i.get("zone") != f["zone"]:
                return self.finir(f, "zone", now)
            if i.get("busy"):
                return self.finir(f, "cinematique", now)
        return []

    def finir(self, f, raison: str, now: float | None = None) -> list:
        now = self.clock() if now is None else now
        if self.fusions.pop(f["id"], None) is None:
            return []
        envois = []
        for pid in (f["porteur"], f["passager"]):
            if self.par_joueur.get(pid) == f["id"]:
                del self.par_joueur[pid]
            if pid != ID_ESSAI:
                envois.append((pid, {"type": "fusion", "ev": "fin", "id": f["id"], "raison": raison}))
        self.repos[frozenset((f["porteur"], f["passager"]))] = now + fu.REPOS
        self.journal.append(f"fusions : fin de la fusion n° {f['id']} ({fu.RAISONS_FIN.get(raison, raison)}, "
                            f"{now - f['debut']:.1f} s)")
        return envois

    def oublier(self, pid) -> list:
        f = self.en_fusion(pid)
        envois = self.finir(f, "depart") if f else []
        for d in (self.etats, self.essai_faux, self.essai_role, self.fen_debit):
            d.pop(pid, None)
        return envois

    def couper(self) -> list:
        """Règle coupée ou mode Hanteurs : toutes les fusions finies."""
        envois = []
        for f in list(self.fusions.values()):
            envois += self.finir(f, "regle")
        return envois

    # -- VRAIE fusion : entrées de l'attaquant (chemin court : relayées sans attendre le tick) -------------------------
    def entrees(self, pid, msg: dict, now: float | None = None) -> list:
        """Un état « fen » de ``pid`` : [(pilote, état)] s'il est l'attaquant d'une fusion en cours (numéro de fusion
        juste, état lisible, débit borné), [] sinon. Le serveur n'en garde rien."""
        f = self.en_fusion(pid)
        e = fu.entrees(msg)
        if f is None or e is None or f["passager"] != pid or f["porteur"] == ID_ESSAI or e["f"] != f["id"]:
            self.fen_jetes += 1
            return []
        now = self.clock() if now is None else now
        d = self.fen_debit.setdefault(pid, [now, 0])
        if now - d[0] >= 1.0:
            d[0], d[1] = now, 0
        d[1] += 1
        if d[1] > fu.FEN_MAX_PAR_S:
            self.fen_jetes += 1
            return []
        self.fen_relayes += 1
        return [(f["porteur"], {"type": "fen", **e})]

    # -- coups relayés ---------------------------------------------------------------------------------------------------
    def champ_hit(self, pid) -> dict:
        """``{"tenf": [tous, magie]}`` de l'attaquant ``pid`` en fusion, {} sinon."""
        f = self.en_fusion(pid)
        if f is None:
            return {}
        v = list(fu.facteurs(f["genre"]))
        return {"tenf": v} if v != [8, 8] else {}

    def vue(self) -> list[dict]:
        now = self.clock()
        return [{"id": f["id"], "genre": fu.GENRES[f["genre"]], "porteur": f["porteur"], "passager": f["passager"],
                 "reste": round(max(0.0, f["fin"] - now), 1), "fen_relayes": self.fen_relayes,
                 "fen_jetes": self.fen_jetes} for f in self.fusions.values()]

    # -- ESSAI -----------------------------------------------------------------------------------------------------------
    def _faux(self, pid, now) -> dict | None:
        f = self.essai_faux.get(pid) if self.essai else None
        if not f or now - f.get("t", -1e9) > FAUX_AGE:
            return None
        return f

    def essai_message(self, pid, msg: dict, info, now: float) -> str:
        """ESSAI (``--essai-fusions`` seulement). Rend la raison d'un refus ("" : fait)."""
        if not self.essai:
            return "pas d'essai sur ce serveur"
        i = info(pid)
        if not self.a_une_tenue(i):
            return "pas de tenue (Hanteur, robot ou outil)"
        ev = msg.get("ev")
        f = self.essai_faux.setdefault(pid, {"pos": None, "zone": None, "t": -1e9, "genre": 0})
        if ev == "essai_faux":
            p = fu.pos3(msg.get("pos"))
            z = msg.get("zone")
            if p is None or not isinstance(z, str) or len(z) > 40:
                return "faux coéquipier mal placé"
            f.update(pos=list(p), zone=z, t=now)
            return ""
        if ev == "essai_bombe_rate":            # ESSAI : il tient une bombe mais ne la lancera pas
            f.update(genre=fu.MEGA_BOMBE, bombe_jusqu=now + ESSAI_GESTE, charge_jusqu=-1e9, charge_t=None, rate=True)
            return ""
        if ev in ("essai_tournoyante", "essai_rate"):
            f.update(genre=fu.TOURNOYANTE, charge_jusqu=now + ESSAI_GESTE, charge_t=now, bombe_jusqu=-1e9,
                     rate=ev == "essai_rate")
            self.journal.append(f"fusions : ESSAI, le faux coéquipier de {i.get('nom', pid)} charge sa tournoyante")
            return ""
        if ev == "essai_bombe":
            f.update(genre=fu.MEGA_BOMBE, bombe_jusqu=now + ESSAI_GESTE, charge_jusqu=-1e9, charge_t=None, rate=False)
            self.journal.append(f"fusions : ESSAI, le faux coéquipier de {i.get('nom', pid)} tient une bombe")
            return ""
        if ev == "essai_mega_direct":           # ESSAI : la méga bombe tout de suite (au prochain tick)
            f.update(genre=fu.MEGA_BOMBE, bombe_jusqu=now + ESSAI_GESTE, charge_jusqu=-1e9, charge_t=None,
                     mega_direct=now + 5.0)
            self.journal.append(f"fusions : ESSAI, méga bombe directe pour {i.get('nom', pid)}")
            return ""
        if ev == "essai_role":
            r = msg.get("role")
            if r not in (0, fu.DEPLACE, fu.ATTAQUE):
                return "rôle inconnu"
            self.essai_role[pid] = r
            return ""
        return "action inconnue"
