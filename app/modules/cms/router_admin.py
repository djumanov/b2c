"""``/admin/content/faq/`` and the fixed static pages (API.md §30).

FAQ sits behind the ``faq`` feature flag: switched off it answers 404 on both
surfaces (API.md §28). Pages take no flag — privacy-policy and terms must
exist on every installation, so there is no switch that could remove them.
``current_staff`` runs first, so an anonymous caller is told 401 and a
customer token 403 before a flag can turn either into a 404.
"""

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Query, Response

from app.api.deps import PaginationDep, RequireFeature, current_staff
from app.api.envelope import Page, enveloped_router
from app.api.listing import ListQueryDep
from app.db.session import SessionDep
from app.modules.audit.deps import Audited
from app.modules.cms import service
from app.modules.cms.models import ContentStatus
from app.modules.cms.schemas import (
    FaqAdminOut,
    FaqIn,
    FaqUpdateIn,
    FixedPageIn,
    FunFactAdminOut,
    FunFactIn,
    FunFactUpdateIn,
    PageAdminOut,
    ReorderItemIn,
)

faq_router = enveloped_router(
    prefix="/content/faq",
    tags=["cms"],
    dependencies=[Depends(current_staff), Depends(RequireFeature("faq"))],
)

StatusParam = Annotated[Literal["draft", "published"] | None, Query(alias="status")]
CategoryParam = Annotated[str | None, Query(max_length=64)]


@faq_router.get("/", summary="List FAQ entries")
async def list_faq(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    status: StatusParam = None,
    category: CategoryParam = None,
) -> Page[FaqAdminOut]:
    return await service.list_faq_admin(
        session, pagination, query, status=status, category=category
    )


@faq_router.post("/", status_code=201, summary="Add an FAQ entry (as a draft)")
async def create_faq(data: FaqIn, session: SessionDep) -> FaqAdminOut:
    return await service.create_faq(session, data)


@faq_router.post(
    "/reorder/",
    status_code=204,
    dependencies=[Depends(Audited("content.faq", "reorder"))],
    summary="Apply a new order",
)
async def reorder_faq(data: list[ReorderItemIn], session: SessionDep) -> Response:
    await service.reorder_faq(session, data)
    return Response(status_code=204)


@faq_router.get("/{id}/", summary="One FAQ entry")
async def get_faq(id: uuid.UUID, session: SessionDep) -> FaqAdminOut:
    return await service.get_faq(session, id)


@faq_router.patch("/{id}/", summary="Change question, answer or category")
async def update_faq(
    id: uuid.UUID, data: FaqUpdateIn, session: SessionDep
) -> FaqAdminOut:
    return await service.update_faq(session, id, data)


@faq_router.delete("/{id}/", status_code=204, summary="Remove an FAQ entry")
async def delete_faq(id: uuid.UUID, session: SessionDep) -> Response:
    await service.delete_faq(session, id)
    return Response(status_code=204)


@faq_router.post("/{id}/publish/", summary="Publish an FAQ entry")
async def publish_faq(id: uuid.UUID, session: SessionDep) -> FaqAdminOut:
    return await service.set_faq_status(session, id, ContentStatus.PUBLISHED)


@faq_router.post("/{id}/unpublish/", summary="Take an FAQ entry off the site")
async def unpublish_faq(id: uuid.UUID, session: SessionDep) -> FaqAdminOut:
    return await service.set_faq_status(session, id, ContentStatus.DRAFT)


# --- fun facts -------------------------------------------------------------------

# No feature flag: the off-switch is publishing nothing — the flight search
# response's ``fun_fact`` is then ``null`` (API.md §20, §30). No ``reorder/``
# either: selection is random, an order would answer nothing.
fun_facts_router = enveloped_router(
    prefix="/content/fun-facts",
    tags=["cms"],
    dependencies=[Depends(current_staff)],
)


