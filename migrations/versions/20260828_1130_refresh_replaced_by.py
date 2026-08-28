"""refresh tokens record what replaced them

Revision ID: f2a7c5b8e491
Revises: e1b4c7d9a583
Create Date: 2026-08-28 11:30:00.000000

``replaced_by_jti`` on both refresh token tables. Rotation writes the ``jti``
it issued in place of the row it retires, and that is the only thing that tells
"rotation replaced this a moment ago" apart from "a logout, a password change
or a block ended it". The first is a race — two tabs, a retried request — and
is served; the second is refused (``core.security.is_rotation_race``).

Nullable, and left NULL for every existing row. A token already revoked before
this migration ran has no successor recorded, so it reads as ended on purpose,
which is the safe direction: the worst it costs is one re-login for a session
that was mid-rotation at deploy time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a7c5b8e491"
down_revision: str | None = "e1b4c7d9a583"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("staff_refresh_tokens", "customer_refresh_tokens")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table, sa.Column("replaced_by_jti", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "replaced_by_jti")
