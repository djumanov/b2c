"""What an order looks like on the wire (order-system/03-design.md §3.6).

**The whole row, and the same shape on both endpoints.** There is no trimmed
list variant: ``data`` — the provider's answer — dominates the payload either
way, so dropping our own columns from the list would save nothing and would
force a client to fetch ``{id}/`` for every row it wanted to render.

``status`` is **ours** now (``states.OrderStatus``), not GTS's. Their code is
still published, beside it, as ``provider_status``: a client that has been
reading ``BO`` can keep reading it while it moves over, and support can still
compare the two when they disagree.

The money field follows API.md §1 — ``{"amount": "1250000.00", "currency":
"UZS"}``, the amount a string — which is why it is assembled here from the two
columns rather than serialised straight off the row.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.money import Money
from app.modules.orders.models import Order


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
    #: The provider's own code, verbatim — ``BO``, ``TI``, ``CB``, …
    provider_status: str | None
    #: GTS's order number — what cancelling takes. An integer upstream, a
    #: string here: API.md §1 keeps identifiers textual.
    provider_order_number: str | None
    #: GTS's internal key for the same order. Nothing we call takes it; it is
    #: what GTS support asks for.
    provider_order_uid: str | None
    #: The airline record locator.
    provider_pnr: str | None
    #: The search and the offer this order came from.
    request_id: str | None
    offer_id: str | None
    #: What the customer owes. ``null`` until the provider has priced it.
    amount: Money | None
    #: Travellers in our own shape, each with its ticket number once issued.
    travelers: list[dict[str, Any]]
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
            provider_status=order.provider_status,
            provider_order_number=order.provider_order_number,
            provider_order_uid=order.provider_order_uid,
            provider_pnr=order.provider_pnr,
            request_id=order.request_id,
            offer_id=order.offer_id,
            amount=amount,
            travelers=order.travelers,
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


__all__ = ["OrderOut"]
