"""order refunds

Revision ID: 3d95c81ab7f4
Revises: f1a6b40d72c9
Create Date: 2026-08-18 21:15:37.881204

Money going back gets a table. Until now an order whose ticket never issued
stopped in ``refunding`` and waited for a person; this is what finishes it
(PROJECT.md D3 — the money is never lost quietly).

One table for both entrances: compensation, which is approved on creation
because nobody has to agree that a customer who paid for nothing should be
repaid, and a customer's request, which waits for one.

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

**Every ``CHECK`` here is written by hand**, with the short names the model
uses — ``db/base.py``'s convention expands each into ``ck_order_refunds_…``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "3d95c81ab7f4"
down_revision: str | None = "f1a6b40d72c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_KINDS = ("auto", "customer", "admin")
_STATES = (
    "requested",
    "approved",
    "rejected",
    "processing",
    "succeeded",
    "failed",
)
#: One refund at a time per order — the states in which one is still running.
_OPEN = sa.text("status IN ('requested', 'approved', 'processing')")

#: ``refund.retry`` joins the order event vocabulary here. Refunding talks to
#: two outside systems and can fail at either, so it retries exactly as
#: ticketing does — and a retry nobody recorded is a gap in the history
#: somebody reads when money is stuck.
_ACTIONS_BEFORE = (
    "booking.confirmed",
    "booking.rejected",
    "booking.unresolved",
    "booking.expired",
    "payment.settled",
    "payment.mismatched",
    "order.cancelled",
    "order.voided",
    "ticketing.started",
    "ticketing.retry",
    "ticketing.succeeded",
    "ticketing.partial",
    "ticketing.failed",
    "refund.started",
    "refund.succeeded",
    "refund.partial",
    "refund.failed",
    "attention.resolved",
)
_ACTIONS_AFTER = (*_ACTIONS_BEFORE, "refund.retry")


def _recheck_actions(values: Sequence[str]) -> None:
    """See the note in the ``order_payments`` revision: the drop is raw SQL
    because ``drop_constraint`` would run an already expanded name through the
    naming convention a second time."""
    op.execute("ALTER TABLE order_events DROP CONSTRAINT ck_order_events_action")
    op.create_check_constraint("action", "order_events", _in_list("action", values))


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def upgrade() -> None:
    _recheck_actions(_ACTIONS_AFTER)

    op.create_table(
        "order_refunds",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column(
            "penalty_amount",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("provider_refund_ref", sa.String(length=128), nullable=True),
        sa.Column("provider_order_action", sa.String(length=32), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
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
        sa.CheckConstraint(_in_list("kind", _KINDS), name="kind"),
        sa.CheckConstraint(_in_list("status", _STATES), name="status"),
        sa.CheckConstraint(
            "amount >= 0 AND penalty_amount >= 0", name="amounts"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_refunds_order_id_orders",
            ondelete="CASCADE",
        ),
        # The attempt may outlive its own row's usefulness; losing the link is
        # better than losing the refund.
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["order_payments.id"],
            name="fk_order_refunds_payment_id_order_payments",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_order_refunds"),
    )
    op.create_index("ix_order_refunds_status", "order_refunds", ["status"])
    op.create_index(
        "ix_order_refunds_order_created", "order_refunds", ["order_id", "created_at"]
    )
    # A second refund opened while the first is still running would race it to
    # the same money.
    op.create_index(
        "uq_order_refunds_open",
        "order_refunds",
        ["order_id"],
        unique=True,
        postgresql_where=_OPEN,
    )


def downgrade() -> None:
    op.execute("DELETE FROM order_events WHERE action = 'refund.retry'")
    _recheck_actions(_ACTIONS_BEFORE)

    op.drop_index(
        "uq_order_refunds_open", table_name="order_refunds", postgresql_where=_OPEN
    )
    op.drop_index("ix_order_refunds_order_created", table_name="order_refunds")
    op.drop_index("ix_order_refunds_status", table_name="order_refunds")
    op.drop_table("order_refunds")
