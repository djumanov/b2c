"""social credentials

Revision ID: 179a9459a8fa
Revises: f7ca79299369
Create Date: 2026-08-06 17:32:03.486758

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

No rows here either: ``repository.social_credentials`` creates one per provider
on first read, so an installation that upgrades into a release supporting Apple
finds its row already there, switched off.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '179a9459a8fa'
down_revision: str | None = 'f7ca79299369'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('social_credentials',
    sa.Column('provider', sa.Enum('google', name='social_provider_code', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('client_id', sa.String(length=255), nullable=True),
    sa.Column('client_secret', sa.Text(), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_social_credentials')),
    sa.UniqueConstraint('provider', name=op.f('uq_social_credentials_provider'))
    )


def downgrade() -> None:
    op.drop_table('social_credentials')
