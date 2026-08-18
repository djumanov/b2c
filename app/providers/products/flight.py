"""The flight vertical — a passthrough adapter over GTS ``/v1/content/``.

Our flight API **is** the GTS API (API.md §20, decision of 2026-08-12): the
client sends GTS's own field names, we forward the body verbatim after a light
shape check, and GTS's ``data`` comes back untouched. The adapter therefore
holds no field map — only the two things a pipe still owes its caller:

* **Validation before the session.** A body that cannot possibly be a search
  fails with our ``422`` and a field name, before a GTS session is spent on it.
  The check is deliberately loose — unknown fields pass through, because GTS
  is the validator of record and its contract grows without waiting for us.
* **A shape check on the way back.** ``search/`` without a ``request_id`` is
  not an answer, whatever the envelope said.

**Booking is where the pipe ends.** Everything up to ``verify`` forwards and
relays; from ``book`` onwards the adapter also implements ``OrderOperations``
and answers in our own types, because the saga must never read a key out of
GTS's dictionaries (``providers/products/orders.py`` explains why at length).
The provider's answer still travels whole in ``raw`` and is still what the wire
publishes — the translation is an addition, not a replacement.

Reading that answer is deliberately forgiving in one direction and strict in
the other. Field spellings differ between GTS's recorded calls (``document.
number`` in one, ``document.passport_number`` in another), so every reader
tries both and a missing optional costs nothing. But an answer with **no order
number or no price** is not an order: it raises ``UnreadableAnswer`` rather
than producing a row that claims to be booked and cannot say for how much.
"""

import datetime as dt
import re
from collections.abc import Mapping
from decimal import InvalidOperation
from typing import Any, Final

import pydantic
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import UpstreamError, ValidationFailed
from app.core.money import Money, quantize, to_decimal
from app.modules.orders.states import OrderStatus
from app.providers.gts.base import GtsClient, GtsTimeouts
from app.providers.products.base import FlowStep, ProductCode
from app.providers.products.orders import (
    BookingResult,
    CancelResult,
    TravelerRef,
    UnreadableAnswer,
)

#: IATA location code — city or airport.
_IATA_PATTERN = r"^[A-Za-z]{3}$"

#: GTS's flight order codes in our vocabulary (GTS.md §4). ``TE`` — *their*
#: ticketing error — lands on the state a person works through: money has moved
#: and no ticket came out of it. The map is only ever read, never authoritative:
#: it says what a code means, not that a transition into it is legal.
_STATUS_MAP: Final[Mapping[str, OrderStatus]] = {
    "BO": OrderStatus.BOOKED,
    "PW": OrderStatus.TICKETING,
    "TI": OrderStatus.TICKETED,
    "TE": OrderStatus.NEEDS_ATTENTION,
    "CB": OrderStatus.CANCELLED,
    "VO": OrderStatus.VOIDED,
    "RF": OrderStatus.REFUNDED,
    "PRF": OrderStatus.PARTIALLY_REFUNDED,
}

#: Below this a bare ``ticket_time_limit`` is read as minutes, above it as
#: seconds. **A guess**, and recorded as one: three spellings have been seen and
#: none is documented — an ISO timestamp in the collection, ``4319`` in a
#: recorded answer, ``288000`` in API.md §20. Both integers are plausible
#: deadlines under exactly one reading each, and this threshold separates them
#: (order-system/03-design.md §3.10, Q1).
_MINUTES_CEILING: Final = 10_000

#: ``UTC+5``, ``UTC-03:30``, ``UTC+0`` — what GTS puts in ``departure_timezone``
#: when it puts anything at all.
_UTC_OFFSET = re.compile(r"UTC([+-])(\d{1,2})(?::?(\d{2}))?$")


class _DirectionIn(BaseModel):
    """One leg, in GTS's own names. Extra keys ride along untouched."""

    model_config = ConfigDict(extra="allow")

    departure: str = Field(pattern=_IATA_PATTERN)
    arrival: str = Field(pattern=_IATA_PATTERN)
    departure_date: dt.date


class _FlightSearchIn(BaseModel):
    """The least a flight search must carry to be worth a GTS session."""

    model_config = ConfigDict(extra="allow")

    directions: list[_DirectionIn] = Field(min_length=1, max_length=6)
    adt: int = Field(ge=1, le=9)
    chd: int = Field(default=0, ge=0, le=9)
    inf: int = Field(default=0, ge=0, le=9)
    ins: int = Field(default=0, ge=0, le=9)


class _FlightOffersIn(BaseModel):
    """Paging is GTS's; we only insist the page belongs to a search."""

    model_config = ConfigDict(extra="allow")

    request_id: str = Field(min_length=1)


