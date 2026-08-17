"""``/public/orders/`` and the order row behind it — API.md §20, §21.

Two claims, and they are the whole module:

* **A booking leaves a record**, filed under the customer who made it, holding
  GTS's answer verbatim — and holding *no* passenger from the request, which
  is a promise about passport numbers (PROJECT.md §13), so it is checked
  against the raw row rather than the API's view of it.
* **The record is what ownership means.** Someone else's order is a 404 on
  every path, and ``cancel/`` refuses one without ever calling GTS.

``tests/contract/test_search_passthrough.py`` is the other half of this: it
holds the same two steps to writing nothing *else* — no offer, no search state
(D2). Here the database is real and the question is what the row says.
"""

import json as jsonlib
import uuid
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.modules.customers.models import Customer
from app.modules.integrations.models import GtsCredential
from app.modules.orders.models import Order
from app.modules.settings import cache as settings_cache
from tests.integration.conftest import customer_headers_for, make_customer

ORDERS = "/api/v1/public/orders/"
BOOKING = "/api/v1/public/flight/booking/"
CANCEL = "/api/v1/public/flight/cancel/"
GTS = "https://gts.test"

BOOKING_BODY: dict[str, Any] = {
    "request_id": "r-1",
    "offer_id": "o-1",
    "passengers": [
        {
            "type": "ADT",
            "gender": "M",
            "first_name": "Azimjon",
            "last_name": "Yusufov",
            "birth_date": "2002-12-20",
            "citizenship": "UZ",
            "document": {
                "type": "PSP",
                "number": "FA2145157",
                "issue_date": "2019-05-30",
                "expire_date": "2029-05-29",
            },
            "email": "yusufovazimjon@gmail.com",
            "phone": {"phone_code": "998", "phone_number": "998328192"},
        }
    ],
    "save_passenger": True,
}

#: GTS's booking answer, in the shape the EASY_GATEWAY collection recorded:
#: our client strips GTS's envelope, so what lands here is the wrapper, and the
#: order's own fields sit one level down under a second ``data``.
GTS_BOOKING: dict[str, Any] = {
    "message": "booked",
    "request_id": "r-1",
    "data": {
        "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "order_number": 61453,
        "status": "BO",
        "gds_pnr": "UBPLKW",
        "supplier_pnr": ["UBPLKW"],
        "trip_type": "OW",
        "price_info": {"price": 46.89, "currency": "EUR"},
    },
}


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


async def _installation(session: AsyncSession) -> None:
    """An installation that sells flights and can reach GTS."""
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
    await settings_cache.write({"products": [{"code": "flight", "enabled": True}]})


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


def _mock_booking(answer: dict[str, Any] = GTS_BOOKING) -> respx.Route:
    return respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(200, json=_envelope(answer))
    )


def _mock_cancel(answer: dict[str, Any] | None = None) -> respx.Route:
    return respx.post(f"{GTS}/v1/content/cancel/").mock(
        return_value=httpx.Response(
            200, json=_envelope(answer or {"data": {"status": "CB"}})
        )
    )


async def _order(session: AsyncSession, customer: Customer, **overrides: Any) -> Order:
    """A recorded booking, without going through GTS to get one."""
    fields: dict[str, Any] = {
        "customer_id": customer.id,
        "product": "flight",
        "gts_order_number": "61453",
        "gts_order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "status": "BO",
        "request_id": "r-1",
        "offer_id": "o-1",
        "gts_response": GTS_BOOKING,
        **overrides,
    }
    order = Order(**fields)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


@pytest.fixture
def headers(customer: Customer) -> dict[str, str]:
    return customer_headers_for(customer)


# --- what booking writes -------------------------------------------------------------


@respx.mock
async def test_a_booking_is_filed_under_the_customer_who_made_it(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    await _installation(session)
    _mock_signin()
    _mock_booking()

    response = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)

    assert response.status_code == 200
    # The response is still GTS's, byte for byte — the row is a side record,
    # not an addition to the passthrough (API.md §20).
    assert response.json()["data"] == GTS_BOOKING

    listed = await api.get(ORDERS, headers=headers)
    assert listed.status_code == 200
    (order,) = listed.json()["data"]
    assert order["product"] == "flight"
    # Read out of the *inner* data, not the wrapper — the bug this shape
    # caught: reading the top level leaves every identifier NULL.
    assert order["gts_order_number"] == "61453"
    assert order["gts_order_uid"] == "cd3f1e7bfde940f8bea03cde13f07dfd"
    assert order["status"] == "BO"
    assert order["cancelled_at"] is None
    # The search this came from, carried on the row.
    assert order["request_id"] == "r-1"
    assert order["offer_id"] == "o-1"
    # GTS's answer, verbatim and whole — including the fields we never read.
    assert order["data"] == GTS_BOOKING
    # The list is the whole row, not a summary: it and the detail endpoint
    # answer with the same keys, so a client never has to fetch {id}/ just to
    # render a list (API.md §21).
    detail = await api.get(f"{ORDERS}{order['id']}/", headers=headers)
    assert detail.json()["data"] == order
    assert set(order) == {
        "id",
        "product",
        "gts_order_number",
        "gts_order_uid",
        "status",
        "request_id",
        "offer_id",
        "created_at",
        "updated_at",
        "cancelled_at",
        "data",
    }
    # ``id`` is ours and ``gts_order_number`` is theirs — never merged
    # (API.md §21), so a client that confuses them fails loudly here.
    assert uuid.UUID(order["id"]) is not None
    assert order["id"] != order["gts_order_number"]


