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

The whole pre-booking flow exists — ``search``, ``offers``, ``upsell``,
``verify``; only ``book`` lands with the booking saga (PHASES.md §4).
"""

import datetime as dt
from typing import Any

import pydantic
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import UpstreamError, ValidationFailed
from app.providers.gts.base import GtsClient, GtsTimeouts
from app.providers.products.base import FlowStep, ProductCode

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


class FlightAdapter:
    """Implements ``ProductAdapter`` for ``flight`` — search and offers."""

    code = ProductCode.FLIGHT

    def supports(self) -> frozenset[FlowStep]:
        return frozenset(
            {FlowStep.SEARCH, FlowStep.OFFERS, FlowStep.UPSELL, FlowStep.VERIFY}
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

    async def book(self, client: GtsClient, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("booking lands with the booking saga")


__all__ = ["FlightAdapter"]
