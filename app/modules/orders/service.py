"""Orders: create on booking, read back by owner, pay, and keep the books straight.

``create_order`` is called by ``products.service.book()`` **after** GTS
confirmed the booking; it is the flow's only write and its own transaction.
There is nothing here for a failed booking on purpose: no row is the record.

Paying is two calls — ``start_payment`` sends the cardholder a code,
``confirm_payment`` charges with it — and every write between them follows
one shape: **lock → re-read → validate → mutate → commit**. A network call
sits either before the first lock (a pure read) or between two locks, never
inside the transaction whose state it decides, so a slow provider cannot hold
a row and a crashed worker cannot leave one half-written. The provider is
resolved before any lock because reading the panel's rows commits the
session (``integrations.service``).

What keeps the money right is not the Redis idempotency layer but two facts
on disk: a charge is sent only after the attempt row says ``confirming``
(so it is never sent twice), and the partial unique index on
``payment_attempts`` allows one open attempt per order (so two starts cannot
both reach the provider). The sweep (``tasks/orders.py``) settles whatever a
lost answer left open by asking the provider, and releases unpaid holds GTS
has let go.

Everything returned crosses a module boundary, so everything returned is a
schema, never a model row (the ``add_card → CardOut`` convention).
"""

import uuid
from datetime import datetime, timedelta
from typing import Final, Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import (
    Conflict,
    NotFound,
    OfferExpired,
    UpstreamError,
    UpstreamTimeout,
)
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
from app.core.crypto import decrypt, encrypt
from app.core.logging import get_logger, new_request_id, request_id_var
from app.core.money import Money
from app.db.mixins import utcnow
from app.db.repository import live
from app.modules.integrations import service as integrations_service
from app.modules.orders import lifecycle
from app.modules.orders.models import (
    AttemptStatus,
    CancelReason,
    Order,
    OrderEvent,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    TicketingStatus,
)
from app.modules.orders.schemas import (
    BookingResultOut,
    OrderListItemOut,
    PaymentAttemptView,
    PaymentConfirmIn,
    PaymentStartIn,
)
from app.modules.payments import service as payments_service
from app.modules.settings import service as settings_service
from app.providers.payments.base import PaymentDeclined, PaymentOutcome
from app.providers.products import gts_order
from app.providers.products.base import (
    BookedOrder,
    OrderSnapshot,
    ProductAdapter,
    registry,
)

logger = get_logger(__name__)

_ORDER_ORDERING: OrderingMap = {"created_at": Order.created_at}

# --- the sweep's clocks (properties of providers and GTS, not of a client) ---------

#: A charge whose answer has been missing this long is asked about. Longer
#: than any provider's own timeout, so a slow answer still arrives first.
CONFIRMING_STALE_AFTER: Final = timedelta(seconds=120)
#: ...and one still unanswered this long is given up on, so the customer can
#: pay again. Logged at ERROR: support checks the provider's own panel.
PAYMENT_CONFIRM_MAX_WAIT: Final = timedelta(minutes=15)
#: A code that was sent and never typed is forgotten after this.
ATTEMPT_STARTED_MAX_AGE: Final = timedelta(minutes=10)
#: An unpaid hold is checked against GTS this long after its deadline, and
#: again no more often than this while GTS still says it is alive.
EXPIRY_GRACE: Final = timedelta(minutes=10)
#: ...and one whose deadline GTS never spelled out, once it is this old.
EXPIRY_WITHOUT_DEADLINE: Final = timedelta(hours=24)
#: How many rows one sweep pass handles per question.
SWEEP_BATCH: Final = 20

#: How long GTS may keep a ticket "in process" (``PW``) before we stop
#: waiting and hand the order to support.
TICKETING_MAX_WAIT: Final = timedelta(minutes=30)
#: How long after our request an unchanged ``BO`` may still mean "the answer
#: is on its way" rather than "GTS never got the request".
TICKETING_POST_GRACE: Final = timedelta(minutes=5)
#: How many times automated code sends the ticketing request at all — the
#: first send and one re-send. More is a human's call (staff retry).
TICKETING_MAX_SENDS: Final = 2


# --- reading ---------------------------------------------------------------------


