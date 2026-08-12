"""Credential, client, adapter — the whole service.

The one thing this layer owns is the seam between the request's DB session and
the stateless adapter: the active GTS credential is decrypted by
``integrations.service.active_credential()`` (the documented door out of that
module), wrapped into a ready ``GtsClient``, and handed to the adapter. No
state, no cache — the regression tests hold this module to D2.

**No active credential is a 502**, not a 404: the product *is* enabled on this
installation, so pretending the resource does not exist would lie to the
client. It is the "the thing behind us cannot serve" case, and
``upstream_error`` is exactly that shelf in the catalogue. A ``CryptoError``
from decryption, by contrast, propagates to the generic 500 handler — it is
our misconfiguration, not GTS's, and the error text must not describe the
key ring.
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import UpstreamError
from app.modules.integrations import service as integrations_service
from app.providers.gts.base import GtsClient
from app.providers.gts.client import client_for
from app.providers.products.base import ProductAdapter


async def _client(session: AsyncSession) -> GtsClient:
    credential = await integrations_service.active_credential(session)
    if credential is None:
        raise UpstreamError(
            "GTS is not configured on this installation: no active credential"
        )
    return client_for(credential)


async def search(
    session: AsyncSession, adapter: ProductAdapter, payload: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.search(await _client(session), payload)


async def offers(
    session: AsyncSession, adapter: ProductAdapter, params: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.offers(await _client(session), params)


__all__ = ["offers", "search"]
