"""The ``SocialVerifier`` port — Google in the first release (PROJECT.md D5).

The browser gets an identity token from the provider and sends it here; this
side proves the token really came from that provider and reads the address out
of it. **No password and no provider session ever reaches this server** — the
only thing crossing the boundary is a signed assertion, which is why a social
sign-in needs no secret from the customer at all.

Built as a port rather than a Google-shaped function because Apple is coming
(D5: iOS requires Sign in with Apple wherever Google is offered), and
`ARCHITECTURE.md` §2 says that has to be a new adapter and a new row — not a
second branch in the sign-in flow.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class SocialProviderCode(StrEnum):
    GOOGLE = "google"


class SocialAuthError(Exception):
    """The token is not one this provider issued to this installation.

    Raised for every reason a token can be unacceptable — forged, expired,
    issued to a different client, or simply unparseable. The caller turns it
    into one `401`, and deliberately does not say which: a caller holding a bad
    token learns nothing useful from being told *how* it is bad.
    """


@dataclass(frozen=True, slots=True)
class SocialIdentity:
    """Who the provider says this is.

    ``email_verified`` is the field that matters. An address a provider has not
    itself confirmed proves nothing — anyone can put an unverified address on a
    profile — so the sign-in flow refuses it rather than creating an account
    somebody else owns.
    """

    subject: str
    email: str
    email_verified: bool
    first_name: str | None = None
    last_name: str | None = None


class SocialVerifier(Protocol):
    code: SocialProviderCode

    async def verify(self, token: str) -> SocialIdentity:
        """Check the token and return who it is for, or raise ``SocialAuthError``."""
        ...


__all__ = [
    "SocialAuthError",
    "SocialIdentity",
    "SocialProviderCode",
    "SocialVerifier",
]
