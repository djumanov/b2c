"""``/public/profile/cards/`` — API.md §19.

The path is a profile path but the module is ``payments``: the row is a provider
token, and everything that writes or spends one lives here (ARCHITECTURE.md §5).
``api/v1/router.py`` mounts this next to ``customers``' passenger router, which
is the same arrangement one step further out.

Every row of §19 is marked ``✓``, so the token check sits on the **router**
rather than on each handler — the choice ``customers/router_profile.py`` makes,
for the same reason: there is no endpoint here that could correctly be left off
it.

The three provider-facing routes carry the ``payment`` rate limit, because each
one spends the installation's merchant credentials (API.md §14). ``resend-otp/``
takes the ``auth`` limit instead: it is the same shape of abuse as resending an
email code, so it gets the same number.
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
from app.modules.payments.schemas import (
    CardCreateIn,
    CardOut,
    CardUpdateIn,
    CardVerifyIn,
)

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


@router.patch("/{id}/", summary="Rename a saved card")
async def update_card(
    id: uuid.UUID,
    data: CardUpdateIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> CardOut:
    return await service.update_card(session, customer.id, id, data)


@router.post(
    "/{id}/verify/",
    summary="Confirm a card with the code the provider sent",
    dependencies=[Depends(RateLimit("payment"))],
)
async def verify_card(
    id: uuid.UUID,
    data: CardVerifyIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> CardOut:
    return await service.verify_card(session, customer.id, id, data)


@router.post(
    "/{id}/resend-otp/",
    summary="Send the confirmation code again",
    dependencies=[Depends(RateLimit("auth"))],
)
async def resend_code(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> CardOut:
    return await service.resend_code(session, customer.id, id)


@router.post("/{id}/default/", summary="Make this the default card")
async def set_default(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> CardOut:
    return await service.set_default(session, customer.id, id)


@router.delete("/{id}/", status_code=204, summary="Forget a card")
async def delete_card(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> Response:
    await service.delete_card(session, customer.id, id)
    return Response(status_code=204)


__all__ = ["router"]
