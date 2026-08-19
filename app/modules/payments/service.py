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
from dataclasses import dataclass
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
from app.modules.payments.schemas import CardCreateIn, CardOut, CardPaymentIn
from app.providers.payments.base import (
    CallbackResult,
    CardCredentials,
    ChargeResult,
    PaymentProvider,
    PaymentProviderCode,
    ReferenceSink,
    RefundResult,
    RegisteredCard,
    VerifiedCard,
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


# --- the card flow (API.md §22) ---------------------------------------------------


@dataclass(frozen=True, slots=True, repr=False)
class CardToken:
    """A provider's card handle, sealed. The plaintext never leaves this module.

    The attempt row that stores it belongs to ``orders``, so the ciphertext and
    its key version travel together in one value rather than as two loose
    columns the caller has to remember to pair. ``orders`` puts the pair in the
    row and hands it back; only the functions below open it.

    ``repr`` is hand-written for the same reason ``CardCredentials``'s is: even
    a ciphertext in a log line is a credential in a log line.
    """

    ciphertext: str
    key_version: int

    def __repr__(self) -> str:
        return f"CardToken(key_version={self.key_version})"


@dataclass(frozen=True, slots=True)
class CardRegistration:
    """What the attempt row needs after the provider has taken the card.

    Everything here is safe to store and safe to show. The number is not here —
    it was opened, sent and dropped inside ``register_card``.
    """

    token: CardToken
    masked_pan: str
    last4: str
    brand: str | None
    #: Masked by the provider; the customer needs to know which phone to look at.
    otp_sent_to: str | None
    #: The provider's own idea of how long before another code may be asked for.
    otp_wait_seconds: int | None


def _seal(value: str) -> CardToken:
    ciphertext, key_version = encrypt(value)
    return CardToken(ciphertext=ciphertext, key_version=key_version)


def _open(token: CardToken) -> str:
    return decrypt(token.ciphertext, token.key_version)


async def register_card(
    session: AsyncSession,
    code: PaymentProviderCode,
    *,
    customer_id: uuid.UUID,
    data: CardPaymentIn,
) -> CardRegistration:
    """Give the provider a card and have it text the customer a code.

    The two ways a card arrives — typed into the request, or named by
    ``card_id`` — meet here and are indistinguishable afterwards. That is the
    whole reason this lives in ``payments``: it is the only module allowed to
    hold a number in the clear, and by keeping both branches inside one function
    nothing downstream ever learns which one it was.

    The masked forms are derived from the digits **locally** rather than taken
    from the provider's answer, so a saved card and a typed one produce the same
    strings and a receipt does not change shape with the provider.
    """
    if data.card_id is not None:
        card = await reveal_card(session, customer_id, data.card_id)
    else:
        # ``_exactly_one_form`` has already refused every other combination.
        assert data.number is not None and data.expire is not None
        card = CardCredentials(
            number=data.number.get_secret_value(),
            expire=data.expire.get_secret_value(),
        )

    adapter = await _adapter(session, code)
    registered = await adapter.register_card(card)

    digits = card.number
    return CardRegistration(
        token=_seal(registered.token),
        masked_pan=f"{digits[:6]}{'*' * 6}{digits[-4:]}",
        last4=digits[-4:],
        brand=_brand_for(digits),
        otp_sent_to=registered.otp_sent_to,
        otp_wait_seconds=registered.otp_wait_seconds,
    )


async def resend_card_code(
    session: AsyncSession, code: PaymentProviderCode, *, token: CardToken
) -> RegisteredCard:
    """Ask the provider to text the code again."""
    adapter = await _adapter(session, code)
    return await adapter.request_card_code(token=_open(token))


async def verify_card(
    session: AsyncSession, code: PaymentProviderCode, *, token: CardToken, otp_code: str
) -> VerifiedCard:
    """Hand the provider the code the customer read out.

    **We do not judge the code.** It was issued by the provider and it is the
    provider that rules on it; a wrong one comes back as ``PaymentFailed`` from
    the adapter. Our own counter exists to stop a customer spending the
    installation's merchant account on guesses, not to check arithmetic.
    """
    adapter = await _adapter(session, code)
    return await adapter.verify_card(token=_open(token), code=otp_code)


async def charge_card(
    session: AsyncSession,
    code: PaymentProviderCode,
    *,
    token: CardToken,
    reference: str,
    amount: Decimal,
    currency: str,
    on_reference: ReferenceSink | None = None,
) -> ChargeResult:
    """Take the money for one order.

    ``reference`` is the **order's** id, for the reason ``charge`` gave: both
    Payme's ``account`` and Click's ``merchant_trans_id`` are the merchant's own
    handle on the purchase, and a settled charge pointing at anything else
    points at nothing.
    """
    adapter = await _adapter(session, code)
    return await adapter.charge_card(
        token=_open(token),
        order_id=reference,
        amount=amount,
        currency=currency,
        on_reference=on_reference,
    )


async def charge_status(
    session: AsyncSession, code: PaymentProviderCode, *, transaction_ref: str
) -> ChargeResult:
    """Ask the provider what became of one charge — the reconciliation path.

    ``transaction_ref`` is the **provider's** id, not ours: it is their record
    being asked after, and ours would mean nothing to them.
    """
    adapter = await _adapter(session, code)
    return await adapter.status(transaction_ref=transaction_ref)


async def forget_card(
    session: AsyncSession, code: PaymentProviderCode, *, token: CardToken
) -> None:
    """Release a token at the provider. **Never raises.**

    Cleanup, and it runs on paths that are already going wrong: a refused card,
    an exhausted code, a cancelled order. A failure here means one dead token
    left on a merchant account, which is worth a log line and is not worth
    holding up the thing being cleaned up after.
    """
    try:
        adapter = await _adapter(session, code)
        await adapter.remove_card(token=_open(token))
    except Exception:  # noqa: BLE001 - deliberate: see the docstring
        logger.warning("card_token_not_released", provider=code.value, exc_info=True)


__all__ = [
    "CardRegistration",
    "CardToken",
    "add_card",
    "callback",
    "charge_status",
    "charge_card",
    "delete_card",
    "forget_card",
    "forget_cards",
    "get_card",
    "list_cards",
    "refund",
    "register_card",
    "resend_card_code",
    "reveal_card",
    "verify_card",
]
