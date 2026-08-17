"""What an order looks like on the wire (API.md §21).

Five columns of ours and one field that is not ours at all: ``data`` is GTS's
booking answer, forwarded the way the booking step forwarded it. Nothing in
here reshapes it, so the customer reading their order history sees exactly
what the booking response showed them.
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
    created_at: datetime
    cancelled_at: datetime | None
    #: GTS's booking answer, verbatim. Named ``data`` rather than
    #: ``gts_response`` because from outside it is simply the order's content;
    #: the column name is an internal detail.
    data: dict[str, Any] = Field(validation_alias="gts_response")


__all__ = ["OrderOut"]