class _FlightOfferRefIn(BaseModel):
    """Names one offer of one search — both IDs are GTS's.

    The shape ``upsell`` and ``verify`` share (and ``rules``/``seat-map``
    later, if they keep to GTS's pattern).
    """

    model_config = ConfigDict(extra="allow")

    request_id: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)


def _validated(model: type[BaseModel], payload: dict[str, Any]) -> None:
    """Run the shape check, turning pydantic's error into our catalogue's.

    A bare ``ValidationError`` escaping a service is a 500; this keeps it a
    422 with the offending field named (API.md §3).
    """
    try:
        model.model_validate(payload)
    except pydantic.ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or None
        raise ValidationFailed(first["msg"], field=field) from exc


def _order_body(response: Mapping[str, Any]) -> Mapping[str, Any]:
    """The order itself, out of GTS's two-layer booking answer.

    Our client already strips GTS's envelope, so what arrives is
    ``{"message": "booked", "request_id": …, "data": {…the order…}}`` and the
    order's own fields sit under that inner ``data`` (EASY_GATEWAY collection,
    ``/content/Booking``).

    Falling back to the response itself is not politeness: an older shape in the
    same collection returns the order flat, and reading a flat answer wrongly
    costs nothing while missing a nested one costs the ability to cancel.
    """
    inner = response.get("data")
    return inner if isinstance(inner, dict) else response


def _text(source: Mapping[str, Any], *keys: str, limit: int) -> str | None:
    """The first usable value among several spellings of the same field.

    Several spellings because the recorded calls disagree with each other —
    ``document.number`` in the booking answer, ``document.passport_number`` in
    the retrieve one — and being able to read both costs one loop.

    Numbers are accepted and stringified: ``order_number`` really is an integer
    upstream (``61453``), and an identifier is never arithmetic. Anything longer
    than the column is refused rather than silently truncated — a cut identifier
    would match the wrong row later, which is worse than none.
    """
    for key in keys:
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, str | int):
            continue
        text = str(value).strip()
        if text and len(text) <= limit:
            return text
    return None


def _money(body: Mapping[str, Any]) -> Money | None:
    """What the customer owes, from ``price_info``.

    ``price`` is fare plus taxes and **excludes** the agency fee sitting beside
    it: in the recorded booking, ``46.89 + 5.50`` is exactly the only
    passenger's ``payable_amount``. Their sum is the figure to charge.

    Amounts arrive as JSON floats and ``core.money.to_decimal`` refuses floats
    on purpose — binary fractions are not money. They go through ``str`` first,
    which is the one conversion that keeps the digits that were sent.
    """
    info = body.get("price_info")
    if not isinstance(info, dict):
        return None
    currency = _text(info, "currency", limit=3)
    if currency is None:
        return None
    try:
        amount = to_decimal(str(info["price"]))
        fee = to_decimal(str(info.get("fee_amount") or 0))
        return Money(amount=quantize(amount + fee), currency=currency.upper())
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None


def _deadline(value: object, *, now: dt.datetime) -> dt.datetime | None:
    """``ticket_time_limit`` in any of the three shapes it has been seen in.

    A string is an instant; an integer is a duration from now, in minutes or in
    seconds depending on ``_MINUTES_CEILING``. Unreadable is ``None`` rather
    than a guess — the caller has a configured fallback, and inventing a
    deadline would either cancel a live booking early or let a dead one sit.
    """
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    span = (
        dt.timedelta(minutes=value)
        if value < _MINUTES_CEILING
        else dt.timedelta(seconds=value)
    )
    return now + span


def _zone(raw: object) -> dt.tzinfo:
    """``departure_timezone`` as an offset, or UTC when it says nothing."""
    if isinstance(raw, str):
        found = _UTC_OFFSET.match(raw.strip())
        if found is not None:
            sign = 1 if found[1] == "+" else -1
            offset = dt.timedelta(hours=int(found[2]), minutes=int(found[3] or 0))
            return dt.timezone(sign * offset)
    return dt.UTC


def _departure(segment: Mapping[str, Any]) -> dt.datetime | None:
    """When the first flight leaves, as an instant.

    The date and time are local to the airport and the offset beside them is
    often blank, so a journey whose timezone GTS did not state is read as UTC.
    The column exists for ordering and reminders, not for a boarding pass — the
    exact local time stays in ``raw``.
    """
    date = _text(segment, "departure_date", limit=32)
    if date is None:
        return None
    time = _text(segment, "departure_time", limit=32) or "00:00"
    try:
        naive = dt.datetime.fromisoformat(f"{date}T{time}")
    except ValueError:
        return None
    return naive.replace(tzinfo=_zone(segment.get("departure_timezone")))


