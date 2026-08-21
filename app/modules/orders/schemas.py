"""What the client sees of an order (modeled on the EasyBooking contract).

A booking answers — and ``GET /public/orders/{id}/`` repeats — four blocks:

* ``order`` — our slim record: ids, money, deadline, and **one** ``status``
  with its sentence (``message``). The status is the three lifecycle columns
  read together (``lifecycle.stage_of``); the columns themselves are for
  staff and appear only on the admin surface, as ``booking_status``,
  ``payment_status`` and ``ticketing_status``. This is the stable contract;
  it comes from columns, never from GTS's spellings.
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
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.core import i18n
from app.core.money import Money
from app.modules.orders.lifecycle import CONFIRMING, Stage, stage_of
from app.modules.orders.messages import MessageCatalogue
from app.modules.orders.models import Order, OrderStatus, PaymentStatus
from app.modules.payments.schemas import CardIn
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
    #: The one status a screen shows, read off the three lifecycle columns.
    status: Stage
    #: The sentence that goes with ``status``, in the request's language.
    message: str
    #: Why a ``cancelled`` order was cancelled — ``expired`` is the deadline.
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
        language: str | None,
        messages: MessageCatalogue,
    ) -> "OrderOut":
        # Assembled by hand rather than ``from_attributes`` because ``amount``
        # is two columns composed into one ``Money``.
        status = stage_of(order)
        return cls(
            id=order.id,
            product=order.product,
            status=status,
            message=messages.render(status, language=language),
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
    #: The same ``status`` the detail shows — the card and the screen agree.
    status: Stage
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
            status=stage_of(order),
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


class PaymentStartIn(BaseModel):
    """Step 1 of paying: which card. A saved one by id, or one typed now.

    Exactly one of the two. ``hide_input_in_errors`` for the same reason as
    on ``CardCreateIn``: a validation error must not echo a card number.
    """

    model_config = {"extra": "forbid", "hide_input_in_errors": True}

    card_id: uuid.UUID | None = None
    card: CardIn | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "PaymentStartIn":
        if (self.card_id is None) == (self.card is None):
            raise ValueError("Send either card_id or card")
        return self


class PaymentConfirmIn(BaseModel):
    """Step 2: the code the cardholder received, for the attempt it belongs to.

    ``payment_id`` is required on purpose: after a second ``payment/`` the
    customer may still be typing the first SMS's code, and "confirm the open
    attempt" would silently pair it with the wrong one.
    """

    model_config = {"extra": "forbid", "hide_input_in_errors": True}

    payment_id: uuid.UUID
    otp: SecretStr


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


class OrderEventOut(BaseModel):
    """One line of the order's history, for the support screen."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime
    event: str
    from_value: str | None
    to_value: str | None
    actor: str
    note: str | None
    data: dict[str, Any] | None
    request_id: str | None


class PaymentAttemptAdminOut(BaseModel):
    """A payment attempt as support sees it — never the provider reference."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    provider: str
    status: str
    amount: Money
    card_last4: str | None
    phone_hint: str | None
    error: str | None
    paid_at: datetime | None


class RefundIn(BaseModel):
    """Support marking where the refund stands — the money moves elsewhere."""

    model_config = {"extra": "forbid"}

    status: Literal["refunding", "refunded", "refund_failed"]
    note: str | None = Field(default=None, max_length=500)


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
        messages: MessageCatalogue,
        attempt: PaymentAttemptView | None = None,
    ) -> "BookingResultOut":
        # The attempt shapes the ``payment`` block only; ``order.status`` is
        # the columns' reading and says the same on the list and here.
        return cls(
            product=order.product,
            order=OrderOut.from_order(order, language=language, messages=messages),
            payment=PaymentOut.from_order(order, attempt),
            ticketing=TicketingOut.from_order(order),
            order_data=_strip_commission(order.gts_response),
        )


def _check_translated(value: i18n.Translated) -> i18n.Translated:
    """The settings module's rule, repeated: schemas are not a module's
    ``service`` door, so importing it from there would cross the one line
    ARCHITECTURE.md draws. Unknown languages are dropped, not refused."""
    return {
        lang: text for lang, text in value.items() if lang in i18n.SUPPORTED_LANGUAGES
    }


#: Long enough for a paragraph with a phone number and an email in it.
MESSAGE_MAX_LENGTH = 1000


class OrderMessageOut(BaseModel):
    """One status's sentence: what we ship, what the panel wrote, what is shown."""

    status: Stage
    default: i18n.Translated
    custom: i18n.Translated
    text: i18n.Translated


