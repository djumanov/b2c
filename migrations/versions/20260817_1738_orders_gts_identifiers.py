"""orders gts identifiers

Revision ID: 97fbdc8dac3f
Revises: b17076f7d6da
Create Date: 2026-08-17 17:38:25.810062

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "97fbdc8dac3f"
down_revision: str | None = "b17076f7d6da"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``gts_order_id`` was a guess. GTS's booking answer names the order twice
    # — ``order_number`` (an integer, and what its ``cancel`` body takes) and
    # ``order_uid`` (its internal key) — so the guess becomes the first and the
    # second gets a column of its own (EASY_GATEWAY collection,
    # ``/content/Booking`` and ``/content/Cancel``).
    op.alter_column("orders", "gts_order_id", new_column_name="gts_order_number")
    op.add_column("orders", sa.Column("gts_order_uid", sa.String(length=64)))

    # The unique index carries the old column name; rebuild it rather than
    # rename, so the partial predicate is written against the new name too.
    op.drop_index(
        "uq_orders_gts_order_id_live",
        table_name="orders",
        postgresql_where=sa.text("gts_order_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_orders_gts_order_number_live",
        "orders",
        ["gts_order_number"],
        unique=True,
        postgresql_where=sa.text("gts_order_number IS NOT NULL AND deleted_at IS NULL"),
    )

    # Any row written before this point read the wrong field and therefore
    # holds NULL — there is nothing to migrate, and nothing is lost: the whole
    # answer is still in ``gts_response`` and can be re-read from there.


def downgrade() -> None:
    op.drop_index(
        "uq_orders_gts_order_number_live",
        table_name="orders",
        postgresql_where=sa.text("gts_order_number IS NOT NULL AND deleted_at IS NULL"),
    )
    op.drop_column("orders", "gts_order_uid")
    op.alter_column("orders", "gts_order_number", new_column_name="gts_order_id")
    op.create_index(
        "uq_orders_gts_order_id_live",
        "orders",
        ["gts_order_id"],
        unique=True,
        postgresql_where=sa.text("gts_order_id IS NOT NULL AND deleted_at IS NULL"),
    )
