"""The flight adapter — a pipe that still checks what enters and leaves it.

Our flight API **is** the GTS API (API.md §20): no field map exists to test.
What is pinned instead: the body goes through **verbatim** (extra fields and
all), junk fails with our 422 *before* a GTS session is spent, and an answer
without a ``request_id`` is refused rather than passed along.
"""

from typing import Any

import pytest

from app.api.errors import UpstreamError, UpstreamTimeout, ValidationFailed
from app.providers.gts.base import GtsTimeouts
from app.providers.products.base import FlowStep, ProductAdapter
from app.providers.products.flight import FlightAdapter

SEARCH_BODY: dict[str, Any] = {
    "directions": [
        {"departure": "TAS", "arrival": "IST", "departure_date": "2026-09-14"}
    ],
    "adt": 1,
    "chd": 0,
    "inf": 0,
    "ins": 0,
    "class": "E",
    "direct": False,
}


class RecordingClient:
    """A ``GtsClient`` that remembers the one call made through it.

    ``post`` answers with the bare data (like the real client); a caller of
    ``post_envelope`` gets the same data wrapped in a GTS envelope whose status
    is ``self.status``.

    ``envelope=False`` turns that wrapping off, for the recorded answers that
    **are** the envelope — ticketing puts the order under ``order`` beside its
    own ``status``, and wrapping one of those again would test a shape GTS
    never sends.

    ``raises`` makes the call fail the way the real client does, **after** the
    call is recorded: the steps that hide an upstream failure still have to
    have asked GTS, and a fake that never reached the wire could not tell the
    two apart.
    """

    def __init__(
        self,
        data: dict[str, Any],
        *,
        status: str = "success",
        envelope: bool = True,
        raises: Exception | None = None,
    ) -> None:
        self.data = data
        self.status = status
        self.envelope = envelope
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None,
    ) -> dict[str, Any]:
        raise AssertionError("the search flow never GETs")

    async def get_envelope(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None,
    ) -> dict[str, Any]:
        """A read whose page is not under ``data`` — the orders list."""
        self.calls.append((path, params or {}, timeout))
        if self.raises is not None:
            raise self.raises
        if not self.envelope:
            return self.data
        return {"status": self.status, "message": "…", "code": 0, "data": self.data}

    async def post(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        self.calls.append((path, json, timeout))
        if self.raises is not None:
            raise self.raises
        return self.data

    async def post_envelope(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        self.calls.append((path, json, timeout))
        if self.raises is not None:
            raise self.raises
        if not self.envelope:
            return self.data
        return {"status": self.status, "message": "…", "code": 0, "data": self.data}


def test_the_adapter_satisfies_the_port() -> None:
    adapter: ProductAdapter = FlightAdapter()

    assert adapter.code == "flight"
    assert adapter.supports() == {
        FlowStep.SEARCH,
        FlowStep.OFFERS,
        FlowStep.UPSELL,
        FlowStep.VERIFY,
        FlowStep.BOOKING,
    }


# --- search --------------------------------------------------------------------------


async def test_the_body_is_forwarded_verbatim() -> None:
    """Extra fields ride along: GTS's contract grows without waiting for us."""
    client = RecordingClient({"request_id": "r-1"})
    body = {**SEARCH_BODY, "flexible": True, "airlines": ["HY"], "new_field": 7}

    result = await FlightAdapter().search(client, body)

    path, sent, timeout = client.calls[0]
    assert path == "/v1/content/search/"
    assert sent is body  # the same object — no copy, no reshaping
    assert timeout == GtsTimeouts.SEARCH_SECONDS
    assert result == {"request_id": "r-1"}


async def test_the_answer_is_returned_as_gts_sent_it() -> None:
    client = RecordingClient({"request_id": "r-2", "extra": {"anything": True}})

    result = await FlightAdapter().search(client, dict(SEARCH_BODY))

    assert result == {"request_id": "r-2", "extra": {"anything": True}}


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        ({"directions": []}, "directions"),
        ({"directions": None}, "directions"),
        ({"adt": 0}, "adt"),
        ({"adt": "two"}, "adt"),
        (
            {
                "directions": [
                    {
                        "departure": "TA",
                        "arrival": "IST",
                        "departure_date": "2026-09-14",
                    }
                ]
            },
            "directions.0.departure",
        ),
        (
            {
                "directions": [
                    {
                        "departure": "TAS",
                        "arrival": "IST",
                        "departure_date": "not-a-date",
                    }
                ]
            },
            "directions.0.departure_date",
        ),
    ],
)
async def test_junk_fails_before_a_session_is_spent(
    mutation: dict[str, Any], field: str
) -> None:
    client = RecordingClient({"request_id": "r-1"})

    with pytest.raises(ValidationFailed) as caught:
        await FlightAdapter().search(client, {**SEARCH_BODY, **mutation})

    assert caught.value.field == field
    assert client.calls == []


