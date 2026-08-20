"""Credential, client, adapter — the whole service.

The one thing this layer owns is the seam between the request's DB session and
the stateless adapter: the active GTS credential is decrypted by
``integrations.service.active_credential()`` (the documented door out of that
module), wrapped into a ready ``GtsClient``, and handed to the adapter.

**Every search step here is stateless** — no offer, no search state, nowhere;
the regression tests hold them to D2. ``book()`` is the one exception by
design: GTS confirms the hold first, and only then is the order recorded
through the orders module's service — the one door into that module.

The flight search response gets one addition on its way out: ``fun_fact``, a
random published fact from the cms module (through its service, never its
models — ARCHITECTURE.md §4), for the client to show while polling
``offers/`` (API.md §20).

**No active credential is a 502**, not a 404: the product *is* enabled on this
installation, so pretending the resource does not exist would lie to the
client. It is the "the thing behind us cannot serve" case, and
``upstream_error`` is exactly that shelf in the catalogue. A ``CryptoError``
from decryption, by contrast, propagates to the generic 500 handler — it is
our misconfiguration, not GTS's, and the error text must not describe the
key ring.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.modules.cms import service as cms_service
from app.modules.integrations import service as integrations_service
from app.modules.orders import service as orders_service
from app.modules.orders.schemas import BookingResultOut
from app.providers.products.base import ProductAdapter, ProductCode

logger = get_logger(__name__)


async def search(
    session: AsyncSession,
    adapter: ProductAdapter,
    payload: dict[str, Any],
    *,
    requested: str | None = None,
) -> dict[str, Any]:
    data = await adapter.search(await integrations_service.gts_client(session), payload)
    if adapter.code != ProductCode.FLIGHT:
        return data
    # Our one addition to the passthrough (API.md §20): a random published
    # fact for the client to show while it polls ``offers/``. Read here and
    # not in the adapter — adapters are forbidden the session. A read leaves
    # no trace, so D2 holds.
    fact = await cms_service.random_fun_fact(session, requested=requested)
    return {**data, "fun_fact": fact}


async def offers(
    session: AsyncSession, adapter: ProductAdapter, params: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.offers(await integrations_service.gts_client(session), params)


async def upsell(
    session: AsyncSession, adapter: ProductAdapter, payload: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.upsell(await integrations_service.gts_client(session), payload)


async def verify(
    session: AsyncSession, adapter: ProductAdapter, payload: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.verify(await integrations_service.gts_client(session), payload)


async def book(
    session: AsyncSession,
    adapter: ProductAdapter,
    payload: dict[str, Any],
    *,
    customer_id: uuid.UUID,
    language: str | None = None,
) -> BookingResultOut:
    """Book at GTS, then record the order — in that order, on purpose.

    The user's chosen flow: a row exists only for a booking GTS confirmed.
    Everything that can fail — validation, no credential, GTS's refusal, a
    timeout — raises before the write, so a failed booking leaves no order
    behind. The write itself is ``orders_service.create_order``'s own
    transaction.
    """
    client = await integrations_service.gts_client(session)
    booked = await adapter.book(client, payload)
    return await orders_service.create_order(
        session,
        customer_id=customer_id,
        product=adapter.code,
        booked=booked,
        language=language,
    )


__all__ = [
    "book",
    "offers",
    "search",
    "upsell",
    "verify",
]
