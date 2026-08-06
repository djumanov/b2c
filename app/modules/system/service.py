"""Health and version reporting.

The four components API.md §39 names are not all the same kind of claim.
``database`` and ``redis`` are **probed** — this process talks to them
constantly anyway, so one more round trip costs nothing. ``gts`` and
``payments`` are only **looked up**: they are upstreams belonging to somebody
else, and a polled endpoint that reached them would turn monitoring into
traffic against a client's own rate limit.
"""

import time
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

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


def _configured(ready: bool, *, where: str) -> ComponentHealth:
    """Report whether an upstream is set up — never whether it answers.

    Deliberately not a probe. This endpoint is polled, and reaching GTS on
    every poll would spend the installation's machine account on a question
    nobody asked; the same argument holds for a payment provider. Liveness is
    what ``integrations/*/test/`` is for, on a button somebody presses.

    So ``ok`` here means "there is something to try", and a broken credential
    still reads ``ok`` until it is tried. That is a narrower claim than the
    other two components make, and it is the honest one to make for free.
    """
    if ready:
        return ComponentHealth(status=ComponentStatus.OK)
    return ComponentHealth(
        status=ComponentStatus.NOT_CONFIGURED, detail=f"Configure under {where}"
    )


async def health(session: AsyncSession) -> HealthOut:
    # Imported here rather than at the top: this module is imported while the
    # router tree is still being assembled, and asking a module who its subject
    # is at request time is the same shape ``api/deps.py`` uses.
    from app.modules.integrations import service as integrations_service

    components = {
        "database": await _probe("database", ping_database),
        "redis": await _probe("redis", ping_redis),
        "gts": _configured(
            await integrations_service.active_credential(session) is not None,
            where="/admin/integrations/gts/",
        ),
        "payments": _configured(
            await integrations_service.any_payment_provider_ready(session),
            where="/admin/integrations/payments/",
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
