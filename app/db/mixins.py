"""Column mixins every table shares: UUID key, timestamps, soft delete.

Deleting is soft by default (API.md §8): ``deleted_at`` is stamped and the row
stays. Financial and audit history has to survive, and an operator undoing a
mistake is a support call rather than a restore from backup.

That creates one problem worth naming. A plain ``UNIQUE(slug)`` would keep a
deleted row's slug reserved forever, so a client who deletes a blog post can
never reuse its URL. ``soft_delete_unique_index`` builds the unique index with
``WHERE deleted_at IS NULL`` instead, which reserves the value only while the
row is live (ARCHITECTURE.md §10).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware now. Naive datetimes are never stored."""
    return datetime.now(UTC)


class UUIDPrimaryKeyMixin:
    """UUID primary keys — ids are handed to clients and appear in URLs.

    Generated application-side so a row's id is known before the insert, which
    the saga needs when it writes an order and its outbox entry in one
    transaction (ARCHITECTURE.md §8).
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    """``created_at`` / ``updated_at``, both UTC and set by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """``deleted_at`` — NULL means live (API.md §8)."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = utcnow()

    def restore(self) -> None:
        self.deleted_at = None


class BaseMixin(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """The three together — what almost every table wants."""


def soft_delete_unique_index(table_name: str, *columns: str) -> Index:
    """Unique across live rows only, so a deleted value can be reused.

    __table_args__ = (soft_delete_unique_index("blogs", "slug"),)
    """
    name = f"uq_{table_name}_{'_'.join(columns)}_live"
    return Index(
        name,
        *columns,
        unique=True,
        postgresql_where=text("deleted_at IS NULL"),
    )


__all__ = [
    "BaseMixin",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "soft_delete_unique_index",
    "utcnow",
]
