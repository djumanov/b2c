"""Saved cards: register, confirm, charge-ready, forget (API.md §19).

The lifecycle is a small state machine, and the interesting part is what it does
**not** own. The confirmation code is issued by the provider and validated by the
provider — we never hold a correct value, so there is no ``code_hash`` here and
no comparison. What this module owns is the row, the token, the attempt ceiling
and the cooldown.

Three rules that shape the code below:

* **The card number never lands.** It arrives as ``SecretStr``, is unwrapped
  once into ``CardCredentials``, and goes straight to the adapter. Nothing in
  between logs it, stores it or puts it in a ``repr`` (PROJECT.md §13).
* **One message for every way a code can fail.** Wrong, expired, out of
  attempts — telling them apart tells somebody guessing how far they got.
* **The provider is told before we are.** Removing a card calls the provider
  first and clears the row either way: a card the provider still holds but we
  have forgotten is a token nobody can ever revoke.
"""

import uuid
from datetime import timedelta
from typing import Final

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import Conflict, NotFound, RateLimited, ValidationFailed
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
from app.db.mixins import utcnow
from app.modules.integrations import service as integrations_service
from app.modules.payments import repository
from app.modules.payments.models import CustomerCard
from app.modules.payments.schemas import (
    CardCreateIn,
    CardOut,
    CardUpdateIn,
    CardVerifyIn,
)
from app.providers.payments.base import (
    CardCredentials,
    CardStatus,
    CardTokenProvider,
    PaymentProviderCode,
    RegisteredCard,
)

logger = structlog.get_logger(__name__)

#: How long the provider's code stays usable on our side. Shorter than the email
#: OTP's because the provider expires it too and the tighter of the two wins;
#: quoting a number we cannot honour would be worse than quoting none.
CARD_OTP_TTL: Final = timedelta(minutes=5)

#: Tighter than the email OTP's five. Each attempt is a call to the provider on
#: the installation's merchant credentials, and the provider has its own ceiling
#: that we must not be the reason for hitting.
CARD_OTP_MAX_ATTEMPTS: Final = 3

#: Floor for the resend cooldown. A provider that states its own wait (Payme
#: returns one) overrides this upward — never downward.
CARD_OTP_RESEND_COOLDOWN: Final = timedelta(seconds=60)

#: One message for wrong, expired and exhausted alike.
_CODE_REJECTED: Final = "This code is invalid or has expired"

_CARD_ORDERING: Final[OrderingMap] = {
    "created_at": CustomerCard.created_at,
    "last_used_at": CustomerCard.last_used_at,
    "label": CustomerCard.label,
}


# --- provider plumbing ----------------------------------------------------------


async def _card_provider(
    session: AsyncSession, code: PaymentProviderCode
) -> CardTokenProvider:
    """The adapter that can save cards for this provider, or ``404``.

    A provider without a card API is not an error, it is an absence — the same
    answer ``ProductAdapter`` gives for a step a vertical does not serve
    (ARCHITECTURE.md §6). A provider the owner has not switched on is the same
    absence from the customer's side, and for the same reason it must not be
    distinguishable: which providers an installation has configured is not the
    customer's business.
    """
    adapter = await integrations_service.payment_provider_adapter(session, code)
    if not isinstance(adapter, CardTokenProvider):
        raise NotFound("This payment method cannot save cards")
    return adapter


def _seal(token: str) -> tuple[str, int]:
    return encrypt(token)


def _open(card: CustomerCard) -> str:
    """The provider token in the clear. The only path that decrypts one."""
    if card.token is None:
        # Reachable only through a removed card, which every caller has already
        # refused. Belt to the CHECK constraint's braces.
        raise Conflict("This card has been removed")
    return decrypt(card.token, card.key_version or 0)


def _apply(card: CustomerCard, registered: RegisteredCard) -> None:
    """Copy what the provider says onto the row, token included."""
    card.token, card.key_version = _seal(registered.token)
    card.masked_pan = registered.masked_pan
    card.last4 = registered.last4
    card.bin = registered.bin
    card.brand = registered.brand
    card.expiry_month = registered.expiry_month
    card.expiry_year = registered.expiry_year


def _stamp_otp(card: CustomerCard, registered: RegisteredCard) -> None:
    now = utcnow()
    card.phone_masked = registered.otp_sent_to
    card.otp_sent_at = now
    card.otp_expires_at = now + CARD_OTP_TTL


