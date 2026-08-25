"""The published schema says what the wire carries — and explains itself.

``/api/v1/openapi.json`` is the contract a client developer reads, so what
it promises is pinned here: the envelope, the two token schemes, the error
shapes, the trailing slash, the parameters a screen needs, and a description
on every operation of the order, payment, card and provider surfaces.
"""

import json
from typing import Any

import httpx
import pytest

from app.main import app
from app.modules.orders.models import PaymentStatus
from app.modules.orders.schemas import PAYMENT_VIEW_STATUSES

SCHEMA: dict[str, Any] = app.openapi()
PATHS: dict[str, Any] = SCHEMA["paths"]
SCHEMAS: dict[str, Any] = SCHEMA["components"]["schemas"]

#: The operations this round documents, every one of which must explain itself.
DOCUMENTED: tuple[tuple[str, str], ...] = (
    ("/api/v1/public/orders/", "get"),
    ("/api/v1/public/orders/{id}/", "get"),
    ("/api/v1/public/orders/{id}/receipt/", "get"),
    ("/api/v1/public/orders/{id}/reprice/", "post"),
    ("/api/v1/public/orders/{id}/reprice/confirm/", "post"),
    ("/api/v1/public/orders/{id}/payment/", "post"),
    ("/api/v1/public/orders/{id}/payment/resend/", "post"),
    ("/api/v1/public/orders/{id}/payment/confirm/", "post"),
    ("/api/v1/public/{product}/booking/", "post"),
    ("/api/v1/public/profile/cards/", "get"),
    ("/api/v1/public/profile/cards/", "post"),
    ("/api/v1/public/profile/cards/{id}/", "get"),
    ("/api/v1/public/profile/cards/{id}/", "delete"),
    ("/api/v1/admin/orders/", "get"),
    ("/api/v1/admin/orders/{id}/", "get"),
    ("/api/v1/admin/orders/{id}/refund/", "post"),
    ("/api/v1/admin/orders/{id}/sync/", "post"),
    ("/api/v1/admin/orders/{id}/ticketing/retry/", "post"),
    ("/api/v1/admin/orders/messages/", "get"),
    ("/api/v1/admin/orders/messages/{status}/", "get"),
    ("/api/v1/admin/orders/messages/{status}/", "patch"),
    ("/api/v1/admin/integrations/payments/", "get"),
    ("/api/v1/admin/integrations/payments/{code}/", "get"),
    ("/api/v1/admin/integrations/payments/{code}/", "patch"),
    ("/api/v1/admin/integrations/payments/{code}/test/", "post"),
)


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (path, method, operation)
        for path, operations in PATHS.items()
        for method, operation in operations.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    """One hop of ``$ref``, and through a one-armed ``allOf``/``anyOf``."""
    if "$ref" in schema:
        return SCHEMAS[schema["$ref"].rsplit("/", 1)[-1]]
    for key in ("allOf", "anyOf"):
        arms = [arm for arm in schema.get(key, []) if arm != {"type": "null"}]
        if len(arms) == 1:
            return _resolve(arms[0])
    return schema


def _data_schema(operation: dict[str, Any], status: str) -> dict[str, Any]:
    body = operation["responses"][status]["content"]["application/json"]["schema"]
    return body["properties"]["data"]


# --- the document --------------------------------------------------------------------


def test_schema_is_a_complete_json_document_that_introduces_itself() -> None:
    assert json.loads(json.dumps(SCHEMA)) == SCHEMA
    assert SCHEMA["openapi"].startswith("3.1")
    intro = SCHEMA["info"]["description"]
    for phrase in ("Authorization", "Idempotency-Key", "Accept-Language", "/`"):
        assert phrase in intro
    assert "API.md" not in intro


def test_every_path_ends_with_a_slash() -> None:
    assert PATHS
    assert all(path.endswith("/") for path in PATHS)


