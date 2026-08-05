"""What the client can change without a deploy — ARCHITECTURE.md §5, §10.

Five singleton tables and one list. Each singleton describes *the*
installation, so a second row would be a question with no right answer;
``SingletonMixin`` plus ``singleton_check`` makes one impossible in the
database rather than in a convention.

Translatable text is ``JSONB {language: value}`` (API.md §7): that is the shape
the admin API returns, so no read has to join, and the public surface collapses
it to one language on the way out.

Rows are created on first read, not by a data migration. Defaults belong next
to the fields they default, where somebody changing one can see them, and a
migration that seeds data is frozen the moment it ships.
"""

import uuid
from typing import Any

from sqlalchemy import Boolean, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.i18n import DEFAULT_LANGUAGE
from app.db.base import Base, Entity
from app.db.mixins import (
    SingletonMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    singleton_check,
)


class Branding(Entity, SingletonMixin):
    """Logo, colours, font — the headline promise of the product (§1)."""

    __tablename__ = "branding"
    __table_args__ = (singleton_check(),)

    #: ``{"primary": "#0A5CFF", "accent": "#FF7A00", "background": "#FFFFFF"}``.
    #: The site turns these into CSS variables at runtime (PROJECT.md §7).
    colors: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    font_family: Mapped[str | None] = mapped_column(String(64), nullable=True)

    logo_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    favicon_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    #: Stored here, but they only take effect in the **next build** — both
    #: stores tie an icon and a name to the submitted binary (PROJECT.md §7).
    app_icon_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    app_name: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Site(Entity, SingletonMixin):
    """Name, domain, how to reach the client."""

    __tablename__ = "site"
    __table_args__ = (singleton_check(),)

    #: Translated: the same installation is "Brand Travel" in one language and
    #: something else in another.
    name: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    support_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    support_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: ``{"telegram": "https://t.me/…", "instagram": "…"}``.
    social: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class Languages(Entity, SingletonMixin):
    """Which of uz/ru/en this installation serves (D8, API.md §7)."""

    __tablename__ = "languages"
    __table_args__ = (singleton_check(),)

    default: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text(f"'{DEFAULT_LANGUAGE}'")
    )
    #: Ordered — the fallback chain walks it after the requested and default
    #: languages have both missed (API.md §7).
    available: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class Currencies(Entity, SingletonMixin):
    """Base currency and what else may be displayed (PROJECT.md §12)."""

    __tablename__ = "currencies"
    __table_args__ = (singleton_check(),)

    default: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default=text("'UZS'")
    )
    available: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )


class Features(Entity, SingletonMixin):
    """Sections the client can switch off — blog, reviews and the like."""

    __tablename__ = "features"
    __table_args__ = (singleton_check(),)

    flags: Mapped[dict[str, bool]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class ProductSetting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One vertical and whether it is on sale. **Read-only from the panel.**

    Which verticals an installation may sell follows from the client's GTS
    contract, not from a switch in the panel (API.md §28, PROJECT.md §5). The
    rows exist so a beat task can keep them in step with GTS once there is a
    GTS to ask (ARCHITECTURE.md §14 A5); until then they are seeded on first
    read and all enabled.

    No soft delete: a vertical is never removed, only disabled.
    """

    __tablename__ = "product_settings"

    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    #: Panel ordering, so a client's most-sold vertical can lead.
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


#: Every settings table, for the code that has to touch all of them — the
#: cache purge does not care which one changed.
SINGLETONS: tuple[type[Any], ...] = (Branding, Site, Languages, Currencies, Features)


__all__ = [
    "SINGLETONS",
    "Branding",
    "Currencies",
    "Features",
    "Languages",
    "ProductSetting",
    "Site",
]
