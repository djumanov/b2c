"""Queries over ``orders``. Only ``service`` calls into here.

Every lookup is scoped by owner, for the reason ``payments.repository`` states:
an order is reachable only through the account that made it, so "no such row"
and "not yours" must be the same answer, and a helper that *could* be called
without the owner is the one somebody eventually calls without it.
"""

import uuid

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import live
from app.modules.orders.models import Order


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


async def order_by_gts_id(
    session: AsyncSession, customer_id: uuid.UUID, gts_order_id: str
) -> Order | None:
    """The ownership check behind ``cancel/`` (API.md §20).

    Scoped by customer like everything else here, so a booking someone else
    made is indistinguishable from one that does not exist.
    """
    row: Order | None = await session.scalar(
        owned_orders(customer_id).where(Order.gts_order_id == gts_order_id)
    )
    return row


__all__ = [
    "order_by_gts_id",
    "order_by_id",
    "owned_orders",
]
