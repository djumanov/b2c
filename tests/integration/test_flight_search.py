"""``POST /public/flight/search|offers/`` — the passthrough, end to end.

The full chain under test: gate (cached ``product_settings``) → active
credential decrypted from Postgres → GTS session signed in and cached in
Redis → the body forwarded verbatim → GTS's ``data`` returned inside our
envelope. And the promise underneath it all, D2: **a search writes nothing**
— the row-count check at the bottom holds the module to it.

``respx`` intercepts the outbound ``httpx`` transport; the ``api`` fixture
speaks ASGI, which respx leaves alone.
"""

import json as jsonlib
import uuid
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.db.base import Base
from app.modules.cms.models import ContentStatus, FunFact
from app.modules.customers.models import Customer
from app.modules.integrations.models import GtsCredential
from app.modules.orders.models import Order
from app.modules.settings import cache as settings_cache
from tests.integration.conftest import customer_headers_for

SEARCH = "/api/v1/public/flight/search/"
OFFERS = "/api/v1/public/flight/offers/"
UPSELL = "/api/v1/public/flight/upsell/"
VERIFY = "/api/v1/public/flight/verify/"
BOOKING = "/api/v1/public/flight/booking/"
CANCEL = "/api/v1/public/flight/cancel/"
GTS = "https://gts.test"

BOOKING_BODY: dict[str, Any] = {
    "request_id": "r-1",
    "offer_id": "o-1",
    "passengers": [
        {
            "first_name": "ALI",
            "last_name": "VALIYEV",
            "birth_date": "1990-04-02",
            "document_number": "AA1234567",
        }
    ],
    "save_passenger": True,
}

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


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


def _mock_signin() -> respx.Route:
    return respx.post(f"{GTS}/v1/auth/signin/").mock(
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


def _mock_search(
    request_id: str = "6c62dcec-9334-11ee-8688-5169d0acfb81",
) -> respx.Route:
    return respx.post(f"{GTS}/v1/content/search/").mock(
        return_value=httpx.Response(200, json=_envelope({"request_id": request_id}))
    )


async def _activate_credential(session: AsyncSession) -> None:
    ciphertext, key_version = encrypt("gts-secret-1a2b")
    session.add(
        GtsCredential(
            label="Prod agent",
            base_url=GTS,
            email="agent@brand.uz",
            password=ciphertext,
            key_version=key_version,
            is_active=True,
        )
    )
    await session.commit()


async def _enable_flight() -> None:
    """Seed the cached document the gate reads, deterministically."""
    await settings_cache.write({"products": [{"code": "flight", "enabled": True}]})


# --- the happy path ------------------------------------------------------------------


@respx.mock
async def test_a_search_passes_through_and_returns_gts_request_id(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    signin = _mock_signin()
    search = _mock_search()

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "data", "errors", "meta"}
    # ``fun_fact`` is our one addition to the passthrough (API.md §20) —
    # null here because nothing is published.
    assert body["data"] == {
        "request_id": "6c62dcec-9334-11ee-8688-5169d0acfb81",
        "fun_fact": None,
    }
    assert signin.call_count == 1
    # The body reached GTS verbatim.
    assert jsonlib.loads(search.calls.last.request.content) == SEARCH_BODY
    cookie = search.calls.last.request.headers["cookie"]
    assert cookie == "esession=session-abc; token=tok-abc"


@respx.mock
async def test_a_second_search_reuses_the_session(
    api: AsyncClient, session: AsyncSession
) -> None:
    """One machine-account login serves many searches — the Redis cache."""
    await _activate_credential(session)
    await _enable_flight()
    signin = _mock_signin()
    search = _mock_search()

    await api.post(SEARCH, json=SEARCH_BODY)
    await api.post(SEARCH, json=SEARCH_BODY)

    assert signin.call_count == 1
    assert search.call_count == 2


@respx.mock
async def test_a_session_that_died_early_is_renewed_mid_request(
    api: AsyncClient, session: AsyncSession
) -> None:
    """Somebody signed into the GTS panel with the same account (``is_single``)."""
    await _activate_credential(session)
    await _enable_flight()
    signin = _mock_signin()
    search = respx.post(f"{GTS}/v1/content/search/").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json=_envelope({"request_id": "r-2"})),
        ]
    )

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 200
    assert response.json()["data"] == {"request_id": "r-2", "fun_fact": None}
    assert search.call_count == 2
    assert signin.call_count == 2


