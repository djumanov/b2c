"""The ticketing step — one POST to GTS, settled by read-backs.

Every GTS answer the decision table knows is a test here: issued at once,
still working, refused, lost; and the sweep's rules for an order that waits
too long or that GTS shows no sign of having heard about.
"""

import logging
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mixins import utcnow
from app.modules.customers.models import Customer
from app.modules.orders import lifecycle, service
from app.modules.orders.models import Order, OrderEvent
from tests.conftest import (
    FakeProvider,
    gts_order_body,
    gts_ticketed_order,
    make_order,
    mock_gts_order,
    mock_gts_signin,
    mock_gts_ticketing,
)

pytestmark = pytest.mark.usefixtures("gts_credential")

ORDERS_URL = "/api/v1/public/orders/"
RAW_CARD = {"card": {"number": "4111111111111111", "expire": "1230"}}
TICKET = "7653081297644"


async def _pay(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str]
) -> dict[str, Any]:
    """Start and confirm in one go; the answer is the confirm's ``data``."""
    started = await client.post(
        f"{ORDERS_URL}{order.id}/payment/", json=RAW_CARD, headers=headers
    )
    assert started.status_code == 200, started.text
    payment_id = started.json()["data"]["payment"]["payment_id"]
    confirmed = await client.post(
        f"{ORDERS_URL}{order.id}/payment/confirm/",
        json={"payment_id": payment_id, "otp": "000000"},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    data: dict[str, Any] = confirmed.json()["data"]
    return data


async def _events(session: AsyncSession, order: Order) -> list[str]:
    rows = await session.scalars(
        select(OrderEvent.event)
        .where(OrderEvent.order_id == order.id)
        .order_by(OrderEvent.created_at)
    )
    return list(rows.all())


async def _paid_processing(
    session: AsyncSession,
    customer: Customer,
    *,
    requested_ago: timedelta,
    sends: int = 1,
) -> Order:
    """An order the confirm step left waiting on GTS some time ago."""
    return await make_order(
        session,
        customer,
        payment_status="paid",
        paid_at=utcnow() - requested_ago,
        ticketing_status="processing",
        ticketing_requested_at=utcnow() - requested_ago,
        ticketing_attempts=sends,
    )


# --- inline, right after the charge --------------------------------------------------


@respx.mock
async def test_paid_confirm_tickets_inline_ti(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    read = mock_gts_order(gts_order_body())
    ticketing = mock_gts_ticketing()
    order = await make_order(db_session, customer)

    data = await _pay(client, order, customer_headers)

    assert data["order"]["stage"] == "ticketed"
    assert data["order"]["message"] == "Chiptalaringiz tayyor."
    assert data["ticketing"]["status"] == "ticketed"
    assert data["ticketing"]["ticketed_at"] is not None
    assert data["ticketing"]["tickets"] == [
        {"passenger": "AZIMJON YUSUFOV", "ticket_number": TICKET}
    ]
    assert data["order_data"]["passengers"][0]["ticket_number"] == TICKET
    assert data["order"]["gts_status"] == "TI"
    assert ticketing.call_count == 1
    assert ticketing.calls.last.request.content == (
        b'{"order_number":61453,"payment_method":"deposit"}'
    )
    # The ``TI`` answer carried the order: no read-back GET after it.
    assert read.call_count == 1
    await db_session.refresh(order)
    assert order.ticketing_attempts == 1
    assert order.ticketing_requested_at is not None


@respx.mock
async def test_answer_pw_stays_processing_with_wait_message(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_ticketing(gts_ticketed_order(status="PW", passengers=[]))
    order = await make_order(db_session, customer)

    data = await _pay(client, order, customer_headers)

    assert data["order"]["stage"] == "ticketing"
    assert data["order"]["message"].startswith("To'lovingiz muvaffaqiyatli")
    assert "kuting" in data["order"]["message"]
    assert data["ticketing"]["status"] == "processing"
    assert data["ticketing"]["tickets"] == []
    assert data["order"]["gts_status"] == "PW"


@respx.mock
async def test_gts_refusal_reads_back_before_failing(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """GTS refused the request but its order says ``TI`` — a refused re-send
    of an already issued ticket must not fail a ticketed order."""
    mock_gts_signin()
    read = mock_gts_order(gts_order_body())
    mock_gts_ticketing(error="TICKETING: order is already ticketed")
    order = await make_order(db_session, customer)
    read.side_effect = [
        httpx.Response(200, json={"status": "success", "data": gts_order_body()}),
        httpx.Response(
            200,
            json={"status": "success", "data": gts_order_body(status="TI")},
        ),
    ]

    data = await _pay(client, order, customer_headers)

    assert data["ticketing"]["status"] == "ticketed"
    assert read.call_count == 2


@respx.mock
async def test_gts_refusal_with_hold_intact_is_failed_with_support_message(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    from app.modules.settings import cache as settings_cache

    await settings_cache.write(
        {"site": {"support_phone": "+998 71 200 00 00", "support_email": "help@x.uz"}}
    )
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_ticketing(error="TICKETING: provider rejected the fare")
    order = await make_order(db_session, customer)

    data = await _pay(client, order, customer_headers)

    assert data["order"]["payment_status"] == "paid"
    assert data["order"]["ticketing_status"] == "failed"
    assert data["order"]["stage"] == "ticketing_failed"
    assert data["order"]["message"].endswith(
        "support xizmatiga murojaat qiling: +998 71 200 00 00, help@x.uz."
    )
    assert data["ticketing"]["error"] == "TICKETING: provider rejected the fare"
    assert data["order"]["status"] == "booked"  # never auto-cancelled
    assert await _events(db_session, order) == [
        "payment.started",
        "payment.confirming",
        "payment.paid",
        "ticketing.processing",
        "ticketing.requested",
        "ticketing.failed",
    ]


@respx.mock
async def test_deposit_empty_is_failed_and_logged_as_error(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    mock_gts_ticketing(
        error="BOOKING: save_booking 403: user don't have enough credits on account"
    )
    order = await make_order(db_session, customer)

    with caplog.at_level(logging.ERROR):
        data = await _pay(client, order, customer_headers)

    assert data["ticketing"]["status"] == "failed"
    assert "gts_deposit_empty" in caplog.text


@respx.mock
async def test_timeout_stays_processing_without_second_post(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body())
    ticketing = respx.post("https://gts.test/v1/content/ticketing/").mock(
        side_effect=httpx.ReadTimeout("GTS is slow")
    )
    order = await make_order(db_session, customer)

    data = await _pay(client, order, customer_headers)

    assert data["order"]["payment_status"] == "paid"
    assert data["ticketing"]["status"] == "processing"
    assert data["order"]["stage"] == "ticketing"
    assert ticketing.call_count == 1

    # A fresh re-check sees ``BO`` within the grace period: still waiting,
    # and above all no second POST.
    assert await service.recheck_processing(db_session) == 0
    assert ticketing.call_count == 1


# --- the sweep: read-backs --------------------------------------------------------


@respx.mock
async def test_recheck_ti_becomes_ticketed(
    customer: Customer, db_session: AsyncSession
) -> None:
    mock_gts_signin()
    mock_gts_order(
        gts_order_body(
            status="TI",
            passengers=[{"firstname": "A", "lastname": "B", "ticket_number": TICKET}],
        )
    )
    order = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=2)
    )

    assert await service.recheck_processing(db_session) == 1
    await db_session.refresh(order)
    assert order.ticketing_status == "ticketed"
    assert order.ticketed_at is not None
    assert order.gts_response["passengers"][0]["ticket_number"] == TICKET
    event = await db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == order.id, OrderEvent.event == "ticketing.ticketed"
        )
    )
    assert event is not None and event.actor == "system"


@respx.mock
async def test_recheck_pw_waits_then_fails_after_max_wait(
    customer: Customer, db_session: AsyncSession
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="PW"))
    waiting = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=10)
    )
    stale = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=31)
    )
    stale.gts_order_number = 61454
    await db_session.commit()
    mock_gts_order(gts_order_body(status="PW"), order_number=61454)

    assert await service.recheck_processing(db_session) == 1
    await db_session.refresh(waiting)
    await db_session.refresh(stale)
    assert waiting.ticketing_status == "processing"
    assert waiting.gts_checked_at is not None
    assert stale.ticketing_status == "failed"
    assert (
        stale.ticketing_error == "GTS did not issue the ticket within the waiting time"
    )


