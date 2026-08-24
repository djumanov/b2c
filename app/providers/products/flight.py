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

**The pipe gains one stateful step: ``book``.** The search steps relay GTS's
shape untouched, but a booking is recorded in our database, so its answer is
read here — once, tolerantly — into a ``BookedOrder``. GTS spells the same
answer differently between installations (``firstname`` vs ``first_name``, a
price that is flat or nested, a deadline that is an ISO string, minutes or
seconds), which is why every reader below reaches with ``.get`` and gives up
to ``None`` rather than trusting one recorded example.

``book`` is also the one step that makes **two** calls. The booking answer
names the order and, on this installation, carries nothing else, so the order
itself is read back from ``/v1/orders/{order_number}/`` — everything a
customer sees (PNR, price, route, the payment deadline) lives there.
"""

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

import pydantic
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import AppError, UpstreamError, UpstreamTimeout, ValidationFailed
from app.core.logging import get_logger
from app.core.money import quantize
from app.providers.gts.base import GtsClient, GtsTimeouts
from app.providers.products import gts_order
from app.providers.products.base import (
    SEARCH_IN_PROCESS,
    BookedOrder,
    FlowStep,
    OrderPrice,
    OrderSnapshot,
    ProductCode,
)

logger = get_logger(__name__)

#: IATA location code — city or airport.
_IATA_PATTERN = r"^[A-Za-z]{3}$"


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


class _FlightBookingIn(BaseModel):
    """The least a booking must carry: the chosen offer and who is flying.

    Passenger fields are GTS's to validate — document rules differ per
    provider and change without waiting for us. We refuse only a body that
    could not possibly be a booking, before a GTS session is spent on it.
    """

    model_config = ConfigDict(extra="allow")

    request_id: str = Field(min_length=1)
    offer_id: str = Field(min_length=1)
    passengers: list[dict[str, Any]] = Field(min_length=1)


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


# --- booking-answer readers ---------------------------------------------------
# Each reads one fact out of GTS's booking answer and answers ``None`` when the
# spelling at hand does not carry it. A booking with a confirmed order number
# is recorded even when a price or a deadline could not be read.


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _order_number(answer: dict[str, Any]) -> int | None:
    """GTS's number for the seat it just held — wherever this installation put it.

    Three shapes carry it, all of them real: the number **alone** under
    ``data`` (live GTS, verified 2026-08-20), the whole order nested one level
    deeper beside ``message: "booked"`` (the gateway collection's documented
    example), and an order flat at the top (its older example). Only the depth
    differs, so both depths are read.
    """
    for source in (answer, answer.get("data")):
        if not isinstance(source, dict):
            continue
        value = source.get("order_number")
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _booked_order(answer: dict[str, Any]) -> dict[str, Any]:
    """The order as the booking answer itself carried it, if it carried one."""
    nested = answer.get("data")
    return nested if isinstance(nested, dict) else answer


async def _order_detail(
    client: GtsClient, order_number: int, *, fallback: dict[str, Any]
) -> dict[str, Any]:
    """The booked order, read back from GTS — the booking answer only names it.

    ``POST /v1/content/booking/`` answers with the order number and nothing
    else on this installation, while every field a customer sees lives on the
    order, so it is fetched once here (``GET /v1/orders/{order_number}/``).

    **A failure here is not a failed booking.** GTS is holding a seat by the
    time this runs, and a seat nothing in our database points at is worse than
    an order missing its price. So the read degrades to whatever the booking
    answer carried, the row is written either way, and the gap is a warning
    rather than a ``502`` — the lesson of the two live seats lost to a parsing
    bug on 2026-08-19.
    """
    try:
        return await client.get(
            f"/v1/orders/{order_number}/", timeout=GtsTimeouts.DEFAULT_SECONDS
        )
    except (UpstreamError, UpstreamTimeout) as failure:
        logger.warning(
            "gts_order_detail_unread",
            order_number=order_number,
            error=str(failure),
        )
        return fallback


def _snapshot(order_number: int, order: dict[str, Any]) -> OrderSnapshot:
    """Read one GTS order into the fields the orders module keeps.

    Shared by ``book`` (the read-back right after the hold) and ``retrieve``
    (every later read), so "where GTS hides the price" is known in one place.
    """
    amount, currency = _booking_amount(order)
    return OrderSnapshot(
        gts_order_number=order_number,
        gts_order_uid=_text(order.get("order_uid")),
        gts_status=_text(order.get("status")) or "",
        pnr=_text(order.get("gds_pnr")),
        trip_type=_text(order.get("trip_type")),
        amount=amount,
        currency=currency,
        route_summary=_route_summary(order),
        passenger_count=_passenger_count(order),
        ticket_time_limit_at=_ticket_deadline(order.get("ticket_time_limit")),
        raw=order,
    )


def _booking_amount(order: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    """The order's total and currency — ``price_info`` first, flat second."""
    price_info = order.get("price_info")
    source = price_info if isinstance(price_info, dict) else order
    price = source.get("price")
    if isinstance(price, bool) or not isinstance(price, int | float | str):
        return None, None
    try:
        # ``str`` first: GTS sends JSON floats, and Decimal(0.1) would keep
        # the binary noise a customer should never see.
        amount = quantize(Decimal(str(price)))
    except InvalidOperation:
        return None, None
    return amount, _text(source.get("currency"))


