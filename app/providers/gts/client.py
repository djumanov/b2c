"""The authenticated GTS client and its session manager.

Everything ``/static/*`` is not: these calls act **as the installation's own
GTS agent account** (``/v1/…``), so a session has to exist before the first
request and has to survive every worker asking for it at once.

How the session works (ARCHITECTURE.md §7, GTS.md §3; verified against live
GTS on 2026-08-12):

* ``POST /v1/auth/signin/`` returns ``session_key``, ``token`` and
  ``timeout_minutes`` (typically 360). GTS then expects **both** values back
  as cookies — ``esession={session_key}; token={token}`` — on every ``/v1/``
  call; either one alone is a 401, and there is no Authorization header
  anywhere in its protocol.
* The ready cookie-header string lives in Redis under
  ``gts:session:{credential_id}:{updated_at}``. Deriving the key from the
  credential row means switching the active credential or changing its
  password needs **no invalidation**: the new Redis key is simply empty and
  the old one dies on its TTL.
* **Re-login is taken under a lock** (``SET NX``): every worker notices the
  empty key at the same moment, and without the lock GTS would see a burst of
  logins from one machine account. Losers poll for the winner's key.
* A ``401``/``403`` mid-flight means the session died early (e.g. someone
  signed into the GTS panel with the same account while ``is_single`` is on).
  The client drops the cached key and retries **once** — the request was
  rejected before GTS did any work, so the retry is safe even for a POST.

**No retry beyond that.** API.md §12 allows an idempotent ``GET`` two retries
with backoff; that lands with the first GET consumer (order sync). ``POST``
is never retried on an error that arrives *after* GTS accepted the call.

The GTS envelope stops here, exactly as in ``static.py``: callers get the
``data`` object and every upstream disappointment becomes a typed ``AppError``.
``download`` is the one call with no envelope to stop — a rendered document
comes back as bytes — and it recognises a refusal by the JSON it is written in.
"""

import asyncio
import time
from typing import Any, Final

import httpx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.core.logging import REQUEST_ID_HEADER, get_logger, request_id_var
from app.db.redis import get_redis
from app.modules.integrations.service import ActiveGtsCredential
from app.providers.gts.base import GtsClient, GtsDocument, GtsTimeouts

logger = get_logger(__name__)

#: Re-login this many seconds before GTS would have expired the session, so a
#: request never sets out with a key that dies in flight.
_TTL_MARGIN_SECONDS: Final = 300

#: When the sign-in answer carries no usable ``timeout_minutes``.
_FALLBACK_TTL_SECONDS: Final = 30 * 60

#: The lock outlives a sign-in (15 s timeout) but not a stuck worker.
_LOCK_TTL_SECONDS: Final = 20

#: How long a loser waits for the winner before giving up.
_WAIT_DEADLINE_SECONDS: Final = 30.0
_POLL_INTERVAL_SECONDS: Final = 0.2

#: How GTS says a document does not exist: Python's ``None`` rendered into
#: the response body, under ``content-type: application/json`` (live, 2026-08-25).
#: ``null`` is the same answer spelled properly, should it ever arrive.
_NO_DOCUMENT: Final = frozenset({b"None", b"null", b'"None"'})

#: What a document's first bytes say it is. GTS labels everything
#: ``application/json``, so its ``Content-Type`` is no help; these two cover
#: what a receipt can be, and anything else is bytes to save.
_MAGIC: Final[tuple[tuple[bytes, str], ...]] = (
    (b"%PDF-", "application/pdf"),
    (b"<", "text/html"),
)

#: The statuses that mean "your session is gone", not "your request is bad".
#: Django answers 401 or 403 depending on middleware, so both are treated as
#: session death; the log line keeps the real one for tuning against live GTS.
_SESSION_EXPIRED_STATUSES: Final = frozenset({401, 403})


