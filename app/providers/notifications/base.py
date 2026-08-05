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
        context: dict[str, Any] | None = None,
    ) -> None: ...

    async def verify(self) -> bool:
        """Check the configuration — behind ``integrations/notifications/test/``."""
        ...


__all__ = ["Channel", "Notifier"]
