"""pages

Revision ID: 391afb5e71ff
Revises: e91849bca6dc
Create Date: 2026-08-10 16:07:58.495477

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '391afb5e71ff'
down_revision: str | None = 'e91849bca6dc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('pages',
    sa.Column('slug', sa.String(length=160), nullable=False),
    sa.Column('title', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('draft', 'published')", name=op.f('ck_pages_content_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pages'))
    )
    op.create_index(op.f('ix_pages_deleted_at'), 'pages', ['deleted_at'], unique=False)
    # Unique across live rows only, so a deleted page's slug can be reused.
    op.create_index('uq_pages_slug_live', 'pages', ['slug'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))


def downgrade() -> None:
    op.drop_index('uq_pages_slug_live', table_name='pages', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_pages_deleted_at'), table_name='pages')
    op.drop_table('pages')
