"""Fragments du Bonheur (Minish Cap), lot B1 de docs/etudes/autres_zelda.md §4 : logique pure du serveur, testable sans
jeu (tests/test_bonheur.py).

Règle de la PARTIE « Fragments du Bonheur », ALLUMÉE par défaut (``--no-bonheur``, case du lobby, ``regles_cmd`` à
chaud), toujours COUPÉE en mode Hanteurs. Moitiés données directement (V1 ; la moitié visible au sol, B4, n'est pas
faite).

* **Tirage à la mort d'un monstre** (instantanés « enemies » de l'autorité, même attribution que les médailles : le
  dernier « hit » reçu sur ce monstre dans les 3 s, sinon l'autorité de la zone) : 1 chance sur 12 (``CHANCE``) pour
  le joueur qui l'a vaincu, une moitié tirée parmi 8 motifs × 2 côtés. Jamais : un boss (procs et étapes de
  synchro/boss.py ; ils ne sont de toute façon jamais synchronisés), un monstre possédé, un monstre réapparu depuis
  moins de 30 s (``REAPPARU`` : pas de culture), un monstre jamais vu vivant ; jamais pour un Hanteur, le robot, un
  outil ou un client sans ``FEAT``. Poche pleine (6 moitiés, ``POCHE_MAX``) : rien.
* **Poches** par NOM de joueur, gardées dans le monde persistant (section « bonheur ») d'une séance à l'autre.
* **Assembler** (commande du client, ``bonheur {ev: "assembler"}``) : s'il y a une proposition pour ce joueur, c'est
  une acceptation ; sinon une proposition au joueur le plus proche (même salle, à moins de 3 m, ``DIST``, nom
  différent) qui a la moitié ASSORTIE (même motif, l'autre côté). L'autre reçoit ``bonheur {ev: "propose"}`` et a
  10 s (``DELAI``, +1 s de marge réseau) pour appuyer à son tour. Le serveur revérifie la distance (marge 1 m :
  ils ont pu bouger) et les deux moitiés ; il les retire, tire la récompense et l'envoie aux DEUX (``ev:
  "assemble"``, avec le point de l'assemblage : le milieu des deux).
* Annulée : l'un des deux ouvre le menu (``ev: "annule"`` de son client), délai dépassé, départ d'un des deux.
  En cinématique : refusé. Porter ou être porté : permis (aucune garde « nc » ici).
* **Récompense** : 1 % quart de cœur, 20 % fée, 79 % pluie de rubis (``RECOMPENSES``). Plafond de sécurité des quarts
  (réglable : ``quarts_donjon``, ``quarts_partie``, défaut 1 et 4) : au-delà, le tirage du quart devient des rubis.
  Un donjon = son numéro (serveur/medailles.py DONJONS), la mer et les îles comptent comme un seul « donjon » 0. Le
  quart compte pour l'assemblage (une fois), même s'il arrive aux deux. ``principal`` : le proposant ; avec une bourse
  commune (rubis) ou une vie max partagée par l'inventaire, seul son client applique (le partage fait le reste, sinon
  ce serait compté deux fois) ; sans partage, chacun reçoit la sienne.

ESSAI « Fragments du Bonheur » (``--essai-bonheur``, Réglages › Essais du lanceur ; à retirer après validation, tout
ce qui sert à l'essai est marqué ESSAI) : tirage à 1 sur 2 ; le faux coéquipier d'un client d'essai (``ID_ESSAI``,
« Essai Assorti », sur son seul PC, position envoyée par le client) a toujours la moitié assortie et accepte après
1,5 s ; il peut aussi proposer (``essai_propose``) ; ``essai_forcer`` envoie une récompense choisie. Un quart de cœur
d'ESSAI (forcé ou tiré avec le faux coéquipier) est toujours ``simule`` : le client ne touche jamais la vie max (rien
ne peut finir dans la sauvegarde). Rien de l'essai n'est écrit dans le monde persistant.
Aucune entrée/sortie : testé hors ligne.
"""
from __future__ import annotations