def _cooldown(card: CustomerCard, registered_wait: int | None = None) -> timedelta:
    wait = CARD_OTP_RESEND_COOLDOWN
    if registered_wait:
        wait = max(wait, timedelta(seconds=registered_wait))
    return wait


# --- reading --------------------------------------------------------------------


async def list_cards(
    session: AsyncSession,
    customer_id: uuid.UUID,
    pagination: Pagination,
    query: ListQuery,
) -> Page[CardOut]:
    stmt = repository.owned_cards(customer_id)
    stmt = apply_search(stmt, query, CustomerCard.label, CustomerCard.last4)
    stmt = apply_created_range(stmt, query, CustomerCard.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_CARD_ORDERING,
        default="-created_at",
        tiebreak=CustomerCard.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([_out(row) for row in rows], pagination, total)


def _out(card: CustomerCard) -> CardOut:
    """Serialise, hiding the OTP fields once they stop meaning anything."""
    pending = card.status is CardStatus.PENDING_VERIFICATION
    return CardOut(
        id=card.id,
        provider=card.provider,
        status=card.status,
        masked_pan=card.masked_pan,
        last4=card.last4,
        brand=card.brand,
        expiry_month=card.expiry_month,
        expiry_year=card.expiry_year,
        label=card.label,
        is_default=card.is_default,
        otp_sent_to=card.phone_masked if pending else None,
        otp_expires_at=card.otp_expires_at if pending else None,
        last_used_at=card.last_used_at,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


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
    return _out(await _require_card(session, customer_id, card_id))


# --- adding -----------------------------------------------------------------------


async def add_card(
    session: AsyncSession, customer_id: uuid.UUID, data: CardCreateIn
) -> CardOut:
    """Register a card with the provider and store the token it hands back.

    The row is written **after** the provider call, unlike the transaction row
    in the checkout path: here a failed registration leaves nothing worth
    keeping, and a half-made card the customer can see but not use would be its
    own support question.
    """
    adapter = await _card_provider(session, data.provider)

    registered = await adapter.register_card(
        CardCredentials(
            number=data.number.get_secret_value(),
            expire=data.expire.get_secret_value(),
        )
    )

    card = CustomerCard(
        customer_id=customer_id,
        provider=data.provider,
        status=CardStatus.ACTIVE
        if registered.verified
        else CardStatus.PENDING_VERIFICATION,
        masked_pan=registered.masked_pan,
        last4=registered.last4,
        label=data.label,
    )
    _apply(card, registered)
    if registered.verified:
        await _promote_if_first(session, card, customer_id)
    else:
        _stamp_otp(card, registered)

    session.add(card)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # The identity index caught a card this customer already has. The token
        # just minted is now unreachable, so hand it back before giving up —
        # otherwise it hangs on the merchant account with nothing pointing at it.
        await _forget_at_provider(session, data.provider, registered.token)
        raise ValidationFailed("This card is already saved", field="number") from None

    await session.refresh(card)
    logger.info(
        "card_registered",
        card_id=str(card.id),
        provider=card.provider.value,
        verified=registered.verified,
    )
    return _out(card)


async def _promote_if_first(
    session: AsyncSession, card: CustomerCard, customer_id: uuid.UUID
) -> None:
    """The first usable card becomes the default, so checkout has one to offer."""
    if await repository.current_default(session, customer_id) is None:
        card.is_default = True


# --- confirming -------------------------------------------------------------------


async def verify_card(
    session: AsyncSession,
    customer_id: uuid.UUID,
    card_id: uuid.UUID,
    data: CardVerifyIn,
) -> CardOut:
    card = await _require_card(session, customer_id, card_id)
    if card.status is CardStatus.ACTIVE:
        raise Conflict("This card is already confirmed")
    if card.status is not CardStatus.PENDING_VERIFICATION:
        raise NotFound("Card not found")

    if card.otp_expires_at is not None and card.otp_expires_at <= utcnow():
        raise ValidationFailed(_CODE_REJECTED, field="code")

    adapter = await _card_provider(session, card.provider)
    try:
        registered = await adapter.verify_card(token=_open(card), code=data.code)
    except ValidationFailed:
        await _count_failure(session, card)
        raise ValidationFailed(_CODE_REJECTED, field="code") from None

    if not registered.verified:
        await _count_failure(session, card)
        raise ValidationFailed(_CODE_REJECTED, field="code")

    _apply(card, registered)
    card.status = CardStatus.ACTIVE
    card.phone_masked = None
    card.otp_sent_at = None
    card.otp_expires_at = None
    card.verify_attempts = 0
    await _promote_if_first(session, card, customer_id)
    await session.commit()
    await session.refresh(card)
    logger.info("card_verified", card_id=str(card.id))
    return _out(card)


async def _count_failure(session: AsyncSession, card: CustomerCard) -> None:
    """Charge one attempt, and burn the card once they run out.

    Committed on its own so the count survives the exception that follows it —
    a counter rolled back with the failed request would not be a counter.
    """
    card.verify_attempts += 1
    if card.verify_attempts >= CARD_OTP_MAX_ATTEMPTS:
        await _forget(session, card)
        logger.info("card_verification_exhausted", card_id=str(card.id))
    await session.commit()


async def resend_code(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CardOut:
    card = await _require_card(session, customer_id, card_id)
    if card.status is not CardStatus.PENDING_VERIFICATION:
        raise Conflict("This card does not need a code")

    if card.otp_sent_at is not None:
        elapsed = utcnow() - card.otp_sent_at
        wait = _cooldown(card)
        if elapsed < wait:
            raise RateLimited(
                "Please wait before asking for another code",
                retry_after=max(1, int((wait - elapsed).total_seconds())),
            )

    adapter = await _card_provider(session, card.provider)
    registered = await adapter.request_card_code(token=_open(card))
    _stamp_otp(card, registered)
    await session.commit()
    await session.refresh(card)
    return _out(card)


# --- editing ----------------------------------------------------------------------


async def update_card(
    session: AsyncSession,
    customer_id: uuid.UUID,
    card_id: uuid.UUID,
    data: CardUpdateIn,
) -> CardOut:
    card = await _require_card(session, customer_id, card_id)
    card.label = data.label
    await session.commit()
    await session.refresh(card)
    return _out(card)


async def set_default(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> CardOut:
    """Make one card the default, clearing whichever held it.

    Both writes happen in one transaction because the partial unique index
    refuses two defaults — clearing after setting would be refused, and the
    reverse leaves a customer with none if the second write fails.
    """
    card = await _require_card(session, customer_id, card_id)
    if card.status is not CardStatus.ACTIVE:
        raise Conflict("Confirm the card before making it the default")

    previous = await repository.current_default(session, customer_id)
    if previous is not None and previous.id != card.id:
        previous.is_default = False
        await session.flush()
    card.is_default = True
    await session.commit()
    await session.refresh(card)
    return _out(card)


# --- removing ---------------------------------------------------------------------


async def _forget_at_provider(
    session: AsyncSession, provider: PaymentProviderCode, token: str
) -> None:
    """Best effort. A provider that will not let go must not block the customer."""
    try:
        adapter = await _card_provider(session, provider)
        await adapter.remove_card(token=token)
    except Exception as exc:  # noqa: BLE001 - the customer's row goes either way
        logger.warning(
            "card_provider_removal_failed",
            provider=provider.value,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )


async def _forget(session: AsyncSession, card: CustomerCard) -> None:
    """Drop the token, mark the row removed, tell the provider. No commit."""
    if card.token is not None:
        await _forget_at_provider(session, card.provider, _open(card))
    card.token = None
    card.key_version = None
    card.status = CardStatus.REMOVED
    card.is_default = False
    card.otp_sent_at = None
    card.otp_expires_at = None
    card.soft_delete()


async def delete_card(
    session: AsyncSession, customer_id: uuid.UUID, card_id: uuid.UUID
) -> None:
    card = await _require_card(session, customer_id, card_id)
    await _forget(session, card)
    await session.commit()
    logger.info("card_deleted", card_id=str(card.id))


async def forget_cards(session: AsyncSession, customer_id: uuid.UUID) -> None:
    """Every card of one account, on account deletion (PROJECT.md §13).

    One of the two doors this module opens to the rest of the application; the
    caller is ``customers.service.delete_account``. It does **not** commit — the
    deletion is one transaction, and a card removed by a rollback that kept the
    account would be the worst of both.
    """
    for card in await repository.live_cards_for(session, customer_id):
        await _forget(session, card)


__all__ = [
    "CARD_OTP_MAX_ATTEMPTS",
    "CARD_OTP_RESEND_COOLDOWN",
    "CARD_OTP_TTL",
    "add_card",
    "delete_card",
    "forget_cards",
    "get_card",
    "list_cards",
    "resend_code",
    "set_default",
    "update_card",
    "verify_card",
]
