"""``/api/v1/admin/system/*`` — API.md §39.

Thin, like every router: parse, call the service, return a model. The envelope
is added by the route class, errors by the exception handlers.

Both roles read system state — API.md §5 gives ``admin`` 👁 on *Tizim va audit*
— so a valid staff token is the whole guard here. ``require_owner`` belongs on
the write routes of that group, once any exist.
"""

from fastapi import Depends

from app.api.deps import current_staff
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.system import service
from app.modules.system.schemas import HealthOut, VersionOut

router = enveloped_router(prefix="/system", tags=["system"])


@router.get(
    "/health/",
    dependencies=[Depends(current_staff)],
    summary="Database, Redis, GTS and payment provider status",
)
async def get_health(session: SessionDep) -> HealthOut:
    return await service.health(session)


@router.get(
    "/version/",
    dependencies=[Depends(current_staff)],
    summary="Backend and panel version",
)
async def get_version() -> VersionOut:
    return service.version()


__all__ = ["router"]
