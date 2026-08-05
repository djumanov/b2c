"""``Idempotency-Key`` for the money endpoints (API.md §10).

Booking, payment and refund calls reach GTS or a payment provider. Networks
drop responses and users press buttons twice, so the same request will arrive
twice; charging twice is not recoverable by an apology. The client sends a key,
we remember the first outcome for 24 hours, and a repeat returns it instead of
acting again.

Two failure modes are handled explicitly:

* **Key reused with a different body.** That is a client bug, and replaying the
  old answer would hide it. It is a ``422``.
* **Two identical requests racing.** The first claims the key; the second sees
  the claim and gets ``409`` rather than a second charge. A crash mid-flight
  leaves the claim to expire on its own, so the operation can be retried.

Missing key on such an endpoint is a ``422`` — never a silent pass.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Final

from fastapi import Depends, Header, Request

from app.api.errors import Conflict, ValidationFailed
from app.db.redis import get_redis

#: 24 hours (API.md §10).
KEY_TTL_SECONDS: Final = 24 * 60 * 60
_PREFIX: Final = "idempotency"
_IN_FLIGHT: Final = "__in_flight__"


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


async def idempotency_key(
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IdempotencyContext:
    if not idempotency_key or not idempotency_key.strip():
        raise ValidationFailed(
            "This operation requires an Idempotency-Key header",
            field="Idempotency-Key",
        )
    key = idempotency_key.strip()
    current = fingerprint(request.method, request.url.path, await request.body())

    stored = await get_redis().get(_record_key(key))
    if stored is None:
        # Claim the key before the work starts, so a concurrent duplicate loses
        # the race instead of running alongside us.
        await get_redis().set(
            _record_key(key),
            json.dumps({"fingerprint": current, "response": _IN_FLIGHT}),
            ex=KEY_TTL_SECONDS,
            nx=True,
        )
        return IdempotencyContext(key=key, fingerprint=current)

    record = json.loads(stored)
    if record.get("fingerprint") != current:
        raise ValidationFailed(
            "This Idempotency-Key was already used for a different request",
            field="Idempotency-Key",
        )
    if record.get("response") == _IN_FLIGHT:
        raise Conflict("An identical request is still being processed")
    return IdempotencyContext(key=key, fingerprint=current, replayed=record["response"])


IdempotencyKey = Annotated[IdempotencyContext, Depends(idempotency_key)]


__all__ = [
    "KEY_TTL_SECONDS",
    "IdempotencyContext",
    "IdempotencyKey",
    "fingerprint",
    "idempotency_key",
]
