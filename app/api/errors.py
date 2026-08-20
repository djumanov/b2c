"""The error catalogue — API.md §3 implemented once.

Eleven codes, each with a fixed HTTP status. Code below raises an ``AppError``
subclass; the handlers registered here are the only place an error becomes a
response. Nothing else may build an error body, and nothing may swallow a
failure into a silent ``return None``.

Upstream failures keep their evidence. GTS answers HTTP 200 with a negative
code even when it fails (GTS.md §10); the adapter turns that into an
``UpstreamError``, and the original text stays in ``message`` while the
original code goes to ``meta.upstream`` — losing it would make production
diagnosis guesswork (API.md §3, ARCHITECTURE.md §7).
"""

from enum import StrEnum
from typing import Any, Final

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.envelope import ApiError, error_envelope, json_response
from app.core.logging import get_logger

logger = get_logger(__name__)


class ErrorCode(StrEnum):
    """The closed set from API.md §3. Do not add without editing the contract."""

    VALIDATION = "validation"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    UPSTREAM_ERROR = "upstream_error"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    OFFER_EXPIRED = "offer_expired"
    INTERNAL = "internal"


#: code → HTTP status, exactly the table in API.md §3.
ERROR_STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.VALIDATION: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.UPSTREAM_TIMEOUT: 504,
    ErrorCode.OFFER_EXPIRED: 409,
    ErrorCode.INTERNAL: 500,
}


class AppError(Exception):
    """Base for every expected failure. ``message`` is shown to the user."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        meta: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.meta = meta
        self.headers = headers

    @property
    def http_status(self) -> int:
        return ERROR_STATUS[self.code]

    def to_api_error(self) -> ApiError:
        return ApiError(code=self.code.value, field=self.field, message=self.message)


class ValidationFailed(AppError):
    """Request body or parameters are wrong. ``field`` names the culprit."""

    code = ErrorCode.VALIDATION


class Unauthorized(AppError):
    """No token, or it is invalid or expired."""

    code = ErrorCode.UNAUTHORIZED


class Forbidden(AppError):
    """Authenticated, but not allowed — including a token for the other surface."""

    code = ErrorCode.FORBIDDEN


class NotFound(AppError):
    code = ErrorCode.NOT_FOUND


class Conflict(AppError):
    """State conflict, e.g. cancelling an already-cancelled order."""

    code = ErrorCode.CONFLICT


class RateLimited(AppError):
    """Too many requests (API.md §14). Always carries ``Retry-After``."""

    code = ErrorCode.RATE_LIMITED

    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message, headers={"Retry-After": str(retry_after)})
        self.retry_after = retry_after


class UpstreamError(AppError):
    """GTS or a payment provider returned an error."""

    code = ErrorCode.UPSTREAM_ERROR

    def __init__(
        self,
        message: str,
        *,
        upstream_code: str | int | None = None,
        upstream_message: str | None = None,
    ) -> None:
        upstream: dict[str, Any] = {}
        if upstream_code is not None:
            upstream["code"] = upstream_code
        if upstream_message is not None:
            upstream["message"] = upstream_message
        super().__init__(message, meta={"upstream": upstream} if upstream else None)


class UpstreamTimeout(AppError):
    """Upstream did not answer inside the budget (API.md §12: 40 s / 15 s)."""

    code = ErrorCode.UPSTREAM_TIMEOUT


class OfferExpired(AppError):
    """The offer timed out on the GTS side — the search must start again."""

    code = ErrorCode.OFFER_EXPIRED


class InternalError(AppError):
    code = ErrorCode.INTERNAL


#: Statuses raised as bare ``HTTPException`` by FastAPI itself, mapped back
#: into the catalogue. 405 is reported as ``not_found``: the catalogue is
#: closed, and "that path does not exist for that method" is the honest
#: reading for a caller.
_HTTP_STATUS_TO_CODE: Final[dict[int, ErrorCode]] = {
    400: ErrorCode.VALIDATION,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION,
    429: ErrorCode.RATE_LIMITED,
    502: ErrorCode.UPSTREAM_ERROR,
    504: ErrorCode.UPSTREAM_TIMEOUT,
}

#: FastAPI reports the location as ``("body", "email")``; the contract wants
#: just ``"email"`` (API.md §2).
_LOCATION_PREFIXES: Final[frozenset[str]] = frozenset(
    {"body", "query", "path", "header", "cookie"}
)


def _field_from_location(location: tuple[Any, ...]) -> str | None:
    parts = list(location)
    if parts and str(parts[0]) in _LOCATION_PREFIXES:
        parts = parts[1:]
    return ".".join(str(part) for part in parts) or None


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the four handlers that turn failures into the envelope."""

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> Response:
        return json_response(
            error_envelope([exc.to_api_error()], exc.meta),
            exc.http_status,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> Response:
        errors = [
            ApiError(
                code=ErrorCode.VALIDATION.value,
                field=_field_from_location(tuple(item.get("loc", ()))),
                message=str(item.get("msg", "Invalid value")),
            )
            for item in exc.errors()
        ]
        return json_response(error_envelope(errors), ERROR_STATUS[ErrorCode.VALIDATION])

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _: Request, exc: StarletteHTTPException
    ) -> Response:
        code = _HTTP_STATUS_TO_CODE.get(
            exc.status_code,
            ErrorCode.INTERNAL if exc.status_code >= 500 else ErrorCode.VALIDATION,
        )
        error = ApiError(code=code.value, message=str(exc.detail))
        headers = dict(exc.headers) if exc.headers else None
        # The catalogue's status, not Starlette's. The two differ where the
        # framework raises something the contract does not have: a 405 comes
        # back as `404 not_found`, because "that path does not exist for that
        # method" is the honest reading and 405 is not one of the eleven
        # statuses a client is ever told to expect (API.md §3).
        return json_response(
            error_envelope([error]), ERROR_STATUS[code], headers=headers
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> Response:
        # Log the traceback, return nothing that could describe our internals.
        logger.exception(
            "unhandled_error",
            method=request.method,
            path=request.url.path,
            error=type(exc).__name__,
        )
        error = ApiError(code=ErrorCode.INTERNAL.value, message="Internal server error")
        return json_response(error_envelope([error]), ERROR_STATUS[ErrorCode.INTERNAL])


__all__ = [
    "ERROR_STATUS",
    "AppError",
    "Conflict",
    "ErrorCode",
    "Forbidden",
    "InternalError",
    "NotFound",
    "OfferExpired",
    "RateLimited",
    "Unauthorized",
    "UpstreamError",
    "UpstreamTimeout",
    "ValidationFailed",
    "register_exception_handlers",
]
