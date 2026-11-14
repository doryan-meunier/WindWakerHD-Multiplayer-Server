"""Canons des plateformes en mer et switches de mort des monstres (--share-cannons, activé par --coop-donjon,
--no-cannons ; --no-death-switch).

Bug vu en jeu (2026-11-12, deux joueurs en mer) : les canons détruits sur une tour de guet par un joueur restaient
intacts chez l'autre, et le coffre qui apparaît quand la tour est nettoyée n'apparaissait que chez celui qui
l'avait nettoyée.

Ce que dit le jeu [HD-ASM, copie lecture seule du cking.rpx ; vérifié par tests/test_canons.py] :

* Le canon des plateformes et des récifs est « Ocanon », proc 63 (table des noms 0x101EBFDC), profil 0x101C8984 :
  instance 0x5A8 octets, **groupe 0 (fopAc_ACTOR_e)**, pas 2 (ENEMY). Le client ne synchronise que les acteurs du
  groupe 2 (autorité / marionnettes) : chaque PC simulait son propre canon, et seul le PC qui le détruisait le
  voyait détruit. Ni « hors table d'espèces » ni « pas d'autorité en mer » : le canon n'est pas un ennemi.
* Paramètres (0x02330100) : +0x3B1 = params >> 24 = switch de destruction (0xFF : aucun) ; +0x3B2 =
  (params >> 8) & 0xFF = switch d'activation ; +0x3B3 = params & 0xFF = type. Mode (+0x3AC, s32), table
  0x10026AF8 : 0 WAIT, 1 ATTACK, 2 DELETE, 3 attente du switch d'activation, 4 apparition. Détruit (PV +0x3A1 à 0
  après des coups : -2 pour une bombe, -1 sinon, 0x02330B64 / 0x02330C18) -> mode 2 : son init (0x02331558) pose
  le switch +0x3B1 (onSwitch 0x025B9E38, salle +0x326), lance les particules et efface les drapeaux d'attention
  (+0x2E0 &= ~0x3F) ; son execute est vide (blr, 0x023318D8) ; le draw (0x02330974) cache le fût en mode 2 ou 3.
  Le canon détruit RESTE chargé (épave) ; créé avec son switch déjà posé, il naît en mode 2 (0x0233031C).
* Le coffre des tours (tkrAOs, funcType 8) attend SON switch ((params >> 12) & 0xFF), comme funcType 1
  (actionSwOnWait, daTbox CreateInit 0x024B6028 -> 0x024B6090) ; funcType 2 seul attend la mort des ennemis
  (actionGenocide, qui ne compte que le groupe 2 : 0x025D98C0). Ce switch est posé par un AND_SW2 de la salle
  (« ET » de switches consécutifs), par exemple salle 19 de la mer : AND_SW2 0xE0E70007 -> 0xE7 quand 0xE0..0xE6
  sont posés ; 0xE0..0xE3 = canons (params >> 24), 0xE4..0xE6 = Bokoblins (angle.z & 0xFF) ; coffre FF0E7508 ->
  switch 0xE7. Salle 24 (récif) : 8 canons + une canonnière (0xE0..0xE8) -> 0x5F (switch de stage, déjà relayé
  par la progression).
* Ces switches 0xE0-0xEF sont des switches de SALLE (zone), que A7 (--share-temp) ne relaie que dans les donjons ;
  et une marionnette supprimée par la boîte aux lettres (commande 5) ne pose jamais son switch de mort, pas plus
  qu'un ennemi tué par un coup distant chez l'autorité. Chez l'autre joueur, le AND_SW2 ne voyait donc jamais ses
  switches : pas de coffre (seule son ouverture, partagée par synchro/coffres.py, arrivait).

Ce module :

1. Canons (``CanonSync``) : un canon qui passe d'un mode vivant (0, 1, 3, 4) au mode 2 chez nous -> « rare »
   {ev: canon, key} relayé aux joueurs de la zone (wwmp_server.handle_rare). Chez eux, le canon de même clé encore
   vivant est détruit PAR LE JEU (pack v19, bit 24, CALL 13 CANON) : PV à 0, son 0x2828, puis son propre
   setMode(this, 0, 2) (0x02330178), dont l'init du mode 2 (0x02331558) pose le switch de destruction et joue SES
   particules (0x82E4, 0x82E5, 0x82E6 et la fumée 0x3E1, via 0x02331418) : la vraie animation de destruction,
   comme à la fin de ses dégâts (0x02330C74-0x02330CF8 : particule 0x0F au point d'impact, son 0x2828,
   setMode(2) ; seule l'étincelle d'impact n'est pas rejouée). Bug vu en jeu le 2026-11-13 : avant, le canon
   passait à l'état détruit avec notre fumée générique (daDisappear), sans ses particules ni son son.
   Repli (pack sans le bit 24, particules indisponibles) : l'état exact d'un canon détruit écrit en données, comme
   avant : switch posé, fumée sans objet, PV à 0, drapeaux d'attention effacés, mode 2 EN DERNIER (son execute ne
   fait plus rien, son fût est caché). Jamais les deux : le CALL refuse un canon déjà en mode 2 (0, rien d'écrit),
   et Python n'écrit rien quand le jeu a fait la transition (switch posé une seule fois, par l'init du jeu).
   Aucun objet en double : un canon ne lâche rien. Symétrique : n'importe quel joueur peut détruire un canon.
2. Switches de mort (``DeathSwitches``) : juste avant que le client supprime une marionnette tuée chez l'autorité,
   ou, chez l'autorité, un ennemi achevé par un coup distant, il pose le switch de mort que le jeu aurait posé :
   Bokoblin (proc 189) +0x3D4 (angle.z & 0xFF, 0 = aucun ; create 0x0209FB30-0x0209FB74, execute 0x0209BD80-
   0x0209BDC0), canonnière Oship (proc 179) +0x3CD (params >> 24, 0xFF = aucun ; 0x023C5890, onSwitch 0x023C7A14 et
   0x023C7BE4). L'Octorok (Oq, proc 227) n'a pas de switch de mort (aucun onSwitch dans son code). Écriture comme
   dSv_info_c::onSwitch : 0x00-0x7F mémoire du stage vivante, 0x80-0xBF donjon, 0xC0-0xEF zone de la salle
   (current.roomNo +0x326) ; au-delà, rien.

Gardes : rien n'est écrit si l'acteur relu n'est pas exactement le canon attendu (id de process, proc 63, switch
+0x3B1 cohérent avec ses paramètres, mode 0-4), si le jeu n'est pas libre, si la salle n'est pas stable depuis
ROOM_SETTLE s, si l'écriture est interdite (--progress-no-apply, pas de pack) ; une erreur de lecture n'est jamais
fatale. Pack : CALL 13 (bit 24) pour un canon reçu en direct ; repli : écritures de données et commande
« disappear » existante (pack v17). Le rappel d'une salle (arrivant, §3) n'utilise jamais le CALL : rien n'explose
sous les yeux de l'arrivant.

3. Mémoire de la salle (``SallesDetruites`` côté serveur, ``CanonSync`` côté client) : un joueur qui arrive dans
   la salle APRÈS la destruction (ou qui y revient, se reconnecte, ou après un redémarrage du serveur avec le
   monde persistant) la voit nettoyée.

   Ce que fait le jeu d'origine [HD-ASM, vérifié par tests/test_canons.py, TestRpxSalle] : rien de tout
   cela n'est dans la sauvegarde. Les zones (dSv_zone_c, gi+0x7C8, 32 × 0x4C) sont hors du fichier :
   - les switches 0xE0-0xEF (canons, Bokoblins, canonnières et le switch du coffre des tours) sont effacés à
     CHAQUE changement de salle de Link, pour toutes les zones chargées : 0x025C35EC (salle sous Link différente
     de mStayNo 0x1047E6C8) -> zoneCountCheck 0x025C33E8 -> clearRoomSwitch 0x025B93B0 (« li r0, 0 ; sth r0,
     4(r3) » : dSv_zoneBit_c.mSwitch[2] = 0) ;
   - la zone elle-même (switches 0xC0-0xDF, ennemis tués mActorFlags) vit tant que la salle est chargée : compte
     mis à 2 au chargement (0x025C3574), décrémenté à chaque changement de salle par zoneCountCheck, zone libérée
     à 0 ; recréée vide (createZone 0x025B9DD8 -> init, 0x025B938C efface les 3 mots de switches) au chargement
     suivant (0x025B3E50 : zone de la salle < 0) ;
   - un canon détruit reste chargé (épave) ; recréé avec son switch effacé, il renaît intact. Un Bokoblin dont
     le switch de mort est posé n'est PAS créé (create 0x0209FB98 : isSwitch -> cPhs_ERROR, li r3, 5), de même
     qu'une canonnière (0x023C62A8) ; un canon créé avec son switch posé naît en mode 2 (setMode 0x02330354, que
     le jeu accompagne de ses propres particules).
   En solo, la tour est donc « propre » tant que Link ne s'en éloigne pas ; elle se repeuple quand sa salle est
   rechargée. Choix de la coop : le serveur garde, par zone (stage:salle), les canons détruits (clés) et les
   switches de mort de la salle (0xC0-0xEF, message rare {ev: sw_mort, sw}, jamais relayé) TANT QU'UN JOUEUR
   « A ENCORE LA SALLE » : il y est, ou c'est la dernière salle qu'il a quittée dans ce stage (la zone vit encore
   chez lui, comme après un changement de salle dans le jeu). Quand plus personne ne l'a, la salle est oubliée
   tout de suite (elle se repeuplera chez tous, comme dans le jeu) ; si c'est un départ (déconnexion) ou un
   redémarrage du serveur, elle est gardée GRACE s pour laisser revenir le joueur. Le monde persistant la garde
   sur disque (section « salles »).
   Rejeu : à chaque entrée dans la zone (et « ground_req », salle rechargée), le serveur envoie {ev: salle,
   canons, sw, rappel}. Le client pose ces switches DÈS que la zone de la salle existe (avant la création des
   acteurs si possible : Bokoblins et canonnières ne naissent pas, canons nés détruits), puis, salle stable, met
   dans l'état détruit les canons encore intacts SANS fumée (rien ne se passe sous les yeux de l'arrivant) et
   supprime sans fumée ni objet les Bokoblins / canonnières encore vivants dont le switch est dans la liste
   (jamais une marionnette de l'autorité). Le AND_SW2 de la salle voit alors tous ses switches : le coffre
   apparaît.

Les piliers (synchro/rares.py) ne sont toujours pas gardés.
"""
from __future__ import annotations

