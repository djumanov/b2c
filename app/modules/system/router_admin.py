"""``/api/v1/admin/system/*`` — API.md §39.

Thin, like every router: parse, call the service, return a model. The envelope
is added by the route class, errors by the exception handlers.
"""

from fastapi import Depends

from app.api.deps import RequirePermission
from app.api.envelope import enveloped_router
from app.modules.system import service
from app.modules.system.schemas import HealthOut, VersionOut

router = enveloped_router(prefix="/system", tags=["system"])


@router.get(
    "/health/",
    dependencies=[Depends(RequirePermission("system.read"))],
    summary="Database, Redis, GTS and payment provider status",
)
async def get_health() -> HealthOut:
    return await service.health()


@router.get(
    "/version/",
    dependencies=[Depends(RequirePermission("system.read"))],
    summary="Backend and panel version",
)
async def get_version() -> VersionOut:
    return service.version()


__all__ = ["router"]
