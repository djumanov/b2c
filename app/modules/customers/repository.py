"""Queries over the three customer tables. Only ``service`` calls into here.

Emails are stored and looked up **lower-cased**, normalised by the service on
the way in — the same rule as ``staff``, and for the same reason: comparing with
``lower(email)`` would not use ``uq_customers_email_live``, and two live rows
for one address is two accounts for one person.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.mixins import utcnow
from app.db.repository import get_live, live
from app.modules.customers.models import (
    Customer,
    CustomerRefreshToken,
    EmailOtp,
    OtpPurpose,
)


async def by_email(session: AsyncSession, email: str) -> Customer | None:
    """A live account — verified or not, blocked or not. The caller decides."""
    row: Customer | None = await session.scalar(
        live(Customer).where(Customer.email == email)
    )
    return row


async def by_id(session: AsyncSession, customer_id: uuid.UUID) -> Customer | None:
    return await get_live(session, Customer, customer_id)


# --- refresh tokens ---------------------------------------------------------------


async def token_by_jti(session: AsyncSession, jti: str) -> CustomerRefreshToken | None:
    row: CustomerRefreshToken | None = await session.scalar(
        select(CustomerRefreshToken).where(CustomerRefreshToken.jti == jti)
    )
    return row


async def live_tokens_for(
    session: AsyncSession, customer_id: uuid.UUID
) -> Sequence[CustomerRefreshToken]:
    """Every session of one customer that has not been revoked yet."""
    rows = await session.scalars(
        select(CustomerRefreshToken)
        .where(CustomerRefreshToken.customer_id == customer_id)
        .where(CustomerRefreshToken.revoked_at.is_(None))
        .where(CustomerRefreshToken.expires_at > utcnow())
    )
    return rows.all()


# --- codes -------------------------------------------------------------------------


async def latest_otp(
    session: AsyncSession, customer_id: uuid.UUID, purpose: OtpPurpose
) -> EmailOtp | None:
    """The most recently issued code of this purpose, spent or not.

    Spent and expired rows come back too, because the resend cooldown is
    measured from the last one that was *sent* — hiding them would let a caller
    ask for a fresh code as fast as they liked by burning each one first.
    """
    row: EmailOtp | None = await session.scalar(
        select(EmailOtp)
        .where(EmailOtp.customer_id == customer_id)
        .where(EmailOtp.purpose == purpose)
        .order_by(EmailOtp.sent_at.desc())
        .limit(1)
    )
    return row


async def consume_open_otps(
    session: AsyncSession, customer_id: uuid.UUID, purpose: OtpPurpose
) -> None:
    """Mark every unspent code of this purpose as used.

    Issuing a code invalidates the one before it. Two live codes for the same
    address would double the chance of a guess landing and would let an old
    message keep working after the address holder asked for a new one.
    """
    rows = await session.scalars(
        select(EmailOtp)
        .where(EmailOtp.customer_id == customer_id)
        .where(EmailOtp.purpose == purpose)
        .where(EmailOtp.consumed_at.is_(None))
    )
    now = utcnow()
    for row in rows.all():
        row.consumed_at = now


__all__ = [
    "by_email",
    "by_id",
    "consume_open_otps",
    "latest_otp",
    "live_tokens_for",
    "token_by_jti",
]
