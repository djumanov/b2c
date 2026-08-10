"""Leads: a message in, panel triage, the answer off-system (API.md §25, §35)."""

from httpx import AsyncClient

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from tests.integration.conftest import customer_headers_for, headers_for

PUBLIC_URL = "/api/v1/public/leads/"
ADMIN_URL = "/api/v1/admin/leads/"

MESSAGE = {
    "topic": "payment",
    "name": "Muzaffar",
    "contact": "+998901234567",
    "message": "To'lov o'tdi, lekin buyurtma ko'rinmayapti",
}


async def test_anonymous_submission(api: AsyncClient, admin: Staff) -> None:
    response = await api.post(PUBLIC_URL, json=MESSAGE)

    assert response.status_code == 201, response.text
    created = response.json()["data"]
    assert created["status"] == "new"

    detail = await api.get(f"{ADMIN_URL}{created['id']}/", headers=headers_for(admin))
    data = detail.json()["data"]
    assert data["message"] == MESSAGE["message"]
    assert data["customer_id"] is None


async def test_a_signed_in_sender_is_linked(
    api: AsyncClient, customer: Customer, admin: Staff
) -> None:
    response = await api.post(
        PUBLIC_URL, json=MESSAGE, headers=customer_headers_for(customer)
    )
    created = response.json()["data"]

    detail = await api.get(f"{ADMIN_URL}{created['id']}/", headers=headers_for(admin))
    assert detail.json()["data"]["customer_id"] == str(customer.id)


async def test_a_bad_token_is_refused_not_ignored(api: AsyncClient) -> None:
    """Optional auth means the header may be absent — not that it may be wrong."""
    response = await api.post(
        PUBLIC_URL, json=MESSAGE, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


async def test_the_required_fields_are_required(api: AsyncClient) -> None:
    response = await api.post(PUBLIC_URL, json={"topic": "payment", "name": "Muzaffar"})
    assert response.status_code == 422


async def test_admin_list_filters_and_search(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    first = (await api.post(PUBLIC_URL, json=MESSAGE)).json()["data"]
    await api.post(
        PUBLIC_URL,
        json={**MESSAGE, "topic": "visa", "contact": "someone@example.uz"},
    )

    await api.patch(
        f"{ADMIN_URL}{first['id']}/", json={"status": "done"}, headers=headers
    )

    done = await api.get(ADMIN_URL, params={"status": "done"}, headers=headers)
    assert [item["id"] for item in done.json()["data"]] == [first["id"]]

    found = await api.get(ADMIN_URL, params={"search": "visa"}, headers=headers)
    assert [item["topic"] for item in found.json()["data"]] == ["visa"]


async def test_triage_is_audited(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    created = (await api.post(PUBLIC_URL, json=MESSAGE)).json()["data"]

    updated = await api.patch(
        f"{ADMIN_URL}{created['id']}/",
        json={"status": "in_progress", "note": "Called, no answer yet"},
        headers=headers,
    )
    assert updated.status_code == 200
    data = updated.json()["data"]
    assert data["status"] == "in_progress"
    assert data["note"] == "Called, no answer yet"

    entries = await api.get(
        "/api/v1/admin/system/audit/", params={"resource": "leads"}, headers=headers
    )
    entry = entries.json()["data"][0]
    assert entry["resource"] == "leads"
    assert entry["action"] == "update"


async def test_switched_off_leads_is_404_on_both_surfaces(
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

    assert (await api.post(PUBLIC_URL, json=MESSAGE)).status_code == 404
    assert (await api.get(ADMIN_URL, headers=headers_for(admin))).status_code == 404
