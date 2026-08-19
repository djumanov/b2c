"""orders route

Revision ID: 7c4e2b915da8
Revises: 3d95c81ab7f4
Create Date: 2026-08-19 10:30:12.442915

The list screen wants a route, dates and travellers, and until now the route
existed in exactly one place: inside ``provider_response``, the provider's
whole booking answer. Reading it there would mean the list query fetching
twenty JSONB blobs per page — the cost the card shape was introduced to avoid
(STATUS.md decisions 14d and 14e).

So the journey comes out of the blob the way the travellers already did:
``route`` holds one object per direction, with its own dates, and
``travel_end_at`` names where the journey stops the way ``travel_start_at``
names where it begins.

**No backfill.** Filling these for existing rows would mean reading GTS's
shape from inside a migration, and GTS's shape is the flight adapter's
business (ARCHITECTURE.md §4); a migration that learned it would also freeze
that knowledge at today's spelling. Rows booked before this revision keep
``route IS NULL`` and the wire builds a summary-only route for them.

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "7c4e2b915da8"
down_revision: str | None = "3d95c81ab7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("travel_end_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("route", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "route")
    op.drop_column("orders", "travel_end_at")
