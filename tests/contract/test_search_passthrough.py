"""D2, mechanically: **search results are never stored** — the acceptance
test PHASES.md §4 names.

The claim is bigger than "we did not mean to cache": after a full search →
offers → upsell → verify → booking → cancel round trip, Redis may hold only
the things the platform itself needs — the site-config document, rate-limit
counters, the GTS session — and GTS's ``request_id`` and ``offer_id`` must
appear in **no key and no value**.
A cache of ours would have to leave a trace here; this sweep is how it would
be caught.

Booking and cancel are in the round trip on purpose. They are the two steps
that could plausibly want to remember something, and what they may remember is
**the order, and only the order** (API.md §21): the customer's own purchase,
never the search that led to it. ``ALLOWED_KEY`` below is the whole of what may
survive them in Redis, and the idempotency record joined it when booking became
a money endpoint — a replay of *our own answer*, not a copy of GTS's offers.

That record is the one place the search identifiers may legitimately appear in
Redis: the answer it caches contains the order, and an order names the search it
came from (API.md §21). So the sweep splits — no key of any kind may carry them,
and no *value* except that record.

D2 is about *offers*, not about orders: a completed purchase is a record we owe
the customer, an offer is a cache we refuse to keep (ARCHITECTURE.md §10). So
this sweep still insists on two things — nothing of the search reaches Redis,
and the booking response gains no field of ours.

No Postgres: the credential lookup is patched at its documented seam
(``integrations.service.active_credential``), so is the fun-fact read
(``cms.service.random_fun_fact`` — the search response's one addition,
API.md §20), and so is the order write and its ownership lookup
(``orders.service``, the door ``products`` uses). Booking and cancel demand a
customer, and loading one is the other Postgres query on this path, so the
principal is overridden at the dependency rather than faked into a database;
everything else is real. What the order row actually contains is
``tests/integration/test_orders.py``'s subject, against a real database.
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

from app.api.deps import Customer, current_customer
from app.main import app
from app.modules.integrations import service as integrations_service
from app.modules.integrations.service import ActiveGtsCredential
from app.modules.products import service as products_service
from app.modules.settings import cache as settings_cache

SEARCH = "/api/v1/public/flight/search/"
OFFERS = "/api/v1/public/flight/offers/"
UPSELL = "/api/v1/public/flight/upsell/"
VERIFY = "/api/v1/public/flight/verify/"
BOOKING = "/api/v1/public/flight/booking/"
CANCEL = "/api/v1/public/flight/cancel/"
GTS = "https://gts.test"
ORDER_NUMBER = 61453
REQUEST_ID = "6c62dcec-9334-11ee-8688-5169d0acfb81"
OFFER_ID = "7cc212c0-c91d-4931-8ff6-4231b7da27c0"

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
ALLOWED_KEY = re.compile(r"^(site-config$|ratelimit:|gts:session:|idempotency:)")

#: The one key whose *value* is our own answer replayed, rather than anything
#: read from GTS (API.md §10).
REPLAY_KEY = re.compile(r"^idempotency:")

#: GTS's booking answer, complete enough for the adapter to read an order out
#: of it — without a price it would refuse, and this suite would be exercising
#: the wrong branch.
GTS_BOOKING: dict[str, Any] = {
    "message": "booked",
    "request_id": REQUEST_ID,
    "data": {
        "order_number": ORDER_NUMBER,
        "status": "BO",
        "price_info": {"price": 100, "currency": "UZS"},
    },
}


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

    # The fun-fact read is the search path's only Postgres query — patched at
    # the same kind of seam, so this suite stays databaseless.
    async def no_fun_fact(session: object, *, requested: str | None = None) -> None:
        return None

    monkeypatch.setattr(products_service.cms_service, "random_fun_fact", no_fun_fact)

    # Booking writes an order and cancel reads one back (API.md §21). Both go
    # through ``orders.service`` — the documented door, patched here for the
    # same reason as the two above: this suite is about what reaches Redis,
    # and it refuses to need a database to find out.
    async def started(
        session: object,
        *,
        customer_id: uuid.UUID,
        product: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[object, bool]:
        return object(), True

    async def confirmed(session: object, order: object, result: Any) -> object:
        return result

    def answered(order: Any) -> dict[str, Any]:
        # Whatever the order module would render is its own suite's subject
        # (``tests/integration/test_orders.py``). What matters here is that the
        # provider's answer still travels whole and gains nothing of GTS's that
        # we invented.
        return {"order": {"status": "booked"}, "payment": None, "data": order.raw}

    async def any_order(
        session: object, *, customer_id: uuid.UUID, provider_order_number: str
    ) -> None:
        return None

    def cancellable(order: object) -> None:
        return None

    async def no_op(session: object, order: object, result: Any) -> None:
        return None

    monkeypatch.setattr(products_service.orders_service, "start_order", started)
    monkeypatch.setattr(products_service.orders_service, "confirm_booking", confirmed)
    monkeypatch.setattr(products_service.orders_service, "booking_answer", answered)
    monkeypatch.setattr(
        products_service.orders_service, "owned_by_provider_number", any_order
    )
    monkeypatch.setattr(
        products_service.orders_service, "ensure_cancellable", cancellable
    )
    monkeypatch.setattr(products_service.orders_service, "apply_cancel", no_op)

    await settings_cache.write({"products": [{"code": "flight", "enabled": True}]})
    return credential


@pytest.fixture
def signed_in() -> Any:
    """A customer for ``booking/`` and ``cancel/``, without a customers table.

    Overriding the dependency rather than minting a token: ``current_customer``
    loads the row to catch an account blocked since the token was issued, and
    that row is the one thing this suite refuses to need.
    """

    def a_customer() -> Customer:
        return Customer(id=uuid.uuid4())

    app.dependency_overrides[current_customer] = a_customer
    yield
    app.dependency_overrides.pop(current_customer, None)


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


def _mock_gts() -> respx.Route:
    respx.post(f"{GTS}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                {
                    "session_key": "session-abc",
                    "token": "tok-abc",
                    "timeout_minutes": 360.0,
                }
            ),
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
                    "offers": [{"offer_id": OFFER_ID, "upsell": True}],
                }
            ),
        )
    )
    respx.post(f"{GTS}/v1/content/upsell/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                {
                    "request_id": REQUEST_ID,
                    "status": "success",
                    "code": "100",
                    "offers": [{"offer_id": "u-1"}],
                }
            ),
        )
    )
    respx.post(f"{GTS}/v1/content/verify/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                {
                    "status": "success",
                    "request_id": REQUEST_ID,
                    "offer_id": OFFER_ID,
                    "code": "100",
                    "verified": True,
                }
            ),
        )
    )
    respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(GTS_BOOKING),
        )
    )
    respx.post(f"{GTS}/v1/content/cancel/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope({"data": {"order_number": ORDER_NUMBER, "status": "CB"}}),
        )
    )
    return search_route


@respx.mock
async def test_the_request_id_passes_through_and_is_stored_nowhere(
    client: AsyncClient,
    fake_redis: fakeredis.aioredis.FakeRedis,
    gts_installation: ActiveGtsCredential,
    signed_in: None,
) -> None:
    _mock_gts()

    search = await client.post(SEARCH, json=SEARCH_BODY)
    offers = await client.post(
        OFFERS, json={"request_id": REQUEST_ID, "next_token": None, "limit": 20}
    )
    upsell = await client.post(
        UPSELL, json={"request_id": REQUEST_ID, "offer_id": OFFER_ID}
    )
    verify = await client.post(
        VERIFY, json={"request_id": REQUEST_ID, "offer_id": OFFER_ID}
    )
    booking = await client.post(
        BOOKING,
        json={"request_id": REQUEST_ID, "offer_id": OFFER_ID, "passengers": []},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    cancel = await client.post(CANCEL, json={"order_number": ORDER_NUMBER})

    # Byte-for-byte passthrough on the way out — plus our one addition,
    # ``fun_fact`` (API.md §20), null here because nothing is published.
    assert search.status_code == 200
    assert search.json()["data"] == {"request_id": REQUEST_ID, "fun_fact": None}
    assert offers.status_code == 200
    assert offers.json()["data"]["offers"] == [{"offer_id": OFFER_ID, "upsell": True}]
    assert upsell.status_code == 200
    assert upsell.json()["data"]["offers"] == [{"offer_id": "u-1"}]
    assert verify.status_code == 200
    assert verify.json()["data"]["verified"] is True
    # Booking now answers with our order beside GTS's answer — but the answer
    # itself is still relayed whole and gains no field we invented, which is the
    # half of the old claim that survived the money path landing on this step
    # (order-system/03-design.md §3.6).
    assert booking.status_code == 200
    assert booking.json()["data"]["data"] == GTS_BOOKING
    assert cancel.status_code == 200
    assert cancel.json()["data"] == {
        "data": {"order_number": ORDER_NUMBER, "status": "CB"}
    }

    # ...and no trace on the way down: only the platform's own keys exist, and
    # neither GTS identifier is in any of them. Values are held to the same rule
    # apart from the idempotency record, which by definition holds a copy of the
    # answer we just sent — including the booking's own ``request_id``. A cache
    # of *offers* would still be caught: it would need a key of its own.
    keys = await fake_redis.keys("*")
    for key in keys:
        assert ALLOWED_KEY.match(key), f"unexpected Redis key after a search: {key}"
        assert REQUEST_ID not in key
        assert OFFER_ID not in key
        value = await fake_redis.get(key)
        if value is not None and not REPLAY_KEY.match(key):
            assert REQUEST_ID not in value
            assert OFFER_ID not in value


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
