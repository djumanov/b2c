"""Ticketing, and the sweep that makes sure it happens.

Two tasks, and the second is the reason the first can be trusted. ``ticket``
does the work; ``run_due`` sweeps ``orders.next_attempt_at`` and sends whatever
is due. The state change and the schedule are written in **one transaction**
(``orders.service.transition``), so a step cannot be lost between committing
and queueing — which is the guarantee a transactional outbox usually buys, at
the cost of one column and up to one poll interval of latency (``O13``).

**The sweep is the only dispatcher.** Sending the task directly after the
commit was in the design and was dropped: it would put the broker on the
critical path of a payment callback, so a broker outage would mean answering
the provider with an error *after* the money had moved. What it bought was up
to thirty seconds of latency the customer never sees — the screen already says
the ticket is being issued. ``ticket`` re-reads under a lock and leaves quietly
if the order moved on, so a duplicate send costs nothing either way.

**Everything here is idempotent by re-reading, not by remembering.** A worker
that dies mid-ticketing leaves an order in ``ticketing`` with a schedule, and
the next pass picks it up exactly as if nothing had happened.
"""

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.modules.orders import service as orders_service
from app.modules.orders.models import Order
from app.modules.orders.states import OrderStatus
from app.modules.products import service as products_service
from app.modules.settings import service as settings_service
from app.providers.products.base import registry
from app.providers.products.orders import (
    FailureClass,
    OrderOperations,
    UnreadableAnswer,
    order_operations,
)
from app.tasks.celery_app import celery_app

logger = get_logger(__name__)

#: How long to wait before each retry. The last value repeats until the
#: deadline; the limit on retrying is **time**, not a count, because the hold
#: is a real deadline and a counter would either stop early or run past it.
_BACKOFF: Final[tuple[int, ...]] = (30, 120, 300, 900, 1800)

#: Spread of the jitter either side of the backoff. Without it every order that
#: failed together retries together, which is how a busy provider stays busy.
_JITTER: Final = 0.2

#: How many due orders one sweep takes. Small enough that a pass is quick and a
#: crash loses little; ``SKIP LOCKED`` means several workers can sweep at once.
_BATCH: Final = 50

#: The statuses a due order can be in and be understood. Anything else is a
#: schedule left behind by a step this release does not have yet.
_DUE_STATUSES: Final = frozenset({OrderStatus.PAID, OrderStatus.TICKETING})


def _next_attempt(attempts: int) -> datetime:
    """When to try again, with jitter so retries do not arrive in a convoy."""
    seconds = _BACKOFF[min(attempts, len(_BACKOFF) - 1)]
    spread = seconds * _JITTER
    return datetime.now(UTC) + timedelta(
        seconds=random.uniform(seconds - spread, seconds + spread)
    )


def _operations(product: str) -> OrderOperations | None:
    adapter = registry.get(product)
    return None if adapter is None else order_operations(adapter)


async def _ticket(order_id: uuid.UUID) -> None:
    """One ticketing attempt, from ``paid`` or from a retry.

    The order is re-read under a lock first and the task leaves quietly if it
    is not in a state that wants ticketing: a duplicate send, a sweep racing a
    direct dispatch, or a cancellation that landed first all end here, and none
    of them is an error.
    """
    async with get_sessionmaker()() as session:
        order = await orders_service.claim_order(session, order_id)
        if order is None or OrderStatus(order.status) not in _DUE_STATUSES:
            # Nothing written, so nothing to undo: leaving closes the session
            # and releases the row lock with it.
            logger.info(
                "ticketing_skipped",
                order_id=str(order_id),
                status=None if order is None else order.status,
            )
            return

        operations = _operations(order.product)
        if operations is None:
            # A vertical with no order half cannot be ticketed, and pretending
            # otherwise would refund a customer for our own gap.
            logger.error(
                "ticketing_unsupported", order_id=str(order_id), product=order.product
            )
            return

        number = order.provider_order_number
        if number is None:
            logger.error("ticketing_without_a_number", order_id=str(order_id))
            return

        settings = await settings_service.get_order_settings(session)
        if OrderStatus(order.status) is OrderStatus.PAID:
            order = await orders_service.begin_ticketing(session, order)

        deadline = orders_service.ticket_deadline(order, settings)
        if datetime.now(UTC) >= deadline:
            await orders_service.send_to_refund(
                session,
                order,
                reason="The ticketing deadline passed before a ticket was issued",
            )
            return

        client = await products_service.gts_client(session)

        try:
            quoted = await operations.reprice(client, number)
        except (AppError, UnreadableAnswer) as failure:
            # Repricing is a *read*. Not being able to do it is a reason to try
            # again, never a reason to refund: the fare may be perfectly fine.
            await _reschedule(session, order, operations, failure, deadline)
            return

        if order.amount_total is not None and quoted.total.amount > (
            order.amount_total + settings.reprice_tolerance
        ):
            await orders_service.send_to_refund(
                session,
                order,
                reason="The fare rose above the agreed tolerance before ticketing",
                failure=(
                    f"paid {order.amount_total} {order.currency}, "
                    f"now {quoted.total.amount} {quoted.total.currency}"
                ),
            )
            return

        try:
            issued = await operations.ticket(client, number)
        except (AppError, UnreadableAnswer) as failure:
            await _reschedule(session, order, operations, failure, deadline)
            return

        ticketed = await orders_service.finish_ticketing(session, order, issued)
        logger.info(
            "order_ticketed",
            order_id=str(ticketed.id),
            order_no=ticketed.order_no,
            status=ticketed.status,
        )


