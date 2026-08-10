"""CMS content: panel CRUD and the public read that collapses languages.

The public list only ever sees published rows, in the order the panel chose.
Which languages the fallback chain may walk comes from the settings module —
asked through its service, never its models (ARCHITECTURE.md §4).
"""

import uuid

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
from app.db.repository import exists_live, get_live_or_404, live
from app.modules.cms.models import ContentStatus, Faq
from app.modules.cms.models import Page as PageModel
from app.modules.cms.schemas import (
    FaqAdminOut,
    FaqIn,
    FaqPublicOut,
    FaqUpdateIn,
    PageAdminOut,
    PageIn,
    PagePublicOut,
    PageUpdateIn,
    ReorderItemIn,
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

_PAGE_ORDERING: OrderingMap = {
    "slug": PageModel.slug,
    "created_at": PageModel.created_at,
}


async def _require_free_slug(session: AsyncSession, slug: str) -> None:
    """A 422 naming the field; the partial unique index is the guarantee."""
    if await exists_live(session, PageModel, PageModel.slug == slug):
        raise ValidationFailed(
            f"A page with slug {slug!r} already exists", field="slug"
        )


async def list_pages_admin(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    status: str | None = None,
) -> Page[PageAdminOut]:
    stmt = live(PageModel)
    if status is not None:
        stmt = stmt.where(PageModel.status == status)
    stmt = apply_search(stmt, query, PageModel.slug)
    stmt = apply_created_range(stmt, query, PageModel.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_PAGE_ORDERING, default="slug", tiebreak=PageModel.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([PageAdminOut.model_validate(row) for row in rows], pagination, total)


async def create_page(session: AsyncSession, data: PageIn) -> PageAdminOut:
    await _require_free_slug(session, data.slug)
    row = PageModel(
        slug=data.slug,
        title=data.title,
        body=data.body,
        status=ContentStatus.DRAFT,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("page_created", page_id=str(row.id), slug=row.slug)
    return PageAdminOut.model_validate(row)


async def get_page(session: AsyncSession, page_id: uuid.UUID) -> PageAdminOut:
    return PageAdminOut.model_validate(await _require_page(session, page_id))


async def update_page(
    session: AsyncSession, page_id: uuid.UUID, data: PageUpdateIn
) -> PageAdminOut:
    row = await _require_page(session, page_id)
    if data.slug is not None and data.slug != row.slug:
        await _require_free_slug(session, data.slug)
        row.slug = data.slug
    # Translated fields merge per language, same as the FAQ.
    if data.title is not None:
        row.title = {**row.title, **data.title}
    if data.body is not None:
        row.body = {**row.body, **data.body}
    await session.commit()
    await session.refresh(row)
    return PageAdminOut.model_validate(row)


async def delete_page(session: AsyncSession, page_id: uuid.UUID) -> None:
    row = await _require_page(session, page_id)
    row.soft_delete()
    await session.commit()
    logger.info("page_deleted", page_id=str(row.id), slug=row.slug)


async def set_page_status(
    session: AsyncSession, page_id: uuid.UUID, status: ContentStatus
) -> PageAdminOut:
    row = await _require_page(session, page_id)
    row.status = status
    await session.commit()
    await session.refresh(row)
    logger.info("page_status_set", page_id=str(row.id), status=str(status))
    return PageAdminOut.model_validate(row)


async def _require_page(session: AsyncSession, page_id: uuid.UUID) -> PageModel:
    return await get_live_or_404(session, PageModel, page_id, name="Page")


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


__all__ = [
    "create_faq",
    "create_page",
    "delete_faq",
    "delete_page",
    "get_faq",
    "get_page",
    "get_page_public",
    "list_faq_admin",
    "list_faq_public",
    "list_pages_admin",
    "reorder_faq",
    "set_faq_status",
    "set_page_status",
    "update_faq",
    "update_page",
]
