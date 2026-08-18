"""``save_passenger`` — the field that was documented for a release and unread.

API.md §19 promised a booking could put its travellers on the customer's saved
list, and nothing in the code ever read the flag. This file is what makes the
promise true, and what keeps it true.

Two things are worth stating about the design, because both look like
omissions:

* **The travellers come from the order, not from the request.** They are read
  back in our own shape (``orders.travelers``), so this path is the same for
  every vertical and does not learn GTS's flight spelling.
* **Nothing here may fail a booking.** By the time it runs a seat is held and
  the customer is on their way to paying. Every case below that goes wrong ends
  with a ``200`` and an order.
"""

from datetime import date
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.modules.customers import service as customers_service
from app.modules.customers.models import Customer, Passenger
from app.modules.integrations.models import GtsCredential
from app.modules.settings import cache as settings_cache
from tests.integration.conftest import customer_headers_for

BOOKING = "/api/v1/public/flight/booking/"
GTS = "https://gts.test"

TRAVELER: dict[str, Any] = {
    "type": "ADT",
    "gender": "M",
    "first_name": "Azimjon",
    "last_name": "Yusufov",
    "middle_name": "Kamoliddin",
    "birth_date": "2002-12-20",
    "citizenship": "UZ",
    "document": {
        "type": "PSP",
        "number": "FA2145157",
        "issue_date": "2019-05-30",
        "expire_date": "2029-05-29",
    },
    "email": "azimjon@example.uz",
    "phone": {"phone_code": "998", "phone_number": "901234567"},
}


def _body(*travelers: dict[str, Any], save: bool | None = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "request_id": "r-1",
        "offer_id": "o-1",
        "passengers": list(travelers) or [TRAVELER],
    }
    if save is not None:
        body["save_passenger"] = save
    return body


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "OK", "code": 0, "data": data}


def _headers(customer: Customer, key: str = "idem-1") -> dict[str, str]:
    return {**customer_headers_for(customer), "Idempotency-Key": key}


async def _installation(session: AsyncSession) -> None:
    """An active credential and a vertical that is on sale."""
    ciphertext, key_version = encrypt("gts-secret-1a2b")
    session.add(
        GtsCredential(
            label="Prod agent",
            base_url=GTS,
            email="agent@brand.uz",
            password=ciphertext,
            key_version=key_version,
            is_active=True,
        )
    )
    await session.commit()
    await settings_cache.write({"products": [{"code": "flight", "enabled": True}]})
    respx.post(f"{GTS}/v1/auth/signin/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                {
                    "session_key": "session-abc",
                    "token": "tok-abc",
                    "timeout_minutes": 360.0,
                }
            ),
        )
    )


def _mock_booking(
    passengers: list[dict[str, Any]] | None = None,
    *,
    numbers: tuple[int, ...] = (1250,),
) -> respx.Route:
    """GTS confirms, and echoes the travellers back the way it spells them.

    ``numbers`` is one order number per call: ``orders`` has a unique index on
    the provider's number, so a test that books twice must be answered twice.
    """
    answers = []
    for number in numbers:
        order: dict[str, Any] = {
            "order_number": number,
            "status": "BO",
            "price_info": {"price": 100, "currency": "UZS"},
        }
        if passengers is not None:
            order["passengers"] = passengers
        answers.append(httpx.Response(200, json=_envelope({"data": order})))
    return respx.post(f"{GTS}/v1/content/booking/").mock(side_effect=answers)


def _mock_catalog() -> None:
    """The §26 lists, so the saved passenger gets whole objects rather than codes."""
    respx.get(f"{GTS}/static/country").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                [
                    {"code": "UZ", "country_eng": "Uzbekistan", "emoji": "🇺🇿"},
                    {"code": "TR", "country_eng": "Türkiye", "emoji": "🇹🇷"},
                ]
            ),
        )
    )
    respx.get(f"{GTS}/static/typedocument").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                [
                    {"type": "PSP", "name": "Passport"},
                    {"type": "NP", "name": "National passport"},
                ]
            ),
        )
    )


