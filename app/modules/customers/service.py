"""Customer authentication — API.md §18.

This is the module's whole public surface: the router calls it, and so does
``api/deps.current_customer`` when it turns a token's subject into a live row.
Nothing outside reaches past it to ``models`` or ``repository``
(ARCHITECTURE.md §4).

The token machinery is the ``staff`` module's, re-aimed at ``Audience.PUBLIC``:
strict rotation, a replayed refresh token treated as theft, and the
subject-wide ``revoked_before`` mark so a password change also ends the access
tokens nobody can name. Two audiences behaving differently under the same threat
would be two sets of rules to remember.

What is specific to this surface:

**Nothing here says whether an address has an account.** Registration answers
204 for a taken address and mails the notice to the address itself; a reset
request answers 204 for an address that does not exist; a failed login cannot be
told from an unknown one, in body or in timing. The endpoints are
unauthenticated and public, so any difference between the two answers is a tool
for enumerating the client's customers.

**An unverified row grants nothing.** ``register/`` writes the account
immediately rather than parking it somewhere, which keeps one address in one
place — but ``is_active`` requires ``email_verified_at``, so until the code comes
back the row is a reservation and not an account.

**A code is spent by being wrong, too.** Six digits is a small space; the
attempt ceiling is what makes it enough, so a wrong guess is recorded even
though the request is about to fail.
"""

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Forbidden, Unauthorized, ValidationFailed
from app.core.logging import get_logger
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
from app.modules.customers import repository
from app.modules.customers.models import (
    Customer,
    CustomerRefreshToken,
    EmailOtp,
    OtpPurpose,
)
from app.modules.customers.schemas import (
    LoginIn,
    PasswordResetVerifyIn,
    RegisterConfirmIn,
    RegisterIn,
    ResetTokenOut,
    TokenPairOut,
)
from app.modules.integrations import service as integrations_service

logger = get_logger(__name__)

#: API.md §18. Long enough for mail to arrive and be read, short enough that a
#: forwarded message stops working.
OTP_TTL: Final = timedelta(minutes=10)
#: What makes six digits enough. Without a ceiling the whole space is a million
#: requests away, and this endpoint is unauthenticated.
OTP_MAX_ATTEMPTS: Final = 5
#: Between two codes for the same address and purpose.
OTP_RESEND_COOLDOWN: Final = timedelta(seconds=60)

#: The artefact ``password/reset/verify/`` hands back. Shorter than the code it
#: replaces: the holder is already at the keyboard typing a new password.
RESET_TOKEN_TTL_SECONDS: Final = 15 * 60
_RESET_PREFIX: Final = "auth:customer-password-reset"

#: One message for every way a code can fail — wrong, expired, already used, or
#: out of attempts. Telling them apart would say how far a guesser had got.
_CODE_REJECTED: Final = "This code is invalid or has expired"
_RESET_TOKEN_REJECTED: Final = "This reset link is invalid or has expired"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


# --- tokens ---------------------------------------------------------------------


def _access_ttl_seconds() -> int:
    return int(TOKEN_TTL[(Audience.PUBLIC, TokenType.ACCESS)].total_seconds())


