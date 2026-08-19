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
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.api.errors import AppError
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
class RouteDirectionRef:
    """One leg of the journey — out and back are two of these.

    **Direction level, not segment level.** A list card answers four questions
    — where from, where to, when, and how many stops — and every one of them is
    a property of the direction as a whole. Flight numbers, airlines and
    terminals belong to the segments underneath, and those stay in ``raw``: an
    order with two connections each way would otherwise put six objects on a
    screen that shows one line.

    ``origin``/``destination`` rather than ``from``/``to`` because ``from`` is a
    Python keyword; the wire spelling is restored in ``schemas.py``.
    """

    origin: str | None = None
    destination: str | None = None
    departure_at: datetime | None = None
    arrival_at: datetime | None = None
    duration_minutes: int | None = None
    stops: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """The JSON stored inside ``orders.route``.

        Datetimes become ISO strings here rather than at the edge: JSONB has no
        datetime, and a column that sometimes holds one shape and sometimes
        another is worse than a column that always holds text.
        """
        return {
            "from": self.origin,
            "to": self.destination,
            "departure_at": (
                self.departure_at.isoformat() if self.departure_at else None
            ),
            "arrival_at": self.arrival_at.isoformat() if self.arrival_at else None,
            "duration_minutes": self.duration_minutes,
            "stops": self.stops,
        }


@dataclass(frozen=True, slots=True)
class RouteRef:
    """The journey, product-agnostic: directions plus the one-line summary.

    A hotel would have one direction and a tour none; the shape does not
    presume flights, which is the point of it being here rather than in the
    flight adapter.
    """

    summary: str | None
    directions: tuple[RouteDirectionRef, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "directions": [leg.as_dict() for leg in self.directions],
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
    #: The end of the journey — the last direction's arrival. Equal to the
    #: departure's arrival on a one-way.
    travel_end_at: datetime | None
    route_summary: str | None
    #: The journey at direction level. ``None`` when the answer's route could
    #: not be read, which is a cosmetic loss and never refuses a booking.
    route: RouteRef | None
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


class FailureClass(StrEnum):
    """What kind of "no" the provider said, which decides what happens next.

    The distinction is the point of ``O5``. An empty deposit balance and a fare
    that no longer exists are both failures to issue a ticket, and treating them
    the same would refund every customer of the day for an accounting problem
    that a top-up fixes in a minute.
    """

    #: Try again — the provider was busy, slow, or briefly unreachable.
    RETRYABLE = "retryable"
    #: Retryable, and **ours**: our own balance at the provider is empty. Kept
    #: apart so it can raise an alarm nobody confuses with a customer's problem.
    DEPOSIT = "deposit"
    #: There is nothing to try again. The fare is gone, the flight is not, or
    #: the provider refused outright.
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class TicketingResult:
    """Tickets, issued.

    Travellers come back whole rather than as bare numbers: the provider is the
    only place a ticket number exists, and reading the answer's own passenger
    list is the only way to be sure whose it is.
    """

    provider_status: str | None
    status: OrderStatus
    travelers: tuple[TravelerRef, ...]
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RepriceResult:
    """What the provider says the order costs **now**.

    Asked before ticketing because the answer can differ from what the customer
    already paid, and buying a ticket at a price nobody agreed to is worse than
    not buying one (``O6``).
    """

    total: Money
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

    async def reprice(self, client: GtsClient, order_number: str) -> RepriceResult:
        """What the reservation costs now, before a ticket is bought."""
        ...

    async def ticket(self, client: GtsClient, order_number: str) -> TicketingResult:
        """Issue the tickets.

        Raises ``UpstreamError``/``UpstreamTimeout`` like every other call; what
        the caller does about it is decided by ``classify``.
        """
        ...

    def classify(self, failure: AppError) -> FailureClass:
        """Sort a provider failure into retry, alarm, or give up.

        On the adapter because the wording is the vertical's: GTS's flight
        service says "user don't have enough credits on account" and nothing
        outside this file should have to know that sentence.
        """
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
    "FailureClass",
    "RepriceResult",
    "RouteDirectionRef",
    "RouteRef",
    "TicketingResult",
    "OrderOperations",
    "TravelerRef",
    "UnreadableAnswer",
    "order_operations",
]