def _order_price(data: dict[str, Any], *, step: str) -> OrderPrice:
    """The price a reprice answer carries — ``data.price_info``, read like an
    order's. No price is no answer: the price is what was asked for."""
    amount, currency = _booking_amount(data)
    if amount is None or currency is None:
        logger.warning("gts_reprice_unreadable", step=step, keys=sorted(data))
        raise UpstreamError(f"the GTS {step} answer carried no price")
    return OrderPrice(amount=amount, currency=currency, raw=data)


def _route_summary(order: dict[str, Any]) -> str | None:
    """``routes[].direction`` joined — ``"TAS-IST, IST-TAS"`` for a round trip.

    Where the routes are is ``gts_order``'s knowledge, not this function's:
    live GTS nests them under ``offer``, and looking only beside the order
    left this summary ``None`` on every real booking.
    """
    directions = [route.get("direction") for route in gts_order.routes(order)]
    summary = ", ".join(part for part in directions if isinstance(part, str) and part)
    return summary[:128] or None


def _passenger_count(order: dict[str, Any]) -> int | None:
    count = order.get("passengers_count")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count
    passengers = order.get("passengers")
    if isinstance(passengers, list) and passengers:
        return len(passengers)
    return None


#: ``ticket_time_limit`` has been seen as an ISO datetime, as minutes (120,
#: 4319) and as seconds (288000). No number of minutes this large is a sane
#: deadline, so the threshold splits the two numeric readings.
_DEADLINE_SECONDS_THRESHOLD = 10_000


def _ticket_deadline(value: Any) -> dt.datetime | None:
    """When GTS releases the unpaid seat — best effort, ``None`` when unclear."""
    if isinstance(value, str) and value:
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    seconds = value if value >= _DEADLINE_SECONDS_THRESHOLD else value * 60
    return dt.datetime.now(dt.UTC) + dt.timedelta(seconds=seconds)


