"""Orders: keep what was bought, and answer "is this yours?" (API.md §21).

The module holds one idea — **the local row is the source of ownership, GTS is
the source of status and ticket** (ARCHITECTURE.md §5). Everything here serves
that and nothing more. In particular this service does **not** interpret the
booking answer: it lifts four values out of it for indexing and stores the rest
as it arrived (``models.py`` explains which four and why).

The doors other modules may use are ``record_booking``, ``owned_by_gts_id`` and
``apply_cancel`` — ``products`` calls all three around its passthrough. Nothing
outside this package touches ``models`` or ``repository`` (ARCHITECTURE.md §4).

**Reading the GTS answer is deliberately forgiving.** Every field name here is
documented but not yet confirmed against live GTS (STATUS.md §8), so a value
that is missing or is not a string becomes ``None`` and the row is written
anyway. A booking that really happened must leave a trace even if we could not
read the shape of it; the alternative is losing the order to a rename.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import NotFound
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    page,
    paginate,
)
from app.core.logging import get_logger
from app.modules.orders import repository
from app.modules.orders.models import Order
from app.modules.orders.schemas import OrderOut

logger = get_logger(__name__)

#: ``created_at`` only. The other columns are GTS's values, and ordering by a
#: vocabulary we do not own would publish it as part of our contract
#: (``api/listing.py`` on why this is a whitelist).
_ORDER_ORDERING: OrderingMap = {"created_at": Order.created_at}


def _text(source: dict[str, Any], key: str, *, limit: int) -> str | None:
    """One field out of a GTS payload, or ``None`` if it is not usable.

    Numbers are accepted and stringified: GTS.md §12 shows ``order_number``
    as ``"1250"``, which is exactly the kind of value a JSON encoder on the
    other side may or may not quote. Anything longer than the column is
    refused rather than silently truncated — a cut identifier would match the
    wrong order later, which is worse than none.
    """
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    text = str(value).strip()
    if not text or len(text) > limit:
        return None
    return text


# --- the doors ``products`` uses ------------------------------------------------


async def record_booking(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    product: str,
    payload: dict[str, Any],
    response: dict[str, Any],
) -> Order:
    """File a confirmed booking under the customer who made it.

    Called after GTS has answered, so the seat already exists. The caller
    decides what a failure here means (``products.service.book`` explains why
    it does not fail the request).
    """
    order = Order(
        customer_id=customer_id,
        product=product,
        gts_order_id=_text(response, "order_id", limit=64),
        status=_text(response, "status", limit=16),
        request_id=_text(payload, "request_id", limit=64),
        offer_id=_text(payload, "offer_id", limit=64),
        # Verbatim, and the only place the answer lives. The passengers of the
        # *request* are deliberately absent (models.py, PROJECT.md §13).
        gts_response=response,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    if order.gts_order_id is None:
        # Not an error — the row is written either way — but it means this
        # order cannot be cancelled through us, so it must be visible.
        logger.warning(
            "order_recorded_without_gts_id",
            order_id=str(order.id),
            product=product,
            fields=sorted(response),
        )
    else:
        logger.info(
            "order_recorded",
            order_id=str(order.id),
            product=product,
            gts_order_id=order.gts_order_id,
            status=order.status,
        )
    return order


async def owned_by_gts_id(
    session: AsyncSession, *, customer_id: uuid.UUID, gts_order_id: str
) -> Order:
    """The order behind a GTS identifier, or ``404`` (API.md §20).

    This is the whole of the cancel ownership check. Nothing reaches GTS
    before it passes.
    """
    order = await repository.order_by_gts_id(session, customer_id, gts_order_id)
    if order is None:
        raise NotFound("No such order")
    return order


async def apply_cancel(
    session: AsyncSession, order: Order, response: dict[str, Any]
) -> None:
    """Carry GTS's answer to a cancellation onto the row.

    The status is copied, not translated — ``CB`` stays ``CB`` (API.md §21).
    If the answer carries no status the stamp is still made: we know we asked
    and GTS did not refuse, which is more than the row said a moment ago.
    """
    status = _text(response, "status", limit=16)
    if status is not None:
        order.status = status
    order.cancelled_at = datetime.now(UTC)
    await session.commit()
    logger.info(
        "order_cancelled",
        order_id=str(order.id),
        gts_order_id=order.gts_order_id,
        status=order.status,
    )


# --- what the customer reads ----------------------------------------------------


async def list_orders(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    customer_id: uuid.UUID,
    product: str | None = None,
    status: str | None = None,
) -> Page[OrderOut]:
    """This customer's orders, newest first (API.md §21).

    No ``apply_search``: the only free text here lives inside the opaque
    answer, and searching a blob we do not read is the beginning of reading
    it. `?search=` over PNR and passenger name is an admin need and arrives
    with the status map (API.md §31).
    """
    stmt = repository.owned_orders(customer_id)
    if product is not None:
        stmt = stmt.where(Order.product == product)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    stmt = apply_created_range(stmt, query, Order.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_ORDER_ORDERING, default="-created_at", tiebreak=Order.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([OrderOut.model_validate(row) for row in rows], pagination, total)


async def get_order(
    session: AsyncSession, *, customer_id: uuid.UUID, order_id: uuid.UUID
) -> OrderOut:
    order = await repository.order_by_id(session, customer_id, order_id)
    if order is None:
        raise NotFound("No such order")
    return OrderOut.model_validate(order)


__all__ = [
    "apply_cancel",
    "get_order",
    "list_orders",
    "owned_by_gts_id",
    "record_booking",
]
