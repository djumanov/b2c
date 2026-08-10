"""The card number reaches the adapter and stops — PROJECT.md §13, D7.

This is the regression test the whole saved-card design exists to pass, so it
does not check the columns we happen to have written. It sweeps
``information_schema`` and asserts the number is absent from **every** text-ish
column of **every** table — which is the form that still holds after somebody
adds a column next year without reading this file.
"""

import structlog
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import redact
from app.modules.customers.models import Customer
from app.modules.payments.schemas import CardCreateIn
from app.providers.payments.base import CardCredentials
from tests.integration.conftest import (
    TEST_CARD_EXPIRE,
    TEST_CARD_NUMBER,
    FakeCardProvider,
    customer_headers_for,
)

CARDS = "/api/v1/public/profile/cards/"

#: Every spelling of the number a careless writer could produce.
_FORBIDDEN = (
    TEST_CARD_NUMBER,
    TEST_CARD_NUMBER[4:],
    " ".join(TEST_CARD_NUMBER[i : i + 4] for i in range(0, len(TEST_CARD_NUMBER), 4)),
)

_TEXT_TYPES = ("character varying", "text", "character", "json", "jsonb")


async def _run_the_whole_flow(
    api: AsyncClient, headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    created = await api.post(
        CARDS,
        json={
            "provider": "payme",
            "number": TEST_CARD_NUMBER,
            "expire": TEST_CARD_EXPIRE,
            "label": "Asosiy",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    card_id = created.json()["data"]["id"]

    verified = await api.post(
        f"{CARDS}{card_id}/verify/",
        json={"code": card_provider.expected_code},
        headers=headers,
    )
    assert verified.status_code == 200, verified.text

    # And a failing path too — an exception carries different things than a
    # success does.
    await api.post(
        CARDS,
        json={"provider": "payme", "number": TEST_CARD_NUMBER, "expire": "0329"},
        headers=headers,
    )


async def test_the_number_is_in_no_column_of_no_table(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    card_provider: FakeCardProvider,
) -> None:
    await _run_the_whole_flow(api, customer_headers_for(customer), card_provider)

    # It really did reach the adapter — otherwise this test proves nothing.
    assert card_provider.seen_numbers == [TEST_CARD_NUMBER, TEST_CARD_NUMBER]

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

    assert hits == [], f"the card number was written to: {', '.join(hits)}"


async def test_the_number_is_in_no_log_line(
    api: AsyncClient,
    customer: Customer,
    card_provider: FakeCardProvider,
) -> None:
    with structlog.testing.capture_logs() as captured:
        await _run_the_whole_flow(api, customer_headers_for(customer), card_provider)

    assert captured, "nothing was logged — the sweep would pass vacuously"
    rendered = str(captured)
    for value in _FORBIDDEN:
        assert value not in rendered


def test_the_schema_hides_the_number_from_every_accidental_printer() -> None:
    data = CardCreateIn(
        provider="payme",  # type: ignore[arg-type]
        number=TEST_CARD_NUMBER,  # type: ignore[arg-type]
        expire=TEST_CARD_EXPIRE,  # type: ignore[arg-type]
    )
    assert TEST_CARD_NUMBER not in repr(data)
    assert TEST_CARD_NUMBER not in str(data)
    assert TEST_CARD_NUMBER not in str(data.model_dump())
    # Only ``get_secret_value`` gets at it, and it is called exactly once.
    assert data.number.get_secret_value() == TEST_CARD_NUMBER


def test_a_validation_error_does_not_quote_the_number() -> None:
    """Pydantic echoes the offending input by default — ``SecretStr`` must not."""
    bad = TEST_CARD_NUMBER[:-1] + "0"  # right length, wrong check digit
    try:
        CardCreateIn(
            provider="payme",  # type: ignore[arg-type]
            number=bad,  # type: ignore[arg-type]
            expire=TEST_CARD_EXPIRE,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        assert bad not in str(exc)
    else:  # pragma: no cover - the number above is deliberately invalid
        raise AssertionError("expected a validation error")


def test_the_credentials_dataclass_does_not_print_itself() -> None:
    card = CardCredentials(number=TEST_CARD_NUMBER, expire=TEST_CARD_EXPIRE)
    assert TEST_CARD_NUMBER not in repr(card)
    assert repr(card) == "CardCredentials(last4='4608')"


def test_redaction_covers_the_card_keys_but_not_code() -> None:
    assert redact({"number": TEST_CARD_NUMBER})["number"] == "[redacted]"
    assert redact({"expire": "0329"})["expire"] == "[redacted]"
    assert redact({"card_token": "tok-1"})["card_token"] == "[redacted]"
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
