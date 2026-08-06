"""smtp settings

Revision ID: d89da85832da
Revises: d83f397c172a
Create Date: 2026-08-06 12:00:52.107975

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = 'd89da85832da'
down_revision: str | None = 'd83f397c172a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('smtp_settings',
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=True),
    sa.Column('port', sa.Integer(), server_default=sa.text('587'), nullable=False),
    sa.Column('tls', sa.Enum('starttls', 'ssl', 'none', name='tls_mode', native_enum=False, create_constraint=True, length=16), server_default=sa.text("'starttls'"), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('password', sa.Text(), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=True),
    sa.Column('from_address', sa.String(length=255), nullable=True),
    sa.Column('from_name', sa.String(length=120), nullable=True),
    sa.Column('last_tested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_test_ok', sa.Boolean(), nullable=True),
    sa.Column('last_test_error', sa.String(length=500), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint('singleton IS TRUE', name=op.f('ck_smtp_settings_singleton')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_smtp_settings')),
    sa.UniqueConstraint('singleton', name=op.f('uq_smtp_settings_singleton'))
    )


def downgrade() -> None:
    op.drop_table('smtp_settings')
