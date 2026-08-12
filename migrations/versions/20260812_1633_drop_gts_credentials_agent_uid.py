"""drop gts credentials agent_uid

Revision ID: 182e01999182
Revises: 6e4b3a1f9c02
Create Date: 2026-08-12 16:33:03.061111

The field was stored for GTS's ``agent-uid`` header, but that header belongs
to GTS's own multi-agent admin scenarios and is never asked of an agent
account like ours — nothing ever read the column (decision of 2026-08-12).

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '182e01999182'
down_revision: str | None = '6e4b3a1f9c02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("gts_credentials", "agent_uid")


def downgrade() -> None:
    # The values are gone for good; nullable, so the downgrade still stands.
    op.add_column(
        "gts_credentials",
        sa.Column("agent_uid", sa.VARCHAR(length=64), nullable=True),
    )