def test_every_used_tag_is_described_and_split_by_surface() -> None:
    described = {tag["name"]: tag["description"] for tag in SCHEMA["tags"]}
    used = {tag for _, _, op in _operations() for tag in op.get("tags", [])}
    assert used <= set(described)
    assert all(described[tag] for tag in used)
    for path, _, op in _operations():
        if path.startswith("/api/v1/admin/orders/"):
            assert op["tags"] == ["admin-orders"], path
        if path.startswith("/api/v1/public/orders/"):
            assert op["tags"] == ["orders"], path
        if path.startswith("/api/v1/public/profile/cards/"):
            assert op["tags"] == ["saved-cards"], path


# --- who may call --------------------------------------------------------------------


def test_two_bearer_schemes_and_a_lock_on_every_guarded_operation() -> None:
    schemes = SCHEMA["components"]["securitySchemes"]
    assert set(schemes) == {"customerToken", "staffToken"}
    assert all(
        scheme["type"] == "http" and scheme["scheme"] == "bearer"
        for scheme in schemes.values()
    )
    for path, _, op in _operations():
        if path.startswith("/api/v1/admin/auth/"):
            continue  # signing in is how one gets a token
        if path.startswith("/api/v1/admin/"):
            assert op["security"] == [{"staffToken": []}], path
        elif path.startswith(("/api/v1/public/orders/", "/api/v1/public/profile/")):
            assert op["security"] == [{"customerToken": []}], path
    # A step anyone may call, signed in or not, says so with an empty alternative.
    search = PATHS["/api/v1/public/{product}/search/"]["post"]
    assert search["security"] == [{"customerToken": []}, {}]


def test_owner_only_operations_say_so() -> None:
    providers = "/api/v1/admin/integrations/payments/{code}/"
    assert PATHS[providers]["patch"]["description"].startswith("**Owner role only.**")
    assert PATHS[f"{providers}test/"]["post"]["description"].startswith(
        "**Owner role only.**"
    )
    assert "Owner role only" not in PATHS[providers]["get"]["description"]


# --- errors ---------------------------------------------------------------------------


def test_every_error_is_the_envelope_and_never_fastapis_shape() -> None:
    assert "HTTPValidationError" not in json.dumps(SCHEMA)
    for path, _, op in _operations():
        for status, response in op["responses"].items():
            if status.startswith("2"):
                continue
            body = response["content"]["application/json"]["schema"]
            assert body["allOf"] == [{"$ref": "#/components/schemas/ErrorEnvelope"}], (
                path,
                status,
            )
            assert response["description"], (path, status)
        assert "Retry-After" in op["responses"]["429"]["headers"], path


def test_the_money_endpoints_list_the_errors_they_raise() -> None:
    # The check is a pure question: GTS's answer or GTS's failure, no 409 of ours.
    check = PATHS["/api/v1/public/orders/{id}/reprice/"]["post"]["responses"]
    assert {"502", "504"} <= set(check)
    assert "409" not in check
    # Confirm writes, and reads the order back, so it can find the hold released.
    confirm_price = PATHS["/api/v1/public/orders/{id}/reprice/confirm/"]["post"][
        "responses"
    ]
    assert {"409", "502", "504"} <= set(confirm_price)
    codes = confirm_price["409"]["content"]["application/json"]["schema"]["properties"][
        "errors"
    ]["items"]["properties"]["code"]["enum"]
    assert set(codes) == {"conflict", "offer_expired"}

    start = PATHS["/api/v1/public/orders/{id}/payment/"]["post"]["responses"]
    assert {"409", "502", "504"} <= set(start)
    codes = start["409"]["content"]["application/json"]["schema"]["properties"][
        "errors"
    ]["items"]["properties"]["code"]["enum"]
    assert set(codes) == {"conflict", "offer_expired"}
    assert "Nothing was charged" in start["502"]["description"]

    resend = PATHS["/api/v1/public/orders/{id}/payment/resend/"]["post"]["responses"]
    assert {"409", "502", "504"} <= set(resend)
    resend_codes = resend["409"]["content"]["application/json"]["schema"]["properties"][
        "errors"
    ]["items"]["properties"]["code"]["enum"]
    assert set(resend_codes) == {"conflict"}
    # Resend never re-reads GTS or checks the ticketing deadline.
    assert "offer_expired" not in resend_codes

    confirm = PATHS["/api/v1/public/orders/{id}/payment/confirm/"]["post"]["responses"]
    assert "409" in confirm
    # Confirm has no upstream path of its own: a lost provider answer is a
    # ``200 processing`` and ticketing failures never surface here.
    assert "502" not in confirm
    assert "504" not in confirm

    booking = PATHS["/api/v1/public/{product}/booking/"]["post"]["responses"]
    assert {"409", "502", "504"} <= set(booking)
    assert "No order is created" in booking["502"]["description"]


