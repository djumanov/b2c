"""Leads: accept a message, let the panel triage it.

The whole answer happens outside the system — the operator calls or writes
back over whatever channel the ``contact`` names. The service therefore has
exactly two jobs: keep the message, and keep the panel's working state on it.
"""

import uuid

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
from app.core.logging import get_logger
from app.db.repository import get_live_or_404, live
from app.modules.leads.models import Lead
from app.modules.leads.schemas import (
    LeadAdminOut,
    LeadCreatedOut,
    LeadCreateIn,
    LeadUpdateIn,
)

logger = get_logger(__name__)

_LEAD_ORDERING: OrderingMap = {
    "created_at": Lead.created_at,
    "status": Lead.status,
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


__all__ = ["create_lead", "get_lead", "list_leads", "update_lead"]
