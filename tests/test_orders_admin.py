"""``/admin/orders/`` — the support inbox, and the three things support may do."""

from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mixins import utcnow
from app.modules.audit.models import AuditLog
from app.modules.customers.models import Customer
from app.modules.orders.lifecycle import Stage, stage_of
from app.modules.orders.models import (
    OrderEvent,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    TicketingStatus,
)
from tests.conftest import (
    FakeProvider,
    gts_order_body,
    make_order,
    mock_gts_order,
    mock_gts_signin,
    mock_gts_ticketing,
)

ADMIN_URL = "/api/v1/admin/orders/"
pytestmark = pytest.mark.usefixtures("gts_credential")


async def _failed_ticketing(
    session: AsyncSession, customer: Customer, **overrides: Any
) -> Any:
    fields: dict[str, Any] = {
        "payment_status": "paid",
        "paid_at": utcnow(),
        "ticketing_status": "failed",
        "ticketing_attempts": 1,
        "ticketing_requested_at": utcnow() - timedelta(minutes=10),
        "ticketing_error": "user don't have enough credits on account",
    }
    fields.update(overrides)
    return await make_order(session, customer, **fields)


async def test_customer_token_is_403_and_no_token_401(
    client: httpx.AsyncClient, customer_headers: dict[str, str]
) -> None:
    assert (await client.get(ADMIN_URL)).status_code == 401
    assert (await client.get(ADMIN_URL, headers=customer_headers)).status_code == 403


