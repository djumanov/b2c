"""Shapes for ``/public/leads/`` (API.md §25) and ``/admin/leads/`` (§35)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.modules.leads.models import LeadStatus


class LeadCreateIn(BaseModel):
    topic: Annotated[str, Field(min_length=1, max_length=128)]
    name: Annotated[str, Field(max_length=160)] | None = None
    contact: Annotated[str, Field(min_length=3, max_length=160)]
    message: Annotated[str, Field(min_length=1, max_length=4000)]


class LeadCreatedOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    status: str
    created_at: datetime


class LeadAdminOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    topic: str
    name: str | None
    contact: str
    message: str
    status: str
    note: str | None
    customer_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class LeadUpdateIn(BaseModel):
    status: LeadStatus | None = None
    #: An empty string clears the note.
    note: Annotated[str, Field(max_length=4000)] | None = None


__all__ = ["LeadAdminOut", "LeadCreateIn", "LeadCreatedOut", "LeadUpdateIn"]
