"""Request and response shapes for the customer surface — API.md §18 and §19.

Handlers return these directly; the envelope is added by the route class.

``Password`` repeats the staff module's floor rather than importing it. The two
are the same number today and mean different things — one is a client's own
staff policy, the other is what the public may pick — and a shared constant
would make changing either look like changing both.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Final

from pydantic import BaseModel, EmailStr, Field

#: A floor, not a policy (PROJECT.md §7): below this the argon2 hash stops
#: being the thing that protects the account.
MIN_PASSWORD_LENGTH: Final = 8

#: API.md §18. Four digits, as a string — leading zeros are part of the code,
#: which is why it never travels as a number.
OTP_LENGTH: Final = 4

Password = Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)]
PersonName = Annotated[str, Field(min_length=1, max_length=120)]
OtpCode = Annotated[str, Field(min_length=OTP_LENGTH, max_length=OTP_LENGTH)]


class RegisterIn(BaseModel):
    """Only the address and the password are required — API.md §18.

    Everything else is a detail of the person, and the account is the address
    plus what proves it. A name the customer did not type is asked for again on
    the profile screen, which is the place that owns it.
    """

    email: EmailStr
    password: Password
    first_name: PersonName | None = None
    last_name: PersonName | None = None
    phone: Annotated[str | None, Field(max_length=32)] = None


class RegisterConfirmIn(BaseModel):
    email: EmailStr
    code: OtpCode


class ResendCodeIn(BaseModel):
    email: EmailStr


class LoginIn(BaseModel):
    """``login`` is an email in the first release — phone + SMS is D6 work."""

    login: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class SocialLoginIn(BaseModel):
    """The identity token the provider handed the browser (API.md §18)."""

    id_token: Annotated[str, Field(min_length=1, max_length=4096)]


class TokenPairOut(BaseModel):
    """API.md §18. ``expires_in`` describes the **access** token, in seconds."""

    access_token: str
    refresh_token: str
    expires_in: int


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetVerifyIn(BaseModel):
    email: EmailStr
    code: OtpCode


class ResetTokenOut(BaseModel):
    """What ``password/reset/verify/`` hands back — single use, short-lived."""

    reset_token: str
    expires_in: int


class PasswordResetConfirmIn(BaseModel):
    reset_token: str
    new_password: Password


# --- profile (API.md §19) -------------------------------------------------------


class ProfileOut(BaseModel):
    """What the customer sees of their own row.

    ``is_blocked`` is deliberately absent: a blocked account cannot reach this
    endpoint at all (``service.get_active`` raises first), so the field could
    only ever read ``false``.

    No ``from_attributes``: ``avatar_url`` is not a column, so the service
    builds this one field by field.
    """

    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
    phone: str | None
    birth_date: date | None
    avatar_id: uuid.UUID | None
    avatar_url: str | None
    created_at: datetime


class ProfileUpdateIn(BaseModel):
    """``PATCH`` is partial — only the fields present change (API.md §8).

    ``extra="forbid"`` so that ``email`` comes back as a 422 naming the field.
    Ignoring it would be worse than refusing it: the client would render an
    address as changed that never changed, and the address is what the OTP
    proved and what password reset trusts (API.md §19).
    """

    model_config = {"extra": "forbid"}

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    phone: Annotated[str | None, Field(max_length=32)] = None
    birth_date: date | None = None


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: Password


class AccountDeleteIn(BaseModel):
    """The password, again. See API.md §19 — this is the one irreversible
    thing a customer token can do, so holding the token is not enough."""

    password: str


# --- saved passengers (API.md §19) ------------------------------------------------


class PassengerOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    first_name: str
    last_name: str
    birth_date: date | None
    document_type: str | None
    document_number: str | None
    created_at: datetime
    updated_at: datetime


class PassengerCreateIn(BaseModel):
    first_name: PersonName
    last_name: PersonName
    birth_date: date | None = None
    #: Free text: the catalogue of document types is GTS's and API.md §26 has
    #: no endpoint serving it yet.
    document_type: Annotated[str | None, Field(max_length=32)] = None
    document_number: Annotated[str | None, Field(max_length=64)] = None


class PassengerUpdateIn(BaseModel):
    model_config = {"extra": "forbid"}

    first_name: PersonName | None = None
    last_name: PersonName | None = None
    birth_date: date | None = None
    document_type: Annotated[str | None, Field(max_length=32)] = None
    document_number: Annotated[str | None, Field(max_length=64)] = None


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "OTP_LENGTH",
    "AccountDeleteIn",
    "LoginIn",
    "PassengerCreateIn",
    "PassengerOut",
    "PassengerUpdateIn",
    "PasswordChangeIn",
    "ProfileOut",
    "ProfileUpdateIn",
    "PasswordResetConfirmIn",
    "PasswordResetRequestIn",
    "PasswordResetVerifyIn",
    "RefreshIn",
    "RegisterConfirmIn",
    "RegisterIn",
    "ResendCodeIn",
    "SocialLoginIn",
    "ResetTokenOut",
    "TokenPairOut",
]
