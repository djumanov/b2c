"""Support contact info: how to reach support directly, next to the topic
form (API.md §25, §35)."""

from httpx import AsyncClient

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from tests.integration.conftest import customer_headers_for, headers_for

ADMIN_URL = "/api/v1/admin/leads/support/"
PUBLIC_URL = "/api/v1/public/leads/support/"

WORKING_HOURS = {"uz": "Dush-Juma 09:00-18:00", "ru": "Пн-Пт 09:00-18:00"}


async def test_empty_by_default(api: AsyncClient, admin: Staff) -> None:
    got = await api.get(ADMIN_URL, headers=headers_for(admin))
    assert got.status_code == 200
    data = got.json()["data"]
    assert data["support_username"] is None
    assert data["support_phone"] is None
    assert data["support_email"] is None
    assert data["working_hours"] == {}

    public = await api.get(PUBLIC_URL)
    assert public.status_code == 200
    assert public.json()["data"] == {
        "support_username": None,
        "support_phone": None,
        "support_email": None,
        "working_hours": None,
        "working_hours_lang": None,
    }


async def test_patch_sets_fields_and_public_reads_them_back(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)
    patched = await api.patch(
        ADMIN_URL,
        json={
            "support_username": "@brand_support",
            "support_phone": "+998901234567",
            "support_email": "support@brand.uz",
            "working_hours": WORKING_HOURS,
        },
        headers=headers,
    )
    assert patched.status_code == 200
    data = patched.json()["data"]
    assert data["support_username"] == "@brand_support"
    assert data["support_phone"] == "+998901234567"
    assert data["support_email"] == "support@brand.uz"
    assert data["working_hours"] == WORKING_HOURS

    russian = await api.get(PUBLIC_URL, params={"lang": "ru"})
    body = russian.json()["data"]
    assert body["support_username"] == "@brand_support"
    assert body["working_hours"] == WORKING_HOURS["ru"]
    assert body["working_hours_lang"] == "ru"


async def test_patch_merges_working_hours_per_language(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)
    await api.patch(
        ADMIN_URL, json={"working_hours": {"uz": WORKING_HOURS["uz"]}}, headers=headers
    )
    patched = await api.patch(
        ADMIN_URL,
        json={"working_hours": {"en": "Mon-Fri 09:00-18:00"}},
        headers=headers,
    )
    assert patched.json()["data"]["working_hours"] == {
        "uz": WORKING_HOURS["uz"],
        "en": "Mon-Fri 09:00-18:00",
    }


async def test_an_empty_string_clears_a_field(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    await api.patch(ADMIN_URL, json={"support_phone": "+998901234567"}, headers=headers)
    cleared = await api.patch(ADMIN_URL, json={"support_phone": ""}, headers=headers)
    assert cleared.json()["data"]["support_phone"] is None


async def test_switched_off_leads_hides_support_on_both_surfaces(
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
