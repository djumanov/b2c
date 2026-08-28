"""Admin authentication and the team resource — API.md §27 and §38.

This is the module's whole public surface: routers call it, and so does
``api/deps.current_staff`` when it turns a token's subject into a live row.
Nothing outside reaches past it to ``models`` or ``repository``
(ARCHITECTURE.md §4).

Three behaviours that are decisions rather than plumbing:

**Rotation is per session, and forgiving for a minute.** Every ``refresh/``
revokes the token it was handed and issues a new pair (API.md §4). An employee
may hold as many sessions as they like — two browsers, a laptop and a phone —
and what happens to one never reaches another. A token that rotation replaced
within ``ROTATION_GRACE`` is still served rather than refused, because the
commonest way to present one is to have two tabs open, or to retry a request
whose response was lost. Outside that window, or once a logout, a password
change or a block has ended it, the token is refused — and only that token.

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
    UpstreamError,
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
    is_marked_revoked,
    is_rotation_race,
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

# ``settings`` is already the config object from ``app.core.config`` here, so
# the module that owns the site's own settings comes in under its full name.
from app.modules.settings import service as site_settings
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
from app.providers.notifications import html as mail_html

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
    replaces: StaffRefreshToken | None = None,
) -> TokenPairOut:
    """Mint an access/refresh pair, optionally retiring the one it succeeds.

    ``replaces`` keeps rotation in one place: the new ``jti`` is known here and
    nowhere else, and it is what the retired row has to record.
    """
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
    # A token replaced inside the grace window can be presented more than once.
    # Only the first replacement is recorded: re-stamping ``revoked_at`` on each
    # would slide the window forward and keep one token alive indefinitely.
    if replaces is not None and not replaces.is_revoked:
        await _revoke(replaces, replaced_by=refresh_claims.jti)
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


async def _revoke(token: StaffRefreshToken, *, replaced_by: str | None = None) -> None:
    """End one session. ``replaced_by`` is set only when rotation ended it.

    That distinction is the whole of the grace window: a token rotation put
    aside a moment ago may still be in flight from a second tab, while one a
    logout or a password change ended was ended on purpose.
    """
    token.revoked_at = utcnow()
    token.replaced_by_jti = replaced_by
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


async def _race_forgiven(token: StaffRefreshToken) -> bool:
    """Is this revoked token a second tab arriving late, and is that still safe?

    Two questions, because the grace window forgives a race but not a
    revocation that overtook one. Rotation must be what retired the token and
    it must have been recent — *and* nobody must have ended every session this
    employee holds since. A password change, a block or a deletion writes the
    subject-wide mark, and a token rotated out a moment before it must not be
    able to mint a fresh pair inside the window it would otherwise have had.
    """
    revoked_at = token.revoked_at
    if revoked_at is None:
        return False
    if not is_rotation_race(
        revoked_at=revoked_at, replaced_by_jti=token.replaced_by_jti
    ):
        return False
    mark = await get_redis().get(revoked_before_key(token.staff_id))
    return not is_marked_revoked(revoked_at, mark, tolerate_same_second=False)


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

    if stored is None:
        raise Unauthorized("This session is no longer valid")

    forgiven = await _race_forgiven(stored)
    if stored.is_revoked and not forgiven:
        # Ended on purpose — a logout, a password change, a block — or replaced
        # long enough ago that no honest client is still holding it. Refused on
        # its own: the employee's other sessions are none of this one's business.
        raise Unauthorized("This session has been revoked")

    staff = await repository.by_id(session, claims.subject_id)
    if staff is None:
        raise Unauthorized("This account no longer exists")
    if staff.is_blocked:
        raise Forbidden("This account has been blocked")

    if forgiven:
        # Worth a line: it is how a client that refreshes more often than it
        # needs to shows up in the log, and it costs a session row each time.
        logger.info("staff_refresh_race", staff_id=str(staff.id))

    pair = await _issue_pair(
        session, staff, user_agent=user_agent, ip=ip, replaces=stored
    )
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


async def _send_reset_link(session: AsyncSession, staff: Staff) -> bool:
    """Mail a reset code. ``False`` when the relay would not take it.

    Never raises, because the two callers need opposite things from a failure.
    ``request_password_reset`` is unauthenticated and answers `204` for every
    address on purpose, so a `500` there would say which addresses belong to an
    employee; the owner-only endpoint turns the ``False`` into a `502` instead,
    because somebody is waiting to hear whether the link went out.

    The token reaches Redis only once the message has gone. A key written for a
    message that never left is a working reset token that nobody can use.
    """
    token = secrets.token_urlsafe(32)
    # Which notifier depends on the installation's SMTP settings and which
    # brand it wears on the branding ones, so both are asked of the module that
    # owns them rather than read from here (ARCHITECTURE.md §4).
    notifier = await integrations_service.notifier(session)
    minutes = RESET_TOKEN_TTL_SECONDS // 60
    intro = "A password reset was requested for your account."
    expiry = f"This code expires in {minutes} minutes."
    html = mail_html.render(
        brand=await site_settings.mail_brand(),
        heading="Password reset",
        paragraphs=(intro, "Use this code to set a new password:"),
        code=token,
        footnotes=(expiry,),
    )
    try:
        await notifier.send(
            recipient=staff.email,
            subject="Password reset",
            body=f"{intro}\n\nUse this code to set a new password: {token}\n{expiry}",
            html=html,
            # The token also travels as a variable: what the reader sees is a
            # rendered message, so the body text is nobody's contract.
            context={
                "purpose": "staff_password_reset",
                "staff_id": str(staff.id),
                "token": token,
            },
        )
    except Exception as exc:  # noqa: BLE001 - the reason is logged, not raised
        logger.error(
            "mail_send_failed",
            purpose="staff_password_reset",
            recipient=staff.email,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
        return False

    await get_redis().set(_reset_key(token), str(staff.id), ex=RESET_TOKEN_TTL_SECONDS)
    return True


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
    else and then read out to them.

    The one caller that is told when the relay refuses: an owner who pressed
    this button is waiting to hear whether the employee can expect a mail, and
    a silent `204` would have them wait for one that never left.
    """
    staff = await _require(session, staff_id)
    if not await _send_reset_link(session, staff):
        raise UpstreamError("The mail relay would not take the reset link")


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
