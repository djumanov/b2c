"""``RequireFeature`` — what a switched-off section actually answers.

Driven through a small app of its own rather than the real one, because no
module that would be gated is built yet (``tests/unit/test_rate_limit.py``
does the same for the limiter). The flag is read through the genuine
``site-config`` cache on fake Redis, so this exercises the real path rather
than a stubbed one.
"""

from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import RequireFeature
from app.api.envelope import EnvelopeRoute, enveloped_router
from app.api.errors import register_exception_handlers
from app.modules.settings import cache


@pytest.fixture
async def gated() -> AsyncIterator[AsyncClient]:
    application = FastAPI(redirect_slashes=False)
    register_exception_handlers(application)

    router: APIRouter = enveloped_router(route_class=EnvelopeRoute)

    @router.get("/blog/", dependencies=[Depends(RequireFeature("blog"))])
    async def blog() -> dict[str, str]:
        return {"ok": "yes"}

    application.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        yield client


async def _cache_features(flags: dict[str, bool]) -> None:
    """Seed the cached document the gate reads from."""
    await cache.write({"features": flags})


async def test_a_section_that_is_on_answers_normally(gated: AsyncClient) -> None:
    await _cache_features({"blog": True})

    response = await gated.get("/blog/")

    assert response.status_code == 200
    assert response.json()["data"] == {"ok": "yes"}


async def test_a_section_that_is_off_is_not_found(gated: AsyncClient) -> None:
    await _cache_features({"blog": False})

    response = await gated.get("/blog/")

    assert response.status_code == 404
    error = response.json()["errors"][0]
    assert error["code"] == "not_found"
    assert "not available on this installation" in error["message"]


async def test_a_flag_the_document_does_not_carry_falls_back_to_its_default(
    gated: AsyncClient,
) -> None:
    """A flag added in a new version is on before anybody has touched it."""
    await _cache_features({"faq": False})

    assert (await gated.get("/blog/")).status_code == 200


async def test_switching_it_off_takes_effect_on_the_next_request(
    gated: AsyncClient,
) -> None:
    """No restart, no per-process cache to go stale — the point of the design."""
    await _cache_features({"blog": True})
    assert (await gated.get("/blog/")).status_code == 200

    await _cache_features({"blog": False})
    assert (await gated.get("/blog/")).status_code == 404


def test_an_unknown_flag_is_refused_where_it_is_written() -> None:
    """At import, not at request time — a typo takes the process down on boot."""
    with pytest.raises(KeyError, match="unknown feature"):
        RequireFeature("blogg")


def test_the_gate_remembers_which_flag_it_guards() -> None:
    """What the contract sweep reads off the dependency tree."""
    assert RequireFeature("blog").flag == "blog"
