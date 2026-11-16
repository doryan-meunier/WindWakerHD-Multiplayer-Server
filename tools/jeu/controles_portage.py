"""Touches de Link du portage natif (ZeldaWWHDRecomp v0.5.1) : le fichier ``controls.json`` que règle le menu du jeu
F1 > Controls (onglet « Commandes » du lanceur, sous-onglet « Link »).

Où : ``<dossier des réglages du portage>/controls.json`` (``host::config_dir`` : ``%APPDATA%\\WWHD``, ou
``<release>/user`` en mode portable, ``$XDG_CONFIG_HOME/wwhd`` sous Linux ; ``jeu/portage.lire`` le donne dans
``config``) ; la variable ``WWHD_CONTROLS`` le remplace (``input_map::default_path``).

Format 2 (``runtime/src/input_map.cpp`` ``to_json`` / ``from_json``), JSON :
* ``"version": 2`` ;
* ``"keyboard"`` : {action: [touche, touche]} (deux touches au plus ; noms ``key_id`` : « K », « Space »,
  « LeftShift », « F1 »… ; souris « Mouse1 »…, « WheelUp ») ;
* ``"controller"`` : {action: bouton | null} (noms ``pad_id`` : « A » = bouton du BAS d'une manette Xbox,
  « LeftShoulder » = LB, « LeftTrigger » = LT…) — les réglages COMMUNS aux manettes sans profil ;
* ``"controllers"`` : [{"guid", "name", "controller": {...}, "pad_preset"}] : une manette qui a son profil (même GUID
  SDL et nom, sinon même GUID, sinon même nom) joue avec SES boutons, pas ceux de « controller » ;
* ``"options"``, ``"pad_preset"``, ``"shortcuts"``… : jamais touchés ici (gardés tels quels).
Actions (``kActions``) : A B X Y L R ZL ZR Plus Minus Home, la croix, les clics des sticks, les 8 directions des deux
sticks, Screenshot (raccourci, réglé par ``shortcuts``), Jump et MoonJump (mods « saut partout » / triche).

Le jeu lit ce fichier AU DÉMARRAGE seulement (``input_map::load_startup``) ; il ne le relit jamais et le RÉÉCRIT en
entier depuis sa mémoire à chaque réglage changé dans F1 (``set_current``). Écrire pendant que le jeu tourne ne
servirait donc à rien et serait écrasé : le lanceur n'écrit que jeu FERMÉ (les changements faits jeu ouvert attendent
sa fermeture), après une copie de l'ancien fichier, en ne changeant QUE les actions modifiées. Un fichier d'un autre
format est refusé (rien écrit).
"""
from __future__ import annotations

import json
import os
import time

NOM = "controls.json"
ENV = "WWHD_CONTROLS"
GARDER = 10

# (id, libellé FR, libellé EN) dans l'ordre de la fenêtre Controls du jeu ; Screenshot exclu (raccourci)
ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("A", "A (action, parler, rouler)", "A (action, talk, roll)"),
    ("B", "B (épée)", "B (sword)"),
    ("X", "X (objet)", "X (item)"),
    ("Y", "Y (objet)", "Y (item)"),
    ("L", "L (page du GamePad)", "L (GamePad page)"),
    ("R", "R (objet)", "R (item)"),
    ("ZL", "ZL (cibler)", "ZL (target)"),
    ("ZR", "ZR (bouclier, s'accroupir)", "ZR (shield, crouch)"),
    ("Plus", "+ (pause)", "+ (pause)"),
    ("Minus", "− (carte)", "− (map)"),
    ("Home", "Home", "Home"),
    ("DpadUp", "Croix ↑", "D-pad ↑"),
    ("DpadDown", "Croix ↓", "D-pad ↓"),
    ("DpadLeft", "Croix ←", "D-pad ←"),
    ("DpadRight", "Croix →", "D-pad →"),
    ("LeftStickClick", "Clic du stick gauche", "Left stick click"),
    ("RightStickClick", "Clic du stick droit", "Right stick click"),
    ("LeftStickUp", "Stick gauche ↑ (avancer)", "Left stick ↑ (move)"),
    ("LeftStickDown", "Stick gauche ↓ (reculer)", "Left stick ↓ (move)"),
    ("LeftStickLeft", "Stick gauche ← (gauche)", "Left stick ← (move)"),
    ("LeftStickRight", "Stick gauche → (droite)", "Left stick → (move)"),
    ("RightStickUp", "Stick droit ↑ (caméra)", "Right stick ↑ (camera)"),
    ("RightStickDown", "Stick droit ↓ (caméra)", "Right stick ↓ (camera)"),
    ("RightStickLeft", "Stick droit ← (caméra)", "Right stick ← (camera)"),
    ("RightStickRight", "Stick droit → (caméra)", "Right stick → (camera)"),
    ("Jump", "Saut partout (mod du portage)", "Jump anywhere (port mod)"),
    ("MoonJump", "Saut lunaire (triche)", "Moon jump (cheat)"),
)
IDS = tuple(a[0] for a in ACTIONS)
FACES = ("A", "B", "X", "Y")
VIRTUELLES = ("Jump", "MoonJump")
LIBRES_EN_JEU = ("L", "DpadDown")          # input_map::free_in_play

