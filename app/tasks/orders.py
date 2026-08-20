"""The order sweep — what settles whatever a lost answer left open.

GTS never calls us and a provider's answer can get lost on the way back, so
anything that has to know what happened out there is **polling**
(ARCHITECTURE.md §12). One task, every ``RECONCILE_EVERY_SECONDS``, asks the
questions the service knows how to settle: which charges have been in flight
too long, which unpaid holds GTS has let go — and, once the ticketing step
lands, which tickets GTS has finished issuing.

Each question runs in its own session and each row in its own transaction
under ``SKIP LOCKED``, so two overlapping runs — a slow GTS, a worker that
died with its task and had it redelivered — cost at most a redundant read,
never a double move. ``dispose_engine``/``close_redis`` in ``finally`` is
load-bearing: a prefork child runs ``asyncio.run`` once per task, and
connections left behind are bound to a loop that no longer exists.
"""

import asyncio
from typing import Any, Final

from app.core.logging import get_logger
from app.db.redis import close_redis
from app.db.session import dispose_engine, get_sessionmaker
from app.modules.orders import service
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

#: Often enough that a customer watching the screen is not kept waiting long,
#: rare enough that GTS sees one read per waiting order per half minute.
RECONCILE_EVERY_SECONDS: Final = 30.0


async def _reconcile() -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        async with get_sessionmaker()() as session:
            counts["payments_settled"] = await service.settle_stale_confirmations(
                session
            )
        async with get_sessionmaker()() as session:
            counts["orders_expired"] = await service.expire_unpaid(session)
    finally:
        await dispose_engine()
        await close_redis()
    return counts


@celery_app.task(name="app.tasks.orders.reconcile_orders")
def reconcile_orders() -> dict[str, Any]:
    """Settle lost provider answers and release holds GTS has let go."""
    counts = asyncio.run(_reconcile())
    logger.info("orders_reconciled", **counts)
    return counts


__all__ = ["RECONCILE_EVERY_SECONDS", "reconcile_orders"]
