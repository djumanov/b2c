"""A task that does nothing but prove the worker and beat are alive.

Every real beat entry belongs to a module that does not exist yet. Without one
working entry, "is the worker running?" can only be answered by adding a task
and redeploying — which is exactly when nobody wants to be experimenting.
"""

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.heartbeat.heartbeat")
def heartbeat() -> str:
    logger.info("heartbeat")
    return "ok"


__all__ = ["heartbeat"]