async def test_list_filters_and_the_inbox_is_the_customers_word(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    fine = await make_order(db_session, customer)
    failed = await _failed_ticketing(db_session, customer, gts_order_number=1001)
    refund_failed = await make_order(
        db_session,
        customer,
        payment_status="refund_failed",
        ticketing_status="failed",
        gts_order_number=1002,
    )
    cancelled_paid = await make_order(
        db_session,
        customer,
        status="cancelled",
        cancel_reason="staff",
        cancelled_at=utcnow(),
        payment_status="paid",
        gts_order_number=1003,
    )
    # The money went back: the ticket still reads ``failed``, the case is closed.
    refunded = await _failed_ticketing(
        db_session, customer, payment_status="refunded", gts_order_number=1004
    )

    everything = await client.get(ADMIN_URL, headers=staff_headers)
    assert everything.json()["meta"]["total"] == 5
    rows = {item["id"]: item for item in everything.json()["data"]}
    row = rows[str(fine.id)]
    assert row["customer_id"] == str(customer.id)
    assert row["gts_order_number"] == 61453
    # The customer's word and the three columns it was read from, side by side.
    assert row["status"] == "booked"
    assert row["booking_status"] == "booked"
    assert row["payment_status"] == "pending"
    assert row["ticketing_status"] == "pending"
    assert row["cancel_reason"] is None
    assert row["ticketing_error"] is None
    assert row["updated_at"] is not None
    paid_row = rows[str(cancelled_paid.id)]
    assert paid_row["status"] == "ticketing_failed"
    assert paid_row["booking_status"] == "cancelled"
    assert paid_row["payment_status"] == "paid"
    assert paid_row["cancel_reason"] == "staff"
    # The inbox reads without opening the row: GTS's reason is on it.
    assert rows[str(failed.id)]["ticketing_error"].startswith("user don't have")
    assert rows[str(refunded.id)]["status"] == "refunded"

    # ``status=ticketing_failed`` is the inbox — and a refunded order is not
    # in it, whatever its ticketing column still says.
    inbox = await client.get(
        ADMIN_URL, params={"status": "ticketing_failed"}, headers=staff_headers
    )
    ids = {item["id"] for item in inbox.json()["data"]}
    assert ids == {str(failed.id), str(refund_failed.id), str(cancelled_paid.id)}
    closed = await client.get(
        ADMIN_URL, params={"status": "refunded"}, headers=staff_headers
    )
    assert [item["id"] for item in closed.json()["data"]] == [str(refunded.id)]
    unknown = await client.get(
        ADMIN_URL, params={"status": "attention"}, headers=staff_headers
    )
    assert unknown.status_code == 422

    by_ticketing = await client.get(
        ADMIN_URL, params={"ticketing_status": "failed"}, headers=staff_headers
    )
    assert {item["id"] for item in by_ticketing.json()["data"]} == {
        str(failed.id),
        str(refund_failed.id),
        str(refunded.id),
    }
    # The two kinds of filter combine: the word, narrowed by a column.
    combined = await client.get(
        ADMIN_URL,
        params={"status": "ticketing_failed", "payment_status": "refund_failed"},
        headers=staff_headers,
    )
    assert [item["id"] for item in combined.json()["data"]] == [str(refund_failed.id)]
    by_number = await client.get(
        ADMIN_URL, params={"search": "1003"}, headers=staff_headers
    )
    assert [item["id"] for item in by_number.json()["data"]] == [str(cancelled_paid.id)]
    # The booking column is filtered under its own name, never as ``status``.
    by_booking = await client.get(
        ADMIN_URL, params={"booking_status": "cancelled"}, headers=staff_headers
    )
    assert [item["id"] for item in by_booking.json()["data"]] == [
        str(cancelled_paid.id)
    ]
    assert (
        await client.get(
            ADMIN_URL, params={"booking_status": "paid"}, headers=staff_headers
        )
    ).status_code == 422

    # Freshest trouble first: the oldest row, touched last, is the first shown.
    fine.route_summary = "TAS-IST"
    await db_session.commit()
    freshest = await client.get(
        ADMIN_URL, params={"ordering": "-updated_at"}, headers=staff_headers
    )
    assert freshest.json()["data"][0]["id"] == str(fine.id)
    newest = await client.get(ADMIN_URL, headers=staff_headers)
    assert newest.json()["data"][0]["id"] == str(refunded.id)


async def test_status_filter_lists_every_row_under_the_word_it_shows(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """All 48 column combinations, each listed under exactly the ``status``
    its own row reports — the filter and the word are one rule."""
    expected: dict[Stage, set[str]] = {stage: set() for stage in Stage}
    for booking in OrderStatus:
        for payment in PaymentStatus:
            for ticketing in TicketingStatus:
                order = await make_order(
                    db_session,
                    customer,
                    status=booking,
                    payment_status=payment,
                    ticketing_status=ticketing,
                    cancelled_at=(
                        utcnow() if booking == OrderStatus.CANCELLED else None
                    ),
                )
                expected[stage_of(order)].add(str(order.id))

    listed: dict[Stage, set[str]] = {}
    for stage in Stage:
        response = await client.get(
            ADMIN_URL,
            params={"status": stage.value, "page_size": 100},
            headers=staff_headers,
        )
        assert response.status_code == 200
        rows = response.json()["data"]
        assert {row["status"] for row in rows} <= {stage.value}
        listed[stage] = {row["id"] for row in rows}

    assert listed == expected
    assert sum(len(ids) for ids in listed.values()) == 48


async def test_detail_history_keeps_the_order_it_was_written_in(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Lines one commit writes share a ``created_at`` (``now()`` is the
    transaction's start); the history still reads in insertion order."""
    order = await make_order(db_session, customer, payment_status="paid")
    names = [f"step.{index}" for index in range(8)]
    db_session.add_all(
        OrderEvent(order_id=order.id, event=name, actor="system") for name in names
    )
    await db_session.commit()

    response = await client.get(f"{ADMIN_URL}{order.id}/", headers=staff_headers)

    events = response.json()["data"]["events"]
    assert len({event["created_at"] for event in events}) == 1
    assert [event["event"] for event in events] == names


async def test_detail_carries_events_and_attempts_without_reference(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    order = await _failed_ticketing(db_session, customer)
    db_session.add(
        PaymentAttempt(
            order_id=order.id,
            customer_id=customer.id,
            provider="sandbox",
            status="paid",
            amount=order.amount,
            currency=order.currency,
            card_last4="6604",
            provider_reference="sealed-secret",
            key_version=1,
            paid_at=utcnow(),
        )
    )
    db_session.add(
        OrderEvent(
            order_id=order.id,
            event="ticketing.failed",
            actor="system",
            to_value="failed",
        )
    )
    await db_session.commit()

    response = await client.get(f"{ADMIN_URL}{order.id}/", headers=staff_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["customer_id"] == str(customer.id)
    assert data["order"]["status"] == "ticketing_failed"
    assert data["order"]["booking_status"] == "booked"
    assert data["order"]["payment_status"] == "paid"
    assert data["order"]["ticketing_status"] == "failed"
    assert data["ticketing"]["error"].startswith("user don't have enough credits")
    assert data["ticketing_attempts"] == 1
    assert [event["event"] for event in data["events"]] == ["ticketing.failed"]
    (payment,) = data["payments"]
    assert payment["status"] == "paid"
    assert payment["card_last4"] == "6604"
    assert payment["amount"] == {"amount": "20.00", "currency": "UZS"}
    assert "sealed-secret" not in response.text
    assert "provider_reference" not in response.text


async def test_refund_marking_rules_and_audit(
    client: httpx.AsyncClient,
    customer: Customer,
    staff: Any,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    order = await _failed_ticketing(db_session, customer)
    url = f"{ADMIN_URL}{order.id}/refund/"

    refunding = await client.post(
        url,
        json={"status": "refunding", "note": "Payme cabinet, request #42"},
        headers=staff_headers,
    )
    assert refunding.status_code == 200
    # A refund under way is still "money taken, talk to us" to the customer.
    assert refunding.json()["data"]["order"]["status"] == "ticketing_failed"
    assert refunding.json()["data"]["order"]["payment_status"] == "refunding"

    # ``refunding → refunded`` is allowed; from ``refunded`` on, nothing is.
    refunded = await client.post(
        url, json={"status": "refunded"}, headers=staff_headers
    )
    assert refunded.status_code == 200
    data = refunded.json()["data"]
    assert data["order"]["payment_status"] == "refunded"
    assert data["order"]["status"] == "refunded"
    assert data["payment"]["status"] == "refunded"
    again = await client.post(url, json={"status": "refunding"}, headers=staff_headers)
    assert again.status_code == 409

    events = [
        event for event in data["events"] if event["event"].startswith("payment.")
    ]
    assert [event["to_value"] for event in events] == ["refunding", "refunded"]
    assert events[0]["actor"] == f"staff:{staff.id}"
    assert events[0]["note"] == "Payme cabinet, request #42"

    journal = (
        await db_session.scalars(select(AuditLog).order_by(AuditLog.created_at))
    ).all()
    # The two changes are journaled; the refused third call is not a change.
    assert [entry.action for entry in journal] == ["refund", "refund"]
    assert journal[0].resource_id == order.id
    assert journal[0].actor_id == staff.id
    assert journal[0].changes == {
        "payment_status": ["paid", "refunding"],
        "note": "Payme cabinet, request #42",
    }


async def test_refund_refused_for_ticketed_and_for_customers(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    ticketed = await make_order(
        db_session,
        customer,
        payment_status="paid",
        ticketing_status="ticketed",
        ticketed_at=utcnow(),
    )
    url = f"{ADMIN_URL}{ticketed.id}/refund/"
    assert (
        await client.post(url, json={"status": "refunding"}, headers=staff_headers)
    ).status_code == 409
    assert (
        await client.post(url, json={"status": "refunding"}, headers=customer_headers)
    ).status_code == 403
    assert (
        await client.post(url, json={"status": "paid"}, headers=staff_headers)
    ).status_code == 422


@respx.mock
async def test_retry_from_failed_posts_ticketing_once_with_staff_actor(
    client: httpx.AsyncClient,
    customer: Customer,
    staff: Any,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="BO"))
    ticketing = mock_gts_ticketing()
    order = await _failed_ticketing(db_session, customer, ticketing_attempts=2)

    response = await client.post(
        f"{ADMIN_URL}{order.id}/ticketing/retry/", headers=staff_headers
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ticketing"]["status"] == "ticketed"
    assert data["ticketing"]["tickets"][0]["ticket_number"] == "7653081297644"
    assert data["ticketing_attempts"] == 3  # staff are not bound by the sweep's cap
    assert ticketing.call_count == 1
    requested = next(
        event for event in data["events"] if event["event"] == "ticketing.requested"
    )
    assert requested["actor"] == f"staff:{staff.id}"


@respx.mock
async def test_retry_when_gts_already_ticketed_syncs_instead(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_order(
        gts_order_body(
            status="TI",
            passengers=[
                {"firstname": "A", "lastname": "B", "ticket_number": "7653081297644"}
            ],
        )
    )
    ticketing = mock_gts_ticketing()
    order = await _failed_ticketing(db_session, customer)

    response = await client.post(
        f"{ADMIN_URL}{order.id}/ticketing/retry/", headers=staff_headers
    )

    assert response.status_code == 200
    assert response.json()["data"]["ticketing"]["status"] == "ticketed"
    assert response.json()["data"]["ticketing"]["error"] is None
    assert ticketing.call_count == 0


@respx.mock
async def test_retry_refused_when_not_paid_or_hold_gone(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    ticketing = mock_gts_ticketing()

    mock_gts_order(gts_order_body(status="BO"))
    unpaid = await make_order(db_session, customer)
    assert (
        await client.post(
            f"{ADMIN_URL}{unpaid.id}/ticketing/retry/", headers=staff_headers
        )
    ).status_code == 409

    mock_gts_order(gts_order_body(status="CB"), order_number=777)
    gone = await _failed_ticketing(db_session, customer, gts_order_number=777)
    response = await client.post(
        f"{ADMIN_URL}{gone.id}/ticketing/retry/", headers=staff_headers
    )
    assert response.status_code == 409
    assert "released" in response.json()["errors"][0]["message"]
    assert ticketing.call_count == 0


@respx.mock
async def test_sync_settles_confirming_attempt_and_processing_ticket(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    from app.core.crypto import encrypt
    from app.providers.payments.base import PaymentOutcome

    mock_gts_signin()
    mock_gts_order(
        gts_order_body(
            status="TI",
            passengers=[
                {"firstname": "A", "lastname": "B", "ticket_number": "7653081297644"}
            ],
        )
    )
    mock_gts_ticketing()
    order = await make_order(db_session, customer)
    sealed, version = encrypt("ref-lost")
    db_session.add(
        PaymentAttempt(
            order_id=order.id,
            customer_id=customer.id,
            provider="fake",
            status="confirming",
            amount=order.amount,
            currency=order.currency,
            provider_reference=sealed,
            key_version=version,
        )
    )
    await db_session.commit()
    fake_provider.status_outcomes = [PaymentOutcome("paid", reference="ref-lost")]

    response = await client.post(f"{ADMIN_URL}{order.id}/sync/", headers=staff_headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["order"]["payment_status"] == "paid"
    # The settle asked for the ticket; GTS answered TI; the sync's own read
    # then found nothing left to do.
    assert data["ticketing"]["status"] == "ticketed"
    assert fake_provider.calls == [("status", {"reference": "ref-lost"})]


@respx.mock
async def test_sync_releases_a_hold_gts_let_go(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_order(gts_order_body(status="STATUS_VOID"))
    order = await make_order(db_session, customer)

    response = await client.post(f"{ADMIN_URL}{order.id}/sync/", headers=staff_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["order"]["status"] == "cancelled"
    assert data["order"]["booking_status"] == "cancelled"
    assert data["order"]["cancel_reason"] == "expired"


@respx.mock
async def test_sync_logs_a_ticket_issued_after_refund_and_leaves_it(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    mock_gts_signin()
    mock_gts_order(gts_order_body(status="TI"))
    order = await make_order(
        db_session, customer, payment_status="refunded", ticketing_status="failed"
    )

    with caplog.at_level(logging.ERROR):
        response = await client.post(
            f"{ADMIN_URL}{order.id}/sync/", headers=staff_headers
        )

    assert response.status_code == 200
    assert response.json()["data"]["ticketing"]["status"] == "failed"
    assert "ticketed_after_refund" in caplog.text


async def test_unknown_order_is_404(
    client: httpx.AsyncClient, staff_headers: dict[str, str]
) -> None:
    import uuid

    assert (
        await client.get(f"{ADMIN_URL}{uuid.uuid4()}/", headers=staff_headers)
    ).status_code == 404
