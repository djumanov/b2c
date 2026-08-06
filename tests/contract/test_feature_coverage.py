"""Every route is either core or behind a feature flag — no third option.

A client who does not want a blog switches it off and the blog is gone
(API.md §28). That promise is only as good as the weakest router: one section
mounted without its flag stays reachable, and nothing says so.

So the sweep is written the other way round from the obvious one. Rather than
"every gated route carries a gate" — which would pass vacuously today, since
no optional module is built yet — it asks **"is every route accounted for?"**.
A route is fine if it is on the core list or if it carries a gate; anything
else fails. Adding `/admin/content/blogs/` in phase 4 therefore forces a
decision that somebody has to write down, in one of two places.

The core list is deliberately in the test rather than in the application. It
is not configuration — it is the record of a judgement, and the place to argue
with it is a diff.
"""

from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from app.api.deps import RequireFeature
from app.main import app
from app.modules.settings.defaults import FEATURE_DEFAULTS

API_PREFIX = "/api/v1"

#: Routes that exist in every installation, whatever the client switched off.
#: Turning any of these off would not configure the product, it would break it.
#:
#: ``/public/{product}/…`` is core **here** and controlled elsewhere: which
#: verticals an installation may sell comes from its GTS agreement, through
#: ``settings/products/``, which the panel cannot write (API.md §28).
CORE_PREFIXES: tuple[str, ...] = (
    # the panel's own machinery
    f"{API_PREFIX}/admin/auth/",
    f"{API_PREFIX}/admin/settings/",
    f"{API_PREFIX}/admin/integrations/",
    f"{API_PREFIX}/admin/staff/",
    f"{API_PREFIX}/admin/system/",
    f"{API_PREFIX}/admin/uploads/",
    # the document every front end starts from
    f"{API_PREFIX}/public/site-config/",
    # signing in is not a section. A client who switched this off would have a
    # site nobody can buy from — PROJECT.md D4 makes an account mandatory to
    # order — so there is no flag it could hang from (PHASES.md §6, 4-faza).
    f"{API_PREFIX}/public/auth/",
    # Nor is having an account. A customer's own details and saved travellers
    # are not a section of the product that a client chooses to sell.
    f"{API_PREFIX}/public/profile/",
)


def _api_routes() -> list[tuple[str, RouteContext]]:
    return [
        (context.path, context)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.route, APIRoute) and context.path.startswith(API_PREFIX)
    ]


def _gate_flags(context: RouteContext) -> set[str]:
    """Which feature flags guard this route, if any.

    Reads ``context.dependant`` — the **effective** one — and not
    ``context.route.dependant``. ``include_router`` mounts by reference, so a
    dependency declared on the parent router is absent from the route's own
    dependant and present only in the composed one. A sweep that read the
    wrong attribute would report every route as ungated and be believed.
    """
    found: set[str] = set()

    def walk(dependencies: list) -> None:  # type: ignore[type-arg]
        for dependency in dependencies:
            if isinstance(dependency.call, RequireFeature):
                found.add(dependency.call.flag)
            walk(dependency.dependencies)

    walk(context.dependant.dependencies)
    return found


def test_there_are_routes_to_check() -> None:
    assert _api_routes(), "feature sweep would pass vacuously"


def test_every_route_is_core_or_gated() -> None:
    """The one that bites when a module is mounted without its flag."""
    unclassified = sorted(
        path
        for path, context in _api_routes()
        if not path.startswith(CORE_PREFIXES) and not _gate_flags(context)
    )

    assert unclassified == [], (
        "these are neither on the core list nor behind a feature flag. Either "
        "mount the router with Depends(RequireFeature(...)) or add the prefix "
        "to CORE_PREFIXES and say why."
    )


def test_every_gate_names_a_known_flag() -> None:
    """``RequireFeature`` refuses one at import; this says so out loud."""
    unknown = sorted(
        flag
        for _, context in _api_routes()
        for flag in _gate_flags(context)
        if flag not in FEATURE_DEFAULTS
    )

    assert unknown == []


def test_no_core_prefix_has_gone_stale() -> None:
    """An allowlist nobody prunes stops being a list of decisions.

    A prefix left behind after its routes moved would silently exempt whatever
    is mounted there next.
    """
    paths = [path for path, _ in _api_routes()]
    unused = [
        prefix
        for prefix in CORE_PREFIXES
        if not any(path.startswith(prefix) for path in paths)
    ]

    assert unused == [], "these core prefixes match no live route any more"
