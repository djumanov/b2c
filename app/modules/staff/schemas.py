"""Request and response shapes for the admin auth and team endpoints.

API.md §27 (autentifikatsiya) and §38 (jamoa). Handlers return these directly —
the envelope is added by the route class, never here.
"""

import uuid
from datetime import datetime
from typing import Annotated, Final

from pydantic import BaseModel, EmailStr, Field

from app.core.roles import Role

#: A floor, not a policy. Anything a client could reasonably want different
#: belongs in the database (PROJECT.md §7); "at least eight characters" is not
#: a preference, it is the minimum below which the argon2 hash stops mattering.
MIN_PASSWORD_LENGTH: Final = 8

Password = Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)]
StaffName = Annotated[str, Field(min_length=1, max_length=120)]


# --- authentication ------------------------------------------------------------


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


class StaffMeOut(BaseModel):
    """``GET /admin/auth/me/`` — the panel builds its menu from ``role``."""

    id: uuid.UUID
    name: str
    email: EmailStr
    role: Role


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: Password


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str
    new_password: Password


# --- the team resource (API.md §38) ---------------------------------------------


class StaffOut(BaseModel):
    """One employee. Every object carries ``id``/``created_at``/``updated_at``
    (API.md §8)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    name: str
    role: Role
    is_blocked: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StaffCreateIn(BaseModel):
    email: EmailStr
    name: StaffName
    #: Only ``owner`` or ``admin``; anything else is a 422, because roles are
    #: fixed in code and the panel cannot invent a third (API.md §38).
    role: Role
    password: Password


class StaffUpdateIn(BaseModel):
    """``PATCH`` is partial — only the fields present change (API.md §8)."""

    email: EmailStr | None = None
    name: StaffName | None = None
    role: Role | None = None


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "LoginIn",
    "PasswordChangeIn",
    "PasswordResetConfirmIn",
    "PasswordResetRequestIn",
    "RefreshIn",
    "StaffCreateIn",
    "StaffMeOut",
    "StaffOut",
    "StaffUpdateIn",
    "TokenPairOut",
]