# Mapping::defaults() : clavier (deux touches au plus), manette (boutons de face « par position » : le bouton du bas
# d'une manette Xbox est le B de la Wii U)
DEFAUT_CLAVIER: dict[str, list[str]] = {
    "A": ["K", "Space"], "B": ["J"], "X": ["L"], "Y": ["I"], "L": ["Q"], "R": ["E"], "ZL": ["LeftShift"],
    "ZR": ["C"], "Plus": ["Return"], "Minus": ["Tab"], "Home": ["H"], "DpadUp": ["1"], "DpadDown": ["2"],
    "DpadLeft": ["3"], "DpadRight": ["4"], "LeftStickClick": ["X"], "RightStickClick": ["V"],
    "LeftStickUp": ["W"], "LeftStickDown": ["S"], "LeftStickLeft": ["A"], "LeftStickRight": ["D"],
    "RightStickUp": ["Up"], "RightStickDown": ["Down"], "RightStickLeft": ["Left"], "RightStickRight": ["Right"],
    "Jump": ["Q"], "MoonJump": ["Q"],
}
DEFAUT_MANETTE: dict[str, str | None] = {
    "A": "B", "B": "A", "X": "Y", "Y": "X", "L": "LeftShoulder", "R": "RightShoulder", "ZL": "LeftTrigger",
    "ZR": "RightTrigger", "Plus": "Menu", "Minus": "Options", "Home": "Home", "DpadUp": "DpadUp",
    "DpadDown": "DpadDown", "DpadLeft": "DpadLeft", "DpadRight": "DpadRight", "LeftStickClick": "LeftStickButton",
    "RightStickClick": "RightStickButton", "LeftStickUp": "LeftStickUp", "LeftStickDown": "LeftStickDown",
    "LeftStickLeft": "LeftStickLeft", "LeftStickRight": "LeftStickRight", "RightStickUp": "RightStickUp",
    "RightStickDown": "RightStickDown", "RightStickLeft": "RightStickLeft", "RightStickRight": "RightStickRight",
    "Jump": "LeftShoulder", "MoonJump": "LeftShoulder",
}
# boutons des manettes reconnues (kPads : SDL GameController, nommés par la position Xbox)
BOUTONS: tuple[tuple[str, str], ...] = (
    ("A", "A / Croix (bas)"), ("B", "B / Rond (droite)"), ("X", "X / Carré (gauche)"), ("Y", "Y / Triangle (haut)"),
    ("LeftShoulder", "LB / L1"), ("RightShoulder", "RB / R1"), ("LeftTrigger", "LT / L2"),
    ("RightTrigger", "RT / R2"), ("Menu", "Menu / Options"), ("Options", "Vue / Share"), ("Home", "Home / Guide"),
    ("LeftStickButton", "Clic du stick gauche"), ("RightStickButton", "Clic du stick droit"),
    ("DpadUp", "Croix ↑"), ("DpadDown", "Croix ↓"), ("DpadLeft", "Croix ←"), ("DpadRight", "Croix →"),
    ("LeftStickUp", "Stick gauche ↑"), ("LeftStickDown", "Stick gauche ↓"), ("LeftStickLeft", "Stick gauche ←"),
    ("LeftStickRight", "Stick gauche →"), ("RightStickUp", "Stick droit ↑"), ("RightStickDown", "Stick droit ↓"),
    ("RightStickLeft", "Stick droit ←"), ("RightStickRight", "Stick droit →"), ("Capture", "Capture / Share"),
    ("RightPaddle1", "Palette droite 1"), ("LeftPaddle1", "Palette gauche 1"), ("RightPaddle2", "Palette droite 2"),
    ("LeftPaddle2", "Palette gauche 2"), ("Touchpad", "Clic du pavé tactile"), ("Misc2", "Bouton en plus 2"),
    ("Misc3", "Bouton en plus 3"), ("Misc4", "Bouton en plus 4"), ("Misc5", "Bouton en plus 5"),
    ("Misc6", "Bouton en plus 6"), ("GameCubeL", "GC L (25 % / clic)"), ("GameCubeR", "GC R (25 % / clic)"),
    ("StartUp", "Start + croix ↑"), ("StartDown", "Start + croix ↓"), ("StartLeft", "Start + croix ←"),
    ("StartRight", "Start + croix →"),
)
IDS_BOUTONS = frozenset(b[0] for b in BOUTONS)
# touches (kKeys + souris) ; les codes « Key93 » (touche sans nom) sont acceptés tels quels
TOUCHES = frozenset(
    [chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(i) for i in range(10)]
    + ["Minus", "Equal", "LeftBracket", "RightBracket", "Backslash", "Semicolon", "Quote", "Comma", "Period",
       "Slash", "Grave", "Section", "Space", "Return", "Tab", "Backspace", "ForwardDelete", "Escape", "LeftShift",
       "RightShift", "LeftControl", "RightControl", "LeftOption", "RightOption", "CapsLock", "Up", "Down", "Left",
       "Right", "HomeKey", "End", "PageUp", "PageDown"]
    + [f"F{i}" for i in range(1, 17)] + [f"Keypad{i}" for i in range(10)]
    + ["KeypadDecimal", "KeypadMultiply", "KeypadPlus", "KeypadMinus", "KeypadDivide", "KeypadEnter",
       "KeypadEquals", "KeypadClear", "Mouse1", "Mouse2", "Mouse3", "Mouse4", "Mouse5", "WheelUp", "WheelDown"])