class GtsSessionManager:
    """Implements ``GtsSession`` (base.py): keeps one agent account signed in."""

    __slots__ = ("_credential", "_key", "_lock_key")

    def __init__(self, credential: ActiveGtsCredential) -> None:
        self._credential = credential
        stamp = credential.updated_at.isoformat()
        self._key = f"gts:session:{credential.id}:{stamp}"
        self._lock_key = f"gts:session:lock:{credential.id}:{stamp}"

    async def token(self) -> str:
        """A live session, as the ready ``Cookie`` header value.

        The stored string is ``esession=…; token=…`` — assembled once at
        sign-in so no caller ever parses it. Losers poll rather than wait on
        the lock: if the winner dies (the lock TTL expires) or its sign-in
        fails, the next caller through the loop takes the lock and signs in
        itself, bounded by the deadline.
        """
        redis = get_redis()
        deadline = time.monotonic() + _WAIT_DEADLINE_SECONDS
        while True:
            cached = await redis.get(self._key)
            if cached:
                return str(cached)
            if await redis.set(self._lock_key, "1", ex=_LOCK_TTL_SECONDS, nx=True):
                try:
                    # Double-check under the lock: the previous holder may
                    # have written the key between our GET and our SET NX.
                    cached = await redis.get(self._key)
                    if cached:
                        return str(cached)
                    cookie, ttl = await self._signin()
                    await redis.set(self._key, cookie, ex=ttl)
                    return cookie
                finally:
                    await redis.delete(self._lock_key)
            if time.monotonic() >= deadline:
                logger.warning(
                    "gts_session_wait_timeout", credential=str(self._credential.id)
                )
                raise UpstreamTimeout("could not obtain a GTS session in time")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def invalidate(self) -> None:
        """Drop the cached session — called once after a 401, then retried."""
        await get_redis().delete(self._key)

    async def _signin(self) -> tuple[str, int]:
        """One ``POST /v1/auth/signin/`` → ``(cookie_header_value, ttl_seconds)``.

        GTS's answer carries the session in **two** fields, ``session_key``
        and ``token``, and its ``/v1/`` endpoints demand both back as cookies
        (verified live: either alone is a 401). ``follow_redirects`` stays
        **off**, unlike ``static.py``: this request carries the account
        password and must not follow it anywhere.
        """
        credential = self._credential
        url = f"{credential.base_url.rstrip('/')}/v1/auth/signin/"
        logger.info("gts_signin", credential=str(credential.id))
        try:
            async with httpx.AsyncClient(
                timeout=GtsTimeouts.DEFAULT_SECONDS, follow_redirects=False
            ) as http:
                response = await http.post(
                    url,
                    json={"email": credential.email, "password": credential.password},
                )
        except httpx.TimeoutException as exc:
            logger.warning("gts_signin_timeout", credential=str(credential.id))
            raise UpstreamTimeout("the GTS sign-in did not answer in time") from exc
        except httpx.HTTPError as exc:
            logger.warning("gts_signin_unreachable", error=str(exc))
            raise UpstreamError(f"GTS is unreachable: {exc}") from exc

        data = _translate("auth/signin", response)
        session_key = data.get("session_key")
        token = data.get("token")
        if (
            not isinstance(session_key, str)
            or not session_key
            or not isinstance(token, str)
            or not token
        ):
            raise UpstreamError("the GTS sign-in returned an unexpected shape")
        cookie = f"esession={session_key}; token={token}"
        return cookie, _session_ttl(data.get("timeout_minutes"))


