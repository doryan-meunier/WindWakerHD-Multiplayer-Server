"""Médailles de fin de donjon (Four Swords) : logique pure du serveur, testable sans jeu (tests/test_medailles.py).

Étude : docs/etudes/autres_zelda.md §2 (mécanique 1). Décisions de l'utilisateur : pour la GLOIRE seule (aucune
récompense, rien n'est écrit dans le jeu hormis le bandeau) ; classement montré seulement à la FIN du donjon (rien
pendant). Le reste : choix par défaut de l'étude.

* **Fenêtre de comptage** : de la première entrée d'un joueur dans une étape du donjon jusqu'au boss vaincu (bit 3 de
  ``mem:dungeon`` du donjon, ``mem<i>`` octet 0x21, posé la première fois dans l'union de la progression du groupe).
  Repartir et revenir ne remet rien à zéro. Un donjon déjà fini (relu du monde persistant) ne se rouvre pas.
* **Catégories** (une médaille chacune, au meilleur ; égalité : partagée) :
  1. rubis ramassés (deltas positifs de la bourse commune envoyés par CE joueur) ;
  2. monstres vaincus (dernier coup « hit » reçu sur ce monstre dans les 3 s, sinon l'autorité de la zone) ;
  3. prudence : le moins de quarts de cœur perdus ; un Game Over dans le donjon fait perdre cette médaille ;
  4. coffres ouverts (premier rapport « coffre » de ce joueur).
  Une catégorie où personne n'a rien (0 partout) ne donne pas de médaille (prudence : il faut au moins un joueur sans
  Game Over).
* **Or du donjon** : le plus de médailles ; égalité départagée par les rubis (comme Four Swords), puis partagée.
* Un Hanteur ne compte pas (le serveur ne l'inscrit jamais ici).

Étapes des donjons [HYPO : noms internes des étapes de la version GC (jeu/game.py KNOWN_STAGES, synchro/boss.py
BOSS_STAGES), seul M_NewD2 relu en direct] ; numéro = celui de la sauvegarde (``mDan.mStageNo``, progress.py).

**Essai solo** (``essai=True``, serveur ``--essai-medailles``, Réglages › Essais du lanceur ; à retirer après
validation : tout ce qui sert à l'essai est marqué ESSAI) : deux faux coéquipiers (« Essai Ana », « Essai Bo »)
entrent dans chaque fenêtre ouverte par un vrai joueur et « font » des actions de donjon à chaque action d'un vrai
joueur (tirage déterministe), et le donjon 0 « Donjon d'essai » couvre tout ce qui n'est pas un donjon (mer, îles).
Une fin de donjon se simule (message ``medailles_essai``).
"""
from __future__ import annotations

import random
import time

VERSION = 1
CATEGORIES = ("rubis", "monstres", "prudence", "coffres")
NOMS_CAT = {"rubis": "Rubis", "monstres": "Monstres", "prudence": "Prudence", "coffres": "Coffres"}

# numéro de sauvegarde -> (nom, étapes) [HYPO, voir plus haut]
DONJONS: dict[int, tuple[str, frozenset[str]]] = {
    2: ("Forteresse Maudite", frozenset({"MajyuE", "Majroom", "majroom", "ma2room", "ma3room", "Mjtower", "M2tower"})),
    3: ("Caverne du Dragon", frozenset({"M_NewD2", "M_DragB"})),
    4: ("Bois Défendus", frozenset({"kindan", "kinBOSS"})),
    5: ("Tour des Dieux", frozenset({"Siren", "SirenB"})),
    6: ("Temple de la Terre", frozenset({"M_Dai", "M_DaiB"})),
    7: ("Temple du Vent", frozenset({"kaze", "kazeB"})),
    8: ("Tour de Ganon", frozenset({"GanonA", "GanonB", "GanonC", "GanonD", "GanonE", "GanonJ", "GanonK", "GanonL",
                                    "GanonM", "GanonN", "GTower", "Xboss0", "Xboss1", "Xboss2", "Xboss3"})),
}
ESSAI_DONJON = 0                       # ESSAI : tout ce qui n'est pas un donjon
ESSAI_NOM = "Donjon d'essai"
ESSAI_FAUX = ("Essai Ana", "Essai Bo")  # ESSAI : faux coéquipiers
BOSS_OCTET, BOSS_MASQUE = 0x21, 0x08   # mDungeonItem bit 3 : boss vaincu (jeu/progress.py)
COUP_FENETRE = 3.0                     # s : dernier coup qui compte pour un monstre vaincu
FINIS_MAX = 64
JOUEURS_MAX = 16
NOM_MAX = 24
ZONES_MAX = 64
CLES_MAX = 256
TEXTE_MAX = 44                         # octets Latin-1 d'une ligne du bandeau (natif/mod_wwmp/boite.h BAN_TEXTE)
OR_RGBA, TITRE_RGBA, LIGNE_RGBA = 0xFFD24AFF, 0xFFD24AFF, 0xFFFFFFFF


