"""Customers and their sessions — the ``aud: public`` identity (ARCHITECTURE.md §5).

The shape mirrors ``staff`` deliberately: an entity plus a session log, with the
same soft-delete and rotation rules. Two audiences that behave differently under
the same threat would be two sets of rules to remember, and API.md §4 describes
one mechanism with two sets of lifetimes.

Four tables:

``customers`` is the entity, soft-deleted like everything else (API.md §8), with
its email unique **among live rows only** — an account that was deleted must not
lock its address away forever.

``passengers`` is the saved-traveller list (API.md §19), also an entity. Note it
is **not** the order's passenger list: ARCHITECTURE.md §10 puts those under
*commerce* as a separate table, because one is a template the customer keeps and
the other is what was actually flown on.

``customer_refresh_tokens`` is a session log, not an entity: a session ends by
being revoked, and ``revoked_at`` says exactly when. Redis carries the ``jti``
denylist every request consults; this table is what survives a flush.

``email_otps`` is the register/reset codes. It could have lived in Redis, where
the reset *token* does — but a code carries an attempt counter and a resend
cooldown, and both are things that must not reset when Redis does. Losing them
turns a six-digit code into something worth guessing.

The personal columns (``first_name`` … ``birth_date``) are created here although
API.md §19 is what exposes them. They are the personal data PROJECT.md §13
enumerates and they belong to this one account row; splitting them into a later
``ALTER`` would buy nothing.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Entity
from app.db.mixins import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    soft_delete_unique_index,
)


class OtpPurpose(StrEnum):
    """What a code was issued for. A register code cannot reset a password."""

    REGISTER = "register"
    PASSWORD_RESET = "password_reset"


#: VARCHAR + CHECK, for the reason ``staff.ROLE_COLUMN`` gives: altering a
#: native enum behaves differently across server versions and clients run
#: migrations unattended (PROJECT.md D10).
OTP_PURPOSE_COLUMN = Enum(
    OtpPurpose,
    name="otp_purpose",
    native_enum=False,
    create_constraint=True,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
)


class Customer(Entity):
    """An end user. No role — API.md §4 gives ``role`` to staff tokens only."""

    __tablename__ = "customers"
    __table_args__ = (soft_delete_unique_index("customers", "email"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The uploaded avatar, by id and **not** by foreign key. ``uploads`` is a
    #: module, reached through its service; a foreign key into its table is the
    #: database's version of importing its ``models.py`` (ARCHITECTURE.md §4).
    #: ``settings.branding`` holds ``logo_id`` the same way.
    avatar_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    #: NULL until the register code is confirmed. Until then the row exists but
    #: grants nothing — see ``service.get_active``.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_active(self) -> bool:
        """May this account be used at all?

        Unverified counts as no, alongside blocked and deleted. An address
        nobody has proved they own must not become a working account just
        because a password was typed next to it.
        """
        return self.is_verified and not self.is_blocked and not self.is_deleted


class Passenger(Entity):
    """Somebody this customer books for — themselves included (API.md §19).

    The field set comes from PROJECT.md §13's inventory of stored personal
    data and stops there: name, birth date, document type and number. Gender,
    citizenship and document expiry are **not** in that list, so they are not
    here either — a declared PII inventory is not something a module extends
    on its way past. Booking may well want them in phase 2; that starts with
    an edit to §13.

    An ``Entity``, so ``DELETE`` is the soft delete API.md §8 promises. Nothing
    unique about it: the same person legitimately appears twice, once with an
    expired passport and once with the new one.
    """

    __tablename__ = "passengers"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Free text, not an enum. The catalogue of document types lives on the GTS
    #: side (GTS.md §9) and API.md §26 has no endpoint that serves it, so a
    #: local enum would be a guess — and a CHECK constraint is the expensive
    #: kind of guess to be wrong about.
    document_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(64), nullable=True)


class CustomerRefreshToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One issued refresh token. Rotation revokes the row it replaced."""

    __tablename__ = "customer_refresh_tokens"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: ``uuid4().hex`` from ``core.security.create_token`` — 32 characters.
    jti: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Context for a future "your devices" screen. Never used for authorisation.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


class EmailOtp(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One six-digit code sent to a customer's address (API.md §18)."""

    __tablename__ = "email_otps"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[OtpPurpose] = mapped_column(OTP_PURPOSE_COLUMN, nullable=False)
    #: SHA-256 of the code. Six digits is a small space, but a database dump
    #: should still not be a list of codes that work right now.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Wrong guesses so far. The ceiling is what makes six digits enough.
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    #: When the code last went out — the resend cooldown is measured from here.
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None


__all__ = [
    "OTP_PURPOSE_COLUMN",
    "Customer",
    "CustomerRefreshToken",
    "EmailOtp",
    "OtpPurpose",
    "Passenger",
]
