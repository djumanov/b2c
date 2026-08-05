"""``/admin/system/audit/`` — API.md §39.

Read-only, and read-only for **both** roles: API.md §5 gives ``admin`` 👁 on
*Tizim va audit*. There is no write endpoint here and there will not be one —
the journal is append-only and the only thing that appends to it is the
middleware (ARCHITECTURE.md §5).

The path sits under ``system/`` because that is where the contract puts it,
even though the module is ``audit``. Sections of the API and packages of the
code do not have to line up one-to-one; the contract wins (CLAUDE.md).
"""

import uuid
from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import PaginationDep, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.audit import service
from app.modules.audit.schemas import AuditLogOut

router = enveloped_router(
    prefix="/system/audit",
    tags=["system"],
    dependencies=[Depends(current_staff)],
)


@router.get("/", summary="Who changed what, and when")
async def list_audit(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    actor: Annotated[uuid.UUID | None, Query()] = None,
    resource: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
) -> Page[AuditLogOut]:
    return await service.list_entries(
        session,
        pagination,
        query,
        actor=actor,
        resource=resource,
        action=action,
    )


__all__ = ["router"]
