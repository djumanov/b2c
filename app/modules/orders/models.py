"""An order: one confirmed GTS booking a customer owns, and what became of it.

The row is written **after** GTS confirms the booking — a deliberate choice:
no money moves at booking time, and a hold GTS granted during a timeout
expires on its own, so the simplest possible flow (one INSERT on success,
nothing on failure) is enough.

**Three lifecycles, one row.** What happens to an order after booking is three
separate stories that a single ``status`` column could only tell by inventing
combined states (``paid_but_ticketing_failed``…). So each story has its own
column and its own small vocabulary:

* ``status`` — is the booking alive? ``booked`` or ``cancelled``.
* ``payment_status`` — has the customer paid, and has the money come back?
* ``ticketing_status`` — has GTS issued the ticket?

``lifecycle.py`` owns which moves are allowed between them; nothing else may
write these columns. The UI's single label (``stage``) is derived there too,
never stored.

Two kinds of fields live side by side. The columns are what we *work* with —
list screens, ownership, and the handles the payment/ticketing steps need
(``gts_order_number`` is what every later GTS call takes). ``gts_response`` is
what we *keep*: GTS's full answer, verbatim, refreshed every time the order is
read back from GTS, because GTS spells its answers inconsistently and whatever
field the next feature needs is in there already. Commission fields inside it
are stripped on the way out to the client, never from the stored copy
(``schemas.py``).
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
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.money import currency_column, money_column
from app.db.base import Base, Entity
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class OrderStatus(enum.StrEnum):
    """Is the booking alive? Our vocabulary — the GTS code rides in ``gts_status``."""

    BOOKED = "booked"
    CANCELLED = "cancelled"


class PaymentStatus(enum.StrEnum):
    """Where the customer's money stands.

    ``failed`` means "the last attempt failed" — a new attempt may open. The
    refund states are marked by staff after the money was returned through the
    provider's own cabinet; nothing here calls a provider for a refund.
    """

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDING = "refunding"
    REFUNDED = "refunded"
    REFUND_FAILED = "refund_failed"


class TicketingStatus(enum.StrEnum):
    """Where the ticket stands at GTS.

    ``processing`` covers both "GTS said wait" and "we asked and do not know
    the answer yet" — the sweep settles either by reading the order back.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    TICKETED = "ticketed"
    FAILED = "failed"


class CancelReason(enum.StrEnum):
    CUSTOMER = "customer"
    #: GTS released the unpaid hold after ``ticket_time_limit``.
    EXPIRED = "expired"
    STAFF = "staff"


def _values_check(
    column: str, values: type[enum.StrEnum], name: str
) -> CheckConstraint:
    joined = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({joined})", name=name)


