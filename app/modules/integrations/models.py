"""What this installation connects to the outside world with — API.md §29.

Two things so far. The GTS agent accounts, which are a **list** because one is
not always enough, and the SMTP server, which is a **singleton** because an
installation sends its mail through one relay. The contract already draws that
distinction: GTS is addressed by id, notifications is not.

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

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import (
    SingletonMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    singleton_check,
)

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


__all__ = [
    "DEFAULT_BASE_URL",
    "TLS_MODE_COLUMN",
    "GtsCredential",
    "SmtpSettings",
    "TlsMode",
]
