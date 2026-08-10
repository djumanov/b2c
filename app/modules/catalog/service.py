"""Cache, then GTS. The whole module is these two functions.

Items are handed on **exactly as GTS sends them** — API.md §7 exempts §26 from
the usual translation-collapsing, because these lists are not our content and
reshaping a reference table we do not own would only mean maintaining a second
vocabulary. The GTS *envelope* is still stripped, in the provider.

The cache is read before the base URL is looked up, so a hit costs no database
round trip: ``AsyncSession`` does not take a connection until its first
statement.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog import cache
from app.modules.integrations import service as integrations_service
from app.providers.gts import static as gts_static


async def document_types(
    session: AsyncSession, *, country: str | None = None
) -> list[dict[str, Any]]:
    cache_key = cache.key("document-types", country=country)
    cached = await cache.read(cache_key)
    if cached is not None:
        return cached

    base_url = await integrations_service.gts_base_url(session)
    items = await gts_static.document_types(base_url, country=country)
    await cache.write(cache_key, items)
    return items


async def countries(session: AsyncSession) -> list[dict[str, Any]]:
    cache_key = cache.key("countries")
    cached = await cache.read(cache_key)
    if cached is not None:
        return cached

    base_url = await integrations_service.gts_base_url(session)
    items = await gts_static.countries(base_url)
    await cache.write(cache_key, items)
    return items


__all__ = ["countries", "document_types"]
