"""Soft-delete-aware reads, spelled once.

Deleting is soft by default (API.md §8), so every ordinary read carries
``WHERE deleted_at IS NULL``. Written by hand at each call site it gets
forgotten exactly once — and the endpoint that forgets it serves deleted rows
to a client, which is the kind of bug that is found by a customer rather than a
test. These four helpers are the one spelling.

They are deliberately thin. A module with a real query keeps it in its own
``repository.py``; this is the shared floor, not an ORM on top of the ORM.
"""

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFound
from app.db.base import Entity


def live[ModelT: Entity](model: type[ModelT]) -> Select[tuple[ModelT]]:
    """``SELECT`` over the live rows of one table."""
    return select(model).where(model.deleted_at.is_(None))


async def get_live[ModelT: Entity](
    session: AsyncSession, model: type[ModelT], entity_id: uuid.UUID
) -> ModelT | None:
    row: ModelT | None = await session.scalar(live(model).where(model.id == entity_id))
    return row


async def get_live_or_404[ModelT: Entity](
    session: AsyncSession, model: type[ModelT], entity_id: uuid.UUID, *, name: str
) -> ModelT:
    """Fetch or raise — ``name`` is what the client is told was not found."""
    row = await get_live(session, model, entity_id)
    if row is None:
        raise NotFound(f"{name} not found")
    return row


async def exists_live[ModelT: Entity](
    session: AsyncSession, model: type[ModelT], *criteria: Any
) -> bool:
    """Whether any live row matches — used for uniqueness checks before insert.

    The partial unique index (``mixins.soft_delete_unique_index``) is what
    actually guarantees uniqueness; this exists so the client gets a `422`
    naming the field instead of a `500` from an integrity error.
    """
    stmt = live(model).where(*criteria).limit(1)
    return await session.scalar(stmt) is not None


__all__ = ["exists_live", "get_live", "get_live_or_404", "live"]
