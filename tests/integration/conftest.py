"""Fixtures for the tests that need a real PostgreSQL.

**Postgres, not SQLite.** The schema uses JSONB for translated fields, partial
unique indexes so a deleted row stops reserving its slug, and
``NUMERIC(18,2)`` for money (ARCHITECTURE.md §10). SQLite honours none of the
three, so a suite that ran on it would be green about a schema that does not
exist.

**The schema is built by running the migration chain**, not by
``Base.metadata.create_all``. Clients upgrade unattended and may skip several
versions (PROJECT.md D10), so a migration that is never executed in CI is a
migration that first runs on the client's server. Running it here means every
test run is also a test of ``alembic upgrade head``.

Isolation is one transaction per test, rolled back at the end. The session is
joined to it with ``join_transaction_mode="create_savepoint"``, so a service
that calls ``commit()`` — most of them do — releases a savepoint instead of
committing for real, and the outer rollback still wipes the test's rows.

Point the suite at a database with ``TEST_DATABASE_URL``; otherwise it derives
one from the ordinary Postgres settings with the name ``b2c_test``. It is
created if it does not exist, and its schema is dropped and rebuilt at the
start of every run.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.roles import Role
from app.core.security import Audience, hash_password
from app.db.mixins import utcnow
from app.db.session import get_session, set_engine
from app.main import app
from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from app.providers.notifications import set_notifier
from app.providers.notifications.base import Channel
from app.providers.payments import clear_overrides as clear_payment_overrides
from app.providers.payments import set_provider as set_payment_provider
from app.providers.payments.base import (
    CallbackResult,
    CardCredentials,
    ChargeResult,
    PaymentProviderCode,
    ProviderCheck,
    RefundResult,
    RefundStatus,
    RegisteredCard,
    TransactionStatus,
)
from app.providers.storage import set_storage
from app.providers.storage.local import LocalStorage
from tests.conftest import bearer, issue_token

TEST_DATABASE_NAME = "b2c_test"
#: Connected to only to issue ``CREATE DATABASE`` — it always exists.
_MAINTENANCE_DATABASE = "postgres"


def _url_for(database: str) -> str:
    return (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{database}"
    )


def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or _url_for(TEST_DATABASE_NAME)


async def _create_database_if_missing(name: str) -> None:
    engine = create_async_engine(
        _url_for(_MAINTENANCE_DATABASE), isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                # The name comes from our own settings, never from a request.
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


async def _reset_schema(engine: AsyncEngine) -> None:
    """Start from nothing, so the run does not depend on the last one."""
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture(scope="session")
async def database() -> AsyncIterator[AsyncEngine]:
    """A migrated, empty database, installed as the process-wide engine."""
    url = test_database_url()

    # Alembic's env.py reads ``settings.database_url``; it is a cached_property,
    # and writing into the instance dict is the documented way to override one.
    settings.__dict__["database_url"] = url

    if not os.environ.get("TEST_DATABASE_URL"):
        await _create_database_if_missing(TEST_DATABASE_NAME)

    engine = create_async_engine(url, pool_pre_ping=True)
    await _reset_schema(engine)

    # ``command.upgrade`` is synchronous and env.py drives it with
    # ``asyncio.run``, which refuses to start inside a running loop — hence the
    # thread.
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")

    set_engine(engine)
    yield engine
    set_engine(None)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def clean_audit_log(database: AsyncEngine) -> None:
    """Empty ``audit_log`` before each test.

    The audit middleware writes in a session of its own and commits — it has to,
    because by the time a response has a status code the request's session is
    closed (see ``modules/audit/service``). Those rows are therefore *outside*
    the test's transaction and survive its rollback, so they are cleared going
    in rather than coming out: a test that counts entries must not be counting
    the previous test's.
    """
    async with database.begin() as connection:
        await connection.execute(text("DELETE FROM audit_log"))


@pytest.fixture
async def connection(database: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One transaction per test, rolled back afterwards."""
    async with database.connect() as conn:
        transaction = await conn.begin()
        yield conn
        await transaction.rollback()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    async with factory() as db_session:
        yield db_session


