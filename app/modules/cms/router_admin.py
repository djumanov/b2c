"""``/admin/content/faq/`` (API.md §30).

The whole section sits behind the ``faq`` feature flag: switched off it
answers 404 on both surfaces (API.md §28). ``current_staff`` runs first, so an
anonymous caller is told 401 and a customer token 403 before the flag can turn
either into a 404.
"""

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query, Response

from app.api.deps import PaginationDep, RequireFeature, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.audit.deps import Audited
from app.modules.cms import service
from app.modules.cms.models import ContentStatus
from app.modules.cms.schemas import FaqAdminOut, FaqIn, FaqUpdateIn, ReorderItemIn

faq_router = enveloped_router(
    prefix="/content/faq",
    tags=["cms"],
    dependencies=[Depends(current_staff), Depends(RequireFeature("faq"))],
)

StatusParam = Annotated[Literal["draft", "published"] | None, Query(alias="status")]
CategoryParam = Annotated[str | None, Query(max_length=64)]


@faq_router.get("/", summary="List FAQ entries")
async def list_faq(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    status: StatusParam = None,
    category: CategoryParam = None,
) -> Page[FaqAdminOut]:
    return await service.list_faq_admin(
        session, pagination, query, status=status, category=category
    )


@faq_router.post("/", status_code=201, summary="Add an FAQ entry (as a draft)")
async def create_faq(data: FaqIn, session: SessionDep) -> FaqAdminOut:
    return await service.create_faq(session, data)


@faq_router.post(
    "/reorder/",
    status_code=204,
    dependencies=[Depends(Audited("content.faq", "reorder"))],
    summary="Apply a new order",
)
async def reorder_faq(data: list[ReorderItemIn], session: SessionDep) -> Response:
    await service.reorder_faq(session, data)
    return Response(status_code=204)


@faq_router.get("/{id}/", summary="One FAQ entry")
async def get_faq(id: uuid.UUID, session: SessionDep) -> FaqAdminOut:
    return await service.get_faq(session, id)


@faq_router.patch("/{id}/", summary="Change question, answer or category")
async def update_faq(
    id: uuid.UUID, data: FaqUpdateIn, session: SessionDep
) -> FaqAdminOut:
    return await service.update_faq(session, id, data)


@faq_router.delete("/{id}/", status_code=204, summary="Remove an FAQ entry")
async def delete_faq(id: uuid.UUID, session: SessionDep) -> Response:
    await service.delete_faq(session, id)
    return Response(status_code=204)


@faq_router.post("/{id}/publish/", summary="Publish an FAQ entry")
async def publish_faq(id: uuid.UUID, session: SessionDep) -> FaqAdminOut:
    return await service.set_faq_status(session, id, ContentStatus.PUBLISHED)


@faq_router.post("/{id}/unpublish/", summary="Take an FAQ entry off the site")
async def unpublish_faq(id: uuid.UUID, session: SessionDep) -> FaqAdminOut:
    return await service.set_faq_status(session, id, ContentStatus.DRAFT)


__all__ = ["faq_router"]
