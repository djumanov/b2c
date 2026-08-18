"""Orders: the state machine, and the doors other modules use.

``transition`` is the whole point of this module. **It is the only way an
order's status changes** — not because a convention says so, but because
everything that keeps the money path honest happens inside it: the row is
locked, the move is checked against ``states.TRANSITIONS``, the history line is
written, and the next background step is scheduled, all in one transaction. A
service that reaches past it and assigns ``order.status`` gets none of that and
leaves no trace behind (order-system/03-design.md §3.3).

The doors other modules may use are ``record_booking``, ``owned_by_provider_number``,
``ensure_cancellable`` and ``apply_cancel``; ``products`` calls them around its
passthrough. Nothing outside this package touches ``models`` or ``repository``
(ARCHITECTURE.md §4).

**Reading the provider's answer stays forgiving, but no longer silent.** The
field names come from the EASY_GATEWAY collection — recorded calls, not guesses
— yet they have still not been seen against this installation's live GTS
(STATUS.md §8). So an answer we cannot read no longer produces a half-written
row: the order lands in ``needs_attention`` with the whole answer attached to
its first event, which is a thing a person can act on. The booking is never
lost either way.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import AppError, Conflict, NotFound, ValidationFailed
from app.api.listing import (
    ListQuery,
    OrderingMap,
    apply_created_range,
    apply_ordering,
    page,
    paginate,
)
from app.core.logging import get_logger
from app.core.money import Money
from app.modules.orders import repository
from app.modules.orders.models import (
    ORDER_NO_SEQUENCE,
    Order,
    OrderEvent,
    OrderPayment,
)
from app.modules.orders.schemas import (
    OrderOut,
    TransactionOut,
    TransactionStartIn,
)
from app.modules.orders.states import (
    INITIAL_STATUS,
    STAMPED_AT,
    Actor,
    EventAction,
    OrderStatus,
    can,
)
from app.modules.payments import service as payments_service
from app.modules.settings import service as settings_service
from app.modules.settings.schemas import OrderSettingsOut
from app.providers.payments.base import (
    PaymentProviderCode,
    TransactionFlow,
    TransactionStatus,
)
from app.providers.products.orders import (
    BookingResult,
    CancelResult,
    TicketingResult,
)

logger = get_logger(__name__)

#: ``created_at`` only. Ordering by anything else would publish it as part of
#: the contract (``api/listing.py`` on why this is a whitelist).
_ORDER_ORDERING: OrderingMap = {"created_at": Order.created_at}


# --- the state machine ----------------------------------------------------------


async def transition(
    session: AsyncSession,
    order_id: uuid.UUID,
    *,
    to: OrderStatus,
    actor: Actor,
    action: EventAction,
    reason: str | None = None,
    meta: dict[str, Any] | None = None,
    fields: Mapping[str, Any] | None = None,
    next_attempt_at: datetime | None = None,
) -> Order:
    """Move one order, or refuse. The only writer of ``orders.status``.

    ``fields`` carries whatever else the move establishes — the provider's
    identifiers on a confirmation, a reason on a cancellation. They are applied
    inside the same transaction because a status that is true and a row that is
    not is exactly the state nobody can recover from.

    ``next_attempt_at`` schedules the next background step and defaults to
    ``None``, which means *nothing is pending*. That default is the right one:
    most moves end with the order waiting on the outside world, and a step that
    really is due says so explicitly (``O13``).

    ``attempts`` is bookkeeping, not an argument: a move to a **different**
    status starts a new step and resets it, while the retry loop
    (``ticketing → ticketing``) counts up.

    A move that is not in the table raises ``Conflict`` — a ``409``, never a
    silent no-op. It is nearly always one of two real problems: a race, or a
    client acting on a screen that has gone stale.
    """
    order = await repository.lock_order(session, order_id)
    if order is None:
        raise NotFound("No such order")

    source = OrderStatus(order.status)
    if not can(source, to):
        raise Conflict(
            f"An order that is {source.value} cannot become {to.value}",
            meta={"from": source.value, "to": to.value},
        )

    order.attempts = order.attempts + 1 if source is to else 0
    order.status = to.value
    stamp = STAMPED_AT.get(to)
    if stamp is not None and getattr(order, stamp) is None:
        # First arrival only. Re-entering a status must not rewrite the moment
        # it was first reached — the reports read these columns.
        setattr(order, stamp, datetime.now(UTC))
    for name, value in (fields or {}).items():
        setattr(order, name, value)
    order.next_attempt_at = next_attempt_at

    session.add(
        OrderEvent(
            order_id=order.id,
            from_status=source.value,
            to_status=to.value,
            action=action.value,
            actor_type=actor.type.value,
            actor_id=actor.id,
            actor_label=actor.label,
            reason=reason,
            meta=meta,
            attempt=order.attempts,
        )
    )
    await session.commit()
    await session.refresh(order)
    logger.info(
        "order_transition",
        order_id=str(order.id),
        order_no=order.order_no,
        product=order.product,
        status_from=source.value,
        status_to=to.value,
        action=action.value,
        actor=actor.type.value,
        attempt=order.attempts,
    )
    return order


async def _next_order_no(session: AsyncSession) -> str:
    """``B2C-2608-000123`` — ours, readable, and unique without a lock.

    A sequence rather than a count: two bookings racing must not be handed the
    same number, and ``nextval`` is the only thing that guarantees that. The
    year and month are cosmetic, taken at allocation time; the number alone is
    what is unique.
    """
    number = await session.scalar(sql_text(f"SELECT nextval('{ORDER_NO_SEQUENCE}')"))
    return f"B2C-{datetime.now(UTC):%y%m}-{int(number or 0):06d}"


# --- the doors ``products`` uses ------------------------------------------------


def _reference(payload: Mapping[str, Any], key: str) -> str | None:
    """One of our own request's identifiers, if it is usable.

    Only ``request_id`` and ``offer_id`` are read here, and only from the
    *request*: everything the provider said arrives already translated in a
    ``BookingResult``. Reading GTS's dictionaries is the adapter's job and
    stopped being this module's the moment that port existed
    (``providers/products/orders.py``).
    """
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    text = str(value).strip()
    return text if text and len(text) <= 64 else None


async def start_order(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    product: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> tuple[Order, bool]:
    """Write the intent to book, **before** anybody asks the provider.

    Returns the order and whether it is new. ``False`` means this key has been
    seen before and the caller must not book again — the earlier attempt is
    what the customer gets back.

    This ordering is the whole point of the slice. Writing the row after the
    provider answered meant a failed insert left a reservation nobody could
    find, cancel, or refund: the seat was real, the money would follow, and the
    only trace was a log line (02-current-audit.md A1). A row written first is
    at worst an order stuck in ``created``, which reconciliation can resolve
    because it knows what to look for.

    The unique index is the guard that actually holds. ``api/idempotency.py``
    claims the key in Redis first and answers most duplicates there, but Redis
    is a cache: a flush, an eviction or a restart lets the second request
    through, and on this path a second request is a second real seat (``O8``).
    """
    order = Order(
        order_no=await _next_order_no(session),
        customer_id=customer_id,
        product=product,
        status=INITIAL_STATUS.value,
        request_id=_reference(payload, "request_id"),
        offer_id=_reference(payload, "offer_id"),
        idempotency_key=idempotency_key,
    )
    session.add(order)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        earlier = await repository.order_by_idempotency_key(
            session, customer_id, idempotency_key
        )
        if earlier is None:
            # Some other constraint, and guessing which would be worse than
            # letting the 500 name it.
            raise
        logger.info(
            "order_idempotent_replay",
            order_id=str(earlier.id),
            order_no=earlier.order_no,
            status=earlier.status,
        )
        return earlier, False
    logger.info(
        "order_created",
        order_id=str(order.id),
        order_no=order.order_no,
        product=product,
        customer_id=str(customer_id),
    )
    return order, True


async def confirm_booking(
    session: AsyncSession, order: Order, result: BookingResult
) -> Order:
    """The provider is holding a seat — T2."""
    return await transition(
        session,
        order.id,
        to=result.status,
        actor=Actor.system("orders.booking"),
        action=EventAction.BOOKING_CONFIRMED,
        meta=result.raw,
        fields={
            "provider_order_number": result.provider_order_number,
            "provider_order_uid": result.provider_order_uid,
            "provider_pnr": result.provider_pnr,
            "provider_status": result.provider_status,
            "provider_response": result.raw,
            "amount_total": result.total.amount,
            "currency": result.total.currency,
            "travelers": [person.as_dict() for person in result.travelers],
            "ticket_time_limit_at": result.ticket_time_limit_at,
            "travel_start_at": result.travel_start_at,
            "route_summary": result.route_summary,
        },
    )


async def record_unreadable_booking(
    session: AsyncSession, order: Order, response: dict[str, Any]
) -> Order:
    """The provider agreed in words we cannot parse — T4.

    Not a failure to report: a real seat is probably held. The key is **kept**,
    so a retry cannot open a second one, and the whole answer goes onto the
    event where a person can read it.
    """
    logger.warning(
        "order_answer_unreadable",
        order_id=str(order.id),
        order_no=order.order_no,
        product=order.product,
        fields=sorted(response),
    )
    return await transition(
        session,
        order.id,
        to=OrderStatus.NEEDS_ATTENTION,
        actor=Actor.system("orders.booking"),
        action=EventAction.BOOKING_UNRESOLVED,
        reason="The booking answer could not be read",
        meta=response,
        fields={
            "provider_response": response,
            "attention_reason": "unreadable_booking_answer",
        },
    )


async def fail_booking(session: AsyncSession, order: Order, *, reason: str) -> Order:
    """The provider refused — T3. No seat was taken and no money will move.

    The idempotency key is **released** here and nowhere else. A refusal is the
    one outcome where retrying is both safe and what the customer wants, and a
    key still claimed by this row would make the retry replay the refusal
    instead of trying again. Every other ending keeps its key, because every
    other ending may be holding a seat.
    """
    return await transition(
        session,
        order.id,
        to=OrderStatus.FAILED,
        actor=Actor.system("orders.booking"),
        action=EventAction.BOOKING_REJECTED,
        reason=reason,
        fields={"failure_message": reason, "idempotency_key": None},
    )


def booking_answer(order: Order) -> dict[str, Any]:
    """What ``booking/`` returns (order-system/03-design.md §3.6).

    Three keys. ``data`` is the provider's answer, unchanged and complete,
    because the route, the segments and the fare rules live only there and we
    do not lift them out. ``order`` is ours, and ``payment`` says what is still
    owed and until when.

    ``payment`` is derived from the order rather than stored (``payment_state``).

    ``data`` is ``null`` on an order that never got an answer — a duplicate
    replayed while the first attempt is still in flight. That is honest: we
    genuinely do not have one yet.
    """
    return {
        "order": OrderOut.from_order(order).model_dump(mode="json"),
        "payment": payment_state(order),
        "data": order.provider_response,
    }


async def owned_by_provider_number(
    session: AsyncSession, *, customer_id: uuid.UUID, provider_order_number: str
) -> Order:
    """The order behind GTS's ``order_number``, or ``404`` (API.md §20).

    This is the whole of the cancel ownership check. Nothing reaches GTS before
    it passes.
    """
    order = await repository.order_by_provider_number(
        session, customer_id, provider_order_number
    )
    if order is None:
        raise NotFound("No such order")
    return order


def ensure_cancellable(order: Order) -> None:
    """Refuse a cancellation the machine would refuse anyway — **before** GTS.

    Without this the check happens after the provider has already released the
    seat, and a ticketed order would be cancelled upstream and then rejected
    here. The transition still runs afterwards and is still authoritative; this
    only moves the "no" to the side of the call where it is free.
    """
    if not can(OrderStatus(order.status), OrderStatus.CANCELLED):
        raise Conflict(
            f"An order that is {order.status} cannot be cancelled",
            meta={"from": order.status},
        )


async def apply_cancel(
    session: AsyncSession, order: Order, result: CancelResult
) -> Order:
    """Carry the provider's answer to a cancellation onto the row.

    Their code is kept beside ours rather than instead of it: ``CB`` is what
    they call it, ``cancelled`` is what happened. Reaching here at all means the
    seat is released — a refusal is an ``UpstreamError`` and never gets this
    far — so the move does not depend on what, if anything, they named it.
    """
    return await transition(
        session,
        order.id,
        to=OrderStatus.CANCELLED,
        actor=Actor.customer(order.customer_id),
        action=EventAction.ORDER_CANCELLED,
        reason="Cancelled by the customer",
        meta=result.raw,
        fields={
            "cancellation_reason": "customer",
            "provider_status": result.provider_status or order.provider_status,
        },
    )


# --- paying for an order (API.md §22) --------------------------------------------


def payment_state(order: Order) -> dict[str, Any]:
    """What the customer still owes, and until when.

    Derived, not stored. One order carries one amount and one currency, so a
    payment *intent* would be a second copy of two columns that already exist
    (``O12``); what is stored is the attempts.

    ``pay_before`` is the provider's hold, unshortened. The ticketing margin
    that will make it earlier arrives with the ticketing slice, and putting a
    guess in front of it now would only have to be unlearned.
    """
    if order.amount_total is None or order.currency is None:
        return {"status": "pending", "amount": None, "pay_before": None}
    settled = order.amount_paid >= order.amount_total
    return {
        "status": "paid" if settled else "pending",
        "amount": Money(amount=order.amount_total, currency=order.currency).model_dump(
            mode="json"
        ),
        "pay_before": (
            order.ticket_time_limit_at.isoformat()
            if order.ticket_time_limit_at is not None
            else None
        ),
    }


async def start_attempt(
    session: AsyncSession, order: Order, *, provider: str, flow: str
) -> OrderPayment:
    """Write the attempt, **then** let the caller talk to the provider.

    The order matters as much as it does at booking. A charge that exists at a
    provider and nowhere here is money nobody can match to an order; an attempt
    row with no ``provider_ref`` is merely an attempt that has not been answered
    yet, and reconciliation knows what to do with one (ARCHITECTURE.md §8).
    """
    if order.amount_total is None or order.currency is None:
        raise Conflict("This order has no amount to pay yet")
    if OrderStatus(order.status) is not OrderStatus.BOOKED:
        raise Conflict(
            f"An order that is {order.status} cannot be paid for",
            meta={"status": order.status},
        )
    attempt = OrderPayment(
        order_id=order.id,
        provider=provider,
        status=TransactionStatus.PENDING.value,
        flow=flow,
        amount=order.amount_total,
        currency=order.currency,
    )
    session.add(attempt)
    await session.commit()
    await session.refresh(attempt)
    logger.info(
        "payment_attempt_started",
        order_id=str(order.id),
        order_no=order.order_no,
        attempt_id=str(attempt.id),
        provider=provider,
    )
    return attempt


async def attach_redirect(
    session: AsyncSession, attempt: OrderPayment, *, redirect_url: str
) -> OrderPayment:
    """Where the customer was sent. Written after the provider answered."""
    attempt.redirect_url = redirect_url
    await session.commit()
    await session.refresh(attempt)
    return attempt


async def abandon_attempt(
    session: AsyncSession, attempt: OrderPayment, *, message: str
) -> None:
    """The provider would not start the charge. The row stays as evidence."""
    attempt.status = TransactionStatus.FAILED.value
    attempt.error_message = message
    await session.commit()
    logger.warning(
        "payment_attempt_abandoned", attempt_id=str(attempt.id), reason=message
    )


async def settle_attempt(
    session: AsyncSession,
    *,
    provider: str,
    provider_ref: str,
    order_id: uuid.UUID,
    provider_state: dict[str, Any] | None = None,
) -> Order:
    """Money arrived — the door a provider callback comes through.

    **Idempotent by construction, not by care.** A provider that has already
    been answered still resends, so the second call finds an attempt that is
    already ``paid`` and returns the order untouched; and if two callbacks race,
    the unique index on ``(provider, provider_ref)`` decides which one wrote it
    rather than whichever query ran first (X6).

    The amount is checked before the order moves. Money that arrives for a
    different sum is not a payment for this order in any sense worth
    automating, so the order goes to ``needs_attention`` rather than to
    ``paid``: something has to be understood before a ticket is bought with it.
    """
    order = await repository.lock_order(session, order_id)
    if order is None:
        raise NotFound("No such order")

    attempt = await repository.attempt_by_provider_ref(session, provider, provider_ref)
    if attempt is None:
        # A hosted redirect hands us a URL and no reference; the provider names
        # the charge when it first calls back, so the open attempt is bound to
        # that name here.
        attempt = await repository.unreferenced_attempt(session, order_id, provider)
        if attempt is None:
            raise NotFound("No such payment attempt")
        attempt.provider_ref = provider_ref
    locked = await repository.lock_attempt(session, attempt.id)
    if locked is None or locked.order_id != order.id:
        raise NotFound("No such payment attempt")
    attempt = locked
    attempt.provider_ref = provider_ref

    if attempt.status == TransactionStatus.PAID.value:
        logger.info("payment_already_settled", attempt_id=str(attempt.id))
        return order

    attempt.status = TransactionStatus.PAID.value
    attempt.paid_at = datetime.now(UTC)
    attempt.provider_state = provider_state
    try:
        await session.commit()
    except IntegrityError as clash:
        # Two references racing for one attempt. The unique index decided, and
        # the loser is a caller that must not be told it settled anything.
        await session.rollback()
        raise Conflict("This payment reference belongs to another attempt") from clash

    if order.amount_total is None or attempt.amount != order.amount_total:
        return await transition(
            session,
            order.id,
            to=OrderStatus.NEEDS_ATTENTION,
            actor=Actor.system("payments.callback"),
            action=EventAction.PAYMENT_MISMATCHED,
            reason="The settled amount is not the amount of this order",
            meta=provider_state,
            fields={
                "amount_paid": attempt.amount,
                "attention_reason": "payment_amount_mismatch",
            },
        )
    return await transition(
        session,
        order.id,
        to=OrderStatus.PAID,
        actor=Actor.system("payments.callback"),
        action=EventAction.PAYMENT_SETTLED,
        reason=f"Settled at {provider}",
        meta=provider_state,
        fields={"amount_paid": attempt.amount},
        # The ticketing step is due immediately; the poller is the safety net
        # for the moment the task fails to reach the queue (``O13``).
        next_attempt_at=datetime.now(UTC),
    )


async def start_transaction(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    data: TransactionStartIn,
) -> TransactionOut:
    """Open an attempt at paying and hand back where to send the customer.

    Four checks before anything leaves the building, in the order that costs
    least: the order is this customer's, the method is one this installation
    offers, the return address is one of ours, and the order is in a state that
    can be paid for.

    **The return address is checked against the installation's own origins.**
    A redirect target chosen by a request and handed to a payment provider is
    an open redirect wearing our merchant account's name; the provider will
    send the customer wherever it is told.
    """
    order = await owned_order(session, customer_id=customer_id, order_id=order_id)
    try:
        code = PaymentProviderCode(data.method)
    except ValueError as unknown:
        raise ValidationFailed("No such payment method", field="method") from unknown
    if not await _is_our_origin(data.return_url):
        raise ValidationFailed(
            "The return address is not one of this installation's own",
            field="return_url",
        )

    attempt = await start_attempt(
        session, order, provider=code.value, flow=TransactionFlow.REDIRECT.value
    )
    try:
        redirect_url = await payments_service.charge(
            session,
            code,
            reference=str(order.id),
            amount=attempt.amount,
            currency=attempt.currency,
            return_url=data.return_url,
        )
    except AppError as refused:
        # The row stays: what was tried is part of the record, and an attempt
        # that failed to start is the cheapest kind of evidence there is.
        await abandon_attempt(session, attempt, message=str(refused))
        raise
    attempt = await attach_redirect(session, attempt, redirect_url=redirect_url)
    return TransactionOut.from_attempt(attempt)


async def _is_our_origin(url: str) -> bool:
    """Whether a return address belongs to this installation.

    Reads the same list the browser is held to (``settings.cors_origins``), so
    turning an origin off closes both doors at once. A wildcard entry means the
    owner has deliberately opened the installation to any origin, and refusing
    here would contradict them.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}"
    allowed = await settings_service.cors_origins()
    return "*" in allowed or origin in allowed


