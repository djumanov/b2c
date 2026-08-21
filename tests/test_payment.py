"""``POST /public/orders/{id}/payment/`` and ``payment/confirm/`` — charge once.

GTS is ``respx``; the provider is the scripted ``FakeProvider`` from
``conftest``, so every branch of the provider contract (paid, declined,
lost answer, still pending) is a line in the test rather than a network.
"""

import asyncio
import json
import logging
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import UpstreamError, UpstreamTimeout
from app.db.mixins import utcnow
from app.modules.customers.models import Customer
from app.modules.orders import service
from app.modules.orders.models import Order, OrderEvent, PaymentAttempt
from app.modules.payments import service as payments_service
from app.modules.payments.models import CustomerCard
from app.modules.payments.schemas import CardCreateIn
from app.providers.payments.base import PaymentOutcome
from tests.conftest import (
    PAYME_CREDENTIALS,
    PAYME_RECEIPT,
    PAYME_TOKEN,
    FakeProvider,
    RpcError,
    bearer,
    gts_order_body,
    make_customer,
    make_order,
    mock_gts_order,
    mock_gts_signin,
    mock_gts_ticketing,
    mock_payme,
)

pytestmark = pytest.mark.usefixtures("gts_credential")

ORDERS_URL = "/api/v1/public/orders/"
#: A Luhn-valid test number nobody is billed on.
PAN = "4111111111111111"
RAW_CARD = {"card": {"number": PAN, "expire": "1230"}}


def _payment_url(order: Order) -> str:
    return f"{ORDERS_URL}{order.id}/payment/"


def _confirm_url(order: Order) -> str:
    return f"{ORDERS_URL}{order.id}/payment/confirm/"


async def _save_card(session: AsyncSession, customer: Customer) -> uuid.UUID:
    card = await payments_service.add_card(
        session,
        customer.id,
        CardCreateIn(number=SecretStr(PAN), expire=SecretStr("1230")),
    )
    return card.id


async def _attempts(session: AsyncSession, order: Order) -> list[PaymentAttempt]:
    rows = await session.scalars(
        select(PaymentAttempt)
        .where(PaymentAttempt.order_id == order.id)
        .order_by(PaymentAttempt.created_at)
    )
    return list(rows.all())


async def _events(session: AsyncSession, order: Order) -> list[str]:
    rows = await session.scalars(
        select(OrderEvent.event)
        .where(OrderEvent.order_id == order.id)
        .order_by(OrderEvent.created_at)
    )
    return list(rows.all())


async def _start(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str], body: Any = None
) -> httpx.Response:
    return await client.post(
        _payment_url(order), json=RAW_CARD if body is None else body, headers=headers
    )


async def _confirm(
    client: httpx.AsyncClient,
    order: Order,
    headers: dict[str, str],
    payment_id: str,
    otp: str = "000000",
) -> httpx.Response:
    return await client.post(
        _confirm_url(order),
        json={"payment_id": payment_id, "otp": otp},
        headers=headers,
    )


# --- start ----------------------------------------------------------------------


