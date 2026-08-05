"""Async engine, session factory and the request-scoped session dependency."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
)

async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: ``session: AsyncSession = Depends(get_session)``."""
    async with async_session() as session:
        yield session


async def ping_database() -> bool:
    """True when the database answers. Used by ``system/health/`` (API.md §39)."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def dispose_engine() -> None:
    await engine.dispose()


__all__ = [
    "async_session",
    "dispose_engine",
    "engine",
    "get_session",
    "ping_database",
]
