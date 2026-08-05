"""Application configuration — infrastructure only.

Every environment variable the backend reads is declared here, once, as a
typed field on ``Settings``. Import the ``settings`` singleton everywhere;
never call ``os.getenv``. ``.env.sample`` documents each variable.

**What may live here.** Database, Redis, the JWT secret, the encryption keys,
the log level and the first-owner bootstrap. Nothing else.

Everything a client could want different — branding, languages, currencies,
timezone, allowed CORS origins, GTS and payment credentials, SMTP — is stored
in the database and edited from the panel, with no redeploy. That is the
project's headline promise (PROJECT.md §7, ARCHITECTURE.md §1); a value that
leaks into this file quietly breaks it, because changing it then needs a
deploy the client cannot perform.
"""

import base64
from functools import cached_property

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# AES-256-GCM: keys are exactly 32 bytes.
ENCRYPTION_KEY_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- app ---
    debug: bool = False
    log_level: str = "INFO"
    app_version: str = "0.1.0"

    # --- database (PostgreSQL) ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "b2c"

    # --- cache / broker (Redis) ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # --- JWT signing ---
    jwt_secret_key: str = "change-me-to-a-long-random-string"

    # --- secret encryption (AES-GCM) ---
    # "1:<base64 32 bytes>,2:<base64 32 bytes>" — every version that any stored
    # row might have been written with. Rotating the key means adding a version
    # here and bumping ``encryption_key_version``; existing rows stay readable,
    # so credentials never have to be re-entered (PROJECT.md §13).
    encryption_keys: str = ""
    encryption_key_version: int = 1

    # --- first owner (read on the very first boot, ignored afterwards) ---
    first_owner_email: str = ""
    first_owner_password: str = ""
    first_owner_name: str = "Owner"

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @cached_property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @cached_property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @cached_property
    def encryption_key_ring(self) -> dict[int, bytes]:
        """Decoded ``{version: key}`` map, validated at import time.

        Empty when unconfigured: the app still boots (migrations, health and
        every endpoint that touches no secret keep working) and ``core.crypto``
        raises a clear error the moment a secret is actually read or written.
        """
        ring: dict[int, bytes] = {}
        for entry in self.encryption_keys.split(","):
            entry = entry.strip()
            if not entry:
                continue
            version, _, encoded = entry.partition(":")
            if not _:
                raise ValueError(
                    "ENCRYPTION_KEYS entries must be 'version:base64key', "
                    f"got {entry!r}"
                )
            key = base64.b64decode(encoded, validate=True)
            if len(key) != ENCRYPTION_KEY_LENGTH:
                raise ValueError(
                    f"ENCRYPTION_KEYS version {version} must decode to "
                    f"{ENCRYPTION_KEY_LENGTH} bytes, got {len(key)}"
                )
            ring[int(version)] = key
        return ring

    @model_validator(mode="after")
    def _active_key_is_in_the_ring(self) -> "Settings":
        ring = self.encryption_key_ring
        if ring and self.encryption_key_version not in ring:
            raise ValueError(
                f"ENCRYPTION_KEY_VERSION={self.encryption_key_version} is not "
                f"among the versions in ENCRYPTION_KEYS ({sorted(ring)}). New "
                "secrets would be unencryptable."
            )
        return self


#: The one instance. Import this, not ``Settings``.
settings = Settings()


__all__ = ["Settings", "settings"]
