"""``POST /public/flight/booking/`` — GTS first, then one row, or no row at all."""

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.models import Order

pytestmark = pytest.mark.usefixtures("gts_credential", "flight_enabled")

#: Must match ``conftest.GTS`` — the seeded credential's base URL.
GTS = "https://gts.test"

BOOKING_URL = "/api/v1/public/flight/booking/"

PAYLOAD: dict[str, Any] = {
    "request_id": "req-abc",
    "offer_id": "offer-abc",
    "passengers": [
        {
            "type": "ADT",
            "gender": "M",
            "first_name": "Azimjon",
            "last_name": "Yusufov",
            "middle_name": "Kamoliddin",
            "birth_date": "2002-12-20",
            "citizenship": "UZ",
            "document": {
                "type": "PSP",
                "number": "FA2145157",
                "issue_date": "2019-05-30",
                "expire_date": "2029-05-29",
            },
            "email": "traveller@example.com",
            "phone": {"phone_code": "998", "phone_number": "901234567"},
        }
    ],
}


def _gts_envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


def _mock_signin() -> None:
    respx.post(f"{GTS}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200,
            json=_gts_envelope(
                {
                    "session_key": "sess-abc",
                    "token": "tok-abc",
                    "timeout_minutes": 360.0,
                }
            ),
        )
    )


ORDER_NUMBER = 61453


def _gts_order(**overrides: Any) -> dict[str, Any]:
    """One order as ``GET /v1/orders/{n}/`` returns it — commission included."""
    order: dict[str, Any] = {
        "order_number": ORDER_NUMBER,
        "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "status": "BO",
        "gds_pnr": "UBPLKW",
        "supplier_pnr": ["UBPLKW"],
        "trip_type": "OW",
        "refundable": False,
        "ticket_time_limit": "2026-08-20T06:53:12Z",
        "price_info": {
            "price": 287500.0,
            "currency": "UZS",
            "fee_amount": 0,
            "commission_amount": 0,
            "from_commission_amount": 0,
        },
        "price_details": [
            {
                "passenger_type": "ADT",
                "total_amount": 287500.0,
                "commission_amount": 0,
                "profit_commission_amount": 0,
            }
        ],
        "routes": [
            {
                "route_index": 1,
                "direction": "TAS-VKO",
                "segments": [{"leg": "TAS-VKO", "flight_number": "12"}],
            }
        ],
        "passengers_count": 1,
        "passengers": [
            {
                "firstname": "Azimjon",
                "lastname": "Yusufov",
                "ticket_number": None,
                "ticket_info": {"fares_info": [{"fare_code": "Y", "cost_price": 100}]},
            }
        ],
    }
    order.update(overrides)
    return order


def _mock_booking(data: Any = None) -> None:
    """The booking answer. Live GTS sends the order number and nothing else."""
    answer = {"order_number": ORDER_NUMBER} if data is None else data
    respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(200, json=_gts_envelope(answer))
    )


def _mock_order(order: dict[str, Any] | None) -> None:
    """``GET /v1/orders/{n}/`` — where everything but the number is read from.

    ``None`` mocks GTS failing that read, which must still leave an order row:
    the seat is held by then.
    """
    respx.get(f"{GTS}/v1/orders/{ORDER_NUMBER}/").mock(
        return_value=httpx.Response(200, json=_gts_envelope(order))
        if order is not None
        else httpx.Response(500, json={"status": "error", "message": "boom"})
    )


async def _order_count(db_session: AsyncSession) -> int:
    return int(await db_session.scalar(select(func.count()).select_from(Order)) or 0)


