"""Create on booking, read back by owner — and the helpers every later step shares.

``create_order`` is called by ``products.service.book()`` **after** GTS
confirmed the booking; it is the flow's only write and its own transaction.
There is nothing here for a failed booking on purpose: no row is the record.

Everything returned crosses a module boundary, so everything returned is a
schema, never a model row (the ``add_card → CardOut`` convention). The
customer's language and the support contact ride into the schema because
``order.message`` is rendered here, on the server, for every client alike.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import NotFound
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
from app.core.logging import get_logger
from app.db.repository import live
from app.modules.orders import lifecycle
from app.modules.orders.models import (
    Order,
    OrderStatus,
    PaymentStatus,
    TicketingStatus,
)
from app.modules.orders.schemas import BookingResultOut, OrderListItemOut
from app.modules.settings import service as settings_service
from app.providers.products.base import BookedOrder

logger = get_logger(__name__)

_ORDER_ORDERING: OrderingMap = {"created_at": Order.created_at}


async def _present(order: Order, *, language: str | None) -> BookingResultOut:
    """The detail shape, with the message rendered for this request."""
    support = await settings_service.support_contact()
    return BookingResultOut.from_order(order, language=language, support=support)


async def create_order(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    product: str,
    booked: BookedOrder,
    language: str | None = None,
) -> BookingResultOut:
    """Record one confirmed GTS booking — and the first line of its history."""
    # The id is minted here, not at flush: the first history line below needs
    # it before anything has been written.
    order = Order(
        id=uuid.uuid4(),
        customer_id=customer_id,
        product=product,
        status=OrderStatus.BOOKED,
        payment_status=PaymentStatus.PENDING,
        ticketing_status=TicketingStatus.PENDING,
        request_id=booked.request_id,
        offer_id=booked.offer_id,
        gts_order_number=booked.gts_order_number,
        gts_order_uid=booked.gts_order_uid,
        gts_status=booked.gts_status,
        pnr=booked.pnr,
        amount=booked.amount,
        currency=booked.currency,
        trip_type=booked.trip_type,
        route_summary=booked.route_summary,
        passenger_count=booked.passenger_count,
        ticket_time_limit_at=booked.ticket_time_limit_at,
        gts_response=booked.raw,
    )
    session.add(order)
    session.add(
        lifecycle.event(
            order,
            event="order.created",
            actor=lifecycle.CUSTOMER,
            to_value=OrderStatus.BOOKED,
            data={"gts_order_number": booked.gts_order_number},
        )
    )
    await session.commit()
    # ``expire_on_commit=False`` keeps the instance readable; the refresh is
    # what loads the server-side ``created_at``/``updated_at``.
    await session.refresh(order)
    logger.info(
        "order_created",
        order_id=str(order.id),
        product=product,
        gts_order_number=order.gts_order_number,
        gts_status=order.gts_status,
    )
    return await _present(order, language=language)


async def list_orders(
    session: AsyncSession,
    customer_id: uuid.UUID,
    pagination: Pagination,
    query: ListQuery,
) -> Page[OrderListItemOut]:
    stmt = live(Order).where(Order.customer_id == customer_id)
    stmt = apply_search(stmt, query, Order.pnr, Order.route_summary)
    stmt = apply_created_range(stmt, query, Order.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_ORDER_ORDERING,
        default="-created_at",
        tiebreak=Order.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([OrderListItemOut.from_order(row) for row in rows], pagination, total)


async def _owned(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> Order:
    """One order — 404 for a missing one **and** for somebody else's.

    Two situations, one answer on purpose: whether an order id exists is
    nobody's business but its owner's (the saved-cards rule).
    """
    order = await session.scalar(
        live(Order).where(Order.id == order_id, Order.customer_id == customer_id)
    )
    if order is None:
        raise NotFound("Order not found")
    return order


async def get_order(
    session: AsyncSession,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    language: str | None = None,
) -> BookingResultOut:
    return await _present(
        await _owned(session, customer_id, order_id), language=language
    )


__all__ = ["create_order", "get_order", "list_orders"]