import random
import time

from serveur import medailles as med_mod
from synchro import boss as boss_mod
from synchro.bonheur import (CHANCE, DELAI, DIST, ESSAI_ACCEPTE, ESSAI_CHANCE, FEAT, ID_ESSAI,  # noqa: F401
                             MOTIFS, NOM_ESSAI, POCHE_MAX, QUARTS_DONJON, QUARTS_PARTIE, REAPPARU, RECOMPENSES, RUBIS,
                             distance, est_id, moitie_ok, nom_moitie, nom_motif, pos3, recompense_ok)

VERSION = 1
GRACE = 1.0                 # s de plus pour une acceptation (réseau)
MARGE = 100.0               # u : à l'acceptation, ils ont pu s'écarter un peu (3 m + 1 m)
COUP_FENETRE = 3.0          # s : dernier coup qui compte (comme les médailles)
ETAT_AGE = 5.0              # s : position plus vieille : refusé
JOUEURS_MAX = 64
NOM_MAX = 24
CLES_MAX = 256
ZONES_MAX = 64
HISTO_MAX = 32


def _nom(n) -> str:
    return str(n or "?")[:NOM_MAX]


def tirer_recompense(rng: random.Random) -> str:
    """« quart » (1 %), « fee » (20 %) ou « rubis » (79 %)."""
    r = rng.randrange(1000)
    for nom, part in RECOMPENSES:
        if r < part:
            return nom
        r -= part
    return "rubis"


def zone_split(zone) -> tuple[str | None, int | None]:
    if not isinstance(zone, str) or ":" not in zone:
        return None, None
    s, r = zone.rsplit(":", 1)
    try:
        return s, int(r)
    except ValueError:
        return s, None


