"""Mode Hanteurs, phase 2 : potion de possession d'un Link (variante « clone ») et PvP Link contre Link.

Étude : docs/etudes/mode_hanteurs.md §10 (variante P2 « clone », recommandée) et §11 phase 2 ; la section
« Vérification » finale prime (point 9 : le PvP écrit la vie, contre la règle de COMBAT §6b, acceptable seulement
dans ce mode jetable et avec un plancher). Client : ``--share-potion`` (activé par ``--coop-donjon``, demande le mode
Hanteurs), ``--no-potion`` pour couper (ce PC ne boit pas, n'est jamais possédé et ne frappe personne : fonction
« potion » absente du ``hello``) ; ``--pvp-natif`` (DÉSACTIVÉ par défaut) : coups PvP rejoués par CALL 9 du pack v19
avec le fantôme de l'attaquant comme propriétaire (recul, invincibilité, garde, fée natifs), au lieu de la vie écrite.
Serveur : réglages ``potion`` (case du lobby, cochée par défaut avec ``--mode-hanteurs``), ``potion_duree`` (10-60 s,
20 par défaut) et ``pvp_degats`` (1-8 quarts par coup d'épée, 2 par défaut).

Ce que les joueurs voient. Un Hanteur qui a assez de points (150 × barème) « boit la potion » sur un Link de SA salle
(bouton du lobby). Pendant ``potion_duree`` secondes :

* le **Link possédé** disparaît de son propre écran (Link caché, comme un Hanteur) et devient spectateur de son
  corps : son Link caché suit le clone (téléportation dans la salle, au sol, au-delà de 400 u) et il voit le clone
  (le fantôme du Hanteur, avec SON équipement) ; il peut **résister** (F4, ou bouton « Résister ») : 5 appuis = 1 s de
  moins ;
* le **Hanteur** réapparaît (son Link n'est plus caché) **à la place du possédé** (téléporté sur sa dernière position
  au sol) et joue avec son propre Link : aucune latence pour lui ;
* les **autres Link** voient le fantôme du possédé bouger comme le Hanteur le joue (le serveur relaie l'état du
  Hanteur sous l'identité du possédé, avec l'équipement du possédé) ; une note dit « X est possédé ! ». Le clone
  peut les **frapper à l'épée** (PvP) ; eux peuvent le frapper aussi : **3 coups** l'exorcisent (fin de la potion) ;
* fin (durée, exorcisme, F4 du Hanteur, salle quittée, plus aucun autre Link dans la salle, départ, changement de
  rôle, réglage coupé) : le Hanteur est de nouveau caché, le possédé réapparaît **là où était le clone** (dernière
  position au sol vue par le serveur, même salle) et perd les dégâts reçus par le clone (plancher : 1 cœur).

PvP (§10.2). Le jeu ne prévoit pas qu'un Link frappe un Link, et le TG du fantôme (A1, pack v19) n'est croisé que par
les AT « VsPlayer » des monstres : l'épée de Link (« VsEnemy | VsOther ») ne le touche jamais. Donc :

1. **détection Python** chez l'attaquant : la lame de son Link (matrices de l'épée, ``SWORD_FIELD``) passe vite
   (400 u/s) à moins de 90 u du centre d'un fantôme visé (``touches_lame``) -> ``pvp {to, zone}`` ;
2. **serveur** : seulement clone -> Link de la même salle (pas le possédé, pas en cinématique, à moins de
   ``PVP_DIST`` d'après les positions du serveur, au plus ``PVP_RATE`` par seconde et par paire) ou Link -> clone
   (exorcisme, mêmes contrôles) ; tout le reste est jeté (pas de tir ami) ;
3. **victime** : par défaut la **vie écrite** (u16 gameInfo+0x22, relue juste avant, jamais vers le haut, plancher 1
   quart : jamais de game over écrit, une fois toutes les ``PVP_INVULN`` s, jamais en événement), sans recul ni fée ;
   avec ``--pvp-natif`` et le pack v19 (capacité « fantôme touchable ») : CALL 9 ``coup_link`` (AT épée, type 0x2,
   ``Atp`` = dégâts, propriétaire = le fantôme du clone chez la victime, jamais un faux acteur) : le jeu fait tout
   (garde au bouclier, recul, invincibilité, fée, game over). Désactivé par défaut : un fantôme ``WwmpLink`` comme
   propriétaire d'une attaque contre Link n'a jamais été vu en jeu [HYPO, test PO9 du plan].

Ce qui n'est PAS fait (et pourquoi) : un TG du fantôme qui accepte l'épée (« masque PvP » de l'étude) demanderait un
changement de l'asm (TG ``IsOther`` + filtre de l'attaquant dans la file des coups) non vérifiable sans essai : le
pack v19 n'est pas modifié ; injection des entrées de manette (P1, étude §10.1 : structure HD inconnue) ; le clone
qui « clignote » (dessin par emplacement, asm) : remplacé par la note « X est possédé ! » et le panneau du lobby.

Inventaire et argent du possédé (étude docs/etudes/possession_inventaire.md ; client ``--share-depense``, activé par
``--coop-donjon``, ``--no-depense`` pour couper ; réglages du lobby ``potion_depense`` et ``potion_plafond``). Avec le
lanceur, les objets sont déjà ceux de toute l'équipe (union, synchro/inventaire.py) et les rubis, flèches et bombes sont
un POT COMMUN (synchro/compteurs.py) : le jeu du Hanteur porte donc l'inventaire du possédé. Pendant la potion, le jeu
du Hanteur est recalé sur le pot commun, puis ses DÉPENSES (rubis, flèches, bombes : ``DEPENSE_KS``, rien d'autre)
sont retirées du pot : le possédé et son équipe les perdent vraiment. Ses gains ne sont jamais comptés, ses objets,
clés, coffres et faits de progression restent bloqués (``hanteurs.MONDE_BLOQUE``). ``potion_depense`` : « rien »,
« tout » (par défaut) ou « plafond » (``potion_plafond`` rubis et ``PLAFOND_MUNITIONS`` flèches et bombes au plus par
possession). Avec ce réglage (hors « rien »), la potion ne demande plus un AUTRE Link dans la salle (un Hanteur peut
posséder un Link seul, pour dépenser) ; ``potion_resister`` (décoché par défaut : le possédé regarde, durée fixe)
remet la touche « Résister ».
"""
from __future__ import annotations

