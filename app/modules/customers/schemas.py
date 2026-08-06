"""Request and response shapes for the customer auth endpoints — API.md §18.

Handlers return these directly; the envelope is added by the route class.

``Password`` repeats the staff module's floor rather than importing it. The two
are the same number today and mean different things — one is a client's own
staff policy, the other is what the public may pick — and a shared constant
would make changing either look like changing both.
"""

from typing import Annotated, Final

from pydantic import BaseModel, EmailStr, Field

#: A floor, not a policy (PROJECT.md §7): below this the argon2 hash stops
#: being the thing that protects the account.
MIN_PASSWORD_LENGTH: Final = 8

#: API.md §18. Six digits, as a string — leading zeros are part of the code.
OTP_LENGTH: Final = 6

Password = Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)]
PersonName = Annotated[str, Field(min_length=1, max_length=120)]
OtpCode = Annotated[str, Field(min_length=OTP_LENGTH, max_length=OTP_LENGTH)]


class RegisterIn(BaseModel):
    """``last_name`` and ``phone`` are optional — API.md §18."""

    email: EmailStr
    password: Password
    first_name: PersonName
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


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "OTP_LENGTH",
    "LoginIn",
    "PasswordResetConfirmIn",
    "PasswordResetRequestIn",
    "PasswordResetVerifyIn",
    "RefreshIn",
    "RegisterConfirmIn",
    "RegisterIn",
    "ResendCodeIn",
    "ResetTokenOut",
    "TokenPairOut",
]