@pytest.fixture
async def api(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A client whose requests run inside the test's own transaction.

    Without the override the app would open its own connection, and rows the
    test just inserted — uncommitted — would be invisible to the handler.
    """

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture(autouse=True)
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    """Files land in a temporary directory, never in the repository's own.

    Autouse: a test that uploads without asking for this fixture would write
    into ``./uploads`` and leave it there.
    """
    local = LocalStorage(tmp_path / "uploads")
    set_storage(local)
    yield local
    set_storage(None)


# --- staff fixtures ---------------------------------------------------------------

#: Long enough to pass ``MIN_PASSWORD_LENGTH``; the same one everywhere so a
#: test that logs in reads as "the right password", not as a magic string.
PASSWORD = "correct-horse-battery"


async def make_staff(
    session: AsyncSession,
    *,
    email: str,
    role: Role,
    name: str = "Test Person",
    password: str = PASSWORD,
    is_blocked: bool = False,
) -> Staff:
    staff = Staff(
        email=email,
        name=name,
        role=role,
        password_hash=hash_password(password),
        is_blocked=is_blocked,
    )
    session.add(staff)
    await session.commit()
    await session.refresh(staff)
    return staff


def headers_for(staff: Staff) -> dict[str, str]:
    """An access token for an employee who really exists."""
    return bearer(issue_token(Audience.ADMIN, subject_id=staff.id, role=staff.role))


@pytest.fixture
async def owner(session: AsyncSession) -> Staff:
    return await make_staff(session, email="owner@example.uz", role=Role.OWNER)


@pytest.fixture
async def admin(session: AsyncSession) -> Staff:
    return await make_staff(session, email="admin@example.uz", role=Role.ADMIN)


# --- customer fixtures ---------------------------------------------------------------


async def make_customer(
    session: AsyncSession,
    *,
    email: str = "buyer@example.uz",
    first_name: str = "Test Buyer",
    password: str = PASSWORD,
    is_verified: bool = True,
    is_blocked: bool = False,
) -> Customer:
    """Verified by default — most tests are about what a real account can do.

    The unverified case is the interesting one and every test that wants it
    says so, rather than the other way round.
    """
    customer = Customer(
        email=email,
        first_name=first_name,
        password_hash=hash_password(password),
        email_verified_at=utcnow() if is_verified else None,
        is_blocked=is_blocked,
    )
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return customer


def customer_headers_for(customer: Customer) -> dict[str, str]:
    """An access token for a customer who really exists. No ``role`` claim —
    API.md §4 gives that to staff tokens only."""
    return bearer(issue_token(Audience.PUBLIC, subject_id=customer.id))


@pytest.fixture
async def customer(session: AsyncSession) -> Customer:
    return await make_customer(session)


# --- mail ---------------------------------------------------------------------------


@dataclass
class RecordingNotifier:
    """Stands in for SMTP and keeps what it was asked to send.

    Structural, not a subclass: the port is what ``send``/``verify`` look like,
    and a test double that has to inherit is a test double that breaks when the
    base class grows a helper.
    """

    channel: Channel = Channel.EMAIL
    sent: list[dict[str, Any]] = field(default_factory=list)
    #: Set to make every following ``send`` raise instead of record — a relay
    #: that is down, refusing the password, or simply not there. The type is an
    #: exception rather than a flag so a test can name the failure it means.
    fails_with: Exception | None = None

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        html: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        if self.fails_with is not None:
            raise self.fails_with
        self.sent.append(
            {
                "recipient": recipient,
                "subject": subject,
                "body": body,
                "html": html,
                "context": context or {},
            }
        )

    async def verify(self) -> bool:
        return True

    @property
    def last_code(self) -> str:
        # Read out of ``context`` rather than scraped from the body: what the
        # reader sees is a rendered message, so the body text is not the
        # contract.
        return str(self.sent[-1]["context"]["code"])

    @property
    def last_token(self) -> str:
        """The staff reset token — same idea as ``last_code``."""
        return str(self.sent[-1]["context"]["token"])


@pytest.fixture
def notifier() -> Iterator[RecordingNotifier]:
    recorder = RecordingNotifier()
    set_notifier(recorder)
    yield recorder
    set_notifier(None)


# --- payment provider fixtures -------------------------------------------------


#: The code every fake card provider answers, so a test does not have to say it
#: twice. Payme rather than Click because Click's card API is a per-merchant
#: add-on and may legitimately be absent (API.md §29).
CARD_PROVIDER = PaymentProviderCode.PAYME

#: A test card. Luhn-valid, and one of the numbers the schemes publish for
#: exactly this purpose — never a real PAN, not even a retired one.
TEST_CARD_NUMBER = "8600490744664608"
TEST_CARD_EXPIRE = "0329"


@dataclass
class FakeCardProvider:
    """Stands in for Payme's Subscribe API.

    Structural rather than a subclass, the reason ``RecordingNotifier`` gives.
    It records the numbers it is handed so a test can prove the card reached
    the adapter and stopped there.
    """

    code: PaymentProviderCode = CARD_PROVIDER
    #: The confirmation code this fake will accept.
    expected_code: str = "123456"
    #: Cards the provider currently holds, by token.
    tokens: dict[str, str] = field(default_factory=dict)
    #: Every number handed over, so a test can assert where it did *not* go.
    seen_numbers: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    #: Set to make ``register_card`` hand back an already-confirmed card.
    registers_verified: bool = False
    _next: int = 0

    def _registered(self, token: str, *, verified: bool) -> RegisteredCard:
        number = self.tokens[token]
        return RegisteredCard(
            token=token,
            masked_pan=f"{number[:6]}******{number[-4:]}",
            last4=number[-4:],
            bin=number[:6],
            brand="uzcard",
            expiry_month=3,
            expiry_year=2029,
            verified=verified,
            otp_sent_to=None if verified else "+9989**1234",
            otp_wait_seconds=None,
        )

    async def register_card(
        self, card: CardCredentials, *, save: bool = True
    ) -> RegisteredCard:
        self.seen_numbers.append(card.number)
        self._next += 1
        token = f"tok-{self._next}"
        self.tokens[token] = card.number
        return self._registered(token, verified=self.registers_verified)

    async def request_card_code(self, *, token: str) -> RegisteredCard:
        return self._registered(token, verified=False)

    async def verify_card(self, *, token: str, code: str) -> RegisteredCard:
        return self._registered(token, verified=code == self.expected_code)

    async def remove_card(self, *, token: str) -> None:
        self.removed.append(token)
        self.tokens.pop(token, None)

    async def charge_card(
        self,
        *,
        token: str,
        order_id: str,
        amount: Decimal,
        currency: str,
        reference: str,
    ) -> ChargeResult:
        return ChargeResult(status=TransactionStatus.PAID, provider_ref=f"rcp-{token}")

    # --- the redirect half of the port, unused by the card tests ---

    async def create_payment(
        self, *, order_id: str, amount: Decimal, currency: str, return_url: str
    ) -> str:
        return "https://example.invalid/pay"

    def verify_signature(self, headers: Mapping[str, str], body: bytes) -> bool:
        return True

    async def handle_callback(
        self, headers: Mapping[str, str], body: bytes
    ) -> CallbackResult:
        return CallbackResult(body={})

    async def refund(
        self, *, transaction_ref: str, amount: Decimal | None = None
    ) -> RefundResult:
        return RefundResult(status=RefundStatus.SUCCEEDED)

    async def status(self, *, transaction_ref: str) -> ChargeResult:
        return ChargeResult(status=TransactionStatus.PAID)

    async def verify(self) -> ProviderCheck:
        return ProviderCheck(ok=True)


@dataclass
class FakeHostedProvider(FakeCardProvider):
    """A provider with **no** card API — everything card-shaped is a 404.

    Inherits the redirect half and drops the five card methods, which is what
    makes ``isinstance(adapter, CardTokenProvider)`` false. This is the shape
    Click takes when its card-token contract is not enabled.
    """

    code: PaymentProviderCode = PaymentProviderCode.CLICK

    register_card = None  # type: ignore[assignment]
    request_card_code = None  # type: ignore[assignment]
    verify_card = None  # type: ignore[assignment]
    remove_card = None  # type: ignore[assignment]
    charge_card = None  # type: ignore[assignment]


@pytest.fixture
def card_provider() -> Iterator[FakeCardProvider]:
    """Pin a card-capable provider, and take the pin away afterwards."""
    fake = FakeCardProvider()
    set_payment_provider(CARD_PROVIDER, fake)
    yield fake
    clear_payment_overrides()
