"""Request limits (API.md §14)."""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import RATE_LIMITS, RateLimit
from app.api.envelope import EnvelopeRoute
from app.api.errors import register_exception_handlers


def test_documented_limits() -> None:
    assert RATE_LIMITS == {
        "auth": (5, 60),
        "search": (30, 60),
        "public": (120, 60),
        "admin": (300, 60),
    }


@pytest.fixture
async def auth_client() -> AsyncClient:
    application = FastAPI(redirect_slashes=False)
    register_exception_handlers(application)
    router = APIRouter(route_class=EnvelopeRoute)

    @router.post("/login/", dependencies=[Depends(RateLimit("auth"))])
    async def login() -> dict[str, str]:
        return {"ok": "yes"}

    application.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as client:
        yield client


async def test_sixth_login_attempt_is_rate_limited(auth_client: AsyncClient) -> None:
    """5 per minute per IP (API.md §14)."""
    for _ in range(5):
        assert (await auth_client.post("/login/")).status_code == 200

    response = await auth_client.post("/login/")

    assert response.status_code == 429
    assert response.json()["errors"][0]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) > 0


async def test_limit_is_per_caller(auth_client: AsyncClient) -> None:
    """One noisy IP must not lock everyone else out."""
    for _ in range(6):
        await auth_client.post("/login/", headers={"X-Forwarded-For": "10.0.0.1"})

    other = await auth_client.post("/login/", headers={"X-Forwarded-For": "10.0.0.2"})

    assert other.status_code == 200
