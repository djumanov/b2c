"""Make the generated OpenAPI describe what is actually sent.

The published schema (``/api/v1/openapi.json``, rendered at ``/api/v1/docs``)
is the REST contract as clients see it; ``docs/order-system/README.md`` is
the order system's authority behind it. FastAPI generates the schema from
the handler signatures, and handlers deliberately return bare models — the
envelope is added by ``EnvelopeRoute`` after the fact. Left alone, the
schema would promise ``{"id": …}`` where the wire carries
``{"status": …, "data": {"id": …}, …}``, and every client generated from it
would be wrong.

So the schema is post-processed once, here:

* each success response is wrapped in the envelope, ``Page`` responses are
  unfolded into ``data`` + ``meta``;
* the shared error responses are attached with the sentence that says
  *when* each happens, and FastAPI's own ``422`` — a ``{"detail": […]}``
  shape this app never sends — is replaced by the enveloped one;
* every operation is stamped with the token it needs (``customerToken`` or
  ``staffToken``), read off its dependencies, so Swagger shows the lock and
  the "Authorize" dialog knows the two audiences apart. There is no
  ``HTTPBearer`` dependency on purpose: it would change the 401 body and
  the rule that the other surface's token is a 403.

Routes add the errors only they raise through ``error_responses``.

Webhook operations are skipped — they answer in the payment provider's own
protocol.

One more repair happens first. ``get_openapi`` finishes by encoding the
schema with ``exclude_none=True``, which walks into hand-written ``example``
blocks and **deletes every ``null``**. That is not cosmetic: the envelope's
``meta`` is null on success, so the surviving example teaches the client to
omit fields the contract requires. Each route's ``openapi_extra`` is
therefore re-applied afterwards, from the untouched original.
"""

from collections.abc import Callable, Iterable
from typing import Any, Final

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute, iter_route_contexts

from app.api.errors import ERROR_STATUS, ErrorCode

WEBHOOK_PATH_MARKER: Final = "/webhooks/"

_API_ERROR_REF: Final = "#/components/schemas/ApiError"
_ERROR_ENVELOPE_REF: Final = "#/components/schemas/ErrorEnvelope"
_PAGE_META_REF: Final = "#/components/schemas/PageMeta"

CUSTOMER_SCHEME: Final = "customerToken"
STAFF_SCHEME: Final = "staffToken"

_NULL: Final[dict[str, Any]] = {"type": "null"}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """``schema`` or ``null`` — the 3.1 spelling of ``nullable: true``."""
    return {"anyOf": [schema, _NULL]}


