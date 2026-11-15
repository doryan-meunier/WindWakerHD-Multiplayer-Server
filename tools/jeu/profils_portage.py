"""``profiles.json`` du portage natif ZeldaWWHDRecomp : allumer NOS mods (``wwmp-*``) et confirmer leur code, à la
place de F1 > Mods > Switch on + avertissement + Restart now (docs/dev/PORTAGE_NATIF.md §8.2).

Ce que fait le portage (lu dans ses sources v0.5.1, ``runtime/src/mods/packages.cpp`` et ``mod_json.h``, et dans
``docs/mod-manager.md`` ; rien deviné) :

* ``<réglages>/ModManager/profiles.json``, UNE ligne de JSON compact (clés triées, ``std::map``) suivie de « \\n »,
  écrite par le jeu via ``profiles.json.tmp`` puis remplacement. Relu au démarrage (``prepare_metadata_unlocked``) :
  refusé (aucun mod tiers activé) si ``format_version`` n'est pas le nombre 1, si ``profiles`` n'est pas un objet non
  vide, si ``active`` n'y nomme pas un profil, si un profil n'est pas un objet, si une clé est en double, au-delà de
  1 Mio ;
* allumé = ``profiles[<active>].enabled[<id>] == true`` (PAR PROFIL ; ``enable()`` note aussi
  ``enabled_since[<id>]`` = ``++enable_serial`` pour un mod qui s'allume) ;
* confiance du code = ``native_trust[<id>]`` (POUR TOUS LES PROFILS) = ``package_fingerprint`` du dossier du mod :
  sha256 de « chemin\\0sha256\\n » de chaque fichier, triés (``jeu.portage.empreinte_dossier``). Une simple
  empreinte : ni signature, ni secret, ni mécanisme contre l'automatisation ; un fichier changé = plus de confiance
  (le jeu laisse alors le mod éteint, « Not loaded: … not confirmed », et le redemande dans F1 > Mods) ;
* un mod de code (``kind`` guest) allumé et confirmé est CONSTRUIT puis chargé au démarrage (``start_guests`` :
  ``inspect`` + ``assign_guest_region`` + ``build`` : la même construction que l'étape « Construire Wind Waker
  Coop… » de F1 > Mods, cache dans ``ModManager/GuestBuild``). Écrit AVANT que le jeu démarre, il n'y a donc pas de
  « Restart now » : ce redémarrage ne sert, dans le jeu, qu'à charger un mod allumé APRÈS le démarrage. Le lanceur
  ne construit rien lui-même (le compilateur et le traducteur sont ceux du portage) ;
* il faut le support des mods de code (``code-mods-active.json`` : ``state`` ready, ``hooks`` true, construit par le
  jeu en ~5 min, ``code_mods.cpp``) : sans lui, rien n'est écrit, l'étape reste dans le jeu (Switch on l'ajoute) ;
* retirer un mod (``remove_package``) efface, pour lui : ``enabled`` / ``config`` / ``setup_pending`` de chaque
  profil, ``native_trust``, ``experimental_ack``, ``catalogue_experimental``, ``guest_regions``, ``guest_prepared``,
  ``setup_receipts``. ``effacer`` fait de même pour un de NOS mods dont le dossier a disparu.

Règles d'écriture (ce module seulement, appelé par le lanceur, jeu FERMÉ) : seulement les entrées de nos
identifiants (``wwmp-``) ; tout le reste du fichier est gardé OCTET POUR OCTET (le texte est modifié sur place aux
endroits de nos clés, jamais réécrit) ; confiance donnée seulement à l'empreinte que l'appelant a vérifiée sur le
paquet qu'il vient d'installer ; copie de sécurité de l'ancien fichier ; écriture atomique (fichier temporaire à
côté, puis remplacement) ; format inconnu (version du portage ou fichier différents) : refus, rien n'est écrit.
"""
from __future__ import annotations

import json
import os
import re
import time

