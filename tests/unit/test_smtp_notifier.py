"""The SMTP adapter, with the socket replaced.

What is being pinned down is the shape of the conversation with the relay:
which of the three TLS modes opens which kind of connection, that a relay
needing no authentication is never handed a login, and that a refusal comes
back as ``False`` rather than as an exception — because the endpoint above it
answers `200` with the reason (API.md §29).
"""

import smtplib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.providers.notifications.smtp import SmtpConfig, SmtpNotifier

CONFIG = SmtpConfig(
    host="smtp.brand.uz",
    port=587,
    tls="starttls",
    from_address="no-reply@brand.uz",
    from_name="Brand Travel",
    username="no-reply@brand.uz",
    password="smtp-secret-1a2b",
)


def _client() -> MagicMock:
    return MagicMock(spec=smtplib.SMTP)


# --- the three TLS modes -------------------------------------------------------------


async def test_starttls_upgrades_a_plain_connection() -> None:
    client = _client()
    with (
        patch("smtplib.SMTP", return_value=client) as plain,
        patch("smtplib.SMTP_SSL") as wrapped,
    ):
        await SmtpNotifier(CONFIG).verify()

    plain.assert_called_once()
    wrapped.assert_not_called()
    client.starttls.assert_called_once()


async def test_ssl_connects_wrapped_from_the_first_byte() -> None:
    client = _client()
    with (
        patch("smtplib.SMTP_SSL", return_value=client) as wrapped,
        patch("smtplib.SMTP") as plain,
    ):
        await SmtpNotifier(SmtpConfig(**{**_as_dict(CONFIG), "tls": "ssl"})).verify()

    wrapped.assert_called_once()
    plain.assert_not_called()
    client.starttls.assert_not_called()


async def test_none_leaves_the_connection_alone() -> None:
    client = _client()
    with patch("smtplib.SMTP", return_value=client):
        await SmtpNotifier(SmtpConfig(**{**_as_dict(CONFIG), "tls": "none"})).verify()

    client.starttls.assert_not_called()


# --- authentication -------------------------------------------------------------------


async def test_a_relay_that_wants_no_login_is_not_given_one() -> None:
    """Common for a relay inside the client's own network."""
    client = _client()
    anonymous = SmtpConfig(**{**_as_dict(CONFIG), "username": None, "password": None})
    with patch("smtplib.SMTP", return_value=client):
        await SmtpNotifier(anonymous).verify()

    client.login.assert_not_called()


async def test_credentials_are_passed_through_when_there_are_any() -> None:
    client = _client()
    with patch("smtplib.SMTP", return_value=client):
        await SmtpNotifier(CONFIG).verify()

    client.login.assert_called_once_with("no-reply@brand.uz", "smtp-secret-1a2b")


# --- sending --------------------------------------------------------------------------


async def test_a_message_carries_the_configured_sender() -> None:
    client = _client()
    with patch("smtplib.SMTP", return_value=client):
        await SmtpNotifier(CONFIG).send(
            recipient="agent@brand.uz", subject="Test message", body="hello"
        )

    message = client.send_message.call_args.args[0]
    assert message["From"] == "Brand Travel <no-reply@brand.uz>"
    assert message["To"] == "agent@brand.uz"
    assert message["Subject"] == "Test message"


async def test_a_message_with_no_html_stays_plain_text() -> None:
    client = _client()
    with patch("smtplib.SMTP", return_value=client):
        await SmtpNotifier(CONFIG).send(
            recipient="agent@brand.uz", subject="Test message", body="hello"
        )

    message = client.send_message.call_args.args[0]
    assert not message.is_multipart()
    assert message.get_content_type() == "text/plain"


async def test_html_travels_beside_the_text_not_instead_of_it() -> None:
    """The plain part is what a client that cannot render HTML falls back to."""
    client = _client()
    with patch("smtplib.SMTP", return_value=client):
        await SmtpNotifier(CONFIG).send(
            recipient="agent@brand.uz",
            subject="Kod",
            body="Kod: 1234",
            html="<p>Kod: 1234</p>",
        )

    message = client.send_message.call_args.args[0]
    assert message.get_content_type() == "multipart/alternative"
    parts = [part.get_content_type() for part in message.iter_parts()]  # type: ignore[attr-defined]
    # Plain first: a client picks the last part it understands.
    assert parts == ["text/plain", "text/html"]
    assert "1234" in message.get_body(("plain",)).get_content()  # type: ignore[union-attr]
    assert "<p>" in message.get_body(("html",)).get_content()  # type: ignore[union-attr]


async def test_the_sender_is_the_bare_address_when_no_name_is_set() -> None:
    nameless = SmtpConfig(**{**_as_dict(CONFIG), "from_name": None})
    assert nameless.sender == "no-reply@brand.uz"


async def test_a_refused_message_reaches_the_caller() -> None:
    """``send`` raises — ``test/`` is what turns that into a readable answer."""
    client = _client()
    client.send_message.side_effect = smtplib.SMTPAuthenticationError(535, b"nope")

    with (
        patch("smtplib.SMTP", return_value=client),
        pytest.raises(smtplib.SMTPAuthenticationError),
    ):
        await SmtpNotifier(CONFIG).send(
            recipient="agent@brand.uz", subject="Test message", body="hello"
        )


# --- verify never raises -----------------------------------------------------------


async def test_verify_reports_a_refusal_rather_than_raising() -> None:
    client = _client()
    client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"nope")

    with patch("smtplib.SMTP", return_value=client):
        assert await SmtpNotifier(CONFIG).verify() is False


async def test_verify_reports_an_unreachable_host_rather_than_raising() -> None:
    with patch("smtplib.SMTP", side_effect=OSError("no route to host")):
        assert await SmtpNotifier(CONFIG).verify() is False


async def test_a_relay_that_hangs_up_on_quit_is_not_a_failure() -> None:
    """The message is accepted by then; the goodbye is not the caller's problem."""
    client = _client()
    client.quit.side_effect = smtplib.SMTPServerDisconnected("gone")

    with patch("smtplib.SMTP", return_value=client):
        assert await SmtpNotifier(CONFIG).verify() is True


# --- the secret does not print ------------------------------------------------------


def test_the_config_does_not_print_its_password() -> None:
    assert "smtp-secret-1a2b" not in repr(CONFIG)
    assert "smtp-secret-1a2b" not in str(CONFIG)


def _as_dict(config: SmtpConfig) -> dict[str, Any]:
    return {
        "host": config.host,
        "port": config.port,
        "tls": config.tls,
        "from_address": config.from_address,
        "from_name": config.from_name,
        "username": config.username,
        "password": config.password,
    }
