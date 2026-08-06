"""Request-scoped middleware: the request id, and where CORS origins come from.

Both of the things in here answer a per-request question that the framework
would rather answer once, at startup: the request id, and which origins may
call this installation. The second is a setting the client edits from the
panel, so "once at startup" would mean "after a restart" — the deploy this
project promises not to need (PROJECT.md §7).

``X-Request-Id`` is the thread a support conversation is pulled by (API.md
§13). If the caller sent one it is kept — so a trace started in the panel or
the app continues here — otherwise one is generated. Either way it is bound to
the logger for the duration of the request, echoed in the response, and passed
on to GTS (ARCHITECTURE.md §7).

The access line is written **here** rather than left to uvicorn. Uvicorn logs
after the response has left the middleware stack, outside the contextvar's
scope, so its line carries no request id — which would make "every log line is
tied to a request id" untrue for the only line most requests produce. The
entrypoint disables uvicorn's access log so there is exactly one.
"""

import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Final

from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from app.core.config import settings
from app.core.logging import (
    REQUEST_ID_HEADER,
    get_logger,
    new_request_id,
    request_id_var,
)

logger = get_logger("app.access")

#: The container healthcheck hits this every 30 seconds. Logging it would bury
#: real traffic in the client's log with nothing to show for it.
_UNLOGGED_PATHS: Final[frozenset[str]] = frozenset({"/healthz"})


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        token = request_id_var.set(request_id)
        # Also on request.state so handlers and the GTS client can read it
        # without touching the contextvar.
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Log before re-raising, while the id is still bound: a request
                # that crashed is the one most worth being able to find.
                self._log(request, 500, started)
                raise
            self._log(request, response.status_code, started)
        finally:
            request_id_var.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    @staticmethod
    def _log(request: Request, status_code: int, started: float) -> None:
        if request.url.path in _UNLOGGED_PATHS:
            return
        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )


#: Stands for "reflect whatever the caller sent". Only ever produced in debug,
#: and deliberately not the literal ``*`` header: a wildcard is invalid with
#: ``Access-Control-Allow-Credentials``, and browsers refuse the pair outright.
ANY_ORIGIN: Final = "*"

#: Per-task, so the sync hook below can read what the async resolution found
#: without two concurrent requests seeing each other's answer.
_allowed_var: ContextVar[list[str]] = ContextVar("cors_allowed_origins")


async def allowed_origins() -> list[str]:
    """Which origins the browser surfaces may be called from (API.md §15).

    A **database setting**, not an environment variable: the client owns their
    domain and changes it from the panel, without a deploy (PROJECT.md §7).

    A failure here must not take the request with it. Answering "no origins" on
    a broken database costs a CORS error in a browser; raising would cost a 500
    on every request that carries an ``Origin``, including the ones that would
    otherwise have worked.
    """
    if settings.debug:
        return [ANY_ORIGIN]
    # Imported late: the settings module imports from ``api``, and this is the
    # one call that goes the other way (ARCHITECTURE.md §4 — through a service).
    from app.modules.settings import service as settings_service

    try:
        return await settings_service.cors_origins()
    except Exception:
        logger.exception("cors_origins_unavailable")
        return []


class DynamicCORSMiddleware(CORSMiddleware):
    """CORS whose origin list is a setting, resolved per request.

    Starlette reads ``allow_origins`` once, at construction. That is the wrong
    lifetime for a value the client edits from the panel: the change would need
    a restart, which is the deploy this project promises not to need. So the
    list is left empty and ``is_allowed_origin`` — the one hook the decision
    goes through, for both preflight and simple requests — answers from what
    was resolved for *this* request.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or "origin" not in Headers(scope=scope):
            # Not a cross-origin request; nothing to resolve and nothing to say.
            await self.app(scope, receive, send)
            return
        token = _allowed_var.set(await allowed_origins())
        try:
            await super().__call__(scope, receive, send)
        finally:
            _allowed_var.reset(token)

    def is_allowed_origin(self, origin: str) -> bool:
        allowed = _allowed_var.get([])
        return ANY_ORIGIN in allowed or origin in allowed


__all__ = [
    "ANY_ORIGIN",
    "DynamicCORSMiddleware",
    "RequestIdMiddleware",
    "allowed_origins",
]