def _find_forbidden_keys(value: Any) -> list[str]:
    """Every commission/cost key anywhere in the client-facing payload."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if "commission" in key or key == "cost_price":
                found.append(key)
            found.extend(_find_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_forbidden_keys(item))
    return found


@respx.mock
async def test_booking_creates_order(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    _mock_signin()
    _mock_booking()
    _mock_order(_gts_order())

    response = await client.post(BOOKING_URL, json=PAYLOAD, headers=customer_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "success"
    data = body["data"]
    assert data["product"] == "flight"

    order = data["order"]
    assert order["status"] == "booked"
    assert order["gts_status"] == "BO"
    assert order["gts_order_number"] == 61453
    assert order["pnr"] == "UBPLKW"
    assert order["trip_type"] == "OW"
    assert order["route_summary"] == "TAS-VKO"
    assert order["passenger_count"] == 1
    assert order["amount"] == {"amount": "287500.00", "currency": "UZS"}
    assert order["ticket_time_limit_at"] is not None
    assert order["request_id"] == "req-abc"
    assert order["offer_id"] == "offer-abc"

    payment = data["payment"]
    assert payment["status"] == "pending"
    assert payment["amount"] == {"amount": "287500.00", "currency": "UZS"}
    assert payment["pay_before"] == order["ticket_time_limit_at"]

    # GTS's answer rides along — minus every commission/cost field.
    order_data = data["order_data"]
    assert order_data["order_number"] == 61453
    assert order_data["routes"][0]["direction"] == "TAS-VKO"
    assert _find_forbidden_keys(order_data) == []

    row = await db_session.scalar(select(Order))
    assert row is not None
    assert row.status == "booked"
    assert row.gts_order_number == 61453
    # The stored copy stays verbatim — stripping is serialization-only.
    assert row.gts_response["price_info"]["commission_amount"] == 0


@respx.mock
async def test_unread_order_detail_still_records_the_seat(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GTS booked and then refused to describe the order — the row must exist.

    A held seat nothing in our database points at is worse than an order
    missing its price, so the booking stands with what it could name.
    """
    _mock_signin()
    _mock_booking()
    _mock_order(None)

    response = await client.post(BOOKING_URL, json=PAYLOAD, headers=customer_headers)

    assert response.status_code == 201
    order = response.json()["data"]["order"]
    assert order["gts_order_number"] == ORDER_NUMBER
    assert order["pnr"] is None
    assert order["amount"] is None

    row = await db_session.scalar(select(Order))
    assert row is not None
    assert row.gts_order_number == ORDER_NUMBER


@respx.mock
async def test_booking_answer_carrying_the_whole_order_is_read(
    client: httpx.AsyncClient, customer_headers: dict[str, str]
) -> None:
    """The other documented shape: the order nested beside ``message: booked``.

    Read from the answer itself when the read-back cannot be had, so an
    installation that already said everything is not asked twice in vain.
    """
    _mock_signin()
    _mock_booking({"message": "booked", "request_id": "req-abc", "data": _gts_order()})
    _mock_order(None)

    response = await client.post(BOOKING_URL, json=PAYLOAD, headers=customer_headers)

    assert response.status_code == 201
    order = response.json()["data"]["order"]
    assert order["gts_order_number"] == ORDER_NUMBER
    assert order["pnr"] == "UBPLKW"
    assert order["amount"] == {"amount": "287500.00", "currency": "UZS"}


@respx.mock
async def test_gts_failure_names_the_reason(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GTS's ``message`` is generic; the reason a customer can act on is in
    ``errors``, and that is what must reach them."""
    _mock_signin()
    respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "message": "Что то не так :(",
                "code": 100604,
                "data": None,
                "errors": ["Passenger already has an active booking on this flight"],
            },
        )
    )

    response = await client.post(BOOKING_URL, json=PAYLOAD, headers=customer_headers)

    assert response.status_code == 502
    body = response.json()
    reason = "Passenger already has an active booking on this flight"
    assert body["errors"][0]["message"] == reason
    assert body["meta"]["upstream"]["message"] == reason
    assert await _order_count(db_session) == 0


@respx.mock
async def test_gts_error_creates_no_order(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    _mock_signin()
    respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "message": "BOOKING: no seats left on this fare",
                "code": -100,
                "data": None,
            },
        )
    )

    response = await client.post(BOOKING_URL, json=PAYLOAD, headers=customer_headers)

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "error"
    assert body["errors"][0]["code"] == "upstream_error"
    assert body["meta"]["upstream"] is not None
    assert await _order_count(db_session) == 0


@respx.mock
async def test_gts_timeout_creates_no_order(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    _mock_signin()
    respx.post(f"{GTS}/v1/content/booking/").mock(
        side_effect=httpx.ReadTimeout("GTS did not answer")
    )

    response = await client.post(BOOKING_URL, json=PAYLOAD, headers=customer_headers)

    assert response.status_code == 504
    assert response.json()["errors"][0]["code"] == "upstream_timeout"
    assert await _order_count(db_session) == 0


async def test_booking_requires_a_token(client: httpx.AsyncClient) -> None:
    response = await client.post(BOOKING_URL, json=PAYLOAD)
    assert response.status_code == 401


async def test_missing_offer_id_is_422(
    client: httpx.AsyncClient, customer_headers: dict[str, str]
) -> None:
    payload = {key: value for key, value in PAYLOAD.items() if key != "offer_id"}
    response = await client.post(BOOKING_URL, json=payload, headers=customer_headers)
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "offer_id"


async def test_unknown_product_is_404(
    client: httpx.AsyncClient, customer_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/public/train/booking/", json=PAYLOAD, headers=customer_headers
    )
    assert response.status_code == 404
