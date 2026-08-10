"""Queries over the payment tables. Only ``service`` calls into here."""

import uuid
from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import live
from app.modules.payments.models import CustomerCard
from app.providers.payments.base import CardStatus


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


async def current_default(
    session: AsyncSession, customer_id: uuid.UUID
) -> CustomerCard | None:
    """The card marked default, if there is one.

    Read before setting a new one so the old flag is cleared in the same
    transaction — the partial unique index refuses two, and a failed insert is
    a worse answer than a correct swap.
    """
    row: CustomerCard | None = await session.scalar(
        owned_cards(customer_id).where(CustomerCard.is_default)
    )
    return row


async def active_cards_for(
    session: AsyncSession, customer_id: uuid.UUID
) -> Sequence[CustomerCard]:
    """Cards that can actually be charged."""
    rows = await session.scalars(
        owned_cards(customer_id).where(CustomerCard.status == CardStatus.ACTIVE)
    )
    return rows.all()


__all__ = [
    "active_cards_for",
    "card_by_id",
    "current_default",
    "live_cards_for",
    "owned_cards",
]
