"""Secret encryption, and the rotation it has to survive.

The rotation test is the important one. If a stored value could only be read by
the current key, changing the key would force the client to re-enter every GTS
and payment credential by hand — the risk called out in PROJECT.md §17.
"""

import base64
import os
from collections.abc import Iterator

import pytest

from app.core import config as config_module
from app.core import crypto
from app.core.config import Settings


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


KEY_V1 = _key()
KEY_V2 = _key()


@pytest.fixture
def one_key() -> Iterator[None]:
    original = config_module.settings
    config_module.settings = Settings(
        encryption_keys=f"1:{KEY_V1}", encryption_key_version=1
    )
    yield
    config_module.settings = original


@pytest.fixture
def two_keys() -> Iterator[None]:
    """Both keys present, v2 active — the state during a rotation."""
    original = config_module.settings
    config_module.settings = Settings(
        encryption_keys=f"1:{KEY_V1},2:{KEY_V2}", encryption_key_version=2
    )
    yield
    config_module.settings = original


def test_round_trip(one_key: None) -> None:
    ciphertext, version = crypto.encrypt("merchant-secret-7c")

    assert version == 1
    assert "merchant-secret" not in ciphertext
    assert crypto.decrypt(ciphertext, version) == "merchant-secret-7c"


def test_each_encryption_is_different(one_key: None) -> None:
    """A fresh nonce every time: identical secrets must not look identical."""
    first, _ = crypto.encrypt("same")
    second, _ = crypto.encrypt("same")

    assert first != second


def test_unicode_survives(one_key: None) -> None:
    ciphertext, version = crypto.encrypt("парол — ключ")

    assert crypto.decrypt(ciphertext, version) == "парол — ключ"


def test_tampering_is_detected(one_key: None) -> None:
    ciphertext, version = crypto.encrypt("secret")
    raw = bytearray(base64.b64decode(ciphertext))
    raw[-1] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()

    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(tampered, version)


def test_rotation_keeps_old_rows_readable(
    one_key: None, request: pytest.FixtureRequest
) -> None:
    """Write with v1, rotate to v2, read the v1 row back."""
    old_ciphertext, old_version = crypto.encrypt("written-before-rotation")
    assert old_version == 1

    request.getfixturevalue("two_keys")

    assert crypto.decrypt(old_ciphertext, old_version) == "written-before-rotation"
    # New writes use the new key.
    _, new_version = crypto.encrypt("written-after")
    assert new_version == 2
    # And the old row is flagged for lazy re-encryption.
    assert crypto.needs_reencryption(old_version) is True
    assert crypto.needs_reencryption(new_version) is False


def test_dropping_a_key_that_is_still_in_use_is_a_clear_error(one_key: None) -> None:
    with pytest.raises(crypto.CryptoError, match="version 9"):
        crypto.decrypt("ZmFrZQ==", 9)


def test_unconfigured_encryption_says_so() -> None:
    original = config_module.settings
    config_module.settings = Settings(encryption_keys="")
    try:
        with pytest.raises(crypto.CryptoError, match="ENCRYPTION_KEYS"):
            crypto.encrypt("secret")
    finally:
        config_module.settings = original


def test_active_key_must_be_in_the_ring() -> None:
    """Misconfiguration fails at startup, not at the first secret write."""
    with pytest.raises(ValueError, match="ENCRYPTION_KEY_VERSION"):
        Settings(encryption_keys=f"1:{KEY_V1}", encryption_key_version=2)


def test_key_must_be_32_bytes() -> None:
    short = base64.b64encode(os.urandom(16)).decode()

    with pytest.raises(ValueError, match="32 bytes"):
        _ = Settings(encryption_keys=f"1:{short}").encryption_key_ring


@pytest.mark.parametrize(
    "value,expected",
    [
        ("abcdef1234567c", "••••••••••567c"),
        ("short", "•••••"),
        ("12345678", "••••••••"),
        ("", ""),
    ],
)
def test_mask_secret_never_shows_a_useful_fraction(value: str, expected: str) -> None:
    assert crypto.mask_secret(value) == expected
