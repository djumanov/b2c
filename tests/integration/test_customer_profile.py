"""The customer's own details — API.md §19.

Three things here are decisions rather than plumbing, and each has a test that
fails loudly if somebody relaxes it: ``email`` is refused rather than ignored,
deleting an account empties the row rather than only hiding it, and
``avatar_id`` is a code this side never interprets.
"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from tests.integration.conftest import PASSWORD, customer_headers_for

PROFILE = "/api/v1/public/profile/"
PASSWORD_URL = "/api/v1/public/profile/password/"
LOGIN = "/api/v1/public/auth/login/"
REGISTER = "/api/v1/public/auth/register/"


# --- reading and editing ------------------------------------------------------------


async def test_the_profile_reports_the_signed_in_customer(
    api: AsyncClient, customer: Customer
) -> None:
    response = await api.get(PROFILE, headers=customer_headers_for(customer))

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["id"] == str(customer.id)
    assert data["email"] == customer.email
    assert data["avatar_id"] is None
    assert "avatar_url" not in data


async def test_the_profile_needs_a_token(api: AsyncClient) -> None:
    assert (await api.get(PROFILE)).status_code == 401


async def test_patch_changes_the_fields_it_is_given(
    api: AsyncClient, customer: Customer
) -> None:
    response = await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"last_name": "Karimov", "birth_date": "1995-04-17"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["last_name"] == "Karimov"
    assert data["birth_date"] == "1995-04-17"
    # Untouched fields stay as they were — PATCH is partial (API.md §8).
    assert data["first_name"] == customer.first_name


async def test_the_profile_says_when_it_is_not_finished_yet(
    api: AsyncClient, customer: Customer
) -> None:
    """The fixture account has a first name and nothing else, which is what a
    real one looks like after registration (API.md §18). The flag flips on the
    field that completes the set, not before it."""
    headers = customer_headers_for(customer)
    assert (await api.get(PROFILE, headers=headers)).json()["data"][
        "is_profile_complete"
    ] is False

    partial = await api.patch(
        PROFILE,
        headers=headers,
        json={"last_name": "Karimov", "middle_name": "Baxtiyorovich"},
    )
    assert partial.json()["data"]["is_profile_complete"] is False

    finished = await api.patch(
        PROFILE,
        headers=headers,
        json={"phone": "+998901234567", "birth_date": "1995-04-17"},
    )
    assert finished.status_code == 200, finished.text
    assert finished.json()["data"]["middle_name"] == "Baxtiyorovich"
    assert finished.json()["data"]["is_profile_complete"] is True


async def test_clearing_a_field_makes_the_profile_incomplete_again(
    api: AsyncClient, customer: Customer
) -> None:
    """Every customer column is nullable, so ``null`` is how somebody removes a
    phone number they no longer want stored — and the flag has to follow it
    back down rather than latch."""
    headers = customer_headers_for(customer)
    await api.patch(
        PROFILE,
        headers=headers,
        json={
            "last_name": "Karimov",
            "middle_name": "Baxtiyorovich",
            "phone": "+998901234567",
            "birth_date": "1995-04-17",
        },
    )

    cleared = await api.patch(PROFILE, headers=headers, json={"phone": None})

    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["phone"] is None
    assert cleared.json()["data"]["is_profile_complete"] is False


async def test_the_completeness_flag_cannot_be_sent(
    api: AsyncClient, customer: Customer
) -> None:
    """It is derived, so writing it is meaningless — and accepting it silently
    would let a client tell itself the profile was finished."""
    response = await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"is_profile_complete": True},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "is_profile_complete"


async def test_changing_the_email_is_refused_not_ignored(
    api: AsyncClient, customer: Customer
) -> None:
    """The address is what the OTP proved and what password reset trusts.

    Silently dropping it would be worse than refusing it: the client would show
    an address as changed that never changed.
    """
    response = await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"email": "someone-else@example.uz"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "validation"

    unchanged = await api.get(PROFILE, headers=customer_headers_for(customer))
    assert unchanged.json()["data"]["email"] == customer.email


# --- password -----------------------------------------------------------------------


async def test_changing_the_password_ends_every_session(
    api: AsyncClient, customer: Customer
) -> None:
    signed_in = (
        await api.post(LOGIN, json={"login": customer.email, "password": PASSWORD})
    ).json()["data"]

    changed = await api.post(
        PASSWORD_URL,
        headers={"Authorization": f"Bearer {signed_in['access_token']}"},
        json={"current_password": PASSWORD, "new_password": "a-brand-new-secret"},
    )
    assert changed.status_code == 204

    replay = await api.post(
        "/api/v1/public/auth/refresh/",
        json={"refresh_token": signed_in["refresh_token"]},
    )
    assert replay.status_code == 401

    signed_in_again = await api.post(
        LOGIN, json={"login": customer.email, "password": "a-brand-new-secret"}
    )
    assert signed_in_again.status_code == 200


async def test_a_wrong_current_password_is_422_not_401(
    api: AsyncClient, customer: Customer
) -> None:
    """The caller is authenticated; it is the field that is wrong."""
    response = await api.post(
        PASSWORD_URL,
        headers=customer_headers_for(customer),
        json={"current_password": "not-it", "new_password": "a-brand-new-secret"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "current_password"


# --- avatar (a code, not a file — API.md §19) ---------------------------------------


async def test_the_avatar_code_is_stored_and_returned_unchanged(
    api: AsyncClient, customer: Customer
) -> None:
    """Stored verbatim: the set of pictures is the client's, so this side has
    nothing to resolve the code against and nothing to rewrite it into."""
    response = await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"avatar_id": "avatar-07"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["avatar_id"] == "avatar-07"

    profile = await api.get(PROFILE, headers=customer_headers_for(customer))
    assert profile.json()["data"]["avatar_id"] == "avatar-07"


async def test_a_code_this_side_does_not_know_is_still_accepted(
    api: AsyncClient, customer: Customer
) -> None:
    """There is no allowed list on the server and this is the test that says
    so: adding a picture to the app must not require a backend deploy, and the
    two clients need not even ship the same set."""
    response = await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"avatar_id": "something-only-the-app-knows"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["avatar_id"] == "something-only-the-app-knows"


async def test_null_clears_the_avatar(api: AsyncClient, customer: Customer) -> None:
    """No endpoint of its own to clear it — it is a column like the rest."""
    await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"avatar_id": "avatar-07"},
    )

    response = await api.patch(
        PROFILE, headers=customer_headers_for(customer), json={"avatar_id": None}
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["avatar_id"] is None


async def test_an_overlong_avatar_code_is_refused(
    api: AsyncClient, customer: Customer
) -> None:
    """The length is the only thing checked, so it is the only thing that can
    come back as a 422 here."""
    response = await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"avatar_id": "x" * 65},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "avatar_id"


async def test_the_avatar_is_not_uploaded(api: AsyncClient, customer: Customer) -> None:
    """The upload endpoints are gone, not merely unused (API.md §11, §19): the
    public surface accepts no files at all."""
    headers = customer_headers_for(customer)
    gone = f"{PROFILE}avatar/"

    assert (await api.post(gone, headers=headers)).status_code == 404
    assert (await api.delete(gone, headers=headers)).status_code == 404


# --- deleting the account ---------------------------------------------------------


async def test_deleting_the_account_empties_the_row(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    """PROJECT.md §13 says personal data is *cleared*. A soft delete conceals a
    row without emptying it, so both happen."""
    # Filled in first, or the assertions below would pass on a column the
    # fixture never wrote.
    await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={
            "middle_name": "Baxtiyorovich",
            "phone": "+998901234567",
            "avatar_id": "avatar-07",
        },
    )

    response = await api.request(
        "DELETE",
        PROFILE,
        headers=customer_headers_for(customer),
        json={"password": PASSWORD},
    )
    assert response.status_code == 204

    await session.refresh(customer)
    assert customer.is_deleted
    assert customer.email != "buyer@example.uz"
    assert customer.middle_name is None
    assert customer.phone is None
    assert customer.birth_date is None
    assert customer.avatar_id is None
    # No hash any password can verify against.
    assert customer.password_hash == ""


async def test_a_deleted_account_loses_access_at_once(
    api: AsyncClient, customer: Customer
) -> None:
    headers = customer_headers_for(customer)
    await api.request("DELETE", PROFILE, headers=headers, json={"password": PASSWORD})

    assert (await api.get(PROFILE, headers=headers)).status_code == 401


async def test_the_address_can_be_registered_again_afterwards(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    """The unique index covers live rows only, and the address is emptied as
    well — so deleting an account does not put it out of reach forever."""
    address = customer.email
    await api.request(
        "DELETE",
        PROFILE,
        headers=customer_headers_for(customer),
        json={"password": PASSWORD},
    )

    response = await api.post(
        REGISTER,
        json={"email": address, "password": PASSWORD, "first_name": "Somebody"},
    )

    assert response.status_code == 204
    rows = await session.scalars(
        select(Customer)
        .where(Customer.email == address)
        .where(Customer.deleted_at.is_(None))
    )
    assert len(rows.all()) == 1


async def test_deleting_needs_the_password(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    """The one irreversible thing a customer token can do, so holding the token
    is not enough (API.md §19)."""
    response = await api.request(
        "DELETE",
        PROFILE,
        headers=customer_headers_for(customer),
        json={"password": "not-it"},
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "password"
    await session.refresh(customer)
    assert not customer.is_deleted
