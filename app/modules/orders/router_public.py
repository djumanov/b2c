"""``/public/orders/`` — a customer reads their own bookings back.

Booking itself lives on the product flow (``POST /public/{product}/booking/``,
``products/router_public.py``): creating an order is the last step of choosing
a flight; reading orders back is this resource. Every row here requires the
owner's token, so the check sits on the router, not on each handler — the
saved-cards arrangement.

``GET`` here never writes. The "is my ticket ready yet?" screen polls this
endpoint, and the sweep — not the read — is what asks GTS; two polls racing a
sweep must not be able to move an order twice.
"""

import uuid

from fastapi import Depends

from app.api.deps import CurrentCustomer, LanguageDep, PaginationDep, current_customer
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.schemas import BookingResultOut, OrderListItemOut

router = enveloped_router(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(current_customer)],
)


@router.get("/", summary="My orders")
async def list_orders(
    customer: CurrentCustomer,
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
) -> Page[OrderListItemOut]:
    return await service.list_orders(session, customer.id, pagination, query)


@router.get("/{id}/", summary="One order, with its GTS data")
async def get_order(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    language: LanguageDep,
) -> BookingResultOut:
    return await service.get_order(
        session, customer.id, id, language=language.requested
    )


__all__ = ["router"]
