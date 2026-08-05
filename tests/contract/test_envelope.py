"""Every response has the same shape (API.md §2).

These are the tests that keep two surfaces and ~150 endpoints consistent. They
are cheap; the drift they prevent is not.
"""

from httpx import AsyncClient

ENVELOPE_KEYS = {"status", "data", "errors", "meta"}


async def test_single_object_is_wrapped(probe: AsyncClient) -> None:
    response = await probe.get("/probe/item/")

    assert response.status_code == 200
    body = response.json()
    assert body.keys() == ENVELOPE_KEYS
    assert body["status"] == "success"
    assert body["data"] == {"id": 1, "title": "Chegirma"}
    assert body["errors"] == []
    assert body["meta"] is None


async def test_list_lifts_items_into_data_and_meta_into_meta(
    probe: AsyncClient,
) -> None:
    response = await probe.get("/probe/list/")

    body = response.json()
    assert body["data"] == [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}]
    # 137 rows at 20 per page is 7 pages — the ceiling, not the floor.
    assert body["meta"] == {
        "page": 2,
        "page_size": 20,
        "total": 137,
        "total_pages": 7,
    }


async def test_empty_list_still_carries_pagination(probe: AsyncClient) -> None:
    body = (await probe.get("/probe/empty-list/")).json()

    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["meta"]["total_pages"] == 0


async def test_null_data_is_still_enveloped(probe: AsyncClient) -> None:
    body = (await probe.get("/probe/null/")).json()

    assert body["status"] == "success"
    assert body["data"] is None


async def test_204_has_no_body(probe: AsyncClient) -> None:
    """Soft delete answers 204 with nothing (API.md §8)."""
    response = await probe.delete("/probe/item/")

    assert response.status_code == 204
    assert response.content == b""


async def test_non_json_response_passes_through(probe: AsyncClient) -> None:
    """A receipt or an export is a file, not an envelope."""
    response = await probe.get("/probe/file/")

    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content == b"%PDF-1.4"


async def test_endpoint_headers_survive_wrapping(probe: AsyncClient) -> None:
    """`site-config` is served with ETag and Cache-Control (API.md §17)."""
    response = await probe.get("/probe/headers/")

    assert response.headers["etag"] == '"abc123"'
    assert response.headers["cache-control"] == "public, max-age=60"
    assert response.json()["data"]["title"] == "cached"
    # The body was replaced, so the length must describe the new one.
    assert int(response.headers["content-length"]) == len(response.content)


async def test_webhooks_are_not_enveloped(probe: AsyncClient) -> None:
    """Payme parses its own protocol; an envelope would break it (API.md §40)."""
    body = (await probe.post("/webhooks/payments/payme/")).json()

    assert body == {"result": {"state": 1}}
    assert "status" not in body