@fun_facts_router.get("/", summary="List fun facts")
async def list_fun_facts(
    session: SessionDep,
    pagination: PaginationDep,
    query: ListQueryDep,
    status: StatusParam = None,
) -> Page[FunFactAdminOut]:
    return await service.list_fun_facts_admin(session, pagination, query, status=status)


@fun_facts_router.post("/", status_code=201, summary="Add a fun fact (as a draft)")
async def create_fun_fact(data: FunFactIn, session: SessionDep) -> FunFactAdminOut:
    return await service.create_fun_fact(session, data)


@fun_facts_router.get("/{id}/", summary="One fun fact")
async def get_fun_fact(id: uuid.UUID, session: SessionDep) -> FunFactAdminOut:
    return await service.get_fun_fact(session, id)


@fun_facts_router.patch("/{id}/", summary="Change the fact's text")
async def update_fun_fact(
    id: uuid.UUID, data: FunFactUpdateIn, session: SessionDep
) -> FunFactAdminOut:
    return await service.update_fun_fact(session, id, data)


@fun_facts_router.delete("/{id}/", status_code=204, summary="Remove a fun fact")
async def delete_fun_fact(id: uuid.UUID, session: SessionDep) -> Response:
    await service.delete_fun_fact(session, id)
    return Response(status_code=204)


@fun_facts_router.post("/{id}/publish/", summary="Publish a fun fact")
async def publish_fun_fact(id: uuid.UUID, session: SessionDep) -> FunFactAdminOut:
    return await service.set_fun_fact_status(session, id, ContentStatus.PUBLISHED)


@fun_facts_router.post("/{id}/unpublish/", summary="Take a fun fact off the site")
async def unpublish_fun_fact(id: uuid.UUID, session: SessionDep) -> FunFactAdminOut:
    return await service.set_fun_fact_status(session, id, ContentStatus.DRAFT)


# --- static pages ----------------------------------------------------------------

pages_router = enveloped_router(
    prefix="/content",
    tags=["cms"],
    dependencies=[Depends(current_staff)],
)

#: What Swagger calls each fixed page (API.md §30, "Sahifa tanasi").
_PAGE_LABELS = {
    "privacy-policy": "the privacy policy",
    "terms": "the terms of use",
    "about": "the about page",
}


def _register_page(slug: str, label: str) -> None:
    """Four routes per page, closed over its slug.

    A ``{slug}`` path parameter would serve the same rows in less code, but
    the frontend and mobile devs build from Swagger — a parameter names none
    of the pages, separate operations name all of them.
    """

    async def get_page(session: SessionDep) -> PageAdminOut:
        return await service.get_fixed_page_admin(session, slug)

    async def put_page(data: FixedPageIn, session: SessionDep) -> PageAdminOut:
        return await service.upsert_fixed_page(session, slug, data)

    async def publish_page(session: SessionDep) -> PageAdminOut:
        return await service.set_fixed_page_status(
            session, slug, ContentStatus.PUBLISHED
        )

    async def unpublish_page(session: SessionDep) -> PageAdminOut:
        return await service.set_fixed_page_status(session, slug, ContentStatus.DRAFT)

    name = slug.replace("-", "_")
    pages_router.get(
        f"/{slug}/", name=f"get_{name}", summary=f"Current state of {label}"
    )(get_page)
    pages_router.put(
        f"/{slug}/",
        name=f"put_{name}",
        summary=f"Write {label} — first PUT creates the draft, later ones "
        "merge languages",
    )(put_page)
    pages_router.post(
        f"/{slug}/publish/", name=f"publish_{name}", summary=f"Publish {label}"
    )(publish_page)
    pages_router.post(
        f"/{slug}/unpublish/",
        name=f"unpublish_{name}",
        summary=f"Take {label} off the site",
    )(unpublish_page)


for _slug in service.FIXED_PAGE_SLUGS:
    _register_page(_slug, _PAGE_LABELS[_slug])


__all__ = ["faq_router", "fun_facts_router", "pages_router"]
