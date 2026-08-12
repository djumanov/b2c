"""The authenticated GTS client — cookie, request id, and the 401 dance.

The envelope translation is ``static.py``'s, so only its differences are
pinned here: the session cookie on every call, ``X-Request-Id`` travelling
outward, ``data`` being a dict rather than a list, and the one automatic
retry after a session death — one, not a loop.
"""

import datetime as dt
import uuid
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import respx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.core.logging import request_id_var
from app.modules.integrations.service import ActiveGtsCredential
from app.providers.gts.client import GtsHttpClient

BASE_URL = "https://gts.test"


def _credential() -> ActiveGtsCredential:
    return ActiveGtsCredential(
        id=uuid.uuid4(),
        label="Prod agent",
        base_url=BASE_URL,
        email="agent@brand.uz",
        password="gts-secret-1a2b",
        agent_uid=None,
        updated_at=dt.datetime(2026, 8, 1, 12, 0, 0, tzinfo=dt.UTC),
    )


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


def _mock_signin(session_key: str = "session-key-1") -> respx.Route:
    return respx.post(f"{BASE_URL}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope({"session_key": session_key, "timeout_minutes": 360}),
        )
    )


# --- what every call carries ---------------------------------------------------------


@respx.mock
async def test_the_session_rides_as_a_cookie(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _mock_signin("abc123")
    route = respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(200, json=_envelope({"request_id": "r-1"}))
    )

    await GtsHttpClient(_credential()).post(
        "/v1/content/search/", json={"adt": 1}, timeout=None
    )

    assert route.calls.last.request.headers["cookie"] == "sessionid=abc123"


@respx.mock
async def test_our_request_id_travels_outward(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """ARCHITECTURE §7 — a GTS log line and ours must be matchable."""
    _mock_signin()
    route = respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(200, json=_envelope({"request_id": "r-1"}))
    )

    token = request_id_var.set("req-42")
    try:
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )
    finally:
        request_id_var.reset(token)

    assert route.calls.last.request.headers["x-request-id"] == "req-42"


@respx.mock
async def test_success_returns_the_bare_data_dict(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _mock_signin()
    respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(200, json=_envelope({"request_id": "r-7"}))
    )

    data = await GtsHttpClient(_credential()).post(
        "/v1/content/search/", json={}, timeout=None
    )

    assert data == {"request_id": "r-7"}


# --- the 401 dance -------------------------------------------------------------------


@respx.mock
async def test_a_dead_session_is_retried_exactly_once(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    signin = _mock_signin()
    route = respx.post(f"{BASE_URL}/v1/content/search/").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json=_envelope({"request_id": "r-2"})),
        ]
    )

    data = await GtsHttpClient(_credential()).post(
        "/v1/content/search/", json={}, timeout=None
    )

    assert data == {"request_id": "r-2"}
    assert route.call_count == 2
    assert signin.call_count == 2  # once before, once after the invalidation


@respx.mock
async def test_a_second_401_is_an_error_not_a_loop(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _mock_signin()
    route = respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(401)
    )

    with pytest.raises(UpstreamError) as caught:
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )

    assert route.call_count == 2
    assert caught.value.meta == {"upstream": {"code": 401}}


@respx.mock
async def test_a_403_counts_as_a_dead_session_too(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Django answers 401 or 403 depending on middleware — treat them alike."""
    _mock_signin()
    route = respx.post(f"{BASE_URL}/v1/content/search/").mock(
        side_effect=[
            httpx.Response(403),
            httpx.Response(200, json=_envelope({"request_id": "r-3"})),
        ]
    )

    data = await GtsHttpClient(_credential()).post(
        "/v1/content/search/", json={}, timeout=None
    )

    assert data == {"request_id": "r-3"}
    assert route.call_count == 2


# --- every other way it can go wrong -------------------------------------------------


@respx.mock
async def test_an_error_wearing_http_200_is_an_upstream_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _mock_signin()
    respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "message": "Ошибка сервиса",
                "code": -104,
                "data": None,
            },
        )
    )

    with pytest.raises(UpstreamError) as caught:
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )

    assert caught.value.message == "Ошибка сервиса"
    assert caught.value.meta == {
        "upstream": {"code": -104, "message": "Ошибка сервиса"}
    }


@respx.mock
async def test_a_list_shaped_message_is_flattened(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """The content service answers with ``message: [{...}]``, not a string."""
    _mock_signin()
    respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "message": [{"message": "no providers", "data": "none"}],
                "code": -21,
                "data": None,
            },
        )
    )

    with pytest.raises(UpstreamError) as caught:
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )

    assert caught.value.message == "no providers"
    assert caught.value.meta == {"upstream": {"code": -21, "message": "no providers"}}


@respx.mock
async def test_a_500_keeps_its_status_in_meta(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _mock_signin()
    respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(500, html="<h1>boom</h1>")
    )

    with pytest.raises(UpstreamError) as caught:
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )

    assert caught.value.meta == {"upstream": {"code": 500}}


@respx.mock
async def test_a_200_that_is_not_json_is_an_upstream_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _mock_signin()
    respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(200, text="<h1>maintenance</h1>")
    )

    with pytest.raises(UpstreamError):
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )


@respx.mock
async def test_data_that_is_not_a_dict_is_an_upstream_error(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """``/v1/`` answers with an object; a list here means we misread GTS."""
    _mock_signin()
    respx.post(f"{BASE_URL}/v1/content/search/").mock(
        return_value=httpx.Response(200, json=_envelope([1, 2]))
    )

    with pytest.raises(UpstreamError):
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )


@respx.mock
async def test_a_timeout_is_a_504(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _mock_signin()
    respx.post(f"{BASE_URL}/v1/content/search/").mock(side_effect=httpx.ReadTimeout)

    with pytest.raises(UpstreamTimeout):
        await GtsHttpClient(_credential()).post(
            "/v1/content/search/", json={}, timeout=None
        )
