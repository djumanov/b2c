"""Translated fields and the fallback chain.

Translatable text is stored as ``{language: value}`` JSONB rather than in a
translation table: that is exactly the shape the admin API returns, so no read
has to join (ARCHITECTURE.md §10). The public API collapses it to one language.

**Not every language has to be filled in.** A missing translation resolves down
the chain — requested → the site's default → the first language that has a
value — and the response reports which language actually came back, so the
client can label it (API.md §7)::

    {"title": "Chegirma", "lang": "uz"}
"""

from dataclasses import dataclass
from typing import Final

#: ``{"uz": "Chegirma", "ru": "Скидка"}`` — not every key need be present.
Translated = dict[str, str]

#: uz + ru + en for the first release (PROJECT.md D8). The set a given
#: installation actually serves is a database setting, not this constant;
#: this is the vocabulary the parser recognises.
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = ("uz", "ru", "en")

DEFAULT_LANGUAGE: Final = "uz"


@dataclass(frozen=True, slots=True)
class Resolved:
    """A translated value and the language it actually came from."""

    value: str | None
    lang: str | None


def normalize_language(raw: str | None) -> str | None:
    """``"RU-ru"`` → ``"ru"``; unknown or empty → ``None``."""
    if not raw:
        return None
    code = raw.strip().lower().split("-", 1)[0]
    return code if code in SUPPORTED_LANGUAGES else None


def parse_accept_language(header: str | None) -> list[str]:
    """Languages from an ``Accept-Language`` header, best first.

    Quality values are honoured; unknown tags are dropped. ``"ru;q=0.8, en"``
    → ``["en", "ru"]``.
    """
    if not header:
        return []
    weighted: list[tuple[float, int, str]] = []
    for position, part in enumerate(header.split(",")):
        tag, _, params = part.strip().partition(";")
        code = normalize_language(tag)
        if code is None:
            continue
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        # Position breaks ties so equal-quality tags keep header order.
        weighted.append((-quality, position, code))
    seen: set[str] = set()
    ordered: list[str] = []
    for _, _, code in sorted(weighted):
        if code not in seen:
            seen.add(code)
            ordered.append(code)
    return ordered


def resolve(
    field: Translated | None,
    *,
    requested: str | None,
    default: str = DEFAULT_LANGUAGE,
    available: tuple[str, ...] | list[str] | None = None,
) -> Resolved:
    """Collapse a translated field to one language (API.md §7).

    Chain: ``requested`` → ``default`` → the first language in ``available``
    that has a non-empty value. ``available`` orders the last step; without it
    the field's own key order is used.

    Returns ``Resolved(None, None)`` when the field is empty or every value is
    blank — an absent translation is not an error.
    """
    if not field:
        return Resolved(None, None)

    order: list[str] = []
    for candidate in (requested, default):
        if candidate and candidate not in order:
            order.append(candidate)
    for candidate in available or field.keys():
        if candidate not in order:
            order.append(candidate)

    for lang in order:
        value = field.get(lang)
        if value:
            return Resolved(value, lang)
    return Resolved(None, None)


def resolve_value(
    field: Translated | None,
    *,
    requested: str | None,
    default: str = DEFAULT_LANGUAGE,
    available: tuple[str, ...] | list[str] | None = None,
) -> str | None:
    """``resolve`` when only the text is wanted."""
    return resolve(
        field, requested=requested, default=default, available=available
    ).value


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "Resolved",
    "Translated",
    "normalize_language",
    "parse_accept_language",
    "resolve",
    "resolve_value",
]
