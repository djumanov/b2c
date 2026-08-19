"""The development-only payment adapter — ``providers/payments/demo.py``.

Two things are worth pinning: that it satisfies the port (so the checkout has
something to call on a laptop, with no merchant account and no phone), and that
it cannot be switched on in production.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.payments.base import (
    CardCredentials,
    PaymentProvider,
    PaymentProviderCode,
    TransactionStatus,
)
from app.providers.payments.demo import DemoPaymentProvider


def test_it_satisfies_the_port() -> None:
    assert isinstance(DemoPaymentProvider(), PaymentProvider)


async def test_a_payment_redirects_back_to_the_caller() -> None:
    url = await DemoPaymentProvider().create_payment(
        order_id="order-1",
        amount=Decimal("125000.00"),
        currency="UZS",
        return_url="https://shop.example/return",
    )
    assert url == "https://shop.example/return?demo_payment=order-1"


async def test_the_card_flow_walks_all_three_steps() -> None:
    """A laptop checkout: card accepted, code always right, charge always paid."""
    provider = DemoPaymentProvider()

    registered = await provider.register_card(
        CardCredentials(number="8600490744664608", expire="0329")
    )
    assert registered.token == "demo-card-4608"
    assert registered.masked_pan == "860049******4608"
    # Masked even here: the fake must not teach a habit the live path forbids.
    assert registered.otp_sent_to == "+9989**0000"

    resent = await provider.request_card_code(token=registered.token)
    assert resent.token == registered.token

    verified = await provider.verify_card(token=registered.token, code="000000")
    assert verified.token == registered.token


async def test_the_charge_names_itself_before_it_pays() -> None:
    """``on_reference`` fires even where there is no gap to protect.

    The real adapters call it between naming the charge and paying it, so a
    service that forgot to persist the reference should fail on a laptop rather
    than against a merchant account.
    """
    seen: list[str] = []

    async def record(reference: str) -> None:
        seen.append(reference)

    result = await DemoPaymentProvider().charge_card(
        token="demo-card-4608",
        order_id="order-1",
        amount=Decimal("1250000.00"),
        currency="UZS",
        on_reference=record,
    )
    assert seen == ["demo-charge-order-1"]
    assert result.status is TransactionStatus.PAID
    assert result.provider_ref == "demo-charge-order-1"
    assert result.paid_at is not None


async def test_every_callback_settles_and_every_refund_succeeds() -> None:
    provider = DemoPaymentProvider()
    assert provider.verify_signature({}, b"") is True

    callback = await provider.handle_callback({}, b"")
    assert callback.settled is True

    refund = await provider.refund(transaction_ref="ref-1")
    assert refund.provider_ref == "demo-refund-ref-1"

    status = await provider.status(transaction_ref="ref-1")
    assert status.status is TransactionStatus.PAID

    assert (await provider.verify()).ok is True


# --- the production guard ------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    """A production-shaped settings object — the same base ``test_config`` uses."""
    base: dict[str, object] = {
        "debug": False,
        "jwt_secret_key": "a-real-key-of-at-least-thirty-two-characters",
        "postgres_password": "a-real-password",
        "first_owner_password": "a-real-password",
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_the_demo_adapter_is_refused_without_debug() -> None:
    with pytest.raises(ValidationError) as exc:
        _settings(debug=False, payments_demo_adapter=True)
    assert "PAYMENTS_DEMO_ADAPTER" in str(exc.value)


def test_the_demo_adapter_is_allowed_in_a_development_checkout() -> None:
    assert _settings(debug=True, payments_demo_adapter=True).payments_demo_adapter


def test_it_is_off_by_default() -> None:
    assert _settings(debug=False).payments_demo_adapter is False


def test_registering_puts_it_behind_a_real_provider_code() -> None:
    """A ``demo`` code of its own would seed a row in every client's panel."""
    from app.providers.payments import demo
    from app.providers.payments.base import PaymentRegistry

    original = demo.registry
    probe = PaymentRegistry()
    demo.registry = probe
    try:
        demo.register()
        assert probe.codes() == (PaymentProviderCode.PAYME,)
    finally:
        demo.registry = original
