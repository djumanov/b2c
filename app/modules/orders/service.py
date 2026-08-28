"""Orders: create on booking, read back by owner, pay, and keep the books straight.

``create_order`` is called by ``products.service.book()`` **after** GTS
confirmed the booking; it is the flow's only write and its own transaction.
There is nothing here for a failed booking on purpose: no row is the record.

Paying starts with the price: ``reprice_order`` asks GTS what the hold
costs today and hands the answer through untouched, and ``confirm_price``
accepts a price that moved, which is when the order changes. A price that
did **not** move is settled by the question itself — GTS refuses to confirm
one — so that is the one thing the question writes.
GTS's own lifecycle puts ``reprice_check`` and ``reprice_confirm`` before
``ticketing`` and refuses a ticket without the check. Then three calls —
``start_payment`` sends the cardholder a code, ``resend_payment_otp`` sends
it again for the same attempt, ``confirm_payment`` charges with it — and
every write between them follows one shape: **lock → re-read → validate →
mutate → commit**. A network call
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

import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

from sqlalchemy import String, and_, cast, delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination, Staff
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
from app.core.money import CURRENCY, Money
from app.db.mixins import utcnow
from app.db.repository import live
from app.modules.audit import context as audit_context
from app.modules.integrations import service as integrations_service
from app.modules.orders import lifecycle
from app.modules.orders.lifecycle import Stage
from app.modules.orders.messages import DEFAULTS, MessageCatalogue
from app.modules.orders.models import (
    AttemptStatus,
    CancelReason,
    Order,
    OrderEvent,
    OrderMessage,
    OrderStatus,
    PaymentAttempt,
    PaymentStatus,
    TicketingStatus,
)
from app.modules.orders.schemas import (
    ADMIN_RECEIPT_PATH,
    RECEIPT_PATH,
    BookingResultOut,
    OrderAdminListItemOut,
    OrderAdminOrderOut,
    OrderAdminOut,
    OrderEventOut,
    OrderListItemOut,
    OrderMessageIn,
    OrderMessageOut,
    PaymentAttemptAdminOut,
    PaymentAttemptView,
    PaymentConfirmIn,
    PaymentResendIn,
    PaymentStartIn,
    ReceiptDocument,
    RefundIn,
    RepriceOut,
    strip_commission,
)
from app.modules.payments import service as payments_service
from app.modules.settings import service as settings_service
from app.providers.payments.base import (
    OtpRejected,
    PaymentDeclined,
    PaymentOutcome,
    PaymentProvider,
)
from app.providers.products import gts_order
from app.providers.products.base import (
    BookedOrder,
    OrderPrice,
    OrderSnapshot,
    ProductAdapter,
    registry,
)

logger = get_logger(__name__)

_ORDER_ORDERING: OrderingMap = {
    "created_at": Order.created_at,
    "updated_at": Order.updated_at,
}

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


def _receipt_url(order: Order, *, admin: bool = False) -> str | None:
    """Where this answer's reader downloads the receipt, or nothing yet.

    Only a ticketed order has one: before that GTS has nothing to render, and
    a link that answers with a refusal is worse than no link.

    It is **our** path, not GTS's. GTS renders the document but will not hand
    it to a browser — its receipt page answers ``401`` without the agent
    session's cookies — so the route below fetches it with ours. Each surface
    gets the path its own token opens.
    """
    if order.ticketing_status != TicketingStatus.TICKETED:
        return None
    template = ADMIN_RECEIPT_PATH if admin else RECEIPT_PATH
    return template.format(id=order.id)


async def _present(
    session: AsyncSession, order: Order, *, language: str | None
) -> BookingResultOut:
    """The detail shape, with the message rendered for this request."""
    messages = await message_catalogue(session)
    attempt = await _latest_attempt(session, order.id)
    return BookingResultOut.from_order(
        order,
        language=language,
        messages=messages,
        attempt=_view(attempt),
        receipt_url=_receipt_url(order),
    )


# --- the messages (``/admin/orders/messages/``) ------------------------------------


async def _message_rows(session: AsyncSession) -> list[OrderMessage]:
    """One row per status and none besides — kept so on read, not by migration.

    A status a release adds gets its row; a status a release retires loses
    it, text and all: nothing renders that key any more, and a row the panel
    cannot reach is not a setting.
    """
    rows = (await session.scalars(select(OrderMessage))).all()
    known = {status.value for status in Stage}
    present = {row.key for row in rows}
    missing = known - present
    stale = present - known
    if missing:
        await session.execute(
            pg_insert(OrderMessage)
            .values([{"id": uuid.uuid4(), "key": key, "text": {}} for key in missing])
            .on_conflict_do_nothing()
        )
    if stale:
        await session.execute(delete(OrderMessage).where(OrderMessage.key.in_(stale)))
    if missing or stale:
        await session.commit()
        rows = (await session.scalars(select(OrderMessage))).all()
    return list(rows)


async def message_catalogue(session: AsyncSession) -> MessageCatalogue:
    """The panel's sentences over our defaults, with the installation's
    language chain — what every order response renders ``message`` from."""
    rows = (await session.scalars(select(OrderMessage))).all()
    languages = await settings_service.get_languages(session)
    return MessageCatalogue(
        overrides={row.key: row.text for row in rows},
        default_language=languages.default,
        available=tuple(languages.available),
    )


def _message_out(row: OrderMessage, catalogue: MessageCatalogue) -> OrderMessageOut:
    status = Stage(row.key)
    return OrderMessageOut(
        status=status,
        default=DEFAULTS[status],
        custom=row.text,
        text=catalogue.text(status),
    )


async def list_messages(session: AsyncSession) -> list[OrderMessageOut]:
    rows = {row.key: row for row in await _message_rows(session)}
    catalogue = await message_catalogue(session)
    return [_message_out(rows[status.value], catalogue) for status in Stage]


async def get_message(session: AsyncSession, status: Stage) -> OrderMessageOut:
    rows = {row.key: row for row in await _message_rows(session)}
    return _message_out(rows[status.value], await message_catalogue(session))


async def update_message(
    session: AsyncSession, status: Stage, data: OrderMessageIn
) -> OrderMessageOut:
    """Languages merge per language (the settings/CMS PATCH rule); an empty
    string clears that language so the default shows again."""
    rows = {row.key: row for row in await _message_rows(session)}
    row = rows[status.value]
    before = dict(row.text)
    merged = {**row.text, **data.text}
    row.text = {lang: text for lang, text in merged.items() if text.strip()}
    await session.commit()
    await session.refresh(row)
    audit_context.describe(
        resource_id=row.id,
        changes=audit_context.diff(before, row.text),
    )
    logger.info(
        "order_message_updated", status=status.value, languages=sorted(row.text)
    )
    return _message_out(row, await message_catalogue(session))


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

    A **confirmed price is not overwritten**: once ``reprice_confirm`` has
    run, ``price_response`` is GTS's later word on what ticketing debits,
    and the order record's own ``price_info`` is the booking's. A read that
    disagrees is logged, not believed — ``gts_response`` is still replaced
    whole (it is the record, verbatim), and ``order_data`` shows the
    confirmed figures over it.

    A price in **another currency is not copied either**, and this is the one
    place that discards rather than raises: the same function runs inside the
    sweep, the cancellation and the ticketing steps, where an exception over a
    figure nobody was about to charge would break work that has nothing to do
    with the price. The order keeps whatever it held, the record still
    updates, and the ERROR line is what a person acts on.
    """
    if snapshot.gts_status:
        order.gts_status = snapshot.gts_status
    if snapshot.gts_order_uid:
        order.gts_order_uid = snapshot.gts_order_uid
    if snapshot.pnr:
        order.pnr = snapshot.pnr
    if snapshot.currency and snapshot.currency != CURRENCY:
        logger.error(
            "gts_price_foreign_currency",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
            step="retrieve",
            answered=f"{snapshot.amount} {snapshot.currency}",
        )
    elif snapshot.amount is not None and snapshot.currency:
        if order.price_confirmed_at is None:
            order.amount = snapshot.amount
            order.currency = snapshot.currency
        elif (snapshot.amount, snapshot.currency) != (order.amount, order.currency):
            logger.warning(
                "gts_price_differs_from_confirmed",
                order_id=str(order.id),
                confirmed=f"{order.amount} {order.currency}",
                read=f"{snapshot.amount} {snapshot.currency}",
            )
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
    """Record one confirmed GTS booking — and the first line of its history.

    A price GTS quoted in another currency is **not recorded** — the order is,
    with no price. GTS is holding the seat by the time this runs, so refusing
    the whole booking would lose the record of a hold that exists; and the
    module already records a booking whose price could not be read at all.
    ``payment/`` then stops with "GTS did not report a price for this order",
    the ERROR line below is the alarm, and support sees the order in
    ``/admin/orders/``.
    """
    amount, currency = booked.amount, booked.currency
    if currency and currency != CURRENCY:
        # No ``order_id`` yet — the row is minted below. GTS's own number is
        # what identifies this booking until then, and it is what support
        # quotes to GTS anyway.
        logger.error(
            "gts_price_foreign_currency",
            gts_order_number=booked.gts_order_number,
            step="booking",
            answered=f"{amount} {currency}",
        )
        amount, currency = None, None

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
        amount=amount,
        currency=currency,
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


