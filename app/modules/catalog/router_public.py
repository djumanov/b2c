"""``GET /public/catalog/*`` — API.md §26.

What a passenger form is filled in from: the document types a traveller can
carry and the countries they can hold. The object the client picks here is
what ``passengers.document_type`` and ``citizenship`` store verbatim
(STATUS.md §4.75). ``airports/`` feeds the flight search form's autocomplete
and, unlike the other two, is an **uncached** live proxy — no Redis entry and
no ``Cache-Control``, because a free-text ``q`` would make an unbounded cache
key (API.md §26).

**No authentication**, and none is declared: a visitor who has not signed in
still has to see the form. Only the rate limit on the parent public router
applies.

Items keep GTS's own shape, ``translations`` object and all (API.md §7).
"""

from typing import Annotated, Any, Final

from fastapi import Query, Response

from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.catalog import cache, service

router = enveloped_router(prefix="/catalog", tags=["catalog"])

#: The same day the Redis entry lives, stated once so the two cannot drift.
#: No ``ETag`` here, unlike ``site-config``: that document carries a five
#: minute ``max-age`` precisely because a client may edit it, so the browser
#: revalidates constantly and an ETag makes revalidation free. A day-long
#: ``max-age`` means the browser does not come back at all, and an ETag would
#: have nothing to do.
CACHE_CONTROL: Final = f"public, max-age={cache.CACHE_TTL_SECONDS}"

#: ISO 3166-1 alpha-2. Checked here so nothing user-shaped reaches a cache key.
CountryParam = Annotated[
    str | None,
    Query(
        min_length=2,
        max_length=2,
        pattern="^[A-Za-z]{2}$",
        description="ISO 3166-1 alpha-2, e.g. UZ",
    ),
]

#: Free text, so only length is checked: airport names carry spaces and
#: non-Latin scripts. The bounds keep a single keystroke and a pasted essay
#: from reaching GTS at all.
SearchParam = Annotated[
    str,
    Query(
        alias="q",
        min_length=2,
        max_length=64,
        description="Airport name, city or IATA code fragment, e.g. TAS",
    ),
]


@router.get("/document-types/", summary="Passenger document types")
async def get_document_types(
    response: Response,
    session: SessionDep,
    country: CountryParam = None,
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = CACHE_CONTROL
    return await service.document_types(
        session, country=country.upper() if country else None
    )


@router.get("/countries/", summary="Countries, with phone codes and flags")
async def get_countries(
    response: Response,
    session: SessionDep,
) -> list[dict[str, Any]]:
    response.headers["Cache-Control"] = CACHE_CONTROL
    return await service.countries(session)


@router.get("/airports/", summary="Airport search (autocomplete)")
async def get_airports(
    session: SessionDep,
    q: SearchParam,
) -> list[dict[str, Any]]:
    return await service.airports(session, search=q)


__all__ = ["CACHE_CONTROL", "router"]
