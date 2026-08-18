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

from app.api.errors import AppError, UpstreamError, UpstreamTimeout, ValidationFailed
from app.core.logging import get_logger
from app.core.money import Money, quantize, to_decimal
from app.modules.orders.states import OrderStatus
from app.providers.gts.base import GtsClient, GtsTimeouts
from app.providers.products.base import SEARCH_IN_PROCESS, FlowStep, ProductCode
from app.providers.products.orders import (
    BookingResult,
    CancelResult,
    FailureClass,
    RepriceResult,
    TicketingResult,
    TravelerRef,
    UnreadableAnswer,
)

logger = get_logger(__name__)

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

#: What an empty agreement balance sounds like. Recorded verbatim from GTS's
#: own error convention (GTS.md §10) — ``"BOOKING: save_booking 403: user don't
#: have enough credits on account"``. It is a failure of *ours*, not the
#: customer's, so it is kept apart from the rest (``O5``).
_DEPOSIT_PHRASES: Final = ("enough credits", "insufficient balance", "balance is low")

#: Failures worth trying again. Short and conservative on purpose: an
#: unrecognised message is treated as terminal, because refunding a customer for
#: a reason nobody understands is recoverable and retrying past the hold is not.
_RETRYABLE_PHRASES: Final = (
    "timeout",
    "timed out",
    "temporarily",
    "try again",
    "service unavailable",
    "too many requests",
)

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


def _number(order_number: str) -> int | str:
    """GTS's ``order_number`` as GTS spells it.

    An integer upstream and text on our side (API.md §1), and the collection's
    bodies send the integer. A value that will not convert is passed through
    rather than rejected: it came out of the provider's own answer, and the
    provider is the one entitled to say what it means.
    """
    try:
        return int(order_number)
    except ValueError:
        return order_number


