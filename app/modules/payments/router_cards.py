"""``/public/profile/cards/`` — the customer's saved cards.

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
from app.api.listing import ListQuery, list_query_dep
from app.db.session import SessionDep
from app.modules.payments import service
from app.modules.payments.schemas import CardCreateIn, CardOut

router = enveloped_router(
    prefix="/profile/cards",
    tags=["saved-cards"],
    dependencies=[Depends(current_customer)],
)

CardsListQuery = Depends(
    list_query_dep(
        ordering=("created_at", "last_used_at"),
        default="-created_at",
        search="the masked number and the brand",
    )
)


@router.get(
    "/",
    summary="Saved payment cards",
    description=(
        "The customer's saved cards, newest first — masked number, last four "
        "digits, brand, expiry and when each was last used. Never the number. "
        "Pay with one by sending its `id` as `card_id` to "
        "`POST /public/orders/{id}/payment/`."
    ),
    response_description="A page of cards.",
)
async def list_cards(
    customer: CurrentCustomer,
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQuery = CardsListQuery,
) -> Page[CardOut]:
    return await service.list_cards(session, customer.id, pagination, query)


@router.post(
    "/",
    status_code=201,
    summary="Save a card",
    description=(
        "Keeps a card for next time. Saving is local: no provider is called, "
        "no charge, no code — the card is checked (13–19 digits, Luhn, `MMYY`) "
        "and sealed at rest. The answer never carries the number. The same "
        "card twice is a `422` on `number`. A card can also be saved while "
        "paying, with `save: true`."
    ),
    response_description="The saved card, masked.",
    dependencies=[Depends(RateLimit("payment"))],
)
async def add_card(
    data: CardCreateIn, customer: CurrentCustomer, session: SessionDep
) -> CardOut:
    return await service.add_card(session, customer.id, data)


@router.get(
    "/{id}/",
    summary="One saved card",
    description="One card, masked. Another customer's card is a `404`.",
)
async def get_card(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> CardOut:
    return await service.get_card(session, customer.id, id)


@router.delete(
    "/{id}/",
    status_code=204,
    summary="Forget a card",
    description=(
        "Removes the card from the customer's list and erases the sealed "
        "number. `204` with no body. Payments already made with it are "
        "unaffected."
    ),
)
async def delete_card(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> Response:
    await service.delete_card(session, customer.id, id)
    return Response(status_code=204)


__all__ = ["router"]
