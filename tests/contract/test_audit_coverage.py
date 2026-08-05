"""Nothing on the admin surface mutates without being recorded.

API.md §13 says *every* `POST`/`PATCH`/`DELETE` there lands in the journal.
That is a claim about the whole route table, so it is checked by sweeping the
whole route table — and it keeps holding as modules are added, which a test
written per endpoint would not.

The sweep is what makes the middleware trustworthy. It cannot be attached
per-router and therefore cannot be forgotten per-router, but it *can* be
side-stepped by mounting a surface it does not match; this is what notices.
"""

from fastapi.routing import APIRoute, iter_route_contexts

from app.main import app
from app.modules.audit.middleware import (
    MUTATING_METHODS,
    SELF_AUDITING_PREFIX,
    is_auditable,
)

ADMIN_PREFIX = "/api/v1/admin"

#: The one area that records its own events, because the middleware cannot:
#: its actor is not signed in and its most important event — a failed login —
#: is a 401 (API.md §13, ``modules/audit/middleware``).
EXPECTED_SELF_AUDITING = (SELF_AUDITING_PREFIX,)


def _admin_mutations() -> list[tuple[str, str]]:
    return [
        (method, context.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.route, APIRoute) and context.path.startswith(ADMIN_PREFIX)
        for method in sorted(context.route.methods or set())
        if method in MUTATING_METHODS
    ]


def test_there_are_mutations_to_check() -> None:
    assert _admin_mutations(), "audit sweep would pass vacuously"


def test_every_admin_mutation_is_audited() -> None:
    unrecorded = [
        f"{method} {path}"
        for method, path in _admin_mutations()
        if not is_auditable(method, path)
        and not path.startswith(EXPECTED_SELF_AUDITING)
    ]

    assert unrecorded == [], (
        "these change state on the admin surface and leave no journal entry"
    )


def test_the_only_exception_is_the_auth_area() -> None:
    """Adding a second exemption should require saying so here, in writing."""
    exempt = {
        path for method, path in _admin_mutations() if not is_auditable(method, path)
    }

    assert all(path.startswith(EXPECTED_SELF_AUDITING) for path in exempt), exempt
