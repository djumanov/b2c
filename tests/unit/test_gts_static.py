"""The GTS static adapter, with the network replaced.

Two things are being pinned down. The first is that the GTS envelope stops at
this file: callers get a list, never ``{status, message, code, data, errors}``.
The second is every way the call can go wrong — a failure that arrives as HTTP
200 with a negative code, an HTML error page, a connection that never opens —
and that each of them leaves as a typed ``AppError`` rather than as whatever
``httpx`` or ``json`` happened to raise.

Also pinned: the URL carries **no trailing slash** and **no credential**. Both
look like oversights next to the rest of the codebase and both are deliberate
(see the module docstring).
"""

from typing import Any
from urllib.parse import quote

import httpx
import pytest
import respx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.providers.gts import static

BASE_URL = "https://gts.test"

DOCUMENT_TYPES = [
    {
        "rule": "",
        "iso_code": "",
        "type": "PSP",
        "country": [],
        "title": "Заграничный паспорт",
        "translations": {"uz": "Xorijga chiqish pasporti", "en": "Passport"},
    }
]


def _envelope(data: Any) -> dict[str, Any]:
    """What GTS actually sends back (GTS.md §3)."""
    return {
        "status": "success",
        "message": "Список тип документов",
        "code": 2319220785510,
        "data": data,
        "errors": [],
    }


# --- the happy path ------------------------------------------------------------------


@respx.mock
async def test_document_types_returns_the_bare_data_list() -> None:
    route = respx.get(f"{BASE_URL}/static/typedocument").mock(
        return_value=httpx.Response(200, json=_envelope(DOCUMENT_TYPES))
    )

    result = await static.document_types(BASE_URL, country="UZ")

    assert result == DOCUMENT_TYPES
    assert route.calls.last.request.url.params["country"] == "UZ"


@respx.mock
async def test_omitting_the_country_sends_no_filter() -> None:
    route = respx.get(f"{BASE_URL}/static/typedocument").mock(
        return_value=httpx.Response(200, json=_envelope(DOCUMENT_TYPES))
    )

    await static.document_types(BASE_URL)

    assert route.calls.last.request.url.params == httpx.QueryParams()


@respx.mock
async def test_countries_returns_the_bare_data_list() -> None:
    respx.get(f"{BASE_URL}/static/country").mock(
        return_value=httpx.Response(200, json=_envelope([{"code": "UZ"}]))
    )

    assert await static.countries(BASE_URL) == [{"code": "UZ"}]


@respx.mock
async def test_airports_returns_the_bare_data_list() -> None:
    airports = [{"code": "TAS", "name": "Tashkent"}]
    route = respx.get(f"{BASE_URL}/static/airports/TAS").mock(
        return_value=httpx.Response(200, json=_envelope(airports))
    )

    assert await static.airports(BASE_URL, search="TAS") == airports
    assert route.calls.last.request.url.path == "/static/airports/TAS"


@respx.mock
async def test_no_search_term_fetches_the_complete_airport_list() -> None:
    """GTS serves the full catalogue at ``/static/airports`` — no slash."""
    airports = [{"code": "TAS"}, {"code": "IST"}]
    route = respx.get(f"{BASE_URL}/static/airports").mock(
        return_value=httpx.Response(200, json=_envelope(airports))
    )

    assert await static.airports(BASE_URL) == airports
    assert route.calls.last.request.url.path == "/static/airports"


@respx.mock
async def test_the_airport_search_term_is_percent_encoded() -> None:
    """User input lands in a path segment; URL structure must not leak in."""
    route = respx.get(url__regex=rf"{BASE_URL}/static/airports/.*").mock(
        return_value=httpx.Response(200, json=_envelope([]))
    )

    await static.airports(BASE_URL, search="foo/bar?x")

    assert route.calls.last.request.url.raw_path.endswith(
        b"/static/airports/foo%2Fbar%3Fx"
    )


