"""Content the panel writes and the site reads (API.md §24, §30).

Translatable text is ``JSONB {language: value}`` (API.md §7). Every resource
carries a ``draft | published`` status: the panel edits in the open, the
public surface only ever sees what has been published.
"""

import enum

from sqlalchemy import CheckConstraint, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity


class ContentStatus(enum.StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


def _status_check() -> CheckConstraint:
    values = ", ".join(f"'{status}'" for status in ContentStatus)
    return CheckConstraint(f"status IN ({values})", name="content_status")


class Faq(Entity):
    """One question and its answer, ordered by hand from the panel."""

    __tablename__ = "faqs"
    __table_args__ = (_status_check(),)

    question: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    answer: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: A free code like ``"payment"`` — grouping, not a table of its own.
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{ContentStatus.DRAFT}'")
    )
    #: The panel's chosen order (``reorder/``); the public list follows it.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


__all__ = ["ContentStatus", "Faq"]
