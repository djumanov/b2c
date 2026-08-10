"""The payment ports — Payme and Click in the first release (D7).

Both providers offer two ways in, and this package models them as **two
protocols** rather than one:

``PaymentProvider``
    Redirect + webhook. The customer leaves for the provider's page and the
    provider calls us back. Every provider implements this.

``CardTokenProvider``
    The card-token API (Payme Subscribe, Click Card Token) — register a card,
    confirm it with the code the provider texts, charge it later by token.
    This is what saved cards are built on (API.md §19).

**Why two protocols and not one with optional methods.** A provider may not
have a card API at all — Click's is a per-merchant contract add-on, and a
hosted-only provider has none. On a single protocol every adapter would carry
five methods that raise, which is the "abstract method that must not be called"
smell. Separated, ``isinstance(adapter, CardTokenProvider)`` *is* the capability
check, it narrows under mypy strict, and it matches the precedent
``ProductAdapter.supports()`` already sets: an unsupported step is a ``404``
(ARCHITECTURE.md §6). It also draws the PCI line in the type system — everything
that touches a card number is in one protocol in one file, which is the thing an
auditor asks to see.

**The card number.** It reaches this package and goes no further than the
outbound HTTP body. It is never stored, never logged, never put in a ``repr``
(PROJECT.md §13). ``CardCredentials`` exists to make that hard to get wrong.

Callbacks arrive more than once — providers resend, and Payme's protocol assumes
it. Handling must be idempotent: the same event must not settle twice
(ARCHITECTURE.md §8).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class PaymentProviderCode(StrEnum):
    PAYME = "payme"
    CLICK = "click"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PARTIALLY_REFUNDED = "partially_refunded"


class TransactionFlow(StrEnum):
    """How one attempt at a payment is being made (API.md §22)."""

    #: Hosted page; the answer arrives by webhook.
    REDIRECT = "redirect"
    #: A new card, entered for this payment only — ``card/`` then ``confirm/``.
    CARD = "card"
    #: A saved card, charged inline. The response is final.
    CARD_TOKEN = "card_token"


class TransactionStatus(StrEnum):
    CREATED = "created"
    AWAITING_CARD = "awaiting_card"
    AWAITING_OTP = "awaiting_otp"
    #: The customer is away at the provider (redirect flow).
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CardStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    REMOVED = "removed"
    EXPIRED = "expired"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, repr=False)
class CardCredentials:
    """A card number in flight. Never stored, never logged, never repr'd.

    The hand-written ``__repr__`` is the same precaution ``ActiveGtsCredential``
    takes, and for a sharper reason: a dataclass carrying a secret ends up
    inside a structlog ``exc_info``, an f-string or a failing assertion sooner
    or later, and the default ``repr`` would print the number in all three.
    """

    #: Digits only — the schema has already stripped spaces and run Luhn.
    number: str
    #: ``MMYY``, which is the shape both providers ask for.
    expire: str

    def __repr__(self) -> str:
        return f"CardCredentials(last4={self.number[-4:]!r})"


@dataclass(frozen=True, slots=True, repr=False)
class RegisteredCard:
    """What the provider says about a card it now holds a token for."""

    #: Clear text. The caller seals it with ``core.crypto`` before it is stored.
    token: str
    #: The provider's own masked string, e.g. ``860049******6604``.
    masked_pan: str
    last4: str
    bin: str | None = None
    #: ``uzcard`` · ``humo`` · ``visa`` · ``mastercard``
    brand: str | None = None
    expiry_month: int | None = None
    expiry_year: int | None = None
    verified: bool = False
    #: The masked phone the provider texted, for "code sent to 9989**1234".
    otp_sent_to: str | None = None
    #: The provider's own resend cooldown, when it gives one.
    otp_wait_seconds: int | None = None

    def __repr__(self) -> str:
        return f"RegisteredCard(last4={self.last4!r}, verified={self.verified})"


@dataclass(frozen=True, slots=True)
class ChargeResult:
    status: TransactionStatus
    #: Payme receipt ``_id`` / Click ``payment_id``.
    provider_ref: str | None = None
    #: The raw provider state, kept for diagnosis rather than for logic.
    provider_state: str | None = None
    paid_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True, slots=True)
class RefundResult:
    status: RefundStatus
    provider_ref: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """What the webhook route sends back — the provider's shape, not ours.

    ``status_code`` is here because the providers disagree with each other about
    what a rejected callback looks like: Payme wants ``200`` with a JSON-RPC
    error, since a ``401`` only makes it retry blindly. The invariant that holds
    either way, and the one the tests assert, is that nothing changed
    (API.md §40).
    """

    body: dict[str, Any]
    status_code: int = 200
    #: This callback moved money.
    settled: bool = False
    cancelled: bool = False
    provider_ref: str | None = None
    order_id: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCheck:
    """``ok`` means *the credentials authenticate*, not *a payment would work*."""

    ok: bool
    detail: str | None = None


@runtime_checkable
class PaymentProvider(Protocol):
    """Redirect + webhook. Every payment adapter implements this."""

    code: PaymentProviderCode

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        """Start a payment; returns the URL to send the customer to."""
        ...

    def verify_signature(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Authenticate a callback. A bad signature changes nothing.

        Takes the **raw bytes**: Click signs the form-encoded body it actually
        sent, and re-serialising a parsed dict would not reproduce it.
        """
        ...

    async def handle_callback(
        self, headers: Mapping[str, str], body: bytes
    ) -> CallbackResult:
        """Process a callback and describe the answer the provider expects.

        The envelope does not apply here (API.md §40).
        """
        ...

    async def refund(
        self, *, transaction_ref: str, amount: Decimal | None = None
    ) -> RefundResult:
        """Refund fully, or partially when ``amount`` is given.

        ``transaction_ref`` is the **provider's** id, not our UUID.
        """
        ...

    async def status(self, *, transaction_ref: str) -> ChargeResult:
        """Ask the provider what happened — the reconciliation path."""
        ...

    async def verify(self) -> ProviderCheck:
        """Check the credentials — behind ``integrations/payments/{code}/test/``.

        Mirrors ``Notifier.verify`` and ``SocialVerifier.verify``. It must not
        move money: the implementation reads a reference that cannot exist and
        judges the *kind* of error that comes back.
        """
        ...


