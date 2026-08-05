"""Storing a file and keeping track of whether anything uses it — API.md §11.

The module's whole job is the gap between "a file exists" and "something points
at it". Other modules reach it through ``link`` and ``unlink``; nothing outside
touches ``models`` (ARCHITECTURE.md §4).
"""

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ValidationFailed
from app.core.logging import get_logger
from app.db.mixins import utcnow
from app.db.repository import get_live, get_live_or_404, live
from app.modules.uploads import rules
from app.modules.uploads.models import Upload
from app.modules.uploads.schemas import UploadOut
from app.providers.storage import get_storage
from app.providers.storage.base import UploadPurpose

logger = get_logger(__name__)

#: API.md §11: "Bog'lanmagan fayllar 24 soatdan keyin tozalanadi."
ORPHAN_GRACE = timedelta(hours=24)


async def _to_out(upload: Upload) -> UploadOut:
    private = not rules.rule_for(upload.purpose).public
    return UploadOut(
        id=upload.id,
        url=await get_storage().url(upload.storage_key, private=private),
        mime=upload.content_type,
        size=upload.size,
        purpose=upload.purpose,
        filename=upload.filename,
        created_at=upload.created_at,
        updated_at=upload.updated_at,
    )


def _validate(purpose: UploadPurpose, content_type: str, content: bytes) -> None:
    rule = rules.rule_for(purpose)
    if not rule.uploadable:
        raise ValidationFailed(
            f"Files with the purpose {purpose.value!r} are produced by the "
            "system, not uploaded",
            field="purpose",
        )
    if not content:
        raise ValidationFailed("The file is empty", field="file")
    if len(content) > rule.max_bytes:
        limit = rule.max_bytes // rules.MEGABYTE
        raise ValidationFailed(
            f"A {purpose.value} may be at most {limit} MB", field="file"
        )
    if content_type not in rule.types:
        allowed = ", ".join(sorted(rule.types))
        raise ValidationFailed(
            f"A {purpose.value} must be one of: {allowed}", field="file"
        )
    if not rules.content_matches(content_type, content):
        # The declared type and the bytes disagree. Whatever this is, it is not
        # what it says, and it would be served back to a browser later.
        raise ValidationFailed(
            f"The file's contents are not {content_type}", field="file"
        )


async def create(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    content_type: str,
    purpose: UploadPurpose,
    uploaded_by: uuid.UUID | None = None,
) -> UploadOut:
    _validate(purpose, content_type, content)

    key = await get_storage().save(content=content, filename=filename, purpose=purpose)
    upload = Upload(
        filename=filename[:255],
        content_type=content_type,
        size=len(content),
        purpose=purpose,
        storage_key=key,
        uploaded_by=uploaded_by,
    )
    session.add(upload)
    try:
        await session.commit()
    except Exception:
        # The bytes are already on disk; without this they would stay there
        # with no row to sweep them by.
        await get_storage().delete(key)
        raise
    await session.refresh(upload)
    logger.info(
        "upload_created",
        upload_id=str(upload.id),
        purpose=purpose.value,
        size=upload.size,
    )
    return await _to_out(upload)


async def get(session: AsyncSession, upload_id: uuid.UUID) -> UploadOut:
    upload = await get_live_or_404(session, Upload, upload_id, name="File")
    return await _to_out(upload)


async def url_for(session: AsyncSession, upload_id: uuid.UUID | None) -> str | None:
    """The URL of a file another module references, or ``None``.

    Tolerant on purpose: a branding row pointing at a file that was swept must
    render as "no logo", not as a 500 on the endpoint the whole site fetches
    first (API.md §17).
    """
    if upload_id is None:
        return None
    upload = await get_live(session, Upload, upload_id)
    if upload is None:
        return None
    private = not rules.rule_for(upload.purpose).public
    return await get_storage().url(upload.storage_key, private=private)


async def by_key(session: AsyncSession, key: str) -> Upload | None:
    """The live row behind a storage key. For support questions and the panel."""
    row: Upload | None = await session.scalar(
        live(Upload).where(Upload.storage_key == key)
    )
    return row


# --- attachment ---------------------------------------------------------------------


async def link(
    session: AsyncSession, upload_id: uuid.UUID, *, purpose: UploadPurpose | None = None
) -> Upload:
    """Claim a file for a resource that is about to reference it.

    Called by the module doing the referencing — ``settings`` when a logo is
    chosen, ``cms`` when a blog cover is. Until it is called the file counts as
    abandoned and the sweep will take it.

    ``purpose`` is checked when given, so a caller cannot attach a ``document``
    where a ``logo`` belongs.
    """
    upload = await get_live_or_404(session, Upload, upload_id, name="File")
    if purpose is not None and upload.purpose is not purpose:
        raise ValidationFailed(
            f"This file was uploaded as {upload.purpose.value!r}, "
            f"but {purpose.value!r} is required"
        )
    if upload.linked_at is None:
        upload.linked_at = utcnow()
    return upload


async def unlink(session: AsyncSession, upload_id: uuid.UUID) -> None:
    """Release a file whose only reference has gone.

    It becomes an orphan again rather than being deleted now: a client who
    swaps a logo and changes their mind an hour later still has the old one.
    """
    upload = await session.get(Upload, upload_id)
    if upload is not None:
        upload.linked_at = None


# --- the sweep (API.md §11) -----------------------------------------------------------


async def sweep_orphans(session: AsyncSession) -> int:
    """Remove files nothing ever referenced. Returns how many went.

    The row is soft-deleted like everything else (API.md §8) but the **bytes
    are gone for good** — keeping them would defeat the point of a sweep, and
    the row is what the journal and any support question need.
    """
    cutoff = utcnow() - ORPHAN_GRACE
    orphans = (
        await session.scalars(
            live(Upload)
            .where(Upload.linked_at.is_(None))
            .where(Upload.created_at < cutoff)
        )
    ).all()

    storage = get_storage()
    for upload in orphans:
        await storage.delete(upload.storage_key)
        upload.soft_delete()
    await session.commit()

    if orphans:
        logger.info("uploads_swept", count=len(orphans))
    return len(orphans)


async def count_orphans(session: AsyncSession) -> int:
    rows = await session.scalars(
        select(Upload.id)
        .where(Upload.linked_at.is_(None))
        .where(Upload.deleted_at.is_(None))
    )
    return len(rows.all())


__all__ = [
    "ORPHAN_GRACE",
    "by_key",
    "count_orphans",
    "create",
    "get",
    "link",
    "sweep_orphans",
    "unlink",
    "url_for",
]
