"""Shared fixtures.

Redis is faked for every test — the contract and unit suites must run in CI
with no services up. PostgreSQL is only needed by the tests that say so.
"""

import uuid
from collections.abc import AsyncIterator, Iterator

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.roles import Role
from app.core.security import Audience, TokenType, create_token
from app.db import redis as redis_module
from app.main import app


@pytest.fixture(autouse=True)
def fake_redis() -> Iterator[fakeredis.aioredis.FakeRedis]:
    """Install a fake Redis for the duration of a test.

    Autouse: rate limiting is attached at the router level, so *every* request
    through the app touches Redis. A test that forgot to ask for this fixture
    would fail on a connection error rather than on what it is testing.
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_module.set_redis(client)
    yield client
    redis_module.set_redis(None)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


def issue_token(
    audience: Audience,
    *,
    subject_id: uuid.UUID | None = None,
    role: Role | str | None = None,
    token_type: TokenType = TokenType.ACCESS,
) -> str:
    """Mint a token without going through a login.

    ``role`` also takes a raw string, so a test can mint a token carrying a
    role the enum no longer has and assert it is refused. ``subject_id``
    defaults to a fresh UUID, which is what the rejection tests want — no such
    employee exists, and none of them get far enough to look.
    """
    token, _ = create_token(
        subject_id=subject_id or uuid.uuid4(),
        audience=audience,
        token_type=token_type,
        role=str(role) if role else None,
    )
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def staff_headers() -> dict[str, str]:
    """An ``owner`` token for an employee who does not exist.

    Good enough for every check that happens before the row is loaded — which
    is all of them in the contract suite. A token that must survive
    ``current_staff`` needs a real row; see the integration suite.
    """
    return bearer(issue_token(Audience.ADMIN, role=Role.OWNER))


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return bearer(issue_token(Audience.ADMIN, role=Role.ADMIN))


@pytest.fixture
def customer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_token(Audience.PUBLIC)}"}