async def test_an_answer_without_a_request_id_is_refused() -> None:
    client = RecordingClient({"status_of_search": "started"})

    with pytest.raises(UpstreamError):
        await FlightAdapter().search(client, dict(SEARCH_BODY))


# --- offers --------------------------------------------------------------------------


async def test_offers_are_passed_through_with_the_search_status() -> None:
    gts_answer = {
        "request_id": "r-1",
        "next_token": "t-2",
        "count": 41,
        "trip_type": "RT",
        "offers": [{"offer_id": "o-1", "price_info": {"price": 221.86}}],
    }
    client = RecordingClient(gts_answer, status="success")
    params = {
        "request_id": "r-1",
        "next_token": None,
        "sort_type": "price",
        "limit": 20,
    }

    result = await FlightAdapter().offers(client, params)

    path, sent, timeout = client.calls[0]
    assert path == "/v1/content/offers/"
    assert sent is params
    assert timeout == GtsTimeouts.SEARCH_SECONDS
    assert result == {**gts_answer, "search_status": "success"}


async def test_a_running_search_reports_in_process_with_partial_offers() -> None:
    """GTS answers ``status: "In process"`` with results already inside —
    that is a state to relay, never an error (observed live, 2026-08-12)."""
    client = RecordingClient(
        {"request_id": "r-1", "offers": [{"offer_id": "o-1"}], "next_token": "t"},
        status="In process",
    )

    result = await FlightAdapter().offers(client, {"request_id": "r-1"})

    assert result["search_status"] == "In process"
    assert result["offers"] == [{"offer_id": "o-1"}]


async def test_offers_without_a_request_id_fail_before_gts() -> None:
    client = RecordingClient({})

    with pytest.raises(ValidationFailed) as caught:
        await FlightAdapter().offers(client, {"next_token": None, "limit": 20})

    assert caught.value.field == "request_id"
    assert client.calls == []


@pytest.mark.parametrize(
    "failure",
    [
        UpstreamError("no providers", upstream_code=-21),
        UpstreamError("GTS is unreachable: connection refused"),
        UpstreamError("GTS returned an unexpected status", upstream_code=500),
        UpstreamTimeout("GTS did not answer in time"),
    ],
    ids=["gts_error", "unreachable", "http_500", "timeout"],
)
async def test_an_upstream_failure_is_an_empty_page_not_an_error(
    failure: Exception,
) -> None:
    """API.md §20: a page we could not fetch must not end the search.

    Every way GTS can disappoint lands on the same answer, because the client
    cannot act on the difference — it can only ask again.
    """
    client = RecordingClient({}, raises=failure)
    params = {"request_id": "r-1", "next_token": "t-2", "limit": 20}

    result = await FlightAdapter().offers(client, params)

    assert client.calls[0][0] == "/v1/content/offers/"
    assert result == {
        "request_id": "r-1",
        # The *same* page is asked for again: advancing past a failure would
        # skip the offers it was carrying.
        "next_token": "t-2",
        "count": 0,
        "offers": [],
        "search_status": "In process",
    }


async def test_a_hidden_failure_still_refuses_a_body_without_a_request_id() -> None:
    """The softening is about GTS, not about us: our own 422 survives it."""
    client = RecordingClient({}, raises=UpstreamError("no providers"))

    with pytest.raises(ValidationFailed):
        await FlightAdapter().offers(client, {"limit": 20})

    assert client.calls == []


# --- upsell --------------------------------------------------------------------------


