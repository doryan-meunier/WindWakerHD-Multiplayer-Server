"""Le serveur de partie distribue le dernier paquet de mise à jour (docs/joueurs/MISES_A_JOUR.md).

    python tools/wwmp_server.py --mises-a-jour DOSSIER     # dossier avec dernier.json + wwmp-*.zip
    python tools/wwmp_server.py --donnees D                # serveur dédié : D/mises_a_jour (relu)

Choix : transfert par morceaux SUR LA CONNEXION DU JEU (même port TCP, même mot de passe de
partie), plutôt qu'un port HTTP à part : rien de plus à ouvrir sur la box ni dans le pare-feu, pas
de second service exposé sur Internet, et seuls ceux qui ont le mot de passe de la partie
téléchargent. Le serveur ne fait confiance à rien non plus : il vérifie la signature et
l'empreinte du paquet avec SA clé publique avant de le proposer (un dossier trafiqué n'est pas
servi), et chaque lanceur revérifie tout de son côté.

Messages (connexion présentée, outil ou joueur) :
  client -> serveur  {"type": "maj_info"}
  serveur -> client  {"type": "maj_info", "dispo": true, "manifeste": "<texte>", "signature": "<hex>",
                      "id": "<sha256 du zip>", "taille": n, "part": PART, "parts": N}
                     {"type": "maj_info", "dispo": false, "raison": "…"}
  client -> serveur  {"type": "maj_part", "n": i, "id": "<sha256>"}
  serveur -> client  {"type": "maj_part", "n": i, "id": …, "data": "<base64, PART octets au plus>"}
                     {"type": "maj_part", "n": i, "err": "…"}     (paquet changé, numéro invalide…)
Un ancien serveur ignore ces messages (le lanceur conclut « pas de mises à jour » après 4 s).
Un morceau à la fois par demande (le client attend la réponse) : pas de rafale qui gênerait le jeu.
"""
from __future__ import annotations

import base64
import os
import time

from commun import maj_paquet as mp

PART = 48 * 1024                 # octets bruts par morceau (64 Kio en base64 < MAX_LINE)
RELIRE = 5.0                     # s : le dossier est relu au plus toutes les RELIRE s
TYPES = ("maj_info", "maj_part")


class Distributeur:
    def __init__(self, dossier: str, cle_pub: bytes | None, log=print, horloge=time.monotonic):
        self.dossier = dossier
        self.cle = cle_pub
        self.log = log
        self.horloge = horloge
        self.t_lu = -1e9
        self.signature_lue = None      # (mtime, taille) de dernier.json déjà chargé
        self.paquet: dict | None = None   # {"dernier", "manifeste", "zip"}
        self.raison = "pas encore lu"
        self.servis = 0

    def charger(self, force: bool = False) -> dict | None:
        now = self.horloge()
        if not force and now - self.t_lu < RELIRE:
            return self.paquet
        self.t_lu = now
        chemin = os.path.join(self.dossier, "dernier.json")
        try:
            st = os.stat(chemin)
        except OSError:
            self.paquet, self.signature_lue = None, None
            self.raison = "aucun paquet dans le dossier des mises à jour"
            return None
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self.signature_lue and not force:
            return self.paquet
        self.signature_lue = sig
        try:
            with open(chemin, "rb") as f:
                dernier = mp.lire_dernier(f.read((8 << 20) + 1))
            m = mp.verifier_dernier(dernier, self.cle)
            with open(os.path.join(self.dossier, m["fichier"]), "rb") as f:
                zip_octets = f.read(mp.ZIP_MAX + 1)
            if len(zip_octets) != m["taille"] or mp.sha256(zip_octets) != m["sha256"]:
                raise mp.ErreurMaj("le zip ne correspond pas à dernier.json")
        except (OSError, mp.ErreurMaj) as e:
            self.paquet = None
            self.raison = "paquet refusé par le serveur"
            self.log(f"mises à jour : paquet de {self.dossier} REFUSÉ, pas distribué ({e})")
            return None
        self.paquet = {"dernier": dernier, "manifeste": m, "zip": zip_octets}
        self.log(f"mises à jour : version {m['version']} du {mp.date_texte(m['date'])} distribuée "
                 f"({len(zip_octets) // 1024} Kio, signature vérifiée)")
        return self.paquet

    def info(self) -> dict:
        p = self.charger()
        if p is None:
            return {"type": "maj_info", "dispo": False, "raison": self.raison}
        m = p["manifeste"]
        return {"type": "maj_info", "dispo": True, "manifeste": p["dernier"]["manifeste"],
                "signature": p["dernier"]["signature"], "id": m["sha256"], "taille": m["taille"],
                "version": m["version"], "date": m["date"], "part": PART,
                "parts": max(1, -(-m["taille"] // PART))}

    def morceau(self, n, ident) -> dict:
        p = self.paquet
        if p is None or ident != p["manifeste"]["sha256"]:
            return {"type": "maj_part", "n": n if isinstance(n, int) else None, "err": "paquet changé : recommence"}
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < max(1, -(-len(p["zip"]) // PART)):
            return {"type": "maj_part", "n": None, "err": "numéro de morceau invalide"}
        if n == 0:
            self.servis += 1
        data = p["zip"][n * PART:(n + 1) * PART]
        return {"type": "maj_part", "n": n, "id": ident, "data": base64.b64encode(data).decode("ascii")}


def traiter(distributeur: Distributeur | None, p, msg: dict) -> None:
    """Appelé par wwmp_server.Server.handle pour maj_info / maj_part (connexion déjà admise)."""
    if not getattr(p, "hello", False):
        return
    t = msg.get("type")
    if distributeur is None:
        if t == "maj_info":
            p.send({"type": "maj_info", "dispo": False, "raison": "ce serveur ne distribue pas de mises à jour"})
        else:
            p.send({"type": "maj_part", "n": None, "err": "pas de mises à jour ici"})
        return
    if t == "maj_info":
        p.send(distributeur.info())
    elif t == "maj_part":
        # au plus 3 fois le paquet par connexion (un client qui boucle ne sature pas le serveur)
        n_max = 3 * max(1, -(-len(distributeur.paquet["zip"]) // PART)) + 8 if distributeur.paquet else 8
        p.maj_parts = getattr(p, "maj_parts", 0) + 1
        if p.maj_parts > n_max:
            p.send({"type": "maj_part", "n": None, "err": "trop de demandes"})
            return
        p.send(distributeur.morceau(msg.get("n"), msg.get("id")))


def ouvrir(dossier: str | None, donnees: str | None, tools: str, log=print) -> Distributeur | None:
    """Distributeur pour --mises-a-jour DOSSIER, sinon DONNEES/mises_a_jour (relu à chaque demande :
    un paquet copié là plus tard est pris sans redémarrer) ; None sans l'un ni l'autre ou sans clé."""
    explicite = bool(dossier)
    if not dossier and donnees:
        dossier = os.path.join(donnees, "mises_a_jour")
    if not dossier:
        return None
    cle = mp.lire_cle_publique(mp.chemin_cle_publique(tools))
    if cle is None:
        if explicite:
            log("mises à jour : pas de clé publique (tools/commun/cle_publique.txt) : rien n'est distribué")
        return None
    d = Distributeur(dossier, cle, log)
    if d.charger(force=True) is None and (explicite or os.path.isdir(dossier)):
        log(f"mises à jour : {d.raison} ({dossier}) ; le dossier sera relu à chaque demande")
    return d
