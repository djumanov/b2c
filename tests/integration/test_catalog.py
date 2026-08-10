"""``GET /public/catalog/*`` — API.md §26.

The lists a passenger form is filled in from. Three promises are being held to
here, and each of them is the kind that quietly stops being true:

* **No authentication.** A visitor who has not signed in has to see the form.
* **The GTS envelope does not reach the client** — ours does, with GTS's items
  inside it and its ``{status, message, code}`` gone.
* **One outbound call per day, not per dropdown.** The cache is the only thing
  standing between a country list and 216 entries of upstream traffic per page
  load.

``respx`` intercepts the outbound ``httpx`` transport; the ``api`` fixture
speaks to the app through ``ASGITransport``, which respx leaves alone. So a
test can mock GTS and still make a real request.
"""

from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.modules.integrations.models import DEFAULT_BASE_URL, GtsCredential

DOCUMENT_TYPES = "/api/v1/public/catalog/document-types/"
COUNTRIES = "/api/v1/public/catalog/countries/"

#: Kept whole and Cyrillic on purpose: it is what GTS really sends, and the
#: contract says §26 hands it on unchanged (API.md §7).
GTS_DOCUMENT_TYPE = {
    "rule": "",
    "iso_code": "",
    "type": "PSP",
    "country": [],
    "title": "Заграничный паспорт",
    "translations": {
        "uz": "Xorijga chiqish pasporti",
        "ru": "Заграничный паспорт",
        "en": "International passport",
        "az": "Ümumvətəndaş (xarici) pasportu",
    },
}

GTS_COUNTRY = {
    "country_rus": "Узбекистан",
    "country_eng": "Uzbekistan",
    "code": "UZ",
    "phone_code": 998,
    "phone_mask": "(##) ###-##-##",
    "emoji": "🇺🇿",
    "translations": {"ru": "Узбекистан", "en": "Uzbekistan", "uz": "Oʻzbekiston"},
}


def _envelope(data: Any) -> dict[str, Any]:
    return {
        "status": "success",
        "message": "Список",
        "code": 2319220785510,
        "data": data,
        "errors": [],
    }


def _mock_document_types(base_url: str = DEFAULT_BASE_URL) -> respx.Route:
    return respx.get(f"{base_url}/static/typedocument").mock(
        return_value=httpx.Response(200, json=_envelope([GTS_DOCUMENT_TYPE]))
    )


def _mock_countries(base_url: str = DEFAULT_BASE_URL) -> respx.Route:
    return respx.get(f"{base_url}/static/country").mock(
        return_value=httpx.Response(200, json=_envelope([GTS_COUNTRY]))
    )


async def _activate_credential(session: AsyncSession, *, base_url: str) -> None:
    ciphertext, key_version = encrypt("gts-secret-1a2b")
    session.add(
        GtsCredential(
            label="Prod agent",
            base_url=base_url,
            email="agent@brand.uz",
            password=ciphertext,
            key_version=key_version,
            is_active=True,
        )
    )
    await session.commit()


# --- what the client gets ------------------------------------------------------------


@respx.mock
async def test_document_types_arrive_inside_our_envelope(api: AsyncClient) -> None:
    _mock_document_types()

    response = await api.get(DOCUMENT_TYPES, params={"country": "UZ"})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"status", "data", "errors", "meta"}
    assert body["status"] == "success"
    assert body["errors"] == []
    # GTS's own keys — ``status``, ``message``, ``code`` — are nowhere in it.
    assert body["data"] == [GTS_DOCUMENT_TYPE]


@respx.mock
async def test_countries_arrive_inside_our_envelope(api: AsyncClient) -> None:
    _mock_countries()

    response = await api.get(COUNTRIES)

    assert response.status_code == 200
    assert response.json()["data"] == [GTS_COUNTRY]


@respx.mock
async def test_translations_are_not_collapsed(api: AsyncClient) -> None:
    """§26 is the one exemption from API.md §7's one-language rule.

    These are GTS's reference tables, not our content. Picking a language for
    the client would mean maintaining a second vocabulary of our own.
    """
    _mock_document_types()

    body = await api.get(DOCUMENT_TYPES, params={"lang": "uz"})

    item = body.json()["data"][0]
    assert item["translations"] == GTS_DOCUMENT_TYPE["translations"]
    assert "lang" not in item


