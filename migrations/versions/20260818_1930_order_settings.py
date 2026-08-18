"""order settings

Revision ID: f1a6b40d72c9
Revises: e07d3a55c1b8
Create Date: 2026-08-18 19:30:18.204471

The two numbers the ticketing step reads, in the database where a client can
change them (PROJECT.md §7). Both pass the rule's own test — could two clients
want different values — and the answer is plainly yes: how much margin a hold
needs before ticketing, and how much of a fare increase to absorb rather than
refund.

A third, ``hold_fallback_minutes``, is not about the provider's rule but about
ours: how long to keep an unpaid order open when the provider states no
deadline at all.

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

**Every ``CHECK`` here is written by hand**, with the short names the model
uses — ``db/base.py``'s convention expands each into ``ck_order_settings_…``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "f1a6b40d72c9"
down_revision: str | None = "e07d3a55c1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "order_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # The singleton pair: always TRUE and UNIQUE, so a second row is
        # impossible at the level that still holds when two workers insert at
        # once (``db/mixins.py``).
        sa.Column(
            "singleton", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "ticket_margin_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column(
            "reprice_tolerance",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "hold_fallback_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("180"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("singleton IS TRUE", name="singleton"),
        sa.CheckConstraint("ticket_margin_minutes >= 0", name="ticket_margin"),
        sa.CheckConstraint("reprice_tolerance >= 0", name="reprice_tolerance"),
        sa.CheckConstraint("hold_fallback_minutes > 0", name="hold_fallback"),
        sa.PrimaryKeyConstraint("id", name="pk_order_settings"),
        sa.UniqueConstraint("singleton", name="uq_order_settings_singleton"),
    )
    op.create_index("ix_order_settings_deleted_at", "order_settings", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_order_settings_deleted_at", table_name="order_settings")
    op.drop_table("order_settings")
