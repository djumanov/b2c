"""``Audited`` — what a route says when the middleware would guess wrong.

The middleware reads the resource and the action off the route template, which
is right for everything shaped like the contract's CRUD and verb-sub-path
conventions (API.md §16). It is wrong where a verb appears with no identifier
in front of it — ``POST /admin/settings/cache/purge/`` reads as *creating* a
``settings.cache.purge``. Such a route corrects it in one line::

    @router.post(
        "/cache/purge/",
        dependencies=[Depends(Audited("settings.cache", "purge"))],
    )

This is an override, not a switch: leaving it off still produces an entry.
Nothing here can turn auditing off for a mutation, which is the point.
"""

from collections.abc import Callable

from app.modules.audit import context


def Audited(  # noqa: N802 - reads as a marker at the use site, like RateLimit
    resource: str, action: str | None = None
) -> Callable[[], None]:
    def dependency() -> None:
        context.describe(resource=resource, action=action)

    return dependency


__all__ = ["Audited"]
