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
    """One order. ``id`` is ours, ``gts_order_id`` is GTS's — see API.md §21."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    product: str
    gts_order_id: str | None
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
