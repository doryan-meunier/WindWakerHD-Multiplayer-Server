"""Signatures Ed25519 des mises à jour (docs/joueurs/MISES_A_JOUR.md).

Utilise la bibliothèque ``cryptography`` si elle est installée, sinon commun/ed25519.py (Python pur,
même résultat) : aucun module à installer chez les amis.
"""
from __future__ import annotations

from commun import ed25519 as _pur

try:  # pragma: no cover - dépend de la machine
    from cryptography.exceptions import InvalidSignature as _Invalide
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Priv
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey as _Pub
    from cryptography.hazmat.primitives import serialization as _ser
    MOTEUR = "cryptography"
except Exception:  # pragma: no cover
    _Priv = _Pub = _Invalide = _ser = None
    MOTEUR = "python"


def publique(graine: bytes) -> bytes:
    """Clé publique (32 octets) d'une graine privée de 32 octets."""
    if len(graine) != 32:
        raise ValueError("clé privée Ed25519 : 32 octets")
    if _Priv is not None:
        return _Priv.from_private_bytes(graine).public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw)
    return _pur.publique(graine)


def signer(graine: bytes, message: bytes) -> bytes:
    if len(graine) != 32:
        raise ValueError("clé privée Ed25519 : 32 octets")
    if _Priv is not None:
        return _Priv.from_private_bytes(graine).sign(message)
    return _pur.signer(graine, message)


def verifier(pub: bytes, message: bytes, sig: bytes, pur: bool = False) -> bool:
    """Vrai seulement si ``sig`` est une signature valide de ``message`` par ``pub``. Jamais d'exception."""
    try:
        if not isinstance(pub, (bytes, bytearray)) or not isinstance(sig, (bytes, bytearray)):
            return False
        if len(pub) != 32 or len(sig) != 64:
            return False
        if _Pub is not None and not pur:
            try:
                _Pub.from_public_bytes(bytes(pub)).verify(bytes(sig), bytes(message))
                return True
            except _Invalide:
                return False
        return _pur.verifier(bytes(pub), bytes(message), bytes(sig))
    except Exception:
        return False
