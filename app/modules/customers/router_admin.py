"""``/admin/customers/deletion-reasons/`` — API.md §34.

The dictionary a customer picks from before deleting the account (§19). The
standard §8 CRUD and nothing else: no draft state — hiding a reason is the
soft delete — and no feature flag, because the deletion flow it feeds cannot
be switched off. Writes are audited by the middleware like every admin
mutation.

The rest of §34 (customer list, block/unblock) is later-phase work; this file
grows those routers when it comes.
"""

import uuid

from fastapi import Depends, Response

from app.api.deps import PaginationDep, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.customers import service
from app.modules.customers.schemas import (
    DeletionReasonAdminOut,
    DeletionReasonIn,
    DeletionReasonUpdateIn,
)

deletion_reasons_router = enveloped_router(
    prefix="/customers/deletion-reasons",
    tags=["customers"],
    dependencies=[Depends(current_staff)],
)


@deletion_reasons_router.get("/", summary="List deletion reasons")
async def list_deletion_reasons(
    session: SessionDep, pagination: PaginationDep, query: ListQueryDep
) -> Page[DeletionReasonAdminOut]:
    return await service.list_deletion_reasons_admin(session, pagination, query)


@deletion_reasons_router.post("/", status_code=201, summary="Add a deletion reason")
async def create_deletion_reason(
    data: DeletionReasonIn, session: SessionDep
) -> DeletionReasonAdminOut:
    return await service.create_deletion_reason(session, data)


@deletion_reasons_router.get("/{id}/", summary="One deletion reason")
async def get_deletion_reason(
    id: uuid.UUID, session: SessionDep
) -> DeletionReasonAdminOut:
    return await service.get_deletion_reason(session, id)


@deletion_reasons_router.patch("/{id}/", summary="Change text or order")
async def update_deletion_reason(
    id: uuid.UUID, data: DeletionReasonUpdateIn, session: SessionDep
) -> DeletionReasonAdminOut:
    return await service.update_deletion_reason(session, id, data)


@deletion_reasons_router.delete(
    "/{id}/", status_code=204, summary="Remove a deletion reason"
)
async def delete_deletion_reason(id: uuid.UUID, session: SessionDep) -> Response:
    await service.delete_deletion_reason(session, id)
    return Response(status_code=204)


__all__ = ["deletion_reasons_router"]
