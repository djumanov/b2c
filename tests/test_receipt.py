"""``receipt/`` — the ticketed order's itinerary receipt, fetched for the client.

GTS renders the document ("маршрутная квитанция") but will not serve it to a
customer: its receipt page wants the agent session's cookies and answers
``401`` without them. So the bytes are fetched with ours and handed on —
nothing is stored, nothing is re-drawn, and the answer is the file rather
than the envelope. The route opens only once the ticket exists; before that
``order.receipt_url`` is ``null`` and the call is a ``409``.

Two surfaces serve it, the customer's own and support's, and they answer with
the same file. GTS is ``respx``; a refusal from it arrives the way every GTS
refusal does, as a JSON envelope under HTTP 200 where a file was asked for.
"""

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.orders.models import Order
from tests.conftest import (
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
async def test_the_order_carries_the_link_only_once_the_ticket_exists(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The field a download button waits for, and the route it opens."""
    mock_gts_signin()
    waiting = await make_order(
        db_session, customer, payment_status="paid", ticketing_status="processing"
    )
    ticketed = await _ticketed(db_session, customer)

    pending = await client.get(f"{ORDERS_URL}{waiting.id}/", headers=customer_headers)
    ready = await client.get(f"{ORDERS_URL}{ticketed.id}/", headers=customer_headers)

    assert pending.json()["data"]["order"]["receipt_url"] is None
    url = ready.json()["data"]["order"]["receipt_url"]
    assert url == f"/api/v1/public/orders/{ticketed.id}/receipt/"

    # And the link is the route: the same token fetches the file from it.
    receipt = mock_gts_receipt()
    served = await client.get(url, headers=customer_headers)
    assert served.status_code == 200, served.text
    assert served.content == RECEIPT_PDF
    assert receipt.call_count == 1


@respx.mock
async def test_support_gets_its_own_path_and_the_same_file(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """A staff token opens the admin path, and only that one."""
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await _ticketed(db_session, customer)

    detail = await client.get(
        f"/api/v1/admin/orders/{order.id}/", headers=staff_headers
    )
    assert detail.status_code == 200, detail.text
    url = detail.json()["data"]["order"]["receipt_url"]
    assert url == f"/api/v1/admin/orders/{order.id}/receipt/"

    served = await client.get(url, headers=staff_headers)
    assert served.status_code == 200, served.text
    assert served.content == RECEIPT_PDF
    assert served.headers["content-disposition"] == (
        'attachment; filename="receipt-UBPLKW.pdf"'
    )
    assert receipt.calls.last.request.url.params["order_number"] == str(ORDER_NUMBER)

    # The two surfaces never cross (the token rule, API.md §4).
    assert (await client.get(url, headers=customer_headers)).status_code == 403
    denied = await client.get(f"{ORDERS_URL}{order.id}/receipt/", headers=staff_headers)
    assert denied.status_code == 403


@respx.mock
async def test_support_may_read_any_order_not_only_one_customers(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """The one difference from the customer's route — whose order it may be."""
    mock_gts_signin()
    mock_gts_receipt()
    stranger = await make_customer(db_session, "stranger@example.com")
    order = await _ticketed(db_session, stranger)

    served = await client.get(
        f"/api/v1/admin/orders/{order.id}/receipt/", headers=staff_headers
    )

    assert served.status_code == 200, served.text
    assert served.content == RECEIPT_PDF


@respx.mock
async def test_support_is_refused_before_the_ticket_too(
    client: httpx.AsyncClient,
    customer: Customer,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    mock_gts_signin()
    receipt = mock_gts_receipt()
    order = await make_order(
        db_session, customer, payment_status="paid", ticketing_status="processing"
    )

    served = await client.get(
        f"/api/v1/admin/orders/{order.id}/receipt/", headers=staff_headers
    )

    assert served.status_code == 409, served.text
    assert served.json()["errors"][0]["code"] == "conflict"
    assert receipt.call_count == 0
