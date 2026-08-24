"""A provider that charges nobody — the whole order flow, end to end, on a
test server whose prices are ``0.00`` anyway.

Deterministic on purpose: the code typed at ``confirm`` **is** the outcome,
so a tester can walk every branch of the payment step by hand, and so the
happy path of the ticketing step can be exercised against the GTS test
server without a real card.

=========  =============================================================
code       what the sandbox does
=========  =============================================================
``000000`` pays
``111111`` declines ("declined by the sandbox")
``222222`` raises ``UpstreamTimeout`` — the outcome is unknown, the
           attempt stays open; ``status`` then says ``pending`` forever,
           which is how the sweep's give-up path is exercised by hand
``333333`` answers ``pending`` — the provider is still deciding
anything   a wrong code, i.e. ``failed``
=========  =============================================================

It never reaches production: ``payments.service.payment_provider`` picks it
only when ``DEBUG`` is on **and** no real provider is enabled in the panel.

``resend`` does nothing — the codes are fixed and deterministic, so there is
nothing to send again.
"""

import uuid
from typing import Final

from app.api.errors import UpstreamTimeout
from app.core.money import Money
from app.providers.payments.base import CardDetails, PaymentOutcome, PaymentStart

SANDBOX_CODE: Final = "sandbox"

OTP_PAID: Final = "000000"
OTP_DECLINED: Final = "111111"
OTP_TIMEOUT: Final = "222222"
OTP_PENDING: Final = "333333"


class SandboxProvider:
    """Implements ``PaymentProvider`` without talking to anybody."""

    code = SANDBOX_CODE

    async def start(
        self, *, card: CardDetails, amount: Money, order_ref: str
    ) -> PaymentStart:
        return PaymentStart(
            reference=f"sbx-{uuid.uuid4().hex}",
            phone_hint="+99890***4567",
            raw={"sandbox": True, "order_ref": order_ref, "amount": str(amount.amount)},
        )

    async def resend(self, *, reference: str) -> PaymentStart:
        # Stateless and deterministic — see the module docstring.
        return PaymentStart(reference=reference, phone_hint="+99890***4567")

    async def confirm(self, *, reference: str, otp: str) -> PaymentOutcome:
        if otp == OTP_PAID:
            return PaymentOutcome("paid", reference=reference, raw={"sandbox": True})
        if otp == OTP_DECLINED:
            return PaymentOutcome(
                "failed", reference=reference, error="declined by the sandbox"
            )
        if otp == OTP_TIMEOUT:
            raise UpstreamTimeout("the sandbox provider did not answer in time")
        if otp == OTP_PENDING:
            return PaymentOutcome("pending", reference=reference)
        return PaymentOutcome("failed", reference=reference, error="wrong code")

    async def status(self, *, reference: str) -> PaymentOutcome:
        # Stateless on purpose — see the module docstring.
        return PaymentOutcome("pending", reference=reference)

    async def probe(self) -> None:
        """There is nothing to reach; the sandbox is always ready."""
        return None


__all__ = [
    "OTP_DECLINED",
    "OTP_PAID",
    "OTP_PENDING",
    "OTP_TIMEOUT",
    "SANDBOX_CODE",
    "SandboxProvider",
]
