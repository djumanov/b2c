"""``cancel/`` — the customer hands the seat back before paying.

GTS holds the seat, so GTS is told first and our record follows; the order
then reads ``cancelled`` with ``cancel_reason: customer``. The step is only
open while the order is unpaid and unticketed — an issued ticket is ``void``
or ``refund``, neither of which this release implements.

The cancellation answer carries no status, so what GTS now holds is learnt
from ``GET /v1/orders/{n}/``: that one read settles a refusal too, because
"already cancelled" is a cancelled order and not a failure. GTS is ``respx``;
the payment provider is the scripted ``FakeProvider``.
"""

import uuid
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderEvent, PaymentAttempt
from tests.conftest import (
    GTS,
    ORDER_NUMBER,
    FakeProvider,
    gts_order_body,
    make_customer,
    make_order,
    mock_gts_cancel,
    mock_gts_order,
    mock_gts_signin,
)

pytestmark = pytest.mark.usefixtures("gts_credential")

ORDERS_URL = "/api/v1/public/orders/"
RAW_CARD = {"card": {"number": "4111111111111111", "expire": "1230"}}


async def _cancel(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(f"{ORDERS_URL}{order.id}/cancel/", headers=headers)


async def _start(
    client: httpx.AsyncClient, order: Order, headers: dict[str, str]
) -> httpx.Response:
    return await client.post(
        f"{ORDERS_URL}{order.id}/payment/",
        json={"method": "fake", **RAW_CARD},
        headers=headers,
    )


async def _events(
    session: AsyncSession, order: Order
) -> list[tuple[str, dict[str, Any] | None]]:
    rows = await session.execute(
        select(OrderEvent.event, OrderEvent.data)
        .where(OrderEvent.order_id == order.id)
        .order_by(OrderEvent.created_at, OrderEvent.seq)
    )
    return [(event, data) for event, data in rows.all()]


async def _attempts(session: AsyncSession, order: Order) -> list[PaymentAttempt]:
    rows = await session.scalars(
        select(PaymentAttempt).where(PaymentAttempt.order_id == order.id)
    )
    return list(rows.all())


def _released_read() -> Any:
    """``GET /v1/orders/{n}/`` answering with the hold gone — GTS's ``CB``."""
    return mock_gts_order(gts_order_body(status="CB"))


# --- the ordinary cancellation ------------------------------------------------------


@respx.mock
async def test_cancel_tells_gts_first_and_records_what_it_did(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The POST goes to GTS, the order is read back, and only then does the
    row move — to ``cancelled`` by the customer, at GTS's own status."""
    mock_gts_signin()
    cancel = mock_gts_cancel()
    read = _released_read()
    order = await make_order(db_session, customer)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["order"]["status"] == "cancelled"
    assert data["order"]["cancel_reason"] == "customer"
    assert data["order"]["gts_status"] == "CB"
    assert data["order"]["cancelled_at"] is not None
    assert data["payment"]["status"] == "cancelled"
    assert data["order"]["message"]

    assert cancel.call_count == 1
    assert cancel.calls.last.request.content == b'{"order_number":%d}' % ORDER_NUMBER
    assert read.call_count == 1

    await db_session.refresh(order)
    assert order.status == "cancelled"
    assert order.cancel_reason == "customer"
    assert order.cancelled_at is not None
    assert order.gts_status == "CB"
    assert await _events(db_session, order) == [
        ("order.cancelled", {"gts_status": "CB"})
    ]


@respx.mock
async def test_cancelling_twice_is_the_order_as_it_stands(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A retried tap costs nothing: no second call to GTS, no second event."""
    mock_gts_signin()
    cancel = mock_gts_cancel()
    _released_read()
    order = await make_order(db_session, customer)

    first = await _cancel(client, order, customer_headers)
    again = await _cancel(client, order, customer_headers)

    assert first.status_code == 200, first.text
    assert again.status_code == 200, again.text
    assert again.json()["data"]["order"]["status"] == "cancelled"
    assert cancel.call_count == 1
    await db_session.refresh(order)
    assert await _events(db_session, order) == [
        ("order.cancelled", {"gts_status": "CB"})
    ]


@respx.mock
async def test_the_code_already_sent_is_abandoned(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    """Nobody will type that code now, and the index that allows one open
    attempt per order should not go on holding it."""
    mock_gts_signin()
    mock_gts_cancel()
    read = mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)
    started = await _start(client, order, customer_headers)
    assert started.json()["data"]["payment"]["status"] == "awaiting_otp"
    read.mock(
        return_value=httpx.Response(
            200, json={"status": "success", "data": gts_order_body(status="CB")}
        )
    )

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["payment"]["status"] == "cancelled"
    (attempt,) = await _attempts(db_session, order)
    assert attempt.status == "abandoned"


@respx.mock
async def test_a_cancelled_order_cannot_be_paid(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    fake_provider: FakeProvider,
) -> None:
    mock_gts_signin()
    mock_gts_cancel()
    _released_read()
    order = await make_order(db_session, customer)
    assert (await _cancel(client, order, customer_headers)).status_code == 200

    response = await _start(client, order, customer_headers)

    assert response.status_code == 409, response.text
    assert response.json()["errors"][0]["code"] == "conflict"


# --- what may not be cancelled ------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("ticketing_status", ["pending", "processing"])
async def test_a_paid_order_is_not_the_customers_to_cancel(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    ticketing_status: str,
) -> None:
    """Money has moved: this is a refund question, and GTS is not asked."""
    mock_gts_signin()
    cancel = mock_gts_cancel()
    order = await make_order(
        db_session,
        customer,
        payment_status="paid",
        ticketing_status=ticketing_status,
    )

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 409, response.text
    assert response.json()["errors"][0]["code"] == "conflict"
    assert cancel.call_count == 0
    await db_session.refresh(order)
    assert order.status == "booked"


@respx.mock
async def test_a_charge_being_confirmed_holds_the_cancellation(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Nothing about an order may change while its money is in flight — and
    GTS is not asked to release a seat that may be about to be paid for."""
    mock_gts_signin()
    cancel = mock_gts_cancel()
    order = await make_order(db_session, customer)
    db_session.add(
        PaymentAttempt(
            order_id=order.id,
            customer_id=customer.id,
            provider="fake",
            status="confirming",
            amount=order.amount,
            currency=order.currency,
        )
    )
    await db_session.commit()

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 409, response.text
    assert cancel.call_count == 0
    await db_session.refresh(order)
    assert order.status == "booked"


@respx.mock
async def test_somebody_elses_order_is_a_404(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    cancel = mock_gts_cancel()
    stranger = await make_customer(db_session, "stranger@example.com")
    order = await make_order(db_session, stranger)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 404, response.text
    assert cancel.call_count == 0


async def test_an_unknown_order_is_a_404(
    client: httpx.AsyncClient, customer_headers: dict[str, str]
) -> None:
    response = await client.post(
        f"{ORDERS_URL}{uuid.uuid4()}/cancel/", headers=customer_headers
    )

    assert response.status_code == 404, response.text


# --- what GTS says, and what it fails to say ----------------------------------------


@respx.mock
async def test_a_refusal_over_a_hold_that_is_already_gone_is_a_cancellation(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GTS refuses to cancel an order it has already released. Read back, that
    refusal is the outcome the customer asked for, not a failure."""
    mock_gts_signin()
    cancel = mock_gts_cancel(error="CANCEL: order is already canceled")
    _released_read()
    order = await make_order(db_session, customer)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["order"]["status"] == "cancelled"
    assert cancel.call_count == 1
    await db_session.refresh(order)
    assert order.status == "cancelled"
    assert order.cancel_reason == "customer"


@respx.mock
async def test_a_refusal_over_a_live_hold_changes_nothing(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GTS said no and still holds the seat: the booking stands, and so does
    every row about it."""
    mock_gts_signin()
    mock_gts_cancel(error="CANCEL: this order cannot be canceled")
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["errors"][0]["code"] == "upstream_error"
    assert "cannot be canceled" in body["errors"][0]["message"]
    await db_session.refresh(order)
    assert order.status == "booked"
    assert order.cancelled_at is None
    assert await _events(db_session, order) == []


@respx.mock
async def test_a_lost_answer_is_settled_by_the_read_back(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The cancellation is never re-sent blind; the order is read instead, and
    a hold GTS has let go is recorded whatever happened to the answer."""
    mock_gts_signin()
    respx.post(f"{GTS}/v1/content/cancel/").mock(
        side_effect=httpx.ReadTimeout("GTS did not answer")
    )
    read = _released_read()
    order = await make_order(db_session, customer)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["order"]["status"] == "cancelled"
    assert read.call_count == 1
    await db_session.refresh(order)
    assert order.status == "cancelled"


@respx.mock
async def test_a_lost_answer_over_a_live_hold_is_a_504(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    respx.post(f"{GTS}/v1/content/cancel/").mock(
        side_effect=httpx.ReadTimeout("GTS did not answer")
    )
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 504, response.text
    assert response.json()["errors"][0]["code"] == "upstream_timeout"
    await db_session.refresh(order)
    assert order.status == "booked"


@respx.mock
async def test_a_read_back_that_fails_does_not_undo_the_cancellation(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """GTS accepted the cancellation; being unable to read the order
    afterwards leaves the record thinner, not the seat un-released."""
    mock_gts_signin()
    mock_gts_cancel()
    mock_gts_order(None)
    order = await make_order(db_session, customer)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["order"]["status"] == "cancelled"
    await db_session.refresh(order)
    assert order.status == "cancelled"
    assert order.gts_status == "BO"


@respx.mock
async def test_gts_accepting_while_still_showing_the_hold_is_believed(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The POST is the act and the read is only corroboration, so a GTS that
    answers ``success`` and still reads ``BO`` has cancelled the order."""
    mock_gts_signin()
    mock_gts_cancel()
    mock_gts_order(gts_order_body())
    order = await make_order(db_session, customer)

    response = await _cancel(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["order"]["status"] == "cancelled"
    await db_session.refresh(order)
    assert order.status == "cancelled"
    assert order.gts_status == "BO"
