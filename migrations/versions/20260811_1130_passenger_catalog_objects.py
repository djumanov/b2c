"""passenger catalog objects

Revision ID: b5e3d1a7c942
Revises: 8c41d09e2b57
Create Date: 2026-08-11 11:30:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

``citizenship`` and ``document_type`` stop being free-text strings and store
the full §26 catalogue object verbatim (JSONB) — the UI shows the name with
its translations and flag, which a bare code cannot carry (STATUS.md §4.75).
Existing string values become NULL both ways: a real GTS object cannot be
reconstructed from "passport" or "Uzbekistan", and only dev/test data exists.
No CHECK constraints — the identifier-key check lives in Pydantic.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5e3d1a7c942"
down_revision: str | None = "8c41d09e2b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "passengers",
        "citizenship",
        existing_type=sa.String(length=64),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="NULL",
    )
    op.alter_column(
        "passengers",
        "document_type",
        existing_type=sa.String(length=32),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=True,
        postgresql_using="NULL",
    )


def downgrade() -> None:
    op.alter_column(
        "passengers",
        "document_type",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.String(length=32),
        existing_nullable=True,
        postgresql_using="NULL",
    )
    op.alter_column(
        "passengers",
        "citizenship",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.String(length=64),
        existing_nullable=True,
        postgresql_using="NULL",
    )
