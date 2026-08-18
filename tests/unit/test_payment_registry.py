"""The payment registry and the hosted port — ARCHITECTURE.md §12."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.providers.payments.base import (
    CallbackResult,
    ChargeResult,
    PaymentProvider,
    PaymentProviderCode,
    PaymentRegistry,
    ProviderCheck,
    RefundResult,
    RefundStatus,
    TransactionStatus,
)


@dataclass
class _Hosted:
    """A minimal structural implementation of the hosted port."""

    code: PaymentProviderCode = PaymentProviderCode.CLICK
    credentials: Mapping[str, str] = field(default_factory=dict)

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        return "https://example.invalid/pay"

    def verify_signature(self, headers: Mapping[str, str], body: bytes) -> bool:
        return True

    def signature_rejected(self) -> CallbackResult:
        return CallbackResult(body={}, status_code=401)

    async def handle_callback(
        self, headers: Mapping[str, str], body: bytes
    ) -> CallbackResult:
        return CallbackResult(body={})

    async def refund(
        self, *, transaction_ref: str, amount: Decimal | None = None
    ) -> RefundResult:
        return RefundResult(status=RefundStatus.SUCCEEDED)

    async def status(self, *, transaction_ref: str) -> ChargeResult:
        return ChargeResult(status=TransactionStatus.PAID)

    async def verify(self) -> ProviderCheck:
        return ProviderCheck(ok=True)


def test_the_protocol_is_satisfied_structurally() -> None:
    """No inheritance anywhere — a port an adapter must subclass is a port that
    breaks when it grows a helper."""
    assert isinstance(_Hosted(), PaymentProvider)


def test_build_returns_a_fresh_adapter_every_call() -> None:
    """The registry holds factories, not instances.

    An adapter carries decrypted credentials the owner edits from the panel;
    one cached at import would be stale in three of the four processes.
    """
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _Hosted())

    first = registry.build("payme", {})
    second = registry.build("payme", {})
    assert first is not second


def test_an_unknown_or_unregistered_code_builds_nothing() -> None:
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _Hosted())

    # Not a member of the enum at all.
    assert registry.build("paygine", {}) is None
    # A real code with no adapter behind it yet.
    assert registry.build("click", {}) is None


def test_codes_lists_what_was_registered() -> None:
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _Hosted())
    assert registry.codes() == (PaymentProviderCode.PAYME,)


def test_the_overrides_are_scoped_per_provider() -> None:
    """Pinning Payme must not make an unconfigured Click start working."""
    from app.providers import payments

    payme = _Hosted(code=PaymentProviderCode.PAYME)
    payments.set_provider(PaymentProviderCode.PAYME, payme)
    try:
        assert payments.get_override(PaymentProviderCode.PAYME) is payme
        assert payments.get_override(PaymentProviderCode.CLICK) is None
    finally:
        payments.clear_overrides()

    assert payments.get_override(PaymentProviderCode.PAYME) is None