def _nested_body(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
    """The order inside a GTS envelope, or nothing.

    Unlike ``_order_body`` this does **not** fall back to the payload itself.
    The envelope carries a ``status`` of its own — ``"success"`` — and reading
    that as the order's would record a provider status GTS never gave, on every
    answer that happened not to nest one.
    """
    for key in ("data", "order"):
        inner = envelope.get(key)
        if isinstance(inner, dict):
            return inner
    return {}


def _order_body(response: Mapping[str, Any]) -> Mapping[str, Any]:
    """The order itself, out of GTS's two-layer booking answer.

    Our client already strips GTS's envelope, so what arrives is
    ``{"message": "booked", "request_id": …, "data": {…the order…}}`` and the
    order's own fields sit under that inner ``data`` (EASY_GATEWAY collection,
    ``/content/Booking``).

    ``order`` is tried after ``data`` because the collection is not consistent
    with itself: ``booking`` nests the order under ``data`` and ``ticketing``
    puts it under ``order``. Falling back to the response itself is not
    politeness either — an older shape returns the order flat, and reading a
    flat answer wrongly costs nothing while missing a nested one costs the
    ability to cancel.
    """
    for key in ("data", "order"):
        inner = response.get(key)
        if isinstance(inner, dict):
            return inner
    return response


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


def _hide_failure(step: str, failure: AppError) -> None:
    """Record an upstream failure the client is about to be spared.

    ``offers/`` and ``upsell/`` answer an empty page instead of a ``502``
    (API.md §20), which means the only trace left of a broken provider — or a
    broken GTS — is this line. The client logged *why* the call failed
    (``gts_rejected``, ``gts_timeout``, ``gts_unreachable``); this logs that we
    then chose to hide it, so a silent degradation is still a countable event
    rather than a search that mysteriously returns nothing.
    """
    logger.warning(
        "search_step_degraded",
        step=step,
        error=str(failure),
        upstream=failure.meta.get("upstream") if failure.meta else None,
    )


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

        **A failure here is a page we could not fetch, not a failed search**
        (API.md §20, decision of 2026-08-18). A search fans out across
        providers and answers unevenly: one of them refuses and the whole
        envelope comes back ``status: "error"``, while the next poll a second
        later carries offers. Turning that into a ``502`` put an error screen
        in front of a customer whose search was going fine. So every upstream
        disappointment — GTS's own error, a ``5xx``, a timeout, an unreachable
        host — becomes an **empty page that still reads ``In process``**, and
        the client simply asks again.

        The cursor is echoed back rather than dropped: the retry must ask for
        the *same* page, because a failure that silently advanced the paging
        would lose the offers it skipped. Our own ``422`` is untouched — the
        shape check runs before the ``try``, and ``ValidationFailed`` is not
        an upstream failure.
        """
        _validated(_FlightOffersIn, params)
        try:
            payload = await client.post_envelope(
                "/v1/content/offers/", json=params, timeout=GtsTimeouts.SEARCH_SECONDS
            )
        except (UpstreamError, UpstreamTimeout) as failure:
            _hide_failure("offers", failure)
            return {
                "request_id": params.get("request_id"),
                "next_token": params.get("next_token"),
                "count": 0,
                "offers": [],
                "search_status": SEARCH_IN_PROCESS,
            }
        data: dict[str, Any] = payload.get("data") or {}
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

        It degrades like ``offers`` too, and for the same reason: branded fares
        are still part of choosing, and a variant list that could not be
        fetched is worth an empty list and another poll rather than an error
        screen over an offer the customer can book as it stands (API.md §20).
        ``verify`` deliberately does **not** follow — by then one offer has been
        chosen, and answering "nothing here, keep asking" about it would be a
        lie.
        """
        _validated(_FlightOfferRefIn, payload)
        try:
            envelope = await client.post_envelope(
                "/v1/content/upsell/", json=payload, timeout=GtsTimeouts.SEARCH_SECONDS
            )
        except (UpstreamError, UpstreamTimeout) as failure:
            _hide_failure("upsell", failure)
            return {
                "request_id": payload.get("request_id"),
                "offers": [],
                "search_status": SEARCH_IN_PROCESS,
            }
        data: dict[str, Any] = envelope.get("data") or {}
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
        data: dict[str, Any] = envelope.get("data") or {}
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

        ``post_envelope`` for the same reason ticketing uses it: the recorded
        cancel answer is ``{status, code, order}`` with **no ``data`` key**, and
        the bare-``data`` reader refuses anything without one — which would have
        made every cancellation a ``502`` against live GTS (STATUS.md §8.15a).
        Reading the whole envelope survives either spelling.

        Nothing is required of the answer either: the client raises on a
        refusal, so reaching this line means the seat is released whether or not
        GTS bothered to name a status.
        """
        raw = await client.post_envelope(
            "/v1/content/cancel/", json=payload, timeout=GtsTimeouts.DEFAULT_SECONDS
        )
        return CancelResult(
            provider_status=_text(_nested_body(raw), "status", limit=16), raw=raw
        )

    async def reprice(self, client: GtsClient, order_number: str) -> RepriceResult:
        """Ask what the reservation costs now.

        ``reprice_check`` reads; it does not commit anything. Whether GTS also
        needs ``reprice_confirm`` before it will ticket at the new price is not
        written down anywhere we control and has not been seen live
        (order-system/03-design.md §3.10, Q4) — so this asks, and the caller
        decides whether the answer is acceptable.
        """
        envelope = await client.post_envelope(
            "/v1/content/reprice_check/",
            json={"order_number": _number(order_number)},
            timeout=GtsTimeouts.DEFAULT_SECONDS,
        )
        total = _money(_nested_body(envelope))
        if total is None:
            raise UnreadableAnswer(
                "the GTS reprice answer names no price", raw=envelope
            )
        return RepriceResult(total=total, raw=envelope)

    async def ticket(self, client: GtsClient, order_number: str) -> TicketingResult:
        """Issue the tickets, paid from the installation's GTS deposit.

        ``post_envelope`` and not ``post``: the recorded answer puts the order
        under **``order``**, not ``data``, and the bare-``data`` reader would
        turn a successful ticketing into a ``502``. Reading the whole envelope
        costs nothing and survives either spelling — the same fix ``cancel``
        needs and will get with its own slice (STATUS.md §8.15a).

        ``payment_method: "deposit"`` is the only value the collection records:
        GTS charges the agreement's balance, so an empty one is *our*
        accounting problem and ``classify`` keeps it apart from a customer's.
        """
        envelope = await client.post_envelope(
            "/v1/content/ticketing/",
            json={"order_number": _number(order_number), "payment_method": "deposit"},
            timeout=GtsTimeouts.DEFAULT_SECONDS,
        )
        body = _nested_body(envelope)
        code = _text(body, "status", limit=16)
        return TicketingResult(
            provider_status=code,
            status=_STATUS_MAP.get(code or "", OrderStatus.TICKETED),
            travelers=_travelers(body, {}),
            raw=envelope,
        )

    def classify(self, failure: AppError) -> FailureClass:
        """Which kind of "no" this was.

        Everything unrecognised is **terminal**, and that direction is
        deliberate: refunding a customer for a failure nobody understands is
        recoverable, while retrying one forever until the hold lapses is not.
        """
        if isinstance(failure, UpstreamTimeout):
            return FailureClass.RETRYABLE
        message = str(failure).lower()
        if any(phrase in message for phrase in _DEPOSIT_PHRASES):
            return FailureClass.DEPOSIT
        if any(phrase in message for phrase in _RETRYABLE_PHRASES):
            return FailureClass.RETRYABLE
        return FailureClass.TERMINAL

    def status_map(self) -> Mapping[str, OrderStatus]:
        return _STATUS_MAP


__all__ = ["FlightAdapter"]