#: What each error means, in one sentence — the shared responses and
#: ``error_responses`` both read from here, so the wording cannot drift.
_ERROR_TEXT: Final[dict[ErrorCode, str]] = {
    ErrorCode.VALIDATION: (
        "The body, a query parameter or a header was refused. Each item in "
        "`errors` names the culprit in `field` — a dotted path into the body "
        "(`card.number`), a query name, or a header name (`Idempotency-Key`)."
    ),
    ErrorCode.UNAUTHORIZED: (
        "No `Authorization: Bearer …` header, or the token is expired or "
        "malformed. Refresh the token or sign in again."
    ),
    ErrorCode.FORBIDDEN: (
        "The token is valid but not for this: a customer token on an `/admin/` "
        "route (or a staff token on `/public/`), or a staff role below what the "
        "route asks for."
    ),
    ErrorCode.NOT_FOUND: (
        "No such resource — or not yours. An id that belongs to another "
        "customer answers the same 404, so existence is never revealed."
    ),
    ErrorCode.CONFLICT: (
        "The request is valid but the resource is not in a state that allows it."
    ),
    ErrorCode.RATE_LIMITED: (
        "Too many requests from this caller for this group of endpoints. "
        "`Retry-After` says how many seconds to wait."
    ),
    ErrorCode.UPSTREAM_ERROR: (
        "GTS or the payment provider refused the request. Their own code and "
        "message, when they gave one, are in `meta.upstream`."
    ),
    ErrorCode.UPSTREAM_TIMEOUT: (
        "GTS or the payment provider did not answer in time. The outcome is "
        "unknown to the client; read the resource back before retrying anything "
        "that moves money or seats."
    ),
    ErrorCode.OFFER_EXPIRED: (
        "GTS released the hold: the deadline passed or the seat is gone. The "
        "order is now `cancelled`; search again."
    ),
    ErrorCode.INTERNAL: (
        "Something broke on our side. The response carries an `X-Request-Id` "
        "to quote to support."
    ),
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

_RETRY_AFTER_HEADER: Final[dict[str, Any]] = {
    "Retry-After": {
        "description": "Seconds to wait before trying again.",
        "schema": {"type": "integer", "minimum": 1},
    }
}

_ENVELOPE_COMPONENTS: Final[dict[str, Any]] = {
    "ApiError": {
        "type": "object",
        "title": "ApiError",
        "description": (
            "One problem. `code` is from the closed catalogue; `message` is "
            "written for a person; `field` is set for `validation` only."
        ),
        "properties": {
            "code": {
                "type": "string",
                "enum": [code.value for code in ErrorCode],
            },
            "field": _nullable({"type": "string"}),
            "message": {"type": "string"},
        },
        "required": ["code", "message"],
    },
    "ErrorEnvelope": {
        "type": "object",
        "title": "ErrorEnvelope",
        "description": (
            "Every non-2xx answer: `status` is `error`, `data` is null, "
            "`errors` holds at least one item, `meta` carries context such as "
            "`upstream` for provider errors."
        ),
        "properties": {
            "status": {"type": "string", "enum": ["error"]},
            "data": _NULL,
            "errors": {"type": "array", "items": {"$ref": _API_ERROR_REF}},
            "meta": _nullable({"type": "object"}),
        },
        "required": ["status", "data", "errors", "meta"],
    },
    "PageMeta": {
        "type": "object",
        "title": "PageMeta",
        "description": "Where this page sits in the whole list.",
        "properties": {
            "page": {"type": "integer", "description": "1-based."},
            "page_size": {"type": "integer"},
            "total": {"type": "integer", "description": "Rows in the whole list."},
            "total_pages": {"type": "integer"},
        },
        "required": ["page", "page_size", "total", "total_pages"],
    },
}

_SECURITY_SCHEMES: Final[dict[str, Any]] = {
    CUSTOMER_SCHEME: {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "A customer's access token (`aud: public`), issued by "
            "`POST /api/v1/public/auth/login/` and refreshed by `refresh/`. "
            "Send it as `Authorization: Bearer <token>`. On an `/admin/` route "
            "it is a **403**, not a 401."
        ),
    },
    STAFF_SCHEME: {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": (
            "A staff member's access token (`aud: admin`), issued by "
            "`POST /api/v1/admin/auth/login/`. Roles are `owner` and `admin`; "
            "routes marked **Owner role only** refuse `admin` with a 403."
        ),
    },
}


# --- errors --------------------------------------------------------------------------


