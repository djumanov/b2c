"""Nothing sensitive reaches a log line (PROJECT.md §13).

Card data must never be stored or logged, and integration credentials would
otherwise land in a log the first time somebody logs a config object.
"""

from app.core.logging import redact_secrets


def _redact(**event: object) -> dict[str, object]:
    return dict(redact_secrets(None, "info", event))


def test_sensitive_keys_are_dropped() -> None:
    result = _redact(event="login", password="hunter2", email="a@b.uz")

    assert result["password"] == "[redacted]"
    assert result["email"] == "a@b.uz"


def test_nested_secrets_are_dropped() -> None:
    result = _redact(config={"gts": {"secret_key": "abc", "url": "https://x"}})

    config = result["config"]
    assert isinstance(config, dict)
    assert config["gts"]["secret_key"] == "[redacted]"
    assert config["gts"]["url"] == "https://x"


def test_secrets_inside_lists_are_dropped() -> None:
    result = _redact(providers=[{"code": "payme", "api_key": "live_123"}])

    providers = result["providers"]
    assert isinstance(providers, list)
    assert providers[0]["api_key"] == "[redacted]"
    assert providers[0]["code"] == "payme"


def test_a_card_number_in_free_text_is_masked() -> None:
    """No key names it, so the pattern has to catch it."""
    result = _redact(event="payment failed for card 4111 1111 1111 1111")

    assert "4111" not in str(result["event"])
    assert "[redacted]" in str(result["event"])


def test_ordinary_numbers_survive() -> None:
    result = _redact(event="order 12345 total 250000")

    assert result["event"] == "order 12345 total 250000"


def test_authorization_header_is_dropped() -> None:
    result = _redact(authorization="Bearer eyJhbGciOi...")

    assert result["authorization"] == "[redacted]"