NOM = "profiles.json"
FORMAT = 1
TAILLE_MAX = 1 << 20                     # le portage refuse un profiles.json plus gros (read_text)
PROFONDEUR_MAX = 32                      # et plus profond (mod_json.h)
VERSIONS = ((0, 5),)                     # versions (majeure, mineure) dont ce format a été lu dans les sources
PREFIXE = "wwmp-"
ID_RE = re.compile(r"^wwmp-[a-z0-9_.-]{1,59}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GARDER = 10                              # copies de sécurité gardées
# remove_package : ce que le jeu oublie d'un mod retiré
PAR_PROFIL = ("enabled", "config", "setup_pending")
GLOBAL = ("native_trust", "experimental_ack", "catalogue_experimental", "guest_regions", "guest_prepared",
          "setup_receipts")


class ErreurProfils(Exception):
    """Refus propre (fr, en) : profiles.json n'a pas été touché."""

    def __init__(self, fr: str, en: str | None = None):
        super().__init__(fr)
        self.fr, self.en = fr, en or fr


# ---------------------------------------------------------------------------
# Lecture avec positions (pour modifier le texte sur place)
# ---------------------------------------------------------------------------
class Noeud:
    __slots__ = ("type", "debut", "fin", "valeur", "membres")

    def __init__(self, type_, debut):
        self.type, self.debut, self.fin, self.valeur, self.membres = type_, debut, debut, None, None

    def membre(self, cle):
        """(début de la clé, nœud) du membre ``cle`` d'un objet, ou None."""
        for k, d, n in self.membres or ():
            if k == cle:
                return d, n
        return None

    def get(self, cle):
        m = self.membre(cle) if self.type == "obj" else None
        return m[1] if m else None


class _Lecteur:
    """JSON strict comme ``mod_json.h`` (blancs : espace, \\n, \\r, \\t ; clés en double refusées)."""

    def __init__(self, s: str):
        self.s, self.p = s, 0

    def erreur(self, quoi):
        raise ErreurProfils(f"profiles.json illisible ({quoi}, octet {self.p})",
                            f"unreadable profiles.json ({quoi}, byte {self.p})")

    def ws(self):
        while self.p < len(self.s) and self.s[self.p] in " \n\r\t":
            self.p += 1

    def prendre(self, c):
        self.ws()
        if self.p < len(self.s) and self.s[self.p] == c:
            self.p += 1
            return True
        return False

    def chaine(self) -> str:
        self.ws()
        if self.p >= len(self.s) or self.s[self.p] != '"':
            self.erreur("chaîne attendue")
        debut = self.p
        self.p += 1
        while self.p < len(self.s):
            c = self.s[self.p]
            if c == '"':
                self.p += 1
                try:
                    return json.loads(self.s[debut:self.p])
                except ValueError:
                    self.erreur("chaîne")
            if ord(c) < 32:
                self.erreur("caractère de contrôle")
            self.p += 2 if c == "\\" else 1
        self.erreur("chaîne non terminée")

    def valeur(self, prof: int) -> Noeud:
        if prof > PROFONDEUR_MAX:
            self.erreur("trop profond")
        self.ws()
        if self.p >= len(self.s):
            self.erreur("fin inattendue")
        n = Noeud("?", self.p)
        c = self.s[self.p]
        if c == '"':
            n.type, n.valeur = "str", self.chaine()
        elif c == "{":
            self.p += 1
            n.type, n.membres = "obj", []
            if not self.prendre("}"):
                while True:
                    self.ws()
                    d = self.p
                    k = self.chaine()
                    if any(m[0] == k for m in n.membres):
                        self.erreur("clé en double")
                    if not self.prendre(":"):
                        self.erreur("« : » attendu")
                    n.membres.append((k, d, self.valeur(prof + 1)))
                    if self.prendre("}"):
                        break
                    if not self.prendre(","):
                        self.erreur("« , » attendue")
        elif c == "[":
            self.p += 1
            n.type, n.valeur = "arr", []
            if not self.prendre("]"):
                while True:
                    n.valeur.append(self.valeur(prof + 1))
                    if self.prendre("]"):
                        break
                    if not self.prendre(","):
                        self.erreur("« , » attendue")
        else:
            for lit, v in (("null", None), ("true", True), ("false", False)):
                if self.s.startswith(lit, self.p):
                    self.p += len(lit)
                    n.type, n.valeur = ("null" if v is None else "bool"), v
                    break
            else:
                m = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?").match(self.s, self.p)
                if not m or m.end() == self.p:
                    self.erreur("valeur attendue")
                self.p = m.end()
                n.type, n.valeur = "num", float(m.group(0))
        n.fin = self.p
        return n


def analyser(texte: str) -> Noeud:
    lu = _Lecteur(texte)
    racine = lu.valeur(0)
    lu.ws()
    if lu.p != len(texte):
        lu.erreur("texte après le JSON")
    return racine


def valider(racine: Noeud) -> str:
    """Ce que le portage exige au démarrage, plus la forme des entrées que nous touchons. Renvoie le profil actif."""
    def non(fr, en):
        raise ErreurProfils(f"profiles.json d'un format inconnu ({fr}) : rien n'est changé",
                            f"profiles.json in an unknown format ({en}): nothing is changed")
    if racine.type != "obj":
        non("pas un objet", "not an object")
    fv = racine.get("format_version")
    if fv is None or fv.type != "num" or fv.valeur != FORMAT:
        non("format_version différent de 1", "format_version is not 1")
    pr = racine.get("profiles")
    if pr is None or pr.type != "obj" or not pr.membres:
        non("pas de profils", "no profiles")
    act = racine.get("active")
    if act is None or act.type != "str" or pr.get(act.valeur) is None:
        non("profil actif inconnu", "unknown active profile")
    for nom, _d, p in pr.membres:
        if p.type != "obj":
            non(f"profil {nom[:20]!r} pas un objet", f"profile {nom[:20]!r} not an object")
        for cle in ("enabled", "enabled_since", "config", "setup_pending"):
            v = p.get(cle)
            if v is not None and v.type not in ("obj", "null"):
                non(f"{cle} pas un objet", f"{cle} not an object")
        v = p.get("enable_serial")
        if v is not None and v.type not in ("num", "null"):
            non("enable_serial pas un nombre", "enable_serial not a number")
    for cle in GLOBAL:
        v = racine.get(cle)
        if v is not None and v.type not in ("obj", "null"):
            non(f"{cle} pas un objet", f"{cle} not an object")
    return act.valeur


# ---------------------------------------------------------------------------
# Modifications sur place (le reste du texte ne bouge pas d'un octet)
# ---------------------------------------------------------------------------
def _cle_tri(k: str) -> bytes:
    return k.encode("utf-8")              # ordre de std::map<std::string> : celui des octets


def _poser(texte: str, chemin: tuple, valeur_json: str) -> str:
    """``chemin`` = clés depuis la racine ; la dernière reçoit ``valeur_json``. Les objets intermédiaires absents
    (ou ``null``) sont créés. Une clé ajoutée va à sa place dans l'ordre trié (comme l'écrit le jeu)."""
    n = analyser(texte)
    for i, cle in enumerate(chemin):
        if n.type == "null":                              # objet absent noté null : remplacé par l'objet
            reste = valeur_json
            for k in reversed(chemin[i:]):
                reste = "{" + json.dumps(k) + ":" + reste + "}"
            return texte[:n.debut] + reste + texte[n.fin:]
        m = n.membre(cle)
        if m is not None and i < len(chemin) - 1:
            n = m[1]
            continue
        if m is not None:                                 # remplacer la valeur
            v = m[1]
            return texte[:v.debut] + valeur_json + texte[v.fin:]
        reste = valeur_json
        for k in reversed(chemin[i + 1:]):
            reste = "{" + json.dumps(k) + ":" + reste + "}"
        membre = json.dumps(cle) + ":" + reste
        if not n.membres:
            return texte[:n.debut] + "{" + membre + "}" + texte[n.fin:]
        apres = [x for x in n.membres if _cle_tri(x[0]) > _cle_tri(cle)]
        if apres:                                         # avant le premier membre qui le suit
            d = apres[0][1]
            return texte[:d] + membre + "," + texte[d:]
        f = n.membres[-1][2].fin                          # après le dernier
        return texte[:f] + "," + membre + texte[f:]
    raise ErreurProfils("chemin vide")


def _effacer(texte: str, chemin: tuple) -> str:
    n = analyser(texte)
    for cle in chemin[:-1]:
        n = n.get(cle) if n.type == "obj" else None
        if n is None or n.type != "obj":
            return texte
    if n.type != "obj":
        return texte
    idx = next((i for i, m in enumerate(n.membres) if m[0] == chemin[-1]), None)
    if idx is None:
        return texte
    _k, d, v = n.membres[idx]
    if idx + 1 < len(n.membres):
        return texte[:d] + texte[n.membres[idx + 1][1]:]
    if idx > 0:
        return texte[:n.membres[idx - 1][2].fin] + texte[v.fin:]
    return texte[:n.debut] + "{}" + texte[n.fin:]


# ---------------------------------------------------------------------------
# Ce que le lanceur fait
# ---------------------------------------------------------------------------
def chemin(config: str) -> str:
    return os.path.join(config, "ModManager", NOM)


def version_connue(version_t) -> bool:
    return bool(version_t) and tuple(version_t[:2]) in VERSIONS


def lire_texte(p: str) -> str:
    try:
        if os.path.getsize(p) > TAILLE_MAX:
            raise ErreurProfils("profiles.json trop gros pour le portage : rien n'est changé",
                                "profiles.json too large for the port: nothing is changed")
        with open(p, "rb") as f:
            brut = f.read()
    except FileNotFoundError:
        raise ErreurProfils("le portage n'a pas encore de profiles.json : lance le jeu une fois (il le crée), "
                            "ferme-le, puis recommence", "the port has no profiles.json yet: start the game once (it "
                            "creates it), close it, then try again") from None
    except OSError as e:
        raise ErreurProfils(f"profiles.json illisible ({e.__class__.__name__})",
                            f"unreadable profiles.json ({e.__class__.__name__})") from None
    try:
        return brut.decode("utf-8")
    except UnicodeDecodeError:
        raise ErreurProfils("profiles.json illisible (pas en UTF-8)", "unreadable profiles.json (not UTF-8)") from None


def etat(texte: str) -> dict:
    """{actif: nom du profil actif, enabled: {id: bool} (profil actif), trust: {id: empreinte}} de nos mods."""
    r = analyser(texte)
    actif = valider(r)
    p = r.get("profiles").get(actif)
    en = p.get("enabled")
    tr = r.get("native_trust")
    out = {"actif": actif, "enabled": {}, "trust": {}}
    for k, _d, v in (en.membres if en is not None and en.type == "obj" else ()):
        if k.startswith(PREFIXE):
            out["enabled"][k] = v.valeur is True
    for k, _d, v in (tr.membres if tr is not None and tr.type == "obj" else ()):
        if k.startswith(PREFIXE) and v.type == "str":
            out["trust"][k] = v.valeur
    return out


def allumes(texte: str) -> set[str]:
    """Tous les mods allumés (``true``) dans le profil actif (les nôtres et les autres). Lecture seule."""
    r = analyser(texte)
    p = r.get("profiles").get(valider(r))
    en = p.get("enabled")
    return {k for k, _d, v in (en.membres if en is not None and en.type == "obj" else ()) if v.valeur is True}


def modifier(texte: str, allumer: dict, effacer: tuple = ()) -> str:
    """Nouveau texte : ``allumer`` = {id: empreinte de confiance, ou None (mod sans code : rien à confirmer)} allumés
    dans le profil ACTIF (``enabled_since`` / ``enable_serial`` comme ``enable()`` pour un mod qui s'allume) ;
    ``effacer`` = ids dont le dossier a disparu (comme ``remove_package``). Seulement des ids ``wwmp-*`` et des
    empreintes exactes (64 chiffres hexadécimaux). Lève ErreurProfils (format inconnu)."""
    for i in list(allumer) + list(effacer):
        if not ID_RE.match(i):
            raise ErreurProfils(f"identifiant refusé {i!r} : seulement nos mods (wwmp-…)")
    for i, e in allumer.items():
        if e is not None and not (isinstance(e, str) and HEX64.match(e)):
            raise ErreurProfils(f"empreinte refusée pour {i}")
    if set(allumer) & set(effacer):
        raise ErreurProfils("un mod ne peut pas être allumé et retiré à la fois")
    r = analyser(texte)
    actif = valider(r)
    t = texte
    for ident in effacer:
        for nom, _d, _p in analyser(t).get("profiles").membres:
            for cle in PAR_PROFIL:
                t = _effacer(t, ("profiles", nom, cle, ident))
        for cle in GLOBAL:
            t = _effacer(t, (cle, ident))
    for ident in sorted(allumer):
        p = analyser(t).get("profiles").get(actif)
        en = p.get("enabled")
        deja = en is not None and en.type == "obj" and en.get(ident) is not None and en.get(ident).valeur is True
        if not deja:
            s = p.get("enable_serial")
            serie = int(s.valeur) + 1 if s is not None and s.type == "num" and float(s.valeur).is_integer() \
                and s.valeur >= 0 else 1
            t = _poser(t, ("profiles", actif, "enabled_since", ident), str(serie))
            t = _poser(t, ("profiles", actif, "enable_serial"), str(serie))
            t = _poser(t, ("profiles", actif, "enabled", ident), "true")
        if allumer[ident] is not None:
            t = _poser(t, ("native_trust", ident), json.dumps(allumer[ident]))
    _verifier(texte, t, allumer, effacer, actif)
    return t


def _sans_nous(n: Noeud, chemin=()):
    """Valeur Python de ``n`` sans nos entrées (et sans les compteurs qu'``enable()`` change avec elles)."""
    if n.type == "obj":
        out = {}
        for k, _d, v in n.membres:
            if k.startswith(PREFIXE) and chemin and (chemin[-1] in PAR_PROFIL + GLOBAL + ("enabled_since",)):
                continue
            if len(chemin) == 2 and chemin[0] == "profiles" and k == "enable_serial":
                continue
            val = _sans_nous(v, chemin + (k,))
            if k in PAR_PROFIL + GLOBAL + ("enabled_since",) and val in ({}, ("null", None)):
                continue                       # conteneur vide (ou créé pour nos seules entrées)
            out[k] = val
        return out
    if n.type == "arr":
        return [_sans_nous(x, chemin) for x in n.valeur]
    return (n.type, n.valeur)


def _verifier(avant: str, apres: str, allumer: dict, effacer, actif: str) -> None:
    """Contrôle avant d'écrire : le nouveau texte se relit (règles du portage), nos entrées sont celles voulues,
    tout le reste a les mêmes valeurs qu'avant (le texte hors de nos clés est identique par construction)."""
    a, b = analyser(avant), analyser(apres)
    if valider(b) != actif or _sans_nous(a) != _sans_nous(b):
        raise ErreurProfils("modification de profiles.json ratée (contrôle) : rien n'est changé",
                            "profiles.json change failed (check): nothing is changed")
    e = etat(apres)
    for i, emp in allumer.items():
        if e["enabled"].get(i) is not True or (emp is not None and e["trust"].get(i) != emp):
            raise ErreurProfils("modification de profiles.json ratée (contrôle) : rien n'est changé",
                                "profiles.json change failed (check): nothing is changed")
    for i in effacer:
        if i in e["trust"] or i in e["enabled"]:
            raise ErreurProfils("modification de profiles.json ratée (contrôle) : rien n'est changé",
                                "profiles.json change failed (check): nothing is changed")


def _copie_securite(p: str, brut: bytes, base: str) -> str:
    os.makedirs(base, exist_ok=True)
    nom = f"profiles-{time.strftime('%Y%m%d-%H%M%S')}"
    dst, k = os.path.join(base, nom + ".json"), 1
    while os.path.exists(dst):
        k += 1
        dst = os.path.join(base, f"{nom}-{k}.json")
    tmp = dst + ".partiel"
    with open(tmp, "wb") as f:
        f.write(brut)
        f.flush()
        os.fsync(f.fileno())
    with open(tmp, "rb") as f:
        if f.read() != brut:
            raise ErreurProfils("copie de sécurité de profiles.json ratée : rien n'est changé",
                                "backup of profiles.json failed: nothing is changed")
    os.replace(tmp, dst)
    copies = sorted((n for n in os.listdir(base) if n.startswith("profiles-") and n.endswith(".json")), reverse=True)
    for n in copies[GARDER:]:
        try:
            os.remove(os.path.join(base, n))
        except OSError:
            pass
    return dst


def ecrire(config: str, allumer: dict, effacer: tuple = (), base_copies: str = "", jeu_tourne=None) -> dict:
    """Applique ``modifier`` au profiles.json du portage dont ``config`` est le dossier des réglages. Jeu FERMÉ
    (``jeu_tourne`` vérifié avant de lire et juste avant de remplacer). Rien à changer : rien n'est écrit. Sinon
    copie de sécurité dans ``base_copies`` (obligatoire), fichier temporaire dans le même dossier, relu, puis
    remplacement atomique. Renvoie {ecrit, copie, chemin}. Lève ErreurProfils (rien n'a été changé)."""
    def ouvert():
        if jeu_tourne is not None and jeu_tourne():
            raise ErreurProfils("le jeu (portage natif) est ouvert : ferme-le d'abord (il réécrit profiles.json)",
                                "the game (native port) is open: close it first (it rewrites profiles.json)")
    if not base_copies:
        raise ErreurProfils("pas de dossier pour la copie de sécurité de profiles.json")
    ouvert()
    p = chemin(config)
    if os.path.islink(p):
        raise ErreurProfils("profiles.json est un lien : rien n'est changé", "profiles.json is a link: nothing is "
                            "changed")
    texte = lire_texte(p)
    brut = texte.encode("utf-8")
    neuf = modifier(texte, allumer, effacer)
    if neuf == texte:
        return {"ecrit": False, "copie": "", "chemin": p}
    data = neuf.encode("utf-8")
    if len(data) > TAILLE_MAX:
        raise ErreurProfils("profiles.json deviendrait trop gros pour le portage : rien n'est changé",
                            "profiles.json would become too large for the port: nothing is changed")
    copie = _copie_securite(p, brut, base_copies)
    tmp = os.path.join(os.path.dirname(p), ".profiles.json.wwmp.tmp")   # pas « profiles.json.tmp » (celui du jeu)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, "rb") as f:
            if f.read() != data:
                raise ErreurProfils("écriture de profiles.json ratée (disque plein ?) : rien n'est changé",
                                    "writing profiles.json failed (disk full?): nothing is changed")
        ouvert()
        with open(p, "rb") as f:                      # changé entre-temps (le jeu…) : on ne l'écrase pas
            if f.read() != brut:
                raise ErreurProfils("profiles.json a changé pendant la préparation : recommence",
                                    "profiles.json changed during the preparation: try again")
        os.replace(tmp, p)
    except OSError as e:
        raise ErreurProfils(f"écriture de profiles.json impossible ({e.__class__.__name__}) : rien n'est changé",
                            f"cannot write profiles.json ({e.__class__.__name__}): nothing is changed") from None
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {"ecrit": True, "copie": copie, "chemin": p}
