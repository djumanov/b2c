"""Google as a ``SocialVerifier`` — verifying an ID token locally.

Google publishes the public halves of its signing keys, so the token is checked
**here** rather than by asking Google about it. That is Google's own
recommendation and it is also the only version that behaves: a per-sign-in call
to a `tokeninfo` endpoint would put a third party on the critical path of every
login, and would turn their outage into ours.

The keys rotate, so they are fetched and cached; the cache is the only network
call this adapter makes, and it happens roughly daily rather than per request.
"""

import json
from typing import Any, Final

import httpx
import jwt

from app.core.logging import get_logger
from app.db.redis import get_redis
from app.providers.social.base import (
    SocialAuthError,
    SocialIdentity,
    SocialProviderCode,
)

logger = get_logger(__name__)

#: Google's JWKS document. Not configurable: it is Google's address, not a
#: client's preference (PROJECT.md §7).
CERTS_URL: Final = "https://www.googleapis.com/oauth2/v3/certs"

#: Both values Google may put in ``iss``. Historically it has used each.
ISSUERS: Final = ("https://accounts.google.com", "accounts.google.com")

_CACHE_KEY: Final = "social:google:jwks"
#: Well under Google's own rotation period, so a key retired between two
#: fetches is never the reason a valid token is refused.
_CACHE_TTL: Final = 60 * 60
_TIMEOUT: Final = 5.0


class GoogleVerifier:
    """Checks that a token was issued by Google **for this installation**."""

    code: SocialProviderCode = SocialProviderCode.GOOGLE

    __slots__ = ("client_id",)

    def __init__(self, client_id: str) -> None:
        self.client_id = client_id

    async def _jwks(self, *, refresh: bool = False) -> dict[str, Any]:
        redis = get_redis()
        if not refresh:
            cached = await redis.get(_CACHE_KEY)
            if cached is not None:
                loaded: dict[str, Any] = json.loads(cached)
                return loaded

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(CERTS_URL)
            response.raise_for_status()
            document: dict[str, Any] = response.json()

        await redis.set(_CACHE_KEY, json.dumps(document), ex=_CACHE_TTL)
        return document

    async def _key_for(self, token: str) -> Any:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            raise SocialAuthError("token header is unreadable") from exc

        for attempt in (False, True):
            document = await self._jwks(refresh=attempt)
            for key in document.get("keys", []):
                if key.get("kid") == kid:
                    return jwt.PyJWK(key).key
            # Not in the cached document: either the token is not Google's, or
            # Google rotated since the last fetch. One forced refresh tells the
            # two apart, and a signing key that has genuinely gone is the more
            # likely of the two on a working installation.
        raise SocialAuthError("token was not signed by a key Google publishes")

    async def verify(self, token: str) -> SocialIdentity:
        key = await self._key_for(token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                # Both are the point of the check: a token addressed to
                # somebody else's client id is a perfectly valid Google token
                # that says nothing about this installation.
                audience=self.client_id,
                issuer=list(ISSUERS),
                options={"require": ["sub", "aud", "exp", "iss"]},
            )
        except jwt.PyJWTError as exc:
            raise SocialAuthError(str(exc)) from exc

        email = claims.get("email")
        if not email:
            # A Google account without an address cannot be matched to one
            # here, and this application has no other identifier for a person.
            raise SocialAuthError("the token carries no email address")

        return SocialIdentity(
            subject=str(claims["sub"]),
            email=str(email),
            email_verified=bool(claims.get("email_verified")),
            first_name=claims.get("given_name"),
            last_name=claims.get("family_name"),
        )


__all__ = ["CERTS_URL", "ISSUERS", "GoogleVerifier"]
