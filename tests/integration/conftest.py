"""Fixtures for the tests that need a real PostgreSQL.

**Postgres, not SQLite.** The schema uses JSONB for translated fields, partial
unique indexes so a deleted row stops reserving its slug, and
``NUMERIC(18,2)`` for money (ARCHITECTURE.md §10). SQLite honours none of the
three, so a suite that ran on it would be green about a schema that does not
exist.

**The schema is built by running the migration chain**, not by
``Base.metadata.create_all``. Clients upgrade unattended and may skip several
versions (PROJECT.md D10), so a migration that is never executed in CI is a
migration that first runs on the client's server. Running it here means every
test run is also a test of ``alembic upgrade head``.

Isolation is one transaction per test, rolled back at the end. The session is
joined to it with ``join_transaction_mode="create_savepoint"``, so a service
that calls ``commit()`` — most of them do — releases a savepoint instead of
committing for real, and the outer rollback still wipes the test's rows.

Point the suite at a database with ``TEST_DATABASE_URL``; otherwise it derives
one from the ordinary Postgres settings with the name ``b2c_test``. It is
created if it does not exist, and its schema is dropped and rebuilt at the
start of every run.
"""

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.roles import Role
from app.core.security import Audience, hash_password
from app.db.session import get_session, set_engine
from app.main import app
from app.modules.staff.models import Staff
from tests.conftest import bearer, issue_token

TEST_DATABASE_NAME = "b2c_test"
#: Connected to only to issue ``CREATE DATABASE`` — it always exists.
_MAINTENANCE_DATABASE = "postgres"


def _url_for(database: str) -> str:
    return (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{database}"
    )


def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or _url_for(TEST_DATABASE_NAME)


async def _create_database_if_missing(name: str) -> None:
    engine = create_async_engine(
        _url_for(_MAINTENANCE_DATABASE), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                # The name comes from our own settings, never from a request.
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


async def _reset_schema(engine: AsyncEngine) -> None:
    """Start from nothing, so the run does not depend on the last one."""
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[AsyncEngine]:
    """A migrated, empty database, installed as the process-wide engine."""
    url = test_database_url()

    # Alembic's env.py reads ``settings.database_url``; it is a cached_property,
    # and writing into the instance dict is the documented way to override one.
    settings.__dict__["database_url"] = url

    if not os.environ.get("TEST_DATABASE_URL"):
        await _create_database_if_missing(TEST_DATABASE_NAME)

    engine = create_async_engine(url, pool_pre_ping=True)
    await _reset_schema(engine)

    # ``command.upgrade`` is synchronous and env.py drives it with
    # ``asyncio.run``, which refuses to start inside a running loop — hence the
    # thread.
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")

    set_engine(engine)
    yield engine
    set_engine(None)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_audit_log(database: AsyncEngine) -> None:
    """Empty ``audit_log`` before each test.

    The audit middleware writes in a session of its own and commits — it has to,
    because by the time a response has a status code the request's session is
    closed (see ``modules/audit/service``). Those rows are therefore *outside*
    the test's transaction and survive its rollback, so they are cleared going
    in rather than coming out: a test that counts entries must not be counting
    the previous test's.
    """
    async with database.begin() as connection:
        await connection.execute(text("DELETE FROM audit_log"))


@pytest.fixture
async def connection(database: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One transaction per test, rolled back afterwards."""
    async with database.connect() as conn:
        transaction = await conn.begin()
        yield conn
        await transaction.rollback()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with factory() as db_session:
        yield db_session


@pytest.fixture
async def api(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A client whose requests run inside the test's own transaction.

    Without the override the app would open its own connection, and rows the
    test just inserted — uncommitted — would be invisible to the handler.
    """

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


# --- staff fixtures ---------------------------------------------------------------

#: Long enough to pass ``MIN_PASSWORD_LENGTH``; the same one everywhere so a
#: test that logs in reads as "the right password", not as a magic string.
PASSWORD = "correct-horse-battery"


async def make_staff(
    session: AsyncSession,
    *,
    email: str,
    role: Role,
    name: str = "Test Person",
    password: str = PASSWORD,
    is_blocked: bool = False,
) -> Staff:
    staff = Staff(
        email=email,
        name=name,
        role=role,
        password_hash=hash_password(password),
        is_blocked=is_blocked,
    )
    session.add(staff)
    await session.commit()
    await session.refresh(staff)
    return staff


def headers_for(staff: Staff) -> dict[str, str]:
    """An access token for an employee who really exists."""
    return bearer(issue_token(Audience.ADMIN, subject_id=staff.id, role=staff.role))


@pytest.fixture
async def owner(session: AsyncSession) -> Staff:
    return await make_staff(session, email="owner@example.uz", role=Role.OWNER)


@pytest.fixture
async def admin(session: AsyncSession) -> Staff:
    return await make_staff(session, email="admin@example.uz", role=Role.ADMIN)
