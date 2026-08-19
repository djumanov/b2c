"""The flight adapter's order half — GTS's answer turned into our types.

No database and no network: the fixtures below are the shapes GTS actually
returned, copied from the ``EASY_GATEWAY`` collection and from the recorded
live answers in ``~/Downloads/drct-error*.json``. Those recordings outrank
``docs/GTS.md`` — they are calls, not documentation — and reading them wrongly
is the failure this file exists to catch (order-system/00-README.md).

What is pinned here is every reading that the saga will later depend on and
cannot check for itself: the money, the deadline, the travellers, the status
map, and the refusal to invent an order out of an answer that does not name
one.
"""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from app.api.errors import UpstreamError, UpstreamTimeout
from app.modules.orders.states import OrderStatus
from app.providers.products.flight import _HELD_CODES, FlightAdapter, _partial
from app.providers.products.orders import (
    FailureClass,
    OrderOperations,
    UnreadableAnswer,
)
from tests.unit.test_flight_adapter import RecordingClient

BOOKING_REQUEST: dict[str, Any] = {
    "request_id": "7788056f-ec7e-4b1d-946c-299b97f07608",
    "offer_id": "9689fa0a-6a7c-4604-afb9-4de663de887b",
    "passengers": [
        {
            "type": "ADT",
            "gender": "M",
            "last_name": "Yusufov",
            "first_name": "Azimjon",
            "middle_name": "Kamoliddin",
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

#: The collection's ``/content/Booking`` answer, trimmed to the fields the
#: adapter reads — two layers deep, prices as JSON floats, the traveller echoed
#: back with a provider id and a null ticket number.
GTS_BOOKING: dict[str, Any] = {
    "message": "booked",
    "request_id": "7788056f-ec7e-4b1d-946c-299b97f07608",
    "data": {
        "order_number": 61453,
        "order_uid": "cd3f1e7bfde940f8bea03cde13f07dfd",
        "status": "BO",
        "gds_pnr": "UBPLKW",
        "trip_type": "OW",
        "ticket_time_limit": 4319,
        "price_info": {
            "price": 46.89,
            "currency": "eur",
            "fee_amount": 5.5,
            "commission_amount": 0,
        },
        "routes": [
            {
                "route_index": 1,
                "direction": "BOM-MAD",
                "segments": [
                    {
                        "leg": "BOM-IST",
                        "departure_city_code": "BOM",
                        "departure_date": "2026-10-01",
                        "departure_time": "06:55",
                        "departure_timezone": "UTC+5",
                    },
                    {"leg": "IST-MAD", "departure_date": "2026-10-01"},
                ],
            }
        ],
        "passengers": [
            {
                "ticket_number": None,
                "passenger_type": "ADT",
                "first_name": "AZIMJON",
                "last_name": "YUSUFOV",
                "middle_name": " ",
                "birth_date": "2002-12-20",
                "gender": "M",
                "phone_number": "+998992747465",
                "passenger_id": "4faa37bc-91d1-4da1-ba3d-b22ef8ec8802",
                "document": {
                    "citizenship": "UZ",
                    "document_type": "NP",
                    "passport_number": "FA2145157",
                    "passport_issuance": "2019-05-30",
                    "passport_expiry": "2029-05-29",
                },
            }
        ],
    },
}


#: One order out of this installation's **live** GTS, captured 2026-08-19. Not
#: from the collection: the collection is older than the service it records,
#: which is how the booking above came to be read against the wrong spellings
#: for a day (STATUS.md §8.15).
#:
#: Everything the adapter needs sits somewhere else here — a flat ``price`` and
#: ``currency`` instead of ``price_info``, ``routes`` under ``offer``, bare
#: ``departure_airport`` instead of ``departure_airport_code``, ``middlename``
#: without its underscore, ``Male`` spelled out, ``GMT+5:00`` rather than
#: ``UTC+5``, and day-first dates in one segment beside ISO ones in another.
#: Reading it is what this fixture is for.
GTS_LIVE_ORDER: dict[str, Any] = {
    "passengers": [
        {
            "offer": {
                "price_info": [
                    {
                        "fee_amount": 1.04,
                        "tax_amount": 32.25,
                        "base_amount": 91.13,
                        "total_amount": 124.42,
                        "payable_amount": 124.42,
                        "commission_amount": 0,
                    }
                ]
            },
            "document": {
                "passport_number": "AA1111111",
                "passport_expiry": "2030-12-10",
                "nationality": "UZ",
                "document_type": "PSP",
            },
            "passenger_id": "db6f7d06-53c7-11ee-9874-f4b520498588",
            "firstname": "Elmurod",
            "lastname": "Egamberdiyev",
            "middlename": "Hojiakbar o'g'li",
            "gender": "Male",
            "birth_date": "2001-01-01",
            "passenger_category": "Student",
            "passenger_type": "ADT",
            "phone_number": "+9989992747465",
            "email_address": "test@mail.uz",
        }
    ],
    "status": "STATUS_VOID",
    "gds_pnr": "L9J87M",
    "supplier_pnr": "J9-L9J87M",
    "created_at": "2023-09-11T21:13:00",
    "ticket_time_limit": "2023-09-13T21:43:00",
    "void_time_limit": 120,
    "price": 320.12,
    "currency": "USD",
    "offer": {
        "routes": [
            {
                "stops": 1,
                "segments": [
                    {
                        "leg": "IST-ESB",
                        "arrival_date": "2023-04-04",
                        "arrival_time": "14:05:00",
                        "carrier_code": "TK",
                        "flight_number": "2150",
                        "segment_index": 1,
                        "departure_date": "2023-04-04",
                        "departure_time": "13:00:00",
                        "arrival_airport": "ESB",
                        "arrival_timezone": "GMT+5:00",
                        "duration_minutes": 65,
                        "departure_airport": "IST",
                        "departure_timezone": "GMT+5:00",
                    },
                    {
                        "dir_number": 2,
                        "arrival_date": "04-04-2023",
                        "arrival_time": "04-04-2023 17:40:00",
                        "carrier_code": "AJ",
                        "flight_number": "7030",
                        "departure_date": "04-04-2023",
                        "departure_time": "04-04-2023 16:35:00",
                        "arrival_airport": "AYT",
                        "duration_minutes": 65,
                        "departure_airport": "ESB",
                    },
                ],
                "direction": "IST-AYT",
                "route_index": 1,
            },
            {
                "segments": [
                    {
                        "dir_number": 1,
                        "arrival_date": "05-04-2023",
                        "arrival_time": "05-04-2023 08:30:00",
                        "carrier_code": "TK",
                        "flight_number": "2435",
                        "departure_date": "05-04-2023",
                        "departure_time": "05-04-2023 07:00:00",
                        "arrival_airport": "IST",
                        "duration_minutes": 90,
                        "departure_airport": "AYT",
                    }
                ],
                "route_index": 2,
            },
        ],
        "offer_id": "4b739ef16ee7af525f7381b2ba7ce661",
        "price_info": {"price": 252, "fee_amount": 1.02, "commission_amount": 0},
        "price_details": [
            {
                "fee_amount": 1.04,
                "tax_amount": 32.25,
                "base_amount": 91.13,
                "total_amount": 124.42,
                "passenger_type": "ADT",
                "payable_amount": 124.42,
                "commission_amount": 0,
            }
        ],
    },
    "provider": {"name": "AerTicket", "provider_id": "1233-12323-1122-12233334"},
    "airline_code": "TK",
    "order_number": 1,
}


def _answer(**overrides: Any) -> dict[str, Any]:
    """The recorded booking answer with its inner ``data`` amended."""
    body = {**GTS_BOOKING["data"], **overrides}
    return {**GTS_BOOKING, "data": body}


async def _book(answer: dict[str, Any], request: dict[str, Any] | None = None) -> Any:
    return await FlightAdapter().book(
        RecordingClient(answer), request or BOOKING_REQUEST
    )


# --- the port itself ------------------------------------------------------------------


def test_the_flight_adapter_can_manage_orders() -> None:
    """``order_operations`` narrows by protocol, so a vertical that has not
    implemented the order half is caught at the seam rather than at the call."""
    assert isinstance(FlightAdapter(), OrderOperations)


def test_every_provider_code_has_a_meaning() -> None:
    """GTS.md §4 lists eight codes and the live service adds a second spelling
    of two of them. A code with no entry used to become the default and turn a
    voided order into a booked one; ``_HELD_CODES`` is what stops that now, and
    this list only has to stay honest about what has been seen."""
    assert set(FlightAdapter().status_map()) == {
        "BO",
        "PW",
        "TI",
        "TE",
        "CB",
        "VO",
        "RF",
        "PRF",
        "STATUS_BOOK",
        "STATUS_VOID",
    }
    assert set(FlightAdapter().status_map().values()) <= set(OrderStatus)


def test_every_held_code_means_booked() -> None:
    """The two lists must agree: a code we act on without asking again has to
    be one the map calls a held seat."""
    mapping = FlightAdapter().status_map()
    assert set(mapping) >= _HELD_CODES
    assert {mapping[code] for code in _HELD_CODES} == {OrderStatus.BOOKED}


# --- money ----------------------------------------------------------------------------


async def test_the_total_is_the_fare_plus_the_fee() -> None:
    """``price`` excludes the agency fee sitting beside it, and their sum is
    exactly the ``payable_amount`` of the recorded passenger. Charging ``price``
    alone would undercharge every order by the fee."""
    result = await _book(GTS_BOOKING)

    assert result.total.amount == Decimal("52.39")
    # Currency is normalised: the recording spells it lower case.
    assert result.total.currency == "EUR"


async def test_a_json_float_survives_as_a_decimal() -> None:
    """``core.money.to_decimal`` refuses floats on purpose — binary fractions
    are not money — so the adapter has to route them through ``str``. Getting
    this wrong is a 500 on every booking."""
    result = await _book(
        _answer(price_info={"price": 0.1, "currency": "USD", "fee_amount": 0.2})
    )

    assert result.total.amount == Decimal("0.30")
    assert result.total.amount != Decimal(0.1) + Decimal(0.2)


async def test_a_missing_fee_is_not_a_missing_price() -> None:
    result = await _book(_answer(price_info={"price": 100, "currency": "UZS"}))

    assert result.total.amount == Decimal("100.00")


# --- the ticket deadline (order-system/03-design.md §3.10, Q1) ------------------------


@pytest.mark.parametrize(
    ("value", "expected_hours"),
    [
        pytest.param(4319, 4319 / 60, id="integer-under-the-ceiling-is-minutes"),
        pytest.param(288000, 288000 / 3600, id="integer-over-the-ceiling-is-seconds"),
    ],
)
async def test_a_relative_deadline_is_read_in_the_unit_that_fits(
    value: int, expected_hours: float
) -> None:
    """Three spellings have been seen and none is documented. Both integers are
    plausible deadlines under exactly one reading each — 4319 minutes is three
    days, 288000 seconds is eighty hours — and the threshold separates them."""
    before = dt.datetime.now(dt.UTC)

    result = await _book(_answer(ticket_time_limit=value))

    assert result.ticket_time_limit_at is not None
    hours = (result.ticket_time_limit_at - before).total_seconds() / 3600
    assert expected_hours - 0.1 < hours < expected_hours + 0.1


async def test_an_absolute_deadline_is_taken_as_it_stands() -> None:
    result = await _book(_answer(ticket_time_limit="2026-09-13T21:43:00Z"))

    assert result.ticket_time_limit_at == dt.datetime(
        2026, 9, 13, 21, 43, tzinfo=dt.UTC
    )


@pytest.mark.parametrize("value", [None, "", "soon", 0, -5, {"in": "hours"}])
async def test_an_unreadable_deadline_is_absent_rather_than_invented(
    value: Any,
) -> None:
    """Guessing here either cancels a live booking early or lets a dead one sit.
    The caller has a configured fallback; the adapter's job is to say it does
    not know."""
    result = await _book(_answer(ticket_time_limit=value))

    assert result.ticket_time_limit_at is None


# --- the journey ----------------------------------------------------------------------


async def test_the_route_and_departure_come_off_the_first_segment() -> None:
    """Both exist so a list row renders without opening the answer."""
    result = await _book(GTS_BOOKING)

    assert result.route_summary == "BOM-MAD"
    # 06:55 at UTC+5 is 01:55 UTC — the offset beside the time is honoured when
    # GTS states one.
    assert result.travel_start_at == dt.datetime(
        2026, 10, 1, 6, 55, tzinfo=dt.timezone(dt.timedelta(hours=5))
    )


async def test_one_direction_per_route_with_its_own_dates() -> None:
    """What a card renders: where from, where to, when, how long, how many
    stops — and nothing from below the direction."""
    result = await _book(
        _answer(
            routes=[
                {
                    "direction": "TAS-VKO",
                    "stops": 0,
                    "trip_time_minutes": 360,
                    "segments": [
                        {
                            "departure_airport_code": "TAS",
                            "departure_date": "2026-03-29",
                            "departure_time": "10:00",
                            "departure_timezone": "UTC+5",
                            "arrival_airport_code": "VKO",
                            "arrival_date": "2026-03-29",
                            "arrival_time": "14:00",
                            "arrival_timezone": "UTC+3",
                        }
                    ],
                }
            ]
        )
    )

    assert result.route is not None
    assert result.route.summary == "TAS-VKO"
    (leg,) = result.route.directions
    assert leg.origin == "TAS"
    assert leg.destination == "VKO"
    assert leg.departure_at == dt.datetime(
        2026, 3, 29, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=5))
    )
    # The two ends carry different offsets, which is why the arrival is read
    # rather than derived from the departure plus the duration.
    assert leg.arrival_at == dt.datetime(
        2026, 3, 29, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=3))
    )
    assert leg.duration_minutes == 360
    assert leg.stops == 0
    assert result.travel_start_at == leg.departure_at
    assert result.travel_end_at == leg.arrival_at


