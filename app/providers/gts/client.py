"""The authenticated GTS client and its session manager.

Everything ``/static/*`` is not: these calls act **as the installation's own
GTS agent account** (``/v1/…``), so a session has to exist before the first
request and has to survive every worker asking for it at once.

How the session works (ARCHITECTURE.md §7, GTS.md §3):

* ``POST /v1/auth/signin/`` returns ``session_key`` and ``timeout_minutes``
  (typically 360). GTS then expects the key back as its session cookie —
  there is no Authorization header anywhere in its protocol.
* The key lives in Redis under ``gts:session:{credential_id}:{updated_at}``.
  Deriving the key from the credential row means switching the active
  credential or changing its password needs **no invalidation**: the new
  Redis key is simply empty and the old one dies on its TTL.
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
"""

import asyncio
import time
from typing import Any, Final

import httpx

from app.api.errors import UpstreamError, UpstreamTimeout
from app.core.logging import REQUEST_ID_HEADER, get_logger, request_id_var
from app.db.redis import get_redis
from app.modules.integrations.service import ActiveGtsCredential
from app.providers.gts.base import GtsClient, GtsTimeouts

logger = get_logger(__name__)

#: GTS is a Django service and the collection relies on the cookie jar, so the
#: session rides under Django's default cookie name. **Assumption** — the one
#: knob to turn if live GTS answers 401 to a fresh session (STATUS.md).
_SESSION_COOKIE: Final = "sessionid"

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
        """A live session key, signing in under the lock if there is none.

        Losers poll rather than wait on the lock: if the winner dies (the lock
        TTL expires) or its sign-in fails, the next caller through the loop
        takes the lock and signs in itself, bounded by the deadline.
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
                    session_key, ttl = await self._signin()
                    await redis.set(self._key, session_key, ex=ttl)
                    return session_key
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
        """One ``POST /v1/auth/signin/``; returns ``(session_key, ttl_seconds)``.

        ``follow_redirects`` stays **off**, unlike ``static.py``: this request
        carries the account password and must not follow it anywhere.
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
        if not isinstance(session_key, str) or not session_key:
            raise UpstreamError("the GTS sign-in returned an unexpected shape")
        return session_key, _session_ttl(data.get("timeout_minutes"))


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

    async def post(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        return await self._request(
            "POST", path, params=None, json=json, timeout=timeout
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        for attempt in (1, 2):
            session_key = await self._session.token()
            headers = {"Cookie": f"{_SESSION_COOKIE}={session_key}"}
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
            return _translate(path, response)
        raise AssertionError("unreachable")  # pragma: no cover


def client_for(credential: ActiveGtsCredential) -> GtsClient:
    """The one place a ready client is built from a decrypted credential."""
    return GtsHttpClient(credential)


def _session_ttl(timeout_minutes: object) -> int:
    """Redis TTL from GTS's ``timeout_minutes``, expiring early on purpose.

    ``expired_time`` is ignored: it is a naive timestamp in GTS's own clock,
    and parsing it would import clock-skew bugs the margin already covers.
    """
    if not isinstance(timeout_minutes, int | str):
        return _FALLBACK_TTL_SECONDS
    try:
        minutes = int(timeout_minutes)
    except ValueError:
        return _FALLBACK_TTL_SECONDS
    if minutes <= 0:
        return _FALLBACK_TTL_SECONDS
    return max(60, minutes * 60 - _TTL_MARGIN_SECONDS)


def _translate(path: str, response: httpx.Response) -> dict[str, Any]:
    """The anti-corruption layer — ``static._get``'s twin for ``/v1/``.

    Every way GTS can disappoint becomes a typed ``AppError``, including the
    failure that arrives as HTTP 200 with ``status: "error"``.
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

    if not isinstance(payload, dict) or payload.get("status") != "success":
        upstream_message = None
        upstream_code = None
        if isinstance(payload, dict):
            upstream_message = _message_text(payload.get("message"))
            upstream_code = payload.get("code")
        logger.warning("gts_rejected", path=path, upstream_code=upstream_code)
        raise UpstreamError(
            upstream_message or "GTS reported a failure",
            upstream_code=upstream_code,
            upstream_message=upstream_message,
        )

    data = payload.get("data")
    if not isinstance(data, dict):
        raise UpstreamError("GTS returned an unexpected shape")

    result: dict[str, Any] = data
    return result


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
