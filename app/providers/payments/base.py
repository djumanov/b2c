"""The ``PaymentProvider`` port — Payme and Click in the first release (D7).

Both are redirect + webhook: the customer leaves for the provider's page and
the provider calls us back. **No card number ever reaches this server**, which
is why the ``transactions/{id}/card|confirm|resend-otp/`` endpoints stay in the
contract but unimplemented (API.md §41) and why we are outside PCI scope.

Callbacks arrive more than once — providers resend, and Payme's protocol
assumes it. Handling must be idempotent: the same event must not settle twice
(ARCHITECTURE.md §8).
"""

from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol


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


class PaymentProvider(Protocol):
    code: PaymentProviderCode

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        """Start a payment; returns the URL to send the customer to."""
        ...

    def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        """Authenticate a callback. A bad signature is ``401`` and changes nothing."""
        ...

    async def handle_callback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a callback and return the body the provider's protocol wants.

        The envelope does not apply here (API.md §40).
        """
        ...

    async def refund(
        self, *, transaction_id: str, amount: Decimal | None = None
    ) -> dict[str, Any]:
        """Refund fully, or partially when ``amount`` is given."""
        ...


__all__ = ["PaymentProvider", "PaymentProviderCode", "PaymentStatus"]
