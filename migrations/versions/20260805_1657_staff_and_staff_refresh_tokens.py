"""staff and staff refresh tokens

Revision ID: 452032bc37c6
Revises: 5d3706388f39
Create Date: 2026-08-05 16:57:05.731175

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '452032bc37c6'
down_revision: str | None = '5d3706388f39'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('staff',
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.Enum('owner', 'admin', name='staff_role', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('is_blocked', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff'))
    )
    op.create_index(op.f('ix_staff_deleted_at'), 'staff', ['deleted_at'], unique=False)
    op.create_index('uq_staff_email_live', 'staff', ['email'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('staff_refresh_tokens',
    sa.Column('staff_id', sa.UUID(), nullable=False),
    sa.Column('jti', sa.String(length=32), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_staff_refresh_tokens_staff_id_staff'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff_refresh_tokens')),
    sa.UniqueConstraint('jti', name=op.f('uq_staff_refresh_tokens_jti'))
    )
    op.create_index(op.f('ix_staff_refresh_tokens_staff_id'), 'staff_refresh_tokens', ['staff_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_staff_refresh_tokens_staff_id'), table_name='staff_refresh_tokens')
    op.drop_table('staff_refresh_tokens')
    op.drop_index('uq_staff_email_live', table_name='staff', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index(op.f('ix_staff_deleted_at'), table_name='staff')
    op.drop_table('staff')
