"""faqs

Revision ID: e91849bca6dc
Revises: 8c1d4a6f2b70
Create Date: 2026-08-10 16:02:55.119911

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e91849bca6dc'
down_revision: str | None = '8c1d4a6f2b70'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('faqs',
    sa.Column('question', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('answer', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_faqs'))
    )
    op.create_index(op.f('ix_faqs_category'), 'faqs', ['category'], unique=False)
    op.create_index(op.f('ix_faqs_deleted_at'), 'faqs', ['deleted_at'], unique=False)
    # By hand — env.py excludes CHECK constraints from autogenerate.
    op.create_check_constraint(
        op.f('ck_faqs_content_status'),
        'faqs',
        "status IN ('draft', 'published')",
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_faqs_deleted_at'), table_name='faqs')
    op.drop_index(op.f('ix_faqs_category'), table_name='faqs')
    op.drop_table('faqs')