@respx.mock
async def test_offers_pass_through_untouched(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    gts_offers = {
        "request_id": "r-1",
        "next_token": "c27b666f",
        "count": 41,
        "trip_type": "RT",
        "offers": [{"offer_id": "o-1", "price_info": {"price": 221.86}}],
    }
    route = respx.post(f"{GTS}/v1/content/offers/").mock(
        return_value=httpx.Response(200, json=_envelope(gts_offers))
    )

    response = await api.post(
        OFFERS,
        json={
            "request_id": "r-1",
            "next_token": None,
            "sort_type": "price",
            "limit": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"] == {**gts_offers, "search_status": "success"}
    assert route.call_count == 1


@respx.mock
async def test_upsell_passes_through_untouched(
    api: AsyncClient, session: AsyncSession
) -> None:
    """Fare variants of one offer — GTS's inner ``status``/``code`` survive
    next to our ``search_status``."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    gts_upsell = {
        "request_id": "r-1",
        "status": "success",
        "code": "100",
        "trip_type": "OW",
        "currency": "USD",
        "offers": [
            {"offer_id": "u-1", "price_info": {"price": 108.67}},
            {"offer_id": "u-2", "price_info": {"price": 158.67}},
        ],
    }
    route = respx.post(f"{GTS}/v1/content/upsell/").mock(
        return_value=httpx.Response(200, json=_envelope(gts_upsell))
    )

    response = await api.post(UPSELL, json={"request_id": "r-1", "offer_id": "o-1"})

    assert response.status_code == 200
    assert response.json()["data"] == {**gts_upsell, "search_status": "success"}
    assert route.call_count == 1


@respx.mock
async def test_verify_passes_through_untouched(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    gts_verify = {
        "status": "success",
        "request_id": "r-1",
        "offer_id": "o-1",
        "code": "100",
        "verified": True,
    }
    route = respx.post(f"{GTS}/v1/content/verify/").mock(
        return_value=httpx.Response(200, json=_envelope(gts_verify))
    )

    response = await api.post(VERIFY, json={"request_id": "r-1", "offer_id": "o-1"})

    assert response.status_code == 200
    assert response.json()["data"] == {**gts_verify, "search_status": "success"}
    assert route.call_count == 1


@respx.mock
async def test_a_running_search_is_relayed_not_failed(
    api: AsyncClient, session: AsyncSession
) -> None:
    """GTS says ``In process`` with partial offers — the client sees exactly
    that, not a 502 (the bug live testing caught on 2026-08-12)."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    respx.post(f"{GTS}/v1/content/offers/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "In process",
                "message": "В процессе",
                "code": 0,
                "data": {"offers": [{"offer_id": "o-1"}], "next_token": "t-1"},
            },
        )
    )

    response = await api.post(OFFERS, json={"request_id": "r-1"})

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["search_status"] == "In process"
    assert body["offers"] == [{"offer_id": "o-1"}]


