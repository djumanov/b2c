"""``/public/profile/cards/`` — API.md §19.

Mirrors ``test_customer_passengers.py``: the same owner-scoping rules, the same
"somebody else's row is a 404" question, plus the register → confirm sequence
that a passenger does not have.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.payments import service as payments_service
from app.providers.payments import clear_overrides, set_provider
from app.providers.payments.base import PaymentProviderCode
from tests.integration.conftest import (
    TEST_CARD_EXPIRE,
    TEST_CARD_NUMBER,
    FakeCardProvider,
    FakeHostedProvider,
    customer_headers_for,
    make_customer,
)

CARDS = "/api/v1/public/profile/cards/"


@pytest.fixture
def customer_headers(customer: Customer) -> dict[str, str]:
    """A token for a customer who really exists — the card routes load the row."""
    return customer_headers_for(customer)


def _body(**overrides: object) -> dict[str, object]:
    return {
        "provider": "payme",
        "number": TEST_CARD_NUMBER,
        "expire": TEST_CARD_EXPIRE,
        **overrides,
    }


async def _saved(api: AsyncClient, headers: dict[str, str], **kw: object) -> dict:
    response = await api.post(CARDS, json=_body(**kw), headers=headers)
    assert response.status_code == 201, response.text
    data: dict = response.json()["data"]
    return data


async def _confirmed(
    api: AsyncClient, headers: dict[str, str], card_provider: FakeCardProvider, **kw
) -> dict:
    card = await _saved(api, headers, **kw)
    response = await api.post(
        f"{CARDS}{card['id']}/verify/",
        json={"code": card_provider.expected_code},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data: dict = response.json()["data"]
    return data


# --- the happy path ----------------------------------------------------------------


async def test_a_card_is_saved_pending_and_confirmed_with_the_code(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _saved(api, customer_headers, label="Asosiy karta")

    assert card["status"] == "pending_verification"
    assert card["masked_pan"] == "860049******4608"
    assert card["last4"] == "4608"
    assert card["brand"] == "uzcard"
    assert card["label"] == "Asosiy karta"
    assert card["otp_sent_to"] == "+9989**1234"
    assert card["otp_expires_at"] is not None
    # Not yet usable, so not yet the default.
    assert card["is_default"] is False

    confirmed = await api.post(
        f"{CARDS}{card['id']}/verify/",
        json={"code": card_provider.expected_code},
        headers=customer_headers,
    )
    assert confirmed.status_code == 200
    data = confirmed.json()["data"]
    assert data["status"] == "active"
    # The OTP fields stop meaning anything and stop being shown.
    assert data["otp_sent_to"] is None
    assert data["otp_expires_at"] is None
    # First usable card, so checkout has something to offer.
    assert data["is_default"] is True


async def test_the_response_never_carries_the_number(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _saved(api, customer_headers)
    assert TEST_CARD_NUMBER not in str(card)
    assert "number" not in card
    assert "expire" not in card

    listed = await api.get(CARDS, headers=customer_headers)
    assert TEST_CARD_NUMBER not in listed.text


async def test_a_provider_that_returns_a_confirmed_card_skips_the_code(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card_provider.registers_verified = True
    card = await _saved(api, customer_headers)
    assert card["status"] == "active"
    assert card["is_default"] is True


# --- the code ----------------------------------------------------------------------


async def test_a_wrong_code_is_refused_without_saying_why(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _saved(api, customer_headers)
    response = await api.post(
        f"{CARDS}{card['id']}/verify/",
        json={"code": "000000"},
        headers=customer_headers,
    )
    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == "validation"
    assert error["field"] == "code"
    # The message must not distinguish wrong from expired from exhausted.
    assert error["message"] == "This code is invalid or has expired"


async def test_the_card_is_burned_after_three_wrong_codes(
    api: AsyncClient,
    customer_headers: dict[str, str],
    card_provider: FakeCardProvider,
) -> None:
    card = await _saved(api, customer_headers)

    for _ in range(payments_service.CARD_OTP_MAX_ATTEMPTS):
        response = await api.post(
            f"{CARDS}{card['id']}/verify/",
            json={"code": "000000"},
            headers=customer_headers,
        )
        assert response.status_code == 422

    # Gone from the customer's list, and handed back to the provider rather
    # than left hanging on the merchant account.
    assert await api.get(f"{CARDS}{card['id']}/", headers=customer_headers) is not None
    gone = await api.get(f"{CARDS}{card['id']}/", headers=customer_headers)
    assert gone.status_code == 404
    assert card_provider.removed == ["tok-1"]

    # And the right code no longer rescues it.
    late = await api.post(
        f"{CARDS}{card['id']}/verify/",
        json={"code": card_provider.expected_code},
        headers=customer_headers,
    )
    assert late.status_code == 404


async def test_confirming_twice_is_a_conflict(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _confirmed(api, customer_headers, card_provider)
    again = await api.post(
        f"{CARDS}{card['id']}/verify/",
        json={"code": card_provider.expected_code},
        headers=customer_headers,
    )
    assert again.status_code == 409


async def test_resending_too_soon_is_refused_with_a_retry_after(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _saved(api, customer_headers)
    response = await api.post(
        f"{CARDS}{card['id']}/resend-otp/", headers=customer_headers
    )
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


async def test_resending_after_the_cooldown_sends_again(
    api: AsyncClient,
    session: AsyncSession,
    customer_headers: dict[str, str],
    card_provider: FakeCardProvider,
) -> None:
    card = await _saved(api, customer_headers)
    # Push the last send back beyond the cooldown rather than sleeping through it.
    await session.execute(
        text(
            "UPDATE customer_cards SET otp_sent_at = otp_sent_at"
            " - interval '10 minutes' WHERE id = :id"
        ),
        {"id": uuid.UUID(card["id"])},
    )
    response = await api.post(
        f"{CARDS}{card['id']}/resend-otp/", headers=customer_headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["otp_sent_to"] == "+9989**1234"


async def test_an_expired_code_is_refused(
    api: AsyncClient,
    session: AsyncSession,
    customer_headers: dict[str, str],
    card_provider: FakeCardProvider,
) -> None:
    card = await _saved(api, customer_headers)
    await session.execute(
        text(
            "UPDATE customer_cards SET otp_expires_at = now() - interval '1 minute'"
            " WHERE id = :id"
        ),
        {"id": uuid.UUID(card["id"])},
    )
    response = await api.post(
        f"{CARDS}{card['id']}/verify/",
        json={"code": card_provider.expected_code},
        headers=customer_headers,
    )
    assert response.status_code == 422
    assert (
        response.json()["errors"][0]["message"] == "This code is invalid or has expired"
    )


# --- validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "8600490744664604",  # right length, fails Luhn
        "123",  # too short
        "not-a-number",
    ],
)
async def test_a_bad_number_never_reaches_the_provider(
    api: AsyncClient,
    customer_headers: dict[str, str],
    card_provider: FakeCardProvider,
    number: str,
) -> None:
    response = await api.post(
        CARDS, json=_body(number=number), headers=customer_headers
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "number"
    # The point of validating locally: the merchant credentials were not spent.
    assert card_provider.seen_numbers == []


async def test_a_bad_expiry_is_refused(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    response = await api.post(
        CARDS, json=_body(expire="1329"), headers=customer_headers
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "expire"


async def test_spaces_in_the_number_are_accepted(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    response = await api.post(
        CARDS, json=_body(number="8600 4907 4466 4608"), headers=customer_headers
    )
    assert response.status_code == 201
    assert card_provider.seen_numbers == [TEST_CARD_NUMBER]


async def test_the_same_card_cannot_be_saved_twice(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    await _confirmed(api, customer_headers, card_provider)
    card_provider.registers_verified = True
    duplicate = await api.post(CARDS, json=_body(), headers=customer_headers)

    assert duplicate.status_code == 422
    assert duplicate.json()["errors"][0]["field"] == "number"
    # The token minted for the rejected attempt was handed back, not orphaned
    # on the merchant account.
    assert card_provider.removed == ["tok-2"]


async def test_a_provider_without_a_card_api_is_a_404(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    set_provider(PaymentProviderCode.CLICK, FakeHostedProvider())
    try:
        response = await api.post(
            CARDS, json=_body(provider="click"), headers=customer_headers
        )
        assert response.status_code == 404
    finally:
        clear_overrides()


async def test_an_unconfigured_provider_is_a_404(
    api: AsyncClient, customer_headers: dict[str, str]
) -> None:
    """No pin, no credentials in the database — indistinguishable from absent."""
    response = await api.post(CARDS, json=_body(), headers=customer_headers)
    assert response.status_code == 404


# --- the default --------------------------------------------------------------------


async def test_setting_a_default_clears_the_previous_one(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    first = await _confirmed(api, customer_headers, card_provider)
    assert first["is_default"] is True

    card_provider.tokens.clear()
    second = await _confirmed(
        api, customer_headers, card_provider, number="8600123456789012"
    )
    assert second["is_default"] is False

    promoted = await api.post(
        f"{CARDS}{second['id']}/default/", headers=customer_headers
    )
    assert promoted.status_code == 200
    assert promoted.json()["data"]["is_default"] is True

    listed = await api.get(CARDS, headers=customer_headers)
    defaults = [row for row in listed.json()["data"] if row["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == second["id"]


async def test_an_unconfirmed_card_cannot_be_made_default(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _saved(api, customer_headers)
    response = await api.post(f"{CARDS}{card['id']}/default/", headers=customer_headers)
    assert response.status_code == 409


# --- editing and removing ------------------------------------------------------------


async def test_patch_changes_the_label_and_nothing_else(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _confirmed(api, customer_headers, card_provider)

    renamed = await api.patch(
        f"{CARDS}{card['id']}/", json={"label": "Ish kartasi"}, headers=customer_headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["label"] == "Ish kartasi"

    refused = await api.patch(
        f"{CARDS}{card['id']}/", json={"brand": "visa"}, headers=customer_headers
    )
    assert refused.status_code == 422


async def test_delete_forgets_the_card_here_and_at_the_provider(
    api: AsyncClient,
    session: AsyncSession,
    customer_headers: dict[str, str],
    card_provider: FakeCardProvider,
) -> None:
    card = await _confirmed(api, customer_headers, card_provider)

    response = await api.delete(f"{CARDS}{card['id']}/", headers=customer_headers)
    assert response.status_code == 204
    assert card_provider.removed == ["tok-1"]

    assert (
        await api.get(f"{CARDS}{card['id']}/", headers=customer_headers)
    ).status_code == 404

    # The row survives the soft delete, but the token does not.
    row = (
        await session.execute(
            text(
                "SELECT status, token, key_version FROM customer_cards WHERE id = :id"
            ),
            {"id": uuid.UUID(card["id"])},
        )
    ).one()
    assert row.status == "removed"
    assert row.token is None
    assert row.key_version is None


async def test_a_provider_that_refuses_removal_still_frees_the_customer(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _confirmed(api, customer_headers, card_provider)

    async def refuse(*, token: str) -> None:
        raise RuntimeError("provider is down")

    card_provider.remove_card = refuse  # type: ignore[method-assign]

    response = await api.delete(f"{CARDS}{card['id']}/", headers=customer_headers)
    assert response.status_code == 204
    assert (
        await api.get(f"{CARDS}{card['id']}/", headers=customer_headers)
    ).status_code == 404


# --- ownership and auth ---------------------------------------------------------------


async def test_another_customers_card_is_a_404_not_a_403(
    api: AsyncClient,
    session: AsyncSession,
    customer_headers: dict[str, str],
    card_provider: FakeCardProvider,
) -> None:
    card = await _confirmed(api, customer_headers, card_provider)
    stranger = await make_customer(session, email="stranger@mail.uz")
    theirs = customer_headers_for(stranger)

    for method, url in (
        ("get", f"{CARDS}{card['id']}/"),
        ("patch", f"{CARDS}{card['id']}/"),
        ("delete", f"{CARDS}{card['id']}/"),
    ):
        response = await getattr(api, method)(
            url,
            headers=theirs,
            **({"json": {"label": "x"}} if method == "patch" else {}),
        )
        assert response.status_code == 404, f"{method} {url}"

    # And it is not in their list either.
    listed = await api.get(CARDS, headers=theirs)
    assert listed.json()["data"] == []


async def test_the_list_needs_a_token(api: AsyncClient) -> None:
    assert (await api.get(CARDS)).status_code == 401


async def test_a_deleted_card_is_not_listed(
    api: AsyncClient, customer_headers: dict[str, str], card_provider: FakeCardProvider
) -> None:
    card = await _confirmed(api, customer_headers, card_provider)
    await api.delete(f"{CARDS}{card['id']}/", headers=customer_headers)

    listed = await api.get(CARDS, headers=customer_headers)
    assert listed.json()["data"] == []
    assert listed.json()["meta"]["total"] == 0
