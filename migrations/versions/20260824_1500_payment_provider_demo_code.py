"""payment provider demo code

Revision ID: c8f2d4a6e357
Revises: b7e1c9d3f246
Create Date: 2026-08-24 15:00:00.000000

``PaymentProviderCode`` gains ``demo`` — a panel provider that charges nobody
and accepts one static code (``providers/payments/demo.py``). The column is a
non-native enum, so the value set lives in a CHECK constraint that must be
widened by hand: ``migrations/env.py`` filters check constraints out of
autogenerate, and adding an enum member produces an empty diff (the
``upload_purpose`` precedent in ``20260806_1724_payment_provider_settings.py``).

No row is inserted here. ``repository.payment_providers`` creates the ``demo``
row on first read, switched off — the same path that seeds a fresh
installation.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8f2d4a6e357"
down_revision: str | None = "b7e1c9d3f246"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CODE_CHECK = "ck_payment_providers_payment_provider_code"


def _code_in(codes: tuple[str, ...]) -> str:
    return "code IN ({})".format(", ".join(f"'{code}'" for code in codes))


def upgrade() -> None:
    op.drop_constraint(op.f(_CODE_CHECK), "payment_providers", type_="check")
    op.create_check_constraint(
        op.f(_CODE_CHECK), "payment_providers", _code_in(("payme", "click", "demo"))
    )


def downgrade() -> None:
    # A demo row would violate the narrower constraint, so it goes first —
    # settings and all; the release being rolled back to cannot read them.
    op.execute("DELETE FROM payment_providers WHERE code = 'demo'")
    op.drop_constraint(op.f(_CODE_CHECK), "payment_providers", type_="check")
    op.create_check_constraint(
        op.f(_CODE_CHECK), "payment_providers", _code_in(("payme", "click"))
    )
