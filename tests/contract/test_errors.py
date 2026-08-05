"""The error catalogue: eleven codes, fixed statuses (API.md §3)."""

import pytest
from httpx import AsyncClient

from app.api.errors import ERROR_STATUS, ErrorCode
from tests.contract.conftest import ERROR_CASES


def test_every_code_has_a_probe() -> None:
    """The sweep below is only complete if no code was left out."""
    assert set(ERROR_CASES) == set(ErrorCode)


@pytest.mark.parametrize("code", list(ErrorCode))
async def test_code_maps_to_its_status(probe: AsyncClient, code: ErrorCode) -> None:
    response = await probe.get(f"/probe/raise/{code.value}/")

    assert response.status_code == ERROR_STATUS[code]
    body = response.json()
    assert body["status"] == "error"
    assert body["data"] is None
    assert body["errors"][0]["code"] == code.value
    assert body["errors"][0]["message"]


async def test_field_is_set_only_for_validation(probe: AsyncClient) -> None:
    validation = (await probe.get("/probe/raise/validation/")).json()
    not_found = (await probe.get("/probe/raise/not_found/")).json()

    assert validation["errors"][0]["field"] == "email"
    assert not_found["errors"][0]["field"] is None


async def test_upstream_detail_survives_in_meta(probe: AsyncClient) -> None:
    """GTS's own code and text must not be lost (API.md §3, ARCH §7)."""
    body = (await probe.get("/probe/raise/upstream_error/")).json()

    assert body["meta"]["upstream"] == {
        "code": -104,
        "message": "not enough credits",
    }


async def test_rate_limited_carries_retry_after(probe: AsyncClient) -> None:
    response = await probe.get("/probe/raise/rate_limited/")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


async def test_request_validation_reports_one_error_per_field(
    probe: AsyncClient,
) -> None:
    response = await probe.post("/probe/validate/", json={"id": "abc"})

    assert response.status_code == 422
    body = response.json()
    assert {error["code"] for error in body["errors"]} == {"validation"}
    # "body" is stripped from the location: the client sees the field name.
    assert {error["field"] for error in body["errors"]} == {"id", "title"}


async def test_unexpected_exception_leaks_nothing(probe: AsyncClient) -> None:
    response = await probe.get("/probe/crash/")

    assert response.status_code == 500
    body = response.json()
    assert body["errors"][0]["code"] == "internal"
    assert body["errors"][0]["message"] == "Internal server error"
    assert "RuntimeError" not in response.text
    assert "unexpected" not in response.text


async def test_a_status_outside_the_catalogue_is_mapped_into_it(
    probe: AsyncClient,
) -> None:
    """The catalogue is closed, so a framework status is translated, not passed on.

    A wrong method on a real path is Starlette's 405. The contract has no 405,
    and "that path does not exist for that method" is the honest reading, so it
    comes back as `404 not_found` (API.md §3).
    """
    response = await probe.post("/probe/item/")

    assert response.status_code == 404
    assert response.json()["errors"][0]["code"] == "not_found"
