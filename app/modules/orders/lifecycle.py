"""The three lifecycles and the one function that moves them.

Every status change in the order module goes through ``transition``. It does
three things and nothing else: refuses a move the tables below do not allow
(``409 conflict``), applies the allowed ones to the row, and hands back one
``OrderEvent`` per change so the caller can add them to the **same
transaction** — the history can then never disagree with the row.

Pure on purpose: no session, no I/O, no clock beyond ``utcnow``. That is what
makes the rules testable as a table and the callers (request handlers, the
sweep, staff actions) free to decide when to commit.

**Money facts are never refused.** ``payment → paid`` is recordable in any
state, even on a cancelled order: the charge happened, and a row that says
otherwise is a lie. What prevents the awkward combinations is the guards on
the *actions* — cancel and expiry refuse while a payment is being confirmed —
and ``stage_of`` reads any that slip through as ``ticketing_failed``: money
was taken and no ticket is coming, so a human must get involved.
"""

import enum
import uuid
from dataclasses import dataclass, replace
from typing import Any, Final

from app.api.errors import Conflict
from app.core.logging import request_id_var
from app.db.mixins import utcnow
from app.modules.orders.models import (
    CancelReason,
    Order,
    OrderEvent,
    OrderStatus,
    PaymentStatus,
    TicketingStatus,
)

# --- who acts ----------------------------------------------------------------------

CUSTOMER: Final = "customer"
SYSTEM: Final = "system"


def staff(staff_id: uuid.UUID) -> str:
    return f"staff:{staff_id}"


def is_staff(actor: str) -> bool:
    return actor.startswith("staff:")


# --- what may move where -------------------------------------------------------------

ORDER_STATUS: Final[dict[str, frozenset[str]]] = {
    OrderStatus.BOOKED: frozenset({OrderStatus.CANCELLED}),
    OrderStatus.CANCELLED: frozenset(),
}

PAYMENT_STATUS: Final[dict[str, frozenset[str]]] = {
    PaymentStatus.PENDING: frozenset({PaymentStatus.PAID, PaymentStatus.FAILED}),
    # "The last attempt failed" — the next one may still pay.
    PaymentStatus.FAILED: frozenset({PaymentStatus.PAID}),
    PaymentStatus.PAID: frozenset({PaymentStatus.REFUNDING, PaymentStatus.REFUNDED}),
    PaymentStatus.REFUNDING: frozenset(
        {PaymentStatus.REFUNDED, PaymentStatus.REFUND_FAILED}
    ),
    PaymentStatus.REFUND_FAILED: frozenset(
        {PaymentStatus.REFUNDING, PaymentStatus.REFUNDED}
    ),
    PaymentStatus.REFUNDED: frozenset(),
}

TICKETING_STATUS: Final[dict[str, frozenset[str]]] = {
    TicketingStatus.PENDING: frozenset({TicketingStatus.PROCESSING}),
    # Re-sending the request to GTS is not a move; the status stays.
    TicketingStatus.PROCESSING: frozenset(
        {TicketingStatus.TICKETED, TicketingStatus.FAILED}
    ),
    # ``processing`` again when staff retry; ``ticketed`` when GTS turns out
    # to have issued the ticket late and a read-back finds it.
    TicketingStatus.FAILED: frozenset(
        {TicketingStatus.PROCESSING, TicketingStatus.TICKETED}
    ),
    TicketingStatus.TICKETED: frozenset(),
}

_TABLES: Final[dict[str, dict[str, frozenset[str]]]] = {
    "status": ORDER_STATUS,
    "payment": PAYMENT_STATUS,
    "ticketing": TICKETING_STATUS,
}

#: Attempt status that means "a charge is in flight at the provider" — the one
#: moment nothing about the order may change under it.
CONFIRMING: Final = "confirming"


@dataclass(frozen=True, slots=True)
class _State:
    """The three columns as one value, so guards read the *resulting* order."""

    status: str
    payment: str
    ticketing: str


def _refusal(
    state: _State, field: str, target: str, *, actor: str, open_attempt: str | None
) -> str | None:
    """Why this move is not allowed right now — ``None`` when it is.

    ``state`` already carries the move (and any earlier move of the same
    call), so one call may say ``payment=paid, ticketing=processing`` and the
    ticketing guard sees the payment as paid.
    """
    if field == "status" and target == OrderStatus.CANCELLED:
        if state.ticketing in (TicketingStatus.PROCESSING, TicketingStatus.TICKETED):
            return "the ticket is being issued or already issued"
        if open_attempt == CONFIRMING:
            return "a payment is being confirmed"
        if not is_staff(actor) and state.payment not in (
            PaymentStatus.PENDING,
            PaymentStatus.FAILED,
        ):
            return "a paid order can only be cancelled by staff"

    if field == "payment" and target in (
        PaymentStatus.REFUNDING,
        PaymentStatus.REFUNDED,
        PaymentStatus.REFUND_FAILED,
    ):
        if not is_staff(actor):
            return "refunds are marked by staff"
        if state.ticketing == TicketingStatus.TICKETED:
            return "a ticketed order is not refunded here"

    if (
        field == "ticketing"
        and target in (TicketingStatus.PROCESSING, TicketingStatus.TICKETED)
        and (state.payment != PaymentStatus.PAID or state.status != OrderStatus.BOOKED)
    ):
        return "ticketing needs a paid, live booking"

    return None


