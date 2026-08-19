"""Paying by card — ``card/``, ``confirm/`` and ``resend-otp/`` (API.md §22).

The customer never leaves the checkout (``O14``): the number comes to us, we
drive the provider's card API, and the money moves inside the ``confirm/``
request (``O16``). What is pinned here is the order of operations around a
provider double, and the three rules that make the flow safe without an
idempotency key:

* one open attempt per order, so a repeat lands on the attempt it already made;
* ``confirm/`` on a paid attempt replays instead of charging again;
* the attempt is ``pending`` and **committed** before the charge goes out, so a
  process that dies leaves something reconciliation can follow.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import PaymentFailed, UpstreamError
from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderPayment
from app.modules.payments.models import CustomerCard
from app.modules.settings import cache as settings_cache
from app.providers.payments import clear_overrides, set_provider
from app.providers.payments.base import PaymentProviderCode, TransactionStatus
from tests.integration.conftest import (
    TEST_CARD_EXPIRE,
    TEST_CARD_NUMBER,
    customer_headers_for,
    make_customer,
)
from tests.integration.test_payments import (
    DOMAIN,
    ORDERS,
    TRANSACTIONS,
    RecordingProvider,
    _booked,
    enable_provider,
)

CARDS = "/api/v1/public/profile/cards/"
GOOD_CODE = "123456"


@pytest.fixture
async def provider(session: AsyncSession) -> Any:
    """Payme, pinned and switched on, for this test only."""
    pinned = RecordingProvider()
    set_provider(PaymentProviderCode.PAYME, pinned)
    await enable_provider(session)
    yield pinned
    clear_overrides()


@pytest.fixture
async def installation() -> None:
    await settings_cache.write(
        {"site": {"domain": DOMAIN}, "products": [{"code": "flight", "enabled": True}]}
    )


@pytest.fixture
def headers(customer: Customer) -> dict[str, str]:
    return customer_headers_for(customer)


async def _open(api: AsyncClient, order: Order, headers: dict[str, str]) -> str:
    started = await api.post(f"{ORDERS}{order.id}/transactions/", headers=headers)
    assert started.status_code == 201, started.text
    attempt_id: str = started.json()["data"]["id"]
    return attempt_id


async def _send_card(
    api: AsyncClient,
    attempt_id: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
) -> Any:
    if body is None:
        body = {"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE}
    return await api.post(
        f"{TRANSACTIONS}{attempt_id}/card/", json=body, headers=headers
    )


async def _confirm(
    api: AsyncClient, attempt_id: str, headers: dict[str, str], code: str = GOOD_CODE
) -> Any:
    return await api.post(
        f"{TRANSACTIONS}{attempt_id}/confirm/",
        json={"otp_code": code},
        headers=headers,
    )


async def _attempt(session: AsyncSession, attempt_id: str) -> OrderPayment:
    row = await session.get(OrderPayment, uuid.UUID(attempt_id))
    assert row is not None
    await session.refresh(row)
    return row


# --- the whole flow -------------------------------------------------------------------


async def test_a_card_pays_for_an_order_end_to_end(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)

    carded = await _send_card(api, attempt_id, headers)

    assert carded.status_code == 200, carded.text
    body = carded.json()["data"]
    assert body["status"] == "awaiting_otp"
    assert body["card"] == {
        "masked_pan": "860049******4608",
        "last4": "4608",
        "brand": "uzcard",
    }
    assert body["otp"]["sent_to"] == "+9989**1234"
    assert body["otp"]["attempts_left"] == 3
    assert provider.registered == ["4608"]

    confirmed = await _confirm(api, attempt_id, headers)

    assert confirmed.status_code == 200, confirmed.text
    paid = confirmed.json()["data"]
    assert paid["status"] == "paid"
    assert paid["paid_at"] is not None
    # Nothing left to count down once the money has moved.
    assert paid["otp"] is None
    assert paid["card"]["last4"] == "4608"

    # The provider was handed the **order's** id: both Payme's `account` and
    # Click's `merchant_trans_id` are the merchant's own order handle.
    assert provider.charged[0]["order_id"] == str(order.id)
    assert provider.charged[0]["amount"] == Decimal("1250000.00")
    assert provider.charged[0]["currency"] == "UZS"

    await session.refresh(order)
    assert order.status == "paid"
    assert order.amount_paid == Decimal("1250000.00")
    # Ticketing is due immediately; the poller is the safety net (``O13``).
    assert order.next_attempt_at is not None

    row = await _attempt(session, attempt_id)
    assert row.status == "paid"
    assert row.provider_ref == "receipt-1"
    # A spent token is not kept — and the CHECK would refuse the row if it were.
    assert row.card_token is None
    assert row.card_masked == "860049******4608"


async def test_the_charge_is_named_before_it_is_paid(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """``on_reference`` is what makes a timed-out charge reconcilable.

    The adapter calls it the moment the provider names the receipt, and the
    reference has to be committed by then — otherwise a charge that never
    answers leaves an attempt pointing at nothing.
    """
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)

    # The double calls ``on_reference`` and then fails, standing in for a charge
    # whose answer never arrives.
    provider.charge_refuses = UpstreamError("the provider stopped answering")

    response = await _confirm(api, attempt_id, headers)

    assert response.status_code == 502
    row = await _attempt(session, attempt_id)
    assert row.provider_ref == "receipt-1"
    assert row.status == "failed"
    assert row.card_token is None


async def test_a_saved_card_pays_without_the_number_being_typed_again(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """``reveal_card`` is the only path that opens the ciphertext (API.md §19)."""
    saved = await api.post(
        CARDS,
        json={"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE},
        headers=headers,
    )
    assert saved.status_code == 201, saved.text
    card_id = saved.json()["data"]["id"]

    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)

    carded = await _send_card(api, attempt_id, headers, body={"card_id": card_id})

    assert carded.status_code == 200, carded.text
    assert carded.json()["data"]["card"]["last4"] == "4608"
    # The adapter received the same digits it would have from a typed card: the
    # branch it came down is invisible from here on.
    assert provider.registered == ["4608"]

    row = await _attempt(session, attempt_id)
    assert row.card_id == uuid.UUID(card_id)

    confirmed = await _confirm(api, attempt_id, headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["status"] == "paid"


async def test_the_card_body_must_be_one_form_or_the_other(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)

    both = await _send_card(
        api,
        attempt_id,
        headers,
        body={
            "number": TEST_CARD_NUMBER,
            "expire": TEST_CARD_EXPIRE,
            "card_id": str(uuid.uuid4()),
        },
    )
    neither = await _send_card(api, attempt_id, headers, body={})

    assert both.status_code == 422
    assert neither.status_code == 422
    assert provider.registered == []


async def test_a_refused_card_leaves_the_attempt_open_for_another(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """A mistyped digit is not a spent payment. The customer types it again."""
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    provider.refuses = PaymentFailed("no such card")

    refused = await _send_card(api, attempt_id, headers)

    assert refused.status_code == 400
    row = await _attempt(session, attempt_id)
    assert row.status == "awaiting_card"

    provider.refuses = None
    assert (await _send_card(api, attempt_id, headers)).status_code == 200


async def test_sending_a_second_card_replaces_the_first(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Only one token is ever live on an attempt, and the old one is released."""
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)

    again = await _send_card(
        api, attempt_id, headers, body={"number": "8600495473316478", "expire": "1230"}
    )

    assert again.status_code == 200
    assert again.json()["data"]["card"]["last4"] == "6478"
    assert provider.forgotten == ["token-4608"]
    row = await _attempt(session, attempt_id)
    assert row.card_last4 == "6478"


