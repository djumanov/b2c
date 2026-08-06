"""Customer registration and sessions — API.md §18.

The staff equivalent is ``test_staff_auth.py`` and the two overlap on purpose:
rotation, reuse detection and the ``revoked_before`` mark are one mechanism with
two sets of lifetimes, and a regression that reaches one audience would reach
the other.

What is only here is the half that does not exist on the panel: an account is
created before anybody has proved they own the address, so every test below that
touches an unverified row is asking the same question — does this row grant
anything yet?
"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Audience, TokenType
from app.db.mixins import utcnow
from app.db.redis import get_redis
from app.modules.customers.models import Customer, EmailOtp
from app.modules.customers.service import OTP_RESEND_COOLDOWN
from tests.conftest import bearer, issue_token
from tests.integration.conftest import (
    PASSWORD,
    RecordingNotifier,
    customer_headers_for,
    make_customer,
)

REGISTER = "/api/v1/public/auth/register/"
CONFIRM = "/api/v1/public/auth/register/confirm/"
RESEND = "/api/v1/public/auth/register/resend/"
LOGIN = "/api/v1/public/auth/login/"
REFRESH = "/api/v1/public/auth/refresh/"
LOGOUT = "/api/v1/public/auth/logout/"

NEW_EMAIL = "newcomer@example.uz"


async def _register(api: AsyncClient, email: str = NEW_EMAIL) -> None:
    response = await api.post(
        REGISTER,
        json={"email": email, "password": PASSWORD, "first_name": "New Person"},
    )
    assert response.status_code == 204, response.text


async def _expire_cooldown(session: AsyncSession, email: str) -> None:
    """Age the last code so the resend cooldown has passed.

    The alternative is a test that sleeps for a minute. What is under test here
    is what a second request does, not how the clock is read.
    """
    customer = await session.scalar(select(Customer).where(Customer.email == email))
    assert customer is not None
    otp = await session.scalar(
        select(EmailOtp)
        .where(EmailOtp.customer_id == customer.id)
        .order_by(EmailOtp.sent_at.desc())
        .limit(1)
    )
    assert otp is not None
    otp.sent_at = utcnow() - OTP_RESEND_COOLDOWN * 2
    await session.commit()


# --- registration ---------------------------------------------------------------------


async def test_register_then_confirm_returns_a_token_pair(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    await _register(api)
    assert notifier.sent[-1]["recipient"] == NEW_EMAIL

    confirmed = await api.post(
        CONFIRM, json={"email": NEW_EMAIL, "code": notifier.last_code}
    )

    assert confirmed.status_code == 200, confirmed.text
    data = confirmed.json()["data"]
    assert data["access_token"] and data["refresh_token"]
    # API.md §4: the customer access token lives half an hour.
    assert data["expires_in"] == 30 * 60


async def test_an_unconfirmed_account_cannot_sign_in(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    """The row exists after ``register/``. It is a reservation, not an account."""
    await _register(api)

    response = await api.post(LOGIN, json={"login": NEW_EMAIL, "password": PASSWORD})

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "forbidden"


async def test_a_taken_address_is_answered_the_same_way(
    api: AsyncClient, customer: Customer, notifier: RecordingNotifier
) -> None:
    """204 either way, or this endpoint reports who has an account here."""
    response = await api.post(
        REGISTER,
        json={
            "email": customer.email,
            "password": "some-other-password",
            "first_name": "Impostor",
        },
    )

    assert response.status_code == 204
    # The notice goes to the address itself — the only party entitled to it —
    # and carries no code, so it is not a way in.
    assert notifier.sent[-1]["recipient"] == customer.email
    assert "code" not in notifier.sent[-1]["context"]


async def test_registering_again_before_confirming_replaces_the_code(
    api: AsyncClient, notifier: RecordingNotifier, session: AsyncSession
) -> None:
    """A mistyped password must not strand the address forever."""
    await _register(api)
    first_code = notifier.last_code

    # Past the cooldown, or the second attempt would be silently dropped.
    await _expire_cooldown(session, NEW_EMAIL)
    response = await api.post(
        REGISTER,
        json={"email": NEW_EMAIL, "password": "the-corrected-one", "first_name": "New"},
    )
    assert response.status_code == 204
    second_code = notifier.last_code

    stale = await api.post(CONFIRM, json={"email": NEW_EMAIL, "code": first_code})
    assert stale.status_code == 422

    confirmed = await api.post(CONFIRM, json={"email": NEW_EMAIL, "code": second_code})
    assert confirmed.status_code == 200
    # The new password is the one that works.
    signed_in = await api.post(
        LOGIN, json={"login": NEW_EMAIL, "password": "the-corrected-one"}
    )
    assert signed_in.status_code == 200


async def _clear_rate_limit() -> None:
    """Forget API.md §14's per-IP window.

    Two different mechanisms guard a code: the §14 limiter counts requests per
    IP per minute, and the attempt ceiling counts wrong guesses per code for as
    long as the code lives. They happen to be the same number, so from one
    address the limiter always trips first — which is why the ceiling has to be
    tested with the limiter out of the way. It is the one that still holds
    against a caller patient enough to spread the guesses out.
    """
    redis = get_redis()
    async for key in redis.scan_iter("ratelimit:*"):
        await redis.delete(key)


async def test_a_wrong_code_is_422_and_the_attempts_run_out(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    """Six digits is a small space; the ceiling is what makes it enough."""
    await _register(api)
    real_code = notifier.last_code

    for _ in range(5):
        await _clear_rate_limit()
        wrong = await api.post(CONFIRM, json={"email": NEW_EMAIL, "code": "000000"})
        assert wrong.status_code == 422
        assert wrong.json()["errors"][0]["field"] == "code"
    await _clear_rate_limit()

    # Burnt: even the code that was actually mailed no longer works.
    assert real_code != "000000"
    spent = await api.post(CONFIRM, json={"email": NEW_EMAIL, "code": real_code})
    assert spent.status_code == 422


async def test_the_code_is_single_use(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    await _register(api)
    code = notifier.last_code

    first = await api.post(CONFIRM, json={"email": NEW_EMAIL, "code": code})
    assert first.status_code == 200

    replay = await api.post(CONFIRM, json={"email": NEW_EMAIL, "code": code})
    assert replay.status_code == 422


async def test_resend_within_the_cooldown_sends_nothing_and_still_says_204(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    """A visible cooldown would say whether the address has a code waiting."""
    await _register(api)
    sent_so_far = len(notifier.sent)

    response = await api.post(RESEND, json={"email": NEW_EMAIL})

    assert response.status_code == 204
    assert len(notifier.sent) == sent_so_far


async def test_resend_for_an_unknown_address_is_204_and_silent(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    response = await api.post(RESEND, json={"email": "nobody@example.uz"})

    assert response.status_code == 204
    assert notifier.sent == []


# --- signing in -----------------------------------------------------------------------


async def test_a_confirmed_customer_can_sign_in(
    api: AsyncClient, customer: Customer
) -> None:
    response = await api.post(
        LOGIN, json={"login": customer.email, "password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["expires_in"] == 30 * 60


async def test_login_is_case_insensitive_in_the_email(
    api: AsyncClient, customer: Customer
) -> None:
    response = await api.post(
        LOGIN, json={"login": customer.email.upper(), "password": PASSWORD}
    )

    assert response.status_code == 200


async def test_a_wrong_password_and_an_unknown_address_are_indistinguishable(
    api: AsyncClient, customer: Customer
) -> None:
    """Anything else and the login form is a directory of this client's buyers."""
    wrong_password = await api.post(
        LOGIN, json={"login": customer.email, "password": "not-the-password"}
    )
    unknown_address = await api.post(
        LOGIN, json={"login": "nobody@example.uz", "password": "not-the-password"}
    )

    assert wrong_password.status_code == unknown_address.status_code == 401
    assert wrong_password.json()["errors"] == unknown_address.json()["errors"]