async def test_a_direction_is_named_by_its_ends_not_its_transfer() -> None:
    """One stop in the middle must not become the destination."""
    result = await _book(
        _answer(
            routes=[
                {
                    "direction": "TAS-MAD",
                    "segments": [
                        {
                            "departure_airport_code": "TAS",
                            "arrival_airport_code": "IST",
                            "departure_date": "2026-10-01",
                            "departure_time": "06:55",
                            "arrival_date": "2026-10-01",
                            "arrival_time": "09:30",
                        },
                        {
                            "departure_airport_code": "IST",
                            "arrival_airport_code": "MAD",
                            "departure_date": "2026-10-01",
                            "departure_time": "11:40",
                            "arrival_date": "2026-10-01",
                            "arrival_time": "14:15",
                        },
                    ],
                }
            ]
        )
    )

    assert result.route is not None
    (leg,) = result.route.directions
    assert (leg.origin, leg.destination) == ("TAS", "MAD")
    assert leg.departure_at == dt.datetime(2026, 10, 1, 6, 55, tzinfo=dt.UTC)
    assert leg.arrival_at == dt.datetime(2026, 10, 1, 14, 15, tzinfo=dt.UTC)


async def test_the_ends_fall_back_to_the_direction_label() -> None:
    """The recorded booking leaves ``departure_city_code`` empty and older
    answers name no airport code at all; ``direction`` still says where."""
    result = await _book(GTS_BOOKING)

    assert result.route is not None
    (leg,) = result.route.directions
    assert (leg.origin, leg.destination) == ("BOM", "MAD")