def _error_response(
    codes: Iterable[ErrorCode],
    description: str,
    *,
    headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One documented error answer, its `code` enum narrowed to ``codes``."""
    values = [code.value for code in codes]
    response: dict[str, Any] = {
        "description": description,
        "content": {
            "application/json": {
                "schema": {
                    "allOf": [{"$ref": _ERROR_ENVELOPE_REF}],
                    "properties": {
                        "errors": {
                            "type": "array",
                            "items": {
                                "allOf": [{"$ref": _API_ERROR_REF}],
                                "properties": {
                                    "code": {"type": "string", "enum": values}
                                },
                            },
                        }
                    },
                }
            }
        },
    }
    if headers:
        response["headers"] = headers
    return response


def _shared_error_response(code: ErrorCode) -> dict[str, Any]:
    headers = _RETRY_AFTER_HEADER if code is ErrorCode.RATE_LIMITED else None
    return _error_response((code,), _ERROR_TEXT[code], headers=headers)


def error_responses(
    *codes: ErrorCode, **wording: str
) -> dict[int | str, dict[str, Any]]:
    """``responses=`` entries for the errors only this route raises.

    The six shared codes are on every operation already; a route adds the
    rest — ``conflict``, ``offer_expired``, ``upstream_error``,
    ``upstream_timeout`` — with its own sentence when the catalogue's is too
    general: ``error_responses(ErrorCode.CONFLICT, conflict="A payment for
    this order is being confirmed.")``. Codes that share an HTTP status
    (``conflict`` and ``offer_expired`` are both 409) are merged into one
    answer whose description names both.
    """
    by_status: dict[int, list[ErrorCode]] = {}
    for code in codes:
        by_status.setdefault(ERROR_STATUS[code], []).append(code)
    responses: dict[int | str, dict[str, Any]] = {}
    for status, grouped in by_status.items():
        sentences = []
        for code in grouped:
            text = wording.get(code.value, _ERROR_TEXT[code])
            sentences.append(f"`{code.value}` — {text}" if len(grouped) > 1 else text)
        responses[status] = _error_response(grouped, " ".join(sentences))
    return responses


# --- the envelope --------------------------------------------------------------------


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
    meta_schema: dict[str, Any] = _nullable({"type": "object"})
    if data_schema and _is_page_schema(data_schema, components):
        page = _resolve(data_schema, components)
        data_schema = page["properties"]["items"]
        meta_schema = {"$ref": _PAGE_META_REF}

    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["success"]},
            "data": data_schema if data_schema is not None else _NULL,
            "errors": {
                "type": "array",
                "items": {"$ref": _API_ERROR_REF},
                "description": "Always empty on success.",
            },
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
        # 204 has no body to wrap: DELETE returns no content.
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
        status = str(ERROR_STATUS[code])
        if code is ErrorCode.VALIDATION:
            # FastAPI already wrote a 422 of its own shape here; ours is the
            # one the wire carries.
            responses[status] = _shared_error_response(code)
        else:
            responses.setdefault(status, _shared_error_response(code))


# --- who may call ---------------------------------------------------------------------


def _auth_calls() -> dict[Callable[..., Any], str]:
    # Imported here: ``deps`` pulls in the customer and staff services, and
    # this module is imported by ``main`` before the routers are.
    from app.api import deps

    return {
        deps.current_customer: CUSTOMER_SCHEME,
        deps.current_customer_optional: CUSTOMER_SCHEME,
        deps.current_staff: STAFF_SCHEME,
        deps.require_owner: STAFF_SCHEME,
    }


def _walk(dependant: Dependant) -> Iterable[Dependant]:
    for child in dependant.dependencies:
        yield child
        yield from _walk(child)


def _security_for(route: APIRoute) -> tuple[list[dict[str, list[str]]], bool]:
    """The ``security`` requirement of one route, and whether it is owner-only.

    Read off the dependency tree: router-level and surface-level
    ``dependencies=[Depends(...)]`` are flattened into it by ``include_router``,
    so one walk sees everything. A route whose only requirement is the
    optional customer may be called anonymously, which OpenAPI spells as
    an empty alternative.
    """
    from app.api import deps

    auth = _auth_calls()
    seen = {auth[node.call] for node in _walk(route.dependant) if node.call in auth}
    calls = {node.call for node in _walk(route.dependant)}
    owner_only = deps.require_owner in calls
    if STAFF_SCHEME in seen:
        return [{STAFF_SCHEME: []}], owner_only
    if CUSTOMER_SCHEME in seen:
        optional_only = deps.current_customer not in calls
        security: list[dict[str, list[str]]] = [{CUSTOMER_SCHEME: []}]
        if optional_only:
            security.append({})
        return security, False
    return [], False


def _stamp_security(app: FastAPI, schema: dict[str, Any]) -> None:
    paths = schema.get("paths", {})
    for context in iter_route_contexts(app.routes):
        route = context.route
        if not isinstance(route, APIRoute):
            continue
        operations = paths.get(context.path)
        if not isinstance(operations, dict):
            continue
        security, owner_only = _security_for(route)
        if not security:
            continue
        for method in route.methods or ():
            operation = operations.get(method.lower())
            if not isinstance(operation, dict):
                continue
            operation["security"] = security
            if owner_only:
                description = operation.get("description") or ""
                operation["description"] = "**Owner role only.**" + (
                    f"\n\n{description}" if description else ""
                )


# --- assembling ----------------------------------------------------------------------


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
        tags=app.openapi_tags,
    )
    _restore_extras(app, schema)
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas.update(_ENVELOPE_COMPONENTS)
    components.setdefault("securitySchemes", {}).update(_SECURITY_SCHEMES)

    for path, operations in schema.get("paths", {}).items():
        if WEBHOOK_PATH_MARKER in path:
            continue
        for method, operation in operations.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                _wrap_operation(operation, schemas)
    _stamp_security(app, schema)

    # FastAPI's validation shapes are referenced by nothing any more.
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    app.openapi_schema = schema
    return schema


__all__ = [
    "CUSTOMER_SCHEME",
    "STAFF_SCHEME",
    "WEBHOOK_PATH_MARKER",
    "build_openapi",
    "error_responses",
]
