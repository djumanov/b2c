"""A payment adapter that pretends — for development only.

It stands in for the card flow, so a checkout can be driven end to end on a
laptop with no merchant account and no phone: the card is always accepted and
the code is always right, so a developer walks the three steps without waiting
for an SMS that is never sent. It is the payment counterpart of
``providers/notifications/log.py`` — the fallback that exists so a flow is
usable, not a simulation of the real thing. Three things it deliberately does
**not** model: money, time, and refusal. Every payment succeeds, nothing
expires, and no code is ever wrong; the refusal paths are exercised by test
doubles, where the expected failure can be named.

**It is not registered unless ``PAYMENTS_DEMO_ADAPTER=true``, which in turn
refuses to load without ``DEBUG=true``** (``core/config.py``). On a live server
this is not a test double, it is a way to wave payments through.

Saved cards need nothing from here: they are local encrypted records owned by
``modules/payments`` and no provider is involved in saving one (PROJECT.md D7).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import structlog

from app.providers.payments.base import (
    CallbackResult,
    CardCredentials,
    ChargeResult,
    PaymentProviderCode,
    ProviderCheck,
    ReferenceSink,
    RefundResult,
    RefundStatus,
    RegisteredCard,
    TransactionStatus,
    VerifiedCard,
    registry,
)

logger = structlog.get_logger(__name__)


@dataclass
class DemoPaymentProvider:
    """Implements the port, so every path in the checkout has something to call."""

    code: PaymentProviderCode = PaymentProviderCode.PAYME
    credentials: Mapping[str, str] = field(default_factory=dict)

    async def register_card(self, card: CardCredentials) -> RegisteredCard:
        # ``card`` is read for its last four digits and for nothing else — the
        # rule the real adapters follow, kept here so the fake cannot teach a
        # habit the live path forbids.
        return RegisteredCard(
            token=f"demo-card-{card.number[-4:]}",
            masked_pan=f"{card.number[:6]}{'*' * 6}{card.number[-4:]}",
            otp_sent_to="+9989**0000",
            otp_wait_seconds=0,
        )

    async def request_card_code(self, *, token: str) -> RegisteredCard:
        return RegisteredCard(
            token=token, otp_sent_to="+9989**0000", otp_wait_seconds=0
        )

    async def verify_card(self, *, token: str, code: str) -> VerifiedCard:
        return VerifiedCard(token=token)

    async def charge_card(
        self,
        *,
        token: str,
        order_id: str,
        amount: Decimal,
        currency: str,
        on_reference: ReferenceSink | None = None,
    ) -> ChargeResult:
        reference = f"demo-charge-{order_id}"
        if on_reference is not None:
            # The real adapters call this between naming the charge and paying
            # it. There is no gap to protect here, but the fake still exercises
            # the callback: a service that forgot to persist the reference
            # should fail on a laptop, not on a merchant account.
            await on_reference(reference)
        return ChargeResult(
            status=TransactionStatus.PAID,
            provider_ref=reference,
            paid_at=datetime.now(UTC),
        )

    async def remove_card(self, *, token: str) -> None:
        return None

    def verify_signature(self, headers: Mapping[str, str], body: bytes) -> bool:
        return True

    def signature_rejected(self) -> CallbackResult:
        return CallbackResult(body={"error": "bad signature"}, status_code=401)

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
        code, lambda credentials: DemoPaymentProvider(credentials=credentials)
    )
    logger.warning(
        "demo_payment_adapter_registered",
        provider=code.value,
        detail="Payments succeed without a merchant account. Development only.",
    )


__all__ = [
    "DemoPaymentProvider",
    "register",
]
