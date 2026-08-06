"""What this installation connects to the outside world with — API.md §29.

Three shapes, and each one is the contract's. The GTS agent accounts are a
**list** because one is not always enough, and are addressed by id. The SMTP
server is a **singleton** because an installation sends its mail through one
relay, and has no ``{id}`` at all. The payment providers are a **registry**:
addressed by ``code``, one row per provider the release supports, and the set
is closed because adding a provider means writing an adapter.

An installation reaches GTS with its own agent account (PROJECT.md D1). One
account is not always enough — production and a test environment, or an old
one being replaced — so the accounts are a list, and exactly one of them is
``is_active``. Every outbound GTS call uses that one.

**"Exactly one" is a database guarantee, not a convention.** Two workers can
activate two rows in the same millisecond; a partial unique index over
``is_active`` makes the second one fail rather than leave an installation with
two active accounts and no way to tell which is in use.

**No soft delete.** Almost every table here keeps deleted rows (API.md §8), but
a deleted credential is a client's password held for no reason, and nothing
references it — no foreign key can dangle. Who deleted which id and when stays
in the audit journal, which is the part worth keeping.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import (
    SingletonMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    singleton_check,
)
from app.providers.payments.base import PaymentProviderCode
from app.providers.social.base import SocialProviderCode

#: Where GTS lives unless the client says otherwise (GTS.md §3). Held per row
#: rather than once for the installation, so production and a test environment
#: sit side by side and switching between them is one action.
DEFAULT_BASE_URL = "https://api2.globaltravel.space"


class GtsCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "gts_credentials"

    #: The owner's own name for this account — "Prod agent", "Zaxira".
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text(f"'{DEFAULT_BASE_URL}'")
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    #: AES-GCM ciphertext, never the password itself (PROJECT.md §13).
    password: Mapped[str] = mapped_column(Text, nullable=False)
    #: Which key sealed it, so rotating the encryption key does not mean
    #: retyping every credential (ARCHITECTURE.md §10).
    key_version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: GTS asks for this as an ``agent-uid`` header on some endpoints. It
    #: belongs to the account, so it travels with it rather than sitting in a
    #: setting that would have to be changed in step.
    agent_uid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    __table_args__ = (
        # One row may be active. The predicate is what makes this work: without
        # it the index would allow exactly one *inactive* row too.
        Index(
            "uq_gts_credentials_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index("uq_gts_credentials_label", "label", unique=True),
    )


class TlsMode(StrEnum):
    """How the connection to the relay is secured."""

    #: Plain connection on 587, upgraded with ``STARTTLS``. The common one.
    STARTTLS = "starttls"
    #: TLS from the first byte, on 465.
    SSL = "ssl"
    #: No encryption — only sane for a relay inside the client's own network.
    NONE = "none"


#: VARCHAR + CHECK rather than a native enum, for the same reason as the upload
#: purpose and the staff role: rewriting a CHECK is ordinary DDL, altering a
#: native enum is not, and clients upgrade unattended (PROJECT.md D10).
TLS_MODE_COLUMN = Enum(
    TlsMode,
    name="tls_mode",
    native_enum=False,
    create_constraint=True,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
)


class SmtpSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin, SingletonMixin):
    """The one relay this installation sends through (PROJECT.md D6).

    A singleton, guaranteed in the database rather than by convention — "which
    row is the real one" must never be a question anybody can ask. Deliberately
    not ``Entity``: ``SoftDeleteMixin`` and ``SingletonMixin`` disagree, because
    a soft-deleted row keeps the single slot reserved forever.

    The row is created on first read, not by a data migration, so the defaults
    live next to the fields they default (the ``settings`` module does the same).
    """

    __tablename__ = "smtp_settings"
    __table_args__ = (singleton_check(),)

    #: Off until somebody fills it in. While it is off the application keeps
    #: writing mail to the log instead of sending it, which is also what a
    #: fresh installation does.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("587")
    )
    tls: Mapped[TlsMode] = mapped_column(
        TLS_MODE_COLUMN, nullable=False, server_default=text("'starttls'")
    )

    #: Both optional: a relay inside the client's own network often takes mail
    #: from its own hosts without asking who they are.
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: AES-GCM ciphertext, never the password itself (PROJECT.md §13).
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    from_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: What the last ``test/`` found. Stored rather than recomputed: the panel
    #: shows it on a plain ``GET``, which must not send anybody an email.
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


#: Same idiom as ``TLS_MODE_COLUMN``: VARCHAR + CHECK rather than a native
#: enum, because rewriting a CHECK is ordinary DDL and clients upgrade
#: unattended (PROJECT.md D10). The members come from the payments port, which
#: is where a provider is really defined.
PROVIDER_CODE_COLUMN = Enum(
    PaymentProviderCode,
    name="payment_provider_code",
    native_enum=False,
    create_constraint=True,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
)


class PaymentProvider(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One payment provider's settings — API.md §29.

    Keyed by ``code``, and the row exists for every code the release knows
    whether the client uses it or not: ``repository.payment_providers`` creates
    the missing ones on first read. An empty list would be the wrong empty
    state, because there would be nothing for the panel to switch on.

    Not an ``Entity``. Deleting a provider is not an operation — the set is
    fixed by which adapters exist — so there is nothing to soft-delete.
    """

    __tablename__ = "payment_providers"

    code: Mapped[PaymentProviderCode] = mapped_column(
        PROVIDER_CODE_COLUMN, nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    #: How the site presents the method (API.md §17). Held here rather than in
    #: code because it is a client's wording and their chosen artwork.
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The uploaded logo, by id and not by foreign key — ``uploads`` is reached
    #: through its service, never by joining its table (ARCHITECTURE.md §4).
    logo_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    #: The contract's *tartib*: what order the buttons appear in on the site.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    #: AES-GCM ciphertext of a **JSON object**, not of one secret. Payme and
    #: Click ask for different keys and the list comes from each provider's own
    #: documentation, so the shape is the adapter's business (phase 2) and this
    #: column stays opaque to everything but ``service``.
    credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: What the last ``test/`` found. Null until the probe lands with the
    #: adapter in phase 2 (PHASES.md §2.13); the columns exist now because the
    #: contract's GET returns them and three nullable columns are not worth a
    #: migration of their own later.
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


#: Same VARCHAR + CHECK idiom, for the same reason.
SOCIAL_PROVIDER_COLUMN = Enum(
    SocialProviderCode,
    name="social_provider_code",
    native_enum=False,
    create_constraint=True,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
)


class SocialCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One social sign-in provider's OAuth client — API.md §29.

    A registry like the payment providers rather than a Google-shaped
    singleton: PROJECT.md D5 requires Sign in with Apple once Google is offered
    on iOS, and ARCHITECTURE.md §2 says adding it must be a new row rather than
    a new branch in the sign-in flow.
    """

    __tablename__ = "social_credentials"

    provider: Mapped[SocialProviderCode] = mapped_column(
        SOCIAL_PROVIDER_COLUMN, nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    #: Not a secret, and stored in the clear on purpose: it is published to
    #: every browser that renders the sign-in button, so encrypting it here
    #: would protect nothing while making the panel unable to show it.
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: AES-GCM ciphertext. Unused by the ID-token flow, which verifies against
    #: Google's public keys — kept because the authorization-code flow needs it
    #: and an installation configures both halves at once.
    client_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)


__all__ = [
    "DEFAULT_BASE_URL",
    "PROVIDER_CODE_COLUMN",
    "SOCIAL_PROVIDER_COLUMN",
    "TLS_MODE_COLUMN",
    "GtsCredential",
    "PaymentProvider",
    "SmtpSettings",
    "SocialCredential",
    "TlsMode",
]
