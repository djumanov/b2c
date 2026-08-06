"""AES-GCM encryption for the secrets held in the database.

GTS and payment-provider credentials live in the database so the client can
change them from the panel without a deploy — which means they must be
encrypted at rest and must never come back out of the API in full (API.md §29).

**Why every ciphertext carries its key version.** If a row only stored the
ciphertext, rotating the encryption key would mean re-entering every
credential by hand, coordinated with a deploy. Storing ``key_version``
alongside lets old and new keys coexist: rotation is "add a key, bump the
active version", and rows re-encrypt lazily when they are next written
(PROJECT.md §13, §17).

Ciphertext layout: ``nonce (12 bytes) || AES-GCM ciphertext+tag``, base64 for
storage in a text column.
"""

import base64
import os
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Read through the module rather than binding the singleton at import: the key
# ring is the one setting that legitimately changes underneath a running
# process during a rotation drill, and tests exercise exactly that.
from app.core import config

NONCE_LENGTH: Final = 12


class CryptoError(Exception):
    """Encryption is unconfigured, or a ciphertext could not be decrypted."""


def _key(version: int) -> bytes:
    ring = config.settings.encryption_key_ring
    if not ring:
        raise CryptoError(
            "ENCRYPTION_KEYS is not configured — integration credentials cannot "
            "be read or written. See .env.sample."
        )
    try:
        return ring[version]
    except KeyError:
        raise CryptoError(
            f"No encryption key for version {version}. It must stay listed in "
            "ENCRYPTION_KEYS for as long as any row still uses it; removing it "
            "makes those rows unreadable."
        ) from None


def encrypt(plaintext: str) -> tuple[str, int]:
    """Encrypt with the active key. Returns ``(ciphertext, key_version)``."""
    version = config.settings.encryption_key_version
    aesgcm = AESGCM(_key(version))
    nonce = os.urandom(NONCE_LENGTH)
    sealed = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + sealed).decode(), version


def decrypt(ciphertext: str, key_version: int) -> str:
    """Decrypt a value written with ``key_version``."""
    aesgcm = AESGCM(_key(key_version))
    try:
        raw = base64.b64decode(ciphertext, validate=True)
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"stored value is not valid base64: {exc}") from exc
    if len(raw) <= NONCE_LENGTH:
        raise CryptoError("stored value is too short to contain a nonce")
    try:
        return aesgcm.decrypt(raw[:NONCE_LENGTH], raw[NONCE_LENGTH:], None).decode()
    except InvalidTag as exc:
        raise CryptoError(
            f"could not decrypt with key version {key_version} — wrong key, or "
            "the stored value was tampered with"
        ) from exc


def needs_reencryption(key_version: int) -> bool:
    """True when a row still uses an older key and should be rewritten."""
    return key_version != config.settings.encryption_key_version


def mask_secret(value: str, *, visible: int = 4) -> str:
    """Mask a secret for display: ``"••••••7c"`` (API.md §29).

    Secrets are **never** returned in full. Short values are masked entirely
    rather than leaking a meaningful fraction of themselves.
    """
    if not value:
        return ""
    # ``visible=0`` has to be its own branch: ``value[-0:]`` is ``value[0:]``,
    # so the general expression below would append the whole secret to its own
    # mask. A caller asking for nothing to show must get nothing shown.
    if visible <= 0 or len(value) <= visible * 2:
        return "•" * len(value)
    return "•" * (len(value) - visible) + value[-visible:]


__all__ = [
    "NONCE_LENGTH",
    "CryptoError",
    "decrypt",
    "encrypt",
    "mask_secret",
    "needs_reencryption",
]
