"""The payment provider port — how a card is charged, whoever charges it.

Payme and Click (D7) charge a card the same way from where we stand: the
number and expiry go to the provider, the provider texts the cardholder a
code, and the charge happens when the code comes back. So the port has four
calls — ``start``, ``resend``, ``confirm``, ``status`` — and every provider,
the sandbox included, fits behind them.

**The contract that matters is about failure.** A provider answers a
definitive "no" (wrong code, card declined, not enough money) as a
``PaymentOutcome`` — that is a result, the attempt is over, the customer may
try again. It raises (``UpstreamError``, ``UpstreamTimeout``) only when the
outcome is **unknown**: the call did not get through or did not come back.
The order module treats those two very differently — a known "no" is
recorded, an unknown is left open and settled later by ``status`` — so an
adapter that raised on a decline would strand the customer in "processing".

Nothing here stores or logs a card number. ``CardDetails`` hides its digits
from ``repr``, and ``raw`` is whatever the adapter chose to keep of the
provider's answer, scrubbed by the adapter before it is handed back.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from app.core.money import Money


class PaymentProviderCode(StrEnum):
    """The providers the panel knows. The sandbox is not one of them on
    purpose: it is chosen by ``DEBUG``, never by a client's settings.

    ``demo`` is a panel provider like the others — enabled for demo
    installations, it charges nobody and accepts one static code the panel
    sets (``providers/payments/demo.py``).
    """

    PAYME = "payme"
    CLICK = "click"
    DEMO = "demo"


@dataclass(frozen=True, slots=True, repr=False)
class CardDetails:
    """A card on its way to a provider. Never logged, never stored.

    The hand-written ``__repr__`` is not decoration: a dataclass carrying a
    number ends up inside a structlog ``exc_info``, an f-string or a failing
    assertion sooner or later, and the default one would print it.
    """

    #: Digits only.
    number: str
    #: ``MMYY``, the shape both provider APIs use.
    expire: str

    @property
    def last4(self) -> str:
        return self.number[-4:]

    def __repr__(self) -> str:
        return f"CardDetails(last4={self.last4!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PaymentStart:
    """What ``start`` hands back: the provider's handle on this payment.

    ``reference`` is whatever the provider needs to hear again at ``confirm``
    and ``status`` — for Payme it is a card token that can charge, which is
    why the orders module stores it encrypted and this class does not print
    it. ``phone_hint`` is the masked number the code went to, for the screen.
    """

    reference: str
    phone_hint: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"PaymentStart(phone_hint={self.phone_hint!r})"


#: ``pending`` means the provider itself is still deciding — keep asking.
OutcomeStatus = Literal["paid", "failed", "pending"]


@dataclass(frozen=True, slots=True, repr=False)
class PaymentOutcome:
    """The answer to ``confirm`` or ``status`` — a verdict, never an exception.

    ``repr`` shows the verdict and the reason, never ``reference``: it is the
    same charging handle ``PaymentStart`` keeps out of its own.
    """

    status: OutcomeStatus
    reference: str | None = None
    #: The provider's reason on ``failed``, in words a customer may read.
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"PaymentOutcome(status={self.status!r}, error={self.error!r})"


class PaymentDeclined(Exception):
    """``start`` or ``resend`` refused outright — a result, not an outage.

    Raised by ``start`` and ``resend`` only (``confirm`` answers with an
    outcome): the card number is wrong, expired, the provider will not take
    it, or it will not send another code. The attempt is recorded as failed
    and the customer may try again.
    """


class PaymentProvider(Protocol):
    code: str

    async def start(
        self, *, card: CardDetails, amount: Money, order_ref: str
    ) -> PaymentStart:
        """Register the card for one charge and send the cardholder a code.

        Raises ``PaymentDeclined`` for a definitive refusal, ``UpstreamError``
        / ``UpstreamTimeout`` when the outcome is unknown. Nothing is charged
        at this step, so either kind of failure is safe to show and retry.
        """
        ...

    async def resend(self, *, reference: str) -> PaymentStart:
        """Send the cardholder the same attempt's code again — no new card, no charge.

        Raises ``PaymentDeclined`` for a definitive refusal to send another
        code, ``UpstreamError``/``UpstreamTimeout`` when the outcome is
        unknown — the same failure shape ``start`` uses, because nothing has
        been charged here either. A provider whose code is static or
        deterministic (the demo, the sandbox) does nothing and simply
        confirms the same ``reference``/``phone_hint`` still hold.
        """
        ...

    async def confirm(self, *, reference: str, otp: str) -> PaymentOutcome:
        """Charge, with the code the cardholder received.

        A wrong code or a decline is a ``failed`` outcome. Raises only when
        the outcome is unknown — the caller then keeps the attempt open and
        asks ``status`` later.
        """
        ...

    async def status(self, *, reference: str) -> PaymentOutcome:
        """What became of a charge whose ``confirm`` answer was lost."""
        ...

    async def probe(self) -> None:
        """Prove the stored settings reach the provider — the panel's test button.

        Moves no money and sends nobody a code. Raises ``UpstreamError`` with
        the provider's own words when the settings are refused, and
        ``UpstreamTimeout`` when it cannot be reached; returns on success.
        """
        ...


@dataclass(frozen=True, slots=True)
class ProviderField:
    """One setting a provider wants from the panel, described once.

    The panel renders a form from these instead of a free key/value editor,
    so a typo in a key name is refused at ``PATCH`` rather than discovered
    when a payment fails. ``secret`` values come back masked; the others are
    shown as written — an operator must be able to read ``environment``.
    """

    key: str
    kind: Literal["text", "secret", "int", "choice"] = "text"
    required: bool = False
    choices: tuple[str, ...] = ()
    default: str | None = None

    @property
    def secret(self) -> bool:
        return self.kind == "secret"


__all__ = [
    "CardDetails",
    "OutcomeStatus",
    "PaymentDeclined",
    "PaymentOutcome",
    "PaymentProvider",
    "PaymentProviderCode",
    "PaymentStart",
    "ProviderField",
]
