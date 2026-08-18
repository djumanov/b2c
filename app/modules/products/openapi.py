"""What the six flight steps send and receive, for Swagger only.

The flow endpoints take a bare ``dict`` and return a bare ``dict`` on purpose:
our flight API **is** the GTS API, and a typed response model would quietly
drop every field GTS adds without asking us (API.md §20, decision of
2026-08-12). The cost is a published schema that says nothing — six identical
``{"type": "object"}`` blobs where a frontend developer needs field names.

So the shapes are described here and attached with ``openapi_extra``, which
touches documentation and nothing else. **Nothing in this file runs on a
request.** The real check is still the adapter's (``providers/products/flight``
``_FlightSearchIn`` and friends), and it is deliberately looser than what is
written below.

Two consequences worth stating plainly, because they look like mistakes:

* Every request schema sets ``additionalProperties: true``. That is not
  laziness — unknown fields really do pass through, and GTS's contract grows
  without waiting for us. ``properties`` here **describes**, it does not
  constrain.
* The response schemas are written at the ``data`` level, not the envelope
  level. ``api/openapi.py`` wraps them afterwards; writing the envelope here
  too would double it. The ``example`` beside each schema *is* the full
  envelope, because that is the thing the client actually parses, and that
  key is left alone by the wrapper.

Sources, in order of authority: API.md §20 for the contract, GTS.md §4 for the
code tables (``BO``/``PW``/``TI``…, ``OW``/``RT``/``MT``, ``E``/``B``/``F``),
and the recorded GTS answers in the flight tests for the concrete examples.
Nothing here is invented.
"""

from typing import Any, Final

#: GTS identifiers are opaque strings. They are echoed verbatim and never
#: parsed — today a UUID, tomorrow whatever GTS decides.
_REQUEST_ID: Final = "6c62dcec-9334-11ee-8688-5169d0acfb81"
_OFFER_ID: Final = "7cc212c0-c91d-4931-8ff6-4231b7da27c0"

#: API.md §20. Relayed verbatim from the GTS envelope's own ``status``.
_SEARCH_STATUS: Final[dict[str, Any]] = {
    "type": "string",
    "enum": ["In process", "success"],
    "description": (
        "**Ours, not GTS's data.** `In process` — providers are still "
        "answering and `offers` holds a partial result; `success` — the search "
        "is done. Keep polling until it reads `success` or `next_token` runs "
        "out."
    ),
}

#: An offer is GTS's structure and we do not model it: it differs per provider
#: and grows without notice. Named here only so the array is not anonymous.
_OFFER: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "GTS's offer object, verbatim. The shape varies by provider — read "
        "`offer_id` and pass it to `upsell`, `verify` and `booking`; render "
        "the rest as it arrives."
    ),
}

_TRIP_TYPE: Final[dict[str, Any]] = {
    "type": "string",
    "enum": ["OW", "RT", "MT"],
    "description": "One way, round trip, multi-city (GTS.md §4).",
}


def _envelope_example(data: Any) -> dict[str, Any]:
    """The body as it leaves us — ``EnvelopeRoute``'s output (API.md §2)."""
    return {"status": "success", "data": data, "errors": [], "meta": None}


def _operation(
    *,
    request_schema: dict[str, Any],
    request_example: dict[str, Any],
    response_schema: dict[str, Any],
    response_example: dict[str, Any],
    response_description: str,
) -> dict[str, Any]:
    """One step's ``openapi_extra``, assembled the same way every time.

    Only the leaves are given. FastAPI deep-merges this into the generated
    operation, so the ``{product}`` parameter and the shared error responses
    survive untouched.
    """
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": request_schema,
                    "example": request_example,
                }
            },
        },
        "responses": {
            "200": {
                "description": response_description,
                "content": {
                    "application/json": {
                        # Becomes ``data`` — api/openapi.py adds the envelope.
                        "schema": response_schema,
                        # Untouched by that wrapper, so it shows the real body.
                        "example": _envelope_example(response_example),
                    }
                },
            }
        },
    }


