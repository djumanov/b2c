"""What the client sees of an order (modeled on the EasyBooking contract).

A booking answers — and ``GET /public/orders/{id}/`` repeats — four blocks:

* ``order`` — our slim record: ids, money, deadline, and **one** ``status``
  with its sentence (``message``). The status is the three lifecycle columns
  read together (``lifecycle.stage_of``); the columns themselves are for
  staff and appear only on the admin surface, as ``booking_status``,
  ``payment_status`` and ``ticketing_status``. This is the stable contract;
  it comes from columns, never from GTS's spellings.
* ``payment`` — what a payment screen needs: how much, until when, where the
  money stands, and (once a payment attempt exists) which attempt, which card,
  and the phone the OTP went to.
* ``ticketing`` — where the ticket stands, when it was asked for, the issued
  ticket numbers by passenger, and GTS's reason when it failed.
* ``order_data`` — GTS's answer nearly verbatim: routes, passengers, fares,
  baggage. The client reads display detail here, exactly as it already reads
  GTS's shapes throughout the search flow. Commission and cost fields are
  stripped on the way out — agent economics are not the customer's business —
  while the stored copy keeps them. Once ``reprice/confirm/`` has run, its
  ``price_info``/``price_details`` are the confirmed ones (``price_response``),
  the same figures ``order.amount`` and ``payment.amount`` show.

The list (``GET /public/orders/``) is a fifth shape, and it takes GTS's
``routes`` with it: an order card shows the airline, the flight number, the
times and the airports, and every one of those lives in a segment. Passengers
come along as **names only** — a card says who is flying, and a passport number
has no business riding on a request that returns twenty rows.
The rest of GTS's answer stays on ``{id}/``.

Every field carries a ``description``: the generated OpenAPI is the contract
a client developer reads, and a ``#:`` comment never reaches it.
"""

import uuid
from datetime import datetime
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from app.core import i18n
from app.core.money import Money
from app.modules.orders.lifecycle import CONFIRMING, Stage, stage_of
from app.modules.orders.messages import MessageCatalogue
from app.modules.orders.models import (
    AttemptStatus,
    Order,
    OrderStatus,
    PaymentStatus,
    TicketingStatus,
)
from app.modules.payments.schemas import CardIn
from app.providers.products import gts_order


def _money(order: Order) -> Money | None:
    if order.amount is None or order.currency is None:
        return None
    return Money(amount=order.amount, currency=order.currency)


def _order_data(order: Order) -> dict[str, Any]:
    """GTS's order for the client, with the repriced figures where there are any.

    ``gts_response`` is the order record, and its ``price_info`` is the
    booking's. Once ``reprice_confirm`` has run, ``price_response`` is GTS's
    later word: its ``price_info`` — and ``price_details``, when it carries any —
    replace the record's, so the client never reads a fare that ``amount``
    has moved on from. Stripped of agent economics on the way out, as ever.
    """
    data: dict[str, Any] = order.gts_response
    repriced = order.price_response
    if isinstance(repriced, dict):
        overlay: dict[str, Any] = {}
        info = repriced.get("price_info")
        if isinstance(info, dict):
            overlay["price_info"] = info
        details = repriced.get("price_details")
        if isinstance(details, list) and details:
            overlay["price_details"] = details
        if overlay:
            data = {**data, **overlay}
    stripped: dict[str, Any] = strip_commission(data)
    return stripped


def strip_commission(value: Any) -> Any:
    """Drop agent-economics keys, recursively, wherever GTS put them."""
    if isinstance(value, dict):
        return {
            key: strip_commission(item)
            for key, item in value.items()
            if not (
                isinstance(key, str) and ("commission" in key or key == "cost_price")
            )
        }
    if isinstance(value, list):
        return [strip_commission(item) for item in value]
    return value


# --- the order block ------------------------------------------------------------------

#: Where each surface serves the receipt. Root-relative on purpose: the
#: installation's own domain is a database setting, not something a schema
#: knows (the rule ``storage.url`` follows for uploads). A customer answer
#: carries the first, the support desk's answer the second — a staff token on
#: the customer's path is a 403, and the other way round.
RECEIPT_PATH: Final = "/api/v1/public/orders/{id}/receipt/"
ADMIN_RECEIPT_PATH: Final = "/api/v1/admin/orders/{id}/receipt/"

_MONEY_OR_NULL = (
    "Null only when GTS never reported a price for the booking — rare, and "
    "the order is still recorded."
)


