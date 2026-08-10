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
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.providers.payments.base import CardStatus, PaymentProviderCode

#: A card number is 13–19 digits (ISO/IEC 7812). Spaces and dashes are stripped
#: before this is applied — people paste what is printed on the card.
_DIGITS = re.compile(r"\d{13,19}$")
_SEPARATORS = re.compile(r"[\s-]")
#: ``MMYY``, which is what both providers ask for.
_EXPIRE = re.compile(r"(0[1-9]|1[0-2])\d{2}$")

CardLabel = Annotated[str, Field(min_length=1, max_length=64)]


def _luhn_ok(number: str) -> bool:
    """The check digit every card carries.

    Cheap, and it turns the commonest typo into a ``422`` from us instead of a
    round trip that spends the installation's merchant credentials to be told
    the same thing.
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


class CardOut(BaseModel):
    """A saved card as the customer sees it. Never carries the number."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    provider: PaymentProviderCode
    status: CardStatus
    masked_pan: str
    last4: str
    brand: str | None
    expiry_month: int | None
    expiry_year: int | None
    label: str | None
    is_default: bool
    #: Both are filled only while ``status`` is ``pending_verification``.
    otp_sent_to: str | None = None
    otp_expires_at: datetime | None = None
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

    provider: PaymentProviderCode
    number: SecretStr
    expire: SecretStr
    label: CardLabel | None = None

    @field_validator("number")
    @classmethod
    def _valid_number(cls, value: SecretStr) -> SecretStr:
        digits = _SEPARATORS.sub("", value.get_secret_value())
        if not _DIGITS.fullmatch(digits) or not _luhn_ok(digits):
            # Deliberately does not say which of the two failed: length and
            # check digit are both "this is not a card number" to the person
            # typing it, and naming the check digit only helps somebody
            # generating candidates.
            raise ValueError("This does not look like a card number")
        return SecretStr(digits)

    @field_validator("expire")
    @classmethod
    def _valid_expire(cls, value: SecretStr) -> SecretStr:
        expire = _SEPARATORS.sub("", value.get_secret_value()).replace("/", "")
        if not _EXPIRE.fullmatch(expire):
            raise ValueError("Expiry must be MMYY")
        return SecretStr(expire)


class CardUpdateIn(BaseModel):
    """``PATCH`` takes the label and nothing else — the rest is the provider's."""

    model_config = {"extra": "forbid"}

    label: CardLabel | None = None


class CardVerifyIn(BaseModel):
    #: Same reason as ``CardCreateIn`` — a rejected code should not be echoed
    #: into a log line either.
    model_config = {"extra": "forbid", "hide_input_in_errors": True}

    #: Length is not pinned: the code is the provider's, and both of them have
    #: changed it before. An empty string is still not a code.
    code: Annotated[str, Field(min_length=1, max_length=16)]


__all__ = [
    "CardCreateIn",
    "CardLabel",
    "CardOut",
    "CardUpdateIn",
    "CardVerifyIn",
]
