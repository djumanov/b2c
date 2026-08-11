"""deletion_reasons_and_deleted_customers

Revision ID: 3f7a92c15d84
Revises: cdac5166fee1
Create Date: 2026-08-11 09:00:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '3f7a92c15d84'
down_revision: str | None = 'cdac5166fee1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('deletion_reasons',
    sa.Column('text', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_deletion_reasons'))
    )
    op.create_index(op.f('ix_deletion_reasons_deleted_at'), 'deletion_reasons', ['deleted_at'], unique=False)

    # No foreign key to customers on purpose: the archive must outlive the row
    # and never be reachable through a cascade (STATUS.md №72).
    op.create_table('deleted_customers',
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('first_name', sa.String(length=120), nullable=True),
    sa.Column('last_name', sa.String(length=120), nullable=True),
    sa.Column('middle_name', sa.String(length=120), nullable=True),
    sa.Column('phone', sa.String(length=32), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('registered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_deleted_customers'))
    )
    op.create_index(op.f('ix_deleted_customers_customer_id'), 'deleted_customers', ['customer_id'], unique=False)
    op.create_index(op.f('ix_deleted_customers_email'), 'deleted_customers', ['email'], unique=False)
    # By hand — env.py excludes CHECK constraints from autogenerate.
    op.create_check_constraint(
        op.f('ck_deleted_customers_reasons_is_array'),
        'deleted_customers',
        "jsonb_typeof(reasons) = 'array'",
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_deleted_customers_email'), table_name='deleted_customers')
    op.drop_index(op.f('ix_deleted_customers_customer_id'), table_name='deleted_customers')
    op.drop_table('deleted_customers')
    op.drop_index(op.f('ix_deletion_reasons_deleted_at'), table_name='deletion_reasons')
    op.drop_table('deletion_reasons')
