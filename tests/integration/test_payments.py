"""Opening a payment — ``/public/orders/{id}/transactions/`` (API.md §22).

The provider is a double pinned through ``providers/payments.set_provider``,
the seam that exists so a checkout can be driven without a merchant account.
What is being checked is not the provider: it is the order of operations around
it, and this file covers only the first step. The card and the code are in
``test_card_checkout.py``.

**The attempt row is written before the provider is called** — and here, before
it is called at all: opening an attempt talks to nobody. That is why this
endpoint needs no idempotency key and why the guard against a second attempt is
a unique index rather than a cached response (``O8``).
"""

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.modules.customers.models import Customer
from app.modules.integrations import repository as integrations_repo
from app.modules.orders.models import Order, OrderPayment
from app.modules.orders.states import OrderStatus
from app.modules.settings import cache as settings_cache
from app.providers.payments import clear_overrides, set_provider
from app.providers.payments.base import (
    CallbackResult,
    CardCredentials,
    ChargeResult,
    PaymentProviderCode,
    ProviderCheck,
    ReferenceSink,
    RefundResult,
    RefundStatus,
    RegisteredCard,
    TransactionStatus,
    VerifiedCard,
)
from tests.integration.conftest import customer_headers_for, make_customer

ORDERS = "/api/v1/public/orders/"
TRANSACTIONS = "/api/v1/public/transactions/"
DOMAIN = "brand.uz"
CREDENTIALS = {"merchant_id": "merchant-1234abcd", "key": "payme-secret"}


@dataclass
class RecordingProvider:
    """A provider that remembers what it was asked and answers as told."""

    code: PaymentProviderCode = PaymentProviderCode.PAYME
    signature_ok: bool = True
    settles: bool = True
    provider_ref: str = "receipt-1"
    #: Raised by ``register_card`` when set — a card the provider will not take.
    refuses: Exception | None = None
    #: Raised by ``verify_card`` when set — the wrong code.
    code_refuses: Exception | None = None
    #: Raised by ``charge_card`` when set — a decline at the last step.
    charge_refuses: Exception | None = None
    charge_status: TransactionStatus = TransactionStatus.PAID
    #: What ``status`` answers when reconciliation asks after a charge.
    status_answer: TransactionStatus = TransactionStatus.PAID
    otp_wait_seconds: int | None = 0
    #: What ``refund`` answers. ``None`` means it succeeded.
    refund_refuses: Exception | None = None
    refund_status: RefundStatus = RefundStatus.SUCCEEDED

    registered: list[str] = field(default_factory=list)
    verified: list[tuple[str, str]] = field(default_factory=list)
    charged: list[dict[str, Any]] = field(default_factory=list)
    resent: list[str] = field(default_factory=list)
    forgotten: list[str] = field(default_factory=list)
    asked_after: list[str] = field(default_factory=list)
    refunded: list[dict[str, Any]] = field(default_factory=list)
    reference: str | None = None

    # --- the card flow ---------------------------------------------------------

    async def register_card(self, card: CardCredentials) -> RegisteredCard:
        # Only the last four are recorded: a double that kept the number would
        # be the one place in the suite where a PAN sat in memory for no reason.
        self.registered.append(card.number[-4:])
        if self.refuses is not None:
            raise self.refuses
        return RegisteredCard(
            token=f"token-{card.number[-4:]}",
            otp_sent_to="+9989**1234",
            otp_wait_seconds=self.otp_wait_seconds,
        )

    async def request_card_code(self, *, token: str) -> RegisteredCard:
        self.resent.append(token)
        return RegisteredCard(
            token=token,
            otp_sent_to="+9989**1234",
            otp_wait_seconds=self.otp_wait_seconds,
        )

    async def verify_card(self, *, token: str, code: str) -> VerifiedCard:
        self.verified.append((token, code))
        if self.code_refuses is not None:
            raise self.code_refuses
        return VerifiedCard(token=token)

    async def charge_card(
        self,
        *,
        token: str,
        order_id: str,
        amount: Decimal,
        currency: str,
        on_reference: ReferenceSink | None = None,
    ) -> ChargeResult:
        self.charged.append(
            {
                "token": token,
                "order_id": order_id,
                "amount": amount,
                "currency": currency,
            }
        )
        # Named first, then paid — Payme's two calls in the order it makes
        # them, so a refusal here stands in for a charge whose answer never
        # arrives rather than for one that never started.
        self.reference = order_id
        if on_reference is not None:
            await on_reference(self.provider_ref)
        if self.charge_refuses is not None:
            raise self.charge_refuses
        return ChargeResult(
            status=self.charge_status,
            provider_ref=(
                self.provider_ref
                if self.charge_status is TransactionStatus.PAID
                else None
            ),
            failure_message=(
                None
                if self.charge_status is TransactionStatus.PAID
                else "the card was declined"
            ),
        )

    async def remove_card(self, *, token: str) -> None:
        self.forgotten.append(token)

    # --- the callback half -----------------------------------------------------

    def verify_signature(self, headers: Any, body: bytes) -> bool:
        return self.signature_ok

    def signature_rejected(self) -> CallbackResult:
        # Payme's shape: a 200 with a JSON-RPC error, because a 401 only makes
        # it retry (API.md §40).
        return CallbackResult(
            body={"error": {"code": -32504, "message": "bad signature"}},
            status_code=200,
        )

    async def handle_callback(self, headers: Any, body: bytes) -> CallbackResult:
        return CallbackResult(
            body={"result": {"state": 2}},
            settled=self.settles,
            provider_ref=self.provider_ref,
            order_id=self.reference,
        )

    async def refund(
        self, *, transaction_ref: str, amount: Decimal | None = None
    ) -> RefundResult:
        self.refunded.append({"transaction_ref": transaction_ref, "amount": amount})
        if self.refund_refuses is not None:
            raise self.refund_refuses
        return RefundResult(
            status=self.refund_status,
            provider_ref=f"refund-{transaction_ref}",
            failure_message=(
                None
                if self.refund_status is RefundStatus.SUCCEEDED
                else "the provider refused the refund"
            ),
        )

    async def status(self, *, transaction_ref: str) -> ChargeResult:
        self.asked_after.append(transaction_ref)
        return ChargeResult(
            status=self.status_answer,
            provider_ref=transaction_ref,
            failure_message=(
                None
                if self.status_answer is not TransactionStatus.FAILED
                else "the card was declined"
            ),
        )

    async def verify(self) -> ProviderCheck:
        return ProviderCheck(ok=True)


