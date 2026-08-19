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
* **Only ``reveal_card`` opens the ciphertext.** It exists for the checkout —
  the server fills the provider's card step itself when the client sends a
  ``card_id`` (API.md §22) — and its result must never be logged.
"""

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Final

import structlog
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
from app.core.crypto import decrypt, encrypt
from app.modules.integrations import service as integrations_service
from app.modules.payments import repository
from app.modules.payments.models import CustomerCard
from app.modules.payments.schemas import CardCreateIn, CardOut
from app.providers.payments.base import (
    CallbackResult,
    CardCredentials,
    PaymentProvider,
    PaymentProviderCode,
    RefundResult,
)

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


async def reveal_card(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CardCredentials:
    """The stored number in the clear — the only path that opens the ciphertext.

    One of the doors this module opens to the rest of the application
    (ARCHITECTURE.md §5). The checkout calls it when the client pays with a
    ``card_id`` instead of typing the number (API.md §22) and hands the result
    straight to ``register_card`` — the same call a freshly typed number makes,
    so nothing downstream can tell the two apart. The result goes to a provider
    adapter and nowhere else.
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


# --- the provider boundary (API.md §22, §40) -------------------------------------


async def _adapter(session: AsyncSession, code: PaymentProviderCode) -> PaymentProvider:
    """The configured adapter, or a ``502`` naming nothing.

    ``payment_provider_adapter`` answers ``None`` for three different reasons —
    no adapter written, provider switched off, no credentials entered — and
    deliberately does not say which. All three mean the installation cannot
    charge through it, and which one it is says something about the
    installation a customer has no business learning.
    """
    adapter = await integrations_service.payment_provider_adapter(session, code)
    if adapter is None:
        raise UpstreamError("This payment method is not available")
    return adapter


async def charge(
    session: AsyncSession,
    code: PaymentProviderCode,
    *,
    reference: str,
    amount: Decimal,
    currency: str,
    return_url: str,
) -> str:
    """Start a hosted charge and return where to send the customer.

    ``reference`` is what the provider will quote back in its callbacks, and it
    is the **order's** id: both Payme's ``account`` and Click's
    ``merchant_trans_id`` are the merchant's own order handle, and giving them
    anything else would leave a settled charge pointing at nothing.
    """
    adapter = await _adapter(session, code)
    return await adapter.create_payment(
        order_id=reference, amount=amount, currency=currency, return_url=return_url
    )


async def refund(
    session: AsyncSession,
    code: PaymentProviderCode,
    *,
    transaction_ref: str,
    amount: Decimal | None = None,
) -> RefundResult:
    """Send money back through the provider that took it.

    ``transaction_ref`` is the **provider's** id for the charge, not ours: a
    refund is an operation on their receipt, and ours would mean nothing to
    them. ``amount`` omitted means the whole charge.
    """
    adapter = await _adapter(session, code)
    return await adapter.refund(transaction_ref=transaction_ref, amount=amount)


async def callback(
    session: AsyncSession,
    code: PaymentProviderCode,
    *,
    headers: Mapping[str, str],
    body: bytes,
) -> CallbackResult:
    """Authenticate a provider callback and let it speak.

    The signature is checked **before** anything else runs, and a failure ends
    here: whatever the answer looks like, nothing changed (API.md §40). The
    shape of that answer belongs to the provider — a plain ``401`` for most,
    ``200`` with a JSON-RPC error for Payme, which reads ``401`` as a reason to
    retry blindly.

    The raw bytes go to the adapter untouched. Click signs the form body it
    actually sent, and re-serialising a parsed dict would not reproduce it.
    """
    adapter = await _adapter(session, code)
    if not adapter.verify_signature(headers, body):
        logger.warning("payment_callback_signature_rejected", provider=code.value)
        return adapter.signature_rejected()
    return await adapter.handle_callback(headers, body)


__all__ = [
    "add_card",
    "callback",
    "charge",
    "refund",
    "delete_card",
    "forget_cards",
    "get_card",
    "list_cards",
    "reveal_card",
]
