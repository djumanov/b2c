"""Make the generated OpenAPI describe what is actually sent.

``API.md`` is the contract; OpenAPI is its artefact (API.md preamble). But
FastAPI generates the schema from the handler signatures, and handlers
deliberately return bare models — the envelope is added by ``EnvelopeRoute``
after the fact. Left alone, the published schema would promise ``{"id": …}``
where the wire carries ``{"status": …, "data": {"id": …}, …}``, and every
client generated from it would be wrong.

So the schema is post-processed once, here: each success response is wrapped
in the envelope, ``Page`` responses are unfolded into ``data`` + ``meta``, and
the shared error responses from API.md §3 are attached.

Webhook operations are skipped — they answer in the payment provider's own
protocol (API.md §40).

One more repair happens first. ``get_openapi`` finishes by encoding the schema
with ``exclude_none=True``, which walks into hand-written ``example`` blocks
and **deletes every ``null``**. That is not cosmetic: the envelope's ``meta``
is null on success and ``next_token`` is null on a first page, so the surviving
example teaches the client to omit fields the contract requires. Each route's
``openapi_extra`` is therefore re-applied afterwards, from the untouched
original.
"""

from typing import Any, Final

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts

from app.api.errors import ERROR_STATUS, ErrorCode

WEBHOOK_PATH_MARKER: Final = "/webhooks/"

#: Declared ``str | None`` on the dependency so a missing key becomes our own
#: ``422`` rather than FastAPI's, which leaves the generated schema calling it
#: optional. It is not: booking, paying and refunding all refuse without it
#: (API.md §10), and a client reading the schema would be told the opposite.
IDEMPOTENCY_HEADER: Final = "Idempotency-Key"

_API_ERROR_REF: Final = "#/components/schemas/ApiError"
_PAGE_META_REF: Final = "#/components/schemas/PageMeta"

_ENVELOPE_COMPONENTS: Final[dict[str, Any]] = {
    "ApiError": {
        "type": "object",
        "title": "ApiError",
        "description": "One problem. `field` is filled for `validation` only.",
        "properties": {
            "code": {
                "type": "string",
                "enum": [code.value for code in ErrorCode],
            },
            "field": {"type": "string", "nullable": True},
            "message": {"type": "string"},
        },
        "required": ["code", "message"],
    },
    "PageMeta": {
        "type": "object",
        "title": "PageMeta",
        "properties": {
            "page": {"type": "integer"},
            "page_size": {"type": "integer"},
            "total": {"type": "integer"},
            "total_pages": {"type": "integer"},
        },
        "required": ["page", "page_size", "total", "total_pages"],
    },
}

#: Errors any endpoint may return, documented once per operation.
_SHARED_ERROR_CODES: Final[tuple[ErrorCode, ...]] = (
    ErrorCode.VALIDATION,
    ErrorCode.UNAUTHORIZED,
    ErrorCode.FORBIDDEN,
    ErrorCode.NOT_FOUND,
    ErrorCode.RATE_LIMITED,
    ErrorCode.INTERNAL,
)


def _error_response(code: ErrorCode) -> dict[str, Any]:
    return {
        "description": code.value,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["error"]},
                        "data": {"nullable": True},
                        "errors": {"type": "array", "items": {"$ref": _API_ERROR_REF}},
                        "meta": {"type": "object", "nullable": True},
                    },
                    "required": ["status", "data", "errors", "meta"],
                }
            }
        },
    }


def _resolve(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """Follow a local ``$ref`` one hop; other schemas are returned unchanged."""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/components/schemas/"):
        return schema
    resolved = components.get(ref.rsplit("/", 1)[-1], {})
    return resolved if isinstance(resolved, dict) else {}


def _is_page_schema(schema: dict[str, Any], components: dict[str, Any]) -> bool:
    resolved = _resolve(schema, components)
    properties = resolved.get("properties")
    return isinstance(properties, dict) and set(properties) == {"items", "meta"}


def _envelope_schema(
    data_schema: dict[str, Any] | None, components: dict[str, Any]
) -> dict[str, Any]:
    """Wrap a success payload, lifting ``Page`` into ``data`` + ``meta``."""
    meta_schema: dict[str, Any] = {"type": "object", "nullable": True}
    if data_schema and _is_page_schema(data_schema, components):
        page = _resolve(data_schema, components)
        data_schema = page["properties"]["items"]
        meta_schema = {"$ref": _PAGE_META_REF}

    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["success"]},
            "data": data_schema if data_schema is not None else {"nullable": True},
            "errors": {"type": "array", "items": {"$ref": _API_ERROR_REF}},
            "meta": meta_schema,
        },
        "required": ["status", "data", "errors", "meta"],
    }


def _wrap_operation(operation: dict[str, Any], components: dict[str, Any]) -> None:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return

    for status_code, response in responses.items():
        if not status_code.isdigit() or not 200 <= int(status_code) < 300:
            continue
        # 204 has no body to wrap (API.md §8: DELETE returns no content).
        content = response.get("content")
        if int(status_code) == 204 or not isinstance(content, dict):
            continue
        json_content = content.get("application/json")
        if not isinstance(json_content, dict):
            continue
        json_content["schema"] = _envelope_schema(
            json_content.get("schema"), components
        )

    for code in _SHARED_ERROR_CODES:
        responses.setdefault(str(ERROR_STATUS[code]), _error_response(code))


def _require_idempotency_key(operation: dict[str, Any]) -> None:
    """Say the header is mandatory where it is (see ``IDEMPOTENCY_HEADER``)."""
    for parameter in operation.get("parameters") or ():
        if (
            isinstance(parameter, dict)
            and parameter.get("in") == "header"
            and parameter.get("name") == IDEMPOTENCY_HEADER
        ):
            parameter["required"] = True
            schema = parameter.get("schema")
            # ``str | None`` publishes an anyOf with a null branch; the header
            # is a string or it is absent, and absent is now a 422.
            if isinstance(schema, dict) and "anyOf" in schema:
                parameter["schema"] = {"type": "string"}


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge ``source`` into ``target``, recursing into nested dicts."""
    for key, value in source.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            _deep_update(current, value)
        else:
            target[key] = value


def _restore_extras(app: FastAPI, schema: dict[str, Any]) -> None:
    """Re-apply every ``openapi_extra``, nulls and all.

    ``include_router`` mounts child routers by reference, so a route's own
    ``path`` lacks its parents' prefixes; ``iter_route_contexts`` resolves the
    path it is actually served at.

    Must run **before** the envelope wrapping: it puts the raw ``data``-level
    response schema back, which ``_wrap_operation`` then wraps. Running it
    afterwards would undo that wrapping.
    """
    paths = schema.get("paths", {})
    for context in iter_route_contexts(app.routes):
        route = context.route
        if not isinstance(route, APIRoute) or not route.openapi_extra:
            continue
        operations = paths.get(context.path)
        if not isinstance(operations, dict):
            continue
        for method in route.methods or ():
            operation = operations.get(method.lower())
            if isinstance(operation, dict):
                _deep_update(operation, route.openapi_extra)


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """Generate the schema, envelope it, and cache it on the app."""
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    _restore_extras(app, schema)
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(_ENVELOPE_COMPONENTS)

    for path, operations in schema.get("paths", {}).items():
        if WEBHOOK_PATH_MARKER in path:
            continue
        for method, operation in operations.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                _require_idempotency_key(operation)
                _wrap_operation(operation, components)

    app.openapi_schema = schema
    return schema


__all__ = ["IDEMPOTENCY_HEADER", "WEBHOOK_PATH_MARKER", "build_openapi"]
