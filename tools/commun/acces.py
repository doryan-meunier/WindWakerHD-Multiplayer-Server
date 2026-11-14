"""Jouer par Internet : mot de passe de partie et garde-fous du serveur (docs/hotes/JOUER_PAR_INTERNET.md).

Sans mot de passe (défaut, réseau local ou VPN de jeu), rien ne change : le client dit bonjour
(« hello ») et le serveur l'accueille.

Avec un mot de passe (``wwmp_server.py --mot-de-passe-fichier F``, variable d'environnement
``WWMP_MOT_DE_PASSE`` ou ``--donnees DOSSIER``) :

  serveur -> client, dès la connexion : {"type": "defi", "v": 1, "nonce": "32 hex"}
  client  -> serveur : hello habituel + {"auth": HMAC-SHA256(clé, "wwmp1|" + nonce) en hex}
                       (clé = SHA-256("wwmp-mdp|" + mot de passe))

Le mot de passe ne passe donc jamais sur le réseau, et une réponse enregistrée ne resservira
pas (nonce neuf à chaque connexion). Le trafic n'est PAS chiffré pour autant (positions, noms) :
pour le chiffrer, passer par Tailscale / ZeroTier.

Refus : « error » code ``mdp`` après DELAI_REFUS s (anti-force brute), puis la connexion est
fermée. Une adresse IP qui se trompe ECHECS_MAX fois en FENETRE s est bloquée BLOCAGE s (un
hello SANS preuve, client mal réglé, ne compte pas : seule une mauvaise preuve compte). Au plus
CONNEXIONS_PAR_IP connexions simultanées par adresse (hors 127.0.0.1) et CONNEXIONS_MAX en tout
(ces deux limites valent aussi sans mot de passe). Le mot de passe n'est jamais journalisé.

Rôle « hôte » (A15, progression de référence) : avec un mot de passe, tout client accepté s'est
authentifié, il peut se déclarer hôte (case « je suis l'hôte » du lanceur, ``--hote`` du client).
Sans mot de passe, ``hote`` n'est accepté que d'une adresse locale ou de VPN (192.168.x, 10.x,
Tailscale 100.64/10, Radmin 26.x, Hamachi 25.x…), jamais d'une adresse publique.

Côté client : ``champs_hello(reader, mdp)`` lit le défi (si on a un mot de passe) et renvoie les
champs à ajouter au hello.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import time

ENV = "WWMP_MOT_DE_PASSE"
FICHIER = "mot_de_passe.txt"      # dans le dossier de données du serveur (--donnees)
V = 1
MDP_MAX = 128
MDP_CONSEILLE = 8                 # en dessous : avertissement dans le journal du serveur
DELAI_REFUS = 1.0                 # s avant de répondre à un mot de passe refusé
ECHECS_MAX = 10                   # mauvaises preuves par adresse IP…
FENETRE = 600.0                   # … en 10 minutes …
BLOCAGE = 900.0                   # … -> adresse bloquée 15 minutes
CONNEXIONS_PAR_IP = 16            # simultanées, par adresse (8 joueurs + lobby / outils derrière une même box)
CONNEXIONS_MAX = 64               # simultanées, en tout
SUIVIES_MAX = 10000               # adresses suivies au plus (échecs, blocages) avant ménage
DEFI_ATTENTE = 4.0                # s : le client attend le défi au plus (serveur sans mot de passe : rien) ;
                                  # Internet : un paquet perdu coûte 1 à 3 s, et le serveur coupe à 5 s (TIMEOUT)
ATTENTE_CLIENT = 60.0             # s : le client de jeu attend au moins ça après un mot de passe refusé
LOCALES = ("127.0.0.1", "::1", "localhost")
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # sans l, i, o, 0, 1 (faciles à confondre)


def nettoyer(mdp) -> str | None:
    """Mot de passe utilisable, ou None (vide). Espaces autour retirés (copier-coller)."""
    if mdp is None:
        return None
    m = str(mdp).strip()
    return m[:MDP_MAX] or None


def generer(groupes: int = 4, taille: int = 4) -> str:
    """Mot de passe facile à dicter : « k7mq-2xpa-… » (≈ 79 bits pour 4 groupes de 4)."""
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(taille)) for _ in range(groupes))


def cle(mdp: str) -> bytes:
    return hashlib.sha256(("wwmp-mdp|" + mdp).encode("utf-8")).digest()


def preuve(mdp: str, nonce: str) -> str:
    """Réponse au défi (le mot de passe est nettoyé comme côté serveur : espaces autour retirés)."""
    return hmac.new(cle(nettoyer(mdp) or ""), ("wwmp1|" + nonce).encode("ascii", "replace"), hashlib.sha256).hexdigest()


def nonce_valide(n) -> bool:
    return isinstance(n, str) and len(n) == 32 and all(c in "0123456789abcdef" for c in n)


def lire_fichier(chemin: str) -> str | None:
    """Première ligne non vide du fichier (None si absent ou vide)."""
    try:
        with open(chemin, "r", encoding="utf-8-sig") as f:
            for ligne in f:
                m = nettoyer(ligne)
                if m:
                    return m
    except OSError:
        return None
    return None


def ecrire_fichier(chemin: str, mdp: str) -> None:
    """Crée le fichier du mot de passe (lisible par son propriétaire seulement, sous Linux)."""
    os.makedirs(os.path.dirname(chemin) or ".", exist_ok=True)
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(mdp + "\n")


RESEAUX_LOCAUX = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",   # PC, réseau local
    "100.64.0.0/10",                                                                   # Tailscale (CGNAT)
    "25.0.0.0/8", "26.0.0.0/8",                                                        # Hamachi, Radmin VPN
    "::1/128", "fe80::/10", "fc00::/7")]                                               # IPv6 local, Tailscale


def ip_locale_ou_vpn(ip: str) -> bool:
    """Adresse du même PC, du réseau local ou d'un VPN de jeu courant (jamais une IP publique)."""
    if ip in LOCALES:
        return True
    try:
        a = ipaddress.ip_address(ip[7:] if ip.startswith("::ffff:") else ip)
    except ValueError:
        return False
    return any(a in n for n in RESEAUX_LOCAUX if n.version == a.version)


