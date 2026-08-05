"""The first-boot entry point called by ``docker/bootstrap.py``.

Kept apart from ``service`` because it runs **outside a request**: there is no
dependency to hand it a session, so it opens one itself. The decision of
whether anything should happen at all lives in the service, next to the rest of
the staff rules.
"""

from app.db.session import dispose_engine, get_sessionmaker
from app.modules.staff import service


async def create_first_owner() -> bool:
    """``True`` when an owner was created, ``False`` when one already existed.

    Safe to run on every container start (PROJECT.md §14) — an installation
    that already has staff is left alone.
    """
    async with get_sessionmaker()() as session:
        created = await service.create_first_owner(session)
    # The bootstrap process exits straight after this; leaving the pool open
    # would have asyncpg complain about a loop closing under live connections.
    await dispose_engine()
    return created is not None


__all__ = ["create_first_owner"]
