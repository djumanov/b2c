"""The permission catalogue and the role → permission map.

Roles are **fixed in code**, not built in the panel (PROJECT.md §9): a staff
member is assigned one of six roles and nothing else. Ten resource groups
× ``.read``/``.write`` give the twenty permission strings the panel reasons
about.

``ACCESS_MATRIX`` below is a literal transcription of the table in API.md §5 —
kept in the same row and column order so the two can be diffed by eye, and
verified cell by cell in ``tests/unit/test_permissions.py``.

The panel builds its menu from ``GET /admin/auth/me/``'s ``permissions`` list,
never from the role name (API.md §27). That is why the role is an
implementation detail here and the permission strings are the public shape.
"""

from enum import StrEnum
from typing import Final


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    CONTENT = "content"
    OPERATOR = "operator"
    FINANCE = "finance"
    GTS_SUPPORT = "gts_support"


class Resource(StrEnum):
    """The ten resource groups of the RBAC matrix (API.md §5)."""

    SETTINGS = "settings"
    INTEGRATIONS = "integrations"
    CONTENT = "content"
    ORDERS = "orders"
    PAYMENTS = "payments"
    PROMOS = "promos"
    CUSTOMERS = "customers"
    REPORTS = "reports"
    STAFF = "staff"
    SYSTEM = "system"


class Access(StrEnum):
    """A matrix cell: ``—`` no access, ``👁`` read, ``✎`` read + write."""

    NONE = "none"
    READ = "read"
    WRITE = "write"


_N = Access.NONE
_R = Access.READ
_W = Access.WRITE

# API.md §5, row for row. Written out in full rather than zipped from compact
# tuples: this table is read far more often than it is edited, and it has to be
# checkable against the document by eye, without running anything.
ACCESS_MATRIX: Final[dict[Resource, dict[Role, Access]]] = {
    Resource.SETTINGS: {
        Role.OWNER: _W,
        Role.ADMIN: _W,
        Role.CONTENT: _N,
        Role.OPERATOR: _N,
        Role.FINANCE: _N,
        Role.GTS_SUPPORT: _R,
    },
    Resource.INTEGRATIONS: {
        Role.OWNER: _W,
        Role.ADMIN: _R,
        Role.CONTENT: _N,
        Role.OPERATOR: _N,
        Role.FINANCE: _N,
        Role.GTS_SUPPORT: _R,
    },
    Resource.CONTENT: {
        Role.OWNER: _W,
        Role.ADMIN: _W,
        Role.CONTENT: _W,
        Role.OPERATOR: _R,
        Role.FINANCE: _N,
        Role.GTS_SUPPORT: _R,
    },
    Resource.ORDERS: {
        Role.OWNER: _W,
        Role.ADMIN: _W,
        Role.CONTENT: _N,
        Role.OPERATOR: _W,
        Role.FINANCE: _R,
        Role.GTS_SUPPORT: _R,
    },
    Resource.PAYMENTS: {
        Role.OWNER: _W,
        Role.ADMIN: _W,
        Role.CONTENT: _N,
        Role.OPERATOR: _R,
        Role.FINANCE: _W,
        Role.GTS_SUPPORT: _R,
    },
    Resource.PROMOS: {
        Role.OWNER: _W,
        Role.ADMIN: _W,
        Role.CONTENT: _R,
        Role.OPERATOR: _R,
        Role.FINANCE: _W,
        Role.GTS_SUPPORT: _R,
    },
    Resource.CUSTOMERS: {
        Role.OWNER: _W,
        Role.ADMIN: _W,
        Role.CONTENT: _N,
        Role.OPERATOR: _W,
        Role.FINANCE: _R,
        Role.GTS_SUPPORT: _R,
    },
    Resource.REPORTS: {
        Role.OWNER: _W,
        Role.ADMIN: _W,
        Role.CONTENT: _N,
        Role.OPERATOR: _R,
        Role.FINANCE: _W,
        Role.GTS_SUPPORT: _R,
    },
    Resource.STAFF: {
        Role.OWNER: _W,
        Role.ADMIN: _N,
        Role.CONTENT: _N,
        Role.OPERATOR: _N,
        Role.FINANCE: _N,
        Role.GTS_SUPPORT: _R,
    },
    # gts_support writes here on purpose: system diagnostics is the one place
    # the support window may act, and every action is audited (PROJECT.md §9).
    Resource.SYSTEM: {
        Role.OWNER: _W,
        Role.ADMIN: _R,
        Role.CONTENT: _N,
        Role.OPERATOR: _N,
        Role.FINANCE: _N,
        Role.GTS_SUPPORT: _W,
    },
}


def permission(resource: Resource, action: str) -> str:
    """``settings`` + ``read`` → ``"settings.read"``."""
    return f"{resource.value}.{action}"


#: All twenty permission strings, in matrix order.
ALL_PERMISSIONS: Final[tuple[str, ...]] = tuple(
    permission(resource, action)
    for resource in Resource
    for action in ("read", "write")
)


def _permissions_for(role: Role) -> frozenset[str]:
    granted: set[str] = set()
    for resource, by_role in ACCESS_MATRIX.items():
        access = by_role[role]
        if access is Access.NONE:
            continue
        granted.add(permission(resource, "read"))
        if access is Access.WRITE:
            granted.add(permission(resource, "write"))
    return frozenset(granted)


#: The final, sorted permission list each role carries.
ROLE_PERMISSIONS: Final[dict[Role, frozenset[str]]] = {
    role: _permissions_for(role) for role in Role
}


def permissions_of(role: Role) -> list[str]:
    """Sorted permission list for ``GET /admin/auth/me/`` (API.md §27)."""
    return sorted(ROLE_PERMISSIONS[role])


def has_permission(role: Role, required: str) -> bool:
    return required in ROLE_PERMISSIONS[role]


__all__ = [
    "ACCESS_MATRIX",
    "ALL_PERMISSIONS",
    "ROLE_PERMISSIONS",
    "Access",
    "Resource",
    "Role",
    "has_permission",
    "permission",
    "permissions_of",
]
