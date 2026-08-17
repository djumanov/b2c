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

FLIGHT_BOOKING: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id", "offer_id"],
        "description": (
            "Books the exact `offer_id` that `verify/` cleared. Only the two "
            "identifiers are checked; passengers and everything else are "
            "forwarded unchecked, because the GTS booking contract decides "
            "which fields it needs." + _UNCONFIRMED
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
                    "Store these travellers on the customer's profile for next "
                    "time (§19)."
                ),
            },
        },
    },
    # Recorded from the EASY_GATEWAY collection's ``/content/Booking`` — a
    # real call, not a composition. One adult, because that is the request
    # that was actually made; the passenger shape is the part worth copying.
    # ``save_passenger`` is ours (§19) and rides along; GTS ignores it.
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
        "additionalProperties": True,
        "description": (
            "GTS's booking answer, verbatim. **No field of ours is added** — "
            "not even the id of the order this writes. The booking is filed "
            "under the signed-in customer and read back through "
            "`GET /public/orders/` (§21); `payment_id` arrives with the saga."
            "\n\n**Mind the second `data`.** The order's own fields are one "
            "level down, under `data` — what you see here is GTS's wrapper "
            "around it." + _UNCONFIRMED
        ),
        "properties": {
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
                            "GTS's order number — **the handle `cancel/` "
                            "takes**. An integer here; `/public/orders/` "
                            "reports it as the string `gts_order_number` "
                            "(API.md §1)."
                        ),
                    },
                    "order_uid": {
                        "type": "string",
                        "description": "GTS's internal key for the same order.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["BO", "PW", "TI", "TE", "CB", "VO", "RF", "PRF"],
                        "description": (
                            "GTS's order status (GTS.md §4). A fresh booking "
                            "is `BO` — held, not ticketed."
                        ),
                    },
                    "gds_pnr": {"type": "string", "description": "Booking reference."},
                    "supplier_pnr": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "An array, not a string.",
                    },
                    "ticket_time_limit": {
                        "type": "integer",
                        "description": "Seconds until the hold lapses.",
                    },
                    "refundable": {"type": "boolean"},
                    "trip_type": _TRIP_TYPE,
                    "routes": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": True},
                        "description": "Segments as booked.",
                    },
                    "price_info": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "What was actually charged.",
                    },
                    "passengers": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": True},
                        "description": (
                            "The travellers as GTS recorded them — note the "
                            "names differ again (`firstname`, `lastname`, "
                            "`document.passport_number`)."
                        ),
                    },
                },
            },
        },
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
    },
    response_description=(
        "The seat is held by GTS and the order is recorded under the caller. "
        "Requires a signed-in customer — there is no guest purchase."
    ),
)

FLIGHT_CANCEL: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["order_number"],
        "description": (
            "`order_number` is required and is the **only** thing we read: it "
            "names the order to verify ownership against. Everything else is "
            "forwarded to GTS untouched and the body is never rebuilt — which "
            "further fields name a booking upstream is not fixed anywhere we "
            "control, and refusing a valid cancellation before GTS sees it "
            "would cost a real seat." + _UNCONFIRMED
        ),
        "properties": {
            "order_number": {
                "type": "integer",
                "description": (
                    "**GTS's** order number, from `booking/`'s `data.data."
                    "order_number` — the value `GET /public/orders/` reports "
                    "as `gts_order_number`. Not our `id`, which is a UUID and "
                    "means nothing upstream.\n\n"
                    "Missing → `422` naming this field. Belonging to another "
                    "customer, or to no order we recorded → `404`, and GTS is "
                    "not called at all (§21)."
                ),
            }
        },
    },
    # The whole of GTS's recorded cancel body (EASY_GATEWAY collection,
    # ``/content/Cancel``) — one field. Inventing a second would publish one
    # that does not exist, which is the same reason the adapter refuses to
    # rebuild this body (providers/products/flight.py).
    request_example={"order_number": 61453},
    response_schema={
        "type": "object",
        "additionalProperties": True,
        "description": (
            "GTS's answer, verbatim. ⚠ The only recorded cancel response is an "
            "older one shaped `{status, code, order}` — it has no `data` key, "
            "which our GTS client requires, so this step's answer is the least "
            "certain thing on the flow and is the first to check against live "
            "GTS (STATUS.md §8)." + _UNCONFIRMED
        ),
        "properties": {
            "status": {
                "type": "string",
                "enum": ["BO", "PW", "TI", "TE", "CB", "VO", "RF", "PRF"],
                "description": "`CB` once the booking is released (GTS.md §4).",
            },
        },
    },
    response_example={"order_number": 61453, "status": "CB"},
    response_description=(
        "The booking is released and the stored order takes GTS's new status. "
        "Only bookings GTS still holds — a ticketed order needs "
        "`void`/`refund`, which are not built yet."
    ),
)


__all__ = [
    "FLIGHT_BOOKING",
    "FLIGHT_CANCEL",
    "FLIGHT_OFFERS",
    "FLIGHT_SEARCH",
    "FLIGHT_UPSELL",
    "FLIGHT_VERIFY",
]
