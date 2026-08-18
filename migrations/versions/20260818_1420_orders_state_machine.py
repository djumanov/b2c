"""orders state machine

Revision ID: 7a2c5e8b91d4
Revises: 3c1f9a4d7e02
Create Date: 2026-08-18 14:20:11.402118

The order row stops being a side-record of a passthrough and becomes the
aggregate the money path runs on (order-system/03-design.md §3.4). The old
table is dropped rather than migrated: it holds GTS's status vocabulary in a
column that is now ours and carries a ``CHECK``, and there is nothing in it
worth carrying across — this installation has never taken a real booking.

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

**Every ``CHECK`` here is written by hand.** Alembic's autogenerate does not
emit them, so a constraint that only exists on the model is a constraint that
does not exist at all. Their names are the **short** ones the model uses —
``db/base.py``'s naming convention expands each into ``ck_orders_…``, and
spelling the full name here would produce ``ck_orders_ck_orders_…`` and leave a
later ``drop_constraint`` unable to find it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "7a2c5e8b91d4"
down_revision: str | None = "3c1f9a4d7e02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PRODUCTS = ("flight", "railway", "insurance", "esim", "transfer")
_STATUSES = (
    "created",
    "booked",
    "paid",
    "ticketing",
    "ticketed",
    "refunding",
    "refunded",
    "partially_refunded",
    "cancelled",
    "voided",
    "failed",
    "needs_attention",
)
_ACTOR_TYPES = ("system", "customer", "staff")
_ACTIONS = (
    "booking.confirmed",
    "booking.rejected",
    "booking.unresolved",
    "booking.expired",
    "payment.settled",
    "order.cancelled",
    "order.voided",
    "ticketing.started",
    "ticketing.retry",
    "ticketing.succeeded",
    "ticketing.failed",
    "refund.started",
    "refund.succeeded",
    "refund.partial",
    "refund.failed",
    "attention.resolved",
)


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    op.drop_table("orders")

    # ``order_no`` comes from here. A sequence and not a row count: two
    # bookings racing must never be handed the same number.
    op.execute("CREATE SEQUENCE orders_order_no_seq")

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_no", sa.String(length=24), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column(
            "provider", sa.String(length=16), nullable=False, server_default="gts"
        ),
        sa.Column("provider_order_number", sa.String(length=64), nullable=True),
        sa.Column("provider_order_uid", sa.String(length=64), nullable=True),
        sa.Column("provider_pnr", sa.String(length=32), nullable=True),
        sa.Column("provider_status", sa.String(length=16), nullable=True),
        sa.Column("provider_response", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("offer_id", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("amount_total", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column(
            "amount_paid",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "amount_refunded",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "travelers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("travel_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("route_summary", sa.String(length=128), nullable=True),
        sa.Column("ticket_time_limit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "attempts", sa.SmallInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=32), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("attention_reason", sa.String(length=64), nullable=True),
        sa.Column("booked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ticketed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(_in_list("product", _PRODUCTS), name="product"),
        sa.CheckConstraint(_in_list("status", _STATUSES), name="status"),
        sa.CheckConstraint(
            "status IN ('created', 'failed', 'needs_attention')"
            " OR (amount_total IS NOT NULL AND currency IS NOT NULL)",
            name="priced_once_booked",
        ),
        sa.CheckConstraint(
            "amount_paid >= 0 AND amount_refunded >= 0"
            " AND amount_refunded <= amount_paid",
            name="amounts",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("order_no", name="uq_orders_order_no"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_product", "orders", ["product"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_provider_pnr", "orders", ["provider_pnr"])
    op.create_index("ix_orders_deleted_at", "orders", ["deleted_at"])
    op.create_index("ix_orders_customer_created", "orders", ["customer_id", "created_at"])
    op.create_index(
        "uq_orders_provider_number_live",
        "orders",
        ["provider", "provider_order_number"],
        unique=True,
        postgresql_where=sa.text(
            "provider_order_number IS NOT NULL AND deleted_at IS NULL"
        ),
    )
    op.create_index(
        "uq_orders_idempotency_key",
        "orders",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # Both sweeps are partial: the deadline sweep only ever reads unpaid holds
    # and the poller only rows with work waiting, so neither grows with the
    # table.
    op.create_index(
        "ix_orders_ticket_deadline",
        "orders",
        ["ticket_time_limit_at"],
        postgresql_where=sa.text("status = 'booked'"),
    )
    op.create_index(
        "ix_orders_due",
        "orders",
        ["next_attempt_at"],
        postgresql_where=sa.text("next_attempt_at IS NOT NULL"),
    )

    op.create_table(
        "order_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        # ``clock_timestamp()`` rather than ``now()``: several events can be
        # written inside one transaction and ``now()`` would give them all the
        # same instant, leaving the history unordered.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_label", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            _in_list("actor_type", _ACTOR_TYPES), name="actor_type"
        ),
        sa.CheckConstraint(
            _in_list("action", _ACTIONS), name="action"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_events_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_order_events"),
    )
    op.create_index(
        "ix_order_events_order_created", "order_events", ["order_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_order_events_order_created", table_name="order_events")
    op.drop_table("order_events")

    op.drop_index(
        "ix_orders_due",
        table_name="orders",
        postgresql_where=sa.text("next_attempt_at IS NOT NULL"),
    )
    op.drop_index(
        "ix_orders_ticket_deadline",
        table_name="orders",
        postgresql_where=sa.text("status = 'booked'"),
    )
    op.drop_index(
        "uq_orders_idempotency_key",
        table_name="orders",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        "uq_orders_provider_number_live",
        table_name="orders",
        postgresql_where=sa.text(
            "provider_order_number IS NOT NULL AND deleted_at IS NULL"
        ),
    )
    op.drop_table("orders")
    op.execute("DROP SEQUENCE orders_order_no_seq")

    # The shape this revision replaced, so an installation can step back onto
    # the passthrough build (PROJECT.md D10).
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product", sa.String(length=16), nullable=False),
        sa.Column("gts_order_number", sa.String(length=64), nullable=True),
        sa.Column("gts_order_uid", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("offer_id", sa.String(length=64), nullable=True),
        sa.Column("gts_response", postgresql.JSONB(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            _in_list("product", _PRODUCTS), name="order_product"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])
    op.create_index("ix_orders_product", "orders", ["product"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_deleted_at", "orders", ["deleted_at"])
    op.create_index("ix_orders_customer_created", "orders", ["customer_id", "created_at"])
    op.create_index(
        "uq_orders_gts_order_number_live",
        "orders",
        ["gts_order_number"],
        unique=True,
        postgresql_where=sa.text("gts_order_number IS NOT NULL AND deleted_at IS NULL"),
    )
