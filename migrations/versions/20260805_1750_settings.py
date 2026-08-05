"""settings

Revision ID: c4adf0023f38
Revises: 3c3dfcd56c36
Create Date: 2026-08-05 17:50:25.638335

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c4adf0023f38'
down_revision: str | None = '3c3dfcd56c36'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('branding',
    sa.Column('colors', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('font_family', sa.String(length=64), nullable=True),
    sa.Column('logo_id', sa.UUID(), nullable=True),
    sa.Column('favicon_id', sa.UUID(), nullable=True),
    sa.Column('app_icon_id', sa.UUID(), nullable=True),
    sa.Column('app_name', sa.String(length=64), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint('singleton IS TRUE', name=op.f('ck_branding_singleton')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_branding')),
    sa.UniqueConstraint('singleton', name=op.f('uq_branding_singleton'))
    )
    op.create_index(op.f('ix_branding_deleted_at'), 'branding', ['deleted_at'], unique=False)
    op.create_table('currencies',
    sa.Column('default', sa.String(length=3), server_default=sa.text("'UZS'"), nullable=False),
    sa.Column('available', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint('singleton IS TRUE', name=op.f('ck_currencies_singleton')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_currencies')),
    sa.UniqueConstraint('singleton', name=op.f('uq_currencies_singleton'))
    )
    op.create_index(op.f('ix_currencies_deleted_at'), 'currencies', ['deleted_at'], unique=False)
    op.create_table('features',
    sa.Column('flags', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint('singleton IS TRUE', name=op.f('ck_features_singleton')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_features')),
    sa.UniqueConstraint('singleton', name=op.f('uq_features_singleton'))
    )
    op.create_index(op.f('ix_features_deleted_at'), 'features', ['deleted_at'], unique=False)
    op.create_table('languages',
    sa.Column('default', sa.String(length=8), server_default=sa.text("'uz'"), nullable=False),
    sa.Column('available', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint('singleton IS TRUE', name=op.f('ck_languages_singleton')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_languages')),
    sa.UniqueConstraint('singleton', name=op.f('uq_languages_singleton'))
    )
    op.create_index(op.f('ix_languages_deleted_at'), 'languages', ['deleted_at'], unique=False)
    op.create_table('product_settings',
    sa.Column('code', sa.String(length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_product_settings')),
    sa.UniqueConstraint('code', name=op.f('uq_product_settings_code'))
    )
    op.create_table('site',
    sa.Column('name', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('domain', sa.String(length=255), nullable=True),
    sa.Column('support_phone', sa.String(length=32), nullable=True),
    sa.Column('support_email', sa.String(length=255), nullable=True),
    sa.Column('social', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('singleton', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint('singleton IS TRUE', name=op.f('ck_site_singleton')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_site')),
    sa.UniqueConstraint('singleton', name=op.f('uq_site_singleton'))
    )
    op.create_index(op.f('ix_site_deleted_at'), 'site', ['deleted_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_site_deleted_at'), table_name='site')
    op.drop_table('site')
    op.drop_table('product_settings')
    op.drop_index(op.f('ix_languages_deleted_at'), table_name='languages')
    op.drop_table('languages')
    op.drop_index(op.f('ix_features_deleted_at'), table_name='features')
    op.drop_table('features')
    op.drop_index(op.f('ix_currencies_deleted_at'), table_name='currencies')
    op.drop_table('currencies')
    op.drop_index(op.f('ix_branding_deleted_at'), table_name='branding')
    op.drop_table('branding')
