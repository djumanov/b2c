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

**The pipe ends at ``verify``.** Booking and everything after it belonged to
the order system, which was removed to be rebuilt; the half of this adapter
that answered in our own types went with it. What is left is the search flow,
and it is a pipe the whole way through.
"""

import datetime as dt
from typing import Any

import pydantic
from pydantic import BaseModel, ConfigDict, Field

from app.api.errors import AppError, UpstreamError, UpstreamTimeout, ValidationFailed
from app.core.logging import get_logger
from app.providers.gts.base import GtsClient, GtsTimeouts
from app.providers.products.base import SEARCH_IN_PROCESS, FlowStep, ProductCode

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


__all__ = ["FlightAdapter"]
