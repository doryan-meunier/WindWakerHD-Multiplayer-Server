"""Monde persistant (chantier D, docs/etudes/monde_persistant.md) : le serveur garde le monde sur disque.

Le jeu reste du solo sur chaque PC. « Le monde » est ce que le serveur sait fusionner (union de la progression,
petites clés, coffres ouverts, quarts de cœur, inventaire commun, rubis / flèches / bombes, vent, deathlink, vie
partagée et sa valeur commune),
plus une IMAGE DE SAUVEGARDE de référence (0x7A0 octets, le format de « Copier la partie de l'hôte », A15) pour ce
qu'aucune synchro ne couvre. Rien ici ne lit ni n'écrit la mémoire du jeu : le serveur ne fait que garder et
renvoyer ce que les clients lui disent déjà.

- Fichier ``<dossier des mondes>/<id>/monde.json`` (JSON versionné, une section par synchro), écrit de façon
  atomique (``.tmp`` + fsync + ``os.replace``), la version précédente gardée en ``monde.json.1``, des copies
  horaires (24) et quotidiennes (14) dans ``sauvegardes/``. Chargement : ``monde.json``, puis ``.1``, puis la
  copie la plus récente ; un fichier illisible n'est JAMAIS écrasé (le serveur refuse de démarrer, message
  clair) ; un fichier d'une version plus récente est refusé. Chaque entrée est validée par le validateur du
  message correspondant : une entrée abîmée est ignorée (journal) sans faire tomber le reste.
- Écriture : drapeau « sale », au plus toutes les ``ECRITURE_MIN`` s (``ECRITURE_RAPIDE`` s après un gain
  important), et à l'arrêt propre (Ctrl+C, CTRL_BREAK, fichier d'arrêt, SIGTERM).
- Image du monde : demandée (``image_req``) au client de jeu le plus avancé qui annonce ``image_monde``, toutes
  les ``IMAGE_PERIODE`` s et ``IMAGE_APRES_GAIN`` s après un gain important ; il répond ``image_monde``
  (lecture seule de sa sauvegarde, comme A15). Une image moins avancée (note plus basse) est refusée : le monde
  ne recule pas ; deux images ne sont jamais fusionnées.
- « Copier la partie » (``rejoindre_req``) est servi PAR LE SERVEUR avec l'image du monde, sans rien remettre
  à zéro ensuite (sinon le monde serait effacé).
- Protection : le ``hello`` porte l'identifiant du monde (lanceur, ``--monde ID`` du client) ; un autre
  identifiant est refusé. Garde de contenu (quarantaine) : la première progression d'un client récent est
  comparée au monde ; une partie qui n'a presque rien de commun avec lui (partie solo chargée par erreur) est
  mise en quarantaine pour cette connexion : elle reçoit le monde, mais rien d'elle n'y entre.
- Pas gardé (voulu) : PV des boss, herbe, switches temporaires, objets de salle, autorité, propriétaires
  d'horloge, météo. Exception : section « salles » (canons détruits et switches de mort des Bokoblins /
  canonnières par salle, synchro/canons.py §3), gardée tant qu'un joueur a encore la salle ; relue au démarrage,
  elle est oubliée si personne ne revient dans la salle en ``SallesDetruites.GRACE`` s.
- Mode Hanteurs (``--mode-hanteurs`` avec un monde : permis) : une partie Hanteurs est une SESSION temporaire
  au-dessus du monde. Gardé : ses RÉGLAGES seulement (``regles.hanteurs``, repris au lancement suivant s'il n'y a
  pas de ``--hanteurs-config``). Jamais gardé : la manche (rôles, points, possessions, potion, ordres, JcJ), qui
  repart de zéro avec le processus. Ce que les Link gagnent entre dans le monde comme en coop normale ; rien d'un
  Hanteur n'y entre (filtres du mode, avant ``entree``) ; son verdict de garde de contenu est rendu sur un
  ``progress`` marqué ``jug`` (jamais fusionné) ; l'image du monde n'est jamais demandée à un Hanteur, à un Link
  possédé (potion) ni à un ancien Hanteur (``image_anciens``, désactivé par défaut, pour ces derniers).
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import time
from collections import OrderedDict

from jeu import savecopy as sc
from jeu.progress import bits_count, bits_sub, from_wire, to_wire

from synchro.cadeaux import valid_record
from synchro.cles import KeyPool
from synchro.compteurs import EV_RE, KS, VAL_MAX, CompteurPool, valid_caps
from commun import difficulte as _dif
from synchro.monde import valid_vent
from commun.protocole import CLIENT_VERSION, REJOINDRE_MAX, REJOINDRE_PART, VERSION, valid_coffre, valid_inv

FORMAT = "wwmp-monde"
VERSION_FORMAT = 1
FICHIER = "monde.json"
SAUVEGARDES = "sauvegardes"
ID_RE = re.compile(r"^[0-9a-f]{12}$")
NOM_MAX = 32
JEU = "WWHD USA v0"

ECRITURE_MIN = 30.0          # s entre deux écritures (drapeau « sale »)
ECRITURE_RAPIDE = 5.0        # s après un gain important (boss, grande clé, objet, nouvelle image)
IMAGE_PERIODE = 600.0        # s entre deux demandes d'image (10 min de jeu)
IMAGE_APRES_GAIN = 30.0      # s après un gain important
IMAGE_ATTENTE = 60.0         # s : demande sans réponse abandonnée
IMAGE_REESSAI = 120.0        # s avant de redemander après un échec
IMAGE_CONNECTE_MIN = 20.0    # s de connexion avant qu'on demande son image à un joueur (partie chargée)
COPIES_HEURE = 24
COPIES_JOUR = 14
JOUEURS_MAX = 256
HISTO_MAX = 32               # quarantaines gardées pour l'affichage
LIBERES_MAX = 64
SUSPECTS_MAX = 64            # joueurs déjà mis en quarantaine (gardés jusqu'à une partie qui ressemble au monde)
SEUIL_RETARD = 64            # bits : « Mettre ma partie au niveau du monde » proposé au-delà [HYPO à régler]
QUAR_SEUIL = 128             # bits nouveaux pour le monde ET bits du monde absents chez lui [HYPO à régler]
QUAR_MONDE_MIN = 256         # un monde plus jeune ne met personne en quarantaine
VERDICT_ATTENTE = 20.0       # s : sans progression jugeable, le joueur est accepté
TAMPON_MAX = 256             # messages gardés pendant l'attente du verdict
GROUPES_MAX = 16

# messages qui changent le monde (gardés pendant l'attente du verdict, filtrés en quarantaine)
TYPES_TAMPON = frozenset({"progress", "keys", "key_get", "key_use", "coffre", "gift", "inv", "cnt", "trk", "vie",
                          "rando_plus"})


class MondeIllisible(Exception):
    """Aucune copie lisible du monde : le serveur refuse de démarrer (rien n'est écrasé)."""


class MondeTropRecent(Exception):
    """Fichier écrit par un serveur plus récent : refusé, jamais écrasé."""


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def maintenant_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def nouvel_id() -> str:
    return secrets.token_hex(6)


def nettoyer_nom(nom) -> str:
    return " ".join(str(nom or "").split())[:NOM_MAX]


def _texte(x, n: int = 64) -> str:
    return str(x)[:n] if isinstance(x, (str, int, float)) and not isinstance(x, bool) else ""


# ---------------------------------------------------------------------------------------------------------
# Image : note (comparer, jamais fusionner) et résumé lisible
# ---------------------------------------------------------------------------------------------------------
def _pop(data: bytes) -> int:
    return sum(bin(b).count("1") for b in data)


def image_corrigee(img: bytes) -> bytes:
    """Image corrigée comme putSave (mMemory de la stage chargée recopiée) ; l'image brute si impossible."""
    try:
        return sc.image_hote(img)
    except ValueError:
        return bytes(img)


def note_image(img: bytes) -> int:
    """Bits posés dans mEvent, mMemory (sans le compteur de clés), collect et map, plus les objets
    d'emplacement. Sert à COMPARER deux images (le monde ne recule pas), jamais à les fusionner."""
    g = sc.gi_depuis_image(image_corrigee(img))
    n = _pop(g[sc.GI_EVENT:sc.GI_EVENT + 0x100])
    for i in range(sc.N_MEM):
        base = sc.GI_MEMORY + i * sc.MEM_SIZE
        mem = g[base:base + sc.MEM_SIZE]
        n += _pop(mem[:sc.MEM_KEYNUM]) + _pop(mem[sc.MEM_KEYNUM + 1:])
    n += _pop(g[sc.GI_COLLECT:sc.GI_COLLECT + 0xD]) + _pop(g[sc.GI_MAP:sc.GI_MAP + 0x84])
    n += sum(1 for b in g[sc.GI_ITEM:sc.GI_ITEM + sc.ITEM_SIZE] if b != 0xFF)
    return n


def resume_image(img: bytes) -> dict:
    """Résumé JSON (lanceur, onglet « Mon monde ») : cœurs, objets, chants, perles, Triforce, donjons."""
    try:
        r = sc.resume(image_corrigee(img))
    except Exception:                                   # image validée avant : ne devrait pas arriver
        return {}
    return {"coeurs": r["coeurs"], "objets": len(r["objets"]), "chants": len(r["chants"]),
            "perles": len(r["perles"]), "triforce": r["triforce"], "cartes": r["cartes"], "iles": r["iles"],
            "donjons": dict(r["donjons"])}


def valider_image_brute(img: bytes) -> str:
    """« » si l'image est acceptable, sinon la raison."""
    if len(img) != sc.IMG_LEN:
        return f"taille 0x{len(img):X} au lieu de 0x{sc.IMG_LEN:X}"
    err, _w = sc.valider_image(img)
    return "; ".join(err)


def gain_important(new: dict) -> bool:
    """Bits nouveaux de l'union qui valent une écriture et une image rapides : plus de 16 d'un coup, ou grande
    clé / boss vaincu d'un donjon (mMemory[n]+0x21, bits 0x04 et 0x08)."""
    if bits_count(new) > 16:
        return True
    for k, offs in new.items():
        if k.startswith("mem") and offs.get(sc.MEM_DUNGEON, 0) & 0x0C:
            return True
    return False


# ---------------------------------------------------------------------------------------------------------
# Fichiers : emplacement, lecture avec replis, écriture atomique, copies de secours
# ---------------------------------------------------------------------------------------------------------
def lire_fichier(chemin: str) -> dict:
    """Lit et contrôle l'en-tête. Lève OSError, ValueError (illisible), MondeTropRecent."""
    with open(chemin, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError("ce n'est pas un fichier de monde wwmp")
    v = doc.get("version")
    if not _int(v) or v < 1:
        raise ValueError(f"version du format illisible ({v!r})")
    if v > VERSION_FORMAT:
        raise MondeTropRecent(f"monde écrit par un serveur plus récent (format {v}, ce serveur lit le {VERSION_FORMAT}) : "
                              "mets le serveur à jour ; le fichier n'est pas touché")
    if not isinstance(doc.get("id"), str) or not ID_RE.match(doc["id"]):
        raise ValueError("identifiant du monde illisible")
    return doc


def _copies(dossier: str) -> list[str]:
    d = os.path.join(dossier, SAUVEGARDES)
    try:
        noms = [n for n in os.listdir(d) if n.endswith(".json") and n.startswith(("monde-", "jour-"))]
    except OSError:
        return []
    # la plus récente d'abord. Le nom porte la date : « monde-AAAAMMJJ-HH00 », « jour-AAAAMMJJ ». La copie du jour
    # est faite à la PREMIÈRE écriture du jour : elle passe après les copies horaires du même jour (relecture
    # adverse : comparer « 20261110-1400.json » à « 20261110.json » la mettait devant, c.-à-d. l'état le plus ancien)
    def cle(n: str) -> tuple[str, str]:
        parts = n[:-len(".json")].split("-")
        return (parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else "")
    return [os.path.join(d, n) for n in sorted(noms, key=cle, reverse=True)]


NOM_BRUT_RE = re.compile(r'"nom"\s*:\s*"((?:[^"\\]|\\.){0,200})"')


def _indices_illisible(dossier: str) -> tuple[str, str]:
    """(nom, modifié) d'un monde illisible, lus au mieux dans le texte brut de ses fichiers (un fichier tronqué
    garde son en-tête). Sert à ne JAMAIS créer un monde neuf du même nom à côté d'un monde abîmé."""
    principal = os.path.join(dossier, FICHIER)
    cands = [c for c in [principal, principal + ".1"] + _copies(dossier) if os.path.exists(c)]
    nom = ""
    for c in cands:
        try:
            with open(c, "r", encoding="utf-8", errors="replace") as f:
                m = NOM_BRUT_RE.search(f.read(4096))
            if m:
                nom = nettoyer_nom(json.loads('"' + m.group(1) + '"'))
                if nom:
                    break
        except (OSError, ValueError):
            continue
    try:
        t = max(os.path.getmtime(c) for c in cands) if cands else os.path.getmtime(dossier)
        modifie = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    except OSError:
        modifie = ""
    return nom, modifie


def charger(dossier: str) -> tuple[dict | None, str, list[str]]:
    """(document, fichier lu, replis). None : aucun fichier (monde neuf). Lève MondeIllisible si des fichiers
    existent mais qu'aucun n'est lisible, MondeTropRecent si le principal vient d'un serveur plus récent."""
    principal = os.path.join(dossier, FICHIER)
    cands = [principal, principal + ".1"] + _copies(dossier)
    existants = [c for c in cands if os.path.exists(c)]
    if not existants:
        return None, "", []
    replis = []
    for c in existants:
        try:
            return lire_fichier(c), c, replis
        except MondeTropRecent:
            raise
        except (OSError, ValueError) as e:
            replis.append(f"{os.path.basename(c)} illisible ({e.__class__.__name__}: {str(e)[:120]})")
    raise MondeIllisible("aucune copie lisible du monde dans " + dossier + " : " + " ; ".join(replis)
                         + ". Rien n'a été écrasé : répare ou remets une copie de « sauvegardes », ou choisis "
                         "un autre --monde")


def ecrire_atomique(chemin: str, doc: dict) -> None:
    """``chemin.tmp`` -> flush + fsync -> l'ancien devient ``chemin.1`` (copie) -> ``os.replace``."""
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(chemin):
        try:
            shutil.copyfile(chemin, chemin + ".1.tmp")
            os.replace(chemin + ".1.tmp", chemin + ".1")
        except OSError:
            pass                                       # la copie de secours ne bloque jamais l'écriture
    os.replace(tmp, chemin)


def copies_de_secours(dossier: str, doc: dict, quand: float | None = None) -> list[str]:
    """Une copie par heure (24 gardées) et une par jour (14 gardées) dans ``sauvegardes/``. Renvoie les
    fichiers créés."""
    quand = time.time() if quand is None else quand
    d = os.path.join(dossier, SAUVEGARDES)
    os.makedirs(d, exist_ok=True)
    lt = time.localtime(quand)
    faits = []
    for prefixe, nom, garder in (("monde", time.strftime("monde-%Y%m%d-%H00.json", lt), COPIES_HEURE),
                                 ("jour", time.strftime("jour-%Y%m%d.json", lt), COPIES_JOUR)):
        chemin = os.path.join(d, nom)
        if not os.path.exists(chemin):
            ecrire_atomique(chemin, doc)
            faits.append(chemin)
            try:
                os.remove(chemin + ".1")               # pas de « .1 » pour une copie
            except OSError:
                pass
        anciens = sorted(n for n in os.listdir(d) if n.startswith(prefixe + "-") and n.endswith(".json"))
        for n in anciens[:-garder]:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
    return faits


def lister(dossier_mondes: str) -> list[dict]:
    """Mondes d'un dossier (le plus récemment modifié d'abord) : id, nom, modifie, joueurs, note, dossier."""
    out = []
    try:
        noms = os.listdir(dossier_mondes)
    except OSError:
        return []
    for n in noms:
        d = os.path.join(dossier_mondes, n)
        if not ID_RE.match(n) or not os.path.isdir(d):
            continue
        try:
            doc, _src, _r = charger(d)
        except (MondeIllisible, MondeTropRecent) as e:
            nom, modifie = _indices_illisible(d)
            out.append({"id": n, "nom": nom or "?", "nom_lu": bool(nom), "erreur": str(e)[:200], "dossier": d,
                        "modifie": modifie})
            continue
        if doc is None:
            continue
        img = doc.get("image") if isinstance(doc.get("image"), dict) else {}
        out.append({"id": doc["id"], "nom": nettoyer_nom(doc.get("nom")) or doc["id"], "dossier": d,
                    "modifie": _texte(doc.get("modifie")), "cree": _texte(doc.get("cree")),
                    "joueurs": sorted(doc.get("joueurs", {}))[:JOUEURS_MAX] if isinstance(doc.get("joueurs"), dict) else [],
                    "note": img.get("note") if _int(img.get("note")) else None,
                    "resume": img.get("resume") if isinstance(img.get("resume"), dict) else None,
                    "alias": [nettoyer_nom(a) for a in doc.get("alias", [])[:16] if isinstance(a, str)]
                    if isinstance(doc.get("alias"), list) else []})
    out.sort(key=lambda m: m.get("modifie") or "", reverse=True)
    return out


def resoudre(dossier_mondes: str, nom_ou_id: str) -> tuple[str, str, str]:
    """(dossier du monde, id, nom) : un id existant, sinon un monde de ce nom (sans tenir compte de la casse),
    sinon un nouveau monde (id au hasard ; le dossier n'est créé qu'à la première écriture)."""
    voulu = nettoyer_nom(nom_ou_id) or "monde"
    if ID_RE.match(voulu) and os.path.isdir(os.path.join(dossier_mondes, voulu)):
        return os.path.join(dossier_mondes, voulu), voulu, voulu
    mondes = lister(dossier_mondes)
    for m in mondes:
        # un monde renommé par l'administrateur se retrouve aussi par ses anciens noms (--monde "Ancien nom") ; un
        # monde ILLISIBLE aussi (nom lu dans son texte brut) : son ouverture échoue alors, rien n'est créé à côté
        if voulu.lower() in [m.get("nom", "").lower()] + [a.lower() for a in m.get("alias") or []] or m["id"] == voulu:
            return m["dossier"], m["id"], m.get("nom") or voulu
    if ID_RE.match(voulu):
        return os.path.join(dossier_mondes, voulu), voulu, voulu   # id connu d'une partie liée : gardé
    inconnus = [m for m in mondes if m.get("erreur") and not m.get("nom_lu")]
    if inconnus:
        # relecture adverse : sinon « --monde Pirates » sur un monde Pirates abîmé créait EN SILENCE un monde neuf
        # (vide) du même nom, que la relance suivante reprenait : l'ancien monde était perdu de vue
        raise MondeIllisible(f"aucun monde lisible nommé « {voulu} » dans {dossier_mondes}, mais "
                             + ", ".join(m["id"] for m in inconnus) + " est illisible et son nom est inconnu (c'est "
                             "peut-être celui-ci). Rien n'a été créé ni écrasé : répare-le ou remets une copie de "
                             "« sauvegardes », déplace-le, ou donne --monde <son id>")
    i = nouvel_id()
    while os.path.exists(os.path.join(dossier_mondes, i)):
        i = nouvel_id()
    return os.path.join(dossier_mondes, i), i, voulu


# ---------------------------------------------------------------------------------------------------------
# Sections : export de l'état du serveur, restauration validée
# ---------------------------------------------------------------------------------------------------------
def exporter_groupes(srv) -> dict:
    groupes: dict[str, dict] = {}

    def g(nom: str) -> dict:
        return groupes.setdefault(nom, {})
    for nom, union in srv.progress.items():
        if union:
            g(nom)["progress"] = to_wire(union)
    for nom, d in srv.keys.groups.items():
        if d:
            g(nom)["cles"] = {str(s): e for s, e in d.items()}
    for nom, reg in srv.coffres.items():
        if reg:
            g(nom)["coffres"] = [dict(r) for r in reg.values()]
    for nom, reg in srv.gifts.items():
        if reg:
            g(nom)["cadeaux"] = [dict(r) for r in reg.values()]
    for nom, st in srv.inv.items():
        g(nom)["inv"] = {"acq": list(st["acq"]), "bits": [[o, m] for o, m in sorted(st["bits"].items())],
                         "cap": dict(st["cap"]), "fl": st["fl"], "sac": None if st["sac"] is None else list(st["sac"])}
    for nom, st in srv.cnt.groups.items():
        if st["tot"]:
            g(nom)["compteurs"] = {"tot": dict(st["tot"]), "cap": dict(st["cap"]), "evs": list(st["evs"])}
    for nom, reg in srv.monde.vent.items():
        g(nom)["vent"] = {"x": reg["x"], "y": reg["y"], "on": reg["on"], "seq": reg["seq"]}
    vies = getattr(srv, "vies", None)
    for nom in (list(vies.groups) if vies is not None else []):
        d = vies.document(nom)
        if d is not None:
            g(nom)["vie"] = d                    # vie partagée : valeur commune et vie max du groupe
    return groupes


def restaurer_groupes(srv, groupes, log, coffres_max: int = 1024, gifts_max: int = 512) -> dict:
    """Charge chaque section dans l'état du serveur, en VALIDANT chaque entrée (une entrée abîmée est
    ignorée). Les groupes relus sont « fixés » : aucun client (hôte compris) n'impose plus ses compteurs, son
    sac ou son vent. Renvoie des comptes pour le journal."""
    n = {"progress": 0, "cles": 0, "coffres": 0, "cadeaux": 0, "inv": 0, "compteurs": 0, "vent": 0, "vie": 0,
         "ignores": 0}
    if not isinstance(groupes, dict):
        if groupes is not None:
            log("monde : section « groupes » illisible : ignorée")
        return n
    cles_json = {}
    for nom, sec in list(groupes.items())[:GROUPES_MAX]:
        if not isinstance(nom, str) or not nom or not isinstance(sec, dict):
            n["ignores"] += 1
            continue
        nom = nom[:32]
        if "progress" in sec:
            bits = from_wire(sec["progress"])
            if bits:
                srv.progress[nom] = bits
                n["progress"] += bits_count(bits)
            elif sec["progress"]:
                n["ignores"] += 1
        if isinstance(sec.get("cles"), dict):
            cles_json[nom] = sec["cles"]
        for rec in (sec.get("coffres") if isinstance(sec.get("coffres"), list) else []):
            v = valid_coffre(rec)
            reg = srv.coffres.setdefault(nom, {})
            if v is None or len(reg) >= coffres_max:
                n["ignores"] += 1
                continue
            v["by"] = _texte(rec.get("by"), 24)
            reg[(v["k"], v["i"], v["n"])] = v
            n["coffres"] += 1
        for rec in (sec.get("cadeaux") if isinstance(sec.get("cadeaux"), list) else []):
            v = valid_record(rec)       # quarts au sol et quarts des personnages (src « pnj: »)
            reg = srv.gifts.setdefault(nom, {})
            if v is None or len(reg) >= gifts_max:
                n["ignores"] += 1
                continue
            v["by"] = _texte(rec.get("by"), 24)
            reg[v["src"]] = v
            n["cadeaux"] += 1
        inv = sec.get("inv")
        if isinstance(inv, dict):
            sac = inv.get("sac")
            v = valid_inv({"seq": 0, "full": 1, "acq": inv.get("acq"), "bits": inv.get("bits"), "cap": inv.get("cap"),
                           "fl": inv.get("fl"), "sac": sac if isinstance(sac, list) else []})
            if v is None:
                n["ignores"] += 1
            else:
                srv.inv[nom] = {"acq": v["acq"], "bits": dict(v["bits"]), "cap": dict(v["cap"]), "fl": v["fl"] or 0,
                                "sac": v["sac"] if isinstance(sac, list) else None, "hote": True}
                n["inv"] += 1
        cp = sec.get("compteurs")
        if isinstance(cp, dict):
            tot = cp.get("tot") if isinstance(cp.get("tot"), dict) else {}
            st = srv.cnt.etat(nom)
            st["cap"].update(valid_caps(cp.get("cap")))
            for k, val in tot.items():
                if k in KS and _int(val) and 0 <= val <= VAL_MAX[k]:
                    st["tot"][k] = val
                else:
                    n["ignores"] += 1
            st["hote"] = set(st["tot"])                  # fixé : plus aucun hello n'impose ses valeurs
            evs = cp.get("evs") if isinstance(cp.get("evs"), list) else []
            st["evs"] = OrderedDict((e, 1) for e in evs[-CompteurPool.EV_MAX:] if isinstance(e, str) and EV_RE.match(e))
            if st["tot"]:
                n["compteurs"] += 1
            else:
                srv.cnt.oublier(nom)
        vt = sec.get("vent")
        if isinstance(vt, dict):
            v = valid_vent({"x": vt.get("x"), "y": vt.get("y"), "on": vt.get("on"), "group": nom})
            seq = vt.get("seq")
            if v is None or not _int(seq) or not 0 < seq < 1 << 31:
                n["ignores"] += 1
            else:
                # « change » : aucun init (hôte compris) ne le remplace ; un nouveau venu s'y range
                srv.monde.vent[nom] = {"x": v["x"], "y": v["y"], "on": v["on"], "seq": seq, "kind": "change",
                                       "hote": False, "from": 0}
                n["vent"] += 1
        if "vie" in sec and getattr(srv, "vies", None) is not None:
            if srv.vies.restaurer(nom, sec["vie"]):
                n["vie"] += 1                    # fixée : aucun hello (hôte compris) ne l'impose plus
            else:
                n["ignores"] += 1
    if cles_json:
        kp = KeyPool.from_json({"groups": cles_json})
        srv.keys = kp
        n["cles"] = sum(len(d) for d in kp.groups.values())
    return n


# ---------------------------------------------------------------------------------------------------------
# Quarantaine : ce qui reste d'un message d'une partie qui ne ressemble pas au monde
# ---------------------------------------------------------------------------------------------------------
MUETS_JOUEURS_MAX = 64
MUETS_PAR_JOUEUR_MAX = 4096


def muets_relus(doc) -> dict:
    """Section « rando_muets » relue (randomizer, étape 5) : {pseudo: {"emp": empreinte, "c": {(index, numéro)}}}.
    Toute entrée abîmée est ignorée ; rien n'est inventé."""
    out = {}
    if not isinstance(doc, dict):
        return out
    for nom, d in list(doc.items())[:MUETS_JOUEURS_MAX]:
        if not (isinstance(nom, str) and nom and isinstance(d, dict) and isinstance(d.get("emp"), str)
                and len(d["emp"]) <= 64 and isinstance(d.get("c"), list)):
            continue
        c = set()
        for x in d["c"][:MUETS_PAR_JOUEUR_MAX]:
            if isinstance(x, list) and len(x) == 2 and all(_int(v) and 0 <= v < 256 for v in x):
                c.add((x[0], x[1]))
        out[nom[:24]] = {"emp": d["emp"], "c": c}
    return out


def muets_document(muets: dict) -> dict:
    """Section « rando_muets » à écrire (forme JSON triée de ``muets_relus``)."""
    return {nom: {"emp": d["emp"], "c": [list(k) for k in sorted(d["c"])]}
            for nom, d in list(muets.items())[:MUETS_JOUEURS_MAX] if isinstance(d, dict) and d.get("c")}


PLUS_GROUPES_MAX = 64


def plus_relus(doc) -> tuple[str, dict]:
    """Section « rando_plus » relue (objets progressifs de TWWHD) : (empreinte de la table, {groupe: {session:
    {famille: n}}}). Toute entrée abîmée est ignorée ; rien n'est inventé."""
    from rando import plus as rando_plus
    if not (isinstance(doc, dict) and isinstance(doc.get("emp"), str) and len(doc["emp"]) <= 64
            and isinstance(doc.get("groupes"), dict)):
        return "", {}
    out = {}
    for g, sessions in list(doc["groupes"].items())[:PLUS_GROUPES_MAX]:
        if not (isinstance(g, str) and isinstance(sessions, dict)):
            continue
        d = {}
        for s, c in list(sessions.items())[:rando_plus.SESSIONS_MAX]:
            try:
                ses = int(s)
            except (TypeError, ValueError):
                continue
            if not (0 < ses < 1 << 32 and isinstance(c, dict)):
                continue
            d[ses] = {f: n for f, n in c.items()
                      if f in rando_plus.NOMS and _int(n) and 0 <= n <= rando_plus.COMPTE_MAX}
        out[g[:32]] = d
    return doc["emp"], out


def plus_document(emp: str, groupes: dict) -> dict:
    """Section « rando_plus » à écrire (forme JSON de ``plus_relus`` : sessions en texte)."""
    return {"emp": emp, "groupes": {g: {str(s): dict(c) for s, c in d.items()}
                                    for g, d in list(groupes.items())[:PLUS_GROUPES_MAX]}}


def filtre_quarantaine(msg: dict) -> dict | None:
    """Il reçoit le monde (inscriptions), rien de lui n'y entre. None : ignoré."""
    t = msg.get("type")
    if t == "progress":
        return dict(msg, bits={}, full=True) if msg.get("full") in (1, True) else None
    if t in ("coffre", "gift"):
        return {"type": t, "group": msg.get("group"), "full": 1} if msg.get("full") in (1, True) else None
    if t == "inv":
        seq = msg.get("seq", 0)
        return {"type": "inv", "group": msg.get("group"), "seq": seq if _int(seq) and seq >= 0 else 0, "sync": 1}
    if t == "keys":
        return dict(msg, slots={}) if msg.get("hello") in (1, True) else None
    if t in ("key_get", "key_use", "pickup", "image_monde", "trk", "rando_plus"):   # « trk » : trackeur ;
        # « rando_plus » : dons progressifs de TWWHD reçus dans une partie en quarantaine
        return None
    if t == "vie":
        # vie partagée : il reçoit la valeur commune (hello sans « hote », sync), aucun de ses dégâts ou soins n'y entre
        if msg.get("hello") in (1, True):
            return {k: v for k, v in msg.items() if k != "hote"}
        return msg if msg.get("sync") in (1, True) else None
    if t == "cnt":
        if msg.get("hello") in (1, True):
            return {k: v for k, v in msg.items() if k not in ("hote", "cap")}
        return msg if msg.get("sync") in (1, True) else None
    return msg


def _inscription(msg: dict) -> bool:
    """Demande d'état, sans aucun fait : passe même pendant l'attente du verdict."""
    t = msg.get("type")
    if t == "progress":
        return msg.get("full") in (1, True) and not from_wire(msg.get("bits"))
    if t in ("coffre", "gift"):
        return msg.get("full") in (1, True)
    if t in ("inv", "cnt", "vie"):
        return msg.get("sync") in (1, True) and not msg.get("hello") and not msg.get("full")
    return False


# ---------------------------------------------------------------------------------------------------------
# Le monde (côté serveur)
# ---------------------------------------------------------------------------------------------------------
class Monde:
    """Un monde, un processus serveur (les zones, l'autorité et les switches temporaires ne sont pas rangés par
    groupe : deux mondes dans un même serveur se mélangeraient). ``srv`` : wwmp_server.Server."""

    def __init__(self, dossier: str, ident: str, nom: str, doc: dict | None = None, log=print,
                 clock=time.monotonic, deathlink: bool = False, quarantaine: bool = True, strict: bool = False,
                 heure_auto: bool = False, ecriture_min: float = ECRITURE_MIN, source: str = "",
                 difficulte: dict | None = None, vie_partagee: bool = False):
        self.dossier, self.id, self.nom = dossier, ident, nettoyer_nom(nom) or ident
        self.chemin = os.path.join(dossier, FICHIER)
        self.log, self.clock = log, clock
        self.quarantaine_on, self.strict, self.heure_auto = bool(quarantaine), bool(strict), bool(heure_auto)
        self.ecriture_min = max(0.5, float(ecriture_min))
        self.doc_source = source
        doc = doc or {}
        self.neuf = not doc
        self.cree = _texte(doc.get("cree")) or maintenant_iso()
        self.modifie = _texte(doc.get("modifie"))
        regles = doc.get("regles") if isinstance(doc.get("regles"), dict) else {}
        # deathlink : la ligne de commande ne compte qu'à la création du monde
        self.deathlink = regles.get("deathlink") is True if "deathlink" in regles else bool(deathlink)
        # vie partagée : comme le deathlink (la ligne de commande ne compte qu'à la création du monde)
        self.vie_partagee = regles.get("vie_partagee") is True if "vie_partagee" in regles else bool(vie_partagee)
        # difficulté (commun/difficulte.py) : comme le deathlink, la ligne de commande ne compte qu'à la création
        self.difficulte = _dif.normaliser(regles["difficulte"] if "difficulte" in regles else difficulte)
        # mode Hanteurs : RÉGLAGES de la dernière session (bruts : validés par le serveur, hanteurs.valider_config) ;
        # gardés tels quels quand le serveur tourne sans le mode. La manche en cours n'est jamais ici.
        h = regles.get("hanteurs")
        self.hanteurs_regles: dict | None = h if isinstance(h, dict) and len(h) <= 64 else None
        self.image_anciens = False         # --monde-image-anciens-hanteurs (désactivé par défaut)
        self.image: bytes | None = None
        self.image_meta: dict = {}
        self._lire_image(doc.get("image"))
        # chantier E (randomizer, palier 1) : table de la partie, gardée TELLE QUELLE (validée et appliquée par le
        # serveur, jamais recalculée ; une table que ces outils refusent reste dans le fichier)
        r = doc.get("rando")
        self.rando_doc: dict | None = r if isinstance(r, dict) and isinstance(r.get("t"), list) else None
        self.rando_off = False
        # randomizer par fichiers : {plan, fichiers} (empreinte des fichiers générés retenue au premier joueur)
        # randomizer, étape 5 : coffres étrangers muets par joueur (validés par muets_relus au démarrage du serveur)
        rm_ = doc.get("rando_muets")
        self.rando_muets_doc: dict | None = rm_ if isinstance(rm_, dict) else None
        # objets progressifs de TWWHD : dons progressifs par groupe et par session (validés par plus_relus)
        rp_ = doc.get("rando_plus")
        self.rando_plus_doc: dict | None = rp_ if isinstance(rp_, dict) else None
        rf = doc.get("rando_fichiers")
        self.rando_fichiers: dict | None = {"plan": rf["plan"], "fichiers": rf["fichiers"]} \
            if isinstance(rf, dict) and isinstance(rf.get("plan"), str) and isinstance(rf.get("fichiers"), str) \
            and len(rf["plan"]) <= 64 and len(rf["fichiers"]) <= 64 else None
        h = doc.get("heure")
        self.heure = None
        if isinstance(h, dict) and isinstance(h.get("t"), (int, float)) and not isinstance(h.get("t"), bool) \
                and 0 <= h["t"] < 360 and _int(h.get("d")) and 0 <= h["d"] <= 0xFFFF:
            self.heure = {"t": float(h["t"]), "d": h["d"], "de": _texte(h.get("de"), 24), "quand": _texte(h.get("quand"))}
        self.joueurs: dict[str, dict] = {}
        for nomj, j in (doc.get("joueurs") or {}).items() if isinstance(doc.get("joueurs"), dict) else []:
            if isinstance(nomj, str) and nomj and isinstance(j, dict) and len(self.joueurs) < JOUEURS_MAX:
                lieu = j.get("lieu")
                self.joueurs[nomj[:24]] = {
                    "premiere": _texte(j.get("premiere")), "derniere": _texte(j.get("derniere")),
                    "temps": int(j["temps"]) if _int(j.get("temps")) and j["temps"] >= 0 else 0,
                    "lieu": [lieu[0][:8], lieu[1]] if isinstance(lieu, list) and len(lieu) == 2
                    and isinstance(lieu[0], str) and (_int(lieu[1]) or lieu[1] is None) else None}
        hist = doc.get("quarantaines")
        self.quarantaines = [dict(nom=_texte(q.get("nom"), 24), quand=_texte(q.get("quand")),
                                  nouveaux=q.get("nouveaux") if _int(q.get("nouveaux")) else 0,
                                  absents=q.get("absents") if _int(q.get("absents")) else 0)
                             for q in (hist if isinstance(hist, list) else [])[-HISTO_MAX:] if isinstance(q, dict)]
        lib = doc.get("liberes")
        alias = doc.get("alias")
        self.alias = [nettoyer_nom(a) for a in (alias if isinstance(alias, list) else [])[:16] if isinstance(a, str)]
        self.liberes: dict[str, str] = {k[:24]: v[:16] for k, v in (lib.items() if isinstance(lib, dict) else [])
                                        if isinstance(k, str) and isinstance(v, str)}
        sus = doc.get("suspects")
        self.suspects: dict[str, int] = {k[:24]: v for k, v in (sus.items() if isinstance(sus, dict) else [])
                                         if isinstance(k, str) and k and _int(v) and v >= 0}
        while len(self.suspects) > SUSPECTS_MAX:
            self.suspects.pop(next(iter(self.suspects)))
        self.illisible_garde = False
        self.groupes_doc = doc.get("groupes")
        # trackeur du randomizer : section relue telle quelle, validée par le serveur (Server.trk_restaurer)
        self.trackeur_doc = doc.get("trackeur") if isinstance(doc.get("trackeur"), dict) else None
        # médailles de fin de donjon (serveur/medailles.py) : relues telles quelles, validées par le serveur
        self.medailles_doc = doc.get("medailles") if isinstance(doc.get("medailles"), dict) else None
        # records des courses en bateau (serveur/courses.py) : relus tels quels, validés par le serveur
        self.courses_doc = doc.get("courses") if isinstance(doc.get("courses"), dict) else None
        # Fragments du Bonheur (serveur/bonheur.py) : poches et quarts donnés, relus tels quels, validés par le serveur
        self.bonheur_doc = doc.get("bonheur") if isinstance(doc.get("bonheur"), dict) else None
        # salles retenues (canons détruits, switches de mort ; synchro/canons.py §3), validées par SallesDetruites
        self.salles_doc = doc.get("salles") if isinstance(doc.get("salles"), dict) else None
        self.ignores = 0
        # écriture
        self.sale = False
        self.rapide_avant: float | None = None
        self.t_ecrit = clock()
        self.n_ecritures = 0
        self.erreur_ecriture = ""
        # image : demande en cours, prochaine demande
        self.req: dict | None = None
        self.prochaine_image = clock() + (IMAGE_CONNECTE_MIN if self.image is None else IMAGE_PERIODE)
        self.image_forcee: str | None = None        # admin : « reprendre l'image de X » (recul permis une fois)
        self.t_boucle = clock()

    # --- ouverture ----------------------------------------------------------------------------------------
    @classmethod
    def ouvrir(cls, dossier_mondes: str, nom_ou_id: str, log=print, nom_neuf: str | None = None, **kw) -> "Monde":
        """Monde existant (id ou nom) ou nouveau (``nom_neuf`` : son nom, sinon ``nom_ou_id``). Lève MondeIllisible /
        MondeTropRecent (rien n'est écrasé)."""
        dossier, ident, nom = resoudre(dossier_mondes, nom_ou_id)
        if nettoyer_nom(nom_neuf):
            nom = nettoyer_nom(nom_neuf)
        doc, source, replis = charger(dossier)
        for r in replis:
            log(f"monde : {r}")
        if doc is not None and source != os.path.join(dossier, FICHIER):
            log(f"monde : ATTENTION, monde.json illisible ou absent : repris de {source}")
        if doc is not None:
            if doc["id"] != ident:
                log(f"monde : identifiant du fichier ({doc['id']}) différent du dossier ({ident}) : celui du fichier gardé")
                ident = doc["id"]
            nom = nettoyer_nom(doc.get("nom")) or nom
        return cls(dossier, ident, nom, doc, log=log, source=source, **kw)

    def _lire_image(self, im) -> None:
        if not isinstance(im, dict):
            return
        try:
            img = base64.b64decode(str(im.get("b64", "")), validate=True)
        except (ValueError, TypeError):
            self.log("monde : image du monde illisible (base64) : ignorée")
            return
        if sc.sha256(img) != im.get("sha256"):
            self.log("monde : image du monde abîmée (sha256 faux) : ignorée")
            return
        raison = valider_image_brute(img)
        if raison:
            self.log(f"monde : image du monde refusée ({raison}) : ignorée")
            return
        self.image = img
        lieu_room = im.get("room")
        self.image_meta = {"de": _texte(im.get("de"), 24), "quand": _texte(im.get("quand")), "note": note_image(img),
                           "stage": _texte(im.get("stage"), 8), "room": lieu_room if _int(lieu_room) else 0,
                           "stage_no": im.get("stage_no") if _int(im.get("stage_no")) else 0,
                           "resume": resume_image(img)}

    # --- état du serveur ----------------------------------------------------------------------------------
    def restaurer(self, srv) -> None:
        """Au démarrage : toutes les sections dans le serveur ; règle du deathlink."""
        srv.morts.on = self.deathlink
        if getattr(srv, "vies", None) is not None:
            # jamais avec le mode Hanteurs (la règle reste gardée dans le monde pour la coop suivante)
            srv.vies.on = self.vie_partagee and getattr(srv, "hanteurs", None) is None
        if getattr(srv, "difficulte_on", False):
            srv.difficulte = _dif.normaliser(self.difficulte)
        n = restaurer_groupes(srv, self.groupes_doc, self.log)
        self.ignores = n.pop("ignores")
        self.groupes_doc = None
        if getattr(self, "trackeur_doc", None) is not None and hasattr(srv, "trackeur_doc"):
            srv.trackeur_doc = self.trackeur_doc
        if getattr(self, "medailles_doc", None) is not None and hasattr(srv, "medailles"):
            self.ignores += srv.medailles.restaurer(self.medailles_doc)
            self.medailles_doc = None
        if getattr(self, "courses_doc", None) is not None and hasattr(srv, "courses"):
            self.ignores += srv.courses.restaurer(self.courses_doc)
            self.courses_doc = None
        if getattr(self, "bonheur_doc", None) is not None and hasattr(srv, "bonheur"):
            self.ignores += srv.bonheur.restaurer(self.bonheur_doc)
            self.bonheur_doc = None
        if getattr(self, "salles_doc", None) is not None and hasattr(srv, "salles"):
            ok, bad = srv.salles.restaurer(self.salles_doc)
            self.ignores += bad
            n["salles"] = ok
            self.salles_doc = None
        if self.neuf:
            self.log(f"monde « {self.nom} » ({self.id}) : NOUVEAU monde, dans {self.dossier}")
            self.sale = True
        else:
            self.log(f"monde « {self.nom} » ({self.id}) relu de {self.doc_source} : "
                     + ", ".join(f"{k} {v}" for k, v in n.items())
                     + (f", image de {self.image_meta.get('de') or '?'} (note {self.image_meta.get('note')})"
                        if self.image else ", pas encore d'image")
                     + (f" ; {self.ignores} entrée(s) abîmée(s) ignorée(s)" if self.ignores else ""))

    def migrer_cles(self, srv, chemin: str | None) -> None:
        """``cles.json`` (ancien --keys-file) repris UNE fois dans un monde neuf, puis renommé ``.migre``."""
        if not chemin or not os.path.isfile(chemin):
            return
        if self.neuf:
            if not srv.keys.groups:
                srv.keys = KeyPool.load(chemin)
            if srv.keys.groups:
                self.log(f"monde : petites clés reprises de {chemin}")
                self.marquer(rapide=True)
        try:
            os.replace(chemin, chemin + ".migre")
            self.log(f"monde : {chemin} renommé en .migre (les clés sont gardées dans le monde)")
        except OSError as e:
            self.log(f"monde : {chemin} non renommé ({e})")

    def document(self, srv) -> dict:
        doc = {"format": FORMAT, "version": VERSION_FORMAT, "id": self.id, "nom": self.nom,
               "cree": self.cree, "modifie": maintenant_iso(),
               "serveur": {"version": VERSION, "outils": CLIENT_VERSION},
               "jeu": JEU, "regles": self.regles(srv),
               "heure": self.heure, "joueurs": self.joueurs, "quarantaines": self.quarantaines,
               "liberes": self.liberes, "suspects": self.suspects, "alias": self.alias,
               "groupes": exporter_groupes(srv)}
        if self.rando_doc is not None:
            doc["rando"] = self.rando_doc
        if getattr(self, "rando_fichiers", None) is not None:
            doc["rando_fichiers"] = dict(self.rando_fichiers)   # un ancien serveur l'ignore sans erreur
        if getattr(self, "rando_muets_doc", None):
            doc["rando_muets"] = self.rando_muets_doc          # étape 5 (un ancien serveur l'ignore sans erreur)
        if getattr(self, "rando_plus_doc", None):
            doc["rando_plus"] = self.rando_plus_doc            # progressifs de TWWHD (un ancien serveur l'ignore)
        trk = srv.trk_document() if hasattr(srv, "trk_document") else None
        if trk is not None:
            doc["trackeur"] = trk      # corrections manuelles du trackeur (un ancien serveur l'ignore sans erreur)
        med = srv.medailles.exporter() if hasattr(srv, "medailles") else None
        if med is not None and (med["finis"] or med["en_cours"]):
            doc["medailles"] = med     # médailles de fin de donjon (un ancien serveur l'ignore sans erreur)
        elif getattr(self, "medailles_doc", None):
            doc["medailles"] = self.medailles_doc     # relue mais pas encore rendue au serveur : jamais perdue
        crs = srv.courses.exporter() if hasattr(srv, "courses") else None
        if crs is not None and crs["records"]:
            doc["courses"] = crs       # records des courses en bateau (un ancien serveur l'ignore sans erreur)
        elif getattr(self, "courses_doc", None):
            doc["courses"] = self.courses_doc         # relue mais pas encore rendue au serveur : jamais perdue
        bon = srv.bonheur.exporter() if hasattr(srv, "bonheur") else None
        if bon and (bon.get("poches") or bon.get("historique") or (bon.get("quarts") or {}).get("total")):
            doc["bonheur"] = bon       # Fragments du Bonheur : poches, quarts (un ancien serveur l'ignore sans erreur)
        elif getattr(self, "bonheur_doc", None):
            doc["bonheur"] = self.bonheur_doc         # relue mais pas encore rendue au serveur : jamais perdue
        salles = srv.salles.document() if hasattr(srv, "salles") else {}
        if salles:
            doc["salles"] = salles     # canons détruits, switches de mort par salle (un ancien serveur l'ignore)
        elif getattr(self, "salles_doc", None):
            doc["salles"] = self.salles_doc   # relue mais pas encore rendue au serveur : jamais perdue
        if self.image is not None:
            doc["image"] = dict({k: v for k, v in self.image_meta.items()},
                                b64=base64.b64encode(self.image).decode("ascii"), sha256=sc.sha256(self.image),
                                fmt=sc.FMT)
        return doc

    def regles(self, srv) -> dict:
        """Règles gardées. Mode Hanteurs : les réglages de la session (ceux du serveur s'il tourne avec le mode,
        sinon les derniers gardés), JAMAIS la manche (rôles, points, possessions, potion, ordres)."""
        if getattr(srv, "difficulte_on", False):         # --no-difficulte : le réglage gardé n'est pas écrasé
            self.difficulte = _dif.normaliser(getattr(srv, "difficulte", None))
        vies = getattr(srv, "vies", None)
        if vies is not None and getattr(srv, "hanteurs", None) is None:
            self.vie_partagee = bool(vies.on)       # session Hanteurs : la règle gardée n'est pas écrasée
        r = {"deathlink": bool(srv.morts.on), "vie_partagee": bool(self.vie_partagee),
             "monde_heure": self.heure_auto, "difficulte": dict(self.difficulte)}
        h = getattr(srv, "hanteurs", None)
        if h is not None:
            self.hanteurs_regles = dict(h.cfg)
        if self.hanteurs_regles is not None:
            r["hanteurs"] = self.hanteurs_regles
        return r

    def exclu_image(self, srv, p, refus: bool = True) -> str:
        """Mode Hanteurs : raison pour laquelle l'image du monde n'est pas demandée à ce joueur ("" : permise).
        Un Hanteur (Link caché, il ne joue pas le monde), un ancien Hanteur en quarantaine, un Link possédé (potion :
        Link caché et téléporté) : jamais. Un ancien Hanteur redevenu Link : sa sauvegarde contient ce que son Link
        caché a ramassé en Hanteur, refusé sauf ``image_anciens``.
        Relecture adverse : vaut aussi quand ce serveur tourne SANS le mode (session Hanteurs finie, serveur
        relancé en coop normale, client resté lancé : son hello dit ``ex_hanteur`` et il refuse l'image de son
        côté). Sans cela, le plus avancé était redemandé sans fin et personne d'autre ne l'était. ``refus`` : un
        client qui vient de refuser (« Hanteur ») n'est pas redemandé avant ``IMAGE_PERIODE`` (choix automatique)."""
        if refus and getattr(p, "mp_image_refus", None) is not None and self.clock() < p.mp_image_refus:
            return "refus de son client"
        h = getattr(srv, "hanteurs", None)
        if h is None:
            return "ancien Hanteur" if getattr(p, "ex_hanteur", False) and not self.image_anciens else ""
        if h.possede_par(p.id) is not None:      # avant bloque_monde (synchro v4 : il compte aussi le Link possédé)
            return "Link possédé"
        if h.bloque_monde(p.id):
            return "Hanteur"
        if (h.ancien_hanteur(p.id) or getattr(p, "ex_hanteur", False)) and not self.image_anciens:
            return "ancien Hanteur"
        return ""

    def garder_illisible(self) -> None:
        """Monde relu d'un repli (``.1`` ou copie) parce que monde.json est illisible : avant la première écriture,
        ce fichier est mis de côté (``monde.json.illisible-DATE``). Sinon ``ecrire_atomique`` le recopiait sur la
        bonne ``.1`` puis l'écrasait (relecture adverse : « un fichier illisible n'est jamais écrasé »)."""
        if self.illisible_garde or not self.doc_source or self.doc_source == self.chemin:
            return
        if os.path.exists(self.chemin):
            garde = self.chemin + time.strftime(".illisible-%Y%m%d-%H%M%S", time.localtime())
            os.replace(self.chemin, garde)          # OSError : écriture refusée, réessayée (rien n'est écrasé)
            self.log(f"monde : monde.json illisible gardé tel quel dans {garde}")
        self.illisible_garde = True

    def ecrire(self, srv, raison: str = "") -> bool:
        try:
            self.garder_illisible()
            doc = self.document(srv)
            ecrire_atomique(self.chemin, doc)
            copies_de_secours(self.dossier, doc)
        except (OSError, TypeError, ValueError) as e:
            if str(e) != self.erreur_ecriture:
                self.log(f"monde : ÉCHEC d'écriture de {self.chemin} ({e.__class__.__name__}: {e}) : nouvel essai "
                         f"dans {self.ecriture_min:.0f} s")
            self.erreur_ecriture = str(e)
            self.t_ecrit = self.clock()
            return False
        if self.erreur_ecriture:
            self.log("monde : écriture de nouveau possible")
        self.erreur_ecriture = ""
        self.modifie = doc["modifie"]
        self.sale, self.rapide_avant, self.t_ecrit = False, None, self.clock()
        self.n_ecritures += 1
        if raison:
            self.log(f"monde « {self.nom} » écrit ({raison})")
        return True

    def marquer(self, rapide: bool = False, gain: bool = False) -> None:
        """Quelque chose du monde a changé. ``rapide`` : écrire sous ECRITURE_RAPIDE s ; ``gain`` : en plus,
        demander une image IMAGE_APRES_GAIN s plus tard."""
        self.sale = True
        now = self.clock()
        if rapide or gain:
            t = now + ECRITURE_RAPIDE
            self.rapide_avant = t if self.rapide_avant is None else min(self.rapide_avant, t)
        if gain:
            self.prochaine_image = min(self.prochaine_image, now + IMAGE_APRES_GAIN)

    # --- boucle (1 s) -------------------------------------------------------------------------------------
    def tick(self, srv, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        for p in list(srv.players.values()):
            t0 = getattr(p, "mp_t0", None)
            if getattr(p, "mp_verdict", "ok") == "attente" and t0 is not None and now - t0 >= VERDICT_ATTENTE:
                self.log(f"monde : {p.name} : rien à juger en {VERDICT_ATTENTE:.0f} s, accepté")
                self.verdict(srv, p, "ok")
        if self.sale and (now - self.t_ecrit >= self.ecriture_min
                          or (self.rapide_avant is not None and now >= self.rapide_avant)):
            self.ecrire(srv)
        if self.req is not None and now - self.req["t"] > IMAGE_ATTENTE:
            o = srv.players.get(self.req["pid"])
            self.log(f"monde : pas d'image de {o.name if o else '?'} en {IMAGE_ATTENTE:.0f} s : abandon")
            self.req = None
            self.prochaine_image = now + IMAGE_REESSAI
        if self.req is None and now >= self.prochaine_image:
            self.demander_image(srv, now)

    # --- joueurs ------------------------------------------------------------------------------------------
    def arrivee(self, srv, p) -> None:
        """Hello d'un client de jeu accepté : verdict (quarantaine) et liste des joueurs du monde."""
        # le délai du verdict part du premier fait GARDÉ, pas de la connexion : un joueur resté plus de
        # VERDICT_ATTENTE s à l'écran titre (cas normal : le client se connecte avant qu'on charge un Journal) était
        # accepté sans jugement, puis sa partie solo entrait dans le monde (relecture adverse)
        p.mp_t0 = None
        union = max((bits_count(u) for u in srv.progress.values()), default=0)
        if (not self.quarantaine_on or "monde_tot" not in p.feat or p.name in self.liberes
                or union < QUAR_MONDE_MIN):
            p.mp_verdict = "ok"
        else:
            p.mp_verdict = "attente"
        j = self.joueurs.get(p.name)
        if j is None:
            if len(self.joueurs) >= JOUEURS_MAX:
                plus_vieux = min(self.joueurs, key=lambda k: self.joueurs[k].get("derniere") or "")
                del self.joueurs[plus_vieux]
            j = self.joueurs[p.name] = {"premiere": maintenant_iso(), "derniere": "", "temps": 0, "lieu": None}
            self.log(f"monde « {self.nom} » : premier passage de {p.name}")
        j["derniere"] = maintenant_iso()
        self.marquer()

    def depart(self, srv, p) -> None:
        j = self.joueurs.get(p.name)
        if j is None:
            return
        j["derniere"] = maintenant_iso()
        j["temps"] = int(j.get("temps", 0) + max(0.0, self.clock() - p.connected))
        stage, room = p.where()
        if stage:
            j["lieu"] = [stage[:8], room]
        self.marquer()
        if self.req is not None and self.req["pid"] == p.id:
            self.req = None
            self.prochaine_image = self.clock() + IMAGE_REESSAI

    def epoque(self, srv, p) -> str:
        """Époque annoncée dans le welcome : celle du serveur, suffixée pour un joueur libéré de quarantaine
        (son client oublie ce qu'il croit avoir envoyé, et renvoie tout)."""
        jeton = self.liberes.get(p.name)
        return f"{srv.epoque}.{jeton}" if jeton else srv.epoque

    # --- verdict / quarantaine ----------------------------------------------------------------------------
    def entree(self, srv, p, msg: dict) -> list[dict]:
        """Messages d'un client de jeu à traiter maintenant (attente du verdict, quarantaine)."""
        t = msg.get("type")
        if t in ("image_monde", "image_err"):
            self.recevoir_image(srv, p, msg)
            return []
        v = getattr(p, "mp_verdict", "ok")
        if v == "attente":
            if t == "progress" and _int(msg.get("tot")) and not msg.get("full"):
                self.juger(srv, p, msg)
                return self.verdict(srv, p, p.mp_verdict, premier=msg, rejouer=False)
            if t in TYPES_TAMPON and not _inscription(msg):
                if len(p.mp_tampon) >= TAMPON_MAX:
                    self.log(f"monde : {p.name} : trop de messages avant le verdict : accepté")
                    return self.verdict(srv, p, "ok", premier=msg, rejouer=False)
                if not p.mp_tampon:
                    p.mp_t0 = self.clock()
                p.mp_tampon.append(msg)
                return []
            return [msg]
        if v == "quarantaine":
            m = filtre_quarantaine(msg)
            return [m] if m is not None else []
        return [msg]

    def juger(self, srv, p, msg: dict) -> None:
        group = str(msg.get("group") or "default")[:32]
        union = srv.progress.get(group, {})
        n_union = bits_count(union)
        bits = from_wire(msg.get("bits"))
        nouveaux = bits_count(bits_sub(bits, union))
        tot = max(0, msg["tot"])
        absents = max(0, n_union - max(0, tot - nouveaux))
        # joueur déjà mis en quarantaine (gardé dans le monde) : sa partie a REÇU le monde pendant la quarantaine
        # (« absents » ~ 0 ensuite) ; à la reconnexion suivante (coupure, serveur redémarré), seuls ses bits
        # inconnus comptent (relecture adverse : sinon sa partie solo entrait au deuxième essai)
        suspect = p.name in self.suspects
        if n_union >= QUAR_MONDE_MIN and nouveaux >= QUAR_SEUIL and (absents >= QUAR_SEUIL or suspect):
            p.mp_verdict = "quarantaine"
            self.suspects.pop(p.name, None)
            self.suspects[p.name] = nouveaux
            while len(self.suspects) > SUSPECTS_MAX:
                self.suspects.pop(next(iter(self.suspects)))
            self.log(f"monde « {self.nom} » : QUARANTAINE pour {p.name} : sa partie a {nouveaux} bit(s) inconnus du "
                     f"monde et il lui manque environ {absents} bit(s) du monde (partie solo ou d'un autre monde "
                     "chargée ?). Il reçoit le monde, mais rien de lui n'y entre pendant cette connexion. "
                     "L'administrateur peut le libérer (lanceur, Mes parties → Mon monde)")
            self.quarantaines = (self.quarantaines + [{"nom": p.name, "quand": maintenant_iso(),
                                                       "nouveaux": nouveaux, "absents": absents}])[-HISTO_MAX:]
            self.marquer()
            p.send({"type": "error", "code": "quarantaine",
                    "msg": f"ta partie ne ressemble pas au monde « {self.nom} » (partie solo chargée ?) : tu vois le "
                           "monde, mais rien de ta partie n'y entre. Charge la partie de ce monde (lanceur)"})
        else:
            p.mp_verdict = "ok"
            if self.suspects.pop(p.name, None) is not None:
                self.marquer()
            self.log(f"monde : {p.name} accepté ({nouveaux} bit(s) nouveaux, ~{absents} en retard sur le monde)")

    def verdict(self, srv, p, v: str, premier: dict | None = None, rejouer: bool = True) -> list[dict]:
        """Fixe le verdict et rend les messages gardés (filtrés en quarantaine). ``rejouer`` : les traiter tout de
        suite (fin d'attente sans message en cours) ; sinon les renvoyer à l'appelant."""
        p.mp_verdict = v
        msgs = ([premier] if premier is not None else []) + list(getattr(p, "mp_tampon", []))
        p.mp_tampon = []
        if v == "quarantaine":
            msgs = [m for m in (filtre_quarantaine(m) for m in msgs) if m is not None]
        if not rejouer:
            return msgs
        for m in msgs:
            try:
                srv._handle(p, m)
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                self.log(f"{p.name} : message gardé mal formé ignoré ({type(e).__name__}: {e})")
        return []

    # --- image du monde -----------------------------------------------------------------------------------
    def candidats(self, srv, now: float) -> list:
        occupes = {r["j2"] for r in srv.rejoindre.values()}
        out = []
        for p in srv.players.values():
            if (p.joueur and "image_monde" in p.feat and getattr(p, "mp_verdict", "ok") == "ok" and p.zone
                    and not p.busy and p.id not in occupes and now - p.connected >= IMAGE_CONNECTE_MIN
                    and not self.exclu_image(srv, p)):
                out.append(p)
        out.sort(key=lambda p: (-(p.mp_tot or 0), -p.mp_actif, p.id))
        return out

    def demander_image(self, srv, now: float | None = None, qui=None) -> bool:
        now = self.clock() if now is None else now
        if qui is None:
            c = self.candidats(srv, now)
            if not c:
                self.prochaine_image = now + IMAGE_CONNECTE_MIN
                return False
            qui = c[0]
        rid = secrets.token_hex(4)
        self.req = {"rid": rid, "pid": qui.id, "t": now, "parts": {}, "n": None, "meta": None, "recu": 0}
        self.prochaine_image = now + IMAGE_PERIODE
        m = {"type": "image_req", "rid": rid, "monde": self.id}
        if self.image_anciens:
            m["anciens"] = 1           # option : le client d'un ancien Hanteur redevenu Link accepte de répondre
        qui.send(m)
        return True

    def recevoir_image(self, srv, p, msg: dict) -> None:
        r = self.req
        if r is None or msg.get("rid") != r["rid"] or r["pid"] != p.id:
            return
        if msg.get("type") == "image_monde" and self.exclu_image(srv, p, refus=False):
            # devenu Hanteur (ou possédé) entre la demande et la réponse : son image n'est pas celle d'un Link
            self.log(f"monde : image de {p.name} refusée ({self.exclu_image(srv, p, refus=False)} depuis la demande)")
            self.req = None
            self.prochaine_image = self.clock() + IMAGE_REESSAI
            return
        if msg.get("type") == "image_err":
            self.log(f"monde : {p.name} n'a pas pu lire sa partie ({_texte(msg.get('msg'), 160) or '?'}) : nouvel essai "
                     f"dans {IMAGE_REESSAI:.0f} s")
            self.req = None
            self.prochaine_image = self.clock() + IMAGE_REESSAI
            if msg.get("msg") == "Hanteur":
                # son client se sait Hanteur, possédé ou ancien Hanteur (rôle changé pendant la demande, serveur
                # relancé) : un autre joueur est choisi au prochain essai, lui plus tard
                p.mp_image_refus = self.clock() + IMAGE_PERIODE
            return
        data, ln, n, i = msg.get("data"), msg.get("len"), msg.get("parts"), msg.get("part")
        if not (isinstance(data, str) and _int(ln) and 0 < ln <= REJOINDRE_MAX and _int(n) and _int(i) and 0 <= i < n <= 4
                and len(data) <= (REJOINDRE_PART + 2) // 3 * 4 and r["recu"] + len(data) <= (REJOINDRE_MAX + 2) // 3 * 4):
            self.log(f"monde : image de {p.name} mal formée : refusée")
            self.req = None
            self.prochaine_image = self.clock() + IMAGE_REESSAI
            return
        try:
            r["parts"][i] = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            self.log(f"monde : image de {p.name} illisible (base64) : refusée")
            self.req = None
            return
        r["recu"] += len(data)
        if r["meta"] is None:
            r["meta"] = msg
        if len(r["parts"]) < n:
            return
        self.req = None
        img = b"".join(r["parts"][k] for k in range(n) if k in r["parts"])
        meta = r["meta"]
        if len(img) != ln or sc.sha256(img) != meta.get("sha256"):
            self.log(f"monde : image de {p.name} : contrôle sha256 faux : refusée")
            return
        self.accepter_image(img, p.name, meta)

    def accepter_image(self, img: bytes, de: str, meta: dict) -> bool:
        raison = valider_image_brute(img)
        if raison:
            self.log(f"monde : image de {de} refusée ({raison})")
            return False
        note = note_image(img)
        avant = self.image_meta.get("note") if self.image is not None else None
        force = self.image_forcee == de
        if avant is not None and note < avant and not force:
            self.log(f"monde : image de {de} moins avancée que celle du monde (note {note} < {avant}) : refusée, le "
                     "monde ne recule pas")
            return False
        self.image_forcee = None
        if self.image == img:
            return False
        self.image = bytes(img)
        stage, room, sno = meta.get("stage"), meta.get("room"), meta.get("stage_no")
        self.image_meta = {"de": _texte(de, 24), "quand": maintenant_iso(), "note": note,
                           "stage": stage[:8] if isinstance(stage, str) else "", "room": room if _int(room) else 0,
                           "stage_no": sno if _int(sno) else 0, "resume": resume_image(img)}
        self.log(f"monde « {self.nom} » : nouvelle image de {de} (note {note}"
                 + (f", avant {avant}" if avant is not None else "") + (", reprise demandée par l'administrateur" if force else "")
                 + ")")
        self.marquer(rapide=True)
        return True

    def servir(self, srv, p, rid: str, group: str) -> str:
        """« Copier la partie » (rejoindre_req) servi par le serveur : l'image du monde, mêmes morceaux et même
        sha256 qu'une image d'hôte. Renvoie « » ou la raison du refus."""
        if self.image is None:
            return (f"le monde « {self.nom} » n'a pas encore d'image : commence une partie neuve, ta partie deviendra "
                    "celle du monde")
        img = self.image
        parts = sc.decouper(img, REJOINDRE_PART)
        srv.rejoindre[rid] = {"j2": p.id, "hote": None, "t": self.clock(), "group": group, "groupe_hote": None,
                              "recu": 0, "relaye": True, "source": "monde"}
        m = self.image_meta
        for i, data in enumerate(parts):
            p.send({"type": "sauvegarde_hote", "rid": rid, "fmt": sc.FMT, "jeu": sc.JEU, "pack": None,
                    "client": CLIENT_VERSION, "name": self.nom, "monde": self.id, "stage": m.get("stage") or "",
                    "room": m.get("room", 0), "stage_no": m.get("stage_no", 0), "base": sc.IMG_BASE, "len": len(img),
                    "part": i, "parts": len(parts), "data": data, "sha256": sc.sha256(img), "group": group,
                    "from": None})
        self.log(f"rejoindre [{rid}] : image du monde « {self.nom} » (de {m.get('de') or '?'}, note {m.get('note')}) "
                 f"envoyée à {p.name}")
        return ""

    # --- heure, règles ------------------------------------------------------------------------------------
    def noter_heure(self, t: float, d: int, de: str) -> None:
        """Dernière heure connue (affichage, --monde-heure) : pas d'écriture pour elle seule."""
        self.heure = {"t": round(float(t), 2), "d": int(d), "de": _texte(de, 24), "quand": maintenant_iso()}

    # --- administration -----------------------------------------------------------------------------------
    def commande(self, srv, p, msg: dict) -> dict:
        """« monde_cmd » du lanceur de l'administrateur (déjà vérifié par le serveur) -> réponse."""
        cmd = msg.get("cmd")
        nom = _texte(msg.get("nom"), 32)
        if cmd == "liberer":
            if not nom:
                return {"ok": 0, "msg": "nom du joueur ?"}
            if len(self.liberes) >= LIBERES_MAX and nom not in self.liberes:
                self.liberes.pop(next(iter(self.liberes)))
            self.liberes[nom] = secrets.token_hex(4)
            self.suspects.pop(nom, None)
            self.marquer(rapide=True)
            n = 0
            for o in list(srv.players.values()):
                if o.joueur and o.name == nom:
                    n += 1
                    o.send({"type": "error", "msg": f"libéré par l'administrateur du monde « {self.nom} » : "
                                                    "reconnexion, ta partie est de nouveau partagée"})
                    o.writer.close()               # il se reconnecte, époque changée : il renvoie tout
            self.log(f"monde : {nom} libéré de la quarantaine par {p.name} ({n} connexion(s) relancée(s))")
            return {"ok": 1, "msg": f"{nom} libéré"}
        if cmd == "image":
            cand = [o for o in srv.players.values() if o.joueur and o.name == nom and "image_monde" in o.feat]
            if not cand:
                return {"ok": 0, "msg": f"{nom} n'est pas connecté (ou outils trop anciens)"}
            raison = self.exclu_image(srv, cand[0], refus=False)
            if raison:
                suite = ("il doit d'abord reprendre la partie du monde et relancer son jeu" if raison == "ancien Hanteur"
                         else "réessaie quand il joue en Link")
                return {"ok": 0, "msg": f"{nom} : {raison} (mode Hanteurs), son image n'est pas celle du monde ; "
                                        + suite}
            if self.req is not None:
                return {"ok": 0, "msg": "une demande d'image est déjà en cours, réessaie dans une minute"}
            self.image_forcee = cand[0].name
            self.demander_image(srv, qui=cand[0])
            self.log(f"monde : l'administrateur ({p.name}) reprend l'image de {nom} (même moins avancée)")
            return {"ok": 1, "msg": f"image de {nom} demandée"}
        if cmd == "renommer":
            nouveau = nettoyer_nom(msg.get("nom"))
            if not nouveau:
                return {"ok": 0, "msg": "nom vide"}
            self.log(f"monde : « {self.nom} » renommé « {nouveau} » par {p.name}")
            if self.nom not in self.alias:
                self.alias = (self.alias + [self.nom])[-16:]
            self.nom = nouveau
            self.marquer(rapide=True)
            return {"ok": 1, "msg": f"monde renommé « {nouveau} »"}
        if cmd == "sauver":
            ok = self.ecrire(srv, f"demandé par {p.name}")
            return {"ok": int(ok), "msg": "monde écrit" if ok else self.erreur_ecriture or "échec"}
        return {"ok": 0, "msg": f"commande inconnue {str(cmd)[:16]!r}"}

    # --- lobby / welcome ----------------------------------------------------------------------------------
    def bref(self) -> dict:
        return {"id": self.id, "nom": self.nom}

    def lobby(self, srv) -> dict:
        en_ligne = {o.name for o in srv.players.values() if o.joueur}
        quar = sorted(o.name for o in srv.players.values() if o.joueur and getattr(o, "mp_verdict", "ok") == "quarantaine")
        joueurs = sorted(({"nom": k, "premiere": j.get("premiere"), "derniere": j.get("derniere"),
                           "temps": j.get("temps", 0), "lieu": j.get("lieu"), "en_ligne": k in en_ligne}
                          for k, j in self.joueurs.items()), key=lambda d: d.get("derniere") or "", reverse=True)
        m = self.image_meta
        return {"id": self.id, "nom": self.nom, "cree": self.cree, "modifie": self.modifie,
                "image": self.image is not None, "note": m.get("note"), "image_de": m.get("de"),
                "image_quand": m.get("quand"), "resume": m.get("resume"), "seuil_retard": SEUIL_RETARD,
                "joueurs": joueurs[:64], "heure": self.heure, "deathlink": bool(srv.morts.on),
                "vie_partagee": bool(getattr(getattr(srv, "vies", None), "on", False)),
                "difficulte": dict(self.difficulte),
                "rando": (self.rando_doc or {}).get("emp") if self.rando_doc else None,
                "quarantaine": quar, "quarantaines": self.quarantaines[-8:],
                "union": sum(bits_count(u) for u in srv.progress.values()),
                "ecriture": {"erreur": self.erreur_ecriture, "sale": self.sale}}
