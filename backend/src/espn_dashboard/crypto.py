"""Envelope-free AES-256-GCM encryption for stored ESPN cookies.

``espn_s2`` is a live ESPN session credential, so it is encrypted before it ever
reaches Firestore and is only decrypted in-process, for the duration of a single
outbound ESPN request. The key lives in Secret Manager and is injected as
``CREDENTIAL_ENCRYPTION_KEY`` (base64, 32 bytes).

Swapping this for Cloud KMS later means reimplementing ``encrypt``/``decrypt``
against ``kms.encrypt``; nothing else in the codebase touches ciphertext.
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12
KEY_BYTES = 32


class CryptoError(RuntimeError):
    """Raised when a key is unusable or a ciphertext fails authentication."""


def load_key(encoded_key: str) -> bytes:
    if not encoded_key:
        raise CryptoError("CREDENTIAL_ENCRYPTION_KEY is not set")
    try:
        key = base64.b64decode(encoded_key, validate=True)
    except Exception as exc:  # noqa: BLE001 - surfaced as a config error
        raise CryptoError("CREDENTIAL_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != KEY_BYTES:
        raise CryptoError(f"CREDENTIAL_ENCRYPTION_KEY must decode to {KEY_BYTES} bytes")
    return key


def generate_key() -> str:
    """Base64 key suitable for ``CREDENTIAL_ENCRYPTION_KEY`` (used by tests/ops)."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode()


def encrypt(plaintext: str, encoded_key: str, *, aad: str = "") -> str:
    """Return base64(nonce || ciphertext). ``aad`` binds the blob to a SWID."""
    key = load_key(encoded_key)
    nonce = os.urandom(NONCE_BYTES)
    blob = AESGCM(key).encrypt(nonce, plaintext.encode(), aad.encode() or None)
    return base64.b64encode(nonce + blob).decode()


def decrypt(token: str, encoded_key: str, *, aad: str = "") -> str:
    key = load_key(encoded_key)
    try:
        raw = base64.b64decode(token, validate=True)
        nonce, blob = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
        return AESGCM(key).decrypt(nonce, blob, aad.encode() or None).decode()
    except CryptoError:
        raise
    except Exception as exc:  # noqa: BLE001 - do not leak cipher details
        raise CryptoError("stored credential could not be decrypted") from exc