# input_map::reserved_key : refusées par le jeu pour une action (il les retire au chargement)
RESERVEES = {"9": "Graphismes › enregistrer l'activité du son", "P": "Graphismes › capture d'image",
             "F12": "Graphismes › capture d'image"}
# raccourcis du portage par défaut sans Ctrl / Alt (kShortcuts) : partagent leur touche avec une action de Link
RACCOURCIS_SIMPLES = {"Escape": "Réglages", "F1": "Réglages", "F11": "Plein écran", "F10": "Capture d'écran",
                      "F6": "Charger l'état 1", "F2": "Charger l'état 2", "F3": "Charger l'état 3",
                      "F4": "Charger l'état 4", "F5": "Charger l'état 5", "R": "Résolution interne",
                      "O": "Occlusion ambiante", "M": "Occlusion pleine taille", "N": "Filtrage anisotrope",
                      "6": "Interpolation d'images", "8": "FXAA"}


class ErreurControles(Exception):
    pass


def touche_ok(t) -> bool:
    if not isinstance(t, str):
        return False
    if t in TOUCHES:
        return True
    return t.startswith("Key") and t[3:].isdigit() and 0 <= int(t[3:]) <= 255


def chemin(inst: dict, env=None) -> str:
    """``controls.json`` du portage ``inst`` (``portage.lire``) ; "" sans dossier de réglages."""
    e = os.environ if env is None else env
    if e.get(ENV):
        return e[ENV]
    cfg = (inst or {}).get("config") or ""
    return os.path.join(cfg, NOM) if cfg else ""


def defauts() -> dict:
    return {"clavier": {a: list(v) for a, v in DEFAUT_CLAVIER.items()}, "manette": dict(DEFAUT_MANETTE)}