@respx.mock
async def test_anonymous_is_welcome_but_garbage_tokens_are_not(
    api: AsyncClient, session: AsyncSession
) -> None:
    """§20's ``(✓)``: optional auth forgives absence, never a bad token."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    _mock_search()

    anonymous = await api.post(SEARCH, json=SEARCH_BODY)
    garbage = await api.post(
        SEARCH, json=SEARCH_BODY, headers={"Authorization": "Bearer not-a-token"}
    )

    assert anonymous.status_code == 200
    assert garbage.status_code == 401


# --- booking and cancel (API.md §20) -------------------------------------------------


def _booking_headers(customer: Customer) -> dict[str, str]:
    """Booking is a money endpoint and refuses to run without a key (§10)."""
    return {**customer_headers_for(customer), "Idempotency-Key": str(uuid.uuid4())}


@respx.mock
async def test_booking_relays_the_answer_beside_our_own_order(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """GTS's answer is still published whole, under ``data`` — the route, the
    segments and the fare rules live only there. What is new beside it is the
    order and the payment placeholder (order-system/03-design.md §3.6)."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    gts_booking = {
        "data": {
            "order_number": 1250,
            "gds_pnr": "ABCDEF",
            "status": "BO",
            "price_info": {"price": 100, "currency": "UZS"},
        }
    }
    route = respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(200, json=_envelope(gts_booking))
    )

    response = await api.post(
        BOOKING, json=BOOKING_BODY, headers=_booking_headers(customer)
    )

    assert response.status_code == 200
    answer = response.json()["data"]
    assert answer["data"] == gts_booking
    assert answer["order"]["provider_order_number"] == "1250"
    assert answer["payment"]["status"] == "pending"
    assert route.call_count == 1
    # Passengers and all, byte for byte — the request is untouched.
    assert jsonlib.loads(route.calls.last.request.content) == BOOKING_BODY


@respx.mock
async def test_cancel_passes_through_untouched(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """The body still reaches GTS verbatim — the ownership check reads
    ``order_id``, it does not rewrite the request (API.md §20)."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    session.add(
        Order(
            order_no="B2C-2608-800001",
            customer_id=customer.id,
            product="flight",
            status="booked",
            provider_order_number="61453",
            provider_status="BO",
            amount_total="52.39",
            currency="EUR",
            provider_response={"data": {"order_number": 61453, "status": "BO"}},
        )
    )
    await session.commit()
    gts_cancel = {"data": {"order_number": 61453, "status": "CB"}}
    route = respx.post(f"{GTS}/v1/content/cancel/").mock(
        return_value=httpx.Response(200, json=_envelope(gts_cancel))
    )

    body = {"order_number": 61453, "reason": "changed my mind"}
    response = await api.post(CANCEL, json=body, headers=customer_headers_for(customer))

    assert response.status_code == 200
    assert response.json()["data"] == gts_cancel
    assert route.call_count == 1
    assert jsonlib.loads(route.calls.last.request.content) == body


@respx.mock
@pytest.mark.parametrize("path", [BOOKING, CANCEL])
async def test_the_writing_steps_refuse_the_anonymous(
    api: AsyncClient, session: AsyncSession, path: str
) -> None:
    """The exception to §20's ``(✓)``: no guest purchase (PROJECT.md D4).
    GTS is never reached — not even a session is signed in."""
    await _activate_credential(session)
    await _enable_flight()
    signin = _mock_signin()

    anonymous = await api.post(path, json=BOOKING_BODY)
    garbage = await api.post(
        path, json=BOOKING_BODY, headers={"Authorization": "Bearer not-a-token"}
    )

    assert anonymous.status_code == 401
    assert garbage.status_code == 401
    assert signin.call_count == 0


@respx.mock
async def test_a_booking_without_an_offer_id_is_422_before_a_session(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    signin = _mock_signin()

    response = await api.post(
        BOOKING,
        json={"request_id": "r-1", "passengers": []},
        headers=_booking_headers(customer),
    )

    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == "validation"
    assert error["field"] == "offer_id"
    assert signin.call_count == 0


@respx.mock
async def test_a_refused_booking_keeps_the_gts_reason(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """GTS reports failure inside HTTP 200 (GTS.md §10). No mapping to
    ``offer_expired`` — a passthrough relays, and the reason must survive."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "code": -104,
                "message": (
                    "BOOKING: save_booking 403: user don't have enough credits "
                    "on account"
                ),
                "data": None,
            },
        )
    )

    response = await api.post(
        BOOKING, json=BOOKING_BODY, headers=_booking_headers(customer)
    )

    assert response.status_code == 502
    error = response.json()["errors"][0]
    assert error["code"] == "upstream_error"
    assert "enough credits" in error["message"]
    assert response.json()["meta"]["upstream"]["code"] == -104


