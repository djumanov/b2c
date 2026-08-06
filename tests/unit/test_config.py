"""The rule that keeps installations from drifting apart (PROJECT.md §7).

Anything a client could want different belongs in the database, behind the
panel. This test is the tripwire: adding a client-specific field to ``Settings``
fails here, at review time, rather than the day a client asks why their colour
change needs a deploy.
"""

import pytest

from app.core.config import PLACEHOLDERS, Settings

#: What a development checkout looks like. The placeholder check is skipped
#: under ``debug``, so the tests below that are about something else say so once
#: here rather than each carrying a secret of its own.
DEV = {"debug": True}

#: Every field ``Settings`` is allowed to have. Infrastructure only.
ALLOWED_FIELDS = {
    "debug",
    "log_level",
    "app_version",
    "postgres_host",
    "postgres_port",
    "postgres_user",
    "postgres_password",
    "postgres_db",
    "redis_host",
    "redis_port",
    "redis_db",
    "jwt_secret_key",
    "encryption_keys",
    "encryption_key_version",
    "first_owner_email",
    "first_owner_password",
    "first_owner_name",
}


def test_settings_holds_infrastructure_only() -> None:
    unexpected = set(Settings.model_fields) - ALLOWED_FIELDS

    assert unexpected == set(), (
        f"{unexpected} looks client-specific. Configuration a client can change "
        "belongs in the database, not the environment (PROJECT.md §7)."
    )


def test_no_branding_or_credential_fields_leaked_in() -> None:
    """A blunter version of the same rule, by keyword."""
    forbidden = ("logo", "color", "brand", "domain", "timezone", "cors", "gts", "smtp")

    for field in Settings.model_fields:
        assert not any(word in field for word in forbidden), field


def test_database_url_is_derived_not_configured() -> None:
    settings = Settings(
        **DEV,
        postgres_host="db",
        postgres_port=5433,
        postgres_user="u",
        postgres_password="p",
        postgres_db="b2c",
    )

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5433/b2c"


def test_redis_url_is_derived() -> None:
    settings = Settings(**DEV, redis_host="cache", redis_port=6380, redis_db=2)

    assert settings.redis_url == "redis://cache:6380/2"


def test_log_level_is_normalised() -> None:
    assert Settings(**DEV, log_level="debug").log_level == "DEBUG"


# --- the values `.env.sample` publishes ----------------------------------------------


PRODUCTION = {
    "debug": False,
    "jwt_secret_key": "a-real-key-of-at-least-thirty-two-characters",
    "postgres_password": "a-real-password",
    "first_owner_password": "a-real-password",
}


@pytest.mark.parametrize("field", sorted(PLACEHOLDERS))
def test_a_published_placeholder_refuses_to_start(field: str) -> None:
    """`.env.sample` is in the repository. Its values are not secrets.

    The signing key is the sharp one: with it anybody mints an `aud: admin`,
    `role: owner` token and the whole panel is theirs. A boot that fails with a
    message is the cheapest possible version of finding that out.
    """
    with pytest.raises(ValueError, match=field.upper()):
        Settings(**{**PRODUCTION, field: PLACEHOLDERS[field]})


@pytest.mark.parametrize("field", sorted(PLACEHOLDERS))
def test_a_placeholder_is_tolerated_in_development(field: str) -> None:
    """A checkout should run with no ceremony; only a client's server is real."""
    Settings(**{**PRODUCTION, **DEV, field: PLACEHOLDERS[field]})


def test_a_short_signing_key_refuses_to_start() -> None:
    """Replaced, but with something worth guessing."""
    with pytest.raises(ValueError, match="JWT_SECRET_KEY"):
        Settings(**{**PRODUCTION, "jwt_secret_key": "hunter2"})


def test_a_configured_installation_starts() -> None:
    assert Settings(**PRODUCTION).debug is False
