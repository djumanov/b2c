"""The accepting half of API.md §4.

The rejections are decided from the token alone and live in
``tests/contract/test_auth_surfaces.py``. Acceptance cannot be: it means the
subject of the token is a real, live employee, which needs a database.
"""

from httpx import AsyncClient

from app.core.roles import Role
from app.core.security import Audience
from app.modules.staff.models import Staff
from tests.conftest import bearer, issue_token
from tests.integration.conftest import headers_for

VERSION_URL = "/api/v1/admin/system/version/"


async def test_an_owner_token_is_accepted(api: AsyncClient, owner: Staff) -> None:
    response = await api.get(VERSION_URL, headers=headers_for(owner))

    assert response.status_code == 200
    assert response.json()["data"]["backend"]


async def test_an_admin_token_is_accepted(api: AsyncClient, admin: Staff) -> None:
    """`admin` reads system state too — API.md §5 gives it 👁 there."""
    response = await api.get(VERSION_URL, headers=headers_for(admin))

    assert response.status_code == 200


async def test_a_token_for_a_subject_that_does_not_exist_is_401(
    api: AsyncClient, owner: Staff
) -> None:
    """A perfectly signed token for nobody. The row is the authority."""
    headers = bearer(issue_token(Audience.ADMIN, role=Role.OWNER))

    response = await api.get(VERSION_URL, headers=headers)

    assert response.status_code == 401
