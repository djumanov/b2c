"""How a route template becomes a resource and an action.

This is the part of the audit layer that guesses, so it is the part worth
pinning down. Everything else either follows from the response or is stated
outright by the service.
"""

import pytest

from app.modules.audit.middleware import describe_route, is_auditable


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/admin/staff/", True),
        ("PATCH", "/api/v1/admin/staff/{id}/", True),
        ("DELETE", "/api/v1/admin/staff/{id}/", True),
        # Reads change nothing.
        ("GET", "/api/v1/admin/staff/", False),
        # The public surface has no journal — it is a panel feature (API.md §13).
        ("POST", "/api/v1/public/auth/login/", False),
        # Webhooks are a provider's protocol, not an operator's action.
        ("POST", "/api/v1/webhooks/payments/payme/", False),
        # Records its own events, failures included.
        ("POST", "/api/v1/admin/auth/login/", False),
        ("POST", "/api/v1/admin/auth/password/change/", False),
    ],
)
def test_what_is_auditable(method: str, path: str, expected: bool) -> None:
    assert is_auditable(method, path) is expected


@pytest.mark.parametrize(
    ("template", "method", "expected"),
    [
        ("/api/v1/admin/staff/", "POST", ("staff", "create")),
        ("/api/v1/admin/staff/{id}/", "PATCH", ("staff", "update")),
        ("/api/v1/admin/staff/{id}/", "DELETE", ("staff", "delete")),
        ("/api/v1/admin/staff/{id}/block/", "POST", ("staff", "block")),
        (
            "/api/v1/admin/staff/{id}/reset-password/",
            "POST",
            ("staff", "reset-password"),
        ),
        # Sub-sections keep their prefix, so `?resource=settings` finds them all.
        ("/api/v1/admin/settings/branding/", "PATCH", ("settings.branding", "update")),
        (
            "/api/v1/admin/content/blogs/{id}/publish/",
            "POST",
            ("content.blogs", "publish"),
        ),
    ],
)
def test_describe_route(template: str, method: str, expected: tuple[str, str]) -> None:
    assert describe_route(template, method) == expected


def test_a_verb_with_no_identifier_before_it_is_read_as_a_resource() -> None:
    """The documented blind spot: such routes carry ``Depends(Audited(...))``.

    Pinned so the day the heuristic is improved, this test is what says the
    override is no longer needed.
    """
    assert describe_route("/api/v1/admin/settings/cache/purge/", "POST") == (
        "settings.cache.purge",
        "create",
    )
