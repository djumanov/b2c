"""GTS's static catalogues — the one corner of GTS that needs no account.

``/static/*`` sits outside the authenticated ``/v1/`` API described in GTS.md
§3: no sign-in, no session cookie, no ``agent-uid``. That is why this adapter
is a separate file from the session-bearing client rather than a method on it —
a citizenship dropdown has no business waiting for the session manager to
exist, and coupling the two would mean an installation that has not yet saved a
GTS credential cannot render a passenger form.

**No retry.** API.md §12 allows an idempotent ``GET`` two retries with backoff,
but that policy belongs with the shared client, next to the 401-relogin dance.
For ``document_types``/``countries`` a 24-hour cache sits in front
(``modules/catalog/cache.py``), so a transient failure reaches one request in
thousands rather than every one. ``airports`` is uncached — a free-text search
term makes an unbounded cache key — so its failures do reach the caller, and
that is fine for an autocomplete the user simply retypes into.

The GTS envelope stops at this file. Callers get the ``data`` list and nothing
else (ARCHITECTURE.md §7).
"""

from typing import Any, Final
from urllib.parse import quote

import httpx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.core.logging import get_logger
from app.providers.gts.base import GtsTimeouts

logger = get_logger(__name__)

#: Where the static service hangs off the installation's ``base_url``. The
#: catalogues are the same for everyone; only the host is configurable.
_STATIC_PREFIX: Final = "/static"

_TIMEOUT: Final = GtsTimeouts.DEFAULT_SECONDS


async def document_types(
    base_url: str, *, country: str | None = None
) -> list[dict[str, Any]]:
    """Passenger document types, optionally narrowed to one country.

    ``country`` is an ISO 3166-1 alpha-2 code. Passing one adds the documents
    that only that country issues; omitting it returns the universal set.
    """
    params = {"country": country} if country else None
    return await _get("typedocument", base_url=base_url, params=params)


async def countries(base_url: str) -> list[dict[str, Any]]:
    """The country list — used for citizenship and phone codes."""
    return await _get("country", base_url=base_url)


async def airports(base_url: str, *, search: str) -> list[dict[str, Any]]:
    """Airport autocomplete for the flight search form.

    ``search`` is user input landing in a **path segment** — the only place in
    this file that happens — so it is percent-encoded with nothing held safe:
    a stray ``/``, ``?`` or ``#`` must reach GTS as data, not as URL structure.
    """
    return await _get(f"airports/{quote(search, safe='')}", base_url=base_url)


async def _get(
    path: str, *, base_url: str, params: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """One static call, and the whole anti-corruption layer for this service.

    Every way GTS can disappoint us is turned into an ``AppError`` here, so no
    caller has to know that a failure arrives as HTTP 200 with a negative code.
    """
    url = f"{base_url.rstrip('/')}{_STATIC_PREFIX}/{path}"

    try:
        # ``follow_redirects`` is safe *here* and nowhere else in this package:
        # the request carries no credential, so a redirect has nothing to leak.
        # It earns its place because an installation whose stored ``base_url``
        # still says ``http://`` gets a 301 rather than an answer.
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            # No trailing slash. Our own paths all end in one (API.md §1) and
            # GTS's static service 404s on exactly that, so this line reads
            # like a mistake and is not one.
            response = await client.get(url, params=params)
    except httpx.TimeoutException as exc:
        logger.warning("gts_static_timeout", path=path)
        raise UpstreamTimeout("the GTS static service did not answer in time") from exc
    except httpx.HTTPError as exc:
        logger.warning("gts_static_unreachable", path=path, error=str(exc))
        raise UpstreamError(f"the GTS static service is unreachable: {exc}") from exc

    if response.status_code != httpx.codes.OK:
        # An unknown path answers with an HTML error page, so the body is not
        # worth quoting — the status is the whole message.
        logger.warning("gts_static_http_error", path=path, status=response.status_code)
        raise UpstreamError(
            "the GTS static service returned an unexpected status",
            upstream_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("gts_static_unparsable", path=path)
        raise UpstreamError(
            "the GTS static service returned a body we cannot read"
        ) from exc

    if not isinstance(payload, dict) or payload.get("status") != "success":
        message = "the GTS static service reported a failure"
        upstream_message = None
        upstream_code = None
        if isinstance(payload, dict):
            upstream_message = payload.get("message")
            upstream_code = payload.get("code")
        logger.warning("gts_static_rejected", path=path, upstream_code=upstream_code)
        raise UpstreamError(
            upstream_message or message,
            upstream_code=upstream_code,
            upstream_message=upstream_message,
        )

    data = payload.get("data")
    if not isinstance(data, list):
        raise UpstreamError("the GTS static service returned an unexpected shape")

    items: list[dict[str, Any]] = data
    return items


__all__ = ["airports", "countries", "document_types"]
