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
    # The dictionary the delete screen reads (API.md §34). The deletion flow it
    # feeds cannot be switched off, so its lookup cannot either — a precise
    # prefix, so the rest of §34 still forces its own decision when it lands.
    f"{API_PREFIX}/admin/customers/deletion-reasons/",
    # Nor are the lists a passenger form is filled in from. A client who
    # "switched off" document types would have a booking flow nobody can
    # complete, so there is no flag in FEATURE_DEFAULTS this could hang from —
    # and inventing one would offer a switch that must never be thrown.
    f"{API_PREFIX}/public/catalog/",
    # Static pages, the same reasoning: privacy-policy and terms must exist on
    # every installation (API.md §28), so there is no flag that could remove
    # them. Each fixed page is its own route on both surfaces — precise
    # prefixes, so FAQ under /content/ stays behind its own flag.
    f"{API_PREFIX}/admin/content/privacy-policy/",
    f"{API_PREFIX}/admin/content/terms/",
    f"{API_PREFIX}/admin/content/about/",
    f"{API_PREFIX}/public/content/privacy-policy/",
    f"{API_PREFIX}/public/content/terms/",
    f"{API_PREFIX}/public/content/about/",
    # The search flow — the docstring's ``/public/{product}/…`` case. Gated by
    # ``product_settings`` through the products router's own dependency, which
    # this sweep cannot see; the flag machinery it looks for does not apply.
    f"{API_PREFIX}/public/{{product}}/",
    # Seeing what you bought is not a section either. The switch that decides
    # whether an installation sells at all is ``product_settings`` one line
    # above; a second flag over the order history would only be able to hide
    # purchases a customer already made, which is never a thing to offer
    # (API.md §21).
    f"{API_PREFIX}/public/orders/",
    # Paying for what you bought, and the providers calling back about it.
    # Same reasoning as the order history one line up: the switch that decides
    # whether an installation sells at all is ``product_settings``, and a flag
    # over payment would only be able to strand orders that already exist. Which
    # methods are offered is a per-provider ``enabled`` row, not a section
    # (API.md §22, §29).
    f"{API_PREFIX}/public/payments/",
    f"{API_PREFIX}/public/transactions/",
    f"{API_PREFIX}/webhooks/",
    # Fun facts ride inside the flight search response; the off-switch is
    # publishing nothing — an empty table already removes the feature, so a
    # flag would be a second switch for the same wire (API.md §20, §30).
    f"{API_PREFIX}/admin/content/fun-facts/",
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
