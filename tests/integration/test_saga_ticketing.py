"""Ticketing — the step where money has already moved (order-system §3.7).

Every case here is a way the provider can say no, and the point of the file is
that they do **not** all mean the same thing. An empty deposit is ours to fix
and a fare that no longer exists is the customer's to be refunded for; treating
them alike would return a whole day of orders for an accounting problem (``O5``).

The vertical is replaced by a double registered under ``flight``, so what is
being tested is the saga rather than GTS's wording. The wording itself is
``tests/unit/test_flight_order_ops.py``'s subject, and ``classify`` here is the
real adapter's — the one place the two files meet.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from app.api.errors import UpstreamError, UpstreamTimeout
from app.core.money import Money
from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderRefund
from app.modules.orders.states import OrderStatus
from app.providers.products.base import ProductCode, registry
from app.providers.products.flight import FlightAdapter
from app.providers.products.orders import (
    BookingResult,
    CancelResult,
    FailureClass,
    RepriceResult,
    TicketingResult,
    TravelerRef,
)
from app.tasks import orders as tasks

PAID = Decimal("1250000.00")


@dataclass
class FakeOperations:
    """A vertical that answers as told and remembers what was asked."""

    code: ProductCode = ProductCode.FLIGHT
    quoted: Decimal = PAID
    reprice_fails: Exception | None = None
    ticket_fails: Exception | None = None
    cancel_fails: Exception | None = None
    #: One entry per traveller; ``None`` means that one got no ticket.
    issued: tuple[str | None, ...] = ("7653081297644",)
    calls: list[str] = field(default_factory=list)

    def supports(self) -> frozenset[Any]:
        return frozenset()

    async def book(self, client: Any, payload: dict[str, Any]) -> BookingResult:
        raise AssertionError("ticketing never books")

    async def cancel(self, client: Any, payload: dict[str, Any]) -> CancelResult:
        # Compensation releases the reservation through this
        # (``test_saga_refund``); ticketing itself never calls it.
        self.calls.append("cancel")
        if self.cancel_fails is not None:
            raise self.cancel_fails
        return CancelResult(provider_status="CB", raw={"order": {"status": "CB"}})

    async def reprice(self, client: Any, order_number: str) -> RepriceResult:
        self.calls.append("reprice")
        if self.reprice_fails is not None:
            raise self.reprice_fails
        return RepriceResult(
            total=Money(amount=self.quoted, currency="UZS"), raw={"repriced": True}
        )

    async def ticket(self, client: Any, order_number: str) -> TicketingResult:
        self.calls.append("ticket")
        if self.ticket_fails is not None:
            raise self.ticket_fails
        return TicketingResult(
            provider_status="TI",
            status=OrderStatus.TICKETED,
            travelers=tuple(
                TravelerRef(position=index, ticket_number=number)
                for index, number in enumerate(self.issued, start=1)
            ),
            raw={"order": {"status": "TI"}},
        )

    def classify(self, failure: Any) -> FailureClass:
        # The real one: how a failure is worded is the adapter's knowledge, and
        # a double that guessed differently would test nothing.
        return FlightAdapter().classify(failure)

    def status_map(self) -> dict[str, OrderStatus]:
        return FlightAdapter().status_map()


@pytest.fixture
def operations(connection: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Stand in for the flight vertical, and put the real one back after.

    The task opens its **own** session, as it does on a worker. Here that
    factory is bound to the test's connection, so what the test wrote — and
    never committed — is visible to it; otherwise every case below would be
    ticketing an order the task cannot see.
    """
    fake = FakeOperations()
    registry.register(fake)

    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: factory)

    async def no_credential_needed(session: object) -> object:
        return object()

    monkeypatch.setattr(tasks.integrations_service, "gts_client", no_credential_needed)
    yield fake
    registry.register(FlightAdapter())


_counter = 0


