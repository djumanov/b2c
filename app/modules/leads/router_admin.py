"""``/admin/leads/`` (API.md §35) — list, read, triage.

No assignee: the two-role model makes one noise (API.md §5). The PATCH is
audited by the middleware's own route reading; nothing here needs an
``Audited`` override.
"""

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query

from app.api.deps import PaginationDep, RequireFeature, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.leads import service
from app.modules.leads.schemas import LeadAdminOut, LeadUpdateIn

router = enveloped_router(
    prefix="/leads",
    tags=["leads"],
    dependencies=[Depends(current_staff), Depends(RequireFeature("leads"))],
)

StatusParam = Annotated[
    Literal["new", "in_progress", "done"] | None, Query(alias="status")
]


@router.get("/", summary="List leads")
async def list_leads(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    status: StatusParam = None,
) -> Page[LeadAdminOut]:
    return await service.list_leads(session, pagination, query, status=status)


@router.get("/{id}/", summary="One lead")
async def get_lead(id: uuid.UUID, session: SessionDep) -> LeadAdminOut:
    return await service.get_lead(session, id)


@router.patch("/{id}/", summary="Set the status or the working note")
async def update_lead(
    id: uuid.UUID, data: LeadUpdateIn, session: SessionDep
) -> LeadAdminOut:
    return await service.update_lead(session, id, data)


__all__ = ["router"]