def _lire_brut(p: str):
    """(racine JSON, texte) ; (None, None) sans fichier. Lève ErreurControles si illisible."""
    try:
        with open(p, "rb") as f:
            brut = f.read(1 << 20)
    except FileNotFoundError:
        return None, None
    except OSError as e:
        raise ErreurControles(f"{NOM} illisible ({e.__class__.__name__})") from None
    try:
        return json.loads(brut.decode("utf-8")), brut
    except (UnicodeDecodeError, ValueError):
        raise ErreurControles(f"{NOM} : JSON illisible (rien n'est changé)") from None


def verifier_format(racine) -> str:
    """"" si le fichier est au format 2 que le lanceur sait modifier ; sinon la raison du refus."""
    if not isinstance(racine, dict):
        return "pas un objet JSON"
    v = racine.get("version")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != 2:
        return (f"format {v!r} inconnu (le lanceur sait modifier le format 2) : change une touche une fois dans le jeu "
                "(F1 > Controls), il le réécrit au format 2")
    for k, genre in (("keyboard", dict), ("controller", dict)):
        if k in racine and not isinstance(racine[k], genre):
            return f"« {k} » n'est pas un objet"
    if "controllers" in racine and not isinstance(racine["controllers"], list):
        return "« controllers » n'est pas une liste"
    return ""


def _clavier_de(racine: dict) -> dict:
    out = defauts()["clavier"]
    for a, v in (racine.get("keyboard") or {}).items():
        if a not in DEFAUT_CLAVIER:
            continue
        lst = v if isinstance(v, list) else [v] if isinstance(v, str) else []
        out[a] = [t for t in lst if isinstance(t, str)][:2]
    return out


def _manette_de(obj: dict, base: dict) -> dict:
    out = dict(base)
    for a, v in (obj or {}).items():
        if a not in DEFAUT_MANETTE:
            continue
        if v is None:
            if a not in FACES:
                out[a] = None
        elif isinstance(v, str):
            out[a] = v
    return out


def _raccourcis(racine: dict) -> list[dict]:
    """Raccourcis du portage (« shortcuts ») : [{"id", "touches": ["Shift+F1", ...], "bouton"}] ; défaut sans eux."""
    out = []
    sc = racine.get("shortcuts") if isinstance(racine, dict) else None
    if not isinstance(sc, dict):
        return out
    for ident, v in sc.items():
        if not isinstance(v, dict):
            continue
        touches = []
        for k in v.get("keys") or []:
            if isinstance(k, dict) and isinstance(k.get("key"), str):
                m = k.get("modifiers") if isinstance(k.get("modifiers"), int) else 0
                touches.append("+".join([n for b, n in ((2, "Ctrl"), (1, "Shift"), (4, "Alt"), (8, "Cmd")) if m & b]
                                        + [k["key"]]))
        out.append({"id": str(ident), "touches": touches,
                    "bouton": v.get("button") if isinstance(v.get("button"), str) else "",
                    "tenir": v.get("hold") if isinstance(v.get("hold"), str) else ""})
    return out


def lire(p: str) -> dict:
    """État du fichier : {"ok", "chemin", "existe", "erreur", "clavier", "manette", "profils", "raccourcis",
    "face_auto"}. Sans fichier : les défauts du jeu (``existe`` False). Format inconnu : ``ok`` False."""
    out = {"ok": False, "chemin": p, "existe": False, "erreur": "", "clavier": defauts()["clavier"],
           "manette": dict(DEFAUT_MANETTE), "profils": [], "raccourcis": [], "face_auto": False}
    if not p:
        out["erreur"] = "dossier des réglages du portage inconnu (choisis le portage dans Réglages)"
        return out
    try:
        racine, _brut = _lire_brut(p)
    except ErreurControles as e:
        out["existe"], out["erreur"] = True, str(e)
        return out
    if racine is None:
        out["ok"] = True
        return out
    out["existe"] = True
    raison = verifier_format(racine)
    if raison:
        out["erreur"] = raison
        return out
    out["clavier"] = _clavier_de(racine)
    out["manette"] = _manette_de(racine.get("controller"), DEFAUT_MANETTE)
    for i, c in enumerate(racine.get("controllers") or []):
        if isinstance(c, dict) and isinstance(c.get("guid"), str) and isinstance(c.get("name"), str):
            out["profils"].append({"index": i, "guid": c["guid"], "nom": c["name"],
                                   "manette": _manette_de(c.get("controller"), out["manette"])})
    opts = racine.get("options") if isinstance(racine.get("options"), dict) else {}
    out["face_auto"] = opts.get("face_layout") == "auto"
    out["raccourcis"] = _raccourcis(racine)
    out["ok"] = True
    return out


