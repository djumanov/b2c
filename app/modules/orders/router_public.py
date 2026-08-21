"""``/public/orders/`` — a customer reads their own bookings back, and pays.

Booking itself lives on the product flow (``POST /public/{product}/booking/``,
``products/router_public.py``): creating an order is the last step of choosing
a flight; reading orders back is this resource. Every row here requires the
owner's token, so the check sits on the router, not on each handler — the
saved-cards arrangement.

``GET`` here never writes. The "is my ticket ready yet?" screen polls this
endpoint, and the sweep — not the read — is what asks GTS; two polls racing a
sweep must not be able to move an order twice.

Paying is two ``POST``s on the order — ``payment/`` sends the code,
``payment/confirm/`` charges with it — both under the tight ``payment`` rate
limit and both idempotent: a repeat answers with the order as it stands now,
and never reaches the provider twice. The row lock and the attempt table are
what make the charge single; the idempotency key only spares the provider a
call. Every failure releases the key — nothing is charged at ``start``, and
``confirm`` answers a lost provider call with the order rather than an error,
so there is no outcome a kept claim would guard.

The ``description`` on each route is written for the developer reading
Swagger: what to send, what comes back, what to do next.
"""

import uuid

from fastapi import Depends

from app.api.deps import (
    CurrentCustomer,
    LanguageDep,
    PaginationDep,
    RateLimit,
    current_customer,
)
from app.api.envelope import Page, enveloped_router
from app.api.errors import AppError, ErrorCode
from app.api.idempotency import IdempotencyKey
from app.api.listing import ListQuery, list_query_dep
from app.api.openapi import error_responses
from app.db.session import SessionDep
from app.modules.orders import service
from app.modules.orders.schemas import (
    BookingResultOut,
    OrderListItemOut,
    PaymentConfirmIn,
    PaymentStartIn,
)

router = enveloped_router(
    prefix="/orders",
    tags=["orders"],
    dependencies=[Depends(current_customer)],
)

OrdersListQuery = Depends(
    list_query_dep(
        ordering=("created_at", "updated_at"),
        default="-created_at",
        search="the PNR and the route summary (`TAS-IST`)",
    )
)


@router.get(
    "/",
    summary="My orders",
    description=(
        "The signed-in customer's bookings, newest first — one row per order "
        "with what a card needs: `status` (the same word the detail shows), "
        "the journey (`routes`, GTS's segments verbatim), who is flying "
        "(`passengers`, names only), the amount and the payment deadline. "
        "Everything else — documents, fares, baggage, ticket numbers — is on "
        "`GET /public/orders/{id}/`."
    ),
    response_description="A page of order cards.",
)
async def list_orders(
    customer: CurrentCustomer,
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQuery = OrdersListQuery,
) -> Page[OrderListItemOut]:
    return await service.list_orders(session, customer.id, pagination, query)


@router.get(
    "/{id}/",
    summary="One order, with its GTS data",
    description=(
        "The order as it stands: `order.status` and `order.message` for the "
        "screen, `payment` for the payment step, `ticketing` for the ticket, "
        "`order_data` for display detail. **Never writes** — poll it freely "
        "while `order.status` is `ticket_waiting` or `payment.status` is "
        "`processing`; the background sweep settles those and this read "
        "reflects it. Another customer's order is a `404`, not a `403`."
    ),
    response_description="The order, in the same shape booking answered.",
)
async def get_order(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    language: LanguageDep,
) -> BookingResultOut:
    return await service.get_order(
        session, customer.id, id, language=language.requested
    )


