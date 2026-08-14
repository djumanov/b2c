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
must not change the flow or the saga. If it does, this port was designed wrong
(PROJECT.md §15).

The port is declared now because the contract needs all five; only ``flight``
is implemented in phase 2 (ARCHITECTURE.md §13.8).
"""

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

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
    #: flight — release a booking GTS still holds (GTS.md §4)
    CANCEL = "cancel"
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

    async def book(self, client: GtsClient, payload: dict[str, Any]) -> dict[str, Any]:
        """Hold the booking on the GTS side — a passthrough like the rest.

        Nothing of ours is written: no order row, no payment, no saga yet
        (API.md §20, decision of 2026-08-14). Those are built *on top of* this
        step later, and the request shape does not change when they are.
        """
        ...

    async def cancel(
        self, client: GtsClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Release a booking GTS still holds. Nothing of ours is undone."""
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
    "FlowStep",
    "ProductAdapter",
    "ProductCode",
    "ProductRegistry",
    "registry",
]
