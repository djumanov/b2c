"""Support topics: the panel's dictionary behind the lead form (API.md §25, §35)."""

from httpx import AsyncClient

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from tests.integration.conftest import customer_headers_for, headers_for

ADMIN_URL = "/api/v1/admin/leads/topics/"
PUBLIC_URL = "/api/v1/public/leads/topics/"

NAME = {"uz": "To'lov", "ru": "Оплата"}


async def _create(
    api: AsyncClient,
    headers: dict[str, str],
    *,
    name: dict[str, str] = NAME,
    sort_order: int | None = None,
) -> dict:
    body: dict = {"name": name}
    if sort_order is not None:
        body["sort_order"] = sort_order
    response = await api.post(ADMIN_URL, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_crud_lifecycle(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)

    created = await _create(api, headers)
    assert created["name"] == NAME

    url = f"{ADMIN_URL}{created['id']}/"
    got = await api.get(url, headers=headers)
    assert got.status_code == 200
    assert got.json()["data"]["name"]["uz"] == NAME["uz"]

    # PATCH merges one language into a translated field, keeping the others.
    patched = await api.patch(url, json={"name": {"en": "Payment"}}, headers=headers)
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == {**NAME, "en": "Payment"}

    deleted = await api.delete(url, headers=headers)
    assert deleted.status_code == 204
    assert (await api.get(url, headers=headers)).status_code == 404


async def test_a_name_needs_at_least_one_language(
    api: AsyncClient, admin: Staff
) -> None:
    response = await api.post(
        ADMIN_URL,
        json={"name": {"uz": "  ", "de": "Zahlung"}},
        headers=headers_for(admin),
    )
    assert response.status_code == 422


async def test_new_topics_join_at_the_end(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    first = await _create(api, headers, name={"uz": "To'lov"})
    second = await _create(api, headers, name={"uz": "Chipta qaytarish"})
    assert second["sort_order"] == first["sort_order"] + 1

    # The public list follows the panel's order; an explicit PATCH reorders.
    await api.patch(
        f"{ADMIN_URL}{second['id']}/", json={"sort_order": -1}, headers=headers
    )
    listed = await api.get(PUBLIC_URL)
    assert [item["id"] for item in listed.json()["data"]] == [
        second["id"],
        first["id"],
    ]


async def test_public_language_selection_and_fallback(
    api: AsyncClient, admin: Staff
) -> None:
    await _create(api, headers_for(admin))  # uz + ru, no en

    russian = await api.get(PUBLIC_URL, params={"lang": "ru"})
    item = russian.json()["data"][0]
    assert item["name"] == NAME["ru"]
    assert item["lang"] == "ru"

    # No English translation: the chain falls back and says where it landed.
    english = await api.get(PUBLIC_URL, params={"lang": "en"})
    item = english.json()["data"][0]
    assert item["name"] == NAME["uz"]
    assert item["lang"] == "uz"


async def test_a_deleted_topic_leaves_the_public_list(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers)
    assert [item["id"] for item in (await api.get(PUBLIC_URL)).json()["data"]] == [
        created["id"]
    ]

    await api.delete(f"{ADMIN_URL}{created['id']}/", headers=headers)
    assert (await api.get(PUBLIC_URL)).json()["data"] == []


async def test_switched_off_leads_hides_topics_on_both_surfaces(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    flags_off = await api.patch(
        "/api/v1/admin/settings/features/",
        json={"flags": {"leads": False}},
        headers=headers_for(owner),
    )
    assert flags_off.status_code == 200
    # Rebuild the cached document through the API — the gate's own rebuild
    # cannot see this test's uncommitted transaction.
    assert (await api.get("/api/v1/public/site-config/")).status_code == 200

    assert (await api.get(PUBLIC_URL)).status_code == 404
    assert (await api.get(ADMIN_URL, headers=headers_for(admin))).status_code == 404


async def test_admin_surface_rejects_the_wrong_callers(
    api: AsyncClient, customer: Customer
) -> None:
    assert (await api.get(ADMIN_URL)).status_code == 401

    wrong_surface = await api.get(ADMIN_URL, headers=customer_headers_for(customer))
    assert wrong_surface.status_code == 403
