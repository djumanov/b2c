"""order lifecycle: three statuses, ticketing bookkeeping, order_events

Revision ID: 8b2e4c6d1f03
Revises: 71de8a147ad4
Create Date: 2026-08-21 09:00:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

The single ``status`` column becomes three lifecycles. Only ``booked`` was
ever written by the code that created this table, but the old CHECK allowed
``paid`` and ``ticketed`` too, so those are folded into the new columns rather
than assumed absent. CHECK constraints are written by hand: ``env.py`` hides
them from autogenerate on purpose.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '8b2e4c6d1f03'
down_revision: str | None = '71de8a147ad4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('payment_status', sa.String(length=16), server_default=sa.text("'pending'"), nullable=False))
    op.add_column('orders', sa.Column('ticketing_status', sa.String(length=16), server_default=sa.text("'pending'"), nullable=False))
    op.add_column('orders', sa.Column('cancel_reason', sa.String(length=16), nullable=True))
    op.add_column('orders', sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('ticketed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('ticketing_requested_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('ticketing_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('ticketing_attempts', sa.SmallInteger(), server_default=sa.text('0'), nullable=False))
    op.add_column('orders', sa.Column('ticketing_error', sa.Text(), nullable=True))

    # Fold the old combined states into the new columns before the CHECKs land.
    op.execute("UPDATE orders SET payment_status = 'paid', paid_at = updated_at WHERE status IN ('paid', 'ticketed')")
    op.execute("UPDATE orders SET ticketing_status = 'ticketed', ticketed_at = updated_at WHERE status = 'ticketed'")
    op.execute("UPDATE orders SET status = 'booked' WHERE status IN ('paid', 'ticketed')")
    op.execute("UPDATE orders SET cancelled_at = updated_at WHERE status = 'cancelled' AND cancelled_at IS NULL")

    op.drop_constraint(op.f('ck_orders_order_status'), 'orders', type_='check')
    op.create_check_constraint(op.f('ck_orders_order_status'), 'orders', "status IN ('booked', 'cancelled')")
    op.create_check_constraint(op.f('ck_orders_payment_status'), 'orders', "payment_status IN ('pending', 'paid', 'failed', 'refunding', 'refunded', 'refund_failed')")
    op.create_check_constraint(op.f('ck_orders_ticketing_status'), 'orders', "ticketing_status IN ('pending', 'processing', 'ticketed', 'failed')")
    op.create_check_constraint(op.f('ck_orders_cancel_reason'), 'orders', "cancel_reason IS NULL OR cancel_reason IN ('customer', 'expired', 'staff')")
    op.create_check_constraint(op.f('ck_orders_cancelled_consistent'), 'orders', "(status = 'cancelled') = (cancelled_at IS NOT NULL)")

    op.create_index('ix_orders_processing', 'orders', ['ticketing_requested_at'], unique=False, postgresql_where=sa.text("ticketing_status = 'processing'"))
    op.create_index('ix_orders_unpaid_deadline', 'orders', ['ticket_time_limit_at'], unique=False, postgresql_where=sa.text("status = 'booked' AND payment_status IN ('pending', 'failed')"))

    op.create_table('order_events',
    sa.Column('order_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('event', sa.String(length=48), nullable=False),
    sa.Column('from_value', sa.String(length=16), nullable=True),
    sa.Column('to_value', sa.String(length=16), nullable=True),
    sa.Column('actor', sa.String(length=48), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_events'))
    )
    op.create_index('ix_order_events_order_created', 'order_events', ['order_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_order_events_order_created', table_name='order_events')
    op.drop_table('order_events')

    op.drop_index('ix_orders_unpaid_deadline', table_name='orders')
    op.drop_index('ix_orders_processing', table_name='orders')

    op.drop_constraint(op.f('ck_orders_cancelled_consistent'), 'orders', type_='check')
    op.drop_constraint(op.f('ck_orders_cancel_reason'), 'orders', type_='check')
    op.drop_constraint(op.f('ck_orders_ticketing_status'), 'orders', type_='check')
    op.drop_constraint(op.f('ck_orders_payment_status'), 'orders', type_='check')
    op.drop_constraint(op.f('ck_orders_order_status'), 'orders', type_='check')

    # Back to the combined vocabulary, keeping as much as it can say.
    op.execute("UPDATE orders SET status = 'paid' WHERE status = 'booked' AND payment_status IN ('paid', 'refunding', 'refunded', 'refund_failed') AND ticketing_status <> 'ticketed'")
    op.execute("UPDATE orders SET status = 'ticketed' WHERE status = 'booked' AND ticketing_status = 'ticketed'")
    op.create_check_constraint(op.f('ck_orders_order_status'), 'orders', "status IN ('booked', 'paid', 'ticketed', 'cancelled')")

    op.drop_column('orders', 'ticketing_error')
    op.drop_column('orders', 'ticketing_attempts')
    op.drop_column('orders', 'ticketing_checked_at')
    op.drop_column('orders', 'ticketing_requested_at')
    op.drop_column('orders', 'cancelled_at')
    op.drop_column('orders', 'ticketed_at')
    op.drop_column('orders', 'paid_at')
    op.drop_column('orders', 'cancel_reason')
    op.drop_column('orders', 'ticketing_status')
    op.drop_column('orders', 'payment_status')
