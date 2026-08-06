"""Request and response shapes for ``/admin/integrations/gts/*`` (API.md §29).

The password goes **in** in full and comes **out** masked, so the two
directions cannot share a model. ``CredentialOut`` has no field that could
carry the real value — that is the point of it being a separate class.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from app.modules.integrations.models import DEFAULT_BASE_URL, TlsMode

Label = Annotated[str, Field(min_length=1, max_length=64)]
GtsPassword = Annotated[str, Field(min_length=1, max_length=128)]
AgentUid = Annotated[str, Field(max_length=64)]


def _clean_base_url(value: str) -> str:
    """Validate it as a URL, then store it without the trailing slash.

    Paths are joined onto this as ``/v1/…``; keeping the slash would produce
    ``…space//v1/…``, which some gateways answer and others do not.
    """
    HttpUrl(value)  # raises for anything that is not a URL
    return value.strip().rstrip("/")


class CredentialCreateIn(BaseModel):
    label: Label
    email: EmailStr
    password: GtsPassword
    base_url: str = DEFAULT_BASE_URL
    agent_uid: AgentUid | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _clean_base_url(value)


class CredentialUpdateIn(BaseModel):
    """``PATCH`` is partial (API.md §8): ``None`` means "leave it alone".

    Which is also why the password cannot be cleared here — an account with no
    password is not a state worth being able to reach. Sending a new one
    replaces the old one; omitting it keeps it.
    """

    label: Label | None = None
    email: EmailStr | None = None
    password: GtsPassword | None = None
    base_url: str | None = None
    agent_uid: AgentUid | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        return None if value is None else _clean_base_url(value)


class CredentialOut(BaseModel):
    """What the panel sees. ``password`` is always masked (API.md §29)."""

    id: uuid.UUID
    label: str
    base_url: str
    email: str
    password: str
    agent_uid: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


# --- SMTP (API.md §29) ------------------------------------------------------------

SmtpHost = Annotated[str, Field(min_length=1, max_length=255)]
SmtpPort = Annotated[int, Field(ge=1, le=65535)]
SmtpUser = Annotated[str, Field(max_length=255)]
SmtpPassword = Annotated[str, Field(min_length=1, max_length=255)]
FromName = Annotated[str, Field(max_length=120)]


class SmtpIn(BaseModel):
    """``PATCH`` is partial (API.md §8): ``None`` means "leave it alone".

    Which is why ``username`` and ``from_name`` are cleared by sending an empty
    string rather than ``null`` — a relay that needs no authentication is a
    real configuration, so it has to be reachable from one that did.
    """

    enabled: bool | None = None
    host: SmtpHost | None = None
    port: SmtpPort | None = None
    tls: TlsMode | None = None
    username: SmtpUser | None = None
    password: SmtpPassword | None = None
    from_address: EmailStr | None = None
    from_name: FromName | None = None


class SmtpOut(BaseModel):
    """``password`` is always masked, like every other secret (API.md §29)."""

    enabled: bool
    host: str | None
    port: int
    tls: TlsMode
    username: str | None
    #: ``None`` when no password is stored at all — which is different from
    #: "stored but hidden", and the panel shows the two differently.
    password: str | None
    from_address: str | None
    from_name: str | None
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_error: str | None


class SmtpTestIn(BaseModel):
    #: Defaults to the address of whoever pressed the button.
    to: EmailStr | None = None


class SmtpTestOut(BaseModel):
    """The outcome of trying, not the outcome of the request.

    A relay that refuses the password answers `200` with ``ok: false``. The
    call did what it was asked; the answer is simply "no" (API.md §29).
    """

    ok: bool
    detail: str | None
    tested_at: datetime


__all__ = [
    "CredentialCreateIn",
    "CredentialOut",
    "CredentialUpdateIn",
    "SmtpIn",
    "SmtpOut",
    "SmtpTestIn",
    "SmtpTestOut",
]
