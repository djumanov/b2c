"""Static pages: each of the fixed three has its own routes (API.md §24, §30)."""

from httpx import AsyncClient

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from tests.integration.conftest import customer_headers_for, headers_for

ADMIN_URL = "/api/v1/admin/content/"
PUBLIC_URL = "/api/v1/public/content/"

TITLE = {"uz": "Maxfiylik siyosati", "ru": "Политика конфиденциальности"}
BODY = {"uz": "# Maxfiylik\n\nMa'lumotlaringiz...", "ru": "# Конфиденциальность"}


async def _put(
    api: AsyncClient,
    headers: dict[str, str],
    *,
    page: str = "privacy-policy",
    title: dict[str, str] = TITLE,
    body: dict[str, str] = BODY,
) -> dict:
    response = await api.put(
        f"{ADMIN_URL}{page}/", json={"title": title, "body": body}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_upsert_and_publish_lifecycle(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)

    # Never written: the panel sees 404, and so does the public surface.
    assert (await api.get(f"{ADMIN_URL}terms/", headers=headers)).status_code == 404
    assert (await api.get(f"{PUBLIC_URL}terms/")).status_code == 404

    created = await _put(api, headers, page="terms")
    assert created["status"] == "draft"
    assert created["slug"] == "terms"

    # A draft is publicly indistinguishable from a page never written.
    assert (await api.get(f"{PUBLIC_URL}terms/")).status_code == 404

    published = await api.post(f"{ADMIN_URL}terms/publish/", headers=headers)
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"

    public = await api.get(f"{PUBLIC_URL}terms/")
    assert public.status_code == 200
    data = public.json()["data"]
    assert data["body"] == BODY["uz"]
    assert data["lang"] == "uz"

    # A later PUT merges one language into a translated field, keeping the
    # others — and does not touch the published status.
    merged = await api.put(
        f"{ADMIN_URL}terms/", json={"body": {"en": "# Terms"}}, headers=headers
    )
    assert merged.json()["data"]["body"] == {**BODY, "en": "# Terms"}
    assert merged.json()["data"]["status"] == "published"

    unpublished = await api.post(f"{ADMIN_URL}terms/unpublish/", headers=headers)
    assert unpublished.json()["data"]["status"] == "draft"
    assert (await api.get(f"{PUBLIC_URL}terms/")).status_code == 404


async def test_language_selection_and_fallback(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    await _put(api, headers)  # uz + ru, no en
    await api.post(f"{ADMIN_URL}privacy-policy/publish/", headers=headers)

    russian = await api.get(f"{PUBLIC_URL}privacy-policy/", params={"lang": "ru"})
    assert russian.json()["data"]["body"] == BODY["ru"]
    assert russian.json()["data"]["lang"] == "ru"

    # No English translation: the chain falls back and says where it landed.
    english = await api.get(f"{PUBLIC_URL}privacy-policy/", params={"lang": "en"})
    assert english.json()["data"]["body"] == BODY["uz"]
    assert english.json()["data"]["lang"] == "uz"


async def test_a_first_put_needs_some_content(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)

    # Nothing at all, and nothing readable in any language, both refuse.
    empty = await api.put(f"{ADMIN_URL}about/", json={}, headers=headers)
    assert empty.status_code == 422
    blank = await api.put(
        f"{ADMIN_URL}about/", json={"title": {"uz": "  "}}, headers=headers
    )
    assert blank.status_code == 422

    # Publishing a page never written is the ordinary 404, not a blank create.
    assert (
        await api.post(f"{ADMIN_URL}about/publish/", headers=headers)
    ).status_code == 404


async def test_admin_routes_refuse_a_customer_token(
    api: AsyncClient, customer: Customer
) -> None:
    headers = customer_headers_for(customer)
    # The wrong audience is a 403 — recognised, but not staff (API.md §4).
    assert (await api.get(f"{ADMIN_URL}terms/", headers=headers)).status_code == 403
    assert (
        await api.put(f"{ADMIN_URL}terms/", json={"title": TITLE}, headers=headers)
    ).status_code == 403


async def test_about_carries_company_contact_details(
    api: AsyncClient, admin: Staff
) -> None:
    """ "About" is the company contact details, not the page's own text.

    Not page content — asked from the settings module through its service
    (API.md §30, §17). The page's publish state still gates the 404, but its
    title/body never appear in the response.
    """
    headers = headers_for(admin)
    await _put(api, headers, page="about")
    await api.post(f"{ADMIN_URL}about/publish/", headers=headers)

    site = await api.patch(
        "/api/v1/admin/settings/site/",
        headers=headers,
        json={
            "name": {"uz": "GTS Sayohat"},
            "support_email": "info@gts.uz",
            "support_phone": "+998901234567",
            "domain": "gts.uz",
            "social": {
                "instagram": "https://instagram.com/gts",
                "telegram": "https://t.me/gts",
            },
        },
    )
    assert site.status_code == 200, site.text

    about = await api.get(f"{PUBLIC_URL}about/")
    assert about.status_code == 200
    data = about.json()["data"]
    assert data["company_name"] == "GTS Sayohat"
    assert data["company_email"] == "info@gts.uz"
    assert data["company_website"] == "gts.uz"
    assert data["company_phone"] == "+998901234567"
    assert {"name": "instagram", "link": "https://instagram.com/gts"} in data[
        "social_media"
    ]
    assert {"name": "telegram", "link": "https://t.me/gts"} in data["social_media"]
    assert data.keys() == {
        "company_name",
        "company_email",
        "company_website",
        "company_phone",
        "social_media",
    }


async def test_pages_ignore_feature_flags(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    """Pages are core — no switch may remove the privacy policy (API.md §28)."""
    headers = headers_for(admin)
    await _put(api, headers, page="about")
    await api.post(f"{ADMIN_URL}about/publish/", headers=headers)

    # Switch off every switchable section, rebuild the cached document…
    flags = (await api.get("/api/v1/admin/settings/features/", headers=headers)).json()[
        "data"
    ]["flags"]
    all_off = await api.patch(
        "/api/v1/admin/settings/features/",
        json={"flags": {flag: False for flag in flags if flag != "loyalty"}},
        headers=headers_for(owner),
    )
    assert all_off.status_code == 200
    assert (await api.get("/api/v1/public/site-config/")).status_code == 200

    # …and the pages are still there on both surfaces.
    assert (await api.get(f"{PUBLIC_URL}about/")).status_code == 200
    assert (await api.get(f"{ADMIN_URL}about/", headers=headers)).status_code == 200
