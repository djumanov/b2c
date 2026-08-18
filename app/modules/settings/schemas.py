"""Shapes for ``/admin/settings/*`` (API.md §28) and ``site-config`` (§17).

Admin reads and writes translated fields as objects, ``{"uz": …, "ru": …}``;
the public surface gets them collapsed to one language (API.md §7). That is why
the two sides have different models rather than one shared one.

``PATCH`` is partial (API.md §8), so every input field defaults to ``None`` and
``None`` means "leave it alone" — which is why an explicit ``null`` cannot be
used to clear a field here. Where clearing matters, the contract gives the
field its own empty value: a colour is removed by omitting it from the object.
"""

import re
import uuid
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.i18n import SUPPORTED_LANGUAGES, Translated
from app.modules.settings.defaults import COLOR_KEYS, FONT_CHOICES, LOCKED_FEATURES

_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _check_languages(codes: list[str]) -> list[str]:
    unknown = [code for code in codes if code not in SUPPORTED_LANGUAGES]
    if unknown:
        raise ValueError(
            f"unsupported language(s): {', '.join(unknown)}; "
            f"this release serves {', '.join(SUPPORTED_LANGUAGES)}"
        )
    if not codes:
        raise ValueError("at least one language must be available")
    return list(dict.fromkeys(codes))


def _check_translated(value: Translated) -> Translated:
    return {lang: text for lang, text in value.items() if lang in SUPPORTED_LANGUAGES}


# --- branding -------------------------------------------------------------------


class BrandingOut(BaseModel):
    colors: dict[str, str]
    font_family: str | None
    logo_id: uuid.UUID | None
    favicon_id: uuid.UUID | None
    app_icon_id: uuid.UUID | None
    app_name: str | None
    #: Resolved for the panel's preview, so it does not have to build the URL.
    logo_url: str | None = None
    favicon_url: str | None = None
    app_icon_url: str | None = None


class BrandingIn(BaseModel):
    colors: dict[str, str] | None = None
    font_family: str | None = None
    logo_id: uuid.UUID | None = None
    favicon_id: uuid.UUID | None = None
    app_icon_id: uuid.UUID | None = None
    app_name: Annotated[str, Field(max_length=64)] | None = None

    @field_validator("colors")
    @classmethod
    def _valid_colors(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        unknown = set(value) - COLOR_KEYS
        if unknown:
            raise ValueError(
                f"unknown colour(s): {', '.join(sorted(unknown))}; "
                f"the site renders {', '.join(sorted(COLOR_KEYS))}"
            )
        for key, colour in value.items():
            if not _HEX_COLOR.match(colour):
                raise ValueError(f"{key} must be a hex colour like #0A5CFF")
        return value

    @field_validator("font_family")
    @classmethod
    def _known_font(cls, value: str | None) -> str | None:
        if value is not None and value not in FONT_CHOICES:
            raise ValueError(f"unknown font; choose one of: {', '.join(FONT_CHOICES)}")
        return value


# --- site -----------------------------------------------------------------------


class SiteOut(BaseModel):
    name: Translated
    domain: str | None
    support_phone: str | None
    support_email: str | None
    social: dict[str, str]


class SiteIn(BaseModel):
    name: Translated | None = None
    domain: Annotated[str, Field(max_length=255)] | None = None
    support_phone: Annotated[str, Field(max_length=32)] | None = None
    support_email: EmailStr | None = None
    social: dict[str, str] | None = None

    @field_validator("name")
    @classmethod
    def _known_languages(cls, value: Translated | None) -> Translated | None:
        return None if value is None else _check_translated(value)


# --- languages and currencies -----------------------------------------------------


class LanguagesOut(BaseModel):
    default: str
    available: list[str]


class LanguagesIn(BaseModel):
    default: str | None = None
    available: list[str] | None = None

    @field_validator("available")
    @classmethod
    def _supported(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_languages(value)

    @field_validator("default")
    @classmethod
    def _supported_default(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_LANGUAGES:
            raise ValueError(f"unsupported language: {value}")
        return value


class OrderSettingsOut(BaseModel):
    """The ticketing dials (API.md §28)."""

    #: Minutes before the provider's deadline that ticketing must be done by.
    ticket_margin_minutes: int
    #: How much dearer a fare may become between paying and ticketing.
    reprice_tolerance: Decimal
    #: How long an unpaid order is held when the provider states no deadline.
    hold_fallback_minutes: int


class OrderSettingsIn(BaseModel):
    ticket_margin_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    reprice_tolerance: Decimal | None = Field(default=None, ge=0)
    hold_fallback_minutes: int | None = Field(default=None, gt=0, le=7 * 24 * 60)


class CurrenciesOut(BaseModel):
    default: str
    available: list[str]


class CurrenciesIn(BaseModel):
    default: str | None = None
    available: list[str] | None = None

    @field_validator("default")
    @classmethod
    def _iso_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if not _CURRENCY.match(code):
            raise ValueError("a currency is a three-letter ISO 4217 code")
        return code

    @field_validator("available")
    @classmethod
    def _iso_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        codes = [code.strip().upper() for code in value]
        for code in codes:
            if not _CURRENCY.match(code):
                raise ValueError(f"{code!r} is not a three-letter ISO 4217 code")
        if not codes:
            raise ValueError("at least one currency must be available")
        return list(dict.fromkeys(codes))


# --- features and products ---------------------------------------------------------


class FeaturesOut(BaseModel):
    flags: dict[str, bool]


class FeaturesIn(BaseModel):
    flags: dict[str, bool]

    @field_validator("flags")
    @classmethod
    def _settable(cls, value: dict[str, bool]) -> dict[str, bool]:
        locked = sorted(set(value) & LOCKED_FEATURES)
        if locked:
            raise ValueError(
                f"{', '.join(locked)} is not part of this product and cannot "
                "be switched on"
            )
        return value


class ProductOut(BaseModel):
    code: str
    enabled: bool


# --- the public document (API.md §17) -----------------------------------------------


class SiteConfigOut(BaseModel):
    """One document, fetched first by the website and the app.

    Its shape is dictated by API.md §17 rather than by our tables, which is why
    it is assembled rather than serialised from a model.
    """

    site: dict[str, Any]
    branding: dict[str, Any]
    languages: LanguagesOut
    currencies: CurrenciesOut
    products: list[ProductOut]
    payment_methods: list[dict[str, Any]]
    menu: list[dict[str, Any]]
    features: dict[str, bool]
    #: Which language the translated fields actually came back in (API.md §7).
    lang: str | None = None


__all__ = [
    "BrandingIn",
    "BrandingOut",
    "CurrenciesIn",
    "CurrenciesOut",
    "FeaturesIn",
    "FeaturesOut",
    "LanguagesIn",
    "LanguagesOut",
    "ProductOut",
    "SiteConfigOut",
    "SiteIn",
    "SiteOut",
]
