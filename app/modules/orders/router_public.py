"""``/public/orders/`` — the customer's own order history (API.md §21).

Both rows of §21 that are built here are marked ``✓``, so the token check sits
on the **router** rather than on each handler — the arrangement
``payments/router_cards.py`` uses, for the same reason: there is no endpoint
here that could correctly be left off it.

Booking still writes these rows from the product flow (§20), but everything
done **to** an order afterwards lives here: paying for one, and releasing one.
``refund/``, ``history/`` and ``receipt/`` arrive with the later slices.

``?status=`` takes **our** status — ``booked``, ``ticketed``, ``cancelled``,
… — because the vocabulary is ours (order-system/03-design.md §3.3). GTS's own
code still travels beside it in ``gts_status``.

The list and the detail answer with **different** shapes, which is the one
thing worth knowing before reading either: ``OrderListOut`` is a card,
``OrderOut`` is the record (API.md §21).
"""

import uuid
from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import CurrentCustomer, PaginationDep, RateLimit, current_customer
from app.api.envelope import Page, enveloped_router
from app.api.errors import UpstreamError
from app.api.idempotency import IdempotencyKey
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.schemas import (
    OrderListOut,
    OrderOut,
    TransactionOut,
    TransactionStartIn,
)

router = enveloped_router(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(current_customer)],
)

#: ``/public/transactions/{id}/`` is a sibling of ``/public/orders/``, not a
#: child: API.md §22 names it that way and a client polling a redirect has the
#: transaction id and nothing else in hand.
transactions_router = enveloped_router(
    prefix="/transactions",
    tags=["payments"],
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
            "is published beside it as `gts_status`, on the detail endpoint."
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
) -> Page[OrderListOut]:
    """A card, not the record: route, price, status and the countdown.

    ``data`` and ``passengers`` are on ``{id}/`` — the first because a page of
    twenty provider answers is hundreds of kilobytes, the second because a list
    screen has no use for passport numbers (API.md §21).
    """
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


@router.post(
    "/{id}/cancel/",
    summary="Release a booking that has not been paid for",
    dependencies=[Depends(RateLimit("payment"))],
)
async def cancel_order(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    idempotency: IdempotencyKey,
) -> OrderOut:
    """Give the seat back.

    An operation on the **order**, which is what it always was: it needs the
    order's state to decide whether it is allowed, and it used to live on the
    product flow only because there was no local order to name (``O3``).

    Refused before the provider is called if the state does not allow it — a
    ticketed order answers ``409`` without a seat being released, which is the
    one ordering of those two steps that does not cost money.
    """
    if idempotency.replayed is not None:
        return OrderOut.model_validate(idempotency.replayed)
    try:
        answer = await service.cancel_order(
            session, customer_id=customer.id, order_id=id
        )
    except UpstreamError:
        await idempotency.release()
        raise
    await idempotency.store(answer.model_dump(mode="json"))
    return answer


@router.post(
    "/{id}/transactions/",
    summary="Start paying for an order",
    status_code=201,
    dependencies=[Depends(RateLimit("payment"))],
)
async def start_transaction(
    id: uuid.UUID,
    payload: TransactionStartIn,
    customer: CurrentCustomer,
    session: SessionDep,
    idempotency: IdempotencyKey,
) -> TransactionOut:
    """Open one attempt at paying, and say where to send the customer.

    The attempt row is written **before** the provider is called, so a process
    that dies mid-call leaves evidence rather than an unmatched charge
    (ARCHITECTURE.md §8). If the provider then refuses to start, the row stays
    as a failed attempt: what was tried is part of the record.

    Idempotent for the same reason as booking — a dropped response and a
    double-tapped button look identical, and one of them must not become two
    charges (API.md §10). The key is derived from the request when the client
    sends none, so the protection does not depend on the client remembering to
    ask for it.
    """
    if idempotency.replayed is not None:
        return TransactionOut.model_validate(idempotency.replayed)
    try:
        answer = await service.start_transaction(
            session, customer_id=customer.id, order_id=id, data=payload
        )
    except UpstreamError:
        await idempotency.release()
        raise
    await idempotency.store(answer.model_dump(mode="json"))
    return answer


@transactions_router.get("/{id}/", summary="One payment attempt")
async def get_transaction(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> TransactionOut:
    """Someone else's attempt answers 404, the same as one that does not exist."""
    return TransactionOut.from_attempt(
        await service.owned_attempt(session, customer_id=customer.id, attempt_id=id)
    )


__all__ = ["router", "transactions_router"]
