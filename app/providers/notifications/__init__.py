"""Which notifier the application sends through.

One accessor, so no caller has to know. Today it is always the log adapter;
when the ``integrations`` module lands it reads the installation's SMTP
settings and returns an ``SmtpNotifier`` instead, and nothing that calls
``get_notifier()`` changes (PROJECT.md §15, ARCHITECTURE.md §12).

``set_notifier`` is the test seam, matching ``db.redis.set_redis``.
"""

from app.providers.notifications.base import Notifier
from app.providers.notifications.log import LogNotifier

_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = LogNotifier()
    return _notifier


def set_notifier(notifier: Notifier | None) -> None:
    """Swap the notifier — used by tests, and by the integrations module once
    an installation has configured a real one."""
    global _notifier
    _notifier = notifier


__all__ = ["get_notifier", "set_notifier"]
