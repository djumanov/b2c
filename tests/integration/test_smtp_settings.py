"""SMTP settings — API.md §29.

The claims: a fresh installation answers with defaults rather than a 404, the
password goes in and never comes back, and only ``owner`` may change any of it.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.integrations import service
from app.modules.integrations.models import SmtpSettings
from app.modules.staff.models import Staff
from app.providers.notifications import set_notifier
from app.providers.notifications.base import Channel
from app.providers.notifications.log import LogNotifier
from app.providers.notifications.smtp import SmtpNotifier
from tests.integration.conftest import headers_for

SMTP = "/api/v1/admin/integrations/notifications/"

#: Could not appear in a response by coincidence.
SMTP_PASSWORD = "smtp-password-4e17b9"

CONFIGURED: dict[str, Any] = {
    "host": "smtp.brand.uz",
    "port": 587,
    "username": "no-reply@brand.uz",
    "password": SMTP_PASSWORD,
    "from_address": "no-reply@brand.uz",
    "from_name": "Brand Travel",
}


@dataclass
class RecordingNotifier:
    """Stands in for the relay and keeps what it was asked to send."""

    channel: Channel = Channel.EMAIL
    sent: list[dict[str, Any]] = field(default_factory=list)

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.sent.append({"recipient": recipient, "subject": subject, "body": body})

    async def verify(self) -> bool:
        return True


@dataclass
class FailingNotifier:
    """A relay that will not have us."""

    channel: Channel = Channel.EMAIL

    async def send(
        self,
        *,
        recipient: str,
        subject: str | None,
        body: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        raise RuntimeError("authentication failed")

    async def verify(self) -> bool:
        return False


@pytest.fixture
def notifier() -> Iterator[RecordingNotifier]:
    recorder = RecordingNotifier()
    set_notifier(recorder)
    yield recorder
    set_notifier(None)


async def _get(api: AsyncClient, staff: Staff) -> dict[str, Any]:
    response = await api.get(SMTP, headers=headers_for(staff))
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


async def _patch(api: AsyncClient, staff: Staff, **body: Any) -> dict[str, Any]:
    response = await api.patch(SMTP, headers=headers_for(staff), json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


# --- a fresh installation ----------------------------------------------------------


async def test_a_fresh_installation_answers_with_defaults(
    api: AsyncClient, owner: Staff
) -> None:
    settings = await _get(api, owner)

    assert settings["enabled"] is False
    assert settings["host"] is None
    assert settings["port"] == 587
    assert settings["tls"] == "starttls"
    # Distinct from a mask: nothing is stored, and the panel says so.
    assert settings["password"] is None
    assert settings["last_tested_at"] is None


async def test_reading_twice_does_not_make_a_second_row(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    """The row is created on first read, and the singleton must hold."""
    await _get(api, owner)
    await _get(api, owner)

    rows = await session.scalar(select(func.count()).select_from(SmtpSettings))
    assert rows == 1


# --- changing it -------------------------------------------------------------------


async def test_the_settings_can_be_filled_in(api: AsyncClient, owner: Staff) -> None:
    settings = await _patch(api, owner, **CONFIGURED)

    assert settings["host"] == "smtp.brand.uz"
    assert settings["username"] == "no-reply@brand.uz"
    assert settings["from_name"] == "Brand Travel"
    assert settings["enabled"] is False, "filling it in does not switch it on"


async def test_a_patch_leaves_the_fields_it_omits_alone(
    api: AsyncClient, owner: Staff
) -> None:
    await _patch(api, owner, **CONFIGURED)
    settings = await _patch(api, owner, port=465, tls="ssl")

    assert settings["port"] == 465
    assert settings["tls"] == "ssl"
    assert settings["host"] == "smtp.brand.uz"
    assert settings["from_address"] == "no-reply@brand.uz"


async def test_email_can_be_switched_on_once_it_is_usable(
    api: AsyncClient, owner: Staff
) -> None:
    await _patch(api, owner, **CONFIGURED)
    assert (await _patch(api, owner, enabled=True))["enabled"] is True


async def test_email_cannot_be_switched_on_while_it_is_empty(
    api: AsyncClient, owner: Staff
) -> None:
    """Otherwise the panel says mail is on and nothing is ever delivered."""
    response = await api.patch(SMTP, headers=headers_for(owner), json={"enabled": True})

    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == "enabled"


async def test_an_empty_username_clears_it(api: AsyncClient, owner: Staff) -> None:
    """A relay that wants no authentication is a real configuration."""
    await _patch(api, owner, **CONFIGURED)
    assert (await _patch(api, owner, username=""))["username"] is None


async def test_a_port_outside_the_range_is_refused(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.patch(SMTP, headers=headers_for(owner), json={"port": 70000})
    assert response.status_code == 422, response.text


async def test_an_unknown_tls_mode_is_refused(api: AsyncClient, owner: Staff) -> None:
    response = await api.patch(SMTP, headers=headers_for(owner), json={"tls": "maybe"})
    assert response.status_code == 422, response.text


# --- the secret ---------------------------------------------------------------------


async def test_the_password_is_never_returned(api: AsyncClient, owner: Staff) -> None:
    stored = await api.patch(SMTP, headers=headers_for(owner), json=CONFIGURED)
    fetched = await api.get(SMTP, headers=headers_for(owner))

    # The raw text, not the parsed field: a secret that leaked into an
    # unexpected key would slip past an assertion on ``data["password"]``.
    assert SMTP_PASSWORD not in stored.text
    assert SMTP_PASSWORD not in fetched.text
    assert set(fetched.json()["data"]["password"]) == {"•"}


async def test_the_password_is_stored_encrypted(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    await _patch(api, owner, **CONFIGURED)

    row = await session.scalar(select(SmtpSettings).limit(1))
    assert row is not None
    assert row.password is not None
    assert row.password != SMTP_PASSWORD
    assert row.key_version == 1


async def test_the_password_does_not_reach_the_audit_journal(
    api: AsyncClient, owner: Staff, database: AsyncEngine
) -> None:
    await _patch(api, owner, **CONFIGURED)

    async with database.connect() as connection:
        rows = list(
            (
                await connection.execute(
                    select(AuditLog.changes).where(AuditLog.resource.like("integr%"))
                )
            ).all()
        )

    assert rows, "the mutation should have been journalled at all"
    assert SMTP_PASSWORD not in str(rows)


# --- roles (API.md §5) --------------------------------------------------------------


async def test_an_admin_may_read_the_settings(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    await _patch(api, owner, **CONFIGURED)

    response = await api.get(SMTP, headers=headers_for(admin))
    assert response.status_code == 200
    assert SMTP_PASSWORD not in response.text


async def test_an_admin_may_not_change_them(api: AsyncClient, admin: Staff) -> None:
    response = await api.patch(
        SMTP, headers=headers_for(admin), json={"host": "evil.example"}
    )
    assert response.status_code == 403, response.text
    assert response.json()["errors"][0]["code"] == "forbidden"


async def test_an_admin_may_not_send_a_test_message(
    api: AsyncClient, admin: Staff
) -> None:
    response = await api.post(f"{SMTP}test/", headers=headers_for(admin), json={})
    assert response.status_code == 403, response.text


# --- which notifier the application sends through -----------------------------------


async def test_an_unconfigured_installation_still_only_logs(
    session: AsyncSession,
) -> None:
    notifier = await service.notifier(session)
    assert isinstance(notifier, LogNotifier)


async def test_settings_that_are_filled_in_but_off_still_only_log(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    await _patch(api, owner, **CONFIGURED)
    assert isinstance(await service.notifier(session), LogNotifier)


async def test_email_switched_on_sends_over_smtp(
    session: AsyncSession, api: AsyncClient, owner: Staff
) -> None:
    await _patch(api, owner, **CONFIGURED)
    await _patch(api, owner, enabled=True)

    notifier = await service.notifier(session)
    assert isinstance(notifier, SmtpNotifier)
    # The password is decrypted on the way to the relay, and only there.
    assert notifier._config.password == SMTP_PASSWORD
    assert notifier._config.host == "smtp.brand.uz"
    # …and the config must not print it when something goes wrong.
    assert SMTP_PASSWORD not in repr(notifier._config)


async def test_a_pinned_notifier_wins(
    session: AsyncSession, api: AsyncClient, owner: Staff, notifier: RecordingNotifier
) -> None:
    """The seam tests hang from — it has to beat a real configuration."""
    await _patch(api, owner, **CONFIGURED)
    await _patch(api, owner, enabled=True)

    assert await service.notifier(session) is notifier


# --- the test message (API.md §29) --------------------------------------------------


async def test_sending_a_test_message_needs_email_switched_on(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.post(f"{SMTP}test/", headers=headers_for(owner), json={})

    assert response.status_code == 409, response.text
    assert response.json()["errors"][0]["code"] == "conflict"


async def test_a_test_message_goes_to_whoever_pressed_the_button(
    api: AsyncClient, owner: Staff, notifier: RecordingNotifier
) -> None:
    await _patch(api, owner, **CONFIGURED)
    await _patch(api, owner, enabled=True)

    response = await api.post(f"{SMTP}test/", headers=headers_for(owner), json={})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["ok"] is True
    assert notifier.sent[-1]["recipient"] == owner.email

    # …and the outcome is remembered, so a plain GET can show it without
    # sending anybody a second message.
    settings = await _get(api, owner)
    assert settings["last_test_ok"] is True
    assert settings["last_tested_at"] is not None


async def test_a_test_message_can_name_another_address(
    api: AsyncClient, owner: Staff, notifier: RecordingNotifier
) -> None:
    await _patch(api, owner, **CONFIGURED)
    await _patch(api, owner, enabled=True)

    response = await api.post(
        f"{SMTP}test/", headers=headers_for(owner), json={"to": "boss@brand.uz"}
    )

    assert response.status_code == 200, response.text
    assert notifier.sent[-1]["recipient"] == "boss@brand.uz"


async def test_a_relay_that_refuses_is_still_a_successful_call(
    api: AsyncClient, owner: Staff
) -> None:
    """A `502` here would tell the owner that something broke, not what."""
    set_notifier(FailingNotifier())
    try:
        await _patch(api, owner, **CONFIGURED)
        await _patch(api, owner, enabled=True)
        response = await api.post(f"{SMTP}test/", headers=headers_for(owner), json={})
    finally:
        set_notifier(None)

    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["ok"] is False
    assert "authentication failed" in body["detail"]

    settings = await _get(api, owner)
    assert settings["last_test_ok"] is False
    assert "authentication failed" in settings["last_test_error"]
