"""Application factory: ``uv run uvicorn app.main:app``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.middleware import RequestIdMiddleware, allowed_origins
from app.api.openapi import build_openapi
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.redis import close_redis
from app.db.session import dispose_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("startup", version=settings.app_version, debug=settings.debug)
    yield
    await dispose_engine()
    await close_redis()
    logger.info("shutdown")


def create_app() -> FastAPI:
    configure_logging()

    application = FastAPI(
        title="B2C Platform API",
        version=settings.app_version,
        description=(
            "White-label travel platform. The contract is docs/API.md; this "
            "schema is its artefact."
        ),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        # Every path in the contract ends with a slash (API.md §1). Redirecting
        # instead of 404-ing would hide the mistake and, worse, drop the body of
        # a POST on the way through the 307.
        redirect_slashes=False,
    )

    application.add_middleware(RequestIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "Retry-After"],
    )

    register_exception_handlers(application)
    application.include_router(api_router)

    @application.get("/healthz", include_in_schema=False)
    async def liveness() -> dict[str, str]:
        """Liveness for the container healthcheck.

        Deliberately outside ``/api/v1`` and outside the contract: it says the
        process is up, nothing more. Whether the installation actually works is
        ``GET /api/v1/admin/system/health/`` (API.md §39), which is authenticated
        because it names what is broken.
        """
        return {"status": "ok"}

    def openapi() -> dict[str, Any]:
        return build_openapi(application)

    application.openapi = openapi  # type: ignore[method-assign]
    return application


app = create_app()

__all__ = ["app", "create_app"]
