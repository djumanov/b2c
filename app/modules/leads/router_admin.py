"""``/admin/leads/`` (API.md §35) — list, read, triage.

No assignee: the two-role model makes one noise (API.md §5). The PATCH is
audited by the middleware's own route reading; nothing here needs an
``Audited`` override.
"""

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query, Response

from app.api.deps import PaginationDep, RequireFeature, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.leads import service
from app.modules.leads.schemas import (
    LeadAdminOut,
    LeadUpdateIn,
    SupportTopicAdminOut,
    SupportTopicIn,
    SupportTopicUpdateIn,
)

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


# --- support topics (API.md §35) --------------------------------------------------

topics_router = enveloped_router(
    prefix="/leads/topics",
    tags=["leads"],
    dependencies=[Depends(current_staff), Depends(RequireFeature("leads"))],
)


@topics_router.get("/", summary="List support topics")
async def list_topics(
    session: SessionDep, pagination: PaginationDep, query: ListQueryDep
) -> Page[SupportTopicAdminOut]:
    return await service.list_topics_admin(session, pagination, query)


@topics_router.post("/", status_code=201, summary="Add a support topic")
async def create_topic(
    data: SupportTopicIn, session: SessionDep
) -> SupportTopicAdminOut:
    return await service.create_topic(session, data)


@topics_router.get("/{id}/", summary="One support topic")
async def get_topic(id: uuid.UUID, session: SessionDep) -> SupportTopicAdminOut:
    return await service.get_topic(session, id)


@topics_router.patch("/{id}/", summary="Change name or order")
async def update_topic(
    id: uuid.UUID, data: SupportTopicUpdateIn, session: SessionDep
) -> SupportTopicAdminOut:
    return await service.update_topic(session, id, data)


@topics_router.delete("/{id}/", status_code=204, summary="Remove a support topic")
async def delete_topic(id: uuid.UUID, session: SessionDep) -> Response:
    await service.delete_topic(session, id)
    return Response(status_code=204)


__all__ = ["router", "topics_router"]