# --- the receipt -----------------------------------------------------------------

#: What GTS may render a receipt as, and the extension the download is named
#: with. Anything else is served as bytes to save rather than as something a
#: browser will open — an installation we have not met must not be able to
#: put a document of its choosing on this application's own origin.
RECEIPT_TYPES: Final[dict[str, str]] = {
    "application/pdf": "pdf",
    "text/html": "html",
}


async def _fetch_receipt(session: AsyncSession, order: Order) -> ReceiptDocument:
    """The itinerary receipt of a ticketed order, fetched from GTS per request.

    **Nothing is stored**, and that is the search rule applied to a file: GTS
    renders the document from the order it holds, so the copy asked for now
    is the only one guaranteed to say what the ticket says. A receipt kept in
    our database would be a second truth, stale from the first change GTS
    made to the booking.

    Only once the ticket exists — the caller has already refused anything
    else, in our own words, rather than letting GTS's refusal reach the
    customer as a ``502``: ``ticketing_status`` knows the answer without
    asking. Nothing here writes, reads GTS's order back or settles anything.
    """
    if order.ticketing_status != TicketingStatus.TICKETED:
        raise Conflict("The ticket for this order has not been issued yet")
    adapter = _adapter(order)
    client = await integrations_service.gts_client(session)
    document = await adapter.receipt(client, order.gts_order_number)
    if document is None:
        # GTS has the ticket and has drawn no paper for it yet — its own
        # answer, not a failure of ours, and not the customer's fault either.
        # A ``409`` says "not now"; the sentence says whose "not now" it is.
        logger.warning(
            "gts_receipt_absent",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
        )
        raise Conflict("GTS has not made the receipt for this order available yet")
    extension = RECEIPT_TYPES.get(document.content_type)
    if extension is None:
        # GTS rendered something we have not seen it render before. The bytes
        # are still the customer's, so they are served — as a file to save.
        logger.warning(
            "gts_receipt_unexpected_type",
            order_id=str(order.id),
            content_type=document.content_type,
        )
    # The PNR is GTS's text and the filename ends up in a response header, so
    # only the letters and digits of it travel.
    name = "".join(char for char in (order.pnr or "") if char.isalnum()) or str(
        order.gts_order_number
    )
    return ReceiptDocument(
        content=document.content,
        content_type=(
            document.content_type if extension else "application/octet-stream"
        ),
        filename=f"receipt-{name}.{extension or 'bin'}",
    )


