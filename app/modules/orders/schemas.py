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

The list (``GET /public/orders/``) is a fourth shape, and it takes GTS's
``routes`` with it: an order card shows the airline, the flight number, the
times and the airports, and every one of those lives in a segment. Passengers
come along as **names only** — a card says who is flying, and a passport number
has no business riding on a request that returns twenty rows (PROJECT.md §13).
The rest of GTS's answer stays on ``{id}/``.
"""

import uuid
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel

from app.core.money import Money
from app.modules.orders.models import Order, OrderStatus
from app.providers.products import gts_order


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


class PassengerNameOut(BaseModel):
    """Who is flying, as a list card names them — nothing more.

    Three fields, all of them nullable: GTS omits a middle name more often
    than it sends one, and a card that cannot name the traveller is still a
    card. Everything else about a passenger — document, birth date,
    citizenship, gender, contacts — is on ``{id}/`` and only there.
    """

    first_name: str | None
    last_name: str | None
    middle_name: str | None


class OrderListItemOut(BaseModel):
    """One row of "my orders" — enough to draw the card, and no more.

    "No more" used to mean the columns alone, with ``route_summary`` standing
    in for the journey. One string cannot carry an airline, a flight number
    and two clock times, so GTS's ``routes`` ride along whole. What stays
    behind is the rest of the answer: fares, baggage, and every passenger
    field but the name.
    """

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
    #: GTS's route objects, verbatim — segments, flight numbers, times.
    #: Empty when the stored answer carried none.
    routes: list[dict[str, Any]]
    #: Names only; documents and contacts live on ``{id}/``.
    passengers: list[PassengerNameOut]

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
            routes=[
                _strip_commission(route)
                for route in gts_order.routes(order.gts_response)
            ],
            passengers=[
                PassengerNameOut.model_validate(person)
                for person in gts_order.passenger_names(order.gts_response)
            ],
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
    "PassengerNameOut",
    "PaymentOut",
]