# --- search ---------------------------------------------------------------------------

_DIRECTION: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": True,
    "required": ["departure", "arrival", "departure_date"],
    "properties": {
        "departure": {
            "type": "string",
            "pattern": "^[A-Za-z]{3}$",
            "description": "IATA code of the origin city or airport, e.g. `TAS`.",
        },
        "arrival": {
            "type": "string",
            "pattern": "^[A-Za-z]{3}$",
            "description": "IATA code of the destination, e.g. `IST`.",
        },
        "departure_date": {
            "type": "string",
            "format": "date",
            "description": "`YYYY-MM-DD`.",
        },
    },
}

FLIGHT_SEARCH: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["directions", "adt"],
        "description": (
            "GTS's own search body, forwarded verbatim. Unlisted fields "
            "(`passengers_ids`, anything GTS adds later) pass straight "
            "through."
        ),
        "properties": {
            "directions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": _DIRECTION,
                "description": "One entry per leg: 1 = one way, 2 = round trip.",
            },
            "adt": {
                "type": "integer",
                "minimum": 1,
                "maximum": 9,
                "description": "Adults. At least one.",
            },
            "chd": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9,
                "default": 0,
                "description": "Children.",
            },
            "inf": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9,
                "default": 0,
                "description": "Infants without a seat.",
            },
            "ins": {
                "type": "integer",
                "minimum": 0,
                "maximum": 9,
                "default": 0,
                "description": "Infants with a seat.",
            },
            "class": {
                "type": "string",
                "enum": ["E", "B", "F"],
                "description": "Economy, business, first (GTS.md §4).",
            },
            "direct": {"type": "boolean", "description": "Non-stop flights only."},
            "flexible": {
                "type": "boolean",
                "description": "Also look at neighbouring dates.",
            },
            "airlines": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Restrict to these airline codes, e.g. `["HY"]`.',
            },
        },
    },
    request_example={
        "directions": [
            {"departure": "TAS", "arrival": "IST", "departure_date": "2026-09-14"}
        ],
        "adt": 1,
        "chd": 0,
        "inf": 0,
        "ins": 0,
        "class": "E",
        "direct": False,
    },
    response_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id"],
        "properties": {
            "request_id": {
                "type": "string",
                "description": (
                    "**GTS's** search identifier. Opaque — keep it verbatim and "
                    "send it with every later step. Nothing about the search is "
                    "stored on our side, so this is the only handle on it."
                ),
            },
            "fun_fact": {
                "type": "string",
                "nullable": True,
                "description": (
                    "**Ours, not GTS's.** A random published fact (§30) in the "
                    "requested language, to show while the client polls "
                    "`offers`. `null` when nothing is published. Flight only."
                ),
            },
        },
    },
    response_example={
        "request_id": _REQUEST_ID,
        "fun_fact": "Boeing 747 qanotida 6 million detal bor.",
    },
    response_description=(
        "The search has started. Offers arrive from `offers/`, not here — "
        "providers are still being asked."
    ),
)


# --- offers ---------------------------------------------------------------------------

