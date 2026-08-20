"""Saved cards: save, list, reveal, forget (API.md §19).

A card is an **autofill record**. Saving one is local: Luhn and ``MMYY`` checks
in the schema, then a row — no provider, no OTP, no confirmation step
(PROJECT.md D7). What this module owns is the row and the ciphertext.

Two rules that shape the code below:

* **The number never lands in the clear.** It arrives as ``SecretStr``, is
  unwrapped once, sealed with ``core.crypto`` and stored; everything shown back
  (``masked_pan``, ``last4``, ``bin``, ``brand``) is derived locally from the
  digits before they are sealed. Nothing logs it and no response carries it
  (PROJECT.md §13).
* **Only ``reveal_card`` opens the ciphertext.** It exists for a checkout that
  fills the provider's card step server-side when the client sends a
  ``card_id``, and its result must never be logged.
"""

import uuid
from dataclasses import dataclass
from typing import Final

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import NotFound, ValidationFailed
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
from app.core.crypto import decrypt, encrypt
from app.modules.payments import repository
from app.modules.payments.models import CustomerCard
from app.modules.payments.schemas import CardCreateIn, CardOut

logger = structlog.get_logger(__name__)

_CARD_ORDERING: Final[OrderingMap] = {
    "created_at": CustomerCard.created_at,
    "last_used_at": CustomerCard.last_used_at,
}

#: BIN prefix → brand, applied locally — no provider is asked (API.md §19).
#: An unknown prefix is ``None``, and that is not an error.
_BRANDS: Final[tuple[tuple[str, str], ...]] = (
    ("8600", "uzcard"),
    ("9860", "humo"),
    ("4", "visa"),
    ("5", "mastercard"),
)


def _brand_for(number: str) -> str | None:
    for prefix, brand in _BRANDS:
        if number.startswith(prefix):
            return brand
    return None


# --- reading --------------------------------------------------------------------


async def list_cards(
    session: AsyncSession,
    customer_id: uuid.UUID,
    pagination: Pagination,
    query: ListQuery,
) -> Page[CardOut]:
    stmt = repository.owned_cards(customer_id)
    stmt = apply_search(stmt, query, CustomerCard.last4)
    stmt = apply_created_range(stmt, query, CustomerCard.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_CARD_ORDERING,
        default="-created_at",
        tiebreak=CustomerCard.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([CardOut.model_validate(row) for row in rows], pagination, total)


async def _require_card(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CustomerCard:
    """404 for a card that does not exist **and** for somebody else's.

    Two different situations, one answer on purpose — the rule API.md §19 states
    for passengers, and a card is the one row where enumeration would be worth
    the effort.
    """
    card = await repository.card_by_id(session, customer_id, card_id)
    if card is None:
        raise NotFound("Card not found")
    return card


async def get_card(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CardOut:
    return CardOut.model_validate(await _require_card(session, customer_id, card_id))


# --- adding -----------------------------------------------------------------------


async def add_card(
    session: AsyncSession, customer_id: uuid.UUID, data: CardCreateIn
) -> CardOut:
    """Seal the number and store the row. Usable immediately — no confirmation."""
    digits = data.number.get_secret_value()
    expire = data.expire.get_secret_value()

    pan, key_version = encrypt(digits)
    card = CustomerCard(
        customer_id=customer_id,
        pan=pan,
        key_version=key_version,
        masked_pan=f"{digits[:6]}{'*' * 6}{digits[-4:]}",
        last4=digits[-4:],
        bin=digits[:6],
        brand=_brand_for(digits),
        expiry_month=int(expire[:2]),
        expiry_year=2000 + int(expire[2:]),
    )

    session.add(card)
    try:
        await session.commit()
    except IntegrityError:
        # The identity index caught a card this customer already has.
        await session.rollback()
        raise ValidationFailed("This card is already saved", field="number") from None

    await session.refresh(card)
    # ``card_id`` only — not even ``last4`` belongs in a log line here.
    logger.info("card_saved", card_id=str(card.id))
    return CardOut.model_validate(card)


# --- revealing --------------------------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class CardCredentials:
    """A card number in flight, on its way out of the vault. Never logged.

    The hand-written ``__repr__`` is not decoration. A dataclass carrying a
    secret ends up inside a structlog ``exc_info``, an f-string or a failing
    assertion sooner or later, and the default one would print the number in
    all three.
    """

    #: Digits only.
    number: str
    #: ``MMYY``, the shape both provider APIs use.
    expire: str

    def __repr__(self) -> str:
        return f"CardCredentials(last4={self.number[-4:]!r})"


async def reveal_card(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CardCredentials:
    """The stored number in the clear — the only path that opens the ciphertext.

    One of the doors this module opens to the rest of the application
    (ARCHITECTURE.md §5): a checkout that pays with a ``card_id`` instead of a
    typed number opens the ciphertext here and hands the digits to a provider,
    and nowhere else. Nothing calls it today — the order system that did was
    removed — but the door is what the vault is for, so it stays.
    """
    card = await _require_card(session, customer_id, card_id)
    if card.pan is None:
        # Unreachable for a live row — the CHECK constraint pairs a null
        # ciphertext with a soft-deleted row, and ``owned_cards`` filters those.
        raise NotFound("Card not found")
    return CardCredentials(
        number=decrypt(card.pan, card.key_version or 0),
        expire=f"{card.expiry_month:02d}{card.expiry_year % 100:02d}",
    )


# --- removing ---------------------------------------------------------------------


def _forget(card: CustomerCard) -> None:
    """Drop the ciphertext and soft-delete the row. No commit."""
    card.pan = None
    card.key_version = None
    card.soft_delete()


async def delete_card(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> None:
    card = await _require_card(session, customer_id, card_id)
    _forget(card)
    await session.commit()
    logger.info("card_deleted", card_id=str(card.id))


async def forget_cards(session: AsyncSession, customer_id: uuid.UUID) -> None:
    """Every card of one account, on account deletion (PROJECT.md §13).

    One of the doors this module opens to the rest of the application; the
    caller is ``customers.service.delete_account``. It does **not** commit — the
    deletion is one transaction, and a card removed by a rollback that kept the
    account would be the worst of both.
    """
    for card in await repository.live_cards_for(session, customer_id):
        _forget(card)


__all__ = [
    "CardCredentials",
    "add_card",
    "delete_card",
    "forget_cards",
    "get_card",
    "list_cards",
    "reveal_card",
]
