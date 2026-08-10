"""The catalogue cache — a day of Redis in front of GTS's static service.

Not an optimisation on the margin. ``/public/catalog/countries/`` is 216
entries of roughly 80 KB, asked for by every passenger form on every device,
and without this every one of those becomes an outbound call.

**There is no purge**, and the difference from ``settings/cache.py`` is worth
naming: there the TTL is a safety net behind a purge that fires on every write,
because the data is the client's and the client edits it. Here nothing in this
installation writes these lists, so the TTL is the *only* invalidation there
can be. A day is the trade — a correction on the GTS side takes that long to
appear, which is the right price for not calling out on every dropdown.
"""

import json
from typing import Any, Final

from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)

CACHE_TTL_SECONDS: Final = 24 * 60 * 60


def key(name: str, *, country: str | None = None) -> str:
    """``catalog:document-types:UZ`` — flat and colon-separated.

    ``country`` reaches here only after the router's pattern has checked it, so
    nothing a caller sends can shape a Redis key.
    """
    return f"catalog:{name}:{country or 'all'}"


async def read(cache_key: str) -> list[dict[str, Any]] | None:
    raw = await get_redis().get(cache_key)
    if raw is None:
        return None
    try:
        cached = json.loads(raw)
    except ValueError:
        logger.warning("catalog_cache_unreadable", cache_key=cache_key)
        return None
    if not isinstance(cached, list):
        logger.warning("catalog_cache_unexpected_shape", cache_key=cache_key)
        return None
    items: list[dict[str, Any]] = cached
    return items


async def write(cache_key: str, items: list[dict[str, Any]]) -> None:
    # ``ensure_ascii=False`` because these lists are mostly Cyrillic: escaping
    # them would roughly double what is stored for no reader's benefit.
    await get_redis().set(
        cache_key,
        json.dumps(items, ensure_ascii=False),
        ex=CACHE_TTL_SECONDS,
    )


__all__ = ["CACHE_TTL_SECONDS", "key", "read", "write"]
