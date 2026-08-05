"""Response shapes for the system endpoints (API.md §39)."""

from enum import StrEnum

from pydantic import BaseModel


class ComponentStatus(StrEnum):
    OK = "ok"
    FAILING = "failing"
    #: Configured from the panel and not filled in yet — not a fault.
    NOT_CONFIGURED = "not_configured"


class ComponentHealth(BaseModel):
    status: ComponentStatus
    latency_ms: float | None = None
    detail: str | None = None


class HealthOut(BaseModel):
    """Which of the three parties is at fault (PROJECT.md §4).

    An installation that stops selling is broken in our code, on the GTS side,
    or in the client's infrastructure. This endpoint separates the three, so
    the first support question has an answer before anyone reads a log.
    """

    status: ComponentStatus
    components: dict[str, ComponentHealth]


class VersionOut(BaseModel):
    """The first thing asked for when a client reports a problem.

    Clients upgrade on their own schedule (PROJECT.md D10), so installations
    sit on different versions and a bug report is meaningless without one.
    """

    backend: str
    panel: str | None = None


__all__ = [
    "ComponentHealth",
    "ComponentStatus",
    "HealthOut",
    "VersionOut",
]
