"""order payment card columns

Revision ID: b3e7c1f04a92
Revises: 3d95c81ab7f4
Create Date: 2026-08-19 14:00:11.204517

The attempt row learns what a card payment needs to remember between two HTTP
requests: which card, what the receipt should show, the provider's token while
it is live, and where the code went (``order-system/03-design.md`` §3.4).

Additive on purpose. The columns the hosted redirect used — ``flow`` and
``redirect_url`` — are still here and still written; they go with the endpoints
that write them, one slice later, so that a checkout is never half-wired.

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

**Every ``CHECK`` here is written by hand**, with the short names the model
uses — ``db/base.py``'s convention expands each into ``ck_order_payments_…``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "b3e7c1f04a92"
down_revision: str | None = "3d95c81ab7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The token can be charged, so it may exist only while the attempt is open.
#: Written as a constraint rather than trusted to the service: "erased when the
#: attempt closes" is the sort of promise that survives review and not the
#: fourth code path.
_TOKEN_CHECK = "card_token IS NULL OR status IN ('awaiting_otp', 'pending')"


def upgrade() -> None:
    op.add_column(
        "order_payments",
        sa.Column("card_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "order_payments", sa.Column("card_masked", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "order_payments", sa.Column("card_last4", sa.String(length=4), nullable=True)
    )
    op.add_column(
        "order_payments", sa.Column("card_brand", sa.String(length=24), nullable=True)
    )
    op.add_column("order_payments", sa.Column("card_token", sa.Text(), nullable=True))
    op.add_column(
        "order_payments",
        sa.Column("card_token_key_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "order_payments",
        sa.Column(
            "otp_attempts",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "order_payments",
        sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_payments",
        sa.Column("otp_resend_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_payments", sa.Column("otp_sent_to", sa.String(length=32), nullable=True)
    )

    op.create_check_constraint(
        "open_attempts_hold_the_token", "order_payments", _TOKEN_CHECK
    )
    op.create_check_constraint("otp_attempts", "order_payments", "otp_attempts >= 0")


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_order_payments_otp_attempts"), "order_payments", type_="check"
    )
    op.drop_constraint(
        op.f("ck_order_payments_open_attempts_hold_the_token"),
        "order_payments",
        type_="check",
    )

    for column in (
        "otp_sent_to",
        "otp_resend_after",
        "otp_expires_at",
        "otp_attempts",
        "card_token_key_version",
        "card_token",
        "card_brand",
        "card_last4",
        "card_masked",
        "card_id",
    ):
        op.drop_column("order_payments", column)
