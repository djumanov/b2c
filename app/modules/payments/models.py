"""Saved payment cards — the ``payments`` module's first table.

**Why a card lives here and not in ``customers``.** The contract puts the route
under ``/public/profile/cards/`` (API.md §19), which is a profile path, and
ARCHITECTURE.md §5 used to name ``customers`` as the owner because of it. But the
row is a provider token: writing it needs the adapter, reading it needs the
decryption key, and charging it happens inside a payment. Had the table stayed
in ``customers``, the checkout path would have to import ``customers`` models to
read a token — the cycle §4 forbids. The path is the contract; the module is the
architecture.

**What is not here.** No card number, in any form. Not a column, not a hash: a
hash of a PAN is still PAN-derived data, and it would put a stored-account-data
control back on the one table this design exists to keep clean. The uniqueness
that stops a customer saving the same card twice is keyed on the provider's
**masked** string instead. No CVV either — neither provider asks for one, and a
field nobody needs is scope nobody has to defend (PROJECT.md §13).

**No ``code_hash``, unlike ``EmailOtp``.** The confirmation code is issued by the
provider and checked by the provider; there is no correct value on this side to
compare against. ``verify_attempts`` is still here, and it is not security
theatre: it stops a customer's client hammering the provider's endpoint on the
installation's merchant credentials.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
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
from app.providers.payments.base import CardStatus, PaymentProviderCode

#: VARCHAR + CHECK rather than a native enum, the rule every enum column in this
#: codebase follows: altering a native enum behaves differently across server
#: versions, and clients run migrations unattended (PROJECT.md D10).
#:
#: Declared here rather than imported from ``integrations.models``, which has an
#: identical column — importing it would be reaching into another module's
#: models, which is the thing ARCHITECTURE.md §4 rules out. Both are projections
#: of the same enum in ``providers``, and that is the shared source.
PROVIDER_CODE_COLUMN = Enum(
    PaymentProviderCode,
    name="payment_provider_code",
    native_enum=False,
    create_constraint=True,
    length=16,
    values_callable=lambda enum: [member.value for member in enum],
)

CARD_STATUS_COLUMN = Enum(
    CardStatus,
    name="card_status",
    native_enum=False,
    create_constraint=True,
    length=24,
    values_callable=lambda enum: [member.value for member in enum],
)


class CustomerCard(Entity):
    """One card a customer saved, held as a provider token (API.md §19).

    An ``Entity``, so ``DELETE`` is the soft delete API.md §8 promises — and the
    row outlives the token on purpose: ``forget_cards`` clears ``token`` but
    keeps the row, so a support question about "the card ending 6604" still has
    an answer.
    """

    __tablename__ = "customer_cards"

    #: No foreign key: ``customers`` is another module, and a cross-module FK is
    #: the database's version of importing its ``models.py`` (ARCHITECTURE.md
    #: §4). The same choice ``payment_providers.logo_id`` already makes. Account
    #: deletion therefore calls ``service.forget_cards`` explicitly rather than
    #: relying on a cascade.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True
    )
    provider: Mapped[PaymentProviderCode] = mapped_column(
        PROVIDER_CODE_COLUMN, nullable=False
    )
    status: Mapped[CardStatus] = mapped_column(CARD_STATUS_COLUMN, nullable=False)

    #: AES-GCM ciphertext of the **provider's token** — never a card number.
    #: Nullable because a removed card keeps its row and loses its token.
    token: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    #: The provider's own masked string, e.g. ``860049******6604``.
    masked_pan: Mapped[str] = mapped_column(String(32), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    #: First six digits. PCI permits storing them; the provider returns them.
    bin: Mapped[str | None] = mapped_column(String(8), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(24), nullable=True)
    expiry_month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    expiry_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    #: The customer's own name for the card — the only field they may edit.
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    #: What the provider says it texted, for "code sent to 9989**1234".
    phone_masked: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verify_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    otp_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    otp_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # "Exactly one default" as a database guarantee, the same reasoning
        # ``uq_gts_credentials_active`` gives: two tabs pressing the button in
        # the same millisecond must not produce two defaults, and the service
        # cannot see the other transaction. ``soft_delete_unique_index`` is not
        # enough here — it emits only ``WHERE deleted_at IS NULL``, and this
        # index must also exclude the non-default rows.
        Index(
            "uq_customer_cards_default_live",
            "customer_id",
            unique=True,
            postgresql_where=text("is_default AND deleted_at IS NULL"),
        ),
        # One card saved once. Keyed on the masked pan rather than on any
        # derivative of the real number — see the module docstring. The same
        # card at two providers is two rows, and that is correct: tokens are
        # merchant-scoped and cannot be swapped.
        Index(
            "uq_customer_cards_identity_live",
            "customer_id",
            "provider",
            "masked_pan",
            "expiry_month",
            "expiry_year",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status = 'active'"),
        ),
        CheckConstraint(
            "expiry_month IS NULL OR expiry_month BETWEEN 1 AND 12",
            name="expiry_month_range",
        ),
        CheckConstraint("verify_attempts >= 0", name="verify_attempts_non_negative"),
        # The belt to the service's braces: a removed card must not keep a
        # usable token, whichever code path removed it.
        CheckConstraint(
            "status <> 'removed' OR token IS NULL", name="removed_has_no_token"
        ),
    )

    @property
    def is_active(self) -> bool:
        return self.status is CardStatus.ACTIVE


__all__ = [
    "CARD_STATUS_COLUMN",
    "PROVIDER_CODE_COLUMN",
    "CustomerCard",
]
