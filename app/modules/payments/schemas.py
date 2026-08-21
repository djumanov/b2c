"""Request and response shapes for saved cards (API.md §19).

The card number and expiry are ``SecretStr`` and **write-only**. That buys four
things at once: ``model_dump()``, ``repr()``, an f-string and a Pydantic
``ValidationError`` all print ``**********`` rather than the number, and the
generated OpenAPI marks the field ``format: password`` so no client puts it in a
URL. ``CardOut`` has no field for either, so there is no path by which one comes
back out.
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, SecretStr, field_validator

#: A card number is 13–19 digits (ISO/IEC 7812). Spaces and dashes are stripped
#: before this is applied — people paste what is printed on the card.
_DIGITS = re.compile(r"\d{13,19}$")
_SEPARATORS = re.compile(r"[\s-]")
#: ``MMYY``, which is what the payment forms use.
_EXPIRE = re.compile(r"(0[1-9]|1[0-2])\d{2}$")


def _luhn_ok(number: str) -> bool:
    """The check digit every card carries.

    Cheap, and it turns the commonest typo into a ``422`` before anything is
    stored.
    """
    total, odd = 0, False
    for char in reversed(number):
        digit = int(char)
        if odd:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
        odd = not odd
    return total % 10 == 0


def _clean_number(value: SecretStr) -> SecretStr:
    """Digits only, or a refusal — shared by saving a card and paying with one.

    A number good enough to store is exactly a number good enough to spend, so
    both request models are held to the same rule.
    """
    digits = _SEPARATORS.sub("", value.get_secret_value())
    if not _DIGITS.fullmatch(digits) or not _luhn_ok(digits):
        # Deliberately does not say which of the two failed: length and check
        # digit are both "this is not a card number" to the person typing it,
        # and naming the check digit only helps somebody generating candidates.
        raise ValueError("This does not look like a card number")
    return SecretStr(digits)


def _clean_expire(value: SecretStr) -> SecretStr:
    expire = _SEPARATORS.sub("", value.get_secret_value()).replace("/", "")
    if not _EXPIRE.fullmatch(expire):
        raise ValueError("Expiry must be MMYY")
    return SecretStr(expire)


class CardOut(BaseModel):
    """A saved card as the customer sees it. Never carries the number."""

    model_config = {"from_attributes": True}

    id: uuid.UUID = Field(description="Send as `card_id` when paying.")
    masked_pan: str = Field(
        description="First six and last four digits, e.g. `860006******6311`."
    )
    last4: str = Field(description="Last four digits, for the card picker.")
    brand: str | None = Field(
        description="`uzcard`, `humo`, `visa`, `mastercard`, or null when unknown."
    )
    expiry_month: int = Field(description="1–12.")
    expiry_year: int = Field(description="Four digits, e.g. `2030`.")
    last_used_at: datetime | None = Field(
        description="When a payment with this card last landed."
    )
    created_at: datetime
    updated_at: datetime


class CardCreateIn(BaseModel):
    """A card to keep for next time. Saving is local: no charge, no code.

    The number is checked (length, Luhn) and sealed at rest; the answer never
    carries it. The same card saved twice is a `422` on `number`.
    """

    # ``hide_input_in_errors`` is not a nicety here. Pydantic records the raw
    # input on every ``ValidationError`` — ``input_value='8600…'`` — and it does
    # so *before* the value becomes a ``SecretStr``, so the secret type does not
    # cover it. The HTTP response only carries ``msg`` and would not leak it,
    # but anything that logs the exception or renders its ``str()`` would. A
    # regression test pins this.
    model_config = {
        "extra": "forbid",
        "hide_input_in_errors": True,
        "json_schema_extra": {
            "examples": [{"number": "8600 0691 9540 6311", "expire": "03/99"}]
        },
    }

    number: SecretStr = Field(
        description=(
            "13–19 digits; spaces and dashes are allowed and ignored. "
            "Luhn-checked. Write-only: never echoed, never logged."
        ),
        json_schema_extra={"example": "8600 0691 9540 6311"},
    )
    expire: SecretStr = Field(
        description="Expiry as `MMYY` (`/` allowed: `03/99`). Write-only.",
        json_schema_extra={"example": "03/99"},
    )

    @field_validator("number")
    @classmethod
    def _valid_number(cls, value: SecretStr) -> SecretStr:
        return _clean_number(value)

    @field_validator("expire")
    @classmethod
    def _valid_expire(cls, value: SecretStr) -> SecretStr:
        return _clean_expire(value)


class CardIn(BaseModel):
    """A card typed at checkout — the same rules as saving one; not stored
    unless the payment asks for it with `save: true`."""

    model_config = {"extra": "forbid", "hide_input_in_errors": True}

    number: SecretStr = Field(
        description=(
            "13–19 digits; spaces and dashes are allowed and ignored. "
            "Luhn-checked. Write-only: never echoed, never logged."
        ),
        json_schema_extra={"example": "8600 0691 9540 6311"},
    )
    expire: SecretStr = Field(
        description="Expiry as `MMYY` (`/` allowed: `03/99`). Write-only.",
        json_schema_extra={"example": "03/99"},
    )

    @field_validator("number")
    @classmethod
    def _valid_number(cls, value: SecretStr) -> SecretStr:
        return _clean_number(value)

    @field_validator("expire")
    @classmethod
    def _valid_expire(cls, value: SecretStr) -> SecretStr:
        return _clean_expire(value)


__all__ = [
    "CardCreateIn",
    "CardIn",
    "CardOut",
]
