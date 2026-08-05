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
"""

from typing import Any, Final

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.api.errors import ERROR_STATUS, ErrorCode

WEBHOOK_PATH_MARKER: Final = "/webhooks/"

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
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(_ENVELOPE_COMPONENTS)

    for path, operations in schema.get("paths", {}).items():
        if WEBHOOK_PATH_MARKER in path:
            continue
        for method, operation in operations.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                _wrap_operation(operation, components)

    app.openapi_schema = schema
    return schema


__all__ = ["WEBHOOK_PATH_MARKER", "build_openapi"]
