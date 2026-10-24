"""Signed verdicts: every card carries a QR that proves it was issued by Pramaan.

A scammer can screenshot a green card and edit it, but cannot produce a valid
signature for the edited content. Scanning the QR opens our verify page, which
checks the signature and shows what Pramaan actually said.

Prototype: Ed25519 key on the server (data/keys/, never committed).
Production design: the same interface backed by AWS KMS (asymmetric, ECC).
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from app.config import DATA_DIR

KEY_PATH = DATA_DIR / "keys" / "verdict_ed25519.pem"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@lru_cache(maxsize=1)
def _key() -> Ed25519PrivateKey:
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    k = Ed25519PrivateKey.generate()
    KEY_PATH.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return k


def public_key_pem() -> str:
    return _key().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def key_id() -> str:
    raw = _key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:12]


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(payload: dict) -> str:
    return _b64(_key().sign(canonical(payload)))


def verify(payload: dict, signature: str, public: Ed25519PublicKey | None = None) -> bool:
    try:
        (public or _key().public_key()).verify(_unb64(signature), canonical(payload))
        return True
    except (InvalidSignature, ValueError):
        return False
