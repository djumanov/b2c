"""A payment adapter that pretends — for development only.

It stands in for the hosted redirect flow, so a checkout can be driven end to
end on a laptop with no merchant account. It is the payment counterpart of
``providers/notifications/log.py`` — the fallback that exists so a flow is
usable, not a simulation of the real thing. Two things it deliberately does
**not** model: money, and time. Every payment succeeds, and nothing expires.

**It is not registered unless ``PAYMENTS_DEMO_ADAPTER=true``, which in turn
refuses to load without ``DEBUG=true``** (``core/config.py``). On a live server
this is not a test double, it is a way to wave payments through.

Saved cards need nothing from here: they are local encrypted records owned by
``modules/payments`` and no provider is involved in saving one (PROJECT.md D7).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

import structlog

from app.providers.payments.base import (
    CallbackResult,
    ChargeResult,
    PaymentProviderCode,
    ProviderCheck,
    RefundResult,
    RefundStatus,
    TransactionStatus,
    registry,
)

logger = structlog.get_logger(__name__)


@dataclass
class DemoPaymentProvider:
    """Implements the hosted port, so every redirect path has something to call."""

    code: PaymentProviderCode = PaymentProviderCode.PAYME
    credentials: Mapping[str, str] = field(default_factory=dict)

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        return f"{return_url}?demo_payment={order_id}"

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
