"""The first-boot SMTP seed called by ``docker/bootstrap.py``.

Apart from ``service`` for the same reason as ``staff/bootstrap.py``: it runs
**outside a request**, so there is no dependency to hand it a session and it
opens one itself. Whether anything should happen at all is decided in the
service, next to the rest of the SMTP rules.
"""

from app.db.session import get_sessionmaker
from app.modules.integrations import service


async def configure_smtp() -> bool:
    """``True`` when the relay was seeded, ``False`` when it was left alone.

    Safe on every container start: an installation whose SMTP row already has
    a host is never touched again.
    """
    async with get_sessionmaker()() as session:
        return await service.bootstrap_smtp(session) is not None


__all__ = ["configure_smtp"]