FLIGHT_OFFERS: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id"],
        "description": (
            "Paging is GTS's. `sort_type`, `limit`, `next_token` and `currency` "
            "are forwarded verbatim, so **only what GTS supports works** — "
            "there is no sorting or filtering of ours on top."
        ),
        "properties": {
            "request_id": {
                "type": "string",
                "description": "The `request_id` returned by `search/`.",
            },
            "next_token": {
                "type": "string",
                "nullable": True,
                "description": "Cursor from the previous page; `null` for the first.",
            },
            "sort_type": {
                "type": "string",
                "description": "GTS sort key, e.g. `price`.",
            },
            "limit": {"type": "integer", "description": "Offers per page."},
            "currency": {
                "type": "string",
                "description": "Currency to price in, e.g. `UZS`. GTS converts.",
            },
        },
    },
    request_example={
        "request_id": _REQUEST_ID,
        "next_token": None,
        "sort_type": "price",
        "limit": 20,
        "currency": "UZS",
    },
    response_schema={
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "search_status": _SEARCH_STATUS,
            "request_id": {"type": "string"},
            "next_token": {
                "type": "string",
                "nullable": True,
                "description": "Cursor for the next page; `null` when exhausted.",
            },
            "count": {"type": "integer", "description": "Offers found so far."},
            "trip_type": _TRIP_TYPE,
            "offers": {"type": "array", "items": _OFFER},
        },
    },
    response_example={
        "search_status": "In process",
        "request_id": _REQUEST_ID,
        "next_token": "c27b666f",
        "count": 41,
        "trip_type": "RT",
        "offers": [{"offer_id": _OFFER_ID, "price_info": {"price": 221.86}}],
    },
    response_description=(
        "One page of offers. A partial result is normal while `search_status` "
        "is `In process` — poll again rather than treating it as the end."
    ),
)


# --- upsell and verify ----------------------------------------------------------------

#: ``upsell``, ``verify`` and ``booking`` all name one offer of one search.
_OFFER_REF_PROPERTIES: Final[dict[str, Any]] = {
    "request_id": {
        "type": "string",
        "description": "The `request_id` returned by `search/`.",
    },
    "offer_id": {
        "type": "string",
        "description": (
            "The chosen offer. After `upsell/` this is the **branded fare's** "
            "id, not the original one."
        ),
    },
}

FLIGHT_UPSELL: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id", "offer_id"],
        "properties": _OFFER_REF_PROPERTIES,
    },
    request_example={"request_id": _REQUEST_ID, "offer_id": _OFFER_ID},
    response_schema={
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "search_status": _SEARCH_STATUS,
            "request_id": {"type": "string"},
            "status": {
                "type": "string",
                "description": "GTS's **own** field inside `data` — not ours.",
            },
            "code": {
                "type": "string",
                "description": "GTS's own result code, e.g. `100`.",
            },
            "trip_type": _TRIP_TYPE,
            "currency": {"type": "string"},
            "offers": {
                "type": "array",
                "items": _OFFER,
                "description": (
                    "The fare variants. The route is unchanged; each variant "
                    "carries a **new `offer_id`**, and that is the one to send "
                    "to `verify/` and `booking/`."
                ),
            },
        },
    },
    response_example={
        "search_status": "success",
        "request_id": _REQUEST_ID,
        "status": "success",
        "code": "100",
        "trip_type": "OW",
        "currency": "USD",
        "offers": [
            {"offer_id": "u-1", "price_info": {"price": 108.67}},
            {"offer_id": "u-2", "price_info": {"price": 158.67}},
        ],
    },
    response_description=(
        "Branded fares for the chosen offer. Only offers flagged "
        "`upsell: true` by `offers/` have them."
    ),
)

FLIGHT_VERIFY: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id", "offer_id"],
        "properties": _OFFER_REF_PROPERTIES,
    },
    request_example={"request_id": _REQUEST_ID, "offer_id": _OFFER_ID},
    response_schema={
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "search_status": _SEARCH_STATUS,
            "status": {"type": "string", "description": "GTS's own field."},
            "request_id": {"type": "string"},
            "offer_id": {"type": "string"},
            "code": {"type": "string"},
            "verified": {
                "type": "boolean",
                "description": "The price and seats still stand — book now.",
            },
        },
    },
    response_example={
        "search_status": "success",
        "status": "success",
        "request_id": _REQUEST_ID,
        "offer_id": _OFFER_ID,
        "code": "100",
        "verified": True,
    },
    response_description=(
        "Price and availability re-checked. An offer that has expired comes "
        "back as `502 upstream_error` with GTS's reason in `meta.upstream` — "
        "start the search again."
    ),
)


# --- booking and cancel ---------------------------------------------------------------