def _routes(body: Mapping[str, Any]) -> tuple[dt.datetime | None, str | None]:
    """Departure and a one-line route, out of ``routes[].segments[]``.

    ``TAS-IST / IST-TAS`` — enough to render a list row without opening the
    answer, which is the only reason either of these is a column.
    """
    routes = body.get("routes")
    if not isinstance(routes, list) or not routes:
        return None, None
    legs = [
        _text(route, "direction", limit=32)
        for route in routes
        if isinstance(route, dict)
    ]
    summary = " / ".join(leg for leg in legs if leg) or None

    first = routes[0]
    segments = first.get("segments") if isinstance(first, dict) else None
    if not isinstance(segments, list) or not segments:
        return None, summary
    if not isinstance(segments[0], dict):
        return None, summary
    return _departure(segments[0]), summary


def _phone(source: Mapping[str, Any]) -> str | None:
    """One phone number out of either spelling.

    The booking *request* nests it (``{"phone_code": "998", "phone_number": …}``)
    and the answer flattens it (``phone_number: "+998…"``).
    """
    flat = _text(source, "phone_number", limit=32)
    if flat is not None:
        return flat
    nested = source.get("phone")
    if isinstance(nested, dict):
        code = _text(nested, "phone_code", limit=8) or ""
        number = _text(nested, "phone_number", limit=32) or ""
        return f"{code}{number}" or None
    return _text(source, "phone", limit=32)


def _traveler(position: int, source: Mapping[str, Any]) -> TravelerRef:
    """One traveller, read from either the request or the answer.

    Both are tried by the same reader because both are legitimate sources: the
    answer carries the provider's id and the ticket number, the request carries
    the document dates GTS does not always echo back.
    """
    document = source.get("document")
    document = document if isinstance(document, dict) else {}
    return TravelerRef(
        position=position,
        type=_text(source, "passenger_type", "type", limit=8),
        first_name=_text(source, "first_name", "firstname", limit=64),
        last_name=_text(source, "last_name", "lastname", limit=64),
        middle_name=_text(source, "middle_name", limit=64),
        birth_date=_text(source, "birth_date", limit=32),
        gender=_text(source, "gender", limit=1),
        citizenship=(
            _text(source, "citizenship", limit=8)
            or _text(document, "citizenship", "nationality", limit=8)
        ),
        document_type=_text(document, "type", "document_type", limit=16),
        document_number=_text(document, "number", "passport_number", limit=64)
        or _text(source, "document_number", limit=64),
        document_issue_date=_text(
            document, "issue_date", "passport_issuance", limit=32
        ),
        document_expiry_date=_text(
            document, "expire_date", "passport_expiry", limit=32
        ),
        email=_text(source, "email", "email_address", limit=255),
        phone=_phone(source),
        provider_traveler_id=_text(source, "passenger_id", limit=64),
        ticket_number=_text(source, "ticket_number", limit=32),
    )