async def test_upsell_is_passed_through_with_the_search_status() -> None:
    """GTS's own ``status``/``code`` sit *inside* ``data`` and must survive
    next to our ``search_status`` — nobody gets to deduplicate them."""
    gts_answer = {
        "request_id": "r-1",
        "status": "success",
        "code": "100",
        "trip_type": "OW",
        "currency": "USD",
        "offers": [
            {"offer_id": "u-1", "price_info": {"price": 108.67}},
            {"offer_id": "u-2", "price_info": {"price": 158.67}},
        ],
    }
    client = RecordingClient(gts_answer, status="success")
    payload = {"request_id": "r-1", "offer_id": "o-1", "new_field": 7}

    result = await FlightAdapter().upsell(client, payload)

    path, sent, timeout = client.calls[0]
    assert path == "/v1/content/upsell/"
    assert sent is payload
    assert timeout == GtsTimeouts.SEARCH_SECONDS
    assert result == {**gts_answer, "search_status": "success"}


async def test_an_upsell_during_a_running_search_is_relayed_not_failed() -> None:
    """An offer can carry ``upsell: true`` while the search still polls —
    the envelope's status is a state here too, exactly as for offers."""
    client = RecordingClient(
        {"request_id": "r-1", "offers": [{"offer_id": "u-1"}]},
        status="In process",
    )

    result = await FlightAdapter().upsell(
        client, {"request_id": "r-1", "offer_id": "o-1"}
    )

    assert result["search_status"] == "In process"
    assert result["offers"] == [{"offer_id": "u-1"}]


async def test_an_upstream_failure_leaves_upsell_with_an_empty_list() -> None:
    """Fare variants are still part of choosing, so a failure to fetch them is
    an empty list and another poll — the chosen offer is bookable as it is."""
    client = RecordingClient({}, raises=UpstreamError("no variants"))

    result = await FlightAdapter().upsell(
        client, {"request_id": "r-1", "offer_id": "o-1"}
    )

    assert client.calls[0][0] == "/v1/content/upsell/"
    assert result == {
        "request_id": "r-1",
        "offers": [],
        "search_status": "In process",
    }


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "request_id"),
        ({"request_id": ""}, "request_id"),
        ({"request_id": "r-1"}, "offer_id"),
        ({"request_id": "r-1", "offer_id": ""}, "offer_id"),
    ],
)
async def test_upsell_junk_fails_before_a_session_is_spent(
    payload: dict[str, Any], field: str
) -> None:
    client = RecordingClient({})

    with pytest.raises(ValidationFailed) as caught:
        await FlightAdapter().upsell(client, payload)

    assert caught.value.field == field
    assert client.calls == []


# --- verify --------------------------------------------------------------------------


async def test_verify_is_passed_through_with_the_search_status() -> None:
    gts_answer = {
        "status": "success",
        "request_id": "r-1",
        "offer_id": "o-1",
        "code": "100",
        "verified": True,
    }
    client = RecordingClient(gts_answer, status="success")
    payload = {"request_id": "r-1", "offer_id": "o-1", "new_field": 7}

    result = await FlightAdapter().verify(client, payload)

    path, sent, timeout = client.calls[0]
    assert path == "/v1/content/verify/"
    assert sent is payload
    assert timeout == GtsTimeouts.SEARCH_SECONDS
    assert result == {**gts_answer, "search_status": "success"}


async def test_a_verify_during_a_running_search_is_relayed_not_failed() -> None:
    client = RecordingClient(
        {"request_id": "r-1", "offer_id": "o-1", "verified": True},
        status="In process",
    )

    result = await FlightAdapter().verify(
        client, {"request_id": "r-1", "offer_id": "o-1"}
    )

    assert result["search_status"] == "In process"
    assert result["verified"] is True


async def test_verify_does_not_hide_an_upstream_failure() -> None:
    """Where the softening stops (API.md §20).

    By ``verify`` one offer has been chosen, and answering "nothing here, ask
    again" about it would be a lie the customer books on.
    """
    client = RecordingClient({}, raises=UpstreamError("offer expired"))

    with pytest.raises(UpstreamError):
        await FlightAdapter().verify(client, {"request_id": "r-1", "offer_id": "o-1"})


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "request_id"),
        ({"request_id": ""}, "request_id"),
        ({"request_id": "r-1"}, "offer_id"),
        ({"request_id": "r-1", "offer_id": ""}, "offer_id"),
    ],
)
async def test_verify_junk_fails_before_a_session_is_spent(
    payload: dict[str, Any], field: str
) -> None:
    client = RecordingClient({})

    with pytest.raises(ValidationFailed) as caught:
        await FlightAdapter().verify(client, payload)

    assert caught.value.field == field
    assert client.calls == []