#: Written from the recorded GTS answers in the flight tests. Live GTS has not
#: confirmed these two yet, which is why every field is optional and the
#: descriptions say so rather than implying certainty.
_UNCONFIRMED: Final = (
    " ⚠ Not yet confirmed against live GTS — treat the field list as "
    "indicative and read whatever arrives."
)

#: The document block GTS's booking body nests the passport into. Its names
#: are not §19's — the client transforms rather than copies (API.md §20).
_DOCUMENT: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "The traveller's document, **nested** — not the flat "
        "`document_type`/`document_number` of the saved passenger (§19)."
    ),
    "properties": {
        "type": {
            "type": "string",
            "description": (
                "Document type **code** only, e.g. `PSP`, `NP`, `FA` — the "
                "`type` key of the §26 `document-types/` object, not the "
                "whole object."
            ),
        },
        "number": {"type": "string", "description": "§19's `document_number`."},
        "issue_date": {
            "type": "string",
            "format": "date",
            "description": (
                "`YYYY-MM-DD`. **Not stored by us** — §19 has no issue date, "
                "so the booking form asks for it (PROJECT.md §13)."
            ),
        },
        "expire_date": {
            "type": "string",
            "format": "date",
            "description": "§19's `document_expiry_date`, renamed.",
        },
    },
}

#: One traveller, in **GTS's** booking shape — recorded from the EASY_GATEWAY
#: collection's ``/content/Booking``, not inferred. It is deliberately *not*
#: §19's saved passenger: the client transforms one into the other, and the
#: differences are called out field by field below because they are the part
#: that goes wrong silently.
_PASSENGER: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "One traveller, in GTS's own shape. **Not the same shape as the saved "
        "passenger of §19** — `citizenship` is a bare code here, the document "
        "fields are nested, and `gender`, `document.issue_date`, `email` and "
        "`phone` have no saved counterpart at all, so the booking form asks "
        "for them (API.md §20). **Nothing here is validated by us**: GTS's "
        "booking contract decides what it needs." + _UNCONFIRMED
    ),
    "properties": {
        "type": {
            "type": "string",
            "enum": ["ADT", "CHD", "INF", "INS"],
            "description": (
                "Traveller category. The mix must match the "
                "`adt`/`chd`/`inf`/`ins` the search was started with."
            ),
        },
        "gender": {
            "type": "string",
            "enum": ["M", "F"],
            "description": (
                "**Not stored by us** — gender is absent from PROJECT.md §13's "
                "inventory, so the booking form asks for it."
            ),
        },
        "first_name": {"type": "string", "description": "As in the document."},
        "last_name": {"type": "string", "description": "As in the document."},
        "middle_name": {
            "type": "string",
            "description": "Optional — a foreign passport often has none (§19).",
        },
        "birth_date": {
            "type": "string",
            "format": "date",
            "description": "`YYYY-MM-DD`.",
        },
        "citizenship": {
            "type": "string",
            "description": (
                "ISO 3166-1 alpha-2 **code only**, e.g. `UZ` — the `code` key "
                "of the §26 `countries/` object, not the whole object §19 "
                "stores."
            ),
        },
        "document": _DOCUMENT,
        "email": {
            "type": "string",
            "format": "email",
            "description": "Per traveller, not per order. Not stored by us.",
        },
        "phone": {
            "type": "object",
            "additionalProperties": True,
            "description": "Per traveller. Not stored by us.",
            "properties": {
                "phone_code": {"type": "string", "description": "e.g. `998`."},
                "phone_number": {"type": "string"},
            },
        },
    },
}

