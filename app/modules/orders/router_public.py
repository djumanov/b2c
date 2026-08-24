"""``/public/orders/`` — a customer reads their own bookings back, and pays.

Booking itself lives on the product flow (``POST /public/{product}/booking/``,
``products/router_public.py``): creating an order is the last step of choosing
a flight; reading orders back is this resource. Every row here requires the
owner's token, so the check sits on the router, not on each handler — the
saved-cards arrangement.

``GET`` here never writes. The "is my ticket ready yet?" screen polls this
endpoint, and the sweep — not the read — is what asks GTS; two polls racing a
sweep must not be able to move an order twice.

Paying is three ``POST``s on the order — ``payment/`` sends the code,
``payment/resend/`` sends it again for the same attempt, ``payment/confirm/``
charges with it — all three under the tight ``payment`` rate limit and all
three idempotent: a repeat answers with the order as it stands now, and
never reaches the provider twice. The row lock and the attempt table are
what make the charge single; the idempotency key only spares the provider a
call. Every failure releases the key — nothing is charged at ``start`` or
``resend``, and ``confirm`` answers a lost provider call with the order
rather than an error, so there is no outcome a kept claim would guard.

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
    PaymentResendIn,
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
        "Starts a payment for a `booked`, unpaid order. Send `method` — a "
        "`code` from site-config `payment_methods` (the methods this "
        "installation has switched on; anything else is a `422` naming "
        "`method`) — and **either** `card_id` (a saved card) **or** `card` "
        "(number + `MMYY`), optionally `save: true` to keep a typed card "
        "once the provider accepts it.\n\n"
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
        conflict=(
            "`payment_id` is not the open attempt (a newer step 1 superseded "
            "it, or it was never started), or the payment method it was "
            "started with has been switched off since — start again."
        ),
        offer_expired=(
            "The payment deadline passed while the code was being typed and "
            "GTS, asked again, has released the hold; the order is `cancelled` "
            "and nothing was charged. While GTS still holds the seat the "
            "charge goes through as usual, whatever our clock says."
        ),
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


@router.post(
    "/{id}/payment/resend/",
    summary="Pay — resend the code",
    description=(
        "Sends the cardholder the same one-time code again, for the open "
        "attempt from step 1 — no new card is registered, no new attempt "
        "is opened, and nothing is charged. Send the `payment_id` from "
        "step 1 (or from a previous resend).\n\n"
        "Real providers (Payme) text a fresh code over the same channel; "
        "providers whose code is fixed for the whole installation (the "
        "demo, the sandbox) do nothing — there is nothing to resend. "
        "Either way the answer is the order with `payment.status` still "
        "`awaiting_otp` and `payment.phone_hint`.\n\n"
        "A refusal to send another code is **not an error**: `200` with "
        "`payment.status = failed` and the reason in `payment.error`, "
        "exactly like a declined card at step 1 — start a fresh payment "
        "to try again. If the provider's answer is lost instead "
        "(`502`/`504`), the attempt is left open and the code already sent "
        "may still work — retry the resend or go straight to step 2.\n\n"
        "Under the same `payment` rate limit as steps 1 and 2 (ten "
        "requests a minute) — that limit is the only cooldown between "
        "resends, on purpose: there is no separate per-attempt wait timer "
        "to keep in sync with it.\n\n"
        "Idempotent like the other two steps: the same body within 24 "
        "hours replays the stored answer and sends no second SMS — send a "
        "fresh `Idempotency-Key` for a deliberate extra resend."
    ),
    response_description=(
        "The order with `payment.status` still `awaiting_otp` (a fresh "
        "code is on its way) or `failed` (the provider refused to send "
        "another one, see `payment.error`)."
    ),
    responses=error_responses(
        ErrorCode.CONFLICT,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        conflict=(
            "`payment_id` is not the open attempt (a newer step 1 "
            "superseded it, or it was never started), a charge for it is "
            "being confirmed right now, or the payment method it was "
            "started with has been switched off since — start again."
        ),
        upstream_error=(
            "The provider's own system refused the request — nothing to "
            "do with this card. Nothing was charged; try again shortly."
        ),
        upstream_timeout="The provider did not answer. Nothing was charged.",
    ),
    dependencies=[Depends(RateLimit("payment"))],
)
async def resend_payment_otp(
    id: uuid.UUID,
    payload: PaymentResendIn,
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
        result = await service.resend_payment_otp(
            session, customer.id, id, payload, language=language.requested
        )
    except AppError:
        await idempotency.release()
        raise
    await idempotency.store({"order_id": str(id)})
    return result


__all__ = ["router"]