class Garde:
    """État du serveur : mot de passe (seulement sa clé dérivée), échecs et connexions par IP."""

    def __init__(self, mdp: str | None = None, horloge=time.monotonic, delai_refus: float = DELAI_REFUS,
                 par_ip: int = CONNEXIONS_PAR_IP, maxi: int = CONNEXIONS_MAX):
        m = nettoyer(mdp)
        self._cle = cle(m) if m else None
        self.longueur = len(m) if m else 0
        self.horloge = horloge
        self.delai_refus = delai_refus
        self.par_ip, self.maxi = par_ip, maxi
        self.connexions: dict[str, int] = {}
        self.echecs: dict[str, list[float]] = {}
        self.bloquees: dict[str, float] = {}       # ip -> fin du blocage
        self.refus_muets = 0                       # connexions d'adresses bloquées (non journalisées)

    def changer_mdp(self, mdp: str | None) -> None:
        """Nouveau mot de passe (administration du serveur dédié) : les connexions déjà acceptées restent ; les
        échecs et blocages comptés aussi."""
        m = nettoyer(mdp)
        self._cle = cle(m) if m else None
        self.longueur = len(m) if m else 0

    @property
    def actif(self) -> bool:
        return self._cle is not None

    # -- connexions -----------------------------------------------------------------
    def bloquee(self, ip: str) -> bool:
        fin = self.bloquees.get(ip)
        if fin is None:
            return False
        if self.horloge() >= fin:
            del self.bloquees[ip]
            self.echecs.pop(ip, None)
            return False
        return True

    def ouvrir(self, ip: str) -> str | None:
        """Nouvelle connexion de ``ip`` : None si acceptée (et comptée), sinon la raison du refus."""
        if self.bloquee(ip):
            self.refus_muets += 1
            return "bloquee"
        total = sum(self.connexions.values())
        if total >= self.maxi:
            return f"trop de connexions ({total})"
        n = self.connexions.get(ip, 0)
        if ip not in LOCALES and n >= self.par_ip:
            return f"trop de connexions depuis {ip} ({n})"
        self.connexions[ip] = n + 1
        return None

    def fermer(self, ip: str) -> None:
        n = self.connexions.get(ip, 0) - 1
        if n > 0:
            self.connexions[ip] = n
        else:
            self.connexions.pop(ip, None)

    # -- mot de passe ------------------------------------------------------------------
    @staticmethod
    def nouveau_defi() -> str:
        return secrets.token_hex(16)

    def verifier(self, ip: str, nonce: str | None, auth) -> str:
        """« ok », « requis » (hello sans preuve : ne compte pas comme un échec), « faux » (échec
        compté) ou « bloquee » (cet échec bloque l'adresse)."""
        if not self.actif:
            return "ok"
        if not isinstance(auth, str) or not auth:
            return "requis"
        attendu = hmac.new(self._cle, ("wwmp1|" + (nonce or "")).encode("ascii", "replace"),
                           hashlib.sha256).hexdigest()
        if nonce and auth.isascii() and hmac.compare_digest(attendu, auth[:64].lower()):
            self.echecs.pop(ip, None)
            return "ok"
        now = self.horloge()
        if len(self.echecs) + len(self.bloquees) > SUIVIES_MAX:     # beaucoup d'adresses : on oublie les vieilles
            self.echecs = {k: v for k, v in self.echecs.items() if v and now - v[-1] < FENETRE}
            self.bloquees = {k: v for k, v in self.bloquees.items() if v > now}
        e = [t for t in self.echecs.get(ip, []) if now - t < FENETRE] + [now]
        self.echecs[ip] = e
        if len(e) >= ECHECS_MAX and ip not in LOCALES:
            self.bloquees[ip] = now + BLOCAGE
            return "bloquee"
        return "faux"

    def hote_permis(self, ip: str) -> bool:
        """A15 : un client peut-il se déclarer hôte ? Avec mot de passe : oui (il s'est authentifié)."""
        return self.actif or ip_locale_ou_vpn(ip)


