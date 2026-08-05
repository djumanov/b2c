"""Structured logging: JSON to stdout, one request id per line.

Logs are JSON on stdout because the client runs this in Docker and reads it
with whatever they have; every line carries the request's ``X-Request-Id`` so a
support conversation can start from one id and find every line it touched
(API.md §13, PROJECT.md §12).

The redaction processor is not a nicety. Card numbers must never reach a log
(PROJECT.md §13), and integration credentials would otherwise land there the
first time somebody logs a config object. Both are dropped here, centrally,
rather than trusted to every call site.
"""

import logging
import re
import sys
import uuid
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from typing import Any, Final

import structlog

#: Bound by the request-id middleware; also read by handlers that need to echo
#: the id back to the caller.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

REQUEST_ID_HEADER: Final = "X-Request-Id"

#: Keys whose value never reaches a log line, at any nesting depth.
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "new_password",
        "old_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "secret",
        "secret_key",
        "api_key",
        "private_key",
        "credentials",
        "card",
        "card_number",
        "pan",
        "cvv",
        "cvc",
        "expiry",
    }
)

_REDACTED: Final = "[redacted]"

# A bare 13-19 digit run, optionally spaced or dashed — a card number in a
# message string that no key-based rule would catch.
_PAN_PATTERN: Final = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    if isinstance(value, str):
        return _PAN_PATTERN.sub(_REDACTED, value)
    return value


def redact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Same rules, for anything that is stored rather than logged.

    The audit journal keeps a field-level diff of every admin mutation
    (ARCHITECTURE.md §11), and integration credentials pass through those
    mutations. A diff written straight to a table would put the client's GTS
    password in a row an ``admin`` is allowed to read.
    """
    return {k: _redact_value(str(k), v) for k, v in payload.items()}


def redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Drop secrets and anything shaped like a card number."""
    return dict(redact(event_dict))


def add_request_id(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Stamp the current request id, when there is one."""
    request_id = request_id_var.get()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


def configure_logging(*, level: str | None = None, json_logs: bool = True) -> None:
    """Wire structlog and the stdlib root logger to the same output.

    Third-party libraries log through the stdlib; routing both through one
    structlog formatter means a line from ``sqlalchemy`` looks like a line from
    our own code and carries the same request id.
    """
    from app.core.config import settings

    resolved_level = (level or settings.log_level).upper()
    # Human-readable output in debug: JSON is for the client's log collector,
    # not for a developer reading a terminal.
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_logs and not settings.debug
        else structlog.dev.ConsoleRenderer()
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        add_request_id,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved_level)

    # Chatty libraries stay at WARNING; httpx in particular logs every request
    # URL, and upstream URLs can carry tokens in their path.
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Uvicorn installs its own handlers; drop them so its lines come out in our
    # format, through our processors, with the request id attached.
    for uvicorn_logger in ("uvicorn", "uvicorn.error"):
        logging.getLogger(uvicorn_logger).handlers = []
        logging.getLogger(uvicorn_logger).propagate = True

    # uvicorn.access is silenced outright. RequestIdMiddleware writes the access
    # line, because it is the only place that still has the request id bound;
    # letting uvicorn's line through as well would mean two lines per request,
    # and the id would be on only one of them. Note that `--no-access-log`
    # alone is not enough: it clears uvicorn's handlers and stops propagation,
    # and the loop above used to switch propagation straight back on.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True


__all__ = [
    "REQUEST_ID_HEADER",
    "add_request_id",
    "configure_logging",
    "get_logger",
    "new_request_id",
    "redact",
    "redact_secrets",
    "request_id_var",
]
