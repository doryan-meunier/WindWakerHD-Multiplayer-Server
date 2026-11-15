"""Rapport d'essai : socle commun au lanceur (``lanceur/rapport.py``) et au serveur dédié (``serveur/rapport.py``).

Un rapport est UN zip daté qu'un joueur envoie au développeur après un essai : journaux (fin seulement), versions,
réglages EXPURGÉS, et un résumé lisible ``rapport.txt`` en tête. Ce module ne sait rien du lanceur ni du serveur :

* ``Expurgeur`` : retire d'un texte (ou d'un JSON) ce qui ne doit jamais sortir du PC : mots de passe et jetons
  (valeurs connues et motifs « mot_de_passe=… », « "mdp": "…" »), adresses e-mail, nom d'utilisateur Windows / Linux
  dans les chemins (« <utilisateur> »), adresses IP publiques (« <ip publique> ») ; les adresses locales gardent leur
  début (192.168.1.x : on voit le réseau, pas la machine) ; 127.0.0.1 et 0.0.0.0 restent ;
* ``lire_journal`` : la FIN d'un journal (``limite`` octets), en UTF-8 (UTF-16 avec ou sans BOM converti), coupée à
  une ligne entière ;
* ``erreurs`` : les lignes d'erreur d'un journal (Traceback, erreur, ÉCHEC, « [code mods] » qui échoue…), sans
  doublon ;
* ``Archive`` : le zip, écrit à côté puis renommé ; seuls des noms ``.txt`` / ``.log`` / ``.json`` sont permis, jamais
  une sauvegarde, un fichier du jeu, une clé (``nom_permis``) ; taille totale bornée.

Python pur, sans dépendance : le serveur dédié l'importe aussi.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time
import zipfile

LIMITE_JOURNAL = 2 << 20          # derniers 2 Mio de chaque journal
LIMITE_TOTALE = 24 << 20          # contenu du zip (avant compression) au plus
LIMITE_JSON = 512 << 10           # un fichier JSON relu au plus
ERREURS_MAX = 40                  # lignes d'erreur gardées par journal dans le résumé
MASQUE = "<masqué>"
SECRET = "<secret>"
UTILISATEUR = "<utilisateur>"
EMAIL = "<e-mail>"
IP_PUBLIQUE = "<ip publique>"
NOM_HOTE = "<nom d'hôte>"

EXTENSIONS = (".txt", ".log", ".json")
# jamais dans un rapport, même renommé : sauvegardes, fichiers du jeu, dump, clés, paquets générés du randomizer, greffe
NOMS_INTERDITS = re.compile(
    r"(\.sav\b|cking|\.rpx\b|\.rpl\b|\.elf\b|\.szs\b|\.arc\b|\.bfres\b|\.wud\b|\.wux\b|\.iso\b|keys?\.txt|otp\.bin|"
    r"seeprom|cles\.json|cle_privee|mot_de_passe\.txt|admin\.txt|wwmp-rando-code|greffe|spoiler|monde\.json)", re.I)

# clés d'un JSON dont la valeur n'est jamais recopiée (mot de passe, jeton, compte admin…)
CLES_SECRETES = re.compile(r"(mdp|mot_?de_?passe|passe|password|passwd|secret|jeton|token|api_?key|cle_privee|"
                           r"comptes|auth|nonce|sig|utilisateur|e_?mail|courriel)", re.I)
# clés dont la valeur est une adresse de serveur (nom d'hôte masqué, IP traitée comme les autres)
CLES_ADRESSES = re.compile(r"^(adresse|defaut_ip|ip|hote|serveur|host|source)$", re.I)
NOMS_COMMUNS = {"admin", "user", "users", "utilisateur", "root", "joueur", "public", "default", "wwmp", "test",
                "home", "lanceur", "serveur", "client"}

_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_RE_IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\.?\d)(?!\w)")
_RE_IPV6 = re.compile(r"(?<![\w:.])((?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|"
                      r"(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}:){0,5}[0-9A-Fa-f]{1,4}|"
                      r"(?:[0-9A-Fa-f]{1,4}:){1,7}:(?![0-9A-Fa-f]))(?![\w:])")
# chemins de dossiers personnels : Windows (« \ » simple ou doublé d'un JSON, ou « / »), Linux, macOS
_RE_MAISON_WIN = re.compile(r"(?i)([A-Z]:(?:\\{1,2}|/)(?:Users|Utilisateurs|Documents and Settings)(?:\\{1,2}|/))"
                            r"([^\\/:*?\"<>|\r\n,;]+(?=\\)|[^\\/:*?\"<>|\r\n\s,;]+)")
_RE_MAISON_NIX = re.compile(r"((?<![\w.])/(?:home|Users)/)([^/\s\"'<>|:]+)")
_CLE_VALEUR = (r"(?i)((?:mot[ _-]?de[ _-]?passe|mdp|password|passwd|jeton|token|secret|api[_-]?key|"
               r"WWMP_MOT_DE_PASSE|WWMP_ADMIN_MOT_DE_PASSE)")
_RE_CLE_EGAL = re.compile(_CLE_VALEUR + r"\s*[=:]\s*)(\"?)([^\s\"',;]{1,256})")
_RE_CLE_JSON = re.compile(r"(?i)(\"[^\"]*(?:mdp|mot_de_passe|password|passwd|jeton|token|secret)[^\"]*\"\s*:\s*)"
                          r"\"(?:[^\"\\]|\\.)*\"")


# ---------------------------------------------------------------------------
# Expurgation
# ---------------------------------------------------------------------------
def masquer_ip(texte_ip: str) -> str:
    """Une adresse IPv4 telle qu'elle sortira du PC : locale à moitié masquée, publique masquée, bouclage gardé."""
    try:
        ip = ipaddress.IPv4Address(texte_ip)
    except ValueError:
        return texte_ip
    o = texte_ip.split(".")
    if ip.is_loopback or ip.is_unspecified or texte_ip == "255.255.255.255":
        return texte_ip
    if o[0] == "192" and o[1] == "168":
        return f"192.168.{o[2]}.x"
    if o[0] == "10" or (o[0] == "172" and 16 <= int(o[1]) <= 31) or (o[0] == "169" and o[1] == "254"):
        return f"{o[0]}.{o[1]}.x.x"
    if o[0] == "100" and 64 <= int(o[1]) <= 127:          # réseau partagé (Tailscale, CGNAT)
        return f"100.{o[1]}.x.x"
    if ip.is_multicast or ip.is_reserved:
        return texte_ip
    return IP_PUBLIQUE