# --- the parameters a screen needs ----------------------------------------------------


def _parameters(path: str, method: str) -> dict[str, dict[str, Any]]:
    return {param["name"]: param for param in PATHS[path][method]["parameters"]}


def test_language_is_a_query_and_a_header_both_explained() -> None:
    params = _parameters("/api/v1/public/orders/{id}/", "get")
    assert params["lang"]["in"] == "query"
    assert "Accept-Language" in params["lang"]["description"]
    assert params["Accept-Language"]["in"] == "header"
    assert params["Accept-Language"]["description"]


def test_list_parameters_name_their_own_fields() -> None:
    public = _parameters("/api/v1/public/orders/", "get")
    assert "PNR" in public["search"]["description"]
    assert "`created_at`" in public["ordering"]["description"]
    assert "`updated_at`" in public["ordering"]["description"]
    assert "-created_at" in public["ordering"]["description"]
    assert public["page"]["description"] and public["page_size"]["description"]

    admin = _parameters("/api/v1/admin/orders/", "get")
    assert "GTS order number" in admin["search"]["description"]
    assert "inbox" in admin["status"]["description"]
    assert "ticket_waiting" in _resolve(admin["status"]["schema"])["enum"]

    cards = _parameters("/api/v1/public/profile/cards/", "get")
    assert "`last_used_at`" in cards["ordering"]["description"]

    payment = _parameters("/api/v1/public/orders/{id}/payment/", "post")
    assert payment["Idempotency-Key"]["in"] == "header"


# --- the shapes -----------------------------------------------------------------------


def test_every_documented_operation_explains_itself() -> None:
    for path, method in DOCUMENTED:
        operation = PATHS[path][method]
        assert operation.get("summary"), (path, method)
        assert len(operation.get("description", "")) > 40, (path, method)


@pytest.mark.parametrize(
    "model",
    [
        "OrderOut",
        "OrderListItemOut",
        "PaymentOut",
        "TicketingOut",
        "BookingResultOut",
        "PaymentStartIn",
        "PaymentConfirmIn",
        "PaymentResendIn",
        "RepriceOut",
        "CardOut",
        "CardCreateIn",
        "CardIn",
        "OrderAdminListItemOut",
        "OrderAdminOut",
        "OrderEventOut",
        "PaymentAttemptAdminOut",
        "RefundIn",
        "OrderMessageOut",
        "OrderMessageIn",
        "PaymentProviderOut",
        "PaymentProviderIn",
        "ProviderFieldOut",
        "PaymentTestOut",
    ],
)
def test_every_field_of_the_documented_models_has_a_description(model: str) -> None:
    schema = SCHEMAS[model]
    assert schema.get("description"), model
    undocumented = [
        name
        for name, prop in schema["properties"].items()
        if not prop.get("description")
        and name not in {"id", "created_at", "updated_at"}
    ]
    assert undocumented == [], (model, undocumented)


