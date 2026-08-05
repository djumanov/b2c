"""The declarative base every model registers on — Alembic reads its metadata.

The naming convention matters more than it looks. Without it PostgreSQL names
constraints for us, autogenerate invents its own names, and a later migration
that drops a constraint fails on an installation whose name differs. Clients
upgrade on their own schedule and may skip several versions (PROJECT.md D10),
so a migration that only works on a freshly built database is worthless.
"""

from typing import Any, Final

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from app.db.mixins import BaseMixin

NAMING_CONVENTION: Final[dict[str, Any]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Entity(Base, BaseMixin):
    """``Base`` plus the three mixins — what almost every table inherits.

    Its real job is typing. ``db.repository``'s helpers are generic over
    ``Entity``, so they can say ``model.deleted_at.is_(None)`` and have mypy
    check it; a bare ``Base`` bound would need a cast at every call site.

    Tables that deliberately want *less* skip it: the audit journal is
    append-only, so it takes ``UUIDPrimaryKeyMixin`` and a ``created_at`` and
    has no business owning a ``deleted_at`` nobody is allowed to set.
    """

    __abstract__ = True


__all__ = ["NAMING_CONVENTION", "Base", "Entity"]