@respx.mock
async def test_no_token_is_needed(api: AsyncClient) -> None:
    """The form is rendered before there is anything to authenticate."""
    _mock_countries()

    response = await api.get(COUNTRIES)

    assert response.status_code == 200
    assert "authorization" not in {key.lower() for key in response.request.headers}


@respx.mock
async def test_the_answer_may_be_cached_for_a_day(api: AsyncClient) -> None:
    _mock_countries()

    response = await api.get(COUNTRIES)

    assert response.headers["Cache-Control"] == "public, max-age=86400"


# --- the cache -----------------------------------------------------------------------


@respx.mock
async def test_a_second_request_does_not_reach_gts(api: AsyncClient) -> None:
    route = _mock_countries()

    first = await api.get(COUNTRIES)
    second = await api.get(COUNTRIES)

    assert first.json()["data"] == second.json()["data"]
    assert route.call_count == 1


@respx.mock
async def test_each_country_filter_is_cached_separately(api: AsyncClient) -> None:
    """``?country=`` changes the answer, so it has to change the key."""
    route = _mock_document_types()

    await api.get(DOCUMENT_TYPES, params={"country": "UZ"})
    await api.get(DOCUMENT_TYPES)

    assert route.call_count == 2


# --- where the base URL comes from ---------------------------------------------------


@respx.mock
async def test_the_active_credential_decides_the_host(
    api: AsyncClient, session: AsyncSession
) -> None:
    """Which GTS an installation talks to is a setting, not a constant.

    Only the configured host is mocked, so a request to the default would fail
    the run rather than pass it quietly.
    """
    await _activate_credential(session, base_url="https://gts.test")
    route = _mock_countries("https://gts.test")

    response = await api.get(COUNTRIES)

    assert response.status_code == 200
    assert route.call_count == 1


@respx.mock
async def test_the_catalogue_works_before_any_credential_is_saved(
    api: AsyncClient,
) -> None:
    """The static service takes no account, so a fresh install still has lists."""
    route = _mock_countries(DEFAULT_BASE_URL)

    response = await api.get(COUNTRIES)

    assert response.status_code == 200
    assert route.call_count == 1


# --- when it goes wrong --------------------------------------------------------------


@respx.mock
async def test_an_invalid_country_is_rejected_before_gts_is_called(
    api: AsyncClient,
) -> None:
    route = _mock_document_types()

    response = await api.get(DOCUMENT_TYPES, params={"country": "UZB"})

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "validation"
    assert route.call_count == 0


@respx.mock
async def test_a_gts_failure_is_502_with_its_own_code_kept(api: AsyncClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/static/country").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "error",
                "message": "Ошибка сервиса",
                "code": -104,
                "data": [],
                "errors": [],
            },
        )
    )

    response = await api.get(COUNTRIES)

    assert response.status_code == 502
    body = response.json()
    assert body["errors"][0]["code"] == "upstream_error"
    assert body["errors"][0]["message"] == "Ошибка сервиса"
    assert body["meta"]["upstream"]["code"] == -104


@respx.mock
async def test_a_gts_timeout_is_504(api: AsyncClient) -> None:
    respx.get(f"{DEFAULT_BASE_URL}/static/country").mock(
        side_effect=httpx.ConnectTimeout
    )

    response = await api.get(COUNTRIES)

    assert response.status_code == 504
    assert response.json()["errors"][0]["code"] == "upstream_timeout"


@respx.mock
async def test_a_failure_is_not_cached(api: AsyncClient) -> None:
    """A bad minute must not become a bad day."""
    route = respx.get(f"{DEFAULT_BASE_URL}/static/country").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=_envelope([GTS_COUNTRY])),
        ]
    )

    failed = await api.get(COUNTRIES)
    recovered = await api.get(COUNTRIES)

    assert failed.status_code == 502
    assert recovered.status_code == 200
    assert route.call_count == 2


@pytest.mark.parametrize("path", [DOCUMENT_TYPES, COUNTRIES])
async def test_the_path_without_its_slash_is_404(api: AsyncClient, path: str) -> None:
    """API.md §1, and not a 307 — a redirect would drop the query string."""
    response = await api.get(path.rstrip("/"))

    assert response.status_code == 404