def test_the_status_vocabularies_are_enumerated() -> None:
    payment = SCHEMAS["PaymentOut"]["properties"]["status"]
    assert set(payment["enum"]) == set(PAYMENT_VIEW_STATUSES)
    assert set(PAYMENT_VIEW_STATUSES) == {member.value for member in PaymentStatus} | {
        "awaiting_otp",
        "processing",
        "cancelled",
    }
    assert set(SCHEMAS["Stage"]["enum"]) == {
        "booked",
        "ticket_waiting",
        "ticketed",
        "ticketing_failed",
        "refunded",
        "cancelled",
    }
    assert "ticket_waiting" in SCHEMAS["Stage"]["description"]
    ticketing = _resolve(SCHEMAS["TicketingOut"]["properties"]["status"])
    assert set(ticketing["enum"]) == {"pending", "processing", "ticketed", "failed"}
    kind = SCHEMAS["ProviderFieldOut"]["properties"]["kind"]
    assert set(kind["enum"]) == {"text", "secret", "int", "choice"}
    admin = SCHEMAS["OrderAdminListItemOut"]["properties"]
    assert _resolve(admin["payment_status"])["enum"]


def test_money_is_a_two_decimal_string() -> None:
    money = SCHEMAS["Money"]
    assert money["properties"]["amount"]["type"] == "string"
    assert money["examples"] == [{"amount": "287500.00", "currency": "UZS"}]
    assert money["properties"]["currency"]["pattern"] == "^[A-Z]{3}$"


def test_success_bodies_are_enveloped_and_lists_unfold() -> None:
    detail = _data_schema(PATHS["/api/v1/public/orders/{id}/"]["get"], "200")
    assert detail == {"$ref": "#/components/schemas/BookingResultOut"}
    listing = PATHS["/api/v1/public/orders/"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["properties"]
    assert listing["data"]["type"] == "array"
    assert listing["data"]["items"] == {"$ref": "#/components/schemas/OrderListItemOut"}
    assert listing["meta"] == {"$ref": "#/components/schemas/PageMeta"}
    delete = PATHS["/api/v1/public/profile/cards/{id}/"]["delete"]["responses"]["204"]
    assert "content" not in delete
    # The receipt is a file: it is the one success body the envelope leaves
    # alone, and it says so by offering no ``application/json`` at all.
    receipt = PATHS["/api/v1/public/orders/{id}/receipt/"]["get"]["responses"]["200"]
    assert set(receipt["content"]) == {"application/pdf", "text/html"}
    index = _parameters("/api/v1/public/orders/{id}/receipt/", "get")["passenger_index"]
    assert index["in"] == "query"
    assert _resolve(index["schema"])["minimum"] == 0


def test_booking_answers_with_the_documented_model_and_a_full_example() -> None:
    booking = PATHS["/api/v1/public/{product}/booking/"]["post"]["responses"]["201"]
    body = booking["content"]["application/json"]
    assert body["schema"]["properties"]["data"] == {
        "$ref": "#/components/schemas/BookingResultOut"
    }
    assert body["example"]["meta"] is None
    assert body["example"]["data"]["payment"]["status"] == "pending"
    examples = SCHEMAS["BookingResultOut"]["examples"]
    assert examples[0]["payment"]["status"] == "awaiting_otp"
    start = SCHEMAS["PaymentStartIn"]
    assert "method" in start["required"]
    assert len(start["examples"]) == 3
    assert all("method" in example for example in start["examples"])


# --- the pages ------------------------------------------------------------------------


async def test_the_docs_and_the_schema_are_served(client: httpx.AsyncClient) -> None:
    docs = await client.get("/api/v1/docs")
    assert docs.status_code == 200
    assert "text/html" in docs.headers["content-type"]
    published = await client.get("/api/v1/openapi.json")
    assert published.status_code == 200
    assert published.json() == json.loads(json.dumps(SCHEMA))
