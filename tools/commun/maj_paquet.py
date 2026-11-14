"""Paquets de mise à jour du mod : format, liste blanche, signature, vérification (docs/joueurs/MISES_A_JOUR.md).

Un paquet, c'est deux fichiers dans un dossier (ou derrière une URL, ou servis par wwmp_server.py) :

  dernier.json        {"format": 1, "manifeste": "<texte JSON>", "signature": "<128 hex>"}
  wwmp-<version>.zip  les fichiers du mod (outils, lanceur, pack graphique, docs utiles)

Le manifeste (texte JSON, signé TEL QUEL : jamais re-sérialisé avant vérification) :

  {"format": 1, "produit": "wwmp", "version": CLIENT_VERSION, "date": secondes depuis 1970,
   "fichier": "wwmp-<version>.zip", "taille": octets du zip, "sha256": empreinte du zip,
   "fichiers": {"tools/wwmp_client.py": ["<sha256>", taille], …}, "notes": "…",
   "pack_mailbox": 19, "python_min": "3.10"}

Signature : Ed25519 (commun/signature.py) de ``DOMAINE + manifeste`` avec la clé privée du
propriétaire ; la clé publique est dans tools/commun/cle_publique.txt (commitée). Le lanceur vérifie
avec la clé publique de la version DÉJÀ installée (jamais celle du paquet reçu), puis l'empreinte du
zip, puis chaque fichier (nom dans la liste blanche, taille et sha256 du manifeste, aucun fichier en
trop ni en moins). Un seul écart : refus, rien n'est écrit.

Jamais dans un paquet : le jeu, une ROM, des clés de console, des sauvegardes, des journaux, des
réglages personnels (liste blanche ci-dessous, extensions comprises).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import zipfile

from commun import signature

FORMAT = 1
PRODUIT = "wwmp"
DOMAINE = b"WWMP-MAJ-1\x00"          # séparation de domaine : la clé ne signe rien d'autre
ZIP_MAX = 64 << 20                   # octets : taille max du zip
TOTAL_MAX = 256 << 20                # octets : taille max une fois décompressé
FICHIER_MAX = 32 << 20               # octets : taille max d'un fichier
FICHIERS_MAX = 4000
NOTES_MAX = 2000
NOM_ZIP_RE = re.compile(r"^wwmp-[A-Za-z0-9._-]{1,64}\.zip$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMPOSANT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,99}$")
NOM_FICHIER_CLE = "cle_publique.txt"

# --- liste blanche -----------------------------------------------------------------------------
EXTENSIONS = {".py", ".bat", ".cmd", ".js", ".html", ".css", ".md", ".txt", ".json", ".asm", ".ttf", ".otf",
              ".woff", ".woff2", ".png", ".svg", ".ico", ".sh", ".service", ".yml", ".yaml", ".exemple"}
NOMS_SANS_EXTENSION = {"Dockerfile"}
FICHIERS_RACINE = {"README.md", "requirements.txt"}
# réglages personnels, secrets, états : jamais (même avec une extension permise)
NOMS_INTERDITS = {"wwhd_config.json", "lancer_client.txt", "compagnon.json", "mot_de_passe.txt", "cles.json",
                  ".env", "cle_privee.txt", "versions.json", "manifeste_wwmp.json", "dernier.json"}


class ErreurMaj(Exception):
    """Paquet refusé (signature, empreinte, liste blanche, format) ou source injoignable."""


def chemin_autorise(rel) -> bool:
    """Le chemin relatif ``rel`` (séparateur « / ») peut-il faire partie d'un paquet ?"""
    if not isinstance(rel, str) or not rel or len(rel) > 200 or "\\" in rel or ":" in rel or rel.startswith("/"):
        return False
    parts = rel.split("/")
    if any(not COMPOSANT_RE.match(c) or c.endswith((" ", ".")) for c in parts):
        return False
    nom = parts[-1]
    if nom in NOMS_INTERDITS or "__pycache__" in parts:
        return False
    if "icones_jeu" in parts:                           # images extraites du jeu sur chaque PC : jamais distribuées
        return False
    ext = os.path.splitext(nom)[1].lower()
    if ext not in EXTENSIONS and nom not in NOMS_SANS_EXTENSION:
        return False
    if len(parts) == 1:
        return nom in FICHIERS_RACINE
    haut = parts[0]
    if haut == "tools":
        return parts[1] != "tests"                      # les tests ne servent pas aux joueurs
    if haut == "graphicpack":
        return len(parts) >= 3 and parts[1].startswith("WWMP_")
    if haut == "docs":
        return len(parts) == 2 and ext == ".md"          # pas docs/etudes/
    if haut == "serveur_dedie":
        return True
    return False