async def test_a_direction_that_names_no_arrival_says_so() -> None:
    """A half-read journey stays half-read — no invented landing time."""
    result = await _book(
        _answer(
            routes=[
                {
                    "direction": "TAS-IST",
                    "segments": [{"departure_date": "2026-09-14"}],
                }
            ]
        )
    )

    assert result.route is not None
    (leg,) = result.route.directions
    assert leg.arrival_at is None
    assert leg.duration_minutes is None
    assert result.travel_end_at is None


async def test_a_missing_timezone_is_read_as_utc() -> None:
    """The column is for ordering and reminders, not for a boarding pass: the
    exact local time stays in ``raw``."""
    result = await _book(
        _answer(
            routes=[
                {
                    "direction": "TAS-IST",
                    "segments": [
                        {"departure_date": "2026-09-14", "departure_time": "17:00:00"}
                    ],
                }
            ]
        )
    )

    assert result.travel_start_at == dt.datetime(2026, 9, 14, 17, 0, tzinfo=dt.UTC)


async def test_two_routes_read_as_one_line() -> None:
    result = await _book(
        _answer(
            routes=[
                {"direction": "TAS-IST", "segments": []},
                {"direction": "IST-TAS", "segments": []},
            ]
        )
    )

    assert result.route_summary == "TAS-IST / IST-TAS"
    assert result.travel_start_at is None
    assert result.route is not None
    assert [(leg.origin, leg.destination) for leg in result.route.directions] == [
        ("TAS", "IST"),
        ("IST", "TAS"),
    ]


