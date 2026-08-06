"""payment provider settings

Revision ID: f7ca79299369
Revises: 96f1b68a3874
Create Date: 2026-08-06 17:24:58.324106

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

The ``upload_purpose`` CHECK is hand-written for the reason spelled out in
``20260806_1636_profile_avatar_and_saved_passengers.py``: ``migrations/env.py``
filters check constraints out of autogenerate, so adding a member to
``UploadPurpose`` produces an empty diff.

No rows are inserted here. ``repository.payment_providers`` creates one per
provider on first read, which is also what gives an upgraded installation its
row for a provider this release added — a data migration would only cover the
providers that existed when it was written.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = 'f7ca79299369'
down_revision: str | None = '96f1b68a3874'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Declaration order of ``UploadPurpose`` (app/providers/storage/base.py).
_PURPOSES_BEFORE = (
    'logo', 'favicon', 'app_icon', 'blog_cover', 'promo_banner', 'banner',
    'document', 'export', 'avatar',
)
_PURPOSES_AFTER = (*_PURPOSES_BEFORE, 'payment_logo')

#: Wrapped in ``op.f`` at every use site — the naming convention would
#: otherwise prefix it a second time. Not wrapped here: ``op`` is a proxy that
#: only exists while a migration runs, so calling it at module level breaks
#: every command that merely loads this script, ``alembic heads`` included.
_PURPOSE_CHECK = 'ck_uploads_upload_purpose'


def _purpose_in(values: Sequence[str]) -> str:
    listed = ', '.join(f"'{value}'" for value in values)
    return f'purpose IN ({listed})'


def upgrade() -> None:
    op.create_table('payment_providers',
    sa.Column('code', sa.Enum('payme', 'click', name='payment_provider_code', native_enum=False, create_constraint=True, length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('title', sa.String(length=64), nullable=False),
    sa.Column('logo_id', sa.UUID(), nullable=True),
    sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('credentials', sa.Text(), nullable=True),
    sa.Column('key_version', sa.Integer(), nullable=True),
    sa.Column('last_tested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_test_ok', sa.Boolean(), nullable=True),
    sa.Column('last_test_error', sa.String(length=500), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_providers')),
    sa.UniqueConstraint('code', name=op.f('uq_payment_providers_code'))
    )

    op.drop_constraint(op.f(_PURPOSE_CHECK), 'uploads', type_='check')
    op.create_check_constraint(
        op.f(_PURPOSE_CHECK), 'uploads', _purpose_in(_PURPOSES_AFTER)
    )


def downgrade() -> None:
    # Any payment logo already uploaded would violate the narrower constraint,
    # so the rows go first. The bytes on disk are left to the orphan sweep — a
    # migration has no storage adapter.
    op.execute("DELETE FROM uploads WHERE purpose = 'payment_logo'")
    op.drop_constraint(op.f(_PURPOSE_CHECK), 'uploads', type_='check')
    op.create_check_constraint(
        op.f(_PURPOSE_CHECK), 'uploads', _purpose_in(_PURPOSES_BEFORE)
    )

    op.drop_table('payment_providers')