async def _paid(session: AsyncSession, customer: Customer, **overrides: Any) -> Order:
    """An order that has been paid for and is waiting to be ticketed."""
    global _counter
    _counter += 1
    fields: dict[str, Any] = {
        "order_no": f"B2C-2608-5{_counter:05d}",
        "customer_id": customer.id,
        "product": "flight",
        "status": OrderStatus.PAID.value,
        "provider_order_number": f"5{_counter:05d}",
        "provider_status": "BO",
        "amount_total": PAID,
        "amount_paid": PAID,
        "currency": "UZS",
        "paid_at": datetime.now(UTC),
        "ticket_time_limit_at": datetime.now(UTC) + timedelta(hours=6),
        "travelers": [{"position": 1, "last_name": "YUSUFOV", "ticket_number": None}],
        **overrides,
    }
    order = Order(**fields)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def _events(session: AsyncSession, order: Order) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT action FROM order_events WHERE order_id = :id ORDER BY created_at"
        ),
        {"id": order.id},
    )
    return [row[0] for row in rows]


# --- the happy path --------------------------------------------------------------


async def test_a_paid_order_becomes_a_ticketed_one(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    order = await _paid(session, customer)

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "ticketed"
    assert order.ticketed_at is not None
    assert order.provider_status == "TI"
    # Nothing is pending any more, so the sweep will not pick it up again.
    assert order.next_attempt_at is None
    # The ticket number lands on the traveller it belongs to, beside the fields
    # the booking answer put there.
    assert order.travelers[0]["ticket_number"] == "7653081297644"
    assert order.travelers[0]["last_name"] == "YUSUFOV"
    # Repriced first, then ticketed — never the other way round.
    assert operations.calls == ["reprice", "ticket"]
    assert await _events(session, order) == [
        "ticketing.started",
        "ticketing.succeeded",
    ]


async def test_ticketing_an_order_twice_issues_one_set_of_tickets(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """A direct send and a sweep can race, and a provider's retry can arrive
    late. The task re-reads under a lock and leaves quietly."""
    order = await _paid(session, customer)

    await tasks._ticket(order.id)
    await tasks._ticket(order.id)

    assert operations.calls == ["reprice", "ticket"]
    await session.refresh(order)
    assert order.status == "ticketed"


async def test_an_order_that_is_not_paid_for_is_left_alone(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    order = await _paid(session, customer, status=OrderStatus.BOOKED.value)

    await tasks._ticket(order.id)

    assert operations.calls == []
    await session.refresh(order)
    assert order.status == "booked"


# --- repricing (``O6``) -----------------------------------------------------------


async def test_a_fare_that_rose_stops_the_ticket(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """Buying at a price nobody agreed to is worse than not buying: the money
    goes back instead."""
    operations.quoted = PAID + Decimal("50000.00")
    order = await _paid(session, customer)

    await tasks._ticket(order.id)

    assert operations.calls == ["reprice"]
    await session.refresh(order)
    assert order.status == "refunding"
    assert "1300000" in (order.failure_message or "")


async def test_a_fare_within_the_tolerance_still_tickets(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """The tolerance is a client's commercial choice, so it is a setting and
    not a constant (PROJECT.md §7)."""
    from app.modules.settings import service as settings_service
    from app.modules.settings.schemas import OrderSettingsIn

    await settings_service.update_order_settings(
        session, OrderSettingsIn(reprice_tolerance=Decimal("50000.00"))
    )
    operations.quoted = PAID + Decimal("50000.00")
    order = await _paid(session, customer)

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "ticketed"


async def test_a_reprice_that_fails_is_a_retry_not_a_refund(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """Repricing is a read. Not being able to do it says nothing about the
    fare, and refunding over it would throw away a perfectly good booking."""
    operations.reprice_fails = UpstreamTimeout("GTS did not answer in time")
    order = await _paid(session, customer)

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "ticketing"
    assert order.next_attempt_at is not None


# --- the ways a ticket can fail to come out ---------------------------------------


async def test_a_transient_failure_is_retried_with_a_gap(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    operations.ticket_fails = UpstreamError("Service temporarily unavailable")
    order = await _paid(session, customer)

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "ticketing"
    assert order.attempts == 1
    assert order.next_attempt_at is not None
    assert order.next_attempt_at > datetime.now(UTC)
    assert await _events(session, order) == ["ticketing.started", "ticketing.retry"]


async def test_an_empty_deposit_is_ours_to_fix_not_the_customers_to_lose(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """The failure ``O5`` exists for. A top-up fixes every order waiting on it
    at once, and refunding them all instead would be an expensive way to
    report an accounting problem."""
    operations.ticket_fails = UpstreamError(
        "BOOKING: save_booking 403: user don't have enough credits on account"
    )
    order = await _paid(session, customer)

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "ticketing"
    assert order.next_attempt_at is not None


async def test_a_terminal_failure_sends_the_money_back(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    operations.ticket_fails = UpstreamError("Fare is no longer available")
    order = await _paid(session, customer)

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "refunding"
    # The compensation is written in the same transaction and scheduled at
    # once: a customer who paid for nothing is not made to wait for anybody's
    # signature (order-system/03-design.md T11).
    assert order.next_attempt_at is not None
    refund = await session.scalar(
        select(OrderRefund).where(OrderRefund.order_id == order.id)
    )
    assert refund is not None
    assert (refund.kind, refund.status) == ("auto", "approved")
    assert refund.amount == PAID
    assert refund.penalty_amount == Decimal(0)
    assert await _events(session, order) == ["ticketing.started", "ticketing.failed"]


async def test_a_deadline_that_has_passed_stops_the_attempt(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """The margin is subtracted from the provider's own deadline, so ticketing
    gives up while there is still time to release the seat rather than racing
    the hold to the second."""
    order = await _paid(
        session, customer, ticket_time_limit_at=datetime.now(UTC) + timedelta(minutes=5)
    )

    await tasks._ticket(order.id)

    assert operations.calls == []
    await session.refresh(order)
    assert order.status == "refunding"


async def test_no_time_left_to_retry_is_the_same_as_no_time_left(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """A retry scheduled past the deadline is a retry that will never run."""
    operations.ticket_fails = UpstreamError("Service temporarily unavailable")
    order = await _paid(
        session,
        customer,
        ticket_time_limit_at=datetime.now(UTC) + timedelta(minutes=30, seconds=20),
    )

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "refunding"


async def test_tickets_for_some_travellers_is_not_a_smaller_success(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """Some passengers hold tickets and some do not, and nothing automatic can
    put that right — least of all a refund of the whole order."""
    operations.issued = ("7653081297644", None)
    order = await _paid(
        session,
        customer,
        travelers=[
            {"position": 1, "last_name": "YUSUFOV", "ticket_number": None},
            {"position": 2, "last_name": "SOFTCON", "ticket_number": None},
        ],
    )

    await tasks._ticket(order.id)

    await session.refresh(order)
    assert order.status == "needs_attention"
    assert order.attention_reason == "partial_ticketing"
    assert order.travelers[0]["ticket_number"] == "7653081297644"
    assert order.travelers[1]["ticket_number"] is None


# --- the sweep -------------------------------------------------------------------


async def test_the_sweep_sends_what_is_due(
    session: AsyncSession,
    customer: Customer,
    operations: FakeOperations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(tasks.ticket, "delay", lambda order_id: sent.append(order_id))

    due = await _paid(
        session, customer, next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    await _paid(
        session, customer, next_attempt_at=datetime.now(UTC) + timedelta(hours=1)
    )
    await _paid(session, customer)

    await tasks._run_due()

    assert sent == [str(due.id)]


async def test_the_sweep_clears_a_schedule_it_cannot_act_on(
    session: AsyncSession,
    customer: Customer,
    operations: FakeOperations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A schedule left on a status nothing automatic acts on. Leaving it would
    make the sweep spin every thirty seconds; the status itself is what a
    person will act on."""
    sent: list[str] = []
    monkeypatch.setattr(tasks.ticket, "delay", lambda order_id: sent.append(order_id))
    monkeypatch.setattr(tasks.refund, "delay", lambda order_id: sent.append(order_id))
    stuck = await _paid(
        session,
        customer,
        status=OrderStatus.NEEDS_ATTENTION.value,
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    await tasks._run_due()

    assert sent == []
    # ``expire_on_commit=False`` keeps the identity map's copy, and the sweep
    # ran in a session of its own — so ask the database again, not the cache.
    await session.refresh(stuck)
    assert stuck.next_attempt_at is None


async def test_an_order_that_vanished_is_not_an_error(
    session: AsyncSession, operations: FakeOperations
) -> None:
    await tasks._ticket(uuid.uuid4())

    assert operations.calls == []


# --- holds nobody paid for -------------------------------------------------------


async def _booked(session: AsyncSession, customer: Customer, **overrides: Any) -> Order:
    return await _paid(
        session,
        customer,
        status=OrderStatus.BOOKED.value,
        amount_paid=Decimal(0),
        paid_at=None,
        **overrides,
    )


async def test_a_hold_whose_window_closed_gives_the_seat_back(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """Without this the hold simply lapses: the provider takes the seat back on
    its own schedule, and the order sits in ``booked`` telling the customer a
    payment is still expected."""
    order = await _booked(
        session, customer, ticket_time_limit_at=datetime.now(UTC) + timedelta(minutes=5)
    )

    await tasks._expire_unpaid()

    await session.refresh(order)
    assert order.status == "cancelled"
    assert order.cancellation_reason == "timelimit"
    assert order.cancelled_at is not None
    # The seat is handed back rather than left to lapse.
    assert operations.calls == ["cancel"]
    assert await _events(session, order) == ["booking.expired"]


async def test_a_hold_still_inside_its_window_is_left_alone(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """The margin is subtracted from the provider's deadline, not added: an
    order with hours left is nobody's business yet."""
    order = await _booked(session, customer)

    await tasks._expire_unpaid()

    await session.refresh(order)
    assert order.status == "booked"
    assert operations.calls == []


async def test_a_hold_with_no_deadline_gets_one_of_ours(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """A provider that named no deadline does not get to keep an order open for
    ever. The bound is ``hold_fallback_minutes`` and it is ours to set."""
    order = await _booked(
        session,
        customer,
        ticket_time_limit_at=None,
        created_at=datetime.now(UTC) - timedelta(hours=4),
    )

    await tasks._expire_unpaid()

    await session.refresh(order)
    assert order.status == "cancelled"


async def test_a_paid_order_is_never_expired(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """Money has moved. Whatever happens next, it is not a quiet cancellation."""
    order = await _paid(
        session, customer, ticket_time_limit_at=datetime.now(UTC) - timedelta(hours=1)
    )

    await tasks._expire_unpaid()

    await session.refresh(order)
    assert order.status == "paid"
    assert operations.calls == []


async def test_a_provider_that_will_not_release_does_not_hold_the_order_open(
    session: AsyncSession, customer: Customer, operations: FakeOperations
) -> None:
    """The hold has already lapsed on the provider's clock and it reclaims the
    seat on its own (GTS.md §11). An order left open would keep telling a
    customer to pay for something they can no longer have, so the refusal is
    logged and the order closes anyway."""
    operations.cancel_fails = UpstreamError("the provider is unavailable")
    order = await _booked(
        session, customer, ticket_time_limit_at=datetime.now(UTC) + timedelta(minutes=5)
    )

    await tasks._expire_unpaid()

    await session.refresh(order)
    assert order.status == "cancelled"
