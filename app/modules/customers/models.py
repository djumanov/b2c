"""Customers and their sessions — the ``aud: public`` identity (ARCHITECTURE.md §5).

The shape mirrors ``staff`` deliberately: an entity plus a session log, with the
same soft-delete and rotation rules. Two audiences that behave differently under
the same threat would be two sets of rules to remember, and API.md §4 describes
one mechanism with two sets of lifetimes.

Six tables:

``customers`` is the entity, soft-deleted like everything else (API.md §8), with
its email unique **among live rows only** — an account that was deleted must not
lock its address away forever.

``deletion_reasons`` is the panel-managed list a customer picks from before
deleting the account (API.md §19, §34), and ``deleted_customers`` is the
append-only archive that keeps the pre-scrub personal snapshot together with
the reasons the customer sent (PROJECT.md §13).

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
turns a four-digit code into something worth guessing.

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
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


def _filled(value: str | None) -> bool:
    """Whitespace is not an answer.

    ``PersonName`` only asks for one character, so ``" "`` reaches the column,
    and ``phone`` has no lower bound at all, so ``""`` does too. Neither is
    somebody having filled the field in.
    """
    return bool(value and value.strip())


class Customer(Entity):
    """An end user. No role — API.md §4 gives ``role`` to staff tokens only."""

    __tablename__ = "customers"
    __table_args__ = (soft_delete_unique_index("customers", "email"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Nullable, like every other personal column: registration asks for the
    #: address and the password and nothing else (API.md §18), so a row can
    #: exist before anybody has said who they are.
    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Optional in the column and in ``PATCH``, but ``is_profile_complete``
    #: counts it: here a full name has three parts (PROJECT.md §13).
    middle_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Which of the client's own avatar pictures this customer picked — a code,
    #: not a file (API.md §19). Nothing is uploaded and nothing is served: the
    #: set of pictures ships with the app and the site, so the server stores the
    #: choice and returns it unread.
    #:
    #: Opaque text with no CHECK and no list to validate against, for the reason
    #: §19 gives: a server-side list would make every new picture a backend
    #: deploy, and the two clients need not even offer the same set.
    avatar_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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

    @property
    def is_profile_complete(self) -> bool:
        """Has this customer finished saying who they are? (API.md §19)

        Registration asks for the address and the password and nothing else,
        so a live row with every personal column NULL is normal rather than
        broken. The rule for "finished" lives here instead of in each client:
        the app and the site must not disagree about whether to show the
        fill-in-your-profile prompt, and a disagreement like that is one
        nobody reports.

        No special case for the ``[deleted]`` sentinel ``delete_account``
        writes — it only ever lands on a soft-deleted row, and such a row
        never reaches ``/public/profile/``; ``get_active`` refuses it first.
        """
        return (
            _filled(self.first_name)
            and _filled(self.last_name)
            and _filled(self.middle_name)
            and _filled(self.phone)
            and self.birth_date is not None
        )


class Passenger(Entity):
    """Somebody this customer books for — themselves included (API.md §19).

    The field set comes from PROJECT.md §13's inventory of stored personal
    data and stops there: name, patronymic, birth date, citizenship, document
    type, number and expiry. Gender is **not** in that list, so it is not here
    either — a declared PII inventory is not something a module extends on its
    way past. Booking may well want it in phase 2; that starts with an edit
    to §13.

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
    #: Optional here, unlike on ``Customer``'s completeness rule: a foreign
    #: passport frequently has no patronymic to copy off it.
    middle_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Required. A saved passenger exists to save retyping, and one without a
    #: birth date has to be retyped before it can be booked anyway — so the
    #: gap would surface at booking time rather than at save time (API.md §19).
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Free text for the same reason as ``document_type``: the list of countries
    #: is GTS's, and picking a code here would also mean picking a standard
    #: ("UZ" or "UZB") the contract has not named yet.
    citizenship: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Free text, not an enum. The catalogue of document types lives on the GTS
    #: side (GTS.md §9) and API.md §26 has no endpoint that serves it, so a
    #: local enum would be a guess — and a CHECK constraint is the expensive
    #: kind of guess to be wrong about.
    document_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Optional: not every kind of document carries one.
    document_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class DeletionReason(Entity):
    """One entry in the why-are-you-leaving list (API.md §19, §34).

    ``text`` is the usual ``{lang: value}`` translation object (API.md §7).
    No draft/publish state: hiding a reason is the soft delete, and the archive
    keeps whatever text the customer actually sent, so editing or deleting a
    reason never rewrites history.
    """

    __tablename__ = "deletion_reasons"

    text: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Plain-string default: the ``text`` column above shadows SQLAlchemy's
    #: ``text()`` for the rest of this class body.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class DeletedCustomer(Base, UUIDPrimaryKeyMixin):
    """The pre-scrub snapshot of a customer who deleted the account.

    ``delete_account`` empties the live row (PROJECT.md §13); this row is what
    it looked like the moment before, plus the reasons the customer sent —
    verbatim, in whatever language they read them. Append-only: no timestamps
    mixin, no soft delete, and **no foreign key** — the archive must outlive
    the customer row and never be reachable through a cascade. No API returns
    it.
    """

    __tablename__ = "deleted_customers"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(reasons) = 'array'", name="reasons_is_array"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True
    )
    #: Indexed for the "what do you hold about this address" support question.
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    first_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    middle_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The customer row's ``created_at`` — how long the account lived.
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


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
    """One four-digit code sent to a customer's address (API.md §18)."""

    __tablename__ = "email_otps"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[OtpPurpose] = mapped_column(OTP_PURPOSE_COLUMN, nullable=False)
    #: SHA-256 of the code. Four digits is a tiny space, but a database dump
    #: should still not be a list of codes that work right now.
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Wrong guesses so far. The ceiling is what makes four digits enough.
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
    "DeletedCustomer",
    "DeletionReason",
    "EmailOtp",
    "OtpPurpose",
    "Passenger",
]
