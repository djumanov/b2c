"""Compensation — the promise that money is never lost quietly (PROJECT.md D3).

Everything here happens after a customer has paid and a ticket has not come
out of it. That is the worst state the system can be in, and the only
acceptable ending is that the money goes back or a person is told it did not.

Both outside systems are doubles: the vertical from ``test_saga_ticketing``
and the payment provider from ``test_payments``. What is under test is the
order between them and what happens when either says no.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.api.errors import UpstreamError
from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderPayment, OrderRefund
from app.modules.orders.states import OrderStatus, RefundKind, RefundState
from app.providers.payments import clear_overrides, set_provider
from app.providers.payments.base import PaymentProviderCode, RefundStatus
from app.providers.products.base import registry
from app.providers.products.flight import FlightAdapter
from app.tasks import orders as tasks
from tests.integration.test_payments import RecordingProvider
from tests.integration.test_saga_ticketing import PAID, FakeOperations

_counter = 0


@pytest.fixture
def world(connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A pinned vertical and a pinned provider, both answering as told."""
    operations = FakeOperations()
    registry.register(operations)
    provider = RecordingProvider()
    set_provider(PaymentProviderCode.PAYME, provider)

    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: factory)

    async def no_credential_needed(session: object) -> object:
        return object()

    monkeypatch.setattr(tasks.products_service, "gts_client", no_credential_needed)
    yield operations, provider
    registry.register(FlightAdapter())
    clear_overrides()


async def _compensating(
    session: AsyncSession, customer: Customer, **overrides: Any
) -> Order:
    """An order that paid, failed to ticket, and is waiting for its money back.

    Built directly rather than by running ticketing first: what happens *before*
    ``refunding`` is ``test_saga_ticketing``'s subject, and reaching this state
    through it would make every case here depend on that one.
    """
    global _counter
    _counter += 1
    fields: dict[str, Any] = {
        "order_no": f"B2C-2608-4{_counter:05d}",
        "customer_id": customer.id,
        "product": "flight",
        "status": OrderStatus.REFUNDING.value,
        "provider_order_number": f"4{_counter:05d}",
        "amount_total": PAID,
        "amount_paid": PAID,
        "currency": "UZS",
        "paid_at": datetime.now(UTC),
        "next_attempt_at": datetime.now(UTC),
        **overrides,
    }
    order = Order(**fields)
    session.add(order)
    await session.flush()
    session.add(
        OrderPayment(
            order_id=order.id,
            provider="payme",
            provider_ref="receipt-1",
            status="paid",
            flow="redirect",
            amount=PAID,
            currency="UZS",
            paid_at=datetime.now(UTC),
        )
    )
    session.add(
        OrderRefund(
            order_id=order.id,
            kind=RefundKind.AUTO.value,
            status=RefundState.APPROVED.value,
            amount=PAID,
            currency="UZS",
            reason="The provider refused to issue the ticket",
        )
    )
    await session.commit()
    await session.refresh(order)
    return order


async def _refund_row(session: AsyncSession, order: Order) -> OrderRefund:
    row = await session.scalar(
        select(OrderRefund).where(OrderRefund.order_id == order.id)
    )
    assert row is not None
    await session.refresh(row)
    return row


async def _events(session: AsyncSession, order: Order) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT action FROM order_events WHERE order_id = :id ORDER BY created_at"
        ),
        {"id": order.id},
    )
    return [row[0] for row in rows]


# --- the money goes back ---------------------------------------------------------