@router.post(
    "/{id}/payment/",
    summary="Pay — step 1: choose the card, receive the code",
    description=(
        "Starts a payment for a `booked`, unpaid order. Send **either** "
        "`card_id` (a saved card) **or** `card` (number + `MMYY`), optionally "
        "`save: true` to keep a typed card once the provider accepts it.\n\n"
        "What happens: the hold is re-checked with GTS (its price wins), the "
        "card is registered with the payment provider and the cardholder is "
        "texted a one-time code. **Nothing is charged yet.** The answer is the "
        "order with `payment.status = awaiting_otp`, `payment.payment_id` (send "
        "it back in step 2) and `payment.phone_hint` (the masked phone the code "
        "went to). `order.status` stays `booked`.\n\n"
        "A card the provider refuses is **not an error**: `200` with "
        "`payment.status = failed` and the reason in `payment.error` — let the "
        "customer try another card. Calling again while a code is still valid "
        "abandons the earlier attempt and sends a new code.\n\n"
        "Idempotent: the same body within 24 hours replays the stored answer "
        "and sends no second SMS — send a fresh `Idempotency-Key` for a "
        "deliberate new attempt."
    ),
    response_description=(
        "The order with `payment.status` `awaiting_otp` (code sent) or "
        "`failed` (card refused, see `payment.error`)."
    ),
    responses=error_responses(
        ErrorCode.CONFLICT,
        ErrorCode.OFFER_EXPIRED,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        conflict=(
            "The order is not payable (cancelled, already paid, being "
            "refunded or ticketed), a charge for it is being confirmed right "
            "now, or an identical request is still in flight."
        ),
        upstream_error=(
            "GTS could not be read, GTS reported no price, the provider "
            "refused the receipt, or no payment provider is configured. "
            "Nothing was charged; the provider's words are in `meta.upstream`."
        ),
        upstream_timeout="GTS or the provider did not answer. Nothing was charged.",
    ),
    dependencies=[Depends(RateLimit("payment"))],
)
async def start_payment(
    id: uuid.UUID,
    payload: PaymentStartIn,
    customer: CurrentCustomer,
    session: SessionDep,
    idempotency: IdempotencyKey,
    language: LanguageDep,
) -> BookingResultOut:
    if idempotency.is_replay:
        return await service.get_order(
            session, customer.id, id, language=language.requested
        )
    try:
        result = await service.start_payment(
            session, customer.id, id, payload, language=language.requested
        )
    except AppError:
        await idempotency.release()
        raise
    await idempotency.store({"order_id": str(id)})
    return result


@router.post(
    "/{id}/payment/confirm/",
    summary="Pay — step 2: confirm with the code",
    description=(
        "Charges the card with the code the cardholder received. Send the "
        "`payment_id` from step 1 and the `otp`.\n\n"
        "Read the answer's `payment.status`:\n\n"
        "* `paid` — the money is taken and the ticket was requested from GTS "
        "in the same call; `order.status` is now `ticketed` (see "
        "`ticketing.tickets`), `ticket_waiting` (GTS still issuing — poll "
        "`GET /public/orders/{id}/`) or `ticketing_failed` (money taken, no "
        "ticket — show `order.message`, support is involved).\n"
        "* `failed` — wrong code, insufficient funds, card blocked: see "
        "`payment.error`. Nothing was charged; start again from step 1.\n"
        "* `processing` — the charge was sent and the provider's answer was "
        "lost. **Do not retry**: keep polling the order, the background sweep "
        "settles it within minutes.\n\n"
        "The charge is sent to the provider **at most once** per attempt "
        "whatever the client does: a repeat of this call while the answer is "
        "pending is a read."
    ),
    response_description=(
        "The order with `payment.status` `paid`, `failed` or `processing`."
    ),
    responses=error_responses(
        ErrorCode.CONFLICT,
        ErrorCode.OFFER_EXPIRED,
        ErrorCode.UPSTREAM_ERROR,
        conflict=(
            "`payment_id` is not the open attempt (a newer step 1 superseded "
            "it, or it was never started), or the attempt was started with a "
            "provider that is no longer the active one — start again."
        ),
        offer_expired=(
            "The payment deadline passed while the code was being typed; the "
            "order is `cancelled` and nothing was charged."
        ),
        upstream_error="No payment provider is configured on this installation.",
    ),
    dependencies=[Depends(RateLimit("payment"))],
)
async def confirm_payment(
    id: uuid.UUID,
    payload: PaymentConfirmIn,
    customer: CurrentCustomer,
    session: SessionDep,
    idempotency: IdempotencyKey,
    language: LanguageDep,
) -> BookingResultOut:
    if idempotency.is_replay:
        return await service.get_order(
            session, customer.id, id, language=language.requested
        )
    try:
        result = await service.confirm_payment(
            session, customer.id, id, payload, language=language.requested
        )
    except AppError:
        await idempotency.release()
        raise
    await idempotency.store({"order_id": str(id)})
    return result


__all__ = ["router"]
