"""Pause vivante : le pack « WWMP Pause » (graphicpack/WWMP_Pause), OBLIGATOIRE en coop, empêche le menu de pause
(et, selon ses réglages, la sauvegarde, les options, le Game Over et la carte de la Ballade des vents) de figer le
monde. Ce module ne fait que LIRE sa zone (magie « WWMPPAU1 ») et décrire ce que chaque joueur a coché.

- ``etat_pack(mem)`` : masque des réglages actifs chez ce PC (bit 0 monde vivant, bit 1 décor, bit 2 Link
  vulnérable, bit 3 sauvegarde et options, bit 4 Game Over, bit 5 Ballade des vents), ``ABSENT`` (-1) si le pack
  n'est pas chargé dans Cemu. ``etat_complet(mem)`` : le masque et le format de la zone. Lecture seule.
- Le client l'annonce au serveur (hello, champs ``pause`` et ``pause_fmt``). Menu vivant obligatoire : ``refus``
  dit pourquoi un joueur n'entre pas (pack pas chargé, trop ancien, « Monde pendant le menu » sur « Figé », outils
  qui n'annoncent rien) et ``message_refus`` ce qu'il doit faire (FR/EN). Le client vérifie son propre pack avant de
  se connecter, le serveur refuse le hello, le lanceur le signale avant de lancer (settings.xml de Cemu).
- Les autres réglages (décor, Link, sauvegarde et options, Game Over, Ballade) restent libres ; le serveur, les
  clients et le lanceur préviennent seulement quand la sauvegarde, les options, le Game Over ou la Ballade figent
  le monde chez certains joueurs et pas chez d'autres (``ecrans_figes``).

Pourquoi c'est obligatoire : chacun a son propre jeu. Un joueur dont le menu fige son monde, s'il est l'autorité
d'une salle, fige les monstres de cette salle chez tout le monde. Étude : docs/etudes/pause_vivante.md.
"""
from __future__ import annotations

import struct

MAGIE = b"WWMPPAU1"
FORMAT = 2
FORMATS = (1, 2)                     # 1 : pack v7 (menu seul), 2 : pack v8 (écrans, Link sans commandes)
# zone (graphicpack/WWMP_Pause/patch_pause.asm, pv_zone)
OFF_FORMAT, OFF_MONDE, OFF_DECOR, OFF_LINK = 0x08, 0x0C, 0x10, 0x14
OFF_VIVANTS, OFF_ETAT, OFF_VERDICT, OFF_DIFFERES = 0x18, 0x1C, 0x20, 0x24
OFF_ECRANS, OFF_GAMEOVER, OFF_BALLADE, OFF_SANS_CMD = 0x28, 0x2C, 0x30, 0x34
TAILLE = 0x40
TAILLES = {1: 0x30, 2: 0x40}
BIT_MONDE, BIT_DECOR, BIT_LINK = 1, 2, 4
BIT_ECRANS, BIT_GAMEOVER, BIT_BALLADE = 8, 16, 32
MASQUE_MAX = BIT_MONDE | BIT_DECOR | BIT_LINK | BIT_ECRANS | BIT_GAMEOVER | BIT_BALLADE
# classes d'écran (verdict de pv_vivant, +0x20)
CLASSES = {0: "figé", 1: "menu", 2: "options", 3: "sauvegarde", 4: "Game Over", 5: "Ballade des vents"}
ABSENT = -1
FORMAT_MIN = 2                       # format exigé en coop : pack v8 et plus (le v7 n'a pas les écrans vivants)
VERSION_PACK = 9                     # « # wwmp_pack = N » de graphicpack/WWMP_Pause/rules.txt
VERSION_CEMU_MAX = 7                 # la ligne « version » de rules.txt est le FORMAT de Cemu 2.6 : au-delà, « Unsupported version »
VERSION_MIN = 8                      # pack installé exigé par le lanceur (zone au format 2)
DEFAUT = 61                          # masque des préréglages par défaut (monde, Link, écrans, Game Over, Ballade)
# raisons de refus (champ ``raison`` de l'erreur du serveur, ``pause_refus`` du fichier d'état du client)
R_OUTILS, R_ABSENT, R_FIGE, R_ANCIEN = "outils", "absent", "fige", "ancien"
RAISONS = (R_OUTILS, R_ABSENT, R_FIGE, R_ANCIEN)
# même plage que wwlib.mailbox (codecave + code, hors MEM2)
SEARCH_LO, SEARCH_HI = 0x0000_0000, 0x1000_0000