import math
import threading
import time

COUT = 150                  # points (× multiplicateur du barème)
DUREE = 20                  # s (réglage ``potion_duree``)
DUREE_MIN, DUREE_MAX = 10, 60
RECHARGE = 120.0            # s par Hanteur, après la fin d'une potion
VIE_MIN = 8                 # quarts : cible refusée sous 2 cœurs (étude §10.3)
PLANCHER = 4                # quarts : les dégâts reçus par le clone ne descendent jamais le possédé sous 1 cœur
ECHANGE_RECENT = 10.0       # s : cible refusée juste après un échange de rôle
EXORCISME = 3               # coups d'épée d'un Link sur le clone
EXO_GAP = 0.5               # s entre deux coups comptés d'un même Link
RESIST_APPUIS = 5           # appuis du possédé pour 1 s de moins
RESIST_GAIN = 1.0
RESIST_RATE = 10            # appuis comptés au plus par seconde
PVP_DEGATS = 2              # quarts par coup d'épée (réglage ``pvp_degats``, 1-8)
PVP_RATE = 4                # coups relayés au plus par seconde et par paire (comme player_hit)
PVP_DIST = 350.0            # u : positions (serveur) de l'attaquant et de la victime plus éloignées : refusé
# LANCER (2026-11-19) : la potion est lancée comme un projectile guidé ; pendant VOL s, rien ne change encore (le Link
# joue toujours son Link) : chaque PC de la salle montre la volute qui vole du Hanteur au Link et entre en lui
# (« hpotion_lancer »), et le PC du Link visé le fait RECULER à FRAPPE s (vrai recul du jeu, comme les coups entre
# joueurs : CALL 9, Atp 0, aucun dégât), vu de tous par son fantôme ; puis la possession commence (revalidée).
VOL = 2.0                   # s entre le lancer et la possession
FRAPPE = 0.85               # s : la volute touche le Link (client : recul)
PVP_INVULN = 1.0            # s entre deux coups appliqués chez la victime (vie écrite : pas de mDamageWaitTimer)
PVP_FRESH = 1.0             # s : coup reçu plus vieux -> jeté
PVP_ESQUIVE = 450.0         # u : Link de la victime plus loin de l'attaquant -> esquivé (latence comprise)
DEGATS_MAX = 80             # quarts au plus gardés pour le possédé
SEUL = 3.0                  # s sans autre Link dans la salle : fin
# dépenses du clone retirées du pot commun du possédé (liste blanche : rien d'autre ne baisse jamais)
DEPENSE_KS = ("r", "a", "b")           # rubis, flèches, bombes (compteurs communs, wwmp/compteurs.KS)
DEPENSE_NOMS = {"r": "rubis", "a": "flèche(s)", "b": "bombe(s)"}
DEPENSES = ("rien", "tout", "plafond")  # réglage ``potion_depense``
DEPENSE_DEFAUT = "tout"
PLAFOND = 200               # rubis au plus par possession (réglage ``potion_plafond``, mode « plafond »)
PLAFOND_MIN, PLAFOND_MAX = 10, 5000
PLAFOND_MUNITIONS = 10      # flèches et bombes au plus par possession (mode « plafond »)
ETAT_AGE = 3.0              # s : âge maximal de la position d'un joueur pour décider
SOL_FENETRE, SOL_DY = 0.6, 4.0
QUARANTAINE_LIBERE = 3.0    # s : après la libération, rien de « monde » n'est accepté du Link libéré (serveur)
LIBERATION_FENETRE = 15.0   # s : baisse de vie de la libération attendue (JEU_ATTENTE du client + marge)
HIT_RADIUS = 90.0           # u : lame -> centre du fantôme (comme detect_hits)
CENTRE_Y = 60.0             # u : centre du corps au-dessus des pieds
AT_EPEE, SE_EPEE, MARQUE_EPEE = 0x2, 1, 0xD     # COMBAT.md §1.2 : AT de la lame de Link [LIVE]


