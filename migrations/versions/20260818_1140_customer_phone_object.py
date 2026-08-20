"""customer phone object

Revision ID: 3c1f9a4d7e02
Revises: 49b3b6e5cc3a
Create Date: 2026-08-18 11:40:00.000000

Clients upgrade on their own schedule and may skip versions (PROJECT.md D10):
keep this revision self-contained and make ``downgrade`` real, not a ``pass``.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "3c1f9a4d7e02"
down_revision: str | None = "49b3b6e5cc3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Both the live row and its pre-scrub archive carry the same shape
#: (``models.DeletedCustomer`` is what ``customers`` looked like).
_TABLES = ("customers", "deleted_customers")

#: GTS's own mask for Uzbekistan, from the EASY_GATEWAY country object. Only
#: written to rows the split below is sure about.
_UZ_MASK = "(##) ###-##-##"

#: ``+998`` or ``998`` followed by exactly nine digits. Deliberately narrow:
#: the old column had no validation at all, so anything looser would be
#: guessing where a country code ends.
_UZ_PHONE = r"^\+?998\d{9}$"


def upgrade() -> None:
    # A flat string could never be handed to GTS's booking body, which takes
    # ``{"phone_code": "998", "phone_number": "…"}`` (API.md §19, GTS.md).
    for table in _TABLES:
        op.add_column(table, sa.Column("phone_code", sa.String(length=8), nullable=True))
        op.add_column(
            table, sa.Column("phone_number", sa.String(length=32), nullable=True)
        )
        op.add_column(table, sa.Column("phone_mask", sa.String(length=32), nullable=True))

        # Uzbek numbers split cleanly, and they are effectively all of them.
        op.execute(
            sa.text(
                f"""
                UPDATE {table}
                   SET phone_code = '998',
                       phone_number = regexp_replace(phone, '^\\+?998', ''),
                       phone_mask = :mask
                 WHERE phone ~ :pattern
                """
            ).bindparams(mask=_UZ_MASK, pattern=_UZ_PHONE)
        )

        # Everything else keeps its digits and loses only the country code,
        # which we genuinely cannot infer. Such a profile reads as incomplete
        # until the customer picks a code — honest, rather than a wrong guess
        # sent to GTS.
        op.execute(
            sa.text(
                f"""
                UPDATE {table}
                   SET phone_number = regexp_replace(phone, '\\D', '', 'g')
                 WHERE phone IS NOT NULL
                   AND phone !~ :pattern
                   AND regexp_replace(phone, '\\D', '', 'g') <> ''
                """
            ).bindparams(pattern=_UZ_PHONE)
        )

        op.drop_column(table, "phone")


def downgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("phone", sa.String(length=32), nullable=True))
        # Recombined with the ``+`` the old column carried; a row whose code
        # never came back gives up its bare number rather than a bogus ``+``.
        op.execute(
            f"""
            UPDATE {table}
               SET phone = CASE
                     WHEN phone_code IS NOT NULL AND phone_number IS NOT NULL
                       THEN '+' || phone_code || phone_number
                     ELSE phone_number
                   END
             WHERE phone_code IS NOT NULL OR phone_number IS NOT NULL
            """
        )
        op.drop_column(table, "phone_mask")
        op.drop_column(table, "phone_number")
        op.drop_column(table, "phone_code")
