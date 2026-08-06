"""Signing in with a provider — API.md §18 and §29.

The verifier is swapped for a stand-in through the same seam the real one uses,
so nothing here talks to Google. What is under test is the flow: which account a
token lands on, and which tokens are refused.

The claim worth pinning is that a provider-verified address counts as verified
here. The OTP in the email flow exists to prove somebody controls an address;
Google saying ``email_verified`` proves the same thing, so requiring a code on
top of it would ask for the same proof twice.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.staff.models import Staff
from app.providers.social import set_verifier
from app.providers.social.base import (
    SocialAuthError,
    SocialIdentity,
    SocialProviderCode,
)
from tests.integration.conftest import headers_for, make_customer

GOOGLE = "/api/v1/public/auth/social/google/"
SOCIAL = "/api/v1/admin/integrations/social/"
PROFILE = "/api/v1/public/profile/"

EMAIL = "signed-in@example.uz"


@dataclass
class StandInVerifier:
    """Answers with whatever the test set, or refuses."""

    code: SocialProviderCode = SocialProviderCode.GOOGLE
    identity: SocialIdentity | None = field(
        default_factory=lambda: SocialIdentity(
            subject="google-subject-1",
            email=EMAIL,
            email_verified=True,
            first_name="Aziz",
            last_name="Karimov",
        )
    )

    async def verify(self, token: str) -> SocialIdentity:
        if self.identity is None:
            raise SocialAuthError("refused by the stand-in")
        return self.identity


@pytest.fixture
def verifier() -> Iterator[StandInVerifier]:
    stand_in = StandInVerifier()
    set_verifier(stand_in)
    yield stand_in
    set_verifier(None)


async def _sign_in(api: AsyncClient) -> tuple[int, dict]:
    response = await api.post(GOOGLE, json={"id_token": "a-token"})
    return response.status_code, response.json()


# --- the flow ---------------------------------------------------------------------


async def test_a_new_customer_is_created_and_already_verified(
    api: AsyncClient, verifier: StandInVerifier, session: AsyncSession
) -> None:
    status, body = await _sign_in(api)

    assert status == 200, body
    assert body["data"]["expires_in"] == 30 * 60

    created = await session.scalar(select(Customer).where(Customer.email == EMAIL))
    assert created is not None
    assert created.is_verified
    assert created.first_name == "Aziz"
    # No password was set, and no hash can be guessed into existence.
    assert created.password_hash == ""


async def test_the_new_account_can_use_the_public_surface_at_once(
    api: AsyncClient, verifier: StandInVerifier
) -> None:
    """Verified means verified — ``current_customer`` lets it through."""
    _, body = await _sign_in(api)
    access = body["data"]["access_token"]

    profile = await api.get(PROFILE, headers={"Authorization": f"Bearer {access}"})

    assert profile.status_code == 200
    assert profile.json()["data"]["email"] == EMAIL


async def test_signing_in_twice_uses_the_same_account(
    api: AsyncClient, verifier: StandInVerifier, session: AsyncSession
) -> None:
    await _sign_in(api)
    await _sign_in(api)

    rows = (
        await session.scalars(select(Customer).where(Customer.email == EMAIL))
    ).all()
    assert len(rows) == 1


async def test_an_existing_password_account_is_signed_into(
    api: AsyncClient, verifier: StandInVerifier, session: AsyncSession
) -> None:
    existing = await make_customer(session, email=EMAIL, first_name="Already Here")

    status, _ = await _sign_in(api)

    assert status == 200
    rows = (
        await session.scalars(select(Customer).where(Customer.email == EMAIL))
    ).all()
    assert [row.id for row in rows] == [existing.id]
    # The provider does not get to rename somebody's existing account.
    await session.refresh(existing)
    assert existing.first_name == "Already Here"


async def test_a_pending_registration_is_confirmed_rather_than_duplicated(
    api: AsyncClient, verifier: StandInVerifier, session: AsyncSession
) -> None:
    """The address was typed here first and never confirmed; the provider has
    now confirmed it, so the waiting row becomes the account."""
    pending = await make_customer(session, email=EMAIL, is_verified=False)

    status, _ = await _sign_in(api)

    assert status == 200
    await session.refresh(pending)
    assert pending.is_verified
    rows = (
        await session.scalars(select(Customer).where(Customer.email == EMAIL))
    ).all()
    assert len(rows) == 1


async def test_a_blocked_account_is_still_refused(
    api: AsyncClient, verifier: StandInVerifier, session: AsyncSession
) -> None:
    """Signing in a different way is not a way around being blocked."""
    await make_customer(session, email=EMAIL, is_blocked=True)

    status, body = await _sign_in(api)

    assert status == 403
    assert body["errors"][0]["code"] == "forbidden"


# --- tokens that are refused ------------------------------------------------------


async def test_a_token_the_provider_rejects_is_401(
    api: AsyncClient, verifier: StandInVerifier
) -> None:
    verifier.identity = None

    status, body = await _sign_in(api)

    assert status == 401
    assert body["errors"][0]["code"] == "unauthorized"


async def test_an_unverified_address_is_refused(
    api: AsyncClient, verifier: StandInVerifier, session: AsyncSession
) -> None:
    """An unverified address on a profile is a claim anybody can make about
    somebody else's mailbox — accepting it would hand over their account."""
    verifier.identity = SocialIdentity(
        subject="google-subject-1", email=EMAIL, email_verified=False
    )

    status, _ = await _sign_in(api)

    assert status == 401
    assert await session.scalar(select(Customer).where(Customer.email == EMAIL)) is None