def message_refus(resultat: str) -> dict:
    if resultat == "requis":
        msg = ("mot de passe requis : ce serveur est protégé. Entre le mot de passe de la partie "
               "(lanceur) ou définis WWMP_MOT_DE_PASSE")
    elif resultat == "bloquee":
        msg = f"trop d'essais de mot de passe : adresse bloquée {BLOCAGE / 60:.0f} minutes"
    else:
        msg = "mot de passe incorrect"
    return {"type": "error", "code": "mdp", "msg": msg}


# ---------------------------------------------------------------------------
# Côté client (wwmp_client, lobby du lanceur, outil rejoindre)
# ---------------------------------------------------------------------------
def mdp_env() -> str | None:
    return nettoyer(os.environ.get(ENV))


async def lire_defi(reader: asyncio.StreamReader, attente: float = DEFI_ATTENTE) -> str | None:
    """Nonce du défi envoyé par un serveur protégé, ou None (serveur sans mot de passe : il
    n'envoie rien avant le hello). N'est appelé qu'avant notre hello : rien d'autre ne peut
    arriver ; une ligne qui ne serait pas un défi est ignorée."""
    try:
        line = await asyncio.wait_for(reader.readline(), attente)
    except (asyncio.TimeoutError, TimeoutError):
        return None
    try:
        m = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if isinstance(m, dict) and m.get("type") == "defi" and nonce_valide(m.get("nonce")):
        return m["nonce"]
    return None


async def champs_hello(reader: asyncio.StreamReader, mdp: str | None, attente: float = DEFI_ATTENTE) -> dict:
    """Champs à ajouter au hello : {"auth": …} si on a un mot de passe et que le serveur envoie
    un défi ; {} sinon (pas de mot de passe : on n'attend rien)."""
    mdp = nettoyer(mdp)
    if not mdp:
        return {}
    nonce = await lire_defi(reader, attente)
    return {"auth": preuve(mdp, nonce)} if nonce else {}


# ---------------------------------------------------------------------------
# Côté serveur (appelé par wwmp_server.Server.client)
# ---------------------------------------------------------------------------
async def controler_hello(garde: Garde, p, msg: dict | None, log) -> bool:
    """Premier message d'une connexion à un serveur protégé : True si c'est un hello avec la bonne
    preuve (``p.auth`` passe à True). Sinon : attente DELAI_REFUS s, « error » code mdp, False
    (l'appelant ferme la connexion). Le mot de passe et la preuve ne sont jamais journalisés."""
    if not isinstance(msg, dict) or msg.get("type") != "hello":
        # y compris une ligne illisible : sinon, en envoyant du charabia toutes les 4 s, une connexion
        # sans mot de passe resterait ouverte (et occuperait une place) indéfiniment
        quoi = str(msg.get("type"))[:16] if isinstance(msg, dict) else "ligne illisible"
        log(f"{p.peer} (id {p.id}) : « {quoi} » avant le mot de passe, connexion fermée")
        return False
    r = garde.verifier(p.ip, getattr(p, "defi", None), msg.get("auth"))
    if r == "ok":
        p.auth = True
        return True
    nom = str(msg.get("name", "?"))[:24]
    texte = {"requis": "sans mot de passe", "faux": "mot de passe incorrect",
             "bloquee": f"mot de passe incorrect, {ECHECS_MAX} essais : adresse bloquée {BLOCAGE / 60:.0f} min"}[r]
    log(f"{nom} ({p.peer}) refusé : {texte}")
    await asyncio.sleep(garde.delai_refus)
    p.send(message_refus(r))
    try:
        await asyncio.wait_for(p.writer.drain(), 2.0)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass
    return False
