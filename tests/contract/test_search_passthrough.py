"""D2, mechanically: **search results are never stored** — the acceptance
test PHASES.md §4 names.

The claim is bigger than "we did not mean to cache": after a full search →
offers round trip, Redis may hold only the things the platform itself needs —
the site-config document, rate-limit counters, the GTS session — and GTS's
``request_id`` must appear in **no key and no value**. A cache of ours would
have to leave a trace here; this sweep is how it would be caught.

No Postgres: the credential lookup is patched at its documented seam
(``integrations.service.active_credential``), everything else is real.
"""

import datetime as dt
import re
import uuid
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
import respx
from httpx import AsyncClient

from app.modules.integrations import service as integrations_service
from app.modules.integrations.service import ActiveGtsCredential
from app.modules.products import service as products_service
from app.modules.settings import cache as settings_cache

SEARCH = "/api/v1/public/flight/search/"
OFFERS = "/api/v1/public/flight/offers/"
GTS = "https://gts.test"
REQUEST_ID = "6c62dcec-9334-11ee-8688-5169d0acfb81"

SEARCH_BODY: dict[str, Any] = {
    "directions": [
        {"departure": "TAS", "arrival": "IST", "departure_date": "2026-09-14"}
    ],
    "adt": 1,
    "chd": 0,
    "inf": 0,
    "ins": 0,
    "class": "E",
    "direct": False,
}

#: What the platform itself is allowed to keep in Redis on the search path
#: (app/db/redis.py's own inventory).
ALLOWED_KEY = re.compile(r"^(site-config$|ratelimit:|gts:session:)")


@pytest.fixture
async def gts_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> ActiveGtsCredential:
    """An installation with flight enabled and a credential, minus Postgres."""
    credential = ActiveGtsCredential(
        id=uuid.uuid4(),
        label="Prod agent",
        base_url=GTS,
        email="agent@brand.uz",
        password="gts-secret-1a2b",
        agent_uid=None,
        updated_at=dt.datetime(2026, 8, 1, 12, 0, 0, tzinfo=dt.UTC),
    )

    async def fixed_credential(session: object) -> ActiveGtsCredential:
        return credential

    # Patched where the products service looks it up — the seam STATUS.md
    # documents, not an internal.
    monkeypatch.setattr(
        products_service.integrations_service, "active_credential", fixed_credential
    )
    assert products_service.integrations_service is integrations_service
    await settings_cache.write({"products": [{"code": "flight", "enabled": True}]})
    return credential


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


def _mock_gts() -> respx.Route:
    respx.post(f"{GTS}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope({"session_key": "session-abc", "timeout_minutes": 360}),
        )
    )
    search_route = respx.post(f"{GTS}/v1/content/search/").mock(
        return_value=httpx.Response(200, json=_envelope({"request_id": REQUEST_ID}))
    )
    respx.post(f"{GTS}/v1/content/offers/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                {
                    "request_id": REQUEST_ID,
                    "next_token": None,
                    "count": 1,
                    "offers": [{"offer_id": "o-1"}],
                }
            ),
        )
    )
    return search_route


@respx.mock
async def test_the_request_id_passes_through_and_is_stored_nowhere(
    client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
    gts_installation: ActiveGtsCredential,
) -> None:
    _mock_gts()

    search = await client.post(SEARCH, json=SEARCH_BODY)
    offers = await client.post(
        OFFERS, json={"request_id": REQUEST_ID, "next_token": None, "limit": 20}
    )

    # Byte-for-byte passthrough on the way out.
    assert search.status_code == 200
    assert search.json()["data"] == {"request_id": REQUEST_ID}
    assert offers.status_code == 200
    assert offers.json()["data"]["offers"] == [{"offer_id": "o-1"}]

    # ...and no trace on the way down: only the platform's own keys exist,
    # and the request_id is in none of them, key or value.
    keys = await fake_redis.keys("*")
    for key in keys:
        assert ALLOWED_KEY.match(key), f"unexpected Redis key after a search: {key}"
        assert REQUEST_ID not in key
        value = await fake_redis.get(key)
        if value is not None:
            assert REQUEST_ID not in value


@respx.mock
async def test_two_identical_searches_both_reach_gts(
    client: AsyncClient,
    gts_installation: ActiveGtsCredential,
) -> None:
    """No cache of ours may answer a search — the inverse of the catalog."""
    route = _mock_gts()

    first = await client.post(SEARCH, json=SEARCH_BODY)
    second = await client.post(SEARCH, json=SEARCH_BODY)

    assert first.status_code == second.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    assert route.call_count == 2
