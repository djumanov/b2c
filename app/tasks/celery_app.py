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
    include=["app.tasks.heartbeat", "app.tasks.uploads"],
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
#   * sync open orders from GTS            -> modules/orders
#   * sweep expired idempotency keys       -> api/idempotency   (Redis TTL does
#                                             most of this already)
#   * refresh the static catalogues        -> modules/catalog
#   * refresh currency rates               -> modules/catalog
celery_app.conf.beat_schedule = {
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
