"""order_events.seq — the order the history was written in

Revision ID: b7e1c9d3f246
Revises: a4d6e8f0b125
Create Date: 2026-08-21 13:00:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

``created_at`` is ``now()``, the transaction's start, so the lines one commit
writes all carry one stamp and have no order between them. ``seq`` is an
identity column: Postgres numbers the rows already there as it adds the
column, in heap order — insertion order for a table that is never updated —
and every later insert takes the next number.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b7e1c9d3f246'
down_revision: str | None = 'a4d6e8f0b125'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'order_events',
        sa.Column('seq', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('order_events', 'seq')
