"""The order state machine — the vocabulary and the legal moves.

This module is **data, not behaviour**: an enum of the states an order can be
in, and a table of the moves between them. ``service.transition`` is the only
thing that reads it, and it is deliberately the only thing that can change an
order's status (order-system/03-design.md §3.3).

Two reasons it lives apart from the model. It has to be readable next to the
document that specifies it — the table below is the same table, row for row,
with the same ``T`` numbers — and it has to be testable without a database, so
the sweep that checks every one of the 12 × 12 pairs is a unit test.

**The vocabulary is ours, not GTS's.** GTS speaks ``BO``/``PW``/``TI``/… and
those codes still arrive; they are kept verbatim in ``orders.provider_status``
and translated here by the vertical's adapter. A status column holding somebody
else's vocabulary cannot carry a ``CHECK`` constraint, cannot be reasoned about,
and grows a new value whenever they ship one — which is what the old column did
(order-system/02-current-audit.md A3).

**What this module does not do: guards.** A transition's preconditions — is the
payment really settled, does every traveller have a ticket number — belong to
the caller that knows the answer. The table says which moves exist; it does not
know whether this particular move is a good idea.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class OrderStatus(StrEnum):
    """Where an order is. Twelve values, and the list is closed.

    Something becomes a status only if it changes what the customer is shown,
    what they are allowed to do, or what runs in the background. Everything
    else is a *reason* on the row — which is why "cancelled by the customer"
    and "cancelled because the hold lapsed" are one status and two reasons
    (order-system/01-research.md §1.4).
    """

    #: The row exists, the provider has not answered yet. Written *before* the
    #: booking call, so a booking can never happen without a row to find it by.
    CREATED = "created"
    #: The provider is holding the reservation; payment is awaited and
    #: ``ticket_time_limit_at`` is running.
    BOOKED = "booked"
    #: Money is in. Ticketing is queued.
    PAID = "paid"
    #: The ticketing call is in flight at the provider. Retries live here.
    TICKETING = "ticketing"
    #: Tickets were issued.
    TICKETED = "ticketed"
    #: A refund is being carried out — automatic compensation or an approved
    #: request.
    REFUNDING = "refunding"
    REFUNDED = "refunded"
    #: Reachable through synchronisation today (GTS ``PRF``); the partial
    #: refund path itself is v2.
    PARTIALLY_REFUNDED = "partially_refunded"
    #: The hold was released. ``cancellation_reason`` says by whom or why.
    CANCELLED = "cancelled"
    #: An issued ticket was voided. v2.
    VOIDED = "voided"
    #: The booking never opened. **No money moved** — this is the one failure
    #: that costs nothing to recover from.
    FAILED = "failed"
    #: Money moved and the automatic compensation could not finish. Terminal
    #: for the machine, a queue for a human (PROJECT.md D3).
    NEEDS_ATTENTION = "needs_attention"


class StatusClass(StrEnum):
    """What kind of state this is — the thing "terminal" is too vague for.

    Three different things get called terminal, and telling them apart is what
    stops the later argument about why an order left a state that was supposed
    to be final (order-system/01-research.md §1.4).
    """

    #: Background work is running or scheduled.
    ACTIVE = "active"
    #: Waiting on something outside: the customer, the provider, a deadline.
    WAITING = "waiting"
    #: Nothing is pending, but a **new** operation may still start — a refund
    #: against a ticketed order is not the machine going backwards.
    SETTLED = "settled"
    #: No way out at all.
    CLOSED = "closed"
    #: Only a member of staff can move it.
    MANUAL = "manual"


STATUS_CLASS: Final[Mapping[OrderStatus, StatusClass]] = {
    OrderStatus.CREATED: StatusClass.ACTIVE,
    OrderStatus.BOOKED: StatusClass.WAITING,
    OrderStatus.PAID: StatusClass.WAITING,
    OrderStatus.TICKETING: StatusClass.ACTIVE,
    OrderStatus.TICKETED: StatusClass.SETTLED,
    OrderStatus.REFUNDING: StatusClass.ACTIVE,
    OrderStatus.REFUNDED: StatusClass.CLOSED,
    OrderStatus.PARTIALLY_REFUNDED: StatusClass.SETTLED,
    OrderStatus.CANCELLED: StatusClass.CLOSED,
    OrderStatus.VOIDED: StatusClass.CLOSED,
    OrderStatus.FAILED: StatusClass.CLOSED,
    OrderStatus.NEEDS_ATTENTION: StatusClass.MANUAL,
}


class EventAction(StrEnum):
    """What happened, as written into ``order_events.action``.

    A closed vocabulary rather than a free string: the panel groups by it and
    a typo in a label nobody reads is a gap in the history somebody eventually
    does read.
    """

    BOOKING_CONFIRMED = "booking.confirmed"
    BOOKING_REJECTED = "booking.rejected"
    BOOKING_UNRESOLVED = "booking.unresolved"
    BOOKING_EXPIRED = "booking.expired"
    PAYMENT_SETTLED = "payment.settled"
    #: Money arrived, but not the money this order is for.
    PAYMENT_MISMATCHED = "payment.mismatched"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_VOIDED = "order.voided"
    TICKETING_STARTED = "ticketing.started"
    TICKETING_RETRY = "ticketing.retry"
    TICKETING_SUCCEEDED = "ticketing.succeeded"
    TICKETING_FAILED = "ticketing.failed"
    #: Some travellers have tickets and some do not — nothing automatic can
    #: put that right.
    TICKETING_PARTIAL = "ticketing.partial"
    REFUND_STARTED = "refund.started"
    REFUND_RETRY = "refund.retry"
    REFUND_SUCCEEDED = "refund.succeeded"
    REFUND_PARTIAL = "refund.partial"
    REFUND_FAILED = "refund.failed"
    ATTENTION_RESOLVED = "attention.resolved"


class RefundKind(StrEnum):
    """Which door a refund came in through.

    It decides whether anybody has to agree to it: compensation does not need
    a person's opinion, a customer's request does (``O11``).
    """

    #: A ticket did not issue and the money has to go back. Approved on
    #: creation — there is nothing to decide.
    AUTO = "auto"
    #: The customer asked. Waits for a person, because the penalty comes from
    #: the fare rules.
    CUSTOMER = "customer"
    #: A member of staff started it.
    ADMIN = "admin"


class RefundState(StrEnum):
    """Where one refund has got to."""

    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ActorType(StrEnum):
    SYSTEM = "system"
    CUSTOMER = "customer"
    STAFF = "staff"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who caused a transition. Stored as values, like the audit journal.

    ``label`` is what a human reads in the history: a task name for the system,
    an email for a member of staff. No foreign key — an order's history has to
    stay readable after the staff row is gone (``audit/models.py`` states the
    same reasoning at length).
    """

    type: ActorType
    id: uuid.UUID | None = None
    label: str | None = None

    @classmethod
    def system(cls, label: str) -> "Actor":
        """A background step. ``label`` is the task or service that ran it."""
        return cls(type=ActorType.SYSTEM, label=label)

    @classmethod
    def customer(cls, customer_id: uuid.UUID) -> "Actor":
        return cls(type=ActorType.CUSTOMER, id=customer_id)

    @classmethod
    def staff(cls, staff_id: uuid.UUID, label: str | None = None) -> "Actor":
        return cls(type=ActorType.STAFF, id=staff_id, label=label)


