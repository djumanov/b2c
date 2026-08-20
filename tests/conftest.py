"""Test fixtures: throwaway database, fake Redis, ASGI client, seeds.

The environment is pinned **before the first ``app.`` import** —
``app.core.config`` instantiates its ``settings`` singleton at import time,
and pydantic-settings lets a real environment variable beat ``.env``, which is
how the suite points the whole process at ``b2c_test`` without touching the
developer's file.

GTS is faked with ``respx``, which patches httpx's real network transports;
the test client below speaks ASGI through an explicit ``ASGITransport``, which
respx leaves alone — so an API request runs the full app while its outbound
GTS calls hit the mocks.
"""

import getpass
import os
import subprocess
import sys

# --- environment first: ``app.core.config`` builds its singleton on import ---
os.environ["DEBUG"] = "true"
os.environ["POSTGRES_DB"] = "b2c_test"
os.environ.setdefault("POSTGRES_USER", getpass.getuser())
os.environ.setdefault("POSTGRES_PASSWORD", "")
# A fixed key ring (base64 of 32 zero bytes) so encrypted seeds decrypt.
os.environ["ENCRYPTION_KEYS"] = "1:MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["ENCRYPTION_KEY_VERSION"] = "1"

from collections.abc import AsyncIterator  # noqa: E402
from typing import Any  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from app.core.config import settings  # noqa: E402
from app.core.crypto import encrypt  # noqa: E402
from app.core.security import (  # noqa: E402
    Audience,
    TokenType,
    create_token,
    hash_password,
)
from app.db.mixins import utcnow  # noqa: E402
from app.db.redis import set_redis  # noqa: E402
from app.db.session import get_sessionmaker, set_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.customers.models import Customer  # noqa: E402
from app.modules.integrations.models import GtsCredential  # noqa: E402
from app.modules.settings import cache as settings_cache  # noqa: E402

#: The base URL the seeded GTS credential points at — respx mocks it.
GTS = "https://gts.test"


def _prepare_database() -> None:
    """Recreate ``b2c_test`` and bring it to the migration head.

    Dropped first, not reused: a leftover test database may carry an
    ``alembic_version`` from a chain that no longer exists. Alembic runs as a
    subprocess: ``migrations/env.py`` calls ``asyncio.run``, which cannot
    happen inside pytest-asyncio's already-running loop.
    """
    subprocess.run(
        ["dropdb", "--if-exists", settings.postgres_db],
        capture_output=True,
        check=False,
    )
    subprocess.run(["createdb", settings.postgres_db], capture_output=True, check=False)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{completed.stderr}")


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    _prepare_database()


@pytest.fixture(scope="session")
async def engine(database: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url)
    set_engine(engine)
    yield engine
    set_engine(None)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    """The services genuinely commit, so isolation is cleanup, not rollback."""
    yield
    async with engine.begin() as connection:
        for table in ("order_events", "orders", "customers", "gts_credentials"):
            await connection.execute(text(f"DELETE FROM {table}"))


@pytest.fixture(autouse=True)
async def fake_redis(
    engine: AsyncEngine,
) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """A fresh fake per test — rate limits and caches never leak across tests."""
    redis = fakeredis.aioredis.FakeRedis()
    set_redis(redis)
    yield redis
    set_redis(None)


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- seeds -------------------------------------------------------------------


async def make_customer(session: AsyncSession, email: str) -> Customer:
    """A verified customer — ``get_active`` refuses unverified rows."""
    row = Customer(
        email=email,
        password_hash=hash_password("secret-password-1"),
        email_verified_at=utcnow(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def bearer(customer: Customer) -> dict[str, str]:
    token, _ = create_token(
        subject_id=customer.id,
        audience=Audience.PUBLIC,
        token_type=TokenType.ACCESS,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def customer(db_session: AsyncSession) -> Customer:
    return await make_customer(db_session, "traveller@example.com")


@pytest.fixture
def customer_headers(customer: Customer) -> dict[str, str]:
    return bearer(customer)


@pytest.fixture
async def gts_credential(db_session: AsyncSession) -> GtsCredential:
    ciphertext, key_version = encrypt("gts-secret-1a2b")
    row = GtsCredential(
        label="Test agent",
        base_url=GTS,
        email="agent@brand.uz",
        password=ciphertext,
        key_version=key_version,
        is_active=True,
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.fixture
async def flight_enabled(fake_redis: Any) -> None:
    """Seed the site-config cache so the product gate answers for ``flight``."""
    await settings_cache.write({"products": [{"code": "flight", "enabled": True}]})
