"""customers, sessions and email codes

Revision ID: 8c64b732eb50
Revises: d89da85832da
Create Date: 2026-08-06 15:41:46.600918

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '8c64b732eb50'
down_revision: str | None = 'd89da85832da'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('customers',
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('first_name', sa.String(length=120), nullable=False),
    sa.Column('last_name', sa.String(length=120), nullable=True),
    sa.Column('phone', sa.String(length=32), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_blocked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers'))
    )
    op.create_index(op.f('ix_customers_deleted_at'), 'customers', ['deleted_at'], unique=False)
    # The predicate is the whole point: an address is unique among live rows
    # only, so a deleted account does not hold its email hostage forever. A
    # plain UNIQUE would make "delete my account" irreversible for the person
    # who wants to come back.
    op.create_index('uq_customers_email_live', 'customers', ['email'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('customer_refresh_tokens',
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('jti', sa.String(length=32), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_customer_refresh_tokens_customer_id_customers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customer_refresh_tokens')),
    sa.UniqueConstraint('jti', name=op.f('uq_customer_refresh_tokens_jti'))
    )
    op.create_index(op.f('ix_customer_refresh_tokens_customer_id'), 'customer_refresh_tokens', ['customer_id'], unique=False)
    op.create_table('email_otps',
    sa.Column('customer_id', sa.UUID(), nullable=False),
    sa.Column('purpose', sa.Enum('register', 'password_reset', name='otp_purpose', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_email_otps_customer_id_customers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_email_otps'))
    )
    op.create_index(op.f('ix_email_otps_customer_id'), 'email_otps', ['customer_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_email_otps_customer_id'), table_name='email_otps')
    op.drop_table('email_otps')
    op.drop_index(op.f('ix_customer_refresh_tokens_customer_id'), table_name='customer_refresh_tokens')
    op.drop_table('customer_refresh_tokens')
    op.drop_index('uq_customers_email_live', table_name='customers', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_customers_deleted_at'), table_name='customers')
    op.drop_table('customers')
