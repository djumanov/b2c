"""A ``Notifier`` that writes to the log instead of sending anything.

The password-reset flow needs somewhere to hand a message **before** the
``integrations`` module exists to hold SMTP credentials (PROJECT.md §15,
1-bosqich). Rather than leave the flow half-written until then, it is finished
against the port and this adapter stands in; ``providers/notifications/smtp.py``
replaces it without the calling code changing (ARCHITECTURE.md §12).

The body — which carries the reset token — is logged at **debug** only. A
production installation runs at ``INFO`` (``.env``: ``LOG_LEVEL``), so the
token does not reach its logs; a developer who wants the link raises the level.
"""

from typing import Any

from app.core.logging import get_logger
from app.providers.notifications.base import Channel

logger = get_logger(__name__)


class LogNotifier:
    """Delivery for installations with no mail service configured yet."""

    channel: Channel = Channel.EMAIL

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        logger.info(
            "notification_not_sent",
            channel=self.channel.value,
            recipient=recipient,
            subject=subject,
            reason="no mail provider configured yet",
        )
        logger.debug("notification_body", recipient=recipient, body=body)

    async def verify(self) -> bool:
        """Nothing to reach, so nothing can be wrong with reaching it."""
        return True


__all__ = ["LogNotifier"]