class OrderOut(BaseModel):
    """Our record of one booking — the stable part of every order answer."""

    id: uuid.UUID = Field(description="Our order id; every later call takes it.")
    product: str = Field(description="Vertical code — `flight` in this release.")
    status: Stage = Field(
        description=(
            "The one status a screen shows, read off the order's lifecycle on "
            "every request. Same word on the list and here. See `Stage` for "
            "what each value means."
        )
    )
    message: str = Field(
        description=(
            "The sentence that goes with `status`, in the request's language "
            "(`?lang=` / `Accept-Language`), written by the operator. Show it "
            "as is."
        )
    )
    cancel_reason: str | None = Field(
        description=(
            "Set when `status` is `cancelled`: `customer`, `staff`, or "
            "`expired` — the payment deadline passed."
        )
    )
    gts_status: str = Field(
        description=(
            "GTS's own status code, verbatim (`BO` held, `PW` issuing, `TI` "
            "ticketed, `CB`/`VO` released). Informational — key the UI on "
            "`status`."
        )
    )
    gts_order_number: int = Field(
        description="GTS's order number — what support quotes to GTS."
    )
    pnr: str | None = Field(
        description="The airline booking reference, once GTS has one."
    )
    trip_type: str | None = Field(description="`OW` one-way, `RT` round trip.")
    route_summary: str | None = Field(
        description="The journey in one string, e.g. `TAS-IST, IST-TAS`."
    )
    passenger_count: int | None = Field(description="How many travellers.")
    amount: Money | None = Field(description=f"The total to pay. {_MONEY_OR_NULL}")
    ticket_time_limit_at: datetime | None = Field(
        description=(
            "When GTS releases the unpaid seat. Also `payment.pay_before`. "
            "Null when GTS gave no deadline."
        )
    )
    paid_at: datetime | None = Field(description="When the charge landed.")
    ticketed_at: datetime | None = Field(description="When GTS issued the ticket.")
    receipt_url: str | None = Field(
        examples=[
            "/api/v1/public/orders/5f0d87c1-9c58-4bff-8f95-6a1f61f4d1f7/receipt/"
        ],
        description=(
            "**Download the itinerary receipt here** — the travel document "
            "the passenger shows at the airport, rendered by GTS. A path on "
            "**this** API: fetch it with the customer's token like any other "
            "call, and the answer is the file itself (`application/pdf`), "
            "not the envelope — save it or show it. Root-relative, so "
            "prefix it with the API's own base URL.\n\n"
            "GTS renders the document but will not serve it to a browser: "
            "its receipt page answers `401` without the agent session, so "
            "this API fetches the bytes with its own and passes them "
            "through.\n\n"
            "`null` until `status` is `ticketed` — before the ticket exists "
            "there is nothing to render, so this field is what a download "
            "button waits for. One document covers everyone on the order."
        ),
    )
    cancelled_at: datetime | None = Field(description="When the order was cancelled.")
    request_id: str = Field(
        description="The search this booking came from (provenance only)."
    )
    offer_id: str = Field(description="The offer that was booked (provenance only).")
    created_at: datetime = Field(description="When the booking was recorded.")

    @classmethod
    def from_order(
        cls,
        order: Order,
        *,
        language: str | None,
        messages: MessageCatalogue,
        receipt_url: str | None = None,
    ) -> "OrderOut":
        # Assembled by hand rather than ``from_attributes`` because ``amount``
        # is two columns composed into one ``Money``.
        status = stage_of(order)
        return cls(
            id=order.id,
            product=order.product,
            status=status,
            message=messages.render(status, language=language),
            cancel_reason=order.cancel_reason,
            gts_status=order.gts_status,
            gts_order_number=order.gts_order_number,
            pnr=order.pnr,
            trip_type=order.trip_type,
            route_summary=order.route_summary,
            passenger_count=order.passenger_count,
            amount=_money(order),
            ticket_time_limit_at=order.ticket_time_limit_at,
            paid_at=order.paid_at,
            ticketed_at=order.ticketed_at,
            # Passed in, not built here: the link is GTS's, and where GTS
            # lives is a database setting the vertical spells (the orders
            # service reads both and hands the result over).
            receipt_url=receipt_url,
            cancelled_at=order.cancelled_at,
            request_id=order.request_id,
            offer_id=order.offer_id,
            created_at=order.created_at,
        )


class PassengerNameOut(BaseModel):
    """Who is flying, as a list card names them — nothing more.

    All three nullable: GTS omits a middle name more often than it sends one,
    and a card that cannot name the traveller is still a card. Documents,
    birth dates and contacts are on `GET /public/orders/{id}/` only.
    """

    first_name: str | None = Field(description="Given name as GTS holds it.")
    last_name: str | None = Field(description="Family name as GTS holds it.")
    middle_name: str | None = Field(description="Usually absent.")


