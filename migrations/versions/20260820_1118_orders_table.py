"""orders table

Revision ID: 71de8a147ad4
Revises: 3c1f9a4d7e02
Create Date: 2026-08-20 11:18:03.464005

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '71de8a147ad4'
down_revision: str | None = '3c1f9a4d7e02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('orders',
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('product', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('offer_id', sa.String(length=64), nullable=False),
    sa.Column('gts_order_number', sa.BigInteger(), nullable=False),
    sa.Column('gts_order_uid', sa.String(length=64), nullable=True),
    sa.Column('gts_status', sa.String(length=32), nullable=False),
    sa.Column('pnr', sa.String(length=16), nullable=True),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('currency', sa.CHAR(length=3), nullable=True),
    sa.Column('trip_type', sa.String(length=8), nullable=True),
    sa.Column('route_summary', sa.String(length=128), nullable=True),
    sa.Column('passenger_count', sa.SmallInteger(), nullable=True),
    sa.Column('ticket_time_limit_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('gts_response', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('booked', 'paid', 'ticketed', 'cancelled')", name=op.f('ck_orders_order_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders'))
    )
    op.create_index('ix_orders_customer_created', 'orders', ['customer_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_orders_deleted_at'), 'orders', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_orders_gts_order_number'), 'orders', ['gts_order_number'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_orders_gts_order_number'), table_name='orders')
    op.drop_index(op.f('ix_orders_deleted_at'), table_name='orders')
    op.drop_index('ix_orders_customer_created', table_name='orders')
    op.drop_table('orders')
