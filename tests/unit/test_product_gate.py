"""The product gate — which verticals answer, and how the rest disappear.

Verticals are **not** feature flags (API.md §28): what a client sells comes
from their GTS agreement through ``product_settings``, read through the same
cached site-config document as the flags. Four different reasons to miss —
unknown code, vertical not implemented, step not declared, vertical switched
off — must all answer the identical 404, because from outside "we do not sell
this" and "this does not exist" must be indistinguishable (API.md §41).

Driven through the real app: the routes exist, only Redis (faked) is touched —
the gate answers before any handler could want a database.
"""

import pytest
from httpx import AsyncClient

from app.modules.settings import cache
from app.modules.settings import service as settings_service
from app.providers.products.base import FlowStep, ProductCode, registry


async def _cache_products(items: list[dict[str, object]]) -> None:
    """Seed the cached document the gate reads from."""
    await cache.write({"products": items})


# --- product_enabled -----------------------------------------------------------------


async def test_an_enabled_row_reads_true() -> None:
    await _cache_products([{"code": "flight", "enabled": True}])

    assert await settings_service.product_enabled("flight") is True


async def test_a_disabled_row_reads_false() -> None:
    await _cache_products([{"code": "flight", "enabled": False}])

    assert await settings_service.product_enabled("flight") is False


async def test_a_code_missing_from_the_list_reads_false() -> None:
    await _cache_products([{"code": "railway", "enabled": True}])

    assert await settings_service.product_enabled("flight") is False


async def test_a_document_without_the_key_reads_enabled() -> None:
    """A pre-upgrade cache must not switch the shop off; the seed enables all."""
    await cache.write({"features": {}})

    assert await settings_service.product_enabled("flight") is True


# --- the gate on the real routes -----------------------------------------------------


NOT_AVAILABLE = "This section is not available on this installation"


async def test_a_disabled_vertical_is_not_found(client: AsyncClient) -> None:
    await _cache_products([{"code": "flight", "enabled": False}])

    response = await client.post("/api/v1/public/flight/search/", json={})

    assert response.status_code == 404
    error = response.json()["errors"][0]
    assert error["code"] == "not_found"
    assert error["message"] == NOT_AVAILABLE


async def test_an_unknown_product_is_the_same_404(client: AsyncClient) -> None:
    await _cache_products([{"code": "flight", "enabled": True}])

    response = await client.post("/api/v1/public/hotels/search/", json={})

    assert response.status_code == 404
    assert response.json()["errors"][0]["message"] == NOT_AVAILABLE


async def test_an_unimplemented_vertical_is_the_same_404(client: AsyncClient) -> None:
    """``railway`` is a real code with no adapter until phase 3."""
    await _cache_products([{"code": "railway", "enabled": True}])

    response = await client.post("/api/v1/public/railway/search/", json={})

    assert response.status_code == 404
    assert response.json()["errors"][0]["message"] == NOT_AVAILABLE


async def test_an_undeclared_step_is_the_same_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``flight`` now declares the whole pre-booking flow, so the step branch
    is proven with a stub vertical that only searches. The gate answers off
    ``supports()`` alone — no handler is reached, so no flow methods exist."""

    class _SearchOnly:
        code = ProductCode.ESIM

        def supports(self) -> frozenset[FlowStep]:
            return frozenset({FlowStep.SEARCH})

    monkeypatch.setitem(registry._adapters, ProductCode.ESIM, _SearchOnly())  # type: ignore[arg-type]
    await _cache_products([{"code": "esim", "enabled": True}])

    response = await client.post("/api/v1/public/esim/verify/", json={})

    assert response.status_code == 404
    assert response.json()["errors"][0]["message"] == NOT_AVAILABLE


async def test_switching_a_vertical_off_needs_no_restart(client: AsyncClient) -> None:
    """The gate reads the cached document per request — a write lands on the
    next one. (The enabled side of the flip needs a database for the
    credential lookup, so it lives in the integration suite.)"""
    await _cache_products([{"code": "flight", "enabled": True}])
    await _cache_products([{"code": "flight", "enabled": False}])

    response = await client.post("/api/v1/public/flight/search/", json={})

    assert response.status_code == 404