async def enable_provider(
    session: AsyncSession, code: PaymentProviderCode = PaymentProviderCode.PAYME
) -> None:
    """Switch one provider on in the database.

    Pinning the adapter is no longer enough on its own: which provider charges
    is now read from the panel's own rows (``O15``), so a test that only
    overrides the adapter would be a test of an installation that accepts no
    payments at all.
    """
    for row in await integrations_repo.payment_providers(session):
        row.enabled = row.code is code
        if row.code is code and not row.credentials:
            row.credentials, row.key_version = encrypt(json.dumps(CREDENTIALS))
    await session.commit()


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


_counter = 0


async def _booked(session: AsyncSession, customer: Customer, **overrides: Any) -> Order:
    global _counter
    _counter += 1
    fields: dict[str, Any] = {
        "order_no": f"B2C-2608-7{_counter:05d}",
        "customer_id": customer.id,
        "product": "flight",
        "status": OrderStatus.BOOKED.value,
        "provider_order_number": f"6{_counter:05d}",
        "amount_total": "1250000.00",
        "currency": "UZS",
        **overrides,
    }
    order = Order(**fields)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def _start(api: AsyncClient, order: Order, headers: dict[str, str]) -> Any:
    return await api.post(f"{ORDERS}{order.id}/transactions/", headers=headers)


# --- opening an attempt ---------------------------------------------------------------


