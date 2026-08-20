"""``GET /public/orders/`` and ``/{id}/`` — a customer sees only their own."""

import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderStatus
from tests.conftest import make_customer

ORDERS_URL = "/api/v1/public/orders/"


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
        },
    }
    fields.update(overrides)
    order = Order(**fields)
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)
    return order


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
