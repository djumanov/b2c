"""Leads: accept a message, let the panel triage it.

The whole answer happens outside the system — the operator calls or writes
back over whatever channel the ``contact`` names. The service therefore has
exactly two jobs: keep the message, and keep the panel's working state on it.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
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
from app.modules.leads.models import Lead, SupportTopic
from app.modules.leads.schemas import (
    LeadAdminOut,
    LeadCreatedOut,
    LeadCreateIn,
    LeadUpdateIn,
    SupportTopicAdminOut,
    SupportTopicIn,
    SupportTopicPublicOut,
    SupportTopicUpdateIn,
)
from app.modules.settings import service as settings_service

logger = get_logger(__name__)

_LEAD_ORDERING: OrderingMap = {
    "created_at": Lead.created_at,
    "status": Lead.status,
}

_TOPIC_ORDERING: OrderingMap = {
    "order": SupportTopic.sort_order,
    "created_at": SupportTopic.created_at,
}


async def create_lead(
    session: AsyncSession, data: LeadCreateIn, *, customer_id: uuid.UUID | None
) -> LeadCreatedOut:
    lead = Lead(
        topic=data.topic.strip(),
        name=(data.name or "").strip() or None,
        contact=data.contact.strip(),
        message=data.message.strip(),
        customer_id=customer_id,
    )
    session.add(lead)
    await session.commit()
    await session.refresh(lead)
    logger.info("lead_created", lead_id=str(lead.id), topic=lead.topic)
    return LeadCreatedOut.model_validate(lead)


async def list_leads(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    status: str | None = None,
) -> Page[LeadAdminOut]:
    stmt = live(Lead)
    if status is not None:
        stmt = stmt.where(Lead.status == status)
    stmt = apply_search(stmt, query, Lead.topic, Lead.name, Lead.contact)
    stmt = apply_created_range(stmt, query, Lead.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_LEAD_ORDERING, default="-created_at", tiebreak=Lead.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([LeadAdminOut.model_validate(row) for row in rows], pagination, total)


async def get_lead(session: AsyncSession, lead_id: uuid.UUID) -> LeadAdminOut:
    return LeadAdminOut.model_validate(await _require(session, lead_id))


async def update_lead(
    session: AsyncSession, lead_id: uuid.UUID, data: LeadUpdateIn
) -> LeadAdminOut:
    lead = await _require(session, lead_id)
    if data.status is not None:
        lead.status = data.status
    if data.note is not None:
        lead.note = data.note.strip() or None
    await session.commit()
    await session.refresh(lead)
    return LeadAdminOut.model_validate(lead)


async def _require(session: AsyncSession, lead_id: uuid.UUID) -> Lead:
    return await get_live_or_404(session, Lead, lead_id, name="Lead")


# --- support topics, admin (API.md §35) -------------------------------------------


async def list_topics_admin(
    session: AsyncSession, pagination: Pagination, query: ListQuery
) -> Page[SupportTopicAdminOut]:
    # No ``search``: the only text is JSONB, which cannot take a plain ILIKE —
    # the same reason the deletion-reasons panel goes without one.
    stmt = live(SupportTopic)
    stmt = apply_created_range(stmt, query, SupportTopic.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_TOPIC_ORDERING, default="order", tiebreak=SupportTopic.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page(
        [SupportTopicAdminOut.model_validate(row) for row in rows], pagination, total
    )


async def create_topic(
    session: AsyncSession, data: SupportTopicIn
) -> SupportTopicAdminOut:
    if data.sort_order is None:
        # New entries join at the end of the panel's order.
        last = await session.scalar(
            select(func.max(SupportTopic.sort_order)).where(
                SupportTopic.deleted_at.is_(None)
            )
        )
        sort_order = (last if last is not None else -1) + 1
    else:
        sort_order = data.sort_order
    topic = SupportTopic(name=data.name, sort_order=sort_order)
    session.add(topic)
    await session.commit()
    await session.refresh(topic)
    logger.info("support_topic_created", topic_id=str(topic.id))
    return SupportTopicAdminOut.model_validate(topic)


async def get_topic(session: AsyncSession, topic_id: uuid.UUID) -> SupportTopicAdminOut:
    return SupportTopicAdminOut.model_validate(await _require_topic(session, topic_id))


async def update_topic(
    session: AsyncSession, topic_id: uuid.UUID, data: SupportTopicUpdateIn
) -> SupportTopicAdminOut:
    topic = await _require_topic(session, topic_id)
    # Translated fields merge per language, so one language can be edited
    # without resending the others (the same PATCH semantics as the cms).
    if data.name is not None:
        topic.name = {**topic.name, **data.name}
    if data.sort_order is not None:
        topic.sort_order = data.sort_order
    await session.commit()
    await session.refresh(topic)
    return SupportTopicAdminOut.model_validate(topic)


async def delete_topic(session: AsyncSession, topic_id: uuid.UUID) -> None:
    topic = await _require_topic(session, topic_id)
    topic.soft_delete()
    await session.commit()
    logger.info("support_topic_deleted", topic_id=str(topic.id))


async def _require_topic(session: AsyncSession, topic_id: uuid.UUID) -> SupportTopic:
    return await get_live_or_404(session, SupportTopic, topic_id, name="Topic")


# --- support topics, public (API.md §25) ------------------------------------------


async def list_topics_public(
    session: AsyncSession, *, requested: str | None
) -> list[SupportTopicPublicOut]:
    languages = await settings_service.get_languages(session)
    stmt = live(SupportTopic).order_by(
        SupportTopic.sort_order.asc(), SupportTopic.id.asc()
    )
    rows = (await session.scalars(stmt)).all()

    items: list[SupportTopicPublicOut] = []
    for row in rows:
        resolved = i18n.resolve(
            row.name,
            requested=requested,
            default=languages.default,
            available=languages.available,
        )
        if resolved.value is None:
            # Nothing readable in any language — better absent than blank.
            continue
        items.append(
            SupportTopicPublicOut(id=row.id, name=resolved.value, lang=resolved.lang)
        )
    return items


__all__ = [
    "create_lead",
    "create_topic",
    "delete_topic",
    "get_lead",
    "get_topic",
    "list_leads",
    "list_topics_admin",
    "list_topics_public",
    "update_lead",
    "update_topic",
]