def verifier_changements(ch: dict) -> dict:
    """Changements demandés nettoyés : {"profil": -1 | index, "clavier": {action: [t, t]}, "manette": {action: b|None}}.
    Lève ErreurControles sur une valeur inconnue (rien écrit)."""
    if not isinstance(ch, dict):
        raise ErreurControles("changements illisibles")
    prof = ch.get("profil", -1)
    if isinstance(prof, bool) or not isinstance(prof, int) or prof < -1:
        raise ErreurControles("profil de manette inconnu")
    cl, ma = {}, {}
    for a, v in (ch.get("clavier") or {}).items():
        if a not in DEFAUT_CLAVIER or not isinstance(v, list) or len(v) > 2:
            raise ErreurControles(f"action {a!r} : touches illisibles")
        lst = [t for t in v if t]
        for t in lst:
            if not touche_ok(t):
                raise ErreurControles(f"touche {t!r} inconnue du portage")
            if t in RESERVEES:
                raise ErreurControles(f"touche {t} réservée par le portage ({RESERVEES[t]}) : il la retirerait")
        if len(set(lst)) != len(lst):
            raise ErreurControles(f"action {a} : deux fois la même touche")
        cl[a] = lst
    for a, v in (ch.get("manette") or {}).items():
        if a not in DEFAUT_MANETTE:
            raise ErreurControles(f"action {a!r} inconnue")
        if v in (None, ""):
            if a in FACES:
                raise ErreurControles(f"{a} doit garder un bouton (le portage remet le défaut sinon)")
            ma[a] = None
        elif isinstance(v, str) and v in IDS_BOUTONS:
            ma[a] = v
        else:
            raise ErreurControles(f"bouton {v!r} inconnu du portage")
    if prof >= 0 and cl:
        raise ErreurControles("un profil de manette n'a que des boutons (le clavier est commun)")
    return {"profil": prof, "clavier": cl, "manette": ma}


def fusionner(attente: dict | None, ch: dict) -> dict:
    """Changements en attente (jeu ouvert) + nouveaux : par profil, la dernière valeur de chaque action gagne."""
    out = {k: dict(v) for k, v in (attente or {}).items()} if isinstance(attente, dict) else {}
    cle = str(ch["profil"])
    e = out.setdefault(cle, {"clavier": {}, "manette": {}})
    e.setdefault("clavier", {}).update(ch["clavier"])
    e.setdefault("manette", {}).update(ch["manette"])
    return out


def _copier_ancien(p: str, brut: bytes, base_copies: str) -> str:
    os.makedirs(base_copies, exist_ok=True)
    nom = f"controls-{time.strftime('%Y%m%d-%H%M%S')}.json"
    dst, k = os.path.join(base_copies, nom), 1
    while os.path.exists(dst):
        k += 1
        dst = os.path.join(base_copies, f"controls-{time.strftime('%Y%m%d-%H%M%S')}-{k}.json")
    with open(dst, "wb") as f:
        f.write(brut)
    anciens = sorted((n for n in os.listdir(base_copies) if n.startswith("controls-")), reverse=True)
    for n in anciens[GARDER:]:
        try:
            os.remove(os.path.join(base_copies, n))
        except OSError:
            pass
    return dst


