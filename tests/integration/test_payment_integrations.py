"""Payment provider settings — API.md §29, and what reaches §17 and §39.

The two behaviours worth more than a round trip are both about secrets: they
never come back readable, and a panel that echoes its own `GET` back must not
overwrite them with the mask it was shown.

The `test/` endpoint is deliberately absent until phase 2 (PHASES.md §2.13) and
there is a test below pinning that, so switching it on later is a decision
somebody makes rather than a route that quietly appears.
"""

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.integrations import service
from app.modules.integrations.models import PaymentProvider
from app.modules.settings import cache
from app.modules.staff.models import Staff
from app.providers.payments.base import PaymentProviderCode
from tests.integration.conftest import headers_for

PAYMENTS = "/api/v1/admin/integrations/payments/"
PAYME = f"{PAYMENTS}payme/"
CONFIG = "/api/v1/public/site-config/"
HEALTH = "/api/v1/admin/system/health/"

#: A secret that could not appear in a response by coincidence.
SECRET_KEY = "payme-secret-4f19c7"
CREDENTIALS = {"merchant_id": "merchant-1234abcd", "secret_key": SECRET_KEY}


async def _configure(
    api: AsyncClient, owner: Staff, **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {"credentials": CREDENTIALS, "enabled": True} | overrides
    response = await api.patch(PAYME, headers=headers_for(owner), json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()["data"]
    return data


# --- the registry -----------------------------------------------------------------


async def test_every_provider_the_release_supports_is_listed(
    api: AsyncClient, admin: Staff
) -> None:
    """A fresh installation has rows to switch on, not an empty list."""
    response = await api.get(PAYMENTS, headers=headers_for(admin))

    assert response.status_code == 200, response.text
    listed = response.json()["data"]
    assert [row["code"] for row in listed] == [
        code.value for code in PaymentProviderCode
    ]
    assert all(row["enabled"] is False for row in listed)
    assert all(row["credentials"] == {} for row in listed)


async def test_the_rows_are_only_created_once(
    api: AsyncClient, admin: Staff, session: AsyncSession
) -> None:
    await api.get(PAYMENTS, headers=headers_for(admin))
    await api.get(PAYMENTS, headers=headers_for(admin))

    rows = (await session.scalars(select(PaymentProvider))).all()
    assert len(rows) == len(PaymentProviderCode)


async def test_an_unknown_code_is_422(api: AsyncClient, admin: Staff) -> None:
    """The set is closed — a provider exists because an adapter does."""
    response = await api.get(f"{PAYMENTS}stripe/", headers=headers_for(admin))

    assert response.status_code == 422


async def test_there_is_no_test_endpoint_yet(api: AsyncClient, owner: Staff) -> None:
    """PHASES.md §2.13 — the probe lands with the adapter, in phase 2."""
    response = await api.post(f"{PAYME}test/", headers=headers_for(owner), json={})

    assert response.status_code == 404


# --- secrets ----------------------------------------------------------------------


async def test_the_credentials_never_come_back_readable(
    api: AsyncClient, owner: Staff, admin: Staff
) -> None:
    created = await _configure(api, owner)
    assert SECRET_KEY not in str(created)

    listed = await api.get(PAYMENTS, headers=headers_for(admin))
    one = await api.get(PAYME, headers=headers_for(admin))

    # The raw text, not the parsed field: a secret that leaked into an
    # unexpected key would slip past an assertion on data["credentials"].
    assert SECRET_KEY not in listed.text
    assert SECRET_KEY not in one.text
    assert one.json()["data"]["credentials"]["secret_key"].endswith("9c7")


async def test_the_credentials_are_encrypted_at_rest(
    api: AsyncClient, owner: Staff, session: AsyncSession
) -> None:
    await _configure(api, owner)

    row = await session.scalar(
        select(PaymentProvider).where(PaymentProvider.code == PaymentProviderCode.PAYME)
    )
    assert row is not None
    assert row.credentials is not None
    assert SECRET_KEY not in row.credentials
    assert row.key_version == 1


async def test_echoing_the_mask_back_does_not_overwrite_the_secret(
    api: AsyncClient, owner: Staff, session: AsyncSession
) -> None:
    """A panel that renders the GET and submits the form untouched would
    otherwise replace every credential with bullet characters."""
    await _configure(api, owner)
    masked = (await api.get(PAYME, headers=headers_for(owner))).json()["data"][
        "credentials"
    ]

    echoed = await api.patch(
        PAYME, headers=headers_for(owner), json={"credentials": masked}
    )
    assert echoed.status_code == 200

    kept = await service.payment_providers(session)
    assert kept[0].credentials["secret_key"] == SECRET_KEY


async def test_a_credential_is_replaced_and_merged_not_swapped_wholesale(
    api: AsyncClient, owner: Staff, session: AsyncSession
) -> None:
    await _configure(api, owner)

    await api.patch(
        PAYME,
        headers=headers_for(owner),
        json={"credentials": {"secret_key": "a-new-secret"}},
    )

    stored = (await service.payment_providers(session))[0].credentials
    assert stored["secret_key"] == "a-new-secret"
    # The key that was not mentioned survives — the panel only ever saw it
    # masked and could not have resent it.
    assert stored["merchant_id"] == "merchant-1234abcd"


async def test_a_null_clears_one_credential(
    api: AsyncClient, owner: Staff, session: AsyncSession
) -> None:
    await _configure(api, owner)

    await api.patch(
        PAYME, headers=headers_for(owner), json={"credentials": {"merchant_id": None}}
    )

    stored = (await service.payment_providers(session))[0].credentials
    assert "merchant_id" not in stored
    assert stored["secret_key"] == SECRET_KEY


async def test_the_seam_hides_its_secrets_in_a_repr(
    api: AsyncClient, owner: Staff, session: AsyncSession
) -> None:
    await _configure(api, owner)

    configured = (await service.payment_providers(session))[0]

    assert SECRET_KEY not in repr(configured)
    assert configured.credentials["secret_key"] == SECRET_KEY


async def test_the_journal_records_the_change_without_the_secret(
    api: AsyncClient, owner: Staff, database: AsyncEngine
) -> None:
    await _configure(api, owner)

    async with database.connect() as connection:
        rows = list(
            (
                await connection.execute(
                    select(AuditLog.changes).where(AuditLog.resource.like("integr%"))
                )
            ).all()
        )

    assert rows, "the mutation should have been journalled at all"
    assert SECRET_KEY not in str(rows)


# --- switching on -----------------------------------------------------------------


async def test_a_provider_cannot_be_switched_on_without_credentials(
    api: AsyncClient, owner: Staff
) -> None:
    """Otherwise the site offers a button that cannot take money."""
    response = await api.patch(
        PAYME, headers=headers_for(owner), json={"enabled": True}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "enabled"


async def test_the_seam_only_reports_providers_that_could_charge(
    api: AsyncClient, owner: Staff, session: AsyncSession
) -> None:
    await _configure(api, owner, enabled=False)
    assert await service.payment_providers(session) == []

    await api.patch(PAYME, headers=headers_for(owner), json={"enabled": True})
    ready = await service.payment_providers(session)

    assert [provider.code for provider in ready] == [PaymentProviderCode.PAYME]


# --- roles (API.md §5) ------------------------------------------------------------


@pytest.mark.parametrize("path", [PAYMENTS, PAYME])
async def test_admin_may_read(api: AsyncClient, admin: Staff, path: str) -> None:
    assert (await api.get(path, headers=headers_for(admin))).status_code == 200


async def test_admin_may_not_write(api: AsyncClient, admin: Staff) -> None:
    """Integratsiya kalitlari — owner ✎, admin 👁."""
    response = await api.patch(
        PAYME, headers=headers_for(admin), json={"title": "Mine now"}
    )

    assert response.status_code == 403
    assert response.json()["errors"][0]["code"] == "forbidden"


async def test_reading_requires_a_token(api: AsyncClient) -> None:
    assert (await api.get(PAYMENTS)).status_code == 401


# --- what the site and the panel see ----------------------------------------------


async def test_an_enabled_provider_reaches_site_config(
    api: AsyncClient, owner: Staff
) -> None:
    await _configure(api, owner, title="Payme")

    data = (await api.get(CONFIG)).json()["data"]

    assert data["payment_methods"] == [
        {
            "code": "payme",
            "title": "Payme",
            "logo_url": None,
        }
    ]


async def test_switching_a_provider_off_removes_it_from_site_config(
    api: AsyncClient, owner: Staff
) -> None:
    await _configure(api, owner)
    await api.get(CONFIG)

    await api.patch(PAYME, headers=headers_for(owner), json={"enabled": False})

    assert (await api.get(CONFIG)).json()["data"]["payment_methods"] == []


async def test_a_payments_change_purges_the_site_config_cache(
    api: AsyncClient, owner: Staff
) -> None:
    """ "Change it in the panel, see it on the site" is the phase-1 criterion,
    and the cache is the only thing between the two."""
    await api.get(CONFIG)
    assert await cache.read() is not None

    await _configure(api, owner)

    assert await cache.read() is None


async def test_the_site_config_etag_changes_with_the_methods(
    api: AsyncClient, owner: Staff
) -> None:
    before = (await api.get(CONFIG)).headers["etag"]

    await _configure(api, owner)

    assert (await api.get(CONFIG)).headers["etag"] != before


async def test_health_reports_payments_once_one_is_ready(
    api: AsyncClient, owner: Staff
) -> None:
    unconfigured = (await api.get(HEALTH, headers=headers_for(owner))).json()["data"]
    assert unconfigured["components"]["payments"]["status"] == "not_configured"

    await _configure(api, owner)

    ready = (await api.get(HEALTH, headers=headers_for(owner))).json()["data"]
    assert ready["components"]["payments"]["status"] == "ok"
    # Configured-ness, not liveness: nothing was contacted, so there is no
    # latency to report (API.md §39, and the docstring in system/service.py).
    assert ready["components"]["payments"]["latency_ms"] is None
