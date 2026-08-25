"""``receipt/`` — the ticketed order's itinerary receipt, straight from GTS.

GTS renders the document ("маршрутная квитанция") and this route hands the
bytes on: nothing is stored, nothing is re-drawn, and the answer is the file
rather than the envelope. It opens only once the ticket exists — before that
``ticketing.receipt_url`` is ``null`` and the call is a ``409``.

GTS is ``respx``; a refusal from it arrives the way every GTS refusal does,
as a JSON envelope under HTTP 200 where a file was asked for.
"""

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.orders.models import Order
from tests.conftest import (
    GTS,
    ORDER_NUMBER,
    RECEIPT_PDF,
    bearer,
    make_customer,
    make_order,
    mock_gts_receipt,
    mock_gts_signin,
)

pytestmark = pytest.mark.usefixtures("gts_credential")

ORDERS_URL = "/api/v1/public/orders/"


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


async def _download(
    client: httpx.AsyncClient,
    order: Order,
    headers: dict[str, str],
    *,
    query: str = "",
) -> httpx.Response:
    return await client.get(f"{ORDERS_URL}{order.id}/receipt/{query}", headers=headers)


# --- the download ---------------------------------------------------------------


@respx.mock
async def test_the_receipt_is_the_file_gts_rendered(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await _ticketed(db_session, customer)

    response = await _download(client, order, customer_headers)

    assert response.status_code == 200, response.text
    # The bytes, not the envelope: a receipt wrapped in ``{status, data, …}``
    # is a receipt nothing can open.
    assert response.content == RECEIPT_PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="receipt-UBPLKW.pdf"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "private, no-store"

    params = receipt.calls.last.request.url.params
    assert params["order_number"] == str(ORDER_NUMBER)
    assert params["product"] == "flight"
    assert "passenger_index" not in params


@respx.mock
async def test_one_passenger_is_asked_for_by_index(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await _ticketed(db_session, customer)

    response = await _download(
        client, order, customer_headers, query="?passenger_index=1"
    )

    assert response.status_code == 200, response.text
    # GTS counts from zero; the file is named for the human's second passenger.
    assert receipt.calls.last.request.url.params["passenger_index"] == "1"
    assert response.headers["content-disposition"] == (
        'attachment; filename="receipt-UBPLKW-2.pdf"'
    )


@respx.mock
async def test_a_passenger_index_below_zero_is_refused_before_gts(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await _ticketed(db_session, customer)

    response = await _download(
        client, order, customer_headers, query="?passenger_index=-1"
    )

    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["code"] == "validation"
    assert receipt.call_count == 0


@respx.mock
async def test_an_order_without_a_pnr_is_named_by_its_gts_number(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_receipt()
    order = await _ticketed(db_session, customer, pnr=None)

    response = await _download(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == (
        f'attachment; filename="receipt-{ORDER_NUMBER}.pdf"'
    )


@respx.mock
async def test_a_document_we_have_not_met_is_served_as_bytes_to_save(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """An installation that renders something else does not get to choose
    what this application's own origin serves."""
    mock_gts_signin()
    mock_gts_receipt(b"GIF89a", content_type="image/gif")
    order = await _ticketed(db_session, customer)

    response = await _download(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.content == b"GIF89a"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == (
        'attachment; filename="receipt-UBPLKW.bin"'
    )


# --- when there is nothing to download ------------------------------------------


@respx.mock
async def test_an_order_still_being_ticketed_has_no_receipt(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await make_order(
        db_session, customer, payment_status="paid", ticketing_status="processing"
    )

    response = await _download(client, order, customer_headers)

    assert response.status_code == 409, response.text
    assert response.json()["errors"][0]["code"] == "conflict"
    # Our own column already knew the answer; GTS was never asked.
    assert receipt.call_count == 0


@respx.mock
async def test_somebody_elses_receipt_is_a_404(
    client: httpx.AsyncClient,
    customer: Customer,
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await _ticketed(db_session, customer)
    stranger = await make_customer(db_session, "stranger@example.com")

    response = await _download(client, order, bearer(stranger))

    assert response.status_code == 404, response.text
    assert receipt.call_count == 0


@respx.mock
async def test_gts_refusing_to_render_is_a_502(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_receipt(error="Заказ не найден")
    order = await _ticketed(db_session, customer)

    response = await _download(client, order, customer_headers)

    assert response.status_code == 502, response.text
    body = response.json()
    assert body["errors"][0]["code"] == "upstream_error"
    # GTS's own words survive, and the answer is our envelope — a refusal is
    # data even when the call asked for a file.
    assert "Заказ не найден" in body["errors"][0]["message"]


@respx.mock
async def test_an_empty_document_is_not_a_receipt(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    mock_gts_receipt(b"")
    order = await _ticketed(db_session, customer)

    response = await _download(client, order, customer_headers)

    assert response.status_code == 502, response.text
    assert response.json()["errors"][0]["code"] == "upstream_error"


# --- the link on the order ------------------------------------------------------


@respx.mock
async def test_the_order_carries_gts_own_link_once_the_ticket_exists(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The whole URL, so an app can open it with nothing but the answer."""
    mock_gts_signin()
    waiting = await make_order(
        db_session, customer, payment_status="paid", ticketing_status="processing"
    )
    ticketed = await _ticketed(db_session, customer)

    pending = await client.get(f"{ORDERS_URL}{waiting.id}/", headers=customer_headers)
    ready = await client.get(f"{ORDERS_URL}{ticketed.id}/", headers=customer_headers)

    assert pending.json()["data"]["ticketing"]["receipt_url"] is None
    assert ready.json()["data"]["ticketing"]["receipt_url"] == (
        f"{GTS}/v1/receipt/pattern/view/?order_number={ORDER_NUMBER}&product=flight"
    )


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

    answer = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)

    assert answer.json()["data"]["ticketing"]["receipt_url"] == (
        "https://gts.another.test/v1/receipt/pattern/view/"
        f"?order_number={ORDER_NUMBER}&product=flight"
    )


@respx.mock
async def test_support_sees_the_same_link_on_the_admin_detail(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    order = await _ticketed(db_session, customer)

    answer = await client.get(
        f"/api/v1/admin/orders/{order.id}/", headers=staff_headers
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["data"]["ticketing"]["receipt_url"] == (
        f"{GTS}/v1/receipt/pattern/view/?order_number={ORDER_NUMBER}&product=flight"
    )


@respx.mock
async def test_this_api_serves_the_same_document_for_a_client_that_asks_us(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await _ticketed(db_session, customer)

    response = await _download(client, order, customer_headers)

    assert response.status_code == 200, response.text
    assert response.content == RECEIPT_PDF
    # Same document, same GTS call — only the session doing the asking differs.
    assert receipt.calls.last.request.url.params["order_number"] == str(ORDER_NUMBER)
