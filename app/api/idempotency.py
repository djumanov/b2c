"""``Idempotency-Key`` for the money endpoints (API.md §10).

Booking, payment and refund calls reach GTS or a payment provider. Networks
drop responses and users press buttons twice, so the same request will arrive
twice; charging twice is not recoverable by an apology. The first outcome is
remembered for 24 hours, and a repeat returns it instead of acting again.

**The key is not asked of the client.** It was, and the cost was a rule the
client had to keep: mint a UUID when the button is pressed, hold it in screen
state, send the same one on every retry, mint a new one when the form changes.
Every one of those can be got wrong, and getting one wrong costs a second seat
or a second charge. So when the header is absent the key is **derived from the
request itself** — the same request always produces the same key, and the
client keeps no state at all.

Derivation is deliberately *not* random. A fresh key per request would give
every duplicate a different key, which is the same as having no first layer.
It is ``sha256`` over the subject, the method, the path and the **canonical**
body, and each of those is load-bearing:

* the **subject**, because the Redis record lives in one flat namespace and two
  customers whose bodies happen to match must never read each other's answer;
* the **canonical** body rather than the raw bytes, because a client that
  serialises the same booking twice does not always produce the same bytes —
  key order and whitespace are the serialiser's business — and hashing the raw
  form would turn a formatting difference into a second booking.

A supplied header still wins, and is the stronger promise of the two: a derived
key protects *this request*, while a client's key protects *the intent* across
an edited body. ``auto:`` is reserved so the two namespaces cannot meet.

Two failure modes are handled explicitly:

* **Key reused with a different body.** That is a client bug, and replaying the
  old answer would hide it. It is a ``422``. It cannot happen on the derived
  path: a different body is a different key.
* **Two identical requests racing.** The first claims the key; the second sees
  the claim and gets ``409`` rather than a second charge. The claim is made
  with ``SET NX`` and **its answer is what decides who won** — a read followed
  by a write would let both requests through the gap between them, which is the
  one case this module exists for. A crash mid-flight leaves the claim to
  expire on its own after ``IN_FLIGHT_TTL_SECONDS``, so the operation can be
  retried in a minute rather than tomorrow.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Final

from fastapi import Depends, Header, Request

from app.api.deps import request_subject
from app.api.errors import Conflict, ValidationFailed
from app.db.redis import get_redis

#: How long a *stored outcome* is replayable — 24 hours (API.md §10).
KEY_TTL_SECONDS: Final = 24 * 60 * 60

#: How long an *unfinished* claim survives. Deliberately far shorter than the
#: outcome: a handler that crashed before storing anything must not lock its key
#: for a day, because every retry would answer ``409`` and the caller would have
#: no way to make the payment at all. A minute outlives any single request and
#: is a tolerable wait.
IN_FLIGHT_TTL_SECONDS: Final = 60

#: The header's name, published in the schema and named in ``field`` when a
#: supplied one is refused.
IDEMPOTENCY_HEADER: Final = "Idempotency-Key"

_PREFIX: Final = "idempotency"
_IN_FLIGHT: Final = "__in_flight__"

#: Reserved for keys this module derives. A client may not write into it, or it
#: could aim a request at another subject's derived key and be handed their
#: answer.
_DERIVED_PREFIX: Final = "auto:"


def _record_key(key: str) -> str:
    return f"{_PREFIX}:{key}"


def fingerprint(method: str, path: str, body: bytes) -> str:
    """Identify the request a key was first used for."""
    digest = hashlib.sha256()
    digest.update(method.encode())
    digest.update(b"\0")
    digest.update(path.encode())
    digest.update(b"\0")
    digest.update(body)
    return digest.hexdigest()


def canonical(body: bytes) -> bytes:
    """The body with its keys sorted and its whitespace gone.

    Two serialisations of the same object are the same request, and the client
    does not choose which one its JSON library emits. A body that will not
    parse — an empty one, on ``cancel/`` — is returned as it arrived, because
    there is nothing to normalise and the bytes are already stable.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return body
    return json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()


def derived_key(subject: str, method: str, path: str, body: bytes) -> str:
    """The key for a request that did not bring one (API.md §10).

    Deterministic on purpose: the same request must hash to the same key, or
    the first layer stops existing. See the module docstring for why each part
    is in here.
    """
    digest = hashlib.sha256()
    for part in (subject, method, path):
        digest.update(part.encode())
        digest.update(b"\0")
    digest.update(canonical(body))
    return f"{_DERIVED_PREFIX}{digest.hexdigest()}"


