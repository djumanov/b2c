"""Queries over the two staff tables. Only ``service`` calls into here.

Emails are stored and looked up **lower-cased**, normalised by the service on
the way in. The alternative — comparing with ``lower(email)`` — would not use
``uq_staff_email_live`` and would let ``Aziz@x.uz`` and ``aziz@x.uz`` both
exist as live rows, which is two accounts for one person.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mixins import utcnow
from app.db.repository import exists_live, get_live, live
from app.modules.staff.models import Staff, StaffRefreshToken


async def by_email(session: AsyncSession, email: str) -> Staff | None:
    """A live account, blocked or not — the caller decides what that means."""
    row: Staff | None = await session.scalar(live(Staff).where(Staff.email == email))
    return row


async def by_id(session: AsyncSession, staff_id: uuid.UUID) -> Staff | None:
    return await get_live(session, Staff, staff_id)


async def email_taken(
    session: AsyncSession, email: str, *, excluding: uuid.UUID | None = None
) -> bool:
    criteria = [Staff.email == email]
    if excluding is not None:
        criteria.append(Staff.id != excluding)
    return await exists_live(session, Staff, *criteria)


async def any_exists(session: AsyncSession) -> bool:
    """Whether the installation has been bootstrapped (``docker/bootstrap.py``).

    Deleted rows count. An installation whose only owner was soft-deleted has
    still been set up, and re-running the bootstrap would silently hand the
    environment's password a fresh account on a live system.
    """
    return await session.scalar(select(Staff.id).limit(1)) is not None


async def other_active_owners(
    session: AsyncSession, *, excluding: uuid.UUID, role_value: str
) -> bool:
    """Is there another owner who could still administer the installation?"""
    stmt = (
        live(Staff)
        .where(Staff.id != excluding)
        .where(Staff.role == role_value)
        .where(Staff.is_blocked.is_(False))
        .limit(1)
    )
    return await session.scalar(stmt) is not None


# --- refresh tokens ---------------------------------------------------------------


async def token_by_jti(session: AsyncSession, jti: str) -> StaffRefreshToken | None:
    row: StaffRefreshToken | None = await session.scalar(
        select(StaffRefreshToken).where(StaffRefreshToken.jti == jti)
    )
    return row


async def live_tokens_for(
    session: AsyncSession, staff_id: uuid.UUID
) -> Sequence[StaffRefreshToken]:
    """Every session of one employee that has not been revoked yet."""
    rows = await session.scalars(
        select(StaffRefreshToken)
        .where(StaffRefreshToken.staff_id == staff_id)
        .where(StaffRefreshToken.revoked_at.is_(None))
        .where(StaffRefreshToken.expires_at > utcnow())
    )
    return rows.all()


__all__ = [
    "any_exists",
    "by_email",
    "by_id",
    "email_taken",
    "live_tokens_for",
    "other_active_owners",
    "token_by_jti",
]
