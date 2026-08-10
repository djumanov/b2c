"""Saved passengers — API.md §19, on §8's standard CRUD pattern.

The one behaviour worth more than a round trip is ownership: every query is
scoped by the account that saved the row, so another customer's passenger is a
404 rather than a 403. Telling the two apart would let a caller learn which ids
exist across the whole installation.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from tests.integration.conftest import customer_headers_for, make_customer

PASSENGERS = "/api/v1/public/profile/passengers/"

AZIZ = {
    "first_name": "Aziz",
    "last_name": "Karimov",
    "birth_date": "1995-04-17",
    "document_type": "passport",
    "document_number": "AA1234567",
}


async def _create(
    api: AsyncClient, customer: Customer, **overrides: object
) -> dict[str, object]:
    response = await api.post(
        PASSENGERS, headers=customer_headers_for(customer), json={**AZIZ, **overrides}
    )
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()["data"]
    return data


async def test_the_crud_round_trip(api: AsyncClient, customer: Customer) -> None:
    created = await _create(api, customer)
    assert created["document_number"] == "AA1234567"
    assert created["created_at"] and created["updated_at"]

    fetched = await api.get(
        f"{PASSENGERS}{created['id']}/", headers=customer_headers_for(customer)
    )
    assert fetched.status_code == 200
    assert fetched.json()["data"] == created

    changed = await api.patch(
        f"{PASSENGERS}{created['id']}/",
        headers=customer_headers_for(customer),
        json={"document_number": "BB7654321"},
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["document_number"] == "BB7654321"
    # PATCH is partial — the untouched fields survive.
    assert changed.json()["data"]["first_name"] == "Aziz"

    removed = await api.delete(
        f"{PASSENGERS}{created['id']}/", headers=customer_headers_for(customer)
    )
    assert removed.status_code == 204


async def test_the_list_is_paginated_and_excludes_deleted_rows(
    api: AsyncClient, customer: Customer
) -> None:
    first = await _create(api, customer)
    await _create(api, customer, first_name="Dilnoza", last_name="Rashidova")

    listed = await api.get(PASSENGERS, headers=customer_headers_for(customer))
    assert listed.status_code == 200
    assert listed.json()["meta"]["total"] == 2

    await api.delete(
        f"{PASSENGERS}{first['id']}/", headers=customer_headers_for(customer)
    )

    after = await api.get(PASSENGERS, headers=customer_headers_for(customer))
    assert after.json()["meta"]["total"] == 1
    assert [row["id"] for row in after.json()["data"]] != [first["id"]]


async def test_search_matches_either_name(api: AsyncClient, customer: Customer) -> None:
    await _create(api, customer)
    await _create(api, customer, first_name="Dilnoza", last_name="Rashidova")

    by_surname = await api.get(
        PASSENGERS, headers=customer_headers_for(customer), params={"search": "Rashid"}
    )

    assert by_surname.json()["meta"]["total"] == 1
    assert by_surname.json()["data"][0]["first_name"] == "Dilnoza"


async def test_a_deleted_passenger_is_gone_for_its_owner_too(
    api: AsyncClient, customer: Customer
) -> None:
    created = await _create(api, customer)
    await api.delete(
        f"{PASSENGERS}{created['id']}/", headers=customer_headers_for(customer)
    )

    fetched = await api.get(
        f"{PASSENGERS}{created['id']}/", headers=customer_headers_for(customer)
    )

    assert fetched.status_code == 404


async def test_another_customers_passenger_is_a_404(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    """404 and not 403 on purpose — "no such row" and "not yours" have to look
    the same from outside."""
    other = await make_customer(session, email="other@example.uz")
    theirs = await _create(api, other)

    headers = customer_headers_for(customer)
    assert (
        await api.get(f"{PASSENGERS}{theirs['id']}/", headers=headers)
    ).status_code == 404
    assert (
        await api.patch(
            f"{PASSENGERS}{theirs['id']}/", headers=headers, json={"first_name": "Mine"}
        )
    ).status_code == 404
    assert (
        await api.delete(f"{PASSENGERS}{theirs['id']}/", headers=headers)
    ).status_code == 404


async def test_the_list_only_shows_your_own(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    other = await make_customer(session, email="other@example.uz")
    await _create(api, other)
    await _create(api, customer, first_name="Mine")

    listed = await api.get(PASSENGERS, headers=customer_headers_for(customer))

    assert listed.json()["meta"]["total"] == 1
    assert listed.json()["data"][0]["first_name"] == "Mine"


async def test_the_same_person_may_be_saved_twice(
    api: AsyncClient, customer: Customer
) -> None:
    """An expired passport and its replacement are two legitimate rows, so
    nothing here is unique."""
    await _create(api, customer)
    await _create(api, customer, document_number="BB7654321")

    listed = await api.get(PASSENGERS, headers=customer_headers_for(customer))

    assert listed.json()["meta"]["total"] == 2


async def test_a_passenger_needs_a_name(api: AsyncClient, customer: Customer) -> None:
    response = await api.post(
        PASSENGERS,
        headers=customer_headers_for(customer),
        json={"first_name": "", "last_name": "Karimov"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "first_name"


async def test_a_passenger_needs_a_birth_date(
    api: AsyncClient, customer: Customer
) -> None:
    """A saved passenger exists to save retyping. Without a birth date it has
    to be retyped before it can be booked anyway, so the gap is refused at save
    time rather than discovered at booking time (API.md §19)."""
    payload = {name: value for name, value in AZIZ.items() if name != "birth_date"}

    response = await api.post(
        PASSENGERS, headers=customer_headers_for(customer), json=payload
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "birth_date"


async def test_a_required_field_cannot_be_cleared(
    api: AsyncClient, customer: Customer
) -> None:
    """The three required columns have no NULL in the table, so ``null`` has to
    come back as a 422 naming the field — not as the 500 a blind ``setattr``
    would turn it into."""
    created = await _create(api, customer)
    url = f"{PASSENGERS}{created['id']}/"

    for field in ("first_name", "last_name", "birth_date"):
        response = await api.patch(
            url, headers=customer_headers_for(customer), json={field: None}
        )
        assert response.status_code == 422, f"{field}: {response.text}"
        assert response.json()["errors"][0]["field"] == field


async def test_an_optional_document_field_can_be_cleared(
    api: AsyncClient, customer: Customer
) -> None:
    """The mirror of the test above, and the reason the guard names three
    fields rather than skipping every ``None``: clearing a document number is
    the only way to remove one."""
    created = await _create(api, customer)

    response = await api.patch(
        f"{PASSENGERS}{created['id']}/",
        headers=customer_headers_for(customer),
        json={"document_number": None},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["document_number"] is None


async def test_the_optional_person_and_document_fields_round_trip(
    api: AsyncClient, customer: Customer
) -> None:
    """A foreign passport has no patronymic and not every document carries an
    expiry, so both stay optional — but both come back when given."""
    given = await _create(
        api,
        customer,
        middle_name="Baxtiyorovich",
        citizenship="Uzbekistan",
        document_expiry_date="2030-01-01",
    )
    assert given["middle_name"] == "Baxtiyorovich"
    assert given["citizenship"] == "Uzbekistan"
    assert given["document_expiry_date"] == "2030-01-01"

    omitted = await _create(api, customer, document_number="BB7654321")
    assert omitted["middle_name"] is None
    assert omitted["citizenship"] is None
    assert omitted["document_expiry_date"] is None


async def test_citizenship_is_not_constrained(
    api: AsyncClient, customer: Customer
) -> None:
    """Free text like ``document_type``: the country list is GTS's, and a code
    column would also pick a standard the contract has not named."""
    spelled_out = await _create(api, customer, citizenship="Republic of Uzbekistan")
    assert spelled_out["citizenship"] == "Republic of Uzbekistan"

    as_a_code = await _create(api, customer, citizenship="UZB")
    assert as_a_code["citizenship"] == "UZB"


async def test_the_document_type_is_not_constrained(
    api: AsyncClient, customer: Customer
) -> None:
    """Free text until API.md §26 gains the catalogue GTS owns — a local enum
    would be a guess a later catalogue contradicts."""
    created = await _create(api, customer, document_type="birth_certificate")

    assert created["document_type"] == "birth_certificate"


async def test_passengers_need_a_token(api: AsyncClient) -> None:
    assert (await api.get(PASSENGERS)).status_code == 401
