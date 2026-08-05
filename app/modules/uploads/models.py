"""Uploaded files — API.md §11, ARCHITECTURE.md §5.

A file is uploaded on its own and attached to something afterwards, so between
those two moments there is a row nobody references. ``linked_at`` is what tells
the two apart: NULL means "uploaded, never attached", and a beat task sweeps
those after 24 hours. Without it, every abandoned form leaves bytes on the
client's disk forever.

``storage_key`` is the adapter's handle, not a URL. The URL is derived when the
file is read, because the same row has to keep working if the client ever moves
from a local disk to a bucket (ARCHITECTURE.md §12).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity
from app.providers.storage.base import UploadPurpose

#: VARCHAR + CHECK, for the same reason as the staff role: rewriting a CHECK is
#: ordinary DDL, altering a native enum is not, and clients upgrade unattended
#: (PROJECT.md D10).
PURPOSE_COLUMN = Enum(
    UploadPurpose,
    name="upload_purpose",
    native_enum=False,
    create_constraint=True,
    length=24,
    values_callable=lambda enum: [member.value for member in enum],
)


class Upload(Entity):
    __tablename__ = "uploads"

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[UploadPurpose] = mapped_column(PURPOSE_COLUMN, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    #: Which employee uploaded it. A plain value, not a foreign key — the same
    #: reason the audit journal keeps its actor that way.
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    #: NULL until something references this file. Set by the module that
    #: attaches it, through ``uploads.service.link``.
    linked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    @property
    def is_linked(self) -> bool:
        return self.linked_at is not None


__all__ = ["PURPOSE_COLUMN", "Upload"]
