"""Test fixtures: throwaway database, fake Redis, ASGI client, seeds.

The environment is pinned **before the first ``app.`` import** —
``app.core.config`` instantiates its ``settings`` singleton at import time,
and pydantic-settings lets a real environment variable beat ``.env``, which is
how the suite points the whole process at ``b2c_test`` without touching the
developer's file.

GTS is faked with ``respx``, which patches httpx's real network transports;
the test client below speaks ASGI through an explicit ``ASGITransport``, which
respx leaves alone — so an API request runs the full app while its outbound
GTS calls hit the mocks.
"""

import getpass
import os
import subprocess
import sys

# --- environment first: ``app.core.config`` builds its singleton on import ---
os.environ["DEBUG"] = "true"
os.environ["POSTGRES_DB"] = "b2c_test"
os.environ.setdefault("POSTGRES_USER", getpass.getuser())
os.environ.setdefault("POSTGRES_PASSWORD", "")
# A fixed key ring (base64 of 32 zero bytes) so encrypted seeds decrypt.
os.environ["ENCRYPTION_KEYS"] = "1:MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
os.environ["ENCRYPTION_KEY_VERSION"] = "1"

from collections.abc import AsyncIterator, Iterator  # noqa: E402
from typing import Any  # noqa: E402

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from app.core.config import settings  # noqa: E402
from app.core.crypto import encrypt  # noqa: E402
from app.core.security import (  # noqa: E402
    Audience,
    TokenType,
    create_token,
    hash_password,
)
from app.db.mixins import utcnow  # noqa: E402
from app.db.redis import set_redis  # noqa: E402
from app.db.session import get_sessionmaker, set_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.customers.models import Customer  # noqa: E402
from app.modules.integrations.models import GtsCredential  # noqa: E402
from app.modules.settings import cache as settings_cache  # noqa: E402

#: The base URL the seeded GTS credential points at — respx mocks it.
GTS = "https://gts.test"


def _prepare_database() -> None:
    """Recreate ``b2c_test`` and bring it to the migration head.

    Dropped first, not reused: a leftover test database may carry an
    ``alembic_version`` from a chain that no longer exists. Alembic runs as a
    subprocess: ``migrations/env.py`` calls ``asyncio.run``, which cannot
    happen inside pytest-asyncio's already-running loop.
    """
    subprocess.run(
        ["dropdb", "--if-exists", settings.postgres_db],
        capture_output=True,
        check=False,
    )
    subprocess.run(["createdb", settings.postgres_db], capture_output=True, check=False)
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{completed.stderr}")


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    _prepare_database()


@pytest.fixture(scope="session")
async def engine(database: None) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url)
    set_engine(engine)
    yield engine
    set_engine(None)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(engine: AsyncEngine) -> AsyncIterator[None]:
    """The services genuinely commit, so isolation is cleanup, not rollback."""
    yield
    async with engine.begin() as connection:
        for table in (
            "payment_attempts",
            "order_events",
            "orders",
            "customer_cards",
            "customers",
            "gts_credentials",
            "audit_log",
            "staff",
        ):
            await connection.execute(text(f"DELETE FROM {table}"))


@pytest.fixture(autouse=True)
async def fake_redis(
    engine: AsyncEngine,
) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """A fresh fake per test — rate limits and caches never leak across tests."""
    redis = fakeredis.aioredis.FakeRedis()
    set_redis(redis)
    yield redis
    set_redis(None)


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- seeds -------------------------------------------------------------------


