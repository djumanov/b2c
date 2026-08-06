"""Request and response shapes for ``/admin/integrations/gts/*`` (API.md §29).

The password goes **in** in full and comes **out** masked, so the two
directions cannot share a model. ``CredentialOut`` has no field that could
carry the real value — that is the point of it being a separate class.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, HttpUrl, field_validator

from app.modules.integrations.models import DEFAULT_BASE_URL

Label = Annotated[str, Field(min_length=1, max_length=64)]
GtsPassword = Annotated[str, Field(min_length=1, max_length=128)]
AgentUid = Annotated[str, Field(max_length=64)]


def _clean_base_url(value: str) -> str:
    """Validate it as a URL, then store it without the trailing slash.

    Paths are joined onto this as ``/v1/…``; keeping the slash would produce
    ``…space//v1/…``, which some gateways answer and others do not.
    """
    HttpUrl(value)  # raises for anything that is not a URL
    return value.strip().rstrip("/")


class CredentialCreateIn(BaseModel):
    label: Label
    email: EmailStr
    password: GtsPassword
    base_url: str = DEFAULT_BASE_URL
    agent_uid: AgentUid | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        return _clean_base_url(value)


class CredentialUpdateIn(BaseModel):
    """``PATCH`` is partial (API.md §8): ``None`` means "leave it alone".

    Which is also why the password cannot be cleared here — an account with no
    password is not a state worth being able to reach. Sending a new one
    replaces the old one; omitting it keeps it.
    """

    label: Label | None = None
    email: EmailStr | None = None
    password: GtsPassword | None = None
    base_url: str | None = None
    agent_uid: AgentUid | None = None

    @field_validator("base_url")
    @classmethod
    def _base_url(cls, value: str | None) -> str | None:
        return None if value is None else _clean_base_url(value)


class CredentialOut(BaseModel):
    """What the panel sees. ``password`` is always masked (API.md §29)."""

    id: uuid.UUID
    label: str
    base_url: str
    email: str
    password: str
    agent_uid: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


__all__ = ["CredentialCreateIn", "CredentialOut", "CredentialUpdateIn"]
