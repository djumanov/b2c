"""The GTS port — the anti-corruption boundary (ARCHITECTURE.md §7).

GTS's contract differs from ours on purpose, and none of it may leak inward:

============================================  =====================================
GTS                                           us
============================================  =====================================
``{status, message, id, time, total, data}``  ``{status, data, errors, meta}``
HTTP 200 even on failure, negative codes      correct HTTP status + error catalogue
``BO/PW/TI/TE/CB/VO/RF/PRF``                  ``booked``/``ticketed``/… (see below)
cookie session that expires                   handled here, invisible above
============================================  =====================================

Two details that are easy to get wrong and expensive to get wrong:

* **Re-login is taken under a lock.** Every worker notices the expired session
  at the same moment; without a lock they all re-authenticate at once, and GTS
  sees a burst of logins from one machine account.
The canonical status vocabulary is **not** here: it is ours, not the
boundary's, and it belongs to whichever module owns the states. Nothing owns
them today — the order system was removed to be rebuilt — so the codes above
are relayed and nothing maps them.

* **Booking and payment are never retried** (API.md §12). A retried booking is
  a second booking. Only idempotent ``GET``s retry — twice, with backoff.
"""

from typing import Any, Protocol


class GtsTimeouts:
    """API.md §12. Search is slow because GTS fans out to providers."""

    SEARCH_SECONDS: float = 40.0
    DEFAULT_SECONDS: float = 15.0


class GtsSession(Protocol):
    """Keeps the installation's own GTS agent account signed in (D1).

    Credentials are decrypted from the database, the session lives in Redis,
    and re-login happens under a lock so only one worker does it.
    """

    async def token(self) -> str:
        """A live session, re-authenticating if it has expired."""
        ...

    async def invalidate(self) -> None:
        """Drop the cached session — called once after a 401, then retried."""
        ...


class GtsClient(Protocol):
    """Every outbound GTS call goes through here.

    Implementations translate the GTS envelope, map its error codes into the
    catalogue (default ``502 upstream_error``, with ``offer_expired`` broken
    out), keep the original text in ``message`` and
    the original code in ``meta.upstream``, and forward ``X-Request-Id``.
    """

    async def get(
        self, path: str, *, params: dict[str, Any] | None = None, timeout: float | None
    ) -> dict[str, Any]: ...

    async def post(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]: ...

    async def get_envelope(
        self, path: str, *, params: dict[str, Any] | None = None, timeout: float | None
    ) -> dict[str, Any]:
        """``get``, with the whole envelope. ``post_envelope``'s twin.

        For reads whose page is not under ``data``: the orders list wraps its
        rows in ``{count, next, previous, results}`` and where that wrapper
        sits is not something the boundary should have to guess.
        """
        ...

    async def post_envelope(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        """The whole GTS envelope, for calls whose ``status`` is a state.

        A running search answers ``offers/`` with ``status: "In process"``
        and partial results already in ``data``; only ``"error"`` fails.
        """
        ...


__all__ = ["GtsClient", "GtsSession", "GtsTimeouts"]
