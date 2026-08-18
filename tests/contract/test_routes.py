"""Rules that hold across the whole route table, checked by sweeping it.

Written now, while there are two endpoints, so that it is already in place when
there are a hundred and fifty. A convention is only a convention if something
fails when it is broken.
"""

from fastapi.routing import APIRoute, iter_route_contexts
from httpx import AsyncClient

from app.api.envelope import EnvelopeRoute
from app.api.openapi import IDEMPOTENCY_HEADER, WEBHOOK_PATH_MARKER
from app.main import app

API_PREFIX = "/api/v1"


def _api_routes() -> list[tuple[str, APIRoute]]:
    """Every contract route, with the path it is actually served at.

    ``include_router`` mounts a child router by reference rather than copying
    its routes, so ``app.routes`` is not flat and a route's own ``path`` lacks
    the parents' prefixes. ``iter_route_contexts`` resolves both.
    """
    return [
        (context.path, context.route)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.route, APIRoute) and context.path.startswith(API_PREFIX)
    ]


def test_there_are_routes_to_check() -> None:
    assert _api_routes(), "route sweep would pass vacuously"


def test_every_path_ends_with_a_slash() -> None:
    """API.md §1: trailing slash, always."""
    offenders = [path for path, _ in _api_routes() if not path.endswith("/")]

    assert offenders == []


def test_every_path_is_on_a_known_surface() -> None:
    """Only three surfaces exist (PROJECT.md §6)."""
    allowed = (
        f"{API_PREFIX}/public/",
        f"{API_PREFIX}/admin/",
        f"{API_PREFIX}/webhooks/",
    )
    offenders = [path for path, _ in _api_routes() if not path.startswith(allowed)]

    assert offenders == []


def test_public_and_admin_routes_use_the_envelope_route_class() -> None:
    """A module router that forgets ``enveloped_router`` fails here.

    ``route_class`` is not inherited from the parent router, so this cannot be
    enforced at assembly time — only detected.
    """
    offenders = [
        path
        for path, route in _api_routes()
        if WEBHOOK_PATH_MARKER not in path and not isinstance(route, EnvelopeRoute)
    ]

    assert offenders == [], (
        f"{offenders} return unenveloped responses. Build the module router "
        "with app.api.envelope.enveloped_router()."
    )


def test_resource_names_use_hyphens_not_underscores() -> None:
    """API.md §16: `popular-directions/`, never `popular_directions/`."""
    offenders = [path for path, _ in _api_routes() if "_" in path]

    assert offenders == []


async def test_missing_slash_is_404_not_a_redirect(client: AsyncClient) -> None:
    """A 307 would drop a POST body on the way through."""
    response = await client.get(f"{API_PREFIX}/admin/system/version")

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "not_found"


async def test_request_id_is_echoed_when_supplied(client: AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "trace-123"})

    assert response.headers["X-Request-Id"] == "trace-123"


async def test_request_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.headers["X-Request-Id"]


async def test_openapi_documents_the_envelope(client: AsyncClient) -> None:
    """The published schema must describe what is actually sent."""
    schema = (await client.get(f"{API_PREFIX}/openapi.json")).json()

    operation = schema["paths"][f"{API_PREFIX}/admin/system/version/"]["get"]
    success = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert success["properties"]["status"]["enum"] == ["success"]
    assert success["properties"]["data"]["$ref"].endswith("VersionOut")
    assert set(success["required"]) == {"status", "data", "errors", "meta"}
    # The shared failures from API.md §3 are documented too.
    assert {"401", "403", "404", "422", "429", "500"} <= set(operation["responses"])


async def test_a_mandatory_idempotency_key_is_published_as_mandatory(
    client: AsyncClient,
) -> None:
    """The dependency declares the header ``str | None`` so that a missing one
    becomes our own ``422`` rather than FastAPI's, which leaves the generated
    schema calling it optional. It is not, and a client reads the schema.

    Swept rather than pinned to one path: booking, paying and refunding all
    carry it, and the next money endpoint must not be the one that forgets.
    """
    schema = (await client.get(f"{API_PREFIX}/openapi.json")).json()

    headers = [
        (path, method, parameter)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        for parameter in operation.get("parameters") or []
        if parameter.get("name") == IDEMPOTENCY_HEADER
    ]

    assert headers, "no endpoint documents an Idempotency-Key"
    optional = [
        f"{method.upper()} {path}"
        for path, method, parameter in headers
        if not parameter.get("required")
    ]
    assert optional == []
    # ``str | None`` publishes an anyOf with a null branch; absent is a 422,
    # not a null.
    assert all("anyOf" not in parameter["schema"] for _, _, parameter in headers)
