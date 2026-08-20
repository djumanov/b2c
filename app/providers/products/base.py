"""The ``ProductAdapter`` port — one flow, five verticals.

API.md §20 asks for the same ``{product}`` pattern everywhere, with known
exceptions: ``railway`` has ``trains/`` and ``train-details/`` instead of
``offers/``, and ``esim`` and ``transfer`` have ``offer/`` instead of
``verify/``.

Writing five nearly identical routers **guarantees** they drift apart — the
exact risk named in PROJECT.md §17. So there is one generic flow, and each
vertical is an adapter that declares what it supports. The public router binds
``{product}`` to an adapter, serves the shared steps generically, mounts the
vertical's extra paths from its declaration, and answers ``404 not_found`` for
a step the adapter does not implement (ARCHITECTURE.md §6).

The acceptance test for phase 3 is a diff: adding the four remaining verticals
must not change the flow. If it does, this port was designed wrong
(PROJECT.md §15).

The port is declared now because the contract needs all five; only ``flight``
is implemented in phase 2 (ARCHITECTURE.md §13.8).
"""

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable

from pydantic import BaseModel

from app.providers.gts.base import GtsClient


class ProductCode(StrEnum):
    """The five verticals of the first release (PROJECT.md §8)."""

    FLIGHT = "flight"
    RAILWAY = "railway"
    INSURANCE = "insurance"
    ESIM = "esim"
    TRANSFER = "transfer"


class FlowStep(StrEnum):
    """Steps a vertical may support. The router serves only what is declared."""

    SEARCH = "search"
    OFFERS = "offers"
    VERIFY = "verify"
    #: eSIM and transfer replace ``verify`` with this (PROJECT.md §8).
    OFFER = "offer"
    BOOKING = "booking"
    #: railway
    TRAINS = "trains"
    TRAIN_DETAILS = "train-details"
    #: flight
    SEAT_MAP = "seat-map"
    ADDITIONAL_SERVICES = "additional-services"
    #: insurance
    CALCULATE = "calculate"
    #: flight and insurance
    UPSELL = "upsell"
    #: transfer
    RECOMMENDED_TIME = "recommended-time"


#: GTS's own word for "the providers are still answering" — the envelope status
#: the search steps relay verbatim as ``search_status`` (API.md §20). Named here
#: because it is no longer only relayed: an adapter that hides an upstream
#: failure answers with it, and the client polls again (STATUS.md §4 no. 86).
SEARCH_IN_PROCESS: Final = "In process"


class OrderSnapshot(BaseModel):
    """One GTS order, read once into the fields the orders module keeps.

    GTS spells its order inconsistently between installations (``firstname``
    vs ``first_name``, prices flat or nested, a deadline that may be an ISO
    string, minutes or seconds), so reading it is the adapter's job and
    happens exactly once, here. The typed fields are what the orders module
    stores in columns; ``raw`` is the full order, kept verbatim.

    Every field an inconsistent GTS might omit is optional — an order with a
    confirmed ``gts_order_number`` is worth recording even when a price or a
    deadline could not be read. The same shape comes back from ``book`` (as a
    ``BookedOrder``) and from ``retrieve``, so refreshing a stored order is
    one function whatever asked for the read.
    """

    gts_order_number: int
    gts_order_uid: str | None = None
    gts_status: str
    pnr: str | None = None
    trip_type: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    route_summary: str | None = None
    passenger_count: int | None = None
    ticket_time_limit_at: dt.datetime | None = None
    raw: dict[str, Any]


class BookedOrder(OrderSnapshot):
    """What ``book()`` hands back: the snapshot plus where it came from."""

    #: Echoed from the request, not read from the answer — the pair that names
    #: the search and the offer this booking came from.
    request_id: str
    offer_id: str


@runtime_checkable
class ProductAdapter(Protocol):
    """What every vertical implements.

    Adapters are **stateless singletons**. Nothing here writes an offer to
    Postgres or Redis: GTS caches by ``request_id`` and we pass through
    (ARCHITECTURE.md D2, §9). The authenticated ``GtsClient`` is handed in per
    call rather than held by the adapter — the credential it wraps comes from
    the request's own DB session, which an adapter must not know about. An
    adapter validates the request lightly and forwards it in GTS's own shape
    (API.md §20).

    ``book()`` is the one step that answers in our own type: the search steps
    relay GTS's shape untouched, but a booking is recorded in our database, so
    its answer is read once — here, behind the port — into a ``BookedOrder``
    the orders module can store without knowing GTS's spellings.
    """

    code: ProductCode

    def supports(self) -> frozenset[FlowStep]:
        """The steps this vertical serves; anything else is ``404``."""
        ...

    async def search(
        self, client: GtsClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Start a search. Returns GTS's ``request_id`` immediately."""
        ...

    async def offers(self, client: GtsClient, params: dict[str, Any]) -> dict[str, Any]:
        """Page through offers — a passthrough to GTS (ARCHITECTURE.md §9)."""
        ...

    async def upsell(
        self, client: GtsClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Fare variants of one offer — a passthrough like ``offers``."""
        ...

    async def verify(
        self, client: GtsClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Re-check price and availability before booking."""
        ...

    async def book(self, client: GtsClient, payload: dict[str, Any]) -> BookedOrder:
        """Book one verified offer at GTS. Never retried, never degraded."""
        ...

    async def retrieve(self, client: GtsClient, order_number: int) -> OrderSnapshot:
        """Read one order back from GTS — the truth about a hold, a price,
        a deadline and a ticket. A GET, so safe to ask as often as needed."""
        ...

    async def ticket(self, client: GtsClient, order_number: int) -> OrderSnapshot:
        """Ask GTS to issue the ticket, charging the agent's deposit.

        A POST that moves money and seats: **never retried blindly**. The
        answer is the order as GTS now sees it — ``TI`` with ticket numbers
        when it issued at once, ``PW`` when it is still working, anything
        else when it did not. An unreadable or absent answer is an exception
        for the caller to settle by ``retrieve``.
        """
        ...


class ProductRegistry:
    """Product code → adapter. Adding a vertical is one registration."""

    def __init__(self) -> None:
        self._adapters: dict[ProductCode, ProductAdapter] = {}

    def register(self, adapter: ProductAdapter) -> None:
        self._adapters[adapter.code] = adapter

    def get(self, code: str) -> ProductAdapter | None:
        try:
            return self._adapters.get(ProductCode(code))
        except ValueError:
            return None

    def codes(self) -> tuple[ProductCode, ...]:
        return tuple(self._adapters)


registry = ProductRegistry()


__all__ = [
    "SEARCH_IN_PROCESS",
    "BookedOrder",
    "FlowStep",
    "OrderSnapshot",
    "ProductAdapter",
    "ProductCode",
    "ProductRegistry",
    "registry",
]