async def _issue_pair(
    session: AsyncSession,
    customer: Customer,
    *,
    user_agent: str | None,
    ip: str | None,
) -> TokenPairOut:
    # No ``role`` on either token: API.md §4 gives that claim to staff only, and
    # a public token that carried one would be a token asking to be trusted with
    # something it is not.
    access, _ = create_token(
        subject_id=customer.id,
        audience=Audience.PUBLIC,
        token_type=TokenType.ACCESS,
    )
    refresh, refresh_claims = create_token(
        subject_id=customer.id,
        audience=Audience.PUBLIC,
        token_type=TokenType.REFRESH,
    )
    session.add(
        CustomerRefreshToken(
            customer_id=customer.id,
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


async def _revoke(token: CustomerRefreshToken) -> None:
    token.revoked_at = utcnow()
    await get_redis().set(
        denylist_key(token.jti), "1", ex=denylist_ttl_for(token.expires_at)
    )


async def _revoke_all_sessions(session: AsyncSession, customer_id: uuid.UUID) -> None:
    """End every session this customer holds — including the access tokens.

    Revoking the stored refresh tokens is only half of it: an access token is
    never stored, so there is no row to mark and nothing to name it by. The
    subject-wide mark is the only thing that reaches it, and every caller of
    this function is somebody deciding that whoever holds those tokens must
    stop now.
    """
    for token in await repository.live_tokens_for(session, customer_id):
        await _revoke(token)
    await get_redis().set(
        revoked_before_key(customer_id),
        revoked_before_value(),
        ex=revoked_before_ttl(Audience.PUBLIC),
    )


def _decode_refresh(token: str) -> TokenClaims:
    try:
        claims = decode_token(token, expected_type=TokenType.REFRESH)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired refresh token") from exc
    if claims.audience is not Audience.PUBLIC:
        raise Forbidden("This token is not valid for this API surface")
    return claims


# --- codes ------------------------------------------------------------------------


def _generate_code() -> str:
    # Zero-padded: the leading zeros are part of the code, which is why it
    # travels as a string everywhere.
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def _may_send(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose
) -> bool:
    """Has the cooldown passed since the last code of this purpose?

    Answered rather than raised. Every caller is on a path that must not reveal
    whether the address exists, so a cooldown that is not up means *do not
    send*, not *tell them why*. The rate limit callers do get is API.md §14's,
    counted per IP by the router.
    """
    latest = await repository.latest_otp(session, customer.id, purpose)
    return latest is None or latest.sent_at + OTP_RESEND_COOLDOWN <= utcnow()


async def _issue_code(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose
) -> str:
    """Invalidate any open code and write a fresh one. Returns the plain code."""
    await repository.consume_open_otps(session, customer.id, purpose)
    code = _generate_code()
    now = utcnow()
    session.add(
        EmailOtp(
            customer_id=customer.id,
            purpose=purpose,
            code_hash=_hash_code(code),
            expires_at=now + OTP_TTL,
            sent_at=now,
        )
    )
    return code


_SUBJECTS: Final[dict[OtpPurpose, str]] = {
    OtpPurpose.REGISTER: "Confirm your email address",
    OtpPurpose.PASSWORD_RESET: "Password reset",
}


async def _send_code(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose, code: str
) -> None:
    # Which notifier depends on the installation's SMTP settings, so it takes a
    # session — a module owns that answer, not the provider package.
    notifier = await integrations_service.notifier(session)
    await notifier.send(
        recipient=customer.email,
        subject=_SUBJECTS[purpose],
        body=(
            f"Your confirmation code is {code}.\n"
            f"It expires in {int(OTP_TTL.total_seconds()) // 60} minutes."
        ),
        # The code also travels as a variable, because a real adapter renders a
        # template rather than sending this plain fallback text.
        context={
            "purpose": purpose.value,
            "customer_id": str(customer.id),
            "code": code,
        },
    )


async def _issue_and_send(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose
) -> None:
    """The whole cycle, cooldown included. Silent when the cooldown is not up."""
    if not await _may_send(session, customer, purpose):
        return
    code = await _issue_code(session, customer, purpose)
    await _send_code(session, customer, purpose, code)


async def _spend_code(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose, code: str
) -> None:
    """Check a code and use it up. Raises 422 for every way it can fail."""
    otp = await repository.latest_otp(session, customer.id, purpose)
    if (
        otp is None
        or otp.is_consumed
        or otp.expires_at <= utcnow()
        or otp.attempts >= OTP_MAX_ATTEMPTS
    ):
        raise ValidationFailed(_CODE_REJECTED, field="code")

    if not secrets.compare_digest(otp.code_hash, _hash_code(code)):
        otp.attempts += 1
        if otp.attempts >= OTP_MAX_ATTEMPTS:
            # Out of attempts: burn it rather than leave a dead row that the
            # next guess would have to be told about separately.
            otp.consumed_at = utcnow()
        # Committed before raising: the exception unwinds before the handler
        # would commit, and an attempt that is not counted is not a ceiling.
        await session.commit()
        raise ValidationFailed(_CODE_REJECTED, field="code")

    otp.consumed_at = utcnow()


# --- registration (API.md §18) ------------------------------------------------------


async def register(session: AsyncSession, data: RegisterIn) -> None:
    """Create the account, unverified, and mail the code. Always succeeds.

    A taken address is answered the same way, and the notice goes to the address
    itself. Anything else and this endpoint answers "does this person have an
    account here?" for anyone who asks.
    """
    email = _normalize_email(data.email)
    existing = await repository.by_email(session, email)

    if existing is not None and existing.is_verified:
        # The one message that goes to somebody who did not ask for it — which
        # is the point: the address holder is the only person entitled to know.
        notifier = await integrations_service.notifier(session)
        await notifier.send(
            recipient=existing.email,
            subject="You already have an account",
            body=(
                "Somebody tried to register with this address. "
                "You already have an account — use password reset if you "
                "cannot sign in."
            ),
            context={"purpose": "register_existing", "customer_id": str(existing.id)},
        )
        await session.commit()
        return

    if existing is not None:
        # Still unverified, so nobody has proved they own this address: let the
        # new details win. A mistyped password must not strand the address.
        existing.password_hash = hash_password(data.password)
        existing.first_name = data.first_name
        existing.last_name = data.last_name
        existing.phone = data.phone
        customer = existing
    else:
        customer = Customer(
            email=email,
            password_hash=hash_password(data.password),
            first_name=data.first_name,
            last_name=data.last_name,
            phone=data.phone,
        )
        session.add(customer)
        await session.flush()

    await _issue_and_send(session, customer, OtpPurpose.REGISTER)
    await session.commit()
    logger.info("customer_registered", customer_id=str(customer.id))


async def resend_registration_code(session: AsyncSession, email: str) -> None:
    """Mail the confirmation code again. Always succeeds, like ``register``."""
    customer = await repository.by_email(session, _normalize_email(email))
    if customer is None or customer.is_verified or customer.is_blocked:
        return
    await _issue_and_send(session, customer, OtpPurpose.REGISTER)
    await session.commit()


async def confirm_registration(
    session: AsyncSession,
    data: RegisterConfirmIn,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPairOut:
    customer = await repository.by_email(session, _normalize_email(data.email))
    if customer is None or customer.is_verified:
        # Same answer as a wrong code: an address with no pending registration
        # is not something this endpoint reports on.
        raise ValidationFailed(_CODE_REJECTED, field="code")
    if customer.is_blocked:
        raise Forbidden("This account has been blocked")

    await _spend_code(session, customer, OtpPurpose.REGISTER, data.code)
    now = utcnow()
    customer.email_verified_at = now
    customer.last_login_at = now
    pair = await _issue_pair(session, customer, user_agent=user_agent, ip=ip)
    await session.commit()
    logger.info("customer_verified", customer_id=str(customer.id))
    return pair


# --- sessions (API.md §18) ----------------------------------------------------------


async def authenticate(
    session: AsyncSession,
    data: LoginIn,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPairOut:
    email = _normalize_email(data.login)
    customer = await repository.by_email(session, email)
    # The same answer for "no such account" and "wrong password", and the hash
    # is verified either way so the two do not differ in how long they take.
    if customer is None:
        verify_password(data.password, hash_password(secrets.token_urlsafe(16)))
        raise Unauthorized("Invalid email or password")
    if not verify_password(data.password, customer.password_hash):
        raise Unauthorized("Invalid email or password")
    # Both of these are told only to somebody who already proved they know the
    # password. To them it is the answer they need; to anyone else it would be
    # a directory of the client's customers.
    if not customer.is_verified:
        raise Forbidden("This account has not been confirmed yet")
    if customer.is_blocked:
        raise Forbidden("This account has been blocked")

    if password_needs_rehash(customer.password_hash):
        customer.password_hash = hash_password(data.password)
    customer.last_login_at = utcnow()

    pair = await _issue_pair(session, customer, user_agent=user_agent, ip=ip)
    await session.commit()
    logger.info("customer_login", customer_id=str(customer.id))
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
        # customer has rather than only refusing this one.
        await _revoke_all_sessions(session, claims.subject_id)
        await session.commit()
        logger.warning("customer_refresh_reuse", customer_id=str(claims.subject_id))
        raise Unauthorized("This session has been revoked")

    customer = await repository.by_id(session, claims.subject_id)
    if customer is None:
        raise Unauthorized("This account no longer exists")
    if not customer.is_verified:
        raise Forbidden("This account has not been confirmed yet")
    if customer.is_blocked:
        raise Forbidden("This account has been blocked")

    await _revoke(stored)
    pair = await _issue_pair(session, customer, user_agent=user_agent, ip=ip)
    await session.commit()
    return pair


async def logout(session: AsyncSession, refresh_token: str) -> None:
    """Revoke one session. Idempotent — logging out twice is not an error."""
    claims = _decode_refresh(refresh_token)
    stored = await repository.token_by_jti(session, claims.jti)
    if stored is not None and not stored.is_revoked:
        stored.revoked_at = utcnow()
    await get_redis().set(denylist_key(claims.jti), "1", ex=denylist_ttl(claims))
    await session.commit()


async def get_active(session: AsyncSession, customer_id: uuid.UUID) -> Customer:
    """The live row behind a token — called on every authenticated request.

    This is what makes blocking and deletion take effect immediately instead of
    when the access token happens to expire.
    """
    customer = await repository.by_id(session, customer_id)
    if customer is None:
        raise Unauthorized("This account no longer exists")
    if not customer.is_verified:
        raise Forbidden("This account has not been confirmed yet")
    if customer.is_blocked:
        raise Forbidden("This account has been blocked")
    return customer


# --- password reset (API.md §18) ----------------------------------------------------


def _reset_key(token: str) -> str:
    # Stored hashed, so a dump of Redis is not a set of working reset tokens.
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"{_RESET_PREFIX}:{digest}"


async def request_password_reset(session: AsyncSession, email: str) -> None:
    """Mail a reset code. Always succeeds from the caller's point of view.

    Reporting "no such address" would let anyone test whether a person is a
    customer of this client, and the request is unauthenticated.
    """
    customer = await repository.by_email(session, _normalize_email(email))
    if customer is None or not customer.is_verified or customer.is_blocked:
        return
    await _issue_and_send(session, customer, OtpPurpose.PASSWORD_RESET)
    await session.commit()


async def verify_password_reset(
    session: AsyncSession, data: PasswordResetVerifyIn
) -> ResetTokenOut:
    """Spend the code and hand back the single-use token that sets the password.

    The middle step exists because API.md §18 asks for it, and it earns its
    place: the code is short and mailed, the token is long and never leaves the
    browser that asked for it, so the value carried across the password form is
    not the one somebody could have read over a shoulder.
    """
    customer = await repository.by_email(session, _normalize_email(data.email))
    if customer is None or not customer.is_verified or customer.is_blocked:
        raise ValidationFailed(_CODE_REJECTED, field="code")

    await _spend_code(session, customer, OtpPurpose.PASSWORD_RESET, data.code)
    token = secrets.token_urlsafe(32)
    await get_redis().set(
        _reset_key(token), str(customer.id), ex=RESET_TOKEN_TTL_SECONDS
    )
    await session.commit()
    return ResetTokenOut(reset_token=token, expires_in=RESET_TOKEN_TTL_SECONDS)


async def confirm_password_reset(
    session: AsyncSession, reset_token: str, new_password: str
) -> None:
    # GETDEL: the token is spent by looking it up, so two requests racing with
    # the same value cannot both set a password.
    raw = await get_redis().getdel(_reset_key(reset_token))
    if raw is None:
        raise ValidationFailed(_RESET_TOKEN_REJECTED, field="reset_token")
    try:
        customer_id = uuid.UUID(str(raw))
    except ValueError as exc:
        # Only reachable if something else wrote this key. Refusing beats a 500.
        raise ValidationFailed(_RESET_TOKEN_REJECTED, field="reset_token") from exc

    customer = await repository.by_id(session, customer_id)
    if customer is None or customer.is_blocked:
        raise ValidationFailed(_RESET_TOKEN_REJECTED, field="reset_token")

    customer.password_hash = hash_password(new_password)
    # Every session dies. A password reset is how somebody reacts to losing
    # control of the account, and leaving old sessions alive would make it
    # useless.
    await _revoke_all_sessions(session, customer.id)
    await session.commit()
    logger.info("customer_password_reset", customer_id=str(customer.id))


__all__ = [
    "OTP_MAX_ATTEMPTS",
    "OTP_RESEND_COOLDOWN",
    "OTP_TTL",
    "RESET_TOKEN_TTL_SECONDS",
    "authenticate",
    "confirm_password_reset",
    "confirm_registration",
    "get_active",
    "logout",
    "refresh_session",
    "register",
    "request_password_reset",
    "resend_registration_code",
    "verify_password_reset",
]
