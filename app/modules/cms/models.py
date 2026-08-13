"""Content the panel writes and the site reads (API.md §24, §30).

Translatable text is ``JSONB {language: value}`` (API.md §7); a page body is
markdown per language (§30 "Sahifa tanasi") — rendering happens on the client.
Every resource carries a ``draft | published`` status: the panel edits in the
open, the public surface only ever sees what has been published.
"""

import enum

from sqlalchemy import CheckConstraint, Integer, String, text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity
from app.db.mixins import soft_delete_unique_index


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


class FunFact(Entity):
    """One aviation/travel fact; the flight search response shows a random
    published one while the client polls ``offers/`` (API.md §20, §30).

    No ``sort_order``: selection is random, so an order would answer a
    question nobody asks. The ``text`` attribute shadows sqlalchemy's
    ``text()`` inside this class body — hence the ``sql_text`` alias.
    """

    __tablename__ = "fun_facts"
    __table_args__ = (_status_check(),)

    text: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=sql_text(f"'{ContentStatus.DRAFT}'")
    )


class Page(Entity):
    """A static page — the fixed set ``service.FIXED_PAGE_SLUGS``.

    Each page has its own route on both surfaces (API.md §24, §30); the slug
    column keys the row behind them. Rows are not seeded: the first admin PUT
    creates one, and a page never written or not published answers 404
    publicly. Company contact details (name, email, website, social links) are
    not page content either — they live in the site settings and are folded
    into the public "about" response by the service layer (API.md §30, §17).
    """

    __tablename__ = "pages"
    __table_args__ = (
        _status_check(),
        soft_delete_unique_index("pages", "slug"),
    )

    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Markdown per language.
    body: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{ContentStatus.DRAFT}'")
    )


__all__ = ["ContentStatus", "Faq", "FunFact", "Page"]
