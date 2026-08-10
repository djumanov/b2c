"""The payment registry and the two ports — ARCHITECTURE.md §12."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.providers.payments.base import (
    CallbackResult,
    CardCredentials,
    CardTokenProvider,
    ChargeResult,
    PaymentProvider,
    PaymentProviderCode,
    PaymentRegistry,
    ProviderCheck,
    RefundResult,
    RefundStatus,
    RegisteredCard,
    TransactionStatus,
)


@dataclass
class _Hosted:
    """Redirect only — no card API, the shape Click takes without the add-on."""

    code: PaymentProviderCode = PaymentProviderCode.CLICK
    credentials: Mapping[str, str] = field(default_factory=dict)

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        return "https://example.invalid/pay"

    def verify_signature(self, headers: Mapping[str, str], body: bytes) -> bool:
        return True

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


@dataclass
class _WithCards(_Hosted):
    code: PaymentProviderCode = PaymentProviderCode.PAYME

    async def register_card(
        self, card: CardCredentials, *, save: bool = True
    ) -> RegisteredCard:
        return RegisteredCard(token="tok", masked_pan="8600******0000", last4="0000")

    async def request_card_code(self, *, token: str) -> RegisteredCard:
        return RegisteredCard(token=token, masked_pan="8600******0000", last4="0000")

    async def verify_card(self, *, token: str, code: str) -> RegisteredCard:
        return RegisteredCard(
            token=token, masked_pan="8600******0000", last4="0000", verified=True
        )

    async def remove_card(self, *, token: str) -> None:
        return None

    async def charge_card(
        self,
        *,
        token: str,
        order_id: str,
        amount: Decimal,
        currency: str,
        reference: str,
    ) -> ChargeResult:
        return ChargeResult(status=TransactionStatus.PAID)


def _registry() -> PaymentRegistry:
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _WithCards(creds=creds))  # type: ignore[call-arg]
    registry.register(PaymentProviderCode.CLICK, lambda creds: _Hosted(creds=creds))  # type: ignore[call-arg]
    return registry


def test_both_protocols_are_satisfied_structurally() -> None:
    """No inheritance anywhere — a port an adapter must subclass is a port that
    breaks when it grows a helper."""
    assert isinstance(_Hosted(), PaymentProvider)
    assert isinstance(_WithCards(), PaymentProvider)
    assert isinstance(_WithCards(), CardTokenProvider)
    # The whole point of splitting them.
    assert not isinstance(_Hosted(), CardTokenProvider)


def test_build_returns_a_fresh_adapter_every_call() -> None:
    """The registry holds factories, not instances.

    An adapter carries decrypted credentials the owner edits from the panel;
    one cached at import would be stale in three of the four processes.
    """
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _WithCards())

    first = registry.build("payme", {})
    second = registry.build("payme", {})
    assert first is not second


def test_an_unknown_or_unregistered_code_builds_nothing() -> None:
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _WithCards())

    # Not a member of the enum at all.
    assert registry.build("paygine", {}) is None
    # A real code with no adapter behind it yet.
    assert registry.build("click", {}) is None


def test_supports_cards_answers_per_provider() -> None:
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _WithCards())
    registry.register(PaymentProviderCode.CLICK, lambda creds: _Hosted())

    assert registry.supports_cards("payme") is True
    assert registry.supports_cards("click") is False
    assert registry.supports_cards("paygine") is False


def test_codes_lists_what_was_registered() -> None:
    registry = PaymentRegistry()
    registry.register(PaymentProviderCode.PAYME, lambda creds: _WithCards())
    assert registry.codes() == (PaymentProviderCode.PAYME,)


def test_the_overrides_are_scoped_per_provider() -> None:
    """Pinning Payme must not make an unconfigured Click start working."""
    from app.providers import payments

    payme = _WithCards()
    payments.set_provider(PaymentProviderCode.PAYME, payme)
    try:
        assert payments.get_override(PaymentProviderCode.PAYME) is payme
        assert payments.get_override(PaymentProviderCode.CLICK) is None
    finally:
        payments.clear_overrides()

    assert payments.get_override(PaymentProviderCode.PAYME) is None


def test_registered_card_and_credentials_do_not_print_their_secrets() -> None:
    card = CardCredentials(number="8600490744664608", expire="0329")
    assert repr(card) == "CardCredentials(last4='4608')"

    registered = RegisteredCard(
        token="tok-secret", masked_pan="860049******4608", last4="4608"
    )
    assert "tok-secret" not in repr(registered)