# --- the code -------------------------------------------------------------------------


async def test_a_wrong_code_costs_one_attempt_and_says_nothing_else(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)
    provider.code_refuses = PaymentFailed("wrong code")

    response = await _confirm(api, attempt_id, headers, code="000000")

    assert response.status_code == 400
    assert response.json()["errors"][0]["message"] == "The code was not accepted"
    row = await _attempt(session, attempt_id)
    assert row.otp_attempts == 1
    assert row.status == "awaiting_otp"

    readback = await api.get(f"{TRANSACTIONS}{attempt_id}/", headers=headers)
    assert readback.json()["data"]["otp"]["attempts_left"] == 2
    assert provider.charged == []


async def test_three_wrong_codes_spend_the_attempt_but_not_the_order(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """The order stays ``booked``, which is what ``O12`` is for: the customer
    opens a second attempt and tries a different card."""
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)
    provider.code_refuses = PaymentFailed("wrong code")

    for _ in range(3):
        refused = await _confirm(api, attempt_id, headers, code="000000")
        assert refused.status_code == 400

    row = await _attempt(session, attempt_id)
    assert row.status == "failed"
    assert row.error_code == "otp_exhausted"
    assert row.card_token is None
    assert provider.forgotten == ["token-4608"]

    await session.refresh(order)
    assert order.status == "booked"

    # The spent attempt is closed, so a fresh one may be opened.
    second = await api.post(f"{ORDERS}{order.id}/transactions/", headers=headers)
    assert second.status_code == 201
    assert second.json()["data"]["id"] != attempt_id


async def test_an_expired_code_is_recovered_by_asking_for_another(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)

    row = await _attempt(session, attempt_id)
    row.otp_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    row.otp_resend_after = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()

    stale = await _confirm(api, attempt_id, headers)

    assert stale.status_code == 400
    # Still open: an expired code is answered with another code, not with a
    # second payment.
    assert (await _attempt(session, attempt_id)).status == "awaiting_otp"
    # The provider was never asked: an expired code is our own guard.
    assert provider.verified == []

    resent = await api.post(f"{TRANSACTIONS}{attempt_id}/resend-otp/", headers=headers)
    assert resent.status_code == 200
    assert provider.resent == ["token-4608"]
    assert (await _confirm(api, attempt_id, headers)).status_code == 200


