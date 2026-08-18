"""What an order looks like on the wire (API.md §21).

**Two shapes, and the difference is deliberate.** ``OrderListOut`` is what a
card in "my orders" needs and nothing else; ``OrderOut`` is the whole record.
The list used to be the whole record too, on the reasoning that ``data`` — the
provider's answer — dominated the payload anyway so trimming our own columns
would save nothing. That reasoning ended when ``data`` itself left the list:
twenty booking answers on one page is hundreds of kilobytes, and every one of
them carries passport numbers past a screen that only wants a route and a
price (PROJECT.md §13).

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

from pydantic import BaseModel

from app.core.money import Money
from app.modules.orders.models import Order, OrderPayment


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
    #: Departure, check-in, tour start — whatever the vertical calls it.
    travel_start_at: datetime | None
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
            travel_start_at=order.travel_start_at,
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

    Eleven fields chosen by asking what is on the screen: which trip, what it
    costs, where it stands, and how long is left to pay. Two things are
    deliberately **absent**, and both are the point of having this class:

    * ``data``, the provider's whole booking answer. It is by far the largest
      thing an order carries — routes, segments, fare rules, baggage — and a
      page of twenty of them is hundreds of kilobytes nobody on a list screen
      reads. ``list_orders`` does not even fetch the column.
    * ``passengers``, which carry passport numbers (PROJECT.md §13). A list is
      the wrong place to spray them, and a card only needs the count.

    Both are one ``{id}/`` away for the screen that actually wants them.
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
    #: ``TAS → IST``. The reason this is a column at all: rendering a list row
    #: must not mean opening the provider's answer.
    route_summary: str | None
    travel_start_at: datetime | None
    #: How many are travelling. The travellers themselves are on ``{id}/``.
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
            route_summary=order.route_summary,
            travel_start_at=order.travel_start_at,
            passenger_count=len(order.travelers),
            gts_pnr=order.provider_pnr,
            ticket_time_limit_at=order.ticket_time_limit_at,
            created_at=order.created_at,
        )


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
    #: ``pending`` · ``paid`` · ``failed`` · ``cancelled``.
    status: str
    #: ``redirect`` today. The card flow fills the rest of the vocabulary.
    flow: str
    amount: Money
    #: Where to send the customer. ``null`` once the provider has answered, or
    #: on a flow that does not leave the site.
    redirect_url: str | None
    paid_at: datetime | None
    #: Why it did not work, when it did not.
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_attempt(cls, attempt: OrderPayment) -> "TransactionOut":
        return cls(
            id=attempt.id,
            order_id=attempt.order_id,
            provider=attempt.provider,
            status=attempt.status,
            flow=attempt.flow,
            amount=Money(amount=attempt.amount, currency=attempt.currency),
            redirect_url=attempt.redirect_url,
            paid_at=attempt.paid_at,
            error_message=attempt.error_message,
            created_at=attempt.created_at,
            updated_at=attempt.updated_at,
        )


class TransactionStartIn(BaseModel):
    """Starting a payment: which method, and where to come back to."""

    model_config = {"extra": "forbid"}

    #: One of the codes ``GET /public/payments/methods/`` published.
    method: str
    #: Where the provider sends the customer afterwards. Checked against the
    #: installation's own origins — a redirect target chosen by a request is
    #: otherwise an open redirect with our merchant account's name on it.
    return_url: str


__all__ = [
    "OrderListOut",
    "OrderOut",
    "TransactionOut",
    "TransactionStartIn",
]
