"""Cross-cutting dependencies: auth, RBAC, pagination, language, rate limits.

None of this belongs in handler bodies. Authentication, authorisation,
pagination, idempotency and audit are the same on all ~150 endpoints, and the
only way two surfaces stay consistent is to express them once, here, and
attach them (ARCHITECTURE.md §13.4).

**A valid token on the wrong surface is 403, not 401** (API.md §4). The caller
proved who they are; a customer is simply not a kind of user that ``/admin/*``
serves. Answering 401 would tell a panel client to go and refresh a token that
is perfectly fine.

Authorisation is one bit wide: the roles are ``owner`` and ``admin`` and the
first contains the second (API.md §5). So a route either takes any staff token
or carries ``require_owner`` — there is no permission string to look up.

While the ``staff`` and ``customers`` tables do not exist yet, the principals
below are built from the token's own claims. Loading the row — to honour a
block, a deleted account or a role changed since the token was issued — lands
with the auth module; the call sites do not change when it does.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Query, Request

from app.api.errors import Forbidden, RateLimited, Unauthorized
from app.core import i18n
from app.core.roles import Role, satisfies
from app.core.security import (
    Audience,
    TokenClaims,
    TokenError,
    TokenType,
    decode_token,
    denylist_key,
)
from app.db.redis import get_redis

MAX_PAGE_SIZE: Final = 100
DEFAULT_PAGE_SIZE: Final = 20


# --- principals ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Customer:
    """The end user behind a ``aud: public`` token."""

    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Staff:
    """A panel user behind an ``aud: admin`` token."""

    id: uuid.UUID
    role: Role


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("Authentication required")
    return token.strip()


async def _claims_for(request: Request, expected: Audience) -> TokenClaims:
    try:
        claims = decode_token(_bearer_token(request), expected_type=TokenType.ACCESS)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token") from exc

    if claims.audience is not expected:
        raise Forbidden("This token is not valid for this API surface")

    if await get_redis().exists(denylist_key(claims.jti)):
        raise Unauthorized("This session has been revoked")
    return claims


async def current_customer(request: Request) -> Customer:
    claims = await _claims_for(request, Audience.PUBLIC)
    return Customer(id=claims.subject_id)


async def current_staff(request: Request) -> Staff:
    claims = await _claims_for(request, Audience.ADMIN)
    try:
        role = Role(claims.role or "")
    except ValueError as exc:
        # A role the enum no longer knows is refused rather than downgraded:
        # a token minted before the two-role model must stop working, not
        # quietly become an ``admin``.
        raise Forbidden(f"Unknown staff role: {claims.role!r}") from exc
    return Staff(id=claims.subject_id, role=role)


CurrentCustomer = Annotated[Customer, Depends(current_customer)]
CurrentStaff = Annotated[Staff, Depends(current_staff)]


async def require_owner(staff: CurrentStaff) -> Staff:
    """Guard an owner-only route: ``dependencies=[Depends(require_owner)]``.

    The roles are two and ordered — ``owner ⊃ admin`` (API.md §5) — so a route
    is either open to any staff token, in which case ``current_staff`` alone is
    the guard, or restricted to the owner, in which case this is. There is no
    third case and no permission catalogue to consult.
    """
    if not satisfies(staff.role, Role.OWNER):
        raise Forbidden("This action requires the owner role")
    return staff


# --- pagination ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pagination:
    """``page`` / ``page_size`` for list endpoints (API.md §6)."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def pagination(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


PaginationDep = Annotated[Pagination, Depends(pagination)]


# --- language ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LanguageContext:
    """Which language the caller asked for — ``?lang=`` beats the header."""

    requested: str | None

    @property
    def is_explicit(self) -> bool:
        return self.requested is not None


def language(
    request: Request,
    lang: Annotated[str | None, Query()] = None,
) -> LanguageContext:
    """Resolve the requested language (API.md §7).

    Only the *request* is read here. Which language actually comes back depends
    on what the row has and on the site's default, and is decided per field by
    ``core.i18n.resolve`` — the response reports it as ``lang``.
    """
    explicit = i18n.normalize_language(lang)
    if explicit:
        return LanguageContext(requested=explicit)
    accepted = i18n.parse_accept_language(request.headers.get("accept-language"))
    return LanguageContext(requested=accepted[0] if accepted else None)


LanguageDep = Annotated[LanguageContext, Depends(language)]


# --- rate limiting ---------------------------------------------------------------


#: API.md §14. Auth is per IP by definition (nobody is logged in yet); the
#: others fall back to the IP when the caller is anonymous.
RATE_LIMITS: Final[dict[str, tuple[int, int]]] = {
    # kind: (max requests, window seconds)
    "auth": (5, 60),
    "search": (30, 60),
    "public": (120, 60),
    "admin": (300, 60),
}


def _client_ip(request: Request) -> str:
    # X-Forwarded-For is set by the client's reverse proxy; the first entry is
    # the original caller.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_subject(request: Request) -> str:
    """Prefer the authenticated subject; fall back to the IP (API.md §14)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        try:
            return f"sub:{decode_token(token.strip()).subject_id}"
        except TokenError:
            pass
    return f"ip:{_client_ip(request)}"


def RateLimit(  # noqa: N802 - reads as a marker at the use site
    kind: str,
) -> Callable[[Request], Awaitable[None]]:
    """Fixed-window limiter: ``dependencies=[Depends(RateLimit("search"))]``.

    A fixed window can let through up to twice the limit across a boundary.
    That is accepted: these limits exist to blunt abuse and runaway clients,
    not to meter a paid quota, and a fixed window is one round trip.
    """
    limit, window = RATE_LIMITS[kind]

    async def dependency(request: Request) -> None:
        redis = get_redis()
        key = f"ratelimit:{kind}:{_rate_limit_subject(request)}"
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()
        if count == 1 or ttl < 0:
            await redis.expire(key, window)
            ttl = window
        if count > limit:
            raise RateLimited(
                "Too many requests, please try again shortly",
                retry_after=max(1, int(ttl)),
            )

    return dependency


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "RATE_LIMITS",
    "CurrentCustomer",
    "CurrentStaff",
    "Customer",
    "LanguageContext",
    "LanguageDep",
    "Pagination",
    "PaginationDep",
    "RateLimit",
    "Staff",
    "current_staff",
    "require_owner",
]
