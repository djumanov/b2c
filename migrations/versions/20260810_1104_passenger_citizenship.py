"""passenger citizenship

Revision ID: 2a9b860859f1
Revises: 2d16940ae60b
Create Date: 2026-08-10 11:04:34.808184

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

Nullable and free text, not a country code: the list of countries is GTS's
(GTS.md §9), and a code column would also fix a standard — "UZ" or "UZB" —
that the contract has not chosen. Widening a string later is cheap; undoing a
CHECK constraint that disagrees with the upstream catalogue is not.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '2a9b860859f1'
down_revision: str | None = '2d16940ae60b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'passengers',
        sa.Column('citizenship', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('passengers', 'citizenship')