async def test_the_money_goes_back_and_the_seat_is_released(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    operations, provider = world
    order = await _compensating(session, customer)

    await tasks._refund(order.id)

    await session.refresh(order)
    assert order.status == "refunded"
    assert order.amount_refunded == PAID
    assert order.next_attempt_at is None

    refund = await _refund_row(session, order)
    assert refund.status == "succeeded"
    assert refund.provider_refund_ref == "refund-receipt-1"

    # The provider's own reference for the charge, not ours — a refund is an
    # operation on their receipt.
    assert provider.refunded == [{"transaction_ref": "receipt-1", "amount": PAID}]
    # And the reservation was released, so the seat goes back on sale.
    assert operations.calls == ["cancel"]
    assert await _events(session, order) == ["refund.succeeded"]


async def test_a_provider_that_will_not_release_the_seat_does_not_hold_the_money(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """The hold lapses on its own (GTS.md §11). Letting our tidiness block a
    customer's refund would be the wrong trade."""
    operations, provider = world
    operations.cancel_fails = UpstreamError("the reservation is already gone")
    order = await _compensating(session, customer)

    await tasks._refund(order.id)

    await session.refresh(order)
    assert order.status == "refunded"
    assert len(provider.refunded) == 1


async def test_refunding_twice_sends_the_money_once(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """The sweep and a direct send can both arrive. The task re-reads the order
    and leaves quietly once it is no longer refunding."""
    _, provider = world
    order = await _compensating(session, customer)

    await tasks._refund(order.id)
    await tasks._refund(order.id)

    assert len(provider.refunded) == 1
    await session.refresh(order)
    assert order.status == "refunded"


# --- when the refund itself fails -------------------------------------------------


async def test_a_refund_that_fails_is_retried(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    _, provider = world
    provider.refund_refuses = UpstreamError("the provider is unavailable")
    order = await _compensating(session, customer)

    await tasks._refund(order.id)

    await session.refresh(order)
    assert order.status == "refunding"
    assert order.attempts == 1
    assert order.next_attempt_at is not None
    # The refund is still open, so the next pass picks up the same one rather
    # than opening a second race for the same money.
    refund = await _refund_row(session, order)
    assert refund.status == "processing"
    assert await _events(session, order) == ["refund.retry"]


async def test_a_provider_that_refuses_outright_is_also_a_retry(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """A refusal and an outage are the same to a customer waiting for money —
    both are worth another try before anybody is woken up."""
    _, provider = world
    provider.refund_status = RefundStatus.FAILED
    order = await _compensating(session, customer)

    await tasks._refund(order.id)

    await session.refresh(order)
    assert order.status == "refunding"


async def test_a_refund_that_keeps_failing_ends_with_a_person(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """``needs_attention`` — the state PROJECT.md D3 promises exists. Money has
    moved, no ticket came of it, and the refund did not work either. Nothing
    automatic is left to try, and the one unacceptable ending is silence."""
    _, provider = world
    provider.refund_refuses = UpstreamError("the provider is unavailable")
    order = await _compensating(session, customer, attempts=7)

    await tasks._refund(order.id)

    await session.refresh(order)
    assert order.status == "needs_attention"
    assert order.attention_reason == "refund_failed"
    assert order.next_attempt_at is None
    refund = await _refund_row(session, order)
    assert refund.status == "failed"
    assert "unavailable" in (refund.failure_message or "")
    assert await _events(session, order) == ["refund.failed"]


# --- the states that cannot be compensated automatically --------------------------


async def test_an_order_with_nothing_settled_is_not_guessed_at(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """Sending money against a charge that was never taken would be inventing a
    transaction. A person is asked instead."""
    _, provider = world
    order = await _compensating(session, customer)
    await session.execute(
        text("UPDATE order_payments SET provider_ref = NULL WHERE order_id = :id"),
        {"id": order.id},
    )
    await session.commit()

    await tasks._refund(order.id)

    await session.refresh(order)
    assert order.status == "needs_attention"
    assert provider.refunded == []


async def test_refunding_with_no_refund_open_asks_a_person(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """A state only a bug or a hand edit produces. Inventing a refund to cover
    it would be guessing about money."""
    _, provider = world
    order = await _compensating(session, customer)
    await session.execute(
        text("DELETE FROM order_refunds WHERE order_id = :id"), {"id": order.id}
    )
    await session.commit()

    await tasks._refund(order.id)

    await session.refresh(order)
    assert order.status == "needs_attention"
    assert provider.refunded == []


async def test_a_refund_still_waiting_for_a_person_is_not_taken(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """A customer's request waits for approval. Nothing automatic may push it
    through, or the approval step would be decoration (``O11``)."""
    _, provider = world
    order = await _compensating(session, customer)
    await session.execute(
        text(
            "UPDATE order_refunds SET kind = 'customer', status = 'requested'"
            " WHERE order_id = :id"
        ),
        {"id": order.id},
    )
    await session.commit()

    await tasks._refund(order.id)

    assert provider.refunded == []
    await session.refresh(order)
    # It ends up needing a person, which is what "waiting for approval" means
    # while the approval surface itself is still being built.
    assert order.status == "needs_attention"


async def test_an_order_that_is_not_refunding_is_left_alone(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    _, provider = world
    order = await _compensating(session, customer, status=OrderStatus.PAID.value)

    await tasks._refund(order.id)

    assert provider.refunded == []
    await session.refresh(order)
    assert order.status == "paid"


async def test_an_order_that_vanished_is_not_an_error(
    session: AsyncSession, world: Any
) -> None:
    await tasks._refund(uuid.uuid4())


# --- the sweep picks compensation up too ------------------------------------------


async def test_the_sweep_sends_a_due_refund(
    session: AsyncSession,
    customer: Customer,
    world: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(tasks.refund, "delay", lambda order_id: sent.append(order_id))
    monkeypatch.setattr(tasks.ticket, "delay", lambda order_id: pytest.fail("not this"))
    order = await _compensating(
        session, customer, next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    await tasks._run_due()

    assert sent == [str(order.id)]


async def test_only_one_refund_may_be_open_at_a_time(
    session: AsyncSession, customer: Customer, world: Any
) -> None:
    """The database says so, not the code: a second refund opened while the
    first is running would race it to the same money."""
    from sqlalchemy.exc import IntegrityError

    order = await _compensating(session, customer)
    session.add(
        OrderRefund(
            order_id=order.id,
            kind=RefundKind.ADMIN.value,
            status=RefundState.APPROVED.value,
            amount=Decimal("1.00"),
            currency="UZS",
        )
    )

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
