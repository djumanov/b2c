"""``/public/orders/`` — the customer's own order history (API.md §21).

Both rows of §21 that are built here are marked ``✓``, so the token check sits
on the **router** rather than on each handler — the arrangement
``payments/router_cards.py`` uses, for the same reason: there is no endpoint
here that could correctly be left off it.

Read-only. Booking writes these rows from the product flow (§20) and there is
no ``POST`` on this surface yet: ``orders/{id}/cancel/``, ``refund/``,
``history/`` and ``receipt/`` arrive with the later slices, and until then
cancelling goes through ``POST /public/{product}/cancel/``, which checks
ownership against exactly these rows.

``?status=`` takes **our** status now — ``booked``, ``ticketed``, ``cancelled``,
… — because the vocabulary is ours (order-system/03-design.md §3.3). GTS's own
code still travels beside it in ``provider_status``.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import CurrentCustomer, PaginationDep, current_customer
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.schemas import OrderOut

router = enveloped_router(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(current_customer)],
)

ProductParam = Annotated[
    str | None,
    Query(description="Vertical code, e.g. `flight`."),
]
StatusParam = Annotated[
    str | None,
    Query(
        description=(
            "Canonical order status — `created`, `booked`, `paid`, `ticketing`, "
            "`ticketed`, `refunding`, `refunded`, `partially_refunded`, "
            "`cancelled`, `voided`, `failed`, `needs_attention`. GTS's own code "
            "is published beside it as `provider_status`."
        )
    ),
]


@router.get("/", summary="My orders")
async def list_orders(
    customer: CurrentCustomer,
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    product: ProductParam = None,
    status: StatusParam = None,
) -> Page[OrderOut]:
    return await service.list_orders(
        session,
        pagination,
        query,
        customer_id=customer.id,
        product=product,
        status=status,
    )


@router.get("/{id}/", summary="One order")
async def get_order(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> OrderOut:
    # Someone else's order answers 404, the same as one that does not exist
    # (API.md §18).
    return await service.get_order(session, customer_id=customer.id, order_id=id)


__all__ = ["router"]
