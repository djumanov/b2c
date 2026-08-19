"""``/public/orders/`` and the order row behind it — API.md §20, §21.

Two claims, and they are the whole module:

* **A booking leaves a record**, filed under the customer who made it, in a
  status that is *ours* — and holding *no* passenger from the request, which is
  a promise about passport numbers (PROJECT.md §13), so it is checked against
  the raw row rather than the API's view of it.
* **The record is what ownership means.** Someone else's order is a 404 on
  every path, and cancelling refuses one without ever calling GTS.

``tests/contract/test_search_passthrough.py`` is the other half of this: it
holds the same two steps to writing nothing *else* — no offer, no search state
(D2). Here the database is real and the question is what the row says.

The state machine itself is ``test_order_transitions.py``'s subject; what is
checked here is that the two write paths go through it.
"""

import datetime as dt
import json as jsonlib
import uuid
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import encrypt
from app.modules.customers.models import Customer
from app.modules.integrations.models import GtsCredential
from app.modules.orders import service as orders_service
from app.modules.orders.models import Order
from app.modules.orders.states import OrderStatus
from app.modules.settings import cache as settings_cache
from tests.integration.conftest import customer_headers_for, make_customer

ORDERS = "/api/v1/public/orders/"
BOOKING = "/api/v1/public/flight/booking/"
CANCEL = "/api/v1/public/flight/cancel/"
GTS = "https://gts.test"

BOOKING_BODY: dict[str, Any] = {
    "request_id": "r-1",
    "offer_id": "o-1",
    "passengers": [
        {
            "type": "ADT",
            "gender": "M",
            "first_name": "Azimjon",
            "last_name": "Yusufov",
            "birth_date": "2002-12-20",
            "citizenship": "UZ",
            "document": {
                "type": "PSP",
                "number": "FA2145157",
                "issue_date": "2019-05-30",
                "expire_date": "2029-05-29",
            },
            "email": "yusufovazimjon@gmail.com",
            "phone": {"phone_code": "998", "phone_number": "998328192"},
        }
    ],
    "save_passenger": True,
}

#: GTS's booking answer, in the shape the EASY_GATEWAY collection recorded:
#: our client strips GTS's envelope, so what lands here is the wrapper, and the
#: order's own fields sit one level down under a second ``data``.
GTS_BOOKING: dict[str, Any] = {
    "message": "booked",
    "request_id": "r-1",
    "data": {
        "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "order_number": 61453,
        "status": "BO",
        "gds_pnr": "UBPLKW",
        "supplier_pnr": ["UBPLKW"],
        "trip_type": "OW",
        "price_info": {"price": 46.89, "currency": "EUR", "fee_amount": 5.5},
        "routes": [
            {
                "route_index": 1,
                "direction": "BOM-MAD",
                "stops": 1,
                "trip_time_minutes": 440,
                "segments": [
                    {
                        "leg": "BOM-IST",
                        "departure_airport_code": "BOM",
                        "departure_date": "2026-10-01",
                        "departure_time": "06:55",
                        "departure_timezone": "UTC+5",
                        "arrival_airport_code": "IST",
                        "arrival_date": "2026-10-01",
                        "arrival_time": "09:30",
                        "arrival_timezone": "UTC+3",
                    },
                    {
                        "leg": "IST-MAD",
                        "departure_airport_code": "IST",
                        "departure_date": "2026-10-01",
                        "departure_time": "11:40",
                        "departure_timezone": "UTC+3",
                        "arrival_airport_code": "MAD",
                        "arrival_date": "2026-10-01",
                        "arrival_time": "14:15",
                        "arrival_timezone": "UTC+2",
                    },
                ],
            }
        ],
    },
}

#: What the customer owes: ``price`` is fare plus taxes and excludes the agency
#: fee beside it, and their sum is exactly the recorded ``payable_amount``.
EXPECTED_TOTAL = "52.39"

#: What ``GET /public/orders/{id}/`` and the booking answer's ``order`` carry.
#: The wire says ``gts_``; the columns say ``provider_`` (API.md §21).
ORDER_FIELDS = {
    "id",
    "order_no",
    "product",
    "status",
    "gts_status",
    "gts_order_number",
    "gts_order_uid",
    "gts_pnr",
    "request_id",
    "offer_id",
    "amount",
    "passengers",
    "route",
    "travel_start_at",
    "travel_end_at",
    "route_summary",
    "ticket_time_limit_at",
    "cancellation_reason",
    "created_at",
    "updated_at",
    "booked_at",
    "paid_at",
    "ticketed_at",
    "cancelled_at",
    "data",
}

