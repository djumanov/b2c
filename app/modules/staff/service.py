"""Admin authentication and the team resource — API.md §27 and §38.

This is the module's whole public surface: routers call it, and so does
``api/deps.current_staff`` when it turns a token's subject into a live row.
Nothing outside reaches past it to ``models`` or ``repository``
(ARCHITECTURE.md §4).

Three behaviours that are decisions rather than plumbing:

**Rotation is strict.** Every ``refresh/`` revokes the token it was handed and
issues a new pair (API.md §4). If a *revoked* refresh token is presented, that
is either a replay or a stolen copy — so every session that employee has is
killed, not just the one. The cost of being wrong is one extra login; the cost
of the other choice is an attacker keeping a foothold.

**Blocked is reported after the password checks out.** Answering "this account
is blocked" to any caller would turn the login form into a directory of the
client's staff. Answering it to somebody who already proved they know the
password tells them what they need and reveals nothing they did not know.

**The installation cannot be left without an owner.** Blocking, deleting or
demoting the last active owner is a `409`, because the alternative is a client
locked out of their own panel with no way back in but a database edit.
"""

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import (
    Conflict,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationFailed,
)
from app.api.listing import (
    ListQuery,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
from app.core.config import settings
from app.core.logging import get_logger, request_id_var
from app.core.roles import Role
from app.core.security import (
    TOKEN_TTL,
    Audience,
    TokenClaims,
    TokenError,
    TokenType,
    create_token,
    decode_token,
    denylist_key,
    denylist_ttl,
    denylist_ttl_for,
    hash_password,
    password_needs_rehash,
    revoked_before_key,
    revoked_before_ttl,
    revoked_before_value,
    verify_password,
)
from app.db.mixins import utcnow
from app.db.redis import get_redis
from app.db.repository import live
from app.modules.audit import context as audit_context
from app.modules.audit import service as audit_service
from app.modules.integrations import service as integrations_service
from app.modules.staff import repository
from app.modules.staff.models import Staff, StaffRefreshToken
from app.modules.staff.schemas import (
    LoginIn,
    PasswordChangeIn,
    StaffCreateIn,
    StaffMeOut,
    StaffOut,
    StaffUpdateIn,
    TokenPairOut,
)

logger = get_logger(__name__)

#: A reset link is useful for an hour. Long enough for an email to arrive and
#: be read, short enough that a forwarded message stops working.
RESET_TOKEN_TTL_SECONDS: Final = 60 * 60
_RESET_PREFIX: Final = "auth:staff-password-reset"

#: What ``?ordering=`` may name (API.md §6). Not the column list — the columns
#: this endpoint chose to expose.
_STAFF_ORDERING: Final = {
    "name": Staff.name,
    "email": Staff.email,
    "role": Staff.role,
    "created_at": Staff.created_at,
    "last_login_at": Staff.last_login_at,
}


def _normalize_email(email: str) -> str:
    return email.strip().lower()


# --- tokens ---------------------------------------------------------------------


def _access_ttl_seconds() -> int:
    return int(TOKEN_TTL[(Audience.ADMIN, TokenType.ACCESS)].total_seconds())


async def _issue_pair(
    session: AsyncSession,
    staff: Staff,
    *,
    user_agent: str | None,
    ip: str | None,
) -> TokenPairOut:
    access, _ = create_token(
        subject_id=staff.id,
        audience=Audience.ADMIN,
        token_type=TokenType.ACCESS,
        role=staff.role.value,
    )
    # The refresh token deliberately carries no ``role``: it lives for hours,
    # and a role changed in the meantime must come from the row at the next
    # access token, not from a claim minted before the change.
    refresh, refresh_claims = create_token(
        subject_id=staff.id,
        audience=Audience.ADMIN,
        token_type=TokenType.REFRESH,
    )
    session.add(
        StaffRefreshToken(
            staff_id=staff.id,
            jti=refresh_claims.jti,
            expires_at=refresh_claims.expires_at,
            user_agent=user_agent[:255] if user_agent else None,
            ip=ip[:45] if ip else None,
        )
    )
    return TokenPairOut(
        access_token=access,
        refresh_token=refresh,
        expires_in=_access_ttl_seconds(),
    )


async def _revoke(token: StaffRefreshToken) -> None:
    token.revoked_at = utcnow()
    await get_redis().set(
        denylist_key(token.jti), "1", ex=denylist_ttl_for(token.expires_at)
    )


async def _revoke_all_sessions(session: AsyncSession, staff_id: uuid.UUID) -> None:
    """End every session this employee holds — including the access tokens.

    Revoking the stored refresh tokens is only half of it. An access token is
    never stored, so there is no row to mark and nothing to name it by; left
    alone it keeps working for its full fifteen minutes. That is the wrong
    answer for every caller of this function — a password change, a detected
    refresh reuse, a blocked or deleted account — because each of them is
    somebody deciding that whoever holds those tokens must stop now.
    """
    for token in await repository.live_tokens_for(session, staff_id):
        await _revoke(token)
    await get_redis().set(
        revoked_before_key(staff_id),
        revoked_before_value(),
        ex=revoked_before_ttl(Audience.ADMIN),
    )


def _decode_refresh(token: str) -> TokenClaims:
    try:
        claims = decode_token(token, expected_type=TokenType.REFRESH)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired refresh token") from exc
    if claims.audience is not Audience.ADMIN:
        raise Forbidden("This token is not valid for this API surface")
    return claims


# --- the auth journal ---------------------------------------------------------------


async def _record_auth(
    session: AsyncSession,
    action: str,
    *,
    staff: Staff | None = None,
    email: str | None = None,
    ip: str | None = None,
    commit: bool = False,
) -> None:
    """Write one authentication event (API.md §13).

    These do not go through the audit middleware. It only records mutations
    that returned 2xx and carried a signed-in principal, and the event most
    worth having — a failed login — has neither. So this module records its own,
    which is also how the contract describes them: *"Autentifikatsiya hodisalari
    (kirish, muvaffaqiyatsiz urinish) alohida belgilanadi."*

    ``commit`` is for the paths that are about to raise: the exception unwinds
    before any handler commits, and an unrecorded failed login is the one thing
    this must not do.
    """
    await audit_service.record(
        session,
        resource="auth",
        action=action,
        actor_id=staff.id if staff else None,
        actor_email=staff.email if staff else email,
        actor_role=staff.role.value if staff else None,
        ip=ip,
        request_id=request_id_var.get(),
    )
    if commit:
        await session.commit()


# --- authentication (API.md §27) ---------------------------------------------------


async def authenticate(
    session: AsyncSession,
    data: LoginIn,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPairOut:
    email = _normalize_email(data.login)
    staff = await repository.by_email(session, email)
    # The same answer for "no such account" and "wrong password", and the hash
    # is verified either way so the two do not differ in how long they take.
    if staff is None:
        verify_password(data.password, hash_password(secrets.token_urlsafe(16)))
        await _record_auth(session, "login_failed", email=email, ip=ip, commit=True)
        raise Unauthorized("Invalid email or password")
    if not verify_password(data.password, staff.password_hash):
        await _record_auth(session, "login_failed", staff=staff, ip=ip, commit=True)
        raise Unauthorized("Invalid email or password")
    if staff.is_blocked:
        await _record_auth(session, "login_blocked", staff=staff, ip=ip, commit=True)
        raise Forbidden("This account has been blocked")

    if password_needs_rehash(staff.password_hash):
        staff.password_hash = hash_password(data.password)
    staff.last_login_at = utcnow()

    pair = await _issue_pair(session, staff, user_agent=user_agent, ip=ip)
    await _record_auth(session, "login", staff=staff, ip=ip)
    await session.commit()
    logger.info("staff_login", staff_id=str(staff.id), role=staff.role.value)
    return pair


async def refresh_session(
    session: AsyncSession,
    refresh_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPairOut:
    claims = _decode_refresh(refresh_token)
    stored = await repository.token_by_jti(session, claims.jti)

    if stored is None or stored.is_revoked:
        # Presented after revocation: replay or theft. End every session this
        # employee has rather than only refusing this one.
        await _revoke_all_sessions(session, claims.subject_id)
        await session.commit()
        logger.warning("staff_refresh_reuse", staff_id=str(claims.subject_id))
        raise Unauthorized("This session has been revoked")

    staff = await repository.by_id(session, claims.subject_id)
    if staff is None:
        raise Unauthorized("This account no longer exists")
    if staff.is_blocked:
        raise Forbidden("This account has been blocked")

    await _revoke(stored)
    pair = await _issue_pair(session, staff, user_agent=user_agent, ip=ip)
    await session.commit()
    return pair


async def logout(session: AsyncSession, refresh_token: str) -> None:
    """Revoke one session. Idempotent — logging out twice is not an error."""
    claims = _decode_refresh(refresh_token)
    stored = await repository.token_by_jti(session, claims.jti)
    if stored is not None and not stored.is_revoked:
        stored.revoked_at = utcnow()
    await get_redis().set(denylist_key(claims.jti), "1", ex=denylist_ttl(claims))
    signed_out = await repository.by_id(session, claims.subject_id)
    await _record_auth(session, "logout", staff=signed_out)
    await session.commit()


async def get_active(session: AsyncSession, staff_id: uuid.UUID) -> Staff:
    """The live row behind a token — called on every authenticated request.

    This is what makes blocking and deletion take effect immediately instead of
    when the access token happens to expire.
    """
    staff = await repository.by_id(session, staff_id)
    if staff is None:
        raise Unauthorized("This account no longer exists")
    if staff.is_blocked:
        raise Forbidden("This account has been blocked")
    return staff


def to_me(staff: Staff) -> StaffMeOut:
    return StaffMeOut(id=staff.id, name=staff.name, email=staff.email, role=staff.role)


# --- passwords --------------------------------------------------------------------


async def change_password(
    session: AsyncSession, staff: Staff, data: PasswordChangeIn
) -> None:
    if not verify_password(data.current_password, staff.password_hash):
        raise ValidationFailed(
            "Current password is incorrect", field="current_password"
        )
    staff.password_hash = hash_password(data.new_password)
    # Every session dies, including this one's refresh token: a password change
    # is how somebody reacts to a suspected leak, and leaving old sessions alive
    # would make it useless.
    await _revoke_all_sessions(session, staff.id)
    await _record_auth(session, "password_changed", staff=staff)
    await session.commit()


def _reset_key(token: str) -> str:
    # Stored hashed, so a dump of Redis is not a set of working reset links.
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"{_RESET_PREFIX}:{digest}"


async def _send_reset_link(session: AsyncSession, staff: Staff) -> None:
    token = secrets.token_urlsafe(32)
    await get_redis().set(_reset_key(token), str(staff.id), ex=RESET_TOKEN_TTL_SECONDS)
    # Which notifier depends on the installation's SMTP settings, so it takes a
    # session — a module owns that answer, not the provider package.
    notifier = await integrations_service.notifier(session)
    await notifier.send(
        recipient=staff.email,
        subject="Password reset",
        body=(
            "A password reset was requested for your account. "
            f"Use this code to set a new password: {token}\n"
            f"It expires in {RESET_TOKEN_TTL_SECONDS // 60} minutes."
        ),
        # The token also travels as a variable, because a real adapter renders
        # a template rather than sending this plain fallback text.
        context={
            "purpose": "staff_password_reset",
            "staff_id": str(staff.id),
            "token": token,
        },
    )


async def request_password_reset(session: AsyncSession, email: str) -> None:
    """Always succeeds from the caller's point of view.

    Reporting "no such address" here would let anyone test whether an employee
    of this client exists, and the request is unauthenticated.
    """
    staff = await repository.by_email(session, _normalize_email(email))
    if staff is None or staff.is_blocked:
        return
    await _send_reset_link(session, staff)
    await _record_auth(session, "password_reset_requested", staff=staff, commit=True)


async def confirm_password_reset(
    session: AsyncSession, token: str, new_password: str
) -> None:
    # GETDEL: the token is spent by looking it up, so two requests racing with
    # the same link cannot both set a password.
    raw = await get_redis().getdel(_reset_key(token))
    if raw is None:
        raise ValidationFailed(
            "This reset link is invalid or has expired", field="token"
        )
    staff = await repository.by_id(session, uuid.UUID(str(raw)))
    if staff is None or staff.is_blocked:
        raise ValidationFailed(
            "This reset link is invalid or has expired", field="token"
        )
    staff.password_hash = hash_password(new_password)
    await _revoke_all_sessions(session, staff.id)
    await _record_auth(session, "password_reset_completed", staff=staff)
    await session.commit()
    logger.info("staff_password_reset", staff_id=str(staff.id))


# --- the team resource (API.md §38) -------------------------------------------------


async def _ensure_owner_remains(
    session: AsyncSession, staff: Staff, *, action: str
) -> None:
    """Refuse anything that would leave no owner able to sign in."""
    if staff.role is not Role.OWNER:
        return
    if await repository.other_active_owners(
        session, excluding=staff.id, role_value=Role.OWNER.value
    ):
        return
    raise Conflict(
        f"Cannot {action} the last owner — the installation would have no "
        "administrator left"
    )


async def list_staff(
    session: AsyncSession, pagination: Pagination, query: ListQuery
) -> Page[StaffOut]:
    stmt = live(Staff)
    stmt = apply_search(stmt, query, Staff.name, Staff.email)
    stmt = apply_created_range(stmt, query, Staff.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_STAFF_ORDERING,
        default="-created_at",
        tiebreak=Staff.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([StaffOut.model_validate(row) for row in rows], pagination, total)


async def get_staff(session: AsyncSession, staff_id: uuid.UUID) -> StaffOut:
    return StaffOut.model_validate(await _require(session, staff_id))


def _auditable_fields(staff: Staff) -> dict[str, Any]:
    """The fields a reader of the journal would care about having changed.

    ``password_hash`` is not among them on purpose: that it changed is recorded
    by the ``password_changed`` event, and the value has no business being in a
    diff an ``admin`` can read.
    """
    return {"email": staff.email, "name": staff.name, "role": staff.role.value}


async def _require(session: AsyncSession, staff_id: uuid.UUID) -> Staff:
    staff = await repository.by_id(session, staff_id)
    if staff is None:
        raise NotFound("Staff member not found")
    return staff


async def create_staff(session: AsyncSession, data: StaffCreateIn) -> StaffOut:
    email = _normalize_email(data.email)
    if await repository.email_taken(session, email):
        raise ValidationFailed(
            "This email already belongs to a staff member", field="email"
        )
    staff = Staff(
        email=email,
        name=data.name.strip(),
        role=data.role,
        password_hash=hash_password(data.password),
    )
    session.add(staff)
    await session.commit()
    await session.refresh(staff)
    logger.info("staff_created", staff_id=str(staff.id), role=staff.role.value)
    return StaffOut.model_validate(staff)


async def update_staff(
    session: AsyncSession, staff_id: uuid.UUID, data: StaffUpdateIn
) -> StaffOut:
    staff = await _require(session, staff_id)
    before = _auditable_fields(staff)

    if data.email is not None:
        email = _normalize_email(data.email)
        if await repository.email_taken(session, email, excluding=staff.id):
            raise ValidationFailed(
                "This email already belongs to a staff member", field="email"
            )
        staff.email = email
    if data.name is not None:
        staff.name = data.name.strip()
    if data.role is not None and data.role is not staff.role:
        await _ensure_owner_remains(session, staff, action="demote")
        staff.role = data.role

    await session.commit()
    await session.refresh(staff)
    # The journal wants what changed, not what was sent (ARCHITECTURE.md §11).
    # A PATCH that resends the current name is not a change and does not appear.
    audit_context.describe(changes=audit_context.diff(before, _auditable_fields(staff)))
    return StaffOut.model_validate(staff)


async def delete_staff(
    session: AsyncSession, staff_id: uuid.UUID, *, actor_id: uuid.UUID
) -> None:
    staff = await _require(session, staff_id)
    if staff.id == actor_id:
        raise Conflict("You cannot delete your own account")
    await _ensure_owner_remains(session, staff, action="delete")
    staff.soft_delete()
    await _revoke_all_sessions(session, staff.id)
    await session.commit()
    logger.info("staff_deleted", staff_id=str(staff.id))


async def block_staff(
    session: AsyncSession, staff_id: uuid.UUID, *, actor_id: uuid.UUID
) -> StaffOut:
    staff = await _require(session, staff_id)
    if staff.id == actor_id:
        raise Conflict("You cannot block your own account")
    await _ensure_owner_remains(session, staff, action="block")
    staff.is_blocked = True
    await _revoke_all_sessions(session, staff.id)
    await session.commit()
    await session.refresh(staff)
    logger.info("staff_blocked", staff_id=str(staff.id))
    return StaffOut.model_validate(staff)


async def send_reset_password_link(session: AsyncSession, staff_id: uuid.UUID) -> None:
    """``POST /admin/staff/{id}/reset-password/`` — the owner sends the link,
    the employee chooses the password. No password is ever set for somebody
    else and then read out to them."""
    staff = await _require(session, staff_id)
    await _send_reset_link(session, staff)


# --- first boot -------------------------------------------------------------------


async def create_first_owner(session: AsyncSession) -> Staff | None:
    """The bootstrap in ``docker/bootstrap.py``. ``None`` when it was a no-op.

    Runs on every container start and does nothing on all but the first
    (PROJECT.md §14): the client has no panel to sign into until one account
    exists, so the very first one comes from the environment.
    """
    if not settings.first_owner_email or not settings.first_owner_password:
        logger.warning("bootstrap_skipped", reason="FIRST_OWNER_* not configured")
        return None
    if await repository.any_exists(session):
        return None

    owner = Staff(
        email=_normalize_email(settings.first_owner_email),
        name=settings.first_owner_name.strip() or "Owner",
        role=Role.OWNER,
        password_hash=hash_password(settings.first_owner_password),
    )
    session.add(owner)
    await session.commit()
    await session.refresh(owner)
    logger.info("bootstrap_owner_created", staff_id=str(owner.id))
    return owner


__all__: Sequence[str] = [
    "RESET_TOKEN_TTL_SECONDS",
    "authenticate",
    "block_staff",
    "change_password",
    "confirm_password_reset",
    "create_first_owner",
    "create_staff",
    "delete_staff",
    "get_active",
    "get_staff",
    "list_staff",
    "logout",
    "refresh_session",
    "request_password_reset",
    "send_reset_password_link",
    "to_me",
    "update_staff",
]