async def make_customer(session: AsyncSession, email: str) -> Customer:
    """A verified customer — ``get_active`` refuses unverified rows."""
    row = Customer(
        email=email,
        password_hash=hash_password("secret-password-1"),
        email_verified_at=utcnow(),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def bearer(customer: Customer) -> dict[str, str]:
    token, _ = create_token(
        subject_id=customer.id,
        audience=Audience.PUBLIC,
        token_type=TokenType.ACCESS,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def customer(db_session: AsyncSession) -> Customer:
    return await make_customer(db_session, "traveller@example.com")


@pytest.fixture
def customer_headers(customer: Customer) -> dict[str, str]:
    return bearer(customer)


@pytest.fixture
async def gts_credential(db_session: AsyncSession) -> GtsCredential:
    ciphertext, key_version = encrypt("gts-secret-1a2b")
    row = GtsCredential(
        label="Test agent",
        base_url=GTS,
        email="agent@brand.uz",
        password=ciphertext,
        key_version=key_version,
        is_active=True,
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest.fixture
async def flight_enabled(fake_redis: Any) -> None:
    """Seed the site-config cache so the product gate answers for ``flight``."""
    await settings_cache.write({"products": [{"code": "flight", "enabled": True}]})


# --- GTS mocks shared by the flows after booking ---------------------------------

ORDER_NUMBER = 61453


def gts_envelope(data: Any) -> dict[str, Any]:
    """GTS's own envelope — distinct from ours."""
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


def mock_gts_signin() -> None:
    import respx

    respx.post(f"{GTS}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200,
            json=gts_envelope(
                {
                    "session_key": "sess-abc",
                    "token": "tok-abc",
                    "timeout_minutes": 360.0,
                }
            ),
        )
    )


def gts_order_body(**overrides: Any) -> dict[str, Any]:
    """One order as ``GET /v1/orders/{n}/`` returns it, a day before its deadline."""
    from datetime import timedelta

    deadline = (utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    order: dict[str, Any] = {
        "order_number": ORDER_NUMBER,
        "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "status": "BO",
        "gds_pnr": "UBPLKW",
        "trip_type": "OW",
        "ticket_time_limit": deadline,
        "price_info": {"price": 287500.0, "currency": "UZS", "commission_amount": 0},
        "routes": [
            {
                "route_index": 1,
                "direction": "TAS-VKO",
                "segments": [{"leg": "TAS-VKO", "flight_number": "12"}],
            }
        ],
        "passengers_count": 1,
        "passengers": [
            {"firstname": "Azimjon", "lastname": "Yusufov", "ticket_number": None}
        ],
    }
    order.update(overrides)
    return order


def mock_gts_order(
    order: dict[str, Any] | None, *, order_number: int = ORDER_NUMBER
) -> Any:
    """``GET /v1/orders/{n}/``; ``None`` makes GTS fail the read."""
    import respx

    return respx.get(f"{GTS}/v1/orders/{order_number}/").mock(
        return_value=httpx.Response(200, json=gts_envelope(order))
        if order is not None
        else httpx.Response(500, json={"status": "error", "message": "boom"})
    )


async def make_order(
    session: AsyncSession, customer: Customer, **overrides: Any
) -> Any:
    """A booked, unpaid order a day before its deadline — the payment tests' start."""
    from datetime import timedelta

    from app.modules.orders.models import Order

    fields: dict[str, Any] = {
        "customer_id": customer.id,
        "product": "flight",
        "status": "booked",
        "payment_status": "pending",
        "ticketing_status": "pending",
        "request_id": "req-abc",
        "offer_id": "offer-abc",
        "gts_order_number": ORDER_NUMBER,
        "gts_status": "BO",
        "pnr": "UBPLKW",
        "amount": 20,
        "currency": "UZS",
        "route_summary": "TAS-VKO",
        "ticket_time_limit_at": utcnow() + timedelta(days=1),
        "gts_response": {"order_number": ORDER_NUMBER},
    }
    fields.update(overrides)
    order = Order(**fields)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


# --- a scripted payment provider ------------------------------------------------------


class FakeProvider:
    """Implements ``PaymentProvider`` from a script the test writes.

    ``confirm_outcomes`` / ``status_outcomes`` are consumed in order; an
    exception in the list is raised instead of returned. ``calls`` records
    every call for assertions such as "the provider was charged once".
    """

    code = "fake"

    def __init__(self) -> None:
        from app.providers.payments.base import PaymentOutcome

        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.start_error: Exception | None = None
        self.confirm_outcomes: list[PaymentOutcome | Exception] = []
        self.status_outcomes: list[PaymentOutcome | Exception] = []
        self.references: list[str] = []

    def _next(self, script: list[Any], default: Any) -> Any:
        outcome = script.pop(0) if script else default
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def start(self, *, card: Any, amount: Any, order_ref: str) -> Any:
        from app.providers.payments.base import PaymentStart

        self.calls.append(
            (
                "start",
                {
                    "last4": card.last4,
                    "amount": str(amount.amount),
                    "order_ref": order_ref,
                },
            )
        )
        if self.start_error is not None:
            raise self.start_error
        reference = f"ref-{len(self.references) + 1}"
        self.references.append(reference)
        return PaymentStart(reference=reference, phone_hint="+99890***1234")

    async def confirm(self, *, reference: str, otp: str) -> Any:
        from app.providers.payments.base import PaymentOutcome

        self.calls.append(("confirm", {"reference": reference, "otp": otp}))
        return self._next(
            self.confirm_outcomes, PaymentOutcome("paid", reference=reference)
        )

    async def status(self, *, reference: str) -> Any:
        from app.providers.payments.base import PaymentOutcome

        self.calls.append(("status", {"reference": reference}))
        return self._next(
            self.status_outcomes, PaymentOutcome("pending", reference=reference)
        )

    def count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)


@pytest.fixture
def fake_provider() -> Iterator[FakeProvider]:
    from app.providers import payments as payment_providers

    provider = FakeProvider()
    payment_providers.set_provider(provider)
    yield provider
    payment_providers.set_provider(None)


def gts_ticketed_order(**overrides: Any) -> dict[str, Any]:
    """The order as GTS's ticketing answer carries it — under ``order``."""
    order: dict[str, Any] = {
        "order_number": ORDER_NUMBER,
        "status": "TI",
        "gds_pnr": "UBPLKW",
        "passengers": [
            {
                "passenger_type": "ADT",
                "first_name": "AZIMJON",
                "last_name": "YUSUFOV",
                "passenger_id": "4faa37bc-91d1-4da1-ba3d-b22ef8ec8802",
                "ticket_number": "7653081297644",
            }
        ],
    }
    order.update(overrides)
    return order


def mock_gts_ticketing(
    order: dict[str, Any] | None = None, *, error: str | None = None
) -> Any:
    """``POST /v1/content/ticketing/``: the order under ``order`` (not ``data``),
    or GTS's refusal — HTTP 200, ``status: "error"``, the reason in ``message``."""
    import respx

    if error is not None:
        body: dict[str, Any] = {
            "status": "error",
            "message": error,
            "code": -104,
            "data": None,
        }
    else:
        body = {
            "status": "success",
            "code": 100,
            "order": order if order is not None else gts_ticketed_order(),
        }
    return respx.post(f"{GTS}/v1/content/ticketing/").mock(
        return_value=httpx.Response(200, json=body)
    )


# --- staff ----------------------------------------------------------------------


async def make_staff(session: AsyncSession, email: str, *, role: str = "owner") -> Any:
    from app.core.roles import Role
    from app.modules.staff.models import Staff

    row = Staff(
        email=email,
        password_hash=hash_password("staff-password-1"),
        name="Support",
        role=Role(role),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def staff_bearer(staff: Any) -> dict[str, str]:
    token, _ = create_token(
        subject_id=staff.id,
        audience=Audience.ADMIN,
        token_type=TokenType.ACCESS,
        role=staff.role.value,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def staff(db_session: AsyncSession) -> Any:
    return await make_staff(db_session, "support@brand.uz")


@pytest.fixture
def staff_headers(staff: Any) -> dict[str, str]:
    return staff_bearer(staff)
