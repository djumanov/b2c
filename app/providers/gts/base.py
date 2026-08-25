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
boundary's, and it belongs to the module that owns the states —
``modules/orders`` keeps GTS's code verbatim in ``gts_status`` beside its own
``status``, and nothing in this package maps between them.

* **Booking and payment are never retried** (API.md §12). A retried booking is
  a second booking. Only idempotent ``GET``s retry — twice, with backoff.
"""

from dataclasses import dataclass
from typing import Any, Protocol


class GtsTimeouts:
    """API.md §12. Search is slow because GTS fans out to providers."""

    SEARCH_SECONDS: float = 40.0
    DEFAULT_SECONDS: float = 15.0


@dataclass(frozen=True, slots=True)
class GtsDocument:
    """A file GTS renders — the one answer that is not an envelope.

    The itinerary receipt of a ticketed order is a document, not data: GTS
    lays it out itself and hands back the bytes. There is nothing to
    translate, so both halves travel as they arrived — the bytes, and GTS's
    own ``Content-Type`` for them. What a surface is willing to serve is the
    surface's decision, not the boundary's.
    """

    content: bytes
    content_type: str


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

    async def download(
        self, path: str, *, params: dict[str, Any] | None = None, timeout: float | None
    ) -> GtsDocument | None:
        """A ``GET`` whose answer is a **document**, not an envelope.

        Nothing is parsed and nothing is unwrapped; the bytes are the answer,
        and what they are is read off the bytes — GTS marks every answer
        ``application/json``, its documents included, so its ``Content-Type``
        settles nothing.

        ``None`` means **GTS has no such document**, which it says by
        rendering Python's ``None`` into the body. It is not a failure and
        the caller is the one who can explain it — the same arrangement as
        ``reprice`` answering with no price. A refusal *is* a failure and
        raises from the catalogue like every other call, as does an empty
        body: a receipt of no bytes is not a receipt.
        """
        ...


__all__ = ["GtsClient", "GtsDocument", "GtsSession", "GtsTimeouts"]