@pytest.mark.parametrize("routes", [None, [], "later", [{"segments": None}]])
async def test_a_journey_we_cannot_read_costs_nothing(routes: Any) -> None:
    """Cosmetic fields must never be able to lose a booking."""
    result = await _book(_answer(routes=routes))

    assert result.provider_order_number == "61453"
    assert result.route_summary is None
    assert result.travel_start_at is None
    assert result.travel_end_at is None
    # ``None`` and not an empty route: a caller must be able to tell "no
    # journey named" from "a journey we could not read".
    assert result.route is None


# --- travellers -----------------------------------------------------------------------


async def test_the_travellers_come_from_the_answer() -> None:
    """Preferred over the request because the answer carries the provider's own
    id and, after ticketing, the ticket number. The document keys are the
    *other* spelling GTS uses — reading only one of the two would drop the
    passport number of every order booked through that path."""
    result = await _book(GTS_BOOKING)

    (traveler,) = result.travelers
    assert traveler.position == 1
    assert traveler.type == "ADT"
    assert traveler.first_name == "AZIMJON"
    assert traveler.last_name == "YUSUFOV"
    # ``" "`` is whitespace, not a middle name.
    assert traveler.middle_name is None
    assert traveler.document_type == "NP"
    assert traveler.document_number == "FA2145157"
    assert traveler.document_issue_date == "2019-05-30"
    assert traveler.document_expiry_date == "2029-05-29"
    assert traveler.citizenship == "UZ"
    assert traveler.phone == "+998992747465"
    assert traveler.provider_traveler_id == "4faa37bc-91d1-4da1-ba3d-b22ef8ec8802"
    assert traveler.ticket_number is None


