"""The ``site-config`` cache — the mechanism behind the product's promise.

``GET /public/site-config/`` is the first call the website and the app make, on
every cold load, and it reads six tables. Caching it in Redis is not an
optimisation on the margin; without it the busiest endpoint on the installation
is also one of the heaviest.

**Any write to any setting purges it.** ARCHITECTURE.md §5 calls this out by
name: it is what turns "change the logo, no deploy" from a slogan into
something that is true a second later. The purge lives here rather than in each
endpoint so that a settings endpoint added in a year cannot forget it.

What is cached is the document *before* translations are collapsed, so one
entry serves every language. Flattening is a dictionary walk; the six queries
are what was worth avoiding.
"""

import json
from typing import Any, Final

from app.core.logging import get_logger
from app.db.redis import get_redis

logger = get_logger(__name__)

CACHE_KEY: Final = "site-config"

#: A safety net, not the mechanism. Writes purge the key, so this only matters
#: if a purge is ever lost — an hour of a stale logo is recoverable, a
#: permanently stale one is a support call.
CACHE_TTL_SECONDS: Final = 60 * 60


async def read() -> dict[str, Any] | None:
    raw = await get_redis().get(CACHE_KEY)
    if raw is None:
        return None
    try:
        cached: dict[str, Any] = json.loads(raw)
    except ValueError:
        # Written by an older version with a different shape. Treat it as a
        # miss rather than serving something the client cannot parse.
        logger.warning("site_config_cache_unreadable")
        return None
    return cached


async def write(document: dict[str, Any]) -> None:
    await get_redis().set(
        CACHE_KEY,
        json.dumps(document, ensure_ascii=False, default=str),
        ex=CACHE_TTL_SECONDS,
    )


async def purge() -> None:
    await get_redis().delete(CACHE_KEY)
    logger.info("site_config_cache_purged")


__all__ = ["CACHE_KEY", "CACHE_TTL_SECONDS", "purge", "read", "write"]