async def order_receipt(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> ReceiptDocument:
    """The customer downloading their own ticket's receipt.

    One document for the whole order — every passenger on it, the way GTS
    draws it when nothing narrows the request.
    """
    order = await _owned(session, customer_id, order_id)
    return await _fetch_receipt(session, order)


async def order_receipt_admin(
    session: AsyncSession, order_id: uuid.UUID
) -> ReceiptDocument:
    """The same document for the support desk — any order, no owner to match.

    Staff answer a customer who cannot reach their own copy, so the only
    difference from the customer's route is whose order may be asked for.
    """
    return await _fetch_receipt(session, await _require(session, order_id))


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


def _require_price_confirmed(order: Order) -> None:
    """Money moves only for a price settled with GTS — checked, and accepted
    where there was something to accept.

    ``reprice/`` settles it on its own when GTS says the price stands; only a
    price that moved needs ``reprice/confirm/`` as well.
    """
    if order.price_confirmed_at is None:
        raise Conflict(
            "The price has not been checked with GTS — call reprice/ (and "
            "reprice/confirm/ if it says the price moved) before paying"
        )


def _require_our_currency(order: Order, price: OrderPrice, *, step: str) -> None:
    """A price we are about to believe is in **our** currency, or it is refused.

    Compared against ``core.money.CURRENCY`` rather than against
    ``order.currency``: an order whose price GTS never reported holds ``NULL``,
    and comparing against that skipped the check exactly where there was
    nothing else to catch a foreign figure.

    GTS's documentation draws ``reprice_check`` in UZS and ``reprice_confirm``
    in USD for the same order, and its live server quotes the *provider's*
    fare in the provider's currency. Misprint or not, a figure in another
    currency is not a price for this order — it is a number that must never
    reach a card or the deposit, and there is no conversion here to make one
    out of it. Refused before anything is written, at ERROR: this is GTS or
    the integration misbehaving, and a person looks.

    **Only for a figure that is about to become the order's price.** A quote
    GTS itself calls unchanged is never read for its currency — see
    ``_price_moved`` — because that is the ordinary live answer and refusing
    it would stop every payment.
    """
    if price.currency != CURRENCY:
        logger.error(
            "gts_price_foreign_currency",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
            step=step,
            answered=f"{price.amount} {price.currency}",
        )
        raise UpstreamError(
            f"GTS {step} answered in {price.currency}; this installation prices "
            f"in {CURRENCY} — the price was not changed"
        )


def _price_data(amount: Decimal | None, currency: str | None) -> dict[str, Any]:
    return {"amount": str(amount) if amount is not None else None, "currency": currency}


async def _guard_price_step(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> tuple[Order, PaymentAttempt | None]:
    """Lock the order for a price step: payable, and no charge in flight."""
    order = await _owned_locked(session, customer_id, order_id)
    _require_payable(order)
    open_attempt = await _open_attempt(session, order.id, for_update=True)
    if open_attempt is not None and open_attempt.status == AttemptStatus.CONFIRMING:
        raise Conflict("A payment for this order is being confirmed")
    return order, open_attempt


def _take_price(
    session: AsyncSession,
    order: Order,
    open_attempt: PaymentAttempt | None,
    price: OrderPrice,
    *,
    event: str,
) -> bool:
    """Write the price GTS confirmed. ``True`` when it differs from the one held.

    The answer is kept whole (``price_response``: the breakdown the client
    shows) whatever the figure. A different price invalidates what the
    customer was shown before: an open attempt — a code sent for the old
    amount — is abandoned so it can never confirm a charge of a price nobody
    accepted, and the event records the move.
    """
    order.price_response = dict(price.raw)
    before = (order.amount, order.currency)
    if before == (price.amount, price.currency):
        return False
    session.add(
        lifecycle.event(
            order,
            event=event,
            actor=lifecycle.CUSTOMER,
            data={
                "from": _price_data(*before),
                "to": _price_data(price.amount, price.currency),
            },
        )
    )
    order.amount = price.amount
    order.currency = price.currency
    if open_attempt is not None:
        open_attempt.status = AttemptStatus.ABANDONED
    return True


def _order_money(order: Order) -> Money | None:
    """The price the order holds, or nothing when it never held one."""
    if order.amount is None or order.currency is None:
        return None
    return Money(amount=order.amount, currency=order.currency)


def _price_moved(old: Money | None, price: OrderPrice | None) -> bool:
    """Did the price move? **GTS's verdict when it gave one**, the figures else.

    The one rule both price steps turn on, so ``reprice/`` cannot promise a
    change ``reprice/confirm/`` then refuses to make. GTS's ``price_changed``
    is believed over any comparison of our own: it quotes the provider's fare
    in the provider's currency beside ``price_changed: false`` (294 EUR for
    an order booked at 343.04 USD, live 2026-08-25 — that order's own currency
    is no longer possible here, the foreign quote beside it still is), and
    comparing those two figures would call that a change. No quote at all is
    no change either — GTS quotes a figure only when it has a new one. The
    comparison is left for an installation whose GTS sends no verdict.

    **The currency is deliberately not checked here.** A quote GTS itself
    calls unchanged is never charged and never shown as a price, so refusing
    it would stop every ordinary payment; ``_require_our_currency`` is what
    runs on the figures that do become a price.
    """
    if price is None:
        return False
    if price.changed is not None:
        return price.changed
    if old is None:
        return True
    return (old.amount, old.currency) != (price.amount, price.currency)


def _price_unchanged(
    order: Order, old: Money | None, answer: dict[str, Any] | None = None
) -> RepriceOut:
    """GTS's ``reprice_check`` said the price did not move: the order's own
    figure is today's figure.

    Two answers arrive at the same place. GTS either quotes no price at all —
    it quotes a figure only when it has a new one — or quotes one and says
    ``price_changed: false`` beside it, which live GTS does with the
    *provider's* fare in the provider's currency (294 EUR against an order
    booked at 343.04 USD, 2026-08-25). Neither is a new price for this order,
    so ``new_price`` is ``old_price`` and the quote, when there was one, is
    still handed through as ``price_info`` for a client that wants to show
    the breakdown.

    An order that holds no price of its own is the one case left unanswered:
    neither side names a figure, and that is the ``502`` it always was.
    """
    if old is None:
        logger.warning(
            "gts_reprice_no_price_either_side",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
        )
        raise UpstreamError("the GTS reprice_check answer carried no price")
    logger.info(
        "order_repriced",
        order_id=str(order.id),
        gts_order_number=order.gts_order_number,
        amount=f"{old.amount} {old.currency}",
        changed=False,
    )
    return RepriceOut(changed=False, old_price=old, new_price=old, **(answer or {}))


async def _settle_unmoved_price(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> None:
    """A check that says the price stands is the whole price step: record it.

    There is no second step to take. GTS keeps nothing to confirm for a price
    that did not move and refuses ``reprice_confirm`` outright (``400803``,
    live 2026-08-25), so waiting for that call before ``payment/`` would wait
    for one GTS will not accept — while its own lifecycle asks only that a
    ``reprice_check`` ran before the ticket. That check just ran, at the
    price the customer is looking at.

    Only for an order that could still be paid: the question is free to ask
    about a paid, ticketing or cancelled order (``_require_payable`` is what
    says which), and it writes nothing for those. The amount is untouched —
    nothing moved — so no open attempt is disturbed either.
    """
    order = await _owned_locked(session, customer_id, order_id)
    try:
        _require_payable(order)
    except Conflict:
        return
    if order.price_confirmed_at is not None:  # settled while we asked GTS
        return
    order.price_confirmed_at = utcnow()
    session.add(
        lifecycle.event(
            order,
            event="price.confirmed",
            actor=lifecycle.CUSTOMER,
            data=_price_data(order.amount, order.currency),
        )
    )
    await session.commit()
    logger.info(
        "order_price_confirmed",
        order_id=str(order.id),
        gts_order_number=order.gts_order_number,
        amount=f"{order.amount} {order.currency}",
        step="reprice_check",
    )


async def reprice_order(
    session: AsyncSession, customer_id: uuid.UUID, order_id: uuid.UUID
) -> RepriceOut:
    """Step 0 of paying: what the held order costs today — GTS's answer, as is,
    with the one comparison the client would otherwise make itself.

    GTS's own lifecycle puts ``reprice_check`` and ``reprice_confirm`` between
    booking and ticketing, and its live server refuses to ticket an order that
    skipped the check; the customer's app asks here, reads ``changed`` /
    ``old_price`` / ``new_price``, and — when the price moved and the customer
    accepts — calls ``confirm_price``. ``old_price`` is the order's own figure
    (what the customer has been shown); ``new_price`` is GTS's today. The
    price itself is never moved here: the answer is GTS's own (agent
    commission stripped, as everywhere on the customer surface), and asking
    again costs nothing. Somebody else's order is a ``404``, like every read.

    **A price that did not move is settled by this call alone**
    (``_settle_unmoved_price``): GTS refuses ``reprice_confirm`` when there is
    nothing to confirm, so requiring that step would require a call GTS will
    not take. The order is marked price-confirmed here and ``payment/`` opens
    — the one thing this endpoint writes, and only for an order that could
    still be paid. A price that moved is untouched: it is ``reprice/confirm/``
    that accepts it.

    **GTS's own verdict decides**, not our comparison. Its check sends
    ``price_changed`` and, when that is ``false``, a figure that is the
    provider's fare rather than this order's price — 294 EUR against an
    order booked at 343.04 USD, its own order record still reading 343.04
    (live 2026-08-25). Comparing the two would announce a price change that
    is not one and send the customer to a confirmation GTS refuses. So a
    ``false`` verdict, and an answer with no price at all, both come back as
    ``changed: false`` at the order's own price (``_price_unchanged``); only
    when GTS says the price moved — or says nothing either way and the
    figures really differ — is it a change.
    """
    client = await integrations_service.gts_client(session)
    order = await _owned(session, customer_id, order_id)
    price = await _adapter(order).reprice(client, order.gts_order_number)
    old = _order_money(order)
    if price is not None and _price_moved(old, price):
        # Checked here and not only at ``reprice/confirm/``: this is the call
        # that shows the customer a new price, and a figure in a currency we
        # cannot charge must not reach that screen either.
        _require_our_currency(order, price, step="reprice_check")
        logger.info(
            "order_repriced",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
            amount=f"{price.amount} {price.currency}",
            changed=True,
        )
        return RepriceOut(
            changed=True,
            old_price=old,
            new_price=Money(amount=price.amount, currency=price.currency),
            **strip_commission(price.raw),
        )
    # The price stands. Built first: it raises, before anything is written,
    # when neither side named a figure at all.
    answer = _price_unchanged(
        order, old, strip_commission(price.raw) if price is not None else None
    )
    if order.price_confirmed_at is None:
        await _settle_unmoved_price(session, customer_id, order_id)
    return answer


async def confirm_price(
    session: AsyncSession,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    language: str | None = None,
) -> BookingResultOut:
    """The customer accepted the price ``reprice_order`` showed — tell GTS.

    The one write of the price step. **It asks GTS the check first and only
    sends ``reprice_confirm`` when that check says the price moved.** There
    is nothing else GTS will accept: with an unmoved price it keeps no
    repriced offer, and a confirmation sent anyway is refused with ``400803``
    ("Срок действия предложения после перерасчёта истёк") — which, since
    ``payment/`` waits for this step, left every ordinary order unpayable
    behind a ``502`` (live 2026-08-25). The check is repeatable and changes
    nothing there, so asking it again here costs a question and buys the one
    fact this step turns on; it also puts a check immediately before the
    ticketing GTS refuses without one.

    When the price moved, the confirmation answers with it, and that one is
    GTS's final word on what ticketing debits — so it is the one stored and
    charged: it replaces whatever the order held, and an open attempt for
    another amount is abandoned. It normally equals what the check showed;
    when it does not, the difference is an event and a warning, and the
    answer to this call is what the customer sees before paying.

    An unmoved price — or a confirmation that quotes none — leaves the order
    its own figure and the ``price_response`` it already had, and the
    confirmation still counts: this is the ordinary case, and refusing it
    would leave the customer unable to pay a price nobody disputes.

    GTS has repriced its record by now, so the order is **read back** and
    the answer is the order as it stands — status, deadline, ``order_data``
    — at the confirmed price. The read-back is best effort: the confirmation
    is the act that matters, and a read that fails is a warning, not a
    confirmation undone. A hold GTS has released since is the same
    ``offer_expired`` the payment step would have found — and takes
    precedence over the confirmation, which is then never recorded.
    """
    client = await integrations_service.gts_client(session)
    order = await _owned(session, customer_id, order_id)
    _require_payable(order)
    adapter = _adapter(order)
    checked = await adapter.reprice(client, order.gts_order_number)
    price = (
        await adapter.confirm_price(client, order.gts_order_number)
        if _price_moved(_order_money(order), checked)
        else None
    )
    snapshot: OrderSnapshot | None
    try:
        snapshot = await adapter.retrieve(client, order.gts_order_number)
    except (UpstreamError, UpstreamTimeout) as exc:
        logger.warning(
            "gts_read_after_confirm_failed",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
            error=str(exc),
        )
        snapshot = None

    order, open_attempt = await _guard_price_step(session, customer_id, order_id)
    if price is not None:
        _require_our_currency(order, price, step="reprice_confirm")
    if snapshot is not None and gts_order.is_released(snapshot.gts_status):
        apply_snapshot(order, snapshot)
        session.add_all(_released(order, snapshot))
        await session.commit()
        raise OfferExpired("The booking has expired at GTS — please search again")
    if snapshot is not None and _window_closed(snapshot, now=utcnow()):
        # Confirming a price GTS will not ticket only sets up the refund.
        apply_snapshot(order, snapshot)
        await session.commit()
        raise OfferExpired(DEADLINE_PASSED_MESSAGE)
    if price is not None:
        changed = _take_price(
            session, order, open_attempt, price, event="price.repriced"
        )
        if changed:
            logger.warning(
                "gts_confirmed_other_price",
                order_id=str(order.id),
                gts_order_number=order.gts_order_number,
                amount=f"{price.amount} {price.currency}",
            )
    # Confirmed **before** the read-back is applied, so the read refreshes
    # the record without touching the amount just confirmed.
    order.price_confirmed_at = utcnow()
    if snapshot is not None:
        apply_snapshot(order, snapshot)
    session.add(
        lifecycle.event(
            order,
            event="price.confirmed",
            actor=lifecycle.CUSTOMER,
            data=_price_data(order.amount, order.currency),
        )
    )
    await session.commit()
    logger.info(
        "order_price_confirmed",
        order_id=str(order.id),
        gts_order_number=order.gts_order_number,
        amount=f"{order.amount} {order.currency}",
    )
    return await _present(session, order, language=language)


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


#: What the customer is told when GTS will no longer ticket the hold. Not
#: "cancelled": GTS still has the record, and only GTS cancels it.
DEADLINE_PASSED_MESSAGE: Final = (
    "The ticketing deadline for this booking has passed — please search again"
)


def _window_closed(snapshot: OrderSnapshot, *, now: datetime) -> bool:
    """GTS's own ticketing deadline, freshly read, has already gone.

    ``BO`` on its own is not enough to charge a card on. Live GTS keeps the
    record ``BO`` past ``ticket_time_limit`` — it even still offers
    ``CANCEL_BOOKING`` — and then refuses the ticket outright: "Выписка
    билета запрещена после истечения лимита времени на выписку" (order
    91068, charged four hours after its deadline, 2026-08-25). The money
    moved and no ticket could ever have come out of it.

    Only GTS's figure from a read taken moments ago is trusted, and only
    once ``EXPIRY_GRACE`` has passed on top of it: our clock against GTS's
    is not worth a refused sale. A hold whose deadline GTS does not spell at
    all is left to ``is_released``, as before.

    This refuses the payment; it does **not** cancel the order. GTS still
    holds the record, and cancelling by our clock is the thing this module
    has always declined to do — ``expire_unpaid`` closes it when GTS lets go.
    """
    deadline = snapshot.ticket_time_limit_at
    return deadline is not None and deadline < now - EXPIRY_GRACE


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
    provider = await payments_service.payment_provider(session, method=data.method)
    client = await integrations_service.gts_client(session)
    order = await _owned(session, customer_id, order_id)
    _require_payable(order)
    _require_price_confirmed(order)
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
    if _window_closed(snapshot, now=utcnow()):
        # No code is sent for a hold GTS has stopped ticketing.
        await session.commit()
        raise OfferExpired(DEADLINE_PASSED_MESSAGE)
    _require_payable(order)
    _require_price_confirmed(order)
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

    attempt_id = attempt.id
    saved_card_id: uuid.UUID | None = None
    if data.save and data.card is not None:
        # The provider has taken the card, so it is worth keeping. Best
        # effort, between the locks: a card that could not be saved is a log
        # line, never a payment that was started and then lost. The rollback
        # expires every row this session holds, hence ``attempt_id`` above.
        try:
            saved_card_id = await payments_service.remember_card(
                session, customer_id, card
            )
        except Exception as exc:  # noqa: BLE001 - the payment stands either way
            await session.rollback()
            logger.warning(
                "card_not_remembered",
                order_id=str(order_id),
                error=f"{type(exc).__name__}: {exc}"[:300],
            )

    order = await _owned_locked(session, customer_id, order_id)
    row = await session.get(PaymentAttempt, attempt_id, with_for_update=True)
    if row is not None and row.status == AttemptStatus.STARTED:
        row.provider_reference, row.key_version = encrypt(started.reference)
        row.phone_hint = started.phone_hint
        row.provider_data = {"start": started.raw}
        if saved_card_id is not None:
            row.card_id = saved_card_id
    await session.commit()
    logger.info(
        "payment_started",
        order_id=str(order.id),
        attempt=str(attempt_id),
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


async def _reopen_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, *, error: str
) -> None:
    """Hand a ``confirming`` attempt back to the customer — the code was refused.

    The mirror of ``settle_attempt``: same lock, same re-read, and the same
    single condition — only an attempt still ``confirming`` is touched. So a
    sweep that settled it first (a slow ``confirm`` outlives the sweep's 120
    seconds) keeps the last word, and this writes nothing.

    The order's ``payment_status`` is not touched: it never became anything
    but what it was, because the charge never went out.
    """
    probe = await session.get(PaymentAttempt, attempt_id)
    if probe is None:
        return
    order = await _locked(session, probe.order_id)
    attempt = await session.get(PaymentAttempt, attempt_id, with_for_update=True)
    if order is None or attempt is None or attempt.status != AttemptStatus.CONFIRMING:
        await session.rollback()
        return
    attempt.status = AttemptStatus.STARTED
    attempt.error = error[:500]
    session.add(
        lifecycle.event(
            order,
            event="payment.otp_rejected",
            actor=lifecycle.CUSTOMER,
            data={"attempt": str(attempt.id)},
        )
    )
    await session.commit()
    logger.info("payment_otp_rejected", order_id=str(order.id), attempt=str(attempt.id))


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

    **A refused code is not a refused payment.** Every provider checks the
    code before it moves the money, so ``OtpRejected`` means nothing was
    charged: the attempt is put back to ``started`` with the reason on it
    (``awaiting_otp`` + ``payment.error``, HTTP 200) and the customer types
    the code again or asks ``resend/`` for a new one. Only the charge itself
    can end an attempt — a decline, or the sweep giving up on a lost answer.

    The provider is the one that **started** the attempt (its code is on the
    row), resolved from an unlocked read before the lock is taken — resolving
    commits. The locked re-read then re-checks the id, so a start that
    supersedes this attempt between the two reads turns into the ``409``, not
    into a charge through the wrong adapter.

    Our copy of the deadline is a best-effort reading of a field GTS spells
    three ways, so a deadline that has passed by our clock is a reason to
    **ask GTS**, never to cancel: the order already exists. What GTS answers
    then ends the attempt two ways — the hold released, or the deadline GTS
    itself reports already gone (``_window_closed``), which is the ticket it
    will refuse to issue however alive ``BO`` looks. Neither cancels the
    order; the expiry sweep does that when GTS lets go. That read happens
    before the lock, like every other call that leaves the process; a read
    that fails is the ``502`` / ``504`` it is, and nothing is charged on a
    guess.
    """
    order = await _owned(session, customer_id, order_id)
    probe = await _open_attempt(session, order.id)
    if probe is None or probe.id != data.payment_id:
        raise Conflict("This payment is not open — start the payment again")
    provider = await payments_service.provider_for_attempt(session, code=probe.provider)
    snapshot: OrderSnapshot | None = None
    deadline = order.ticket_time_limit_at
    if deadline is not None and deadline <= utcnow():
        client = await integrations_service.gts_client(session)
        snapshot = await _adapter(order).retrieve(client, order.gts_order_number)

    order = await _owned_locked(session, customer_id, order_id)
    attempt = await _open_attempt(session, order.id, for_update=True)
    if attempt is None or attempt.id != data.payment_id:
        raise Conflict("This payment is not open — start the payment again")
    if attempt.status == AttemptStatus.CONFIRMING:
        return await _present(session, order, language=language)
    if provider is None:
        # The panel switched this method off while the code was being typed.
        # Nothing was charged at ``start``, so the attempt is closed here and
        # the next ``payment/`` opens one with a method the panel still backs
        # — a reference is never handed to an adapter that did not write it.
        attempt.status = AttemptStatus.ABANDONED
        await session.commit()
        logger.warning(
            "payment_method_unavailable",
            order_id=str(order.id),
            attempt=str(attempt.id),
            provider=attempt.provider,
        )
        raise Conflict(
            "The payment method this attempt was started with is no longer "
            "enabled — start the payment again"
        )
    if attempt.provider_reference is None:
        raise Conflict("This payment was not started — start the payment again")
    if snapshot is not None:
        # Past our deadline while the code was being typed: GTS decides.
        apply_snapshot(order, snapshot)
        if gts_order.is_released(snapshot.gts_status):
            attempt.status = AttemptStatus.ABANDONED
            session.add_all(_released(order, snapshot))
            await session.commit()
            raise OfferExpired("The booking has expired at GTS — please search again")
        if _window_closed(snapshot, now=utcnow()):
            attempt.status = AttemptStatus.ABANDONED
            await session.commit()
            raise OfferExpired(DEADLINE_PASSED_MESSAGE)
    reference = decrypt(attempt.provider_reference, attempt.key_version or 0)
    attempt_id = attempt.id
    attempt.status = AttemptStatus.CONFIRMING
    # The reason a previous code was refused belongs to that code, not to
    # this one: it is dropped here so ``processing`` never carries it.
    attempt.error = None
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
    except OtpRejected as exc:
        # The provider would not take the code, which it says before it moves
        # any money: the attempt goes back to waiting for one, with the same
        # card and the same ``payment_id``. Another ``confirm/`` or a
        # ``resend/`` follows — nothing here is over.
        await _reopen_attempt(session, attempt_id, error=str(exc))
        return await _present(
            session, await _owned(session, customer_id, order_id), language=language
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


async def resend_payment_otp(
    session: AsyncSession,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    data: PaymentResendIn,
    *,
    language: str | None = None,
) -> BookingResultOut:
    """Resend: send the same open attempt's code again — no new attempt, no charge.

    Same reference, same provider: the cardholder is asked to look at their
    phone again, never asked to hand over a card twice. Blocked while the
    attempt is ``confirming`` — a code is not resent once its answer might
    already be on the wire. The rate limit (``RateLimit("payment")``, ten a
    minute, shared with ``start`` and ``confirm``) is the only cooldown;
    there is no attempt-level "wait N seconds" column to keep in sync with it.

    The provider is the one that **started** the attempt, resolved unlocked
    before the lock, exactly as ``confirm_payment`` does. An unknown answer
    (``UpstreamError``/``UpstreamTimeout``) leaves the attempt exactly as it
    was — unlike ``start``'s failure path, nothing here is thrown away on a
    guess, because the code already sent at ``start`` may still be good.
    Only a definitive refusal (``PaymentDeclined``) ends the attempt, the
    same way a refusal at ``start`` does.
    """
    order = await _owned(session, customer_id, order_id)
    probe = await _open_attempt(session, order.id)
    if probe is None or probe.id != data.payment_id:
        raise Conflict("This payment is not open — start the payment again")
    provider = await payments_service.provider_for_attempt(session, code=probe.provider)

    order = await _owned_locked(session, customer_id, order_id)
    attempt = await _open_attempt(session, order.id, for_update=True)
    if attempt is None or attempt.id != data.payment_id:
        raise Conflict("This payment is not open — start the payment again")
    if attempt.status == AttemptStatus.CONFIRMING:
        raise Conflict("A payment for this order is being confirmed")
    if provider is None:
        # The panel switched this method off while the code was being typed.
        attempt.status = AttemptStatus.ABANDONED
        await session.commit()
        logger.warning(
            "payment_method_unavailable",
            order_id=str(order.id),
            attempt=str(attempt.id),
            provider=attempt.provider,
        )
        raise Conflict(
            "The payment method this attempt was started with is no longer "
            "enabled — start the payment again"
        )
    if attempt.provider_reference is None:
        raise Conflict("This payment was not started — start the payment again")

    reference = decrypt(attempt.provider_reference, attempt.key_version or 0)
    attempt_id = attempt.id
    # Nothing has been mutated yet — drop the lock before the network call,
    # the same way every write in this module keeps a slow provider from
    # holding a row.
    await session.rollback()

    try:
        started = await provider.resend(reference=reference)
    except PaymentDeclined as exc:
        await _fail_attempt(session, attempt_id, error=str(exc))
        return await _present(
            session, await _owned(session, customer_id, order_id), language=language
        )
    # UpstreamError/UpstreamTimeout propagate on purpose: the outcome is
    # unknown, but the code ``start`` already sent may still be good, so the
    # attempt is left open rather than failed forward.

    order = await _owned_locked(session, customer_id, order_id)
    row = await session.get(PaymentAttempt, attempt_id, with_for_update=True)
    if row is not None and row.status == AttemptStatus.STARTED:
        row.phone_hint = started.phone_hint or row.phone_hint
        # A fresh code answers for itself; the refused one's reason goes.
        row.error = None
        row.provider_data = {**(row.provider_data or {}), "resend": started.raw}
        session.add(
            lifecycle.event(
                order,
                event="payment.otp_resent",
                actor=lifecycle.CUSTOMER,
                data={"attempt": str(row.id)},
            )
        )
    await session.commit()
    logger.info(
        "payment_otp_resent",
        order_id=str(order.id),
        attempt=str(attempt_id),
        provider=provider.code,
    )
    return await _present(session, order, language=language)


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


# --- cancelling ------------------------------------------------------------------


async def cancel_order(
    session: AsyncSession,
    customer_id: uuid.UUID,
    order_id: uuid.UUID,
    *,
    language: str | None = None,
) -> BookingResultOut:
    """The customer lets a hold go — **at GTS first, in our record second**.

    GTS releasing the seat is the act; the row is the record of it. So the
    cancellation goes out before anything here is written, and what is
    written afterwards describes what GTS did. The other order would put a
    cancelled order in the books against a seat GTS keeps holding until the
    deadline.

    Only before a ticket. GTS ends a hold with ``cancel`` and an issued
    ticket with ``void`` or ``refund``, and neither of those is part of this
    flow — so the guard is the payment one: ``_require_payable`` names an
    order whose hold is still ours to release, because an order that can
    still take money is exactly that order. ``lifecycle.transition`` has the
    final word under the lock. A charge being confirmed refuses the
    cancellation twice, before GTS is asked and again after: nothing about an
    order may change while its money is in flight.

    **The answer is read back whatever GTS said**, because neither answer
    settles it alone: the cancellation carries no status, and a refusal may
    only mean the hold is already gone — an order GTS has released refuses a
    second cancellation, and that is a cancelled order, not a failure. One
    ``retrieve`` decides both. A refusal with the hold still alive is the
    ``502`` it is and writes nothing.

    Asking again once the order is cancelled is neither an error nor a second
    call to GTS: the order comes back as it stands.
    """
    order = await _owned(session, customer_id, order_id)
    if order.status == OrderStatus.CANCELLED:
        return await _present(session, order, language=language)
    _require_payable(order)
    open_attempt = await _open_attempt(session, order.id)
    if open_attempt is not None and open_attempt.status == AttemptStatus.CONFIRMING:
        raise Conflict("A payment for this order is being confirmed")

    adapter = _adapter(order)
    client = await integrations_service.gts_client(session)
    refusal: UpstreamError | UpstreamTimeout | None = None
    try:
        await adapter.cancel(client, order.gts_order_number)
    except (UpstreamError, UpstreamTimeout) as exc:
        refusal = exc
    snapshot: OrderSnapshot | None
    try:
        snapshot = await adapter.retrieve(client, order.gts_order_number)
    except (UpstreamError, UpstreamTimeout) as exc:
        logger.warning(
            "gts_read_after_cancel_failed",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
            error=str(exc),
        )
        snapshot = None
    released = snapshot is not None and gts_order.is_released(snapshot.gts_status)
    if refusal is not None and not released:
        raise refusal
    if refusal is None and snapshot is not None and not released:
        # GTS accepted the cancellation and still shows the hold. The POST is
        # the act and the read is the corroboration, so the act stands — but
        # a disagreement this shape is worth a person seeing.
        logger.warning(
            "gts_still_holds_after_cancel",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
            gts_status=snapshot.gts_status,
        )

    order = await _owned_locked(session, customer_id, order_id)
    open_attempt = await _open_attempt(session, order.id, for_update=True)
    try:
        events = lifecycle.transition(
            order,
            actor=lifecycle.CUSTOMER,
            status=OrderStatus.CANCELLED,
            cancel_reason=CancelReason.CUSTOMER,
            open_attempt=open_attempt.status if open_attempt is not None else None,
            data={"gts_status": snapshot.gts_status if snapshot else order.gts_status},
        )
    except Conflict:
        # The seat is gone at GTS and the order moved under us in the moment
        # it took to ask — only a payment can do that here. Left for support:
        # the ticket GTS will now refuse is what makes it visible, and
        # ``/admin/orders/{id}/sync/`` shows the released hold right away.
        logger.error(
            "cancel_raced_payment",
            order_id=str(order.id),
            gts_order_number=order.gts_order_number,
            payment_status=order.payment_status,
        )
        raise
    if open_attempt is not None:
        # A code sent for an order nobody will pay for; the index that allows
        # one open attempt per order should not keep holding it either.
        open_attempt.status = AttemptStatus.ABANDONED
    if snapshot is not None:
        apply_snapshot(order, snapshot)
    session.add_all(events)
    await session.commit()
    logger.info(
        "order_cancelled",
        order_id=str(order.id),
        gts_order_number=order.gts_order_number,
        gts_status=order.gts_status,
    )
    return await _present(session, order, language=language)


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

    An attempt is asked about **only** by the provider that started it,
    resolved from the code on its row. One whose provider is gone — switched
    off, adapter not in this release — is left alone and reported, because
    that provider may have charged; a human settles it. A resolution failure
    ends that attempt's question, not the sweep: the ticketing and expiry
    passes behind it must still run.
    """
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
    if not rows:
        return 0
    # Resolved per attempt (its own provider, never "the active one") and
    # before any settle takes a lock — resolving commits.
    providers: dict[str, PaymentProvider | None] = {}
    for code in {probe.provider for probe in rows}:
        providers[code] = await payments_service.provider_for_attempt(
            session, code=code
        )
    settled = 0
    for probe in rows:
        request_id_var.set(new_request_id())
        provider = providers[probe.provider]
        if probe.provider_reference is None:
            outcome = PaymentOutcome("failed", error="no provider reference")
        elif provider is None:
            logger.error(
                "payment_provider_unavailable",
                order_id=str(probe.order_id),
                attempt=str(probe.id),
                provider=probe.provider,
            )
            continue
        else:
            try:
                outcome = await provider.status(
                    reference=decrypt(probe.provider_reference, probe.key_version or 0)
                )
            except (UpstreamError, UpstreamTimeout) as exc:
                # Not settled without evidence — but past the give-up window
                # a human must know the provider is not answering about it.
                overdue = probe.created_at < now - PAYMENT_CONFIRM_MAX_WAIT
                logger.log(
                    logging.ERROR if overdue else logging.WARNING,
                    "payment_status_unread",
                    order_id=str(probe.order_id),
                    attempt=str(probe.id),
                    overdue=overdue,
                    error=str(exc),
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


# --- support (``/admin/orders/``) --------------------------------------------------


async def _admin_view(session: AsyncSession, order: Order) -> OrderAdminOut:
    messages = await message_catalogue(session)
    attempts = (
        await session.scalars(
            select(PaymentAttempt)
            .where(PaymentAttempt.order_id == order.id)
            .order_by(PaymentAttempt.created_at)
        )
    ).all()
    events = (
        await session.scalars(
            select(OrderEvent)
            .where(OrderEvent.order_id == order.id)
            .order_by(OrderEvent.created_at, OrderEvent.seq)
        )
    ).all()
    latest = attempts[-1] if attempts else None
    customer_view = BookingResultOut.from_order(
        order,
        language=None,
        messages=messages,
        attempt=_view(latest),
        receipt_url=_receipt_url(order, admin=True),
    )
    return OrderAdminOut(
        **customer_view.model_dump(exclude={"order"}),
        order=OrderAdminOrderOut.from_public(customer_view.order, order),
        customer_id=order.customer_id,
        ticketing_attempts=order.ticketing_attempts,
        ticketing_requested_at=order.ticketing_requested_at,
        gts_checked_at=order.gts_checked_at,
        events=[OrderEventOut.model_validate(event) for event in events],
        payments=[
            PaymentAttemptAdminOut(
                id=attempt.id,
                created_at=attempt.created_at,
                updated_at=attempt.updated_at,
                provider=attempt.provider,
                status=AttemptStatus(attempt.status),
                amount=Money(amount=attempt.amount, currency=attempt.currency),
                card_last4=attempt.card_last4,
                phone_hint=attempt.phone_hint,
                error=attempt.error,
                paid_at=attempt.paid_at,
            )
            for attempt in attempts
        ],
    )


async def _require(session: AsyncSession, order_id: uuid.UUID) -> Order:
    order = await session.scalar(live(Order).where(Order.id == order_id))
    if order is None:
        raise NotFound("Order not found")
    return order


async def _require_locked(session: AsyncSession, order_id: uuid.UUID) -> Order:
    order = await _locked(session, order_id)
    if order is None:
        raise NotFound("Order not found")
    return order


async def list_orders_admin(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    status: Stage | None = None,
    booking_status: OrderStatus | None = None,
    payment_status: PaymentStatus | None = None,
    ticketing_status: TicketingStatus | None = None,
) -> Page[OrderAdminListItemOut]:
    """Every order, newest first, filterable by the customer's word and by
    the three columns behind it.

    ``status`` is the filter support works from: a row is listed under the
    word its customer sees (``lifecycle.stage_filter``), so
    ``status=ticketing_failed`` is the inbox — every order whose screen says
    "contact support", and none whose money has already gone back. The
    column filters are for the narrower questions ("which refunds are
    still under way?").
    """
    stmt = live(Order)
    if status is not None:
        stmt = stmt.where(lifecycle.stage_filter(status))
    if booking_status is not None:
        stmt = stmt.where(Order.status == booking_status)
    if payment_status is not None:
        stmt = stmt.where(Order.payment_status == payment_status)
    if ticketing_status is not None:
        stmt = stmt.where(Order.ticketing_status == ticketing_status)
    stmt = apply_search(stmt, query, Order.pnr, cast(Order.gts_order_number, String))
    stmt = apply_created_range(stmt, query, Order.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_ORDER_ORDERING,
        default="-created_at",
        tiebreak=Order.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page(
        [OrderAdminListItemOut.from_order(row) for row in rows], pagination, total
    )


async def get_order_admin(session: AsyncSession, order_id: uuid.UUID) -> OrderAdminOut:
    return await _admin_view(session, await _require(session, order_id))


async def mark_refund(
    session: AsyncSession, order_id: uuid.UUID, data: RefundIn, *, staff: Staff
) -> OrderAdminOut:
    """Where the refund stands, as support says — the money moves in the
    provider's own cabinet, and this is the record that it did."""
    order = await _require_locked(session, order_id)
    before = order.payment_status
    session.add_all(
        lifecycle.transition(
            order,
            actor=lifecycle.staff(staff.id),
            payment=PaymentStatus(data.status),
            note=data.note,
        )
    )
    await session.commit()
    audit_context.describe(
        resource_id=order.id,
        changes={"payment_status": [before, order.payment_status], "note": data.note},
    )
    logger.info(
        "refund_marked",
        order_id=str(order.id),
        payment_status=order.payment_status,
        staff_id=str(staff.id),
    )
    return await _admin_view(session, order)


async def sync_order(
    session: AsyncSession, order_id: uuid.UUID, *, staff: Staff
) -> OrderAdminOut:
    """Compare the order with GTS (and the provider) right now, and settle.

    The sweep's questions, asked for one order on a human's request: a
    charge whose answer was lost, a ticket GTS finished or never started, a
    hold GTS released. What it finds is applied through the same steps the
    sweep uses, so "sync" can never do something the sweep could not.
    """
    actor = lifecycle.staff(staff.id)
    order = await _require(session, order_id)

    attempt = await _open_attempt(session, order.id)
    if attempt is not None and attempt.status == AttemptStatus.CONFIRMING:
        provider = await payments_service.provider_for_attempt(
            session, code=attempt.provider
        )
        if provider is None:
            # Only the provider that started a charge may say what became of
            # it, and that provider is gone — it may have charged, so the
            # attempt is left for a human, not settled blind.
            logger.error(
                "payment_provider_unavailable",
                order_id=str(order.id),
                attempt=str(attempt.id),
                provider=attempt.provider,
            )
        elif attempt.provider_reference is not None:
            outcome = await provider.status(
                reference=decrypt(attempt.provider_reference, attempt.key_version or 0)
            )
            await settle_attempt(session, attempt.id, outcome, actor=actor)

    client = await integrations_service.gts_client(session)
    snapshot = await _adapter(order).retrieve(client, order.gts_order_number)
    order = await _require_locked(session, order_id)
    if order.ticketing_status == TicketingStatus.PROCESSING:
        await session.rollback()
        decision = await _apply_ticketing(
            session, order_id, snapshot, error=None, actor=actor
        )
        if decision == "resend":
            await ticket(session, order_id, actor=actor)
        order = await _require(session, order_id)
        audit_context.describe(resource_id=order.id)
        return await _admin_view(session, order)

    before = (order.status, order.payment_status, order.ticketing_status)
    apply_snapshot(order, snapshot)
    if (
        order.ticketing_status == TicketingStatus.FAILED
        and gts_order.is_ticketed(snapshot.gts_status)
        and order.payment_status == PaymentStatus.PAID
        and order.status == OrderStatus.BOOKED
    ):
        # GTS issued late. Allowed for a paid, live order; anything else is
        # left as it is and the mismatch logged for a human.
        order.ticketing_error = None
        session.add_all(
            lifecycle.transition(
                order,
                actor=actor,
                ticketing=TicketingStatus.TICKETED,
                data={"gts_status": snapshot.gts_status},
            )
        )
    elif order.ticketing_status != TicketingStatus.TICKETED and gts_order.is_ticketed(
        snapshot.gts_status
    ):
        logger.error(
            "ticketed_after_refund",
            order_id=str(order.id),
            payment_status=order.payment_status,
            status=order.status,
        )
    elif (
        order.status == OrderStatus.BOOKED
        and order.payment_status in (PaymentStatus.PENDING, PaymentStatus.FAILED)
        and gts_order.is_released(snapshot.gts_status)
    ):
        open_attempt = await _open_attempt(session, order.id, for_update=True)
        if open_attempt is not None and open_attempt.status == AttemptStatus.STARTED:
            open_attempt.status = AttemptStatus.ABANDONED
        if open_attempt is None or open_attempt.status != AttemptStatus.CONFIRMING:
            session.add_all(_released(order, snapshot))
    await session.commit()
    after = (order.status, order.payment_status, order.ticketing_status)
    audit_context.describe(
        resource_id=order.id,
        changes={"before": list(before), "after": list(after)}
        if before != after
        else None,
    )
    return await _admin_view(session, order)


async def retry_ticketing(
    session: AsyncSession, order_id: uuid.UUID, *, staff: Staff
) -> OrderAdminOut:
    """Ask GTS for the ticket again — after the deposit was topped up, say.

    Synced first, so a ticket GTS issued in the meantime is recorded rather
    than requested twice; refused while the order is not paid, not live, or
    GTS shows the hold gone. Staff are not bound by the sweep's send cap —
    that cap exists to stop a machine, not a person who has looked.
    """
    await sync_order(session, order_id, staff=staff)
    order = await _require(session, order_id)
    if order.ticketing_status == TicketingStatus.TICKETED:
        return await _admin_view(session, order)
    if order.status != OrderStatus.BOOKED or order.payment_status != PaymentStatus.PAID:
        raise Conflict("Only a paid, live order can be ticketed")
    if order.ticketing_status == TicketingStatus.PROCESSING:
        raise Conflict("A ticketing request is already in flight — sync again later")
    if gts_order.is_released(order.gts_status):
        raise Conflict(f"GTS has released this order (status {order.gts_status})")
    await ticket(session, order_id, actor=lifecycle.staff(staff.id))
    order = await _require(session, order_id)
    audit_context.describe(resource_id=order.id)
    return await _admin_view(session, order)


__all__ = [
    "ATTEMPT_STARTED_MAX_AGE",
    "CONFIRMING_STALE_AFTER",
    "DEADLINE_PASSED_MESSAGE",
    "EXPIRY_GRACE",
    "EXPIRY_WITHOUT_DEADLINE",
    "PAYMENT_CONFIRM_MAX_WAIT",
    "SWEEP_BATCH",
    "TICKETING_MAX_SENDS",
    "TICKETING_MAX_WAIT",
    "TICKETING_POST_GRACE",
    "apply_snapshot",
    "cancel_order",
    "confirm_payment",
    "confirm_price",
    "create_order",
    "expire_unpaid",
    "get_message",
    "get_order",
    "get_order_admin",
    "list_messages",
    "list_orders",
    "list_orders_admin",
    "mark_refund",
    "message_catalogue",
    "order_receipt",
    "order_receipt_admin",
    "recheck_processing",
    "reprice_order",
    "resend_payment_otp",
    "retry_ticketing",
    "settle_attempt",
    "settle_stale_confirmations",
    "start_payment",
    "sync_order",
    "ticket",
    "ticket_paid_pending",
    "update_message",
]
