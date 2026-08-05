"""The orphan sweep — API.md §11, ARCHITECTURE.md §12.

A file is uploaded before anything references it, so every abandoned form
leaves bytes behind. Hourly is often enough: the grace period is 24 hours, so
the exact minute a file goes does not matter, and an hourly pass keeps each
one small.
"""

import asyncio
from typing import Any

from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.modules.uploads import service
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


async def _sweep() -> int:
    async with get_sessionmaker()() as session:
        return await service.sweep_orphans(session)


@celery_app.task(name="app.tasks.uploads.sweep_unlinked_uploads")
def sweep_unlinked_uploads() -> dict[str, Any]:
    """Delete files nothing ever attached itself to."""
    swept = asyncio.run(_sweep())
    return {"swept": swept}


__all__ = ["sweep_unlinked_uploads"]
