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
                "items": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": "Passenger fields as in §19.",
                },
                "description": (
                    "One entry per traveller. Not validated here — GTS "
                    "answers if something is missing."
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
    request_example={
        "request_id": _REQUEST_ID,
        "offer_id": _OFFER_ID,
        "passengers": [
            {
                "first_name": "ALI",
                "last_name": "VALIYEV",
                "birth_date": "1990-04-02",
                "document_number": "AA1234567",
            }
        ],
        "save_passenger": True,
    },
    response_schema={
        "type": "object",
        "additionalProperties": True,
        "description": (
            "GTS's booking answer, verbatim. **No field of ours is added** — "
            "no `payment_id` and no order of ours, because nothing is stored "
            "yet; that arrives with the saga." + _UNCONFIRMED
        ),
        "properties": {
            "order_id": {
                "type": "string",
                "description": "**GTS's** order number — the handle for `cancel/`.",
            },
            "pnr": {"type": "string", "description": "Booking reference."},
            "status": {
                "type": "string",
                "enum": ["BO", "PW", "TI", "TE", "CB", "VO", "RF", "PRF"],
                "description": (
                    "GTS's order status (GTS.md §4). A fresh booking is `BO` — "
                    "held, not ticketed."
                ),
            },
            "total": {
                "type": "object",
                "additionalProperties": True,
                "description": "Price actually booked, e.g. "
                '`{"amount": "221.86", "currency": "USD"}`.',
            },
        },
    },
    response_example={
        "order_id": "1250",
        "pnr": "ABCDEF",
        "status": "BO",
        "total": {"amount": "221.86", "currency": "USD"},
    },
    response_description=(
        "The seat is held by GTS. Requires a signed-in customer — there is no "
        "guest purchase."
    ),
)

FLIGHT_CANCEL: Final[dict[str, Any]] = _operation(
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "description": (
            "Forwarded to GTS **without any check** — the one step with no "
            "shape validation. Which field names the booking is not fixed "
            "anywhere we control, and refusing a valid cancellation before GTS "
            "sees it would cost a real seat." + _UNCONFIRMED
        ),
        "properties": {
            "order_id": {
                "type": "string",
                "description": "The `order_id` that `booking/` returned.",
            }
        },
    },
    request_example={"order_id": "1250"},
    response_schema={
        "type": "object",
        "additionalProperties": True,
        "description": "GTS's answer, verbatim." + _UNCONFIRMED,
        "properties": {
            "order_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["BO", "PW", "TI", "TE", "CB", "VO", "RF", "PRF"],
                "description": "`CB` once the booking is released (GTS.md §4).",
            },
        },
    },
    response_example={"order_id": "1250", "status": "CB"},
    response_description=(
        "The booking is released. Only bookings GTS still holds — a ticketed "
        "order needs `void`/`refund`, which are not built yet."
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
