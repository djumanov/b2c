"""The first owner comes from the environment, once (PROJECT.md §14).

``docker/entrypoint.sh`` runs this on **every** container start, so "does
nothing the second time" is not a nicety — without it, every restart of a live
installation would try to plant an account whose password is sitting in an env
file.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.roles import Role
from app.core.security import verify_password
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
