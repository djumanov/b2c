"""The order sweep — what settles whatever a lost answer left open.

GTS never calls us and a provider's answer can get lost on the way back, so
anything that has to know what happened out there is **polling**
(ARCHITECTURE.md §12). One task, every ``RECONCILE_EVERY_SECONDS``, asks the
questions the service knows how to settle: which charges have been in flight
too long, which unpaid holds GTS has let go — and, once the ticketing step
lands, which tickets GTS has finished issuing.

Each question runs in its own session and each row in its own transaction, and
the writes re-read what they are about to change under the row lock — so two
overlapping runs (a slow GTS, a worker that died with its task and had it
redelivered) cost at most a redundant read, never a double move.

**One question failing must not silence the others.** They are independent —
a charge nobody answered for has nothing to do with a hold GTS has let go —
and they share a process whose most likely failure is common to all of them
(GTS unreachable, no active credential). Left in one ``try``, the first
question to raise would take the rest of the pass with it, and the questions
behind it would simply not be asked until something else went right. So each
runs in its own, reports ``0``, and says so at exception level.

``dispose_engine``/``close_redis`` in ``finally`` is load-bearing: a prefork
child runs ``asyncio.run`` once per task, and connections left behind are
bound to a loop that no longer exists.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.redis import close_redis
from app.db.session import dispose_engine, get_sessionmaker
from app.modules.orders import service
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

#: Often enough that a customer watching the screen is not kept waiting long,
#: rare enough that GTS sees one read per waiting order per half minute.
RECONCILE_EVERY_SECONDS: Final = 30.0

#: The questions, in the order they are asked. Payments first: a charge that
#: settles here is what makes an order ticketable in the pass below it.
PASSES: Final[tuple[tuple[str, Callable[[AsyncSession], Awaitable[int]]], ...]] = (
    ("payments_settled", service.settle_stale_confirmations),
    ("tickets_requested", service.ticket_paid_pending),
    ("tickets_settled", service.recheck_processing),
    ("orders_expired", service.expire_unpaid),
)


async def _reconcile() -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        for name, ask in PASSES:
            try:
                async with get_sessionmaker()() as session:
                    counts[name] = await ask(session)
            except Exception:  # noqa: BLE001 - the other questions still stand
                # ``step``, not ``pass``: the latter is a keyword, and structlog
                # renders the key as it is given.
                logger.exception("sweep_pass_failed", step=name)
                counts[name] = 0
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
