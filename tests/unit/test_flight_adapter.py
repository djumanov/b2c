"""The flight adapter — a pipe that still checks what enters and leaves it.

Our flight API **is** the GTS API (API.md §20): no field map exists to test.
What is pinned instead: the body goes through **verbatim** (extra fields and
all), junk fails with our 422 *before* a GTS session is spent, and an answer
without a ``request_id`` is refused rather than passed along.
"""

from typing import Any

import pytest

from app.api.errors import UpstreamError, ValidationFailed
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
    ``post_envelope`` gets the same data wrapped in a GTS envelope whose
    status is ``self.status``.
    """

    def __init__(self, data: dict[str, Any], *, status: str = "success") -> None:
        self.data = data
        self.status = status
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None,
    ) -> dict[str, Any]:
        raise AssertionError("the search flow never GETs")

    async def post(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        self.calls.append((path, json, timeout))
        return self.data

    async def post_envelope(
        self, path: str, *, json: dict[str, Any], timeout: float | None
    ) -> dict[str, Any]:
        self.calls.append((path, json, timeout))
        return {"status": self.status, "message": "…", "code": 0, "data": self.data}


def test_the_adapter_satisfies_the_port() -> None:
    adapter: ProductAdapter = FlightAdapter()

    assert adapter.code == "flight"
    assert adapter.supports() == {FlowStep.SEARCH, FlowStep.OFFERS}


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