async def test_the_request_is_the_fallback_when_the_answer_is_silent() -> None:
    """A booking whose travellers we cannot list is a receipt nobody can print,
    so the request stands in — including its nested phone and its own spelling
    of the document keys."""
    result = await _book(_answer(passengers=[]))

    (traveler,) = result.travelers
    assert traveler.first_name == "Azimjon"
    assert traveler.middle_name == "Kamoliddin"
    assert traveler.document_number == "FA2145157"
    assert traveler.email == "yusufovazimjon@gmail.com"
    assert traveler.phone == "998998328192"
    assert traveler.provider_traveler_id is None


async def test_travellers_are_numbered_from_one() -> None:
    result = await _book(
        _answer(
            passengers=[
                {"passenger_type": "ADT", "last_name": "ONE"},
                {"passenger_type": "CHD", "last_name": "TWO"},
            ]
        )
    )

    assert [(person.position, person.last_name) for person in result.travelers] == [
        (1, "ONE"),
        (2, "TWO"),
    ]


async def test_a_traveller_becomes_the_json_the_row_stores() -> None:
    """The stored shape is ours end to end — which is what makes an order
    anonymisable at all (``O10``)."""
    result = await _book(GTS_BOOKING)

    stored = result.travelers[0].as_dict()
    assert stored["document"] == {
        "type": "NP",
        "number": "FA2145157",
        "issue_date": "2019-05-30",
        "expire_date": "2029-05-29",
    }
    assert stored["anonymized_at"] is None


# --- identity, status and refusal -----------------------------------------------------


async def test_the_identifiers_come_out_of_the_inner_data() -> None:
    """The answer is two layers deep: reading the top level leaves every
    identifier ``None``, which is the bug that made every cancellation a 404
    before the shape was recorded (STATUS.md №84)."""
    result = await _book(GTS_BOOKING)

    assert result.provider_order_number == "61453"
    assert result.provider_order_uid == "cd3f1e7bfde940f8bea03cde13f07dfd"
    assert result.provider_pnr == "UBPLKW"
    assert result.provider_status == "BO"
    assert result.status is OrderStatus.BOOKED
    assert result.raw == GTS_BOOKING


async def test_a_flat_answer_is_read_too() -> None:
    """An older shape in the same collection returns the order flat. Reading a
    flat answer wrongly costs nothing; missing a nested one costs the ability
    to cancel."""
    flat = dict(GTS_BOOKING["data"])

    result = await FlightAdapter().book(RecordingClient(flat), BOOKING_REQUEST)

    assert result.provider_order_number == "61453"


