"""A probe application that exercises the envelope rules in isolation.

The contract has to hold for shapes the real app does not serve yet — a
paginated list, a 204, a raw file response, each of the eleven error codes.
Rather than wait for a module to grow such an endpoint, the probe app mounts
one route per case using the same ``EnvelopeRoute`` and the same exception
handlers as production. If these rules break, they break here first.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter, FastAPI, Response
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.envelope import EnvelopeRoute, Page, PageMeta
from app.api.errors import (
    AppError,
    Conflict,
    ErrorCode,
    Forbidden,
    InternalError,
    NotFound,
    OfferExpired,
    PaymentFailed,
    RateLimited,
    Unauthorized,
    UpstreamError,
    UpstreamTimeout,
    ValidationFailed,
    register_exception_handlers,
)


class Item(BaseModel):
    id: int
    title: str


#: One raiser per code in API.md §3, so the sweep test cannot miss one.
ERROR_CASES: dict[ErrorCode, AppError] = {
    ErrorCode.VALIDATION: ValidationFailed("Bad field", field="email"),
    ErrorCode.UNAUTHORIZED: Unauthorized("No token"),
    ErrorCode.FORBIDDEN: Forbidden("Not allowed"),
    ErrorCode.NOT_FOUND: NotFound("Gone"),
    ErrorCode.CONFLICT: Conflict("Already cancelled"),
    ErrorCode.RATE_LIMITED: RateLimited("Slow down", retry_after=30),
    ErrorCode.UPSTREAM_ERROR: UpstreamError(
        "GTS refused", upstream_code=-104, upstream_message="not enough credits"
    ),
    ErrorCode.UPSTREAM_TIMEOUT: UpstreamTimeout("GTS did not answer"),
    ErrorCode.PAYMENT_FAILED: PaymentFailed("Card declined"),
    ErrorCode.OFFER_EXPIRED: OfferExpired("Offer expired"),
    ErrorCode.INTERNAL: InternalError("Boom"),
}


def build_probe_app() -> FastAPI:
    application = FastAPI(redirect_slashes=False)
    register_exception_handlers(application)

    enveloped = APIRouter(prefix="/probe", route_class=EnvelopeRoute)

    @enveloped.get("/item/")
    async def one_item() -> Item:
        return Item(id=1, title="Chegirma")

    @enveloped.get("/list/")
    async def item_list() -> Page[Item]:
        return Page(
            items=[Item(id=1, title="a"), Item(id=2, title="b")],
            meta=PageMeta.build(page=2, page_size=20, total=137),
        )

    @enveloped.get("/empty-list/")
    async def empty_list() -> Page[Item]:
        return Page(items=[], meta=PageMeta.build(page=1, page_size=20, total=0))

    @enveloped.delete("/item/", status_code=204)
    async def delete_item() -> Response:
        return Response(status_code=204)

    @enveloped.get("/file/")
    async def raw_file() -> Response:
        return Response(content=b"%PDF-1.4", media_type="application/pdf")

    @enveloped.get("/headers/")
    async def with_headers(response: Response) -> Item:
        response.headers["ETag"] = '"abc123"'
        response.headers["Cache-Control"] = "public, max-age=60"
        return Item(id=1, title="cached")

    @enveloped.get("/null/")
    async def nothing() -> None:
        return None

    @enveloped.get("/raise/{code}/")
    async def raise_error(code: str) -> Item:
        raise ERROR_CASES[ErrorCode(code)]

    @enveloped.get("/crash/")
    async def crash() -> Item:
        raise RuntimeError("unexpected")

    @enveloped.post("/validate/")
    async def validate(item: Item) -> Item:
        return item

    # No EnvelopeRoute: the provider's protocol decides the shape (API.md §40).
    webhooks = APIRouter(prefix="/webhooks")

    @webhooks.post("/payments/payme/")
    async def payme_callback() -> dict[str, object]:
        return {"result": {"state": 1}}

    application.include_router(enveloped)
    application.include_router(webhooks)
    return application


@pytest.fixture(scope="session")
def probe_app() -> FastAPI:
    return build_probe_app()


@pytest.fixture
async def probe(probe_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=probe_app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as async_client:
        yield async_client