def _travelers(
    body: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[TravelerRef, ...]:
    """Who is travelling — from the answer, or from the request if it is silent.

    The answer is preferred because it carries the provider's traveller ids and,
    later, the ticket numbers. The request is the fallback rather than nothing:
    a booking whose travellers we cannot list is a receipt nobody can print.
    """
    for candidate in (body.get("passengers"), payload.get("passengers")):
        if isinstance(candidate, list) and candidate:
            return tuple(
                _traveler(index, person)
                for index, person in enumerate(candidate, start=1)
                if isinstance(person, dict)
            )
    return ()


class FlightAdapter:
    """``ProductAdapter`` and ``OrderOperations`` for ``flight``.

    Both protocols on one object: a vertical is one file and one registry line
    (ARCHITECTURE.md §6), and splitting it in two would only create a second
    place for the list of verticals to disagree with itself.
    """

    code = ProductCode.FLIGHT

    def supports(self) -> frozenset[FlowStep]:
        return frozenset(
            {
                FlowStep.SEARCH,
                FlowStep.OFFERS,
                FlowStep.UPSELL,
                FlowStep.VERIFY,
                FlowStep.BOOKING,
                FlowStep.CANCEL,
            }
        )

    async def search(
        self, client: GtsClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        _validated(_FlightSearchIn, payload)
        data = await client.post(
            "/v1/content/search/", json=payload, timeout=GtsTimeouts.SEARCH_SECONDS
        )
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise UpstreamError("the GTS search returned an unexpected shape")
        return data

    async def offers(self, client: GtsClient, params: dict[str, Any]) -> dict[str, Any]:
        """One page of offers, plus where the search stands.

        GTS reports progress in its envelope's ``status`` — ``"In process"``
        while providers are still answering (with partial results already in
        ``data``), ``"success"`` when done. The envelope is ours to strip, so
        that one field rides on as ``search_status``, verbatim: the client
        polls until it says ``"success"``.
        """
        _validated(_FlightOffersIn, params)
        payload = await client.post_envelope(
            "/v1/content/offers/", json=params, timeout=GtsTimeouts.SEARCH_SECONDS
        )
        data: dict[str, Any] = payload["data"]
        return {**data, "search_status": payload.get("status")}

    async def upsell(
        self, client: GtsClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Fare variants (branded fares) of one offer — new ``offer_id``s.

        An offer arrives flagged ``upsell: true`` while the search may still
        be ``"In process"``, so the envelope's ``status`` is a state here just
        as it is for ``offers`` — hence ``post_envelope`` and the same
        ``search_status`` relay, not a failure on anything short of
        ``"success"``. GTS's own ``status``/``code`` *inside* ``data`` ride
        through untouched.
        """
        _validated(_FlightOfferRefIn, payload)
        envelope = await client.post_envelope(
            "/v1/content/upsell/", json=payload, timeout=GtsTimeouts.SEARCH_SECONDS
        )
        data: dict[str, Any] = envelope["data"]
        return {**data, "search_status": envelope.get("status")}

    async def verify(
        self, client: GtsClient, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Re-check price and availability of one offer before booking.

        The same pipe as ``upsell``: an expired offer comes back as GTS's own
        error and is relayed as ``upstream_error`` — no mapping in between
        (API.md §20, decision of 2026-08-13).
        """
        _validated(_FlightOfferRefIn, payload)
        envelope = await client.post_envelope(
            "/v1/content/verify/", json=payload, timeout=GtsTimeouts.SEARCH_SECONDS
        )
        data: dict[str, Any] = envelope["data"]
        return {**data, "search_status": envelope.get("status")}

    async def book(self, client: GtsClient, payload: dict[str, Any]) -> BookingResult:
        """Book the offer ``verify`` cleared, and say what came back.

        Two departures from the steps above, both deliberate. The request gains
        **nothing**: there is no ``search_status`` here, because booking has no
        "In process" state to report — so ``post``, not ``post_envelope``. And
        the timeout is the ordinary 15 s, not the 40 s a fan-out search needs
        (API.md §12).

        Passengers ride out unchecked. ``_FlightOfferRefIn`` allows extra keys,
        and which passenger fields GTS insists on is GTS's contract to state,
        not ours to guess. They are read back in on the way home, though: the
        answer's travellers are what the order stores.

        **No order number or no price is not an order.** Everything else is
        optional — a booking with no route summary is a cosmetic loss — but
        without those two there is nothing to cancel and nothing to charge, and
        a row claiming otherwise would be worse than the exception.
        """
        _validated(_FlightOfferRefIn, payload)
        raw = await client.post(
            "/v1/content/booking/", json=payload, timeout=GtsTimeouts.DEFAULT_SECONDS
        )
        body = _order_body(raw)
        number = _text(body, "order_number", limit=64)
        total = _money(body)
        if number is None or total is None:
            raise UnreadableAnswer(
                "the GTS booking answer names no order or no price", raw=raw
            )
        code = _text(body, "status", limit=16)
        departure, route = _routes(body)
        return BookingResult(
            provider_order_number=number,
            provider_order_uid=_text(body, "order_uid", limit=64),
            provider_pnr=_text(body, "gds_pnr", limit=32),
            provider_status=code,
            # An answer we could read at all is a reservation, whatever code it
            # carries; the code is kept beside ours rather than trusted over it.
            status=_STATUS_MAP.get(code or "", OrderStatus.BOOKED),
            total=total,
            travelers=_travelers(body, payload),
            ticket_time_limit_at=_deadline(
                body.get("ticket_time_limit"), now=dt.datetime.now(dt.UTC)
            ),
            travel_start_at=departure,
            route_summary=route,
            raw=raw,
        )

    async def cancel(self, client: GtsClient, payload: dict[str, Any]) -> CancelResult:
        """Release a booking GTS still holds.

        **No shape check at all** — the one step without one. Which field names
        the booking is not written down anywhere we control, and a wrong guess
        would refuse a valid cancellation before GTS ever saw it. Being wrong
        here costs a real seat, so the pipe forwards and lets GTS answer
        (API.md §20).

        Nothing is required of the answer either: the client raises on a
        refusal, so reaching this line means the seat is released whether or not
        GTS bothered to name a status.
        """
        raw = await client.post(
            "/v1/content/cancel/", json=payload, timeout=GtsTimeouts.DEFAULT_SECONDS
        )
        return CancelResult(
            provider_status=_text(_order_body(raw), "status", limit=16), raw=raw
        )

    def status_map(self) -> Mapping[str, OrderStatus]:
        return _STATUS_MAP


__all__ = ["FlightAdapter"]