class FlightAdapter:
    """``ProductAdapter`` for ``flight``.

    A vertical is one file and one registry line (ARCHITECTURE.md §6).
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

    async def book(self, client: GtsClient, payload: dict[str, Any]) -> BookedOrder:
        """Book one offer — the step after ``verify`` cleared it.

        Bare ``post``, not ``post_envelope``: a booking has no ``"In
        process"`` state, so anything short of ``"success"`` is a failure the
        client must see (a 502 with GTS's own words in ``meta.upstream``).
        And **no degradation**: unlike ``offers/``, an error here must never
        be softened into "keep polling" — nothing will improve.

        **The answer names the order and little else.** Live GTS replies with
        ``{"order_number": N}`` under the envelope the client already stripped
        — no PNR, no price, no route — so the order is read back from
        ``/v1/orders/{N}/`` and *that* is what fills the ``BookedOrder``.
        Installations that answer with the whole order (the gateway
        collection's example nests it beside ``message: "booked"``) are read
        just the same, and their copy stands in if the read-back fails.

        No order number anywhere means we could not name the seat GTS may now
        be holding — a 502, same as a timeout, and nothing is written.
        """
        _validated(_FlightBookingIn, payload)
        answer = await client.post(
            "/v1/content/booking/", json=payload, timeout=GtsTimeouts.DEFAULT_SECONDS
        )
        order_number = _order_number(answer)
        if order_number is None:
            logger.warning("gts_booking_unreadable", keys=sorted(answer))
            raise UpstreamError("the GTS booking returned an unexpected shape")
        order = await _order_detail(
            client, order_number, fallback=_booked_order(answer)
        )
        return BookedOrder(
            request_id=str(payload["request_id"]),
            offer_id=str(payload["offer_id"]),
            **_snapshot(order_number, order).model_dump(),
        )

    async def reprice(self, client: GtsClient, order_number: int) -> OrderPrice:
        """``POST /v1/content/reprice_check/`` — the order's price today.

        GTS refuses to ticket a booking that was not priced again first
        ("Перед выпиской билета выполните reprice_check", live 2026-08-24),
        so this and ``confirm_price`` sit between booking and payment. Bare
        ``post``: the answer is a verdict, ``data.price_info`` or a refusal.
        """
        data = await client.post(
            "/v1/content/reprice_check/",
            json={"order_number": order_number},
            timeout=GtsTimeouts.DEFAULT_SECONDS,
        )
        return _order_price(data, step="reprice_check")

    async def confirm_price(self, client: GtsClient, order_number: int) -> OrderPrice:
        """``POST /v1/content/reprice_confirm/`` — accept today's price.

        Answers with a price too, and that one is GTS's final word: it is what
        ticketing will debit, so the orders module stores it over the check's.
        """
        data = await client.post(
            "/v1/content/reprice_confirm/",
            json={"order_number": order_number},
            timeout=GtsTimeouts.DEFAULT_SECONDS,
        )
        return _order_price(data, step="reprice_confirm")

    async def ticket(self, client: GtsClient, order_number: int) -> OrderSnapshot:
        """``POST /v1/content/ticketing/`` — issue the ticket against our deposit.

        ``payment_method: "deposit"`` is the only value GTS's collection
        records: GTS charges the agreement's balance, the customer's money
        having already been taken by us. The answer nests the order under
        ``order``, not ``data`` — the one shape that made a bare ``post``
        turn every success into a 502 — so the whole envelope is read and
        the order looked for under ``order``, then ``data``, then flat.

        GTS's own refusal (``status: "error"``) raises ``UpstreamError`` with
        GTS's words; a deposit that is empty reads "user don't have enough
        credits on account", and that is **our** failure, not the customer's.
        """
        envelope = await client.post_envelope(
            "/v1/content/ticketing/",
            json={"order_number": order_number, "payment_method": "deposit"},
            timeout=GtsTimeouts.DEFAULT_SECONDS,
        )
        order = gts_order.order_body(envelope)
        if order is None:
            logger.warning("gts_ticketing_unreadable", keys=sorted(envelope))
            raise UpstreamError("the GTS ticketing answer had an unexpected shape")
        return _snapshot(order_number, order)

    async def retrieve(self, client: GtsClient, order_number: int) -> OrderSnapshot:
        """``GET /v1/orders/{n}/`` — the order as GTS holds it right now.

        Unlike the read-back inside ``book``, this one **does not degrade**:
        its callers are about to charge a card, release a hold or settle a
        ticket on the strength of the answer, and "could not read it" must
        reach them as the ``502``/``504`` it is, never as a stale guess.
        """
        order = await client.get(
            f"/v1/orders/{order_number}/", timeout=GtsTimeouts.DEFAULT_SECONDS
        )
        return _snapshot(order_number, order)


__all__ = ["FlightAdapter"]
