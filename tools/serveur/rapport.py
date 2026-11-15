"""Rapport d'essai CÔTÉ SERVEUR (docs/hotes/JOUER_PAR_INTERNET.md, « Rapport d'essai du serveur ») : le même genre de
zip que le bouton « Rapport d'essai » du lanceur (lanceur/rapport.py), pour le serveur dédié Docker.

    docker compose exec wwmp python -m serveur.rapport
        -> /donnees/rapports/WWMP_rapport_serveur_<date>.zip, puis :
    docker compose cp wwmp:/donnees/rapports/WWMP_rapport_serveur_<date>.zip .

    # avec le journal de Docker (Linux / macOS : le zip sort directement sur la sortie standard)
    docker compose logs --no-color --tail 20000 wwmp | docker compose exec -T wwmp python -m serveur.rapport --journal-stdin --stdout > rapport_serveur.zip

Contenu : ``rapport.txt`` (versions, système, erreurs et anomalies trouvées), ``versions.json``, ``config_serveur.json``
(config.json du volume, expurgée), ``etat_serveur.json`` (réponse « lire » de l'administration : réglages appliqués,
joueurs, autorités, sans le mot de passe de partie), ``anomalies_s2.json`` (journal d'anomalies de la validation côté
serveur, lot S2, en mémoire dans le serveur), ``mondes.json`` (liste des mondes : nom, date, nombre de joueurs, taille ;
jamais leur contenu), ``mises_a_jour.json``, ``journaux/`` (fichiers ``*.log`` du volume, et le journal de Docker reçu
par ``--journal-stdin`` ; la fin seulement), ``serveur_anomalies_s2.txt`` (lignes « ANOMALIE » de ces journaux).

L'état et les anomalies se lisent par l'administration du serveur (serveur/admin.py : défi HMAC, compte admin du volume
ou des variables WWMP_ADMIN_*, jamais créé ici), sur 127.0.0.1 dans le conteneur. ``--sans-connexion`` : rien que les
fichiers. Jamais dans le zip : admin.txt, mot_de_passe.txt, cles.json, spoilers, contenu des mondes ; mots de passe,
e-mails et IP publiques des joueurs expurgés (commun/rapport.py).
"""
from __future__ import annotations

import argparse
import os
import platform
import sys
import time

from commun import rapport as R
from commun import acces
from commun.protocole import CLIENT_VERSION, VERSION

PREFIXE = "WWMP_rapport_serveur"
GARDER = 10                     # rapports gardés dans /donnees/rapports
PORT = 7777


def _compte(donnees: str, env) -> tuple[str | None, str | None]:
    """Compte admin SANS le créer (serveur/admin.compte le créerait)."""
    from serveur import admin as adm
    u = adm.nettoyer_utilisateur(env.get(adm.ENV_UTILISATEUR))
    m = acces.nettoyer(env.get(adm.ENV_MDP))
    if m:
        return u or adm.UTILISATEUR_DEFAUT, m
    fu, fm = adm.lire_fichier_compte(os.path.join(donnees, adm.FICHIER))
    return (u or fu or adm.UTILISATEUR_DEFAUT, fm) if fm else (None, None)


