"""Writing and reading the journal — ARCHITECTURE.md §5, API.md §13 and §39.

Two ways in, because there are two kinds of event:

``record`` writes into **the caller's session**. The staff module uses it for
authentication events, which the contract singles out ("Autentifikatsiya
hodisalari … alohida belgilanadi", API.md §13) and which the middleware cannot
see properly: a failed login has no signed-in actor and returns 401, so a rule
built on "2xx and ``request.state.principal``" would miss exactly the event
worth keeping.

``record_detached`` opens a session of its own and is what the middleware uses,
because by the time a response has a status code the request's session is
closed. That means the entry is written **after** the change committed, in a
separate transaction, and the two can therefore come apart. The alternative —
failing the request when its audit entry cannot be written — would turn a
completed mutation into a 500 and invite the client to repeat it. A missing
journal line is bad; a double refund is worse. The failure is logged at
exception level, which is the loudest thing available that does not lie to the
caller about what happened.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.listing import (
    ListQuery,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
from app.core.logging import get_logger, redact
from app.db.session import get_sessionmaker
from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditActorOut, AuditLogOut

logger = get_logger(__name__)

#: What ``?ordering=`` may name (API.md §6).
_ORDERING = {
    "created_at": AuditLog.created_at,
    "resource": AuditLog.resource,
    "action": AuditLog.action,
}


async def record(
    session: AsyncSession,
    *,
    resource: str,
    action: str,
    actor_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    actor_role: str | None = None,
    resource_id: uuid.UUID | None = None,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
    ip: str | None = None,
    request_id: str | None = None,
    changes: dict[str, Any] | None = None,
) -> AuditLog:
    """Add an entry. The caller owns the commit."""
    entry = AuditLog(
        resource=resource,
        action=action,
        actor_id=actor_id,
        actor_email=actor_email,
        actor_role=actor_role,
        resource_id=resource_id,
        method=method,
        path=path[:255] if path else None,
        status_code=status_code,
        ip=ip[:45] if ip else None,
        request_id=request_id,
        changes=redact(changes) if changes else None,
    )
    session.add(entry)
    return entry


async def record_detached(**fields: Any) -> None:
    """Write in a session of its own, and never raise. See the module docstring."""
    try:
        async with get_sessionmaker()() as session:
            await record(session, **fields)
            await session.commit()
    except Exception:
        logger.exception(
            "audit_write_failed",
            resource=fields.get("resource"),
            action=fields.get("action"),
            request_id=fields.get("request_id"),
        )


def _to_out(entry: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=entry.id,
        created_at=entry.created_at,
        actor=AuditActorOut(
            id=entry.actor_id, email=entry.actor_email, role=entry.actor_role
        ),
        resource=entry.resource,
        action=entry.action,
        resource_id=entry.resource_id,
        method=entry.method,
        path=entry.path,
        status_code=entry.status_code,
        ip=entry.ip,
        request_id=entry.request_id,
        changes=entry.changes,
    )


async def list_entries(
    session: AsyncSession,
    pagination: Pagination,
    query: ListQuery,
    *,
    actor: uuid.UUID | None = None,
    resource: str | None = None,
    action: str | None = None,
) -> Page[AuditLogOut]:
    """API.md §39: ``?actor=``, ``?resource=``, ``?action=`` plus the §6 set.

    ``resource`` matches by prefix, so ``?resource=settings`` answers "what has
    anyone changed in the settings" without the caller having to know that the
    entries are stored as ``settings.branding``, ``settings.languages`` and so
    on.
    """
    stmt = select(AuditLog)
    if actor is not None:
        stmt = stmt.where(AuditLog.actor_id == actor)
    if resource:
        stmt = stmt.where(AuditLog.resource.startswith(resource))
    if action:
        stmt = stmt.where(AuditLog.action == action)

    stmt = apply_search(stmt, query, AuditLog.actor_email, AuditLog.path)
    stmt = apply_created_range(stmt, query, AuditLog.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_ORDERING,
        default="-created_at",
        tiebreak=AuditLog.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([_to_out(row) for row in rows], pagination, total)


__all__ = ["list_entries", "record", "record_detached"]
