"""The rule that keeps installations from drifting apart (PROJECT.md §7).

Anything a client could want different belongs in the database, behind the
panel. This test is the tripwire: adding a client-specific field to ``Settings``
fails here, at review time, rather than the day a client asks why their colour
change needs a deploy.
"""

from app.core.config import Settings

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
        postgres_host="db",
        postgres_port=5433,
        postgres_user="u",
        postgres_password="p",
        postgres_db="b2c",
    )

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5433/b2c"


def test_redis_url_is_derived() -> None:
    settings = Settings(redis_host="cache", redis_port=6380, redis_db=2)

    assert settings.redis_url == "redis://cache:6380/2"


def test_log_level_is_normalised() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"
