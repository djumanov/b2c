"""``/public/content/*`` (API.md §24) — no auth, one language.

Only published rows. ``?lang=`` (or ``Accept-Language``) picks the language;
the response reports the one each text actually came back in (§7).
"""

from typing import Annotated

from fastapi import Depends, Path, Query

from app.api.deps import LanguageDep, RequireFeature
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.cms import service
from app.modules.cms.schemas import SLUG_PATTERN, FaqPublicOut, PagePublicOut

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

pages_router = enveloped_router(prefix="/content/pages", tags=["content"])


@pages_router.get("/{slug}/", summary="A published static page, body in markdown")
async def get_page(
    slug: Annotated[str, Path(max_length=160, pattern=SLUG_PATTERN)],
    session: SessionDep,
    language: LanguageDep,
) -> PagePublicOut:
    return await service.get_page_public(session, slug, requested=language.requested)


__all__ = ["faq_router", "pages_router"]