def _view(attempt: PaymentAttempt | None) -> PaymentAttemptView | None:
    if attempt is None:
        return None
    return PaymentAttemptView(
        id=attempt.id,
        status=attempt.status,
        provider=attempt.provider,
        card_last4=attempt.card_last4,
        phone_hint=attempt.phone_hint,
        paid_at=attempt.paid_at,
        error=attempt.error,
    )


async def _latest_attempt(
    session: AsyncSession, order_id: uuid.UUID
) -> PaymentAttempt | None:
    row: PaymentAttempt | None = await session.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.order_id == order_id)
        .order_by(PaymentAttempt.created_at.desc())
        .limit(1)
    )
    return row


async def _open_attempt(
    session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False
) -> PaymentAttempt | None:
    """The attempt that is ``started`` or ``confirming`` — at most one exists."""
    stmt = select(PaymentAttempt).where(
        PaymentAttempt.order_id == order_id,
        PaymentAttempt.status.in_([AttemptStatus.STARTED, AttemptStatus.CONFIRMING]),
    )
    if for_update:
        stmt = stmt.with_for_update()
    row: PaymentAttempt | None = await session.scalar(stmt)
    return row


async def _present(
    session: AsyncSession, order: Order, *, language: str | None
) -> BookingResultOut:
    """The detail shape, with the message rendered for this request."""
    support = await settings_service.support_contact()
    attempt = await _latest_attempt(session, order.id)
    return BookingResultOut.from_order(
        order, language=language, support=support, attempt=_view(attempt)
    )


