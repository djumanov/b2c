"""``/admin/settings/*`` — API.md §28.

Both roles write here: the settings row of API.md §5 gives ``admin`` ✎, because
changing a colour is day-to-day work, not administration of the installation.

``settings/menu/`` is absent, not stubbed. It is out of the first release
(API.md §41) because the menu model is still an open question (PROJECT.md §16),
and a route that does not exist answers `404` on its own.
"""

from fastapi import Depends, Response

from app.api.deps import current_staff
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.audit.deps import Audited
from app.modules.settings import service
from app.modules.settings.schemas import (
    BrandingIn,
    BrandingOut,
    CurrenciesIn,
    CurrenciesOut,
    FeaturesIn,
    FeaturesOut,
    LanguagesIn,
    LanguagesOut,
    ProductOut,
    SiteIn,
    SiteOut,
)

router = enveloped_router(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(current_staff)],
)


@router.get("/branding/", summary="Logo, favicon, colours, font")
async def get_branding(session: SessionDep) -> BrandingOut:
    return await service.get_branding(session)


@router.patch("/branding/", summary="Change branding — no deploy follows")
async def update_branding(data: BrandingIn, session: SessionDep) -> BrandingOut:
    return await service.update_branding(session, data)


@router.get("/site/", summary="Name, domain, contacts, social links")
async def get_site(session: SessionDep) -> SiteOut:
    return await service.get_site(session)


@router.patch("/site/", summary="Change the site's own details")
async def update_site(data: SiteIn, session: SessionDep) -> SiteOut:
    return await service.update_site(session, data)


@router.get("/languages/", summary="Default and available languages")
async def get_languages(session: SessionDep) -> LanguagesOut:
    return await service.get_languages(session)


@router.patch("/languages/", summary="Change which languages the site serves")
async def update_languages(data: LanguagesIn, session: SessionDep) -> LanguagesOut:
    return await service.update_languages(session, data)


@router.get("/currencies/", summary="Base and displayed currencies")
async def get_currencies(session: SessionDep) -> CurrenciesOut:
    return await service.get_currencies(session)


@router.patch("/currencies/", summary="Change the currencies on offer")
async def update_currencies(data: CurrenciesIn, session: SessionDep) -> CurrenciesOut:
    return await service.update_currencies(session, data)


@router.get("/features/", summary="Which sections are switched on")
async def get_features(session: SessionDep) -> FeaturesOut:
    return await service.get_features(session)


@router.patch("/features/", summary="Switch a section on or off")
async def update_features(data: FeaturesIn, session: SessionDep) -> FeaturesOut:
    return await service.update_features(session, data)


@router.get(
    "/products/",
    summary="Which verticals are on sale — read only",
)
async def get_products(session: SessionDep) -> list[ProductOut]:
    """Read-only by contract: what an installation may sell follows from the
    client's GTS agreement, not from a switch in the panel (API.md §28)."""
    return await service.get_products(session)


@router.post(
    "/cache/purge/",
    status_code=204,
    # The route's verb comes before any identifier, which is the one shape the
    # audit middleware reads wrongly — so it says so itself.
    dependencies=[Depends(Audited("settings.cache", "purge"))],
    summary="Drop the cached site-config",
)
async def purge_cache() -> Response:
    await service.purge_cache()
    return Response(status_code=204)


__all__ = ["router"]
