"""``orders.service.transition`` against a real database.

``test_order_states.py`` proves the table is right. This proves the machine
*uses* it: one history line per move, a refusal that is a ``409`` rather than a
silent no-op, timestamps that are written once, and — the reason the row is
locked at all — two callers racing where only one may win.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.errors import Conflict, NotFound
from app.modules.customers.models import Customer
from app.modules.orders import repository, service
from app.modules.orders.models import Order
from app.modules.orders.states import Actor, EventAction, OrderStatus

_counter = 0


async def _order(session: AsyncSession, customer: Customer, **overrides: Any) -> Order:
    global _counter
    _counter += 1
    fields: dict[str, Any] = {
        "order_no": f"B2C-2608-9{_counter:05d}",
        "customer_id": customer.id,
        "product": "flight",
        "status": OrderStatus.BOOKED.value,
        "provider_order_number": f"7{_counter:05d}",
        "amount_total": "100.00",
        "currency": "UZS",
        **overrides,
    }
    order = Order(**fields)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def _events(session: AsyncSession, order: Order) -> list[dict[str, Any]]:
    rows = await session.scalars(repository.events_of(order.id))
    return [
        {
            "from": row.from_status,
            "to": row.to_status,
            "action": row.action,
            "actor_type": row.actor_type,
            "actor_label": row.actor_label,
            "reason": row.reason,
            "attempt": row.attempt,
        }
        for row in rows
    ]


async def test_a_move_writes_the_row_and_exactly_one_history_line(
    session: AsyncSession, customer: Customer
) -> None:
    order = await _order(session, customer)

    moved = await service.transition(
        session,
        order.id,
        to=OrderStatus.PAID,
        actor=Actor.system("payments.webhook"),
        action=EventAction.PAYMENT_SETTLED,
        reason="payme_perform",
        fields={"amount_paid": "100.00"},
        next_attempt_at=datetime.now(UTC),
    )

    assert moved.status == "paid"
    assert moved.paid_at is not None
    # ``fields`` is applied inside the same transaction as the status: a status
    # that is true beside a row that is not is the state nobody recovers from.
    assert str(moved.amount_paid) == "100.00"
    # The next step is scheduled here rather than sent — the poller is what
    # makes the guarantee, the direct send is only for latency (``O13``).
    assert moved.next_attempt_at is not None
    assert await _events(session, order) == [
        {
            "from": "booked",
            "to": "paid",
            "action": "payment.settled",
            "actor_type": "system",
            "actor_label": "payments.webhook",
            "reason": "payme_perform",
            "attempt": 0,
        }
    ]


async def test_a_move_that_is_not_in_the_table_is_a_conflict(
    session: AsyncSession, customer: Customer
) -> None:
    """A ``409``, never a silent no-op: it is nearly always a race or a client
    acting on a screen that has gone stale (API.md §3)."""
    order = await _order(session, customer)

    with pytest.raises(Conflict) as raised:
        await service.transition(
            session,
            order.id,
            to=OrderStatus.TICKETED,
            actor=Actor.system("test"),
            action=EventAction.TICKETING_SUCCEEDED,
        )

    assert raised.value.meta == {"from": "booked", "to": "ticketed"}
    await session.refresh(order)
    assert order.status == "booked"
    assert await _events(session, order) == []


async def test_an_order_that_does_not_exist_is_a_404(session: AsyncSession) -> None:
    with pytest.raises(NotFound):
        await service.transition(
            session,
            uuid.uuid4(),
            to=OrderStatus.PAID,
            actor=Actor.system("test"),
            action=EventAction.PAYMENT_SETTLED,
        )


async def test_nothing_pending_is_the_default(
    session: AsyncSession, customer: Customer
) -> None:
    """Most moves end with the order waiting on the outside world. A step that
    is really due has to say so, so that a forgotten argument leaves an order
    idle rather than spinning."""
    order = await _order(
        session, customer, next_attempt_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    moved = await service.transition(
        session,
        order.id,
        to=OrderStatus.CANCELLED,
        actor=Actor.customer(customer.id),
        action=EventAction.ORDER_CANCELLED,
    )

    assert moved.next_attempt_at is None


async def test_a_retry_counts_up_and_a_real_move_resets(
    session: AsyncSession, customer: Customer
) -> None:
    """``ticketing → ticketing`` is the retry loop, and it is the only move
    that keeps counting; arriving somewhere new starts a new step."""
    order = await _order(session, customer, status=OrderStatus.PAID.value)

    await service.transition(
        session,
        order.id,
        to=OrderStatus.TICKETING,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_STARTED,
    )
    assert order.attempts == 0

    for expected in (1, 2):
        retried = await service.transition(
            session,
            order.id,
            to=OrderStatus.TICKETING,
            actor=Actor.system("orders.ticket"),
            action=EventAction.TICKETING_RETRY,
            reason="upstream timeout",
        )
        assert retried.attempts == expected

    settled = await service.transition(
        session,
        order.id,
        to=OrderStatus.TICKETED,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_SUCCEEDED,
    )
    assert settled.attempts == 0
    assert [event["attempt"] for event in await _events(session, order)] == [0, 1, 2, 0]


async def test_a_timestamp_is_written_once(
    session: AsyncSession, customer: Customer
) -> None:
    """Re-entering a status must not rewrite the moment it was first reached —
    the reports read these columns, and ``ticketing`` is entered repeatedly."""
    order = await _order(session, customer, status=OrderStatus.PAID.value)
    await service.transition(
        session,
        order.id,
        to=OrderStatus.TICKETING,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_STARTED,
    )
    first = await service.transition(
        session,
        order.id,
        to=OrderStatus.TICKETED,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_SUCCEEDED,
    )
    stamped = first.ticketed_at

    await service.transition(
        session,
        order.id,
        to=OrderStatus.REFUNDING,
        actor=Actor.staff(uuid.uuid4(), label="ops@brand.uz"),
        action=EventAction.REFUND_STARTED,
    )
    again = await service.transition(
        session,
        order.id,
        to=OrderStatus.NEEDS_ATTENTION,
        actor=Actor.system("refunds.commit"),
        action=EventAction.REFUND_FAILED,
    )
    resolved = await service.transition(
        session,
        again.id,
        to=OrderStatus.TICKETED,
        actor=Actor.staff(uuid.uuid4(), label="ops@brand.uz"),
        action=EventAction.ATTENTION_RESOLVED,
        reason="issued by hand at the airline",
    )

    assert resolved.ticketed_at == stamped


async def test_the_history_carries_the_provider_answer(
    session: AsyncSession, customer: Customer
) -> None:
    """There is no snapshot table: the evidence for a step is attached to the
    step (``O9``)."""
    order = await _order(session, customer)
    answer = {"data": {"status": "CB", "order_number": 61453}}

    await service.transition(
        session,
        order.id,
        to=OrderStatus.CANCELLED,
        actor=Actor.customer(customer.id),
        action=EventAction.ORDER_CANCELLED,
        meta=answer,
    )

    stored = await session.scalar(
        text("SELECT meta FROM order_events WHERE order_id = :id"), {"id": order.id}
    )
    assert stored == answer


async def test_two_callers_racing_leave_one_winner(
    database: AsyncEngine, customer: Customer
) -> None:
    """The reason the row is locked. Two cancellations arriving together used
    to both pass the ownership check and both reach GTS; now the second reads
    the status the first left behind and is refused.

    Real connections, because the shared-transaction fixtures cannot show a
    lock: the test's own transaction never commits, so nothing it writes is
    visible to anyone else and two sessions on one connection never contend.
    Everything here is therefore committed for real and deleted at the end.
    ``orders.customer_id`` carries no foreign key, so the customer staying
    inside the test transaction does not matter (ARCHITECTURE.md §4).
    """
    factory = async_sessionmaker(bind=database, expire_on_commit=False)
    async with factory() as setup:
        order = await _order(setup, customer)
        order_id = order.id

    async def cancel() -> Conflict | None:
        async with factory() as own:
            try:
                await service.transition(
                    own,
                    order_id,
                    to=OrderStatus.CANCELLED,
                    actor=Actor.customer(customer.id),
                    action=EventAction.ORDER_CANCELLED,
                )
            except Conflict as refused:
                return refused
            return None

    try:
        outcomes = await asyncio.gather(cancel(), cancel())
        # Exactly one winner: the loser waited on the lock, then read the
        # status the winner had already written.
        assert sorted(outcome is None for outcome in outcomes) == [False, True]
        loser = next(outcome for outcome in outcomes if outcome is not None)
        assert loser.meta == {"from": "cancelled", "to": "cancelled"}

        async with factory() as check:
            events = await check.scalar(
                text("SELECT count(*) FROM order_events WHERE order_id = :id"),
                {"id": order_id},
            )
            assert events == 1
    finally:
        async with factory() as cleanup:
            await cleanup.execute(
                text("DELETE FROM orders WHERE id = :id"), {"id": order_id}
            )
            await cleanup.commit()
