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

NAMING_CONVENTION: Final[dict[str, Any]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


__all__ = ["NAMING_CONVENTION", "Base"]
