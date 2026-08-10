"""leads

Revision ID: cdac5166fee1
Revises: 391afb5e71ff
Create Date: 2026-08-10 16:11:02.745928

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = 'cdac5166fee1'
down_revision: str | None = '391afb5e71ff'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('leads',
    sa.Column('topic', sa.String(length=128), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=True),
    sa.Column('contact', sa.String(length=160), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default=sa.text("'new'"), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('customer_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('new', 'in_progress', 'done')", name=op.f('ck_leads_lead_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads'))
    )
    op.create_index(op.f('ix_leads_customer_id'), 'leads', ['customer_id'], unique=False)
    op.create_index(op.f('ix_leads_deleted_at'), 'leads', ['deleted_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_deleted_at'), table_name='leads')
    op.drop_index(op.f('ix_leads_customer_id'), table_name='leads')
    op.drop_table('leads')