async def _saved(session: AsyncSession, customer: Customer) -> list[Passenger]:
    rows = await session.scalars(
        select(Passenger)
        .where(Passenger.customer_id == customer.id, Passenger.deleted_at.is_(None))
        .order_by(Passenger.created_at)
    )
    return list(rows.all())


# --- the promise --------------------------------------------------------------------


@respx.mock
async def test_a_booking_that_asks_saves_its_travellers(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    await _installation(session)
    _mock_booking()
    _mock_catalog()

    response = await api.post(BOOKING, json=_body(), headers=_headers(customer))

    assert response.status_code == 200
    saved = await _saved(session, customer)
    assert len(saved) == 1
    person = saved[0]
    assert person.first_name == "Azimjon"
    assert person.last_name == "Yusufov"
    assert person.middle_name == "Kamoliddin"
    assert person.birth_date.isoformat() == "2002-12-20"
    assert person.document_number == "FA2145157"
    assert person.document_expiry_date is not None
    assert person.document_expiry_date.isoformat() == "2029-05-29"


@respx.mock
async def test_the_catalogue_objects_are_stored_whole_not_as_codes(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """§19 stores the §26 object, because the UI shows a flag and a translated
    name that ``"UZ"`` cannot carry. A booking only ever names the code, so the
    code is looked up on the way in."""
    await _installation(session)
    _mock_booking()
    _mock_catalog()

    await api.post(BOOKING, json=_body(), headers=_headers(customer))

    person = (await _saved(session, customer))[0]
    assert person.citizenship == {
        "code": "UZ",
        "country_eng": "Uzbekistan",
        "emoji": "🇺🇿",
    }
    assert person.document_type == {"type": "PSP", "name": "Passport"}


@respx.mock
async def test_the_travellers_are_read_from_the_answer_not_the_request(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """GTS is entitled to correct what it was sent — a name transliterated, a
    document normalised. What was booked is what gets saved."""
    await _installation(session)
    _mock_catalog()
    _mock_booking(
        [
            {
                "firstname": "AZIMJON",
                "lastname": "YUSUFOV",
                "birth_date": "2002-12-20",
                "document": {"type": "PSP", "passport_number": "FA9999999"},
            }
        ]
    )

    await api.post(BOOKING, json=_body(), headers=_headers(customer))

    person = (await _saved(session, customer))[0]
    assert person.first_name == "AZIMJON"
    assert person.document_number == "FA9999999"


@respx.mock
async def test_several_travellers_all_land(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    await _installation(session)
    _mock_booking()
    _mock_catalog()
    second = {
        **TRAVELER,
        "first_name": "Nilufar",
        "document": {**TRAVELER["document"], "number": "FA7777777"},
    }

    await api.post(BOOKING, json=_body(TRAVELER, second), headers=_headers(customer))

    assert len(await _saved(session, customer)) == 2


# --- when nothing should be saved ----------------------------------------------------


@respx.mock
@pytest.mark.parametrize("save", [False, None])
async def test_a_booking_that_does_not_ask_saves_nobody(
    api: AsyncClient, session: AsyncSession, customer: Customer, save: bool | None
) -> None:
    """``false`` and "the field was not sent" are the same answer."""
    await _installation(session)
    _mock_booking()
    _mock_catalog()

    await api.post(BOOKING, json=_body(save=save), headers=_headers(customer))

    assert await _saved(session, customer) == []


@respx.mock
async def test_a_string_is_not_a_yes(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """The contract says boolean and Swagger says boolean. Guessing at
    ``"true"`` would be inventing a second contract nobody documented."""
    await _installation(session)
    _mock_booking()
    _mock_catalog()

    body = {**_body(), "save_passenger": "true"}
    await api.post(BOOKING, json=body, headers=_headers(customer))

    assert await _saved(session, customer) == []


@respx.mock
async def test_a_refused_booking_saves_nobody(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """No seat, no traveller worth remembering."""
    await _installation(session)
    _mock_catalog()
    respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200, json={"status": "error", "message": "no seats", "code": -1}
        )
    )

    response = await api.post(BOOKING, json=_body(), headers=_headers(customer))

    assert response.status_code == 502
    assert await _saved(session, customer) == []


@respx.mock
async def test_a_traveller_with_no_birth_date_is_skipped_not_guessed_at(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """``Passenger.birth_date`` has no NULL, and API.md §19 says a passenger
    without one only *looks* saved. The rest of the booking is unaffected."""
    await _installation(session)
    _mock_catalog()
    _mock_booking(
        [
            {"firstname": "Azimjon", "lastname": "Yusufov"},
            {
                "firstname": "Nilufar",
                "lastname": "Yusufova",
                "birth_date": "1999-01-02",
            },
        ]
    )

    response = await api.post(BOOKING, json=_body(), headers=_headers(customer))

    assert response.status_code == 200
    saved = await _saved(session, customer)
    assert [person.first_name for person in saved] == ["Nilufar"]


# --- not twice ------------------------------------------------------------------------


@respx.mock
async def test_booking_the_same_person_twice_saves_one_row(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """The whole point is to save retyping. Doing it once per booking would
    turn the list into a log."""
    await _installation(session)
    _mock_booking(numbers=(1250, 1251))
    _mock_catalog()

    await api.post(BOOKING, json=_body(), headers=_headers(customer, "idem-1"))
    await api.post(BOOKING, json=_body(), headers=_headers(customer, "idem-2"))

    assert len(await _saved(session, customer)) == 1


@respx.mock
async def test_a_renewed_passport_is_a_second_row(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """``Passenger`` says so itself: the same person appears twice, once with
    the expired document and once with the new one."""
    await _installation(session)
    _mock_booking(numbers=(1250, 1251))
    _mock_catalog()
    renewed = {**TRAVELER, "document": {**TRAVELER["document"], "number": "FB0000001"}}

    await api.post(BOOKING, json=_body(), headers=_headers(customer, "idem-1"))
    await api.post(BOOKING, json=_body(renewed), headers=_headers(customer, "idem-2"))

    assert {person.document_number for person in await _saved(session, customer)} == {
        "FA2145157",
        "FB0000001",
    }


@respx.mock
async def test_a_traveller_already_on_the_list_is_not_added_again(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """Case is not identity — a booking form does not agree with itself about
    capitals, and ``ALI`` must not become a second ``Ali``."""
    await _installation(session)
    _mock_booking()
    _mock_catalog()
    session.add(
        Passenger(
            customer_id=customer.id,
            first_name="AZIMJON",
            last_name="yusufov",
            birth_date=date(2002, 12, 20),
            document_number="fa2145157",
        )
    )
    await session.commit()

    await api.post(BOOKING, json=_body(), headers=_headers(customer))

    assert len(await _saved(session, customer)) == 1


# --- nothing here may cost a booking --------------------------------------------------


@respx.mock
async def test_an_unreachable_catalogue_still_saves_the_passenger(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    """The catalogue is two calls to GTS. A passenger holding the bare code is
    something the customer can finish by hand; no passenger is not."""
    await _installation(session)
    _mock_booking()
    respx.get(f"{GTS}/static/country").mock(side_effect=httpx.ConnectError("down"))
    respx.get(f"{GTS}/static/typedocument").mock(side_effect=httpx.ConnectError("down"))

    response = await api.post(BOOKING, json=_body(), headers=_headers(customer))

    assert response.status_code == 200
    person = (await _saved(session, customer))[0]
    assert person.citizenship == {"code": "UZ"}
    assert person.document_type == {"type": "PSP"}


@respx.mock
async def test_a_failure_while_saving_does_not_cost_the_booking(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seat is held and the money is next. Whatever happens on this path,
    the customer gets their order."""
    await _installation(session)
    _mock_booking()
    _mock_catalog()

    async def explode(*args: Any, **kwargs: Any) -> int:
        raise RuntimeError("the passengers table is on fire")

    monkeypatch.setattr(customers_service, "_write_travelers", explode)

    response = await api.post(BOOKING, json=_body(), headers=_headers(customer))

    assert response.status_code == 200
    assert response.json()["data"]["order"]["status"] == "booked"
    assert await _saved(session, customer) == []