@respx.mock
async def test_a_booking_timeout_is_a_504(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    respx.post(f"{GTS}/v1/content/booking/").mock(
        side_effect=httpx.ReadTimeout("too slow")
    )

    response = await api.post(
        BOOKING, json=BOOKING_BODY, headers=_booking_headers(customer)
    )

    assert response.status_code == 504
    assert response.json()["errors"][0]["code"] == "upstream_timeout"


@respx.mock
async def test_a_booking_writes_one_order_row_and_touches_nothing_else(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """Booking writes exactly one order, and its history line (API.md §21).

    Not a passenger row — not even the ``save_passenger`` the body asks for,
    which lands with the module that owns passengers — and nothing that
    resembles a cache of the search (D2). Two tables move, and they are the
    order and the record of how it got where it is."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                {
                    "data": {
                        "order_number": 1250,
                        "status": "BO",
                        "price_info": {"price": 100, "currency": "UZS"},
                    }
                }
            ),
        )
    )

    async def counts() -> dict[str, int]:
        result = {}
        for table in Base.metadata.sorted_tables:
            count = await session.scalar(select(func.count()).select_from(table))
            result[table.name] = count
        return result

    before = await counts()
    response = await api.post(
        BOOKING, json=BOOKING_BODY, headers=_booking_headers(customer)
    )
    after = await counts()

    assert response.status_code == 200
    assert after == {
        **before,
        "orders": before["orders"] + 1,
        "order_events": before["order_events"] + 1,
    }


# --- the fun fact (API.md §20) --------------------------------------------------------

FACT_TEXT = {
    "uz": "Boeing 747 qanotida 6 million detal bor.",
    "ru": "В крыле Boeing 747 шесть миллионов деталей.",
}


async def _seed_fact(
    session: AsyncSession,
    text: dict[str, str] = FACT_TEXT,
    *,
    status: str = ContentStatus.PUBLISHED,
    deleted: bool = False,
) -> FunFact:
    fact = FunFact(text=text, status=status)
    if deleted:
        fact.soft_delete()
    session.add(fact)
    await session.commit()
    return fact


@respx.mock
async def test_the_one_published_fact_rides_the_search_response(
    api: AsyncClient, session: AsyncSession
) -> None:
    """Drafts and soft-deleted rows never surface — with exactly one live
    published fact, the random draw is deterministic."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    _mock_search()
    await _seed_fact(session)
    await _seed_fact(session, {"uz": "Qoralama fakt."}, status=ContentStatus.DRAFT)
    await _seed_fact(session, {"uz": "O'chirilgan fakt."}, deleted=True)

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 200
    assert response.json()["data"]["fun_fact"] == FACT_TEXT["uz"]


@respx.mock
async def test_the_fact_speaks_the_requested_language_and_falls_back(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    _mock_search()
    await _seed_fact(session)  # uz + ru, no en

    russian = await api.post(f"{SEARCH}?lang=ru", json=SEARCH_BODY)
    assert russian.json()["data"]["fun_fact"] == FACT_TEXT["ru"]

    # No English translation: the chain falls back to the site default.
    english = await api.post(f"{SEARCH}?lang=en", json=SEARCH_BODY)
    assert english.json()["data"]["fun_fact"] == FACT_TEXT["uz"]


@respx.mock
async def test_a_random_fact_is_always_one_of_the_published(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    _mock_search()
    texts = {f"Fakt {n}." for n in range(3)}
    for text in texts:
        await _seed_fact(session, {"uz": text})

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.json()["data"]["fun_fact"] in texts


@respx.mock
async def test_no_published_fact_means_null_not_an_error(
    api: AsyncClient, session: AsyncSession
) -> None:
    """The off-switch: an installation that publishes nothing shows nothing —
    there is deliberately no feature flag (API.md §30)."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    _mock_search()
    await _seed_fact(session, status=ContentStatus.DRAFT)

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 200
    assert response.json()["data"]["fun_fact"] is None


# --- the gate and the missing credential ---------------------------------------------


@respx.mock
async def test_a_disabled_vertical_is_404_before_gts_is_touched(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await settings_cache.write({"products": [{"code": "flight", "enabled": False}]})
    signin = _mock_signin()

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "not_found"
    assert signin.call_count == 0


@respx.mock
async def test_no_active_credential_is_a_502_naming_configuration(
    api: AsyncClient,
) -> None:
    await _enable_flight()

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 502
    error = response.json()["errors"][0]
    assert error["code"] == "upstream_error"
    assert "not configured" in error["message"]


# --- upstream failures ---------------------------------------------------------------


@respx.mock
async def test_a_gts_error_wearing_http_200_is_a_502_with_its_code_kept(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    respx.post(f"{GTS}/v1/content/search/").mock(
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

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 502
    body = response.json()
    assert body["errors"][0]["message"] == "no providers"
    assert body["meta"]["upstream"] == {"code": -21, "message": "no providers"}


@respx.mock
async def test_a_search_timeout_is_a_504(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    respx.post(f"{GTS}/v1/content/search/").mock(side_effect=httpx.ReadTimeout)

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 504
    assert response.json()["errors"][0]["code"] == "upstream_timeout"


@respx.mock
async def test_a_signin_timeout_is_a_504_too(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    respx.post(f"{GTS}/v1/auth/signin/").mock(side_effect=httpx.ConnectTimeout)

    response = await api.post(SEARCH, json=SEARCH_BODY)

    assert response.status_code == 504


# --- our own refusals ----------------------------------------------------------------


@respx.mock
async def test_junk_is_a_422_before_a_session_is_spent(
    api: AsyncClient, session: AsyncSession
) -> None:
    await _activate_credential(session)
    await _enable_flight()
    signin = _mock_signin()

    response = await api.post(SEARCH, json={**SEARCH_BODY, "adt": 0})

    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == "validation"
    assert error["field"] == "adt"
    assert signin.call_count == 0


@respx.mock
async def test_the_thirty_first_search_in_a_minute_is_rate_limited(
    api: AsyncClient, session: AsyncSession
) -> None:
    """API.md §14 — the tighter ``search`` bucket, stacked on ``public``."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    _mock_search()

    last = None
    for _ in range(31):
        last = await api.post(SEARCH, json=SEARCH_BODY)

    assert last is not None
    assert last.status_code == 429
    assert last.json()["errors"][0]["code"] == "rate_limited"
    assert "Retry-After" in last.headers


@pytest.mark.parametrize("path", [SEARCH, OFFERS, UPSELL, VERIFY, BOOKING, CANCEL])
async def test_the_path_without_its_slash_is_404(api: AsyncClient, path: str) -> None:
    response = await api.post(path.rstrip("/"), json={})

    assert response.status_code == 404


# --- D2: a search writes nothing -----------------------------------------------------


@respx.mock
async def test_a_search_leaves_every_table_exactly_as_it_found_it(
    api: AsyncClient, session: AsyncSession
) -> None:
    """The regression guard PHASES.md §4 asks for, on the database side."""
    await _activate_credential(session)
    await _enable_flight()
    _mock_signin()
    _mock_search()

    async def counts() -> dict[str, int]:
        result = {}
        for table in Base.metadata.sorted_tables:
            count = await session.scalar(select(func.count()).select_from(table))
            result[table.name] = count
        return result

    before = await counts()
    response = await api.post(SEARCH, json=SEARCH_BODY)
    after = await counts()

    assert response.status_code == 200
    assert after == before
