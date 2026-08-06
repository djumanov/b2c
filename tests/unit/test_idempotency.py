"""Idempotency-Key behaviour (API.md §10) — the money endpoints depend on it."""

import json
from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api import idempotency
from app.api.envelope import EnvelopeRoute
from app.api.errors import register_exception_handlers
from app.api.idempotency import (
    IN_FLIGHT_TTL_SECONDS,
    KEY_TTL_SECONDS,
    IdempotencyKey,
    fingerprint,
)


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

    @router.post("/crash/")
    async def crash(charge: Charge, key: IdempotencyKey) -> dict[str, object]:
        """Claims the key and then dies, leaving the claim behind."""
        raise RuntimeError("the provider hung up")

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


# --- the race, and what a claim left behind costs ------------------------------------


async def test_a_claim_someone_else_holds_is_a_409(
    pay_client: AsyncClient,
    charges: list[str],
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """The twin is still running. A second charge is not recoverable."""
    body = {"amount": "100.00"}
    headers = {"Idempotency-Key": "key-3"}

    # Let the app write the record, then put it back the way it looked while
    # the first request was still in flight. Reconstructing the fingerprint by
    # hand would only be testing httpx's JSON encoder.
    await pay_client.post("/pay/", json=body, headers=headers)
    record = json.loads(await fake_redis.get("idempotency:key-3"))
    record["response"] = "__in_flight__"
    await fake_redis.set("idempotency:key-3", json.dumps(record))
    charges.clear()

    response = await pay_client.post("/pay/", json=body, headers=headers)

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "conflict"
    assert charges == []


async def test_the_loser_of_a_race_does_not_charge_again(
    pay_client: AsyncClient,
    charges: list[str],
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both requests find nothing; only the one that *created* the claim runs.

    The gap between the read and the write is where a duplicate charge lives,
    so it is forced open here rather than waited for: the read answers "no
    record" to both requests, and `SET NX` is left to pick the winner. Take the
    winner's answer away — use the read alone, as the code once did — and the
    second request charges a second time.
    """

    class BlindReads:
        """Answers the first two reads of a key with "nothing there"."""

        def __init__(self, real: Any) -> None:
            self._real = real
            self._seen: dict[str, int] = {}

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        async def get(self, key: str) -> Any:
            self._seen[key] = self._seen.get(key, 0) + 1
            if self._seen[key] <= 2:
                return None
            return await self._real.get(key)

    monkeypatch.setattr(idempotency, "get_redis", lambda: BlindReads(fake_redis))

    headers = {"Idempotency-Key": "key-4"}
    body = {"amount": "100.00"}

    first = await pay_client.post("/pay/", json=body, headers=headers)
    second = await pay_client.post("/pay/", json=body, headers=headers)

    assert first.status_code == 200
    # The loser is told to retry. What it must not do is charge again.
    assert second.status_code == 409
    assert charges == ["100.00"]


async def test_an_unfinished_claim_expires_in_a_minute_not_a_day(
    pay_client: AsyncClient, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """A handler that died must not lock its key until tomorrow.

    The caller would have no way to make the payment at all: every retry of the
    key it already sent would answer 409 for 24 hours.
    """
    with pytest.raises(RuntimeError):
        await pay_client.post(
            "/crash/", json={"amount": "1.00"}, headers={"Idempotency-Key": "key-5"}
        )

    ttl = await fake_redis.ttl("idempotency:key-5")
    assert 0 < ttl <= IN_FLIGHT_TTL_SECONDS
    assert IN_FLIGHT_TTL_SECONDS < KEY_TTL_SECONDS


async def test_a_stored_outcome_stays_replayable_for_a_day(
    pay_client: AsyncClient, fake_redis: fakeredis.aioredis.FakeRedis
) -> None:
    """The short TTL is for the claim only — API.md §10 promises 24 hours."""
    await pay_client.post(
        "/pay/", json={"amount": "1.00"}, headers={"Idempotency-Key": "key-6"}
    )

    ttl = await fake_redis.ttl("idempotency:key-6")
    assert IN_FLIGHT_TTL_SECONDS < ttl <= KEY_TTL_SECONDS


def test_fingerprint_covers_method_path_and_body() -> None:
    base = fingerprint("POST", "/pay/", b'{"amount":"1"}')

    assert base == fingerprint("POST", "/pay/", b'{"amount":"1"}')
    assert base != fingerprint("PATCH", "/pay/", b'{"amount":"1"}')
    assert base != fingerprint("POST", "/refund/", b'{"amount":"1"}')
    assert base != fingerprint("POST", "/pay/", b'{"amount":"2"}')