async def _owned(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> Order:
    """One order — 404 for a missing one **and** for somebody else's.

    Two situations, one answer on purpose: whether an order id exists is
    nobody's business but its owner's (the saved-cards rule).
    """
    order = await session.scalar(
        live(Order).where(Order.id == order_id, Order.customer_id == customer_id)
    )
    if order is None:
        raise NotFound("Order not found")
    return order


async def _locked(
    session: AsyncSession,
    order_id: uuid.UUID,
    *,
    customer_id: uuid.UUID | None = None,
    skip_locked: bool = False,
) -> Order | None:
    """The row under ``FOR UPDATE`` — every write starts here.

    Always taken **before** the attempt's lock, so two writers that want both
    rows take them in the same order and cannot deadlock. ``skip_locked`` is
    for the sweep: a row another worker holds is simply somebody else's turn.
    """
    stmt = live(Order).where(Order.id == order_id)
    if customer_id is not None:
        stmt = stmt.where(Order.customer_id == customer_id)
    row: Order | None = await session.scalar(
        stmt.with_for_update(skip_locked=skip_locked)
    )
    return row


async def _owned_locked(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> Order:
    order = await _locked(session, order_id, customer_id=customer_id)
    if order is None:
        raise NotFound("Order not found")
    return order


def _adapter(order: Order) -> ProductAdapter:
    adapter = registry.get(order.product)
    if adapter is None:
        raise UpstreamError(f"no adapter serves product {order.product!r}")
    return adapter


def apply_snapshot(order: Order, snapshot: OrderSnapshot) -> None:
    """Refresh the columns from a fresh GTS read. ``None`` never overwrites.

    The deadline is taken as read: live GTS spells it as an absolute time, and
    an installation that sends minutes-remaining shrinks it on every read
    rather than extending it.
    """
    if snapshot.gts_status:
        order.gts_status = snapshot.gts_status
    if snapshot.gts_order_uid:
        order.gts_order_uid = snapshot.gts_order_uid
    if snapshot.pnr:
        order.pnr = snapshot.pnr
    if snapshot.amount is not None and snapshot.currency:
        order.amount = snapshot.amount
        order.currency = snapshot.currency
    if snapshot.trip_type:
        order.trip_type = snapshot.trip_type
    if snapshot.route_summary:
        order.route_summary = snapshot.route_summary
    if snapshot.passenger_count:
        order.passenger_count = snapshot.passenger_count
    if snapshot.ticket_time_limit_at is not None:
        order.ticket_time_limit_at = snapshot.ticket_time_limit_at
    order.gts_response = snapshot.raw
    order.gts_checked_at = utcnow()


async def create_order(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    product: str,
    booked: BookedOrder,
    language: str | None = None,
) -> BookingResultOut:
    """Record one confirmed GTS booking — and the first line of its history."""
    # The id is minted here, not at flush: the first history line below needs
    # it before anything has been written.
    order = Order(
        id=uuid.uuid4(),
        customer_id=customer_id,
        product=product,
        status=OrderStatus.BOOKED,
        payment_status=PaymentStatus.PENDING,
        ticketing_status=TicketingStatus.PENDING,
        request_id=booked.request_id,
        offer_id=booked.offer_id,
        gts_order_number=booked.gts_order_number,
        gts_order_uid=booked.gts_order_uid,
        gts_status=booked.gts_status,
        pnr=booked.pnr,
        amount=booked.amount,
        currency=booked.currency,
        trip_type=booked.trip_type,
        route_summary=booked.route_summary,
        passenger_count=booked.passenger_count,
        ticket_time_limit_at=booked.ticket_time_limit_at,
        gts_response=booked.raw,
    )
    session.add(order)
    session.add(
        lifecycle.event(
            order,
            event="order.created",
            actor=lifecycle.CUSTOMER,
            to_value=OrderStatus.BOOKED,
            data={"gts_order_number": booked.gts_order_number},
        )
    )
    await session.commit()
    # ``expire_on_commit=False`` keeps the instance readable; the refresh is
    # what loads the server-side ``created_at``/``updated_at``.
    await session.refresh(order)
    logger.info(
        "order_created",
        order_id=str(order.id),
        product=product,
        gts_order_number=order.gts_order_number,
        gts_status=order.gts_status,
    )
    return await _present(session, order, language=language)


async def list_orders(
    session: AsyncSession,
    customer_id: uuid.UUID,
    pagination: Pagination,
    query: ListQuery,
) -> Page[OrderListItemOut]:
    stmt = live(Order).where(Order.customer_id == customer_id)
    stmt = apply_search(stmt, query, Order.pnr, Order.route_summary)
    stmt = apply_created_range(stmt, query, Order.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_ORDER_ORDERING,
        default="-created_at",
        tiebreak=Order.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([OrderListItemOut.from_order(row) for row in rows], pagination, total)


async def get_order(
    session: AsyncSession,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    language: str | None = None,
) -> BookingResultOut:
    return await _present(
        session, await _owned(session, customer_id, order_id), language=language
    )


# --- paying ----------------------------------------------------------------------


def _require_payable(order: Order) -> None:
    """The order can take a payment right now, or the reason it cannot."""
    if order.status != OrderStatus.BOOKED:
        raise Conflict("This order is cancelled")
    if order.payment_status == PaymentStatus.PAID:
        raise Conflict("This order is already paid")
    if order.payment_status not in (PaymentStatus.PENDING, PaymentStatus.FAILED):
        raise Conflict("This order is being refunded")
    if order.ticketing_status != TicketingStatus.PENDING:
        raise Conflict("This order is already being ticketed")


def _released(order: Order, snapshot: OrderSnapshot) -> list[OrderEvent]:
    """GTS let the hold go — record it, so the customer is told to search again."""
    return lifecycle.transition(
        order,
        actor=lifecycle.SYSTEM,
        status=OrderStatus.CANCELLED,
        cancel_reason=CancelReason.EXPIRED,
        note=f"GTS status {snapshot.gts_status}",
        data={"gts_status": snapshot.gts_status},
    )


async def start_payment(
    session: AsyncSession,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    data: PaymentStartIn,
    *,
    language: str | None = None,
) -> BookingResultOut:
    """Step 1: confirm the hold is alive at GTS, claim the attempt, send the code.

    The GTS read-back comes first and is not optional: charging for a hold
    GTS has already released would only turn into a refund. Its price wins
    over ours, too — it is the amount GTS will debit at ticketing.

    The attempt row is written **before** the provider is called. It is the
    claim: a second start for the same order, racing this one, collides on
    the partial unique index instead of sending a second code.
    """
    # Everything that talks to the outside world, before any lock.
    provider = await payments_service.payment_provider(session)
    client = await integrations_service.gts_client(session)
    order = await _owned(session, customer_id, order_id)
    _require_payable(order)
    card = await payments_service.card_for_charge(
        session, customer_id, card_id=data.card_id, card=data.card
    )
    snapshot = await _adapter(order).retrieve(client, order.gts_order_number)

    order = await _owned_locked(session, customer_id, order_id)
    open_attempt = await _open_attempt(session, order.id, for_update=True)
    if open_attempt is not None and open_attempt.status == AttemptStatus.CONFIRMING:
        raise Conflict("A payment for this order is being confirmed")
    apply_snapshot(order, snapshot)
    if gts_order.is_released(snapshot.gts_status):
        session.add_all(_released(order, snapshot))
        await session.commit()
        raise OfferExpired("The booking has expired at GTS — please search again")
    _require_payable(order)
    if order.amount is None or order.currency is None:
        raise UpstreamError("GTS did not report a price for this order")
    if open_attempt is not None:
        # A code was sent and never typed; this start supersedes it.
        open_attempt.status = AttemptStatus.ABANDONED

    attempt = PaymentAttempt(
        id=uuid.uuid4(),
        order_id=order.id,
        customer_id=customer_id,
        provider=provider.code,
        status=AttemptStatus.STARTED,
        amount=order.amount,
        currency=order.currency,
        card_id=data.card_id,
        card_last4=card.last4,
    )
    session.add(attempt)
    session.add(
        lifecycle.event(
            order,
            event="payment.started",
            actor=lifecycle.CUSTOMER,
            data={"attempt": str(attempt.id), "provider": provider.code},
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict("Another payment for this order is already open") from None

    try:
        started = await provider.start(
            card=card,
            amount=Money(amount=attempt.amount, currency=attempt.currency),
            order_ref=str(order.id),
        )
    except PaymentDeclined as exc:
        await _fail_attempt(session, attempt.id, error=str(exc))
        return await _present(
            session, await _owned(session, customer_id, order_id), language=language
        )
    except (UpstreamError, UpstreamTimeout) as exc:
        # Nothing is charged at this step, so failing the attempt is safe and
        # the customer may simply try again.
        await _fail_attempt(session, attempt.id, error=str(exc))
        raise

    order = await _owned_locked(session, customer_id, order_id)
    row = await session.get(PaymentAttempt, attempt.id, with_for_update=True)
    if row is not None and row.status == AttemptStatus.STARTED:
        row.provider_reference, row.key_version = encrypt(started.reference)
        row.phone_hint = started.phone_hint
        row.provider_data = {"start": started.raw}
    await session.commit()
    logger.info(
        "payment_started",
        order_id=str(order.id),
        attempt=str(attempt.id),
        provider=provider.code,
    )
    return await _present(session, order, language=language)


async def _fail_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, *, error: str
) -> None:
    """Close an attempt the provider refused at ``start``; the order reads failed."""
    probe = await session.get(PaymentAttempt, attempt_id)
    if probe is None:
        return
    order = await _locked(session, probe.order_id)
    attempt = await session.get(PaymentAttempt, attempt_id, with_for_update=True)
    if order is None or attempt is None or attempt.status != AttemptStatus.STARTED:
        return
    attempt.status = AttemptStatus.FAILED
    attempt.error = error[:500]
    session.add_all(
        lifecycle.transition(
            order,
            actor=lifecycle.CUSTOMER,
            payment=PaymentStatus.FAILED,
            note=attempt.error,
            data={"attempt": str(attempt.id)},
        )
    )
    await session.commit()
    logger.info("payment_start_failed", order_id=str(order.id), attempt=str(attempt.id))


async def confirm_payment(
    session: AsyncSession,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    data: PaymentConfirmIn,
    *,
    language: str | None = None,
) -> BookingResultOut:
    """Step 2: charge with the code — exactly once.

    The attempt is marked ``confirming`` and **committed before** the
    provider is called, so whatever happens next — a timeout, a crash, a
    retry from the customer — the charge is never sent a second time. An
    unknown answer leaves the attempt ``confirming``; the sweep asks the
    provider what became of it. A repeat of this call while the attempt is
    ``confirming`` is a read, not a second charge.
    """
    provider = await payments_service.payment_provider(session)

    order = await _owned_locked(session, customer_id, order_id)
    attempt = await _open_attempt(session, order.id, for_update=True)
    if attempt is None or attempt.id != data.payment_id:
        raise Conflict("This payment is not open — start the payment again")
    if attempt.status == AttemptStatus.CONFIRMING:
        return await _present(session, order, language=language)
    if attempt.provider_reference is None:
        raise Conflict("This payment was not started — start the payment again")
    if (
        order.ticket_time_limit_at is not None
        and order.ticket_time_limit_at <= utcnow()
    ):
        # The hold lapsed while the code was being typed. Charging now would
        # buy nothing; GTS's own status is re-read by the sweep.
        attempt.status = AttemptStatus.ABANDONED
        session.add_all(
            lifecycle.transition(
                order,
                actor=lifecycle.SYSTEM,
                status=OrderStatus.CANCELLED,
                cancel_reason=CancelReason.EXPIRED,
                note="payment deadline passed before confirmation",
            )
        )
        await session.commit()
        raise OfferExpired("The payment deadline has passed — please search again")
    reference = decrypt(attempt.provider_reference, attempt.key_version or 0)
    attempt.status = AttemptStatus.CONFIRMING
    session.add(
        lifecycle.event(
            order,
            event="payment.confirming",
            actor=lifecycle.CUSTOMER,
            data={"attempt": str(attempt.id)},
        )
    )
    await session.commit()

    try:
        outcome = await provider.confirm(
            reference=reference, otp=data.otp.get_secret_value()
        )
    except (UpstreamError, UpstreamTimeout) as exc:
        logger.warning(
            "payment_confirm_unknown",
            order_id=str(order.id),
            attempt=str(attempt.id),
            error=str(exc),
        )
        return await _present(session, order, language=language)

    await settle_attempt(session, attempt.id, outcome, actor=lifecycle.CUSTOMER)
    return await _present(
        session, await _owned(session, customer_id, order_id), language=language
    )


async def settle_attempt(
    session: AsyncSession,
    attempt_id: uuid.UUID,
    outcome: PaymentOutcome,
    *,
    actor: str,
    skip_locked: bool = False,
) -> bool:
    """Apply the provider's verdict to a ``confirming`` attempt — once.

    Shared by the confirm handler and the sweep, which can race each other:
    both re-lock and re-read, and whichever finds the attempt no longer
    ``confirming`` does nothing. ``True`` when this call settled it.
    """
    probe = await session.get(PaymentAttempt, attempt_id)
    if probe is None:
        return False
    order = await _locked(session, probe.order_id, skip_locked=skip_locked)
    if order is None:
        return False
    attempt = await session.get(PaymentAttempt, attempt_id, with_for_update=True)
    if attempt is None or attempt.status != AttemptStatus.CONFIRMING:
        await session.rollback()
        return False

    data = {**(attempt.provider_data or {}), "outcome": outcome.raw}
    if outcome.status == "paid":
        attempt.status = AttemptStatus.PAID
        attempt.paid_at = utcnow()
        attempt.provider_data = data
        session.add_all(
            lifecycle.transition(
                order,
                actor=actor,
                payment=PaymentStatus.PAID,
                data={"attempt": str(attempt.id)},
            )
        )
        if attempt.card_id is not None:
            await payments_service.mark_card_used(
                session, attempt.customer_id, attempt.card_id
            )
        await session.commit()
        logger.info("payment_paid", order_id=str(order.id), attempt=str(attempt.id))
        await _after_paid(session, order, actor=actor)
        return True
    if outcome.status == "failed":
        attempt.status = AttemptStatus.FAILED
        attempt.error = (outcome.error or "declined")[:500]
        attempt.provider_data = data
        session.add_all(
            lifecycle.transition(
                order,
                actor=actor,
                payment=PaymentStatus.FAILED,
                note=attempt.error,
                data={"attempt": str(attempt.id)},
            )
        )
        await session.commit()
        logger.info("payment_failed", order_id=str(order.id), attempt=str(attempt.id))
        return True
    # Still pending at the provider: restart the sweep's clock and wait.
    attempt.provider_data = data
    attempt.updated_at = utcnow()
    await session.commit()
    return False


async def _after_paid(session: AsyncSession, order: Order, *, actor: str) -> None:
    """What follows a successful charge: the ticket, asked for right away.

    The money has moved by the time this runs, so nothing here may turn into
    an error for the caller — a failure is logged and the order stays
    ``paid`` / ``pending``, where the sweep picks it up.
    """
    try:
        await ticket(session, order.id, actor=actor)
    except Exception as exc:  # noqa: BLE001 - the sweep retries; the payment stands
        logger.exception(
            "ticketing_after_payment_failed",
            order_id=str(order.id),
            error=f"{type(exc).__name__}: {exc}"[:300],
        )


# --- ticketing -------------------------------------------------------------------

Decision = Literal["ticketed", "failed", "wait", "resend"]


async def ticket(session: AsyncSession, order_id: uuid.UUID, *, actor: str) -> None:
    """Ask GTS to issue the ticket — the only place the ticketing POST is sent.

    The order is marked ``processing`` and **committed before** the POST, so
    whatever happens next the request is never repeated by accident: a
    crash or a timeout leaves ``processing``, and the sweep settles it by
    reading the order back. GTS's own refusal is read back too before it is
    believed — a request re-sent for an order GTS had already ticketed is
    refused, and the refusal must not mark a ticketed order failed.
    """
    client = await integrations_service.gts_client(session)
    order = await _locked(session, order_id)
    if order is None:
        return
    events = lifecycle.transition(
        order, actor=actor, ticketing=TicketingStatus.PROCESSING
    )
    order.ticketing_attempts += 1
    order.ticketing_requested_at = utcnow()
    session.add_all(events)
    session.add(
        lifecycle.event(
            order,
            event="ticketing.requested",
            actor=actor,
            data={"send": order.ticketing_attempts},
        )
    )
    await session.commit()
    logger.info(
        "ticketing_requested",
        order_id=str(order.id),
        gts_order_number=order.gts_order_number,
        send=order.ticketing_attempts,
    )

    adapter = _adapter(order)
    error: str | None = None
    snapshot: OrderSnapshot | None
    try:
        snapshot = await adapter.ticket(client, order.gts_order_number)
    except UpstreamTimeout as exc:
        logger.warning(
            "ticketing_answer_unknown", order_id=str(order.id), error=str(exc)
        )
        return
    except UpstreamError as exc:
        error = exc.message
        try:
            snapshot = await adapter.retrieve(client, order.gts_order_number)
        except (UpstreamError, UpstreamTimeout):
            snapshot = None
    await _apply_ticketing(session, order_id, snapshot, error=error, actor=actor)


def _decide(
    order: Order, status: str | None, error: str | None, *, now: datetime
) -> tuple[Decision, str | None]:
    """What GTS's current status means for an order we asked a ticket for.

    One table for the POST's answer and for every later read-back; ``error``
    is GTS's refusal text when the POST itself was refused.
    """
    if gts_order.is_ticketed(status):
        return "ticketed", None
    if gts_order.is_released(status) or status == "TE":
        return "failed", error or f"GTS status {status}"
    requested = order.ticketing_requested_at or now
    waited = now - requested
    if gts_order.is_waiting(status):
        if waited > TICKETING_MAX_WAIT:
            return "failed", "GTS did not issue the ticket within the waiting time"
        return "wait", None
    if error is not None:
        return "failed", error
    if gts_order.is_held(status):
        # Still ``BO``: GTS shows no sign of our request. Give the answer its
        # grace, re-send once while the hold is alive, then stop guessing.
        if waited < TICKETING_POST_GRACE:
            return "wait", None
        deadline = order.ticket_time_limit_at
        if order.ticketing_attempts < TICKETING_MAX_SENDS and (
            deadline is None or deadline > now
        ):
            return "resend", None
        return "failed", "the ticketing request was not confirmed by GTS"
    if waited > TICKETING_MAX_WAIT:
        return "failed", "GTS did not issue the ticket within the waiting time"
    return "wait", None


async def _apply_ticketing(
    session: AsyncSession,
    order_id: uuid.UUID,
    snapshot: OrderSnapshot | None,
    *,
    error: str | None,
    actor: str,
    skip_locked: bool = False,
) -> Decision:
    """Apply what GTS says to a ``processing`` order — once, under the lock."""
    order = await _locked(session, order_id, skip_locked=skip_locked)
    if order is None or order.ticketing_status != TicketingStatus.PROCESSING:
        await session.rollback()
        return "wait"
    now = utcnow()
    if snapshot is not None:
        apply_snapshot(order, snapshot)
    else:
        order.gts_checked_at = now
    status = snapshot.gts_status if snapshot is not None else None
    decision, reason = _decide(order, status, error, now=now)

    if decision == "ticketed":
        order.ticketing_error = None
        session.add_all(
            lifecycle.transition(
                order,
                actor=actor,
                ticketing=TicketingStatus.TICKETED,
                data={"gts_status": status},
            )
        )
        logger.info("order_ticketed", order_id=str(order.id))
    elif decision == "failed":
        order.ticketing_error = (reason or "")[:500] or None
        session.add_all(
            lifecycle.transition(
                order,
                actor=actor,
                ticketing=TicketingStatus.FAILED,
                note=order.ticketing_error,
                data={"gts_status": status},
            )
        )
        if gts_order.is_deposit_empty(reason):
            # Our balance at GTS, not the customer's card: an alarm, and a
            # staff retry once the deposit is topped up.
            logger.error(
                "gts_deposit_empty", order_id=str(order.id), error=order.ticketing_error
            )
        else:
            logger.warning(
                "ticketing_failed",
                order_id=str(order.id),
                gts_status=status,
                error=order.ticketing_error,
            )
    await session.commit()
    return decision


async def recheck_processing(session: AsyncSession) -> int:
    """Read every order that is waiting on GTS back, and settle the ones it can.

    The sweep's third question. Returns how many orders moved (to ticketed
    or failed); a re-send counts as a move too, since the request went out.
    """
    client = await integrations_service.gts_client(session)
    rows = (
        await session.scalars(
            live(Order)
            .where(Order.ticketing_status == TicketingStatus.PROCESSING)
            .order_by(Order.gts_checked_at.nulls_first(), Order.ticketing_requested_at)
            .limit(SWEEP_BATCH)
        )
    ).all()
    moved = 0
    for probe in rows:
        request_id_var.set(new_request_id())
        snapshot: OrderSnapshot | None
        try:
            snapshot = await _adapter(probe).retrieve(client, probe.gts_order_number)
        except (UpstreamError, UpstreamTimeout) as exc:
            logger.warning(
                "ticketing_recheck_unread", order_id=str(probe.id), error=str(exc)
            )
            snapshot = None
        decision = await _apply_ticketing(
            session,
            probe.id,
            snapshot,
            error=None,
            actor=lifecycle.SYSTEM,
            skip_locked=True,
        )
        if decision == "resend":
            await ticket(session, probe.id, actor=lifecycle.SYSTEM)
        if decision != "wait":
            moved += 1
    return moved


async def ticket_paid_pending(session: AsyncSession) -> int:
    """Paid orders nobody asked a ticket for — the crash between the two.

    ``settle_attempt`` commits the payment and then asks for the ticket; a
    worker that dies in between leaves ``paid`` / ``pending``, and this is
    the safety net that notices.
    """
    rows = (
        await session.scalars(
            live(Order)
            .where(
                Order.status == OrderStatus.BOOKED,
                Order.payment_status == PaymentStatus.PAID,
                Order.ticketing_status == TicketingStatus.PENDING,
            )
            .order_by(Order.paid_at)
            .limit(SWEEP_BATCH)
        )
    ).all()
    for probe in rows:
        request_id_var.set(new_request_id())
        await ticket(session, probe.id, actor=lifecycle.SYSTEM)
    return len(rows)


# --- the sweep (tasks/orders.py) ---------------------------------------------------


async def settle_stale_confirmations(session: AsyncSession) -> int:
    """Ask the provider about charges whose answer never came back.

    A ``confirming`` attempt older than ``CONFIRMING_STALE_AFTER`` is asked
    about; one the provider still calls pending after
    ``PAYMENT_CONFIRM_MAX_WAIT`` is given up on and marked failed, so the
    customer is not locked out of paying — logged at ERROR because a human
    should look at the provider's panel. Returns how many were settled.
    """
    provider = await payments_service.payment_provider(session)
    now = utcnow()
    rows = (
        await session.scalars(
            select(PaymentAttempt)
            .where(
                PaymentAttempt.status == AttemptStatus.CONFIRMING,
                PaymentAttempt.updated_at < now - CONFIRMING_STALE_AFTER,
            )
            .order_by(PaymentAttempt.updated_at)
            .limit(SWEEP_BATCH)
        )
    ).all()
    settled = 0
    for probe in rows:
        request_id_var.set(new_request_id())
        if probe.provider_reference is None:
            outcome = PaymentOutcome("failed", error="no provider reference")
        else:
            try:
                outcome = await provider.status(
                    reference=decrypt(probe.provider_reference, probe.key_version or 0)
                )
            except (UpstreamError, UpstreamTimeout) as exc:
                logger.warning(
                    "payment_status_unread", attempt=str(probe.id), error=str(exc)
                )
                continue
        if (
            outcome.status == "pending"
            and probe.created_at < now - PAYMENT_CONFIRM_MAX_WAIT
        ):
            logger.error(
                "payment_unconfirmed",
                order_id=str(probe.order_id),
                attempt=str(probe.id),
                provider=probe.provider,
            )
            outcome = PaymentOutcome(
                "failed",
                reference=outcome.reference,
                error="the provider never confirmed this charge",
                raw=outcome.raw,
            )
        if await settle_attempt(
            session, probe.id, outcome, actor=lifecycle.SYSTEM, skip_locked=True
        ):
            settled += 1
    return settled


async def expire_unpaid(session: AsyncSession) -> int:
    """Release unpaid holds GTS has let go — after asking GTS, never by the clock.

    Our deadline is a best-effort reading of a field GTS spells three ways,
    so an order past it is only a *candidate*: the hold is released here
    when GTS's own status says so, and left alone (deadline refreshed) when
    GTS still holds it. An order with a code in flight is skipped; a code
    nobody typed for ``ATTEMPT_STARTED_MAX_AGE`` is forgotten first.
    """
    client = await integrations_service.gts_client(session)
    now = utcnow()
    rows = (
        await session.scalars(
            live(Order)
            .where(
                Order.status == OrderStatus.BOOKED,
                Order.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.FAILED]),
                or_(
                    Order.ticket_time_limit_at < now - EXPIRY_GRACE,
                    and_(
                        Order.ticket_time_limit_at.is_(None),
                        Order.created_at < now - EXPIRY_WITHOUT_DEADLINE,
                    ),
                ),
                or_(
                    Order.gts_checked_at.is_(None),
                    Order.gts_checked_at < now - EXPIRY_GRACE,
                ),
            )
            .order_by(Order.ticket_time_limit_at)
            .limit(SWEEP_BATCH)
        )
    ).all()
    expired = 0
    for probe in rows:
        request_id_var.set(new_request_id())
        try:
            snapshot = await _adapter(probe).retrieve(client, probe.gts_order_number)
        except (UpstreamError, UpstreamTimeout) as exc:
            logger.warning(
                "expiry_check_unread", order_id=str(probe.id), error=str(exc)
            )
            continue
        order = await _locked(session, probe.id, skip_locked=True)
        if (
            order is None
            or order.status != OrderStatus.BOOKED
            or order.payment_status not in (PaymentStatus.PENDING, PaymentStatus.FAILED)
        ):
            await session.rollback()
            continue
        open_attempt = await _open_attempt(session, order.id, for_update=True)
        if open_attempt is not None:
            if (
                open_attempt.status == AttemptStatus.CONFIRMING
                or open_attempt.created_at > now - ATTEMPT_STARTED_MAX_AGE
            ):
                await session.rollback()
                continue
            open_attempt.status = AttemptStatus.ABANDONED
        apply_snapshot(order, snapshot)
        if gts_order.is_released(snapshot.gts_status):
            session.add_all(_released(order, snapshot))
            expired += 1
            logger.info(
                "order_expired", order_id=str(order.id), gts_status=snapshot.gts_status
            )
        await session.commit()
    return expired


__all__ = [
    "ATTEMPT_STARTED_MAX_AGE",
    "CONFIRMING_STALE_AFTER",
    "EXPIRY_GRACE",
    "EXPIRY_WITHOUT_DEADLINE",
    "PAYMENT_CONFIRM_MAX_WAIT",
    "SWEEP_BATCH",
    "TICKETING_MAX_SENDS",
    "TICKETING_MAX_WAIT",
    "TICKETING_POST_GRACE",
    "apply_snapshot",
    "confirm_payment",
    "create_order",
    "expire_unpaid",
    "get_order",
    "list_orders",
    "recheck_processing",
    "settle_attempt",
    "settle_stale_confirmations",
    "start_payment",
    "ticket",
    "ticket_paid_pending",
]
