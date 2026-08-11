"""leads_support_contact

Revision ID: 6e4b3a1f9c02
Revises: a1c9f4e82d07
Create Date: 2026-08-11 12:00:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

Autogenerate skips ``CheckConstraint``, so the singleton check is written by
hand here, matching ``settings.Site``'s own migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '6e4b3a1f9c02'
down_revision: str | None = 'a1c9f4e82d07'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('leads_support_contact',
    sa.Column('support_username', sa.String(length=160), nullable=True),
    sa.Column('support_phone', sa.String(length=32), nullable=True),
    sa.Column('support_email', sa.String(length=255), nullable=True),
    sa.Column('working_hours', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint('singleton IS TRUE', name=op.f('ck_leads_support_contact_singleton')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_leads_support_contact')),
    sa.UniqueConstraint('singleton', name=op.f('uq_leads_support_contact_singleton'))
    )
    op.create_index(op.f('ix_leads_support_contact_deleted_at'), 'leads_support_contact', ['deleted_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_support_contact_deleted_at'), table_name='leads_support_contact')
    op.drop_table('leads_support_contact')
