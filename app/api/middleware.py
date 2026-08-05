"""Request-scoped middleware: the request id, and where CORS origins come from.

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
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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


def allowed_origins() -> list[str]:
    """Which origins the browser surfaces may be called from (API.md §15).

    This is a **database setting**, not an environment variable: the client
    owns their domain and changes it from the panel, without a deploy
    (PROJECT.md §7). Until the settings module exists, development is open and
    production is closed — closed is the safe direction to be wrong in.
    """
    if settings.debug:
        return ["*"]
    return []


__all__ = ["RequestIdMiddleware", "allowed_origins"]
