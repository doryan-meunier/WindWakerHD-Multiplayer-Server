"""Hôte d'autorité préféré (lot S1 de docs/etudes/serveur_dedie_jeu.md §7 C et §9).

Sans réglage (défaut), l'autorité d'une zone (stage:salle) va au joueur présent depuis le plus longtemps et y reste
tant qu'il est là (``Server.reassign``). Avec ``autorite_preferee`` (``--autorite-preferee`` du serveur, clé
``autorite_preferee`` de config.json réglée dans « Administrer le serveur », interrupteur « Mon PC prend l'autorité »
d'Héberger › « Sur ce PC »), un PC choisi prend l'autorité des zones où il est présent : le PC le plus puissant, ou le
plus près du serveur sur Internet.

Valeur : un à PREF_MAX pseudos séparés par des virgules (ordre de priorité, sans tenir compte des majuscules), ou le
jeton ``@hote`` : le client lancé avec ``--hote`` (case « Je suis l'hôte » d'un serveur dédié), sinon le client du PC
du serveur (Héberger sur ce PC). Exemple : « Doryan, @hote ».

Règles (toutes côté serveur, rien ne change pour un client, ancien ou récent) :

* **Passation obligée** (l'autorité est partie, zone qui se remplit) : le préféré présent, libre (pas en cinématique,
  A11) et qui répond est choisi d'abord ; sinon la règle d'avant (le plus ancien présent).
* **Reprise** (l'autorité est là mais ce n'est pas un préféré) : le préféré la prend seulement s'il est dans la zone et
  répond depuis PREF_ENTREE s, qu'aucune passation n'a eu lieu dans la zone depuis PREF_APRES s (hystérésis : pas de
  va-et-vient quand il passe d'une salle à l'autre), que personne n'est en cinématique, que la zone est calme depuis
  PREF_CALME s (aucun coup porté ou reçu, aucun monstre blessé ou tué : jamais de passation au milieu d'un combat) et
  qu'aucun Hanteur n'y tient un monstre en laisse. Une autorité qui est déjà un préféré n'est jamais reprise par un
  autre.
* **Repli** : un préféré qui détient l'autorité mais dont aucun ``state`` n'arrive depuis PREF_SILENCE s (connexion
  qui décroche, jeu figé) la rend au meilleur autre joueur présent qui répond ; il ne la reprend ensuite que par la
  règle de reprise. S'il part ou se déconnecte, c'est la passation obligée habituelle. Jamais un Hanteur ni un Link
  possédé (``Partie.hors_jeu``) : la règle du mode Hanteurs passe avant.

Python pur, sans dépendance (serveur dédié autonome).
"""
from __future__ import annotations

PREF_HOTE = "@hote"
PREF_MAX = 4               # pseudos au plus
NOM_MAX = 24               # comme NAME_MAX du serveur
PREF_ENTREE = 3.0          # s : présent dans la zone et qui répond depuis au moins… avant une reprise
PREF_APRES = 10.0          # s : après une passation entre deux joueurs dans la zone, pas de reprise (hystérésis)
PREF_CALME = 6.0           # s : sans combat dans la zone avant une reprise
PREF_SILENCE = 2.5         # s : sans « state » du préféré qui détient l'autorité -> repli (20 states/s en jeu)
PREF_PAS = 0.5             # s : une zone est revue au plus toutes les… (sur les « state » de ses membres)
ALIAS_HOTE = {"@hote", "@hôte", "hote", "hôte", "@host", "host"}


def liste(valeur) -> list[str]:
    """Pseudos (et jeton ``@hote``) d'un réglage, dans l'ordre, sans doublon (majuscules ignorées). Texte « a, b »
    ou liste. Une valeur absente, vide ou invalide donne []."""
    if isinstance(valeur, str):
        bruts = valeur.split(",")
    elif isinstance(valeur, (list, tuple)):
        bruts = [x for x in valeur if isinstance(x, str)]
    else:
        return []
    out, vus = [], set()
    for b in bruts[:16]:
        n = "".join(c for c in b.strip() if c.isprintable())[:NOM_MAX].strip()
        if not n:
            continue
        if n.lower() in ALIAS_HOTE:
            n = PREF_HOTE
        if n.lower() in vus:
            continue
        vus.add(n.lower())
        out.append(n)
        if len(out) >= PREF_MAX:
            break
    return out


def normaliser(valeur) -> str | None:
    """Forme rangée dans config.json et passée en option : « a, b » ou None (pas de préférence)."""
    lst = liste(valeur)
    return ", ".join(lst) if lst else None


class Preference:
    """Réglage courant (changé à chaud par l'administrateur). ``rang`` : 0 pour le premier pseudo, 1 pour le
    second…, None si le joueur n'est pas un PC préféré."""

    def __init__(self, valeur=None):
        self.noms: list[str] = []
        self.regler(valeur)

    def regler(self, valeur) -> bool:
        """True si le réglage a changé."""
        neuf = liste(valeur)
        change = [n.lower() for n in neuf] != [n.lower() for n in self.noms]
        self.noms = neuf
        return change

    @property
    def actif(self) -> bool:
        return bool(self.noms)

    @property
    def valeur(self) -> str | None:
        return ", ".join(self.noms) if self.noms else None

    def rang(self, nom: str | None, hote: bool) -> int | None:
        """``nom`` : nom effectif du joueur ; ``hote`` : il est « l'hôte » au sens du jeton @hote (client --hote,
        sinon client du PC du serveur ; calculé par le serveur)."""
        n = (nom or "").lower()
        for i, v in enumerate(self.noms):
            if (v == PREF_HOTE and hote) or (v != PREF_HOTE and v.lower() == n):
                return i
        return None

    def texte(self) -> str:
        if not self.noms:
            return "aucun (le plus ancien présent dans la zone)"
        return ", ".join("l'hôte (client --hote, sinon celui du PC du serveur)" if v == PREF_HOTE else f"« {v} »"
                         for v in self.noms)