def trouver(mem) -> int | None:
    """Adresse de la zone (magie alignée sur 4, format connu), None sans le pack. Lecture seule. Portage natif
    (``mem.zone_boite``) : la même zone est dans la boîte du mod C (natif/mod_wwmp, lot 3), cherchée dans ses 0x8000
    octets (jeu/natif.py, plage_zones)."""
    lo, hi = SEARCH_LO, SEARCH_HI
    if isinstance(getattr(mem, "zone_boite", None), tuple):
        from jeu import natif
        plage = natif.plage_zones(mem)
        if plage is None:
            return None
        lo, hi = plage
    for h in mem.find_bytes(MAGIE, lo, hi, limit=8):
        if h % 4 == 0:
            try:
                if struct.unpack(">I", bytes(mem.read(h + OFF_FORMAT, 4)))[0] in FORMATS:
                    return h
            except Exception:
                continue
    return None


def lire(mem, addr: int) -> dict:
    """Réglages et compteurs de la zone (lecture seule). Format 1 (pack v7) : ni écrans, ni Game Over, ni Ballade."""
    fmt = struct.unpack(">I", bytes(mem.read(addr + OFF_FORMAT, 4)))[0]
    n = TAILLES.get(fmt, TAILLES[1])
    mots = struct.unpack(f">{n // 4}I", bytes(mem.read(addr, n))) + (0,) * ((TAILLE - n) // 4)
    return {"format": mots[2], "monde": mots[3] != 0, "decor": mots[4] != 0, "link": mots[5] != 0,
            "link_sauvegarde": mots[5] == 2, "vivants": mots[6], "etat": mots[7], "verdict": mots[8],
            "differes": mots[9], "ecrans": mots[10] != 0, "gameover": mots[11] != 0, "ballade": mots[12] != 0,
            "sans_commandes": mots[13]}


def masque(z: dict | None) -> int:
    """Masque annoncé : ABSENT sans zone ; les autres réglages ne comptent qu'avec le monde vivant (comme dans le
    pack)."""
    if not z:
        return ABSENT
    if not z.get("monde"):
        return 0
    return (BIT_MONDE | (BIT_DECOR if z.get("decor") else 0) | (BIT_LINK if z.get("link") else 0)
            | (BIT_ECRANS if z.get("ecrans") else 0) | (BIT_GAMEOVER if z.get("gameover") else 0)
            | (BIT_BALLADE if z.get("ballade") else 0))


def etat_pack(mem) -> int:
    """Masque des réglages actifs de ce PC, ABSENT si le pack n'est pas chargé (ou illisible)."""
    try:
        a = trouver(mem)
        return masque(lire(mem, a)) if a is not None else ABSENT
    except Exception:
        return ABSENT


def etat_complet(mem) -> tuple[int, int | None]:
    """(masque, format de la zone) de ce PC ; (ABSENT, None) si le pack n'est pas chargé (ou illisible)."""
    try:
        a = trouver(mem)
        if a is None:
            return ABSENT, None
        z = lire(mem, a)
        return masque(z), z["format"]
    except Exception:
        return ABSENT, None


def etat_a(mem, addr: int | None) -> tuple[int, int | None, int | None]:
    """Relecture bon marché pendant la partie : (masque, format, adresse). La zone est relue à ``addr`` (adresse de la
    lecture précédente) si la magie y est encore ; sinon elle est recherchée (``trouver``). (ABSENT, None, None) si
    le pack n'est plus chargé (jeu relancé sans lui) ou illisible."""
    try:
        if addr is not None and bytes(mem.read(addr, len(MAGIE))) == MAGIE:
            a = addr
        else:
            a = trouver(mem)
        if a is None:
            return ABSENT, None, None
        z = lire(mem, a)
        if z["format"] not in FORMATS:
            return ABSENT, None, None
        return masque(z), z["format"], a
    except Exception:
        return ABSENT, None, None


def normaliser_format(v) -> int | None:
    """Format de zone reçu d'un client (hello, ``pause_fmt``) : 1..255, sinon None."""
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 255:
        return None
    return v


def refus(v, fmt) -> str | None:
    """Menu vivant obligatoire : raison du refus d'un joueur qui annonce ``v`` (masque) et ``fmt`` (format de sa
    zone), None s'il peut jouer. Valeur absente ou invalide (outils anciens) : refusé aussi."""
    v = normaliser(v)
    if v is None:
        return R_OUTILS
    if v == ABSENT:
        return R_ABSENT
    if not v & BIT_MONDE:
        return R_FIGE
    fmt = normaliser_format(fmt)
    if fmt is None:
        return R_OUTILS
    if fmt < FORMAT_MIN:
        return R_ANCIEN
    return None


_COCHER_FR = ("dans Cemu, Options › Packs graphiques › The Legend of Zelda: The Wind Waker HD › Mods, coche « WWMP "
              "Pause » avec le préréglage « Vivant » pour « Monde pendant le menu », puis relance le jeu")
_COCHER_EN = ("in Cemu, Options › Graphic packs › The Legend of Zelda: The Wind Waker HD › Mods, tick “WWMP Pause” "
              "with the “Vivant” (live) preset for “Monde pendant le menu” (world during the menu), then restart the "
              "game")
_REINSTALLER_FR = ("réinstalle les packs (le lanceur à jour les recopie ; ou python tools/install_graphicpack.py), "
                   "Cemu fermé")
_REINSTALLER_EN = ("reinstall the packs (an up-to-date launcher copies them; or python tools/install_graphicpack.py) "
                   "with Cemu closed")


def message_refus(raison: str | None, langue: str = "fr") -> str:
    """Phrase complète (ce qu'il faut faire) pour une raison de ``refus`` ; "" si None."""
    en = langue == "en"
    if not raison:
        return ""
    if raison == R_ABSENT:
        if en:
            return ("The “WWMP Pause” pack (live menu) is not loaded in your game, and it is required for co-op: "
                    f"{_COCHER_EN}. If it is not in the list, {_REINSTALLER_EN}.")
        return ("Le pack « WWMP Pause » (menu vivant) n'est pas chargé dans ton jeu ; il est obligatoire en coop : "
                f"{_COCHER_FR}. S'il n'est pas dans la liste, {_REINSTALLER_FR}.")
    if raison == R_FIGE:
        if en:
            return ("Your “WWMP Pause” pack is set to “Fige” (frozen) for “Monde pendant le menu” (world during the "
                    f"menu); the live menu is required for co-op: {_COCHER_EN} (or update the packs: the frozen "
                    "choice no longer exists).")
        return ("Ton pack « WWMP Pause » est réglé sur « Figé » pour « Monde pendant le menu » ; le menu vivant est "
                f"obligatoire en coop : {_COCHER_FR} (ou mets les packs à jour : le choix « Figé » n'existe plus).")
    if raison == R_ANCIEN:
        if en:
            return (f"Your “WWMP Pause” pack is too old (v7) for the live menu required in co-op: {_REINSTALLER_EN}, "
                    f"then {_COCHER_EN}.")
        return (f"Ton pack « WWMP Pause » est trop ancien (v7) pour le menu vivant obligatoire en coop : "
                f"{_REINSTALLER_FR}, puis {_COCHER_FR}.")
    if en:
        return ("Your WWMP tools are too old: they do not report the “WWMP Pause” pack (live menu), which is required "
                f"for co-op. Update them (launcher), then {_COCHER_EN}.")
    return ("Tes outils WWMP sont trop anciens : ils n'annoncent pas le pack « WWMP Pause » (menu vivant), obligatoire "
            f"en coop. Mets-les à jour (lanceur), puis {_COCHER_FR}.")


def normaliser(v) -> int | None:
    """Valeur reçue d'un client (hello) : -1..63, sinon None (inconnu : outils anciens ou valeur invalide)."""
    if isinstance(v, bool) or not isinstance(v, int) or not ABSENT <= v <= MASQUE_MAX:
        return None
    return v


def vivant(v) -> bool | None:
    """Monde vivant pendant le menu ? None si inconnu."""
    v = normaliser(v)
    return None if v is None else bool(v >= 0 and v & BIT_MONDE)


BITS_ECRANS = BIT_ECRANS | BIT_GAMEOVER | BIT_BALLADE


def ecrans_figes(joueurs) -> list[str]:
    """``joueurs`` : [(nom, valeur annoncée)]. Parmi les joueurs qui ONT le monde vivant, noms de ceux dont la
    sauvegarde, les options, le Game Over ou la Ballade figent encore le monde (préréglage « Figé » choisi dans
    Cemu ; un pack v7 est refusé par le serveur) alors qu'un autre joueur de la partie a cet écran vivant. Quand l'un d'eux est l'autorité d'une salle, ces écrans y
    figent les monstres chez tout le monde. Valeurs inconnues ou sans le monde vivant (refusées par le serveur) : ni
    pour ni contre. Ces réglages restent libres : un simple avertissement."""
    vivants = [(n, normaliser(v)) for n, v in joueurs if vivant(v)]
    union = 0
    for _n, v in vivants:
        union |= v & BITS_ECRANS
    return [n for n, v in vivants if v & BITS_ECRANS != union]


def avertissement_ecrans(noms: list[str], langue: str = "fr") -> str:
    """Phrase du lobby / du serveur / du client quand ``ecrans_figes`` n'est pas vide. Seul un préréglage choisi dans
    Cemu peut causer cet écart (un pack trop ancien est refusé) : la phrase cite les noms exacts des préréglages."""
    liste = ", ".join(noms)
    un = len(noms) == 1
    if langue == "en":
        return (f"{liste} {'has' if un else 'have'} the “WWMP Pause” pack, but the save screen, options, Game Over "
                "or Ballad of Gales map still freeze the world there (“Figees (comme le jeu)”, “Fige (comme le "
                "jeu)” or “Figee (comme le jeu)” preset chosen in Cemu): while one of these screens is open, the "
                f"monsters of the rooms they are in charge of freeze for everyone. {PRESETS_VIVANTS_EN}")
    if un:
        return (f"{liste} a le pack « WWMP Pause », mais chez lui la sauvegarde, les options, le Game Over ou la "
                "Ballade des vents figent encore le monde (préréglage « Figé » choisi dans Cemu) : pendant ces "
                "écrans, les monstres des salles dont il est l'autorité se figent chez tout le monde. "
                f"{PRESETS_VIVANTS_FR}")
    return (f"{liste} ont le pack « WWMP Pause », mais chez eux la sauvegarde, les options, le Game Over ou la "
            "Ballade des vents figent encore le monde (préréglage « Figé » choisi dans Cemu) : pendant ces écrans, "
            f"les monstres des salles dont ils sont l'autorité se figent chez tout le monde. {PRESETS_VIVANTS_FR}")


# préréglages « vivants » des écrans (graphicpack/WWMP_Pause/rules.txt, noms exacts affichés par Cemu)
PRESETS_VIVANTS_FR = ("Dans Cemu (Packs graphiques › WWMP Pause), choisissez « Vivantes » pour « Sauvegarde et "
                      "options », « Vivant (Link mort reste fige) » pour « Game Over » et « Carte vivante (Link reste "
                      "fige) » pour « Ballade des vents », puis relancez le jeu.")
PRESETS_VIVANTS_EN = ("In Cemu (Graphic packs › WWMP Pause), pick “Vivantes” for “Sauvegarde et options”, “Vivant "
                      "(Link mort reste fige)” for “Game Over” and “Carte vivante (Link reste fige)” for “Ballade des "
                      "vents”, then restart the game.")


def texte(v, langue: str = "fr") -> str:
    """Description courte d'une valeur annoncée (journaux, lanceur)."""
    v = normaliser(v)
    en = langue == "en"
    if v is None:
        return "unknown" if en else "inconnu"
    if v == ABSENT:
        return "WWMP Pause pack not loaded" if en else "pack WWMP Pause pas chargé"
    if not v & BIT_MONDE:
        return "menu pauses the world" if en else "le menu fige le monde"
    extra = [("scenery" if en else "décor") if v & BIT_DECOR else "",
             ("Link vulnerable" if en else "Link vulnérable") if v & BIT_LINK else "",
             ("save and options" if en else "sauvegarde et options") if v & BIT_ECRANS else "",
             "Game Over" if v & BIT_GAMEOVER else "",
             ("Ballad of Gales map" if en else "Ballade des vents") if v & BIT_BALLADE else ""]
    extra = [e for e in extra if e]
    base = "world keeps running in the menu" if en else "le monde continue pendant le menu"
    return base + (f" (+ {', '.join(extra)})" if extra else "")


# --- lanceur : ce que Cemu chargera (settings.xml et rules.txt installé), avant de lancer -------------------------
CATEGORIE_MONDE = "Monde pendant le menu"
DOSSIER_PACK = "WWMP_Pause"


def version_rules(rules: str) -> int | None:
    """Version du pack d'un rules.txt de WWMP_Pause (None : illisible) : le commentaire « # wwmp_pack = N » (pack v9
    et après) ; sinon la ligne ``version`` de [Definition], qui servait de numéro de pack jusqu'à la v8 (c'est en
    fait le format de Cemu : 7 au plus, au-delà Cemu affiche « Unsupported version » et ne charge pas le pack)."""
    import re
    m = re.search(r"(?m)^\s*#\s*wwmp_pack\s*=\s*(\d+)", rules or "")
    if m:
        return int(m.group(1))
    m = re.search(r"(?m)^\s*version\s*=\s*(\d+)\s*$", rules or "")
    return int(m.group(1)) if m else None


def prereglages_figes(rules: str) -> set[str]:
    """Noms des préréglages de « Monde pendant le menu » qui figent le monde ($monde = 0) dans ce rules.txt (pack
    v8 et avant : « Fige (comme le jeu) » ; v9 : aucun)."""
    out = set()
    for bloc in (rules or "").split("[Preset]")[1:]:
        champs = {}
        for ligne in bloc.splitlines():
            if ligne.strip().startswith("["):
                break
            k, sep, v = ligne.partition("=")
            if sep:
                champs[k.strip()] = v.strip()
        if champs.get("category") == CATEGORIE_MONDE and champs.get("$monde") == "0" and champs.get("name"):
            out.add(champs["name"])
    return out


def etat_cemu(settings: str | None, rules: str | None) -> str | None:
    """Lanceur : raison du refus que le serveur opposera (``refus``), prévue d'après ``settings.xml`` de Cemu et le
    ``rules.txt`` installé de WWMP_Pause (textes, None : illisible ou absent) ; None si le pack sera bon. Pack pas
    installé ou pas coché : ``R_ABSENT`` ; installé trop ancien : ``R_ANCIEN`` ; « Figé » choisi alors que le pack
    installé l'a encore : ``R_FIGE`` (pack v9 : un ancien choix « Figé » n'existe plus, Cemu prend « Vivant »)."""
    import xml.etree.ElementTree as ET
    if rules is None:
        return R_ABSENT
    v = version_rules(rules)
    if v is not None and v < VERSION_MIN:
        return R_ANCIEN
    if not settings:
        return R_ABSENT
    try:
        racine = ET.fromstring(settings)
    except ET.ParseError:
        # settings.xml illisible en XML : repli sur le texte (comme le pack Mailbox)
        return None if f"graphicPacks/{DOSSIER_PACK}/" in settings else R_ABSENT
    for e in racine.iter("Entry"):
        if f"/{DOSSIER_PACK}/" not in "/" + (e.get("filename") or "").replace("\\", "/"):
            continue
        if (e.get("disabled") or "").lower() in ("true", "1"):
            return R_ABSENT
        figes = prereglages_figes(rules)
        for pr in e.iter("Preset"):
            if (pr.findtext("category") or "") == CATEGORIE_MONDE and (pr.findtext("preset") or "") in figes:
                return R_FIGE
        return None
    return R_ABSENT
