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
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import (
    AppError,
    Conflict,
    NotFound,
    PaymentFailed,
    RateLimited,
    UpstreamError,
)
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
from app.modules.integrations import service as integrations_service
from app.modules.orders import repository
from app.modules.orders.models import (
    ORDER_NO_SEQUENCE,
    Order,
    OrderEvent,
    OrderPayment,
    OrderRefund,
)
from app.modules.orders.schemas import (
    OrderListOut,
    OrderOut,
    TransactionOut,
)
from app.modules.orders.states import (
    INITIAL_STATUS,
    STAMPED_AT,
    Actor,
    EventAction,
    OrderStatus,
    RefundKind,
    RefundState,
    can,
)
from app.modules.payments import service as payments_service
from app.modules.payments.schemas import CardPaymentIn, OtpConfirmIn
from app.modules.settings.schemas import OrderSettingsOut
from app.providers.payments.base import (
    PaymentProviderCode,
    TransactionStatus,
)
from app.providers.products.base import registry
from app.providers.products.orders import (
    BookingResult,
    CancelResult,
    OrderOperations,
    TicketingResult,
    order_operations,
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
    """What ``booking/`` returns: the provider's answer, plus two keys (§20).

    **Additive, not nested.** The provider's answer stays exactly where it was
    before this module existed, and ``order`` and ``payment`` arrive beside it.
    Adding an optional field is not a breaking change; moving every existing
    field one level down is (API.md §1), and doing the second one cost the
    clients a release for no gain.

    ``order`` is ours — the record this booking wrote — and ``payment`` says
    what is still owed and until when; it is derived from the order rather than
    stored (``payment_state``).

    An order that never got an answer — a duplicate replayed while the first
    attempt is still in flight — carries **none** of the provider's keys at
    all. That is honest: we genuinely do not have one yet.
    """
    answer = dict(order.provider_response or {})
    # Ours win. Nothing GTS sends back from a booking is called either of
    # these, but their cancel answer does use ``order``, so this is not a
    # theoretical collision — it is one worth hearing about if it happens.
    for key in ("order", "payment"):
        if key in answer:
            logger.warning(
                "booking_answer_key_overwritten",
                order_id=str(order.id),
                order_no=order.order_no,
                key=key,
            )
    answer["order"] = OrderOut.from_order(order).model_dump(mode="json")
    answer["payment"] = payment_state(order)
    return answer


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


async def cancel_order(
    session: AsyncSession, *, customer_id: uuid.UUID, order_id: uuid.UUID
) -> OrderOut:
    """Release a reservation this customer made (API.md §21).

    Three steps, and their order is the whole of it: the order is found by its
    owner, the move is refused **before** the provider is called if the state
    does not allow it, and only then is the seat given back. Checking afterwards
    would release a real seat and then answer ``409``.

    The provider is not asked to validate anything for us. Its own cancel body
    is ``{"order_number": …}`` and that is what it gets — which field names a
    booking upstream is not ours to decide, and a wrong guess costs a seat
    (``providers/products/flight.py``).
    """
    order = await owned_order(session, customer_id=customer_id, order_id=order_id)
    ensure_cancellable(order)
    if order.provider_order_number is None:
        raise Conflict("This order has no reservation to release")

    operations = _order_operations(order.product)
    result = await operations.cancel(
        await integrations_service.gts_client(session),
        {"order_number": order.provider_order_number},
    )
    return OrderOut.from_order(await apply_cancel(session, order, result))


def _order_operations(product: str) -> OrderOperations:
    """The vertical's order half, or the gate's own ``404``.

    Unreachable in practice — an order exists only because its vertical booked
    it — but the narrowing is what makes the call type-safe, and answering with
    the gate's words keeps the two indistinguishable if it ever is reached.
    """
    adapter = registry.get(product)
    operations = None if adapter is None else order_operations(adapter)
    if operations is None:
        raise NotFound("This section is not available on this installation")
    return operations


async def apply_cancel(
    session: AsyncSession, order: Order, result: CancelResult
) -> Order:
    """Carry the provider's answer to a cancellation onto the row.

    Their code is kept beside ours rather than instead of it: ``CB`` is what
    they call it, ``cancelled`` is what happened. Reaching here at all means the
    seat is released — a refusal is an ``UpstreamError`` and never gets this
    far — so the move does not depend on what, if anything, they named it.

    An attempt still open goes with it, in this transaction: an order that is
    cancelled and a payment that is still collecting a card must not be two
    different facts about the same purchase.
    """
    await close_open_attempts(session, order)
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


#: How long a code we asked for is worth trying. Our own guard, not the
#: provider's — theirs is the one that rules on the code, and ours only stops an
#: attempt sitting open forever. Module constants rather than settings, for the
#: reason ``customers`` gives about the email OTP: PROJECT.md §7 asks whether two
#: clients could want different values, and here they could not — Payme and Click
#: issue the code and own its real lifetime.
CARD_OTP_TTL: Final = timedelta(minutes=3)
#: The floor between two codes. The provider's own wait wins when it is longer.
CARD_OTP_RESEND_FLOOR: Final = timedelta(seconds=60)
#: Wrong codes before the attempt is spent. Not a judgement on the code — that
#: is the provider's — but a limit on how long somebody may keep asking it
#: questions with the installation's merchant credentials.
CARD_OTP_MAX_ATTEMPTS: Final = 3


def _ensure_payable(order: Order) -> None:
    """Whether this order can take money at all, in the order that costs least."""
    if order.amount_total is None or order.currency is None:
        raise Conflict("This order has no amount to pay yet")
    if OrderStatus(order.status) is not OrderStatus.BOOKED:
        raise Conflict(
            f"An order that is {order.status} cannot be paid for",
            meta={"status": order.status},
        )
    if order.currency != _CARD_CURRENCY:
        # Not the request's fault and not fixable by retrying: the price came
        # from the provider and neither card API takes anything but so'm.
        raise Conflict(
            f"A card can only pay in {_CARD_CURRENCY}",
            meta={"currency": order.currency},
        )


#: Both card APIs take so'm and nothing else (API.md §22).
_CARD_CURRENCY: Final = "UZS"


async def start_attempt(
    session: AsyncSession, order: Order, *, provider: str
) -> OrderPayment:
    """Write the attempt, **then** let the caller talk to the provider.

    The order matters as much as it does at booking. A charge that exists at a
    provider and nowhere here is money nobody can match to an order; an attempt
    row with no ``provider_ref`` is merely an attempt that has not been answered
    yet, and reconciliation knows what to do with one (ARCHITECTURE.md §8).
    """
    _ensure_payable(order)
    assert order.amount_total is not None and order.currency is not None
    attempt = OrderPayment(
        order_id=order.id,
        provider=provider,
        status=TransactionStatus.AWAITING_CARD.value,
        amount=order.amount_total,
        currency=order.currency,
    )
    session.add(attempt)
    try:
        await session.commit()
    except IntegrityError as clash:
        # ``uq_order_payments_open`` — somebody opened one in the gap. The
        # caller is about to be told to use theirs, which is what it wanted.
        await session.rollback()
        raise Conflict("This order already has a payment in progress") from clash
    await session.refresh(attempt)
    logger.info(
        "payment_attempt_started",
        order_id=str(order.id),
        order_no=order.order_no,
        attempt_id=str(attempt.id),
        provider=provider,
    )
    return attempt


def _release_token(attempt: OrderPayment) -> payments_service.CardToken | None:
    """Drop the provider's token from the row, returning it for one last use.

    Called on every path out of an open attempt, and not out of tidiness: the
    ``open_attempts_hold_the_token`` CHECK refuses a closed row that still
    carries one, so forgetting this is a failed commit rather than a slow leak.
    """
    if attempt.card_token is None:
        return None
    token = payments_service.CardToken(
        ciphertext=attempt.card_token,
        key_version=attempt.card_token_key_version or 0,
    )
    attempt.card_token = None
    attempt.card_token_key_version = None
    return token


async def abandon_attempt(
    session: AsyncSession,
    attempt: OrderPayment,
    *,
    message: str,
    code: str | None = None,
) -> None:
    """The attempt is spent. The row stays as evidence of what was tried."""
    token = _release_token(attempt)
    attempt.status = TransactionStatus.FAILED.value
    attempt.error_code = code
    attempt.error_message = message
    await session.commit()
    if token is not None:
        await payments_service.forget_card(
            session, PaymentProviderCode(attempt.provider), token=token
        )
    logger.warning(
        "payment_attempt_abandoned",
        attempt_id=str(attempt.id),
        reason=message,
        error_code=code,
    )


async def close_open_attempts(session: AsyncSession, order: Order) -> None:
    """Shut the attempt an order leaves behind. **Does not commit.**

    Runs inside the cancellation it belongs to, so an order that closes and an
    attempt that stays open cannot be two different facts. The token goes with
    it — the CHECK would refuse the row otherwise — and is not handed back to
    the provider from here: it is single-use and short-lived on both of them,
    and a network call inside a cancellation would put a provider between a
    customer and their released seat.

    A ``pending`` attempt is **left alone**. The charge went out and the answer
    is unknown; marking it cancelled would file a payment that may have happened
    as one that did not. ``payments.reconcile`` is what closes those.
    """
    attempt = await repository.open_attempt(session, order.id, lock=True)
    if attempt is None:
        return
    if attempt.status == TransactionStatus.PENDING.value:
        logger.warning(
            "payment_attempt_left_pending",
            attempt_id=str(attempt.id),
            order_id=str(order.id),
            detail="a charge was in flight when the order closed",
        )
        return
    _release_token(attempt)
    attempt.status = TransactionStatus.CANCELLED.value
    logger.info("payment_attempt_cancelled", attempt_id=str(attempt.id))


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
        # The charge is normally named before it is paid — ``charge_card`` calls
        # back with the reference and the row is written then — but a provider
        # that calls us with a name we never saw is still talking about the
        # attempt that is in flight, so it is bound to that one here.
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
    # A spent token is not kept, and the CHECK on the table would refuse the row
    # if it were. Nothing is sent to the provider: it has just been used, and
    # both of them hand out single-use tokens for a charge like this.
    _release_token(attempt)
    attempt.otp_expires_at = None
    attempt.otp_resend_after = None
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


def _out(attempt: OrderPayment) -> TransactionOut:
    return TransactionOut.from_attempt(attempt, otp_max_attempts=CARD_OTP_MAX_ATTEMPTS)


#: How long an attempt may sit waiting for a card or a code before the sweep
#: closes it. Generous next to ``CARD_OTP_TTL`` on purpose: the customer may be
#: asking for a second code, and the cost of waiting is a row, while the cost of
#: being early is a checkout that closes under somebody's hands.
STALE_ATTEMPT_AFTER: Final = timedelta(minutes=30)

#: How long a charge may go unanswered before the provider is asked about it.
#: Long enough that an ordinary slow call is not mistaken for a lost one.
UNANSWERED_CHARGE_AFTER: Final = timedelta(minutes=5)


async def expire_stale_attempts(session: AsyncSession, *, limit: int = 50) -> int:
    """Close the checkouts nobody came back to.

    An abandoned attempt is not harmless: it holds the order's one open slot,
    so the customer who returns tomorrow could not start again until it went.
    The token goes with it, which the CHECK on the table insists on anyway.
    """
    cutoff = datetime.now(UTC) - STALE_ATTEMPT_AFTER
    stale = await repository.stale_attempts(session, before=cutoff, limit=limit)
    for attempt in stale:
        _release_token(attempt)
        attempt.status = TransactionStatus.CANCELLED.value
        attempt.error_code = "abandoned"
    if stale:
        await session.commit()
        logger.info("payment_attempts_expired", count=len(stale))
    return len(stale)


async def reconcile_attempt(session: AsyncSession, attempt: OrderPayment) -> bool:
    """Ask the provider what happened to a charge that never answered.

    The reason ``pending`` exists rather than being called ``failed``: the money
    may have moved, and the only party that knows is the one that moved it. A
    provider still saying "pending" is left alone — being asked twice costs
    nothing, and guessing costs a customer their money or their seat.

    Returns whether the attempt was resolved.
    """
    if attempt.provider_ref is None:  # pragma: no cover - the query excludes these
        return False
    code = PaymentProviderCode(attempt.provider)
    try:
        answer = await payments_service.charge_status(
            session, code, transaction_ref=attempt.provider_ref
        )
    except AppError as unreachable:
        logger.warning(
            "payment_reconcile_failed",
            attempt_id=str(attempt.id),
            reason=str(unreachable),
        )
        return False

    if answer.status is TransactionStatus.PAID:
        await settle_attempt(
            session,
            provider=code.value,
            provider_ref=attempt.provider_ref,
            order_id=attempt.order_id,
            provider_state=(
                {"state": answer.provider_state}
                if answer.provider_state is not None
                else None
            ),
        )
        logger.info("payment_reconciled_as_paid", attempt_id=str(attempt.id))
        return True
    if answer.status in (TransactionStatus.FAILED, TransactionStatus.CANCELLED):
        await abandon_attempt(
            session,
            attempt,
            message=answer.failure_message or "The provider did not take the money",
            code=answer.failure_code or "charge_declined",
        )
        return True
    return False


async def reconcile_payments(session: AsyncSession, *, limit: int = 50) -> int:
    """Every charge that went out and never came back."""
    cutoff = datetime.now(UTC) - UNANSWERED_CHARGE_AFTER
    unanswered = await repository.reconcilable_attempts(
        session, before=cutoff, limit=limit
    )
    resolved = 0
    for attempt in unanswered:
        if await reconcile_attempt(session, attempt):
            resolved += 1
    if unanswered:
        logger.info("payments_reconciled", asked=len(unanswered), resolved=resolved)
    return resolved


async def start_transaction(
    session: AsyncSession, *, customer_id: uuid.UUID, order_id: uuid.UUID
) -> TransactionOut:
    """Open an attempt at paying, or hand back the one already open.

    There is nothing to choose. The provider is the installation's, not the
    request's (``O15``), and with no hosted page there is nowhere to come back
    from — so this takes an empty body and the only questions left are about
    the order.

    **Idempotent without a key.** A customer who opened the checkout, wandered
    off and came back gets the attempt they left rather than a second one
    against the same order; ``uq_order_payments_open`` is what makes that true
    under a race rather than merely usually. A ``pending`` attempt is different
    and answers ``409``: a charge is in flight and may have moved money, and
    starting another over the top of it is the one thing that must not happen.
    """
    order = await owned_order(session, customer_id=customer_id, order_id=order_id)
    _ensure_payable(order)

    existing = await repository.open_attempt(session, order.id)
    if existing is not None:
        if existing.status == TransactionStatus.PENDING.value:
            raise Conflict("A payment for this order is still being processed")
        return _out(existing)

    provider = await integrations_service.active_payment_provider(session)
    if provider is None:
        # No attempt row: an attempt is evidence of a conversation with a
        # provider, and there was no provider to have one with.
        raise UpstreamError("No payment method is available")
    return _out(await start_attempt(session, order, provider=provider.code.value))


async def _open_attempt_for(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    attempt_id: uuid.UUID,
    expected: tuple[str, ...],
) -> OrderPayment:
    """This customer's attempt, locked, and in a state this step can act on.

    Someone else's answers ``404`` exactly as a missing one does — the rule §19
    states for cards and passengers, and an attempt id is worth no more guessing
    than either.
    """
    attempt = await repository.attempt_by_id(session, customer_id, attempt_id)
    if attempt is None:
        raise NotFound("No such payment attempt")
    locked = await repository.lock_attempt(session, attempt.id)
    if locked is None:
        raise NotFound("No such payment attempt")
    if locked.status not in expected:
        raise Conflict(
            f"A payment that is {locked.status} cannot take this step",
            meta={"status": locked.status},
        )
    return locked


def _token_of(attempt: OrderPayment) -> payments_service.CardToken:
    if attempt.card_token is None:
        # Only reachable if a row were hand-edited: the CHECK pairs an open
        # attempt past the card step with a token.
        raise Conflict("This payment has no card on it yet")
    return payments_service.CardToken(
        ciphertext=attempt.card_token,
        key_version=attempt.card_token_key_version or 0,
    )


def _stamp_code(attempt: OrderPayment, *, wait_seconds: int | None) -> None:
    """Record that a code has just gone out."""
    now = datetime.now(UTC)
    wait = max(CARD_OTP_RESEND_FLOOR, timedelta(seconds=wait_seconds or 0))
    attempt.otp_expires_at = now + CARD_OTP_TTL
    attempt.otp_resend_after = now + wait


async def submit_card(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    attempt_id: uuid.UUID,
    data: CardPaymentIn,
) -> TransactionOut:
    """Hand the card to the provider, which texts the customer a code.

    Accepted from ``awaiting_otp`` as well as ``awaiting_card``: a customer who
    mistyped a digit should be able to type it again rather than start over, and
    the cost of allowing it is one extra SMS against a bucket that already
    allows ten requests a minute. The previous registration is dropped first, so
    only one token is ever live on an attempt.

    A refusal leaves the attempt **open**. The card was wrong, not the payment;
    failing the attempt here would turn a typo into a round trip through
    ``transactions/`` and a row in the history for every mistyped digit.
    """
    attempt = await _open_attempt_for(
        session,
        customer_id=customer_id,
        attempt_id=attempt_id,
        expected=(
            TransactionStatus.AWAITING_CARD.value,
            TransactionStatus.AWAITING_OTP.value,
        ),
    )
    order = await owned_order(
        session, customer_id=customer_id, order_id=attempt.order_id
    )
    _ensure_payable(order)

    code = PaymentProviderCode(attempt.provider)
    superseded = _release_token(attempt)
    if superseded is not None:
        await session.commit()
        await payments_service.forget_card(session, code, token=superseded)

    registration = await payments_service.register_card(
        session, code, customer_id=customer_id, data=data
    )

    attempt.card_token = registration.token.ciphertext
    attempt.card_token_key_version = registration.token.key_version
    attempt.card_masked = registration.masked_pan
    attempt.card_last4 = registration.last4
    attempt.card_brand = registration.brand
    attempt.card_id = data.card_id
    attempt.otp_sent_to = registration.otp_sent_to
    # A new card is a new count. The limit is about guessing one code, not about
    # how long the customer has been at the checkout.
    attempt.otp_attempts = 0
    attempt.status = TransactionStatus.AWAITING_OTP.value
    _stamp_code(attempt, wait_seconds=registration.otp_wait_seconds)
    await session.commit()
    await session.refresh(attempt)
    logger.info(
        "payment_card_registered",
        attempt_id=str(attempt.id),
        saved_card=data.card_id is not None,
    )
    return _out(attempt)


async def resend_code(
    session: AsyncSession, *, customer_id: uuid.UUID, attempt_id: uuid.UUID
) -> TransactionOut:
    """Ask the provider to text the code again.

    The cooldown is on the row rather than in the rate limiter, because it is a
    property of this attempt and has to survive a Redis that was flushed. It
    does **not** reset the wrong-code count: a counter a customer can clear by
    pressing "resend" is not a counter.
    """
    attempt = await _open_attempt_for(
        session,
        customer_id=customer_id,
        attempt_id=attempt_id,
        expected=(TransactionStatus.AWAITING_OTP.value,),
    )
    now = datetime.now(UTC)
    if attempt.otp_resend_after is not None and now < attempt.otp_resend_after:
        raise RateLimited(
            "The code was only just sent",
            retry_after=max(1, int((attempt.otp_resend_after - now).total_seconds())),
        )

    code = PaymentProviderCode(attempt.provider)
    resent = await payments_service.resend_card_code(
        session, code, token=_token_of(attempt)
    )
    attempt.otp_sent_to = resent.otp_sent_to or attempt.otp_sent_to
    _stamp_code(attempt, wait_seconds=resent.otp_wait_seconds)
    await session.commit()
    await session.refresh(attempt)
    logger.info("payment_card_code_resent", attempt_id=str(attempt.id))
    return _out(attempt)


#: Every way a code can be refused says the same thing. Which of them it was
#: only helps somebody working through the space (API.md §22).
_CODE_REJECTED: Final = "The code was not accepted"


async def _count_wrong_code(session: AsyncSession, attempt: OrderPayment) -> None:
    """One wrong code, and the attempt if that was the last one it had."""
    attempt.otp_attempts += 1
    if attempt.otp_attempts >= CARD_OTP_MAX_ATTEMPTS:
        await abandon_attempt(
            session, attempt, message=_CODE_REJECTED, code="otp_exhausted"
        )
        return
    await session.commit()


async def confirm_payment(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    attempt_id: uuid.UUID,
    data: OtpConfirmIn,
) -> TransactionOut:
    """Verify the code and take the money — the whole of the payment (``O16``).

    The order of the two provider calls is the point. Verifying is free to fail
    and changes nothing, so it goes first and a wrong code costs one counter.
    Charging is not free to fail: between "the provider took it" and "we wrote
    that down" there is a window nothing else closes, so the attempt is moved to
    ``pending`` and **committed** before the charge goes out, and the reference
    is written the instant the provider names it. A process that dies anywhere
    after that leaves a row reconciliation can follow.

    A repeat on an attempt already ``paid`` returns the same answer rather than
    charging again — replay by state, which is what stands in for an
    ``Idempotency-Key`` this endpoint must not have.
    """
    attempt = await repository.attempt_by_id(session, customer_id, attempt_id)
    if attempt is None:
        raise NotFound("No such payment attempt")
    locked = await repository.lock_attempt(session, attempt.id)
    if locked is None:
        raise NotFound("No such payment attempt")
    if locked.status == TransactionStatus.PAID.value:
        return _out(locked)
    if locked.status != TransactionStatus.AWAITING_OTP.value:
        raise Conflict(
            f"A payment that is {locked.status} cannot be confirmed",
            meta={"status": locked.status},
        )
    attempt = locked

    now = datetime.now(UTC)
    if attempt.otp_expires_at is not None and now > attempt.otp_expires_at:
        # The attempt stays open on purpose: an expired code is recovered by
        # asking for another, not by starting the payment again.
        raise PaymentFailed(_CODE_REJECTED)

    code = PaymentProviderCode(attempt.provider)
    token = _token_of(attempt)
    try:
        await payments_service.verify_card(
            session, code, token=token, otp_code=data.otp_code
        )
    except PaymentFailed:
        await _count_wrong_code(session, attempt)
        raise PaymentFailed(_CODE_REJECTED) from None

    attempt.status = TransactionStatus.PENDING.value
    await session.commit()

    async def name_the_charge(reference: str) -> None:
        attempt.provider_ref = reference
        await session.commit()

    try:
        result = await payments_service.charge_card(
            session,
            code,
            token=token,
            reference=str(attempt.order_id),
            amount=attempt.amount,
            currency=attempt.currency,
            on_reference=name_the_charge,
        )
    except AppError as refused:
        await abandon_attempt(
            session, attempt, message=str(refused), code="charge_failed"
        )
        raise

    if result.status is not TransactionStatus.PAID or result.provider_ref is None:
        await abandon_attempt(
            session,
            attempt,
            message=result.failure_message or "The payment was declined",
            code=result.failure_code or "charge_declined",
        )
        raise PaymentFailed(result.failure_message or "The payment was declined")

    await settle_attempt(
        session,
        provider=code.value,
        provider_ref=result.provider_ref,
        order_id=attempt.order_id,
        provider_state=(
            {"state": result.provider_state}
            if result.provider_state is not None
            else None
        ),
    )
    await session.refresh(attempt)
    return _out(attempt)


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


async def due_for_expiry(
    session: AsyncSession, settings: OrderSettingsOut, *, limit: int
) -> Sequence[Order]:
    """Unpaid holds whose window has closed — a door for the sweep."""
    now = datetime.now(UTC)
    return await repository.expiring_orders(
        session,
        deadline=now + timedelta(minutes=settings.ticket_margin_minutes),
        fallback_before=now - timedelta(minutes=settings.hold_fallback_minutes),
        limit=limit,
    )


async def expire_booking(session: AsyncSession, order: Order) -> Order:
    """T7 — the hold lapsed unpaid, so the seat goes back.

    Distinct from a customer cancelling only by its reason, and that is the
    whole reason ``cancellation_reason`` exists rather than a second status: the
    two look identical to everything except a report (order-system §3.3).

    A half-finished payment closes with it — the seat is gone, so a customer
    still typing a code into it is being asked for money for nothing.
    """
    await close_open_attempts(session, order)
    logger.info(
        "order_expired",
        order_id=str(order.id),
        order_no=order.order_no,
        ticket_time_limit_at=(
            None
            if order.ticket_time_limit_at is None
            else order.ticket_time_limit_at.isoformat()
        ),
    )
    return await transition(
        session,
        order.id,
        to=OrderStatus.CANCELLED,
        actor=Actor.system("orders.expire"),
        action=EventAction.BOOKING_EXPIRED,
        reason="The hold lapsed before the order was paid for",
        fields={"cancellation_reason": "timelimit"},
    )


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

    The refund row is written in the **same transaction** as the status change,
    already approved: nobody has to agree that a customer who paid for nothing
    should be repaid, and a compensation waiting for a signature is a
    compensation that does not happen at three in the morning.

    ``next_attempt_at`` is set to now, so the sweep picks it up on its next
    pass exactly as it does a ticketing step.
    """
    attempt = await repository.paid_attempt(session, order.id)
    session.add(
        OrderRefund(
            order_id=order.id,
            payment_id=None if attempt is None else attempt.id,
            kind=RefundKind.AUTO.value,
            status=RefundState.APPROVED.value,
            amount=order.amount_paid,
            penalty_amount=Decimal("0"),
            currency=order.currency or "UZS",
            reason=reason,
        )
    )
    logger.error(
        "order_compensating",
        order_id=str(order.id),
        order_no=order.order_no,
        amount=str(order.amount_paid),
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
        next_attempt_at=datetime.now(UTC),
    )


# --- giving the money back (order-system/03-design.md T15, T17) ------------------


async def settled_attempt(session: AsyncSession, order: Order) -> OrderPayment | None:
    """The attempt that actually took the money — a door, not a peek.

    The refund task lives in ``app/tasks`` and a module's repository is nobody
    else's to reach into (ARCHITECTURE.md §4).
    """
    return await repository.paid_attempt(session, order.id)


async def claim_refund(session: AsyncSession, order: Order) -> OrderRefund | None:
    """The refund still running on this order, locked and marked ``processing``.

    ``None`` means there is nothing to do — the order says ``refunding`` and no
    refund is open, which is a state only a bug or a hand-edit can produce, and
    one the caller must not paper over by inventing a refund.
    """
    refund = await repository.open_refund(session, order.id)
    if refund is None:
        return None
    if refund.status == RefundState.REQUESTED.value:
        # Still waiting for a person. Nothing automatic may take it further.
        return None
    refund.status = RefundState.PROCESSING.value
    await session.commit()
    return refund


async def settle_refund(
    session: AsyncSession,
    order: Order,
    refund: OrderRefund,
    *,
    provider_ref: str | None,
    order_action: str | None,
) -> Order:
    """T15 — the money is back with the customer."""
    refund.status = RefundState.SUCCEEDED.value
    refund.provider_refund_ref = provider_ref
    refund.provider_order_action = order_action
    await session.commit()
    logger.info(
        "order_refunded",
        order_id=str(order.id),
        order_no=order.order_no,
        amount=str(refund.amount),
    )
    return await transition(
        session,
        order.id,
        to=OrderStatus.REFUNDED,
        actor=Actor.system("orders.refund"),
        action=EventAction.REFUND_SUCCEEDED,
        reason=refund.reason,
        fields={"amount_refunded": refund.amount},
    )


async def retry_refund(
    session: AsyncSession, order: Order, *, reason: str, when: datetime
) -> Order:
    """T15x — try again. The self-transition that keeps the history honest."""
    return await transition(
        session,
        order.id,
        to=OrderStatus.REFUNDING,
        actor=Actor.system("orders.refund"),
        action=EventAction.REFUND_RETRY,
        reason=reason,
        next_attempt_at=when,
    )


async def abandon_refund(
    session: AsyncSession, order: Order, refund: OrderRefund | None, *, reason: str
) -> Order:
    """T17 — the last automatic step failed, so a person takes over.

    This is the state PROJECT.md D3 promises exists: money has moved, no ticket
    came out of it, and the refund did not work either. It is loud on purpose —
    the alternative is money quietly going nowhere, which is the one outcome
    this whole design exists to prevent.
    """
    if refund is not None:
        refund.status = RefundState.FAILED.value
        refund.failure_message = reason
        await session.commit()
    logger.error(
        "order_needs_attention",
        order_id=str(order.id),
        order_no=order.order_no,
        amount_paid=str(order.amount_paid),
        reason=reason,
    )
    return await transition(
        session,
        order.id,
        to=OrderStatus.NEEDS_ATTENTION,
        actor=Actor.system("orders.refund"),
        action=EventAction.REFUND_FAILED,
        reason=reason,
        fields={"attention_reason": "refund_failed"},
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
) -> Page[OrderListOut]:
    """This customer's orders, newest first (API.md §21).

    ``OrderListOut``, not ``OrderOut``: a card wants a route, a price and a
    status, and the two heaviest fields on the row — the provider's answer and
    the travellers — are neither. The provider's answer is not even read out of
    Postgres, which is what ``defer`` is doing on the query below.

    No ``apply_search`` yet: searching by PNR and passenger name is an admin
    need and arrives with that surface (API.md §31).
    """
    stmt = repository.owned_orders(customer_id).options(defer(Order.provider_response))
    if product is not None:
        stmt = stmt.where(Order.product == product)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    stmt = apply_created_range(stmt, query, Order.created_at)
    stmt = apply_ordering(
        stmt, query, allowed=_ORDER_ORDERING, default="-created_at", tiebreak=Order.id
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([OrderListOut.from_order(row) for row in rows], pagination, total)


async def get_order(
    session: AsyncSession, *, customer_id: uuid.UUID, order_id: uuid.UUID
) -> OrderOut:
    order = await repository.order_by_id(session, customer_id, order_id)
    if order is None:
        raise NotFound("No such order")
    return OrderOut.from_order(order)


__all__ = [
    "abandon_attempt",
    "abandon_refund",
    "apply_cancel",
    "cancel_order",
    "close_open_attempts",
    "confirm_payment",
    "begin_ticketing",
    "booking_answer",
    "claim_order",
    "claim_refund",
    "confirm_booking",
    "due_for_expiry",
    "ensure_cancellable",
    "expire_booking",
    "expire_stale_attempts",
    "fail_booking",
    "finish_ticketing",
    "get_order",
    "list_orders",
    "owned_attempt",
    "owned_by_provider_number",
    "owned_order",
    "payment_state",
    "reconcile_attempt",
    "reconcile_payments",
    "record_unreadable_booking",
    "resend_code",
    "retry_refund",
    "retry_ticketing",
    "send_to_refund",
    "settle_attempt",
    "settle_refund",
    "settled_attempt",
    "start_attempt",
    "start_order",
    "start_transaction",
    "submit_card",
    "ticket_deadline",
    "transition",
]
