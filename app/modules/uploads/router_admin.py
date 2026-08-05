"""``POST /admin/uploads/`` — API.md §11 and §39.

A file is uploaded on its own and its ``id`` is attached to a resource
afterwards, which is why this is one endpoint and not a field on seven others.

It belongs to the *content* group rather than the read-only *system* group,
even though the contract lists it under §39: both roles may upload, because
both roles edit content (API.md §5).
"""

from typing import Annotated

from fastapi import File, Form, UploadFile

from app.api.deps import CurrentStaff
from app.api.envelope import enveloped_router
from app.db.session import SessionDep
from app.modules.uploads import rules, service
from app.modules.uploads.schemas import UploadOut
from app.providers.storage.base import UploadPurpose

router = enveloped_router(prefix="/uploads", tags=["uploads"])

#: One byte over the largest rule, so "too large" is caught here with a 422
#: naming the field rather than by the reverse proxy with a bare 413.
_READ_LIMIT = rules.MAX_UPLOAD_BYTES + 1
_CHUNK = 64 * 1024


async def _read_limited(file: UploadFile) -> bytes:
    """Read the body, but stop just past the limit.

    ``await file.read()`` with no argument would pull an arbitrarily large body
    into memory before anything got the chance to reject it.
    """
    chunks: list[bytes] = []
    total = 0
    while total <= _READ_LIMIT:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


@router.post("/", status_code=201, summary="Upload a file and get its id")
async def create_upload(
    staff: CurrentStaff,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[UploadPurpose, Form()],
) -> UploadOut:
    return await service.create(
        session,
        content=await _read_limited(file),
        filename=file.filename or "file",
        content_type=(file.content_type or "").split(";")[0].strip().lower(),
        purpose=purpose,
        uploaded_by=staff.id,
    )


__all__ = ["router"]