@pytest.mark.parametrize(
    "code", ["PW", "TI", "TE", "CB", "VO", "RF", "PRF", "STATUS_VOID", "", "WHAT"]
)
async def test_a_code_that_is_not_a_held_seat_is_not_a_booking(code: str) -> None:
    """An answer that reads perfectly is still not a reservation when GTS calls
    the order something other than held.

    It used to become ``booked`` either way — an unknown code by the default, a
    known one by a status ``created`` cannot even reach, which made
    ``confirm_booking`` answer the customer ``409`` over a live seat. Now the
    order stays ``created`` and reconciliation asks GTS what it really is.

    Nothing is thrown away: the identifiers ride on the exception, so the row
    can still name the seat it may be holding.
    """
    with pytest.raises(UnreadableAnswer) as refused:
        await _book(_answer(status=code))

    partial = refused.value.partial
    assert partial is not None
    assert partial.provider_status == (code or None)
    assert partial.provider_order_number == "61453"
    assert partial.total is not None


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        pytest.param({"order_number": None}, "nothing to cancel by", id="no-number"),
        pytest.param({"price_info": None}, "nothing to charge", id="no-price"),
        pytest.param(
            {"price_info": {"price": 10}}, "no currency to charge in", id="no-currency"
        ),
        pytest.param({"price_info": {"currency": "UZS"}}, "no amount", id="no-amount"),
    ],
)
async def test_an_answer_that_names_no_order_is_refused(
    overrides: dict[str, Any], why: str
) -> None:
    """Not an ``UpstreamError``: GTS agreed, so a real seat is probably held.
    The whole answer rides on the exception because it is the only evidence of
    it, and the caller files a row a person can work through rather than a row
    claiming to be booked for an unknown amount."""
    answer = _answer(**overrides)

    with pytest.raises(UnreadableAnswer) as refused:
        await _book(answer)

    assert refused.value.raw == answer


# --- the live answer ------------------------------------------------------------------


def test_the_live_answer_reads_where_it_actually_keeps_things() -> None:
    """The whole point of the fixture. Every one of these used to be ``None``
    against live GTS, and the price being one of them is what sent two real
    bookings to a state nobody could get them out of."""
    partial = _partial(GTS_LIVE_ORDER)

    assert partial.missing() == ()
    assert partial.provider_order_number == "1"
    assert partial.provider_pnr == "L9J87M"
    assert partial.provider_status == "STATUS_VOID"
    assert partial.total is not None
    assert partial.total.amount == Decimal("320.12")
    assert partial.total.currency == "USD"
    assert partial.ticket_time_limit_at == dt.datetime(
        2023, 9, 13, 21, 43, tzinfo=dt.UTC
    )


def _priced_at(answer: dict[str, Any]) -> Decimal | None:
    total = _partial(answer).total
    return None if total is None else total.amount


def test_the_order_layer_outranks_the_offer_it_was_made_from() -> None:
    """Both are in the same answer and they disagree: the order costs 320.12
    and the offer beneath it says 252 plus a fee of 1.02. What the customer
    owes is the order's own figure — charging the offer's would undercharge
    every booking — and the offer is only read when the order names no price.
    """
    assert _priced_at(GTS_LIVE_ORDER) == Decimal("320.12")
    without_flat = {
        key: value for key, value in GTS_LIVE_ORDER.items() if key != "price"
    }

    assert _priced_at(without_flat) == Decimal("253.02")


def test_the_live_route_comes_out_of_the_offer() -> None:
    """``routes`` is under ``offer`` here, not beside the order. Reading only
    the outer level left the card with no route, no dates and no stops."""
    route = _partial(GTS_LIVE_ORDER).route

    assert route is not None
    assert route.summary == "IST-AYT"
    out, back = route.directions
    assert (out.origin, out.destination) == ("IST", "AYT")
    assert out.stops == 1
    # ``GMT+5:00`` is the same offset as ``UTC+5``, spelled the other way.
    assert out.departure_at == dt.datetime(
        2023, 4, 4, 13, 0, tzinfo=dt.timezone(dt.timedelta(hours=5))
    )
    # Day-first dates, and a whole datetime sitting in the ``arrival_time``
    # field of the very next segment.
    assert out.arrival_at == dt.datetime(2023, 4, 4, 17, 40, tzinfo=dt.UTC)
    # The second direction names no ``direction`` string at all, so its ends
    # come from the segments alone.
    assert (back.origin, back.destination) == ("AYT", "IST")


def test_the_live_journey_spans_both_directions() -> None:
    partial = _partial(GTS_LIVE_ORDER)

    assert partial.travel_start_at == dt.datetime(
        2023, 4, 4, 13, 0, tzinfo=dt.timezone(dt.timedelta(hours=5))
    )
    assert partial.travel_end_at == dt.datetime(2023, 4, 5, 8, 30, tzinfo=dt.UTC)


