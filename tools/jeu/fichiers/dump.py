"""Contrôle du dump de l'utilisateur (LECTURE SEULE) : Wind Waker HD USA v0.

* ``meta/meta.xml`` : title_id 0005000010143500, région 00000002, title_version 0 ;
* chaque fichier source lu par le générateur : taille et SHA-256 comparés à ``empreintes_usa_v0.json`` (des
  EMPREINTES, jamais des fichiers du jeu), produit une fois depuis un vrai dump par
  ``python tools/rando/fichiers.py --relever-empreintes <dump>``.

Même dump USA v0 + même plan + mêmes outils = mêmes fichiers générés sur tous les PC.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from jeu.fichiers import ErreurFormat

ICI = os.path.dirname(os.path.abspath(__file__))
FICHIER_EMPREINTES = os.path.join(ICI, "empreintes_usa_v0.json")
TITLE_ID = "0005000010143500"
REGION = "00000002"
VERSION_TITRE = 0
# fichiers relevés : tout ce qu'un randomizer par fichiers peut lire (packs, stages ; lot 8 : Misc.szs, qui porte
# cmapdat.bin, les cartes de la carte marine)
RELEVES = ("content/Common/Pack", "content/Common/Stage", "content/Common/Misc")


class ErreurDump(ErreurFormat):
    """Le dossier n'est pas un dump USA v0 utilisable (message FR / EN dans ``fr`` / ``en``)."""

    def __init__(self, fr: str, en: str):
        super().__init__(fr)
        self.fr, self.en = fr, en


def sha256_octets(d: bytes) -> str:
    return hashlib.sha256(d).hexdigest()


def sha256_fichier(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def lire_meta(jeu: str) -> dict:
    p = os.path.join(jeu, "meta", "meta.xml")
    try:
        with open(p, "rb") as f:
            t = f.read(1 << 20).decode("utf-8", "replace")
    except OSError:
        raise ErreurDump(f"dossier du jeu sans meta/meta.xml : {jeu} (choisis le dossier qui contient code, "
                         "content et meta)",
                         f"game folder without meta/meta.xml: {jeu} (pick the folder that holds code, content "
                         "and meta)") from None
    out = {}
    for cle in ("title_id", "region", "title_version", "product_code"):
        m = re.search(rf"<{cle}\b[^>]*>([^<]*)</{cle}>", t)
        out[cle] = m.group(1).strip() if m else ""
    return out


def verifier_meta(jeu: str) -> dict:
    m = lire_meta(jeu)
    if m["title_id"].lower() != TITLE_ID.lower():
        raise ErreurDump(f"ce dossier n'est pas Wind Waker HD USA (title_id {m['title_id'] or '?'}, attendu "
                         f"{TITLE_ID})",
                         f"this folder is not Wind Waker HD USA (title_id {m['title_id'] or '?'}, expected "
                         f"{TITLE_ID})")
    if m["region"].lower() != REGION:
        raise ErreurDump(f"ce dump n'est pas la version USA (région {m['region'] or '?'})",
                         f"this dump is not the USA version (region {m['region'] or '?'})")
    try:
        v = int(m["title_version"] or "-1")
    except ValueError:
        v = -1
    if v != VERSION_TITRE:
        raise ErreurDump(f"ce dump n'est pas la version v0 (version {m['title_version'] or '?'}) : seule la v0 "
                         "est prise en charge",
                         f"this dump is not version v0 (version {m['title_version'] or '?'}): only v0 is "
                         "supported")
    return m


def charger_empreintes(chemin: str | None = None) -> dict:
    with open(chemin or FICHIER_EMPREINTES, encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict) or not isinstance(doc.get("fichiers"), dict):
        raise ErreurFormat("empreintes_usa_v0.json illisible")
    return doc


def empreinte_liste(doc: dict | None = None) -> str:
    """SHA-256 de la liste d'empreintes elle-même (forme canonique) : entre dans l'empreinte du plan."""
    doc = charger_empreintes() if doc is None else doc
    t = json.dumps(doc["fichiers"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def verifier_fichier(rel: str, data: bytes, doc: dict | None = None) -> None:
    """Le fichier source ``rel`` lu dans le dump est-il celui de l'USA v0 ? Lève ErreurDump sinon."""
    doc = charger_empreintes() if doc is None else doc
    e = doc["fichiers"].get(rel)
    if e is None:
        raise ErreurDump(f"fichier sans empreinte connue : {rel} (outils incomplets ?)",
                         f"file without a known fingerprint: {rel} (incomplete tools?)")
    if len(data) != e.get("taille") or sha256_octets(data) != e.get("sha256"):
        raise ErreurDump(f"votre dump n'est pas l'USA v0 attendu : {rel} diffère (fichier abîmé, autre version, "
                         "ou déjà modifié ?)",
                         f"your dump is not the expected USA v0: {rel} differs (damaged file, other version, or "
                         "already modified?)")


def relever(jeu: str) -> dict:
    """Empreintes de tous les fichiers de RELEVES dans le dump (lecture seule), forme de empreintes_usa_v0.json."""
    verifier_meta(jeu)
    out = {}
    for d in RELEVES:
        base = os.path.join(jeu, *d.split("/"))
        for dp, _dn, fn in os.walk(base):
            for f in fn:
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, jeu).replace("\\", "/")
                out[rel] = {"taille": os.path.getsize(p), "sha256": sha256_fichier(p)}
    return {"jeu": "Wind Waker HD", "title_id": TITLE_ID, "region": REGION, "version": VERSION_TITRE,
            "note": "Empreintes SHA-256 des fichiers du dump USA v0 (aucun fichier du jeu ici).",
            "fichiers": dict(sorted(out.items()))}
