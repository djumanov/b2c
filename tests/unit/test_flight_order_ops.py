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

from app.modules.orders.states import OrderStatus
from app.providers.products.flight import FlightAdapter
from app.providers.products.orders import OrderOperations, UnreadableAnswer
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
    """GTS.md §4 lists eight codes; a code with no entry would silently become
    the default and turn a voided ticket into a booked one."""
    assert set(FlightAdapter().status_map()) == {
        "BO",
        "PW",
        "TI",
        "TE",
        "CB",
        "VO",
        "RF",
        "PRF",
    }
    assert set(FlightAdapter().status_map().values()) <= set(OrderStatus)


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


@pytest.mark.parametrize("routes", [None, [], "later", [{"segments": None}]])
async def test_a_journey_we_cannot_read_costs_nothing(routes: Any) -> None:
    """Cosmetic fields must never be able to lose a booking."""
    result = await _book(_answer(routes=routes))

    assert result.provider_order_number == "61453"


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
    "code", ["PW", "TI", "TE", "CB", "VO", "RF", "PRF", "", "WHAT"]
)
async def test_a_code_we_do_not_know_still_leaves_a_reservation(code: str) -> None:
    """An answer we could read at all is a reservation. The code is recorded
    beside our status rather than trusted over it, and an unknown one falls back
    to ``booked`` — the state that keeps the deadline running."""
    result = await _book(_answer(status=code))

    assert result.provider_status == (code or None)
    assert result.status is FlightAdapter().status_map().get(code, OrderStatus.BOOKED)


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


# --- cancelling -----------------------------------------------------------------------


async def test_cancelling_reads_the_code_and_keeps_the_answer() -> None:
    client = RecordingClient({"data": {"order_number": 61453, "status": "CB"}})

    result = await FlightAdapter().cancel(client, {"order_number": 61453})

    assert result.provider_status == "CB"
    assert result.raw == {"data": {"order_number": 61453, "status": "CB"}}


async def test_cancelling_needs_nothing_from_the_answer() -> None:
    """The client raises on a refusal, so reaching this line means the seat is
    released whether or not GTS bothered to name a status. Insisting on one
    would turn a successful cancellation into an error."""
    result = await FlightAdapter().cancel(RecordingClient({}), {"order_number": 1})

    assert result.provider_status is None