@respx.mock
async def test_recheck_bo_after_grace_resends_once_then_fails(
    customer: Customer, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="BO"))
    ticketing = mock_gts_ticketing(gts_ticketed_order(status="PW", passengers=[]))
    order = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=6)
    )

    # First pass: GTS shows no sign of the request — send it again, once.
    assert await service.recheck_processing(db_session) == 1
    assert ticketing.call_count == 1
    await db_session.refresh(order)
    assert order.ticketing_attempts == 2
    assert order.ticketing_status == "processing"
    assert order.gts_status == "PW"

    # The re-send also went unheard: stop guessing, hand it to a human.
    order.ticketing_requested_at = utcnow() - timedelta(minutes=6)
    order.gts_status = "BO"
    await db_session.commit()
    with caplog.at_level(logging.WARNING):
        assert await service.recheck_processing(db_session) == 1
    assert ticketing.call_count == 1
    await db_session.refresh(order)
    assert order.ticketing_status == "failed"
    assert order.ticketing_error == "the ticketing request was not confirmed by GTS"


@respx.mock
async def test_recheck_cb_fails(customer: Customer, db_session: AsyncSession) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="CB"))
    order = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=1)
    )

    assert await service.recheck_processing(db_session) == 1
    await db_session.refresh(order)
    assert order.ticketing_status == "failed"
    assert order.ticketing_error == "GTS status CB"
    assert order.payment_status == "paid"
    assert order.status == "booked"


