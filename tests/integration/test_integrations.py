"""GTS credentials — API.md §29.

Three claims are being tested, and the first two are the reason the module
exists: that an installation can hold several GTS accounts with **exactly one**
of them in use, and that the password of any of them never comes back out. The
third is the ordinary one — ``admin`` may look, only ``owner`` may change.
"""

import asyncio
import base64
import os
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.errors import Conflict
from app.core import config as config_module
from app.core.crypto import encrypt
from app.modules.audit.models import AuditLog
from app.modules.integrations import service
from app.modules.integrations.models import DEFAULT_BASE_URL, GtsCredential
from app.modules.staff.models import Staff
from tests.integration.conftest import headers_for

CREDENTIALS = "/api/v1/admin/integrations/gts/credentials/"

#: A password that could not appear in a response by coincidence.
GTS_PASSWORD = "gts-password-9c41f7"


def _body(**overrides: Any) -> dict[str, Any]:
    return {
        "label": "Prod agent",
        "email": "agent@brand.uz",
        "password": GTS_PASSWORD,
    } | overrides


async def _create(api: AsyncClient, owner: Staff, **overrides: Any) -> dict[str, Any]:
    response = await api.post(
        CREDENTIALS, headers=headers_for(owner), json=_body(**overrides)
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()["data"]
    return created


async def _list(api: AsyncClient, staff: Staff) -> list[dict[str, Any]]:
    response = await api.get(CREDENTIALS, headers=headers_for(staff))
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()["data"]
    return rows


# --- the list, and which one is in use --------------------------------------------


async def test_a_fresh_installation_has_no_gts_account(
    api: AsyncClient, owner: Staff
) -> None:
    assert await _list(api, owner) == []


async def test_the_first_account_added_becomes_the_active_one(
    api: AsyncClient, owner: Staff
) -> None:
    created = await _create(api, owner)
    assert created["is_active"] is True
    assert created["base_url"] == DEFAULT_BASE_URL


async def test_a_second_account_is_stored_without_taking_over(
    api: AsyncClient, owner: Staff
) -> None:
    first = await _create(api, owner)
    second = await _create(api, owner, label="Zaxira", email="ops@brand.uz")

    assert second["is_active"] is False
    by_id = {row["id"]: row for row in await _list(api, owner)}
    assert by_id[first["id"]]["is_active"] is True
    assert len(by_id) == 2


async def test_activating_an_account_stands_the_previous_one_down(
    api: AsyncClient, owner: Staff
) -> None:
    first = await _create(api, owner)
    second = await _create(api, owner, label="Zaxira", email="ops@brand.uz")

    response = await api.post(
        f"{CREDENTIALS}{second['id']}/activate/", headers=headers_for(owner)
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["is_active"] is True

    by_id = {row["id"]: row for row in await _list(api, owner)}
    assert by_id[first["id"]]["is_active"] is False
    assert by_id[second["id"]]["is_active"] is True


async def test_an_account_that_was_never_tested_can_still_be_activated(
    api: AsyncClient, owner: Staff
) -> None:
    """The headline rule of API.md §29.

    ``test/`` can fail because GTS is down, or because two-factor confirmation
    is still on for the machine account (PROJECT.md D1). Neither is a reason to
    stop the owner switching their own account, so nothing gates ``activate/``.
    """
    await _create(api, owner)
    fresh = await _create(api, owner, label="Zaxira", email="ops@brand.uz")

    response = await api.post(
        f"{CREDENTIALS}{fresh['id']}/activate/", headers=headers_for(owner)
    )
    assert response.status_code == 200, response.text


async def test_activating_the_account_already_in_use_changes_nothing(
    api: AsyncClient, owner: Staff
) -> None:
    created = await _create(api, owner)
    response = await api.post(
        f"{CREDENTIALS}{created['id']}/activate/", headers=headers_for(owner)
    )
    assert response.status_code == 200
    assert response.json()["data"]["is_active"] is True


# --- the secret never comes back ---------------------------------------------------


async def test_the_password_is_never_returned(api: AsyncClient, owner: Staff) -> None:
    created = await _create(api, owner)
    assert GTS_PASSWORD not in str(created)

    listed = await api.get(CREDENTIALS, headers=headers_for(owner))
    one = await api.get(f"{CREDENTIALS}{created['id']}/", headers=headers_for(owner))

    # The raw text, not the parsed field: a password that leaked into an
    # unexpected key would slip past an assertion on ``data["password"]``.
    assert GTS_PASSWORD not in listed.text
    assert GTS_PASSWORD not in one.text
    assert set(one.json()["data"]["password"]) == {"•"}


async def test_the_password_does_not_reach_the_audit_journal(
    api: AsyncClient, owner: Staff, database: AsyncEngine
) -> None:
    await _create(api, owner)

    async with database.connect() as connection:
        rows = list(
            (
                await connection.execute(
                    select(AuditLog.changes).where(AuditLog.resource.like("integr%"))
                )
            ).all()
        )

    assert rows, "the mutation should have been journalled at all"
    assert GTS_PASSWORD not in str(rows)


async def test_the_active_account_comes_back_decrypted_for_gts(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    """What ``providers/gts`` will call. The one path that decrypts."""
    await _create(api, owner)

    active = await service.active_credential(session)
    assert active is not None
    assert active.password == GTS_PASSWORD
    assert active.email == "agent@brand.uz"
    # The secret must not be printable by accident — see the dataclass.
    assert GTS_PASSWORD not in repr(active)


async def test_there_is_no_active_account_before_one_is_added(
    session: AsyncSession,
) -> None:
    assert await service.active_credential(session) is None


# --- changing and removing ---------------------------------------------------------


async def test_a_new_password_replaces_the_stored_one(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    created = await _create(api, owner)

    response = await api.patch(
        f"{CREDENTIALS}{created['id']}/",
        headers=headers_for(owner),
        json={"password": "second-password-4a2b"},
    )
    assert response.status_code == 200, response.text

    active = await service.active_credential(session)
    assert active is not None
    assert active.password == "second-password-4a2b"


async def test_omitting_the_password_leaves_it_alone(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    created = await _create(api, owner)

    response = await api.patch(
        f"{CREDENTIALS}{created['id']}/",
        headers=headers_for(owner),
        json={"label": "Renamed"},
    )
    assert response.status_code == 200, response.text

    active = await service.active_credential(session)
    assert active is not None
    assert active.password == GTS_PASSWORD
    assert active.label == "Renamed"


async def test_removing_the_account_in_use_is_refused_while_another_exists(
    api: AsyncClient, owner: Staff
) -> None:
    active = await _create(api, owner)
    await _create(api, owner, label="Zaxira", email="ops@brand.uz")

    response = await api.delete(
        f"{CREDENTIALS}{active['id']}/", headers=headers_for(owner)
    )
    assert response.status_code == 409, response.text
    assert response.json()["errors"][0]["code"] == "conflict"


async def test_removing_the_only_account_is_allowed(
    api: AsyncClient, owner: Staff
) -> None:
    """Otherwise a client with a single account could never remove it."""
    created = await _create(api, owner)

    response = await api.delete(
        f"{CREDENTIALS}{created['id']}/", headers=headers_for(owner)
    )
    assert response.status_code == 204
    assert await _list(api, owner) == []


async def test_a_removed_account_leaves_no_row_behind(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    """No soft delete here — a stored password nobody uses is a liability."""
    created = await _create(api, owner)
    await api.delete(f"{CREDENTIALS}{created['id']}/", headers=headers_for(owner))

    remaining = (await session.scalars(select(GtsCredential))).all()
    assert list(remaining) == []


# --- validation --------------------------------------------------------------------


async def test_two_accounts_may_not_share_a_name(
    api: AsyncClient, owner: Staff
) -> None:
    await _create(api, owner)
    response = await api.post(
        CREDENTIALS, headers=headers_for(owner), json=_body(email="other@brand.uz")
    )
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == "label"


async def test_an_unknown_account_is_not_found(api: AsyncClient, owner: Staff) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    for response in (
        await api.get(f"{CREDENTIALS}{missing}/", headers=headers_for(owner)),
        await api.post(f"{CREDENTIALS}{missing}/activate/", headers=headers_for(owner)),
        await api.delete(f"{CREDENTIALS}{missing}/", headers=headers_for(owner)),
    ):
        assert response.status_code == 404, response.text


async def test_a_base_url_that_is_not_a_url_is_refused(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.post(
        CREDENTIALS, headers=headers_for(owner), json=_body(base_url="not a url")
    )
    assert response.status_code == 422, response.text


async def test_a_trailing_slash_is_dropped_from_the_base_url(
    api: AsyncClient, owner: Staff
) -> None:
    """``/v1/…`` is joined onto this; the slash would double up."""
    created = await _create(api, owner, base_url="https://gts.example.uz/")
    assert created["base_url"] == "https://gts.example.uz"


# --- roles (API.md §5) -------------------------------------------------------------


async def test_an_admin_may_read_the_accounts(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    created = await _create(api, owner)

    listed = await api.get(CREDENTIALS, headers=headers_for(admin))
    one = await api.get(f"{CREDENTIALS}{created['id']}/", headers=headers_for(admin))

    assert listed.status_code == 200
    assert one.status_code == 200
    assert GTS_PASSWORD not in listed.text


async def test_an_admin_may_not_change_them(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    created = await _create(api, owner)
    headers = headers_for(admin)

    for response in (
        await api.post(CREDENTIALS, headers=headers, json=_body(label="Sneaky")),
        await api.patch(
            f"{CREDENTIALS}{created['id']}/", headers=headers, json={"label": "Sneaky"}
        ),
        await api.delete(f"{CREDENTIALS}{created['id']}/", headers=headers),
        await api.post(f"{CREDENTIALS}{created['id']}/activate/", headers=headers),
    ):
        assert response.status_code == 403, response.text
        assert response.json()["errors"][0]["code"] == "forbidden"


# --- key rotation (PROJECT.md §13) -------------------------------------------------


async def test_a_row_sealed_with_the_old_key_is_still_readable(
    session: AsyncSession,
) -> None:
    """Rotation must not mean retyping every credential."""
    settings = config_module.settings
    original_keys = settings.encryption_keys
    original_version = settings.encryption_key_version

    ciphertext, key_version = encrypt(GTS_PASSWORD)
    session.add(
        GtsCredential(
            label="Old key",
            base_url=DEFAULT_BASE_URL,
            email="agent@brand.uz",
            password=ciphertext,
            key_version=key_version,
            is_active=True,
        )
    )
    await session.commit()

    try:
        # The rotation: a second key becomes active, the first stays in the
        # ring because rows still carry its version.
        second_key = base64.b64encode(os.urandom(32)).decode()
        settings.__dict__["encryption_keys"] = f"{original_keys},2:{second_key}"
        settings.__dict__["encryption_key_version"] = 2
        settings.__dict__.pop("encryption_key_ring", None)

        active = await service.active_credential(session)
        assert active is not None
        assert active.password == GTS_PASSWORD
    finally:
        settings.__dict__["encryption_keys"] = original_keys
        settings.__dict__["encryption_key_version"] = original_version
        settings.__dict__.pop("encryption_key_ring", None)


# --- the invariant --------------------------------------------------------------


async def test_the_database_itself_refuses_a_second_active_account(
    session: AsyncSession,
) -> None:
    """The guarantee is the partial unique index, not the service's care.

    Written against the table on purpose: every other test here goes through
    ``activate_credential``, which is exactly the code that would still look
    correct if the index were missing.
    """
    for index in range(2):
        session.add(
            GtsCredential(
                label=f"Both active {index}",
                base_url=DEFAULT_BASE_URL,
                email=f"both{index}@brand.uz",
                password=encrypt(GTS_PASSWORD)[0],
                key_version=encrypt(GTS_PASSWORD)[1],
                is_active=True,
            )
        )

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


# --- the invariant under concurrency -----------------------------------------------


@pytest.mark.usefixtures("database")
async def test_two_accounts_cannot_end_up_active_at_once(
    database: AsyncEngine,
) -> None:
    """Two owners activating two accounts in the same moment.

    This is the one test in the file that does not use the ``session`` and
    ``api`` fixtures. They give a test a single connection inside a single
    rolled-back transaction, so two ``gather``ed calls would share one session
    and never actually race. Real concurrency needs real connections — which
    also means this test commits, and has to clean up after itself.
    """
    factory = async_sessionmaker(bind=database, expire_on_commit=False)

    async with factory() as setup:
        rows = [
            GtsCredential(
                label=f"Race {index}",
                base_url=DEFAULT_BASE_URL,
                email=f"race{index}@brand.uz",
                password=encrypt(GTS_PASSWORD)[0],
                key_version=encrypt(GTS_PASSWORD)[1],
                is_active=False,
            )
            for index in range(2)
        ]
        setup.add_all(rows)
        await setup.commit()
        ids = [row.id for row in rows]

    async def activate(credential_id: Any) -> str:
        async with factory() as own_session:
            try:
                await service.activate_credential(own_session, credential_id)
            except Conflict:
                return "conflict"
            return "activated"

    try:
        results = await asyncio.gather(
            activate(ids[0]), activate(ids[1]), return_exceptions=True
        )
        assert all(not isinstance(result, BaseException) for result in results), results

        async with database.connect() as connection:
            active_count = await connection.scalar(
                text("SELECT count(*) FROM gts_credentials WHERE is_active")
            )
        # Whichever order they landed in, the database refuses to hold two.
        assert active_count == 1
    finally:
        async with factory() as cleanup:
            await cleanup.execute(delete(GtsCredential))
            await cleanup.commit()
