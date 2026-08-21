"""``/admin/integrations/payments/`` with a real adapter behind ``payme``.

The registry itself — one row per code, masking, merge, one enabled at a
time — predates the adapter. What the adapter adds is pinned here: the
declared ``fields``, what ``PATCH`` refuses, which values come back in the
clear, and the test button.
"""

from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from tests.conftest import (
    PAYME_CREDENTIALS,
    RpcError,
    make_staff,
    mock_payme,
    staff_bearer,
)

PAYMENTS = "/api/v1/admin/integrations/payments/"
PAYME = f"{PAYMENTS}payme/"
CLICK = f"{PAYMENTS}click/"


async def _patch(
    client: httpx.AsyncClient, headers: dict[str, str], **body: Any
) -> httpx.Response:
    return await client.patch(PAYME, json=body, headers=headers)


async def test_fields_describe_the_form_and_secrets_alone_are_masked(
    client: httpx.AsyncClient, staff_headers: dict[str, str]
) -> None:
    listed = await client.get(PAYMENTS, headers=staff_headers)
    assert listed.status_code == 200
    by_code = {row["code"]: row for row in listed.json()["data"]}
    payme_fields = {field["key"]: field for field in by_code["payme"]["fields"]}
    assert payme_fields["merchant_id"] == {
        "key": "merchant_id",
        "kind": "text",
        "required": True,
        "choices": [],
        "default": None,
    }
    assert payme_fields["key"]["kind"] == "secret"
    assert payme_fields["environment"]["choices"] == ["production", "test"]
    assert payme_fields["environment"]["default"] == "production"
    # No adapter, no form: Click keeps the free key/value editor.
    assert by_code["click"]["fields"] == []

    saved = await _patch(client, staff_headers, credentials=PAYME_CREDENTIALS)
    assert saved.status_code == 200, saved.text
    shown = saved.json()["data"]["credentials"]
    assert shown["merchant_id"] == "m-1234abcd"
    assert shown["environment"] == "test"
    assert "•" in shown["key"]
    assert "k-secret-9f" not in saved.text

    # Click stores the same keys but declares nothing — everything is masked.
    click = await client.patch(
        CLICK, json={"credentials": PAYME_CREDENTIALS}, headers=staff_headers
    )
    assert click.status_code == 200
    assert all("•" in value for value in click.json()["data"]["credentials"].values())


async def test_patch_refuses_what_the_adapter_would_not_understand(
    client: httpx.AsyncClient, staff_headers: dict[str, str]
) -> None:
    unknown = await _patch(client, staff_headers, credentials={"merchant-id": "m-1"})
    assert unknown.status_code == 422
    assert "merchant-id" in unknown.json()["errors"][0]["message"]

    staging = await _patch(
        client,
        staff_headers,
        credentials={**PAYME_CREDENTIALS, "environment": "staging"},
    )
    assert staging.status_code == 422
    assert "production, test" in staging.json()["errors"][0]["message"]

    vat = await _patch(
        client,
        staff_headers,
        credentials={**PAYME_CREDENTIALS, "fiscal_vat_percent": "twelve"},
    )
    assert vat.status_code == 422
    assert "whole number" in vat.json()["errors"][0]["message"]

    # Nothing of the refused patches was kept.
    current = await client.get(PAYME, headers=staff_headers)
    assert current.json()["data"]["credentials"] == {}


async def test_switching_on_needs_both_halves_of_the_credentials(
    client: httpx.AsyncClient, staff_headers: dict[str, str]
) -> None:
    half = await _patch(
        client, staff_headers, credentials={"merchant_id": "m-1"}, enabled=True
    )
    assert half.status_code == 422
    assert half.json()["errors"][0]["field"] == "enabled"
    assert "key" in half.json()["errors"][0]["message"]

    whole = await _patch(
        client, staff_headers, credentials=PAYME_CREDENTIALS, enabled=True
    )
    assert whole.status_code == 200, whole.text
    assert whole.json()["data"]["enabled"] is True

    # A masked echo of the secret keeps it; a plain setting may be re-sent.
    echoed = await _patch(
        client,
        staff_headers,
        credentials={
            "key": whole.json()["data"]["credentials"]["key"],
            "environment": "test",
        },
    )
    assert echoed.status_code == 200
    # Deleting the key switches nothing off by itself, but the row can no
    # longer be switched on again without it.
    cleared = await _patch(
        client, staff_headers, credentials={"key": None}, enabled=False
    )
    assert cleared.status_code == 200
    again = await _patch(client, staff_headers, enabled=True)
    assert again.status_code == 422


