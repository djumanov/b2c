"""``/admin/orders/`` — what support sees and the few things it may do.

Reading is open to every staff token; the three actions are too, because
each is a recorded, reversible bookkeeping step rather than money moving:
``refund/`` marks where a refund the provider's cabinet performed stands,
``sync/`` compares the order with GTS and the provider right now, and
``ticketing/retry/`` asks GTS for the ticket again (after a deposit top-up,
typically). Every action writes an ``order_events`` line with the staff id,
and the audit middleware journals the HTTP call.

``receipt/`` is a read like the two above it, and the customer's own route in
everything but whose order may be asked for: GTS will not serve its receipt
page to anyone without the agent session, so both surfaces fetch the document
with ours. Support needs it for the customer who cannot reach their copy.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Query, Response

from app.api.deps import CurrentStaff, PaginationDep, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.errors import ErrorCode
from app.api.listing import ListQuery, list_query_dep
from app.api.openapi import error_responses
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.lifecycle import Stage
from app.modules.orders.models import OrderStatus, PaymentStatus, TicketingStatus
from app.modules.orders.receipt import (
    RECEIPT_RESPONSES,
    PassengerIndex,
    receipt_response,
)
from app.modules.orders.schemas import (
    OrderAdminListItemOut,
    OrderAdminOut,
    OrderMessageIn,
    OrderMessageOut,
    RefundIn,
)

router = enveloped_router(
    prefix="/orders",
    tags=["admin-orders"],
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
    tags=["admin-orders"],
    dependencies=[Depends(current_staff)],
)


@messages_router.get(
    "/",
    summary="Every status's sentence, in every language",
    description=(
        "One row per customer-facing `status` (six of them): the sentence this "
        "release ships (`default`), what the panel wrote (`custom`), and what "
        "customers actually see (`text` — `custom` over `default`, per "
        "language). This is the `order.message` every customer answer carries."
    ),
    response_description="Six rows, in the order screens list them.",
)
async def list_messages(session: SessionDep) -> list[OrderMessageOut]:
    return await service.list_messages(session)


@messages_router.get(
    "/{status}/",
    summary="One status's sentence",
    description="The same row as in the list, for one `status`.",
)
async def get_message(status: Stage, session: SessionDep) -> OrderMessageOut:
    return await service.get_message(session, status)


@messages_router.patch(
    "/{status}/",
    summary="Rewrite a status's sentence — per language; empty restores the default",
    description=(
        'Languages **merge**: send `{"text": {"uz": "…"}}` to change Uzbek '
        'and leave the others as they are. An empty string (`""`) clears that '
        "language back to the shipped default. Up to 1000 characters per "
        "language; unknown languages are dropped. The text is shown to "
        "customers **exactly as written** — no placeholders — so a support "
        "phone number belongs in the sentence itself."
    ),
    response_description="The row after the change.",
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
BookingParam = Annotated[
    OrderStatus | None,
    Query(alias="booking_status", description="Is the booking alive?"),
]
PaymentParam = Annotated[
    PaymentStatus | None,
    Query(alias="payment_status", description="Where the money is."),
]
TicketingParam = Annotated[
    TicketingStatus | None,
    Query(alias="ticketing_status", description="Where the ticket is."),
]

AdminOrdersListQuery = Depends(
    list_query_dep(
        ordering=("created_at", "updated_at"),
        default="-created_at",
        search="the PNR and the GTS order number",
    )
)


@router.get(
    "/",
    summary="All orders, filterable by the customer's status",
    description=(
        "Every order, newest first. Each row carries the customer's `status` "
        "**and** the three columns it is read from (`booking_status`, "
        "`payment_status`, `ticketing_status`), plus the reason when something "
        "went wrong (`cancel_reason`, `ticketing_error`) so the inbox reads "
        "without opening rows.\n\n"
        "The four filters combine (AND). **The support inbox is "
        "`?status=ticketing_failed&ordering=-updated_at`** — money taken, no "
        "ticket coming on its own, freshest first. `search` matches the PNR "
        "or the GTS order number."
    ),
    response_description="A page of support rows.",
)
async def list_orders(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQuery = AdminOrdersListQuery,
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


@router.get(
    "/{id}/",
    summary="One order, with its history and payments",
    description=(
        "The customer's view of the order (`order`, `payment`, `ticketing`, "
        "`order_data`) plus the books behind it: `order.booking_status` / "
        "`payment_status` / `ticketing_status`, the customer id, how many "
        "times GTS was asked for the ticket, `events[]` (every change, who "
        "made it, oldest first) and `payments[]` (every attempt, without the "
        "provider's references). `order.message` is rendered in the site's "
        "default language."
    ),
    response_description="The order and its books.",
)
async def get_order(id: uuid.UUID, session: SessionDep) -> OrderAdminOut:
    return await service.get_order_admin(session, id)


@router.get(
    "/{id}/receipt/",
    summary="Receipt — the order's itinerary receipt, for support",
    description=(
        "The same file the customer's own route serves, for any order rather "
        "than one caller's: the itinerary receipt of a **ticketed** order, "
        "rendered by GTS and fetched with this installation's agent session. "
        "The answer is the file, not the envelope — fetch it with the staff "
        "token and save what comes back.\n\n"
        "`order.receipt_url` on the admin detail is this path. It is `null`, "
        "and this route answers `409`, until GTS has issued the ticket. Add "
        "`?passenger_index=0` for one traveller's copy. Reading only: nothing "
        "is written and no `order_events` line is added."
    ),
    response_description="The receipt file, as GTS rendered it.",
    response_class=Response,
    responses=RECEIPT_RESPONSES,
)
async def download_receipt(
    id: uuid.UUID,
    session: SessionDep,
    passenger_index: PassengerIndex = None,
) -> Response:
    return receipt_response(
        await service.order_receipt_admin(session, id, passenger_index=passenger_index)
    )


@router.post(
    "/{id}/refund/",
    summary="Mark where the refund stands",
    description=(
        "**This moves no money.** Support refunds in the payment provider's "
        "own cabinet; this records what happened so the order — and the "
        "customer's screen — say so: `refunding` (started), `refunded` (the "
        "money went back; final; the customer sees `status = refunded`), "
        "`refund_failed`. Allowed moves: `paid → refunding | refunded`, "
        "`refunding → refunded | refund_failed`, `refund_failed → refunding | "
        "refunded`. Writes a history line with the staff id and `note`."
    ),
    response_description="The order after the mark.",
    responses=error_responses(
        ErrorCode.CONFLICT,
        conflict=(
            "The move is not allowed from the order's current payment state, "
            "or the order is `ticketed` — a ticketed order is not refunded here."
        ),
    ),
)
async def mark_refund(
    id: uuid.UUID, data: RefundIn, staff: CurrentStaff, session: SessionDep
) -> OrderAdminOut:
    return await service.mark_refund(session, id, data, staff=staff)


@router.post(
    "/{id}/sync/",
    summary="Compare with GTS and the provider now",
    description=(
        "Asks, right now, the questions the background sweep asks every "
        "30 seconds — for this one order: a charge whose answer was lost "
        "(`receipts.check` at the provider), a ticket GTS finished or never "
        "started, a hold GTS released. What it finds is applied through the "
        "same steps the sweep uses, so `sync` can never do something the "
        "sweep could not. Use it when a customer is on the phone and the "
        "screen says `processing` or `ticket_waiting`."
    ),
    response_description="The order after the comparison.",
    responses=error_responses(
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        upstream_error=(
            "GTS or the provider refused the read; their words are in "
            "`meta.upstream`. Nothing was changed."
        ),
        upstream_timeout="GTS or the provider did not answer. Nothing was changed.",
    ),
)
async def sync_order(
    id: uuid.UUID, staff: CurrentStaff, session: SessionDep
) -> OrderAdminOut:
    return await service.sync_order(session, id, staff=staff)


@router.post(
    "/{id}/ticketing/retry/",
    summary="Ask GTS for the ticket again",
    description=(
        "For an order that is paid and `ticketing_failed` — typically after "
        "the GTS deposit was topped up. Syncs first: if GTS already shows the "
        "ticket issued, it is recorded and no request is sent. Otherwise the "
        "ticketing request goes to GTS again (staff are not bound by the "
        "sweep's automatic retry limit) and the answer lands as `ticketed`, "
        "`processing` (the sweep finishes it) or `failed` with GTS's reason."
    ),
    response_description="The order after the attempt.",
    responses=error_responses(
        ErrorCode.CONFLICT,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        conflict=(
            "The order is not paid and booked, or GTS has released the hold — "
            "there is nothing to ticket."
        ),
    ),
)
async def retry_ticketing(
    id: uuid.UUID, staff: CurrentStaff, session: SessionDep
) -> OrderAdminOut:
    return await service.retry_ticketing(session, id, staff=staff)


__all__ = ["messages_router", "router"]
