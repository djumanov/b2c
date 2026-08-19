"""``/public/orders/`` — the customer's own order history (API.md §21).

Both rows of §21 that are built here are marked ``✓``, so the token check sits
on the **router** rather than on each handler — the arrangement
``payments/router_cards.py`` uses, for the same reason: there is no endpoint
here that could correctly be left off it.

Booking still writes these rows from the product flow (§20), but everything
done **to** an order afterwards lives here: paying for one, and releasing one.
``refund/``, ``history/`` and ``receipt/`` arrive with the later slices.

``?status=`` takes **our** status — ``booked``, ``ticketed``, ``cancelled``,
… — because the vocabulary is ours (order-system/03-design.md §3.3). GTS's own
code still travels beside it in ``gts_status``.

The list and the detail answer with **different** shapes, which is the one
thing worth knowing before reading either: ``OrderListOut`` is a card,
``OrderOut`` is the record (API.md §21).
"""

import uuid
from typing import Annotated

from fastapi import Depends, Query

from app.api.deps import CurrentCustomer, PaginationDep, RateLimit, current_customer
from app.api.envelope import Page, enveloped_router
from app.api.errors import UpstreamError
from app.api.idempotency import IdempotencyKey
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.schemas import (
    OrderListOut,
    OrderOut,
    TransactionOut,
    TransactionStartIn,
)

# The card body belongs to ``payments``: it is the only module allowed to hold a
# number in the clear, and the validation a saved card is held to is the same
# validation a card being spent is held to (ARCHITECTURE.md §5).
from app.modules.payments.schemas import CardPaymentIn, OtpConfirmIn

router = enveloped_router(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(current_customer)],
)

#: ``/public/transactions/{id}/`` is a sibling of ``/public/orders/``, not a
#: child: API.md §22 names it that way and a client polling a redirect has the
#: transaction id and nothing else in hand.
transactions_router = enveloped_router(
    prefix="/transactions",
    tags=["payments"],
    dependencies=[Depends(current_customer)],
)

ProductParam = Annotated[
    str | None,
    Query(description="Vertical code, e.g. `flight`."),
]
StatusParam = Annotated[
    str | None,
    Query(
        description=(
            "Canonical order status — `created`, `booked`, `paid`, `ticketing`, "
            "`ticketed`, `refunding`, `refunded`, `partially_refunded`, "
            "`cancelled`, `voided`, `failed`, `needs_attention`. GTS's own code "
            "is published beside it as `gts_status`, on the detail endpoint."
        )
    ),
]


@router.get("/", summary="My orders")
async def list_orders(
    customer: CurrentCustomer,
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    product: ProductParam = None,
    status: StatusParam = None,
) -> Page[OrderListOut]:
    """A card, not the record: route, dates, travellers, price and status.

    ``data`` is on ``{id}/`` — a page of twenty provider answers is hundreds of
    kilobytes. So is every traveller field §13 calls personal data: the card
    names who is flying, not what is in their passport (API.md §21).
    """
    return await service.list_orders(
        session,
        pagination,
        query,
        customer_id=customer.id,
        product=product,
        status=status,
    )


