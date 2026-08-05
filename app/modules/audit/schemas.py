"""What ``GET /admin/system/audit/`` returns (API.md §39)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditActorOut(BaseModel):
    """Who acted. All three fields are null for an event with no actor —
    a failed login is the ordinary case."""

    id: uuid.UUID | None = None
    email: str | None = None
    role: str | None = None


class AuditLogOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    actor: AuditActorOut
    resource: str
    action: str
    resource_id: uuid.UUID | None = None
    method: str | None = None
    path: str | None = None
    status_code: int | None = None
    ip: str | None = None
    request_id: str | None = None
    changes: dict[str, Any] | None = None


__all__ = ["AuditActorOut", "AuditLogOut"]