#: What a row of ``GET /public/orders/`` carries — a card, not the record.
LIST_FIELDS = {
    "id",
    "order_no",
    "product",
    "status",
    "amount",
    "route",
    "passengers",
    "route_summary",
    "travel_start_at",
    "travel_end_at",
    "passenger_count",
    "gts_pnr",
    "ticket_time_limit_at",
    "created_at",
}

#: The five keys a card's traveller carries. Everything PROJECT.md §13 calls
#: personal data is missing on purpose, and the test below names the missing
#: keys rather than only this set — a leak would show up as an extra key.
PASSENGER_CARD_FIELDS = {
    "first_name",
    "last_name",
    "middle_name",
    "type",
    "ticket_number",
}

#: What a card's traveller must never carry (PROJECT.md §13).
PASSENGER_PRIVATE_FIELDS = {
    "document",
    "birth_date",
    "citizenship",
    "gender",
    "email",
    "phone",
    "provider_traveler_id",
    "position",
    "anonymized_at",
}


def _envelope(data: Any) -> dict[str, Any]:
    return {"status": "success", "message": "Все ок.", "code": 0, "data": data}


async def _installation(session: AsyncSession) -> None:
    """An installation that sells flights and can reach GTS."""
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


def _mock_signin() -> respx.Route:
    return respx.post(f"{GTS}/v1/auth/signin/").mock(
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


def _mock_booking(answer: dict[str, Any] = GTS_BOOKING) -> respx.Route:
    return respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(200, json=_envelope(answer))
    )


def _mock_cancel() -> respx.Route:
    """GTS's recorded cancel answer: ``order``, and **no ``data``**."""
    return respx.post(f"{GTS}/v1/content/cancel/").mock(
        return_value=httpx.Response(
            200, json={"status": "success", "code": 100, "order": {"status": "CB"}}
        )
    )


_counter = 0


async def _order(session: AsyncSession, customer: Customer, **overrides: Any) -> Order:
    """A booked order, without going through GTS to get one."""
    global _counter
    _counter += 1
    fields: dict[str, Any] = {
        "order_no": f"B2C-2608-{_counter:06d}",
        "customer_id": customer.id,
        "product": "flight",
        "status": OrderStatus.BOOKED.value,
        "provider_order_number": "61453",
        "provider_order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "provider_pnr": "UBPLKW",
        "provider_status": "BO",
        "provider_response": GTS_BOOKING,
        "request_id": "r-1",
        "offer_id": "o-1",
        "amount_total": "52.39",
        "currency": "EUR",
        **overrides,
    }
    order = Order(**fields)
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


@pytest.fixture
def headers(customer: Customer) -> dict[str, str]:
    return customer_headers_for(customer)


def _booking_headers(headers: dict[str, str], key: str | None = None) -> dict[str, str]:
    """A client that names its own key. Sending none is the ordinary case and
    is covered separately — the server derives one (API.md §10)."""
    return {**headers, "Idempotency-Key": key or str(uuid.uuid4())}


