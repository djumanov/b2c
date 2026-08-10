"""Forgotten passwords — API.md §27, and ``/admin/staff/{id}/reset-password/``.

The delivery adapter is swapped for a recorder. What is tested is the flow: a
link is issued once, spends itself on use, and never tells an anonymous caller
whether an address belongs to anybody.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import Role
from app.modules.staff.models import Staff
from tests.integration.conftest import (
    PASSWORD,
    RecordingNotifier,
    headers_for,
    make_staff,
)

REQUEST = "/api/v1/admin/auth/password/reset/request/"
CONFIRM = "/api/v1/admin/auth/password/reset/confirm/"
LOGIN = "/api/v1/admin/auth/login/"


async def test_the_link_sets_a_new_password_and_is_spent(
    api: AsyncClient, owner: Staff, notifier: RecordingNotifier
) -> None:
    requested = await api.post(REQUEST, json={"email": owner.email})
    assert requested.status_code == 204
    assert notifier.sent[-1]["recipient"] == owner.email

    token = notifier.last_token
    confirmed = await api.post(
        CONFIRM, json={"token": token, "new_password": "a-brand-new-secret"}
    )
    assert confirmed.status_code == 204

    signed_in = await api.post(
        LOGIN, json={"login": owner.email, "password": "a-brand-new-secret"}
    )
    assert signed_in.status_code == 200

    # Single use: the same link cannot set the password a second time.
    replay = await api.post(
        CONFIRM, json={"token": token, "new_password": "yet-another-secret"}
    )
    assert replay.status_code == 422


async def test_an_unknown_address_is_answered_the_same_way(
    api: AsyncClient, notifier: RecordingNotifier
) -> None:
    """204 either way — the endpoint is unauthenticated and must not confirm
    that an employee of this client exists."""
    response = await api.post(REQUEST, json={"email": "nobody@example.uz"})

    assert response.status_code == 204
    assert notifier.sent == []


async def test_a_made_up_token_is_422(api: AsyncClient, owner: Staff) -> None:
    response = await api.post(
        CONFIRM, json={"token": "not-a-real-token", "new_password": PASSWORD}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "token"


async def test_the_owner_can_send_an_employee_a_link(
    api: AsyncClient, session: AsyncSession, owner: Staff, notifier: RecordingNotifier
) -> None:
    """The owner never picks the password — the employee does (API.md §38)."""
    employee = await make_staff(session, email="aziz@example.uz", role=Role.ADMIN)

    response = await api.post(
        f"/api/v1/admin/staff/{employee.id}/reset-password/",
        headers=headers_for(owner),
    )

    assert response.status_code == 204
    assert notifier.sent[-1]["recipient"] == employee.email


async def test_a_relay_that_refuses_keeps_the_request_silent(
    api: AsyncClient, owner: Staff, notifier: RecordingNotifier
) -> None:
    """Still 204: the endpoint is unauthenticated, so a `502` for a real address
    and a `204` for an invented one would be the enumeration this hides."""
    notifier.fails_with = RuntimeError("relay refused the message")

    response = await api.post(REQUEST, json={"email": owner.email})

    assert response.status_code == 204


async def test_a_refused_link_leaves_no_working_token(
    api: AsyncClient, owner: Staff, notifier: RecordingNotifier
) -> None:
    """The token is written down only once the message has gone, so a refused
    one is not a live reset token sitting in Redis with nobody holding it."""
    notifier.fails_with = RuntimeError("relay refused the message")
    assert (await api.post(REQUEST, json={"email": owner.email})).status_code == 204

    notifier.fails_with = None
    assert (await api.post(REQUEST, json={"email": owner.email})).status_code == 204

    # The delivered link is the only one that works.
    confirmed = await api.post(
        CONFIRM, json={"token": notifier.last_token, "new_password": PASSWORD}
    )
    assert confirmed.status_code == 204, confirmed.text


async def test_the_owner_is_told_when_the_relay_refuses(
    api: AsyncClient, session: AsyncSession, owner: Staff, notifier: RecordingNotifier
) -> None:
    """The one caller that hears about it: somebody pressed the button and is
    waiting to know whether the employee can expect a mail (API.md §38)."""
    employee = await make_staff(session, email="aziz@example.uz", role=Role.ADMIN)
    notifier.fails_with = RuntimeError("relay refused the message")

    response = await api.post(
        f"/api/v1/admin/staff/{employee.id}/reset-password/",
        headers=headers_for(owner),
    )

    assert response.status_code == 502, response.text
    assert response.json()["errors"][0]["code"] == "upstream_error"


async def test_a_blocked_employee_gets_no_link(
    api: AsyncClient, session: AsyncSession, notifier: RecordingNotifier
) -> None:
    blocked = await make_staff(
        session, email="blocked@example.uz", role=Role.ADMIN, is_blocked=True
    )

    response = await api.post(REQUEST, json={"email": blocked.email})

    assert response.status_code == 204
    assert notifier.sent == []
