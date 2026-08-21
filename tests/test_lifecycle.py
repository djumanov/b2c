"""``orders/lifecycle.py`` — the transition tables and guards, as a table.

Pure tests: an ``Order`` built in memory, ``transition`` applied, the events it
hands back inspected. No database, because the rules must hold regardless of
what any caller commits.
"""

import uuid
from typing import Any

import pytest

from app.api.errors import Conflict
from app.modules.orders import lifecycle
from app.modules.orders.lifecycle import Stage, stage_of, transition
from app.modules.orders.models import (
    CancelReason,
    Order,
    OrderStatus,
    PaymentStatus,
    TicketingStatus,
)

STAFF = lifecycle.staff(uuid.uuid4())


def _order(**overrides: Any) -> Order:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "customer_id": uuid.uuid4(),
        "product": "flight",
        "status": OrderStatus.BOOKED,
        "payment_status": PaymentStatus.PENDING,
        "ticketing_status": TicketingStatus.PENDING,
        "request_id": "req",
        "offer_id": "offer",
        "gts_order_number": 1,
        "gts_status": "BO",
        "gts_response": {},
    }
    fields.update(overrides)
    return Order(**fields)


def test_same_value_is_noop() -> None:
    order = _order()
    assert (
        transition(order, actor=lifecycle.CUSTOMER, payment=PaymentStatus.PENDING) == []
    )
    assert order.paid_at is None


def test_unknown_target_is_conflict() -> None:
    with pytest.raises(Conflict):
        transition(_order(), actor=lifecycle.SYSTEM, ticketing=TicketingStatus.TICKETED)
    with pytest.raises(Conflict):
        transition(
            _order(payment_status=PaymentStatus.REFUNDED),
            actor=STAFF,
            payment=PaymentStatus.PAID,
        )


def test_paid_stamps_and_records_event() -> None:
    order = _order()
    (event,) = transition(order, actor=lifecycle.CUSTOMER, payment=PaymentStatus.PAID)
    assert order.payment_status == "paid"
    assert order.paid_at is not None
    assert (event.event, event.from_value, event.to_value) == (
        "payment.paid",
        "pending",
        "paid",
    )
    assert event.actor == "customer"
    assert event.order_id == order.id


def test_one_call_applies_paid_then_processing() -> None:
    order = _order()
    events = transition(
        order,
        actor=lifecycle.CUSTOMER,
        payment=PaymentStatus.PAID,
        ticketing=TicketingStatus.PROCESSING,
    )
    assert [event.event for event in events] == [
        "payment.paid",
        "ticketing.processing",
    ]
    assert order.ticketing_status == "processing"


def test_ticketing_needs_paid_and_booked() -> None:
    with pytest.raises(Conflict):
        transition(
            _order(), actor=lifecycle.SYSTEM, ticketing=TicketingStatus.PROCESSING
        )
    cancelled = _order(
        status=OrderStatus.CANCELLED,
        payment_status=PaymentStatus.PAID,
        cancelled_at=lifecycle.utcnow(),
    )
    with pytest.raises(Conflict):
        transition(
            cancelled, actor=lifecycle.SYSTEM, ticketing=TicketingStatus.PROCESSING
        )


def test_refused_move_leaves_order_untouched() -> None:
    """A refusal in the second move must not leave the first one applied."""
    order = _order(status=OrderStatus.CANCELLED, cancelled_at=lifecycle.utcnow())
    with pytest.raises(Conflict):
        transition(
            order,
            actor=lifecycle.SYSTEM,
            payment=PaymentStatus.PAID,
            ticketing=TicketingStatus.PROCESSING,
        )
    assert order.payment_status == "pending"
    assert order.paid_at is None


def test_customer_cannot_cancel_paid_but_staff_can() -> None:
    order = _order(
        payment_status=PaymentStatus.PAID, ticketing_status=TicketingStatus.FAILED
    )
    with pytest.raises(Conflict):
        transition(
            order,
            actor=lifecycle.CUSTOMER,
            status=OrderStatus.CANCELLED,
            cancel_reason=CancelReason.CUSTOMER,
        )
    (event,) = transition(
        order,
        actor=STAFF,
        status=OrderStatus.CANCELLED,
        cancel_reason=CancelReason.STAFF,
    )
    assert order.status == "cancelled"
    assert order.cancelled_at is not None
    assert order.cancel_reason == "staff"
    assert event.event == "order.cancelled"


def test_cancel_blocked_while_ticketing_or_confirming() -> None:
    for ticketing in (TicketingStatus.PROCESSING, TicketingStatus.TICKETED):
        order = _order(payment_status=PaymentStatus.PAID, ticketing_status=ticketing)
        with pytest.raises(Conflict):
            transition(order, actor=STAFF, status=OrderStatus.CANCELLED)
    with pytest.raises(Conflict):
        transition(
            _order(),
            actor=lifecycle.SYSTEM,
            status=OrderStatus.CANCELLED,
            open_attempt=lifecycle.CONFIRMING,
        )


def test_failed_to_processing_allowed_and_failed_to_ticketed_needs_paid() -> None:
    order = _order(
        payment_status=PaymentStatus.PAID, ticketing_status=TicketingStatus.FAILED
    )
    transition(order, actor=STAFF, ticketing=TicketingStatus.PROCESSING)
    assert order.ticketing_status == "processing"

    late = _order(
        payment_status=PaymentStatus.PAID, ticketing_status=TicketingStatus.FAILED
    )
    transition(late, actor=lifecycle.SYSTEM, ticketing=TicketingStatus.TICKETED)
    assert late.ticketed_at is not None

    refunded = _order(
        payment_status=PaymentStatus.REFUNDED, ticketing_status=TicketingStatus.FAILED
    )
    with pytest.raises(Conflict):
        transition(refunded, actor=lifecycle.SYSTEM, ticketing=TicketingStatus.TICKETED)