def donjon_de(stage) -> int | None:
    """Numéro du donjon de l'étape (None : pas un donjon)."""
    if not isinstance(stage, str):
        return None
    for n, (_nom, etapes) in DONJONS.items():
        if stage in etapes:
            return n
    return None


def nom_donjon(n: int) -> str:
    return ESSAI_NOM if n == ESSAI_DONJON else DONJONS.get(n, (f"donjon {n}",))[0]


def fins_de_donjon(bits: dict) -> list[int]:
    """Donjons dont le bit « boss vaincu » est dans ``bits`` (format de jeu/progress.py : {« mem3 »: {0x21: masque}})."""
    out = []
    for n in DONJONS:
        if (bits.get(f"mem{n}") or {}).get(BOSS_OCTET, 0) & BOSS_MASQUE:
            out.append(n)
    return out


def _stats() -> dict:
    return {"rubis": 0, "monstres": 0, "degats": 0, "coffres": 0, "morts": 0}


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _nom(nom) -> str:
    return str(nom or "?")[:NOM_MAX]


def attribuer(joueurs: dict[str, dict]) -> tuple[dict[str, list[str]], list[str]]:
    """Médailles par catégorie (liste des gagnants, partagée en cas d'égalité) et or du donjon."""
    med: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        if cat == "prudence":
            cand = {n: s["degats"] for n, s in joueurs.items() if not s["morts"]}
            if not cand:
                med[cat] = []
                continue
            best = min(cand.values())
        else:
            cand = {n: s[cat] for n, s in joueurs.items()}
            best = max(cand.values()) if cand else 0
            if best <= 0:
                med[cat] = []
                continue
        med[cat] = sorted(n for n, v in cand.items() if v == best)
    nb = {n: sum(n in g for g in med.values()) for n in joueurs}
    top = max(nb.values()) if nb else 0
    if top <= 0:
        return med, []
    cand = [n for n, v in nb.items() if v == top]
    r = max(joueurs[n]["rubis"] for n in cand)
    return med, sorted(n for n in cand if joueurs[n]["rubis"] == r)


def _latin1(t: str) -> str:
    """Texte que le bandeau peut montrer (Latin-1) : tirets et guillemets typographiques remplacés."""
    t = t.replace("—", "-").replace("–", "-").replace("’", "'").replace("«", '"').replace("»", '"')
    return t.encode("latin-1", "replace").decode("latin-1")


def coeurs(quarts: int) -> str:
    """Cœurs perdus, en Latin-1 : « 0 coeur », « -1,5 coeur », « -3 coeurs »."""
    if quarts <= 0:
        return "0 coeur"
    t = f"{quarts / 4:g}".replace(".", ",")
    return f"-{t} coeur{'s' if quarts >= 8 else ''}"


def _court(noms: list[str], n: int) -> str:
    return "+".join(x[:n] for x in noms)


def _ligne(parties: list[tuple[str, list[str], str]]) -> str:
    """« Rubis Léa 212 · Monstres Mathis 31 » tenu dans TEXTE_MAX octets (noms raccourcis au besoin)."""
    for n in (NOM_MAX, 12, 10, 8, 6, 4, 3):
        t = " · ".join(f"{cat} {_court(g, n)} {v}".rstrip() for cat, g, v in parties)
        t = _latin1(t)
        if len(t) <= TEXTE_MAX:
            return t
    return t[:TEXTE_MAX]


