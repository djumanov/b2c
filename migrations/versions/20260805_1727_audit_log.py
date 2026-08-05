"""audit log

Revision ID: fa8973fd56f2
Revises: 452032bc37c6
Create Date: 2026-08-05 17:27:18.542741

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'fa8973fd56f2'
down_revision: str | None = '452032bc37c6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('actor_email', sa.String(length=255), nullable=True),
    sa.Column('actor_role', sa.String(length=16), nullable=True),
    sa.Column('resource', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=48), nullable=False),
    sa.Column('resource_id', sa.UUID(), nullable=True),
    sa.Column('method', sa.String(length=8), nullable=True),
    sa.Column('path', sa.String(length=255), nullable=True),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('changes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_action'), 'audit_log', ['action'], unique=False)
    op.create_index('ix_audit_log_actor_resource_created', 'audit_log', ['actor_id', 'resource', 'created_at'], unique=False)
    op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_log_resource'), 'audit_log', ['resource'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_log_resource'), table_name='audit_log')
    op.drop_index('ix_audit_log_created_at', table_name='audit_log')
    op.drop_index('ix_audit_log_actor_resource_created', table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_action'), table_name='audit_log')
    op.drop_table('audit_log')