class Order(Entity):
    """One booking. An ``Entity``, but ``deleted_at`` is never written —

    an order is a financial record, and the column is honoured on reads only
    so history cannot quietly disappear.
    """

    __tablename__ = "orders"
    __table_args__ = (
        _values_check("status", OrderStatus, "order_status"),
        _values_check("payment_status", PaymentStatus, "payment_status"),
        _values_check("ticketing_status", TicketingStatus, "ticketing_status"),
        CheckConstraint(
            "cancel_reason IS NULL "
            "OR cancel_reason IN ('customer', 'expired', 'staff')",
            name="cancel_reason",
        ),
        # A cancelled order always knows when; a live one never carries a stamp.
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="cancelled_consistent",
        ),
        # "My orders", newest first — string names because ``created_at``
        # comes from the timestamp mixin, not this class body.
        Index("ix_orders_customer_created", "customer_id", "created_at"),
        # The sweep's two questions, each answered by a small partial index:
        # "which orders are waiting on GTS?" and "which unpaid holds are old?"
        Index(
            "ix_orders_processing",
            "ticketing_requested_at",
            postgresql_where=text("ticketing_status = 'processing'"),
        ),
        Index(
            "ix_orders_unpaid_deadline",
            "ticket_time_limit_at",
            postgresql_where=text(
                "status = 'booked' AND payment_status IN ('pending', 'failed')"
            ),
        ),
    )

    #: No foreign key: ``customers`` is another module, and a cross-module FK
    #: is the database's version of importing its ``models.py`` (the
    #: ``customer_cards`` choice). Never NULL — there is no guest checkout.
    customer_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: ``ProductCode`` value; ``"flight"`` until the other verticals book.
    product: Mapped[str] = mapped_column(String(16), nullable=False)

    # --- the three lifecycles (written only through ``lifecycle.transition``) --
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payment_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    ticketing_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'")
    )
    cancel_reason: Mapped[str | None] = mapped_column(String(16))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ticketed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- ticketing bookkeeping -----------------------------------------------
    #: When we last asked GTS to issue the ticket — the clock the sweep's
    #: waiting rules run on. NULL until the first request.
    ticketing_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: When the sweep last read the order back from GTS — waiting on a ticket
    #: or checking whether an unpaid hold is still alive. The sweep's throttle.
    gts_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: How many times the ticketing request was sent. Bounded by the sweep:
    #: a request GTS never confirms is re-sent at most once, then left to staff.
    ticketing_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    #: GTS's own words for why the ticket was not issued — for support, and
    #: for the customer's message when it is safe to show.
    ticketing_error: Mapped[str | None] = mapped_column(Text)

    #: The search and offer this booking came from. Provenance, not a cache —
    #: search results are never stored (D2), these are just the two IDs.
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: GTS's integer handle — retrieve/ticketing/cancel all take this number.
    gts_order_number: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    gts_order_uid: Mapped[str | None] = mapped_column(String(64))
    #: GTS's own status code, verbatim (``"BO"`` for a fresh booking, ``"TI"``
    #: once ticketed) — refreshed on every read-back.
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
    #: When GTS releases the unpaid seat — the payment step's deadline. Best
    #: effort (GTS spells it three ways); GTS's live ``status`` is the truth.
    ticket_time_limit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    #: GTS's order, complete and verbatim — the latest read-back.
    gts_response: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class OrderEvent(Base, UUIDPrimaryKeyMixin):
    """One line of an order's history: what moved, from where, who did it.

    Append-only, so no timestamps mixin and no soft delete — a record of
    "the payment was marked refunded by staff on Tuesday" is not something
    anybody is allowed to edit. Written in the **same transaction** as the
    change it describes (``lifecycle.transition``), so the history can never
    disagree with the row.

    ``data`` holds codes and ids only — a GTS status code, an attempt id —
    never a raw upstream body (that lives in ``orders.gts_response``) and
    never anything about a card.
    """

    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_order_created", "order_id", "created_at"),)

    #: No foreign key on purpose, like every cross-row reference here: the
    #: order is never deleted, and an index is all the lookup needs.
    order_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: ``"<lifecycle>.<new value>"`` — ``payment.paid``, ``ticketing.failed``,
    #: ``order.created`` — so a filter needs no joins and no parsing.
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    from_value: Mapped[str | None] = mapped_column(String(16))
    to_value: Mapped[str | None] = mapped_column(String(16))
    #: ``customer`` · ``system`` (the sweep) · ``staff:<uuid>``.
    actor: Mapped[str] = mapped_column(String(48), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: The request this change happened in — the same id that travels to GTS.
    request_id: Mapped[str | None] = mapped_column(String(64))


class AttemptStatus(enum.StrEnum):
    """Where one conversation with the provider stands.

    ``started`` — the row exists (it is the claim) and the code may be out;
    ``confirming`` — the charge was sent and the answer is not back, the one
    state in which nothing about the order may change; ``paid`` / ``failed``
    — the provider answered; ``abandoned`` — superseded by a newer attempt
    or given up on before any charge.
    """

    STARTED = "started"
    CONFIRMING = "confirming"
    PAID = "paid"
    FAILED = "failed"
    ABANDONED = "abandoned"


class PaymentAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One try at charging one order — the evidence of a provider conversation.

    Lives beside the order, not in ``payments``: its whole life happens under
    the order's row lock and commits in the order's transactions, and a table
    owned by another module would have to be written through a service that
    may never commit. The ``payments`` module keeps the cards and the
    provider; the attempt is the order's.

    **At most one open attempt per order, and at most one paid** — the
    partial unique index below is the structural guard against charging
    twice, whatever the code above it does. The row is inserted **before**
    the provider is called, so two concurrent starts collide on the index
    rather than both sending a code.
    """

    __tablename__ = "payment_attempts"
    __table_args__ = (
        _values_check("status", AttemptStatus, "attempt_status"),
        Index(
            "uq_payment_attempts_open",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('started', 'confirming', 'paid')"),
        ),
        # The sweep's question: "which charges have been in flight too long?"
        Index(
            "ix_payment_attempts_confirming",
            "updated_at",
            postgresql_where=text("status = 'confirming'"),
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: ``payme`` · ``click`` · ``sandbox``.
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = money_column(nullable=False)
    currency: Mapped[str] = currency_column(nullable=False)

    #: The saved card used, if one was; a typed card leaves only ``last4``.
    card_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    card_last4: Mapped[str | None] = mapped_column(String(4))

    #: The provider's handle, AES-GCM encrypted: for Payme it is a token that
    #: can charge the card again, so it is a secret at rest like a password.
    provider_reference: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[int | None] = mapped_column(Integer)
    #: Masked number the code was sent to — for the screen, nothing more.
    phone_hint: Mapped[str | None] = mapped_column(String(32))
    #: What the adapter chose to keep of the provider's answers, scrubbed.
    provider_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    #: The provider's reason on ``failed``.
    error: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What the panel wrote for one stage — the overrides, never the defaults.

    One row per ``lifecycle.Stage`` value, created on first read like every
    other keyed setting (``payment_providers``): no data migration, and a
    stage added in a new release gets its row the first time anybody looks.
    ``text`` holds only the languages staff actually wrote; a language they
    never touched — or cleared — reads from ``messages.DEFAULTS``.
    """

    __tablename__ = "order_messages"

    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    text: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


__all__ = [
    "AttemptStatus",
    "CancelReason",
    "Order",
    "OrderEvent",
    "OrderMessage",
    "OrderStatus",
    "PaymentAttempt",
    "PaymentStatus",
    "TicketingStatus",
]