def test_the_live_traveller_survives_both_spellings() -> None:
    """``middlename`` without its underscore, and a gender spelled out. Both
    were silently dropped — the second by a one-character length limit."""
    (person,) = _partial(GTS_LIVE_ORDER).travelers

    assert person.first_name == "Elmurod"
    assert person.last_name == "Egamberdiyev"
    assert person.middle_name == "Hojiakbar o'g'li"
    assert person.gender == "M"
    assert person.type == "ADT"
    assert person.citizenship == "UZ"
    assert person.document_type == "PSP"
    assert person.document_number == "AA1111111"
    assert person.document_expiry_date == "2030-12-10"
    assert person.email == "test@mail.uz"
    assert person.provider_traveler_id == "db6f7d06-53c7-11ee-9874-f4b520498588"


def test_an_airport_name_is_never_read_as_a_code() -> None:
    """``departure_airport`` carries the code in the live answer and the
    airport's *name* in the collection's. Only a three-letter code is taken, so
    the shape that spells the name out falls back to the ``direction`` label
    rather than putting "Ankara" where ``ESB`` belongs."""
    named = _answer(
        routes=[
            {
                "direction": "IST-ESB",
                "segments": [
                    {
                        "departure_airport": "Turkey",
                        "arrival_airport": "Ankara",
                        "departure_date": "2026-10-01",
                    }
                ],
            }
        ]
    )

    route = _partial(named).route

    assert route is not None
    (leg,) = route.directions
    assert (leg.origin, leg.destination) == ("IST", "ESB")


# --- asking again ---------------------------------------------------------------------


def _page(*orders: dict[str, Any]) -> dict[str, Any]:
    """A ``/v1/orders/list/`` page around the given rows."""
    return {"count": len(orders), "next": None, "previous": None, "results": [*orders]}


async def _retrieve(page: dict[str, Any], number: str = "1") -> Any:
    return await FlightAdapter().retrieve(RecordingClient(page), number)


async def test_retrieving_reads_the_order_out_of_the_page() -> None:
    """The same reader as ``book``. That is the point of it being one function:
    the two answers have the same shape and reading them in two places is how
    spellings drift apart."""
    held = {**GTS_LIVE_ORDER, "status": "STATUS_BOOK"}

    result = await _retrieve(_page(held))

    assert result is not None
    assert result.provider_order_number == "1"
    assert result.provider_pnr == "L9J87M"
    assert result.total.amount == Decimal("320.12")
    assert result.status is OrderStatus.BOOKED


async def test_retrieving_asks_by_number_and_checks_the_answer_again() -> None:
    """A filter GTS quietly ignores would hand back the first order on the
    page. Confirming a booking against somebody else's reservation is the one
    mistake this sweep exists to prevent, so the number is matched here too."""
    client = RecordingClient(_page({**GTS_LIVE_ORDER, "status": "STATUS_BOOK"}))

    assert await FlightAdapter().retrieve(client, "999") is None
    (path, params, _) = client.calls[0]
    assert path == "/v1/orders/list/"
    assert params["order_number"] == 999


async def test_an_order_gts_does_not_list_is_not_a_refusal() -> None:
    """``None``, not an exception. GTS may simply not list it yet, and calling
    a booking dead on a silence is how a paid-for seat disappears."""
    assert await _retrieve(_page()) is None


async def test_a_retrieved_order_that_is_not_held_still_carries_its_identifiers() -> (
    None
):
    """The live capture is a voided order. Refusing it is right; losing the
    number while refusing it would leave the sweep nothing to ask about."""
    with pytest.raises(UnreadableAnswer) as refused:
        await _retrieve(_page(GTS_LIVE_ORDER))

    assert refused.value.partial is not None
    assert refused.value.partial.provider_order_number == "1"
    assert refused.value.partial.provider_status == "STATUS_VOID"


def test_rereading_gets_an_identifier_back_out_of_a_stored_answer() -> None:
    """No call at all. An order recorded before a spelling was known has the
    whole answer on the row, and today's reader can name the seat that
    yesterday's could not (STATUS.md §8.15)."""
    assert FlightAdapter().reread(GTS_LIVE_ORDER).provider_order_number == "1"
    assert FlightAdapter().reread({"nothing": "useful"}).provider_order_number is None


# --- cancelling -----------------------------------------------------------------------


