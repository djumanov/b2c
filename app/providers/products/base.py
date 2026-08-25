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

from app.providers.gts.base import GtsClient, GtsDocument


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


class OrderPrice(BaseModel):
    """What ``reprice`` and ``confirm_price`` answer: the order's price today.

    GTS prices a held booking again before it will issue the ticket
    (``reprice_check`` → ``reprice_confirm`` → ``ticketing``, its documented
    lifecycle); this is that price, read once behind the port. The amount is
    not optional here — a quoted price is a whole price or it is nothing.
    "GTS quoted nothing" is said by returning no ``OrderPrice`` at all, and
    means the price has not changed, not that the answer was unreadable.
    """

    amount: Decimal
    currency: str

    #: GTS's **own** verdict on whether the price moved (``price_changed`` in
    #: its answer), ``None`` when it did not say. It is the verdict, not the
    #: figures, that decides: live GTS quotes the *provider's* fare in the
    #: provider's currency (294 EUR against an order booked at 343.04 USD)
    #: while saying ``price_changed: false``, and its own order record keeps
    #: the booked price. Comparing the two figures would call that a change;
    #: GTS says it is not one, and GTS is the one issuing the ticket.
    changed: bool | None = None

    raw: dict[str, Any]


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

    async def reprice(self, client: GtsClient, order_number: int) -> OrderPrice | None:
        """Ask GTS what the held order costs today (``reprice_check``).

        The first of the two price steps GTS requires between booking and
        ticketing. Repeatable: it changes nothing on GTS's side. Raises
        ``UpstreamError`` with GTS's words when the order cannot be priced
        (released, unknown).

        ``None`` means **the price did not change**: GTS quotes a figure only
        when there is a new one, so an answer without a price is its way of
        saying the order still costs what it costs. The caller answers with
        the price it already holds. When a price does come back, read
        ``OrderPrice.changed`` before believing it moved.
        """
        ...

    async def confirm_price(
        self, client: GtsClient, order_number: int
    ) -> OrderPrice | None:
        """Accept today's price on GTS's side (``reprice_confirm``).

        The second price step — GTS's final word on what ticketing will
        debit, sent once the customer has seen the price ``reprice``
        returned. The answer's price is authoritative over the check's;
        ``None`` is "unchanged" here too, and leaves the order's own price
        standing as the confirmed one.

        **Sent only after a check that said the price moved.** GTS keeps no
        offer to confirm otherwise and refuses with ``400803`` ("the offer's
        validity after the recalculation has expired"), which is why the
        caller asks ``reprice`` first and skips this step when the answer is
        "unchanged" (live 2026-08-25).
        """
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

    def receipt_url(
        self,
        base_url: str,
        order_number: int,
        *,
        passenger_index: int | None = None,
    ) -> str:
        """The **whole** link to the document, at GTS's own installation.

        Handed to the customer's app so it can fetch the receipt itself,
        which is why it is absolute: ``base_url`` is this installation's GTS
        (the active credential's, a database setting — never a constant in
        our code), and the rest is the vertical's to spell.

        No call is made and no session is spent: this is a string. Whether
        GTS serves it to a caller holding no agent session is GTS's rule, not
        ours; ``receipt`` fetches the same document *with* our session for
        clients that would rather we did.
        """
        ...

    async def receipt(
        self,
        client: GtsClient,
        order_number: int,
        *,
        passenger_index: int | None = None,
    ) -> GtsDocument:
        """The travel document of a **ticketed** order, as GTS renders it.

        The flight vertical's is the itinerary receipt ("маршрутная
        квитанция"); another vertical's will be its own paper. Whatever it
        is, GTS lays it out and we hand it on: the bytes are never stored,
        because GTS renders them from the order it holds and a copy of ours
        would be the staler of the two the moment anything changed.

        ``passenger_index`` (0-based) narrows it to one traveller; without it
        the document covers everyone on the order. Asking before the ticket
        exists is the caller's mistake to prevent — GTS answers a refusal,
        and that is an ``UpstreamError`` like any other.
        """
        ...

    async def cancel(self, client: GtsClient, order_number: int) -> None:
        """Release a held booking before any ticket is issued (``cancel``).

        GTS's own exit from a hold — "отмена брони до выписки". A ticket
        already issued is not undone here: that is ``void`` or ``refund``,
        and neither is part of this flow.

        Nothing is returned because there is nothing trustworthy to return.
        The answer names the order and the moment it was released and
        **carries no status**, so the caller reads the order back with
        ``retrieve`` to learn what GTS now holds (``CB`` once the hold is
        gone). A refusal raises ``UpstreamError`` with GTS's words — and is
        read back before it is believed, exactly as after a refused
        ``ticket``: an order GTS has already released refuses a second
        cancellation.
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
    "OrderPrice",
    "OrderSnapshot",
    "ProductAdapter",
    "ProductCode",
    "ProductRegistry",
    "registry",
]
