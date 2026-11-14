"""Ed25519 en Python pur (signature des mises à jour, commun/signature.py).

Écrit d'après la RFC 8032 (§5.1, coordonnées étendues) et l'implémentation de référence
``ed25519.py`` de D. J. Bernstein, N. Duif, T. Lange, P. Schwabe et B.-Y. Yang (domaine public,
https://ed25519.cr.yp.to/software.html). Ce module est lui aussi placé dans le domaine public.

Il ne sert que si la bibliothèque ``cryptography`` est absente : commun/signature.py la préfère
quand elle est installée (même résultat, plus rapide). Ici, pas de temps constant : c'est sans
importance pour VÉRIFIER une signature (rien de secret) ; pour SIGNER (wwmp_publier.py, sur le PC
du propriétaire seulement), la clé privée ne quitte pas la machine.

Vérification stricte : S < L, points bien encodés (y < p), sinon refus. Vecteurs de test RFC 8032
§7.1 dans ``VECTEURS`` (tests/test_mises_a_jour.py les vérifie).

    publique(graine32) -> 32 octets
    signer(graine32, message) -> 64 octets
    verifier(publique32, message, signature64) -> bool
"""
from __future__ import annotations

import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
D2 = (2 * D) % P
RAC_M1 = pow(2, (P - 1) // 4, P)          # racine carrée de -1 modulo p


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _x_depuis_y(y: int, signe: int) -> int | None:
    """Abscisse du point d'ordonnée y (RFC 8032 §5.1.3), None si y n'est pas sur la courbe."""
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0:
        return None if signe else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * RAC_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != signe:
        x = P - x
    return x


_BY = 4 * _inv(5) % P
_BX = _x_depuis_y(_BY, 0)
B = (_BX, _BY, 1, _BX * _BY % P)           # point de base, coordonnées étendues (X, Y, Z, T)
NEUTRE = (0, 1, 1, 0)


def _ajouter(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = t1 * D2 * t2 % P
    d = z1 * 2 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _doubler(p):
    x1, y1, z1, _t1 = p
    a = x1 * x1 % P
    b = y1 * y1 % P
    c = 2 * z1 * z1 % P
    h = a + b
    e = h - (x1 + y1) * (x1 + y1)
    g = a - b
    f = c + g
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _multiplier(k: int, p):
    q = NEUTRE
    while k > 0:
        if k & 1:
            q = _ajouter(q, p)
        p = _doubler(p)
        k >>= 1
    return q


def _egaux(p, q) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % P == 0 and (y1 * z2 - y2 * z1) % P == 0


def _encoder(p) -> bytes:
    x, y, z, _ = p
    zi = _inv(z)
    x, y = x * zi % P, y * zi % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decoder(s: bytes):
    if len(s) != 32:
        return None
    n = int.from_bytes(s, "little")
    y, signe = n & ((1 << 255) - 1), n >> 255
    x = _x_depuis_y(y, signe)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _h(*parts: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def _secret(graine: bytes) -> tuple[int, bytes]:
    if len(graine) != 32:
        raise ValueError("graine Ed25519 : 32 octets")
    h = hashlib.sha512(graine).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def publique(graine: bytes) -> bytes:
    a, _ = _secret(graine)
    return _encoder(_multiplier(a, B))


def signer(graine: bytes, message: bytes) -> bytes:
    a, prefixe = _secret(graine)
    pub = _encoder(_multiplier(a, B))
    r = _h(prefixe, message) % L
    rs = _encoder(_multiplier(r, B))
    k = _h(rs, pub, message) % L
    s = (r + k * a) % L
    return rs + int.to_bytes(s, 32, "little")


def verifier(pub: bytes, message: bytes, signature: bytes) -> bool:
    if not isinstance(pub, (bytes, bytearray)) or not isinstance(signature, (bytes, bytearray)):
        return False
    if len(pub) != 32 or len(signature) != 64:
        return False
    a = _decoder(bytes(pub))
    r = _decoder(bytes(signature[:32]))
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    k = _h(bytes(signature[:32]), bytes(pub), bytes(message)) % L
    return _egaux(_multiplier(s, B), _ajouter(r, _multiplier(k, a)))


# RFC 8032 §7.1 : (graine, publique, message, signature), en hexadécimal
VECTEURS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
     "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
     "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
     "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]
