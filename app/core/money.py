"""Money: a decimal amount and its currency, serialised as a string.

The contract is ``{"amount": "125000.00", "currency": "UZS"}`` — a **string**,
never a JSON number (API.md §1). A float amount silently loses cents somewhere
between the database, the payment provider and the client; a JSON number does
the same in whichever client parses it into a double. Both are avoided by
keeping ``Decimal`` inside and text on the wire.

Stored as ``NUMERIC(18,2)`` with the currency in a separate ``CHAR(3)`` column
(ARCHITECTURE.md §10) — see ``money_column`` / ``currency_column``.

**One currency, and it is code rather than a setting.** ``CURRENCY`` below is
the deliberate exception to "anything a client could want different lives in
the database": an installation sells against one GTS agreement, that agreement
is denominated in one currency, and a second one is not a value somebody types
into a panel. It is an exchange-rate source, a rate history, a rounding policy
and an audit trail that says which rate a given charge was taken at — none of
which exist here. So the choice is not offered, and every figure that reaches
a card, the deposit or a stored order is checked against this constant instead.

The shape on the wire keeps its ``currency`` field. It costs nothing, it is
what every client already reads, and a payload that names its own currency is
still the honest one to send.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any, Final

from pydantic import BaseModel, Field, field_serializer, field_validator
from sqlalchemy import CHAR, Numeric
from sqlalchemy.orm import Mapped, mapped_column

#: Amounts are stored and compared at two decimal places, in every currency.
#: The contract shows ``"125000.00"`` for UZS, so this is not per-currency.
EXPONENT: Final = Decimal("0.01")
AMOUNT_PRECISION: Final = 18
AMOUNT_SCALE: Final = 2
CURRENCY_LENGTH: Final = 3

#: The one currency this installation prices, charges and books in. See the
#: module docstring for why it is a constant and not a row in ``settings``.
#: A ``CHECK`` constraint on ``orders`` and ``payment_attempts`` says the same
#: thing at the level that still holds when two workers write at once.
CURRENCY: Final = "UZS"


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
    """An amount in one currency, e.g. `{"amount": "287500.00", "currency": "UZS"}`.

    `amount` is always a **string with two decimals**, never a JSON number.
    `currency` is always `UZS` — this installation prices in one currency.
    Immutable; arithmetic returns new values.
    """

    model_config = {
        "frozen": True,
        "json_schema_extra": {"examples": [{"amount": "287500.00", "currency": "UZS"}]},
    }

    amount: Decimal = Field(
        description=(
            "The value as a decimal string with exactly two places "
            '(`"287500.00"`). Sent as a string so no client rounds it.'
        )
    )
    currency: str = Field(
        default=CURRENCY,
        description=(
            f"Always `{CURRENCY}`. This installation prices, charges and books "
            "in one currency; the field is kept because a payload that names "
            "its own currency is the honest one to send."
        ),
        pattern=r"^[A-Z]{3}$",
        json_schema_extra={"example": CURRENCY},
    )

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount(cls, value: Any) -> Decimal:
        return to_decimal(value)

    @field_validator("currency")
    @classmethod
    def _parse_currency(cls, value: str) -> str:
        """The one currency, or a refusal.

        ``Money`` is a **response** type everywhere — no request body carries
        one — so this never turns a client's mistake into a 500. What it does
        catch is a figure arriving from GTS in a currency this installation
        cannot charge, at the moment somebody tries to build a price out of
        it. The callers that read GTS refuse such a figure earlier and with a
        better sentence (``orders.service._require_our_currency``); this is
        the floor under them.
        """
        code = value.strip().upper()
        if len(code) != CURRENCY_LENGTH or not code.isalpha():
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        if code != CURRENCY:
            raise ValueError(f"this installation prices in {CURRENCY} only, got {code}")
        return code

    @field_serializer("amount")
    def _serialize_amount(self, amount: Decimal) -> str:
        return f"{amount:.{AMOUNT_SCALE}f}"

    def _same_currency(self, other: "Money") -> None:
        """Kept, though the validator above already makes it unreachable.

        Two ``Money`` values cannot differ in currency while there is only one
        currency — but the day a second one arrives, arithmetic is exactly
        where a missing conversion would otherwise pass silently.
        """
        if self.currency != other.currency:
            raise ValueError(
                f"cannot combine {self.currency} and {other.currency}; there is "
                "no conversion here, and none is configured"
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
    def zero(cls, currency: str = CURRENCY) -> "Money":
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
    "CURRENCY",
    "CURRENCY_LENGTH",
    "EXPONENT",
    "AmountColumn",
    "Money",
    "currency_column",
    "money_column",
    "quantize",
    "to_decimal",
]
