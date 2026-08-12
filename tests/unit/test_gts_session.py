"""The GTS session manager — one signed-in machine account, whoever asks.

What is being pinned down: the session lives in Redis under a key derived
from the credential row (so switching credentials needs no invalidation), the
sign-in happens **under a lock** (so a burst of workers is one login, not
many), and the TTL comes from GTS's own ``timeout_minutes`` with an early
margin (so no request sets out with a key about to die).
"""

import asyncio
import datetime as dt
import uuid
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import respx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.modules.integrations.service import ActiveGtsCredential
from app.providers.gts import client as gts_client
from app.providers.gts.client import GtsSessionManager

BASE_URL = "https://gts.test"
UPDATED_AT = dt.datetime(2026, 8, 1, 12, 0, 0, tzinfo=dt.UTC)


def _credential(
    *,
    credential_id: uuid.UUID | None = None,
    updated_at: dt.datetime = UPDATED_AT,
) -> ActiveGtsCredential:
    return ActiveGtsCredential(
        id=credential_id or uuid.uuid4(),
        label="Prod agent",
        base_url=BASE_URL,
        email="agent@brand.uz",
        password="gts-secret-1a2b",
        updated_at=updated_at,
    )


def _signin_response(
    session_key: str = "f771d913342bec7e9d6572ef9c8783",
    timeout_minutes: Any = 360,
) -> dict[str, Any]:
    return {
        "status": "success",
        "message": "Все ок.",
        "code": 0,
        "data": {
            "session_key": session_key,
            "expired_time": "2026-08-01T18:00:00.000000",
            "timeout_minutes": timeout_minutes,
        },
    }


def _mock_signin(**kwargs: Any) -> respx.Route:
    return respx.post(f"{BASE_URL}/v1/auth/signin/").mock(
        return_value=httpx.Response(200, json=_signin_response(**kwargs))
    )


# --- the cache -----------------------------------------------------------------------


