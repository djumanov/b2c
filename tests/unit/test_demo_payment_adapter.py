"""The development-only payment adapter — ``providers/payments/demo.py``.

Two things are worth pinning: that it satisfies both ports (otherwise the card
routes it exists to unblock still answer 404), and that it cannot be switched on
in production.
"""

import pytest
from pydantic import ValidationError

from app.api.errors import NotFound, PaymentFailed
from app.core.config import Settings
from app.providers.payments.base import (
    CardCredentials,
    CardTokenProvider,
    PaymentProvider,
    PaymentProviderCode,
    TransactionStatus,
)
from app.providers.payments.demo import (
    DECLINE_SUFFIX,
    DECLINED_TEST_CARD,
    DEMO_OTP_CODE,
    DemoCardProvider,
    reset,
)

VALID = CardCredentials(number="8600490744664608", expire="0329")


def test_it_satisfies_both_ports() -> None:
    provider = DemoCardProvider()
    assert isinstance(provider, PaymentProvider)
    assert isinstance(provider, CardTokenProvider)


async def test_the_store_is_shared_between_instances() -> None:
    """The registry builds an adapter per call, so a card saved by one request
    must be visible to the next."""
    reset()
    card = await DemoCardProvider().register_card(VALID)
    # A different instance, as the next request would get.
    confirmed = await DemoCardProvider().verify_card(
        token=card.token, code=DEMO_OTP_CODE
    )
    assert confirmed.verified is True


async def test_registering_returns_an_unconfirmed_card() -> None:
    provider = DemoCardProvider()
    card = await provider.register_card(VALID)

    assert card.verified is False
    assert card.masked_pan == "860049******4608"
    assert card.last4 == "4608"
    assert card.brand == "uzcard"
    assert card.expiry_month == 3
    assert card.expiry_year == 2029
    assert card.otp_sent_to == "+9989**1234"


@pytest.mark.parametrize(
    ("number", "brand"),
    [
        ("8600490744664608", "uzcard"),
        ("9860160101234567", "humo"),
        ("4111111111111111", "visa"),
        ("5555555555554444", "mastercard"),
    ],
)
async def test_the_brand_follows_the_bin(number: str, brand: str) -> None:
    card = await DemoCardProvider().register_card(
        CardCredentials(number=number, expire="0329")
    )
    assert card.brand == brand


async def test_the_right_code_confirms_and_a_wrong_one_does_not() -> None:
    provider = DemoCardProvider()
    card = await provider.register_card(VALID)

    refused = await provider.verify_card(token=card.token, code="000000")
    # Returned unverified rather than raised — what the real providers do, and
    # what the service counts an attempt off.
    assert refused.verified is False

    confirmed = await provider.verify_card(token=card.token, code=DEMO_OTP_CODE)
    assert confirmed.verified is True
    # And it stays confirmed.
    assert (await provider.request_card_code(token=card.token)).verified is True


async def test_the_declining_test_card_is_refused_as_a_payment_failure() -> None:
    """It must reach the adapter, so it has to be Luhn-valid — the schema would
    otherwise reject it first and this branch would be unreachable."""
    from app.modules.payments.schemas import _luhn_ok

    assert DECLINED_TEST_CARD.endswith(DECLINE_SUFFIX)
    assert _luhn_ok(DECLINED_TEST_CARD), "the schema would refuse it before the adapter"

    with pytest.raises(PaymentFailed):
        await DemoCardProvider().register_card(
            CardCredentials(number=DECLINED_TEST_CARD, expire="0329")
        )


async def test_a_forgotten_card_cannot_be_used() -> None:
    provider = DemoCardProvider()
    card = await provider.register_card(VALID)
    await provider.remove_card(token=card.token)

    with pytest.raises(NotFound):
        await provider.verify_card(token=card.token, code=DEMO_OTP_CODE)


async def test_charging_always_succeeds() -> None:
    from decimal import Decimal

    provider = DemoCardProvider()
    card = await provider.register_card(VALID)
    await provider.verify_card(token=card.token, code=DEMO_OTP_CODE)

    result = await provider.charge_card(
        token=card.token,
        order_id="order-1",
        amount=Decimal("125000.00"),
        currency="UZS",
        reference="ref-1",
    )
    assert result.status is TransactionStatus.PAID
    assert result.provider_ref == "demo-receipt-ref-1"


def test_it_never_prints_the_number() -> None:
    provider = DemoCardProvider()
    assert VALID.number not in repr(provider)
    assert VALID.number not in repr(VALID)


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
        assert probe.supports_cards("payme") is True
    finally:
        demo.registry = original