@dataclass(frozen=True, slots=True)
class Transition:
    """One legal move. ``code`` is its number in order-system/03-design.md §3.3."""

    code: str
    source: OrderStatus
    target: OrderStatus
    action: EventAction


#: The status an order is born in. Not a transition: there is no prior state,
#: and the row is written by an ``INSERT``.
INITIAL_STATUS: Final = OrderStatus.CREATED


#: Every legal move, in the order the design document lists them. Two rows may
#: share a ``(source, target)`` pair — a hold is released the same way whether
#: the customer asked or the deadline passed, and only the action and the
#: reason differ.
TRANSITIONS: Final[tuple[Transition, ...]] = (
    Transition(
        "T2", OrderStatus.CREATED, OrderStatus.BOOKED, EventAction.BOOKING_CONFIRMED
    ),
    Transition(
        "T3", OrderStatus.CREATED, OrderStatus.FAILED, EventAction.BOOKING_REJECTED
    ),
    # The provider answered and we will not call it a reservation: a field the
    # row cannot do without would not read, or the status is not a held seat.
    # It stays ``created`` — which is exactly what that status means, "we asked
    # and do not know" — and the reconciliation sweep settles it with GTS. Not
    # ``needs_attention``: no money has moved, and that state is the one a
    # person has to work through (03-design.md §3.3).
    Transition(
        "T2x",
        OrderStatus.CREATED,
        OrderStatus.CREATED,
        EventAction.BOOKING_UNRESOLVED,
    ),
    # The end of that road. Reconciliation asked GTS repeatedly and could
    # neither confirm the order nor be told it does not exist; an order nobody
    # can account for is a person's problem, and leaving it ``created`` for
    # ever would only hide it.
    Transition(
        "T4",
        OrderStatus.CREATED,
        OrderStatus.NEEDS_ATTENTION,
        EventAction.BOOKING_UNRESOLVED,
    ),
    Transition("T5", OrderStatus.BOOKED, OrderStatus.PAID, EventAction.PAYMENT_SETTLED),
    # The guard-violation edges. A move whose precondition fails is not always a
    # refusal: when money has already moved, there is nowhere safe to stand and
    # the order goes to the queue a person works through
    # (order-system/03-design.md §3.3, the "Guard buzilsa" column).
    Transition(
        "T5x",
        OrderStatus.BOOKED,
        OrderStatus.NEEDS_ATTENTION,
        EventAction.PAYMENT_MISMATCHED,
    ),
    Transition(
        "T6", OrderStatus.BOOKED, OrderStatus.CANCELLED, EventAction.ORDER_CANCELLED
    ),
    Transition(
        "T7", OrderStatus.BOOKED, OrderStatus.CANCELLED, EventAction.BOOKING_EXPIRED
    ),
    Transition(
        "T8", OrderStatus.PAID, OrderStatus.TICKETING, EventAction.TICKETING_STARTED
    ),
    Transition(
        "T9", OrderStatus.TICKETING, OrderStatus.TICKETING, EventAction.TICKETING_RETRY
    ),
    Transition(
        "T10",
        OrderStatus.TICKETING,
        OrderStatus.TICKETED,
        EventAction.TICKETING_SUCCEEDED,
    ),
    Transition(
        "T10x",
        OrderStatus.TICKETING,
        OrderStatus.NEEDS_ATTENTION,
        EventAction.TICKETING_PARTIAL,
    ),
    Transition(
        "T11",
        OrderStatus.TICKETING,
        OrderStatus.REFUNDING,
        EventAction.TICKETING_FAILED,
    ),
    Transition(
        "T12", OrderStatus.PAID, OrderStatus.REFUNDING, EventAction.REFUND_STARTED
    ),
    Transition(
        "T13", OrderStatus.TICKETED, OrderStatus.REFUNDING, EventAction.REFUND_STARTED
    ),
    Transition(
        "T14", OrderStatus.TICKETED, OrderStatus.VOIDED, EventAction.ORDER_VOIDED
    ),
    Transition(
        "T15x",
        OrderStatus.REFUNDING,
        OrderStatus.REFUNDING,
        EventAction.REFUND_RETRY,
    ),
    Transition(
        "T15", OrderStatus.REFUNDING, OrderStatus.REFUNDED, EventAction.REFUND_SUCCEEDED
    ),
    Transition(
        "T16",
        OrderStatus.REFUNDING,
        OrderStatus.PARTIALLY_REFUNDED,
        EventAction.REFUND_PARTIAL,
    ),
    Transition(
        "T17",
        OrderStatus.REFUNDING,
        OrderStatus.NEEDS_ATTENTION,
        EventAction.REFUND_FAILED,
    ),
    Transition(
        "T18",
        OrderStatus.NEEDS_ATTENTION,
        OrderStatus.TICKETED,
        EventAction.ATTENTION_RESOLVED,
    ),
    Transition(
        "T18",
        OrderStatus.NEEDS_ATTENTION,
        OrderStatus.REFUNDED,
        EventAction.ATTENTION_RESOLVED,
    ),
    Transition(
        "T18",
        OrderStatus.NEEDS_ATTENTION,
        OrderStatus.CANCELLED,
        EventAction.ATTENTION_RESOLVED,
    ),
)