# --- booking -------------------------------------------------------------------------


#: The least an answer needs before it is an order: something to cancel by and
#: something to charge. What the adapter makes of a full one is
#: ``test_flight_order_ops.py``'s subject.
BOOKED_ANSWER: dict[str, Any] = {
    "data": {
        "order_number": 1250,
        "status": "BO",
        "price_info": {"price": 221.86, "currency": "USD"},
    }
}


async def test_booking_sends_the_body_untouched_and_keeps_the_answer_whole() -> None:
    """The request is still forwarded object-for-object, and the provider's
    answer still travels whole — now beside our reading of it rather than
    instead of it (order-system/03-design.md ``O4``)."""
    client = RecordingClient(BOOKED_ANSWER)
    payload = {
        "request_id": "r-1",
        "offer_id": "o-1",
        "passengers": [{"first_name": "ALI", "last_name": "VALIYEV"}],
        "save_passenger": True,
    }

    result = await FlightAdapter().book(client, payload)

    path, sent, timeout = client.calls[0]
    assert path == "/v1/content/booking/"
    assert sent is payload  # the same object — passengers and all
    assert timeout == GtsTimeouts.DEFAULT_SECONDS
    assert result.raw == BOOKED_ANSWER
    assert result.provider_order_number == "1250"


async def test_booking_does_not_borrow_the_search_timeout() -> None:
    """API.md §12: 40 s is for a fan-out search, 15 s for everything else."""
    client = RecordingClient(BOOKED_ANSWER)

    await FlightAdapter().book(client, {"request_id": "r-1", "offer_id": "o-1"})

    assert client.calls[0][2] == GtsTimeouts.DEFAULT_SECONDS
    assert GtsTimeouts.DEFAULT_SECONDS != GtsTimeouts.SEARCH_SECONDS


async def test_passenger_fields_are_not_second_guessed() -> None:
    """Which passenger fields GTS insists on is GTS's contract to state, not
    ours to guess (STATUS.md, 2-faza kuzatuvi) — an empty list still goes."""
    client = RecordingClient(BOOKED_ANSWER)

    await FlightAdapter().book(
        client, {"request_id": "r-1", "offer_id": "o-1", "passengers": []}
    )

    assert client.calls[0][1]["passengers"] == []


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({}, "request_id"),
        ({"request_id": ""}, "request_id"),
        ({"request_id": "r-1"}, "offer_id"),
        ({"request_id": "r-1", "offer_id": ""}, "offer_id"),
    ],
)
async def test_booking_junk_fails_before_a_session_is_spent(
    payload: dict[str, Any], field: str
) -> None:
    client = RecordingClient({})

    with pytest.raises(ValidationFailed) as caught:
        await FlightAdapter().book(client, payload)

    assert caught.value.field == field
    assert client.calls == []


# --- cancel --------------------------------------------------------------------------


async def test_cancel_sends_the_body_untouched_and_keeps_the_answer_whole() -> None:
    # ``envelope=False``: the recorded cancel answer *is* the envelope — it puts
    # the order under ``order`` beside its own ``status`` and has no ``data``
    # key at all, which is why the adapter reads the whole payload.
    gts_answer = {"status": "success", "code": 100, "order": {"status": "CB"}}
    client = RecordingClient(gts_answer, envelope=False)
    payload = {"order_id": "1250"}

    result = await FlightAdapter().cancel(client, payload)

    path, sent, timeout = client.calls[0]
    assert path == "/v1/content/cancel/"
    assert sent is payload
    assert timeout == GtsTimeouts.DEFAULT_SECONDS
    assert result.raw == gts_answer
    # Their code is read but not obeyed: reaching here means the seat is
    # released, so the order goes to ``cancelled`` either way.
    assert result.provider_status == "CB"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"booking_id": "1250"},
        {"order_number": 1250, "anything": {"at": "all"}},
    ],
)
async def test_cancel_forwards_a_body_it_has_never_seen(
    payload: dict[str, Any],
) -> None:
    """The one step with no shape check. Which field names the booking is not
    written down anywhere we control, and refusing a valid cancellation before
    GTS sees it costs a real seat — so every body goes through (API.md §20)."""
    client = RecordingClient({"status": "CB"})

    await FlightAdapter().cancel(client, payload)

    assert client.calls[0][1] is payload
