"""Customer authentication and profile — API.md §18 and §19.

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

**A code is spent by being wrong, too.** Four digits is a tiny space; the
attempt ceiling is what makes it enough, so a wrong guess is recorded even
though the request is about to fail.

**Deleting an account empties the row, not just hides it.** PROJECT.md §13
says personal data is cleared; a soft delete alone conceals a row without
emptying it, so the two happen together.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Pagination
from app.api.envelope import Page
from app.api.errors import Forbidden, NotFound, Unauthorized, ValidationFailed
from app.api.listing import (
    ListQuery,
    apply_created_range,
    apply_ordering,
    apply_search,
    page,
    paginate,
)
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
    Passenger,
)
from app.modules.customers.schemas import (
    OTP_LENGTH,
    AccountDeleteIn,
    LoginIn,
    PassengerCreateIn,
    PassengerOut,
    PassengerUpdateIn,
    PasswordChangeIn,
    PasswordResetVerifyIn,
    ProfileOut,
    ProfileUpdateIn,
    RegisterConfirmIn,
    RegisterIn,
    ResetTokenOut,
    TokenPairOut,
)
from app.modules.integrations import service as integrations_service
from app.modules.payments import service as payments_service
from app.modules.settings import service as settings_service
from app.modules.uploads import service as uploads_service
from app.providers.notifications import html as mail_html
from app.providers.social.base import SocialAuthError
from app.providers.storage.base import UploadPurpose

logger = get_logger(__name__)

#: API.md §18. Long enough for mail to arrive and be read, short enough that a
#: forwarded message stops working.
OTP_TTL: Final = timedelta(minutes=10)
#: What makes four digits enough, and the reason it carries more weight than it
#: used to: the space is ten thousand, so without a ceiling the endpoint — which
#: is unauthenticated — would be a few seconds of typing. Five guesses burn the
#: code and the next one is a different number.
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

#: How large an avatar may be, asked of the module that owns the answer. The
#: router needs it to size its body read; this module does not second-guess it.
AVATAR_MAX_BYTES: Final = uploads_service.max_bytes(UploadPurpose.AVATAR)

#: What a scrubbed row's required name column holds. Not an empty string — a
#: reader of the table should be able to tell "erased" from "never filled in".
_REDACTED: Final = "[deleted]"

#: What ``?ordering=`` may name on the passenger list (API.md §6). Not the
#: column list — the columns this endpoint chose to expose.
_PASSENGER_ORDERING: Final = {
    "first_name": Passenger.first_name,
    "last_name": Passenger.last_name,
    "birth_date": Passenger.birth_date,
    "created_at": Passenger.created_at,
}


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
    # travels as a string everywhere. The width is ``OTP_LENGTH``'s and not a
    # second place to change it — ``schemas`` is what the contract validates
    # against, so a mismatch here would issue codes the API then refuses.
    return f"{secrets.randbelow(10**OTP_LENGTH):0{OTP_LENGTH}d}"


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


async def _record_code(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose, code: str
) -> None:
    """Invalidate any open code and write this one down as sent.

    Called *after* the message has left, never before. This row is what starts
    the resend cooldown, so writing it for a message the relay refused would
    leave the address unable to ask again for a minute, with nothing in the
    inbox to wait for. For the same reason the previous code is invalidated
    here and not earlier: until a new one has actually reached the reader, the
    one they may already be holding is the only one they have.
    """
    await repository.consume_open_otps(session, customer.id, purpose)
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


@dataclass(frozen=True, slots=True)
class _Message:
    """One mail, written once and rendered twice.

    The plain text and the HTML say the same sentences — they are the same
    message, so they are written in one place rather than in two that drift.
    Only the arrangement differs: text runs top to bottom, HTML puts the code
    in a box with ``footnotes`` under it.
    """

    subject: str
    heading: str
    paragraphs: tuple[str, ...] = ()
    footnotes: tuple[str, ...] = ()

    def text(self, code: str | None = None) -> str:
        lines = [*self.paragraphs]
        if code is not None:
            lines.append(f"Kod: {code}")
        lines.extend(self.footnotes)
        return "\n\n".join(lines)


#: Written in Uzbek — ``i18n.DEFAULT_LANGUAGE``, the language this installation
#: serves unless told otherwise. There is no per-customer language to pick by:
#: the chain in API.md §7 resolves *stored* content for a request that carries a
#: header, and a code is mailed with no request in sight. When the profile grows
#: a language column this dict grows a second key; until then a message
#: catalogue would be scaffolding with one language in it.
#:
#: The two purposes read differently on purpose. The same sentence for both
#: would leave the reset mail unable to say the one thing that matters when it
#: was not asked for — that ignoring it leaves the password alone.
#:
#: ``{minutes}`` is filled in below.
_CODE_MESSAGES: Final[dict[OtpPurpose, _Message]] = {
    OtpPurpose.REGISTER: _Message(
        subject="Ro'yxatdan o'tishni tasdiqlash",
        heading="Ro'yxatdan o'tishni tasdiqlang",
        paragraphs=("Quyidagi tasdiqlash kodini kiriting.",),
        footnotes=(
            "Kod {minutes} daqiqa amal qiladi.",
            "Agar ro'yxatdan o'tmagan bo'lsangiz, bu xabarga e'tibor bermang.",
        ),
    ),
    OtpPurpose.PASSWORD_RESET: _Message(
        subject="Parolni tiklash",
        heading="Parolni tiklash",
        paragraphs=("Yangi parol o'rnatish uchun quyidagi kodni kiriting.",),
        footnotes=(
            "Kod {minutes} daqiqa amal qiladi.",
            "Agar so'rov yubormagan bo'lsangiz, bu xabarga e'tibor bermang — "
            "parolingiz o'zgarmaydi.",
        ),
    ),
}

#: Not a code, so it has no box and no expiry — but it goes through the same
#: shell, because it is the same installation writing to the same person.
_EXISTING_ACCOUNT = _Message(
    subject="Sizda allaqachon akkaunt bor",
    heading="Sizda allaqachon akkaunt bor",
    paragraphs=(
        "Bu manzil bilan ro'yxatdan o'tishga urinish bo'ldi.",
        "Sizda allaqachon akkaunt bor. Kira olmayotgan bo'lsangiz, "
        "parolni tiklashdan foydalaning.",
    ),
)


async def _send(
    session: AsyncSession,
    *,
    recipient: str,
    message: _Message,
    code: str | None = None,
    context: dict[str, str],
) -> bool:
    """Hand one message to whichever notifier this installation uses.

    Which notifier depends on the SMTP settings, and the brand it is dressed in
    depends on the branding settings — both are asked of the module that owns
    them, never read from here (ARCHITECTURE.md §4).

    ``False`` when the relay would not take the message, and **never** an
    exception. Every caller in this module answers `204` for every address by
    design (API.md §18), so a refused message must not become a `500`: the
    difference between the two would tell an unauthenticated caller which
    addresses have an account here, which is the one thing these endpoints are
    shaped to hide. The owner is the one who needs the reason, and they get it
    from the log line below and from `notifications/test/` (API.md §29).

    Only the delivery is guarded. Reading the settings and rendering the message
    are this installation's own doing: if those break, the request deserves to
    fail loudly rather than quietly send nothing for every address at once.
    """
    notifier = await integrations_service.notifier(session)
    html = mail_html.render(
        brand=await settings_service.mail_brand(),
        heading=message.heading,
        paragraphs=message.paragraphs,
        code=code,
        footnotes=message.footnotes,
    )
    try:
        await notifier.send(
            recipient=recipient,
            subject=message.subject,
            body=message.text(code),
            html=html,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001 - the reason is logged, not raised
        logger.error(
            "mail_send_failed",
            purpose=context.get("purpose"),
            recipient=recipient,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
        return False
    return True


async def _send_code(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose, code: str
) -> bool:
    minutes = int(OTP_TTL.total_seconds()) // 60
    template = _CODE_MESSAGES[purpose]
    message = replace(
        template,
        footnotes=tuple(line.format(minutes=minutes) for line in template.footnotes),
    )
    return await _send(
        session,
        recipient=customer.email,
        message=message,
        code=code,
        # The code also travels as a variable: what the reader sees is a
        # rendered message, so the body text is nobody's contract.
        context={
            "purpose": purpose.value,
            "customer_id": str(customer.id),
            "code": code,
        },
    )


async def _issue_and_send(
    session: AsyncSession, customer: Customer, purpose: OtpPurpose
) -> None:
    """The whole cycle, cooldown included. Silent when the cooldown is not up.

    Generated, sent, and only then written down — the order is the point, and
    the reason is in ``_record_code``. A relay that refuses leaves no trace but
    a log line, so the very next attempt is free to try again.
    """
    if not await _may_send(session, customer, purpose):
        return
    code = _generate_code()
    if await _send_code(session, customer, purpose, code):
        await _record_code(session, customer, purpose, code)


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
        await _send(
            session,
            recipient=existing.email,
            message=_EXISTING_ACCOUNT,
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


async def authenticate_social(
    session: AsyncSession,
    provider: str,
    id_token: str,
    *,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPairOut:
    """Sign in with a provider's identity token (API.md §18).

    The account comes back **verified**. That is not a shortcut: the email OTP
    exists to prove somebody controls an address, and a provider that says
    ``email_verified`` has proved exactly that already. A provider that says
    otherwise is refused rather than trusted — an unverified address on a
    profile is a claim anybody can make about somebody else's mailbox.
    """
    verifier = await integrations_service.social_verifier(session, provider)
    if verifier is None:
        # Switched off, unconfigured, or a provider this release does not have.
        # All three are "there is no such sign-in here" (API.md §29).
        raise NotFound("This sign-in method is not available")

    try:
        identity = await verifier.verify(id_token)
    except SocialAuthError as exc:
        logger.warning("social_token_rejected", provider=provider, error=str(exc))
        # One answer for every way a token can be bad. Which way it is bad is
        # not something the holder of a bad token benefits from learning.
        raise Unauthorized("This sign-in could not be verified") from exc

    if not identity.email_verified:
        raise Unauthorized("This sign-in could not be verified")

    email = _normalize_email(identity.email)
    customer = await repository.by_email(session, email)
    now = utcnow()

    if customer is None:
        customer = Customer(
            email=email,
            # No password is set, and none can be guessed: an empty hash never
            # verifies. Whoever wants one asks for a password reset, which
            # proves the same address a second time.
            password_hash="",
            # Whatever the provider gave, including nothing: the column is
            # nullable and a name derived from the address would be a guess
            # presented to the customer as their own name.
            first_name=identity.first_name,
            last_name=identity.last_name,
            email_verified_at=now,
        )
        session.add(customer)
        await session.flush()
        logger.info("customer_registered_social", customer_id=str(customer.id))
    elif not customer.is_verified:
        # The address was typed here first and never confirmed. The provider
        # has now confirmed it, so the pending row becomes the account rather
        # than a second one appearing beside it.
        customer.email_verified_at = now

    if customer.is_blocked:
        raise Forbidden("This account has been blocked")

    customer.last_login_at = now
    pair = await _issue_pair(session, customer, user_agent=user_agent, ip=ip)
    await session.commit()
    logger.info(
        "customer_login_social", customer_id=str(customer.id), provider=provider
    )
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


# --- the profile (API.md §19) --------------------------------------------------------


async def to_profile(session: AsyncSession, customer: Customer) -> ProfileOut:
    """Built field by field rather than from attributes, because ``avatar_url``
    is not a column — it is asked of the module that owns the file, and that
    lookup is tolerant: a file swept out from under the row renders as "no
    avatar" rather than as a 500. ``settings/service.py::_branding_out`` does
    the same for the logo."""
    return ProfileOut(
        id=customer.id,
        email=customer.email,
        first_name=customer.first_name,
        last_name=customer.last_name,
        middle_name=customer.middle_name,
        phone=customer.phone,
        birth_date=customer.birth_date,
        avatar_id=customer.avatar_id,
        avatar_url=await uploads_service.url_for(session, customer.avatar_id),
        created_at=customer.created_at,
        is_profile_complete=customer.is_profile_complete,
    )


async def update_profile(
    session: AsyncSession, customer: Customer, data: ProfileUpdateIn
) -> ProfileOut:
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    await session.commit()
    return await to_profile(session, customer)


async def change_password(
    session: AsyncSession, customer: Customer, data: PasswordChangeIn
) -> None:
    if not verify_password(data.current_password, customer.password_hash):
        # 422 rather than 401: the caller is authenticated, it is the field
        # that is wrong.
        raise ValidationFailed(
            "Current password is incorrect", field="current_password"
        )
    customer.password_hash = hash_password(data.new_password)
    # Every session dies, including this one's. A password change is how
    # somebody reacts to a suspected leak; leaving the old ones alive would
    # make it pointless.
    await _revoke_all_sessions(session, customer.id)
    await session.commit()
    logger.info("customer_password_changed", customer_id=str(customer.id))


# --- avatar (API.md §19) -------------------------------------------------------------


async def set_avatar(
    session: AsyncSession,
    customer: Customer,
    *,
    content: bytes,
    filename: str,
    content_type: str,
) -> ProfileOut:
    """Store the bytes, point the row at them, release whatever it held.

    The upload goes straight here rather than through ``/admin/uploads/``:
    the public surface has no uploads resource and a customer token cannot
    reach the admin one (API.md §4, §11).
    """
    uploaded = await uploads_service.create(
        session,
        content=content,
        filename=filename,
        content_type=content_type,
        purpose=UploadPurpose.AVATAR,
        uploaded_by=customer.id,
    )
    previous = customer.avatar_id
    if previous == uploaded.id:  # pragma: no cover - a fresh id never collides
        return await to_profile(session, customer)

    # Link first: it is the step that can still refuse, and nothing has been
    # mutated yet (settings/service.py::_attach_image does the same).
    await uploads_service.link(session, uploaded.id, purpose=UploadPurpose.AVATAR)
    if previous is not None:
        # Released, not deleted — the sweep collects it after the 24h grace, so
        # a customer who changes their mind has not lost the old one yet.
        await uploads_service.unlink(session, previous)
    customer.avatar_id = uploaded.id
    await session.commit()
    return await to_profile(session, customer)


async def clear_avatar(session: AsyncSession, customer: Customer) -> None:
    if customer.avatar_id is None:
        return
    await uploads_service.unlink(session, customer.avatar_id)
    customer.avatar_id = None
    await session.commit()


# --- account deletion (API.md §19, PROJECT.md §13) -----------------------------------


async def delete_account(
    session: AsyncSession, customer: Customer, data: AccountDeleteIn
) -> None:
    """Scrub the personal data and end the account.

    PROJECT.md §13 says personal data is *cleared*, not merely hidden — so a
    soft delete on its own would not do: ``deleted_at`` conceals a row, it does
    not empty it. Order and payment records would stay, anonymised, as
    financial documents; there are none yet in this phase.

    The address is emptied too, which is also what frees it: the unique index
    covers live rows only, so the same person can register again afterwards.
    """
    if not verify_password(data.password, customer.password_hash):
        raise ValidationFailed("Password is incorrect", field="password")

    if customer.avatar_id is not None:
        await uploads_service.unlink(session, customer.avatar_id)
        customer.avatar_id = None
    for passenger in await repository.live_passengers_for(session, customer.id):
        passenger.soft_delete()
    # A service call, not a cascade: ``customer_cards`` belongs to ``payments``
    # and carries no foreign key back here (ARCHITECTURE.md §4, §5). It also has
    # to reach the provider, which no database constraint could do.
    await payments_service.forget_cards(session, customer.id)

    customer.email = f"deleted-{customer.id}@invalid"
    customer.first_name = _REDACTED
    customer.last_name = None
    customer.middle_name = None
    customer.phone = None
    customer.birth_date = None
    # Not a hash of anything: argon2 will never verify against it, so no
    # password can reopen the account.
    customer.password_hash = ""
    customer.soft_delete()

    await _revoke_all_sessions(session, customer.id)
    await session.commit()
    logger.info("customer_deleted", customer_id=str(customer.id))


# --- saved passengers (API.md §19) ---------------------------------------------------


async def list_passengers(
    session: AsyncSession,
    customer: Customer,
    pagination: Pagination,
    query: ListQuery,
) -> Page[PassengerOut]:
    stmt = repository.owned_passengers(customer.id)
    stmt = apply_search(stmt, query, Passenger.first_name, Passenger.last_name)
    stmt = apply_created_range(stmt, query, Passenger.created_at)
    stmt = apply_ordering(
        stmt,
        query,
        allowed=_PASSENGER_ORDERING,
        default="-created_at",
        tiebreak=Passenger.id,
    )
    rows, total = await paginate(session, stmt, pagination)
    return page([PassengerOut.model_validate(row) for row in rows], pagination, total)


async def _require_passenger(
    session: AsyncSession, customer: Customer, passenger_id: uuid.UUID
) -> Passenger:
    """404 for a passenger that does not exist **and** for somebody else's.

    Two different situations, one answer on purpose: telling them apart would
    let a caller enumerate which ids exist across the whole installation
    (API.md §19).
    """
    passenger = await repository.passenger_by_id(session, customer.id, passenger_id)
    if passenger is None:
        raise NotFound("Passenger not found")
    return passenger


async def get_passenger(
    session: AsyncSession, customer: Customer, passenger_id: uuid.UUID
) -> PassengerOut:
    return PassengerOut.model_validate(
        await _require_passenger(session, customer, passenger_id)
    )


async def create_passenger(
    session: AsyncSession, customer: Customer, data: PassengerCreateIn
) -> PassengerOut:
    passenger = Passenger(customer_id=customer.id, **data.model_dump())
    session.add(passenger)
    await session.commit()
    await session.refresh(passenger)
    return PassengerOut.model_validate(passenger)


async def update_passenger(
    session: AsyncSession,
    customer: Customer,
    passenger_id: uuid.UUID,
    data: PassengerUpdateIn,
) -> PassengerOut:
    passenger = await _require_passenger(session, customer, passenger_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(passenger, field, value)
    await session.commit()
    await session.refresh(passenger)
    return PassengerOut.model_validate(passenger)


async def delete_passenger(
    session: AsyncSession, customer: Customer, passenger_id: uuid.UUID
) -> None:
    passenger = await _require_passenger(session, customer, passenger_id)
    passenger.soft_delete()
    await session.commit()


__all__ = [
    "AVATAR_MAX_BYTES",
    "OTP_MAX_ATTEMPTS",
    "OTP_RESEND_COOLDOWN",
    "OTP_TTL",
    "RESET_TOKEN_TTL_SECONDS",
    "authenticate",
    "authenticate_social",
    "change_password",
    "clear_avatar",
    "confirm_password_reset",
    "confirm_registration",
    "create_passenger",
    "delete_account",
    "delete_passenger",
    "get_active",
    "get_passenger",
    "list_passengers",
    "logout",
    "refresh_session",
    "register",
    "request_password_reset",
    "resend_registration_code",
    "set_avatar",
    "to_profile",
    "update_passenger",
    "update_profile",
    "verify_password_reset",
]
