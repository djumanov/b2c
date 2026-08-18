"""The ``OrderOperations`` port — the anti-corruption layer for the money path.

``ProductAdapter`` is the search flow, and it is a **pipe**: bodies go out and
come back in GTS's own shape, because our flight API *is* the GTS API
(API.md §20). That works right up until an answer has to become a row. From
there the saga reads a status, a total, a deadline and a list of travellers, and
if it reads them out of GTS's dictionaries then every one of those readings is
duplicated per vertical inside the saga — which is the shape that makes adding
a hotel a rewrite instead of a file (order-system/03-design.md `O4`, §3.5).

So the boundary moves: **from ``book`` onwards, an adapter returns our types.**
The provider's answer still travels whole in ``raw``, because the wire keeps
publishing it verbatim and support reads it during incidents; but nothing above
this line reads a key out of it.

The port grows a slice at a time. ``book`` and ``cancel`` are here because they
have callers; ``ticket`` and ``reprice`` arrive with the ticketing slice and
the refund pair with the refund slice. Declaring a method the only adapter
cannot implement would make ``isinstance`` fail and buy nothing.

**Why this module may import ``modules.orders``.** The canonical status
vocabulary is ours, not the boundary's, and the map from GTS's codes onto it is
per vertical — so it belongs to the adapter (§3.5). ``providers/gts/client.py``
already reaches into ``modules.integrations.service`` for ``ActiveGtsCredential``
for the same reason: a module's *published vocabulary* is not its ``models.py``
(ARCHITECTURE.md §4).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.core.money import Money
from app.modules.orders.states import OrderStatus
from app.providers.gts.base import GtsClient
from app.providers.products.base import ProductAdapter, ProductCode


class UnreadableAnswer(Exception):
    """The provider answered, and no order can be made out of what it said.

    Distinct from ``UpstreamError``, and the distinction decides what happens
    to a real reservation. An ``UpstreamError`` means GTS *refused*: nothing was
    booked and the order failed. This means GTS very probably **did** book
    something and we cannot name it — which is not a failure to report to the
    customer but a row for a person to look at (order-system/03-design.md T4).

    The whole answer travels on the exception, because it is the only evidence
    of a reservation that may be holding a real seat.
    """

    def __init__(self, message: str, *, raw: dict[str, Any]) -> None:
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True, slots=True)
class TravelerRef:
    """One traveller, in **our** shape rather than the provider's.

    This is the whole of decision ``O10``. GTS's ``passengers[]`` carries
    passport numbers inside a structure we do not control and cannot promise to
    scrub; the same facts in a shape we define can be anonymised field by field
    when an account is deleted (PROJECT.md §13).

    Every field is optional except the position: a provider that omits a
    middle name must not cost us the traveller.
    """

    position: int
    type: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    birth_date: str | None = None
    gender: str | None = None
    citizenship: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    document_issue_date: str | None = None
    document_expiry_date: str | None = None
    email: str | None = None
    phone: str | None = None
    #: The provider's own id for this traveller — what their support asks for.
    provider_traveler_id: str | None = None
    #: Filled by ticketing, not by booking.
    ticket_number: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The JSON stored in ``orders.travelers``.

        Dates stay strings: they arrive as strings, they are shown as strings,
        and parsing them here would only invent a timezone question nobody
        asked. ``anonymized_at`` is written by the privacy path, not here.
        """
        return {
            "position": self.position,
            "type": self.type,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "middle_name": self.middle_name,
            "birth_date": self.birth_date,
            "gender": self.gender,
            "citizenship": self.citizenship,
            "document": {
                "type": self.document_type,
                "number": self.document_number,
                "issue_date": self.document_issue_date,
                "expire_date": self.document_expiry_date,
            },
            "email": self.email,
            "phone": self.phone,
            "provider_traveler_id": self.provider_traveler_id,
            "ticket_number": self.ticket_number,
            "anonymized_at": None,
        }


@dataclass(frozen=True, slots=True)
class BookingResult:
    """A reservation, said in our words.

    Everything the order row needs and nothing it does not: the identifiers a
    later call will take, the money to charge, the deadline the hold runs to,
    who is travelling, and enough of the journey to render a list row without
    opening ``raw``.
    """

    provider_order_number: str
    provider_order_uid: str | None
    provider_pnr: str | None
    #: The provider's code, kept verbatim beside our own status.
    provider_status: str | None
    status: OrderStatus
    total: Money
    travelers: tuple[TravelerRef, ...]
    #: When the provider stops holding the seat. ``None`` when it did not say —
    #: the caller falls back to a configured window rather than guessing.
    ticket_time_limit_at: datetime | None
    travel_start_at: datetime | None
    route_summary: str | None
    #: The provider's answer, whole. Published on the wire and stored as the
    #: order's ``provider_response``.
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CancelResult:
    """A released reservation.

    Only the provider's code and its answer: the order always ends up
    ``cancelled``, because we asked and the provider did not refuse — a refusal
    is an ``UpstreamError`` and never reaches here.
    """

    provider_status: str | None
    raw: dict[str, Any]


@runtime_checkable
class OrderOperations(Protocol):
    """What a vertical must implement before its orders can be managed.

    Optional beside ``ProductAdapter``: a vertical could be searchable before
    it is bookable, and the router's ``FlowStep`` gate already answers 404 for
    a step the adapter does not declare (ARCHITECTURE.md §6).
    """

    code: ProductCode

    async def book(self, client: GtsClient, payload: dict[str, Any]) -> BookingResult:
        """Hold the reservation and describe it in our terms.

        Raises ``UpstreamError`` when the provider refuses, and
        ``UnreadableAnswer`` when it agrees in words we cannot parse.
        """
        ...

    async def cancel(self, client: GtsClient, payload: dict[str, Any]) -> CancelResult:
        """Release a reservation the provider is still holding."""
        ...

    def status_map(self) -> Mapping[str, OrderStatus]:
        """The vertical's provider codes, translated into our vocabulary.

        Written per vertical because the codes are: GTS's flight service speaks
        ``BO``/``TI``/``CB`` and its rail service does not have to agree.
        """
        ...


def order_operations(adapter: ProductAdapter) -> OrderOperations | None:
    """The adapter's order half, if it has one.

    A function rather than a second registry: one adapter object implements
    both protocols, and asking it what it can do beats keeping two maps that
    can disagree about which verticals exist.
    """
    return adapter if isinstance(adapter, OrderOperations) else None


__all__ = [
    "BookingResult",
    "CancelResult",
    "OrderOperations",
    "TravelerRef",
    "UnreadableAnswer",
    "order_operations",
]
