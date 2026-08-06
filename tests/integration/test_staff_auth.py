"""Signing in to the panel — API.md §27.

The first acceptance criterion of phase 1 is "an owner can sign in to the
panel" (PROJECT.md §15), and this is where that claim is actually made.
"""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import Role
from app.core.security import Audience, TokenType, create_token
from app.modules.staff.models import Staff
from tests.integration.conftest import PASSWORD, headers_for, make_staff

LOGIN = "/api/v1/admin/auth/login/"
REFRESH = "/api/v1/admin/auth/refresh/"
LOGOUT = "/api/v1/admin/auth/logout/"
ME = "/api/v1/admin/auth/me/"
CHANGE = "/api/v1/admin/auth/password/change/"


async def _login(api: AsyncClient, staff: Staff) -> dict[str, str]:
    response = await api.post(LOGIN, json={"login": staff.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    data: dict[str, str] = response.json()["data"]
    return data


async def test_owner_can_sign_in(api: AsyncClient, owner: Staff) -> None:
    tokens = await _login(api, owner)

    assert tokens["access_token"]
    assert tokens["refresh_token"]
    # API.md §4: staff access tokens live fifteen minutes.
    assert tokens["expires_in"] == 15 * 60


async def test_me_reports_the_role_the_panel_builds_its_menu_from(
    api: AsyncClient, owner: Staff
) -> None:
    tokens = await _login(api, owner)

    response = await api.get(
        ME, headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "id": str(owner.id),
        "name": owner.name,
        "email": owner.email,
        "role": "owner",
    }


async def test_wrong_password_is_401(api: AsyncClient, owner: Staff) -> None:
    response = await api.post(
        LOGIN, json={"login": owner.email, "password": "not-the-password"}
    )

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "unauthorized"


async def test_unknown_email_is_401_and_says_the_same_thing(
    api: AsyncClient, owner: Staff
) -> None:
    """No hint that the address is not registered — the form is public."""
    unknown = await api.post(
        LOGIN, json={"login": "nobody@example.uz", "password": PASSWORD}
    )
    wrong = await api.post(
        LOGIN, json={"login": owner.email, "password": "not-the-password"}
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["errors"] == wrong.json()["errors"]


async def test_login_is_case_insensitive_in_the_email(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.post(
        LOGIN, json={"login": owner.email.upper(), "password": PASSWORD}
    )

    assert response.status_code == 200


async def test_blocked_account_is_403_after_the_password_checks_out(
    api: AsyncClient, session: AsyncSession
) -> None:
    blocked = await make_staff(
        session, email="blocked@example.uz", role=Role.ADMIN, is_blocked=True
    )

    response = await api.post(
        LOGIN, json={"login": blocked.email, "password": PASSWORD}
    )

    assert response.status_code == 403
    assert "blocked" in response.json()["errors"][0]["message"].lower()


async def test_refresh_rotates_and_kills_the_old_token(
    api: AsyncClient, owner: Staff
) -> None:
    first = await _login(api, owner)

    second = await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    assert second.json()["data"]["refresh_token"] != first["refresh_token"]

    # API.md §4: every refresh revokes the one it was handed.
    replay = await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401


async def test_replaying_a_revoked_refresh_ends_every_session(
    api: AsyncClient, owner: Staff
) -> None:
    """Reuse is a theft signal, so the session it was rotated into dies too."""
    first = await _login(api, owner)
    second = await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    live_token = second.json()["data"]["refresh_token"]

    await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})

    response = await api.post(REFRESH, json={"refresh_token": live_token})
    assert response.status_code == 401


async def test_an_access_token_is_not_accepted_as_a_refresh_token(
    api: AsyncClient, owner: Staff
) -> None:
    tokens = await _login(api, owner)

    response = await api.post(REFRESH, json={"refresh_token": tokens["access_token"]})

    assert response.status_code == 401


async def test_logout_revokes_the_refresh_token(api: AsyncClient, owner: Staff) -> None:
    tokens = await _login(api, owner)
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    response = await api.post(
        LOGOUT, json={"refresh_token": tokens["refresh_token"]}, headers=auth
    )
    assert response.status_code == 204

    again = await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 401


async def test_logout_requires_a_token_of_its_own(
    api: AsyncClient, owner: Staff
) -> None:
    tokens = await _login(api, owner)

    response = await api.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 401


async def test_changing_the_password_ends_every_session(
    api: AsyncClient, owner: Staff
) -> None:
    tokens = await _login(api, owner)
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    response = await api.post(
        CHANGE,
        json={"current_password": PASSWORD, "new_password": "a-brand-new-secret"},
        headers=auth,
    )
    assert response.status_code == 204

    stale = await api.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert stale.status_code == 401

    fresh = await api.post(
        LOGIN, json={"login": owner.email, "password": "a-brand-new-secret"}
    )
    assert fresh.status_code == 200


def _token_issued_a_minute_ago(staff: Staff) -> dict[str, str]:
    """A token that is already in somebody else's hands.

    Minted with an earlier `iat` on purpose: a token issued in the same second
    as the revocation survives by design (`security.is_revoked_before`), and a
    test that logged in and immediately changed the password would be asserting
    against that one-second window rather than against the leak.
    """
    token, _ = create_token(
        subject_id=staff.id,
        audience=Audience.ADMIN,
        token_type=TokenType.ACCESS,
        role=staff.role.value,
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    return {"Authorization": f"Bearer {token}"}


async def test_changing_the_password_kills_the_access_token_too(
    api: AsyncClient, owner: Staff
) -> None:
    """Ending every session has to reach the one that is not stored anywhere.

    A refresh token is a row and can be revoked by name. An access token is
    not, so without this it would go on working for its full fifteen minutes —
    in the hands of exactly the person the password change was a reaction to.
    """
    leaked = _token_issued_a_minute_ago(owner)
    assert (await api.get(ME, headers=leaked)).status_code == 200

    changed = await api.post(
        CHANGE,
        json={"current_password": PASSWORD, "new_password": "a-brand-new-secret"},
        headers=headers_for(owner),
    )
    assert changed.status_code == 204

    assert (await api.get(ME, headers=leaked)).status_code == 401


async def test_replaying_a_revoked_refresh_kills_the_access_token_too(
    api: AsyncClient, owner: Staff
) -> None:
    """Reuse is the theft signal, so the thief's access token goes with it."""
    leaked = _token_issued_a_minute_ago(owner)
    first = await _login(api, owner)
    await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})

    replay = await api.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401

    assert (await api.get(ME, headers=leaked)).status_code == 401


async def test_a_session_opened_after_the_change_is_left_alone(
    api: AsyncClient, owner: Staff
) -> None:
    """The mark ends what came before it, not what comes after."""
    await api.post(
        CHANGE,
        json={"current_password": PASSWORD, "new_password": "a-brand-new-secret"},
        headers=headers_for(owner),
    )

    tokens = await api.post(
        LOGIN, json={"login": owner.email, "password": "a-brand-new-secret"}
    )
    auth = {"Authorization": f"Bearer {tokens.json()['data']['access_token']}"}

    assert (await api.get(ME, headers=auth)).status_code == 200


async def test_changing_the_password_needs_the_current_one(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.post(
        CHANGE,
        json={"current_password": "wrong", "new_password": "a-brand-new-secret"},
        headers=headers_for(owner),
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "current_password"


async def test_a_short_new_password_is_refused(api: AsyncClient, owner: Staff) -> None:
    response = await api.post(
        CHANGE,
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=headers_for(owner),
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "new_password"


async def test_a_deleted_employee_loses_access_immediately(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """The row decides, not the token — that is why ``current_staff`` loads it."""
    headers = headers_for(owner)
    assert (await api.get(ME, headers=headers)).status_code == 200

    owner.soft_delete()
    await session.commit()

    assert (await api.get(ME, headers=headers)).status_code == 401


async def test_a_blocked_employee_loses_access_immediately(
    api: AsyncClient, session: AsyncSession, admin: Staff
) -> None:
    headers = headers_for(admin)
    assert (await api.get(ME, headers=headers)).status_code == 200

    admin.is_blocked = True
    await session.commit()

    assert (await api.get(ME, headers=headers)).status_code == 403
