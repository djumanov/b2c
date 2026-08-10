"""``/public/content/faq/`` (API.md §24) — no auth, one language.

Only published rows, in the panel's order. ``?lang=`` (or ``Accept-Language``)
picks the language; each item reports the one it actually came back in (§7).
"""

from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import LanguageDep, RequireFeature
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.cms import service
from app.modules.cms.schemas import FaqPublicOut

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


__all__ = ["faq_router"]