def creer(donnees: str, port: int = PORT, sortie: str | None = None, demande=None, env=None, journal_texte: str = "",
          connexion: bool = True, hote: str = "127.0.0.1", maintenant: float | None = None,
          limite_journal: int = R.LIMITE_JOURNAL) -> dict:
    """Construit le zip ; ``sortie`` : dossier (défaut DONNEES/rapports) ou chemin d'un fichier .zip. ``demande`` :
    fonction de l'administration (serveur.admin.demande ; remplacée dans les essais). Renvoie {chemin, taille, ...}."""
    from serveur import admin as adm
    env = os.environ if env is None else env
    t = time.time() if maintenant is None else maintenant
    u, m = _compte(donnees, env)
    secrets = {s for s in (m, acces.lire_fichier(os.path.join(donnees, acces.FICHIER)), env.get("WWMP_MOT_DE_PASSE"),
                           env.get(adm.ENV_MDP)) if s}
    ex = R.Expurgeur((), secrets, ())
    entrees: list[tuple[str, str, str]] = []
    trouvees: list[tuple[str, list[str], int]] = []
    notes: list[str] = []

    def j(obj) -> str:
        import json
        return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"

    # état du serveur qui tourne (administration)
    etat = None
    if connexion:
        if not m:
            notes.append("état du serveur non lu : pas de compte admin (admin.txt ou WWMP_ADMIN_*)")
        else:
            try:
                etat = (demande or adm.demande)(hote, port, u, m, {"cmd": "lire"})
            except Exception as e:              # noqa: BLE001  (ErreurAdmin, réseau…)
                notes.append(f"état du serveur non lu : {getattr(e, 'args', [e])[0] if getattr(e, 'args', None) else e}")
    else:
        notes.append("état du serveur non lu : --sans-connexion")
    anomalies = None
    if isinstance(etat, dict):
        etat = {k: v for k, v in etat.items() if k not in ("mdp_partie", "sig", "nonce", "type", "utilisateur")}
        anomalies = etat.pop("anomalies", None)
        entrees.append(("etat_serveur.json", j(ex.json(etat)), "réponse « lire » de l'administration"))
        entrees.append(("anomalies_s2.json", j(ex.json(anomalies)), "journal d'anomalies (lot S2)"))
    cfg = R.lire_json(os.path.join(donnees, adm.CONFIG))
    if isinstance(cfg, dict):
        entrees.append(("config_serveur.json", j(ex.json(cfg)), "expurgée"))
    # mondes : la liste seulement
    try:
        from serveur import monde_persistant as mpm
        lst = []
        for x in mpm.lister(os.path.join(donnees, "mondes"))[:64]:
            p = os.path.join(donnees, "mondes", x["id"], "monde.json")
            lst.append({"id": x["id"], "nom": x.get("nom"), "modifie": x.get("modifie"),
                        "joueurs": len(x.get("joueurs") or []), "erreur": bool(x.get("erreur")),
                        "taille": os.path.getsize(p) if os.path.isfile(p) else None})
        entrees.append(("mondes.json", j(ex.json(lst)), "liste seulement, jamais le contenu"))
    except Exception as e:                      # noqa: BLE001
        notes.append(f"mondes non listés ({type(e).__name__})")
    dernier = R.lire_json(os.path.join(donnees, "mises_a_jour", "dernier.json"))
    if isinstance(dernier, dict):
        entrees.append(("mises_a_jour.json", j(ex.json({k: v for k, v in dernier.items() if k != "signature"})), ""))
    # journaux : *.log du volume (--journal), journal de Docker reçu sur l'entrée standard
    anomalies_lignes: list[str] = []
    journaux: list[tuple[str, str, dict]] = []
    try:
        noms = sorted(n for n in os.listdir(donnees) if n.endswith((".log", ".log.1")))
    except OSError:
        noms = []
    for n in noms[:8]:
        texte, info = R.lire_journal(os.path.join(donnees, n), limite_journal)
        if not info["erreur"]:
            journaux.append(("journaux/" + (n[:-6] + ".1.log" if n.endswith(".log.1") else n), texte, info))
    if journal_texte:
        brut = journal_texte[-limite_journal:]
        journaux.append(("journaux/docker.log", brut, {"coupe": len(journal_texte) > limite_journal,
                                                       "taille": len(journal_texte)}))
    for nz, texte, info in journaux:
        texte = ex.texte(texte)
        entrees.append((nz, texte, f"fin seulement ({R.taille_texte(info.get('taille', 0))} en tout)"
                        if info.get("coupe") else ""))
        nb, lignes = R.erreurs(texte)
        if nb:
            trouvees.append((nz, lignes, nb))
        anomalies_lignes += [x for x in texte.splitlines() if "ANOMALIE" in x]
    if journaux:
        entrees.append(("serveur_anomalies_s2.txt", "\n".join(anomalies_lignes[-2000:]) + "\n" if anomalies_lignes
                        else "(aucune ligne « ANOMALIE » dans les journaux)\n", ""))
    versions = {"outils": CLIENT_VERSION, "protocole": VERSION, "python": sys.version.split()[0],
                "systeme": platform.platform(), "docker": os.path.exists("/.dockerenv"), "donnees": donnees,
                "port": port}
    entrees.insert(0, ("versions.json", j(ex.json(versions)), ""))
    resume = _resume(t, versions, etat, anomalies, trouvees, notes, entrees)
    if sortie and sortie.lower().endswith(".zip"):
        chemin = sortie
    else:
        dossier = sortie or os.path.join(donnees, "rapports")
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, R.nom_zip(PREFIXE, t))
    res = R.ecrire_zip(chemin, [("rapport.txt", resume, "")] + entrees)
    if not (sortie and sortie.lower().endswith(".zip")):
        R.garder_derniers(os.path.dirname(chemin), PREFIXE, GARDER)
    res["etat_lu"] = etat is not None
    return res


