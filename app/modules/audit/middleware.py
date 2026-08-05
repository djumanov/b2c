"""Every admin mutation lands in the journal, without any handler saying so.

API.md §13: *"Har bir mutatsiya (POST/PATCH/DELETE) admin yuzasida audit
log'ga tushadi."* Every one — so the mechanism cannot be something a module has
to remember to attach. ARCHITECTURE.md §13.4 puts audit in the same list as the
envelope and RBAC: cross-cutting work is a dependency, not handler code.

**Why middleware and not the route class.** ``route_class`` does not propagate
through ``include_router`` (see ``api/envelope.enveloped_router``), so a
route-class solution has to be repeated by every module router — and the one
that forgets it fails silently, which is the worst way for an audit trail to
fail. A dependency cannot do it either: dependencies finish before there is a
status code, and an entry for a request that ended in 422 would be a lie.

**What it deliberately does not cover.** ``/admin/auth/*`` is excluded. Its
events have no signed-in actor and the interesting one — a failed login — is a
401, so this rule would miss it. The staff service records those itself, which
is also how the contract describes them ("alohida belgilanadi").
"""

import uuid
from collections.abc import Awaitable, Callable
from itertools import takewhile
from typing import Any, Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.api.deps import client_ip
from app.core.logging import REQUEST_ID_HEADER, get_logger
from app.modules.audit import context, service

logger = get_logger(__name__)

ADMIN_PREFIX: Final = "/api/v1/admin/"
#: Records its own events — see the module docstring.
SELF_AUDITING_PREFIX: Final = "/api/v1/admin/auth/"

MUTATING_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: The fallback when a path carries no verb of its own.
_ACTION_BY_METHOD: Final[dict[str, str]] = {
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}


def is_auditable(method: str, path: str) -> bool:
    return (
        method in MUTATING_METHODS
        and path.startswith(ADMIN_PREFIX)
        and not path.startswith(SELF_AUDITING_PREFIX)
    )


def describe_route(template: str, method: str) -> tuple[str, str]:
    """Read ``resource`` and ``action`` off the **route template**.

    The template still has its ``{id}`` placeholders, which is what makes this
    reliable — the concrete path cannot tell a resource segment from an
    identifier without guessing at UUID shapes.

        staff/                      → ("staff", "create")      on POST
        staff/{id}/                 → ("staff", "delete")      on DELETE
        staff/{id}/block/           → ("staff", "block")
        settings/branding/          → ("settings.branding", "update")

    A path whose verb comes before any placeholder — ``settings/cache/purge/``
    — reads as the resource ``settings.cache.purge``, which is wrong. Those
    routes say so themselves with ``Depends(Audited(...))``.
    """
    if template.startswith(ADMIN_PREFIX):
        template = template[len(ADMIN_PREFIX) :]
    segments = [segment for segment in template.split("/") if segment]

    before = list(takewhile(lambda s: not s.startswith("{"), segments))
    after = [s for s in segments[len(before) :] if not s.startswith("{")]

    resource = ".".join(before) or "admin"
    action = ".".join(after) if after else _ACTION_BY_METHOD.get(method, method.lower())
    return resource, action


def _resource_id(path_params: dict[str, Any]) -> uuid.UUID | None:
    """Which row was acted on, from the path.

    The values here are the router's, so they are still **strings** — FastAPI
    converts them to ``uuid.UUID`` when it builds the endpoint's arguments, and
    that conversion is not visible in the scope. ``id`` is tried first because
    that is what the contract calls the identifier (API.md §8); anything else
    that parses as a UUID is accepted so a nested path still records something.
    """
    for key in ("id", *path_params):
        value = path_params.get(key)
        if isinstance(value, uuid.UUID):
            return value
        if isinstance(value, str):
            try:
                return uuid.UUID(value)
            except ValueError:
                continue
    return None


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not is_auditable(request.method, request.url.path):
            return await call_next(request)

        # Seeded before dispatch so a service deeper in can fill in the diff;
        # see ``audit.context`` for why the draft has to be mutable.
        token = context.begin()
        try:
            response = await call_next(request)
        finally:
            draft = context.current()
            context.finish(token)

        # Only what actually happened. A 422 changed nothing, and recording it
        # would make the journal useless as an answer to "what was done".
        if 200 <= response.status_code < 300 and not (draft and draft.suppressed):
            await self._write(request, response, draft)
        return response

    @staticmethod
    async def _write(
        request: Request, response: Response, draft: context.AuditDraft | None
    ) -> None:
        route = request.scope.get("route")
        template = getattr(route, "path", request.url.path)
        resource, action = describe_route(template, request.method)

        principal = getattr(request.state, "principal", None)

        await service.record_detached(
            resource=(draft.resource if draft and draft.resource else resource),
            action=(draft.action if draft and draft.action else action),
            resource_id=(
                draft.resource_id
                if draft and draft.resource_id
                else _resource_id(request.path_params)
            ),
            actor_id=getattr(principal, "id", None),
            actor_email=getattr(principal, "email", None),
            actor_role=str(getattr(principal, "role", "")) or None,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            ip=client_ip(request),
            request_id=response.headers.get(REQUEST_ID_HEADER)
            or getattr(request.state, "request_id", None),
            changes=(draft.changes if draft and draft.has_changes else None),
        )


__all__ = [
    "ADMIN_PREFIX",
    "MUTATING_METHODS",
    "SELF_AUDITING_PREFIX",
    "AuditMiddleware",
    "describe_route",
    "is_auditable",
]
