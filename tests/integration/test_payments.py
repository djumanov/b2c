"""Paying for an order — ``/public/orders/{id}/transactions/`` (API.md §22).

The provider is a double pinned through ``providers/payments.set_provider``,
the seam that exists so a charge can be driven without a merchant account. What
is being checked is not the provider: it is the order of operations around it.

**The attempt row is written before the provider is called.** Everything else
in this file follows from that — a provider that refuses leaves a failed
attempt rather than nothing, and a process that dies mid-call leaves an attempt
with no reference for reconciliation to find (ARCHITECTURE.md §8).
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.orders.models import Order, OrderPayment
from app.modules.orders.states import OrderStatus
from app.modules.settings import cache as settings_cache
from app.providers.payments import clear_overrides, set_provider
from app.providers.payments.base import (
    CallbackResult,
    ChargeResult,
    PaymentProviderCode,
    ProviderCheck,
    RefundResult,
    RefundStatus,
    TransactionStatus,
)
from tests.integration.conftest import customer_headers_for, make_customer

ORDERS = "/api/v1/public/orders/"
TRANSACTIONS = "/api/v1/public/transactions/"
DOMAIN = "brand.uz"
RETURN_URL = f"https://{DOMAIN}/checkout/done"
CHECKOUT_URL = "https://checkout.test/pay/receipt-1"


@dataclass
class RecordingProvider:
    """A provider that remembers what it was asked and answers as told."""

    code: PaymentProviderCode = PaymentProviderCode.PAYME
    redirect_url: str = CHECKOUT_URL
    signature_ok: bool = True
    settles: bool = True
    provider_ref: str = "receipt-1"
    refuses: Exception | None = None
    #: What ``refund`` answers. ``None`` means it succeeded.
    refund_refuses: Exception | None = None
    refund_status: RefundStatus = RefundStatus.SUCCEEDED
    charged: list[dict[str, Any]] = field(default_factory=list)
    refunded: list[dict[str, Any]] = field(default_factory=list)
    reference: str | None = None

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        self.charged.append(
            {
                "order_id": order_id,
                "amount": amount,
                "currency": currency,
                "return_url": return_url,
            }
        )
        if self.refuses is not None:
            raise self.refuses
        self.reference = order_id
        return self.redirect_url

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
        return ChargeResult(status=TransactionStatus.PAID)

    async def verify(self) -> ProviderCheck:
        return ProviderCheck(ok=True)


@pytest.fixture
async def provider() -> Any:
    """Payme, pinned for this test only."""
    pinned = RecordingProvider()
    set_provider(PaymentProviderCode.PAYME, pinned)
    yield pinned
    clear_overrides()


@pytest.fixture
async def installation() -> None:
    """An installation with a domain, so a return address can belong to it."""
    await settings_cache.write(
        {"site": {"domain": DOMAIN}, "products": [{"code": "flight", "enabled": True}]}
    )


@pytest.fixture
def headers(customer: Customer) -> dict[str, str]:
    return customer_headers_for(customer)


def _keyed(headers: dict[str, str], key: str | None = None) -> dict[str, str]:
    return {**headers, "Idempotency-Key": key or str(uuid.uuid4())}


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


async def _start(
    api: AsyncClient,
    order: Order,
    headers: dict[str, str],
    *,
    key: str | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    return await api.post(
        f"{ORDERS}{order.id}/transactions/",
        json=body or {"method": "payme", "return_url": RETURN_URL},
        headers=_keyed(headers, key),
    )


# --- starting an attempt --------------------------------------------------------------


async def test_starting_a_payment_writes_the_attempt_and_returns_the_redirect(
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
    assert body["status"] == "pending"
    assert body["flow"] == "redirect"
    assert body["provider"] == "payme"
    assert body["redirect_url"] == CHECKOUT_URL
    # The amount is copied from the order, not asked for by the client.
    assert body["amount"] == {"amount": "1250000.00", "currency": "UZS"}

    # The provider was handed the **order's** id: both Payme's `account` and
    # Click's `merchant_trans_id` are the merchant's own order handle, and a
    # settled charge pointing at anything else would point at nothing.
    assert provider.charged[0]["order_id"] == str(order.id)
    assert provider.charged[0]["amount"] == Decimal("1250000.00")
    assert provider.charged[0]["return_url"] == RETURN_URL

    attempt = await session.scalar(
        select(OrderPayment).where(OrderPayment.order_id == order.id)
    )
    assert attempt is not None
    # No reference yet: a hosted redirect gives a URL, and the provider names
    # the charge when it first calls back.
    assert attempt.provider_ref is None
    assert attempt.status == "pending"


async def test_a_provider_that_refuses_leaves_a_failed_attempt(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """What was tried is part of the record. An attempt that never started is
    the cheapest evidence there is, and throwing it away would leave a customer
    reporting a failure nobody can see."""
    from app.api.errors import UpstreamError

    provider.refuses = UpstreamError("the merchant account is suspended")
    order = await _booked(session, customer)

    response = await _start(api, order, headers)

    assert response.status_code == 502
    attempt = await session.scalar(
        select(OrderPayment).where(OrderPayment.order_id == order.id)
    )
    assert attempt is not None
    assert attempt.status == "failed"
    assert "suspended" in (attempt.error_message or "")


async def test_a_return_address_that_is_not_ours_is_refused(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """A redirect target chosen by a request and handed to a payment provider
    is an open redirect wearing the installation's merchant account."""
    order = await _booked(session, customer)

    response = await _start(
        api,
        order,
        headers,
        body={"method": "payme", "return_url": "https://evil.test/collect"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "return_url"
    assert provider.charged == []


async def test_an_unknown_method_never_reaches_a_provider(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    order = await _booked(session, customer)

    response = await _start(
        api, order, headers, body={"method": "bitcoin", "return_url": RETURN_URL}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "method"


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
    assert provider.charged == []


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
    assert provider.charged == []


async def test_starting_without_a_key_charges_once(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """No header is the ordinary case: the server derives the key from the
    request, so a double-tapped pay button is still one charge (API.md §10)."""
    order = await _booked(session, customer)
    body = {"method": "payme", "return_url": RETURN_URL}

    first = await api.post(
        f"{ORDERS}{order.id}/transactions/", json=body, headers=headers
    )
    second = await api.post(
        f"{ORDERS}{order.id}/transactions/", json=body, headers=headers
    )

    assert first.status_code == 201
    assert first.json() == second.json()
    assert len(provider.charged) == 1


async def test_the_same_key_starts_one_attempt(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    installation: None,
    provider: RecordingProvider,
) -> None:
    """A dropped response and a double-tapped button look identical, and one of
    them must not become two charges (API.md §10)."""
    order = await _booked(session, customer)
    key = str(uuid.uuid4())

    first = await _start(api, order, headers, key=key)
    second = await _start(api, order, headers, key=key)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(provider.charged) == 1


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
    """No auth: which methods an installation accepts is printed on its own
    checkout page."""
    response = await api.get("/api/v1/public/payments/methods/")

    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)
