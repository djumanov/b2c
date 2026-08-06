"""``/admin/settings/*`` — API.md §28."""

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settings import service
from app.modules.settings.models import Branding
from app.modules.staff.models import Staff
from app.modules.uploads.models import Upload
from tests.integration.conftest import headers_for
from tests.integration.test_uploads import PNG

SETTINGS = "/api/v1/admin/settings/"
SITE_CONFIG = "/api/v1/public/site-config/"


async def _upload_logo(api: AsyncClient, staff: Staff, *, purpose: str = "logo") -> str:
    response = await api.post(
        "/api/v1/admin/uploads/",
        headers=headers_for(staff),
        files={"file": ("logo.png", PNG, "image/png")},
        data={"purpose": purpose},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


# --- they exist before anybody configures them ----------------------------------------


async def test_a_fresh_installation_has_defaults(
    api: AsyncClient, owner: Staff
) -> None:
    """No data migration seeds these — they appear on first read."""
    response = await api.get(f"{SETTINGS}branding/", headers=headers_for(owner))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["colors"]["primary"].startswith("#")
    assert data["font_family"] == "Inter"


async def test_reading_twice_does_not_make_a_second_row(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    await api.get(f"{SETTINGS}branding/", headers=headers_for(owner))
    await api.get(f"{SETTINGS}branding/", headers=headers_for(owner))

    total = await session.scalar(select(func.count()).select_from(Branding))
    assert total == 1


async def test_both_roles_may_change_settings(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    """API.md §5 gives `admin` ✎ on settings — a colour is day-to-day work."""
    for staff in (owner, admin):
        response = await api.patch(
            f"{SETTINGS}branding/",
            headers=headers_for(staff),
            json={"colors": {"primary": "#123456"}},
        )
        assert response.status_code == 200


# --- branding ---------------------------------------------------------------------


async def test_colours_are_merged_not_replaced(api: AsyncClient, owner: Staff) -> None:
    """The panel sends the one swatch somebody moved."""
    response = await api.patch(
        f"{SETTINGS}branding/",
        headers=headers_for(owner),
        json={"colors": {"primary": "#0A5CFF"}},
    )

    colors = response.json()["data"]["colors"]
    assert colors["primary"] == "#0A5CFF"
    assert "accent" in colors and "background" in colors


async def test_an_unknown_colour_is_refused(api: AsyncClient, owner: Staff) -> None:
    """Storing one the site never renders would be a silent no-op."""
    response = await api.patch(
        f"{SETTINGS}branding/",
        headers=headers_for(owner),
        json={"colors": {"sidebar": "#000000"}},
    )

    assert response.status_code == 422


async def test_a_value_that_is_not_a_colour_is_refused(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.patch(
        f"{SETTINGS}branding/",
        headers=headers_for(owner),
        json={"colors": {"primary": "blue"}},
    )

    assert response.status_code == 422


async def test_an_unknown_font_is_refused(api: AsyncClient, owner: Staff) -> None:
    """The site ships the faces, so a free-text name would not load."""
    response = await api.patch(
        f"{SETTINGS}branding/",
        headers=headers_for(owner),
        json={"font_family": "Comic Sans"},
    )

    assert response.status_code == 422


async def test_setting_a_logo_claims_the_file(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """Otherwise the sweep would take it 24 hours later (API.md §11)."""
    logo_id = await _upload_logo(api, owner)

    response = await api.patch(
        f"{SETTINGS}branding/", headers=headers_for(owner), json={"logo_id": logo_id}
    )

    assert response.status_code == 200
    assert response.json()["data"]["logo_url"].startswith("/uploads/logo/")
    upload = await session.get(Upload, logo_id)
    assert upload is not None and upload.is_linked


async def test_replacing_a_logo_releases_the_old_one(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    first = await _upload_logo(api, owner)
    second = await _upload_logo(api, owner)
    headers = headers_for(owner)

    await api.patch(f"{SETTINGS}branding/", headers=headers, json={"logo_id": first})
    await api.patch(f"{SETTINGS}branding/", headers=headers, json={"logo_id": second})

    old = await session.get(Upload, first)
    new = await session.get(Upload, second)
    assert old is not None and not old.is_linked
    assert new is not None and new.is_linked


async def test_a_favicon_cannot_be_used_as_a_logo(
    api: AsyncClient, owner: Staff
) -> None:
    favicon_id = await _upload_logo(api, owner, purpose="favicon")

    response = await api.patch(
        f"{SETTINGS}branding/", headers=headers_for(owner), json={"logo_id": favicon_id}
    )

    assert response.status_code == 422


# --- site, languages, currencies, features --------------------------------------------


async def test_the_site_name_is_translated(api: AsyncClient, owner: Staff) -> None:
    """Admin reads and writes objects; the public surface gets one language."""
    response = await api.patch(
        f"{SETTINGS}site/",
        headers=headers_for(owner),
        json={"name": {"uz": "Brand Travel", "ru": "Бренд Тревел"}},
    )

    assert response.json()["data"]["name"] == {
        "uz": "Brand Travel",
        "ru": "Бренд Тревел",
    }


async def test_the_default_language_must_be_available(
    api: AsyncClient, owner: Staff
) -> None:
    """Otherwise the fallback chain starts where nothing is written."""
    response = await api.patch(
        f"{SETTINGS}languages/",
        headers=headers_for(owner),
        json={"default": "en", "available": ["uz", "ru"]},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "default"


async def test_an_unsupported_language_is_refused(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.patch(
        f"{SETTINGS}languages/",
        headers=headers_for(owner),
        json={"available": ["uz", "de"]},
    )

    assert response.status_code == 422


async def test_currencies_are_iso_codes(api: AsyncClient, owner: Staff) -> None:
    ok = await api.patch(
        f"{SETTINGS}currencies/",
        headers=headers_for(owner),
        json={"default": "usd", "available": ["uzs", "usd"]},
    )
    assert ok.status_code == 200
    assert ok.json()["data"] == {"default": "USD", "available": ["UZS", "USD"]}

    bad = await api.patch(
        f"{SETTINGS}currencies/", headers=headers_for(owner), json={"default": "dollar"}
    )
    assert bad.status_code == 422


async def test_features_can_be_switched_off(api: AsyncClient, owner: Staff) -> None:
    response = await api.patch(
        f"{SETTINGS}features/",
        headers=headers_for(owner),
        json={"flags": {"blog": False}},
    )

    flags = response.json()["data"]["flags"]
    assert flags["blog"] is False
    # The others keep their defaults.
    assert flags["faq"] is True


async def test_switching_a_feature_off_reaches_the_gate_immediately(
    api: AsyncClient, owner: Staff
) -> None:
    """What ``RequireFeature`` will read, through the same cached document.

    The sequence is the real one: a write purges the cache, the next
    ``site-config`` request rebuilds it, and the gate reads that — which is why
    the gate can be a Redis GET rather than a query.

    The rebuild is driven through the API on purpose. ``feature_enabled``
    opens its own session when the cache is cold, and that session cannot see
    this test's uncommitted transaction — so a direct call would rebuild the
    document from a database that has not heard about the PATCH yet.
    """
    await api.patch(
        f"{SETTINGS}features/",
        headers=headers_for(owner),
        json={"flags": {"blog": False}},
    )
    assert (await api.get(SITE_CONFIG)).status_code == 200

    assert await service.feature_enabled("blog") is False
    assert await service.feature_enabled("faq") is True


async def test_an_unknown_feature_is_refused(api: AsyncClient, owner: Staff) -> None:
    response = await api.patch(
        f"{SETTINGS}features/",
        headers=headers_for(owner),
        json={"flags": {"teleportation": True}},
    )

    assert response.status_code == 422


async def test_loyalty_cannot_be_switched_on(api: AsyncClient, owner: Staff) -> None:
    """Out of scope for this product (PROJECT.md §3, API.md §41)."""
    response = await api.patch(
        f"{SETTINGS}features/",
        headers=headers_for(owner),
        json={"flags": {"loyalty": True}},
    )

    assert response.status_code == 422


# --- products and the menu ------------------------------------------------------------


async def test_the_five_verticals_are_listed_read_only(
    api: AsyncClient, owner: Staff
) -> None:
    """What may be sold follows from the GTS contract, not from the panel."""
    response = await api.get(f"{SETTINGS}products/", headers=headers_for(owner))

    codes = [row["code"] for row in response.json()["data"]]
    assert codes == ["flight", "railway", "insurance", "esim", "transfer"]
    assert all(row["enabled"] for row in response.json()["data"])


async def test_products_cannot_be_changed(api: AsyncClient, owner: Staff) -> None:
    for method in ("POST", "PATCH", "DELETE"):
        response = await api.request(
            method, f"{SETTINGS}products/", headers=headers_for(owner), json={}
        )
        assert response.status_code == 404, method


async def test_the_menu_builder_is_not_in_this_release(
    api: AsyncClient, owner: Staff
) -> None:
    """API.md §41: the model is still an open question (PROJECT.md §16)."""
    response = await api.get(f"{SETTINGS}menu/", headers=headers_for(owner))

    assert response.status_code == 404


# --- the journal --------------------------------------------------------------------


async def test_a_settings_change_is_audited_with_its_diff(
    api: AsyncClient, owner: Staff
) -> None:
    await api.patch(
        f"{SETTINGS}branding/",
        headers=headers_for(owner),
        json={"colors": {"primary": "#111111"}},
    )

    entries = await api.get(
        "/api/v1/admin/system/audit/?resource=settings", headers=headers_for(owner)
    )
    entry = entries.json()["data"][0]
    assert entry["resource"] == "settings.branding"
    assert entry["action"] == "update"
    assert entry["changes"]["colors"]["to"]["primary"] == "#111111"


async def test_the_cache_purge_endpoint_names_itself_in_the_journal(
    api: AsyncClient, owner: Staff
) -> None:
    """Its verb comes before any identifier, which the middleware reads wrongly
    — so the route carries ``Depends(Audited(...))``."""
    response = await api.post(f"{SETTINGS}cache/purge/", headers=headers_for(owner))
    assert response.status_code == 204

    entries = await api.get(
        "/api/v1/admin/system/audit/?resource=settings.cache",
        headers=headers_for(owner),
    )
    assert entries.json()["data"][0]["action"] == "purge"