import math
import struct
import threading
import time

from jeu import progress as prog

# --- fopAc_ac_c HD -----------------------------------------------------------------------------------------
OFF_PC_ID, OFF_PROC, OFF_PARAMS = 0x04, 0x08, 0xB0
OFF_ATTN = 0x2E0               # u32 attention_info.flags (mode 2 : rlwinm r0, r0, 0, 0, 25 -> &= ~0x3F)
OFF_HOME = 0x2EC               # home.pos f32[3] ; home.roomNo s8 +0x12
OFF_HOME_ROOM = 0x2FE
OFF_POS = 0x314                # current.pos f32[3]
OFF_ROT_Y = 0x322              # current.angle.y s16
OFF_ROOM = 0x326               # current.roomNo s8 (onSwitch(sw, lbz 0x326))
OFF_HEALTH = 0x3A1             # s8 health
ATTN_MASK = 0x3F

# --- daObj_Canon (« Ocanon », proc 63) [HD-ASM] ------------------------------------------------------------
PROC_OCANON = 63
OC_SIZE = 0x5A8                # profil 0x101C8984 (+0x10)
OC_MODE = 0x3AC                # s32 mode (setMode 0x02330178 : stw r5, 0x3AC(r3))
OC_SW_DEATH = 0x3B1            # u8 params >> 24
OC_SW_APPEAR = 0x3B2           # u8 (params >> 8) & 0xFF
OC_READ = 0x3B4
MODE_WAIT, MODE_ATTACK, MODE_DELETE, MODE_SW_WAIT, MODE_APPEAR = 0, 1, 2, 3, 4
ALIVE = frozenset({MODE_WAIT, MODE_ATTACK, MODE_SW_WAIT, MODE_APPEAR})
NO_SWITCH = 0xFF
PROCS = (PROC_OCANON,)
PUFF_Y = 100.0                 # fumée au-dessus du canon (comme les marionnettes)

