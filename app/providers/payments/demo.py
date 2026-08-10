"""A payment adapter that pretends — for development only.

The card endpoints cannot be exercised without a provider: a saved card *is* a
provider token, so with nothing behind the port every request is a ``404``
(API.md §19). This stands in, so the whole flow — register, receive a code,
confirm, set a default, charge, forget — can be driven end to end on a laptop
with no merchant account.

**It is not registered unless ``PAYMENTS_DEMO_ADAPTER=true``, which in turn
refuses to load without ``DEBUG=true``** (``core/config.py``). Both gates are
deliberate. This class accepts any card the schema let through and confirms it
with a code it prints to the log; on a live server that is not a test double,
it is a way to collect real customers' card numbers and wave them through.

It is the payment counterpart of ``providers/notifications/log.py`` — the
fallback that exists so a checkout is usable, not a simulation of the real
thing. Two things it deliberately does **not** model: money, and time. Every
charge succeeds, and nothing expires on the provider's side.

State lives in memory, so it resets when the process does and is not shared
between workers. That is fine for what it is for, and it is the reason the real
adapters must never be written this way.
"""

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

import structlog

from app.api.errors import NotFound, PaymentFailed
from app.providers.payments.base import (
    CallbackResult,
    CardCredentials,
    ChargeResult,
    PaymentProviderCode,
    ProviderCheck,
    RefundResult,
    RefundStatus,
    RegisteredCard,
    TransactionStatus,
    registry,
)

logger = structlog.get_logger(__name__)

#: The code this provider always "sends". Fixed rather than random so a manual
#: run does not need the log at all — but it is logged too, because the
#: alternative is a developer wondering why 111111 stopped working after
#: somebody changed it here.
DEMO_OTP_CODE: Final = "111111"

#: Cards whose last four digits are this are refused at registration, so the
#: unhappy path can be exercised without editing code. The number still has to
#: pass Luhn to get this far — ``8600490000000000`` is one that does — otherwise
#: the schema rejects it first and this branch is never reached.
DECLINE_SUFFIX: Final = "0000"

#: A Luhn-valid number ending in :data:`DECLINE_SUFFIX`, quoted in ``.env.sample``
#: so the decline path is reachable by copy and paste.
DECLINED_TEST_CARD: Final = "8600490000000000"

#: Registered cards, keyed by token, **shared by every instance**.
#:
#: The registry builds an adapter per call (``PaymentRegistry`` holds factories,
#: not instances, because a real adapter carries credentials the owner can
#: change underneath it). A demo that kept its cards on ``self`` would therefore
#: forget every one of them between the request that saved a card and the
#: request that confirms it. Module state is wrong for a real adapter and right
#: for this one — it stands in for the provider, and the provider is not
#: rebuilt per call either.
_CARDS: dict[str, RegisteredCard] = {}


def reset() -> None:
    """Forget every demo card — for a test that wants a clean provider."""
    _CARDS.clear()


#: BIN prefix → the brand the real providers report.
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


