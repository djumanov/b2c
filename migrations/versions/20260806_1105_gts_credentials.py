"""gts credentials

Revision ID: d83f397c172a
Revises: c4adf0023f38
Create Date: 2026-08-06 11:05:14.980009

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = 'd83f397c172a'
down_revision: str | None = 'c4adf0023f38'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('gts_credentials',
    sa.Column('label', sa.String(length=64), nullable=False),
    sa.Column('base_url', sa.String(length=255), server_default=sa.text("'https://api2.globaltravel.space'"), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password', sa.Text(), nullable=False),
    sa.Column('key_version', sa.Integer(), nullable=False),
    sa.Column('agent_uid', sa.String(length=64), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_gts_credentials'))
    )
    # The predicate is what makes this "at most one active row" rather than
    # "at most one row per is_active value" — without it a second inactive
    # credential could never be stored.
    op.create_index('uq_gts_credentials_active', 'gts_credentials', ['is_active'], unique=True, postgresql_where=sa.text('is_active'))
    op.create_index('uq_gts_credentials_label', 'gts_credentials', ['label'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_gts_credentials_label', table_name='gts_credentials')
    op.drop_index('uq_gts_credentials_active', table_name='gts_credentials', postgresql_where=sa.text('is_active'))
    op.drop_table('gts_credentials')
