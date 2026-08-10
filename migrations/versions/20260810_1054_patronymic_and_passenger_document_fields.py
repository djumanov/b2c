"""patronymic and passenger document fields

Revision ID: 2d16940ae60b
Revises: 3b6f21c0d4ae
Create Date: 2026-08-10 10:54:27.841683

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.

A patronymic on both person-shaped rows, and the document expiry a saved
passenger had nowhere to put (PROJECT.md §13, API.md §19).

``passengers.birth_date`` gains its ``NOT NULL`` with **no backfill**, unlike
``customers.first_name`` in 3b6f21c0d4ae. There the address's local part was a
placeholder somebody could correct on the profile screen; here there is no
honest placeholder, and an invented date is the kind that gets printed on a
ticket. An installation still holding rows without one is told so by column
name and decides for itself — nothing is guessed and nothing is dropped.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = '2d16940ae60b'
down_revision: str | None = '3b6f21c0d4ae'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'customers',
        sa.Column('middle_name', sa.String(length=120), nullable=True),
    )
    op.add_column(
        'passengers',
        sa.Column('middle_name', sa.String(length=120), nullable=True),
    )
    op.add_column(
        'passengers',
        sa.Column('document_expiry_date', sa.Date(), nullable=True),
    )
    op.alter_column(
        'passengers',
        'birth_date',
        existing_type=sa.Date(),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'passengers',
        'birth_date',
        existing_type=sa.Date(),
        nullable=True,
    )
    op.drop_column('passengers', 'document_expiry_date')
    op.drop_column('passengers', 'middle_name')
    op.drop_column('customers', 'middle_name')
