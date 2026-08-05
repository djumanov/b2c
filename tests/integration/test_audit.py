"""The journal — API.md §13 and §39.

Two claims are being made. That **every** admin mutation leaves an entry
without the handler doing anything about it, and that authentication events —
including the failures, which never reach the middleware — leave one too.
"""

from typing import Any

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.staff.models import Staff
from tests.integration.conftest import PASSWORD, headers_for

AUDIT = "/api/v1/admin/system/audit/"
STAFF = "/api/v1/admin/staff/"
LOGIN = "/api/v1/admin/auth/login/"


async def _entries(api: AsyncClient, staff: Staff, **params: Any) -> list[Any]:
    response = await api.get(AUDIT, headers=headers_for(staff), params=params)
    assert response.status_code == 200, response.text
    rows: list[Any] = response.json()["data"]
    return rows


# --- mutations ---------------------------------------------------------------------


async def test_creating_an_employee_is_recorded(api: AsyncClient, owner: Staff) -> None:
    created = await api.post(
        STAFF,
        headers=headers_for(owner),
        json={
            "email": "aziz@example.uz",
            "name": "Aziz",
            "role": "admin",
            "password": PASSWORD,
        },
    )
    assert created.status_code == 201

    entry = (await _entries(api, owner, resource="staff"))[0]
    assert entry["action"] == "create"
    assert entry["resource"] == "staff"
    assert entry["method"] == "POST"
    assert entry["status_code"] == 201
    assert entry["actor"]["id"] == str(owner.id)
    assert entry["actor"]["email"] == owner.email
    assert entry["actor"]["role"] == "owner"
    # The thread back to the log lines of the same request (API.md §13).
    assert entry["request_id"]


