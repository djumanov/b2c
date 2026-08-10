"""The customer's own details — API.md §19.

Three things here are decisions rather than plumbing, and each has a test that
fails loudly if somebody relaxes it: ``email`` is refused rather than ignored,
deleting an account empties the row rather than only hiding it, and the avatar
that gets replaced is released rather than destroyed.
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customers.models import Customer
from app.modules.uploads.models import Upload
from tests.integration.conftest import (
    PASSWORD,
    customer_headers_for,
    make_customer,
)

PROFILE = "/api/v1/public/profile/"
AVATAR = "/api/v1/public/profile/avatar/"
PASSWORD_URL = "/api/v1/public/profile/password/"
LOGIN = "/api/v1/public/auth/login/"
REGISTER = "/api/v1/public/auth/register/"

#: A real 1x1 PNG — the signature check is part of what is under test, so
#: ``b"fake"`` would be rejected for the wrong reason.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000050001a5f645ee0000000049454e44ae426082"
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


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
    assert data["avatar_url"] is None


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


# --- avatar -------------------------------------------------------------------------


async def _upload_avatar(
    api: AsyncClient,
    customer: Customer,
    *,
    content: bytes = PNG,
    filename: str = "me.png",
    mime: str = "image/png",
) -> tuple[int, dict[str, object]]:
    response = await api.post(
        AVATAR,
        headers=customer_headers_for(customer),
        files={"file": (filename, content, mime)},
    )
    return response.status_code, response.json()


async def test_an_avatar_is_uploaded_and_reported_on_the_profile(
    api: AsyncClient, customer: Customer
) -> None:
    status, body = await _upload_avatar(api, customer)

    assert status == 200, body
    data = body["data"]
    assert isinstance(data, dict)
    assert data["avatar_id"] is not None
    # A public purpose, so the URL is the unauthenticated one.
    assert str(data["avatar_url"]).startswith("/uploads/avatar/")


async def test_the_avatar_is_readable_without_a_token(
    api: AsyncClient, customer: Customer
) -> None:
    """It is `public` precisely so its owner can fetch it — the private file
    route is guarded by a staff token."""
    _, body = await _upload_avatar(api, customer)
    url = str(body["data"]["avatar_url"])  # type: ignore[index]

    response = await api.get(url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


async def test_replacing_an_avatar_releases_the_old_one(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    """Released, not deleted — the sweep collects it after the grace period, so
    a customer who changes their mind within the day has not lost it."""
    _, first = await _upload_avatar(api, customer)
    first_id = uuid.UUID(str(first["data"]["avatar_id"]))  # type: ignore[index]

    _, second = await _upload_avatar(api, customer, filename="new.png")
    second_id = uuid.UUID(str(second["data"]["avatar_id"]))  # type: ignore[index]
    assert second_id != first_id

    released = await session.scalar(select(Upload).where(Upload.id == first_id))
    kept = await session.scalar(select(Upload).where(Upload.id == second_id))
    assert released is not None and released.linked_at is None
    assert kept is not None and kept.linked_at is not None


async def test_deleting_the_avatar_clears_it(
    api: AsyncClient, customer: Customer
) -> None:
    await _upload_avatar(api, customer)

    removed = await api.delete(AVATAR, headers=customer_headers_for(customer))
    assert removed.status_code == 204

    profile = await api.get(PROFILE, headers=customer_headers_for(customer))
    assert profile.json()["data"]["avatar_id"] is None


async def test_an_svg_avatar_is_refused(api: AsyncClient, customer: Customer) -> None:
    """SVG is XML from an anonymous uploader, and the signature check cannot
    see inside one — so this purpose takes raster only."""
    status, body = await _upload_avatar(
        api, customer, content=SVG, filename="me.svg", mime="image/svg+xml"
    )

    assert status == 422
    assert body["errors"][0]["field"] == "file"  # type: ignore[index]


async def test_an_oversized_avatar_is_refused(
    api: AsyncClient, customer: Customer
) -> None:
    status, body = await _upload_avatar(
        api, customer, content=PNG + b"\x00" * 2_000_000
    )

    assert status == 422
    assert body["errors"][0]["field"] == "file"  # type: ignore[index]


# --- deleting the account ---------------------------------------------------------


async def test_deleting_the_account_empties_the_row(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    """PROJECT.md §13 says personal data is *cleared*. A soft delete conceals a
    row without emptying it, so both happen."""
    await _upload_avatar(api, customer)
    # Filled in first, or the assertions below would pass on a column the
    # fixture never wrote.
    await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"middle_name": "Baxtiyorovich", "phone": "+998901234567"},
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


async def test_another_customers_avatar_is_not_reachable_through_the_profile(
    api: AsyncClient, customer: Customer, session: AsyncSession
) -> None:
    """There is no id to pass — the avatar is set by uploading, never by
    naming an existing file, so one account cannot claim another's."""
    other = await make_customer(session, email="other@example.uz")
    _, body = await _upload_avatar(api, other)
    other_avatar = body["data"]["avatar_id"]  # type: ignore[index]

    response = await api.patch(
        PROFILE,
        headers=customer_headers_for(customer),
        json={"avatar_id": other_avatar},
    )

    assert response.status_code == 422
