"""A lead: one message from a customer, answered out of band (API.md §25, §35).

Deliberately flat — topic, message, how to reach the sender. The operator
reads it, sets a status, writes a note and picks up the phone; there is no
in-app conversation and no per-source field schema (ARCHITECTURE.md §14 G1).
"""

import enum
import uuid

from sqlalchemy import CheckConstraint, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Entity
from app.db.mixins import SingletonMixin, singleton_check


class LeadStatus(enum.StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    DONE = "done"


def _status_check() -> CheckConstraint:
    values = ", ".join(f"'{status}'" for status in LeadStatus)
    return CheckConstraint(f"status IN ({values})", name="lead_status")


class Lead(Entity):
    __tablename__ = "leads"
    __table_args__ = (_status_check(),)

    #: A free code like ``"payment"`` — what the app's form offered.
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160))
    #: Phone or email, as the sender wrote it — the operator dials it, code
    #: does not.
    contact: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{LeadStatus.NEW}'")
    )
    #: The operator's working note — "called, no answer", that sort of thing.
    note: Mapped[str | None] = mapped_column(Text)

    #: Set when the sender was signed in. No foreign key: ``customers`` is
    #: another module, and a cross-module FK is the database's version of
    #: importing its ``models.py`` (the ``customer_cards`` choice).
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), index=True
    )


class SupportTopic(Entity):
    """What the app's lead form offers in its topic picker (API.md §25, §35).

    The lead keeps the collapsed text, not a key into this table — the same
    verbatim choice as deletion reasons (§19) — so no draft/publish state and
    no FK: hiding a topic is the soft delete, and history survives it.
    """

    __tablename__ = "support_topics"

    name: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: The panel's chosen order; the public list follows it.
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class SupportContact(Entity, SingletonMixin):
    """How to reach support directly, next to the topic form (API.md §25, §35).

    A second, narrower door than ``settings.Site.support_phone/support_email``:
    that document describes the installation as a whole, this one is what the
    "contact us" screen shows and includes a channel (Telegram) the site
    document does not carry. One row, config-in-DB (CLAUDE.md) so a client
    changing their handle or hours needs no deploy.
    """

    __tablename__ = "leads_support_contact"
    __table_args__ = (singleton_check(),)

    #: Telegram handle, e.g. ``"@brand_support"`` — the operator's own
    #: convention, not validated here.
    support_username: Mapped[str | None] = mapped_column(String(160))
    support_phone: Mapped[str | None] = mapped_column(String(32))
    support_email: Mapped[str | None] = mapped_column(String(255))
    #: Translated free text, e.g. ``{"uz": "Dush-Juma 09:00-18:00"}`` — shown
    #: to the customer, so it follows the same shape as ``SupportTopic.name``.
    #: Optional: an empty dict means "not shown".
    working_hours: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


__all__ = ["Lead", "LeadStatus", "SupportContact", "SupportTopic"]
