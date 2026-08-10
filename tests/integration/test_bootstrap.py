"""The owner and the relay come from the environment, once (PROJECT.md §14).

``docker/entrypoint.sh`` runs this on **every** container start, so "does
nothing the second time" is not a nicety — without it, every restart of a live
installation would try to plant an account whose password is sitting in an env
file, and would undo whatever the client had since typed into the panel.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.crypto import decrypt
from app.core.roles import Role
from app.core.security import verify_password
from app.modules.integrations import service as integrations_service
from app.modules.integrations.models import TlsMode
from app.modules.staff import service
from app.modules.staff.models import Staff


@pytest.fixture(autouse=True)
def first_owner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``settings`` is a process-wide singleton — put it back afterwards."""
    monkeypatch.setattr(settings, "first_owner_email", "Founder@Example.uz")
    monkeypatch.setattr(settings, "first_owner_password", "bootstrap-secret")
    monkeypatch.setattr(settings, "first_owner_name", "Founder")


async def _count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Staff)) or 0)


async def test_the_first_owner_is_created_from_the_environment(
    session: AsyncSession,
) -> None:
    owner = await service.create_first_owner(session)

    assert owner is not None
    assert owner.role is Role.OWNER
    assert owner.email == "founder@example.uz"
    assert verify_password("bootstrap-secret", owner.password_hash)


async def test_running_it_again_does_nothing(session: AsyncSession) -> None:
    await service.create_first_owner(session)

    assert await service.create_first_owner(session) is None
    assert await _count(session) == 1


async def test_it_does_nothing_when_staff_already_exist(
    session: AsyncSession, admin: Staff
) -> None:
    """An installation with any staff has been set up — even if no owner is
    left, planting one from the environment now would be a back door."""
    assert await service.create_first_owner(session) is None
    assert await _count(session) == 1


async def test_it_does_nothing_when_the_environment_is_empty(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "first_owner_password", "")

    assert await service.create_first_owner(session) is None
    assert await _count(session) == 0


# --- the relay ---------------------------------------------------------------------


@pytest.fixture
def smtp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.brand.uz")
    monkeypatch.setattr(settings, "smtp_port", 465)
    monkeypatch.setattr(settings, "smtp_tls", "ssl")
    monkeypatch.setattr(settings, "smtp_username", "no-reply@brand.uz")
    monkeypatch.setattr(settings, "smtp_password", "seeded-secret-9f")
    monkeypatch.setattr(settings, "smtp_from", "no-reply@brand.uz")
    monkeypatch.setattr(settings, "smtp_from_name", "Brand Travel")


async def test_the_relay_is_configured_from_the_environment(
    session: AsyncSession, smtp_env: None
) -> None:
    """A fresh database can send mail without anybody signing in first."""
    row = await integrations_service.bootstrap_smtp(session)

    assert row is not None
    assert row.enabled is True
    assert row.host == "smtp.brand.uz"
    assert row.port == 465
    assert row.tls is TlsMode.SSL
    assert row.from_name == "Brand Travel"
    # Stored the way the panel would have stored it, not in the clear.
    assert row.password != "seeded-secret-9f"
    assert decrypt(row.password or "", row.key_version or 0) == "seeded-secret-9f"


async def test_a_configured_relay_is_never_overwritten(
    session: AsyncSession, smtp_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The panel wins. A ``.env`` edited later must not undo what was typed."""
    await integrations_service.bootstrap_smtp(session)
    monkeypatch.setattr(settings, "smtp_host", "smtp.somewhere-else.uz")

    assert await integrations_service.bootstrap_smtp(session) is None

    row = await integrations_service.get_smtp(session)
    assert row.host == "smtp.brand.uz"


async def test_an_unset_environment_leaves_mail_switched_off(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "smtp_host", "")

    assert await integrations_service.bootstrap_smtp(session) is None
    assert (await integrations_service.get_smtp(session)).enabled is False


async def test_a_tls_mode_that_is_not_one_falls_back(
    session: AsyncSession, smtp_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mail with the wrong TLS mode fails loudly; mail never configured does not."""
    monkeypatch.setattr(settings, "smtp_tls", "sslv3-maybe")

    row = await integrations_service.bootstrap_smtp(session)

    assert row is not None
    assert row.tls is TlsMode.STARTTLS
