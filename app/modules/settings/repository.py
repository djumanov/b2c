"""Reading the singletons, creating them the first time anybody asks.

A fresh installation has no settings rows. Rather than seed them in a
migration — which freezes the defaults into a revision that can never be
edited — each is created on first read, from the values in ``defaults``.

The insert is ``ON CONFLICT DO NOTHING`` followed by a read. Two workers can
answer the very first request at the same moment, and the alternative,
catching an integrity error, would mean rolling back a transaction that may
already hold the caller's own work.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.modules.settings import defaults
from app.modules.settings.models import (
    Branding,
    Currencies,
    Features,
    Languages,
    ProductSetting,
    Site,
)
from app.providers.products.base import ProductCode


async def _singleton[ModelT: Base](
    session: AsyncSession, model: type[ModelT], values: dict[str, Any]
) -> ModelT:
    row: ModelT | None = await session.scalar(select(model).limit(1))
    if row is not None:
        return row

    await session.execute(
        pg_insert(model)
        .values(id=uuid.uuid4(), singleton=True, **values)
        .on_conflict_do_nothing()
    )
    created: ModelT | None = await session.scalar(select(model).limit(1))
    if created is None:  # pragma: no cover - the insert or a peer just made it
        raise RuntimeError(f"{model.__tablename__} could not be initialised")
    return created


async def branding(session: AsyncSession) -> Branding:
    return await _singleton(
        session,
        Branding,
        {"colors": dict(defaults.DEFAULT_COLORS), "font_family": defaults.DEFAULT_FONT},
    )


async def site(session: AsyncSession) -> Site:
    return await _singleton(session, Site, {"name": {}, "social": {}})


async def languages(session: AsyncSession) -> Languages:
    return await _singleton(
        session,
        Languages,
        {
            "default": defaults.DEFAULT_LANGUAGE,
            "available": list(defaults.DEFAULT_LANGUAGES),
        },
    )


async def currencies(session: AsyncSession) -> Currencies:
    return await _singleton(
        session,
        Currencies,
        {
            "default": defaults.DEFAULT_CURRENCY,
            "available": list(defaults.DEFAULT_CURRENCIES),
        },
    )


async def features(session: AsyncSession) -> Features:
    return await _singleton(
        session, Features, {"flags": dict(defaults.FEATURE_DEFAULTS)}
    )


async def products(session: AsyncSession) -> list[ProductSetting]:
    """The five verticals, seeded on first read (PROJECT.md §8, D9).

    All enabled: which ones an installation may actually sell comes from the
    client's GTS contract, and there is no GTS to ask yet (ARCHITECTURE.md A5).
    """
    ordered = select(ProductSetting).order_by(ProductSetting.position)
    rows = list((await session.scalars(ordered)).all())
    if len(rows) == len(ProductCode):
        return rows

    await session.execute(
        pg_insert(ProductSetting)
        .values(
            [
                {"id": uuid.uuid4(), "code": code.value, "position": position}
                for position, code in enumerate(ProductCode)
            ]
        )
        .on_conflict_do_nothing(index_elements=["code"])
    )
    return list((await session.scalars(ordered)).all())


__all__ = [
    "branding",
    "currencies",
    "features",
    "languages",
    "products",
    "site",
]
