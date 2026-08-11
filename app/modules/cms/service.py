"""CMS content: panel CRUD and the public read that collapses languages.

The public list only ever sees published rows, in the order the panel chose.
Which languages the fallback chain may walk comes from the settings module —
asked through its service, never its models (ARCHITECTURE.md §4).
"""

import uuid
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import NotFound, ValidationFailed
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
from app.core import i18n
from app.core.logging import get_logger
from app.db.repository import get_live_or_404, live
from app.modules.cms.models import ContentStatus, Faq
from app.modules.cms.models import Page as PageModel
from app.modules.cms.schemas import (
    AboutPageOut,
    FaqAdminOut,
    FaqIn,
    FaqPublicOut,
    FaqUpdateIn,
    FixedPageIn,
    PageAdminOut,
    PagePublicOut,
    ReorderItemIn,
    SocialLinkOut,
)
from app.modules.settings import service as settings_service

logger = get_logger(__name__)

_FAQ_ORDERING: OrderingMap = {
    "order": Faq.sort_order,
    "created_at": Faq.created_at,
}


# --- faq, admin (API.md §30) -----------------------------------------------------


async def list_faq_admin(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    status: str | None = None,
    category: str | None = None,
) -> Page[FaqAdminOut]:
    stmt = live(Faq)
    if status is not None:
        stmt = stmt.where(Faq.status == status)
    if category is not None:
        stmt = stmt.where(Faq.category == category)
    # JSONB question/answer cannot take a plain ILIKE; the panel searches the
    # category code instead, which is what an operator actually types.
    stmt = apply_search(stmt, query, Faq.category)
    stmt = apply_created_range(stmt, query, Faq.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_FAQ_ORDERING, default="order", tiebreak=Faq.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([FaqAdminOut.model_validate(row) for row in rows], pagination, total)


async def create_faq(session: AsyncSession, data: FaqIn) -> FaqAdminOut:
    # New entries join at the end of the panel's order.
    last = await session.scalar(
        select(func.max(Faq.sort_order)).where(Faq.deleted_at.is_(None))
    )
    faq = Faq(
        question=data.question,
        answer=data.answer,
        category=(data.category or "").strip() or None,
        status=ContentStatus.DRAFT,
        sort_order=(last if last is not None else -1) + 1,
    )
    session.add(faq)
    await session.commit()
    await session.refresh(faq)
    logger.info("faq_created", faq_id=str(faq.id))
    return FaqAdminOut.model_validate(faq)


async def get_faq(session: AsyncSession, faq_id: uuid.UUID) -> FaqAdminOut:
    return FaqAdminOut.model_validate(await _require(session, faq_id))


async def update_faq(
    session: AsyncSession, faq_id: uuid.UUID, data: FaqUpdateIn
) -> FaqAdminOut:
    faq = await _require(session, faq_id)
    # Translated fields merge per language, so one language can be edited
    # without resending the others (the same PATCH semantics as settings).
    if data.question is not None:
        faq.question = {**faq.question, **data.question}
    if data.answer is not None:
        faq.answer = {**faq.answer, **data.answer}
    if data.category is not None:
        faq.category = data.category.strip() or None
    await session.commit()
    await session.refresh(faq)
    return FaqAdminOut.model_validate(faq)


async def delete_faq(session: AsyncSession, faq_id: uuid.UUID) -> None:
    faq = await _require(session, faq_id)
    faq.soft_delete()
    await session.commit()
    logger.info("faq_deleted", faq_id=str(faq.id))


async def set_faq_status(
    session: AsyncSession, faq_id: uuid.UUID, status: ContentStatus
) -> FaqAdminOut:
    """Publish or unpublish — idempotent, like the flag it flips."""
    faq = await _require(session, faq_id)
    faq.status = status
    await session.commit()
    await session.refresh(faq)
    logger.info("faq_status_set", faq_id=str(faq.id), status=str(status))
    return FaqAdminOut.model_validate(faq)


async def reorder_faq(session: AsyncSession, items: list[ReorderItemIn]) -> None:
    """Apply the panel's order in one go (API.md §30, ``[{id, order}]``)."""
    rows = {faq.id: faq for faq in (await session.scalars(live(Faq))).all()}
    missing = [str(item.id) for item in items if item.id not in rows]
    if missing:
        raise NotFound(f"FAQ entry not found: {', '.join(missing)}")
    for item in items:
        rows[item.id].sort_order = item.order
    await session.commit()


async def _require(session: AsyncSession, faq_id: uuid.UUID) -> Faq:
    return await get_live_or_404(session, Faq, faq_id, name="FAQ entry")


# --- faq, public (API.md §24) ----------------------------------------------------


async def list_faq_public(
    session: AsyncSession,
    *,
    requested: str | None,
    category: str | None = None,
) -> list[FaqPublicOut]:
    languages = await settings_service.get_languages(session)
    stmt = live(Faq).where(Faq.status == ContentStatus.PUBLISHED)
    if category is not None:
        stmt = stmt.where(Faq.category == category)
    stmt = stmt.order_by(Faq.sort_order.asc(), Faq.id.asc())
    rows = (await session.scalars(stmt)).all()

    items: list[FaqPublicOut] = []
    for row in rows:
        question = i18n.resolve(
            row.question,
            requested=requested,
            default=languages.default,
            available=languages.available,
        )
        answer = i18n.resolve_value(
            row.answer,
            requested=requested,
            default=languages.default,
            available=languages.available,
        )
        if question.value is None or answer is None:
            # Nothing readable in any language — publishing guarantees at
            # least one, but a row this empty is better absent than blank.
            continue
        items.append(
            FaqPublicOut(
                id=row.id,
                question=question.value,
                answer=answer,
                category=row.category,
                # The primary field names the language of the whole item.
                lang=question.lang,
            )
        )
    return items


# --- pages, admin (API.md §30) ---------------------------------------------------

#: The whole set of static pages. Each has its own route on both surfaces so
#: Swagger names them outright — nothing else ever reaches the ``pages`` table.
FIXED_PAGE_SLUGS: Final = ("privacy-policy", "terms", "about")


async def get_fixed_page_admin(session: AsyncSession, slug: str) -> PageAdminOut:
    return PageAdminOut.model_validate(await _require_page(session, slug))


async def upsert_fixed_page(
    session: AsyncSession, slug: str, data: FixedPageIn
) -> PageAdminOut:
    """The first ``PUT`` creates the draft; later ones merge languages.

    The merge is per language, same as the FAQ PATCH — one language can be
    edited without resending the others.
    """
    row = await session.scalar(live(PageModel).where(PageModel.slug == slug))
    if row is None:
        if data.title is None and data.body is None:
            # ``FixedPageIn`` guarantees a sent field has a value in at least
            # one language; a first PUT with neither has nothing to create.
            raise ValidationFailed("A new page needs a title or a body")
        row = PageModel(
            slug=slug,
            title=data.title or {},
            body=data.body or {},
            status=ContentStatus.DRAFT,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        logger.info("page_created", page_id=str(row.id), slug=slug)
        return PageAdminOut.model_validate(row)

    if data.title is not None:
        row.title = {**row.title, **data.title}
    if data.body is not None:
        row.body = {**row.body, **data.body}
    await session.commit()
    await session.refresh(row)
    logger.info("page_updated", page_id=str(row.id), slug=slug)
    return PageAdminOut.model_validate(row)


async def set_fixed_page_status(
    session: AsyncSession, slug: str, status: ContentStatus
) -> PageAdminOut:
    """Publish or unpublish — idempotent, like the flag it flips."""
    row = await _require_page(session, slug)
    row.status = status
    await session.commit()
    await session.refresh(row)
    logger.info("page_status_set", page_id=str(row.id), slug=slug, status=str(status))
    return PageAdminOut.model_validate(row)


async def _require_page(session: AsyncSession, slug: str) -> PageModel:
    row = await session.scalar(live(PageModel).where(PageModel.slug == slug))
    if row is None:
        # Never written yet — the panel shows an empty editor, not an error
        # page, but the API shape is the ordinary 404 (API.md §41).
        raise NotFound("Page not found")
    return row


# --- pages, public (API.md §24) --------------------------------------------------


async def get_page_public(
    session: AsyncSession, slug: str, *, requested: str | None
) -> PagePublicOut:
    row = await session.scalar(live(PageModel).where(PageModel.slug == slug))
    # Missing and unpublished answer the same 404 — a draft's existence is
    # nobody's business (the shape API.md §41 gives every absent thing).
    if row is None or row.status != ContentStatus.PUBLISHED:
        raise NotFound("Page not found")

    languages = await settings_service.get_languages(session)
    title = i18n.resolve(
        row.title,
        requested=requested,
        default=languages.default,
        available=languages.available,
    )
    body = i18n.resolve_value(
        row.body,
        requested=requested,
        default=languages.default,
        available=languages.available,
    )
    if title.value is None or body is None:
        raise NotFound("Page not found")
    return PagePublicOut(
        slug=row.slug,
        title=title.value,
        body=body,
        lang=title.lang,
        updated_at=row.updated_at,
    )


async def get_about_page_public(
    session: AsyncSession, *, requested: str | None
) -> AboutPageOut:
    """The company contact details, not the "about" page's text (API.md §24, §30).

    Company name, email, website, phone and social links are not page
    content — the admin enters them once in ``/admin/settings/site/`` and
    this asks the settings module for the answer rather than keeping its own
    copy (ARCHITECTURE.md §4). ``get_page_public`` is still called to gate on
    the "about" page's publish state (404 when unpublished) — its title and
    body are discarded, they are not part of this response.
    """
    await get_page_public(session, "about", requested=requested)
    site = await settings_service.get_site(session)
    languages = await settings_service.get_languages(session)
    company_name = i18n.resolve_value(
        site.name,
        requested=requested,
        default=languages.default,
        available=languages.available,
    )
    return AboutPageOut(
        company_name=company_name,
        company_email=site.support_email,
        company_website=site.domain,
        company_phone=site.support_phone,
        social_media=[
            SocialLinkOut(name=name, link=link) for name, link in site.social.items()
        ],
    )


__all__ = [
    "FIXED_PAGE_SLUGS",
    "create_faq",
    "delete_faq",
    "get_about_page_public",
    "get_faq",
    "get_fixed_page_admin",
    "get_page_public",
    "list_faq_admin",
    "list_faq_public",
    "reorder_faq",
    "set_faq_status",
    "set_fixed_page_status",
    "update_faq",
    "upsert_fixed_page",
]
