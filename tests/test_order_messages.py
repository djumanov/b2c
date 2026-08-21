"""``/admin/orders/messages/`` — the customer's sentences, written by the panel."""

from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.customers.models import Customer
from app.modules.orders.lifecycle import Stage
from app.modules.orders.messages import DEFAULTS
from app.modules.orders.models import OrderMessage
from tests.conftest import make_order, make_staff, staff_bearer

URL = "/api/v1/admin/orders/messages/"
ORDERS_URL = "/api/v1/public/orders/"


async def _patch(
    client: httpx.AsyncClient,
    status: str,
    text: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    return await client.patch(f"{URL}{status}/", json={"text": text}, headers=headers)


async def test_list_seeds_every_status_with_defaults(
    client: httpx.AsyncClient, staff_headers: dict[str, str], db_session: AsyncSession
) -> None:
    response = await client.get(URL, headers=staff_headers)

    assert response.status_code == 200
    items = response.json()["data"]
    assert [item["status"] for item in items] == [status.value for status in Stage]
    for item in items:
        assert item["custom"] == {}
        assert item["default"] == DEFAULTS[Stage(item["status"])]
        assert item["text"] == item["default"]
        assert set(item["text"]) == {"uz", "ru", "en"}
    rows = (await db_session.scalars(select(OrderMessage))).all()
    assert len(rows) == len(Stage)


async def test_patch_merges_one_language_and_detail_shows_it(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    order = await make_order(
        db_session, customer, payment_status="paid", ticketing_status="failed"
    )
    sentence = "Оплата прошла, билет не выписан. Звоните: +998 71 200 00 00"

    response = await _patch(client, "ticketing_failed", {"ru": sentence}, staff_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["custom"] == {"ru": sentence}
    assert data["text"]["ru"] == sentence
    assert data["text"]["uz"] == DEFAULTS[Stage.TICKETING_FAILED]["uz"]  # untouched

    in_ru = await client.get(
        f"{ORDERS_URL}{order.id}/", params={"lang": "ru"}, headers=customer_headers
    )
    in_uz = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)
    assert in_ru.json()["data"]["order"]["message"] == sentence
    assert (
        in_uz.json()["data"]["order"]["message"]
        == DEFAULTS[Stage.TICKETING_FAILED]["uz"]
    )

    single = await client.get(f"{URL}ticketing_failed/", headers=staff_headers)
    assert single.json()["data"]["custom"] == {"ru": sentence}


async def test_empty_string_resets_language_to_default(
    client: httpx.AsyncClient, staff_headers: dict[str, str]
) -> None:
    await _patch(
        client, "ticketed", {"uz": "Chipta tayyor!", "en": "Done."}, staff_headers
    )

    response = await _patch(client, "ticketed", {"uz": "   "}, staff_headers)

    data = response.json()["data"]
    assert data["custom"] == {"en": "Done."}
    assert data["text"]["uz"] == DEFAULTS[Stage.TICKETED]["uz"]
    assert data["text"]["en"] == "Done."


async def test_unknown_language_is_dropped_and_unknown_status_is_422(
    client: httpx.AsyncClient, staff_headers: dict[str, str]
) -> None:
    dropped = await _patch(
        client, "ticketed", {"de": "Fertig", "uz": "Tayyor"}, staff_headers
    )
    assert dropped.status_code == 200
    assert dropped.json()["data"]["custom"] == {"uz": "Tayyor"}

    only_unknown = await _patch(client, "ticketed", {"de": "Fertig"}, staff_headers)
    assert only_unknown.status_code == 422
    assert only_unknown.json()["errors"][0]["field"] == "text"

    too_long = await _patch(client, "ticketed", {"uz": "x" * 1001}, staff_headers)
    assert too_long.status_code == 422

    unknown_status = await _patch(client, "shipped", {"uz": "Yo'lda"}, staff_headers)
    assert unknown_status.status_code == 422
    assert (
        await client.get(f"{URL}shipped/", headers=staff_headers)
    ).status_code == 422
    # A status this release retired is no more a key than one it never had.
    retired = await _patch(client, "awaiting_payment", {"uz": "Kuting"}, staff_headers)
    assert retired.status_code == 422

    empty = await client.patch(f"{URL}ticketed/", json={}, headers=staff_headers)
    assert empty.status_code == 422


async def test_text_is_verbatim_no_interpolation(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    order = await make_order(db_session, customer)
    literal = "Kutamiz {support} {} — 100% {pnr}"

    await _patch(client, "booked", {"uz": literal}, staff_headers)

    response = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)
    assert response.json()["data"]["order"]["message"] == literal


async def test_fallback_uses_site_default_language(
    client: httpx.AsyncClient,
    customer: Customer,
    customer_headers: dict[str, str],
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """No requested language: the installation's default decides, not ``uz``."""
    from app.modules.settings import service as settings_service
    from app.modules.settings.schemas import LanguagesIn

    await settings_service.update_languages(
        db_session, LanguagesIn(default="ru", available=["ru", "uz", "en"])
    )
    order = await make_order(db_session, customer)
    await _patch(client, "booked", {"ru": "Ждём оплату"}, staff_headers)

    response = await client.get(f"{ORDERS_URL}{order.id}/", headers=customer_headers)
    assert response.json()["data"]["order"]["message"] == "Ждём оплату"

    # A language the panel never wrote and the defaults do not have falls
    # through the chain to the default language rather than to nothing.
    await _patch(client, "booked", {"uz": "", "en": ""}, staff_headers)
    in_de = await client.get(
        f"{ORDERS_URL}{order.id}/",
        headers={**customer_headers, "Accept-Language": "de"},
    )
    assert in_de.json()["data"]["order"]["message"] == "Ждём оплату"


async def test_list_drops_rows_of_retired_statuses(
    client: httpx.AsyncClient, staff_headers: dict[str, str], db_session: AsyncSession
) -> None:
    """The table is a registry keyed by the enum: a key no release renders any
    more is not a setting, and goes on the next read — text and all."""
    db_session.add(OrderMessage(key="awaiting_payment", text={"uz": "Eski matn"}))
    db_session.add(OrderMessage(key="refund_due", text={}))
    await db_session.commit()

    response = await client.get(URL, headers=staff_headers)

    assert response.status_code == 200
    assert [item["status"] for item in response.json()["data"]] == [
        status.value for status in Stage
    ]
    keys = set(await db_session.scalars(select(OrderMessage.key)))
    assert keys == {status.value for status in Stage}


async def test_roles_and_audit(
    client: httpx.AsyncClient,
    customer_headers: dict[str, str],
    staff: Any,
    staff_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    assert (await client.get(URL)).status_code == 401
    assert (await client.get(URL, headers=customer_headers)).status_code == 403
    assert (
        await _patch(client, "ticketed", {"uz": "x"}, customer_headers)
    ).status_code == 403

    # Day-to-day wording: an ``admin`` writes too, not only the ``owner``.
    admin = await make_staff(db_session, "admin@brand.uz", role="admin")
    response = await _patch(
        client, "ticketed", {"uz": "Chipta tayyor"}, staff_bearer(admin)
    )
    assert response.status_code == 200

    journal = (await db_session.scalars(select(AuditLog))).all()
    assert len(journal) == 1
    entry = journal[0]
    assert entry.actor_id == admin.id
    assert entry.resource == "orders.messages"
    assert entry.changes == {"uz": {"from": None, "to": "Chipta tayyor"}}