async def test_asking_for_a_code_too_soon_is_refused_with_a_wait(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """The cooldown is on the row, so a flushed Redis does not open the tap."""
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)

    response = await api.post(
        f"{TRANSACTIONS}{attempt_id}/resend-otp/", headers=headers
    )

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert provider.resent == []


async def test_a_resend_does_not_clear_the_wrong_code_count(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """A counter a customer can reset by pressing a button is not a counter."""
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)
    provider.code_refuses = PaymentFailed("wrong code")
    await _confirm(api, attempt_id, headers, code="000000")

    row = await _attempt(session, attempt_id)
    row.otp_resend_after = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    await api.post(f"{TRANSACTIONS}{attempt_id}/resend-otp/", headers=headers)

    assert (await _attempt(session, attempt_id)).otp_attempts == 1


# --- confirming ----------------------------------------------------------------------


async def test_confirming_twice_charges_once(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Replay by state — what stands in for the idempotency key this endpoint
    must not have, because its body carries a one-time password."""
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)

    first = await _confirm(api, attempt_id, headers)
    second = await _confirm(api, attempt_id, headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["paid_at"] == second.json()["data"]["paid_at"]
    assert len(provider.charged) == 1


async def test_confirming_before_the_card_is_a_conflict(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)

    response = await _confirm(api, attempt_id, headers)

    assert response.status_code == 409
    assert provider.verified == []


async def test_a_declined_charge_leaves_the_order_payable(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)
    provider.charge_status = TransactionStatus.FAILED

    response = await _confirm(api, attempt_id, headers)

    assert response.status_code == 400
    row = await _attempt(session, attempt_id)
    assert row.status == "failed"
    assert row.card_token is None
    await session.refresh(order)
    assert order.status == "booked"


async def test_somebody_elses_payment_cannot_be_advanced(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    stranger = await make_customer(session, email="someone.else@example.uz")
    theirs = customer_headers_for(stranger)

    assert (await _send_card(api, attempt_id, theirs)).status_code == 404
    assert (await _confirm(api, attempt_id, theirs)).status_code == 404
    resent = await api.post(f"{TRANSACTIONS}{attempt_id}/resend-otp/", headers=theirs)
    assert resent.status_code == 404
    assert provider.registered == []


# --- what the card flow must never leak ----------------------------------------------


async def test_no_response_in_the_flow_carries_the_number(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)

    carded = await _send_card(api, attempt_id, headers)
    confirmed = await _confirm(api, attempt_id, headers)
    read = await api.get(f"{TRANSACTIONS}{attempt_id}/", headers=headers)

    for response in (carded, confirmed, read):
        assert TEST_CARD_NUMBER not in response.text
        assert TEST_CARD_EXPIRE not in response.text


async def test_the_stored_token_is_never_in_the_clear(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """The provider's token can be charged, so it is sealed like any credential."""
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)

    row = await _attempt(session, attempt_id)
    assert row.card_token is not None
    assert "token-4608" not in row.card_token
    assert row.card_token_key_version is not None


# --- an order that closes underneath a payment ---------------------------------------


async def test_cancelling_an_order_closes_the_payment_with_it(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """An order that is cancelled and a payment still collecting a card must not
    be two different facts about the same purchase."""
    from app.modules.orders import service as orders_service

    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers)

    await orders_service.expire_booking(session, order)

    row = await _attempt(session, attempt_id)
    assert row.status == "cancelled"
    assert row.card_token is None
    await session.refresh(order)
    assert order.status == "cancelled"


async def test_saved_cards_survive_the_attempt_that_used_them(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """A receipt outlives the card: deleting the card leaves the masked copy."""
    saved = await api.post(
        CARDS,
        json={"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE},
        headers=headers,
    )
    card_id = saved.json()["data"]["id"]
    order = await _booked(session, customer)
    attempt_id = await _open(api, order, headers)
    await _send_card(api, attempt_id, headers, body={"card_id": card_id})
    await _confirm(api, attempt_id, headers)

    assert (await api.delete(f"{CARDS}{card_id}/", headers=headers)).status_code == 204

    row = await _attempt(session, attempt_id)
    assert row.card_masked == "860049******4608"
    assert row.card_last4 == "4608"
    card = await session.get(CustomerCard, uuid.UUID(card_id))
    assert card is not None and card.pan is None


async def test_only_one_attempt_may_be_open_per_order(
    session: AsyncSession, customer: Customer
) -> None:
    """The database says so, not the handler — a read then a write has a gap."""
    from sqlalchemy.exc import IntegrityError

    order = await _booked(session, customer)
    order_id = order.id
    for _ in range(2):
        session.add(
            OrderPayment(
                order_id=order_id,
                provider="payme",
                status=TransactionStatus.AWAITING_CARD.value,
                amount=Decimal("1250000.00"),
                currency="UZS",
            )
        )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
    else:  # pragma: no cover - the index is what this test exists for
        raise AssertionError("a second open attempt was allowed")

    rows = (
        await session.scalars(
            select(OrderPayment).where(OrderPayment.order_id == order_id)
        )
    ).all()
    assert rows == []