@dataclass
class DemoCardProvider:
    """Implements both ports, so every card and redirect path has something to call."""

    code: PaymentProviderCode = PaymentProviderCode.PAYME
    credentials: Mapping[str, str] = field(default_factory=dict)

    # --- CardTokenProvider ---------------------------------------------------

    async def register_card(
        self, card: CardCredentials, *, save: bool = True
    ) -> RegisteredCard:
        if card.number.endswith(DECLINE_SUFFIX):
            # A catalogue error, not a bare exception: an adapter that raises
            # something the handler has never heard of is a 500, and "the bank
            # said no" is not a bug in this server (API.md §3).
            raise PaymentFailed("The demo provider declines cards ending 0000")

        token = f"demo-{secrets.token_hex(8)}"
        registered = RegisteredCard(
            token=token,
            masked_pan=f"{card.number[:6]}{'*' * 6}{card.number[-4:]}",
            last4=card.number[-4:],
            bin=card.number[:6],
            brand=_brand_for(card.number),
            expiry_month=int(card.expire[:2]),
            expiry_year=2000 + int(card.expire[2:]),
            verified=False,
            otp_sent_to="+9989**1234",
            otp_wait_seconds=None,
        )
        _CARDS[token] = registered
        # ``last4`` and never the number: this file is development tooling, not
        # an exemption from PROJECT.md §13.
        logger.info(
            "demo_card_registered",
            last4=registered.last4,
            confirm_with=DEMO_OTP_CODE,
        )
        return registered

    async def request_card_code(self, *, token: str) -> RegisteredCard:
        registered = self._require(token)
        logger.info(
            "demo_card_code_resent",
            last4=registered.last4,
            confirm_with=DEMO_OTP_CODE,
        )
        return registered

    async def verify_card(self, *, token: str, code: str) -> RegisteredCard:
        registered = self._require(token)
        if code != DEMO_OTP_CODE:
            # Returned rather than raised: an unverified card is what the real
            # providers answer for a wrong code, and the service counts the
            # attempt off exactly that.
            return registered
        # ``replace`` rather than reaching for ``__dict__``: the value objects
        # are ``slots=True`` and do not have one.
        confirmed = replace(registered, verified=True, otp_sent_to=None)
        _CARDS[token] = confirmed
        return confirmed

    async def remove_card(self, *, token: str) -> None:
        _CARDS.pop(token, None)

    async def charge_card(
        self,
        *,
        token: str,
        order_id: str,
        amount: Decimal,
        currency: str,
        reference: str,
    ) -> ChargeResult:
        self._require(token)
        return ChargeResult(
            status=TransactionStatus.PAID,
            provider_ref=f"demo-receipt-{reference}",
            provider_state="performed",
            paid_at=datetime.now(UTC),
        )

    def _require(self, token: str) -> RegisteredCard:
        registered = _CARDS.get(token)
        if registered is None:
            # Reachable after a process restart, since the store is in memory.
            # ``NotFound`` rather than a 500: from the customer's side a card the
            # provider no longer knows about is a card that is not there.
            raise NotFound("The demo provider has forgotten this card")
        return registered

    # --- PaymentProvider -----------------------------------------------------

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        return f"{return_url}?demo_payment={order_id}"

    def verify_signature(self, headers: Mapping[str, str], body: bytes) -> bool:
        return True

    async def handle_callback(
        self, headers: Mapping[str, str], body: bytes
    ) -> CallbackResult:
        return CallbackResult(body={"result": {"state": 2}}, settled=True)

    async def refund(
        self, *, transaction_ref: str, amount: Decimal | None = None
    ) -> RefundResult:
        return RefundResult(
            status=RefundStatus.SUCCEEDED, provider_ref=f"demo-refund-{transaction_ref}"
        )

    async def status(self, *, transaction_ref: str) -> ChargeResult:
        return ChargeResult(status=TransactionStatus.PAID, provider_ref=transaction_ref)

    async def verify(self) -> ProviderCheck:
        return ProviderCheck(ok=True, detail="Demo provider — no real account")


def register(code: PaymentProviderCode = PaymentProviderCode.PAYME) -> None:
    """Put the demo adapter behind one provider code.

    Behind an existing code rather than a ``demo`` one of its own: the set of
    codes seeds a row in every installation's panel
    (``integrations/repository.py::payment_providers``), so a new member would
    show a client a payment method that does not exist.
    """
    registry.register(
        code, lambda credentials: DemoCardProvider(credentials=credentials)
    )
    logger.warning(
        "demo_payment_adapter_registered",
        provider=code.value,
        detail="Cards are accepted without a merchant account. Development only.",
    )


__all__ = [
    "DECLINED_TEST_CARD",
    "DECLINE_SUFFIX",
    "DEMO_OTP_CODE",
    "DemoCardProvider",
    "register",
    "reset",
]
