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

Both principals are resolved from the **row**, not from the token's claims, so
a block, a deletion or a role changed since the token was issued takes effect on
the next request rather than when the token happens to expire. Each surface asks
its own module who the subject is.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Header, Query, Request
from redis.exceptions import RedisError

from app.api.errors import Forbidden, NotFound, RateLimited, Unauthorized
from app.core import i18n
from app.core.logging import get_logger
from app.core.roles import Role, satisfies
from app.core.security import (
    Audience,
    TokenClaims,
    TokenError,
    TokenType,
    decode_token,
    denylist_key,
    is_revoked_before,
    revoked_before_key,
)
from app.db.redis import get_redis
from app.db.session import SessionDep

# The flag names only — a leaf module that imports nothing but ``core.i18n``,
# so no cycle. Reading a flag's *value* needs the service, and that import is
# deferred inside ``RequireFeature.__call__``.
from app.modules.settings.defaults import FEATURE_DEFAULTS

logger = get_logger(__name__)

MAX_PAGE_SIZE: Final = 100
DEFAULT_PAGE_SIZE: Final = 20


# --- principals ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Customer:
    """The end user behind a ``aud: public`` token."""

    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Staff:
    """A panel user behind an ``aud: admin`` token, as the row currently reads.

    ``email`` and ``name`` ride along because they are already loaded and the
    two things that ask for them — ``auth/me/`` and the audit journal, which
    records who did what — would otherwise each fetch the row again.
    """

    id: uuid.UUID
    role: Role
    email: str
    name: str


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

    # Two ways a token dies before its expiry, asked in one round trip: this
    # exact token was revoked by name, or everything its subject held was ended
    # at once (a password change, a detected refresh reuse). The second is the
    # only one that can reach an access token, which is not stored anywhere and
    # so cannot be named.
    revoked_jti, revoked_before = await get_redis().mget(
        [denylist_key(claims.jti), revoked_before_key(claims.subject_id)]
    )
    if revoked_jti is not None or is_revoked_before(claims, revoked_before):
        raise Unauthorized("This session has been revoked")
    return claims


async def current_customer(request: Request, session: SessionDep) -> Customer:
    claims = await _claims_for(request, Audience.PUBLIC)

    # Imported here for the same reason as the staff import below: this module
    # is imported while the router tree is still being assembled.
    from app.modules.customers import service as customers_service

    # The row, not the claim. An account blocked or deleted since the token was
    # issued loses access now rather than in half an hour, and an account whose
    # address was never confirmed never had access to begin with.
    row = await customers_service.get_active(session, claims.subject_id)
    return Customer(id=row.id)


async def current_staff(request: Request, session: SessionDep) -> Staff:
    claims = await _claims_for(request, Audience.ADMIN)
    try:
        Role(claims.role or "")
    except ValueError as exc:
        # A role the enum no longer knows is refused rather than downgraded:
        # a token minted before the two-role model must stop working, not
        # quietly become an ``admin``.
        raise Forbidden(f"Unknown staff role: {claims.role!r}") from exc

    # Imported here, not at the top: the staff module's service imports this
    # file for ``Pagination``, so a module-level import would close the loop.
    # The dependency is real either way — cross-cutting auth has to ask a
    # module who the subject is.
    from app.modules.staff import service as staff_service

    # The row, not the claim, decides. An account blocked or deleted since the
    # token was issued loses access now rather than in fifteen minutes, and the
    # role that governs ``require_owner`` is the current one.
    row = await staff_service.get_active(session, claims.subject_id)
    principal = Staff(id=row.id, role=row.role, email=row.email, name=row.name)
    # The audit middleware runs after the handler and has no dependencies of
    # its own; this is where it learns who was acting (ARCHITECTURE.md §11).
    request.state.principal = principal
    return principal


async def current_customer_optional(
    request: Request, session: SessionDep
) -> Customer | None:
    """``current_customer`` for a route that also serves the anonymous.

    No ``Authorization`` header means anonymous, not refused. A header that is
    *present* goes through the full check and a bad token is still 401 —
    quietly ignoring it would hide a client bug behind an anonymous success.
    """
    if not request.headers.get("authorization"):
        return None
    return await current_customer(request, session)


