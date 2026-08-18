"""The flight flow is documented in Swagger, not just implemented.

The six flow endpoints take a bare ``dict`` and return a bare ``dict`` — that
is the passthrough (API.md §20), and it means FastAPI can infer **nothing**
about them. The shapes are supplied by hand in ``products/openapi.py``, and
hand-written documentation rots silently unless something fails when it goes
missing. That is what this file is.

Two things are checked that are easy to get wrong and invisible until a
frontend developer hits them:

* the envelope wrapper still runs on top of the hand-written schema, so ``data``
  holds our shape rather than replacing the envelope with it;
* ``null`` survives in the examples. ``get_openapi`` encodes with
  ``exclude_none=True``, which deletes ``"meta": null`` and
  ``"next_token": null`` from an example and quietly teaches the client to omit
  a field the contract requires.
"""

from typing import Any

import pytest

from app.main import app

API_PREFIX = "/api/v1"

#: Every step of the flow, and what ``data`` must describe for it.
STEPS: dict[str, tuple[str, ...]] = {
    "search": ("request_id", "fun_fact"),
    "offers": ("search_status", "next_token", "count", "trip_type", "offers"),
    "upsell": ("search_status", "offers"),
    "verify": ("search_status", "verified"),
    # Booking answers with our order beside GTS's answer; the answer itself is
    # what ``data`` holds, still whole, and the order inside it is checked
    # separately below.
    "booking": ("order", "payment", "data"),
    "cancel": ("status",),
}

#: What the order itself must name, one level under booking's ``data``. These
#: are the fields the ``orders`` module reads, so a schema that stops
#: describing them is a schema that has drifted from the code.
BOOKING_ORDER_FIELDS = ("order_number", "order_uid", "status")

ENVELOPE_KEYS = ["status", "data", "errors", "meta"]


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """The published document, from the same call that serves it over HTTP."""
    return app.openapi()


def _operation(schema: dict[str, Any], step: str) -> dict[str, Any]:
    operation = schema["paths"][f"{API_PREFIX}/public/{{product}}/{step}/"]["post"]
    assert isinstance(operation, dict)
    return operation


def _json(operation: dict[str, Any], where: str) -> dict[str, Any]:
    """The ``application/json`` block of the request body or the 200."""
    holder = (
        operation["requestBody"]
        if where == "request"
        else operation["responses"]["200"]
    )
    return holder["content"]["application/json"]


@pytest.mark.parametrize("step", STEPS)
def test_the_request_body_is_described(schema: dict[str, Any], step: str) -> None:
    """An untyped ``{"type": "object"}`` teaches nobody anything."""
    body = _json(_operation(schema, step), "request")

    assert body["schema"]["properties"], f"{step}: request body has no properties"
    # Unknown fields really do reach GTS — the schema describes, it never
    # constrains (API.md §20).
    assert body["schema"]["additionalProperties"] is True


@pytest.mark.parametrize("step", STEPS)
def test_the_request_carries_a_usable_example(
    schema: dict[str, Any], step: str
) -> None:
    """ "Try it out" must arrive pre-filled with something GTS would accept."""
    body = _json(_operation(schema, step), "request")

    assert isinstance(body.get("example"), dict)
    assert body["example"], f"{step}: empty request example"


@pytest.mark.parametrize(("step", "fields"), STEPS.items())
def test_the_response_data_is_described(
    schema: dict[str, Any], step: str, fields: tuple[str, ...]
) -> None:
    """The hand-written shape lands inside ``data``, not instead of it."""
    success = _json(_operation(schema, step), "response")["schema"]

    assert success["properties"]["status"]["enum"] == ["success"]
    assert sorted(success["required"]) == sorted(ENVELOPE_KEYS)

    data = success["properties"]["data"]
    for field in fields:
        assert field in data["properties"], f"{step}: `{field}` undocumented"


def test_the_booking_order_itself_is_described(schema: dict[str, Any]) -> None:
    """One level under booking's ``data`` is the order, and the three fields
    the ``orders`` module reads out of it must stay documented — that pair
    drifting apart is what made every ``gts_order_number`` NULL once."""
    success = _json(_operation(schema, "booking"), "response")["schema"]
    # envelope ``data`` → our answer → GTS's wrapper → the order itself.
    answer = success["properties"]["data"]["properties"]["data"]
    order = answer["properties"]["data"]

    for field in BOOKING_ORDER_FIELDS:
        assert field in order["properties"], f"booking: `{field}` undocumented"


@pytest.mark.parametrize("step", STEPS)
def test_the_response_example_is_the_whole_envelope(
    schema: dict[str, Any], step: str
) -> None:
    """What the client parses is the envelope, so that is what it is shown.

    ``meta`` is the canary: it is ``null`` on success, and ``null`` is exactly
    what the schema encoder strips.
    """
    example = _json(_operation(schema, step), "response")["example"]

    assert list(example) == ENVELOPE_KEYS
    assert example["status"] == "success"
    assert example["errors"] == []
    assert example["meta"] is None
    assert example["data"], f"{step}: empty data example"


def test_a_null_in_a_request_example_survives(schema: dict[str, Any]) -> None:
    """``offers`` asks for ``next_token: null`` on the first page (API.md §20).

    Dropping it would document a first page that omits the cursor entirely.
    """
    example = _json(_operation(schema, "offers"), "request")["example"]

    assert "next_token" in example
    assert example["next_token"] is None


def test_the_product_parameter_says_what_to_put_there(schema: dict[str, Any]) -> None:
    """``/{product}/`` is meaningless without naming the vertical."""
    parameters = _operation(schema, "search")["parameters"]
    product = next(p for p in parameters if p["name"] == "product")

    assert "flight" in product["description"]


@pytest.mark.parametrize("step", ["booking", "cancel"])
def test_the_unconfirmed_steps_say_so(schema: dict[str, Any], step: str) -> None:
    """Booking and cancel were never run against live GTS (STATUS.md §8).

    Documenting them as settled would be the schema asserting something nobody
    has checked.
    """
    operation = _operation(schema, step)
    request = _json(operation, "request")["schema"]["description"]
    # The hand-written schema is nested inside the envelope by now.
    response = _json(operation, "response")["schema"]["properties"]["data"][
        "description"
    ]

    assert "not yet confirmed against live gts" in request.lower()
    assert "not yet confirmed against live gts" in response.lower()
