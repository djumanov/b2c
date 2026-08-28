"""single currency

Revision ID: e1b4c7d9a583
Revises: d9a3e5b7f468
Create Date: 2026-08-28 10:30:00.000000

The installation prices in one currency and that currency is now
``core.money.CURRENCY`` — a constant, not a row. Payme settles in UZS only and
there is no exchange rate anywhere in this codebase, so an order GTS priced in
another currency could be booked and then never paid; the choice is removed
rather than the conversion added.

Three things happen here. The ``currencies`` settings table goes, because there
is nothing left to store in it. ``orders`` and ``payment_attempts`` gain a
``CHECK`` that says the invariant at the level that still holds when two
workers write at once. And before either of those, the money already on disk is
**counted, not rewritten**: an installation carrying a foreign-currency order
is a situation for a person, not for a migration that quietly restates what
somebody was charged.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e1b4c7d9a583"
down_revision: str | None = "d9a3e5b7f468"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Spelled out rather than imported from ``core.money``: a migration is frozen
#: the moment it ships and must keep doing what it did on the day it was
#: written, even after the application's constants move on.
CURRENCY = "UZS"


def _refuse_foreign_money() -> None:
    """Stop the upgrade if any stored amount is in another currency.

    Adding the ``CHECK`` would fail on such a row anyway, with Postgres's own
    message and no clue which row. This fails first and names them, so the
    person running the upgrade can decide what those orders were actually
    worth — which is not a decision a migration is allowed to make.
    """
    connection = op.get_bind()
    for table, predicate in (
        ("orders", f"currency IS NOT NULL AND currency <> '{CURRENCY}'"),
        ("payment_attempts", f"currency <> '{CURRENCY}'"),
    ):
        rows = connection.execute(
            sa.text(
                f"SELECT id, currency FROM {table} WHERE {predicate} "  # noqa: S608
                "ORDER BY created_at LIMIT 20"
            )
        ).all()
        if not rows:
            continue
        total = connection.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {predicate}")  # noqa: S608
        ).scalar_one()
        listed = ", ".join(f"{row.id} ({row.currency})" for row in rows)
        raise RuntimeError(
            f"{total} row(s) in {table} are not priced in {CURRENCY}, so this "
            f"installation cannot move to a single currency unattended. "
            f"First rows: {listed}. Decide what each is worth in {CURRENCY} "
            f"and correct them, then run this upgrade again."
        )


def upgrade() -> None:
    _refuse_foreign_money()

    # NULL stays legal on ``orders``: a booking whose price GTS did not report
    # is still recorded, and a price read in another currency is now discarded
    # on the way in, so it arrives as NULL too.
    op.create_check_constraint(
        "currency", "orders", f"currency IS NULL OR currency = '{CURRENCY}'"
    )
    op.create_check_constraint(
        "currency", "payment_attempts", f"currency = '{CURRENCY}'"
    )

    op.drop_index(op.f("ix_currencies_deleted_at"), table_name="currencies")
    op.drop_table("currencies")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_payment_attempts_currency"), "payment_attempts")
    op.drop_constraint(op.f("ck_orders_currency"), "orders")

    # Recreated exactly as 20260805_1750_settings built it. The row itself is
    # not seeded: the settings tables have always been filled in on first read,
    # never by a migration.
    op.create_table(
        "currencies",
        sa.Column(
            "default", sa.String(length=3), server_default=sa.text(f"'{CURRENCY}'"),
            nullable=False,
        ),
        sa.Column(
            "available", JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"), nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.CheckConstraint("singleton IS TRUE", name=op.f("ck_currencies_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_currencies")),
        sa.UniqueConstraint("singleton", name=op.f("uq_currencies_singleton")),
    )
    op.create_index(
        op.f("ix_currencies_deleted_at"), "currencies", ["deleted_at"], unique=False
    )
