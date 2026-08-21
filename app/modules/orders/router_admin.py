"""``/admin/orders/`` — what support sees and the few things it may do.

Reading is open to every staff token; the three actions are too, because
each is a recorded, reversible bookkeeping step rather than money moving:
``refund/`` marks where a refund the provider's cabinet performed stands,
``sync/`` compares the order with GTS and the provider right now, and
``ticketing/retry/`` asks GTS for the ticket again (after a deposit top-up,
typically). Every action writes an ``order_events`` line with the staff id,
and the audit middleware journals the HTTP call.
"""

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query

from app.api.deps import CurrentStaff, PaginationDep, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.lifecycle import Stage
from app.modules.orders.schemas import (
    OrderAdminListItemOut,
    OrderAdminOut,
    OrderMessageIn,
    OrderMessageOut,
    RefundIn,
)

router = enveloped_router(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(current_staff)],
)

# --- the customer's sentences ---------------------------------------------------------
#
# A registry keyed by ``Stage``: every stage has a row, there is no POST and no
# DELETE, and both roles write — wording is day-to-day work, like branding.
# Its own router, mounted **before** ``router``: under one prefix the path
# ``/orders/messages/`` would be read as ``/orders/{id}/`` and refused as a
# malformed id.

messages_router = enveloped_router(
    prefix="/orders/messages",
    tags=["orders"],
    dependencies=[Depends(current_staff)],
)


@messages_router.get("/", summary="Every stage's sentence, in every language")
async def list_messages(session: SessionDep) -> list[OrderMessageOut]:
    return await service.list_messages(session)


@messages_router.get("/{stage}/", summary="One stage's sentence")
async def get_message(stage: Stage, session: SessionDep) -> OrderMessageOut:
    return await service.get_message(session, stage)


@messages_router.patch(
    "/{stage}/",
    summary="Rewrite a stage's sentence — per language; empty restores the default",
)
async def update_message(
    stage: Stage, data: OrderMessageIn, session: SessionDep
) -> OrderMessageOut:
    return await service.update_message(session, stage, data)


# --- the orders themselves ------------------------------------------------------------

StatusParam = Annotated[Literal["booked", "cancelled"] | None, Query(alias="status")]
PaymentParam = Annotated[
    Literal["pending", "paid", "failed", "refunding", "refunded", "refund_failed"]
    | None,
    Query(alias="payment_status"),
]
TicketingParam = Annotated[
    Literal["pending", "processing", "ticketed", "failed"] | None,
    Query(alias="ticketing_status"),
]
AttentionParam = Annotated[
    bool,
    Query(
        description=(
            "Only orders a human must act on: the ticket did not come out, a "
            "refund failed, or money was taken on a cancelled order."
        )
    ),
]


@router.get("/", summary="All orders, with the support inbox filter")
async def list_orders(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    status: StatusParam = None,
    payment_status: PaymentParam = None,
    ticketing_status: TicketingParam = None,
    attention: AttentionParam = False,
) -> Page[OrderAdminListItemOut]:
    return await service.list_orders_admin(
        session,
        pagination,
        query,
        status=status,
        payment_status=payment_status,
        ticketing_status=ticketing_status,
        attention=attention,
    )


@router.get("/{id}/", summary="One order, with its history and payments")
async def get_order(id: uuid.UUID, session: SessionDep) -> OrderAdminOut:
    return await service.get_order_admin(session, id)


@router.post("/{id}/refund/", summary="Mark where the refund stands")
async def mark_refund(
    id: uuid.UUID, data: RefundIn, staff: CurrentStaff, session: SessionDep
) -> OrderAdminOut:
    return await service.mark_refund(session, id, data, staff=staff)


@router.post("/{id}/sync/", summary="Compare with GTS and the provider now")
async def sync_order(
    id: uuid.UUID, staff: CurrentStaff, session: SessionDep
) -> OrderAdminOut:
    return await service.sync_order(session, id, staff=staff)


@router.post("/{id}/ticketing/retry/", summary="Ask GTS for the ticket again")
async def retry_ticketing(
    id: uuid.UUID, staff: CurrentStaff, session: SessionDep
) -> OrderAdminOut:
    return await service.retry_ticketing(session, id, staff=staff)


__all__ = ["messages_router", "router"]
