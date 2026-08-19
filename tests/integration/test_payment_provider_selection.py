"""Which provider charges — API.md §22 and §29 (``O15``).

The customer does not choose. One provider is enabled at a time and that one
takes the money, so "which is on" and "which charges" are the same question with
one answer. What is pinned here is that switching one on switches the others
off, that the charge follows the switch, and that ``site-config`` stops
advertising a choice it no longer offers.
"""

from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integrations import service as integrations_service
from app.modules.staff.models import Staff
from app.providers.payments.base import PaymentProviderCode
from tests.integration.conftest import headers_for

PAYMENTS = "/api/v1/admin/integrations/payments/"
CONFIG = "/api/v1/public/site-config/"
CREDENTIALS = {"merchant_id": "merchant-1234abcd", "key": "a-secret"}


async def _enable(api: AsyncClient, owner: Staff, code: str) -> Any:
    return await api.patch(
        f"{PAYMENTS}{code}/",
        headers=headers_for(owner),
        json={"credentials": CREDENTIALS, "enabled": True},
    )


async def test_switching_one_on_switches_the_other_off(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """One click, one intent. Refusing with a ``409`` would make the owner do
    the same job in two steps and read a rule to find out why."""
    assert (await _enable(api, owner, "payme")).status_code == 200

    assert (await _enable(api, owner, "click")).status_code == 200

    listed = await api.get(PAYMENTS, headers=headers_for(owner))
    enabled = {row["code"]: row["enabled"] for row in listed.json()["data"]}
    assert enabled == {"payme": False, "click": True}


async def test_the_provider_left_dark_keeps_its_credentials(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """A second provider may sit configured and switched off, ready for the day
    the installation moves to it."""
    await _enable(api, owner, "payme")
    await _enable(api, owner, "click")

    configured = await integrations_service.payment_providers(session)
    # ``payment_providers`` lists only what could charge, so Payme is absent —
    # but its row still holds what it would need.
    assert [row.code for row in configured] == [PaymentProviderCode.CLICK]

    payme = await api.get(f"{PAYMENTS}payme/", headers=headers_for(owner))
    assert payme.json()["data"]["enabled"] is False
    # Never readable, but present: the mask is what the panel shows back.
    assert payme.json()["data"]["credentials"]["merchant_id"].endswith("abcd")


async def test_the_charging_provider_is_the_enabled_one(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    await _enable(api, owner, "click")

    active = await integrations_service.active_payment_provider(session)

    assert active is not None
    assert active.code is PaymentProviderCode.CLICK


async def test_an_installation_with_nothing_enabled_charges_through_nothing(
    session: AsyncSession,
) -> None:
    assert await integrations_service.active_payment_provider(session) is None


async def test_site_config_advertises_one_provider_not_a_choice(
    api: AsyncClient, session: AsyncSession, owner: Staff
) -> None:
    """The list became branding when the customer stopped picking (§17)."""
    await _enable(api, owner, "payme")

    methods = (await api.get(CONFIG)).json()["data"]["payment_methods"]

    assert [row["code"] for row in methods] == ["payme"]
    assert "enabled" not in methods[0]
