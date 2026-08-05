"""``/admin/staff/`` — API.md §38. The whole section is ``owner``.

This file carries the third acceptance criterion of phase 1 (PROJECT.md §15):
an ``admin`` token on an endpoint that requires ``owner`` gets **403**.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import Role
from app.modules.staff.models import Staff
from tests.integration.conftest import PASSWORD, headers_for, make_staff

STAFF = "/api/v1/admin/staff/"


# --- the two-role rule -------------------------------------------------------------


async def test_admin_is_refused_the_whole_team_section(
    api: AsyncClient, admin: Staff
) -> None:
    """API.md §5 gives `admin` no access at all to *Jamoa* — 403, not a
    filtered list."""
    for method, url in (
        ("GET", STAFF),
        ("POST", STAFF),
        ("GET", f"{STAFF}{admin.id}/"),
        ("PATCH", f"{STAFF}{admin.id}/"),
        ("DELETE", f"{STAFF}{admin.id}/"),
        ("POST", f"{STAFF}{admin.id}/block/"),
        ("POST", f"{STAFF}{admin.id}/reset-password/"),
    ):
        response = await api.request(method, url, headers=headers_for(admin), json={})

        assert response.status_code == 403, f"{method} {url}"
        assert response.json()["errors"][0]["code"] == "forbidden"


async def test_owner_reaches_the_team_section(api: AsyncClient, owner: Staff) -> None:
    response = await api.get(STAFF, headers=headers_for(owner))

    assert response.status_code == 200


# --- CRUD (API.md §8) --------------------------------------------------------------


async def test_create_read_update_delete(api: AsyncClient, owner: Staff) -> None:
    headers = headers_for(owner)

    created = await api.post(
        STAFF,
        headers=headers,
        json={
            "email": "Nodira@Example.uz",
            "name": "Nodira",
            "role": "admin",
            "password": PASSWORD,
        },
    )
    assert created.status_code == 201
    body = created.json()["data"]
    # Stored lower-cased, so one person cannot become two accounts.
    assert body["email"] == "nodira@example.uz"
    assert body["role"] == "admin"
    assert body["is_blocked"] is False
    staff_id = body["id"]

    fetched = await api.get(f"{STAFF}{staff_id}/", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["data"]["name"] == "Nodira"

    patched = await api.patch(
        f"{STAFF}{staff_id}/", headers=headers, json={"name": "Nodira K."}
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["name"] == "Nodira K."
    assert patched.json()["data"]["email"] == "nodira@example.uz"

    deleted = await api.delete(f"{STAFF}{staff_id}/", headers=headers)
    assert deleted.status_code == 204
    assert not deleted.content

    gone = await api.get(f"{STAFF}{staff_id}/", headers=headers)
    assert gone.status_code == 404


async def test_the_list_is_paginated(api: AsyncClient, owner: Staff) -> None:
    response = await api.get(STAFF, headers=headers_for(owner))

    payload = response.json()
    assert payload["meta"] == {
        "page": 1,
        "page_size": 20,
        "total": 1,
        "total_pages": 1,
    }
    assert [row["email"] for row in payload["data"]] == [owner.email]


async def test_search_and_ordering(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    await make_staff(
        session, email="aziz@example.uz", role=Role.ADMIN, name="Aziz Karimov"
    )
    headers = headers_for(owner)

    found = await api.get(f"{STAFF}?search=karimov", headers=headers)
    assert [row["email"] for row in found.json()["data"]] == ["aziz@example.uz"]

    ordered = await api.get(f"{STAFF}?ordering=email", headers=headers)
    assert [row["email"] for row in ordered.json()["data"]] == [
        "aziz@example.uz",
        "owner@example.uz",
    ]


async def test_ordering_by_an_unexposed_column_is_422(
    api: AsyncClient, owner: Staff
) -> None:
    """A whitelist, not a column name from the request."""
    response = await api.get(
        f"{STAFF}?ordering=password_hash", headers=headers_for(owner)
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "ordering"


async def test_a_duplicate_email_is_422_naming_the_field(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.post(
        STAFF,
        headers=headers_for(owner),
        json={
            "email": owner.email,
            "name": "Impostor",
            "role": "admin",
            "password": PASSWORD,
        },
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "email"


async def test_a_third_role_is_refused(api: AsyncClient, owner: Staff) -> None:
    """Roles are fixed in code; the panel cannot invent one (API.md §38)."""
    response = await api.post(
        STAFF,
        headers=headers_for(owner),
        json={
            "email": "super@example.uz",
            "name": "Super",
            "role": "superadmin",
            "password": PASSWORD,
        },
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "role"


async def test_a_deleted_email_can_be_used_again(
    api: AsyncClient, owner: Staff
) -> None:
    """What the partial unique index is for (ARCHITECTURE.md §10)."""
    headers = headers_for(owner)
    payload = {
        "email": "temp@example.uz",
        "name": "Temp",
        "role": "admin",
        "password": PASSWORD,
    }

    first = await api.post(STAFF, headers=headers, json=payload)
    await api.delete(f"{STAFF}{first.json()['data']['id']}/", headers=headers)

    again = await api.post(STAFF, headers=headers, json=payload)
    assert again.status_code == 201


# --- the installation always keeps an owner ------------------------------------------


async def test_the_last_owner_cannot_be_deleted(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    other = await make_staff(session, email="second@example.uz", role=Role.OWNER)

    response = await api.delete(f"{STAFF}{owner.id}/", headers=headers_for(other))
    assert response.status_code == 204

    last = await api.delete(f"{STAFF}{other.id}/", headers=headers_for(other))
    assert last.status_code == 409


async def test_the_last_owner_cannot_be_demoted(api: AsyncClient, owner: Staff) -> None:
    response = await api.patch(
        f"{STAFF}{owner.id}/", headers=headers_for(owner), json={"role": "admin"}
    )

    assert response.status_code == 409


async def test_you_cannot_delete_or_block_yourself(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    await make_staff(session, email="second@example.uz", role=Role.OWNER)
    headers = headers_for(owner)

    assert (await api.delete(f"{STAFF}{owner.id}/", headers=headers)).status_code == 409
    blocked = await api.post(f"{STAFF}{owner.id}/block/", headers=headers)
    assert blocked.status_code == 409


async def test_blocking_ends_that_employees_sessions(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    tokens = await api.post(
        "/api/v1/admin/auth/login/",
        json={"login": admin.email, "password": PASSWORD},
    )
    refresh_token = tokens.json()["data"]["refresh_token"]

    blocked = await api.post(f"{STAFF}{admin.id}/block/", headers=headers_for(owner))
    assert blocked.status_code == 200
    assert blocked.json()["data"]["is_blocked"] is True

    stale = await api.post(
        "/api/v1/admin/auth/refresh/", json={"refresh_token": refresh_token}
    )
    assert stale.status_code == 401
