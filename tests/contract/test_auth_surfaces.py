"""The two token subjects never cross (API.md §4).

The 403-not-401 rule is the one worth a test of its own: answering 401 would
tell the panel to refresh a token that is perfectly valid, and it would loop.

Every case here is a **rejection**, and every rejection is decided from the
token alone — before ``current_staff`` looks the employee up. That is what
keeps this suite runnable with no services up. The mirror image, that a good
token is *accepted*, needs a real staff row and lives in
``tests/integration/test_auth_surfaces.py``.
"""

from httpx import AsyncClient

from app.core.roles import Role
from app.core.security import Audience, TokenType
from tests.conftest import issue_token

VERSION_URL = "/api/v1/admin/system/version/"


async def test_no_token_is_401(client: AsyncClient) -> None:
    response = await client.get(VERSION_URL)

    assert response.status_code == 401
    assert response.json()["errors"][0]["code"] == "unauthorized"


async def test_garbage_token_is_401(client: AsyncClient) -> None:
    response = await client.get(
        VERSION_URL, headers={"Authorization": "Bearer not-a-jwt"}
    )

    assert response.status_code == 401


async def test_customer_token_on_admin_surface_is_403(client: AsyncClient) -> None:
    """Valid token, wrong subject — forbidden, not unauthenticated."""
    headers = {"Authorization": f"Bearer {issue_token(Audience.PUBLIC)}"}

    response = await client.get(VERSION_URL, headers=headers)

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "forbidden"


async def test_refresh_token_is_not_accepted_as_access(client: AsyncClient) -> None:
    token = issue_token(Audience.ADMIN, role=Role.OWNER, token_type=TokenType.REFRESH)

    response = await client.get(
        VERSION_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401


async def test_a_role_that_no_longer_exists_is_403(client: AsyncClient) -> None:
    """A token minted before the two-role model is refused, not downgraded."""
    token = issue_token(Audience.ADMIN, role="content")

    response = await client.get(
        VERSION_URL, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "forbidden"


async def test_revoked_token_is_401(
    client: AsyncClient, fake_redis: object, staff_headers: dict[str, str]
) -> None:
    from app.core.security import decode_token, denylist_key, denylist_ttl
    from app.db.redis import get_redis

    claims = decode_token(staff_headers["Authorization"].removeprefix("Bearer "))
    await get_redis().set(denylist_key(claims.jti), "1", ex=denylist_ttl(claims))

    response = await client.get(VERSION_URL, headers=staff_headers)

    assert response.status_code == 401
    assert "revoked" in response.json()["errors"][0]["message"].lower()
