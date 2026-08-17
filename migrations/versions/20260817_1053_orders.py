"""orders

Revision ID: b17076f7d6da
Revises: 49b3b6e5cc3a
Create Date: 2026-08-17 10:53:16.017189

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b17076f7d6da"
down_revision: str | None = "49b3b6e5cc3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The commerce group's first table (ARCHITECTURE.md §10). ``gts_response``
    # holds GTS's booking answer verbatim; the four columns beside it are
    # lifted out of the request and the answer only because they need indexes
    # (app/modules/orders/models.py).
    #
    # No foreign key to ``customers``: a cross-module FK is the database's
    # version of importing another module's models (ARCHITECTURE.md §4) — the
    # ``customer_cards`` choice.
    #
    # ``status`` deliberately has **no** CHECK: it holds GTS's own code, and a
    # new code upstream must not stop a real booking being recorded.
    op.create_table(
        "orders",
        sa.Column("customer_id", sa.UUID(), nullable=False),
        sa.Column("product", sa.String(length=16), nullable=False),
        sa.Column("gts_order_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("offer_id", sa.String(length=64), nullable=True),
        sa.Column(
            "gts_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "product IN ('flight', 'railway', 'insurance', 'esim', 'transfer')",
            name=op.f("ck_orders_order_product"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(
        "ix_orders_customer_created",
        "orders",
        ["customer_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_orders_customer_id"), "orders", ["customer_id"], unique=False
    )
    op.create_index(
        op.f("ix_orders_deleted_at"), "orders", ["deleted_at"], unique=False
    )
    op.create_index(op.f("ix_orders_product"), "orders", ["product"], unique=False)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)
    # One GTS order is one row. Partial on two counts: rows whose identifier we
    # could not read (NULL) must not collide with each other, and a soft-deleted
    # row must not block re-recording the same booking.
    op.create_index(
        "uq_orders_gts_order_id_live",
        "orders",
        ["gts_order_id"],
        unique=True,
        postgresql_where=sa.text("gts_order_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_orders_gts_order_id_live",
        table_name="orders",
        postgresql_where=sa.text("gts_order_id IS NOT NULL AND deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_product"), table_name="orders")
    op.drop_index(op.f("ix_orders_deleted_at"), table_name="orders")
    op.drop_index(op.f("ix_orders_customer_id"), table_name="orders")
    op.drop_index("ix_orders_customer_created", table_name="orders")
    op.drop_table("orders")