async def test_a_blocked_account_is_403_after_the_password_checks_out(
    api: AsyncClient, session: AsyncSession
) -> None:
    blocked = await make_customer(session, email="blocked@example.uz", is_blocked=True)

    refused = await api.post(LOGIN, json={"login": blocked.email, "password": PASSWORD})
    assert refused.status_code == 403

    # Told apart from the wrong password, which is 401 — but only to somebody
    # who already proved they know it.
    wrong = await api.post(
        LOGIN, json={"login": blocked.email, "password": "not-the-password"}
    )
    assert wrong.status_code == 401


# --- sessions -------------------------------------------------------------------------


async def test_refresh_rotates_and_kills_the_old_token(
    api: AsyncClient, customer: Customer
) -> None:
    signed_in = await api.post(
        LOGIN, json={"login": customer.email, "password": PASSWORD}
    )
    first = signed_in.json()["data"]["refresh_token"]

    rotated = await api.post(REFRESH, json={"refresh_token": first})
    assert rotated.status_code == 200
    assert rotated.json()["data"]["refresh_token"] != first

    replay = await api.post(REFRESH, json={"refresh_token": first})
    assert replay.status_code == 401


async def test_replaying_a_revoked_refresh_ends_every_session(
    api: AsyncClient, customer: Customer
) -> None:
    """A token presented after revocation is a replay or a stolen copy. The
    cost of being wrong is one extra login."""
    first = (
        await api.post(LOGIN, json={"login": customer.email, "password": PASSWORD})
    ).json()["data"]["refresh_token"]
    second = (await api.post(REFRESH, json={"refresh_token": first})).json()["data"][
        "refresh_token"
    ]

    await api.post(REFRESH, json={"refresh_token": first})

    # The session that was still good is gone too.
    assert (await api.post(REFRESH, json={"refresh_token": second})).status_code == 401


