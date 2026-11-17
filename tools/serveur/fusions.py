"""Tenues à pouvoir, partie B : les deux FUSIONS (docs/etudes/autres_zelda.md §6) ; logique pure du serveur, testable
sans jeu (tests/test_fusions.py).

Avec la règle de la partie « Tenues à pouvoir » (pas de règle à part) : le SERVEUR décide qu'une fusion commence et
finit, et dit à chacun des deux son rôle ; le client applique (client/fusions.py, mod natif 0.47.0).

* État des clients (``fusion`` ev ``etat``) : ``charge`` (B tenu depuis CHARGE_MIN sans rien porter), ``bombe`` (une
  bombe en main) ; ev ``lancer`` (la bombe vient d'être lancée, ``age`` : depuis combien de temps elle était tenue) ;
  ev ``fin`` (le client constate la fin : mort, L3, port perdu).
* **Tournoyante** (sombre + turquoise) : les deux chargent en même temps, collés (même zone, à moins de DISTANCE) :
  le premier qui a chargé déplace. **Méga bombe** (orange + violet) : deux lancers à moins de ECART_LANCERS, chaque
  bombe tenue depuis BOMBE_MURE au moins, collés : le premier qui a lancé déplace.
* Jamais : règle coupée, mode Hanteurs, Hanteur, robot, joueur sans « fusions » (mod 0.47.0), cinématique (busy),
  menu / porté / porteur (nc), coureur d'une course, état vieux, départ en cours, l'un déjà fusionné, duo au repos.
* Fin (tick) : DUREE écoulée, départ, zone changée (étape ou salle), cinématique ; client : mort, L3, port perdu.
* ``relais`` (méga bombe) : l'explosion est créée dans les DEUX jeux ; chez l'autorité de la salle, elle touche les
  vrais monstres ; ailleurs elle touche des marionnettes, dont les coups ne sont relayés que si l'autorité n'est ni l'un
  ni l'autre (et alors seulement chez celui qui déplace) : jamais deux fois.
* Coup relayé d'un fusionné : ``champ_hit`` ajoute ``tenf`` (facteurs de fusion) que l'autorité écrit avant le CALL 14.

ESSAI « fusions de tenues » (``--essai-fusions``, Réglages › Essais ; à retirer après validation, tout ce qui sert à
l'essai est marqué ESSAI) : un client d'essai annonce un faux coéquipier (``essai_faux`` : position, zone) ;
``essai_tournoyante`` : il charge (20 s) ; ``essai_bombe`` : il tient une bombe (20 s) et la lance en même temps que
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

    def __init__(self, on: bool = True, essai: bool = False, clock=time.monotonic):
        self.on = bool(on)
        self.essai = bool(essai)
        self.clock = clock
        self.hanteurs = False
        self.etats: dict[int, dict] = {}      # pid -> {charge, charge_t, bombe, bombe_t, lancer_t, age, t}
        self.fusions: dict[int, dict] = {}    # id -> fusion
        self.par_joueur: dict[int, int] = {}  # pid -> id de fusion
        self.repos: dict[frozenset, float] = {}
        self.n = 0
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
            if ch and not e["charge"]:
                e["charge_t"] = now
            if not ch:
                e["charge_t"] = None
            if bo and not e["bombe"]:
                e["bombe_t"] = now
            e["charge"], e["bombe"], e["t"] = ch, bo, now
            return self.tick([pid] + [q for q in self.etats if q != pid], info, autorite, now)
        if ev == "lancer":
            e = self.etats.setdefault(pid, {"charge": False, "charge_t": None, "bombe": False, "bombe_t": None,
                                            "lancer_t": None, "age": 0.0})
            age = msg.get("age")
            e["lancer_t"] = now
            e["age"] = float(age) if isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age < 60 else 0.0
            e["bombe"], e["t"] = False, now
            return self._mega(pid, info, autorite, now)
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
                tp, tq = e.get("charge_t") or now, eq.get("charge_t") or now
                porteur = pid if tp <= tq else q
                envois += self.commencer(g, porteur, q if porteur == pid else pid, {pid: c, q: cq}, info, autorite,
                                         now)
                break
        return envois

    def _mega(self, pid, info, autorite, now) -> list:
        if not self.actif():
            return []
        e = self.etats.get(pid) or {}
        if e.get("age", 0.0) < fu.BOMBE_MURE:
            if self.essai and pid in self.essai_faux:
                self.journal.append(f"fusions : ESSAI, lancer trop tôt ({e.get('age', 0.0):.1f} s < "
                                    f"{fu.BOMBE_MURE:.1f} s) : pas de méga bombe")
            return []
        for q, j, c, cq, g in self._partenaires(pid, info, now):
            if g != fu.MEGA_BOMBE:
                continue
            if q == ID_ESSAI:
                ef = self._etat_faux(pid, now)
                if not ef.get("bombe"):
                    continue
                tq = now                            # ESSAI : le faux coéquipier lance en même temps
                age_q = fu.BOMBE_MURE
            else:
                eq = self.etats.get(q) or {}
                tq, age_q = eq.get("lancer_t"), eq.get("age", 0.0)
                if tq is None or abs(now - tq) > fu.ECART_LANCERS or age_q < fu.BOMBE_MURE:
                    continue
            porteur = q if tq < now else pid        # le premier qui a lancé déplace
            return self.commencer(g, porteur, q if porteur == pid else pid, {pid: c, q: cq}, info, autorite, now)
        return []

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
        for d in (self.etats, self.essai_faux, self.essai_role):
            d.pop(pid, None)
        return envois

    def couper(self) -> list:
        """Règle coupée ou mode Hanteurs : toutes les fusions finies."""
        envois = []
        for f in list(self.fusions.values()):
            envois += self.finir(f, "regle")
        return envois

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
                 "reste": round(max(0.0, f["fin"] - now), 1)} for f in self.fusions.values()]

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
        if ev == "essai_tournoyante":
            f.update(genre=fu.TOURNOYANTE, charge_jusqu=now + ESSAI_GESTE, charge_t=now, bombe_jusqu=-1e9)
            self.journal.append(f"fusions : ESSAI, le faux coéquipier de {i.get('nom', pid)} charge sa tournoyante")
            return ""
        if ev == "essai_bombe":
            f.update(genre=fu.MEGA_BOMBE, bombe_jusqu=now + ESSAI_GESTE, charge_jusqu=-1e9, charge_t=None)
            self.journal.append(f"fusions : ESSAI, le faux coéquipier de {i.get('nom', pid)} tient une bombe")
            return ""
        if ev == "essai_role":
            r = msg.get("role")
            if r not in (0, fu.DEPLACE, fu.ATTAQUE):
                return "rôle inconnu"
            self.essai_role[pid] = r
            return ""
        return "action inconnue"