# --- switches de mort : proc -> (octet de l'instance, valeurs « aucun ») [HD-ASM, voir l'en-tête] ----------
PROC_BK, PROC_OSHIP = 189, 179
DEATH_SW = {
    PROC_BK: (0x3D4, frozenset({0x00, 0xFF})),
    PROC_OSHIP: (0x3CD, frozenset({0xFF})),
    PROC_OCANON: (OC_SW_DEATH, frozenset({0xFF})),
}

PROCS_MORT = (PROC_BK, PROC_OSHIP)    # ennemis dont le switch de mort est gardé pour la salle

KEY_MAX = 96
SW_FIRST, SW_END = 0xC0, 0xF0          # switches de zone (0xC0-0xDF) et de salle (0xE0-0xEF)
ZONE_MAX = 24                          # « stage:salle »


def _gi(mem) -> int | None:
    raw = mem.try_read(prog.SYM_GAMEINFO_PTR, 4)
    if raw is None:
        return None
    gi = struct.unpack(">I", raw)[0]
    return gi if 0x10000000 <= gi < 0x50000000 else None


def game_free(mem) -> str:
    """Relecture brute juste avant d'écrire : '' = libre."""
    nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
    if nxt is None or nxt[0]:
        return "changement de stage en cours"
    return prog.event_busy(mem)


def set_switch(mem, gi: int | None, sw: int, room: int) -> bool | None:
    """dSv_info_c::onSwitch(sw, room) en Python : True si l'octet a changé, False s'il était déjà posé, None si
    rien à faire (aucun switch, zone ou donjon absents, sauvegarde illisible)."""
    if gi is None or not isinstance(sw, int) or not 0 <= sw < prog.TSW_END:
        return None
    try:
        if sw < prog.TSW_DAN_FIRST:
            va = gi + prog.GI_CUR_MEMORY + 0x04 + 4 * (sw >> 5) + 3 - ((sw & 31) >> 3)
            return prog._rmw(mem, va, 1 << (sw & 7))
        return prog.write_temp_switch(mem, gi, room, sw, 1)
    except Exception:
        return None


def death_switch(mem, base: int) -> tuple[int, int, int] | None:
    """(proc, switch, salle courante) de l'acteur ENCORE en mémoire, ou None (espèce sans switch connu, aucun
    switch, lecture impossible)."""
    try:
        raw = mem.try_read(base + OFF_PROC, 2)
        if raw is None:
            return None
        proc = struct.unpack(">h", raw)[0]
        spec = DEATH_SW.get(proc)
        if spec is None:
            return None
        off, none = spec
        sw = mem.try_read(base + off, 1)
        room = mem.try_read(base + OFF_ROOM, 1)
        if sw is None or room is None or sw[0] in none:
            return None
        return proc, sw[0], struct.unpack(">b", room)[0]
    except Exception:
        return None