def _resume(t, versions, etat, anomalies, trouvees, notes, entrees) -> str:
    L = ["Rapport d'essai du serveur Wind Waker Coop", "=" * 43, "",
         f"Créé le {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))} (heure du serveur)", "",
         f"Outils {versions['outils']} ; protocole {versions['protocole']} ; Python {versions['python']} ; "
         f"{versions['systeme']}{' ; Docker' if versions['docker'] else ''}", ""]
    if isinstance(etat, dict):
        a = etat.get("actuel") or {}
        L += ["Serveur en marche (administration lue)",
              f"  joueurs : {', '.join(etat.get('joueurs') or []) or 'aucun'}",
              f"  monde : {(etat.get('monde') or {}).get('nom') or 'aucun'} ; validation : {a.get('validation')} ; "
              f"max joueurs : {a.get('max_joueurs')} ; Hanteurs : {a.get('mode_hanteurs')}", ""]
    if isinstance(anomalies, dict):
        L += [f"Anomalies (lot S2) : {anomalies.get('total', 0)} en tout, {anomalies.get('refuses', 0)} refusées ; par "
              f"genre : {anomalies.get('par_genre') or {}}", ""]
    for n in notes:
        L.append(f"Note : {n}")
    if notes:
        L.append("")
    total = sum(nb for _n, _l, nb in trouvees)
    L.append(f"Erreurs trouvées dans les journaux : {total or 'aucune'}")
    for nz, lignes, nb in trouvees:
        L.append(f"  {nz} ({nb}) :")
        L += [f"    {x}" for x in lignes[-15:]]
    L += ["", "Contenu du zip"]
    for nom, contenu, note in entrees:
        L.append(f"  {nom:30} {R.taille_texte(len(contenu.encode('utf-8'))):>9}" + (f"  ({note})" if note else ""))
    L += ["", "Jamais recopiés : admin.txt, mot_de_passe.txt, cles.json, spoilers, contenu des mondes. Expurgés : mots de",
          "passe, adresses e-mail, IP publiques des joueurs (192.168.1.x : début gardé).", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m serveur.rapport",
                                 description="Rapport d'essai du serveur (un zip à envoyer au développeur)")
    ap.add_argument("action", nargs="?", default="rapport", choices=("rapport",))
    ap.add_argument("--donnees", default=os.environ.get("WWMP_DONNEES", "/donnees"),
                    help="dossier de données du serveur (défaut /donnees, celui du conteneur)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", PORT)), help="port du serveur (défaut 7777)")
    ap.add_argument("--sortie", default=None, help="dossier (défaut DONNEES/rapports) ou fichier .zip")
    ap.add_argument("--stdout", action="store_true", help="écrit le zip sur la sortie standard (docker compose exec -T)")
    ap.add_argument("--journal-stdin", action="store_true",
                    help="lit le journal de Docker sur l'entrée standard (docker compose logs … | …)")
    ap.add_argument("--sans-connexion", action="store_true", help="ne lit pas l'état du serveur qui tourne")
    a = ap.parse_args(argv)
    journal = ""
    if a.journal_stdin:
        journal = sys.stdin.buffer.read().decode("utf-8", "replace")
    if a.stdout:
        import tempfile
        d = tempfile.mkdtemp(prefix="wwmp-rapport-")
        chemin = os.path.join(d, R.nom_zip(PREFIXE))
        try:
            creer(a.donnees, a.port, chemin, journal_texte=journal, connexion=not a.sans_connexion)
            with open(chemin, "rb") as f:
                sys.stdout.buffer.write(f.read())
            sys.stdout.buffer.flush()
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        return 0
    r = creer(a.donnees, a.port, a.sortie, journal_texte=journal, connexion=not a.sans_connexion)
    print(f"Rapport d'essai du serveur : {r['chemin']} ({R.taille_texte(r['taille'])})")
    if r["chemin"].startswith("/donnees/"):
        print(f"Pour le récupérer : docker compose cp wwmp:{r['chemin']} .")
    return 0


if __name__ == "__main__":
    sys.exit(main())
