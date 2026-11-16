"""Administration du serveur dédié Docker (docs/hotes/JOUER_PAR_INTERNET.md) : compte admin, configuration du volume.

Le serveur dédié Docker est lancé avec ``--donnees /donnees --config /donnees/config.json``. Tout ce qui se règle se
trouve alors dans le volume, et se change depuis le lanceur (Héberger › « Sur mon serveur dédié » › « Administrer le
serveur »), sans rien éditer dans docker-compose.yml :

  /donnees/config.json      configuration de la partie (CLES ci-dessous), écrite par le serveur
  /donnees/admin.txt        compte d'administration (utilisateur, mot de passe) s'il n'est pas dans le .env
  /donnees/mot_de_passe.txt mot de passe de partie (commun/acces.py), réglable aussi depuis le lanceur
  /donnees/mondes/…         mondes persistants ; cles.json, spoilers, mises_a_jour/

Compte admin (DISTINCT du mot de passe de partie) : variables WWMP_ADMIN_UTILISATEUR / WWMP_ADMIN_MOT_DE_PASSE (fichier
.env à côté de docker-compose.yml : elles l'emportent tant qu'elles y sont), sinon admin.txt du volume, sinon créé au
hasard au premier démarrage, écrit dans admin.txt et affiché UNE fois dans le journal.

Protocole (une connexion par demande ; le mot de passe admin ne passe jamais sur le réseau) :

  client -> serveur : {"type": "admin_defi"}
  serveur -> client : {"type": "admin_defi", "v": 1, "nonce": "32 hex"}          (neuf à chaque demande)
  client -> serveur : {"type": "admin", "utilisateur": U, "req": REQ, "auth": HMAC-SHA256(K, "wwmp-admin1|" + nonce +
                       "|" + SHA-256(json canonique de REQ))}  avec K = SHA-256("wwmp-admin|" + u minuscule + "|" + mdp)
  serveur -> client : {"type": "admin_rep", "ok": 1, …, "sig": HMAC(K, "wwmp-admin1-rep|" + nonce + "|" + SHA-256(json
                       canonique de la réponse sans type ni sig))}  ou  {"type": "admin_rep", "ok": 0, "code", "msg",
                       "msg_en"} puis fermeture.

La demande est donc liée au nonce (rejouée, elle est refusée) et à son contenu (modifiée en route, elle est refusée) ;
la réponse est signée. Les secrets transportés (nouveau mot de passe de partie, mot de passe de partie actuel renvoyé
au lanceur) sont chiffrés par un flot dérivé de K et du nonce (``chiffrer``), jamais en clair. Le reste du trafic
n'est pas chiffré (comme le jeu : passer par Tailscale / ZeroTier pour tout chiffrer).

Garde-fous : DELAI_REFUS s avant un refus ; ECHECS_MAX mauvaises preuves par adresse en FENETRE s -> adresse bloquée
BLOCAGE s pour l'administration (hors adresse de ce PC). Aucune commande arbitraire : seules « lire », « ecrire » et
« redemarrer » existent, et « ecrire » ne prend que les clés de CLES, chacune validée.

Ce qui l'emporte : config.json (le lanceur l'écrit). Les options de ligne de commande correspondantes (--monde,
--rando-graine, --rando-reglages, --mode-hanteurs, --hanteurs-config, --max-joueurs, --deathlink, --vie-partagee, --degats,
--pv-monstres, --autorite-preferee, --validation, --robot) ne servent qu'à CRÉER config.json au premier démarrage (migration d'un
docker-compose.override.yml ; une clé absente d'un ancien config.json est ajoutée depuis l'option) ; ensuite elles
sont ignorées, avec une ligne dans le journal. « lire » renvoie aussi « autorites » (autorité de chaque zone occupée,
lot S1), « anomalies » (journal de la validation côté serveur, lot S2) et « robot » (état du joueur robot,
serveur/robot.py ; None sans --robot auto) ; « ecrire » accepte « effacer_anomalies »
(vide ce journal). Un ancien lanceur les ignore.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time

from commun import acces

ENV_UTILISATEUR = "WWMP_ADMIN_UTILISATEUR"
ENV_MDP = "WWMP_ADMIN_MOT_DE_PASSE"
FICHIER = "admin.txt"
CONFIG = "config.json"
UTILISATEUR_DEFAUT = "admin"
UTILISATEUR_MAX = 32
V = 1
DELAI_REFUS = 1.0
ECHECS_MAX = 5                    # mauvaises preuves par adresse…
FENETRE = 600.0                   # … en 10 minutes …
BLOCAGE = 900.0                   # … -> administration bloquée 15 minutes pour cette adresse
SUIVIES_MAX = 10000
SECRET_MAX = 256
COMMANDES = ("lire", "ecrire", "redemarrer")
FORMAT = "wwmp-config"

# clés de config.json ; les autres options du serveur restent celles de la ligne de commande
CLES = ("monde", "rando_graine", "rando_reglages", "mode_hanteurs", "hanteurs", "max_joueurs", "deathlink",
        "degats", "pv_monstres", "autorite_preferee", "validation", "robot", "vie_partagee")
A_CHAUD = ("deathlink", "degats", "pv_monstres", "max_joueurs", "hanteurs", "autorite_preferee", "validation", "robot",
           "vie_partagee")
AU_REDEMARRAGE = ("monde", "rando_graine", "rando_reglages", "mode_hanteurs")
OPTIONS = {"monde": "--monde", "rando_graine": "--rando-graine", "rando_reglages": "--rando-reglages",
           "mode_hanteurs": "--mode-hanteurs", "hanteurs": "--hanteurs-config", "max_joueurs": "--max-joueurs",
           "deathlink": "--deathlink", "degats": "--degats", "pv_monstres": "--pv-monstres",
           "autorite_preferee": "--autorite-preferee", "validation": "--validation", "robot": "--robot",
           "vie_partagee": "--vie-partagee"}


class ConfigIllisible(Exception):
    pass


class ErreurAdmin(Exception):
    """Refus ou erreur côté lanceur : ``fr`` / ``en`` (le message ne contient jamais de mot de passe)."""

    def __init__(self, fr: str, en: str = "", code: str = ""):
        super().__init__(fr)
        self.fr, self.en, self.code = fr, en or fr, code


# ---------------------------------------------------------------------------------------------------------
# Cryptographie (HMAC-SHA256, rien d'autre)
# ---------------------------------------------------------------------------------------------------------
def nettoyer_utilisateur(u) -> str | None:
    if not isinstance(u, str):
        return None
    u = "".join(c for c in u.strip() if c.isprintable() and not c.isspace())
    return u[:UTILISATEUR_MAX] or None


def cle(utilisateur: str, mdp: str) -> bytes:
    return hashlib.sha256(("wwmp-admin|" + (nettoyer_utilisateur(utilisateur) or "").lower() + "|"
                           + (acces.nettoyer(mdp) or "")).encode("utf-8")).digest()


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def preuve(k: bytes, nonce: str, req) -> str:
    return hmac.new(k, ("wwmp-admin1|" + nonce + "|" + hashlib.sha256(canon(req)).hexdigest()).encode("ascii"),
                    hashlib.sha256).hexdigest()


def signature(k: bytes, nonce: str, rep: dict) -> str:
    corps = {a: b for a, b in rep.items() if a not in ("type", "sig")}
    return hmac.new(k, ("wwmp-admin1-rep|" + nonce + "|" + hashlib.sha256(canon(corps)).hexdigest()).encode("ascii"),
                    hashlib.sha256).hexdigest()


def _flot(k: bytes, nonce: str, sens: str, n: int) -> bytes:
    out = b""
    i = 0
    while len(out) < n:
        out += hashlib.sha256(k + f"|chiffre|{sens}|{nonce}|{i}".encode("ascii")).digest()
        i += 1
    return out[:n]


def chiffrer(k: bytes, nonce: str, sens: str, texte: str) -> str:
    """``sens`` : « c » (lanceur -> serveur) ou « s » (serveur -> lanceur) : jamais deux fois le même flot."""
    b = str(texte).encode("utf-8")[:SECRET_MAX]
    return bytes(x ^ y for x, y in zip(b, _flot(k, nonce, sens, len(b)))).hex()


def dechiffrer(k: bytes, nonce: str, sens: str, h) -> str | None:
    if not isinstance(h, str) or len(h) > 2 * SECRET_MAX or len(h) % 2:
        return None
    try:
        b = bytes.fromhex(h)
        return bytes(x ^ y for x, y in zip(b, _flot(k, nonce, sens, len(b)))).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------------------------------------
# Compte admin (serveur)
# ---------------------------------------------------------------------------------------------------------
def lire_fichier_compte(chemin: str) -> tuple[str | None, str | None]:
    u = m = None
    try:
        with open(chemin, "r", encoding="utf-8-sig") as f:
            for ligne in f:
                cle_, sep, val = ligne.strip().partition("=")
                if not sep or cle_.startswith("#"):
                    continue
                if cle_.strip() == "utilisateur":
                    u = nettoyer_utilisateur(val)
                elif cle_.strip() == "mot_de_passe":
                    m = acces.nettoyer(val)
    except OSError:
        return None, None
    return u, m


def ecrire_fichier_compte(chemin: str, utilisateur: str, mdp: str) -> None:
    os.makedirs(os.path.dirname(chemin) or ".", exist_ok=True)
    fd = os.open(chemin, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# Compte d'administration du serveur Wind Waker Coop (lanceur : Héberger, « Sur mon serveur dédié »,\n"
                "# « Administrer le serveur »). Distinct du mot de passe de partie. Pour le changer : modifie ces\n"
                "# deux lignes puis redémarre le serveur, ou mets WWMP_ADMIN_UTILISATEUR / WWMP_ADMIN_MOT_DE_PASSE\n"
                "# dans le fichier .env (elles l'emportent sur ce fichier).\n"
                f"utilisateur={utilisateur}\nmot_de_passe={mdp}\n")


def compte(dossier: str, env=None, log=print) -> tuple[str, str, str]:
    """(utilisateur, mot de passe, d'où il vient). Variables d'environnement, sinon admin.txt, sinon créé au hasard
    (écrit dans admin.txt et affiché UNE fois dans le journal : c'est la seule fois où un mot de passe y est écrit)."""
    env = os.environ if env is None else env
    u_env = nettoyer_utilisateur(env.get(ENV_UTILISATEUR))
    m_env = acces.nettoyer(env.get(ENV_MDP))
    if m_env:
        return u_env or UTILISATEUR_DEFAUT, m_env, f"variables {ENV_UTILISATEUR} / {ENV_MDP}"
    chemin = os.path.join(dossier, FICHIER)
    u, m = lire_fichier_compte(chemin)
    if m:
        return u_env or u or UTILISATEUR_DEFAUT, m, f"fichier {chemin}"
    u = u_env or u or UTILISATEUR_DEFAUT
    m = acces.generer(5, 4)
    ecrire_fichier_compte(chemin, u, m)
    log(f"COMPTE ADMIN DU SERVEUR créé : utilisateur « {u} », mot de passe « {m} » (affiché cette fois seulement ; "
        f"il est aussi dans {chemin}). Lanceur : Héberger, « Sur mon serveur dédié », « Administrer le serveur »")
    return u, m, f"fichier {chemin}, créé maintenant"


# ---------------------------------------------------------------------------------------------------------
# config.json
# ---------------------------------------------------------------------------------------------------------
def _defauts(max_players: int) -> dict:
    return {"monde": None, "rando_graine": None, "rando_reglages": None, "mode_hanteurs": False, "hanteurs": None,
            "max_joueurs": max_players, "deathlink": False, "degats": 1, "pv_monstres": "off",
            "autorite_preferee": None, "validation": "journal", "robot": "off", "vie_partagee": False}


def valider(d, max_players: int) -> tuple[dict, list[str]]:
    """Clés connues de ``d`` validées -> (valeurs, erreurs en français). Une clé invalide n'est pas reprise."""
    from commun import difficulte as dif_mod
    from synchro import hanteurs as hmod
    from serveur import monde_persistant as mpm
    from rando import noyau as rnd
    out, err = {}, []
    if not isinstance(d, dict):
        return out, ["configuration : objet JSON attendu"]
    for k in CLES:
        if k not in d:
            continue
        v = d[k]
        if k == "monde":
            n = mpm.nettoyer_nom(v) if v is not None else None
            if v is not None and not n:
                err.append("monde : nom vide")
                continue
            out[k] = n or None
        elif k == "rando_graine":
            out[k] = (rnd.nettoyer_graine(v) or None) if v is not None else None
        elif k == "rando_reglages":
            c = "".join(str(v).split()) if isinstance(v, str) else None
            if c:
                try:
                    from rando import moteur as rando_moteur
                    rando_moteur.reglages_depuis_chaine(c)
                except Exception as e:          # chaîne refusée par le moteur (ErreurRando), quelle qu'en soit la forme
                    err.append(f"chaîne de réglages du randomizer refusée ({str(e)[:160]})")
                    continue
            out[k] = c or None
        elif k in ("mode_hanteurs", "deathlink", "vie_partagee"):
            if not isinstance(v, bool):
                err.append(f"{k} : vrai ou faux attendu")
                continue
            out[k] = v
        elif k == "hanteurs":
            out[k] = hmod.valider_config(v) if isinstance(v, dict) else None
        elif k == "max_joueurs":
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= max_players:
                err.append(f"nombre de joueurs : de 1 à {max_players}")
                continue
            out[k] = v
        elif k == "degats":
            if v not in dif_mod.DEGATS or isinstance(v, bool):
                err.append("dégâts : 1, 2, 3 ou 4")
                continue
            out[k] = v
        elif k == "pv_monstres":
            if v not in dif_mod.PV_MODES:
                err.append("vie des monstres : off, demi ou link")
                continue
            out[k] = v
        elif k == "autorite_preferee":
            # lot S1 (serveur/autorite.py) : pseudos séparés par des virgules, ou « @hote » ; vide = aucun
            if v is not None and not isinstance(v, (str, list)):
                err.append("PC d'autorité préféré : pseudo(s) ou @hote attendu(s)")
                continue
            from serveur import autorite
            out[k] = autorite.normaliser(v)
        elif k == "validation":
            # lot S2 (serveur/validation.py) : journal (défaut), refus ou off
            from serveur import validation as valid_mod
            if v not in valid_mod.MODES:
                err.append("validation : journal, refus ou off")
                continue
            out[k] = v
        elif k == "robot":
            # joueur robot (serveur/robot.py) : off (défaut) ou auto
            if v not in ("off", "auto"):
                err.append("robot : off ou auto")
                continue
            out[k] = v
    return out, err


def lire_config(chemin: str) -> dict | None:
    """Configuration du volume (None si le fichier n'existe pas). Lève ConfigIllisible (rien n'est écrasé)."""
    try:
        with open(chemin, "r", encoding="utf-8-sig") as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise ConfigIllisible(f"{chemin} illisible ({e}) : corrige-le ou supprime-le (le serveur en recréera un "
                              "depuis ses options), rien n'a été écrasé") from None
    if not isinstance(doc, dict):
        raise ConfigIllisible(f"{chemin} : objet JSON attendu")
    return doc


def ecrire_config(chemin: str, cfg: dict) -> None:
    os.makedirs(os.path.dirname(chemin) or ".", exist_ok=True)
    doc = {"format": FORMAT, "v": V, "modifie": time.strftime("%Y-%m-%d %H:%M:%S"),
           **{k: cfg.get(k) for k in CLES}}
    tmp = chemin + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, chemin)


def options_lignes(args, hcfg) -> dict:
    """Valeurs des options de ligne de commande, rangées comme config.json."""
    return {"monde": args.monde, "rando_graine": None if getattr(args, "no_rando", False) else args.rando_graine,
            "rando_reglages": args.rando_reglages, "mode_hanteurs": bool(args.mode_hanteurs),
            "hanteurs": hcfg if isinstance(hcfg, dict) else None, "max_joueurs": args.max_joueurs,
            "deathlink": bool(args.deathlink and not args.no_deathlink), "degats": args.degats,
            "pv_monstres": args.pv_monstres, "autorite_preferee": getattr(args, "autorite_preferee", None) or None,
            "validation": getattr(args, "validation", None) or "journal",
            "robot": getattr(args, "robot", None) or "off",
            "vie_partagee": bool(getattr(args, "vie_partagee", False) and not getattr(args, "no_vie_partagee", False))}


def preparer(chemin: str, args, defauts_args, hcfg, max_players: int, log=print) -> tuple[dict, dict | None]:
    """Au démarrage (--config) : relit config.json (le crée depuis les options au premier démarrage), signale les
    options ignorées, puis REMPLACE les options correspondantes de ``args``. Renvoie (config, réglages Hanteurs).
    ``defauts_args`` : options de la même ligne de commande sans rien (pour savoir ce qui a été donné)."""
    cli = options_lignes(args, hcfg)
    base = options_lignes(defauts_args, None)
    donnees = {k: v for k, v in cli.items() if v != base[k]}
    doc = lire_config(chemin)
    if doc is None:
        cfg = dict(_defauts(max_players), **cli)
        cfg, err = valider(cfg, max_players)
        cfg = dict(_defauts(max_players), **cfg)
        for e in err:
            log(f"configuration : {e} (valeur par défaut gardée)")
        ecrire_config(chemin, cfg)
        log(f"configuration du serveur créée : {chemin}"
            + (" ; reprises de la ligne de commande : " + ", ".join(OPTIONS[k] for k in CLES if k in donnees)
               + ". Tu peux maintenant retirer ces options (docker-compose.override.yml) : la configuration se règle "
                 "depuis le lanceur (Héberger, « Administrer le serveur »)" if donnees else ""))
    else:
        lus, err = valider(doc, max_players)
        for e in err:
            log(f"configuration : {e} (dans {chemin}, valeur par défaut gardée)")
        cfg = dict(_defauts(max_players))
        manquantes = [k for k in CLES if k not in doc]
        for k in manquantes:
            cfg[k] = cli[k]
        cfg.update(lus)
        ignorees = [OPTIONS[k] for k in CLES if k in donnees and k not in manquantes and donnees[k] != cfg[k]]
        if ignorees:
            log("configuration : option(s) " + ", ".join(ignorees) + f" ignorée(s) : {chemin} l'emporte (réglé "
                "depuis le lanceur). Retire-les de la ligne de commande (docker-compose.override.yml)")
        if manquantes or err:
            ecrire_config(chemin, cfg)
        log(f"configuration du serveur : {chemin}")
    args.monde = cfg["monde"]
    args.rando_graine = cfg["rando_graine"]
    args.rando_reglages = cfg["rando_reglages"]
    args.mode_hanteurs = cfg["mode_hanteurs"]
    args.max_joueurs = cfg["max_joueurs"]
    args.deathlink, args.no_deathlink = cfg["deathlink"], False
    args.vie_partagee, args.no_vie_partagee = cfg["vie_partagee"], False
    args.degats, args.pv_monstres = cfg["degats"], cfg["pv_monstres"]
    args.autorite_preferee = cfg["autorite_preferee"]
    args.validation = cfg["validation"]
    args.robot = cfg["robot"]
    if cfg["rando_graine"]:
        args.no_rando = False
    return cfg, cfg["hanteurs"]


# ---------------------------------------------------------------------------------------------------------
# Serveur : garde, demandes
# ---------------------------------------------------------------------------------------------------------
class Admin:
    """Compte admin (seulement sa clé dérivée), configuration du volume, essais par adresse."""

    def __init__(self, utilisateur: str, mdp: str, chemin_config: str, config: dict, dossier_mondes: str = "",
                 mdp_fichier: str | None = None, mdp_source: str = "", source_compte: str = "",
                 horloge=time.monotonic, delai_refus: float = DELAI_REFUS):
        self.utilisateur = nettoyer_utilisateur(utilisateur) or UTILISATEUR_DEFAUT
        self._cle = cle(self.utilisateur, mdp)
        self.chemin = chemin_config
        self.config = dict(config)
        self.dossier_mondes = dossier_mondes
        self.mdp_fichier = mdp_fichier          # mot_de_passe.txt du volume, s'il est la source du mot de passe
        self.mdp_source = mdp_source
        self.source_compte = source_compte      # d'où vient le compte (jamais le mot de passe lui-même)
        self.horloge, self.delai_refus = horloge, delai_refus
        self.echecs: dict[str, list[float]] = {}
        self.bloquees: dict[str, float] = {}

    def bloquee(self, ip: str) -> bool:
        fin = self.bloquees.get(ip)
        if fin is None:
            return False
        if self.horloge() >= fin:
            del self.bloquees[ip]
            self.echecs.pop(ip, None)
            return False
        return True

    def verifier(self, ip: str, utilisateur, nonce: str | None, req, auth) -> str:
        """« ok », « faux » (échec compté) ou « bloquee »."""
        if self.bloquee(ip):
            return "bloquee"
        u = nettoyer_utilisateur(utilisateur)
        bon = False
        if nonce and u and isinstance(auth, str) and auth.isascii() and isinstance(req, dict):
            try:
                attendu = preuve(self._cle, nonce, req)
            except (TypeError, ValueError):
                attendu = ""
            # utilisateur ET mot de passe dans la clé : un mauvais utilisateur donne une mauvaise preuve
            bon = bool(attendu) and u.lower() == self.utilisateur.lower() and hmac.compare_digest(attendu, auth[:64])
        if bon:
            self.echecs.pop(ip, None)
            return "ok"
        now = self.horloge()
        if len(self.echecs) + len(self.bloquees) > SUIVIES_MAX:
            self.echecs = {k: v for k, v in self.echecs.items() if v and now - v[-1] < FENETRE}
            self.bloquees = {k: v for k, v in self.bloquees.items() if v > now}
        e = [t for t in self.echecs.get(ip, []) if now - t < FENETRE] + [now]
        self.echecs[ip] = e
        if len(e) >= ECHECS_MAX and ip not in acces.LOCALES:
            self.bloquees[ip] = now + BLOCAGE
            return "bloquee"
        return "faux"

    def enregistrer(self) -> None:
        ecrire_config(self.chemin, self.config)


def refus(code: str) -> dict:
    if code == "indisponible":
        fr = ("ce serveur n'a pas de compte d'administration (seul le serveur dédié Docker en a un : option --config "
              "/donnees/config.json)")
        en = "this server has no administration account (only the Docker dedicated server has one: --config option)"
    elif code == "bloquee":
        fr = f"trop d'essais : administration bloquée {BLOCAGE / 60:.0f} minutes pour cette adresse"
        en = f"too many attempts: administration blocked for {BLOCAGE / 60:.0f} minutes for this address"
    elif code == "defi":
        fr, en = "demande d'administration sans défi (recommence)", "administration request without challenge (retry)"
    else:
        fr, en = "utilisateur ou mot de passe admin incorrect", "wrong admin user or password"
    return {"type": "admin_rep", "ok": 0, "code": code, "msg": fr, "msg_en": en}


async def recevoir(srv, p, msg: dict, log) -> bool:
    """Message « admin_defi » / « admin » d'une connexion (avant ou sans hello). False : la connexion est fermée."""
    a = getattr(srv, "admin", None)
    p.admin_vu = True

    async def refuser(code: str) -> bool:
        await asyncio.sleep(a.delai_refus if a is not None else DELAI_REFUS)
        p.send(refus(code))
        try:
            await asyncio.wait_for(p.writer.drain(), 2.0)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        return False

    if a is None:
        log(f"{p.peer} : demande d'administration refusée (pas de compte admin sur ce serveur)")
        return await refuser("indisponible")
    if msg.get("type") == "admin_defi":
        if a.bloquee(p.ip):
            return await refuser("bloquee")
        p.admin_nonce = secrets.token_hex(16)
        p.send({"type": "admin_defi", "v": V, "nonce": p.admin_nonce})
        return True
    nonce = getattr(p, "admin_nonce", None)
    p.admin_nonce = None                    # une demande par défi : une demande rejouée est refusée
    if not nonce:
        return await refuser("defi")
    req = msg.get("req")
    r = a.verifier(p.ip, msg.get("utilisateur"), nonce, req, msg.get("auth"))
    if r != "ok":
        log(f"{p.peer} : administration refusée ("
            + ("identifiants incorrects" if r == "faux" else f"{ECHECS_MAX} essais, adresse bloquée "
                                                             f"{BLOCAGE / 60:.0f} min") + ")")
        return await refuser("bloquee" if r == "bloquee" else "identifiants")
    p.name = ("admin " + a.utilisateur)[:24]
    try:
        rep = executer(srv, p, req, nonce, log)
    except (KeyError, TypeError, ValueError, AttributeError, OSError) as e:
        log(f"administration : demande en échec ({type(e).__name__}: {e})")
        rep = {"ok": 0, "msg": f"échec côté serveur ({type(e).__name__})",
               "msg_en": f"server-side failure ({type(e).__name__})"}
    rep = dict(rep, nonce=nonce)
    rep["sig"] = signature(a._cle, nonce, rep)
    p.send(dict(rep, type="admin_rep"))
    return True


def _vue_mondes(a: Admin, srv) -> list[dict]:
    from serveur import monde_persistant as mpm
    actif = srv.mp.id if getattr(srv, "mp", None) is not None else None
    out = []
    for m in mpm.lister(a.dossier_mondes)[:64] if a.dossier_mondes else []:
        out.append({"id": m["id"], "nom": m.get("nom") or m["id"], "modifie": m.get("modifie") or "",
                    "joueurs": len(m.get("joueurs") or []), "actif": m["id"] == actif,
                    "erreur": bool(m.get("erreur"))})
    return out


def _actuel(srv) -> dict:
    h = getattr(srv, "hanteurs", None)
    dif = getattr(srv, "difficulte", None) or {"degats": 1, "pv_monstres": "off"}
    pref = getattr(srv, "pref", None)
    vies = getattr(srv, "vies", None)
    return {"deathlink": bool(srv.morts.on), "vie_partagee": bool(vies.on) if vies is not None else False,
            "degats": dif.get("degats", 1),
            "pv_monstres": dif.get("pv_monstres", "off"), "max_joueurs": srv.max_players,
            "mode_hanteurs": h is not None, "hanteurs": dict(h.cfg) if h is not None else None,
            "autorite_preferee": pref.valeur if pref is not None else None,
            "validation": srv.valid.mode if getattr(srv, "valid", None) is not None else None,
            "robot": srv.robot.mode if getattr(srv, "robot", None) is not None else None}


def _monde_vue(srv) -> dict | None:
    mp = getattr(srv, "mp", None)
    if mp is None:
        return None
    t = getattr(srv, "rando", None)
    return {"id": mp.id, "nom": mp.nom,
            "rando": {"graine": t.graine, "mode": t.mode, "empreinte": t.empreinte} if t is not None else None,
            "table": mp.rando_doc is not None}


def lire(srv, a: Admin, nonce: str) -> dict:
    from commun.protocole import MAX_PLAYERS, VERSION
    mdp = ""
    clair = acces.lire_fichier(a.mdp_fichier) if a.mdp_fichier else None
    if clair and srv.acces.actif:
        mdp = chiffrer(a._cle, nonce, "s", clair)
    return {"ok": 1, "config": {k: a.config.get(k) for k in CLES}, "actuel": _actuel(srv),
            "monde": _monde_vue(srv), "mondes": _vue_mondes(a, srv),
            "joueurs": sorted(o.name for o in srv.players.values() if getattr(o, "joueur", False))[:64],
            "version": VERSION, "max_possible": MAX_PLAYERS, "difficulte_on": bool(srv.difficulte_on),
            "mdp_partie": mdp, "mdp_modifiable": bool(a.mdp_fichier), "utilisateur": a.utilisateur,
            # lot S1 : autorité de chaque zone occupée (lecture seule ; absent d'un ancien serveur)
            "autorites": srv.autorites_vue() if hasattr(srv, "autorites_vue") else [],
            # lot S2 : journal d'anomalies (absent d'un ancien serveur)
            "anomalies": srv.valid.vue() if getattr(srv, "valid", None) is not None else None,
            # joueur robot (serveur/robot.py) : None si --robot off (absent d'un ancien serveur)
            "robot": srv.robot_vue() if hasattr(srv, "robot_vue") else None}


def _meme_monde(srv, a: Admin, voulu) -> bool:
    mp = getattr(srv, "mp", None)
    if voulu is None:
        return a.config.get("monde") is None or (mp is not None and a.config.get("monde") == mp.id)
    if mp is None:
        return False
    v = str(voulu).lower()
    return v in (mp.id.lower(), (mp.nom or "").lower())


def ecrire(srv, p, a: Admin, req: dict, nonce: str, log) -> dict:
    from commun.protocole import MAX_PLAYERS
    d = req.get("config") if isinstance(req.get("config"), dict) else {}
    vals, err = valider(d, MAX_PLAYERS)
    mdp_neuf = None
    if req.get("mdp_partie") is not None:
        mdp_neuf = acces.nettoyer(dechiffrer(a._cle, nonce, "c", req.get("mdp_partie")))
        if not mdp_neuf:
            err.append("mot de passe de partie vide")
        elif not a.mdp_fichier:
            err.append("le mot de passe de partie vient d'une option du serveur (" + (a.mdp_source or "?") + ") : "
                       "retire-la pour le régler ici")
    renommer = None
    if req.get("renommer"):
        from serveur import monde_persistant as mpm
        renommer = mpm.nettoyer_nom(req.get("renommer"))
        if getattr(srv, "mp", None) is None:
            err.append("pas de monde persistant à renommer")
        elif not renommer:
            err.append("nouveau nom du monde vide")
    if err:
        return {"ok": 0, "msg": " ; ".join(err), "msg_en": "refused: " + " ; ".join(err)}
    appliques, avert = [], []
    # avant tout changement à chaud (renommer change le nom auquel « monde » se compare)
    monde_change = "monde" in vals and not _meme_monde(srv, a, vals["monde"])
    redemarrage = monde_change
    # --- à chaud
    act = _actuel(srv)
    if "deathlink" in vals and vals["deathlink"] != act["deathlink"]:
        srv.admin_deathlink(vals["deathlink"])
        appliques.append("deathlink")
    if "vie_partagee" in vals and vals["vie_partagee"] != act["vie_partagee"] and hasattr(srv, "admin_vie_partagee"):
        if vals["vie_partagee"] and getattr(srv, "hanteurs", None) is not None:
            avert.append("vie partagée : impossible avec le mode Hanteurs")
        else:
            srv.admin_vie_partagee(vals["vie_partagee"])
            appliques.append("vie_partagee")
    dif = {k: vals[k] for k in ("degats", "pv_monstres") if k in vals and vals[k] != act[k]}
    if dif:
        if srv.difficulte_on:
            srv.admin_difficulte(dif)
            appliques.append("difficulte")
        else:
            avert.append("difficulté coupée sur ce serveur (--no-difficulte) : gardée pour plus tard")
    if "max_joueurs" in vals and vals["max_joueurs"] != srv.max_players:
        srv.max_players = vals["max_joueurs"]
        log(f"administration : {srv.max_players} joueurs au plus")
        appliques.append("max_joueurs")
    mode_change = "mode_hanteurs" in vals and vals["mode_hanteurs"] != act["mode_hanteurs"]
    if "autorite_preferee" in vals and hasattr(srv, "admin_autorite") \
            and srv.admin_autorite(vals["autorite_preferee"]):
        appliques.append("autorite_preferee")
    if "validation" in vals and hasattr(srv, "admin_validation") and srv.admin_validation(vals["validation"]):
        appliques.append("validation")
    if "robot" in vals and hasattr(srv, "admin_robot"):
        ok_r = srv.admin_robot(vals["robot"])
        if ok_r is True:
            appliques.append("robot")
        elif isinstance(ok_r, str):
            avert.append(ok_r)
    if req.get("effacer_anomalies") in (1, True) and getattr(srv, "valid", None) is not None:
        srv.valid.effacer()
        log("administration : journal d'anomalies effacé")
        appliques.append("anomalies_effacees")
    if "hanteurs" in vals and vals["hanteurs"] is not None and srv.hanteurs is not None and not mode_change \
            and vals["hanteurs"] != srv.hanteurs.cfg:
        srv.hanteurs_config(vals["hanteurs"], "l'administrateur")
        appliques.append("hanteurs")
    if mdp_neuf and (mdp_neuf != acces.lire_fichier(a.mdp_fichier) or not srv.acces.actif):
        acces.ecrire_fichier(a.mdp_fichier, mdp_neuf)
        srv.acces.changer_mdp(mdp_neuf)
        log("administration : mot de passe de partie changé (les joueurs déjà connectés restent)")
        appliques.append("mot_de_passe")
    if renommer and renommer != srv.mp.nom:
        srv.mp.commande(srv, p, {"cmd": "renommer", "nom": renommer})
        appliques.append("renommer")
    # --- au redémarrage
    ancien = dict(a.config)
    a.config.update(vals)
    if "monde" in vals and not monde_change:
        a.config["monde"] = ancien.get("monde")
    if mode_change:
        redemarrage = True
    rando = any(k in vals and vals[k] != ancien.get(k) for k in ("rando_graine", "rando_reglages"))
    if rando:
        mp = getattr(srv, "mp", None)
        if mp is not None and mp.rando_doc is not None and not redemarrage:
            avert.append(f"le monde « {mp.nom} » garde la table de sa première partie randomizer : la nouvelle graine "
                         "servira à un monde neuf (choisis « Nouveau monde »)")
        else:
            redemarrage = True
    a.enregistrer()
    if getattr(srv, "mp", None) is not None and appliques:
        srv.mp_marquer(rapide=True)
    log("administration : configuration enregistrée" + (f" ; appliqué tout de suite : {', '.join(appliques)}"
                                                       if appliques else "")
        + (" ; redémarrage du serveur" if redemarrage else ""))
    if redemarrage:
        srv.redemarrer("configuration changée par l'administrateur")
    return {"ok": 1, "appliques": appliques, "redemarrage": int(redemarrage), "avertissements": avert}


def executer(srv, p, req, nonce: str, log) -> dict:
    a = srv.admin
    cmd = req.get("cmd") if isinstance(req, dict) else None
    if cmd not in COMMANDES:
        return {"ok": 0, "msg": f"commande inconnue {str(cmd)[:16]!r}", "msg_en": f"unknown command {str(cmd)[:16]!r}"}
    if cmd == "lire":
        return lire(srv, a, nonce)
    if cmd == "redemarrer":
        log("administration : redémarrage demandé")
        srv.redemarrer("redémarrage demandé par l'administrateur")
        return {"ok": 1, "redemarrage": 1}
    return ecrire(srv, p, a, req, nonce, log)


# ---------------------------------------------------------------------------------------------------------
# Lanceur : une demande
# ---------------------------------------------------------------------------------------------------------
async def demande_async(hote: str, port: int, utilisateur: str, mdp: str, req: dict, secrets_: dict | None = None,
                        attente: float = 10.0) -> dict:
    """Envoie ``req`` (``secrets_`` : champs de ``req`` à chiffrer, en clair ici) ; renvoie la réponse vérifiée, ses
    secrets déchiffrés. Lève ErreurAdmin."""
    u = nettoyer_utilisateur(utilisateur)
    m = acces.nettoyer(mdp)
    if not u or not m:
        raise ErreurAdmin("utilisateur et mot de passe admin requis", "admin user and password required", "vide")
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(hote, port, limit=1 << 20), attente)
    except (OSError, asyncio.TimeoutError) as e:
        raise ErreurAdmin(f"serveur {hote}:{port} injoignable ({type(e).__name__})",
                          f"server {hote}:{port} unreachable ({type(e).__name__})", "reseau") from None

    async def lire_msg(types: tuple) -> dict:
        while True:
            ligne = await asyncio.wait_for(reader.readline(), attente)
            if not ligne:
                raise ErreurAdmin("connexion fermée par le serveur", "connection closed by the server", "reseau")
            try:
                o = json.loads(ligne.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                continue
            if isinstance(o, dict) and o.get("type") == "admin_rep" and not o.get("ok"):
                raise ErreurAdmin(str(o.get("msg") or "refusé")[:300], str(o.get("msg_en") or o.get("msg") or
                                                                         "refused")[:300], str(o.get("code") or ""))
            if isinstance(o, dict) and o.get("type") in types:
                return o            # (le « defi » du mot de passe de partie est ignoré : l'admin n'en a pas besoin)

    try:
        writer.write(b'{"type":"admin_defi"}\n')
        await writer.drain()
        d = await lire_msg(("admin_defi",))
        nonce = d.get("nonce")
        if not acces.nonce_valide(nonce):
            raise ErreurAdmin("réponse du serveur invalide", "invalid server reply", "protocole")
        k = cle(u, m)
        req = dict(req)
        for champ, val in (secrets_ or {}).items():
            req[champ] = chiffrer(k, nonce, "c", val)
        writer.write(json.dumps({"type": "admin", "utilisateur": u, "req": req,
                                 "auth": preuve(k, nonce, req)}).encode("utf-8") + b"\n")
        await writer.drain()
        rep = await lire_msg(("admin_rep",))
        if rep.get("nonce") != nonce or not isinstance(rep.get("sig"), str) \
                or not hmac.compare_digest(signature(k, nonce, rep), rep["sig"]):
            raise ErreurAdmin("réponse du serveur non signée ou modifiée en route",
                              "server reply unsigned or modified on the way", "signature")
        if rep.get("mdp_partie"):
            rep["mdp_partie"] = dechiffrer(k, nonce, "s", rep["mdp_partie"]) or ""
        return rep
    except asyncio.TimeoutError:
        raise ErreurAdmin(f"serveur {hote}:{port} : pas de réponse", f"server {hote}:{port}: no reply",
                          "reseau") from None
    except (ConnectionError, OSError) as e:
        raise ErreurAdmin(f"serveur {hote}:{port} : connexion coupée ({type(e).__name__})",
                          f"server {hote}:{port}: connection lost ({type(e).__name__})", "reseau") from None
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2.0)
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass


def demande(hote: str, port: int, utilisateur: str, mdp: str, req: dict, secrets_: dict | None = None,
            attente: float = 10.0) -> dict:
    """Bloquant (fil à part du lanceur)."""
    return asyncio.run(demande_async(hote, port, utilisateur, mdp, req, secrets_, attente))