_ALLOWED: Final[frozenset[tuple[OrderStatus, OrderStatus]]] = frozenset(
    (item.source, item.target) for item in TRANSITIONS
)

#: Which column records the moment a status was reached. Only the four the
#: reports read: everything else is reconstructable from ``order_events``, and
#: a timestamp column nobody queries is a column that drifts.
STAMPED_AT: Final[Mapping[OrderStatus, str]] = {
    OrderStatus.BOOKED: "booked_at",
    OrderStatus.PAID: "paid_at",
    OrderStatus.TICKETED: "ticketed_at",
    OrderStatus.CANCELLED: "cancelled_at",
}


def can(source: OrderStatus, target: OrderStatus) -> bool:
    """Is this move in the table? Everything outside it is a ``409``."""
    return (source, target) in _ALLOWED


def targets_from(source: OrderStatus) -> frozenset[OrderStatus]:
    """Every status reachable in one step. Empty for a *closed* status."""
    return frozenset(item.target for item in TRANSITIONS if item.source == source)


def status_class(status: OrderStatus) -> StatusClass:
    return STATUS_CLASS[status]


def is_closed(status: OrderStatus) -> bool:
    """No way out — the order is finished and will not move again."""
    return STATUS_CLASS[status] is StatusClass.CLOSED


__all__ = [
    "INITIAL_STATUS",
    "RefundKind",
    "RefundState",
    "STAMPED_AT",
    "STATUS_CLASS",
    "TRANSITIONS",
    "Actor",
    "ActorType",
    "EventAction",
    "OrderStatus",
    "StatusClass",
    "Transition",
    "can",
    "is_closed",
    "status_class",
    "targets_from",
]