class OrderListItemOut(BaseModel):
    """One row of "my orders" — enough to draw the card, and no more."""

    id: uuid.UUID = Field(description="Our order id.")
    product: str = Field(description="Vertical code — `flight`.")
    status: Stage = Field(
        description="The same `status` the detail shows — see `Stage`."
    )
    pnr: str | None = Field(description="Airline booking reference, once known.")
    trip_type: str | None = Field(description="`OW` or `RT`.")
    route_summary: str | None = Field(description="`TAS-IST, IST-TAS`.")
    passenger_count: int | None = Field(description="How many travellers.")
    amount: Money | None = Field(description=f"The total. {_MONEY_OR_NULL}")
    ticket_time_limit_at: datetime | None = Field(
        description="Pay before this or GTS releases the seat."
    )
    created_at: datetime = Field(description="When the booking was recorded.")
    routes: list[dict[str, Any]] = Field(
        description=(
            "GTS's route objects verbatim — segments with airline, flight "
            "number, airports and times, the same shapes the search flow "
            "returns. Empty when the stored answer carried none."
        )
    )
    passengers: list[PassengerNameOut] = Field(
        description="Names only; documents and contacts live on `{id}/`."
    )

    @classmethod
    def from_order(cls, order: Order) -> "OrderListItemOut":
        return cls(
            id=order.id,
            product=order.product,
            status=stage_of(order),
            pnr=order.pnr,
            trip_type=order.trip_type,
            route_summary=order.route_summary,
            passenger_count=order.passenger_count,
            amount=_money(order),
            ticket_time_limit_at=order.ticket_time_limit_at,
            created_at=order.created_at,
            routes=[
                strip_commission(route)
                for route in gts_order.routes(order.gts_response)
            ],
            passengers=[
                PassengerNameOut.model_validate(person)
                for person in gts_order.passenger_names(order.gts_response)
            ],
        )


# --- paying, the two request bodies ---------------------------------------------------


