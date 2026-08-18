"""orders idempotency per customer

Revision ID: c4b81e2f905a
Revises: 7a2c5e8b91d4
Create Date: 2026-08-18 16:10:44.913277

The idempotency key becomes unique **per customer** rather than globally, the
moment booking starts using it.

A global unique index would make one customer's key claim the value for
everybody: a second customer sending the same key finds no row of their own,
and the natural recovery — read back the order that holds the key — would hand
them somebody else's booking. Keys are chosen by clients, so two of them
choosing the same string is not a hypothetical the database should resolve by
picking a winner.

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "c4b81e2f905a"
down_revision: str | None = "7a2c5e8b91d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WHERE = sa.text("idempotency_key IS NOT NULL")


def upgrade() -> None:
    op.drop_index(
        "uq_orders_idempotency_key", table_name="orders", postgresql_where=_WHERE
    )
    op.create_index(
        "uq_orders_customer_id_idempotency_key",
        "orders",
        ["customer_id", "idempotency_key"],
        unique=True,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_orders_customer_id_idempotency_key",
        table_name="orders",
        postgresql_where=_WHERE,
    )
    op.create_index(
        "uq_orders_idempotency_key",
        "orders",
        ["idempotency_key"],
        unique=True,
        postgresql_where=_WHERE,
    )
