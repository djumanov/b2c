"""The ``Notifier`` port. SMTP only in the first release (D6).

SMS and push are added as adapters behind this same interface, without the
calling code changing (ARCHITECTURE.md §12).
"""

from enum import StrEnum
from typing import Any, Protocol


class Channel(StrEnum):
    EMAIL = "email"
    #: Not in the first release — see API.md §41.
    SMS = "sms"
    PUSH = "push"


class Notifier(Protocol):
    channel: Channel

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        html: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Deliver one message.

        ``body`` is the message; ``html`` is the same message dressed up, and
        an adapter that has no use for it drops it. Email sends both and lets
        the client pick, SMS and push have nowhere to put it — which is why it
        is optional here rather than a second method only one adapter answers.
        """
        ...

    async def verify(self) -> bool:
        """Check the configuration — behind ``integrations/notifications/test/``."""
        ...


__all__ = ["Channel", "Notifier"]
