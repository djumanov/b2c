"""Request and response shapes for ``/admin/integrations/*`` (API.md §29).

Secrets go **in** in full and come **out** masked, so the two directions never
share a model. ``CredentialOut`` has no field that could carry the real value —
that is the point of it being a separate class.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from app.modules.integrations.models import DEFAULT_BASE_URL, TlsMode
from app.providers.payments.base import PaymentProviderCode
from app.providers.social.base import SocialProviderCode

Label = Annotated[str, Field(min_length=1, max_length=64)]
GtsPassword = Annotated[str, Field(min_length=1, max_length=128)]


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


class ProviderFieldOut(BaseModel):
    """One setting the provider's adapter reads — what the panel's form shows."""

    key: str = Field(description="The key to send inside `credentials`.")
    kind: Literal["text", "secret", "int", "choice"] = Field(
        description=(
            "`secret` comes back masked; `int` must be a whole number; "
            "`choice` must be one of `choices`; `text` is free."
        )
    )
    required: bool = Field(
        description="Must be set before the provider can be switched on."
    )
    choices: list[str] = Field(description="The allowed values of a `choice` field.")
    default: str | None = Field(description="What the adapter assumes when unset.")


class PaymentProviderOut(BaseModel):
    """One provider as the panel sees it. Secret ``credentials`` are masked.

    ``fields`` is empty for a provider this release has no adapter for: the
    panel then shows its free key/value editor, and every value comes back
    masked because nothing says which ones are secret.
    """

    code: PaymentProviderCode = Field(description="`payme`, `click`, or `demo`.")
    enabled: bool = Field(
        description=(
            "Whether the customer may choose this method by `method` when "
            "paying. Several providers may be enabled at once."
        )
    )
    title: str = Field(description="How the site names the method on its buttons.")
    logo_id: uuid.UUID | None = Field(description="The uploaded logo, if any.")
    logo_url: str | None = Field(description="Where the logo is served from.")
    sort_order: int = Field(description="Button order on the site, ascending.")
    credentials: dict[str, str] = Field(
        description=(
            "The stored settings by key. `secret` fields (see `fields`) come "
            "back masked — `••••••9f` — so the panel can show which are set; "
            "the others as written. Empty when nothing is stored."
        )
    )
    fields: list[ProviderFieldOut] = Field(
        description=(
            "The settings this provider's adapter reads — render a form from "
            "it. Empty for a provider this release has no adapter for, which "
            "then accepts any key and masks every value."
        )
    )
    last_tested_at: datetime | None = Field(
        description="When `POST …/{code}/test/` last ran."
    )
    last_test_ok: bool | None = Field(description="What it found.")
    last_test_error: str | None = Field(description="The provider's words when not ok.")
    created_at: datetime
    updated_at: datetime


class PaymentProviderIn(BaseModel):
    """``PATCH`` is partial, and ``credentials`` **merges** into what is stored.

    Replacing the whole object would mean a panel that edits one key has to
    resend the others — which it cannot do, because it only ever received them
    masked.
    """

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "credentials": {
                        "merchant_id": "5e730e8e0b852a417aa49ceb",
                        "key": "<cashbox key>",
                        "environment": "test",
                        "account_field": "order_id",
                    }
                },
                {"enabled": True},
                {"credentials": {"fiscal_title": None}},
            ]
        }
    }

    enabled: bool | None = Field(
        default=None,
        description=(
            "`true` switches this provider on — several may be on at once, "
            "and the customer picks one by `method` when paying. Needs the "
            "required keys stored first; a provider this release ships no "
            "adapter for cannot be switched on. `false` switches it off."
        ),
    )
    title: ProviderTitle | None = Field(
        default=None, description="The button label on the site."
    )
    logo_id: uuid.UUID | None = Field(
        default=None, description="An upload id from `POST /admin/uploads/`."
    )
    sort_order: Annotated[int, Field(ge=0, le=9999)] | None = Field(
        default=None, description="Button order on the site."
    )
    credentials: Credentials | None = Field(
        default=None,
        description=(
            "Keys **merge** into what is stored: send one to change one; "
            "`null` deletes a key; a masked value echoed back is ignored. For "
            "a provider with `fields`, an unknown key, a `choice` outside its "
            "choices or a non-numeric `int` is a `422`."
        ),
    )


class PaymentTestOut(BaseModel):
    """What the provider said to the stored settings (the ``SmtpTestOut``
    shape): ``ok: false`` with the reason is an answer, not an error."""

    ok: bool = Field(description="Whether the provider accepted the stored settings.")
    detail: str | None = Field(
        description="The provider's words when not ok — shown to the operator."
    )
    tested_at: datetime = Field(description="When this test ran.")


# --- social sign-in (API.md §29) ----------------------------------------------------


class SocialCredentialOut(BaseModel):
    """``client_id`` in full, ``client_secret`` masked.

    Not an oversight: the client id is handed to every browser that renders the
    sign-in button, so hiding it in the panel would cost the operator the one
    value they need to compare against the Google console.
    """

    provider: SocialProviderCode
    enabled: bool
    client_id: str | None
    client_secret: str | None
    created_at: datetime
    updated_at: datetime


class SocialCredentialIn(BaseModel):
    enabled: bool | None = None
    #: An empty string clears it — the same convention as the SMTP username.
    client_id: Annotated[str | None, Field(max_length=255)] = None
    client_secret: Annotated[str | None, Field(max_length=255)] = None


__all__ = [
    "CredentialCreateIn",
    "CredentialOut",
    "CredentialUpdateIn",
    "PaymentProviderIn",
    "PaymentProviderOut",
    "PaymentTestOut",
    "ProviderFieldOut",
    "SmtpIn",
    "SmtpOut",
    "SmtpTestIn",
    "SmtpTestOut",
    "SocialCredentialIn",
    "SocialCredentialOut",
]
