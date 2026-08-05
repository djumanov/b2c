"""Every request leaves one log line, tied to its id (API.md §13).

Support starts from a request id, so a request whose only log line lacks one is
a request nobody can find.
"""

import logging
from typing import Any

import pytest
from httpx import AsyncClient


def _access_lines(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    """structlog passes the whole event dict through as the record message."""
    return [
        record.msg
        for record in caplog.records
        if record.name == "app.access" and isinstance(record.msg, dict)
    ]


@pytest.fixture
def access_records(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="app.access")
    return caplog


async def test_request_is_logged_with_its_id(
    client: AsyncClient, access_records: pytest.LogCaptureFixture
) -> None:
    await client.get(
        "/api/v1/admin/system/version/", headers={"X-Request-Id": "trace-abc"}
    )

    assert _access_lines(access_records), "no access line was written"
    entry = _access_lines(access_records)[-1]
    assert entry["request_id"] == "trace-abc"
    assert entry["path"] == "/api/v1/admin/system/version/"
    assert entry["method"] == "GET"
    # Unauthenticated, so 401 — the status must be recorded, not just the path.
    assert entry["status_code"] == 401
    assert entry["duration_ms"] >= 0


async def test_generated_id_is_logged_and_returned(
    client: AsyncClient, access_records: pytest.LogCaptureFixture
) -> None:
    response = await client.get("/api/v1/admin/system/version/")

    entry = _access_lines(access_records)[-1]
    assert entry["request_id"] == response.headers["X-Request-Id"]


async def test_healthcheck_is_not_logged(
    client: AsyncClient, access_records: pytest.LogCaptureFixture
) -> None:
    """The container probes it every 30s; logging it buries real traffic."""
    await client.get("/healthz")

    assert _access_lines(access_records) == []