async def owned_order(
    session: AsyncSession, *, customer_id: uuid.UUID, order_id: uuid.UUID
) -> Order:
    """The order, if it is this customer's — the check every write starts with."""
    order = await repository.order_by_id(session, customer_id, order_id)
    if order is None:
        raise NotFound("No such order")
    return order


async def owned_attempt(
    session: AsyncSession, *, customer_id: uuid.UUID, attempt_id: uuid.UUID
) -> OrderPayment:
    order_payment = await repository.attempt_by_id(session, customer_id, attempt_id)
    if order_payment is None:
        raise NotFound("No such transaction")
    return order_payment


# --- ticketing (order-system/03-design.md §3.7) ----------------------------------


def ticket_deadline(order: Order, settings: OrderSettingsOut) -> datetime:
    """The last moment ticketing may still be attempted.

    Earlier than the provider's own deadline by the configured margin, because
    a ticket bought a second before a hold lapses is a race nobody wins — and
    the retries have to fit inside what is left.

    A provider that named no deadline gets one of ours. That is not a guess at
    *their* rule: it is a bound on how long an order may sit open here, which
    is a thing we are entitled to decide (``hold_fallback_minutes``).
    """
    limit = order.ticket_time_limit_at
    if limit is None:
        started = order.paid_at or order.created_at
        limit = started + timedelta(minutes=settings.hold_fallback_minutes)
    return limit - timedelta(minutes=settings.ticket_margin_minutes)