async def test_opening_a_payment_writes_an_attempt_and_asks_for_a_card(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)

    response = await _start(api, order, headers)

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["status"] == "awaiting_card"
    assert body["provider"] == "payme"
    assert body["card"] is None
    assert body["otp"] is None
    # The amount is copied from the order, not asked for by the client.
    assert body["amount"] == {"amount": "1250000.00", "currency": "UZS"}
    # Nothing has been said to the provider yet — the card has not arrived.
    assert provider.registered == []

    attempt = await session.scalar(
        select(OrderPayment).where(OrderPayment.order_id == order.id)
    )
    assert attempt is not None
    assert attempt.status == "awaiting_card"
    assert attempt.provider_ref is None
    assert attempt.card_token is None


async def test_opening_twice_returns_the_attempt_already_open(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Idempotent without a key: the second call lands on the first attempt.

    A derived key could not do this job — the body is empty, so every call would
    hash alike and a failed first attempt would replay for a day (API.md §22).
    """
    order = await _booked(session, customer)

    first = await _start(api, order, headers)
    second = await _start(api, order, headers)

    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"]

    rows = (
        await session.scalars(
            select(OrderPayment).where(OrderPayment.order_id == order.id)
        )
    ).all()
    assert len(rows) == 1


async def test_a_charge_in_flight_blocks_a_second_attempt(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """``pending`` means the money may already have moved. Nothing goes on top."""
    order = await _booked(session, customer)
    started = (await _start(api, order, headers)).json()["data"]
    attempt = await session.get(OrderPayment, uuid.UUID(started["id"]))
    assert attempt is not None
    attempt.status = TransactionStatus.PENDING.value
    await session.commit()

    response = await _start(api, order, headers)

    assert response.status_code == 409


async def test_the_old_body_is_refused_rather_than_ignored(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """A client still choosing a method is told the contract moved (``O15``)."""
    order = await _booked(session, customer)

    response = await api.post(
        f"{ORDERS}{order.id}/transactions/",
        json={"method": "payme", "return_url": f"https://{DOMAIN}/done"},
        headers=headers,
    )

    assert response.status_code == 422


async def test_an_order_that_is_not_booked_cannot_be_paid_for(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Paying twice for the same seat is the failure this prevents."""
    order = await _booked(session, customer, status=OrderStatus.PAID.value)

    response = await _start(api, order, headers)

    assert response.status_code == 409


async def test_an_order_priced_in_another_currency_cannot_be_paid_by_card(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """Neither card API takes anything but so'm (API.md §22)."""
    order = await _booked(session, customer, currency="EUR")

    response = await _start(api, order, headers)

    assert response.status_code == 409


async def test_someone_elses_order_cannot_be_paid_for(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    stranger = await make_customer(session, email="someone.else@example.uz")
    order = await _booked(session, stranger)

    response = await _start(api, order, headers)

    assert response.status_code == 404


async def test_an_installation_with_no_provider_writes_no_attempt(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
) -> None:
    """An attempt is evidence of a conversation with a provider. There was none."""
    order = await _booked(session, customer)

    response = await _start(api, order, headers)

    assert response.status_code == 502
    rows = (
        await session.scalars(
            select(OrderPayment).where(OrderPayment.order_id == order.id)
        )
    ).all()
    assert rows == []


# --- reading one back -----------------------------------------------------------------


async def test_a_transaction_is_readable_by_its_owner_only(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)
    started = (await _start(api, order, headers)).json()["data"]
    stranger = await make_customer(session, email="someone.else@example.uz")

    mine = await api.get(f"{TRANSACTIONS}{started['id']}/", headers=headers)
    theirs = await api.get(
        f"{TRANSACTIONS}{started['id']}/", headers=customer_headers_for(stranger)
    )
    missing = await api.get(f"{TRANSACTIONS}{uuid.uuid4()}/", headers=headers)

    assert mine.status_code == 200
    assert mine.json()["data"]["id"] == started["id"]
    # "Not yours" and "no such thing" are the same answer (API.md §18).
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["errors"] == missing.json()["errors"]


async def test_reading_a_transaction_needs_a_token(api: AsyncClient) -> None:
    assert (await api.get(f"{TRANSACTIONS}{uuid.uuid4()}/")).status_code == 401


async def test_the_enabled_methods_are_public(api: AsyncClient) -> None:
    """No auth: which provider an installation works through is printed on its
    own checkout page (API.md §17)."""
    response = await api.get("/api/v1/public/payments/methods/")

    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)
