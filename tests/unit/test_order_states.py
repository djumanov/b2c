"""The transition table, checked as a table — no database, no service.

The point of keeping the state machine in a module of its own is that its
correctness is a property of a data structure, and a data structure can be
swept exhaustively. So this file does exactly that: all 12 × 12 ordered pairs,
every status classified, every closed status genuinely closed.

If a row is added to ``states.TRANSITIONS`` without a reason, one of these
sweeps notices. That is the whole intent — the money path's rules should be
hard to widen by accident (order-system/03-design.md §3.3).
"""

import itertools

import pytest

from app.modules.orders.states import (
    INITIAL_STATUS,
    STAMPED_AT,
    TRANSITIONS,
    Actor,
    ActorType,
    EventAction,
    OrderStatus,
    StatusClass,
    can,
    is_closed,
    status_class,
    targets_from,
)

#: Written out rather than derived from ``TRANSITIONS``: a sweep that builds
#: its expectation from the thing it is checking proves nothing. This is the
#: table in order-system/03-design.md §3.3, by hand.
EXPECTED_MOVES: set[tuple[OrderStatus, OrderStatus]] = {
    (OrderStatus.CREATED, OrderStatus.BOOKED),
    (OrderStatus.CREATED, OrderStatus.FAILED),
    (OrderStatus.CREATED, OrderStatus.NEEDS_ATTENTION),
    (OrderStatus.BOOKED, OrderStatus.PAID),
    (OrderStatus.BOOKED, OrderStatus.CANCELLED),
    (OrderStatus.BOOKED, OrderStatus.NEEDS_ATTENTION),
    (OrderStatus.PAID, OrderStatus.TICKETING),
    (OrderStatus.PAID, OrderStatus.REFUNDING),
    (OrderStatus.TICKETING, OrderStatus.TICKETING),
    (OrderStatus.TICKETING, OrderStatus.TICKETED),
    (OrderStatus.TICKETING, OrderStatus.REFUNDING),
    (OrderStatus.TICKETING, OrderStatus.NEEDS_ATTENTION),
    (OrderStatus.TICKETED, OrderStatus.REFUNDING),
    (OrderStatus.TICKETED, OrderStatus.VOIDED),
    (OrderStatus.REFUNDING, OrderStatus.REFUNDING),
    (OrderStatus.REFUNDING, OrderStatus.REFUNDED),
    (OrderStatus.REFUNDING, OrderStatus.PARTIALLY_REFUNDED),
    (OrderStatus.REFUNDING, OrderStatus.NEEDS_ATTENTION),
    (OrderStatus.NEEDS_ATTENTION, OrderStatus.TICKETED),
    (OrderStatus.NEEDS_ATTENTION, OrderStatus.REFUNDED),
    (OrderStatus.NEEDS_ATTENTION, OrderStatus.CANCELLED),
}


def test_the_table_is_the_table_in_the_document() -> None:
    assert {(item.source, item.target) for item in TRANSITIONS} == EXPECTED_MOVES


@pytest.mark.parametrize(
    ("source", "target"), list(itertools.product(OrderStatus, OrderStatus))
)
def test_every_pair_is_allowed_only_if_the_table_says_so(
    source: OrderStatus, target: OrderStatus
) -> None:
    """The sweep. 144 pairs, and 21 of them are legal."""
    assert can(source, target) is ((source, target) in EXPECTED_MOVES)


def test_nothing_transitions_back_into_the_initial_status() -> None:
    """An order is *born* ``created``; it can never return there. Otherwise a
    booked seat could be walked back to "we have not asked yet"."""
    assert INITIAL_STATUS is OrderStatus.CREATED
    assert not any(item.target is OrderStatus.CREATED for item in TRANSITIONS)


@pytest.mark.parametrize("status", list(OrderStatus))
def test_every_status_is_classified(status: OrderStatus) -> None:
    assert status_class(status) in set(StatusClass)


#: ``partially_refunded`` is reachable — synchronisation brings GTS's ``PRF``
#: — but nothing leads *out* of it until the partial-refund path is built
#: (v2, order-system/03-design.md §3.0). Until then it behaves like a closed
#: status while being classified ``SETTLED``, and the exemption is written down
#: here so that building v2 has to come back and delete it.
_NO_EXIT_YET = frozenset({OrderStatus.PARTIALLY_REFUNDED})


@pytest.mark.parametrize("status", list(OrderStatus))
def test_a_closed_status_has_no_way_out(status: OrderStatus) -> None:
    """ "Closed" is the strongest of the three kinds of terminal, and the only
    one that means it literally (order-system/01-research.md §1.4). The
    converse holds too, so a state that quietly became a dead end is caught."""
    if is_closed(status) or status in _NO_EXIT_YET:
        assert targets_from(status) == frozenset()
    else:
        assert targets_from(status) != frozenset()


def test_the_settled_statuses_can_still_start_something_new() -> None:
    """The distinction "closed" exists for: a refund against a ticketed order
    is not the machine running backwards."""
    assert status_class(OrderStatus.TICKETED) is StatusClass.SETTLED
    assert OrderStatus.REFUNDING in targets_from(OrderStatus.TICKETED)


def test_only_the_outward_steps_repeat_themselves() -> None:
    """A self-transition is a retry, and only the two states that talk to an
    outside system have anything to retry. Anywhere else it would hide a stuck
    order rather than describe one."""
    repeats = {item.source for item in TRANSITIONS if item.source is item.target}
    assert repeats == {OrderStatus.TICKETING, OrderStatus.REFUNDING}


def test_needs_attention_is_only_left_by_a_person() -> None:
    """Its entire purpose is being a queue somebody works through, so no
    automatic move may lead out of it."""
    assert status_class(OrderStatus.NEEDS_ATTENTION) is StatusClass.MANUAL
    out = [item for item in TRANSITIONS if item.source is OrderStatus.NEEDS_ATTENTION]
    assert {item.action for item in out} == {EventAction.ATTENTION_RESOLVED}


def test_every_action_belongs_to_a_move() -> None:
    """An action nothing can produce is a label that will drift out of date."""
    assert {item.action for item in TRANSITIONS} == set(EventAction)


def test_only_real_statuses_are_stamped() -> None:
    """``STAMPED_AT`` names columns on ``orders``; a key that is not a status
    would be a column write that never happens."""
    assert set(STAMPED_AT) <= set(OrderStatus)
    assert set(STAMPED_AT) == {
        OrderStatus.BOOKED,
        OrderStatus.PAID,
        OrderStatus.TICKETED,
        OrderStatus.CANCELLED,
    }


def test_an_actor_says_who_without_pointing_at_a_row() -> None:
    """Actors are stored as values — the history has to stay readable after the
    staff row is gone (``audit/models.py`` states the same at length)."""
    system = Actor.system("orders.ticket")
    assert (system.type, system.id, system.label) == (
        ActorType.SYSTEM,
        None,
        "orders.ticket",
    )

    with pytest.raises(AttributeError):
        system.label = "something else"  # type: ignore[misc]
