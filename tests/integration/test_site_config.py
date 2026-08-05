"""``GET /public/site-config/`` — API.md §17.

This file carries the second acceptance criterion of phase 1 (PROJECT.md §15):

    brend rangi o'zgartirilsa `site-config` da **deploysiz** aks etadi

which is also the sentence ARCHITECTURE.md §16 ends its verification list with,
and the product's headline promise (PROJECT.md §1).
"""

from httpx import AsyncClient

from app.modules.settings import cache
from app.modules.staff.models import Staff
from tests.integration.conftest import headers_for
from tests.integration.test_uploads import PNG

CONFIG = "/api/v1/public/site-config/"
BRANDING = "/api/v1/admin/settings/branding/"


# --- the promise ---------------------------------------------------------------------


async def test_a_colour_change_is_visible_without_a_restart(
    api: AsyncClient, owner: Staff
) -> None:
    """The acceptance criterion, end to end and in one test.

    Nothing is restarted, no cache is cleared by hand, and the process serving
    the second request is the one that served the first.
    """
    before = await api.get(CONFIG)
    assert before.json()["data"]["branding"]["colors"]["primary"] != "#00AA55"

    changed = await api.patch(
        BRANDING, headers=headers_for(owner), json={"colors": {"primary": "#00AA55"}}
    )
    assert changed.status_code == 200

    after = await api.get(CONFIG)
    assert after.json()["data"]["branding"]["colors"]["primary"] == "#00AA55"


async def test_a_new_logo_is_visible_without_a_restart(
    api: AsyncClient, owner: Staff
) -> None:
    uploaded = await api.post(
        "/api/v1/admin/uploads/",
        headers=headers_for(owner),
        files={"file": ("logo.png", PNG, "image/png")},
        data={"purpose": "logo"},
    )
    logo_id = uploaded.json()["data"]["id"]

    await api.get(CONFIG)  # warm the cache, so the purge is what is under test
    await api.patch(BRANDING, headers=headers_for(owner), json={"logo_id": logo_id})

    logo_url = (await api.get(CONFIG)).json()["data"]["branding"]["logo_url"]
    assert logo_url is not None
    assert (await api.get(logo_url)).status_code == 200


# --- who may read it ------------------------------------------------------------------


async def test_it_needs_no_authentication(api: AsyncClient) -> None:
    """It is what an anonymous visitor needs before there is a session."""
    assert (await api.get(CONFIG)).status_code == 200


async def test_it_has_the_shape_the_contract_specifies(api: AsyncClient) -> None:
    data = (await api.get(CONFIG)).json()["data"]

    assert set(data) >= {
        "site",
        "branding",
        "languages",
        "currencies",
        "products",
        "payment_methods",
        "menu",
        "features",
    }
    assert [row["code"] for row in data["products"]] == [
        "flight",
        "railway",
        "insurance",
        "esim",
        "transfer",
    ]
    # Both arrive with their modules: payment methods with `integrations`, the
    # menu in phase 5 (API.md §41).
    assert data["payment_methods"] == []
    assert data["menu"] == []


# --- caching (API.md §17) -------------------------------------------------------------


async def test_the_second_read_comes_from_the_cache(api: AsyncClient) -> None:
    assert await cache.read() is None

    await api.get(CONFIG)

    assert await cache.read() is not None


async def test_any_settings_write_purges_the_cache(
    api: AsyncClient, owner: Staff
) -> None:
    """ARCHITECTURE.md §5 names this as the mechanism behind the promise."""
    await api.get(CONFIG)
    assert await cache.read() is not None

    await api.patch(
        "/api/v1/admin/settings/site/",
        headers=headers_for(owner),
        json={"support_phone": "+998901234567"},
    )

    assert await cache.read() is None


async def test_the_purge_endpoint_empties_it(api: AsyncClient, owner: Staff) -> None:
    await api.get(CONFIG)

    await api.post("/api/v1/admin/settings/cache/purge/", headers=headers_for(owner))

    assert await cache.read() is None


async def test_an_unchanged_document_answers_304(api: AsyncClient) -> None:
    first = await api.get(CONFIG)
    etag = first.headers["etag"]
    assert first.headers["cache-control"].startswith("public")

    again = await api.get(CONFIG, headers={"If-None-Match": etag})

    assert again.status_code == 304
    assert not again.content


async def test_the_etag_changes_when_the_branding_does(
    api: AsyncClient, owner: Staff
) -> None:
    """A browser that kept the old document must not be told to reuse it."""
    etag = (await api.get(CONFIG)).headers["etag"]

    await api.patch(
        BRANDING, headers=headers_for(owner), json={"colors": {"accent": "#FF0000"}}
    )

    assert (await api.get(CONFIG)).headers["etag"] != etag


# --- one language out (API.md §7) -----------------------------------------------------


async def test_the_name_comes_back_in_the_requested_language(
    api: AsyncClient, owner: Staff
) -> None:
    await api.patch(
        "/api/v1/admin/settings/site/",
        headers=headers_for(owner),
        json={"name": {"uz": "Brand Travel", "ru": "Бренд Тревел"}},
    )

    russian = (await api.get(f"{CONFIG}?lang=ru")).json()["data"]
    assert russian["site"]["name"] == "Бренд Тревел"
    assert russian["lang"] == "ru"


async def test_a_missing_translation_falls_back_and_says_so(
    api: AsyncClient, owner: Staff
) -> None:
    """API.md §7: the response reports the language that actually came back."""
    await api.patch(
        "/api/v1/admin/settings/site/",
        headers=headers_for(owner),
        json={"name": {"uz": "Brand Travel"}},
    )

    english = (await api.get(f"{CONFIG}?lang=en")).json()["data"]

    assert english["site"]["name"] == "Brand Travel"
    assert english["lang"] == "uz"


async def test_the_accept_language_header_is_honoured(
    api: AsyncClient, owner: Staff
) -> None:
    await api.patch(
        "/api/v1/admin/settings/site/",
        headers=headers_for(owner),
        json={"name": {"uz": "Brand Travel", "ru": "Бренд Тревел"}},
    )

    response = await api.get(CONFIG, headers={"Accept-Language": "ru-RU,ru;q=0.9"})

    assert response.json()["data"]["site"]["name"] == "Бренд Тревел"


async def test_two_languages_share_one_cache_entry(
    api: AsyncClient, owner: Staff
) -> None:
    """Flattening is a dictionary walk; the six queries are what was cached."""
    await api.patch(
        "/api/v1/admin/settings/site/",
        headers=headers_for(owner),
        json={"name": {"uz": "Brand Travel", "ru": "Бренд Тревел"}},
    )

    uz = await api.get(f"{CONFIG}?lang=uz")
    ru = await api.get(f"{CONFIG}?lang=ru")

    assert uz.json()["data"]["site"]["name"] == "Brand Travel"
    assert ru.json()["data"]["site"]["name"] == "Бренд Тревел"
    # Different documents, so different validators.
    assert uz.headers["etag"] != ru.headers["etag"]
