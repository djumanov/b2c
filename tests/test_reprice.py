"""``reprice/`` and ``reprice/confirm/`` — the price steps GTS wants before a ticket.

GTS's lifecycle is ``booking → reprice_check → reprice_confirm → ticketing``
and its live server refuses to ticket an order that skipped the two price
steps. The customer's app takes them before the payment screen; ``payment/``
refuses until the price is confirmed. GTS is ``respx``; the payment provider
is the scripted ``FakeProvider``.
"""

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import UpstreamTimeout
from app.modules.customers.models import Customer
from app.modules.orders import service
from app.modules.orders.models import Order, OrderEvent, PaymentAttempt
from app.providers.products.base import OrderSnapshot
from tests.conftest import (
    ORDER_NUMBER,
    FakeProvider,
    gts_order_body,
    gts_price,
    make_customer,
    make_order,
    mock_gts_order,
    mock_gts_reprice_check,
    mock_gts_reprice_confirm,
    mock_gts_signin,
)

pytestmark = pytest.mark.usefixtures("gts_credential")

ORDERS_URL = "/api/v1/public/orders/"
RAW_CARD = {"card": {"number": "4111111111111111", "expire": "1230"}}
NEW_PRICE = gts_price(300000.0)


async def _reprice(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(f"{ORDERS_URL}{order.id}/reprice/", headers=headers)


async def _confirm_price(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        f"{ORDERS_URL}{order.id}/reprice/confirm/", headers=headers
    )


async def _start(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        f"{ORDERS_URL}{order.id}/payment/",
        json={"method": "fake", **RAW_CARD},
        headers=headers,
    )


async def _confirm_payment(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str], payment_id: str
) -> httpx.Response:
    return await client.post(
        f"{ORDERS_URL}{order.id}/payment/confirm/",
        json={"payment_id": payment_id, "otp": "000000"},
        headers=headers,
    )


async def _events(
    session: AsyncSession, order: Order
) -> list[tuple[str, dict[str, Any] | None]]:
    rows = await session.execute(
        select(OrderEvent.event, OrderEvent.data)
        .where(OrderEvent.order_id == order.id)
        .order_by(OrderEvent.created_at, OrderEvent.seq)
    )
    return [(event, data) for event, data in rows.all()]


async def _attempts(session: AsyncSession, order: Order) -> list[PaymentAttempt]:
    rows = await session.scalars(
        select(PaymentAttempt).where(PaymentAttempt.order_id == order.id)
    )
    return list(rows.all())


UZS_20 = {"amount": "20.00", "currency": "UZS"}
UZS_300000 = {"amount": "300000.00", "currency": "UZS"}


# --- reprice (check) ------------------------------------------------------------------


