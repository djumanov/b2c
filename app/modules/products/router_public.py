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

**Auth is optional** (§20's ``(✓)``): the search form is public. A presented
but invalid token still 401s — ``OptionalCustomer`` forgives absence, not
garbage — and the search rate limit keys on the subject when there is one,
the IP otherwise.
"""

from typing import Annotated, Any

from fastapi import Depends

from app.api.deps import LanguageDep, OptionalCustomer, RateLimit
from app.api.envelope import enveloped_router
from app.api.errors import NotFound
from app.db.session import SessionDep
from app.modules.products import service
from app.modules.settings import service as settings_service
from app.providers.products.base import FlowStep, ProductAdapter, registry
from app.providers.products.flight import FlightAdapter

router = enveloped_router(prefix="/{product}", tags=["products"])

# One line per vertical, beside the router that serves it: the route cannot
# exist without its adapter, and phase 3 is four more of these lines.
registry.register(FlightAdapter())


class RequireProductStep:
    """Resolve ``{product}`` to an adapter or 404 — the vertical's gate.

    Registry first (no Redis round-trip for a code that cannot exist), the
    switched-off check second. The message matches ``RequireFeature``'s so
    the two gates are indistinguishable from outside.
    """

    __slots__ = ("step",)

    def __init__(self, step: FlowStep) -> None:
        self.step = step

    async def __call__(self, product: str) -> ProductAdapter:
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
)
async def verify(
    payload: dict[str, Any],
    session: SessionDep,
    adapter: Annotated[ProductAdapter, Depends(RequireProductStep(FlowStep.VERIFY))],
    _customer: OptionalCustomer,
) -> dict[str, Any]:
    return await service.verify(session, adapter, payload)


__all__ = ["RequireProductStep", "router"]