class DeathSwitches:
    """Pose le switch de mort d'un monstre que le client supprime lui-même (marionnette tuée chez l'autorité,
    ennemi achevé par un coup distant chez l'autorité). --no-death-switch pour désactiver."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.n = 0
        self.last = ""

    def mark(self, mem, base: int) -> bool:
        if not self.enabled or not base:
            return False
        d = death_switch(mem, base)
        if d is None:
            return False
        proc, sw, room = d
        if prog.TSW_ZONE_FIRST <= sw and room < 0:
            return False
        res = set_switch(mem, _gi(mem), sw, room)
        if res is None:
            return False
        self.n += 1
        self.last = f"switch 0x{sw:02X}@{room} (proc {proc})" + ("" if res else " déjà posé")
        return True

    def status(self) -> str:
        if not self.enabled:
            return "désactivés"
        return f"{self.n} posé(s)" + (f" ; dernier : {self.last}" if self.last else "")


# ---------------------------------------------------------------------------------------------------------
# Canons
# ---------------------------------------------------------------------------------------------------------
def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def canon_key(room: int, params: int, home) -> str:
    """Comme rares.pillar_key : proc, salle, params, home arrondi à 10 u (position du fichier)."""
    return (f"{PROC_OCANON}:{room}:{params & 0xFFFFFFFF:08X}:"
            f"{round(home[0] / 10) * 10:.0f},{round(home[1] / 10) * 10:.0f},{round(home[2] / 10) * 10:.0f}")


def valid_canon(msg) -> dict | None:
    """« rare » {ev: canon, key} : None si invalide."""
    if not isinstance(msg, dict) or msg.get("ev") != "canon":
        return None
    key = msg.get("key")
    if not isinstance(key, str) or not 0 < len(key) <= KEY_MAX or not key.startswith(f"{PROC_OCANON}:"):
        return None
    return {"ev": "canon", "key": key}


def valid_sw_mort(msg) -> dict | None:
    """« rare » {ev: sw_mort, sw} : switch de mort (Bokoblin, canonnière) posé dans la salle de l'émetteur."""
    if not isinstance(msg, dict) or msg.get("ev") != "sw_mort":
        return None
    sw = msg.get("sw")
    if not _int(sw) or not SW_FIRST <= sw < SW_END:
        return None
    return {"ev": "sw_mort", "sw": sw}


def zone_room(zone) -> int | None:
    """« stage:salle » -> salle (0-63), sinon None."""
    if not isinstance(zone, str) or not 0 < len(zone) <= ZONE_MAX or ":" not in zone:
        return None
    stage, _, room = zone.rpartition(":")
    if not stage or not room.isdigit() or len(room) > 2:
        return None
    r = int(room)
    return r if 0 <= r < 64 else None


def key_room(key: str) -> int | None:
    """Salle d'une clé de canon bien formée (« 63:salle:PARAMS:x,y,z », voir canon_key), sinon None."""
    try:
        parts = key.split(":")
        if len(parts) != 4 or len(parts[2]) != 8:
            return None
        int(parts[2], 16)
        return int(parts[1])
    except (ValueError, AttributeError):
        return None


def key_switch(key: str) -> int | None:
    """Switch de destruction d'un canon d'après sa clé (params >> 24), s'il est un switch de zone."""
    try:
        sw = int(key.split(":")[2], 16) >> 24
    except (ValueError, IndexError, AttributeError):
        return None
    return sw if SW_FIRST <= sw < SW_END else None


def valid_salle(msg) -> dict | None:
    """« rare » {ev: salle, zone, canons, sw} (serveur -> client, rappel de la salle) : None si invalide."""
    if not isinstance(msg, dict) or msg.get("ev") != "salle":
        return None
    zone, cans, sws = msg.get("zone"), msg.get("canons", []), msg.get("sw", [])
    room = zone_room(zone)
    if room is None or not isinstance(cans, list) or not isinstance(sws, list):
        return None
    if len(cans) > SallesDetruites.CANONS_MAX or len(sws) > SW_END - SW_FIRST:
        return None
    keys = []
    for k in cans:
        v = valid_canon({"ev": "canon", "key": k})
        if v is None or key_room(k) != room:
            return None
        keys.append(k)
    sw = []
    for s in sws:
        if not _int(s) or not SW_FIRST <= s < SW_END:
            return None
        sw.append(s)
    return {"ev": "salle", "zone": zone, "canons": keys, "sw": sw}