@dataclass(slots=True)
class IdempotencyContext:
    """Handed to the handler; it stores the result once the work succeeded."""

    key: str
    fingerprint: str
    replayed: dict[str, Any] | None = None

    @property
    def is_replay(self) -> bool:
        return self.replayed is not None

    async def store(self, response: Any) -> None:
        """Remember this outcome so a repeat of the key replays it."""
        await get_redis().set(
            _record_key(self.key),
            json.dumps(
                {"fingerprint": self.fingerprint, "response": response},
                ensure_ascii=False,
                default=str,
            ),
            ex=KEY_TTL_SECONDS,
        )

    async def release(self) -> None:
        """Drop the claim after a failure, so the caller may retry the key."""
        await get_redis().delete(_record_key(self.key))


def _answer_from(key: str, current: str, stored: str | bytes) -> IdempotencyContext:
    """Read somebody else's record: a mismatch, a claim, or a replay."""
    record = json.loads(stored)
    if record.get("fingerprint") != current:
        raise ValidationFailed(
            "This Idempotency-Key was already used for a different request",
            field="Idempotency-Key",
        )
    if record.get("response") == _IN_FLIGHT:
        raise Conflict("An identical request is still being processed")
    return IdempotencyContext(key=key, fingerprint=current, replayed=record["response"])


_HEADER_DESCRIPTION: Final = (
    "**Optional.** Leave it out and the server derives one from the request "
    "itself, so an identical repeat never acts twice — the client keeps no "
    "state. Send one to make the promise stronger: a supplied key holds across "
    "an edited body, where a derived one does not. Either way the same key "
    "replays the first outcome for 24 hours, a supplied key reused with a "
    "different body is a `422`, and a duplicate arriving while the first is "
    "still running is a `409` (API.md §10). Keys beginning `auto:` are the "
    "server's own and are refused."
)


async def idempotency_key(
    request: Request,
    idempotency_key: Annotated[
        str | None,
        Header(alias=IDEMPOTENCY_HEADER, description=_HEADER_DESCRIPTION),
    ] = None,
) -> IdempotencyContext:
    body = await request.body()
    supplied = (idempotency_key or "").strip()
    if supplied.startswith(_DERIVED_PREFIX):
        raise ValidationFailed(
            "Keys beginning `auto:` are reserved for the server",
            field=IDEMPOTENCY_HEADER,
        )
    if supplied:
        key = supplied
        current = fingerprint(request.method, request.url.path, body)
    else:
        # Derived from the canonical body, and fingerprinted over the same
        # bytes: the key and the fingerprint then move together, so the
        # "reused for a different request" 422 cannot fire on this path.
        key = derived_key(
            request_subject(request), request.method, request.url.path, body
        )
        current = fingerprint(request.method, request.url.path, canonical(body))

    redis = get_redis()
    record_key = _record_key(key)

    stored = await redis.get(record_key)
    if stored is None:
        # Claim the key before the work starts, so a concurrent duplicate loses
        # the race instead of running alongside us. `NX` answers the only
        # question that matters — did *this* request create the claim? Reading
        # and then writing would not: two duplicates can both find nothing.
        claimed = await redis.set(
            record_key,
            json.dumps({"fingerprint": current, "response": _IN_FLIGHT}),
            ex=IN_FLIGHT_TTL_SECONDS,
            nx=True,
        )
        if claimed:
            return IdempotencyContext(key=key, fingerprint=current)

        # Somebody claimed it in the gap. Answer from their record, exactly as
        # if this request had arrived a moment later.
        stored = await redis.get(record_key)
        if stored is None:
            # Gone again between the two calls. On a money endpoint the
            # conservative answer is to make the caller retry rather than to
            # let a request through whose twin's outcome is unknown.
            raise Conflict("An identical request is still being processed")

    return _answer_from(key, current, stored)


IdempotencyKey = Annotated[IdempotencyContext, Depends(idempotency_key)]


__all__ = [
    "IDEMPOTENCY_HEADER",
    "IN_FLIGHT_TTL_SECONDS",
    "KEY_TTL_SECONDS",
    "IdempotencyContext",
    "IdempotencyKey",
    "canonical",
    "derived_key",
    "fingerprint",
    "idempotency_key",
]
