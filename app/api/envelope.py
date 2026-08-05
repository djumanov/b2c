"""The one response envelope, applied centrally.

Every response — success or failure — has the same shape (API.md §2)::

    {"status": "success", "data": {...}, "errors": [], "meta": null}
    {"status": "error",   "data": null,  "errors": [{...}], "meta": null}

**Handlers never build this.** They return a plain model, or a ``Page`` when
the result is a paginated list; ``EnvelopeRoute`` wraps it on the way out, and
``api.errors`` wraps failures. With roughly 150 endpoints across two surfaces,
hand-written envelopes would drift within a month — one endpoint would forget
``meta``, another would spell ``errors`` as ``error`` (ARCHITECTURE.md §11).

The one exception is ``/api/v1/webhooks/*``: there the response shape is
dictated by the payment provider's protocol, so that router does **not** use
this route class (API.md §40).
"""

import json
from collections.abc import Callable, Coroutine
from typing import Any, Final, Literal

from fastapi import APIRouter, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.requests import Request

ENVELOPE_KEYS: Final[frozenset[str]] = frozenset({"status", "data", "errors", "meta"})
PAGE_KEYS: Final[frozenset[str]] = frozenset({"items", "meta"})


class ApiError(BaseModel):
    """One problem. ``field`` is filled for ``validation`` only (API.md §3)."""

    code: str
    field: str | None = None
    message: str


class PageMeta(BaseModel):
    """Pagination block returned in ``meta`` for list endpoints (API.md §6)."""

    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def build(cls, *, page: int, page_size: int, total: int) -> "PageMeta":
        # A page size of zero cannot happen through Pagination, but computing
        # total_pages must not be the thing that raises if it ever does.
        total_pages = -(-total // page_size) if page_size > 0 else 0
        return cls(page=page, page_size=page_size, total=total, total_pages=total_pages)


class Page[T](BaseModel):
    """What a list endpoint returns: the rows plus their pagination meta.

    ``EnvelopeRoute`` lifts ``items`` into ``data`` and ``meta`` into ``meta``,
    so the handler never mentions the envelope::

        return Page(items=rows, meta=PageMeta.build(page=1, page_size=20, total=137))
    """

    items: list[T]
    meta: PageMeta


class ApiResponse[T](BaseModel):
    """The envelope itself — used for documentation and by error handlers."""

    status: Literal["success", "error"]
    data: T | None = None
    errors: list[ApiError] = Field(default_factory=list)
    meta: dict[str, Any] | None = None


def success_envelope(data: Any, meta: Any = None) -> dict[str, Any]:
    return {"status": "success", "data": data, "errors": [], "meta": meta}


def error_envelope(
    errors: list[ApiError], meta: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "status": "error",
        "data": None,
        "errors": [error.model_dump() for error in errors],
        "meta": meta,
    }


def json_response(
    payload: dict[str, Any], status_code: int, headers: dict[str, str] | None = None
) -> Response:
    return Response(
        content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        status_code=status_code,
        media_type="application/json",
        headers=headers,
    )


def _wrap(response: Response) -> Response:
    """Re-emit a serialised endpoint result inside the envelope.

    This re-parses the JSON FastAPI just produced. That costs one extra decode
    and encode per response, which is nothing next to a GTS call, and it buys
    the guarantee that *every* endpoint is enveloped regardless of how it chose
    to return its data.
    """
    body: bytes | None = getattr(response, "body", None)
    # 204 (soft delete, API.md §8) and streamed or file responses pass through.
    if not body or response.status_code == 204:
        return response
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        return response

    payload = json.loads(body)

    if isinstance(payload, dict):
        # Already enveloped: a handler built one by hand. Tolerated so the
        # response is not double-wrapped; the contract test is what flags it.
        if payload.keys() == ENVELOPE_KEYS:
            return response
        if payload.keys() == PAGE_KEYS:
            envelope = success_envelope(payload["items"], payload["meta"])
        else:
            envelope = success_envelope(payload)
    else:
        envelope = success_envelope(payload)

    wrapped = json_response(envelope, response.status_code)
    # Keep whatever the endpoint set — Cache-Control, ETag, Location,
    # Retry-After — but not the length and type of the body we just replaced.
    for key, value in response.headers.items():
        if key.lower() not in {"content-length", "content-type"}:
            wrapped.headers[key] = value
    return wrapped


class EnvelopeRoute(APIRoute):
    """Route class for the public and admin surfaces. Not for webhooks."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_route_handler = super().get_route_handler()

        async def envelope_route_handler(request: Request) -> Response:
            return _wrap(await original_route_handler(request))

        return envelope_route_handler


def enveloped_router(**kwargs: Any) -> APIRouter:
    """Build a module router that envelopes its responses.

    Every public and admin module router must be created with this::

        router = enveloped_router(prefix="/system", tags=["system"])

    ``route_class`` is **not** inherited from the parent router: ``include_router``
    keeps a reference to the child and serves its routes as the child declared
    them. So a module that builds a bare ``APIRouter`` silently returns
    unenveloped responses — which is what
    ``tests/contract/test_routes.py::test_public_and_admin_routes_use_the_envelope_route_class``
    exists to catch.
    """
    kwargs.setdefault("route_class", EnvelopeRoute)
    return APIRouter(**kwargs)


__all__ = [
    "ENVELOPE_KEYS",
    "PAGE_KEYS",
    "ApiError",
    "ApiResponse",
    "EnvelopeRoute",
    "Page",
    "PageMeta",
    "enveloped_router",
    "error_envelope",
    "json_response",
    "success_envelope",
]
