"""Static pages: markdown body per language, conventional slugs (API.md §24, §30)."""

from httpx import AsyncClient

from app.modules.staff.models import Staff
from tests.integration.conftest import headers_for

ADMIN_URL = "/api/v1/admin/content/pages/"
PUBLIC_URL = "/api/v1/public/content/pages/"

TITLE = {"uz": "Maxfiylik siyosati", "ru": "Политика конфиденциальности"}
BODY = {"uz": "# Maxfiylik\n\nMa'lumotlaringiz...", "ru": "# Конфиденциальность"}


async def _create(
    api: AsyncClient,
    headers: dict[str, str],
    *,
    slug: str = "privacy-policy",
    title: dict[str, str] = TITLE,
    body: dict[str, str] = BODY,
) -> dict:
    response = await api.post(
        ADMIN_URL,
        json={"slug": slug, "title": title, "body": body},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_crud_and_publish_lifecycle(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers)
    assert created["status"] == "draft"

    # A draft slug is indistinguishable from a missing one.
    assert (await api.get(f"{PUBLIC_URL}privacy-policy/")).status_code == 404
    assert (await api.get(f"{PUBLIC_URL}no-such-page/")).status_code == 404

    url = f"{ADMIN_URL}{created['id']}/"
    published = await api.post(f"{url}publish/", headers=headers)
    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"

    public = await api.get(f"{PUBLIC_URL}privacy-policy/")
    assert public.status_code == 200
    data = public.json()["data"]
    assert data["body"] == BODY["uz"]
    assert data["lang"] == "uz"

    # PATCH merges one language into a translated field, keeping the others.
    patched = await api.patch(url, json={"body": {"en": "# Privacy"}}, headers=headers)
    assert patched.json()["data"]["body"] == {**BODY, "en": "# Privacy"}

    unpublished = await api.post(f"{url}unpublish/", headers=headers)
    assert unpublished.json()["data"]["status"] == "draft"
    assert (await api.get(f"{PUBLIC_URL}privacy-policy/")).status_code == 404


async def test_language_selection_and_fallback(api: AsyncClient, admin: Staff) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers)  # uz + ru, no en
    await api.post(f"{ADMIN_URL}{created['id']}/publish/", headers=headers)

    russian = await api.get(f"{PUBLIC_URL}privacy-policy/", params={"lang": "ru"})
    assert russian.json()["data"]["body"] == BODY["ru"]
    assert russian.json()["data"]["lang"] == "ru"

    # No English translation: the chain falls back and says where it landed.
    english = await api.get(f"{PUBLIC_URL}privacy-policy/", params={"lang": "en"})
    assert english.json()["data"]["body"] == BODY["uz"]
    assert english.json()["data"]["lang"] == "uz"


async def test_a_live_slug_cannot_be_taken_twice(
    api: AsyncClient, admin: Staff
) -> None:
    headers = headers_for(admin)
    created = await _create(api, headers, slug="terms")

    duplicate = await api.post(
        ADMIN_URL,
        json={"slug": "terms", "title": TITLE, "body": BODY},
        headers=headers,
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["errors"][0]["field"] == "slug"

    # Deleting frees the slug — the unique index only covers live rows.
    await api.delete(f"{ADMIN_URL}{created['id']}/", headers=headers)
    await _create(api, headers, slug="terms")


async def test_an_ugly_slug_is_refused(api: AsyncClient, admin: Staff) -> None:
    response = await api.post(
        ADMIN_URL,
        json={"slug": "Not A Slug!", "title": TITLE, "body": BODY},
        headers=headers_for(admin),
    )
    assert response.status_code == 422


async def test_pages_ignore_feature_flags(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    """Pages are core — no switch may remove the privacy policy (API.md §28)."""
    headers = headers_for(admin)
    created = await _create(api, headers, slug="about")
    await api.post(f"{ADMIN_URL}{created['id']}/publish/", headers=headers)

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
    assert (await api.get(ADMIN_URL, headers=headers)).status_code == 200
