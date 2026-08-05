"""The two staff roles and the order between them.

Roles are **fixed in code**, not built in the panel (PROJECT.md §9): a staff
member is either the ``owner`` of the installation or an ``admin`` who runs it
day to day. There is no third role and no role builder.

There is also no permission catalogue. ``owner ⊃ admin`` — an ``admin`` is
never granted anything the ``owner`` lacks — so authorisation collapses to two
cases: a route is open to any staff token, or it is owner-only. Twenty
permission strings would only be a second, driftable spelling of that same
one-bit decision (API.md §5).

Which resource groups the ``admin`` may merely read — integration secrets,
system and audit — is expressed by putting ``require_owner`` on the write
routes, not by a table this module would have to keep in step with the
document.
"""

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"


def satisfies(role: Role, required: Role) -> bool:
    """``owner`` meets every requirement; ``admin`` only its own."""
    return role is Role.OWNER or role is required


__all__ = ["Role", "satisfies"]
