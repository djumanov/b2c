"""Password hashing and JWT issuing/verification for both token subjects.

Two subjects share one backend but never share a token (API.md §4):

===========  ==========  ==================  ===========  ============
subject      ``aud``     surface             access TTL   refresh TTL
===========  ==========  ==================  ===========  ============
customer     ``public``  ``/api/v1/public``  30 minutes   30 days
staff        ``admin``   ``/api/v1/admin``   15 minutes   12 hours
===========  ==========  ==================  ===========  ============

Presenting a valid token to the wrong surface is *forbidden*, not
*unauthenticated*: the caller proved who they are, they simply are not that
kind of user. ``api.deps`` turns that into a 403 (API.md §4).

Refresh tokens rotate — every refresh revokes the one that was used — and
revocation is a ``jti`` denylist in Redis that outlives the token's own TTL by
nothing more than a safety margin.

The denylist alone cannot end a *session*, though. A refresh token is stored,
so it can be named; an access token is not, so the only thing left of it after
it is handed out is what it says about itself. Ending every session therefore
takes a second mark — "nothing this subject was issued before now is valid any
more" — which is what ``revoked_before`` is. Without it a password change, the
reaction to a suspected leak, would leave the leaked access token working until
it expired on its own.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

ALGORITHM: Final = "HS256"

_hasher = PasswordHasher()


class Audience(StrEnum):
    """The ``aud`` claim — which surface a token is valid on."""

    PUBLIC = "public"
    ADMIN = "admin"


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


# API.md §4. Access tokens are short so a leaked one expires quickly; staff
# refresh is a working day, customer refresh a month of not re-logging in.
TOKEN_TTL: Final[dict[tuple[Audience, TokenType], timedelta]] = {
    (Audience.PUBLIC, TokenType.ACCESS): timedelta(minutes=30),
    (Audience.PUBLIC, TokenType.REFRESH): timedelta(days=30),
    (Audience.ADMIN, TokenType.ACCESS): timedelta(minutes=15),
    (Audience.ADMIN, TokenType.REFRESH): timedelta(hours=12),
}


class TokenError(Exception):
    """A token is missing, malformed, expired, revoked or of the wrong type."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The decoded, verified payload."""

    subject_id: uuid.UUID
    audience: Audience
    token_type: TokenType
    jti: str
    expires_at: datetime
    #: When it was issued. Read, not decorative: it is what a ``revoked_before``
    #: mark is compared against.
    issued_at: datetime
    role: str | None = None


# --- passwords ---------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """True when argon2's parameters have moved on since this hash was made."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# --- tokens ------------------------------------------------------------------


def create_token(
    *,
    subject_id: uuid.UUID,
    audience: Audience,
    token_type: TokenType,
    role: str | None = None,
    now: datetime | None = None,
) -> tuple[str, TokenClaims]:
    """Issue a signed token and hand back its claims (the caller stores ``jti``)."""
    # Truncated to the second, because that is the resolution `iat` has once it
    # is encoded — and a claims object that disagreed with its own token would
    # make every comparison against `iat` subtly wrong.
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + TOKEN_TTL[(audience, token_type)]
    jti = uuid.uuid4().hex

    payload: dict[str, Any] = {
        "sub": str(subject_id),
        "aud": audience.value,
        "typ": token_type.value,
        "jti": jti,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    if role is not None:
        payload["role"] = role

    encoded = jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)
    claims = TokenClaims(
        subject_id=subject_id,
        audience=audience,
        token_type=token_type,
        jti=jti,
        expires_at=expires_at,
        issued_at=issued_at,
        role=role,
    )
    return encoded, claims