async def claim_order(session: AsyncSession, order_id: uuid.UUID) -> Order | None:
    """The order, locked, for a background step that is about to work on it.

    A door rather than a peek at the repository: the ticketing task lives in
    ``app/tasks`` and a module's repository is nobody else's to reach into
    (ARCHITECTURE.md §4). ``None`` means the row is gone, which a task must
    treat as "nothing to do" rather than as an error.
    """
    return await repository.lock_order(session, order_id)


async def begin_ticketing(session: AsyncSession, order: Order) -> Order:
    """T8. Only from ``paid`` — a run already under way just carries on."""
    return await transition(
        session,
        order.id,
        to=OrderStatus.TICKETING,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_STARTED,
    )


async def retry_ticketing(
    session: AsyncSession, order: Order, *, reason: str, when: datetime
) -> Order:
    """T9 — the retry loop, and the only self-transition in the machine.

    ``attempts`` counts up rather than being passed in, so the schedule cannot
    disagree with the history about which try this was.
    """
    return await transition(
        session,
        order.id,
        to=OrderStatus.TICKETING,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_RETRY,
        reason=reason,
        next_attempt_at=when,
    )


async def finish_ticketing(
    session: AsyncSession, order: Order, result: TicketingResult
) -> Order:
    """T10, or T10x when only some of the tickets came out.

    Ticket numbers are merged onto the travellers already stored rather than
    replacing them: the booking answer carried document dates and contacts that
    a ticketing answer has no reason to repeat, and overwriting the list would
    quietly lose them.

    **Every traveller or none.** A partial issue is not a smaller success — the
    order has some tickets and some passengers without one, and nothing
    automatic can put that right.
    """
    issued = {
        traveler.position: traveler.ticket_number
        for traveler in result.travelers
        if traveler.ticket_number
    }
    travelers = [
        {**person, "ticket_number": issued.get(index, person.get("ticket_number"))}
        for index, person in enumerate(order.travelers, start=1)
    ]
    missing = [person for person in travelers if not person.get("ticket_number")]

    if missing or not travelers:
        return await transition(
            session,
            order.id,
            to=OrderStatus.NEEDS_ATTENTION,
            actor=Actor.system("orders.ticket"),
            action=EventAction.TICKETING_PARTIAL,
            reason=f"{len(missing)} of {len(travelers)} travellers have no ticket",
            meta=result.raw,
            fields={
                "travelers": travelers,
                "provider_status": result.provider_status,
                "provider_response": result.raw,
                "attention_reason": "partial_ticketing",
            },
        )

    return await transition(
        session,
        order.id,
        to=OrderStatus.TICKETED,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_SUCCEEDED,
        meta=result.raw,
        fields={
            "travelers": travelers,
            "provider_status": result.provider_status,
            "provider_response": result.raw,
        },
    )