class OrderMessageIn(BaseModel):
    """Languages merge: send one to change one. An empty string clears that
    language back to the default."""

    model_config = {"extra": "forbid"}

    text: i18n.Translated

    @field_validator("text")
    @classmethod
    def _known_languages(cls, value: i18n.Translated) -> i18n.Translated:
        cleaned = _check_translated(value)
        if not cleaned:
            raise ValueError(
                "send at least one language; "
                f"this release serves {', '.join(i18n.SUPPORTED_LANGUAGES)}"
            )
        for lang, text in cleaned.items():
            if len(text) > MESSAGE_MAX_LENGTH:
                raise ValueError(f"{lang}: at most {MESSAGE_MAX_LENGTH} characters")
        return cleaned


class OrderAdminListItemOut(OrderListItemOut):
    """A list row for support: the customer's row, the three columns behind
    its ``status``, who and which GTS order — and the reason, so the inbox
    reads without opening every row."""

    booking_status: str
    payment_status: str
    ticketing_status: str
    #: Why a ``cancelled`` order was cancelled.
    cancel_reason: str | None
    #: GTS's words when the ticket did not come out — the inbox's "why".
    ticketing_error: str | None
    customer_id: uuid.UUID
    gts_order_number: int
    gts_status: str
    #: The last time anything about the order moved; ``ordering=-updated_at``
    #: puts the freshest trouble first.
    updated_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderAdminListItemOut":
        base = OrderListItemOut.from_order(order)
        return cls(
            **base.model_dump(),
            booking_status=order.status,
            payment_status=order.payment_status,
            ticketing_status=order.ticketing_status,
            cancel_reason=order.cancel_reason,
            ticketing_error=order.ticketing_error,
            customer_id=order.customer_id,
            gts_order_number=order.gts_order_number,
            gts_status=order.gts_status,
            updated_at=order.updated_at,
        )


class OrderAdminOrderOut(OrderOut):
    """The customer's ``order`` block plus the three columns its ``status``
    was read from — support sees both the word and the reason for it."""

    booking_status: str
    payment_status: str
    ticketing_status: str

    @classmethod
    def from_public(cls, public: OrderOut, order: Order) -> "OrderAdminOrderOut":
        return cls(
            **public.model_dump(),
            booking_status=order.status,
            payment_status=order.payment_status,
            ticketing_status=order.ticketing_status,
        )


class OrderAdminOut(BookingResultOut):
    """The detail for support: the customer's view plus the books behind it."""

    order: OrderAdminOrderOut
    customer_id: uuid.UUID
    ticketing_attempts: int
    ticketing_requested_at: datetime | None
    gts_checked_at: datetime | None
    events: list[OrderEventOut]
    payments: list[PaymentAttemptAdminOut]


__all__ = [
    "BookingResultOut",
    "OrderAdminListItemOut",
    "OrderAdminOrderOut",
    "OrderAdminOut",
    "OrderEventOut",
    "OrderListItemOut",
    "OrderMessageIn",
    "OrderMessageOut",
    "OrderOut",
    "PassengerNameOut",
    "PaymentAttemptAdminOut",
    "PaymentAttemptView",
    "PaymentConfirmIn",
    "PaymentOut",
    "PaymentStartIn",
    "RefundIn",
    "TicketOut",
    "TicketingOut",
]
