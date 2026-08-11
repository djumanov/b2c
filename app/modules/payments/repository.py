"""Queries over the payment tables. Only ``service`` calls into here."""

import uuid
from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import live
from app.modules.payments.models import CustomerCard


def owned_cards(customer_id: uuid.UUID) -> Select[tuple[CustomerCard]]:
    """Every live card of one customer — the base of every query here.

    There is no lookup that is not scoped by owner, for the reason
    ``customers.repository.owned_passengers`` states: a card is reachable only
    through the account that saved it, so "no such row" and "not yours" must be
    the same answer, and a helper that *could* be called without the owner is
    the one somebody eventually calls without it.
    """
    return live(CustomerCard).where(CustomerCard.customer_id == customer_id)


async def card_by_id(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CustomerCard | None:
    row: CustomerCard | None = await session.scalar(
        owned_cards(customer_id).where(CustomerCard.id == card_id)
    )
    return row


async def live_cards_for(
    session: AsyncSession, customer_id: uuid.UUID
) -> Sequence[CustomerCard]:
    """Used when an account is deleted and its saved cards go with it."""
    rows = await session.scalars(owned_cards(customer_id))
    return rows.all()


__all__ = [
    "card_by_id",
    "live_cards_for",
    "owned_cards",
]
