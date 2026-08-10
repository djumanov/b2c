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
from app.api.errors import NotFound
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
from app.modules.cms.schemas import (
    FaqAdminOut,
    FaqIn,
    FaqPublicOut,
    FaqUpdateIn,
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


__all__ = [
    "create_faq",
    "delete_faq",
    "get_faq",
    "list_faq_admin",
    "list_faq_public",
    "reorder_faq",
    "set_faq_status",
    "update_faq",
]