@respx.mock
async def test_recheck_unread_waits_and_then_gives_up(
    customer: Customer, db_session: AsyncSession
) -> None:
    mock_gts_signin()
    mock_gts_order(None)
    fresh = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=1)
    )
    old = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=40)
    )
    old.gts_order_number = 61455
    await db_session.commit()
    mock_gts_order(None, order_number=61455)

    assert await service.recheck_processing(db_session) == 1
    await db_session.refresh(fresh)
    await db_session.refresh(old)
    assert fresh.ticketing_status == "processing"
    assert old.ticketing_status == "failed"


@respx.mock
async def test_apply_leaves_non_processing_orders_alone(
    customer: Customer, db_session: AsyncSession
) -> None:
    """The settle step moves ``processing`` orders only — a ``failed`` one
    that GTS ticketed late is staff's to sync, with the guard's blessing."""
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="TI"))
    order = await make_order(
        db_session, customer, payment_status="paid", ticketing_status="failed"
    )
    decision = await service._apply_ticketing(  # noqa: SLF001 - the settle step itself
        db_session, order.id, None, error=None, actor=lifecycle.SYSTEM
    )
    assert decision == "wait"
    await db_session.refresh(order)
    assert order.ticketing_status == "failed"


@respx.mock
async def test_paid_pending_safety_net_asks_for_the_ticket(
    customer: Customer, db_session: AsyncSession
) -> None:
    """A worker that died between the payment commit and the POST."""
    mock_gts_signin()
    ticketing = mock_gts_ticketing()
    order = await make_order(
        db_session, customer, payment_status="paid", paid_at=utcnow()
    )

    assert await service.ticket_paid_pending(db_session) == 1
    assert ticketing.call_count == 1
    await db_session.refresh(order)
    assert order.ticketing_status == "ticketed"


@respx.mock
async def test_cancelled_order_is_never_ticketed(
    customer: Customer, db_session: AsyncSession
) -> None:
    from app.api.errors import Conflict

    mock_gts_signin()
    ticketing = mock_gts_ticketing()
    order = await make_order(
        db_session,
        customer,
        status="cancelled",
        cancel_reason="staff",
        cancelled_at=utcnow(),
        payment_status="paid",
    )
    with pytest.raises(Conflict):
        await service.ticket(db_session, order.id, actor=lifecycle.SYSTEM)
    assert ticketing.call_count == 0


@respx.mock
async def test_two_rechecks_move_an_order_once(
    customer: Customer, db_session: AsyncSession
) -> None:
    import asyncio

    from app.db.session import get_sessionmaker

    mock_gts_signin()
    read = mock_gts_order(gts_order_body(status="TI"))
    order = await _paid_processing(
        db_session, customer, requested_ago=timedelta(minutes=2)
    )

    async def sweep() -> int:
        async with get_sessionmaker()() as session:
            return await service.recheck_processing(session)

    moved = await asyncio.gather(sweep(), sweep())

    assert sorted(moved) == [0, 1]
    events = await _events(db_session, order)
    assert events.count("ticketing.ticketed") == 1
    assert read.call_count >= 1
