"""``/public/content/*`` (API.md §24) — no auth, one language.

Only published rows. ``?lang=`` (or ``Accept-Language``) picks the language;
the response reports the one each text actually came back in (§7).
"""

from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import LanguageDep, RequireFeature
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.cms import service
from app.modules.cms.schemas import AboutPageOut, FaqPublicOut, PagePublicOut

faq_router = enveloped_router(
    prefix="/content/faq",
    tags=["content"],
    dependencies=[Depends(RequireFeature("faq"))],
)


@faq_router.get("/", summary="Published FAQ")
async def list_faq(
    session: SessionDep,
    language: LanguageDep,
    category: Annotated[str | None, Query(max_length=64)] = None,
) -> list[FaqPublicOut]:
    return await service.list_faq_public(
        session, requested=language.requested, category=category
    )


# --- static pages ----------------------------------------------------------------

# Each fixed page is its own route: the frontend and mobile devs read the
# contract off Swagger, and a `{slug}` parameter names none of the pages.

pages_router = enveloped_router(prefix="/content", tags=["content"])


@pages_router.get("/privacy-policy/", summary="Privacy policy, body in markdown")
async def get_privacy_policy(
    session: SessionDep, language: LanguageDep
) -> PagePublicOut:
    return await service.get_page_public(
        session, "privacy-policy", requested=language.requested
    )


@pages_router.get("/terms/", summary="Terms of use, body in markdown")
async def get_terms(session: SessionDep, language: LanguageDep) -> PagePublicOut:
    return await service.get_page_public(session, "terms", requested=language.requested)


@pages_router.get(
    "/about/", summary="About the company, body in markdown, plus contact details"
)
async def get_about(session: SessionDep, language: LanguageDep) -> AboutPageOut:
    return await service.get_about_page_public(session, requested=language.requested)


__all__ = ["faq_router", "pages_router"]