CurrentCustomer = Annotated[Customer, Depends(current_customer)]
CurrentStaff = Annotated[Staff, Depends(current_staff)]
OptionalCustomer = Annotated[Customer | None, Depends(current_customer_optional)]


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
    page: Annotated[int, Query(ge=1, description="1-based page number.")] = 1,
    page_size: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_SIZE,
            description=f"Rows per page, 1–{MAX_PAGE_SIZE}.",
        ),
    ] = DEFAULT_PAGE_SIZE,
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
    lang: Annotated[
        str | None,
        Query(
            description=(
                "Language for translated fields — one of `uz`, `ru`, `en` "
                "(case-insensitive, a region suffix is ignored). Beats "
                "`Accept-Language`. An unknown value is ignored, never refused."
            )
        ),
    ] = None,
    accept_language: Annotated[
        str | None,
        Header(
            alias="Accept-Language",
            description=(
                "The usual browser header, read when `lang` is absent; the "
                "first supported language by `q` wins. Without either, the "
                "site's default language applies."
            ),
        ),
    ] = None,
) -> LanguageContext:
    """Resolve the requested language.

    Only the *request* is read here. Which language actually comes back depends
    on what the row has and on the site's default, and is decided per field by
    ``core.i18n.resolve`` — the response reports it as ``lang``. Declared as
    parameters rather than read off ``request.headers`` so both appear in the
    schema; the values arrive exactly as the header carried them.
    """
    explicit = i18n.normalize_language(lang)
    if explicit:
        return LanguageContext(requested=explicit)
    accepted = i18n.parse_accept_language(accept_language)
    return LanguageContext(requested=accepted[0] if accepted else None)


LanguageDep = Annotated[LanguageContext, Depends(language)]


# --- rate limiting ---------------------------------------------------------------


#: API.md §14. Auth is per IP by definition (nobody is logged in yet); the
#: others fall back to the IP when the caller is anonymous.
RATE_LIMITS: Final[dict[str, tuple[int, int]]] = {
    # kind: (max requests, window seconds)
    # Guessing a credential: a password, a four-digit code, a reset token.
    # Five a minute, and by IP, because nobody is signed in yet.
    "auth": (5, 60),
    # Holding a session already proved — rotating a refresh token, reading
    # ``auth/me/``. Nothing here is guessable, so the ``auth`` bucket only ever
    # blocked honest clients: several tabs, a page that refreshes on each 401,
    # a panel that asks who it is on every screen. Separate, and roomy.
    "session": (30, 60),
    "search": (30, 60),
    # Tighter than the rest of the public surface: what sits behind it writes
    # card data, and a bucket that lets a script hammer it is a bucket that
    # lets a script test card numbers (API.md §14).
    "payment": (10, 60),
    "public": (120, 60),
    "admin": (300, 60),
}


def client_ip(request: Request) -> str:
    """The caller's address, as far as it can be *trusted* to be one.

    Not the first ``X-Forwarded-For`` entry, which is what this used to read.
    That entry is whatever the caller typed: nginx builds the header with
    ``$proxy_add_x_forwarded_for``, which **appends** the peer it saw to what
    arrived, so a request carrying ``X-Forwarded-For: 9.9.9.9`` reaches us as
    ``9.9.9.9, <real ip>``. Reading the front of that made every rate-limit
    bucket the caller's to choose — and a login limit that a header defeats is
    not a login limit.

    So: ``X-Real-IP``, which the proxy *overwrites* rather than extends
    (``docker/nginx.conf``), then the **last** hop of ``X-Forwarded-For``,
    which is the same address by construction, then the socket.

    One consequence worth knowing. Put a CDN in front of nginx and this becomes
    the CDN edge, so visitors behind one edge share a bucket. That errs towards
    limiting too much, which is the safe direction; the fix is a trusted-proxy
    depth, and it needs to be configured rather than guessed.
    """
    real = request.headers.get("x-real-ip")
    if real and real.strip():
        return real.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


