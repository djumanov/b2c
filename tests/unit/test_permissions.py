"""The RBAC matrix, checked cell by cell against API.md §5.

The table below is transcribed from the document a second time, independently
of ``core.permissions``. Copying it is the point: a typo in the catalogue and a
typo in the test would have to be the *same* typo to pass.

Legend, as in the document: ``w`` = ✎ read+write · ``r`` = 👁 read · ``-`` = no access.
"""

import pytest

from app.core.permissions import (
    ALL_PERMISSIONS,
    ROLE_PERMISSIONS,
    Resource,
    Role,
    has_permission,
    permissions_of,
)

#                                   owner admin content operator finance support
DOCUMENTED_MATRIX: dict[str, str] = {
    "settings": "w  w  -  -  -  r",
    "integrations": "w  r  -  -  -  r",
    "content": "w  w  w  r  -  r",
    "orders": "w  w  -  w  r  r",
    "payments": "w  w  -  r  w  r",
    "promos": "w  w  r  r  w  r",
    "customers": "w  w  -  w  r  r",
    "reports": "w  w  -  r  w  r",
    "staff": "w  -  -  -  -  r",
    "system": "w  r  -  -  -  w",
}

ROLE_ORDER = [
    Role.OWNER,
    Role.ADMIN,
    Role.CONTENT,
    Role.OPERATOR,
    Role.FINANCE,
    Role.GTS_SUPPORT,
]


def _expected(cell: str) -> tuple[bool, bool]:
    """``"w"`` → (read, write)."""
    return {"w": (True, True), "r": (True, False), "-": (False, False)}[cell]


@pytest.mark.parametrize("resource_name,row", DOCUMENTED_MATRIX.items())
def test_matrix_row_matches_the_document(resource_name: str, row: str) -> None:
    cells = row.split()
    assert len(cells) == len(ROLE_ORDER), f"{resource_name}: wrong number of columns"

    for role, cell in zip(ROLE_ORDER, cells, strict=True):
        can_read, can_write = _expected(cell)
        assert has_permission(role, f"{resource_name}.read") is can_read, (
            f"{role.value} × {resource_name}.read"
        )
        assert has_permission(role, f"{resource_name}.write") is can_write, (
            f"{role.value} × {resource_name}.write"
        )


def test_matrix_covers_every_resource_group() -> None:
    assert set(DOCUMENTED_MATRIX) == {resource.value for resource in Resource}


def test_there_are_exactly_twenty_permissions() -> None:
    """Ten resource groups × read/write (API.md §5)."""
    assert len(ALL_PERMISSIONS) == 20
    assert len(set(ALL_PERMISSIONS)) == 20


def test_owner_holds_every_permission() -> None:
    assert ROLE_PERMISSIONS[Role.OWNER] == frozenset(ALL_PERMISSIONS)


def test_write_always_implies_read() -> None:
    for role, granted in ROLE_PERMISSIONS.items():
        for permission in granted:
            if permission.endswith(".write"):
                read = permission.replace(".write", ".read")
                assert read in granted, f"{role.value} may write but not read {read}"


def test_gts_support_writes_only_to_system() -> None:
    """Diagnostics is its one exception; everything else is read-only (§9)."""
    writes = {
        permission
        for permission in ROLE_PERMISSIONS[Role.GTS_SUPPORT]
        if permission.endswith(".write")
    }

    assert writes == {"system.write"}


def test_permissions_of_returns_a_sorted_list() -> None:
    """`GET /admin/auth/me/` returns a list, and the panel diffs it (§27)."""
    result = permissions_of(Role.FINANCE)

    assert result == sorted(result)
    assert "payments.write" in result
    assert "staff.read" not in result
