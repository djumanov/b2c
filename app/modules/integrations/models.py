"""The GTS agent accounts this installation may sign in with — API.md §29.

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

from sqlalchemy import Boolean, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin

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


__all__ = ["DEFAULT_BASE_URL", "GtsCredential"]
