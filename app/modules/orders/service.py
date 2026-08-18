"""Orders: the state machine, and the doors other modules use.

``transition`` is the whole point of this module. **It is the only way an
order's status changes** — not because a convention says so, but because
everything that keeps the money path honest happens inside it: the row is
locked, the move is checked against ``states.TRANSITIONS``, the history line is
written, and the next background step is scheduled, all in one transaction. A
service that reaches past it and assigns ``order.status`` gets none of that and
leaves no trace behind (order-system/03-design.md §3.3).

The doors other modules may use are ``record_booking``, ``owned_by_provider_number``,
``ensure_cancellable`` and ``apply_cancel``; ``products`` calls them around its
passthrough. Nothing outside this package touches ``models`` or ``repository``
(ARCHITECTURE.md §4).

**Reading the provider's answer stays forgiving, but no longer silent.** The
field names come from the EASY_GATEWAY collection — recorded calls, not guesses
— yet they have still not been seen against this installation's live GTS
(STATUS.md §8). So an answer we cannot read no longer produces a half-written
row: the order lands in ``needs_attention`` with the whole answer attached to
its first event, which is a thing a person can act on. The booking is never
lost either way.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import Conflict, NotFound
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    page,
    paginate,
)
from app.core.logging import get_logger
from app.modules.orders import repository
from app.modules.orders.models import ORDER_NO_SEQUENCE, Order, OrderEvent
from app.modules.orders.schemas import OrderOut
from app.modules.orders.states import (
    INITIAL_STATUS,
    STAMPED_AT,
    Actor,
    EventAction,
    OrderStatus,
    can,
)
from app.providers.products.orders import BookingResult, CancelResult

logger = get_logger(__name__)

#: ``created_at`` only. Ordering by anything else would publish it as part of
#: the contract (``api/listing.py`` on why this is a whitelist).
_ORDER_ORDERING: OrderingMap = {"created_at": Order.created_at}


# --- the state machine ----------------------------------------------------------


async def transition(
    session: AsyncSession,
    order_id: uuid.UUID,
    *,
    to: OrderStatus,
    actor: Actor,
    action: EventAction,
    reason: str | None = None,
    meta: dict[str, Any] | None = None,
    fields: Mapping[str, Any] | None = None,
    next_attempt_at: datetime | None = None,
) -> Order:
    """Move one order, or refuse. The only writer of ``orders.status``.

    ``fields`` carries whatever else the move establishes — the provider's
    identifiers on a confirmation, a reason on a cancellation. They are applied
    inside the same transaction because a status that is true and a row that is
    not is exactly the state nobody can recover from.

    ``next_attempt_at`` schedules the next background step and defaults to
    ``None``, which means *nothing is pending*. That default is the right one:
    most moves end with the order waiting on the outside world, and a step that
    really is due says so explicitly (``O13``).

    ``attempts`` is bookkeeping, not an argument: a move to a **different**
    status starts a new step and resets it, while the retry loop
    (``ticketing → ticketing``) counts up.

    A move that is not in the table raises ``Conflict`` — a ``409``, never a
    silent no-op. It is nearly always one of two real problems: a race, or a
    client acting on a screen that has gone stale.
    """
    order = await repository.lock_order(session, order_id)
    if order is None:
        raise NotFound("No such order")

    source = OrderStatus(order.status)
    if not can(source, to):
        raise Conflict(
            f"An order that is {source.value} cannot become {to.value}",
            meta={"from": source.value, "to": to.value},
        )

    order.attempts = order.attempts + 1 if source is to else 0
    order.status = to.value
    stamp = STAMPED_AT.get(to)
    if stamp is not None and getattr(order, stamp) is None:
        # First arrival only. Re-entering a status must not rewrite the moment
        # it was first reached — the reports read these columns.
        setattr(order, stamp, datetime.now(UTC))
    for name, value in (fields or {}).items():
        setattr(order, name, value)
    order.next_attempt_at = next_attempt_at

    session.add(
        OrderEvent(
            order_id=order.id,
            from_status=source.value,
            to_status=to.value,
            action=action.value,
            actor_type=actor.type.value,
            actor_id=actor.id,
            actor_label=actor.label,
            reason=reason,
            meta=meta,
            attempt=order.attempts,
        )
    )
    await session.commit()
    await session.refresh(order)
    logger.info(
        "order_transition",
        order_id=str(order.id),
        order_no=order.order_no,
        product=order.product,
        status_from=source.value,
        status_to=to.value,
        action=action.value,
        actor=actor.type.value,
        attempt=order.attempts,
    )
    return order


async def _next_order_no(session: AsyncSession) -> str:
    """``B2C-2608-000123`` — ours, readable, and unique without a lock.

    A sequence rather than a count: two bookings racing must not be handed the
    same number, and ``nextval`` is the only thing that guarantees that. The
    year and month are cosmetic, taken at allocation time; the number alone is
    what is unique.
    """
    number = await session.scalar(sql_text(f"SELECT nextval('{ORDER_NO_SEQUENCE}')"))
    return f"B2C-{datetime.now(UTC):%y%m}-{int(number or 0):06d}"


# --- the doors ``products`` uses ------------------------------------------------


def _reference(payload: Mapping[str, Any], key: str) -> str | None:
    """One of our own request's identifiers, if it is usable.

    Only ``request_id`` and ``offer_id`` are read here, and only from the
    *request*: everything the provider said arrives already translated in a
    ``BookingResult``. Reading GTS's dictionaries is the adapter's job and
    stopped being this module's the moment that port existed
    (``providers/products/orders.py``).
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    text = str(value).strip()
    return text if text and len(text) <= 64 else None