async def send_to_refund(
    session: AsyncSession, order: Order, *, reason: str, failure: str | None = None
) -> Order:
    """T11 — ticketing is not going to happen and the money must go back.

    **No ``next_attempt_at``.** The refund itself is the next slice; until then
    an order that lands here is money taken and no ticket issued, which is a
    thing a person has to finish. It is logged at ``ERROR`` for that reason
    rather than left to be noticed.
    """
    logger.error(
        "order_awaiting_manual_refund",
        order_id=str(order.id),
        order_no=order.order_no,
        reason=reason,
    )
    return await transition(
        session,
        order.id,
        to=OrderStatus.REFUNDING,
        actor=Actor.system("orders.ticket"),
        action=EventAction.TICKETING_FAILED,
        reason=reason,
        fields={"failure_message": failure or reason},
    )


# --- what the customer reads ----------------------------------------------------


async def list_orders(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    customer_id: uuid.UUID,
    product: str | None = None,
    status: str | None = None,
) -> Page[OrderOut]:
    """This customer's orders, newest first (API.md §21).

    No ``apply_search`` yet: searching by PNR and passenger name is an admin
    need and arrives with that surface (API.md §31).
    """
    stmt = repository.owned_orders(customer_id)
    if product is not None:
        stmt = stmt.where(Order.product == product)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    stmt = apply_created_range(stmt, query, Order.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_ORDER_ORDERING, default="-created_at", tiebreak=Order.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([OrderOut.from_order(row) for row in rows], pagination, total)


async def get_order(
    session: AsyncSession, *, customer_id: uuid.UUID, order_id: uuid.UUID
) -> OrderOut:
    order = await repository.order_by_id(session, customer_id, order_id)
    if order is None:
        raise NotFound("No such order")
    return OrderOut.from_order(order)


__all__ = [
    "abandon_attempt",
    "apply_cancel",
    "attach_redirect",
    "begin_ticketing",
    "booking_answer",
    "claim_order",
    "confirm_booking",
    "ensure_cancellable",
    "fail_booking",
    "finish_ticketing",
    "get_order",
    "list_orders",
    "owned_attempt",
    "owned_by_provider_number",
    "owned_order",
    "payment_state",
    "record_unreadable_booking",
    "retry_ticketing",
    "send_to_refund",
    "settle_attempt",
    "start_attempt",
    "start_order",
    "start_transaction",
    "ticket_deadline",
    "transition",
]
