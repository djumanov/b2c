"""Reading the integration rows. No soft delete here, so no ``deleted_at``.

Ordering is deliberate and not a detail: the active GTS account leads, then the
rest by label, and payment providers come in the order the site will show them.
A panel list that reorders itself between two reads is a list nobody trusts.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations.models import (
    GtsCredential,
    PaymentProvider,
    SmtpSettings,
    SocialCredential,
)
from app.providers.payments.base import PaymentProviderCode
from app.providers.social.base import SocialProviderCode

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


async def smtp(session: AsyncSession) -> SmtpSettings:
    """The SMTP row, created on first read.

    ``ON CONFLICT DO NOTHING`` then a read, rather than catching an integrity
    error: two workers can answer the very first request at the same moment,
    and a rollback here would take the caller's own work with it. Same shape as
    ``settings/repository.py::_singleton``, same reasoning.
    """
    row: SmtpSettings | None = await session.scalar(select(SmtpSettings).limit(1))
    if row is not None:
        return row

    await session.execute(
        pg_insert(SmtpSettings)
        .values(id=uuid.uuid4(), singleton=True)
        .on_conflict_do_nothing()
    )
    created: SmtpSettings | None = await session.scalar(select(SmtpSettings).limit(1))
    if created is None:  # pragma: no cover - the insert or a peer just made it
        raise RuntimeError("smtp_settings could not be initialised")
    return created


#: Panel order, and the same order the site gets (API.md §17). ``code`` breaks
#: the tie so a list with two providers on the same rank does not shuffle
#: itself between two reads.
_PROVIDERS_ORDERED = select(PaymentProvider).order_by(
    PaymentProvider.sort_order, PaymentProvider.code
)


async def payment_providers(session: AsyncSession) -> list[PaymentProvider]:
    """Every provider the release supports, creating any that are missing.

    The set comes from ``PaymentProviderCode``, so a release that adds an
    adapter gets its row on the next read rather than in a data migration —
    and an installation that upgrades finds the new provider already listed,
    switched off.

    ``ON CONFLICT DO NOTHING`` for the same reason as ``smtp()``: two workers
    can answer the very first request at once, and a rollback here would take
    the caller's own work with it.
    """
    missing = set(PaymentProviderCode) - {
        row.code for row in (await session.scalars(_PROVIDERS_ORDERED)).all()
    }
    if missing:
        order = list(PaymentProviderCode)
        await session.execute(
            pg_insert(PaymentProvider)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "code": code.value,
                        # A starting name the client can rewrite. Capitalised
                        # rather than left empty: an unnamed button is worse
                        # than a wrongly-named one.
                        "title": code.value.capitalize(),
                        # Declaration order, in tens so a later provider can be
                        # slotted between two without renumbering. The default
                        # order is then the release's own rather than
                        # alphabetical, which would be an arbitrary answer to a
                        # question the client had not yet been asked.
                        "sort_order": order.index(code) * 10,
                    }
                    for code in sorted(missing, key=order.index)
                ]
            )
            .on_conflict_do_nothing()
        )
    return list((await session.scalars(_PROVIDERS_ORDERED)).all())


async def payment_provider(
    session: AsyncSession, code: PaymentProviderCode
) -> PaymentProvider | None:
    row: PaymentProvider | None = await session.scalar(
        select(PaymentProvider).where(PaymentProvider.code == code)
    )
    return row


_SOCIAL_ORDERED = select(SocialCredential).order_by(SocialCredential.provider)


async def social_credentials(session: AsyncSession) -> list[SocialCredential]:
    """Every social provider the release supports, creating any that are missing.

    Same seeding rule as ``payment_providers``, and for the same reason: the
    set comes from the code, so an installation that upgrades finds a newly
    supported provider already listed and switched off.
    """
    missing = set(SocialProviderCode) - {
        row.provider for row in (await session.scalars(_SOCIAL_ORDERED)).all()
    }
    if missing:
        await session.execute(
            pg_insert(SocialCredential)
            .values(
                [
                    {"id": uuid.uuid4(), "provider": provider.value}
                    for provider in sorted(missing)
                ]
            )
            .on_conflict_do_nothing()
        )
    return list((await session.scalars(_SOCIAL_ORDERED)).all())


async def social_credential(
    session: AsyncSession, provider: SocialProviderCode
) -> SocialCredential | None:
    row: SocialCredential | None = await session.scalar(
        select(SocialCredential).where(SocialCredential.provider == provider)
    )
    return row


__all__ = [
    "active",
    "all_credentials",
    "by_id",
    "count",
    "deactivate_all",
    "label_taken",
    "payment_provider",
    "payment_providers",
    "smtp",
    "social_credential",
    "social_credentials",
]
