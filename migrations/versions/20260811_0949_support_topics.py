"""support_topics

Revision ID: 8c41d09e2b57
Revises: 3f7a92c15d84
Create Date: 2026-08-11 09:49:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '8c41d09e2b57'
down_revision: str | None = '3f7a92c15d84'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('support_topics',
    sa.Column('name', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_support_topics'))
    )
    op.create_index(op.f('ix_support_topics_deleted_at'), 'support_topics', ['deleted_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_support_topics_deleted_at'), table_name='support_topics')
    op.drop_table('support_topics')