@runtime_checkable
class CardTokenProvider(Protocol):
    """Saving and charging cards. **Optional** — not every provider has it.

    An adapter that does not implement this makes card routes ``404`` for its
    provider, the same answer ``ProductAdapter`` gives for a step a vertical
    does not serve.
    """

    code: PaymentProviderCode

    async def register_card(
        self, card: CardCredentials, *, save: bool = True
    ) -> RegisteredCard:
        """Hand the card to the provider and get a token back.

        The returned token is **not yet usable**: ``verified`` is false until
        the customer confirms with the code the provider texts them.
        """
        ...

    async def request_card_code(self, *, token: str) -> RegisteredCard:
        """Ask the provider to (re)send the confirmation code."""
        ...

    async def verify_card(self, *, token: str, code: str) -> RegisteredCard:
        """Confirm the card with the code.

        **We do not check the code** — the provider issues it and the provider
        validates it. There is no correct value on this side to compare against.
        """
        ...

    async def remove_card(self, *, token: str) -> None:
        """Forget the card on the provider's side."""
        ...

    async def charge_card(
        self,
        *,
        token: str,
        order_id: str,
        amount: Decimal,
        currency: str,
        reference: str,
    ) -> ChargeResult:
        """Charge a saved card. Synchronous — the result is final."""
        ...


#: Built per call from the credentials the panel holds, never cached.
type ProviderFactory = Callable[[Mapping[str, str]], PaymentProvider]


class PaymentRegistry:
    """Provider code → **factory**, not instance.

    ``ProductRegistry`` holds instances because product adapters are stateless.
    A payment adapter is not: it carries the credentials the owner edits from
    the panel, and four worker processes must not disagree about them. This is
    the same reasoning ``integrations.service.notifier()`` states — the adapter
    is built per call, from the row, every time.
    """

    def __init__(self) -> None:
        self._factories: dict[PaymentProviderCode, ProviderFactory] = {}

    def register(self, code: PaymentProviderCode, factory: ProviderFactory) -> None:
        self._factories[code] = factory

    def build(
        self, code: str, credentials: Mapping[str, str]
    ) -> PaymentProvider | None:
        """A configured adapter, or ``None`` for an unknown or unbuilt code."""
        try:
            factory = self._factories.get(PaymentProviderCode(code))
        except ValueError:
            return None
        return None if factory is None else factory(credentials)

    def codes(self) -> tuple[PaymentProviderCode, ...]:
        return tuple(self._factories)

    def supports_cards(self, code: str) -> bool:
        """Whether this provider's adapter can save cards (API.md §17).

        Answered by building the adapter with empty credentials and asking the
        type system. That is safe because a constructor must not talk to the
        network — it only stores what it was handed.
        """
        adapter = self.build(code, {})
        return isinstance(adapter, CardTokenProvider)


registry = PaymentRegistry()


__all__ = [
    "CallbackResult",
    "CardCredentials",
    "CardStatus",
    "CardTokenProvider",
    "ChargeResult",
    "PaymentProvider",
    "PaymentProviderCode",
    "PaymentRegistry",
    "PaymentStatus",
    "ProviderCheck",
    "ProviderFactory",
    "RefundResult",
    "RefundStatus",
    "RegisteredCard",
    "TransactionFlow",
    "TransactionStatus",
    "registry",
]