def ecrire(p: str, changements: dict, base_copies: str, jeu_tourne=None) -> dict:
    """Applique ``changements`` (``verifier_changements``, ou plusieurs profils : {"-1": {...}, "0": {...}}) au fichier
    RELU maintenant, jeu FERMÉ : seules ces actions changent, tout le reste est gardé. Copie de l'ancien fichier dans
    ``base_copies`` (10 gardées), fichier temporaire puis remplacement. Sans fichier : un fichier format 2 minimal
    (le jeu complète tout le reste par ses défauts). Rend {"copie": chemin ou "", "etat": lire(p)}."""
    if not p:
        raise ErreurControles("dossier des réglages du portage inconnu")
    if jeu_tourne is not None and jeu_tourne():
        raise ErreurControles("le jeu est ouvert : il ne relit ce fichier qu'au démarrage et le réécrit lui-même ; "
                              "le changement sera écrit à sa fermeture")
    par_profil = (changements if "profil" not in changements
                  else {str(changements["profil"]): changements})
    nets = {}
    for k, v in par_profil.items():
        v = dict(v, profil=int(k)) if isinstance(v, dict) else v
        nets[int(k)] = verifier_changements(v)
    racine, brut = _lire_brut(p)
    if racine is None:
        racine, brut = {"version": 2, "keyboard": {}, "controller": {}}, None
    raison = verifier_format(racine)
    if raison:
        raise ErreurControles(f"{NOM} : {raison} : rien n'est changé")
    for prof, ch in sorted(nets.items()):
        if prof < 0:
            kb = racine.setdefault("keyboard", {})
            for a, v in ch["clavier"].items():
                kb[a] = list(v)
            pc = racine.setdefault("controller", {})
            for a, v in ch["manette"].items():
                pc[a] = v
            if any(a in FACES for a in ch["manette"]):
                opts = racine.get("options")
                if isinstance(opts, dict) and opts.get("face_layout") == "auto":
                    del opts["face_layout"]        # comme set_pad_binding : un choix à la main n'est plus écrasé
        else:
            profils = racine.get("controllers") or []
            if prof >= len(profils) or not isinstance(profils[prof], dict):
                raise ErreurControles("profil de manette introuvable (fichier changé ?) : rien n'est changé")
            pc = profils[prof].setdefault("controller", {})
            if not isinstance(pc, dict):
                raise ErreurControles("profil de manette illisible : rien n'est changé")
            for a, v in ch["manette"].items():
                pc[a] = v
    texte = json.dumps(racine, ensure_ascii=False, indent=2) + "\n"
    copie = _copier_ancien(p, brut, base_copies) if brut is not None else ""
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".wwmp.tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(texte)
            f.flush()
            os.fsync(f.fileno())
        if jeu_tourne is not None and jeu_tourne():
            raise ErreurControles("le jeu vient de s'ouvrir : rien n'est changé")
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"copie": copie, "etat": lire(p)}


def conflits(clavier: dict, manette: dict, raccourcis: list | None = None) -> list[dict]:
    """Conflits comme la fenêtre du jeu (``has_conflict``, ``shares_ok``) : deux actions sur une même touche ou un
    même bouton, sauf les partages permis (Jump / MoonJump entre eux et avec L ou la croix ↓ ; la croix ↓ avec un
    bouton d'objet) ; une touche de Link prise aussi par un raccourci du portage sans Ctrl / Alt."""
    def partage_ok(a, b):
        va, vb = a in VIRTUELLES, b in VIRTUELLES
        if va and vb:
            return True
        if va or vb:
            return (b if va else a) in LIBRES_EN_JEU
        return (a == "DpadDown") != (b == "DpadDown") and (b if a == "DpadDown" else a) not in LIBRES_EN_JEU
    out = []
    for genre, table in (("clavier", {a: v for a, v in clavier.items()}),
                         ("manette", {a: [v] if v else [] for a, v in manette.items()})):
        par: dict[str, list[str]] = {}
        for a in IDS:
            for v in table.get(a) or []:
                if v and a not in par.get(v, []):
                    par.setdefault(v, []).append(a)
        for v, acts in par.items():
            mauvais = [(x, y) for i, x in enumerate(acts) for y in acts[i + 1:] if not partage_ok(x, y)]
            if mauvais:
                out.append({"genre": genre, "valeur": v, "actions": sorted({z for p in mauvais for z in p},
                                                                             key=IDS.index)})
    simples = dict(RACCOURCIS_SIMPLES)
    if raccourcis:
        simples = {}
        for r in raccourcis:
            for t in r.get("touches") or []:
                if "Ctrl+" not in t and "Alt+" not in t and "Cmd+" not in t:
                    simples[t.replace("Shift+", "")] = r["id"]
    for a in IDS:
        for t in clavier.get(a) or []:
            if t in simples:
                out.append({"genre": "raccourci", "valeur": t, "actions": [a], "avec": simples[t]})
    return out