@respx.mock
async def test_the_test_button_asks_payme_and_records_the_answer(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    payme = mock_payme()
    await _patch(client, staff_headers, credentials=PAYME_CREDENTIALS)

    # Before switching on — that is what the button is for.
    probed = await client.post(f"{PAYME}test/", headers=staff_headers)

    assert probed.status_code == 200, probed.text
    data = probed.json()["data"]
    assert data["ok"] is True
    assert data["detail"] is None
    assert payme.methods == ["receipts.get_all"]
    assert payme.auth("receipts.get_all") == "m-1234abcd:k-secret-9f"
    shown = (await client.get(PAYME, headers=staff_headers)).json()["data"]
    assert shown["last_test_ok"] is True
    assert shown["last_tested_at"] == data["tested_at"]

    respx.reset()
    mock_payme({"receipts.get_all": RpcError(-32504, "Insufficient privileges")})
    refused = await client.post(f"{PAYME}test/", headers=staff_headers)
    assert refused.status_code == 200
    assert refused.json()["data"]["ok"] is False
    assert "Insufficient privileges" in refused.json()["data"]["detail"]
    shown = (await client.get(PAYME, headers=staff_headers)).json()["data"]
    assert shown["last_test_ok"] is False
    assert "Insufficient privileges" in shown["last_test_error"]

    # Settings the adapter cannot work with are an answer too, not a 502.
    await _patch(client, staff_headers, credentials={"fiscal_title": "Aviachipta"})
    unfinished = await client.post(f"{PAYME}test/", headers=staff_headers)
    assert unfinished.status_code == 200
    assert unfinished.json()["data"]["ok"] is False
    assert "together" in unfinished.json()["data"]["detail"]

    # The journal knows the button was pressed and nothing more.
    rows = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.resource == "integrations.payments")
        )
    ).all()
    assert {row.action for row in rows} == {"update", "test"}
    for row in rows:
        assert "k-secret-9f" not in str(row.changes)
        assert "m-1234abcd" not in str(row.changes)


async def test_the_test_button_needs_settings_an_adapter_and_an_owner(
    client: httpx.AsyncClient,
    staff_headers: dict[str, str],
    customer_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    empty = await client.post(f"{PAYME}test/", headers=staff_headers)
    assert empty.status_code == 409
    assert "settings first" in empty.json()["errors"][0]["message"]

    await client.patch(
        CLICK, json={"credentials": PAYME_CREDENTIALS}, headers=staff_headers
    )
    click = await client.post(f"{CLICK}test/", headers=staff_headers)
    assert click.status_code == 409
    assert "not available in this release" in click.json()["errors"][0]["message"]

    admin = await make_staff(db_session, "admin@brand.uz", role="admin")
    forbidden = await client.post(f"{PAYME}test/", headers=staff_bearer(admin))
    assert forbidden.status_code == 403
    assert (
        await client.post(f"{PAYME}test/", headers=customer_headers)
    ).status_code == 403
    assert (await client.post(f"{PAYME}test/")).status_code == 401


@pytest.mark.parametrize("code", ["payme", "click"])
async def test_reads_are_open_to_admins(
    client: httpx.AsyncClient, db_session: AsyncSession, code: str
) -> None:
    admin = await make_staff(db_session, "reader@brand.uz", role="admin")
    response = await client.get(f"{PAYMENTS}{code}/", headers=staff_bearer(admin))
    assert response.status_code == 200
    assert response.json()["data"]["code"] == code
