"""``/webhooks/payments/{provider}/`` — money arriving (API.md §40).

The three rules the contract states, held to against a real database:

* the envelope does not apply — the answer is the provider's protocol;
* a bad signature changes **nothing**;
* a repeat settles once, and says so both times.

Plus the one the design adds: money that arrives for a different amount is not
a payment for this order, and the order goes to the queue a person works
through rather than to ``paid`` (order-system/03-design.md T5).
"""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderPayment
from app.providers.payments import clear_overrides, set_provider
from app.providers.payments.base import PaymentProviderCode
from tests.integration.conftest import customer_headers_for
from tests.integration.test_payments import (
    ORDERS,
    RETURN_URL,
    RecordingProvider,
    _booked,
)

WEBHOOK = "/api/v1/webhooks/payments/payme/"


@pytest.fixture
async def provider() -> Any:
    pinned = RecordingProvider()
    set_provider(PaymentProviderCode.PAYME, pinned)
    yield pinned
    clear_overrides()


@pytest.fixture
async def installation() -> None:
    from app.modules.settings import cache as settings_cache

    await settings_cache.write(
        {
            "site": {"domain": "brand.uz"},
            "products": [{"code": "flight", "enabled": True}],
        }
    )


async def _paying(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> tuple[Order, dict[str, Any]]:
    """A booked order with one attempt open at the provider."""
    order = await _booked(session, customer)
    started = await api.post(
        f"{ORDERS}{order.id}/transactions/",
        json={"method": "payme", "return_url": RETURN_URL},
        headers={
            **customer_headers_for(customer),
            "Idempotency-Key": str(uuid.uuid4()),
        },
    )
    assert started.status_code == 201
    return order, started.json()["data"]


async def _events(session: AsyncSession, order: Order) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT action FROM order_events WHERE order_id = :id ORDER BY created_at"
        ),
        {"id": order.id},
    )
    return [row[0] for row in rows]


async def test_a_callback_settles_the_order(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    installation: None,
    provider: RecordingProvider,
) -> None:
    order, attempt = await _paying(api, session, customer)

    response = await api.post(WEBHOOK, json={"method": "PerformTransaction"})

    assert response.status_code == 200
    # The envelope does not apply here: a JSON-RPC caller handed
    # ``{status, data, errors, meta}`` would treat every answer as malformed.
    assert response.json() == {"result": {"state": 2}}

    await session.refresh(order)
    assert order.status == "paid"
    assert order.paid_at is not None
    assert order.amount_paid == Decimal("1250000.00")
    # The ticketing step is due immediately; the poller is the safety net for
    # the moment the task fails to reach the queue (``O13``).
    assert order.next_attempt_at is not None

    row = await session.scalar(
        select(OrderPayment).where(OrderPayment.id == uuid.UUID(attempt["id"]))
    )
    assert row is not None
    assert row.status == "paid"
    assert row.paid_at is not None
    # The reference the provider named the charge by, bound on its first call.
    assert row.provider_ref == "receipt-1"

    assert await _events(session, order) == ["payment.settled"]


async def test_a_repeated_callback_settles_once(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Providers resend. "This is already paid" is a fact, not a failure — and
    the guard that makes it harmless is the unique index on
    ``(provider, provider_ref)``, not the handler's care (X6)."""
    order, _ = await _paying(api, session, customer)

    first = await api.post(WEBHOOK, json={"method": "PerformTransaction"})
    second = await api.post(WEBHOOK, json={"method": "PerformTransaction"})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    await session.refresh(order)
    assert order.status == "paid"
    # One settlement, one line in the history.
    assert await _events(session, order) == ["payment.settled"]


async def test_a_bad_signature_changes_nothing(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Unconditional rule (API.md §40). The answer's shape is the provider's —
    Payme wants a 200 with a JSON-RPC error, because it reads a 401 as a reason
    to retry blindly — but what is checked is that nothing moved."""
    order, attempt = await _paying(api, session, customer)
    provider.signature_ok = False

    response = await api.post(WEBHOOK, json={"method": "PerformTransaction"})

    assert response.status_code == 200
    assert response.json() == {"error": {"code": -32504, "message": "bad signature"}}

    await session.refresh(order)
    assert order.status == "booked"
    assert order.amount_paid == Decimal(0)
    row = await session.scalar(
        select(OrderPayment).where(OrderPayment.id == uuid.UUID(attempt["id"]))
    )
    assert row is not None
    assert row.status == "pending"
    assert row.provider_ref is None
    assert await _events(session, order) == []


async def test_money_for_a_different_amount_needs_a_person(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    installation: None,
    provider: RecordingProvider,
) -> None:
    """The attempt keeps the amount that was asked for, so an order repriced in
    between settles against a sum nobody agreed to. There is nowhere safe to
    stand: the money has moved, and buying a ticket with it would compound the
    problem."""
    order, _ = await _paying(api, session, customer)
    order.amount_total = Decimal("1300000.00")
    await session.commit()

    response = await api.post(WEBHOOK, json={"method": "PerformTransaction"})

    assert response.status_code == 200
    await session.refresh(order)
    assert order.status == "needs_attention"
    assert order.attention_reason == "payment_amount_mismatch"
    assert await _events(session, order) == ["payment.mismatched"]


async def test_a_callback_for_no_attempt_of_ours_is_a_404(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    installation: None,
    provider: RecordingProvider,
) -> None:
    """A provider quoting an order we have no open attempt for is not talking
    about anything of ours, and saying so touches nothing."""
    order = await _booked(session, customer)
    provider.reference = str(order.id)

    response = await api.post(WEBHOOK, json={"method": "PerformTransaction"})

    assert response.status_code == 404
    await session.refresh(order)
    assert order.status == "booked"


async def test_an_unknown_provider_is_a_404(api: AsyncClient) -> None:
    response = await api.post("/api/v1/webhooks/payments/stripe/", json={})

    assert response.status_code == 404


async def test_a_callback_that_settles_nothing_moves_nothing(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Payme's protocol takes several exchanges before the money is real, and
    only one of them means it moved. The rest are answered without anything
    changing."""
    order, _ = await _paying(api, session, customer)
    provider.settles = False

    response = await api.post(WEBHOOK, json={"method": "CheckPerformTransaction"})

    assert response.status_code == 200
    await session.refresh(order)
    assert order.status == "booked"
    assert await _events(session, order) == []
