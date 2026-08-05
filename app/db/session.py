"""Async engine, session factory and the request-scoped session dependency.

Everything is built **lazily**, like the Redis client next door: importing this
module must never open a connection. Alembic, the Celery workers and the test
suite all import the application graph, and an engine created at import time
would have them dial PostgreSQL merely by being loaded.

``set_engine`` is the seam the integration suite uses to point the whole
process at a throwaway database, mirroring ``db.redis.set_redis``.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """The process-wide engine. Lazily built, so importing never connects."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def set_engine(engine: AsyncEngine | None) -> None:
    """Swap the engine — used by tests to point at a throwaway database."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: ``session: SessionDep``."""
    async with get_sessionmaker()() as session:
        yield session


#: The annotation to write in handlers and dependencies.
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def ping_database() -> bool:
    """True when the database answers. Used by ``system/health/`` (API.md §39)."""
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


__all__ = [
    "SessionDep",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "ping_database",
    "set_engine",
]
