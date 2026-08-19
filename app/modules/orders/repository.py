"""Queries over ``orders`` and ``order_events``. Only ``service`` calls in here.

Every customer-facing lookup is scoped by owner, for the reason
``payments.repository`` states: an order is reachable only through the account
that made it, so "no such row" and "not yours" must be the same answer, and a
helper that *could* be called without the owner is the one somebody eventually
calls without it.

``lock_order`` is the deliberate exception. The state machine and the
background steps run without a customer in hand — a poller has no session — and
by the time either of them locks a row, ownership has already been settled by
whoever handed over the id.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Final

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import live
from app.modules.orders.models import Order, OrderEvent, OrderPayment, OrderRefund
from app.modules.orders.states import OrderStatus, RefundState
from app.providers.payments.base import TransactionStatus

#: The states a refund is still running in — the unique index uses the same set.
_OPEN_REFUNDS = (
    RefundState.REQUESTED.value,
    RefundState.APPROVED.value,
    RefundState.PROCESSING.value,
)


def owned_orders(customer_id: uuid.UUID) -> Select[tuple[Order]]:
    """Every live order of one customer — the base of every query here."""
    return live(Order).where(Order.customer_id == customer_id)


async def order_by_id(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> Order | None:
    row: Order | None = await session.scalar(
        owned_orders(customer_id).where(Order.id == order_id)
    )
    return row


async def order_by_provider_number(
    session: AsyncSession, customer_id: uuid.UUID, provider_order_number: str
) -> Order | None:
    """The ownership check behind cancelling (API.md §20).

    Keyed on GTS's ``order_number``, because that is what its ``cancel`` body
    names the booking by. Scoped by customer like everything else here, so a
    booking someone else made is indistinguishable from one that does not
    exist.
    """
    row: Order | None = await session.scalar(
        owned_orders(customer_id).where(
            Order.provider_order_number == provider_order_number
        )
    )
    return row


async def order_by_idempotency_key(
    session: AsyncSession, customer_id: uuid.UUID, key: str
) -> Order | None:
    """The order a previous attempt at this key created, if there was one.

    Scoped by customer because the unique index is: keys are chosen by clients,
    and reading one back without the owner would hand a booking to whoever
    happened to pick the same string.

    Not filtered by ``deleted_at`` — the index is not either, and an order is
    never soft-deleted anyway (``models.py``).
    """
    row: Order | None = await session.scalar(
        select(Order).where(
            Order.customer_id == customer_id, Order.idempotency_key == key
        )
    )
    return row


async def expiring_orders(
    session: AsyncSession, *, deadline: datetime, fallback_before: datetime, limit: int
) -> Sequence[Order]:
    """Holds whose payment window has closed, locked for one sweep.

    Two conditions because a provider may not have named a deadline. When it
    did, the window closes a margin before it, so there is still time to release
    the seat rather than letting the hold lapse untidily. When it did not, the
    bound is ours: an order that has been unpaid since before ``fallback_before``
    has waited long enough.

    ``SKIP LOCKED`` so several workers may sweep at once without both taking the
    same order.
    """
    rows = await session.scalars(
        select(Order)
        .where(
            Order.status == OrderStatus.BOOKED.value,
            or_(
                Order.ticket_time_limit_at <= deadline,
                and_(
                    Order.ticket_time_limit_at.is_(None),
                    Order.created_at <= fallback_before,
                ),
            ),
        )
        .order_by(Order.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return rows.all()


async def lock_order(session: AsyncSession, order_id: uuid.UUID) -> Order | None:
    """The row, held under ``SELECT … FOR UPDATE`` until the transaction ends.

    This is what makes a transition atomic against a concurrent one: two
    cancellations arriving together serialise here, and the second finds the
    status the first left behind rather than the one it started from.
    """
    row: Order | None = await session.scalar(
        select(Order).where(Order.id == order_id).with_for_update()
    )
    return row


async def attempt_by_id(
    session: AsyncSession, customer_id: uuid.UUID, attempt_id: uuid.UUID
) -> OrderPayment | None:
    """One payment attempt of one customer, or nothing.

    Scoped through the order it belongs to rather than by a copy of the owner:
    an attempt is only ever reachable via its order, and duplicating
    ``customer_id`` here would be a second answer to the same question.
    """
    row: OrderPayment | None = await session.scalar(
        select(OrderPayment)
        .join(Order, Order.id == OrderPayment.order_id)
        .where(OrderPayment.id == attempt_id, Order.customer_id == customer_id)
    )
    return row


async def attempt_by_provider_ref(
    session: AsyncSession, provider: str, provider_ref: str
) -> OrderPayment | None:
    """The attempt a callback is talking about.

    Not owner-scoped, and cannot be: a provider calling back has no session and
    knows nothing about our customers. The unique index on the same two columns
    is what keeps this from ever matching more than one row.
    """
    row: OrderPayment | None = await session.scalar(
        select(OrderPayment).where(
            OrderPayment.provider == provider,
            OrderPayment.provider_ref == provider_ref,
        )
    )
    return row


#: The statuses that make an attempt "open" — the same list the partial unique
#: index uses, kept in one place so the two cannot drift apart.
OPEN_ATTEMPT_STATUSES: Final = (
    TransactionStatus.AWAITING_CARD.value,
    TransactionStatus.AWAITING_OTP.value,
    TransactionStatus.PENDING.value,
)


async def open_attempt(
    session: AsyncSession, order_id: uuid.UUID, *, lock: bool = False
) -> OrderPayment | None:
    """The attempt this order is in the middle of, if any.

    At most one can exist — ``uq_order_payments_open`` says so — which is what
    lets a customer who wandered off mid-checkout come back to the attempt they
    left instead of opening a second one against the same order.
    """
    stmt = select(OrderPayment).where(
        OrderPayment.order_id == order_id,
        OrderPayment.status.in_(OPEN_ATTEMPT_STATUSES),
    )
    if lock:
        stmt = stmt.with_for_update()
    row: OrderPayment | None = await session.scalar(stmt)
    return row


async def stale_attempts(
    session: AsyncSession, *, before: datetime, limit: int
) -> Sequence[OrderPayment]:
    """Attempts still waiting for a card or a code long after anyone would.

    Measured from ``updated_at`` rather than ``created_at``: a customer who asks
    for a second code is still at the checkout, and closing that attempt under
    them would be the sweep picking a fight with a live customer.

    ``pending`` is **not** here. A charge whose answer is unknown is not stale,
    it is unresolved, and it belongs to ``reconcilable_attempts`` below.
    """
    rows = await session.scalars(
        select(OrderPayment)
        .where(
            OrderPayment.status.in_(
                (
                    TransactionStatus.AWAITING_CARD.value,
                    TransactionStatus.AWAITING_OTP.value,
                )
            ),
            OrderPayment.updated_at < before,
        )
        .order_by(OrderPayment.updated_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return rows.all()


async def reconcilable_attempts(
    session: AsyncSession, *, before: datetime, limit: int
) -> Sequence[OrderPayment]:
    """Charges that went out and never answered.

    The one state worth asking a provider about: the money may have moved, and
    nothing on our side knows. ``provider_ref IS NOT NULL`` because a charge we
    never got a name for cannot be asked after — ``on_reference`` is what makes
    that case vanishingly rare, and what remains of it is a person's job.
    """
    rows = await session.scalars(
        select(OrderPayment)
        .where(
            OrderPayment.status == TransactionStatus.PENDING.value,
            OrderPayment.provider_ref.is_not(None),
            OrderPayment.updated_at < before,
        )
        .order_by(OrderPayment.updated_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return rows.all()


async def unreferenced_attempt(
    session: AsyncSession, order_id: uuid.UUID, provider: str
) -> OrderPayment | None:
    """The attempt still waiting to be named by its provider.

    A hosted redirect gives us a URL and nothing else; the provider's own
    reference arrives with its first callback. So settlement looks for the
    reference first and falls back to the one open attempt at that provider —
    newest, because a customer who started twice is finishing the later one.
    """
    row: OrderPayment | None = await session.scalar(
        select(OrderPayment)
        .where(
            OrderPayment.order_id == order_id,
            OrderPayment.provider == provider,
            OrderPayment.provider_ref.is_(None),
            OrderPayment.status == TransactionStatus.PENDING.value,
        )
        .order_by(OrderPayment.created_at.desc())
        .limit(1)
    )
    return row


async def lock_attempt(
    session: AsyncSession, attempt_id: uuid.UUID
) -> OrderPayment | None:
    """The attempt, held until the transaction ends — settlement serialises."""
    row: OrderPayment | None = await session.scalar(
        select(OrderPayment).where(OrderPayment.id == attempt_id).with_for_update()
    )
    return row


def attempts_of(order_id: uuid.UUID) -> Select[tuple[OrderPayment]]:
    """One order's attempts, newest last."""
    return (
        select(OrderPayment)
        .where(OrderPayment.order_id == order_id)
        .order_by(OrderPayment.created_at.asc(), OrderPayment.id.asc())
    )


