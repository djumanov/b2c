"""What the client sees of an order (modeled on the EasyBooking contract).

A booking answers — and ``GET /public/orders/{id}/`` repeats — three blocks:

* ``order`` — our slim record: ids, status, money, deadline. This is the
  stable contract; it comes from columns, never from GTS's spellings.
* ``payment`` — **computed, not stored**: what a payment screen needs today
  (how much, until when, still pending?). When the payment iteration lands a
  real payment record, this block is where it will surface.
* ``order_data`` — GTS's answer nearly verbatim: routes, passengers, fares,
  baggage. The client reads display detail here, exactly as it already reads
  GTS's shapes throughout the search flow. Commission and cost fields are
  stripped on the way out — agent economics are not the customer's business —
  while the stored copy keeps them.
"""

import uuid
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel

from app.core.money import Money
from app.modules.orders.models import Order, OrderStatus


def _money(order: Order) -> Money | None:
    if order.amount is None or order.currency is None:
        return None
    return Money(amount=order.amount, currency=order.currency)


def _strip_commission(value: Any) -> Any:
    """Drop agent-economics keys, recursively, wherever GTS put them."""
    if isinstance(value, dict):
        return {
            key: _strip_commission(item)
            for key, item in value.items()
            if not (
                isinstance(key, str) and ("commission" in key or key == "cost_price")
            )
        }
    if isinstance(value, list):
        return [_strip_commission(item) for item in value]
    return value


class OrderOut(BaseModel):
    """Our slim record of one booking."""

    id: uuid.UUID
    product: str
    status: str
    gts_status: str
    gts_order_number: int
    pnr: str | None
    trip_type: str | None
    route_summary: str | None
    passenger_count: int | None
    amount: Money | None
    ticket_time_limit_at: datetime | None
    request_id: str
    offer_id: str
    created_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderOut":
        # Assembled by hand rather than ``from_attributes`` because ``amount``
        # is two columns composed into one ``Money``.
        return cls(
            id=order.id,
            product=order.product,
            status=order.status,
            gts_status=order.gts_status,
            gts_order_number=order.gts_order_number,
            pnr=order.pnr,
            trip_type=order.trip_type,
            route_summary=order.route_summary,
            passenger_count=order.passenger_count,
            amount=_money(order),
            ticket_time_limit_at=order.ticket_time_limit_at,
            request_id=order.request_id,
            offer_id=order.offer_id,
            created_at=order.created_at,
        )


class OrderListItemOut(BaseModel):
    """One row of "my orders" — enough to render the list, nothing heavy."""

    id: uuid.UUID
    product: str
    status: str
    pnr: str | None
    trip_type: str | None
    route_summary: str | None
    passenger_count: int | None
    amount: Money | None
    ticket_time_limit_at: datetime | None
    created_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderListItemOut":
        return cls(
            id=order.id,
            product=order.product,
            status=order.status,
            pnr=order.pnr,
            trip_type=order.trip_type,
            route_summary=order.route_summary,
            passenger_count=order.passenger_count,
            amount=_money(order),
            ticket_time_limit_at=order.ticket_time_limit_at,
            created_at=order.created_at,
        )


#: Order status → what the payment screen should say about it.
_PAYMENT_STATUS: Final[dict[str, str]] = {
    OrderStatus.BOOKED: "pending",
    OrderStatus.PAID: "paid",
    OrderStatus.TICKETED: "paid",
    OrderStatus.CANCELLED: "cancelled",
}


class PaymentOut(BaseModel):
    """The payment as the client should see it — derived from the order."""

    status: str
    amount: Money | None
    #: Pay before this moment or GTS releases the seat.
    pay_before: datetime | None


class BookingResultOut(BaseModel):
    """The booking response, and the order-detail response — same shape."""

    product: str
    order: OrderOut
    payment: PaymentOut
    order_data: dict[str, Any]

    @classmethod
    def from_order(cls, order: Order) -> "BookingResultOut":
        return cls(
            product=order.product,
            order=OrderOut.from_order(order),
            payment=PaymentOut(
                status=_PAYMENT_STATUS.get(order.status, "pending"),
                amount=_money(order),
                pay_before=order.ticket_time_limit_at,
            ),
            order_data=_strip_commission(order.gts_response),
        )


__all__ = [
    "BookingResultOut",
    "OrderListItemOut",
    "OrderOut",
    "PaymentOut",
]