def lignes_bandeau(res: dict) -> list[tuple[str, int]]:
    """Les quatre lignes du bandeau de fin de donjon (texte Latin-1 de TEXTE_MAX octets au plus, couleur)."""
    j, med = res["joueurs"], res["medailles"]

    def part(cat: str) -> tuple[str, list[str], str]:
        g = med.get(cat) or []
        if not g:
            return (NOMS_CAT[cat], ["-"], "")
        if cat == "prudence":
            v = j[g[0]]["degats"]
            return (NOMS_CAT[cat], g, coeurs(v))
        return (NOMS_CAT[cat], g, str(j[g[0]][cat]))
    titre = _latin1(f"Médailles - {res['nom']}")[:TEXTE_MAX]
    l2 = _ligne([part("rubis"), part("monstres")])
    l3 = _ligne([part("prudence"), part("coffres")])
    if res["or"]:
        n = sum(res["or"][0] in g for g in med.values())
        l4 = ""
        for k in (NOM_MAX, 12, 10, 8, 6, 4):
            l4 = _latin1(f"Or du donjon : {_court(res['or'], k)} ({n} médaille{'s' if n > 1 else ''})")
            if len(l4) <= TEXTE_MAX:
                break
        l4 = l4[:TEXTE_MAX]
    else:
        l4 = "Or du donjon : personne"
    return [(titre, TITRE_RGBA), (l2, LIGNE_RGBA), (l3, LIGNE_RGBA), (l4, OR_RGBA)]