async def _book(
    api: AsyncClient,
    headers: dict[str, str],
    *,
    key: str | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    return await api.post(
        BOOKING, json=body or BOOKING_BODY, headers=_booking_headers(headers, key)
    )


# --- what booking writes -------------------------------------------------------------


@respx.mock
async def test_a_booking_is_filed_under_the_customer_who_made_it(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    await _installation(session)
    _mock_signin()
    _mock_booking()

    response = await _book(api, headers)

    assert response.status_code == 200
    answer = response.json()["data"]
    # GTS's answer, byte for byte and **where it has always been**, with two
    # keys of ours beside it. Adding a field is not a breaking change; moving
    # every existing one a level down is (API.md §1, §20).
    assert {key: answer[key] for key in GTS_BOOKING} == GTS_BOOKING
    assert set(answer) == set(GTS_BOOKING) | {"order", "payment"}
    assert answer["order"]["status"] == "booked"
    # What is still owed, derived from the order rather than stored (``O12``).
    assert answer["payment"] == {
        "status": "pending",
        "amount": {"amount": EXPECTED_TOTAL, "currency": "EUR"},
        "pay_before": None,
    }

    detail = (
        await api.get(f"{ORDERS}{answer['order']['id']}/", headers=headers)
    ).json()
    order = detail["data"]
    assert order["product"] == "flight"
    # Read out of the *inner* data, not the wrapper — the bug this shape
    # caught: reading the top level leaves every identifier NULL.
    assert order["gts_order_number"] == "61453"
    assert order["gts_order_uid"] == "cd3f1e7bfde940f8bea03cde13f07dfd"
    assert order["gts_pnr"] == "UBPLKW"
    # Ours and theirs, side by side and never merged.
    assert order["status"] == "booked"
    assert order["gts_status"] == "BO"
    assert order["booked_at"] is not None
    assert order["cancelled_at"] is None
    # Money is a string with a currency beside it (API.md §1), and it is the
    # fare plus the fee — what the customer will actually be charged.
    assert order["amount"] == {"amount": EXPECTED_TOTAL, "currency": "EUR"}
    # An order number of our own exists from the first row, before GTS has
    # named anything: support needs one handle that always works.
    assert order["order_no"].startswith("B2C-")
    # The search this came from, carried on the row.
    assert order["request_id"] == "r-1"
    assert order["offer_id"] == "o-1"
    # GTS's answer, verbatim and whole — including the fields we never read.
    assert order["data"] == GTS_BOOKING
    assert set(order) == ORDER_FIELDS
    # The booking answer's ``order`` is the detail, not a third shape.
    assert answer["order"] == order
    # ``id`` is ours and ``gts_order_number`` is theirs — never merged
    # (API.md §21), so a client that confuses them fails loudly here.
    assert uuid.UUID(order["id"]) is not None
    assert order["id"] != order["gts_order_number"]


@respx.mock
async def test_an_answer_we_do_not_have_yet_carries_only_our_half(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """A duplicate replayed while the first attempt is still in flight.

    ``order`` and ``payment`` are always there; GTS's keys are simply absent,
    which is honest — we do not have an answer to relay. The old shape said
    ``"data": null``, which claimed a key we could not fill (API.md §20).
    """
    await _installation(session)
    order = await _order(session, customer, status="created", provider_response=None)

    answer = orders_service.booking_answer(order)

    assert set(answer) == {"order", "payment"}
    assert answer["order"]["status"] == "created"
    assert answer["payment"]["status"] == "pending"


@respx.mock
async def test_the_list_is_a_card_and_the_detail_is_the_record(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The list used to be the whole row. It stopped being one when ``data``
    left it: twenty provider answers on a page is hundreds of kilobytes nobody
    on a list screen reads (API.md §21).

    What stayed is the card: route, dates, travellers, price. The line is the
    **field**, not the object — the traveller's document, birth date and
    contacts are what must not fly past on every request (PROJECT.md §13)."""
    await _installation(session)
    _mock_signin()
    _mock_booking()
    await _book(api, headers)

    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]

    assert set(row) == LIST_FIELDS
    # The heavy one is gone; the count stands beside the travellers it counts.
    assert "data" not in row
    assert row["passenger_count"] == 1
    assert len(row["passengers"]) == row["passenger_count"]
    assert row["amount"] == {"amount": EXPECTED_TOTAL, "currency": "EUR"}
    assert row["gts_pnr"] == "UBPLKW"
    assert row["route"]["summary"] == row["route_summary"]

    # Every field the card shows means the same thing on the record, so the
    # two cannot drift into disagreeing about one. ``passengers`` is exempt:
    # it is the same object with fewer keys, which the next test pins down.
    shared = set(row) - {"passenger_count", "passengers"}
    detail = (await api.get(f"{ORDERS}{row['id']}/", headers=headers)).json()["data"]
    assert {key: detail[key] for key in shared} == {
        key: value for key, value in row.items() if key in shared
    }
    assert len(detail["passengers"]) == row["passenger_count"]


@respx.mock
async def test_a_card_names_the_traveller_and_not_their_passport(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The guard PROJECT.md §13 asks for: a list request may say who is
    flying, and may not say what is in their document.

    Written as "these keys are absent" and not only as a set comparison,
    because the failure this exists to catch is a key **appearing** — a
    ``model_config`` that stopped dropping extras, or a builder switched to
    copying the stored traveller whole."""
    await _installation(session)
    _mock_signin()
    _mock_booking()
    await _book(api, headers)

    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]
    (person,) = row["passengers"]

    assert set(person) == PASSENGER_CARD_FIELDS
    assert not set(person) & PASSENGER_PRIVATE_FIELDS
    assert person["first_name"] == "Azimjon"
    assert person["last_name"] == "Yusufov"
    assert person["type"] == "ADT"
    # Not issued yet, and the key is still there: a card renders one shape.
    assert person["ticket_number"] is None

    # The detail is where the document lives, for one order at a time.
    detail = (await api.get(f"{ORDERS}{row['id']}/", headers=headers)).json()["data"]
    (full,) = detail["passengers"]
    assert full["document"]["number"] == "FA2145157"


async def test_an_order_booked_before_route_existed_still_has_one(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """No backfill ran, so every row booked before the column exists carries
    ``route IS NULL``. The card is built from what that row does know — which
    is the summary, and no directions."""
    await _order(session, customer, route=None, route_summary="TAS-IST")

    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]

    assert row["route"] == {"summary": "TAS-IST", "directions": []}


async def test_an_order_that_names_no_journey_says_null(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """Not an empty object: a row with nothing to say about the journey says
    nothing rather than an object of empty fields."""
    await _order(session, customer, route=None, route_summary=None)

    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]

    assert row["route"] is None


async def test_a_route_stored_in_a_shape_we_cannot_read_falls_back(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """One unreadable row must not take the whole page down with it — the
    list is twenty orders and nineteen of them are fine."""
    await _order(
        session,
        customer,
        route={"summary": "TAS-IST", "directions": "later"},
        route_summary="TAS-IST",
    )

    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]

    assert row["route"] == {"summary": "TAS-IST", "directions": []}


@respx.mock
async def test_a_card_carries_the_journey_without_the_segments(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """Direction level and no further: where from, where to, when. Flight
    numbers and airlines stay in ``data`` (API.md §21)."""
    await _installation(session)
    _mock_signin()
    _mock_booking()
    await _book(api, headers)

    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]

    assert row["route"]["summary"] == "BOM-MAD"
    (leg,) = row["route"]["directions"]
    assert leg["from"] == "BOM"
    assert leg["to"] == "MAD"
    assert "segments" not in leg

    # The same instant twice, and deliberately not the same string: the column
    # comes back from Postgres in UTC, while the stored direction keeps the
    # airport's local time and the offset beside it.
    assert dt.datetime.fromisoformat(leg["departure_at"]) == dt.datetime.fromisoformat(
        row["travel_start_at"]
    )


@respx.mock
async def test_a_booking_leaves_its_first_history_line(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The row is born ``created`` and is moved to ``booked`` by the machine,
    so the confirmation is a transition with an event and not an assignment.
    Slice S3 moves the insert to before the GTS call; this history does not
    change when it does."""
    await _installation(session)
    _mock_signin()
    _mock_booking()

    await _book(api, headers)

    rows = (
        (
            await session.execute(
                text(
                    "SELECT from_status, to_status, action, actor_type, actor_label"
                    " FROM order_events ORDER BY created_at"
                )
            )
        )
        .mappings()
        .all()
    )
    assert [dict(row) for row in rows] == [
        {
            "from_status": "created",
            "to_status": "booked",
            "action": "booking.confirmed",
            "actor_type": "system",
            "actor_label": "orders.booking",
        }
    ]


@respx.mock
async def test_the_travellers_are_stored_in_our_own_shape(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """PROJECT.md §13 promises an order can be anonymised, and that is exactly
    why the travellers are now stored rather than left in the provider blob:
    a structure we define can be scrubbed field by field, one whose shape GTS
    owns cannot (order-system/03-design.md ``O10``, closing A8).

    This answer carries no ``passengers``, so they come from the request — the
    fallback that keeps a receipt printable when the provider does not echo
    them back."""
    await _installation(session)
    _mock_signin()
    _mock_booking()

    await _book(api, headers)

    # The detail, not the list: travellers are one of the two things the card
    # deliberately leaves behind (API.md §21).
    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]
    order = (await api.get(f"{ORDERS}{row['id']}/", headers=headers)).json()["data"]
    assert order["passengers"] == [
        {
            "position": 1,
            "type": "ADT",
            "first_name": "Azimjon",
            "last_name": "Yusufov",
            "middle_name": None,
            "birth_date": "2002-12-20",
            "gender": "M",
            "citizenship": "UZ",
            "document": {
                "type": "PSP",
                "number": "FA2145157",
                "issue_date": "2019-05-30",
                "expire_date": "2029-05-29",
            },
            "email": "yusufovazimjon@gmail.com",
            "phone": "998998328192",
            "provider_traveler_id": None,
            # Booking holds a seat; the ticket number arrives with ticketing.
            "ticket_number": None,
            "anonymized_at": None,
        }
    ]


@respx.mock
async def test_an_answer_we_cannot_confirm_stays_created(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """A booking that really happened must leave a trace even when we cannot
    read the shape of it — but a row claiming to be ``booked`` while unable to
    name a price is worse than one that says so.

    It stays ``created``, which is precisely what that status means: we asked
    and do not know. **Not** ``needs_attention`` — no money has moved, and that
    state is one only a person leaves, which is how a parsing failure once
    turned into orders nobody could cancel or sweep (03-design.md §3.3).
    """
    await _installation(session)
    _mock_signin()
    _mock_booking({"data": {"reference": "1250", "state": "held"}})

    response = await _book(api, headers)

    assert response.status_code == 200
    assert response.json()["data"]["order"]["status"] == "created"
    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]
    order = (await api.get(f"{ORDERS}{row['id']}/", headers=headers)).json()["data"]
    assert order["status"] == "created"
    assert order["gts_order_number"] is None
    assert order["amount"] is None
    assert order["data"] == {"data": {"reference": "1250", "state": "held"}}
    # The evidence is on the event, which is what a person will read, and the
    # reason says which field was missing rather than only that one was.
    event = (
        await session.execute(
            text(
                "SELECT reason, meta::text FROM order_events"
                " WHERE action = 'booking.unresolved'"
            )
        )
    ).one()
    assert "no order number and no price" in str(event[0])
    assert "1250" in str(event[1])


@respx.mock
async def test_what_did_read_is_kept_even_when_the_booking_is_not_confirmed(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The seat is real; the row has to be able to name it.

    An order with no ``provider_order_number`` cannot be cancelled at all and
    no sweep looks at it, so an answer we could half read used to leak a live
    reservation with nothing in our database pointing at it. Everything that
    read is written now — only the price, the one thing missing, stays null.
    """
    await _installation(session)
    _mock_signin()
    _mock_booking({**GTS_BOOKING, "data": {**GTS_BOOKING["data"], "price_info": None}})

    response = await _book(api, headers)

    assert response.status_code == 200
    (row,) = (await api.get(ORDERS, headers=headers)).json()["data"]
    assert row["status"] == "created"
    assert row["amount"] is None
    # The card still draws: route, dates and travellers all came out of the
    # same answer the price did not.
    assert row["gts_pnr"] == "UBPLKW"
    assert row["route_summary"] == "BOM-MAD"
    assert row["route"]["directions"][0]["from"] == "BOM"
    assert row["travel_start_at"] == "2026-10-01T01:55:00Z"
    assert row["passenger_count"] == 1

    order = (await api.get(f"{ORDERS}{row['id']}/", headers=headers)).json()["data"]
    assert order["gts_order_number"] == "61453"
    assert order["gts_order_uid"] == "cd3f1e7bfde940f8bea03cde13f07dfd"
    assert order["gts_status"] == "BO"
    assert order["amount"] is None


@respx.mock
async def test_a_write_that_fails_never_reaches_gts(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inverse of what this used to assert, and the point of the slice.

    The row used to be written *after* GTS answered, inside a bare ``except``:
    a failed insert left a reservation nobody could find, cancel or refund, and
    the request still answered 200 (02-current-audit.md A1). Now the write comes
    first, so a database that cannot take the row costs a 500 and **no seat**.
    """

    async def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the database went away")

    monkeypatch.setattr(orders_service, "start_order", boom)
    await _installation(session)
    signin = _mock_signin()
    booking = _mock_booking()

    with pytest.raises(RuntimeError):
        await _book(api, headers)

    assert booking.call_count == 0
    assert signin.call_count == 0


@respx.mock
async def test_the_same_key_books_once(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """A dropped response and a double-tapped button look identical from here,
    and both must cost one seat (API.md §10)."""
    await _installation(session)
    _mock_signin()
    booking = _mock_booking()
    key = str(uuid.uuid4())

    first = await _book(api, headers, key=key)
    second = await _book(api, headers, key=key)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert booking.call_count == 1
    assert (await api.get(ORDERS, headers=headers)).json()["meta"]["total"] == 1


@respx.mock
async def test_the_database_holds_the_line_when_redis_forgets(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
    fake_redis: Any,
) -> None:
    """The Redis record is a cache, and a cache can be flushed. If that were the
    only guard, one eviction would turn a retry into a second real seat — so the
    unique index is the thing that actually holds (``O8``)."""
    await _installation(session)
    _mock_signin()
    booking = _mock_booking()
    key = str(uuid.uuid4())

    first = await _book(api, headers, key=key)
    await fake_redis.flushall()
    second = await _book(api, headers, key=key)

    assert second.status_code == 200
    assert booking.call_count == 1
    assert second.json()["data"]["order"]["id"] == first.json()["data"]["order"]["id"]
    assert (await api.get(ORDERS, headers=headers)).json()["meta"]["total"] == 1


@respx.mock
async def test_a_key_two_customers_share_is_refused_not_confused(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """Keys are chosen by clients, so two of them can collide. What must never
    happen is one customer being handed the other's booking.

    Two guards, and they answer differently. The Redis record in
    ``api/idempotency.py`` is keyed on the header alone, so it sees a second
    request with the same key and a different body and calls it a client bug —
    ``422``, which is the right answer to give a stranger. The database index is
    keyed on ``(customer_id, idempotency_key)``, so once the Redis record has
    expired — it lives 24 hours and an order lives forever — the second customer
    gets an order of their own rather than a collision nobody can resolve.

    Neither path ever reads across customers, which is the property that
    matters. The cost is that a colliding key is unusable for the second
    customer for a day; with UUIDs, as the contract specifies, it does not
    arise.
    """
    await _installation(session)
    _mock_signin()
    booking = _mock_booking()
    stranger = await make_customer(session, email="someone.else@example.uz")
    key = str(uuid.uuid4())

    mine = await _book(api, headers, key=key)
    theirs = await _book(
        api,
        customer_headers_for(stranger),
        key=key,
        body={**BOOKING_BODY, "offer_id": "o-2"},
    )

    assert mine.status_code == 200
    assert theirs.status_code == 422
    assert theirs.json()["errors"][0]["field"] == "Idempotency-Key"
    # The stranger's request never became a booking, and never saw mine.
    assert booking.call_count == 1
    assert theirs.json()["data"] is None


@respx.mock
async def test_booking_without_a_key_still_books_exactly_once(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The ordinary case now. It used to be a ``422``; the server derives the
    key from the request instead, so the protection no longer depends on the
    client having built a state machine for it (API.md §10)."""
    await _installation(session)
    _mock_signin()
    booking = _mock_booking()

    first = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)
    second = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)

    assert first.status_code == 200
    assert first.json()["data"] == second.json()["data"]
    assert booking.call_count == 1
    assert (await api.get(ORDERS, headers=headers)).json()["meta"]["total"] == 1


@respx.mock
async def test_a_keyless_booking_is_scoped_to_its_customer(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The derived key carries the subject. Without it the Redis record is one
    flat namespace, and two customers whose bodies happened to match would be
    handed each other's order."""
    await _installation(session)
    _mock_signin()
    booking = _mock_booking()
    stranger = await make_customer(session, email="stranger@brand.uz")
    # Two answers: ``orders`` is unique on the provider's number, so one seat
    # cannot be sold twice even in a test.
    booking.mock(
        side_effect=[
            httpx.Response(200, json=_envelope(GTS_BOOKING)),
            httpx.Response(
                200,
                json=_envelope(
                    {
                        **GTS_BOOKING,
                        "data": {**GTS_BOOKING["data"], "order_number": 61454},
                    }
                ),
            ),
        ]
    )

    mine = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)
    theirs = await api.post(
        BOOKING, json=BOOKING_BODY, headers=customer_headers_for(stranger)
    )

    assert mine.status_code == theirs.status_code == 200
    assert mine.json()["data"]["order"]["id"] != theirs.json()["data"]["order"]["id"]
    assert booking.call_count == 2
    # And each sees only their own.
    assert (await api.get(ORDERS, headers=headers)).json()["meta"]["total"] == 1


@respx.mock
async def test_a_keyless_booking_that_is_refused_can_be_retried(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """Releasing on refusal has to work for the derived key too, or a customer
    whose first attempt hit a full flight could never book that search again."""
    await _installation(session)
    _mock_signin()
    refused = respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200, json={"status": "error", "code": -104, "message": "no seats left"}
        )
    )

    first = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)
    assert first.status_code == 502

    refused.mock(return_value=httpx.Response(200, json=_envelope(GTS_BOOKING)))
    second = await api.post(BOOKING, json=BOOKING_BODY, headers=headers)

    assert second.status_code == 200
    assert second.json()["data"]["order"]["status"] == "booked"


@respx.mock
async def test_a_refused_booking_frees_its_key(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """A refusal took no seat, so retrying is safe and is what the customer
    wants. Every other ending keeps its key, because every other ending may be
    holding one."""
    await _installation(session)
    _mock_signin()
    refused = respx.post(f"{GTS}/v1/content/booking/").mock(
        return_value=httpx.Response(
            200, json={"status": "error", "code": -104, "message": "no seats left"}
        )
    )
    key = str(uuid.uuid4())

    first = await _book(api, headers, key=key)
    assert first.status_code == 502

    refused.mock(return_value=httpx.Response(200, json=_envelope(GTS_BOOKING)))
    second = await _book(api, headers, key=key)

    assert second.status_code == 200
    orders = (await api.get(ORDERS, headers=headers)).json()["data"]
    assert sorted(order["status"] for order in orders) == ["booked", "failed"]


# --- what the customer reads ---------------------------------------------------------


async def test_the_list_shows_only_my_orders(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    stranger = await make_customer(session, email="someone.else@example.uz")
    mine = await _order(session, customer)
    await _order(session, stranger, provider_order_number="9999")

    response = await api.get(ORDERS, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["data"]] == [str(mine.id)]
    assert body["meta"]["total"] == 1


async def test_the_list_filters_and_pages(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """``?status=`` takes our vocabulary now, not GTS's."""
    await _order(session, customer, provider_order_number="1")
    await _order(
        session, customer, provider_order_number="2", status=OrderStatus.TICKETED.value
    )
    await _order(
        session, customer, provider_order_number="3", status=OrderStatus.TICKETED.value
    )

    booked = await api.get(ORDERS, params={"status": "booked"}, headers=headers)
    ticketed = await api.get(ORDERS, params={"status": "ticketed"}, headers=headers)
    upstream = await api.get(ORDERS, params={"status": "BO"}, headers=headers)
    railway = await api.get(ORDERS, params={"product": "railway"}, headers=headers)
    paged = await api.get(ORDERS, params={"page_size": 2}, headers=headers)

    assert booked.json()["meta"]["total"] == 1
    assert ticketed.json()["meta"]["total"] == 2
    # GTS's code is not our filter: it matches nothing rather than everything.
    assert upstream.json()["meta"]["total"] == 0
    assert railway.json()["meta"]["total"] == 0
    assert len(paged.json()["data"]) == 2
    assert paged.json()["meta"]["total_pages"] == 2


async def test_ordering_is_a_whitelist(
    api: AsyncClient, customer: Customer, headers: dict[str, str]
) -> None:
    """Only ``created_at``. Ordering by a status would publish the order of the
    enum as part of the contract (``api/listing.py``)."""
    response = await api.get(ORDERS, params={"ordering": "status"}, headers=headers)

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "ordering"


async def test_one_order_is_readable_and_someone_elses_is_not(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    stranger = await make_customer(session, email="someone.else@example.uz")
    mine = await _order(session, customer)
    theirs = await _order(session, stranger, provider_order_number="9999")

    ok = await api.get(f"{ORDERS}{mine.id}/", headers=headers)
    forbidden = await api.get(f"{ORDERS}{theirs.id}/", headers=headers)
    missing = await api.get(f"{ORDERS}{uuid.uuid4()}/", headers=headers)

    assert ok.status_code == 200
    assert ok.json()["data"]["data"] == GTS_BOOKING
    # "Not yours" and "no such thing" are the same answer (API.md §18).
    assert forbidden.status_code == missing.status_code == 404
    assert forbidden.json()["errors"] == missing.json()["errors"]


async def test_the_history_needs_a_customer_token(api: AsyncClient) -> None:
    assert (await api.get(ORDERS)).status_code == 401


# --- cancelling, on the order (API.md §21) --------------------------------------------


def _cancel(api: AsyncClient, order: Order, headers: dict[str, str]) -> Any:
    return api.post(f"{ORDERS}{order.id}/cancel/", headers=_booking_headers(headers))


@respx.mock
async def test_cancelling_someone_elses_booking_never_reaches_gts(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """The hole this module was built to close: knowing an identifier used to
    be enough to release somebody else's seat."""
    await _installation(session)
    signin = _mock_signin()
    cancel = _mock_cancel()
    stranger = await make_customer(session, email="someone.else@example.uz")
    order = await _order(session, stranger)

    response = await _cancel(api, order, headers)

    assert response.status_code == 404
    assert cancel.call_count == 0
    assert signin.call_count == 0


@respx.mock
async def test_cancelling_my_own_booking_moves_the_order(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    await _installation(session)
    _mock_signin()
    cancel = _mock_cancel()
    order = await _order(session, customer)

    response = await _cancel(api, order, headers)

    assert response.status_code == 200
    assert cancel.call_count == 1
    # The provider is named by **its** handle, not ours.
    assert jsonlib.loads(cancel.calls.last.request.content) == {"order_number": "61453"}
    detail = response.json()["data"]
    # Ours says what happened, theirs says what they called it.
    assert detail["status"] == "cancelled"
    assert detail["gts_status"] == "CB"
    assert detail["cancellation_reason"] == "customer"
    assert detail["cancelled_at"] is not None


@respx.mock
async def test_a_ticketed_order_is_refused_before_gts_is_called(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """``ticketed → cancelled`` is not in the table, and the refusal has to
    happen on the near side of the call: checking afterwards would release a
    real seat and then answer 409."""
    await _installation(session)
    signin = _mock_signin()
    cancel = _mock_cancel()
    order = await _order(session, customer, status=OrderStatus.TICKETED.value)

    response = await _cancel(api, order, headers)

    assert response.status_code == 409
    assert response.json()["errors"][0]["code"] == "conflict"
    assert cancel.call_count == 0
    assert signin.call_count == 0


@respx.mock
async def test_cancelling_needs_a_key_and_only_cancels_once(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """A money endpoint like the others: releasing a seat twice is not harmful
    on its own, but a second call would answer 409 to a client that had already
    succeeded (API.md §10)."""
    await _installation(session)
    _mock_signin()
    cancel = _mock_cancel()
    order = await _order(session, customer)
    key = str(uuid.uuid4())

    first = await api.post(
        f"{ORDERS}{order.id}/cancel/", headers=_booking_headers(headers, key)
    )
    second = await api.post(
        f"{ORDERS}{order.id}/cancel/", headers=_booking_headers(headers, key)
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert cancel.call_count == 1


@respx.mock
async def test_cancelling_twice_without_a_key_only_cancels_once(
    api: AsyncClient,
    session: AsyncSession,
    customer: Customer,
    headers: dict[str, str],
) -> None:
    """``cancel/`` sends no body at all, so the derived key rests on the path —
    which carries the order id. Two calls for one order collapse; two orders
    stay two."""
    await _installation(session)
    _mock_signin()
    cancel = _mock_cancel()
    order = await _order(session, customer)

    first = await api.post(f"{ORDERS}{order.id}/cancel/", headers=headers)
    second = await api.post(f"{ORDERS}{order.id}/cancel/", headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert cancel.call_count == 1


async def test_cancelling_needs_a_customer_token(
    api: AsyncClient, session: AsyncSession, customer: Customer
) -> None:
    order = await _order(session, customer)

    response = await api.post(f"{ORDERS}{order.id}/cancel/")

    assert response.status_code == 401
