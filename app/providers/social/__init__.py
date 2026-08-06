"""The social verifier override — the seam tests hang from.

Same shape and same reasoning as ``providers/notifications/__init__.py``:
**which verifier to use is a settings question, and settings belong to a
module.** The client id a token must be addressed to lives in the database and
is edited from the panel, so the answer is
``integrations.service.social_verifier(session, provider)``. Answering it here
would mean a provider importing ``modules``, the one direction
ARCHITECTURE.md §4 does not allow.

What stays here is the override, which is the only way a test can sign somebody
in without a real Google token.
"""

from app.providers.social.base import SocialVerifier

_override: SocialVerifier | None = None


def set_verifier(verifier: SocialVerifier | None) -> None:
    """Pin the verifier, or pass ``None`` to go back to the configured one."""
    global _override
    _override = verifier


def get_override() -> SocialVerifier | None:
    """The pinned verifier, if there is one. ``None`` means "use the settings"."""
    return _override


__all__ = ["get_override", "set_verifier"]
