"""Credential, client, adapter — the whole service.

The one thing this layer owns is the seam between the request's DB session and
the stateless adapter: the active GTS credential is decrypted by
``integrations.service.active_credential()`` (the documented door out of that
module), wrapped into a ready ``GtsClient``, and handed to the adapter.

**The search steps stay stateless** — no offer, no search state, nowhere; the
regression tests hold them to D2. ``book`` and ``cancel`` are the exception,
and only in one direction: a completed purchase is written to ``orders``
through that module's service (never its models — ARCHITECTURE.md §4). An
order row is not an offer cache, so D2 is untouched (ARCHITECTURE.md §10);
what it buys is that a customer can find their booking again, and that
cancelling is limited to bookings they actually made.

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

from app.api.errors import NotFound, UpstreamError, ValidationFailed
from app.core.logging import get_logger
from app.modules.cms import service as cms_service
from app.modules.integrations import service as integrations_service
from app.modules.orders import service as orders_service
from app.providers.gts.base import GtsClient
from app.providers.gts.client import client_for
from app.providers.products.base import ProductAdapter, ProductCode
from app.providers.products.orders import (
    BookingResult,
    OrderOperations,
    UnreadableAnswer,
    order_operations,
)

logger = get_logger(__name__)


async def _client(session: AsyncSession) -> GtsClient:
    credential = await integrations_service.active_credential(session)
    if credential is None:
        raise UpstreamError(
            "GTS is not configured on this installation: no active credential"
        )
    return client_for(credential)


async def search(
    session: AsyncSession,
    adapter: ProductAdapter,
    payload: dict[str, Any],
    *,
    requested: str | None = None,
) -> dict[str, Any]:
    data = await adapter.search(await _client(session), payload)
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
    return await adapter.offers(await _client(session), params)


async def upsell(
    session: AsyncSession, adapter: ProductAdapter, payload: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.upsell(await _client(session), payload)


async def verify(
    session: AsyncSession, adapter: ProductAdapter, payload: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.verify(await _client(session), payload)


def _order_ops(adapter: ProductAdapter) -> OrderOperations:
    """The adapter's order half. Unreachable ``404`` — the gate got there first.

    ``RequireProductStep`` already refused any vertical that does not declare
    the step, so a registered adapter serving ``booking/`` implements this. The
    check exists because a protocol narrowed by ``isinstance`` is the only thing
    that makes the call type-safe, and answering with the gate's own words keeps
    the two indistinguishable if it ever is reachable.
    """
    ops = order_operations(adapter)
    if ops is None:
        raise NotFound("This section is not available on this installation")
    return ops


async def book(
    session: AsyncSession,
    adapter: ProductAdapter,
    payload: dict[str, Any],
    *,
    customer_id: uuid.UUID,
) -> dict[str, Any]:
    """Book, then file the answer under the customer (API.md §20, §21).

    The wire is unchanged: what goes back is the provider's own answer, byte for
    byte. What changed underneath is that the adapter now *reads* that answer
    into a ``BookingResult`` before this module sees it, so the order row is
    built from our own types rather than from GTS's dictionaries
    (``providers/products/orders.py``).

    ``UnreadableAnswer`` is not a failure: the provider agreed in words we
    cannot parse, so the seat is probably real. The row is written anyway and
    lands in ``needs_attention``; the customer still gets the answer that names
    their booking.

    **A failed write still does not fail the booking.** GTS is already holding
    the seat, and answering ``500`` would send the client into a retry that
    opens a *second* one — a real seat, and later real money. The response
    carries the provider's identifiers either way, so the customer leaves with a
    handle even when our row is missing; the loss is that they cannot cancel
    through us. Slice S3 removes the need for this by writing the row before the
    call (order-system/04-plan.md).
    """
    ops = _order_ops(adapter)
    client = await _client(session)
    try:
        result = await ops.book(client, payload)
    except UnreadableAnswer as unreadable:
        await _record(
            session,
            customer_id=customer_id,
            product=adapter.code,
            payload=payload,
            result=None,
            response=unreadable.raw,
        )
        return unreadable.raw

    await _record(
        session,
        customer_id=customer_id,
        product=adapter.code,
        payload=payload,
        result=result,
        response=result.raw,
    )
    return result.raw


async def _record(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    product: str,
    payload: dict[str, Any],
    result: BookingResult | None,
    response: dict[str, Any],
) -> None:
    """Write the order, and swallow a write that fails — see ``book``."""
    try:
        await orders_service.record_booking(
            session,
            customer_id=customer_id,
            product=product,
            payload=payload,
            result=result,
            response=response,
        )
    except Exception:
        logger.exception(
            "order_not_recorded", product=product, customer_id=str(customer_id)
        )


async def cancel(
    session: AsyncSession,
    adapter: ProductAdapter,
    payload: dict[str, Any],
    *,
    customer_id: uuid.UUID,
) -> dict[str, Any]:
    """Release a booking — but only one this customer made (API.md §20).

    ``order_number`` is the one field this step reads — GTS's own cancel body is
    ``{"order_number": 61453}`` (EASY_GATEWAY collection, ``/content/Cancel``) —
    and it is read rather than rewritten: the body still reaches GTS exactly as
    it arrived, because which further fields name a booking upstream is not ours
    to decide and a wrong guess costs a real seat
    (``providers/products/flight.py``).

    The lookup runs **before** the GTS call, so a booking that is not this
    customer's is refused without touching the seat — and so is one whose state
    does not allow cancelling at all.
    """
    number = payload.get("order_number") if isinstance(payload, dict) else None
    if not isinstance(number, str | int) or isinstance(number, bool):
        raise ValidationFailed(
            "Cancelling needs the order_number that booking returned",
            field="order_number",
        )
    ops = _order_ops(adapter)
    order = await orders_service.owned_by_provider_number(
        session, customer_id=customer_id, provider_order_number=str(number).strip()
    )
    # Refuse here what the state machine would refuse anyway. Checking after the
    # call would release a real seat and then answer 409 — the one ordering of
    # these two steps that costs money.
    orders_service.ensure_cancellable(order)
    result = await ops.cancel(await _client(session), payload)
    await orders_service.apply_cancel(session, order, result)
    return result.raw


__all__ = ["book", "cancel", "offers", "search", "upsell", "verify"]