@respx.mock
async def test_same_price_stamps_the_check_and_keeps_the_confirmation(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    check = mock_gts_reprice_check(gts_price(20.0))
    order = await make_order(db_session, customer, repriced_at=None)

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 200, response.text
    payment = response.json()["data"]["payment"]
    assert payment["amount"] == UZS_20
    assert payment["price_confirmed"] is True
    assert check.call_count == 1
    assert check.calls.last.request.content == b'{"order_number":%d}' % ORDER_NUMBER
    await db_session.refresh(order)
    assert order.repriced_at is not None
    assert order.price_confirmed_at is not None
    assert await _events(db_session, order) == []


@respx.mock
async def test_new_price_replaces_the_amount_and_clears_the_confirmation(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_reprice_check(NEW_PRICE)
    order = await make_order(db_session, customer)

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["payment"]["amount"] == UZS_300000
    assert data["order"]["amount"] == UZS_300000
    assert data["payment"]["price_confirmed"] is False
    await db_session.refresh(order)
    assert str(order.amount) == "300000.00"
    assert order.price_confirmed_at is None
    assert await _events(db_session, order) == [
        ("price.repriced", {"from": UZS_20, "to": UZS_300000})
    ]


@respx.mock
async def test_new_price_abandons_the_code_already_sent(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """A code out for the old amount must never confirm a charge of the new one."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_reprice_check(NEW_PRICE)
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    assert started.json()["data"]["payment"]["status"] == "awaiting_otp"

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 200, response.text
    payment = response.json()["data"]["payment"]
    assert payment["status"] == "pending"
    assert payment["price_confirmed"] is False
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "abandoned"
    assert fake_provider.count("start") == 1


@respx.mock
async def test_reprice_while_a_charge_is_being_confirmed_is_409(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_reprice_check(NEW_PRICE)
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]
    fake_provider.confirm_outcomes = [UpstreamTimeout("lost")]
    await _confirm_payment(client, order, customer_headers, payment_id)

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 409
    assert "being confirmed" in response.json()["errors"][0]["message"]
    await db_session.refresh(order)
    assert str(order.amount) == "20.00"
    assert order.price_confirmed_at is not None


@respx.mock
async def test_gts_refusal_is_502_with_its_words_and_changes_nothing(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_reprice_check(error="Заказ не найден")
    order = await make_order(db_session, customer, repriced_at=None)

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 502
    assert response.json()["meta"]["upstream"]["message"] == "Заказ не найден"
    await db_session.refresh(order)
    assert order.repriced_at is None
    assert str(order.amount) == "20.00"


@respx.mock
async def test_an_answer_without_a_price_is_502(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_reprice_check({"price_info": {"currency": "UZS"}, "price_details": []})
    order = await make_order(db_session, customer)

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 502
    assert "no price" in response.json()["errors"][0]["message"]


@respx.mock
async def test_a_paid_order_cannot_be_repriced(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    check = mock_gts_reprice_check()
    order = await make_order(db_session, customer, payment_status="paid")

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 409
    assert check.call_count == 0


@respx.mock
async def test_strangers_order_is_404(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    check = mock_gts_reprice_check()
    stranger = await make_customer(db_session, "stranger@example.com")
    order = await make_order(db_session, stranger)

    assert (await _reprice(client, order, customer_headers)).status_code == 404
    assert (await _confirm_price(client, order, customer_headers)).status_code == 404
    assert check.call_count == 0


# --- reprice/confirm ------------------------------------------------------------------


@respx.mock
async def test_confirm_without_a_check_is_409_and_asks_gts_nothing(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    confirm = mock_gts_reprice_confirm()
    order = await make_order(
        db_session, customer, repriced_at=None, price_confirmed_at=None
    )

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 409
    assert "reprice/" in response.json()["errors"][0]["message"]
    assert confirm.call_count == 0


@respx.mock
async def test_confirm_accepts_the_price_and_unlocks_payment(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    confirm = mock_gts_reprice_confirm(gts_price(20.0))
    order = await make_order(db_session, customer, price_confirmed_at=None)

    refused = await _start(client, order, customer_headers)
    assert refused.status_code == 409
    assert "not been confirmed" in refused.json()["errors"][0]["message"]
    assert fake_provider.calls == []

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 200, response.text
    payment = response.json()["data"]["payment"]
    assert payment["amount"] == UZS_20
    assert payment["price_confirmed"] is True
    assert confirm.call_count == 1
    assert await _events(db_session, order) == [("price.confirmed", UZS_20)]

    started = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "after-confirm"}
    )
    assert started.status_code == 200, started.text
    assert started.json()["data"]["payment"]["status"] == "awaiting_otp"
    (attempt,) = await _attempts(db_session, order)
    assert str(attempt.amount) == "20.00"


@respx.mock
async def test_confirm_stores_gts_final_price_when_it_differs_from_the_check(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The confirmation's figure is what ticketing debits, so it is what is charged."""
    mock_gts_signin()
    mock_gts_reprice_confirm(NEW_PRICE)
    order = await make_order(db_session, customer, price_confirmed_at=None)

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 200, response.text
    payment = response.json()["data"]["payment"]
    assert payment["amount"] == UZS_300000
    assert payment["price_confirmed"] is True
    await db_session.refresh(order)
    assert str(order.amount) == "300000.00"
    assert order.price_confirmed_at is not None
    assert await _events(db_session, order) == [
        ("price.repriced", {"from": UZS_20, "to": UZS_300000}),
        ("price.confirmed", UZS_300000),
    ]


@respx.mock
async def test_confirm_refused_by_gts_leaves_the_price_unconfirmed(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_reprice_confirm(error="Цена изменилась")
    order = await make_order(db_session, customer, price_confirmed_at=None)

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 502
    assert response.json()["meta"]["upstream"]["message"] == "Цена изменилась"
    await db_session.refresh(order)
    assert order.price_confirmed_at is None


# --- the confirmed price and GTS's read-backs -----------------------------------------


async def test_a_read_back_refreshes_an_unconfirmed_price_and_keeps_a_confirmed_one(
    customer: Customer, db_session: AsyncSession
) -> None:
    """``reprice_confirm`` is GTS's later word than the order's own ``price_info``."""
    snapshot = OrderSnapshot(
        gts_order_number=ORDER_NUMBER,
        gts_status="BO",
        amount=Decimal("287500.00"),
        currency="UZS",
        raw={},
    )
    unconfirmed = await make_order(db_session, customer, price_confirmed_at=None)
    service.apply_snapshot(unconfirmed, snapshot)
    assert str(unconfirmed.amount) == "287500.00"

    confirmed = await make_order(db_session, customer)
    service.apply_snapshot(confirmed, snapshot)
    assert str(confirmed.amount) == "20.00"
    assert confirmed.gts_status == "BO"
    assert confirmed.gts_checked_at is not None


async def test_the_columns_start_empty_for_an_order_booked_before_the_price_steps(
    customer: Customer, db_session: AsyncSession
) -> None:
    order = await make_order(
        db_session, customer, repriced_at=None, price_confirmed_at=None
    )
    row = await db_session.get(Order, order.id)
    assert row is not None
    assert row.repriced_at is None and row.price_confirmed_at is None
    assert uuid.UUID(str(row.id)) == order.id
