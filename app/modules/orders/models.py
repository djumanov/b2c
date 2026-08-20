"""An order: one confirmed GTS booking a customer owns.

The row is written **after** GTS confirms the booking — a deliberate choice:
no money moves at booking time, and a hold GTS granted during a timeout
expires on its own, so the simplest possible flow (one INSERT on success,
nothing on failure) is enough.

Two kinds of fields live side by side. The columns are what we *work* with —
list screens, ownership, and the handles the coming payment/ticketing/cancel
steps will need (``gts_order_number`` is what every later GTS call takes).
``gts_response`` is what we *keep*: GTS's full answer, verbatim, because GTS
spells its answers inconsistently and whatever field the next feature needs is
in there already. Commission fields inside it are stripped on the way out to
the client, never from the stored copy (``schemas.py``).
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.money import currency_column, money_column
from app.db.base import Entity


class OrderStatus(enum.StrEnum):
    """Our vocabulary, not GTS's — the GTS code rides in ``gts_status``.

    Only ``BOOKED`` is written today; the rest are declared now so the
    payment and ticketing iterations extend behaviour, not the contract.
    """

    BOOKED = "booked"
    PAID = "paid"
    TICKETED = "ticketed"
    CANCELLED = "cancelled"


def _status_check() -> CheckConstraint:
    values = ", ".join(f"'{status}'" for status in OrderStatus)
    return CheckConstraint(f"status IN ({values})", name="order_status")


class Order(Entity):
    """One booking. An ``Entity``, but ``deleted_at`` is never written —

    an order is a financial record, and the column is honoured on reads only
    so history cannot quietly disappear.
    """

    __tablename__ = "orders"
    __table_args__ = (
        _status_check(),
        # "My orders", newest first — string names because ``created_at``
        # comes from the timestamp mixin, not this class body.
        Index("ix_orders_customer_created", "customer_id", "created_at"),
    )

    #: No foreign key: ``customers`` is another module, and a cross-module FK
    #: is the database's version of importing its ``models.py`` (the
    #: ``customer_cards`` choice). Never NULL — there is no guest checkout.
    customer_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: ``ProductCode`` value; ``"flight"`` until the other verticals book.
    product: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    #: The search and offer this booking came from. Provenance, not a cache —
    #: search results are never stored (D2), these are just the two IDs.
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: GTS's integer handle — retrieve/ticketing/cancel all take this number.
    gts_order_number: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    gts_order_uid: Mapped[str | None] = mapped_column(String(64))
    #: GTS's own status code, verbatim (``"BO"`` for a fresh booking).
    gts_status: Mapped[str] = mapped_column(String(32), nullable=False)
    pnr: Mapped[str | None] = mapped_column(String(16))

    #: Total to pay, read from the answer best-effort — nullable because a
    #: booking with a confirmed number is recorded even when GTS's price could
    #: not be read; the full breakdown is in ``gts_response``.
    amount: Mapped[Decimal | None] = money_column(nullable=True)
    currency: Mapped[str | None] = currency_column(nullable=True)

    trip_type: Mapped[str | None] = mapped_column(String(8))
    #: For the list screen: ``routes[].direction`` joined, "TAS-IST, IST-TAS".
    route_summary: Mapped[str | None] = mapped_column(String(128))
    passenger_count: Mapped[int | None] = mapped_column(SmallInteger)
    #: When GTS releases the unpaid seat — the payment step's deadline.
    ticket_time_limit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    #: GTS's booking answer, complete and verbatim.
    gts_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


__all__ = ["Order", "OrderStatus"]
