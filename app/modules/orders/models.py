"""One order: what the customer bought, as GTS answered it (API.md §21).

The row exists for **ownership**, not for understanding. GTS's booking answer
is kept verbatim in ``gts_response`` and is never interpreted — the client
gets the same bytes back that the passthrough returned. Only four values are
lifted out into columns, and only because an index is needed there:
``gts_order_id`` and ``status`` from the answer, ``request_id`` and
``offer_id`` from the request that produced it.

**The booking request's ``passengers`` block is not stored.** It carries
passport numbers, and PROJECT.md §13 promises an order is anonymised when the
account is deleted — a promise no one can keep about a blob whose shape is
unknown. Travellers reach the ``passengers`` table through ``save_passenger``
instead (API.md §19), so nothing is lost.

``status`` holds **GTS's own code** — ``BO``/``PW``/``TI``/``TE``/``CB``/
``VO``/``RF``/``PRF`` (GTS.md §4). The canonical enum of API.md §21 arrives
with the saga, because its first real consumer is the cancel/refund rules
(PHASES.md §4, chunk 9). Until then there is nothing to map onto.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity
from app.providers.products.base import ProductCode


def _product_check() -> CheckConstraint:
    values = ", ".join(f"'{code}'" for code in ProductCode)
    return CheckConstraint(f"product IN ({values})", name="order_product")


class Order(Entity):
    """A booking GTS confirmed, filed under the customer who made it."""

    __tablename__ = "orders"
    __table_args__ = (
        _product_check(),
        # The list query: this customer's orders, newest first.
        Index("ix_orders_customer_created", "customer_id", "created_at"),
        # One GTS order is one row. Partial, so the rows we could not read an
        # identifier out of (NULL) do not collide with each other — and so a
        # soft-deleted row does not block re-recording the same booking.
        Index(
            "uq_orders_gts_order_id_live",
            "gts_order_id",
            unique=True,
            postgresql_where=text("gts_order_id IS NOT NULL AND deleted_at IS NULL"),
        ),
    )

    #: No foreign key: ``customers`` is another module, and a cross-module FK
    #: is the database's version of importing its ``models.py``
    #: (ARCHITECTURE.md §4) — the ``customer_cards`` choice. Never null:
    #: there is no guest purchase (PROJECT.md D4).
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True
    )

    #: The vertical, from the adapter that served the booking — ``flight``
    #: today, the other four in phase 3.
    product: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: GTS's own order number, e.g. ``"1250"`` — the handle ``cancel/`` takes.
    #: Nullable because the field name is not yet confirmed against live GTS
    #: (STATUS.md §8): a booking whose answer we could not read is still
    #: recorded, rather than lost.
    gts_order_id: Mapped[str | None] = mapped_column(String(64))

    #: GTS's status code, verbatim. **No CHECK constraint on purpose** — this
    #: is GTS's vocabulary, not ours, and a new code on their side must not
    #: stop us recording a booking that really happened.
    status: Mapped[str | None] = mapped_column(String(16), index=True)

    #: The search and the offer this order came from. Kept for support and for
    #: the reprice steps that arrive with the saga; not a cache of either — the
    #: offer itself is stored nowhere (ARCHITECTURE.md D2, §10).
    request_id: Mapped[str | None] = mapped_column(String(64))
    offer_id: Mapped[str | None] = mapped_column(String(64))

    #: GTS's booking answer as it arrived. Opaque by design: we read the two
    #: columns above out of it and interpret nothing else.
    gts_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    #: Stamped when our own ``cancel/`` came back from GTS. ``status`` carries
    #: whatever GTS called it; this says when we asked.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = ["Order"]