#: One traveller **as we store and republish them** — ``order.passengers``.
#: Deliberately not ``_PASSENGER``: that is the shape GTS is *sent*, this is
#: the shape that comes back. Same name on the wire, different object, and
#: the differences are the ones a client gets wrong — ``phone`` is one string
#: here, and four fields arrive that were never sent.
_ORDER_PASSENGER: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "One traveller in **our** shape (order-system/03-design.md `O10`) — "
        "**not** the object the request sends under the same name. Every field "
        "may be `null`: a provider that omits a middle name must not cost us "
        "the traveller."
    ),
    "properties": {
        "position": {
            "type": "integer",
            "description": "1-based, in the order the travellers were sent.",
        },
        "type": {"type": "string", "description": "`ADT` · `CHD` · `INF` · `INS`."},
        "first_name": {"type": "string", "nullable": True},
        "last_name": {"type": "string", "nullable": True},
        "middle_name": {"type": "string", "nullable": True},
        "birth_date": {
            "type": "string",
            "nullable": True,
            "description": "`YYYY-MM-DD`.",
        },
        "gender": {"type": "string", "nullable": True},
        "citizenship": {"type": "string", "nullable": True, "description": "ISO code."},
        "document": {
            "type": "object",
            "description": "The same four keys the request nests.",
            "properties": {
                "type": {"type": "string", "nullable": True},
                "number": {"type": "string", "nullable": True},
                "issue_date": {"type": "string", "nullable": True},
                "expire_date": {"type": "string", "nullable": True},
            },
        },
        "email": {"type": "string", "nullable": True},
        "phone": {
            "type": "string",
            "nullable": True,
            "description": (
                "**One string**, not the `{phone_code, phone_number}` object "
                "the request sends — the two halves are joined."
            ),
        },
        "provider_traveler_id": {
            "type": "string",
            "nullable": True,
            "description": (
                "GTS's own id for this traveller. What their support asks for."
            ),
        },
        "ticket_number": {
            "type": "string",
            "nullable": True,
            "description": "**`null` here.** Ticketing fills it, after payment.",
        },
        "anonymized_at": {
            "type": "string",
            "format": "date-time",
            "nullable": True,
            "description": (
                "Set when the account is deleted and the traveller is scrubbed "
                "(PROJECT.md §13). `null` on a live order."
            ),
        },
    },
}

