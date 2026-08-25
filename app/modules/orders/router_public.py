"""``/public/orders/`` — a customer reads their own bookings back, and pays.

Booking itself lives on the product flow (``POST /public/{product}/booking/``,
``products/router_public.py``): creating an order is the last step of choosing
a flight; reading orders back is this resource. Every row here requires the
owner's token, so the check sits on the router, not on each handler — the
saved-cards arrangement.

``GET`` here never writes. The "is my ticket ready yet?" screen polls this
endpoint, and the sweep — not the read — is what asks GTS; two polls racing a
sweep must not be able to move an order twice.

Paying begins with the price — ``reprice/`` asks GTS what the hold costs
today and settles it there and then if nothing moved, ``reprice/confirm/``
accepts a price that did; ``payment/`` refuses until one of the two has —
and is then three ``POST``s on the order — ``payment/`` sends the
code, ``payment/resend/`` sends it again for the same attempt,
``payment/confirm/`` charges with it — all under the tight ``payment`` rate
limit, the three payment steps idempotent: a repeat answers with the order
as it stands now, and
never reaches the provider twice. The row lock and the attempt table are
what make the charge single; the idempotency key only spares the provider a
call. Every failure releases the key — nothing is charged at ``start`` or
``resend``, and ``confirm`` answers a lost provider call with the order
rather than an error, so there is no outcome a kept claim would guard.

``receipt/`` is the end of the road: once the ticket exists GTS will render
the itinerary receipt, but it will not serve it to a customer — its receipt
page wants the agent session's cookies and answers ``401`` without them. So
the bytes are fetched here, with ours, and handed on; ``order.receipt_url``
points at this route, and this is the one answer on the router that is a file
rather than the envelope.

``cancel/`` is the way out of the same stretch: while the order is unpaid the
customer can hand the seat back, and GTS is told before our own record moves,
because GTS is the one holding it.

The ``description`` on each route is written for the developer reading
Swagger: what to send, what comes back, what to do next.
"""

import uuid

from fastapi import Depends, Response

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
from app.modules.orders.receipt import (
    RECEIPT_RESPONSES,
    PassengerIndex,
    receipt_response,
)
from app.modules.orders.schemas import (
    TICKETED_EXAMPLE,
    BookingResultOut,
    OrderListItemOut,
    PaymentConfirmIn,
    PaymentResendIn,
    PaymentStartIn,
    RepriceOut,
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
        "`order_data` for display detail. Once the ticket is issued, "
        "**`order.receipt_url`** is where the itinerary receipt is "
        "downloaded — `GET …/{id}/receipt/` on this API, fetched with the "
        "same token, answering with the file itself; `null` while there is "
        "no ticket. **Never writes** — poll it freely "
        "while `order.status` is `ticket_waiting` or `payment.status` is "
        "`processing`; the background sweep settles those and this read "
        "reflects it. Another customer's order is a `404`, not a `403`."
    ),
    response_description="The order, in the same shape booking answered.",
    # A **ticketed** order for the example, not the booked one the model
    # carries: the fields a client comes here for — `receipt_url`, the ticket
    # numbers, `paid_at` — are exactly the ones a booked order leaves `null`,
    # and Swagger's generation deletes nulls from a model-level example.
    # ``openapi_extra`` is re-applied afterwards, so this one survives whole.
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "status": "success",
                            "data": TICKETED_EXAMPLE,
                            "errors": [],
                            "meta": None,
                        }
                    }
                }
            }
        }
    },
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


