"""How code deep in a request adds detail to the entry about to be written.

The middleware knows *that* a mutation happened, from the route and the status
code. It cannot know *what changed* — only the service holding the row before
and after does. This is the channel between them.

It is a ``ContextVar`` holding a **mutable draft**, seeded by the middleware
before the request is dispatched. That detail matters: ``BaseHTTPMiddleware``
runs the rest of the application in its own task, which inherits a *copy* of
the context, so rebinding the variable downstream would be invisible upstream.
Mutating the object it already points at is not. Hence ``describe`` only ever
mutates, and there is no setter.

The alternative was threading ``Request`` into every service that wants to
record a diff, which would put a web object in the signature of functions that
have nothing to do with the web.
"""

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import redact


@dataclass(slots=True)
class AuditDraft:
    """What the middleware will write, once the response status is known."""

    resource: str | None = None
    action: str | None = None
    resource_id: uuid.UUID | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    #: Set by a route that audits itself, so the middleware does not write a
    #: second, poorer entry for the same request.
    suppressed: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


_draft: ContextVar[AuditDraft | None] = ContextVar("audit_draft", default=None)


def begin() -> Token[AuditDraft | None]:
    """Start collecting. Called by the middleware, nowhere else."""
    return _draft.set(AuditDraft())


def finish(token: Token[AuditDraft | None]) -> None:
    _draft.reset(token)


def current() -> AuditDraft | None:
    """The draft for this request, or ``None`` outside an audited one."""
    return _draft.get()


def describe(
    *,
    resource: str | None = None,
    action: str | None = None,
    resource_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    suppress: bool = False,
) -> None:
    """Add detail to this request's entry. A no-op when nothing is collecting.

    Safe to call from a service that is also used by a Celery task or a test:
    outside a request there is no draft, and the call does nothing.
    """
    draft = _draft.get()
    if draft is None:
        return
    if resource is not None:
        draft.resource = resource
    if action is not None:
        draft.action = action
    if resource_id is not None:
        draft.resource_id = resource_id
    if changes:
        # Redacted on the way in, so a secret cannot reach the table even if
        # the caller handed over a whole model dump (PROJECT.md §13).
        draft.changes.update(redact(changes))
    if suppress:
        draft.suppressed = True


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """``{field: {"from": …, "to": …}}`` for the fields that actually moved.

    Unchanged fields are dropped: an entry that repeats every column tells a
    reader nothing, and the one field somebody cares about is buried in it.
    """
    changed: dict[str, Any] = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changed[key] = {"from": old_value, "to": new_value}
    return changed


__all__ = ["AuditDraft", "begin", "current", "describe", "diff", "finish"]
