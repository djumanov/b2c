"""One order and its history (order-system/03-design.md §3.4).

The order row is the **aggregate root**: the state machine lives on it, the
customer owns it through it, and every later step — payment, ticketing, refund
— reads its status before doing anything. Nothing else in the system is allowed
to decide where an order is.

Three things it deliberately keeps inside itself rather than in tables of their
own, and each is a decision with a number in the design document:

* **The booking.** GTS's ``order_number``, ``order_uid``, PNR, raw status and
  ticket deadline are columns here. One order has exactly one reservation; a
  table for it would add a join and nothing else.
* **The travellers.** ``travelers`` is JSONB in **our** shape, not GTS's. That
  is the whole point: an answer whose shape we do not control cannot be
  anonymised, which is why the promise in PROJECT.md §13 could not be kept
  while the passengers lived inside the provider blob (``O10``).
* **The queue.** There is no outbox table. ``next_attempt_at`` is written in
  the same transaction as the status change, and a poller picks up whatever is
  due; the task is also sent straight after the commit, so the poller is the
  safety net rather than the mechanism (``O13``).

``status`` is ours and therefore carries a ``CHECK``. The old column held GTS's
vocabulary and could not: a code they invented next week would have stopped a
real booking from being recorded. Their code still arrives and still matters
for diagnosis, so it is kept beside ours in ``provider_status``.

``deleted_at`` is inherited and **never written**. An order is a financial
document (PROJECT.md §13): it is anonymised, not deleted.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.money import currency_column, money_column
from app.db.base import Base, Entity
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.orders.states import (
    ActorType,
    EventAction,
    OrderStatus,
    RefundKind,
    RefundState,
)
from app.providers.payments.base import (
    PaymentProviderCode,
    TransactionStatus,
)
from app.providers.products.base import ProductCode

#: Feeds ``order_no``. A sequence rather than a count of rows: two concurrent
#: bookings must not be handed the same number, and ``nextval`` is the only
#: thing that guarantees it without a lock.
ORDER_NO_SEQUENCE = "orders_order_no_seq"


def _in_values(column: str, values: Iterable[StrEnum], *, name: str) -> CheckConstraint:
    """``column IN (…)`` from an enum — written by hand on purpose.

    Alembic's autogenerate does not emit ``CHECK`` constraints, so every one of
    them has to be repeated in the migration by hand and dropped in its
    ``downgrade``. Building the SQL here keeps the two copies honest: they read
    from the same enum.
    """
    literals = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({literals})", name=name)


class Order(Entity):
    """What a customer bought, and where it has got to."""

    __tablename__ = "orders"
    __table_args__ = (
        _in_values("product", ProductCode, name="product"),
        _in_values("status", OrderStatus, name="status"),
        # Price arrives with the provider's answer. Three states can exist
        # without one and no others: the row that has not asked yet, the one
        # that was refused, and the one whose answer could not be read. Once an
        # order is holding a seat, an amount it cannot name is a bug.
        CheckConstraint(
            "status IN ('created', 'failed', 'needs_attention')"
            " OR (amount_total IS NOT NULL AND currency IS NOT NULL)",
            name="priced_once_booked",
        ),
        CheckConstraint(
            "amount_paid >= 0 AND amount_refunded >= 0 "
            "AND amount_refunded <= amount_paid",
            name="amounts",
        ),
        # The list query: this customer's orders, newest first.
        Index("ix_orders_customer_created", "customer_id", "created_at"),
        # One provider reservation is one row. Partial so the rows that have no
        # number yet (``created``, ``failed``) do not collide with each other.
        Index(
            "uq_orders_provider_number_live",
            "provider",
            "provider_order_number",
            unique=True,
            postgresql_where=text(
                "provider_order_number IS NOT NULL AND deleted_at IS NULL"
            ),
        ),
        # The real duplicate guard. The Redis record in ``api/idempotency.py``
        # is a cache; a flush or an eviction would let a retry through, and on
        # this path a retry is a second real seat (``O8``).
        #
        # Per customer, not global: keys are chosen by clients, so two of them
        # choosing the same string must not make one customer's booking
        # readable — or claimable — by the other.
        Index(
            "uq_orders_customer_id_idempotency_key",
            "customer_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # The two sweeps. Both partial, so they stay cheap as the table grows:
        # the deadline sweep only ever looks at unpaid holds, and the poller
        # only at rows with work waiting.
        Index(
            "ix_orders_ticket_deadline",
            "ticket_time_limit_at",
            postgresql_where=text("status = 'booked'"),
        ),
        Index(
            "ix_orders_due",
            "next_attempt_at",
            postgresql_where=text("next_attempt_at IS NOT NULL"),
        ),
    )

    #: Human-readable and ours: ``B2C-2608-000123``. GTS's number does not
    #: exist until the booking succeeds, and support needs one handle that
    #: works for every order including the ones that failed.
    order_no: Mapped[str] = mapped_column(String(24), nullable=False, unique=True)

    #: No foreign key: ``customers`` is another module, and a cross-module FK
    #: is the database's version of importing its ``models.py``
    #: (ARCHITECTURE.md §4). Never null — there is no guest purchase (D4).
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False, index=True
    )

    product: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: **Ours** (``states.OrderStatus``), which is why the CHECK above exists.
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)

    # --- the reservation, as the provider names it --------------------------

    #: Which upstream holds it. One value today; the column exists so a direct
    #: supplier integration would not need a migration to be told apart.
    provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="gts", server_default="gts"
    )
    #: GTS's ``order_number`` — the handle ``cancel``, ``retrieve`` and
    #: ``ticketing`` all take. An integer upstream, text here: API.md §1 keeps
    #: identifiers textual, and an id is never arithmetic.
    provider_order_number: Mapped[str | None] = mapped_column(String(64))
    provider_order_uid: Mapped[str | None] = mapped_column(String(64))
    #: ``gds_pnr``. Indexed because the admin search is by it (API.md §31).
    provider_pnr: Mapped[str | None] = mapped_column(String(32), index=True)
    #: GTS's own code, verbatim — ``BO``/``PW``/``TI``/… Kept beside ours so a
    #: disagreement between the two is visible rather than lost in translation.
    provider_status: Mapped[str | None] = mapped_column(String(16))
    #: The provider's latest answer, unread. Its history is in
    #: ``order_events.meta``; this is the convenience copy the detail endpoint
    #: returns.
    provider_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    #: The search and the offer this order came from. Not a cache of either —
    #: the offer itself is stored nowhere (D2).
    request_id: Mapped[str | None] = mapped_column(String(64))
    offer_id: Mapped[str | None] = mapped_column(String(64))

    # --- money ---------------------------------------------------------------

    currency: Mapped[str | None] = currency_column()
    amount_total: Mapped[Decimal | None] = money_column()
    amount_paid: Mapped[Decimal] = money_column(
        nullable=False, default=Decimal("0"), server_default=text("0")
    )
    amount_refunded: Mapped[Decimal] = money_column(
        nullable=False, default=Decimal("0"), server_default=text("0")
    )

    # --- what was bought -----------------------------------------------------

    #: Travellers in **our** shape, one object each, with the ticket number on
    #: the person it belongs to. See the module docstring for why this is not a
    #: table and not the provider's blob.
    travelers: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: The journey, product-agnostic: a flight's departure, a hotel's check-in,
    #: a tour's start. Reports and reminders read these, never the blob.
    travel_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: ``TAS-IST-TAS`` — enough to render a list row without opening anything.
    route_summary: Mapped[str | None] = mapped_column(String(128))

    # --- deadlines and the queue ---------------------------------------------

    #: When the provider stops holding the seat. Everything about the payment
    #: window and the ticketing deadline is derived from it.
    ticket_time_limit_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: ``Idempotency-Key`` of the request that created this order.
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    #: Attempts at the *current* step, reset when the step changes.
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    #: When the next background step is due. ``NULL`` means nothing is pending
    #: — this column **is** the queue (``O13``).
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- why it ended where it did -------------------------------------------

    #: ``customer`` · ``admin`` · ``timelimit`` · ``payment_failed``.
    cancellation_reason: Mapped[str | None] = mapped_column(String(32))
    failure_message: Mapped[str | None] = mapped_column(Text)
    attention_reason: Mapped[str | None] = mapped_column(String(64))

    # --- when it got there ---------------------------------------------------

    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ticketed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderEvent(Base, UUIDPrimaryKeyMixin):
    """One status change. Append-only, like the audit journal.

    Deliberately not an ``Entity``: a history whose rows can be edited or
    soft-deleted is not a history. It has a ``created_at`` and nothing else
    from the mixins.

    ``meta`` is where the provider's raw answer for **that step** lands. It is
    the reason there is no separate snapshot table: the history had to exist
    anyway, and attaching the evidence to the event that produced it is both
    smaller and easier to read than a second table keyed on the same order
    (``O9``).
    """

    __tablename__ = "order_events"
    __table_args__ = (
        _in_values("actor_type", ActorType, name="actor_type"),
        _in_values("action", EventAction, name="action"),
        Index("ix_order_events_order_created", "order_id", "created_at"),
    )

    #: A real foreign key: same module, same lifetime, and an event without its
    #: order is meaningless (ARCHITECTURE.md §4 forbids the *cross*-module one).
    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: ``clock_timestamp()`` and not ``now()``: ``now()`` is the *transaction*
    #: start, so several events written in one transaction would share a
    #: timestamp and the history would have no defined order. The wall clock is
    #: what an append-only log wants anyway.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )

    #: ``NULL`` on the first event — the order had no status before it existed.
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)

    #: Values, not references — the same reasoning as ``audit_log``: the
    #: history has to stay readable after the staff row is gone.
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    actor_label: Mapped[str | None] = mapped_column(String(128))

    reason: Mapped[str | None] = mapped_column(String(255))
    #: The provider's raw answer for this step, or whatever else explains it.
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    #: Which attempt this was, when the step is one that retries.
    attempt: Mapped[int | None] = mapped_column(Integer)


class OrderPayment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One **attempt** at paying for an order.

    Not a payment intent: the amount and the currency the customer owes live on
    the order, because one order carries one of each. What varies is the trying
    — a card refused and then a second card accepted is two rows here and one
    order there (``O12``). Several attempts may exist; only one may be **open**,
    and the partial unique index below is what says so.

    No soft delete. An attempt is evidence of a conversation with a payment
    provider, and evidence that can be deleted is not evidence — the same
    reasoning ``order_events`` and ``audit_log`` state.

    **The row is committed before the provider is called.** If the process dies
    mid-call, an attempt with no ``provider_ref`` is what tells reconciliation
    where to look; with nothing written, a charge could exist that nobody could
    connect to an order (ARCHITECTURE.md §8).
    """

    __tablename__ = "order_payments"
    __table_args__ = (
        _in_values("provider", PaymentProviderCode, name="provider"),
        _in_values("status", TransactionStatus, name="status"),
        CheckConstraint("amount > 0", name="amount"),
        # The provider's token is a live payment credential: it can be charged.
        # It exists only while the attempt is open, and this is what makes
        # "erased when the attempt closes" a property of the table rather than a
        # promise of the code — the same shape ``customer_cards`` uses to pair a
        # deleted row with a null ciphertext.
        CheckConstraint(
            "card_token IS NULL OR status IN ('awaiting_otp', 'pending')",
            name="open_attempts_hold_the_token",
        ),
        CheckConstraint("otp_attempts >= 0", name="otp_attempts"),
        # **This index is why a repeated callback cannot settle twice.** Not the
        # handler's logic — logic can be raced, a unique index cannot (X6).
        # Partial because the reference arrives with the provider's first
        # callback, and until then there is nothing to be unique about.
        Index(
            "uq_order_payments_provider_ref",
            "provider",
            "provider_ref",
            unique=True,
            postgresql_where=text("provider_ref IS NOT NULL"),
        ),
        Index("ix_order_payments_order_created", "order_id", "created_at"),
        # **One open attempt per order.** The card flow spans three requests, so
        # a customer who wanders off and comes back must land on the attempt
        # they left rather than open a second one against the same order. Doing
        # that in the handler would be a read followed by a write with a gap in
        # the middle; the index has no gap. Same shape as
        # ``uq_order_refunds_open``, and the reason ``O8`` gives for putting the
        # real guard in the database.
        Index(
            "uq_order_payments_open",
            "order_id",
            unique=True,
            postgresql_where=text(
                "status IN ('awaiting_card', 'awaiting_otp', 'pending')"
            ),
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The provider's own id for this attempt — Payme's receipt ``_id``, Click's
    #: ``payment_id``. ``NULL`` until the provider names it, which for a hosted
    #: redirect is the first callback rather than the call that starts it.
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: Copied from the order rather than referenced, because it is what was
    #: *asked for* — an order repriced afterwards must not silently rewrite the
    #: history of what a customer was charged.
    amount: Mapped[Decimal] = money_column(nullable=False)
    currency: Mapped[str] = currency_column(nullable=False)

    #: Which saved card paid, when one did. **No foreign key**: ``customer_cards``
    #: belongs to ``payments`` and a constraint across the two would be the
    #: database importing another module's models (ARCHITECTURE.md §4) — the same
    #: reasoning that leaves ``customer_cards.customer_id`` unconstrained.
    card_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    #: What the receipt shows. Kept here rather than read back through
    #: ``card_id`` because a receipt outlives the card: deleting a saved card
    #: erases its ciphertext, and a paid attempt must still be able to say which
    #: card paid (PROJECT.md §13).
    card_masked: Mapped[str | None] = mapped_column(String(32))
    card_last4: Mapped[str | None] = mapped_column(String(4))
    card_brand: Mapped[str | None] = mapped_column(String(24))
    #: The provider's handle for the card, AES-GCM encrypted — the same pattern
    #: as every other credential at rest. It lives between ``card/`` and
    #: ``confirm/`` and is erased the moment the attempt closes; a CHECK above
    #: enforces that. Not in Redis: an eviction would leave a token nobody could
    #: release and a pending charge nobody could reconcile.
    card_token: Mapped[str | None] = mapped_column(Text)
    card_token_key_version: Mapped[int | None] = mapped_column(Integer)

    #: How many wrong codes this attempt has taken. Counted here rather than in
    #: Redis for the reason X6 gives about idempotency: a ``FLUSHALL`` must not
    #: hand somebody unlimited tries against a live card token.
    otp_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    #: Our own guard, not the provider's — theirs is the one that rules on the
    #: code. Past this the attempt stays open and ``resend-otp/`` recovers it.
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_resend_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Masked by the provider before we ever see it. The customer needs to know
    #: which phone to look at, and nothing here needs the rest of the number.
    otp_sent_to: Mapped[str | None] = mapped_column(String(32))

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    #: Whatever the provider said about this attempt, for diagnosis.
    provider_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class OrderRefund(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Money going back, and who decided it should.

    One table for two entrances. Compensation after a ticket failed to issue
    arrives already ``approved``, because nobody has to agree that a customer
    who paid for nothing should be repaid. A refund a customer asks for arrives
    ``requested`` and waits for a person, because the penalty comes from the
    fare rules and getting it wrong is expensive (``O11``).

    No schedule of its own: the order carries ``next_attempt_at`` and the sweep
    reads it there. A refund without an order is not a thing, and two columns
    that could disagree about when to try again would be one too many.
    """

    __tablename__ = "order_refunds"
    __table_args__ = (
        _in_values("kind", RefundKind, name="kind"),
        _in_values("status", RefundState, name="status"),
        CheckConstraint("amount >= 0 AND penalty_amount >= 0", name="amounts"),
        # One refund at a time. A second one opened while the first is still
        # running would race it to the same money.
        Index(
            "uq_order_refunds_open",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('requested', 'approved', 'processing')"),
        ),
        Index("ix_order_refunds_order_created", "order_id", "created_at"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Which attempt's money is going back. Not a foreign key across modules —
    #: same module here, so it is one.
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("order_payments.id", ondelete="SET NULL"),
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    #: What the customer gets back, after any penalty.
    amount: Mapped[Decimal] = money_column(nullable=False)
    #: What the fare rules kept. Always ``0`` on compensation: a ticket that was
    #: never issued cannot carry a cancellation fee.
    penalty_amount: Mapped[Decimal] = money_column(
        nullable=False, default=Decimal("0"), server_default=text("0")
    )
    currency: Mapped[str] = currency_column(nullable=False)

    #: Who asked and who agreed. Values, not references — the history has to
    #: stay readable after a staff row is gone.
    requested_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    reason: Mapped[str | None] = mapped_column(String(255))

    #: The payment provider's reference for the money it sent back.
    provider_refund_ref: Mapped[str | None] = mapped_column(String(128))
    #: What the upstream did with the reservation — released it, or refunded a
    #: ticket it had already issued.
    provider_order_action: Mapped[str | None] = mapped_column(String(32))
    failure_message: Mapped[str | None] = mapped_column(Text)


__all__ = [
    "ORDER_NO_SEQUENCE",
    "Order",
    "OrderEvent",
    "OrderPayment",
    "OrderRefund",
]