def sha256(octets: bytes) -> str:
    return hashlib.sha256(octets).hexdigest()


def sha256_fichier(chemin: str) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


# --- clés --------------------------------------------------------------------------------------
def lire_cle_publique(chemin: str) -> bytes | None:
    """Clé publique (32 octets) du fichier cle_publique.txt (première ligne de 64 hex hors « # »)."""
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip().lower()
                if ligne and not ligne.startswith("#") and HEX64.match(ligne):
                    return bytes.fromhex(ligne)
    except (OSError, UnicodeDecodeError):
        return None
    return None


def chemin_cle_publique(tools: str) -> str:
    return os.path.join(tools, "commun", NOM_FICHIER_CLE)


def empreinte_cle(pub: bytes) -> str:
    """Résumé lisible d'une clé publique (16 hex), pour comparer à l'œil."""
    return sha256(pub)[:16]


# --- manifeste et signature ----------------------------------------------------------------------
def message_signe(manifeste_texte: str) -> bytes:
    return DOMAINE + manifeste_texte.encode("utf-8")


def _int(x, lo: int, hi: int) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and lo <= x <= hi


def valider_manifeste(m) -> dict:
    """Contrôle de forme du manifeste (après la signature). Lève ErreurMaj."""
    if not isinstance(m, dict) or m.get("format") != FORMAT or m.get("produit") != PRODUIT:
        raise ErreurMaj("manifeste : format inconnu")
    if not isinstance(m.get("version"), str) or not VERSION_RE.match(m["version"]):
        raise ErreurMaj("manifeste : version invalide")
    if not _int(m.get("date"), 0, 2 ** 40):
        raise ErreurMaj("manifeste : date invalide")
    if not isinstance(m.get("fichier"), str) or not NOM_ZIP_RE.match(m["fichier"]):
        raise ErreurMaj("manifeste : nom de fichier invalide")
    if not _int(m.get("taille"), 1, ZIP_MAX) or not isinstance(m.get("sha256"), str) or not HEX64.match(m["sha256"]):
        raise ErreurMaj("manifeste : taille ou empreinte du paquet invalide")
    f = m.get("fichiers")
    if not isinstance(f, dict) or not 0 < len(f) <= FICHIERS_MAX:
        raise ErreurMaj("manifeste : liste de fichiers invalide")
    total = 0
    for rel, v in f.items():
        if not chemin_autorise(rel):
            raise ErreurMaj(f"manifeste : fichier interdit {rel[:80]!r}")
        if not (isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and HEX64.match(v[0])
                and _int(v[1], 0, FICHIER_MAX)):
            raise ErreurMaj(f"manifeste : empreinte invalide pour {rel}")
        total += v[1]
    if total > TOTAL_MAX:
        raise ErreurMaj("manifeste : paquet trop gros une fois décompressé")
    minus = {}
    for rel in f:
        k = rel.lower()
        if k in minus:
            raise ErreurMaj(f"manifeste : deux fichiers ne diffèrent que par la casse ({rel})")
        minus[k] = rel
    if not isinstance(m.get("notes", ""), str):
        raise ErreurMaj("manifeste : notes invalides")
    return m