def cout_potion(cfg: dict, presets: dict) -> int:
    return max(1, int(round(COUT * presets[cfg["points"]]["cout"])))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pos3(v) -> tuple[float, float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(_num(x) for x in v):
        return None
    p = tuple(float(x) for x in v)
    return p if all(abs(x) < 1e6 for x in p) else None


def sol_stable(hist, now: float) -> bool:
    """``hist`` : [(heure, y)] d'un joueur. Vrai s'il est « au sol » : au moins 3 relevés dans les SOL_FENETRE
    dernières secondes, dont un assez vieux, et une hauteur qui n'a pas bougé de plus de SOL_DY."""
    ys = [y for t, y in hist if now - t <= SOL_FENETRE]
    vieux = [t for t, _y in hist if now - t >= SOL_FENETRE - 0.2]
    return len(ys) >= 3 and bool(vieux) and max(ys) - min(ys) <= SOL_DY


def touches_lame(blade, cibles: dict, rayon: float = HIT_RADIUS) -> list:
    """Cibles (joueur -> centre (x, y, z)) dont le centre est à moins de ``rayon`` d'un point de la lame
    (liste de points (x, y, z)). Calcul pur (testé hors ligne)."""
    out = []
    pts = [tuple(float(c) for c in p) for p in blade]
    for j in sorted(cibles):
        c = _pos3(cibles[j])
        if c is None:
            continue
        if any(math.dist(p, c) < rayon for p in pts):
            out.append(j)
    return out


def vie_pvp(life: int, dmg: int) -> int:
    """Vie écrite par un coup PvP : jamais au-dessus, jamais sous 1 quart (pas de game over écrit)."""
    if life <= 1 or dmg <= 0:
        return life
    return max(1, life - dmg)


def vie_liberation(life: int, degats: int) -> int:
    """Vie du possédé à la fin : baissée des dégâts reçus par le clone, jamais sous PLANCHER (s'il était déjà plus
    bas, inchangée), jamais vers le haut."""
    if degats <= 0 or life <= PLANCHER:
        return life
    return max(PLANCHER, life - degats)


def texte_depense(dep) -> str:
    """{"r": 120, "a": 3} -> « 120 rubis, 3 flèche(s) » ("" : rien). Valeurs non entières ou négatives ignorées."""
    return ", ".join(f"{v} {DEPENSE_NOMS[k]}" for k, v in lire_depense(dep).items())


def lire_depense(dep) -> dict:
    """Champ ``depense`` (cumul par compteur), nettoyé : clés de la liste blanche, entiers de 1 à 99999."""
    if not isinstance(dep, dict):
        return {}
    return {k: min(99999, dep[k]) for k in DEPENSE_KS
            if isinstance(dep.get(k), int) and not isinstance(dep.get(k), bool) and dep[k] > 0}


def etat_clone(out: dict, ident: int, nom: str, spec, clone: bool) -> dict:
    """State du Hanteur relayé comme celui d'un autre joueur : identité ``ident`` / ``nom``, équipement ``spec``
    (accessoires du possédé : le fantôme a SON apparence). ``clone`` : marque pour le possédé (son client montre ce
    fantôme bien que ce soit un Hanteur). Le champ ``clone`` venu du client n'est jamais gardé."""
    m = {k: v for k, v in out.items() if k != "clone"}
    m["id"], m["name"] = ident, nom
    att = m.get("att")
    if isinstance(spec, list) and isinstance(att, dict):
        m["att"] = dict(att, spec=list(spec))
    if clone:
        m["clone"] = 1
    return m


# =========================================================================================================
# Serveur : mêlé à hanteurs.Partie (rôles, points, sortie). Aucune entrée/sortie.
# =========================================================================================================
class PotionPartie:
    """Mixin de ``hanteurs.Partie`` : potions en cours (Hanteur -> {cible, ...}), PvP, résistance, exorcisme."""

    def _potion_init(self) -> None:
        self.potions: dict[int, dict] = {}        # Hanteur -> {cible, zone, t0, fin, exo, degats, sol, ...}
        self.pvp_t: dict[tuple[int, int], list[float]] = {}
        self.exo_t: dict[int, float] = {}
        self.resist_t: dict[int, list[float]] = {}
        self.potion_t: dict[int, list[float]] = {}
        self.vie_ignore: dict[int, list] = {}     # libéré -> [fin, quarts] : baisse de la libération pas comptée
        self.n_potions = self.n_pvp = self.n_pvp_refus = 0
        self.lancers: dict[int, dict] = {}        # Hanteur -> {cible, t, a_potion} : potion en vol (VOL s)

    # --- requêtes ---------------------------------------------------------------------------------------
    def possede_par(self, pid) -> int | None:
        """Hanteur qui possède le Link ``pid`` (potion), ou None."""
        for h, po in self.potions.items():
            if po["cible"] == pid:
                return h
        return None

    def hors_jeu(self, pid) -> bool:
        """Jamais l'autorité d'une zone : Hanteur, ou Link possédé (son Link est caché)."""
        return self.est_hanteur(pid) or self.possede_par(pid) is not None

    def possedes(self) -> list[int]:
        return sorted(po["cible"] for po in self.potions.values())

    def _hist(self, pid: int, y: float, now: float) -> None:
        e = self.etat.setdefault(pid, {})
        h = [x for x in e.get("ys", []) if now - x[0] <= SOL_FENETRE + 0.5]
        h.append((now, y))
        e["ys"] = h[-40:]

    # --- boire ------------------------------------------------------------------------------------------
    def boire(self, pid: int, msg: dict, a_potion, now: float | None = None) -> str:
        """``hpotion {cible?}`` d'un Hanteur. ``a_potion(id)`` : le client de ce joueur sait faire (fonction
        « potion » du hello). Renvoie "" (accepté) ou la raison du refus (``hrefus`` avec ``quoi: potion``)."""
        now = self.clock() if now is None else now
        raison, cible = self._refus_potion(pid, msg, a_potion, now)
        if raison:
            self.n_refus += 1
            self.sortie.append((pid, {"type": "hrefus", "quoi": "potion", "raison": raison}))
            return raison
        if VOL > 0:                           # la potion vole d'abord (VOL s) : la possession commence ensuite
            e, ec = self.etat[pid], self.etat[cible]
            self.lancers[pid] = {"cible": cible, "t": now, "a_potion": a_potion}
            self.sortie.append((("zone", e["zone"]), {
                "type": "hpotion_lancer", "par": pid, "cible": cible, "de": [round(v, 1) for v in e["pos"]],
                "vers": [round(v, 1) for v in ec["pos"]], "stage": e["stage"], "room": e["room"],
                "vol": VOL, "frappe": FRAPPE}))
            self.log(f"Hanteurs : {self.noms.get(pid, pid)} lance la potion sur {self.noms.get(cible, '?')}")
            return ""
        return self._commencer(pid, cible, now)

    def _lancers_tick(self, now: float) -> None:
        """Potions en vol arrivées : la possession commence si elle est toujours permise (sinon refus au Hanteur)."""
        for pid, la in list(self.lancers.items()):
            if now - la["t"] < VOL:
                continue
            del self.lancers[pid]
            raison, cible = self._refus_potion(pid, {"cible": la["cible"]}, la["a_potion"], now)
            if raison:
                self.n_refus += 1
                self.sortie.append((pid, {"type": "hrefus", "quoi": "potion", "raison": f"la potion a manqué : {raison}"}))
                continue
            self._commencer(pid, cible, now)

    def _commencer(self, pid: int, cible: int, now: float) -> str:
        """La possession commence (après le vol de la potion)."""
        if pid in self.possessions:
            self.lacher(pid, "tu bois la potion", now)
        e, ec = self.etat[pid], self.etat[cible]
        cout = cout_potion(self.cfg, self._presets())
        self.points[pid] -= cout
        po = {"cible": cible, "zone": e["zone"], "t0": now, "fin": now + self.cfg["potion_duree"], "exo": 0,
              "degats": 0, "sol": tuple(ec["pos"]), "resist": 0, "seul": None, "cout": cout,
              "depense": {k: 0 for k in DEPENSE_KS}, "refuse": 0, "gains": 0}
        self.potions[pid] = po
        self.n_potions += 1
        # le Link possédé n'agit plus sur le monde (hanteurs.Partie.bloque_monde) ; ses inscriptions futures ne
        # porteront pas l'état de son jeu (ce que son Link caché aurait ramassé avec un pack sans « Link gelé »)
        self.anciens.add(cible)
        nom = self.noms.get(cible, "?")
        self.sortie.append((pid, {"type": "hpotion_debut", "cible": cible, "nom": nom,
                                  "duree": self.cfg["potion_duree"], "pos": [round(v, 1) for v in ec["pos"]],
                                  "stage": e["stage"], "room": e["room"], "depense": self.depense_reglee()}))
        self.sortie.append((cible, {"type": "hpossede_link", "par": pid, "duree": self.cfg["potion_duree"],
                                    "depense": self.depense_reglee(),
                                    "resister": int(bool(self.cfg["potion_resister"]))}))
        # grande fumée sur le Link visé, pour les autres joueurs de la salle (pas pour lui : son jeu est en train
        # de lui être pris, Link caché et déplacé)
        self._fx("potion", e["zone"], ec["pos"], cible=cible, sauf=cible)
        suite = (" ; il peut dépenser les rubis, les flèches et les bombes de l'équipe"
                 if self.depense_reglee() != "rien" else "")
        for o in self._liens():
            if o != cible:
                self.sortie.append((o, {"type": "hnote", "msg": f"{nom} est possédé par un Hanteur ! Frappe-le "
                                        f"{EXORCISME} fois à l'épée pour l'exorciser{suite}"}))
        self.changes.append(cible)            # le serveur réattribue l'autorité de sa zone (jamais un possédé)
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : {self.noms.get(pid, pid)} boit la potion et possède {nom} (-{cout} points)")
        return ""

    def _presets(self) -> dict:
        from synchro.hanteurs import PRESETS
        return PRESETS

    def _refus_potion(self, pid: int, msg: dict, a_potion, now: float) -> tuple[str, int | None]:
        if not self.repartie:
            return "la partie n'a pas commencé", None
        if not self.est_hanteur(pid):
            return "seuls les Hanteurs boivent la potion", None
        if not self.cfg["potion"]:
            return "potion coupée dans cette partie", None
        t = [x for x in self.potion_t.get(pid, []) if now - x < 1.0]
        if len(t) >= 2:
            self.potion_t[pid] = t
            return "trop de demandes", None
        t.append(now)
        self.potion_t[pid] = t
        if pid in self.potions:
            return "tu joues déjà un Link", None
        if self.lancers:
            return "une potion est déjà lancée (une à la fois)", None
        if self.potions:
            return "une potion est déjà en cours dans la partie (une à la fois)", None
        if not a_potion(pid):
            return "tes outils ne savent pas jouer un Link (mets-les à jour, ou --no-potion)", None
        e = self.etat.get(pid) or {}
        zone, pos = e.get("zone"), e.get("pos")
        if zone is None or pos is None or e.get("stage") is None or now - e.get("t", 0) > ETAT_AGE:
            return "position inconnue (jeu pas prêt ?)", None
        from synchro import boss, cibles
        if e["stage"] in cibles.STAGES_EXCLUES or e["stage"] in boss.BOSS_STAGES:
            return "pas de potion dans cette stage", None
        if e.get("busy"):
            return "tu es en cinématique", None
        if e.get("ship"):
            return "pas en bateau", None
        r = self.recharges.get((pid, "potion"), 0.0) - now
        if r > 0:
            return f"potion : recharge, encore {r:.0f} s", None
        cout = cout_potion(self.cfg, self._presets())
        if self.points.get(pid, 0.0) < cout:
            return f"pas assez de points ({int(self.points.get(pid, 0.0))}/{cout})", None
        liens = [o for o in self._liens() if (self.etat.get(o) or {}).get("zone") == zone
                 and now - (self.etat.get(o) or {}).get("t", 0) <= ETAT_AGE]
        voulu = msg.get("cible")
        if isinstance(voulu, int) and not isinstance(voulu, bool):
            if voulu not in liens:
                return f"{self.noms.get(voulu, '?')} n'est pas un Link de ta salle", None
            cible = voulu
        else:
            if not liens:
                return "aucun Link dans ta salle", None
            cible = min(liens, key=lambda o: (math.dist(self.etat[o].get("pos") or pos, pos), o))
        ec = self.etat[cible]
        nom = self.noms.get(cible, "?")
        if len([o for o in liens if o != cible]) == 0 and not self.link_seul_permis():
            return f"aucun autre Link dans ta salle (le clone de {nom} n'aurait personne à combattre)", None
        if not a_potion(cible):
            return f"les outils de {nom} ne savent pas être possédés (pas à jour, ou --no-potion)", None
        if ec.get("pos") is None:
            return f"position de {nom} inconnue", None
        if ec.get("busy"):
            return f"{nom} est en cinématique", None
        if ec.get("ship"):
            return f"{nom} est en bateau", None
        if now - self.depuis_role.get(cible, float("-inf")) < ECHANGE_RECENT:
            return f"{nom} vient de changer de rôle", None
        v = (self.vie.get(cible) or {}).get("v")
        if not isinstance(v, int) or v < VIE_MIN:
            return f"{nom} a moins de {VIE_MIN // 4} cœurs", None
        if not sol_stable(ec.get("ys", []), now):
            return f"{nom} n'est pas au sol (saut, chute, nage ?)", None
        return "", cible

    # --- pendant ----------------------------------------------------------------------------------------
    def potion_state(self, pid: int, e: dict, now: float) -> None:
        """Un ``state`` vient d'arriver (Partie.state) : historique des hauteurs, dernière position au sol du clone,
        fins (le clone ou le possédé quitte la salle)."""
        if e.get("pos") is not None:
            self._hist(pid, e["pos"][1], now)
        po = self.potions.get(pid)
        if po is not None:
            if e.get("zone") != po["zone"]:
                self.potion_fin(pid, "le clone a quitté la salle", now)
                return
            if e.get("ship"):
                self.potion_fin(pid, "bateau", now)
                return
            if e.get("pos") is not None and not e.get("busy") and sol_stable(e.get("ys", []), now):
                po["sol"] = tuple(e["pos"])
            return
        h = self.possede_par(pid)
        if h is not None and e.get("zone") != self.potions[h]["zone"]:
            self.potion_fin(h, f"{self.noms.get(pid, '?')} a quitté la salle", now)

    def coup_clone(self, possede: int, dmg: int, now: float | None = None) -> bool:
        """``player_hit`` d'un monstre vers le fantôme d'un possédé (le clone) : pas relayé, gardé pour la fin."""
        h = self.possede_par(possede)
        if h is None:
            return False
        po = self.potions[h]
        po["degats"] = min(DEGATS_MAX, po["degats"] + max(0, int(dmg)))
        return True

    def pvp(self, src: int, msg: dict, now: float | None = None) -> str:
        """``pvp {to, zone}`` : un coup d'épée vu chez ``src`` (détection Python). Clone -> Link : relayé à la
        victime (``pvp_coup``) ; Link -> clone : exorcisme. Renvoie "" (accepté) ou la raison (journal)."""
        now = self.clock() if now is None else now
        r = self._pvp(src, msg, now)
        if r:
            self.n_pvp_refus += 1
        else:
            self.n_pvp += 1
        return r

    def _pvp(self, src: int, msg: dict, now: float) -> str:
        if not self.repartie or not self.cfg["potion"] or not self.potions:
            return "aucune potion en cours"
        to = msg.get("to")
        if not isinstance(to, int) or isinstance(to, bool) or to == src:
            return "cible invalide"
        es = self.etat.get(src) or {}
        zone = msg.get("zone")
        if not isinstance(zone, str) or es.get("zone") != zone or es.get("busy") or es.get("pos") is None \
                or now - es.get("t", 0) > ETAT_AGE:
            return "attaquant pas dans cette salle (ou en cinématique)"
        if src in self.potions:                              # le clone frappe un Link
            po = self.potions[src]
            if to == po["cible"] or self.role(to) != "link" or self.possede_par(to) is not None:
                return "pas une cible du clone"
            ev = self.etat.get(to) or {}
            if ev.get("zone") != zone or ev.get("busy") or ev.get("pos") is None:
                return "victime pas dans la salle (ou en cinématique)"
            if math.dist(ev["pos"], es["pos"]) > PVP_DIST:
                return "trop loin"
            t = [x for x in self.pvp_t.get((src, to), []) if now - x < 1.0]
            if len(t) >= PVP_RATE:
                self.pvp_t[(src, to)] = t
                return "trop de coups"
            t.append(now)
            self.pvp_t[(src, to)] = t
            self.coups[to] = (f"pvp:{src}", now)             # attribution des dégâts (points, K.O. de Crawl)
            self.sortie.append((to, {"type": "pvp_coup", "from": po["cible"], "zone": zone,
                                     "dmg": self.cfg["pvp_degats"], "pos": [round(v, 1) for v in es["pos"]],
                                     "seq": self.n_pvp + 1}))
            return ""
        h = self.possede_par(to)                             # un Link frappe le clone : exorcisme
        if h is None or self.role(src) != "link" or self.possede_par(src) is not None:
            return "pas de tir ami"
        po = self.potions[h]
        eh = self.etat.get(h) or {}
        if zone != po["zone"] or eh.get("pos") is None or math.dist(eh["pos"], es["pos"]) > PVP_DIST:
            return "trop loin du clone"
        if now - self.exo_t.get(src, float("-inf")) < EXO_GAP:
            return "même coup"
        self.exo_t[src] = now
        po["exo"] += 1
        po["degats"] = min(DEGATS_MAX, po["degats"] + self.cfg["pvp_degats"])
        nom = self.noms.get(src, "?")
        self.sortie.append((h, {"type": "hnote", "msg": f"{nom} frappe ton clone (exorcisme {po['exo']}/{EXORCISME})"}))
        self.sortie.append((src, {"type": "hnote", "msg": f"exorcisme {po['exo']}/{EXORCISME}"}))
        if po["exo"] >= EXORCISME:
            self.potion_fin(h, f"exorcisé par {nom}", now)
        return ""

    def resister(self, pid: int, now: float | None = None) -> None:
        """``hresister`` du possédé : RESIST_APPUIS appuis = RESIST_GAIN s de moins (au plus RESIST_RATE par s)."""
        now = self.clock() if now is None else now
        h = self.possede_par(pid)
        if h is None or not self.cfg["potion_resister"]:
            return                                 # réglage décoché (défaut) : le possédé regarde, durée fixe
        t = [x for x in self.resist_t.get(pid, []) if now - x < 1.0]
        if len(t) >= RESIST_RATE:
            self.resist_t[pid] = t
            return
        t.append(now)
        self.resist_t[pid] = t
        po = self.potions[h]
        po["resist"] += 1
        if po["resist"] % RESIST_APPUIS == 0:
            po["fin"] -= RESIST_GAIN
            if po["resist"] % (RESIST_APPUIS * 5) == 0:
                self.sortie.append((h, {"type": "hnote", "msg": f"{self.noms.get(pid, '?')} résiste : "
                                        f"{max(0.0, po['fin'] - now):.0f} s"}))
            if now >= po["fin"]:
                self.potion_fin(h, f"{self.noms.get(pid, '?')} a résisté", now)

    # --- dépenses du clone (inventaire et argent du possédé) -------------------------------------------
    # Monde persistant (fusion des deux chantiers) : le serveur pose ``depense_interdite`` quand il garde un monde.
    # « Rien d'un Hanteur n'entre dans le monde gardé » vaut aussi pour les dépenses du clone : une baisse des
    # compteurs y serait écrite pour toujours. Le réglage ``potion_depense`` reste gardé tel quel (il resservira
    # sur une partie sans monde) ; seul son EFFET vaut « rien » : aucun ``cnt`` du clone n'est compté, les
    # messages de la potion annoncent « rien » (le client du Hanteur n'envoie donc rien non plus).
    depense_interdite = False

    def depense_reglee(self) -> str:
        """Réglage ``potion_depense`` qui vaut vraiment : « rien » sur un monde persistant."""
        return "rien" if self.depense_interdite else self.cfg["potion_depense"]

    def link_seul_permis(self) -> bool:
        """Posséder un Link SEUL dans sa salle est permis (et la potion ne finit pas quand il n'en reste aucun
        autre). Suit le réglage CHOISI, jamais ``depense_interdite`` : sur un monde persistant le clone ne dépense
        rien, mais la possession reste utilisable à deux joueurs et garde sa durée fixe (relecture de la fusion :
        elle y était toujours refusée à 1 Link + 1 Hanteur, et coupée après SEUL s sinon)."""
        return self.cfg["potion_depense"] != "rien"

    def clone_depense(self, pid) -> int | None:
        """Le Hanteur ``pid`` joue un Link et le réglage le laisse dépenser : renvoie le Link possédé (le pot
        commun touché est le sien), sinon None."""
        po = self.potions.get(pid)
        if po is None or not self.cfg["potion"] or self.depense_reglee() == "rien":
            return None
        return po["cible"]

    def plafond_depense(self, k: str) -> int | None:
        """Plafond par possession du compteur ``k`` (None : pas de plafond)."""
        if self.depense_reglee() != "plafond":
            return None
        return self.cfg["potion_plafond"] if k == "r" else PLAFOND_MUNITIONS

    def depense_clone(self, pid: int, d: dict, tot: dict) -> dict:
        """Deltas ``d`` d'un événement de compteurs du clone ``pid`` -> ce qui est retiré du pot commun ``tot``
        du possédé. Seules les BAISSES des compteurs de ``DEPENSE_KS`` passent, bornées par le total (jamais sous
        zéro) et par le plafond de la possession ; un gain n'est jamais compté. Le cumul est gardé dans la potion
        (annoncé à la fin). À appeler une seule fois par événement (le serveur dédoublonne avant)."""
        po = self.potions.get(pid)
        if po is None or self.clone_depense(pid) is None or not isinstance(d, dict) or not isinstance(tot, dict):
            return {}
        out = {}
        for k in DEPENSE_KS:
            dv = d.get(k)
            if not isinstance(dv, int) or isinstance(dv, bool) or dv == 0:
                continue
            if dv > 0:
                po["gains"] += 1                   # gain du clone : jamais compté (corrigé chez lui par le total)
                continue
            dispo = tot.get(k)
            n = min(-dv, dispo if isinstance(dispo, int) and not isinstance(dispo, bool) and dispo > 0 else 0)
            plafond = self.plafond_depense(k)
            if plafond is not None:
                n = min(n, max(0, plafond - po["depense"][k]))
            if n < -dv:
                po["refuse"] += 1
            if n > 0:
                out[k] = -n
                po["depense"][k] += n
        return out

    def arreter(self, pid: int, now: float | None = None) -> None:
        """``hpotion_stop`` : le Hanteur arrête sa potion."""
        if pid in self.potions:
            self.potion_fin(pid, "arrêtée", now)

    def potion_tick(self, now: float) -> None:
        self._lancers_tick(now)
        for h, po in list(self.potions.items()):
            if now >= po["fin"]:
                self.potion_fin(h, "durée écoulée", now)
                continue
            autres = [o for o in self._liens() if o != po["cible"] and (self.etat.get(o) or {}).get("zone") == po["zone"]]
            if autres or self.link_seul_permis():
                po["seul"] = None                  # réglage « dépenses » : posséder un Link seul a un but
            elif po["seul"] is None:
                po["seul"] = now
            elif now - po["seul"] >= SEUL:
                self.potion_fin(h, "plus aucun autre Link dans la salle", now)

    # --- fin --------------------------------------------------------------------------------------------
    def potion_fin(self, h: int, raison: str, now: float | None = None) -> bool:
        po = self.potions.pop(h, None)
        if po is None:
            return False
        now = self.clock() if now is None else now
        self.recharges[(h, "potion")] = now + RECHARGE
        cible = po["cible"]
        dep = lire_depense(po.get("depense"))
        perdu = texte_depense(dep)
        self.sortie.append((h, {"type": "hpotion_fin", "raison": raison, "depense": dep}))
        msg = {"type": "hlibere", "raison": raison, "degats": int(po["degats"]), "depense": dep}
        if po.get("sol") is not None and isinstance(po["zone"], str) and ":" in po["zone"]:
            stage, _, room = po["zone"].rpartition(":")
            msg.update(pos=[round(v, 1) for v in po["sol"]], stage=stage, room=int(room))
        self.sortie.append((cible, msg))
        self._fx("libere", po["zone"], po.get("sol"), cible=cible)     # fumée à l'endroit du retour
        # la baisse de vie de la libération (au plus ``degats`` quarts, appliquée chez le possédé jusqu'à
        # JEU_ATTENTE s après, jeu libre) n'est le dégât de personne
        self.vie_ignore[cible] = [now + LIBERATION_FENETRE, int(po["degats"])]
        # ce que son jeu a encore en file au moment de la libération ne part pas non plus (même quarantaine qu'un
        # Hanteur qui redevient Link ; le client vide ses files et se réinscrit après)
        if not self.est_hanteur(cible):
            self.quarantaine[cible] = max(self.quarantaine.get(cible, 0.0), now + QUARANTAINE_LIBERE)
        nom = self.noms.get(cible, "?")
        for o in self._liens():
            if o != cible:
                self.sortie.append((o, {"type": "hnote", "msg": f"{nom} est libéré ({raison})"
                                        + (f" ; le Hanteur a dépensé {perdu}" if perdu else "")}))
        self.changes.append(cible)            # il redevient candidat à l'autorité
        self.t_diffusion = float("-inf")
        self.log(f"Hanteurs : potion de {self.noms.get(h, h)} sur {nom} terminée ({raison})"
                 + (f" ; dépensé : {perdu}" if perdu else ""))
        return True

    def potion_fin_joueur(self, pid: int, raison: str, now: float) -> None:
        """Changement de rôle, départ, reconnexion : toute potion où ``pid`` est le clone ou le possédé finit."""
        if pid in self.potions:
            self.potion_fin(pid, raison, now)
        h = self.possede_par(pid)
        if h is not None:
            self.potion_fin(h, raison, now)

    def potion_config(self, now: float | None = None) -> None:
        """Réglages changés par l'hôte : potion coupée -> toutes les potions finissent."""
        if not self.cfg["potion"]:
            for h in list(self.potions):
                self.potion_fin(h, "potion coupée par l'hôte", now)

    def vue_potion(self, pid: int, now: float) -> dict:
        po = self.potions.get(pid)
        h = self.possede_par(pid)
        return {"potion": ({"cible": po["cible"], "nom": self.noms.get(po["cible"], "?"),
                            "reste": round(max(0.0, po["fin"] - now), 1), "exo": po["exo"],
                            "depense": lire_depense(po.get("depense"))} if po else None),
                "possede_par": ({"reste": round(max(0.0, self.potions[h]["fin"] - now), 1)}
                                if h is not None else None),
                "recharge_potion": round(max(0.0, self.recharges.get((pid, "potion"), now) - now), 1)}


# =========================================================================================================
# Client : coups PvP reçus (victime)
# =========================================================================================================
class PvpSync:
    """Coups PvP (``pvp_coup``) reçus du serveur (boucle réseau) et appliqués au tick suivant (fil de travail)."""

    def __init__(self, enabled: bool = False, natif: bool = False, clock=time.monotonic):
        self.enabled = bool(enabled)
        self.natif = bool(natif) and self.enabled
        self.clock = clock
        self._lock = threading.Lock()
        self.inbox: list[tuple[float, dict]] = []
        self.vus: list[int] = []
        self.dernier = float("-inf")
        self.notes: list[str] = []
        self.n_recus = self.n_appliques = self.n_refuses = self.n_envoyes = 0
        self.last_hit: dict[int, float] = {}

    def receive(self, msg: dict) -> bool:
        if not self.enabled:
            return False
        frm, dmg, zone, pos, seq = (msg.get("from"), msg.get("dmg"), msg.get("zone"), _pos3(msg.get("pos")),
                                    msg.get("seq"))
        if not (isinstance(frm, int) and not isinstance(frm, bool) and isinstance(dmg, int)
                and not isinstance(dmg, bool) and 1 <= dmg <= 20 and isinstance(zone, str) and pos is not None):
            return False
        with self._lock:
            if isinstance(seq, int) and seq in self.vus:
                return False
            if isinstance(seq, int):
                self.vus = (self.vus + [seq])[-64:]
            self.n_recus += 1
            self.inbox = (self.inbox + [(self.clock(), {"from": frm, "dmg": dmg, "zone": zone, "pos": pos})])[-8:]
        return True

    def _note(self, s: str) -> None:
        if len(self.notes) < 16:
            self.notes.append("Hanteurs (PvP) : " + s)

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def victim_tick(self, now: float, zone, free: bool, life, link_pos, ghost_pid, inject, direct, noms=None) -> int:
        """``ghost_pid(joueur)`` -> id de process de son fantôme chez nous (ou None) ; ``inject(owner, dmg, vec)`` ->
        code de CALL 9 ou None (pas de pack / --pvp-natif absent) ; ``direct(vie)`` -> True si écrite."""
        with self._lock:
            box, self.inbox = self.inbox, []
        n = 0
        noms = noms or {}
        for t, c in box:
            qui = noms.get(c["from"], "?")
            if now - t > PVP_FRESH or c["zone"] != zone or not free or not isinstance(life, int) or life <= 0:
                self.n_refuses += 1
                continue
            lp = _pos3(link_pos)
            if lp is None or math.dist(lp, c["pos"]) > PVP_ESQUIVE:
                self.n_refuses += 1
                self._note(f"coup du clone de {qui} esquivé (tu étais déjà loin)")
                continue
            if now - self.dernier < PVP_INVULN:
                self.n_refuses += 1
                continue
            if self.natif and inject is not None:
                owner = ghost_pid(c["from"])
                if owner is not None:
                    d = (lp[0] - c["pos"][0], 0.0, lp[2] - c["pos"][2])
                    nrm = math.hypot(d[0], d[2]) or 1.0
                    code = inject(owner, c["dmg"], [d[0] / nrm, 0.0, d[2] / nrm])
                    if code == 1:
                        self.dernier = now
                        n += 1
                        self.n_appliques += 1
                        self._note(f"touché par le clone de {qui}")
                        continue
                    if code is not None and code != -2 and code != -3:
                        self._note(f"CALL 9 refusé ({code}) : vie écrite à la place")
                    elif code in (-2, -3):
                        self.n_refuses += 1          # Medli / Makar, événement : rien
                        continue
            new = vie_pvp(life, c["dmg"])
            if new < life and direct is not None and direct(new):
                self.dernier = now
                life = new
                n += 1
                self.n_appliques += 1
                self._note(f"touché par le clone de {qui} : -{c['dmg']} quart(s)")
            else:
                self.n_refuses += 1
        return n

    def status(self) -> str:
        return (f"PvP : {self.n_envoyes} coup(s) envoyé(s), {self.n_recus} reçu(s), {self.n_appliques} appliqué(s)"
                + (" (natif, CALL 9)" if self.natif else " (vie écrite)"))