class GtsHttpClient:
    """Implements ``GtsClient`` (base.py): every authenticated call goes here.

    Paths are passed **with** the ``/v1/`` prefix and the trailing slash
    (``/v1/content/search/``) — the opposite of the static adapter, whose
    upstream 404s on a slash.
    """

    __slots__ = ("_base_url", "_session")

    def __init__(self, credential: ActiveGtsCredential) -> None:
        self._base_url = credential.base_url.rstrip("/")
        self._session = GtsSessionManager(credential)

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET", path, params=params, json=None, timeout=timeout
        )

    async def get_envelope(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None,
    ) -> dict[str, Any]:
        """``get``, with the whole envelope — see ``post_envelope``."""
        return await self._request(
            "GET", path, params=params, json=None, timeout=timeout, envelope=True
        )

    async def post(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        return await self._request(
            "POST", path, params=None, json=json, timeout=timeout
        )

    async def post_envelope(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        """Like ``post``, but the whole GTS envelope comes back.

        For the calls whose ``status`` is a **state**, not a verdict: a
        running search answers ``offers/`` with ``status: "In process"`` and
        partial results already in ``data`` (observed live, 2026-08-12).
        Only ``status: "error"`` is a failure here.
        """
        return await self._request(
            "POST", path, params=None, json=json, timeout=timeout, envelope=True
        )

    async def download(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None,
    ) -> GtsDocument | None:
        """The bytes of a document GTS renders — see ``GtsClient.download``.

        **The answer is read, not its label.** GTS serves its documents from
        the same DRF stack as its data and marks every one of them
        ``application/json``, including the ones that are not (live, orders
        4903 and 4905, 2026-08-25: ``content-type: application/json`` over a
        four-byte body reading ``None``). Believing the header turned a
        rendered receipt into a parse failure, so the first bytes decide what
        this is, and the type served on is sniffed from them.

        Three things can come back. A **document** — anything that is not
        JSON — is returned as one. GTS's word for *"there is nothing here"*
        is Python's ``None`` rendered into the body, and that is ``None``
        here too: not a failure, and the caller's to explain. A **JSON
        envelope** is data where a file was asked for: a refusal raises with
        GTS's own words through ``_translate``, and an envelope that reports
        success but carries no file is "nothing here" as well.
        """
        response = await self._send(
            "GET", path, params=params, json=None, timeout=timeout
        )
        if response.status_code != httpx.codes.OK:
            _translate(path, response)  # raises: a document never 4xx/5xx
        body = response.content.strip()
        if body in _NO_DOCUMENT:
            logger.info("gts_document_absent", path=path)
            return None
        if not body:
            logger.warning("gts_document_empty", path=path)
            raise UpstreamError("GTS returned an empty document")
        if body[:1] in b"{[":
            _translate(path, response)  # raises on GTS's own refusal
            logger.warning("gts_document_is_data", path=path)
            return None
        return GtsDocument(content=response.content, content_type=_media_type(body))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        timeout: float | None,
        envelope: bool = False,
    ) -> dict[str, Any]:
        response = await self._send(
            method, path, params=params, json=json, timeout=timeout
        )
        return _translate(path, response, envelope=envelope)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        timeout: float | None,
    ) -> httpx.Response:
        """The call itself — the session, the retry, the network.

        Separate from what the answer *means*, because that differs: most
        calls carry an envelope, one carries a file.
        """
        for attempt in (1, 2):
            # The manager hands back the ready ``esession=…; token=…`` pair.
            headers = {"Cookie": await self._session.token()}
            request_id = request_id_var.get()
            if request_id:
                # ARCHITECTURE.md §7: our request id travels outward, so a GTS
                # log line and ours can be matched during an incident.
                headers[REQUEST_ID_HEADER] = request_id
            try:
                async with httpx.AsyncClient(
                    timeout=timeout or GtsTimeouts.DEFAULT_SECONDS,
                    follow_redirects=False,
                ) as http:
                    response = await http.request(
                        method,
                        f"{self._base_url}{path}",
                        params=params,
                        json=json,
                        headers=headers,
                    )
            except httpx.TimeoutException as exc:
                logger.warning("gts_timeout", path=path)
                raise UpstreamTimeout("GTS did not answer in time") from exc
            except httpx.HTTPError as exc:
                logger.warning("gts_unreachable", path=path, error=str(exc))
                raise UpstreamError(f"GTS is unreachable: {exc}") from exc

            if response.status_code in _SESSION_EXPIRED_STATUSES and attempt == 1:
                # The session died under us; the request never reached the
                # handler behind it, so one retry with a fresh session is safe
                # even for a POST.
                logger.info(
                    "gts_session_expired", status=response.status_code, path=path
                )
                await self._session.invalidate()
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover


def _media_type(body: bytes) -> str:
    """What the bytes are, since the header will not say."""
    for magic, media_type in _MAGIC:
        if body.startswith(magic):
            return media_type
    return "application/octet-stream"


def client_for(credential: ActiveGtsCredential) -> GtsClient:
    """The one place a ready client is built from a decrypted credential."""
    return GtsHttpClient(credential)


def _session_ttl(timeout_minutes: object) -> int:
    """Redis TTL from GTS's ``timeout_minutes``, expiring early on purpose.

    ``expired_time`` is ignored: it is a naive timestamp in GTS's own clock,
    and parsing it would import clock-skew bugs the margin already covers.
    """
    # Live GTS answers with a float (``360.0``); the collection showed an int.
    if not isinstance(timeout_minutes, int | float | str):
        return _FALLBACK_TTL_SECONDS
    try:
        minutes = int(float(timeout_minutes))
    except (ValueError, OverflowError):
        return _FALLBACK_TTL_SECONDS
    if minutes <= 0:
        return _FALLBACK_TTL_SECONDS
    return max(60, minutes * 60 - _TTL_MARGIN_SECONDS)


