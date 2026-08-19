"""card only payments

Revision ID: c8f2a5d61b40
Revises: b3e7c1f04a92
Create Date: 2026-08-19 15:20:44.913602

The hosted redirect leaves the schema (``order-system/03-design.md`` ``O14``).
``flow`` held one value and a constraint asserting that value; ``redirect_url``
held where a customer was sent, and nobody is sent anywhere any more.

In their place, the guard that makes ``transactions/`` safe without an
idempotency key: **one open attempt per order**. A partial unique index rather
than a check in the handler, for the reason ``O8`` gives — a read followed by a
write has a gap between them and an index does not.

Any attempt left ``created`` or ``pending`` from the redirect era is closed as
``cancelled`` before the new vocabulary is applied. ``pending`` keeps its name
and changes its meaning: it is now "the charge went out and the answer is
unknown", which no redirect-era row can honestly claim.

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

**Every ``CHECK`` here is written by hand**, with the short names the model
uses — ``db/base.py``'s convention expands each into ``ck_order_payments_…``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "c8f2a5d61b40"
down_revision: str | None = "b3e7c1f04a92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUSES_BEFORE = (
    "created",
    "awaiting_card",
    "awaiting_otp",
    "pending",
    "paid",
    "failed",
    "cancelled",
)
_STATUSES_AFTER = (
    "awaiting_card",
    "awaiting_otp",
    "pending",
    "paid",
    "failed",
    "cancelled",
)

_OPEN_WHERE = sa.text("status IN ('awaiting_card', 'awaiting_otp', 'pending')")


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _restate_status(values: Sequence[str]) -> None:
    """Replace the status vocabulary on ``order_payments``.

    The drop is raw SQL and the create is not, for the reason the
    ``order_payments`` revision spells out: ``create_check_constraint`` runs the
    short name through the naming convention, while ``drop_constraint`` would
    run the already-expanded name through it a second time.
    """
    op.execute("ALTER TABLE order_payments DROP CONSTRAINT ck_order_payments_status")
    op.create_check_constraint("status", "order_payments", _in_list("status", values))


def upgrade() -> None:
    # A redirect-era attempt cannot be carried into the new vocabulary: nobody
    # is coming back from a provider page to finish it, and `pending` now means
    # something it cannot mean. Close them rather than leave rows that violate
    # the constraint about to be created.
    op.execute(
        "UPDATE order_payments SET status = 'cancelled'"
        " WHERE status IN ('created', 'pending')"
    )
    _restate_status(_STATUSES_AFTER)

    op.drop_constraint(op.f("ck_order_payments_flow"), "order_payments", type_="check")
    op.drop_column("order_payments", "flow")
    op.drop_column("order_payments", "redirect_url")

    op.create_index(
        "uq_order_payments_open",
        "order_payments",
        ["order_id"],
        unique=True,
        postgresql_where=_OPEN_WHERE,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_order_payments_open",
        table_name="order_payments",
        postgresql_where=_OPEN_WHERE,
    )

    op.add_column("order_payments", sa.Column("redirect_url", sa.Text(), nullable=True))
    # ``flow`` is NOT NULL going back, so it needs a value for every existing
    # row before the constraint can be restored. Everything here was paid by
    # card; calling it that is the only honest answer.
    op.add_column(
        "order_payments",
        sa.Column(
            "flow", sa.String(length=8), nullable=False, server_default=sa.text("'card'")
        ),
    )
    op.alter_column("order_payments", "flow", server_default=None)
    op.create_check_constraint(
        "flow", "order_payments", _in_list("flow", ("redirect", "card"))
    )

    _restate_status(_STATUSES_BEFORE)
