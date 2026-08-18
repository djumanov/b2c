"""The payment port — Payme and Click in the first release (D7).

One protocol, ``PaymentProvider``: redirect + webhook. The customer leaves for
the provider's page and the provider calls us back. Every payment adapter
implements it.

Saved cards do **not** pass through here. A saved card is a local record owned
by ``modules/payments`` — the number is stored AES-GCM encrypted and opened
server-side at charge time (PROJECT.md §13, D7). When the checkout path lands
(phase 2) it will feed the opened number into the provider's card flow; nothing
about *saving* a card involves a provider.

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
    #: The card flow — ``card/`` then ``confirm/``. With a saved card the
    #: server fills the card step itself from the stored encrypted number.
    CARD = "card"


class TransactionStatus(StrEnum):
    CREATED = "created"
    AWAITING_CARD = "awaiting_card"
    AWAITING_OTP = "awaiting_otp"
    #: The customer is away at the provider (redirect flow).
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


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

    def signature_rejected(self) -> CallbackResult:
        """What to answer a callback whose signature did not check out.

        On the port because the providers disagree about it and the route must
        not guess: a plain ``401`` is the ordinary answer, but Payme reads one
        as "retry blindly" and wants ``200`` with JSON-RPC ``-32504`` instead
        (API.md §40). What the route enforces either way is that nothing
        changed before this is returned.
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


registry = PaymentRegistry()


__all__ = [
    "CallbackResult",
    "ChargeResult",
    "PaymentProvider",
    "PaymentProviderCode",
    "PaymentRegistry",
    "PaymentStatus",
    "ProviderCheck",
    "ProviderFactory",
    "RefundResult",
    "RefundStatus",
    "TransactionFlow",
    "TransactionStatus",
    "registry",
]
