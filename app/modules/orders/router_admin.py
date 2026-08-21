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
from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import CurrentStaff, PaginationDep, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.lifecycle import Stage
from app.modules.orders.models import OrderStatus, PaymentStatus, TicketingStatus
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
# A registry keyed by the public ``status`` (``Stage``): every status has a
# row, there is no POST and no DELETE, and both roles write — wording is
# day-to-day work, like branding. Its own router, mounted **before**
# ``router``: under one prefix the path ``/orders/messages/`` would be read as
# ``/orders/{id}/`` and refused as a malformed id.

messages_router = enveloped_router(
    prefix="/orders/messages",
    tags=["orders"],
    dependencies=[Depends(current_staff)],
)


@messages_router.get("/", summary="Every status's sentence, in every language")
async def list_messages(session: SessionDep) -> list[OrderMessageOut]:
    return await service.list_messages(session)


@messages_router.get("/{status}/", summary="One status's sentence")
async def get_message(status: Stage, session: SessionDep) -> OrderMessageOut:
    return await service.get_message(session, status)


@messages_router.patch(
    "/{status}/",
    summary="Rewrite a status's sentence — per language; empty restores the default",
)
async def update_message(
    status: Stage, data: OrderMessageIn, session: SessionDep
) -> OrderMessageOut:
    return await service.update_message(session, status, data)


# --- the orders themselves ------------------------------------------------------------
#
# Four filters: the customer's ``status`` — the word the row shows, matched
# in SQL by the same rule that derives it — and the three raw columns, named
# as the admin row names them. The vocabularies are the enums themselves, so
# a value added to one is accepted here the same day.

StatusParam = Annotated[
    Stage | None,
    Query(
        description=(
            "The customer's status, as the row shows it. "
            "`ticketing_failed` is the support inbox: every order whose screen "
            "says to contact support, and none whose money has gone back."
        )
    ),
]
BookingParam = Annotated[OrderStatus | None, Query(alias="booking_status")]
PaymentParam = Annotated[PaymentStatus | None, Query(alias="payment_status")]
TicketingParam = Annotated[TicketingStatus | None, Query(alias="ticketing_status")]


@router.get("/", summary="All orders, filterable by the customer's status")
async def list_orders(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    status: StatusParam = None,
    booking_status: BookingParam = None,
    payment_status: PaymentParam = None,
    ticketing_status: TicketingParam = None,
) -> Page[OrderAdminListItemOut]:
    return await service.list_orders_admin(
        session,
        pagination,
        query,
        status=status,
        booking_status=booking_status,
        payment_status=payment_status,
        ticketing_status=ticketing_status,
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