@router.get(
    "/{id}/receipt/",
    summary="Receipt — download the ticket's itinerary receipt",
    description=(
        "The itinerary receipt of a **ticketed** order, exactly as GTS "
        "renders it — the document the passenger travels with. The answer is "
        "the file itself (a PDF), **not the envelope**: fetch it with the "
        "customer's token like any other call and save or display what comes "
        "back. `Content-Type` says what it is and `Content-Disposition` "
        "carries a filename.\n\n"
        "```js\n"
        "const r = await fetch(order.receipt_url, "
        "{headers: {Authorization: `Bearer ${token}`}});\n"
        "const blob = await r.blob();\n"
        "```\n\n"
        "`order.receipt_url` on the order answer is this path, and it is "
        "`null` until the ticket exists — that is the flag to show the "
        "download button on. Asking earlier is a `409`: an order still being "
        "ticketed has no receipt to render.\n\n"
        "GTS renders the document but will not serve it to a browser — its "
        "receipt page answers `401` without the agent session — so this API "
        "fetches it with its own and passes the bytes through.\n\n"
        "Add `?passenger_index=0` for a single traveller's copy; without it "
        "the document covers everyone on the order.\n\n"
        "Nothing is stored on our side: every call renders the receipt at "
        "GTS, so what the customer downloads is always the current one. Safe "
        "to repeat, and it changes nothing."
    ),
    response_description=(
        "The receipt file, with GTS's own content type and a filename."
    ),
    response_class=Response,
    responses=RECEIPT_RESPONSES,
)
async def download_receipt(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    passenger_index: PassengerIndex = None,
) -> Response:
    return receipt_response(
        await service.order_receipt(
            session, customer.id, id, passenger_index=passenger_index
        )
    )


@router.post(
    "/{id}/reprice/",
    summary="Pay — step 0: check today's price",
    description=(
        "Asks GTS what the held order costs right now (`reprice_check`) and "
        "answers with the verdict and GTS's data as is: `changed` — did the "
        "price move; `old_price` — what the order holds, the price the "
        "customer has been looking at; `new_price` — GTS's price today; then "
        "GTS's own `price_info` (`price`, `currency`, `fee_amount`, …) and "
        "`price_details`, with agent commission fields removed. The price itself "
        "is never moved here: `payment.amount` stays what it was and no code "
        "already sent is voided.\n\n"
        "**Read `changed` and the two prices, not `price_info`.** `changed` "
        "is GTS's own verdict, and when it says the price did not move "
        "`new_price` equals `old_price` whatever `price_info` holds: GTS "
        "answers an unchanged order either with no price at all "
        "(`price_info: {}`) or with the *provider's* fare in the provider's "
        "currency — 294 EUR against an order booked at 343.04 USD, its own "
        "record still reading 343.04. That figure is passed through for the "
        "breakdown; it is not what this order costs.\n\n"
        "**`changed: false` — go straight to `payment/`.** There is nothing "
        "to confirm and GTS refuses to confirm it: this call is the whole "
        "price step, and it leaves `payment.price_confirmed = true`.\n\n"
        "**`changed: true` — show `new_price` (and `old_price`) and ask.** "
        "Once the customer accepts, call `reprice/confirm/`: that is the "
        "step that updates the order and unlocks `payment/`.\n\n"
        "GTS wants a check before it will issue a ticket, so this call is "
        "never skipped. Repeatable at no cost.\n\n"
        "Nothing is charged here. Under the `payment` rate limit."
    ),
    response_description=(
        "`changed`, `old_price`, `new_price`, and GTS's `reprice_check` answer "
        "(`price_info`, `price_details`) with commission fields removed."
    ),
    responses=error_responses(
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        upstream_error=(
            "GTS could not price the order — released or unknown; GTS's words "
            "are in `meta.upstream`. (An answer without a price is not this: "
            "it means the price has not changed.)"
        ),
        upstream_timeout="GTS did not answer.",
    ),
    dependencies=[Depends(RateLimit("payment"))],
)
async def reprice_order(
    id: uuid.UUID, customer: CurrentCustomer, session: SessionDep
) -> RepriceOut:
    return await service.reprice_order(session, customer.id, id)


