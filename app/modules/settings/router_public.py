"""``GET /public/site-config/`` — API.md §17.

The first call the website and the app make, on every cold load, and the only
one they cannot start without. No authentication: it is what an anonymous
visitor needs before there is anything to authenticate.

Cached with an ``ETag`` and ``Cache-Control`` so a returning visitor gets a
`304` and no body at all. The cache behind it is dropped by any settings write,
which is what makes a colour change visible without a deploy (ARCHITECTURE.md
§5) — and the ETag is computed per language, because one cache entry serves
all three.
"""

from fastapi import Request, Response

from app.api.deps import LanguageDep
from app.api.envelope import enveloped_router, json_response, success_envelope
from app.db.session import SessionDep
from app.modules.settings import service
from app.modules.settings.schemas import SiteConfigOut

router = enveloped_router(prefix="/site-config", tags=["site-config"])

#: Five minutes in a shared cache. Long enough to absorb a burst of first
#: loads, short enough that a client who changes a colour and reloads sees it
#: even if their proxy ignores the ETag.
CACHE_CONTROL = "public, max-age=300"


@router.get("/", summary="Branding, languages, currencies, products, features")
async def get_site_config(
    request: Request,
    session: SessionDep,
    language: LanguageDep,
) -> Response:
    config = await service.site_config(session, requested=language.requested)
    etag = service.etag_for(config)

    if request.headers.get("if-none-match") == etag:
        # Nothing changed since the client last asked. 304 carries no body, so
        # it is returned directly rather than through the envelope.
        return Response(
            status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL}
        )

    return json_response(
        success_envelope(config.model_dump(mode="json")),
        200,
        headers={"ETag": etag, "Cache-Control": CACHE_CONTROL},
    )


__all__ = ["CACHE_CONTROL", "SiteConfigOut", "router"]
