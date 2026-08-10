"""Sending mail through the client's own relay — the SMTP adapter (D6).

The settings behind it are panel-configured and live in the database, so this
class is handed a config rather than reading one: the port stays ignorant of
the ``integrations`` module, and a module never imports another module's
models (ARCHITECTURE.md §4).

**Standard library, in a thread.** ``smtplib`` is blocking, and the obvious
alternative is another dependency. This path sends a password-reset link and,
later, a login code — a handful of messages a minute at the very most — so a
thread per message is not a cost worth taking a dependency to avoid. The
stdlib is also typed, which keeps mypy strict quiet.
"""

import asyncio
import smtplib
import ssl
from contextlib import suppress
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from app.core.logging import get_logger
from app.providers.notifications.base import Channel

logger = get_logger(__name__)

#: Long enough for a slow relay's greeting, short enough that a password-reset
#: request does not hang on a host that is simply gone.
TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True, repr=False)
class SmtpConfig:
    """Everything needed to reach the relay. ``password`` is in the clear.

    ``repr`` is written by hand, for the same reason as
    ``integrations.service.ActiveGtsCredential``: a dataclass carrying a secret
    ends up in a structlog ``exc_info`` or a failing assertion eventually, and
    the generated one would print the password there.
    """

    host: str
    port: int
    tls: str
    from_address: str
    from_name: str | None = None
    username: str | None = None
    password: str | None = None

    def __repr__(self) -> str:
        return f"SmtpConfig(host={self.host!r}, port={self.port}, tls={self.tls!r})"

    @property
    def sender(self) -> str:
        return (
            f"{self.from_name} <{self.from_address}>"
            if self.from_name
            else self.from_address
        )


def _close(client: smtplib.SMTP) -> None:
    """Hang up. A relay that has already gone is not the caller's problem.

    ``quit`` raises when the connection is already broken — by which point the
    message has been accepted, so reporting it would turn a delivered mail into
    a failed one.
    """
    with suppress(smtplib.SMTPException, OSError):
        client.quit()


class SmtpNotifier:
    """The ``Notifier`` port over SMTP."""

    channel: Channel = Channel.EMAIL

    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    def _connect(self) -> smtplib.SMTP:
        """Open a connection and authenticate. Blocking — call in a thread."""
        config = self._config
        client: smtplib.SMTP
        if config.tls == "ssl":
            client = smtplib.SMTP_SSL(
                config.host,
                config.port,
                timeout=TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(config.host, config.port, timeout=TIMEOUT_SECONDS)
            if config.tls == "starttls":
                client.starttls(context=ssl.create_default_context())
        client.ehlo()
        # A relay on the client's own network often takes mail from its own
        # hosts without asking who they are, so there may be nothing to send.
        if config.username:
            client.login(config.username, config.password or "")
        return client

    def _deliver(
        self, recipient: str, subject: str | None, body: str, html: str | None
    ) -> None:
        message = EmailMessage()
        message["From"] = self._config.sender
        message["To"] = recipient
        message["Subject"] = subject or ""
        message.set_content(body)
        if html is not None:
            # ``multipart/alternative``, plain part first: a client that cannot
            # render HTML — or a person who has turned it off — still gets the
            # code. ``add_alternative`` is what turns the message into one.
            message.add_alternative(html, subtype="html")

        client = self._connect()
        try:
            client.send_message(message)
        finally:
            _close(client)

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        html: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        await asyncio.to_thread(self._deliver, recipient, subject, body, html)
        logger.info(
            "notification_sent", channel=self.channel.value, recipient=recipient
        )

    def _probe(self) -> None:
        client = self._connect()
        try:
            client.noop()
        finally:
            _close(client)

    async def verify(self) -> bool:
        """Reach the relay and authenticate, without sending anything.

        Returns rather than raises: "these settings do not work" is an answer,
        not a failure of the call that asked (API.md §29).
        """
        try:
            await asyncio.to_thread(self._probe)
        except (OSError, smtplib.SMTPException) as exc:
            logger.warning("smtp_verify_failed", error=str(exc)[:200])
            return False
        return True


__all__ = ["TIMEOUT_SECONDS", "SmtpConfig", "SmtpNotifier"]
