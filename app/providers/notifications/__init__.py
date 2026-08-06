"""The notifier override — the seam tests and the log fallback hang from.

**Which notifier to use is a settings question, and settings belong to a
module.** This package used to answer it itself, with a process-global built on
first use. That was wrong twice over: a global set by one worker is invisible
to the other three, and it cannot re-read a relay the owner has just changed.
Worse, answering it here would mean a provider importing ``modules`` — the one
direction ARCHITECTURE.md §4 does not allow.

So the choice moved to ``integrations.service.notifier(session)``, which reads
the row and builds an ``SmtpNotifier`` or falls back to ``LogNotifier``. What
stays here is the override: ``set_notifier`` lets a test — or anything else
that must not touch the real relay — pin the answer.
"""

from app.providers.notifications.base import Notifier

_override: Notifier | None = None


def set_notifier(notifier: Notifier | None) -> None:
    """Pin the notifier, or pass ``None`` to go back to the configured one."""
    global _override
    _override = notifier


def get_override() -> Notifier | None:
    """The pinned notifier, if there is one. ``None`` means "use the settings"."""
    return _override


__all__ = ["get_override", "set_notifier"]
