"""customer first name optional

Revision ID: 3b6f21c0d4ae
Revises: 179a9459a8fa
Create Date: 2026-08-10 11:05:00.000000

Registration asks for the address and the password only (API.md §18), so
``customers.first_name`` drops its ``NOT NULL``.

``downgrade`` cannot simply put the constraint back: rows registered while it
was gone have no name, and the installation downgrading is the one that has
them. It fills those in from the address's local part first — a placeholder,
but a reversible migration is worth more here than a pretty name, and the
customer can correct it on the profile screen.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '3b6f21c0d4ae'
down_revision: str | None = '179a9459a8fa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'customers',
        'first_name',
        existing_type=sa.String(length=120),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE customers "
            "SET first_name = split_part(email, '@', 1) "
            "WHERE first_name IS NULL"
        )
    )
    op.alter_column(
        'customers',
        'first_name',
        existing_type=sa.String(length=120),
        nullable=False,
    )
