"""The Redis connection, shared by the API and the workers.

Redis holds: the ``site-config`` cache, the static catalogues, idempotency
keys, the GTS session, rate-limit counters, and the Celery broker.

It does **not** hold search results. GTS already caches offers by
``request_id``; a second cache would mean two TTLs to reconcile and a state
where an offer exists on one side and not the other (ARCHITECTURE.md D2, §9).
"""

from typing import Any

from redis.asyncio import Redis, from_url

from app.core.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """The process-wide client. Lazily built, so importing never connects."""
    global _client
    if _client is None:
        _client = from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _client


def set_redis(client: Redis | None) -> None:
    """Swap the client — used by tests to install a fake."""
    global _client
    _client = client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping_redis() -> bool:
    """True when Redis answers. Used by ``system/health/`` (API.md §39)."""
    result: Any = await get_redis().ping()
    return bool(result)


__all__ = ["Redis", "close_redis", "get_redis", "ping_redis", "set_redis"]