async def paid_attempt(
    session: AsyncSession, order_id: uuid.UUID
) -> OrderPayment | None:
    """The attempt that actually took the money — the one to send it back to."""
    row: OrderPayment | None = await session.scalar(
        select(OrderPayment)
        .where(
            OrderPayment.order_id == order_id,
            OrderPayment.status == TransactionStatus.PAID.value,
        )
        .order_by(OrderPayment.paid_at.desc())
        .limit(1)
    )
    return row


async def open_refund(session: AsyncSession, order_id: uuid.UUID) -> OrderRefund | None:
    """The refund still running on this order, locked.

    At most one can exist — the partial unique index says so — which is what
    lets a retry pick up where it left off rather than opening a second one.
    """
    row: OrderRefund | None = await session.scalar(
        select(OrderRefund)
        .where(
            OrderRefund.order_id == order_id,
            OrderRefund.status.in_(_OPEN_REFUNDS),
        )
        .with_for_update()
    )
    return row


def events_of(order_id: uuid.UUID) -> Select[tuple[OrderEvent]]:
    """One order's history, oldest first — the way a timeline reads."""
    return (
        select(OrderEvent)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.created_at.asc(), OrderEvent.id.asc())
    )


__all__ = [
    "attempt_by_id",
    "attempt_by_provider_ref",
    "attempts_of",
    "events_of",
    "expiring_orders",
    "lock_attempt",
    "unreferenced_attempt",
    "lock_order",
    "order_by_idempotency_key",
    "open_refund",
    "order_by_id",
    "order_by_provider_number",
    "owned_orders",
    "paid_attempt",
]
