"""Staff and their sessions — the ``aud: admin`` identity (ARCHITECTURE.md §5).

Two tables, and the reason they are two:

``staff`` is a business entity and is soft-deleted like everything else
(API.md §8). Its email is unique **among live rows only**, so an employee who
leaves and comes back can have their address again — that is what
``soft_delete_unique_index`` is for.

``staff_refresh_tokens`` is not an entity, it is a session log. It has no
``deleted_at``: a session ends by being revoked, and ``revoked_at`` says so
precisely. Redis already carries the ``jti`` denylist that every request
checks (``core/security.py``); this table is what survives a Redis flush, and
what lets a future "your sessions" screen list them.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.roles import Role
from app.db.base import Base, Entity
from app.db.mixins import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    soft_delete_unique_index,
)

#: VARCHAR + CHECK rather than a native PostgreSQL enum. The two roles are
#: fixed in code (PROJECT.md §9), but altering a native enum is a migration
#: that behaves differently across server versions, and clients run those
#: unattended (D10); rewriting a CHECK is ordinary, portable DDL.
#:
#: ``create_constraint=True`` because SQLAlchemy leaves it off by default — the
#: column would otherwise be a bare ``VARCHAR(16)`` that happily stores
#: ``superadmin``. ``values_callable`` because the default persists the *member
#: names*, ``OWNER``/``ADMIN``, and the contract says ``owner``/``admin``.
ROLE_COLUMN = Enum(
    Role,
    name="staff_role",
    native_enum=False,
    create_constraint=True,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
)


class Staff(Entity):
    """A panel user. Exactly one of ``owner`` or ``admin`` (API.md §5)."""

    __tablename__ = "staff"
    __table_args__ = (soft_delete_unique_index("staff", "email"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[Role] = mapped_column(ROLE_COLUMN, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_active(self) -> bool:
        """May this account be used at all? Blocked and deleted both mean no."""
        return not self.is_blocked and not self.is_deleted


class StaffRefreshToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One issued refresh token. Rotation revokes the row it replaced."""

    __tablename__ = "staff_refresh_tokens"

    staff_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
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
    #: Context for the panel's session list. Never used for authorisation.
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None


__all__ = ["ROLE_COLUMN", "Staff", "StaffRefreshToken"]
