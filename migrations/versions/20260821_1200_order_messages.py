"""order messages — the panel's sentence per stage, over the code's defaults

Revision ID: a4d6e8f0b125
Revises: 9c3f5d7e2a14
Create Date: 2026-08-21 12:00:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

No data migration: rows are created per ``Stage`` on first read, and ``text``
holds only what staff wrote — the defaults live in code.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a4d6e8f0b125'
down_revision: str | None = '9c3f5d7e2a14'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('order_messages',
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('text', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_messages')),
    sa.UniqueConstraint('key', name=op.f('uq_order_messages_key'))
    )


def downgrade() -> None:
    op.drop_table('order_messages')
