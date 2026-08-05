"""Money is a decimal, and it goes over the wire as a string (API.md §1)."""

from decimal import Decimal

import pytest

from app.core.money import Money, quantize, to_decimal


def test_serialises_amount_as_a_string() -> None:
    money = Money(amount=Decimal("125000"), currency="UZS")

    assert money.model_dump() == {"amount": "125000.00", "currency": "UZS"}


def test_serialised_amount_is_never_a_json_number() -> None:
    payload = Money(amount=Decimal("10.5"), currency="USD").model_dump_json()

    assert '"amount":"10.50"' in payload


def test_parses_a_string_amount() -> None:
    assert Money(amount="99.99", currency="usd").amount == Decimal("99.99")


def test_currency_is_upper_cased() -> None:
    assert Money(amount=1, currency="uzs").currency == "UZS"


@pytest.mark.parametrize("bad", ["US", "USDD", "12$", ""])
def test_rejects_a_bad_currency(bad: str) -> None:
    with pytest.raises(ValueError):
        Money(amount=1, currency=bad)


def test_rejects_float_amounts() -> None:
    """A float loses cents somewhere between here and the provider."""
    with pytest.raises(ValueError):
        Money(amount=10.5, currency="USD")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10.005", "10.01"),  # half up, not banker's
        ("10.004", "10.00"),
        ("10.015", "10.02"),
        ("-10.005", "-10.01"),
    ],
)
def test_rounds_half_up(raw: str, expected: str) -> None:
    assert quantize(Decimal(raw)) == Decimal(expected)


def test_arithmetic_stays_in_one_currency() -> None:
    a = Money(amount="10.00", currency="USD")
    b = Money(amount="5.50", currency="USD")

    assert (a + b).amount == Decimal("15.50")
    assert (a - b).amount == Decimal("4.50")
    assert (a * 3).amount == Decimal("30.00")


def test_mixing_currencies_raises() -> None:
    """Conversion is GTS's job (PROJECT.md A3), never an implicit local sum."""
    with pytest.raises(ValueError, match="convert"):
        Money(amount="10", currency="USD") + Money(amount="10", currency="UZS")


def test_money_is_immutable() -> None:
    money = Money(amount="1.00", currency="USD")

    with pytest.raises(ValueError):
        money.amount = Decimal("2.00")  # type: ignore[misc]


def test_zero() -> None:
    assert Money.zero("UZS").model_dump() == {"amount": "0.00", "currency": "UZS"}


def test_to_decimal_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        to_decimal("abc")