def decode_token(token: str, *, expected_type: TokenType | None = None) -> TokenClaims:
    """Verify signature and expiry and return the claims.

    The audience is **not** checked here. Callers need to tell "expired or
    forged" (401) apart from "valid, wrong surface" (403), and that decision
    belongs to the dependency that knows which surface was addressed.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[ALGORITHM],
            # Audience is validated by the caller, see above.
            options={
                "verify_aud": False,
                # ``iat`` is required because ending a session depends on it: a
                # token that did not say when it was issued could not be caught
                # by a ``revoked_before`` mark.
                "require": ["sub", "aud", "exp", "jti", "iat"],
            },
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    try:
        audience = Audience(payload["aud"])
        token_type = TokenType(payload.get("typ", TokenType.ACCESS.value))
        claims = TokenClaims(
            subject_id=uuid.UUID(payload["sub"]),
            audience=audience,
            token_type=token_type,
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
            role=payload.get("role"),
        )
    except (KeyError, ValueError) as exc:
        raise TokenError(f"malformed token payload: {exc}") from exc

    if expected_type is not None and claims.token_type is not expected_type:
        raise TokenError(
            f"expected a {expected_type.value} token, got {claims.token_type.value}"
        )
    return claims


# --- revocation ---------------------------------------------------------------

_DENYLIST_PREFIX: Final = "auth:revoked-jti"
# Keep a revoked jti a little past its own expiry so clock skew between the
# issuing and checking process cannot resurrect it.
_DENYLIST_SKEW: Final = timedelta(minutes=5)


def denylist_key(jti: str) -> str:
    return f"{_DENYLIST_PREFIX}:{jti}"


def denylist_ttl_for(expires_at: datetime, *, now: datetime | None = None) -> int:
    """Seconds a revoked ``jti`` must be remembered — at least one second.

    Takes the expiry rather than the claims, because sessions are also revoked
    from the stored row (an owner blocking an employee has the row, not the
    token they were issued).
    """
    remaining = expires_at - (now or datetime.now(UTC)) + _DENYLIST_SKEW
    return max(1, int(remaining.total_seconds()))


def denylist_ttl(claims: TokenClaims, *, now: datetime | None = None) -> int:
    return denylist_ttl_for(claims.expires_at, now=now)


# --- ending every session at once ----------------------------------------------

_REVOKED_BEFORE_PREFIX: Final = "auth:revoked-before"


def revoked_before_key(subject_id: uuid.UUID) -> str:
    return f"{_REVOKED_BEFORE_PREFIX}:{subject_id}"


def revoked_before_ttl(audience: Audience) -> int:
    """How long the mark has to outlive the tokens it invalidates.

    Only access tokens are caught by it — refresh tokens are stored and revoked
    by name — so the mark can be forgotten once the longest-lived access token
    issued before it would have expired anyway.
    """
    lifetime = TOKEN_TTL[(audience, TokenType.ACCESS)] + _DENYLIST_SKEW
    return max(1, int(lifetime.total_seconds()))


def revoked_before_value(now: datetime | None = None) -> str:
    """The mark itself: the instant, at ``iat``'s own resolution."""
    return str(int((now or datetime.now(UTC)).timestamp()))


def is_revoked_before(claims: TokenClaims, mark: str | bytes | None) -> bool:
    """Was this token issued before its subject's sessions were all ended?

    A token issued in the *same second* as the mark survives, which is
    deliberate: `iat` is only accurate to the second, and the alternative locks
    a person out of the login they make immediately after changing their own
    password. The cost is a window no wider than one second, at an instant an
    attacker would have to be inside already.

    An unreadable mark counts as revoked. It is written by this process and
    nothing else; if it says something else, something is wrong, and on a
    revocation check the safe direction to be wrong in is "no".
    """
    if mark is None:
        return False
    try:
        return int(claims.issued_at.timestamp()) < int(mark)
    except (TypeError, ValueError):
        return True


__all__ = [
    "ALGORITHM",
    "TOKEN_TTL",
    "Audience",
    "TokenClaims",
    "TokenError",
    "TokenType",
    "create_token",
    "decode_token",
    "denylist_key",
    "denylist_ttl",
    "denylist_ttl_for",
    "hash_password",
    "is_revoked_before",
    "password_needs_rehash",
    "revoked_before_key",
    "revoked_before_ttl",
    "revoked_before_value",
    "verify_password",
]