class PaymentStartIn(BaseModel):
    """Step 1 of paying: which method charges, and which card.

    `method` is a `code` from site-config `payment_methods` — required, no
    default. The card is exactly one of `card_id` and `card`. A validation
    error never echoes a card number. `save` keeps a typed card for next
    time, once the provider has accepted it (never before); it means nothing
    with `card_id`.
    """

    model_config = {
        "extra": "forbid",
        "hide_input_in_errors": True,
        "json_schema_extra": {
            "examples": [
                {
                    "method": "payme",
                    "card_id": "3f7c9b2e-1d44-4a3b-9a1e-2b7c8d9e0f11",
                },
                {
                    "method": "payme",
                    "card": {"number": "8600 0691 9540 6311", "expire": "03/99"},
                },
                {
                    "method": "click",
                    "card": {"number": "8600069195406311", "expire": "0399"},
                    "save": True,
                },
            ]
        },
    }

    method: str = Field(
        min_length=1,
        max_length=32,
        description=(
            "Which payment method charges — a `code` from site-config "
            "`payment_methods` (e.g. `payme`), sent verbatim. Required: "
            "there is no default and no fallback; a method this installation "
            "has not enabled is a `422` naming `method`."
        ),
    )
    card_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "A card from `GET /public/profile/cards/`. Omit when sending `card`."
        ),
    )
    card: CardIn | None = Field(
        default=None,
        description=(
            "A card typed now: number and `MMYY` expiry. Omit when sending `card_id`."
        ),
    )
    save: bool = Field(
        default=False,
        description=(
            "With `card` only: keep this card in the customer's saved cards "
            "once the provider has accepted it. A card the customer already "
            "has is reused, not duplicated."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "PaymentStartIn":
        if (self.card_id is None) == (self.card is None):
            raise ValueError("Send either card_id or card")
        if self.save and self.card is None:
            raise ValueError("save applies to a card typed now, not to card_id")
        return self


class PaymentConfirmIn(BaseModel):
    """Step 2: the code the cardholder received, for the attempt it belongs to.

    `payment_id` is required on purpose: after a second `payment/` the
    customer may still be typing the first SMS's code, and "confirm the open
    attempt" would silently pair it with the wrong one.
    """

    model_config = {
        "extra": "forbid",
        "hide_input_in_errors": True,
        "json_schema_extra": {
            "examples": [
                {
                    "payment_id": "9c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f",
                    "otp": "123456",
                }
            ]
        },
    }

    payment_id: uuid.UUID = Field(
        description="`payment.payment_id` from the step-1 answer."
    )
    otp: SecretStr = Field(
        description="The one-time code the provider texted the cardholder."
    )


class PaymentResendIn(BaseModel):
    """Resend: the same open attempt to speak to again — nothing else changes."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [{"payment_id": "9c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"}]
        },
    }

    payment_id: uuid.UUID = Field(
        description="`payment.payment_id` from the step-1 (or a later resend's) answer."
    )


class RepriceOut(BaseModel):
    """Step 0's answer: GTS's ``reprice_check`` data as it came, plus the verdict.

    ``old_price`` is what the order holds — the price the customer has been
    looking at (the booking's, or the last confirmed one); ``new_price`` is
    what GTS says today; ``changed`` compares the two so no client has to.
    Everything else is GTS's ``data`` handed through (``price_info``,
    ``price_details`` and whatever GTS adds), agent commission stripped.
    Nothing here is stored: the order changes only at ``reprice/confirm/``.

    ``changed`` is **GTS's own verdict** (``price_changed``), not a
    comparison of the two figures: an unchanged order comes back either with
    no price to hand through (``price_info`` empty) or with the provider's
    fare in the provider's currency, and either way ``new_price`` is
    ``old_price``. A client reads the two prices and the verdict, never the
    raw quote.
    """

    model_config = {
        "extra": "allow",
        "json_schema_extra": {
            "examples": [
                {
                    "changed": True,
                    "old_price": {"amount": "287500.00", "currency": "UZS"},
                    "new_price": {"amount": "301200.00", "currency": "UZS"},
                    "price_info": {
                        "price": 301200.0,
                        "currency": "UZS",
                        "fee_amount": 0,
                    },
                    "price_details": [],
                }
            ]
        },
    }

    changed: bool = Field(
        description=(
            "GTS's own verdict on whether the price moved. `true` — show "
            "`new_price` (and `old_price`) and ask the customer before "
            "`reprice/confirm/`. `false` — same price, confirm straight "
            "away. Where GTS sends no verdict it is `new_price` differing "
            "from `old_price` in amount or currency (or the order holding "
            "no price)."
        )
    )
    old_price: Money | None = Field(
        description=(
            "The price the order holds now — what the customer has been shown: "
            "the booking's, or the last one confirmed. Null only when GTS "
            "never reported a price for the booking."
        )
    )
    new_price: Money = Field(
        description=(
            "What the order costs today: GTS's `price_info` as `Money` when "
            "it says the price moved, `old_price` again when it says it did "
            "not. **This, not `price_info`, is the figure to show.**"
        )
    )
    price_info: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "GTS's `price_info` verbatim (`price`, `currency`, `fee_amount`, …), "
            "commission fields removed — the breakdown, not the verdict. "
            "Empty `{}` when GTS quoted nothing, and with `changed: false` "
            "it may be the provider's own fare in another currency (294 EUR "
            "beside an order priced in UZS) — read `new_price` and `changed`, "
            "which are always there and always in `UZS`."
        ),
    )
    price_details: list[Any] = Field(
        default_factory=list,
        description="GTS's per-passenger breakdown verbatim, when it sends one.",
    )


# --- the payment block ---------------------------------------------------------------

#: ``PaymentOut.status`` — the order's ``payment_status`` plus three readings
#: off the open attempt. Declared once so the schema, the code and the test
#: that pins them agree.
PAYMENT_VIEW_STATUSES: Final = (
    "pending",
    "awaiting_otp",
    "processing",
    "paid",
    "failed",
    "cancelled",
    "refunding",
    "refunded",
    "refund_failed",
)
PaymentViewStatus = Literal[
    "pending",
    "awaiting_otp",
    "processing",
    "paid",
    "failed",
    "cancelled",
    "refunding",
    "refunded",
    "refund_failed",
]


class PaymentAttemptView(BaseModel):
    """The open or latest payment attempt, as the payment block shows it.

    Filled by the payment flow; a freshly booked order has none. Kept apart
    from the attempt row so the response never grows a provider reference.
    """

    id: uuid.UUID
    status: str
    provider: str
    card_last4: str | None = None
    phone_hint: str | None = None
    paid_at: datetime | None = None
    error: str | None = None


class PaymentOut(BaseModel):
    """Where the money stands — the block the payment screen keys on."""

    status: PaymentViewStatus = Field(
        description=(
            "`pending` — nothing started; `awaiting_otp` — a code was sent, "
            "call `payment/confirm/` with it (also where a refused code "
            "lands: see `error`, then send another code or `payment/resend/` "
            "— the attempt is still open and nothing was charged); "
            "`processing` — the charge was sent and the answer is not back, "
            "keep polling; `paid`; `failed` — the charge was declined, see "
            "`error`, start again from step 1; `cancelled` — the order was "
            "released before any money moved; `refunding` / `refunded` / "
            "`refund_failed` — a refund marked by support. Payment attempts "
            "never change `order.status`."
        )
    )
    amount: Money | None = Field(description=f"What is charged. {_MONEY_OR_NULL}")
    price_confirmed: bool = Field(
        description=(
            "`true` once the price is settled with GTS — step 1 of payment "
            "refuses until then. `reprice/` alone settles a price that did "
            "not move (GTS refuses to confirm one that did not); a price "
            "that moved is settled by `reprice/confirm/`."
        )
    )
    pay_before: datetime | None = Field(
        description="Pay before this moment or GTS releases the seat."
    )
    payment_id: uuid.UUID | None = Field(
        default=None,
        description="The attempt to confirm — send it back in `payment/confirm/`.",
    )
    provider: str | None = Field(
        default=None, description="`payme`, `click`, `demo`, or `sandbox` in debug."
    )
    card_last4: str | None = Field(
        default=None, description="Last four digits of the card being charged."
    )
    phone_hint: str | None = Field(
        default=None,
        description="The masked phone the code went to, e.g. `99890*****31`.",
    )
    paid_at: datetime | None = Field(
        default=None, description="When the charge landed."
    )
    error: str | None = Field(
        default=None,
        description=(
            "The provider's reason when `status` is `failed`, or why the last "
            "code was refused while `status` is `awaiting_otp`. Written for a "
            "person; cleared as soon as another code or a resend is sent."
        ),
    )

    @classmethod
    def from_order(
        cls, order: Order, attempt: PaymentAttemptView | None
    ) -> "PaymentOut":
        status: str = order.payment_status
        if order.status == OrderStatus.CANCELLED and status in (
            PaymentStatus.PENDING,
            PaymentStatus.FAILED,
        ):
            status = "cancelled"
        elif attempt is not None and status != PaymentStatus.PAID:
            if attempt.status == CONFIRMING:
                status = "processing"
            elif attempt.status == "started":
                status = "awaiting_otp"
        return cls(
            status=cast(PaymentViewStatus, status),
            amount=_money(order),
            price_confirmed=order.price_confirmed_at is not None,
            pay_before=order.ticket_time_limit_at,
            payment_id=attempt.id if attempt else None,
            provider=attempt.provider if attempt else None,
            card_last4=attempt.card_last4 if attempt else None,
            phone_hint=attempt.phone_hint if attempt else None,
            paid_at=order.paid_at,
            error=attempt.error if attempt else None,
        )


# --- the ticketing block -------------------------------------------------------------


class TicketOut(BaseModel):
    """One issued ticket, named by its passenger."""

    passenger: str = Field(description="`First Last`, as GTS spells it.")
    ticket_number: str = Field(description="The airline ticket number.")


class TicketingOut(BaseModel):
    """Where the ticket stands — the block the "please wait" screen polls."""

    status: TicketingStatus = Field(
        description=(
            "`pending` — not asked for yet (unpaid); `processing` — asked, GTS "
            "is working; `ticketed` — issued, see `tickets`; `failed` — GTS "
            "refused, see `error` and `order.message`."
        )
    )
    requested_at: datetime | None = Field(
        description="When the ticket was last asked for."
    )
    ticketed_at: datetime | None = Field(description="When GTS issued it.")
    tickets: list[TicketOut] = Field(
        description="Issued ticket numbers by passenger; empty until `ticketed`."
    )
    error: str | None = Field(description="GTS's reason when `status` is `failed`.")

    @classmethod
    def from_order(cls, order: Order) -> "TicketingOut":
        return cls(
            status=TicketingStatus(order.ticketing_status),
            requested_at=order.ticketing_requested_at,
            ticketed_at=order.ticketed_at,
            tickets=[
                TicketOut.model_validate(ticket)
                for ticket in gts_order.tickets(order.gts_response)
            ],
            error=order.ticketing_error,
        )


# --- the whole answer ----------------------------------------------------------------

_BOOKING_EXAMPLE: Final[dict[str, Any]] = {
    "product": "flight",
    "order": {
        "id": "5f0d87c1-9c58-4bff-8f95-6a1f61f4d1f7",
        "product": "flight",
        "status": "booked",
        "message": (
            "Bron qilindi. Chiptani olish uchun to'lovni belgilangan "
            "muddatgacha amalga oshiring."
        ),
        "cancel_reason": None,
        "gts_status": "BO",
        "gts_order_number": 61453,
        "pnr": "UBPLKW",
        "trip_type": "OW",
        "route_summary": "TAS-VKO",
        "passenger_count": 1,
        "amount": {"amount": "287500.00", "currency": "UZS"},
        "ticket_time_limit_at": "2026-08-20T06:53:12Z",
        "paid_at": None,
        "ticketed_at": None,
        # Filled once the ticket is issued — see ``TICKETED_EXAMPLE``.
        "receipt_url": None,
        "cancelled_at": None,
        "request_id": "6b4f3a1e-2c7d-4e8f-9a0b-1c2d3e4f5a6b",
        "offer_id": "1f2e3d4c-5b6a-4978-8d9c-0e1f2a3b4c5d",
        "created_at": "2026-08-20T05:54:12Z",
    },
    "payment": {
        "status": "awaiting_otp",
        "amount": {"amount": "287500.00", "currency": "UZS"},
        "price_confirmed": True,
        "pay_before": "2026-08-20T06:53:12Z",
        "payment_id": "9c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f",
        "provider": "payme",
        "card_last4": "6311",
        "phone_hint": "99890*****31",
        "paid_at": None,
        "error": None,
    },
    "ticketing": {
        "status": "pending",
        "requested_at": None,
        "ticketed_at": None,
        "tickets": [],
        "error": None,
    },
    "order_data": {
        "order_number": 61453,
        "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "status": "BO",
        "gds_pnr": "UBPLKW",
        "routes": ["…"],
        "passengers": ["…"],
    },
}


#: The same order once GTS has issued the ticket — the state a client
#: developer is looking for when the question is "where do I get the
#: receipt?". Written out rather than derived, because the whole point is to
#: show the fields a booked order can only leave ``null``: the link, the
#: ticket numbers, and the stamps beside them. ``GET /public/orders/{id}/``
#: publishes it (``router_public``), where a null survives Swagger's
#: generation — a model-level example is stripped of them.
TICKETED_EXAMPLE: Final[dict[str, Any]] = {
    **_BOOKING_EXAMPLE,
    "order": {
        **_BOOKING_EXAMPLE["order"],
        "status": "ticketed",
        "message": "Chiptangiz tayyor. Yaxshi safar!",
        "gts_status": "TI",
        "paid_at": "2026-08-20T06:12:41Z",
        "ticketed_at": "2026-08-20T06:13:05Z",
        "receipt_url": (
            "/api/v1/public/orders/5f0d87c1-9c58-4bff-8f95-6a1f61f4d1f7/receipt/"
        ),
    },
    "payment": {
        **_BOOKING_EXAMPLE["payment"],
        "status": "paid",
        "paid_at": "2026-08-20T06:12:41Z",
    },
    "ticketing": {
        "status": "ticketed",
        "requested_at": "2026-08-20T06:12:42Z",
        "ticketed_at": "2026-08-20T06:13:05Z",
        "tickets": [{"passenger": "AZIMJON YUSUFOV", "ticket_number": "7653081297644"}],
        "error": None,
    },
    "order_data": {**_BOOKING_EXAMPLE["order_data"], "status": "TI"},
}


class BookingResultOut(BaseModel):
    """The booking answer, the order detail and both payment answers — one shape.

    `order.status` is the word a screen shows; `payment.status` is what the
    payment screen does next; `ticketing` is what the "please wait" screen
    polls; `order_data` is GTS's own record for display detail.
    """

    model_config = {"json_schema_extra": {"examples": [_BOOKING_EXAMPLE]}}

    product: str = Field(description="Vertical code — `flight`.")
    order: OrderOut = Field(description="Our record: ids, money, deadline, `status`.")
    payment: PaymentOut = Field(description="Where the money stands.")
    ticketing: TicketingOut = Field(description="Where the ticket stands.")
    order_data: dict[str, Any] = Field(
        description=(
            "GTS's order nearly verbatim — routes with segments, passengers "
            "with documents, fares, baggage — the same shapes as the search "
            "flow, with agent commission fields removed. Once `reprice/confirm/` "
            "has run, `price_info` and `price_details` are the confirmed figures "
            "— the same price `order.amount` and `payment.amount` show. Read "
            "display detail here; never key logic on it."
        )
    )

    @classmethod
    def from_order(
        cls,
        order: Order,
        *,
        language: str | None,
        messages: MessageCatalogue,
        attempt: PaymentAttemptView | None = None,
        receipt_url: str | None = None,
    ) -> "BookingResultOut":
        # The attempt shapes the ``payment`` block only; ``order.status`` is
        # the columns' reading and says the same on the list and here.
        return cls(
            product=order.product,
            order=OrderOut.from_order(
                order, language=language, messages=messages, receipt_url=receipt_url
            ),
            payment=PaymentOut.from_order(order, attempt),
            ticketing=TicketingOut.from_order(order),
            order_data=_order_data(order),
        )


class ReceiptDocument(BaseModel):
    """The receipt on its way out — bytes, not JSON.

    Never part of the published schema: the route answers with the file
    itself, so this only carries it from the service to the route together
    with the two things the response needs to say about it.
    """

    content: bytes
    content_type: str
    filename: str


# --- the support desk ----------------------------------------------------------------


class OrderEventOut(BaseModel):
    """One line of the order's history, oldest first."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime = Field(
        description=(
            "When it happened. Lines one commit wrote share a stamp; the list "
            "is in write order."
        )
    )
    event: str = Field(
        description=(
            "`<lifecycle>.<new value>` — `order.created`, `payment.started`, "
            "`payment.confirming`, `payment.otp_rejected`, `payment.paid`, "
            "`payment.failed`, `payment.refunding`, `ticketing.requested`, "
            "`ticketing.processing`, `ticketing.ticketed`, `ticketing.failed`, "
            "`order.cancelled`…"
        )
    )
    from_value: str | None = Field(description="The column's value before.")
    to_value: str | None = Field(description="The column's value after.")
    actor: str = Field(
        description="`customer`, `system` (the sweep), or `staff:<staff id>`."
    )
    note: str | None = Field(
        description=("Free text: a GTS reason, a refund note, why it was cancelled.")
    )
    data: dict[str, Any] | None = Field(
        description=(
            "Codes and ids only — a GTS status, an attempt id. Never card data."
        )
    )
    request_id: str | None = Field(
        description=("The request it happened in — the same id in our logs and at GTS.")
    )


class PaymentAttemptAdminOut(BaseModel):
    """One conversation with the payment provider — never its reference."""

    id: uuid.UUID = Field(description="The `payment_id` the customer confirmed with.")
    created_at: datetime
    updated_at: datetime
    provider: str = Field(description="`payme`, `click`, `demo`, `sandbox`.")
    status: AttemptStatus = Field(
        description=(
            "`started` — code sent; `confirming` — charge sent, answer not "
            "back; `paid`; `failed`; `abandoned` — superseded or given up "
            "before any charge."
        )
    )
    amount: Money = Field(description="What this attempt would charge.")
    card_last4: str | None = Field(description="Last four digits of the card.")
    phone_hint: str | None = Field(description="The masked phone the code went to.")
    error: str | None = Field(
        description=(
            "The provider's reason on `failed`, or why the last code was "
            "refused while the attempt is still open."
        )
    )
    paid_at: datetime | None = Field(description="When the charge landed.")


class RefundIn(BaseModel):
    """Where the refund stands, as support says — the money moves in the
    provider's own cabinet; this is the record that it did."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [{"status": "refunded", "note": "Payme cabinet, request #42"}]
        },
    }

    status: Literal["refunding", "refunded", "refund_failed"] = Field(
        description=(
            "`refunding` — started in the provider's cabinet; `refunded` — "
            "the money went back (final; the customer's `status` becomes "
            "`refunded`); `refund_failed` — the provider refused. A `ticketed` "
            "order cannot be refunded here."
        )
    )
    note: str | None = Field(
        default=None,
        max_length=500,
        description="For the history line — a cabinet request number, a reason.",
    )


def _check_translated(value: i18n.Translated) -> i18n.Translated:
    """The settings module's rule, repeated: schemas are not a module's
    ``service`` door, so importing it from there would cross the one line the
    architecture draws. Unknown languages are dropped, not refused."""
    return {
        lang: text for lang, text in value.items() if lang in i18n.SUPPORTED_LANGUAGES
    }


#: Long enough for a paragraph with a phone number and an email in it.
MESSAGE_MAX_LENGTH = 1000


class OrderMessageOut(BaseModel):
    """The sentence customers see for one `status`, per language."""

    status: Stage = Field(description="Which status the sentence belongs to.")
    default: i18n.Translated = Field(
        description="What this release ships, per language (`uz`, `ru`, `en`)."
    )
    custom: i18n.Translated = Field(
        description="What the panel wrote — only the languages staff touched."
    )
    text: i18n.Translated = Field(
        description=(
            "What customers actually see: `custom` over `default`, per language."
        )
    )


class OrderMessageIn(BaseModel):
    """Languages merge: send one to change one. An empty string clears that
    language back to the default."""

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "text": {
                        "uz": (
                            "To'lov qabul qilindi, chipta chiqarilmadi. "
                            "+998 90 123 45 67 ga qo'ng'iroq qiling."
                        ),
                        "ru": "",
                    }
                }
            ]
        },
    }

    text: i18n.Translated = Field(
        description=(
            "Language code → sentence, up to 1000 characters each. Only the "
            'languages sent change; `""` restores that language\'s default; '
            "unknown languages are dropped."
        )
    )

    @field_validator("text")
    @classmethod
    def _known_languages(cls, value: i18n.Translated) -> i18n.Translated:
        cleaned = _check_translated(value)
        if not cleaned:
            raise ValueError(
                "send at least one language; "
                f"this release serves {', '.join(i18n.SUPPORTED_LANGUAGES)}"
            )
        for lang, text in cleaned.items():
            if len(text) > MESSAGE_MAX_LENGTH:
                raise ValueError(f"{lang}: at most {MESSAGE_MAX_LENGTH} characters")
        return cleaned


_BOOKING_STATUS_TEXT: Final = "Is the booking alive? `booked` or `cancelled`."
_PAYMENT_STATUS_TEXT: Final = (
    "Where the money is: `pending`, `paid`, `failed` (last attempt), "
    "`refunding`, `refunded`, `refund_failed`."
)
_TICKETING_STATUS_TEXT: Final = (
    "Where the ticket is: `pending`, `processing`, `ticketed`, `failed`."
)


class OrderAdminListItemOut(OrderListItemOut):
    """A list row for support: the customer's row, the three columns behind
    its `status`, who and which GTS order — and the reason, so the inbox
    reads without opening every row."""

    booking_status: OrderStatus = Field(description=_BOOKING_STATUS_TEXT)
    payment_status: PaymentStatus = Field(description=_PAYMENT_STATUS_TEXT)
    ticketing_status: TicketingStatus = Field(description=_TICKETING_STATUS_TEXT)
    cancel_reason: str | None = Field(
        description=(
            "`customer`, `staff` or `expired` when `booking_status` is `cancelled`."
        )
    )
    ticketing_error: str | None = Field(
        description=("GTS's words when the ticket did not come out — the inbox's why.")
    )
    customer_id: uuid.UUID = Field(description="Who booked — `/admin/customers/{id}/`.")
    gts_order_number: int = Field(description="GTS's order number.")
    gts_status: str = Field(description="GTS's own status code, verbatim.")
    updated_at: datetime = Field(
        description=(
            "The last time anything about the order moved; "
            "`ordering=-updated_at` puts the freshest trouble first."
        )
    )

    @classmethod
    def from_order(cls, order: Order) -> "OrderAdminListItemOut":
        base = OrderListItemOut.from_order(order)
        return cls(
            **base.model_dump(),
            booking_status=OrderStatus(order.status),
            payment_status=PaymentStatus(order.payment_status),
            ticketing_status=TicketingStatus(order.ticketing_status),
            cancel_reason=order.cancel_reason,
            ticketing_error=order.ticketing_error,
            customer_id=order.customer_id,
            gts_order_number=order.gts_order_number,
            gts_status=order.gts_status,
            updated_at=order.updated_at,
        )


class OrderAdminOrderOut(OrderOut):
    """The customer's `order` block plus the three columns its `status` was
    read from — support sees both the word and the reason for it."""

    booking_status: OrderStatus = Field(description=_BOOKING_STATUS_TEXT)
    payment_status: PaymentStatus = Field(description=_PAYMENT_STATUS_TEXT)
    ticketing_status: TicketingStatus = Field(description=_TICKETING_STATUS_TEXT)

    @classmethod
    def from_public(cls, public: OrderOut, order: Order) -> "OrderAdminOrderOut":
        return cls(
            **public.model_dump(),
            booking_status=OrderStatus(order.status),
            payment_status=PaymentStatus(order.payment_status),
            ticketing_status=TicketingStatus(order.ticketing_status),
        )


class OrderAdminOut(BookingResultOut):
    """The detail for support: the customer's view plus the books behind it."""

    # The customer example does not fit a row with extra fields.
    model_config = {"json_schema_extra": {}}

    order: OrderAdminOrderOut = Field(
        description="The customer's block plus the three raw columns."
    )
    customer_id: uuid.UUID = Field(description="Who booked.")
    ticketing_attempts: int = Field(
        description="How many times the ticketing request was sent to GTS."
    )
    ticketing_requested_at: datetime | None = Field(
        description="When the ticket was last asked for."
    )
    gts_checked_at: datetime | None = Field(
        description="When the sweep last read the order back from GTS."
    )
    events: list[OrderEventOut] = Field(description="The history, oldest first.")
    payments: list[PaymentAttemptAdminOut] = Field(
        description=(
            "Every payment attempt, oldest first, without provider references."
        )
    )


__all__ = [
    "ADMIN_RECEIPT_PATH",
    "PAYMENT_VIEW_STATUSES",
    "RECEIPT_PATH",
    "TICKETED_EXAMPLE",
    "BookingResultOut",
    "OrderAdminListItemOut",
    "OrderAdminOrderOut",
    "OrderAdminOut",
    "OrderEventOut",
    "OrderListItemOut",
    "OrderMessageIn",
    "OrderMessageOut",
    "OrderOut",
    "PassengerNameOut",
    "PaymentAttemptAdminOut",
    "PaymentAttemptView",
    "PaymentConfirmIn",
    "PaymentOut",
    "PaymentResendIn",
    "PaymentStartIn",
    "PaymentViewStatus",
    "ReceiptDocument",
    "RefundIn",
    "RepriceOut",
    "strip_commission",
    "TicketOut",
    "TicketingOut",
]
