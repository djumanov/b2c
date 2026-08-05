"""What ``POST /admin/uploads/`` gives back — API.md §11."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.providers.storage.base import UploadPurpose


class UploadOut(BaseModel):
    """``{"id": …, "url": …, "mime": …, "size": …}`` from the contract, plus
    the fields API.md §8 requires of every object."""

    id: uuid.UUID
    url: str
    #: Spelled ``mime`` in the contract's example, not ``content_type``.
    mime: str
    size: int
    purpose: UploadPurpose
    filename: str
    created_at: datetime
    updated_at: datetime


__all__ = ["UploadOut"]
