"""order payments

Revision ID: e07d3a55c1b8
Revises: c4b81e2f905a
Create Date: 2026-08-18 17:45:02.556411

Payment attempts get a table, and the order event vocabulary gains the two
actions that describe a guard failing after money has already moved
(order-system/03-design.md §3.3).

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

**Every ``CHECK`` here is written by hand.** Alembic's autogenerate does not
emit them, and their names are the **short** ones the model uses — the naming
convention in ``db/base.py`` expands each into ``ck_<table>_…``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "e07d3a55c1b8"
down_revision: str | None = "c4b81e2f905a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PROVIDERS = ("payme", "click")
_TRANSACTION_STATUSES = (
    "created",
    "awaiting_card",
    "awaiting_otp",
    "pending",
    "paid",
    "failed",
    "cancelled",
)
_FLOWS = ("redirect", "card")

_ACTIONS_BEFORE = (
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
_ACTIONS_AFTER = (*_ACTIONS_BEFORE, "payment.mismatched", "ticketing.partial")

_REF_WHERE = sa.text("provider_ref IS NOT NULL")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _recheck_actions(values: Sequence[str]) -> None:
    """Replace the action vocabulary on ``order_events``.

    The drop is raw SQL and the create is not, which looks inconsistent and is
    not: ``create_check_constraint`` runs the short name through the naming
    convention in ``db/base.py`` and produces ``ck_order_events_action``, while
    ``drop_constraint`` would run the *already expanded* name through it again
    and look for ``ck_order_events_ck_order_events_action``.
    """
    op.execute("ALTER TABLE order_events DROP CONSTRAINT ck_order_events_action")
    op.create_check_constraint("action", "order_events", _in_list("action", values))


def upgrade() -> None:
    # Money that arrives for the wrong amount, and a ticketing run that issued
    # some of the tickets: two guards whose failure cannot be a refusal, because
    # by then something irreversible has happened.
    _recheck_actions(_ACTIONS_AFTER)

    op.create_table(
        "order_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provider_ref", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("flow", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("redirect_url", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider_state", postgresql.JSONB(), nullable=True),
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
        sa.CheckConstraint(
            _in_list("provider", _PROVIDERS), name="provider"
        ),
        sa.CheckConstraint(
            _in_list("status", _TRANSACTION_STATUSES), name="status"
        ),
        sa.CheckConstraint(_in_list("flow", _FLOWS), name="flow"),
        sa.CheckConstraint("amount > 0", name="amount"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_payments_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_order_payments"),
    )
    op.create_index("ix_order_payments_status", "order_payments", ["status"])
    op.create_index(
        "ix_order_payments_order_created", "order_payments", ["order_id", "created_at"]
    )
    # The guard that makes a repeated callback harmless.
    op.create_index(
        "uq_order_payments_provider_ref",
        "order_payments",
        ["provider", "provider_ref"],
        unique=True,
        postgresql_where=_REF_WHERE,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_order_payments_provider_ref",
        table_name="order_payments",
        postgresql_where=_REF_WHERE,
    )
    op.drop_index("ix_order_payments_order_created", table_name="order_payments")
    op.drop_index("ix_order_payments_status", table_name="order_payments")
    op.drop_table("order_payments")

    # Rows written with the new actions would fail the older constraint, so they
    # go first: stepping back is allowed to lose history, never to be impossible.
    op.execute(
        "DELETE FROM order_events"
        " WHERE action IN ('payment.mismatched', 'ticketing.partial')"
    )
    _recheck_actions(_ACTIONS_BEFORE)
