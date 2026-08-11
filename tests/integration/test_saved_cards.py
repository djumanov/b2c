"""``/public/profile/cards/`` — API.md §19.

Mirrors ``test_customer_passengers.py``: the same owner-scoping rules, the same
"somebody else's row is a 404" question. A card is simpler than a passenger —
saving is one step and immediately final — so what this file adds instead is
the derived-field checks: everything the customer sees (``masked_pan``,
``last4``, ``brand``, expiry) comes from the number itself, locally.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt
from app.modules.customers.models import Customer
from tests.integration.conftest import (
    TEST_CARD_EXPIRE,
    TEST_CARD_NUMBER,
    customer_headers_for,
    make_customer,
)

CARDS = "/api/v1/public/profile/cards/"


@pytest.fixture
def customer_headers(customer: Customer) -> dict[str, str]:
    """A token for a customer who really exists — the card routes load the row."""
    return customer_headers_for(customer)


def _body(**overrides: object) -> dict[str, object]:
    return {"number": TEST_CARD_NUMBER, "expire": TEST_CARD_EXPIRE, **overrides}


async def _saved(api: AsyncClient, headers: dict[str, str], **kw: object) -> dict:
    response = await api.post(CARDS, json=_body(**kw), headers=headers)
    assert response.status_code == 201, response.text
    data: dict = response.json()["data"]
    return data


async def _rows(session: AsyncSession) -> list:
    return list(
        (
            await session.execute(
                text("SELECT pan, deleted_at, key_version FROM customer_cards")
            )
        ).all()
    )


# --- the happy path ----------------------------------------------------------------


async def test_a_card_is_saved_in_one_step_and_immediately_listed(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    card = await _saved(api, customer_headers)

    assert card["masked_pan"] == "860049******4608"
    assert card["last4"] == "4608"
    assert card["brand"] == "uzcard"
    assert card["expiry_month"] == 3
    assert card["expiry_year"] == 2029
    # No confirmation machinery: none of the old two-step fields exist.
    for gone in ("status", "provider", "label", "is_default", "otp_sent_to"):
        assert gone not in card

    listed = await api.get(CARDS, headers=customer_headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["data"]] == [card["id"]]


async def test_the_row_holds_the_number_encrypted_not_in_the_clear(
    api: AsyncClient, session: AsyncSession, customer_headers: dict[str, str]
) -> None:
    """The autofill promise: the number is retrievable, but only by decrypting."""
    await _saved(api, customer_headers)

    [(pan, deleted_at, key_version)] = await _rows(session)
    assert deleted_at is None
    assert pan is not None and TEST_CARD_NUMBER not in pan
    assert decrypt(pan, key_version) == TEST_CARD_NUMBER


async def test_no_response_ever_carries_the_number_or_an_expire_field(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    card = await _saved(api, customer_headers)
    detail = await api.get(f"{CARDS}{card['id']}/", headers=customer_headers)
    listed = await api.get(CARDS, headers=customer_headers)

    for response in (detail, listed):
        assert response.status_code == 200
        assert TEST_CARD_NUMBER not in response.text
        assert "expire" not in response.text
    assert "number" not in detail.json()["data"]


@pytest.mark.parametrize(
    ("number", "brand"),
    [
        ("8600490744664608", "uzcard"),
        ("9860010000000009", "humo"),
        ("4111111111111111", "visa"),
        ("5555555555554444", "mastercard"),
        # An unknown prefix is not an error — the card still saves (API.md §19).
        ("6011000990139424", None),
    ],
)
async def test_the_brand_comes_from_the_bin_locally(
    api: AsyncClient, customer_headers: dict[str, str], number: str, brand: str | None
) -> None:
    card = await _saved(api, customer_headers, number=number)
    assert card["brand"] == brand
    assert card["last4"] == number[-4:]


async def test_separators_in_the_number_are_accepted(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    spaced = " ".join(
        TEST_CARD_NUMBER[i : i + 4] for i in range(0, len(TEST_CARD_NUMBER), 4)
    )
    card = await _saved(api, customer_headers, number=spaced)
    assert card["last4"] == TEST_CARD_NUMBER[-4:]


# --- validation --------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "8600",  # too short
        TEST_CARD_NUMBER[:-1] + "0",  # right length, wrong check digit
        "not-a-number",
    ],
)
async def test_a_bad_number_is_a_422_and_nothing_is_stored(
    api: AsyncClient,
    session: AsyncSession,
    customer_headers: dict[str, str],
    number: str,
) -> None:
    response = await api.post(
        CARDS, json=_body(number=number), headers=customer_headers
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "number"
    assert await _rows(session) == []


async def test_a_bad_expiry_is_a_422_on_its_own_field(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    response = await api.post(
        CARDS, json=_body(expire="1329"), headers=customer_headers
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "expire"


async def test_the_same_card_cannot_be_saved_twice(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    await _saved(api, customer_headers)
    response = await api.post(CARDS, json=_body(), headers=customer_headers)
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "number"


async def test_a_deleted_card_frees_its_identity_slot(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    """The unique index covers live rows only — saving again must work."""
    card = await _saved(api, customer_headers)
    assert (
        await api.delete(f"{CARDS}{card['id']}/", headers=customer_headers)
    ).status_code == 204
    again = await _saved(api, customer_headers)
    assert again["id"] != card["id"]


# --- ownership ---------------------------------------------------------------------


async def test_somebody_elses_card_is_a_404_not_a_403(
    api: AsyncClient, session: AsyncSession, customer_headers: dict[str, str]
) -> None:
    card = await _saved(api, customer_headers)
    other = await make_customer(session, email="other@example.com")
    theirs = customer_headers_for(other)

    for method, url in (
        ("GET", f"{CARDS}{card['id']}/"),
        ("DELETE", f"{CARDS}{card['id']}/"),
    ):
        response = await api.request(method, url, headers=theirs)
        assert response.status_code == 404, (method, response.text)


async def test_a_random_id_is_a_404(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    response = await api.get(f"{CARDS}{uuid.uuid4()}/", headers=customer_headers)
    assert response.status_code == 404


async def test_the_routes_require_a_token(api: AsyncClient) -> None:
    assert (await api.get(CARDS)).status_code == 401
    assert (await api.post(CARDS, json=_body())).status_code == 401


# --- deleting ----------------------------------------------------------------------


async def test_delete_soft_deletes_the_row_and_erases_the_ciphertext(
    api: AsyncClient, session: AsyncSession, customer_headers: dict[str, str]
) -> None:
    card = await _saved(api, customer_headers)

    response = await api.delete(f"{CARDS}{card['id']}/", headers=customer_headers)
    assert response.status_code == 204

    listed = await api.get(CARDS, headers=customer_headers)
    assert listed.json()["data"] == []

    # The row survives for support; the ciphertext does not (API.md §19).
    [(pan, deleted_at, key_version)] = await _rows(session)
    assert deleted_at is not None
    assert pan is None
    assert key_version is None


async def test_account_deletion_erases_the_cards_too(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """The ``forget_cards`` seam: ``customers`` calls it on account deletion."""
    headers = customer_headers_for(customer)
    await _saved(api, headers)

    response = await api.request(
        "DELETE",
        "/api/v1/public/profile/",
        json={"reasons": ["No longer needed"]},
        headers=headers,
    )
    assert response.status_code == 204, response.text

    [(pan, deleted_at, _)] = await _rows(session)
    assert deleted_at is not None
    assert pan is None


# --- listing -----------------------------------------------------------------------


async def test_search_filters_by_last4(
    api: AsyncClient, session: AsyncSession, customer_headers: dict[str, str]
) -> None:
    first = await _saved(api, customer_headers)
    second = await _saved(api, customer_headers, number="4111111111111111")

    listed = await api.get(CARDS, headers=customer_headers)
    assert {row["id"] for row in listed.json()["data"]} == {first["id"], second["id"]}

    found = await api.get(CARDS, params={"search": "4608"}, headers=customer_headers)
    assert [row["id"] for row in found.json()["data"]] == [first["id"]]


async def test_newest_card_comes_first(
    api: AsyncClient, session: AsyncSession, customer_headers: dict[str, str]
) -> None:
    """Default ordering is ``-created_at`` (API.md §19).

    Inside one test transaction ``now()`` is pinned, so the rows are nudged
    apart explicitly — otherwise the order would fall to the id tiebreak and
    the assertion would be about UUID luck.
    """
    first = await _saved(api, customer_headers)
    second = await _saved(api, customer_headers, number="4111111111111111")
    await session.execute(
        text(
            "UPDATE customer_cards SET created_at = created_at - interval '1 minute'"
            " WHERE id = :id"
        ),
        {"id": first["id"]},
    )

    listed = await api.get(CARDS, headers=customer_headers)
    assert [row["id"] for row in listed.json()["data"]] == [second["id"], first["id"]]
