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

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import live
from app.modules.orders.models import Order, OrderEvent


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


def events_of(order_id: uuid.UUID) -> Select[tuple[OrderEvent]]:
    """One order's history, oldest first — the way a timeline reads."""
    return (
        select(OrderEvent)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.created_at.asc(), OrderEvent.id.asc())
    )


__all__ = [
    "events_of",
    "lock_order",
    "order_by_id",
    "order_by_provider_number",
    "owned_orders",
]
