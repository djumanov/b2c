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

The module also answers the one question the charge step asks of settings:
**which provider charges** (``payment_provider``). The orders module owns the
charge itself — the attempt row, the lock, the lifecycle — and comes here only
for the card and the provider, so the card number crosses exactly one module
boundary, on its way to the provider and nowhere else.
"""

import uuid
from typing import Final

import structlog
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import NotFound, UpstreamError, ValidationFailed
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.db.mixins import utcnow
from app.modules.integrations import service as integrations_service
from app.modules.payments import repository
from app.modules.payments.models import CustomerCard
from app.modules.payments.schemas import CardCreateIn, CardIn, CardOut
from app.providers import payments as payment_providers
from app.providers.payments.base import CardDetails, PaymentProvider
from app.providers.payments.sandbox import SandboxProvider

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


def _identity(digits: str, expire: str) -> tuple[str, int, int]:
    """What the identity index is built on — ``add_card`` spells it the same."""
    return (
        f"{digits[:6]}{'*' * 6}{digits[-4:]}",
        int(expire[:2]),
        2000 + int(expire[2:]),
    )


async def remember_card(
    session: AsyncSession, customer_id: uuid.UUID, card: CardDetails
) -> uuid.UUID:
    """Save the card a payment was just started with — ``save: true`` on
    ``payment/``.

    Called after the provider accepted the card, so a number Payme refused is
    never kept. A card the customer already has is not an error here, the
    way it is on ``POST /cards/``: the payment asked for it to be saved and
    it is — the existing row's id comes back, so the attempt can point at it
    and ``last_used_at`` gets stamped when the charge lands. Looked up
    first rather than caught as a duplicate: ``add_card``'s rollback would
    expire every row the caller still holds mid-request.
    """
    masked_pan, month, year = _identity(card.number, card.expire)
    existing = await repository.card_by_identity(
        session,
        customer_id,
        masked_pan=masked_pan,
        expiry_month=month,
        expiry_year=year,
    )
    if existing is not None:
        return existing.id
    saved = await add_card(
        session,
        customer_id,
        CardCreateIn(number=SecretStr(card.number), expire=SecretStr(card.expire)),
    )
    return saved.id


# --- revealing --------------------------------------------------------------------


async def reveal_card(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CardDetails:
    """The stored number in the clear — the only path that opens the ciphertext.

    One of the doors this module opens to the rest of the application
    (ARCHITECTURE.md §5): a checkout that pays with a ``card_id`` instead of a
    typed number opens the ciphertext here and hands the digits to a provider,
    and nowhere else.
    """
    card = await _require_card(session, customer_id, card_id)
    if card.pan is None:
        # Unreachable for a live row — the CHECK constraint pairs a null
        # ciphertext with a soft-deleted row, and ``owned_cards`` filters those.
        raise NotFound("Card not found")
    return CardDetails(
        number=decrypt(card.pan, card.key_version or 0),
        expire=f"{card.expiry_month:02d}{card.expiry_year % 100:02d}",
    )


# --- charging: the card and the provider ------------------------------------------


async def card_for_charge(
    session: AsyncSession,
    customer_id: uuid.UUID,
    *,
    card_id: uuid.UUID | None,
    card: CardIn | None,
) -> CardDetails:
    """The card a payment will be made with — saved (``card_id``) or typed.

    Exactly one of the two; the request schema already insists, this is the
    last line of defence. A typed card is **not** saved here: saving is the
    customer's explicit act on ``/profile/cards/``, never a side effect of
    paying.
    """
    if card_id is not None:
        return await reveal_card(session, customer_id, card_id)
    if card is not None:
        return CardDetails(
            number=card.number.get_secret_value(),
            expire=card.expire.get_secret_value(),
        )
    raise ValidationFailed("Send a card_id or a card", field="card")


async def mark_card_used(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> None:
    """Stamp ``last_used_at`` on a successful charge. No commit — the charge's."""
    card = await repository.card_by_id(session, customer_id, card_id)
    if card is not None:
        card.last_used_at = utcnow()


async def payment_provider(session: AsyncSession) -> PaymentProvider:
    """The provider that charges on this installation.

    In order: a test's pinned provider; the provider the panel enabled, if
    this release ships its adapter; the sandbox, when ``DEBUG`` is on and no
    real provider is enabled — never otherwise, so a staging database copied
    to production cannot carry a fake provider with it. Anything else is a
    ``502``: the installation cannot take money yet.

    **Call it before taking a row lock.** Reading the panel's rows commits
    the session when one is open (``integrations.service``), and a commit in
    the middle of a locked transaction quietly drops the lock.
    """
    pinned = payment_providers.get_override()
    if pinned is not None:
        return pinned
    configured = await integrations_service.active_payment_provider(session)
    if configured is not None:
        factory = payment_providers.ADAPTERS.get(configured.code)
        if factory is None:
            raise UpstreamError(
                f"payment provider {configured.code.value} is not available "
                "in this release"
            )
        return factory(configured.credentials)
    if settings.debug:
        return SandboxProvider()
    raise UpstreamError("no payment provider is configured on this installation")


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
    "add_card",
    "card_for_charge",
    "delete_card",
    "forget_cards",
    "get_card",
    "list_cards",
    "mark_card_used",
    "payment_provider",
    "remember_card",
    "reveal_card",
]
