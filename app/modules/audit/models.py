"""The audit journal — append-only (ARCHITECTURE.md §5, §10).

Deliberately **not** an ``Entity``. Nothing in this module updates or deletes a
row, so the table has no ``updated_at`` and no ``deleted_at``: a journal whose
entries can be edited is not evidence, and a nullable ``deleted_at`` is an
invitation to write the code that sets it.

The actor is stored as **values, not as a reference**: id, email and role are
copied in and there is no foreign key to ``staff``.

Two reasons, and the second is the one that decides it. The journal has to stay
readable years later, after the employee has left and their row is gone — a
reference that can dangle is not evidence. And a foreign key from this table to
another module's table is the database's version of importing that module's
``models.py``, which is the one rule that keeps this a modular monolith rather
than a tangle (CLAUDE.md, ARCHITECTURE.md §4). The audit module records what it
was told; it does not depend on the shape of who told it.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import UUIDPrimaryKeyMixin


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_log"
    __table_args__ = (
        # ARCHITECTURE.md §10 names this one: the panel filters by actor and by
        # resource, and always reads newest first.
        Index(
            "ix_audit_log_actor_resource_created", "actor_id", "resource", "created_at"
        ),
        # The unfiltered list is the common case and only this one serves it.
        Index("ix_audit_log_created_at", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    #: NULL for events with no signed-in actor — a failed login is the reason
    #: this column is nullable. Not a foreign key; see the module docstring.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)

    #: ``staff``, ``settings.branding``, ``auth`` — the dotted resource path.
    resource: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: ``create``/``update``/``delete`` for CRUD, or the verb from the path
    #: (``block``, ``publish``, ``login_failed``).
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )

    method: Mapped[str | None] = mapped_column(String(8), nullable=True)
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    #: Ties the entry to every log line the request produced (API.md §13).
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: What changed, secrets already removed by ``core.logging.redact``.
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


__all__ = ["AuditLog"]
