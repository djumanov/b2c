"""The fallback chain and what `lang` reports back (API.md §7)."""

import pytest

from app.core.i18n import (
    normalize_language,
    parse_accept_language,
    resolve,
    resolve_value,
)

FULL = {"uz": "Chegirma", "ru": "Скидка", "en": "Discount"}
PARTIAL = {"ru": "Скидка"}


def test_requested_language_wins() -> None:
    result = resolve(FULL, requested="ru", default="uz")

    assert result.value == "Скидка"
    assert result.lang == "ru"


def test_falls_back_to_site_default() -> None:
    result = resolve(PARTIAL, requested="en", default="uz", available=("uz", "ru"))

    # Neither en nor uz is filled in, so the chain reaches the first available.
    assert result.value == "Скидка"
    assert result.lang == "ru"


def test_default_is_used_before_other_languages() -> None:
    result = resolve(FULL, requested="fr", default="en")

    assert result.lang == "en"


def test_reported_language_is_what_came_back_not_what_was_asked() -> None:
    """The client has to be able to label a fallback (API.md §7)."""
    result = resolve(PARTIAL, requested="uz", default="uz")

    assert result.lang == "ru"
    assert result.lang != "uz"


def test_empty_strings_are_treated_as_missing() -> None:
    result = resolve({"uz": "", "ru": "Скидка"}, requested="uz", default="uz")

    assert result.value == "Скидка"
    assert result.lang == "ru"


def test_no_translation_at_all_is_not_an_error() -> None:
    assert resolve(None, requested="uz").value is None
    assert resolve({}, requested="uz").lang is None
    assert resolve({"uz": ""}, requested="uz").value is None


def test_available_orders_the_last_step() -> None:
    field = {"en": "Discount", "ru": "Скидка"}

    ru_first = resolve(field, requested="uz", default="uz", available=("ru", "en"))
    en_first = resolve(field, requested="uz", default="uz", available=("en", "ru"))

    assert ru_first.lang == "ru"
    assert en_first.lang == "en"


def test_resolve_value_returns_just_the_text() -> None:
    assert resolve_value(FULL, requested="en") == "Discount"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("uz", "uz"),
        ("RU", "ru"),
        ("en-US", "en"),
        ("ru-RU", "ru"),
        ("fr", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_language(raw: str | None, expected: str | None) -> None:
    assert normalize_language(raw) == expected


@pytest.mark.parametrize(
    "header,expected",
    [
        ("ru", ["ru"]),
        ("ru,en;q=0.8", ["ru", "en"]),
        ("ru;q=0.8, en", ["en", "ru"]),
        ("fr,de", []),
        ("en-GB,uz;q=0.9,fr;q=0.5", ["en", "uz"]),
        ("", []),
        (None, []),
    ],
)
def test_parse_accept_language(header: str | None, expected: list[str]) -> None:
    assert parse_accept_language(header) == expected
