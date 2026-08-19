"""The payment port — Payme and Click in the first release (D7).

One protocol, ``PaymentProvider``, and it speaks the **card flow**: register a
card, ask for the code the provider texts, verify it, charge, forget the token.
The customer never leaves our checkout (API.md §22, ``O14``).

Both providers work the same way in outline and differ in every detail, and the
differences stay on this side of the port: Payme registers and asks for the code
in two calls where Click does it in one, Payme charges in two where Click
charges in one, and Payme counts money in tiyin where Click counts it in so'm.
An adapter that leaked any of that would make the service speak one provider's
dialect.

Saved cards do **not** pass through here. A saved card is a local record owned
by ``modules/payments`` — the number is stored AES-GCM encrypted and opened
server-side at charge time (PROJECT.md §13, D7). What the checkout does with a
``card_id`` is open that ciphertext and hand the digits to ``register_card``,
which is the same call a freshly typed number makes; nothing about *saving* a
card involves a provider.

The webhook half stays. Settlement now happens inside the ``confirm/`` request
(``O16``), but Payme closes its receipt by callback regardless, callbacks arrive
more than once, and its protocol assumes they will. Handling must be idempotent:
the same event must not settle twice (ARCHITECTURE.md §8).
"""

from collections.abc import Awaitable, Callable, Mapping
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


class TransactionStatus(StrEnum):
    """Where one attempt has got to (API.md §22).

    No ``created``: the row exists because ``transactions/`` opened it, and at
    that instant it is already waiting for a card.
    """

    AWAITING_CARD = "awaiting_card"
    AWAITING_OTP = "awaiting_otp"
    #: **The charge went out and the answer is unknown.** The one genuinely
    #: dangerous state: a timed-out charge may have moved money, so calling it
    #: ``failed`` would be a lie that invites a second one. ``payments.reconcile``
    #: asks the provider and closes it.
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RefundStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True, repr=False)
class CardCredentials:
    """A card number in flight, on its way to a provider. Never logged.

    Lives on the port rather than in ``modules/payments`` because both sides
    need it and a provider adapter may not import a module's service
    (ARCHITECTURE.md §4). ``payments.service.reveal_card`` returns one of these,
    and so does the request body's validator — which is the point: by the time
    a number reaches an adapter, where it came from no longer shows.

    The hand-written ``__repr__`` is not decoration. A dataclass carrying a
    secret ends up inside a structlog ``exc_info``, an f-string or a failing
    assertion sooner or later, and the default one would print the number in all
    three.
    """

    #: Digits only.
    number: str
    #: ``MMYY``, the shape both provider APIs use.
    expire: str

    def __repr__(self) -> str:
        return f"CardCredentials(last4={self.number[-4:]!r})"


@dataclass(frozen=True, slots=True)
class RegisteredCard:
    """A card the provider now holds, and the code it has just texted.

    Returning from ``register_card`` means the SMS **is already on its way** —
    Payme needs a second call for that and Click does not, and the service must
    not have to know which.
    """

    #: The provider's handle for the card. Sealed before it is stored.
    token: str
    masked_pan: str | None = None
    #: Masked by the adapter, because the provider masks it: we never see the
    #: customer's whole phone number and have no reason to.
    otp_sent_to: str | None = None
    #: How long the provider itself wants before another code is asked for. The
    #: service takes the larger of this and its own floor.
    otp_wait_seconds: int | None = None
    provider_state: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VerifiedCard:
    """The code checked out. The token may now be charged, once."""

    token: str
    masked_pan: str | None = None
    provider_state: dict[str, Any] | None = None


#: Called by an adapter the instant the provider names the charge, before the
#: call that moves the money. See ``PaymentProvider.charge_card``.
type ReferenceSink = Callable[[str], Awaitable[None]]


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
    """The card flow plus the callback half. Every payment adapter implements it."""

    code: PaymentProviderCode

    async def register_card(self, card: CardCredentials) -> RegisteredCard:
        """Hand the card to the provider and get the customer texted a code.

        The token that comes back is single-use: neither provider is asked to
        remember the card, because the copy worth keeping is our own encrypted
        one (D7). Payme is told ``save: false`` and Click ``temporary: true``.
        """
        ...

    async def request_card_code(self, *, token: str) -> RegisteredCard:
        """Send the code again for a card already registered."""
        ...

    async def verify_card(self, *, token: str, code: str) -> VerifiedCard:
        """Give the provider the code the customer read out.

        **We never check the code ourselves** — the provider issued it and the
        provider judges it. A wrong one is a ``PaymentFailed``, not a decision
        of ours.
        """
        ...

    async def charge_card(
        self,
        *,
        token: str,
        order_id: str,
        amount: Decimal,
        currency: str,
        on_reference: ReferenceSink | None = None,
    ) -> ChargeResult:
        """Take the money. ``amount`` is in the order's currency, always UZS.

        ``on_reference`` is the money-path form of "the row is written before
        the network call". Payme charges in two steps — one names the receipt,
        the next moves the money — and if the second times out, the receipt id
        must already be ours or the attempt is left pending with nothing to
        reconcile it against. An adapter calls this the moment the provider
        names the charge; the service persists the reference and commits before
        the paying call goes out. Click, which charges in one step, calls it
        once with the same id it then returns.
        """
        ...

    async def remove_card(self, *, token: str) -> None:
        """Release a token we are done with. Never raises for one already gone.

        This is cleanup — it runs after a refusal, after an exhausted code and
        after an order is cancelled — and cleanup that can fail loudly is
        cleanup that holds up the thing it was cleaning after.
        """
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
    "CardCredentials",
    "ChargeResult",
    "PaymentProvider",
    "PaymentProviderCode",
    "PaymentRegistry",
    "PaymentStatus",
    "ProviderCheck",
    "ProviderFactory",
    "ReferenceSink",
    "RefundResult",
    "RefundStatus",
    "RegisteredCard",
    "TransactionStatus",
    "VerifiedCard",
    "registry",
]