@router.get("/{id}/", summary="One order")
async def get_order(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> OrderOut:
    # Someone else's order answers 404, the same as one that does not exist
    # (API.md §18).
    return await service.get_order(session, customer_id=customer.id, order_id=id)


@router.post(
    "/{id}/cancel/",
    summary="Release a booking that has not been paid for",
    dependencies=[Depends(RateLimit("payment"))],
)
async def cancel_order(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    idempotency: IdempotencyKey,
) -> OrderOut:
    """Give the seat back.

    An operation on the **order**, which is what it always was: it needs the
    order's state to decide whether it is allowed, and it used to live on the
    product flow only because there was no local order to name (``O3``).

    Refused before the provider is called if the state does not allow it — a
    ticketed order answers ``409`` without a seat being released, which is the
    one ordering of those two steps that does not cost money.
    """
    if idempotency.replayed is not None:
        return OrderOut.model_validate(idempotency.replayed)
    try:
        answer = await service.cancel_order(
            session, customer_id=customer.id, order_id=id
        )
    except UpstreamError:
        await idempotency.release()
        raise
    await idempotency.store(answer.model_dump(mode="json"))
    return answer


@router.post(
    "/{id}/transactions/",
    summary="Start paying for an order",
    status_code=201,
    dependencies=[Depends(RateLimit("payment"))],
)
async def start_transaction(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    payload: TransactionStartIn | None = None,
) -> TransactionOut:
    """Open one attempt at paying for this order, or return the one already open.

    The body is empty: the provider is the installation's choice and there is
    nowhere to come back from (API.md §22). It is still declared, and still
    ``extra: forbid``, so a client that has not caught up and sends
    ``{"method": "payme"}`` is told the contract moved rather than quietly
    ignored.

    **No ``Idempotency-Key``**, and that is not an omission. The key would be
    derived from the request, and with an empty body every call from one
    customer for one order would hash to the same one — a first attempt that
    failed would then replay for twenty-four hours and the customer could never
    pay. The guard is in the database instead: one open attempt per order
    (``uq_order_payments_open``), so a double tap lands on the attempt it
    already made.
    """
    return await service.start_transaction(
        session, customer_id=customer.id, order_id=id
    )


@transactions_router.post(
    "/{id}/card/",
    summary="Send the card for a payment",
    dependencies=[Depends(RateLimit("payment"))],
)
async def submit_card(
    id: uuid.UUID,
    payload: CardPaymentIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> TransactionOut:
    """Give the provider the card; it texts the customer a code.

    Either a typed number and expiry, or the id of a card this customer already
    saved — the server opens the stored ciphertext itself and the client never
    handles the number (API.md §19, §22).

    **This is the one path that carries a card number, and it deliberately has
    no ``Idempotency-Key``.** The key is a hash of the request body, and it is
    stored as a Redis key; a PAN-derived digest at rest would contradict the
    rule that the number reaches the adapter and stops there (PROJECT.md §13).
    What stands in for it is the attempt's own state, held under
    ``SELECT … FOR UPDATE``.
    """
    return await service.submit_card(
        session, customer_id=customer.id, attempt_id=id, data=payload
    )


@transactions_router.post(
    "/{id}/confirm/",
    summary="Confirm a payment with the code",
    dependencies=[Depends(RateLimit("payment"))],
)
async def confirm_payment(
    id: uuid.UUID,
    payload: OtpConfirmIn,
    customer: CurrentCustomer,
    session: SessionDep,
) -> TransactionOut:
    """Verify the code and take the money.

    The whole payment happens inside this request (``O16``): when it answers,
    the order is already ``paid`` and ticketing is queued. A repeat on an
    attempt that is already paid returns the same answer instead of charging
    again — replay by state, which is what an endpoint carrying a one-time
    password uses in place of an idempotency key.
    """
    return await service.confirm_payment(
        session, customer_id=customer.id, attempt_id=id, data=payload
    )


@transactions_router.post(
    "/{id}/resend-otp/",
    summary="Send the code again",
    dependencies=[Depends(RateLimit("payment"))],
)
async def resend_otp(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> TransactionOut:
    """Ask the provider to text the code again.

    Rate-limited twice over: the payment bucket, and a cooldown on the attempt
    itself. The second one is on the row rather than in Redis because it belongs
    to this payment and has to outlive a flushed cache — otherwise a customer
    could spend the installation's merchant account on messages.
    """
    return await service.resend_code(session, customer_id=customer.id, attempt_id=id)


@transactions_router.get("/{id}/", summary="One payment attempt")
async def get_transaction(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> TransactionOut:
    """Someone else's attempt answers 404, the same as one that does not exist."""
    attempt = await service.owned_attempt(
        session, customer_id=customer.id, attempt_id=id
    )
    return TransactionOut.from_attempt(
        attempt, otp_max_attempts=service.CARD_OTP_MAX_ATTEMPTS
    )


__all__ = ["router", "transactions_router"]
