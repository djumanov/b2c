"""order reprice

Revision ID: d9a3e5b7f468
Revises: c8f2d4a6e357
Create Date: 2026-08-24 19:00:00.000000

GTS prices a held booking again before it will issue the ticket — its
lifecycle is ``booking → reprice_check → reprice_confirm → ticketing``, and
the live server refuses ``ticketing`` for an order that skipped the two price
steps. ``orders`` gains the two stamps the payment step keys on: when the
price was last checked (``repriced_at``) and when the customer accepted it
and GTS confirmed it (``price_confirmed_at``), and the reprice answer itself
(``price_response``) — GTS's later word on the price than the order record's
own ``price_info``, which ``amount`` and ``order_data`` are read from once it
exists. All three NULL for every existing row: an order booked before this
release goes through the price steps like a new one before it can be paid.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d9a3e5b7f468"
down_revision: str | None = "c8f2d4a6e357"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("repriced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column("price_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("orders", sa.Column("price_response", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "price_response")
    op.drop_column("orders", "price_confirmed_at")
    op.drop_column("orders", "repriced_at")