def verifier_dernier(dernier, cle_pub: bytes | None) -> dict:
    """``dernier`` (dict lu de dernier.json) -> manifeste vérifié (signature puis forme). Lève ErreurMaj."""
    if cle_pub is None:
        raise ErreurMaj("mises à jour non configurées : pas de clé publique (tools/commun/cle_publique.txt)")
    if not isinstance(dernier, dict) or dernier.get("format") != FORMAT:
        raise ErreurMaj("dernier.json : format inconnu")
    texte, sig = dernier.get("manifeste"), dernier.get("signature")
    if not isinstance(texte, str) or len(texte) > 4 << 20 or not isinstance(sig, str) \
            or not re.match(r"^[0-9a-f]{128}$", sig):
        raise ErreurMaj("dernier.json : manifeste ou signature absent")
    if not signature.verifier(cle_pub, message_signe(texte), bytes.fromhex(sig)):
        raise ErreurMaj("SIGNATURE INVALIDE : ce paquet n'a pas été signé par le propriétaire du mod, "
                        "mise à jour refusée")
    try:
        m = json.loads(texte)
    except ValueError:
        raise ErreurMaj("manifeste illisible") from None
    return valider_manifeste(m)


def lire_dernier(octets: bytes) -> dict:
    if len(octets) > 8 << 20:
        raise ErreurMaj("dernier.json trop gros")
    try:
        d = json.loads(octets.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ErreurMaj("dernier.json illisible") from None
    if not isinstance(d, dict):
        raise ErreurMaj("dernier.json illisible")
    return d


def identifiant(manifeste: dict) -> str:
    """Identifiant unique d'un paquet : empreinte de son zip."""
    return manifeste["sha256"]


def nom_dossier(manifeste: dict) -> str:
    """Nom du dossier de version installée : <version>-<8 hex>."""
    return f"{manifeste['version']}-{manifeste['sha256'][:8]}"


# --- zip -----------------------------------------------------------------------------------------
def verifier_zip(zip_octets: bytes, manifeste: dict) -> dict[str, bytes]:
    """Vérifie le zip contre le manifeste (déjà signé). Renvoie {chemin: contenu}. Lève ErreurMaj."""
    if len(zip_octets) != manifeste["taille"] or sha256(zip_octets) != manifeste["sha256"]:
        raise ErreurMaj("le paquet téléchargé ne correspond pas à son manifeste (empreinte) : refusé")
    attendus = manifeste["fichiers"]
    try:
        z = zipfile.ZipFile(io.BytesIO(zip_octets))
    except (zipfile.BadZipFile, ValueError):
        raise ErreurMaj("paquet zip illisible") from None
    contenu: dict[str, bytes] = {}
    with z:
        for info in z.infolist():
            nom = info.filename
            if nom.endswith("/"):
                continue
            if nom not in attendus or nom in contenu:
                raise ErreurMaj(f"fichier inattendu dans le paquet : {nom[:80]!r}")
            sha, taille = attendus[nom]
            if info.file_size != taille or info.flag_bits & 0x1:
                raise ErreurMaj(f"taille incorrecte : {nom}")
            with z.open(info) as f:
                data = f.read(taille + 1)
            if len(data) != taille or sha256(data) != sha:
                raise ErreurMaj(f"empreinte incorrecte : {nom}")
            contenu[nom] = data
    manque = set(attendus) - set(contenu)
    if manque:
        raise ErreurMaj(f"fichier manquant dans le paquet : {sorted(manque)[0]}")
    return contenu


def extraire(contenu: dict[str, bytes], manifeste: dict, dest: str) -> None:
    """Écrit les fichiers vérifiés dans ``dest`` (dossier neuf), puis relit chacun et recontrôle."""
    racine = os.path.abspath(dest)
    for rel, data in contenu.items():
        chemin = os.path.abspath(os.path.join(racine, *rel.split("/")))
        if os.path.commonpath([racine, chemin]) != racine:
            raise ErreurMaj(f"chemin hors du dossier : {rel}")
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "wb") as f:
            f.write(data)
    for rel, (sha, _t) in manifeste["fichiers"].items():
        if sha256_fichier(os.path.join(racine, *rel.split("/"))) != sha:
            raise ErreurMaj(f"vérification après écriture ratée : {rel}")