def _masquer_ipv6(texte_ip: str) -> str:
    try:
        ip = ipaddress.IPv6Address(texte_ip)
    except ValueError:
        return texte_ip
    if ip.is_loopback or ip.is_unspecified:
        return texte_ip
    if ip.is_link_local:
        return "fe80::x"
    if ip.is_private:
        return "<ipv6 locale>"
    return IP_PUBLIQUE


class Expurgeur:
    """``utilisateurs`` : noms de compte du PC (remplacés partout par <utilisateur>) ; ``secrets`` : valeurs exactes à
    ne jamais recopier (mots de passe retenus…) ; ``hotes`` : noms d'hôte de serveurs (DNS dynamique…)."""

    def __init__(self, utilisateurs=(), secrets=(), hotes=()):
        self.utilisateurs = sorted({u for u in utilisateurs if isinstance(u, str) and len(u.strip()) >= 2
                                    and u.strip().lower() not in NOMS_COMMUNS}, key=len, reverse=True)
        self.secrets = sorted({s for s in secrets if isinstance(s, str) and len(s) >= 4}, key=len, reverse=True)
        self.hotes = sorted({h.lower() for h in hotes if isinstance(h, str) and len(h) >= 4
                             and h.lower() not in ("localhost",) and not _RE_IPV4.fullmatch(h)}, key=len, reverse=True)
        self._re_util = (re.compile(r"(?i)(?<![\w])(" + "|".join(re.escape(u) for u in self.utilisateurs) + r")(?![\w])")
                         if self.utilisateurs else None)

    def texte(self, t: str) -> str:
        if not t:
            return t
        for s in self.secrets:
            t = t.replace(s, SECRET)
        t = _RE_CLE_JSON.sub(lambda m: m.group(1) + f'"{MASQUE}"', t)
        t = _RE_CLE_EGAL.sub(lambda m: m.group(1) + m.group(2) + MASQUE, t)
        t = _RE_EMAIL.sub(EMAIL, t)
        t = _RE_MAISON_WIN.sub(lambda m: m.group(1) + UTILISATEUR, t)
        t = _RE_MAISON_NIX.sub(lambda m: m.group(1) + UTILISATEUR, t)
        if self._re_util is not None:
            t = self._re_util.sub(UTILISATEUR, t)
        for h in self.hotes:
            t = re.sub(re.escape(h), NOM_HOTE, t, flags=re.I)
        t = _RE_IPV4.sub(lambda m: masquer_ip(m.group(0)), t)
        t = _RE_IPV6.sub(lambda m: _masquer_ipv6(m.group(0)), t)
        return t

    def json(self, obj, cle: str = ""):
        """Copie expurgée d'un document JSON : valeurs des clés secrètes masquées, textes expurgés."""
        if cle and CLES_SECRETES.search(cle):
            return MASQUE if obj not in (None, "", {}, []) else obj
        if isinstance(obj, dict):
            return {self.texte(str(k)): self.json(v, str(k)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.json(v, cle) for v in obj]
        if isinstance(obj, str):
            if cle and CLES_ADRESSES.match(cle) and obj and not _RE_IPV4.search(obj) \
                    and not obj.lower().startswith(("localhost", "127.", "0.0.0.0")) and not os.path.isabs(obj):
                return NOM_HOTE + (obj[obj.rfind(":"):] if re.search(r":\d+$", obj) else "")
            return self.texte(obj)
        return obj


def secrets_de(obj, cle: str = "", out: set | None = None) -> set:
    """Toutes les valeurs (textes) rangées sous une clé secrète d'un JSON : pour les retirer aussi des journaux."""
    out = set() if out is None else out
    secret = bool(cle) and bool(CLES_SECRETES.search(cle)) and not re.fullmatch(r"(?i)utilisateur", cle)
    if isinstance(obj, dict):
        for k, v in obj.items():
            if secret and isinstance(v, str):
                out.add(v)
            secrets_de(v, str(k) if not secret else cle, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            secrets_de(v, cle, out)
    elif secret and isinstance(obj, str):
        out.add(obj)
    return out


def utilisateurs_du_pc(env=None) -> set[str]:
    """Noms de compte du PC (variables d'environnement, dossier personnel)."""
    e = os.environ if env is None else env
    out = set()
    for k in ("USERNAME", "USER", "LOGNAME"):
        if e.get(k):
            out.add(e[k])
    for k in ("USERPROFILE", "HOME"):
        if e.get(k):
            out.add(os.path.basename(e[k].rstrip("\\/")))
    if env is None:
        try:
            import getpass
            out.add(getpass.getuser())
        except Exception:                       # noqa: BLE001
            pass
    return {u for u in out if u}


# ---------------------------------------------------------------------------
# Journaux
# ---------------------------------------------------------------------------
def _encodage(debut: bytes) -> tuple[str, int]:
    """(encodage, taille du BOM) d'après le début du fichier."""
    if debut[:2] == b"\xff\xfe":
        return "utf-16-le", 2
    if debut[:2] == b"\xfe\xff":
        return "utf-16-be", 2
    if debut[:3] == b"\xef\xbb\xbf":
        return "utf-8", 3
    ech = debut[:4096]
    if len(ech) >= 8 and ech.count(b"\x00") >= len(ech) // 4:
        pairs, impairs = ech[0::2].count(b"\x00"), ech[1::2].count(b"\x00")
        return ("utf-16-le", 0) if impairs > pairs else ("utf-16-be", 0)
    return "utf-8", 0


def lire_journal(chemin: str, limite: int = LIMITE_JOURNAL) -> tuple[str, dict]:
    """(texte UTF-8 de la fin du journal, infos : taille, coupe, encodage). Texte vide si illisible."""
    info = {"taille": 0, "coupe": False, "encodage": "", "erreur": ""}
    try:
        with open(chemin, "rb") as f:
            f.seek(0, 2)
            taille = f.tell()
            f.seek(0)
            enc, bom = _encodage(f.read(4096))
            debut = max(bom, taille - limite)
            if enc.startswith("utf-16") and (debut - bom) % 2:
                debut += 1
            f.seek(debut)
            brut = f.read()
    except OSError as e:
        info["erreur"] = e.__class__.__name__
        return "", info
    info.update(taille=taille, coupe=debut > bom, encodage=enc)
    texte = brut.decode(enc, "replace").replace("\r\n", "\n").replace("\x00", "")
    if info["coupe"]:
        i = texte.find("\n")
        texte = texte[i + 1:] if 0 <= i < 4096 else texte
        texte = f"[… début coupé : {debut} octets plus anciens non recopiés …]\n" + texte
    return texte, info


_RE_ERREUR = re.compile(
    r"(Traceback|Exception\b|\w+Error\b|\bERREUR\b|\b[Ee]rreur\b|\bERROR\b|\b[Ee]rror\b|\bFATAL\b|\b[Ff]atal\b|ÉCHEC|"
    r"\b[ée]chec\b|\b[Cc]rash|[Pp]lantage|Not loaded|\[code mods\].*(?:refus|échou|[ée]chec|fail|error|erreur|"
    r"impossible|invalid|not |manqu))")
_RE_HEURE = re.compile(r"^\s*(?:\d{4}-\d\d-\d\d[ T])?\d\d:\d\d:\d\d(?:[.,]\d+)?\s*")


def erreurs(texte: str, maximum: int = ERREURS_MAX) -> tuple[int, list[str]]:
    """(nombre de lignes d'erreur, dernières lignes distinctes, la plus récente en dernier)."""
    vues: dict[str, str] = {}
    n = 0
    for ligne in texte.splitlines():
        if _RE_ERREUR.search(ligne):
            n += 1
            cle = _RE_HEURE.sub("", ligne).strip()[:300]
            vues.pop(cle, None)
            vues[cle] = ligne.strip()[:400]
    return n, list(vues.values())[-maximum:]


_RE_ENTETE = re.compile(r"^===== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ", re.M)


def debut_session(texte: str) -> float | None:
    """Heure (epoch) du dernier lancement noté dans un journal de processus du lanceur (« ===== date argv »)."""
    lst = _RE_ENTETE.findall(texte or "")
    if not lst:
        return None
    try:
        return time.mktime(time.strptime(lst[-1], "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def debut_session_fichier(chemin: str) -> float | None:
    """Comme ``debut_session``, en lisant tout le fichier ligne à ligne (l'en-tête peut être avant la fin recopiée)."""
    dernier = None
    try:
        with open(chemin, "rb") as f:
            for ligne in f:
                if ligne.startswith(b"===== "):
                    dernier = ligne
    except OSError:
        return None
    return debut_session(dernier.decode("utf-8", "replace")) if dernier else None


def duree_texte(s: float | None) -> str:
    if s is None or s < 0:
        return "inconnue"
    s = int(s)
    h, m = divmod(s // 60, 60)
    return (f"{h} h {m:02d} min" if h else f"{m} min {s % 60:02d} s")


def lire_json(chemin: str):
    try:
        if os.path.getsize(chemin) > LIMITE_JSON:
            return None
        with open(chemin, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def taille_texte(n: int) -> str:
    return f"{n / (1 << 20):.1f} Mio" if n >= 1 << 20 else f"{n / 1024:.0f} Kio" if n >= 1024 else f"{n} o"


# ---------------------------------------------------------------------------
# Le zip
# ---------------------------------------------------------------------------
def nom_permis(nom: str) -> bool:
    """Seuls des textes (``.txt``, ``.log``, ``.json``), jamais une sauvegarde, un fichier du jeu ou une clé."""
    n = nom.replace("\\", "/")
    if n.startswith("/") or ".." in n.split("/") or not n.lower().endswith(EXTENSIONS):
        return False
    return not NOMS_INTERDITS.search(n)


class Archive:
    """Zip du rapport : écrit dans ``chemin + '.partiel'``, renommé à ``fermer``. ``ajouter`` refuse un nom interdit et
    coupe ce qui dépasse ``limite`` (contenu total avant compression)."""

    def __init__(self, chemin: str, limite: int = LIMITE_TOTALE):
        self.chemin, self.limite = chemin, limite
        self.partiel = chemin + ".partiel"
        os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
        self.z = zipfile.ZipFile(self.partiel, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
        self.total = 0
        self.contenu: list[tuple[str, int, str]] = []      # (nom, octets, note)
        self.refuses: list[str] = []

    def ajouter(self, nom: str, texte: str | bytes, note: str = "") -> bool:
        if not nom_permis(nom):
            self.refuses.append(nom)
            return False
        data = texte.encode("utf-8") if isinstance(texte, str) else bytes(texte)
        reste = self.limite - self.total
        if reste <= 0:
            self.refuses.append(nom)
            return False
        if len(data) > reste:
            data = data[-reste:]
            note = (note + " ; " if note else "") + "coupé (taille totale du rapport atteinte)"
        self.z.writestr(nom, data)
        self.total += len(data)
        self.contenu.append((nom, len(data), note))
        return True

    def ajouter_json(self, nom: str, obj, note: str = "") -> bool:
        return self.ajouter(nom, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", note)

    def fermer(self) -> str:
        self.z.close()
        os.replace(self.partiel, self.chemin)
        return self.chemin

    def abandonner(self) -> None:
        try:
            self.z.close()
        except Exception:                       # noqa: BLE001
            pass
        try:
            os.remove(self.partiel)
        except OSError:
            pass


def ecrire_zip(chemin: str, entrees: list[tuple[str, str | bytes, str]], limite: int = LIMITE_TOTALE) -> dict:
    """Écrit les ``entrees`` (nom, contenu, note) dans le zip ; la première est le résumé (rapport.txt). Renvoie
    {chemin, taille, contenu, refuses}."""
    a = Archive(chemin, limite)
    try:
        for nom, contenu, note in entrees:
            a.ajouter(nom, contenu, note)
        a.fermer()
    except BaseException:
        a.abandonner()
        raise
    return {"chemin": chemin, "taille": os.path.getsize(chemin), "contenu": a.contenu, "refuses": a.refuses}


def nom_zip(prefixe: str, t: float | None = None) -> str:
    return f"{prefixe}_{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}.zip"


def garder_derniers(dossier: str, prefixe: str, n: int) -> None:
    """Efface les plus anciens rapports ``prefixe_*.zip`` de ``dossier`` au-delà de ``n`` (serveur : volume borné)."""
    try:
        lst = sorted(x for x in os.listdir(dossier) if x.startswith(prefixe + "_") and x.endswith(".zip"))
    except OSError:
        return
    for x in lst[:-n] if n > 0 else lst:
        try:
            os.remove(os.path.join(dossier, x))
        except OSError:
            pass