async def test_a_verb_path_records_the_verb_not_the_method(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    blocked = await api.post(f"{STAFF}{admin.id}/block/", headers=headers_for(owner))
    assert blocked.status_code == 200

    entry = (await _entries(api, owner, resource="staff"))[0]
    assert entry["action"] == "block"
    assert entry["resource_id"] == str(admin.id)


async def test_deleting_records_the_target(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    await api.delete(f"{STAFF}{admin.id}/", headers=headers_for(owner))

    entry = (await _entries(api, owner, resource="staff"))[0]
    assert entry["action"] == "delete"
    assert entry["resource_id"] == str(admin.id)
    assert entry["status_code"] == 204


async def test_an_update_records_what_actually_changed(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    await api.patch(
        f"{STAFF}{admin.id}/",
        headers=headers_for(owner),
        # `name` is resent unchanged: it must not appear in the diff.
        json={"name": admin.name, "role": "owner"},
    )

    entry = (await _entries(api, owner, resource="staff"))[0]
    assert entry["action"] == "update"
    assert entry["changes"] == {"role": {"from": "admin", "to": "owner"}}


async def test_a_refused_request_records_nothing(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    """A 403 changed nothing, and a journal that lists attempts stops being an
    answer to "what was done"."""
    refused = await api.post(f"{STAFF}{owner.id}/block/", headers=headers_for(admin))
    assert refused.status_code == 403

    assert await _entries(api, owner, resource="staff") == []


async def test_a_validation_failure_records_nothing(
    api: AsyncClient, owner: Staff
) -> None:
    rejected = await api.post(
        STAFF,
        headers=headers_for(owner),
        json={"email": "not-an-email", "name": "X", "role": "admin", "password": "x"},
    )
    assert rejected.status_code == 422

    assert await _entries(api, owner, resource="staff") == []


async def test_reads_are_not_recorded(api: AsyncClient, owner: Staff) -> None:
    await api.get(STAFF, headers=headers_for(owner))
    await api.get(f"{STAFF}{owner.id}/", headers=headers_for(owner))

    assert await _entries(api, owner, resource="staff") == []


# --- authentication events (API.md §13) -----------------------------------------------


async def test_a_successful_login_is_recorded(api: AsyncClient, owner: Staff) -> None:
    await api.post(LOGIN, json={"login": owner.email, "password": PASSWORD})

    entry = (await _entries(api, owner, resource="auth"))[0]
    assert entry["action"] == "login"
    assert entry["actor"]["id"] == str(owner.id)


async def test_a_failed_login_is_recorded(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """The middleware never sees this one: 401, and nobody is signed in."""
    failed = await api.post(
        LOGIN, json={"login": owner.email, "password": "not-the-password"}
    )
    assert failed.status_code == 401

    entry = (await _entries(api, owner, resource="auth"))[0]
    assert entry["action"] == "login_failed"
    assert entry["actor"]["email"] == owner.email


async def test_a_failed_login_for_an_unknown_address_is_recorded(
    api: AsyncClient, owner: Staff
) -> None:
    await api.post(LOGIN, json={"login": "nobody@example.uz", "password": PASSWORD})

    entry = (await _entries(api, owner, action="login_failed"))[0]
    assert entry["actor"]["id"] is None
    assert entry["actor"]["email"] == "nobody@example.uz"


async def test_a_password_change_is_recorded(api: AsyncClient, owner: Staff) -> None:
    await api.post(
        "/api/v1/admin/auth/password/change/",
        headers=headers_for(owner),
        json={"current_password": PASSWORD, "new_password": "a-brand-new-secret"},
    )

    entry = (await _entries(api, owner, action="password_changed"))[0]
    assert entry["actor"]["id"] == str(owner.id)


async def test_no_password_ever_reaches_the_journal(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    await api.post(
        STAFF,
        headers=headers_for(owner),
        json={
            "email": "aziz@example.uz",
            "name": "Aziz",
            "role": "admin",
            "password": "a-very-secret-password",
        },
    )

    written = " ".join(
        str(row) for row in (await session.scalars(select(AuditLog.changes))).all()
    )
    assert "a-very-secret-password" not in written


# --- reading it (API.md §39) --------------------------------------------------------


async def test_both_roles_may_read_the_journal(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    """API.md §5 gives `admin` 👁 on *Tizim va audit*."""
    assert (await api.get(AUDIT, headers=headers_for(admin))).status_code == 200
    assert (await api.get(AUDIT, headers=headers_for(owner))).status_code == 200


async def test_the_journal_has_no_write_endpoint(
    api: AsyncClient, owner: Staff
) -> None:
    """Append-only is a property of the API surface, not just of the code."""
    for method in ("POST", "PATCH", "DELETE"):
        response = await api.request(method, AUDIT, headers=headers_for(owner), json={})

        assert response.status_code == 404, method
        assert response.json()["errors"][0]["code"] == "not_found"


async def test_filters(api: AsyncClient, owner: Staff, admin: Staff) -> None:
    await api.post(f"{STAFF}{admin.id}/block/", headers=headers_for(owner))
    await api.post(LOGIN, json={"login": owner.email, "password": PASSWORD})

    assert len(await _entries(api, owner)) == 2
    assert len(await _entries(api, owner, resource="auth")) == 1
    assert len(await _entries(api, owner, resource="staff")) == 1
    assert len(await _entries(api, owner, action="block")) == 1
    assert len(await _entries(api, owner, actor=str(owner.id))) == 2
    assert len(await _entries(api, owner, actor=str(admin.id))) == 0


async def test_newest_first_by_default(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    await api.post(f"{STAFF}{admin.id}/block/", headers=headers_for(owner))
    await api.delete(f"{STAFF}{admin.id}/", headers=headers_for(owner))

    actions = [entry["action"] for entry in await _entries(api, owner)]
    assert actions == ["delete", "block"]


async def test_entries_survive_the_employee_being_removed(
    api: AsyncClient, session: AsyncSession, owner: Staff, admin: Staff
) -> None:
    """ "Who did this" must still have an answer after they leave."""
    await api.post(f"{STAFF}{admin.id}/block/", headers=headers_for(owner))
    await api.delete(f"{STAFF}{admin.id}/", headers=headers_for(owner))

    total = await session.scalar(select(func.count()).select_from(AuditLog))
    assert total == 2
    entry = (await _entries(api, owner))[0]
    assert entry["actor"]["email"] == owner.email
