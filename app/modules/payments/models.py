"""Saved payment cards — the ``payments`` module's first table.

**A card is an autofill record, not a provider token.** The customer saves a
number and expiry once; no provider takes part in saving it (PROJECT.md D7).
The number is stored **only AES-GCM encrypted** (``pan`` + ``key_version``,
the same pattern integration credentials use) and is opened server-side at
charge time — never in plaintext, never logged, never returned to the client
(PROJECT.md §13). No CVV: neither provider asks for one at charge time, and a
field nobody needs is scope nobody has to defend.

**Why the table lives here and not in ``customers``.** The contract puts the
route under ``/public/profile/cards/`` (API.md §19), a profile path, but the
row is an encrypted payment credential: reading it needs the decryption key and
spending it happens inside a payment. Had it stayed in ``customers``, checkout
would have to import ``customers`` models to read the number — the cycle
ARCHITECTURE.md §4 forbids.

**Uniqueness is keyed on the masked identity.** The ciphertext cannot be a
unique key (AES-GCM uses a random nonce, so the same number encrypts
differently every time), and a PAN hash would be one more PAN-derived copy for
no gain when the plaintext already sits, encrypted, in the row.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity


class CustomerCard(Entity):
    """One card a customer saved for autofill (API.md §19).

    An ``Entity``, so ``DELETE`` is the soft delete API.md §8 promises — and
    the row outlives the ciphertext on purpose: deletion clears ``pan`` but
    keeps the row, so a support question about "the card ending 6604" still
    has an answer.
    """

    __tablename__ = "customer_cards"

    #: No foreign key: ``customers`` is another module, and a cross-module FK is
    #: the database's version of importing its ``models.py`` (ARCHITECTURE.md
    #: §4). Account deletion therefore calls ``service.forget_cards`` explicitly
    #: rather than relying on a cascade.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True
    )

    #: AES-GCM ciphertext of the card number. Nullable because a deleted card
    #: keeps its row and loses its ciphertext — and only then (see the CHECK).
    pan: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: Derived locally from the number itself, e.g. ``860049******6604``.
    masked_pan: Mapped[str] = mapped_column(String(32), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    #: First six digits. PCI permits storing them in the clear.
    bin: Mapped[str] = mapped_column(String(8), nullable=False)
    #: ``uzcard`` · ``humo`` · ``visa`` · ``mastercard`` · ``NULL`` for a
    #: prefix the local mapping does not know — not an error (API.md §19).
    brand: Mapped[str | None] = mapped_column(String(24), nullable=True)
    expiry_month: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    expiry_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    #: Stamped by the phase-2 charge path; ordering key until then.
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # One card saved once. Keyed on the masked identity rather than on any
        # derivative of the real number — see the module docstring.
        Index(
            "uq_customer_cards_identity_live",
            "customer_id",
            "masked_pan",
            "expiry_month",
            "expiry_year",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "expiry_month BETWEEN 1 AND 12",
            name="expiry_month_range",
        ),
        # A live row always holds ciphertext, a deleted row never does —
        # whichever code path deleted it.
        CheckConstraint(
            "(deleted_at IS NULL) = (pan IS NOT NULL)",
            name="deleted_has_no_pan",
        ),
    )


__all__ = [
    "CustomerCard",
]
