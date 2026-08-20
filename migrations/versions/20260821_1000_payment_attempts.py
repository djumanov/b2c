"""payment attempts — one row per conversation with a provider

Revision ID: 9c3f5d7e2a14
Revises: 8b2e4c6d1f03
Create Date: 2026-08-21 10:00:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

The partial unique index is the structural guard against charging twice: one
open (``started``/``confirming``) or ``paid`` attempt per order, enforced by
the database whatever the code above it does. CHECK constraints are written
by hand: ``env.py`` hides them from autogenerate on purpose.

``orders.ticketing_checked_at`` becomes ``gts_checked_at``: the sweep reads an
order back from GTS for more than one reason, and the column is its throttle.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '9c3f5d7e2a14'
down_revision: str | None = '8b2e4c6d1f03'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('orders', 'ticketing_checked_at', new_column_name='gts_checked_at')

    op.create_table('payment_attempts',
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('card_id', sa.UUID(), nullable=True),
    sa.Column('card_last4', sa.String(length=4), nullable=True),
    sa.Column('provider_reference', sa.Text(), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=True),
    sa.Column('phone_hint', sa.String(length=32), nullable=True),
    sa.Column('provider_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('started', 'confirming', 'paid', 'failed', 'abandoned')", name=op.f('ck_payment_attempts_attempt_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_attempts'))
    )
    op.create_index(op.f('ix_payment_attempts_order_id'), 'payment_attempts', ['order_id'], unique=False)
    op.create_index('uq_payment_attempts_open', 'payment_attempts', ['order_id'], unique=True, postgresql_where=sa.text("status IN ('started', 'confirming', 'paid')"))
    op.create_index('ix_payment_attempts_confirming', 'payment_attempts', ['updated_at'], unique=False, postgresql_where=sa.text("status = 'confirming'"))


def downgrade() -> None:
    op.drop_index('ix_payment_attempts_confirming', table_name='payment_attempts')
    op.drop_index('uq_payment_attempts_open', table_name='payment_attempts')
    op.drop_index(op.f('ix_payment_attempts_order_id'), table_name='payment_attempts')
    op.drop_table('payment_attempts')

    op.alter_column('orders', 'gts_checked_at', new_column_name='ticketing_checked_at')