def verifier_dossier(dossier: str, manifeste: dict) -> bool:
    """Le dossier contient-il exactement les fichiers du manifeste (empreintes) ?"""
    try:
        for rel, (sha, _t) in manifeste["fichiers"].items():
            if sha256_fichier(os.path.join(dossier, *rel.split("/"))) != sha:
                return False
    except OSError:
        return False
    return True


# --- fabrication (wwmp_publier.py) ---------------------------------------------------------------
def fabriquer(racine: str, fichiers: list[str], version: str, graine: bytes, notes: str = "",
              date: int | None = None, pack_mailbox: int | None = None) -> tuple[bytes, dict, dict]:
    """Zip reproductible (ordre, dates fixes) + manifeste signé. Renvoie (zip, dernier, manifeste)."""
    if not VERSION_RE.match(version):
        raise ErreurMaj(f"version invalide pour un paquet : {version!r}")
    fichiers = sorted(set(fichiers))
    refus = [f for f in fichiers if not chemin_autorise(f)]
    if refus:
        raise ErreurMaj(f"fichier hors liste blanche : {refus[0]}")
    tampon = io.BytesIO()
    inventaire = {}
    secrets_cle = (graine, graine.hex().encode("ascii"))
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in fichiers:
            with open(os.path.join(racine, *rel.split("/")), "rb") as f:
                data = f.read()
            if len(data) > FICHIER_MAX:
                raise ErreurMaj(f"fichier trop gros : {rel}")
            if any(x in data or x in data.lower() for x in secrets_cle):
                raise ErreurMaj(f"la clé PRIVÉE de signature se trouve dans {rel} : paquet refusé (retire-la "
                                "de ce fichier, garde-la seulement dans %APPDATA%\\WWMP_signature)")
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
            inventaire[rel] = [sha256(data), len(data)]
    zip_octets = tampon.getvalue()
    m = {"format": FORMAT, "produit": PRODUIT, "version": version,
         "date": int(time.time() if date is None else date),
         "fichier": f"wwmp-{version}.zip", "taille": len(zip_octets), "sha256": sha256(zip_octets),
         "fichiers": inventaire, "notes": str(notes or "")[:NOTES_MAX],
         "pack_mailbox": pack_mailbox, "python_min": "3.10"}
    valider_manifeste(m)
    texte = json.dumps(m, ensure_ascii=False, sort_keys=True, indent=1)
    sig = signature.signer(graine, message_signe(texte))
    dernier = {"format": FORMAT, "manifeste": texte, "signature": sig.hex()}
    return zip_octets, dernier, m


def ecrire_atomique(chemin: str, octets: bytes) -> None:
    tmp = f"{chemin}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(octets)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, chemin)


def ecrire_paquet(dossier: str, zip_octets: bytes, dernier: dict, manifeste: dict, garder: int = 2) -> str:
    """Écrit le paquet dans ``dossier`` (zip d'abord, dernier.json ensuite : un lecteur ne voit jamais
    un dernier.json qui pointe vers un zip absent). Garde les ``garder`` zips les plus récents."""
    os.makedirs(dossier, exist_ok=True)
    ecrire_atomique(os.path.join(dossier, manifeste["fichier"]), zip_octets)
    ecrire_atomique(os.path.join(dossier, "dernier.json"),
                    json.dumps(dernier, ensure_ascii=False, indent=1).encode("utf-8"))
    zips = sorted((n for n in os.listdir(dossier) if NOM_ZIP_RE.match(n) and n != manifeste["fichier"]),
                  key=lambda n: os.path.getmtime(os.path.join(dossier, n)), reverse=True)
    for n in zips[max(0, garder - 1):]:
        try:
            os.remove(os.path.join(dossier, n))
        except OSError:
            pass
    return os.path.join(dossier, "dernier.json")


def date_texte(date: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(date)))
    except (OverflowError, ValueError, OSError):
        return "?"
