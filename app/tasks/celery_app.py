"""Celery: ``uv run celery -A app.tasks.celery_app:celery_app worker|beat``.

Redis is both broker and result backend — the client already runs it, and
adding a second piece of infrastructure multiplies their support burden by the
number of installations (ARCHITECTURE.md §3).

Order synchronisation is deliberately **polling**. GTS.md §12 documents only
the B2C→GTS direction, so we cannot rely on GTS calling us. If it turns out
that it can, a webhook becomes an optimisation rather than a rewrite
(ARCHITECTURE.md §12).
"""

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def _configure_worker_logging(**_kwargs: Any) -> None:
    """Log in the worker exactly as the API does.

    Celery hijacks the root logger and leaves third-party loggers at INFO,
    including httpx — which prints every request URL, and upstream URLs can
    carry credentials. Connecting to ``setup_logging`` takes logging back.
    """
    configure_logging()


setup_logging.connect(_configure_worker_logging)

celery_app = Celery(
    "b2c",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.heartbeat", "app.tasks.orders", "app.tasks.uploads"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # A task that dies with its worker must come back, not vanish: the saga
    # depends on every step eventually running (ARCHITECTURE.md §8).
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=24 * 60 * 60,
)

# The schedule from ARCHITECTURE.md §12. Each entry is added together with the
# module that owns its task — a beat entry pointing at a task that does not
# exist yet crashes the beat process on startup:
#
#   * sweep expired idempotency keys       -> api/idempotency   (Redis TTL does
#                                             most of this already)
#   * refresh the static catalogues        -> modules/catalog
#   * refresh currency rates               -> modules/catalog
celery_app.conf.beat_schedule = {
    # The money path's safety net. A step is scheduled in the same transaction
    # as the state change that needs it and the task is also sent directly, so
    # this sweep only ever picks up what the direct send lost — but it has to
    # run often, because what it picks up is a customer waiting for a ticket
    # (order-system/03-design.md §3.7).
    #
    # **No separate queue yet.** The design reserves a ``money`` queue so a
    # long catalogue sync cannot starve ticketing; there is nothing to starve
    # it today, and a routing rule the worker is not told to consume would
    # strand these silently. It arrives with the first competing beat entry.
    "orders-run-due-every-thirty-seconds": {
        "task": "app.tasks.orders.run_due",
        "schedule": 30.0,
    },
    # Holds nobody paid for. Every minute because the window closes on the
    # provider's clock, and a seat released late is a seat somebody else could
    # have bought (order-system/03-design.md §3.7).
    "orders-expire-unpaid-every-minute": {
        "task": "app.tasks.orders.expire_unpaid",
        "schedule": 60.0,
    },
    # Orders GTS answered in words we would not call a reservation, and orders
    # whose booking call never came back. A minute, because until this runs the
    # customer is looking at "tekshirilmoqda" and the seat is unclaimed
    # (order-system/03-design.md §3.9).
    "orders-sync-open-every-minute": {
        "task": "app.tasks.orders.sync_open",
        "schedule": 60.0,
    },
    # Abandoned checkouts and charges that never answered. Five minutes rather
    # than one: nothing here is a customer waiting — a stale attempt is one
    # nobody came back to, and a charge that has not answered in five minutes
    # will not answer in six either (order-system/03-design.md §3.7).
    "orders-sweep-payments-every-five-minutes": {
        "task": "app.tasks.orders.sweep_payments",
        "schedule": 300.0,
    },
    "heartbeat-every-five-minutes": {
        "task": "app.tasks.heartbeat.heartbeat",
        "schedule": 300.0,
    },
    # The grace period is 24 hours (API.md §11), so the exact minute a file
    # goes does not matter; hourly keeps each pass small.
    "sweep-unlinked-uploads-hourly": {
        "task": "app.tasks.uploads.sweep_unlinked_uploads",
        "schedule": 3600.0,
    },
}


__all__ = ["celery_app"]