@router.post(
    "/{id}/reprice/confirm/",
    summary="Pay — step 0b: accept the price",
    description=(
        "The customer accepted the **new** price `reprice/` showed: confirms "
        "it with GTS, reads the order back and unlocks `payment/`.\n\n"
        "Only needed when `reprice/` answered `changed: true`. A price that "
        "did not move is already settled by `reprice/` itself — GTS keeps "
        "nothing to confirm for one and refuses the call — so calling this "
        "then is harmless but pointless: it re-checks with GTS, finds "
        "nothing moved, sends no confirmation and answers the order as it "
        "stands.\n\n"
        "When the price did move, GTS answers with the price it confirmed, "
        "and that is the one stored and charged — it replaces whatever the "
        "order held, and a code already sent for another amount is void. "
        "The answer is the "
        "order as it now stands, everything at that price — "
        "`payment.amount`, `order.amount`, `order_data.price_info` / "
        "`price_details` — plus a fresh `pay_before` and `order_data`. Show "
        "`payment.amount` on the payment screen; it is what the customer "
        "pays and what GTS will debit at ticketing. `payment.price_confirmed` "
        "is now `true`. Repeatable."
    ),
    response_description=(
        "The order, re-read from GTS, with the confirmed price in "
        "`payment.amount`, `order.amount` and `order_data`, and "
        "`payment.price_confirmed = true`."
    ),
    responses=error_responses(
        ErrorCode.CONFLICT,
        ErrorCode.OFFER_EXPIRED,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        conflict=(
            "The order is not payable (cancelled, already paid, being "
            "refunded or ticketed), or a charge for it is being confirmed "
            "right now."
        ),
        offer_expired=(
            "GTS confirmed the price but the read-back shows it has released "
            "the hold since; the order is `cancelled` — search again."
        ),
        upstream_error=(
            "GTS refused to confirm the price, or confirmed it in another "
            "currency than the order's (refused, nothing changed); GTS's "
            "words are in `meta.upstream`. Check it again with `reprice/`."
        ),
        upstream_timeout="GTS did not answer.",
    ),
    dependencies=[Depends(RateLimit("payment"))],
)
async def confirm_price(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    language: LanguageDep,
) -> BookingResultOut:
    return await service.confirm_price(
        session, customer.id, id, language=language.requested
    )


@router.post(
    "/{id}/payment/",
    summary="Pay — step 1: choose the card, receive the code",
    description=(
        "Starts a payment for a `booked`, unpaid order whose price has been "
        "settled with GTS — `reprice/`, plus `reprice/confirm/` when it "
        "said the price moved; otherwise `409`. "
        "Send `method` — a "
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
            "refunded or ticketed), its price is not confirmed yet, a charge "
            "for it is being confirmed right now, or an identical request is "
            "still in flight."
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


@router.post(
    "/{id}/cancel/",
    summary="Cancel — give the seat back before paying",
    description=(
        "Releases a booking the customer no longer wants, **before any "
        "money and before any ticket**. GTS is told first — it is the one "
        "holding the seat — and the order is then recorded as `cancelled` "
        "with `cancel_reason: customer`.\n\n"
        "Allowed while the order is `booked` and unpaid: a failed payment "
        "attempt does not stand in the way, a charge being confirmed right "
        "now does (`409` — wait for it to settle). Once the ticket is paid "
        "for there is nothing to cancel here; that is a refund, and support "
        "handles it.\n\n"
        "**Safe to repeat.** An order that is already cancelled answers "
        "`200` with the order as it stands and GTS is not called again, so "
        "a retried tap or a lost answer costs nothing. No "
        "`Idempotency-Key` needed.\n\n"
        "The answer is the full order: `order.status` is `cancelled`, "
        "`order.cancel_reason` is `customer`, `payment.status` is "
        "`cancelled`, and `order.message` is the sentence to show. To fly "
        "after all, search again — this booking is gone."
    ),
    response_description=(
        "The order, now `cancelled`, with `cancel_reason: customer` and the "
        "message for that status."
    ),
    responses=error_responses(
        ErrorCode.CONFLICT,
        ErrorCode.UPSTREAM_ERROR,
        ErrorCode.UPSTREAM_TIMEOUT,
        conflict=(
            "There is nothing to release: the order is paid, being refunded "
            "or already being ticketed — or a charge for it is being "
            "confirmed right now, in which case try again once it settles."
        ),
        upstream_error=(
            "GTS refused to release the booking and still shows it held; "
            "its words are in `meta.upstream`. Nothing was changed."
        ),
        upstream_timeout=(
            "GTS did not answer and the booking still shows as held. "
            "Nothing was changed; read the order back and try again."
        ),
    ),
    dependencies=[Depends(RateLimit("payment"))],
)
async def cancel_order(
    id: uuid.UUID,
    customer: CurrentCustomer,
    session: SessionDep,
    language: LanguageDep,
) -> BookingResultOut:
    return await service.cancel_order(
        session, customer.id, id, language=language.requested
    )


__all__ = ["router"]
