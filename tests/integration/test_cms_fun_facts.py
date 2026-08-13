"""Fun facts: panel CRUD behind the flight search response (API.md §20, §30).

No public list — the only public surface is the ``fun_fact`` field inside
``POST /public/flight/search/``, which ``test_flight_search.py`` covers.
"""

from httpx import AsyncClient

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from tests.integration.conftest import customer_headers_for, headers_for

ADMIN_URL = "/api/v1/admin/content/fun-facts/"

TEXT = {
    "uz": "Boeing 747 qanotida 6 million detal bor.",
    "ru": "В крыле Boeing 747 шесть миллионов деталей.",
}


async def _create(
    api: AsyncClient,
    headers: dict[str, str],
    *,
    text: dict[str, str] = TEXT,
) -> dict:
    response = await api.post(ADMIN_URL, json={"text": text}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_crud_lifecycle(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)

    created = await _create(api, headers)
    assert created["status"] == "draft"

    url = f"{ADMIN_URL}{created['id']}/"
    got = await api.get(url, headers=headers)
    assert got.status_code == 200
    assert got.json()["data"]["text"]["uz"] == TEXT["uz"]

    # PATCH merges one language into the translated field, keeping the others.
    patched = await api.patch(
        url, json={"text": {"en": "A 747 wing has six million parts."}}, headers=headers
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["text"] == {
        **TEXT,
        "en": "A 747 wing has six million parts.",
    }

    deleted = await api.delete(url, headers=headers)
    assert deleted.status_code == 204
    assert (await api.get(url, headers=headers)).status_code == 404


async def test_publish_and_unpublish_are_idempotent(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers)

    for _ in range(2):
        published = await api.post(
            f"{ADMIN_URL}{created['id']}/publish/", headers=headers
        )
        assert published.status_code == 200
        assert published.json()["data"]["status"] == "published"

    unpublished = await api.post(
        f"{ADMIN_URL}{created['id']}/unpublish/", headers=headers
    )
    assert unpublished.status_code == 200
    assert unpublished.json()["data"]["status"] == "draft"


async def test_status_filters_the_admin_list(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    draft = await _create(api, headers)
    published = await _create(api, headers)
    await api.post(f"{ADMIN_URL}{published['id']}/publish/", headers=headers)

    drafts = await api.get(ADMIN_URL, params={"status": "draft"}, headers=headers)
    assert [item["id"] for item in drafts.json()["data"]] == [draft["id"]]

    live = await api.get(ADMIN_URL, params={"status": "published"}, headers=headers)
    assert [item["id"] for item in live.json()["data"]] == [published["id"]]


async def test_text_must_have_a_value_in_a_supported_language(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)

    empty = await api.post(ADMIN_URL, json={"text": {}}, headers=headers)
    assert empty.status_code == 422

    blank = await api.post(ADMIN_URL, json={"text": {"uz": "  "}}, headers=headers)
    assert blank.status_code == 422

    # An unsupported language is dropped, not stored and not an error —
    # as long as a supported one carries a value.
    mixed = await _create(api, headers, text={"uz": TEXT["uz"], "de": "Fakt."})
    assert mixed["text"] == {"uz": TEXT["uz"]}


async def test_admin_surface_rejects_the_wrong_callers(
    api: AsyncClient, customer: Customer
) -> None:
    assert (await api.get(ADMIN_URL)).status_code == 401

    wrong_surface = await api.get(ADMIN_URL, headers=customer_headers_for(customer))
    assert wrong_surface.status_code == 403