def _translate(
    path: str, response: httpx.Response, *, envelope: bool = False
) -> dict[str, Any]:
    """The anti-corruption layer — ``static._get``'s twin for ``/v1/``.

    Every way GTS can disappoint becomes a typed ``AppError``, including the
    failure that arrives as HTTP 200 with ``status: "error"``. With
    ``envelope=True`` the whole payload is returned and only ``"error"`` is a
    failure — any other status (``"success"``, ``"In process"``, whatever GTS
    invents next) is a state the caller wants to see; without it the caller
    gets the bare ``data`` and anything but ``"success"`` is refused.

    The shape check follows the same split. Bare mode promises a ``data``
    dictionary and refuses anything else, because that is all its callers get.
    Envelope mode promises nothing beyond "GTS did not report an error": the
    answer may sit under ``data`` or under ``order`` depending on the endpoint,
    and only the adapter knows which.
    """
    if response.status_code != httpx.codes.OK:
        logger.warning("gts_http_error", path=path, status=response.status_code)
        raise UpstreamError(
            "GTS returned an unexpected status", upstream_code=response.status_code
        )

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("gts_unparsable", path=path)
        raise UpstreamError("GTS returned a body we cannot read") from exc

    status = payload.get("status") if isinstance(payload, dict) else None
    rejected = status == "error" if envelope else status != "success"
    if not isinstance(payload, dict) or rejected:
        upstream_message = None
        upstream_code = None
        if isinstance(payload, dict):
            upstream_message = _failure_text(payload)
            upstream_code = payload.get("code")
        logger.warning("gts_rejected", path=path, upstream_code=upstream_code)
        raise UpstreamError(
            upstream_message or "GTS reported a failure",
            upstream_code=upstream_code,
            upstream_message=upstream_message,
        )

    if envelope:
        # The whole payload, and **no shape demanded of it**. GTS is not
        # consistent with itself about where the answer goes: ``booking`` nests
        # it under ``data``, ``ticketing`` and ``cancel`` under ``order``, and
        # requiring ``data`` here turned every successful cancellation into a
        # 502 (STATUS.md §8.15a). Which key holds the answer is the vertical's
        # knowledge, so the adapter is the one entitled to look.
        return payload

    data = payload.get("data")
    if not isinstance(data, dict):
        raise UpstreamError("GTS returned an unexpected shape")
    result: dict[str, Any] = data
    return result


#: A sentence, not a rendered error page: a wrong path puts a whole HTML
#: document in ``errors``, and none of it belongs in a client's error message.
_MAX_ERROR_CHARS: Final = 300


def _failure_text(payload: dict[str, Any]) -> str | None:
    """GTS's own words for a failure — as close to the real reason as it gets.

    ``message`` is usually the generic ``"Что то не так :("`` while the
    sentence a customer could act on sits in ``errors``: "the passenger already
    has a booking on this flight", "the surname is invalid" (both observed
    live, 2026-08-20). So ``errors`` is preferred whenever it holds sentences,
    and ``message`` answers for the envelopes that carry no ``errors`` at all.
    """
    return _errors_text(payload.get("errors")) or _message_text(payload.get("message"))


def _errors_text(raw: object) -> str | None:
    """``errors`` flattened — a list of sentences, or nothing.

    Anything else is ignored on purpose: GTS puts an HTML page there for a
    path it does not route, and ``message`` is the better answer then.
    """
    if not isinstance(raw, list):
        return None
    parts = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return "; ".join(parts)[:_MAX_ERROR_CHARS] or None


def _message_text(raw: object) -> str | None:
    """GTS's ``message`` is a string in the generic envelope but a **list of
    objects** in the content service's answers — flatten either to one line."""
    if isinstance(raw, str):
        return raw or None
    if isinstance(raw, list):
        parts = [
            str(item.get("message"))
            for item in raw
            if isinstance(item, dict) and item.get("message")
        ]
        return "; ".join(parts) or None
    return None


__all__ = ["GtsHttpClient", "GtsSessionManager", "client_for"]
