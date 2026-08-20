"""``GET /public/orders/`` and ``/{id}/`` — a customer sees only their own."""

import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderStatus
from tests.conftest import make_customer

ORDERS_URL = "/api/v1/public/orders/"


#: One GTS route, as the card needs it: the airline and the flight number sit
#: on the segment, the direction and the duration on the route.
ROUTES: list[dict[str, Any]] = [
    {
        "route_index": 1,
        "direction": "TAS-VKO",
        "trip_time_minutes": 255,
        "segments": [
            {
                "leg": "TAS-VKO",
                "flight_number": "HY-301",
                "airline": "Uzbekistan Airways",
                "departure_time": "08:30",
                "arrival_time": "14:45",
                # Agent economics turn up inside routes too, not only beside
                # the order — the card must not carry them out.
                "commission_amount": 12,
            }
        ],
    }
]

#: A GTS passenger as the live installation spells one — names without the
#: underscore, and every private field this list must refuse to repeat.
PASSENGER: dict[str, Any] = {
    "firstname": "Jasur",
    "lastname": "Aliyev",
    "middlename": None,
    "document_number": "AA1234567",
    "birth_date": "1990-04-12",
    "citizenship": {"code": "UZ"},
    "gender": "Male",
    "email": "jasur@example.com",
    "phone": "+998901234567",
}

#: Never on a list response, whatever GTS sends (PROJECT.md §13).
PRIVATE_KEYS = (
    "document_number",
    "document_type",
    "birth_date",
    "citizenship",
    "gender",
    "email",
    "phone",
)


async def _make_order(
    db_session: AsyncSession, customer: Customer, **overrides: Any
) -> Order:
    fields: dict[str, Any] = {
        "customer_id": customer.id,
        "product": "flight",
        "status": OrderStatus.BOOKED,
        "request_id": "req-abc",
        "offer_id": "offer-abc",
        "gts_order_number": 61453,
        "gts_status": "BO",
        "pnr": "UBPLKW",
        "route_summary": "TAS-VKO",
        "gts_response": {
            "order_number": 61453,
            "price_info": {"price": 20, "commission_amount": 0},
            "routes": ROUTES,
            "passengers": [PASSENGER],
        },
    }
    fields.update(overrides)
    order = Order(**fields)
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)
    return order


def _keys(value: Any) -> set[str]:
    """Every key anywhere in a payload, however deeply nested."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            found |= _keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _keys(item)
    return found


async def test_list_shows_only_own_orders(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mine = await _make_order(db_session, customer)
    stranger = await make_customer(db_session, "stranger@example.com")
    await _make_order(db_session, stranger, gts_order_number=99999, pnr="ZZZZZZ")

    response = await client.get(ORDERS_URL, headers=customer_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 1
    (item,) = body["data"]
    assert item["id"] == str(mine.id)
    assert item["status"] == "booked"
    assert item["pnr"] == "UBPLKW"
    assert item["routes"][0]["direction"] == "TAS-VKO"
    assert item["passengers"] == [
        {"first_name": "Jasur", "last_name": "Aliyev", "middle_name": None}
    ]


async def test_list_carries_the_gts_route(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The card is drawn from the segments, so the segments must arrive."""
    await _make_order(db_session, customer)

    response = await client.get(ORDERS_URL, headers=customer_headers)

    assert response.status_code == 200
    (item,) = response.json()["data"]
    (route,) = item["routes"]
    assert route["trip_time_minutes"] == 255
    (segment,) = route["segments"]
    assert segment["flight_number"] == "HY-301"
    assert segment["airline"] == "Uzbekistan Airways"
    assert segment["departure_time"] == "08:30"
    # Stripped on the way out, kept in the stored copy — as on detail.
    assert "commission_amount" not in segment


async def test_list_reads_routes_nested_under_offer(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Live GTS puts the journey under ``offer``, not beside the order.

    Reading only the top level is what left ``route_summary`` empty on real
    bookings; the card must not repeat the mistake.
    """
    await _make_order(
        db_session,
        customer,
        gts_response={"order_number": 61453, "offer": {"routes": ROUTES}},
    )

    response = await client.get(ORDERS_URL, headers=customer_headers)

    assert response.status_code == 200
    (item,) = response.json()["data"]
    assert item["routes"][0]["direction"] == "TAS-VKO"


async def test_list_hides_passenger_documents(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A passport number must not fly on a request that returns twenty rows."""
    await _make_order(db_session, customer)

    response = await client.get(ORDERS_URL, headers=customer_headers)

    assert response.status_code == 200
    keys = _keys(response.json())
    assert keys.isdisjoint(PRIVATE_KEYS)
    assert "Jasur" in response.text  # the name itself still gets through


async def test_list_without_a_readable_answer_is_empty_lists(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A booking recorded when GTS refused to describe it still lists."""
    await _make_order(db_session, customer, gts_response={"order_number": 61453})

    response = await client.get(ORDERS_URL, headers=customer_headers)

    assert response.status_code == 200
    (item,) = response.json()["data"]
    assert item["routes"] == []
    assert item["passengers"] == []


async def test_detail_returns_stripped_order_data(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, customer)

    response = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["order"]["id"] == str(order.id)
    assert data["payment"]["status"] == "pending"
    assert data["order_data"]["order_number"] == 61453
    assert "commission_amount" not in data["order_data"]["price_info"]


async def test_someone_elses_order_is_404(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    stranger = await make_customer(db_session, "stranger@example.com")
    order = await _make_order(db_session, stranger)

    response = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)
    assert response.status_code == 404


async def test_missing_order_is_404(
    client: httpx.AsyncClient, customer_headers: dict[str, str]
) -> None:
    response = await client.get(
        f"{ORDERS_URL}{uuid.uuid4()}/", headers=customer_headers
    )
    assert response.status_code == 404


async def test_list_requires_a_token(client: httpx.AsyncClient) -> None:
    response = await client.get(ORDERS_URL)
    assert response.status_code == 401
