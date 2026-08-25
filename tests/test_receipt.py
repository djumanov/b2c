"""The itinerary receipt — a link on the order, and nothing more of ours.

GTS renders the document ("маршрутная квитанция") and serves it; the customer's
app opens the link itself. So there is no endpoint here to test: what is pinned
is the link the order answer carries — that it is whole, that its host is the
credential in the database, and that it appears exactly when the ticket does.
"""

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from tests.conftest import GTS, ORDER_NUMBER, make_order, mock_gts_signin

pytestmark = pytest.mark.usefixtures("gts_credential")

ORDERS_URL = "/api/v1/public/orders/"

RECEIPT = f"{GTS}/v1/receipt/pattern/view/?order_number={ORDER_NUMBER}&product=flight"


async def _ticketed(session: AsyncSession, customer: Customer, **overrides: Any) -> Any:
    """An order GTS has issued the ticket for — the only kind with a receipt."""
    from app.db.mixins import utcnow

    return await make_order(
        session,
        customer,
        payment_status="paid",
        ticketing_status="ticketed",
        gts_status="TI",
        paid_at=utcnow(),
        ticketed_at=utcnow(),
        **overrides,
    )


@respx.mock
async def test_a_ticketed_order_carries_the_whole_link(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Whole, so an app can open it with nothing but the answer in hand."""
    mock_gts_signin()
    order = await _ticketed(db_session, customer)

    response = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["order"]["receipt_url"] == RECEIPT


@respx.mock
async def test_there_is_no_link_before_the_ticket_exists(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A booked order and one still being ticketed have nothing to render."""
    mock_gts_signin()
    booked = await make_order(db_session, customer)
    waiting = await make_order(
        db_session, customer, payment_status="paid", ticketing_status="processing"
    )

    for order in (booked, waiting):
        response = await client.get(
            f"{ORDERS_URL}{order.id}/", headers=customer_headers
        )
        assert response.json()["data"]["order"]["receipt_url"] is None, order.id


@respx.mock
async def test_the_link_follows_the_credential_in_the_database(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
    gts_credential: Any,
) -> None:
    """Move the installation to another GTS and every link moves with it —
    the host is a database setting, never a constant in our code."""
    mock_gts_signin()
    gts_credential.base_url = "https://gts.another.test/"
    await db_session.commit()
    order = await _ticketed(db_session, customer)

    response = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)

    assert response.json()["data"]["order"]["receipt_url"] == (
        "https://gts.another.test/v1/receipt/pattern/view/"
        f"?order_number={ORDER_NUMBER}&product=flight"
    )


@respx.mock
async def test_nothing_of_ours_serves_the_document(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The receipt is fetched from GTS by the app, not proxied by this API."""
    mock_gts_signin()
    order = await _ticketed(db_session, customer)

    response = await client.get(
        f"{ORDERS_URL}{order.id}/receipt/", headers=customer_headers
    )

    assert response.status_code == 404, response.text


@respx.mock
async def test_support_sees_the_same_link(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    order = await _ticketed(db_session, customer)

    response = await client.get(
        f"/api/v1/admin/orders/{order.id}/", headers=staff_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["order"]["receipt_url"] == RECEIPT


def test_one_passenger_copy_is_the_same_link_with_an_index() -> None:
    """GTS counts passengers from zero; the client appends the parameter."""
    from app.providers.products.flight import FlightAdapter

    assert FlightAdapter().receipt_url(GTS, ORDER_NUMBER, passenger_index=0) == (
        f"{RECEIPT}&passenger_index=0"
    )
