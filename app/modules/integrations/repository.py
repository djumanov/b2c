"""Reading GTS credentials. No soft delete here, so no ``deleted_at`` filter.

Ordering is deliberate and not a detail: the active account leads, then the
rest by label. A panel list that reorders itself between two reads is a list
nobody trusts.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations.models import GtsCredential

_ORDERED = select(GtsCredential).order_by(
    GtsCredential.is_active.desc(), GtsCredential.label
)


async def all_credentials(session: AsyncSession) -> list[GtsCredential]:
    return list((await session.scalars(_ORDERED)).all())


async def by_id(
    session: AsyncSession, credential_id: uuid.UUID
) -> GtsCredential | None:
    return await session.get(GtsCredential, credential_id)


async def active(session: AsyncSession) -> GtsCredential | None:
    row: GtsCredential | None = await session.scalar(
        select(GtsCredential).where(GtsCredential.is_active).limit(1)
    )
    return row


async def count(session: AsyncSession) -> int:
    total = await session.scalar(select(func.count()).select_from(GtsCredential))
    return total or 0


async def label_taken(
    session: AsyncSession, label: str, *, excluding: uuid.UUID | None = None
) -> bool:
    query = select(GtsCredential.id).where(GtsCredential.label == label)
    if excluding is not None:
        query = query.where(GtsCredential.id != excluding)
    return await session.scalar(query.limit(1)) is not None


async def deactivate_all(session: AsyncSession) -> None:
    """Clear the flag on every row, ready for one of them to be set again."""
    for row in await session.scalars(
        select(GtsCredential).where(GtsCredential.is_active)
    ):
        row.is_active = False
    # Flushed by the caller — the order of the two UPDATEs is what keeps the
    # partial unique index happy, and only the caller knows both halves.


__all__ = [
    "active",
    "all_credentials",
    "by_id",
    "count",
    "deactivate_all",
    "label_taken",
]
