"""A panel provider that charges nobody — the demonstration payment method.

The sandbox next door is a developer's tool: ``DEBUG`` only, never a panel
row, and the code typed at ``confirm`` chooses the outcome. This one is for
showing the product: the panel enables it like any real provider (``DEBUG``
plays no part), it sits beside Payme in site-config's ``payment_methods``,
and exactly one code — the ``otp`` the panel stores, ``123456`` unless
changed — pays. Any other code is ``OtpRejected``, the way a wrong SMS code
is, so the demo walks the same screens a real payment does — including the
one where a mistyped code is simply typed again.

Stateless like the sandbox: ``confirm`` answers locally and never loses an
answer, so ``status`` — which exists for charges whose answer went missing —
has nothing to look up and says ``pending``; the sweep's give-up window
closes the theoretical leftovers. Nothing here logs a card or a code.

``resend`` does nothing — the code is static, so there is nothing to send
again; it just confirms the same ``phone_hint`` still stands.
"""

import uuid
from typing import Final

from app.core.money import Money
from app.providers.payments.base import (
    CardDetails,
    OtpRejected,
    PaymentOutcome,
    PaymentStart,
    ProviderField,
)

DEMO_CODE: Final = "demo"

DEFAULT_OTP: Final = "123456"

#: What the panel asks for — the one code that pays. Plain text on purpose:
#: the operator reads it off the panel while demonstrating.
FIELDS: Final[tuple[ProviderField, ...]] = (
    ProviderField("otp", required=True, default=DEFAULT_OTP),
)


class DemoProvider:
    """Implements ``PaymentProvider`` without talking to anybody."""

    code = DEMO_CODE

    def __init__(self, otp: str = DEFAULT_OTP) -> None:
        self._otp = otp

    @classmethod
    def from_credentials(cls, credentials: dict[str, str]) -> "DemoProvider":
        return cls(otp=(credentials.get("otp") or "").strip() or DEFAULT_OTP)

    async def start(
        self, *, card: CardDetails, amount: Money, order_ref: str
    ) -> PaymentStart:
        return PaymentStart(
            reference=f"demo-{uuid.uuid4().hex}",
            phone_hint="+99890***0000",
            raw={"demo": True, "order_ref": order_ref, "amount": str(amount.amount)},
        )

    async def resend(self, *, reference: str) -> PaymentStart:
        # Stateless and deterministic — see the module docstring. Nothing to
        # send again; this only satisfies the port.
        return PaymentStart(reference=reference, phone_hint="+99890***0000")

    async def confirm(self, *, reference: str, otp: str) -> PaymentOutcome:
        if otp != self._otp:
            raise OtpRejected("The code is wrong")
        return PaymentOutcome("paid", reference=reference, raw={"demo": True})

    async def status(self, *, reference: str) -> PaymentOutcome:
        # Stateless on purpose — see the module docstring.
        return PaymentOutcome("pending", reference=reference)

    async def probe(self) -> None:
        """There is nothing to reach; the demo is always ready."""
        return None


__all__ = [
    "DEFAULT_OTP",
    "DEMO_CODE",
    "FIELDS",
    "DemoProvider",
]