@respx.mock
async def test_a_cached_session_costs_no_http(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    credential = _credential()
    key = f"gts:session:{credential.id}:{credential.updated_at.isoformat()}"
    await fake_redis.set(key, "cached-session-key")
    route = _mock_signin()

    token = await GtsSessionManager(credential).token()

    assert token == "cached-session-key"
    assert route.call_count == 0


@respx.mock
async def test_a_miss_signs_in_once_and_writes_the_derived_key(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    credential = _credential()
    route = _mock_signin()

    token = await GtsSessionManager(credential).token()

    assert token == "f771d913342bec7e9d6572ef9c8783"
    assert route.call_count == 1
    key = f"gts:session:{credential.id}:{credential.updated_at.isoformat()}"
    assert await fake_redis.get(key) == token
    # 360 minutes, minus the five-minute early-expiry margin.
    assert await fake_redis.ttl(key) == 360 * 60 - 300


@respx.mock
async def test_the_signin_carries_the_credential_and_no_cookie(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    route = _mock_signin()

    await GtsSessionManager(_credential()).token()

    request = route.calls.last.request
    import json

    assert json.loads(request.content) == {
        "email": "agent@brand.uz",
        "password": "gts-secret-1a2b",
    }
    assert "cookie" not in request.headers


@respx.mock
async def test_garbage_timeout_minutes_falls_back_to_thirty_minutes(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    credential = _credential()
    _mock_signin(timeout_minutes="soon")

    await GtsSessionManager(credential).token()

    key = f"gts:session:{credential.id}:{credential.updated_at.isoformat()}"
    assert await fake_redis.ttl(key) == 30 * 60


@respx.mock
async def test_a_changed_password_means_a_different_key(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """``updated_at`` is in the key, so a stale session cannot be reused."""
    credential_id = uuid.uuid4()
    old = _credential(credential_id=credential_id)
    await fake_redis.set(
        f"gts:session:{credential_id}:{old.updated_at.isoformat()}", "stale-key"
    )
    route = _mock_signin()

    new = _credential(
        credential_id=credential_id,
        updated_at=UPDATED_AT + dt.timedelta(minutes=5),
    )
    token = await GtsSessionManager(new).token()

    assert token != "stale-key"
    assert route.call_count == 1


@respx.mock
async def test_invalidate_deletes_the_key(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    credential = _credential()
    key = f"gts:session:{credential.id}:{credential.updated_at.isoformat()}"
    await fake_redis.set(key, "doomed")

    await GtsSessionManager(credential).invalidate()

    assert await fake_redis.get(key) is None


# --- the lock ------------------------------------------------------------------------


@respx.mock
async def test_concurrent_callers_produce_one_login(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """The whole point of the lock: a burst is one sign-in, not a burst."""
    route = _mock_signin()
    manager = GtsSessionManager(_credential())

    tokens = await asyncio.gather(*(manager.token() for _ in range(5)))

    assert set(tokens) == {"f771d913342bec7e9d6572ef9c8783"}
    assert route.call_count == 1


@respx.mock
async def test_a_loser_returns_the_winners_key_without_logging_in(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    credential = _credential()
    key = f"gts:session:{credential.id}:{credential.updated_at.isoformat()}"
    lock = f"gts:session:lock:{credential.id}:{credential.updated_at.isoformat()}"
    await fake_redis.set(lock, "1")  # somebody else is signing in
    route = _mock_signin()

    async def winner_finishes() -> None:
        await asyncio.sleep(0.3)
        await fake_redis.set(key, "the-winners-key")

    token, _ = await asyncio.gather(
        GtsSessionManager(credential).token(), winner_finishes()
    )

    assert token == "the-winners-key"
    assert route.call_count == 0


@respx.mock
async def test_a_dead_winner_does_not_strand_the_losers(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """The lock has a TTL; when it expires a loser takes over and signs in."""
    credential = _credential()
    lock = f"gts:session:lock:{credential.id}:{credential.updated_at.isoformat()}"
    await fake_redis.set(lock, "1", px=300)  # dies long before the deadline
    route = _mock_signin()

    token = await GtsSessionManager(credential).token()

    assert token == "f771d913342bec7e9d6572ef9c8783"
    assert route.call_count == 1


@respx.mock
async def test_waiting_past_the_deadline_is_a_504(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = _credential()
    lock = f"gts:session:lock:{credential.id}:{credential.updated_at.isoformat()}"
    await fake_redis.set(lock, "1")  # held forever, key never appears
    monkeypatch.setattr(gts_client, "_WAIT_DEADLINE_SECONDS", 0.5)

    with pytest.raises(UpstreamTimeout):
        await GtsSessionManager(credential).token()


# --- sign-in failures ----------------------------------------------------------------


@respx.mock
async def test_a_rejected_signin_is_an_upstream_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    respx.post(f"{BASE_URL}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "message": "Неверный логин или пароль",
                "code": -3,
                "data": None,
            },
        )
    )

    with pytest.raises(UpstreamError) as caught:
        await GtsSessionManager(_credential()).token()

    assert caught.value.message == "Неверный логин или пароль"
    assert caught.value.meta == {
        "upstream": {"code": -3, "message": "Неверный логин или пароль"}
    }


@respx.mock
async def test_a_signin_without_a_session_key_is_an_upstream_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    respx.post(f"{BASE_URL}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "message": "ok", "code": 0, "data": {}}
        )
    )

    with pytest.raises(UpstreamError):
        await GtsSessionManager(_credential()).token()


@respx.mock
async def test_a_failed_signin_releases_the_lock_for_the_next_caller(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    credential = _credential()
    route = respx.post(f"{BASE_URL}/v1/auth/signin/").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=_signin_response()),
        ]
    )
    manager = GtsSessionManager(credential)

    with pytest.raises(UpstreamError):
        await manager.token()
    token = await manager.token()

    assert token == "f771d913342bec7e9d6572ef9c8783"
    assert route.call_count == 2


@respx.mock
async def test_a_signin_timeout_is_a_504(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    respx.post(f"{BASE_URL}/v1/auth/signin/").mock(side_effect=httpx.ConnectTimeout)

    with pytest.raises(UpstreamTimeout):
        await GtsSessionManager(_credential()).token()
