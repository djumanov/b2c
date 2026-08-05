"""Idempotency-Key behaviour (API.md §10) — the money endpoints depend on it."""

from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.envelope import EnvelopeRoute
from app.api.errors import register_exception_handlers
from app.api.idempotency import IdempotencyKey, fingerprint


class Charge(BaseModel):
    amount: str


def build_app(charges: list[str]) -> FastAPI:
    """A single money endpoint. ``charges`` records real work being done."""
    application = FastAPI(redirect_slashes=False)
    register_exception_handlers(application)

    router = APIRouter(route_class=EnvelopeRoute)

    @router.post("/pay/")
    async def pay(charge: Charge, key: IdempotencyKey) -> dict[str, object]:
        if key.is_replay:
            return dict(key.replayed or {})
        charges.append(charge.amount)
        result: dict[str, object] = {
            "charged": charge.amount,
            "call_number": len(charges),
        }
        await key.store(result)
        return result

    application.include_router(router)
    return application


@pytest.fixture
def charges() -> list[str]:
    return []


@pytest.fixture
async def pay_client(charges: list[str]) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=build_app(charges)),
        base_url="http://testserver",
    ) as client:
        yield client


async def test_missing_key_is_422(pay_client: AsyncClient) -> None:
    """Never a silent pass: an unkeyed charge is rejected."""
    response = await pay_client.post("/pay/", json={"amount": "100.00"})

    assert response.status_code == 422
    error = response.json()["errors"][0]
    assert error["code"] == "validation"
    assert error["field"] == "Idempotency-Key"


async def test_repeat_replays_instead_of_charging_twice(
    pay_client: AsyncClient, charges: list[str]
) -> None:
    headers = {"Idempotency-Key": "key-1"}
    body = {"amount": "100.00"}

    first = await pay_client.post("/pay/", json=body, headers=headers)
    second = await pay_client.post("/pay/", json=body, headers=headers)

    assert first.json()["data"] == second.json()["data"]
    assert second.json()["data"]["call_number"] == 1
    assert charges == ["100.00"]


async def test_different_keys_both_charge(
    pay_client: AsyncClient, charges: list[str]
) -> None:
    body = {"amount": "100.00"}

    await pay_client.post("/pay/", json=body, headers={"Idempotency-Key": "a"})
    await pay_client.post("/pay/", json=body, headers={"Idempotency-Key": "b"})

    assert charges == ["100.00", "100.00"]


async def test_key_reused_with_a_different_body_is_422(
    pay_client: AsyncClient, charges: list[str]
) -> None:
    """A client bug. Replaying the old answer would hide it."""
    headers = {"Idempotency-Key": "key-2"}

    await pay_client.post("/pay/", json={"amount": "100.00"}, headers=headers)
    response = await pay_client.post(
        "/pay/", json={"amount": "999.00"}, headers=headers
    )

    assert response.status_code == 422
    assert "different request" in response.json()["errors"][0]["message"]
    assert charges == ["100.00"]


async def test_blank_key_is_rejected(pay_client: AsyncClient) -> None:
    response = await pay_client.post(
        "/pay/", json={"amount": "1.00"}, headers={"Idempotency-Key": "   "}
    )

    assert response.status_code == 422


def test_fingerprint_covers_method_path_and_body() -> None:
    base = fingerprint("POST", "/pay/", b'{"amount":"1"}')

    assert base == fingerprint("POST", "/pay/", b'{"amount":"1"}')
    assert base != fingerprint("PATCH", "/pay/", b'{"amount":"1"}')
    assert base != fingerprint("POST", "/refund/", b'{"amount":"1"}')
    assert base != fingerprint("POST", "/pay/", b'{"amount":"2"}')
