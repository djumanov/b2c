"""What the four flight search steps send and receive, for Swagger only.

The flow endpoints take a bare ``dict`` and return a bare ``dict`` on purpose:
our flight API **is** the GTS API, and a typed response model would quietly
drop every field GTS adds without asking us (API.md §20, decision of
2026-08-12). The cost is a published schema that says nothing — four identical
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
code tables (``OW``/``RT``/``MT``, ``E``/``B``/``F``), and recorded GTS answers
for the concrete examples. Nothing here is invented.
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
    status: str = "200",
) -> dict[str, Any]:
    """One step's ``openapi_extra``, assembled the same way every time.

    Only the leaves are given. FastAPI deep-merges this into the generated
    operation, so the ``{product}`` parameter and the shared error responses
    survive untouched. ``status`` must match the route's ``status_code`` —
    keyed under anything else, this blob would merge in as a *second* success
    response that never actually happens.
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
            status: {
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
        "is `In process` — poll again rather than treating it as the end. "
        "**An upstream failure is not an error here**: if GTS refuses, times "
        "out or cannot be reached, this answers `200` with `offers: []`, "
        "`count: 0` and `search_status: In process`, and the client polls "
        "again (API.md §20). Bound the polling — a permanently broken GTS "
        "never reaches `success`."
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
        "`upsell: true` by `offers/` have them. Like `offers/`, an upstream "
        "failure answers `200` with `offers: []` and `search_status: "
        "In process` instead of a `502` (API.md §20) — the chosen offer is "
        "still bookable as it stands."
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

_PASSENGER: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": True,
    "description": (
        "GTS's own passenger shape, forwarded verbatim — GTS is the validator "
        "of record for these fields."
    ),
    "required": ["type", "first_name", "last_name", "birth_date", "document"],
    "properties": {
        "type": {"type": "string", "enum": ["ADT", "CHD", "INF", "INS"]},
        "gender": {"type": "string", "enum": ["M", "F"]},
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "middle_name": {"type": "string"},
        "birth_date": {"type": "string", "format": "date"},
        "citizenship": {
            "type": "string",
            "description": "Bare ISO 3166-1 alpha-2 code, e.g. `UZ`.",
        },
        "document": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "type": {"type": "string", "description": "`PSP`, `NP`, `FA`…"},
                "number": {"type": "string"},
                "issue_date": {"type": "string", "format": "date"},
                "expire_date": {"type": "string", "format": "date"},
            },
        },
        "email": {"type": "string"},
        "phone": {
            "type": "object",
            "properties": {
                "phone_code": {"type": "string"},
                "phone_number": {"type": "string"},
            },
        },
    },
}

_MONEY: Final[dict[str, Any]] = {
    "type": "object",
    "nullable": True,
    "properties": {
        "amount": {
            "type": "string",
            "description": "A string, never a JSON number (API.md §1).",
        },
        "currency": {"type": "string"},
    },
}

FLIGHT_BOOKING: Final[dict[str, Any]] = _operation(
    status="201",
    request_schema={
        "type": "object",
        "additionalProperties": True,
        "required": ["request_id", "offer_id", "passengers"],
        "properties": {
            **_OFFER_REF_PROPERTIES,
            "passengers": {
                "type": "array",
                "minItems": 1,
                "items": _PASSENGER,
            },
        },
    },
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
                "email": "traveller@example.com",
                "phone": {"phone_code": "998", "phone_number": "901234567"},
            }
        ],
    },
    response_schema={
        "type": "object",
        "properties": {
            "product": {"type": "string"},
            "order": {
                "type": "object",
                "description": "Our record of the booking — the stable contract.",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "product": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["booked", "paid", "ticketed", "cancelled"],
                    },
                    "gts_status": {
                        "type": "string",
                        "description": "GTS's own code, verbatim — `BO` when held.",
                    },
                    "gts_order_number": {"type": "integer"},
                    "pnr": {"type": "string", "nullable": True},
                    "trip_type": {"type": "string", "nullable": True},
                    "route_summary": {"type": "string", "nullable": True},
                    "passenger_count": {"type": "integer", "nullable": True},
                    "amount": _MONEY,
                    "ticket_time_limit_at": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                    },
                    "request_id": {"type": "string"},
                    "offer_id": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"},
                },
            },
            "payment": {
                "type": "object",
                "description": (
                    "Derived, not yet a stored payment: pay this much "
                    "before `pay_before`."
                ),
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "paid", "cancelled"],
                    },
                    "amount": _MONEY,
                    "pay_before": {
                        "type": "string",
                        "format": "date-time",
                        "nullable": True,
                    },
                },
            },
            "order_data": {
                "type": "object",
                "additionalProperties": True,
                "description": (
                    "GTS's booking answer — routes, passengers, fares, baggage "
                    "— minus commission fields. Read display detail here."
                ),
            },
        },
    },
    response_example={
        "product": "flight",
        "order": {
            "id": "5f0d87c1-9c58-4bff-8f95-6a1f61f4d1f7",
            "product": "flight",
            "status": "booked",
            "gts_status": "BO",
            "gts_order_number": 61453,
            "pnr": "UBPLKW",
            "trip_type": "OW",
            "route_summary": "TAS-VKO",
            "passenger_count": 1,
            "amount": {"amount": "287500.00", "currency": "UZS"},
            "ticket_time_limit_at": "2026-08-20T06:53:12Z",
            "request_id": _REQUEST_ID,
            "offer_id": _OFFER_ID,
            "created_at": "2026-08-20T05:54:12Z",
        },
        "payment": {
            "status": "pending",
            "amount": {"amount": "287500.00", "currency": "UZS"},
            "pay_before": "2026-08-20T06:53:12Z",
        },
        "order_data": {
            "order_number": 61453,
            "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
            "status": "BO",
            "gds_pnr": "UBPLKW",
            "routes": ["…"],
            "passengers": ["…"],
        },
    },
    response_description=(
        "The seat is held and the order recorded. Pay before "
        "`payment.pay_before` or GTS releases the hold. A GTS refusal is a "
        "`502 upstream_error` with GTS's reason in `meta.upstream` and **no "
        "order is created**; a `504 upstream_timeout` means the outcome is "
        "unknown — check with support before booking again."
    ),
)


__all__ = [
    "FLIGHT_BOOKING",
    "FLIGHT_OFFERS",
    "FLIGHT_SEARCH",
    "FLIGHT_UPSELL",
    "FLIGHT_VERIFY",
]