class Medailles:
    """État des médailles d'une partie (serveur) : fenêtres en cours, donjons finis, attribution des monstres."""

    def __init__(self, on: bool = True, essai: bool = False, horloge=time.monotonic, graine: int = 0):
        self.on = bool(on)
        self.essai = bool(essai)                      # ESSAI
        self.horloge = horloge
        self.en_cours: dict[int, dict[str, dict]] = {}   # donjon -> joueur -> stats
        self.finis: list[dict] = []
        self.ou: dict[str, int | None] = {}           # joueur -> donjon où il est
        self.vie: dict[str, tuple] = {}               # joueur -> (étape, vie) au dernier « state »
        self.coups: dict[tuple, tuple] = {}           # (zone, clé) -> (joueur, heure)
        self.vivants: dict[str, set] = {}             # zone -> clés vues vivantes
        self.morts_vus: dict[str, set] = {}           # zone -> clés déjà comptées
        self.rng = random.Random(graine)              # ESSAI : tirages des faux coéquipiers
        self.rev = 0

    # --- fenêtres -------------------------------------------------------------------------------------------------
    def _fini(self, n: int) -> bool:
        """Donjon déjà fini pour de bon (une fin simulée de l'essai ne ferme rien)."""
        return any(r["donjon"] == n and not r.get("essai") for r in self.finis)

    def _donjon(self, stage) -> int | None:
        n = donjon_de(stage)
        if n is None and self.essai and isinstance(stage, str) and stage:
            return ESSAI_DONJON                       # ESSAI : la mer et les îles comptent aussi
        return n

    def _ouvrir(self, n: int, nom: str) -> dict | None:
        if self._fini(n):
            return None
        j = self.en_cours.setdefault(n, {})
        if nom not in j and len(j) < JOUEURS_MAX:
            j[nom] = _stats()
            if self.essai:
                for f in ESSAI_FAUX:                  # ESSAI : les faux coéquipiers entrent avec lui
                    j.setdefault(f, _stats())
        return j.get(nom)

    def entree(self, nom: str, stage) -> None:
        """Le joueur est dans cette étape (chaque « state ») : fenêtre ouverte à sa première entrée."""
        if not self.on:
            return
        nom = _nom(nom)
        n = self._donjon(stage)
        self.ou[nom] = n
        if n is not None:
            self._ouvrir(n, nom)

    def _stat(self, nom: str) -> dict | None:
        n = self.ou.get(_nom(nom))
        if n is None or n not in self.en_cours:
            return None
        return self.en_cours[n].get(_nom(nom))

    def _essai_suivre(self, nom: str, cle: str, v: int) -> None:
        """ESSAI : les faux coéquipiers du même donjon font à peu près la même chose (tirage déterministe)."""
        if not self.essai or nom in ESSAI_FAUX:
            return
        n = self.ou.get(nom)
        if n is None or n not in self.en_cours:
            return
        for f in ESSAI_FAUX:
            s = self.en_cours[n].get(f)
            if s is None:
                continue
            if cle == "rubis":
                s["rubis"] += int(round(v * self.rng.uniform(0.2, 1.8)))
            elif cle == "morts":
                continue
            elif self.rng.random() < 0.75:
                s[cle] += v if cle == "degats" else 1

    def _ajouter(self, nom: str, cle: str, v: int) -> bool:
        if not self.on:
            return False
        s = self._stat(nom)
        if s is None:
            return False
        s[cle] += v
        self.rev += 1
        self._essai_suivre(_nom(nom), cle, v)
        return True

    def rubis(self, nom: str, v: int) -> bool:
        return _int(v) and v > 0 and self._ajouter(nom, "rubis", min(v, 5000))

    def coffre(self, nom: str) -> bool:
        return self._ajouter(nom, "coffres", 1)

    def degats(self, nom: str, quarts: int) -> bool:
        return _int(quarts) and quarts > 0 and self._ajouter(nom, "degats", min(quarts, 80))

    def mort(self, nom: str) -> bool:
        return self._ajouter(nom, "morts", 1)

    def etat(self, nom: str, stage, vie, vie_max, busy: bool, compter_degats: bool = True) -> None:
        """« state » d'un joueur : fenêtre, et (sans vie partagée) les quarts perdus d'un état au suivant dans la même
        étape ; vie tombée à 0 : Game Over. Changement d'étape, vie illisible : rien (pas de faux dégât au
        rechargement, mêmes règles que synchro/vie_partagee.py)."""
        if not self.on:
            return
        nom = _nom(nom)
        self.entree(nom, stage)
        if not (_int(vie) and _int(vie_max) and 0 <= vie <= vie_max <= 80):
            self.vie.pop(nom, None)
            return
        avant = self.vie.get(nom)
        self.vie[nom] = (stage, vie)
        if avant is None or avant[0] != stage or busy:
            return
        perte = avant[1] - vie
        if perte > 0 and compter_degats:
            self.degats(nom, perte)
        if vie == 0 and avant[1] > 0:
            self.mort(nom)

    # --- monstres -------------------------------------------------------------------------------------------------
    def coup(self, zone: str, cle, nom: str) -> None:
        """Un « hit » d'un joueur sur la clé d'un monstre de la zone (le dernier compte, COUP_FENETRE)."""
        if not self.on or not isinstance(zone, str) or cle is None:
            return
        if len(self.coups) >= CLES_MAX * 4:
            self.coups.pop(next(iter(self.coups)))
        self.coups[(zone, str(cle)[:32])] = (_nom(nom), self.horloge())

    def ennemis(self, zone: str, autorite: str, lst) -> list[tuple[str, str]]:
        """Instantané « enemies » de l'autorité : chaque monstre vu vivant puis mort est compté une fois, au dernier
        coup reçu sur lui dans les COUP_FENETRE s, sinon à l'autorité. Rend [(joueur, clé)] des monstres comptés."""
        if not self.on or not isinstance(zone, str) or not isinstance(lst, list):
            return []
        if zone not in self.vivants and len(self.vivants) >= ZONES_MAX:
            k = next(iter(self.vivants))
            self.vivants.pop(k)
            self.morts_vus.pop(k, None)
        viv = self.vivants.setdefault(zone, set())
        vus = self.morts_vus.setdefault(zone, set())
        now = self.horloge()
        out = []
        for e in lst[:CLES_MAX]:
            if not isinstance(e, dict) or e.get("key") is None:
                continue
            cle = str(e["key"])[:32]
            mort = e.get("dead") in (1, True) or (_int(e.get("max_hp")) and e["max_hp"] > 0 and _int(e.get("hp"))
                                                  and e["hp"] <= 0)
            if not mort:
                if len(viv) < CLES_MAX:
                    viv.add(cle)
                vus.discard(cle)
                continue
            if cle not in viv or cle in vus:
                continue
            viv.discard(cle)
            vus.add(cle)
            par = self.coups.pop((zone, cle), None)
            qui = par[0] if par is not None and now - par[1] <= COUP_FENETRE else _nom(autorite)
            if self._ajouter(qui, "monstres", 1):
                out.append((qui, cle))
        return out

    # --- fin ------------------------------------------------------------------------------------------------------
    def fin(self, n: int, essai: bool = False) -> dict | None:
        """Boss vaincu (ou fin simulée) : classement du donjon ``n`` (None : personne n'y est entré, ou déjà fini)."""
        if not self.on or self._fini(n):
            return None
        joueurs = self.en_cours.pop(n, None)
        if not joueurs:
            return None
        if self.essai:                                # ESSAI : jamais une ligne vide chez les faux coéquipiers
            for f in ESSAI_FAUX:
                s = joueurs.get(f)
                if s is not None and not any(s[k] for k in ("rubis", "monstres", "coffres")):
                    s["rubis"] += self.rng.randint(5, 60)
                    s["monstres"] += self.rng.randint(1, 6)
                    s["degats"] += self.rng.randint(0, 8)
                    s["coffres"] += self.rng.randint(0, 3)
        med, gagnants = attribuer(joueurs)
        res = {"donjon": n, "nom": nom_donjon(n), "joueurs": joueurs, "medailles": med, "or": gagnants,
               "t": int(time.time())}
        if essai or n == ESSAI_DONJON:
            res["essai"] = 1                          # ESSAI : jamais gardé dans le monde, ne ferme rien
            self.finis = [r for r in self.finis if not (r.get("essai") and r["donjon"] == n)]
        self.finis.append(res)
        del self.finis[:-FINIS_MAX]
        self.rev += 1
        return res

    def fin_essai(self, nom: str) -> dict | None:
        """ESSAI : la fin du donjon où est ce joueur, simulée (mer et îles : « Donjon d'essai »)."""
        if not self.essai:
            return None
        n = self.ou.get(_nom(nom))
        if n is None:
            n = ESSAI_DONJON
        self._ouvrir(n, _nom(nom))
        return self.fin(n, essai=True)

    # --- vue et monde persistant ----------------------------------------------------------------------------------
    def totaux(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.finis:
            for n in r["joueurs"]:
                t = out.setdefault(n, {"or": 0, "medailles": 0})
                t["medailles"] += sum(n in g for g in r["medailles"].values())
                t["or"] += n in r["or"]
        return out

    def vue(self) -> dict:
        """Ce que montre le trackeur (onglet Médailles) : seulement les donjons FINIS (classement à la fin)."""
        return {"v": VERSION, "rev": self.rev, "essai": 1 if self.essai else 0,
                "finis": [{k: r[k] for k in ("donjon", "nom", "joueurs", "medailles", "or", "t")}
                          | ({"essai": 1} if r.get("essai") else {}) for r in self.finis],
                "totaux": self.totaux(), "en_cours": sorted(self.en_cours)}

    def exporter(self) -> dict:
        """Section « medailles » du monde persistant (donjons finis et fenêtres en cours ; jamais l'essai)."""
        return {"v": VERSION,
                "finis": [r for r in self.finis if not r.get("essai")],
                "en_cours": {str(n): j for n, j in self.en_cours.items() if n != ESSAI_DONJON}}

    def restaurer(self, doc) -> int:
        """Relit la section du monde (entrées abîmées ignorées) ; rend le nombre d'entrées ignorées."""
        if not isinstance(doc, dict):
            return 0
        bad = 0
        finis = []
        for r in (doc.get("finis") if isinstance(doc.get("finis"), list) else [])[-FINIS_MAX:]:
            ok = self._valider(r)
            if ok is None:
                bad += 1
            else:
                finis.append(ok)
        en = {}
        ec = doc.get("en_cours")
        for k, j in (ec.items() if isinstance(ec, dict) else []):
            try:
                n = int(k)
            except (TypeError, ValueError):
                bad += 1
                continue
            jj = self._joueurs(j)
            if n in DONJONS and jj is not None:
                en[n] = jj
            else:
                bad += 1
        self.finis, self.en_cours = finis, en
        return bad

    @staticmethod
    def _joueurs(j) -> dict | None:
        if not isinstance(j, dict) or len(j) > JOUEURS_MAX:
            return None
        out = {}
        for nom, s in j.items():
            if not isinstance(nom, str) or not isinstance(s, dict):
                return None
            st = _stats()
            for k in st:
                v = s.get(k, 0)
                if not (_int(v) and 0 <= v < 1 << 20):
                    return None
                st[k] = v
            out[nom[:NOM_MAX]] = st
        return out

    def _valider(self, r) -> dict | None:
        if not isinstance(r, dict) or not _int(r.get("donjon")) or r["donjon"] not in DONJONS:
            return None
        j = self._joueurs(r.get("joueurs"))
        if not j:
            return None
        med, gagnants = attribuer(j)                  # recalculés : jamais pris tels quels du fichier
        return {"donjon": r["donjon"], "nom": nom_donjon(r["donjon"]), "joueurs": j, "medailles": med, "or": gagnants,
                "t": r["t"] if _int(r.get("t")) else 0}