@respx.mock
async def test_start_with_saved_card_creates_started_attempt_and_phone_hint(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    card_id = await _save_card(db_session, customer)

    response = await _start(client, order, customer_headers, {"card_id": str(card_id)})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["payment"]["status"] == "awaiting_otp"
    assert data["payment"]["phone_hint"] == "+99890***1234"
    assert data["payment"]["card_last4"] == "1111"
    assert data["payment"]["provider"] == "fake"
    # A code in flight does not move the order: it stays ``booked``.
    assert data["order"]["status"] == "booked"

    (attempt,) = await _attempts(db_session, order)
    assert str(attempt.id) == data["payment"]["payment_id"]
    assert attempt.status == "started"
    assert attempt.card_id == card_id
    # The reference is stored sealed, never as the provider spelled it.
    assert attempt.provider_reference not in (None, "ref-1")
    assert attempt.key_version == 1
    assert fake_provider.calls == [
        ("start", {"last4": "1111", "amount": "287500.00", "order_ref": str(order.id)})
    ]
    assert await _events(db_session, order) == ["payment.started"]


@respx.mock
async def test_start_with_raw_card_never_stores_or_logs_the_number(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)

    with caplog.at_level(logging.DEBUG):
        response = await _start(client, order, customer_headers)

    assert response.status_code == 200
    assert PAN not in response.text
    assert PAN not in caplog.text
    (attempt,) = await _attempts(db_session, order)
    assert attempt.card_id is None
    assert attempt.card_last4 == "1111"
    for row in (attempt.provider_data, attempt.error, attempt.provider_reference):
        assert PAN not in str(row)
    assert await db_session.scalar(select(CustomerCard)) is None  # not saved


@respx.mock
async def test_start_refreshes_amount_and_deadline_from_gts(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """GTS's price is the one it will debit at ticketing, so it wins."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer, amount=20, gts_response={})

    response = await _start(client, order, customer_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["order"]["amount"] == {"amount": "287500.00", "currency": "UZS"}
    assert data["payment"]["amount"] == {"amount": "287500.00", "currency": "UZS"}
    assert data["order_data"]["gds_pnr"] == "UBPLKW"
    (attempt,) = await _attempts(db_session, order)
    assert str(attempt.amount) == "287500.00"


@respx.mock
async def test_start_on_dead_gts_hold_is_offer_expired_and_cancels_locally(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="CB"))
    order = await make_order(db_session, customer)

    response = await _start(client, order, customer_headers)

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "offer_expired"
    await db_session.refresh(order)
    assert order.status == "cancelled"
    assert order.cancel_reason == "expired"
    assert order.cancelled_at is not None
    assert order.gts_status == "CB"
    assert await _attempts(db_session, order) == []
    assert fake_provider.calls == []
    assert await _events(db_session, order) == ["order.cancelled"]


@respx.mock
async def test_start_when_gts_cannot_be_read_charges_nothing(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(None)
    order = await make_order(db_session, customer)

    response = await _start(client, order, customer_headers)

    assert response.status_code == 502
    assert await _attempts(db_session, order) == []
    assert fake_provider.calls == []


@respx.mock
async def test_start_when_provider_declines_is_200_failed_and_retryable(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    from app.providers.payments.base import PaymentDeclined

    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    fake_provider.start_error = PaymentDeclined("card expired")

    response = await _start(client, order, customer_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["payment"]["status"] == "failed"
    assert data["payment"]["error"] == "card expired"
    # A declined card is the payment block's news; the order is still held.
    assert data["order"]["status"] == "booked"
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "failed"

    fake_provider.start_error = None
    again = await _start(client, order, {**customer_headers, "Idempotency-Key": "k2"})
    assert again.status_code == 200
    assert again.json()["data"]["payment"]["status"] == "awaiting_otp"


@respx.mock
async def test_start_when_provider_is_down_is_504_and_nothing_open(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    fake_provider.start_error = UpstreamTimeout("provider did not answer")

    response = await _start(client, order, customer_headers)

    assert response.status_code == 504
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "failed"
    await db_session.refresh(order)
    assert order.payment_status == "failed"


@respx.mock
async def test_second_start_abandons_first(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)

    first = await _start(client, order, customer_headers)
    second = await _start(
        client,
        order,
        customer_headers,
        {"card": {"number": "5555555555554444", "expire": "1131"}},
    )

    assert first.status_code == 200 and second.status_code == 200
    first_attempt, second_attempt = await _attempts(db_session, order)
    assert first_attempt.status == "abandoned"
    assert second_attempt.status == "started"
    assert second.json()["data"]["payment"]["card_last4"] == "4444"
    assert fake_provider.count("start") == 2


@respx.mock
async def test_identical_concurrent_starts_call_provider_once(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """A double tap sends one code: the twin meets the in-flight claim."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)

    responses = await asyncio.gather(
        _start(client, order, customer_headers), _start(client, order, customer_headers)
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert fake_provider.count("start") == 1
    assert len(await _attempts(db_session, order)) == 1


@respx.mock
async def test_start_while_confirming_is_409(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]
    fake_provider.confirm_outcomes = [UpstreamTimeout("lost")]
    await _confirm(client, order, customer_headers, payment_id)

    response = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "k2"}
    )

    assert response.status_code == 409
    assert "being confirmed" in response.json()["errors"][0]["message"]


@respx.mock
async def test_strangers_order_is_404(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    stranger = await make_customer(db_session, "stranger@example.com")
    order = await make_order(db_session, stranger)

    response = await _start(client, order, customer_headers)

    assert response.status_code == 404
    assert fake_provider.calls == []


async def test_start_needs_exactly_one_card(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    order = await make_order(db_session, customer)

    neither = await _start(client, order, customer_headers, {})
    both = await _start(
        client, order, customer_headers, {"card_id": str(uuid.uuid4()), **RAW_CARD}
    )

    assert neither.status_code == 422
    assert both.status_code == 422
    assert PAN not in both.text


# --- confirm --------------------------------------------------------------------


@respx.mock
async def test_confirm_pays_and_stamps_card_last_used(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_ticketing()
    order = await make_order(db_session, customer)
    card_id = await _save_card(db_session, customer)
    started = await _start(client, order, customer_headers, {"card_id": str(card_id)})
    payment_id = started.json()["data"]["payment"]["payment_id"]

    response = await _confirm(client, order, customer_headers, payment_id, "123456")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["payment"]["status"] == "paid"
    assert data["payment"]["paid_at"] is not None
    # Paid and ticketed in the same request — GTS issued at once.
    assert data["order"]["status"] == "ticketed"
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "paid"
    assert attempt.paid_at is not None
    assert fake_provider.calls[-1] == (
        "confirm",
        {"reference": "ref-1", "otp": "123456"},
    )
    card = await db_session.get(CustomerCard, card_id)
    assert card is not None and card.last_used_at is not None
    assert await _events(db_session, order) == [
        "payment.started",
        "payment.confirming",
        "payment.paid",
        "ticketing.processing",
        "ticketing.requested",
        "ticketing.ticketed",
    ]


@respx.mock
async def test_confirm_declined_is_200_with_failed_block_and_retryable(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]
    fake_provider.confirm_outcomes = [
        PaymentOutcome("failed", error="insufficient funds")
    ]

    response = await _confirm(client, order, customer_headers, payment_id)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["payment"]["status"] == "failed"
    assert data["payment"]["error"] == "insufficient funds"
    assert data["order"]["status"] == "booked"

    again = await _start(client, order, {**customer_headers, "Idempotency-Key": "k2"})
    assert again.status_code == 200
    assert again.json()["data"]["payment"]["status"] == "awaiting_otp"
    assert [attempt.status for attempt in await _attempts(db_session, order)] == [
        "failed",
        "started",
    ]


@respx.mock
async def test_confirm_timeout_leaves_confirming_and_reports_processing(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]
    fake_provider.confirm_outcomes = [UpstreamTimeout("lost on the way back")]

    response = await _confirm(client, order, customer_headers, payment_id)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["payment"]["status"] == "processing"
    # The unknown answer is the payment block's to report; the order waits.
    assert data["order"]["status"] == "booked"
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "confirming"

    # Asking again is a read: the charge is not sent a second time.
    again = await _confirm(
        client, order, {**customer_headers, "Idempotency-Key": "k2"}, payment_id
    )
    assert again.status_code == 200
    assert again.json()["data"]["payment"]["status"] == "processing"
    assert fake_provider.count("confirm") == 1


@respx.mock
async def test_confirm_twice_concurrently_calls_provider_once(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]

    responses = await asyncio.gather(
        _confirm(client, order, customer_headers, payment_id),
        _confirm(client, order, customer_headers, payment_id),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert fake_provider.count("confirm") == 1
    await db_session.refresh(order)
    assert order.payment_status == "paid"


@respx.mock
async def test_replay_returns_fresh_state(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """The same confirm twice: one charge, and the repeat shows what is true now."""
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]

    first = await _confirm(client, order, customer_headers, payment_id)
    second = await _confirm(client, order, customer_headers, payment_id)

    assert first.status_code == second.status_code == 200
    assert second.json()["data"]["payment"]["status"] == "paid"
    assert fake_provider.count("confirm") == 1


@respx.mock
async def test_confirm_wrong_payment_id_is_409(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    await _start(client, order, customer_headers)

    response = await _confirm(client, order, customer_headers, str(uuid.uuid4()))

    assert response.status_code == 409
    assert fake_provider.count("confirm") == 0


@respx.mock
async def test_confirm_after_deadline_is_offer_expired_and_no_charge(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """GTS still said ``BO`` at start but its deadline has passed by confirm."""
    mock_gts_signin()
    past = (utcnow() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mock_gts_order(gts_order_body(ticket_time_limit=past))
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]

    response = await _confirm(client, order, customer_headers, payment_id)

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "offer_expired"
    assert fake_provider.count("confirm") == 0
    await db_session.refresh(order)
    assert order.status == "cancelled" and order.cancel_reason == "expired"
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "abandoned"


async def test_confirm_without_start_is_409(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    order = await make_order(db_session, customer)
    response = await _confirm(client, order, customer_headers, str(uuid.uuid4()))
    assert response.status_code == 409


# --- the sweep: lost answers ---------------------------------------------------------


async def _age_attempt(
    session: AsyncSession, order: Order, *, updated: timedelta, created: timedelta
) -> PaymentAttempt:
    (attempt,) = await _attempts(session, order)
    attempt.updated_at = utcnow() - updated
    attempt.created_at = utcnow() - created
    await session.commit()
    return attempt


async def _leave_confirming(
    client: httpx.AsyncClient,
    order: Order,
    headers: dict[str, str],
    provider: FakeProvider,
) -> str:
    started = await _start(client, order, headers)
    payment_id: str = started.json()["data"]["payment"]["payment_id"]
    provider.confirm_outcomes = [UpstreamError("lost")]
    await _confirm(client, order, headers, payment_id)
    return payment_id


@respx.mock
async def test_sweep_settles_confirming_via_status_paid(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_ticketing()
    order = await make_order(db_session, customer)
    await _leave_confirming(client, order, customer_headers, fake_provider)
    await _age_attempt(
        db_session, order, updated=timedelta(minutes=3), created=timedelta(minutes=3)
    )
    fake_provider.status_outcomes = [PaymentOutcome("paid", reference="ref-1")]

    settled = await service.settle_stale_confirmations(db_session)

    assert settled == 1
    await db_session.refresh(order)
    assert order.payment_status == "paid"
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "paid"
    assert fake_provider.calls[-1] == ("status", {"reference": "ref-1"})
    assert "payment.paid" in await _events(db_session, order)
    paid = await db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == order.id, OrderEvent.event == "payment.paid"
        )
    )
    assert paid is not None and paid.actor == "system" and paid.request_id


@respx.mock
async def test_sweep_settles_confirming_via_status_failed(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    await _leave_confirming(client, order, customer_headers, fake_provider)
    await _age_attempt(
        db_session, order, updated=timedelta(minutes=3), created=timedelta(minutes=3)
    )
    fake_provider.status_outcomes = [PaymentOutcome("failed", error="declined")]

    assert await service.settle_stale_confirmations(db_session) == 1
    await db_session.refresh(order)
    assert order.payment_status == "failed"


@respx.mock
async def test_sweep_ignores_fresh_confirming_and_pending_answers(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    await _leave_confirming(client, order, customer_headers, fake_provider)

    # Too fresh: the provider's own answer may still be on its way.
    assert await service.settle_stale_confirmations(db_session) == 0
    assert fake_provider.count("status") == 0

    # Old enough to ask, but the provider is still deciding: wait.
    await _age_attempt(
        db_session, order, updated=timedelta(minutes=3), created=timedelta(minutes=3)
    )
    assert await service.settle_stale_confirmations(db_session) == 0
    assert fake_provider.count("status") == 1
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "confirming"


@respx.mock
async def test_sweep_gives_up_after_max_wait(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    await _leave_confirming(client, order, customer_headers, fake_provider)
    await _age_attempt(
        db_session, order, updated=timedelta(minutes=3), created=timedelta(minutes=20)
    )

    with caplog.at_level(logging.ERROR):
        assert await service.settle_stale_confirmations(db_session) == 1

    assert "payment_unconfirmed" in caplog.text
    await db_session.refresh(order)
    assert order.payment_status == "failed"
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "failed"
    assert attempt.error == "the provider never confirmed this charge"


# --- the sweep: expired holds ------------------------------------------------------


@respx.mock
async def test_expiry_confirms_with_gts_before_cancelling(
    client: httpx.AsyncClient,
    customer: Customer,
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    route = mock_gts_order(gts_order_body(status="BO"))
    order = await make_order(
        db_session, customer, ticket_time_limit_at=utcnow() - timedelta(minutes=20)
    )

    # GTS still holds it: nothing happens but a refreshed read.
    assert await service.expire_unpaid(db_session) == 0
    await db_session.refresh(order)
    assert order.status == "booked"
    assert order.gts_checked_at is not None
    assert order.ticket_time_limit_at > utcnow()  # the read-back's deadline

    # Within the grace period the same order is not asked about again.
    order.ticket_time_limit_at = utcnow() - timedelta(minutes=20)
    await db_session.commit()
    assert await service.expire_unpaid(db_session) == 0
    assert route.call_count == 1

    # Past it, GTS says the hold is gone: now it is released here too.
    order.gts_checked_at = utcnow() - timedelta(minutes=20)
    await db_session.commit()
    route.mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": gts_order_body(status="STATUS_VOID")},
        )
    )
    assert await service.expire_unpaid(db_session) == 1
    await db_session.refresh(order)
    assert order.status == "cancelled"
    assert order.cancel_reason == "expired"
    assert await _events(db_session, order) == ["order.cancelled"]


@respx.mock
async def test_expiry_skips_open_attempt(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    route = mock_gts_order(gts_order_body(status="BO"))
    order = await make_order(db_session, customer)
    await _leave_confirming(client, order, customer_headers, fake_provider)
    route.mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": gts_order_body(status="CB")}
        )
    )
    order.ticket_time_limit_at = utcnow() - timedelta(minutes=20)
    order.gts_checked_at = None
    await db_session.commit()

    assert await service.expire_unpaid(db_session) == 0
    await db_session.refresh(order)
    assert order.status == "booked"

    # A code nobody typed for long enough is forgotten, and the hold released.
    (attempt,) = await _attempts(db_session, order)
    attempt.status = "started"
    attempt.created_at = utcnow() - timedelta(minutes=15)
    order.gts_checked_at = None
    await db_session.commit()
    assert await service.expire_unpaid(db_session) == 1
    await db_session.refresh(attempt)
    assert attempt.status == "abandoned"


@respx.mock
async def test_expiry_leaves_unknown_reads_alone(
    customer: Customer, db_session: AsyncSession
) -> None:
    mock_gts_signin()
    mock_gts_order(None)
    order = await make_order(
        db_session, customer, ticket_time_limit_at=utcnow() - timedelta(minutes=20)
    )
    assert await service.expire_unpaid(db_session) == 0
    await db_session.refresh(order)
    assert order.status == "booked"


# --- which provider ---------------------------------------------------------------


async def test_no_provider_configured_is_502_and_no_attempt(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "debug", False)
    order = await make_order(db_session, customer)

    response = await _start(client, order, customer_headers)

    assert response.status_code == 502
    assert await _attempts(db_session, order) == []


async def test_sandbox_only_in_debug(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings
    from app.providers.payments.sandbox import SandboxProvider

    assert isinstance(
        await payments_service.payment_provider(db_session), SandboxProvider
    )
    monkeypatch.setattr(settings, "debug", False)
    with pytest.raises(UpstreamError):
        await payments_service.payment_provider(db_session)


async def test_sandbox_codes() -> None:
    from app.core.money import Money
    from app.providers.payments.base import CardDetails
    from app.providers.payments.sandbox import SandboxProvider

    sandbox = SandboxProvider()
    started = await sandbox.start(
        card=CardDetails(number=PAN, expire="1230"),
        amount=Money(amount="0.00", currency="USD"),
        order_ref="o",
    )
    assert started.reference.startswith("sbx-")
    assert PAN not in repr(started)
    assert (
        await sandbox.confirm(reference=started.reference, otp="000000")
    ).status == "paid"
    assert (
        await sandbox.confirm(reference=started.reference, otp="111111")
    ).status == "failed"
    assert (
        await sandbox.confirm(reference=started.reference, otp="333333")
    ).status == "pending"
    assert (
        await sandbox.confirm(reference=started.reference, otp="9")
    ).error == "wrong code"
    with pytest.raises(UpstreamTimeout):
        await sandbox.confirm(reference=started.reference, otp="222222")
    assert (await sandbox.status(reference=started.reference)).status == "pending"


async def test_customer_token_cannot_reach_admin_but_payment_needs_owner(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    stranger = await make_customer(db_session, "stranger@example.com")
    order = await make_order(db_session, stranger)
    response = await client.post(_payment_url(order), json=RAW_CARD)
    assert response.status_code == 401
    assert (
        await client.post(_payment_url(order), json=RAW_CARD, headers=bearer(stranger))
    ).status_code != 401


# --- Payme, end to end ---------------------------------------------------------------
#
# The adapter's own branches live in ``test_payme.py``; these prove the panel's
# row reaches ``payment_provider``, that the orders module stores what the
# adapter hands back and nothing more, and that the sweep settles a lost
# ``receipts.pay`` by reading the receipt.

PAYME_ADMIN_URL = "/api/v1/admin/integrations/payments/payme/"


async def _enable_payme(
    client: httpx.AsyncClient, staff_headers: dict[str, str]
) -> None:
    response = await client.patch(
        PAYME_ADMIN_URL,
        json={"credentials": PAYME_CREDENTIALS, "enabled": True},
        headers=staff_headers,
    )
    assert response.status_code == 200, response.text


@respx.mock
async def test_payme_charges_and_tickets_end_to_end(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_ticketing()
    payme = mock_payme()
    await _enable_payme(client, staff_headers)
    order = await make_order(db_session, customer)

    started = await _start(client, order, customer_headers)

    assert started.status_code == 200, started.text
    data = started.json()["data"]
    assert data["payment"]["status"] == "awaiting_otp"
    assert data["payment"]["provider"] == "payme"
    assert data["payment"]["phone_hint"] == "99890*****31"
    assert data["payment"]["card_last4"] == "1111"
    assert payme.methods == ["receipts.create", "cards.create", "cards.get_verify_code"]
    # GTS's price, in tiyin, against our order id.
    assert payme.params("receipts.create")["amount"] == 28750000
    assert payme.params("receipts.create")["account"] == {"order_id": str(order.id)}
    assert payme.params("cards.create")["card"] == {"number": PAN, "expire": "1230"}
    (attempt,) = await _attempts(db_session, order)
    assert attempt.provider == "payme"
    assert attempt.status == "started"
    # The reference is sealed; the token is in nothing a reader can open.
    assert attempt.provider_reference is not None
    assert PAYME_TOKEN not in attempt.provider_reference
    assert PAYME_TOKEN not in json.dumps(attempt.provider_data)
    assert attempt.provider_data == {
        "start": {"receipt": PAYME_RECEIPT, "card": "860006******6311", "wait": 60000}
    }
    assert PAYME_TOKEN not in started.text
    assert PAN not in started.text

    confirmed = await _confirm(
        client, order, customer_headers, data["payment"]["payment_id"], "666666"
    )

    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["data"]
    assert result["payment"]["status"] == "paid"
    assert result["order"]["status"] == "ticketed"
    assert payme.methods[3:] == ["cards.verify", "receipts.pay"]
    assert payme.params("cards.verify") == {"token": PAYME_TOKEN, "code": "666666"}
    assert payme.params("receipts.pay") == {"id": PAYME_RECEIPT, "token": PAYME_TOKEN}
    await db_session.refresh(attempt)
    assert attempt.status == "paid"
    assert attempt.provider_data["outcome"] == {"receipt": PAYME_RECEIPT, "state": 4}
    assert PAYME_TOKEN not in confirmed.text
    assert await _events(db_session, order) == [
        "payment.started",
        "payment.confirming",
        "payment.paid",
        "ticketing.processing",
        "ticketing.requested",
        "ticketing.ticketed",
    ]


@respx.mock
async def test_payme_refusals_reach_the_customer_as_the_contract_says(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    await _enable_payme(client, staff_headers)
    order = await make_order(db_session, customer)

    # A refused card: 200, ``failed``, Payme's words, and a new start works.
    mock_payme({"cards.create": RpcError(-31300, {"uz": "Karta raqami noto'g'ri"})})
    declined = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "payme-1"}
    )
    assert declined.status_code == 200
    assert declined.json()["data"]["payment"]["status"] == "failed"
    assert declined.json()["data"]["payment"]["error"] == "Karta raqami noto'g'ri"

    # A refused receipt is our problem, not the card's: 502 with Payme's words.
    respx.reset()
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_payme({"receipts.create": RpcError(-31001, "Недопустимая сумма")})
    refused = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "payme-2"}
    )
    assert refused.status_code == 502
    assert refused.json()["meta"] == {
        "upstream": {"code": -31001, "message": "Недопустимая сумма"}
    }

    # A wrong code: 200, ``failed``, nothing paid, and the next start opens
    # a fresh attempt with a fresh SMS.
    respx.reset()
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    payme = mock_payme({"cards.verify": RpcError(-31103, "Неверный код")})
    started = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "payme-3"}
    )
    payment_id = started.json()["data"]["payment"]["payment_id"]
    wrong = await _confirm(client, order, customer_headers, payment_id, "000000")
    assert wrong.status_code == 200
    assert wrong.json()["data"]["payment"]["status"] == "failed"
    assert wrong.json()["data"]["payment"]["error"] == "Неверный код"
    assert payme.count("receipts.pay") == 0
    again = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "payme-4"}
    )
    assert again.json()["data"]["payment"]["status"] == "awaiting_otp"
    assert payme.count("cards.get_verify_code") == 2


@respx.mock
async def test_payme_lost_pay_answer_is_settled_by_reading_the_receipt(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_ticketing()
    payme = mock_payme({"receipts.pay": httpx.ReadTimeout("gone")})
    await _enable_payme(client, staff_headers)
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]

    lost = await _confirm(client, order, customer_headers, payment_id, "666666")

    assert lost.status_code == 200
    assert lost.json()["data"]["payment"]["status"] == "processing"
    assert payme.count("receipts.pay") == 1
    # Too fresh to ask about; then old enough, and the receipt says paid.
    assert await service.settle_stale_confirmations(db_session) == 0
    assert payme.count("receipts.check") == 0
    attempt = await _age_attempt(
        db_session, order, updated=timedelta(minutes=3), created=timedelta(minutes=3)
    )
    assert await service.settle_stale_confirmations(db_session) == 1
    assert payme.params("receipts.check") == {"id": PAYME_RECEIPT}
    assert payme.count("receipts.pay") == 1
    await db_session.refresh(attempt)
    assert attempt.status == "paid"
    await db_session.refresh(order)
    assert order.payment_status == "paid"


# --- the sweep and the panel: guards --------------------------------------------------


async def _stale_confirming(
    session: AsyncSession, order: Order, *, provider: str, reference: str
) -> PaymentAttempt:
    from app.core.crypto import encrypt

    sealed, version = encrypt(reference)
    attempt = PaymentAttempt(
        order_id=order.id,
        customer_id=order.customer_id,
        provider=provider,
        status="confirming",
        amount=order.amount,
        currency=order.currency,
        card_last4="1111",
        provider_reference=sealed,
        key_version=version,
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    attempt.updated_at = utcnow() - timedelta(minutes=3)
    attempt.created_at = utcnow() - timedelta(minutes=3)
    await session.commit()
    return attempt


async def test_sweep_outlives_a_provider_it_cannot_resolve(
    customer: Customer,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No provider, or one whose settings are unfinished, ends this question
    — never the sweep: the ticketing and expiry passes behind it must run."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "debug", False)
    # Nothing to ask about: the provider is not even looked up.
    assert await service.settle_stale_confirmations(db_session) == 0

    order = await make_order(db_session, customer)
    attempt = await _stale_confirming(
        db_session, order, provider="fake", reference="ref-1"
    )
    with caplog.at_level(logging.ERROR):
        assert await service.settle_stale_confirmations(db_session) == 0
    assert any(
        "payment_provider_unavailable" in record.getMessage()
        for record in caplog.records
    )
    await db_session.refresh(attempt)
    assert attempt.status == "confirming"


@respx.mock
async def test_an_attempt_is_settled_only_by_the_provider_that_started_it(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.providers import payments as payment_providers

    mock_gts_signin()
    mock_gts_order(gts_order_body())
    payme = mock_payme()
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    payment_id = started.json()["data"]["payment"]["payment_id"]

    # The panel switches to Payme while the code is being typed.
    payment_providers.set_provider(None)
    await _enable_payme(client, staff_headers)

    stale = await _confirm(client, order, customer_headers, payment_id, "000000")

    assert stale.status_code == 409
    assert "another provider" in stale.json()["errors"][0]["message"]
    assert payme.methods == []
    assert fake_provider.count("confirm") == 0
    (abandoned,) = await _attempts(db_session, order)
    assert abandoned.status == "abandoned"
    # The next start belongs to Payme.
    again = await _start(
        client, order, {**customer_headers, "Idempotency-Key": "payme-after"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["data"]["payment"]["provider"] == "payme"
    assert payme.methods == ["receipts.create", "cards.create", "cards.get_verify_code"]

    # A charge the old provider may have made is never asked of the new one:
    # the sweep and ``sync/`` leave it, loudly, to a human.
    other = await make_order(db_session, customer, gts_order_number=777)
    confirming = await _stale_confirming(
        db_session, other, provider="fake", reference="ref-9"
    )
    with caplog.at_level(logging.ERROR):
        assert await service.settle_stale_confirmations(db_session) == 0
    assert any(
        "payment_provider_mismatch" in record.getMessage() for record in caplog.records
    )
    assert payme.count("receipts.check") == 0
    mock_gts_order(gts_order_body(order_number=777), order_number=777)
    synced = await client.post(
        f"/api/v1/admin/orders/{other.id}/sync/", headers=staff_headers
    )
    assert synced.status_code == 200, synced.text
    assert payme.count("receipts.check") == 0
    await db_session.refresh(confirming)
    assert confirming.status == "confirming"