@respx.mock
async def test_a_non_latin_search_term_survives_the_trip() -> None:
    route = respx.get(url__regex=rf"{BASE_URL}/static/airports/.*").mock(
        return_value=httpx.Response(200, json=_envelope([]))
    )

    await static.airports(BASE_URL, search="Ташкент")

    encoded = quote("Ташкент", safe="")
    assert route.calls.last.request.url.raw_path.endswith(
        f"/static/airports/{encoded}".encode()
    )


# --- the two details that read like mistakes -----------------------------------------


@respx.mock
async def test_the_path_carries_no_trailing_slash() -> None:
    """Every path *we* serve ends in one; GTS's static service 404s on it."""
    route = respx.get(f"{BASE_URL}/static/country").mock(
        return_value=httpx.Response(200, json=_envelope([]))
    )

    await static.countries(BASE_URL)

    assert route.calls.last.request.url.path == "/static/country"


@respx.mock
async def test_no_credential_is_sent() -> None:
    """Sending one would couple this file to a session manager it does not need."""
    route = respx.get(f"{BASE_URL}/static/country").mock(
        return_value=httpx.Response(200, json=_envelope([]))
    )

    await static.countries(BASE_URL)

    headers = route.calls.last.request.headers
    assert "authorization" not in headers
    assert "agent-uid" not in headers
    assert "cookie" not in headers


@respx.mock
async def test_a_base_url_stored_with_a_slash_does_not_double_it() -> None:
    route = respx.get(f"{BASE_URL}/static/country").mock(
        return_value=httpx.Response(200, json=_envelope([]))
    )

    await static.countries(f"{BASE_URL}/")

    assert str(route.calls.last.request.url) == f"{BASE_URL}/static/country"


# --- every way it can go wrong -------------------------------------------------------


@respx.mock
async def test_a_failure_reported_as_http_200_becomes_an_upstream_error() -> None:
    """GTS answers 200 with a negative code. Ours must not (API.md §3)."""
    respx.get(f"{BASE_URL}/static/country").mock(
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

    with pytest.raises(UpstreamError) as caught:
        await static.countries(BASE_URL)

    # The original text and code survive, which is the whole point of §3's
    # last paragraph — without them a 502 is undiagnosable.
    assert caught.value.message == "Ошибка сервиса"
    assert caught.value.meta == {
        "upstream": {"code": -104, "message": "Ошибка сервиса"}
    }


@respx.mock
async def test_an_html_error_page_does_not_surface_as_a_decode_error() -> None:
    respx.get(f"{BASE_URL}/static/country").mock(
        return_value=httpx.Response(404, html="<h1>Not Found</h1>")
    )

    with pytest.raises(UpstreamError) as caught:
        await static.countries(BASE_URL)

    assert caught.value.meta == {"upstream": {"code": 404}}


@respx.mock
async def test_a_200_that_is_not_json_becomes_an_upstream_error() -> None:
    respx.get(f"{BASE_URL}/static/country").mock(
        return_value=httpx.Response(200, text="<h1>maintenance</h1>")
    )

    with pytest.raises(UpstreamError):
        await static.countries(BASE_URL)


@respx.mock
async def test_data_that_is_not_a_list_becomes_an_upstream_error() -> None:
    """The return type says ``list``; nothing else may reach a caller."""
    respx.get(f"{BASE_URL}/static/country").mock(
        return_value=httpx.Response(200, json=_envelope({"code": "UZ"}))
    )

    with pytest.raises(UpstreamError):
        await static.countries(BASE_URL)


@respx.mock
async def test_a_timeout_is_504_not_502() -> None:
    respx.get(f"{BASE_URL}/static/country").mock(side_effect=httpx.ConnectTimeout)

    with pytest.raises(UpstreamTimeout):
        await static.countries(BASE_URL)


@respx.mock
async def test_a_refused_connection_is_an_upstream_error() -> None:
    respx.get(f"{BASE_URL}/static/country").mock(side_effect=httpx.ConnectError)

    with pytest.raises(UpstreamError):
        await static.countries(BASE_URL)
