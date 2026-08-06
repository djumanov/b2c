"""Forgotten customer passwords — API.md §18, the three-step flow.

Unlike the staff surface's two steps, the code and the token are separate
artefacts here: the code is short and travels by mail, the token is long and
never leaves the browser that asked for it. So the tests below check both that
the code buys a token and that the token — not the code — is what sets the
password.

The delivery adapter is swapped for the recorder ``test_customer_auth`` defines;
the code is read out of ``context``, never scraped from the body.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.conftest import PASSWORD, RecordingNotifier, make_customer

REQUEST = "/api/v1/public/auth/password/reset/request/"
VERIFY = "/api/v1/public/auth/password/reset/verify/"
CONFIRM = "/api/v1/public/auth/password/reset/confirm/"
LOGIN = "/api/v1/public/auth/login/"
REFRESH = "/api/v1/public/auth/refresh/"

NEW_PASSWORD = "a-brand-new-secret"


async def test_the_three_steps_set_a_new_password(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    customer = await make_customer(session)

    requested = await api.post(REQUEST, json={"email": customer.email})
    assert requested.status_code == 204
    assert notifier.sent[-1]["recipient"] == customer.email

    verified = await api.post(
        VERIFY, json={"email": customer.email, "code": notifier.last_code}
    )
    assert verified.status_code == 200, verified.text
    token = verified.json()["data"]["reset_token"]
    assert verified.json()["data"]["expires_in"] == 15 * 60

    confirmed = await api.post(
        CONFIRM, json={"reset_token": token, "new_password": NEW_PASSWORD}
    )
    assert confirmed.status_code == 204

    signed_in = await api.post(
        LOGIN, json={"login": customer.email, "password": NEW_PASSWORD}
    )
    assert signed_in.status_code == 200


async def test_the_reset_token_is_single_use(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    customer = await make_customer(session)
    await api.post(REQUEST, json={"email": customer.email})
    token = (
        await api.post(
            VERIFY, json={"email": customer.email, "code": notifier.last_code}
        )
    ).json()["data"]["reset_token"]

    first = await api.post(
        CONFIRM, json={"reset_token": token, "new_password": NEW_PASSWORD}
    )
    assert first.status_code == 204

    replay = await api.post(
        CONFIRM, json={"reset_token": token, "new_password": "yet-another-secret"}
    )
    assert replay.status_code == 422
    assert replay.json()["errors"][0]["field"] == "reset_token"


async def test_the_code_alone_does_not_set_a_password(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    """The middle step is not decoration — ``confirm/`` takes the token only."""
    customer = await make_customer(session)
    await api.post(REQUEST, json={"email": customer.email})

    response = await api.post(
        CONFIRM,
        json={"reset_token": notifier.last_code, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 422


async def test_the_code_is_spent_by_verifying(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    customer = await make_customer(session)
    await api.post(REQUEST, json={"email": customer.email})
    code = notifier.last_code

    assert (
        await api.post(VERIFY, json={"email": customer.email, "code": code})
    ).status_code == 200

    replay = await api.post(VERIFY, json={"email": customer.email, "code": code})
    assert replay.status_code == 422


async def test_resetting_the_password_ends_every_session(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    """A reset is how somebody reacts to losing control of the account."""
    customer = await make_customer(session)
    refresh_token = (
        await api.post(LOGIN, json={"login": customer.email, "password": PASSWORD})
    ).json()["data"]["refresh_token"]

    await api.post(REQUEST, json={"email": customer.email})
    token = (
        await api.post(
            VERIFY, json={"email": customer.email, "code": notifier.last_code}
        )
    ).json()["data"]["reset_token"]
    await api.post(CONFIRM, json={"reset_token": token, "new_password": NEW_PASSWORD})

    assert (
        await api.post(REFRESH, json={"refresh_token": refresh_token})
    ).status_code == 401


async def test_an_unknown_address_is_answered_the_same_way(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    """204 and silence — the request is unauthenticated."""
    response = await api.post(REQUEST, json={"email": "nobody@example.uz"})

    assert response.status_code == 204
    assert notifier.sent == []


async def test_a_blocked_account_gets_no_reset_code(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    blocked = await make_customer(session, email="blocked@example.uz", is_blocked=True)

    response = await api.post(REQUEST, json={"email": blocked.email})

    assert response.status_code == 204
    assert notifier.sent == []


async def test_an_unconfirmed_account_gets_no_reset_code(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    """Nobody has proved they own the address, so a reset would hand it over.
    The way in is ``register/resend/``."""
    pending = await make_customer(
        session, email="pending@example.uz", is_verified=False
    )

    response = await api.post(REQUEST, json={"email": pending.email})

    assert response.status_code == 204
    assert notifier.sent == []


async def test_a_made_up_reset_token_is_422(
    api: AsyncClient, session: AsyncSession
) -> None:
    await make_customer(session)

    response = await api.post(
        CONFIRM, json={"reset_token": "not-a-real-token", "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 422