def request_subject(request: Request) -> str:
    """Who is asking: the authenticated subject, or the IP (API.md §14).

    Read straight off the header rather than through ``current_customer``: this
    is used by the rate limiter, which runs before any principal is resolved,
    and by ``api/idempotency.py``, which must not acquire a dependency on the
    database to name a Redis key. A token that will not decode is nobody, not
    an error — the endpoint's own auth dependency is what refuses it.
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        try:
            return f"sub:{decode_token(token.strip()).subject_id}"
        except TokenError:
            pass
    return f"ip:{client_ip(request)}"


def RateLimit(  # noqa: N802 - reads as a marker at the use site
    kind: str,
) -> Callable[[Request], Awaitable[None]]:
    """Fixed-window limiter: ``dependencies=[Depends(RateLimit("search"))]``.

    A fixed window can let through up to twice the limit across a boundary.
    That is accepted: these limits exist to blunt abuse and runaway clients,
    not to meter a paid quota, and a fixed window is one round trip.
    """
    limit, window = RATE_LIMITS[kind]

    async def count_hit(key: str) -> tuple[int, int]:
        redis = get_redis()
        async with redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            count, ttl = await pipe.execute()
        if count == 1 or ttl < 0:
            # Not in the pipeline above, so a process that dies between the two
            # leaves a counter with no expiry. ``ttl < 0`` is that case, and
            # re-arming here means it costs one window rather than forever.
            await redis.expire(key, window)
            ttl = window
        return int(count), int(ttl)

    async def dependency(request: Request) -> None:
        try:
            count, ttl = await count_hit(f"ratelimit:{kind}:{request_subject(request)}")
        except RedisError:
            # Fail **open**. This limiter hangs off ``/public`` and ``/admin``
            # wholesale, so raising here would turn a Redis blip into a 500 on
            # every endpoint in the API — including the reads that only ever
            # needed PostgreSQL. Blunting abuse is worth less than staying up,
            # and the log line is what says the guard was down.
            logger.warning("rate_limit_unavailable", kind=kind, exc_info=True)
            return
        if count > limit:
            raise RateLimited(
                "Too many requests, please try again shortly",
                retry_after=max(1, ttl),
            )

    return dependency


class RequireFeature:
    """Gate a section behind its flag: ``Depends(RequireFeature("blog"))``.

    A client who does not sell tours does not want a blog, and PROJECT.md §1
    says that difference lives in the database rather than in the code. This is
    the half that makes the flag mean something: switched off, the routes it
    guards answer ``404`` on both surfaces (API.md §28).

    **404, not 403.** Nobody is forbidden — the resource is not there. The same
    shape API.md §41 uses for endpoints the release has not wired, and §6's
    adapter registry uses for a step a vertical does not support.

    A **class** where ``RateLimit`` and ``Audited`` are function factories, for
    one reason: ``tests/contract/test_feature_gate.py`` has to recognise the
    gate inside a route's dependency tree, and ``isinstance`` is the honest way
    to do that. Hanging an attribute on a closure would work and would not
    survive the first person who reads it.
    """

    __slots__ = ("flag",)

    def __init__(self, flag: str) -> None:
        if flag not in FEATURE_DEFAULTS:
            # Import time, not request time — the same trick as
            # ``RATE_LIMITS[kind]``. A typo takes the process down on boot
            # instead of quietly serving a section nobody can switch off.
            raise KeyError(
                f"unknown feature {flag!r}; add it to "
                "settings.defaults.FEATURE_DEFAULTS and to API.md §28 first"
            )
        self.flag = flag

    async def __call__(self) -> None:
        # Deferred like the staff import in ``current_staff``: this module is
        # imported while the router tree is still being assembled.
        from app.modules.settings import service as settings_service

        if not await settings_service.feature_enabled(self.flag):
            raise NotFound("This section is not available on this installation")


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "RATE_LIMITS",
    "CurrentCustomer",
    "CurrentStaff",
    "Customer",
    "LanguageContext",
    "LanguageDep",
    "OptionalCustomer",
    "Pagination",
    "PaginationDep",
    "RateLimit",
    "RequireFeature",
    "Staff",
    "client_ip",
    "current_customer",
    "current_customer_optional",
    "current_staff",
    "request_subject",
    "require_owner",
]
