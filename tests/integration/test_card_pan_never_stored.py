"""The card number is stored encrypted and nowhere else — PROJECT.md §13, D7.

This is the regression test the saved-card design exists to pass, so it does
not check the columns we happen to have written. It sweeps
``information_schema`` and asserts the number is absent from **every** text-ish
column of **every** table — which is the form that still holds after somebody
adds a column next year without reading this file. The ciphertext passes the
sweep by construction: AES-GCM output is base64 and cannot contain the digit
substring. A companion test in ``test_saved_cards.py`` proves the number *is*
there, encrypted — stored-but-sealed, not simply absent.

The rest pins the leak paths around the row: log lines, ``repr``/``str``/
``model_dump``, a ``ValidationError`` echoing its input, the redaction map, and
the OpenAPI schema.
"""

import structlog
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import redact
from app.modules.customers.models import Customer
from app.modules.payments.schemas import CardCreateIn
from app.modules.payments.service import CardSecret
from tests.integration.conftest import (
    TEST_CARD_EXPIRE,
    TEST_CARD_NUMBER,
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


async def _run_the_whole_flow(api: AsyncClient, headers: dict[str, str]) -> None:
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


async def test_the_number_is_in_no_column_of_no_table_in_the_clear(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
) -> None:
    await _run_the_whole_flow(api, customer_headers_for(customer))

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
    customer: Customer,
) -> None:
    with structlog.testing.capture_logs() as captured:
        await _run_the_whole_flow(api, customer_headers_for(customer))

    assert captured, "nothing was logged — the sweep would pass vacuously"
    rendered = str(captured)
    for value in _FORBIDDEN:
        assert value not in rendered


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
    secret = CardSecret(number=TEST_CARD_NUMBER, expire=TEST_CARD_EXPIRE)
    assert TEST_CARD_NUMBER not in repr(secret)
    assert repr(secret) == "CardSecret(last4='4608')"


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
