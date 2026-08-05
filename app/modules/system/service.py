"""Health and version reporting."""

import time
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.db.redis import ping_redis
from app.db.session import ping_database
from app.modules.system.schemas import (
    ComponentHealth,
    ComponentStatus,
    HealthOut,
    VersionOut,
)

logger = get_logger(__name__)


async def _probe(name: str, check: Callable[[], Awaitable[bool]]) -> ComponentHealth:
    started = time.perf_counter()
    try:
        await check()
    except Exception as exc:
        # A failing dependency is the answer this endpoint exists to give, so
        # it is reported, not raised.
        logger.warning("health_check_failed", component=name, error=str(exc))
        return ComponentHealth(
            status=ComponentStatus.FAILING,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            detail=str(exc)[:200],
        )
    return ComponentHealth(
        status=ComponentStatus.OK,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


async def health() -> HealthOut:
    components = {
        "database": await _probe("database", ping_database),
        "redis": await _probe("redis", ping_redis),
        # Both are configured from the panel and stored encrypted in the
        # database. Until the integrations module lands there is nothing to
        # probe, and "not configured" is the truthful answer — an installation
        # with no GTS credentials yet is half-installed, not broken.
        "gts": ComponentHealth(
            status=ComponentStatus.NOT_CONFIGURED,
            detail="Configure under /admin/integrations/gts/",
        ),
        "payments": ComponentHealth(
            status=ComponentStatus.NOT_CONFIGURED,
            detail="Configure under /admin/integrations/payments/",
        ),
    }
    overall = (
        ComponentStatus.FAILING
        if any(c.status is ComponentStatus.FAILING for c in components.values())
        else ComponentStatus.OK
    )
    return HealthOut(status=overall, components=components)


def version() -> VersionOut:
    # The panel reports its own build; the backend does not know it yet.
    return VersionOut(backend=settings.app_version, panel=None)


__all__ = ["health", "version"]
