"""Shapes for ``/admin/content/*`` (API.md §30) and ``/public/content/*`` (§24).

The admin surface reads and writes translated fields as objects,
``{"uz": …, "ru": …}``; the public surface gets them collapsed to one language
and reports which one actually came back (API.md §7). That is why the two
sides have different models rather than one shared one.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.core.i18n import SUPPORTED_LANGUAGES, Translated


def _check_translated(value: Translated) -> Translated:
    filtered = {
        lang: text for lang, text in value.items() if lang in SUPPORTED_LANGUAGES
    }
    if not any(text.strip() for text in filtered.values()):
        raise ValueError(
            "at least one language must have a value; "
            f"this release serves {', '.join(SUPPORTED_LANGUAGES)}"
        )
    return filtered


# --- faq -------------------------------------------------------------------------


class FaqIn(BaseModel):
    question: Translated
    answer: Translated
    category: Annotated[str, Field(max_length=64)] | None = None

    @field_validator("question", "answer")
    @classmethod
    def _known_languages(cls, value: Translated) -> Translated:
        return _check_translated(value)


class FaqUpdateIn(BaseModel):
    question: Translated | None = None
    answer: Translated | None = None
    #: An empty string clears the category, like the site settings do.
    category: Annotated[str, Field(max_length=64)] | None = None

    @field_validator("question", "answer")
    @classmethod
    def _known_languages(cls, value: Translated | None) -> Translated | None:
        return None if value is None else _check_translated(value)


class FaqAdminOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    question: Translated
    answer: Translated
    category: str | None
    status: str
    sort_order: int
    created_at: datetime
    updated_at: datetime


class FaqPublicOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    category: str | None
    #: The language the text actually came back in (API.md §7).
    lang: str | None


# --- reorder ---------------------------------------------------------------------


class ReorderItemIn(BaseModel):
    id: uuid.UUID
    order: int


__all__ = [
    "FaqAdminOut",
    "FaqIn",
    "FaqPublicOut",
    "FaqUpdateIn",
    "ReorderItemIn",
]
