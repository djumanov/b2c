"""The two roles and the guard built on them (API.md §5).

There is no matrix to transcribe any more. What is worth pinning down is the
shape itself: that the set of roles is exactly two, that ``owner`` contains
``admin`` and not the reverse, and that ``require_owner`` acts on that.

The guard is exercised here rather than through a route because no owner-only
route exists yet — the ``staff`` module is still empty. When one lands, the
route-level check belongs in the contract suite; this stays either way.
"""

import uuid

import pytest

from app.api.deps import Staff, require_owner
from app.api.errors import Forbidden
from app.core.roles import Role, satisfies


def test_there_are_exactly_two_roles() -> None:
    assert {role.value for role in Role} == {"owner", "admin"}


def test_owner_satisfies_every_requirement() -> None:
    assert satisfies(Role.OWNER, Role.OWNER)
    assert satisfies(Role.OWNER, Role.ADMIN)


def test_admin_satisfies_only_itself() -> None:
    """The containment goes one way — this is what makes the check one bit."""
    assert satisfies(Role.ADMIN, Role.ADMIN)
    assert not satisfies(Role.ADMIN, Role.OWNER)


def _staff(role: Role) -> Staff:
    return Staff(id=uuid.uuid4(), role=role)


async def test_require_owner_lets_the_owner_through() -> None:
    staff = _staff(Role.OWNER)

    assert await require_owner(staff) is staff


async def test_require_owner_rejects_admin() -> None:
    with pytest.raises(Forbidden) as excinfo:
        await require_owner(_staff(Role.ADMIN))

    assert "owner" in str(excinfo.value)
