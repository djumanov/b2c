"""Shapes for ``/public/leads/`` (API.md §25) and ``/admin/leads/`` (§35)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.core.i18n import SUPPORTED_LANGUAGES, Translated
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


# --- support topics (API.md §25, §35) ---------------------------------------------


def _check_translated(value: Translated) -> Translated:
    """The cms module's rule, repeated: schemas are not a module's ``service``
    door, so importing it from there would cross the one line ARCHITECTURE.md
    draws."""
    filtered = {
        lang: text for lang, text in value.items() if lang in SUPPORTED_LANGUAGES
    }
    if not any(text.strip() for text in filtered.values()):
        raise ValueError(
            "at least one language must have a value; "
            f"this release serves {', '.join(SUPPORTED_LANGUAGES)}"
        )
    return filtered


class SupportTopicIn(BaseModel):
    name: Translated
    #: Omitted means "append at the end" — the service picks max + 1.
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def _known_languages(cls, value: Translated) -> Translated:
        return _check_translated(value)


class SupportTopicUpdateIn(BaseModel):
    #: Merged per language, like every translated PATCH (API.md §30).
    name: Translated | None = None
    sort_order: int | None = None

    @field_validator("name")
    @classmethod
    def _known_languages(cls, value: Translated | None) -> Translated | None:
        return None if value is None else _check_translated(value)


class SupportTopicAdminOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: Translated
    sort_order: int
    created_at: datetime
    updated_at: datetime


class SupportTopicPublicOut(BaseModel):
    id: uuid.UUID
    name: str
    #: The language the text actually came back in (API.md §7).
    lang: str | None


__all__ = [
    "LeadAdminOut",
    "LeadCreateIn",
    "LeadCreatedOut",
    "LeadUpdateIn",
    "SupportTopicAdminOut",
    "SupportTopicIn",
    "SupportTopicPublicOut",
    "SupportTopicUpdateIn",
]
