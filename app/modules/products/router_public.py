"""``POST /public/{product}/…`` — the generic search flow (API.md §20).

One router serves every vertical: ``{product}`` is resolved against the
adapter registry, so adding a vertical is one adapter file and one
registration line, with no new routes (ARCHITECTURE.md §6).

**The gate is not a feature flag.** Which verticals an installation sells
comes from its GTS agreement through ``product_settings``, which the panel
cannot write (API.md §28) — so the check reads the cached site-config
document, not ``features``. All four ways to miss — unknown code, vertical
not yet implemented, step the adapter does not declare, vertical switched
off — answer the same ``404`` with ``RequireFeature``'s exact message,
because from outside "we do not sell this" and "this does not exist" must be
indistinguishable (API.md §41).

**Auth is optional on the search steps** (§20's ``(✓)``): the search form is
public. A presented but invalid token still 401s — ``OptionalCustomer``
forgives absence, not garbage — and the search rate limit keys on the subject
when there is one, the IP otherwise.

**Booking is the flow's one authenticated, writing step.** The search steps
relay GTS untouched; ``booking/`` holds the seat at GTS and records the order
(``modules/orders``), so it demands a customer token and the tighter
``payment`` rate limit. The adapter gate is declared **before** the token
check so a vertical this installation does not sell answers ``404`` even to
an anonymous caller (API.md §41).

**The bodies stay ``dict``, the documentation does not.** Every step carries an
``openapi_extra`` from ``products/openapi.py`` describing what it sends and
returns, because a passthrough that annotates nothing publishes a schema that
teaches nothing. Those schemas are **documentation only** — they never run.
The check that does run is the adapter's, and it is looser on purpose
(``providers/products/flight.py``).
"""

from typing import Annotated, Any

from fastapi import Depends, Path

from app.api.deps import CurrentCustomer, LanguageDep, OptionalCustomer, RateLimit
from app.api.envelope import enveloped_router
from app.api.errors import NotFound
from app.db.session import SessionDep
from app.modules.orders.schemas import BookingResultOut
from app.modules.products import service
from app.modules.products.openapi import (
    FLIGHT_BOOKING,
    FLIGHT_OFFERS,
    FLIGHT_SEARCH,
    FLIGHT_UPSELL,
    FLIGHT_VERIFY,
)
from app.modules.settings import service as settings_service
from app.providers.products.base import FlowStep, ProductAdapter, registry

router = enveloped_router(prefix="/{product}", tags=["products"])

# The vertical registry populates itself: ``providers/products/__init__.py``
# registers every adapter, and importing ``providers.products.base`` runs it.
# That is where it belongs — a Celery worker never imports a router, and
# ticketing needs the same registry a request does.


class RequireProductStep:
    """Resolve ``{product}`` to an adapter or 404 — the vertical's gate.

    Registry first (no Redis round-trip for a code that cannot exist), the
    switched-off check second. The message matches ``RequireFeature``'s so
    the two gates are indistinguishable from outside.
    """

    __slots__ = ("step",)

    def __init__(self, step: FlowStep) -> None:
        self.step = step

    async def __call__(
        self,
        product: Annotated[
            str,
            Path(
                description=(
                    "Vertical code. Today only `flight` answers; the other four "
                    "arrive in phase 3 and any other value is a 404."
                )
            ),
        ],
    ) -> ProductAdapter:
        adapter = registry.get(product)
        if adapter is None or self.step not in adapter.supports():
            raise NotFound("This section is not available on this installation")
        if not await settings_service.product_enabled(product):
            raise NotFound("This section is not available on this installation")
        return adapter


@router.post(
    "/search/",
    summary="Start a search",
    dependencies=[Depends(RateLimit("search"))],
    openapi_extra=FLIGHT_SEARCH,
)
async def search(
    payload: dict[str, Any],
    session: SessionDep,
    adapter: Annotated[ProductAdapter, Depends(RequireProductStep(FlowStep.SEARCH))],
    _customer: OptionalCustomer,
    language: LanguageDep,
) -> dict[str, Any]:
    # ``language`` picks the language of ``fun_fact`` — our one addition to
    # the search passthrough (API.md §20). ``offers/`` takes no language: its
    # response is GTS's verbatim.
    return await service.search(session, adapter, payload, requested=language.requested)


@router.post(
    "/offers/",
    summary="Page through the offers of a search",
    dependencies=[Depends(RateLimit("search"))],
    openapi_extra=FLIGHT_OFFERS,
)
async def offers(
    payload: dict[str, Any],
    session: SessionDep,
    adapter: Annotated[ProductAdapter, Depends(RequireProductStep(FlowStep.OFFERS))],
    _customer: OptionalCustomer,
) -> dict[str, Any]:
    return await service.offers(session, adapter, payload)


@router.post(
    "/upsell/",
    summary="Fare variants of a chosen offer",
    dependencies=[Depends(RateLimit("search"))],
    openapi_extra=FLIGHT_UPSELL,
)
async def upsell(
    payload: dict[str, Any],
    session: SessionDep,
    adapter: Annotated[ProductAdapter, Depends(RequireProductStep(FlowStep.UPSELL))],
    _customer: OptionalCustomer,
) -> dict[str, Any]:
    return await service.upsell(session, adapter, payload)


@router.post(
    "/verify/",
    summary="Re-check price and availability of an offer",
    dependencies=[Depends(RateLimit("search"))],
    openapi_extra=FLIGHT_VERIFY,
)
async def verify(
    payload: dict[str, Any],
    session: SessionDep,
    adapter: Annotated[ProductAdapter, Depends(RequireProductStep(FlowStep.VERIFY))],
    _customer: OptionalCustomer,
) -> dict[str, Any]:
    return await service.verify(session, adapter, payload)


@router.post(
    "/booking/",
    status_code=201,
    summary="Book a chosen offer",
    dependencies=[Depends(RateLimit("payment"))],
    openapi_extra=FLIGHT_BOOKING,
)
async def booking(
    payload: dict[str, Any],
    session: SessionDep,
    # ``adapter`` before ``customer`` on purpose: a step that does not exist
    # here must 404 before anybody is asked to log in (API.md §41).
    adapter: Annotated[ProductAdapter, Depends(RequireProductStep(FlowStep.BOOKING))],
    customer: CurrentCustomer,
) -> BookingResultOut:
    return await service.book(session, adapter, payload, customer_id=customer.id)


__all__ = ["RequireProductStep", "router"]
