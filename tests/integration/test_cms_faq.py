"""FAQ: panel CRUD and the public read that collapses languages (API.md §24, §30)."""

from httpx import AsyncClient

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from tests.integration.conftest import customer_headers_for, headers_for

ADMIN_URL = "/api/v1/admin/content/faq/"
PUBLIC_URL = "/api/v1/public/content/faq/"

QUESTION = {"uz": "To'lov qanday qaytadi?", "ru": "Как вернуть оплату?"}
ANSWER = {"uz": "Kartaga qaytadi.", "ru": "Возврат на карту."}


async def _create(
    api: AsyncClient,
    headers: dict[str, str],
    *,
    question: dict[str, str] = QUESTION,
    answer: dict[str, str] = ANSWER,
    category: str | None = "payment",
) -> dict:
    response = await api.post(
        ADMIN_URL,
        json={"question": question, "answer": answer, "category": category},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_crud_lifecycle(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)

    created = await _create(api, headers)
    assert created["status"] == "draft"

    url = f"{ADMIN_URL}{created['id']}/"
    got = await api.get(url, headers=headers)
    assert got.status_code == 200
    assert got.json()["data"]["question"]["uz"] == QUESTION["uz"]

    # PATCH merges one language into a translated field, keeping the others.
    patched = await api.patch(
        url, json={"question": {"en": "Refunds?"}}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["question"] == {**QUESTION, "en": "Refunds?"}

    deleted = await api.delete(url, headers=headers)
    assert deleted.status_code == 204
    assert (await api.get(url, headers=headers)).status_code == 404


async def test_draft_is_invisible_until_published(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers)

    empty = await api.get(PUBLIC_URL)
    assert empty.status_code == 200
    assert empty.json()["data"] == []

    published = await api.post(f"{ADMIN_URL}{created['id']}/publish/", headers=headers)
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"

    visible = await api.get(PUBLIC_URL)
    assert [item["id"] for item in visible.json()["data"]] == [created["id"]]

    await api.post(f"{ADMIN_URL}{created['id']}/unpublish/", headers=headers)
    assert (await api.get(PUBLIC_URL)).json()["data"] == []


async def test_category_filters_both_surfaces(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    payment = await _create(api, headers, category="payment")
    visa = await _create(api, headers, category="visa")
    for item in (payment, visa):
        await api.post(f"{ADMIN_URL}{item['id']}/publish/", headers=headers)

    admin_list = await api.get(ADMIN_URL, params={"category": "visa"}, headers=headers)
    assert [item["id"] for item in admin_list.json()["data"]] == [visa["id"]]

    public_list = await api.get(PUBLIC_URL, params={"category": "payment"})
    assert [item["id"] for item in public_list.json()["data"]] == [payment["id"]]


async def test_reorder_changes_the_public_order(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    first = await _create(api, headers)
    second = await _create(api, headers)
    for item in (first, second):
        await api.post(f"{ADMIN_URL}{item['id']}/publish/", headers=headers)

    # Created order first…
    listed = await api.get(PUBLIC_URL)
    assert [item["id"] for item in listed.json()["data"]] == [first["id"], second["id"]]

    reordered = await api.post(
        f"{ADMIN_URL}reorder/",
        json=[
            {"id": second["id"], "order": 0},
            {"id": first["id"], "order": 1},
        ],
        headers=headers,
    )
    assert reordered.status_code == 204

    listed = await api.get(PUBLIC_URL)
    assert [item["id"] for item in listed.json()["data"]] == [second["id"], first["id"]]


async def test_public_language_selection_and_fallback(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers)  # uz + ru, no en
    await api.post(f"{ADMIN_URL}{created['id']}/publish/", headers=headers)

    russian = await api.get(PUBLIC_URL, params={"lang": "ru"})
    item = russian.json()["data"][0]
    assert item["question"] == QUESTION["ru"]
    assert item["lang"] == "ru"

    # No English translation: the chain falls back and says where it landed.
    english = await api.get(PUBLIC_URL, params={"lang": "en"})
    item = english.json()["data"][0]
    assert item["question"] == QUESTION["uz"]
    assert item["lang"] == "uz"


async def test_switched_off_faq_is_404_on_both_surfaces(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    flags_off = await api.patch(
        "/api/v1/admin/settings/features/",
        json={"flags": {"faq": False}},
        headers=headers_for(owner),
    )
    assert flags_off.status_code == 200
    # Rebuild the cached document through the API — the gate's own rebuild
    # opens a session that cannot see this test's uncommitted transaction
    # (see tests/integration/test_settings.py for the long version).
    assert (await api.get("/api/v1/public/site-config/")).status_code == 200

    assert (await api.get(PUBLIC_URL)).status_code == 404
    assert (await api.get(ADMIN_URL, headers=headers_for(admin))).status_code == 404


async def test_admin_surface_rejects_the_wrong_callers(
    api: AsyncClient, customer: Customer
) -> None:
    assert (await api.get(ADMIN_URL)).status_code == 401

    wrong_surface = await api.get(ADMIN_URL, headers=customer_headers_for(customer))
    assert wrong_surface.status_code == 403
