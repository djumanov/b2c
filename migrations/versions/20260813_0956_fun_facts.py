"""fun facts

Revision ID: 49b3b6e5cc3a
Revises: 182e01999182
Create Date: 2026-08-13 09:56:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '49b3b6e5cc3a'
down_revision: str | None = '182e01999182'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('fun_facts',
    sa.Column('text', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fun_facts'))
    )
    op.create_index(op.f('ix_fun_facts_deleted_at'), 'fun_facts', ['deleted_at'], unique=False)
    # By hand — env.py excludes CHECK constraints from autogenerate.
    op.create_check_constraint(
        op.f('ck_fun_facts_content_status'),
        'fun_facts',
        "status IN ('draft', 'published')",
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_fun_facts_deleted_at'), table_name='fun_facts')
    op.drop_table('fun_facts')
