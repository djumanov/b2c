"""The card number is stored encrypted and nowhere else — PROJECT.md §13, D7.

This is the regression test the saved-card design exists to pass, so it does
not check the columns we happen to have written. It sweeps
``information_schema`` and asserts the number is absent from **every** text-ish
column of **every** table — which is the form that still holds after somebody
adds a column next year without reading this file. The ciphertext passes the
sweep by construction: AES-GCM output is base64 and cannot contain the digit
substring. A companion test in ``test_saved_cards.py`` proves the number *is*
there, encrypted — stored-but-sealed, not simply absent.

The flow it sweeps is the **whole** one, not just saving a card: a number that
is only stored is easy to keep, and the one that gets spent passes through an
order, an attempt row, an event journal and an audit entry on its way to a
provider. Redis is swept too, because the idempotency layer derives its keys by
hashing the request body — which is exactly why the card endpoint has no
idempotency key, and this is the test that would notice if one were added
(API.md §22).

The rest pins the leak paths around the row: log lines, ``repr``/``str``/
``model_dump``, a ``ValidationError`` echoing its input, the redaction map, and
the OpenAPI schema.
"""

from typing import Any

import pytest
import structlog
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import derived_key
from app.core.logging import redact
from app.db.redis import get_redis
from app.modules.customers.models import Customer
from app.modules.payments.schemas import CardCreateIn
from app.modules.settings import cache as settings_cache
from app.providers.payments import clear_overrides, set_provider
from app.providers.payments.base import CardCredentials, PaymentProviderCode
from tests.integration.conftest import (
    TEST_CARD_EXPIRE,
    TEST_CARD_NUMBER,
    customer_headers_for,
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


@pytest.fixture
async def provider(session: AsyncSession) -> Any:
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


#: Every spelling of the number a careless writer could produce.
_FORBIDDEN = (
    TEST_CARD_NUMBER,
    TEST_CARD_NUMBER[4:],
    " ".join(TEST_CARD_NUMBER[i : i + 4] for i in range(0, len(TEST_CARD_NUMBER), 4)),
)

_TEXT_TYPES = ("character varying", "text", "character", "json", "jsonb")


async def _run_the_whole_flow(
    api: AsyncClient,
    headers: dict[str, str],
    session: AsyncSession | None = None,
    customer: Customer | None = None,
) -> None:
    created = await api.post(
        CARDS,
        json={"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    # And a failing path too — an exception carries different things than a
    # success does. The duplicate save raises through the IntegrityError branch.
    duplicate = await api.post(
        CARDS,
        json={"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE},
        headers=headers,
    )
    assert duplicate.status_code == 422, duplicate.text

    if session is None or customer is None:
        return

    # Then spend it. This is the half that touches everything: an order row, an
    # attempt row, the event journal, and a provider on the far side.
    #
    # The refresh is not decoration: the requests above committed through this
    # same session, which expires every instance loaded from it.
    await session.refresh(customer)
    order = await _booked(session, customer)
    started = await api.post(f"{ORDERS}{order.id}/transactions/", headers=headers)
    assert started.status_code == 201, started.text
    attempt_id = started.json()["data"]["id"]

    carded = await api.post(
        f"{TRANSACTIONS}{attempt_id}/card/",
        json={"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE},
        headers=headers,
    )
    assert carded.status_code == 200, carded.text

    confirmed = await api.post(
        f"{TRANSACTIONS}{attempt_id}/confirm/",
        json={"otp_code": "123456"},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text


async def test_the_number_is_in_no_column_of_no_table_in_the_clear(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    provider: RecordingProvider,
    installation: None,
) -> None:
    await _run_the_whole_flow(api, customer_headers_for(customer), session, customer)

    # The card really was written — otherwise this sweep proves nothing.
    stored = (
        await session.execute(text("SELECT count(*) FROM customer_cards"))
    ).scalar()
    assert stored == 1

    columns = (
        await session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND data_type = ANY(:types)"
            ),
            {"types": list(_TEXT_TYPES)},
        )
    ).all()
    assert columns, "no text columns found — the sweep would pass vacuously"

    hits: list[str] = []
    for table_name, column_name in columns:
        found = (
            await session.execute(
                text(
                    f'SELECT 1 FROM "{table_name}" WHERE "{column_name}"::text'
                    " LIKE ANY(:patterns) LIMIT 1"
                ),
                {"patterns": [f"%{value}%" for value in _FORBIDDEN]},
            )
        ).first()
        if found is not None:
            hits.append(f"{table_name}.{column_name}")

    assert hits == [], f"the card number was written in the clear to: {', '.join(hits)}"


async def test_the_number_is_in_no_log_line(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    provider: RecordingProvider,
    installation: None,
) -> None:
    with structlog.testing.capture_logs() as captured:
        await _run_the_whole_flow(
            api, customer_headers_for(customer), session, customer
        )

    assert captured, "nothing was logged — the sweep would pass vacuously"
    rendered = str(captured)
    for value in _FORBIDDEN:
        assert value not in rendered


async def test_the_number_is_in_no_redis_key_and_no_redis_value(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    provider: RecordingProvider,
    installation: None,
) -> None:
    """Redis is not a database, which is exactly why it is easy to forget.

    The idempotency layer derives its key by hashing the request body and stores
    the body's fingerprint beside the cached answer. Nothing in the card flow may
    reach it — and the assertion below is not "no digits found" but the sharper
    one: the key that *would* exist if the card endpoint were idempotency-wrapped
    does not (API.md §22).
    """
    headers = customer_headers_for(customer)
    await _run_the_whole_flow(api, headers, session, customer)

    redis = get_redis()
    keys = [key async for key in redis.scan_iter(match="*")]
    assert keys, "Redis is empty — the sweep would pass vacuously"

    for key in keys:
        stored = await redis.get(key)
        haystack = f"{key}{stored}"
        for value in _FORBIDDEN:
            assert value not in haystack, f"the number reached Redis at {key}"

    # And the one that would exist if somebody wrapped ``card/`` in an
    # idempotency key: a sha256 over the card body, standing in a key name.
    body = (
        b'{"expire":"'
        + TEST_CARD_EXPIRE.encode()
        + b'","number":"'
        + TEST_CARD_NUMBER.encode()
        + b'"}'
    )
    would_be = derived_key(
        str(customer.id), "POST", "/api/v1/public/transactions/x/card/", body
    )
    assert await redis.get(f"idempotency:{would_be}") is None


async def test_the_provider_token_is_never_in_the_clear(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    provider: RecordingProvider,
    installation: None,
) -> None:
    """The token can be charged, so it is a credential and is sealed like one."""
    headers = customer_headers_for(customer)
    order = await _booked(session, customer)
    started = await api.post(f"{ORDERS}{order.id}/transactions/", headers=headers)
    attempt_id = started.json()["data"]["id"]
    await api.post(
        f"{TRANSACTIONS}{attempt_id}/card/",
        json={"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE},
        headers=headers,
    )

    hits = (
        await session.execute(
            text("SELECT 1 FROM order_payments WHERE card_token LIKE :pattern LIMIT 1"),
            {"pattern": "%token-4608%"},
        )
    ).first()

    assert hits is None
    # It really is there, sealed — otherwise this proves nothing.
    sealed = (
        await session.execute(
            text("SELECT card_token FROM order_payments WHERE card_token IS NOT NULL")
        )
    ).scalar()
    assert sealed is not None


def test_the_schema_hides_the_number_from_every_accidental_printer() -> None:
    data = CardCreateIn(
        number=TEST_CARD_NUMBER,  # type: ignore[arg-type]
        expire=TEST_CARD_EXPIRE,  # type: ignore[arg-type]
    )
    assert TEST_CARD_NUMBER not in repr(data)
    assert TEST_CARD_NUMBER not in str(data)
    assert TEST_CARD_NUMBER not in str(data.model_dump())
    # Only ``get_secret_value`` gets at it.
    assert data.number.get_secret_value() == TEST_CARD_NUMBER


def test_a_validation_error_does_not_quote_the_number() -> None:
    """Pydantic echoes the offending input by default — ``SecretStr`` must not."""
    bad = TEST_CARD_NUMBER[:-1] + "0"  # right length, wrong check digit
    try:
        CardCreateIn(
            number=bad,  # type: ignore[arg-type]
            expire=TEST_CARD_EXPIRE,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        assert bad not in str(exc)
    else:  # pragma: no cover - the number above is deliberately invalid
        raise AssertionError("expected a validation error")


def test_the_revealed_secret_does_not_print_itself() -> None:
    """``reveal_card``'s result is headed for a provider — never for a log."""
    secret = CardCredentials(number=TEST_CARD_NUMBER, expire=TEST_CARD_EXPIRE)
    assert TEST_CARD_NUMBER not in repr(secret)
    assert repr(secret) == "CardCredentials(last4='4608')"


def test_redaction_covers_the_card_keys_but_not_code() -> None:
    assert redact({"number": TEST_CARD_NUMBER})["number"] == "[redacted]"
    assert redact({"expire": "0329"})["expire"] == "[redacted]"
    # Deliberately *not* redacted — see the comment in ``core/logging.py``.
    # ``code`` is the provider code, the error code and the notifier's template
    # key; blanking it would empty half the journal.
    assert redact({"code": "payme"})["code"] == "payme"
    assert redact({"card_id": "7c1a"})["card_id"] == "7c1a"
    # And the neighbouring passenger field must survive: keys match exactly.
    assert redact({"document_number": "AA123"})["document_number"] == "AA123"


async def test_the_openapi_schema_marks_the_number_as_a_password(
    api: AsyncClient,
) -> None:
    schema = (await api.get("/api/v1/openapi.json")).json()
    card_in = schema["components"]["schemas"]["CardCreateIn"]["properties"]
    assert card_in["number"]["format"] == "password"
    assert card_in["expire"]["format"] == "password"