@respx.mock
async def test_the_passengers_of_the_request_are_not_stored(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """PROJECT.md §13: an order must be anonymisable, so the passport numbers
    of the booking request never enter the row. Checked against the raw row —
    a schema that happens not to expose them would prove nothing."""
    await _installation(session)
    _mock_signin()
    _mock_booking()

    await api.post(BOOKING, json=BOOKING_BODY, headers=headers)

    rows = (await session.execute(text("SELECT * FROM orders"))).mappings().all()
    assert len(rows) == 1
    stored = jsonlib.dumps(dict(rows[0]), default=str)
    assert "AA1234567" not in stored
    assert "VALIYEV" not in stored
    assert "1990-04-02" not in stored


@respx.mock
async def test_an_answer_without_an_order_number_is_still_recorded(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The field names are not yet confirmed against live GTS (STATUS.md §8).
    A booking that really happened must leave a trace even when we cannot read
    the shape of it — losing it to a rename would be worse."""
    await _installation(session)
    _mock_signin()
    _mock_booking({"data": {"reference": "1250", "state": "held"}})

    response = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)

    assert response.status_code == 200
    (order,) = (await api.get(ORDERS, headers=headers)).json()["data"]
    assert order["gts_order_number"] is None
    assert order["status"] is None
    assert order["data"] == {"data": {"reference": "1250", "state": "held"}}


@respx.mock
async def test_a_failed_write_does_not_fail_the_booking(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GTS is already holding the seat. A 500 would send the client into a
    retry and open a second booking — a real seat (API.md §20)."""
    from app.modules.products import service as products_service

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(products_service.orders_service, "record_booking", boom)
    await _installation(session)
    _mock_signin()
    _mock_booking()

    response = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)

    assert response.status_code == 200
    assert response.json()["data"] == GTS_BOOKING


# --- what the customer reads ---------------------------------------------------------


async def test_the_list_shows_only_my_orders(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    stranger = await make_customer(session, email="someone.else@example.uz")
    mine = await _order(session, customer)
    await _order(session, stranger, gts_order_number="9999")

    response = await api.get(ORDERS, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["data"]] == [str(mine.id)]
    assert body["meta"]["total"] == 1


async def test_the_list_filters_and_pages(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    await _order(session, customer, gts_order_number="1", status="BO")
    await _order(session, customer, gts_order_number="2", status="TI")
    await _order(session, customer, gts_order_number="3", status="TI")

    booked = await api.get(ORDERS, params={"status": "BO"}, headers=headers)
    ticketed = await api.get(ORDERS, params={"status": "TI"}, headers=headers)
    railway = await api.get(ORDERS, params={"product": "railway"}, headers=headers)
    paged = await api.get(ORDERS, params={"page_size": 2}, headers=headers)

    assert booked.json()["meta"]["total"] == 1
    assert ticketed.json()["meta"]["total"] == 2
    assert railway.json()["meta"]["total"] == 0
    assert len(paged.json()["data"]) == 2
    assert paged.json()["meta"]["total_pages"] == 2


async def test_ordering_is_a_whitelist(
    api: AsyncClient, customer: Customer, headers: dict[str, str]
) -> None:
    """``status`` is GTS's vocabulary; ordering by it would publish it as ours
    (``api/listing.py``)."""
    response = await api.get(ORDERS, params={"ordering": "status"}, headers=headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "ordering"


async def test_one_order_is_readable_and_someone_elses_is_not(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    stranger = await make_customer(session, email="someone.else@example.uz")
    mine = await _order(session, customer)
    theirs = await _order(session, stranger, gts_order_number="9999")

    ok = await api.get(f"{ORDERS}{mine.id}/", headers=headers)
    forbidden = await api.get(f"{ORDERS}{theirs.id}/", headers=headers)
    missing = await api.get(f"{ORDERS}{uuid.uuid4()}/", headers=headers)

    assert ok.status_code == 200
    assert ok.json()["data"]["data"] == GTS_BOOKING
    # "Not yours" and "no such thing" are the same answer (API.md §18).
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json()["errors"] == missing.json()["errors"]


async def test_the_history_needs_a_customer_token(api: AsyncClient) -> None:
    assert (await api.get(ORDERS)).status_code == 401


# --- ownership on cancel (API.md §20, STATUS.md §8.14) --------------------------------


@respx.mock
async def test_cancelling_someone_elses_booking_never_reaches_gts(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The hole this module was built to close: knowing a GTS identifier used
    to be enough to release somebody else's seat."""
    await _installation(session)
    signin = _mock_signin()
    cancel = _mock_cancel()
    stranger = await make_customer(session, email="someone.else@example.uz")
    await _order(session, stranger)

    response = await api.post(CANCEL, json={"order_number": 61453}, headers=headers)

    assert response.status_code == 404
    assert cancel.call_count == 0
    assert signin.call_count == 0


@respx.mock
async def test_cancelling_my_own_booking_updates_the_row(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    await _installation(session)
    _mock_signin()
    cancel = _mock_cancel()
    order = await _order(session, customer)

    response = await api.post(CANCEL, json={"order_number": 61453}, headers=headers)

    assert response.status_code == 200
    assert cancel.call_count == 1
    # GTS's code, copied not translated (API.md §21).
    detail = (await api.get(f"{ORDERS}{order.id}/", headers=headers)).json()["data"]
    assert detail["status"] == "CB"
    assert detail["cancelled_at"] is not None


@respx.mock
async def test_cancelling_without_an_order_number_is_a_422(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    await _installation(session)
    signin = _mock_signin()
    cancel = _mock_cancel()

    response = await api.post(CANCEL, json={"pnr": "ABCDEF"}, headers=headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "order_number"
    assert cancel.call_count == 0
    assert signin.call_count == 0