async def test_cancelling_reads_the_code_out_of_its_own_key() -> None:
    """The recorded cancel answer has **no ``data``** — it puts the order under
    ``order``, and the bare-``data`` reader refuses anything without one. That
    would have made every live cancellation a 502 (STATUS.md §8.15a)."""
    answer = {
        "status": "success",
        "code": 100,
        "order": {"order_number": 61453, "status": "CB"},
    }
    client = RecordingClient(answer, envelope=False)

    result = await FlightAdapter().cancel(client, {"order_number": 61453})

    assert result.provider_status == "CB"
    assert result.raw == answer


async def test_cancelling_needs_nothing_from_the_answer() -> None:
    """The client raises on a refusal, so reaching this line means the seat is
    released whether or not GTS bothered to name a status. Insisting on one
    would turn a successful cancellation into an error."""
    result = await FlightAdapter().cancel(
        RecordingClient({"status": "success"}, envelope=False), {"order_number": 1}
    )

    assert result.provider_status is None


# --- repricing and ticketing ---------------------------------------------------


GTS_TICKETED: dict[str, Any] = {
    "status": "success",
    "code": 100,
    # **``order``, not ``data``.** The recorded ticketing answer nests the order
    # under a different key than booking does, and the bare-``data`` reader
    # would call a successful ticketing a 502 (STATUS.md §8.15a).
    "order": {
        "status": "TI",
        "passengers": [
            {
                "passenger_type": "ADT",
                "first_name": "AZIMJON",
                "last_name": "YUSUFOV",
                "passenger_id": "4faa37bc-91d1-4da1-ba3d-b22ef8ec8802",
                "ticket_number": "7653081297644",
            }
        ],
    },
}


async def test_ticketing_reads_the_order_out_of_its_own_key() -> None:
    client = RecordingClient(GTS_TICKETED, envelope=False)

    result = await FlightAdapter().ticket(client, "61453")

    path, sent, timeout = client.calls[0]
    assert path == "/v1/content/ticketing/"
    # The integer GTS spells it with, and the deposit it charges.
    assert sent == {"order_number": 61453, "payment_method": "deposit"}
    assert result.provider_status == "TI"
    assert result.status is OrderStatus.TICKETED
    (traveler,) = result.travelers
    assert traveler.ticket_number == "7653081297644"
    assert traveler.last_name == "YUSUFOV"


async def test_repricing_reads_what_the_order_costs_now() -> None:
    client = RecordingClient(
        {
            "status": "success",
            "code": 200,
            "data": {"price_info": {"price": 243.28, "currency": "UZS"}},
        },
        envelope=False,
    )

    result = await FlightAdapter().reprice(client, "430")

    assert client.calls[0][0] == "/v1/content/reprice_check/"
    assert client.calls[0][1] == {"order_number": 430}
    assert result.total.amount == Decimal("243.28")
    assert result.total.currency == "UZS"


async def test_a_reprice_that_names_no_price_is_refused() -> None:
    client = RecordingClient({"status": "success", "data": {}}, envelope=False)

    with pytest.raises(UnreadableAnswer):
        await FlightAdapter().reprice(client, "430")


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        pytest.param(
            UpstreamTimeout("GTS did not answer in time"),
            FailureClass.RETRYABLE,
            id="timeout",
        ),
        pytest.param(
            UpstreamError(
                "BOOKING: save_booking 403: user don't have enough credits on account"
            ),
            FailureClass.DEPOSIT,
            id="our-own-balance",
        ),
        pytest.param(
            UpstreamError("Service temporarily unavailable"),
            FailureClass.RETRYABLE,
            id="upstream-busy",
        ),
        pytest.param(
            UpstreamError("Fare is no longer available"),
            FailureClass.TERMINAL,
            id="fare-gone",
        ),
        pytest.param(
            UpstreamError("something nobody has seen before"),
            FailureClass.TERMINAL,
            id="unrecognised-is-terminal",
        ),
    ],
)
async def test_failures_are_sorted_into_retry_alarm_or_give_up(
    failure: Any, expected: FailureClass
) -> None:
    """An empty deposit and a fare that no longer exists are both "no ticket",
    and treating them the same would refund a whole day of customers for an
    accounting problem a top-up fixes in a minute (``O5``).

    Unrecognised is **terminal** on purpose: refunding for a reason nobody
    understands is recoverable, retrying past the hold is not.
    """
    assert FlightAdapter().classify(failure) is expected