class SallesDetruites:
    """Serveur : par zone (« stage:salle »), canons détruits (clés) et switches de mort de la salle, rejoués à qui
    y entre. Durée de vie : voir l'en-tête du module (§3). Rien ici ne touche au réseau : wwmp_server appelle
    ``tenus`` (zones encore « chez » un joueur) après chaque changement de zone et chaque départ."""

    GRACE = 120.0          # s : salle gardée après le départ (déconnexion) du dernier joueur qui l'avait, ou relue
    ZONES_MAX = 256
    CANONS_MAX = 32

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.zones: dict[str, dict] = {}       # zone -> {"canons": [clé], "sw": set, "orph": heure | None}

    def _rec(self, zone: str) -> dict | None:
        r = self.zones.get(zone)
        if r is None:
            if zone_room(zone) is None or len(self.zones) >= self.ZONES_MAX:
                return None
            r = self.zones[zone] = {"canons": [], "sw": set(), "orph": None}
        return r

    def add_canon(self, zone: str, key: str) -> bool:
        """True si la salle a changé."""
        if valid_canon({"ev": "canon", "key": key}) is None or key_room(key) != zone_room(zone):
            return False
        r = self._rec(zone)
        if r is None or key in r["canons"] or len(r["canons"]) >= self.CANONS_MAX:
            return False
        r["canons"].append(key)
        return True

    def add_sw(self, zone: str, sw: int) -> bool:
        if not _int(sw) or not SW_FIRST <= sw < SW_END:
            return False
        r = self._rec(zone)
        if r is None or sw in r["sw"]:
            return False
        r["sw"].add(sw)
        return True

    def tenus(self, kept: set, depart: bool = False, now: float | None = None) -> list[str]:
        """``kept`` : zones qu'au moins un joueur « a encore ». Une zone qui n'est plus tenue est oubliée tout de
        suite (changement de salle), ou gardée GRACE s (``depart`` : déconnexion du dernier). Renvoie les zones
        oubliées."""
        now = self.clock() if now is None else now
        gone = []
        for z, r in list(self.zones.items()):
            if z in kept:
                r["orph"] = None
            elif r["orph"] is None:
                if depart:
                    r["orph"] = now
                else:
                    gone.append(z)
            elif now - r["orph"] > self.GRACE:
                gone.append(z)
        for z in gone:
            del self.zones[z]
        return gone

    def snap(self, zone: str, now: float | None = None) -> dict | None:
        r = self.zones.get(zone)
        if r is None:
            return None
        now = self.clock() if now is None else now
        if r["orph"] is not None and now - r["orph"] > self.GRACE:
            del self.zones[zone]
            return None
        if not r["canons"] and not r["sw"]:
            return None
        return {"ev": "salle", "zone": zone, "canons": list(r["canons"]), "sw": sorted(r["sw"])}

    # --- monde persistant -------------------------------------------------------------------------------
    def document(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        return {z: {"canons": list(r["canons"]), "sw": sorted(r["sw"])} for z, r in sorted(self.zones.items())
                if (r["canons"] or r["sw"]) and (r["orph"] is None or now - r["orph"] <= self.GRACE)}

    def restaurer(self, doc, now: float | None = None) -> tuple[int, int]:
        """Section « salles » relue : chaque salle validée comme un rappel ; gardée GRACE s si personne ne la
        reprend. Renvoie (salles gardées, salles abîmées ignorées)."""
        now = self.clock() if now is None else now
        ok = bad = 0
        if not isinstance(doc, dict):
            return 0, 0
        for z, e in doc.items():
            v = valid_salle(dict(e, ev="salle", zone=z)) if isinstance(e, dict) else None
            if v is None or len(self.zones) >= self.ZONES_MAX:
                bad += 1
                continue
            self.zones[z] = {"canons": list(dict.fromkeys(v["canons"])), "sw": set(v["sw"]), "orph": now}
            ok += 1
        return ok, bad


def read_canon(mem, base: int) -> dict | None:
    raw = mem.try_read(base, OC_READ)
    if raw is None or len(raw) < OC_READ:
        return None
    pid, = struct.unpack_from(">I", raw, OFF_PC_ID)
    proc, = struct.unpack_from(">h", raw, OFF_PROC)
    if proc != PROC_OCANON:
        return None
    pos = struct.unpack_from(">3f", raw, OFF_POS)
    return {"pid": pid, "params": struct.unpack_from(">I", raw, OFF_PARAMS)[0],
            "home": struct.unpack_from(">3f", raw, OFF_HOME),
            "home_room": struct.unpack_from(">b", raw, OFF_HOME_ROOM)[0],
            "room": struct.unpack_from(">b", raw, OFF_ROOM)[0],
            "pos": pos, "rot_y": struct.unpack_from(">h", raw, OFF_ROT_Y)[0],
            "attn": struct.unpack_from(">I", raw, OFF_ATTN)[0],
            "health": struct.unpack_from(">b", raw, OFF_HEALTH)[0],
            "mode": struct.unpack_from(">i", raw, OC_MODE)[0],
            "sw": raw[OC_SW_DEATH], "sw_appear": raw[OC_SW_APPEAR]}


def consistent(c: dict | None) -> bool:
    """Canon lisible et cohérent : switch de destruction = params >> 24 (preuve que les décalages sont les
    bons), mode connu, position finie."""
    return (c is not None and c["sw"] == (c["params"] >> 24) & 0xFF
            and c["sw_appear"] == (c["params"] >> 8) & 0xFF
            and MODE_WAIT <= c["mode"] <= MODE_APPEAR and all(math.isfinite(v) for v in c["pos"]))


# CALL 13 (pack v19, bit 24) : résultat -> raison de destroy ('' = fait par le jeu)
CALL_RAISONS = {1: "", 0: "déjà détruit", -1: "lecture incohérente", -2: "pas de sauvegarde",
                -3: "événement en cours", -4: "acteur changé"}


def destroy(mem, expect: dict, puff=None, appel=None) -> str:
    """Met le canon ``expect`` ({pid, base}) dans l'état d'un canon détruit. Renvoie '' si fait, sinon la raison.

    ``appel(pid) -> int | None`` (pack v19 bit 24, CALL 13) : le JEU détruit le canon (setMode(2) : son init pose le
    switch et joue SES particules, plus le son 0x2828) ; Python n'écrit alors rien. None (capacité absente) ou -5
    (particules indisponibles) : repli = écritures de données (switch, fumée ``puff``, PV, attention, mode 2 en
    dernier). Requête sans réponse (exception) : rien n'est fait ici, nouvel essai au tick suivant."""
    base = expect["base"]
    cur = read_canon(mem, base)
    if cur is None or cur["pid"] != expect["pid"]:
        return "acteur changé"
    if not consistent(cur):
        return "lecture incohérente"
    if cur["mode"] == MODE_DELETE:
        return "déjà détruit"
    if cur["mode"] not in ALIVE:
        return f"mode {cur['mode']}"
    busy = game_free(mem)
    if busy:
        return busy
    if appel is not None:
        try:
            res = appel(cur["pid"])
        except Exception as exc:              # boîte aux lettres sans réponse : rien n'a été fait
            return f"boîte aux lettres : {type(exc).__name__}"
        if res is not None and res in CALL_RAISONS:
            return CALL_RAISONS[res]
        # None (pack sans le bit 24), -5 ou code inconnu : rien n'a été fait par le jeu -> repli données
    if cur["sw"] != NO_SWITCH and cur["room"] >= 0:
        set_switch(mem, _gi(mem), cur["sw"], cur["room"])     # ce que fait l'init du mode 2 (onSwitch)
    if puff is not None:
        try:
            x, y, z = cur["pos"]
            puff(cur["pid"], (x, y + PUFF_Y, z), cur["room"], cur["rot_y"])
        except Exception:
            pass                                            # pas de fumée : le canon est détruit quand même
    again = read_canon(mem, base)
    if again is None or again["pid"] != cur["pid"] or again["mode"] not in ALIVE:
        return "acteur changé"
    mem.write(base + OFF_HEALTH, struct.pack(">b", 0))
    mem.write(base + OFF_ATTN, struct.pack(">I", again["attn"] & ~ATTN_MASK & 0xFFFFFFFF))
    mem.write(base + OC_MODE, struct.pack(">i", MODE_DELETE))   # EN DERNIER : execute vide, fût caché
    return ""


class CanonSync:
    ROOM_SETTLE = 2.0       # s dans la salle avant toute écriture
    TTL = 10.0              # s : canon reçu non appliqué -> abandonné
    ECHO = 30.0             # s : canon détruit par nous sur ordre reçu -> jamais renvoyé
    VERIFY = 2.0            # s pendant lesquelles un canon détruit par nous est relu (mode 2 tenu)
    RETRIES = 3
    TTL_RAPPEL = 20.0       # s : rappel de la salle (serveur) appliqué au plus tard

    def __init__(self, enabled: bool = False, clock=time.time):
        self.enabled = enabled
        self.clock = clock
        self.lock = threading.Lock()
        self.inbox: list[dict] = []
        self.out: list[dict] = []
        self.notes: list[str] = []
        self.zone: str | None = None
        self.prev: dict[str, tuple] = {}      # clé -> (id de process, mode) vus au relevé précédent
        self.pending: list[dict] = []
        self.ours: dict[str, float] = {}      # clé -> heure : détruit chez nous sur ordre reçu (anti-écho)
        self.verify: dict[str, list] = {}     # clé -> [id, heure, essais] : mode 2 relu après notre écriture
        self.n = {"out": 0, "in": 0, "retry": 0, "rappel": 0, "sw": 0, "suppr": 0, "jeu": 0}
        # rappel de la salle (serveur, à l'entrée) : {zone, t, sw (tous), morts (Bokoblins / canonnières),
        # poses (switches déjà posés)}
        self.recall: dict | None = None
        self.watch: set[int] = set()          # switches de mort des ennemis vus dans la salle
        self.watch_pids: set[int] = set()     # ennemis déjà lus
        self.sw_sent: set[int] = set()        # switches de mort déjà annoncés pour la salle

    # --- files ----------------------------------------------------------------------------------------
    def _note(self, s: str) -> None:
        self.notes = (self.notes + [s])[-40:]

    def take_notes(self) -> list[str]:
        out, self.notes = self.notes, []
        return out

    def take_outgoing(self) -> list[dict]:
        with self.lock:
            out, self.out = self.out, []
        return out

    def _send(self, msg: dict) -> None:
        msg.setdefault("zone", self.zone)
        with self.lock:
            self.out = (self.out + [msg])[-64:]

    def on_message(self, msg: dict, me=None, now: float | None = None) -> None:
        """Boucle réseau : rangé, appliqué au prochain step (thread de travail)."""
        if not isinstance(msg, dict) or (me is not None and msg.get("from") == me and msg.get("from") is not None):
            return
        if msg.get("ev") == "salle":
            s = valid_salle(msg)
            if s is not None and msg.get("from") is None:      # du serveur seulement
                with self.lock:
                    self.inbox = (self.inbox + [dict(s, t=self.clock() if now is None else now)])[-64:]
            return
        v = valid_canon(msg)
        if v is None or not isinstance(msg.get("zone"), str):
            return
        m = dict(v, zone=msg["zone"], t=self.clock() if now is None else now)
        with self.lock:
            self.inbox = (self.inbox + [m])[-64:]

    def reset_network(self) -> None:
        with self.lock:
            self.inbox.clear()
            self.out.clear()
        self.pending.clear()
        self.recall = None
        self.sw_sent.clear()                  # nouveau serveur (redémarré) : on lui redit ce qu'on a vu

    # --- étape (thread de travail) --------------------------------------------------------------------
    def _canons(self, actors, room: int) -> dict:
        out = {}
        for a in actors or ():
            pid, base, proc, params, hroom = a[0], a[1], a[2], a[3], a[4]
            size = a[5] if len(a) > 5 else None
            home = a[6] if len(a) > 6 else None
            if proc != PROC_OCANON or hroom not in (room, -1) or home is None:
                continue
            if size not in (None, 0, OC_SIZE):
                continue                      # profil inattendu : pas notre canon
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in home):
                continue
            k = canon_key(room, params, home)
            if k in out:
                out[k] = None                 # deux canons de même clé : ignorés
                continue
            out[k] = {"pid": pid, "base": base}
        return {k: v for k, v in out.items() if v is not None}

    # --- mémoire de la salle (§3 de l'en-tête) -------------------------------------------------------
    def _recall_in(self, e: dict) -> None:
        """Rappel du serveur : canons à remettre détruits (sans fumée), switches à reposer, ennemis à retirer."""
        zone, t = e["zone"], e["t"]
        sws = set(e["sw"])
        for k in e["canons"]:
            s = key_switch(k)
            if s is not None:
                sws.add(s)
        self.recall = {"zone": zone, "room": zone_room(zone), "t": t, "sw": sws, "morts": set(e["sw"]),
                       "poses": set()}
        have = {(p["zone"], p["key"]) for p in self.pending}
        for k in e["canons"]:
            if (zone, k) not in have:
                self.pending.append({"ev": "canon", "key": k, "zone": zone, "t": t, "rappel": 1})
        self.pending = self.pending[-64:]

    def _recall_step(self, mem, now: float, zone: str, room: int, ok_write: bool, remove, can: bool = True) -> None:
        r = self.recall
        if r is None or r["zone"] != zone or r["room"] != room or room < 0:
            return
        if now - r["t"] > self.TTL_RAPPEL:
            self.recall = None
            return
        if not can:
            return                            # écriture interdite (--progress-no-apply, pas de pack)
        nxt = mem.try_read(prog.SYM_NEXT_STAGE_ENABLED, 1)
        if nxt is None or nxt[0]:
            return                            # changement de stage : la zone va disparaître
        # switches posés DÈS que la zone de la salle existe (avant la création des acteurs si possible) : ce
        # n'est qu'un bit de la sauvegarde en mémoire, comme onSwitch ; l'ordre de la salle courante (effacement
        # des switches de salle au changement de salle) est déjà passé puisque l'état dit cette salle
        gi = _gi(mem)
        for sw in sorted(r["sw"] - r["poses"]):
            res = set_switch(mem, gi, sw, room)
            if res is None:
                break                         # zone pas (encore) créée : au tick suivant
            r["poses"].add(sw)
            if res:
                self.n["sw"] += 1
        if ok_write and remove is not None and r["morts"] and r["poses"] >= r["morts"]:
            done = remove({(room, sw) for sw in r["morts"]}) or ()
            if done:
                self.n["suppr"] += len(done)
                self._note(f"canons : {len(done)} ennemi(s) déjà tué(s) dans cette salle retiré(s)")

    def _watch_step(self, mem, zone: str, room: int, morts) -> None:
        """Switches de mort des Bokoblins / canonnières de la salle posés chez nous (mort jouée par le jeu,
        marionnette supprimée, rappel) -> annoncés une fois au serveur, qui les garde pour la salle."""
        if room < 0 or (not morts and not self.watch):
            return
        gi = _gi(mem)
        if gi is None:
            return
        dan = mem.try_read(gi + prog.GI_DAN + prog.DAN_STAGE_NO, 1)
        if dan is None or struct.unpack(">b", dan)[0] in prog.TSW_DUNGEON_SAVE_NOS:
            return                            # donjon : A7 relaie déjà les switches de salle
        for a in morts or ():
            pid, base, proc, hroom = a[0], a[1], a[2], a[3]
            if proc not in PROCS_MORT or hroom != room or pid in self.watch_pids:
                continue
            d = death_switch(mem, base)
            if d is not None and d[0] == proc and d[2] == room and SW_FIRST <= d[1] < SW_END:
                self.watch.add(d[1])
                self.watch_pids.add(pid)
        if not self.watch:
            return
        zb = prog._zone_base(mem, gi, room)
        raw = mem.try_read(zb + prog.ZONE_BITS, prog.ZONE_SWITCH_SIZE) if zb is not None else None
        if raw is None:
            return
        on = prog._zone_switches(raw)
        for s in sorted(self.watch):
            if s not in on:
                self.sw_sent.discard(s)       # effacé (salle rechargée) : une nouvelle mort sera annoncée
            elif s not in self.sw_sent:
                self.sw_sent.add(s)
                self._send({"type": "rare", "ev": "sw_mort", "sw": s})

    def step(self, mem, now: float, zone: str | None, actors, room: int = -1, free: bool = True,
             settled: bool = False, can: bool = True, puff=None, morts=None, remove=None, appel=None) -> None:
        """actors : [(id, adresse, proc, params, salle d'origine, taille, home)] des canons, relevés ce tick.
        morts : [(id, adresse, proc, salle d'origine)] des Bokoblins et canonnières (switches de mort de la
        salle) ; remove({(salle, switch)}) : supprime sans fumée nos ennemis vivants de ces switches.
        appel(pid) -> int | None : CALL 13 du pack (bit 24), le jeu détruit lui-même un canon reçu en direct
        (vraie destruction : particules et son du jeu) ; None : repli (écritures et fumée générique)."""
        if not self.enabled:
            return
        if zone != self.zone:
            self.zone = zone
            self.prev.clear()
            self.verify.clear()
            self.watch.clear()
            self.watch_pids.clear()
            self.sw_sent.clear()
            self.pending = [e for e in self.pending if e["zone"] == zone]
            if self.recall is not None and self.recall["zone"] != zone:
                self.recall = None
        with self.lock:
            inbox, self.inbox = self.inbox, []
        for e in inbox:
            if e.get("ev") == "salle":
                self._recall_in(e)
            else:
                self.pending = (self.pending + [e])[-64:]
        if mem is None or zone is None:
            return
        ok_write = free and settled and can
        try:
            self._recall_step(mem, now, zone, room, ok_write, remove, can)
        except Exception:
            pass                              # rappel non appliqué ce tick : nouvel essai au suivant
        try:
            self._watch_step(mem, zone, room, morts)
        except Exception:
            pass
        canons = self._canons(actors, room)
        for k in [k for k, t in self.ours.items() if now - t > self.ECHO]:
            del self.ours[k]
        seen = {}
        for k, o in canons.items():
            cur = read_canon(mem, o["base"])
            if cur is None or cur["pid"] != o["pid"] or not consistent(cur):
                continue
            seen[k] = (cur["pid"], cur["mode"])
            p = self.prev.get(k)
            # même acteur (salle rechargée = canon recréé, peut-être né détruit : pas une destruction)
            if p is not None and p[0] == cur["pid"] and p[1] in ALIVE and cur["mode"] == MODE_DELETE \
                    and k not in self.ours:
                self._send({"type": "rare", "ev": "canon", "key": k})
                self.n["out"] += 1
            v = self.verify.get(k)
            if v is not None:
                if v[0] != cur["pid"] or now - v[1] > self.VERIFY:
                    del self.verify[k]
                elif cur["mode"] != MODE_DELETE and ok_write and v[2] < self.RETRIES:
                    # le jeu a repris un autre mode juste après notre écriture (tir en cours) : on la refait
                    v[2] += 1
                    self.n["retry"] += 1
                    if destroy(mem, o) == "":
                        seen[k] = (cur["pid"], MODE_DELETE)
        self.prev = seen
        keep = []
        for e in self.pending:
            if now - e["t"] > (self.TTL_RAPPEL if e.get("rappel") else self.TTL):
                if not e.get("rappel"):
                    self._note("canons : canon détruit chez un autre joueur, pas chez toi (salle pas prête)")
                continue
            if e["zone"] != zone:
                continue
            o = canons.get(e["key"])
            if o is None:
                keep.append(e)                # pas (encore) chargé
                continue
            if self.prev.get(e["key"]) == (o["pid"], MODE_DELETE):
                continue                      # déjà détruit ici
            if not ok_write:
                keep.append(e)
                continue
            try:
                # rappel (arrivant) : sans fumée ni explosion, rien ne se passe sous ses yeux ; en direct : le jeu
                # détruit le canon lui-même (CALL 13), sinon écritures et fumée générique
                if e.get("rappel"):
                    why = destroy(mem, o, None)
                else:
                    vus = []

                    def ap(pid, _vus=vus):
                        r = appel(pid)
                        _vus.append(r)
                        return r
                    why = destroy(mem, o, puff, ap if appel is not None else None)
                    if why == "" and vus and vus[-1] == 1:
                        self.n["jeu"] += 1    # détruit par le jeu (vraie destruction), pas par nos écritures
            except Exception as exc:          # écriture refusée : nouvel essai au tick suivant
                why = f"erreur {type(exc).__name__}"
            if why == "":
                self.ours[e["key"]] = now
                self.prev[e["key"]] = (o["pid"], MODE_DELETE)
                self.verify[e["key"]] = [o["pid"], now, 0]
                if e.get("rappel"):
                    self.n["rappel"] += 1
                else:
                    self.n["in"] += 1
                    self._note("canons : un canon détruit chez un autre joueur est détruit chez toi")
            elif why in ("déjà détruit", "acteur changé", "lecture incohérente") or why.startswith("mode "):
                continue
            else:
                keep.append(e)                # jeu occupé (événement, chargement) : plus tard
        self.pending = keep

    def status(self) -> str:
        if not self.enabled:
            return "désactivé"
        r = f", {self.n['retry']} réécriture(s)" if self.n["retry"] else ""
        m = (f" ; salle retenue : {self.n['rappel']} canon(s) remis détruits, {self.n['sw']} switch(es) reposé(s), "
             f"{self.n['suppr']} ennemi(s) retiré(s)") if self.n["rappel"] or self.n["sw"] or self.n["suppr"] else ""
        j = f" (dont {self.n['jeu']} par le jeu)" if self.n["jeu"] else ""
        return f"{self.n['out']} détruit(s) chez toi, {self.n['in']} rejoué(s){j}{r}{m}"