#: What the booking answer's ``order`` half names. Written out rather than left
#: an opaque object because this is the one endpoint where a client meets the
#: order for the first time, and ``id`` is the handle every later call takes.
_BOOKED_ORDER: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "The order, in the same shape `GET /public/orders/{id}/` uses "
        "(API.md §21). **Keep `id`** — paying, cancelling and polling all "
        "take it."
    ),
    "properties": {
        "id": {
            "type": "string",
            "format": "uuid",
            "description": (
                "**Ours.** The handle for `POST /public/orders/{id}/"
                "transactions/`, `POST /public/orders/{id}/cancel/` and "
                "`GET /public/orders/{id}/`. Not GTS's order number."
            ),
        },
        "order_no": {
            "type": "string",
            "description": "Human-readable: `B2C-2608-000123`. What support asks for.",
        },
        "product": {"type": "string", "description": "The vertical, e.g. `flight`."},
        "status": {
            "type": "string",
            "enum": [
                "created",
                "booked",
                "paid",
                "ticketing",
                "ticketed",
                "refunding",
                "refunded",
                "partially_refunded",
                "cancelled",
                "voided",
                "failed",
                "needs_attention",
            ],
            "description": (
                "**Canonical, ours** (order-system/03-design.md §3.3). A "
                "booking normally answers `booked`. `needs_attention` means "
                "GTS agreed in words we could not read — a seat is probably "
                "held, so **do not book again**; poll the order instead."
            ),
        },
        "gts_status": {
            "type": "string",
            "nullable": True,
            "description": (
                "GTS's own code, verbatim — `BO`, `PW`, `TI`, `CB`, … "
                "(GTS.md §4). Informational; decide on `status`."
            ),
        },
        "gts_order_number": {
            "type": "string",
            "nullable": True,
            "description": (
                "GTS's order number as a **string** (an integer upstream — "
                "API.md §1 keeps identifiers textual). **What `cancel/` acts "
                "on**, though it takes the order's own `id`."
            ),
        },
        "gts_order_uid": {
            "type": "string",
            "nullable": True,
            "description": "GTS's internal key. Nothing we expose takes it.",
        },
        "gts_pnr": {
            "type": "string",
            "nullable": True,
            "description": "The airline record locator (`gds_pnr`).",
        },
        "request_id": {"type": "string", "nullable": True},
        "offer_id": {"type": "string", "nullable": True},
        "amount": {
            "type": "object",
            "nullable": True,
            "description": (
                'What the customer owes: `{"amount": "52.39", "currency": '
                '"EUR"}` — the amount a **string** (API.md §1). `null` only '
                "if the provider has not priced the order."
            ),
            "properties": {
                "amount": {"type": "string"},
                "currency": {"type": "string", "minLength": 3, "maxLength": 3},
            },
        },
        "passengers": {
            "type": "array",
            "items": _ORDER_PASSENGER,
            "description": (
                "Travellers in **our** shape, not GTS's — read back from the "
                "answer where it lists them, from the request otherwise. The "
                "request's `passengers` is a different object with the same "
                "name; transform, do not copy."
            ),
        },
        "ticket_time_limit_at": {
            "type": "string",
            "format": "date-time",
            "nullable": True,
            "description": (
                "**The deadline.** GTS holds the seat until this moment; an "
                "unpaid order lapses on its own afterwards. Send the customer "
                "to payment straight away."
            ),
        },
        "travel_start_at": {
            "type": "string",
            "format": "date-time",
            "nullable": True,
            "description": "Departure — for the list row, nothing depends on it.",
        },
        "route_summary": {
            "type": "string",
            "nullable": True,
            "description": "`TAS → IST`, for display.",
        },
        "cancellation_reason": {"type": "string", "nullable": True},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "booked_at": {"type": "string", "format": "date-time", "nullable": True},
        "paid_at": {"type": "string", "format": "date-time", "nullable": True},
        "ticketed_at": {"type": "string", "format": "date-time", "nullable": True},
        "cancelled_at": {"type": "string", "format": "date-time", "nullable": True},
        "data": {
            "type": "object",
            "nullable": True,
            "additionalProperties": True,
            "description": (
                "The provider's answer — **the same content as the `message` / "
                "`request_id` / `data` keys beside `order`**. Read either."
            ),
        },
    },
}

#: What is owed and until when. Derived from the order, not a stored record:
#: one order carries one amount, and the attempts at paying it live in
#: ``/public/orders/{id}/transactions/`` (API.md §22).
_PAYMENT_STATE: Final[dict[str, Any]] = {
    "type": "object",
    "description": (
        "What the customer still owes, and until when. Derived from the "
        "order — it is not a payment record. Starting a payment is a separate "
        "call: `POST /public/orders/{id}/transactions/` (API.md §22)."
    ),
    "properties": {
        "status": {
            "type": "string",
            "enum": ["pending", "paid"],
            "description": "`pending` until the full amount has settled.",
        },
        "amount": {
            "type": "object",
            "nullable": True,
            "description": "The same money as `order.amount`.",
            "properties": {
                "amount": {"type": "string"},
                "currency": {"type": "string", "minLength": 3, "maxLength": 3},
            },
        },
        "pay_before": {
            "type": "string",
            "format": "date-time",
            "nullable": True,
            "description": (
                "`order.ticket_time_limit_at`, unshortened. `null` when the "
                "provider named no deadline."
            ),
        },
    },
    "required": ["status", "amount", "pay_before"],
}

