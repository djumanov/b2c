# ruff: noqa: E501 — markdown tables and JSON samples do not wrap.
"""The words at the top of Swagger: the introduction and the tag groups.

Kept apart from ``main.py`` because it is prose, and prose is long. What is
written here is what a frontend or mobile developer reads before the first
request — the conventions every endpoint shares, so no endpoint has to
repeat them. ``build_openapi`` puts the security schemes, the envelope and
the error catalogue into the schema itself; this is the explanation.
"""

from typing import Any, Final

API_DESCRIPTION: Final = """
White-label travel platform: flights today, other verticals behind the same
flow. Two surfaces share one contract:

* **`/api/v1/public/*`** — the customer app and website. Authenticate with a
  **customer token** (`customerToken`, `aud: public`) from
  `POST /public/auth/login/`.
* **`/api/v1/admin/*`** — the operator's panel. Authenticate with a **staff
  token** (`staffToken`, `aud: admin`) from `POST /admin/auth/login/`.
  Roles: `owner` and `admin`; routes marked **Owner role only** refuse `admin`.

Send the token as `Authorization: Bearer <access token>`, or use the
**Authorize** button above with whichever scheme the route shows. A valid
token on the wrong surface is a **403**, not a 401.

### Every response is an envelope

```json
{"status": "success", "data": {…}, "errors": [], "meta": null}
{"status": "error",   "data": null, "errors": [{"code": "validation", "field": "card.number", "message": "…"}], "meta": null}
```

List endpoints put the rows in `data` and the paging in `meta`:
`{"page": 1, "page_size": 20, "total": 57, "total_pages": 3}`. `DELETE`
answers `204` with no body. Webhooks (provider callbacks) are the one
exception and answer in the provider's own shape.

### Errors

| `code` | HTTP | When |
|---|---|---|
| `validation` | 422 | body, query or header refused — `field` names the culprit |
| `unauthorized` | 401 | no, expired or malformed token |
| `forbidden` | 403 | other surface's token, or a role below the route's |
| `not_found` | 404 | no such resource — or not yours (same answer on purpose) |
| `conflict` | 409 | the resource is not in a state that allows this |
| `offer_expired` | 409 | GTS released the hold; the order is `cancelled` |
| `rate_limited` | 429 | too many requests — `Retry-After` says how long |
| `upstream_error` | 502 | GTS or the payment provider refused — their words in `meta.upstream` |
| `upstream_timeout` | 504 | GTS or the provider did not answer; read the resource back before retrying |
| `internal` | 500 | our fault — quote `X-Request-Id` to support |

### Conventions

* **Every path ends with `/`.** There are no redirects: `/public/orders`
  without the slash is a 404.
* **Money is a string**: `{"amount": "287500.00", "currency": "UZS"}` —
  always two decimals, never a JSON number; `currency` is an upper-case
  ISO 4217 code.
* **Timestamps** are ISO 8601 in UTC (`2026-08-21T10:15:00Z`). Dates sent
  without an offset are read as UTC.
* **Ids** are UUIDs. Every response carries an `X-Request-Id` header.

### Lists

`page` (1-based) and `page_size` (1–100, default 20); `search` — a
case-insensitive substring over the columns each endpoint names;
`ordering` — a field name, `-` prefix for descending, from the endpoint's
own whitelist (anything else is a 422 listing the choices);
`created_from` / `created_to` — inclusive.

### Idempotency

`POST` routes that book or pay accept an optional `Idempotency-Key` header.
Without one the server derives a key from the caller, the path and the
body, so an accidental double tap never charges twice; an identical request
within 24 hours replays the stored answer, one still in flight is a `409`,
and the same key with a different body is a `422`. Send your own key to
start a genuinely new attempt with the same body.

### Languages

Translated fields follow `?lang=` first, then `Accept-Language`, then the
site's default. Supported: `uz`, `ru`, `en`; unknown values are ignored,
never refused.

### Rate limits

Per caller, per minute: sign-in 5, search 30, **payment 10**, other public
routes 120, admin routes 300. A refused request is a `429` with
`Retry-After`.
"""

