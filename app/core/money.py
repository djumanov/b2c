"""Money: a decimal amount and its currency, serialised as a string.

The contract is ``{"amount": "125000.00", "currency": "UZS"}`` — a **string**,
never a JSON number (API.md §1). A float amount silently loses cents somewhere
between the database, the payment provider and the client; a JSON number does
the same in whichever client parses it into a double. Both are avoided by
keeping ``Decimal`` inside and text on the wire.

Stored as ``NUMERIC(18,2)`` with the currency in a separate ``CHAR(3)`` column
(ARCHITECTURE.md §10) — see ``money_column`` / ``currency_column``.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Final

from pydantic import BaseModel, field_serializer, field_validator
from sqlalchemy import CHAR, Numeric
from sqlalchemy.orm import Mapped, mapped_column

#: Amounts are stored and compared at two decimal places, in every currency.
#: The contract shows ``"125000.00"`` for UZS, so this is not per-currency.
EXPONENT: Final = Decimal("0.01")
AMOUNT_PRECISION: Final = 18
AMOUNT_SCALE: Final = 2
CURRENCY_LENGTH: Final = 3


def quantize(amount: Decimal) -> Decimal:
    """Round to two places, half up — the rounding a customer expects."""
    return amount.quantize(EXPONENT, rounding=ROUND_HALF_UP)


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Parse an amount. Floats are rejected rather than quietly accepted.

    ``ValueError`` and not ``TypeError``: this runs inside a Pydantic validator,
    and a client that sends ``{"amount": 10.5}`` should get a ``422`` naming the
    field, not a 500.
    """
    if isinstance(value, float):
        raise ValueError("send money amounts as a string, not a JSON number")
    try:
        return quantize(Decimal(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a valid amount: {value!r}") from exc


class Money(BaseModel):
    """An amount in one currency. Immutable; arithmetic returns new values."""

    model_config = {"frozen": True}

    amount: Decimal
    currency: str

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: Any) -> Decimal:
        return to_decimal(value)

    @field_validator("currency")
    @classmethod
    def _parse_currency(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != CURRENCY_LENGTH or not code.isalpha():
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return code

    @field_serializer("amount")
    def _serialize_amount(self, amount: Decimal) -> str:
        return f"{amount:.{AMOUNT_SCALE}f}"

    def _same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"cannot combine {self.currency} and {other.currency}; convert "
                "first — conversion is GTS's job (PROJECT.md A3)"
            )

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: Decimal | int) -> "Money":
        return Money(amount=self.amount * Decimal(factor), currency=self.currency)

    def __str__(self) -> str:
        return f"{self.amount:.{AMOUNT_SCALE}f} {self.currency}"

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(amount=Decimal("0"), currency=currency)


#: ``NUMERIC(18,2)`` — use for every amount column.
AmountColumn = Annotated[
    Decimal, mapped_column(Numeric(AMOUNT_PRECISION, AMOUNT_SCALE))
]


def money_column(**kwargs: Any) -> Mapped[Decimal]:
    """An amount column: ``total: Mapped[Decimal] = money_column()``."""
    return mapped_column(Numeric(AMOUNT_PRECISION, AMOUNT_SCALE), **kwargs)


def currency_column(**kwargs: Any) -> Mapped[str]:
    """The currency column that pairs with an amount."""
    return mapped_column(CHAR(CURRENCY_LENGTH), **kwargs)


__all__ = [
    "AMOUNT_PRECISION",
    "AMOUNT_SCALE",
    "CURRENCY_LENGTH",
    "EXPONENT",
    "AmountColumn",
    "Money",
    "currency_column",
    "money_column",
    "quantize",
    "to_decimal",
]
