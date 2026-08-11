"""``/public/profile/cards/`` — API.md §19.

The path is a profile path but the module is ``payments``: the row is an
encrypted payment credential, and everything that writes or spends one lives
here (ARCHITECTURE.md §5). ``api/v1/router.py`` mounts this next to
``customers``' passenger router, which is the same arrangement one step out.

Every row of §19 is marked ``✓``, so the token check sits on the **router**
rather than on each handler — the choice ``customers/router_profile.py`` makes,
for the same reason: there is no endpoint here that could correctly be left off
it.

``POST`` keeps the ``payment`` rate limit (API.md §14): no provider is called
any more, but the endpoint still handles raw card data and should not be a wide
open door.
"""

import uuid

from fastapi import Depends, Response

from app.api.deps import (
    CurrentCustomer,
    PaginationDep,
    RateLimit,
    current_customer,
)
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.payments import service
from app.modules.payments.schemas import CardCreateIn, CardOut

router = enveloped_router(
    prefix="/profile/cards",
    tags=["public-profile"],
    dependencies=[Depends(current_customer)],
)


@router.get("/", summary="Saved payment cards")
async def list_cards(
    customer: CurrentCustomer,
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
) -> Page[CardOut]:
    return await service.list_cards(session, customer.id, pagination, query)


@router.post(
    "/",
    status_code=201,
    summary="Save a card",
    dependencies=[Depends(RateLimit("payment"))],
)
async def add_card(
    data: CardCreateIn, customer: CurrentCustomer, session: SessionDep
) -> CardOut:
    return await service.add_card(session, customer.id, data)


@router.get("/{id}/", summary="One saved card")
async def get_card(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> CardOut:
    return await service.get_card(session, customer.id, id)


@router.delete("/{id}/", status_code=204, summary="Forget a card")
async def delete_card(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> Response:
    await service.delete_card(session, customer.id, id)
    return Response(status_code=204)


__all__ = ["router"]
