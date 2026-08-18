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

from app.api.errors import (
    NotFound,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
)
from app.core.logging import get_logger
from app.modules.cms import service as cms_service
from app.modules.integrations import service as integrations_service
from app.modules.orders import service as orders_service
from app.providers.gts.base import GtsClient
from app.providers.gts.client import client_for
from app.providers.products.base import ProductAdapter, ProductCode, registry
from app.providers.products.flight import FlightAdapter
from app.providers.products.orders import (
    OrderOperations,
    UnreadableAnswer,
    order_operations,
)

logger = get_logger(__name__)


# One line per vertical, and it lives **here** rather than beside the router.
# A Celery worker never imports a router, so an adapter registered there is
# missing from every background step — which is where ticketing runs. The
# router imports this module, so both surfaces get the same registry.
registry.register(FlightAdapter())


async def gts_client(session: AsyncSession) -> GtsClient:
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
    data = await adapter.search(await gts_client(session), payload)
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
    return await adapter.offers(await gts_client(session), params)


async def upsell(
    session: AsyncSession, adapter: ProductAdapter, payload: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.upsell(await gts_client(session), payload)


async def verify(
    session: AsyncSession, adapter: ProductAdapter, payload: dict[str, Any]
) -> dict[str, Any]:
    return await adapter.verify(await gts_client(session), payload)


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
    idempotency_key: str,
) -> dict[str, Any]:
    """Book, and never lose the booking (API.md §20, §21).

    The order of the first three lines is the slice. Everything that can fail
    *without* touching a seat happens first — the adapter is resolved, the
    credential is read — and only then is the row written, **before** the
    provider is asked. From that line on there is no outcome that leaves a
    reservation nobody can find (02-current-audit.md A1).

    Each ending is a different thing and is treated as one:

    * **duplicate** — the key has been used, so the earlier order is returned
      and GTS is not called again;
    * **refused** — no seat was taken, the order is ``failed``, and the key is
      released so the customer may try again with it;
    * **no answer** — the row stays ``created``, which is precisely what that
      status means: we do not know. Calling it failed would throw away a real
      booking, so reconciliation resolves it later (slice S7);
    * **unreadable** — the provider agreed in words we cannot parse, so the
      order goes to ``needs_attention`` with the answer attached and the key is
      kept: a seat is probably held and a retry must not open a second one.
    """
    ops = _order_ops(adapter)
    client = await gts_client(session)
    order, is_new = await orders_service.start_order(
        session,
        customer_id=customer_id,
        product=adapter.code,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    if not is_new:
        return orders_service.booking_answer(order)

    try:
        result = await ops.book(client, payload)
    except UnreadableAnswer as unreadable:
        order = await orders_service.record_unreadable_booking(
            session, order, unreadable.raw
        )
        return orders_service.booking_answer(order)
    except UpstreamTimeout:
        # Deliberately not marked. ``created`` *is* "we asked and do not know".
        logger.warning(
            "order_answer_missing", order_id=str(order.id), order_no=order.order_no
        )
        raise
    except UpstreamError as refused:
        await orders_service.fail_booking(session, order, reason=str(refused))
        raise

    order = await orders_service.confirm_booking(session, order, result)
    return orders_service.booking_answer(order)


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
    result = await ops.cancel(await gts_client(session), payload)
    await orders_service.apply_cancel(session, order, result)
    return result.raw


__all__ = [
    "book",
    "cancel",
    "gts_client",
    "offers",
    "search",
    "upsell",
    "verify",
]