async def _reschedule(
    session: AsyncSession,
    order: Order,
    operations: OrderOperations,
    failure: Exception,
    deadline: datetime,
) -> None:
    """Retry, raise the alarm, or give up — the whole of ``O5`` in one place."""
    kind = (
        FailureClass.TERMINAL
        if not isinstance(failure, AppError)
        else operations.classify(failure)
    )
    if kind is FailureClass.DEPOSIT:
        # Not the customer's problem and not a reason to refund them: our own
        # balance at the provider is empty, and a top-up fixes every order
        # waiting on it at once.
        logger.error(
            "gts_deposit_low",
            order_id=str(order.id),
            order_no=order.order_no,
            detail=str(failure),
        )

    when = _next_attempt(order.attempts)
    if kind is FailureClass.TERMINAL or when >= deadline:
        await orders_service.send_to_refund(
            session,
            order,
            reason=(
                "The provider refused to issue the ticket"
                if kind is FailureClass.TERMINAL
                else "No time left to retry before the ticketing deadline"
            ),
            failure=str(failure),
        )
        return

    retried = await orders_service.retry_ticketing(
        session, order, reason=str(failure), when=when
    )
    logger.warning(
        "ticketing_retry",
        order_id=str(order.id),
        order_no=order.order_no,
        attempt=retried.attempts,
        failure_class=kind.value,
        next_attempt_at=when.isoformat(),
    )


async def _run_due() -> None:
    """Send whatever is due. The sweep that makes a lost task recoverable.

    ``FOR UPDATE SKIP LOCKED`` so several workers may sweep at once without
    two of them taking the same order, and the lock is released by the commit
    below rather than held across the work — the work re-reads and re-locks.
    """
    async with get_sessionmaker()() as session:
        due = (
            await session.scalars(
                select(Order)
                .where(
                    Order.next_attempt_at.is_not(None),
                    Order.next_attempt_at <= datetime.now(UTC),
                )
                .order_by(Order.next_attempt_at)
                .limit(_BATCH)
                .with_for_update(skip_locked=True)
            )
        ).all()

        sent = 0
        for order in due:
            if OrderStatus(order.status) in _DUE_STATUSES:
                ticket.delay(str(order.id))
                sent += 1
                continue
            # A schedule nothing in this release can act on. Clearing it stops
            # the sweep spinning on it every pass; the state itself is what a
            # person will see, and ``send_to_refund`` already logged why.
            logger.error(
                "order_due_unhandled",
                order_id=str(order.id),
                order_no=order.order_no,
                status=order.status,
            )
            order.next_attempt_at = None
        await session.commit()

    if sent:
        logger.info("orders_dispatched", count=sent)


@celery_app.task(name="app.tasks.orders.ticket")
def ticket(order_id: str) -> None:
    """Issue the tickets for one order. Safe to send twice."""
    asyncio.run(_ticket(uuid.UUID(order_id)))


@celery_app.task(name="app.tasks.orders.run_due")
def run_due() -> None:
    """The sweep. Every schedule that came due since the last pass."""
    asyncio.run(_run_due())


__all__ = ["run_due", "ticket"]
