"""uploads

Revision ID: 3c3dfcd56c36
Revises: fa8973fd56f2
Create Date: 2026-08-05 17:39:22.772641

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '3c3dfcd56c36'
down_revision: str | None = 'fa8973fd56f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('uploads',
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=128), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('purpose', sa.Enum('logo', 'favicon', 'app_icon', 'blog_cover', 'promo_banner', 'banner', 'document', 'export', name='upload_purpose', native_enum=False, create_constraint=True, length=24), nullable=False),
    sa.Column('storage_key', sa.String(length=255), nullable=False),
    sa.Column('uploaded_by', sa.UUID(), nullable=True),
    sa.Column('linked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_uploads')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_uploads_storage_key'))
    )
    op.create_index(op.f('ix_uploads_deleted_at'), 'uploads', ['deleted_at'], unique=False)
    op.create_index(op.f('ix_uploads_linked_at'), 'uploads', ['linked_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_uploads_linked_at'), table_name='uploads')
    op.drop_index(op.f('ix_uploads_deleted_at'), table_name='uploads')
    op.drop_table('uploads')
