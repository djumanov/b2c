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

#: **Not here on purpose:** the currency. This file holds what the panel may
#: change and what it starts at; the currency is neither — it is
#: ``core.money.CURRENCY``, a constant, because a second one needs an exchange
#: rate rather than a form field.

#: The sections a client may switch off, and how they start. Every key the
#: contract's ``features`` object may contain is here — an unknown one is a
#: typo in the panel, and answering 422 says so instead of storing it.
#:
#: A ``false`` here is not decoration: ``api.deps.RequireFeature`` turns the
#: matching routes into ``404`` on both surfaces (API.md §28). Which is why
#: adding a key is a contract change, not a line in a dict.
#:
#: **Not here on purpose:** the five verticals. What an installation may sell
#: comes from its GTS agreement and lives in ``product_settings``, read-only
#: from the panel (API.md §28, PROJECT.md §5).
FEATURE_DEFAULTS: Final[dict[str, bool]] = {
    # --- content (the `cms` and `feedback` modules) ---
    "blog": True,
    "promotions": True,
    "faq": True,
    "contacts": True,
    "banners": True,
    "popular_directions": True,
    "feedbacks": True,
    # --- whole modules ---
    #: Deliberately not "promo": ``promotions`` above is the CMS resource, and
    #: the two sit next to each other on the same panel screen.
    "promo_codes": True,
    "leads": True,
    "reports": True,
    #: Templates and mass sending (API.md §36). Transactional mail — OTP, a
    #: password reset — does **not** hang from this; it goes through
    #: ``integrations`` and is core.
    "broadcast": True,
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
    "DEFAULT_FONT",
    "DEFAULT_LANGUAGE",
    "DEFAULT_LANGUAGES",
    "FEATURE_DEFAULTS",
    "FONT_CHOICES",
    "LOCKED_FEATURES",
]