async def test_an_access_token_is_not_accepted_as_a_refresh_token(
    api: AsyncClient, customer: Customer
) -> None:
    access = (
        await api.post(LOGIN, json={"login": customer.email, "password": PASSWORD})
    ).json()["data"]["access_token"]

    response = await api.post(REFRESH, json={"refresh_token": access})

    assert response.status_code == 401


async def test_logout_revokes_the_refresh_token(
    api: AsyncClient, customer: Customer
) -> None:
    data = (
        await api.post(LOGIN, json={"login": customer.email, "password": PASSWORD})
    ).json()["data"]

    signed_out = await api.post(
        LOGOUT,
        json={"refresh_token": data["refresh_token"]},
        headers=bearer(data["access_token"]),
    )
    assert signed_out.status_code == 204

    assert (
        await api.post(REFRESH, json={"refresh_token": data["refresh_token"]})
    ).status_code == 401


async def test_logout_needs_a_token_of_its_own(
    api: AsyncClient, customer: Customer
) -> None:
    """API.md §18 marks ``logout/`` ✓ — a leaked refresh token is not also a
    way to sign somebody out."""
    data = (
        await api.post(LOGIN, json={"login": customer.email, "password": PASSWORD})
    ).json()["data"]

    response = await api.post(LOGOUT, json={"refresh_token": data["refresh_token"]})

    assert response.status_code == 401


# --- what the row decides, not the claim ---------------------------------------------


async def test_a_customer_token_is_403_on_the_admin_surface(
    api: AsyncClient, customer: Customer
) -> None:
    """API.md §4: the caller proved who they are, they are just not staff."""
    response = await api.get(
        "/api/v1/admin/staff/", headers=customer_headers_for(customer)
    )

    assert response.status_code == 403


async def test_a_deleted_customer_loses_access_immediately(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    headers = customer_headers_for(customer)
    customer.soft_delete()
    await session.commit()

    response = await api.post(
        LOGOUT, json={"refresh_token": "irrelevant"}, headers=headers
    )

    assert response.status_code == 401


async def test_a_blocked_customer_loses_access_immediately(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    headers = customer_headers_for(customer)
    customer.is_blocked = True
    await session.commit()

    response = await api.post(
        LOGOUT, json={"refresh_token": "irrelevant"}, headers=headers
    )

    assert response.status_code == 403


async def test_an_unconfirmed_customer_holding_a_token_still_gets_nothing(
    api: AsyncClient, session: AsyncSession
) -> None:
    """The row is loaded on every request, so a token minted for an account
    that was never confirmed is not a way past ``register/confirm/``."""
    unconfirmed = await make_customer(
        session, email="pending@example.uz", is_verified=False
    )
    headers = bearer(
        issue_token(
            Audience.PUBLIC, subject_id=unconfirmed.id, token_type=TokenType.ACCESS
        )
    )

    response = await api.post(
        LOGOUT, json={"refresh_token": "irrelevant"}, headers=headers
    )

    assert response.status_code == 403