def test_refund_marks_are_staff_only_and_not_for_ticketed() -> None:
    order = _order(
        payment_status=PaymentStatus.PAID, ticketing_status=TicketingStatus.FAILED
    )
    with pytest.raises(Conflict):
        transition(order, actor=lifecycle.CUSTOMER, payment=PaymentStatus.REFUNDING)
    transition(order, actor=STAFF, payment=PaymentStatus.REFUNDING)
    transition(order, actor=STAFF, payment=PaymentStatus.REFUND_FAILED)
    transition(order, actor=STAFF, payment=PaymentStatus.REFUNDED)
    assert order.payment_status == "refunded"

    ticketed = _order(
        payment_status=PaymentStatus.PAID, ticketing_status=TicketingStatus.TICKETED
    )
    with pytest.raises(Conflict):
        transition(ticketed, actor=STAFF, payment=PaymentStatus.REFUNDING)


def test_paid_is_recorded_even_on_cancelled() -> None:
    """A money fact is never refused — the customer then reads
    ``ticketing_failed``: money taken, no ticket coming, talk to support."""
    order = _order(
        status=OrderStatus.CANCELLED,
        cancel_reason=CancelReason.EXPIRED,
        cancelled_at=lifecycle.utcnow(),
    )
    transition(order, actor=lifecycle.SYSTEM, payment=PaymentStatus.PAID)
    assert stage_of(order) is Stage.TICKETING_FAILED


# --- the public status ---------------------------------------------------------------


def test_status_vocabulary_is_six_words_in_screen_order() -> None:
    """The contract: six values, listed the way the panel shows them."""
    assert [status.value for status in Stage] == [
        "booked",
        "ticket_waiting",
        "ticketed",
        "ticketing_failed",
        "refunded",
        "cancelled",
    ]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # Held, not paid — whatever the last attempt did.
        ({}, Stage.BOOKED),
        ({"payment_status": "failed"}, Stage.BOOKED),
        # Paid; GTS has not answered, or has not been asked yet.
        ({"payment_status": "paid"}, Stage.TICKET_WAITING),
        (
            {"payment_status": "paid", "ticketing_status": "processing"},
            Stage.TICKET_WAITING,
        ),
        ({"payment_status": "paid", "ticketing_status": "ticketed"}, Stage.TICKETED),
        # Money taken, no ticket coming on its own.
        (
            {"payment_status": "paid", "ticketing_status": "failed"},
            Stage.TICKETING_FAILED,
        ),
        ({"payment_status": "refunding"}, Stage.TICKETING_FAILED),
        ({"payment_status": "refund_failed"}, Stage.TICKETING_FAILED),
        (
            {"status": "cancelled", "payment_status": "paid", "cancelled_at": "x"},
            Stage.TICKETING_FAILED,
        ),
        (
            {"status": "cancelled", "payment_status": "refunding", "cancelled_at": "x"},
            Stage.TICKETING_FAILED,
        ),
        # The money went back — terminal, wherever the booking stands.
        ({"payment_status": "refunded"}, Stage.REFUNDED),
        (
            {"status": "cancelled", "payment_status": "refunded", "cancelled_at": "x"},
            Stage.REFUNDED,
        ),
        # Released before any money moved; ``cancel_reason`` keeps the why.
        (
            {"status": "cancelled", "cancel_reason": "expired", "cancelled_at": "x"},
            Stage.CANCELLED,
        ),
        (
            {"status": "cancelled", "cancel_reason": "customer", "cancelled_at": "x"},
            Stage.CANCELLED,
        ),
        (
            {"status": "cancelled", "payment_status": "failed", "cancelled_at": "x"},
            Stage.CANCELLED,
        ),
    ],
)
def test_status_table(overrides: dict[str, Any], expected: Stage) -> None:
    if overrides.get("cancelled_at") == "x":
        overrides = {**overrides, "cancelled_at": lifecycle.utcnow()}
    assert stage_of(_order(**overrides)) is expected


def test_status_is_total_over_the_three_columns() -> None:
    """Every combination the columns can hold has an answer — including the
    ones the guards never let happen, which must land on the side that asks
    a human to look rather than raise. Checked against what the customer must
    never be told, not against the function's own branches."""
    no_money = {PaymentStatus.PENDING, PaymentStatus.FAILED}
    for status in OrderStatus:
        for payment in PaymentStatus:
            for ticketing in TicketingStatus:
                order = _order(
                    status=status,
                    payment_status=payment,
                    ticketing_status=ticketing,
                    cancelled_at=(
                        lifecycle.utcnow() if status == OrderStatus.CANCELLED else None
                    ),
                )
                result = stage_of(order)
                assert isinstance(result, Stage)
                if payment in no_money:
                    # Nothing was charged: never "contact us", never a refund.
                    assert result in (Stage.BOOKED, Stage.CANCELLED)
                else:
                    # Money moved: never "just pay" or a plain cancellation.
                    assert result not in (Stage.BOOKED, Stage.CANCELLED)
                if payment == PaymentStatus.REFUNDED:
                    assert result is Stage.REFUNDED
                if result is Stage.TICKETED:
                    assert ticketing == TicketingStatus.TICKETED
                if result is Stage.TICKET_WAITING:
                    assert status == OrderStatus.BOOKED
                    assert payment == PaymentStatus.PAID