async def test_a_rejected_token_and_an_unverified_one_read_the_same(
    api: AsyncClient, verifier: StandInVerifier
) -> None:
    verifier.identity = None
    refused = (await api.post(GOOGLE, json={"id_token": "a-token"})).json()

    verifier.identity = SocialIdentity(subject="s", email=EMAIL, email_verified=False)
    unverified = (await api.post(GOOGLE, json={"id_token": "a-token"})).json()

    assert refused["errors"] == unverified["errors"]


async def test_an_unknown_provider_reads_like_a_switched_off_one(
    api: AsyncClient, verifier: StandInVerifier
) -> None:
    """404 and not 422: a 422 would describe our provider list to an anonymous
    caller, and "we do not support that" is not different, from outside, from
    "this installation switched it off"."""
    response = await api.post(
        "/api/v1/public/auth/social/facebook/", json={"id_token": "a-token"}
    )

    assert response.status_code == 404


# --- configuration (API.md §29) ---------------------------------------------------


async def test_a_provider_that_is_not_configured_is_404(api: AsyncClient) -> None:
    """No override installed, and nothing in the database — "there is no such
    sign-in here", the shape a switched-off section uses."""
    response = await api.post(GOOGLE, json={"id_token": "a-token"})

    assert response.status_code == 404


async def test_the_panel_lists_every_provider_with_the_secret_masked(
    api: AsyncClient, owner: Staff
) -> None:
    configured = await api.patch(
        f"{SOCIAL}google/",
        headers=headers_for(owner),
        json={
            "client_id": "1234.apps.googleusercontent.com",
            "client_secret": "google-secret-8b31d5",
            "enabled": True,
        },
    )
    assert configured.status_code == 200, configured.text

    listed = await api.get(SOCIAL, headers=headers_for(owner))

    assert "google-secret-8b31d5" not in listed.text
    row = listed.json()["data"][0]
    # The client id is published to every browser that draws the button, so
    # hiding it in the panel would cost the operator the one value they can
    # check against the provider's console.
    assert row["client_id"] == "1234.apps.googleusercontent.com"
    assert set(row["client_secret"]) == {"•"}


async def test_a_provider_cannot_be_switched_on_without_a_client_id(
    api: AsyncClient, owner: Staff
) -> None:
    response = await api.patch(
        f"{SOCIAL}google/", headers=headers_for(owner), json={"enabled": True}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "enabled"


async def test_admin_may_read_but_not_write(api: AsyncClient, admin: Staff) -> None:
    assert (await api.get(SOCIAL, headers=headers_for(admin))).status_code == 200

    response = await api.patch(
        f"{SOCIAL}google/", headers=headers_for(admin), json={"enabled": False}
    )

    assert response.status_code == 403
