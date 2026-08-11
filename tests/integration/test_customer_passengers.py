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

# The §26 catalogue shapes as GTS serves them (test_catalog.py pins the
# passthrough); stored verbatim, so the tests assert on the whole object.
GTS_DOCUMENT_TYPE = {
    "rule": "",
    "iso_code": "",
    "type": "PSP",
    "country": [],
    "title": "Заграничный паспорт",
    "translations": {
        "uz": "Xorijga chiqish pasporti",
        "ru": "Заграничный паспорт",
        "en": "International passport",
        "az": "Ümumvətəndaş (xarici) pasportu",
    },
}

GTS_COUNTRY = {
    "country_rus": "Узбекистан",
    "country_eng": "Uzbekistan",
    "code": "UZ",
    "phone_code": 998,
    "phone_mask": "(##) ###-##-##",
    "emoji": "🇺🇿",
    "translations": {"ru": "Узбекистан", "en": "Uzbekistan", "uz": "Oʻzbekiston"},
}

AZIZ = {
    "first_name": "Aziz",
    "last_name": "Karimov",
    "birth_date": "1995-04-17",
    "document_type": GTS_DOCUMENT_TYPE,
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
        citizenship=GTS_COUNTRY,
        document_expiry_date="2030-01-01",
    )
    assert given["middle_name"] == "Baxtiyorovich"
    assert given["citizenship"] == GTS_COUNTRY
    assert given["document_expiry_date"] == "2030-01-01"

    omitted = await _create(api, customer, document_number="BB7654321")
    assert omitted["middle_name"] is None
    assert omitted["citizenship"] is None
    assert omitted["document_expiry_date"] is None


async def test_the_catalog_objects_are_stored_verbatim(
    api: AsyncClient, customer: Customer
) -> None:
    """The §26 object is ours to keep, not to reshape: extra keys, Cyrillic and
    the flag emoji all have to survive the round trip, because the UI renders
    from the stored copy (STATUS.md §4.75)."""
    embellished = {**GTS_COUNTRY, "an_unknown_future_key": {"nested": True}}
    created = await _create(api, customer, citizenship=embellished)

    assert created["citizenship"] == embellished
    assert created["document_type"] == GTS_DOCUMENT_TYPE

    fetched = await api.get(
        f"{PASSENGERS}{created['id']}/", headers=customer_headers_for(customer)
    )
    assert fetched.json()["data"]["citizenship"] == embellished
    assert fetched.json()["data"]["document_type"] == GTS_DOCUMENT_TYPE


async def test_a_catalog_object_needs_its_identifier_key(
    api: AsyncClient, customer: Customer
) -> None:
    """Everything but the identifier is GTS's shape and passes untouched — but
    ``"code"``/``"type"`` is what the booking flow will hand back to GTS, so an
    object without one is refused at save time, naming the field."""
    cases = [
        ("citizenship", {k: v for k, v in GTS_COUNTRY.items() if k != "code"}),
        ("citizenship", {**GTS_COUNTRY, "code": "  "}),
        ("document_type", {k: v for k, v in GTS_DOCUMENT_TYPE.items() if k != "type"}),
        ("document_type", {**GTS_DOCUMENT_TYPE, "type": ""}),
    ]
    for field, value in cases:
        response = await api.post(
            PASSENGERS,
            headers=customer_headers_for(customer),
            json={**AZIZ, field: value},
        )
        assert response.status_code == 422, f"{field}: {response.text}"
        assert response.json()["errors"][0]["field"] == field


async def test_a_bare_string_is_no_longer_accepted(
    api: AsyncClient, customer: Customer
) -> None:
    """The old contract took free text; the new one takes the catalogue object
    or nothing."""
    for field, value in (("citizenship", "Uzbekistan"), ("document_type", "passport")):
        response = await api.post(
            PASSENGERS,
            headers=customer_headers_for(customer),
            json={**AZIZ, field: value},
        )
        assert response.status_code == 422, f"{field}: {response.text}"
        assert response.json()["errors"][0]["field"] == field


async def test_a_catalog_object_can_be_cleared_and_replaced(
    api: AsyncClient, customer: Customer
) -> None:
    """Both fields stay optional: ``null`` clears them, and a ``PATCH`` with a
    different object replaces the whole stored copy, not a merge of the two."""
    created = await _create(api, customer, citizenship=GTS_COUNTRY)
    url = f"{PASSENGERS}{created['id']}/"
    headers = customer_headers_for(customer)

    cleared = await api.patch(
        url, headers=headers, json={"citizenship": None, "document_type": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["citizenship"] is None
    assert cleared.json()["data"]["document_type"] is None

    kazakhstan = {
        "country_rus": "Казахстан",
        "country_eng": "Kazakhstan",
        "code": "KZ",
        "phone_code": 7,
        "phone_mask": "(###) ###-##-##",
        "emoji": "🇰🇿",
        "translations": {"ru": "Казахстан", "en": "Kazakhstan", "uz": "Qozogʻiston"},
    }
    replaced = await api.patch(url, headers=headers, json={"citizenship": kazakhstan})
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["data"]["citizenship"] == kazakhstan

    fetched = await api.get(url, headers=headers)
    assert fetched.json()["data"]["citizenship"] == kazakhstan
    assert fetched.json()["data"]["document_type"] is None


async def test_passengers_need_a_token(api: AsyncClient) -> None:
    assert (await api.get(PASSENGERS)).status_code == 401
