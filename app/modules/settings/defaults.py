"""What an installation looks like before anybody has configured it.

These are **defaults, not settings**: the client changes every one of them from
the panel, and none of them lives in the environment (PROJECT.md §7). They sit
here so a fresh installation renders as something recognisable rather than as a
blank page, and so the first boot needs no data migration.

The font list is closed on purpose. PROJECT.md §7 says the font is "chosen from
a predefined list" — the site ships the faces, so a free-text value would name
one the browser cannot load.
"""

from typing import Final

from app.core.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

#: A neutral blue that is nobody's brand, so an unconfigured installation does
#: not look like it belongs to whoever the developer had in mind.
DEFAULT_COLORS: Final[dict[str, str]] = {
    "primary": "#0A5CFF",
    "accent": "#FF7A00",
    "background": "#FFFFFF",
}

#: Which keys ``colors`` may hold. The site binds exactly these to CSS
#: variables, so an extra one would be stored and never rendered.
COLOR_KEYS: Final[frozenset[str]] = frozenset(DEFAULT_COLORS)

FONT_CHOICES: Final[tuple[str, ...]] = (
    "Inter",
    "Roboto",
    "Open Sans",
    "Montserrat",
    "Manrope",
)
DEFAULT_FONT: Final = "Inter"

DEFAULT_LANGUAGES: Final[list[str]] = list(SUPPORTED_LANGUAGES)
DEFAULT_CURRENCY: Final = "UZS"
DEFAULT_CURRENCIES: Final[list[str]] = [DEFAULT_CURRENCY]

#: The sections a client may switch off, and how they start. Every key the
#: contract's ``features`` object may contain is here — an unknown one is a
#: typo in the panel, and answering 422 says so instead of storing it.
FEATURE_DEFAULTS: Final[dict[str, bool]] = {
    "blog": True,
    "promotions": True,
    "faq": True,
    "contacts": True,
    "banners": True,
    "popular_directions": True,
    "feedbacks": True,
    #: Out of scope for this product (PROJECT.md §3, API.md §41). It appears in
    #: ``site-config`` because the contract's example shows it, and it cannot
    #: be switched on.
    "loyalty": False,
}

#: Flags that exist in the response but cannot be set.
LOCKED_FEATURES: Final[frozenset[str]] = frozenset({"loyalty"})


__all__ = [
    "COLOR_KEYS",
    "DEFAULT_COLORS",
    "DEFAULT_CURRENCIES",
    "DEFAULT_CURRENCY",
    "DEFAULT_FONT",
    "DEFAULT_LANGUAGE",
    "DEFAULT_LANGUAGES",
    "FEATURE_DEFAULTS",
    "FONT_CHOICES",
    "LOCKED_FEATURES",
]
