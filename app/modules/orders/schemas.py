"""What an order looks like on the wire (API.md §21).

**Two shapes, and the difference is deliberate.** ``OrderListOut`` is what a
card in "my orders" needs and nothing else; ``OrderOut`` is the whole record.
The list used to be the whole record too, on the reasoning that ``data`` — the
provider's answer — dominated the payload anyway so trimming our own columns
would save nothing. That reasoning ended when ``data`` itself left the list:
twenty booking answers on one page is hundreds of kilobytes nobody on a list
screen reads.

The line between the two shapes is **the field, not the object**. A card does
want the route, the dates, the price and who is flying — going to ``{id}/``
for each of twenty rows to render a name is worse for everyone. What it must
not carry is the traveller's document, birth date and contact details, which
would otherwise fly past on every list request (PROJECT.md §13). So the list
publishes ``passengers`` too, trimmed to five fields by ``PassengerCardOut``,
and ``route`` at direction level while the segments stay in ``data``.

**The wire says ``gts_``, the columns say ``provider_``.** Inside, the system
is product-agnostic and GTS is one provider among the ones that may follow;
outside, GTS is the only one there is and clients have been reading
``gts_order_number`` since before the order system existed. Renaming their
field bought nothing and cost them a release, so the translation happens here,
in ``from_order``, where it is one line each.

``status`` is **ours** (``states.OrderStatus``), not GTS's. Their code is still
published, beside it, as ``gts_status``: a client that has been reading ``BO``
can keep reading it while it moves over, and support can still compare the two
when they disagree.

The money field follows API.md §1 — ``{"amount": "1250000.00", "currency":
"UZS"}``, the amount a string — which is why it is assembled here from the two
columns rather than serialised straight off the row.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.logging import get_logger
from app.core.money import Money
from app.modules.orders.models import Order, OrderPayment
from app.providers.payments.base import TransactionStatus

logger = get_logger(__name__)


class RouteDirectionOut(BaseModel):
    """One leg of the journey — out and back are two of these.

    ``from`` is a Python keyword, so the field is ``origin`` and the wire
    spelling is restored by ``alias``. It has to be ``alias`` and not
    ``serialization_alias``: this object is read back **out of the column** as
    well as written, and the column holds the wire spelling.
    """

    model_config = ConfigDict(populate_by_name=True)

    #: Airport codes, from the direction's first and last segment — a
    #: connection does not show up here.
    origin: str | None = Field(default=None, alias="from")
    destination: str | None = Field(default=None, alias="to")
    #: Local to the airport, with the offset GTS stated; UTC when it did not.
    departure_at: datetime | None = None
    arrival_at: datetime | None = None
    #: The whole direction, not one segment of it.
    duration_minutes: int | None = None
    #: ``0`` is a direct flight.
    stops: int | None = None


class RouteOut(BaseModel):
    """The journey as a list row needs it: where, when, how many stops.

    Deliberately stops at the direction. Flight numbers, airlines, terminals
    and aircraft are one level down and stay in the provider's answer, which
    the detail endpoint publishes whole as ``data`` (API.md §20).
    """

    #: ``TAS-IST / IST-TAS`` — the same string as ``route_summary``.
    summary: str | None = None
    directions: list[RouteDirectionOut] = Field(default_factory=list)


class PassengerCardOut(BaseModel):
    """A traveller as a **card** shows one: who, which fare, which ticket.

    The five fields left after removing everything PROJECT.md §13 calls
    personal data — document type and number, birth date, citizenship, gender,
    email, phone. Those are one ``{id}/`` away, where they arrive once for one
    order rather than twenty times per page.

    Not a different object from the detail's traveller: same keys, fewer of
    them. A client that renders a name from the list renders it from the
    detail with the same code.
    """

    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    #: ``ADT`` · ``CHD`` · ``INF`` · ``INS``.
    type: str | None = None
    #: ``null`` until the ticket is issued.
    ticket_number: str | None = None


def _route_out(order: Order) -> RouteOut | None:
    """The stored route, or one built from the summary.

    Three cases, and the fallback is the reason this is a function rather than
    a ``model_validate`` call at each site:

    * a row booked since the column existed — the object, as stored;
    * a row booked before it — ``route_summary`` and no directions, which is
      exactly what that row knows;
    * neither — ``None``.

    Stored JSON that does not fit the shape falls back to the summary as well.
    The column is written by one adapter and read here, so a mismatch means a
    row from an older spelling — and an old order must not make the whole page
    fail to render.
    """
    stored = order.route
    if isinstance(stored, dict):
        try:
            return RouteOut.model_validate(stored)
        except ValidationError:
            logger.warning(
                "order_route_unreadable", order_id=str(order.id), keys=sorted(stored)
            )
    if order.route_summary is None:
        return None
    return RouteOut(summary=order.route_summary, directions=[])


def _passenger_cards(travelers: list[dict[str, Any]]) -> list[PassengerCardOut]:
    """The travellers, trimmed to what a card shows.

    Built key by key rather than by ``model_validate``: the stored traveller
    carries passport numbers, and a constructor that took the whole object and
    relied on the model to drop the extras would put one ``model_config``
    change between us and leaking them.
    """
    return [
        PassengerCardOut(
            first_name=_str(person, "first_name"),
            last_name=_str(person, "last_name"),
            middle_name=_str(person, "middle_name"),
            type=_str(person, "type"),
            ticket_number=_str(person, "ticket_number"),
        )
        for person in travelers
    ]


def _str(person: dict[str, Any], key: str) -> str | None:
    """One traveller field, if it is text. Stored JSON is not typed."""
    value = person.get(key)
    return value if isinstance(value, str) else None


class OrderOut(BaseModel):
    """One order (API.md §21, order-system/03-design.md §3.6)."""

    id: uuid.UUID
    #: Ours and human-readable: ``B2C-2608-000123``. Present on every order,
    #: including the ones that never reached the provider.
    order_no: str
    product: str
    #: Canonical: ``created`` · ``booked`` · ``paid`` · ``ticketing`` ·
    #: ``ticketed`` · … (order-system/03-design.md §3.3).
    status: str
    #: GTS's own code, verbatim — ``BO``, ``TI``, ``CB``, …
    gts_status: str | None
    #: GTS's order number — what cancelling takes. An integer upstream, a
    #: string here: API.md §1 keeps identifiers textual.
    gts_order_number: str | None
    #: GTS's internal key for the same order. Nothing we call takes it; it is
    #: what GTS support asks for.
    gts_order_uid: str | None
    #: The airline record locator.
    gts_pnr: str | None
    #: The search and the offer this order came from.
    request_id: str | None
    offer_id: str | None
    #: What the customer owes. ``null`` until the provider has priced it.
    amount: Money | None
    #: Travellers in **our** shape, each with its ticket number once issued.
    #: Not the same object the booking request sends under the same name — the
    #: phone is one string here, and four fields are added (API.md §20).
    passengers: list[dict[str, Any]]
    #: The journey at direction level; the segments underneath stay in
    #: ``data``. ``null`` only when the order names no journey at all.
    route: RouteOut | None
    #: Departure, check-in, tour start — whatever the vertical calls it.
    travel_start_at: datetime | None
    #: The last direction's arrival. Equal to the departure's arrival one-way.
    travel_end_at: datetime | None
    route_summary: str | None
    #: When the provider stops holding the seat.
    ticket_time_limit_at: datetime | None
    #: ``customer`` · ``admin`` · ``timelimit`` · ``payment_failed``.
    cancellation_reason: str | None
    created_at: datetime
    updated_at: datetime
    booked_at: datetime | None
    paid_at: datetime | None
    ticketed_at: datetime | None
    cancelled_at: datetime | None
    #: The provider's latest answer, verbatim. Named ``data`` rather than
    #: ``provider_response`` because from outside it is simply the order's
    #: content; the column name is an internal detail.
    data: dict[str, Any] | None

    @classmethod
    def from_order(cls, order: Order) -> "OrderOut":
        """Built explicitly rather than by ``model_validate``.

        ``amount`` is two columns on the row and one object on the wire, and a
        validator that reached across an ORM instance to assemble it would be
        harder to read than this.
        """
        amount = (
            Money(amount=order.amount_total, currency=order.currency)
            if order.amount_total is not None and order.currency is not None
            else None
        )
        return cls(
            id=order.id,
            order_no=order.order_no,
            product=order.product,
            status=order.status,
            gts_status=order.provider_status,
            gts_order_number=order.provider_order_number,
            gts_order_uid=order.provider_order_uid,
            gts_pnr=order.provider_pnr,
            request_id=order.request_id,
            offer_id=order.offer_id,
            amount=amount,
            passengers=order.travelers,
            route=_route_out(order),
            travel_start_at=order.travel_start_at,
            travel_end_at=order.travel_end_at,
            route_summary=order.route_summary,
            ticket_time_limit_at=order.ticket_time_limit_at,
            cancellation_reason=order.cancellation_reason,
            created_at=order.created_at,
            updated_at=order.updated_at,
            booked_at=order.booked_at,
            paid_at=order.paid_at,
            ticketed_at=order.ticketed_at,
            cancelled_at=order.cancelled_at,
            data=order.provider_response,
        )


class OrderListOut(BaseModel):
    """One row of "my orders" — what a card renders, and nothing more.

    Chosen by asking what is on the screen: which trip, when, who is on it,
    what it costs, where it stands, and how long is left to pay. Two things are
    deliberately **absent**, and both are the point of having this class:

    * ``data``, the provider's whole booking answer. It is by far the largest
      thing an order carries — segments, fare rules, baggage — and a page of
      twenty of them is hundreds of kilobytes nobody on a list screen reads.
      ``list_orders`` does not even fetch the column.
    * every traveller field that PROJECT.md §13 calls personal data: document
      type and number, birth date, citizenship, gender, email, phone. A list
      request is the wrong place to spray passport numbers; a card needs a
      name and a ticket number.

    Both are one ``{id}/`` away for the screen that actually wants them, where
    they arrive once for one order instead of twenty times for a page.
    """

    id: uuid.UUID
    #: Human-readable and always present, even on an order the provider never
    #: answered for: ``B2C-2608-000123``. What support asks for.
    order_no: str
    product: str
    #: Canonical (order-system/03-design.md §3.3) — what the badge reads.
    status: str
    #: ``null`` until the provider has priced it.
    amount: Money | None
    #: Where, when, how many stops — one object per direction. The reason this
    #: is a column at all: rendering a list row must not mean opening the
    #: provider's answer.
    route: RouteOut | None
    #: Who is travelling, **without** their documents — see
    #: ``PassengerCardOut``.
    passengers: list[PassengerCardOut]
    #: ``TAS-IST / IST-TAS``. The same string as ``route.summary``, kept beside
    #: it because clients have been reading it since before ``route`` existed.
    route_summary: str | None
    travel_start_at: datetime | None
    travel_end_at: datetime | None
    #: How many are travelling. ``len(passengers)``, and cheaper to read when
    #: the card only counts them.
    passenger_count: int
    #: The airline record locator, once there is one.
    gts_pnr: str | None
    #: The countdown on an unpaid order: after this the hold lapses by itself.
    ticket_time_limit_at: datetime | None
    #: The only field ``?ordering=`` accepts, and the default sort.
    created_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderListOut":
        amount = (
            Money(amount=order.amount_total, currency=order.currency)
            if order.amount_total is not None and order.currency is not None
            else None
        )
        return cls(
            id=order.id,
            order_no=order.order_no,
            product=order.product,
            status=order.status,
            amount=amount,
            route=_route_out(order),
            passengers=_passenger_cards(order.travelers),
            route_summary=order.route_summary,
            travel_start_at=order.travel_start_at,
            travel_end_at=order.travel_end_at,
            passenger_count=len(order.travelers),
            gts_pnr=order.provider_pnr,
            ticket_time_limit_at=order.ticket_time_limit_at,
            created_at=order.created_at,
        )


class CardView(BaseModel):
    """The card an attempt is being paid with, as the customer may see it.

    Derived from the digits before they were sent and stored on the attempt, so
    it survives the card being forgotten — a receipt is read afterwards
    (PROJECT.md §13).
    """

    masked_pan: str
    last4: str
    brand: str | None


class OtpView(BaseModel):
    """Where the code went and what the customer may still do about it."""

    #: Masked by the provider. We never see the whole number.
    sent_to: str | None
    #: Our own guard. Past it the attempt stays open and a resend recovers it.
    expires_at: datetime | None
    #: Before this, ``resend-otp/`` answers ``429``.
    resend_after: datetime | None
    attempts_left: int


class TransactionOut(BaseModel):
    """One attempt at paying for an order (API.md §22).

    Called a transaction on the wire because that is what the contract calls
    it, and because "attempt" is the honest word only from inside: a customer
    sees one payment that either went through or did not.
    """

    id: uuid.UUID
    order_id: uuid.UUID
    #: ``payme`` · ``click``.
    provider: str
    #: ``awaiting_card`` · ``awaiting_otp`` · ``pending`` · ``paid`` ·
    #: ``failed`` · ``cancelled``.
    status: str
    amount: Money
    #: ``null`` until the card step has been taken.
    card: CardView | None
    #: ``null`` unless a code is outstanding — so it is ``null`` again once the
    #: attempt is paid, and the client has nothing to count down.
    otp: OtpView | None
    paid_at: datetime | None
    #: Why it did not work, when it did not.
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_attempt(
        cls, attempt: OrderPayment, *, otp_max_attempts: int
    ) -> "TransactionOut":
        card = (
            CardView(
                masked_pan=attempt.card_masked,
                last4=attempt.card_last4 or "",
                brand=attempt.card_brand,
            )
            if attempt.card_masked is not None
            else None
        )
        otp = (
            OtpView(
                sent_to=attempt.otp_sent_to,
                expires_at=attempt.otp_expires_at,
                resend_after=attempt.otp_resend_after,
                attempts_left=max(0, otp_max_attempts - attempt.otp_attempts),
            )
            if attempt.status == TransactionStatus.AWAITING_OTP.value
            else None
        )
        return cls(
            id=attempt.id,
            order_id=attempt.order_id,
            provider=attempt.provider,
            status=attempt.status,
            amount=Money(amount=attempt.amount, currency=attempt.currency),
            card=card,
            otp=otp,
            paid_at=attempt.paid_at,
            error_message=attempt.error_message,
            created_at=attempt.created_at,
            updated_at=attempt.updated_at,
        )


class TransactionStartIn(BaseModel):
    """Opening a payment attempt — and there is nothing to say.

    The provider is the installation's choice rather than the request's
    (``O15``), and with the hosted redirect gone (``O14``) there is nowhere to
    come back from. The model is kept, empty, rather than deleted: ``extra:
    forbid`` is what tells a client still sending ``{"method": "payme"}`` that
    the contract moved, instead of silently ignoring it.
    """

    model_config = {"extra": "forbid"}


__all__ = [
    "CardView",
    "OrderListOut",
    "OrderOut",
    "OtpView",
    "TransactionOut",
    "TransactionStartIn",
]
