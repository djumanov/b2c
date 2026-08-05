"""baseline

Revision ID: 5d3706388f39
Revises:
Create Date: 2026-08-05 15:41:54.260266

The empty root of the chain. It creates no tables — the domain modules have not
been built yet — but it gives ``alembic upgrade head`` something to apply, so
the container entrypoint's migration step is exercised from the very first
boot rather than the first time a table appears.

It also fixes the chain's root. Every later revision descends from this one,
which keeps the chain single-headed: a client who skips several versions runs
one linear sequence, with no branch for them to resolve (PROJECT.md D10).
"""

from collections.abc import Sequence

revision: str = "5d3706388f39"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
