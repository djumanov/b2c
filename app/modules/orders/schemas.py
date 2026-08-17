"""What an order looks like on the wire (API.md §21).

**The whole row, and the same shape on both endpoints.** There is no trimmed
list variant: ``data`` — GTS's booking answer, forwarded the way the booking
step forwarded it — dominates the payload either way, so dropping our own
columns from the list would save nothing and would force a client to fetch
``{id}/`` for every row it wanted to render.

Nothing here reshapes ``data``, so a customer reading their history sees
exactly what the booking response showed them.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OrderOut(BaseModel):
    """One order. ``id`` is ours, the ``gts_*`` pair is GTS's — API.md §21."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    product: str
    #: GTS's order number — what ``cancel/`` takes (§20). An integer upstream,
    #: a string here: API.md §1 keeps identifiers textual.
    gts_order_number: str | None
    #: GTS's internal key for the same order. Nothing we call takes it; it is
    #: what GTS support asks for.
    gts_order_uid: str | None
    #: GTS's own code (``BO``, ``TI``, ``CB``, …), not the canonical enum —
    #: that arrives with the saga (ARCHITECTURE.md §7).
    status: str | None
    #: The search and the offer this order came from. Useful to a client
    #: reconstructing what was bought, and to support tracing a booking back
    #: through GTS's own logs.
    request_id: str | None
    offer_id: str | None
    created_at: datetime
    updated_at: datetime
    #: When *we* asked GTS to release it. ``status`` says what GTS called the
    #: result; this says when the asking happened.
    cancelled_at: datetime | None
    #: GTS's booking answer, verbatim. Named ``data`` rather than
    #: ``gts_response`` because from outside it is simply the order's content;
    #: the column name is an internal detail.
    data: dict[str, Any] = Field(validation_alias="gts_response")


__all__ = ["OrderOut"]
