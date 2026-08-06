"""Request and response shapes for ``/admin/integrations/*`` (API.md §29).

Secrets go **in** in full and come **out** masked, so the two directions never
share a model. ``CredentialOut`` has no field that could carry the real value —
that is the point of it being a separate class.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from app.modules.integrations.models import DEFAULT_BASE_URL, TlsMode
from app.providers.payments.base import PaymentProviderCode

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


# --- payment providers (API.md §29) ------------------------------------------------

ProviderTitle = Annotated[str, Field(min_length=1, max_length=64)]

#: Deliberately loose. Which keys a provider wants comes from that provider's
#: documentation and arrives with its adapter (PHASES.md §2.13); a schema here
#: would be a guess the adapters then contradict. The bound is on size, so a
#: caller cannot use this as free storage.
Credentials = Annotated[dict[str, str | None], Field(max_length=20)]


class PaymentProviderOut(BaseModel):
    """One provider as the panel sees it. ``credentials`` values are masked."""

    code: PaymentProviderCode
    enabled: bool
    title: str
    logo_id: uuid.UUID | None
    logo_url: str | None
    sort_order: int
    credentials: dict[str, str]
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_error: str | None
    created_at: datetime
    updated_at: datetime


class PaymentProviderIn(BaseModel):
    """``PATCH`` is partial, and ``credentials`` **merges** into what is stored.

    Replacing the whole object would mean a panel that edits one key has to
    resend the others — which it cannot do, because it only ever received them
    masked.
    """

    enabled: bool | None = None
    title: ProviderTitle | None = None
    logo_id: uuid.UUID | None = None
    sort_order: Annotated[int, Field(ge=0, le=9999)] | None = None
    credentials: Credentials | None = None


__all__ = [
    "CredentialCreateIn",
    "CredentialOut",
    "CredentialUpdateIn",
    "PaymentProviderIn",
    "PaymentProviderOut",
    "SmtpIn",
    "SmtpOut",
    "SmtpTestIn",
    "SmtpTestOut",
]
