"""Deletion reasons: panel CRUD and the customer-facing list (API.md §19, §34)."""

from httpx import AsyncClient

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from tests.integration.conftest import customer_headers_for, headers_for

ADMIN_URL = "/api/v1/admin/customers/deletion-reasons/"
PUBLIC_URL = "/api/v1/public/profile/deletion-reasons/"

TEXT = {"uz": "Narxlar qimmat", "ru": "Не устраивают цены"}


async def _create(
    api: AsyncClient,
    headers: dict[str, str],
    *,
    text: dict[str, str] = TEXT,
    sort_order: int | None = None,
) -> dict:
    body: dict = {"text": text}
    if sort_order is not None:
        body["sort_order"] = sort_order
    response = await api.post(ADMIN_URL, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_crud_lifecycle(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)

    created = await _create(api, headers)
    assert created["text"] == TEXT
    assert created["sort_order"] == 0

    url = f"{ADMIN_URL}{created['id']}/"
    got = await api.get(url, headers=headers)
    assert got.status_code == 200
    assert got.json()["data"]["text"]["uz"] == TEXT["uz"]

    # PATCH merges one language into the translated field, keeping the others,
    # and moves the row in the panel's order.
    patched = await api.patch(
        url, json={"text": {"en": "Too expensive"}, "sort_order": 5}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["text"] == {**TEXT, "en": "Too expensive"}
    assert patched.json()["data"]["sort_order"] == 5

    deleted = await api.delete(url, headers=headers)
    assert deleted.status_code == 204
    assert (await api.get(url, headers=headers)).status_code == 404


async def test_a_reason_needs_at_least_one_language(
    api: AsyncClient, admin: Staff
) -> None:
    response = await api.post(
        ADMIN_URL, json={"text": {"uz": "  ", "ru": ""}}, headers=headers_for(admin)
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "text"


async def test_new_reasons_join_at_the_end(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    first = await _create(api, headers)
    second = await _create(api, headers)
    assert (first["sort_order"], second["sort_order"]) == (0, 1)


async def test_public_list_follows_the_panel_order(
    api: AsyncClient, admin: Staff, customer: Customer
) -> None:
    headers = headers_for(admin)
    first = await _create(api, headers, sort_order=2)
    second = await _create(api, headers, sort_order=1)

    listed = await api.get(PUBLIC_URL, headers=customer_headers_for(customer))
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["data"]] == [
        second["id"],
        first["id"],
    ]


async def test_public_language_selection_and_fallback(
    api: AsyncClient, admin: Staff, customer: Customer
) -> None:
    await _create(api, headers_for(admin))  # uz + ru, no en
    headers = customer_headers_for(customer)

    russian = await api.get(PUBLIC_URL, params={"lang": "ru"}, headers=headers)
    item = russian.json()["data"][0]
    assert item["text"] == TEXT["ru"]
    assert item["lang"] == "ru"

    # No English translation: the chain falls back and says where it landed.
    english = await api.get(PUBLIC_URL, params={"lang": "en"}, headers=headers)
    item = english.json()["data"][0]
    assert item["text"] == TEXT["uz"]
    assert item["lang"] == "uz"


async def test_a_deleted_reason_leaves_the_public_list(
    api: AsyncClient, admin: Staff, customer: Customer
) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers)
    await api.delete(f"{ADMIN_URL}{created['id']}/", headers=headers)

    listed = await api.get(PUBLIC_URL, headers=customer_headers_for(customer))
    assert listed.json()["data"] == []


async def test_the_public_list_needs_a_token(api: AsyncClient) -> None:
    """The list sits on the profile router (API.md §19) — only a signed-in
    customer about to delete ever sees the screen."""
    assert (await api.get(PUBLIC_URL)).status_code == 401


async def test_admin_surface_rejects_the_wrong_callers(
    api: AsyncClient, customer: Customer
) -> None:
    assert (await api.get(ADMIN_URL)).status_code == 401

    wrong_surface = await api.get(ADMIN_URL, headers=customer_headers_for(customer))
    assert wrong_surface.status_code == 403
