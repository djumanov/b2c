"""What the client sees of an order (modeled on the EasyBooking contract).

A booking answers — and ``GET /public/orders/{id}/`` repeats — four blocks:

* ``order`` — our slim record: ids, the three lifecycle statuses, money,
  deadline, and the one label a screen shows (``stage``) with its sentence
  (``message``). This is the stable contract; it comes from columns, never
  from GTS's spellings.
* ``payment`` — what a payment screen needs: how much, until when, where the
  money stands, and (once a payment attempt exists) which attempt, which card,
  and the phone the OTP went to.
* ``ticketing`` — where the ticket stands, when it was asked for, the issued
  ticket numbers by passenger, and GTS's reason when it failed.
* ``order_data`` — GTS's answer nearly verbatim: routes, passengers, fares,
  baggage. The client reads display detail here, exactly as it already reads
  GTS's shapes throughout the search flow. Commission and cost fields are
  stripped on the way out — agent economics are not the customer's business —
  while the stored copy keeps them.

The list (``GET /public/orders/``) is a fifth shape, and it takes GTS's
``routes`` with it: an order card shows the airline, the flight number, the
times and the airports, and every one of those lives in a segment. Passengers
come along as **names only** — a card says who is flying, and a passport number
has no business riding on a request that returns twenty rows (PROJECT.md §13).
The rest of GTS's answer stays on ``{id}/``.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.money import Money
from app.modules.orders.lifecycle import CONFIRMING, Stage, stage_of
from app.modules.orders.messages import message_for
from app.modules.orders.models import Order, OrderStatus, PaymentStatus
from app.modules.settings.service import SupportContact
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
    payment_status: str
    ticketing_status: str
    #: The one label a screen shows, derived from the three statuses.
    stage: Stage
    #: The sentence that goes with ``stage``, in the request's language.
    message: str
    cancel_reason: str | None
    gts_status: str
    gts_order_number: int
    pnr: str | None
    trip_type: str | None
    route_summary: str | None
    passenger_count: int | None
    amount: Money | None
    ticket_time_limit_at: datetime | None
    paid_at: datetime | None
    ticketed_at: datetime | None
    cancelled_at: datetime | None
    request_id: str
    offer_id: str
    created_at: datetime

    @classmethod
    def from_order(
        cls,
        order: Order,
        *,
        stage: Stage,
        language: str | None,
        support: SupportContact,
    ) -> "OrderOut":
        # Assembled by hand rather than ``from_attributes`` because ``amount``
        # is two columns composed into one ``Money``.
        return cls(
            id=order.id,
            product=order.product,
            status=order.status,
            payment_status=order.payment_status,
            ticketing_status=order.ticketing_status,
            stage=stage,
            message=message_for(stage, language=language, support=support),
            cancel_reason=order.cancel_reason,
            gts_status=order.gts_status,
            gts_order_number=order.gts_order_number,
            pnr=order.pnr,
            trip_type=order.trip_type,
            route_summary=order.route_summary,
            passenger_count=order.passenger_count,
            amount=_money(order),
            ticket_time_limit_at=order.ticket_time_limit_at,
            paid_at=order.paid_at,
            ticketed_at=order.ticketed_at,
            cancelled_at=order.cancelled_at,
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
    payment_status: str
    ticketing_status: str
    stage: Stage
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
        # No attempt lookup on a list: an open attempt reads as awaiting
        # payment here, and the detail screen says more.
        return cls(
            id=order.id,
            product=order.product,
            status=order.status,
            payment_status=order.payment_status,
            ticketing_status=order.ticketing_status,
            stage=stage_of(order),
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


class PaymentAttemptView(BaseModel):
    """The open or latest payment attempt, as the payment block shows it.

    Filled by the payment flow; a freshly booked order has none. Kept apart
    from the attempt row so the response never grows a provider reference.
    """

    id: uuid.UUID
    status: str
    provider: str
    card_last4: str | None = None
    phone_hint: str | None = None
    paid_at: datetime | None = None
    error: str | None = None


class PaymentOut(BaseModel):
    """The payment as the client should see it.

    ``status`` is the order's ``payment_status`` with two refinements read off
    the attempt — ``awaiting_otp`` while the code is being typed,
    ``processing`` while the provider's answer is unknown — and ``cancelled``
    for an order cancelled before it was paid, which is what the screen
    should say rather than "pending".
    """

    status: str
    amount: Money | None
    #: Pay before this moment or GTS releases the seat.
    pay_before: datetime | None
    payment_id: uuid.UUID | None = None
    provider: str | None = None
    card_last4: str | None = None
    phone_hint: str | None = None
    paid_at: datetime | None = None
    error: str | None = None

    @classmethod
    def from_order(
        cls, order: Order, attempt: PaymentAttemptView | None
    ) -> "PaymentOut":
        status = order.payment_status
        if order.status == OrderStatus.CANCELLED and status in (
            PaymentStatus.PENDING,
            PaymentStatus.FAILED,
        ):
            status = "cancelled"
        elif attempt is not None and status != PaymentStatus.PAID:
            if attempt.status == CONFIRMING:
                status = "processing"
            elif attempt.status == "started":
                status = "awaiting_otp"
        return cls(
            status=status,
            amount=_money(order),
            pay_before=order.ticket_time_limit_at,
            payment_id=attempt.id if attempt else None,
            provider=attempt.provider if attempt else None,
            card_last4=attempt.card_last4 if attempt else None,
            phone_hint=attempt.phone_hint if attempt else None,
            paid_at=order.paid_at,
            error=attempt.error if attempt else None,
        )


class TicketOut(BaseModel):
    passenger: str
    ticket_number: str


class TicketingOut(BaseModel):
    """Where the ticket stands — the block the "please wait" screen polls."""

    status: str
    requested_at: datetime | None
    ticketed_at: datetime | None
    tickets: list[TicketOut]
    error: str | None

    @classmethod
    def from_order(cls, order: Order) -> "TicketingOut":
        return cls(
            status=order.ticketing_status,
            requested_at=order.ticketing_requested_at,
            ticketed_at=order.ticketed_at,
            tickets=[
                TicketOut.model_validate(ticket)
                for ticket in gts_order.tickets(order.gts_response)
            ],
            error=order.ticketing_error,
        )


class BookingResultOut(BaseModel):
    """The booking response, the order-detail response and the payment
    responses — one shape for all of them."""

    product: str
    order: OrderOut
    payment: PaymentOut
    ticketing: TicketingOut
    order_data: dict[str, Any]

    @classmethod
    def from_order(
        cls,
        order: Order,
        *,
        language: str | None,
        support: SupportContact,
        attempt: PaymentAttemptView | None = None,
    ) -> "BookingResultOut":
        open_attempt = (
            attempt.status
            if attempt and attempt.status in ("started", CONFIRMING)
            else None
        )
        stage = stage_of(order, open_attempt=open_attempt)
        return cls(
            product=order.product,
            order=OrderOut.from_order(
                order, stage=stage, language=language, support=support
            ),
            payment=PaymentOut.from_order(order, attempt),
            ticketing=TicketingOut.from_order(order),
            order_data=_strip_commission(order.gts_response),
        )


__all__ = [
    "BookingResultOut",
    "OrderListItemOut",
    "OrderOut",
    "PassengerNameOut",
    "PaymentAttemptView",
    "PaymentOut",
    "TicketOut",
    "TicketingOut",
]