def _current(order: Order) -> _State:
    return _State(order.status, order.payment_status, order.ticketing_status)


def event(
    order: Order,
    *,
    event: str,
    actor: str,
    from_value: str | None = None,
    to_value: str | None = None,
    note: str | None = None,
    data: dict[str, Any] | None = None,
) -> OrderEvent:
    """One history line, stamped with the request it happened in."""
    return OrderEvent(
        order_id=order.id,
        event=event,
        from_value=from_value,
        to_value=to_value,
        actor=actor,
        note=note,
        data=data,
        request_id=request_id_var.get() or None,
    )


def transition(
    order: Order,
    *,
    actor: str,
    status: OrderStatus | None = None,
    payment: PaymentStatus | None = None,
    ticketing: TicketingStatus | None = None,
    cancel_reason: CancelReason | None = None,
    note: str | None = None,
    data: dict[str, Any] | None = None,
    open_attempt: str | None = None,
) -> list[OrderEvent]:
    """Move the order; return the events to add to the caller's session.

    A move to the value already held is a no-op and produces no event, so a
    settle that races another settle simply does nothing the second time. A
    move the tables refuse, or a guard refuses, raises ``Conflict`` **before**
    anything is applied — the order is untouched when the caller sees it.

    ``open_attempt`` is the status of the order's open payment attempt, if
    any; the caller knows it, this module does not query.
    """
    requested = [
        ("status", status),
        ("payment", payment),
        ("ticketing", ticketing),
    ]
    state = _current(order)
    moves: list[tuple[str, str, str]] = []
    for field, target in requested:
        if target is None:
            continue
        current = getattr(state, field)
        if target == current:
            continue
        if target not in _TABLES[field][current]:
            raise Conflict(f"order cannot move {field} from {current} to {target}")
        state = replace(state, **{field: str(target)})
        refusal = _refusal(state, field, target, actor=actor, open_attempt=open_attempt)
        if refusal is not None:
            raise Conflict(f"order cannot move {field} to {target}: {refusal}")
        moves.append((field, current, str(target)))

    now = utcnow()
    events: list[OrderEvent] = []
    for field, before, after in moves:
        if field == "status":
            order.status = after
            order.cancelled_at = now
            order.cancel_reason = cancel_reason
        elif field == "payment":
            order.payment_status = after
            if after == PaymentStatus.PAID:
                order.paid_at = now
        else:
            order.ticketing_status = after
            if after == TicketingStatus.TICKETED:
                order.ticketed_at = now
        events.append(
            event(
                order,
                event=f"{'order' if field == 'status' else field}.{after}",
                actor=actor,
                from_value=before,
                to_value=after,
                note=note,
                data=data,
            )
        )
    return events


# --- the public ``status`` -----------------------------------------------------------


class Stage(enum.StrEnum):
    """The three columns collapsed to the one ``status`` a client reads.

    Derived on every read, never stored: the columns are the truth and this
    is one reading of them, kept on the server so every client reads alike.
    Six words, because a screen needs no more — what the payment attempt is
    doing lives in the ``payment`` block, and the raw columns are for staff.

    Declaration order is the order the panel lists the sentences in.
    """

    #: Held and not paid — whatever the last attempt did, the next may pay.
    BOOKED = "booked"
    #: Paid; GTS has not said yes or no yet.
    TICKET_WAITING = "ticket_waiting"
    TICKETED = "ticketed"
    #: Money was taken and no ticket is coming on its own: GTS refused, staff
    #: cancelled a paid order, or a refund is under way — a human is involved.
    TICKETING_FAILED = "ticketing_failed"
    #: The money went back. The only terminal money state, named so the
    #: customer is never told to "contact us" about a refund already made.
    REFUNDED = "refunded"
    #: Released before any money moved — by the customer, by staff, or by the
    #: deadline (``cancel_reason`` says which).
    CANCELLED = "cancelled"


def stage_of(order: Order) -> Stage:
    """Total over the three columns: every combination has an answer, the
    unreachable ones land on the side that asks a human to look."""
    payment = order.payment_status
    if payment == PaymentStatus.REFUNDED:
        return Stage.REFUNDED
    if payment in (PaymentStatus.PENDING, PaymentStatus.FAILED):
        if order.status == OrderStatus.CANCELLED:
            return Stage.CANCELLED
        return Stage.BOOKED
    # Money was taken: paid, refunding or refund_failed.
    if order.ticketing_status == TicketingStatus.TICKETED:
        return Stage.TICKETED
    if (
        order.status == OrderStatus.CANCELLED
        or payment != PaymentStatus.PAID
        or order.ticketing_status == TicketingStatus.FAILED
    ):
        return Stage.TICKETING_FAILED
    return Stage.TICKET_WAITING


__all__ = [
    "CONFIRMING",
    "CUSTOMER",
    "ORDER_STATUS",
    "PAYMENT_STATUS",
    "SYSTEM",
    "TICKETING_STATUS",
    "Stage",
    "event",
    "is_staff",
    "staff",
    "stage_of",
    "transition",
]
