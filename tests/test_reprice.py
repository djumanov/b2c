"""``reprice/`` and ``reprice/confirm/`` — the price steps GTS wants before a ticket.

GTS's lifecycle is ``booking → reprice_check → reprice_confirm → ticketing``
and its live server refuses to ticket an order that skipped the two price
steps. The customer's app takes them before the payment screen: ``reprice/``
is a question answered straight from GTS, ``reprice/confirm/`` is the write;
``payment/`` refuses until the price is confirmed. GTS is ``respx``; the
payment provider is the scripted ``FakeProvider``.
"""

import logging
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


async def _event_names(session: AsyncSession, order: Order) -> list[str]:
    return [event for event, _ in await _events(session, order)]


async def _attempts(session: AsyncSession, order: Order) -> list[PaymentAttempt]:
    rows = await session.scalars(
        select(PaymentAttempt).where(PaymentAttempt.order_id == order.id)
    )
    return list(rows.all())


UZS_20 = {"amount": "20.00", "currency": "UZS"}
UZS_300000 = {"amount": "300000.00", "currency": "UZS"}


# --- reprice (check): a question, answered as GTS answered it --------------------


@respx.mock
async def test_reprice_hands_gts_answer_through_and_changes_nothing(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """Whatever GTS says the order costs today, the client hears it verbatim
    (commission stripped) — and the order, its confirmation and the code
    already sent stay exactly as they were. Deciding is ``reprice/confirm/``."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    assert started.json()["data"]["payment"]["status"] == "awaiting_otp"
    check = mock_gts_reprice_check(NEW_PRICE)

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "changed": True,
        "old_price": UZS_20,
        "new_price": UZS_300000,
        "price_info": {"price": 300000.0, "currency": "UZS", "fee_amount": 0},
        "price_details": [],
    }
    assert check.call_count == 1
    assert check.calls.last.request.content == b'{"order_number":%d}' % ORDER_NUMBER
    await db_session.refresh(order)
    assert str(order.amount) == "20.00"
    assert order.price_confirmed_at is not None
    assert order.price_response == gts_price(20.0)
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "started"
    assert await _event_names(db_session, order) == ["payment.started"]

    # The order the client reads back says the same as before the question.
    detail = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)
    payment = detail.json()["data"]["payment"]
    assert payment["amount"] == UZS_20
    assert payment["status"] == "awaiting_otp"
    assert payment["price_confirmed"] is True


@respx.mock
async def test_reprice_asks_gts_even_for_an_unconfirmed_or_paid_order(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Nothing of ours gates a question; GTS answers or refuses on its own."""
    mock_gts_signin()
    check = mock_gts_reprice_check(gts_price(20.0))
    fresh = await make_order(db_session, customer, price_confirmed_at=None)
    paid = await make_order(db_session, customer, payment_status="paid")

    for order in (fresh, paid):
        response = await _reprice(client, order, customer_headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["price_info"]["price"] == 20.0
    assert check.call_count == 2
    await db_session.refresh(fresh)
    assert fresh.price_confirmed_at is None


@respx.mock
async def test_reprice_says_whether_the_price_moved(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """``old_price`` is the order's own figure, ``new_price`` GTS's today, and
    ``changed`` the comparison — amount **or** currency, or no price held."""
    mock_gts_signin()
    order = await make_order(db_session, customer)

    mock_gts_reprice_check(gts_price(20.0))
    same = (await _reprice(client, order, customer_headers)).json()["data"]
    assert (same["changed"], same["old_price"], same["new_price"]) == (
        False,
        UZS_20,
        UZS_20,
    )

    respx.reset()
    mock_gts_signin()
    mock_gts_reprice_check(gts_price(20.0, "USD"))
    other_currency = (await _reprice(client, order, customer_headers)).json()["data"]
    assert other_currency["changed"] is True
    assert other_currency["new_price"] == {"amount": "20.00", "currency": "USD"}
    assert other_currency["price_info"]["currency"] == "USD"

    respx.reset()
    mock_gts_signin()
    mock_gts_reprice_check(gts_price(20.0))
    unpriced = await make_order(db_session, customer, amount=None, currency=None)
    no_price_held = (await _reprice(client, unpriced, customer_headers)).json()["data"]
    assert no_price_held["changed"] is True
    assert no_price_held["old_price"] is None
    assert no_price_held["new_price"] == UZS_20
    # Still a question: nothing was written for any of the three.
    await db_session.refresh(order)
    assert str(order.amount) == "20.00" and order.currency == "UZS"
    await db_session.refresh(unpriced)
    assert unpriced.amount is None


@respx.mock
async def test_gts_refusal_is_502_with_its_words(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_reprice_check(error="Заказ не найден")
    order = await make_order(db_session, customer)

    response = await _reprice(client, order, customer_headers)

    assert response.status_code == 502
    assert response.json()["meta"]["upstream"]["message"] == "Заказ не найден"


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
async def test_strangers_order_is_404(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    check = mock_gts_reprice_check()
    confirm = mock_gts_reprice_confirm()
    stranger = await make_customer(db_session, "stranger@example.com")
    order = await make_order(db_session, stranger)

    assert (await _reprice(client, order, customer_headers)).status_code == 404
    assert (await _confirm_price(client, order, customer_headers)).status_code == 404
    assert check.call_count == 0 and confirm.call_count == 0


# --- reprice/confirm: the write ---------------------------------------------------


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
    assert confirm.calls.last.request.content == b'{"order_number":%d}' % ORDER_NUMBER
    assert await _events(db_session, order) == [("price.confirmed", UZS_20)]

    started = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "after-confirm"}
    )
    assert started.status_code == 200, started.text
    assert started.json()["data"]["payment"]["status"] == "awaiting_otp"
    (attempt,) = await _attempts(db_session, order)
    assert str(attempt.amount) == "20.00"


@respx.mock
async def test_confirm_stores_gts_final_price_and_shows_it_everywhere(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The confirmation's figure is what ticketing debits, so it is what is
    charged — and what the order, the payment block and ``order_data`` say."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_reprice_confirm(NEW_PRICE)
    order = await make_order(db_session, customer, price_confirmed_at=None)

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["payment"]["amount"] == UZS_300000
    assert data["order"]["amount"] == UZS_300000
    assert data["payment"]["price_confirmed"] is True
    assert data["order_data"]["price_info"]["price"] == 300000.0
    assert data["order_data"]["price_info"]["currency"] == "UZS"
    assert "commission_amount" not in data["order_data"]["price_info"]
    await db_session.refresh(order)
    assert str(order.amount) == "300000.00"
    assert order.price_confirmed_at is not None
    assert order.price_response == NEW_PRICE
    assert await _events(db_session, order) == [
        ("price.repriced", {"from": UZS_20, "to": UZS_300000}),
        ("price.confirmed", UZS_300000),
    ]


@respx.mock
async def test_confirm_at_a_new_price_abandons_the_code_already_sent(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """A code out for the old amount must never confirm a charge of the new one."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_reprice_confirm(NEW_PRICE)
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    assert started.json()["data"]["payment"]["status"] == "awaiting_otp"

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 200, response.text
    payment = response.json()["data"]["payment"]
    assert payment["status"] == "pending"
    assert payment["amount"] == UZS_300000
    assert payment["price_confirmed"] is True
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "abandoned"
    assert str(attempt.amount) == "20.00"
    assert fake_provider.count("start") == 1
    assert await _event_names(db_session, order) == [
        "payment.started",
        "price.repriced",
        "price.confirmed",
    ]


@respx.mock
async def test_confirm_while_a_charge_is_being_confirmed_is_409(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_reprice_confirm(NEW_PRICE)
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]
    fake_provider.confirm_outcomes = [UpstreamTimeout("lost")]
    await _confirm_payment(client, order, customer_headers, payment_id)

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 409
    assert "being confirmed" in response.json()["errors"][0]["message"]
    await db_session.refresh(order)
    assert str(order.amount) == "20.00"
    assert order.price_response == gts_price(20.0)


@respx.mock
async def test_a_paid_order_cannot_be_confirmed(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    confirm = mock_gts_reprice_confirm()
    order = await make_order(db_session, customer, payment_status="paid")

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 409
    assert confirm.call_count == 0


@respx.mock
async def test_confirm_reads_the_order_back_and_keeps_the_confirmed_price_over_it(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """After confirming, the order is GTS's current record — at the confirmed
    price, even where the record's own ``price_info`` still says the booking's."""
    mock_gts_signin()
    read = mock_gts_order(gts_order_body())  # status BO, pnr, deadline, price 287500
    mock_gts_reprice_confirm(gts_price(20.0))
    order = await make_order(
        db_session,
        customer,
        price_confirmed_at=None,
        gts_status="STATUS_BOOK",
        pnr=None,
        gts_response={"order_number": ORDER_NUMBER},
    )

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert read.call_count == 1
    assert data["payment"]["amount"] == UZS_20
    assert data["order"]["amount"] == UZS_20
    assert data["payment"]["price_confirmed"] is True
    assert data["payment"]["pay_before"] is not None
    # The read-back filled in the record...
    assert data["order_data"]["gds_pnr"] == "UBPLKW"
    assert data["order_data"]["routes"][0]["direction"] == "TAS-VKO"
    # ...and the confirmed price sits over its booking-time figure.
    assert data["order_data"]["price_info"]["price"] == 20.0
    await db_session.refresh(order)
    assert order.gts_status == "BO"
    assert order.pnr == "UBPLKW"
    assert order.gts_checked_at is not None
    assert order.gts_response["price_info"]["price"] == 287500.0
    assert str(order.amount) == "20.00"


@respx.mock
async def test_confirm_stands_when_the_read_back_fails(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The confirmation is the act; a read that fails is a warning, not an undo."""
    mock_gts_signin()
    mock_gts_order(None)
    mock_gts_reprice_confirm(gts_price(20.0))
    order = await make_order(db_session, customer, price_confirmed_at=None)

    with caplog.at_level(logging.WARNING):
        response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["payment"]["price_confirmed"] is True
    assert any(
        "gts_read_after_confirm_failed" in record.getMessage()
        for record in caplog.records
    )
    await db_session.refresh(order)
    assert order.price_confirmed_at is not None
    assert order.gts_checked_at is None


@respx.mock
async def test_confirm_finds_the_hold_released_and_cancels(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A released hold outranks the confirmation, which is then never recorded."""
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="CB"))
    mock_gts_reprice_confirm(NEW_PRICE)
    order = await make_order(db_session, customer, price_confirmed_at=None)

    response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "offer_expired"
    await db_session.refresh(order)
    assert order.status == "cancelled"
    assert order.cancel_reason == "expired"
    assert order.gts_status == "CB"
    assert order.price_confirmed_at is None
    assert order.price_response == gts_price(20.0)
    assert await _event_names(db_session, order) == ["order.cancelled"]


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


@respx.mock
async def test_a_confirmation_in_another_currency_is_refused_and_nothing_changes(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GTS's documentation draws the confirm in USD and the check in UZS; a
    figure in another currency is not a new price and must never be charged."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_reprice_confirm(gts_price(20.0, "USD"))
    order = await make_order(db_session, customer, price_confirmed_at=None)

    with caplog.at_level(logging.ERROR):
        response = await _confirm_price(client, order, customer_headers)

    assert response.status_code == 502, response.text
    message = response.json()["errors"][0]["message"]
    assert "answered in USD" in message and "priced in UZS" in message
    assert any(
        "gts_reprice_currency_mismatch" in record.getMessage()
        for record in caplog.records
    )
    await db_session.refresh(order)
    assert str(order.amount) == "20.00"
    assert order.currency == "UZS"
    assert order.price_confirmed_at is None
    assert order.price_response == gts_price(20.0)
    assert await _events(db_session, order) == []


# --- the confirmed price and GTS's read-backs ---------------------------------------


async def test_a_read_back_refreshes_the_price_only_until_it_is_confirmed(
    customer: Customer, db_session: AsyncSession
) -> None:
    """``reprice_confirm`` is GTS's later word than the order's own ``price_info``."""
    snapshot = OrderSnapshot(
        gts_order_number=ORDER_NUMBER,
        gts_status="BO",
        amount=Decimal("287500.00"),
        currency="UZS",
        raw={"price_info": {"price": 287500.0}},
    )
    unconfirmed = await make_order(db_session, customer, price_confirmed_at=None)
    service.apply_snapshot(unconfirmed, snapshot)
    assert str(unconfirmed.amount) == "287500.00"

    confirmed = await make_order(db_session, customer)
    service.apply_snapshot(confirmed, snapshot)
    assert str(confirmed.amount) == "20.00"
    assert confirmed.gts_status == "BO"
    assert confirmed.gts_checked_at is not None
    # The record itself is still refreshed whole; the overlay is presentational.
    assert confirmed.gts_response == {"price_info": {"price": 287500.0}}


async def test_the_columns_start_empty_for_an_order_booked_before_the_price_steps(
    customer: Customer, db_session: AsyncSession
) -> None:
    order = await make_order(
        db_session, customer, price_confirmed_at=None, price_response=None
    )
    row = await db_session.get(Order, order.id)
    assert row is not None
    assert row.price_confirmed_at is None and row.price_response is None
    assert uuid.UUID(str(row.id)) == order.id


async def test_order_data_shows_the_record_alone_until_confirmed(
    customer: Customer, db_session: AsyncSession
) -> None:
    from app.modules.orders.schemas import _order_data

    body = {
        "order_number": ORDER_NUMBER,
        "price_info": {"price": 287500.0, "currency": "UZS", "commission_amount": 0},
        "price_details": [{"passenger_type": "ADT", "total_amount": 287500.0}],
    }
    before = await make_order(
        db_session,
        customer,
        price_confirmed_at=None,
        price_response=None,
        gts_response=body,
    )
    assert _order_data(before)["price_info"] == {"price": 287500.0, "currency": "UZS"}
    assert _order_data(before)["price_details"] == body["price_details"]

    after = await make_order(
        db_session,
        customer,
        gts_response=body,
        price_response={**gts_price(20.0), "price_details": []},
    )
    shown = _order_data(after)
    assert shown["price_info"] == {"price": 20.0, "currency": "UZS", "fee_amount": 0}
    # An empty breakdown in the answer does not blank the record's.
    assert shown["price_details"] == body["price_details"]
    assert shown["order_number"] == ORDER_NUMBER