FLIGHT_BOOKING: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id", "offer_id"],
        "description": (
            "Books the exact `offer_id` that `verify/` cleared — after "
            "`upsell/`, the **branded fare's** id rather than the original."
            "\n\n**One mandatory header:** `Authorization: Bearer …` for a "
            "customer token (`aud: public`; an admin token answers `403`). "
            "`Idempotency-Key` is **optional** — leave it out and the server "
            "derives one from the request, so an identical repeat never books "
            "twice (API.md §10)."
            "\n\n**Only the two identifiers are checked by us.** Everything "
            "else — passengers included — is forwarded unchecked, because the "
            "GTS booking contract decides which fields it needs, and a "
            "mismatch comes back as `502` with GTS's own message." + _UNCONFIRMED
        ),
        "properties": {
            **_OFFER_REF_PROPERTIES,
            "passengers": {
                "type": "array",
                "items": _PASSENGER,
                "description": (
                    "One entry per traveller. The count and mix must match the "
                    "`adt`/`chd`/`inf`/`ins` the search was started with — GTS "
                    "checks that, we do not."
                ),
            },
            "save_passenger": {
                "type": "boolean",
                "description": (
                    "**Ours, not GTS's** (§19). `true` adds the travellers to "
                    "the customer's saved passengers once the booking is "
                    "confirmed — a refused, unanswered or unreadable booking "
                    "saves nobody. Somebody already on the list is not added "
                    "twice, a traveller with no birth date is skipped, and "
                    "none of it can fail the booking. Strictly boolean: the "
                    'string `"true"` is not a yes.'
                ),
            },
        },
    },
    # Recorded from the EASY_GATEWAY collection's ``/content/Booking`` — a
    # real call, not a composition. One adult, because that is the request
    # that was actually made; the passenger shape is the part worth copying.
    request_example={
        "request_id": _REQUEST_ID,
        "offer_id": _OFFER_ID,
        "passengers": [
            {
                "type": "ADT",
                "gender": "M",
                "first_name": "Azimjon",
                "last_name": "Yusufov",
                "middle_name": "Kamoliddin",
                "birth_date": "2002-12-20",
                "citizenship": "UZ",
                "document": {
                    "type": "PSP",
                    "number": "FA2145157",
                    "issue_date": "2019-05-30",
                    "expire_date": "2029-05-29",
                },
                "email": "yusufovazimjon@gmail.com",
                "phone": {"phone_code": "998", "phone_number": "998328192"},
            }
        ],
        "save_passenger": True,
    },
    response_schema={
        "type": "object",
        "description": (
            "**GTS's booking answer, plus two keys of ours.** Everything GTS "
            "sends stays exactly where it was — `message`, `request_id` and "
            "`data` are at this level — and `order` and `payment` arrive "
            "beside them. Adding a field is not a breaking change; moving "
            "every existing one a level down is, which is why they did not "
            "move (API.md §1)."
            "\n\n**Mind the second `data`.** The order's own fields are one "
            "level down, under `data` — that nesting is GTS's, not ours. "
            "There is no `search_status` here: booking has no "
            '"In process" state.'
            "\n\n`order` is the record this booking wrote, in the shape "
            "`GET /public/orders/{id}/` uses (§21); its `id` is what every "
            "later call takes. `payment` says what is owed and until when. "
            "Both are always present — on a replay whose first attempt is "
            "still in flight, GTS's keys are the ones missing."
            "\n\n**Next call:** `POST /public/orders/{id}/transactions/` "
            "(§22). An unpaid order lapses at `order.ticket_time_limit_at`."
            "\n\n`order` and `payment` are ours and settled." + _UNCONFIRMED
        ),
        "properties": {
            "order": _BOOKED_ORDER,
            "payment": _PAYMENT_STATE,
            "message": {
                "type": "string",
                "description": "GTS's word for the outcome, e.g. `booked`.",
            },
            "request_id": {
                "type": "string",
                "description": "The search this order came from, echoed back.",
            },
            "data": {
                "type": "object",
                "additionalProperties": True,
                "description": "**The order itself.**",
                "properties": {
                    "order_number": {
                        "type": "integer",
                        "description": (
                            "GTS's order number. An integer here; we "
                            "publish it as the string "
                            "`order.gts_order_number` (API.md §1)."
                        ),
                    },
                    "order_uid": {
                        "type": "string",
                        "description": "GTS's internal key for the same order.",
                    },
                    "status": {
                        "type": "string",
                        "enum": [
                            "BO",
                            "PW",
                            "TI",
                            "TE",
                            "CB",
                            "VO",
                            "RF",
                            "PRF",
                        ],
                        "description": (
                            "GTS's order status (GTS.md §4). A fresh booking "
                            "is `BO` — held, not ticketed."
                        ),
                    },
                    "gds_pnr": {
                        "type": "string",
                        "description": "Booking reference.",
                    },
                    "supplier_pnr": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "An array, not a string.",
                    },
                    "ticket_time_limit": {
                        "type": "integer",
                        "description": (
                            "How long the hold lasts. Read it from "
                            "`order.ticket_time_limit_at` instead — the "
                            "unit here is not documented and three "
                            "spellings have been seen "
                            "(order-system/03-design.md Q1)."
                        ),
                    },
                    "refundable": {"type": "boolean"},
                    "trip_type": _TRIP_TYPE,
                    "routes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                        "description": "Segments as booked.",
                    },
                    "price_info": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "What was actually charged.",
                    },
                    "passengers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                        "description": (
                            "The travellers as GTS recorded them — note the "
                            "names differ again (`firstname`, `lastname`, "
                            "`document.passport_number`). Read "
                            "`order.passengers` instead unless you need "
                            "something only GTS carries."
                        ),
                    },
                },
            },
        },
        "required": ["order", "payment"],
    },
    # Trimmed to the fields worth naming; the recorded answer also carries
    # routes, fares, baggage and supplier keys, all of which pass through.
    response_example={
        "message": "booked",
        "request_id": _REQUEST_ID,
        "data": {
            "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
            "order_number": 61453,
            "status": "BO",
            "gds_pnr": "UBPLKW",
            "supplier_pnr": ["UBPLKW"],
            "trip_type": "OW",
            "refundable": False,
            "ticket_time_limit": 288000,
            "price_info": {"price": 46.89, "currency": "EUR", "fee_amount": 5.5},
        },
        "order": {
            "id": "3f1c5f6e-2c0a-4f5e-9a7d-1b2c3d4e5f60",
            "order_no": "B2C-2608-000123",
            "product": "flight",
            "status": "booked",
            "gts_status": "BO",
            "gts_order_number": "61453",
            "gts_order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
            "gts_pnr": "UBPLKW",
            "amount": {"amount": "52.39", "currency": "EUR"},
            "passengers": [
                {
                    "position": 1,
                    "type": "ADT",
                    "first_name": "Azimjon",
                    "last_name": "Yusufov",
                    "middle_name": "Kamoliddin",
                    "birth_date": "2002-12-20",
                    "gender": "M",
                    "citizenship": "UZ",
                    "document": {
                        "type": "PSP",
                        "number": "FA2145157",
                        "issue_date": "2019-05-30",
                        "expire_date": "2029-05-29",
                    },
                    "email": "yusufovazimjon@gmail.com",
                    "phone": "998998328192",
                    "provider_traveler_id": "1",
                    "ticket_number": None,
                    "anonymized_at": None,
                }
            ],
            "ticket_time_limit_at": "2026-08-21T09:14:22Z",
            "route_summary": "TAS → IST",
            "booked_at": "2026-08-18T09:14:22Z",
            "paid_at": None,
            "ticketed_at": None,
        },
        "payment": {
            "status": "pending",
            "amount": {"amount": "52.39", "currency": "EUR"},
            "pay_before": "2026-08-21T09:14:22Z",
        },
    },
    response_description=(
        "The seat is held by GTS and the order is recorded under the caller. "
        "Requires a signed-in customer — there is no guest purchase — and is "
        "idempotent whether or not the caller supplies a key."
    ),
)

__all__ = [
    "FLIGHT_BOOKING",
    "FLIGHT_OFFERS",
    "FLIGHT_SEARCH",
    "FLIGHT_UPSELL",
    "FLIGHT_VERIFY",
]
