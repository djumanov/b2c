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

from pydantic import BaseModel, SecretStr, field_validator

#: A card number is 13–19 digits (ISO/IEC 7812). Spaces and dashes are stripped
#: before this is applied — people paste what is printed on the card.
_DIGITS = re.compile(r"\d{13,19}$")
_SEPARATORS = re.compile(r"[\s-]")
#: ``MMYY``, which is what the payment forms use.
_EXPIRE = re.compile(r"(0[1-9]|1[0-2])\d{2}$")


class CardOut(BaseModel):
    """A saved card as the customer sees it. Never carries the number."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    masked_pan: str
    last4: str
    brand: str | None
    expiry_month: int
    expiry_year: int
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CardCreateIn(BaseModel):
    #: ``hide_input_in_errors`` is not a nicety here. Pydantic records the raw
    #: input on every ``ValidationError`` — ``input_value='8600…'`` — and it does
    #: so *before* the value becomes a ``SecretStr``, so the secret type does not
    #: cover it. The HTTP response only carries ``msg`` and would not leak it,
    #: but anything that logs the exception or renders its ``str()`` would. A
    #: regression test pins this.
    model_config = {"extra": "forbid", "hide_input_in_errors": True}

    number: SecretStr
    expire: SecretStr

    @field_validator("number")
    @classmethod
    def _valid_number(cls, value: SecretStr) -> SecretStr:
        digits = _SEPARATORS.sub("", value.get_secret_value())
        if not _DIGITS.fullmatch(digits):
            raise ValueError("This does not look like a card number")
        return SecretStr(digits)

    @field_validator("expire")
    @classmethod
    def _valid_expire(cls, value: SecretStr) -> SecretStr:
        expire = _SEPARATORS.sub("", value.get_secret_value()).replace("/", "")
        if not _EXPIRE.fullmatch(expire):
            raise ValueError("Expiry must be MMYY")
        return SecretStr(expire)


__all__ = [
    "CardCreateIn",
    "CardOut",
]