async def record_booking(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    product: str,
    payload: Mapping[str, Any],
    result: BookingResult | None,
    response: dict[str, Any],
) -> Order:
    """File a booking under the customer who made it.

    Two writes, and the order of them is the point. The row is inserted first,
    in ``created`` — so it exists no matter what the answer turns out to look
    like — and only then moved by the machine, which is what puts the first
    line in its history.

    ``result`` is ``None`` when the adapter could not make an order out of the
    answer. That is not a failure to report: GTS very probably booked something
    and we cannot name it, so the row goes to ``needs_attention`` with the whole
    answer on its first event, which is a thing a person can act on. Neither
    guessing nor discarding is acceptable when a real seat may be held.

    Slice S3 moves the insert to **before** the provider call, which is what
    finally closes the lost-booking gap (02-current-audit.md A1). Nothing in the
    shape below changes when it does.
    """
    order = Order(
        order_no=await _next_order_no(session),
        customer_id=customer_id,
        product=product,
        status=INITIAL_STATUS.value,
        request_id=_reference(payload, "request_id"),
        offer_id=_reference(payload, "offer_id"),
    )
    session.add(order)
    await session.commit()

    if result is None:
        logger.warning(
            "order_answer_unreadable",
            order_id=str(order.id),
            order_no=order.order_no,
            product=product,
            fields=sorted(response),
        )
        return await transition(
            session,
            order.id,
            to=OrderStatus.NEEDS_ATTENTION,
            actor=Actor.system("orders.booking"),
            action=EventAction.BOOKING_UNRESOLVED,
            reason="The booking answer could not be read",
            meta=response,
            fields={
                "provider_response": response,
                "attention_reason": "unreadable_booking_answer",
            },
        )

    return await transition(
        session,
        order.id,
        to=result.status,
        actor=Actor.system("orders.booking"),
        action=EventAction.BOOKING_CONFIRMED,
        meta=result.raw,
        fields={
            "provider_order_number": result.provider_order_number,
            "provider_order_uid": result.provider_order_uid,
            "provider_pnr": result.provider_pnr,
            "provider_status": result.provider_status,
            "provider_response": result.raw,
            "amount_total": result.total.amount,
            "currency": result.total.currency,
            "travelers": [person.as_dict() for person in result.travelers],
            "ticket_time_limit_at": result.ticket_time_limit_at,
            "travel_start_at": result.travel_start_at,
            "route_summary": result.route_summary,
        },
    )


async def owned_by_provider_number(
    session: AsyncSession, *, customer_id: uuid.UUID, provider_order_number: str
) -> Order:
    """The order behind GTS's ``order_number``, or ``404`` (API.md §20).

    This is the whole of the cancel ownership check. Nothing reaches GTS before
    it passes.
    """
    order = await repository.order_by_provider_number(
        session, customer_id, provider_order_number
    )
    if order is None:
        raise NotFound("No such order")
    return order


def ensure_cancellable(order: Order) -> None:
    """Refuse a cancellation the machine would refuse anyway — **before** GTS.

    Without this the check happens after the provider has already released the
    seat, and a ticketed order would be cancelled upstream and then rejected
    here. The transition still runs afterwards and is still authoritative; this
    only moves the "no" to the side of the call where it is free.
    """
    if not can(OrderStatus(order.status), OrderStatus.CANCELLED):
        raise Conflict(
            f"An order that is {order.status} cannot be cancelled",
            meta={"from": order.status},
        )


async def apply_cancel(
    session: AsyncSession, order: Order, result: CancelResult
) -> Order:
    """Carry the provider's answer to a cancellation onto the row.

    Their code is kept beside ours rather than instead of it: ``CB`` is what
    they call it, ``cancelled`` is what happened. Reaching here at all means the
    seat is released — a refusal is an ``UpstreamError`` and never gets this
    far — so the move does not depend on what, if anything, they named it.
    """
    return await transition(
        session,
        order.id,
        to=OrderStatus.CANCELLED,
        actor=Actor.customer(order.customer_id),
        action=EventAction.ORDER_CANCELLED,
        reason="Cancelled by the customer",
        meta=result.raw,
        fields={
            "cancellation_reason": "customer",
            "provider_status": result.provider_status or order.provider_status,
        },
    )


# --- what the customer reads ----------------------------------------------------


async def list_orders(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    customer_id: uuid.UUID,
    product: str | None = None,
    status: str | None = None,
) -> Page[OrderOut]:
    """This customer's orders, newest first (API.md §21).

    No ``apply_search`` yet: searching by PNR and passenger name is an admin
    need and arrives with that surface (API.md §31).
    """
    stmt = repository.owned_orders(customer_id)
    if product is not None:
        stmt = stmt.where(Order.product == product)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    stmt = apply_created_range(stmt, query, Order.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_ORDER_ORDERING, default="-created_at", tiebreak=Order.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([OrderOut.from_order(row) for row in rows], pagination, total)


async def get_order(
    session: AsyncSession, *, customer_id: uuid.UUID, order_id: uuid.UUID
) -> OrderOut:
    order = await repository.order_by_id(session, customer_id, order_id)
    if order is None:
        raise NotFound("No such order")
    return OrderOut.from_order(order)


__all__ = [
    "apply_cancel",
    "ensure_cancellable",
    "get_order",
    "list_orders",
    "owned_by_provider_number",
    "record_booking",
    "transition",
]