class BonheurServeur:
    """``joueur(pid)`` : dict {nom, joueur, zone, busy, pos, t, feat, hanteur} ou None ; ``ids()`` : les joueurs
    connectés."""

    def __init__(self, on: bool = True, essai: bool = False, horloge=time.monotonic, graine: int | None = None,
                 quarts_donjon: int = QUARTS_DONJON, quarts_partie: int = QUARTS_PARTIE):
        self.on = bool(on)
        self.essai = bool(essai)                 # ESSAI : 1 sur 2, faux coéquipier assorti, récompenses forcées
        self.vu = False                          # changée à chaud une fois : « regles » l'annonce (welcome sinon)
        self.horloge = horloge
        self.rng = random.Random(graine)
        self.quarts_donjon = max(0, int(quarts_donjon))
        self.quarts_partie = max(0, int(quarts_partie))
        self.poches: dict[str, list[list[int]]] = {}
        self.quarts_total = 0
        self.quarts_par: dict[int, int] = {}     # donjon (0 : mer et îles) -> quarts donnés
        self.historique: list[dict] = []
        self.props: dict[int, dict] = {}         # id -> {src, dst, motif, t, src_nom, dst_nom, faux_pos, accepte_t}
        self.seq = 0
        self.coups: dict[tuple[str, str], tuple[int, float]] = {}
        self.vivants: dict[str, set] = {}
        self.morts_vus: dict[str, set] = {}
        self.morts_t: dict[tuple[str, str], float] = {}     # dernière mort comptée d'un monstre
        self.retour_t: dict[tuple[str, str], float] = {}    # revu vivant après une mort (réapparu)
        self.journal: list[str] = []
        self.changes = False                     # monde persistant à écrire
        self.rev = 0
        self.doc_relu: dict | None = None        # ESSAI : ce qui a été relu du monde, rendu tel quel
        self.n_tirages = self.n_moities = self.n_assemblages = self.n_plafonnes = 0

    # --- outils ---------------------------------------------------------------------------------------------------
    def _log(self, s: str) -> None:
        if len(self.journal) < 64:
            self.journal.append("fragments du Bonheur : " + s)

    def prendre_journal(self) -> list[str]:
        out, self.journal = self.journal, []
        return out

    def actif(self) -> bool:
        return self.on or self.essai

    def poche(self, nom: str) -> list[list[int]]:
        return [list(m) for m in self.poches.get(_nom(nom), [])]

    def _modif(self) -> None:
        self.rev += 1
        if not self.essai:
            self.changes = True

    @staticmethod
    def _ok_joueur(j: dict | None) -> str:
        """"" si ce joueur peut recevoir une moitié / assembler, sinon la raison."""
        if j is None or not j.get("joueur"):
            return "pas un joueur de jeu (robot, outil)"
        if j.get("hanteur"):
            return "Hanteur"
        if FEAT not in (j.get("feat") or ()):
            return "client sans les Fragments du Bonheur"
        return ""

    # --- monstres -------------------------------------------------------------------------------------------------
    def coup(self, zone: str, cle, pid: int) -> None:
        """Un « hit » d'un joueur sur un monstre de la zone (le dernier compte, COUP_FENETRE)."""
        if not self.actif() or not isinstance(zone, str) or cle is None or not est_id(pid):
            return
        if len(self.coups) >= CLES_MAX * 4:
            self.coups.pop(next(iter(self.coups)))
        self.coups[(zone, str(cle)[:32])] = (pid, self.horloge())

    def ennemis(self, zone: str, autorite: int, lst, joueur, possedes=()) -> list[tuple[int, dict]]:
        """Instantané « enemies » de l'autorité : chaque monstre vu vivant puis mort donne un tirage à celui qui l'a
        vaincu. Rend [(destinataire, message)]."""
        if not self.actif() or not isinstance(zone, str) or not isinstance(lst, list):
            return []
        stage, _room = zone_split(zone)
        if zone not in self.vivants and len(self.vivants) >= ZONES_MAX:
            k = next(iter(self.vivants))
            self.vivants.pop(k)
            self.morts_vus.pop(k, None)
        viv = self.vivants.setdefault(zone, set())
        vus = self.morts_vus.setdefault(zone, set())
        now = self.horloge()
        poss = {str(k)[:32] for k in (possedes or ())}
        out = []
        for e in lst[:CLES_MAX]:
            if not isinstance(e, dict) or e.get("key") is None:
                continue
            cle = str(e["key"])[:32]
            mhp, hp = e.get("max_hp"), e.get("hp")
            mort = e.get("dead") in (1, True) or (est_id(mhp) and mhp > 0 and est_id(hp) and hp <= 0)
            k = (zone, cle)
            if not mort:
                if cle in vus or (k in self.morts_t and k not in self.retour_t):
                    self.retour_t[k] = now          # revu vivant après une mort : réapparu
                if len(viv) < CLES_MAX:
                    viv.add(cle)
                vus.discard(cle)
                continue
            if cle not in viv or cle in vus:
                continue
            viv.discard(cle)
            vus.add(cle)
            par = self.coups.pop(k, None)
            qui = par[0] if par is not None and now - par[1] <= COUP_FENETRE else autorite
            avant = self.morts_t.get(k)
            retour = self.retour_t.pop(k, None)
            if len(self.morts_t) >= CLES_MAX * 8:
                self.morts_t.pop(next(iter(self.morts_t)))
            self.morts_t[k] = now
            if boss_mod.is_boss(e.get("proc") if est_id(e.get("proc")) else -1, stage):
                continue                            # jamais un boss
            if cle in poss:
                continue                            # jamais un monstre possédé
            if avant is not None and retour is not None and now - retour < REAPPARU:
                continue                            # réapparu depuis moins de 30 s : pas de culture
            m = self._tirage(qui, joueur)
            if m is not None:
                out.append(m)
        return out

    def _tirage(self, pid, joueur) -> tuple[int, dict] | None:
        j = joueur(pid) if est_id(pid) else None
        if self._ok_joueur(j):
            return None
        self.n_tirages += 1
        if self.rng.random() >= (ESSAI_CHANCE if self.essai else CHANCE):
            return None
        nom = _nom(j.get("nom"))
        poche = self.poches.setdefault(nom, [])
        if len(poche) >= POCHE_MAX:
            self._log(f"{nom} : moitié perdue, poche pleine ({POCHE_MAX})")
            return None
        if len(self.poches) > JOUEURS_MAX:
            return None
        m = [self.rng.randrange(len(MOTIFS)), self.rng.randrange(2)]
        poche.append(m)
        self.n_moities += 1
        self._modif()
        self._log(f"{nom} reçoit la moitié {nom_moitie(m)} ({len(poche)}/{POCHE_MAX})")
        return pid, {"type": "bonheur", "ev": "fragment", "motif": m[0], "cote": m[1], "nom": nom_moitie(m),
                     "poche": self.poche(nom), **({"essai": 1} if self.essai else {})}

    # --- assemblage -----------------------------------------------------------------------------------------------
    def _purger(self, now: float) -> list[tuple[int, dict]]:
        out = []
        for k, p in list(self.props.items()):
            if now - p["t"] > DELAI + GRACE:
                del self.props[k]
                out += self._annonce_annule(p, "délai dépassé (10 s)")
        return out

    def _annonce_annule(self, p: dict, raison: str, sauf: int | None = None) -> list[tuple[int, dict]]:
        out = []
        for d in (p["src"], p["dst"]):
            if d != ID_ESSAI and d != sauf:
                out.append((d, {"type": "bonheur", "ev": "annule", "id": p["id"], "raison": raison[:80]}))
        return out

    def _prop_de(self, pid: int) -> dict | None:
        return next((p for p in self.props.values() if pid in (p["src"], p["dst"])), None)

    def _cote_src(self, pr: dict) -> int | None:
        """Côté de la moitié du proposant pour cette proposition (None : les deux moitiés ne sont plus là). Le faux
        coéquipier (ESSAI) a toujours l'autre côté."""
        m = pr["motif"]
        ps = None if pr["src"] == ID_ESSAI else self.poches.get(pr["src_nom"], [])
        pd = None if pr["dst"] == ID_ESSAI else self.poches.get(pr["dst_nom"], [])
        for c in (0, 1):
            if (ps is None or [m, c] in ps) and (pd is None or [m, 1 - c] in pd):
                return c
        return None

    def _assortie(self, a: list, b: list) -> int | None:
        """Premier motif de la poche ``a`` dont ``b`` a l'autre côté."""
        for m, c in a:
            if [m, 1 - c] in b:
                return m
        return None

    def _pos(self, j: dict, now: float):
        p = pos3(j.get("pos"))
        if p is None or j.get("t") is None or now - j["t"] > ETAT_AGE:
            return None
        return p

    def assembler(self, pid: int, msg: dict, joueur, ids, now: float | None = None,
                  hanteurs: bool = False) -> tuple[str, list[tuple[int, dict]]]:
        """``bonheur {ev: "assembler"}`` de ``pid`` : acceptation ou proposition. Rend (raison du refus ou "",
        [(destinataire, message)])."""
        now = self.horloge() if now is None else now
        out = self._purger(now)
        if hanteurs:
            return "mode Hanteurs (Fragments du Bonheur coupés)", out
        if not self.actif():
            return "règle de la partie coupée", out
        j = joueur(pid)
        r = self._ok_joueur(j)
        if r:
            return r, out
        if j.get("busy"):
            return "en cinématique", out
        p_moi = self._pos(j, now)
        if p_moi is None:
            return "position inconnue ou trop vieille", out
        nom = _nom(j.get("nom"))
        essai = self.essai and msg.get("essai") in (1, True)
        faux_pos = pos3(msg.get("faux_pos")) if essai else None
        # acceptation d'une proposition pour moi
        pr = next((p for p in self.props.values() if p["dst"] == pid), None)
        if pr is not None:
            return self._accepter(pr, pid, j, p_moi, faux_pos, joueur, now, out)
        mine = next((p for p in self.props.values() if p["src"] == pid), None)
        if mine is not None:
            return f"proposition déjà en cours avec {mine['dst_nom']}", out
        poche = self.poches.get(nom, [])
        if not poche:
            return "aucune moitié en poche", out
        # le plus proche, même salle, à moins de 3 m, nom différent, moitié assortie
        proches, assortis = [], []
        for i in sorted(ids()):
            if i == pid:
                continue
            o = joueur(i)
            if self._ok_joueur(o) or o.get("zone") != j.get("zone") or _nom(o.get("nom")) == nom:
                continue
            po = self._pos(o, now)
            if po is None or distance(po, p_moi) > DIST:
                continue
            proches.append(i)
            if o.get("busy") or self._prop_de(i) is not None:
                continue
            mo = self._assortie(poche, self.poches.get(_nom(o.get("nom")), []))
            if mo is not None:
                assortis.append((distance(po, p_moi), i, o, mo))
        if faux_pos is not None:                         # ESSAI : le faux coéquipier a toujours la moitié assortie
            d = distance(faux_pos, p_moi)
            if d <= DIST:
                proches.append(ID_ESSAI)
                assortis.append((d, ID_ESSAI, {"nom": NOM_ESSAI}, poche[0][0]))
        if not assortis:
            if not proches:
                extra = ""
                if faux_pos is not None:
                    extra = f" (le faux coéquipier est à {distance(faux_pos, p_moi) / 100:.1f} m)"
                return "personne à moins de 3 m" + extra, out
            return "aucune moitié assortie à moins de 3 m", out
        assortis.sort(key=lambda a: (a[0], a[1]))
        _d, dst, o, motif = assortis[0]
        self.seq += 1
        p = {"id": self.seq, "src": pid, "dst": dst, "motif": motif, "t": now, "src_nom": nom,
             "dst_nom": _nom(o.get("nom")), "faux_pos": faux_pos if dst == ID_ESSAI else None,
             "accepte_t": now + ESSAI_ACCEPTE if dst == ID_ESSAI else None, "essai": dst == ID_ESSAI}
        self.props[p["id"]] = p
        c_src = self._cote_src(p) or 0
        if dst != ID_ESSAI:
            out.append((dst, {"type": "bonheur", "ev": "propose", "id": p["id"], "from": pid, "nom": nom,
                              "motif": motif, "cote": 1 - c_src, "delai": DELAI}))
        out.append((pid, {"type": "bonheur", "ev": "attente", "id": p["id"], "vers": dst, "nom": p["dst_nom"],
                          "motif": motif, "delai": DELAI, **({"essai": 1} if p["essai"] else {})}))
        self._log(f"{nom} propose d'assembler {nom_motif(motif)} à {p['dst_nom']}"
                  + (" (ESSAI, faux coéquipier)" if p["essai"] else ""))
        return "", out

    def _accepter(self, pr: dict, pid: int, j: dict, p_moi, faux_pos, joueur, now: float,
                  out: list) -> tuple[str, list]:
        if now - pr["t"] > DELAI + GRACE:
            del self.props[pr["id"]]
            return "trop tard (10 s)", out + self._annonce_annule(pr, "délai dépassé (10 s)")
        if pr["src"] == ID_ESSAI:                        # ESSAI : le faux coéquipier proposait
            ps = pos3(faux_pos) or pr["faux_pos"]
            o = {"nom": NOM_ESSAI}
        else:
            o = joueur(pr["src"])
            if self._ok_joueur(o):
                del self.props[pr["id"]]
                return "le proposant n'est plus là", out
            ps = self._pos(o, now)
            if o.get("zone") != j.get("zone"):
                ps = None
        if ps is None or distance(ps, p_moi) > DIST + MARGE:
            del self.props[pr["id"]]
            return "trop loin l'un de l'autre", out + self._annonce_annule(pr, "trop loin l'un de l'autre", sauf=pid)
        if j.get("busy"):
            return "en cinématique", out
        if self._cote_src(pr) is None:
            del self.props[pr["id"]]
            return "moitiés plus en poche", out + self._annonce_annule(pr, "moitiés plus en poche", sauf=pid)
        return "", out + self._finir(pr, ps, p_moi, j, now)

    def _finir(self, pr: dict, ps, pd, jd: dict, now: float, forcee: str | None = None) -> list[tuple[int, dict]]:
        """Retire les deux moitiés, tire la récompense, l'envoie aux deux."""
        self.props.pop(pr["id"], None)
        m = pr["motif"]
        essai = pr.get("essai") or forcee is not None
        if forcee is None:
            cs = self._cote_src(pr)
            for qui, pid, c in ((pr["src_nom"], pr["src"], cs), (pr["dst_nom"], pr["dst"], 1 - cs)):
                po = self.poches.get(qui, [])
                if pid != ID_ESSAI and [m, c] in po:
                    po.remove([m, c])
        stage, room = zone_split(jd.get("zone"))
        rec = forcee or tirer_recompense(self.rng)
        simule = False
        if rec == "quart":
            if essai:
                simule = True                        # ESSAI : jamais la vie max (rien dans la sauvegarde)
            else:
                d = med_mod.donjon_de(stage) or 0
                if self.quarts_total >= self.quarts_partie or self.quarts_par.get(d, 0) >= self.quarts_donjon:
                    self.n_plafonnes += 1
                    self._log(f"quart de cœur tiré mais plafonné ({self.quarts_total}/{self.quarts_partie} par partie, "
                              f"{self.quarts_par.get(d, 0)}/{self.quarts_donjon} ici) : pluie de rubis à la place")
                    rec = "rubis"
                else:
                    self.quarts_total += 1
                    self.quarts_par[d] = self.quarts_par.get(d, 0) + 1
        n_rubis = self.rng.choice(RUBIS) if rec == "rubis" else 0
        centre = [round((a + b) / 2.0, 1) for a, b in zip(ps, pd)]
        if not essai:
            self.n_assemblages += 1
        self.historique.append({"t": int(time.time()), "a": pr["src_nom"], "b": pr["dst_nom"], "motif": m,
                                "recompense": rec, "rubis": n_rubis, **({"essai": 1} if essai else {}),
                                **({"simule": 1} if simule else {})})
        del self.historique[:-HISTO_MAX]
        self._modif()
        self._log(f"{pr['src_nom']} et {pr['dst_nom']} assemblent {nom_motif(m)} : "
                  + {"quart": "quart de cœur" + (" (ESSAI : simulé)" if simule else ""), "fee": "fée",
                     "rubis": f"pluie de rubis ({n_rubis})"}[rec] + (" (ESSAI)" if essai else ""))
        out = []
        for pid, avec, principal in ((pr["src"], pr["dst_nom"], 1), (pr["dst"], pr["src_nom"], 0)):
            if pid == ID_ESSAI:
                continue
            if pr["src"] == ID_ESSAI:
                principal = 1                        # ESSAI : seul le vrai joueur applique
            qui = pr["src_nom"] if pid == pr["src"] else pr["dst_nom"]
            out.append((pid, {"type": "bonheur", "ev": "assemble", "id": pr["id"], "avec": avec, "motif": m,
                              "recompense": rec, "rubis": n_rubis, "pos": centre, "stage": stage, "room": room,
                              "principal": principal, "poche": self.poche(qui) if forcee is None else None,
                              **({"essai": 1} if essai else {}), **({"simule": 1} if simule else {})}))
        return out

    def annuler(self, pid: int, raison: str = "menu ouvert") -> list[tuple[int, dict]]:
        """``bonheur {ev: "annule"}`` (le client a ouvert le menu) : ses propositions sont annulées pour les deux."""
        out = []
        for k, p in list(self.props.items()):
            if pid in (p["src"], p["dst"]):
                del self.props[k]
                out += self._annonce_annule(p, raison)
                self._log(f"assemblage {p['src_nom']} / {p['dst_nom']} annulé ({raison})")
        return out

    def oublier(self, pid: int) -> list[tuple[int, dict]]:
        """Un joueur part : ses propositions sont annulées (l'autre est prévenu)."""
        out = []
        for k, p in list(self.props.items()):
            if pid in (p["src"], p["dst"]):
                del self.props[k]
                out += self._annonce_annule(p, "un joueur est parti", sauf=pid)
        return out

    def tick(self, joueur, now: float | None = None) -> list[tuple[int, dict]]:
        """Délais dépassés ; ESSAI : le faux coéquipier accepte après ESSAI_ACCEPTE s."""
        now = self.horloge() if now is None else now
        out = []
        for p in list(self.props.values()):
            if p["dst"] == ID_ESSAI and p.get("accepte_t") is not None and now >= p["accepte_t"]:
                j = joueur(p["src"])
                pj = self._pos(j, now) if j is not None else None
                if pj is None or distance(pj, p["faux_pos"]) > DIST + MARGE:
                    del self.props[p["id"]]
                    out += self._annonce_annule(p, "trop loin du faux coéquipier")
                else:
                    out += self._finir(p, p["faux_pos"], pj, j, now)
        return out + self._purger(now)

    # --- ESSAI ----------------------------------------------------------------------------------------------------
    def essai_proposer(self, pid: int, msg: dict, joueur, now: float | None = None) -> tuple[str, list]:
        """ESSAI : le faux coéquipier propose à ``pid`` (s'il n'a aucune moitié, il en reçoit une d'essai)."""
        now = self.horloge() if now is None else now
        out = self._purger(now)
        if not self.essai:
            return "pas un serveur d'essai", out
        j = joueur(pid)
        r = self._ok_joueur(j)
        if r:
            return r, out
        fp, pj = pos3(msg.get("faux_pos")), self._pos(j, now)
        if fp is None or pj is None:
            return "position inconnue", out
        if distance(fp, pj) > DIST:
            return f"approche-toi du faux coéquipier (à {distance(fp, pj) / 100:.1f} m, 3 m au plus)", out
        if self._prop_de(pid) is not None:
            return "proposition déjà en cours", out
        nom = _nom(j.get("nom"))
        poche = self.poches.setdefault(nom, [])
        if not poche:
            m = [self.rng.randrange(len(MOTIFS)), self.rng.randrange(2)]
            poche.append(m)
            self.rev += 1
            out.append((pid, {"type": "bonheur", "ev": "fragment", "motif": m[0], "cote": m[1], "nom": nom_moitie(m),
                              "poche": self.poche(nom), "essai": 1}))
        motif, cote = poche[0]
        self.seq += 1
        p = {"id": self.seq, "src": ID_ESSAI, "dst": pid, "motif": motif, "t": now, "src_nom": NOM_ESSAI,
             "dst_nom": nom, "faux_pos": fp, "accepte_t": None, "essai": True}
        self.props[p["id"]] = p
        out.append((pid, {"type": "bonheur", "ev": "propose", "id": p["id"], "from": ID_ESSAI, "nom": NOM_ESSAI,
                          "motif": motif, "cote": cote, "delai": DELAI, "essai": 1}))
        self._log(f"ESSAI : le faux coéquipier propose d'assembler {nom_motif(motif)} à {nom}")
        return "", out

    def essai_forcer(self, pid: int, msg: dict, joueur, now: float | None = None) -> tuple[str, list]:
        """ESSAI : une récompense choisie (``recompense``), sans toucher aux poches ; quart toujours simulé."""
        now = self.horloge() if now is None else now
        if not self.essai:
            return "pas un serveur d'essai", []
        rec = msg.get("recompense")
        if not recompense_ok(rec):
            return "récompense inconnue", []
        j = joueur(pid)
        r = self._ok_joueur(j)
        if r:
            return r, []
        pj = self._pos(j, now)
        if pj is None:
            return "position inconnue", []
        fp = pos3(msg.get("faux_pos")) or pj
        self.seq += 1
        pr = {"id": self.seq, "src": pid, "dst": ID_ESSAI, "motif": self.rng.randrange(len(MOTIFS)), "t": now,
              "src_nom": _nom(j.get("nom")), "dst_nom": NOM_ESSAI, "essai": True}
        return "", self._finir(pr, fp, pj, j, now, forcee=rec)

    # --- vue et monde persistant ----------------------------------------------------------------------------------
    def vue(self, noms=()) -> dict:
        """Ce que montre le trackeur (bloc « Fragments ») : poches, derniers assemblages, plafond des quarts."""
        tous = list(dict.fromkeys([_nom(n) for n in noms] + sorted(self.poches)))[:JOUEURS_MAX]
        return {"v": VERSION, "rev": self.rev, "on": 1 if self.on else 0, "essai": 1 if self.essai else 0,
                "motifs": list(MOTIFS), "poches": [{"nom": n, "moities": self.poche(n)} for n in tous],
                "historique": [dict(h) for h in self.historique[-16:]],
                "quarts": {"total": self.quarts_total, "partie": self.quarts_partie, "donjon": self.quarts_donjon},
                "propositions": len(self.props)}

    def exporter(self) -> dict | None:
        """Section « bonheur » du monde persistant (jamais l'essai : ce qui avait été relu est rendu tel quel)."""
        if self.essai:
            return self.doc_relu
        return {"v": VERSION, "poches": {n: [list(m) for m in p] for n, p in self.poches.items() if p},
                "quarts": {"total": self.quarts_total, "donjons": {str(d): n for d, n in self.quarts_par.items()}},
                "historique": [h for h in self.historique if not h.get("essai")]}

    def restaurer(self, doc) -> int:
        """Relit la section du monde (entrées abîmées ignorées) ; rend le nombre d'entrées ignorées."""
        if not isinstance(doc, dict):
            return 0
        self.doc_relu = doc if self.essai else None
        bad = 0
        poches = {}
        pc = doc.get("poches")
        for n, lst in (pc.items() if isinstance(pc, dict) else []):
            if not isinstance(n, str) or not n or not isinstance(lst, list) or len(poches) >= JOUEURS_MAX:
                bad += 1
                continue
            ok = [[m[0], m[1]] for m in lst[:POCHE_MAX] if moitie_ok(m)]
            bad += len(lst) - len(ok)
            if ok:
                poches[n[:NOM_MAX]] = ok
        q = doc.get("quarts") if isinstance(doc.get("quarts"), dict) else {}
        tot = q.get("total")
        par = {}
        dj = q.get("donjons")
        for k, v in (dj.items() if isinstance(dj, dict) else []):
            try:
                d = int(k)
            except (TypeError, ValueError):
                bad += 1
                continue
            if est_id(v) and 0 <= v < 1000 and (d == 0 or d in med_mod.DONJONS):
                par[d] = v
            else:
                bad += 1
        histo = []
        for h in (doc.get("historique") if isinstance(doc.get("historique"), list) else [])[-HISTO_MAX:]:
            if (isinstance(h, dict) and recompense_ok(h.get("recompense")) and est_id(h.get("motif"))
                    and 0 <= h["motif"] < len(MOTIFS)):
                histo.append({"t": h["t"] if est_id(h.get("t")) else 0, "a": _nom(h.get("a")), "b": _nom(h.get("b")),
                              "motif": h["motif"], "recompense": h["recompense"],
                              "rubis": h["rubis"] if est_id(h.get("rubis")) and 0 <= h["rubis"] <= 5000 else 0})
            else:
                bad += 1
        self.poches = poches
        self.quarts_total = tot if est_id(tot) and 0 <= tot < 1000 else sum(par.values())
        self.quarts_par = par
        self.historique = histo
        self.rev += 1
        return bad