#: Tag groups, in the order they appear. Every tag a router uses is listed
#: here — a contract test checks — so no group is ever a bare name.
TAGS: Final[list[dict[str, Any]]] = [
    {
        "name": "public-auth",
        "description": (
            "Customer sign-up with an emailed code, sign-in, social sign-in, "
            "token refresh and password reset."
        ),
    },
    {
        "name": "public-profile",
        "description": (
            "The signed-in customer: own details, password, saved passengers, "
            "account deletion."
        ),
    },
    {
        "name": "saved-cards",
        "description": (
            "Cards the customer keeps for next time. Saving is local — no "
            "charge, no code — and the number is sealed at rest and never "
            "returned; what comes back is the masked number, the last four "
            "digits and the brand. Pay with one by sending its `card_id`."
        ),
    },
    {
        "name": "products",
        "description": (
            "The booking flow, one shape for every vertical: `search/` → "
            "`offers/` (poll while `search_status` is `In process`) → "
            "`verify/` → `booking/`. Booking answers `201` with the order; "
            "nothing is charged yet — pay through **orders**."
        ),
    },
    {
        "name": "orders",
        "description": (
            "The customer's bookings, the price step and the payment. "
            "GTS prices a held booking again before it will ticket it, so "
            "paying starts with the price: `POST …/reprice/` asks GTS what "
            "the order costs today and hands its answer through as is — a "
            "question, nothing changes; `POST …/reprice/confirm/` is the "
            "customer accepting it — GTS confirms if the price moved (it "
            "refuses to confirm one that did not, and then the order's own "
            "price is the confirmed one), the order is re-read and "
            "everything (`order.amount`, `payment.amount`, `order_data`) "
            "now says the confirmed price. Then `POST …/payment/` registers "
            "the card with the chosen `method` (a `code` from site-config "
            "`payment_methods`) and sends the cardholder a code "
            "(`payment.status = awaiting_otp`) — refused until the price is "
            "confirmed; `POST …/payment/resend/` sends the same code again; "
            "`POST …/payment/confirm/` charges with it, and on `paid` the "
            "ticket is requested from GTS in the same call. "
            '`GET …/{id}/` is what the "is my ticket ready?" screen polls; '
            "it never writes. `order.status` is one of six words and "
            "`order.message` the sentence to show for it."
        ),
    },
    {
        "name": "site-config",
        "description": "Public branding, languages, currencies and enabled payment methods.",
    },
    {
        "name": "content",
        "description": "Published FAQ and the legal/about pages, in markdown.",
    },
    {
        "name": "catalog",
        "description": (
            "Reference data for the forms: airport autocomplete, countries "
            "with phone codes, passenger document types."
        ),
    },
    {
        "name": "leads",
        "description": "Messages from the site's contact form, topics and support contacts.",
    },
    {
        "name": "admin-auth",
        "description": "Staff sign-in and token refresh.",
    },
    {
        "name": "admin-orders",
        "description": (
            "The support desk. Every order with the customer's `status` and the "
            "three columns behind it (`booking_status`, `payment_status`, "
            "`ticketing_status`); `status=ticketing_failed` is the inbox — "
            "money taken, no ticket, a human must act. Actions are bookkeeping "
            "steps, each written to the order's history: mark where a refund "
            "stands (the money moves in the provider's cabinet), sync with GTS "
            "and the provider, ask GTS for the ticket again. `messages/` holds "
            "the sentence customers see for each status, per language."
        ),
    },
    {
        "name": "integrations",
        "description": (
            "Credentials and settings for the outside world: GTS accounts, the "
            "payment providers (each declares the fields its adapter reads; "
            "secrets come back masked; a test button checks the stored settings "
            "without moving money), social sign-in and email."
        ),
    },
    {
        "name": "settings",
        "description": "Branding, languages, currencies, timezone and the other site-wide settings.",
    },
    {"name": "cms", "description": "Pages, FAQ and fun facts — the editing side."},
    {"name": "customers", "description": "Customer accounts as the panel sees them."},
    {"name": "staff", "description": "Staff accounts and roles."},
    {
        "name": "uploads",
        "description": "Images and files referenced by settings and content.",
    },
    {
        "name": "system",
        "description": (
            "What is up and what is configured (database, Redis, GTS, payment "
            "provider), versions, and the audit journal."
        ),
    },
]

__all__ = ["API_DESCRIPTION", "TAGS"]
